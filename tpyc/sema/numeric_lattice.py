"""
Numeric inference lattice helpers.

This module centralizes numeric family/rank logic used by reassignment-based
inference so future numeric types can be added in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..typesys import (
    TpyType, IntLiteralType, FixedIntType, BigIntType, FloatType, Float32Type, BoolType,
    BIGINT, FLOAT, FLOAT32,
)


@dataclass(frozen=True)
class NumericTypeInfo:
    """Classification metadata for numeric-like types."""
    family: str  # "int_literal" | "int" | "float" | "bool"
    rank: int


def numeric_info(typ: TpyType) -> NumericTypeInfo | None:
    """Return numeric metadata for known numeric-like types."""
    if isinstance(typ, IntLiteralType):
        return NumericTypeInfo("int_literal", 0)
    if isinstance(typ, FixedIntType):
        # Rank scales with bit width: Int8=8, Int16=9, Int32=10, Int64=11
        return NumericTypeInfo("int", typ.bits // 8 + 6)
    if isinstance(typ, BigIntType):
        return NumericTypeInfo("int", 100)
    if isinstance(typ, Float32Type):
        return NumericTypeInfo("float", 190)
    if isinstance(typ, FloatType):
        return NumericTypeInfo("float", 200)
    if isinstance(typ, BoolType):
        return NumericTypeInfo("bool", 5)
    return None


def fixed_int_range_contains(typ: FixedIntType, value: int) -> bool:
    return typ.min_value <= value <= typ.max_value


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
            isinstance(existing_type, FixedIntType)
            and init_type.value is not None
            and not fixed_int_range_contains(existing_type, init_type.value)
        ):
            return BIGINT
        return existing_type
    if isinstance(init_type, FixedIntType):
        if all(fixed_int_range_contains(init_type, v) for v in literal_values):
            return init_type
        return existing_type
    if isinstance(init_type, BigIntType):
        return BIGINT
    if isinstance(init_type, Float32Type):
        return FLOAT32
    if isinstance(init_type, FloatType):
        return FLOAT
    # bool is intentionally separate and should not merge with numeric literals.
    return None


def widen_numeric_types(a: TpyType, b: TpyType) -> TpyType | None:
    """Return the widened type for two concrete numeric types, or None.

    Used by reassignment inference when a variable is assigned a different
    numeric type (e.g. Int32 then Int64 -> Int64, Int32 then float -> float).

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

    # IntLiteral is handled by merge_literal_seed_target, not here.
    if info_a.family == "int_literal" or info_b.family == "int_literal":
        return None

    # Both float-family -> widen to the higher-rank float
    if isinstance(a, (FloatType, Float32Type)) and isinstance(b, (FloatType, Float32Type)):
        if type(a) is type(b):
            return None
        # Float64 wins over Float32
        return a if isinstance(a, FloatType) else b

    # Either is float-family -> float-family wins over int
    if isinstance(a, (FloatType, Float32Type)):
        return a
    if isinstance(b, (FloatType, Float32Type)):
        return b

    # Both BigInt -> same type, no widening
    if isinstance(a, BigIntType) and isinstance(b, BigIntType):
        return None

    # Either is BigInt -> BigInt wins
    if isinstance(a, BigIntType):
        return a
    if isinstance(b, BigIntType):
        return b

    # Both FixedInt
    assert isinstance(a, FixedIntType) and isinstance(b, FixedIntType)
    if a == b:
        return None

    if a.signed == b.signed:
        return a if a.bits > b.bits else b

    # Mixed sign: allow only if the wider type is signed with strictly more bits
    wider, narrower = (a, b) if a.bits > b.bits else (b, a)
    if wider.signed and wider.bits > narrower.bits:
        return wider

    # Same width mixed sign (e.g. Int32 + UInt32) -> refuse
    return None
