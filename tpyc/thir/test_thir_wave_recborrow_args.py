"""Pins for the record-borrow arg rows: T&-returning calls and checked
record-element subscripts bound inline at record ref slots, and the
record-getitem `auto` temp at structural protocol slots -- plus the
rvalue-call temp boundary."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_REC = (
    "from tpy import Int32, Own\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def bump(p: Point) -> None:\n"
    "    p.x += 10\n"
)


class TestRecordBorrowArgs:
    def test_borrow_call_arg_inlines(self):
        src = _REC + (
            "def find_first(pts: list[Point]) -> Point:\n"
            "    return pts[0]\n"
            "def main() -> None:\n"
            "    pts = [Point(1)]\n"
            "    bump(find_first(pts))\n"
            "    print(pts[0].x)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["arg.record_borrow_call"] >= 1
        _assert_byte_identical(src)

    def test_rvalue_call_arg_still_temps(self):
        # An Own-returning (prvalue) call at the mutable ref slot keeps the
        # typed ArgTemp -- the inline row is borrow-results only.
        src = _REC + (
            "def make_point(x: Int32) -> Own[Point]:\n"
            "    return Point(x)\n"
            "def main() -> None:\n"
            "    bump(make_point(5))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert not faces.get("arg.record_borrow_call")
        _assert_byte_identical(src)

    def test_record_elem_subscript_arg_inlines(self):
        src = _REC + (
            "def main() -> None:\n"
            "    pts = [Point(1), Point(2)]\n"
            "    bump(pts[1])\n"
            "    print(pts[1].x)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["subscript.record_elem_borrow"] >= 1
        _assert_byte_identical(src)

    def test_record_getitem_protocol_temp(self):
        src = (
            "from typing import Protocol\n"
            "from tpy import Int32\n"
            "class HasValue(Protocol):\n"
            "    def get(self) -> Int32: ...\n"
            "class IntBox:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    def get(self) -> Int32:\n"
            "        return self.v\n"
            "class BoxContainer:\n"
            "    items: list[IntBox]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = [IntBox(1), IntBox(2)]\n"
            "    def __getitem__(self, i: Int32) -> IntBox:\n"
            "        return self.items[i]\n"
            "def show(h: HasValue) -> None:\n"
            "    print(h.get())\n"
            "def main() -> None:\n"
            "    container = BoxContainer()\n"
            "    show(container[1])\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)
