"""
TurboPython Shared Overload Resolution

Unified type matching and overload resolution used across
operators, calls, methods, and module infrastructure.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, Int32Type, FixedIntType, BigIntType, BIGINT,
    FloatType, Float32Type,
    TypeParamRef, TypeParamKind, FunctionInfo, is_protocol_type, unwrap_readonly,
    PendingStrType, StrType, StringType, StrViewType,
    NamedType, PtrType, OwnType,
)
from ..coercions import resolve_coercion, CoercionContext

if TYPE_CHECKING:
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    DerefChecker = Callable[[TpyType], TpyType | None]
    SubclassChecker = Callable[['NamedType', 'NamedType'], bool]


def _contains_type_param_ref(t: TpyType) -> bool:
    """Check if a type contains any TypeParamRef."""
    if isinstance(t, TypeParamRef):
        return True
    return any(_contains_type_param_ref(inner) for inner in t.inner_types())


def _structural_match(arg: TpyType, param: TpyType) -> bool:
    """Structural match with TypeParamRef as wildcard."""
    if isinstance(param, TypeParamRef):
        return True
    # Unwrap ReadonlyType from both sides (readonly param accepts mutable arg)
    from ..typesys import ReadonlyType
    if isinstance(param, ReadonlyType):
        return _structural_match(unwrap_readonly(arg), param.wrapped)
    arg = unwrap_readonly(arg)
    if type(arg) != type(param):
        return False
    # PtrType: readonly arg cannot match mutable param (would drop const)
    if isinstance(arg, PtrType) and isinstance(param, PtrType):
        if arg.is_readonly and not param.is_readonly:
            return False
    arg_inners = list(arg.inner_types())
    param_inners = list(param.inner_types())
    if len(arg_inners) != len(param_inners):
        return False
    if not arg_inners:
        return arg == param
    return all(_structural_match(a, p) for a, p in zip(arg_inners, param_inners))


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
        # Unwrap Own[T] from arg -- copy()-wrapped return values should still match
        # plain protocol params (Own is a caller-side ownership marker, not a new type).
        check_arg = arg_inner.wrapped if isinstance(arg_inner, OwnType) else arg_inner
        # Strip Own[T] wrappers from protocol type args so that e.g.
        # list[Node] matches Iterable[Own[Node]] for protocol conformance.
        check_param = param_type
        if (isinstance(param_type, NamedType) and param_type.type_args
                and any(isinstance(a, OwnType) for a in param_type.type_args)):
            stripped = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_type.type_args)
            check_param = dc_replace(param_type, type_args=stripped)
        return protocol_checker(check_arg, check_param)
    # Non-protocol NamedType with Own[T] in type args: strip Own for overload matching.
    # Applies to any concrete container (e.g. dict[K, Own[V]]) not just DictType.
    # Copy warnings are emitted later by check_type_compatible.
    if (isinstance(param_type, NamedType) and not is_protocol_type(param_type)
            and any(isinstance(a, OwnType) for a in param_type.inner_types())):
        stripped_inner = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_type.inner_types())
        stripped_param = param_type.with_inner_types(stripped_inner)
        return arg_inner == stripped_param
    return False


def type_matches_numeric(
    arg_type: TpyType,
    param_type: TpyType,
) -> bool:
    """Type matching for numeric operators and constructors.

    Handles IntLiteralType and TypeParamRef(INT) flexibility without
    triggering general type coercions (e.g., Int32->BigInt promotion).

    - Exact equality
    - IntLiteralType matches IntLiteralType, Int32Type, BigIntType, FloatType, or Float32Type
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
        if isinstance(param_type, (FloatType, Float32Type)):
            return True
    if isinstance(arg_type, FloatLiteralType):
        if isinstance(param_type, (FloatType, Float32Type, FloatLiteralType)):
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
                # Ptr[readonly[T]] cannot coerce to mutable Ptr (would drop const)
                if not (arg_inner.is_readonly and not param_type.is_readonly):
                    if subclass_checker(arg_inner.pointee, param_type.pointee):
                        return True
    # Non-protocol NamedType with Own[T] in type args: strip Own for overload matching.
    # Symmetric with the same block in type_matches_strict.
    if (isinstance(param_type, NamedType) and not is_protocol_type(param_type)
            and any(isinstance(a, OwnType) for a in param_type.inner_types())):
        stripped_inner = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_type.inner_types())
        stripped_param = param_type.with_inner_types(stripped_inner)
        return arg_inner == stripped_param
    return False


def resolve_overload(
    overloads: list[FunctionInfo],
    arg_types: list[TpyType],
    protocol_checker: ProtocolChecker | None = None,
    deref_checker: DerefChecker | None = None,
    default_int_type: TpyType | None = None,
    subclass_checker: SubclassChecker | None = None,
    is_readonly_receiver: bool | None = None,
    is_consuming_receiver: bool | None = None,
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
        is_readonly_receiver: If set, pre-filter auto_readonly clones --
                              True prefers readonly overloads, False prefers mutable.
        is_consuming_receiver: If set, pre-filter consuming vs borrowing overloads --
                               True prefers is_consuming overloads, False prefers non-consuming.

    Returns:
        The matching FunctionInfo, or None if no match found.
    """
    # Pre-filter consuming vs borrowing overloads by receiver ownership.
    # Must run BEFORE readonly filter: consuming overloads are non-readonly,
    # so the readonly filter would drop the borrowing (readonly) overload
    # and leave only the consuming one for mutable receivers.
    if is_consuming_receiver is not None and any(m.is_consuming for m in overloads):
        if is_consuming_receiver:
            consuming = [m for m in overloads if m.is_consuming]
            if consuming:
                overloads = consuming
        else:
            borrowing = [m for m in overloads if not m.is_consuming]
            if borrowing:
                overloads = borrowing

    # Pre-filter auto_readonly clones by receiver constness
    if is_readonly_receiver is not None:
        if is_readonly_receiver:
            ro = [m for m in overloads if m.is_readonly]
            if ro:
                overloads = ro
        else:
            mut = [m for m in overloads if not m.is_readonly]
            if mut:
                overloads = mut

    # First pass: strict matching (exact types + protocols).
    # Generic overloads use structural matching (TypeParamRef as wildcard);
    # actual type param inference happens later in _analyze_generic_function_call.
    for overload in overloads:
        if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
            continue
        if overload.is_generic():
            if all(_structural_match(arg_t, ptype)
                   for arg_t, (_, ptype) in zip(arg_types, overload.params)):
                return overload
        elif all(type_matches_strict(arg_t, ptype, protocol_checker)
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
