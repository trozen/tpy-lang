"""Return-slot wave arms: the structural-protocol / native-iterator
return slots, the container-FIELD arg into a structural protocol slot,
and the bytearray container-return families."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _fn, _assert_byte_identical,
)


class TestProtocolReturn:
    def test_iter_protocol_return_routes(self):
        # `def __iter__ -> Iterator[T]: return iter(self.items)` -- the
        # protocol return slot + the container-field arg render bare.
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "class Bag:\n"
               "    items: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = [1, 2, 3]\n"
               "    def __iter__(self) -> Iterator[Int32]:\n"
               "        return iter(self.items)\n"
               "def main() -> None:\n"
               "    for x in Bag():\n"
               "        print(x)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "__iter__") is not None
        cpp = _assert_byte_identical(src)
        assert "return ::tpy::__iter__(this->items);" in (cpp[0] + cpp[1])

    def test_spaniter_return_routes(self):
        # `def __iter__ -> SpanIter[readonly[T]]: return SpanIter(...)` --
        # the native-iterator return slot (single-overload __span__, no
        # auto_readonly fence): the ctor's pre-substituted template renders
        # bare at the return.
        src = ("from tpy import Int32, Span, SpanIter\n"
               "from tpy import readonly\n"
               "class Buf:\n"
               "    _data: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = [7, 8]\n"
               "    def __span__(self) -> Span[readonly[Int32]]:\n"
               "        return self._data\n"
               "    def __iter__(self) -> SpanIter[readonly[Int32]]:\n"
               "        return SpanIter(self.__span__())\n"
               "def main() -> None:\n"
               "    b = Buf()\n"
               "    for x in b:\n"
               "        print(x)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "__iter__") is not None
        cpp = _assert_byte_identical(src)
        assert ("return ::tpy::SpanIter<const int32_t>"
                "(this->__span__());" in cpp[0] + cpp[1])

    def test_auto_readonly_span_recv_routes(self):
        # RE-PINNED ROUTED (retslot track): the @auto_readonly `__span__`
        # clone PAIR was already admitted at the overload gate; the Span
        # RESULT row was the only blocker, so
        # `return SpanIter(self.__span__())` now routes byte-identically.
        src = ("from tpy import Int32, Span, SpanIter\n"
               "from tpy import auto_readonly\n"
               "class Buf:\n"
               "    _data: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = [10, 20]\n"
               "    @auto_readonly\n"
               "    def __span__(self) -> Span[auto_readonly[Int32]]:\n"
               "        return self._data\n"
               "    @auto_readonly\n"
               "    def __iter__(self) -> SpanIter[auto_readonly[Int32]]:\n"
               "        return SpanIter(self.__span__())\n"
               "def main() -> None:\n"
               "    b = Buf()\n"
               "    for x in b:\n"
               "        print(x)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "__iter__") is not None
        _assert_byte_identical(src)


class TestBytearrayReturn:
    def test_own_and_borrow_returns_route(self):
        # `-> Own[bytearray]` returns the fresh buffer by value; a
        # bytearray PARAM returns by reference -- both bare renders.
        src = ("from tpy import Own\n"
               "def make() -> Own[bytearray]:\n"
               "    return bytearray(b\"hi\")\n"
               "def first_param(b: bytearray) -> bytearray:\n"
               "    return b\n"
               "def main() -> None:\n"
               "    ba = make()\n"
               "    print(len(first_param(ba)))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "make") is not None
        assert _fn(thir, "first_param") is not None
        cpp = _assert_byte_identical(src)
        assert ("return ::tpy::bytes_copy(::tpy::bytes_literal(\"hi\", 2));"
                in cpp[1])

    def test_field_source_borrow_return_routes(self):
        # `return self.buf` at a borrow bytearray return rides the
        # element-blind container field-source arm (bare member render).
        src = ("class H:\n"
               "    buf: bytearray\n"
               "    def __init__(self) -> None:\n"
               "        self.buf = bytearray(b\"abc\")\n"
               "    def view(self) -> bytearray:\n"
               "        return self.buf\n"
               "def main() -> None:\n"
               "    h = H()\n"
               "    v = h.view()\n"
               "    v.append(33)\n"
               "    print(len(h.buf))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "ctor:ctor.mil_field.nominal.native_call")

    def test_own_name_source_return_stays_ast(self):
        # `return ba` (a NAME source) at the Own[bytearray] storage return
        # is the un-routed container-source rung -- keeps falling back.
        src = ("from tpy import Own\n"
               "def fresh() -> Own[bytearray]:\n"
               "    ba = bytearray(b\"xy\")\n"
               "    ba.append(48)\n"
               "    return ba\n"
               "def main() -> None:\n"
               "    print(len(fresh()))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.container_source")
