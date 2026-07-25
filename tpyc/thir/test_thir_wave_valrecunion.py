"""Value-record union family: the `_eligible_value_union` member-table
widening to ValueType-record members (isinstance narrowing, None tests,
variant arg temps), the nested-temp threading the family needed (method
record-rvalue args, borrow-tuple rvalue elements, call-shaped field
receivers), and the boundaries that stay AST."""

from __future__ import annotations

import io

from .emit import emit_thir_body
from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _compile, _entry,
)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_VALREC = (
    "from dataclasses import dataclass\n"
    "from tpy import ValueType, Int32\n"
    "@dataclass(frozen=True)\n"
    "class Fx(ValueType):\n"
    "    off: Int32\n"
    "@dataclass(frozen=True)\n"
    "class Zn(ValueType):\n"
    "    zid: Int32\n"
)


class TestValueRecordUnionNarrowing:
    def test_isinstance_chain_routes_byte_identical(self):
        src = _VALREC + (
            "def f(u: Fx | Zn | None) -> Int32:\n"
            "    if u is None:\n"
            "        return -1\n"
            "    if isinstance(u, Fx):\n"
            "        return u.off\n"
            "    return u.zid\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_narrow_alias_render_const_ref_get(self):
        # The value-union PARAM extraction: `const auto&` + member-keyed
        # `std::get<Fx>` -- no ptr-variant `*`.
        thir = _lower_ctx(_VALREC + (
            "def f(u: Fx | Zn | None) -> Int32:\n"
            "    if u is None:\n"
            "        return -1\n"
            "    if isinstance(u, Fx):\n"
            "        return u.off\n"
            "    return u.zid\n"))
        body = _body(thir, "f")
        assert "if (std::holds_alternative<Fx>(u)) {" in body
        assert "const auto& __u = std::get<Fx>(u);" in body
        assert "return __u.off;" in body

    def test_mixed_scalar_record_members_route(self):
        src = _VALREC + (
            "def f(u: Int32 | Fx | None) -> Int32:\n"
            "    if u is None:\n"
            "        return -1\n"
            "    if isinstance(u, Fx):\n"
            "        return u.off\n"
            "    return u\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_generic_valrec_member_still_defers(self):
        # A GENERIC value-record member (`Pair[Int32]`) stays out of the
        # member table (`not m.type_args` -- the F5 spelling slice).
        src = _VALREC + (
            "class Pair[T: ValueType](ValueType):\n"
            "    a: T\n"
            "    def __init__(self, a: T) -> None:\n"
            "        self.a = a\n"
            "def f(u: Pair[Int32] | Zn | None) -> Int32:\n"
            "    if isinstance(u, Zn):\n"
            "        return u.zid\n"
            "    return 0\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_value_opt_record_none_test_still_defers(self):
        # `Fx | None` is a value-repr OPTIONAL[record], not a union: the
        # has_value/narrowed-read binding family is unrouted (parked).
        src = _VALREC + (
            "def f(u: Fx | None) -> Int32:\n"
            "    if u is None:\n"
            "        return -1\n"
            "    return 0\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestValueRecordUnionArgTemps:
    def test_ctor_rvalue_free_arg_hoists_variant_temp(self):
        thir, faces = _lower_ctx_witnessed(_VALREC + (
            "def take(u: Fx | Zn | None) -> bool:\n"
            "    return u is None\n"
            "def f() -> None:\n"
            "    print(take(Fx(3)))\n"))
        body = _body(thir, "f")
        assert ("std::variant<std::monostate, Fx, Zn> __tmp_1 = Fx(3);"
                in body)
        assert faces.get("argtemp.value_union", 0) >= 1
        _assert_byte_identical(_VALREC + (
            "def take(u: Fx | Zn | None) -> bool:\n"
            "    return u is None\n"
            "def f() -> None:\n"
            "    print(take(Fx(3)))\n"))

    def test_nested_method_arg_ctor_union_temp_routes(self):
        # The eval_once shape: a method arg's record-ctor rvalue renders
        # inline while its NESTED union-slot arg still flushes the variant
        # temp at the statement (`b.eat(Box(mk(...)))`).
        src = _VALREC + (
            "def mk(n: Int32) -> Fx:\n"
            "    print(\"eval\")\n"
            "    return Fx(n)\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, tz: Fx | Zn | None = None) -> None:\n"
            "        if tz is None:\n"
            "            self.v = 0\n"
            "        elif isinstance(tz, Fx):\n"
            "            self.v = tz.off\n"
            "        else:\n"
            "            self.v = tz.zid\n"
            "    def eat(self, other: \"Box\") -> Int32:\n"
            "        return other.v\n"
            "def f() -> None:\n"
            "    b = Box()\n"
            "    print(b.eat(Box(mk(3))))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        body = _body(thir, "f")
        assert ("std::variant<std::monostate, Fx, Zn> __tmp_1 = "
                "mk(3);" in body)
        assert "b.eat(Box(__tmp_1))" in body
        _assert_byte_identical(src)

    def test_field_receiver_ctor_arg_temp_flushes(self):
        # A call-shaped FIELD receiver inherits the statement's flush right
        # (`Holder(Fx(1)).kind` -> the variant temp hoists before the read).
        src = _VALREC + (
            "class Holder:\n"
            "    kind: Int32\n"
            "    def __init__(self, tz: Fx | Zn | None = None) -> None:\n"
            "        k = 0\n"
            "        if tz is not None:\n"
            "            k = 1\n"
            "        self.kind = k\n"
            "def f() -> None:\n"
            "    print(Holder(Fx(1)).kind)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        body = _body(thir, "f")
        assert ("std::variant<std::monostate, Fx, Zn> __tmp_1 = Fx(1);"
                in body)
        assert "Holder(__tmp_1).kind" in body
        _assert_byte_identical(src)


_PTRREC = (
    "from tpy import Int32\n"
    "class Box:\n"
    "    v: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.v = n\n"
)


class TestBorrowTupleOptionalElem:
    def test_rvalue_into_optional_elem_slot_routes(self):
        # A pointer-repr Optional elem slot takes the plain borrow spelling
        # (`Box*` / source `Box`) for an RVALUE element.
        src = _PTRREC + (
            "def take(pair: tuple[Box | None, Int32]) -> Int32:\n"
            "    return pair[1]\n"
            "def f() -> None:\n"
            "    print(take((Box(7), 4)))\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_name_into_optional_elem_slot_routes_addr(self):
        # A plain lvalue NAME source takes the `&(name)` lift
        # (btuple.elem_optptr); narrowed/storage-form names still defer
        # (pinned in test_thir_wave_ctorargs7).
        src = _PTRREC + (
            "def take(pair: tuple[Box | None, Int32]) -> Int32:\n"
            "    return pair[1]\n"
            "def f() -> None:\n"
            "    bx = Box(3)\n"
            "    print(take((bx, 5)))\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


_CORO = (
    "from tpy import Int32\n"
    "from tpy.coro import poll_once, poll_ready, Poll, Waker\n"
    "async def coro() -> Int32:\n"
    "    return Int32(42)\n"
)


class TestConsumingReceiverAndCoroTemp:
    def test_consuming_on_rvalue_call_receiver_routes(self):
        # `poll_once(coro()).value()`: the consuming fi admits an RVALUE
        # call receiver (no std::move -- the AST moves NAME receivers only);
        # the coro-factory arg hoists the un-spelled `auto __tmp_N` temp
        # and the generic callee spells explicit template args.
        src = _CORO + (
            "def f() -> None:\n"
            "    print(poll_once(coro()).value())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        body = _body(thir, "f")
        assert "auto __tmp_1 = coro();" in body
        assert "poll_once<int32_t>(__tmp_1).value()" in body
        assert "std::move" not in body
        assert faces.get("argtemp.protocol", 0) >= 1
        _assert_byte_identical(src)

    def test_consuming_on_name_receiver_still_moves(self):
        # The NAME-receiver move render is unchanged by the rvalue widening.
        src = _CORO + (
            "def f() -> None:\n"
            "    p = poll_once(coro())\n"
            "    print(p.value())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert "std::move(p).value()" in _body(thir, "f")
        _assert_byte_identical(src)

    def test_coro_temp_in_match_guard_still_defers(self):
        # A match guard is never a flush point: the coro-factory auto-temp
        # must keep falling back there.
        src = _CORO + (
            "def f(n: Int32) -> Int32:\n"
            "    match n:\n"
            "        case v if poll_once(coro()).is_ready():\n"
            "            return v\n"
            "        case _:\n"
            "            return Int32(-1)\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestPrintOptvalRecordCall:
    def test_value_record_optional_method_result_prints_wrapped(self):
        # `print(h.tz())` on a `-> Fx | None` method: the value-repr
        # `std::optional<Fx>` result feeds `::tpy::print_optional_val(...)`
        # bare (the value-record-inner call arm of _print_optval_opt).
        src = _VALREC + (
            "class H:\n"
            "    def tz(self) -> Fx | None:\n"
            "        return Fx(1)\n"
            "def f(h: H) -> None:\n"
            "    print(h.tz())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert "print_optional_val" in _body(thir, "f")
        assert faces.get("print.optval", 0) >= 1
        _assert_byte_identical(src)

    def test_ptr_repr_optional_method_result_still_defers(self):
        # A pointer-repr Optional[record] result (a non-value class) keeps
        # falling back -- only value-repr optionals take the wrap.
        src = (
            "from tpy import Int32\n"
            "class Dog:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class H:\n"
            "    d: Dog\n"
            "    def __init__(self) -> None:\n"
            "        self.d = Dog(Int32(1))\n"
            "    def pick(self) -> Dog | None:\n"
            "        return self.d\n"
            "def f(h: H) -> None:\n"
            "    print(h.pick())\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestBorrowContainerReturns:
    _SRC = (
        "from tpy import Int32\n"
        "class H:\n"
        "    _xs: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._xs = [Int32(1)]\n"
        "    def xs(self) -> list[Int32]:\n"
        "        return self._xs\n"
    )

    def test_field_and_name_sources_route(self):
        src = self._SRC + (
            "def keep(cells: list[Int32]) -> list[Int32]:\n"
            "    return cells\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "xs") is not None
        assert _fn(thir, "keep") is not None
        assert faces.get("ret.container_borrow", 0) >= 2
        _assert_byte_identical(src)

    def test_call_source_still_defers(self):
        src = self._SRC + (
            "    def via(self) -> list[Int32]:\n"
            "        return self.xs()\n")
        assert _fn(_lower_ctx(src), "via") is None


class TestValueRecordAndWrapperDecls:
    def test_value_record_decl_and_reassign_route(self):
        src = _VALREC + (
            "def mk(n: Int32) -> Fx:\n"
            "    return Fx(n)\n"
            "def f() -> Int32:\n"
            "    a = mk(1)\n"
            "    a = mk(2)\n"
            "    b = Fx(3)\n"
            "    return a.off + b.off\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_generic_value_record_decl_routes_via_f1(self):
        # A CONCRETE-args generic value record (`Pair[Int32]`) rides the F1
        # generic-record decl machinery (`_f1_record` admits concrete
        # args) -- only the union MEMBER table excludes generics.
        src = _VALREC + (
            "class Pair[T: ValueType](ValueType):\n"
            "    a: T\n"
            "    def __init__(self, a: T) -> None:\n"
            "        self.a = a\n"
            "def f() -> Int32:\n"
            "    p = Pair(Int32(1))\n"
            "    return p.a\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_wrapper_decl_from_call_routes(self):
        # The wrapper alias spelling (JsonValue) is codegen-populated, so
        # the pin is byte-identity through the pipeline, not _body text.
        src = (
            "import json\n"
            "def f(s: str) -> None:\n"
            "    v = json.loads(s)\n"
            "    print(json.dumps(v))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_wrapper_reassigned_decl_still_defers(self):
        # A reassigned wrapper local takes the AST's pointer-rebind
        # machinery (`(*v)` reads) -- stays AST (dualgen-caught).
        src = (
            "import json\n"
            "def f(s: str) -> None:\n"
            "    v = json.loads(s)\n"
            "    print(json.dumps(v))\n"
            "    v = json.loads(\"[1]\")\n"
            "    print(json.dumps(v))\n")
        assert _fn(_lower_ctx(src), "f") is None


_SUBMOD_REC = (
    "from tpy import Int32, Own\n"
    "class Rec:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def make(n: Int32) -> Own[Rec]:\n"
    "    return Rec(n)\n"
)


class TestModuleQualifiedCtorArgTemp:
    def _setup(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "sub.py").write_text(_SUBMOD_REC)
        return tmp_path

    def _emit(self, src, lib, thir_flag):
        compiler, modules = _compile(src, extra_lib_dirs=[lib])
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir_flag))
        return hpp + cpp

    def test_qualified_ctor_free_arg_hoists_temp(self, tmp_path):
        # `use(sub.Rec(7))`: the qualified-ctor method-call rides the
        # record-rvalue temp row (`::tpyapp::pkg::sub::Rec __tmp_1 =
        # ::tpyapp::pkg::sub::Rec(7);`).
        lib = self._setup(tmp_path)
        src = ("from pkg import sub\nfrom tpy import Int32\n"
               "def use(r: sub.Rec) -> Int32:\n    return r.n\n"
               "def f() -> None:\n    print(use(sub.Rec(Int32(7))))\n")
        thir, faces = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "f") is not None
        assert faces.get("argtemp.record_rvalue", 0) >= 1
        out = self._emit(src, lib, True)
        assert out == self._emit(src, lib, False)
        assert ("::tpyapp::pkg::sub::Rec __tmp_1 = "
                "::tpyapp::pkg::sub::Rec(7);" in out)

    def test_spanlike_field_return_routes(self):
        # `return self.data` at a Span return slot: the as_span/as_mut_span
        # helper wraps the bare member read (the @auto_readonly pair).
        src = (
            "from tpy import Int32, Span, auto_readonly, readonly\n"
            "class Buf:\n"
            "    data: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.data = [Int32(1)]\n"
            "    @auto_readonly\n"
            "    def view(self) -> Span[auto_readonly[Int32]]:\n"
            "        return self.data\n"
            "def f(b: readonly[Buf]) -> Int32:\n"
            "    s = b.view()\n"
            "    return s[Int32(0)]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        # BOTH @auto_readonly clones route (they appear as two `view` fns).
        assert sum(1 for fn in thir.functions if fn.name == "view") == 2
        _assert_byte_identical(src)

    def test_methodcall_rvalue_storage_return_routes(self):
        # `return self._c.clone()` at an Own storage return slot renders the
        # bare call; the method call's own gates run in the STORAGE tail.
        src = (
            "from tpy import Int32, Own\n"
            "class C:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def clone(self) -> Own[\"C\"]:\n"
            "        return C(self.n)\n"
            "class H:\n"
            "    _c: C\n"
            "    def __init__(self) -> None:\n"
            "        self._c = C(Int32(1))\n"
            "    def handle(self) -> Own[C]:\n"
            "        return self._c.clone()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "handle") is not None
        assert faces.get("ret.record_methodcall", 0) >= 1
        _assert_byte_identical(src)

    def test_methodcall_borrow_return_still_defers(self):
        # A method-call source at a BORROW (`T&`) return slot stays AST
        # (the borrow-return machinery is a different render).
        src = (
            "from tpy import Int32\n"
            "class C:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class H:\n"
            "    _c: C\n"
            "    def __init__(self) -> None:\n"
            "        self._c = C(Int32(1))\n"
            "    def get(self) -> C:\n"
            "        return self._c\n"
            "    def via(self) -> C:\n"
            "        return self.get()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "via") is None

    def test_strview_field_return_takes_the_str_arm(self):
        # A str field returned as StrView takes the view-form split, NOT the
        # spanlike helper -- the invariant this pin has always guarded. The
        # shape now routes (via the view-target str coerce, coerce.str_field
        # _view); what must never happen is the spanlike field branch
        # claiming it, which would wrap the member read in as_span.
        src = (
            "from tpy import StrView\n"
            "class W:\n"
            "    s: str\n"
            "    def __init__(self, s: str) -> None:\n"
            "        self.s = s\n"
            "    def get_view(self) -> StrView:\n"
            "        return self.s\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "get_view") is not None
        assert faces.get("coerce.str_field_view", 0) == 1
        assert faces.get("coerce.span_array_literal", 0) == 0
        _assert_byte_identical(src)

    def test_qualified_ctor_at_mutated_ctor_slot_still_defers(self, tmp_path):
        # The module-qualified ctor slice is const-slot-only: at a MUTATED
        # ctor ref slot the rec ArgTemp's init would dead-end at the marker
        # result gate, so admission rejects the shape honestly.
        lib = self._setup(tmp_path)
        src = ("from pkg import sub\nfrom tpy import Int32\n"
               "class W:\n"
               "    n: Int32\n"
               "    def __init__(self, r: sub.Rec) -> None:\n"
               "        r.n = r.n + 1\n"
               "        self.n = r.n\n"
               "def f() -> None:\n"
               "    print(W(sub.Rec(Int32(7))).n)\n")
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "f") is None

    def test_conformer_ctor_rvalue_at_generic_structural_slot(self):
        # A handwritten-conformer CTOR rvalue at a generic callee's
        # structural slot (reachable via the generic-arg protocol row):
        # the un-spelled `auto __tmp_N = Ready(...);` hoist
        # (dualgen-verified byte-identical).
        src = (
            "from tpy import Int32, Own\n"
            "from tpy.coro import poll_once, poll_ready, Poll, Waker\n"
            "class Ready:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "    def __poll__(self, waker: Waker) -> Own[Poll[Int32]]:\n"
            "        return poll_ready(self.n)\n"
            "def f() -> None:\n"
            "    print(poll_once(Ready(7)).value())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert "auto __tmp_1 = Ready(7);" in _body(thir, "f")
        _assert_byte_identical(src)

    def test_qualified_nonctor_call_arg_still_defers(self, tmp_path):
        # A qualified NON-ctor record-returning call at the slot keeps
        # falling back (only the is_constructor slice rides the temp row).
        lib = self._setup(tmp_path)
        src = ("from pkg import sub\nfrom tpy import Int32\n"
               "def use(r: sub.Rec) -> Int32:\n    return r.n\n"
               "def f() -> None:\n    print(use(sub.make(Int32(9))))\n")
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "f") is None
