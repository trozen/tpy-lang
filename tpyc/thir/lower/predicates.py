"""Shared type/shape facts for THIR lowering.

Leaf predicates over resolved types and small expression shapes: the
eligible-scalar/str/bytes/enum/union/tuple families, F1/F2 record facts,
field/receiver/write facts, narrowing condition info, and the coercion
dispositions. These leaf classifiers do not recurse through an expression or
body and do not construct THIR; lowering arms consume their results locally.
"""

from __future__ import annotations
import math
from ...parse.nodes import (
    FunctionLinkage,
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyIfExpr,
    TpyFloatLiteral,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyListRepeat,
    TpyMethodCall,
    TpyGeneratorExpression,
    TpyName,
    TpyNamedExpr,
    TpyNoneLiteral,
    TpyReturn,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
    TpyUnaryOp,
    TpyVarDecl,
    TupleElemCapture,
)
from ...modules.defs import get_dunder_cpp_template
from ...modules.type_resolution import get_iterable_element_type
from ...typesys import (
    RecursiveAliasInstanceType,
    AliasRef,
    AnyType,
    CONST_PARAMS_METHODS,
    BYTES_FAMILY,
    CallableType,
    FLOAT,
    FloatLiteralType,
    INT32,
    IntLiteralType,
    LiteralType,
    NoneType,
    is_polymorphic_subclass_fact,
    polymorphic_source_inner,
    polymorphic_source_is_pointer,
    NominalType,
    OptionalType,
    OwnType,
    RefType,
    PendingDictType,
    PendingListType,
    PendingSetType,
    PendingViewType,
    PtrType,
    ReadonlyType,
    STR_FAMILY,
    TpyType,
    TupleType,
    TypeParamKind,
    TypeParamRef,
    UnionType,
    is_any_bytes_type,
    is_any_str_type,
    is_dyn_protocol,
    is_float_type,
    is_protocol_type,
    is_readonly_span,
    is_void_like_type,
    resolve_int_literals,
    unwrap_optional_own,
    unwrap_qualifiers,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    view_family_for_type,
)
from ...type_def_registry import (
    enum_info_of,
    int_traits_of,
    is_array,
    is_basic_slice_type,
    is_big_int_type,
    is_bool_type,
    is_bytearray_type,
    is_bytes_type,
    is_bytes_view_type,
    is_char_type,
    is_dict,
    is_enum_type,
    is_fixed_int_type,
    is_int_enum_type,
    is_list,
    is_range,
    is_set,
    is_slice_type,
    is_span,
    is_str_type,
    is_str_view_type,
    is_string_type,
    type_def_of,
)
from ...coercions import CoercionContext
from ...value_category import call_returns_cpp_ref, is_rvalue_source
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    reads_storage_form_optional,
)
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.context import (
    bigint_index_narrow_type,
    enum_cpp_name,
    escape_cpp_name,
    qualified_cpp_name,
)
from ...namespace import BindingKind
from ...codegen_cpp.protocols import (
    dynamic_adapter_type,
    dynamic_base_name,
    dynamic_ref_adapter_type,
    record_inherits_dynamic,
)
from ...compilation_context import get_current_compiler
from ...qnames import COPY as COPY_QNAME
from ..fallback import ThirUnsupported
from ..faces import witness as _witness
from ..nodes import (
    Form,
    TruthinessMode,
    THIRArgTemp,
    THIRCoerce,
    THIRExpr,
    THIRFormConvert,
    THIRIsNone,
    THIRLiteral,
    THIRMove,
    THIRName,
)

# Arithmetic operators whose dunders carry a `@cpp_template` (`add_check`, ...).
# NB the parser emits true-division as op `div` (not `/`); truediv rides the same
# resolved-binop template arm -- its dunder's `::tpy::truediv({self}, {0})`
# expands exactly like `add_check`. `in`/`is` take other emit paths, out of slice.
_ARITH_OPS = frozenset({"+", "-", "*", "div", "//", "%", "**"})

# Bitwise operators. Their fixed-int dunders carry a `@cpp_template` too
# (`::tpy::lshift_check<T>`, `static_cast<T>({self} & {0})`, ...), and the emit
# is the same resolved-binop template expansion arithmetic uses, so they ride
# the scalar arm of `_lower_binop` (a set `&`/`|`/`^` returns a container, not
# a scalar, and rejects there). None of these is `+`, so the arm's bytes/str
# concat special cases stay inert for them.
_BITWISE_OPS = frozenset({"&", "|", "^", "<<", ">>"})

# Comparison operators -- `<`/`==` dunders carry a `{self} OP {0}` template (the
# derived ones emit as a bare C++ operator); the result is bool. Admitted both
# as `if`/`while` conditions and as values (`x = a < b`).
_COMPARE_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})

# Logical and/or (the parser folds `a and b` to TpyBinOp("&&")). Admitted only
# with a bool result over bool operands, where the AST emits the bare C++
# operator (`(l && r)`); the non-bool Python value semantics (`x or default` ->
# temp + ternary via _gen_logical_value) stay on the AST path.
_LOGICAL_OPS = frozenset({"&&", "||"})

# Identity tests. Admitted only as the None test on a pointer-repr Optional
# borrow name (`p is None` / `p is not None` -> `(p ==|!= nullptr)`, the
# THIRIsNone render); every other identity shape (record-vs-record, storage /
# protocol / union operands) takes another _gen_binop identity arm -> AST path.
_IS_OPS = frozenset({"is", "is not"})

# Membership. Admitted only over a dict/set container NAME whose `__contains__`
# is a plain @native member (`c.contains(needle)`, the `resolved_contains`
# render) with scalar or string needles: `needle in c` / `needle not in c`.
# list membership (`std::ranges::
# contains`, no `__contains__` member), bytes membership (a @native FREE
# function), str `.find()`, TypedDict/tuple-literal/global receivers, and the
# universal `__iter__`/`__next__` fallback take other _gen_binop arms -> AST path.
_MEMBERSHIP_OPS = frozenset({"in", "not in"})

# Literal-into-typed-slot coercions the slice reproduces, both pass-throughs on
# the C++ side (the inner literal renders directly in the slot's type): a literal
# into a fixed-int slot, and a float literal into a double `float` slot.
_INT_LIT_COERCION = "int_literal_to_fixed_int"

_FLOAT_LIT_COERCION = "float_literal_to_float"

# `String` (a concat result) into a `str` slot: identity in EVERY position (the
# Coercion carries no codegen lambda -- both sides spell std::string, and a str
# ARG slot's std::string_view converts implicitly), so it lowers as a
# THIRCoerce passthrough. The other str-family coercions are position-dependent
# (materializing at some sinks) -- see _coerce_disposition.
_STRING_TO_STR_COERCION = "string_to_str"

# Str-family coercions whose target is a VIEW: the result borrows the source's
# buffer, so the coerce renders its inner bare at every position.
_VIEW_TARGET_STR_COERCIONS = frozenset({"str_to_strview", "string_to_strview"})

# Str-family coercions that are identity in EVERY position (no codegen lambda):
# both sides of string_to_str spell std::string; the two *_to_strview arms feed
# a std::string_view slot every source converts into implicitly.
_IDENTITY_STR_COERCIONS = (frozenset({_STRING_TO_STR_COERCION})
                           | _VIEW_TARGET_STR_COERCIONS)

# Scalar-cast coercions whose codegen lambda is a fixed template around the
# inner render, position-independent -- mirrored here as `{0}` templates and
# carried on `THIRCoerce.wrap` (computed at lowering, formatted at emit).
# `fixed_int_widening`'s target-typed cast is derived from the coerce's
# expected type at lowering (see `_coerce_wrap`), not listed here.
_TEMPLATE_COERCIONS: dict[str, str] = {
    "int_literal_to_float": "static_cast<double>({0})",
    "fixed_int_to_float": "static_cast<double>({0})",
    "float32_to_float": "static_cast<double>({0})",
    "int_literal_to_float32": "static_cast<float>({0})",
    "fixed_int_to_float32": "static_cast<float>({0})",
    "float_to_float32": "static_cast<float>({0})",
    "fixed_int_to_bigint": "::tpy::BigInt({0})",
    "bigint_to_float": "static_cast<double>({0})",
    "bigint_to_float32": "static_cast<float>({0})",
    # Char -> str/String/StrView: position-independent single-arg wraps
    # (coercions.py's char_to_* lambdas). The StrView target views the shared
    # buffer, so the coerce arm's str-view-target rule tags it BORROW.
    "char_to_str": "std::string(::tpy::char_to_str({0}))",
    "char_to_string": "std::string(1, {0})",
    "char_to_strview": "::tpy::char_to_str({0})",
}

# The Float32-targeted float literal: an identity lambda whose `f`-suffix
# render comes from the literal seeing the coerce TARGET (the AST forwards it
# into gen_expr; lowering mirrors by retyping the THIRLiteral to Float32).
_FLOAT32_LIT_COERCION = "float_literal_to_float32"

# Its BigInt twin: `int_literal_to_bigint` is an identity lambda whose
# `::tpy::BigInt(...)` wrap comes from the literal's BigInt resolution --
# lowering retypes the THIRLiteral so the emitter picks the ctor arms.
_BIGINT_LIT_COERCION = "int_literal_to_bigint"

# Address-taking Ptr coercions: the codegen lambda is `&{e}` in every
# position. The AST pre-derefs an indirect-name inner (`&(*g)` for a
# pointer-slot global / pointer-local source, expressions.py's coerce
# arm); the THIR coerce arm mirrors that deref for names in lc.pointers.
_ADDR_PTR_COERCIONS = frozenset({
    "record_to_ptr", "record_to_const_ptr", "value_to_ptr",
    "upcast_to_ptr", "upcast_to_const_ptr"})

# The coercions whose AST emit pre-derefs an indirect-name inner
# (expressions.py's "need dereferencing for globals" list): the addr
# family above + the method-calling BigInt cast. The THIR coerce arm
# mirrors the deref for un-narrowed names in lc.pointers.
_INDIRECT_DEREF_COERCIONS = _ADDR_PTR_COERCIONS | {"bigint_to_fixed_int"}

# Position-independent identity coercions on the ptr/span/slice axis: both
# sides are C++-implicitly convertible (`T*` -> `const T*`, `Slice`'s
# BasicSlice ctor, span -> const-span -- _gen_span_coercion's is_span(actual)
# arm returns the inner render bare).
_PTR_IDENTITY_COERCIONS = frozenset({
    "ptr_to_const_ptr", "basic_slice_to_slice", "span_to_readonly_span"})

# list/Array -> Span[T] (`::tpy::as_span` / `as_mut_span` around the inner
# render -- _gen_span_coercion's helper tail). An array-literal inner keeps
# the same helper wrap; its make_array-prefixed inner render is target-
# threaded at the coerce arm. A span-typed actual (identity) is split off
# in `_coerce_disposition`.
_SPANLIKE_COERCIONS = frozenset({"spanlike_to_span", "spanlike_to_span_arg"})

# `__span__()`-method / Spannable-protocol coercions (sema's pre-built pair,
# not in COERCIONS): a user-record actual renders `{0}.__span__()`
# (_gen_span_coercion's user-record arm; the indirect-receiver deref is
# guarded at the coerce arm). The protocol-typed actual (bare as_span
# render) stays out -- protocol params/locals reject upstream, so the row
# would be dead.
_SPAN_METHOD_COERCIONS = frozenset({"span_method_to_span",
                                    "span_method_to_span_arg"})

def _coerce_wrap(e: TpyCoerce) -> 'str | None':
    """The `{0}` render template mirroring the coercion's codegen lambda for
    the scalar-cast family, else None. `fixed_int_widening`'s and
    `bigint_to_fixed_int`'s target-typed spellings are derived from the
    coerce's expected type verbatim (the lambdas read `b.to_cpp()` off the
    same node field)."""
    name = e.coercion.name
    if name in _TEMPLATE_COERCIONS:
        return _TEMPLATE_COERCIONS[name]
    if name == "fixed_int_widening":
        return f"static_cast<{e.expected_type.to_cpp()}>({{0}})"
    if name == "bigint_to_fixed_int":
        return f"({{0}}).to_fixed_check<{e.expected_type.to_cpp()}>()"
    # INT-kind type-param reads (`N` -- a std::size_t template value param)
    # cast per the coercion lambdas: target-typed for fixed ints, the
    # int64_t hop for BigInt.
    if name == "int_type_param_to_fixed_int":
        return f"static_cast<{e.expected_type.to_cpp()}>({{0}})"
    if name == "int_type_param_to_bigint":
        return "::tpy::BigInt(static_cast<int64_t>({0}))"
    if name in _ADDR_PTR_COERCIONS:
        return "&{0}"
    # `deref_to_target` over a Ptr[T] source is position-uniform
    # (`_deref_codegen`'s PtrType arm: arg/return/init all render
    # `::tpy::deref_check(x)`); the record-wrapper `.__deref__()` flavor
    # keeps its dedicated arg row / AST path.
    if name == "deref_to_target" and isinstance(e.actual_type, PtrType):
        return "::tpy::deref_check({0})"
    if name in _SPANLIKE_COERCIONS and not is_span(e.actual_type):
        # A span-typed actual is identity (handled in _coerce_disposition,
        # never a wrap). An array-literal inner takes the same helper wrap;
        # the coerce arm threads its make_array target into the inner render.
        helper = ("::tpy::as_span" if is_readonly_span(e.expected_type)
                  else "::tpy::as_mut_span")
        return helper + "({0})"
    if name in _SPAN_METHOD_COERCIONS:
        if is_span(e.actual_type):
            return None
        if is_protocol_type(e.actual_type):
            # `_gen_span_coercion`'s Spannable arm, checked BEFORE the
            # user-record `.__span__()` one: a `Spannable[T]` actual always
            # renders `::tpy::as_span(x)` -- readonly regardless of the slot's
            # mutability, unlike the helper tail below. Any OTHER protocol
            # falls to that tail, whose render this row does not reproduce.
            return ("::tpy::as_span({0})"
                    if e.actual_type.qualified_name() == "tpy.Spannable"
                    else None)
        return "{0}.__span__()"
    return None

def _coerce_disposition(e: TpyCoerce, *,
                        own_slot_arg: bool = False) -> 'str | None':
    """'identity' (emit passthrough), 'materialize' (`std::string(x)`, lowered
    to the S1 view->owned THIRFormConvert), 'template' (a scalar cast rendered
    through `_coerce_wrap`'s `{0}` template), or None (outside the slice).

    Mirrors the tpyc/coercions.py codegen lambdas exactly, reading the same
    facts off the node: `strview_to_str` is identity at a plain ARG slot (a
    `str` param spells std::string_view) and materializes at INIT/ASSIGN/
    RETURN and at an `Own[str]` ARG slot (the lambda's `isinstance(b,
    OwnType)` branch) -- but the Own face is served only under
    `own_slot_arg`, the flag the Own-slot copy row threads; every other
    consumer keeps the blanket `Own[...]` reject, because there the
    surrounding gen_call_arg auto-move cascade owns the render decision.
    `str_to_string` materializes only at ARG for a non-literal source; a
    NUL-free literal is const char[N], binding const std::string& directly
    (the lambda's startswith('"') token check made structural:
    cpp_string_literal_expr emits the bare-quote form exactly when the value
    is NUL-free). `strview_to_string` (const std::string& slot / owned
    String target) materializes in every position. The Optional and Char
    arms have their own renders -> AST path."""
    name = e.coercion.name
    if name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION,
                _FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION):
        return "identity"
    if name in _IDENTITY_STR_COERCIONS:
        return "identity"
    if isinstance(e.expected_type, OwnType):
        if own_slot_arg and name == "strview_to_str":
            return "materialize"
        return None
    if name in _PTR_IDENTITY_COERCIONS:
        return "identity"
    if name in _SPANLIKE_COERCIONS and is_span(e.actual_type):
        # _gen_span_coercion's is_span(actual) arm: the inner render passes
        # through bare (span -> span widening is C++-implicit).
        return "identity"
    # Scalar casts stay below the Own reject: at an `Own[...]` slot the arg
    # cascade owns the decision (conservative under-routing, not a mirror gap).
    if _coerce_wrap(e) is not None:
        return "template"
    if name == "strview_to_str":
        return ("identity" if e.context_kind == CoercionContext.ARG
                else "materialize")
    if name == "str_to_string":
        if e.context_kind != CoercionContext.ARG:
            return "identity"
        if isinstance(e.expr, TpyStrLiteral) and "\x00" not in e.expr.value:
            return "identity"
        return "materialize"
    if name == "strview_to_string":
        return "materialize"
    return None

def _peel_stale_view_owned_coerce(init: TpyExpr, binding_t: TpyType | None,
                                  analyzer) -> TpyExpr:
    """gen_expr's stale-coerce identity arm, mirrored at the decl/reassign
    sink: a str/bytes view->owned coerce is attached against the ANNOTATED
    owned type, but the binding's storage is decided later by the pending
    view resolution -- when the binding resolved VIEW, materializing would
    bind the view to an owned temporary dying at end of statement (dangling),
    so the AST renders the source bare. Returns the peeled source (to lower
    in the coerce's place) or the original init."""
    if not isinstance(init, TpyCoerce):
        return init
    if init.coercion.name == "strview_to_str":
        rt = _resolved_str_value(binding_t, analyzer)
        if rt is not None and is_str_view_type(rt):
            return init.expr
    if init.coercion.name == "bytesview_to_bytes":
        rt = _resolved_bytes_value(binding_t, analyzer)
        if rt is not None and is_bytes_view_type(rt):
            return init.expr
    return init

def _for_each_elem_binding_ok(et: TpyType | None) -> bool:
    """A for-each loop-var element type has a byte-identical `loop_var_binding`
    arm. Compositional replacement for the enumerated element-family whitelist:
    the binding FORM -- value copy (`T v = *b`), composite ref (`const auto&` /
    `auto&&` for a union/tuple), or borrow alias (`auto&&` for a record) -- is
    chosen INSIDE the shared `loop_var_binding` off `is_value_type()` and
    Union/Tuple-ness, and both the AST and THIR for-each call that same helper
    with the same resolved `et`. So the binding line matches for ANY concrete
    element; the element gate need not enumerate nominal families, and sequential
    body lowering rejects any use of the loop var it cannot route.

    The one divergence risk is an UNRESOLVED pending element type: THIR spells
    its `et` before the AST's `resolve_type` would concretize it, so the binding
    would diverge. `et` reaches here with int-literals and str already resolved
    by the caller, so a surviving `PendingViewType` is a bytes view (no
    bytes-loop-var cell yet) and a pending container is a not-yet-concretized
    nested container -- both stay on the AST path.

    An `Optional` loop var is excluded EXCEPT the value-repr Optional[cheap
    scalar / Char] family (`std::optional<T> item = *__beg_N;`): a body that
    NARROWS one (`if x is None: continue`) then reads it needs the value-repr
    deref (`(*x)`) that the value-opt param arms render -- the for-each
    lowering registers the loop var in `lc.value_opt_locals` so those arms
    fire for it (the same declared-binding-keyed renders the AST applies).
    Other Optional inners keep the blanket reject; a narrowable UNION loop var
    is NOT excluded -- its isinstance extraction reads the shared `declared`
    map, which the loop var populates, so it mirrors byte-identically."""
    if et is None:
        return False
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if isinstance(bare, OptionalType):
        return _foreach_value_opt_elem(et) is not None
    return not isinstance(bare, (PendingViewType, PendingListType,
                                 PendingDictType, PendingSetType))


def _foreach_value_opt_elem(et: TpyType | None) -> 'OptionalType | None':
    """The value-repr `Optional[cheap scalar / Char]` loop-var element family
    (the same inner slice `_value_opt_scalar` admits for params, spelled
    locally so this gate does not move if that helper's slice widens). The
    loop var binds as a typed `std::optional<T>` copy and its body reads ride
    the value-opt binding arms via `lc.value_opt_locals`."""
    if not isinstance(et, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    if isinstance(inner, OwnType):
        return None
    return t if (_eligible_scalar(inner) or _eligible_char(inner)) else None

def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints, `bool`, both float widths (`double` / `float`), and
    BigInt (`::tpy::BigInt` -- heap-backed but a value type with overloaded
    operators, so reads/writes/ops render bare like any scalar): borrow/
    storage form never arises and the C++ spelling comes straight from
    `TpyType.to_cpp()`. Target-typed literal renders (the Float32 `f`
    suffix, the BigInt ctor wraps) ride `_slot_literal_retype` at the slot
    sites plus the literal coerce arms, which retype the literal so the
    emitter picks the wrapped render.
    """
    return t is not None and (is_fixed_int_type(t) or is_bool_type(t)
                              or is_float_type(t) or is_big_int_type(t))

def _callable_value(t: 'TpyType | None') -> bool:
    """A non-template `Callable[[...], R]` value slot (`std::function<...>`
    by value): decls, call results, and bare-name passes all render bare --
    the lambda/std::function converts implicitly. `Fn` templates (no to_cpp)
    and wrapper-marked slots stay out."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(t, CallableType) and not t.is_template


def _is_type_param_slot(t: 'TpyType | int | None') -> bool:
    """True if `t` is a bare generic type-param slot (`T` in a `Record[T]`),
    seen through the readonly / Ref wrappers a param or field type carries. A
    `T` slot takes no borrow/storage lift -- it renders per-instantiation via
    the C++ template's `val_or_ref_t<T>` traits -- so every gate that admits one
    keys on this single shape."""
    return isinstance(t, TpyType) and isinstance(
        unwrap_readonly(unwrap_ref_type(t)), TypeParamRef)

def _own_type_param_slot(t: 'TpyType | int | None') -> bool:
    """True if `t` is `Own[T]` for a bare generic type-param `T` -- the
    ownership-transfer sibling of `_is_type_param_slot`. An `Own[T]` param
    (`own_param_t<T>` == `T&&`) and return (`own_return_t<T>`) render
    per-instantiation via the C++ traits, so a bare param read and a DIRECT
    `return <own-param>` pass byte-identically (no `std::move` on the return --
    the move only arises at an intermediate local decl, `y = std::move(x)`, the
    deferred `Own[T]` local-decl cell)."""
    if not isinstance(t, TpyType):
        return False
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(inner, OwnType) and _is_type_param_slot(inner.wrapped)

def _value_record_member(m: 'TpyType') -> bool:
    """A non-generic user VALUE-record union member (`Fixed` in `Fixed | Zone
    | None`, datetime's `ZoneInfo`): stored by value in the variant like a
    scalar, spelled via to_cpp/native_cpp_names (cross-module qualification
    agrees with the resolver). Generic value records stay out -- their
    type-arg recursion is the F5 spelling slice."""
    return (isinstance(m, NominalType) and m.is_user_record
            and m.is_value_type() and not m.type_args)

def _value_record_slot(t: 'TpyType | None') -> bool:
    """A user VALUE-record DECL slot -- `_value_record_member` widened to
    generic instantiations (`Pair<int32_t> q = p;`). The union-member
    predicate excludes those because a variant member's spelling recurses
    through the type args; a decl spells its slot through `render_type`,
    which already renders the instantiation."""
    return (isinstance(t, NominalType) and t.is_user_record
            and t.is_value_type())


def _span_slot(t: 'TpyType | None', analyzer) -> bool:
    """A `Span[T]` DECL slot over a scalar or plain-record element
    (`std::span<Node> s = ::tpy::as_mut_span(b);`): the decl only spells the
    slot, and the local's own reads gate themselves, so the reference-element
    span decls as the same plain copy the scalar one does. `_span_value` stays
    the narrower READ/pass slice (element-dependent renders live there);
    nested-span and other composite elements keep rejecting -- their reads
    have no admitted arm to gate against."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType) or not is_span(u):
        return False
    args = getattr(u, "type_args", None)
    if not args:
        return False
    elem = unwrap_readonly(args[0])
    return _eligible_scalar(elem) or _f1_record(elem, analyzer)


def _eligible_value_union(t: TpyType | None) -> 'UnionType | None':
    """The F4 U1 slice: a value-form union of scalar / Char / str / StrView /
    value-record members (`Int32 | Float64 [| None]`, `Int32 | str`,
    `Fixed | Zone | None`) -- `std::variant<...>` where every member is stored
    by value (a str member is owned `std::string`, a StrView member a
    `std::string_view`, a ValueType record itself). At the WHOLE-variant
    positions the slice routes -- reads/writes/returns/same-type args, and
    isinstance extraction (`std::get<std::string>` / `std::get<Fixed>`,
    spelled through the shared `render_type`) -- the member form is fixed by
    the variant, so both paths render bare (the converting ctor does the
    work) and a `None` source renders `std::monostate{}`. The form-relevant
    boundary is member INSERT: a str-VIEW value into a `... | str` slot is a
    view->owned conversion (`std::variant<...> __tmp = view;`), which
    `_value_union_temp_slot`'s member check rejects (the body then stays
    AST). Pointer-variant record members (U2) and recursive-alias wrappers
    ride later F4 cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return None
    if not all(_eligible_scalar(m) or _eligible_char(m)
               or is_str_type(m) or is_str_view_type(m)
               or _value_record_member(m)
               or is_void_like_type(m) for m in t.members):
        return None
    return t

def _wrapper_member_ctor_slot(init, t: 'TpyType | None',
                              analyzer) -> bool:
    """M4c: an annotated recursive-alias wrapper decl initialized with a
    member-record ctor rvalue (`a: Tree = Leaf(42)` -> `Tree a =
    Leaf(...);`). The wrapper struct's template converting ctor absorbs
    the member, so the init renders bare -- the wrapper twin of the
    value-union converting-ctor row. Non-ctor sources (names, calls,
    container literals) stay out."""
    if t is None or not isinstance(init, TpyCall):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or not t.needs_wrapper():
        return False
    it = unwrap_readonly(analyzer.get_expr_type(init))
    if not (isinstance(it, NominalType) and it.is_user_record):
        return False
    wrapper = t.wrapper_info()
    members = wrapper.full_members if wrapper is not None else t.members
    return any(m == it for m in members)


def _union_member_ctor_slot(init, t: 'TpyType | None', analyzer) -> bool:
    """A value-variant union ELEMENT slot over record members, initialized
    with a member-record ctor rvalue (`[Dog("Rex"), Cat("W")]` into
    `std::vector<std::variant<Cat, Dog>>`): the variant's converting ctor
    absorbs the member, so the element renders bare on both paths.

    The plain-union twin of `_wrapper_member_ctor_slot`, deliberately narrow:
    a union with any NON-record member (`Int32 | Cat`) can need the
    target-typed literal render, a wrapper union has its own row, and a
    union-TYPED name source is the `to_value_variant` lift, not this."""
    if t is None or not isinstance(init, TpyCall):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return False
    members = [unwrap_readonly(m) for m in t.members]
    if not members or not all(isinstance(m, NominalType) and m.is_user_record
                              for m in members):
        return False
    it = unwrap_readonly(analyzer.get_expr_type(init))
    if not (isinstance(it, NominalType) and it.is_user_record):
        return False
    return any(m == it for m in members)


def _own_storage_union_return(t: TpyType | None, analyzer) -> 'UnionType | None':
    """An `Own[A | B]` return slot over F1-RECORD members: a by-value
    `std::variant<A, B>` (the storage form -- ownership makes the union a
    value at the return boundary, never the pointer variant). The routed
    source is a member-record ctor rvalue, which returns bare (the variant's
    converting ctor absorbs it). A None member keeps the slot out (the
    monostate/None renders are the Optional families'); value-member unions
    ride `_eligible_value_union` at the plain-union return arm."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    inner = unwrap_readonly(u.wrapped)
    if not isinstance(inner, UnionType) or inner.has_none_member():
        return None
    if not all(_f1_record(m, analyzer) for m in inner.members):
        return None
    return inner

def _eligible_ptr_union(t: TpyType | None, analyzer) -> 'UnionType | None':
    """The F4 U2 slice: a pointer-repr union of record members (`A | B
    [| None]` -> borrow `std::variant<[std::monostate, ]A*, B*>` / storage
    `std::variant<[std::monostate, ]A, B>`). Non-None members must be
    `_f1_record`-renderable (any non-generic user record -- native / cross-module
    type spelling agrees with the resolver; only generics stay off), so both C++
    spellings render byte-identically; a None member is the monostate slot --
    both spellings render it `std::monostate`, so it adds no form question.
    A MIXED union's eligible-scalar members (`Int32 | Dog | None` -> an
    `int32_t*` alternative) render through the same member-shape-blind
    machinery (`m.to_cpp() + "*"` borrow, bare value storage, `*std::get<
    int32_t*>(v)` extraction), so they ride the record rows unchanged; an
    ALL-scalar union is value-repr (`is_ptr_variant_union` False) and stays
    on the U1 slice. Recursive-alias wrappers and protocol unions ride
    later cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_f1_record(m, analyzer) or _eligible_scalar(m)
               or is_void_like_type(m) for m in t.members):
        return None
    return t

def _eligible_wrapper_union(t: TpyType | None,
                            analyzer=None) -> 'UnionType | None':
    """The F6 slice: a recursive-alias WRAPPER union (`needs_wrapper()` --
    emitted as `struct Alias { std::variant<...> value; }`, stored by value
    everywhere). The variant access is the VALUE form reached via `.value`
    (`_narrow_variant_cpp`, mirroring VariantAccess.variant_expr), so the
    isinstance test and the extraction alias render member-shape-blind
    (`holds_alternative<M>(v.value)` / `std::get<M>(v.value)`); no member
    restriction is needed for the render itself -- the narrowed FACT retypes
    the subject for the branch walk, where the ordinary per-construct gates
    apply. With `analyzer`, a non-generic `AliasRef` slot (the placeholder
    container elements and annotations carry) resolves through the registry
    to its union body first."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, AliasRef) and not t.args and analyzer is not None:
        t = analyzer.registry.resolve_alias_ref(t)
    if not (isinstance(t, UnionType) and t.needs_wrapper()):
        return None
    return t

def _union_storage_val_cpp(ptr_u: 'UnionType') -> str:
    """The value-variant STORAGE spelling for a ptr-variant union slot
    (`__slot_N`), mirroring the AST's `type_to_cpp` for a union: a NAMED plain
    union alias (`type Shape = A | B`) spells its C++ name, everything else the
    expanded `std::variant<...>`. The codegen `union_alias_names` registry (what
    the AST's `to_cpp` reads) is not populated until header emission (after THIR
    lowering), so the sema-time `union_display_names` is the lowering-visible
    source. The two agree for every slot THIR reaches: a module-LOCAL alias keys
    the same short name in both, and a NON-renamed cross-module alias registers
    that same short name under both the defining module's `setdefault` (display)
    and the importing module's `register_union_alias` (alias). A RENAMED
    cross-module alias (`from mod import Shape as MyShape`) is the only shape
    where they DIVERGE -- display keeps "Shape", alias becomes "MyShape" -- but
    that form is currently rejected at sema (the local-decl annotation cannot
    resolve the renamed name; see BUGS.md + error_union_type_alias_cross_module
    _renamed), so no such slot reaches this function. If that rejection is
    lifted, this must read `union_alias_names` (or fall back to AST) instead."""
    compiler = get_current_compiler()
    if compiler is not None:
        alias = compiler.union_display_names.get(ptr_u.members)
        if alias is not None:
            return alias
    return ptr_u.to_cpp()

def _union_binding_divergent(e: 'TpyName', locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A union-declared name whose read type is NOT that union: sema's
    assignment narrowing retyped the read to a member (`x: I | F = k; v = x`
    reads `x` as Int32), but the AST renders the bare variant name into the
    member-typed sink -- invalid C++ without a `std::get` (a pre-existing AST
    miscompile, see BUGS.md). No green corpus case can exercise it, so any
    narrowing-divergent union read stays on the AST path."""
    bt = locals_.get(e.name)
    if bt is None:
        return False
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    if not isinstance(bt, UnionType):
        return False
    rt = analyzer.get_expr_type(e)
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return rt != bt

def _isinstance_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool] | None':
    """The F4 U3 isinstance-condition shape: `isinstance(v, A)` /
    `isinstance(v, (A, B))` on a declared local/param of a routed union (U1
    value / U2 pointer-variant). Returns `(var, union, check_members,
    folded_true)` or None. `folded_true` is sema's exhaustiveness constant-fold
    (`macro_expansion == True`, the last elif of an exhausted union): the
    condition renders `true` and the AST suppresses the dead implicit-else
    (`_condition_static_true`). Out of the slice: Any / polymorphic /
    deref-view / type-param subjects (different extraction machinery),
    readonly-qualified subjects (the `ptr_variant_to_const` chain stays AST,
    the U2 verdict), and indirect / frame-slot names (no globals or resumable
    frames route). A recursive-alias wrapper union rides the F6 slice
    (`_eligible_wrapper_union`; the `.value` variant access)."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if cond.isinstance_type_param or cond.isinstance_deref_depth:
        return None
    me = cond.macro_expansion
    folded = isinstance(me, TpyBoolLiteral) and me.value is True
    if me is not None and not folded:
        return None
    var = cond.isinstance_var
    dt = declared.get(var)
    if dt is None:
        return None
    db = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    if db is not dt:
        # F2: a READONLY-qualified subject (a @readonly callable's union
        # param, `Ref(ReadonlyType(U))`) is admitted -- the const-pointee
        # spelling rides `_narrow_subject_const`; any other wrapper stays
        # out.
        if not isinstance(unwrap_ref_type(unwrap_send_sync(dt)),
                          ReadonlyType):
            return None
        dt = db
    u = (_eligible_value_union(dt) or _eligible_ptr_union(dt, analyzer)
         or _eligible_wrapper_union(dt, analyzer))
    if u is None:
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    # Each check member must be a member of the subject union (sema enforces;
    # kept as a slice guard so a fact mismatch gate-rejects, never mis-lowers).
    if not all(any(m == cm for m in u.members) for cm in members):
        return None
    return var, u, members, folded

def _any_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, tuple[TpyType, ...]] | None':
    """The D15 Any-isinstance condition shape: `isinstance(v, A)` /
    `isinstance(v, (A, B))` on a declared local/param of exactly `Any`.
    Returns `(var, check_members)` or None. The Any sibling of
    `_isinstance_narrow_info`: no exhaustiveness fold exists for Any (sema
    cannot exhaust an open type), and `NoneType` check members stay AST
    (the typeid(std::monostate) arm, a later rung)."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    var = cond.isinstance_var
    dt = declared.get(var)
    if dt is None or unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))) is not dt:
        return None
    if not isinstance(dt, AnyType):
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    if any(isinstance(m, NoneType) for m in members):
        return None
    return var, members


