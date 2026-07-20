"""THIR method frontier M1/M2: instance methods, record params, readonly
free functions, scalar field writes + aug-assign."""

from __future__ import annotations

import pytest

from ..codegen_cpp.context import CodeGenOptions
from ..diagnostics import SemanticError
from ..codegen_cpp.forms import LocalBinding
from ..typesys import NominalType, PtrType
from ..compilation_context import activate_compiler
from .lower.checks import (
    _bytearray_method_call_supported, _container_method_arg_ok,
    _container_method_call_supported, _method_recv_family,
    _protocol_method_arg_ok, _protocol_method_call_supported,
    _view_method_arg_ok, _view_method_call_supported,
)
from .lower.predicates import _eligible_ptr_value
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRCall, THIRCoerce, THIRExprStmt,
    THIRFieldAccess, THIRFormConvert,
    THIRMethodCall, THIRName, THIRReturn, THIRSelf, THIRSetItem, THIRStrAppend,
    THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _lower_ctor,
    _fn, _F1_RECORDS, _assert_byte_identical,
)

# --- M1 method frontier: instance methods with a `self` (`this`) receiver ---

# Methods over the F1 records. `get_n` is a scalar field read (auto-readonly,
# const self); `head` a REF_ALIAS off self; `peek` an OPTIONAL_TO_PTR off a
# readonly self (-> `const Inner*`); `reset` a non-readonly method writing
# `self.opt = None` (mutates self -> non-const `this`).
_M1_METHODS = (
    _F1_RECORDS
    + "    def get_n(self) -> Int32:\n        return self.n\n"
    + "    def head(self) -> Int32:\n        x = self.inner\n        return x.value\n"
    + "    def peek(self) -> Int32:\n        p = self.opt\n        return 0\n"
    + "    def reset(self):\n        self.opt = None\n"
)


class TestMethodFrontier:
    def test_scalar_field_read_off_self(self):
        thir = _lower_ctx(_M1_METHODS)
        fn = _fn(thir, "get_n")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)
        assert isinstance(ret.value.receiver, THIRSelf)
        assert ret.value.is_arrow and ret.value.form is Form.VALUE

    def test_ref_alias_off_self(self):
        decl = _fn(_lower_ctx(_M1_METHODS), "head").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_optional_to_ptr_off_readonly_self_is_const(self):
        # A readonly method's `self` is const, so the borrow lifts to `const T*`.
        decl = _fn(_lower_ctx(_M1_METHODS), "peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_nonreadonly_method_routes(self):
        # `reset` writes `self.opt = None` -> self is non-readonly (non-const
        # `this`); routes via the F2c storage-form None write.
        assert _fn(_lower_ctx(_M1_METHODS), "reset") is not None

    def test_staticmethod_routes_without_receiver(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @staticmethod\n    def smethod(a: Int32) -> Int32:\n        return a\n")
        fn = _fn(thir, "smethod")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRName)

    def test_record_param_method_routes(self):
        # M2: a non-readonly method takes an F1-record param like a free function;
        # `other.n` is a scalar field read off the record param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def with_box(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "with_box") is not None

    def test_generic_record_concrete_method_routes(self):
        # A generic record's method with a fully CONCRETE body (a `Int32` field
        # read + return, no `T`) routes: the `self` is templated but inline
        # (`this`), so the body is byte-identical to the AST's. The gate opened
        # once `_method_self_type` yields the generic `Wrap[T]` self.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Wrap[T]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def get(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "get") is not None

    def test_constructor_excluded(self):
        # The ctor body is emitted via the member-init-list driver, not gen_body;
        # iter_module_callables skips record.init_method (the M3 ctor frontier).
        thir = _lower_ctx(_M1_METHODS)
        assert _fn(thir, "__init__") is None

    def test_property_getter_routes_with_self_receiver(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @property\n    def doubled(self) -> Int32:\n        return self.n\n")
        fn = _fn(thir, "doubled")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)
        assert isinstance(ret.value.receiver, THIRSelf) and ret.value.is_arrow

    def test_optional_to_ptr_off_mutable_self_is_nonconst(self):
        # A non-readonly method (writes self.opt) reads self.opt off a non-const
        # `this`, so the borrow lifts to a mutable `Inner*`, not `const Inner*`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def churn(self):\n        p = self.opt\n        self.opt = None\n")
        decl = _fn(thir, "churn").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const



class TestMethodFrontierEmit:
    def _emit(self, src: str, thir: bool):
        # Instance methods emit inline in the struct (the .hpp), so the contract
        # is checked over header + source, not just the .cpp.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M1_METHODS
        + "def main():\n    b = Box(Inner(0))\n    print(b.get_n() + b.head())\n"
        + "    print(b.peek())\n    b.reset()\n"
        + "main()\n"
    )

    def test_methods_byte_identical(self):
        # The load-bearing contract for the frontier: method bodies emit
        # identically from THIR and the AST path.
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_renders_as_this_arrow(self):
        # The `self` receiver renders as the C++ `this` pointer with `->`.
        assert "return this->n;" in self._emit(self.SRC, thir=True)

    def test_readonly_self_optional_read_is_const(self):
        assert "const Inner* p = ::tpy::optional_to_ptr(this->opt);" in self._emit(self.SRC, thir=True)

    def test_nonreadonly_self_none_write(self):
        assert "this->opt = std::nullopt;" in self._emit(self.SRC, thir=True)



# --- M2: record params on instance methods (readonly or not) ---

_M2_METHODS = (
    _F1_RECORDS
    # reads a scalar field off the record param -> param is const-ref (not mutated)
    + "    def sum_with(self, other: Box) -> Int32:\n        return self.n + other.n\n"
    # reads other.opt -> a borrow local off a const record param (const Inner*)
    + "    def peek_other(self, other: Box) -> Int32:\n        p = other.opt\n        return 0\n"
    # writes other.opt -> the param is mutated, so it is a mutable ref (Inner*)
    + "    def clear_other(self, other: Box):\n        p = other.opt\n        other.opt = None\n"
    # mutates b but only reads a.opt: the const verdict must key on a's param index
    + "    def mix(self, a: Box, b: Box) -> Int32:\n        b.n = 1\n        p = a.opt\n        return 0\n"
)


class TestMethodFrontierM2:
    def test_const_record_param_scalar_read_routes(self):
        assert _fn(_lower_ctx(_M2_METHODS), "sum_with") is not None

    def test_optional_to_ptr_off_const_record_param_is_const(self):
        # `other` is not mutated -> const-ref param -> the borrow off other.opt
        # lifts to `const Inner*` (the const verdict comes from the method's own
        # const_borrow_params, looked up on the owning record).
        decl = _fn(_lower_ctx(_M2_METHODS), "peek_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_optional_to_ptr_off_mutated_record_param_is_nonconst(self):
        # `clear_other` writes other.opt -> `other` is a mutable ref, so the
        # borrow off it is `Inner*`, not `const Inner*`.
        decl = _fn(_lower_ctx(_M2_METHODS), "clear_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const

    def test_readonly_method_with_record_param_routes(self):
        # An explicit @readonly method with a record param routes: for a plain
        # F1-record (ref) param the forced-const and inferred-const verdicts
        # coincide, so const_borrow_params is exact (no carve-out needed).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_with(self, other: Box) -> Int32:\n"
            + "        return other.n\n")
        assert _fn(thir, "ro_with") is not None

    def test_auto_readonly_method_with_record_param_routes(self):
        # A non-mutating method that reads a record param is auto-readonly; it
        # must still route (this is the common case the rung exists for).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def auto_ro(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "auto_ro") is not None

    def test_optional_to_ptr_off_explicit_readonly_record_param_is_const(self):
        # An explicit @readonly method that lifts a borrow off a record param:
        # the param is not mutated, so the forced-const verdict and the inferred
        # const_borrow_params verdict coincide -> `const Inner*` (pins the claim
        # the eligibility comment rests on, for the explicit-readonly path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_peek(self, other: Box) -> Int32:\n"
            + "        p = other.opt\n        return 0\n")
        decl = _fn(thir, "ro_peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_mixed_mutation_const_verdict_keys_on_param_index(self):
        # `mix` mutates b but only reads a.opt; the const verdict must key on a's
        # param index (0), not b's (1) -- exercises the index-based
        # const_borrow_params lookup that a single-param method never does.
        decl = next(s for s in _fn(_lower_ctx(_M2_METHODS), "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const



class TestMethodFrontierM2Emit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M2_METHODS
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n"
        + "    print(a.sum_with(b))\n    print(a.peek_other(b))\n    a.clear_other(b)\n"
        + "main()\n"
    )

    def test_m2_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_const_record_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(other.opt);"
                in self._emit(self.SRC, thir=True))



# --- Readonly free functions: a `@readonly` free function is admitted (its
# record params are forced const, which coincides with the inferred verdict) ---

_RO_FREE = (
    _F1_RECORDS
    + "@readonly\ndef width(b: Box) -> Int32:\n    return b.n\n"
    + "@readonly\ndef peek_free(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
)


class TestReadonlyFreeFunction:
    def test_readonly_free_function_routes(self):
        assert _fn(_lower_ctx(_RO_FREE), "width") is not None

    def test_optional_to_ptr_off_readonly_free_param_is_const(self):
        # A readonly free function forces its record param const; that coincides
        # with the inferred const_borrow_params verdict (param not mutated), so the
        # borrow off b.opt lifts to `const Inner*`.
        decl = _fn(_lower_ctx(_RO_FREE), "peek_free").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const



class TestReadonlyFreeFunctionEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _RO_FREE
        + "def main():\n    b = Box(Inner(5))\n    print(width(b))\n    print(peek_free(b))\n"
        + "main()\n"
    )

    def test_readonly_free_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_readonly_free_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(b.opt);"
                in self._emit(self.SRC, thir=True))



# --- Scalar field writes: `recv.field = <scalar>` off an F1-record receiver ---

_SCALAR_WRITE = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n    other: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n        self.other = 0\n"
    "    def reset(self):\n        self.count = 0\n"
    "    def copy_field(self):\n        self.count = self.other\n"
    # off a record param in a free function (non-self receiver)
    "def bump(c: Counter, n: Int32):\n    c.count = n\n    c.other = c.count + 1\n"
)


class TestScalarFieldWrite:
    def test_write_off_self_routes_as_plain_assign(self):
        # `self.count = 0` lowers to a plain THIRAssign (value form), NOT the F2b
        # borrow->storage path -- the field is a scalar, so no THIRFormConvert.
        fn = _fn(_lower_ctx(_SCALAR_WRITE), "reset")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert not isinstance(st.value, THIRFormConvert)
        assert st.value.form is Form.VALUE

    def test_field_to_field_scalar_copy(self):
        st = _fn(_lower_ctx(_SCALAR_WRITE), "copy_field").body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.value, THIRFieldAccess) and st.value.form is Form.VALUE

    def test_write_off_record_param_routes(self):
        # A scalar field write off a record param in a free function (non-self).
        assert _fn(_lower_ctx(_SCALAR_WRITE), "bump") is not None

    def test_property_setter_target_routes(self):
        # A field write that is really a @property setter lowers as the void
        # setter method call (`self.set_n(5)`) -- the assign arm delegates to
        # the method-call arm.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class C:\n    _n: Int32\n"
            "    def __init__(self):\n        self._n = 0\n"
            "    @property\n    def n(self) -> Int32:\n        return self._n\n"
            "    @n.setter\n    def n(self, v: Int32):\n        self._n = v\n"
            "    def use(self):\n        self.n = 5\n")
        assert _fn(thir, "use") is not None

    def test_write_off_pointer_local_routes_with_arrow(self):
        # Receiver is an F2 reseatable `T*` pointer-local -- the third
        # `_field_receiver_ok` receiver kind, rendering `->` (distinct from self's
        # `this->` and a record param's `.`).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n")
        fn = _fn(thir, "via_ptr")
        assert fn is not None
        st = fn.body[-1]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_mixed_scalar_and_optional_write_body(self):
        # Both write forms in one body exercise sequential assignment
        # validation -- neither blocks the other's lowering.
        thir = _lower_ctx(
            _F1_RECORDS + "def mixed(b: Box):\n    b.n = 7\n    b.opt = None\n")
        assert _fn(thir, "mixed") is not None



class TestScalarFieldWriteEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _SCALAR_WRITE
        + "def main():\n    c = Counter(3)\n    c.reset()\n    c.copy_field()\n    bump(c, 7)\n"
        + "    print(c.count + c.other)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_scalar_write_emits_arrow_assign(self):
        assert "this->count = 0;" in self._emit(self.SRC, thir=True)

    def test_param_scalar_write_emits_dot_assign(self):
        assert "c.count = n;" in self._emit(self.SRC, thir=True)

    def test_pointer_local_scalar_write_emits_arrow(self):
        src = (
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n"
            + "def main():\n    b = Box(Inner(1))\n    via_ptr(b, b)\n    print(b.inner.value)\n"
            + "main()\n")
        assert "x->value = 5;" in self._emit(src, thir=True)
        assert self._emit(src, thir=True) == self._emit(src, thir=False)



# --- Field writes beyond scalars: record names, container literals, str ---

_FW_RECORDS = (
    "from tpy import Int32, Own, copy\n"
    "class Inner:\n    v: Int32\n"
    "    def __init__(self, v: Int32):\n        self.v = v\n"
    "class Holder:\n    g: Inner\n    tag: str\n    items: list[Int32]\n"
    "    def __init__(self, v: Int32):\n"
    "        self.g = Inner(v)\n        self.tag = \"t\"\n        self.items = [v]\n"
)


class TestFieldWriteFamilies:
    def test_record_name_param_write_routes(self):
        # `self.g = p` off a borrow record param: the bare copy (sema's
        # implicit-copy warning rides the shared sema pass, path-independent).
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def set_g(self, p: Inner):\n        self.g = p\n")
        fn = _fn(thir, "set_g")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.value, THIRName)  # bare copy, no convert

    def test_record_name_own_param_write_moves(self):
        # An Own param at its last use moves (`std::move(p)`, the plain-record
        # STORAGE convert arm).
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def set_own(self, p: Own[Inner]):\n        self.g = p\n")
        fn = _fn(thir, "set_own")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st.value, THIRFormConvert) and st.value.move

    def test_record_name_local_used_after_copies(self):
        # A movable local NOT at its last use copies bare (the AST's
        # `_maybe_move` fires on last use only).
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def set_local(self, v: Int32) -> Int32:\n"
            + "        t = Inner(v)\n        self.g = t\n        return t.v\n")
        fn = _fn(thir, "set_local")
        assert fn is not None
        st = fn.body[1]
        assert isinstance(st.value, THIRName)

    def test_record_copy_call_value_stays_ast(self):
        # `self.g = copy(p)` renders `Inner(p)` via _gen_copy_expr -- an emit
        # form the slice does not mirror; the body stays on the AST path.
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def set_copy(self, p: Inner):\n        self.g = copy(p)\n")
        assert _fn(thir, "set_copy") is None

    def test_record_pointer_local_source_stays_ast(self):
        # A pointer-local source needs the `(*p)` deref (+ possible move) --
        # kept on the AST path by the ws.pointers reject.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def graft(self, other: Box):\n"
            + "        x = self.inner\n        x = other.inner\n"
            + "        self.inner = x\n")
        assert _fn(thir, "graft") is None

    def test_container_literal_write_routes(self):
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def reset(self):\n        self.items = []\n")
        assert _fn(thir, "reset") is not None

    def test_str_field_write_from_param_routes(self):
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def rename(self, s: str):\n        self.tag = s\n")
        fn = _fn(thir, "rename")
        assert fn is not None
        st = fn.body[0]
        # BARE assign: operator=(string_view), no view->owned FormConvert.
        assert isinstance(st.value, THIRName)

    def test_str_field_write_concat_value_stays_ast(self):
        # A concat value is a String rvalue -- source shapes beyond
        # names/literals stay on the AST path.
        thir = _lower_ctx(
            _FW_RECORDS
            + "    def suffix(self, s: str):\n        self.tag = s + \"!\"\n")
        assert _fn(thir, "suffix") is None


# A value-storage Optional[record] field (`std::optional<Inner>`): the
# record-field-write shape at an Optional slot -- a record NAME copies/moves
# the inner, a record RVALUE copies bare; `None` keeps its own nullopt arm.
_OPTREC = (
    "from tpy import Int32, Own\n"
    "class Inner:\n    v: Int32\n"
    "    def __init__(self, v: Int32):\n        self.v = v\n"
    "class Box:\n    opt: Inner | None\n"
    "    def __init__(self):\n        self.opt = None\n"
)


class TestOptionalRecordFieldWrite:
    def test_record_name_copy_routes(self):
        thir = _lower_ctx(
            _OPTREC + "    def stash(self, i: Inner):\n        self.opt = i\n")
        fn = _fn(thir, "stash")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        # Convert to the INNER record storage -> bare copy (NOT ptr_to_optional).
        assert isinstance(st.value, THIRFormConvert) and not st.value.move

    def test_own_param_moves(self):
        thir = _lower_ctx(
            _OPTREC + "    def stash_own(self, i: Own[Inner]):\n        self.opt = i\n")
        st = _fn(thir, "stash_own").body[0]
        assert isinstance(st.value, THIRFormConvert) and st.value.move

    def test_record_rvalue_routes(self):
        thir = _lower_ctx(
            _OPTREC + "    def fresh(self, v: Int32):\n        self.opt = Inner(v)\n")
        st = _fn(thir, "fresh").body[0]
        assert isinstance(st, THIRAssign)
        # An rvalue is STORAGE form already -- copied bare, no convert.
        assert not isinstance(st.value, THIRFormConvert)

    def test_none_keeps_nullopt_arm(self):
        thir = _lower_ctx(
            _OPTREC + "    def clear(self):\n        self.opt = None\n")
        assert _fn(thir, "clear") is not None

    def test_value_record_optional_stays_ast(self):
        # A VALUE-record inner has no plain-non-value convert arm -> AST.
        src = ("from tpy import Int32, ValueType\n"
               "class V(ValueType):\n    x: Int32\n"
               "    def __init__(self, x: Int32):\n        self.x = x\n"
               "class W:\n    o: V | None\n"
               "    def __init__(self):\n        self.o = None\n"
               "    def put(self, v: V):\n        self.o = v\n")
        assert _fn(_lower_ctx(src), "put") is None


class TestOptionalRecordFieldWriteEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _OPTREC
        + "    def set_name(self, i: Inner):\n        self.opt = i\n"
        + "    def set_own(self, i: Own[Inner]):\n        self.opt = i\n"
        + "    def set_new(self, v: Int32):\n        self.opt = Inner(v)\n"
        + "    def clear(self):\n        self.opt = None\n"
        + "def main():\n    b = Box()\n    b.set_name(Inner(1))\n"
        + "    b.set_own(Inner(2))\n    b.set_new(3)\n    b.clear()\n"
        + "    print(0)\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "this->opt = i;" in out               # borrow param: bare copy
        assert "this->opt = std::move(i);" in out    # Own param at last use
        assert "this->opt = Inner(v);" in out        # rvalue
        assert "this->opt = std::nullopt;" in out    # None -> nullopt arm


class TestFieldWriteFamiliesEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _FW_RECORDS
        + "    def set_g(self, p: Inner):\n        self.g = p\n"
        + "    def set_own(self, p: Own[Inner]):\n        self.g = p\n"
        + "    def set_local(self, v: Int32) -> Int32:\n"
        + "        t = Inner(v)\n        self.g = t\n        return t.v\n"
        + "    def reset(self):\n        self.items = []\n"
        + "    def fill(self, v: Int32):\n        self.items = [v, 9]\n"
        + "    def rename(self, s: str):\n        self.tag = s\n"
        + "    def relabel(self):\n        self.tag = \"x\"\n"
        + "def main():\n    h = Holder(1)\n    h.set_g(Inner(2))\n"
        + "    h.set_own(Inner(3))\n    print(h.set_local(4))\n    h.reset()\n"
        + "    h.fill(5)\n    h.rename(\"n\")\n    h.relabel()\n"
        + "    print(h.g.v, h.tag, h.items[0])\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_record_name_copy_and_move_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "this->g = p;" in out              # borrow param: bare copy
        assert "this->g = std::move(p);" in out   # Own param at last use
        assert "this->g = t;" in out              # non-last-use local: copy

    def test_container_and_str_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "this->items = std::vector<int32_t>{};" in out  # spelled empty
        assert "this->items = {v, 9};" in out                  # brace literal
        assert "this->tag = s;" in out                         # bare view assign
        assert 'this->tag = "x";' in out                       # bare literal


# --- Augmented assignment: `x += y` / `recv.field += y` (scalar) ---

_AUG = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n"
    "    def tick(self, n: Int32):\n        self.count += n\n"
    # local aug-assign + a field aug-assign off a record param (non-self)
    "def bump(c: Counter, n: Int32) -> Int32:\n"
    "    n += 1\n    c.count += n\n    return n\n"
)


class TestScalarAugAssign:
    def test_local_aug_assign_routes_as_binop(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRName)
        assert isinstance(st.value, THIRBinOp) and st.value.op == "+"
        # the binop's left operand re-reads the target (the AST likewise
        # substitutes the target string into both the lvalue and the binop).
        assert isinstance(st.value.left, THIRName) and st.value.left.name == "n"

    def test_field_aug_assign_off_param(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[1]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert isinstance(st.value, THIRBinOp)
        assert isinstance(st.value.left, THIRFieldAccess)

    def test_field_aug_assign_off_self(self):
        st = _fn(_lower_ctx(_AUG), "tick").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert st.target.is_arrow
        assert isinstance(st.value, THIRBinOp)

    def test_inplace_dunder_excluded(self):
        # `xs += [v]` resolves to list_extend (__iadd__) -- mutates in place via a
        # method call, not the binop substitution -> AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def grow(v: Int32):\n    xs = [1]\n    xs += [v]\n")
        assert _fn(thir, "grow") is None

    def test_str_aug_assign_routes_as_append(self):
        # `s += t` on a str takes the in-place-append emit -- not the scalar
        # synthetic-binop path this class covers (S3, THIRStrAppend).
        thir = _lower_ctx(
            "def cat(t: str):\n    s = 'a'\n    s += t\n")
        stmt = _fn(thir, "cat").body[1]
        assert isinstance(stmt, THIRStrAppend)

    def test_subscript_aug_assign_routes(self):
        # A subscript target takes the read-modify-write __setitem__ pair
        # (THIRSetItem), not the name-target THIRAssign desugar.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def at(xs: list[Int32], i: Int32):\n    xs[i] += 1\n")
        stmt = _fn(thir, "at").body[0]
        assert isinstance(stmt, THIRSetItem)

    def test_float_local_aug_assign_routes(self):
        # A double `float` is an eligible scalar -- the value-scalar slice is not
        # int-only.
        st = _fn(_lower(
            "def f(a: float) -> float:\n    a += 1.0\n    return a\n"), "f").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)



class TestScalarAugAssignEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _AUG
        + "def main():\n    c = Counter(3)\n    c.tick(2)\n    print(bump(c, 4))\n"
        + "    print(c.count)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_field_aug_emits_arrow(self):
        # No outer parens (the aug-assign RHS is a full statement RHS).
        assert ("this->count = ::tpy::add_check<int32_t>(this->count, n);"
                in self._emit(self.SRC, thir=True))

    def test_floordiv_aug_not_swapped(self):
        # `q //= d` with a non-proven-zero divisor must emit the checked helper,
        # not div_floor -- the AST aug-assign path never swaps it.
        src = (
            "from tpy import Int32\n"
            "def f(q: Int32, d: Int32) -> Int32:\n    q //= d\n    return q\n"
            "def main():\n    print(f(10, 3))\nmain()\n")
        out = self._emit(src, thir=True)
        assert "div_floor" not in out
        assert out == self._emit(src, thir=False)

    def test_other_ops_byte_identical(self):
        # -= *= %= alongside the += / //= already covered.
        src = (
            "from tpy import Int32\n"
            "def f(a: Int32, b: Int32) -> Int32:\n"
            "    a -= b\n    a *= b\n    a %= b\n    return a\n"
            "def main():\n    print(f(20, 3))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)

    def test_float_aug_byte_identical(self):
        src = (
            "def g(a: float, b: float) -> float:\n    a += b\n    a *= b\n    return a\n"
            "def main():\n    print(g(1.5, 2.0))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)


# --- Static / property / dunder-operator methods: every method kind funnels
# its body through gen_body, so the cell only widens the gate -- statics lower
# like free functions (no receiver), property getters/setters like instance
# methods; dunders were already admitted as plain instance methods (the C++
# operator wrappers delegating to them are structural emission, not bodies) ---

def _fns(thir, name):
    return [f for f in thir.functions if f.name == name]


_SPD_METHODS = (
    "from tpy import Int32, readonly\n"
    "class Leaf:\n    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Holder:\n"
    "    value: Int32\n"
    "    opt: Leaf | None\n"
    "    def __init__(self, value: Int32):\n"
    "        self.value = value\n        self.opt = None\n"
    "    @staticmethod\n"
    "    def peek_static(h: Holder) -> Int32:\n"
    "        p = h.opt\n        return h.value\n"
    "    @staticmethod\n"
    "    def bump_static(h: Holder):\n"
    "        h.value = 7\n        h.opt = None\n"
    "    @property\n"
    "    def peeked(self) -> Int32:\n"
    "        p = self.opt\n        return self.value\n"
    "    @peeked.setter\n"
    "    def peeked(self, v: Int32):\n"
    "        self.value = v\n"
)


class TestStaticPropertyMethods:
    def test_static_borrow_off_const_record_param(self):
        # The const verdict for a static's record param resolves through the
        # method's FunctionInfo on the owning record (same lookup as codegen's
        # _get_method_mutated_params), not the free-function registry.
        decl = _fn(_lower_ctx(_SPD_METHODS), "peek_static").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_mutating_static_routes(self):
        # A static that mutates its record param: the param is a mutable ref on
        # both paths (scalar field write + storage-form None write route).
        assert _fn(_lower_ctx(_SPD_METHODS), "bump_static") is not None

    def test_readonly_staticmethod_routes(self):
        # @readonly on a @staticmethod emits with the readonly verdicts dropped
        # (gen_method_def branches on `is_const and not is_static`): those are
        # signature-only, and the static body (no `self`) lowers identically to a
        # plain static, so it routes.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @staticmethod\n    @readonly\n"
            + "    def sm(a: Int32) -> Int32:\n        return a\n")
        assert _fn(thir, "sm") is not None

    def test_property_pair_both_route(self):
        # Getter and setter share one method name in the registry (stored as a
        # two-entry list) but each has its own body -- the overload gate's
        # property-pair carve-out admits both.
        pair = _fns(_lower_ctx(_SPD_METHODS), "peeked")
        assert len(pair) == 2
        getter = next(f for f in pair if not f.params)
        setter = next(f for f in pair if f.params)
        # readonly getter: the borrow off self.opt lifts const.
        decl = getter.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const
        # setter: a scalar field write off a non-const self.
        st = setter.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_pointer_repr_optional_getter_routes(self):
        # A @property getter returning `Leaf | None` (pointer-repr) routes
        # byte-identically via the property-getter arm, which returns the
        # field's storage optional by reference (split from the plain-method
        # optional_to_ptr lift on `is_property_getter`).
        src = (
            _SPD_METHODS
            + "    @property\n"
            + "    def sibling(self) -> Leaf | None:\n        return self.opt\n")
        assert _fn(_lower_ctx(src), "sibling") is not None
        _assert_byte_identical(src)


class TestDunderMethods:
    _SCORE = (
        "from functools import total_ordering\n"
        "from tpy import Int32\n"
        "@total_ordering\n"
        "class Score:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n"
        "    def __eq__(self, other: Score) -> bool:\n"
        "        return self.v == other.v\n"
        "    def __lt__(self, other: Score) -> bool:\n"
        "        return self.v < other.v\n"
    )

    def test_user_comparison_dunders_route(self):
        thir = _lower_ctx(self._SCORE)
        assert _fn(thir, "__eq__") is not None
        assert _fn(thir, "__lt__") is not None

    def test_total_ordering_synthesized_dunders_route(self):
        # @total_ordering synthesizes `__le__`/`__gt__`/`__ge__` bodies from
        # record compares (`self < other`), whose bare-self operand derefs to
        # `(*this)` in value position -- the record-compare arm.
        thir = _lower_ctx(self._SCORE)
        for name in ("__le__", "__gt__", "__ge__"):
            assert _fn(thir, name) is not None, name

    def test_inplace_dunder_excluded(self):
        # __iadd__ takes forced-const params (CONST_PARAMS_METHODS) and returns
        # `*this` -- both outside the mirror.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Acc:\n    total: Int32\n"
            "    def __init__(self, total: Int32):\n        self.total = total\n"
            "    def __iadd__(self, n: Int32) -> Acc:\n"
            "        self.total += n\n        return self\n")
        assert _fn(thir, "__iadd__") is None


class TestStaticPropertyDunderEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _SPD_METHODS
        + "def main():\n"
        + "    h = Holder(3)\n"
        + "    print(Holder.peek_static(h))\n"
        + "    Holder.bump_static(h)\n"
        + "    print(h.peeked)\n"
        + "    h.peeked = 9\n"
        + "    print(h.value)\n"
        + "main()\n"
    )

    def test_static_property_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_static_body_borrow_is_const(self):
        # Inside the static body the record param behaves like a free
        # function's: the unmutated `h`'s borrow lifts const.
        assert ("const Leaf* p = ::tpy::optional_to_ptr(h.opt);"
                in self._emit(self.SRC, thir=True))

    def test_setter_body_writes_through_this(self):
        assert "this->value = v;" in self._emit(self.SRC, thir=True)

    def test_dunders_byte_identical(self):
        src = (
            TestDunderMethods._SCORE
            + "def main():\n"
            + "    a = Score(1)\n    b = Score(2)\n"
            + "    print(a < b)\n    print(a <= b)\n    print(a == b)\n"
            + "main()\n")
        out = self._emit(src, thir=True)
        assert out == self._emit(src, thir=False)
        assert "return (this->v < other.v);" in out


# --- Method receivers beyond a bare name: a one-level field access producing a
# value F1-record (`self.field.method()` / `obj.field.method()`). The receiver
# renders as its own THIRFieldAccess; the outer member access is `.` (is_arrow
# keys on a NAME receiver). Container / Optional / deeper-chain field receivers
# are deferred. ---

_RECV_SHAPE = (
    "from tpy import Int32\n"
    "class Inner:\n    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "    def get(self) -> Int32:\n        return self.n\n"
    "    def bump(self, d: Int32) -> Int32:\n        self.n += d\n        return self.n\n"
    "class Outer:\n    inner: Inner\n"
    "    def __init__(self, x: Int32):\n        self.inner = Inner(x)\n"
    "    def run(self) -> Int32:\n        self.inner.bump(5)\n        return self.inner.get()\n"
    # off a record param in a free function (non-self field receiver, `.` access)
    "def use(o: Outer) -> Int32:\n    o.inner.bump(2)\n    return o.inner.get()\n"
)


class TestMethodReceiverShape:
    def test_self_field_receiver_routes_with_dot_over_arrow_field(self):
        # `self.inner.bump(5)`: the outer method access is `.` (the receiver is
        # not a name); the receiver field access itself is `this->inner`.
        fn = _fn(_lower_ctx(_RECV_SHAPE), "run")
        assert fn is not None
        call = fn.body[0].expr
        assert isinstance(call, THIRMethodCall)
        assert not call.is_arrow and not call.deref_check
        assert isinstance(call.receiver, THIRFieldAccess)
        assert isinstance(call.receiver.receiver, THIRSelf) and call.receiver.is_arrow

    def test_record_param_field_receiver_routes(self):
        # `o.inner.get()` off a record param: the receiver field access is a
        # plain `.` (o is a record param, not a pointer-local).
        fn = _fn(_lower_ctx(_RECV_SHAPE), "use")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRMethodCall) and not ret.value.is_arrow
        assert isinstance(ret.value.receiver, THIRFieldAccess)
        assert not ret.value.receiver.is_arrow

    def test_container_field_receiver_routes(self):
        # `self.items.append(x)` / `self.m.pop(k)`: a scalar-read container field
        # receiver routes the container arm over a bare `this->field` receiver.
        thir, w = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "class C:\n    items: list[Int32]\n    m: dict[Int32, Int32]\n"
            "    def __init__(self):\n        self.items = []\n        self.m = {}\n"
            "    def add(self, x: Int32):\n        self.items.append(x)\n"
            "    def take(self) -> Int32:\n        return self.items.pop()\n"
            "    def dpop(self, k: Int32) -> Int32:\n        return self.m.pop(k)\n")
        for name in ("add", "take", "dpop"):
            fn = _fn(thir, name)
            assert fn is not None, name
        assert w.get("method.recv.container_field", 0) >= 3
        # The receiver is a bare `this->field` THIRFieldAccess (`.`, not `->`
        # from the outer call; the field access itself is arrow off self).
        call = _fn(thir, "add").body[0].expr
        assert isinstance(call, THIRMethodCall) and not call.is_arrow
        assert isinstance(call.receiver, THIRFieldAccess)
        assert isinstance(call.receiver.receiver, THIRSelf) and call.receiver.is_arrow

    def test_value_record_field_receiver_witnesses_record_field(self):
        # The plain value F1-record field receiver rides its own face
        # (`method.recv.record_field`) -- distinct from the container-field
        # and subscript/method-chain receiver faces.
        thir, w = _lower_ctx_witnessed(_RECV_SHAPE)
        assert _fn(thir, "run") is not None
        assert w.get("method.recv.record_field", 0) >= 1

    def test_generic_record_field_receiver_routes(self):
        # `self.w.get_n()` where `w: Wrap[Int32]` -- a concrete-arg generic
        # record field. `_f1_record` admits the generic (Int32 is an F1 type
        # arg), so the receiver renders bare (`this->w`) and the call routes
        # the user-record arm exactly as a plain record field would.
        thir, w = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "class Wrap[T]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def get_n(self) -> Int32:\n        return self.n\n"
            "class Holder:\n    w: Wrap[Int32]\n"
            "    def __init__(self, w: Wrap[Int32]):\n        self.w = w\n"
            "    def read(self) -> Int32:\n        return self.w.get_n()\n")
        fn = _fn(thir, "read")
        assert fn is not None
        assert w.get("method.recv.record_field", 0) >= 1
        call = fn.body[-1].value
        assert isinstance(call, THIRMethodCall) and not call.is_arrow
        assert isinstance(call.receiver, THIRFieldAccess)
        assert isinstance(call.receiver.receiver, THIRSelf) and call.receiver.is_arrow

    def test_native_record_field_receiver_routes(self):
        # `self.v.total()` where `v: Vec2` (@native record). `_f1_record` admits
        # native records (spelled via native_cpp_names), so the field receiver
        # routes like a same-module one.
        thir, w = _lower_ctx_witnessed(
            "from tpy.extern import native\n"
            "from tpy import Int32\n"
            "@native\n"
            "class Vec2:\n    x: Int32\n    y: Int32\n"
            "    def total(self) -> Int32: ...\n"
            "class Holder:\n    v: Vec2\n"
            "    def __init__(self, v: Vec2):\n        self.v = v\n"
            "    def read(self) -> Int32:\n        return self.v.total()\n")
        fn = _fn(thir, "read")
        assert fn is not None
        assert w.get("method.recv.record_field", 0) >= 1

    def test_set_field_receiver_excluded(self):
        # `self.s.add(x)`: a set has no `__getitem__` -- outside the
        # container-scalar-read admit set -> stays AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class S:\n    s: set[Int32]\n"
            "    def __init__(self):\n        self.s = set()\n"
            "    def add(self, x: Int32):\n        self.s.add(x)\n")
        assert _fn(thir, "add") is None

    def test_optional_field_receiver_excluded(self):
        # `self.opt.get()`: an Optional field receiver needs the outer deref /
        # `(*obj)` unwrap the value-record arm does not emit -> AST.
        thir = _lower_ctx(
            _RECV_SHAPE
            + "class Holder:\n    opt: Inner | None\n"
            "    def __init__(self):\n        self.opt = None\n"
            "    def peek(self) -> Int32:\n        return self.opt.get()\n")
        assert _fn(thir, "peek") is None

    def test_deep_chain_receiver_excluded(self):
        # `o.mid.inner.get()`: a two-level field chain -- the receiver's own
        # receiver is a field access, not a name -> only one level is admitted.
        thir = _lower_ctx(
            _RECV_SHAPE
            + "class Mid:\n    inner: Inner\n"
            "    def __init__(self, x: Int32):\n        self.inner = Inner(x)\n"
            "class Deep:\n    mid: Mid\n"
            "    def __init__(self, x: Int32):\n        self.mid = Mid(x)\n"
            "def reach(d: Deep) -> Int32:\n    return d.mid.inner.get()\n")
        assert _fn(thir, "reach") is None


class TestMethodReceiverShapeEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _RECV_SHAPE
        + "def main():\n    o = Outer(10)\n    print(o.run())\n    print(use(o))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_field_receiver_emits_this_arrow_dot(self):
        assert "this->inner.bump(5);" in self._emit(self.SRC, thir=True)

    def test_param_field_receiver_emits_dot(self):
        assert "o.inner.get()" in self._emit(self.SRC, thir=True)

    GENERIC_SRC = (
        "from tpy import Int32\n"
        "class Wrap[T]:\n    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "    def get_n(self) -> Int32:\n        return self.n\n"
        "class Holder:\n    w: Wrap[Int32]\n"
        "    def __init__(self, w: Wrap[Int32]):\n        self.w = w\n"
        "    def read(self) -> Int32:\n        return self.w.get_n()\n"
        "def main():\n    print(Holder(Wrap(7)).read())\n"
        "main()\n"
    )

    def test_generic_record_field_receiver_byte_identical(self):
        assert (self._emit(self.GENERIC_SRC, thir=True)
                == self._emit(self.GENERIC_SRC, thir=False))


class TestContainerFieldReceiverEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    # Container field receiver off self (self.data.append / .pop, self.m.pop)
    # AND off a record param (b.data.append / .pop) -- both render the receiver
    # as its bare field access.
    SRC = (
        "from tpy import Int32\n"
        "class Buf:\n    data: list[Int32]\n    m: dict[Int32, Int32]\n"
        "    def __init__(self):\n        self.data = []\n        self.m = {}\n"
        "    def add(self, x: Int32):\n        self.data.append(x)\n"
        "    def take(self) -> Int32:\n        return self.data.pop()\n"
        "    def dpop(self, k: Int32) -> Int32:\n        return self.m.pop(k)\n"
        "def fill(b: Buf, x: Int32):\n    b.data.append(x)\n"
        "def drain(b: Buf) -> Int32:\n    return b.data.pop()\n"
        "def main():\n    b = Buf()\n    fill(b, 7)\n    print(drain(b))\n    print(b.take())\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_container_field_emits_this_arrow_receiver(self):
        out = self._emit(self.SRC, thir=True)
        assert "this->data.push_back(x);" in out
        assert "::tpy::pop_back(this->data)" in out
        assert "::tpy::dict_pop(this->m, k)" in out

    def test_param_container_field_emits_dot_receiver(self):
        out = self._emit(self.SRC, thir=True)
        assert "b.data.push_back(x);" in out
        assert "::tpy::pop_back(b.data)" in out


# --- Non-F1 record frontier, stage 1: @native records. Their C++ TYPE spelling
# already agrees with the resolver (native_cpp_names), methods dispatch on
# fi.native_name, and field renames ride native_field_name (stamped into THIR
# field access via _field_cpp). So a native record renders byte-identically off
# the same body slice as an F1 record -- admitted by the widened _f1_record. ---

_NATIVE_REC = (
    "from tpy.extern import native, native_field\n"
    "from tpy import Int32\n"
    "@native\nclass Vec2:\n"
    "    x: Int32 = native_field(\"m_x\")\n"
    "    y: Int32 = native_field(\"m_y\")\n"
    "def read_x(v: Vec2) -> Int32:\n    return v.x\n"
    "def add(v: Vec2) -> Int32:\n    return v.x + v.y\n"
    "def take(v: Vec2) -> Int32:\n    return read_x(v)\n"
)


class TestNativeRecordFrontier:
    def test_native_field_read_uses_rename(self):
        # `v.x` on a @native record renders the native_field rename `m_x`, not
        # the source name -- the _field_cpp stamp, the one native divergence.
        ret = _fn(_lower_ctx(_NATIVE_REC), "read_x").body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)
        assert ret.value.field_cpp == "m_x"

    def test_native_record_body_routes(self):
        # A body reading native-record fields + a binop routes (the record is a
        # signature param -- AST -- but the body is now in the slice).
        assert _fn(_lower_ctx(_NATIVE_REC), "add") is not None

    def test_native_record_bare_name_arg_routes(self):
        # A native-record name passed to a free callee renders bare on both
        # paths -- _record_pass_through_arg admits it via the widened slice.
        assert _fn(_lower_ctx(_NATIVE_REC), "take") is not None

    def test_native_record_field_ctor_routes(self):
        # The ctor-dominated win: a record with a @native-record-typed field
        # copies it in the member-init list -- native records are now in the
        # ctor slice, so the MIL routes.
        ctor = _lower_ctor(
            "from tpy.extern import native, native_field\n"
            "from tpy import Int32\n"
            "@native\nclass Vec2:\n    x: Int32 = native_field(\"m_x\")\n"
            "class Holder:\n    v: Vec2\n"
            "    def __init__(self, v: Vec2):\n        self.v = v\n",
            "Holder")
        assert ctor is not None

    def test_native_record_plain_method_call_routes(self):
        # A @native record's PLAIN method (no native_name / cpp_template, just a
        # `...` declaration) passes `_plain_method_fi_ok`, so a bare `v.mag()`
        # call routes byte-identically. A native_name RENAME on a native record
        # also routes (see the RENAMED tests below); only cpp_template /
        # native_function stay AST.
        src = ("from tpy.extern import native\nfrom tpy import Int32\n"
               "@native\nclass NV:\n    x: Int32\n"
               "    def mag(self) -> Int32: ...\n"
               "def call_mag(v: NV) -> Int32:\n    return v.mag()\n")
        assert _fn(_lower_ctx(src), "call_mag") is not None
        def emit(thir):
            compiler, modules = _compile(src)
            entry = _entry(modules)
            hpp, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return hpp + cpp
        assert emit(True) == emit(False)

    # A @native record whose method carries a native_name RENAME (the file
    # handle `fh.write` shape). The member resolves to `fi.native_name` in the
    # method-call emit -- a bare `recv.name(args)`, byte-identical -- so the
    # call routes. Only native records admit the rename; a native_function
    # (free-function form) or cpp_template stays AST.
    _RENAMED = ("from tpy.extern import native\nfrom tpy import Int32\n"
                "@native(\"nsFile\")\nclass NF:\n"
                "    @native(\"do_write\")\n"
                "    def write(self, n: Int32) -> Int32: ...\n"
                "    @native(\"do_close\")\n"
                "    def close(self) -> None: ...\n")

    def test_native_renamed_method_call_routes(self):
        src = (self._RENAMED
               + "def use(f: NF) -> Int32:\n    return f.write(5)\n")
        assert _fn(_lower_ctx(src), "use") is not None

    def test_native_renamed_void_method_stmt_routes(self):
        # A void native method in statement position (`f.close()`) routes and
        # emits the bare call as a statement.
        src = (self._RENAMED
               + "def shut(f: NF) -> None:\n    f.close()\n")
        assert _fn(_lower_ctx(src), "shut") is not None

    def test_native_renamed_method_emit_byte_identical(self):
        src = (self._RENAMED
               + "def use(f: NF) -> Int32:\n    return f.write(5)\n"
               + "def shut(f: NF) -> None:\n    f.close()\n")
        def emit(thir):
            compiler, modules = _compile(src)
            entry = _entry(modules)
            hpp, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return hpp + cpp
        out = emit(True)
        assert out == emit(False)
        # the rename spells the member; a plain `.` call, not `::sym(recv,...)`
        assert "f.do_write(5)" in out
        assert "f.do_close();" in out

    def test_native_function_method_stays_ast(self):
        # A @native(function=True) method takes the free-function form
        # `::sym(recv, args)`, a different emit than the plain member call, so
        # it stays AST (the native_method arm admits native_name only).
        src = ("from tpy.extern import native\nfrom tpy import Int32\n"
               "@native(\"nsF\")\nclass NF:\n"
               "    @native(\"sz\", function=True)\n"
               "    def size(self) -> Int32: ...\n"
               "def use(f: NF) -> Int32:\n    return f.size()\n")
        assert _fn(_lower_ctx(src), "use") is None


class TestNativeRecordFrontierEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_native_record_byte_identical(self):
        assert self._emit(_NATIVE_REC, thir=True) == self._emit(_NATIVE_REC, thir=False)

    def test_native_field_rename_in_emit(self):
        out = self._emit(_NATIVE_REC, thir=True)
        assert "v.m_x" in out and "v.m_y" in out


# --- Non-F1 record frontier, stage 2: cross-module non-native records. Their
# C++ type spelling qualifies via native_cpp_names exactly as the resolver's
# imported_record_qualification does, so `_f1_record` admits them (only generic
# records stay AST). ---

_XMOD_GEO = (
    "from tpy import Int32\n"
    "class Point:\n    x: Int32\n    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32):\n"
    "        self.x = x\n        self.y = y\n"
)


