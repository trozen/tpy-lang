"""Pins for the asdict-expansion arms: the dict-CONSTRUCTION call at a
container element slot, the multi-list-member union comp disambiguation,
the container FIELD at a union element slot, the dict-literal tuple
member, the FIELD-receiver dict view, and the dict-literal print arg.
Each mirrors a render the asdict/astuple macro expansion reaches; the
remaining astuple tuple-construction print family stays deferred."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = "from tpy import Int32\n"


class TestAsdictArms:
    def test_dict_ctor_call_element_routes(self):
        # The asdict macro's top expansion is a dict({...}) CONSTRUCTION
        # call in print position -- the witnessed shape (a hand-spelled
        # `dict({...})` resolves through a different sema path and is not
        # the macro's node). NB this flat flavor routed before the
        # asdict-arms cell; the NESTED-slot flavor the cell added is
        # exercised through the multi-list and tuple-member pins below.
        src = _HDR + (
            "from dataclasses import dataclass, asdict\n"
            "@dataclass\n"
            "class P:\n"
            "    x: Int32 = 0\n"
            "def main() -> None:\n"
            "    print(asdict(P(1)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_dict_literal_print_arg_routes(self):
        src = _HDR + (
            "def main() -> None:\n"
            "    print({'a': 1})\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_multi_list_member_union_comp_routes(self):
        # The fuzz shape: asdict over a dataclass with a list-of-dataclass
        # field AND a list[str] field -- the expansion's comp sits at a
        # TWO-list-member union value slot and disambiguates by the comp's
        # own sema type; the labels field is the container-field union
        # element.
        src = _HDR + (
            "from dataclasses import dataclass, asdict\n"
            "@dataclass\n"
            "class P:\n"
            "    x: Int32 = 0\n"
            "@dataclass\n"
            "class ML:\n"
            "    points: list[P]\n"
            "    labels: list[str]\n"
            "def main() -> None:\n"
            "    ml = ML([P(1)], ['a'])\n"
            "    print(asdict(ml))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["comp.union_member_source"] >= 1
        assert faces["containerlit.union_field_elem"] >= 1
        _assert_byte_identical(src)

    def test_container_field_union_element_routes(self):
        # A container FIELD read at a union element slot copies bare into
        # the value variant.
        src = _HDR + (
            "class H:\n"
            "    tags: list[str]\n"
            "    def __init__(self, t: list[str]) -> None:\n"
            "        self.tags = t\n"
            "def show(h: H) -> None:\n"
            "    d: dict[str, Int32 | list[str]] = {'n': 1,\n"
            "                                       'tags': h.tags}\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    show(H(['a']))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        assert faces["containerlit.union_field_elem"] >= 1
        _assert_byte_identical(src)

    def test_field_receiver_dict_view_comp_routes(self):
        # The FIELD-receiver dict view (`::tpy::dict_items(h.m)`) in a COMP
        # source: the ITERABLE override is receiver-blind past the
        # admission (the receiver lowers through the field arm), so the
        # render matches the NAME flavor's.
        src = _HDR + (
            "class H:\n"
            "    m: dict[str, Int32]\n"
            "    def __init__(self, m: dict[str, Int32]) -> None:\n"
            "        self.m = m\n"
            "def show(h: H) -> None:\n"
            "    d = {k: v * 2 for k, v in h.m.items()}\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    show(H({'a': 1}))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        _assert_byte_identical(src)

    def test_field_receiver_dict_view_for_head_still_defers(self):
        # The FOR-HEAD flavor stays out: its storage-tuple loop-var
        # registration is name-receiver-keyed, and routing without it was
        # caught as a binding-fact gap (tplib/json_model_nested). The comp
        # route has no such registration (it lowers its own unpack vars).
        src = _HDR + (
            "class H:\n"
            "    m: dict[str, Int32]\n"
            "    def __init__(self, m: dict[str, Int32]) -> None:\n"
            "        self.m = m\n"
            "def show(h: H) -> None:\n"
            "    for k, v in h.m.items():\n"
            "        print(k, v)\n"
            "def main() -> None:\n"
            "    show(H({'a': 1}))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is None
        _assert_byte_identical(src)

    def test_dict_literal_tuple_member_routes(self):
        # The dict_tuple shape: asdict over a dataclass with a
        # tuple[P, Int32] field -- the expansion's storage tuple carries a
        # dict-LITERAL member rendered self-describing inline.
        src = _HDR + (
            "from tpy import Own\n"
            "from dataclasses import dataclass, asdict\n"
            "@dataclass\n"
            "class P:\n"
            "    x: Int32 = 0\n"
            "@dataclass\n"
            "class T:\n"
            "    pair: tuple[P, Int32]\n"
            "def main() -> None:\n"
            "    t = T((P(1), 2))\n"
            "    print(asdict(t))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)


class TestMacroPrintArg:
    def test_astuple_value_expansion_routes(self):
        # The print gate treats a call-macro arg as its EXPANSION (the
        # AST's gen_expr renders it in place, type-keyed): an all-value
        # astuple expansion rides the pre-existing tuple-literal print
        # row.
        src = _HDR + (
            "from dataclasses import dataclass, astuple\n"
            "@dataclass\n"
            "class P:\n"
            "    x: Int32 = 0\n"
            "    y: Int32 = 0\n"
            "def main() -> None:\n"
            "    print(astuple(P(1, 2)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_plain_value_tuple_literal_print_routes(self):
        # The value-tuple print wrap is a GENERAL admission, not
        # macro-only: a plain typed tuple literal in print position wraps
        # its spelled brace render in TuplePrinter.
        src = _HDR + (
            "def main() -> None:\n"
            "    x: Int32 = 1\n"
            "    y: Int32 = 2\n"
            "    print((x, y))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

    def test_astuple_container_expansion_still_defers(self):
        # BOUNDARY: a pointer-repr astuple expansion (list field) needs
        # the spelled storage-tuple print render
        # (`TuplePrinter(std::tuple<std::vector<..>>(..))`) -- unmirrored;
        # the tuple-literal wrap's borrow-form render would diverge, so
        # the shape must keep deferring.
        src = _HDR + (
            "from dataclasses import dataclass, astuple\n"
            "@dataclass\n"
            "class Q:\n"
            "    xs: list[Int32]\n"
            "def main() -> None:\n"
            "    print(astuple(Q([1, 2])))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is None
        _assert_byte_identical(src)


class TestAsdictBoundaries:
    def test_set_literal_tuple_member_still_defers(self):
        # The unwitnessed sibling: a SET literal tuple member keeps
        # deferring.
        src = _HDR + (
            "from tpy import Own\n"
            "def make() -> tuple[Own[set[Int32]], Int32]:\n"
            "    return ({1, 2}, 3)\n"
            "def main() -> None:\n"
            "    t = make()\n"
            "    print(len(t[0]), t[1])\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "make") is None
        _assert_byte_identical(src)

    def test_narrowed_optional_dict_field_view_still_defers(self):
        # A narrowed Optional dict FIELD receiver: declared-type keyed,
        # the view predicate must not take the narrowed read.
        src = _HDR + (
            "class H:\n"
            "    m: dict[str, Int32] | None\n"
            "    def __init__(self, m: dict[str, Int32]) -> None:\n"
            "        self.m = m\n"
            "def show(h: H) -> None:\n"
            "    if h.m is not None:\n"
            "        d = {k: v for k, v in h.m.items()}\n"
            "        print(len(d))\n"
            "def main() -> None:\n"
            "    show(H({'a': 1}))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is None
        _assert_byte_identical(src)
