"""The conversion boundary's differential matrix: every (source, sink,
position) cell compiled under two trees and compared.

TODO.md "One conversion boundary for every value sink": a sink migrated onto
`convert` must keep every cell's verdict (compiles / which reject) and its
C++ unless the change is a decided one. This script generates one program
per cell, compiles it with `tpyc --dump-code` under the current tree and
under `--base <checkout>`, classifies each side -- the reject reason, the
warnings, the C++ of the cell's function -- and prints the cells that
differ. A manual instrument: minutes, not part of the pytest run.

    uv run python scripts/thir_migration/review/convert_matrix.py \\
        --base ../tpy-m4 [--jobs 4] [--out build/convert_matrix] [-k SUBSTR]
        [--known KNOWN.json] [--build] [--pin-known] [--no-prune]
        [--json CELLS.json]

Each side compiles in its own checkout through `uv run --project <tree>`.
Programs and dumps land under `--out` (default `build/convert_matrix`, not
committed). A cell a position cannot spell (a receiver in a free function,
a member-init outside a constructor) or whose source type the sink does not
take is skipped.

The sinks are the return / yield (`ret_*`), the field write and the
member-init, and the call argument (`arg_*`: `take_*(<e>)` at `P`,
`readonly[P]`, `Own[P]`, `P | None`, `str`, `Own[str]`, `*args: P`, a
structural protocol, an open `T`, `list[P]`, `dict[str, P]`).

A (source, sink) PAIR that sema rejects at its first position, with the
same error on both trees, is compiled at that position only and reported
once as `<src>__<sink>__sema`: the rejection is a type question, so the
other positions would repeat it. `--no-prune` compiles every position.
`--json` writes each cell's result on both sides.

`--known` names the cells whose difference is a USER DECISION, by exact
cell id: {"<src>__<sink>__<pos>": {"decided": "<who>, <date>", "reason":
..., "expect": {verdict, reason?, warnings, minus, plus, cpp_sha256}}}.
`expect` pins what the decided cell looks like on the current tree (its
verdict, its warnings, its C++ lines against the base and a hash of its
whole C++), so a later change in a decided cell is reported, not
absorbed. `--pin-known` writes `expect` for
every listed id from this run. A known entry never records a fix.

`--build` compiles both sides of every differing cell to a binary with the
project toolchain (`tpyc -b`); a cell that builds on the base and not on
the current tree is a regression. Exit 1 when an unknown cell differs, a
decided cell moved, or a build regressed.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                    ".."))

PRELUDE = '''\
from typing import Iterator, Optional, Self

from tpy import Own, readonly


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Q(P):
    def __init__(self, v: int) -> None:
        super().__init__(v)


def pick(p: P) -> P:
    return p


def mk() -> Own[P]:
    return P(1)


def sval() -> str:
    return "x" + "y"
'''

# What a cell's source or sink adds to the prelude. A cell whose source and
# sink need none of it compiles exactly PRELUDE, so every cell that predates
# a piece keeps its program byte for byte (and a decided cell its pinned
# C++).
EXTRA_IMPORTS = {
    "any": "from typing import Any",
    "callable": "from typing import Callable",
    "protocol": "from typing import Protocol",
    "copy": "from tpy import copy",
}
PIECES = {
    # A record unrelated to P, for the pointer-variant union `P | S`.
    "S": '''

class S:
    w: int

    def __init__(self, w: int) -> None:
        self.w = w
''',
    # A stored record behind a getter and behind a method declaring a
    # reference result.
    "K": '''

class K:
    p: P

    def __init__(self) -> None:
        self.p = P(0)

    @property
    def pp(self) -> P:
        return self.p

    def get(self) -> P:
        return self.p
''',
    "HasV": '''

class HasV(Protocol):
    v: int
''',
}
# Each argument sink's callee. `take_ref` writes through its parameter so
# it stays a mutable reference, apart from the `readonly[P]` one.
CALLEES = {
    "take_ref": ("p: P", "p.v += 1"),
    "take_ro": ("p: readonly[P]", "print(p.v)"),
    "take_own": ("p: Own[P]", "keep.append(p)"),
    "take_opt": ("p: P | None", "print(p is None)"),
    "take_str": ("p: str", "print(len(p))"),
    "take_own_str": ("p: Own[str]", "print(len(p))"),
    "take_args": ("*args: P", "print(len(args))"),
    "take_proto": ("p: HasV", "print(1)"),
    "take_t[T]": ("p: T", "print(1)"),
    "take_list": ("xs: list[P]", "print(len(xs))"),
    "take_dict": ("d: dict[str, P]", "print(len(d))"),
}

# Each source: (params, setup lines, the expression, needs a receiver, the
# family of its type). `{recv}` is `self` in a method / constructor and the
# `h` parameter elsewhere.
SOURCES = {
    "param": ("p: P", [], "p", False, "P"),
    "readonly_param": ("p: readonly[P]", [], "p", False, "P"),
    "local_last_use": ("", ["x = P(1)"], "x", False, "P"),
    "live_local": ("", ["x = P(1)", "", "def peek() -> int:",
                        "    return x.v", "", "n = peek()"], "x", False,
                   "P"),
    "field": ("", [], "{recv}.f", False, "P"),
    "list_elem": ("xs: list[P]", [], "xs[0]", False, "P"),
    "tuple_elem_local": ("", ["t = (P(1), 2)"], "t[0]", False, "P"),
    "tuple_elem_own_param": ("t: Own[tuple[P, int]]", [], "t[0]", False,
                             "P"),
    "borrow_call": ("p: P", [], "pick(p)", False, "P"),
    "value_call": ("", [], "mk()", False, "P"),
    "ternary": ("a: P, b: P, c: bool", [], "a if c else b", False, "P"),
    "walrus": ("p: P", [], "(w := p)", False, "P"),
    "self_consuming": ("", [], "self", True, "P"),
    "self_plain": ("", [], "self", True, "P"),
    "subclass_local": ("", ["q = Q(1)"], "q", False, "P"),
    "str_param": ("s: str", [], "s", False, "str"),
    "str_local": ("", ['s = "a" + "b"'], "s", False, "str"),
    "bytes_param": ("b: bytes", [], "b", False, "bytes"),
    "bytes_local": ("", ['b = b"a" + b"b"'], "b", False, "bytes"),
    "str_select_lit": ("a: str, c: bool", [], 'a if c else "lit"', False,
                       "str"),
    "str_or_lit": ("a: str", [], 'a or "lit"', False, "str"),
    "opt_str_narrowed": ("a: Optional[str]", ["assert a is not None"], "a",
                         False, "str"),
    "str_call": ("", [], "sval()", False, "str"),
    # Binding sources: the record a read resolves to through a scope, a
    # region, a frame or an overload's stub default. A cell pins the C++
    # the sink renders from that record against the base tree, so a record
    # that answers differently shows as a differing cell.
    "comp_shadowed_local": ("xs: list[P]", [
        "x = P(1)", "n = len([x.v for x in xs])", "x.v = n"], "x", False,
        "P"),
    "walrus_leaked": ("p: P", [
        "if (w := pick(p)).v > 0:", "    w.v = 2"], "w", False, "P"),
    "nonlocal_rebound": ("", [
        "x = P(1)", "", "def bump() -> None:", "    nonlocal x",
        "    x = P(2)", "", "bump()", "x.v = 3"], "x", False, "P"),
    "own_param": ("p: Own[P]", ["p.v = 2"], "p", False, "P"),
    "comp_element_outer": ("", [
        "x = P(1)", "ys = [x for _i in range(2)]", "ys[0].v = 5"], "x",
        False, "P"),
    "stub_default_local": ('s: str = "d"', [], "s", False, "str"),
    "frame_local": ("", ["x = P(1)", "{suspend}", "x.v = 4"], "x", False,
                    "P"),
    # The source families landing 2's deleted rows covered. A setup line
    # may name `{recv}` too.
    "fresh_ternary": ("c: bool", [], "P(1) if c else P(2)", False, "P"),
    "mixed_ternary": ("p: P, c: bool", [], "mk() if c else p", False, "P"),
    "record_or": ("a: P, b: P", [], "a or b", False, "P"),
    "list_select": ("xs: list[P], ys: list[P], c: bool", [],
                    "xs if c else ys", False, "list"),
    "dict_select": ("da: dict[str, P], db: dict[str, P], c: bool", [],
                    "da if c else db", False, "dict"),
    "opt_param": ("o: Optional[P]", [], "o", False, "optP"),
    "opt_field": ("", [], "{recv}.o", False, "optP"),
    "union_param": ("u: P | S", [], "u", False, "PS"),
    "tuple_local": ("", ["t = (P(1), 2)"], "t", False, "tupPi"),
    "borrow_tuple_param": ("t: tuple[P, int]", [], "t", False, "tupPi"),
    # The owning `tuple[str, P]` local: sema refuses `Own[...]` spelled on
    # a local as redundant, a local owns its value inline.
    "own_tuple_local": ("", ['t = ("a", P(1))'], "t",
                        False, "tupSP"),
    "own_tuple_param": ("t: Own[tuple[str, P]]", [], "t", False, "tupSP"),
    "prop_getter": ("k: K", [], "k.pp", False, "P"),
    "ref_result_call": ("k: K", [], "k.get()", False, "P"),
    "ptr_local": ("xs: list[P], c: bool", [
        "q = xs[0] if c else None", "assert q is not None"], "q", False,
        "P"),
    "own_list_elem": ("xs: list[Own[P]]", [], "xs[0]", False, "P"),
    "narrowed_opt_member": ("o: Optional[K]", ["assert o is not None"],
                            "o.p", False, "P"),
    "narrowed_opt_field": ("", ["assert {recv}.o is not None"], "{recv}.o",
                           False, "P"),
    "copy_call": ("p: P", [], "copy(p)", False, "P"),
}

# A lambda body is one more return position: `lambda: <src>` over a P
# source, at a `Callable[[], P]` slot.
LAMBDA_OVER = ("param", "field", "list_elem", "local_last_use", "borrow_call",
               "value_call", "fresh_ternary", "prop_getter", "ptr_local")
for _name in LAMBDA_OVER:
    _params, _setup, _expr, _self, _fam = SOURCES[_name]
    SOURCES["lambda_" + _name] = (_params, _setup, "lambda: " + _expr, _self,
                                  "fn_P")

# What each source needs beyond PRELUDE (see PIECES / EXTRA_IMPORTS).
SOURCE_NEEDS = {
    "union_param": ("S",),
    "prop_getter": ("K",), "ref_result_call": ("K",),
    "narrowed_opt_member": ("K",),
    "lambda_prop_getter": ("K",),
    "copy_call": ("copy",),
}

# Sources a position cannot spell on its own: a stub-default local needs
# the function to be an overloaded implementation (two stubs, one taking
# the defaulted param); a frame local needs a suspension in the body.
STUB_DEFAULT_SOURCES = ("stub_default_local",)
FRAME_SOURCES = ("frame_local",)
TICK = "\n\nasync def tick() -> None:\n    pass\n"

# Each sink: (declared result or None for a field, the statement, the
# source families it takes).
SINKS = {
    "ret_ref": ("P", "return {e}", ("P",)),
    "ret_opt": ("Optional[P]", "return {e}", ("P", "optP")),
    "ret_own": ("Own[P]", "return {e}", ("P",)),
    "ret_tuple": ("tuple[P, int]", "return ({e}, 1)", ("P",)),
    "ret_str": ("str", "return {e}", ("str",)),
    "ret_opt_str": ("Optional[str]", "return {e}", ("str",)),
    "field": (None, "{recv}.p = {e}", ("P",)),
    "field_opt": (None, "{recv}.o = {e}", ("P", "optP")),
    "field_str": (None, "{recv}.s = {e}", ("str",)),
    "field_str_int": (None, "{recv}.u = {e}", ("str",)),
    "field_opt_str": (None, "{recv}.os = {e}", ("str",)),
    "field_bytes": (None, "{recv}.b = {e}", ("bytes",)),
    "member_init": (None, "self.p = {e}", ("P",)),
    "member_init_opt": (None, "self.o = {e}", ("P", "optP")),
    "member_init_str": (None, "self.s = {e}", ("str",)),
    "member_init_str_int": (None, "self.u = {e}", ("str",)),
    "member_init_opt_str": (None, "self.os = {e}", ("str",)),
    "member_init_bytes": (None, "self.b = {e}", ("bytes",)),
    # The return slots landing 2 adds rows for.
    "ret_tuple_whole": ("tuple[P, int]", "return {e}", ("tupPi",)),
    "ret_own_tuple": ("Own[tuple[str, P]]", 'return ("a", {e})', ("P",)),
    "ret_own_tuple_whole": ("Own[tuple[str, P]]", "return {e}", ("tupSP",)),
    "ret_int_str": ("int | str", "return {e}", ("str",)),
    "ret_any": ("Any", "return {e}", ("P", "optP", "str", "list")),
    "ret_union": ("P | S", "return {e}", ("P", "PS")),
    "ret_callable": ("Callable[[], P]", "return {e}", ("fn_P",)),
    "ret_list": ("list[P]", "return {e}", ("list",)),
    "ret_dict": ("dict[str, P]", "return {e}", ("dict",)),
    # The call argument: `take_*(<e>)` at each parameter shape.
    "arg_ref": (None, "take_ref({e})", ("P",)),
    "arg_ro": (None, "take_ro({e})", ("P",)),
    "arg_own": (None, "take_own({e})", ("P",)),
    "arg_opt": (None, "take_opt({e})", ("P", "optP")),
    "arg_str": (None, "take_str({e})", ("str",)),
    "arg_own_str": (None, "take_own_str({e})", ("str",)),
    "arg_varargs": (None, "take_args({e})", ("P",)),
    "arg_proto": (None, "take_proto({e})", ("P",)),
    "arg_open_t": (None, "take_t({e})", ("P", "optP", "str", "list")),
    "arg_list": (None, "take_list({e})", ("list",)),
    "arg_dict": (None, "take_dict({e})", ("dict",)),
}

SINK_NEEDS = {
    "ret_any": ("any",),
    "ret_union": ("S",),
    "ret_callable": ("callable",),
    "arg_proto": ("protocol", "HasV"),
}

# A sink that is a statement, not a return, at the generator position: the
# body opens with a `yield` of its own so it is a frame. (The field and
# member-init sinks predate this and compile a plain function there.)
STATEMENT_IN_FRAME = tuple(k for k in SINKS if k.startswith("arg_"))


def _callee(sink):
    """The callee an argument sink calls, as module-level text, or ''."""
    stmt = SINKS[sink][1]
    if not stmt.startswith("take_"):
        return ""
    name = stmt.split("(")[0]
    key = next(k for k in CALLEES if k.split("[")[0] == name)
    params, body = CALLEES[key]
    # `take_own` consumes its parameter, so the callee carries no warning.
    keep = "\n\nkeep: list[P] = []\n" if "keep." in body else ""
    return keep + "\n\ndef %s(%s) -> None:\n    %s\n" % (key, params, body)


def _prelude(src, sink):
    """PRELUDE plus what the cell's source and sink need."""
    needs = SOURCE_NEEDS.get(src, ()) + SINK_NEEDS.get(sink, ())
    text = PRELUDE
    imports = [EXTRA_IMPORTS[n] for n in needs if n in EXTRA_IMPORTS]
    if imports:
        anchor = "from tpy import Own, readonly\n"
        text = text.replace(anchor, anchor + "".join(i + "\n"
                                                     for i in imports))
    text += "".join(PIECES[n] for n in needs if n in PIECES)
    return text + _callee(sink)

