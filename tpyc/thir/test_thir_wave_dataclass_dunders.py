"""Pins for the dataclass dunder-pair rows: the fstring
wrap-table readonly-TypeParamRef row, the protocol-slot field-arg rows
(optional / record / container members bare at repr), and the bare
field-eq pairs (optional-str, optional-record, value-element list/set) --
plus the non-F1 and dict boundaries."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_DC = (
    "from dataclasses import dataclass\n"
    "from typing import Optional\n"
    "from tpy import Int32\n"
)


class TestReprFieldArgs:
    def test_optional_str_field_repr_routes(self):
        src = _DC + (
            "@dataclass\n"
            "class Cfg:\n"
            "    label: Optional[str] = None\n"
            "def main() -> None:\n"
            "    print(Cfg('a'))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "__repr__") is not None
        assert faces["arg.native_protocol_field"] >= 1
        assert faces["field.whole_optional"] >= 1
        _assert_byte_identical(src)

    def test_generic_record_field_str_routes(self):
        # The readonly-wrapped TypeParamRef wrap-table row: a const method's
        # `self.value: T` read arrives readonly-wrapped.
        src = (
            "from tpy import Int32, Stringable\n"
            "class Pair:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def __str__(self) -> str:\n"
            "        return f'({self.x})'\n"
            "class Wrapper[T: Stringable]:\n"
            "    value: T\n"
            "    def __init__(self, value: T) -> None:\n"
            "        self.value = value\n"
            "    def __str__(self) -> str:\n"
            "        return f'W({self.value})'\n"
            "def main() -> None:\n"
            "    wp: Wrapper[Pair] = Wrapper(Pair(1))\n"
            "    print(str(wp))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert len([f for f in thir.functions if f.name == "__str__"]) == 2
        assert faces["fstr.user_arg"] >= 1
        _assert_byte_identical(src)

    def test_generic_record_field_repr_routes(self):
        # The dataclass_field_generic shape: a synthesized __repr__ over a
        # generic-record field renders `::tpy::repr_of(this->pair)` bare.
        src = _DC + (
            "class Pair[T]:\n"
            "    first: T\n"
            "    def __init__(self, first: T = T()) -> None:\n"
            "        self.first = first\n"
            "@dataclass\n"
            "class Wrapper:\n"
            "    name: str\n"
            "    pair: Pair[Int32]\n"
            "def main() -> None:\n"
            "    print(Wrapper('a', Pair(1)))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "__repr__") is not None
        assert faces["arg.native_protocol_field"] >= 1
        _assert_byte_identical(src)


class TestBareFieldEqPairs:
    def test_optional_str_field_pair_routes(self):
        src = _DC + (
            "@dataclass\n"
            "class Cfg:\n"
            "    name: str\n"
            "    label: Optional[str] = None\n"
            "def main() -> None:\n"
            "    print(Cfg('a') == Cfg('a'))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "__eq__") is not None
        assert faces["binop.opt_scalar_eq"] >= 1
        _assert_byte_identical(src)

    def test_optional_record_field_pair_routes(self):
        src = _DC + (
            "@dataclass\n"
            "class Inner:\n"
            "    x: Int32\n"
            "@dataclass\n"
            "class Outer:\n"
            "    inner: Inner | None = None\n"
            "def main() -> None:\n"
            "    print(Outer(Inner(1)) == Outer(Inner(1)))\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "__eq__"]) == 2
        _assert_byte_identical(src)

    def test_list_and_set_field_pairs_route(self):
        src = _DC + (
            "from dataclasses import field\n"
            "@dataclass\n"
            "class Bag:\n"
            "    items: list[Int32] = field(default_factory=list)\n"
            "    names: set[Int32] = field(default_factory=set)\n"
            "def main() -> None:\n"
            "    print(Bag([1], {2}) == Bag([1], {2}))\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "__eq__") is not None
        _assert_byte_identical(src)

    def test_dict_field_pair_routes(self):
        # CONVERTED FENCE (the container compare-pair row): dict members
        # now ride `_container_compare_pair` -- the container's own
        # operator, bare renders.
        src = _DC + (
            "from dataclasses import field\n"
            "@dataclass\n"
            "class Env:\n"
            "    vars: dict[str, Int32] = field(default_factory=dict)\n"
            "def main() -> None:\n"
            "    print(Env({'a': 1}) == Env({'a': 1}))\n"
        )
        assert _fn(_lower_ctx(src), "__eq__") is not None
        _assert_byte_identical(src)

    def test_record_element_list_pair_routes_preexisting(self):
        # Record-element list eq turned out to route via a pre-existing
        # compare arm (this pin documents the discovery -- both records'
        # __eq__ route); the byte-diff is the guard that matters.
        src = _DC + (
            "from dataclasses import field\n"
            "@dataclass\n"
            "class P:\n"
            "    x: Int32\n"
            "@dataclass\n"
            "class Poly:\n"
            "    pts: list[P] = field(default_factory=list)\n"
            "def main() -> None:\n"
            "    print(Poly([P(1)]) == Poly([P(1)]))\n"
        )
        thir = _lower_ctx(src)
        assert len([f for f in thir.functions if f.name == "__eq__"]) == 2
        _assert_byte_identical(src)


class TestReprFieldBoundaries:
    def test_non_f1_record_field_stays_ast(self):
        # A generic-record field whose type-arg is a UNION is outside the
        # byte-identical F1 slice (union type-args spell alias names on the
        # resolver side) -- the repr field row must not take it.
        src = _DC + (
            "class Holder[T]:\n"
            "    item: T\n"
            "    def __init__(self, item: T) -> None:\n"
            "        self.item = item\n"
            "class Box:\n"
            "    h: Holder[Int32 | str]\n"
            "    def __init__(self, h: Holder[Int32 | str]) -> None:\n"
            "        self.h = h\n"
            "    def show(self) -> str:\n"
            "        return repr(self.h)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.native_arg.record_nonf1")


class TestDictFieldRepr:
    # The dict flavor of the native-protocol field-arg row
    # (`repr(self.lookup)` -> `::tpy::dict_to_str(this->lookup)`): the
    # whole-member read passes bare; `_value_elem_container` gains dict.
    def test_dict_field_repr_routes(self):
        src = _DC + (
            "from dataclasses import field\n"
            "@dataclass\n"
            "class Env:\n"
            "    vars: dict[str, Int32] = field(default_factory=dict)\n"
            "def main() -> None:\n"
            "    print(repr(Env({'a': 1})))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "__repr__") is not None
        assert faces["arg.native_protocol_field"] >= 1
        _assert_byte_identical(src)


class TestRecordFieldReprAndPrint:
    # The last two dataclass_field rows: a concrete F1-record field at a
    # still-PROTOCOL template slot (`repr(self.origin)` -- the repr fi's
    # param stays Representable, so the exact-match rows never fire), and
    # the F1-record FIELD print arg (RAW stream over the bare member).
    SRC = _DC + (
        "@dataclass\n"
        "class Point:\n"
        "    x: Int32 = 0\n"
        "@dataclass\n"
        "class Canvas:\n"
        "    name: str\n"
        "    origin: Point\n"
        "def main() -> None:\n"
        "    cv = Canvas('m', Point(3))\n"
        "    print(cv.origin)\n"
        "    print(repr(cv))\n"
    )

    def test_routes_byte_identical(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert _fn(thir, "__repr__") is not None
        assert faces["arg.protocol_record_field"] >= 1
        assert faces["print.record_field"] >= 1
        _assert_byte_identical(self.SRC)