def _poly_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, NominalType, TpyType] | None':
    """The polymorphic-isinstance if condition: `isinstance(v, Sub)` on a
    declared local/param whose type is a @dynamic-dispatch source (a bare
    dyn-protocol ref or polymorphic base -- `polymorphic_source_inner`),
    narrowing to a strict subclass. Returns `(var, member, var_decl)` or
    None. The slice admits the BARE (`T&`) binding and the raw `Ptr[Base]`
    binding (the cast arg spells `&var` vs bare `var`, picked at lowering);
    pointer-repr Optional (nullability machinery) / deref-view subjects,
    tuple checks, negation, and compound conditions stay AST."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    member = cond.isinstance_type
    if not isinstance(member, NominalType):
        return None
    var = cond.isinstance_var
    dt = _poly_subject_decl(declared.get(var))
    if dt is None:
        return None
    registry = analyzer.registry
    if not is_polymorphic_subclass_fact(dt, member, registry):
        return None
    return var, member, dt


def _poly_narrow_multi_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, tuple[NominalType, ...], TpyType] | None':
    """The NO-ALIAS polymorphic isinstance forms: the TUPLE check
    `isinstance(v, (A, B))` -> the no-init OR-chain
    `((dynamic_cast<const A*>(v) != nullptr) || ...)`, and the ROOT-class
    identity check `isinstance(v, Base)` -> the single no-init null-check
    (the `is not None` pin). Neither binds an extraction alias (a union
    fact / identity fact extracts nothing on the AST path); reads inside
    the branch keep the subject's own render. Returns
    `(var, members, var_decl)` or None."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    var = cond.isinstance_var
    dt = _poly_subject_decl(declared.get(var))
    if dt is None:
        return None
    registry = analyzer.registry
    ct = cond.isinstance_type
    if isinstance(ct, UnionType):
        members = tuple(ct.members)
        if not all(isinstance(m, NominalType)
                   and is_polymorphic_subclass_fact(dt, m, registry)
                   for m in members):
            return None
        return var, members, dt
    if isinstance(ct, NominalType):
        root = polymorphic_source_inner(dt, registry)
        if root is None or ct != root:
            return None
        return var, (ct,), dt
    return None


def _poly_subject_decl(dt: 'TpyType | None') -> 'TpyType | None':
    """The poly-subject declared type after the admitted wrappers: bare, or
    READONLY-qualified (`readonly[Optional[Base]]` -- the const-pointee
    spelling rides `_poly_subject_readonly`). Any other wrapper declines."""
    if dt is None:
        return None
    db = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    if db is not dt and not isinstance(
            unwrap_ref_type(unwrap_send_sync(dt)), ReadonlyType):
        return None
    return db


def _poly_subject_readonly(dt: 'TpyType | None') -> bool:
    """Whether the subject's RAW declared type is readonly-qualified -- the
    AST's `isinstance(var_decl, ReadonlyType)` const arm."""
    return (dt is not None
            and isinstance(unwrap_ref_type(unwrap_send_sync(dt)),
                           ReadonlyType))


def _poly_isinstance_value_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, tuple[NominalType, ...], TpyType] | None':
    """A VALUE-position polymorphic isinstance (`return isinstance(e, VE)`):
    the bare null-check chain -- no branch, no alias, so members may be
    strict subclasses or the root, single or tuple. Returns
    `(var, members, var_decl)` or None."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    var = cond.isinstance_var
    dt = _poly_subject_decl(declared.get(var))
    if dt is None:
        return None
    registry = analyzer.registry
    root = polymorphic_source_inner(dt, registry)
    if root is None:
        return None
    ct = cond.isinstance_type
    members = (tuple(ct.members) if isinstance(ct, UnionType) else (ct,))
    if not all(isinstance(m, NominalType)
               and (m == root
                    or is_polymorphic_subclass_fact(dt, m, registry))
               for m in members):
        return None
    return var, members, dt


def _any_narrow_fact(members: tuple[TpyType, ...],
                     facts: dict[str, TpyType], var: str) -> TpyType | None:
    """The concrete extraction fact for an Any-narrowed branch, or None when
    the branch extracts nothing (no fact, or a union/void fact -- the tuple
    form's A|B branch fact aliases nothing, mirroring the union path)."""
    ft = facts.get(var)
    if ft is None or isinstance(ft, UnionType) or is_void_like_type(ft):
        return None
    return ft if any(m == ft for m in members) else None


def _any_narrow_facts_ok(members: tuple[TpyType, ...],
                         facts: dict[str, TpyType], var: str) -> bool:
    """A branch facts map the Any slice can mirror: facts describe only the
    checked var, each a check member, a union, or void (the Any sibling of
    `_narrow_facts_ok`; an `AnyType` remainder fact also extracts nothing)."""
    for k, ft in facts.items():
        if k != var:
            return False
        if not (isinstance(ft, (UnionType, AnyType))
                or is_void_like_type(ft)
                or any(m == ft for m in members)):
            return False
    return True


def _narrow_fact_member(u: UnionType, facts: dict[str, TpyType],
                        var: str) -> TpyType | None:
    """The concrete-member extraction fact for `var` in a branch's type-facts
    map, or None when the branch keeps the variant un-extracted (no fact, or a
    remaining-union / void fact -- mirrors `_emit_isinstance_extractions`'
    union/void skip). A fact that is neither a member of `u` nor union/void has
    no mirrored emit; the caller gate-rejects on it via `_narrow_facts_ok`."""
    ft = facts.get(var)
    if ft is None or isinstance(ft, UnionType) or is_void_like_type(ft):
        return None
    return ft if any(m == ft for m in u.members) else None

def _narrow_facts_ok(u: UnionType, facts: dict[str, TpyType], var: str) -> bool:
    """A branch facts map the slice can mirror: facts describe only the checked
    var (a compound condition or deref-view key would carry other entries), and
    each fact is a member of the union, a remaining union, or void."""
    for k, ft in facts.items():
        if k != var:
            return False
        if not (isinstance(ft, UnionType) or is_void_like_type(ft)
                or any(m == ft for m in u.members)):
            return False
    return True

def _facts_have_concrete(facts: dict[str, TpyType]) -> bool:
    """Mirror of `_has_concrete_isinstance_facts`: whether a facts map would
    emit an extraction -- the AST's elif-flattening gate (a concrete else-fact
    forces `} else {` + a nested if instead of a flat `else if`)."""
    return any(
        not (isinstance(ty, (UnionType, LiteralType)) or is_void_like_type(ty))
        and not is_protocol_type(ty)
        for ty in facts.values()
    )

def _is_elif_link(outer: TpyIf, inner: TpyIf) -> bool:
    """Mirror of StatementGenerator._is_elif (and emit._is_elif): an elif keeps
    the outer's column; a nested `else: if` sits deeper. Both-locs-None (macro
    fragments) counts as elif."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column

def _elif_link(stmt: TpyIf) -> 'TpyIf | None':
    """The single elif continuation in `stmt`'s else body (the AST's
    is_elif_continuation shape: one same-column TpyIf), or None for a genuine
    else block. Whether the link then FLATTENS to `else if` additionally
    requires no concrete else-fact (`_facts_have_concrete`) -- the callers
    that flatten check that separately, mirroring _gen_if's chain collect."""
    if (len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], TpyIf)
            and _is_elif_link(stmt, stmt.else_body[0])):
        return stmt.else_body[0]
    return None

def _post_if_narrow_fact(
        stmt: TpyIf, info, narrowed: 'set[str] | dict[str, str]',
) -> TpyType | None:
    """The early-return implicit-else fact: when the then-body terminates with
    a return and there is no else block, code after the if is the else branch,
    and the AST emits a persistent statement-level extraction (`_gen_if`'s
    post-narrowing arm, `_narrows_to_union_member` + not-already-narrowed).
    `narrowed` is the active narrowing scope (eligibility's set / lowering's
    alias map -- only membership is read). Returns the member fact or None."""
    var, u, _members, folded = info
    if folded or stmt.else_body or not stmt.else_type_facts:
        return None
    if var in narrowed:
        return None
    if not (stmt.then_body and isinstance(stmt.then_body[-1], TpyReturn)):
        return None
    return _narrow_fact_member(u, stmt.else_type_facts, var)

def _chain_post_if_fact(
        stmt: TpyIf, declared: dict[str, TpyType],
        narrowed: 'set[str] | dict[str, str]', analyzer,
) -> 'tuple[str, UnionType, TpyType] | None':
    """The post-if extraction for a whole if statement: the AST collects the
    flat elif chain first and runs post-narrowing on `chain[-1]`, emitting the
    persistent alias at the ENCLOSING scope -- so the fact belongs to the last
    FLATTENABLE link (same column, no concrete intermediate else-fact),
    whatever the head condition's kind (a plain-headed chain can still end in
    a narrowing elif). A nested `else: if` is a body statement of its else
    block and handles its own post-if there. Returns `(var, union, member)`
    or None."""
    last = stmt
    while ((nxt := _elif_link(last)) is not None
           and not _facts_have_concrete(last.else_type_facts)):
        last = nxt
    # Peel a leading `not` (the negated-polarity simple form): the fact is read
    # from `last.else_type_facts`, which sema already computed for the actual
    # else branch regardless of the condition's polarity.
    cond = last.condition
    if isinstance(cond, TpyUnaryOp) and cond.op == "!":
        cond = cond.operand
    info = _isinstance_narrow_info(cond, declared, analyzer)
    if info is None:
        return None
    post = _post_if_narrow_fact(last, info, narrowed)
    if post is None:
        return None
    return info[0], info[1], post

def _reassert_bump_info(
        stmt: TpyAssert, declared: dict[str, TpyType],
        persistent_narrowed, analyzer,
) -> 'tuple[str, TpyType] | None':
    """The U4 re-assert: `assert isinstance(v, A)` on a subject already
    PERSISTENTLY extracted to that same member. Sema folds the condition
    (`macro_expansion == True`, so the AST emits `if (!(true)) ...`) and the
    extraction re-runs with a suffix-bumped alias (`__v` -> `__v_2`)
    targeting the original variant. `persistent_narrowed` answers whether
    the subject's live alias is statement-level (eligibility's set /
    lowering's derived var set) -- after a branch/loop-scoped extraction the
    same AST emit REDECLARES the alias in the same C++ block (the BUGS.md
    `_gen_while` collision), so those reject. Returns `(var, member)` or
    None."""
    cond = stmt.condition
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if cond.isinstance_type_param or cond.isinstance_deref_depth:
        return None
    me = cond.macro_expansion
    if not (isinstance(me, TpyBoolLiteral) and me.value is True):
        return None
    var = cond.isinstance_var
    if var not in persistent_narrowed:
        return None
    member = declared.get(var)
    facts = stmt.then_type_facts
    if member is None or list(facts) != [var] or facts[var] != member:
        return None
    return var, member

def _folded_neg_int_literal(e: TpyExpr, analyzer) -> int | None:
    """The negated value of a unary-minus int literal (`-3`), or None.

    Mirrors `_gen_unaryop`'s literal-negation fold exactly (op `-`, a
    `TpyIntLiteral` operand of `IntLiteralType`): the AST renders
    `_gen_int_literal_value(-v, target)`. Lowering carries that helper's final
    spelling on the resulting `THIRLiteral`, including wide-value casts and
    suffixes."""
    if not (isinstance(e, TpyUnaryOp) and e.op == "-"
            and isinstance(e.operand, TpyIntLiteral)
            and isinstance(analyzer.get_expr_type(e.operand), IntLiteralType)):
        return None
    return -e.operand.value

def _resolved_scalar(t: TpyType | None, analyzer) -> bool:
    """`_eligible_scalar` over a type that may still be an IntLiteralType: a
    literal-seeded container leaves IntLiteral element types on its use sites
    (the sema-resolved method fi's slots, a `pop`/subscript result, print args of
    its loop var) -- the AST path resolves these through TypeResolver/default-int
    at emit; the emitted value is the same bare literal either way. Readonly/Ref
    wrappers are peeled first: a scalar coerced into a `readonly[K]` slot carries
    the wrapper on its expr type, and a readonly scalar is representationally
    the same C++ value.

    Companion convention: a RECEIVER gate reads the declared/`locals_` BINDING
    type, never `get_expr_type` on the name -- a literal-seeded local's use sites
    carry the pre-resolution pending container type (see `_is_len_call`,
    method-call lowering, subscript lowering,
    `_for_each_container_route`)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _eligible_scalar(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))

def _resolve_pending_view(t: TpyType | None, analyzer) -> TpyType | None:
    """A `PendingStrType`/`PendingBytesType` resolved to its concrete view/owned
    type through the family's ViewVarInfo (sema's usage resolution is FINAL
    pre-lowering), else None. Mirrors codegen's `_resolve_view_storage`: no
    registry entry (or an unresolved one) falls back to the owned type."""
    if not isinstance(t, PendingViewType):
        return None
    info = analyzer.ctx.view_vars(t.family).get(t.var_id)
    return (info.resolved_type if info is not None and info.resolved_type
            else t.family.owned_type)

def _resolved_str_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The sema-RESOLVED str-slice type -- owned `str` (`std::string` storage /
    `std::string_view` param), `StrView` (`std::string_view`), or `tpy.String`
    (`std::string` everywhere, including the `const std::string&` param slot
    the SKELETON emitter spells -- no body arm renders it) -- or None outside
    the slice. A str local's binding type stays `PendingStrType` on the
    AST/sema side; resolve it like `_resolve_pending_view` does. `Char`,
    `Literal[str]`-annotated bindings and the bytes family stay on the AST
    path. Callers that key on the FORM axis must treat String as owned:
    `is_str_type` is False for it, so a bare `is_str_type(resolved)` test
    reads it as a view -- see `_str_name_form`."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return _resolve_pending_view(t, analyzer) if t.family is STR_FAMILY else None
    if isinstance(t, NominalType) and (is_str_type(t) or is_str_view_type(t)
                                       or is_string_type(t)):
        return t
    return None

def _resolved_bytes_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The bytes twin of `_resolved_str_value`: the sema-RESOLVED bytes-slice
    type -- owned `bytes` (`std::vector<uint8_t>` storage / `std::span<const
    uint8_t>` param) or `BytesView` (span) -- or None outside the slice. A
    bytes local's binding stays `PendingBytesType`; resolve it through the
    family's ViewVarInfo. `bytearray` (a reference type, different axis) and
    `Literal[bytes]` bindings stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return (_resolve_pending_view(t, analyzer)
                if t.family is BYTES_FAMILY else None)
    if isinstance(t, NominalType) and (is_bytes_type(t) or is_bytes_view_type(t)):
        return t
    return None

def _resolved_viewfam_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The resolved str- OR bytes-family slice value -- the two view families
    share the slice/iteration receiver shapes and emit machinery -- or None."""
    st = _resolved_str_value(t, analyzer)
    return st if st is not None else _resolved_bytes_value(t, analyzer)

def _bytes_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-slice comparison operand: a bytes literal (rendered OWNED --
    `_comparison_targets` threads no target for bytes, so the AST's
    `gen_expr(lit, None)` takes the owned arm) or a bytes/BytesView value.
    Guards the compare arm's operand pin -- see `_lower_binop`."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None

def _str_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-slice comparison operand: a str literal (its expr type is
    `LiteralType[str]`, but the const char[N] emit is position-independent),
    a str/StrView value, or a `String` value (a concat result -- std::string
    takes the same compare templates / bare operators, rendered bare). Guards
    the compare arm's operand pin -- see `_lower_binop`."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))

def _is_string_owned(t: TpyType | None) -> bool:
    """A `tpy.String` value -- the owned std::string type a str-family concat
    produces (and the resulting type of a local bound to one). String is now
    INSIDE `_resolved_str_value`'s slice (its `const std::string&` param slot
    is spelled by the skeleton emitter, so no body arm renders it), which
    makes most `or _is_string_owned(...)` disjuncts redundant; this predicate
    survives for the places that must tell String apart from `str` on the
    FORM axis -- the STORAGE name form, the in-place append target, and the
    `Optional[String]` exclusion from the value-optional view family."""
    if t is None:
        return False
    return is_string_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))

def _str_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-concat operand the slice renders bare into the resolved dunder's
    template: a str literal (const char[N]), a str/StrView value (param, local,
    or owned-str call result), or a `String` value (a concat-result local /
    nested concat). A `Char` operand's overload wraps it in `char_to_str` with
    its own operand render -- out of the slice (S4 introduces Char values)."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))

def _bytes_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-concat operand rendered bare into `::tpy::bytes_concat(l, r)`:
    a bytes literal (rendered OWNED -- the resolved `__add__` overload's
    receiver/param target is owned `bytes`, never `BytesView`, so the AST's
    target-threaded render takes the owned arm) or a bytes/BytesView value
    (param, local, nested concat, or owned-bytes call result -- names and
    spans render bare; the vector->span conversion at the span params is
    implicit). A `bytearray` operand also resolves to the native
    `bytes_concat` dunder but is not a bytes-slice value -> AST path."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None

def _peel_coerce(e: TpyExpr) -> TpyExpr:
    """The expression under any stack of TpyCoerce wrappers."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e

def _str_self_append_rhs(target_name: str, value: TpyExpr) -> 'TpyExpr | None':
    """The `x = x + y` self-append trigger, mirroring the AST's
    `_try_str_inplace_append` shape test exactly: peel any TpyCoerce wrappers,
    then match `TpyBinOp("+", TpyName(target), rhs)`. Returns the rhs (`y`) the
    peephole appends, or None when the shape does not match (the assignment
    then lowers as a plain reassign). The TARGET-type half of the AST condition
    is checked by the caller via `_owned_str_append_target`."""
    inner = _peel_coerce(value)
    if (isinstance(inner, TpyBinOp) and inner.op == "+"
            and isinstance(inner.left, TpyName)
            and inner.left.name == target_name):
        return inner.right
    return None

def _owned_str_append_target(t: TpyType | None, analyzer) -> bool:
    """The target-type half of the AST's in-place-append conditions (the str
    `+=` branch and `_try_str_inplace_append`): the binding is owned-str-family
    -- `str`, `String`, or a `PendingStrType`. The AST admits ANY pending
    binding; here the pending must RESOLVE owned, which is equivalent for every
    shape that reaches lowering (the very reassign/aug-assign being checked
    forces the owned resolution) and keeps the emitted `t += v;` honest."""
    st = _resolved_str_value(t, analyzer)
    if st is not None and is_str_type(st):
        return True
    # A String binding resolves INSIDE the str slice now, so the owned check
    # must run on the resolved type too -- otherwise `s += v` on a String
    # target reads as a view and loses the in-place append.
    return _is_string_owned(st if st is not None else t)

def _str_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The C++ shape of a str-slice NAME read -- mirrors the AST's
    `_is_str_view_source`: a `StrView`-resolved binding and a `str`-typed param
    (the signature spells `std::string_view`) are view/BORROW; an owned local is
    `std::string` (STORAGE). The owned-sink copy (`std::string(x)` at a decl
    init / return) fires only on a BORROW source; a str literal is const
    char[N] (implicitly convertible both ways) and stays VALUE, never wrapped.
    A `String` binding is owned in EVERY position -- its param slot is
    `const std::string&`, so the param arm must not read it as a view."""
    if is_string_type(resolved):
        return Form.STORAGE
    if is_str_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE

def _bytes_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The bytes twin of `_str_name_form`, mirroring the AST's
    `_is_bytes_view_source`: a `BytesView`-resolved binding and a `bytes`-typed
    param (the signature spells `std::span<const uint8_t>`) are view/BORROW --
    they drive the owned-sink `::tpy::bytes_copy(x)` -- while an owned local is
    `std::vector<uint8_t>` (STORAGE)."""
    if is_bytes_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE

def _eligible_char(t: TpyType | None) -> bool:
    """A `Char` value (C++ `char`): value-scalar-like for the shapes this slice
    routes -- str subscript results, str-iteration loop vars, params/returns,
    compare operands, print args (streamed raw; Char has no int_traits, so no
    int8 cast arises). Kept out of `_eligible_scalar` deliberately: a str
    literal in a Char-typed slot renders as a target-typed C++ char literal
    (`'x'`), which the scalar positions do not thread -- each Char position
    gates its literal shape explicitly (single-char literals route at compare
    operands, annotated decl inits, and Char-slot call args; the reassign /
    return guards stay as defensive rejects -- sema type-errors those)."""
    if t is None:
        return False
    return is_char_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))

def _readonly_global_type(gt: TpyType | None, analyzer) -> TpyType | None:
    """The unwrapped type of a same-module VALUE global whose function-body
    READ renders bare -- exactly a local/param read of the same resolved type
    (value globals are plain namespace-scope objects; only non-value globals
    take the `T*` pointer slot and its `(*g)` / `->` renders). Scalars and
    Char render bare everywhere their arms admit them; str/bytes globals
    carry the same view/owned form duality as locals (`_str_name_form` keys
    on the resolved type, and a global is never in `param_names`, so an owned
    `std::string` global is STORAGE and a `StrView` one BORROW -- both the
    AST's `_is_str_view_source` verdicts); a `Ptr[T]` global is a `T*` VALUE
    slot (PtrType is a value type, so no pointer-slot indirection arises).
    A VALUE-TUPLE global (scalar / owned-str / nested value-tuple elements,
    `_value_tuple` recursively -- the `Final[tuple[...]]` constants) is also
    a plain namespace-scope value whose read renders bare; its routed
    consumers (the standalone tuple-unpack source, tuple subscript reads)
    render `std::get` over the bare name on both paths. Everything else
    stays AST: containers/records are pointer slots, Optional-value globals
    have no THIR read/narrow arms yet (the AST's narrowed-global deref is
    the oracle to mirror when seeding them), and unions/enums are unprobed.
    Returns None when out of the family."""
    if gt is None:
        return None
    gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
    # An unannotated literal-init global carries IntLiteralType; its storage
    # resolves through the module default int (the AST's _resolve_global_type
    # does the same), so resolve before the family check.
    gt = resolve_int_literals(gt, analyzer.ctx.default_int_for_literal)
    if _eligible_scalar(gt) or _eligible_char(gt):
        return gt
    if (_resolved_str_value(gt, analyzer) is not None
            or _resolved_bytes_value(gt, analyzer) is not None
            or _eligible_ptr_value(gt, analyzer)):
        return gt
    if _value_tuple_global(gt, analyzer) is not None:
        return gt
    # A value-repr Optional[scalar] global (`std::optional<T>` at namespace
    # scope) reads exactly like a value-opt LOCAL: bare whole-optional
    # (None-tests, opt slots), `(*g)` on a narrowed occurrence, and
    # `deref_optional_check(g)` unproven -- the AST's narrowed-global deref
    # family renders through the same local-shaped sites since the
    # _declared_type_incl_globals fix. Callers register the name in
    # lc.value_opt_locals so the reads ride _value_opt_scalar_binding.
    if _value_opt_scalar(gt, analyzer) is not None:
        return gt
    return None

def _pointer_slot_global_type(gt: TpyType | None, analyzer, *,
                              name: 'str | None' = None,
                              native_globals=()) -> 'TpyType | None':
    """The unwrapped type of a POINTER-SLOT global (`T* g{};` at namespace
    scope -- the generator's `pointer_globals` classification: non-value,
    no wrapper) whose READ-ONLY body renders ride the pointer-local arms:
    `(*g)` value reads, `g->` receivers, the addr-coerce `&(*g)`, and the
    `T& q = (*g);` alias bind. Only the plain shapes admit -- an F1-record
    or a list/dict/set container; Optional/union/protocol/iterator-typed
    globals carry unmirrored read machinery and stay rejected. Pass `name`
    (with the module's `native_globals`) for a same-module candidate so
    the Final / native-linkage exclusion lives here (those names are
    namespace-scope VALUES on the AST side, never slots); imported
    candidates key finality on their own registry facts and pass no name.
    Returns None when out of the family."""
    if gt is None:
        return None
    if name is not None and (name in native_globals
                             or name in analyzer.ctx.final_globals):
        return None
    gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
    if gt.is_value_type() or gt.needs_wrapper():
        return None
    if (_f1_record(gt, analyzer)
            or is_list(gt) or is_dict(gt) or is_set(gt)):
        return gt
    return None

def _value_tuple_global(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The `Final[tuple[...]]` global / class-constant family: scalar, str
    (owned OR view -- a constant's str element is a static string_view,
    which reads bare exactly like a view local), and NESTED such tuples
    (`NESTED: Final[tuple[tuple[str, Int32], str]]`; a nested element reads
    as a plain value copy `std::tuple<...> inner = std::get<0>(__tup_N);`).
    Wider than `_value_tuple` on the str-view and nesting axes because no
    tuple-LITERAL render is involved here -- only bare name reads and
    `std::get` element reads."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    for e in t.element_types:
        if (_eligible_scalar(e)
                or _resolved_str_value(unwrap_ref_type(e), analyzer)
                is not None):
            continue
        inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if (isinstance(inner, TupleType)
                and _value_tuple_global(inner, analyzer) is not None):
            continue
        return None
    return t

def _runtime_bigint(t: TpyType | None, analyzer) -> bool:
    """`TypeResolver.is_runtime_bigint`'s type half: a concrete BigInt value,
    or an IntLiteralType whose module default int is BigInt. Guards the
    positions whose AST render NARROWS a BigInt (`.to_fixed_check<int32_t>()`
    at subscript indices / slice bounds, the range-counter machinery) -- the
    slice pins those to fixed-int operands and defers the narrow arms."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_big_int_type(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))

_BIGINT_NARROW = "bigint_narrow"  # synthetic THIRCoerce tag (not a sema coercion)

def _unwrap_lit_coerce(e: TpyExpr) -> TpyExpr:
    """Strip sema's int-literal slot coercions (fixed-int / BigInt targets) so
    literal-shape checks see the digit token the AST renders."""
    while (isinstance(e, TpyCoerce)
           and e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)):
        e = e.expr
    return e

def _bigint_index_disposition(index: TpyExpr, obj_type: 'TpyType | None',
                              analyzer) -> 'str | TpyType':
    """How a subscript index renders when its type half is a runtime BigInt --
    gen_index_expr's decision, written once so the gates and the wrap sites
    cannot drift. obj_type is the receiver (its declared key/index type picks
    the narrow width via bigint_index_narrow_type):

      * 'bare' -- no narrow: not runtime-BigInt, an int32-range (possibly
        negated) int literal (`_is_int_constant` exempts those, and the
        emitter renders an unresolved IntLiteralType literal as the bare
        token on both paths), or a BigInt-keyed receiver (the index passes
        through unnarrowed; a big literal renders through the shared
        render_int_literal_value on both paths);
      * a fixed-int TpyType -- the `{0}.to_fixed_check<T>()` wrap at the
        receiver's declared key width (no outer parens: any composite render
        already carries its own);
      * 'reject' -- an out-of-int32-range literal headed for a narrow: the
        AST renders the BigInt ctor wrap inside the narrow, a shape the
        literal emit does not reproduce."""
    if not _runtime_bigint(analyzer.get_expr_type(index), analyzer):
        return "bare"
    narrow = (INT32 if obj_type is None
              else bigint_index_narrow_type(obj_type, analyzer))
    c = _const_index(index)
    if c is not None:
        if -(2**31) <= c <= 2**31 - 1:
            return "bare"
        return "bare" if narrow is None else "reject"
    if _const_index(_unwrap_lit_coerce(index)) is not None:
        # A coerce-wrapped literal fails `_is_int_constant` on the AST path, so
        # the narrow would wrap the literal's target-typed render -- a shape
        # not observed at index positions (sema leaves indices unwrapped);
        # defensive reject rather than a guessed mirror.
        return "reject"
    return "bare" if narrow is None else narrow

def _narrow_bigint_index(idx: 'THIRExpr', e: TpyExpr, obj_type: 'TpyType | None',
                         analyzer, loc) -> 'THIRExpr':
    """Wrap a lowered runtime-BigInt index in the `.to_fixed_check<T>()`
    narrow when its disposition says so (reads, del-item); 'reject' never
    reaches lowering (the gates exclude it)."""
    disp = _bigint_index_disposition(e, obj_type, analyzer)
    if isinstance(disp, str):
        return idx
    _witness("narrow.subscript_index")
    return THIRCoerce(result_type=disp, expr=idx,
                      coercion_name=_BIGINT_NARROW,
                      wrap=f"{{0}}.to_fixed_check<{disp.to_cpp()}>()", loc=loc)

def _eligible_enum(t: TpyType | None, analyzer) -> 'TpyType | None':
    """A registered enum value type of any flavor: same-module (bare name),
    cross-module (qualified `::tpyapp::m::E`), @native (user qname + member
    rename map + the `__repr__` print arm), or nested (`Outer.Kind`, the
    record-scoped `A::B` spelling). Value-position spellings all route
    through `enum_cpp_name` at lowering (member access, `E(x)`) or through
    the shared `native_cpp_names`-aware `to_cpp()` / `render_type` (decl
    types), so no per-flavor emit split is needed here. Returns the
    unwrapped enum type, or None."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not is_enum_type(t):
        return None
    if enum_info_of(t) is None:
        return None
    return t

def _enum_member_cpp(e: TpyFieldAccess, analyzer) -> str:
    """The rendered `E::A` spelling for a type-level enum member access --
    gen_expr's BindingKind.ENUM arm (and the chained nested-access arm):
    enum_cpp_name over the current module (bare / `Outer::Kind` /
    `::tpyapp::m::E` / @native qname) plus the @native member rename map."""
    enum_type = e.enum_member_of
    einfo = enum_info_of(enum_type)
    member_cpp = (einfo.cpp_member_name_map.get(e.field, e.field)
                  if einfo is not None else e.field)
    spelled = enum_cpp_name(enum_type, analyzer.ctx.module_name, einfo=einfo)
    return f"{spelled}::{member_cpp}"

def _chain_module_name(node: TpyExpr, declared: dict[str, TpyType],
                       analyzer) -> 'str | None':
    """Mirror of the AST's `_chain_to_module_name`: the registered module a
    name/field chain resolves to (`m` after `import m`, `pkg.sub` after
    `import pkg.sub`), else None. The AST asks the function namespace for the
    head binding; here a name in `declared` (a local/param) answers VARIABLE
    there, so it short-circuits to None before the module-level lookup."""
    if isinstance(node, TpyName):
        if node.name in declared:
            return None
        binding = analyzer.global_ns.lookup(node.name)
        if binding is None:
            return (node.name
                    if analyzer.registry.get_module(node.name) is not None
                    else None)
        if binding.kind == BindingKind.MODULE:
            return (binding.import_source[0]
                    if binding.import_source else node.name)
        return None
    if isinstance(node, TpyFieldAccess):
        head = _chain_module_name(node.obj, declared, analyzer)
        if head is None:
            return None
        dotted = f"{head}.{node.field}"
        return dotted if analyzer.registry.get_module(dotted) is not None else None
    return None

def _static_type_chain(node: TpyExpr, declared: dict[str, TpyType],
                       analyzer) -> bool:
    """Mirror of the AST's `_is_static_type_chain`: the chain names a static
    C++ type (record/enum, nested, imported, or module-qualified), so a
    class-constant read off it needs no receiver eval. `declared` stands in
    for the function-namespace VARIABLE bindings (a shadowing local makes the
    chain a runtime expression on both paths)."""
    if isinstance(node, TpyName):
        if node.name in declared:
            return False
        binding = analyzer.global_ns.lookup(node.name)
        if binding is None:
            return False
        if binding.kind in (BindingKind.RECORD, BindingKind.ENUM):
            return True
        if binding.kind == BindingKind.IMPORTED_NAME and binding.import_source:
            src_mod, src_name = binding.import_source
            registry = analyzer.registry
            dotted = f"{src_mod}.{src_name}"
            return (registry.find_module_record(src_mod, src_name) is not None
                    or registry.get_builtin_record(dotted) is not None
                    or registry.get_enum(dotted) is not None)
        return False
    if isinstance(node, TpyFieldAccess):
        module_name = _chain_module_name(node.obj, declared, analyzer)
        if (module_name is not None
                and analyzer.registry.find_module_record(
                    module_name, node.field) is not None):
            return True
        return _static_type_chain(node.obj, declared, analyzer)
    return False

def _bare_module_recv(obj: TpyExpr, declared: dict[str, TpyType],
                      analyzer) -> 'str | None':
    """The registered module a BARE `mod.X` receiver names (gen_expr's
    module-variable arm: a MODULE binding, aliased imports resolved through
    import_source), or None. A shadowing local/global VARIABLE binding makes
    the read a normal field access on both paths -- `declared` covers the
    local half, the global_ns kind check the module-level half."""
    if not isinstance(obj, TpyName) or obj.name in declared:
        return None
    binding = analyzer.global_ns.lookup(obj.name)
    if binding is None or binding.kind != BindingKind.MODULE:
        return None
    return binding.import_source[0] if binding.import_source else obj.name

def _module_var_read_cpp(module_name: str, var_name: str,
                         analyzer) -> 'str | None':
    """The AST render of a module-variable read (`mod.X` / `pkg.sub.X`):
    the declared native symbol, the `(*slot)` deref for a non-value pointer
    slot, or the qualified `cpp_expr`. None when the (module, var) pair is
    not registered -- the AST falls through to other arms there, so the
    caller rejects instead of guessing."""
    mi = analyzer.registry.get_module(module_name)
    if mi is None or var_name not in mi.variables:
        return None
    vi = mi.variables[var_name]
    if vi.native_cpp_name is not None:
        return vi.native_cpp_name
    if vi.is_pointer:
        return f"(*{vi.cpp_expr})"
    return vi.cpp_expr

def _global_record_recv(obj: TpyExpr, declared: dict[str, TpyType],
                        analyzer) -> 'TpyType | None':
    """A SAME-module non-value F1-record global used as a field-access
    receiver (`time.x` off a top-level `time: Timer = Timer()`): the AST
    renders the bare pointer-slot name with an arrow (`time->x`,
    is_indirect_name). Returns the record type, or None. Locals/params
    shadow (`declared`), imported and narrowed names keep their own arms,
    and value types never take the pointer slot."""
    if not isinstance(obj, TpyName) or obj.name in declared:
        return None
    if (obj.name not in analyzer.ctx.top_level_decls
            or obj.name in analyzer.imported_names):
        return None
    gt = analyzer.ctx.global_scope.lookup(obj.name)
    if gt is None:
        nb = analyzer.global_ns.lookup_local(obj.name)
        gt = (nb.type if nb is not None
              and nb.kind is BindingKind.VARIABLE else None)
    if gt is None:
        return None
    gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
    if gt.is_value_type() or not _f1_record(gt, analyzer):
        return None
    return gt

def _field_over_global_record_ok(e: TpyExpr, declared: dict[str, TpyType],
                                 analyzer) -> bool:
    """A marker-clean value field READ off a same-module global-record
    receiver (`_global_record_recv`) -- the `time->x` pointer-slot render.
    Read position only: the write/aug statement gates keep their declared-
    receiver requirement, so a global field write stays on the AST path."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _global_record_recv(e.obj, declared, analyzer) is not None)

def _class_const_pure_receiver(e: TpyFieldAccess, declared: dict[str, TpyType],
                               analyzer) -> bool:
    """The class-constant receiver shapes whose AST render is the BARE
    qualified name (`_class_constant_access_parts` returns receiver_eval
    None): a name receiver (`C.X`, `c.X`, `self.X`) or a static-type chain
    (`Outer.Inner.X`, `mod.C.X`). An unproven-Optional receiver
    (`deref_check`) or an effectful receiver (a call, evaluated via
    `static_cast<void>`) renders a statement expression -- out of the arm."""
    if e.needs_optional_runtime_check:
        return False
    if isinstance(e.obj, TpyName):
        return True
    return _static_type_chain(e.obj, declared, analyzer)

def _class_constant_cpp(e: TpyFieldAccess, analyzer,
                        render_type) -> 'str | None':
    """The full `<owner>::<member>` spelling of a class-constant access --
    the qualified-name half of the AST's `_class_constant_access_parts`:
    @native rename, generic per-instantiation spelling (off the receiver's
    typed instantiation via `render_type`, codegen's type_to_cpp), the
    cross-module qualification, or the same-module (possibly nested/dotted)
    name; plus the Final[T] = native_field(...) member rename. None when the
    generic path lacks its receiver type or renderer (reject, not a guess)."""
    owner = e.class_constant_owner
    if owner.is_native and owner.native_name:
        cpp_qname = owner.native_name
    elif owner.type_params:
        obj_type = analyzer.get_expr_type(e.obj)
        if obj_type is None or render_type is None:
            return None
        unwrapped = unwrap_qualifiers(obj_type)
        if isinstance(unwrapped, OptionalType):
            unwrapped = unwrapped.inner
        cpp_qname = render_type(unwrapped)
    else:
        qual = analyzer.registry.record_qualification(
            owner, analyzer.ctx.module_name)
        if qual:
            cpp_qname = qualified_cpp_name(*qual)
        else:
            cpp_qname = "::".join(
                escape_cpp_name(part) for part in owner.name.split("."))
    cc_field = owner.class_constants.get(e.field)
    cpp_member = (cc_field.native_name if cc_field and cc_field.native_name
                  else escape_cpp_name(e.field))
    return f"{cpp_qname}::{cpp_member}"

def _enum_compare_pair(e: TpyBinOp, lt: TpyType | None, rt: TpyType | None,
                       analyzer) -> bool:
    """Enum comparison operands: the same eligible enum on both sides (the
    bare/templated compare -- `(c == Color::RED)`, enum class operators), or
    -- when sema set `int_enum_coercion` (ordering over IntEnums, or an
    IntEnum against an int) -- each int-enum side casting to the underlying
    type at emit (`static_cast<int32_t>(p) >= static_cast<int32_t>(...)`,
    the AST's per-side post-generation casts)."""
    ie = e.int_enum_coercion
    if ie is not None:
        if _eligible_enum(ie, analyzer) is None:
            return False
        return all(_eligible_enum(t, analyzer) is not None
                   or _resolved_scalar(t, analyzer) for t in (lt, rt))
    el, er = _eligible_enum(lt, analyzer), _eligible_enum(rt, analyzer)
    return el is not None and er is not None and el == er

def _binop_operand_casts(e: TpyBinOp, analyzer) -> 'tuple[str | None, str | None]':
    """The per-side post-generation operand casts of _gen_binop's comparison
    path, as `{0}` wraps:

      * `int_enum_coercion` set -> whichever operand is IntEnum-typed casts
        to the underlying type (`static_cast<int32_t>(p)`);
      * a mixed BigInt/float comparison -> the BigInt operand casts to the
        float operand's C++ type (BigInt has no implicit conversion to
        double, `static_cast<double>(b) < x`).
    """
    def operand_t(operand):
        t = analyzer.get_expr_type(operand)
        return (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                if t is not None else None)

    ie = e.int_enum_coercion
    if ie is not None:
        u_cpp = enum_info_of(ie).underlying_type.to_cpp()
        wrap = f"static_cast<{u_cpp}>({{0}})"

        def enum_side(operand):
            t = operand_t(operand)
            return wrap if t is not None and is_int_enum_type(t) else None

        return (enum_side(e.left), enum_side(e.right))
    if e.op in _COMPARE_OPS:
        # Mirror the AST's `left_cmp`/`right_cmp` (get_resolved_type): a bare
        # float literal opposite a BigInt resolves to `double`, so the
        # is_float_type check below fires (is_float_type excludes the
        # unresolved FloatLiteralType).
        def resolved_operand_t(operand):
            t = operand_t(operand)
            return (resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
                    if t is not None else None)
        lt, rt = resolved_operand_t(e.left), resolved_operand_t(e.right)
        if lt is not None and rt is not None:
            if is_big_int_type(lt) and is_float_type(rt):
                return (f"static_cast<{rt.to_cpp()}>({{0}})", None)
            if is_big_int_type(rt) and is_float_type(lt):
                return (None, f"static_cast<{lt.to_cpp()}>({{0}})")
    return (None, None)

def _enum_truthy_wrap(t: TpyType | None, analyzer) -> 'str | None':
    """The truthiness render for an enum-typed operand, as a `{0}` wrap --
    gen_truthy_expr's enum arms: an IntEnum tests its underlying value
    (`(static_cast<U>({0}) != 0)`), while every plain enum value is truthy, so
    the value folds to `true` and the operand rides along as a discard. None
    for non-enum types; `_plain_enum_truthy` is the arm discriminator."""
    et = _eligible_enum(t, analyzer)
    if et is None:
        return None
    if is_int_enum_type(et):
        u_cpp = enum_info_of(et).underlying_type.to_cpp()
        return f"(static_cast<{u_cpp}>({{0}}) != 0)"
    return "(static_cast<void>({0}), true)"

def _plain_enum_truthy(t: TpyType | None, analyzer) -> bool:
    """True for a PLAIN (non-Int) enum operand -- the always-true arm. The two
    enum arms differ in more than their render (operand use, witness, which
    operand shapes admit), so they discriminate on this rather than on the
    wrap text."""
    et = _eligible_enum(t, analyzer)
    return et is not None and not is_int_enum_type(et)

def _truthiness_mode(t: TpyType | None, analyzer) -> TruthinessMode | None:
    """Mirror the non-identity arms of `_truthy_for_rendered`.

    None means the truthiness render is the ordinary value render. Enum
    truthiness stays on `_enum_truthy_wrap`, which also carries its underlying
    C++ type spelling.
    """
    if t is None:
        return None
    resolved = resolve_pending_container(t, analyzer) or _resolve_pending_view(
        t, analyzer) or t
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(resolved)))
    if is_enum_type(u):
        return None
    if isinstance(u, AnyType):
        return TruthinessMode.TO_BOOL
    if isinstance(u, OptionalType) and not u.uses_pointer_repr():
        return TruthinessMode.IS_TRUTHY
    if is_any_str_type(u) or is_any_bytes_type(u):
        return TruthinessMode.NONEMPTY
    # The AST checks primitives before record lookup; bool's builtin record
    # carries __bool__, but still renders bare here.
    if (is_bool_type(u) or is_fixed_int_type(u) or is_big_int_type(u)
            or is_float_type(u) or is_char_type(u)
            or isinstance(u, IntLiteralType)):
        return None
    record = analyzer.registry.get_record_for_type(u)
    if record:
        if record.get_method_overloads("__bool__"):
            return TruthinessMode.RECORD_BOOL
        if record.get_method_overloads("__len__"):
            return TruthinessMode.RECORD_LEN
    if isinstance(u, NominalType) and u.is_user_record:
        return TruthinessMode.ALWAYS_TRUE
    return None

