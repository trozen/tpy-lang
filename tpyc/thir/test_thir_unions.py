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
    Form, THIRCoerce, THIRFormConvert, THIRFunction, THIRFunctionLayout,
    THIRLiteral, THIRName, THIRReturn, THIRVarDecl,
)
from .validate import (
    THIRValidationError, validate_constructor, validate_function,
)
from .testutil import _compile, _entry, _fn, _lower, _lower_ctor, _lower_ctx

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

    def test_member_valued_arg_rejected(self):
        # A float-literal member arg hoists a `__tmp_N` variant temp on the
        # AST path (the gen_call_arg cascade frontier) -> AST path.
        thir = _lower(_PRELUDE + (
            "def take(v: Int32 | Float64) -> Int32:\n"
            "    return 0\n"
            "def h() -> Int32:\n"
            "    return take(2.5)\n"))
        assert _fn(thir, "h") is None

    def test_union_print_rejected(self):
        # union __str__ is an S2-deferred row; print(v) stays AST.
        thir = _lower(_PRELUDE + (
            "def p(v: Int32 | Float64) -> None:\n"
            "    print(v)\n"))
        assert _fn(thir, "p") is None

    def test_str_member_union_rejected(self):
        # A str/view member makes the union form-relevant per slot -> later
        # F4 cell.
        thir = _lower(_PRELUDE + (
            "def f(v: Int32 | str) -> Int32:\n"
            "    return 0\n"))
        assert _fn(thir, "f") is None


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

    def test_field_write_from_field_or_member_rejects(self):
        # Field-to-field and member-valued union field writes ride later
        # cells (`_ptr_union_source_ok(allow_field=False)` -- name sources
        # only); the write gate must reject both sides, like the reseat pair.
        thir = self._lower("def w1(h: H, g: H) -> None:\n    h.u = g.u\n")
        assert _fn(thir, "w1") is None
        thir = self._lower("def w2(h: H, a2: A) -> None:\n    h.u = a2\n")
        assert _fn(thir, "w2") is None

    def test_member_name_return_rejects(self):
        # `return d` (a MEMBER record name) takes the AST's `&(d)` address-of
        # lift into the pointer variant -- caught by the corpus byte-diff
        # (union_return_concrete_param / match_ptr_variant_call); the return
        # gate admits same-union names only.
        thir = self._lower("def wrap(a2: A) -> A | B:\n    return a2\n")
        assert _fn(thir, "wrap") is None

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

    def test_readonly_slot_arg_rejects(self):
        # A readonly ptr-variant slot takes _gen_union_arg's
        # ptr_variant_to_const wrap -> AST path.
        thir = self._lower(
            "def take(v: readonly[A | B]) -> Int32:\n    return 0\n"
            "def ra(v: A | B) -> Int32:\n    return take(v)\n")
        assert _fn(thir, "ra") is None

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
        assert "explicit H(std::variant<A, B>&& v) : u(std::move(v)), n(0) {}" in cpp

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("take", "fwd", "ro", "mu", "rs", "wf"):
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