POSITIONS = ("function", "method", "constructor", "generator", "async")

# Every field the holder and the cell's own class declare, with the
# initializer that sets it when the cell does not.
FIELDS = (("p", "P", "P(0)"), ("o", "Optional[P]", "None"),
          ("f", "P", "P(0)"), ("s", "str", '""'), ("u", "str | int", "0"),
          ("os", "Optional[str]", "None"), ("b", "bytes", 'b""'))

_DECLS = ["    %s: %s" % (n, t) for n, t, _ in FIELDS]


def _inits(skip=None):
    return ["self.%s = %s" % (n, v) for n, _, v in FIELDS if n != skip]


HOLDER = "\n".join(["", "class H:"] + _DECLS + [
    "", "    def __init__(self) -> None:"]
    + ["        " + ln for ln in _inits()]) + "\n"


def _indent(lines, n):
    return ["    " * n + ln if ln else "" for ln in lines]


def cell_program(src, sink, pos):
    """The program for one cell, or None where the position cannot spell
    it or the sink does not take the source's type."""
    params, setup, expr, needs_self, family = SOURCES[src]
    ret_t, stmt, takes = SINKS[sink]
    if family not in takes:
        return None
    if src in STUB_DEFAULT_SOURCES and pos != "function":
        return None
    prelude = _prelude(src, sink)
    if src in FRAME_SOURCES:
        if pos == "async":
            suspend = "await tick()"
            prelude = prelude + TICK
        elif pos == "generator" and ret_t is not None:
            suspend = "yield " + stmt[len("return "):].format(
                e=expr.format(recv="h"))
        else:
            return None
        setup = [suspend if ln == "{suspend}" else ln for ln in setup]
    member_init = sink.startswith("member_init")
    if needs_self and pos not in ("method",):
        return None
    if member_init and pos != "constructor":
        return None
    if pos == "constructor" and ret_t is not None:
        return None
    in_class = pos in ("method", "constructor")
    recv = "self" if in_class else "h"
    expr = expr.format(recv=recv)
    body = [ln.replace("{recv}", recv) for ln in setup]
    frame_lead = pos == "generator" and sink in STATEMENT_IN_FRAME
    if frame_lead:
        body.insert(0, "yield 0")
    if pos == "generator" and ret_t is not None:
        line = "yield " + stmt[len("return "):].format(e=expr)
    else:
        line = stmt.format(e=expr, recv=recv)
    body.append(line)
    if src == "live_local" and ret_t is None:
        body.append("print(x.v)")
    plist = [p for p in (params,) if p]
    if not in_class:
        plist.insert(0, "h: H")
    if pos == "constructor":
        sig_params = ", ".join(["self"] + plist)
        head = "def __init__(%s) -> None:" % sig_params
        if not member_init:
            # A body write after a statement, not a member-init.
            body = ['print("ctor")'] + _inits() + body
        else:
            target = stmt.split("=")[0].strip()[len("self."):]
            body = body + _inits(skip=target)
        cls = ["", "class C:"] + _DECLS + [""] + _indent(
            [head] + _indent(body, 1), 1)
        return prelude + HOLDER + "\n".join(cls) + "\n"
    if frame_lead:
        rt = "Iterator[int]"
    elif ret_t is None:
        rt = "None"
    elif pos == "generator":
        rt = "Iterator[%s]" % ret_t
    else:
        rt = ret_t
    if pos == "method":
        selft = "self: Own[Self]" if src == "self_consuming" else "self"
        head = "def m(%s) -> %s:" % (", ".join([selft] + plist), rt)
        cls = ["", "class C(P):"] + _DECLS + [
            "", "    def __init__(self) -> None:",
            "        super().__init__(0)"] + [
            "        " + ln for ln in _inits()] + [""]
        cls += _indent([head] + _indent(body, 1), 1)
        return prelude + HOLDER + "\n".join(cls) + "\n"
    kw = "async def" if pos == "async" else "def"
    head = "%s f(%s) -> %s:" % (kw, ", ".join(plist), rt)
    stubs = []
    if src in STUB_DEFAULT_SOURCES:
        stub_params = [p.split("=")[0].strip() for p in plist]
        prelude = prelude.replace("from typing import Iterator, Optional, Self",
                                  "from typing import (Iterator, Optional, Self,\n"
                                  "                    overload)")
        stubs = ["@overload",
                 "def f(%s) -> %s: ..." % (", ".join(stub_params[:-1]), rt),
                 "", "", "@overload",
                 "def f(%s) -> %s: ..." % (", ".join(stub_params), rt),
                 "", ""]
    return prelude + HOLDER + "\n" + "\n".join(
        stubs + [head] + _indent(body, 1)) + "\n"


