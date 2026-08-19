"""The widened tuple-literal DECL family (_decl_tuple_nested): container
and value-union elements route with the AST's auto-vs-typed decl split
(`auto` iff the tuple has ref elements), pointer-repr-element tuples
register storage like the record-tuple decl."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestWideTupleLiteralDecl:
    def test_container_elements_route_auto(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t: tuple[dict[str, Int32], Int32] = ({\"a\": 1}, 42)\n"
               "    print(t)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["decl.tuple_literal_wide"] >= 1
        cpp = _assert_byte_identical(src)
        assert ("auto t = std::tuple<::tpy::ordered_map<std::string, "
                "int32_t>, int32_t>{" in cpp[1])

    def test_set_element_routes(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t: tuple[set[Int32], str] = ({10, 20}, \"hello\")\n"
               "    print(t)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_union_elements_route_typed(self):
        # Value-variant elements: no ref elements, so the decl keeps the
        # SPELLED tuple type (the auto split's other side).
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t: tuple[Int32 | str, Int32 | str] = (1, \"hi\")\n"
               "    print(t)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("std::tuple<std::variant<int32_t, std::string>, "
                "std::variant<int32_t, std::string>> t = " in cpp[1])

    def test_reassigned_wide_tuple_routes_via_reassign_arms(self):
        # The widened branch is first-decl only; the reassignment itself
        # rides the existing reassign machinery -- byte-identical.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t: tuple[dict[str, Int32], Int32] = ({\"a\": 1}, 42)\n"
               "    t = ({\"b\": 2}, 43)\n"
               "    print(t)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)


class TestUnionContainerLiterals:
    def test_mixed_union_dict_values_route(self):
        # A dict literal with union VALUES: the str literal lands bare, the
        # nested array literal takes the typed member ctor (union_prefix).
        src = ("def main() -> None:\n"
               "    d3: dict[str, list[int] | str] = "
               "{\"nums\": [1, 2], \"tag\": \"ok\"}\n"
               "    print(d3)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["containerlit.union_member_prefix"] >= 1
        cpp = _assert_byte_identical(src)
        assert ("{\"nums\", std::vector<::tpy::BigInt>{1, 2}}" in cpp[1])

    def test_optional_list_elements_route(self):
        src = ("def main() -> None:\n"
               "    l3: list[list[int] | None] = [[1, 2], None, [3]]\n"
               "    print(l3)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("{std::vector<::tpy::BigInt>{1, 2}, std::nullopt, "
                "std::vector<::tpy::BigInt>{3}}" in cpp[1])

    def test_three_way_union_none_monostate(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    l5: list[Int32 | str | None] = [1, \"two\", None]\n"
               "    print(l5)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "{1, \"two\", std::monostate{}}" in cpp[1]

    def test_tuple_union_elem_literals_route(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    l4: list[tuple[str, Int32 | str]] = "
               "[(\"a\", 1), (\"b\", \"two\")]\n"
               "    print(l4)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["containerlit.tuple_union_elem"] >= 1
        _assert_byte_identical(src)

    def test_mixed_union_record_ctor_value_routes(self):
        src = ("from tpy import Int32\n"
               "class Pt:\n"
               "    x: Int32\n"
               "    y: Int32\n"
               "    def __init__(self, x: Int32, y: Int32) -> None:\n"
               "        self.x = x\n"
               "        self.y = y\n"
               "def main() -> None:\n"
               "    d4: dict[str, Pt | str] = "
               "{\"p\": Pt(1, 2), \"name\": \"origin\"}\n"
               "    print(len(d4))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "{\"p\", Pt(1, 2)}" in cpp[1]


class TestTupleElemContainerWrite:
    def test_tuple_elem_setitem_routes(self):
        # `t[0][0] = 9` writes through the get lvalue.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t = ([1, 2], [3, 4])\n"
               "    print(t)\n"
               "    t[0][0] = 9\n"
               "    print(t)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "::tpy::__setitem__(std::get<0>(t), 0, 9);" in cpp[1]

    def test_tuple_elem_value_read_stays_gated(self):
        # A container element read at a VALUE sink (a copy decl) keeps the
        # tuple-shape reject -- only RECEIVER positions are admitted.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    t = ([1, 2], [3, 4])\n"
               "    row = t[0]\n"
               "    print(row)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)


class TestDemotedArrayAlias:
    def test_array_alias_binds_reference(self):
        # A demoted list literal keeps list ALIAS semantics: `ys = xs`
        # binds `std::array<...>&` and mutation through the alias is
        # visible on the original.
        src = ("def main() -> None:\n"
               "    xs = [1, 2]\n"
               "    ys = xs\n"
               "    ys[0] = 9\n"
               "    print(xs)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "std::array<int32_t, 2>& ys = xs;" in cpp[1]

    def test_bytearray_alias_binds_reference(self):
        # `bytearray` is a reference type like `list`, so its name alias binds
        # the same `T&`. The span-borrow shape that keeps it out of other arms
        # is a PARAM/arg-slot fact -- the REF_ALIAS decl emitter is
        # family-blind.
        src = ("def main() -> None:\n"
               "    b = bytearray(b\"abc\")\n"
               "    c = b\n"
               "    c.append(33)\n"
               "    print(len(b))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "std::vector<uint8_t>& c = b;" in cpp[1]


class TestUnionLiteralBoundaries:
    def test_two_container_members_stay_ast(self):
        # A union with TWO container members has no unique union_prefix
        # target -- the nested array literal keeps rejecting.
        src = ("def main() -> None:\n"
               "    d: dict[str, list[int] | list[str]] = {\"a\": [1]}\n"
               "    print(len(d))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)


class TestChainedTupleElemWrite:
    def test_container_of_tuple_chain_routes(self):
        # `xs[0][1][0] = 8`: the tuple-element receiver off a container
        # element, plus the alias-mutate sibling.
        src = ("def main() -> None:\n"
               "    xs = [([1, 2], [3, 4])]\n"
               "    xs[0][1][0] = 8\n"
               "    ys = xs\n"
               "    ys[0][0][0] = 7\n"
               "    print(xs)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("::tpy::__setitem__(std::get<1>(::tpy::__getitem__(xs, 0)), "
                "0, 8);" in cpp[1])
