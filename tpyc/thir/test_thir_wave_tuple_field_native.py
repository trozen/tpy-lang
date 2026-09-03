"""Pins for the tuple FIELD read at a native/template callee's tuple slot:
the VALUE-tuple whole-member bare bind (`repr(self.pair)` ->
`::tpy::tuple_to_str(this->pair)`), the F3-tuple kind-blind
`tuple_to_pointer` lift off const- and non-const-rooted receivers, the
narrowed value-opt tuple field riding the same rows, and the
nested-tuple-element boundary that must keep rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = (
    "from tpy import Int32\n"
    "from dataclasses import dataclass\n"
)


class TestValueTupleFieldNativeSlot:
    def test_value_tuple_field_repr_routes(self):
        src = _HDR + (
            "class H:\n"
            "    pair: tuple[Int32, str]\n"
            "    def __init__(self, p: tuple[Int32, str]) -> None:\n"
            "        self.pair = p\n"
            "    def show(self) -> str:\n"
            "        return repr(self.pair)\n"
            "def main() -> None:\n"
            "    print(H((1, 'a')).show())\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        assert faces["arg.native_value_tuple_field"] >= 1
        assert faces["field.value_tuple"] >= 1
        _assert_byte_identical(src)

    def test_narrowed_value_opt_tuple_field_routes(self):
        # A NARROWED `tuple[..] | None` field read at the same slot: sema
        # retypes the read to the inner tuple and the value-opt field
        # machinery derefs it -- the bare-member row must compose, not
        # bypass, that render.
        src = _HDR + (
            "class H:\n"
            "    maybe: tuple[Int32, str] | None\n"
            "    def __init__(self, p: tuple[Int32, str]) -> None:\n"
            "        self.maybe = p\n"
            "    def show(self) -> str:\n"
            "        if self.maybe is not None:\n"
            "            return repr(self.maybe)\n"
            "        return 'none'\n"
            "def main() -> None:\n"
            "    print(H((5, 'z')).show())\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "ctor:ctor.mil_field.optional.name")


class TestF3TupleFieldNativeSlot:
    _REC = _HDR + (
        "@dataclass\n"
        "class Point:\n"
        "    x: Int32 = 0\n"
    )

    def test_f3_tuple_field_repr_lifts_const(self):
        # Const-rooted receiver (a @readonly __repr__): the lift spells
        # const element pointers, byte-identical to the AST's want_const
        # pair.
        src = self._REC + (
            "class H:\n"
            "    pair: tuple[Point, Int32]\n"
            "    def __init__(self, p: tuple[Point, Int32]) -> None:\n"
            "        self.pair = p\n"
            "    def show(self) -> str:\n"
            "        return repr(self.pair)\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    h = H((pt, 2))\n"
            "    print(h.show())\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "show") is not None
        assert faces["arg.borrow_tuple_field"] >= 1
        _assert_byte_identical(src)

    def test_f3_tuple_field_repr_lifts_mut(self):
        # The non-const-rooted half: the same lift off a mutating method.
        src = self._REC + (
            "class H:\n"
            "    pair: tuple[Point, Int32]\n"
            "    def __init__(self, p: tuple[Point, Int32]) -> None:\n"
            "        self.pair = p\n"
            "    def bump(self) -> str:\n"
            "        self.pair = (Point(3), 4)\n"
            "        return repr(self.pair)\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    h = H((pt, 2))\n"
            "    print(h.bump())\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "bump") is not None
        assert faces["arg.borrow_tuple_field"] >= 1
        _assert_byte_identical(src)


class TestTupleFieldNativeBoundaries:
    def test_nested_tuple_element_field_still_defers(self):
        # A nested-tuple element keeps the field outside BOTH families
        # (neither `_value_tuple` nor `_f1_tuple` admits it) -- the arg
        # gate must keep rejecting, and identity holds via fallback.
        src = _HDR + (
            "class H:\n"
            "    trip: tuple[tuple[Int32, Int32], Int32]\n"
            "    def __init__(self, p: tuple[tuple[Int32, Int32], Int32])"
            " -> None:\n"
            "        self.trip = p\n"
            "    def show(self) -> str:\n"
            "        return repr(self.trip)\n"
            "def main() -> None:\n"
            "    print(H(((1, 2), 3)).show())\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.native_arg.other")
