"""THIR dynamic-attrs (D16) sites: `del obj.attr` / the setattr-dunder write
statement / the delattr builtin's statement-position macro expansion, and the
dict[K, Any] positions the dunder bodies need (setitem / del-item / the Any
return read / the container-name field write in the ctor)."""

from __future__ import annotations

from .testutil import _emit_expr
from .nodes import (
    Form, THIRAssign, THIRCall, THIRCoerce, THIRExprStmt, THIRFormConvert,
    THIRMethodCall, THIRMove, THIRName, THIRPrint, THIRReturn, THIRSetItem,
    THIRStrLiteral, THIRSubscript,
)
from .testutil import (
    _assert_routes_byte_identical, _fn, _lower_ctx, _lower_ctx_witnessed,
    _lower_ctor,
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

    def test_bare_literal_at_non_any_param_routes(self):
        # A `__setattr__` whose value param is NOT Any sees no coerce: the
        # bare str literal is a plain arg (`s.host = "ok"` ->
        # `s.__setattr__("host", "ok");`).
        thir, faces = _lower_ctx_witnessed(
            "from typing import Any\n"
            "class Strict:\n"
            "    _data: dict[str, Any]\n"
            "    def __init__(self) -> None:\n"
            "        d: dict[str, Any] = {}\n"
            "        self._data = d\n"
            "    def __setattr__(self, name: str, value: str) -> None:\n"
            "        self._data[name] = value\n"
            "def f() -> None:\n"
            "    s = Strict()\n"
            "    s.host = \"ok\"\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("method.dyn_setattr", 0) == 1
        call = fn.body[1].expr
        assert isinstance(call, THIRMethodCall)
        assert _emit_expr(call) == 's.__setattr__("host", "ok")'

    def test_bool_literal_at_non_any_param_falls_back(self):
        # A bool value param's literal is outside the bare-literal leg
        # (str/int only) -- the write stays on the AST path.
        thir = _lower_ctx(
            "from typing import Any\n"
            "class Strict:\n"
            "    flag: bool\n"
            "    def __init__(self) -> None:\n"
            "        self.flag = False\n"
            "    def __setattr__(self, name: str, value: bool) -> None:\n"
            "        if name.startswith(\"_\"):\n"
            "            raise AttributeError(name)\n"
            "def f() -> None:\n"
            "    s = Strict()\n"
            "    s.on = True\n")
        assert _fn(thir, "f") is None


class TestDynGetattrRead:
    def test_cast_any_read_routes(self):
        # `cast(str, b.x)` -> `::tpy::any_cast_or_panic<std::string>(
        # b.__getattr__("x"))`: the synthesized getattr read behind the
        # Any-source cast wrap.
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "from typing import cast\n"
            + "def f() -> None:\n"
            + "    b = Bag()\n"
            + "    print(cast(str, b.x))\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("method.dyn_getattr", 0) == 1
        assert faces.get("call.cast_any", 0) == 1
        stmt = fn.body[1]
        assert isinstance(stmt, THIRPrint)
        arg = stmt.args[0].expr
        assert isinstance(arg, THIRCoerce) and arg.coercion_name == "any_cast"
        inner = arg.expr
        assert isinstance(inner, THIRMethodCall)
        assert inner.method_cpp == "__getattr__"
        assert (_emit_expr(arg)
                == '::tpy::any_cast_or_panic<std::string>(b.__getattr__("x"))')

    def test_non_name_receiver_falls_back(self):
        # A non-NAME receiver (an rvalue ctor call) is outside the read
        # slice; the body stays on the AST path.
        thir = _lower_ctx(
            _DYN_BAG
            + "from typing import cast\n"
            + "def f() -> None:\n"
            + "    print(cast(str, Bag().x))\n")
        assert _fn(thir, "f") is None

    def test_self_receiver_read_routes(self):
        # `return self.k` inside a method -> `return this->__getattr__("k");`
        # (the generic arm's self spelling, arrow receiver).
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "    def peek(self) -> Any:\n"
            + "        return self.k\n")
        fn = _fn(thir, "peek")
        assert fn is not None
        assert faces.get("method.dyn_getattr", 0) == 1
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        call = ret.value
        assert isinstance(call, THIRMethodCall)
        assert call.method_cpp == "__getattr__" and call.is_arrow
        assert _emit_expr(call) == 'this->__getattr__("k")'

    def test_any_ret_method_call_routes(self):
        # `cast(str, b.peek())`: the Any-returning method call lands bare
        # under the any_cast wrap -- the free-call AnyType row's twin.
        thir, faces = _lower_ctx_witnessed(
            _DYN_BAG
            + "    def peek(self) -> Any:\n"
            + "        return self.k\n"
            + "from typing import cast\n"
            + "def f(b: Bag) -> None:\n"
            + "    print(cast(str, b.peek()))\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("method.any_ret", 0) >= 1

    def test_self_setattr_falls_back(self):
        # BOUNDARY: the setattr twin keeps its self exclusion -- a dyn
        # write through self stays on the AST path.
        thir = _lower_ctx(
            _DYN_BAG
            + "    def poke(self) -> None:\n"
            + "        self.k = \"w\"\n")
        assert _fn(thir, "poke") is None

    def test_self_hasattr_probe_falls_back(self):
        # BOUNDARY: the hasattr probe form keeps its self exclusion.
        thir = _lower_ctx(
            _DYN_BAG
            + "    def probe(self) -> bool:\n"
            + "        return hasattr(self, \"k\")\n")
        assert _fn(thir, "probe") is None


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

    def test_setitem_into_any_literal_routes(self):
        # A not-yet-Any int-literal value rides the into_any coerce's `{0}`
        # template into the element slot: `::tpy::__setitem__(b._data, "k",
        # ::tpy::make_any(::tpy::BigInt(1)));`.
        source = (
            _DYN_BAG
            + "def f(b: Bag) -> None:\n"
            + "    b._data[\"k\"] = 1\n")
        _assert_routes_byte_identical(source)
        thir, faces = _lower_ctx_witnessed(source)
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("setitem.into_any", 0) == 1
        stmt = fn.body[0]
        assert isinstance(stmt, THIRSetItem)
        value = stmt.value
        assert isinstance(value, THIRCoerce)
        assert value.coercion_name == "into_any"
        assert value.wrap == "::tpy::make_any(::tpy::BigInt({0}))"

    def test_setitem_into_any_name_routes(self):
        # A non-Any str param stored into the Any slot: the coerce wraps the
        # bare name (`::tpy::make_any(std::string(value))`) -- the
        # panic_setattr_reject_unhandled corpus shape.
        thir, faces = _lower_ctx_witnessed(
            "from typing import Any\n"
            "class Strict:\n"
            "    _data: dict[str, Any]\n"
            "    def __init__(self) -> None:\n"
            "        d: dict[str, Any] = {}\n"
            "        self._data = d\n"
            "    def __setattr__(self, name: str, value: str) -> None:\n"
            "        self._data[name] = value\n")
        fn = _fn(thir, "__setattr__")
        assert fn is not None
        assert faces.get("setitem.into_any", 0) == 1
        stmt = fn.body[0]
        assert isinstance(stmt, THIRSetItem)
        value = stmt.value
        assert isinstance(value, THIRCoerce)
        assert value.wrap == "::tpy::make_any(std::string({0}))"
        assert isinstance(value.expr, THIRName) and value.expr.name == "value"

    def test_setitem_into_any_container_literal_falls_back(self):
        # A container-LITERAL inner's brace-init is not placeholder-safe at
        # the element slot -- the gate keeps it on the AST path.
        thir = _lower_ctx(
            _DYN_BAG
            + "def f(b: Bag) -> None:\n"
            + "    b._data[\"k\"] = [1, 2]\n")
        assert _fn(thir, "f") is None

    def test_setitem_into_any_bool_falls_back(self):
        # A bool inner has no placeholder-pinned wrap (no corpus witness);
        # the coerce row admits str/int literals and bare names only.
        thir = _lower_ctx(
            _DYN_BAG
            + "def f(b: Bag) -> None:\n"
            + "    b._data[\"k\"] = True\n")
        assert _fn(thir, "f") is None

    def test_delattr_body_delitem_routes(self):
        # `del self._data[name]` -> `::tpy::__delitem__(this->_data, name);`
        # -- the del emit never touches the Any value slot.
        thir, faces = _lower_ctx_witnessed(_DYN_BAG)
        fn = _fn(thir, "__delattr__")
        assert fn is not None
        assert faces.get("delitem.container", 0) == 1
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


_DYN_HEADERS = (
    "class Headers:\n"
    "    _store: dict[str, str]\n"
    "    def __init__(self, store: dict[str, str]) -> None:\n"
    "        self._store = store\n"
    "    def __getattr__(self, name: str) -> str:\n"
    "        return self._store[name]\n"
)


class TestDynViewFieldReceiver:
    def test_str_dyn_getattr_receiver_routes(self):
        # `h.a.upper()` -> `::tpy::str_upper(h.__getattr__("a"))`: the
        # synthesized read composes as the view family's receiver.
        source = (
            _DYN_HEADERS
            + "def f() -> None:\n"
            + "    h = Headers({\"a\": \"x\"})\n"
            + "    print(h.a.upper())\n"
            + "f()\n")
        _assert_routes_byte_identical(source)
        _, faces = _lower_ctx_witnessed(source)
        assert faces.get("method.recv.dyn_view_field", 0) >= 1

    def test_bytes_dyn_getattr_receiver_routes(self):
        # The bytes twin (`b.p.decode()` composes the same way).
        _assert_routes_byte_identical(
            "class Blobs:\n"
            "    _store: dict[str, bytes]\n"
            "    def __init__(self, store: dict[str, bytes]) -> None:\n"
            "        self._store = store\n"
            "    def __getattr__(self, name: str) -> bytes:\n"
            "        return self._store[name]\n"
            "def f() -> None:\n"
            "    b = Blobs({\"p\": b\"hi\"})\n"
            "    print(b.p.decode())\n"
            "f()\n")

    def test_scalar_dyn_getattr_receiver_still_defers(self):
        # An int-valued dyn read as a method receiver is outside the view
        # slice -- the body keeps the AST path (method.recv.field_parent).
        thir = _lower_ctx(
            "class Counts:\n"
            "    _store: dict[str, int]\n"
            "    def __init__(self, store: dict[str, int]) -> None:\n"
            "        self._store = store\n"
            "    def __getattr__(self, name: str) -> int:\n"
            "        return self._store[name]\n"
            "def f(c: Counts) -> None:\n"
            "    print(c.n.bit_length())\n")
        assert _fn(thir, "f") is None


class TestScalarDictRegression:
    def test_scalar_value_dict_delitem_still_routes(self):
        # The pre-existing dict[str, scalar] del-item family is untouched by
        # the Any-value widening.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "def f(d: dict[str, Int32]) -> None:\n"
            + "    del d[\"k\"]\n")
        assert _fn(thir, "f") is not None


# The Any FIELD write fixture (the any/warn_copy_into_any corpus shape).
_ANY_FIELD = (
    "from typing import Any\n"
    "from tpy import Int32\n"
    "class Node:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Holder:\n"
    "    payload: Any\n"
    "    def __init__(self) -> None:\n"
    "        self.payload = None\n"
)


class TestAnyFieldWrite:
    def test_into_any_record_name_routes(self):
        # `h.payload = n` (n used after): the field assign carries the
        # into_any coerce bare -- `h.payload = ::tpy::make_any(n);` (the
        # store COPIES; sema warns instead of moving).
        thir, faces = _lower_ctx_witnessed(
            _ANY_FIELD
            + "def f(h: Holder) -> None:\n"
            + "    n = Node(1)\n"
            + "    h.payload = n\n"
            + "    print(n.n)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("field_write.any", 0) == 1
        assign = fn.body[1]
        assert isinstance(assign, THIRAssign)
        value = assign.value
        assert isinstance(value, THIRCoerce)
        assert value.coercion_name == "into_any"
        assert value.wrap == "::tpy::make_any({0})"

    def test_into_any_last_use_moves(self):
        # The AST's `_maybe_move` peels the coerce: a movable name's last
        # use moves the whole make_any value
        # (`h.payload = std::move(::tpy::make_any(n));`).
        source = (
            _ANY_FIELD
            + "def f(h: Holder) -> None:\n"
            + "    n = Node(1)\n"
            + "    h.payload = n\n")
        _assert_routes_byte_identical(source)
        thir, faces = _lower_ctx_witnessed(source)
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("field_write.any", 0) == 1
        assign = fn.body[1]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRMove)
        assert isinstance(assign.value.value, THIRCoerce)

    def test_any_name_value_routes(self):
        # An already-Any value copies bare: `h.payload = a;`.
        source = (
            _ANY_FIELD
            + "def f(h: Holder, a: Any) -> None:\n"
            + "    h.payload = a\n")
        _assert_routes_byte_identical(source)
        thir, faces = _lower_ctx_witnessed(source)
        fn = _fn(thir, "f")
        assert fn is not None
        assert faces.get("field_write.any", 0) == 1
        assign = fn.body[0]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRName)
        assert assign.value.name == "a"

    def test_container_literal_value_falls_back(self):
        # A container-LITERAL inner is not placeholder-safe at the field
        # position -- stays on the AST path.
        thir = _lower_ctx(
            _ANY_FIELD
            + "def f(h: Holder) -> None:\n"
            + "    h.payload = [1, 2]\n")
        assert _fn(thir, "f") is None

    def test_bool_literal_value_falls_back(self):
        # A bool inner has no placeholder-pinned wrap -- stays AST.
        thir = _lower_ctx(
            _ANY_FIELD
            + "def f(h: Holder) -> None:\n"
            + "    h.payload = True\n")
        assert _fn(thir, "f") is None
