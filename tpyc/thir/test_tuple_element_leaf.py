"""How a tuple subscript's element is held, per receiver kind: the leaf of
the receiver's Source (`tuple_elems(receiver.source, layout)[N]`) is a bare
pointer exactly where the tuple holds the element as `T*`. Expected values
are written per receiver, not taken from another predicate."""

from __future__ import annotations

import dataclasses

import pytest

from ..identity_map import IdentityMap
from . import nodes as th
from .lower.convert import layout_tuple
from .source import tuple_elems
from .testutil import lower_bodies
from .validate import _iter_children

_SRC = '''
from typing import Iterator
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    t: tuple[Box, int32]
    p: tuple[int32, Box]

    def __init__(self) -> None:
        self.t = (Box(1), 1)
        self.p = (7, Box(2))


def mk(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def mk_own(n: int32) -> Own[tuple[Box, int32]]:
    return (Box(n), n)


def mk_pair(v: int32) -> tuple[int32, Own[Box]]:
    return (v, Box(v))


def borrow_param(t: tuple[Box, int32]) -> int32:
    return t[0].n


def own_param(t: Own[tuple[Box, int32]]) -> int32:
    return t[0].n


def borrow_local(b: Box) -> int32:
    t = (b, 1)
    return t[0].n


def mixed_local(b: Box) -> int32:
    t = (Box(1), b)
    return t[0].n + t[1].n


def field(h: H) -> int32:
    return h.t[0].n


def container(ts: list[tuple[Box, int32]]) -> int32:
    return ts[0][0].n


def mixed_call(b: Box) -> int32:
    return mk(b)[1].n + mk(b)[0].n


def walrus_slot() -> int32:
    if (t := mk_own(5))[1] > 0:
        return t[0].n
    return 0


def walrus_reassigned(h: H) -> int32:
    first = (t := mk_pair(9))[1].n
    t = h.p
    return first + t[0]


def frame_own(b: Box) -> Iterator[int32]:
    t = (Box(1), b)
    yield t[0].n
    yield t[1].n
'''

# Per body, each tuple element read in source order: whether the tuple holds
# that element as a bare pointer.
_EXPECTED = {
    "borrow_param": [True],
    "own_param": [False],
    "borrow_local": [True],
    "mixed_local": [False, True],
    "field": [False],
    "container": [False],
    "mixed_call": [True, False],
    "walrus_slot": [False, False],
    "walrus_reassigned": [True, False],
    "frame_own": [False, True],
}


def _walk(node):
    yield node
    if isinstance(node, th.THIRResumableBody):
        # A frame's statements live in its leaf tables.
        for f in dataclasses.fields(node):
            table = getattr(node, f.name)
            if isinstance(table, IdentityMap):
                for v in table.values():
                    for x in (v if isinstance(v, (list, tuple)) else (v,)):
                        if isinstance(x, th.THIRNode):
                            yield from _walk(x)
        return
    for child in _iter_children(node):
        yield from _walk(child)


def _element_reads(body) -> list[th.THIRSubscript]:
    out: list[th.THIRSubscript] = []
    seen: set[int] = set()
    for n in _walk(body):
        # A read the lowering synthesized (a lent element) has no location.
        if (isinstance(n, th.THIRSubscript) and n.tuple_index is not None
                and n.loc is not None and id(n) not in seen):
            seen.add(id(n))
            out.append(n)
    # A frame's leaf tables may hold one read more than once; source
    # order is the reads' own.
    out.sort(key=lambda n: (n.loc.line, n.loc.column))
    uniq: list[th.THIRSubscript] = []
    for n in out:
        if not uniq or (uniq[-1].loc, uniq[-1].tuple_index) != (
                n.loc, n.tuple_index):
            uniq.append(n)
    return uniq


@pytest.fixture(scope="module")
def bodies():
    return lower_bodies(_SRC)[0]


@pytest.mark.parametrize("name", sorted(_EXPECTED))
def test_tuple_element_leaf_is_pointer_where_held_as_one(bodies, name):
    reads = _element_reads(bodies[name])
    leaves = []
    for n in reads:
        recv = n.receiver
        assert recv.source is not None, f"{name}: unstamped receiver"
        layout = layout_tuple(recv)
        assert layout is not None, f"{name}: receiver is no tuple"
        leaves.append(tuple_elems(recv.source, layout)[n.tuple_index]
                      .pointer_held)
    assert leaves == _EXPECTED[name]
    # The read's one carrier of the fact is the leaf's.
    assert [n.elem_pointer for n in reads] == _EXPECTED[name]
