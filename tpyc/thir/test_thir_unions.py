"""THIR F4 unions. U1: value-form unions of scalar members (std::variant with
no borrow/storage duality) -- params/locals/returns/same-type call args render
bare, None renders std::monostate{}. Plus the structural form validator."""

from __future__ import annotations

import dataclasses

import pytest

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from ..typesys import INT32, UnionType, VoidType
from .lower import iter_module_constructors, lower_constructor, lower_module
from .nodes import (
    Form, THIRArgTemp, THIRAssert, THIRAssign, THIRBinOp, THIRCoerce, THIRCtorCall,
    THIRFormConvert, THIRFunction, THIRFunctionLayout, THIRIf, THIRIsinstance,
    THIRLiteral, THIRName, THIRNarrowAlias, THIRNarrowedRead, THIRReturn,
    THIRSelf, THIRUnionArgLift, THIRVarDecl, THIRWhile,
)
from .validate import (
    THIRValidationError, validate_constructor, validate_function,
)
from .testutil import (
    _assert_byte_identical, _compile, _entry, _fn, _lower, _lower_ctor,
    _lower_ctx, _lower_ctx_witnessed,
)

_PRELUDE = "from tpy import Int32, Int64, Float64\n"


class TestValueUnionEligibility:
    def test_union_param_and_return_route(self):
        thir = _lower(_PRELUDE + (
            "def f(v: Int32 | Float64) -> Int32 | Float64:\n"
            "    return v\n"))
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName)
        assert isinstance(ret.value.result_type, UnionType)

    def test_union_local_decl_and_reassign_route(self):
        thir = _lower(_PRELUDE + (
            "def g() -> Int32 | Float64:\n"
            "    x: Int32 | Float64 = 1\n"
            "    x = 2.5\n"
            "    return x\n"))
        fn = _fn(thir, "g")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.resolved_type, UnionType)

    def test_none_member_decl_reassign_return(self):
        thir = _lower(_PRELUDE + (
            "def n() -> Int32 | Float64 | None:\n"
            "    x: Int32 | Float64 | None = None\n"
            "    x = 3\n"
            "    return None\n"))
        fn = _fn(thir, "n")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRLiteral) and decl.init.value is None
        assert isinstance(decl.init.result_type, UnionType)
        ret = fn.body[2]
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None

    def test_same_union_compare_routes(self):
        thir = _lower(_PRELUDE + (
            "def c(a: Int32 | Float64, b: Int32 | Float64) -> bool:\n"
            "    return a == b\n"))
        assert _fn(thir, "c") is not None

    def test_union_vs_member_compare_rejected(self):
        # `a == k` (union vs member) renders the bare mixed pair on the AST
        # path -- invalid C++ (the union-operand BUGS.md class) -> AST path.
        thir = _lower(_PRELUDE + (
            "def c(a: Int32 | Float64, k: Int32) -> bool:\n"
            "    return a == k\n"))
        assert _fn(thir, "c") is None

    def test_narrowing_divergent_read_rejected(self):
        # `x` is assign-narrowed to Int32, so `v = x` reads member-typed but
        # the AST renders the bare variant name (invalid C++, BUGS.md) -> AST.
        thir = _lower(_PRELUDE + (
            "def m(k: Int32) -> Int32 | Float64:\n"
            "    x: Int32 | Float64 = k\n"
            "    v = x\n"
            "    return v\n"))
        assert _fn(thir, "m") is None

    def test_same_union_arg_passes_through(self):
        thir = _lower(_PRELUDE + (
            "def take(v: Int32 | Float64) -> Int32:\n"
            "    return 0\n"
            "def h(v: Int32 | Float64) -> Int32:\n"
            "    return take(v)\n"))
        assert _fn(thir, "h") is not None

    def test_member_valued_float_literal_arg_temps(self):
        # A float-literal member arg hoists a `__tmp_N` variant temp -- the
        # arg-temp row (the literal's FloatLiteralType resolves to the
        # union's double member).
        thir = _lower(_PRELUDE + (
            "def take(v: Int32 | Float64) -> Int32:\n"
            "    return 0\n"
            "def h() -> Int32:\n"
            "    return take(2.5)\n"))
        fn = _fn(thir, "h")
        assert fn is not None
        arg = fn.body[0].value.args[0]
        assert isinstance(arg, THIRArgTemp)
        assert isinstance(arg.init, THIRLiteral) and arg.init.value == 2.5

    def test_union_print_routes_str_visitor(self):
        # A union-typed NAME print arg streams via the `::tpy::__str__`
        # visitor (the union-returns wave's PrintForm.STR row).
        thir = _lower(_PRELUDE + (
            "def p(v: Int32 | Float64) -> None:\n"
            "    print(v)\n"))
        assert _fn(thir, "p") is not None

    def test_str_member_union_whole_variant_routes(self):
        # A str member is owned `std::string` in the value variant (no
        # borrow/storage duality at the WHOLE-variant positions -- param read,
        # return, same-union pass-through), so those render bare like a scalar
        # union. Only member INSERT (a view->owned arg temp) is form-relevant
        # and self-rejects via `_value_union_temp_slot`'s scalar-only check.
        thir = _lower(_PRELUDE + (
            "def take(v: Int32 | str) -> Int32:\n    return 0\n"
            "def f(v: Int32 | str) -> Int32 | str:\n    return v\n"
            "def g(v: Int32 | str) -> Int32:\n    return take(v)\n"))
        for name in ("take", "f", "g"):
            assert _fn(thir, name) is not None, name

    def test_char_member_union_routes(self):
        thir = _lower("from tpy import Int32, Char\n" + (
            "def f(v: Int32 | Char) -> Int32 | Char:\n    return v\n"
            "def c(a: Int32 | Char, b: Int32 | Char) -> bool:\n    return a == b\n"))
        for name in ("f", "c"):
            assert _fn(thir, name) is not None, name

    def test_str_member_valued_view_arg_rejects(self):
        # A str-view VALUE into a `Int32 | str` slot is a view->owned member
        # insert (`std::variant<...> __tmp = view;`) -- form-relevant, so the
        # arg temp stays on the AST path and the caller body rejects.
        thir = _lower(_PRELUDE + (
            "def take(v: Int32 | str) -> Int32:\n    return 0\n"
            "def f(s: str) -> Int32:\n    return take(s)\n"))
        assert _fn(thir, "f") is None

    def test_str_union_narrowed_print_routes_str_wrap(self):
        # Printing a narrowed union subject takes the AST's `__str__` wrap
        # (`__str__(__v)`) even after extraction -- gen_print keys the
        # DECLARED union (ctx.var_types) -- mirrored by PrintForm.STR keyed
        # on `lc.narrow.subject_union`.
        thir = _lower(_PRELUDE + (
            "def f(v: Int32 | str) -> None:\n"
            "    if isinstance(v, Int32):\n        print(v)\n"
            "    else:\n        print(v)\n"))
        assert _fn(thir, "f") is not None
        _assert_byte_identical(_PRELUDE + (
            "def f(v: Int32 | str) -> None:\n"
            "    if isinstance(v, Int32):\n        print(v)\n"
            "    else:\n        print(v)\n"))

    def test_member_literal_return_routes(self):
        # A member LITERAL returned into a value union renders bare -- the
        # variant converting ctor takes `1` / `"x"` directly (no BigInt /
        # Float32 slot wrap; lowering retypes the literal to the union).
        thir = _lower(_PRELUDE + (
            "def f(b: bool) -> Int32 | str:\n"
            "    if b:\n        return 1\n"
            "    return \"x\"\n"))
        assert _fn(thir, "f") is not None

    def test_member_scalar_name_return_routes(self):
        # A member-typed scalar NAME returned into the union (`return v`, v a
        # bare Int32) is value-form -- renders bare like the whole-variant read.
        thir = _lower(_PRELUDE + (
            "def f(v: Int32) -> Int32 | Float64:\n    return v\n"))
        assert _fn(thir, "f") is not None

    def test_char_member_literal_return_routes(self):
        thir = _lower("from tpy import Int32, Char\n" + (
            "def f(b: bool, c: Char) -> Int32 | Char:\n"
            "    if b:\n        return 1\n"
            "    return c\n"))
        assert _fn(thir, "f") is not None

    def test_three_member_str_union_return_routes(self):
        thir = _lower(_PRELUDE + (
            "def f(b: bool) -> Int32 | Float64 | str:\n"
            "    if b:\n        return 1\n"
            "    return \"x\"\n"))
        assert _fn(thir, "f") is not None

    def test_str_view_source_into_union_return_deferred(self):
        # A str param / local (std::string_view / pending view) returned into a
        # `... | str` slot is a view->owned member INSERT the union return arm
        # does not wire -- the same form-relevant boundary `_value_union_temp_
        # slot` rejects for arg inserts. The AST's bare `return s;` is itself a
        # miscompile here (BUGS.md), so DEFER: the whole body stays on the AST
        # path. An owned `str` LITERAL (value-form, target-typed) still routes.
        param = _lower(_PRELUDE + (
            "def f(s: str) -> Int32 | str:\n    return s\n"))
        assert _fn(param, "f") is None
        local = _lower(_PRELUDE + (
            "def f() -> Int32 | str:\n    s: str = \"hi\"\n    return s\n"))
        assert _fn(local, "f") is None


class TestValueUnionEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = _PRELUDE + (
        "def f(v: Int32 | Float64) -> Int32 | Float64:\n"
        "    return v\n"
        "def g() -> Int32 | Float64:\n"
        "    x: Int32 | Float64 = 1\n"
        "    x = 2.5\n"
        "    y = x\n"
        "    return y\n"
        "def n(v: Int32 | Float64 | None) -> Int32 | Float64 | None:\n"
        "    x: Int32 | Float64 | None = None\n"
        "    x = 3\n"
        "    return None\n"
        "def c(a: Int32 | Float64, b: Int32 | Float64) -> bool:\n"
        "    return a == b\n"
        "def take(v: Int32 | Float64) -> Int32:\n"
        "    return 0\n"
        "def h(v: Int32 | Float64) -> Int32:\n"
        "    return take(v)\n"
        "def main():\n"
        "    h(7)\n"
        "main()\n")

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_monostate_and_variant_render(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::variant<std::monostate, int32_t, double> x = std::monostate{};" in cpp
        assert "return std::monostate{};" in cpp
        assert "std::variant<int32_t, double> x = 1;" in cpp

    def test_routing_is_non_vacuous(self):
        # `g`/`n`/`c`/`h` must actually route (THIR path), not silently fall
        # back to AST -- the byte-diff alone cannot tell.
        thir = _lower(self.SRC)
        for name in ("f", "g", "n", "c", "h"):
            assert _fn(thir, name) is not None, name

    RET_SRC = _PRELUDE + (
        "def pick(b: bool) -> Int32 | str:\n"
        "    if b:\n        return 1\n"
        "    return \"x\"\n"
        "def scalar(v: Int32) -> Int32 | Float64:\n"
        "    return v\n"
        "def main():\n"
        "    pick(True)\n    scalar(3)\n"
        "main()\n")

    def test_member_return_byte_identical(self):
        assert self._cpp(self.RET_SRC, thir=True) \
            == self._cpp(self.RET_SRC, thir=False)

    def test_member_return_renders_bare(self):
        cpp = self._cpp(self.RET_SRC, thir=True)
        assert "return 1;" in cpp
        assert 'return "x";' in cpp
        assert "return v;" in cpp

    def test_member_return_routes(self):
        thir = _lower(self.RET_SRC)
        for name in ("pick", "scalar"):
            assert _fn(thir, name) is not None, name


_PTR_RECORDS = (
    "from tpy import Int32, Own, readonly\n"
    "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
    "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
    "class H:\n"
    "    u: A | B\n"
    "    n: Int32\n"
    "    def __init__(self, v: Own[A | B]):\n"
    "        self.u = v\n        self.n = 0\n"
)


class TestPtrUnionEligibility:
    def _lower(self, src: str):
        return _lower_ctx(_PTR_RECORDS + src)

    def test_param_return_local_arg_passthrough(self):
        thir = self._lower(
            "def take(v: A | B) -> Int32:\n    return 0\n"
            "def fwd(v: A | B) -> A | B:\n    return v\n"
            "def loc(v: A | B) -> Int32:\n    w = v\n    return take(w)\n")
        for name in ("take", "fwd", "loc"):
            assert _fn(thir, name) is not None, name

    def test_field_read_local_routes_const_and_mut(self):
        thir = self._lower(
            "def take(v: A | B) -> Int32:\n    return 0\n"
            "def ro(h: H) -> Int32:\n    w = h.u\n    return take(w)\n"
            "def mu(h: H) -> Int32:\n    w = h.u\n    h.n = 1\n    return take(w)\n")
        for name in ("ro", "mu"):
            fn = _fn(thir, name)
            assert fn is not None, name
            decl = fn.body[0]
            assert isinstance(decl, THIRVarDecl)
            assert isinstance(decl.init, THIRFormConvert)
            assert decl.init.form is Form.BORROW
        ro = _fn(thir, "ro").body[0]
        mu = _fn(thir, "mu").body[0]
        assert ro.is_const and ro.init.is_const
        assert not mu.is_const and not mu.init.is_const

    def test_name_reseat_routes_field_reseat_rejects(self):
        thir = self._lower(
            "def take(v: A | B) -> Int32:\n    return 0\n"
            "def rs(v: A | B, v2: A | B) -> Int32:\n"
            "    w = v\n    take(w)\n    w = v2\n    return take(w)\n"
            "def rsf(h: H, g: H) -> Int32:\n"
            "    w = h.u\n    take(w)\n    w = g.u\n    return take(w)\n")
        assert _fn(thir, "rs") is not None
        assert _fn(thir, "rsf") is None  # field reseat: const chain -> AST

    def test_field_write_from_name_routes(self):
        thir = self._lower(
            "def wf(h: H, v: A | B) -> None:\n    h.u = v\n")
        fn = _fn(thir, "wf")
        assert fn is not None
        w = fn.body[0]
        assert isinstance(w.value, THIRFormConvert)
        assert w.value.form is Form.STORAGE

    def test_field_write_from_field_routes_bare(self):
        # A field-to-field union copy is storage-to-storage on the AST path
        # (a field source is not a ptr-variant source): a plain assign with
        # no to_value_variant lift.
        thir = self._lower("def w1(h: H, g: H) -> None:\n    h.u = g.u\n")
        fn = _fn(thir, "w1")
        assert fn is not None
        w = fn.body[0]
        assert not isinstance(w.value, THIRFormConvert)
        assert w.value.form is Form.STORAGE

    def test_field_write_from_member_rejects(self):
        # A member-valued union field write rides the gen_call_arg cascade
        # cell; the write gate must reject it, like the reseat pair.
        thir = self._lower("def w2(h: H, a2: A) -> None:\n    h.u = a2\n")
        assert _fn(thir, "w2") is None

    def test_member_name_return_takes_address(self):
        # `return d` (a MEMBER record name) takes the AST's `&(d)` address-of
        # lift into the pointer variant, which the return arm now mirrors
        # (`ret.narrowed_union_addr`); the address is a param's, so nothing
        # local escapes. Same-union names keep returning bare.
        thir = self._lower("def wrap(a2: A) -> A | B:\n    return a2\n")
        fn = _fn(thir, "wrap")
        assert fn is not None
        assert fn.body[0].value.addr_of

    def test_storage_field_return_rejects(self):
        # `return h.u` emits the plain `&(...)` lift on the AST path (invalid
        # C++, BUGS.md) -> AST path.
        thir = self._lower("def r(h: H) -> A | B:\n    return h.u\n")
        assert _fn(thir, "r") is None

    def test_field_as_arg_rejects(self):
        # `take(h.u)` renders the bare storage field on the AST path (invalid
        # C++, BUGS.md) -> AST path.
        thir = self._lower(
            "def take(v: A | B) -> Int32:\n    return 0\n"
            "def fa(h: H) -> Int32:\n    return take(h.u)\n")
        assert _fn(thir, "fa") is None

    def test_own_union_param_rejects(self):
        thir = self._lower(
            "def take(v: A | B) -> Int32:\n    return 0\n"
            "def op(v: Own[A | B]) -> Int32:\n    return take(v)\n")
        assert _fn(thir, "op") is None

    def test_readonly_slot_arg_const_wraps(self):
        # A readonly ptr-variant slot takes _gen_union_arg's
        # ptr_variant_to_const wrap -- mirrored (render pinned in
        # TestUnionCallArgLift.test_readonly_slot_union_name_arg_const_wraps).
        thir = self._lower(
            "def take(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def ra(v: A | B) -> Int32:\n    return take(v)\n")
        assert _fn(thir, "ra") is not None

    def test_ctor_mil_own_move_routes(self):
        ctor = _lower_ctor(_PTR_RECORDS, "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert ctor.mil_inits[0].value.form is Form.STORAGE


class TestPtrUnionEmit:
    def _cpp(self, src: str, thir: bool):
        # hpp + cpp: the ctor MIL is emitted inline in the header.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = _PTR_RECORDS + (
        "def take(v: A | B) -> Int32:\n    return 0\n"
        "def fwd(v: A | B) -> A | B:\n    return v\n"
        "def ro(h: H) -> Int32:\n    w = h.u\n    return take(w)\n"
        "def mu(h: H) -> Int32:\n    w = h.u\n    h.n = 1\n    return take(w)\n"
        "def rs(v: A | B, v2: A | B) -> Int32:\n"
        "    w = v\n    take(w)\n    w = v2\n    return take(w)\n"
        "def wf(h: H, v: A | B) -> None:\n    h.u = v\n"
        "def main():\n"
        "    h = H(A(1))\n"
        "    mu(h)\n"
        "main()\n")

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_conversion_renders(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::variant<const A*, const B*> w = ::tpy::to_const_ptr_variant(h.u);" in cpp
        assert "std::variant<A*, B*> w = ::tpy::to_ptr_variant(h.u);" in cpp
        assert "h.u = ::tpy::to_value_variant<std::variant<A, B>>(v);" in cpp
        assert "inline H::H(std::variant<A, B>&& v) : u(std::move(v)), n(0) {}" in cpp

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("take", "fwd", "ro", "mu", "rs", "wf"):
            assert _fn(thir, name) is not None, name


_PTR_NONE_RECORDS = (
    "from tpy import Int32\n"
    "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
    "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
    "class S:\n"
    "    p: A | B | None\n"
    "    def __init__(self, v: A | B | None):\n"
    "        self.p = v\n"
)


class TestPtrUnionNoneEligibility:
    """The U2 monostate write arms: a None member makes the ptr union
    `std::variant<std::monostate, A*, B*>`; None writes store the monostate
    member bare at decls, reseats, field writes, and returns."""

    def _lower(self, src: str):
        return _lower_ctx(_PTR_NONE_RECORDS + src)

    def test_none_decl_and_reseat_route(self):
        thir = self._lower(
            "def dn(v: A | B | None) -> Int32:\n"
            "    w: A | B | None = None\n"
            "    w = v\n"
            "    w = None\n"
            "    return 0\n")
        fn = _fn(thir, "dn")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.init, THIRLiteral) and decl.init.value is None
        assert isinstance(decl.init.result_type, UnionType)

    def test_none_field_write_routes(self):
        thir = self._lower("def wn(s: S) -> None:\n    s.p = None\n")
        fn = _fn(thir, "wn")
        assert fn is not None
        w = fn.body[0]
        assert isinstance(w.value, THIRLiteral) and w.value.value is None
        assert isinstance(w.value.result_type, UnionType)

    def test_none_return_routes(self):
        thir = self._lower(
            "def rn(v: A | B | None) -> A | B | None:\n    return None\n")
        fn = _fn(thir, "rn")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None
        assert isinstance(ret.value.result_type, UnionType)

    def test_passthrough_routes(self):
        thir = self._lower(
            "def fwd(v: A | B | None) -> A | B | None:\n    return v\n"
            "def wf(s: S, v: A | B | None) -> None:\n    s.p = v\n"
            "def cp(s1: S, s2: S) -> None:\n    s1.p = s2.p\n")
        for name in ("fwd", "wf", "cp"):
            assert _fn(thir, name) is not None, name

    def test_unused_non_f1_member_union_routes(self):
        thir = self._lower(
            "def f(v: Int32 | A | None) -> Int32:\n    return 0\n")
        assert _fn(thir, "f") is not None


class TestPtrUnionNoneEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = _PTR_NONE_RECORDS + (
        "def dn(v: A | B | None) -> A | B | None:\n"
        "    w: A | B | None = None\n"
        "    w = v\n"
        "    w = None\n"
        "    return v\n"
        "def wn(s: S) -> None:\n    s.p = None\n"
        "def cp(s1: S, s2: S) -> None:\n    s1.p = s2.p\n"
        "def rn(v: A | B | None) -> A | B | None:\n    return None\n"
        "def main():\n"
        "    a = A(1)\n"
        "    pv: A | B | None = a\n"
        "    s1 = S(pv)\n"
        "    s2 = S(pv)\n"
        "    wn(s1)\n"
        "    cp(s1, s2)\n"
        "main()\n")

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_monostate_and_bare_copy_render(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert ("std::variant<std::monostate, A*, B*> w = std::monostate{};"
                in cpp)
        assert "    w = std::monostate{};" in cpp
        assert "s.p = std::monostate{};" in cpp
        assert "s1.p = s2.p;" in cpp
        assert "return std::monostate{};" in cpp

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("dn", "wn", "cp", "rn"):
            assert _fn(thir, name) is not None, name


class TestValidator:
    def _valid_fn(self, body) -> THIRFunction:
        return THIRFunction(name="t", params=(), return_type=VoidType(),
                            body=tuple(body), layout=THIRFunctionLayout())

    def test_noop_form_convert_raises(self):
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.STORAGE)
        bad = THIRFormConvert(result_type=INT32, value=inner, form=Form.STORAGE)
        fn = self._valid_fn([THIRReturn(value=bad)])
        with pytest.raises(THIRValidationError, match="no-op form convert"):
            validate_function(fn)

    def test_form_changing_convert_passes(self):
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.BORROW)
        ok = THIRFormConvert(result_type=INT32, value=inner, form=Form.STORAGE)
        validate_function(self._valid_fn([THIRReturn(value=ok)]))

    def test_coerce_form_lie_raises(self):
        # A non-view-target coerce must carry its inner form (the F6 review
        # finding this validator exists to catch).
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.STORAGE)
        bad = THIRCoerce(result_type=INT32, expr=inner,
                         coercion_name="int_literal", form=Form.VALUE)
        fn = self._valid_fn([THIRReturn(value=bad)])
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_function(fn)

    def test_corpus_units_still_validate(self):
        # The validator runs inside lower_function; any unit in this file
        # lowering successfully already exercises it. Sanity-check one shape
        # with a genuine THIRFormConvert (str view->owned).
        thir = _lower('def s(v: str) -> str:\n    t: str = v\n    return t\n')
        assert _fn(thir, "s") is not None

    # --- sink-position raise paths (U2): strip the convert off a GOOD
    # --- lowering and assert the validator screams. Built inside a compiler
    # --- context so the pointer-repr predicates resolve.

    def _lowered_in_ctx(self, src: str):
        compiler, modules = _compile(_PTR_RECORDS + src)
        entry = _entry(modules)
        with activate_compiler(compiler):
            return lower_module(entry.ast, entry.analyzer)

    def test_borrow_at_pointer_lifted_field_write_raises(self):
        thir = self._lowered_in_ctx(
            "def wf(h: H, v: A | B) -> None:\n    h.u = v\n")
        fn = _fn(thir, "wf")
        good = fn.body[0]
        bad = dataclasses.replace(good, value=good.value.value)
        with pytest.raises(THIRValidationError,
                           match="pointer-lifted field-write"):
            validate_function(dataclasses.replace(fn, body=(bad,)))

    def test_borrow_at_mil_cell_raises(self):
        compiler, modules = _compile(_PTR_RECORDS)
        entry = _entry(modules)
        with activate_compiler(compiler):
            for rec, init, self_type in iter_module_constructors(
                    entry.ast, entry.analyzer):
                if rec.name != "H":
                    continue
                ctor = lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
                mil = ctor.mil_inits[0]
                bad = dataclasses.replace(
                    mil, value=dataclasses.replace(mil.value,
                                                   form=Form.BORROW))
                with pytest.raises(THIRValidationError, match="MIL cell"):
                    validate_constructor(
                        dataclasses.replace(ctor, mil_inits=(bad,)))
                return
        raise AssertionError("H ctor not lowered")

    def test_borrow_return_of_value_type_raises(self):
        bad = THIRReturn(value=THIRLiteral(result_type=INT32, value=1,
                                           form=Form.BORROW))
        fn = THIRFunction(name="t", params=(), return_type=INT32,
                          body=(bad,), layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError, match="BORROW return"):
            validate_function(fn)