class TestCrossModuleRecordFrontier:
    def _setup(self, tmp_path):
        (tmp_path / "geo.py").write_text(_XMOD_GEO)
        return tmp_path

    def test_cross_module_field_read_routes(self, tmp_path):
        lib = self._setup(tmp_path)
        src = ("from geo import Point\nfrom tpy import Int32\n"
               "def read_x(p: Point) -> Int32:\n    return p.x\n"
               "def sum2(p: Point) -> Int32:\n    return p.x + p.y\n")
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "read_x") is not None
        assert _fn(thir, "sum2") is not None

    def test_cross_module_record_byte_identical(self, tmp_path):
        lib = self._setup(tmp_path)
        src = ("from geo import Point\nfrom tpy import Int32\n"
               "def read_x(p: Point) -> Int32:\n    return p.x\n"
               "def make() -> Int32:\n    p = Point(3, 4)\n    return p.x\n")
        def emit(thir_flag):
            compiler, modules = _compile(src, extra_lib_dirs=[lib])
            entry = _entry(modules)
            hpp, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir_flag))
            return hpp + cpp
        out = emit(True)
        assert out == emit(False)
        # the body spells the record fully qualified, like the resolver
        assert "::tpyapp::geo::Point" in out

    def test_cross_module_record_field_ctor_routes(self, tmp_path):
        # Cross-module ctor-field storage: a local record with a field typed as
        # another module's record, copied in the member-init list.
        lib = self._setup(tmp_path)
        ctor = _lower_ctor(
            "from geo import Point\n"
            "class Holder:\n    p: Point\n"
            "    def __init__(self, p: Point):\n        self.p = p\n",
            "Holder", extra_lib_dirs=[lib])
        assert ctor is not None

    def test_cross_module_method_call_routes(self, tmp_path):
        # A cross-module TPy record's methods are regular (not @native) -> they
        # route, unlike native-record methods.
        (tmp_path / "geo.py").write_text(
            _XMOD_GEO
            + "    def mag(self) -> Int32:\n        return self.x\n")
        src = ("from geo import Point\nfrom tpy import Int32\n"
               "def call_mag(p: Point) -> Int32:\n    return p.mag()\n")
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])
        assert _fn(thir, "call_mag") is not None

    def test_cross_module_ctor_qualified_spelling(self, tmp_path):
        # An imported record's ctor call routes and spells the declaring
        # module's qualification (ctor.cross_module) -- `_gen_call`'s
        # record-branch qual arm, mirrored on THIRCtorCall.type_cpp.
        lib = self._setup(tmp_path)
        src = ("from geo import Point\nfrom tpy import Int32\n"
               "def make() -> Int32:\n    p = Point(3, 4)\n    return p.x\n")
        thir, witnesses = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "make") is not None
        assert witnesses.get("ctor.cross_module", 0) >= 1
        def emit(thir_flag):
            compiler, modules = _compile(src, extra_lib_dirs=[lib])
            entry = _entry(modules)
            hpp, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir_flag))
            return hpp + cpp
        out = emit(True)
        assert out == emit(False)
        assert "= ::tpyapp::geo::Point(3, 4);" in out

    def test_import_alias_ctor_spells_canonical_name(self, tmp_path):
        # `from geo import Point as Pt`: the qual arm spells the CANONICAL
        # record name from record_qualification, not the alias.
        lib = self._setup(tmp_path)
        src = ("from geo import Point as Pt\nfrom tpy import Int32\n"
               "def make() -> Int32:\n    p = Pt(3, 4)\n    return p.x\n")
        thir, witnesses = _lower_ctx_witnessed(src, extra_lib_dirs=[lib])
        assert _fn(thir, "make") is not None
        assert witnesses.get("ctor.cross_module", 0) >= 1
        compiler, modules = _compile(src, extra_lib_dirs=[lib])
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "= ::tpyapp::geo::Point(3, 4);" in cpp

    def test_short_name_collision_falls_back(self, tmp_path):
        # Two records both named Tag in different modules: the ambiguous
        # short-name lookup (`Tag(5)` resolving to a DIFFERENT record than
        # sema's result type) rejects at `_ctor_shape_ok` -> that body falls
        # back; the unambiguous alias (`BlueTag(10)`) routes qualified. The
        # collision guard is what keeps the lowering's qual spelling honest.
        (tmp_path / "red.py").write_text(
            "from tpy import Int32\n"
            "class Tag:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n")
        (tmp_path / "blue.py").write_text(
            "from tpy import Int32\n"
            "class Tag:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n")
        src = ("from red import Tag\n"
               "from blue import Tag as BlueTag\n"
               "def make_red() -> None:\n    t = Tag(5)\n    print(t.n)\n"
               "def make_blue() -> None:\n    b = BlueTag(10)\n    print(b.n)\n")
        thir = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])[0]
        assert _fn(thir, "make_red") is None
        assert _fn(thir, "make_blue") is not None
        def emit(thir_flag):
            compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
            entry = _entry(modules)
            hpp, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir_flag))
            return hpp + cpp
        out = emit(True)
        assert out == emit(False)
        assert "= ::tpyapp::blue::Tag(10);" in out


# --- F5 stage A: generic records with CONCRETE type args (`Pair[Int32]`).
# The base name resolves as for any user record and `to_cpp()`'s type-arg
# recursion agrees with the resolver iff every arg is itself in the slice
# (scalar / F1-record / INT). A body that only MENTIONS such an instantiation
# routes; the generic record's OWN templated bodies (TypeParamRef args) stay AST
# until stage B/C. ---

_GEN_CONCRETE = (
    "from tpy import Int32\n"
    "class Pair[T]:\n    first: T\n    second: T\n"
    "    def __init__(self, a: T, b: T):\n"
    "        self.first = a\n        self.second = b\n"
    "def read_first(p: Pair[Int32]) -> Int32:\n    return p.first\n"
    "def sum_pair(p: Pair[Int32]) -> Int32:\n    return p.first + p.second\n"
)


class TestGenericRecordConcreteArgFrontier:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_concrete_arg_field_read_routes(self):
        # `p.first` off a `Pair[Int32]` param resolves the substituted field type
        # (Int32) -- a scalar read off a now-F1 generic instantiation.
        thir = _lower_ctx(_GEN_CONCRETE)
        assert _fn(thir, "read_first") is not None
        assert _fn(thir, "sum_pair") is not None

    def test_concrete_arg_byte_identical(self):
        assert self._emit(_GEN_CONCRETE, thir=True) == self._emit(_GEN_CONCRETE, thir=False)
        out = self._emit(_GEN_CONCRETE, thir=True)
        assert "Pair<int32_t>" in out

    def test_generic_record_own_ctor_routes(self):
        # Stage B: the generic record's OWN ctor (`Pair[T].__init__`, TypeParamRef
        # self + `T` fields fed by `T` params) routes -- a bare `T` param copies
        # into a `T` field. Byte-identical (the AST-emitted template header /
        # signature pairs with the THIR MIL tail).
        assert _lower_ctor(_GEN_CONCRETE, "Pair") is not None
        assert self._emit(_GEN_CONCRETE, thir=True) == self._emit(_GEN_CONCRETE, thir=False)

    def test_nonslice_arg_still_excluded(self):
        # A generic arg outside the byte-identical slice (a tuple) keeps the
        # outer generic on the AST path (element qualification diverges).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Box2[T]:\n    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "def read(b: Box2[tuple[Int32, Int32]]) -> Int32:\n    return b.v\n")
        assert _fn(thir, "read") is None

    def test_generic_field_in_nongeneric_ctor_routes(self):
        # A non-generic record with a concrete-arg generic field (`Pair[Int32]`)
        # copies it in the MIL -- the field type is now F1, so the ctor routes.
        src = (
            "from tpy import Int32\n"
            "class Pair[T]:\n    first: T\n    second: T\n"
            "    def __init__(self, a: T, b: T):\n"
            "        self.first = a\n        self.second = b\n"
            "class Holder:\n    p: Pair[Int32]\n"
            "    def __init__(self, p: Pair[Int32]):\n        self.p = p\n")
        assert _lower_ctor(src, "Holder") is not None
        assert self._emit(src, thir=True) == self._emit(src, thir=False)

    def test_int_kind_type_arg_routes(self):
        # An INT-kind type param (`Buf[T, N: int]`, instantiated `Buf[Int32, 8]`)
        # -- the raw-int arg `8` passes `_f1_record_type_arg_ok`'s isinstance(int)
        # branch and the self-type carries the INT-kind TypeParamRef.
        src = (
            "from tpy import Int32\n"
            "class Buf[T, N: int]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "def read(b: Buf[Int32, 8]) -> Int32:\n    return b.n\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "read") is not None


# --- F5 stage C: generic-record method bodies over `T` VALUES. A `T` field
# read / `T` param read / `T` return / `T` field write is a form-neutral
# pass-through (the C++ template's `val_or_ref_t<T>` / `param_val_or_ref_t<T>`
# traits resolve value-vs-ref per instantiation, so the source-level render is
# byte-identical to the AST for any `T`). A `T` field write emits as a plain
# assign (`field = v` / `field = std::move(v)`, the BORROW->STORAGE convert's
# TypeParamRef arm). A `T` local decl is still unhandled and falls back
# byte-identically (a separate follow-up). ---

_GEN_T_METHODS = (
    "from tpy import Int32, Own\n"
    "class Cell[T]:\n    value: T\n    other: T\n"
    "    def __init__(self, v: Own[T], o: Own[T]):\n"
    "        self.value = v\n        self.other = o\n"
    "    def get(self) -> T:\n        return self.value\n"
    "    def pick(self) -> T:\n        return self.other\n"
    "    def echo(self, v: T) -> T:\n        return v\n"
    "    def store(self, v: T) -> None:\n        self.value = v\n"
    "    def copy_out(self) -> T:\n        x = self.value\n        return x\n"
)


class TestGenericRecordTValueMethods:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_t_field_read_return_routes(self):
        thir = _lower_ctx(_GEN_T_METHODS)
        assert _fn(thir, "get") is not None    # T field read + T return
        assert _fn(thir, "pick") is not None   # a second T field
        assert _fn(thir, "echo") is not None   # T param read + T return

    def test_t_value_methods_byte_identical(self):
        assert self._emit(_GEN_T_METHODS, thir=True) == self._emit(_GEN_T_METHODS, thir=False)

    def test_t_field_write_routes(self):
        # A `T` field write (`self.value = v`) routes: a plain assign via the
        # BORROW->STORAGE convert's TypeParamRef arm (`this->value = v;`), byte
        # identical to the AST's `param_val_or_ref_t<T>` copy.
        thir = _lower_ctx(_GEN_T_METHODS)
        assert _fn(thir, "store") is not None

    def test_t_local_decl_falls_back(self):
        # A `T` local decl (`x = self.value`) is unhandled -- falls back.
        thir = _lower_ctx(_GEN_T_METHODS)
        assert _fn(thir, "copy_out") is None

    def test_own_t_param_ctor_moves(self):
        # `Cell.__init__(self, v: Own[T], o: Own[T])` moves each Own[T] param
        # into its `T` field -- the ctor routes and every MIL init is `move=True`
        # (the TypeParamRef field arm's move path; a routing assertion, since the
        # byte-identical test alone would pass even if the ctor fell back).
        ctor = _lower_ctor(_GEN_T_METHODS, "Cell")
        assert ctor is not None
        assert len(ctor.mil_inits) == 2
        assert all(mi.move for mi in ctor.mil_inits)


# --- Marker calls: module-qualified and static method-call shapes whose emit
# is receiver-less (THIRCall with a pre-rendered callee_cpp / native symbol) ---

_MARKER_HELPER = (
    "from tpy import Int32\n"
    "def bump(n: Int32) -> Int32:\n"
    "    return n + 1\n"
    "def shout(n: Int32) -> None:\n"
    "    print(n)\n"
    "def pick[T](a: T, b: T) -> T:\n"
    "    return a\n"
    "class Kit:\n"
    "    @staticmethod\n"
    "    def twice(n: Int32) -> Int32:\n"
    "        return n * 2\n"
)

_MARKER_SRC = (
    "from tpy import Int32\n"
    "import helper\n"
    "def use(n: Int32) -> Int32:\n"
    "    m = helper.bump(n)\n"
    "    helper.shout(m)\n"
    "    if helper.bump(m) > 3:\n"
    "        m = m + 1\n"
    "    return helper.bump(m)\n"
    "def gen(n: Int32) -> Int32:\n"
    "    return helper.pick(n, 2)\n"
    "def mstatic(n: Int32) -> Int32:\n"
    "    return helper.Kit.twice(n)\n"
    "def main():\n"
    "    print(use(2))\n"
    "    print(gen(1))\n"
    "    print(mstatic(3))\n"
    "main()\n"
)