def _enum_prop_wrap(e: TpyFieldAccess, analyzer) -> 'str | None':
    """An enum instance property read, as a `{0}` wrap over the receiver
    render (gen_expr's enum property arms): `c.value` -> `static_cast<U>({0})`
    (U = the underlying type), `c.name` -> `::tpy::EnumUtil<E>::name({0})`
    (a `string_view` into EnumUtil's static member-name storage; sema types
    it StrView, so lowering tags it BORROW and owned-str sinks fire the S1
    view->owned copy like any other view source). None when `e` is not such
    a read. The receiver is an enum VALUE (never a pointer-local), so the
    bare receiver render composes in any position."""
    if e.enum_member_of is not None or e.field not in ("name", "value"):
        return None
    et = _eligible_enum(analyzer.get_expr_type(e.obj), analyzer)
    if et is None:
        return None
    if e.field == "name":
        return f"::tpy::EnumUtil<{et.to_cpp()}>::name({{0}})"
    einfo = enum_info_of(et)
    return f"static_cast<{einfo.underlying_type.to_cpp()}>({{0}})"

def _enum_neg_wrap(e: TpyUnaryOp, analyzer) -> 'str | None':
    """IntEnum unary negation `-p` -> `(-static_cast<U>({0}))` (_gen_unaryop's
    IntEnum arm; sema leaves resolved_unaryop None there). None otherwise."""
    if e.op != "-" or e.resolved_unaryop is not None:
        return None
    et = _eligible_enum(analyzer.get_expr_type(e.operand), analyzer)
    if et is None or not is_int_enum_type(et):
        return None
    u_cpp = enum_info_of(et).underlying_type.to_cpp()
    return f"(-static_cast<{u_cpp}>({{0}}))"

def _slice_object_type(t: TpyType | None) -> bool:
    """A slice-object value (`basic_slice` -> `::tpy::BasicSlice`, `slice` ->
    `::tpy::Slice`): a by-value C++ type whose only admitted use is as a str
    subscript index (`s[sl]`, rendered bare into the resolved slice
    `__getitem__` template). Admitted as a param and as a ctor-initialized
    local (`sl = basic_slice(1, 3)`, validated by TpyCall lowering)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_basic_slice_type(t) or is_slice_type(t)

def _range_object_value(t: TpyType | None) -> bool:
    """A `range(...)` object value (`::tpy::Range<T>`): a by-value builtin
    with its own operator<< and iterator, decl'd as the plain spelled copy
    (`::tpy::Range<int32_t> r = ::tpy::Range<int32_t>(3);`). The init is a
    `range()` call validated by TpyCall lowering; the name streams raw."""
    if t is None:
        return False
    return is_range(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))

def _char_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A Char comparison operand: a Char-typed value, or a single-char str
    literal (the AST threads target=CHAR into its render -> `'x'`,
    `_comparison_targets`' char arm). A multi-char literal never renders as a
    char literal -> AST path. The str-pair arm is checked first, so a
    literal-vs-literal compare stays a plain string compare (no char target
    arises without a Char-typed operand)."""
    if isinstance(e, TpyStrLiteral):
        return len(e.value) == 1
    return _eligible_char(t)

def _span_value(t: TpyType | None) -> bool:
    """A value-view `Span[scalar]` / `Span[readonly[scalar]]` value
    (`std::span<T>` / `std::span<const T>`). A span is a value type -- reads,
    copies, decls and returns all render bare (no owning wrap, no per-element
    convert), the same on both paths. Only byte-identical SOURCES actually
    route: a bare span name / call result / storage-form field read whose
    spelling already matches, or a `Spannable`->span / const-widening coerce
    (`::tpy::as_mut_span(b)` / `as_span`), whose own arm renders the call. The
    DECL slot is `_span_slot`'s (element-blind by design); this predicate
    stays restricted to the eligible scalars for the READ / pass positions,
    matching the span param / read slice's `_container_elem_family` span
    arm."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType) or not is_span(u):
        return False
    args = getattr(u, "type_args", None)
    return bool(args) and _eligible_scalar(unwrap_readonly(args[0]))

def _span_return(t: TpyType | None) -> bool:
    """The span RETURN slot -- see `_span_value` (the return is one of its
    bare-render positions)."""
    return _span_value(t)

def _f1_record_type_arg_ok(a: 'TpyType | int', analyzer) -> bool:
    """A generic user-record type-arg that THIR spells byte-identically to the
    resolver. The resolver recurses args via `type_to_cpp`, THIR via each arg's
    own `to_cpp()`; the two coincide only on this slice -- a raw INT type param,
    an eligible value scalar, or a (recursively) F1-renderable record. Union /
    enum / tuple / nested-container args diverge (union alias names, enum
    renames, tuple element qualification) and keep the outer generic on the AST
    path. A `TypeParamRef` arg (`Pair[T]` -- the generic record's OWN definition
    context) renders `Pair<T>` byte-identically (both paths spell the bare param
    name `T`); admitting it opens the sig/ctor gate for the record's templated
    bodies (stage B/C), whose T-typed slots are gated separately."""
    if isinstance(a, int):
        return True
    if _is_type_param_slot(a):
        return True
    if isinstance(a, TpyType):
        # A CONCRETE str-family arg (`Box[str]` / `Box[StrView]`): both paths
        # spell the storage form (`std::string` / `std::string_view`) -- the
        # view/owned param split never applies inside a type-arg list.
        u = unwrap_readonly(a)
        if isinstance(u, NominalType) and (is_str_type(u)
                                           or is_str_view_type(u)):
            return True
        # A builtin-container arg (`Box[list[Int32]]`): both paths spell the
        # formatter form (`std::vector<...>`) recursing element args through
        # the same slice; union/enum/tuple elements keep their divergent
        # spellings out via the recursion.
        if isinstance(u, NominalType) and (is_list(u) or is_dict(u)
                                           or is_set(u)):
            return bool(u.type_args) and all(
                _f1_record_type_arg_ok(ea, analyzer) for ea in u.type_args)
        # `Rc[None]` / `Box[None]`: the unit arg spells `std::monostate` on
        # both paths; an unresolved float literal arg (`Rc.new(3.14)`)
        # resolves to the default double on both.
        if isinstance(u, (NoneType, FloatLiteralType)):
            return True
    return (_eligible_scalar(a) or _eligible_char(a)
            or _f1_record(a, analyzer)
            or _f1_dyn_protocol_type_arg(a, analyzer))

def _f1_dyn_protocol_type_arg(a: 'TpyType | int', analyzer) -> bool:
    """A `@dynamic` protocol type-arg (`Box[Conn]` / `Rc[Conn]`) that THIR
    spells byte-identically to the resolver. The resolver spells the arg via
    `dynamic_base_name`; THIR via its bare `to_cpp()`. They coincide only for a
    same-module, non-`@native` (no `cpp_concept`), non-shadowed protocol -- a
    cross-module / shadowed / native protocol qualifies on the resolver side
    only, so it keeps the outer generic on the AST path."""
    if not isinstance(a, TpyType):
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
    if not (isinstance(u, NominalType) and is_dyn_protocol(u)):
        return False
    return dynamic_base_name(u, analyzer) == u.to_cpp()

def _f1_record(t: TpyType | None, analyzer) -> bool:
    """The byte-identical THIR record slice: any concrete user record whose
    `TpyType.to_cpp()` == `TypeResolver.type_to_cpp()` and whose field / method
    names THIR reproduces. Record kinds that satisfy that:

    - same-module records -- no qualification / rename at all;
    - `@native` records -- type spelled via `Compiler.native_cpp_names` (the same
      map the resolver reads), field renames via the AST's `native_field_name`
      (stamped into THIR field access by `_field_cpp`), methods via
      `fi.native_name` (already honored); the module axis is irrelevant
      (native_cpp_names qualifies a cross-module native record too);
    - cross-module non-native records -- `native_cpp_names` qualifies them by
      qname exactly as the resolver's `imported_record_qualification_for_type`
      does (corpus-verified byte-identical);
    - GENERIC records with concrete args (`Pair[int]`) -- the base name resolves
      as above and `to_cpp()`'s type-arg recursion agrees with the resolver iff
      every arg is itself in the byte-identical slice (`_f1_record_type_arg_ok`:
      scalar / F1-record / INT). This is the F5 rung's type-spelling half; the
      generic record's OWN templated bodies (TypeParamRef args) ride Stage B/C;
    - `@builtin_type` records WITH real bodies whose TypeDef carries no
      `cpp_formatter` (Poll; Waker stays excluded -- its static TypeDef DOES
      carry a formatter): `to_cpp()` then falls through to the same
      `{name}<{args}>` + `native_cpp_names` path as a user record, so the
      spelling invariant holds; `builtin_type_key` keys TypeDef payload
      dispatch, never body-position rendering. A formatter-carrying builtin
      (`list` -> `std::vector`) has a different C++ shape entirely and is
      excluded by the formatter check, not by name."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = t.wrapped
    if not isinstance(t, NominalType):
        return False
    is_builtin_record = False
    if not t.is_user_record:
        td = type_def_of(t)
        if (td is None or td.record is None or td.cpp_formatter is not None
                or td.is_compile_time_only):
            return False
        is_builtin_record = True
    if t.type_args and not all(
            _f1_record_type_arg_ok(a, analyzer) for a in t.type_args):
        return False
    if analyzer.registry.get_record_for_type(t) is None:
        return False
    return not is_builtin_record or _witness("recv.builtin_record")

def _protocol_subscript_recv(recv: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """A bare protocol-typed NAME receiver whose `__getitem__` resolves to
    the shared checked-dunder protocol template (get_type_method_fi's
    fallback: `::tpy::__getitem__({self}, {0})`) -- the Sequence-family
    subscript inside the concept-bounded template. A protocol whose own
    registered `__getitem__` carries a template/native render keeps
    rejecting (that fi would render differently) -- verified UNREACHABLE
    today (sema rejects @cpp_template on protocol methods; no lib protocol
    carries one), so the arm guards a future lib protocol only."""
    if not (isinstance(recv, TpyName) and recv.name in declared):
        return False
    pb = _protocol_binding(declared[recv.name])
    if pb is None or is_dyn_protocol(pb):
        return False
    ri = analyzer.registry.get_record_for_type(pb)
    if ri is not None:
        for fi in ri.get_method_overloads("__getitem__"):
            if fi.cpp_template or fi.native_function or fi.native_name:
                return False
    return bool(get_dunder_cpp_template("__getitem__"))


def _protocol_binding(t: 'TpyType | None') -> 'NominalType | None':
    """The protocol a bare protocol-typed binding names, or None.

    Both flavors bind as a C++ REFERENCE -- a structural protocol param is the
    template `T_p&` / `const T_p&`, a @dynamic one the abstract `Base&` -- so a
    bare name reads bare and a method call takes the `.` accessor, exactly like
    an F1-record binding. `Own[P]` is deliberately NOT unwrapped: it lowers to
    `std::unique_ptr<P>` (structural: a `T_p&&` forwarding ref), whose method
    calls render `->` and whose reads move.
    """
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return u if is_protocol_type(u) else None

def _bounded_tparam_protocol(t: 'TpyType | None',
                             bounds: 'dict | None') -> 'NominalType | None':
    """The protocol BOUND of a TYPE-kind type-param binding (`item: T` under
    `[T: Stringable]`), or None. Inside the template such a receiver behaves
    exactly like a structural-protocol binding: the AST's user-record guard
    skips a TypeParamRef, so the call renders the bare member over the free
    `_args()` loop -- the same emit `_protocol_method_call_supported`
    mirrors. Sema does not stamp bounds on expression-type TypeParamRefs,
    so `bounds` is the in-scope name->bound dict (`lc.tparam_bounds`, the
    AST's `current_type_param_bounds` mirror); a stamped `t.bound` wins.
    A marker-only bound (Send/Sync) IS a protocol and resolves here, but
    carries no methods -- sema rejects any method call against it, so no
    valid body reaches the method checker through one."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(u, TypeParamRef) or u.kind is not TypeParamKind.TYPE:
        return None
    b = u.bound
    if b is None and bounds:
        b = bounds.get(u.name)
    return b if b is not None and is_protocol_type(b) else None

def _protocol_arg_slot(ptype: 'TpyType | None') -> 'NominalType | None':
    """The protocol a call-arg SLOT names, when its arg render is one this
    slice reproduces, else None.

    Admits a bare (or `readonly[P]` / `Send[P]`) protocol slot -- @dynamic
    (`_gen_dynamic_protocol_arg`) or single required structural (which
    `_gen_protocol_arg` explicitly hands back to `gen_call_arg`). Rejects
    `Own[P]` (`::tpy::make_adapter<Base>` / `std::make_unique`) and the
    `Optional[P]` / protocol-union slots, whose `_gen_protocol_arg` renders
    (typed null, `&(...)` address-of, `optional_to_ptr`) are their own rung.

    An `Own[...]` TYPE ARG also rejects: `Iterable[Own[T]]` is a `T_p&&`
    forwarding-ref slot, and `gen_call_arg` -- not the protocol pre-arms --
    rewrites a last-use arg into the consuming `::tpy::own_iter(std::move(x))`.
    """
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(ptype))
    if isinstance(u, OwnType) or not is_protocol_type(u):
        return None
    if any(isinstance(t, OwnType) for t in u.type_args):
        return None
    return u

def _protocol_arg_temp(proto: 'NominalType', arg_type: 'TpyType | None',
                       arg_cpp_type: str, analyzer, *,
                       rvalue: bool) -> 'tuple[str | None, bool] | None':
    """The `(cpp_type, brace_init)` of the `__tmp_N` a protocol slot hoists for
    this arg, or None when the arg passes bare.

    Mirrors `_gen_dynamic_protocol_arg` / the free-call `is_ref_param() +
    is_temporary_expr` temp arm. `arg_cpp_type` is the arg's rendered concrete
    C++ spelling (the caller renders it; the gate never needs the string):

    - the arg is already protocol-typed -> bare (`gen_expr_deref` forwards);
    - @dynamic slot, INHERITANCE conformer (the C++ struct derives from the
      abstract base): an lvalue binds `Base&` directly -> bare; an rvalue
      materializes the concrete `Dog __tmp_N{Dog()};`
    - @dynamic slot, STRUCTURAL conformer (no C++ base): always a temp -- an
      owning `::tpy::Adapter<Base, C> __tmp_N{C()};` for an rvalue, a
      zero-copy `::tpy::RefAdapter<Base, C> __tmp_N{p};` for an lvalue;
    - structural slot: an lvalue deduces `T_p` from the arg -> bare; an rvalue
      hoists the un-spelled `auto __tmp_N = C(...);` (a braced-init-list cannot
      deduce a template param, and the temp must outlive the call).
    """
    if is_protocol_type(arg_type):
        return None
    if not is_dyn_protocol(proto):
        return (None, False) if rvalue else None
    if record_inherits_dynamic(arg_type, proto, analyzer.registry):
        return (arg_cpp_type, True) if rvalue else None
    wrap = dynamic_adapter_type if rvalue else dynamic_ref_adapter_type
    return (wrap(proto, arg_cpp_type, analyzer), True)

def _type_family_tag(t: 'TpyType | None', analyzer) -> str:
    """Coarse type-family tag for the fallback drilldown sub-classifiers
    (diagnostic labels only, never a gate/emit fact): one shared chain so
    the sig.param_type / call.arg_shape tallies name families consistently.
    Splits the record family by F1 membership (an F1 record here means the
    blocker is elsewhere in the signature/args, not the record spelling)."""
    if t is None:
        return "untyped"
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        return "own_" + _type_family_tag(u.wrapped, analyzer)
    if isinstance(u, TypeParamRef):
        return "generic"
    if isinstance(u, OptionalType):
        return "optional"
    if isinstance(u, UnionType):
        return "union"
    if isinstance(u, TupleType):
        return "tuple"
    if isinstance(u, PtrType):
        return "ptr"
    if (_eligible_scalar(u) or is_char_type(u) or is_big_int_type(u)
            or is_float_type(u) or is_bool_type(u)):
        # An admitted-family slot: the blocker is the ARG shape (or a
        # sibling slot), not the slot's type family.
        return "scalar"
    if is_list(u) or is_dict(u) or is_set(u) or is_array(u):
        return "container"
    if is_span(u):
        return "span"
    if is_enum_type(u):
        return "enum"
    if is_str_type(u) or is_str_view_type(u) or is_string_type(u):
        return "str"
    if is_bytes_type(u) or is_bytes_view_type(u):
        return "bytes"
    if is_protocol_type(u):
        # The two protocol flavors take different SIGNATURE emits (a
        # monomorphized `T_p&` template param vs a `Base&` vtable ref), so the
        # drill ranks them apart even though their BODY renders coincide.
        return "protocol.dyn" if is_dyn_protocol(u) else "protocol.static"
    if isinstance(u, NominalType):
        if _f1_record(u, analyzer):
            return "record_f1"
        ri = analyzer.registry.get_record(u.name)
        if ri is not None and getattr(ri, "is_native", False):
            return "record_native"
        return "record_nonf1"
    # Self-describing residue: the type-class name keeps the catch-all
    # drillable without a new arm per exotic type.
    return "other_" + type(u).__name__.lower()

def _record_borrow_return(t: TpyType | None, analyzer) -> 'NominalType | None':
    """The borrow-form F1-record return slot (`-> Box` -> C++ `Box&` /
    `const Box&`), or None. `Own[record]` is the storage (by-value) direction
    (`_record_storage_return`) -- checked before `_f1_record`'s own Own-unwrap
    can admit it."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType) or not isinstance(t, NominalType):
        return None
    return t if _f1_record(t, analyzer) else None

def _eligible_ptr_value(t: 'TpyType | None', analyzer) -> bool:
    """A `Ptr[T]` value (`T*` by value -- copied around like a scalar),
    admitted at the value slots (param / return / field-read result) when the
    pointee spells byte-identically on both paths: an F1-record (native /
    cross-module included via native_cpp_names), an eligible scalar, Char,
    void (`Ptr[None]` -> `void*`), or a @dynamic-protocol base (`Ptr[
    _RcCellBase]` -> `_RcCellBase*`); `Ptr[readonly[T]]` -> `const T*` rides
    the same arms. The slice renders only bare passes and field reads -- a
    MEMBER access THROUGH the Ptr takes the AST's `::tpy::deref_check(p)`
    non-null render (or the proven `->`), a mirror the Ptr-receiver
    method-call arm carries; other deref shapes fall back on their own arms.

    The dyn-protocol arm needs no per-flavor spelling check: BOTH paths
    spell a Ptr type through the one `PtrType.to_cpp()` (codegen's
    `type_to_cpp` has no PtrType arm and falls through to it; THIR's
    `render_type` IS `type_to_cpp`), so the pointee reads the same
    `NominalType.to_cpp()` on both -- bare local name, cross-module and
    @native(cpp_concept) protocols via the generator's `native_cpp_names`
    registration. `get_dynamic_base_name` (bare-protocol locals / adapters)
    is never consulted for a Ptr pointee. A STRUCTURAL protocol pointee has
    no runtime C++ type at all (`to_cpp()` is the monomorphization
    placeholder `T`) and stays excluded."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, PtrType):
        return False
    inner = t.inner_pointee
    if is_dyn_protocol(inner):
        return _witness("ptr.value_slot") and _witness("ptr.dyn_proto_pointee")
    # A TypeParamRef pointee (`Ptr[T]` inside a generic body) spells the bare
    # param name on both paths (`T*` -- the _f1_record_type_arg_ok rule), so
    # the value slice is pointee-blind here like everywhere else.
    return ((is_void_like_type(inner) or _eligible_scalar(inner)
             or _eligible_char(inner) or _is_type_param_slot(inner)
             or _f1_record(inner, analyzer))
            and _witness("ptr.value_slot"))

def _dyn_proto_ptr(t: 'TpyType | None') -> bool:
    """A `Ptr[T]` whose pointee is a @dynamic protocol. The one Ptr-value
    flavor whose LOCAL DECL the AST spells `auto` (`_cpp_decl_type`'s
    contains_protocol_type arm recurses through the pointee), unlike the
    record/scalar pointees' spelled `T*` -- lowering mirrors the `auto` and
    the None-init first decl rejects (see the decl gate)."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(t, PtrType) and is_dyn_protocol(t.inner_pointee)

def _ptr_value_field_recv_ok(e: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """A `.field` access whose receiver evaluates to an explicit `Ptr[record]`
    VALUE -- a local/param NAME (`p.x` on `p: Ptr[Point]`) or an F1-record's
    `Ptr` FIELD read (`m._a.x` on `_a: Ptr[A]`): _gen_field_access's pointer
    arm renders `<recv>->x` when sema proved the pointer non-null
    (`ptr_non_null`) else `::tpy::deref_check(<recv>).x`. A NAME receiver must
    be bound to an eligible `Ptr[F1-record]` (globals take the `(*g)->`
    wrapper arm, out of slice); a FIELD receiver must itself be an admitted
    F1-record field read whose value is such a Ptr (it renders bare, then this
    arm wraps it). The pointee's F1-ness makes the outer `.field` spell. Read
    AND write target alike -- the render is position-independent, picked at
    lowering from `ptr_non_null`.

    `deref_depth` (sema's auto-deref marker) IS set on a Ptr member access and
    is EXPECTED here -- the AST's `obj_type.is_pointer()` arm renders one `->`
    / `deref_check` depth-independently, ignoring the `.__deref__()` chain
    (that chain belongs to the user-Deref proxy, `not is_pointer()`). So the
    marker guard excludes only the other special-emit markers, NOT
    `deref_depth`."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if (e.module_var_access is not None or e.class_constant_owner is not None
            or e.property_getter_call is not None
            or e.dyn_getattr_call is not None
            or e.property_setter_call is not None
            or e.dyn_setattr_call is not None
            or e.unbound_self_parent_type is not None
            or e.deref_narrowed_to is not None
            or e.needs_optional_runtime_check):
        return False
    recv = e.obj
    if isinstance(recv, TpyName):
        if recv.name not in declared:
            return False
        rt = declared[recv.name]
    elif isinstance(recv, TpyFieldAccess):
        if not _field_receiver_ok(recv, declared, analyzer):
            return False
        rt = analyzer.get_expr_type(recv)
    else:
        return False
    if not _eligible_ptr_value(rt, analyzer):
        return False
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    if not isinstance(inner, PtrType):
        return False
    return _f1_record(inner.inner_pointee, analyzer)

def _user_deref_field_recv_ok(e: TpyExpr, declared: dict[str, TpyType],
                              narrowed: 'AbstractSet[str]', analyzer,
                              pointers: 'AbstractSet[str]') -> bool:
    """A `.field` access auto-dereffed through a USER Deref-style wrapper
    (`r.x` on `r: Ref` with a `__deref__` method) -> `r.__deref__().x`
    (_gen_field_access's deref_chain arm). The receiver is a NAME bound to an
    F1 user record: a plain value binding spells the bare `.` chain; a
    pointer-local (a PROVEN narrowed-Optional local or an F2-reseated `T*`)
    spells the indirect `recv->__deref__().field` -- the lowering keys the
    first hop on the pointer set like the method twin. NOT
    isinstance-narrowed; the record must carry a `__deref__` overload.
    `deref_depth` is the auto-deref count (the chain length);
    `deref_narrowed_to` (a deref-view cast) stays AST. Read AND
    scalar-write target alike -- the chain render is position-independent."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if not e.deref_depth or e.deref_narrowed_to is not None:
        return False
    if (e.module_var_access is not None or e.class_constant_owner is not None
            or e.property_getter_call is not None
            or e.dyn_getattr_call is not None
            or e.property_setter_call is not None
            or e.dyn_setattr_call is not None
            or e.unbound_self_parent_type is not None
            or e.needs_optional_runtime_check):
        return False
    recv = e.obj
    u = _deref_wrapper_receiver_record(recv, declared, narrowed, pointers,
                                       analyzer)
    if u is None:
        return False
    ri = analyzer.registry.get_record_for_type(u)
    return ri is not None and bool(ri.get_method_overloads("__deref__"))


def _deref_wrapper_receiver_record(recv: TpyExpr,
                                   declared: dict[str, TpyType],
                                   narrowed: 'AbstractSet[str]',
                                   pointers: 'AbstractSet[str]',
                                   analyzer) -> 'NominalType | None':
    """The user-Deref twins' shared receiver resolution: an in-scope,
    non-isinstance-narrowed NAME whose binding unwraps to an F1 user record
    -- a plain value binding (bare `.` chain), or a PROVEN Optional-ptr
    local (`r: Ref | None` narrowed non-None, in `pointers` -- the lowering
    spells the `->` first hop off the pointer set), whose wrapper record is
    the Optional's inner. None outside the slice."""
    if not isinstance(recv, TpyName) or recv.name not in declared:
        return None
    if recv.name in narrowed:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[recv.name])))
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if isinstance(u, OptionalType):
        if recv.name not in pointers or not u.uses_pointer_repr():
            return None
        u = unwrap_readonly(_unwrap_own(u.inner))
    if not (isinstance(u, NominalType) and _f1_record(u, analyzer)):
        return None
    return u

def _typed_dict_recv_ok(obj: TpyExpr, declared: dict[str, TpyType],
                        pointers: 'AbstractSet[str]',
                        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """The typed-dict subscript RECEIVER set, shared by the read arm and the
    write-target gate so the two cannot drift: a bare declared NAME (not a
    pointer-local / narrowed -- those read through AST-side unwraps), or a
    one-level FIELD off an admitted binding (`p.addr["city"]`)."""
    if isinstance(obj, TpyName):
        return (obj.name in declared and obj.name not in pointers
                and obj.name not in narrowed)
    return (isinstance(obj, TpyFieldAccess)
            and _field_receiver_ok(obj, declared, analyzer))

def _user_deref_stub_method_ok(e: TpyExpr, declared: dict[str, TpyType],
                               narrowed: 'AbstractSet[str]', analyzer,
                               pointers: 'AbstractSet[str]') -> bool:
    """A container-stub MEMBER method call through a USER Deref wrapper
    (`g.append(4)` on a Mutex guard over a list ->
    `g.__deref__().push_back(4)`): the deref receiver shape of
    `_user_deref_method_call_ok`, but the fi is the payload container's
    MEMBER-rename native (`@native("push_back")` / bare `clear`). The
    THIR deref emit spells `recv.__deref__()...member(args)` exactly;
    function=True natives and cpp_template stubs thread the receiver
    through a symbol/template slot the deref branch cannot reach -> AST."""
    if not isinstance(e, TpyMethodCall):
        return False
    if not e.deref_depth or e.deref_narrowed_to is not None:
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.is_static_call or e.super_parent_type is not None
            or e.unbound_self_parent_type is not None
            or e.user_module_call is not None
            or e.builtin_module_call is not None
            or e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None or e.type_args
            or e.inferred_type_args or e.needs_optional_runtime_check):
        return False
    fi = e.resolved_function_info
    if fi is None:
        return False
    if not (fi.native_name and not fi.native_function
            and fi.cpp_template is None and not fi.type_params):
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in declared:
        return False
    if recv.name in narrowed:
        return False
    # A POINTER-local receiver is admitted: the lowering sets is_arrow and
    # the deref emit joins the first hop with `->`
    # (`g->__deref__().push_back(3)` -- the Arc-chained guard target).
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[recv.name])))
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if not (isinstance(u, NominalType) and _f1_record(u, analyzer)):
        return False
    ri = analyzer.registry.get_record_for_type(u)
    return ri is not None and bool(ri.get_method_overloads("__deref__"))

