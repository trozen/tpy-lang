#!/usr/bin/env python3
"""The FIELD-WRITE admission verdict ratchet: position x slot type x source.

A write to a record field -- a constructor member-init, a method's or a
frame's `self.f = src`, a local instance's `h.f = src` -- is admitted by the
field's declared slot and the lowered source's form. This script generates
the whole grid and records it, so "the ctor twin of this cell rejects and
the method twin does not" is a diff of `field_admission_sweep.expected.json`,
not a review discovery. A cell of class `R` (rejects valid code) that starts
compiling is printed as PROGRESS; every other move needs an explanation.

THE AXES.
  position  -- `ctor` (member-init: `self.fld = src` in `__init__`), `method`
               (the same write in a plain method), `gen_method` and
               `async_method` (the same write in a generator / `async def`
               method: a resumable frame, the write lowers while the frame is
               emitted), `local_holder` (`hold.fld = src` in a free function,
               `hold` a local instance), and `local` (the CONTROL: a local
               declaration `loc: T = src` -- the sink with no field ladder).
  slot      -- the field type (`SLOTS`): the str/bytes views and owners, their
               Optionals, the builtin containers, a record, an Optional and a
               union of records, the numeric types, a tuple and a value union.
  source    -- the right-hand expression shape (`SOURCES`).

WHAT A CELL RECORDS, one list per cell:
  [verdict, detail, render, class, reason, prelude_warning]
  verdict   -- `ok`, `warning`, `error` or `crash`.
  detail    -- for an error `[where] message` (`where` is `subject`, `prelude`
               or the line offset from the subject); for a warning every
               warning inside the cell, offset-tagged when not on the subject.
               A `{driver-main error: ...}` suffix means the cell's `main()`
               driver failed and the cell was re-verdicted with an empty
               `main()`.
  render    -- the generated C++ of the subject write, whitespace-normalised
               (`init:` a member initialiser, `body:` an assignment, `decl:`
               the control's declaration). The half a verdict cannot show: two
               admitted cells where one copies and the other moves differ
               only here.
  class     -- for a rejected cell: `R` rejects valid code, `E` a justified
               rejection (a view slot fed from a temporary), `?` unclassified
               (an error off the subject line, or a sema rule every position
               shares). Empty for an admitted cell.

EACH CELL IS ITS OWN PROGRAM, compiled with `tpyc -o` as a subprocess. Unlike
the THIR-survey siblings (`arg_family_sweep.py`, `ref_sink_sweep.py`) this
measures the whole front end -- a sema error, a lowering reject and the
emitted C++ alike -- at ~1.7 s a cell, so a full pass is a few minutes at the
default four jobs.

SKIPPED CELLS are the (position, slot, source) triples no program is written
for; `skip_reason()` names why, and every run prints them grouped (all of
them with `--skips`).

  uv run python scripts/thir_migration/review/field_admission_sweep.py
      check against the committed table; exits 1 on an unexplained move
  ... --update            rewrite the committed table (full run only)
  ... --only TEXT         only cells whose key contains TEXT
  ... --explain FILE      a JSON {cell key: reason} of expected moves
  ... --grid [--fresh]    print the per-position grids (committed table,
                          or the fresh run with --fresh)
  ... --run [--only TEXT] build and run the behaviour subset (`RUN_CELLS`,
                          `INT_CELLS`) against CPython, at most two at a
                          time; exits 1 on DIFFERS-SILENT or a build/run
                          failure (a copy with the copy warning is
                          DIFFERS+warn, a documented divergence
                          DIFFERS-DECLARED, a compile error tpy-reject)
  ... --jobs N            parallel compilations (default: min(4, CPUs))
  ... --repo PATH         compile against another checkout
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The tree the sweep compiles against; `--repo` retargets it.
REPO = Path(__file__).resolve().parents[3]

EXPECTED = Path(__file__).with_suffix(".expected.json")

Q = chr(34)

# ---------------------------------------------------------------------------
# The axes.
# ---------------------------------------------------------------------------

# name -> (annotation, literal, show(@), in-place mutation of @ or "")
SLOTS: dict[str, tuple[str, str, str, str]] = {
    "str": ("str", '"hi"', "@", ""),
    "StrView": ("StrView", '"hi"', "@", ""),
    "bytes": ("bytes", 'b"hi"', "@", ""),
    "Opt[str]": ("Optional[str]", '"hi"', "@", ""),
    "Opt[bytes]": ("Optional[bytes]", 'b"hi"', "@", ""),
    "list[int]": ("list[int]", "[1, 2]", "@", "@.append(9)"),
    "dict[str,int]": ("dict[str, int]", '{"k": 1}', "@", '@["z"] = 9'),
    "set[int]": ("set[int]", "{1, 2}", "len(@)", "@.add(9)"),
    "P": ("P", "P(1)", "@.v", "@.v = 9"),
    "Opt[P]": ("Optional[P]", "P(1)", "@ is None", ""),
    "int": ("int", "5", "@", ""),
    "int32": ("int32", "5", "@", ""),
    "float": ("float", "1.5", "@", ""),
    "tuple[int,str]": ("tuple[int, str]", '(1, "a")', "@", ""),
    "int|str": ("int | str", '"hi"', "@", ""),
    "P|Q": ("P | Q", "P(1)", "isinstance(@, P)", ""),
}

REF_SLOTS = {"list[int]", "dict[str,int]", "set[int]", "P", "Opt[P]", "P|Q"}
OPT_SLOTS = {"Opt[str]", "Opt[bytes]", "Opt[P]"}

SOURCES = ("literal", "param", "local", "local_live", "other_field",
           "self_field", "call", "method_call", "binop", "slice", "elem",
           "dict_get", "cond", "comp", "walrus", "tuple_elem", "chain2",
           "conv", "none")

POSITIONS = ("ctor", "method", "gen_method", "async_method", "local_holder",
             "local")

# The positions whose subject is a field of an instance built by `Subj()`.
HELD = ("method", "gen_method", "async_method", "local_holder")

# Per-source spellings for the sources that exist only at some slots. A slot
# missing from a table has no such expression of its type.
BINOP = {
    "str": ([("a", "str", '"ab"')], 'a + "!"'),
    "StrView": ([("a", "StrView", '"ab"')], 'a + "!"'),
    "bytes": ([("a", "bytes", 'b"ab"')], 'a + b"!"'),
    "Opt[str]": ([("a", "str", '"ab"')], 'a + "!"'),
    "Opt[bytes]": ([("a", "bytes", 'b"ab"')], 'a + b"!"'),
    "list[int]": ([("a", "list[int]", "[1, 2]")], "a + [1]"),
    "dict[str,int]": ([("a", "dict[str, int]", '{"k": 1}')], 'a | {"z": 1}'),
    "set[int]": ([("a", "set[int]", "{1, 2}")], "a | {3}"),
    "int": ([("a", "int", "5")], "a + 1"),
    "int32": ([("a", "int32", "5")], "a + 1"),
    "float": ([("a", "float", "1.5")], "a + 1.0"),
    "int|str": ([("n", "int", "5")], "n + 1"),
}

SLICE = {
    "str": ([("a", "str", '"hello"')], "a[1:3]"),
    "StrView": ([("a", "StrView", '"hello"')], "a[1:3]"),
    "bytes": ([("a", "bytes", 'b"hello"')], "a[1:3]"),
    "Opt[str]": ([("a", "str", '"hello"')], "a[1:3]"),
    "Opt[bytes]": ([("a", "bytes", 'b"hello"')], "a[1:3]"),
    "list[int]": ([("a", "list[int]", "[1, 2, 3, 4]")], "a[1:3]"),
}

COMP = {
    "list[int]": ([("a", "list[int]", "[1, 2]")], "[v * 2 for v in a]"),
    "set[int]": ([("a", "set[int]", "{1, 2}")], "{v * 2 for v in a}"),
    "dict[str,int]": ([("a", "dict[str, int]", '{"k": 1}')],
                      "{k: v * 2 for k, v in a.items()}"),
}

CONV = {
    "str": ([("n", "int", "42")], "str(n)"),
    "StrView": ([("n", "int", "42")], "str(n)"),
    "bytes": ([("ba", "bytearray", 'bytearray(b"hi")')], "bytes(ba)"),
    "Opt[str]": ([("n", "int", "42")], "str(n)"),
    "Opt[bytes]": ([("ba", "bytearray", 'bytearray(b"hi")')], "bytes(ba)"),
    "list[int]": ([("a", "list[int]", "[1, 2]")], "list(a)"),
    "dict[str,int]": ([("a", "dict[str, int]", '{"k": 1}')], "dict(a)"),
    "set[int]": ([("a", "list[int]", "[1, 2]")], "set(a)"),
    "int": ([("s", "str", '"42"')], "int(s)"),
    "int32": ([("n", "int", "42")], "int32(n)"),
    "float": ([("n", "int", "42")], "float(n)"),
    "int|str": ([("n", "int", "42")], "str(n)"),
}

PER_SLOT = {"binop": BINOP, "slice": SLICE, "comp": COMP, "conv": CONV}


def skip_reason(pos: str, slot: str, source: str) -> str | None:
    """Why no program is written for this cell, or None."""
    if pos == "local" and source == "self_field":
        return "the control is a free-function local: there is no sibling field"
    table = PER_SLOT.get(source)
    if table is not None and slot not in table:
        return f"no `{source}` expression produces this slot type"
    if source == "none" and slot not in OPT_SLOTS:
        return "`None` only feeds an Optional slot"
    return None


def _src(slot: str, name: str):
    """(params, pre-lines, post-lines, expr) of one source at one slot."""
    ty, lit, show, mut = SLOTS[slot]
    if name == "literal":
        return [], [], [], lit
    if name == "param":
        return [("a", ty, lit)], [], [], "a"
    if name == "local":
        return [], [f"x: {ty} = {lit}"], [], "x"
    if name == "local_live":
        post = ([mut.replace("@", "x")] if mut
                else [f"print({show.replace('@', 'x')})"])
        return [], [f"x: {ty} = {lit}"], post, "x"
    if name == "other_field":
        return [("o", "A", "@ANEW@")], [], [], "o.f"
    if name == "self_field":
        return [], [], [], "@RECV@.g2"
    if name == "call":
        return [], [], [], "mk()"
    if name == "method_call":
        return [("o", "A", "@ANEW@")], [], [], "o.get_f()"
    if name in PER_SLOT:
        p, e = PER_SLOT[name][slot]
        return p, [], [], e
    if name == "elem":
        return [("xs", f"list[{ty}]", f"[{lit}]")], [], [], "xs[0]"
    if name == "dict_get":
        return ([("d", f"dict[str, {ty}]", "{" + Q + "k" + Q + ": " + lit
                  + "}")], [], [], 'd["k"]')
    if name == "cond":
        return ([("a", ty, lit), ("b", ty, lit), ("c", "bool", "True")], [],
                [], "a if c else b")
    if name == "walrus":
        return [("a", ty, lit)], [], [], "(w := a)"
    if name == "tuple_elem":
        return [("t", f"tuple[{ty}, int32]", f"({lit}, 1)")], [], [], "t[0]"
    if name == "chain2":
        return [("h", "H", "@HNEW@")], [], [], "h.a.f"
    if name == "none":
        return [], [], [], "None"
    raise KeyError(name)


# The object a source aliases, for the behaviour run: after the write the run
# program mutates it and prints both it and the field, so a copy where CPython
# aliases shows as an output difference. Sources that build a fresh value have
# nothing to alias.
ALIAS_ROOT = {
    "param": "a", "local": "x", "local_live": "x", "other_field": "o.f",
    "self_field": "@RECV@.g2", "method_call": "o.f", "elem": "xs[0]",
    "dict_get": 'd["k"]', "cond": "a", "walrus": "a", "tuple_elem": "t[0]",
    "chain2": "h.a.f",
}

# ---------------------------------------------------------------------------
# Program construction.
# ---------------------------------------------------------------------------

ABLOCK = '''class A:
    f: @T@

    def __init__(self@AP@) -> None:
        self.f = @AI@
@GETF@

'''

HBLOCK = '''class H:
    a: A

    def __init__(self@HP@) -> None:
        self.a = @HI@


'''

# The holder A's own ctor write; where the ctor-literal cell itself rejects,
# A is initialized through a parameter so the other_field/chain2 cells still
# measure their own subject.
PARAM_INIT = {"Opt[bytes]", "int|str"}
# A `-> T: return self.f` getter (and its `-> Own[T]` twin) itself rejects at
# these slots, so there the method_call source calls a method returning a
# fresh value (`return mk()`) instead of a field borrow.
GETF_OWN = {"Opt[str]", "Opt[bytes]", "tuple[int,str]", "int|str", "P|Q"}

PRELUDE = '''@IMPORTS@from typing import @TYPING@
from tpy import Own, StrView, int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Q:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


@ABLOCK@@HBLOCK@def mk() -> @R@:
    return @L@


# --- cell ---
'''


def _run_tail(slot: str, source: str, target: str) -> list[str]:
    """The behaviour run's lines after the subject: mutate the aliased source
    and print it beside the field."""
    _ty, _lit, show, mut = SLOTS[slot]
    root = ALIAS_ROOT.get(source)
    # `local` is the last use of `x` (the write may move it); `local_live` is
    # its twin with a later use.
    if not mut or root is None or source == "local":
        return []
    # `local_live` already mutates `x` after the write.
    out = [] if source == "local_live" else [mut.replace("@", root)]
    return out + [f"print({show.replace('@', root)})",
                  f"print({show.replace('@', target)})"]


def build(pos: str, slot: str, source: str, minimal_main: bool = False,
          run: bool = False) -> tuple[str, int] | None:
    """(program text, subject line number), or None for a skipped cell."""
    if skip_reason(pos, slot, source):
        return None
    params, pre, post, expr = _src(slot, source)
    recv = "hold" if pos == "local_holder" else "self"
    expr = expr.replace("@RECV@", recv)
    ty, lit, show, _mut = SLOTS[slot]
    target = "loc" if pos == "local" else f"{recv}.fld"
    if run:
        post = post + [ln.replace("@RECV@", recv)
                       for ln in _run_tail(slot, source, target)]
    rty = f"Own[{ty}]" if slot in REF_SLOTS else ty
    gty = rty if slot in GETF_OWN else ty
    gbody = "mk()" if slot in GETF_OWN else "self.f"
    getf = ("\n    def get_f(self) -> " + gty + ":\n        return " + gbody
            + "\n" if source == "method_call" else "")
    pinit = slot in PARAM_INIT
    ablock = ABLOCK if source in ("other_field", "method_call", "chain2") else ""
    hblock = HBLOCK if source == "chain2" else ""
    head = PRELUDE.replace("@ABLOCK@", ablock).replace("@HBLOCK@", hblock)
    head = head.replace("@IMPORTS@",
                        "import asyncio\n" if pos == "async_method" else "")
    head = head.replace("@TYPING@", "Iterator, Optional"
                        if pos == "gen_method" else "Optional")
    head = head.replace("@AI@", "f0" if pinit else lit)
    head = head.replace("@HP@", ", a0: A" if pinit else "")
    head = head.replace("@HI@", "a0" if pinit else "A()")
    head = head.replace("@AP@", f", f0: {ty}" if pinit else "")
    anew = f"A({lit})" if pinit else "A()"
    hnew = f"H(A({lit}))" if pinit else "H()"
    params = [(n, t, v.replace("@HNEW@", hnew).replace("@ANEW@", anew))
              for n, t, v in params]
    head = (head.replace("@GETF@", getf).replace("@R@", rty)
            .replace("@T@", ty).replace("@L@", lit))
    lines = head.split("\n")
    body: list[str] = []
    subj_line = 0

    def add_subject(indent: str, stmt: str) -> None:
        nonlocal subj_line
        body.extend(indent + p for p in pre)
        body.append(indent + stmt + "  # SUBJECT")
        subj_line = len(lines) + len(body)
        body.extend(indent + p for p in post)

    need_g2 = source == "self_field"
    if pinit and need_g2 and pos == "ctor":
        params = params + [("g0", ty, lit)]
    psig = ", ".join(f"{n}: {t}" for n, t, _ in params)
    pargs = ", ".join("v_" + n for n, _, _ in params)
    argdecl = [f"    v_{n}: {t} = {v}" for n, t, v in params]
    # `isinstance` on a union field reads through a local, which is the
    # spelling every position accepts.
    zline = "    z = @.fld" if slot == "P|Q" else "    pass"
    shown = "isinstance(z, P)" if slot == "P|Q" else show.replace(
        "@", "@.fld")

    def subj_class() -> None:
        body.extend(["class Subj:", f"    fld: {ty}"])
        if need_g2:
            body.append(f"    g2: {ty}")
        if pos == "ctor":
            return
        if pinit:
            body.extend(["", f"    def __init__(self, f0: {ty}) -> None:",
                         "        self.fld = f0"])
        else:
            body.extend(["", "    def __init__(self) -> None:",
                         f"        self.fld = {lit}"])
        if need_g2:
            body.append("        self.g2 = " + ("f0" if pinit else lit))

    sig = "self" + (", " + psig if psig else "")
    # A PARAM_INIT slot's holder takes its initial value through a typed
    # local, the spelling its constructor accepts.
    make = ([f"f0: {ty} = {lit}", "@ = Subj(f0)"] if pinit
            else ["@ = Subj()"])
    if pos == "ctor":
        subj_class()
        body.extend(["", f"    def __init__({sig}) -> None:"])
        if need_g2:
            body.append("        self.g2 = " + ("g0" if pinit else lit))
        add_subject("        ", f"self.fld = {expr}")
        driver = argdecl + [f"    s = Subj({pargs})"]
    elif pos in ("method", "gen_method", "async_method"):
        subj_class()
        if pos == "gen_method":
            body.extend(["", f"    def put({sig}) -> Iterator[int32]:"])
        elif pos == "async_method":
            body.extend(["", f"    async def put({sig}) -> None:"])
        else:
            body.extend(["", f"    def put({sig}) -> None:"])
        add_subject("        ", f"self.fld = {expr}")
        if pos == "gen_method":
            body.append("        yield 1")
        driver = argdecl + ["    " + m.replace("@", "s") for m in make]
        if pos == "gen_method":
            driver += [f"    for _ in s.put({pargs}):", "        pass"]
        elif pos == "async_method":
            driver += [f"    asyncio.run(s.put({pargs}))"]
        else:
            driver += [f"    s.put({pargs})"]
    elif pos == "local_holder":
        subj_class()
        body.extend(["", "", f"def probe({psig}) -> None:"]
                    + ["    " + m.replace("@", "hold") for m in make])
        add_subject("    ", f"hold.fld = {expr}")
        body.extend([zline.replace("@", "hold"),
                     f"    print({shown.replace('@', 'hold')})"])
        driver = argdecl + [f"    probe({pargs})"]
    else:
        body.append(f"def probe({psig}) -> None:")
        add_subject("    ", f"loc: {ty} = {expr}")
        body.append(f"    print({show.replace('@', 'loc')})")
        driver = argdecl + [f"    probe({pargs})"]
    if pos in ("local_holder", "local"):
        body.extend(["", "", "def main() -> None:"] + driver)
    else:
        body.extend(["", "", "def main() -> None:"] + driver
                    + [zline.replace("@", "s"),
                       f"    print({shown.replace('@', 's')})"])
    if minimal_main:
        cut = body.index("def main() -> None:")
        body = body[:cut + 1] + ["    pass"]
    body.extend(["", "", "main()", ""])
    return "\n".join(lines + body), subj_line


def cell_key(pos: str, slot: str, source: str) -> str:
    return f"{pos}__{slot}__{source}"


def safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", key)


def all_cells(only: str = "") -> tuple[list[tuple[str, str, str]],
                                       list[tuple[str, str, str, str]]]:
    cells, skipped = [], []
    for pos in POSITIONS:
        for slot in SLOTS:
            for source in SOURCES:
                if only and only not in cell_key(pos, slot, source):
                    continue
                why = skip_reason(pos, slot, source)
                if why:
                    skipped.append((pos, slot, source, why))
                else:
                    cells.append((pos, slot, source))
    return cells, skipped


# ---------------------------------------------------------------------------
# Compilation and render extraction.
# ---------------------------------------------------------------------------

DIAG_RE = re.compile(r"^(\S+?):(\d+): (error|warning): (.*)$")


def _match_close(text: str, i: int) -> int:
    """Index just past the bracket group opening at text[i]."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = [pairs[text[i]]]
    j = i + 1
    in_str = None
    while j < len(text) and stack:
        ch = text[j]
        if in_str:
            if ch == "\\":
                j += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in ")}]":
            stack.pop()
        j += 1
    return j


