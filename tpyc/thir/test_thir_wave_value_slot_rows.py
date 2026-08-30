"""Pins for three admissions that all turn on a slot being a BY-VALUE C++
object, so its render carries no borrow/storage duality to get wrong:

  - an operator rvalue at a VALUE-record return slot (`return -self` on a
    `@dataclass(frozen=True)` ValueType), which renders like the STORAGE
    row even though the slot is spelled as a bare `-> T`;
  - a same-VALUE-union NON-NAME argument (a property-getter read at a
    `std::variant<...>` slot), where the already-union source has exactly
    one AST render -- the default argument tail;
  - a VALUE-union property getter at ANY result position, the
    position-blind sibling of the pointer-variant getter's receiver-only
    row.

The two union rows share one boundary: at a POINTER variant the
already-union renders branch on the position and on the source being an
un-narrowed name, so neither may reach it. The record row's boundary is
sema's, not THIR's -- a reference record's bare `-> T` slot binds `T&`,
and an operator result there is rejected as a dangling reference.

A fourth, unrelated row rides along because it shares the "the DECLARED
type, not the value category, settles ownership" reasoning: an
Own-returning @native call at an `Own[record]` slot.
"""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from .testutil import (_assert_rejects_at, _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)

# A frozen ValueType record: `-> Val` is spelled like a borrow return but the
# record is returned BY VALUE.
_VAL = (
    "from dataclasses import dataclass\n"
    "from tpy import Int32, Own, ValueType\n"
    "@dataclass(frozen=True)\n"
    "class Val(ValueType):\n"
    "    x: Int32\n"
    "    def __add__(self, o: Val) -> Val:\n"
    "        return Val(self.x + o.x)\n"
    "    def __neg__(self) -> Val:\n"
    "        return Val(-self.x)\n"
)


