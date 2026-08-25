"""The two user-record subscript gates must answer the index question alike.

`_record_getitem_idx_recv_ok` (read) and `_user_record_setitem_ok` (write) each
run `_bigint_index_disposition` over the subscript index. The write side is
REDUNDANT today -- a `recv[k] = v` target lowers through the read arm, so the
read gate already rejects everything the write gate would -- which means no
routing pin, no reject pin and no byte-diff can observe the write gate drifting
away from its sibling. This file is that observation: it calls both gates
directly on the same index shapes and fails if their verdicts part.
"""

from __future__ import annotations

import pytest

from ..compilation_context import activate_compiler
from ..parse.nodes import (
    TpyAssign, TpyCall, TpyCoerce, TpyExprStmt, TpySubscript,
)
from ..typesys import BIGINT
from .lower.checks import _user_record_setitem_ok
from .lower.predicates import _record_getitem_idx_recv_ok
from .testutil import _compile, _entry

# `p = 0` retro-widened to BigInt by a later `p = gi()`: the shape whose
# per-occurrence type (Int32) and DECLARED type (BigInt) disagree, and the only
# reason the disposition call exists at either gate.
_SRC = (
    "from tpy import Int32\n"
    "def gi() -> int:\n"
    "    return 1\n"
    "class Bag:\n"
    "    xs: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.xs = [1, 2, 3, 4]\n"
    "    def __getitem__(self, i: Int32) -> Int32:\n"
    "        return self.xs[i]\n"
    "    def __setitem__(self, i: Int32, v: Int32) -> None:\n"
    "        self.xs[i] = v\n"
    "class BigBag:\n"
    "    xs: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.xs = [1, 2, 3, 4]\n"
    "    def __getitem__(self, i: int) -> Int32:\n"
    "        return self.xs[0]\n"
    "    def __setitem__(self, i: int, v: Int32) -> None:\n"
    "        self.xs[0] = v\n"
    "def probe(b: Bag, bb: BigBag) -> None:\n"
    "    p = 0\n"
    "    print(b[p])\n"
    "    b[p] = 5\n"
    "    print(b[p + 1])\n"
    "    b[p + 1] = 5\n"
    "    print(b[1])\n"
    "    b[1] = 5\n"
    "    print(bb[p])\n"
    "    bb[p] = 5\n"
    "    p = gi()\n"
    "    print(p)\n"
    "def main() -> None:\n"
    "    probe(Bag(), BigBag())\n"
    "main()\n"
)

# One row per (read, write) statement pair in `probe`, in source order.
# The verdict each pair must agree on -- and, being the disposition's own
# answer, the reason each gate has to ask it.
_ROWS = [
    ("bare widened name", True),
    ("composite over widened name", False),
    ("in-range literal", True),
    ("bare widened name, BigInt-keyed receiver", True),
]


def _peel(e):
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def _pairs(fn):
    """The (read subscript, write assign) statement pairs of `probe`."""
    reads: list[TpySubscript] = []
    writes: list[TpyAssign] = []
    for stmt in fn.body:
        if (isinstance(stmt, TpyExprStmt)
                and isinstance(stmt.expr, TpyCall)
                and stmt.expr.args
                and isinstance(_peel(stmt.expr.args[0]), TpySubscript)):
            reads.append(_peel(stmt.expr.args[0]))
        elif isinstance(stmt, TpyAssign) and isinstance(stmt.target,
                                                        TpySubscript):
            writes.append(stmt)
    return list(zip(reads, writes))


@pytest.fixture(scope="module")
def _gate_inputs():
    compiler, modules = _compile(_SRC)
    entry = _entry(modules)
    analyzer = entry.analyzer
    fn = next(f for f in entry.ast.functions if f.name == "probe")
    pairs = _pairs(fn)
    assert len(pairs) == len(_ROWS), (
        f"the fixture yielded {len(pairs)} read/write pairs, not "
        f"{len(_ROWS)} -- the table and the source have drifted apart")
    # What the lowerer would hold at these statements: the params plus `p` at
    # its FINAL, retro-widened type (the gates read the declared map, not
    # sema's per-occurrence cache).
    declared = {
        "b": analyzer.get_expr_type(pairs[0][0].obj),
        "bb": analyzer.get_expr_type(pairs[3][0].obj),
        "p": BIGINT,
    }
    assert all(t is not None for t in declared.values())
    return compiler, analyzer, declared, pairs


@pytest.mark.parametrize("row", range(len(_ROWS)))
def test_read_and_write_gates_agree_on_the_index(_gate_inputs, row):
    compiler, analyzer, declared, pairs = _gate_inputs
    label, expected = _ROWS[row]
    sub, assign = pairs[row]
    with activate_compiler(compiler):
        read_ok = _record_getitem_idx_recv_ok(sub, declared, analyzer, set())
        write_ok = _user_record_setitem_ok(assign, declared, set(), frozenset(),
                                           analyzer)
    assert read_ok == expected, f"{label}: read gate said {read_ok}"
    assert write_ok == expected, (
        f"{label}: the write gate said {write_ok} where its read sibling said "
        f"{read_ok} -- the two user-record subscript gates must answer the "
        f"same index question, and nothing else can see them part")
