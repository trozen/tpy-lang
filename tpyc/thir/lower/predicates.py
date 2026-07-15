"""Shared type/shape facts for THIR lowering.

Leaf predicates over resolved types and small expression shapes: the
eligible-scalar/str/bytes/enum/union/tuple families, F1/F2 record facts,
field/receiver/write facts, narrowing condition info, and the coercion
dispositions. These leaf classifiers do not recurse through an expression or
body and do not construct THIR; lowering arms consume their results locally.
"""

from __future__ import annotations
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFieldAccess,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyListRepeat,
    TpyMethodCall,
    TpyGeneratorExpression,
    TpyName,
    TpyNoneLiteral,
    TpyReturn,
    TpyStrLiteral,
    TpySubscript,
    TpyUnaryOp,
    TpyVarDecl,
)
from ...modules.type_resolution import get_iterable_element_type
from ...typesys import (
    AnyType,
    BYTES_FAMILY,
    CallableType,
    FLOAT,
    FloatLiteralType,
    INT32,
    IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType,
    OwnType,
    PendingDictType,
    PendingListType,
    PendingSetType,
    PendingViewType,
    PtrType,
    ReadonlyType,
    STR_FAMILY,
    TpyType,
    TupleType,
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
    is_bytes_type,
    is_bytes_view_type,
    is_char_type,
    is_dict,
    is_enum_type,
    is_fixed_int_type,
    is_int_enum_type,
    is_list,
    is_set,
    is_slice_type,
    is_span,
    is_str_type,
    is_str_view_type,
    is_string_type,
    type_def_of,
)
from ...coercions import CoercionContext
from ...value_category import is_rvalue_source
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    reads_storage_form_optional,
)
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.context import (
    enum_cpp_name,
    escape_cpp_name,
    qualified_cpp_name,
)
from ...namespace import BindingKind
from ...codegen_cpp.protocols import (
    dynamic_adapter_type,
    dynamic_ref_adapter_type,
    record_inherits_dynamic,
)
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
# NB the parser emits true-division as op `div`, not `/`, so the `/` token here
# is inert -- truediv stays on the AST path (see TODO: decide enable-or-drop).
# `in`/`is` take other emit paths, out of the slice.
_ARITH_OPS = frozenset({"+", "-", "*", "/", "//", "%"})

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

# Str-family coercions that are identity in EVERY position (no codegen lambda):
# both sides of string_to_str spell std::string; the two *_to_strview arms feed
# a std::string_view slot every source converts into implicitly.
_IDENTITY_STR_COERCIONS = frozenset(
    {_STRING_TO_STR_COERCION, "str_to_strview", "string_to_strview"})

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
# position. The AST's indirect-global pre-deref (`&(*g)`) never arises
# through THIR -- a record/container GLOBAL read rejects during inner
# lowering, so any inner that lowers here is a local/param/field lvalue.
_ADDR_PTR_COERCIONS = frozenset({
    "record_to_ptr", "record_to_const_ptr", "value_to_ptr",
    "upcast_to_ptr", "upcast_to_const_ptr"})

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
    if name in _ADDR_PTR_COERCIONS:
        return "&{0}"
    if name in _SPANLIKE_COERCIONS and not is_span(e.actual_type):
        # A span-typed actual is identity (handled in _coerce_disposition,
        # never a wrap). An array-literal inner takes the same helper wrap;
        # the coerce arm threads its make_array target into the inner render.
        helper = ("::tpy::as_span" if is_readonly_span(e.expected_type)
                  else "::tpy::as_mut_span")
        return helper + "({0})"
    if name in _SPAN_METHOD_COERCIONS:
        if is_protocol_type(e.actual_type) or is_span(e.actual_type):
            return None
        return "{0}.__span__()"
    return None