class TestValueRecordOperatorReturn:
    def test_unary_and_binop_return_bare(self):
        src = _VAL + (
            "    def flip(self) -> Val:\n"
            "        return -self\n"
            "    def plus(self, o: Val) -> Val:\n"
            "        return self + o\n"
            "def use() -> None:\n"
            "    print(Val(3).flip().x)\n"
            "    print(Val(3).plus(Val(4)).x)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "return -((*this));" in out
        assert "return (((*this)) + (o));" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_op_value"] == 2

    def test_own_slot_keeps_the_storage_face(self):
        # SEPARATION: the same source at an `Own[Val]` slot is the STORAGE
        # row. The two renders coincide here -- which is exactly why the
        # faces must stay apart, or a later divergence at one slot would be
        # invisible at the other.
        src = _VAL + (
            "    def flip(self) -> Own[Val]:\n"
            "        return -self\n"
            "def use() -> None:\n"
            "    print(Val(3).flip().x)\n"
            "use()\n"
        )
        _assert_routes_byte_identical(src)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.record_op_storage"] == 1
        assert "ret.record_op_value" not in faces

    def test_non_value_record_operator_return_has_no_source_form(self):
        # BOUNDARY: the arm's value-ness guard is not reachable from the
        # other side -- a REFERENCE record's bare `-> T` slot binds `T&`,
        # and sema rejects any operator result there as a dangling
        # reference whatever the dunder's return convention. So the guard
        # can only ever loosen by a sema widening, never by a THIR edit.
        src = (
            "from tpy import Int32, Own\n"
            "class Ref:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __add__(self, o: Ref) -> Own[Ref]:\n"
            "        return Ref(self.n + o.n)\n"
            "    def combine(self, o: Ref) -> Ref:\n"
            "        return self + o\n"
            "def use() -> None:\n"
            "    print(Ref(1).combine(Ref(2)).n)\n"
            "use()\n"
        )
        with pytest.raises(SemanticError, match="returned by reference"):
            _thir_ctx(src)


# A VALUE union (`std::variant<int32_t, std::string>`) behind a property
# getter, plus a plain method returning the same union.
_CELL = (
    "from tpy import Int32\n"
    "def take(u: Int32 | str) -> Int32:\n"
    "    if isinstance(u, Int32):\n"
    "        return u\n"
    "    return len(u)\n"
    "class Cell:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    @property\n"
    "    def peek(self) -> Int32 | str:\n"
    "        if self.n == 0:\n"
    "            return \"zero\"\n"
    "        return self.n\n"
    "    def get(self) -> Int32 | str:\n"
    "        if self.n == 0:\n"
    "            return \"zero\"\n"
    "        return self.n\n"
    "    def absorb(self, u: Int32 | str) -> Int32:\n"
    "        return take(u)\n"
    "class Sink:\n"
    "    m: Int32\n"
    "    def __init__(self, u: Int32 | str) -> None:\n"
    "        self.m = take(u)\n"
)


class TestValueUnionPropertyGetter:
    def test_getter_routes_at_every_result_position(self):
        # The ctor arg is the shape that pays; the rest prove the admission
        # really is position-blind rather than one sink's special case.
        src = _CELL + (
            "def ctor_arg(c: Cell) -> Int32:\n"
            "    return Sink(c.peek).m\n"
            "def decl_slot(c: Cell) -> Int32:\n"
            "    u = c.peek\n"
            "    return take(u)\n"
            "def arg_slot(c: Cell) -> Int32:\n"
            "    return take(c.peek)\n"
            "def ret_slot(c: Cell) -> Int32 | str:\n"
            "    return c.peek\n"
            "def cmp_slot(c: Cell, d: Cell) -> bool:\n"
            "    return c.peek == d.peek\n"
            "def use(c: Cell) -> None:\n"
            "    print(ctor_arg(c), decl_slot(c), arg_slot(c))\n"
            "    r = ret_slot(c)\n"
            "    print(take(r), cmp_slot(c, c))\n"
            "use(Cell(3))\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "return Sink(c.peek()).m;" in out
        assert "std::variant<int32_t, std::string> u = c.peek();" in out
        assert "return take(c.peek());" in out
        assert "return c.peek();" in out
        assert "return (c.peek() == d.peek());" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["method.value_union_property_ret"] >= 5

    def test_non_property_union_method_keeps_rejecting(self):
        # BOUNDARY: the row is keyed on `is_property_getter`. A plain method
        # returning the same union is a different AST arm at the same
        # positions, so it must stay on the AST path.
        src = _CELL + (
            "def use(c: Cell) -> None:\n"
            "    print(take(c.get()))\n"
            "use(Cell(3))\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.expr_stmt", "method.ret_type")


# The POINTER-variant twin: reference-typed members, so the union is
# `std::variant<Circle*, Square*>` at borrow positions.
_SHAPES = (
    "from tpy import Int32\n"
    "class Circle:\n"
    "    r: Int32\n"
    "    def __init__(self, r: Int32) -> None:\n"
    "        self.r = r\n"
    "class Square:\n"
    "    s: Int32\n"
    "    def __init__(self, s: Int32) -> None:\n"
    "        self.s = s\n"
    "class Box:\n"
    "    shape: Circle | Square\n"
    "    def __init__(self, shape: Circle | Square) -> None:\n"
    "        self.shape = shape\n"
    "    @property\n"
    "    def view(self) -> Circle | Square:\n"
    "        return self.shape\n"
    "def take(u: Circle | Square) -> Int32:\n"
    "    if isinstance(u, Circle):\n"
    "        return u.r\n"
    "    return u.s\n"
)


class TestSameUnionNonNameArg:
    def test_value_union_non_name_sources_pass_bare(self):
        # A property read and a container-element read: neither is a NAME,
        # and both are already the slot's union, so the AST's union-arg arm
        # declines and the default tail renders them bare. Across the three
        # callee families the row sits in -- a ctor slot, a free-call slot
        # and a method slot -- since each has its own arg loop.
        src = _CELL + (
            "def from_prop(c: Cell) -> Int32:\n"
            "    return Sink(c.peek).m\n"
            "def from_elem(xs: list[Int32 | str], i: Int32) -> Int32:\n"
            "    return take(xs[i])\n"
            "def via_method(c: Cell, d: Cell) -> Int32:\n"
            "    return c.absorb(d.peek)\n"
            "def use(c: Cell) -> None:\n"
            "    print(from_prop(c), from_elem([1, \"ab\"], 1))\n"
            "    print(via_method(c, c))\n"
            "use(Cell(3))\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "return Sink(c.peek()).m;" in out
        assert "return take(::tpy::__getitem__(xs, i));" in out
        assert "return c.absorb(d.peek());" in out

    def test_ptr_variant_non_name_source_keeps_rejecting(self):
        # BOUNDARY: at a POINTER-variant slot the already-union renders
        # branch on the slot's const-ness and on the source being an
        # un-narrowed name, so a non-name source has no settled render and
        # must stay on the AST path. This is the fence the value/pointer
        # split draws.
        src = _SHAPES + (
            "def from_prop(b: Box) -> Int32:\n"
            "    return take(b.view)\n"
            "def use() -> None:\n"
            "    print(from_prop(Box(Circle(3))))\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:expr.call", "call.arg_shape.union")


# A @native record whose factory spells the ownership transfer, next to two
# callees that do not.
_NATIVE = (
    "from tpy import Int32, Own, nocopy\n"
    "from tpy.extern import cpp_template, native\n"
    "@native(\"probe::Raw\")\n"
    "@nocopy\n"
    "class Raw:\n"
    "    def value(self) -> Int32: ...\n"
    "@native(\"probe::Plain\")\n"
    "class Plain:\n"
    "    def value(self) -> Int32: ...\n"
    "@native(\"probe::make_raw\")\n"
    "def make_raw(n: Int32) -> Own[Raw]: ...\n"
    "@native(\"probe::make_plain\")\n"
    "def make_plain(n: Int32) -> Plain: ...\n"
    "@cpp_template(\"probe::tmpl_raw({0})\")\n"
    "def tmpl_raw(n: Int32) -> Own[Raw]: ...\n"
    "@nocopy\n"
    "class Handle:\n"
    "    raw: Raw\n"
    "    def __init__(self, raw: Own[Raw]) -> None:\n"
    "        self.raw = raw\n"
    "class PlainHolder:\n"
    "    p: Plain\n"
    "    def __init__(self, p: Own[Plain]) -> None:\n"
    "        self.p = p\n"
)


class TestNativeOwnRecordRvalue:
    def test_own_returning_native_binds_the_own_slot(self):
        src = _NATIVE + (
            "def wrap(n: Int32) -> Own[Handle]:\n"
            "    return Handle(make_raw(n))\n"
            "def use() -> None:\n"
            "    print(wrap(3).raw.value())\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return Handle(::probe::make_raw(n));" in hpp + cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["own.native_record_rvalue"] == 1
        assert faces["call.native_own_record_value"] == 1

    def test_bare_record_return_keeps_rejecting(self):
        # BOUNDARY: a free @native declared `-> V` leaves its C++ return
        # convention unspelled, and the value category cannot tell a
        # borrowed result from a transferred one -- binding a `T&` into the
        # slot's `T&&` is ill-formed at a reference-type instantiation.
        src = _NATIVE + (
            "def wrap(n: Int32) -> Own[PlainHolder]:\n"
            "    return PlainHolder(make_plain(n))\n"
            "def use() -> None:\n"
            "    print(wrap(3).p.value())\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:expr.call",
                           "call.ctor_arg.own_record_f1")

    def test_cpp_template_callee_keeps_rejecting(self):
        # BOUNDARY: a template text-substitutes the return spelling, so its
        # divergence mechanism is independent of the declared type the
        # admission reads.
        src = _NATIVE + (
            "def wrap(n: Int32) -> Own[Handle]:\n"
            "    return Handle(tmpl_raw(n))\n"
            "def use() -> None:\n"
            "    print(wrap(3).raw.value())\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:expr.call",
                           "call.ctor_arg.own_record_f1")
