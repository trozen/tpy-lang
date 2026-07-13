"""THIR dynamic-attrs (D16) sites: `del obj.attr` / the setattr-dunder write
statement / the delattr builtin's statement-position macro expansion, and the
dict[K, Any] positions the dunder bodies need (setitem / del-item / the Any
return read / the container-name field write in the ctor)."""

from __future__ import annotations

from .testutil import _emit_expr
from .nodes import (
    Form, THIRAssign, THIRCall, THIRCoerce, THIRExprStmt, THIRFormConvert,
    THIRMethodCall, THIRName, THIRReturn, THIRSetItem, THIRStrLiteral,
    THIRSubscript,
)
from .testutil import (
    _fn, _lower_ctx, _lower_ctx_witnessed, _lower_ctor,
)

# The dynamic-attrs fixture: a dict[str, Any] store behind the three dunders
# (the tests/cases/dynamic_attrs shape).
_DYN_BAG = (
    "from typing import Any\n"
    "from tpy import Int32\n"
    "class Bag:\n"
    "    _data: dict[str, Any]\n"
    "    def __init__(self) -> None:\n"
    "        d: dict[str, Any] = {}\n"
    "        self._data = d\n"
    "    def __setattr__(self, name: str, value: Any) -> None:\n"
    "        self._data[name] = value\n"
    "    def __delattr__(self, name: str) -> None:\n"
    "        del self._data[name]\n"
    "    def __getattr__(self, name: str) -> Any:\n"
    "        return self._data[name]\n"
)


