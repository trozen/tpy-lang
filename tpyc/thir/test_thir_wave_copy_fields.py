"""`copy()` sources at record / Optional[record] field writes, and the
pointer-repr-Optional identity peel that makes `copy(x)` render as `x`."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_POINT = (
    "from tpy import Int32, copy\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n        self.y = y\n"
)


class TestCopyAtRecordField:
    _SRC = (_POINT
            + "class Holder:\n"
            + "    p: Point\n"
            + "    def __init__(self, p: Point) -> None:\n"
            + "        self.p = copy(p)\n")

    def test_name_copy_construct_routes(self):
        # `copy(name)` is the copy-CONSTRUCT rvalue `Point(p)`, not a peel.
        src = (self._SRC
               + "def use(h: Holder, q: Point) -> None:\n"
               + "    h.p = copy(q)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field_write.record_copy", 0) >= 1
        _assert_byte_identical(src)

    def test_ctor_arg_peels(self):
        # `copy(T(...))` renders the constructor's own prvalue UNCHANGED.
        src = (self._SRC
               + "def use(h: Holder) -> None:\n"
               + "    h.p = copy(Point(3, 4))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field_write.record_copy_ctor", 0) >= 1
        _assert_byte_identical(src)


class TestCopyAtOptionalRecordField:
    _SRC = (_POINT
            + "class Box:\n"
            + "    item: Point | None\n"
            + "    def __init__(self) -> None:\n"
            + "        self.item = None\n"
            + "def find(ps: list[Point]) -> Point | None:\n"
            + "    for p in ps:\n"
            + "        return p\n"
            + "    return None\n")

    def test_inner_name_and_ctor_route(self):
        src = (self._SRC
               + "def use(b: Box, q: Point) -> None:\n"
               + "    b.item = copy(q)\n"
               + "    b.item = copy(Point(1, 2))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field_write.optrec_copy", 0) >= 1
        assert w.get("field_write.optrec_copy_ctor", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_call_source_lifts(self):
        # A BORROW-returning ptr-repr Optional call lifts through
        # `ptr_to_optional` -- bare and `copy()`-wrapped render the same.
        src = (self._SRC
               + "def use(b: Box, ps: list[Point]) -> None:\n"
               + "    b.item = find(ps)\n"
               + "    b.item = copy(find(ps))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("call.ptr_opt_lift", 0) >= 2
        _assert_byte_identical(src)

    def test_field_source_copies_bare(self):
        # A FIELD read of the same Optional is already STORAGE, so it copies
        # bare -- the tail picks lift-vs-bare off the lowered form.
        src = (self._SRC
               + "def use(b: Box, c: Box) -> None:\n"
               + "    b.item = c.item\n"
               + "    b.item = copy(c.item)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestCopyFieldBoundaries:
    def test_container_copy_stays_ast(self):
        # `copy(container)` is the copy-CONSTRUCT arm too
        # (`std::vector<int32_t>(data)`), but its field-write row is not
        # built -- the record-only admission must not leak to it.
        src = ("from tpy import Int32, copy\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = []\n"
               "    def set(self, data: list[Int32]) -> None:\n"
               "        self.items = copy(data)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "set") is None

    def test_subclass_rvalue_copy_stays_ast(self):
        # The exact-type check keeps a SLICING copy out: `copy(Sub(...))`
        # into a `Base` field would silently slice.
        src = (_POINT
               + "class Sub(Point):\n"
               + "    z: Int32\n"
               + "    def __init__(self) -> None:\n"
               + "        Point.__init__(self, 1, 2)\n"
               + "        self.z = 3\n"
               + "class Holder:\n"
               + "    p: Point\n"
               + "    def __init__(self, p: Point) -> None:\n"
               + "        self.p = copy(p)\n"
               + "def use(h: Holder) -> None:\n"
               + "    h.p = copy(Sub())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
