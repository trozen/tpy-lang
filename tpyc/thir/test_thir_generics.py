"""THIR generic frontier: generic free functions AND method-level `[U]`
generics route their bodies via the TypeParamRef T-value arms F5 built for
generic-record methods -- the template signature stays AST, the body's `T`/`U`
param/return slots are form-neutral value pass-throughs (on a generic record
the record's T rides the F5 self-feed while the method's U rides these slots).
INT-kind type params (`[N: int]`) seed as INT TypeParamRef bindings so their
bare-name reads route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import THIRName, THIRReturn
from .testutil import (
    _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed,
    _assert_byte_identical,
)


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

    def test_int_kind_type_param_routes(self):
        # `[N: int]` (INT-kind) is a template VALUE param: N is seeded as an
        # INT TypeParamRef binding, so bodies over it route.
        thir = _lower_ctx(
            "from tpy import Int32, Array\n"
            "def head[N: int](a: Array[Int32, N]) -> Int32:\n"
            "    return a[0]\n")
        assert _fn(thir, "head") is not None



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
    """`expand_fi_template` mirrors gen_call_from_fi's
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

    def test_last_use_arg_wraps_consuming_iter(self):
        # `list(xs)` at xs's LAST use takes the consuming-__iter__ wrap
        # (`::tpy::own_iter(std::move(xs))`) -- the instantiation arm's
        # last-use branch.
        src = ("def main():\n"
               "    xs = list(range(3))\n"
               "    ys = list(xs)\n"
               "    print(len(ys))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        full = src + "main()\n"
        out = self._cpp(full, thir=True)
        assert out == self._cpp(full, thir=False)
        assert "::tpy::own_iter(std::move(xs))" in out

    def test_method_call_arg_routes_view(self):
        # A `d.keys()` arg renders the inline dict-view call
        # (call.inst_view_arg).
        src = ("def main():\n"
               "    d = {1: 2}\n"
               "    ks = list(d.keys())\n"
               "    print(len(ks), len(d))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        full = src + "main()\n"
        assert self._cpp(full, thir=True) == self._cpp(full, thir=False)

    def test_ptr_null_ctor_routes(self):
        # `Ptr[Int32]()` (typed-nullptr render) routes byte-identically via the
        # PtrType null-ctor arm.
        src = ("from tpy import Int32, Ptr\n"
               "def main():\n"
               "    p = Ptr[Int32]()\n"
               "    print(p is None)\n")
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)


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

    def test_template_native_instantiation_stays_ast(self):
        # An arg-ful native-record instantiation whose real __init__ set is
        # @cpp_template / multi-overload has its own emit arms -> AST.
        src = ("from tpy import Int32, Span\n"
               "def main():\n"
               "    a = [1, 2, 3]\n"
               "    s = Span[Int32](a)\n"
               "    print(len(s), len(a))\n"
               "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_plain_stub_native_argful_instantiation_routes(self):
        # An arg-ful native-record instantiation whose real __init__ is a
        # PLAIN stub renders the same `type_to_cpp(call_type)(args)`
        # (`::tpy::UninitHeapStorage<int32_t>(1)`).
        src = ("from tpy.mem import UninitHeapStorage\n"
               "from tpy import Int32\n"
               "def main():\n"
               "    s: UninitHeapStorage[Int32] = UninitHeapStorage(1)\n"
               "    s.init0(41)\n"
               "    print(s.load0())\n"
               "    s.drop0()\n"
               "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        assert "::tpy::UninitHeapStorage<int32_t> s = " \
               "::tpy::UninitHeapStorage<int32_t>(1);" in thir_cpp

    def test_instantiation_omitted_defaults_routes(self):
        # The instantiation form admits omitted trailing defaults like the
        # raw-name face: `Cell[Int32]()` over a defaulted ctor param calls
        # the spelled zero-arg ctor (the default rides the C++ signature).
        src = ("from tpy import Int32\n"
               "class Cell[T]:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32 = 7) -> None:\n"
               "        self.x = x\n"
               "def main():\n"
               "    c = Cell[Int32]()\n"
               "    print(c.x)\n"
               "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        assert "Cell<int32_t> c = Cell<int32_t>();" in thir_cpp


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

    def test_nested_generic_call_in_print_routes(self):
        # A print arg is a flush position: the generic call's TypeParamRef
        # ref-slot literal temps hoist before the cout chain, so the body
        # routes like the direct decl-init shape (TestGenericPlainCallee).
        src = ("def pick[T](a: T, b: T) -> T:\n"
               "    return b\n"
               "def use():\n"
               "    print(pick(1, 2))\n"
               "use()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
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

    def test_str_type_arg_local_routes(self):
        # `Box[str]("hello")` -- a concrete str-family type arg spells the
        # storage form (`Box<std::string>`) on both paths.
        src = ("class Box[T]:\n"
               "    value: T\n"
               "    def __init__(self, value: T) -> None:\n"
               "        self.value = value\n"
               "def main():\n"
               "    b = Box[str](\"hello\")\n"
               "    print(b.value)\n"
               "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        assert "Box<std::string> b = Box<std::string>(\"hello\");" in thir_cpp

    def test_enum_type_arg_routes(self):
        # An enum arg is spelled identically by both paths at lowering time
        # (the F1 fence re-scope measurement), so the outer generic routes.
        src = ("from enum import Enum\n"
               "class Color(Enum):\n"
               "    RED = 1\n"
               "class Box[T]:\n"
               "    value: T\n"
               "    def __init__(self, value: T) -> None:\n"
               "        self.value = value\n"
               "def main():\n"
               "    b = Box[Color](Color.RED)\n"
               "    print(1)\n"
               "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None


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

    def test_name_arg_routes(self):
        # A bare lvalue name copies into a temp then moves
        # (`auto __tmp = n; Box<int32_t>(std::move(__tmp))`) -- the Own-lvalue
        # ctor-arg row renders the same temp+move, byte-identically.
        src = self._decl("n", sig="n: Int32")
        assert _fn(_lower_ctx(src), "mk") is not None
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


class TestGenericDeclArms:
    """Generic decl arms: the open-T val_or_ref_t local, the borrow
    method-call REF_ALIAS, and the container type-arg slice."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    BOX = (
        "from tpy import Int32\n"
        "class Box[T]:\n"
        "    value: T\n"
        "    def __init__(self, value: T) -> None:\n"
        "        self.value = value\n"
        "    def get(self) -> T:\n"
        "        return self.value\n"
    )

    def test_open_t_local_from_method_call_routes(self):
        # `item = box.get()` in a generic body -> the form-neutral
        # `::tpy::val_or_ref_t<T> item = box.get();` bind.
        src = (self.BOX
               + "def read[T](box: Box[T]) -> None:\n"
               + "    item = box.get()\n"
               + "    print(1)\n")
        fn = _fn(_lower_ctx(src), "read")
        assert fn is not None
        out = self._cpp(src + "read(Box[Int32](1))\n", thir=True)
        assert "::tpy::val_or_ref_t<T> item = box.get();" in out

    def test_open_t_local_byte_identical(self):
        src = (self.BOX
               + "def read[T](box: Box[T]) -> None:\n"
               + "    item = box.get()\n"
               + "    print(1)\n"
               + "read(Box[Int32](1))\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_open_t_local_reassigned_falls_back(self):
        # References cannot rebind: a reassigned open-T local stays AST.
        src = (self.BOX
               + "def read[T](box: Box[T]) -> None:\n"
               + "    item = box.get()\n"
               + "    item = box.get()\n"
               + "    print(1)\n")
        assert _fn(_lower_ctx(src), "read") is None

    def test_record_ref_alias_from_method_call(self):
        # Concrete instantiation site: the substituted T-return binds the
        # plain record alias (`Rec& num = h.get();`).
        src = (
            "from tpy import Int32\n"
            "class Rec:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class Holder[T]:\n"
            "    item: T\n"
            "    def __init__(self, item: T) -> None:\n"
            "        self.item = item\n"
            "    def get(self) -> T:\n"
            "        return self.item\n"
            "def main() -> None:\n"
            "    h = Holder[Rec](Rec(10))\n"
            "    num = h.get()\n"
            "    print(num.v)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        out = self._cpp(src, thir=True)
        assert "Rec& num = h.get();" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_container_type_arg_record_decl_routes(self):
        # `Box[list[Int32]]` joins the F1 type-arg slice: the local decl,
        # ctor and get() all spell through the formatter form.
        src = (self.BOX
               + "def main() -> None:\n"
               + "    b = Box[list[Int32]]([1, 2, 3])\n"
               + "    xs = b.get()\n"
               + "    print(len(xs))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert ("Box<std::vector<int32_t>> b = "
                "Box<std::vector<int32_t>>({1, 2, 3});" in out)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestGenericCallArgArms:
    """Generic call-arg arms: non-scalar T-slot names, ref-slot temps for
    str/None/scalar-call rvalues, and the explicit-targ callee face."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_record_name_into_t_slot(self):
        # A record NAME lvalue binds the `param_val_or_ref_t<T>` ref slot
        # bare, like the concrete `const R&` slot.
        src = (
            "from tpy import Int32\n"
            "class MyInt:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "def is_less[T](a: T, b: T) -> bool:\n"
            "    return True\n"
            "def main() -> None:\n"
            "    x = MyInt(1)\n"
            "    y = MyInt(2)\n"
            "    print(is_less(x, y))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "is_less<MyInt>(x, y)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_explicit_type_args_route(self):
        # `f[str](x)` / `f[None](None)`: the lingering subscript_callee no
        # longer rejects the resolved explicit-targ call; literal args hoist
        # the ref-slot temp with the resolved spelling.
        src = (
            "def identity[T](x: T) -> T:\n"
            "    return x\n"
            "def main() -> None:\n"
            "    s = identity[str](\"hello\")\n"
            "    identity[None](None)\n"
            "    print(s)\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "std::string __tmp_1 = \"hello\";" in out
        assert "identity<std::string>(__tmp_1)" in out
        assert "std::monostate __tmp_2 = std::monostate{};" in out
        assert "identity<std::monostate>(__tmp_2)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_str_call_rvalue_temp(self):
        # A by-value str call into a T slot hoists the same named temp.
        src = (
            "def identity[T](x: T) -> T:\n"
            "    return x\n"
            "def make() -> str:\n"
            "    return \"a\" + \"b\"\n"
            "def main() -> None:\n"
            "    print(identity(make()))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "std::string __tmp_1 = make();" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_float_literal_own_slot_renders_bare(self):
        # `Box(2.71)`: a FloatLiteralType literal into an Own[float] ctor
        # slot renders bare (no temp) on both paths.
        src = (
            "from tplib.box import Box\n"
            "def main() -> None:\n"
            "    bf = Box(2.71)\n"
            "    print(bf.get())\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "(2.71)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)



class TestBoundedReceiversAndGenericMethods:
    """Bounded-T receiver dispatch through the protocol checker, the raw-T
    method-slot temp, and generic-method targs."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_bounded_t_receiver_method_call(self):
        # `item.to_str()` on a `[T: Stringable]` param routes like a
        # structural-protocol receiver: bare member call in the template.
        src = (
            "from typing import Protocol\n"
            "class Stringable(Protocol):\n"
            "    def to_str(self) -> str: ...\n"
            "def stringify[T: Stringable](item: T) -> str:\n"
            "    return item.to_str()\n"
            "class V:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def to_str(self) -> str:\n"
            "        return 'v'\n"
            "def main() -> None:\n"
            "    v = V()\n"
            "    print(stringify(v))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "stringify") is not None
        out = self._cpp(src, thir=True)
        assert "return item.to_str();" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_generic_method_targs(self):
        # Inferred targs spell the method_targs suffix.
        src = (
            "from tpy import Int32\n"
            "class Conv:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def identity[U](self, val: U) -> U:\n"
            "        return val\n"
            "def main() -> None:\n"
            "    c = Conv()\n"
            "    print(c.identity(42))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "c.identity<int32_t>(42)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_generic_method_class_shadow_bare_call(self):
        # A method-level T shadow-binding the class's own T resolves with
        # NO inferred args -- the call spells the bare member, no <...>
        # suffix (T is fixed by the receiver).
        src = (
            "from tpy import Int32, Copyable\n"
            "class Cell[T]:\n"
            "    value: T\n"
            "    def __init__(self, value: T) -> None:\n"
            "        self.value = value\n"
            "    def duplicate[T: Copyable](self) -> T:\n"
            "        return self.value\n"
            "def main() -> None:\n"
            "    c = Cell[Int32](7)\n"
            "    print(c.duplicate())\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "c.duplicate()" in out
        assert "c.duplicate<" not in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_tparam_slot_ctor_rvalue_temp(self):
        # A ctor rvalue into a generic-record method's raw T slot hoists
        # the named temp with the substituted type.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Printer[T]:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def show(self, item: T) -> Int32:\n"
            "        return 1\n"
            "def main() -> None:\n"
            "    pr = Printer[P]()\n"
            "    print(pr.show(P(7)))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "P __tmp_1 = P(7);" in out
        assert "pr.show(__tmp_1)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_nested_generic_call_t_passthrough(self):
        # `outer(x)` inside a generic body: the callee's T substitutes to the
        # caller's own T and the name passes the form-neutral slot bare.
        src = (
            "from typing import Sized\n"
            "from tpy import Int32\n"
            "def inner_len[T: Sized](x: T) -> Int32:\n"
            "    return len(x)\n"
            "def outer_len[T: Sized](x: T) -> Int32:\n"
            "    return inner_len(x)\n"
            "def main() -> None:\n"
            "    xs = [1, 2, 3]\n"
            "    print(outer_len(xs))\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "outer_len") is not None
        out = self._cpp(src, thir=True)
        assert "return inner_len<T>(x);" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestGenericCallDeclAndUnitArgs:
    """Generic-callee record-rvalue decls, unit-None args, and
    pending-float Own slots."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_generic_callee_record_rvalue_decl(self):
        # `cloned = clone_it(box)` -- an Own[T]-returning generic call binds
        # the owned record local with the explicit-targ spelling.
        src = (
            "from typing import Protocol, Self\n"
            "from tpy import Int32, Own\n"
            "class Clonable(Protocol):\n"
            "    def clone(self) -> Own[Self]: ...\n"
            "class BoxC:\n"
            "    value: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.value = v\n"
            "    def clone(self) -> Own[BoxC]:\n"
            "        return BoxC(self.value)\n"
            "def clone_it[T: Clonable](item: T) -> Own[T]:\n"
            "    return item.clone()\n"
            "def main() -> None:\n"
            "    box = BoxC(123)\n"
            "    cloned = clone_it(box)\n"
            "    print(cloned.value)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        # clone_it's own body (a bounded-T `item.clone()` returning bare T)
        # must ROUTE, not just byte-match via fallback -- pins the
        # _tparam_value(ret) protocol-method return branch.
        assert _fn(thir, "clone_it") is not None
        out = self._cpp(src, thir=True)
        assert "BoxC cloned = clone_it<BoxC>(box);" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_unit_none_and_pending_float_args(self):
        # `Rc.new(None)` / `Box(None)` render the bare monostate arg;
        # `Rc.new(3.14)` passes the pending-float Own slot bare.
        src = (
            "from tplib.rc import Rc\n"
            "from tplib.box import Box\n"
            "def main() -> None:\n"
            "    r = Rc.new(None)\n"
            "    b = Box(None)\n"
            "    rf = Rc.new(3.14)\n"
            "    print(rf.get())\n"
            "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        out = self._cpp(src, thir=True)
        assert "Rc<std::monostate>::new_<std::monostate>(std::monostate{})" in out
        assert "Box<std::monostate>(std::monostate{})" in out
        assert "Rc<double>::new_<double>(3.14)" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestReadonlyGenericConstPaths:
    """The two readonly-keyed branches nothing in the corpus reaches: the
    val_or_cref_t open-T decl and the const REF_ALIAS off a readonly
    method's ref return -- neither the ratchet nor the byte-diff can see
    them regress without these pins."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    RO_BOX = (
        "from tpy import Int32, readonly\n"
        "class Box[T]:\n"
        "    value: T\n"
        "    def __init__(self, value: T) -> None:\n"
        "        self.value = value\n"
        "    @readonly\n"
        "    def get(self) -> T:\n"
        "        return self.value\n"
    )

    def test_open_t_local_from_readonly_method_uses_cref(self):
        # A readonly method's T return binds the const trait
        # (`::tpy::val_or_cref_t<T> item = box.get();`).
        src = (self.RO_BOX
               + "def read[T](box: Box[T]) -> None:\n"
               + "    item = box.get()\n"
               + "    print(1)\n"
               + "read(Box[Int32](1))\n")
        assert _fn(_lower_ctx(src), "read") is not None
        out = self._cpp(src, thir=True)
        assert "::tpy::val_or_cref_t<T> item = box.get();" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_const_ref_alias_from_readonly_method(self):
        # Concrete site: the readonly method's substituted ref return binds
        # `const R&` -- the _f1_is_const readonly-method branch.
        src = (
            "from tpy import Int32, readonly\n"
            "class Rec:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class Holder[T]:\n"
            "    item: T\n"
            "    def __init__(self, item: T) -> None:\n"
            "        self.item = item\n"
            "    @readonly\n"
            "    def get(self) -> T:\n"
            "        return self.item\n"
            "def main() -> None:\n"
            "    h = Holder[Rec](Rec(10))\n"
            "    num = h.get()\n"
            "    print(num.v)\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        out = self._cpp(src, thir=True)
        assert "const Rec& num = h.get();" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestGenericCompositionWitnesses:
    """The two compositions the waves-7-9 merge check hand-traced: a generic
    method's targs alongside a vararg pack, and a marker-only bound."""

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_generic_method_targs_with_vararg_pack(self):
        # `b.combine(1, 2)` -- method-level targs and the vararg pack render
        # orthogonally (`b.combine<int32_t>(<pack>)`), the seam where the
        # generics-foundation targs met the waves' THIRVarargPack.
        src = (
            "from tpy import Int32\n"
            "class B:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def combine[T](self, *xs: T) -> Int32:\n"
            "        return len(xs)\n"
            "def main() -> None:\n"
            "    b = B()\n"
            "    print(b.combine(1, 2))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        assert _fn(thir, "combine") is not None
        out = self._cpp(src, thir=True)
        assert "b.combine<int32_t>(" in out
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_marker_only_bound_routes_as_plain_t(self):
        # A `[T: Send]` marker bound resolves through _bounded_tparam_protocol
        # (Send IS a protocol type) but carries no methods, so a body over it
        # is the ordinary form-neutral T pass-through.
        src = (
            "from tpy import Int32, Send\n"
            "def idpass[T: Send](x: T) -> T:\n"
            "    return x\n"
            "def main() -> None:\n"
            "    print(idpass(5))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "idpass") is not None
        assert _fn(thir, "main") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


class TestOpenTOwnReturnValidation:
    # `Own[T]` with T an open type param materializes storage from a borrow
    # source exactly like `Own[Record]` does (`val_or_cref_t<T>` -> `T`, the
    # same NRVO / implicit move), but TypeParamRef is not a NominalType, so
    # the validator's row missed it and a routed body FAILED the walk
    # (THIRValidationError) instead of falling back.
    def test_open_t_marker_ret_row_is_witnessed(self):
        # The gate row itself (not just the validator): an open-T method
        # result at a marker/qualcall slot. Without a face this row was
        # invisible to the faces coverage metric and pinned only by the
        # corpus ratchet.
        src = ("from tpy import Int32, Ptr\n"
               "class Store[T]:\n"
               "    _v: T\n"
               "    def __init__(self, v: T) -> None:\n        self._v = v\n"
               "    def load(self) -> T:\n        return self._v\n"
               "class Holder[T]:\n"
               "    _s: Ptr[Store[T]]\n"
               "    def __init__(self, p: Ptr[Store[T]]) -> None:\n"
               "        self._s = p\n"
               "    def load_at(self) -> T:\n"
               "        return self._s.load()\n")
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("method.qualcall.ret_tparam", 0) >= 1

    def test_own_open_t_return_is_borrow_legal(self):
        from ..typesys import NominalType, OwnType, TypeParamRef, UnionType
        from .validate import _borrow_legal_return
        t = TypeParamRef(name="T", bound=None)
        assert _borrow_legal_return(OwnType(wrapped=t)) is True

    def test_bare_open_t_return_legal_via_the_nonvalue_row(self):
        # NOT a boundary, though it looks like one: a BARE open-T return
        # was already legal before this change, via the
        # pre-existing not-a-value-type row (TypeParamRef.is_value_type() is
        # False). Only the Own-WRAPPED form reached the failure, because
        # OwnType(T).is_value_type() is True and so skips that row.
        from ..typesys import OwnType, TypeParamRef
        from .validate import _borrow_legal_return
        t = TypeParamRef(name="T", bound=None)
        assert t.is_value_type() is False
        assert _borrow_legal_return(t) is True
        assert OwnType(wrapped=t).is_value_type() is True

    def test_own_union_return_borrow_legal(self):
        # The union-returns wave added the Own[union] row: a borrow source
        # at the by-value `std::variant<...>` slot is legal C++ (the
        # converting ctor materializes, implicit move under P1825); the
        # RETURN ARM now owns the source gating instead of this validator.
        from ..typesys import NominalType, OwnType, UnionType
        from .validate import _borrow_legal_return
        u = UnionType(members=(NominalType("A", ()), NominalType("B", ())))
        assert OwnType(wrapped=u).is_value_type() is True
        assert _borrow_legal_return(OwnType(wrapped=u)) is True


class TestGenericTupleLiteralArg:
    """A tuple LITERAL at a value-tuple-resolved T slot renders the spelled
    brace prvalue INLINE (`push_t<...>(pq, std::tuple<...>{2, "second"})`)
    -- no ref-slot temp, unlike the str/list literal rows."""

    _PUSH = (
        "from tpy import Int32\n"
        "def push_t[T](xs: list[T], item: T) -> None:\n"
        "    xs.append(item)\n"
    )

    def test_value_tuple_literal_routes_inline(self):
        src = (self._PUSH
               + "def use() -> None:\n"
               + "    pq: list[tuple[Int32, str]] = []\n"
               + '    push_t(pq, (2, "second"))\n'
               + "    print(len(pq))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("call.generic_tuple_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_nested_value_tuple_literal_routes(self):
        src = (self._PUSH
               + "def use() -> None:\n"
               + "    nested: list[tuple[tuple[Int32, Int32], str]] = []\n"
               + '    push_t(nested, ((1, 2), "x"))\n'
               + "    print(len(nested))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("call.generic_tuple_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_record_element_tuple_literal_still_defers(self):
        # Boundary: a record element gives the tuple pointer-repr storage --
        # outside the value family, the body falls back whole.
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
            "def take_any[T](x: T) -> None:\n    pass\n"
            "def use() -> None:\n"
            "    b = Box(1)\n"
            '    take_any((b, "x"))\n')
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestOwnProtoContainerArg:
    """A CONTAINER conformer NAME into an `Own[protocol]` slot of a generic
    callee (`indexed(nums)` at `Own[Iterable[T]]`): the monomorphized
    `T_items&&` param consumes the container, so the last-use lvalue moves
    (`indexed<int32_t>(std::move(nums))`). The still-live copy half is
    unwitnessed and rejects; the sgen lambda reads the captured Own param
    bare (Own[PROTOCOL] payloads only -- an Own[container] param flips the
    skeleton's iterable-strategy classification and must stay AST)."""

    _SRC = ("from tpy import Own, Int32\n"
            "from typing import Iterable, Iterator\n\n"
            "def indexed[T](items: Own[Iterable[T]])"
            " -> Iterator[tuple[Int32, T]]:\n"
            "    i: Int32 = 0\n"
            "    for item in items:\n"
            "        yield (i, item)\n"
            "        i += 1\n\n")

    def test_last_use_container_moves(self):
        from .testutil import _assert_routes_byte_identical
        src = (self._SRC
               + "def main() -> None:\n"
               + "    nums: list[Int32] = [10, 20, 30]\n"
               + "    for i, n in indexed(nums):\n"
               + "        print(i, n)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "indexed<int32_t>(std::move(nums))" in cpp

    def test_still_live_container_copies_and_moves_temp(self):
        # The container is read after the call: the still-live half hoists
        # the copy temp and moves that (`auto __tmp_N = nums;` +
        # `indexed<int32_t>(std::move(__tmp_N))`) -- the cell-D
        # argtemp.own_proto_container arm.
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (self._SRC
               + "def main() -> None:\n"
               + "    nums: list[Int32] = [10, 20, 30]\n"
               + "    for i, n in indexed(nums):\n"
               + "        print(i, n)\n"
               + "    print(len(nums))\n"
               + "main()\n")
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("argtemp.own_proto_container", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_own_container_sgen_param_stays_ast(self):
        # BOUNDARY: an Own[CONTAINER] sgen param read must keep
        # name.own_read -- admitting it flips the skeleton's
        # iterable-strategy classification (ctx.var_types is seeded by the
        # AST's gen_body, not the leaf path); dualgen-caught scaffolding
        # divergence.
        from .testutil import _thir_ctx
        src = ("from tpy import Own, Int32\n"
               "from typing import Iterator\n\n"
               "def drain(xs: Own[list[Int32]]) -> Iterator[Int32]:\n"
               "    for x in xs:\n"
               "        yield x + len(xs)\n\n"
               "def main() -> None:\n"
               "    for u in drain([1, 2, 3]):\n"
               "        print(u)\n"
               "main()\n")
        _ctx, fallback = _thir_ctx(src)
        assert fallback == {"body:name.own_read": 1}, fallback
        _assert_byte_identical(src)