def _user_deref_method_call_ok(e: TpyExpr, declared: dict[str, TpyType],
                               narrowed: 'AbstractSet[str]', analyzer,
                               pointers: 'AbstractSet[str]') -> bool:
    """A method call auto-dereffed through a USER Deref-style wrapper
    (`r.sum()` on `r: Ref` with `__deref__`) -> `r.__deref__().sum()`
    (_gen_method_call's deref_chain arm; N = deref_depth). The method arm
    (plain member, no marker) mirrors the field read's
    `_user_deref_field_recv_ok`: a NAME bound to an F1 user record with a
    `__deref__` overload -- a plain value binding spells the bare `.` chain,
    a pointer-local (proven narrowed-Optional / F2-reseated) the indirect
    `recv->__deref__()` first hop; isinstance-narrowed stays AST. The
    called method's fi is a plain user method on the DEREFFED type; every
    special-emit marker (static/module/template/native/type-args/nested/
    optional-check) stays AST."""
    if not isinstance(e, TpyMethodCall):
        return False
    if not e.deref_depth or e.deref_narrowed_to is not None:
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.is_static_call or e.super_parent_type is not None
            or e.unbound_self_parent_type is not None
            or e.user_module_call is not None
            or e.builtin_module_call is not None
            or e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None or e.type_args
            or e.inferred_type_args or e.needs_optional_runtime_check):
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi):
        return False
    if (fi.cpp_template is not None or fi.native_function or fi.native_name
            or fi.type_params or fi.linkage != FunctionLinkage.DEFAULT):
        return False
    recv = e.obj
    u = _deref_wrapper_receiver_record(recv, declared, narrowed, pointers,
                                       analyzer)
    if u is None:
        return False
    ri = analyzer.registry.get_record_for_type(u)
    return ri is not None and bool(ri.get_method_overloads("__deref__"))

def _record_storage_return(t: TpyType | None, analyzer) -> 'NominalType | None':
    """The storage-form F1-record return slot (`-> Own[Box]` -> C++ `Box` by
    value), or None. Bare name sources return bare (`return b;` -- NRVO for an
    owned local, C++ implicit move for an `Own` rvalue-ref param; a borrowed
    source without copy() is a sema error, so no copy shape arises); a
    record-rvalue ctor / by-value call returns its bare expansion
    (`return Box(n);`)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, OwnType):
        return None
    inner = unwrap_readonly(t.wrapped)
    if not isinstance(inner, NominalType):
        return None
    return inner if _f1_record(inner, analyzer) else None

def _unwrap_own(t: TpyType) -> TpyType:
    """The payload of an `Own[T]` wrapper, else `t` unchanged -- the recurring unwrap
    the `Optional`-inner helpers apply before an `_f1_record` check."""
    return t.wrapped if isinstance(t, OwnType) else t

def _res_container_return(t: TpyType | None, analyzer) -> 'TpyType | None':
    """The RESUMABLE container return slot (`Poll<std::vector<T>>` /
    `expected<std::vector<T>, StopIteration>`), or None. Wider than the sync
    `_container_storage_return` at the `Own` axis: a coroutine's return slot
    holds `T` by value whether or not the signature spells `Own`, so a bare
    `-> list[T]` (reachable when the value comes from an await, the only
    source sema admits without `Own`) shares the render. The value rides the
    position-blind tail -- the scaffolding's `{ret_cpp} __tpy_async_ret =
    <value>;` decl supplies the type, so names/calls/literals emit bare.

    That plain-`T` binding is also why a container return COPIES where CPython
    aliases (the reference-type divergence `_res_capture_ok`'s RETURN bullet
    records against BUGS.md). Pre-existing and byte-identical, so routing these
    shapes does not change it -- but when the fix routes returns through
    `val_or_ref_t<T>`, re-check THIS arm together with the capture arm and the
    return leaf, exactly as that bullet instructs."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return t if (is_list(t) or is_dict(t) or is_set(t)) else None

def _container_storage_return(t: TpyType | None, analyzer) -> 'TpyType | None':
    """The storage-form container return slot (`-> Own[list[T]]` -> C++
    `std::vector<T>` by value), or None: `Own` wrapping list / dict / set
    (`Array` is a value type -- no Own return slot arises for it). Bare owned
    container names return bare (NRVO / implicit move, like
    `_record_storage_return`); container literals render position-independently
    (the decl-init brace-init / spelled-ctor emits verbatim, including the
    empty-list `std::vector<T>{}` spell -- probe-verified at the return slot).
    Element families are bounded at the return ARM per source shape (a literal
    re-checks the decl gate's element slice; a bare name spells nothing), not
    here."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, OwnType):
        return None
    inner = unwrap_readonly(t.wrapped)
    return inner if (is_list(inner) or is_dict(inner) or is_set(inner)) else None

def _container_borrow_return(t: TpyType | None) -> 'TpyType | None':
    """The BORROW-form container return slot (`-> list[T]` / `-> readonly[
    list[T]]` -- C++ `std::vector<T>&` / `const std::vector<T>&`), or None.
    A bare container NAME (`return cells;`) and a plain FIELD read
    (`return self._items;`) return bare on both paths -- element-blind (no
    per-element conversion happens at a whole-container borrow return).
    The Own axis is `_container_storage_return`'s."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        return None
    return u if (is_list(u) or is_dict(u) or is_set(u)) else None

def _plain_container_read(t: TpyType | None) -> bool:
    """A plain (non-`Own`) list/dict/set read at a position where the bare
    read IS the whole render -- the for-head, whose begin()/end() are taken
    off it directly, and the `std::ranges::contains` haystack. `Own` is
    excluded: a consuming iteration moves the container
    (`own_iter(std::move(..))`), a different render."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return (not isinstance(u, OwnType)
            and (is_list(u) or is_dict(u) or is_set(u)))

def _own_storage_viewfam_return(t: TpyType | None, analyzer) -> 'TpyType | None':
    """The resolved owned str/bytes family behind an `Own[str]` / `Own[bytes]`
    return slot, or None. The slot spells the same owned storage type the bare
    `-> str` / `-> bytes` slot does (`std::string` / `std::vector<uint8_t>`),
    so the S1/S6 return arms (bare owned-name render, view->owned convert,
    owned bytes-literal render) apply unchanged; only the Own unwrap is new.
    View inners (`Own[StrView]`) never arise -- sema rejects Own over a view."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, OwnType):
        return None
    inner = unwrap_readonly(t.wrapped)
    st = _resolved_str_value(inner, analyzer)
    if st is not None and is_str_type(st):
        return st
    bt = _resolved_bytes_value(inner, analyzer)
    if bt is not None and is_bytes_type(bt):
        return bt
    return None

def _call_ret_union_ok(ret: 'TpyType | None', analyzer) -> bool:
    """A union-returning call landing bare in a same-union STORAGE sink:
    a ptr-variant return (`std::variant<monostate, A*, B*>` by value) or
    an `Own[union]` factory's storage variant -- no per-member conversion
    fires on either path."""
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if not isinstance(t, UnionType):
        return False
    return (_eligible_value_union(t) is not None
            or _eligible_ptr_union(t, analyzer) is not None)


def _optional_ptr_borrow(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The pointer-repr `Optional[F1-record]` BORROW binding type -- the C++
    shape of an `A | None` param or an OPTIONAL_TO_PTR local (a bare
    `A*` / `const A*`), or None. An own-optional (`Optional[Own[A]]` /
    `Own[A | None]`) is storage-repr (`std::optional<A>`) and excluded --
    the OwnType check is defensive on top of `uses_pointer_repr` (an Own
    inner must never slip in via `_f1_record`'s own Own-unwrap)."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    if isinstance(inner, OwnType):
        return None
    return t if _f1_record(inner, analyzer) else None

def _optional_ptr_borrow_name(e: TpyExpr, declared: dict[str, TpyType],
                              analyzer) -> 'OptionalType | None':
    """`e` is a bare name whose DECLARED type is a pointer-repr
    `Optional[F1-record]` -- an `A | None` param or an OPTIONAL_TO_PTR local,
    both borrow `A*` bindings (a reassigned Optional local classifies OTHER
    at its decl, so no other Optional-ptr name can appear in a routed body).
    Keyed on the declared type, not the flow-narrowed expr type: the AST's
    None-test and field/method dispatch key on the C++ binding shape, which
    narrowing does not change."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return None
    return _optional_ptr_borrow(declared[e.name], analyzer)

def _ptr_value_none_name(e: TpyExpr, declared: dict[str, TpyType],
                         analyzer) -> bool:
    """`e` is a bare name whose DECLARED type is an eligible `Ptr[T]` VALUE
    (a nullable raw pointer). Its `is [not] None` test renders the AST's
    type-agnostic pointer compare `(p != nullptr)` / `(p == nullptr)` (the
    _gen_binop identity fallback -- a PtrType is neither Optional nor union),
    the non-null narrowing riding the deref sites, not this test."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return False
    return _eligible_ptr_value(declared[e.name], analyzer)

def _value_opt_scalar(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[value scalar]` binding type -- an `Int32 | None`
    / `bool | None` / `Char | None` / `float | None` / `int | None` param bound
    `std::optional<T>` by value, or None. The inner is a value scalar: a
    fixed-int, bool, Char, either float width, BigInt, or a registered enum
    (`std::optional<Color>` -- the same bare/un-narrowed and `(*p)`-narrowed
    renders as the fixed-int inners). A BigInt (expensive-
    copy) inner takes a `std::move((*p))` at its last narrowed read (the
    `seed_param_locals` movable face, mirrored via the context.py movable
    seeding + the return/container move sinks). Str/bytes views are excluded
    (the `optional<string_view>` vs `optional<string>` ARG split needs the
    `_maybe_convert_opt_view_param` shim). Own-optional (`Own[X] | None`) rides
    the Own axis in `_unrouted_binding_read`."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    if isinstance(inner, OwnType):
        return None
    return t if (_eligible_scalar(inner) or _eligible_char(inner)
                 or _eligible_enum(inner, analyzer) is not None) else None

