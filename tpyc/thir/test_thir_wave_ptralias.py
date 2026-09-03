"""The pointer-local alias/reseat long-tail rows: a plain record
local/param NAME source at a reassigned pointer decl takes the PTR_ADDR
address-of (`Point* x = &(a);`), its reseat twins (`x = &(b);` over a
record param's `T&`; `result = &(pick(seed));` over a borrow-returning
call), and the unproven value-opt scalar FIELD operand's checked unwrap
(`deref_optional_check(local->value)`)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_POINT = (
    "from tpy import Int32\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestPtrAliasLongTail:
    def test_local_name_source_addr_routes(self):
        src = _POINT + (
            "def main() -> None:\n"
            "    a = Point(1)\n"
            "    b = Point(2)\n"
            "    x = a\n"
            "    print(x.x)\n"
            "    x = b\n"
            "    x.x = 9\n"
            "    print(a.x, b.x)\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["decl.ptr_name_addr"] >= 1
        cpp = _assert_byte_identical(src)
        assert "Point* x = &(a);" in cpp[1]

    def test_param_name_reseat_routes(self):
        src = _POINT + (
            "def f(a: Point, b: Point) -> None:\n"
            "    x = a\n"
            "    print(x.x)\n"
            "    x = b\n"
            "    x.x = 9\n"
            "    print(b.x)\n"
            "def main() -> None:\n"
            "    f(Point(1), Point(2))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["reseat.param_name"] >= 1
        cpp = _assert_byte_identical(src)
        assert "x = &(b);" in cpp[1]

    def test_borrow_call_reseat_routes(self):
        src = _POINT + (
            "def pick(p: Point) -> Point:\n"
            "    return p\n"
            "def f(seed: Point) -> None:\n"
            "    result = seed\n"
            "    result = pick(seed)\n"
            "    result.x = 5\n"
            "    print(seed.x)\n"
            "def main() -> None:\n"
            "    f(Point(1))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["reseat.borrow_call"] >= 1
        cpp = _assert_byte_identical(src)
        assert "result = &(pick(seed));" in cpp[1]

    def test_ptr_local_source_decl_stays_ast(self):
        # A source that is ITSELF a pointer local renders the bare pointer
        # copy on the AST path -- the &() row must not capture it.
        src = _POINT + (
            "def main() -> None:\n"
            "    a = Point(1)\n"
            "    b = Point(2)\n"
            "    x = a\n"
            "    x = b\n"
            "    y = x\n"
            "    y = a\n"
            "    y.x = 9\n"
            "    print(a.x, b.x)\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_value_call_reseat_keeps_rebind_slot(self):
        # A VALUE-returning call reseat addresses a temp if taken by the
        # &() row; it must keep the rebind-slot machinery (routes today).
        src = _POINT + (
            "from tpy import Own\n"
            "def make(n: Int32) -> Own[Point]:\n"
            "    return Point(n)\n"
            "def f(seed: Point) -> None:\n"
            "    result = seed\n"
            "    result = make(3)\n"
            "    print(result.x)\n"
            "def main() -> None:\n"
            "    f(Point(1))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "result = &(make(3));" not in cpp[1]


class TestPtrAliasBoundaries:
    def test_bytearray_ptr_local_slice_receiver_stays_ast(self):
        # A reassigned bytearray alias is a pointer local; the slice
        # receiver row excludes it (its spelling would need the deref).
        src = ("def main() -> None:\n"
               "    ba = bytearray(b\"0123456789\")\n"
               "    ba2 = bytearray(b\"abcdefghij\")\n"
               "    v = ba\n"
               "    v = ba2\n"
               "    s = v[1:3]\n"
               "    print(len(s))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_narrowed_union_source_alias_byte_identical(self):
        # A narrowed union member as the reassigned-alias source: the
        # spelled source stays off the bare &(name) row and the body
        # still renders byte-identically through its own arms.
        src = ("from tpy import Int32\n"
               "class A:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "class B:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def f(u: A | B) -> None:\n"
               "    if isinstance(u, A):\n"
               "        y = u\n"
               "        y = u\n"
               "        y.x = 9\n"
               "        print(y.x)\n"
               "def main() -> None:\n"
               "    f(A(1))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestUnprovenOptFieldOperand:
    _BOX = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    value: Int32 | None\n"
        "    def __init__(self, value: Int32 | None) -> None:\n"
        "        self.value = value\n"
    )

    def test_stale_narrow_field_operand_unwraps_checked(self):
        # The rebind invalidates the narrowing, so the arithmetic operand
        # takes the runtime-checked unwrap (the warned read).
        src = self._BOX + (
            "def f(a: Box, b: Box) -> Int32:\n"
            "    local = a\n"
            "    if local.value is not None:\n"
            "        local = b\n"
            "        return local.value + 1"
            "  # tpyc: warning(/Potential None access/)\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(f(Box(1), Box(2)))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["field.opt_deref_check_read"] >= 1
        cpp = _assert_byte_identical(src)
        assert ("::tpy::deref_optional_check(local->value)" in cpp[1])

    def test_proven_field_operand_keeps_narrowed_render(self):
        # A proven read never takes the checked unwrap; == operands keep
        # the bare-optional compare path.
        src = self._BOX + (
            "def f(b: Box) -> Int32:\n"
            "    if b.value is not None:\n"
            "        return b.value + 1\n"
            "    return 0\n"
            "def g(b: Box) -> bool:\n"
            "    return b.value == 7\n"
            "def main() -> None:\n"
            "    print(f(Box(1)), g(Box(7)))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None
        cpp = _assert_byte_identical(src)
        assert "deref_optional_check" not in cpp[1]
