"""The `Ptr[T]` slot of a ctor nested in a flush-less position.

The nested ctor family admits only renders that need no hoisted temp. A
by-value pointer slot never fires the ownership cascade, so every pointer
source binds it in place -- the same leg the direct family reaches inside
its pass-through bundle, taken alone because the rest of that bundle is not
temp-free."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _reject_tally, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)

_PRELUDE = (
    "from tpy import Ptr, Int32, Own, take_ptr\n"
    "from tplib.rc import Rc\n"
    "class Sess:\n"
    "    n: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.n = 0\n"
)


class TestNestedCtorPtrSlot:
    def test_ptr_name_at_a_nested_ctor_slot_routes(self):
        # `Holder(s)` sits under a method-call argument under a ctor
        # argument, so it carries no flush right of its own.
        src = (_PRELUDE
               + "class Holder:\n"
               + "    p: Ptr[Sess]\n"
               + "    def __init__(self, s: Ptr[Sess]) -> None:\n"
               + "        self.p = s\n"
               + "class Sock:\n"
               + "    inner: Rc[Holder]\n"
               + "    def __init__(self, inner: Own[Rc[Holder]]) -> None:\n"
               + "        self.inner = inner\n"
               + "def build(s: Ptr[Sess]) -> Own[Sock]:\n"
               + "    return Sock(Rc.new(Holder(s)))\n"
               + "def main() -> None:\n"
               + "    s = Sess()\n"
               + "    w = build(take_ptr(s))\n"
               + "    print(1)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "Sock(Rc<Holder>::new_<Holder>(Holder(s)))" in cpp

    def test_container_name_at_the_same_position_stays_ast(self):
        # BOUNDARY: the pointer leg is taken alone. A container NAME at the
        # same flush-less nested slot is the direct family's pass-through
        # bundle too, and it keeps rejecting here.
        src = (_PRELUDE
               + "class Holder:\n"
               + "    xs: list[Int32]\n"
               + "    def __init__(self, xs: list[Int32]) -> None:\n"
               + "        self.xs = xs\n"
               + "class Sock:\n"
               + "    inner: Rc[Holder]\n"
               + "    def __init__(self, inner: Own[Rc[Holder]]) -> None:\n"
               + "        self.inner = inner\n"
               + "def build(xs: list[Int32]) -> Own[Sock]:\n"
               + "    return Sock(Rc.new(Holder(xs)))\n"
               + "def main() -> None:\n"
               + "    w = build([1, 2])\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src), 'body:expr.call', 'call.ctor_arg.container')
