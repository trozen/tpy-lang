"""An `Own[T]` operand at a type-param comparison. The wrapper marks a
transfer the consuming SINK performs, so reading one to compare it is the
same bare `T` value -- but a concrete (non-type-param) `Own` payload is a
different disjunct and keeps rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _thir_ctx,
)


class TestOwnTparamCompare:

    def test_own_param_against_element_routes(self):
        # The heapq heappushpop shape: a list element compared against the
        # Own[T] param, which the setitem sink then moves out of.
        src = ("from tpy import Int32, Comparable, Own, copy\n"
               "def pushpop[T: Comparable](heap: list[T], item: Own[T]) -> Own[T]:\n"
               "    if len(heap) > 0 and heap[0] < item:\n"
               "        result: T = copy(heap[0])\n"
               "        heap[0] = item\n"
               "        return result\n"
               "    return item\n"
               "def main() -> None:\n"
               "    xs = [3, 1, 2]\n"
               "    print(pushpop(xs, 5))\n"
               "main()\n")
        hpp = _assert_routes_byte_identical(src)[0]
        # The compare reads the operand bare; the move belongs to the sink.
        assert "(::tpy::__getitem__(heap, 0) < item)" in hpp
        assert "::tpy::__setitem__(heap, 0, std::move(item))" in hpp

    def test_own_operand_on_the_left_routes(self):
        src = ("from tpy import Int32, Comparable, Own\n"
               "def mirror[T: Comparable](heap: list[T], item: Own[T]) -> Int32:\n"
               "    if item < heap[0]:\n"
               "        return 1\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    xs = [3, 1, 2]\n"
               "    print(mirror(xs, 5))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_own_operands_on_both_sides_route(self):
        # Ordering and equality over a pair of Own[T] params.
        src = ("from tpy import Int32, Comparable, Own\n"
               "def both[T: Comparable](a: Own[T], b: Own[T]) -> Int32:\n"
               "    if a < b:\n"
               "        return 1\n"
               "    if a == b:\n"
               "        return 2\n"
               "    return 0\n"
               "print(both(1, 2))\n")
        _assert_routes_byte_identical(src)

    def test_concrete_own_payload_stays_ast(self):
        # A CONCRETE `Own[Int32]` operand is the scalar disjunct's business,
        # and that one still keys on the wrapper -- it must keep rejecting.
        src = ("from tpy import Int32, Own\n"
               "def pick(a: Int32, item: Own[Int32]) -> Int32:\n"
               "    if a < item:\n"
               "        return a\n"
               "    return item\n"
               "print(pick(1, 2))\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.if")
