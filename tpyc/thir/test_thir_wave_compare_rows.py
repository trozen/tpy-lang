"""Pins for the two compare rows at the binop terminal: the mixed-sign
fixed-int `::std::cmp_*` slice (target-less, non-literal operands; the
spelling table is IMPORTED from codegen's _CMP_HELPER so the two paths
cannot drift) and the pointer-repr tuple FIELD compare pair
(`this->pair == other.pair` -> `::tpy::tuple_eq(bare, bare)`).
Boundaries: a literal-side mixed compare and the field-vs-name pair keep
their own renders/rejects."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_INT = "from tpy import Int32, UInt32, Int64\n"

_REC = (
    "from tpy import Int32\n"
    "from dataclasses import dataclass\n"
    "@dataclass\n"
    "class Point:\n"
    "    x: Int32 = 0\n"
    "class H:\n"
    "    pair: tuple[Point, Int32]\n"
    "    def __init__(self, p: tuple[Point, Int32]) -> None:\n"
    "        self.pair = p\n"
)


class TestMixedSignCompare:
    def test_cmp_helper_slice_routes(self):
        src = _INT + (
            "def lt(a: Int32, b: UInt32) -> bool:\n"
            "    return a < b\n"
            "def ge(a: Int64, b: UInt32) -> bool:\n"
            "    return a >= b\n"
            "def eq(a: Int32, b: UInt32) -> bool:\n"
            "    return a == b\n"
            "def main() -> None:\n"
            "    print(lt(-1, 5), ge(10, 5), eq(3, 3))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "lt") is not None
        assert _fn(thir, "ge") is not None
        assert faces["binop.mixed_sign_cmp"] >= 3
        _assert_byte_identical(src)

    def test_narrowed_opt_operand_still_defers(self):
        # THE DELTA-REVIEW CRITICAL's pin: a PROVEN-narrowed value-opt
        # scalar operand reads its inner fixed int here, but the AST's raw
        # resolved type is still the Optional -- its comparison target
        # suppresses cmp_* there (the bare `((*a) < b)` render), so the
        # row must keep rejecting (identity via fallback). Without the
        # narrowed-unwrap guard this emitted cmp_less((*a), b): a byte
        # divergence AND a different boolean for negative values.
        src = (
            "from tpy import Int32, UInt64\n"
            "def f(a: Int32 | None, b: UInt64) -> bool:\n"
            "    if a is not None:\n"
            "        return a < b\n"
            "    return False\n"
            "def main() -> None:\n"
            "    print(f(3, 5))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is None
        assert not faces.get("binop.mixed_sign_cmp")
        _assert_byte_identical(src)

    def test_narrowed_opt_eq_still_defers(self):
        # The `==` flavor of the narrowed guard: the optional_safe_eq /
        # narrowed machinery owns the render, never the cmp_* row.
        src = (
            "from tpy import Int32, UInt32\n"
            "def f(a: Int32 | None, b: UInt32) -> bool:\n"
            "    if a is not None:\n"
            "        return a == b\n"
            "    return False\n"
            "def main() -> None:\n"
            "    print(f(3, 5))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("binop.mixed_sign_cmp")
        _assert_byte_identical(src)

    def test_unproven_opt_ordering_still_defers(self):
        # The unproven value-opt ordering operand unwraps through
        # deref_optional_check on the AST -- the cmp_* row must not fire
        # on the unwrapped inner.
        src = (
            "from tpy import Int32, UInt32\n"
            "def f(a: Int32 | None, b: UInt32) -> bool:\n"
            "    return a < b\n"
            "def main() -> None:\n"
            "    print(f(3, 5))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("binop.mixed_sign_cmp")
        _assert_byte_identical(src)

    def test_literal_side_keeps_plain_render(self):
        # A literal side folds cleanly on the AST path (no cmp_*); the
        # row must not take it -- either the plain render or a reject,
        # never the helper.
        src = _INT + (
            "def lt0(u: UInt32) -> bool:\n"
            "    return u < 1\n"
            "def main() -> None:\n"
            "    print(lt0(3))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("binop.mixed_sign_cmp")
        _assert_byte_identical(src)


class TestPtrTupleFieldCompare:
    def test_field_pair_eq_ne_route(self):
        src = _REC + (
            "def eq(h1: H, h2: H) -> bool:\n"
            "    return h1.pair == h2.pair\n"
            "def ne(h1: H, h2: H) -> bool:\n"
            "    return h1.pair != h2.pair\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    h1 = H((pt, 2))\n"
            "    h2 = H((pt, 2))\n"
            "    print(eq(h1, h2), ne(h1, h2))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "eq") is not None
        assert _fn(thir, "ne") is not None
        assert faces["binop.tuple_field_compare"] >= 2
        _assert_byte_identical(src)

    def test_property_tuple_pair_still_defers(self):
        # A @property read is a call in disguise -- the field-pair
        # predicate excludes it, whatever the tuple type.
        src = _REC + (
            "class P:\n"
            "    _p: tuple[Point, Int32]\n"
            "    def __init__(self, p: tuple[Point, Int32]) -> None:\n"
            "        self._p = p\n"
            "    @property\n"
            "    def pair(self) -> tuple[Point, Int32]:\n"
            "        return self._p\n"
            "def eq(a: P, b: P) -> bool:\n"
            "    return a.pair == b.pair\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    print(eq(P((pt, 2)), P((pt, 2))))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("binop.tuple_field_compare")
        _assert_byte_identical(src)

    def test_narrowed_optional_field_pair_still_defers(self):
        # A field DECLARED `tuple[..] | None` narrowed non-None reads with
        # an unwrap the bare-member pair does not mirror -- the
        # declared-vs-analyzed equality keeps it out.
        src = _REC + (
            "class N:\n"
            "    maybe: tuple[Point, Int32] | None\n"
            "    def __init__(self, p: tuple[Point, Int32]) -> None:\n"
            "        self.maybe = p\n"
            "def eq(a: N, b: N) -> bool:\n"
            "    if a.maybe is not None and b.maybe is not None:\n"
            "        return a.maybe == b.maybe\n"
            "    return False\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    print(eq(N((pt, 2)), N((pt, 2))))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("binop.tuple_field_compare")
        _assert_byte_identical(src)

    def test_field_vs_name_pair_still_defers(self):
        # A mixed field-vs-name operand pair sits outside BOTH pair
        # predicates (the name read carries a form conversion the pair
        # does not mirror) -- must keep rejecting.
        src = _REC + (
            "def mixed(h1: H, p: tuple[Point, Int32]) -> bool:\n"
            "    return h1.pair == p\n"
            "def main() -> None:\n"
            "    pt = Point(1)\n"
            "    h1 = H((pt, 2))\n"
            "    print(mixed(h1, h1.pair))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "mixed") is None
        assert not faces.get("binop.tuple_field_compare")
        _assert_byte_identical(src)