def _stmt_end(text: str, i: int) -> int:
    """Index of the ';' ending the statement starting at i (depth 0)."""
    j = i
    while j < len(text):
        ch = text[j]
        if ch in "({[":
            j = _match_close(text, j)
            continue
        if ch == ";":
            return j
        j += 1
    return j


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= 220 else s[:217] + "..."


def _func_bodies(text: str, sig_re: str):
    """Yield (init-list text or '', body text) of every DEFINITION matching
    `sig_re` (which must end just before or at the parameter list's `(`)."""
    for m in re.finditer(sig_re, text):
        p = text.index("(", m.end() - 1)
        k = _match_close(text, p)
        # skip trailing qualifiers such as `const` or a return spelling
        while k < len(text) and text[k] not in ":{;":
            k += 1
        if k >= len(text) or text[k] == ";":
            continue
        init = ""
        if text[k] == ":" and text[k:k + 2] != "::":
            # A member initialiser can itself be braced (`fld{...}`): a `{`
            # right after an identifier opens one, any other opens the body.
            j = k + 1
            body_open = None
            while j < len(text):
                ch = text[j]
                if ch in "([":
                    j = _match_close(text, j)
                    continue
                if ch == "{":
                    prev = text[j - 1]
                    if prev.isalnum() or prev in "_>":
                        j = _match_close(text, j)
                        continue
                    body_open = j
                    break
                j += 1
            if body_open is None:
                continue
            init = text[k + 1:body_open]
            k = body_open
        elif text[k] == ":":
            continue
        bend = _match_close(text, k)
        yield init, text[k + 1:bend - 1]


