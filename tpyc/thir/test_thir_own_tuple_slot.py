"""An owning CALL whose result IS an `Own[tuple]` argument slot: the prvalue
binds the by-value slot with no lift, so the render is bare and blind to the
callee being generic. A BORROW-returning callee at the same slot stays out --
`T&` into `T&&` is ill-formed once the tuple carries a reference-type
element."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical, _lower_ctx_witnessed,
    _thir_ctx,
)

_PREAMBLE = ("from tpy import Int32, Own, Ptr\n"
             "class Box:\n"
             "    n: Int32\n"
             "    def __init__(self, n: Int32) -> None:\n"
             "        self.n = n\n"
             "class Holder:\n"
             "    _b: Box\n"
             "    def __init__(self, b: Own[Box]) -> None:\n"
             "        self._b = b\n"
             "    def make(self) -> tuple[Own[Box], Int32]:\n"
             "        return (Box(1), 2)\n"
             "    def borrow(self) -> tuple[Box, Int32]:\n"
             "        return (self._b, 3)\n"
             "def wrap[T](v: Own[T]) -> Own[T]:\n"
             "    return v\n")


class TestOwnTupleCallRvalueAtGenericSlot:
    SRC = (_PREAMBLE
           + "def f(p: Ptr[Holder]) -> Int32:\n"
             "    q = wrap(p.make())\n"
             "    return q[1]\n"
             "def main() -> None:\n"
             "    h = Holder(Box(7))\n"
             "    print(f(h))\n"
             "main()\n")

    def test_routes_byte_identical(self):
        _, cpp = _assert_routes_byte_identical(self.SRC)
        assert ("wrap<std::tuple<Box, int32_t>>("
                "::tpy::deref_check(p).make())") in cpp

    def test_the_arg_row_and_the_result_gate_both_fire(self):
        _, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["arg.own_tuple_call_rvalue"] >= 1
        assert faces["method.qualcall.own_tuple_slot"] >= 1

    def test_borrow_returning_callee_keeps_rejecting(self):
        # BOUNDARY: a tuple return with no owned element is not the shape the
        # row names, and a borrow-returning callee is excluded a second time
        # by the row's rvalue test -- which folds the callee's declared return
        # in for every call node kind the row admits.
        src = (_PREAMBLE
               + "def f(p: Ptr[Holder]) -> Int32:\n"
                 "    q = wrap(p.borrow())\n"
                 "    return q[1]\n"
                 "def main() -> None:\n"
                 "    h = Holder(Box(7))\n"
                 "    print(f(h))\n"
                 "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           shape="method.qualcall.ret.tuple")
