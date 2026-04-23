"""Type coercion registry for TurboPython."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .typesys import (
    TpyType, IntLiteralType, FloatLiteralType,
    NominalType, PtrType, OptionalType, OwnType, is_readonly_ptr,
    is_readonly_span, PendingListType, TypeParamRef, TypeParamKind, ReadonlyType,
    is_integer_type, unwrap_readonly,
)
from .type_def_registry import (
    is_array, is_span, is_list, int_traits_of,
    is_fixed_int_type, is_big_int_type, is_float64_type, is_float32_type,
    is_char_type, is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytearray_type, is_bytes_view_type,
    is_basic_slice_type, is_slice_type,
)


class CoercionContext(Enum):
    """Context in which a type coercion is being applied."""
    ASSIGN = "assign"
    INIT = "init"
    ARG = "arg"
    RETURN = "return"


# A type-side matcher: predicate that identifies the actual/expected side.
# Used by `Coercion.from_type` / `Coercion.to_type` as a coarse pre-filter;
# finer discrimination (e.g. element equality, readonly-ness) belongs in
# `Coercion.type_match`.
TypeMatcher = Callable[[TpyType], bool]


def _is(cls: type) -> TypeMatcher:
    """Build a TypeMatcher that checks isinstance against a class."""
    return lambda t: isinstance(t, cls)


def _match_any_side(_: TpyType) -> bool:
    return True


def _match_any(_: TpyType, __: TpyType) -> bool:
    return True


def _int_type_param_match(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual is an INT TypeParamRef."""
    return isinstance(actual, TypeParamRef) and actual.kind == TypeParamKind.INT


def _int_literal_fits_fixed_int(lit: TpyType, target: TpyType) -> bool:
    """check_range for int_literal_to_fixed_int: literal value (if known) must
    fit in the target fixed-int's range."""
    if not isinstance(lit, IntLiteralType):
        return False
    tr = int_traits_of(target)
    if tr is None:
        return False
    return lit.value is None or (tr.min_value <= lit.value <= tr.max_value)


