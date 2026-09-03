"""The REF_ALIAS decl off a GENERIC record's borrow-returning `__getitem__`
(`pt = lst[i]` on `ArrayList[Point, N]`), whose declared return is a
reference to a bare type param, plus the reassigned sibling that must keep
rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
    _reject_tally,
)

_SRC = (
    "from tpy import Int32\n"
    "from tplib import ArrayList\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def show(lst: ArrayList[Point, 8]) -> None:\n"
    "    for i in range(len(lst)):\n"
    "        pt = lst[i]\n"
    "        print(pt.x)\n"
    "def bump(lst: ArrayList[Point, 8]) -> None:\n"
    "    p = lst[0]\n"
    "    p.x = 99\n"
    "def main() -> None:\n"
    "    lst = ArrayList[Point, 8]()\n"
    "    lst.append(Point(1))\n"
    "    show(lst)\n"
    "    bump(lst)\n"
    "    show(lst)\n"
    "main()\n"
)


class TestGenericRecordGetitemAlias:
    def test_routes_both_alias_decls(self):
        thir, faces = _lower_ctx_witnessed(_SRC)
        assert _fn(thir, "show") is not None
        assert _fn(thir, "bump") is not None
        assert faces["subscript.record_getitem"] >= 2

    def test_renders_the_reference_bind(self):
        cpp = _assert_routes_byte_identical(_SRC)[1]
        # The receiver's constness propagates: `show` never writes through
        # its alias, so sema infers a readonly param and the alias binds
        # `const Point&`.
        assert "const Point& pt = lst[i];" in cpp
        assert "Point& p = lst[0];" in cpp

    def test_reassigned_alias_keeps_rejecting(self):
        # The POINTER sibling needs the `&(e[k])` reseat lift, which no arm
        # spells for a record `__getitem__`.
        src = _SRC.replace(
            "    p = lst[0]\n    p.x = 99\n",
            "    p = lst[0]\n    p.x = 99\n    p = lst[0]\n    p.x = 98\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.var_decl",
                           shape="decl.slot_type")