def _coerce_disposition(e: TpyCoerce) -> 'str | None':
    """'identity' (emit passthrough), 'materialize' (`std::string(x)`, lowered
    to the S1 view->owned THIRFormConvert), 'template' (a scalar cast rendered
    through `_coerce_wrap`'s `{0}` template), or None (outside the slice).

    Mirrors the tpyc/coercions.py codegen lambdas exactly, reading the same
    facts off the node: `strview_to_str` is identity at a plain ARG slot (a
    `str` param spells std::string_view) and materializes at INIT/ASSIGN/
    RETURN; an `Own[...]` ARG slot is rejected -- the surrounding gen_call_arg
    auto-move cascade is its own deferred frontier. `str_to_string`
    materializes only at ARG for a non-literal source; a NUL-free literal is
    const char[N], binding const std::string& directly (the lambda's
    startswith('"') token check made structural: cpp_string_literal_expr emits
    the bare-quote form exactly when the value is NUL-free).
    `strview_to_string` (const std::string& slot / owned String target)
    materializes in every position. The Optional and Char arms have their own
    renders -> AST path."""
    name = e.coercion.name
    if name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION,
                _FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION):
        return "identity"
    if name in _IDENTITY_STR_COERCIONS:
        return "identity"
    if isinstance(e.expected_type, OwnType):
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