def _split_top(s: str) -> list[str]:
    out, cur, j = [], 0, 0
    while j < len(s):
        if s[j] in "({[":
            j = _match_close(s, j)
            continue
        if s[j] == ",":
            out.append(s[cur:j])
            cur = j + 1
        j += 1
    out.append(s[cur:])
    return [x.strip() for x in out]


FLD_WRITE = re.compile(r"[\w>.\-]*\bfld\s*=(?!=)")


def _body_write(body: str) -> str | None:
    m = FLD_WRITE.search(body)
    if m is None:
        return None
    return "body: " + _norm(body[m.start():_stmt_end(body, m.start())])


def extract_render(pos: str, code: str) -> str:
    if pos in ("ctor", "method"):
        name = "Subj" if pos == "ctor" else "put"
        for init, body in _func_bodies(code, r"\bSubj::" + name + r"\("):
            if init:
                for ent in _split_top(init):
                    if re.match(r"fld\s*[({]", ent):
                        return "init: " + _norm(ent)
            return _body_write(body) or "<no fld write found>"
        return "<no definition found>"
    if pos in ("gen_method", "async_method"):
        frame = "__gen_Subj_put" if pos == "gen_method" else "__coro_Subj_put"
        found = False
        for _init, body in _func_bodies(code, r"\b" + frame + r"::~?\w+\("):
            found = True
            r = _body_write(body)
            if r:
                return r
        return "<no fld write found>" if found else "<no definition found>"
    for _init, body in _func_bodies(code, r"\bprobe\("):
        if pos == "local_holder":
            return _body_write(body) or "<no fld write found>"
        m = re.search(r"[^\n;{}]*\bloc\s*(=[^=]|\(|\{)", body)
        if m is None:
            return "<no loc decl found>"
        return "decl: " + _norm(body[m.start():_stmt_end(body, m.start())])
    return "<no definition found>"


