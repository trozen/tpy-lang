"""A BORROW-returning method call at a module-scope global slot: the slot
points AT the callee-owned storage (`g = &(c->get());`), no slot allocated."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at, _assert_byte_identical,
                      _reject_tally)

_SRC = (
    "from tpy import Int32, Own\n"
    "class Inner:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Holder:\n"
    "    v: Inner\n"
    "    def __init__(self, v: Inner) -> None:\n"
    "        self.v = v\n"
    "    def get(self) -> Inner:\n"
    "        return self.v\n"
    "h = Holder(Inner(1))\n"
)


class TestGlobalAddrOfBorrowCall:
    def test_borrow_call_source_takes_address_of(self):
        src = _SRC + "g: Inner = h.get()\nprint(g.n)\n"
        # `_lower_ctx` calls `lower_module` without the generator's global
        # map, so `top_level` is always None here -- ROUTING proof for this
        # shape is the flipped corpus case `array_span/uninit_array_storage`
        # (unmarked => the ratchet fails if its module init falls back).
        # Byte identity is what this pin adds.
        _assert_byte_identical(src)


class TestGlobalAddrBoundaries:
    def test_rvalue_call_still_allocates_a_slot(self):
        # An RVALUE-returning call is NOT this row -- it takes the
        # `static __global_slot_N` materializing path, which is a different
        # render and must not be captured by the address-of arm.
        src = (_SRC
               + "def make() -> Own[Inner]:\n    return Inner(7)\n"
               + "g: Inner = make()\nprint(g.n)\n")
        _assert_byte_identical(src)

    def test_subclass_borrow_stays_ast(self):
        # A borrow whose type differs from the slot retypes it (the
        # polymorphic arm's business), so the exact-type pin must hold.
        src = ("from tpy import Int32\n"
               "class Base:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Sub(Base):\n"
               "    def __init__(self) -> None:\n"
               "        Base.__init__(self, 1)\n"
               "class H:\n"
               "    s: Sub\n"
               "    def __init__(self, s: Sub) -> None:\n"
               "        self.s = s\n"
               "    def get(self) -> Sub:\n"
               "        return self.s\n"
               "h = H(Sub())\n"
               "g: Base = h.get()\n"
               "print(g.n)\n")
        _assert_rejects_at(_reject_tally(src),
                           "top_level:stmt.var_decl:top_level.global_slot_shape")