def _eligible_value_union(t: TpyType | None) -> 'UnionType | None':
    """The F4 U1 slice: a value-form union of scalar / Char / str / StrView
    members (`Int32 | Float64 [| None]`, `Int32 | str`, `Int32 | Char`) --
    `std::variant<...>` where every member is stored by value (a str member is
    owned `std::string`, a StrView member a `std::string_view`). At the
    WHOLE-variant positions the slice routes -- reads/writes/returns/same-type
    args, and isinstance extraction (`std::get<std::string>`, spelled through
    the shared `render_type`) -- the member form is fixed by the variant, so
    both paths render bare (the converting ctor does the work) and a `None`
    source renders `std::monostate{}`. The form-relevant boundary is member
    INSERT: a str-VIEW value into a `... | str` slot is a view->owned
    conversion (`std::variant<...> __tmp = view;`), which `_value_union_temp_
    slot`'s scalar-only member check rejects (the body then stays AST). Record
    members (pointer-variant, U2) and recursive-alias wrappers ride later F4
    cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return None
    if not all(_eligible_scalar(m) or _eligible_char(m)
               or is_str_type(m) or is_str_view_type(m)
               or is_void_like_type(m) for m in t.members):
        return None
    return t

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
    Recursive-alias wrappers and protocol unions ride later cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_f1_record(m, analyzer) or is_void_like_type(m)
               for m in t.members):
        return None
    return t

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
    frames route)."""
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
    if dt is None or unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))) is not dt:
        return None
    u = _eligible_value_union(dt) or _eligible_ptr_union(dt, analyzer)
    if u is None:
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    # Each check member must be a member of the subject union (sema enforces;
    # kept as a slice guard so a fact mismatch gate-rejects, never mis-lowers).
    if not all(any(m == cm for m in u.members) for cm in members):
        return None
    return var, u, members, folded

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
    info = _isinstance_narrow_info(last.condition, declared, analyzer)
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
    `std::string_view` param) or `StrView` (`std::string_view`) -- or None
    outside the slice. A str local's binding type stays `PendingStrType` on the
    AST/sema side; resolve it like `_resolve_pending_view` does. `String`,
    `Char`, `Literal[str]`-annotated bindings, and the bytes family stay on the
    AST path (later cells)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return _resolve_pending_view(t, analyzer) if t.family is STR_FAMILY else None
    if isinstance(t, NominalType) and (is_str_type(t) or is_str_view_type(t)):
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
    produces (and the resulting type of a local bound to one). Kept separate
    from `_resolved_str_value` deliberately: String PARAMS spell
    `const std::string&` in the signature, a shape the S1 param emit does not
    reproduce, so the param/return/call gates must keep rejecting String while
    the concat slice admits it for operands, locals, len and print args."""
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
    if st is not None:
        return is_str_type(st)
    return _is_string_owned(t)

def _str_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The C++ shape of a str-slice NAME read -- mirrors the AST's
    `_is_str_view_source`: a `StrView`-resolved binding and a `str`-typed param
    (the signature spells `std::string_view`) are view/BORROW; an owned local is
    `std::string` (STORAGE). The owned-sink copy (`std::string(x)` at a decl
    init / return) fires only on a BORROW source; a str literal is const
    char[N] (implicitly convertible both ways) and stays VALUE, never wrapped."""
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
    hit the AST's broken narrowed-global read (no `.value()` extraction --
    and seeding one would let THIR's local-style narrowing arms admit what
    the AST renders bare), and unions/enums are unprobed. Returns None when
    out of the family."""
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

_BIGINT_INDEX_NARROW_WRAP = "{0}.to_fixed_check<int32_t>()"

def _unwrap_lit_coerce(e: TpyExpr) -> TpyExpr:
    """Strip sema's int-literal slot coercions (fixed-int / BigInt targets) so
    literal-shape checks see the digit token the AST renders."""
    while (isinstance(e, TpyCoerce)
           and e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)):
        e = e.expr
    return e

def _bigint_index_disposition(index: TpyExpr, analyzer) -> str:
    """How a subscript index / str-slice-adjacent int position renders when its
    type half is a runtime BigInt -- gen_index_expr's decision, written once so
    the gates and the wrap sites cannot drift:

      * 'bare' -- not runtime-BigInt, or an int32-range (possibly negated) int
        literal: `_is_int_constant` exempts those from the narrow, and the
        emitter renders an unresolved IntLiteralType literal as the bare token
        on both paths;
      * 'narrow' -- the `{0}.to_fixed_check<int32_t>()` wrap (no outer parens:
        any composite render already carries its own);
      * 'reject' -- an out-of-int32-range literal: the AST renders the BigInt
        ctor wrap inside the narrow, a shape the literal emit does not
        reproduce."""
    if not _runtime_bigint(analyzer.get_expr_type(index), analyzer):
        return "bare"
    c = _const_index(index)
    if c is not None:
        return "bare" if -(2**31) <= c <= 2**31 - 1 else "reject"
    if _const_index(_unwrap_lit_coerce(index)) is not None:
        # A coerce-wrapped literal fails `_is_int_constant` on the AST path, so
        # the narrow would wrap the literal's target-typed render -- a shape
        # not observed at index positions (sema leaves indices unwrapped);
        # defensive reject rather than a guessed mirror.
        return "reject"
    return "narrow"

def _narrow_bigint_index(idx: 'THIRExpr', e: TpyExpr, analyzer,
                         loc) -> 'THIRExpr':
    """Wrap a lowered runtime-BigInt index in the `.to_fixed_check<int32_t>()`
    narrow when its disposition says so (reads, del-item); 'reject' never
    reaches lowering (the gates exclude it)."""
    if _bigint_index_disposition(e, analyzer) != "narrow":
        return idx
    _witness("narrow.subscript_index")
    return THIRCoerce(result_type=INT32, expr=idx,
                      coercion_name=_BIGINT_NARROW,
                      wrap=_BIGINT_INDEX_NARROW_WRAP, loc=loc)

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
        lt, rt = operand_t(e.left), operand_t(e.right)
        if lt is not None and rt is not None:
            if is_big_int_type(lt) and is_float_type(rt):
                return (f"static_cast<{rt.to_cpp()}>({{0}})", None)
            if is_big_int_type(rt) and is_float_type(lt):
                return (None, f"static_cast<{lt.to_cpp()}>({{0}})")
    return (None, None)

def _enum_truthy_wrap(t: TpyType | None, analyzer) -> 'str | None':
    """The truthiness render for an enum-typed operand, as a `{0}` wrap --
    gen_truthy_expr's enum arms: an IntEnum tests its underlying value
    (`(static_cast<U>({0}) != 0)`); a plain enum is ALWAYS truthy and renders
    the literal `true` with the operand dropped. None for non-enum types."""
    et = _eligible_enum(t, analyzer)
    if et is None:
        return None
    if is_int_enum_type(et):
        u_cpp = enum_info_of(et).underlying_type.to_cpp()
        return f"(static_cast<{u_cpp}>({{0}}) != 0)"
    return "true"

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
    spelling already matches. A `Spannable`->span conversion source (an Array
    field -> `::tpy::as_mut_span`, a list -> `::tpy::as_span`) carries the
    `spanlike_to_span` coerce, and the `Span[T]`->`Span[readonly[T]]`
    widen carries `span_to_readonly_span`; neither is in `_coerce_disposition`,
    so those bodies reject at the coerce gate and stay on the AST path. Element
    restricted to the eligible scalars -- matching the span param / read
    slice's `_container_elem_family` span arm."""
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
    return (_eligible_scalar(a) or _eligible_char(a)
            or _f1_record(a, analyzer))

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
    if record_inherits_dynamic(arg_type, proto, analyzer):
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
    if isinstance(a, (TpyNoneLiteral, TpyStrLiteral)):
        return False
    at = analyzer.get_expr_type(a)
    if at is None or isinstance(unwrap_readonly(unwrap_send_sync(at)),
                                OptionalType):
        return False
    return bool(_resolved_scalar(at, analyzer) or _eligible_char(at)
                or _eligible_enum(at, analyzer) is not None)

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
                or _value_opt_view(u, analyzer) is not None):
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

    Precondition: callers must first exclude a STORAGE-form pointer-repr tuple (an F3
    `auto&&` alias local), which has the same type but reads as STORAGE -- this query
    keys on the type alone and would mistag it BORROW. The name-read call site checks
    `storage_tuple_locals` before falling through here. The other call site -- the
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
    """The narrow value-tuple element: an eligible value scalar or an owned-str
    slot. Both read bare in every sink (a str element is an owned `std::string`
    lvalue), so a subscript read of such an element needs no lift."""
    return _eligible_scalar(e) or _owned_str_slot(e, analyzer)

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
    return t if _resolved_str_value(t.inner, analyzer) is not None else None

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
    `_f1_tuple`); else None. Shared by the value-element and record-element read gates
    -- non-name receivers, ineligible tuples, and non-const indices stay on the AST
    path."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, _idx = res
    if not (_value_tuple(recv_t, analyzer) is not None
            or _f1_tuple(recv_t, analyzer) is not None):
        return None
    return res