class TestDelAttrStatement:
    def test_del_attr_routes_as_delattr_call(self):
        # `del b.x` -> the synthesized `b.__delattr__("x");` through the
        # generic method-call arm (str-literal arg, void result).
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    del b.x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("stmt.del_attr", 0) == 1
        stmt = fn.body[1]
        assert isinstance(stmt, THIRExprStmt)
        call = stmt.expr
        assert isinstance(call, THIRMethodCall)
        assert call.method_cpp == "__delattr__" and not call.is_arrow
        assert _emit_expr(call) == 'b.__delattr__("x")'

    def test_multi_target_del_attr_falls_back(self):
        # The AST arm emits one line per target; the single-node statement
        # lowering rejects the multi-target form.
        thir = _lower_ctx(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    del b.x, b.y\n")
        assert _fn(thir, "f") is None

    def test_delattr_builtin_stmt_routes(self):
        # `delattr(b, "x")` -- a void TpyCall whose macro_expansion is the
        # same synthesized method call, dispatched in statement position.
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    delattr(b, \"x\")\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("expr_stmt.macro_discard", 0) == 1
        stmt = fn.body[1]
        assert isinstance(stmt, THIRExprStmt)
        assert isinstance(stmt.expr, THIRMethodCall)
        assert _emit_expr(stmt.expr) == 'b.__delattr__("x")'


class TestDynSetattrWrite:
    def test_str_literal_write_routes(self):
        # `b.x = "hello"` -> `b.__setattr__("x", ::tpy::make_any(
        # std::string("hello")));` -- the into_any wrap carried as the
        # THIRCoerce `{0}` template.
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    b.x = \"hello\"\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("method.dyn_setattr", 0) == 1
        stmt = fn.body[1]
        assert isinstance(stmt, THIRExprStmt)
        call = stmt.expr
        assert isinstance(call, THIRMethodCall)
        assert call.method_cpp == "__setattr__"
        assert isinstance(call.args[0], THIRStrLiteral)
        value = call.args[1]
        assert isinstance(value, THIRCoerce) and value.coercion_name == "into_any"
        assert value.wrap == "::tpy::make_any(std::string({0}))"
        assert (_emit_expr(call)
                == 'b.__setattr__("x", ::tpy::make_any(std::string("hello")))')

    def test_int_literal_write_routes(self):
        # `b.x = 42` -> `b.__setattr__("x", ::tpy::make_any(::tpy::BigInt(42)))`
        # (the IntLiteralType arm of the coercion's storage-form spelling).
        thir = _lower_ctx(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    b.x = 42\n")
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[1].expr
        assert (_emit_expr(call)
                == 'b.__setattr__("x", ::tpy::make_any(::tpy::BigInt(42)))')

    def test_unpinned_value_falls_back(self):
        # A bool literal's into_any render is not placeholder-pinned (no
        # corpus witness); the body stays on the AST path.
        thir = _lower_ctx(
            _DYN_BAG
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    b.x = True\n")
        assert _fn(thir, "f") is None


class TestAnyValueDictPositions:
    def test_setattr_body_setitem_routes(self):
        # `self._data[name] = value` -- an Any-typed NAME into the dict's Any
        # value slot renders bare (no make_any can fire on either path).
        thir, faces = _lower_ctx_witnessed(_DYN_BAG)
        fn = _fn(thir, "__setattr__")
        assert fn is not None
        assert faces.get("setitem.any_value", 0) == 1
        stmt = fn.body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.value, THIRName) and stmt.value.name == "value"
        assert isinstance(stmt.target, THIRSubscript)
        assert not stmt.target.bounds_safe

    def test_setitem_non_any_value_falls_back(self):
        # A not-yet-Any value (an into_any-coerced literal) keeps the AST's
        # element-slot make_any wrap -> the body stays on the AST path.
        thir = _lower_ctx(
            _DYN_BAG
            + "def f(b: Bag) -> None:\n"
            + "    b._data[\"k\"] = 1\n")
        assert _fn(thir, "f") is None

    def test_delattr_body_delitem_routes(self):
        # `del self._data[name]` -> `::tpy::__delitem__(this->_data, name);`
        # -- the del emit never touches the Any value slot.
        thir, faces = _lower_ctx_witnessed(_DYN_BAG)
        fn = _fn(thir, "__delattr__")
        assert fn is not None
        assert faces.get("delitem.any_value", 0) == 1
        stmt = fn.body[0]
        assert isinstance(stmt, THIRExprStmt)
        call = stmt.expr
        assert isinstance(call, THIRCall)
        assert call.native_name == "tpy::__delitem__"

    def test_getattr_body_any_return_routes(self):
        # `return self._data[name]` at the Any return slot -> the bare
        # checked read `return ::tpy::__getitem__(this->_data, name);`.
        thir, faces = _lower_ctx_witnessed(_DYN_BAG)
        fn = _fn(thir, "__getattr__")
        assert fn is not None
        assert faces.get("ret.any_subscript", 0) == 1
        stmt = fn.body[0]
        assert isinstance(stmt, THIRReturn)
        assert isinstance(stmt.value, THIRSubscript)
        assert stmt.value.form is Form.VALUE

    def test_ctor_container_name_field_write_routes(self):
        # `self._data = d` in the ctor body: the container-name field write
        # (`this->_data = std::move(d);` -- `d` is movable at its last use).
        ctor = _lower_ctor(_DYN_BAG, "Bag")
        assert ctor is not None
        assign = next(s for s in ctor.body if isinstance(s, THIRAssign))
        conv = assign.value
        assert isinstance(conv, THIRFormConvert)
        assert conv.form is Form.STORAGE and conv.move

    def test_list_and_set_name_field_writes_route(self):
        # The admission predicate is container-family-symmetric; list and
        # set names ride the same borrow->storage tail as the dict fixture.
        for ann, lit in (("list[Int32]", "[1]"), ("set[Int32]", "{1}")):
            src = (
                "from tpy import Int32\n"
                "class Holder:\n"
                f"    xs: {ann}\n"
                "    def __init__(self) -> None:\n"
                f"        xs: {ann} = {lit}\n"
                "        self.xs = xs\n"
            )
            ctor = _lower_ctor(src, "Holder")
            assert ctor is not None, ann
            assign = next(s for s in ctor.body
                          if isinstance(s, THIRAssign))
            conv = assign.value
            assert isinstance(conv, THIRFormConvert), ann
            assert conv.form is Form.STORAGE and conv.move, ann


class TestScalarDictRegression:
    def test_scalar_value_dict_delitem_still_routes(self):
        # The pre-existing dict[str, scalar] del-item family is untouched by
        # the Any-value widening.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "def f(d: dict[str, Int32]) -> None:\n"
            + "    del d[\"k\"]\n")
        assert _fn(thir, "f") is not None
