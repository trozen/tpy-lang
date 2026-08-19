"""`h.value = maybe_make(3)` at `-> Own[T] | None` into a `T | None` field.

The `Own` inner makes the VALUE a value-repr `std::optional<T>` while the
FIELD is pointer-repr, so the same-repr leg of the optional-field-write
predicate cannot see the pair -- yet the AST assigns it BARE. Two halves,
one shared predicate: the admission, and the tail's suppression of the
`ptr_to_optional` lift the field's repr would otherwise imply (that lift
takes a `T*` and would be handed a `std::optional<T>` -- ill-formed C++,
which the byte-diff would report but a reader could mistake for cosmetic).
"""
from __future__ import annotations

PRE = (
    "from tpy import Int32, Own\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32):\n"
    "        self.x = x\n"
    "        self.y = y\n"
    "class Holder:\n"
    "    value: Point | None\n"
    "    def __init__(self) -> None:\n"
    "        self.value = None\n"
    "def maybe_make(x: Int32) -> Own[Point] | None:\n"
    "    if x > 0:\n"
    "        return Point(x, x)\n"
    "    return None\n")

FREE_CALL = PRE + (
    "def test() -> None:\n"
    "    h = Holder()\n"
    "    h.value = maybe_make(3)\n"
    "    print(h.value is None)\n"
    "test()\n")

SELF_FIELD = PRE + (
    "class Wrap:\n"
    "    value: Point | None\n"
    "    def __init__(self) -> None:\n"
    "        self.value = None\n"
    "    def set(self, n: Int32) -> None:\n"
    "        self.value = maybe_make(n)\n"
    "def test() -> None:\n"
    "    w = Wrap()\n"
    "    w.set(3)\n"
    "    print(w.value is None)\n"
    "test()\n")

METHOD_CALL = PRE + (
    "class Factory:\n"
    "    seed: Int32\n"
    "    def __init__(self, seed: Int32) -> None:\n"
    "        self.seed = seed\n"
    "    def maybe(self) -> Own[Point] | None:\n"
    "        if self.seed > 0:\n"
    "            return Point(self.seed, self.seed)\n"
    "        return None\n"
    "def test() -> None:\n"
    "    h = Holder()\n"
    "    f = Factory(2)\n"
    "    h.value = f.maybe()\n"
    "    print(h.value is None)\n"
    "test()\n")

BORROW_CALL = PRE + (
    "def find(pts: list[Point], n: Int32) -> Point | None:\n"
    "    for p in pts:\n"
    "        if p.x == n:\n"
    "            return p\n"
    "    return None\n"
    "def test() -> None:\n"
    "    h = Holder()\n"
    "    pts = [Point(1, 1)]\n"
    "    h.value = find(pts, 1)\n"
    "    print(h.value is None)\n"
    "test()\n")

PARAM_NAME = PRE + (
    "def put(h: Holder, p: Own[Point] | None) -> None:\n"
    "    h.value = p\n"
    "def test() -> None:\n"
    "    h = Holder()\n"
    "    put(h, maybe_make(3))\n"
    "    print(h.value is None)\n"
    "test()\n")

NESTED_RECV = PRE + (
    "class Outer:\n"
    "    h: Holder\n"
    "    def __init__(self) -> None:\n"
    "        self.h = Holder()\n"
    "def test() -> None:\n"
    "    o = Outer()\n"
    "    o.h.value = maybe_make(3)\n"
    "    print(o.h.value is None)\n"
    "test()\n")


def _collect(source: str):
    from .testutil import _thir_ctx_witnessed
    _ctx, wit, fb = _thir_ctx_witnessed(source)
    return wit, fb


class TestOwnedOptionalCallFieldWrite:

    def test_free_call_routes_and_assigns_bare(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(FREE_CALL)
        assert "h.value = maybe_make(3);" in cpp
        assert "ptr_to_optional" not in cpp

    def test_self_field_target_routes_and_assigns_bare(self):
        from .testutil import _assert_routes_byte_identical
        hpp, cpp = _assert_routes_byte_identical(SELF_FIELD)
        assert "this->value = maybe_make(n);" in hpp + cpp
        assert "ptr_to_optional" not in hpp + cpp

    def test_method_call_source_routes_and_assigns_bare(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(METHOD_CALL)
        assert "h.value = f.maybe();" in cpp
        assert "ptr_to_optional" not in cpp

    def test_free_call_witnesses_its_face(self):
        wit, fb = _collect(FREE_CALL)
        assert not fb, fb
        assert wit.get("field_write.owned_opt_call", 0) == 1


class TestBorrowOptionalCallKeepsTheLift:
    """BOUNDARY: a BORROW `T | None` call returns a `T*`, so it still takes
    `ptr_to_optional`. This is the shape the bare arm must never eat."""

    def test_borrow_optional_call_still_lifts(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(BORROW_CALL)
        assert "h.value = ::tpy::ptr_to_optional(find(pts, 1));" in cpp


class TestOwnedOptionalResidues:
    """Both halves of the AST's arm the row deliberately does NOT mirror --
    conservative, and pinned so a later widening is a visible change."""

    def test_own_optional_param_name_source_still_rejects(self):
        # The AST extends `is_owned_optional` to an `Own[...]`-typed param
        # NAME; the row is call-only.
        _wit, fb = _collect(PARAM_NAME)
        assert any(k.endswith("expr.call") for k in fb), fb

    def test_nested_receiver_still_rejects(self):
        # `_field_receiver_ok` declines the chain one rung before the row.
        _wit, fb = _collect(NESTED_RECV)
        assert any(k.endswith("assign.field_write_shape") for k in fb), fb