def _tuple_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> 'int | None':
    """A value-result tuple subscript read `t[N]` -> `std::get<N>(t)` (value form, no
    lift): element N is a value scalar. Returns the normalized index, or None -- record
    / `Optional` (borrow) elements ride the field-receiver path (`t[N].field`)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    el = recv_t.element_types[idx]
    # An owned-str element reads as an owned lvalue (`std::get<N>(t)` yields
    # `const std::string&`) -- bare in every sink on both paths, so it rides
    # the same value-read arm as a scalar element.
    return (idx if (_eligible_scalar(el) or _owned_str_slot(el, analyzer))
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
    `_narrow_bigint_index` on the INDEX type alone (gen_index_expr narrows a
    runtime-BigInt key even into a BigInt-keyed map -- the int32 round-trip is
    mirrored, not endorsed). An `Own[container]` (move-in
    `T&&` param) is excluded explicitly -- its ABI differs from the borrow shape
    this slice's emit assumes, and it rides a later cell (mirrors the Own unwrap
    in `_f1_record`, which admits Own where this deliberately does not)."""
    return _container_elem_family(
        t, analyzer,
        lambda a: _eligible_scalar(a) or _owned_str_slot(a, analyzer),
        span_ok=True)

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
        return ((is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
                 or _owned_str_slot(key, analyzer))
                and elem_ok(val))
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
                           or _owned_str_slot(args[0], analyzer))

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
    """Mirror of the AST's `_is_nocopy_container_element` over the
    gate-admitted element slots: the union / recursive-alias arms are
    unreachable (those slots are not admitted into container literals), so
    the direct record check decides the make_vector/make_ordered_* switch."""
    return _cpp_noncopyable_type(t, analyzer)

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