def _value_opt_callable(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[Callable]` binding type -- a
    `Callable[...] | None` param bound `std::optional<std::function<...>>`
    by value, or None. Non-template callables only (`_callable_value`); an
    `Fn` template slot has no value binding. The routed reads are the WHOLE-
    optional ones (a bare pass into a matching value-opt slot); a NARROWED
    read (`cb(x)` under `cb is not None`) derefs on the AST path and is
    guarded per-use at name lowering."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    return t if _callable_value(t.inner) else None

def _value_opt_scalar_name(e: TpyExpr, declared: dict[str, TpyType],
                           analyzer) -> 'OptionalType | None':
    """`e` is a bare name whose DECLARED type is a value-repr `Optional[scalar]`
    -- a value-optional-scalar param (a value-optional LOCAL classifies OTHER at
    its decl, so only params qualify). Keyed on the declared type, not the
    flow-narrowed read type: the None-test / truthiness dispatch keys on the
    `std::optional<T>` binding shape, which narrowing does not change."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return None
    return _value_opt_scalar(declared[e.name], analyzer)

def _none_value_opt_arg(a: TpyExpr, ptype: 'TpyType | None',
                        analyzer) -> 'OptionalType | None':
    """A bare `None` literal into a value-repr Optional param slot
    (`std::optional<T>` by value -- ANY non-pointer-repr inner: scalar / Char /
    float / BigInt / str-view / bytes-view / value-tuple). All render
    `std::nullopt` (the STORAGE-form None) whatever the inner, so this one row
    covers every value-optional family -- the value-repr twin of the
    pointer-repr `_optional_ptr_arg` None face (which lifts `nullptr`). Keyed on
    the slot alone (a None literal carries no source type)."""
    if not isinstance(a, TpyNoneLiteral) or not isinstance(ptype, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if isinstance(t, OwnType):
        # An `Own[T | None]` STORAGE slot (`std::optional<T>&&` -- a moved
        # @dataclass Optional field): the Own wrapper forces value/storage
        # repr, so None renders `std::nullopt` whatever the inner. A
        # pointer-repr record inner still stores `std::optional<record>`
        # here, unlike a bare `record | None` param (`record*` -> nullptr).
        inner = unwrap_readonly(unwrap_send_sync(t.wrapped))
        return inner if isinstance(inner, OptionalType) else None
    return t if isinstance(t, OptionalType) and not t.uses_pointer_repr() else None

def _value_opt_scalar_value_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A scalar VALUE arg into a value-repr `Optional[scalar]` slot
    (`a.settimeout(0.5)` into `float | None`): both paths render the arg
    bare -- the implicit `std::optional<T>` ctor absorbs the value, and the
    user-record method loop renders numeric literals target-less on both
    sides. The arg's own type must be non-Optional (a whole-optional pass is
    the binding row in _lower_call_arg); a `None` literal rides
    `_none_value_opt_arg`; a str literal is excluded (the Char-literal
    render keys on `_eligible_char(ptype)`, which an Optional slot fails,
    so the two paths would diverge)."""
    if _value_opt_scalar(ptype, analyzer) is None:
        return False
    # A scalar literal into a fixed-int/enum value-opt slot arrives wrapped in
    # a TpyCoerce to the Optional target (the implicit widening); the AST
    # renders the bare source (`Counter(10, 4)`), so key on the source type.
    src = _peel_coerce(a)
    if isinstance(src, (TpyNoneLiteral, TpyStrLiteral)):
        return False
    at = analyzer.get_expr_type(src)
    if at is None or isinstance(unwrap_readonly(unwrap_send_sync(at)),
                                OptionalType):
        return False
    return bool(_resolved_scalar(at, analyzer) or _eligible_char(at)
                or _eligible_enum(at, analyzer) is not None)

def _whole_value_opt_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType],
                              narrowed: 'AbstractSet[str]', analyzer,
                              slot_check) -> bool:
    """Shared core of the whole value-opt NAME pass-through rows: an
    un-narrowed NAME whose DECLARED type is exactly the value-repr Optional
    slot passes BARE (`std::optional<T>` is a value type passed by value --
    no lift, no shim, no temp on either path). `slot_check` picks the
    admitted inner family; the exact-slot pin stays because a differing
    inner would carry a conversion the bare render does not. NARROWED names
    are excluded (their read derefs); pointer-repr Optionals keep
    `_optional_ptr_arg`'s lift and the `None` literal its own row."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if slot_check(slot, analyzer) is None:
        return False
    if not isinstance(a, TpyName) or a.name in narrowed or a.is_function_ref:
        return False
    at = locals_.get(a.name)
    if at is None:
        at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if isinstance(at, TpyType) else None)
    return at == slot

def _value_opt_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                                locals_: dict[str, TpyType],
                                narrowed: 'AbstractSet[str]',
                                analyzer) -> bool:
    """The SCALAR-inner row (`eq_left(some_a, a)` at a `Char | None` param):
    scoped so admission matches the lowering row that renders it
    (`_value_opt_scalar_binding` in `_lower_call_arg`); the str/bytes inners
    have their own shim rows and would only be admitted here to reject again
    inside lowering."""
    return _whole_value_opt_name_arg(a, ptype, locals_, narrowed, analyzer,
                                     _value_opt_scalar)

def _value_opt_callable_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 locals_: dict[str, TpyType],
                                 narrowed: 'AbstractSet[str]',
                                 analyzer) -> bool:
    """The CALLABLE-inner row (`os.walk(top, onerror=cb)` at a
    `Callable[..] | None` param)."""
    return bool(_whole_value_opt_name_arg(a, ptype, locals_, narrowed,
                                          analyzer, _value_opt_callable)
                and _witness("arg.value_opt_callable"))

def _value_opt_view_whole_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType],
                              narrowed: 'AbstractSet[str]',
                              analyzer) -> bool:
    """The VIEW-inner row, RECORD-METHOD position only (`conn.request(m, u,
    body, hdrs)` at `body: bytes | None`): the user-record method loop is
    TARGET-LESS (target_type=None), so `_maybe_convert_opt_view_param`'s arg
    split never fires and the AST passes the whole optional BARE -- unlike
    the free-call position, whose slot threading takes the shim
    (`_opt_view_arg_shim`). The free ladder keeps its shim rows, and the
    stub loops DO thread the raw param (see the shim arm's exclusion note in
    `_lower_call_arg`), so the render arm also keys off `method_arg_stub`."""
    return _whole_value_opt_name_arg(a, ptype, locals_, narrowed, analyzer,
                                     _value_opt_view)

def _str_literal_value_opt_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A str LITERAL into a value-repr `Optional[str]` slot
    (`Info("Alice")` into `str | None` -- the total=False TypedDict ctor
    face): both paths render the bare literal; C++'s implicit
    `const char*` -> `optional<string>` chain absorbs it. Owned-str inner
    only: a view inner (`optional<string_view>`) takes the ARG-split shim,
    and non-literal sources need the view->owned wrap."""
    if not isinstance(a, TpyStrLiteral):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if not (isinstance(pt, OptionalType) and not pt.uses_pointer_repr()):
        return False
    inner = unwrap_readonly(pt.inner)
    return isinstance(inner, NominalType) and is_str_type(inner)

def _unrouted_binding_read(t: 'TpyType | None', analyzer) -> 'str | None':
    """A declared binding kind whose bare NAME read has no THIR arm -- reachable
    through function and constructor parameters (no local-decl arm produces such
    a binding). Actual uses decide whether the body routes. Returns the reject
    detail, or None for every binding the slice routes today:

    - a VALUE-repr Optional whose inner the scalar slice does not admit (a
      str/bytes view -- the `optional<string_view>`/`optional<string>` ARG
      split): the AST renders a narrowed read `(*p)` and the view->owned copy,
      not mirrored (the eligible scalar / BigInt inners route via
      `_value_opt_scalar`);
    - an `Own[...]` whose payload has no routed read arm (anything but a
      TypeParamRef / F1-record / eligible ptr-union, or an Optional of
      those): `seed_param_locals` marks such a param movable, so the AST's
      last-use read renders `std::move(p)` -- not mirrored. The ctor MIL move
      arm is unaffected (it lowers the source name directly, not through the
      expr gate)."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if ((isinstance(u, OwnType) and isinstance(u.wrapped, OptionalType))
            or (isinstance(u, OptionalType)
                and isinstance(u.inner, OwnType))):
        return "name.own_optional_read"
    own = unwrap_optional_own(u)
    if own is not None:
        inner = own.wrapped
        if isinstance(inner, OptionalType):
            inner = inner.inner
        inner = unwrap_readonly(inner)
        if (isinstance(inner, TypeParamRef)
                or _f1_record(inner, analyzer)
                or _eligible_ptr_union(inner, analyzer) is not None):
            return None
        return "name.own_read"
    if isinstance(u, OptionalType) and not u.uses_pointer_repr():
        # A value-repr Optional[scalar] param routes: an un-narrowed read
        # renders the bare optional (`p`, into an optional slot), a narrowed
        # read the value unwrap `(*p)` (deref-on-narrow at name lowering); an
        # expensive-copy (BigInt) inner moves `std::move((*p))` at its last use.
        # A value-repr Optional[str] param routes its narrowed read too (`(*s)`,
        # a borrow string_view); the un-narrowed value read rejects during name
        # lowering, so this predicate must not blanket-reject it here.
        # Own-optional inners still reject (Own-axis faces not mirrored).
        if (_value_opt_scalar(u, analyzer) is not None
                or _value_opt_view(u, analyzer) is not None
                # A value-repr Optional[Callable] param routes its WHOLE-
                # optional reads (bare into a matching value-opt slot); the
                # narrowed read derefs on the AST path and is guarded per-use
                # at name lowering (name.optcallable_narrowed_read).
                or _value_opt_callable(u, analyzer) is not None):
            return None
        return "name.optval_read"
    if (isinstance(u, OptionalType) and u.uses_pointer_repr()
            and _optional_ptr_borrow(u, analyzer) is None):
        return "name.optional_ptr_read"
    return None

def _storage_optional_return_type(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The storage-form `Optional[F1-record]` return slot (F2c): `Own[T] | None`,
    which lowers to a `std::optional<T>` returned by value. `Inner | None` is
    pointer-repr (the function returns a borrow `Inner*`, a different direction)
    and is excluded -- it stays on the AST path. The caller passes None for a
    non-`TpyType` (unresolved) return annotation."""
    if not isinstance(t, OptionalType) or t.uses_pointer_repr():
        return None
    return t if _f1_record(_unwrap_own(t.inner), analyzer) else None

def _f1_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """A tuple element that renders byte-identically off the F1 slice: an eligible
    value scalar (`T`, same in both forms), an F1-record (BORROW_REF: `T*` borrow /
    `T` storage), or a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL: `T*` borrow
    / `std::optional<T>` storage). Each keeps `to_cpp_return()` / `to_cpp()`
    recursion off cross-module / native / generic / pending types, where bare
    `to_cpp()` would mis-spell. Union / container / generic elements ride later
    rungs."""
    if _eligible_scalar(e) or _f1_record(e, analyzer):
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if isinstance(inner, OptionalType) and inner.uses_pointer_repr():
        return _f1_record(_unwrap_own(inner.inner), analyzer)
    return False

def _f1_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A pointer-repr tuple whose every element is F1-renderable -- the F3 tuple:
    borrow form `std::tuple<..., T*>` differs from storage form
    `std::tuple<..., std::optional<T>>` / `std::tuple<..., T>`, so a storage source
    lifts via `tuple_to_pointer` and a borrow source stores via `tuple_to_storage`.
    `has_pointer_repr_element` ensures the two forms genuinely differ (an all-value
    tuple needs no conversion). Other tuples stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or not t.has_pointer_repr_element():
        return None
    if not all(_f1_tuple_element_ok(e, analyzer) for e in t.element_types):
        return None
    return t

def _borrow_tuple_return_type(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The function's borrow-form pointer-repr tuple return slot (F3): a
    `tuple[..., Ref]` returned as `std::tuple<..., T*>`, into which a `return
    <storage tuple lvalue>` lifts via `tuple_to_pointer`."""
    return _f1_tuple(t, analyzer)

def _is_borrow_form_name(t: TpyType | None) -> bool:
    """Whether a bare name read renders in borrow form: a non-value type (record /
    Optional / etc. -- a pointer / reference) or a pointer-repr tuple (`std::tuple<
    ..., T*>`, value-typed yet with distinct borrow and storage forms). Used to keep
    a THIRName's form tag honest so a convert source is never mislabeled VALUE.

    This is the type-level stand-in for codegen's `local_cpp_form` ladder, which
    keys on BINDING-set membership; it agrees only for the rungs whose set the
    name-read arm has already consulted. Precondition: callers must first exclude
    a STORAGE-form pointer-repr tuple (an F3 `auto&&` alias local), which has the
    same type but reads as STORAGE -- this query keys on the type alone and would
    mistag it BORROW. The name-read call site checks `storage_tuple_locals` (and
    the Own-param / value-opt / opt-record bindings) before falling through here.
    The other call site -- the
    `TpySubscript` branch tagging a subscript *result* -- is safe without that check
    because an admitted element is only ever a value scalar or a plain record, never
    itself a pointer-repr tuple (a nested-tuple element is not in the admitted set), so
    the storage-alias ambiguity cannot arise there."""
    if t is None:
        return False
    if not t.is_value_type():
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(inner, TupleType) and inner.has_pointer_repr_element()

def _value_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """The narrow value-tuple element: an eligible value scalar, an owned-str
    slot, or an `Any` cell. All read bare in every sink (a str element is an
    owned `std::string` lvalue, an Any element a `const ::tpy::Any&`), so a
    subscript read of such an element needs no lift."""
    return (_eligible_scalar(e) or _owned_str_slot(e, analyzer)
            # A UNIT element (`tuple[None, int]`) is `std::monostate` -- a
            # value type with no storage/borrow split, read bare like a
            # scalar.
            or is_void_like_type(e)
            or isinstance(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(e))), AnyType))

def _value_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The value tuple of scalar / owned-str elements (`tuple[int, bool]` /
    `tuple[str, int]`), or None: a value type rendered `std::tuple<...>`
    where borrow and storage forms coincide at the tuple level, so a
    subscript read of any element needs no lift (a str element is an owned
    `std::string` inside the tuple storage; its read is an owned lvalue --
    bare in every sink). Admitted at the param slot (`const std::tuple<...>&`,
    the signature staying on the AST path), the return slot (a by-value
    `std::tuple<...>`), and the subscript-read gate. A view (`StrView`) /
    Char / record / nested-tuple element keeps the tuple outside this
    family: views make the literal render pin static storage, non-value
    elements make it pointer-repr (the `_f1_tuple` family)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    return t if all(_value_tuple_element_ok(e, analyzer)
                    for e in t.element_types) else None

def _value_opt_str(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[str]` type: `str | None` / `StrView | None`,
    bound `std::optional<std::string_view>` at the param boundary (borrow form)
    and an owned `std::optional<std::string>` elsewhere -- the ARG/non-ARG split
    the `_maybe_convert_opt_view_param` shim patches. Two consumers:

    - the tuple RETURN element (`_value_tuple_return_element_ok`): a str-view
      source wraps via `std::string(view)`, `None` renders `std::nullopt`, a
      str literal lands bare;
    - the value-repr `Optional[view]` PARAM read/None-test/truthiness/arg-shim
      slice (`_value_opt_view` / `_value_opt_view_name` / `_value_opt_view_param`,
      shared with the bytes twin `_value_opt_bytes`): a narrowed read unwraps
      `(*s)` (a borrow string_view), None-tests render `has_value()`, truthiness
      `is_truthy(s)`, and a pass into another `Optional[str]` slot takes the shim
      (`s ? std::make_optional(std::string(*s)) : std::nullopt`). The str return
      sink is `_value_opt_view`-routed; the decl/print sinks stay AST."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    inner = t.inner
    if _is_string_owned(inner):
        # `Optional[String]` is NOT part of this borrow/owned split: the AST
        # MOVES its narrowed deref at an owned sink (`return std::move((*x));`)
        # rather than taking the view->owned copy the family's emit spells.
        return None
    return t if _resolved_str_value(inner, analyzer) is not None else None

def _value_opt_bytes(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[bytes]` type: `bytes | None` / `BytesView | None`,
    bound `std::optional<std::span<const uint8_t>>` at the param boundary (borrow
    form) and an owned `std::optional<std::vector<uint8_t>>` elsewhere -- the
    bytes twin of `_value_opt_str`. Every position renders family-neutrally
    (`has_value()`, the narrowed `(*b)` span read, `::tpy::is_truthy(b)`) except
    the view->owned copy, which the view-family emit spells `::tpy::bytes_copy`
    instead of `std::string` (the arg-split shim / owned sinks). Consumed only at
    the family-NEUTRAL value-optional sites via `_value_opt_view`; the
    str-specific arms (if-expr str-result, `print_optional_val`, value-tuple
    element) stay `_value_opt_str`-keyed."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    return t if _resolved_bytes_value(t.inner, analyzer) is not None else None

def _value_opt_view(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """A value-repr `Optional[view]` -- str OR bytes -- the shared family-neutral
    predicate for the value-optional read / None-test / truthiness / arg-shim /
    param+return-gate sites. The view-family emit (`view_to_owned_conv`) resolves
    the `std::string` vs `::tpy::bytes_copy` split, so these sites need no
    per-family branch."""
    return (_value_opt_str(t, analyzer)
            or _value_opt_bytes(t, analyzer))

def _value_opt_owned_view(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """A value-repr `Optional[view]` whose inner is an OWNED family (`str`/`bytes`,
    NOT `StrView`/`BytesView`) -- the shape a LOCAL binds `std::optional<std::string>`
    / `<std::vector<uint8_t>>` (owned inner), so its narrowed deref `(*acc)` is
    already OWNED (STORAGE). A view-INNER optional (`optional<string_view>`) is
    excluded: its LOCAL narrowed read stays on the str/bytes-name arm (no deref,
    matching the AST), so it must not enter `value_opt_view_locals`."""
    ov = _value_opt_view(t, analyzer)
    if ov is None:
        return None
    inner = unwrap_readonly(ov.inner)
    if is_str_view_type(inner) or is_bytes_view_type(inner):
        return None
    return ov

def _value_opt_view_name(e: TpyExpr, declared: dict[str, TpyType],
                         analyzer) -> 'OptionalType | None':
    """`e` is a bare name whose DECLARED type is a value-repr `Optional[view]`
    (str or bytes) -- a value-optional-view param (a value-optional LOCAL
    classifies OTHER at its decl, so only params qualify). Keyed on the declared
    type, not the flow-narrowed read type: the None-test / truthiness / arg-shim
    dispatch keys on the `std::optional<view>` binding shape, which narrowing
    does not change (the view twin of `_value_opt_scalar_name`)."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return None
    return _value_opt_view(declared[e.name], analyzer)

def _opt_view_arg_shim(src: 'TpyType | None', slot: 'TpyType | None',
                       analyzer) -> bool:
    """Whether passing a value-repr `Optional[str]` param typed `src` into slot
    `slot` fires `_maybe_convert_opt_view_param`'s ARG split -- mirrored exactly:
    both must be value-repr Optional whose inner resolves to an OWNED view family
    (`view_family_for_type` non-None, e.g. `str` / `bytes`), of the SAME family. A
    `StrView` / `BytesView` inner keys `view_family_for_type` to None (the map is
    owned-qname-keyed), so the AST passes it BARE -- excluded here, keeping that
    shape on the AST path. The same-family check routes a str src to a str slot
    and a bytes src to a bytes slot (their families differ)."""
    so = _value_opt_view(src, analyzer)
    to = _value_opt_view(slot, analyzer)
    if so is None or to is None:
        return False
    sfam = view_family_for_type(so.inner)
    return sfam is not None and sfam is view_family_for_type(to.inner)

def _value_tuple_return_element_ok(e: TpyType, analyzer) -> bool:
    """A value-tuple element admitted at the RETURN slot only. Beyond the narrow
    set (`_value_tuple_element_ok`): a NESTED value-tuple element (its literal
    spelled recursively by `_lower_tuple_literal`), a value-repr
    `Optional[scalar]` element (`None` renders `std::nullopt`, a scalar value
    lands bare), a value-repr `Optional[str]` element (the str-value source
    takes the `std::string(view)` wrap threaded through the Optional slot by
    `_lower_container_elem`), and an `Own[F1-record]` element (a by-value `T`
    slot in the spelled `std::tuple<..., T>`: a ctor rvalue lands bare, an
    owned name moves via the element move source) -- all by-value slots that
    render position-independently."""
    if _value_tuple_element_ok(e, analyzer):
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if isinstance(inner, TupleType):
        return _value_tuple_return(inner, analyzer) is not None
    if isinstance(inner, OwnType):
        return _f1_record(inner.wrapped, analyzer)
    return (_value_opt_scalar(e, analyzer) is not None
            or _value_opt_str(e, analyzer) is not None)

def _value_tuple_return(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The value-tuple RETURN slot: `_value_tuple` widened at the element axis
    (nested value-tuple / value-`Optional[scalar]` elements). Return-only -- a
    literal source renders the spelled brace-init recursively (`_lower_tuple_
    literal` handles nested tuples and the `None`/scalar-into-Optional element
    slots); the param / decl / subscript-read sinks keep the narrow
    `_value_tuple` (a widened-element receiver has no bare-copy read arm).
    An `Own[tuple[...]]` slot unwraps: the return is the same by-value
    `std::tuple<...>` (ownership transfer changes nothing about the render)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if not isinstance(t, TupleType):
        return None
    return t if all(_value_tuple_return_element_ok(e, analyzer)
                    for e in t.element_types) else None

def _generic_value_tuple_return(t: TpyType | None,
                                analyzer) -> 'TupleType | None':
    """The GENERIC tuple RETURN slot: a tuple with at least one bare
    TypeParamRef element (its slot spells `::tpy::val_or_ptr_t<T>` and the
    element wraps in `::tpy::to_val_or_ptr` -- the AST slot-info ladder's
    TypeParamRef row, deferring value-vs-pointer to instantiation), every
    other element a narrow value-tuple element (scalar / owned-str / Any --
    bare by-value slots). Disjoint from `_value_tuple_return` (which has no
    TypeParamRef row); wrapped generic elements (`readonly[T]` / `Ref[T]`)
    and wider concrete mixes (Own / Optional / nested tuples beside a T)
    have no witnesses and keep the whole body on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    has_tp = False
    for e in t.element_types:
        if isinstance(e, TypeParamRef):
            has_tp = True
        elif not _value_tuple_element_ok(e, analyzer):
            return None
    return t if has_tp else None

def _value_tuple_nested(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A value tuple whose every element is a narrow value-tuple element (scalar /
    owned-str) OR itself a (recursively) value tuple -- the nesting-agnostic
    render/print family. Wider than `_value_tuple` (which stays narrow at the
    subscript-READ gate, where a nested-tuple element read is not a bare value),
    and used only at sinks whose emit is nesting-independent: the local decl-init
    (`_lower_tuple_literal` spells the nested brace-init recursively) and the
    whole-tuple print (`TuplePrinter` recurses). Narrower than `_value_tuple_return`
    on the element axis (no Own / Optional elements) -- those ride the return slot."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    for e in t.element_types:
        if not (_value_tuple_element_ok(e, analyzer)
                or _value_tuple_nested(e, analyzer) is not None):
            return None
    return t

def _storage_record_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """A STORAGE-form value-tuple element: a by-value slot in `std::tuple<...>`
    whose literal element lowers through the same move/copy path as a container
    element -- a scalar / owned-str (bare), an F1-record (a ctor rvalue lands
    bare, a last-use name moves in), an `Own[F1-record]` (the same by-value
    slot), or a nested storage tuple."""
    if _value_tuple_element_ok(e, analyzer):
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if isinstance(inner, OwnType):
        return _f1_record(inner.wrapped, analyzer)
    if isinstance(inner, TupleType):
        return _storage_record_tuple(inner, analyzer) is not None
    return _f1_record(inner, analyzer)

def _storage_record_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A tuple whose every element is storage-eligible -- consumed only for a
    VALUE-capture literal decl (`_lower_tuple_literal` over
    `_lower_container_elem`), where the local binds the tuple BY VALUE
    (`std::tuple<..., T>`, the element's `to_cpp()` value spelling) rather than
    the borrow form `std::tuple<..., T*>`. A @nocopy / owned-last-use record
    element moves in and a copy() rvalue constructs in place, exactly like a
    container element. The pointer-repr-vs-storage decision is the literal's
    per-element capture, NOT the tuple type (a plain-record tuple TYPE is
    pointer-repr, yet a VALUE-capture literal of it binds storage) -- so the
    caller gates on `elem_capture` all-VALUE, and this predicate only vets the
    element set."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    return t if all(_storage_record_tuple_element_ok(e, analyzer)
                    for e in t.element_types) else None

def _tuple_compare_pair(lt: 'TpyType | None', rt: 'TpyType | None',
                        analyzer) -> bool:
    """Both compare operands are value tuples (scalar / owned-str / nested,
    no pointer-repr element) -- std::tuple's native `operator==` / `operator<`
    (and the derived ordering ops) compares them element-wise, which the AST
    emits as the bare `(left op right)` fall-through. A pointer-repr tuple
    (record / Optional[record] element) whose slots are bare pointers would
    compare ADDRESSES, so the AST routes it through the `::tpy::tuple_eq` /
    `tuple_lt` deref-aware helpers -- a separate face kept on the AST path
    (`_value_tuple_nested` excludes pointer-repr tuples)."""
    return (_value_tuple_nested(lt, analyzer) is not None
            and _value_tuple_nested(rt, analyzer) is not None)

def _const_index(index: TpyExpr) -> 'int | None':
    """The compile-time integer index of a tuple subscript, mirroring the AST's
    `_extract_compile_time_index`: a bare int literal or a negated int literal. A
    non-constant tuple index never reaches lowering (sema rejects it); the
    lowering uses this to confirm the literal form regardless."""
    if isinstance(index, TpyIntLiteral):
        return index.value
    if (isinstance(index, TpyUnaryOp) and index.op == "-"
            and isinstance(index.operand, TpyIntLiteral)):
        return -index.operand.value
    return None

def _subscript_index_and_tuple(sub: TpySubscript,
                               analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a tuple subscript with a compile-time-const,
    in-bounds index (a negative literal folded by the tuple arity), or None if the
    receiver is not a tuple or the index is not such a constant. The receiver-type
    resolution + index fold written once and consumed by lowering and its arrow
    decision so the two can never drift."""
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(sub.obj))))
    if not isinstance(recv_t, TupleType):
        return None
    idx = _const_index(sub.index)
    if idx is None:
        return None
    n = len(recv_t.element_types)
    if idx < 0:
        idx += n
    if not (0 <= idx < n):
        return None
    return recv_t, idx

def _subscript_recv_tuple(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a subscript `t[N]` off an in-scope
    eligible-tuple name (a value-scalar tuple or an already-routed pointer-repr
    `_f1_tuple`), off a container-element read of such a tuple
    (`items[i][N]` -- a storage-form tuple lvalue, so the get chains off the
    container read: `std::get<N>(::tpy::__getitem__(items, i))`, `.` element
    access), OR off a clean field read of such a tuple (`c.data[N]` -- a
    storage-form tuple member, `std::get<N>(c.data)`, `.` element access);
    else None. Shared by the value-element and record-element read
    gates -- other receiver shapes (nested tuple gets, calls),
    ineligible tuples, and non-const indices stay on the AST path."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if isinstance(recv, TpyName):
        if recv.name not in locals_:
            return None
    elif isinstance(recv, TpyFieldAccess):
        # A clean field read of a storage-form tuple (`c.data[N]` /
        # `self.data[N]`): the field renders bare off its admitted receiver,
        # so the get chains identically (`std::get<N>(c.data)`); elements are
        # held by value (storage form -- `.` member access, no borrow lift).
        if not _field_receiver_ok(recv, locals_, analyzer):
            return None
    elif (isinstance(recv, TpySubscript)
            and isinstance(recv.obj, TpyName)
            and recv.obj.name in locals_):
        # The inner read must be a CONTAINER element (its receiver is not
        # itself a tuple) -- a tuple-over-tuple chain is an unverified
        # render.
        it = locals_.get(recv.obj.name)
        ib = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
              if isinstance(it, TpyType) else None)
        if ib is None or isinstance(ib, TupleType):
            return None
    else:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    if isinstance(recv, TpySubscript):
        # The container-element receiver's analyzer type can carry
        # unresolved literal elements (`items = [(7, Box(10))]` types
        # `items[0]` with an IntLiteralType member); the container's
        # DECLARED element tuple is the resolved authority.
        et = get_iterable_element_type(ib, analyzer.registry)
        eb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
              if isinstance(et, TpyType) else None)
        if (not isinstance(eb, TupleType)
                or len(eb.element_types) != len(recv_t.element_types)):
            return None
        recv_t = eb
        res = (recv_t, idx)
    if not (_value_tuple(recv_t, analyzer) is not None
            or _f1_tuple(recv_t, analyzer) is not None):
        return None
    return res

def _tuple_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> 'int | None':
    """A value-result tuple subscript read `t[N]` -> `std::get<N>(t)` (value form, no
    lift): element N is a value scalar or owned-str. Returns the normalized index, or
    None -- record / `Optional` (borrow) elements ride the field-receiver path
    (`t[N].field`). The receiver is an in-scope eligible-tuple NAME, or a clean
    value-tuple FIELD read (`self.data[N]` -> `std::get<N>(this->data)`; the field
    receiver renders exactly like the name arm, so the value element read is
    position-neutral)."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if isinstance(recv, TpyName):
        if recv.name not in locals_:
            return None
    elif isinstance(recv, TpyFieldAccess):
        # A value-tuple field (`self.data[N]`) or a storage-form mixed
        # record/scalar tuple field (`c.data[N]` on an `_f1_tuple` -- elements
        # held by value, so a scalar element reads bare like the name arm).
        if not (_field_receiver_ok(recv, locals_, analyzer)
                and (_value_tuple(analyzer.get_expr_type(recv), analyzer)
                     is not None
                     or _f1_tuple(analyzer.get_expr_type(recv), analyzer)
                     is not None)):
            return None
    elif isinstance(recv, TpySubscript):
        # A nested read `t[i][j]`: the inner `t[i]` must itself be a value-tuple
        # subscript read yielding a (possibly nested) value tuple, so the outer
        # `std::get<j>(std::get<i>(t))` stays a bare value read.
        if (_tuple_subscript_value_read(recv, locals_, analyzer) is None
                or _value_tuple_nested(
                    analyzer.get_expr_type(recv), analyzer) is None):
            return None
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A value-tuple-returning CALL receiver (`getsockname()[1]`): the
        # AST renders `std::get<N>(<call>)` with the call emitted in place,
        # so the read is position-neutral iff the call itself lowers -- the
        # call gates in _lower_expr, and a non-routable one rejects the
        # body there (safe fallback, never a mis-render).
        if _value_tuple_nested(analyzer.get_expr_type(recv),
                               analyzer) is None:
            return None
    elif isinstance(recv, TpyNamedExpr):
        # A walrus receiver (`(t := (1, b))[0]`): the assign form renders in
        # place (`std::get<0>((t = ...))`); the walrus arm validates its own
        # target/source classes during receiver lowering.
        pass
    else:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    if isinstance(recv, TpyNamedExpr):
        # A literal-init walrus type carries IntLiteral elements
        # (`(t := (1, b))` -> tuple[IntLiteral(1), Box]); the AST's
        # get_resolved_type collapses them, mirror before the family check.
        recv_t = resolve_int_literals(recv_t,
                                      analyzer.ctx.default_int_for_literal)
    if (_value_tuple_nested(recv_t, analyzer) is None
            and _f1_tuple(recv_t, analyzer) is None):
        return None
    el = recv_t.element_types[idx]
    # An owned-str element reads as an owned lvalue (`std::get<N>(t)` yields
    # `const std::string&`) -- bare in every sink on both paths, so it rides
    # the same value-read arm as a scalar element. A nested value-tuple element
    # reads bare as a whole `std::tuple<...>` value (recursively value-tuple),
    # consumed by print / decl-init / a further subscript.
    return (idx if (_eligible_scalar(el) or _owned_str_slot(el, analyzer)
                    or _value_tuple_nested(el, analyzer) is not None)
            else None)

def _subscript_record_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> 'int | None':
    """`t[N]` whose element is a plain F1-record (a `BORROW_REF` pointer-repr slot) --
    a borrow result usable as a scalar-field-read receiver (`t[N].field`). Returns the
    normalized index, or None. `Optional[record]` elements take the null-check member
    path (`_subscript_optional_field_recv`) and are excluded here (`_f1_record` rejects
    them)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    return idx if _f1_record(recv_t.element_types[idx], analyzer) else None

def _subscript_optional_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                   analyzer) -> 'int | None':
    """`t[N]` whose element is a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL) -- a
    nullable borrow usable as a runtime-null-checked field receiver (`t[N].field` ->
    `deref_check(...)`). Returns the normalized index, or None. Mirrors the Optional arm
    of `_f1_tuple_element_ok`."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t.element_types[idx])))
    if not (isinstance(inner, OptionalType) and inner.uses_pointer_repr()):
        return None
    return idx if _f1_record(_unwrap_own(inner.inner), analyzer) else None

def _field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A scalar field access off a record-element tuple subscript (`t[N].field`): the
    receiver `t[N]` is a plain-record borrow, the field a value scalar (checked by the
    caller). Position-neutral -- valid as a read (RHS) or a scalar-field write target
    (LHS), since both render `std::get<N>(t)->field` / `.field` off the same receiver.
    The borrow-local-binding source path keeps its own name-receiver gate, so `b = t[N]`
    stays on the AST path. Markers-clean excludes the Optional null-check / property /
    setattr shapes, so an Optional-element write and a property-setter write stay on the
    AST path."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _subscript_record_field_recv(e.obj, locals_, analyzer) is not None)

def _optional_field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                      analyzer) -> bool:
    """A scalar field read off an `Optional[record]`-element tuple subscript with an
    unproven None (`t[N].field` -> `deref_check(...).field`, the
    `needs_optional_runtime_check` path). The receiver `t[N]` is a nullable borrow, the
    field a value scalar (checked by the caller). Read only -- writes / binds keep the
    name-receiver gate."""
    return (isinstance(e, TpyFieldAccess) and e.needs_optional_runtime_check
            and _field_markers_clean(e, allow_optional_check=True)
            and _subscript_optional_field_recv(e.obj, locals_, analyzer) is not None)

def _record_getitem_idx_recv_ok(sub: 'TpySubscript',
                                locals_: dict[str, TpyType], analyzer,
                                pointers: 'AbstractSet[str]') -> bool:
    """The record-getitem subscript arm's index/receiver admission, written
    once for the subscript arm and the field-over-getitem gate: a scalar
    index (a runtime-BigInt one against a fixed-int key param carries the
    `.to_fixed_check` narrow the arm applies) or a str value, off a declared
    non-pointer NAME or clean-field receiver
    (pointer-local receivers render `(*p)[...]` -- excluded). An UNPROVEN
    Optional receiver keeps its runtime check on the AST path (explicit
    here rather than relying on `_record_getitem_key`'s unwrap staying
    narrow)."""
    if sub.needs_optional_runtime_check:
        return False
    idx_type = analyzer.get_expr_type(sub.index)
    idx_ok = ((_resolved_scalar(idx_type, analyzer)
               and (not _runtime_bigint(idx_type, analyzer)
                    or _bigint_index_disposition(
                           sub.index, analyzer.get_expr_type(sub.obj),
                           analyzer) != "reject"))
              or _resolved_str_value(idx_type, analyzer) is not None)
    recv_ok = ((isinstance(sub.obj, TpyName) and sub.obj.name in locals_
                and sub.obj.name not in pointers)
               or (isinstance(sub.obj, TpyFieldAccess)
                   and _field_receiver_ok(sub.obj, locals_, analyzer)))
    return bool(idx_ok and recv_ok)


def _field_over_record_getitem_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                  analyzer,
                                  pointers: 'AbstractSet[str]') -> bool:
    """A field access off an F1-RECORD-returning user-record `__getitem__`
    subscript (`points[0].x` on `ArrayList[Point, N]`): the receiver spells
    the record's bare `operator[]` lvalue, the field chains `.` off it.
    Only the member-access consumer admits (a value position would copy the
    borrow); position-neutral -- a read (RHS) and a scalar-field write
    target (LHS) render off the same receiver. Index/receiver shapes are
    the record-getitem subscript arm's (`_record_getitem_idx_recv_ok`)."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpySubscript)):
        return False
    sub = e.obj
    if isinstance(sub.index, TpySlice):
        return False
    if _record_getitem_key(analyzer.get_expr_type(sub.obj), analyzer) is None:
        return False
    return bool(_record_getitem_idx_recv_ok(sub, locals_, analyzer, pointers)
                and _f1_record(analyzer.get_expr_type(sub), analyzer))

def _owned_str_slot(t: TpyType | None, analyzer) -> bool:
    """An owned `str` container element/key/value slot (S5). Only the owned
    nominal is admitted: a `StrView`/`BytesView` slot makes the container hold
    views, whose literal keys/elements the AST pins to static storage
    (`view_key_target` threads the key type into the literal render) -- a shape
    this slice does not reproduce; the bytes family rides S6."""
    st = _resolved_str_value(t, analyzer)
    return st is not None and is_str_type(st)

def _container_scalar_read(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value read renders as a bare value via the
    container subscript emit: `list[scalar|str]`, `Array[scalar|str, N]` (sema's
    read-only list-literal demotion -- subscript/len/iteration emit identically
    to list), or `dict[fixed-int|str key, scalar|str value]`. `set` has no
    `__getitem__`. Owned-`str` keys read identically to a fixed-int index
    (`::tpy::__getitem__(c, k)` -- the static-storage literal pin fires only for
    VIEW-typed keys, which `_owned_str_slot` excludes); a BigInt-KEYED dict is
    admitted like a fixed-int-keyed one -- every render this predicate feeds is
    key-type-neutral (bare receiver name, `__len__`, dict-literal elements via
    the slot retype) except the index positions, which apply
    `_narrow_bigint_index` against the receiver's declared key type (a
    BigInt-keyed map passes the key through unnarrowed, a fixed-int key
    narrows to its declared width). An `Own[container]` (move-in
    `T&&` param) is excluded explicitly -- its ABI differs from the borrow shape
    this slice's emit assumes, and it rides a later cell (mirrors the Own unwrap
    in `_f1_record`, which admits Own where this deliberately does not)."""
    return _container_elem_family(
        t, analyzer,
        lambda a: _eligible_scalar(a) or _owned_str_slot(a, analyzer),
        span_ok=True)

def _dict_key_shape_ok(key: 'TpyType', analyzer) -> bool:
    """The ADMITTED dict/set key slice, written once for the dict-literal
    gate, the container elem-family dispatch, and the subscript reject
    namer: fixed-int / runtime-BigInt / owned-str / F1-record / Any keys --
    every render is key-type-neutral (index-position exprs gate their own
    shapes). `_any_value_dict` deliberately keeps the narrower int/BigInt/
    str trio: record/Any-KEYED Any-dict writes have no byte-diff witness."""
    kb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(key)))
    return (is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
            or _owned_str_slot(key, analyzer)
            or _f1_record(kb, analyzer)
            or isinstance(kb, AnyType))

def _container_elem_family(t: 'TpyType | None', analyzer, elem_ok,
                           *, span_ok: bool = False) -> bool:
    """The shared container-shape dispatch behind the per-element-family
    predicates (`_container_scalar_read` / `_bytes_elem_container` /
    `_container_record_elem`): list/Array admit on `elem_ok(elem)`, dict on
    the shared key slice (fixed-int / runtime-BigInt / owned-str) plus
    `elem_ok(value)`, `Own[container]` always rejects (move-in ABI). Only
    the scalar family admits `Span` (`span_ok`) -- span reads of str /
    record / bytes elements ride later cells. Adding an element family
    means one new thin front, not a fourth copy of this dispatch."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t) or is_array(t):
        return bool(args) and elem_ok(args[0])
    if span_ok and is_span(t):
        # `Span[scalar]` / `Span[readonly[scalar]]` (`std::span<T>` /
        # `std::span<const T>`, a by-value view param): subscript / len /
        # iteration emit exactly like list (the bounds-safe operator[] /
        # `::tpy::__getitem__`, `::tpy::__len__`, the begin/end capture
        # loop).
        return bool(args) and _eligible_scalar(unwrap_readonly(args[0]))
    if is_dict(t):
        if not args or len(args) < 2:
            return False
        key, val = args[0], args[1]
        return _dict_key_shape_ok(key, analyzer) and elem_ok(val)
    return False

def _bytes_elem_container(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is OWNED `bytes` -- admitted for
    subscript READS only (the io.py `chunks[0]` family). The element lvalue
    (`const std::vector<uint8_t>&`) lands bare in every admitted sink: an
    owned decl/return copies implicitly, a view-resolved binding / span arg
    converts implicitly, compare/print wrap by type -- so the read is STORAGE
    form (never the S6 `::tpy::bytes_copy` view wrap). Writes / literals /
    iteration keep `_container_scalar_read`'s families; `BytesView` elements
    stay excluded (the static-storage literal key pin, like str-view slots)."""
    def owned_bytes(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        bt = _resolved_bytes_value(a, analyzer)
        return bt is not None and is_bytes_type(bt)

    return _container_elem_family(t, analyzer, owned_bytes)

def _container_value_opt_scalar_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a value-repr `Optional[scalar]`
    (`list[Int32 | None]`, `dict[str, int | None]`): the subscript read yields
    the whole `std::optional<T>` element bare. Kept OFF `_container_value_leaf_read`
    (its docstring excludes composite Optional elements) because the bare
    optional lands only in a WHOLE-optional sink -- the read arm admits it solely
    under `allow_whole_optional`."""
    return _container_elem_family(
        t, analyzer, lambda a: _value_opt_scalar(a, analyzer) is not None)

def _container_value_tuple_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a VALUE tuple
    (`list[tuple[str, Int32]]`): the element is a self-contained
    `std::tuple<...>`, so the subscript read hands out the whole tuple bare
    exactly like a scalar element. Pointer-repr (reference-element) tuples
    stay out -- their reads carry the borrow/storage lift."""
    return _container_elem_family(
        t, analyzer, lambda a: _value_tuple(a, analyzer) is not None)


def _container_value_leaf_read(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value subscript READ renders bare in a value
    position -- the compositional replacement for the enumerated
    `_container_scalar_read` / `_bytes_elem_container` split. The read emit is a
    pure function of the element type: a value scalar / Char / enum / `Ptr`
    value is VALUE, an owned `str` / `bytes` element a bare-landing STORAGE
    lvalue -- all land bare in every value sink. This is exactly the value-leaf
    read set the field-read arm admits, so a subscript and a field read of the
    same element type route together. Record and str-VIEW elements are BORROW
    (routed by the record-receiver arm / str-view excluded); Optional / union /
    nested-container / tuple elements are composite (the naive read emit tags
    them VALUE but they do not land bare) -- byte-diff would diverge, so both
    stay off this arm. `Span` keeps the shared `span_ok` scalar-only arm."""
    def leaf(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        bt = _resolved_bytes_value(a, analyzer)
        return (_eligible_scalar(a) or _eligible_char(a)
                or _eligible_enum(a, analyzer) is not None
                or _eligible_ptr_value(a, analyzer)
                or _owned_str_slot(a, analyzer)
                # An `Any` value element (`dict[str, Any]`): the subscript
                # read is a bare `const Any&` lvalue consumed by from_any /
                # print, landing bare in every value sink.
                or isinstance(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a))),
                    AnyType)
                # An open-T element inside the generic body: the bare
                # checked-dunder read, form-neutral per instantiation.
                or _is_type_param_slot(a)
                # A `Callable` element (`list[Callable[[Int32], Int32]]`):
                # the `std::function` value lands bare like a scalar.
                or _callable_value(a)
                or (bt is not None and is_bytes_type(bt)))

    return _container_elem_family(t, analyzer, leaf, span_ok=True)

def _container_record_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a plain F1-record -- the family half
    of the record-element subscript read (`_container_record_elem_subscript`
    in expressions.py owns the expression shape). `Optional`-element and
    generic-record-element containers reject at `_f1_record` (the AST wraps
    those reads differently)."""
    return _container_elem_family(
        t, analyzer, lambda a: _f1_record(a, analyzer))

def _container_ref_alias_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is itself a plain list/dict/set: the
    element subscript yields a `T&` borrow bindable as a REF_ALIAS local
    (`row = matrix[0]` -> `std::vector<...>& row = ...`). The nested-container
    analog of `_container_record_elem`."""
    def container_elem(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        a = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
        if isinstance(a, OwnType):
            a = unwrap_readonly(a.wrapped)
        # Array included alongside list/dict/set: the element lvalue render
        # (`T& x = __getitem__(c, k)`) is the same for a demoted `std::array`
        # value (`{1:[1,2],2:[3,4]}` -> dict[int, Array[int,2]]).
        return is_list(a) or is_dict(a) or is_set(a) or is_array(a)
    return _container_elem_family(t, analyzer, container_elem)

def _container_str_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a resolved str value (`list[str]` /
    `dict[K, str]`): the element subscript yields the owned-string lvalue,
    consumable positionally as a str view-method receiver
    (`argv[i].startswith("-")` -> `::tpy::str_startswith(__getitem__(argv, i),
    ...)`). The str sibling of `_container_record_elem`."""
    return _container_elem_family(
        t, analyzer, lambda a: isinstance(a, TpyType)
        and _resolved_str_value(a, analyzer) is not None)


def _set_method_recv(t: TpyType | None, analyzer) -> bool:
    """A `set[scalar|owned-str]` METHOD-CALL receiver. Deliberately its own
    predicate, NOT a widening of `_container_scalar_read`: that family feeds
    subscript / decl-storage / for-each consumers where a set is invalid
    (`set` has no `__getitem__`). Method dispatch is fi-driven -- the set
    stubs' native helpers (`::tpy::set_remove(s, ...)`) and bare members
    (`s.clear()`) both ride THIRMethodCall's existing native_function_name /
    plain-member arms."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType) or not is_set(t):
        return False
    args = getattr(t, "type_args", None)
    return bool(args) and (_eligible_scalar(args[0])
                           or _owned_str_slot(args[0], analyzer)
                           # An F1-record element (`set[Point]`): inserts
                           # render the bare rvalue / move like a list's;
                           # per-method args and results still gate.
                           or _f1_record(unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(args[0]))), analyzer)
                           or isinstance(unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(args[0]))), AnyType))

def _cpp_noncopyable_type(t: 'TpyType | None', analyzer) -> bool:
    """Mirror of the AST's `_is_cpp_noncopyable` (sema facts only): @nocopy,
    `__del__` (deletes copy ops in C++ though sema's nocopy system doesn't
    track it), or a noncopyable field, with the `__copy__` escape hatch."""
    if t is None:
        return False
    ctx = analyzer.ctx
    if ctx.is_type_nocopy(t):
        return True
    record = ctx.registry.get_record_for_type(t)
    if record is None:
        return False
    if record.has_copy:
        return False
    if record.has_del:
        return True
    return any(_cpp_noncopyable_type(f.type, analyzer) for f in record.fields)

def _container_nocopy_elem(t: 'TpyType | None', analyzer) -> bool:
    """Mirror of the AST's `_is_nocopy_container_element`: a @nocopy member
    anywhere in the element slot forces the make_vector/make_ordered_*
    reserve+emplace switch (a `std::move` inside a brace-init would silently
    copy). The union / alias arms became reachable with the member-record
    union element row -- one non-copyable ALTERNATIVE makes the whole variant
    non-copyable, so they must be walked, not assumed away."""
    if t is None:
        return False
    if _cpp_noncopyable_type(t, analyzer):
        return True
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, AliasRef):
        u = analyzer.registry.resolve_alias_ref(u)
    if isinstance(u, UnionType):
        return any(_cpp_noncopyable_type(m, analyzer) for m in u.members
                   if not is_void_like_type(m))
    return False

def _container_enum_spell(t: 'TpyType | None', analyzer) -> bool:
    """A container decl type carrying an enum anywhere in its args: the decl
    C++ spelling must come from the resolver (render_type) like a plain enum
    local's -- `to_cpp()` reads the native_cpp_names view, which an
    aliased-import collision can skew."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (is_list(u) or is_dict(u) or is_set(u) or is_array(u)):
        return False

    def has_enum(x: 'TpyType | int') -> bool:
        if not isinstance(x, TpyType):
            return False
        x = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(x)))
        if is_enum_type(x):
            return True
        if isinstance(x, OptionalType):
            return has_enum(x.inner)
        if isinstance(x, TupleType):
            return any(has_enum(m) for m in x.element_types)
        return any(has_enum(a) for a in getattr(x, "type_args", ()) or ())

    return any(has_enum(a) for a in getattr(u, "type_args", ()) or ())

def _unbound_self_field_ok(e: TpyExpr) -> bool:
    """`BaseN.field` inside a method of a descendant -- sema's unbound-self
    field access (`unbound_self_parent_type` set), which `_gen_field_access`
    renders through a receiver-INDEPENDENT early return,
    `this->{parent.to_cpp()}::{field}`. The syntactic class-name receiver has
    no value type at all, so none of the receiver-shape rows can describe it;
    this is its own admission, deliberately NOT a row inside
    `_field_receiver_ok` (whose 70-odd callers read the receiver binding).

    Every other field marker takes its own AST emit path and is excluded:
    property / dyn-attr are tested BEFORE the unbound-self return, module-var
    / class-constant after it, and a deref chain or Optional null-check would
    wrap a render this arm spells whole.

    The receiver SPELLING (`this` vs a resumable frame's `__self`) is the
    lowering arm's check, not admission's -- the AST hardcodes `this->`."""
    if not isinstance(e, TpyFieldAccess) or e.unbound_self_parent_type is None:
        return False
    # A @native field RENAME is excluded: `_gen_field_access` computes
    # `cpp_field` from the source name BEFORE its `native_field_name`
    # override, and the unbound-self early return sits between the two -- so
    # the AST spells the UNRENAMED member there and emits uncompilable C++
    # (BUGS.md). Routing would silently "fix" it into a divergence, so the
    # shape keeps falling back until the AST arm is corrected.
    if e.native_field_name is not None:
        return False
    return not (e.module_var_access is not None
                or e.class_constant_owner is not None
                or e.property_getter_call is not None
                or e.dyn_getattr_call is not None
                or e.property_setter_call is not None
                or e.dyn_setattr_call is not None
                or e.deref_depth
                or e.deref_narrowed_to is not None
                or e.needs_optional_runtime_check)

def _field_receiver_or_unbound_self_ok(e: TpyExpr,
                                       declared: dict[str, TpyType],
                                       analyzer) -> bool:
    """`_field_receiver_ok` OR the unbound-self form -- the receiver set every
    row shares whose render is `recv.field` / `recv->field` / the
    base-qualified `this->BaseN::field`, all of which the field-write and
    value-read arms spell the same way after the receiver.

    Written once so a future receiver-shape row cannot forget to OR the
    unbound-self case in; `_field_receiver_ok` itself stays narrow because its
    ~75 other callers read the receiver BINDING, which an unbound-self access
    does not have."""
    return (_field_receiver_ok(e, declared, analyzer)
            or _unbound_self_field_ok(e))

def _field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a plain field access `recv.field` off an F1-record receiver (a record
    param, REF_ALIAS local, or F2 plain `T*` pointer-local in `declared`) or a
    PROVEN-non-None pointer-repr `Optional[F1-record]` borrow name (an `A | None`
    param / OPTIONAL_TO_PTR local whose access sema proved -- the runtime-check
    marker is clear, so the AST renders the plain indirect access `p->field`),
    with no special-emit marker -- read or write position. The receiver's
    pointer-vs-reference shape (`->` vs `.`) is decided at lowering from the
    pointer set; eligibility only needs the receiver binding to be in the slice.
    An UNPROVEN Optional access carries `needs_optional_runtime_check` and is
    rejected by the marker guard here -- its `deref_check` face rides
    `_optional_checked_field` at the positions that mirror it. The
    property-setter / `__setattr__` markers guard the write position (a
    property/setattr field assign takes a method-call emit path)."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    recv = e.obj
    if not isinstance(recv, TpyName):
        return False
    return (_f1_record(declared.get(recv.name), analyzer)
            or _optional_ptr_borrow_name(recv, declared, analyzer) is not None)

def _tparam_protocol_field_recv_ok(e: TpyExpr, declared: dict[str, TpyType],
                                   bounds: 'dict | None') -> bool:
    """`item.value` off a protocol-BOUND type-param binding (`item: T` under
    `[T: HasValue]`). Inside the template the receiver is a
    `param_val_or_ref_t<T>` value-or-reference -- never a pointer -- and the
    concept requires the member, so `_gen_field_access` renders the plain
    `item.value`. The method-call sibling is `_bounded_tparam_protocol`'s."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    recv = e.obj
    if not isinstance(recv, TpyName):
        return False
    return _bounded_tparam_protocol(declared.get(recv.name),
                                    bounds) is not None

def _tparam_protocol_field_over_field_ok(e: TpyExpr, bounds: 'dict | None',
                                         analyzer) -> bool:
    """`self.inner.value` where the INNER field is a protocol-bound type param
    of the enclosing generic record -- the chain sibling of
    `_tparam_protocol_field_recv_ok`. The member spells the same postfix `.`
    off the inner read, which lowers through its own arms."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpyFieldAccess)
            and _field_markers_clean(e.obj)):
        return False
    return _bounded_tparam_protocol(analyzer.get_expr_type(e.obj),
                                    bounds) is not None

def _str_field_value_read(e: TpyExpr, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A value-position read of a str-family field off an admitted receiver
    (`recv.field`, `_field_receiver_ok`): an owned `std::string` member reads
    bare as STORAGE (a view sink binds it implicitly, an owned sink copies by
    value); a `StrView` member reads bare as BORROW, so the owned-str sinks
    that admit it fire the explicit view->owned copy (`std::string(...)` at
    the return convert) -- both byte-identical at the positions that admit
    the read (the str-family return slot, a print / f-string arg). A `String`
    field resolves INSIDE the slice but is excluded here by the
    `is_str_type`/`is_str_view_type` filter below -- its owned form has no
    view sink to convert at. An unbound-self `BaseN.field` read joins the
    row: its render is the same bare member read behind a fixed qualifier."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_receiver_or_unbound_self_ok(e, declared, analyzer)):
        return False
    st = _resolved_str_value(analyzer.get_expr_type(e), analyzer)
    return st is not None and (is_str_type(st) or is_str_view_type(st))

def _bytes_field_value_read(e: TpyExpr, declared: dict[str, TpyType],
                            analyzer) -> bool:
    """A value-position read of a bytes-family field off an admitted receiver
    (`recv.field`, `_field_receiver_ok`) -- the bytes sibling of
    `_str_field_value_read`. An owned `std::vector<uint8_t>` member reads bare
    as STORAGE, a `BytesView` (span) member as BORROW; the render is bare
    `.field` at every position that admits the read (print sink, compare/concat
    operands, membership needle), byte-identical to the AST path. An
    unbound-self `BaseN.field` read joins the row like its str twin."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_receiver_or_unbound_self_ok(e, declared, analyzer)):
        return False
    bt = _resolved_bytes_value(analyzer.get_expr_type(e), analyzer)
    return bt is not None and (is_bytes_type(bt) or is_bytes_view_type(bt))

def _const_exact_field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType],
                                   analyzer) -> bool:
    """`_field_receiver_ok` minus Optional-ptr borrow-name receivers -- for the
    const-SPELLING sinks (borrow-local decls, storage-tuple aliases, union
    locals from fields, borrow-tuple returns), whose emitted decl spells the
    receiver's const verdict. THIR reads the inferred verdict
    (`_param_is_const` / `const_locals`), but the AST keys these decls on
    `const_indirect_locals`, which `seed_param_locals` populates for an
    Optional-ptr receiver only when it is `readonly[...]`-ANNOTATED -- an
    inferred-const narrowed receiver thus emits a non-compiling mutable decl
    (`A& g = h->g;` off `const H* h`; the BUGS.md "borrow locals off a
    narrowed Optional receiver drop inferred constness" entry). Mirroring
    would fork the verdict solely to reproduce the bug -> gate-reject per the
    ledger criterion. Render-const-blind consumers (field reads/writes, arg
    lifts, reseats, MIL copies) keep the plain `_field_receiver_ok`."""
    if not _field_receiver_ok(e, declared, analyzer):
        return False
    return _optional_ptr_borrow_name(e.obj, declared, analyzer) is None


def _storage_tuple_alias_src_ok(init: TpyExpr, lc, declared: dict[str, TpyType],
                                analyzer) -> bool:
    """The source-side gate for an `auto&&` storage-tuple alias, per shape.

    A FIELD source keeps the const-spelling receiver gate, because the decl
    below derives the alias's const-ness from it. The SUBSCRIPT and NAME shapes
    are admitted CONST-FREE only: their const verdict would have to come from
    the AST's `is_const_storage_source`, a recursive walk over a body-global set
    whose downstream consumers decide later borrow reads -- so admitting a const
    one would mean mirroring that set's whole consumer topology, not just its
    value here. Rejecting const sources keeps the new shapes from contributing a
    member at all, which is what makes this widening const-topology-neutral.

    Both non-field arms also consult `const_storage_tuple_locals`, where a loop
    variable iterating a const source records its const-ness. That check CANNOT
    FIRE today -- F3 admission requires `fn_top`, and a loop body lowers with
    `in_branch` set -- so this is belt-and-braces, not a live-bug fix. It is here
    because the guarantee the gate advertises should be enforced by the gate,
    rather than resting on an admission condition two files away staying narrow.
    """
    if isinstance(init, TpyFieldAccess):
        return _const_exact_field_receiver_ok(init, declared, analyzer)
    if isinstance(init, TpySubscript):
        recv = init.obj
        if not isinstance(recv, TpyName):
            return False
        return not (recv.name in lc.const_locals
                    or recv.name in lc.const_storage_tuple_locals
                    or _param_is_const(recv.name, lc.func, analyzer,
                                       lc.record_name))
    return (isinstance(init, TpyName)
            and init.name not in lc.const_locals
            and init.name not in lc.const_storage_tuple_locals)


def _optional_checked_field(e: TpyExpr, declared: dict[str, TpyType],
                            analyzer) -> bool:
    """An UNPROVEN field access off a pointer-repr `Optional[F1-record]` borrow
    name: `p.x` where sema could not prove `p` non-None, marked
    `needs_optional_runtime_check` -> `::tpy::deref_check(p).x`
    (_gen_field_access's runtime-check path over the already-`T*` receiver;
    `pointer_value_expr` is the identity for a non-global name). Serves read,
    write-target, and aug-assign-target positions -- the render is
    position-independent. Storage-form Optional sources (fields / subscripts /
    OPTIONAL_STORAGE names -- the `deref_optional_check` render) never bind a
    declared Optional-ptr name in a routed body, so the name check alone pins
    the `deref_check` spelling."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if not e.needs_optional_runtime_check:
        return False
    if not _field_markers_clean(e, allow_optional_check=True):
        return False
    return _optional_ptr_borrow_name(e.obj, declared, analyzer) is not None

def _optional_checked_field_over_field_ok(e: TpyExpr,
                                          declared: dict[str, TpyType],
                                          analyzer) -> bool:
    """An UNPROVEN field access whose receiver is a clean field READ of a
    STORAGE `Optional[F1-record]` member (`h.opt.x` where sema could not
    prove `h.opt` non-None): the AST wraps the whole optional lvalue --
    `::tpy::deref_optional_check(h.opt).x` -- the storage sibling of
    `_optional_checked_field`'s already-`T*` name receiver. Read positions
    only (the write target keeps its own gate)."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if not e.needs_optional_runtime_check:
        return False
    if not _field_markers_clean(e, allow_optional_check=True):
        return False
    recv = e.obj
    if not (isinstance(recv, TpyFieldAccess)
            and _field_receiver_ok(recv, declared, analyzer)):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if not (isinstance(rt, OptionalType) and rt.uses_pointer_repr()):
        return False
    return _f1_record(_unwrap_own(rt.inner), analyzer)

def _field_markers_clean(e: TpyFieldAccess, *,
                         allow_optional_check: bool = False) -> bool:
    """The field access carries no special-emit marker (module var / class constant /
    property / dyn attr / unbound-self / deref chain / Optional null-check) -- a plain
    `.field` read or write. Each marker takes its own AST emit path, out of the slice.
    `allow_optional_check` keeps `needs_optional_runtime_check` admissible for the
    Optional-element member path (which reproduces that runtime check)."""
    return not (e.module_var_access is not None or e.class_constant_owner is not None
                or e.property_getter_call is not None or e.dyn_getattr_call is not None
                or e.property_setter_call is not None or e.dyn_setattr_call is not None
                or e.unbound_self_parent_type is not None or e.deref_depth
                or e.deref_narrowed_to is not None
                or (e.needs_optional_runtime_check and not allow_optional_check))

def _field_decl_type(e: TpyFieldAccess, declared: dict[str, TpyType],
                     analyzer) -> 'TpyType | None':
    """The field's DECLARED type off the gate's declared map -- the
    `_resolve_field_declared_type` mirror for the admitted receiver shapes
    (a record / proven Optional-ptr NAME; the receiver gate pinned that),
    widened to inherited fields (the AST helper checks own fields only and
    falls back to the expr type, which for an unnarrowed read is the same
    declared type). Consumers type on it rather than the flow-narrowed expr
    type, so a narrowed Optional/union field -- whose AST render takes the
    `(*recv.field)` unwrap -- types at the un-narrowed declared type and
    rejects at the caller's family check.

    A non-NAME receiver reads its own expression type instead of `declared`
    (the receiver gate pinned the shape); a chain whose type does not resolve
    to a record answers None, so a caller that has not run the receiver gate
    first gets a reject rather than an AttributeError."""
    if isinstance(e.obj, TpyName):
        base = declared.get(e.obj.name)
    else:
        # A call / element receiver (`h.get().field`) has no declared entry;
        # its own gate pinned the shape, and its RESULT type carries the
        # record whose field declaration this reads -- so the narrowed-field
        # guards downstream stay live for those receivers too.
        base = analyzer.get_expr_type(e.obj)
    if base is None:
        return None
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(base)))
    if isinstance(rt, OptionalType):
        if rt.inner.is_value_type():
            return None
        rt = rt.inner
    if not (isinstance(rt, NominalType) and rt.is_record):
        return None
    record = analyzer.registry.get_record_for_type(rt)
    if record is None:
        return None
    # Own fields take precedence over inherited ones (get_all_fields is
    # base-first); the C++ member access renders identically either way.
    for f in reversed(analyzer.registry.get_all_fields(record)):
        if f.name == e.field:
            return f.type
    return None

def _subscript_container_recv_type(recv: TpyExpr, locals_: dict[str, TpyType],
                                   analyzer) -> 'TpyType | None':
    """The receiver binding type for a container subscript read/write/del: a
    bare in-scope NAME (the DECLARED binding -- a literal-seeded local's use
    sites carry the pre-resolution pending type) or a one-level field access
    off an admitted receiver name (`_field_receiver_ok`), typed at the field's
    DECLARED type (`_field_decl_type`). The family check
    (`_container_scalar_read`) stays with the caller -- this only resolves the
    receiver shape to a type. None for shapes outside the slice (deeper
    chains, Optional-checked fields via the marker guard, subscript / call
    receivers)."""
    if isinstance(recv, TpyName):
        return locals_.get(recv.name)
    if (isinstance(recv, TpyFieldAccess)
            and _field_receiver_ok(recv, locals_, analyzer)):
        return _field_decl_type(recv, locals_, analyzer)
    return None

def _record_getitem_key(obj_type: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The key param type of a user-record subscript receiver's `__getitem__`
    (so `recv[index]` spells the record's generated bare `operator[]`), or
    None. Non-@native user records (including monomorphized generic records
    like `FixStr[16]`, whose C++ operator[] renders bare identically -- mirrors
    the AST's `_is_concrete_user_record`, which likewise has no type-args
    guard): a @native record wrapping an STL type has a C++ operator[] taking
    size_t (the AST casts there)."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return None
    m = ri.get_method("__getitem__")
    if m is not None and len(m.params) >= 1:
        # Method params exclude the implicit self, so params[0] is the key.
        return m.params[0].type
    return None


def _record_setitem_value(obj_type: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The VALUE param type of a user-record subscript receiver's `__setitem__`
    (so `recv[key] = v` spells the AST's no-container fallback
    `::tpy::__setitem__(recv, key, v)`), or None. Same non-@native restriction
    as `_record_getitem_key` (monomorphized generic records included) -- a
    native STL wrapper keeps its own emit path."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return None
    m = ri.get_method("__setitem__")
    if m is not None and len(m.params) >= 2:
        # (key, value) after the implicit self -- the value is the last param.
        return m.params[-1].type
    return None


def _record_has_delitem(obj_type: 'TpyType | None', analyzer) -> bool:
    """A CONCRETE user record defining `__delitem__` (so `del recv[key]` spells
    `::tpy::__delitem__(recv, key)`). Same concrete / non-@native restriction as
    `_record_getitem_key`."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record and not t.type_args):
        return False
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return False
    return ri.get_method("__delitem__") is not None

def _f2_reseat_ok(init: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """A pointer-local reseat value: an lvalue field read off an F1-record receiver
    whose field is itself an F1-record (the new pointee), so it reseats as
    `p = &(recv.field);`. rvalue / `None` / name-alias reseats need the rebind-slot
    (`__slot_N`) machinery and stay on the AST path."""
    return (_field_receiver_ok(init, declared, analyzer)
            and _f1_record(analyzer.get_expr_type(init), analyzer))

def _f1_param_lvalue_reseat_ok(init: TpyExpr, pointee: TpyType,
                               declared: dict[str, TpyType], lc, analyzer) -> bool:
    """A pointer-repr `Optional` local reseat source that lifts via `&(name)`: a
    bare record PARAM name whose stripped type is the exact F1-record pointee. A
    param renders as a plain lvalue (`T&` / `const T&`), so `&(p)` is well-formed
    -- the AST `_gen_pointer_local_rebind` else/global address-of arm. A
    pointer-local source (bare copy) or an owned-local / rvalue source (a
    different emit) stays on the AST path."""
    if not isinstance(init, TpyName):
        return False
    if init.name not in lc.prescan.param_names or init.name in lc.pointers:
        return False
    t = declared.get(init.name)
    if t is None:
        return False
    tu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _f1_record(tu, analyzer) and tu == pointee

def _is_borrow_ptr_local(e: TpyExpr, declared: dict[str, TpyType],
                         pointers: set[str]) -> bool:
    """`e` is a bare borrow `T*` local that lifts to a storage `optional<T>` at a
    write/return: an F2a POINTER / F2d REBIND_SLOT (plain-record, in `pointers`) or
    an F1 OPTIONAL_TO_PTR (a pointer-repr `Optional` local, known by its declared
    type). All render `T*`; the copy-vs-move choice (`ptr_to_optional` vs
    `ptr_to_optional_move`) is decided at lowering from movability + last-use, not
    here (a non-owning borrow is never movable, so it always copies). A `T&`
    REF_ALIAS is excluded -- not a pointer, so the AST path emits it differently."""
    if not isinstance(e, TpyName):
        return False
    if e.name in pointers:
        return True
    t = declared.get(e.name)
    return isinstance(t, OptionalType) and t.uses_pointer_repr()

def _tuple_literal_has_ref_elements(e: TpyTupleLiteral,
                                    slot: 'TupleType') -> bool:
    """`_gen_tuple_literal`'s `has_ref_elements` verdict, which picks the
    BORROW slot ladder (`std::tuple<T*, ...>`) over the plain value tuple:
    any sema-annotated non-VALUE capture, any TypeParamRef slot, or -- with
    no annotation at all -- any element slot that is neither a value type nor
    an `Own[...]`. Mirrored here so the container-element and field-write arms
    split on the same fact the AST does."""
    if e.elem_capture:
        return any(c is not TupleElemCapture.VALUE for c in e.elem_capture)
    return any(isinstance(t, TypeParamRef)
               or (not t.is_value_type() and not isinstance(t, OwnType))
               for t in slot.element_types)


def _tuple_elem_slots_ptr_optional(slot: 'TupleType') -> bool:
    """Every non-value element slot of `slot` is a pointer-repr `Optional`.

    `_tuple_literal_slot_info` forces REF (non-const) for those so the slot
    shape stays uniform, but sends any OTHER non-value simple lvalue to
    CONST_REF in a storage context -- a rule `_lower_borrow_tuple_literal`
    does not carry. Restricting the container-element borrow arm to the
    all-Optional shape keeps it on the branch both ladders agree on; the
    plain-record member (`const T*` on the AST path) stays AST."""
    def elem_ok(t: TpyType) -> bool:
        if t.is_value_type():
            return True
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        return isinstance(bare, OptionalType) and bare.uses_pointer_repr()

    return all(elem_ok(t) for t in slot.element_types)


def copy_call_arg(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """The single argument of a `copy(x)` builtin call, or None.

    The three `_gen_copy_expr` mirrors (`copy_plain_record_source`,
    `copy_ctor_rvalue_source`, `copy_ptr_optional_peel`) each discriminate a
    DIFFERENT branch of that emit, but they share this entry test -- keeping
    it in one place stops the guard itself from drifting between them."""
    if not isinstance(e, TpyCall):
        return None
    fi = e.resolved_function_info
    if (fi is None or fi.qualified_name != COPY_QNAME
            or len(e.args) != 1 or e.kwargs):
        return None
    return e.args[0]


def copy_ptr_optional_peel(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """`copy(x)` where `x` is a pointer-repr `Optional` -- `_gen_copy_expr`'s
    identity early return, which hands `gen_expr(arg)` straight back and makes
    no copy at all (the borrow `T*` reaches the sink unchanged; the sink's own
    storage lift is what copies). Returns the inner expression so consumers
    render the bare argument; None when this is not that shape."""
    arg = copy_call_arg(e, analyzer)
    if arg is None:
        return None
    at = analyzer.get_expr_type(arg)
    return (arg if isinstance(at, OptionalType) and at.uses_pointer_repr()
            else None)


def _f2b_optional_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """An optional-field write `recv.field = <value>`: the target is a pointer-repr
    `Optional[record]` field off an F1-record receiver and the value is either a
    bare borrow `T*` local (`recv.field = ::tpy::ptr_to_optional[_move](p)`, copy
    or move per last-use), a `None` literal (`recv.field = std::nullopt`), a
    CALL returning the same pointer-repr Optional (a borrow `T*` rvalue taking
    the same lift -- `h.value = ptr_to_optional(find_point(pts, 1))`), or a
    FIELD read of the same Optional (already `std::optional<T>` STORAGE, so it
    copies bare). The generic tail picks lift-vs-bare off the LOWERED form, so
    both new rows share one emit.

    A `copy()` wrapper peels first: for a pointer-repr Optional argument
    `_gen_copy_expr` is the identity, so `copy(src)` and `src` render the
    same string."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    if not _f1_record(ftype.inner, analyzer):
        return False
    value = copy_ptr_optional_peel(stmt.value, analyzer) or stmt.value
    if isinstance(value, TpyNoneLiteral) or _is_borrow_ptr_local(
            value, declared, pointers):
        return True
    if not isinstance(value, (TpyCall, TpyMethodCall, TpyFieldAccess)):
        return False
    # Same-Optional sources only: a differing inner would need a conversion
    # neither the bare copy nor the plain lift carries.
    vt = analyzer.get_expr_type(value)
    if not (isinstance(vt, OptionalType) and vt.uses_pointer_repr()
            and vt.inner == ftype.inner):
        return False
    if isinstance(value, TpyFieldAccess):
        return _field_receiver_ok(value, declared, analyzer)
    return True

def _is_borrow_tuple_source(e: TpyExpr, declared: dict[str, TpyType],
                            storage_tuple_locals: set[str], analyzer) -> bool:
    """A borrow-form tuple name (`std::tuple<..., T*>`) that lifts to storage form
    at a field write via `tuple_to_storage`: a borrow tuple PARAM. A storage-tuple
    alias local (`auto&&`, in `storage_tuple_locals`) is STORAGE form -- a direct
    copy, no wrap -- and is excluded; a storage-form field/subscript/global source
    is likewise a direct copy and is not this borrow source."""
    return (isinstance(e, TpyName)
            and e.name not in storage_tuple_locals
            and _f1_tuple(declared.get(e.name), analyzer) is not None)

def _f1_tuple_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                             storage_tuple_locals: set[str], analyzer) -> bool:
    """A tuple-field write `recv.field = <borrow tuple>`: an F3 tuple field off an
    F1-record receiver, written from a borrow tuple source -> the field-write lifts
    borrow->storage via `tuple_to_storage` (copy; the `Own[tuple]` move arm and the
    storage-source direct-copy ride later cells)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ft = _f1_tuple(analyzer.get_expr_type(target), analyzer)
    if ft is None:
        return False
    # A tuple LITERAL renders the spelled value-form brace-init and the
    # assign wraps it (`tuple_to_storage<S>(std::tuple<..>{std::move(a), ..})`
    # -- `_maybe_wrap_tuple_to_storage` over `_gen_tuple_literal`'s
    # all-VALUE-elements path). The REF-element ladder spells its slots
    # differently and stays out.
    if isinstance(stmt.value, TpyTupleLiteral):
        return (len(stmt.value.elements) == len(ft.element_types)
                and not _tuple_literal_has_ref_elements(stmt.value, ft))
    return _is_borrow_tuple_source(stmt.value, declared, storage_tuple_locals, analyzer)


def _value_tuple_field_literal_write_ok(stmt: TpyAssign,
                                        declared: dict[str, TpyType],
                                        analyzer) -> bool:
    """A VALUE-tuple field written from a tuple LITERAL (`s.auth = ("u", "p")`
    -> `s.auth = std::tuple<std::string, std::string>{"u", "p"};`): borrow and
    storage coincide for a value tuple, so the spelled brace-init assigns
    directly with no `tuple_to_storage` wrap -- the F3 sibling's lift is what
    distinguishes them."""
    if not isinstance(stmt.value, TpyTupleLiteral):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    vt = _value_tuple(analyzer.get_expr_type(stmt.target), analyzer)
    if vt is None:
        return False
    return (len(stmt.value.elements) == len(vt.element_types)
            and not _tuple_literal_has_ref_elements(stmt.value, vt))

def _param_const_verdict(name: str, func: TpyFunction, analyzer,
                         record_name: str | None, attr: str) -> bool:
    """Whether param `name` is in the function's `attr` verdict set
    (`const_borrow_params` / `deep_const_borrow_params` -- both are
    param-index sets on the registry FunctionInfo). A method's FunctionInfo
    lives on the owning record (`record_name`), a free function's in the
    function registry -- the same lookup codegen's
    `_get_method_mutated_params` uses. A property pair shares one overload
    list (getter + setter); [-1] is safe only because a getter has no
    non-self params (this lookup is never consulted for it) and the setter's
    non-value param is forced Own[...] (routing around const entirely)."""
    if record_name is not None:
        ri = analyzer.registry.get_record(record_name)
        overloads = ri.get_method_overloads(func.name) if ri is not None else None
    else:
        overloads = analyzer.registry.get_function(func.name)
    fi = overloads[-1] if overloads else None
    verdict = getattr(fi, attr, None) if fi is not None else None
    if not verdict:
        return False
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    return idx is not None and idx in verdict

def _param_is_deep_const(name: str, func: TpyFunction, analyzer,
                         record_name: str | None = None) -> bool:
    """Whether param `name` carries the DEEP-const verdict
    (`FunctionInfo.deep_const_borrow_params` -- discriminant-only use, no
    address escape), which deep-consts a pointer-variant param's pointees
    (`std::variant<const A*, const B*>`) in the signature and every
    narrowed-member spelling."""
    return _param_const_verdict(name, func, analyzer, record_name,
                                "deep_const_borrow_params")

def _param_is_const(name: str, func: TpyFunction, analyzer,
                    record_name: str | None = None) -> bool:
    """Whether param `name` is emitted `const` -- read from the sema fact
    `FunctionInfo.const_borrow_params` (param indices), which equals codegen's
    `const_ref_params` for a function's plain F1-record (ref) params. This holds
    for a readonly callable (method or free function) too: a `@readonly` callable's
    non-value param is stored as `Ref(ReadonlyType(T))`, so `decide_param_const`
    takes the `ReadonlyType` early-exit -- the forced-const (codegen body) and
    inferred (`const_borrow_params`) verdicts traverse the same branch, making the
    inferred set exact. The verdict set is None when Phase-2 has not run --
    unreachable for an admitted function (Phase-1 always sets
    `mutated_params`), so the resulting not-const is a safe default, not a
    divergence. Inplace dunders (`__iadd__` ...) take the AST's FORCED
    const-params verdict (`use_const_params` via CONST_PARAMS_METHODS --
    a codegen-side force sema's `const_borrow_params` does not record);
    the mutated-param slice the force does NOT cover (decide_param_const's
    directly_mutated short-circuit) is rejected at the sig check, so the
    flat force here is exact for every admitted body."""
    if (func.is_method and func.name in CONST_PARAMS_METHODS
            and name != "self"):
        return True
    return _param_const_verdict(name, func, analyzer, record_name,
                                "const_borrow_params")

def _const_borrow_name(name: str, lc) -> bool:
    """The AST `_is_const_borrow_source` mirror for a bare name: a param
    under the deep-const or const-borrow verdicts. The AST's third arm
    (ReadonlyType declared type) never fires for admitted subjects -- the
    poly/dyn admission requires the declared entry fully unwrapped, so a
    readonly-declared name rejects before const-ness is consulted."""
    return (_param_is_deep_const(name, lc.func, lc.analyzer, lc.record_name)
            or _param_is_const(name, lc.func, lc.analyzer, lc.record_name))

def _already_pointer_source(expr: TpyExpr, lc) -> bool:
    """`ctx.is_already_pointer_source` mirror: True when `expr` renders as a
    `T*` with no further lifting, so an `&(...)` lift would produce `T**`.

    Both of codegen's disjuncts. The name half (`lc.pointers`, plus the
    `self` receiver whose `this` is a prvalue pointer) is what THIR sites
    historically spelled inline; the `Ptr[T]` half is the one they dropped --
    a `Ptr[T]` source never enters `lc.pointers` (the `_eligible_ptr_value`
    family owns it), so a membership test alone answers "not a pointer" for
    exactly the type that most obviously is one. Takes an EXPRESSION, not a
    name, so subscript and field sources are covered too."""
    if isinstance(expr, TpyName):
        if expr.name in lc.pointers:
            return True
        if expr.name == lc.self_receiver and lc.self_is_pointer:
            return True
    return isinstance(unwrap_readonly(lc.analyzer.get_expr_type(expr)),
                      PtrType)

def _poly_subject_const(expr: TpyExpr, lc) -> bool:
    """`_poly_subject_is_const` mirror: whether a polymorphic dispatch
    subject's pointee is const, so the cast targets `const Sub*`. True for
    a readonly-typed subject, a name param under the const verdicts, or a
    field/subscript whose receiver chain is const (C++ propagates const
    through member access)."""
    if isinstance(lc.analyzer.get_expr_type(expr), ReadonlyType):
        return True
    while isinstance(expr, (TpyFieldAccess, TpySubscript)):
        expr = expr.obj
    if isinstance(expr, TpyName):
        return (isinstance(lc.analyzer.get_expr_type(expr), ReadonlyType)
                or _const_borrow_name(expr.name, lc))
    return False

def _f1_is_const(binding: 'LocalBinding', target_type: TpyType | None,
                 stmt: TpyVarDecl, func: TpyFunction, analyzer,
                 const_locals: set[str], record_name: str | None = None) -> bool:
    """The const-ness of an F1 borrow local's decl (`const T&` / `const T*`).

    Mirrors `_is_const_indirect` for the field source (ReadonlyType reads on the
    optional inner / the init's raw sema type / the var_types entry) plus, for
    OPTIONAL_TO_PTR, the storage-optional const bump (`is_const_union_source`:
    the receiver in const_ref_params (param) or const_indirect_locals (a const F1
    local, tracked in `const_locals`)), and the readonly-method ref-return
    branch for a method-call source. The name const branch of
    `_is_const_indirect` does not apply to a field/call source."""
    if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
        return True
    if isinstance(analyzer.get_expr_type(stmt.init), ReadonlyType):  # raw sema type
        return True
    svt = analyzer.var_types.get(id(stmt))
    if isinstance(svt, OptionalType) and isinstance(svt.inner, ReadonlyType):
        return True
    if binding is LocalBinding.OPTIONAL_TO_PTR:
        recv = stmt.init.obj  # TpyName (validated by _field_receiver_ok)
        if (recv.name in const_locals
                or _param_is_const(recv.name, func, analyzer, record_name)):
            return True
    # A readonly method's ref return binds `const T&` -- the method-call
    # branch of `_is_const_indirect`.
    if isinstance(stmt.init, TpyMethodCall):
        fi = stmt.init.resolved_function_info
        if (fi is not None and fi.is_readonly
                and call_returns_cpp_ref(analyzer, fi)):
            return True
    # A subscript / method call on a const-rooted receiver binds const even
    # when sema resolved the MUTABLE twin (the enclosing method's
    # readonly-ness is INFERRED post body-analysis, so fi.is_readonly above
    # misses; C++ overload resolution on the const receiver picks the const
    # twin regardless) -- mirror of the AST's receiver-const arm in
    # `_is_const_indirect`.
    if isinstance(stmt.init, (TpySubscript, TpyMethodCall)) \
            and _f1_const_rooted_source(stmt.init.obj, func, analyzer,
                                        const_locals, record_name):
        return True
    # Borrow-alias of an lvalue rooted in a const source (`p = ps[i]`,
    # `r = obj.field`, `c = self.store[k]`): mirror of the AST REF_ALIAS
    # const propagation via `is_const_union_source` -- recurse through
    # chained field/subscript access to the base name. The reassigned POINTER
    # sibling (`x = a` off a const-ref param -> `const T* x = &(a);`) roots the
    # same way -- the name branch of the AST's `_is_const_indirect`.
    if binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER) \
            and _f1_const_rooted_source(
                stmt.init, func, analyzer, const_locals, record_name):
        return True
    return False

def _f1_const_rooted_source(expr: TpyExpr, func: TpyFunction, analyzer,
                            const_locals: set[str],
                            record_name: str | None) -> bool:
    """Mirror of codegen's `is_const_union_source`: True when `expr` is an
    lvalue rooted in a const source (param in `const_borrow_params` / const F1
    local), recursing through chained field/subscript access to the base name."""
    if isinstance(expr, TpyCoerce):
        return _f1_const_rooted_source(expr.expr, func, analyzer, const_locals, record_name)
    if isinstance(expr, TpyName):
        # A bare-name alias source (`w = v`): const iff the aliased name is a
        # const F1 local or a const/deep-const borrow param -- the name branch
        # of the AST's `_is_const_indirect`.
        return (expr.name in const_locals
                or _param_is_const(expr.name, func, analyzer, record_name)
                or _param_is_deep_const(expr.name, func, analyzer, record_name))
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        obj = expr.obj
        if isinstance(obj, TpyName):
            return (obj.name in const_locals
                    or _param_is_const(obj.name, func, analyzer, record_name))
        return _f1_const_rooted_source(obj, func, analyzer, const_locals, record_name)
    return False

def _operand_type(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> TpyType | None:
    # The operand's resolved type for the mixed-sign comparison gate. For a
    # local/param name use the tracked resolved type -- codegen's get_resolved_type
    # reads ctx.var_types, which holds e.g. a retro-widened literal-seeded local's
    # final type (UInt64), whereas analyzer.get_expr_type returns the pre-widen
    # seed (Int32). Using the seed would over-exclude same-sign-after-widen loops.
    if isinstance(e, TpyName):
        t = locals_.get(e.name)
        if t is not None:
            return t
    return analyzer.get_expr_type(e)

def _mixed_sign_compare(left: TpyType | None, right: TpyType | None) -> bool:
    # Mirror of codegen's _mixed_sign_fixed_int (expressions.py): a signed-vs-
    # unsigned fixed-int comparison emits std::cmp_* (and a mixed-sign one with a
    # coercion target emits a cast), never the bare `(l op r)` the slice emits --
    # so exclude it. Built on the same int_traits_of primitive; the byte-identical
    # net gates any drift from the codegen predicate.
    if not (is_fixed_int_type(left) and is_fixed_int_type(right)):
        return False
    lt, rt = int_traits_of(left), int_traits_of(right)
    return lt is not None and rt is not None and lt.signed != rt.signed

def _union_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two SAME-TYPE value-union compare operands: `std::variant`'s own
    comparison operators, the rb=None bare-operator arm -- `(a == b)` on both
    paths. A union-vs-member compare renders the bare mixed pair on the AST
    path (invalid C++, the union-operand BUGS.md class) -> AST path; the
    equal-union requirement rejects it."""
    u = _eligible_value_union(lt)
    return u is not None and u == _eligible_value_union(rt)

def _any_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two `Any` compare operands (`a == b` / `a != b`): `tpy::Any`'s own
    equality operator, the rb=None bare-operator arm -- `(a == b)` on both
    paths (typeid + underlying compare done inside the runtime operator). An
    `is None` typeid probe is a separate face (the `_IS_OPS` arm)."""
    def _is_any(t: TpyType | None) -> bool:
        return t is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))), AnyType)
    return _is_any(lt) and _is_any(rt)

def _record_compare_operand(t: TpyType | None) -> 'NominalType | None':
    """A user-record compare operand's nominal type, or None. Sema only
    resolves a record compare when the record carries the generated comparison
    operators (`@dataclass(eq=...)` / `order=True`, `@total_ordering`, a user
    dunder), so the emit is the bare C++ operator or the dunder's
    `{self} OP {0}` template -- _gen_binop's `is_record` arm / the resolved-binop
    arm, uniform across non-generic and generic records (a generic record's
    dunder like `Box.__eq__[T: Equatable]` resolves to the same operator
    template, `((a) == (b))` on both paths). The same-type equality at the pair
    keeps a mixed-instantiation compare (a sema error) out."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, NominalType) and t.is_user_record:
        return t
    return None

def _record_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two SAME user-record compare operands: the record's generated C++
    comparison (bare operator for a derived `!=`/`<=`, or the dunder template),
    mirroring _gen_binop's record arm. A mixed record/other pair is a sema
    error, so the equal-record requirement is the only admitted shape."""
    lu = _record_compare_operand(lt)
    return lu is not None and lu == _record_compare_operand(rt)

def _optional_narrow_facts_ok(facts: dict[str, TpyType],
                              declared: dict[str, TpyType], analyzer) -> bool:
    """Assert-condition narrowing facts the slice needs no emit for: every
    fact narrows a pointer-repr `Optional[F1-record]` borrow name to its inner
    record (`assert p is not None` / `assert p`). Optional narrowing is
    sema-side only -- downstream reads arrive retyped with the runtime-check
    marker cleared, so the mirror is per-node and the assert emits just its
    condition (no extraction alias, unlike the isinstance facts)."""
    for var, fact in facts.items():
        opt = (_optional_ptr_borrow(declared.get(var), analyzer)
               if var in declared else None)
        if opt is None or unwrap_readonly(opt.inner) != fact:
            return False
    return True

def _value_opt_rvalue(e: TpyExpr, analyzer) -> 'OptionalType | None':
    """A value-repr Optional RVALUE `is [not] None` operand that is a call
    (`pick(...) is None` on a `-> str | None` / `bytes | None` / `int | None`
    callee): the materialized result is `std::optional<T>` by value, so the
    None-test reads `.has_value()` over it -- the same _gen_binop storage-form
    arm the value-opt NAME/FIELD rows key. No narrowing applies to an rvalue,
    so the RESOLVED type is authoritative (unlike the name rows, which key the
    declared binding). A container-ELEMENT subscript read of a value-opt
    element (`items[1] is None` on `list[Int32 | None]`) takes the same
    `.has_value()` over the read (`::tpy::__getitem__(items, 1).has_value()`);
    the subscript arm re-validates receiver/index under allow_whole_optional.
    Names / fields / None-literals are handled by the other operand rows; a
    pointer-repr optional rvalue is excluded here."""
    if isinstance(e, TpySubscript):
        if isinstance(e.index, TpySlice) or e.slice_function_info is not None:
            return None
    elif not isinstance(e, (TpyCall, TpyMethodCall)):
        return None
    t = analyzer.get_expr_type(e)
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OptionalType) and not u.uses_pointer_repr():
        return u
    return None

def _is_none_compare_operand(e: TpyBinOp, locals_: dict[str, TpyType],
                             analyzer) -> 'TpyExpr | None':
    """The Optional operand of an admitted `is [not] None` test, or None. The
    shape is exactly one `None` literal against a pointer-repr
    `Optional[F1-record]` borrow NAME (the declared type, matching the AST's
    `get_resolved_type` -- a flow-narrowed `p` still renders the pointer
    compare), a value-repr Optional name, a value-repr Optional call RVALUE, or
    an Optional FIELD subject (`self.f is None` -- storage is std::optional<T>
    whatever the repr, so the AST compares `.has_value()`, _gen_binop's
    storage-form arm). Shared by `_lower_binop` and the lowering (`_lower_expr`'s
    is-arm) so both key one verdict."""
    left_none = isinstance(e.left, TpyNoneLiteral)
    right_none = isinstance(e.right, TpyNoneLiteral)
    if left_none == right_none:  # both or neither
        return None
    operand = e.right if left_none else e.left
    if isinstance(operand, TpyNamedExpr):
        # A walrus None-test operand: a ptr-Optional target wraps the
        # pointer compare (`((t = optional_to_ptr(...)) != nullptr)`), a
        # value-opt target the has_value test (`(!(x = ...).has_value())`);
        # the walrus arm validates target/source shapes during lowering.
        wt = analyzer.get_expr_type(operand)
        if (_optional_ptr_borrow(wt, analyzer) is not None
                or _value_opt_scalar(wt, analyzer) is not None):
            return operand
        return None
    if (_optional_ptr_borrow_name(operand, locals_, analyzer) is None
            and _value_opt_scalar_name(operand, locals_, analyzer) is None
            and _value_opt_view_name(operand, locals_, analyzer) is None
            and _value_opt_rvalue(operand, analyzer) is None
            and not _ptr_value_none_name(operand, locals_, analyzer)
            and not _ptr_value_none_field(operand, locals_, analyzer)
            and not _optional_field_none_subject(operand, locals_, analyzer)
            and _union_none_name(operand, locals_, analyzer) is None):
        return None
    return operand

def _union_none_name(e: TpyExpr, locals_: dict[str, TpyType],
                     analyzer) -> 'UnionType | None':
    """A union-typed NAME subject of an `is [not] None` test (`v is None` on
    `v: Int32 | Dog | None`): the AST's monostate arm renders
    `std::holds_alternative<std::monostate>(v)` over the bare binding --
    identical for value- and pointer-variant reprs (monostate is a value
    member in both). Keyed on the DECLARED type like the AST's
    `get_resolved_type` (a flow-narrowed subject still tests the variant
    binding; the lowering rejects narrowed names whose READ is an extraction
    alias). Wrapper (recursive-alias) unions read through `.value` -- a
    different render, out of slice. Locals only (union globals are never
    seeded, so a global subject's body falls back whole)."""
    if not (isinstance(e, TpyName) and e.name in locals_):
        return None
    dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[e.name])))
    if not isinstance(dt, UnionType) or dt.needs_wrapper():
        return None
    if not any(is_void_like_type(m) for m in dt.members):
        return None
    return dt

def _any_none_subject(e: TpyBinOp, locals_: dict[str, TpyType],
                      analyzer) -> 'TpyExpr | None':
    """The `Any` operand of an `Any is [not] None` test, or None. The subject
    renders bare and is substituted twice into the D15 typeid probe, so it is
    restricted to a bare in-scope Any NAME (no double-eval side effect), matching
    the AST's `gen_expr(any_expr)` over the plain name."""
    left_none = isinstance(e.left, TpyNoneLiteral)
    right_none = isinstance(e.right, TpyNoneLiteral)
    if left_none == right_none:  # both or neither
        return None
    operand = e.right if left_none else e.left
    if not (isinstance(operand, TpyName) and operand.name in locals_):
        return None
    at = analyzer.get_expr_type(operand)
    if at is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))), AnyType):
        return operand
    return None

def _optional_field_none_subject(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A one-level Optional FIELD subject of an `is [not] None` test
    (`self.f is None` / `cfg.port is not None`): declared field storage is
    `std::optional<T>` whatever the repr, so the AST renders the storage-form
    `.has_value()` compare over the bare member read. Keyed on the DECLARED
    field type only: a sema-NARROWED subject (analyzed type is the payload)
    renders the identical bare-member `.has_value()` -- the AST's
    narrowed-field recovery arm in `_gen_binop` -- so it is admitted the
    same way. Same receiver/marker shape as the plain field arm."""
    if not (isinstance(e, TpyFieldAccess) and isinstance(e.obj, TpyName)
            and _field_markers_clean(e)
            and _field_receiver_ok(e, locals_, analyzer)):
        return False
    fdt = _field_decl_type(e, locals_, analyzer)
    return fdt is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt))),
        OptionalType)

def _ptr_value_none_field(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> bool:
    """A one-level `Ptr[T]`-value FIELD subject of an `is [not] None` test
    (`s.p is None` on a `p: Ptr[T]` field): declared storage is a raw `T*`,
    so the AST renders the `(s.p == nullptr)` pointer compare over the bare
    member read -- NOT the `.has_value()` of the Optional-field arm. Same
    receiver/marker admission as the plain field read."""
    if not (isinstance(e, TpyFieldAccess) and isinstance(e.obj, TpyName)
            and _field_markers_clean(e)
            and _field_receiver_ok(e, locals_, analyzer)):
        return False
    return _eligible_ptr_value(_field_decl_type(e, locals_, analyzer), analyzer)

def _narrowed_opt_field_read(e: TpyFieldAccess, rtype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A sema-NARROWED Optional field read: the declared field type is
    Optional but the analyzed read type is not (the flow proof), so a value
    position unwraps `(*recv.field)` -- gen_expr_deref's
    is_narrowed_optional_field arm. Stateless like the AST's: keyed on the
    declared-vs-analyzed type mismatch, no narrowing scope involved."""
    if not isinstance(e.obj, TpyName):
        return False
    if rtype is None or isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
            OptionalType):
        return False
    fdt = _field_decl_type(e, locals_, analyzer)
    return fdt is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt))),
        OptionalType)

def _nonvalue_container_ret(ret: TpyType | None) -> bool:
    """A non-value builtin-container call return (`list`/`dict`/`set`) admitted
    as a for-each iterable: an `Own[...]` return arrives Own-stripped from
    `get_expr_type` (a by-value rvalue), a borrow return Ref-stripped (a C++
    lvalue), a readonly borrow return ReadonlyType-wrapped -- all three capture
    into `__obj_N` and iterate identically past the `auto`-vs-`auto&` verdict
    (`_call_iterable_lvalue`). Array/Span returns (value types) and bytes
    (admitted by the value-position check already) stay gate-excluded."""
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_send_sync(ret))
    return is_list(t) or is_dict(t) or is_set(t)

def _resolve_literal_seeded(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """Resolve a literal-seeded analyzer type to its final form: a PENDING
    container (PendingListType -> list, or the read-only demoted Array) and
    IntLiteral element types (through the module default) -- the pair the
    AST's render-time get_resolved_type applies. One helper so the storage
    families and the Own-slot arg rows cannot drift apart."""
    if t is None:
        return None
    t = resolve_pending_container(t, analyzer) or t
    return resolve_int_literals(t, analyzer.ctx.default_int_for_literal)

def _storage_call_ret(ret: TpyType | None, analyzer) -> TpyType | None:
    """A call result admitted at the storage decl-init / return sinks: an
    owned builtin container (list/dict of the literal-decl families, or a
    set of scalar/owned-str elements -- so the spelled decl type and the
    downstream receiver gates line up), a value tuple, or a value union.
    Each renders the bare `f(args)` into `T x = f(...);` / `return f(...);`
    on both paths. Optional (the `__slot_N` + optional_to_ptr hoist),
    pointer-variant unions (the to_ptr_variant lift), and Array/Span results
    keep their own reject tags."""
    if ret is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    t = _resolve_literal_seeded(t, analyzer)
    if is_list(t) or is_dict(t):
        if _container_scalar_read(t, analyzer):
            return t
        # An owned-bytes-element list (`parts = b"a,b".split(b",")` -- the
        # bare rvalue copy into the decl slot); element reads gate their own
        # consumers (the for-each bytes element already routes).
        if is_list(t):
            args = getattr(t, "type_args", None)
            if args and is_bytes_type(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(args[0])))):
                return t
        return None
    if is_bytearray_type(t):
        # `ba = bytearray(...)` -- a scalar container (std::vector<uint8_t>),
        # the same plain-copy decl render as a scalar list.
        return t
    if is_set(t):
        args = getattr(t, "type_args", None)
        if bool(args) and (_eligible_scalar(args[0])
                           or _owned_str_slot(args[0], analyzer)
                           or isinstance(unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(args[0]))), AnyType)):
            return t
        return None
    if _value_tuple(t, analyzer) is not None:
        return t
    if _eligible_value_union(t) is not None:
        return t
    if isinstance(t, UnionType) and t.needs_wrapper():
        # A recursive-union WRAPPER result (`v = json.loads(s)` ->
        # `::tpystd::json::JsonValue v = ::tpystd::json::loads(..);`): the
        # bare call lands in the spelled wrapper slot like a value union.
        return t
    return None

def _container_storage_return_call_ret(ret: TpyType | None, analyzer) -> bool:
    """A container-typed call result at a position whose render does not read
    the ELEMENT type -- element-blind, unlike `_storage_call_ret`, whose scalar
    guard is a DECL consumer concern (subsequent element reads must render
    bare). Two such positions:

    - the STORAGE return sink (`return make_recs()` -> `return
      make_recs(args);`): the whole container is returned bare into the
      storage-form `std::vector`/`map`/`set` slot, with no per-element
      conversion, so a record / nested-container element list renders the same
      bare call a scalar-element one does. A container-of-records DECL is
      blocked at its own `decl.slot_type` local slot before the call gate, so
      this does not reach the decl path;
    - the generic-type INSTANTIATION result (`list(it)` / `set(xs)`): sema has
      already substituted the whole container into the ctor's `@cpp_template`
      (`::tpy::construct<std::vector<Point>>({0})`), so again nothing in the
      render reads the element. The sink this call then lands in -- decl slot,
      rebind, return, arg -- runs its own family check, so it is not the
      instantiation expression's job.
    """
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    return is_list(t) or is_dict(t) or is_set(t)


def _owned_tuple_call_ret(ret: TpyType | None, analyzer) -> 'TupleType | None':
    """A call-result tuple with at least one `Own[F1-record]` element, every
    other element a value scalar or str -- the tuple-unpack move-out family
    (`a, b = socket.socketpair()`). Admitted ONLY at the standalone
    tuple-unpack SOURCE (use.tuple_source): the bare `auto __tup_N =
    f(...);` capture + per-element `std::move(std::get<i>)` decls mirror the
    AST arm there. Deliberately NOT folded into `_storage_call_ret`: the
    decl-init `storage_call` escape bypasses the decl slot gate, and an
    Own-tuple LOCAL decl is an unrouted slot."""
    if ret is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if not isinstance(t, TupleType):
        return None
    if not any(isinstance(e, OwnType) for e in t.element_types):
        return None
    for e in t.element_types:
        if isinstance(e, OwnType):
            if not _f1_record(e.wrapped, analyzer):
                return None
        elif not (_eligible_scalar(e)
                  or _resolved_str_value(unwrap_ref_type(e), analyzer)
                  is not None):
            return None
    return t

def _storage_call_container(t: TpyType) -> bool:
    """Whether a `_storage_call_ret` verdict is the container family -- the
    one whose reassigned locals take the AST's pointer-local machinery
    (tuples/unions are value types; their reassign is a plain value assign)."""
    return is_list(t) or is_dict(t) or is_set(t)

def _own_declared_call_ret(call: 'TpyCall | TpyMethodCall') -> bool:
    """Whether the callee's DECLARED return is `Own[...]` -- the owning
    signal sema strips from the call expr's stamped type (the same fi
    consult as `_call_iterable_lvalue`; the skeleton's
    `is_storage_form_source` keys owning tuple slots on it)."""
    fi = getattr(call, "resolved_function_info", None)
    rt = getattr(fi, "return_type", None) if fi is not None else None
    if not isinstance(rt, TpyType):
        return False
    return isinstance(unwrap_readonly(unwrap_send_sync(rt)), OwnType)

def _ptr_opt_borrow_call_ret(e: 'TpyCall | TpyMethodCall',
                             ret: 'TpyType | None') -> bool:
    """A call whose result is a ptr-repr `Optional[T]` the callee BORROWS
    (its declared return is not `Own[...]`), i.e. already a `T*` at the call
    site -- `callee_returns_own_ptr_optional`'s complement, which is what
    picks the AST's bare pass-through over the slot + `optional_to_ptr`
    lift."""
    return (isinstance(ret, OptionalType) and ret.uses_pointer_repr()
            and not _own_declared_call_ret(e))


def _call_iterable_lvalue(e: TpyCall, analyzer) -> bool:
    """`is_lvalue_iterable`'s call arm over the admitted iterable calls: an
    `Own[...]` return is a by-value rvalue even though `get_expr_type` strips
    the Own -- consult the fi like the AST does; a value-type (str) return is
    an rvalue (both take the owning `auto __obj_N =` capture); the remaining
    admitted shape is a borrow return (`T&` / `const T&`), a C++ lvalue
    (`auto& __obj_N =`)."""
    rfi = e.resolved_function_info
    if rfi is not None and isinstance(rfi.return_type, OwnType):
        return False
    return not analyzer.get_expr_type(e).is_value_type()

def _member_valued_union_slot(a: TpyExpr, ptype: TpyType | None,
                              analyzer) -> bool:
    """A union param slot receiving a MEMBER-valued scalar arg (`take_vu(k)` /
    `take_vu(2.5)` -- the arg's expr type is a member, not the union):
    `_gen_union_arg`'s value branch hoists a `std::variant<...> __tmp_N = v;`
    temp -- the arg-temp row (`_value_union_temp_arg`) where the statement
    position flushes, AST otherwise. An
    already-union arg (a same-union name, the union-coerced literal) is NOT
    member-valued and rides its own pass-through arm. Guards the bare-scalar
    arg disjunct, which is slot-blind by construction."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    if not isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt))),
                      UnionType):
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return not isinstance(at, UnionType)

def _record_call_rvalue_operand(a: TpyExpr, analyzer) -> bool:
    """A user-record-returning call RVALUE at a consuming operand sink
    (compare operand, print arg): the sink threads BORROW_BIND so the
    call's rvalue record result is admitted by its own result gate --
    one helper so the sinks cannot drift."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    st = analyzer.get_expr_type(a)
    stu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
           if st is not None else None)
    if isinstance(stu, OwnType):
        stu = unwrap_readonly(stu.wrapped)
    return (isinstance(stu, NominalType) and stu.is_user_record
            and is_rvalue_source(analyzer, a))

def _value_union_temp_slot(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType],
                           analyzer) -> 'UnionType | None':
    """The value-union arg-temp row (`_gen_union_arg`'s value branch): a
    MEMBER-valued eligible-scalar arg into a non-pointer union slot hoists
    `std::variant<...> __tmp_N = <arg>;` and passes the bare temp name
    (`_maybe_move` is a no-op for the scalar shapes admitted). The slot
    unwraps Ref then readonly EXACTLY like the AST (`_gen_call` +
    `_gen_union_arg`) -- no Send/Sync peel, so a wrapped slot falls to the
    same default render on both paths. An already-union arg (a same-union
    name, the union-coerced int literal) is not member-valued and rides its
    pass-through arms; membership is a slice guard (sema already typed the
    arg against the union). Consumed by `_lower_call_arg` so admission and
    temp selection key on one verdict; the NAME-narrowing
    reject (a narrowed subject reads the extraction alias while the AST's
    `already_union` verdict renders it bare, temp-free) stays site-specific
    -- the gate reads `ws.narrowed`, lowering `lc.narrow`."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, UnionType):
        return None
    ut = _eligible_value_union(pt)
    if ut is None:
        return None
    # `_gen_union_arg`'s `already_union` verdict keys on the C++ DECLARED type:
    # a union-declared name whose read type sema retyped to a member
    # (`x: A | B = A(); f(x)`) is still the variant in C++ and passes bare (no
    # temp) -- it rides `_union_pass_through_arg`, not this member-valued row.
    if isinstance(a, TpyName):
        dt = locals_.get(a.name)
        dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
              if dt is not None else None)
        if isinstance(dt, UnionType):
            return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    # A bare float literal types as FloatLiteralType and lands in the union's
    # double member, rendering repr(v) -- resolve it like the f-string row
    # does. (An int literal never arrives bare: sema coerces it to the union,
    # the temp-free `_union_coerced_literal_arg` row.)
    if isinstance(a, TpyNoneLiteral):
        # `W("x", None)` at a value-union-with-None slot: the AST value
        # branch hoists the monostate temp (`std::variant<...> __tmp_N =
        # std::monostate{};`); the arm's init is the union-typed None
        # literal (emit's monostate render).
        if any(is_void_like_type(m) for m in ut.members):
            return ut
        return None
    if isinstance(at, IntLiteralType):
        # A bare int literal at a value-union ctor slot is NOT sema-coerced
        # (unlike the free-call row): the AST hoists the temp with the
        # target-less literal render (`__tmp_N = 1;`, the variant's
        # converting ctor picks the single int-family member).
        int_members = [m for m in ut.members
                       if is_fixed_int_type(unwrap_readonly(m))
                       or is_big_int_type(unwrap_readonly(m))]
        if len(int_members) == 1:
            return ut
        return None
    if isinstance(at, FloatLiteralType):
        at = FLOAT
    if isinstance(at, OwnType):
        # A record-ctor rvalue types as Own[member]; the variant temp
        # absorbs it by value either way.
        at = unwrap_readonly(at.wrapped)
    if at is not None and _value_record_member(at):
        # The value-RECORD member row (`datetime(.., tzinfo=ist)`, a member
        # ctor/call rvalue like `Holder(Fixed(60))` -- the ValueType-record
        # members store by value, so the same `std::variant<...> __tmp_N =
        # v;` hoist applies; the ArgTemp arm spells the variant via
        # render_type for cross-module members).
        if any(at == m for m in ut.members if not is_void_like_type(m)):
            return ut
        return None
    if not _eligible_scalar(at):
        return None
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return None
    return ut

def _ru_wrapper_arg_slot(ptype: TpyType | None) -> 'UnionType | None':
    """A recursive-union WRAPPER arg slot (`v: JsonValue` -- the expanded
    non-generic UnionType whose C++ form is the alias's wrapper struct), or
    None. `_gen_union_arg`'s value branch hoists `JsonValue __tmp_N =
    <arg>;` (create_typed, `= init` form) and passes the bare temp name.
    Mirrors that branch's unwrap exactly (readonly only -- a Ref/SendSync
    wrapper leaves the AST branch inert, so both paths take the default
    render). A generic alias instance is a `RecursiveAliasInstanceType`,
    never a UnionType, so the key naturally excludes it."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if not isinstance(pt, UnionType) or not pt.needs_wrapper():
        return None
    if is_ptr_variant_union(pt):
        return None
    return pt

def _ru_wrapper_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                         locals_: dict[str, TpyType],
                         narrowed: 'AbstractSet[str]') -> bool:
    """A wrapper-union NAME at a same-wrapper arg slot (`json.dumps(v)` on
    `v: JsonValue`): the binding is already the wrapper struct, so both
    paths render the bare name (no lift, no temp). An F6-NARROWED name
    passes only when its branch fact is a MEMBER of the slot's union: the
    extraction alias renders bare and the wrapper's converting ctor absorbs
    it (`dumps(__d, ...)`); any other narrowed shape keeps rejecting."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, TpyName) or a.name not in locals_:
        return False
    dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if a.name in narrowed:
        return (any(dt == m for m in ut.members if not is_void_like_type(m))
                and _witness("arg.ru_wrapper_narrowed"))
    return dt == ut

def _ru_wrapper_member_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                                locals_: dict[str, TpyType],
                                narrowed: 'AbstractSet[str]',
                                ) -> 'UnionType | None':
    """A member-typed NAME into a wrapper-union slot (`head(b)` on
    `b: list[Tree]`): `_gen_union_arg`'s value branch hoists the typed
    temp (`Tree __tmp_N = <name>;`, create_typed `= init` form) and passes
    the bare temp name; the init takes the `_maybe_move` wrap at a movable
    last use (mirrored at lowering). A same-union name is already_union
    (bare, no temp) and rides `_ru_wrapper_name_arg`; narrowed names keep
    rejecting (the AST's already_union verdict reads the C++ DECLARED
    type, while the lowered read is the extraction alias)."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, TpyName) or a.name not in locals_:
        return None
    if a.name in narrowed:
        return None
    dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    # The AST's value branch has NO membership check (sema already typed
    # the arg against the union) -- only already_union routes it elsewhere:
    # a union binding (bare pass-through) or a same-alias AliasRef
    # self-reference (also bare). Everything else hoists the typed temp.
    if isinstance(dt, (UnionType, AliasRef)):
        return None
    return ut


def _ru_wrapper_scalar_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> 'UnionType | None':
    """A scalar / str / bytes / bool LITERAL into a wrapper-union slot
    (`show(42)` on `Value = int | str | Neg` -> `Value __tmp_N = 42;` +
    the bare temp name) -- the literal sibling of
    `_ru_wrapper_member_name_arg`. `_gen_union_arg`'s value branch renders
    the init against the arg's OWN sema type, which for a literal is its
    target-less spelling, and hoists it with `create_typed`. Literals only:
    a name or call source brings `_maybe_move` / deref questions the
    neighbouring rows own."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None:
        return None
    if not isinstance(a, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                          TpyStrLiteral, TpyBytesLiteral)):
        return None
    at = analyzer.get_expr_type(a)
    # `already_union` routes a union-typed / same-alias source elsewhere (bare,
    # no temp); a literal can only be one of those through a sema coerce.
    if at is None or isinstance(at, (UnionType, AliasRef)):
        return None
    return ut


