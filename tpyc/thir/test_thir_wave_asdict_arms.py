"""Pins for the asdict-expansion arms: the dict-CONSTRUCTION call at a
container element slot, the multi-list-member union comp disambiguation,
the container FIELD at a union element slot, the dict-literal tuple
member, the FIELD-receiver dict view, the dict-literal print arg, and the
all-rvalue storage spelling of a tuple literal at the print sink. Each
mirrors a render the asdict/astuple macro expansion reaches; the mixed
lvalue+rvalue print tuple and the container-FIELD expansion element stay
deferred."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
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

    def test_field_receiver_dict_view_for_head_routes(self):
        # CONVERTED: the FOR-HEAD flavor routes now. Its unpack branch was
        # missing the storage-form loop-var registration the AST performs
        # for every for head, which showed up as a binding-fact gap
        # (tplib/json_model_nested) rather than a byte diff; supplying it
        # is what let the field receiver open here.
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
        assert _fn(thir, "show") is not None
        out = "".join(_assert_byte_identical(src))
        assert "::tpy::dict_items(h.m)" in out

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
        # BOUNDARY, and NOT at the tuple spelling any more: the all-rvalue
        # storage arm now renders this wrap's `TuplePrinter(std::tuple<
        # std::vector<..>>(..))` correctly. What keeps the shape out is its
        # ELEMENT -- the expansion's container FIELD read rejects at
        # `field.result_type` inside `_lower_container_elem`. Fence the
        # element row, not the tuple render.
        src = _HDR + (
            "from dataclasses import dataclass, astuple\n"
            "@dataclass\n"
            "class Q:\n"
            "    xs: list[Int32]\n"
            "def main() -> None:\n"
            "    print(astuple(Q([1, 2])))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:field.result_type")


class TestAsdictBoundaries:
    def test_set_literal_tuple_whole_decl_still_defers(self):
        # The RETURN of an owned-container tuple routes; what still defers is
        # the caller's WHOLE-tuple decl -- binding the storage tuple to one
        # local is a slot the decl cascade does not spell (an unpack does).
        src = _HDR + (
            "from tpy import Own\n"
            "def make() -> tuple[Own[set[Int32]], Int32]:\n"
            "    return ({1, 2}, 3)\n"
            "def main() -> None:\n"
            "    t = make()\n"
            "    print(len(t[0]), t[1])\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

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
        _assert_rejects_at(_reject_tally(src), "body:expr.dict_comp")


class TestPrintTupleStorageArm:
    """The print sink threads no target, so the AST's slot ladder splits on
    each ELEMENT: an all-rvalue literal takes the storage spelling, a simple
    lvalue the borrow one. The astuple expansion of a dict-of-dataclass field
    is the all-rvalue half; the dict comp inside it needs the value leg's
    node-gated tuple row to build its `tuple` VALUE slot."""

    DICT_OF_DC = _HDR + (
        "from dataclasses import dataclass, astuple\n"
        "@dataclass\n"
        "class P:\n"
        "    x: Int32 = 0\n"
        "    y: Int32 = 0\n"
        "@dataclass\n"
        "class D:\n"
        "    items: dict[str, P]\n"
        "def main() -> None:\n"
        "    d = D({'a': P(1, 2)})\n"
        "    print(astuple(d))\n"
    )

    def test_astuple_dict_of_dataclass_routes(self):
        thir, faces = _lower_ctx_witnessed(self.DICT_OF_DC)
        assert _fn(thir, "main") is not None
        assert faces["print.tuple_literal_storage_arg"] >= 1
        assert faces["comp.dict"] >= 1
        _hpp, cpp = _assert_routes_byte_identical(self.DICT_OF_DC,
                                                  comments=False)
        # The arity-1 paren construct, NOT braces: `std::tuple<T>{x}` would
        # hit the C++23 brace-init ambiguity the AST spells around.
        assert ("::tpy::TuplePrinter(std::tuple<::tpy::ordered_map<"
                "std::string, std::tuple<int32_t, int32_t>>>((") in cpp
        assert ("__result.insert_or_assign(__macro_1, "
                "std::tuple<int32_t, int32_t>{") in cpp

    def test_rvalue_container_literal_in_tuple_routes(self):
        # The general (non-macro) shape of the same arm.
        src = _HDR + (
            "def main() -> None:\n"
            "    print(([1, 2], 3))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["print.tuple_literal_storage_arg"] >= 1
        _assert_routes_byte_identical(src)

    def test_dict_comp_tuple_value_slot_routes_outside_print(self):
        # The value leg's row on its own, at a decl -- it is not gated on
        # the print sink.
        src = _HDR + (
            "def f(src: dict[str, Int32]) -> None:\n"
            "    d = {k: (v, v) for k, v in src.items()}\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    f({'a': 1})\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["comp.dict"] >= 1
        _assert_routes_byte_identical(src)

    def test_record_name_pair_keeps_the_borrow_spelling(self):
        # THE key boundary: two record NAMES are simple lvalues, so the AST
        # renders the BORROW spelling. Keying the storage arm on anything
        # coarser than per-element rvalue-ness (an empty `elem_capture`, for
        # instance) turns this alias into a copy -- byte-visible only here,
        # never on the two cases the arm was built for.
        src = _HDR + (
            "class R:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    def __repr__(self) -> str:\n"
            "        return 'R'\n"
            "def f(a: R, b: R) -> None:\n"
            "    print((a, b))\n"
            "def main() -> None:\n"
            "    f(R(1), R(2))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert not faces.get("print.tuple_literal_storage_arg")
        assert faces["print.tuple_literal_arg"] >= 1
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "std::tuple<R*, R*>{&(a), &(b)}" in cpp

    def test_mixed_lvalue_rvalue_print_tuple_still_defers(self):
        # The AST gives a mixed literal a per-element mixed slot tuple that
        # neither arm builds.
        src = _HDR + (
            "class R:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "    def __repr__(self) -> str:\n"
            "        return 'R'\n"
            "def f(a: R) -> None:\n"
            "    print((a, R(9)))\n"
            "def main() -> None:\n"
            "    f(R(1))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:btuple.elem_rvalue")

    def test_dict_comp_bare_name_tuple_value_still_defers(self):
        # The value leg's row is NODE-gated: a bare NAME value_expr at the
        # tuple slot would need the whole `tuple_to_storage` copy, so it
        # must keep rejecting.
        src = _HDR + (
            "def f(src: dict[str, tuple[Int32, Int32]]) -> None:\n"
            "    d = {k: v for k, v in src.items()}\n"
            "    print(len(d))\n"
            "def main() -> None:\n"
            "    f({'a': (1, 2)})\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.dict_comp")
