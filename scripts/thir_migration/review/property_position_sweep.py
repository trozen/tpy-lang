#!/usr/bin/env python3
"""The property x position VERDICT RATCHET.

A `@property` read is one node kind (a method call) from sema on, so every
position in the language answers for it the same way it answers for the
spelled-method twin -- except where a stated rule says otherwise. That is a
MATRIX claim, and nothing in the corpus states it: the cases pin the cells
someone thought to write, and three review rounds each found a cell nobody
had. This script generates the matrix, compiles every cell, and writes the
verdict table next to itself. The committed table is the instrument: a future
change that moves any cell shows up as a diff in `property_position_sweep.
expected.json` instead of as a review discovery.

NOT an arm pin. The house rule is that a THIR lowering arm is pinned by a
CASE under `tests/cases/`, and that still holds -- every rule this table
records has its own case. This is the same kind of thing as the other
instruments in this directory (`container_gates.py`, `inventory_sites.py`): a
measurement over a generated corpus, committed so the number cannot drift
unnoticed.

The matrix is (position x getter flavour x receiver kind) for the accessor
spelling, and the same for the spelled-METHOD twin, so a cell records BOTH
verdicts -- "the getter rejects here" is only interesting beside what the
method does. Receiver kinds are a NAMED binding, a TEMPORARY (`mk().p`), and
`self` inside the owning record -- the last on its own axis, because a read
off `self` has to live in a method body and so needs a different program
shape (cells prefixed `self_`).

WHAT A CELL RECORDS, AND WHAT IT DOES NOT. Each cell is a pair: the
compile VERDICT (`ok`, or the reject/error tag the compiler stopped at) and
the FIRST WARNING emitted BELOW the cell's own marker line, or "" for none
(the shared prelude warns about its owned-str getter in every program, which
is why the marker exists). That is the whole claim. In particular the table does NOT certify behaviour: the cells read
the bound value and print, so a cell that COPIES where CPython ALIASES is
`ok` here and would stay `ok` if the copy became a silent miscompile. Copy-
vs-alias is the corpus cases' job (they mutate after the boundary and
observe); this table's job is that no cell's verdict or warning moves
without someone saying so.

One label in the table is not the sink you would guess:
`module_level__*__prop__temp` reports `local_decl.lends_from_temporary`,
because a module-level binding WITHOUT an annotation shares the local decl
lowering, where an annotated module global takes `global_slot_write`. Both
reject and both are right; the split is in the module-level decl routing and
predates the rule the tag names.

  python scripts/thir_migration/review/property_position_sweep.py          # check
  python scripts/thir_migration/review/property_position_sweep.py --update # rewrite
  python scripts/thir_migration/review/property_position_sweep.py --exec   # + build/run

`--exec` is the reviewer's half: it builds and runs each admitted cell and
reports the ones whose C++ does not compile. It needs a toolchain, so it is
never part of the committed check.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from tpyc.compiler import Compiler  # noqa: E402 -- needs REPO on sys.path
from tpyc.diagnostics import DiagnosticLevel  # noqa: E402

EXPECTED = Path(__file__).with_suffix(".expected.json")

# The cell bodies are built by string substitution, so a quote or a brace
# spelled inline would either close the surrounding literal or be eaten by
# `str.format`-style brace handling in the template. Naming them keeps every
# row readable as one line.
Q = chr(34)
LB = chr(123)
RB = chr(125)

# The imports every cell shares. A row that needs more names declares them in
# `IMPORTS` below, because an import line has to precede the defs.
PRELUDE_IMPORTS = """from typing import Iterator, Optional
from tpy import Own, ReturnException, StrView, error_return, int32
"""

# The receiver type every cell shares. `B` carries one field per getter
# flavour, a getter over each, and the spelled-method twin of each.
PRELUDE = """

class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        pass


