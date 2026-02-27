"""Type coercion registry for TurboPython."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType,
    NamedType, PtrType, ConstPtrType, CharType, StrType, StringType, StrViewType,
    SpanType, PendingListType, TypeParamRef, TypeParamKind,
)


class CoercionContext(Enum):
    """Context in which a type coercion is being applied."""
    ASSIGN = "assign"
    INIT = "init"
    ARG = "arg"
    RETURN = "return"


def _match_any(_: TpyType, __: TpyType) -> bool:
    return True


def _int_type_param_match(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual is an INT TypeParamRef."""
    return isinstance(actual, TypeParamRef) and actual.kind == TypeParamKind.INT


def _is_safe_widening(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual FixedIntType can safely widen to expected FixedIntType."""
    if not isinstance(actual, FixedIntType) or not isinstance(expected, FixedIntType):
        return False
    if actual == expected:
        return False
    if actual.signed == expected.signed:
        return actual.bits < expected.bits
    # Unsigned -> signed: need strictly more bits (e.g. UInt8 -> Int16)
    if not actual.signed and expected.signed:
        return actual.bits < expected.bits
    return False


def _contiguous_to_span_match(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual type (extending NativeContiguous[T]) can coerce to Span[T]."""
    if not isinstance(expected, SpanType):
        return False
    actual_elem = actual.get_element_type()
    if actual_elem is None:
        return False
    expected_elem = expected.element_type

    # PendingListType is an internal compiler type that resolves to list (which extends NativeContiguous).
    # Handle it directly since it's not in the module system.
    if isinstance(actual, PendingListType):
        if actual_elem == expected_elem:
            return True
        # IntLiteral elements coerce to any FixedIntType or BigInt
        if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (FixedIntType, BigIntType)):
            return True
        return False

    # Check if actual extends NativeContiguous[T] with matching element type
    from tpyc.modules import type_extends_protocol
    # Direct element type match
    if actual_elem == expected_elem:
        return type_extends_protocol(actual, "NativeContiguous", [actual_elem])
    # Allow IntLiteral element to coerce to FixedInt/BigInt elements
    # Check NativeContiguous[expected_elem] since containers extend NativeContiguous with concrete types
    if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (FixedIntType, BigIntType)):
        return type_extends_protocol(actual, "NativeContiguous", [expected_elem])
    return False


@dataclass(frozen=True)
class Coercion:
    """A type coercion rule."""
    name: str
    from_type: type[TpyType]
    to_type: type[TpyType]
    type_match: Callable[[TpyType, TpyType], bool] = _match_any
    contexts: Optional[set[CoercionContext]] = None
    requires_lvalue: bool = False
    requires_mutable: bool = False
    forbid_return_local: bool = False
    # Lossless coercion safe for protocol return type matching.
    protocol_safe: bool = False
    check_range: Optional[Callable[[TpyType, TpyType], bool]] = None
    codegen: Callable[[str, TpyType, TpyType, CoercionContext], str] = lambda expr, _a, _e, _c: expr