def _ru_container_literal_ok(a: TpyExpr, analyzer) -> bool:
    """A list/dict literal coercible into a recursive-union wrapper slot,
    admitted when the whole tree mirrors byte-identically: the container's
    sema type carries the NON-generic `AliasRef` placeholder element
    (`list[JsonValue]` / `dict[str, JsonValue]` -- the AST's typed-prefix,
    empty-spelling, and monostate arms all key on that shape), and every
    element is a scalar literal (target-less render on both paths), None
    (the wrapper's monostate), or a nested list/dict literal of the same
    family. Dict keys are str literals only (the owned-key `"k"` render).
    Everything else -- names, calls, f-strings, generic
    `RecursiveAliasInstanceType` elements -- stays AST."""
    at = analyzer.get_expr_type(a)
    at = resolve_pending_container(at, analyzer) or at
    if isinstance(a, TpyArrayLiteral):
        if not is_list(at):
            return False
        et = at.type_args[0] if getattr(at, "type_args", None) else None
        if not (isinstance(et, AliasRef) and not et.args
                and _ru_alias_copyable(et, analyzer)):
            return False
        return all(_ru_elem_ok(x, analyzer) for x in a.elements)
    if isinstance(a, TpyDictLiteral):
        if not is_dict(at):
            return False
        kt, vt = at.type_args[0], at.type_args[1]
        if not (isinstance(vt, AliasRef) and not vt.args
                and is_str_type(kt)
                and _ru_alias_copyable(vt, analyzer)):
            return False
        return (all(isinstance(k, TpyStrLiteral) for k in a.keys)
                and all(_ru_elem_ok(v, analyzer) for v in a.values))
    return False

