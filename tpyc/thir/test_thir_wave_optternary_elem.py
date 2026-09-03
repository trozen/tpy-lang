"""The container-ELEMENT Optional-ternary wave.

At a list/dict element, a comprehension element or dict VALUE, and a printed
tuple element, the storage slot is `std::optional<T>` whatever
`uses_pointer_repr()` says, so `_gen_if_expr` wraps BOTH ternary arms in the
spelled optional there (its `in_container_element` carve-out). The admitted
inners are the two whose arms render as self-contained values -- a dict literal
and a value-tuple literal -- which is exactly what the dataclass `asdict()` /
`astuple()` expansion over an `Optional[dataclass]` field emits.

DEEPER positions under the same element REJECT: the AST flag is a sticky
context, so it wraps a ternary nested inside the element too, and over a
pointer-form arm that wrap is ill-formed C++ (`TestNestedElemTernaryStaysOut`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _reject_tags(source):
    return _reject_tally(source)


_PRE = (
    "from typing import Optional\n"
    "from tpy import Int32\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.val = v\n"
    "class Holder:\n"
    "    opt: Optional[Box]\n"
    "    def __init__(self, b: Optional[Box]) -> None:\n"
    "        self.opt = b\n"
)


class TestDataclassAsdictOptionalField:
    # The shape the wave exists for: `asdict()` / `astuple()` over an
    # `Optional[dataclass]` field, direct and through a list / dict field.
    # Every element position the expansion reaches is here -- a dict-literal
    # value at an Optional slot and at a UNION slot, a printed tuple element,
    # a list-comp element, a dict-comp value.
    SRC = (
        "from dataclasses import dataclass, asdict, astuple\n"
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "@dataclass\n"
        "class Point:\n"
        "    x: Int32\n"
        "    y: Int32\n"
        "@dataclass\n"
        "class MaybePoint:\n"
        "    label: str\n"
        "    pos: Optional[Point]\n"
        "@dataclass\n"
        "class Listed:\n"
        "    items: list[Optional[Point]]\n"
        "@dataclass\n"
        "class Named:\n"
        "    named: dict[str, Optional[Point]]\n"
        "def main() -> None:\n"
        "    mp = MaybePoint('o', Point(0, 0))\n"
        "    print(asdict(mp))\n"
        "    print(astuple(mp))\n"
        "    li = Listed([Point(1, 2), None])\n"
        "    print(asdict(li))\n"
        "    print(astuple(li))\n"
        "    nm = Named({'a': Point(3, 4), 'b': None})\n"
        "    print(asdict(nm))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_every_new_row(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("ifexpr.storage_opt_elem", 0) == 5, faces
        assert faces.get("ifexpr.storage_opt_dict_arm", 0) == 3, faces
        assert faces.get("ifexpr.storage_opt_tuple_arm", 0) == 2, faces
        assert faces.get("print.opt_ternary_tuple_arg", 0) == 1, faces


class TestOptDictTernaryAtElementSlots:
    # The same ternary hand-written at the three element slots, without the
    # macro: a dict-literal VALUE, a list element, a dict-comp VALUE.
    SRC = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "def build(c: bool) -> None:\n"
        "    a: dict[str, Optional[dict[str, Int32]]] = "
        "{'k': ({'x': 1} if c else None)}\n"
        "    xs: list[Optional[dict[str, Int32]]] = [{'x': 1} if c else None]\n"
        "    d = {k: ({'n': 1} if c else None) for k in ['a', 'b']}\n"
        "    print(len(a), len(xs), len(d))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_the_storage_wrap(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("ifexpr.storage_opt_elem", 0) == 3, faces
        assert faces.get("ifexpr.storage_opt_dict_arm", 0) == 3, faces


class TestOptContainerInnerStaysOut:
    # BOUNDARY: the admitted inner family is dict + value tuple ONLY. A
    # list/set inner has no arm render here, so the element gate keeps
    # rejecting. (An `Optional[set]` COMP element is unreachable a second way
    # -- sema rejects a non-hashable set element -- so only the literal form
    # is pinned.)
    SRC_LIST = (
        _PRE +
        "def build(c: bool) -> None:\n"
        "    xs: list[Optional[list[Int32]]] = [[1, 2] if c else None]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )
    SRC_SET = (
        _PRE +
        "def build(c: bool) -> None:\n"
        "    xs: list[Optional[set[Int32]]] = [{1, 2} if c else None]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )

    def test_list_inner_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_LIST),
                           "body:expr.container_literal")

    def test_set_inner_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_SET),
                           "body:expr.container_literal")


class TestNonLiteralArmStaysOut:
    # BOUNDARY: the arm ladder is node-gated to None / dict literal / value
    # tuple literal. A dict NAME arm would need the pointer-local deref + move
    # mirror through the wrap; an Own-returning CALL arm the rvalue
    # materialization -- neither render exists here.
    SRC_NAME = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "def build(c: bool) -> None:\n"
        "    d = {'x': 1}\n"
        "    xs: list[Optional[dict[str, Int32]]] = [d if c else None]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )
    SRC_CALL = (
        "from typing import Optional\n"
        "from tpy import Int32, Own\n"
        "def mk() -> Own[dict[str, Int32]]:\n"
        "    return {'x': 1}\n"
        "def build(c: bool) -> None:\n"
        "    xs: list[Optional[dict[str, Int32]]] = [mk() if c else None]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )
    SRC_BOTH_DICTS = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "def build(c: bool) -> None:\n"
        "    xs: list[Optional[dict[str, Int32]]] = "
        "[{'x': 1} if c else {'y': 2}]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build(True)\n"
        "main()\n"
    )

    def test_name_arm_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_NAME), "body:expr.ifexpr")

    def test_call_arm_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_CALL), "body:expr.ifexpr")

    def test_non_optional_result_defers_byte_identical(self):
        # Both arms dict literals: the ternary's own type is the DICT, not the
        # Optional, so the storage-wrap family does not describe it at all.
        _assert_rejects_at(_reject_tally(self.SRC_BOTH_DICTS),
                           "body:expr.container_literal")


class TestViewFormStrElementStaysOut:
    # BOUNDARY: a str/bytes element whose SLOT resolved owned but whose SOURCE
    # reads as a view (a `str` PARAM) takes the `std::string(x)` element copy
    # in `_lower_container_elem`, where the AST spells the source bare inside
    # the tuple brace. Both the printed-tuple row and the ternary's own
    # value-tuple ARM exclude it; a FIELD read (whose slot IS the member's own
    # resolved type) is what the astuple expansion emits and stays admitted.
    SRC_PRINT = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "def build(s: str, v: Int32, c: bool) -> None:\n"
        "    print((s, (v, v) if c else None))\n"
        "def main() -> None:\n"
        "    build('hi', Int32(1), True)\n"
        "main()\n"
    )
    SRC_ARM = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "def build(s: str, v: Int32, c: bool) -> None:\n"
        "    xs: list[Optional[tuple[str, Int32]]] = [(s, v) if c else None]\n"
        "    print(len(xs))\n"
        "def main() -> None:\n"
        "    build('hi', Int32(1), True)\n"
        "main()\n"
    )

    def test_print_row_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_PRINT),
                           "body:stmt.expr_stmt:print.arg.tuple_tuple_literal")

    def test_tuple_arm_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC_ARM), "body:expr.ifexpr")


class TestPrintTupleRecordElemKeepsBorrowSlot:
    # The two element forms in ONE printed tuple: the record element keeps the
    # AST slot ladder's BORROW slot (`std::tuple<Box*, ...>{&(b), ...}`) while
    # the Optional ternary beside it takes the storage wrap. This arrives
    # through the pre-existing `has_pointer_repr_element()` print row, not the
    # value-tuple one -- the pin exists so a future narrowing of either row
    # cannot turn the `&(b)` borrow into a copy unnoticed.
    SRC = (
        _PRE +
        "def build(b: Box, v: Int32, c: bool) -> None:\n"
        "    print((b, (v, v) if c else None))\n"
        "def main() -> None:\n"
        "    build(Box(1), Int32(1), True)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        thir = _assert_routes_byte_identical(self.SRC)
        assert "std::tuple<Box*, std::optional<std::tuple<int32_t," in thir[1]
        assert "{&(b), ((c) ? (std::optional<" in thir[1]


class TestNestedElemTernaryStaysOut:
    # BOUNDARY: the storage wrap is granted to the IMMEDIATE element only. Here
    # the ternary sits under an `Any` element's `make_any(...)`, where the AST's
    # sticky flag still applies the wrap -- over a `const Box*` arm, which does
    # not compile. THIR's pointer render would be well-formed but would
    # byte-diverge, so the position rejects rather than pick either.
    SRC = (
        "from typing import Any\n" +
        _PRE +
        "def build(p: Optional[Box], h: Holder, c: bool) -> None:\n"
        "    d: dict[str, Any] = {'k': (p if c else h.opt)}\n"
        "    print(len(d))\n"
        "def main() -> None:\n"
        "    build(Box(3), Holder(Box(7)), True)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC), "body:expr.ifexpr")


class TestPlainPositionsKeepTheirRoute:
    # CONTROL: outside an element position the same `Optional[dict]` ternary
    # keeps whatever route it had -- the grant is threaded per CONSUMER, not
    # derived from the ternary's type, so a plain decl must not start taking
    # the storage wrap.
    SRC = (
        "from tpy import Int32\n"
        "def build(c: bool) -> Int32:\n"
        "    r = {'x': 1} if c else None\n"
        "    if r is None:\n"
        "        return 0\n"
        "    return Int32(len(r))\n"
        "def main() -> None:\n"
        "    print(build(True))\n"
        "main()\n"
    )

    def test_byte_identical(self):
        _assert_rejects_at(_reject_tally(self.SRC), "body:expr.ifexpr")

    def test_does_not_witness_the_element_wrap(self):
        _thir, faces = _lower_ctx_witnessed(self.SRC)
        assert faces.get("ifexpr.storage_opt_elem", 0) == 0, faces