def _tool(repo: Path, name: str) -> list[str]:
    exe = repo / ".venv" / "bin" / name
    return [str(exe)] if exe.exists() else ["uv", "run", name]


def compile_cell(repo: Path, work: Path, pos: str, text: str,
                 subj: int) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    src = work / "main.py"
    src.write_text(text)
    out = work / "out"
    proc = subprocess.run(_tool(repo, "tpyc") + [str(src), "-o", str(out)],
                          cwd=repo, capture_output=True, text=True,
                          timeout=300)
    errs, warns = [], []
    for line in (proc.stderr + proc.stdout).splitlines():
        m = DIAG_RE.match(line.strip())
        if m and m.group(1).endswith("main.py"):
            ln = int(m.group(2))
            (errs if m.group(3) == "error" else warns).append(
                (ln, m.group(4).replace(str(work), "<cell>")))
    tl = text.split("\n")
    cell_start = tl.index("# --- cell ---") + 1
    main_line = next(i for i, ln in enumerate(tl, 1)
                     if ln.startswith("def main("))
    res = {
        "warn": " | ".join((f"[L{ln - subj:+d}] " if ln != subj else "") + msg
                           for ln, msg in warns if cell_start < ln < main_line),
        "prelude_warn": " | ".join(msg for ln, msg in warns
                                   if ln <= cell_start),
        "render": "", "where": "", "msg": "",
    }
    # `-o` copies ~1 MB of runtime sources per cell; 1500 cells would leave
    # over a gigabyte behind. The module's own C++ is kept for inspection.
    shutil.rmtree(out / "runtime", ignore_errors=True)
    if proc.returncode != 0:
        if errs:
            ln, msg = errs[0]
            res["verdict"] = "error"
            res["where"] = "subject" if ln == subj else (
                "prelude" if ln <= cell_start else f"line{ln - subj:+d}")
            res["msg"] = msg
            res["in_main"] = ln >= main_line
        else:
            tail = [ln for ln in proc.stderr.splitlines() if ln.strip()]
            res["verdict"] = "crash"
            res["where"] = "?"
            res["msg"] = tail[-1][:200] if tail else f"rc={proc.returncode}"
        return res
    code = "".join(f.read_text() + "\n" for f in
                   (out / "include" / "main.hpp",
                    out / "include" / "main_inl.hpp",
                    out / "src" / "main.cpp") if f.exists())
    res["verdict"] = "warning" if res["warn"] else "ok"
    res["render"] = extract_render(pos, code)
    return res


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------

