"""
Numeric inference lattice helpers.

This module centralizes numeric family/rank logic used by reassignment-based
inference so future numeric types can be added in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType,
    BIGINT, FLOAT, FLOAT32,
    is_float_type,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_bool_type,
    is_float32_type, is_float64_type,
    int_traits_of,
)


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


def merge_literal_seed_target(
    existing_type: TpyType,
    init_type: TpyType,
    literal_values: list[int],
) -> TpyType | None:
    """Infer target type for a literal-seeded variable.

    A literal-seeded variable starts as ctx.default_int_type (`x = 0`) and may
    be refined by later writes if safe.
    """
    if isinstance(init_type, IntLiteralType):
        if (
            is_fixed_int_type(existing_type)
            and init_type.value is not None
            and not fixed_int_range_contains(existing_type, init_type.value)
        ):
            return BIGINT
        return existing_type
    if is_fixed_int_type(init_type):
        if all(fixed_int_range_contains(init_type, v) for v in literal_values):
            return init_type
        return existing_type
    if is_big_int_type(init_type):
        return BIGINT
    if is_float32_type(init_type):
        return FLOAT32
    if is_float64_type(init_type):
        return FLOAT
    # bool is intentionally separate and should not merge with numeric literals.
    return None


def widen_numeric_types(a: TpyType, b: TpyType) -> TpyType | None:
    """Return the widened type for two concrete numeric types, or None.

    Used by reassignment inference when a variable is assigned a different
    numeric type (e.g. int32 then int64 -> int64, int32 then float -> float).

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

    # Literal families are handled by merge_literal_seed_target, not here.
    # Defensive: callers (local_deduction, _select_join) already
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