def _str_field_value_read(e: TpyExpr, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A value-position read of a str-family field off an admitted receiver
    (`recv.field`, `_field_receiver_ok`): an owned `std::string` member reads
    bare as STORAGE (a view sink binds it implicitly, an owned sink copies by
    value); a `StrView` member reads bare as BORROW, so the owned-str sinks
    that admit it fire the explicit view->owned copy (`std::string(...)` at
    the return convert) -- both byte-identical at the positions that admit
    the read (the str-family return slot, a print / f-string arg). A `String`
    field resolves outside the str slice -- excluded."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_receiver_ok(e, declared, analyzer)):
        return False
    st = _resolved_str_value(analyzer.get_expr_type(e), analyzer)
    return st is not None and (is_str_type(st) or is_str_view_type(st))

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
    rejects at the caller's family check."""
    base = declared.get(e.obj.name)
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

def _f2_reseat_ok(init: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """A pointer-local reseat value: an lvalue field read off an F1-record receiver
    whose field is itself an F1-record (the new pointee), so it reseats as
    `p = &(recv.field);`. rvalue / `None` / name-alias reseats need the rebind-slot
    (`__slot_N`) machinery and stay on the AST path."""
    return (_field_receiver_ok(init, declared, analyzer)
            and _f1_record(analyzer.get_expr_type(init), analyzer))

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

def _f2b_optional_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """An optional-field write `recv.field = <value>`: the target is a pointer-repr
    `Optional[record]` field off an F1-record receiver and the value is either a
    bare borrow `T*` local (`recv.field = ::tpy::ptr_to_optional[_move](p)`, copy
    or move per last-use) or a `None` literal (`recv.field = std::nullopt`). The
    `copy()`-acknowledged and call/rvalue value sources stay on the AST path."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    if not _f1_record(ftype.inner, analyzer):
        return False
    return (isinstance(stmt.value, TpyNoneLiteral)
            or _is_borrow_ptr_local(stmt.value, declared, pointers))

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
    if _f1_tuple(analyzer.get_expr_type(target), analyzer) is None:
        return False
    return _is_borrow_tuple_source(stmt.value, declared, storage_tuple_locals, analyzer)

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
    divergence."""
    return _param_const_verdict(name, func, analyzer, record_name,
                                "const_borrow_params")

def _f1_is_const(binding: 'LocalBinding', target_type: TpyType | None,
                 stmt: TpyVarDecl, func: TpyFunction, analyzer,
                 const_locals: set[str], record_name: str | None = None) -> bool:
    """The const-ness of an F1 borrow local's decl (`const T&` / `const T*`).

    Mirrors `_is_const_indirect` for the field source (ReadonlyType reads on the
    optional inner / the init's raw sema type / the var_types entry) plus, for
    OPTIONAL_TO_PTR, the storage-optional const bump (`_is_const_union_source`:
    the receiver in const_ref_params (param) or const_indirect_locals (a const F1
    local, tracked in `const_locals`)). The name/method-call const branches of
    `_is_const_indirect` do not apply to a field source."""
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
    # Borrow-alias of an lvalue rooted in a const source (`p = ps[i]`,
    # `r = obj.field`, `c = self.store[k]`): mirror of the AST REF_ALIAS
    # const propagation via `_is_const_union_source` -- recurse through
    # chained field/subscript access to the base name.
    if binding is LocalBinding.REF_ALIAS and _f1_const_rooted_source(
            stmt.init, func, analyzer, const_locals, record_name):
        return True
    return False

def _f1_const_rooted_source(expr: TpyExpr, func: TpyFunction, analyzer,
                            const_locals: set[str],
                            record_name: str | None) -> bool:
    """Mirror of codegen's `_is_const_union_source`: True when `expr` is an
    lvalue rooted in a const source (param in `const_borrow_params` / const F1
    local), recursing through chained field/subscript access to the base name."""
    if isinstance(expr, TpyCoerce):
        return _f1_const_rooted_source(expr.expr, func, analyzer, const_locals, record_name)
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

def _is_none_compare_operand(e: TpyBinOp, locals_: dict[str, TpyType],
                             analyzer) -> 'TpyExpr | None':
    """The Optional operand of an admitted `is [not] None` test, or None. The
    shape is exactly one `None` literal against a pointer-repr
    `Optional[F1-record]` borrow NAME (the declared type, matching the AST's
    `get_resolved_type` -- a flow-narrowed `p` still renders the pointer
    compare), a value-repr Optional name, or an Optional FIELD subject
    (`self.f is None` -- storage is std::optional<T> whatever the repr, so
    the AST compares `.has_value()`, _gen_binop's storage-form arm). Shared
    by `_lower_binop` and the lowering (`_lower_expr`'s is-arm) so both key
    one verdict."""
    left_none = isinstance(e.left, TpyNoneLiteral)
    right_none = isinstance(e.right, TpyNoneLiteral)
    if left_none == right_none:  # both or neither
        return None
    operand = e.right if left_none else e.left
    if (_optional_ptr_borrow_name(operand, locals_, analyzer) is None
            and _value_opt_scalar_name(operand, locals_, analyzer) is None
            and _value_opt_view_name(operand, locals_, analyzer) is None
            and not _optional_field_none_subject(operand, locals_, analyzer)):
        return None
    return operand

def _optional_field_none_subject(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A one-level Optional FIELD subject of an `is [not] None` test
    (`self.f is None` / `cfg.port is not None`): declared field storage is
    `std::optional<T>` whatever the repr, so the AST renders the storage-form
    `.has_value()` compare over the bare member read. Requires the
    UN-narrowed Optional read (a sema-narrowed subject folds elsewhere) and
    the same receiver/marker shape the plain field arm admits."""
    if not (isinstance(e, TpyFieldAccess) and isinstance(e.obj, TpyName)
            and _field_markers_clean(e)
            and _field_receiver_ok(e, locals_, analyzer)):
        return False
    at = analyzer.get_expr_type(e)
    if at is None or not isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))),
            OptionalType):
        return False
    fdt = _field_decl_type(e, locals_, analyzer)
    return fdt is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt))),
        OptionalType)

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
    if is_list(t) or is_dict(t):
        return t if _container_scalar_read(t, analyzer) else None
    if is_set(t):
        args = getattr(t, "type_args", None)
        if bool(args) and (_eligible_scalar(args[0])
                           or _owned_str_slot(args[0], analyzer)):
            return t
        return None
    if _value_tuple(t, analyzer) is not None:
        return t
    if _eligible_value_union(t) is not None:
        return t
    return None

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

def _value_union_temp_slot(a: TpyExpr, ptype: TpyType | None,
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
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    # A bare float literal types as FloatLiteralType and lands in the union's
    # double member, rendering repr(v) -- resolve it like the f-string row
    # does. (An int literal never arrives bare: sema coerces it to the union,
    # the temp-free `_union_coerced_literal_arg` row.)
    if isinstance(at, FloatLiteralType):
        at = FLOAT
    if not _eligible_scalar(at):
        return None
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return None
    return ut

def _record_rvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                             analyzer) -> 'NominalType | None':
    """The record-rvalue arg-temp row (the free-call `is_ref_param() +
    is_temporary_expr` cascade arm): a record RVALUE -- a ctor
    `A(7)` or a by-value record-returning call `make(7)` -- into a
    SAME-nominal plain record slot hoists `A __tmp_N = A(7);` and passes the
    temp name -- mutated (`A&`) and const (`const A&`) slots alike (the AST
    arm is mutation-blind). `TempState.create` renders the slot type's bare
    `to_cpp()`, which the F1 restriction keeps equal to the ctor's own
    spelling (raw name same-module, `native_cpp_names` qualification
    cross-module).
    A readonly slot (`const A` decl spelling) survives `unwrap_ref_type` as a
    ReadonlyType and rejects; a SUBCLASS-typed rvalue (the upcast temp declares
    the CHILD's type) rejects on the same-nominal check. A borrow-returning
    call is not an rvalue source (the AST binds/copies without this temp) and
    rejects. Shared by the local slot classifier and `_lower_call_arg`;
    recursive lowering validates the source call's arguments."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_ref_type(pt)
    if not (isinstance(pt, NominalType) and pt.is_user_record
            and pt.is_ref_param()):
        return None
    if not _f1_record(pt, analyzer):
        return None
    if not isinstance(a, TpyCall):
        return None
    if not is_rvalue_source(analyzer, a):
        return None
    if analyzer.get_expr_type(a) != pt:
        return None
    return pt

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
    """Slot/shape verdict for the Own-slot copy+move row -- a bare (un-coerced)
    NAME / eligible field read into a plain `Own[T]` slot of eligible-scalar
    or same-nominal F1-record payload. Renders `auto __tmp_N = <arg>;` +
    `f(std::move(__tmp_N))` -- or the temp-free `f(std::move(name))` when the
    name is movable at its last use (gen_call_arg's `_maybe_move` arm, decided
    at lowering from the same `movable_locals` + last-use facts; a scalar is
    never movable, a pointer-local is a non-owning borrow -- both always
    copy). Shared by the gate (`_own_lvalue_arg`, which adds the locals_/
    narrowing rejects) and `_lower_call_arg` (which adds the lc-side
    narrowing reject and picks `THIRMove` vs `THIRArgTemp`)."""
    w = _plain_own_slot(ptype)
    if w is None:
        return None
    if not isinstance(a, (TpyName, TpyFieldAccess)):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OwnType):
        # An Own[T]-bound param name reads bare, like any owned local.
        at = unwrap_readonly(at.wrapped)
    if _eligible_scalar(w):
        return w if _eligible_scalar(at) else None
    if _f1_record(w, analyzer):
        # Same-nominal is a slice guard: sema rejects an upcast into an Own
        # slot outright, so no other pairing reaches codegen.
        return w if at == w else None
    if _is_type_param_slot(w):
        # A TypeParamRef payload (`Own[T]` slot fed by an `Own[T]` param in
        # a generic body): the copy renders the same `auto __tmp_N = <arg>;`
        # and the movable last use the same temp-free `std::move(name)`.
        return w if at == w else None
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
                           analyzer) -> str | None:
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
    None rejects: subscript sources, coerced args, `self`, non-ctor
    rvalues (`Ptr[T]`-typed calls etc. stay AST). Shared by the eligibility
    gate (which adds receiver checks) and `_lower_call_arg` so both key one
    verdict; constructor args are validated during recursive lowering."""
    ot = _optional_ptr_arg_slot(ptype, analyzer)
    if ot is None:
        return None
    inner = unwrap_readonly(ot.inner)
    if isinstance(a, TpyNoneLiteral):
        return 'none'
    if isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is None or not fi.is_constructor:
            return None
        return 'ctor' if analyzer.get_expr_type(a) == inner else None
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
    if not (is_list(at) or is_dict(at) or is_set(at) or is_array(at)):
        return False
    if ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    # Explicit Own/Optional rejects (the move / address-of lift slots); the
    # concrete-container check below would also exclude them, but the invariant
    # should be self-evident, mirroring gen_call_arg's own Own detection.
    if isinstance(pt, (OwnType, OptionalType)):
        return False
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
    """The ROOT stub + inferred substitution for a plain generic free call
    -- ONE derivation shared by the gate (`_generic_plain_arg_ok`) and
    lowering (`_lower_generic_plain_call`), so the two cannot drift. The
    kind classifier already pinned the single-stub group and the
    targs/type-params arity."""
    root = analyzer.registry.get_function(e.func_name)[0]
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
    if (e.subscript_callee is not None or e.type_args
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

def _plain_member_call_markers_ok(e: TpyMethodCall) -> bool:
    """No special-emit marker: every marker takes a different _gen_method_call
    path (static / super / module-qualified / typed-dict / nested-ctor /
    callable-field / macro / fstr / deref chain). The Optional runtime-check
    marker is NOT in this set -- callers dispose of it themselves (the
    optional-ptr borrow receiver mirrors it as the deref_check face; every
    other caller must reject it explicitly)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    return not (e.is_static_call or e.super_parent_type is not None
                or e.unbound_self_parent_type is not None
                or e.user_module_call is not None
                or e.builtin_module_call is not None
                or e.typed_dict_get_field is not None
                or e.is_nested_constructor or e.is_nested_enum_constructor
                or e.is_callable_field or e.macro_expansion is not None
                or e.fstr_expansion is not None or e.type_args
                or e.inferred_type_args or e.deref_depth
                or e.deref_narrowed_to is not None)

def _plain_method_fi_ok(fi, *, generator_ok: bool = False) -> bool:
    """Shared fi rejects. A consuming method moves the receiver
    (`std::move(xs)`); `cpp_return_type` wraps the call in a static_cast;
    @error_return unwraps via a statement expression; a LiteralType param
    mangles the member name. None are reproduced. `generator_ok` admits a
    generator fi (set only by the iterable-position member-gen-call
    classifier -- the factory call spells like any plain member call)."""
    return not (fi.is_consuming or fi.error_return_type is not None
                or fi.native_cpp_return_type is not None
                or any(isinstance(p.type, LiteralType) for p in fi.params)
                or fi.is_async or (fi.is_generator and not generator_ok)
                or fi.is_property_getter or fi.is_property_setter)

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