# (pos, slot, source) -> (class, reason); pos may be "*".
CLASS_OVERRIDES: dict[tuple[str, str, str], tuple[str, str]] = {}

# Sources that hand a StrView slot a view over a temporary str.
VIEW_TEMP_SOURCES = {"binop", "conv"}


def classify(pos: str, slot: str, source: str, r: dict) -> tuple[str, str]:
    for key in ((pos, slot, source), ("*", slot, source)):
        if key in CLASS_OVERRIDES:
            return CLASS_OVERRIDES[key]
    if r["verdict"] in ("ok", "warning"):
        return ("", "")
    if r["verdict"] == "crash":
        return ("?", "compiler crash")
    msg = r["msg"]
    tag = msg[msg.rfind("(") + 1:-1] if msg.endswith(")") else ""
    if r["where"] == "prelude":
        return ("?", "setup: the holder's getter itself rejects (" + tag
                + "); cell unmeasurable")
    if r["where"] != "subject":
        if "(res." in msg:
            return ("?", "frame rejects a local/param before the subject ("
                    + tag + "); cell unmeasurable")
        return ("?", "error off the subject line (" + r["where"] + ")")
    if slot == "StrView" and source in VIEW_TEMP_SOURCES:
        return ("E", "view slot fed from a temporary str (dangles)")
    if "not yet supported" in msg:
        if slot == "StrView":
            return ("R", "view copy of a view with the admitted param's "
                    "lifetime class; lowering reject by kind")
        return ("R", "lowering reject by source kind")
    if "got Span[" in msg:
        return ("?", "list slice types as Span view (language rule, every "
                "position)")
    if "Invalid operand types for '|'" in msg:
        return ("R", "sema: set/dict literal operand typed int32, not "
                "context-typed (every position)")
    if "cannot be constructed from" in msg:
        return ("R", "sema: dict(d) copy-construct unsupported (every "
                "position)")
    return ("?", "sema error")


