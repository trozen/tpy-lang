"""THIR generic frontier: generic free functions AND method-level `[U]`
generics route their bodies via the TypeParamRef T-value arms F5 built for
generic-record methods -- the template signature stays AST, the body's `T`/`U`
param/return slots are form-neutral value pass-throughs (on a generic record
the record's T rides the F5 self-feed while the method's U rides these slots).
INT-kind type params stay on the AST path (separate cell)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import THIRName, THIRReturn
from .testutil import _compile, _entry, _fn, _lower_ctx


class TestGenericFreeFunction:
    def test_identity_routes(self):
        # `def gid[T](x: T) -> T: return x` -- the minimal T-value pass-through:
        # param and return are both TypeParamRef, the body a bare name return.
        thir = _lower_ctx("def gid[T](x: T) -> T:\n    return x\n")
        fn = _fn(thir, "gid")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRName)

    def test_multi_type_param_routes(self):
        # Multiple params sharing one `T`, and a two-param generic returning the
        # second -- still all form-neutral T slots.
        thir = _lower_ctx("def gpair[T](x: T, y: T) -> T:\n    return y\n")
        assert _fn(thir, "gpair") is not None

    def test_two_distinct_params_route(self):
        thir = _lower_ctx("def first[A, B](a: A, b: B) -> A:\n    return a\n")
        assert _fn(thir, "first") is not None

    def test_method_level_generic_routes(self):
        # A method's OWN `[U]` spells its slots as TypeParamRef exactly like a
        # free function's -- same arms, template signature stays AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Holder:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def echo[U](self, x: U) -> U:\n        return x\n")
        fn = _fn(thir, "echo")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRName)

    def test_method_generic_on_generic_record_routes(self):
        # `[U]` on a generic record's method: the record's T rides the F5
        # self-feed, the method's U rides the free-fn arms -- both compose.
        thir = _lower_ctx(
            "from tpy import Own\n"
            "class Pair[T]:\n    first: T\n"
            "    def __init__(self, first: Own[T]):\n        self.first = first\n"
            "    def echo[U](self, x: U) -> U:\n        return x\n")
        assert _fn(thir, "echo") is not None

    def test_own_u_method_param_routes(self):
        # `Own[U]` method param + return -- the _own_type_param_slot arms.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class Holder:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def take[U](self, v: Own[U]) -> Own[U]:\n        return v\n")
        assert _fn(thir, "take") is not None

    def test_int_kind_type_param_stays_ast(self):
        # `[N: int]` (INT-kind) is out of the slice: N read as a value has no
        # T-slot arm yet.
        thir = _lower_ctx(
            "from tpy import Int32, Array\n"
            "def head[N: int](a: Array[Int32, N]) -> Int32:\n"
            "    return a[0]\n")
        assert _fn(thir, "head") is None



class TestGenericFreeFunctionEmit:
    def _emit(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "def gid[T](x: T) -> T:\n    return x\n"
        # a body with a `T` local decl -- falls back to AST, must still emit the
        # byte-identical `T& y = x;` alias (the local-T-decl residual sub-cell).
        "def glocal[T](x: T) -> T:\n    y = x\n    return y\n"
        "def main():\n    print(gid(5))\n    print(glocal(7))\n"
        "main()\n"
    )

    METHOD_SRC = (
        "from tpy import Int32, Own\n"
        "class Holder:\n    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "    def echo[U](self, x: U) -> U:\n        return x\n"
        "    def take[U](self, v: Own[U]) -> Own[U]:\n        return v\n"
        "def main():\n    h = Holder(1)\n    print(h.echo(5))\n    print(h.take(7))\n"
        "main()\n"
    )

    def test_generic_free_fn_byte_identical(self):
        # The load-bearing contract: the routed body and the fallen-back body
        # both emit identically from THIR and the AST path.
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_method_generic_byte_identical(self):
        assert (self._emit(self.METHOD_SRC, thir=True)
                == self._emit(self.METHOD_SRC, thir=False))

    def test_method_generic_renders_template_body(self):
        # The method template SIGNATURE stays AST-owned (const-inferred:
        # `val_or_cref_t<U>` / `const U&`); THIR renders only the body.
        out = self._emit(self.METHOD_SRC, thir=True)
        assert "::tpy::val_or_cref_t<U> echo(const U& x) const" in out
        assert "return x;" in out

    def test_own_u_method_passthrough_renders_bare(self):
        # `Own[U]` method param returned directly: bare (no std::move -- the
        # move only arises at an intermediate local decl).
        out = self._emit(self.METHOD_SRC, thir=True)
        assert "U take(::tpy::own_param_t<U> v) const" in out
        assert "return v;" in out

    def test_identity_body_renders_bare_param_return(self):
        assert "::tpy::val_or_ref_t<T> gid(::tpy::param_val_or_ref_t<T> x) {\n    return x;\n}" \
            in self._emit(self.SRC, thir=True)

    def test_local_t_decl_renders_ref_alias(self):
        assert "T& y = x;" in self._emit(self.SRC, thir=True)


class TestOwnTypeParam:
    def test_own_t_passthrough_routes(self):
        # `Own[T]` param + return: `own_param_t<T>` / `own_return_t<T>`. A DIRECT
        # `return x` of an Own param passes bare (no std::move -- the move only
        # arises at an intermediate local decl).
        thir = _lower_ctx(
            "from tpy import Own\n"
            "def take[T](x: Own[T]) -> Own[T]:\n    return x\n")
        assert _fn(thir, "take") is not None

    def test_own_t_method_param_field_write_routes(self):
        # An `Own[T]` METHOD param written into a `T` field:
        # `self.item = std::move(item)`. This was F5's filed residual -- it lowers
        # to a STORAGE->STORAGE `THIRFormConvert` carrying `move=True` (the move
        # IS the operation), which the validator must accept as non-dead.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class Holder[T]:\n    item: T\n"
            "    def __init__(self, item: Own[T]):\n        self.item = item\n"
            "    def replace(self, item: Own[T], flag: Int32) -> Int32:\n"
            "        self.item = item\n        return flag\n")
        assert _fn(thir, "replace") is not None

    def test_value_bound_own_t_method_field_write_routes(self):
        # A value-bound `T` (`T: ValueType`): the `Own[T]` method param is passed
        # by value and the field write is a bare COPY (no move -- mirrors the AST
        # method body, unlike the ctor MIL which moves). The move-free field write
        # must NOT build a no-op STORAGE->STORAGE convert (the validator rejects
        # one); it emits the bare source.
        thir = _lower_ctx(
            "from tpy import Int32, Own, ValueType\n"
            "class Holder[T: ValueType]:\n    item: T\n"
            "    def __init__(self, item: Own[T]):\n        self.item = item\n"
            "    def replace(self, item: Own[T], flag: Int32) -> Int32:\n"
            "        self.item = item\n        return flag\n")
        assert _fn(thir, "replace") is not None


class TestOwnTypeParamEmit:
    def _emit(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "from tpy import Int32, Own\n"
        "class Holder[T]:\n    item: T\n"
        "    def __init__(self, item: Own[T]):\n        self.item = item\n"
        "    def replace(self, item: Own[T], flag: Int32) -> Int32:\n"
        "        self.item = item\n        return flag\n"
        "def take[T](x: Own[T]) -> Own[T]:\n    return x\n"
        "def main():\n    print(take(3))\n"
        "main()\n"
    )

    def test_own_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_direct_own_return_renders_bare(self):
        assert "::tpy::own_return_t<T> take(::tpy::own_param_t<T> x) {\n    return x;\n}" \
            in self._emit(self.SRC, thir=True)

    def test_own_param_field_write_renders_move(self):
        assert "this->item = std::move(item);" in self._emit(self.SRC, thir=True)

    VALUE_BOUND_SRC = (
        "from tpy import Int32, Own, ValueType\n"
        "class Holder[T: ValueType]:\n    item: T\n"
        "    def __init__(self, item: Own[T]):\n        self.item = item\n"
        "    def replace(self, item: Own[T], flag: Int32) -> Int32:\n"
        "        self.item = item\n        return flag\n"
        "def main():\n    h = Holder(Int32(1))\n    print(h.replace(Int32(2), 3))\n"
        "main()\n"
    )

    def test_value_bound_byte_identical(self):
        assert self._emit(self.VALUE_BOUND_SRC, thir=True) \
            == self._emit(self.VALUE_BOUND_SRC, thir=False)

    def test_value_bound_field_write_renders_bare_copy(self):
        # Value-bound `Own[T]` is copied at a field write (no std::move) -- the
        # move-free path that must NOT build a no-op convert.
        emitted = self._emit(self.VALUE_BOUND_SRC, thir=True)
        assert "this->item = item;" in emitted
        assert "this->item = std::move(item);" not in emitted


class TestFormConvertMoveValidation:
    """A same-form same-type THIRFormConvert is a no-op ONLY when it carries no
    move: a `move=True` convert materializes `std::move(x)` (an Own[T] param
    written into a `T` field)."""

    def _fn_with_convert(self, move: bool):
        from ..typesys import TypeParamRef
        from .nodes import (
            Form, THIRAssign, THIRFieldAccess, THIRFormConvert, THIRFunction,
            THIRFunctionLayout, THIRName, THIRSelf,
        )
        t = TypeParamRef("T")
        src = THIRName(result_type=t, name="item", form=Form.STORAGE)
        conv = THIRFormConvert(result_type=t, value=src, form=Form.STORAGE, move=move)
        tgt = THIRFieldAccess(result_type=t, receiver=THIRSelf(result_type=t),
                              field_cpp="item", is_arrow=True, form=Form.STORAGE)
        from ..typesys import VoidType
        return THIRFunction(
            name="w", params=(), return_type=VoidType(),
            body=(THIRAssign(target=tgt, value=conv),), layout=THIRFunctionLayout())

    def test_move_convert_passes(self):
        from .validate import validate_function
        validate_function(self._fn_with_convert(move=True))

    def test_moveless_convert_raises(self):
        import pytest
        from .validate import THIRValidationError, validate_function
        with pytest.raises(THIRValidationError, match="no-op form convert"):
            validate_function(self._fn_with_convert(move=False))


class TestTypeArgSpellingFoundation:
    """Wave-7 foundation: `expand_fi_template` mirrors gen_call_from_fi's
    {T}/{cpp} substitution; `THIRCall.template_args_cpp` renders the explicit
    template-arg list over the plain/imported spellings only."""

    def test_expand_fi_template_named_placeholder(self):
        from ..typesys import INT32, FunctionInfo, TypeParamRef
        from .lower.generics import expand_fi_template
        fi = FunctionInfo(name="r", params=[], return_type=TypeParamRef("T"),
                          cpp_template="::tpy::Range<{T}>({0}, {1}, {2})",
                          type_params=["T"])
        assert (expand_fi_template(fi, (INT32,))
                == "::tpy::Range<int32_t>({0}, {1}, {2})")

    def test_expand_fi_template_cpp_placeholder(self):
        # {cpp} spells the SUBSTITUTED return type via to_cpp().
        from ..typesys import INT32, FunctionInfo, TypeParamRef
        from .lower.generics import expand_fi_template
        fi = FunctionInfo(name="mk", params=[], return_type=TypeParamRef("T"),
                          cpp_template="make<{cpp}>({0})", type_params=["T"])
        assert expand_fi_template(fi, (INT32,)) == "make<int32_t>({0})"

    def test_expand_fi_template_no_targs_passthrough(self):
        from ..typesys import INT32, FunctionInfo
        from .lower.generics import expand_fi_template
        fi = FunctionInfo(name="f", params=[], return_type=INT32,
                          cpp_template="g({0})")
        assert expand_fi_template(fi, None) == "g({0})"

    def test_emit_explicit_template_args(self):
        from ..typesys import INT32
        from .emit import CommentSink, _EmitState, _emit_call
        from .nodes import THIRCall
        st = _EmitState(comments=CommentSink())
        call = THIRCall(result_type=INT32, callee="pick", args=(),
                        template_args_cpp=("int32_t", "double"))
        assert _emit_call(call, st) == "pick<int32_t, double>()"
        qual = THIRCall(result_type=INT32, callee="pick", args=(),
                        callee_cpp="::tpyapp::m::pick",
                        template_args_cpp=("int32_t",))
        assert _emit_call(qual, st) == "::tpyapp::m::pick<int32_t>()"

    def test_template_args_with_native_rejected(self):
        import pytest
        from ..typesys import VoidType
        from .nodes import (THIRCall, THIRExprStmt, THIRFunction,
                            THIRFunctionLayout)
        from .validate import THIRValidationError, validate_function
        bad = THIRCall(result_type=VoidType(), callee="f", args=(),
                       native_name="tpy::f", template_args_cpp=("int32_t",))
        fn = THIRFunction(name="f", params=(), return_type=VoidType(),
                          body=(THIRExprStmt(expr=bad),),
                          layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError, match="template_args_cpp"):
            validate_function(fn)


class TestInstantiationTemplateCall:
    """G2a: generic-type instantiation calls (`list(it)` / `set(xs)`) at the
    storage decl sinks -- the resolved ctor's sema-substituted @cpp_template
    (`::tpy::construct<std::vector<int32_t>>({0})`) expanded over
    range-object / bare non-last-use container-name args."""

    _SRC = ("def main():\n"
            "    xs = list(range(3))\n"
            "    ys = list(xs)\n"
            "    zs = set(xs)\n"
            "    print(len(xs), len(ys), len(zs))\n")

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_instantiation_calls_route(self):
        from .testutil import _lower_ctx_witnessed
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("call.instantiation_template", 0) >= 3

    def test_instantiation_byte_identical(self):
        src = self._SRC + "main()\n"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_last_use_arg_stays_ast(self):
        # `list(xs)` at xs's LAST use may take the consuming-__iter__ / move
        # renders -> the face rejects, the whole body stays AST.
        src = ("def main():\n"
               "    xs = list(range(3))\n"
               "    ys = list(xs)\n"
               "    print(len(ys))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None
        full = src + "main()\n"
        assert self._cpp(full, thir=True) == self._cpp(full, thir=False)

    def test_method_call_arg_stays_ast(self):
        # A `d.keys()` arg is outside the admitted arg shapes (range call /
        # bare container name) -> AST path.
        src = ("def main():\n"
               "    d = {1: 2}\n"
               "    ks = list(d.keys())\n"
               "    print(len(ks), len(d))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None

    def test_ptr_null_ctor_stays_ast(self):
        # `Ptr[Int32]()` carries a PtrType call_type (the typed-nullptr
        # render) -- excluded from the face.
        src = ("from tpy import Int32, Ptr\n"
               "def main():\n"
               "    p = Ptr[Int32]()\n"
               "    print(p is None)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None


class TestCtorInstantiation:
    """G2b/G5a: the record-ctor INSTANTIATION form (`Cell[Int32]()` /
    `Poll[T]()` / inferred `Pair(1, 2)`) routes with type_cpp =
    lc.render_type(call_type); a zero-arg NATIVE-record instantiation
    (`UninitStorage[T]()`) rides the same face as a ctor-MIL field source."""

    _CELL = ("from tpy import Int32, Own\n"
             "class Cell[T]:\n"
             "    x: Int32\n"
             "    def __init__(self) -> None:\n"
             "        self.x = 1\n")

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_return_instantiation_routes(self):
        from .testutil import _lower_ctx_witnessed
        src = (self._CELL
               + "def pend() -> Own[Cell[Int32]]:\n"
               + "    return Cell[Int32]()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pend") is not None
        assert faces.get("ctor.instantiation", 0) >= 1

    def test_decl_instantiation_byte_identical(self):
        src = (self._CELL
               + "def ready() -> Own[Cell[Int32]]:\n"
               + "    p = Cell[Int32]()\n"
               + "    return p\n"
               + "def main():\n"
               + "    r = ready()\n"
               + "    print(r.x)\n"
               + "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_generic_mil_field_instantiation_routes(self):
        # `self.slot = UninitStorage[T]()` hoists to the MIL as
        # `slot(::tpy::UninitStorage<T>())` -- the G5a marquee shape.
        src = ("from tpy.mem import UninitStorage\n"
               "from tpy import Int32\n"
               "class Holder[T]:\n"
               "    slot: UninitStorage[T]\n"
               "    n: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.slot = UninitStorage[T]()\n"
               "        self.n = 0\n"
               "def main():\n"
               "    h = Holder[Int32]()\n"
               "    print(h.n)\n"
               "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower import iter_module_constructors, lower_constructor
        compiler, modules = _compile(src)
        entry = _entry(modules)
        with activate_compiler(compiler):
            for rec, init, st in iter_module_constructors(
                    entry.ast, entry.analyzer):
                if rec.name == "Holder":
                    ctor = lower_constructor(rec, init, entry.analyzer,
                                             self_type=st)
                    assert ctor is not None

    def test_argful_native_instantiation_stays_ast(self):
        # An arg-ful native-record instantiation may resolve @native /
        # @cpp_template __init__ overloads with their own emit arms -> AST.
        src = ("from tpy import Int32, Span\n"
               "def main():\n"
               "    a = [1, 2, 3]\n"
               "    s = Span[Int32](a)\n"
               "    print(len(s), len(a))\n"
               "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestGenericNativeCallee:
    """G1a: a generic @native import / @cpp_template free callee with
    inferred type args routes targ-blind -- the AST skips explicit template
    args for natives (C++ deduction) and substitutes {T} into templates
    (expand_fi_template); the plain `f<T>(args)` explicit spelling stays
    AST."""

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    _RC = ("from tplib.rc import Rc\n"
           "from tpy import Int32\n"
           "def main():\n"
           "    r = Rc.new(41)\n"
           "    print(r.get())\n"
           "main()\n")

    def test_native_generic_callee_byte_identical(self):
        # Rc/Weak.__del__ carry `unsafe_release(self._cell)` (a generic
        # @native callee, `::tpy::heap_release(...)`); Rc.new carries
        # `unsafe_take(_RcCell[U]())` (instantiation rvalue into Own[T]).
        assert self._cpp(self._RC, thir=True) == self._cpp(self._RC, thir=False)

    def test_rc_del_routes(self):
        from .testutil import _compile
        from ..compilation_context import activate_compiler
        from .lower import (iter_module_callables, lower_function,
                            module_native_globals)
        compiler, modules = _compile(self._RC)
        routed = {}
        with activate_compiler(compiler):
            for m in modules:
                ng = module_native_globals(m.ast)
                for f, st in iter_module_callables(m.ast, m.analyzer):
                    if f.name in ("__del__", "new") and not f.is_stub:
                        tf = lower_function(f, m.analyzer, self_type=st,
                                            native_globals=ng)
                        routed.setdefault(f.name, tf is not None)
        assert routed.get("__del__") is True

    def test_nested_generic_call_stays_ast(self):
        # A generic call in a NESTED (non-flush) position -- a print arg --
        # cannot hoist its TypeParamRef ref-slot literal temps, so the body
        # stays AST; the direct decl-init shape routes (see
        # TestGenericPlainCallee).
        src = ("def pick[T](a: T, b: T) -> T:\n"
               "    return b\n"
               "def use():\n"
               "    print(pick(1, 2))\n"
               "use()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestTypeParamCompare:
    """G4: a T-typed operand pair under a Comparable/Equatable bound
    (`self.get() < other.get()`) -- rb is None, the derived bare-operator
    emit `(l OP r)`; the T-returning method-call operands are admitted by
    the method arm's TypeParamRef result widening."""

    _SRC = ("from tpy import Comparable, Own, readonly\n"
            "class Box2[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: Own[T]) -> None:\n"
            "        self.v = v\n"
            "    @readonly\n"
            "    def get(self) -> T:\n"
            "        return self.v\n"
            "    def __lt__[T: Comparable](self, other: 'Box2[T]') -> bool:\n"
            "        return self.get() < other.get()\n"
            "def main():\n"
            "    a = Box2(1)\n"
            "    b = Box2(2)\n"
            "    print(a < b)\n"
            "main()\n")

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_tparam_compare_routes_byte_identical(self):
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "__lt__") is not None
        assert self._cpp(self._SRC, thir=True) == self._cpp(self._SRC, thir=False)


