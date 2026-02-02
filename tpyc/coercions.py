"""Type coercion registry for TurboPython."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType,
    RecordType, PtrType, ConstPtrType, CharType, StrType,
    SpanType, PendingListType,
)


CoercionContext = str  # "assign" | "init" | "arg" | "return"


def _match_any(_: TpyType, __: TpyType) -> bool:
    return True


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
        # IntLiteral elements coerce to Int32/BigInt
        if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (Int32Type, BigIntType)):
            return True
        return False

    # Check if actual extends NativeContiguous[T] with matching element type
    from tpyc.modules import type_extends_protocol
    # Direct element type match
    if actual_elem == expected_elem:
        return type_extends_protocol(actual, "NativeContiguous", [actual_elem])
    # Allow IntLiteral element to coerce to Int32/BigInt elements
    # Check NativeContiguous[expected_elem] since containers extend NativeContiguous with concrete types
    if isinstance(actual_elem, IntLiteralType) and isinstance(expected_elem, (Int32Type, BigIntType)):
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
    check_range: Optional[Callable[[TpyType, TpyType], bool]] = None
    codegen: Callable[[str, TpyType, TpyType, CoercionContext], str] = lambda expr, _a, _e, _c: expr


# NOTE: Order matters; higher priority first for overlapping rules.
COERCIONS: list[Coercion] = [
    # Integer coercions
    Coercion(
        name="int_literal_to_int32",
        from_type=IntLiteralType,
        to_type=Int32Type,
        check_range=lambda lit, _: -(2 ** 31) <= lit.value <= (2 ** 31 - 1),
    ),
    Coercion(
        name="int32_to_bigint",
        from_type=Int32Type,
        to_type=BigIntType,
        codegen=lambda e, _a, _b, _c: f"tpy::BigInt({e})",
    ),
    Coercion(
        name="bigint_to_int32",
        from_type=BigIntType,
        to_type=Int32Type,
        codegen=lambda e, _a, _b, _c: f"({e}).to_int32()",
    ),

    # Float coercions
    Coercion(
        name="int_literal_to_float",
        from_type=IntLiteralType,
        to_type=FloatType,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="int32_to_float",
        from_type=Int32Type,
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
        codegen=lambda e, _a, _b, _c: f"tpy::char_to_str({e})",
    ),

    # Pointer coercions
    Coercion(
        name="record_to_ptr",
        from_type=RecordType,
        to_type=PtrType,
        type_match=lambda rec, ptr: (
            isinstance(ptr, PtrType) and isinstance(ptr.pointee, RecordType) and rec.name == ptr.pointee.name
        ),
        requires_lvalue=True,
        requires_mutable=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="record_to_const_ptr",
        from_type=RecordType,
        to_type=ConstPtrType,
        type_match=lambda rec, ptr: (
            isinstance(ptr, ConstPtrType) and isinstance(ptr.pointee, RecordType) and rec.name == ptr.pointee.name
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
    Coercion(
        name="ptr_to_record",
        from_type=PtrType,
        to_type=RecordType,
        type_match=lambda ptr, rec: isinstance(ptr, PtrType) and isinstance(rec, RecordType) and ptr.pointee == rec,
        codegen=lambda e, _a, _b, _c: f"tpy::deref_ptr({e})",
    ),

    # Span coercions: any NativeContiguous[T] type can coerce to Span[T]
    # Arg context allows temporaries
    Coercion(
        name="contiguous_to_span_arg",
        from_type=TpyType,  # Matches any type; _contiguous_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_contiguous_to_span_match,
        contexts={"arg"},
    ),
    # Non-arg contexts require lvalue (can't take span of temporary)
    Coercion(
        name="contiguous_to_span",
        from_type=TpyType,  # Matches any type; _contiguous_to_span_match filters by protocol
        to_type=SpanType,
        type_match=_contiguous_to_span_match,
        contexts={"init", "assign", "return"},
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
