"""Type coercion registry for TurboPython."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .typesys import (
    TpyType, IntLiteralType, FloatLiteralType, LiteralType,
    NominalType, PtrType, OptionalType, OwnType, is_readonly_ptr,
    is_readonly_span, PendingListType, TypeParamRef, TypeParamKind, ReadonlyType,
    is_integer_type, unwrap_readonly, AnyType, same_nominal_symbol_loose,
    VIEW_TYPE_FAMILIES,
)
from .type_def_registry import (
    is_array, is_span, is_list, is_dict, is_set, int_traits_of, float_traits_of,
    is_fixed_int_type, is_big_int_type, is_float64_type, is_float32_type,
    is_float_category, is_bool_type,
    is_char_type, is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytearray_type, is_bytes_view_type,
    is_basic_slice_type, is_slice_type, view_to_owned_conv,
    has_view_param_form,
)


class CoercionContext(Enum):
    """Context in which a type coercion is being applied."""
    ASSIGN = "assign"
    INIT = "init"
    ARG = "arg"
    RETURN = "return"

    @property
    def verb(self) -> str:
        """What a value is in this context: `passed` as an argument."""
        return _CONTEXT_WORDS[self][0]

    @property
    def slot(self) -> str:
        """The slot a value goes to in this context, as a diagnostic names
        it: `the parameter`."""
        return _CONTEXT_WORDS[self][1]


_CONTEXT_WORDS = {
    CoercionContext.ARG: ("passed", "the parameter"),
    CoercionContext.RETURN: ("returned", "the return type"),
    CoercionContext.INIT: ("bound", "the variable"),
    CoercionContext.ASSIGN: ("stored", "the target"),
}


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


def int_literal_fits_fixed_int(lit: TpyType, target: TpyType) -> bool:
    """check_range for int_literal_to_fixed_int: literal value (if known) must
    fit in the target fixed-int's range."""
    if not isinstance(lit, IntLiteralType):
        return False
    tr = int_traits_of(target)
    if tr is None:
        return False
    return lit.value is None or (tr.min_value <= lit.value <= tr.max_value)


def float_literal_fits_float(lit: TpyType, target: TpyType) -> bool:
    """check_range for float_literal_to_float32: a known finite literal
    value must round to a finite value of the target float type; an
    infinite or NaN one stays what it is."""
    if not isinstance(lit, FloatLiteralType):
        return False
    tr = float_traits_of(target)
    if tr is None:
        return False
    v = lit.value
    if v is None or math.isinf(v) or math.isnan(v):
        return True
    return not math.isinf(tr.rounded(v))


def literal_range_error(lit: TpyType, target: TpyType) -> str:
    """The refusal of a literal a range-checked row does not fit (its
    `check_range`): the literal as written, sign included, and the target's
    range, from its traits."""
    if isinstance(lit, FloatLiteralType):
        ftr = float_traits_of(target)
        assert ftr is not None and lit.value is not None
        hi = ftr.spell(ftr.max_finite)
        return (f"Float literal {lit.value!r} is outside {target} range "
                f"[-{hi}, {hi}]")
    itr = int_traits_of(target)
    assert itr is not None and isinstance(lit, IntLiteralType)
    return (f"Integer literal {lit.value} is outside {target} range "
            f"[{itr.min_value}, {itr.max_value}]")


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
    # Unsigned -> signed: need strictly more bits (e.g. uint8 -> int16)
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
    # Lossless widening (actual->expected) at a container element position:
    # iterating actual yields values that widen implicitly into expected.
    # "Lossless" follows Python/CPython's informal notion: large fixed-ints
    # to float32 and BigInt to float/float32 lose mantissa precision but are
    # still considered widenings at this position (same as implicit int->float
    # promotion in Python arithmetic).
    widening_safe: bool = False
    check_range: Optional[Callable[[TpyType, TpyType], bool]] = None
    # Address-taking coercions (`&{expr}`) produce a Ptr that is unconditionally
    # non-null. Consumers can elide the deref null-check, same as for `take_ptr`.
    produces_non_null_ptr: bool = False
    # The codegen BUILDS a fresh value (`(x).to_fixed_check<int32_t>()`,
    # `std::string(::tpy::char_to_str(c))`) because C++ has no implicit
    # conversion for the pair. Two consequences every sink reads off this one
    # fact: the render must be SPELLED wherever it lands -- including a
    # list/tuple literal element, which the aggregate codegen otherwise emits
    # against the expected C++ type and silently drops (a dropped char->str
    # leaves `std::vector<std::string>{c}`, which resolves to the SIZE ctor) --
    # and what it spells is a prvalue, never an alias of the source, so it is
    # an rvalue at every position and an owning slot needs no copy/move
    # cascade for it. Rules whose render is C++-implicit or position-dependent
    # must stay out: spelling one here would double-convert.
    builds_fresh_value: bool = False
    # Admitted only where the destination BORROWS what it is handed: the rule
    # binds a buffer it does not own, so an owning sink (a field, a container
    # element, an `Own[...]` slot, or any sink that is not a plain argument)
    # must not take it. Where an owning sibling row exists the owning sink
    # falls through to it; where none does, the program has to spell the copy
    # (`borrow_only_veto` is what says so). `contexts` cannot express this --
    # the owning verdict is not a context: a borrowing parameter, an
    # `Own[...]` parameter and a container element all arrive here as
    # CoercionContext.ARG.
    borrow_only: bool = False
    # The codegen is a single wrap around its operand that reads neither the
    # coercion context nor the operand's text, so the render can be computed
    # once as a `{0}` template. THIR carries no CoercionContext, so that is
    # the only shape the lowering can pre-render; a row that reads either
    # (`str_to_string`'s literal test, the optional-view statement expression)
    # must stay out.
    context_free_wrap: bool = False
    codegen: Callable[[str, TpyType, TpyType, CoercionContext], str] = lambda expr, _a, _e, _c: expr


