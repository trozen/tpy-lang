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
    THIRIf, THIRIsinstance, THIRLiteral, THIRName, THIRNarrowAlias,
    THIRReturn, THIRVarDecl,
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


_THREE_RECORDS = _PRELUDE + _PTR_RECORDS + (
    "class C:\n    z: Int32\n    def __init__(self, z: Int32):\n        self.z = z\n"
)


class TestNarrowingEligibility:
    """F4 U3: isinstance-narrowing reads (if/elif/else + the early-return
    implicit else). Writes to the narrowed subject, while-isinstance,
    compound conditions, and readonly subjects stay on the AST path."""

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
        lc.persistent_aliases.add("__v")
        assert _persistent_alias_name("v", lc) == "__v_2"
        lc.persistent_aliases.add("__v_2")
        assert _persistent_alias_name("v", lc) == "__v_3"

    def test_assign_node_rebind_of_narrowed_rejects(self):
        # The parser emits TpyVarDecl for every ordinary name-target assign; a
        # name-target TpyAssign only arises from macro-authored / frontend-IR
        # ASTs. The gate must reject a narrowed-subject rebind there too -- a
        # routed rebind would leave later reads on the stale extraction alias.
        from ..parse.nodes import TpyAssign, TpyName
        from .lower import _Prescan, _stmt_eligible
        compiler, modules = _compile(_PRELUDE + (
            "def f(v: Int32 | Float64, v2: Int32 | Float64) -> Int32:\n"
            "    x = v2\n"
            "    return 0\n"))
        entry = _entry(modules)
        an = entry.analyzer
        fn = entry.ast.functions[0]
        declared = {n: t for n, t in fn.params}
        synthetic = TpyAssign(target=TpyName("v"), value=fn.body[0].init)
        common = dict(in_branch=True, pointers=set(), rebind_slots=set(),
                      storage_tuple_locals=set())
        prescan = _Prescan(fn, an)
        assert _stmt_eligible(synthetic, an, dict(declared), prescan,
                              narrowed=set(), **common)
        assert not _stmt_eligible(synthetic, an, dict(declared), prescan,
                                  narrowed={"v"}, **common)

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

    def test_narrowed_method_receiver_rejects(self):
        # `v.sound()` on the narrowed member rides the record-method-CALL
        # frontier (the call-site gate admits container receivers only), not
        # U3 -> AST. The rename itself is exercised by field reads.
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
        assert _fn(thir, "f") is None

    def test_narrowed_record_call_arg_rejects(self):
        # A record arg (`use(v)` with v narrowed to A) rides the call-arg
        # record frontier (auto-move / conversion cascade), not U3 -> AST.
        thir = self._lower(
            "def use(a2: A) -> Int32:\n    return a2.x\n"
            "def f(v: A | B) -> Int32:\n"
            "    if isinstance(v, A):\n        return use(v)\n"
            "    return v.y\n")
        assert _fn(thir, "f") is None

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

    def test_readonly_subject_rejects(self):
        thir = self._lower(
            "def f(v: readonly[A | B]) -> Int32:\n"
            "    if isinstance(v, A):\n        return v.x\n"
            "    return v.y\n")
        assert _fn(thir, "f") is None

    def test_while_isinstance_rejects(self):
        thir = self._lower(
            "def f(v: A | B) -> Int32:\n"
            "    while isinstance(v, A):\n        return v.x\n"
            "    return 0\n")
        assert _fn(thir, "f") is None

    def test_compound_condition_rejects(self):
        thir = self._lower(
            "def f(v: A | B, flag: bool) -> Int32:\n"
            "    if isinstance(v, A) and flag:\n        return v.x\n"
            "    return 0\n")
        assert _fn(thir, "f") is None

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
        "def main():\n"
        "    two(A(1))\n    post(B(2))\n    exhaust(A(3))\n"
        "    three(C(4))\n    val(5)\n    chain(B(6))\n"
        "    loop(A(7))\n    plain_head(False, B(8))\n"
        "    h = H(A(9))\n    constw(h)\n    two_vars(A(10), B(11))\n"
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

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("two", "post", "exhaust", "three", "val", "chain",
                     "loop", "plain_head", "constw", "two_vars"):
            assert _fn(thir, name) is not None, name