class B:
    _n: int32
    _s: str
    _items: list[int32]
    _rec: R
    _opt: Optional[R]
    _guard: Guard

    def __init__(self) -> None:
        self._n = 3
        self._s = "abc"
        self._items = [1, 2, 3]
        self._rec = R(9)
        self._opt = R(4)
        self._guard = Guard()

    @property
    def n(self) -> int32:
        return self._n

    @property
    def name(self) -> StrView:
        return self._s

    @property
    def text(self) -> str:
        return self._s

    @property
    def items(self) -> list[int32]:
        return self._items

    @property
    def rec(self) -> R:
        return self._rec

    @property
    def opt(self) -> Optional[R]:
        return self._opt

    @property
    def own(self) -> Own[list[int32]]:
        return [7, 8]

    @property
    def guard(self) -> Guard:
        return self._guard

    def n_m(self) -> int32:
        return self._n

    def name_m(self) -> StrView:
        return self._s

    def text_m(self) -> str:
        return self._s

    def items_m(self) -> list[int32]:
        return self._items

    def rec_m(self) -> R:
        return self._rec

    def opt_m(self) -> Optional[R]:
        return self._opt

    def own_m(self) -> Own[list[int32]]:
        return [7, 8]

    def guard_m(self) -> Guard:
        return self._guard
@SELF_PROBE@

def mk() -> Own[B]:
    return B()


def sink_i(v: int32) -> int32:
    return v + 1


def sink_s(v: StrView) -> int32:
    return len(v)


def sink_l(v: list[int32]) -> int32:
    return len(v)


def sink_r(v: R) -> int32:
    return v.x


class Taker:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    def sink_i(self, v: int32) -> int32:
        return v + 1

    def sink_s(self, v: StrView) -> int32:
        return len(v)

    def sink_l(self, v: list[int32]) -> int32:
        return len(v)

    def sink_r(self, v: R) -> int32:
        return v.x


