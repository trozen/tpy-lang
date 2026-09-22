#!/usr/bin/env python3
"""The tuple-element-vs-singleton VERDICT MATRIX.

The rule (`docs/PITFALLS.md`, `tuple-equals-scalar`): element `i` of a tuple
at position P behaves exactly as its type would as a standalone value at P.
That is a MATRIX claim -- element form x position -- and the corpus pins only
the cells someone thought to write: two censuses found two cells that had
changed character with no work aimed at them, and two defects nobody had
filed. This script generates the matrix, compiles every cell, and
writes the table next to itself. `docs/TUPLE_COMPLETION_PLAN.md` is gated on
its diff.

A cell is a PROGRAM, and most positions have two: `S`, the singleton, and
`T`, the same program with the value wrapped in a tuple. The table records
both, because "the tuple rejects here" only means something beside what the
singleton does. The grid is eight element forms over six positions (param,
return, field, collection, local, global); the `x` row holds the positions
that do not fit a grid -- unpacks, yields, loops, closures -- for a borrowed
reference element.

WHAT A CELL RECORDS. Four columns:

  verdict   "ok", or the reject / error tag the compiler stopped at
  warning   every warning the cell produced, " | "-joined ("" for none)
  form      the emitted C++ lines that name the probe slot (`probe_*`) or an
            unpack temp (`__tup_N`), for an admitted cell
  behaviour "same" / "differs" against CPython, from `--exec` only

WHICH COLUMNS GATE. The pytest gate (`tests/test_tuple_element_matrix.py`)
compares VERDICT and WARNING only: those move when a tuple program's
accept / reject / warn outcome changes, which is a language event, and the
fix is one `--update` with no build. The FORM and BEHAVIOUR columns are
advisory -- recorded, diffed by the script's own check, refreshed by whoever
works a tuple unit, never a reason for an unrelated branch to go red. The
form is a few lines of generated C++ that any render change can move, and the
behaviour needs a toolchain; gating either would tax every branch that
touches emission with a local build, for a regression the snapshot corpus
also sees. `--update` keeps a cell's recorded behaviour while its verdict,
warning and form are unchanged and resets it to "?" when they move;
`--exec` / `--exec-moved` fill it in, and `--report` shows the stale cells.

The FORM column is what shows the silent defects without a run: a tuple
global that copies is `extern std::tuple<Cell, int32_t> probe_g` beside the
scalar's `Cell* probe_g`, an unpack that copies is `auto __tup_1 = t;`, and a
`str` element that allocates is `std::tuple<std::string, ...>` beside
`std::string_view`. A `borrow` / `mixed` / `x` program mutates through the binding
and prints the ORIGINAL, so "differs" means TPy copied where CPython aliased;
whether that was warned is the warning column. The `own` row cannot be made
to observe that -- a copy in place of a move prints the same bytes -- so its
"same" says only that the program runs, and its FORM is what pins the move.
The `nocopy` row is the `borrow` row over a `@nocopy` record, where a copy is
a compile error: it moves the silent-copy class from `--exec` into the
verdict column, which is the one the gate reads.

  python scripts/thir_migration/review/tuple_element_matrix.py            # check
  python scripts/thir_migration/review/tuple_element_matrix.py --update   # rewrite
  python scripts/thir_migration/review/tuple_element_matrix.py --update --exec
  python scripts/thir_migration/review/tuple_element_matrix.py --update --exec-moved
  python scripts/thir_migration/review/tuple_element_matrix.py --report   # the grid
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The test gate loads this file by path, so the sibling module is not
# importable until its directory is on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cell_verdict import CELL_MARK, REPO, cell_verdict  # noqa: E402

EXPECTED = Path(__file__).with_suffix(".expected.json")

PRELUDE = '''from typing import Iterator
from tpy import Own, int32, nocopy


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class NCell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Other:
    def __init__(self) -> None:
        pass


def sink(x: Own[Cell]) -> int32:
    return x.n

'''

POSITIONS = ("param", "return", "field", "collection", "local", "global")

CELLS: dict[str, str] = {}

# ------------------------------------------------------------ borrow (ref)
CELLS["borrow__param__S"] = '''
def probe_fn(p: Cell) -> None:
    p.n = 9

def main() -> None:
    b = Cell(1)
    probe_fn(b)
    print(b.n)
'''
CELLS["borrow__param__T"] = '''
def probe_fn(p: tuple[Cell, int32]) -> None:
    p[0].n = 9

def main() -> None:
    b = Cell(1)
    probe_fn((b, 1))
    print(b.n)
'''
CELLS["borrow__return__S"] = '''
def probe_fn(b: Cell) -> Cell:
    return b

def main() -> None:
    b = Cell(1)
    r = probe_fn(b)
    r.n = 9
    print(b.n)
'''
CELLS["borrow__return__T"] = '''
def probe_fn(b: Cell) -> tuple[Cell, int32]:
    return (b, 1)

def main() -> None:
    b = Cell(1)
    r = probe_fn(b)
    r[0].n = 9
    print(b.n)
'''
CELLS["borrow__field__S"] = '''
class H:
    probe_f: Cell

    def __init__(self, v: Cell) -> None:
        self.probe_f = v

def main() -> None:
    b = Cell(1)
    h = H(b)
    h.probe_f.n = 9
    print(b.n)
'''
CELLS["borrow__field__T"] = '''
class H:
    probe_f: tuple[Cell, int32]

    def __init__(self, v: tuple[Cell, int32]) -> None:
        self.probe_f = v

def main() -> None:
    b = Cell(1)
    h = H((b, 1))
    h.probe_f[0].n = 9
    print(b.n)
'''
CELLS["borrow__collection__S"] = '''
def main() -> None:
    b = Cell(1)
    probe_xs: list[Cell] = []
    probe_xs.append(b)
    probe_xs[0].n = 9
    print(b.n)
'''
CELLS["borrow__collection__T_lit"] = '''
def main() -> None:
    b = Cell(1)
    probe_xs: list[tuple[Cell, int32]] = []
    probe_xs.append((b, 1))
    probe_xs[0][0].n = 9
    print(b.n)
'''
CELLS["borrow__collection__T_local"] = '''
def main() -> None:
    b = Cell(1)
    probe_xs: list[tuple[Cell, int32]] = []
    t = (b, 1)
    probe_xs.append(t)
    probe_xs[0][0].n = 9
    print(b.n)
'''
CELLS["borrow__collection__T_call"] = '''
def mk(b: Cell) -> tuple[Cell, int32]:
    return (b, 1)

def main() -> None:
    b = Cell(1)
    probe_xs: list[tuple[Cell, int32]] = []
    probe_xs.append(mk(b))
    probe_xs[0][0].n = 9
    print(b.n)
'''
CELLS["borrow__local__S"] = '''
def main() -> None:
    b = Cell(1)
    probe_loc = b
    probe_loc.n = 9
    print(b.n)
'''
CELLS["borrow__local__T"] = '''
def main() -> None:
    b = Cell(1)
    probe_loc = (b, 1)
    probe_loc[0].n = 9
    print(b.n)
'''
CELLS["borrow__global__S"] = '''
V = Cell(1)
probe_g: Cell = V

def main() -> None:
    probe_g.n = 9
    print(V.n)
'''
CELLS["borrow__global__T"] = '''
V = Cell(1)
probe_g: tuple[Cell, int32] = (V, 1)

def main() -> None:
    probe_g[0].n = 9
    print(V.n)
'''

# ------------------------------------------------------------ Own[T] (ref)
CELLS["own__param__S"] = '''
def probe_fn(x: Own[Cell]) -> int32:
    return sink(x)

def main() -> None:
    print(probe_fn(Cell(1)))
'''
CELLS["own__param__T"] = '''
def probe_fn(p: tuple[Own[Cell], int32]) -> int32:
    x, k = p
    return sink(x) + k

def main() -> None:
    print(probe_fn((Cell(1), 1)))
'''
CELLS["own__return__S"] = '''
def probe_fn() -> Own[Cell]:
    return Cell(1)

def main() -> None:
    r = probe_fn()
    r.n = 9
    print(sink(r))
'''
CELLS["own__return__T"] = '''
def probe_fn() -> tuple[Own[Cell], int32]:
    return (Cell(1), 1)

def main() -> None:
    x, k = probe_fn()
    x.n = 9
    print(sink(x) + k)
'''
CELLS["own__field__S"] = '''
class H:
    probe_f: Own[Cell]

    def __init__(self) -> None:
        self.probe_f = Cell(1)

def main() -> None:
    print(H().probe_f.n)
'''
CELLS["own__field__T"] = '''
class H:
    probe_f: tuple[Own[Cell], int32]

    def __init__(self) -> None:
        self.probe_f = (Cell(1), 1)

def main() -> None:
    print(H().probe_f[0].n)
'''
CELLS["own__collection__S"] = '''
def main() -> None:
    probe_xs: list[Own[Cell]] = []
    probe_xs.append(Cell(1))
    print(probe_xs[0].n)
'''
CELLS["own__collection__T"] = '''
def main() -> None:
    probe_xs: list[tuple[Own[Cell], int32]] = []
    probe_xs.append((Cell(1), 1))
    print(probe_xs[0][0].n)
'''
CELLS["own__local__S"] = '''
def main() -> None:
    probe_loc: Own[Cell] = Cell(1)
    print(probe_loc.n)
'''
CELLS["own__local__T"] = '''
def main() -> None:
    probe_loc: tuple[Own[Cell], int32] = (Cell(1), 1)
    print(probe_loc[0].n)
'''
CELLS["own__global__S"] = '''
probe_g: Own[Cell] = Cell(1)

def main() -> None:
    print(probe_g.n)
'''
CELLS["own__global__T"] = '''
probe_g: tuple[Own[Cell], int32] = (Cell(1), 1)

def main() -> None:
    print(probe_g[0].n)
'''

# ------------------------------------------------------------ mixed Own+borrow
# No singleton twin: the twins are the `own` and `borrow` rows.
_MK_MIXED = '''
def mk(b: Cell) -> tuple[Own[Cell], Cell]:
    return (Cell(1), b)
'''
CELLS["mixed__param__T_consume"] = '''
def probe_fn(p: tuple[Own[Cell], Cell]) -> int32:
    o, b = p
    b.n = 77
    return sink(o)

def main() -> None:
    b = Cell(1)
    print(probe_fn((Cell(2), b)), b.n)
'''
CELLS["mixed__param__T_read"] = '''
def probe_fn(p: tuple[Own[Cell], Cell]) -> int32:
    p[1].n = 77
    return p[0].n

def main() -> None:
    b = Cell(1)
    print(probe_fn((Cell(2), b)), b.n)
'''
CELLS["mixed__param__T_write_own"] = '''
def probe_fn(p: tuple[Own[Cell], Cell]) -> int32:
    p[0].n = 99
    return p[0].n

def main() -> None:
    b = Cell(1)
    print(probe_fn((Cell(2), b)), b.n)
'''
CELLS["mixed__return__T"] = _MK_MIXED + '''
def main() -> None:
    b = Cell(2)
    o, x = mk(b)
    x.n = 9
    o.n = 5
    print(b.n, sink(o))
'''
CELLS["mixed__field__T"] = _MK_MIXED + '''
class H:
    probe_f: tuple[Cell, Cell]

    def __init__(self, b: Cell) -> None:
        self.probe_f = mk(b)

def main() -> None:
    b = Cell(2)
    h = H(b)
    h.probe_f[1].n = 9
    print(b.n)
'''
CELLS["mixed__collection__T_call"] = _MK_MIXED + '''
def main() -> None:
    b = Cell(2)
    probe_xs: list[tuple[Cell, Cell]] = []
    probe_xs.append(mk(b))
    probe_xs[0][1].n = 9
    print(b.n)
'''
CELLS["mixed__collection__T_local"] = _MK_MIXED + '''
def main() -> None:
    b = Cell(2)
    probe_xs: list[tuple[Cell, Cell]] = []
    t = mk(b)
    probe_xs.append(t)
    probe_xs[0][1].n = 9
    print(b.n)
'''
CELLS["mixed__local__T"] = _MK_MIXED + '''
def main() -> None:
    b = Cell(2)
    probe_loc = mk(b)
    probe_loc[1].n = 9
    probe_loc[0].n = 5
    print(b.n, probe_loc[0].n)
'''
CELLS["mixed__global__T"] = _MK_MIXED + '''
V = Cell(2)
probe_g: tuple[Cell, Cell] = mk(V)

def main() -> None:
    probe_g[1].n = 9
    print(V.n)
'''

# ------------------------------------------------------------ value rows
# Every tuple program comes in both read shapes -- `T_sub` binds `t[0]`,
# `T_unpack` binds `x, k = t` -- and both bind the element to a `probe_x`
# local before printing it. The binding is the point: aliasing is
# unobservable for an immutable value, so what the rule says about these
# rows is VIEW-NESS, and only the emitted type of the bound element shows
# whether the read took a view or materialized an owned copy.
_VALUE_ROWS = (
    ("value", "int32", "7"),
    ("bigint", "int", "2**70"),
    ("str", "str", '"hello"'),
    ("bytes", "bytes", 'b"hello"'),
)


def _read(src: str, how: str, indent: str = "    ") -> str:
    if how == "sub":
        return (f"{indent}probe_x = {src}[0]\n"
                f"{indent}print(probe_x, {src}[1])")
    return f"{indent}probe_x, k = {src}\n{indent}print(probe_x, k)"


for _row, _ann, _lit in _VALUE_ROWS:
    CELLS[f"{_row}__param__S"] = f'''
def probe_fn(p: {_ann}) -> None:
    print(p)

def run(v: {_ann}) -> None:
    probe_fn(v)

def main() -> None:
    run({_lit})
'''
    CELLS[f"{_row}__return__S"] = f'''
def probe_fn(v: {_ann}) -> {_ann}:
    return v

def main() -> None:
    print(probe_fn({_lit}))
'''
    CELLS[f"{_row}__field__S"] = f'''
class H:
    probe_f: {_ann}

    def __init__(self, v: {_ann}) -> None:
        self.probe_f = v

def main() -> None:
    print(H({_lit}).probe_f)
'''
    CELLS[f"{_row}__collection__S"] = f'''
def run(v: {_ann}) -> None:
    probe_xs: list[{_ann}] = []
    probe_xs.append(v)
    print(probe_xs[0])

def main() -> None:
    run({_lit})
'''
    CELLS[f"{_row}__local__S"] = f'''
def run(v: {_ann}) -> None:
    probe_loc: {_ann} = v
    print(probe_loc)

def main() -> None:
    run({_lit})
'''
    CELLS[f"{_row}__global__S"] = f'''
probe_g: {_ann} = {_lit}

def main() -> None:
    print(probe_g)
'''
    for _how in ("sub", "unpack"):
        CELLS[f"{_row}__param__T_{_how}"] = f'''
def probe_fn(p: tuple[{_ann}, int32]) -> None:
{_read("p", _how)}

def run(v: {_ann}) -> None:
    probe_fn((v, 1))

def main() -> None:
    run({_lit})
'''
        CELLS[f"{_row}__return__T_{_how}"] = f'''
def probe_fn(v: {_ann}) -> tuple[{_ann}, int32]:
    return (v, 1)

def main() -> None:
    r = probe_fn({_lit})
{_read("r", _how)}
'''
        CELLS[f"{_row}__field__T_{_how}"] = f'''
class H:
    probe_f: tuple[{_ann}, int32]

    def __init__(self, v: {_ann}) -> None:
        self.probe_f = (v, 1)

def main() -> None:
    h = H({_lit})
{_read("h.probe_f", _how)}
'''
        CELLS[f"{_row}__collection__T_{_how}"] = f'''
def run(v: {_ann}) -> None:
    probe_xs: list[tuple[{_ann}, int32]] = []
    probe_xs.append((v, 1))
{_read("probe_xs[0]", _how)}

def main() -> None:
    run({_lit})
'''
        CELLS[f"{_row}__local__T_{_how}"] = f'''
def run(v: {_ann}) -> None:
    probe_loc: tuple[{_ann}, int32] = (v, 1)
{_read("probe_loc", _how)}

def main() -> None:
    run({_lit})
'''
        CELLS[f"{_row}__global__T_{_how}"] = f'''
probe_g: tuple[{_ann}, int32] = ({_lit}, 1)

def main() -> None:
{_read("probe_g", _how)}
'''

# ------------------------------------------------------------ nocopy (ref)
# The `borrow` row over a `@nocopy` record. A copy of one is a compile error,
# so a position that silently copies a `Cell` shows up HERE in the verdict
# column, with no build: the row is the toolchain-free witness for the
# aliasing the `borrow` row can only prove under `--exec`.
for _name in [n for n in CELLS if n.startswith("borrow__")]:
    CELLS["nocopy__" + _name[len("borrow__"):]] = re.sub(
        r"\bCell\b", "NCell", CELLS[_name])

# ------------------------------------------------------------ x: off-grid positions
# A borrowed reference element at the positions a grid does not hold.
_G = '''
def g(b: Cell) -> tuple[Cell, int32]:
    return (b, 1)
'''
CELLS["x__unpack_local__T"] = '''
def main() -> None:
    b = Cell(1)
    t = (b, 1)
    x, k = t
    x.n = 9
    print(b.n, k)
'''
CELLS["x__unpack_call__T"] = _G + '''
def main() -> None:
    b = Cell(1)
    x, k = g(b)
    x.n = 9
    print(b.n, k)
'''
CELLS["x__unpack_relay__T"] = _G + '''
def relay(b: Cell) -> tuple[Cell, int32]:
    return g(b)

def main() -> None:
    b = Cell(1)
    x, k = relay(b)
    x.n = 9
    print(b.n, k)
'''
CELLS["x__unpack_live_own__S"] = '''
def mk() -> Own[Cell]:
    return Cell(1)

def main() -> None:
    t = mk()
    x = t
    x.n = 9
    print(t.n)
'''
CELLS["x__unpack_live_own__T"] = '''
def mk() -> tuple[Own[Cell], int32]:
    return (Cell(1), 1)

def main() -> None:
    t = mk()
    x, k = t
    x.n = 9
    print(t[0].n, k)
'''
CELLS["x__unpack_live_literal__T"] = '''
def main() -> None:
    t = (Cell(1), 1)
    x, k = t
    x.n = 9
    print(t[0].n, k)
'''
CELLS["x__unpack_field__T"] = '''
class H:
    f: tuple[Cell, int32]

    def __init__(self) -> None:
        self.f = (Cell(1), 1)

def main() -> None:
    h = H()
    x, k = h.f
    x.n = 9
    print(h.f[0].n, k)
'''
CELLS["x__rebind_unpack__S"] = '''
def main() -> None:
    b = Cell(1)
    a = b
    a = Cell(5)
    a.n = 6
    print(b.n)
'''
CELLS["x__rebind_unpack__T"] = _G + '''
def main() -> None:
    b = Cell(1)
    x, k = g(b)
    x = Cell(5)
    x.n = 6
    print(b.n, k)
'''
CELLS["x__swap__T"] = '''
def main() -> None:
    first = Cell(1)
    second = Cell(2)
    probe_a = first
    probe_b = second
    probe_a, probe_b = probe_b, probe_a
    probe_a.n = 9
    print(first.n, second.n)
'''
CELLS["x__return_two__T"] = '''
def pick(a: Cell, b: Cell) -> tuple[Cell, Cell]:
    return (b, a)

def main() -> None:
    a = Cell(1)
    b = Cell(2)
    x, y = pick(a, b)
    x.n = 8
    y.n = 9
    print(a.n, b.n)
'''
CELLS["x__method_return__S"] = '''
class H:
    b: Cell

    def __init__(self) -> None:
        self.b = Cell(1)

    def get(self) -> Cell:
        return self.b

def main() -> None:
    h = H()
    probe_loc = h.get()
    probe_loc.n = 9
    print(h.b.n)
'''
CELLS["x__method_return__T"] = '''
class H:
    t: tuple[Cell, int32]

    def __init__(self) -> None:
        self.t = (Cell(1), 1)

    def get(self) -> tuple[Cell, int32]:
        return self.t

def main() -> None:
    h = H()
    probe_loc = h.get()
    probe_loc[0].n = 9
    print(h.t[0].n)
'''
CELLS["x__yield__S"] = '''
def gen(b: Cell) -> Iterator[Cell]:
    yield b

def main() -> None:
    b = Cell(1)
    for probe_loc in gen(b):
        probe_loc.n = 9
    print(b.n)
'''
CELLS["x__yield_unpack__T"] = '''
def gen(b: Cell) -> Iterator[tuple[Cell, int32]]:
    yield (b, 1)

def main() -> None:
    b = Cell(1)
    for x, k in gen(b):
        x.n = 9
    print(b.n)
'''
CELLS["x__yield_whole__T"] = '''
def gen(b: Cell) -> Iterator[tuple[Cell, int32]]:
    yield (b, 1)

def main() -> None:
    b = Cell(1)
    for probe_loc in gen(b):
        probe_loc[0].n = 9
    print(b.n)
'''
CELLS["x__yield_repack__T"] = '''
def mk() -> tuple[Own[Cell], int32]:
    return (Cell(1), 1)

def gen() -> Iterator[tuple[Cell, int32]]:
    t = mk()
    yield (t[0], t[1])
    print(t[0].n)

def main() -> None:
    for x, k in gen():
        x.n = 9
'''
CELLS["x__for__S"] = '''
def main() -> None:
    ys = [Cell(1), Cell(2)]
    for bx in ys:
        bx.n = 9
    print(ys[0].n, ys[1].n)
'''
CELLS["x__for_unpack__T"] = '''
def main() -> None:
    xs = [(Cell(1), 1), (Cell(2), 2)]
    for bx, k in xs:
        bx.n = 9 + k
    print(xs[0][0].n, xs[1][0].n)
'''
CELLS["x__enumerate__T"] = '''
def main() -> None:
    ys = [Cell(1), Cell(2)]
    for i, bx in enumerate(ys):
        bx.n = 9 + i
    print(ys[0].n, ys[1].n)
'''
CELLS["x__zip__T"] = '''
def main() -> None:
    ys = [Cell(1), Cell(2)]
    zs = [Cell(3), Cell(4)]
    for x, y in zip(ys, zs):
        x.n = y.n + 10
    print(ys[0].n, ys[1].n)
'''
CELLS["x__dict_items__T"] = '''
def main() -> None:
    d = {"a": Cell(1)}
    for k, v in d.items():
        v.n = 9
    print(d["a"].n)
'''
CELLS["x__closure__S"] = '''
def main() -> None:
    b = Cell(1)
    probe_loc = b

    def inner() -> None:
        probe_loc.n = 9

    inner()
    print(b.n)
'''
CELLS["x__closure__T"] = '''
def main() -> None:
    b = Cell(1)
    probe_loc = (b, 1)

    def inner() -> None:
        probe_loc[0].n = 9

    inner()
    print(b.n)
'''
CELLS["x__walrus__S"] = '''
def g1(b: Cell) -> Cell:
    return b

def main() -> None:
    b = Cell(1)
    if (r := g1(b)).n == 1:
        r.n = 9
    print(b.n)
'''
CELLS["x__walrus__T"] = _G + '''
def main() -> None:
    b = Cell(1)
    if (t := g(b))[1] == 1:
        t[0].n = 9
    print(b.n)
'''
CELLS["x__finally_return__S"] = '''
def g1(b: Cell) -> Cell:
    try:
        return b
    finally:
        b.n = 7

def main() -> None:
    b = Cell(1)
    probe_loc = g1(b)
    print(probe_loc.n)
    probe_loc.n = 9
    print(b.n)
'''
CELLS["x__finally_return__T"] = '''
def g2(b: Cell) -> tuple[Cell, int32]:
    try:
        return (b, 1)
    finally:
        b.n = 7

def main() -> None:
    b = Cell(1)
    probe_loc = g2(b)
    print(probe_loc[0].n)
    probe_loc[0].n = 9
    print(b.n)
'''
CELLS["x__read_elem__S"] = '''
def main() -> None:
    ys = [Cell(1), Cell(2)]
    probe_loc = ys[0]
    probe_loc.n = 9
    print(ys[0].n)
'''
CELLS["x__read_elem__T"] = '''
def main() -> None:
    xs = [(Cell(1), 1), (Cell(2), 2)]
    probe_loc = xs[0]
    probe_loc[0].n = 9
    print(xs[0][0].n)
'''
CELLS["x__optional_elem__S"] = '''
def main() -> None:
    b = Cell(1)
    x: Cell | None = b
    if x is not None:
        x.n = 9
    print(b.n)
'''
CELLS["x__optional_elem__T"] = '''
def main() -> None:
    b = Cell(1)
    t: tuple[Cell | None, int32] = (b, 1)
    x = t[0]
    if x is not None:
        x.n = 9
    print(b.n)
'''
CELLS["x__nested__T"] = '''
def main() -> None:
    b = Cell(1)
    probe_loc = (1, (b, 2))
    probe_loc[1][0].n = 9
    print(b.n)
'''
CELLS["x__dict_value__S"] = '''
def main() -> None:
    b = Cell(1)
    d: dict[str, Cell] = {}
    d["a"] = b
    d["a"].n = 9
    print(b.n)
'''
CELLS["x__dict_value__T"] = '''
def main() -> None:
    b = Cell(1)
    d: dict[str, tuple[Cell, int32]] = {}
    d["a"] = (b, 1)
    d["a"][0].n = 9
    print(b.n)
'''
CELLS["x__own_arg__S"] = '''
def take(x: Own[Cell]) -> int32:
    return x.n

def main() -> None:
    b = Cell(1)
    print(take(b))
    b.n = 9
    print(b.n)
'''
CELLS["x__own_arg__T"] = '''
def take(p: Own[tuple[Cell, int32]]) -> int32:
    return p[0].n

def main() -> None:
    b = Cell(1)
    t = (b, 1)
    print(take(t))
    b.n = 9
    print(b.n)
'''
CELLS["x__method_param__S"] = '''
class H:
    def __init__(self) -> None:
        pass

    def probe_fn(self, p: Cell) -> None:
        p.n = 9

def main() -> None:
    b = Cell(1)
    H().probe_fn(b)
    print(b.n)
'''
CELLS["x__method_param__T"] = '''
class H:
    def __init__(self) -> None:
        pass

    def probe_fn(self, p: tuple[Cell, int32]) -> None:
        p[0].n = 9

def main() -> None:
    b = Cell(1)
    H().probe_fn((b, 1))
    print(b.n)
'''
CELLS["x__union_elem__S"] = '''
def probe_fn(p: Cell | Other) -> None:
    if isinstance(p, Cell):
        p.n = 9

def main() -> None:
    b = Cell(1)
    probe_fn(b)
    print(b.n)
'''
CELLS["x__union_elem__T"] = '''
def probe_fn(p: tuple[Cell | Other, int32]) -> None:
    x = p[0]
    if isinstance(x, Cell):
        x.n = 9

def main() -> None:
    b = Cell(1)
    probe_fn((b, 1))
    print(b.n)
'''
# Read-only, so parity-blind by construction: a comprehension cannot mutate.
# The cell is here for its VERDICT and its unpack form.
CELLS["x__comprehension__T"] = '''
def main() -> None:
    xs = [(Cell(1), 1), (Cell(2), 2)]
    probe_xs = [bx.n + k for bx, k in xs]
    print(probe_xs)
'''


def generate(out_dir: Path) -> dict[str, Path]:
    """Write one program per cell; return {cell name: path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cells: dict[str, Path] = {}
    for name, body in CELLS.items():
        path = out_dir / f"{name}.py"
        path.write_text(PRELUDE + CELL_MARK + "\n" + body + "\n\nmain()\n")
        cells[name] = path
    return cells


_FORM_LINE = re.compile(r"\bprobe_\w+|\b__tup_\d+\b")
_FORM_MAX = 6


def _form(src: Path, build_dir: Path) -> str:
    """The emitted lines that name the probe slot or an unpack temp.

    Declarations first (the header), then the body. A `std::cout` line is a
    read of the result, not the slot, and the module namespace carries the
    cell's own name, so both are dropped.
    """
    stem = src.stem
    out_root = build_dir / f"{stem}.d"
    lines: list[str] = []
    for rel in (f"include/{stem}.hpp", f"src/{stem}.cpp"):
        path = out_root / rel
        if not path.exists():
            continue
        for raw in path.read_text().split("\n"):
            if not _FORM_LINE.search(raw) or "std::cout" in raw:
                continue
            line = " ".join(raw.split())
            line = line.replace(f"::tpyapp::{stem}::", "")
            line = line.removesuffix(" {").removesuffix(";")
            if line not in lines:
                lines.append(line)
    # Say so when the cap bites: a slot line dropped silently would make the
    # column blind exactly where it is longest.
    more = [" ..."] if len(lines) > _FORM_MAX else []
    return " | ".join(lines[:_FORM_MAX] + more)


def comp_cell(src: Path, build_dir: Path) -> list[str]:
    """`[verdict, warning, form]` -- the toolchain-free columns."""
    verdict, warning = cell_verdict(src, build_dir, all_warnings=True)
    form = _form(src, build_dir) if verdict == "ok" else ""
    return [verdict, warning, form]


def run(out_dir: Path, cells: dict[str, Path]) -> dict[str, list[str]]:
    build = out_dir / "build"
    table: dict[str, list[str]] = {}
    for name, path in sorted(cells.items()):
        table[name] = comp_cell(path, build / name)
    return table


def _behaviour(src: Path) -> str:
    """Build and run the cell under TPy and under CPython; compare stdout."""
    env = dict(os.environ)
    tpy = subprocess.run(["uv", "run", "tpy", "-j", "4", str(src)],
                         cwd=src.parent, capture_output=True, text=True,
                         env=env)
    if tpy.returncode != 0:
        # A C++ diagnostic names the generated source; anything else died at
        # run time, which for this matrix is the more serious of the two.
        return "build-fail" if ".cpp:" in tpy.stderr else "run-fail"
    env["PYTHONPATH"] = str(REPO / "lib" / "cpy")
    cpy = subprocess.run([sys.executable, str(src)], cwd=src.parent,
                         capture_output=True, text=True, env=env)
    if cpy.returncode != 0:
        return "cpython-fail"
    return "same" if tpy.stdout == cpy.stdout else "differs"


def run_exec(cells: dict[str, Path], table: dict[str, list[str]],
             only: set[str] | None = None) -> dict[str, str]:
    admitted = sorted(k for k, v in table.items()
                      if v[0] == "ok" and (only is None or k in only))
    print(f"building and running {len(admitted)} admitted cells",
          file=sys.stderr)
    # Four builds at four jobs each: a full pass is about ten minutes and
    # leaves the machine usable. Uncapped it ran 16 jobs per build.
    with ThreadPoolExecutor(max_workers=4) as pool:
        seen = pool.map(lambda name: _behaviour(cells[name]), admitted)
        return dict(zip(admitted, seen))


def _classify(row: list[str]) -> str:
    verdict, warning, _form_, behaviour = row
    if verdict != "ok":
        return f"reject({verdict[:44]})"
    if behaviour in ("same", "?"):
        return "ok" if behaviour == "same" else "ok?"
    if behaviour == "differs":
        return "copy+warn" if "copies" in warning or "copy()" in warning \
            else "SILENT"
    return behaviour


def report(table: dict[str, list[str]]) -> None:
    """The element form x position grid, one class per program."""
    rows: dict[str, dict[str, list[str]]] = {}
    for name, cols in table.items():
        row, pos, prog = name.split("__", 2)
        rows.setdefault(row, {}).setdefault(pos, []).append(
            f"{prog}: {_classify(cols)}")
    grid = [r for r in rows if r != "x"]
    print("| element form | " + " | ".join(POSITIONS) + " |")
    print("|---|" + "---|" * len(POSITIONS))
    for row in grid:
        print(f"| {row} | " + " | ".join(
            "<br>".join(sorted(rows[row].get(pos, ["-"])))
            for pos in POSITIONS) + " |")
    print()
    print("| off-grid position | programs |")
    print("|---|---|")
    for pos, progs in sorted(rows.get("x", {}).items()):
        print(f"| {pos} | " + "; ".join(sorted(progs)) + " |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the committed table")
    ap.add_argument("--exec", dest="do_exec", action="store_true",
                    help="also build and run each admitted cell against "
                         "CPython (needs a C++ toolchain)")
    ap.add_argument("--exec-moved", dest="exec_moved", action="store_true",
                    help="like --exec, but only for the cells whose "
                         "toolchain-free columns moved")
    ap.add_argument("--report", action="store_true",
                    help="print the grid from the COMMITTED table and exit")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the generated programs")
    args = ap.parse_args()

    want: dict[str, list[str]] = (
        json.loads(EXPECTED.read_text()) if EXPECTED.exists() else {})
    if args.report:
        report(want)
        return 0

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="tuple_matrix_"))
    cells = generate(out_dir)
    print(f"{len(cells)} cells in {out_dir}", file=sys.stderr)
    comp = run(out_dir, cells)
    if args.do_exec:
        behaviour = run_exec(cells, comp)
    elif args.exec_moved:
        behaviour = run_exec(cells, comp, {
            k for k, v in comp.items() if (want.get(k) or [])[:3] != v})
    else:
        behaviour = {}

    table: dict[str, list[str]] = {}
    for name, cols in comp.items():
        old = want.get(name)
        if name in behaviour:
            seen = behaviour[name]
        elif cols[0] != "ok":
            seen = ""
        elif old is not None and old[:3] == cols:
            seen = old[3]
        else:
            seen = "?"
        table[name] = cols + [seen]

    if args.update:
        EXPECTED.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        unknown = sum(1 for v in table.values() if v[3] == "?")
        print(f"wrote {EXPECTED} ({len(table)} cells, {unknown} with "
              f"behaviour unknown -- rerun with --exec)")
        return 0

    width = 4 if args.do_exec else 3
    moved = {k: (want.get(k), v) for k, v in table.items()
             if (want.get(k) or [])[:width] != v[:width]}
    gone = sorted(set(want) - set(table))
    if not moved and not gone:
        print(f"{len(table)} cells, nothing moved")
        return 0
    for k, (was, now) in sorted(moved.items()):
        print(f"MOVED {k}:\n  was {was}\n  now {now}")
    for k in gone:
        print(f"GONE  {k}: {want[k]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
