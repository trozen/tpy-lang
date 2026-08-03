"""The generator-iterable tuple unpack's const-ref VALUE element: a
`const T& i = std::get<0>(...)` bind renders identically off borrow and
storage tuples (value elements are stored by value in both), so the
fresh-const-ref fence defers only NON-value targets."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _fn, _assert_byte_identical,
)

_G = (
    "from tpy import Int32\n"
    "from typing import Iterator\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32) -> None:\n"
    "        self.val = val\n"
    "def g(items: list[Box]) -> Iterator[tuple[int, Box]]:\n"
    "    i = 0\n"
    "    for b in items:\n"
    "        yield (i, b)\n"
    "        i = i + 1\n"
)


class TestGenIterConstRefValueElem:
    def test_const_ref_value_target_routes(self):
        src = _G + (
            "def main() -> None:\n"
            "    data = [Box(1), Box(2), Box(3)]\n"
            "    for i, b in g(data):\n"
            "        b.val = b.val + 100\n"
            "    for box in data:\n"
            "        print(box.val)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "const ::tpy::BigInt& i = std::get<0>(__tup_1);" in cpp[1]
        assert ("auto&& b = ::tpy::unwrap_ref("
                "::tpy::tuple_elem_ref(std::get<1>(__tup_1)));" in cpp[1])

    def test_read_only_record_target_byte_identical(self):
        # A read-only record target rides the REF capture -- routes and
        # stays byte-identical (the adjacent shape the fence guarded).
        src = _G + (
            "def main() -> None:\n"
            "    data = [Box(1), Box(2)]\n"
            "    total = 0\n"
            "    for i, b in g(data):\n"
            "        total = total + b.val + i\n"
            "    print(total)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)
