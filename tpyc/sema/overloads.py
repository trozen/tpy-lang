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
    PendingStrType, PendingViewType, StrType, StringType, StrViewType, LiteralType,
    PendingBytesType, BytesType, ByteArrayType, BytesViewType,
    NominalType, PtrType, OwnType, CallableType, is_fn_type, VoidType, NoneType,
    OptionalType, BoolType,
    unwrap_ref_type,
    is_callable_type, is_float_type, is_integer_type, is_any_float_type,

)
from ..coercions import resolve_coercion, CoercionContext

if TYPE_CHECKING:
    ProtocolChecker = Callable[[TpyType, TpyType], bool]
    DerefChecker = Callable[[TpyType], TpyType | None]
    SubclassChecker = Callable[['NominalType', 'NominalType'], bool]


def _contains_type_param_ref(t: TpyType) -> bool:
    """Check if a type contains any TypeParamRef."""
    if isinstance(t, TypeParamRef):
        return True
    return any(_contains_type_param_ref(inner) for inner in t.inner_types())


def _structural_match(arg: TpyType, param: TpyType) -> bool:
    """Structural match with TypeParamRef as wildcard."""
    if isinstance(param, TypeParamRef):
        return True
    # Unwrap Ref, Own, Readonly from both sides (transparent for matching)
    from ..typesys import ReadonlyType, OwnType
    arg = unwrap_ref_type(arg)
    param = unwrap_ref_type(param)
    if isinstance(param, TypeParamRef):
        return True
    if isinstance(arg, OwnType):
        arg = arg.wrapped
    if isinstance(param, ReadonlyType):
        return _structural_match(unwrap_readonly(arg), param.wrapped)
    arg = unwrap_readonly(arg)
    # Callable -> Fn: structurally compatible callable types
    if isinstance(arg, CallableType) and is_fn_type(param):
        if len(arg.param_types) != len(param.param_types):
            return False
        return (all(_structural_match(a, p) for a, p in zip(arg.param_types, param.param_types))
                and _structural_match(arg.return_type, param.return_type))
    # None literal (NoneType) matches None annotation (VoidType)
    if isinstance(arg, NoneType) and isinstance(param, VoidType):
        return True
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
    # Unwrap ReadonlyType, OwnType, RefType -- these are ownership/ref
    # qualifiers transparent for overload matching.
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    if isinstance(param_inner, OwnType):
        param_inner = param_inner.wrapped
    if arg_inner == param_inner:
        return True
    # FnType/CallableType: compare with qualifier unwrapping on inner types.
    # The arg FnType may have Own/Ref on param/return types from FI, while the
    # resolved overload's Fn type has bare types from substitution.
    from ..typesys import CallableType, OwnType as _Own
    if (is_callable_type(arg_inner)
            and is_callable_type(param_inner)
            and len(arg_inner.param_types) == len(param_inner.param_types)):
        def _strip(t: TpyType) -> TpyType:
            t = unwrap_ref_type(t)
            if isinstance(t, _Own): t = t.wrapped
            if isinstance(t, PendingViewType): t = t.family.owned_type
            return t
        if (all(_strip(a) == _strip(p) for a, p in zip(arg_inner.param_types, param_inner.param_types))
                and _strip(arg_inner.return_type) == _strip(param_inner.return_type)):
            return True
    # IntLiteralType matches the specific FixedIntType it was inferred to (from
    # generic resolution). This allows resolved-generic overloads like
    # range(stop: Int32) to match IntLiteralType(5) in the first pass.
    if isinstance(arg_inner, IntLiteralType) and isinstance(param_inner, FixedIntType):
        if arg_inner.value is None or param_inner.min_value <= arg_inner.value <= param_inner.max_value:
            return True
    # None literal (NoneType) matches None type annotation (VoidType)
    if isinstance(arg_inner, NoneType) and isinstance(param_inner, VoidType):
        return True
    # T -> Optional[T] and None -> Optional[T]
    if isinstance(param_inner, OptionalType):
        if isinstance(arg_inner, NoneType):
            return True
        return type_matches_strict(arg_inner, param_inner.inner, protocol_checker)
    # Callable -> Fn: std::function satisfies template requires clauses
    if (isinstance(arg_inner, CallableType) and is_fn_type(param_inner)
            and arg_inner.param_types == param_inner.param_types
            and (arg_inner.return_type == param_inner.return_type
                 or isinstance(param_inner.return_type, VoidType))):
        return True
    # PendingViewType: same-family pending types match each other and their resolved types
    if isinstance(arg_inner, PendingViewType) and isinstance(param_inner, PendingViewType):
        if arg_inner.family is param_inner.family:
            return True
    if isinstance(arg_inner, PendingStrType) and isinstance(param_inner, StrType):
        return True
    # Single-value LiteralType matches multi-value LiteralType if value is in the set.
    # Only in strict pass -- LiteralType -> base type is deferred to coercion pass
    # so that Literal stubs are preferred over plain stubs regardless of order.
    if isinstance(arg_inner, LiteralType) and isinstance(param_inner, LiteralType):
        return all(v in param_inner.values for v in arg_inner.values)
    # IntLiteralType matches LiteralType with int base if value is in the set.
    if isinstance(arg_inner, IntLiteralType) and isinstance(param_inner, LiteralType) and param_inner.is_int_base():
        if arg_inner.value is not None:
            return param_inner.contains("int", arg_inner.value)
        return False
    # PendingBytesType (unresolved bytes local) matches bytes params
    if isinstance(arg_inner, PendingBytesType) and isinstance(param_inner, BytesType):
        return True
    if protocol_checker and is_protocol_type(param_inner):
        # Unwrap Own[T] from arg -- copy()-wrapped return values should still match
        # plain protocol params (Own is a caller-side ownership marker, not a new type).
        check_arg = arg_inner.wrapped if isinstance(arg_inner, OwnType) else arg_inner
        # Strip Own[T] wrappers from protocol type args so that e.g.
        # list[Node] matches Iterable[Own[Node]] for protocol conformance.
        check_param = param_inner
        if (isinstance(param_inner, NominalType) and param_inner.type_args
                and any(isinstance(a, OwnType) for a in param_inner.type_args)):
            stripped = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_inner.type_args)
            check_param = dc_replace(param_inner, type_args=stripped)
        return protocol_checker(check_arg, check_param)
    # Non-protocol NominalType with Own[T] in type args: strip Own for overload matching.
    # Applies to any concrete container (e.g. dict[K, Own[V]]) not just DictType.
    # Copy warnings are emitted later by check_type_compatible.
    if (isinstance(param_inner, NominalType) and not is_protocol_type(param_inner)
            and any(isinstance(a, OwnType) for a in param_inner.inner_types())):
        stripped_inner = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_inner.inner_types())
        stripped_param = param_inner.with_inner_types(stripped_inner)
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
        if is_float_type(param_type):
            return True
    if isinstance(arg_type, FloatLiteralType):
        if is_any_float_type(param_type):
            return True
    if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
        if is_integer_type(param_type):
            return True
    # T -> Optional[T]: unwrap Optional param and match inner type
    if isinstance(param_type, OptionalType):
        return type_matches_numeric(arg_type, param_type.inner)
    # Recursive container matching: e.g. make_list(IntLiteralType) vs make_list(Int32)
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
    # Unwrap ReadonlyType, OwnType, RefType from args -- mutable values match
    # readonly params, Own[T] variables match T params, and Ref[T] is transparent.
    arg_inner = unwrap_ref_type(unwrap_readonly(arg_type))
    if isinstance(arg_inner, OwnType):
        arg_inner = arg_inner.wrapped
    param_inner = unwrap_ref_type(unwrap_readonly(param_type))
    # Own[T] param accepts T values (ownership marker, not a distinct type)
    if isinstance(param_inner, OwnType):
        param_inner = param_inner.wrapped
    if type_matches_numeric(arg_inner, param_inner):
        return True
    # None -> Optional[T]
    if isinstance(arg_inner, NoneType) and isinstance(param_inner, OptionalType):
        return True
    # PendingStrType matches any string type (str, String, StrView)
    if isinstance(arg_inner, PendingStrType) and isinstance(param_inner, (StrType, StringType, StrViewType)):
        return True
    # Single-value LiteralType matches multi-value LiteralType if value is in the set
    if isinstance(arg_inner, LiteralType) and isinstance(param_inner, LiteralType):
        return all(v in param_inner.values for v in arg_inner.values)
    # LiteralType falls back to matching its base type
    if isinstance(arg_inner, LiteralType):
        if arg_inner.is_str_base() and isinstance(param_inner, (StrType, StringType, StrViewType)):
            return True
        if arg_inner.is_int_base() and is_integer_type(param_inner):
            return True
        if arg_inner.is_bool_base() and isinstance(param_inner, BoolType):
            return True
    # IntLiteralType matches LiteralType with int base if value is in the set
    if isinstance(arg_inner, IntLiteralType) and isinstance(param_inner, LiteralType) and param_inner.is_int_base():
        if arg_inner.value is not None:
            return param_inner.contains("int", arg_inner.value)
        return False
    # PendingBytesType matches any bytes type (bytes, bytearray, BytesView)
    if isinstance(arg_inner, PendingBytesType) and isinstance(param_inner, (BytesType, ByteArrayType, BytesViewType)):
        return True
    if protocol_checker and is_protocol_type(param_inner):
        # Unwrap Own[T] -- ownership marker, not a distinct type.
        # Mirrors the unwrapping in type_matches_strict.
        check_arg = arg_inner.wrapped if isinstance(arg_inner, OwnType) else arg_inner
        return protocol_checker(check_arg, param_inner)
    # Callable -> Fn: std::function satisfies template requires clauses
    if (isinstance(arg_inner, CallableType) and is_fn_type(param_inner)
            and arg_inner.param_types == param_inner.param_types
            and (arg_inner.return_type == param_inner.return_type
                 or isinstance(param_inner.return_type, VoidType))):
        return True
    if resolve_coercion(arg_inner, param_inner, CoercionContext.ARG) is not None:
        return True
    if deref_checker:
        deref_target = deref_checker(arg_inner)
        if deref_target is not None and deref_target == param_inner:
            return True
    # Inheritance: Child -> Parent (value upcast and pointer coercions)
    if subclass_checker:
        if isinstance(arg_inner, NominalType) and arg_inner.is_user_record:
            if isinstance(param_inner, NominalType) and param_inner.is_user_record:
                if subclass_checker(arg_inner, param_inner):
                    return True
            pointee = getattr(param_inner, 'pointee', None)
            if isinstance(pointee, NominalType) and pointee.is_user_record:
                if subclass_checker(arg_inner, pointee):
                    return True
        if isinstance(arg_inner, PtrType) and isinstance(arg_inner.pointee, NominalType):
            if isinstance(param_inner, PtrType) and isinstance(param_inner.pointee, NominalType):
                # Ptr[readonly[T]] cannot coerce to mutable Ptr (would drop const)
                if not (arg_inner.is_readonly and not param_inner.is_readonly):
                    if subclass_checker(arg_inner.pointee, param_inner.pointee):
                        return True
    # Non-protocol NominalType with Own[T] in type args: strip Own for overload matching.
    # Symmetric with the same block in type_matches_strict.
    if (isinstance(param_inner, NominalType) and not is_protocol_type(param_inner)
            and any(isinstance(a, OwnType) for a in param_inner.inner_types())):
        stripped_inner = tuple(a.wrapped if isinstance(a, OwnType) else a for a in param_inner.inner_types())
        stripped_param = param_inner.with_inner_types(stripped_inner)
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
    # Overloads with unresolved TypeParamRef in params use structural matching
    # (TypeParamRef as wildcard); resolved generics use type_matches_strict.
    for overload in overloads:
        if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
            continue
        has_tpr = overload.is_generic() and any(
            _contains_type_param_ref(p.type) for p in overload.params)
        if has_tpr:
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
                           if isinstance(arg_t, BigIntType) and isinstance(unwrap_ref_type(ptype), Int32Type))
            candidates.append((score, narrowing, overload))

    if candidates:
        if default_int_type is None:
            default_int_type = BIGINT

        def _int_literal_penalty(arg_t: TpyType, ptype: TpyType) -> int:
            if not isinstance(arg_t, IntLiteralType):
                return 0
            ptype = unwrap_ref_type(ptype)
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
