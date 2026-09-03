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
    _assert_rejects_at,
    _reject_tally,
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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.slot_type")

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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.slot_type")


class TestMixedOwnStorageSinks:
    """Mixed-own-CALL sources at storage sinks -- every owning sink
    materializes the borrowed half via the NON-move
    `tuple_to_storage<S>(make_mixed(b))` (`_mixed_own_storage_source`)."""

    _MK = _HDR + (
        "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
        "    return (Box(1), b)\n"
    )

    def test_storage_sinks_route_byte_identical(self):
        src = self._MK + (
            "def in_list(b: Box) -> Int32:\n"
            "    xs = [make_mixed(b)]\n"
            "    return xs[0][1].n\n"
            "def in_dict(b: Box) -> Int32:\n"
            "    d = {1: make_mixed(b)}\n"
            "    return d[1][1].n\n"
            "def via_append(b: Box) -> Int32:\n"
            "    xs: list[tuple[Box, Box]] = []\n"
            "    xs.append(make_mixed(b))\n"
            "    return xs[0][1].n\n"
            "def via_setitem(b: Box) -> Int32:\n"
            "    d: dict[Int32, tuple[Box, Box]] = {}\n"
            "    d[1] = make_mixed(b)\n"
            "    return d[1][1].n\n"
            "def main() -> None:\n"
            "    print(in_list(Box(2)), in_dict(Box(2)))\n"
            "    print(via_append(Box(2)), via_setitem(Box(2)))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("push_back(::tpy::tuple_to_storage<std::tuple<Box, Box>>"
                "(make_mixed(b)));" in cpp)
        assert ("::tpy::__setitem__(d, 1, ::tpy::tuple_to_storage<"
                "std::tuple<Box, Box>>(make_mixed(b)));" in cpp)

    def test_mixed_call_positions_route(self):
        # The mixed render survives in the call result and the ternary:
        # subscript-on-call receivers spell `->`, the whole-local lift at a
        # borrow-tuple param spells tuple_to_pointer, the ternary decl
        # composes both arms bare.
        src = self._MK + (
            "def take(t: tuple[Box, Box]) -> Int32:\n"
            "    return t[0].n + t[1].n\n"
            "def read_direct(b: Box) -> Int32:\n"
            "    return make_mixed(b)[1].n\n"
            "def pass_whole(b: Box) -> Int32:\n"
            "    p = make_mixed(b)\n"
            "    n = take(p)\n"
            "    p[1].n = 50\n"
            "    return n + b.n\n"
            "def via_ternary(b: Box, c: Box, flag: bool) -> Int32:\n"
            "    p = make_mixed(b) if flag else make_mixed(c)\n"
            "    return p[1].n\n"
            "def main() -> None:\n"
            "    print(read_direct(Box(7)))\n"
            "    print(pass_whole(Box(7)))\n"
            "    print(via_ternary(Box(2), Box(3), True))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::get<1>(make_mixed(b))->n;" in cpp
        assert ("take(::tpy::tuple_to_pointer<std::tuple<const Box*, "
                "const Box*>>(p))" in cpp)
        assert "auto p = ((flag) ? (make_mixed(b)) : (make_mixed(c)));" in cpp

    def test_mixed_name_at_container_elem_still_defers(self):
        # Boundary: a mixed LOCAL name has no witnessed container-element
        # render (the sink would need the whole tuple_to_storage over the
        # name) -- the call-shaped predicate must not capture it.
        src = self._MK + (
            "def name_in_list(b: Box) -> Int32:\n"
            "    p = make_mixed(b)\n"
            "    xs = [p]\n"
            "    return xs[0][1].n\n"
            "def main() -> None:\n"
            "    print(name_in_list(Box(2)))\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:expr.container_literal")

    def test_own_container_elem_mixed_call_still_defers(self):
        # Boundary: an `Own[list]`-element mixed call stays outside the F1
        # slice at the storage sinks too (the container-literal twin of the
        # pass-row pins above).
        src = _HDR + (
            "def make_lc() -> tuple[Own[list[Int32]], Own[Box]]:\n"
            "    return ([1, 2], Box(3))\n"
            "def lc_in_list() -> Int32:\n"
            "    ys = [make_lc()]\n"
            "    return len(ys)\n"
            "def main() -> None:\n"
            "    print(lc_in_list())\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.slot_type")
