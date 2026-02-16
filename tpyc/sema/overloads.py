"""
TurboPython Shared Overload Resolution

Unified type matching and overload resolution used across
operators, calls, methods, and module infrastructure.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, IntLiteralType, Int32Type, FixedIntType, BigIntType, TypeParamRef,
    TypeParamKind, FunctionInfo, is_protocol_type, unwrap_readonly,
)
from ..coercions import resolve_coercion, CoercionContext

if TYPE_CHECKING:
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    DerefChecker = Callable[[TpyType], TpyType | None]


def type_matches_strict(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_checker: ProtocolChecker | None = None,
) -> bool:
    """Strict type matching: exact equality or protocol conformance.

    Used for first-pass overload resolution where no coercions are desired.
    """
    # Unwrap ReadonlyType -- readonly values can match mutable params
    # (type compatibility will catch unsafe cases separately)
    arg_inner = unwrap_readonly(arg_type)
    if arg_inner == param_type:
        return True
    if protocol_checker and is_protocol_type(param_type):
        return protocol_checker(arg_inner, param_type)
    return False


def type_matches_numeric(
    arg_type: TpyType,
    param_type: TpyType,
) -> bool:
    """Type matching for numeric operators and constructors.

    Handles IntLiteralType and TypeParamRef(INT) flexibility without
    triggering general type coercions (e.g., Int32->BigInt promotion).

    - Exact equality
    - IntLiteralType matches IntLiteralType, Int32Type, or BigIntType
    - INT TypeParamRef matches Int32Type or BigIntType
    """
    if arg_type == param_type:
        return True
    if isinstance(arg_type, IntLiteralType):
        if isinstance(param_type, FixedIntType):
            if arg_type.value is None:
                return True  # Unknown value -- can't range-check, allow match
            return param_type.min_value <= arg_type.value <= param_type.max_value
        if isinstance(param_type, (BigIntType, IntLiteralType)):
            return True
    if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
        if isinstance(param_type, (Int32Type, BigIntType)):
            return True
    return False


def type_matches_with_coercion(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
) -> bool:
    """Type matching allowing IntLiteral flexibility, protocols, and registered coercions.

    Used for overload resolution second pass and constructor matching.
    """
    arg_inner = unwrap_readonly(arg_type)
    if type_matches_numeric(arg_inner, param_type):
        return True
    if protocol_checker and is_protocol_type(param_type):
        return protocol_checker(arg_inner, param_type)
    if resolve_coercion(arg_inner, param_type, CoercionContext.ARG) is not None:
        return True
    if deref_checker:
        deref_target = deref_checker(arg_inner)
        if deref_target is not None and deref_target == param_type:
            return True
    return False


def resolve_overload(
    overloads: list[FunctionInfo],
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
) -> FunctionInfo | None:
    """Two-pass overload resolution: exact match first, then with coercions.

    Args:
        overloads: List of function overloads to match against.
        arg_types: Already-analyzed argument types.
        protocol_checker: Optional callback (arg_type, param_type) -> bool
                          for protocol conformance checking.
        deref_checker: Optional callback (type) -> deref target or None,
                       for Deref[T] coercion in overload matching.

    Returns:
        The matching FunctionInfo, or None if no match found.
    """
    # First pass: strict matching (exact types + protocols)
    for overload in overloads:
        if len(overload.params) != len(arg_types):
            continue
        if all(type_matches_strict(arg_t, ptype, protocol_checker)
               for arg_t, (_, ptype) in zip(arg_types, overload.params)):
            return overload

    # Second pass: allow coercions, prefer overload with most non-coercion
    # matches and fewest narrowing conversions (BigInt->Int32 is lossy).
    candidates: list[tuple[int, int, FunctionInfo]] = []
    for overload in overloads:
        if len(overload.params) != len(arg_types):
            continue
        if all(type_matches_with_coercion(arg_t, ptype, protocol_checker, deref_checker)
               for arg_t, (_, ptype) in zip(arg_types, overload.params)):
            score = sum(1 for arg_t, (_, ptype) in zip(arg_types, overload.params)
                        if type_matches_numeric(arg_t, ptype))
            narrowing = sum(1 for arg_t, (_, ptype) in zip(arg_types, overload.params)
                           if isinstance(arg_t, BigIntType) and isinstance(ptype, Int32Type))
            candidates.append((score, narrowing, overload))

    if candidates:
        # When return types vary across candidates, IntLiteral->FixedInt is
        # narrowing (Python's default int is BigInt, so prefer that).
        # When return types are identical (e.g. Char(97)), FixedInt is fine.
        if len(candidates) > 1 and not all(
            c[2].return_type == candidates[0][2].return_type for c in candidates[1:]
        ):
            for i, (score, narrowing, overload) in enumerate(candidates):
                extra = sum(1 for arg_t, (_, ptype) in zip(arg_types, overload.params)
                            if isinstance(ptype, FixedIntType) and isinstance(arg_t, IntLiteralType))
                candidates[i] = (score, narrowing + extra, overload)
        # Best: most numeric matches, then fewest narrowing conversions
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][2]

    return None
