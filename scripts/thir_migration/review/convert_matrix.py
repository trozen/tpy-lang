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
        [--known KNOWN.json] [--build] [--pin-known]

Each side compiles in its own checkout through `uv run --project <tree>`.
Programs and dumps land under `--out` (default `build/convert_matrix`, not
committed). A cell a position cannot spell (a receiver in a free function,
a member-init outside a constructor) or whose source type the sink does not
take is skipped.

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
}

# Each sink: (declared result or None for a field, the statement, the
# source families it takes).
SINKS = {
    "ret_ref": ("P", "return {e}", ("P",)),
    "ret_opt": ("Optional[P]", "return {e}", ("P",)),
    "ret_own": ("Own[P]", "return {e}", ("P",)),
    "ret_tuple": ("tuple[P, int]", "return ({e}, 1)", ("P",)),
    "ret_str": ("str", "return {e}", ("str",)),
    "ret_opt_str": ("Optional[str]", "return {e}", ("str",)),
    "field": (None, "{recv}.p = {e}", ("P",)),
    "field_opt": (None, "{recv}.o = {e}", ("P",)),
    "field_str": (None, "{recv}.s = {e}", ("str",)),
    "field_str_int": (None, "{recv}.u = {e}", ("str",)),
    "field_opt_str": (None, "{recv}.os = {e}", ("str",)),
    "field_bytes": (None, "{recv}.b = {e}", ("bytes",)),
    "member_init": (None, "self.p = {e}", ("P",)),
    "member_init_opt": (None, "self.o = {e}", ("P",)),
    "member_init_str": (None, "self.s = {e}", ("str",)),
    "member_init_str_int": (None, "self.u = {e}", ("str",)),
    "member_init_opt_str": (None, "self.os = {e}", ("str",)),
    "member_init_bytes": (None, "self.b = {e}", ("bytes",)),
}

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
    body = list(setup)
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
        return PRELUDE + HOLDER + "\n".join(cls) + "\n"
    if ret_t is None:
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
        return PRELUDE + HOLDER + "\n".join(cls) + "\n"
    kw = "async def" if pos == "async" else "def"
    head = "%s f(%s) -> %s:" % (kw, ", ".join(plist), rt)
    return PRELUDE + HOLDER + "\n" + "\n".join([head] + _indent(body, 1)) \
        + "\n"


_ERR = re.compile(r"error: (.*)")
_WARN = re.compile(r"warning: (.*)")
_REASON = re.compile(r"\(([a-z_.:0-9]+(?:\.[a-z_0-9:.]+)*)\)\s*$")


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
                "warnings": warns}
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
    args = ap.parse_args()
    base = os.path.abspath(args.base)
    known = json.load(open(args.known)) if args.known else {}
    # Cells compile with cwd at the cell, so the output root must be absolute.
    args.out = os.path.abspath(args.out)
    os.makedirs(args.out, exist_ok=True)
    cells = []
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
                cells.append((cid, os.path.join(d, "main.py")))
    ids = {cid for cid, _ in cells}
    missing = sorted(k for k in known if k not in ids and not args.k)
    jobs = [(cid, tree, path) for cid, path in cells
            for tree in (REPO, base)]
    res = {}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        futs = {ex.submit(classify, tree, path): (cid, tree)
                for cid, tree, path in jobs}
        for f in concurrent.futures.as_completed(futs):
            res[futs[f]] = f.result()
    for _cid, path in cells:
        tdir = os.path.join(os.path.dirname(path), "__tpyc__")
        if os.path.isdir(tdir):
            subprocess.run(["rm", "-rf", tdir])
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
    print("cells %d (%s); identical %d, decided %d, UNKNOWN %d, "
          "decided-but-MOVED %d, build regressions %d" % (
              len(cells), ", ".join("%s %d" % kv
                                    for kv in sorted(summary.items())),
              same, len(decided), len(diff), len(moved), len(regressed)))
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
