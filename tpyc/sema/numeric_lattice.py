"""
Numeric inference lattice helpers.

This module centralizes numeric family/rank logic used by reassignment-based
inference so future numeric types (Int64/UInt32/Float32/Float64) can be added
in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..typesys import (
    TpyType, IntLiteralType, Int32Type, BigIntType, FloatType, BoolType,
    INT32, BIGINT, FLOAT,
)


@dataclass(frozen=True)
class NumericTypeInfo:
    """Classification metadata for numeric-like types."""
    family: str  # "int_literal" | "int" | "float" | "bool"
    # Reserved for future generalized widening decisions across additional
    # numeric families (Int64/UInt32/Float32/etc.).
    rank: int


def numeric_info(typ: TpyType) -> NumericTypeInfo | None:
    """Return numeric metadata for known numeric-like types."""
    if isinstance(typ, IntLiteralType):
        return NumericTypeInfo("int_literal", 0)
    if isinstance(typ, Int32Type):
        return NumericTypeInfo("int", 10)
    if isinstance(typ, BigIntType):
        return NumericTypeInfo("int", 100)
    if isinstance(typ, FloatType):
        return NumericTypeInfo("float", 200)
    if isinstance(typ, BoolType):
        return NumericTypeInfo("bool", 5)
    return None


def int32_range_contains(value: int) -> bool:
    return -(2 ** 31) <= value <= (2 ** 31 - 1)


def merge_literal_seed_target(init_type: TpyType, literal_values: list[int]) -> TpyType | None:
    """Infer target type for a literal-seeded variable.

    A literal-seeded variable starts as BigInt (`x = 0`) and may be narrowed
    by later writes if safe.
    """
    info = numeric_info(init_type)
    if info is None:
        return None
    if info.family == "int_literal":
        return BIGINT
    if isinstance(init_type, Int32Type):
        if all(int32_range_contains(v) for v in literal_values):
            return INT32
        return BIGINT
    if isinstance(init_type, BigIntType):
        return BIGINT
    if isinstance(init_type, FloatType):
        return FLOAT
    # bool is intentionally separate and should not merge with numeric literals.
    return None