# NOTE: Order matters; higher priority first for overlapping rules.
COERCIONS: list[Coercion] = [
    # INT type parameter coercions (compile-time constants)
    Coercion(
        name="int_type_param_to_fixed_int",
        from_type=_is(TypeParamRef),
        to_type=is_fixed_int_type,
        type_match=_int_type_param_match,
        context_free_wrap=True,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),
    Coercion(
        name="int_type_param_to_bigint",
        from_type=_is(TypeParamRef),
        to_type=is_big_int_type,
        type_match=_int_type_param_match,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt(static_cast<int64_t>({e}))",
    ),

    # Integer literal to any fixed-width integer (range-checked)
    Coercion(
        name="int_literal_to_fixed_int",
        from_type=_is(IntLiteralType),
        to_type=is_fixed_int_type,
        check_range=int_literal_fits_fixed_int,
    ),

    # Integer literal to BigInt (always valid)
    Coercion(
        name="int_literal_to_bigint",
        from_type=_is(IntLiteralType),
        to_type=is_big_int_type,
    ),

    # Widening between fixed-width integers (e.g. int8 -> int32, uint8 -> int16)
    Coercion(
        name="fixed_int_widening",
        from_type=is_fixed_int_type,
        to_type=is_fixed_int_type,
        type_match=_is_safe_widening,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, b, _c: f"static_cast<{b.to_cpp()}>({e})",
    ),

    # Fixed-width integer to BigInt
    Coercion(
        name="fixed_int_to_bigint",
        from_type=is_fixed_int_type,
        to_type=is_big_int_type,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"::tpy::BigInt({e})",
    ),
    # BigInt to fixed-width integer (narrowing, runtime checked)
    Coercion(
        name="bigint_to_fixed_int",
        from_type=is_big_int_type,
        to_type=is_fixed_int_type,
        builds_fresh_value=True,
        context_free_wrap=True,
        codegen=lambda e, _a, b, _c: f"({e}).to_fixed_check<{b.to_cpp()}>()",
    ),

    # Float coercions
    Coercion(
        name="int_literal_to_float",
        from_type=_is(IntLiteralType),
        to_type=is_float64_type,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="fixed_int_to_float",
        from_type=is_fixed_int_type,
        to_type=is_float64_type,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    Coercion(
        name="bigint_to_float",
        from_type=is_big_int_type,
        to_type=is_float64_type,
        widening_safe=True,
        context_free_wrap=True,
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
        check_range=float_literal_fits_float,
        codegen=lambda e, _a, _b, _c: e,  # identity: gen_expr provides 'f' suffix
    ),

    # float32 coercions (widening to float32)
    Coercion(
        name="int_literal_to_float32",
        from_type=_is(IntLiteralType),
        to_type=is_float32_type,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="fixed_int_to_float32",
        from_type=is_fixed_int_type,
        to_type=is_float32_type,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    Coercion(
        name="bigint_to_float32",
        from_type=is_big_int_type,
        to_type=is_float32_type,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<float>({e})",
    ),
    # float32 -> float (widening, lossless)
    Coercion(
        name="float32_to_float",
        from_type=is_float32_type,
        to_type=is_float64_type,
        widening_safe=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"static_cast<double>({e})",
    ),
    # No float -> float32 row: a declared slot never narrows a wider value
    # of its own family implicitly, as int64 never converts into int32; the
    # user spells float32(x). Only `int` (BigInt) narrows into a fixed width,
    # and that row checks the range at run time.

    # char -> str at a BORROWING slot. `str`'s param form is a view, so the
    # owning row below would bind the slot to a full-expression temporary and
    # a view the callee hands back would dangle. `char_to_str` views an
    # immortal one-char table, which outlives every caller. Ordered before the
    # owning row so the borrowing sink takes it; `resolve_coercion` skips it
    # wherever the sink owns.
    Coercion(
        name="char_to_borrowed_str",
        from_type=is_char_type,
        to_type=is_str_type,
        # Reads the param-form verdict rather than assuming it: the row only
        # applies while `str`'s borrow form really is a distinct view type.
        type_match=lambda _a, b: has_view_param_form(b),
        borrow_only=True,
        builds_fresh_value=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"::tpy::char_to_str({e})",
    ),
    # char to str coercion
    Coercion(
        name="char_to_str",
        from_type=is_char_type,
        to_type=is_str_type,
        builds_fresh_value=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"std::string(::tpy::char_to_str({e}))",
    ),
    # char to String coercion
    Coercion(
        name="char_to_string",
        from_type=is_char_type,
        to_type=is_string_type,
        builds_fresh_value=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"::tpy::String(1, {e})",
    ),
    # char to StrView coercion
    Coercion(
        name="char_to_strview",
        from_type=is_char_type,
        to_type=is_str_view_type,
        builds_fresh_value=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"::tpy::char_to_str({e})",
    ),

    # String <-> str. `String` derives from `std::string`, so String -> str (and
    # -> StrView) is C++-implicit in every position. str -> String materializes
    # at ARG (where `str` renders a view and the `String` slot is
    # `const ::tpy::String&`) and is a C++-implicit copy elsewhere. The ARG wrap
    # skips plain string literals -- `"foo"` is a `const char*`, which the
    # `String` slot converts from directly. Non-literal sources (names, calls,
    # slices) need the wrap.
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
            else f"::tpy::String({e})"
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
        codegen=lambda e, _a, _b, _c: f"::tpy::String({e})",
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

    # bytearray -> bytes at a BORROWING `bytes` parameter only: that slot is a
    # span view, which a ByteArray satisfies with no copy. At an owning sink
    # the row does not apply, so the mismatch is reported and the copy has to
    # be written out -- neither buffer type converts to the other and CPython
    # copies at neither, so a silent copy would hand the program a private
    # buffer where CPython shares one. The `bytes` -> `bytearray` direction
    # has no row at all: an immutable buffer cannot back a mutable slot in
    # any position.
    Coercion(
        name="bytearray_to_bytes",
        from_type=is_bytearray_type,
        to_type=is_bytes_type,
        borrow_only=True,
        protocol_safe=True,
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
        codegen=lambda e, _a, b, _c: f"{view_to_owned_conv(b)}({e})",
        protocol_safe=True,
    ),
    Coercion(
        name="bytesview_to_bytearray",
        from_type=is_bytes_view_type,
        to_type=is_bytearray_type,
        codegen=lambda e, _a, b, _c: f"{view_to_owned_conv(b)}({e})",
        protocol_safe=True,
    ),

    # Pointer coercions
    Coercion(
        name="record_to_ptr",
        from_type=_is_user_record,
        to_type=_is_mutable_ptr,
        type_match=lambda rec, ptr: (
            isinstance(ptr.pointee, NominalType) and ptr.pointee.is_user_record
            and same_nominal_symbol_loose(rec, ptr.pointee)
        ),
        requires_lvalue=True,
        requires_mutable_lvalue=True,
        forbid_return_local=True,
        produces_non_null_ptr=True,
        context_free_wrap=True,
        codegen=lambda e, _a, _b, _c: f"&{e}",
    ),
    Coercion(
        name="record_to_const_ptr",
        from_type=_is_user_record,
        to_type=_is_readonly_ptr_match,
        type_match=lambda rec, ptr: (
            isinstance(ptr.inner_pointee, NominalType) and ptr.inner_pointee.is_user_record
            and same_nominal_symbol_loose(rec, ptr.inner_pointee)
        ),
        requires_lvalue=True,
        forbid_return_local=True,
        produces_non_null_ptr=True,
        context_free_wrap=True,
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

# The runtime-checked BigInt -> fixed-width narrowing THIR lowering inserts
# itself at an index it must narrow (`(i).to_fixed_check<int32_t>()`). Sema
# never resolves it, so it stays out of COERCIONS; it is declared here so its
# facts have the same home as every sema rule's.
BIGINT_NARROW = Coercion(
    name="bigint_narrow",
    from_type=is_big_int_type,
    to_type=is_fixed_int_type,
    builds_fresh_value=True,
)

_RULES_BY_NAME = {rule.name: rule for rule in (*COERCIONS, BIGINT_NARROW)}


def coercion_rule(name: str) -> Optional[Coercion]:
    """The declared rule a lowered coercion names, or None for a tag no rule
    declares."""
    return _RULES_BY_NAME.get(name)


def resolve_coercion(actual: TpyType, expected: TpyType,
                     ctx: Optional[CoercionContext],
                     sink_owns: bool = False) -> Optional[Coercion]:
    """Find a coercion rule that converts actual to expected in the given context.

    `ctx` is None when the caller knows of no context. A row that names the
    contexts it applies in is a positive admission, so it does not apply under
    an unknown one.

    `sink_owns` is the caller's verdict that the destination owns what it is
    handed rather than borrowing it; a `borrow_only` row is not admitted there.
    """
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
        if coercion.borrow_only and sink_owns:
            continue
        if coercion.from_type(actual) and coercion.to_type(expected):
            if coercion.type_match(actual, expected):
                return coercion
    return None


def context_free_wrap_template(coercion: Coercion, actual: TpyType,
                               expected: TpyType) -> Optional[str]:
    """The `{0}` render template of a `context_free_wrap` row, else None.

    The template is the row's OWN codegen lambda applied to the placeholder,
    so a coercion's C++ spelling has one home; a second table of the same
    renders drifts (`char_to_string` shipped `std::string(1, c)` from one and
    `::tpy::String(1, c)` from the other). The context handed to the lambda is
    arbitrary -- that is exactly what the flag asserts.
    """
    if not coercion.context_free_wrap:
        return None
    return coercion.codegen("{0}", actual, expected, CoercionContext.ARG)


def borrow_only_veto(actual: TpyType, expected: TpyType,
                     ctx: Optional[CoercionContext],
                     sink_owns: bool) -> Optional[Coercion]:
    """The row that converts this pair, but only where the destination borrows,
    asked at a destination that OWNS.

    Not-None means the value can be handed over at a borrowing position and
    this position is not one, so what the program is missing is the copy.
    `resolve_coercion` refuses the same row; this reports WHY, so the mismatch
    can say what to write.
    """
    if not sink_owns:
        return None
    for coercion in COERCIONS:
        if not coercion.borrow_only:
            continue
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


def is_protocol_type_arg_widening(
    actual: TpyType, expected: TpyType, default_int_type: TpyType,
) -> bool:
    """Check if actual->expected is a lossless widening at a protocol type-arg position.

    Used for container conformance (e.g. list[A] satisfies Iterable[B]) where
    the caller iterates actual and each element implicitly widens into expected.
    Narrowing is rejected (list[int32] must NOT satisfy Iterable[uint8]).

    At this position `actual` is an element type of a container, never a scalar.
    IntLiteralType's concrete value is therefore meaningless (it describes the
    first element only, not the whole sequence), so we treat it as the compiler's
    default int type and fall through to the standard int->int widening rules.
    """
    # readonly is a container-level qualifier at element positions.
    actual = unwrap_readonly(actual)
    expected = unwrap_readonly(expected)
    if isinstance(actual, IntLiteralType):
        actual = default_int_type
    elif isinstance(actual, FloatLiteralType):
        # No default_float concept: any float target is a lossless widening
        # of an abstract float literal.
        return is_float64_type(expected) or is_float32_type(expected)
    if actual == expected:
        return True
    for c in COERCIONS:
        if (c.widening_safe and c.from_type(actual) and c.to_type(expected)
                and c.type_match(actual, expected)):
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
    produces_non_null_ptr=True,
    context_free_wrap=True,
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
    produces_non_null_ptr=True,
    context_free_wrap=True,
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
    produces_non_null_ptr=True,
    context_free_wrap=True,
    codegen=lambda e, _a, _b, _c: f"&{e}",
)


# T -> Any: type-erased storage. View types are converted to their owning
# equivalent at the storage site so the Any cell owns its contents (the
# typeid stored in std::any is the owning type, not the view type). All
# other copyable types are stored as-is. Sema (compatibility.py) gates
# move-only sources before this coercion is reached.
def _any_storage_form(
    e: str, actual: TpyType, c: CoercionContext,
) -> str:
    """Return the C++ expression to pass to `::tpy::make_any(...)` for
    storing `e` (statically typed `actual`) inside a tpy::Any.

    The expression must produce a value whose decayed C++ type is the
    intended storage typeid -- argument deduction in `make_any<T>(value)`
    picks T from this expression, so the typeid is spelled from the
    resolved TPy type wherever the rendered expression could deduce
    differently, never trusted to deduction:

    - Literal-typed sources (IntLiteralType / FloatLiteralType /
      LiteralType) resolve to their canonical storage type so the typeid
      is stable regardless of which literal value triggered the coercion
      (`x: Any = 42` and `[42]: list[Any]` both store BigInt).

    - Scalars (fixed-width ints, floats, bool, char) are spelled with
      their C++ type: a fixed-int constructor over a literal folds to the
      bare literal (`int64(1)` renders `1`), which deduces `int`.

    - str / bytes sources store the family's OWNED form whatever position
      they come from: a literal (`const char*`), a view (a param, or a
      LOCAL whose storage sema left pending), or an owned value (where
      the construction is a copy; correctness over micro-efficiency).

    - Container literals reach codegen as raw brace-init expressions
      (`{1, 2, 3}`); make_any's argument deduction can't pick a type
      from a braced-init, so we prefix with the explicit C++ container
      type. For non-literal sources the same prefix is just a copy ctor.

    The bare tail below -- records, `Span` and `Array` -- is the UN-SPELLED
    remainder: it stores whatever C++ value the expression produced. That is
    right for the two owning members and wrong for `Span`, whose view is
    copied into a cell that outlives the buffer
    (`BUGS.md#span-into-any-stores-view`).
    """
    if isinstance(actual, IntLiteralType):
        # TPy's `int` annotation is BigInt; storing IntLiteral sources as
        # BigInt makes `cast(int, x)` work for the natural pattern
        # `x: Any = 42; cast(int, x)`.
        return f"::tpy::BigInt({e})"
    if isinstance(actual, FloatLiteralType):
        return f"static_cast<double>({e})"
    if isinstance(actual, LiteralType):
        return _any_storage_form(e, actual.base_type, c)
    if (is_fixed_int_type(actual) or is_float_category(actual)
            or is_bool_type(actual) or is_char_type(actual)):
        return f"{actual.to_cpp()}({e})"
    if is_bytearray_type(actual):
        # A reference type with its own storage form, not the family's.
        if c == CoercionContext.ARG:
            return f"{view_to_owned_conv(actual)}({e})"
        return e
    fam = next((f for f in VIEW_TYPE_FAMILIES if f.is_any_member(actual)), None)
    if fam is not None:
        return f"{view_to_owned_conv(fam.owned_type)}({e})"
    if is_list(actual) or is_dict(actual) or is_set(actual):
        cpp = actual.to_cpp()
        return f"{cpp}{e}" if e.startswith("{") else f"{cpp}({e})"
    return e


def wrap_into_any(
    e: str, actual: TpyType,
    ctx: CoercionContext = CoercionContext.INIT,
) -> str:
    """Build the C++ expression that wraps `e` (typed `actual`) as a tpy::Any.
    Shared by the INTO_ANY coercion and by container-element codegen sites
    where the element slot type is Any (list[Any], dict[K, Any], ...).
    """
    return f"::tpy::make_any({_any_storage_form(e, actual, ctx)})"


def _into_any_codegen(
    e: str, actual: TpyType, _expected: TpyType, c: CoercionContext,
) -> str:
    return wrap_into_any(e, actual, c)


INTO_ANY = Coercion(
    name="into_any",
    from_type=_match_any_side,
    to_type=_is(AnyType),
    codegen=_into_any_codegen,
)


# Any -> T auto-coerce: runtime checked extraction. Target T must be a
# concrete type (sema gates Union / Optional / generic-type-param targets
# before reaching this codegen). When the typeid mismatches at runtime,
# any_cast_or_panic delivers the documented panic message.
FROM_ANY = Coercion(
    name="from_any",
    from_type=_is(AnyType),
    to_type=_match_any_side,
    codegen=lambda e, _a, b, _c: f"::tpy::any_cast_or_panic<{b.to_cpp()}>({e})",
)
