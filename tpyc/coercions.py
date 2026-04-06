"""Type coercion registry for TurboPython."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type,
    FloatLiteralType,
    NamedType, PtrType, is_readonly_ptr, CharType, StrType, StringType, StrViewType,
    BytesType, ByteArrayType, BytesViewType,
    SpanType, is_readonly_span, PendingListType, TypeParamRef, TypeParamKind, ReadonlyType,
    ListType, ArrayType, BasicSliceType, SliceType,
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


def _spanlike_to_span_match(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual type (extending ReadOnlySpanLike[T]) can coerce to Span[T]/ReadOnlySpan[T]."""
    if not isinstance(expected, SpanType):
        return False
    # Span[readonly[T]] and readonly[container] cannot coerce to mutable Span (const violation)
    if isinstance(actual, SpanType) and actual.is_readonly and not expected.is_readonly:
        return False
    if isinstance(actual, ReadonlyType) and not expected.is_readonly:
        return False
    actual_elem = actual.get_element_type()
    if actual_elem is None:
        return False
    expected_elem = expected.inner_element_type

    # PendingListType is an internal compiler type that resolves to list (which extends ReadOnlySpanLike).
    # Handle it directly since it's not in the module system.
    if isinstance(actual, PendingListType):
        if actual_elem == expected_elem:
            return True
        # IntLiteral elements coerce to any FixedIntType or BigInt
        if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (FixedIntType, BigIntType)):
            return True
        return False

    # Check if actual is a builtin type that implements ReadOnlySpanLike[T].
    # These are compiler-internal types with known span coercion support.
    if not isinstance(actual, (ListType, ArrayType, SpanType)):
        return False
    if actual_elem == expected_elem:
        return True
    if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (FixedIntType, BigIntType)):
        return True
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
    requires_mutable_lvalue: bool = False
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
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt(static_cast<int64_t>({e}))",
    ),

    # Integer literal to any fixed-width integer (range-checked)
    Coercion(
        name="int_literal_to_fixed_int",
        from_type=IntLiteralType,
        to_type=FixedIntType,
        check_range=lambda lit, target: lit.value is None or (target.min_value <= lit.value <= target.max_value),
    ),

    # Integer literal to BigInt (always valid)
    Coercion(
        name="int_literal_to_bigint",
        from_type=IntLiteralType,
        to_type=BigIntType,
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
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt({e})",
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

    # Float literal coercions (adapts to context)
    Coercion(
        name="float_literal_to_float",
        from_type=FloatLiteralType,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: e,
    ),
    Coercion(
        name="float_literal_to_float32",
        from_type=FloatLiteralType,
        to_type=Float32Type,
        codegen=lambda e, _a, _b, _c: e,  # identity: gen_expr provides 'f' suffix
    ),

    # Float32 coercions (widening to Float32)
    Coercion(
        name="int_literal_to_float32",
        from_type=IntLiteralType,
        to_type=Float32Type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="fixed_int_to_float32",
        from_type=FixedIntType,
        to_type=Float32Type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="bigint_to_float32",
        from_type=BigIntType,
        to_type=Float32Type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    # Float32 -> float (widening, lossless)
    Coercion(
        name="float32_to_float",
        from_type=Float32Type,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    # float -> Float32 (narrowing, but allowed for convenience -- matches C++ behavior)
    Coercion(
        name="float_to_float32",
        from_type=FloatType,
        to_type=Float32Type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),

    # Char to str coercion
    Coercion(
        name="char_to_str",
        from_type=CharType,
        to_type=StrType,
        codegen=lambda e, _a, _b, _c: f"std::string(::tpy::char_to_str({e}))",
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
        codegen=lambda e, _a, _b, _c: f"::tpy::char_to_str({e})",
    ),

    # String <-> StrType identity coercions (both map to std::string)
    Coercion(
        name="string_to_str",
        from_type=StringType,
        to_type=StrType,
        protocol_safe=True,
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

    # bytearray <-> bytes identity coercions (both map to std::vector<uint8_t>)
    Coercion(
        name="bytearray_to_bytes",
        from_type=ByteArrayType,
        to_type=BytesType,
        protocol_safe=True,
    ),
    Coercion(
        name="bytes_to_bytearray",
        from_type=BytesType,
        to_type=ByteArrayType,
    ),

    # bytes/bytearray -> BytesView (safe implicit, vector -> span)
    Coercion(
        name="bytes_to_bytesview",
        from_type=BytesType,
        to_type=BytesViewType,
    ),
    Coercion(
        name="bytearray_to_bytesview",
        from_type=ByteArrayType,
        to_type=BytesViewType,
    ),

    # BytesView -> bytes/bytearray (allocates)
    Coercion(
        name="bytesview_to_bytes",
        from_type=BytesViewType,
        to_type=BytesType,
        codegen=lambda e, _a, _b, _c: f"std::vector<uint8_t>({e}.begin(), {e}.end())",
        protocol_safe=True,
    ),
    Coercion(
        name="bytesview_to_bytearray",
        from_type=BytesViewType,
        to_type=ByteArrayType,
        codegen=lambda e, _a, _b, _c: f"std::vector<uint8_t>({e}.begin(), {e}.end())",
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
        requires_mutable_lvalue=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="record_to_const_ptr",
        from_type=NamedType,
        to_type=PtrType,
        type_match=lambda rec, ptr: (
            rec.is_user_record and is_readonly_ptr(ptr) and isinstance(ptr.inner_pointee, NamedType) and ptr.inner_pointee.is_user_record and rec.name == ptr.inner_pointee.name
        ),
        requires_lvalue=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="ptr_to_const_ptr",
        from_type=PtrType,
        to_type=PtrType,
        type_match=lambda p1, p2: isinstance(p1, PtrType) and not p1.is_readonly and is_readonly_ptr(p2) and p1.inner_pointee == p2.inner_pointee,
    ),
    Coercion(
        name="span_to_readonly_span",
        from_type=SpanType,
        to_type=SpanType,
        type_match=lambda s1, s2: isinstance(s1, SpanType) and not s1.is_readonly and is_readonly_span(s2) and s1.inner_element_type == s2.inner_element_type,
        protocol_safe=True,
    ),
    # basic_slice -> slice (adds step=nullopt). C++ implicit via Slice(BasicSlice) ctor.
    Coercion(
        name="basic_slice_to_slice",
        from_type=BasicSliceType,
        to_type=SliceType,
    ),
    # Span coercions: any ReadOnlySpanLike[T] type can coerce to Span[T]
    # Arg context allows temporaries
    Coercion(
        name="spanlike_to_span_arg",
        from_type=TpyType,  # Matches any type; _spanlike_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_spanlike_to_span_match,
        contexts={CoercionContext.ARG},
    ),
    # Non-arg contexts require lvalue (can't take span of temporary)
    Coercion(
        name="spanlike_to_span",
        from_type=TpyType,  # Matches any type; _spanlike_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_spanlike_to_span_match,
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
    if isinstance(actual, PtrType):
        return f"::tpy::deref_check({e})"
    return f"{e}.__deref__()"


DEREF_COERCION = Coercion(
    name="deref_to_target",
    from_type=TpyType,
    to_type=TpyType,
    codegen=_deref_codegen,
)

# @value_ptr_coercion: T -> Ptr[T] coercion for any type (not in COERCIONS list).
# Applied explicitly by calls.py for functions with @value_ptr_coercion.
VALUE_TO_PTR = Coercion(
    name="value_to_ptr",
    from_type=TpyType,
    to_type=PtrType,
    requires_lvalue=True,
    requires_mutable_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)

# Pre-built coercions for inheritance-based upcasts (not in COERCIONS list --
# requires TypeRegistry access that type_match lambdas don't have).
# Used directly by compatibility.py.
UPCAST_TO_PTR = Coercion(
    name="upcast_to_ptr",
    from_type=NamedType,
    to_type=PtrType,
    requires_lvalue=True,
    requires_mutable_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)

# Pre-built coercions for __span__() and ReadOnlySpanLike[T] protocol coercion to ReadOnlySpan.
# Used directly by compatibility.py (not in COERCIONS list); from_type is not consulted.
# Codegen is handled in expressions.py _gen_span_coercion.
# Arg context: temporaries allowed.
SPAN_METHOD_TO_SPAN_ARG = Coercion(
    name="span_method_to_span_arg",
    from_type=TpyType,
    to_type=SpanType,
)

# Non-arg contexts: lvalue required, no returning locals.
SPAN_METHOD_TO_SPAN = Coercion(
    name="span_method_to_span",
    from_type=TpyType,
    to_type=SpanType,
    requires_lvalue=True,
    forbid_return_local=True,
)

UPCAST_TO_CONST_PTR = Coercion(
    name="upcast_to_const_ptr",
    from_type=NamedType,
    to_type=PtrType,
    requires_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)
