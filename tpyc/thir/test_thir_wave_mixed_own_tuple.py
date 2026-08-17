"""Pins for the mixed own/borrow tuple call-pass rows: a call rvalue whose
type EXACTLY matches a `tuple[Own[Box], Box]` param binds the slot bare
(the element-blind pass, `_mixed_own_borrow_tuple` widening
`_btuple_pass_arg` -- gate and render key the same predicate), and the
fully-owned sibling (`take_owned(make_owned())` at
`std::tuple<Box, Box>&&`) binds its rvalue-ref slot bare
(`call.own_tuple_pass`). Boundary: an `Own[container]` element keeps the
tuple outside the family."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)


class TestMixedOwnTuplePass:
    def test_mixed_call_rvalue_binds_bare(self):
        src = _HDR + (
            "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
            "    return (Box(7), b)\n"
            "def take_mixed(p: tuple[Own[Box], Box]) -> Int32:\n"
            "    return p[0].n + p[1].n\n"
            "def main() -> None:\n"
            "    b = Box(2)\n"
            "    print(take_mixed(make_mixed(b)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["call.btuple_pass"] >= 1
        _assert_byte_identical(src)

    def test_owned_movable_call_rvalue_binds_bare(self):
        src = _HDR + (
            "def make_owned() -> tuple[Own[Box], Own[Box]]:\n"
            "    return (Box(1), Box(2))\n"
            "def take_owned(p: tuple[Own[Box], Own[Box]]) -> Int32:\n"
            "    return p[0].n + p[1].n\n"
            "def main() -> None:\n"
            "    print(take_owned(make_owned()))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["call.own_tuple_pass"] >= 1
        _assert_byte_identical(src)


class TestMixedOwnTupleBoundaries:
    def test_pure_owned_container_element_still_defers(self):
        # The fully-owned sibling with a container element
        # (`tuple[Own[list[Int32]], Own[Box]]`): the pass row's predicate
        # is element-unrestricted, but the surrounding gates (the inner
        # call's result family) keep the shape out today -- pin that so a
        # future widening re-measures the render instead of inheriting
        # the bare bind unverified.
        src = _HDR + (
            "def make_lc() -> tuple[Own[list[Int32]], Own[Box]]:\n"
            "    return ([1, 2], Box(3))\n"
            "def take_lc(p: tuple[Own[list[Int32]], Own[Box]]) -> Int32:\n"
            "    return len(p[0]) + p[1].n\n"
            "def main() -> None:\n"
            "    print(take_lc(make_lc()))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        _assert_byte_identical(src)

    def test_own_container_element_still_defers(self):
        # An `Own[list]` element keeps the tuple outside the F1 slice --
        # the pass rows must not take it.
        src = _HDR + (
            "def make_bad(b: Box) -> tuple[Own[list[Int32]], Box]:\n"
            "    return ([1, 2], b)\n"
            "def take_bad(p: tuple[Own[list[Int32]], Box]) -> Int32:\n"
            "    return len(p[0])\n"
            "def main() -> None:\n"
            "    b = Box(9)\n"
            "    print(take_bad(make_bad(b)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        assert not faces.get("call.own_tuple_pass")
        _assert_byte_identical(src)