def _is_safe_widening(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual fixed-int can safely widen to expected fixed-int."""
    a = int_traits_of(actual)
    b = int_traits_of(expected)
    if a is None or b is None:
        return False
    if a == b:
        return False
    if a.signed == b.signed:
        return a.bits < b.bits
    # Unsigned -> signed: need strictly more bits (e.g. UInt8 -> Int16)
    if not a.signed and b.signed:
        return a.bits < b.bits
    return False


def _spanlike_to_span_match(actual: TpyType, expected: TpyType) -> bool:
    """Check if actual type (extending Spannable[T]) can coerce to Span[T]/ReadOnlySpan[T]."""
    if not is_span(expected):
        return False
    # Span[readonly[T]] and readonly[container] cannot coerce to mutable Span (const violation)
    expected_is_readonly = is_readonly_span(expected)
    if is_span(actual) and is_readonly_span(actual) and not expected_is_readonly:
        return False
    if isinstance(actual, ReadonlyType) and not expected_is_readonly:
        return False
    actual_elem = actual.get_element_type()
    if actual_elem is None:
        return False
    expected_elem = unwrap_readonly(expected.type_args[0])

    # PendingListType is an internal compiler type that resolves to list (which extends Spannable).
    # Handle it directly since it's not in the module system.
    if isinstance(actual, PendingListType):
        if actual_elem == expected_elem:
            return True
        # IntLiteral elements coerce to any fixed-width int or BigInt
        if isinstance(actual_elem, IntLiteralType) and is_integer_type(expected_elem):
            return True
        return False

    # Check if actual is a builtin type that implements Spannable[T].
    # These are compiler-internal types with known span coercion support.
    if not (is_array(actual) or is_list(actual)):
        return False
    if actual_elem == expected_elem:
        return True
    if isinstance(actual_elem, IntLiteralType) and is_integer_type(expected_elem):
        return True
    return False


# Convenience matchers for the Ptr-shape rules (kept readable as helpers).
def _is_user_record(t: TpyType) -> bool:
    return isinstance(t, NominalType) and t.is_user_record


def _is_mutable_ptr(t: TpyType) -> bool:
    return isinstance(t, PtrType) and not is_readonly_ptr(t)


def _is_readonly_ptr_match(t: TpyType) -> bool:
    return isinstance(t, PtrType) and is_readonly_ptr(t)


@dataclass(frozen=True)
class Coercion:
    """A type coercion rule.

    `from_type` / `to_type` are predicates on a single type (coarse pre-filter).
    `type_match` is an optional finer two-sided predicate.
    """
    name: str
    from_type: TypeMatcher
    to_type: TypeMatcher
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
        from_type=_is(TypeParamRef),
        to_type=is_fixed_int_type,
        type_match=_int_type_param_match,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),
    Coercion(
        name="int_type_param_to_bigint",
        from_type=_is(TypeParamRef),
        to_type=is_big_int_type,
        type_match=_int_type_param_match,
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt(static_cast<int64_t>({e}))",
    ),

    # Integer literal to any fixed-width integer (range-checked)
    Coercion(
        name="int_literal_to_fixed_int",
        from_type=_is(IntLiteralType),
        to_type=is_fixed_int_type,
        check_range=_int_literal_fits_fixed_int,
    ),

    # Integer literal to BigInt (always valid)
    Coercion(
        name="int_literal_to_bigint",
        from_type=_is(IntLiteralType),
        to_type=is_big_int_type,
    ),

    # Widening between fixed-width integers (e.g. Int8 -> Int32, UInt8 -> Int16)
    Coercion(
        name="fixed_int_widening",
        from_type=is_fixed_int_type,
        to_type=is_fixed_int_type,
        type_match=_is_safe_widening,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),

    # Fixed-width integer to BigInt
    Coercion(
        name="fixed_int_to_bigint",
        from_type=is_fixed_int_type,
        to_type=is_big_int_type,
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt({e})",
    ),
    # BigInt to fixed-width integer (narrowing, runtime checked)
    Coercion(
        name="bigint_to_fixed_int",
        from_type=is_big_int_type,
        to_type=is_fixed_int_type,
        codegen=lambda e, _a, b, _c: f"({e}).to_fixed_check<{b.to_cpp()}>()",
    ),

    # Float coercions
    Coercion(
        name="int_literal_to_float",
        from_type=_is(IntLiteralType),
        to_type=is_float64_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="fixed_int_to_float",
        from_type=is_fixed_int_type,
        to_type=is_float64_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="bigint_to_float",
        from_type=is_big_int_type,
        to_type=is_float64_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),

    # Float literal coercions (adapts to context)
    Coercion(
        name="float_literal_to_float",
        from_type=_is(FloatLiteralType),
        to_type=is_float64_type,
        codegen=lambda e, _a, _b, _c: e,
    ),
    Coercion(
        name="float_literal_to_float32",
        from_type=_is(FloatLiteralType),
        to_type=is_float32_type,
        codegen=lambda e, _a, _b, _c: e,  # identity: gen_expr provides 'f' suffix
    ),

    # Float32 coercions (widening to Float32)
    Coercion(
        name="int_literal_to_float32",
        from_type=_is(IntLiteralType),
        to_type=is_float32_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="fixed_int_to_float32",
        from_type=is_fixed_int_type,
        to_type=is_float32_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="bigint_to_float32",
        from_type=is_big_int_type,
        to_type=is_float32_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    # Float32 -> float (widening, lossless)
    Coercion(
        name="float32_to_float",
        from_type=is_float32_type,
        to_type=is_float64_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    # float -> Float32 (narrowing, but allowed for convenience -- matches C++ behavior)
    Coercion(
        name="float_to_float32",
        from_type=is_float64_type,
        to_type=is_float32_type,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),

    # Char to str coercion
    Coercion(
        name="char_to_str",
        from_type=is_char_type,
        to_type=is_str_type,
        codegen=lambda e, _a, _b, _c: f"std::string(::tpy::char_to_str({e}))",
    ),
    # Char to String coercion
    Coercion(
        name="char_to_string",
        from_type=is_char_type,
        to_type=is_string_type,
        codegen=lambda e, _a, _b, _c: f"std::string(1, {e})",
    ),
    # Char to StrView coercion
    Coercion(
        name="char_to_strview",
        from_type=is_char_type,
        to_type=is_str_view_type,
        codegen=lambda e, _a, _b, _c: f"::tpy::char_to_str({e})",
    ),

    # String <-> str (both lower to std::string in non-ARG contexts, but `str`
    # params lower to std::string_view at ARG). string -> str is always safe
    # (std::string implicitly converts to std::string_view). str -> String
    # materializes at ARG (where str is string_view and String is const
    # std::string& / std::string) and is identity elsewhere (both std::string).
    # The ARG wrap skips plain string literals -- `"foo"` is const char* which
    # binds to const std::string& directly, so `std::string("foo")` is only a
    # cosmetic change. Non-literal sources (names, calls, slices) need the wrap.
    Coercion(
        name="string_to_str",
        from_type=is_string_type,
        to_type=is_str_type,
        protocol_safe=True,
    ),
    Coercion(
        name="str_to_string",
        from_type=is_str_type,
        to_type=is_string_type,
        codegen=lambda e, _a, _b, c: (
            e if c != CoercionContext.ARG or (e.startswith('"') and e.endswith('"'))
            else f"std::string({e})"
        ),
    ),

    # String -> StrView (safe implicit, C++ handles std::string -> string_view)
    Coercion(
        name="string_to_strview",
        from_type=is_string_type,
        to_type=is_str_view_type,
    ),
    # str -> StrView (safe implicit, C++ handles std::string -> string_view)
    Coercion(
        name="str_to_strview",
        from_type=is_str_type,
        to_type=is_str_view_type,
    ),

    # StrView -> String (allocates)
    Coercion(
        name="strview_to_string",
        from_type=is_str_view_type,
        to_type=is_string_type,
        codegen=lambda e, _a, _b, _c: f"std::string({e})",
        protocol_safe=True,
    ),
    # StrView -> str. At ARG position plain `str` params lower to
    # std::string_view, so the transfer is identity; but `Own[str]` params
    # (e.g. list[str].append's value) lower to std::string by value, and
    # INIT/ASSIGN/RETURN targets are also owned std::string -- both need
    # materialization.
    Coercion(
        name="strview_to_str",
        from_type=is_str_view_type,
        to_type=is_str_type,
        codegen=lambda e, _a, b, c: (
            e if c == CoercionContext.ARG and not isinstance(b, OwnType)
            else f"std::string({e})"
        ),
        protocol_safe=True,
    ),
    # Optional[StrView] <-> Optional[str]: plain `Optional[str]` params at ARG
    # lower to `std::optional<std::string_view>`, so the transfer is identity;
    # but `Own[Optional[str]]` params (e.g. list[Optional[str]].append) and
    # non-ARG contexts (INIT/ASSIGN/RETURN) target `std::optional<std::string>`
    # and need per-element materialization. Statement-expression pattern hoists
    # the source into a local so the conditional evaluates the expression
    # exactly once.
    Coercion(
        name="optional_strview_to_str",
        from_type=lambda t: isinstance(t, OptionalType) and is_str_view_type(t.inner),
        to_type=lambda t: isinstance(t, OptionalType) and is_str_type(t.inner),
        codegen=lambda e, _a, b, c: (
            e if c == CoercionContext.ARG and not isinstance(b, OwnType)
            else (f"({{ auto __ov = ({e}); "
                  f"__ov ? std::make_optional(std::string(*__ov)) : std::nullopt; }})")
        ),
    ),
    Coercion(
        name="optional_str_to_strview",
        from_type=lambda t: isinstance(t, OptionalType) and is_str_type(t.inner),
        to_type=lambda t: isinstance(t, OptionalType) and is_str_view_type(t.inner),
        codegen=lambda e, _a, b, c: (
            e if c == CoercionContext.ARG and not isinstance(b, OwnType)
            else (f"({{ auto __ov = ({e}); "
                  f"__ov ? std::make_optional(std::string_view(*__ov)) : std::nullopt; }})")
        ),
    ),

    # bytearray <-> bytes identity coercions (both map to std::vector<uint8_t>)
    Coercion(
        name="bytearray_to_bytes",
        from_type=is_bytearray_type,
        to_type=is_bytes_type,
        protocol_safe=True,
    ),
    Coercion(
        name="bytes_to_bytearray",
        from_type=is_bytes_type,
        to_type=is_bytearray_type,
    ),

    # bytes/bytearray -> BytesView (safe implicit, vector -> span)
    Coercion(
        name="bytes_to_bytesview",
        from_type=is_bytes_type,
        to_type=is_bytes_view_type,
    ),
    Coercion(
        name="bytearray_to_bytesview",
        from_type=is_bytearray_type,
        to_type=is_bytes_view_type,
    ),

    # BytesView -> bytes/bytearray (allocates)
    Coercion(
        name="bytesview_to_bytes",
        from_type=is_bytes_view_type,
        to_type=is_bytes_type,
        codegen=lambda e, _a, _b, _c: f"std::vector<uint8_t>({e}.begin(), {e}.end())",
        protocol_safe=True,
    ),
    Coercion(
        name="bytesview_to_bytearray",
        from_type=is_bytes_view_type,
        to_type=is_bytearray_type,
        codegen=lambda e, _a, _b, _c: f"std::vector<uint8_t>({e}.begin(), {e}.end())",
        protocol_safe=True,
    ),

    # Pointer coercions
    Coercion(
        name="record_to_ptr",
        from_type=_is_user_record,
        to_type=_is_mutable_ptr,
        type_match=lambda rec, ptr: (
            isinstance(ptr.pointee, NominalType) and ptr.pointee.is_user_record
            and rec.name == ptr.pointee.name
        ),
        requires_lvalue=True,
        requires_mutable_lvalue=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="record_to_const_ptr",
        from_type=_is_user_record,
        to_type=_is_readonly_ptr_match,
        type_match=lambda rec, ptr: (
            isinstance(ptr.inner_pointee, NominalType) and ptr.inner_pointee.is_user_record
            and rec.name == ptr.inner_pointee.name
        ),
        requires_lvalue=True,
        forbid_return_local=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="ptr_to_const_ptr",
        from_type=_is_mutable_ptr,
        to_type=_is_readonly_ptr_match,
        type_match=lambda p1, p2: p1.inner_pointee == p2.inner_pointee,
    ),
    Coercion(
        name="span_to_readonly_span",
        from_type=_is(NominalType),
        to_type=_is(NominalType),
        type_match=lambda s1, s2: (
            is_span(s1) and is_span(s2)
            and not is_readonly_span(s1) and is_readonly_span(s2)
            and unwrap_readonly(s1.type_args[0]) == unwrap_readonly(s2.type_args[0])
        ),
        protocol_safe=True,
    ),
    # basic_slice -> slice (adds step=nullopt). C++ implicit via Slice(BasicSlice) ctor.
    Coercion(
        name="basic_slice_to_slice",
        from_type=is_basic_slice_type,
        to_type=is_slice_type,
    ),
    # Span coercions: any Spannable[T] type can coerce to Span[T]
    # Arg context allows temporaries
    Coercion(
        name="spanlike_to_span_arg",
        from_type=_match_any_side,  # Matches any type; _spanlike_to_span_match filters by protocol
        to_type=_is(NominalType),
        type_match=_spanlike_to_span_match,
        contexts={CoercionContext.ARG},
    ),
    # Non-arg contexts require lvalue (can't take span of temporary)
    Coercion(
        name="spanlike_to_span",
        from_type=_match_any_side,  # Matches any type; _spanlike_to_span_match filters by protocol
        to_type=_is(NominalType),
        type_match=_spanlike_to_span_match,
        contexts={CoercionContext.INIT, CoercionContext.ASSIGN, CoercionContext.RETURN},
        requires_lvalue=True,
        forbid_return_local=True,
    ),
]


def resolve_coercion(actual: TpyType, expected: TpyType, ctx: CoercionContext) -> Optional[Coercion]:
    """Find a coercion rule that converts actual to expected in the given context."""
    # PERF TODO: linear scan over ~39 rules, each evaluating two Python-level
    # predicates (from_type / to_type) plus an optional type_match. When primitive
    # subclasses were collapsed to NominalType singletons, from_type / to_type
    # changed from C-level isinstance checks to Python calls that do a
    # type_def_of dict lookup. Most rules are indexable by (from_qname, to_qname) --
    # a hash-table dispatch with a fallback linear scan for wildcard-side rules
    # (spanlike_to_span_arg, DEREF_COERCION) would cut per-call cost >10x.
    # Not worth doing until a profile shows dispatch in the top costs.
    for coercion in COERCIONS:
        if coercion.contexts is not None and ctx not in coercion.contexts:
            continue
        if coercion.from_type(actual) and coercion.to_type(expected):
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
        if coercion.from_type(actual) and coercion.to_type(expected):
            if coercion.type_match(actual, expected):
                return True
    return False


def _deref_codegen(e: str, actual: TpyType, _expected: TpyType, _ctx: CoercionContext) -> str:
    if isinstance(actual, PtrType):
        return f"::tpy::deref_check({e})"
    return f"{e}.__deref__()"


DEREF_COERCION = Coercion(
    name="deref_to_target",
    from_type=_match_any_side,
    to_type=_match_any_side,
    codegen=_deref_codegen,
)

# @value_ptr_coercion: T -> Ptr[T] coercion for any type (not in COERCIONS list).
# Applied explicitly by calls.py for functions with @value_ptr_coercion.
VALUE_TO_PTR = Coercion(
    name="value_to_ptr",
    from_type=_match_any_side,
    to_type=_is(PtrType),
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
    from_type=_is(NominalType),
    to_type=_is(PtrType),
    requires_lvalue=True,
    requires_mutable_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)

# Pre-built coercions for __span__() and Spannable[T] protocol coercion to ReadOnlySpan.
# Used directly by compatibility.py (not in COERCIONS list); from_type is not consulted.
# Codegen is handled in expressions.py _gen_span_coercion.
# Arg context: temporaries allowed.
SPAN_METHOD_TO_SPAN_ARG = Coercion(
    name="span_method_to_span_arg",
    from_type=_match_any_side,
    to_type=_is(NominalType),
)

# Non-arg contexts: lvalue required, no returning locals.
SPAN_METHOD_TO_SPAN = Coercion(
    name="span_method_to_span",
    from_type=_match_any_side,
    to_type=_is(NominalType),
    requires_lvalue=True,
    forbid_return_local=True,
)

UPCAST_TO_CONST_PTR = Coercion(
    name="upcast_to_const_ptr",
    from_type=_is(NominalType),
    to_type=_is(PtrType),
    requires_lvalue=True,
    forbid_return_local=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)