def run_cell(repo: Path, work_root: Path, cell: tuple[str, str, str]
             ) -> tuple[str, list[str]]:
    pos, slot, source = cell
    key = cell_key(pos, slot, source)
    text, subj = build(pos, slot, source)
    try:
        r = compile_cell(repo, work_root / safe(key), pos, text, subj)
        if r.get("in_main"):
            # The driver in main() failed, not the cell: re-verdict the cell
            # with an empty main and keep the driver error as a note.
            note = r["msg"]
            t2, s2 = build(pos, slot, source, minimal_main=True)
            r = compile_cell(repo, work_root / (safe(key) + "__nomain"), pos,
                             t2, s2)
            r["main_note"] = note
    except Exception as exc:  # noqa: BLE001
        r = {"verdict": "crash", "where": "?", "msg": str(exc)[:200],
             "render": "", "warn": "", "prelude_warn": ""}
    cls, why = classify(pos, slot, source, r)
    if r["verdict"] == "error":
        detail = f"[{r['where']}] {r['msg']}"
    elif r["verdict"] == "crash":
        detail = r["msg"]
    else:
        detail = r["warn"]
    if r.get("main_note"):
        detail = ((detail + " " if detail else "")
                  + "{driver-main error: " + r["main_note"] + "}")
    return key, [r["verdict"], detail, r["render"], cls, why,
                 r["prelude_warn"]]


def survey(repo: Path, work_root: Path, cells, jobs: int
           ) -> dict[str, list[str]]:
    done = 0
    t0 = time.monotonic()

    def one(c):
        nonlocal done
        res = run_cell(repo, work_root, c)
        done += 1
        if os.environ.get("SWEEP_PROGRESS") and done % 50 == 0:
            print(f"  {done}/{len(cells)} {time.monotonic() - t0:.0f}s",
                  file=sys.stderr, flush=True)
        return res

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return dict(ex.map(one, cells))


# ---------------------------------------------------------------------------
# The behaviour run: a representative subset built and compared with CPython.
# ---------------------------------------------------------------------------

# One admitted cell per (slot family x render class) of the master baseline,
# biased to the reference slots with an aliasable source -- where a copy
# against CPython's alias is observable. The render classes: member-init copy
# (`init: fld(a)`, `fld(o.f)`, `fld((c) ? (a) : (b))`), assignment copy
# (`this->fld = x`, `this->g2`, `__getitem__(xs, 0)`, `o.get_f()`), move of
# a last-use local (`std::move(x)`, the frame's `std::move((*x))`), fresh
# rvalue (`mk()`, a comprehension), the Optional / union conversions
# (`ptr_to_optional_move`, `to_value_variant`), the frame's `__self.fld`, the
# local holder's `hold.fld`, one cell per value-slot family, and the control.
RUN_CELLS: tuple[tuple[str, str, str], ...] = (
    ("ctor", "list[int]", "param"),
    ("ctor", "P", "param"),
    ("ctor", "P", "cond"),
    ("ctor", "P", "other_field"),
    ("ctor", "dict[str,int]", "local_live"),
    ("ctor", "set[int]", "local"),
    ("ctor", "list[int]", "comp"),
    ("ctor", "str", "param"),
    ("ctor", "tuple[int,str]", "param"),
    ("method", "list[int]", "method_call"),
    ("method", "dict[str,int]", "other_field"),
    ("method", "P", "elem"),
    ("method", "P", "self_field"),
    ("method", "list[int]", "call"),
    ("method", "Opt[P]", "local"),
    ("method", "P|Q", "param"),
    ("method", "StrView", "param"),
    ("method", "Opt[str]", "none"),
    ("gen_method", "list[int]", "param"),
    ("gen_method", "P", "local"),
    ("async_method", "P", "local_live"),
    ("async_method", "set[int]", "self_field"),
    ("local_holder", "list[int]", "param"),
    ("local_holder", "P", "dict_get"),
    ("local", "list[int]", "param"),
)

# The integer-width block: (position, field type, source type or None for a
# literal, value). The out-of-range value into a narrower field exposes a
# silent wrap where CPython keeps the value, if the write is ever admitted.
# An int into a `float` field prints `100.0` where CPython keeps `100`: the
# documented numeric-tower divergence ("Only a declared `float` converts an
# int", docs/LANGUAGE_FEATURES.md), reported as DIFFERS-DECLARED.
INT_CELLS: tuple[tuple[str, str, str | None, str], ...] = (
    ("method", "int8", "int32", "100"),
    ("method", "int8", "int32", "300"),
    ("method", "int8", None, "100"),
    ("method", "int32", "int8", "100"),
    ("method", "int32", "int64", "100"),
    ("method", "int64", "int32", "100"),
    ("method", "int64", "int", "100"),
    ("method", "int", "int64", "100"),
    ("method", "int", "int32", "100"),
    ("method", "float", "int32", "100"),
    ("method", "float", "int", "100"),
    ("method", "float", None, "100"),
    ("ctor", "int8", "int32", "300"),
    ("ctor", "int32", "int64", "100"),
    ("ctor", "int", "int32", "100"),
    ("ctor", "float", "int32", "100"),
)


