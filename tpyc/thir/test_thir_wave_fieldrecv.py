"""Field-receiver wave arms: storage-form tuple FIELD subscripts, the
record-returning user-getitem receiver (read + scalar write), the
deref_optional_check field-chain receiver, the pointer-first-hop user-Deref
chain, the class-constant receiver effect/check statement expressions, and
the property-getter record receiver."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_POINT = (
    "from tpy import Int32, copy\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n        self.y = y\n"
)


class TestTupleFieldSubscript:
    _SRC = (_POINT
            + "class Container:\n"
            + "    data: tuple[Point, Int32]\n"
            + "    def __init__(self, p: Point, n: Int32) -> None:\n"
            + "        self.data = (copy(p), n)\n")

    def test_field_rooted_read_routes(self):
        src = (self._SRC
               + "def use(c: Container) -> None:\n"
               + "    print(c.data[0].x)\n"
               + "    print(c.data[1])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_field_rooted_write_routes(self):
        src = (self._SRC
               + "def use(c: Container) -> None:\n"
               + "    c.data[0].x = 5\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_chain_rooted_receiver_routes(self):
        # A field-over-field ROOT under the subscript is fine: the field row
        # keys the subscript's ELEMENT type and the subscript arm gates its
        # own receiver, so the two compose.
        src = (self._SRC
               + "class Outer:\n"
               + "    c: Container\n"
               + "    def __init__(self, c: Container) -> None:\n"
               + "        self.c = copy(c)\n"
               + "def use(o: Outer) -> None:\n"
               + "    print(o.c.data[0].x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestRecordGetitemReceiver:
    _SRC = (_POINT
            + "class Grid:\n"
            + "    p: Point\n"
            + "    def __init__(self, p: Point) -> None:\n"
            + "        self.p = copy(p)\n"
            + "    def __getitem__(self, i: Int32) -> Point:\n"
            + "        return self.p\n")

    def test_read_routes(self):
        src = (self._SRC
               + "def use(g: Grid) -> None:\n"
               + "    print(g[0].x)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("subscript.record_getitem", 0) >= 1
        _assert_byte_identical(src)

    def test_scalar_write_routes(self):
        src = (self._SRC
               + "def use(g: Grid) -> None:\n"
               + "    g[0].x = 9\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_borrow_bind_decl_routes(self):
        # `q = g[0]` binds the borrow-returning operator[] lvalue as a
        # REF_ALIAS (`Point& q = g[0];`) -- the alias-decl wave's row; the
        # Own-returning/value boundary lives in test_thir_wave_alias_decl.
        src = (self._SRC
               + "def use(g: Grid) -> None:\n"
               + "    q = g[0]\n"
               + "    print(q.x)\n")
        _assert_routes_byte_identical(src)


class TestOptionalFieldChainCheck:
    _SRC = (_POINT
            + "class Holder:\n"
            + "    opt: Point | None\n"
            + "    def __init__(self, p: Point) -> None:\n"
            + "        self.opt = copy(p)\n")

    def test_unproven_read_routes(self):
        src = (self._SRC
               + "def use(h: Holder) -> None:\n"
               + "    print(h.opt.x)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field.opt_check_field_recv", 0) >= 1
        _assert_byte_identical(src)

    def test_unproven_write_routes(self):
        # The AST wraps the whole optional lvalue
        # (`::tpy::deref_optional_check(h.opt).x = 5;`) and that render is
        # position-independent, exactly like the already-`T*` name receiver
        # beside it -- so the write ladder takes the same row the read
        # ladder does.
        src = (self._SRC
               + "def use(h: Holder) -> None:\n"
               + "    h.opt.x = 5\n")
        _assert_byte_identical(src)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None

    def test_narrowed_field_none_subject_reads_bare(self):
        # A sema-NARROWED storage-Optional FIELD as an `is None` subject
        # reads the BARE member into has_value -- the whole-optional
        # consumer strips the narrowed `(*t.o)` deref (the latent
        # divergence filed from the merge audit, fixed here).
        src = ("from tpy import Int32\n"
               "class T:\n"
               "    o: Int32 | None\n"
               "    def __init__(self) -> None:\n"
               "        self.o = 1\n"
               "def use(t: T) -> None:\n"
               "    if t.o is not None:\n"
               "        print(t.o)\n"
               "        if t.o is None:\n"
               "            print(0)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


_REF = (_POINT
        + "from tpy import auto_readonly\n"
        + "class Ref:\n"
        + "    _target: Point\n"
        + "    def __init__(self, target: Point) -> None:\n"
        + "        self._target = copy(target)\n"
        + "    @auto_readonly\n"
        + "    def __deref__(self) -> Point:\n"
        + "        return self._target\n")


class TestDerefPointerFirstHop:
    def test_narrowed_ptr_field_and_method_route(self):
        # A PROVEN narrowed-Optional local is a `T*` -- the chain joins the
        # first hop with `->` (`r->__deref__().x`), mirroring the AST.
        src = (_REF
               + "def use() -> None:\n"
               + "    r: Ref | None = Ref(Point(1, 2))\n"
               + "    print(r.x)\n"
               + "    print(r._target.x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_plain_value_receiver_still_routes(self):
        src = (_REF
               + "def use() -> None:\n"
               + "    r = Ref(Point(3, 4))\n"
               + "    print(r.y)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_unproven_receiver_stays_ast(self):
        # An UNPROVEN Optional receiver carries the runtime check -- the
        # deref-chain arms exclude it (the AST spells deref_check there).
        src = (_REF
               + "def use(r: Ref | None) -> None:\n"
               + "    print(r.x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestClassConstReceiverWrap:
    _SRC = ("from typing import Final\n"
            "from tpy import Int32, Own\n"
            "class C:\n"
            "    LIMIT: Final[Int32] = 7\n"
            "    def __init__(self) -> None:\n        pass\n"
            "def make_c() -> Own[C]:\n    return C()\n")

    def test_unproven_name_receiver_check_routes(self):
        src = (self._SRC
               + "def use(c: C | None) -> None:\n"
               + "    print(c.LIMIT)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field.class_const_recv_check", 0) >= 1
        _assert_byte_identical(src)

    def test_effect_receiver_discard_routes(self):
        src = (self._SRC
               + "def use() -> None:\n"
               + "    print(make_c().LIMIT)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field.class_const_recv_effect", 0) >= 1
        _assert_byte_identical(src)

    def test_pure_receiver_stays_bare(self):
        src = (self._SRC
               + "def use(c: C) -> None:\n"
               + "    print(c.LIMIT)\n"
               + "    print(C.LIMIT)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_unproven_nonname_receiver_stays_ast(self):
        # The check variant is a declared Optional-ptr NAME only: an
        # unproven FIELD-chain receiver (`h.opt.LIMIT`) keeps falling back
        # (field.class_const_receiver).
        src = (self._SRC
               + "class H:\n"
               + "    opt: C | None\n"
               + "    def __init__(self) -> None:\n"
               + "        self.opt = None\n"
               + "def use(h: H) -> None:\n"
               + "    print(h.opt.LIMIT)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestPtrDerefRecordRetReceiver:
    _SRC = (_POINT
            + "from tpy import Ptr\n"
            + "class Cell:\n"
            + "    p: Point\n"
            + "    def __init__(self, p: Point) -> None:\n"
            + "        self.p = copy(p)\n"
            + "    def get_point(self) -> Point:\n"
            + "        return self.p\n")

    def test_record_ret_receiver_routes(self):
        # A record-returning method on a Ptr[T] receiver consumed by a
        # postfix member (`c.get_point().x`): proven -> arrow, unproven ->
        # deref_check -- the record_ret_ok RECEIVER slice.
        src = (self._SRC
               + "def use(c: Ptr[Cell]) -> None:\n"
               + "    print(c.get_point().x)\n"
               + "    if c is not None:\n"
               + "        print(c.get_point().y)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_value_position_stays_ast(self):
        # The same call in a VALUE position (decl init) is outside the
        # RECEIVER-only slice -- the body keeps falling back.
        src = (self._SRC
               + "def use(c: Ptr[Cell]) -> None:\n"
               + "    if c is not None:\n"
               + "        q = c.get_point()\n"
               + "        print(q.x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestPtrValueFieldReceiver:
    """The Ptr rows: a Ptr[T]-value FIELD method receiver
    (`self.ptr.__deref__()`), and the field read/write over a
    Ptr-RETURNING call receiver (`h.get().value`)."""

    _SRC = (
        "from tpy import Int32, Ptr, readonly, take_ptr\n"
        "class Data:\n"
        "    value: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.value = v\n"
        "class Holder:\n"
        "    p: Ptr[Data]\n"
        "    def __init__(self) -> None:\n"
        "        self.p = Ptr[Data]()\n"
        "    def get(self) -> Ptr[Data]:\n"
        "        return self.p\n"
        "    @readonly\n"
        "    def read(self) -> Int32:\n"
        "        return self.p.__deref__().value\n"
    )

    def test_ptr_field_deref_receiver_routes(self):
        # `self.p.__deref__().value` / `h.p.__deref__().value`: the Ptr
        # field receiver composes the Ptr/@cpp_template family; the
        # deref call renders `::tpy::deref_check(this->p)` / `(h.p)`.
        src = (self._SRC
               + "def use(h: Holder) -> Int32:\n"
               + "    return h.p.__deref__().value\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert _fn(thir, "read") is not None
        assert w.get("method.recv.ptr_field", 0) >= 2
        _assert_routes_byte_identical(src)

    def test_ptr_call_recv_field_read_routes(self):
        # `h.get().value` -> `::tpy::deref_check(h.get()).value`: the
        # field arm wraps the Ptr call result like a Ptr NAME's.
        src = (self._SRC
               + "def use(h: Holder) -> Int32:\n"
               + "    return h.get().value\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field.ptr_value", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_ptr_call_recv_field_write_routes(self):
        # The write twin: `h.get().value = 5` ->
        # `::tpy::deref_check(h.get()).value = 5;`.
        src = (self._SRC
               + "def use(h: Holder) -> None:\n"
               + "    h.get().value = 5\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_routes_byte_identical(src)

    def test_ptr_null_ctor_arg_stays_ast(self):
        # BOUNDARY: `Ptr[T]()` at an ARG slot is outside the MIL's
        # STORAGE thread -- the call-use gate keeps the body AST.
        src = (self._SRC
               + "def use_ptr(q: Ptr[Data]) -> Int32:\n"
               + "    return q.__deref__().value\n"
               + "def use(h: Holder) -> Int32:\n"
               + "    return use_ptr(Ptr[Data]())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_nonvalue_ptr_field_receiver_stays_ast(self):
        # BOUNDARY: a Ptr pointee outside _eligible_ptr_value (StrView)
        # does not take the ptr-field receiver row.
        src = ("from tpy import Int32, Ptr, StrView\n"
               "class Cell:\n"
               "    q: Ptr[StrView]\n"
               "    def __init__(self, q: Ptr[StrView]) -> None:\n"
               "        self.q = q\n"
               "def use(c: Cell) -> None:\n"
               "    print(c.q.__deref__())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestIterProtoRefUnpack:
    _SRC = (_POINT
            + "def use(ps: list[Point]) -> None:\n"
            + "    for i, p in enumerate(ps):\n"
            + "        p.x = (i + 1) * 10\n")

    def test_enumerate_ref_target_routes(self):
        # The iter-proto element is already a borrow tuple: mutable
        # `auto& __tup_N` head, `auto&& p = unwrap_ref(tuple_elem_ref(...))`
        # target -- no tuple_to_pointer lift.
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "use") is not None
        assert w.get("stmt.tuple_unpack.ref_target_iter", 0) >= 1
        _assert_byte_identical(self._SRC)

    def test_zip_ref_target_routes(self):
        src = (_POINT
               + "def use(ps: list[Point], ss: list[Int32]) -> None:\n"
               + "    for p, s in zip(ps, ss):\n"
               + "        p.y *= s\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_hoisted_ref_target_stays_ast(self):
        # A ref target used AFTER the loop takes the AST's pointer-slot
        # assign, not the fresh `auto&&` alias -- still deferred.
        src = (_POINT
               + "def use(ps: list[Point]) -> None:\n"
               + "    for i, p in enumerate(ps):\n"
               + "        pass\n"
               + "    print(p.x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestExportCBodies:
    _SRC = (
        "from tpy.extern import export\n"
        "from tpy import Int32\n"
        "@export(\"Helper_Add\", binding=\"C\")\n"
        "def helper_add(x: Int32) -> Int32:\n"
        "    return x + Int32(1)\n"
        "@export(binding=\"C\")\n"
        "def app_init() -> None:\n"
        "    y: Int32 = helper_add(Int32(42))\n"
        "    print(y)\n"
    )

    def test_export_bodies_and_renamed_callee_route(self):
        # The body renders like a plain function's (the driver owns the
        # extern "C" signature); the renamed callee spells the RAW C symbol
        # (`Helper_Add(42)`), pinned by byte identity.
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "helper_add") is not None
        assert _fn(thir, "app_init") is not None
        _assert_byte_identical(self._SRC)

    def test_str_param_export_stays_ast(self):
        # A str-family param respells `const char*` in the C signature and
        # the AST renders its body reads ABI-blind -- the slice stays out
        # until cutover decides the str story.
        src = (
            "from tpy.extern import export\n"
            "@export(binding=\"C\")\n"
            "def take_name(name: str) -> None:\n"
            "    print(len(name))\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "take_name") is None


_METERS = (
    "from tpy import Int32, ValueType\n"
    "class Meters(ValueType):\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
    "    def __floordiv__(self, other: Meters) -> Meters:\n"
    "        return Meters(self.v // other.v)\n"
    "    def __rfloordiv__(self, other: Int32) -> Meters:\n"
    "        return Meters(other // self.v)\n"
    "    def total(self) -> Int32:\n"
    "        return self.v\n"
)


class TestRecordDunderRvalue:
    def test_field_receiver_routes(self):
        src = (_METERS
               + "def use() -> None:\n"
               + "    a = Meters(12)\n"
               + "    b = Meters(4)\n"
               + "    print((a // b).v)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("binop.record_dunder", 0) >= 1
        assert w.get("field.binop_recv", 0) >= 1
        _assert_byte_identical(src)

    def test_reverse_dunder_swap_routes(self):
        # `13 // b` resolves __rfloordiv__ -- the is_reverse swap must
        # render `(b).__rfloordiv__(13)`, pinned by byte identity.
        src = (_METERS
               + "def use() -> None:\n"
               + "    b = Meters(4)\n"
               + "    print((13 // b).v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_method_receiver_routes(self):
        src = (_METERS
               + "def use() -> None:\n"
               + "    a = Meters(12)\n"
               + "    b = Meters(4)\n"
               + "    print((a // b).total())\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("method.recv.binop", 0) >= 1
        _assert_byte_identical(src)

    def test_decl_init_routes_value_record(self):
        # A record-binop DECL init at a bare VALUE-record slot rides the
        # value-record decl row (`Meters c = (a) // (b);` -- the plain
        # spelled copy over the dunder render; dualgen-verified).
        src = (_METERS
               + "def use() -> None:\n"
               + "    a = Meters(12)\n"
               + "    b = Meters(4)\n"
               + "    c = a // b\n"
               + "    print(c.v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_field_over_unary_stays_ast(self):
        # `(-a).v` -- field over a UNARY dunder has no ladder row (no
        # corpus witness); it must keep falling back.
        src = (_METERS.replace(
                   "    def total",
                   "    def __neg__(self) -> Meters:\n"
                   "        return Meters(-self.v)\n"
                   "    def total")
               + "def use() -> None:\n"
               + "    a = Meters(12)\n"
               + "    print((-a).v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestReadonlyParamLambda:
    def test_scalar_key_lambda_routes(self):
        # The key-function readonly spelling for a value scalar is the
        # plain by-value param (`[](int32_t x) -> int32_t`).
        src = ("def use() -> None:\n"
               "    b = [-3, 1, -4]\n"
               "    print(sorted(b, key=lambda x: x if x >= 0 else -x))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_void_body_lambda_routes_print(self):
        # A void PRINT-body lambda routes as the statement-body closure
        # (THIRPrintChain body); other void bodies stay deferred.
        src = ("from typing import Callable\n"
               "def run(f: Callable[[int], None]) -> None:\n"
               "    f(1)\n"
               "def use() -> None:\n"
               "    run(lambda x: print(x))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestPropertyRecordReceiver:
    _SRC = (_POINT
            + "class Holder:\n"
            + "    _p: Point\n"
            + "    def __init__(self, p: Point) -> None:\n"
            + "        self._p = copy(p)\n"
            + "    @property\n"
            + "    def mid(self) -> Point:\n"
            + "        return self._p\n")

    def test_property_record_field_read_routes(self):
        src = (self._SRC
               + "def use(h: Holder) -> None:\n"
               + "    print(h.mid.x)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("field.property_call_recv", 0) >= 1
        _assert_byte_identical(src)