_ERR = re.compile(r"error: (.*)")
_WARN = re.compile(r"warning: (.*)")
_REASON = re.compile(r"\(([a-z_.:0-9]+(?:\.[a-z_0-9:.]+)*)\)\s*$")
# What every lowering (THIR) reject says; any other error is sema's.
_LOWERING_REJECT = "not yet supported by C++ code generation"


def _cell_code(cpp):
    """The dump without its source-comment lines: the prelude is the same
    program on both sides, so a line that differs is the cell's."""
    return "\n".join(ln.rstrip() for ln in cpp.splitlines()
                     if not ln.strip().startswith("//"))


def _env():
    # Each tree runs in its own environment, never the caller's.
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def classify(tree, path):
    p = subprocess.run(["uv", "run", "--project", tree, "tpyc",
                        "--dump-code", path], capture_output=True, text=True,
                       cwd=os.path.dirname(path), env=_env())
    text = p.stdout + p.stderr
    warns = sorted({m.group(1) for m in _WARN.finditer(text)})
    errs = [m.group(1) for m in _ERR.finditer(text)]
    if p.returncode != 0 and not errs:
        # No diagnostic at all: the compiler crashed or was never reached.
        # A crash is compared like a verdict; an unreached compiler (the
        # program file missing) is never one, or two broken trees would
        # compare equal.
        if not os.path.exists(path):
            raise SystemExit(f"{tree}: {path}: program missing:\n"
                             + text.strip()[-600:])
        return {"verdict": "crash", "warnings": warns,
                "reason": (text.strip().splitlines() or ["?"])[-1][:160]}
    if errs:
        err = errs[0]
        m = _REASON.search(err) if isinstance(err, str) else None
        return {"verdict": "reject",
                "reason": m.group(1) if m else str(err)[:160],
                "sema": _LOWERING_REJECT not in err, "warnings": warns}
    return {"verdict": "compiles", "warnings": warns,
            "cpp": _cell_code(p.stdout)}