class TestGenericPlainCallee:
    """G1b: a plain TPy generic free callee spells explicit template args
    (`pick<int32_t>(...)`, type_to_cpp_stored per inferred arg); a literal
    arg into a TypeParamRef ref slot hoists the resolved-typed `__tmp_N`
    at flush positions, a bare scalar name binds the ref slot bare."""

    _PICK = ("from tpy import Int32\n"
             "def pick[T](a: T, b: T) -> T:\n"
             "    return b\n")

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_literal_args_route_with_temps(self):
        from .testutil import _lower_ctx_witnessed
        src = (self._PICK
               + "def use():\n"
               + "    p = pick(1, 2)\n"
               + "    print(p)\n"
               + "use()\n")
        thir, faces = _lower_ctx_witnessed(src)
        fn = _fn(thir, "use")
        assert fn is not None
        assert faces.get("call.generic_free", 0) >= 1
        assert faces.get("argtemp.generic_ref_slot", 0) >= 2
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_name_args_route_bare(self):
        src = (self._PICK
               + "def use(x: Int32, y: Int32):\n"
               + "    print(pick(x, y))\n"
               + "use(3, 4)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_template_args_spelled_on_call(self):
        src = (self._PICK
               + "def use(x: Int32, y: Int32):\n"
               + "    print(pick(x, y))\n"
               + "use(3, 4)\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "use")
        call = fn.body[0].args[0].expr
        assert call.template_args_cpp == ("int32_t",)


class TestGenericStaticCalls:
    """G3: generic STATIC calls -- same-module `Cls.m(args)` composes
    `Cls<CA>::template m<MA>(args)` (class/method targs split, dependent
    `template ` keyword); the module-qualified form qualifies the class
    through the module namespace."""

    def _cpp(self, src: str, thir: bool, libdir=None) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src, [libdir] if libdir else None)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_same_module_static_generic_routes(self):
        from .testutil import _lower_ctx_witnessed
        src = ("from tpy import Int32\n"
               "class Util:\n"
               "    @staticmethod\n"
               "    def smax[T](a: T, b: T) -> T:\n"
               "        return b\n"
               "def use(x: Int32, y: Int32):\n"
               "    print(Util.smax(x, y))\n"
               "use(5, 6)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("call.generic_static", 0) >= 1
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_module_static_generic_routes(self, tmp_path):
        (tmp_path / "m2.py").write_text(
            "class Util:\n"
            "    @staticmethod\n"
            "    def smax[T](a: T, b: T) -> T:\n"
            "        return b\n")
        src = ("import m2\n"
               "from tpy import Int32\n"
               "def use(x: Int32, y: Int32):\n"
               "    print(m2.Util.smax(x, y))\n"
               "use(5, 6)\n")
        thir = None
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower import lower_module
        compiler, modules = _compile(src, [tmp_path])
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "use") is not None
        assert (self._cpp(src, thir=True, libdir=tmp_path)
                == self._cpp(src, thir=False, libdir=tmp_path))


class TestOwnMoveArg:
    """G5b: a movable OWN-param name at its LAST USE moves temp-free
    (`std::move(name)`) into an Own slot in ANY position -- the
    `Box._ptr = heap_take(std::move(value))` ctor-MIL shape. VALUE payloads
    never move (codegen registers movables only at non-value decl arms)."""

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_box_ctor_mil_native_move_routes(self):
        # Box.__init__'s `self._ptr = heap_take(value)` hoists to the MIL as
        # `_ptr(::tpy::heap_take(std::move(value)))` -- the generic native
        # callee + own-param move + Ptr[T] field composition.
        from pathlib import Path
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower import iter_module_constructors, lower_constructor
        src = ("from tplib.box import Box\n"
               "def main():\n"
               "    b = Box(41)\n"
               "    print(b.get())\n"
               "main()\n")
        compiler, modules = _compile(src)
        routed = None
        with activate_compiler(compiler):
            for m in modules:
                for rec, init, st in iter_module_constructors(
                        m.ast, m.analyzer):
                    if rec.name == "Box":
                        routed = lower_constructor(
                            rec, init, m.analyzer, self_type=st) is not None
        assert routed is True
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_value_payload_last_use_does_not_move(self):
        # Regression pin (caught by the corpus byte-diff): a sema-movable
        # VALUE local at its last use renders BARE into an Own[scalar] slot
        # (`xs.append(n)`, the AST's inline-template bare pass) -- the raw
        # sema movable set over-moves without the value filter.
        src = ("from tpy import UInt64\n"
               "def f() -> UInt64:\n"
               "    xs: list[UInt64] = []\n"
               "    n = 19\n"
               "    xs.append(n)\n"
               "    return xs[0]\n"
               "def main():\n"
               "    print(f())\n"
               "main()\n")
        out = self._cpp(src, thir=True)
        assert "push_back(n)" in out and "std::move(n)" not in out
        assert out == self._cpp(src, thir=False)

    def test_char_type_arg_instantiation_routes(self):
        # `UninitArrayStorage[Char, N]()` -- a Char + INT-param type-arg pair
        # (the FixStr MIL shape); Char spells `char` on both paths.
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower import iter_module_constructors, lower_constructor
        src = ("from tplib.fix_str import FixStr\n"
               "def main():\n"
               "    s = FixStr[8]()\n"
               "    print(len(s))\n"
               "main()\n")
        compiler, modules = _compile(src)
        routed = None
        with activate_compiler(compiler):
            for m in modules:
                for rec, init, st in iter_module_constructors(
                        m.ast, m.analyzer):
                    if rec.name == "FixStr":
                        routed = lower_constructor(
                            rec, init, m.analyzer, self_type=st) is not None
        assert routed is True
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestDependentStaticTargs:
    """The dependent `template ` keyword: a generic static called from a
    generic body with class args still carrying a TypeParamRef emits
    `Pack<T>::template pair<int32_t>(...)` -- and ONLY when method targs
    follow (the AST nests the keyword under `if method_args:`)."""

    _SRC = ("from tpy import Int32, Own\n"
            "class Pack[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: Own[T]) -> None:\n"
            "        self.v = v\n"
            "    @staticmethod\n"
            "    def pair[U](v: Own[T], u: U) -> U:\n"
            "        return u\n"
            "def mk[T](a: Own[T]) -> Int32:\n"
            "    return Pack.pair(a, 7)\n"
            "def main():\n"
            "    print(mk(5))\n"
            "main()\n")

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_dependent_template_keyword_byte_identical(self):
        out = self._cpp(self._SRC, thir=True)
        assert out == self._cpp(self._SRC, thir=False)

    def test_dependent_template_keyword_spelled(self):
        # Pin the composed spelling when the shape routes; if the enclosing
        # body falls back, the byte-identity above still owns correctness.
        thir = _lower_ctx(self._SRC)
        fn = _fn(thir, "mk")
        if fn is not None:
            call = fn.body[0].value
            assert "::template pair" in call.callee_cpp
            assert call.template_args_cpp == ("int32_t",)

    def test_explicit_subscript_targs_byte_identical(self):
        # `pick[Int32](1, 2)` -- the explicit-subscript spelling; pinned
        # byte-identical whichever path carries it (explicit type_args
        # without inferred stay AST via the kind classifier).
        src = ("from tpy import Int32\n"
               "def pick[T](a: T, b: T) -> T:\n"
               "    return b\n"
               "def use():\n"
               "    p = pick[Int32](1, 2)\n"
               "    print(p)\n"
               "use()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestArgfulGenericInstantiation:
    """A generic-record ctor instantiation with a PRVALUE arg
    (`Box(5)` / `Box(a + b)` / `Box(h())` / `Box(Inner(3))`) binds the
    substituted `Own[Int32]` / `Own[Inner]` slot directly (no temp), so the
    `THIRCtorCall(type_cpp=Box<...>)` renders bare like the AST's
    `type_to_cpp(call_type)(args)`. The decl-init and the ctor-MIL
    field-source positions share the `_is_record_rvalue_source` arg row. A
    bare lvalue-NAME / str-view arg stays on the copy+move / owned-wrap AST
    path."""

    def _cpp(self, src: str, thir: bool) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    _HEAD = ("from tplib.box import Box\n"
             "from tpy import Int32, Own\n")

    def _decl(self, arg_src: str, sig: str = "") -> str:
        return (self._HEAD
                + f"def mk({sig}) -> Own[Box[Int32]]:\n"
                + f"    b = Box({arg_src})\n"
                + "    return b\n")

    def test_scalar_literal_routes(self):
        from .testutil import _lower_ctx_witnessed
        thir, faces = _lower_ctx_witnessed(self._decl("5"))
        assert _fn(thir, "mk") is not None
        assert faces.get("own.scalar_rvalue", 0) >= 1
        assert faces.get("ctor.instantiation", 0) >= 1

    def test_scalar_arith_routes(self):
        src = self._decl("a + c", sig="a: Int32, c: Int32")
        thir = _lower_ctx(src)
        assert _fn(thir, "mk") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_scalar_call_routes(self):
        src = (self._HEAD
               + "def h() -> Int32:\n    return 7\n"
               + "def mk() -> Own[Box[Int32]]:\n"
               + "    b = Box(h())\n"
               + "    return b\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "mk") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_record_ctor_arg_routes(self):
        from .testutil import _lower_ctx_witnessed
        src = ("from tplib.box import Box\n"
               "from tpy import Int32, Own\n"
               "class Inner:\n    x: Int32\n"
               "    def __init__(self, x: Int32):\n        self.x = x\n"
               "def mk() -> Own[Box[Inner]]:\n"
               "    b = Box(Inner(3))\n"
               "    return b\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "mk") is not None
        assert faces.get("own.record_rvalue", 0) >= 1
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_scalar_literal_byte_identical(self):
        # The whole program: routed decl + the surrounding get()/print.
        src = (self._HEAD
               + "def mk() -> Own[Box[Int32]]:\n"
               + "    b = Box(5)\n"
               + "    return b\n"
               + "def main():\n"
               + "    print(mk().get())\n"
               + "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_mil_field_source_instantiation_routes(self):
        # `self.b = Box(n + 1)` / `self.r = Box(Inner(3))` -- the same arg
        # row at the ctor-MIL field-source position, hoisted into the
        # initializer list.
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower import iter_module_constructors, lower_constructor
        src = ("from tplib.box import Box\n"
               "from tpy import Int32\n"
               "class Inner:\n    x: Int32\n"
               "    def __init__(self, x: Int32):\n        self.x = x\n"
               "class Holder:\n"
               "    b: Box[Int32]\n"
               "    r: Box[Inner]\n"
               "    def __init__(self, n: Int32):\n"
               "        self.b = Box(n + 1)\n"
               "        self.r = Box(Inner(3))\n"
               "def main():\n"
               "    h = Holder(4)\n"
               "    print(h.b.get())\n"
               "    print(h.r.get().x)\n"
               "main()\n")
        compiler, modules = _compile(src)
        routed = None
        with activate_compiler(compiler):
            for m in modules:
                for rec, init, st in iter_module_constructors(
                        m.ast, m.analyzer):
                    if rec.name == "Holder":
                        routed = lower_constructor(
                            rec, init, m.analyzer, self_type=st) is not None
        assert routed is True
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_name_arg_stays_ast(self):
        # A bare lvalue name copies into a temp then moves
        # (`auto __tmp = n; Box<int32_t>(std::move(__tmp))`) -- not the bare
        # render, so it stays on the AST path.
        src = self._decl("n", sig="n: Int32")
        assert _fn(_lower_ctx(src), "mk") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_strview_arg_stays_ast(self):
        # A str-view arg into an `Own[str]` slot materializes an owned copy
        # the bare emit does not reproduce -> AST.
        src = ("from tplib.box import Box\n"
               "def mk(s: str) -> None:\n"
               "    b = Box(s)\n"
               "    print(b.get())\n"
               "mk('hi')\n")
        assert _fn(_lower_ctx(src), "mk") is None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
