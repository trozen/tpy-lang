"""Container FIELD writes whose source is a call: an `Own[container]`-returning
free call (the owned prvalue) and a borrow-returning method (the warned copy),
plus the borrow-returning free call that must not be mistaken for either."""

from __future__ import annotations

from .testutil import (
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_SRC = (
    "from tpy import Int32, Own\n"
    "def make_list(n: Int32) -> Own[list[Int32]]:\n"
    "    out: list[Int32] = []\n"
    "    for i in range(n):\n"
    "        out.append(i)\n"
    "    return out\n"
    "class Holder:\n"
    "    items: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.items = []\n"
    "    def peek(self) -> list[Int32]:\n"
    "        return self.items\n"
    "class Sink:\n"
    "    data: list[Int32]\n"
    "    mirror: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self.data = []\n"
    "        self.mirror = []\n"
    "    def fill_own(self, n: Int32) -> None:\n"
    "        self.data = make_list(n)\n"
    "    def fill_borrow(self, h: Holder) -> None:\n"
    "        self.mirror = h.peek()\n"
    "def main() -> None:\n"
    "    s = Sink()\n"
    "    s.fill_own(3)\n"
    "    h = Holder()\n"
    "    h.items.append(42)\n"
    "    s.fill_borrow(h)\n"
    "    print(len(s.data), len(s.mirror))\n"
    "main()\n"
)


class TestContainerFieldCallSources:
    def test_both_call_sources_route(self):
        thir, faces = _lower_ctx_witnessed(_SRC)
        assert _fn(thir, "make_list") is not None
        assert faces["field_write.container_free_call"] >= 1
        # The BORROW-returning call source shares the reference rvalue
        # row with the record half (`h.p = identity(pt);`), so it is
        # witnessed there.
        assert faces["field_write.record_rvalue"] >= 1

    def test_renders_both_bare(self):
        hpp = _assert_routes_byte_identical(_SRC)[0]
        assert "this->data = make_list(n);" in hpp
        assert "this->mirror = h.peek();" in hpp

    def test_borrow_free_call_takes_the_borrow_row_not_the_prvalue_row(self):
        # The adjacent shape: dropping `Own` from the return makes the same
        # call a BORROW source, which must land on the copy row (a warned
        # copy-assign) rather than the owned-prvalue row.
        src = _SRC.replace("def make_list(n: Int32) -> Own[list[Int32]]:\n"
                           "    out: list[Int32] = []\n"
                           "    for i in range(n):\n"
                           "        out.append(i)\n"
                           "    return out\n",
                           "def first_of(h2: Holder) -> list[Int32]:\n"
                           "    return h2.items\n").replace(
            "        self.data = make_list(n)\n",
            "        self.data = first_of(h3)\n").replace(
            "    def fill_own(self, n: Int32) -> None:\n",
            "    def fill_own(self, h3: Holder) -> None:\n").replace(
            "    s.fill_own(3)\n", "    s.fill_own(Holder())\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("field_write.container_free_call", 0) == 0
        assert faces["field_write.record_rvalue"] >= 1