def build(tree, path, tag):
    """Whether the cell's program builds to a binary under `tree`, and the
    first error line when it does not."""
    d = os.path.dirname(path)
    out = os.path.join(d, "bin_" + tag)
    p = subprocess.run(["uv", "run", "--project", tree, "tpyc", "-b", "-j",
                        "1", path, "-o", out], capture_output=True, text=True,
                       cwd=d, env=_env())
    subprocess.run(["rm", "-rf", out, os.path.join(d, "__tpyc__")])
    if p.returncode == 0:
        return {"builds": True}
    text = p.stdout + p.stderr
    first = next((ln.strip() for ln in text.splitlines()
                  if "error" in ln), text.strip()[-200:])
    return {"builds": False, "error": first[:240]}


def delta(a, b):
    """The cell's own lines: what the current side (`a`) renders that the
    base (`b`) does not, and the reverse."""
    if a.get("cpp") is None or b.get("cpp") is None:
        return [], []
    al, bl = a["cpp"].splitlines(), b["cpp"].splitlines()
    return ([ln.strip() for ln in bl if ln not in al],
            [ln.strip() for ln in al if ln not in bl])


def observed(a, b):
    """What a decided cell looks like on the current tree."""
    minus, plus = delta(a, b)
    out = {"verdict": a["verdict"], "warnings": a["warnings"],
           "minus": minus, "plus": plus}
    if a["verdict"] == "reject":
        out["reason"] = a["reason"]
    else:
        # The whole render, so a decided cell whose base side has no C++ to
        # diff against (a widening) still pins its own.
        out["cpp_sha256"] = hashlib.sha256(a["cpp"].encode()).hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(REPO, "build",
                                                  "convert_matrix"))
    ap.add_argument("-k", default=None)
    ap.add_argument("--known", default=None)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--pin-known", action="store_true")
    ap.add_argument("--no-prune", action="store_true",
                    help="compile every position of a pair sema rejects")
    ap.add_argument("--json", default=None,
                    help="write every cell's verdict on both sides here")
    args = ap.parse_args()
    base = os.path.abspath(args.base)
    known = json.load(open(args.known)) if args.known else {}
    # Cells compile with cwd at the cell, so the output root must be absolute.
    args.out = os.path.abspath(args.out)
    os.makedirs(args.out, exist_ok=True)
    # {(source, sink): [(cell id, program path)] in POSITIONS order}
    pairs = {}
    for src in SOURCES:
        for sink in SINKS:
            for pos in POSITIONS:
                cid = "%s__%s__%s" % (src, sink, pos)
                if args.k and args.k not in cid:
                    continue
                prog = cell_program(src, sink, pos)
                if prog is None:
                    continue
                d = os.path.join(args.out, cid)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "main.py"), "w") as fh:
                    fh.write(prog)
                pairs.setdefault((src, sink), []).append(
                    (cid, os.path.join(d, "main.py")))
    res = {}

    def run(cells):
        jobs = [(cid, tree, path) for cid, path in cells
                for tree in (REPO, base)]
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
            futs = {ex.submit(classify, tree, path): (cid, tree)
                    for cid, tree, path in jobs}
            for f in concurrent.futures.as_completed(futs):
                res[futs[f]] = f.result()

    # A pair sema rejects is a type question, not a position one: its first
    # position answers for the pair when both trees give the same sema error
    # there, and the pair is recorded once, as `<src>__<sink>__sema`.
    probes = [cells[0] for cells in pairs.values()]
    run(probes if not args.no_prune else
        [c for cells in pairs.values() for c in cells])
    cells, pruned = [], 0
    for (src, sink), pcells in pairs.items():
        cid, path = pcells[0]
        a, b = res[(cid, REPO)], res[(cid, base)]
        if (not args.no_prune and a == b and a["verdict"] == "reject"
                and a.get("sema")):
            sid = "%s__%s__sema" % (src, sink)
            res[(sid, REPO)], res[(sid, base)] = a, b
            cells.append((sid, path))
            pruned += len(pcells) - 1
            continue
        cells += pcells
    if not args.no_prune:
        run([c for c in cells if (c[0], REPO) not in res])
    for cid, path in cells:
        tdir = os.path.join(os.path.dirname(path), "__tpyc__")
        if os.path.isdir(tdir):
            subprocess.run(["rm", "-rf", tdir])
    ids = {cid for cid, _ in cells}
    missing = sorted(k for k in known if k not in ids and not args.k)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump({cid: {"current": res[(cid, REPO)],
                             "base": res[(cid, base)]}
                       for cid, _p in cells}, fh, indent=1, sort_keys=True)
            fh.write("\n")
    same, diff, decided, moved, stale = 0, [], [], [], []
    paths = dict(cells)
    for cid, _path in cells:
        a, b = res[(cid, REPO)], res[(cid, base)]
        entry = known.get(cid)
        if a == b:
            same += 1
            if entry is not None:
                stale.append(cid)
            continue
        if entry is None:
            diff.append((cid, a, b, None))
            continue
        if args.pin_known:
            entry["expect"] = observed(a, b)
        if entry.get("expect") == observed(a, b):
            decided.append((cid, a, b, entry))
        else:
            moved.append((cid, a, b, entry))
    builds = {}
    if args.build:
        targets = [cid for cid, *_ in decided + diff + moved
                   if res[(cid, REPO)]["verdict"] == "compiles"
                   or res[(cid, base)]["verdict"] == "compiles"]
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
            futs = {ex.submit(build, tree, paths[cid], tag): (cid, tree)
                    for cid in targets
                    for tree, tag in ((REPO, "current"), (base, "base"))}
            for f in concurrent.futures.as_completed(futs):
                builds[futs[f]] = f.result()
    regressed = [cid for (cid, tree), r in builds.items()
                 if tree == REPO and not r["builds"]
                 and builds[(cid, base)]["builds"]]
    summary = {}
    for cid, _p in cells:
        a = res[(cid, REPO)]
        summary[a["verdict"]] = summary.get(a["verdict"], 0) + 1
    print("cells %d (%s; %d positions pruned behind a sema pair); identical "
          "%d, decided %d, UNKNOWN %d, decided-but-MOVED %d, build "
          "regressions %d" % (
              len(cells), ", ".join("%s %d" % kv
                                    for kv in sorted(summary.items())),
              pruned, same, len(decided), len(diff), len(moved),
              len(regressed)))
    for cid in stale:
        print("note: known entry %s no longer differs" % cid)
    for cid in missing:
        print("note: known entry %s names no cell" % cid)
    for cid, a, b, entry in decided + moved + diff:
        tag = ("" if entry is None else
               "  [decided: %s -- %s]" % (entry.get("decided", "?"),
                                          entry.get("reason", ""))
               if (cid, a, b, entry) in decided else
               "  [DECIDED CELL MOVED: %s]" % entry.get("decided", "?"))
        print("\n== %s%s" % (cid, tag))
        for side, r, tree in (("current", a, REPO), ("base", b, base)):
            if r["verdict"] == "reject":
                print("  %-7s reject %s" % (side, r["reason"]))
            else:
                print("  %-7s compiles" % side)
            print("          warnings: %s" % "; ".join(r["warnings"]))
            br = builds.get((cid, tree))
            if br is not None:
                print("          build: %s" % (
                    "ok" if br["builds"] else "FAILS " + br["error"]))
        minus, plus = delta(a, b)
        for ln in minus:
            print("  - " + ln)
        for ln in plus:
            print("  + " + ln)
    if args.pin_known and args.known:
        with open(args.known, "w") as fh:
            json.dump(known, fh, indent=1, sort_keys=True)
            fh.write("\n")
    return 1 if diff or moved or regressed else 0


if __name__ == "__main__":
    sys.exit(main())
