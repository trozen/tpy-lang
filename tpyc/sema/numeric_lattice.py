"""
Numeric inference lattice helpers.

This module centralizes numeric family/rank logic used by reassignment-based
inference so future numeric types can be added in one place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, ALL_FIXED_INTS, BIGINT, FLOAT,
    is_float_type, is_integer_type,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_bool_type,
    is_float32_type, is_float64_type,
    int_traits_of,
)


# The largest finite float32 magnitude.
_FLOAT32_MAX = 3.4028234663852886e38


@dataclass(frozen=True)
class NumericTypeInfo:
    """Classification metadata for numeric-like types."""
    family: str  # "int_literal" | "float_literal" | "int" | "float" | "bool"
    rank: int


def numeric_info(typ: TpyType) -> NumericTypeInfo | None:
    """Return numeric metadata for known numeric-like types."""
    if isinstance(typ, IntLiteralType):
        return NumericTypeInfo("int_literal", 0)
    if isinstance(typ, FloatLiteralType):
        return NumericTypeInfo("float_literal", 0)
    if is_fixed_int_type(typ):
        # Rank scales with bit width: int8=8, int16=9, int32=10, int64=11
        tr = int_traits_of(typ)
        assert tr is not None, f"fixed int without IntTraits: {typ}"
        return NumericTypeInfo("int", tr.bits // 8 + 6)
    if is_big_int_type(typ):
        return NumericTypeInfo("int", 100)
    if is_float32_type(typ):
        return NumericTypeInfo("float", 190)
    if is_float64_type(typ):
        return NumericTypeInfo("float", 200)
    if is_bool_type(typ):
        return NumericTypeInfo("bool", 5)
    return None


def fixed_int_range_contains(typ: TpyType, value: int) -> bool:
    tr = int_traits_of(typ)
    assert tr is not None, f"fixed_int_range_contains called on non-fixed-int {typ}"
    return tr.min_value <= value <= tr.max_value


def widen_numeric_types(a: TpyType, b: TpyType) -> TpyType | None:
    """Return the widened type for two concrete numeric types, or None.

    Used by reassignment inference when a variable is assigned a different
    numeric type (e.g. int32 then int64 -> int64), and by the inferred joins,
    which all refuse an int meeting a float before they ask
    (`tpyc/sema/type_join.py`).

    Returns None when widening is not applicable (non-numeric, bool mixed
    with numeric, same-width mixed sign).
    """
    info_a = numeric_info(a)
    info_b = numeric_info(b)
    if info_a is None or info_b is None:
        return None

    # Bool mixed with numeric -> refuse
    if info_a.family == "bool" or info_b.family == "bool":
        return None

    # Literal families have no width to join. Defensive: callers (local_deduction, _select_join) already
    # resolve literals to concrete types before delegating; this guards
    # against future callers that forget.
    if info_a.family in ("int_literal", "float_literal"):
        return None
    if info_b.family in ("int_literal", "float_literal"):
        return None

    # Both float-family -> widen to the higher-rank float
    if is_float_type(a) and is_float_type(b):
        if a == b:
            return None
        # float64 wins over float32
        return a if is_float64_type(a) else b

    # Either is float-family -> float-family wins over int
    if is_float_type(a):
        return a
    if is_float_type(b):
        return b

    # Both BigInt -> same type, no widening
    if is_big_int_type(a) and is_big_int_type(b):
        return None

    # Either is BigInt -> BigInt wins
    if is_big_int_type(a):
        return a
    if is_big_int_type(b):
        return b

    # Both FixedInt
    a_tr = int_traits_of(a)
    b_tr = int_traits_of(b)
    assert a_tr is not None and b_tr is not None, f"expected fixed ints, got {a} and {b}"
    if a_tr == b_tr:
        return None

    if a_tr.signed == b_tr.signed:
        return a if a_tr.bits > b_tr.bits else b

    # Mixed sign: allow only if the wider type is signed with strictly more bits
    if a_tr.bits > b_tr.bits:
        wider, wider_traits, narrower_traits = a, a_tr, b_tr
    else:
        wider, wider_traits, narrower_traits = b, b_tr, a_tr
    if wider_traits.signed and wider_traits.bits > narrower_traits.bits:
        return wider

    # Same width mixed sign (e.g. int32 + uint32) -> refuse
    return None


def same_width_family(a: TpyType, b: TpyType) -> bool:
    """Whether `a` and `b` are both fixed-width ints or both floats -- the
    pairs where a wider value stored into a declared slot would narrow it.
    An `int` (BigInt) is left out: it converts into a fixed width by a
    range-checked conversion, which is no narrowing."""
    return ((is_fixed_int_type(a) and is_fixed_int_type(b))
            or (is_float_type(a) and is_float_type(b)))


def join_numeric(types: Iterable[TpyType]) -> TpyType | None:
    """The one of `types` (numbers: fixed ints, `int`, floats) that every
    other widens into by the slot relation, or None when no such member
    exists. Asked over the whole set at once, never pairwise in order:
    `(int8, uint8, int16)` joins to int16 although int8 and uint8 alone have
    no common type. The member is unique: two that held each other would
    widen both ways, which the relation never does for distinct types."""
    distinct = list(dict.fromkeys(types))
    for cand in distinct:
        if all(t == cand or widen_numeric_types(cand, t) == cand
               for t in distinct):
            return cand
    return None


def smallest_type_holding(t: TpyType, literals: Iterable[TpyType]) -> TpyType:
    """The narrowest type of `t`'s family that `t` widens into and that
    holds the value of every literal in `literals`: `t` itself when they
    all fit it, `float` for a float literal past `float32`'s range, `int`
    when no fixed width holds them. A literal of unknown value fits."""
    values = [lit.value for lit in literals
              if isinstance(lit, (IntLiteralType, FloatLiteralType))
              and lit.value is not None]
    if is_float_type(t):
        if is_float64_type(t) or all(
                math.isinf(v) or math.isnan(v) or abs(v) <= _FLOAT32_MAX
                for v in values):
            return t
        return FLOAT
    if not is_fixed_int_type(t):
        return t
    signed = int_traits_of(t).signed
    # Same-width candidates of the join's own signedness come first.
    by_width = sorted(ALL_FIXED_INTS, key=lambda c: (
        int_traits_of(c).bits, int_traits_of(c).signed != signed))
    for c in by_width:
        tr = int_traits_of(c)
        if (join_numeric((t, c)) == c
                and all(tr.min_value <= v <= tr.max_value for v in values)):
            return c
    return BIGINT


def smallest_common_int(a: TpyType, b: TpyType) -> TpyType | None:
    """The narrowest integer type both `a` and `b` widen into by the slot
    relation (`int32` and `uint32` -> `int64`; `int` when no fixed type
    holds both), for a conversion hint; None unless both are integers."""
    if not (is_integer_type(a) and is_integer_type(b)):
        return None
    if not (is_fixed_int_type(a) and is_fixed_int_type(b)):
        return BIGINT
    by_width = sorted(ALL_FIXED_INTS, key=lambda t: int_traits_of(t).bits)
    return next((c for c in by_width if join_numeric((a, b, c)) == c),
                BIGINT)
