"""Pending-deferred-generic method calls: a still-pending receiver's call
carries sema's minimal fi (raw method params, method-kind bits preserved),
and a scalar value into its bare `T` slot renders bare on both paths
(`method.tparam_scalar_arg`). Record / str args into a pending `T` slot
keep rejecting -- they carry borrow/move or view/owned questions the
scalar row does not answer."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_PAIR_HDR = (
    "class Pair[T, U]:\n"
    "    a: T\n"
    "    b: U\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def set_a(self, val: T) -> None:\n"
    "        self.a = val\n"
    "    def set_b(self, val: U) -> None:\n"
    "        self.b = val\n"
    "    def get_a(self) -> T:\n"
    "        return self.a\n"
)


class TestPendingGenericMethodArgs:
    def test_pending_scalar_arg_routes(self):
        # `p.set_a(Int32(10))` while U is still unknown: the minimal fi's
        # raw `T` slot takes the scalar bare -- the ctor-coercion call
        # folds to the literal on both paths.
        src = ("from tpy import Int32, Int64\n"
               + _PAIR_HDR +
               "def main() -> None:\n"
               "    p = Pair()\n"
               "    p.set_a(Int32(10))\n"
               "    p.set_b(Int64(20))\n"
               "    print(p.get_a())\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("method.tparam_scalar_arg", 0) >= 1
        cpp = _assert_routes_byte_identical(src)
        assert "p.set_a(10);" in cpp[1]

    def test_pending_record_arg_stays_ast(self):
        # A RECORD into the pending `T` slot: borrow/move questions the
        # scalar row does not answer -- the body must keep falling back.
        src = ("from tpy import Int32, Int64\n"
               "class Rec:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               + _PAIR_HDR +
               "def main() -> None:\n"
               "    p = Pair()\n"
               "    r = Rec(5)\n"
               "    p.set_a(r)\n"
               "    p.set_b(Int64(2))\n"
               "    print(p.get_a().x)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.arg_shape")

    def test_pending_str_arg_stays_ast(self):
        # A str into the pending `T` slot: the view/owned split stays out
        # of the scalar row.
        src = ("from tpy import Int64\n"
               + _PAIR_HDR +
               "def main() -> None:\n"
               "    p = Pair()\n"
               "    p.set_a(\"hello\")\n"
               "    p.set_b(Int64(2))\n"
               "    print(p.get_a())\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.arg_shape")
