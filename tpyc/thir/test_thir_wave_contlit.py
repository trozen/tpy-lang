"""Container-literal element rows landed grinding the elem gate to zero:
literal-typed slots, marker protocol arg hoists, copy()/union/String/tparam
elements, jagged tuple members, and the tuple-elem-over-subscript receiver."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn,
    _assert_byte_identical,
)

_P = (
    "from tpy import Int32, copy\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestLiteralTypedSlots:
    """A native CALL-ARG literal keeps Int/FloatLiteralType element slots;
    `_resolved_scalar` classifies them as the base scalar family."""

    def test_marker_float_literal_args_hoist(self):
        # The qualcall protocol hoist: `auto __tmp_N = std::array<double,
        # 3>{...}` with the protocol's element type substituted into the
        # spelling (never `std::array<1.0, 3>`).
        src = ("import math\n"
               "def f() -> None:\n"
               "    math.dist([1.0, 2.0, 3.0], [4.0, 5.0])\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("argtemp.marker_protocol_literal", 0) >= 2
        _assert_byte_identical(src)

    def test_marker_range_arg_hoists(self):
        # The range rung: `auto __tmp_N = ::tpy::Range<int32_t>(1, 11);`.
        src = ("import math\n"
               "def f() -> None:\n"
               "    t: float = math.fsum(range(1, 11))\n"
               "    print(t)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("argtemp.marker_protocol_range", 0) >= 1
        _assert_byte_identical(src)

    def test_marker_name_arg_stays_bare(self):
        # An lvalue NAME at the same slot binds bare -- no hoist.
        src = ("import math\n"
               "def f() -> None:\n"
               "    xs = [1.0, 2.0]\n"
               "    print(math.fsum(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("argtemp.marker_protocol_literal", 0) == 0
        _assert_byte_identical(src)


class TestCopyRecordElement:
    def test_copy_element_routes(self):
        # `[copy(p)]` -> the copy-construct rvalue (`{P(p)}`).
        src = (_P
               + "def f() -> None:\n"
               + "    p = P(1)\n"
               + "    xs = [copy(p)]\n"
               + "    print(p.x, len(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("containerlit.copy_record", 0) >= 1
        _assert_byte_identical(src)

    def test_copy_field_element_stays_ast(self):
        # The boundary: copy_plain_record_source admits bare NAMES only; a
        # field-access source (`copy(h.p)`) keeps rejecting.
        src = (_P
               + "class H:\n"
               + "    p: P\n"
               + "    def __init__(self) -> None:\n"
               + "        self.p = P(2)\n"
               + "def f() -> None:\n"
               + "    h = H()\n"
               + "    xs = [copy(h.p)]\n"
               + "    print(len(xs))\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestJaggedTupleMember:
    def test_nested_list_member_routes(self):
        # `[(1, [2, 3])]` -- the nested list member renders its BARE brace
        # inside the storage tuple (`S{1, {2, 3}}`).
        src = ("def f() -> None:\n"
               "    xs = [(1, [2, 3]), (4, [5, 6, 7])]\n"
               "    print(xs)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_dict_member_stays_ast(self):
        # A dict literal member is not the admitted nested shape.
        src = ("def f() -> None:\n"
               "    xs = [(1, {2: 3})]\n"
               "    print(xs)\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_tuple_elem_subscript_receiver_routes(self):
        # `xs[1][1].append(9)` -> `std::get<1>(::tpy::__getitem__(xs,
        # 1)).push_back(9)` -- the tuple-elem-over-subscript method receiver.
        src = ("def f() -> None:\n"
               "    xs = [(1, [2, 3]), (4, [5, 6, 7])]\n"
               "    xs[1][1].append(9)\n"
               "    print(xs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("method.recv.tuple_elem_subscript", 0) >= 1
        assert w.get("subscript.tuple_elem_recv", 0) >= 1
        _assert_byte_identical(src)


_AB = (
    "from tpy import Int32\n"
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.x = 1\n"
    "class B:\n"
    "    x: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.x = 2\n"
)


class TestUnionElements:
    def test_member_record_name_moves_via_make(self):
        # `[r, B()]` at `list[A | B]`: the member NAME moves at last use via
        # make_vector (`make_vector<variant<..>>(std::move(r), B())`).
        src = (_AB
               + "def f() -> None:\n"
               + "    r = A()\n"
               + "    xs: list[A | B] = [r, B()]\n"
               + "    print(len(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("containerlit.make", 0) >= 1
        _assert_byte_identical(src)

    def test_union_name_lifts_to_value_variant(self):
        # A tracked ptr-variant union NAME lifts (`to_value_variant<..>(a)`),
        # and it is NOT a move source (the local is a non-owning alias).
        src = (_AB
               + "def show(v: A | B) -> None:\n"
               + "    pass\n"
               + "def f() -> None:\n"
               + "    a: A | B = A()\n"
               + "    xs: list[A | B] = [a]\n"
               + "    show(a)\n"
               + "    print(len(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("containerlit.union_name_lift", 0) >= 1
        _assert_byte_identical(src)

    def test_narrowed_union_name_reads_alias(self):
        # Inside `isinstance(a, A)` the element reads the extraction alias
        # BARE (`{__a, to_value_variant<..>(b)}` -- the converting ctor
        # absorbs the member), and the alias never moves.
        src = (_AB
               + "def show(v: A | B) -> None:\n"
               + "    pass\n"
               + "def f() -> None:\n"
               + "    a: A | B = A()\n"
               + "    b: A | B = B()\n"
               + "    items: list[A | B] = [a, b]\n"
               + "    show(a)\n"
               + "    show(b)\n"
               + "    print(len(items))\n"
               + "    if isinstance(a, A):\n"
               + "        xs: list[A | B] = [a, b]\n"
               + "        print(len(xs))\n")
        # NB the narrowed-elem face witness (`containerlit.union_narrowed_elem`)
        # fires on the full-pipeline run (corpus case list/list_own_unwrap);
        # this harness lowers the branch decl through a sibling path, so the
        # unit pins the render via byte-identity + the lift count.
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("containerlit.union_name_lift", 0) >= 3
        _assert_byte_identical(src)

    def test_mismatched_union_name_stays_ast(self):
        # A name of a DIFFERENT union than the slot stays AST.
        src = (_AB
               + "class C:\n"
               + "    x: Int32\n"
               + "    def __init__(self) -> None:\n"
               + "        self.x = 3\n"
               + "def f() -> None:\n"
               + "    a: A | B = A()\n"
               + "    xs: list[A | C] = [a]\n"
               + "    print(len(xs))\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestWrapperScalarLiteral:
    def test_scalar_literals_route_bare(self):
        # `xs: list[V] = [1, 2, 3]` over a recursive wrapper V: the
        # converting ctor absorbs the bare literals (`{1, 2, 3}`).
        src = ("from tpy import Int32\n"
               "type V = Int32 | list[V]\n"
               "def f() -> None:\n"
               "    xs: list[V] = [1, 2, 3]\n"
               "    print(len(xs))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_scalar_name_element_stays_ast(self):
        # The boundary: only LITERALS take the bare wrapper-absorb render; a
        # scalar NAME at the wrapper slot keeps rejecting.
        src = ("from tpy import Int32\n"
               "type V = Int32 | list[V]\n"
               "def f() -> None:\n"
               "    n: Int32 = 1\n"
               "    xs: list[V] = [n]\n"
               "    print(len(xs))\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestStringElement:
    def test_string_param_element_routes(self):
        # A `String` param element at its owned-str slot copies bare
        # (`std::array<std::string, 1> xs = {s};`).
        src = ("from tpy import String\n"
               "def f(s: String) -> None:\n"
               "    xs = [s]\n"
               "    print(xs[0])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestBuiltinModuleMarkers:
    """The marker-kind rows landed with this wave: the builtin-module
    @cpp_template TYPE ctor (`tpy.Int32(10)`) and the partial-explicit
    generic targ prefix (`tpy.unsafe.unsafe_cast[UInt32](p)`)."""

    def test_qualified_type_ctor_routes(self):
        src = ("import tpy\n"
               "def add(a: tpy.Int32, b: tpy.Int32) -> tpy.Int32:\n"
               "    return a + b\n"
               "def f() -> None:\n"
               "    x: tpy.Int32 = tpy.Int32(10)\n"
               "    y: tpy.Int32 = tpy.Int32(20)\n"
               "    print(add(x, y))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("call.static_template", 0) >= 2
        _assert_byte_identical(src)

    def test_generic_ctor_overload_stays_ast(self):
        # A builtin-module ctor resolving a GENERIC overload
        # (`tpy.Int32(tpy.UInt32(5))` -> int_cast_check<{cpp}>) keeps the
        # carve-out's `not fi.type_params` guard.
        src = ("import tpy\n"
               "def f() -> None:\n"
               "    x: tpy.Int32 = tpy.Int32(10)\n"
               "    y: tpy.Int32 = tpy.Int32(tpy.UInt32(5))\n"
               "    print(x, y)\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_partial_explicit_targ_prefix_routes(self):
        # `unsafe_cast[UInt32](p)` spells ONE of [T, U]; the explicit list is
        # a PREFIX of the inferred list and the render comes from the
        # inferred targs alone.
        src = ("from tpy import Int32, UInt32, Ptr, Array\n"
               "import tpy.unsafe\n"
               "def f() -> None:\n"
               "    arr: Array[Int32, 2] = [Int32(1), Int32(2)]\n"
               "    p: Ptr[Int32] = tpy.unsafe.unsafe_ptr(arr)\n"
               "    q: Ptr[UInt32] = tpy.unsafe.unsafe_cast[UInt32](p)\n"
               "    print(tpy.unsafe.unsafe_load(q, UInt32(0)))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("call.static_template", 0) >= 1
        _assert_byte_identical(src)


class TestReturnSlotRows:
    """The return.slot_type rows landed grinding statements.py's
    ret_supported gate: Own[value-scalar] / Own[structural-protocol] /
    Any-wrap / Own[storage-tuple] / value-bound Optional[T] / Span[T] /
    Own[record|scalar union] returns."""

    def test_own_scalar_return_routes(self):
        src = ("from tpy import Int32, auto_own\n"
               "from typing import Self\n"
               "class Bag:\n"
               "    items: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = [1, 2]\n"
               "    def consume(self: auto_own[Self]) -> auto_own[Int32]:\n"
               "        total: Int32 = 0\n"
               "        for x in self.items:\n"
               "            total += x\n"
               "        return total\n")
        thir = _lower_ctx(src)
        assert thir is not None
        _assert_byte_identical(src)

    def test_own_protocol_return_routes(self):
        # `auto_own[Iterator[Int32]]` returns the same `auto` slot as the
        # bare structural protocol (`return iter(self.items)`).
        src = ("from tpy import Int32, auto_own\n"
               "from typing import Iterator, Self\n"
               "class Bag:\n"
               "    items: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = [1, 2]\n"
               "    def __iter__(self: auto_own[Self]) -> "
               "auto_own[Iterator[Int32]]:\n"
               "        return iter(self.items)\n")
        thir = _lower_ctx(src)
        assert thir is not None
        _assert_byte_identical(src)

    def test_generic_span_return_routes(self):
        # `Span[auto_readonly[T]]` (the auto_readonly getter pair): both
        # clones route, sources are the spanlike coerces
        # (`as_mut_span` / `as_span`).
        src = ("from tpy import Int32, Span, auto_readonly\n"
               "class Buf[T]:\n"
               "    _data: list[T]\n"
               "    def __init__(self) -> None:\n"
               "        self._data = []\n"
               "    @auto_readonly\n"
               "    def data(self) -> Span[auto_readonly[T]]:\n"
               "        return self._data\n")
        thir = _lower_ctx(src)
        assert thir is not None
        _assert_byte_identical(src)

    def test_any_return_wraps_make_any(self):
        src = ("from typing import Any\n"
               "from tpy import Int32\n"
               "class Bag:\n"
               "    def __getattr__(self, name: str) -> Any:\n"
               "        if name == \"count\":\n"
               "            return 7\n"
               "        raise AttributeError(name)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("ret.any_wrap", 0) >= 1
        _assert_byte_identical(src)

    def test_own_storage_tuple_literal_returns(self):
        src = ("from tpy import Int32, Own\n"
               "class Res:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n"
               "def make_pair(x: Int32) -> Own[tuple[str, Res]]:\n"
               "    return (str(x), Res(x))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "make_pair") is not None
        assert w.get("ret.own_storage_tuple", 0) >= 1
        _assert_byte_identical(src)

    def test_own_storage_tuple_movable_name_member_routes(self):
        # A non-value member NAME that is MOVABLE at its last use moves
        # into the storage slot (`{..., std::move(r)}` -- the wave-3c
        # widening); the non-last-use sibling stays pinned out in
        # test_thir_wave_pending_view_own.py.
        src = ("from tpy import Int32, Own, copy\n"
               "class Res:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.v = v\n"
               "def make_pair(x: Int32) -> Own[tuple[str, Res]]:\n"
               "    r = Res(x)\n"
               "    r.v += 1\n"
               "    return (str(x), r)\n")
        assert _fn(_lower_ctx(src), "make_pair") is not None

    def test_value_bound_optional_return_routes(self):
        src = ("from tpy import Int32, ValueType\n"
               "class Cell[T: ValueType]:\n"
               "    _value: T\n"
               "    _has: bool\n"
               "    def __init__(self, v: T) -> None:\n"
               "        self._value = v\n"
               "        self._has = True\n"
               "    def get(self) -> T | None:\n"
               "        if self._has:\n"
               "            return self._value\n"
               "        return None\n")
        thir = _lower_ctx(src)
        assert thir is not None
        _assert_byte_identical(src)

    def test_own_union_scalar_member_return_routes(self):
        src = ("from tpy import Int32, Own\n"
               "class Bag:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "def get(flag: bool) -> Own[Bag | int]:\n"
               "    if flag:\n"
               "        return Bag()\n"
               "    return 0\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "get") is not None
        assert w.get("ret.own_union_ctor", 0) >= 1
        _assert_byte_identical(src)


class TestTypeParamElement:
    def test_tparam_name_element_routes(self):
        # `return [x]` in a generic body renders `return {x};`.
        src = ("from tpy import Own\n"
               "def make[T](x: T) -> Own[list[T]]:\n"
               "    return [x]\n"
               "def f() -> None:\n"
               "    print(make(42))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "make") is not None
        assert w.get("containerlit.tparam_elem", 0) >= 1
        _assert_byte_identical(src)

    def test_tparam_call_element_stays_ast(self):
        # A non-name source at the T slot stays AST.
        src = ("from tpy import Own, copy\n"
               "def make[T](x: T) -> Own[list[T]]:\n"
               "    return [copy(x)]\n"
               "def f() -> None:\n"
               "    print(make(42))\n")
        assert _fn(_lower_ctx(src), "make") is None


class TestCompRouteRows:
    """The comp-route rows from the list_comp grind: array-source unpack
    heads, range-arm value-tuple elements, non-value tuple elements
    (literal + whole name), and items() record unpack targets."""

    def test_array_source_unpack_routes(self):
        src = ("from tpy import Int32, Array\n"
               "def sum_pairs(ps: Array[tuple[Int32, Int32], 3]) -> None:\n"
               "    sums = [a + b for a, b in ps]\n"
               "    print(sums[0], sums[2])\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "sum_pairs") is not None
        assert w.get("comp.array_source", 0) >= 1
        assert w.get("comp.unpack", 0) >= 1
        _assert_byte_identical(src)

    def test_range_value_tuple_element_routes(self):
        src = ("from tpy import Int32, Array\n"
               "def take(ps: Array[tuple[Int32, Int32], 3]) -> None:\n"
               "    print(len(ps))\n"
               "def f() -> None:\n"
               "    pairs = [(i, i * 10) for i in range(3)]\n"
               "    take(pairs)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_nonvalue_tuple_elements_route(self):
        # A tuple LITERAL with a record member (the storage-context
        # CONST_REF row) and the whole loop-var NAME (the non-move
        # tuple_to_storage copy) at a non-value tuple element slot.
        src = (_P
               + "def lit(cells: list[P]) -> None:\n"
               + "    xs: list[tuple[Int32, P]] = [(1, c) for c in cells]\n"
               + "    print(len(xs))\n"
               + "def whole(src: list[tuple[Int32, P]]) -> None:\n"
               + "    xs: list[tuple[Int32, P]] = [t for t in src]\n"
               + "    print(len(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "lit") is not None
        assert _fn(thir, "whole") is not None
        assert w.get("containerlit.tuple_name_storage", 0) >= 1
        _assert_byte_identical(src)

    def test_tuple_name_storage_lift_arm(self):
        # BOUNDARY for the row above: a storage-form loop var takes the SKIP
        # path, so that test alone would stay green if the convert were deleted.
        # A tuple PARAM read as the element is NOT storage form, so it still
        # owes the `tuple_to_storage` wrap -- this pins the arm that emits it.
        src = (_P
               + "def lift(p: tuple[Int32, P]) -> None:\n"
               + "    xs: list[tuple[Int32, P]] = [p for _ in range(2)]\n"
               + "    print(len(xs))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "lift") is not None
        assert w.get("containerlit.tuple_name_storage", 0) >= 1
        emitted = _assert_byte_identical(src)
        assert "::tpy::tuple_to_storage<std::tuple<int32_t, P>>(p)" in \
            "".join(emitted)

    def test_items_record_unpack_routes(self):
        src = (_P
               + "def f() -> None:\n"
               + "    m: dict[str, P] = {\"a\": P(1)}\n"
               + "    pts = [p for _, p in m.items()]\n"
               + "    print(pts[0].x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_narrowing_ternary_element_stays_ast(self):
        # The boundary: a narrowing ternary over the Optional loop var
        # needs per-element narrowing machinery -- keeps rejecting.
        src = (_P
               + "def f(items: list[P | None]) -> None:\n"
               + "    xs = [i.x if i is not None else -1 for i in items]\n"
               + "    print(len(xs))\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_values_record_loop_routes(self):
        # The dict-view widening's sibling: a record-VALUE dict's values()
        # loop var binds through the shared loop_var_binding -- routes
        # byte-identically (the widened _dict_view_iterable_ok admits the
        # record-value dict; the elem gates decide the loop var).
        src = (_P
               + "def f() -> None:\n"
               + "    m: dict[str, P] = {\"a\": P(1)}\n"
               + "    pts = [p for p in m.values()]\n"
               + "    print(pts[0].x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestInplaceDunderAug:
    """The resolved-inplace aug-assign row: gen_call_from_fi over the
    inplace method, THIRMethodCall's three arms."""

    def test_atomic_scalar_routes(self):
        src = ("from tpy.atomic import Atomic\n"
               "from tpy import Int32\n"
               "def f() -> None:\n"
               "    b = Atomic[Int32](60)\n"
               "    b += 10\n"
               "    b -= 5\n"
               "    print(b.load())\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("aug.inplace_dunder", 0) >= 2
        _assert_byte_identical(src)

    def test_set_update_template_routes(self):
        # `s |= {3}` -> the cpp_template arm
        # (`::tpy::set_update(s, ::tpy::ordered_set<int32_t>({3}))`).
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    s = {1, 2}\n"
               "    s |= {3}\n"
               "    print(len(s))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("aug.inplace_dunder", 0) >= 1
        _assert_byte_identical(src)

    def test_narrowed_receiver_stays_ast(self):
        # A narrowed Optional receiver is a pointer-form binding whose AST
        # render composes a deref the row does not carry -- stays AST.
        src = ("from tpy.atomic import Atomic\n"
               "from tpy import Int32\n"
               "def f(b: Atomic[Int32] | None) -> None:\n"
               "    if b is not None:\n"
               "        b += 10\n"
               "        print(b.load())\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_field_target_routes(self):
        # `a.get().n += 5` -> `a.get().n.__iadd__(5);` (the field arm's
        # receiver gates apply during lowering).
        src = ("from tpy.atomic import Atomic\n"
               "from tpy import Int32\n"
               "from tplib import Rc\n"
               "class Counter:\n"
               "    n: Atomic[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.n = Atomic[Int32](0)\n"
               "def f() -> None:\n"
               "    a = Rc.new(Counter())\n"
               "    a.get().n += 5\n"
               "    print(a.get().n.load())\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("aug.inplace_dunder", 0) >= 1
        _assert_byte_identical(src)