# NOTE: Order matters; higher priority first for overlapping rules.
COERCIONS: list[Coercion] = [
    # INT type parameter coercions (compile-time constants)
    Coercion(
        name="int_type_param_to_fixed_int",
        from_type=TypeParamRef,
        to_type=FixedIntType,
        type_match=_int_type_param_match,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),
    Coercion(
        name="int_type_param_to_bigint",
        from_type=TypeParamRef,
        to_type=BigIntType,
        type_match=_int_type_param_match,
        codegen=lambda e, _a, _b, _c: f"tpy::BigInt(static_cast<int64_t>({e}))",
    ),

    # Integer literal to any fixed-width integer (range-checked)
    Coercion(
        name="int_literal_to_fixed_int",
        from_type=IntLiteralType,
        to_type=FixedIntType,
        check_range=lambda lit, target: lit.value is None or (target.min_value <= lit.value <= target.max_value),
    ),

    # Widening between fixed-width integers (e.g. Int8 -> Int32, UInt8 -> Int16)
    Coercion(
        name="fixed_int_widening",
        from_type=FixedIntType,
        to_type=FixedIntType,
        type_match=_is_safe_widening,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),

    # Fixed-width integer to BigInt
    Coercion(
        name="fixed_int_to_bigint",
        from_type=FixedIntType,
        to_type=BigIntType,
        codegen=lambda e, _a, _b, _c: f"tpy::BigInt({e})",
    ),
    # BigInt to fixed-width integer (narrowing, runtime checked)
    Coercion(
        name="bigint_to_fixed_int",
        from_type=BigIntType,
        to_type=FixedIntType,
        codegen=lambda e, _a, b, _c: f"({e}).to_fixed_check<{b.to_cpp()}>()",
    ),

    # Float coercions
    Coercion(
        name="int_literal_to_float",
        from_type=IntLiteralType,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="fixed_int_to_float",
        from_type=FixedIntType,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="bigint_to_float",
        from_type=BigIntType,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),

    # Char to str coercion
    Coercion(
        name="char_to_str",
        from_type=CharType,
        to_type=StrType,
        codegen=lambda e, _a, _b, _c: f"std::string(tpy::char_to_str({e}))",
    ),
    # Char to String coercion
    Coercion(
        name="char_to_string",
        from_type=CharType,
        to_type=StringType,
        codegen=lambda e, _a, _b, _c: f"std::string(1, {e})",
    ),
    # Char to StrView coercion
    Coercion(
        name="char_to_strview",
        from_type=CharType,
        to_type=StrViewType,
        codegen=lambda e, _a, _b, _c: f"tpy::char_to_str({e})",
    ),

    # String <-> StrType identity coercions (both map to std::string)
    Coercion(
        name="string_to_str",
        from_type=StringType,
        to_type=StrType,
    ),
    Coercion(
        name="str_to_string",
        from_type=StrType,
        to_type=StringType,
    ),

    # String -> StrView (safe implicit, C++ handles std::string -> string_view)
    Coercion(
        name="string_to_strview",
        from_type=StringType,
        to_type=StrViewType,
    ),
    # StrType -> StrView (safe implicit, C++ handles std::string -> string_view)
    Coercion(
        name="str_to_strview",
        from_type=StrType,
        to_type=StrViewType,
    ),

    # StrView -> String (allocates)
    Coercion(
        name="strview_to_string",
        from_type=StrViewType,
        to_type=StringType,
        codegen=lambda e, _a, _b, _c: f"std::string({e})",
        protocol_safe=True,
    ),
    # StrView -> StrType (allocates -- StrType is now std::string)
    Coercion(
        name="strview_to_str",
        from_type=StrViewType,
        to_type=StrType,
        codegen=lambda e, _a, _b, _c: f"std::string({e})",
        protocol_safe=True,
    ),

    # Pointer coercions
    Coercion(
        name="record_to_ptr",
        from_type=NamedType,
        to_type=PtrType,
        type_match=lambda rec, ptr: (
            rec.is_user_record and isinstance(ptr, PtrType) and isinstance(ptr.pointee, NamedType) and ptr.pointee.is_user_record and rec.name == ptr.pointee.name
        ),
        requires_lvalue=True,
        requires_mutable=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="record_to_const_ptr",
        from_type=NamedType,
        to_type=ConstPtrType,
        type_match=lambda rec, ptr: (
            rec.is_user_record and isinstance(ptr, ConstPtrType) and isinstance(ptr.pointee, NamedType) and ptr.pointee.is_user_record and rec.name == ptr.pointee.name
        ),
        requires_lvalue=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="ptr_to_const_ptr",
        from_type=PtrType,
        to_type=ConstPtrType,
        type_match=lambda p1, p2: isinstance(p1, PtrType) and isinstance(p2, ConstPtrType) and p1.pointee == p2.pointee,
    ),
    # Span coercions: any NativeContiguous[T] type can coerce to Span[T]
    # Arg context allows temporaries
    Coercion(
        name="contiguous_to_span_arg",
        from_type=TpyType,  # Matches any type; _contiguous_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_contiguous_to_span_match,
        contexts={CoercionContext.ARG},
    ),
    # Non-arg contexts require lvalue (can't take span of temporary)
    Coercion(
        name="contiguous_to_span",
        from_type=TpyType,  # Matches any type; _contiguous_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_contiguous_to_span_match,
        contexts={CoercionContext.INIT, CoercionContext.ASSIGN, CoercionContext.RETURN},
        requires_lvalue=True,
        forbid_return_local=True,
    ),
]


def resolve_coercion(actual: TpyType, expected: TpyType, ctx: CoercionContext) -> Optional[Coercion]:
    """Find a coercion rule that converts actual to expected in the given context."""
    for coercion in COERCIONS:
        if coercion.contexts is not None and ctx not in coercion.contexts:
            continue
        if isinstance(actual, coercion.from_type) and isinstance(expected, coercion.to_type):
            if coercion.type_match(actual, expected):
                return coercion
    return None


def is_protocol_safe_coercion(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual can coerce to expected in a protocol return type context.

    Only lossless coercions marked protocol_safe=True are considered.
    """
    for coercion in COERCIONS:
        if not coercion.protocol_safe:
            continue
        if isinstance(actual, coercion.from_type) and isinstance(expected, coercion.to_type):
            if coercion.type_match(actual, expected):
                return True
    return False


def _deref_codegen(e: str, actual: TpyType, _expected: TpyType, _ctx: str) -> str:
    if isinstance(actual, (PtrType, ConstPtrType)):
        return f"tpy::deref_check({e})"
    return f"{e}.__deref__()"


DEREF_COERCION = Coercion(
    name="deref_to_target",
    from_type=TpyType,
    to_type=TpyType,
    codegen=_deref_codegen,
)

# Pre-built coercions for inheritance-based upcasts (not in COERCIONS list --
# requires TypeRegistry access that type_match lambdas don't have).
# Used directly by compatibility.py.
UPCAST_TO_PTR = Coercion(
    name="upcast_to_ptr",
    from_type=NamedType,
    to_type=PtrType,
    requires_lvalue=True,
    requires_mutable=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)

UPCAST_TO_CONST_PTR = Coercion(
    name="upcast_to_const_ptr",
    from_type=NamedType,
    to_type=ConstPtrType,
    requires_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)
