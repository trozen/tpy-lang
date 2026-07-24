"""Pins for the comprehension ArgTemp and borrow-tuple field arg rows:
the slot-typed `({...})` temp at container ref slots, and the
tuple_to_pointer wrap over a storage F3-tuple member -- with the
deep-const verdict boundary."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)


class TestComprehensionArg:
    def test_list_comp_arg_temps(self):
        src = (
            "from tpy import Int32, Int64\n"
            "def accept_wide(items: list[Int64]) -> None:\n"
            "    print(len(items))\n"
            "def main() -> None:\n"
            "    items: list[Int32] = [1, 2, 3]\n"
            "    accept_wide([x for x in items])\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.comprehension"] >= 1
        _assert_byte_identical(src)


class TestBorrowTupleFieldArg:
    _SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "class Holder:\n"
        "    pair: tuple[Int32, Box]\n"
        "    def __init__(self, b: Box) -> None:\n"
        "        self.pair = (1, b)\n"
    )

    def test_mutable_slot_wraps_nonconst(self):
        src = self._SRC + (
            "def bump(p: tuple[Int32, Box]) -> None:\n"
            "    p[1].val += 5\n"
            "def main() -> None:\n"
            "    h = Holder(Box(10))\n"
            "    bump(h.pair)\n"
            "    print(h.pair[1].val)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.borrow_tuple_field"] >= 1
        _assert_byte_identical(src)

    def test_deep_const_slot_wraps_const(self):
        # An UNMUTATED callee param carries the inferred deep-const verdict:
        # the wrap's destination spells `const Box*` -- the readonly_target
        # threading, byte-diff pinned (a bare readonly check missed it).
        src = self._SRC + (
            "def read_pair(p: tuple[Int32, Box]) -> Int32:\n"
            "    return p[1].val\n"
            "def main() -> None:\n"
            "    h = Holder(Box(10))\n"
            "    print(read_pair(h.pair))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.borrow_tuple_field"] >= 1
        _assert_byte_identical(src)