def _ru_alias_copyable(et: 'AliasRef', analyzer) -> bool:
    """No noncopyable member in the alias's union body: a nocopy member
    would flip the AST render to make_vector / make_ordered_map
    (`_is_nocopy_container_element`'s AliasRef arm) -- unmirrored."""
    alias = analyzer.registry.resolve_alias_ref(et)
    if not isinstance(alias, UnionType):
        return False
    return not any(_cpp_noncopyable_type(m, analyzer)
                   for m in alias.members if not is_void_like_type(m))

def _ru_instance_literal_ok(a: TpyExpr, analyzer) -> bool:
    """A list/dict literal whose sema type involves a GENERIC recursive-alias
    INSTANCE -- the generic sibling of `_ru_container_literal_ok`, whose
    literals type as `list[AliasRef]`.

    Two shapes, because sema types the outer and the nested literals
    differently: the OUTER literal at a `Tree[int]` slot types AS the wrapper
    (`Tree[int]`), while a NESTED one types as the container of wrappers
    (`list[Tree[int]]`). Both render the same way -- the AST spells the
    container of wrapper members -- so the lowering synthesises the container
    only for the first. Element rules are shared with the non-generic arm."""
    at = analyzer.get_expr_type(a)
    at = resolve_pending_container(at, analyzer) or at
    if isinstance(at, RecursiveAliasInstanceType):
        pass
    elif isinstance(a, TpyArrayLiteral) and is_list(at):
        args = getattr(at, "type_args", None)
        if not (args and isinstance(args[0], RecursiveAliasInstanceType)):
            return False
    elif isinstance(a, TpyDictLiteral) and is_dict(at):
        args = getattr(at, "type_args", None)
        if not (args and len(args) > 1
                and isinstance(args[1], RecursiveAliasInstanceType)):
            return False
    else:
        return False
    # A `None` element is EXCLUDED here, unlike the non-generic arm: the
    # wrapper's monostate render keys on the element's result type being the
    # UnionType, and an alias INSTANCE renders `nullptr` instead of
    # `std::monostate{}`. Probe-verified divergence -- re-admit only with the
    # instance's underlying union threaded into `_lower_ru_elem`.
    def elem_ok(x: TpyExpr) -> bool:
        return not isinstance(x, TpyNoneLiteral) and _ru_elem_ok(x, analyzer)

    if isinstance(a, TpyArrayLiteral):
        return all(elem_ok(x) for x in a.elements)
    if isinstance(a, TpyDictLiteral):
        return (all(isinstance(k, TpyStrLiteral) for k in a.keys)
                and all(elem_ok(v) for v in a.values))
    return False


def _ru_elem_ok(x: TpyExpr, analyzer) -> bool:
    """One recursive-union container element (see `_ru_container_literal_ok`).
    Int literals stay inside int32 so the render is the bare token on both
    paths (a wider literal takes the width-pinned ctor spelling); floats stay
    finite (inf/nan take their own spellings)."""
    if isinstance(x, (TpyArrayLiteral, TpyDictLiteral)):
        # A nested literal types either way depending on whether the alias is
        # generic -- `list[AliasRef]` for the plain form, the alias INSTANCE
        # for `Tree[int]` -- and both render the same nested container.
        return (_ru_container_literal_ok(x, analyzer)
                or _ru_instance_literal_ok(x, analyzer))
    if isinstance(x, TpyNoneLiteral):
        return True
    if isinstance(x, TpyIntLiteral):
        return -(2 ** 31) < x.value < 2 ** 31
    if isinstance(x, TpyFloatLiteral):
        return math.isfinite(x.value)
    return isinstance(x, (TpyBoolLiteral, TpyStrLiteral))

