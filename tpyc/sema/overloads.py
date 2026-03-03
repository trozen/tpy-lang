"""
TurboPython Shared Overload Resolution

Unified type matching and overload resolution used across
operators, calls, methods, and module infrastructure.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, IntLiteralType, Int32Type, FixedIntType, BigIntType, BIGINT,
    TypeParamRef, TypeParamKind, FunctionInfo, is_protocol_type, unwrap_readonly,
    PendingStrType, StrType, StringType, StrViewType,
    NamedType, PtrType,
)
from ..coercions import resolve_coercion, CoercionContext

if TYPE_CHECKING:
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    DerefChecker = Callable[[TpyType], TpyType | None]
    SubclassChecker = Callable[['NamedType', 'NamedType'], bool]


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
    # PendingStrType (unresolved str local) matches str params
    if isinstance(arg_inner, PendingStrType) and isinstance(param_type, StrType):
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
    # Recursive container matching: e.g. ListType(IntLiteralType) vs ListType(Int32)
    if type(arg_type) == type(param_type):
        arg_elem = arg_type.get_element_type()
        param_elem = param_type.get_element_type()
        if arg_elem is not None and param_elem is not None:
            return type_matches_numeric(arg_elem, param_elem)
    return False


def type_matches_with_coercion(
    arg_type: TpyType,
    param_type: TpyType,
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
    subclass_checker: SubclassChecker | None = None,
) -> bool:
    """Type matching allowing IntLiteral flexibility, protocols, and registered coercions.

    Used for overload resolution second pass and constructor matching.
    """
    arg_inner = unwrap_readonly(arg_type)
    if type_matches_numeric(arg_inner, param_type):
        return True
    # PendingStrType matches any string type (str, String, StrView)
    if isinstance(arg_inner, PendingStrType) and isinstance(param_type, (StrType, StringType, StrViewType)):
        return True
    if protocol_checker and is_protocol_type(param_type):
        return protocol_checker(arg_inner, param_type)
    if resolve_coercion(arg_inner, param_type, CoercionContext.ARG) is not None:
        return True
    if deref_checker:
        deref_target = deref_checker(arg_inner)
        if deref_target is not None and deref_target == param_type:
            return True
    # Inheritance: Child -> Parent (value upcast and pointer coercions)
    if subclass_checker:
        if isinstance(arg_inner, NamedType) and arg_inner.is_user_record:
            if isinstance(param_type, NamedType) and param_type.is_user_record:
                if subclass_checker(arg_inner, param_type):
                    return True
            pointee = getattr(param_type, 'pointee', None)
            if isinstance(pointee, NamedType) and pointee.is_user_record:
                if subclass_checker(arg_inner, pointee):
                    return True
        if isinstance(arg_inner, PtrType) and isinstance(arg_inner.pointee, NamedType):
            if isinstance(param_type, PtrType) and isinstance(param_type.pointee, NamedType):
                # ReadOnlyPtr cannot coerce to mutable Ptr (would drop const)
                if not (arg_inner.is_readonly and not param_type.is_readonly):
                    if subclass_checker(arg_inner.pointee, param_type.pointee):
                        return True
    return False


def resolve_overload(
    overloads: list[FunctionInfo],
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
    default_int_type: TpyType | None = None,
    subclass_checker: SubclassChecker | None = None,
) -> FunctionInfo | None:
    """Two-pass overload resolution: exact match first, then with coercions.

    Args:
        overloads: List of function overloads to match against.
        arg_types: Already-analyzed argument types.
        protocol_checker: Optional callback (arg_type, param_type) -> bool
                          for protocol conformance checking.
        deref_checker: Optional callback (type) -> deref target or None,
                       for Deref[T] coercion in overload matching.
        subclass_checker: Optional callback (child, parent) -> bool
                          for inheritance-based upcast matching.

    Returns:
        The matching FunctionInfo, or None if no match found.
    """
    # First pass: strict matching (exact types + protocols)
    for overload in overloads:
        if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
            continue
        if all(type_matches_strict(arg_t, ptype, protocol_checker)
               for arg_t, (_, ptype) in zip(arg_types, overload.params)):
            return overload

    # Second pass: allow coercions, prefer overload with most non-coercion
    # matches and fewest narrowing conversions (BigInt->Int32 is lossy).
    candidates: list[tuple[int, int, FunctionInfo]] = []
    for overload in overloads:
        if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
            continue
        if all(type_matches_with_coercion(arg_t, ptype, protocol_checker, deref_checker, subclass_checker)
               for arg_t, (_, ptype) in zip(arg_types, overload.params)):
            score = sum(1 for arg_t, (_, ptype) in zip(arg_types, overload.params)
                        if type_matches_numeric(arg_t, ptype))
            narrowing = sum(1 for arg_t, (_, ptype) in zip(arg_types, overload.params)
                           if isinstance(arg_t, BigIntType) and isinstance(ptype, Int32Type))
            candidates.append((score, narrowing, overload))

    if candidates:
        if default_int_type is None:
            default_int_type = BIGINT

        def _int_literal_penalty(arg_t: TpyType, ptype: TpyType) -> int:
            if not isinstance(arg_t, IntLiteralType):
                return 0
            # Prefer the configured default integer type for integer literals.
            if ptype == default_int_type:
                return 0
            if isinstance(default_int_type, FixedIntType):
                if isinstance(ptype, FixedIntType):
                    # Keep preference stable around configured width/signedness.
                    width_gap = abs(ptype.bits - default_int_type.bits) // 8
                    sign_penalty = 1 if ptype.signed != default_int_type.signed else 0
                    return 1 + width_gap + sign_penalty
                if isinstance(ptype, BigIntType):
                    return 8
            if isinstance(default_int_type, BigIntType):
                if isinstance(ptype, FixedIntType):
                    return 2
            return 1

        # Apply literal penalty to break ties deterministically.
        # Covers both same-return (e.g. range(IntLiteral) over many fixed-int
        # overloads) and different-return cases (IntLiteral->FixedInt narrowing).
        if len(candidates) > 1:
            for i, (score, narrowing, overload) in enumerate(candidates):
                extra = sum(
                    _int_literal_penalty(arg_t, ptype)
                    for arg_t, (_, ptype) in zip(arg_types, overload.params)
                )
                candidates[i] = (score, narrowing + extra, overload)
        # Best: most numeric matches, then fewest narrowing conversions
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][2]

    return None