"""

# How a cell OBSERVES the bound value, per flavour.
USE: dict[str, str] = {
    "n": "print(v)", "name": "print(v)", "text": "print(v)",
    "items": "print(len(v))", "own": "print(len(v))",
    "rec": "print(v.x)", "opt": "print(v is None)",
    "guard": "print(v.n)"}
SINK: dict[str, str] = {
    "n": "sink_i", "name": "sink_s", "text": "sink_s",
    "items": "sink_l", "own": "sink_l", "rec": "sink_r",
    "opt": "sink_i", "guard": "sink_i"}
RT: dict[str, str] = {
    "n": "int32", "name": "StrView", "text": "str",
    "items": "list[int32]", "own": "Own[list[int32]]",
    "rec": "R", "opt": "Optional[R]", "guard": "Guard"}

ALL: list[str] = ["n", "name", "text", "items", "own", "rec", "opt"]
VAL: list[str] = ["n"]
VIEW: list[str] = ["name", "text"]
CONT: list[str] = ["items", "own"]
SCAL: list[str] = ["n", "name", "text"]

# (position, flavours, extra top-level defs, body). `None` body = the cell is
# a MODULE-LEVEL statement, which has its own variable model. A row needing
# an import beyond the shared prelude spells it in `IMPORTS`.
IMPORTS: dict[str, str] = {"async_body": "import asyncio\n"}

POS: list[tuple[str, list[str], list[str], list[str] | None]] = [
    ("decl", ALL, [], ["    v = @E@", "    @USE@"]),
    ("call_arg", ["n", "name", "text", "items", "own", "rec"], [],
     ["    print(@SINK@(@E@))"]),
    ("print", ["n", "name", "text", "items", "own"], [], ["    print(@E@)"]),
    ("ret", ALL, ["def take(b: B) -> @RT@:", "    return @E@", ""],
     ["    v = take(b)", "    @USE@"]),
    ("fstring", SCAL, [],
     ["    print(f" + Q + "v=" + LB + "@E@" + RB + Q + ")"]),
    ("tuple_elem", SCAL, [], ["    t = (@E@, 1)", "    print(t[1])"]),
    ("list_elem", ["n", "name", "text", "rec"], [],
     ["    xs = [@E@]", "    print(len(xs))"]),
    ("dict_val", SCAL, [],
     ["    d = " + LB + Q + "k" + Q + ": @E@" + RB, "    print(len(d))"]),
    ("set_elem", SCAL, [], ["    s = " + LB + "@E@" + RB, "    print(len(s))"]),
    ("subscript_index", VAL, [],
     ["    xs = [1, 2, 3, 4, 5, 6]", "    print(xs[@E@])"]),
    ("arith", VAL, [], ["    print(@E@ + 1)"]),
    ("arith_str", VIEW, [], ["    print(@E@ + " + Q + "z" + Q + ")"]),
    ("compare", VAL, [], ["    print(@E@ > 1)"]),
    ("compare_str", VIEW, [], ["    print(@E@ == " + Q + "abc" + Q + ")"]),
    ("truthy", ALL, [],
     ["    if @E@:", "        print(1)", "    else:", "        print(0)"]),
    ("foreach", CONT, [], ["    for e in @E@:", "        print(e)"]),
    ("match_subject", VAL, [],
     ["    match @E@:", "        case 3:", "            print(1)",
      "        case _:", "            print(0)"]),
    ("augassign", VAL, [], ["    v = 0", "    v += @E@", "    print(v)"]),
    ("augassign_list", ["items"], [],
     ["    acc: list[int32] = [0]", "    acc += @E@", "    print(len(acc))"]),
    ("membership", CONT, [], ["    print(1 in @E@)"]),
    ("membership_needle", VIEW, [],
     ["    d = " + LB + Q + "abc" + Q + ": 1" + RB, "    print(@E@ in d)"]),
    ("dict_key_read", VIEW, [],
     ["    d = " + LB + Q + "abc" + Q + ": 1" + RB, "    print(d[@E@])"]),
    ("dict_key_write", VIEW, [],
     ["    d = " + LB + Q + "abc" + Q + ": 1" + RB, "    d[@E@] = 2",
      "    print(len(d))"]),
    # The annotation is load-bearing: an un-annotated `[0, 0]` stays a
    # pending int-literal list, and the element write never reaches the sink
    # this row exists to exercise.
    ("setitem_value", VAL, [],
     ["    xs: list[int32] = [0, 0]", "    xs[0] = @E@", "    print(xs[0])"]),
    ("global_write", VAL, ["G: int32 = 0", ""],
     ["    global G", "    G = @E@", "    print(G)"]),
    ("method_recv_cont", CONT, [], ["    print(@E@.count(1))"]),
    ("method_recv_view", VIEW, [], ["    print(@E@.upper())"]),
    ("field_read", ["rec", "opt"], [], ["    print(@E@.x)"]),
    ("len", ["items", "own", "name", "text"], [], ["    print(len(@E@))"]),
    ("str_coerce", SCAL, [], ["    print(str(@E@))"]),
    ("sorted_arg", CONT, [], ["    print(len(sorted(@E@)))"]),
    ("reversed_arg", CONT, [],
     ["    for e in reversed(@E@):", "        print(e)"]),
    ("assert_test", VAL, [],
     ["    assert @E@ > 0, " + Q + "m" + Q, "    print(1)"]),
    ("raise_operand", VIEW, [],
     ["    try:", "        raise ValueError(@E@)", "    except ValueError:",
      "        print(1)"]),
    ("while_cond", VAL, [],
     ["    k = 0", "    while k < @E@:", "        k += 1", "    print(k)"]),
    ("comp_iter", CONT, [],
     ["    ys = [e * 2 for e in @E@]", "    print(len(ys))"]),
    ("comp_elem", SCAL + ["rec"], [],
     ["    ys = [@E@ for _ in range(2)]", "    print(len(ys))"]),
    ("genexp_iter", CONT, [], ["    print(sum(e for e in @E@))"]),
    ("range_arg", VAL, [], ["    for i in range(@E@):", "        print(i)"]),
    ("kwarg", VAL, ["def kw(v: int32) -> int32:", "    return v", ""],
     ["    print(kw(v=@E@))"]),
    ("walrus", VAL, [], ["    if (v := @E@) > 0:", "        print(v)"]),
    ("ternary", SCAL, [], ["    v = @E@ if True else @E@", "    @USE@"]),
    ("try_finally", SCAL, [],
     ["    try:", "        print(@E@)", "    finally:", "        print(0)"]),
    # The RECORD flavour is in this row deliberately: at the PARAMETER
    # receiver this axis uses, both spellings reject together, and the row
    # exists so that pairing is visible if either side moves. The SELF
    # receiver (a record yielding its own getter) is a separate axis; see
    # the self-receiver rows below.
    ("yield_val", SCAL + CONT + ["rec"],
     ["def gen(b: B) -> Iterator[@RT@]:", "    yield @E@", ""],
     ["    for v in gen(b):", "        @USE@"]),
    ("ctor_body", SCAL,
     ["class C:", "    v: @RT@", "    def __init__(self, b: B) -> None:",
      "        self.v = @E@", ""],
     ["    c = C(b)", "    print(c.v)"]),
    ("closure", SCAL, [],
     ["    def inner() -> @RT@:", "        return @E@", "    v = inner()",
      "    @USE@"]),
    ("match_arm", SCAL, [],
     ["    k = 1", "    match k:", "        case 1:", "            print(@E@)",
      "        case _:", "            print(0)"]),
    ("module_level", SCAL, [], None),
    # A USER-record METHOD's argument. Its own body decides whether the
    # argument may hold a borrow of dying storage, so this row and the free
    # `call_arg` row above can legitimately differ.
    ("meth_arg", ["n", "name", "text", "items", "own", "rec"], [],
     ["    t = Taker()", "    print(t.@SINK@(@E@))"]),
    # ... and a user CONSTRUCTOR's argument, the same question at the ctor.
    ("ctor_arg", ["n", "name", "text", "items", "rec"],
     ["class K:", "    v: @RT@", "    def __init__(self, v: @RT@) -> None:",
      "        self.v = v", ""],
     ["    k = K(@E@)", "    v = k.v", "    @USE@"]),
    # The with-MANAGER position: the region binds the manager by reference
    # when it is an lvalue, and a borrow-returning getter is one.
    ("with_mgr", ["guard"], [], ["    with @E@ as q:", "        print(q)"]),
    # A read inside a context-manager BODY -- the `with` block as a position,
    # not as the manager.
    ("with_body", SCAL, [],
     ["    g = Guard()", "    with g as q:", "        print(q, @E@)"]),
    # The eagerly-copying builtins, the parity-critical list: each takes the
    # argument, reads it during the call, and hands back owned storage.
    ("list_arg", CONT, [],
     ["    ys = list(@E@)", "    print(len(ys))"]),
    ("sum_arg", CONT, [], ["    print(sum(@E@))"]),
    ("any_arg", CONT, [], ["    print(any(@E@))"]),
    # A view element into an OWNING `list[str]` slot: the targeted spelling,
    # where the slot decides the copy rather than the element source.
    ("list_elem_targeted", VIEW, [],
     ["    xs: list[str] = [@E@]", "    print(len(xs))"]),
    # A getter as the augmented assignment's RECEIVER: the lvalue is reached
    # THROUGH the read, so the render would spell it on both sides.
    ("augassign_recv", ["rec"], [],
     ["    @E@.x += 1", "    print(b._rec.x)"]),
    # ... and its INDEX sibling: the render spells the whole target twice,
    # so a getter in the index runs twice exactly as one in the receiver
    # chain does.
    # Four elements because the `n` flavour reads 3; a shorter list makes the
    # METHOD twin -- the half that still RUNS here -- panic instead of
    # rendering, and the row would say nothing.
    ("augassign_index", VAL, [],
     ["    xs: list[int32] = [10, 20, 30, 40]", "    xs[@E@] += 1",
      "    print(xs[0], xs[3])"]),
    # An `@error_return` body -- its own return convention (`std::expected`)
    # around the same read.
    ("error_return_body", SCAL,
     ["class NotFound(Exception, ReturnException):", "    pass", "",
      "", "@error_return(NotFound)", "def er(b: B) -> @RT@:",
      "    if b._n < 0:", "        raise NotFound", "    return @E@", ""],
     ["    try:", "        v = er(b)", "    except NotFound:",
      "        print(0)", "    else:", "        @USE@"]),
    # ... and an `async def` body, the resumable frame's half of the same.
    ("async_body", SCAL,
     ["async def ab(b: B) -> @RT@:", "    return @E@", ""],
     ["    v = asyncio.run(ab(b))", "    @USE@"]),
]

# The SELF-receiver axis: the same question for a read off `self` inside the
# owning record, which has to live in a method body. `@E@` is the only
# substitution these rows need; the driver supplies the enclosing method
# signature per position.
SELF_POS: list[tuple[str, list[str], list[str]]] = [
    ("decl", ALL, ["        v = @E@", "        @USE@"]),
    ("ret", ALL, ["        return @E@"]),
    ("yield", ALL, ["        yield @E@"]),
    ("call_arg", ALL, ["        print(@SINK@(@E@))"]),
    ("foreach", CONT, ["        for e in @E@:", "            print(e)"]),
    ("print", ["n", "name", "text", "items"], ["        print(@E@)"]),
    ("truthy", ALL, ["        if @E@:", "            print(1)"]),
    ("comp_iter", CONT,
     ["        xs = [e for e in @E@]", "        print(len(xs))"]),
    ("method_recv", CONT, ["        print(@E@.count(1))"]),
    ("tuple_elem", SCAL, ["        t = (@E@, 1)", "        print(t[1])"]),
]

# The LOOP-LOCAL receiver axis: the receiver is built INSIDE a loop, so what
# a borrow-returning getter lends is destroyed at the end of the iteration.
# Rows whose sink BINDS the read read it back AFTER the loop, because that is
# the only place the escape is observable; the rest consume it in the loop and
# are here to say that the escape rule does not reach them. `@E0@` is the same
# read off the module-level receiver, for the initial binding that types the
# local (and for a fallback return).
# (position, flavours, extra defs, in-loop body, after-loop lines)
LOOP_POS: list[tuple[str, list[str], list[str], list[str], list[str]]] = [
    ("decl", ALL, [], ["        v = @E@"], ["    @USE@"]),
    ("ret", ALL,
     ["def take() -> @RT@:", "    for _i in range(2):", "        b = B()",
      "        return @E@", "    return @E0@", ""],
     [], ["    v = take()", "    @USE@"]),
    ("call_arg", ["n", "name", "text", "items", "own", "rec"], [],
     ["        print(@SINK@(@E@))"], []),
    ("foreach", CONT, [], ["        for e in @E@:", "            print(e)"],
     []),
    ("tuple_elem", SCAL, [], ["        t = (@E@, 1)", "        print(t[1])"],
     []),
    ("with_mgr", ["guard"], [], ["        with @E@ as q:",
                                 "            print(q)"], []),
]


def _subst(lines: list[str], expr: str, kind: str,
           expr0: str = "") -> list[str]:
    out = []
    for ln in lines:
        ln = ln.replace("@E0@", expr0)
        ln = ln.replace("@E@", expr)
        ln = ln.replace("@USE@", USE[kind])
        ln = ln.replace("@SINK@", SINK.get(kind, "sink_i"))
        ln = ln.replace("@RT@", RT[kind])
        out.append(ln)
    return out


# The last line of every cell's shared head. `_verdict` finds it to tell the
# PRELUDE's own diagnostics from the cell's: the `text` getter warns about
# copying a str field in every one of the 700-odd programs, and a warning
# column reporting that would record nothing about any cell.
CELL_MARK = "# --- cell ---"


def _prelude(pos: str, probe: list[str] | None = None) -> list[str]:
    """The shared program head, with this row's imports and (for a SELF cell)
    the probe method spliced into the receiver record."""
    body = PRELUDE.replace(
        "@SELF_PROBE@", "\n".join(probe) if probe else "")
    return (PRELUDE_IMPORTS + IMPORTS.get(pos, "")
            + body).split("\n") + [CELL_MARK]


def generate(out_dir: Path) -> dict[str, Path]:
    """Write one program per cell; return {cell name: path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cells: dict[str, Path] = {}
    for pos, kinds, defs, body in POS:
        for kind in kinds:
            for spelling, suffix in (("prop", ""), ("meth", "_m()")):
                for recv_kind, recv in (("named", "b"), ("temp", "mk()")):
                    expr = recv + "." + kind + suffix
                    lines = _prelude(pos)
                    lines += _subst(defs, expr, kind)
                    if body is None:
                        lines += ["b = B()", "v = " + expr, USE[kind]]
                    else:
                        lines += ["def main() -> None:", "    b = B()"]
                        lines += _subst(body, expr, kind)
                        lines += ["", "main()"]
                    name = f"{pos}__{kind}__{spelling}__{recv_kind}"
                    path = out_dir / f"{name}.py"
                    path.write_text("\n".join(lines) + "\n")
                    cells[name] = path
    cells.update(generate_self(out_dir))
    cells.update(generate_loop_local(out_dir))
    return cells


def generate_loop_local(out_dir: Path) -> dict[str, Path]:
    """The LOOP-LOCAL receiver cells: the receiver is rebuilt on every
    iteration, so a read that lends its storage must not outlive the body."""
    cells: dict[str, Path] = {}
    for pos, kinds, defs, body, after in LOOP_POS:
        for kind in kinds:
            for spelling, suffix in (("prop", ""), ("meth", "_m()")):
                expr = "b." + kind + suffix
                expr0 = "b0." + kind + suffix
                lines = _prelude(pos)
                lines += ["b0 = B()", ""]
                lines += _subst(defs, expr, kind, expr0)
                lines += ["def main() -> None:", "    v = " + expr0]
                if body:
                    lines += ["    for _i in range(2):", "        b = B()"]
                    lines += _subst(body, expr, kind, expr0)
                lines += _subst(after, expr, kind, expr0)
                lines += ["", "main()"]
                name = f"loop_{pos}__{kind}__{spelling}__local"
                path = out_dir / f"{name}.py"
                path.write_text("\n".join(lines) + "\n")
                cells[name] = path
    return cells


def generate_self(out_dir: Path) -> dict[str, Path]:
    """The SELF-receiver cells: the read lives in a method of the record that
    declares the getter, so the probe method is spliced into `B` and `main`
    only calls it."""
    cells: dict[str, Path] = {}
    for pos, kinds, body in SELF_POS:
        for kind in kinds:
            for spelling, suffix in (("prop", ""), ("meth", "_m()")):
                expr = "self." + kind + suffix
                if pos == "ret":
                    sig = "    def probe(self) -> " + RT[kind] + ":"
                elif pos == "yield":
                    sig = "    def probe(self) -> Iterator[" + RT[kind] + "]:"
                else:
                    sig = "    def probe(self) -> None:"
                probe = ["", sig] + _subst(body, expr, kind)
                lines = _prelude(pos, probe)
                lines += ["def main() -> None:", "    b = B()"]
                if pos == "ret":
                    lines += ["    v = b.probe()", "    " + USE[kind]]
                elif pos == "yield":
                    lines += ["    for v in b.probe():",
                              "        " + USE[kind]]
                else:
                    lines += ["    b.probe()"]
                lines += ["", "main()"]
                name = f"self_{pos}__{kind}__{spelling}__self"
                path = out_dir / f"{name}.py"
                path.write_text("\n".join(lines) + "\n")
                cells[name] = path
    return cells


def _verdict(src: Path, build_dir: Path) -> list[str]:
    """`[verdict, warning]`: "ok" or the reject/error tag the compiler stopped
    at, beside the FIRST warning it emitted ("" for none).

    The warning is half the answer at this matrix's positions -- several
    cells compile only because a copy was made, and say so only in a
    warning, so a table recording the tag alone would call a lost or gained
    warning "no verdict moved". Only warnings BELOW `CELL_MARK` count: the
    shared prelude emits one of its own, which every cell would otherwise
    report instead of its own.
    """
    lib = REPO / "lib" / "tpy"
    warned = ""
    cell_line = _cell_start(src)
    try:
        compiler = Compiler(src, default_int="int32", lib_dirs=[lib])
        modules = compiler.compile()
        diags = list(compiler.diagnostics)
        for mod in modules:
            an = getattr(mod, "analyzer", None)
            if an is not None:
                diags += list(getattr(an, "diagnostics", []))
        warnings = [d for d in diags
                    if d.level == DiagnosticLevel.WARNING
                    and _diag_line(d) > cell_line]
        if warnings:
            warned = _tag(warnings[0].message)
        errors = [d for d in diags if d.level == DiagnosticLevel.ERROR]
        if errors:
            return [_tag(errors[0].message), warned]
        entry = next(m for m in modules if m.is_entry_point)
        for mod in modules:
            compiler.generate_code(mod, build_dir,
                                   entry_module_name=entry.name)
    except Exception as exc:  # noqa: BLE001 -- every failure IS a verdict
        return [_tag(str(exc)), warned]
    return ["ok", warned]


def _cell_start(src: Path) -> int:
    """The line `CELL_MARK` sits on; 0 if the program has no mark."""
    for i, line in enumerate(src.read_text().split("\n"), start=1):
        if line == CELL_MARK:
            return i
    return 0


def _diag_line(diag) -> int:
    """A diagnostic's line, or 0 when it carries no location."""
    loc = getattr(diag, "loc", None)
    return getattr(loc, "line", 0) or 0


def _tag(message: str) -> str:
    """The reject tag out of a diagnostic, or a short form of the message.

    The tag is what the table records: the prose around it moves with
    unrelated wording changes, the tag is the verdict.
    """
    if "(" in message and message.rstrip().endswith(")"):
        return message[message.rfind("(") + 1:-1]
    return message.strip().split("\n")[0][:80]


def run(out_dir: Path, cells: dict[str, Path]) -> dict[str, list[str]]:
    build = out_dir / "build"
    build.mkdir(exist_ok=True)
    table: dict[str, list[str]] = {}
    for i, (name, path) in enumerate(sorted(cells.items())):
        table[name] = _verdict(path, build / name)
        if os.environ.get("SWEEP_PROGRESS") and i % 25 == 0:
            print(f"  {i}/{len(cells)}", file=sys.stderr)
    return table


def run_exec(cells: dict[str, Path], table: dict[str, list[str]]) -> int:
    """Build and RUN every admitted cell; report the ones whose C++ does not
    compile or whose binary fails.

    The comp half says a cell has a render; only this says the render is
    well-formed C++ that runs. It needs a toolchain, which is why it is the
    reviewer's half and never part of the committed check.
    """
    bad = 0
    admitted = sorted(k for k, v in table.items() if v[0] == "ok")
    print(f"building and running {len(admitted)} admitted cells",
          file=sys.stderr)
    for i, name in enumerate(admitted):
        proc = subprocess.run(
            ["uv", "run", "tpy", str(cells[name])],
            cwd=REPO, capture_output=True, text=True)
        if proc.returncode != 0:
            bad += 1
            tail = (proc.stderr or proc.stdout).strip().split("\n")[-1][:160]
            print(f"EXEC  {name}: exit {proc.returncode}: {tail}")
        if os.environ.get("SWEEP_PROGRESS") and i % 25 == 0:
            print(f"  {i}/{len(admitted)}", file=sys.stderr)
    print(f"{len(admitted)} admitted cells, {bad} failed to build or run")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the committed verdict table")
    ap.add_argument("--exec", dest="do_exec", action="store_true",
                    help="also build and run each admitted cell (needs a "
                         "C++ toolchain); reviewer-only")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the generated programs")
    args = ap.parse_args()

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="prop_sweep_"))
    cells = generate(out_dir)
    print(f"{len(cells)} cells in {out_dir}", file=sys.stderr)
    table = run(out_dir, cells)

    if args.update:
        EXPECTED.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        print(f"wrote {EXPECTED} ({len(table)} cells)")
        return run_exec(cells, table) if args.do_exec else 0

    if args.do_exec:
        return run_exec(cells, table)

    want = json.loads(EXPECTED.read_text())
    moved = {k: (want.get(k), v) for k, v in table.items()
             if want.get(k) != v}
    gone = sorted(set(want) - set(table))
    if not moved and not gone:
        print(f"{len(table)} cells, no verdict moved")
        return 0
    for k, (was, now) in sorted(moved.items()):
        print(f"MOVED {k}: {was} -> {now}")
    for k in gone:
        print(f"GONE  {k}: {want[k]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
