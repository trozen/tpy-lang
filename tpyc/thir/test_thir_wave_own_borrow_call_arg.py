"""A BORROW-returning CALL at a plain `Own[T]` slot.

A non-simple lvalue cannot bind the `T&&` slot, so the AST hoists
`auto __tmp_N = <call>;` and moves the temp -- the temp-free move arm needs a
NAME and never fires here. Corpus witness: tplib.box's `Box.clone`
(`Box(self.get())`)."""

from .testutil import (
    _reject_tally, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


def _reject_tags(src: str):
    return _reject_tally(src)


class TestOwnSlotBorrowCallArg:
    TYPE_PARAM = ("from tpy import Int32, Own, readonly, auto_readonly\n"
                  "class Cell[T]:\n"
                  "    v: T\n"
                  "    def __init__(self, v: Own[T]) -> None:\n"
                  "        self.v = v\n"
                  "    @auto_readonly\n"
                  "    def get(self) -> auto_readonly[T]:\n"
                  "        return self.v\n"
                  "    @readonly\n"
                  "    def dup(self) -> Own[Cell[T]]:\n"
                  "        return Cell(self.get())\n"
                  "def main() -> None:\n"
                  "    c = Cell(3)\n"
                  "    print(c.dup().get())\n"
                  "main()\n")

    RECORD = ("from tpy import Int32, Own\n"
              "class Item:\n"
              "    k: Int32\n"
              "    def __init__(self, k: Int32) -> None:\n"
              "        self.k = k\n"
              "class Holder:\n"
              "    it: Item\n"
              "    def __init__(self, it: Own[Item]) -> None:\n"
              "        self.it = it\n"
              "    def peek(self) -> Item:\n"
              "        return self.it\n"
              "def collect(h: Holder, out: list[Item]) -> None:\n"
              "    out.append(h.peek())\n"
              "def main() -> None:\n"
              "    h = Holder(Item(5))\n"
              "    out: list[Item] = []\n"
              "    collect(h, out)\n"
              "    out[0].k = 7\n"
              "    print(out[0].k)\n"
              "main()\n")

    def test_type_param_payload_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.TYPE_PARAM)
        assert witnesses.get("argtemp.own_borrow_call", 0) >= 1
        _assert_routes_byte_identical(self.TYPE_PARAM)

    def test_record_payload_routes(self):
        # The record-payload leg admits the container-element slot; without
        # it this body falls back at the element arg shape.
        _assert_routes_byte_identical(self.RECORD)


class TestOwnSlotBorrowCallArgBoundary:
    def test_rvalue_returning_call_keeps_rejecting(self):
        # A generic `-> T` return is by value: an rvalue that binds the
        # `T&&` slot directly, so the AST hoists no temp.
        src = ("from tpy import Int32, Own\n"
               "class Cell[T]:\n"
               "    v: T\n"
               "    def __init__(self, v: Own[T]) -> None:\n"
               "        self.v = v\n"
               "    def get(self) -> T:\n"
               "        return self.v\n"
               "    def dup(self) -> Own[Cell[T]]:\n"
               "        return Cell(self.get())\n"
               "def main() -> None:\n"
               "    c = Cell(3)\n"
               "    print(c.dup().get())\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_generic")

    def test_str_payload_routes_through_the_owned_slot(self):
        # A str payload's temp is a view->owned CONVERSION with its own
        # spelling, not the `auto` copy this row renders.
        src = ("from tpy import Own\n"
               "class Sack:\n"
               "    s: str\n"
               "    def __init__(self, s: Own[str]) -> None:\n"
               "        self.s = s\n"
               "    def label(self) -> str:\n"
               "        return self.s\n"
               "def dup(x: Sack) -> Own[Sack]:\n"
               "    return Sack(x.label())\n"
               "def main() -> None:\n"
               "    print(dup(Sack('hi')).s)\n"
               "main()\n")
        # The Own[str] ctor slot has its own owned-copy row now, so this
        # position routes rather than landing on the borrow-arg boundary.
        assert not _reject_tags(src)

    def test_container_payload_keeps_rejecting(self):
        src = ("from tpy import Int32, Own\n"
               "class Bag:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self, xs: Own[list[Int32]]) -> None:\n"
               "        self.xs = xs\n"
               "    def items(self) -> list[Int32]:\n"
               "        return self.xs\n"
               "def dup(b: Bag) -> Own[Bag]:\n"
               "    return Bag(b.items())\n"
               "def main() -> None:\n"
               "    b = Bag([1, 2])\n"
               "    print(len(dup(b).xs))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_container")

    def test_match_guard_position_keeps_rejecting(self):
        src = ("from tpy import Int32, Own, auto_readonly\n"
               "class Item:\n"
               "    k: Int32\n"
               "    def __init__(self, k: Int32) -> None:\n"
               "        self.k = k\n"
               "class Holder:\n"
               "    it: Item\n"
               "    def __init__(self, it: Own[Item]) -> None:\n"
               "        self.it = it\n"
               "    @auto_readonly\n"
               "    def peek(self) -> auto_readonly[Item]:\n"
               "        return self.it\n"
               "def dup(h: Holder, k: Int32) -> Int32:\n"
               "    match k:\n"
               "        case n if Holder(h.peek()).it.k > 0:\n"
               "            return n\n"
               "        case _:\n"
               "            return 0\n"
               "def main() -> None:\n"
               "    h = Holder(Item(5))\n"
               "    print(dup(h, 1))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_record_f1")
