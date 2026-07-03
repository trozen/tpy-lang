"""THIR method frontier M1/M2: instance methods, record params, readonly
free functions, scalar field writes + aug-assign."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRFieldAccess, THIRFormConvert, THIRName,
    THIRReturn, THIRSelf, THIRStrAppend, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _F1_RECORDS,
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

    def test_generic_record_method_excluded(self):
        # A generic record's `self` is templated -> outside the F1-record slice.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Wrap[T]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def get(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "get") is None

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

    def test_property_setter_target_excluded(self):
        # A field write that is really a @property setter takes a method-call
        # emit path, not a plain field assign -> stays on the AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class C:\n    _n: Int32\n"
            "    def __init__(self):\n        self._n = 0\n"
            "    @property\n    def n(self) -> Int32:\n        return self._n\n"
            "    @n.setter\n    def n(self, v: Int32):\n        self._n = v\n"
            "    def use(self):\n        self.n = 5\n")
        assert _fn(thir, "use") is None

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
        # Both write forms in one body exercise the `or`-dispatch in
        # `_stmt_eligible` -- neither blocks the other's eligibility.
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

    def test_subscript_aug_assign_excluded(self):
        # A subscript target takes the set_value/get_value path -> AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def at(xs: list[Int32], i: Int32):\n    xs[i] += 1\n")
        assert _fn(thir, "at") is None

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

    def test_readonly_staticmethod_excluded(self):
        # @readonly on a @staticmethod emits with the readonly verdicts dropped
        # (gen_method_def branches on `is_const and not is_static`) -- an
        # asymmetry the mirror does not reproduce, so it stays AST.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @staticmethod\n    @readonly\n"
            + "    def sm(a: Int32) -> Int32:\n        return a\n")
        assert _fn(thir, "sm") is None

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

    def test_pointer_repr_optional_getter_excluded(self):
        # A getter returning `Leaf | None` (pointer-repr) takes the
        # in_property_getter return arm (returns the field's storage by
        # reference) -- the return gate rejects the type before that arm can
        # diverge.
        thir = _lower_ctx(
            _SPD_METHODS
            + "    @property\n"
            + "    def sibling(self) -> Leaf | None:\n        return self.opt\n")
        assert _fn(thir, "sibling") is None


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

    def test_total_ordering_synthesized_dunders_stay_ast(self):
        # @total_ordering synthesizes `__le__`/`__gt__`/`__ge__` bodies from
        # record compares (`self < other`), whose operands need gen_expr_deref's
        # indirection -- pinned outside the compare slice.
        thir = _lower_ctx(self._SCORE)
        for name in ("__le__", "__gt__", "__ge__"):
            assert _fn(thir, name) is None, name

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