_THREE_RECORDS = _PRELUDE + _PTR_RECORDS + (
    "class C:\n    z: Int32\n    def __init__(self, z: Int32):\n        self.z = z\n"
)


class TestNarrowingEligibility:
    """F4 U3/U4: isinstance-narrowing reads (if/elif/else + the early-return
    implicit else), while-isinstance (loop-entry extraction),
    assert-isinstance (persistent extraction + the re-assert suffix bump),
    and compound `and` conditions (inline deref reads). Writes to the
    narrowed subject, `or` conditions, and multi-subject compounds stay on
    the AST path; readonly subjects route with const-qualified
    alternatives (F2)."""

    def _lower(self, src: str):
        return _lower_ctx(_THREE_RECORDS + src)

    def test_two_member_if_else_routes(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    else:\n        return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        node = fn.body[0]
        assert isinstance(node, THIRIf)
        assert isinstance(node.condition, THIRIsinstance)
        assert node.condition.member_cpps == ("A*",)
        alias = node.then_body[0]
        assert isinstance(alias, THIRNarrowAlias)
        assert alias.alias == "__v" and alias.is_ptr_variant
        assert not alias.const_ref  # ptr-variant alias is `auto&`
        # reads inside the branch renamed to the alias
        ret = node.then_body[1]
        assert isinstance(ret.value.receiver, THIRName)
        assert ret.value.receiver.name == "__v"
        # the else branch extracts the complement member
        el = node.else_body[0]
        assert isinstance(el, THIRNarrowAlias) and el.member_cpp == "B*"

    def test_isinstance_narrow_with_hoist_is_ineligible(self):
        # An isinstance-narrowing condition combined with a sema-hoisted
        # branch-decl (`x` definitely-assigned after the if) is deferred: the
        # alias-scope / predecl interaction is not part of this slice.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        x = v.x\n"
            "    else:\n        x = v.y\n"
            "    return x\n")
        assert _fn(thir, "f") is None

    def test_post_if_alias_and_scope(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRIf)
        post = fn.body[1]
        assert isinstance(post, THIRNarrowAlias)
        assert post.member_cpp == "B*" and post.no_source_comment
        ret = fn.body[2]
        assert ret.value.receiver.name == "__v"

    def test_exhaustive_elif_folds_to_nested_true(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    elif isinstance(v, B):\n        return v.y\n"
            "    return -1\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        assert outer.else_is_nested  # concrete else-fact breaks the flat chain
        inner = outer.else_body[0]
        assert isinstance(inner, THIRIf)
        assert isinstance(inner.condition, THIRLiteral)
        assert inner.condition.value is True  # the exhaustiveness fold
        assert isinstance(inner.then_body[0], THIRNarrowAlias)
        # the dead implicit-else is suppressed: no post-if alias after outer
        assert not isinstance(fn.body[1], THIRNarrowAlias)

    def test_three_member_elif_chain_flattens(self):
        thir = self._lower(
            "def f(v: A | B | C) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    elif isinstance(v, B):\n        return v.y\n"
            "    else:\n        return v.z\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        # intermediate else-fact is the remaining union -> flat `else if`
        assert not outer.else_is_nested
        inner = outer.else_body[0]
        assert isinstance(inner, THIRIf)
        assert isinstance(inner.condition, THIRIsinstance)
        assert isinstance(inner.else_body[0], THIRNarrowAlias)
        assert inner.else_body[0].member_cpp == "C*"

    def test_chain_post_if_alias_at_enclosing_scope(self):
        # The early-return implicit else belongs to the LAST link of the flat
        # elif chain and its alias emits at the ENCLOSING scope (the AST runs
        # post-narrowing on chain[-1]) -- not inside the else arm.
        thir = self._lower(
            "def f(v: A | B | C) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    elif isinstance(v, B):\n        return v.y\n"
            "    return v.z\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        assert not outer.else_is_nested
        post = fn.body[1]
        assert isinstance(post, THIRNarrowAlias) and post.member_cpp == "C*"
        assert fn.body[2].value.receiver.name == "__v"

    def test_persistent_alias_suffix_bump(self):
        # Unreachable from the routed slice today (a second post-if on the
        # same subject is rejected as a re-dispatch), but the assert-isinstance
        # cell will reach it -- pin _fresh_alias_local's bump mirror directly.
        from .lower import _LowerCtx, _persistent_alias_name
        compiler, modules = _compile(_PRELUDE + "def f() -> Int32:\n    return 0\n")
        entry = _entry(modules)
        lc = _LowerCtx(entry.ast.functions[0], entry.analyzer, None)
        assert _persistent_alias_name("v", lc) == "__v"
        lc.narrow.persistent_aliases.add("__v")
        assert _persistent_alias_name("v", lc) == "__v_2"
        lc.narrow.persistent_aliases.add("__v_2")
        assert _persistent_alias_name("v", lc) == "__v_3"

    def test_assign_node_rebind_of_narrowed_rejects(self):
        # The parser emits TpyVarDecl for every ordinary name-target assign; a
        # name-target TpyAssign only arises from macro-authored / frontend-IR
        # ASTs. Lowering must reject a narrowed-subject rebind there too -- a
        # routed rebind would leave later reads on the stale extraction alias.
        from ..parse.nodes import TpyAssign, TpyName
        from .fallback import ThirUnsupported
        from .lower import _LowerCtx
        from .lower.statements import _lower_stmt
        compiler, modules = _compile(_PRELUDE + (
            "def f(v: Int32 | Float64, v2: Int32 | Float64) -> Int32:\n"
            "    x = v2\n"
            "    return 0\n"))
        entry = _entry(modules)
        an = entry.analyzer
        fn = entry.ast.functions[0]
        declared = {n: t for n, t in fn.params}
        synthetic = TpyAssign(target=TpyName("v"), value=fn.body[0].init)
        lc = _LowerCtx(fn, an, None)
        lc.narrow.narrowed["v"] = "__v"
        with pytest.raises(ThirUnsupported, match="stmt.assign"):
            _lower_stmt(synthetic, lc, declared)

    def test_narrowing_inside_loop_body_scopes(self):
        # Narrow inside a for body; the scope pops at the loop's closing
        # brace, so a fresh isinstance on the SAME subject after the loop is
        # still admitted (a leaked scope would reject it as a re-dispatch).
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    for i in range(2):\n"
            "        if isinstance(v, A):\n            print(v.x)\n"
            "    if isinstance(v, B):\n        return v.y\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        loop_if = fn.body[0].body[0]
        assert isinstance(loop_if.then_body[0], THIRNarrowAlias)
        assert isinstance(fn.body[1].then_body[0], THIRNarrowAlias)
        assert fn.body[1].then_body[0].member_cpp == "B*"

    def test_plain_headed_chain_post_if(self):
        # A PLAIN-condition head whose chain ends in a narrowing elif: the
        # early-return fact still belongs to the last link and the alias
        # emits at the enclosing scope.
        thir = self._lower(
            "def f(flag: bool, v: A | B) -> Int32:\n"
            "    if flag:\n        return 0\n"
            "    elif isinstance(v, A):\n        return v.x\n"
            "    return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        assert not isinstance(outer.condition, THIRIsinstance)
        assert isinstance(outer.else_body[0], THIRIf)
        post = fn.body[1]
        assert isinstance(post, THIRNarrowAlias) and post.member_cpp == "B*"
        assert fn.body[2].value.receiver.name == "__v"

    def test_const_pointee_local_narrowing(self):
        # A U2 const-lifted ptr-variant local (field read off an unmutated
        # receiver) narrowed via isinstance: the const chain reaches BOTH the
        # holds_alternative template arg and the extraction.
        thir = self._lower(
            "def f(h: H) -> Int32:\n"
            "    w = h.u\n"
            "    if isinstance(w, A):\n        return w.x\n"
            "    return w.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        node = fn.body[1]
        assert node.condition.member_cpps == ("const A*",)
        alias = node.then_body[0]
        assert alias.member_cpp == "const A*" and alias.is_ptr_variant
        assert fn.body[2].member_cpp == "const B*"  # post-if complement

    def test_two_subject_nested_narrowing(self):
        # Independent aliases for two different subjects in one nest; the
        # inner post-if alias (unused) still emits inside the outer branch.
        thir = self._lower(
            "def f(v: A | B, w: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n"
            "        if isinstance(w, B):\n            return v.x + w.y\n"
            "        return v.x\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        assert outer.then_body[0].alias == "__v"
        inner = outer.then_body[1]
        assert inner.then_body[0].alias == "__w"
        ret = inner.then_body[1]
        assert ret.value.left.receiver.name == "__v"
        assert ret.value.right.receiver.name == "__w"
        # the inner post-if complement alias for w, inside v's branch
        assert isinstance(outer.then_body[2], THIRNarrowAlias)
        assert outer.then_body[2].alias == "__w"
        assert outer.then_body[2].member_cpp == "A*"

    def test_narrowed_method_receiver_routes(self):
        # `v.sound()` on the narrowed subject routes through the record
        # method-call row (call-arg cascade): the read renames to the `T&`
        # extraction alias on both the branch narrowing and the early-return
        # post-if complement.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class A:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    def sound(self) -> Int32:\n        return self.x\n"
            "class B:\n    y: Int32\n"
            "    def __init__(self, y: Int32):\n        self.y = y\n"
            "    def sound(self) -> Int32:\n        return self.y\n"
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.sound()\n"
            "    return v.sound()\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0].then_body[-1]
        assert ret.value.receiver.name == "__v"

    def test_narrowed_record_call_arg_routes(self):
        # A record arg (`use(v)` with v narrowed to A) routes through the
        # record call-arg row: the retyped subject renames to its alias.
        thir = self._lower(
            "def use(a2: A) -> Int32:\n    return a2.x\n"
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return use(v)\n"
            "    return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0].then_body[-1]
        assert ret.value.args[0].name == "__v"

    def test_write_to_narrowed_subject_rejects(self):
        # Rebinding the narrowed LOCAL inside the branch writes through the
        # alias on the AST path -- out of the slice. (A param subject can't
        # even be reassigned -- sema rejects that outright.)
        thir = self._lower(
            "def f(v: A | B, v2: A | B) -> Int32:\n"
            "    w = v\n"
            "    if isinstance(w, A):\n        w = v2\n"
            "    return 0\n")
        assert _fn(thir, "f") is None

    def test_readonly_subject_routes_const_alternatives(self):
        # F2: a readonly-qualified subject narrows with const-qualified
        # alternatives (`std::get<const A*>`), dualgen-verified identical
        # incl. the post-if implicit-else extraction.
        src = _THREE_RECORDS + (
            "def f(v: readonly[A | B]) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    return v.y\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_while_isinstance_routes(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    while isinstance(v, A):\n        return v.x\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        w = fn.body[0]
        assert isinstance(w, THIRWhile)
        assert isinstance(w.condition, THIRIsinstance)
        assert isinstance(w.body[0], THIRNarrowAlias)
        assert w.body[0].alias == "__v" and w.body[0].member_cpp == "A*"
        assert w.body[1].value.receiver.name == "__v"

    def test_while_isinstance_scope_pops(self):
        # The loop alias pops at the closing brace: a fresh isinstance on the
        # SAME subject after the loop still routes, with the base alias name.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    while isinstance(v, A):\n        return v.x\n"
            "    if isinstance(v, B):\n        return v.y\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRIf)
        assert fn.body[1].then_body[0].alias == "__v"

    def test_while_body_assert_same_subject_rejects(self):
        # The AST redeclares the loop alias here (`auto& __v` twice in one
        # block -- the BUGS.md _gen_while persistent=False collision), so the
        # shape must stay on the AST path, not be mirrored.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    while isinstance(v, A):\n"
            "        assert isinstance(v, A)\n"
            "        return v.x\n"
            "    return 0\n")
        assert _fn(thir, "f") is None

    def test_assert_isinstance_routes(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    assert isinstance(v, A)\n"
            "    return v.x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        a, alias, ret = fn.body
        assert isinstance(a, THIRAssert) and a.message is None
        assert isinstance(a.condition, THIRIsinstance)
        assert isinstance(alias, THIRNarrowAlias) and alias.alias == "__v"
        assert ret.value.receiver.name == "__v"

    def test_assert_messages_route(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            '    assert isinstance(v, A), "want A"\n'
            "    return v.x\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].message == "want A"
        thir = self._lower(
            "def g(v: A | B, m: str) -> Int32:\n"
            "    assert isinstance(v, A), m\n"
            "    return v.x\n")
        g = _fn(thir, "g")
        assert g is not None and isinstance(g.body[0].message, THIRName)

    def test_reassert_suffix_bump(self):
        # Sema folds the second condition to `true`; the extraction re-runs
        # with the suffix-bumped alias against the original variant.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    assert isinstance(v, A)\n"
            "    assert isinstance(v, A)\n"
            "    return v.x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        a2, alias2 = fn.body[2], fn.body[3]
        assert isinstance(a2, THIRAssert)
        assert isinstance(a2.condition, THIRLiteral) and a2.condition.value is True
        assert isinstance(alias2, THIRNarrowAlias) and alias2.alias == "__v_2"
        assert fn.body[4].value.receiver.name == "__v_2"

    def test_post_if_then_reassert_bump(self):
        # A post-if persistent alias counts for the re-assert bump too (both
        # are statement-level registrations).
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    assert isinstance(v, B)\n"
            "    return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRNarrowAlias)  # post-if `__v` (B*)
        assert isinstance(fn.body[2], THIRAssert)
        assert isinstance(fn.body[2].condition, THIRLiteral)
        assert fn.body[3].alias == "__v_2"
        assert fn.body[4].value.receiver.name == "__v_2"

    def test_plain_assert_routes(self):
        thir = self._lower(
            "def f(x: Int32) -> Int32:\n"
            "    assert x > 0\n"
            "    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRAssert)
        assert len(fn.body) == 2  # no extraction alias

    def test_assert_union_fact_no_alias(self):
        # `isinstance(v, (A, B))` extracts nothing -- the holds-OR test emits
        # bare and the subject stays the variant.
        thir = self._lower(
            "def f(v: A | B | C) -> Int32:\n"
            "    assert isinstance(v, (A, B))\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        a = fn.body[0]
        assert isinstance(a, THIRAssert)
        assert a.condition.member_cpps == ("A*", "B*")
        assert not isinstance(fn.body[1], THIRNarrowAlias)

    def test_compound_assert_routes(self):
        # The RHS read renders as the inline deref (no alias yet); the
        # persistent alias follows the assert line.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    assert isinstance(v, A) and v.x > 0\n"
            "    return v.x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        a, alias, ret = fn.body
        assert isinstance(a, THIRAssert)
        assert isinstance(a.condition.left, THIRIsinstance)
        assert a.condition.left.member_cpps == ("A*",)
        rhs_recv = a.condition.right.left.receiver
        assert isinstance(rhs_recv, THIRNarrowedRead)
        assert rhs_recv.variant_cpp == "v" and rhs_recv.member_cpp == "A*"
        assert rhs_recv.is_ptr_variant
        assert isinstance(alias, THIRNarrowAlias) and alias.alias == "__v"
        assert ret.value.receiver.name == "__v"

    def test_compound_condition_routes(self):
        # isinstance in either operand position; reads before the test (g4
        # shape) never mention the subject, reads after rename inline.
        thir = self._lower(
            "def f(v: A | B, flag: bool) -> Int32:\n"
            "    if isinstance(v, A) and flag:\n        return v.x\n"
            "    return 0\n"
            "def g(v: A | B, flag: bool) -> Int32:\n"
            "    if flag and isinstance(v, A):\n        return v.x\n"
            "    return 0\n")
        for name in ("f", "g"):
            fn = _fn(thir, name)
            assert fn is not None, name
            node = fn.body[0]
            assert isinstance(node, THIRIf)
            assert isinstance(node.then_body[0], THIRNarrowAlias)
            assert node.then_body[1].value.receiver.name == "__v"
        # no post-if fact from a compound early-return (else facts are weak)
        fn = _fn(thir, "f")
        assert not isinstance(fn.body[1], THIRNarrowAlias)

    def test_compound_value_union_inline_read(self):
        thir = self._lower(
            "def f(v: Int32 | Float64) -> Int32:\n"
            "    if isinstance(v, Int32) and v > 0:\n        return v\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        read = fn.body[0].condition.right.left
        assert isinstance(read, THIRNarrowedRead)
        assert read.variant_cpp == "v" and read.member_cpp == "int32_t"
        assert not read.is_ptr_variant

    def test_assert_narrow_in_branch_pops(self):
        # A persistent assert narrowing made INSIDE a branch pops at the
        # closing brace: the post-branch isinstance on the same subject still
        # routes with the base alias name (no bump, no re-dispatch reject).
        thir = self._lower(
            "def f(v: A | B, flag: bool) -> Int32:\n"
            "    if flag:\n"
            "        assert isinstance(v, A)\n"
            "        return v.x\n"
            "    if isinstance(v, B):\n        return v.y\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        branch = fn.body[0].then_body
        assert isinstance(branch[0], THIRAssert)
        assert isinstance(branch[1], THIRNarrowAlias) and branch[1].alias == "__v"
        assert isinstance(fn.body[1], THIRIf)
        assert fn.body[1].then_body[0].alias == "__v"

    def test_compound_not_and_chained_leaves(self):
        # The chained pair reads the narrowed subject through the inline get.
        thir = self._lower(
            "def f(v: A | B, flag: bool) -> Int32:\n"
            "    if isinstance(v, A) and not flag:\n        return v.x\n"
            "    return 0\n"
            "def g(v: A | B) -> Int32:\n"
            "    if isinstance(v, A) and 0 < v.x < 10:\n        return v.x\n"
            "    return 0\n")
        for name in ("f", "g"):
            assert _fn(thir, name) is not None, name
        pair0 = _fn(thir, "g").body[0].condition.right.left  # (0 < v.x)
        assert isinstance(pair0.right.receiver, THIRNarrowedRead)

    def test_assert_dump_renders(self):
        from .dump import dump_thir
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            '    assert isinstance(v, A), "want A"\n'
            "    return v.x\n")
        assert "assert isinstance(%v, [A*]), 'want A'" in dump_thir(thir)

    def test_compound_or_and_multi_isinstance_reject(self):
        thir = self._lower(
            "def f(v: A | B, flag: bool) -> Int32:\n"
            "    if isinstance(v, A) or flag:\n        return 0\n"
            "    return 0\n"
            "def g(v: A | B, w: A | B) -> Int32:\n"
            "    if isinstance(v, A) and isinstance(w, B):\n        return v.x\n"
            "    return 0\n")
        assert _fn(thir, "f") is None
        assert _fn(thir, "g") is None

    def test_unused_alias_still_emitted(self):
        # The AST extracts at branch entry even when the branch never reads
        # the narrowed name.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return 10\n"
            "    return v.y\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].then_body[0], THIRNarrowAlias)

    def test_value_union_narrowing_routes(self):
        thir = _lower(_PRELUDE + (
            "def f(v: Int32 | Float64) -> Int32:\n"
            "    if isinstance(v, Int32):\n        return v\n"
            "    return 7\n"))
        fn = _fn(thir, "f")
        assert fn is not None
        alias = fn.body[0].then_body[0]
        assert isinstance(alias, THIRNarrowAlias)
        assert not alias.is_ptr_variant
        assert alias.const_ref  # value-type union PARAM -> `const auto&`
        assert alias.member_cpp == "int32_t"

    def test_value_union_local_alias_is_mutable_ref(self):
        thir = _lower(_PRELUDE + (
            "def f(v: Int32 | Float64) -> Int32:\n"
            "    x = v\n"
            "    if isinstance(x, Int32):\n        return x\n"
            "    return 7\n"))
        fn = _fn(thir, "f")
        assert fn is not None
        alias = fn.body[1].then_body[0]
        assert isinstance(alias, THIRNarrowAlias)
        assert not alias.const_ref  # local -> `auto&`

    def test_tuple_form_check_routes_without_extraction(self):
        # `isinstance(v, (A, B))` on a 3-member union: the then-fact is the
        # remaining union -- condition ORs, no alias, reads of v stay bare.
        thir = self._lower(
            "def f(v: A | B | C) -> Int32:\n"
            "    if isinstance(v, (A, B)):\n        return 1\n"
            "    return v.z\n")
        fn = _fn(thir, "f")
        assert fn is not None
        node = fn.body[0]
        assert isinstance(node.condition, THIRIsinstance)
        assert node.condition.member_cpps == ("A*", "B*")
        assert not isinstance(node.then_body[0], THIRNarrowAlias)
        # the implicit else IS concrete (C) -> post-if alias
        assert isinstance(fn.body[1], THIRNarrowAlias)
        assert fn.body[1].member_cpp == "C*"


class TestNarrowingEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = _THREE_RECORDS + (
        "def two(v: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    else:\n        return v.y\n"
        "def post(v: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    return v.y\n"
        "def exhaust(v: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    elif isinstance(v, B):\n        return v.y\n"
        "    return -1\n"
        "def three(v: A | B | C) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    elif isinstance(v, B):\n        return v.y\n"
        "    else:\n        return v.z\n"
        "def val(v: Int32 | Float64) -> Int32:\n"
        "    if isinstance(v, Int32):\n        return v\n"
        "    return 7\n"
        "def chain(v: A | B | C) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    elif isinstance(v, B):\n        return v.y\n"
        "    return v.z\n"
        "def loop(v: A | B) -> Int32:\n"
        "    for i in range(2):\n"
        "        if isinstance(v, A):\n            print(v.x)\n"
        "    if isinstance(v, B):\n        return v.y\n"
        "    return 0\n"
        "def plain_head(flag: bool, v: A | B) -> Int32:\n"
        "    if flag:\n        return 0\n"
        "    elif isinstance(v, A):\n        return v.x\n"
        "    return v.y\n"
        "def constw(h: H) -> Int32:\n"
        "    w = h.u\n"
        "    if isinstance(w, A):\n        return w.x\n"
        "    return w.y\n"
        "def two_vars(v: A | B, w: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n"
        "        if isinstance(w, B):\n            return v.x + w.y\n"
        "        return v.x\n"
        "    return 0\n"
        "def drain(v: A | B) -> Int32:\n"
        "    while isinstance(v, A):\n        return v.x\n"
        "    return 0\n"
        "def chk(v: A | B) -> Int32:\n"
        '    assert isinstance(v, A), "want A"\n'
        "    return v.x\n"
        "def rechk(v: A | B) -> Int32:\n"
        "    assert isinstance(v, A)\n"
        "    assert isinstance(v, A)\n"
        "    return v.x\n"
        "def comp(v: A | B) -> Int32:\n"
        "    if isinstance(v, A) and v.x > 0:\n        return v.x\n"
        "    return 0\n"
        "def compw(v: A | B) -> Int32:\n"
        "    while isinstance(v, A) and v.x > 0:\n        return v.x\n"
        "    return 0\n"
        "def compv(v: Int32 | Float64) -> Int32:\n"
        "    if isinstance(v, Int32) and v > 0:\n        return v\n"
        "    return 0\n"
        "def compa(v: A | B) -> Int32:\n"
        "    assert isinstance(v, A) and v.x > 0\n"
        "    return v.x\n"
        "def compc(v: A | B) -> Int32:\n"
        "    if isinstance(v, A) and 0 < v.x < 10:\n        return v.x\n"
        "    return 0\n"
        "def esc(v: A | B) -> Int32:\n"
        "    assert isinstance(v, A), 'q\"b\\\\s'\n"
        "    return v.x\n"
        "def main():\n"
        "    two(A(1))\n    post(B(2))\n    exhaust(A(3))\n"
        "    three(C(4))\n    val(5)\n    chain(B(6))\n"
        "    loop(A(7))\n    plain_head(False, B(8))\n"
        "    h = H(A(9))\n    constw(h)\n    two_vars(A(10), B(11))\n"
        "    drain(A(12))\n    chk(A(13))\n    rechk(A(14))\n"
        "    comp(A(15))\n    compw(A(16))\n    compv(17)\n"
        "    compa(A(18))\n    compc(A(5))\n    esc(A(19))\n"
        "main()\n")

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_narrowing_renders(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "if (std::holds_alternative<A*>(v)) {" in cpp
        assert "auto& __v = *std::get<A*>(v);" in cpp
        assert "auto& __v = *std::get<B*>(v);" in cpp  # complement + post-if
        assert "if (true) {" in cpp  # the exhaustiveness fold
        assert "} else if (std::holds_alternative<B*>(v)) {" in cpp  # flat chain
        assert "const auto& __v = std::get<int32_t>(v);" in cpp  # value union
        assert "return __v.x;" in cpp
        # U4: while-isinstance loop-entry extraction + assert forms
        assert "while (std::holds_alternative<A*>(v)) {" in cpp
        assert ('if (!(std::holds_alternative<A*>(v))) '
                '::tpy::raise_assertion_error("want A");') in cpp
        assert "if (!(true)) ::tpy::raise_assertion_error();" in cpp
        assert "auto& __v_2 = *std::get<A*>(v);" in cpp  # re-assert bump
        # U4 compound conditions: inline deref reads, no alias in-condition
        assert ("if ((std::holds_alternative<A*>(v) && "
                "((*std::get<A*>(v)).x > 0))) {") in cpp
        assert ("while ((std::holds_alternative<A*>(v) && "
                "((*std::get<A*>(v)).x > 0))) {") in cpp
        assert ("if ((std::holds_alternative<int32_t>(v) && "
                "(std::get<int32_t>(v) > 0))) {") in cpp
        assert ("if (!((std::holds_alternative<A*>(v) && "
                "((*std::get<A*>(v)).x > 0)))) "
                "::tpy::raise_assertion_error();") in cpp  # compound assert
        # message escaping mirrors _gen_assert_throw: `\` doubles, `"` escapes
        assert '::tpy::raise_assertion_error("q\\"b\\\\s");' in cpp

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("two", "post", "exhaust", "three", "val", "chain",
                     "loop", "plain_head", "constw", "two_vars",
                     "drain", "chk", "rechk", "comp", "compw", "compv",
                     "compa", "compc", "esc"):
            assert _fn(thir, name) is not None, name


_CALLARG_RECORDS = (
    "from tpy import Int32, Float64, Own, readonly\n"
    "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
    "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
    "class S:\n    u: A | B\n"
    "    def __init__(self, v: Own[A | B]):\n        self.u = v\n"
    "def mut(v: A | B) -> None:\n"
    "    if isinstance(v, A):\n        v.x = v.x + 1\n"
    "def take(v: A | B) -> Int32:\n    return 0\n"
    "def take_opt(v: A | B | None) -> Int32:\n    return 0\n"
    "def take_own(v: Own[A | B]) -> Int32:\n"
    "    s = S(v)\n    return 1\n"
    "def take_vu(v: Int32 | Float64) -> Int32:\n    return 0\n"
)


class TestUnionCallArgLift:
    """The temp-free union call-arg rows: member name / None into a
    pointer-variant slot (THIRUnionArgLift), a record-ctor rvalue into an
    Own[union] slot (THIRCtorCall), an already-union coerced literal into a
    value-union slot (bare). The temp rows (member scalar into a value union,
    record rvalue into a ptr union, readonly / Own-name slots) stay AST."""

    def _lower(self, src: str):
        return _lower_ctx(_CALLARG_RECORDS + src)

    def _arg(self, thir, name):
        fn = _fn(thir, name)
        assert fn is not None, name
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        return ret.value.args[0]

    def test_member_param_arg_lifts(self):
        thir = self._lower("def f(a2: A) -> Int32:\n    return take(a2)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift)
        assert arg.variant_cpp == "std::variant<A*, B*>"
        assert isinstance(arg.value, THIRName) and not arg.deref
        assert arg.form is Form.BORROW

    def test_none_arg_lifts_monostate(self):
        thir = self._lower("def f() -> Int32:\n    return take_opt(None)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift)
        assert arg.value is None
        assert arg.variant_cpp == "std::variant<std::monostate, A*, B*>"

    def test_ctor_rvalue_own_union_arg_routes(self):
        thir = self._lower("def f() -> Int32:\n    return take_own(A(7))\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRCtorCall)
        assert arg.type_cpp == "A"
        assert arg.form is Form.STORAGE

    def test_coerced_literal_value_union_arg_routes(self):
        thir = self._lower("def f() -> Int32:\n    return take_vu(3)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRCoerce)

    def test_narrowed_subject_union_arg_routes_bare(self):
        # The narrowed subject's C++ binding is still the variant
        # (already_union), so the AST renders the bare extraction alias --
        # mirrored as a plain renamed read, no lift node.
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return take(v)\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0].then_body[1]  # [0] is the extraction alias
        arg = ret.value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "__v"

    def test_self_arg_lifts_with_deref(self):
        thir = self._lower(
            "class C:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def go(self) -> Int32:\n        return take_c(self)\n"
            "def take_c(v: C | A) -> Int32:\n    return 0\n")
        arg = self._arg(thir, "go")
        assert isinstance(arg, THIRUnionArgLift) and arg.deref
        assert isinstance(arg.value, THIRSelf)

    def test_pointer_local_arg_lifts_with_deref(self):
        thir = self._lower(
            "class H2:\n    a1: A\n    a2: A\n"
            "    def __init__(self, m: Int32, n: Int32):\n"
            "        self.a1 = A(m)\n        self.a2 = A(n)\n"
            "    def pick(self, flip: bool) -> Int32:\n"
            "        p = self.a1\n"
            "        if flip:\n            p = self.a2\n"
            "        return take(p)\n")
        arg = self._arg(thir, "pick")
        assert isinstance(arg, THIRUnionArgLift) and arg.deref

    def test_scalar_name_value_union_arg_temps(self):
        # `take_vu(k)` hoists `std::variant<int32_t, double> __tmp_N = k;`
        # -- the member-valued arg-temp row (number-free node; the real
        # __tmp_N is drawn from the module-cumulative sink at emission).
        thir = self._lower("def f(k: Int32) -> Int32:\n    return take_vu(k)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRArgTemp)
        assert arg.cpp_type == "std::variant<int32_t, double>"
        assert not arg.brace_init
        assert isinstance(arg.init, THIRName) and arg.init.name == "k"

    def test_scalar_binop_value_union_arg_temps(self):
        # Any member-valued scalar expression temps the same way; the init
        # renders in expression position (paren-wrapped binop).
        thir = self._lower(
            "def f(k: Int32) -> Int32:\n    return take_vu(k + 1)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRArgTemp)
        assert isinstance(arg.init, THIRBinOp) and arg.init.paren_wrap

    def test_none_value_union_arg_temps(self):
        # `None` into a value-union-with-None slot hoists the monostate
        # temp (`std::variant<...> __tmp_N = std::monostate{};` -- the
        # union-typed literal init, emit's monostate render).
        thir = self._lower(
            "def take_vn(v: Int32 | Float64 | None) -> Int32:\n"
            "    return 0\n"
            "def f() -> Int32:\n    return take_vn(None)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRArgTemp)
        assert isinstance(arg.init, THIRLiteral) and arg.init.value is None
        assert isinstance(arg.init.result_type, UnionType)

    def test_two_int_member_union_ctor_literal_routes(self):
        # RE-PINNED ROUTED (thir-wave-next6): the ctor-arg ladder gained
        # the free-call union rows, and the two-int-member literal renders
        # identically on both paths (dualgen-verified: the hoisted temp's
        # target-less literal render + the variant's converting ctor).
        thir = self._lower(
            "from tpy import Int64\n"
            "class WI:\n    uni: Int32 | Int64 | None\n"
            "    def __init__(self, uni: 'Int32 | Int64 | None') -> None:\n"
            "        self.uni = uni\n"
            "def look(w: WI) -> Int32:\n    return 0\n"
            "def f() -> None:\n    print(look(WI(1)))\n")
        assert _fn(thir, "f") is not None

    def test_int_literal_value_union_ctor_arg_temps(self):
        # A bare int literal at a value-union ctor slot is not sema-coerced:
        # the temp hoists with the target-less literal render (`__tmp_N =
        # 1;`, the variant's converting ctor picks the int member). The
        # flush position is the enclosing statement.
        thir = self._lower(
            "class WU:\n    uni: int | str | None\n"
            "    def __init__(self, uni: 'int | str | None') -> None:\n"
            "        self.uni = uni\n"
            "def look(w: WU) -> Int32:\n    return 0\n"
            "def f() -> None:\n    print(look(WU(1)))\n")
        fn = _fn(thir, "f")
        assert fn is not None

    def test_record_rvalue_ptr_union_arg_temps(self):
        # `take(A(n))` hoists a named record temp (`A __tmp_N = A(n);`) and
        # lifts its address -- the ctor-rvalue temp arm (flush positions
        # only; the return statement flushes).
        thir = self._lower("def f(n: Int32) -> Int32:\n    return take(A(n))\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift) and arg.temp_cpp == "A"
        assert isinstance(arg.value, THIRCtorCall)

    def test_readonly_slot_member_arg_lifts_const(self):
        # A readonly ptr-variant slot spells const pointees on the lift
        # (`std::variant<const A*, const B*>{&(a2)}`).
        thir = self._lower(
            "def tr(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def f(a2: A) -> Int32:\n    return tr(a2)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift) and not arg.const_wrap
        assert arg.variant_cpp == "std::variant<const A*, const B*>"
        assert isinstance(arg.value, THIRName) and not arg.deref

    def test_readonly_slot_none_arg_lifts_const_monostate(self):
        thir = self._lower(
            "def tro(v: readonly[A | B | None]) -> Int32:\n    return 0\n"
            "def f() -> Int32:\n    return tro(None)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift) and arg.value is None
        assert arg.variant_cpp == (
            "std::variant<std::monostate, const A*, const B*>")

    def test_readonly_slot_union_name_arg_const_wraps(self):
        # An already-union name into a deep-const slot takes the explicit
        # `::tpy::ptr_variant_to_const<...>(v)` conversion.
        thir = self._lower(
            "def tr(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def f(v: A | B) -> Int32:\n    return tr(v)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift) and arg.const_wrap
        assert arg.variant_cpp == "std::variant<const A*, const B*>"
        assert isinstance(arg.value, THIRName) and not arg.deref

    def test_readonly_slot_narrowed_union_arg_routes_bare(self):
        # `_gen_union_arg` SKIPS the const wrap for an isinstance-narrowed
        # arg (`is_narrowed`) and falls to the bare extraction alias --
        # mirrored as the plain renamed read (byte-identical; the render is
        # the same pre-existing `A&`-into-variant AST miscompile class as the
        # mutable-slot narrowed arg, see BUGS.md).
        thir = self._lower(
            "def tr(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return tr(v)\n"
            "    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0].then_body[1]  # [0] is the extraction alias
        arg = ret.value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "__v"

    def test_dcbp_readonly_fn_member_arg_lifts_const(self):
        # The deep-const verdict WITHOUT an annotation: a @readonly free fn's
        # plain union param lands in `deep_const_borrow_params`, and the AST
        # (`is_readonly_target`) spells const pointees at every call site.
        thir = self._lower(
            "@readonly\n"
            "def show(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    return v.y\n"
            "def f(a2: A) -> Int32:\n    return show(a2)\n"
            "def g(v: A | B) -> Int32:\n    return show(v)\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRUnionArgLift) and not arg.const_wrap
        assert arg.variant_cpp == "std::variant<const A*, const B*>"
        arg = self._arg(thir, "g")
        assert isinstance(arg, THIRUnionArgLift) and arg.const_wrap

    def test_own_slot_name_arg_routes_move(self):
        # RE-PINNED ROUTED (thir-wave-next6): an Own[union] slot with an
        # Own-param NAME arg takes the last-use `std::move(v)` via the
        # Own-slot cascade's union row (corpus: callarg_own_ctor).
        thir = self._lower(
            "def f(v: Own[A | B]) -> Int32:\n    return take_own(v)\n")
        assert _fn(thir, "f") is not None

    def test_ctor_rvalue_plain_record_slot_temps(self):
        # `use(A(7))` into a plain record param hoists `A __tmp_N = A(7);`
        # -- the record-rvalue arg-temp row (same-nominal ref slot).
        thir = self._lower(
            "def use(a2: A) -> Int32:\n    return a2.x\n"
            "def f() -> Int32:\n    return use(A(7))\n")
        arg = self._arg(thir, "f")
        assert isinstance(arg, THIRArgTemp)
        assert arg.cpp_type == "A"
        assert isinstance(arg.init, THIRCtorCall) and arg.init.type_cpp == "A"

    def test_ctor_record_own_arg_routes_bare(self):
        # A nested ctor whose own arg is a record-ctor rvalue into an
        # `Own[A]` slot (`take_own2(W(A(1)))`): temp-free, the rvalue binds
        # the T&& slot bare -- routed since the nested tail admits
        # `_own_record_rvalue_arg`.
        thir = self._lower(
            "class W:\n    a: A\n"
            "    def __init__(self, a: Own[A]):\n        self.a = a\n"
            "def take_w(v: W | A) -> Int32:\n    return 0\n"
            "def f() -> Int32:\n    return take_own2(W(A(1)))\n"
            "def take_own2(v: Own[W | A]) -> Int32:\n"
            "    s2 = S2(v)\n    return 1\n"
            "class S2:\n    u: W | A\n"
            "    def __init__(self, v: Own[W | A]):\n        self.u = v\n")
        outer = self._arg(thir, "f")
        assert isinstance(outer, THIRCtorCall) and outer.type_cpp == "W"
        inner = outer.args[0]
        assert isinstance(inner, THIRCtorCall) and inner.type_cpp == "A"

    def test_witnesses_lift_faces(self):
        # The monostate arm (None into `A | B | None`), the deep-const
        # already-union conversion (`ptr_variant_to_const`), and the member
        # address-of -- one per arm.
        _, w = _lower_ctx_witnessed(
            _CALLARG_RECORDS
            + "def tr(v: readonly[A | B]) -> Int32:\n    return 0\n"
            + "def f() -> Int32:\n    return take_opt(None)\n"
            + "def g(v: A | B) -> Int32:\n    return tr(v)\n"
            + "def h(a2: A) -> Int32:\n    return take(a2)\n")
        assert w.get("unionlift.none", 0) == 1
        assert w.get("unionlift.const_wrap", 0) == 1
        assert "unionlift.member" in w

    def test_dump_renders_lift_arms_and_ctor(self):
        # All three THIRUnionArgLift arms (member address-of, monostate,
        # const conversion) plus the THIRCtorCall render.
        from .dump import dump_thir
        thir = self._lower(
            "def tr(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def f(a2: A) -> Int32:\n    return take(a2)\n"
            "def g() -> Int32:\n    return take_opt(None)\n"
            "def h() -> Int32:\n    return take_own(A(7))\n"
            "def w(v: A | B) -> Int32:\n    return tr(v)\n")
        text = dump_thir(thir)
        assert "union_lift[std::variant<A*, B*>]{&(%a2)}" in text
        assert ("union_lift[std::variant<std::monostate, A*, B*>]"
                "{monostate}") in text
        assert "ctor(A, [coerce(lit(7) -> Int32)])" in text
        assert ("union_lift[std::variant<const A*, const B*>]"
                "{to_const(%v)}") in text


class TestUnionCallArgEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = _CALLARG_RECORDS + (
        "class H2:\n    a1: A\n    a2: A\n"
        "    def __init__(self, m: Int32, n: Int32):\n"
        "        self.a1 = A(m)\n        self.a2 = A(n)\n"
        "    def pick(self, flip: bool) -> Int32:\n"
        "        p = self.a1\n"
        "        if flip:\n            p = self.a2\n"
        "        mut(p)\n"
        "        return take(p)\n"
        "class C:\n    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "    def go(self) -> Int32:\n        mut_c(self)\n        return self.n\n"
        "def mut_c(v: C | A) -> None:\n"
        "    if isinstance(v, C):\n        v.n = v.n + 1\n"
        "def f1(a2: A) -> Int32:\n    mut(a2)\n    return take(a2)\n"
        "def f2() -> Int32:\n    return take_opt(None)\n"
        "def f3() -> Int32:\n    return take_own(A(7))\n"
        "def f4() -> Int32:\n    return take_vu(3)\n"
        "def f5(n: Int32) -> Int32:\n"
        "    t = 0\n"
        "    while take_own(A(n)) > t:\n"
        "        t = t + 4\n"
        "    return t\n"
        "def tr(v: readonly[A | B]) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    return v.y\n"
        "def tro(v: readonly[A | B | None]) -> Int32:\n"
        "    if v is None:\n        return -1\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    return 0\n"
        "@readonly\n"
        "def show(v: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    return v.y\n"
        "def f6(a2: A) -> Int32:\n    return tr(a2) + tro(a2) + show(a2)\n"
        "def f7() -> Int32:\n    return tro(None)\n"
        "def f8(v: A | B) -> Int32:\n    return tr(v) + show(v)\n"
        "def main():\n"
        "    a = A(1)\n"
        "    print(f1(a))\n"
        "    print(f2())\n"
        "    print(f3())\n"
        "    print(f4())\n"
        "    print(f5(2))\n"
        "    h = H2(1, 2)\n"
        "    print(h.pick(True))\n"
        "    c = C(5)\n"
        "    print(c.go())\n"
        "    print(f6(a))\n"
        "    print(f7())\n"
        "    print(f8(a))\n"
        "main()\n")

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_readonly_slot_emitted_shapes(self):
        out = self._cpp(self.SRC, thir=True)
        # Member lift + monostate spell const pointees; the already-union
        # name takes the explicit const conversion.
        assert "std::variant<const A*, const B*>{&(a2)}" in out
        assert ("tro(std::variant<std::monostate, const A*, const B*>"
                "{std::monostate{}})") in out
        assert ("::tpy::ptr_variant_to_const"
                "<std::variant<const A*, const B*>>(v)") in out

    def test_lift_renders(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "take(std::variant<A*, B*>{&(a2)})" in cpp
        assert ("take_opt(std::variant<std::monostate, A*, B*>"
                "{std::monostate{}})") in cpp
        assert "take_own(A(7))" in cpp
        assert "take_vu(3)" in cpp
        assert "while ((take_own(A(n)) > t))" in cpp
        assert "take(std::variant<A*, B*>{&((*p))})" in cpp  # pointer-local
        assert "mut_c(std::variant<A*, C*>{&((*this))})" in cpp  # self

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8",
                     "pick", "go"):
            assert _fn(thir, name) is not None, name

    # Separate fixture: the narrowed-arg wrap SKIP into a readonly slot. The
    # render (the bare `A&` extraction alias into a variant slot) is the
    # pre-existing AST miscompile class in BUGS.md -- it does not compile as
    # C++, so it lives outside the exec'd SRC above; the byte comparison
    # still pins the mirror (string compare only, never built).
    NARROW_SKIP_SRC = _CALLARG_RECORDS + (
        "def tr(v: readonly[A | B]) -> Int32:\n"
        "    if isinstance(v, A):\n        return v.x\n"
        "    return v.y\n"
        "def f(v: A | B) -> Int32:\n"
        "    if isinstance(v, A):\n        return tr(v)\n"
        "    return 0\n")

    def test_narrowed_arg_wrap_skip_byte_identical(self):
        assert (self._cpp(self.NARROW_SKIP_SRC, thir=True)
                == self._cpp(self.NARROW_SKIP_SRC, thir=False))
        out = self._cpp(self.NARROW_SKIP_SRC, thir=True)
        assert "return tr(__v);" in out  # the is_narrowed wrap skip


# --- Value-union-returning calls at the decl-init sink ---

# A value-union-returning free call renders bare at a decl init
# (`std::variant<int32_t, double> u = make(n);`) and a reassign (a plain
# value assign -- value unions carry no pointer-local machinery). A
# pointer-variant union return (record members) stays on the AST path.
class TestUnionCallDecl:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def make(n: Int32) -> Int32 | Float64:\n"
        + "    if n > 0:\n        return n\n"
        + "    return 2.5\n"
        + "def use(n: Int32) -> Int32:\n"
        + "    u = make(n)\n"
        + "    u = make(n - 1)\n"
        + "    if isinstance(u, Int32):\n        return u\n"
        + "    return 0\n"
        + "def main():\n    print(use(1))\nmain()\n"
    )

    def test_decl_and_reassign_route(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "use")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)
        assert isinstance(fn.body[1], THIRAssign)
        assert faces.get("decl.storage_call", 0) >= 1

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_bare_call(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::variant<int32_t, double> u = make(n);" in cpp
        assert "u = make((::tpy::sub_check<int32_t>(n, 1)));" in cpp

    def test_ptr_union_call_decl_routes(self):
        # RE-PINNED ROUTED (thir-wave-next6): a plain ptr-variant union
        # return assigns BARE at the decl (`u = pick(a, b, true);` -- the
        # C++ return is already the pointer variant); only `Own[union]`
        # factories keep the UNION_RVALUE storage slot. Corpus-verified by
        # union_ptr_variant_none_write.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class A:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "class B:\n    y: Int32\n"
            "    def __init__(self, y: Int32):\n        self.y = y\n"
            "def pick(a: A, b: B, f: bool) -> A | B:\n"
            "    if f:\n        return a\n    return b\n"
            "def use(a: A, b: B) -> Int32:\n"
            "    u = pick(a, b, True)\n"
            "    return 1\n")
        assert _fn(thir, "use") is not None


class TestOwnUnionReturn:
    """An `Own[A | B]` record-member union return (`std::variant<A, B>` by
    value): a member ctor rvalue returns bare (the converting ctor absorbs
    it); every other source shape stays AST."""

    _RECORDS = (
        "from tpy import Int32, Own\n"
        "class Dog:\n"
        "    age: Int32\n"
        "    def __init__(self, age: Int32):\n        self.age = age\n"
        "class Cat:\n"
        "    lives: Int32\n"
        "    def __init__(self, lives: Int32):\n        self.lives = lives\n"
    )

    def test_member_ctor_rvalue_routes(self):
        thir = _lower_ctx(
            self._RECORDS
            + "def make(age: Int32) -> Own[Dog | Cat]:\n"
            + "    return Dog(age)\n")
        assert _fn(thir, "make") is not None

    def test_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            self._RECORDS
            + "def make(age: Int32) -> Own[Dog | Cat]:\n"
            + "    return Dog(age)\n")
        assert faces.get("ret.own_union_ctor", 0) >= 1

    def test_member_name_source_stays_ast(self):
        # A member-typed NAME return needs the move/borrow verdicts the arm
        # does not mirror -- reject.
        thir = _lower_ctx(
            self._RECORDS
            + "def make(d: Own[Dog]) -> Own[Dog | Cat]:\n"
            + "    return d\n")
        assert _fn(thir, "make") is None

    def test_emit_byte_identical(self):
        src = (
            self._RECORDS
            + "def make_dog(age: Int32) -> Own[Dog | Cat]:\n"
            + "    return Dog(age)\n"
            + "def make_cat(lives: Int32) -> Own[Dog | Cat]:\n"
            + "    return Cat(lives)\n"
            + "def main():\n"
            + "    pet = make_dog(5)\n"
            + "    if isinstance(pet, Dog):\n        print(pet.age)\n"
            + "main()\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)

        def cpp(thir: bool):
            _, out = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return out

        thir_cpp = cpp(True)
        assert thir_cpp == cpp(False)
        assert "return Dog(age);" in thir_cpp
        assert "return Cat(lives);" in thir_cpp


class TestAssignNarrowedUnionFieldRead:
    """`.field` off an ASSIGN-narrowed pointer-variant union name renders the
    inline bare get (`(*std::get<Circle*>(c)).radius`); a field WRITE through
    the same receiver stays on the AST path."""

    _RECORDS = (
        "class Circle:\n    radius: float\n"
        "    def __init__(self, radius: float) -> None:\n"
        "        self.radius = radius\n"
        "class Rect:\n    width: float\n"
        "    def __init__(self, width: float) -> None:\n"
        "        self.width = width\n"
    )

    def test_read_routes_byte_identical(self):
        src = (self._RECORDS
               + "def f() -> None:\n"
               + "    c: Circle | Rect = Circle(5.0)\n"
               + "    print(c.radius)\n")
        thir, _ = _lower_ctx(src), None
        assert _fn(thir, "f") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        from ..codegen_cpp.context import CodeGenOptions
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=False))
        out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert out == ast
        assert "(*std::get<Circle*>(c)).radius" in out[1]

    def test_write_falls_back(self):
        src = (self._RECORDS
               + "def f() -> None:\n"
               + "    c: Circle | Rect = Circle(5.0)\n"
               + "    c.radius = 6.0\n"
               + "    print(c.radius)\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestUnionCallSubjectMatch:
    """`match p.choose(d):` -- a Call/MethodCall rvalue returning a
    non-wrapper ptr-variant union materializes into the by-value dispatch
    local (`auto __match_subject_N = p.choose(d);`), the arms `*std::get`
    off it. The union return admits ONLY at this position (the scoped
    match_union_subject flag); a decl consumer keeps rejecting."""

    _SRC = (
        "class Dog:\n"
        "    name: str\n"
        "    def __init__(self, n: str) -> None:\n"
        "        self.name = n\n"
        "class Cat:\n"
        "    name: str\n"
        "    def __init__(self, n: str) -> None:\n"
        "        self.name = n\n"
        "class Picker:\n"
        "    def choose(self, d: Dog) -> Dog | Cat:\n"
        "        return d\n"
        "def main() -> None:\n"
        "    p = Picker()\n"
        '    d = Dog("rex")\n'
        "    match p.choose(d):\n"
        "        case Dog() as x:\n"
        "            print(x.name)\n"
        "        case Cat() as y:\n"
        "            print(y.name)\n"
        "main()\n"
    )

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_call_subject_routes_by_value(self):
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert w.get("match.subject_rvalue", 0) >= 1
        assert w.get("method.union_subject_ret", 0) >= 1
        cpp = self._cpp(self._SRC, thir=True)
        assert cpp == self._cpp(self._SRC, thir=False)
        assert "auto __match_subject_1 = p.choose(d);" in cpp
        assert "auto& x = *std::get<1>(__match_subject_1);" in cpp

    def test_free_call_subject_routes(self):
        # The FREE-call twin (dualgen-probed): the free-call result gate's
        # call.union_subject_ret row admits the same non-wrapper
        # ptr-variant return at the dispatch-local sink.
        src = (
            "class Dog:\n"
            "    name: str\n"
            "    def __init__(self, n: str) -> None:\n"
            "        self.name = n\n"
            "class Cat:\n"
            "    name: str\n"
            "    def __init__(self, n: str) -> None:\n"
            "        self.name = n\n"
            "def choose(d: Dog) -> Dog | Cat:\n"
            "    return d\n"
            "def main() -> None:\n"
            '    d = Dog("rex")\n'
            "    match choose(d):\n"
            "        case Dog() as x:\n"
            "            print(x.name)\n"
            "        case Cat() as y:\n"
            "            print(y.name)\n"
            "main()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert w.get("call.union_subject_ret", 0) >= 1
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_guarded_call_subject_defers(self):
        # BOUNDARY (dualgen-probed): a GUARDED union match with a call
        # subject falls back byte-identically -- the guarded lowerer keeps
        # its lvalue-only subject path.
        src = (
            "class Dog:\n"
            "    n: int\n"
            "    def __init__(self, n: int) -> None:\n"
            "        self.n = n\n"
            "class Cat:\n"
            "    n: int\n"
            "    def __init__(self, n: int) -> None:\n"
            "        self.n = n\n"
            "class Picker:\n"
            "    def choose(self, d: Dog) -> Dog | Cat:\n"
            "        return d\n"
            "def main() -> None:\n"
            "    p = Picker()\n"
            "    d = Dog(5)\n"
            "    match p.choose(d):\n"
            "        case Dog() as x if x.n > 3:\n"
            '            print("big dog")\n'
            "        case Dog():\n"
            '            print("small dog")\n'
            "        case Cat():\n"
            '            print("cat")\n'
            "main()\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_union_return_at_decl_still_rejects(self):
        # BOUNDARY (dualgen-probed): the same return at a DECL consumer
        # keeps rejecting -- the flag is position-scoped.
        src = (
            "class Dog:\n"
            "    name: str\n"
            "    def __init__(self, n: str) -> None:\n"
            "        self.name = n\n"
            "class Cat:\n"
            "    name: str\n"
            "    def __init__(self, n: str) -> None:\n"
            "        self.name = n\n"
            "class Picker:\n"
            "    def choose(self, d: Dog) -> Dog | Cat:\n"
            "        return d\n"
            "def main() -> None:\n"
            "    p = Picker()\n"
            '    d = Dog("rex")\n'
            "    u = p.choose(d)\n"
            "    if isinstance(u, Dog):\n"
            "        print(u.name)\n"
            "main()\n"
        )
        assert _fn(_lower_ctx(src), "main") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
