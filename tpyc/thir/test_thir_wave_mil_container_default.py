"""The container ctor call in a member-init cell: `self.items = list()`
-> `items(std::vector<T>())` and its one-argument sibling
`self.items = list(src)` -> `items(::tpy::construct<std::vector<T>>(src))`.

The gate names four callees (`list` / `dict` / `set` / `Array`); three of
them have a constructible one-argument form and are pinned below. `Array(x)`
is NOT pinned because sema rejects every one-argument spelling of it
(`Array() cannot be constructed from list[Int32]` / `from Array[Int32, 3]`,
and a bare `Iterable` source fails element inference), so the gate's `Array`
leg is reachable at the zero-argument form only.
"""

from __future__ import annotations

from .testutil import (
    _lower_ctor, _ctor_tail, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestMilContainerDefault:
    def test_list_dict_set_default_construct(self):
        src = ("from tpy import Int32\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    data: dict[str, Int32]\n"
               "    seen: set[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = list()\n"
               "        self.data = dict()\n"
               "        self.seen = set()\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        # The spelling comes from the FIELD type, not from the call.
        assert "std::vector<int32_t>()" in _ctor_tail(ctor)
        _assert_byte_identical(src)

    def test_array_default_constructs_too(self):
        src = ("from tpy import Int32, Array\n"
               "class C:\n"
               "    data: Array[Int32, 3]\n"
               "    def __init__(self) -> None:\n"
               "        self.data = Array()\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        _assert_byte_identical(src)


class TestMilContainerOneArg:
    """The single-argument form shares the field-typed target threading with
    the zero-arg one -- `gen_expr(source, fld_type)` on both paths -- so the
    argument rides the ordinary call lowering, which raises on any arg shape
    it does not mirror."""

    def test_iterable_param_constructs_the_field_container(self):
        src = ("from tpy import Int32, Own\n"
               "from typing import Iterable\n"
               "class Bag:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, src: Iterable[Own[Int32]]) -> None:\n"
               "        self.items = list(src)\n"
               "def main() -> None:\n"
               "    ns: list[Int32] = [1, 2, 3]\n"
               "    b = Bag(ns)\n"
               "    print(len(b.items))\n")
        ctor = _lower_ctor(src, "Bag")
        assert ctor is not None
        # The spelling comes from the FIELD type, not from the call.
        assert "::tpy::construct<std::vector<int32_t>>(src)" in _ctor_tail(ctor)
        _assert_routes_byte_identical(src)

    def test_set_field_and_nested_call_arg_route(self):
        src = ("from tpy import Int32\n"
               "class SetBag:\n"
               "    elems: set[Int32]\n"
               "    def __init__(self, xs: list[Int32]) -> None:\n"
               "        self.elems = set(xs)\n"
               "class Rev:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, xs: list[Int32]) -> None:\n"
               "        self.items = list(reversed(xs))\n"
               "def main() -> None:\n"
               "    ns: list[Int32] = [1, 2, 3]\n"
               "    s = SetBag(ns)\n"
               "    r = Rev(ns)\n"
               "    print(len(s.elems), len(r.items))\n")
        assert _lower_ctor(src, "SetBag") is not None
        assert _lower_ctor(src, "Rev") is not None
        _assert_routes_byte_identical(src)

    def test_dict_field_from_pair_iterable_routes(self):
        # The fourth callee name the gate admits at the one-arg form. The
        # dict spelling comes from the FIELD's key/value pair, not from the
        # argument's element type.
        src = ("from tpy import Int32\n"
               "from typing import Iterable\n"
               "class Table:\n"
               "    data: dict[Int32, Int32]\n"
               "    def __init__(self, pairs: Iterable[tuple[Int32, Int32]]) -> None:\n"
               "        self.data = dict(pairs)\n"
               "def main() -> None:\n"
               "    ps: list[tuple[Int32, Int32]] = [(1, 2), (3, 4)]\n"
               "    t = Table(ps)\n"
               "    print(len(t.data))\n")
        ctor = _lower_ctor(src, "Table")
        assert ctor is not None
        assert ("data(::tpy::dict_construct<int32_t, int32_t>(pairs))"
                in _ctor_tail(ctor))
        _assert_routes_byte_identical(src)


class TestMilContainerDefaultBoundaries:
    def test_non_container_callee_stays_ast(self):
        # BOUNDARY: the row keys on the container-ctor NAMES. `sorted()`
        # returns a fresh list through its own render, so it must keep
        # rejecting at the gate.
        src = ("from tpy import Int32\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, xs: list[Int32]) -> None:\n"
               "        self.items = sorted(xs)\n")
        assert _lower_ctor(src, "C") is None
        _assert_byte_identical(src)

    def test_own_container_param_arg_stays_ast(self):
        # BOUNDARY: the gate admits the shape, but `list(xs)` off an
        # `Own[list]` param rejects deeper in the call lowering -- the
        # ctor still falls back, byte-identically.
        src = ("from tpy import Int32, Own\n"
               "class C:\n"
               "    items: list[Int32]\n"
               "    def __init__(self, xs: Own[list[Int32]]) -> None:\n"
               "        self.items = list(xs)\n"
               "def main() -> None:\n"
               "    ns: list[Int32] = [4, 5]\n"
               "    c = C(ns)\n"
               "    print(len(c.items))\n")
        assert _lower_ctor(src, "C") is None
        _assert_byte_identical(src)