class TestMarkerModuleCall:
    """Module-qualified calls (`import helper; helper.bump(x)`) route as
    THIRCall with the pre-rendered qualified spelling on callee_cpp --
    module_qualified_callee_cpp, the ONE decision shared with the AST's
    user_module_call arm. Generic and module-static forms stay AST."""

    def _lowered(self, tmp_path):
        (tmp_path / "helper.py").write_text(_MARKER_HELPER)
        return _lower_ctx_witnessed(_MARKER_SRC, extra_lib_dirs=[tmp_path])

    def test_routes_with_qualified_spelling(self, tmp_path):
        thir, witnessed = self._lowered(tmp_path)
        fn = _fn(thir, "use")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.init, THIRCall)
        assert decl.init.callee_cpp == "::tpyapp::helper::bump"
        assert decl.init.native_name is None
        # value / stmt / condition / return positions all witnessed
        assert witnessed.get("call.marker_qualified", 0) >= 4

    def test_generic_module_call_routes(self, tmp_path):
        # `helper.pick(a, b)` spells `::tpyapp::helper::pick<int32_t>(a, b)`
        # -- the wave-7 residue's generic_qualified kind.
        thir, faces = self._lowered(tmp_path)
        assert _fn(thir, "gen") is not None
        assert faces.get("call.generic_qualified", 0) >= 1

    def test_module_static_stays_ast(self, tmp_path):
        # `helper.Kit.twice(n)` spells through the module-static arm
        # (`::tpyapp::helper::Kit::twice`) -- not mirrored yet.
        thir, _ = self._lowered(tmp_path)
        assert _fn(thir, "mstatic") is None

    def test_byte_identical(self, tmp_path):
        (tmp_path / "helper.py").write_text(_MARKER_HELPER)
        compiler, modules = _compile(_MARKER_SRC, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::tpyapp::helper::bump(" in thir_out[1]


_NATIVE_MODULE_SRC = (
    "from tpy import Float64\n"
    "import math\n"
    "def f(x: Float64) -> Float64:\n"
    "    return math.sqrt(x)\n"
    "def main():\n"
    "    print(f(4.0))\n"
    "main()\n"
)


class TestMarkerModuleNativeCall:
    """A bare-@native cross-module callee (`import math; math.sqrt(x)`)
    routes on the THIRCall native_name arm -- the same `::std::sqrt(x)`
    render as the from-imported free call, reached through the
    user_module_call marker."""

    def test_routes_on_native_arm(self):
        thir, witnessed = _lower_ctx_witnessed(_NATIVE_MODULE_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call, THIRCall)
        assert call.native_name == "std::sqrt"
        assert call.callee_cpp is None and call.cpp_template is None
        assert witnessed.get("call.module_native", 0) >= 1

    def test_byte_identical(self):
        compiler, modules = _compile(_NATIVE_MODULE_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::std::sqrt(" in thir_out[1]


_STATIC_SRC = (
    _F1_RECORDS
    + "    @staticmethod\n"
    + "    def make(n: Int32) -> Int32:\n"
    + "        return n + 10\n"
    + "def call_static(n: Int32) -> Int32:\n"
    + "    return Box.make(n)\n"
)


class TestMarkerStaticCall:
    """A same-module static call (`Box.make(n)`) routes as THIRCall with the
    `Box::make` spelling on callee_cpp (static_method_callee_cpp). The args
    interpolate the same first-pass loop as a plain free call (probe-verified:
    the Own move cascade fires there), so the free-call arg rows apply."""

    def test_routes_with_class_spelling(self):
        thir, witnessed = _lower_ctx_witnessed(_STATIC_SRC)
        fn = _fn(thir, "call_static")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRCall)
        assert ret.value.callee_cpp == "Box::make"
        assert witnessed.get("call.marker_qualified", 0) >= 1

    def test_byte_identical(self):
        compiler, modules = _compile(_STATIC_SRC + "print(call_static(1))\n")
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "Box::make(" in thir_out[1]

    def test_super_call_routes(self):
        # The super marker lowers as a "qualified" THIRCall whose callee is
        # the pre-rendered `this->A::val` spelling.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self):\n        self.n = 1\n"
            "    def val(self) -> Int32:\n        return self.n\n"
            "class B(A):\n"
            "    def __init__(self):\n        super().__init__()\n"
            "    def doubled(self) -> Int32:\n        return super().val() * 2\n")
        assert _fn(thir, "val") is not None
        assert _fn(thir, "doubled") is not None


# --- @builtin_type record receivers (the Poll/Waker family) ---


class TestBuiltinRecordReceiver:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # A formatter-free @builtin_type record spells the user-record
    # {name}<{args}> form, so its bodied methods route like a user
    # record's; Poll itself is exercised by every async-importing corpus
    # case, so the unit uses a local stand-in to isolate the gate.
    SRC = (
        "from tpy import Int32\n"
        "from tpy.extern import builtin_type\n"
        "@builtin_type('tpy.test.Gauge')\n"
        "class Gauge:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "    def bump(self) -> Int32:\n"
        "        self.n += 1\n        return self.n\n"
        "def main():\n    g = Gauge(1)\n    print(g.bump())\nmain()\n")

    def test_builtin_record_method_routes(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "bump") is not None
        assert witnessed.get("recv.builtin_record", 0) > 0

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_formatter_builtin_stays_excluded(self):
        # A container receiver (list) carries a cpp_formatter -- its
        # spelled C++ (std::vector) is not the nominal form, so the
        # builtin-record arm must not admit it as an F1 record: a body
        # taking `list[Int32]` still routes ONLY via the container
        # family (probe: the record param gate, isolated by a body that
        # reads a field off it, which no container family admits).
        from .lower.predicates import _f1_record
        from ..typesys import NominalType, make_list
        compiler, modules = _compile(self.SRC)
        from ..compilation_context import activate_compiler
        with activate_compiler(compiler):
            analyzer = compiler.modules[_entry(modules).name].analyzer
            lt = make_list(NominalType("Int32", "tpy.Int32"))
            assert not _f1_record(lt, analyzer)


_STATIC_TEMPLATE_SRC = (
    "from tpy import Int32, UInt32\n"
    "class Util:\n"
    "    @staticmethod\n"
    "    def first[T](a: T, b: T) -> T:\n"
    "        return a\n"
    "def f(i: Int32) -> Int32:\n"
    "    u = UInt32.trunc(i)\n"
    "    v = UInt32.add_wrap(u, UInt32(1))\n"
    "    return Int32.trunc(v)\n"
    "def g(n: Int32) -> Int32:\n"
    "    return Util.first(n, 2)\n"
    "def main():\n"
    "    print(f(41))\n"
    "    print(g(7))\n"
    "main()\n"
)


class TestMarkerStaticTemplate:
    """A same-module static `@cpp_template` call (`UInt32.trunc(i)`) routes as
    THIRCall with the positional-only template on cpp_template -- generic
    statics included, since gen_call_from_fi's type-arg substitution is a
    no-op on a {T}-free template. A generic static WITHOUT a template
    (`Util.first(n, 2)` -> `Util::first<int32_t>(n, 2)`) needs the `<T>`
    spelling and stays AST (the generics frontier)."""

    def test_routes_with_template(self):
        thir, witnessed = _lower_ctx_witnessed(_STATIC_TEMPLATE_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.init, THIRCall)
        assert decl.init.cpp_template is not None
        assert decl.init.callee_cpp is None and decl.init.native_name is None
        assert witnessed.get("call.static_template", 0) >= 3

    def test_generic_static_with_targs_routes(self):
        # The generic static template substitutes {T} through the shared
        # expand_fi_template and expands positionally -- wave-7 residue.
        thir, _ = _lower_ctx_witnessed(_STATIC_TEMPLATE_SRC)
        assert _fn(thir, "g") is not None

    def test_byte_identical(self):
        compiler, modules = _compile(_STATIC_TEMPLATE_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "static_cast<uint32_t>(i)" in thir_out[1]


_PTR_DEREF_SRC = (
    "from tpy import Int32, Ptr, take_ptr\n"
    "class Cell:\n"
    "    n: Int32\n"
    "    def __init__(self):\n        self.n = 3\n"
    "    def val(self) -> Int32:\n        return self.n\n"
    "    def bump(self, k: Int32) -> Int32:\n        return self.n + k\n"
    "class Holder:\n"
    "    cell: Ptr[Cell]\n"
    "    def __init__(self, c: Ptr[Cell]):\n        self.cell = c\n"
    "    def get(self) -> Int32:\n        return self.cell.val()\n"
    "def f(p: Ptr[Cell]) -> Int32:\n"
    "    a = p.val()\n"
    "    b = p.bump(2)\n"
    "    return a + b\n"
    "def main():\n"
    "    c = Cell()\n"
    "    print(f(take_ptr(c)))\n"
    "    h = Holder(take_ptr(c))\n"
    "    print(h.get())\n"
    "main()\n"
)


class TestPtrDerefMethodCall:
    """A Deref method call through a `Ptr[T]` value receiver routes as
    THIRMethodCall on the existing arms: unproven -> deref_check
    (`::tpy::deref_check(p).val()`), sema-proven non-null (the post-access
    narrowing after the first deref) -> is_arrow (`p->bump(2)`). A field-read
    receiver (`self.cell.val()`) rides the same arms over the F1 field
    render. Box/Rc wrapper receivers spell `.__deref__()` -- not mirrored,
    stays AST (see test_box_deref_stays_ast)."""

    def test_ptr_name_receiver_both_arms(self):
        thir, witnessed = _lower_ctx_witnessed(_PTR_DEREF_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        first = fn.body[0].init
        second = fn.body[1].init
        assert isinstance(first, THIRMethodCall)
        assert first.deref_check and not first.is_arrow
        assert isinstance(second, THIRMethodCall)
        assert second.is_arrow and not second.deref_check
        assert witnessed.get("method.ptr_checked", 0) >= 1
        assert witnessed.get("method.ptr_arrow", 0) >= 1

    def test_ptr_field_receiver_routes(self):
        thir, _ = _lower_ctx_witnessed(_PTR_DEREF_SRC)
        fn = _fn(thir, "get")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRMethodCall)
        assert ret.value.deref_check
        assert isinstance(ret.value.receiver, THIRFieldAccess)

    def test_byte_identical(self):
        compiler, modules = _compile(_PTR_DEREF_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::tpy::deref_check(p).val()" in thir_out[1]
        assert "p->bump(2)" in thir_out[1]

    def test_box_deref_routes(self):
        # A Box receiver resolves through the user `__deref__` chain and spells
        # `b.__deref__().val()` -- routes byte-identically via the user-deref
        # proxy-method-call arm (the deref chain spells identically regardless
        # of Box's type param).
        src = (
            "from tpy import Int32\n"
            "from tplib.box import Box\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self):\n        self.n = 1\n"
            "    def val(self) -> Int32:\n        return self.n\n"
            "def use_box(b: Box[A]) -> Int32:\n"
            "    return b.val()\n")
        assert _fn(_lower_ctx(src), "use_box") is not None
        _assert_byte_identical(src)


# --- @auto_readonly / auto_own clone pairs (not overload-set hazards) ---


class TestAutoCloneOverloadCarveout:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # A clone-pair method call in an otherwise-eligible body: the call
    # renders the same plain `recv.method(args)` whichever clone sema
    # resolved, so the body routes; the clone DEFS themselves gate on
    # their own body content (honest re-attribution, not this test's
    # concern).
    SRC = (
        "from tpy import Int32\n"
        "from tplib import Box\n"
        "def peek(b: Box[Int32]) -> Int32:\n"
        "    return b.get()\n"
        "def main() -> None:\n"
        "    b = Box(Int32(41))\n"
        "    print(peek(b) + 1)\n"
        "main()\n")

    def test_clone_pair_call_routes(self):
        # _lower_ctx: Box is cross-module, so _f1_record needs the
        # codegen-time native_cpp_names registration active.
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "peek") is not None

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_genuine_overload_call_still_rejected(self):
        # A real @overload stub set at the call site keeps rejecting: the
        # literal-overload call render MANGLES the callee name, and the
        # def side shares one impl body across stubs.
        src = (
            "from tpy import Int32\n"
            "from typing import overload\n"
            "class W:\n"
            "    n: Int32\n"
            "    def __init__(self):\n        self.n = 0\n"
            "    @overload\n"
            "    def m(self, x: Int32) -> Int32: ...\n"
            "    @overload\n"
            "    def m(self, x: bool) -> Int32: ...\n"
            "    def m(self, x: Int32 | bool) -> Int32:\n        return self.n\n"
            "def f(w: W) -> Int32:\n    return w.m(1)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_clone_def_carveout_reaches_body_gates(self):
        # The clone DEFS pass the overload gate (the pair carve-out) and
        # reject on body content instead -- pinned via the fallback tag:
        # no sig.overload_set fold for a pure clone pair.
        compiler, modules = _compile(self.SRC)
        from ..codegen_cpp.context import CodeGenOptions as _O
        compiler.generate_code_to_strings(
            _entry(modules), options=_O(emit_source_comments=False,
                                        thir_codegen=True))
        ov = {k: v for k, v in compiler._thir_fallback.items()
              if 'sig.overload_set' in k}
        assert not ov, ov


# rc.py's receiver shape: a @dynamic-protocol pointee behind Ptr, method
# calls dispatching through the abstract base's virtuals.
_PTR_DYN_PROTO_SRC = (
    "from typing import Protocol\n"
    "from tpy import Int32, Ptr, dynamic, take_ptr\n"
    "@dynamic\n"
    "class CellBase(Protocol):\n"
    "    def incr(self) -> None: ...\n"
    "    def release(self) -> bool: ...\n"
    "class Cell(CellBase):\n"
    "    n: Int32\n"
    "    def __init__(self):\n        self.n = 1\n"
    "    def incr(self):\n        self.n = self.n + 1\n"
    "    def release(self) -> bool:\n"
    "        self.n = self.n - 1\n        return self.n == 0\n"
    "class Holder:\n"
    "    cell: Ptr[CellBase]\n"
    "    def __init__(self, c: Ptr[CellBase]):\n        self.cell = c\n"
    "    def drop(self) -> bool:\n        return self.cell.release()\n"
    "def f(p: Ptr[CellBase]) -> bool:\n"
    "    p.incr()\n"
    "    return p.release()\n"
    "def copy_then_call(p: Ptr[CellBase]) -> bool:\n"
    "    q = p\n"
    "    return q.release()\n"
    "def none_decl(p: Ptr[CellBase]) -> bool:\n"
    "    q: Ptr[CellBase] = None\n"
    "    q = p\n"
    "    return q.release()\n"
    "def main():\n"
    "    c = Cell()\n"
    "    print(f(take_ptr(c)))\n"
    "    h = Holder(take_ptr(c))\n"
    "    print(h.drop())\n"
    "    c2 = Cell()\n"
    "    print(copy_then_call(take_ptr(c2)))\n"
    "main()\n"
)


class TestPtrDynProtoPointee:
    """`Ptr[T]` value slots widened to @dynamic-protocol pointees: the same
    THIRMethodCall deref_check / is_arrow arms as a record pointee, plus the
    one dyn-proto-specific decl fact -- the AST spells a protocol-containing
    decl type `auto` (`auto q = p;`), mirrored on THIRVarDecl.cpp_type. A
    first `q: Ptr[P] = None` decl is the AST's `auto q = nullptr;`
    (std::nullptr_t) miscompile -- gate-rejected, not mirrored."""

    def test_ptr_name_receiver_routes_both_arms(self):
        thir, witnessed = _lower_ctx_witnessed(_PTR_DYN_PROTO_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        first = fn.body[0].expr
        assert isinstance(first, THIRMethodCall)
        assert first.deref_check and not first.is_arrow
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRMethodCall)
        assert ret.value.is_arrow and not ret.value.deref_check
        assert witnessed.get("ptr.dyn_proto_pointee", 0) >= 1

    def test_ptr_field_receiver_routes(self):
        # `self.cell.release()` -- the rc.py `self._cell` receiver shape.
        fn = _fn(_lower_ctx(_PTR_DYN_PROTO_SRC), "drop")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRMethodCall) and ret.value.deref_check
        assert isinstance(ret.value.receiver, THIRFieldAccess)

    def test_decl_spells_auto(self):
        # The dyn-proto Ptr local decl mirrors _cpp_decl_type's protocol arm.
        fn = _fn(_lower_ctx(_PTR_DYN_PROTO_SRC), "copy_then_call")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and decl.cpp_type == "auto"

    def test_none_init_first_decl_stays_ast(self):
        # `q: Ptr[P] = None` -> the AST's `auto q = nullptr;` miscompile;
        # the whole body falls back.
        assert _fn(_lower_ctx(_PTR_DYN_PROTO_SRC), "none_decl") is None

    def test_byte_identical(self):
        compiler, modules = _compile(_PTR_DYN_PROTO_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::tpy::deref_check(p).incr()" in thir_out[1]
        assert "p->release()" in thir_out[1]
        # Methods emit inline in the header.
        assert "::tpy::deref_check(this->cell).release()" in thir_out[0]
        assert "auto q = p;" in thir_out[1]

    def test_structural_protocol_pointee_rejected(self):
        # A structural protocol has no runtime C++ type (to_cpp() is the
        # monomorphization placeholder), so it must stay outside the family;
        # the dynamic flag alone admits.
        compiler, modules = _compile("def t() -> None:\n    pass\n")
        analyzer = _entry(modules).analyzer
        structural = PtrType(NominalType("P", is_protocol=True))
        dyn = PtrType(NominalType("P", is_protocol=True,
                                  is_dynamic_protocol=True))
        assert not _eligible_ptr_value(structural, analyzer)
        assert _eligible_ptr_value(dyn, analyzer)


class TestAutoOwnCloneCarveout:
    # The auto_own[Self] half of the clone-pair carve-out: the pair's
    # borrowing member carries is_auto_own_borrowing_clone (its consuming
    # twin is already rejected as is_consuming), and a general method call
    # always resolves to the borrowing member (sema hardcodes
    # is_consuming_receiver=False), so the call render is member-blind.
    # Non-generic record: the generic auto_own_basic corpus case is
    # excluded by the generics frontier, leaving this branch unit-only.
    SRC = (
        "from typing import Self\n"
        "from tpy import Int32, auto_own\n"
        "class Holder:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "    def peek(self: auto_own[Self]) -> auto_own[Int32]:\n"
        "        return self.n\n"
        "def use(h: Holder) -> Int32:\n    return h.peek()\n"
        "def main():\n    print(use(Holder(7)))\nmain()\n")

    def test_borrowing_clone_def_passes_overload_gate(self):
        # The DEF-side carve-out: no sig.overload_set fold for the pair
        # (the borrowing clone body then gates on its own content).
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        ov = {k: v for k, v in compiler._thir_fallback.items()
              if 'sig.overload_set' in k}
        assert not ov, ov

    def test_call_site_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "use") is not None

    def test_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        _, cpp_t = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        compiler2, modules2 = _compile(self.SRC)
        _, cpp_a = compiler2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=False))
        assert cpp_t == cpp_a


# --- Method arg + return grid: the free-call ret/arg families mirrored onto
# user-record and builtin-container method calls (enum / bytes / Ptr returns;
# enum / bytes / value-tuple / own-record-rvalue args). ---

_GRID_SRC = (
    "from tpy import Int32, Own\n"
    "from enum import Enum\n"
    "class Color(Enum):\n    RED = 1\n    GREEN = 2\n"
    "class Widget:\n    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "    def pick(self) -> Color:\n        return Color.RED\n"
    "    def make_bytes(self) -> bytes:\n        return b\"hi\"\n"
    "    def take_enum(self, c: Color) -> Int32:\n        return self.x\n"
    "    def take_bytes(self, b: bytes) -> Int32:\n        return len(b)\n"
    "    def take_tuple(self, t: tuple[Int32, Int32]) -> Int32:\n        return t[0]\n"
    "    def swallow(self, o: Own[Widget]) -> Int32:\n        return o.x\n"
    "def drive(w: Widget) -> Int32:\n"
    "    c = w.pick()\n"
    "    bs = w.make_bytes()\n"
    "    n1 = w.take_enum(c)\n"
    "    n2 = w.take_enum(Color.GREEN)\n"
    "    n3 = w.take_bytes(b\"abc\")\n"
    "    n4 = w.take_tuple((1, 2))\n"
    "    n5 = w.swallow(Widget(9))\n"
    "    return n1 + n3 + n4 + n5 + len(bs)\n"
)


class TestMethodArgReturnGrid:
    def _emit(self, thir: bool):
        compiler, modules = _compile(_GRID_SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_drive_routes(self):
        # The enum/bytes/Ptr returns and enum/bytes/value-tuple/own-rvalue
        # method args all route now that the method grid mirrors the free-call
        # ret/arg cascade.
        assert _fn(_lower_ctx(_GRID_SRC), "drive") is not None

    def test_byte_identical(self):
        assert self._emit(True) == self._emit(False)


# A container-element-record subscript receiver (`ps[i].m()`) in stmt / var_decl
# / return positions: the subscript is a plain-record borrow lvalue
# (`::tpy::__getitem__(ps, i)`, `.` access), so the outer method routes the
# user-record arm identically.
_RECV_SUBSCRIPT_SRC = (
    "from tpy import Int32\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "    def bump(self) -> Int32:\n        return self.x + 1\n"
    "    def touch(self):\n        self.x = 9\n"
    "def run(ps: list[P], i: Int32) -> Int32:\n"
    "    ps[i].touch()\n"
    "    r = ps[i].bump()\n"
    "    return r + ps[i].bump()\n"
    "def main():\n"
    "    ps = [P(1), P(2)]\n"
    "    print(run(ps, 0))\n"
    "main()\n"
)


class TestSubscriptMethodReceiver:
    def _emit(self, thir: bool):
        compiler, modules = _compile(_RECV_SUBSCRIPT_SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_routes_and_witnesses(self):
        thir, witnessed = _lower_ctx_witnessed(_RECV_SUBSCRIPT_SRC)
        assert _fn(thir, "run") is not None
        # touch (stmt) + bump (var_decl) + bump (return) all off `ps[i]`.
        assert witnessed.get("method.recv.subscript", 0) >= 3

    def test_byte_identical(self):
        assert self._emit(True) == self._emit(False)


# A method-call receiver (`self.b.get().m()`): `Box.get()` yields a plain-record
# borrow (`this->b.get()`, `.` access), so the outer method routes identically.
_RECV_METHOD_SRC = (
    "from tpy import Int32, Own\n"
    "from tplib.box import Box\n"
    "class Node:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "    def bump(self) -> Int32:\n        return self.x + 1\n"
    "    def touch(self):\n        self.x = 9\n"
    "class Holder:\n"
    "    b: Box[Node]\n"
    "    def __init__(self, b: Own[Box[Node]]):\n        self.b = b\n"
    "    def go(self) -> Int32:\n"
    "        self.b.get().touch()\n"
    "        c = self.b.get().bump()\n"
    "        return c + self.b.get().bump()\n"
    "def main():\n"
    "    h = Holder(Box(Node(1)))\n"
    "    print(h.go())\n"
    "main()\n"
)


class TestMethodCallReceiver:
    def _emit(self, thir: bool):
        compiler, modules = _compile(_RECV_METHOD_SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_routes_and_witnesses(self):
        thir, witnessed = _lower_ctx_witnessed(_RECV_METHOD_SRC)
        assert _fn(thir, "go") is not None
        assert witnessed.get("method.recv.method", 0) >= 3

    def test_byte_identical(self):
        assert self._emit(True) == self._emit(False)


class TestSetMethodReceivers:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=thir))
        return hpp + cpp

    # set[scalar|owned-str] method receivers route through the container
    # branch (`_set_method_recv` -- deliberately not a widening of
    # `_container_scalar_read`, which feeds subscript/decl consumers where a
    # set is invalid). Renders ride THIRMethodCall's existing arms: native
    # helpers (`::tpy::set_remove(s, 10)`) and bare members (`s.clear()`).
    SRC = (
        "from tpy import Int32\n"
        "def ops(s: set[Int32]) -> Int32:\n"
        "    s.discard(20)\n"
        "    s.remove(10)\n"
        "    s.add(4)\n"
        "    v = s.pop()\n"
        "    s.clear()\n"
        "    return v\n"
    )

    def test_byte_identical_and_routes(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "ops") is not None
        out = self._cpp(self.SRC, thir=True)
        assert "::tpy::set_remove(s, 10);" in out
        assert "s.clear();" in out


class TestOwnedStrRvalueElementArg:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=thir))
        return hpp + cpp

    # An owned-str RVALUE (concat binop / owned-returning method call) into
    # the Own[str] container-element slot binds the `T&&` bare -- no temp,
    # no view convert. Owned LVALUES keep the AST's copy+move-temp cascade.
    SRC = (
        "def app3(xs: list[str], s: str):\n"
        "    xs.append(s + \" world\")\n"
        "def app4(xs: list[str], s: str):\n"
        "    xs.append(s.upper())\n"
    )

    def test_byte_identical_and_routes(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "app3") is not None
        assert _fn(thir, "app4") is not None
        out = self._cpp(self.SRC, thir=True)
        assert "xs.push_back((::tpy::str_concat(s, \" world\")));" in out

    def test_owned_str_local_arg_stays_ast(self):
        # An owned str LOCAL (STORAGE lvalue) still rides the AST's
        # copy+move-temp cascade.
        src = (
            "def app(xs: list[str], s: str):\n"
            "    owned = s.upper()\n"
            "    owned = owned.lower()\n"
            "    xs.append(owned)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "app") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestFreeCallResultReceiver:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=thir))
        return hpp + cpp

    # `make(3).get()`: a plain F1-record free-call result as the method
    # receiver keeps `.` access on both paths; the inner call lowers via the
    # shared free-call machinery at BORROW_BIND use.
    SRC = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "    def get(self) -> Int32:\n        return self.x\n"
        "def make(x: Int32) -> Own[A]:\n    return A(x)\n"
        "def use() -> Int32:\n    return make(3).get()\n"
    )

    def test_byte_identical_and_routes(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "use") is not None
        assert witnesses.get("method.recv.free_call", 0) >= 1
        assert "return make(3).get();" in self._cpp(self.SRC, thir=True)

    def test_optional_returning_call_receiver_stays_ast(self):
        # An Optional-record-returning callee reads through a deref/unwrap on
        # the AST path -- the bare `.` arm must not admit it.
        src = (
            "from tpy import Int32, Own\n"
            "class A:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    def get(self) -> Int32:\n        return self.x\n"
            "def maybe(x: Int32) -> Own[A] | None:\n"
            "    if x > 0:\n        return A(x)\n"
            "    return None\n"
            "def use() -> Int32:\n"
            "    m = maybe(3)\n"
            "    if m is not None:\n        return m.get()\n"
            "    return 0\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestValueOptScalarArg:
    """A scalar value / `None` into a value-repr Optional[scalar] method
    slot renders bare / `std::nullopt` (the implicit std::optional<T> ctor
    absorbs the value) -- `sock.settimeout(0.5)`."""

    _SRC = (
        "from tpy import Int32, Float64\n"
        "class S:\n"
        "    t: Float64\n"
        "    def __init__(self):\n        self.t = 0.0\n"
        "    def settimeout(self, value: Float64 | None) -> None:\n"
        "        if value is None:\n            self.t = -1.0\n"
        "        else:\n            self.t = value\n"
    )

    def test_literal_and_none_route(self):
        thir = _lower_ctx(
            self._SRC
            + "def use(s: S, v: Float64) -> None:\n"
            + "    s.settimeout(0.5)\n    s.settimeout(None)\n"
            + "    s.settimeout(v)\n")
        assert _fn(thir, "use") is not None

    def test_str_literal_into_opt_char_slot_rejected_by_sema(self):
        # The predicate's str-literal exclusion is defensive only: sema
        # rejects a str literal into a `Char | None` slot before lowering
        # runs. If sema ever admits the shape, this pin flips and the
        # THIR arg row needs a real char-retype decision.
        with pytest.raises(SemanticError):
            _lower_ctx(
                "from tpy import Char\n"
                "class S:\n"
                "    c: Char\n"
                "    def __init__(self):\n        self.c = \"x\"\n"
                "    def put(self, value: Char | None) -> None:\n"
                "        if value is not None:\n            self.c = value\n"
                "def use(s: S) -> None:\n"
                "    s.put(\"a\")\n")

    def test_emit_byte_identical(self):
        src = (
            self._SRC
            + "def use(s: S, v: Float64) -> None:\n"
            + "    s.settimeout(0.5)\n    s.settimeout(None)\n"
            + "    s.settimeout(v)\n"
            + "def main():\n    s = S()\n    use(s, 2.5)\n    print(s.t)\n"
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
        assert "s.settimeout(0.5);" in thir_cpp
        assert "s.settimeout(std::nullopt);" in thir_cpp
        assert "s.settimeout(v);" in thir_cpp


_ASYNCIO_DRIVER_SRC = (
    "import asyncio\n"
    "async def work() -> None:\n"
    "    print(\"w\")\n"
    "def main() -> None:\n"
    "    asyncio.run(work())\n"
    "main()\n"
)


class TestAsyncioRunDriverCall:
    """The `asyncio.run(coro())` driver: a module-qualified GENERIC call
    (generic_qualified kind, `::tpystd::asyncio::run<T>(...)`) whose
    Own[Cancellable[T]] arg is a DIRECT async-def factory call -- the
    make_adapter erasure boundary (`_dyn_own_coro_factory_arg`). Bound
    handles, method coros, and generic factories keep their AST renders."""

    def test_driver_routes_with_adapter_wrap(self):
        thir, witnessed = _lower_ctx_witnessed(_ASYNCIO_DRIVER_SRC)
        fn = _fn(thir, "main")
        assert fn is not None
        stmt = fn.body[0]
        assert isinstance(stmt, THIRExprStmt)
        call = stmt.expr
        assert isinstance(call, THIRCall)
        assert call.callee_cpp == "::tpystd::asyncio::run"
        assert call.template_args_cpp == ("std::monostate",)
        arg = call.args[0]
        assert isinstance(arg, THIRCoerce)
        assert arg.coercion_name == "dyn_own_adapter"
        assert arg.wrap == ("::tpy::make_adapter<::tpystd::coro::"
                            "Cancellable<std::monostate>>({0})")
        inner = arg.expr
        assert isinstance(inner, THIRCall) and inner.callee == "work"
        assert witnessed.get("call.coro_factory_adapter", 0) >= 1
        assert witnessed.get("call.generic_qualified", 0) >= 1

    def test_value_returning_driver_routes(self):
        thir, _ = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "import asyncio\n"
            "async def compute() -> Int32:\n"
            "    return 7\n"
            "def main() -> None:\n"
            "    n = asyncio.run(compute())\n"
            "    print(n)\n"
            "main()\n")
        fn = _fn(thir, "main")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.init, THIRCall)
        assert decl.init.template_args_cpp == ("int32_t",)

    def test_bound_handle_arg_stays_ast(self):
        # `c = work(); asyncio.run(c)` takes the `std::move(*(c))`
        # optional-slot unwrap -- a different render, not mirrored.
        thir = _lower_ctx(
            "import asyncio\n"
            "async def work() -> None:\n"
            "    print(\"w\")\n"
            "def main() -> None:\n"
            "    c = work()\n"
            "    asyncio.run(c)\n"
            "main()\n")
        assert _fn(thir, "main") is None

    def test_method_coro_arg_stays_ast(self):
        # A bound async-METHOD coroutine captures its receiver; the factory
        # is a TpyMethodCall -- outside the free-call factory row.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "import asyncio\n"
            "class W:\n"
            "    n: Int32\n"
            "    def __init__(self):\n"
            "        self.n = 1\n"
            "    async def go(self) -> None:\n"
            "        print(self.n)\n"
            "def main() -> None:\n"
            "    w = W()\n"
            "    asyncio.run(w.go())\n"
            "main()\n")
        assert _fn(thir, "main") is None

    def test_byte_identical(self):
        compiler, modules = _compile(_ASYNCIO_DRIVER_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert ("::tpystd::asyncio::run<std::monostate>(::tpy::make_adapter<"
                "::tpystd::coro::Cancellable<std::monostate>>(work()))"
                in thir_out[1])


class TestMethodRecvFamilyTable:
    # Both method gates (the shape ladder in expressions.py and the arg gate
    # in _method_call_arg_ok) dispatch through _method_recv_family; these
    # pins guard each table row's (shape-fn, arg-fn, stub_recv) pairing.

    def test_family_classification_pins_table_rows(self):
        compiler, modules = _compile(
            "from tpy import Int32\n"
            "from typing import Protocol\n"
            "class Sized(Protocol):\n"
            "    def size(self) -> Int32: ...\n"
            "class Rec:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 0\n"
            "def f(xs: list[Int32], ba: bytearray, s: str, bs: bytes,\n"
            "      p: Sized, r: Rec) -> None:\n"
            "    pass\n")
        entry = _entry(modules)
        analyzer = entry.analyzer
        types = dict(next(fd for fd in entry.ast.functions
                          if fd.name == "f").params)
        with activate_compiler(compiler):
            fam = {name: _method_recv_family(t, analyzer, None)
                   for name, t in types.items()}
        assert fam["xs"].shape_ok is _container_method_call_supported
        assert fam["xs"].arg_ok is _container_method_arg_ok
        assert fam["xs"].stub_recv
        assert fam["p"].shape_ok is _protocol_method_call_supported
        assert fam["p"].arg_ok is _protocol_method_arg_ok
        assert not fam["p"].stub_recv
        assert fam["ba"].shape_ok is _bytearray_method_call_supported
        assert fam["ba"].arg_ok is _view_method_arg_ok
        assert fam["ba"].stub_recv
        # str and bytes receivers share the one view row: bytes args take the
        # str twin's view rows, not the record fallback.
        assert fam["s"] is fam["bs"]
        assert fam["s"].shape_ok is _view_method_call_supported
        assert fam["s"].arg_ok is _view_method_arg_ok
        assert fam["s"].stub_recv
        # A user record is the residual (non-table) dispatch.
        assert fam["r"] is None

    def test_bytes_receiver_method_with_arg_routes(self):
        # A bytes receiver's method call WITH an arg takes the view arg rows
        # like its str twin (the record fallback used to reject it, falling
        # the whole body back to AST).
        source = (
            "def f(bs: bytes) -> None:\n"
            '    n = bs.find(b"ab")\n'
            "    print(n)\n")
        thir = _lower(source)
        f = _fn(thir, "f")
        assert f is not None
        call = f.body[0].init
        assert isinstance(call, THIRMethodCall)
        assert call.native_function_name == "tpy::bytes_find"
        _assert_byte_identical(source)


class TestRecordResultDiscard:
    """A discarded F1-record method result at statement position renders the
    same bare call (`s.get(url);`) -- the record sibling of the container
    family's stmt_storage_ok row."""

    _SRC = (
        "from tpy import Int32, Own\n"
        "class R:\n"
        "    v: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.v = 1\n"
        "class S:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def make(self) -> Own[R]:\n"
        "        self.n += 1\n"
        "        return R()\n"
    )

    def test_discarded_record_result_routes(self):
        src = (self._SRC
               + "def f(s: S) -> None:\n"
               + "    s.make()\n"
               + "    print(s.n)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("method.record_discard")
        _assert_byte_identical(src)

    def test_value_position_record_result_still_gated(self):
        # In VALUE position the record result keeps its sink-specific gates
        # (here: an admitted owned-record decl -- routes via that sink, not
        # the discard row).
        src = (self._SRC
               + "def f(s: S) -> None:\n"
               + "    r = s.make()\n"
               + "    print(r.v)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("method.record_discard")
        _assert_byte_identical(src)


class TestOptionalPtrContainerMethodArgs:
    """Pointer-repr Optional[container] method-arg faces off a record
    receiver: `None` -> `nullptr`, and a container LITERAL -> the typed
    `__tmp_N` + `&(__tmp_N)` hoist (`s.get(url, None, {...})`)."""

    _SRC = (
        "from tpy import Int32\n"
        "class S:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def get(self, url: str, params: dict[str, str] | None = None,\n"
        "            headers: dict[str, str] | None = None) -> Int32:\n"
        "        self.n += 1\n"
        "        return self.n\n"
    )

    def test_dict_literal_hoists_addr_temp(self):
        src = (self._SRC
               + "def f(s: S) -> None:\n"
               + '    print(s.get("u", None, {"k": "v"}))\n')
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("argtemp.optptr_container_literal")
        _assert_byte_identical(src)

    def test_none_renders_nullptr(self):
        src = (self._SRC
               + "def f(s: S) -> None:\n"
               + '    print(s.get("u", None))\n')
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestOptionalPtrContainerNameArg:
    """The bare-container-name face at a record-method's pointer-repr
    Optional[container] slot: `s.get("u", d)` -> `s.get("u", &(d))`."""

    def test_container_name_addr_of(self):
        src = ("from tpy import Int32\n"
               "class S:\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n"
               "    def get(self, url: str,\n"
               "            params: dict[str, str] | None = None) -> Int32:\n"
               "        self.n += 1\n"
               "        return self.n\n"
               "def f(s: S) -> None:\n"
               '    d = {"k": "v"}\n'
               '    print(s.get("u", d))\n')
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("optptr.name")
        _assert_byte_identical(src)