def _record_rvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                             analyzer, *,
                             frame_capturing: bool = False,
                             upcast_ok: bool = False) -> 'NominalType | None':
    """The record-rvalue arg-temp row (the free-call `is_ref_param() +
    is_temporary_expr` cascade arm): a record RVALUE -- a ctor
    `A(7)` or a by-value record-returning call `make(7)` -- into a
    SAME-nominal plain record slot hoists `A __tmp_N = A(7);` and passes the
    temp name -- mutated (`A&`) and const (`const A&`) slots alike (the AST
    arm is mutation-blind). A readonly (`readonly[A]`) slot hoists only for
    a frame-capturing callee (`frame_capturing`) -- a sync callee binds the
    rvalue inline on the const ref (statement lifetime, CPython drop
    timing; the `own.readonly_ctor` bare arm admits that shape).
    `TempState.create` renders the slot type's bare `to_cpp()`, which the F1
    restriction keeps equal to the ctor's own spelling (raw name
    same-module, `native_cpp_names` qualification cross-module).
    A SUBCLASS-typed rvalue declares the CHILD's type (`Dog __tmp_1 =
    Dog();` into a `const Animal&` slot -- the AST's `temps.create(
    arg_type, ..)` upcast temp; C++'s implicit derived-to-base binding
    does the rest), so the CHILD type is returned -- but ONLY on the
    FREE-call row (`upcast_ok`): the ctor mutated-slot row spells the
    SLOT type instead (`Base __tmp_1 = Child();` -- dualgen-verified),
    so the ctor consumers keep the same-nominal slice. A
    borrow-returning call is not an rvalue source (the AST binds/copies
    without this temp) and rejects. Shared by the local slot classifier
    and `_lower_call_arg`; recursive lowering validates the source
    call's arguments."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_ref_type(pt)
    if isinstance(pt, ReadonlyType) and not frame_capturing:
        return None
    pt = unwrap_readonly(pt)
    if not (isinstance(pt, NominalType) and pt.is_user_record
            and pt.is_ref_param()):
        return None
    if not _f1_record(pt, analyzer):
        return None
    # TpyMethodCall: the module-qualified ctor spelling (`pcre2.Code(7)`);
    # the shape half of each consumer keeps other method-call sources out.
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return None
    if not is_rvalue_source(analyzer, a):
        return None
    at = analyzer.get_expr_type(a)
    if at == pt:
        return pt
    if (upcast_ok
            and isinstance(at, NominalType) and _f1_record(at, analyzer)
            and analyzer.registry.is_subclass_of(at, pt)):
        return at
    return None

def _own_cascade_fires(ptype: TpyType | None) -> bool:
    """Whether gen_call_arg's ownership cascade fires for this slot: an
    `Own[T]` (or `Own[T] | None`) payload survives the cascade's own unwrap
    (readonly + Ref -- NOT Send/Sync: a Send/Sync-wrapped slot leaves the
    cascade inert, so both paths render bare). The scalar pass-through arms
    must reject such slots -- a bare name would silently skip the AST's
    copy+move temp (the latent slot-blind hole, the Own analog of
    `_member_valued_union_slot`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    return unwrap_optional_own(unwrap_readonly(unwrap_ref_type(pt))) is not None

def _plain_own_slot(ptype: TpyType | None) -> TpyType | None:
    """The readonly-unwrapped payload of a PLAIN `Own[T]` call-arg slot, or
    None. The `Own[T] | None` face is excluded -- its indirect-name args take
    the `ptr_to_optional[_move]` wrap arm -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, OwnType):
        return None
    return unwrap_readonly(pt.wrapped)

def _own_lvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                          analyzer) -> TpyType | None:
    """Slot/shape verdict for the Own-slot copy+move row -- a NAME / eligible
    field read (possibly coerce-wrapped, see below) into a plain `Own[T]`
    slot of eligible-scalar, str, or same-nominal F1-record payload. Renders
    `auto __tmp_N = <arg>;` + `f(std::move(__tmp_N))` (a str payload declares
    the owned type instead: `std::string __tmp_N{<arg>};` -- the copy is the
    view->owned CONVERSION, gen_call_arg's `is_any_str_type` branch) -- or
    the temp-free `f(std::move(name))` when the name is movable at its last
    use (gen_call_arg's `_maybe_move` arm, decided at lowering from the same
    `movable_locals` + last-use facts; a scalar is never movable, a
    pointer-local is a non-owning borrow -- both always copy). Shared by the
    gate (`_own_lvalue_arg`, which adds the locals_/narrowing rejects) and
    `_lower_call_arg` (which adds the lc-side narrowing reject and picks
    `THIRMove` vs `THIRArgTemp`).

    A COERCE-WRAPPED lvalue splits on the AST's `needs_copy` rendered-string
    identity test, mirrored here by coercion KIND via `_coerce_disposition`
    (a KIND's render either always equals its inner or always wraps it, so
    the two tests agree wherever the name itself renders plain): an
    all-identity chain renders as the bare
    lvalue, so the copy temp still applies (`a.append(v)` under an
    int-literal coerce); a wrapping link over a NAME produces an rvalue that
    binds the slot bare (needs_copy=False) -- not this row, unwitnessed ->
    AST; over a FIELD the AST test never runs (needs_copy stays True), so
    the temp hoists with the wrapped render as its init
    (`std::string __tmp_N{std::string(<enum-name render>)};`). The wrapped
    faces are payload-sliced to the witnessed scalar/str rows; the name-
    renders-plain factor the string test folds in (frame-slot `(*name)` /
    pointer-local derefs) is re-checked at lowering, which rejects those
    inners honestly."""
    w = _plain_own_slot(ptype)
    if w is None:
        return None
    if isinstance(a, TpyCoerce):
        bare = _peel_coerce(a)
        if not isinstance(bare, (TpyName, TpyFieldAccess)):
            return None
        disps = []
        c = a
        while isinstance(c, TpyCoerce):
            disps.append(_coerce_disposition(c, own_slot_arg=True))
            c = c.expr
        if any(d is None for d in disps):
            return None
        if isinstance(bare, TpyName):
            if any(d != "identity" for d in disps):
                # needs_copy=False: the wrapped rvalue binds the slot bare.
                return None
            # An identity chain means source and slot payload share one C++
            # type (what the AST's render==name test certifies), so the
            # peeled name stands in for the at-check below.
            return w if _eligible_scalar(w) else None
        # FIELD inner: the AST test never runs, the copy temp is
        # unconditional and its init carries the (possibly wrapping) chain
        # render; str is the witnessed payload.
        return w if is_str_type(w) else None
    # A TERNARY is admitted for the COPY half only: it binds as an lvalue
    # reference the `T&&` slot cannot take, so `_maybe_move` never fires and
    # the cascade always hoists `auto __tmp_N = ((c) ? (a) : (b));` + the
    # move wrap. The move-source rows below all require a NAME, so widening
    # the shape here cannot hand a ternary the temp-free render.
    if not isinstance(a, (TpyName, TpyFieldAccess, TpyIfExpr)):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OwnType):
        # An Own[T]-bound param name reads bare, like any owned local.
        at = unwrap_readonly(at.wrapped)
    if _eligible_scalar(w):
        # A literal-seeded name's use site may still carry an IntLiteralType
        # (`for v in [5, 3]: heappush(h, v)`); the render is the same bare
        # name either way, so resolve like `_resolved_scalar`.
        if at is not None:
            at = resolve_int_literals(at, analyzer.ctx.default_int_for_literal)
        return w if _eligible_scalar(at) else None
    if is_str_type(w):
        # An owned-str slot fed by a str-family FIELD read
        # (`dropped.append(self.label)`): the copy temp declares the owned
        # type (`std::string __tmp_N{this->label};`) -- the view->owned
        # conversion the AST's `is_any_str_type` branch spells. A str NAME
        # stays out: no witness, and its local view/owned form split is a
        # separate render axis.
        if not isinstance(a, TpyFieldAccess):
            return None
        return w if (isinstance(at, NominalType)
                     and (is_str_type(at) or is_str_view_type(at)
                          or is_string_type(at))) else None
    if _f1_record(w, analyzer):
        # Same-nominal is a slice guard: sema rejects an upcast into an Own
        # slot outright, so no other pairing reaches codegen.
        return w if at == w else None
    if isinstance(w, UnionType) and (
            _eligible_value_union(w) is not None
            or _eligible_ptr_union(w, analyzer) is not None):
        # An `Own[union]` slot: the storage variant moves/copies whole
        # (`Sink(std::move(v))`), family-blind like the record row.
        return w if at == w else None
    if _is_type_param_slot(w):
        # A TypeParamRef payload (`Own[T]` slot fed by an `Own[T]` param in
        # a generic body): the copy renders the same `auto __tmp_N = <arg>;`
        # and the movable last use the same temp-free `std::move(name)`.
        return w if at == w else None
    wu = unwrap_readonly(unwrap_send_sync(w))
    if is_list(wu) or is_dict(wu) or is_set(wu) or is_bytearray_type(wu):
        # A builtin-container payload (`g.set(live)` into `Own[list[T]]`):
        # the same copy temp (`auto __tmp_N = live;` + the move wrap) and
        # the same temp-free move at a movable last use -- containers are
        # non-value, so the movable seeding applies exactly as for records.
        # A literal-seeded local's read is still a PendingListType here;
        # resolve it before the same-type compare.
        at_res = resolve_pending_container(at, analyzer) or at
        return w if at_res == wu else None
    if is_dyn_protocol(wu):
        # An already-erased `unique_ptr<P>` payload (`Box(initial)` off an
        # `Own[P]` param -- the classifier's 'forward' verdict): the same
        # temp-free `std::move(name)` at a movable last use / copy temp
        # otherwise. Same-protocol only (the slice guard the forward
        # verdict pins).
        return w if at == wu else None
    return None

def _optional_ptr_arg_slot(ptype: TpyType | None, analyzer) -> 'OptionalType | None':
    """The pointer-repr `Optional[F1-record]` call-arg slot of
    `_gen_optional_ptr_arg`'s non-protocol tail, or None. Mirrors the AST's
    unwrap (readonly only -- the arm keys on the raw param type, no Send/Sync
    peel). A protocol inner (the typed-null / adapter faces) fails the F1
    check; an `Own[T] | None` slot is storage-repr and never reaches here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if not (isinstance(pt, OptionalType) and pt.uses_pointer_repr()):
        return None
    if not _f1_record(unwrap_readonly(pt.inner), analyzer):
        return None
    return pt

def _optional_ptr_arg_face(a: TpyExpr, ptype: TpyType | None,
                           declared: dict[str, TpyType], analyzer) -> str | None:
    """Classify a call arg against a pointer-repr `Optional[record]` slot into
    its `_gen_optional_ptr_arg` face: 'none' (the `nullptr` literal), 'pass'
    (a pointer-repr Optional binding -- already `T*`, renders bare), 'name'
    (a plain record name -- `&(name)`, or bare for an F2 pointer-local, split
    at LOWERING from `lc.pointers`), 'lift' (a storage-form Optional field
    read, `::tpy::optional_to_ptr(...)`), or 'ctor' (a same-nominal
    record-ctor rvalue -- the `&(__tmp_N)` temp face, flushable positions
    only). A NARROWED union subject classifies 'name' (its expr type arrives
    member-stamped): the read renames to the extraction alias / inline get
    inside `_lower_expr` and both paths wrap `&(...)` -- the render mirrors.
    A record-element lvalue SUBSCRIPT off an lvalue container (`items[i]`, a
    `T&` off an in-scope name / admitted-field container) classifies
    'subscript': `&(<subscript>)`, mirroring the AST's `_gen_optional_ptr_arg`
    `&(gen)` tail. The subscript guards mirror `_borrow_elem_subscript_shape`
    (checks.py): an unproven-Optional container is rejected (the AST wraps the
    receiver in `deref_check`, which the THIR `subscript_prechecked` render
    drops -> null-container UB), and the container receiver must resolve
    through `_subscript_container_recv_type` (an lvalue name / admitted field)
    -- an rvalue container (`make_list()[i]`) is NOT caught by
    `is_rvalue_source` (it does not recurse into the subscript receiver), so
    `&(<dying temp>[i])` would dangle. None rejects: rvalue-container /
    unproven-Optional / slice subscripts, coerced args, `self`, non-ctor
    rvalues (`Ptr[T]`-typed calls etc. stay AST). Shared by the eligibility
    gate (which adds receiver checks) and `_lower_call_arg` so both key one
    verdict; constructor args are validated during recursive lowering."""
    ot = _optional_ptr_arg_slot(ptype, analyzer)
    if ot is None:
        return None
    inner = unwrap_readonly(ot.inner)
    if isinstance(a, TpyNoneLiteral):
        return 'none'
    if isinstance(a, TpySubscript):
        # Mirror _borrow_elem_subscript_shape (checks.py:561): reject the
        # unproven-Optional receiver (deref_check would be dropped) and any
        # slice/slice-function form, and require an lvalue-container receiver
        # (rvalue containers dangle -- is_rvalue_source does not recurse into
        # e.obj).
        if a.needs_optional_runtime_check:
            return None
        if a.slice_function_info is not None or isinstance(a.index, TpySlice):
            return None
        if _subscript_container_recv_type(a.obj, declared, analyzer) is None:
            return None
        at = analyzer.get_expr_type(a)
        at = unwrap_readonly(at) if at is not None else None
        if at == inner:
            return 'subscript'
        return None
    if isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is None or not fi.is_constructor:
            return None
        at = analyzer.get_expr_type(a)
        if at == inner:
            return 'ctor'
        # A SUBCLASS ctor rvalue: the CHILD-typed temp's address binds the
        # base pointer implicitly (`ClickEvent __tmp_2 = ClickEvent(..);
        # describe(&(__tmp_2))` -- the AST's upcast temp).
        if (isinstance(at, NominalType)
                and is_polymorphic_subclass_fact(inner, at,
                                                 analyzer.registry)):
            return 'ctor'
        return None
    if isinstance(a, TpyMethodCall):
        # A record-returning marker-call rvalue (`HTTPSConnection(..,
        # ssl.create_default_context())`): the AST tail hoists the same
        # `&(__tmp_N)` temp as a ctor rvalue; the call's own lowering
        # validates the marker kind/args recursively.
        if (analyzer.get_expr_type(a) == inner
                and is_rvalue_source(analyzer, a)):
            return 'ctor'
        return None
    if isinstance(a, TpyFieldAccess):
        at = analyzer.get_expr_type(a)
        at = unwrap_readonly(at) if at is not None else None
        if (isinstance(at, OptionalType)
                and unwrap_readonly(at.inner) == inner
                and reads_storage_form_optional(analyzer, a)):
            return 'lift'
        return None
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OptionalType):
        # An unnarrowed pointer-repr Optional binding (a `T | None` param /
        # OPTIONAL_TO_PTR local) is already `T*` -- the bare pass face.
        return 'pass' if (at.uses_pointer_repr()
                          and unwrap_readonly(at.inner) == inner) else None
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    return 'name' if at == inner else None

def _arg_ptr_union_slot(ptype: TpyType | None, analyzer,
                        *, readonly_target: bool = False,
                        ) -> 'tuple[UnionType, bool] | None':
    """The pointer-variant union of a non-Own call-arg slot (the member/None
    inline-lift target) plus its deep-const verdict (a `readonly[...]`
    annotation or the callee's `deep_const_borrow_params` fact, threaded as
    `readonly_target` -- the AST's `is_readonly_target`), or None. A
    deep-const slot spells const pointees on the lift and takes the
    `ptr_variant_to_const` wrap on already-union args. An `Own[union]` slot
    is the value-variant auto-move cascade -> AST path. Consumed by
    `_lower_call_arg` so admission and lift selection key on one verdict."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_send_sync(pt)
    deep_const = isinstance(pt, ReadonlyType) or readonly_target
    if isinstance(unwrap_readonly(pt), OwnType):
        return None
    ut = _eligible_ptr_union(pt, analyzer)
    if ut is None:
        return None
    return ut, deep_const

def _scalar_pass_through_slot(ptype: TpyType | None, analyzer) -> bool:
    """A method param slot the inline-template arg path passes a scalar into
    bare: a value scalar or `Own[scalar]`. Scalars are value types -- copied,
    never moved -- and `gen_call_arg`'s Own handling skips the copy+move temp for
    a template/native callee (`inline_template=True`), so the arg emits as the
    bare expression. Every other slot shape (record / Optional / union / tuple /
    Ptr / str / protocol / unsubstituted type param) takes a lift, move, or
    conversion the slice does not reproduce. A literal-seeded container's fi
    carries `Own[IntLiteral]` slots (resolved like every scalar-typed check)."""
    if ptype is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(ptype))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _resolved_scalar(t, analyzer)

def _field_read_ref_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                             locals_: dict[str, TpyType], analyzer,
                             *, mutated: bool = False) -> bool:
    """A bare FIELD read into a ctor REF slot whose referent is the field's own
    declared type (`IntListIter(this->items)`, `Pair<B, A>(p.second, p.first)`,
    `Grid<T, N>(this->_value)`): the member read binds the `const T&` slot
    directly, so the emit is the bare read on both paths -- the field twin of
    `_container_pass_through_arg`'s bare-NAME row, and the reason it covers
    containers and open-T alike is that the rule is slot/field type EQUALITY,
    not a family list.

    Keyed on the DECLARED field type, so a narrowed field types at its
    un-narrowed type and stays out (its AST render takes the unwrap). A
    MUTATED slot is excluded: the equality rule cannot see whether the callee
    writes through the `T&`, so the conservative fence is the same one the
    shared rows use."""
    if mutated or not isinstance(a, TpyFieldAccess):
        return False
    if not _field_receiver_ok(a, locals_, analyzer):
        return False
    if not isinstance(unwrap_send_sync(ptype), RefType):
        return False
    ft = _field_decl_type(a, locals_, analyzer)
    if ft is None:
        return False
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    return unwrap_readonly(ft) == slot


def _container_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType], analyzer) -> bool:
    """A container arg the AST passes as the bare name: a bare in-scope name
    with a builtin-container binding, into a NON-Own concrete builtin-container
    param (`std::vector<T>&` / `const ordered_map<K, V>&` / ...).
    `gen_call_arg`'s ownership cascade never fires for that slot shape (`own is
    None`), so the emit is the bare name on both paths. An `Own[container]`
    slot auto-moves at last use (`f(std::move(xs))`), a `Span` / protocol
    (`Iterable`) slot converts (`::tpy::as_mut_span(xs)` / adapter wrap), and an
    `Optional[container]` slot lifts (`&(xs)`), so those stay on the AST path.
    The container-LITERAL arg rides its own row (`_container_literal_arg`,
    checks.py -- it needs the element-shape gate defined there). The binding
    type is read from `locals_`, per the receiver-gate convention on
    `_resolved_scalar`. NB unlike the sibling `_scalar_pass_through_slot` (slot
    check only; the arg shape is checked separately at its call sites), this
    predicate owns BOTH sides -- the container arg shape is inseparable from the
    slot shape it pairs with."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    span_src = is_span(at)
    ba_src = is_bytearray_type(at)
    if not (span_src or ba_src or is_list(at) or is_dict(at)
            or is_set(at) or is_array(at)):
        return False
    if ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    # Explicit Own/Optional rejects (the move / address-of lift slots); the
    # concrete-container check below would also exclude them, but the invariant
    # should be self-evident, mirroring gen_call_arg's own Own detection.
    if isinstance(pt, (OwnType, OptionalType)):
        return False
    if span_src:
        # A span NAME into the SAME span slot: both sides are the by-value
        # view, so the arg renders bare (no as_span / const widen -- those
        # arrive as coerces and ride `_span_coerce_arg`).
        return at == pt
    if ba_src:
        # A `bytearray` name into a `bytearray` slot: the same
        # `std::vector<uint8_t>&` bare bind. NOT into a `bytes` slot -- that
        # pairing is the BytesView coerce, which arrives as its own node.
        return is_bytearray_type(pt)
    return is_list(pt) or is_dict(pt) or is_set(pt) or is_array(pt)

def _native_iterable_container_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   locals_: dict[str, TpyType]) -> bool:
    """A builtin-container name into a NATIVE builtin's structural `Iterable[T]`
    (or `Sequence[T]` -- `reversed(xs)`) protocol param (`all(xs)` / `any(xs)`
    / `sum(xs)` -> `::tpy::builtin_all(xs)`):
    the runtime overload is a C++ template that binds the container BARE, so no
    adapter/span conversion runs -- unlike a plain-TPy `Iterable` param, which
    `_container_pass_through_arg` leaves on the AST path for exactly that
    conversion. Native/@cpp_template loop ONLY (the caller gates the branch); in
    the plain loop the same slot would need the adapter wrap."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not (is_list(at) or is_dict(at) or is_set(at) or is_array(at)
            or is_span(at)):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")

def _value_opt_member_arg(a: TpyExpr, ptype: 'TpyType | None',
                          declared: dict[str, TpyType], analyzer) -> bool:
    """A member-typed arg into a VALUE-repr Optional slot (`std::optional<T>`
    by value): gen_call_arg has no value-optional arm at all, so the arg
    falls to the generic tail and the optional's converting ctor absorbs the
    bare member render (`f(5)`, `f("hi")`, `f(Color.Red)`, a bytes rvalue) --
    position-blind, mirrored by `_lower_call_arg`'s own tail. Excluded:
    `Optional[Own[...]]` slots (gen_call_arg's Own cascade), optional-typed
    args (the whole-optional rows), and any NAME/FIELD whose C++ binding is
    still the optional (a narrowed read -- the AST passes the WHOLE optional
    bare there, while the plain lowered read would deref)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not isinstance(u, OptionalType) or u.uses_pointer_repr():
        return False
    if isinstance(unwrap_readonly(unwrap_send_sync(u.inner)), OwnType):
        return False
    # Shape checks run on the peeled expr: a slot-coerced arg is stamped with
    # the OPTIONAL itself (`5` / `b"a" + b"b"` -> `T | None`) but still
    # renders as the bare member through the coerce arm.
    peeled = _peel_coerce(a)
    if isinstance(peeled, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                           TpyStrLiteral, TpyBytesLiteral)):
        return True
    at = analyzer.get_expr_type(peeled)
    if at is None:
        return False
    at_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at_u, (OptionalType, UnionType)):
        return False
    if isinstance(peeled, TpyName):
        dt = declared.get(peeled.name)
        if dt is None or peeled.name == "self":
            return False
        du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
        return not isinstance(du, (OptionalType, UnionType))
    if isinstance(peeled, TpyFieldAccess):
        # Only the type-level enum-member read (a fixed spelling); a data
        # field could be a narrowed optional field -> keep rejecting.
        return getattr(peeled, "enum_member_of", None) is not None
    # Member-typed rvalues (a scalar ctor, a bytes/str binop, a call): the
    # recursive lowering validates the expression itself.
    return isinstance(peeled, (TpyCall, TpyMethodCall, TpyBinOp, TpyUnaryOp))


def _native_iterable_field_arg(a: TpyExpr, ptype: 'TpyType | None',
                               declared: dict[str, TpyType], analyzer) -> bool:
    """The FIELD sibling of `_native_iterable_container_arg`: a container
    member read at a native builtin's structural `Iterable[T]` / `Sequence[T]`
    slot (`",".join(self._parts)` -> `::tpy::str_join(",", this->_parts)`).
    The member read renders bare exactly like the name row, and the runtime
    overload is the same binding-by-template, so no adapter conversion runs."""
    if not _field_receiver_ok(a, declared, analyzer):
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not (is_list(at) or is_dict(at) or is_set(at) or is_array(at)
            or is_span(at)):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")


def _native_iterable_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                              analyzer) -> bool:
    """A container-returning CALL rvalue into a NATIVE builtin's structural
    `Iterable[T]` / `Sequence[T]` slot (`zip(get_names(), get_scores())` ->
    `::tpy::builtin_zip<...>(get_names(), get_scores())`): the call-branch twin
    of `_native_iterable_container_arg`'s bare-name row. The runtime overload
    is a C++ template that binds the container BARE, so an `Own[list]`-returning
    free/method call renders in place with no move temp -- the Iterable slot is
    not `Own[T]`, so no ownership cascade fires. Native/@cpp_template loop ONLY
    (the caller gates the branch); the inner call is re-validated by its own
    value-position lowering, so an unroutable source falls the body back."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pb = _protocol_binding(ptype)
    if pb is None or pb.name not in ("Iterable", "Sequence"):
        return False
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    return (is_list(at) or is_dict(at) or is_set(at) or is_array(at)
            or is_span(at))

def _native_iterable_range_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A `range(...)` rvalue into a NATIVE builtin's structural `Iterable[T]`
    / `Sequence[T]` slot (`zip(range(3), names)` ->
    `::tpy::Range<int32_t>(3)` bound bare by the template): the
    instantiation ladder's range row on the native ladder. Shape (arity /
    counter-scalar) is validated at the render branch, which mirrors the
    instantiation arm's checks and falls the body back outside them."""
    if not _is_range_call(a):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")


def _native_iterable_genexpr_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A generator expression into a NATIVE builtin's `Iterable[T]` slot
    (`all(x > 0 for x in xs)`): the make_generator IIFE binds directly. Admitted
    broadly here; `_lower_genexpr` raises for the shapes outside its slice
    (range / filter / non-lvalue / unpack / owned / narrowed), so an unsupported
    genexpr falls the whole body back rather than misrouting."""
    if not isinstance(a, TpyGeneratorExpression):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name == "Iterable"

def _positional_only_template(tmpl: str, n_args: int) -> bool:
    """Whether a `@cpp_template` body contains only in-range positional
    placeholders (`{0}`, `{1}`, ...) and `{{`/`}}` literal-brace escapes --
    the subset `expand_cpp_template` can render with no receiver and no
    substitution context. A surviving named field (`{cpp}`, `{self}`, a type
    param) means sema's substitution did not fully resolve the template, so
    the call must stay on the AST path (which has the substitution machinery)."""
    i, n = 0, len(tmpl)
    while i < n:
        c = tmpl[i]
        if c == "{":
            if i + 1 < n and tmpl[i + 1] == "{":
                i += 2
                continue
            close = tmpl.find("}", i + 1)
            field = tmpl[i + 1:close] if close != -1 else ""
            if not field.isdigit() or int(field) >= n_args:
                return False
            i = close + 1
        elif c == "}":
            # Only the `}}` escape is admitted; a lone `}` (which expand would
            # pass through literally) never occurs in a real template -- reject
            # conservatively.
            if not (i + 1 < n and tmpl[i + 1] == "}"):
                return False
            i += 2
        else:
            i += 1
    return True

def _ctor_arg_slot_ok(ptype: TpyType | None, analyzer) -> bool:
    """A scalar-ctor param slot the arg passes bare: a value scalar /
    `Own[scalar]` (`_scalar_pass_through_slot`), or the generic conversion
    overload's unsubstituted method type param (`__init__[T: AnyFixedInt](self,
    x: T)`, the `int_cast_check` arm). `gen_call_arg`'s target hint is inert for
    a TypeParamRef slot -- not Own, not fixed-int, no borrow/storage lift -- so
    an eligible-scalar arg emits bare exactly as into a concrete scalar slot.
    The Ref/readonly peel mirrors gen_call_arg's own `ptype_inner` unwrap (the
    stub stores the generic param as `Ref(TypeParamRef)`)."""
    if _is_type_param_slot(ptype):
        return True
    return _scalar_pass_through_slot(ptype, analyzer)

def _template_init_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` FunctionInfo of a bare-name `@cpp_template`
    type-constructor call in the pure-template-expansion shape (the `_gen_call`
    `fi.cpp_template and not call_type` branch), or None. Shared by the scalar
    and slice-object ctor gates; each adds its own result/arg checks."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Markers that take earlier / different _gen_call branches. Explicit
    # type_args are rejected; INFERRED type args (the generic int_cast_check
    # overload) are fine -- the positional-only template makes gen_call_from_fi's
    # substitution loop a no-op.
    if (e.call_type is not None or e.type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return None
    fi = e.resolved_function_info
    if fi is None or not (fi.is_method and fi.name == "__init__"):
        return None
    # Post-call wrappers / special member forms the bare template emit does not
    # reproduce (mirrors method-call lowering's fi rejects).
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if not fi.cpp_template or not _positional_only_template(fi.cpp_template,
                                                            len(e.args)):
        return None
    if len(e.args) != len(fi.params):
        return None
    return fi

def _view_ctor_bare_source(e: TpyCall, rtype: 'TpyType | None',
                           locals_: dict[str, TpyType],
                           analyzer) -> bool:
    """A `Span[...]` / `Array[...]` instantiation whose args are all bare
    in-scope container / array / span NAMES. These are VALUE views: the AST
    spells the target type and direct-initializes it (`std::span<const
    int32_t>(lst)`), rather than going through the storage-container
    `make_vector` path. No ctor fi resolves for them, so the args carry no
    slot type -- which is why only bare names are admitted here; any shape
    whose render depends on a target type stays out."""
    if getattr(e, "call_type", None) is None or not e.args:
        return False
    if e.kwargs or getattr(e, "double_star_unpack", None) is not None:
        return False
    ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if not (is_span(ct) or is_array(ct)):
        return False
    for a in e.args:
        if not isinstance(a, TpyName) or a.name not in locals_:
            return False
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
        if not (is_list(at) or is_array(at) or is_span(at)):
            return False
    return True


def _array_literal_ctor_source(e: TpyCall, rtype: 'TpyType | None') -> bool:
    """An `Array[T, N]([...])` instantiation over an array LITERAL. The AST's
    resolved-ctor template arm skips an array-literal first arg outright, so
    this lands in the call_type tail, which spells the target type and hands
    the literal `call_type` as its brace target (`std::array<int32_t, 3>({10,
    20, 30})`). Restricted to Array's own param-less `__init__`: a user ctor
    taking a container param reaches the same tail with a real slot type, and
    the AST still threads `call_type` there -- a different render this arm must
    not claim."""
    if getattr(e, "call_type", None) is None or len(e.args) != 1:
        return False
    if e.kwargs or getattr(e, "double_star_unpack", None) is not None:
        return False
    if (e.subscript_callee is not None or e.type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert):
        return False
    if not isinstance(e.args[0], TpyArrayLiteral):
        return False
    if not is_array(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))):
        return False
    fi = e.resolved_function_info
    return fi is not None and not fi.params


def _template_ctor_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` fi of a bare-name type-constructor call whose
    emit is the ctor's positional `@cpp_template` expanded over inline args --
    the AST's `_gen_call` call_type-branch resolved-template arm
    (`gen_call_from_fi(ctor, None, gen_args)`). Shape only: it makes no
    judgement about the RESULT family, which is what decides whether that arm
    is the one the AST reaches, so each caller adds its own family gate."""
    if (not isinstance(e.func, TpyName) or not e.args
            or e.kwargs or e.double_star_unpack is not None):
        return None
    if (e.type_args or e.enum_from_value is not None
            or e.cast_target_type is not None or e.isinstance_var is not None
            or e.dunder_call is not None or e.macro_expansion is not None
            or e.compile_time_assert or e.subscript_callee is not None):
        return None
    fi = e.resolved_function_info
    if fi is None or not (fi.is_method and fi.name == "__init__"):
        return None
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if not fi.cpp_template or not _positional_only_template(fi.cpp_template,
                                                            len(e.args)):
        return None
    if len(e.args) != len(fi.params):
        return None
    return fi


def _inst_call_rvalue_arg(arg: TpyExpr, analyzer) -> bool:
    """A CALL RVALUE at an instantiation arg slot -- `set(make_nodes())`,
    `set(copy(b))`, `list(copy_iter(it))`, `list(heapq.merge(a, b))`. The AST
    renders it through the ordinary call-arg dispatch, inline into the
    construct template, exactly as the generator-factory and combinator arms
    above do for their narrower callee shapes.

    Keyed on the value CATEGORY, not on the callee: an rvalue owns its result,
    so none of the own_iter / last-use machinery the bare-NAME branch exists
    for can apply to it.

    Restricted to a CONTAINER result. The iterator-shaped rvalues in the same
    position (`copy_iter(..)`'s `CopyIter[T]`, a module-qualified generator
    factory) are blocked one layer down at the free-call result gate and the
    module-marker gate, so admitting them here would be unwitnessable
    surface."""
    if not isinstance(arg, (TpyCall, TpyMethodCall)):
        return False
    if not is_rvalue_source(analyzer, arg):
        return False
    rt = analyzer.get_expr_type(arg)
    if rt is None:
        return False
    rt = _resolve_literal_seeded(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt))), analyzer)
    return is_list(rt) or is_dict(rt) or is_set(rt)


def _container_literal_inst_slot(e: TpyCall, rtype: 'TpyType | None',
                                 analyzer) -> 'TpyType | None':
    """The element slot of a `list([...])` / `set([...])` instantiation -- a
    container ctor whose single arg is an array LITERAL -- or None. The AST's
    resolved-ctor template arm skips a literal first arg outright, so this
    lands in the call_type tail, which spells the result type around the
    literal's own braces (`::tpy::ordered_set<Node>({Node(2)})`).

    The literal must be lowered against a LIST of this slot, never against its
    own sema-resolved type (a read-only literal demotes to `Array[T, N]`, whose
    element target the AST does not apply here) and never against the call's
    own type (a `set` result would render the literal as a second
    `ordered_set`). Only scalar / F1-record element slots are admitted: those
    are exactly the families for which the AST's `target_type` yields no
    element target at all, so a list of the slot reproduces its render. Every
    family where the derivation DOES fire -- Optional / union / tuple / Any /
    str / bytes / recursive-alias elements, and any DICT result, whose elements
    retarget to `tuple[K, V]` -- stays out."""
    if getattr(e, "call_type", None) is None or len(e.args) != 1:
        return None
    if e.kwargs or getattr(e, "double_star_unpack", None) is not None:
        return None
    if (e.subscript_callee is not None or e.enum_from_value is not None
            or e.cast_target_type is not None or e.isinstance_var is not None
            or e.dunder_call is not None or e.macro_expansion is not None
            or e.compile_time_assert):
        return None
    lit = e.args[0]
    if not isinstance(lit, TpyArrayLiteral) or not lit.elements:
        return None
    rt = _resolve_literal_seeded(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))), analyzer)
    if rt is None or not (is_list(rt) or is_set(rt)):
        return None
    targs = getattr(rt, "type_args", None)
    if not targs:
        return None
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(targs[0])))
    if not (_eligible_scalar(slot) or _f1_record(slot, analyzer)):
        return None
    return slot


def _viewfam_ctor_call_fi(e: TpyCall, rtype: 'TpyType | None',
                          analyzer) -> 'FunctionInfo | None':
    """The resolved `__init__` fi of a str-family VALUE type-constructor call
    that carries `call_type` (`StrView("x")` / `String("x")`) -- the AST's
    `_gen_call` call_type-branch resolved-template arm (`gen_call_from_fi(ctor,
    None, gen_args)`), which for a viewfam/owned-str result renders the ctor's
    positional `@cpp_template` over inline args (`StrView("x")` -> bare `"x"`,
    `String("x")` -> `std::string("x")`), or None. The `call_type`-blind twin
    of `_template_init_call_fi`, gated to viewfam/owned-str VALUE results so it
    never overlaps the storage-container instantiation arm."""
    if (_resolved_viewfam_value(rtype, analyzer) is None
            and not _is_string_owned(rtype)):
        return None
    return _template_ctor_call_fi(e)


def _span_ctor_call_fi(e: TpyCall, rtype: 'TpyType | None') -> 'FunctionInfo | None':
    """The resolved `__init__` fi of a `Span(ptr, n)` construction -- the same
    template-expansion arm as the str family, over a SPAN result
    (`std::span<int32_t>({0}, static_cast<size_t>({1}))`). A span is a VALUE
    view, so no storage-container machinery is involved; the args land inline
    in the ctor's own template. Placed ahead of the bare-source view arm
    because the AST reaches the resolved-template arm FIRST -- a span
    construction that resolves a template ctor never gets the spelled
    direct-init render."""
    if not is_span(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))):
        return None
    return _template_ctor_call_fi(e)

def _tparam_value(t: 'TpyType | None') -> bool:
    """A bare type-param value (`T` after the ro/ref/send unwraps): renders
    by name on both paths (the `_f1_record_type_arg_ok` rule), so T-typed
    results/operands are spelling-safe; POSITIONS still gate their own
    family checks (a T decl/sink rejects on its declared-type family)."""
    if t is None:
        return False
    return _is_type_param_slot(unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(t))))

def _generic_root_subst(e: TpyCall, analyzer) -> 'tuple[FunctionInfo, dict[str, TpyType]]':
    """The stub the args resolve against + the inferred substitution for a
    plain generic free call -- ONE derivation shared by the gate
    (`_generic_plain_arg_ok`) and lowering (`_lower_generic_plain_call`), so
    the two cannot drift. An OVERLOAD GROUP resolves to the sema-selected
    stub (the AST's `func_info` pick), a single-stub group to its only entry;
    the kind classifier already pinned the targs/type-params arity."""
    fis = analyzer.registry.get_function(e.func_name)
    root = (e.resolved_function_info
            if len(fis) > 1 and e.resolved_function_info is not None
            else fis[0])
    return root, dict(zip(root.type_params, e.inferred_type_args))

def _is_range_call(it: TpyExpr) -> bool:
    """The `range(...)` iterable form -- the range-vs-container discriminator
    shared by the for-loop cell, the comprehension routes, and the
    instantiation-arg face, so eligibility and lowering can't drift on which
    shape a range takes."""
    return isinstance(it, TpyCall) and it.func_name == "range"

def _range_counter_type(call, analyzer) -> 'TpyType | None':
    """The range call's counter type -- get_iterable_element_type over the
    Range object type, IntLiteral resolved to the module default."""
    counter = get_iterable_element_type(analyzer.get_expr_type(call),
                                        registry=analyzer.registry)
    if counter is None or isinstance(counter, IntLiteralType):
        counter = analyzer.ctx.default_int_type
    return counter

def _instantiation_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` FunctionInfo of a generic-type INSTANTIATION
    call (`list(it)` / `set(xs)` -- `call_type` set, bare-name form) whose
    emit is the resolved `@cpp_template` ctor expanded over per-slot args:
    `_gen_call`'s `call_type` branch's resolved-template arm (sema already
    substituted the class type params, e.g. `::tpy::construct<std::vector<
    int32_t>>({0})`), or None. The subscript-spelled form (`Stack[Int32]()`,
    `subscript_callee` set), the Ptr null ctor, the list-repeat and
    array-literal arms, native ctors, and the float-str constant fold all
    take different renders -> AST path."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    if e.call_type is None or isinstance(e.call_type, PtrType):
        return None
    # `e.type_args` is NOT excluded: an explicitly spelled `list[Int32](it)`
    # renders exactly like the inferred `list(it)` -- sema folds the spelling
    # into `call_type` and into the ctor template, and no AST call-gen branch
    # reads the node's own type args.
    if (e.subscript_callee is not None
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert):
        return None
    if not e.args or isinstance(e.args[0], (TpyListRepeat, TpyArrayLiteral)):
        return None
    fi = e.resolved_function_info
    if fi is None or not (fi.is_method and fi.name == "__init__"):
        return None
    if fi.owning_type_qname == "builtins.float":
        return None  # the float("nan") constexpr fold takes its own render
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if not fi.cpp_template or not _positional_only_template(fi.cpp_template,
                                                            len(e.args)):
        return None
    if len(e.args) != len(fi.params):
        return None
    return fi

def _plain_member_call_markers_ok(e: TpyMethodCall, *,
                                  targs_ok: bool = False) -> bool:
    """No special-emit marker: every marker takes a different _gen_method_call
    path (static / super / module-qualified / typed-dict / nested-ctor /
    callable-field / macro / fstr / deref chain). The Optional runtime-check
    marker is NOT in this set -- callers dispose of it themselves (the
    optional-ptr borrow receiver mirrors it as the deref_check face; every
    other caller must reject it explicitly). `targs_ok` admits a plain
    generic METHOD call's explicit/inferred type args -- the AST spells them
    as `recv.method<targs>(args)` on the same plain-member path (the
    method_targs suffix), so only callers that thread `method_targs_cpp`
    set it."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    return not (e.is_static_call or e.super_parent_type is not None
                or e.unbound_self_parent_type is not None
                or e.user_module_call is not None
                or e.builtin_module_call is not None
                or e.typed_dict_get_field is not None
                or e.is_nested_constructor or e.is_nested_enum_constructor
                or e.is_callable_field or e.macro_expansion is not None
                or e.fstr_expansion is not None
                or ((e.type_args or e.inferred_type_args) and not targs_ok)
                or e.deref_depth
                or e.deref_narrowed_to is not None)

def _plain_method_fi_ok(fi, *, generator_ok: bool = False,
                        property_getter_ok: bool = False,
                        property_setter_ok: bool = False,
                        coro_factory_ok: bool = False,
                        consuming_ok: bool = False,
                        error_return_ok: bool = False,
                        ret_cast_ok: bool = False) -> bool:
    """Shared fi rejects. A consuming method moves the receiver
    (`std::move(xs)`) -- `consuming_ok` admits it (set only by the record
    method arm for a bare non-pointer, non-narrowed name receiver, whose
    move_receiver render mirrors the wrap); `cpp_return_type` wraps the call in a static_cast;
    @error_return unwraps via a statement expression; a LiteralType param
    mangles the member name. None are reproduced. `generator_ok` admits a
    generator fi (set only by the iterable-position member-gen-call
    classifier -- the factory call spells like any plain member call).
    `coro_factory_ok` is the async sibling (set only by the concrete-coro
    handle-binding position): an async METHOD call is a coroutine-factory
    call spelling like any plain member call; generic factories stay out
    with the free-call arm's reasoning.
    `error_return_ok` admits a fallible fi -- set only under
    `error_return_raw`, where a statement-level handler owns the unwrap and
    the call renders as the bare `std::expected` member call. Expression
    position keeps rejecting: that render is the statement-expression
    unwrap, which this arm does not carry.
    `property_getter_ok`/`property_setter_ok` admit the accessor fis -- set
    only by the property read/write delegation, whose `c.prop` -> `c.prop()`
    and `c.prop = v` -> `c.set_prop(v)` render like any plain method."""
    return not ((fi.is_consuming and not consuming_ok)
                or (fi.error_return_type is not None and not error_return_ok)
                # `ret_cast_ok` admits a declared cpp_return_type -- set only
                # by the record method arm, whose tail composes the
                # `static_cast<declared>(...)` wrap (the AST post-process).
                or (fi.native_cpp_return_type is not None
                    and not ret_cast_ok)
                or any(isinstance(p.type, LiteralType) for p in fi.params)
                or (fi.is_async and not coro_factory_ok)
                or (fi.is_async and fi.type_params)
                or (fi.is_generator and not generator_ok)
                or (fi.is_property_getter and not property_getter_ok)
                or (fi.is_property_setter and not property_setter_ok))

def _dict_view_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                           analyzer,
                           methods: tuple[str, ...] = ("values", "keys")) -> bool:
    """`d.values()` / `d.keys()` as a for-loop iterable: a zero-arg dict-view
    method on a bare-name eligible dict binding. The view result is an rvalue
    (the owning `auto __obj_N = ::tpy::dict_values(d);` capture); the loop var
    is the dict's value/key, checked by the caller's shared elem gate.
    `.items()` yields tuples -- the tuple-unpack gate passes it explicitly."""
    if e.method not in methods or e.args:
        return False
    if not isinstance(e.obj, TpyName) or e.obj.name not in locals_:
        return False
    # An Optional-checked receiver (`d.values()` on a narrowable Optional
    # dict) takes the deref_check method face, not the bare view call.
    if not _plain_member_call_markers_ok(e) or e.needs_optional_runtime_check:
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi) or fi.params:
        return False
    return _container_scalar_read(locals_[e.obj.name], analyzer)

def _plain_scalar_slot(ptype: TpyType | None, analyzer) -> bool:
    """A NON-Own value-scalar param slot. The user-record sibling of
    `_scalar_pass_through_slot`: a plain user method is not an inline
    template, so gen_call_arg's Own arm copies an `Own[scalar]` arg into a
    temp and moves it (`auto __tmp_N = n; ...(std::move(__tmp_N))`) -- Own
    slots stay on the AST path here."""
    if ptype is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if isinstance(t, OwnType):
        return False
    return _resolved_scalar(t, analyzer)

def _is_bytes_family(t: TpyType | None) -> bool:
    """A bytes/BytesView value or a pending bytes binding -- an analyzer-free
    family check for positions where the view/owned resolution is irrelevant
    (the print wrapper)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return t.family is BYTES_FAMILY
    return is_bytes_type(t) or is_bytes_view_type(t)

def _var_decl_type(stmt: TpyVarDecl, analyzer) -> TpyType | None:
    # Mirror codegen's _resolve_target_type (value-scalar subset): the binding
    # type captures sema's local deduction -- e.g. a literal-seeded local that
    # retro-widens to UInt64 from later usage -- which the init's type alone
    # (IntLiteralType) does not. Fall back to the init type, then resolve any
    # remaining int literal to the module default int.
    target = resolve_stmt_binding_type(stmt, analyzer, include_global_binding=False)
    if target is None and stmt.init is not None:
        target = analyzer.get_expr_type(stmt.init)
    if target is None:
        return None
    target = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target)))
    if isinstance(target, OwnType):
        target = target.wrapped
    return resolve_int_literals(target, analyzer.ctx.default_int_for_literal)