def build_int(pos: str, fty: str, sty: str | None, val: str
              ) -> tuple[str, int]:
    init = "0.0" if fty == "float" else "0"
    src = "a" if sty else val
    sig = f", a: {sty}" if sty else ""
    lines = ["from tpy import int8, int32, int64", "", "",
             "class Subj:", f"    fld: {fty}", ""]
    if pos == "ctor":
        lines += [f"    def __init__(self{sig}) -> None:",
                  f"        self.fld = {src}  # SUBJECT"]
        subj = len(lines)
        call = [f"    s = Subj({'v_a' if sty else ''})"]
    else:
        lines += ["    def __init__(self) -> None:",
                  f"        self.fld = {init}", "",
                  f"    def put(self{sig}) -> None:",
                  f"        self.fld = {src}  # SUBJECT"]
        subj = len(lines)
        call = ["    s = Subj()", f"    s.put({'v_a' if sty else ''})"]
    lines += ["", "", "def main() -> None:"]
    if sty:
        lines.append(f"    v_a: {sty} = {val}")
    lines += call + ["    print(s.fld)", "", "", "main()", ""]
    return "\n".join(lines), subj


def _behaviour(repo: Path, d: Path, subj: int) -> tuple[str, str, str]:
    """(outcome, subject-line warning, detail)."""
    src = d / "main.py"
    tpy = subprocess.run(_tool(repo, "tpy") + ["-j", "2", str(src)],
                         cwd=d, capture_output=True, text=True, timeout=900)
    # The build tree is ~190 MB a program and is never reused.
    shutil.rmtree(d / "__tpyc__", ignore_errors=True)
    warn = ""
    err = ""
    for line in tpy.stderr.splitlines():
        m = DIAG_RE.match(line.strip())
        if m and m.group(1).endswith("main.py"):
            if m.group(3) == "warning" and int(m.group(2)) == subj and not warn:
                warn = m.group(4)
            if m.group(3) == "error" and not err:
                err = f"L{int(m.group(2)) - subj:+d}: {m.group(4)}"
    if tpy.returncode != 0:
        if err:
            return "tpy-reject", warn, err
        tail = [ln for ln in tpy.stderr.splitlines() if ln.strip()]
        return ("build-fail" if ".cpp:" in tpy.stderr else "run-fail"), warn, (
            tail[-1][:160] if tail else "")
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{repo / 'lib' / 'cpy'}:{d}"
    cpy = subprocess.run(["uv", "run", "--project", str(repo), "python",
                          "main.py"], cwd=d, capture_output=True, text=True,
                         env=env, timeout=120)
    if cpy.returncode != 0:
        tail = [ln for ln in cpy.stderr.splitlines() if ln.strip()]
        return "cpython-fail", warn, tail[-1][:160] if tail else ""
    if tpy.stdout == cpy.stdout:
        return "same", warn, ""
    return "differs", warn, ("tpy " + tpy.stdout.strip().replace("\n", "/")
                             + "  cpy " + cpy.stdout.strip().replace("\n", "/"))


def behaviour_run(repo: Path, work_root: Path, jobs: int,
                  only: str = "") -> int:
    progs: list[tuple[str, str, int, bool]] = []
    for pos, slot, source in RUN_CELLS:
        progs.append((cell_key(pos, slot, source),
                      *build(pos, slot, source, run=True), False))
    for pos, fty, sty, val in INT_CELLS:
        progs.append((f"int:{pos}__{fty}__{sty or 'literal'}={val}",
                      *build_int(pos, fty, sty, val), fty == "float"))
    progs = [p for p in progs if only in p[0]]
    dirs: list[Path] = []
    for key, text, _subj, _decl in progs:
        d = work_root / "run" / safe(key)
        d.mkdir(parents=True, exist_ok=True)
        (d / "main.py").write_text(text)
        dirs.append(d)
    print(f"building and running {len(progs)} programs (-j {jobs})",
          file=sys.stderr)
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        seen = list(ex.map(lambda i: _behaviour(repo, dirs[i], progs[i][2]),
                           range(len(progs))))
    print(f"{time.monotonic() - t0:.0f}s", file=sys.stderr)
    bad = 0
    counts: dict[str, int] = {}
    for (key, _t, _s, declared), (outcome, warn, detail) in zip(progs, seen):
        label = outcome
        if outcome == "differs":
            label = ("DIFFERS+warn" if warn else
                     "DIFFERS-DECLARED" if declared else "DIFFERS-SILENT")
        if outcome in ("build-fail", "run-fail", "cpython-fail") or \
                label == "DIFFERS-SILENT":
            bad += 1
        counts[label] = counts.get(label, 0) + 1
        line = f"{label:16} {key}"
        if warn:
            line += f"  [warn: {warn}]"
        if detail:
            line += f"  {detail}"
        print(line)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

ABBR = {"literal": "lit", "param": "par", "local": "loc", "local_live": "liv",
        "other_field": "ofl", "self_field": "sfl", "call": "cal",
        "method_call": "mcl", "binop": "bin", "slice": "slc", "elem": "elm",
        "dict_get": "dgt", "cond": "cnd", "comp": "cmp", "walrus": "wal",
        "tuple_elem": "tel", "chain2": "ch2", "conv": "cnv", "none": "non"}


def _sym(row: list[str] | None) -> str:
    if row is None:
        return "-"
    if row[0] == "ok":
        return "."
    if row[0] == "warning":
        return "W"
    return row[3] or "?"


