"""Property-getter positions: the REF_ALIAS decl (`v = c.items`), the
foreach iterable (`for x in c.items:`), the subscript receiver
(`c.items[0]`), and the setter body's Own-container param move
(`self._items = v` -> `this->_items = std::move(v);`). The Own-RETURNING
getter flavors stay AST at the alias/foreach positions: the AST binds a
non-const `T&` / `auto&` to the by-value getter result (ill-formed C++,
BUGS.md), so the broken oracle is fenced, not mirrored."""

from __future__ import annotations

from .nodes import THIRAssign, THIRFormConvert, THIRVarDecl
from .testutil import (
    _assert_routes_byte_identical, _fn, _lower_ctx, _lower_ctx_witnessed,
)

_CONTAINER = (
    "from tpy import Int32, Own\n"
    "class Container:\n"
    "    _items: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self._items = [1, 2, 3]\n"
    "    @property\n"
    "    def items(self) -> list[Int32]:\n"
    "        return self._items\n"
    "    @items.setter\n"
    "    def items(self, v: list[Int32]) -> None:\n"
    "        self._items = v\n"
)

_OWN_PROP = (
    "from tpy import Int32, Own\n"
    "class Snap:\n"
    "    _items: list[Int32]\n"
    "    def __init__(self) -> None:\n"
    "        self._items = [1]\n"
    "    @property\n"
    "    def snapshot(self) -> Own[list[Int32]]:\n"
    "        out: list[Int32] = []\n"
    "        for x in self._items:\n"
    "            out.append(x)\n"
    "        return out\n"
)


class TestPropertyAliasDecl:
    def test_ref_alias_property_decl_routes(self):
        # `v = c.items` -> `std::vector<int32_t>& v = c.items();` -- the
        # property twin of the borrow-call REF_ALIAS row.
        src = (_CONTAINER
               + "def use(c: Container) -> None:\n"
               + "    v = c.items\n"
               + "    print(v)\n")
        thir, w = _lower_ctx_witnessed(src)
        fn = _fn(thir, "use")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert w.get("decl.record_borrow_call", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_reassigned_property_alias_stays_ast(self):
        # BOUNDARY: the reassigned POINTER sibling needs the `&(c.items())`
        # reseat lift, unwitnessed -- the body keeps the AST path.
        src = (_CONTAINER
               + "def use(c: Container, d: Container) -> None:\n"
               + "    v = c.items\n"
               + "    print(v)\n"
               + "    v = d.items\n"
               + "    print(v)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None

    def test_own_property_alias_stays_ast(self):
        # BOUNDARY (AST bug): an Own-returning getter's alias decl binds
        # `T&` to the by-value result off a mutable receiver -- ill-formed
        # C++ on the AST path, so the shape must keep rejecting.
        src = (_OWN_PROP
               + "def use(s: Snap) -> None:\n"
               + "    v = s.snapshot\n"
               + "    print(v)\n"
               + "    s._items = [9]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestPropertyForeach:
    def test_property_iterable_routes(self):
        # `for x in c.items:` -> `auto& __obj_N = c.items();` -- the
        # container-returning user-METHOD iterable leg one shape over.
        src = (_CONTAINER
               + "def use(c: Container) -> None:\n"
               + "    for x in c.items:\n"
               + "        print(x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_routes_byte_identical(src)

    def test_own_property_iterable_stays_ast(self):
        # BOUNDARY (AST bug): the AST binds `auto&` to the by-value getter
        # result -- ill-formed C++, fenced.
        src = (_OWN_PROP
               + "def use(s: Snap) -> None:\n"
               + "    for x in s.snapshot:\n"
               + "        print(x)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


class TestPropertySubscript:
    def test_property_subscript_read_routes(self):
        # `c.items[0]` -> `::tpy::__getitem__(c.items(), 0)` -- the
        # container-returning CALL receiver row, property flavor.
        src = (_CONTAINER
               + "def use(c: Container) -> None:\n"
               + "    v = c.items[0]\n"
               + "    print(v)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("subscript.call_recv", 0) >= 1
        _assert_routes_byte_identical(src)


class TestPropertySetterBody:
    def test_setter_own_param_move_routes(self):
        # The setter body `self._items = v` moves the Own[list] param at
        # its last use: `this->_items = std::move(v);`.
        thir, w = _lower_ctx_witnessed(_CONTAINER)
        # The getter/setter trio all lower under the property's name; the
        # setter is the one whose body is the field assign.
        assigns = [f.body[0] for f in thir.functions
                   if f.name == "items" and f.body
                   and isinstance(f.body[0], THIRAssign)]
        assert len(assigns) == 1
        # The plain container FIELD slot is the merged reference name
        # row (`field_write.container_name` now covers the Optional
        # slot only).
        assert w.get("field_write.record_name", 0) >= 1
        # The merged name row spells the move as the plain-non-value STORAGE
        # convert (`std::move(v)` -- the documented field-write arm), where
        # the container-only tail used to spell a bare THIRMove. Same render.
        assert isinstance(assigns[0].value, THIRFormConvert)
        assert assigns[0].value.move
        _assert_routes_byte_identical(_CONTAINER + "c = Container()\n"
                                      "c.items = [4]\n"
                                      "print(c.items[0])\n")

    def test_own_param_alias_decl_then_field_sink_routes(self):
        # An Own[container] param aliased by a bare decl and THEN moved into
        # the field: the decl binds a reference to the by-value param
        # (`std::vector<int32_t>& xs = v;`) and only the field sink is a
        # move, so the alias read stays valid on both paths.
        src = (_CONTAINER
               + "    def bad(self, v: Own[list[Int32]]) -> None:\n"
               + "        xs = v\n"
               + "        print(len(xs))\n"
               + "        self._items = v\n")
        hpp, cpp = _assert_routes_byte_identical(
            src + "def main() -> None:\n"
            + "    c = Container()\n    c.bad([1, 2])\nmain()\n")
        out = hpp + cpp
        assert "std::vector<int32_t>& xs = v;" in out
        assert "this->_items = std::move(v);" in out
