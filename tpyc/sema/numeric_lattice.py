"""
Numeric inference lattice helpers.

This module centralizes numeric family/rank logic used by reassignment-based
inference so future numeric types can be added in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..typesys import (
    TpyType, IntLiteralType, FixedIntType, BigIntType, FloatType, BoolType,
    BIGINT, FLOAT,
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
    if isinstance(init_type, FloatType):
        return FLOAT
    # bool is intentionally separate and should not merge with numeric literals.
    return None
