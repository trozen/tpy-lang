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
        # must NOT build a no-op STORAGE->STORAGE convert (a validator crash before
        # this was fixed); it emits the bare source.
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