def print_grid(table: dict[str, list[str]]) -> None:
    print("legend: . ok  W warning  R rejects valid  E justified  "
          "? unclassified  - skipped")
    for pos in POSITIONS:
        print(f"== {pos}")
        print(f"{'':15}" + " ".join(ABBR[s] for s in SOURCES))
        for slot in SLOTS:
            print(f"{slot:15}" + " ".join(
                f"{_sym(table.get(cell_key(pos, slot, s))):^3}"
                for s in SOURCES))
        print()


def print_counts(table: dict[str, list[str]]) -> None:
    for pos in POSITIONS:
        rows = [v for k, v in table.items() if k.startswith(pos + "__")]
        if not rows:
            continue
        c: dict[str, int] = {}
        for v in rows:
            label = v[0] if v[0] in ("ok", "warning") else f"{v[0]}:{v[3]}"
            c[label] = c.get(label, 0) + 1
        print(f"{pos:13} {len(rows):4} cells  " + "  ".join(
            f"{k}={n}" for k, n in sorted(c.items())))


def print_skips(skipped, full: bool) -> None:
    print(f"{len(skipped)} cells skipped (no program written):")
    if full:
        for pos, slot, source, why in skipped:
            print(f"  {cell_key(pos, slot, source)}: {why}")
        return
    grouped: dict[tuple[str, str], list[str]] = {}
    for pos, slot, source, why in skipped:
        grouped.setdefault((source, why), []).append(f"{pos}/{slot}")
    for (source, why), where in sorted(grouped.items()):
        slots = sorted({w.split("/", 1)[1] for w in where})
        poss = sorted({w.split("/", 1)[0] for w in where})
        print(f"  {source}: {why} -- {len(where)} cells, positions "
              f"{','.join(poss)}; slots {', '.join(slots)}")


def _load_explain(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    return json.loads(path.read_text())


def _explained(key: str, explain: dict[str, str]) -> str | None:
    for pat, why in explain.items():
        if fnmatch.fnmatchcase(key, pat):
            return why
    return None


def check(table: dict[str, list[str]], want: dict[str, list[str]],
          explain: dict[str, str], only: str) -> int:
    moved = {k: (want.get(k), v) for k, v in table.items() if want.get(k) != v}
    gone = sorted(k for k in set(want) - set(table)
                  if not only or only in k)
    unexplained = 0
    for k, (was, now) in sorted(moved.items()):
        why = _explained(k, explain)
        if was is None:
            head = "NEW"
        elif was[3] == "R" and now[0] in ("ok", "warning"):
            head = "PROGRESS"
        elif why:
            head = "EXPLAINED"
        else:
            head = "MOVED"
        if head in ("NEW", "MOVED") and not why:
            unexplained += 1
        print(f"{head} {k}" + (f"  ({why})" if why else ""))
        print(f"  was {was}")
        print(f"  now {now}")
    for k in gone:
        print(f"GONE  {k}: {want[k]}")
    unexplained += len(gone)
    if not moved and not gone:
        print(f"{len(table)} cells, nothing moved")
        return 0
    print(f"{len(moved)} moved, {len(gone)} gone, {unexplained} unexplained")
    return 1 if unexplained else 0


def main() -> int:
    global REPO
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the committed verdict table")
    ap.add_argument("--only", default="",
                    help="only cells whose key contains this substring")
    ap.add_argument("--repo", type=Path, default=None,
                    help="the checkout to compile against")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the generated programs")
    ap.add_argument("--jobs", "-j", type=int,
                    default=min(4, os.cpu_count() or 1),
                    help="parallel compilations (default: min(4, CPUs))")
    ap.add_argument("--explain", type=Path, default=None,
                    help="JSON {cell key or fnmatch pattern: reason} of "
                         "expected moves")
    ap.add_argument("--grid", action="store_true",
                    help="print the per-position grids of the committed "
                         "table (of the fresh run with --fresh) and exit")
    ap.add_argument("--fresh", action="store_true",
                    help="with --grid: survey first and grid the result")
    ap.add_argument("--skips", action="store_true",
                    help="print every skipped cell with its reason")
    ap.add_argument("--run", action="store_true",
                    help="build and run the behaviour subset against CPython "
                         "instead of the verdict survey")
    ap.add_argument("--raw", type=Path, default=None,
                    help="also dump the fresh table here")
    args = ap.parse_args()
    if args.repo is not None:
        REPO = args.repo.resolve()
    want: dict[str, list[str]] = (
        json.loads(EXPECTED.read_text()) if EXPECTED.exists() else {})

    if args.grid and not args.fresh:
        print_grid(want)
        print_counts(want)
        return 0

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="field_admission_"))
    if args.run:
        return behaviour_run(REPO, out_dir, min(args.jobs, 2), args.only)

    cells, skipped = all_cells(args.only)
    print_skips(skipped, args.skips)
    t0 = time.monotonic()
    table = survey(REPO, out_dir, cells, args.jobs)
    print(f"{len(cells)} cells in {out_dir}, {time.monotonic() - t0:.0f}s",
          file=sys.stderr)
    if args.raw:
        args.raw.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
    print_counts(table)
    if args.grid:
        print_grid(table)

    if args.update:
        if args.only:
            print("--only cannot rewrite the table: it would drop every cell "
                  "the filter excluded", file=sys.stderr)
            return 2
        EXPECTED.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        print(f"wrote {EXPECTED} ({len(table)} cells)")
        return 0
    return check(table, want, _load_explain(args.explain), args.only)


if __name__ == "__main__":
    raise SystemExit(main())
