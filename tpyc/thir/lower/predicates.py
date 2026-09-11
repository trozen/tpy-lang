"""Shared type/shape facts for THIR lowering.

Leaf predicates over resolved types and small expression shapes: the
eligible-scalar/str/bytes/enum/union/tuple families, F1/F2 record facts,
field/receiver/write facts, narrowing condition info, and the coercion
dispositions. These leaf classifiers do not recurse through an expression or
body and do not construct THIR; lowering arms consume their results locally.
"""

from __future__ import annotations
import math
from dataclasses import replace
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
    TpyRaise,
    TpyReturn,
    TpySetLiteral,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
    TpyUnaryOp,
    TpyVarDecl,
    TupleElemCapture,
)
from ...modules.defs import BINOP_TO_METHOD, get_dunder_cpp_template
from ...modules.type_resolution import get_iterable_element_type
from ...sema.literal_utils import fixed_int_literal_value_from_expr
from ...typesys import (
    substitute_type_params_simple,
    collapse_tuple_own_elements,
    contains_type_param,
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
    ResolvedBinop,
    STR_FAMILY,
    SelfType,
    TpyType,
    TupleType,
    TypeParamKind,
    TypeParamRef,
    UnionType,
    ValueForm,
    is_any_bytes_type,
    is_any_str_type,
    is_dyn_protocol,
    is_float_type,
    is_protocol_type,
    is_own_pointer_repr_optional,
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
    TypeCategory,
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
    is_dict_view,
    is_enum_type,
    is_fixed_int_type,
    is_int_enum_type,
    is_list,
    is_range,
    is_set,
    is_slice_type,
    is_span,
    is_varargs,
    is_str_type,
    is_str_view_type,
    is_string_type,
    type_def_of,
)
from ...coercions import CoercionContext
from ...value_category import (
    _CONTAINER_LITERAL_NODES,
    call_returns_cpp_ref,
    is_rvalue_source,
)
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    reads_storage_form_optional,
)
from ...codegen_cpp import emit_prims
from ...codegen_cpp.param_const import decide_param_const
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.context import (
    bigint_index_narrow_type,
    enum_cpp_name,
    escape_cpp_name,
    is_constructor_call,
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
from ...prescan import parse_deref_view_key
from ..reject import ThirUnsupported, note_detail, stmt_reject_reason
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

# Logical and/or (the parser folds `a and b` to TpyBinOp("&&")). A bool result
# over bool operands renders as the bare C++ operator (`(l && r)`); the non-bool
# Python value semantics (`x or default`) need the once-evaluated-LHS temp +
# ternary instead, and take their own arm.
_LOGICAL_OPS = frozenset({"&&", "||"})

# Identity tests. Admitted only as the None test on a pointer-repr Optional
# borrow name (`p is None` / `p is not None` -> `(p ==|!= nullptr)`, the
# THIRIsNone render); every other identity shape (record-vs-record, storage /
# protocol / union operands) rejects.
_IS_OPS = frozenset({"is", "is not"})

# Membership. The container-member form is `needle in c` / `needle not in c`
# over a dict/set container NAME whose `__contains__` is a plain @native member
# (`c.contains(needle)`, the `resolved_contains` render) with scalar or string
# needles. list membership (`std::ranges::
# contains`, no `__contains__` member), bytes membership (a @native FREE
# function), str `.find()`, TypedDict/tuple-literal/global receivers, and the
# universal `__iter__`/`__next__` form each take their own arm.
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
# inner render, position-independent -- spelled here as `{0}` templates and
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
# render comes from the literal seeing the coerce TARGET, so lowering retypes
# the THIRLiteral to Float32.
_FLOAT32_LIT_COERCION = "float_literal_to_float32"

# Its BigInt twin: `int_literal_to_bigint` is an identity lambda whose
# `::tpy::BigInt(...)` wrap comes from the literal's BigInt resolution --
# lowering retypes the THIRLiteral so the emitter picks the ctor arms.
_BIGINT_LIT_COERCION = "int_literal_to_bigint"

# Address-taking Ptr coercions: the codegen lambda is `&{e}` in every
# position. An indirect-name inner is pre-dereferenced (`&(*g)` for a
# pointer-slot global / pointer-local source), so the coerce arm derefs
# names in lc.pointers.
_ADDR_PTR_COERCIONS = frozenset({
    "record_to_ptr", "record_to_const_ptr", "value_to_ptr",
    "upcast_to_ptr", "upcast_to_const_ptr"})

# Position-independent identity coercions on the ptr/span/slice axis: both
# sides are C++-implicitly convertible (`T*` -> `const T*`, `Slice`'s
# BasicSlice ctor, span -> const-span), so the inner render passes through
# bare.
_PTR_IDENTITY_COERCIONS = frozenset({
    "ptr_to_const_ptr", "basic_slice_to_slice", "span_to_readonly_span"})

# list/Array -> Span[T]: `::tpy::as_span` / `as_mut_span` wraps the inner
# render. An array-literal inner keeps
# the same helper wrap; its make_array-prefixed inner render is target-
# threaded at the coerce arm. A span-typed actual (identity) is split off
# in `_coerce_disposition`.
_SPANLIKE_COERCIONS = frozenset({"spanlike_to_span", "spanlike_to_span_arg"})

# `__span__()`-method / Spannable-protocol coercions (sema's pre-built pair,
# not in COERCIONS): a user-record actual renders `{0}.__span__()`. The
# protocol-typed actual (bare as_span
# render) stays out -- protocol params/locals reject upstream, so the row
# would be dead.
_SPAN_METHOD_COERCIONS = frozenset({"span_method_to_span",
                                    "span_method_to_span_arg"})

# The coercions that pre-deref an indirect-name inner (the "need
# dereferencing for globals" set): the addr family, the method-calling BigInt
# cast, and the `__span__()` call above -- the coerce arm derefs un-narrowed
# names in lc.pointers, so an indirect receiver reaches its member as
# `(*name).__span__()`.
_INDIRECT_DEREF_COERCIONS = (_ADDR_PTR_COERCIONS | {"bigint_to_fixed_int"}
                             | _SPAN_METHOD_COERCIONS)

def _coerce_wrap(e: TpyCoerce) -> 'str | None':
    """The `{0}` render template for the scalar-cast coercion family, else
    None. `fixed_int_widening`'s and
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
    # (arg/return/init all render `::tpy::deref_check(x)`); the
    # record-wrapper `.__deref__()` flavor keeps its dedicated arg row.
    if name == "deref_to_target" and isinstance(e.actual_type, PtrType):
        return "::tpy::deref_check({0})"
    if name in _SPANLIKE_COERCIONS and not is_span(e.actual_type):
        # A span-typed actual is identity (handled in _coerce_disposition,
        # never a wrap). An array-literal inner takes the same helper wrap;
        # the coerce arm threads its make_array target into the inner render.
        # An `Optional[Span[...]]` expected peels ONE level before the
        # helper choice: unpeeled, a readonly-span slot would wrongly
        # pick as_mut_span.
        expected = e.expected_type
        if isinstance(expected, OptionalType) and is_span(expected.inner):
            expected = expected.inner
        helper = ("::tpy::as_span" if is_readonly_span(expected)
                  else "::tpy::as_mut_span")
        return helper + "({0})"
    if name in _SPAN_METHOD_COERCIONS:
        if is_span(e.actual_type):
            return None
        if is_protocol_type(e.actual_type):
            # The Spannable arm, checked BEFORE the user-record
            # `.__span__()` one: a `Spannable[T]` actual always
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

    Follows the tpyc/coercions.py codegen lambdas, reading the same facts
    off the node: `strview_to_str` is identity at a plain ARG slot (a
    `str` param spells std::string_view) and materializes at INIT/ASSIGN/
    RETURN and at an `Own[str]` ARG slot (the lambda's `isinstance(b,
    OwnType)` branch) -- but the Own face is served only under
    `own_slot_arg`, the flag the Own-slot copy row threads; every other
    consumer keeps the blanket `Own[...]` reject, because there the
    surrounding call-argument auto-move cascade owns the render decision.
    `str_to_string` materializes only at ARG for a non-literal source; a
    NUL-free literal is const char[N], which the `String` slot converts from
    directly (the lambda's startswith('"') token check made structural:
    cpp_string_literal_expr emits the bare-quote form exactly when the value
    is NUL-free). `strview_to_string` (a `const ::tpy::String&` slot / an owned
    String target) materializes in every position. The Optional and Char
    arms have their own renders and reject here."""
    name = e.coercion.name
    if name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION,
                _FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION):
        return "identity"
    if name in _IDENTITY_STR_COERCIONS:
        return "identity"
    if name == "bytes_to_bytesview":
        # A bytes NAME/slice source is already the `std::span<const
        # uint8_t>` the view spells -- identity in every position
        # (`return b;`). A LITERAL source passes through too: the
        # view-typed sink's retag flips its form to the static
        # `bytes_literal` span (`bv: BytesView = b"hello"`).
        return "identity"
    if name == "bytearray_to_bytes":
        # Only the BORROWING `bytes` parameter reaches here: that slot is the
        # span view a ByteArray satisfies directly, so the coercion has no
        # codegen. An owning `bytes` sink never lowers -- sema refuses it and
        # asks for the explicit `bytes(...)`.
        return "identity"
    if isinstance(e.expected_type, OwnType):
        if own_slot_arg and name == "strview_to_str":
            return "materialize"
        if (name in _ADDR_PTR_COERCIONS
                and isinstance(unwrap_readonly(e.expected_type.wrapped),
                               PtrType)):
            # `Own[Ptr[T]]` is a no-op spelling -- Own over a VALUE type adds
            # nothing, so there is no auto-move cascade to own the decision
            # and the address-taking render stands (`ps.append(items[0])` ->
            # `push_back(&::tpy::__getitem__(items, 0))`).
            return "template"
        if (_eligible_scalar(unwrap_readonly(e.expected_type.wrapped))
                and _coerce_wrap(e) is not None):
            # A scalar cast into an `Own[value-scalar]` slot
            # (`log.append(v)` -> `push_back((v).to_fixed_check<..>())`):
            # Own over a value scalar adds nothing, and the cast rvalue
            # binds the slot natively -- the Own[Ptr] precedent.
            return "template"
        if (name == "strview_to_str" and not isinstance(e.expr, TpyName)):
            # A view->owned materialize of an RVALUE source at an
            # `Own[str]` slot (`items.append(subject[:5])` ->
            # `push_back(std::string(::tpy::str_slice(..)))`): the
            # conversion rvalue binds `T&&` directly, so a non-simple-lvalue
            # source renders bare, no temp.
            return "materialize"
        return None
    if name in _PTR_IDENTITY_COERCIONS:
        return "identity"
    if name in _SPANLIKE_COERCIONS and is_span(e.actual_type):
        # A span-typed actual: the inner render passes through bare
        # (span -> span widening is C++-implicit).
        return "identity"
    # Scalar casts stay below the Own reject: at an `Own[...]` slot the arg
    # cascade owns the decision.
    if _coerce_wrap(e) is not None:
        return "template"
    if name == "strview_to_str":
        return ("identity" if e.context_kind == CoercionContext.ARG
                else "materialize")
    if name in ("optional_strview_to_str", "optional_str_to_strview"):
        # Identity at a plain ARG slot -- both sides spell
        # `std::optional<std::string_view>`, and the coercion lambda passes
        # the expression through bare exactly there. Every other position
        # rebuilds via the `__ov` statement expression, which this row
        # does not cover; the Own-slot face returns None above, same as
        # the lambda's else.
        return ("identity" if e.context_kind == CoercionContext.ARG
                else None)
    if name == "str_to_string":
        if e.context_kind != CoercionContext.ARG:
            return "identity"
        if isinstance(e.expr, TpyStrLiteral) and "\x00" not in e.expr.value:
            return "identity"
        return "materialize"
    if name == "strview_to_string":
        return "materialize"
    if name == "bytesview_to_bytes":
        # `::tpy::Bytes({0})` with no position or source branch in the
        # lambda -- the `strview_to_string` shape, so it materializes
        # everywhere below the Own reject, which leaves the arg cascade
        # owning the render decision exactly as for the str family.
        return "materialize"
    if name == "bytesview_to_bytearray":
        # The bytearray twin of bytesview_to_bytes, spelling its own owned
        # buffer (`::tpy::ByteArray({0})`). Unlike bytes, a bytearray sink
        # from a view source ALWAYS carries this sema coercion (the
        # coercion-less same-type case cannot arise for a reference type),
        # so this row alone covers the family's view->owned direction.
        return "materialize"
    return None


def _wrap_view_owned_sink(value: 'THIRExpr | None',
                          slot_type: 'TpyType | None',
                          loc) -> 'THIRExpr | None':
    """An owned-str/bytes slot (std::string / std::vector<uint8_t> by value)
    fed a view-form source copies explicitly -- `std::string(a)` /
    `::tpy::Bytes(a)` -- the view->owned construction being explicit.
    The single chokepoint for that copy, keyed on the lowered value's own
    form fact; a literal (VALUE) or owned local / owned call result
    (STORAGE) lands bare. Shared by the return sinks and the generator's
    iterator slot.
    """
    if value is None or value.form is not Form.BORROW or slot_type is None:
        return value
    if is_str_type(slot_type) or is_bytes_type(slot_type):
        return THIRFormConvert(result_type=slot_type, value=value,
                               form=Form.STORAGE, loc=loc)
    return value


def _owned_copy_sink(value: 'THIRExpr | None', slot_type: 'TpyType | None',
                     analyzer) -> bool:
    """Whether the view->owned copy applies to `value` at an owned str/bytes
    slot.

    Resolves the slot through the analyzer before testing it, so it also
    covers a slot whose str/bytes-ness is not spelled directly -- wider reach
    than a bare type test, and the reason this cannot be folded into one.

    A sink that also decides a last-use move has to ask it first: the move
    is skipped exactly where this copy applies, because what the copy
    constructs from is a trivially-copyable VIEW, not the binding the source
    name reads. Asking in the other order moves the wrong object."""
    if value is None or value.form is not Form.BORROW or slot_type is None:
        return False
    st = _resolved_str_value(slot_type, analyzer)
    if st is not None and is_str_type(st):
        return True
    bt = _resolved_bytes_value(slot_type, analyzer)
    return bt is not None and is_bytes_type(bt)


def _peel_stale_view_owned_coerce(init: TpyExpr, binding_t: TpyType | None,
                                  analyzer) -> TpyExpr:
    """The stale-coerce identity arm at the decl/reassign
    sink: a str/bytes view->owned coerce is attached against the ANNOTATED
    owned type, but the binding's storage is decided later by the pending
    view resolution -- when the binding resolved VIEW, materializing would
    bind the view to an owned temporary dying at end of statement (dangling),
    so the source renders bare. Returns the peeled source (to lower
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
    """A for-each loop-var element type the shared `loop_var_binding` can
    bind. The binding FORM -- value copy (`T v = *b`), composite ref (`const
    auto&` / `auto&&` for a union/tuple), or borrow alias (`auto&&` for a
    record) -- is chosen INSIDE that helper off `is_value_type()` and
    Union/Tuple-ness, so the element gate need not enumerate nominal families,
    and sequential body lowering rejects any use of the loop var it cannot
    route.

    An UNRESOLVED pending element type would be spelled before anything
    concretizes it, so it rejects. `et` reaches here with int-literals and str
    already resolved by the caller, so a surviving `PendingViewType` is a bytes
    view (no bytes loop-var arm yet) and a pending container is a
    not-yet-concretized nested container.

    An `Optional` loop var is excluded EXCEPT the value-repr Optional[cheap
    scalar / Char] family (`std::optional<T> item = *__beg_N;`): a body that
    NARROWS one (`if x is None: continue`) then reads it needs the value-repr
    deref (`(*x)`) that the value-opt param arms render -- the for-each
    lowering registers the loop var's SCALAR value-opt binding so those arms
    fire for it. Other Optional inners keep the blanket reject; a narrowable
    UNION loop var is NOT excluded -- its isinstance extraction reads the
    shared `declared` map, which the loop var populates."""
    if et is None:
        return False
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if isinstance(bare, OptionalType):
        return _foreach_value_opt_elem(et) is not None
    return not isinstance(bare, (PendingViewType, PendingListType,
                                 PendingDictType, PendingSetType))


def _foreach_storage_opt_elem(et: TpyType | None, analyzer) -> bool:
    """A pointer-repr `Optional[F1-record]` CONTAINER element (`for v in
    d.values():` over `dict[str, P | None]`): the loop var binds the
    STORAGE-form `std::optional<P>` (`const auto&` composite ref) and
    registers in the storage-opt set -- the for-STATEMENT producer of
    codegen's `storage_form_optional_locals`. Container route only: a
    generator / iter-proto source yields the BORROW form (`T*`), which
    must not register storage."""
    if not isinstance(et, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if not (isinstance(t, OptionalType) and t.uses_pointer_repr()):
        return False
    return _f1_record(_unwrap_own(unwrap_readonly(t.inner)), analyzer)

def _foreach_value_opt_elem(et: TpyType | None) -> 'OptionalType | None':
    """The value-repr `Optional[cheap scalar / Char]` loop-var element family
    (the same inner slice `_value_opt_scalar` admits for params, spelled
    locally so this gate does not move if that helper's slice widens). The
    loop var binds as a typed `std::optional<T>` copy and its body reads ride
    the value-opt binding arms via `lc.value_opt_bindings`."""
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
    emitter picks the wrapped render. A `LiteralType` over a scalar base
    classifies as the base (it delegates every render to it; the
    dead-branch constant folds are decided separately at the compare
    lowering via `lc.literal_facts`).
    """
    if isinstance(t, LiteralType):
        t = t.base_type
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

def _type_param_value_slot(t: 'TpyType | None') -> bool:
    """A DECL slot spelled as a BARE TYPE-kind type-param (`T newitem = ...;`
    inside a `[T]` template). Borrow and storage form coincide here because
    neither exists yet: `T` is fixed only at instantiation, so the C++ template
    traits pick the shape and the decl is the plain spelled copy a scalar's is.
    Bare only -- an `Own[T]` slot is a move source and a `readonly[T]` /
    `Ref[T]` wrapper spells its own decoration, both of which reopen the
    question this row answers."""
    return isinstance(t, TypeParamRef) and t.kind is TypeParamKind.TYPE

def _int_type_param_value(t: 'TpyType | None') -> bool:
    """A value read of an INT-kind type param (`N` under `[N: int]`). Its C++
    spelling is the non-type template parameter -- a `std::size_t` constant
    that renders as the bare name in every value position -- so a slot whose
    render passes a scalar bare passes this bare too. Distinct from
    `_eligible_scalar`: `N` carries no TPy-side fixed width, so nothing may
    derive a cast or a range from it."""
    return isinstance(t, TypeParamRef) and t.kind is TypeParamKind.INT

def _own_type_param_slot(t: 'TpyType | int | None') -> bool:
    """True if `t` is `Own[T]` for a bare generic type-param `T` -- the
    ownership-transfer sibling of `_is_type_param_slot`. An `Own[T]` param
    (`own_param_t<T>` == `T&&`) and return (`own_return_t<T>`) render
    per-instantiation via the C++ traits, so a bare param read and a DIRECT
    `return <own-param>` both pass bare (no `std::move` on the return -- the
    move only arises at an intermediate local decl, `y = std::move(x)`)."""
    if not isinstance(t, TpyType):
        return False
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(inner, OwnType) and _is_type_param_slot(inner.wrapped)

def _open_tparam_pass_arg(a: 'TpyExpr', ptype: 'TpyType | None',
                          locals_: 'dict[str, TpyType]', analyzer,
                          param_names: 'AbstractSet[str]') -> bool:
    """A PARAM still typed as the SAME open type-param as the slot it feeds
    (`self._raw.store(value, order)` inside `Atomic[T].store`): both sides
    spell `param_val_or_ref_t<T>`, so the forward is bare --
    the slot's C++ shape is only fixed at instantiation, so no borrow /
    storage lift can attach here, and a `param_val_or_ref_t<T>` binding is a
    reference, never a move source.

    Params only, and same-`T` only: a LOCAL of open type could be a movable
    last-use source, and a different open param (or a resolved arg type)
    reopens the conversion question this row does not answer. INT-kind
    params are `std::size_t` values and keep their own rows."""
    if not (isinstance(a, TpyName) and a.name in param_names):
        return False
    if not isinstance(ptype, TpyType):
        return False
    slot = unwrap_readonly(unwrap_ref_type(ptype))
    if not (isinstance(slot, TypeParamRef) and slot.kind is TypeParamKind.TYPE):
        return False
    bound = locals_.get(a.name)
    if not isinstance(bound, TpyType):
        return False
    if unwrap_readonly(unwrap_ref_type(bound)) != slot:
        return False
    occ = analyzer.get_expr_type(a)
    return (isinstance(occ, TpyType)
            and unwrap_readonly(unwrap_ref_type(occ)) == slot)

def _value_record_member(m: 'TpyType') -> bool:
    """A non-generic user VALUE-record union member (`Fixed` in `Fixed | Zone
    | None`, datetime's `ZoneInfo`): stored by value in the variant like a
    scalar, spelled via to_cpp/native_cpp_names (cross-module qualification
    agrees with the resolver). Generic value records stay out -- their
    type-arg recursion is the F5 spelling slice."""
    return (isinstance(m, NominalType) and m.is_user_record
            and m.is_value_type() and not m.type_args)


def _builtin_value_member(m: 'TpyType') -> bool:
    """A non-generic BUILTIN value nominal as a union member (`basic_slice`
    in `Int32 | basic_slice` -> `std::variant<int32_t, ::tpy::BasicSlice>`):
    same by-value storage as a user value record, spelled via the TypeDef's
    to_cpp. Non-user only -- the user flavor is `_value_record_member`;
    the whole STR/BYTES type CLASSES keep their own rows (an owned-buffer
    member's insert/extract renders are theirs to gate, not this row's --
    and qname checks miss the tpy.String/FStr siblings)."""
    if not (isinstance(m, NominalType) and not m.is_user_record
            and m.is_value_type() and not m.type_args):
        return False
    td = type_def_of(m)
    return (td is not None
            and td.category not in (TypeCategory.STR, TypeCategory.BYTES))

def _value_record_slot(t: 'TpyType | None') -> bool:
    """A user VALUE-record DECL slot -- `_value_record_member` widened to
    generic instantiations (`Pair<int32_t> q = p;`). The union-member
    predicate excludes those because a variant member's spelling recurses
    through the type args; a decl spells its slot through `render_type`,
    which already renders the instantiation."""
    return (isinstance(t, NominalType) and t.is_user_record
            and t.is_value_type())


def _native_iter_value_slot(t: 'TpyType | None', analyzer) -> bool:
    """A VALUE-typed native ITERATOR record DECL slot (`SpanIter[T]` --
    `::tpy::SpanIter<int32_t> it = a.__iter__();`): the plain spelled copy,
    `_value_record_slot`'s native sibling keyed on the `__next__` method so
    only the iterator family is admitted; the local's own reads (the
    universal loop, explicit `__next__()`) gate themselves."""
    if not (isinstance(t, NominalType) and not t.is_user_record
            and not t.is_protocol and t.is_value_type()):
        return False
    rec = analyzer.registry.get_record_for_type(t)
    return rec is not None and bool(rec.get_method_overloads("__next__"))


def _protocol_auto_slot(t: 'TpyType | None') -> bool:
    """A structural-protocol-typed DECL slot (`it = iter(c)` binds
    `Iterator[T]`) or a Self slot inside a monomorphized protocol body
    (`result = d.duplicate()` -- Own[Self]): C++ concepts / open Self
    cannot type a variable, so the decl spells `auto`
    and the init render carries the concrete type. @dynamic protocols
    take the adapter machinery instead."""
    return ((isinstance(t, NominalType) and t.is_protocol
             and not t.is_dynamic_protocol)
            or isinstance(t, SelfType))


def _container_rvalue_select(init: 'TpyExpr | None', t: 'TpyType | None',
                             analyzer) -> bool:
    """An ALL-RVALUE container and/or / ternary init (`x = [1,2] or [3,4]`,
    `x = [1,2] if c else [3,4]`): the select is an rvalue (both operands
    are), so the decl is the plain spelled copy -- the deref of the
    pointer-select, or the bare ternary of spelled literals. Lvalue-operand
    selects classify REF_ALIAS and never reach the slot gate."""
    if init is None or t is None:
        return False
    tu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not _f1_container_ref(tu):
        return False
    if isinstance(init, TpyBinOp):
        # The and/or pointer-select AND the arithmetic-binop rvalue
        # (`c = a + b` -> the list_concat prvalue) both land in the plain
        # spelled copy decl; the binop render gates its own operand shapes.
        return is_rvalue_source(analyzer, init)
    if isinstance(init, TpyIfExpr):
        return is_rvalue_source(analyzer, init)
    return False


def _peel_readonly(t: 'TpyType') -> 'TpyType':
    """Strip STACKED ReadonlyType layers: the readonly-span projections
    (`ro.__span__()`) stamp `readonly[readonly[T]]` elements, which render as
    one `const` -- every span element check peels them all."""
    while isinstance(t, ReadonlyType):
        t = t.wrapped
    return t

def _span_slot(t: 'TpyType | None', analyzer) -> bool:
    """A `Span[T]` DECL slot over a scalar or plain-record element
    (`std::span<Node> s = ::tpy::as_mut_span(b);`): the decl only spells the
    slot, and the local's own reads gate themselves, so the reference-element
    span decls as the same plain copy the scalar one does. An OPEN type-param
    element (`Span[readonly[T]]` inside a `[T]` template) rides the same
    argument one step earlier: the element's C++ shape is not chosen until
    instantiation, so no borrow/storage lift can attach to the slot at all.
    `_span_value` stays the narrower READ/pass slice (element-dependent renders
    live there); nested-span and other composite elements keep rejecting --
    their reads have no admitted arm to gate against."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType) or not is_span(u):
        return False
    args = getattr(u, "type_args", None)
    if not args:
        return False
    elem = _peel_readonly(args[0])
    return (_eligible_scalar(elem) or _f1_record(elem, analyzer)
            # A str-family element (`argv = sys.argv[1:]` ->
            # `std::span<std::string>`) spells the same plain copy; its reads
            # are the ordinary str-element rows.
            or _resolved_str_value(elem, analyzer) is not None
            or _type_param_value_slot(elem))


def _eligible_value_union(t: TpyType | None) -> 'UnionType | None':
    """The F4 U1 slice: a value-form union of scalar / Char / str / StrView /
    value-record members (`Int32 | Float64 [| None]`, `Int32 | str`,
    `Fixed | Zone | None`) -- `std::variant<...>` where every member is stored
    by value (a str member is owned `std::string`, a StrView member a
    `std::string_view`, a ValueType record itself). At the WHOLE-variant
    positions the slice routes -- reads/writes/returns/same-type args, and
    isinstance extraction (`std::get<std::string>` / `std::get<Fixed>`,
    spelled through the shared `render_type`) -- the member form is fixed by
    the variant, so the member renders bare (the converting ctor does the
    work) and a `None` source renders `std::monostate{}`. The form-relevant
    boundary is member INSERT: a str-VIEW value into a `... | str` slot is a
    view->owned conversion (`std::variant<...> __tmp = view;`), which
    `_value_union_temp_slot`'s member check rejects. Pointer-variant record
    members (U2) and recursive-alias wrappers are not lowered here."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return None
    if not all(_eligible_scalar(m) or _eligible_char(m)
               or is_str_type(m) or is_str_view_type(m)
               or _value_record_member(m)
               or _builtin_value_member(m)
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
    t = _wrapper_union_like(t)
    if t is None:
        return False
    it = unwrap_readonly(analyzer.get_expr_type(init))
    if not (isinstance(it, NominalType) and it.is_user_record):
        return False
    return any(m == it for m in _wrapper_like_members(t))


def _wrapper_member_literal_slot(init, t: 'TpyType | None',
                                 analyzer) -> bool:
    """The literal sibling of `_wrapper_member_ctor_slot`: an annotated
    wrapper-like decl initialized with a scalar/str/bool/bytes LITERAL
    (`leaf: Tree[int] = 9` -> `Tree<::tpy::BigInt> leaf = 9;`). The
    wrapper's converting ctor absorbs the target-less literal render, the
    plain spelled copy -- the decl twin of
    `_ru_wrapper_scalar_literal_arg`."""
    if t is None:
        return False
    lit = init
    while isinstance(lit, TpyCoerce):
        lit = lit.expr
    if not isinstance(lit, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                            TpyStrLiteral, TpyBytesLiteral)):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _wrapper_union_like(t) is not None


def _union_member_ctor_slot(init, t: 'TpyType | None', analyzer) -> bool:
    """A value-variant union ELEMENT slot over record members, initialized
    with a member-record ctor rvalue (`[Dog("Rex"), Cat("W")]` into
    `std::vector<std::variant<Cat, Dog>>`): the variant's converting ctor
    absorbs the member, so the element renders bare.

    The plain-union twin of `_wrapper_member_ctor_slot`. Mixed unions
    (`Pt | str`) admit too: a CTOR-rvalue element renders bare whatever
    the sibling members are (only LITERAL elements need the target-typed
    render, and those ride their own rows). A wrapper union has its own
    row, and a union-TYPED name source is the `to_value_variant` lift,
    not this."""
    if t is None or not isinstance(init, TpyCall):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return False
    members = [unwrap_readonly(m) for m in t.members]
    it = unwrap_readonly(analyzer.get_expr_type(init))
    if not (isinstance(it, NominalType) and it.is_user_record):
        return False
    return any(m == it for m in members)


def _ptr_union_member_wide(m: TpyType, analyzer) -> bool:
    """The WIDENED variant-member class: the U2 record/scalar slice plus
    str, containers, value tuples and Span. Consumed ONLY by the
    member-shape-BLIND machinery -- the Own[union] return slot, the
    whole-union call-ret admission, the UNION_RVALUE decl twin, the
    isinstance-narrow subject gate, the bare same-union NAME arg pass,
    and the ptr-union ternary row (all spell members via render_type /
    to_cpp_ptr_variant, never per-member renders). `_eligible_ptr_union`
    keeps the narrow set on purpose: widening IT would open every
    narrowing / extraction consumer."""
    if (_f1_record(m, analyzer) or _eligible_scalar(m)
            or is_void_like_type(m)):
        return True
    mu = unwrap_readonly(m)
    # bytes rides beside str for the same reason: both spell one concrete
    # storage type (`std::vector<uint8_t>` / `std::string`) with no type-arg
    # recursion, so `render_type(m)` alone reproduces every spelling here.
    return (is_str_type(mu) or is_bytes_type(mu)
            or _f1_container_ref(mu)
            or _span_value(mu)
            or _value_tuple(mu, analyzer) is not None)


def _distinct_variant_alternatives(u: 'UnionType') -> bool:
    """Every non-None member of `u` spells a DISTINCT C++ type.

    The widened member class holds several TPy types that share one C++
    spelling (`bytes`, `bytearray` and `list[UInt8]` all render
    `std::vector<uint8_t>`), and every consumer reaches the variant
    through `std::holds_alternative<T>` / `std::get<T>`, which are
    ill-formed when an alternative repeats. Keeping such a union out of
    the widened class leaves a located reject where the render would not
    compile at all."""
    seen: set[str] = set()
    for m in u.members:
        # Keyed on the member exactly as `to_cpp_ptr_variant` renders it
        # (typesys.py) -- an alternative collides only when the spelling the
        # render EMITS collides, so unwrapping first could miss a clash the
        # render still produces (or invent one it does not).
        if is_void_like_type(m):
            continue
        cpp = m.to_cpp()
        if cpp in seen:
            return False
        seen.add(cpp)
    return True


def _eligible_ptr_union_wide(t: TpyType | None,
                             analyzer) -> 'UnionType | None':
    """`_eligible_ptr_union` over the WIDENED member class -- see
    `_ptr_union_member_wide` for the scoping contract."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_ptr_union_member_wide(m, analyzer) for m in t.members):
        return None
    if not _distinct_variant_alternatives(t):
        return None
    return t


def _own_storage_union_return(t: TpyType | None, analyzer) -> 'UnionType | None':
    """An `Own[A | B]` return slot over the widened member class (F1
    records, value scalars, str, containers, value tuples, Span): a
    by-value `std::variant<...>` (the storage form -- ownership makes the
    union a value at the return boundary, never the pointer variant). The
    routed sources gate at the return arm (member-record ctor rvalues,
    scalar values, container/span names, slice rvalues), all returned bare
    (the variant's converting ctor absorbs them). A None member keeps the
    slot out (the monostate/None renders are the Optional families');
    all-value-member unions ride `_eligible_value_union` at the
    plain-union return arm."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    inner = unwrap_readonly(u.wrapped)
    if not isinstance(inner, UnionType) or inner.has_none_member():
        return None
    if inner.needs_wrapper():
        # A recursive-alias WRAPPER union is `ret_own_wrapper`'s slot --
        # the two facts are mutually exclusive so the return arm's gates
        # cannot double-fire on one slot.
        return None
    if not all(_ptr_union_member_wide(unwrap_readonly(m), analyzer)
               for m in inner.members):
        return None
    if not _distinct_variant_alternatives(inner):
        return None
    return inner

def _own_wrapper_return(t: TpyType | None, analyzer) -> 'UnionType | None':
    """An `Own[V]` return slot over a recursive-alias WRAPPER union
    (`needs_wrapper()` -- `struct V { std::variant<...> value; }` by
    value): source rows gate at the return arm (None -> monostate, scalar
    literals bare, container literals via the ru render, wrapper-member
    container names bare). The generic-instance flavor
    (RecursiveAliasInstanceType) keeps its own `_own_genrec_return`
    fact."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    w = _wrapper_union_like(u.wrapped, analyzer)
    return w if isinstance(w, UnionType) else None


def _wrapper_borrow_return(t: TpyType | None, analyzer) -> 'UnionType | None':
    """A bare wrapper-union return slot (`-> Expr` -> `Expr&` /
    `const Expr&`): the wrapper twin of `_record_borrow_return`. NAME
    sources gate at the return arm (a borrow param returns bare)."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if isinstance(u, OwnType):
        return None
    w = _wrapper_union_like(u, analyzer)
    return w if isinstance(w, UnionType) else None


def _own_genrec_return(t: TpyType | None) -> 'TpyType | None':
    """An `Own[Tree[Int32]]` return slot -- the generic-instance sibling of
    `_own_storage_union_return`: the wrapper struct returns by value
    (`Tree<int32_t>`), and the routed sources are the ones whose render the
    wrapper's converting ctor absorbs (a scalar member value returns bare,
    a container literal takes the ru-instance spelled render). No member
    restriction: the wrapper is one C++ value type whatever its variant
    holds -- the SOURCE rows gate instead."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    inner = unwrap_readonly(u.wrapped)
    return inner if isinstance(inner, RecursiveAliasInstanceType) else None


def _dyn_borrow_return(t: TpyType | None) -> 'NominalType | None':
    """The @dynamic protocol of a BORROW-form protocol return slot
    (`-> P` -> `P&`, `-> readonly[P]` -> `const P&`): the abstract base
    returns by reference, so the routed sources are NAME reads (a borrow
    param bare, a pointer-local/global deref) -- the return arm gates them.
    None for structural protocols (`_protocol_auto_slot`'s family) and
    every non-protocol slot."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    return u if (isinstance(u, NominalType) and is_dyn_protocol(u)) else None


def _own_dyn_return(t: TpyType | None) -> 'NominalType | None':
    """The @dynamic protocol under an `Own[P]` return slot
    (`std::unique_ptr<P>`): sources dispatch on the shared
    `classify_dyn_own_arg` verdict at the return arm ('forward' names /
    calls bare -- C++ implicit-moves on a by-value return -- conformer
    ctor rvalues take the make_unique/make_adapter wrap). Keyed on the RAW
    wrapped type: an `Own[readonly[P]]` slot never takes these renders."""
    if t is None:
        return None
    u = unwrap_send_sync(t)
    if not isinstance(u, OwnType):
        return None
    w = u.wrapped
    return w if (isinstance(w, NominalType) and is_dyn_protocol(w)) else None


def _eligible_ptr_union(t: TpyType | None, analyzer) -> 'UnionType | None':
    """The F4 U2 slice: a pointer-repr union of record members (`A | B
    [| None]` -> borrow `::tpy::Union<[std::monostate, ]A*, B*>` / storage
    `std::variant<[std::monostate, ]A, B>`). Non-None members must be
    `_f1_record`-renderable (any non-generic user record -- native / cross-module
    type spelling agrees with the resolver; only generics stay off); a None
    member is the monostate slot -- both spellings render it
    `std::monostate`, so it adds no form question.
    A MIXED union's eligible-scalar members (`Int32 | Dog | None` -> an
    `int32_t*` alternative) render through the same member-shape-blind
    machinery (`m.to_cpp() + "*"` borrow, bare value storage, `*std::get<
    int32_t*>(v)` extraction), so they ride the record rows unchanged; an
    ALL-scalar union is value-repr (`is_ptr_variant_union` False) and stays
    on the U1 slice. str and concrete-container members ride
    `_ptr_union_view_member_ok` (to_cpp_ptr_variant pointer-spells EVERY
    member uniformly, so the shape-blind machinery covers them; the
    narrow-vs-wide split with `_ptr_union_member_wide` still stands for
    the shapes that predicate names). Recursive-alias wrappers and
    protocol unions are not lowered here."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_f1_record(m, analyzer) or _eligible_scalar(m)
               or is_void_like_type(m)
               or _ptr_union_view_member_ok(m, analyzer)
               for m in t.members):
        return None
    return t

def _eligible_ptr_union_either(t: TpyType | None,
                               analyzer) -> 'UnionType | None':
    """The member class the WHOLE-union arg rows admit on: the narrow slice,
    or the widened one for a member the narrow slice's test excludes (a
    zero-type-arg `bytearray`).

    Two rows read it -- `_union_pass_through_arg`'s admission and
    `_arg_ptr_union_slot`'s const bridge -- and they have to answer the same
    class or an argument is admitted at one and skipped at the other, which
    is how a deep-const slot once received an un-wrapped argument."""
    return (_eligible_ptr_union(t, analyzer)
            or _eligible_ptr_union_wide(t, analyzer))

def _ptr_union_view_member_ok(m: 'TpyType', analyzer) -> bool:
    """A str or concrete builtin-container member of a ptr-variant union
    (`list[int] | str`): inside a variant both spell the STORAGE form
    (`std::string` / `std::vector<T>`) position-uniformly through the
    member-shape-blind machinery (`m.to_cpp() + "*"` borrow, bare value
    storage), so they extend the record/scalar slice unchanged. Container
    ELEMENT args recurse through the F1 type-arg slice so a divergently
    spelled element keeps the union out."""
    u = unwrap_readonly(m)
    if isinstance(u, NominalType) and (is_str_type(u) or is_bytes_type(u)):
        # bytes spells `std::vector<uint8_t>` storage / `...*` borrow --
        # concrete and position-uniform like str, no type-arg recursion.
        return True
    if _f1_container_ref(u):
        return bool(u.type_args) and all(
            _f1_record_type_arg_ok(a, analyzer) for a in u.type_args)
    return False

def _resolve_plain_alias(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """Resolve a bare (no-args) `AliasRef` placeholder to its registry body;
    every other type passes through unchanged. THE single accessor for the
    AliasRef-vs-resolved-union duality -- sema carries the placeholder at
    some positions (bindings, element slots) and the resolved UnionType at
    others, and per-site re-derivation drifts the moment one is widened."""
    if isinstance(t, AliasRef) and not t.args and analyzer is not None:
        return analyzer.registry.resolve_alias_ref(t)
    return t


def _wrapper_value_return(t: TpyType | None, analyzer) -> 'UnionType | None':
    """A wrapper-union VALUE return slot (`-> Own[Expr]`): the by-value
    wrapper struct binds a const-ref arg slot directly. Own-declared only
    -- a bare `-> Expr` renders `Expr&` and rides
    `_wrapper_borrow_return`."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    w = _wrapper_union_like(unwrap_readonly(u.wrapped), analyzer)
    return w if isinstance(w, UnionType) else None


def _wrapper_union_like(t: TpyType | None, analyzer=None) -> 'TpyType | None':
    """The genrec-track classifier accessor: the shared duck view of a
    wrapper-union-LIKE subject -- a recursive-alias WRAPPER union
    (`UnionType.needs_wrapper()`) OR a generic instance
    (`RecursiveAliasInstanceType`, always wrapper-repr). Both classes carry
    the same duck API (`needs_wrapper()` / `wrapper_info()` /
    `alternatives()` / `substituted_body()`), so every row keyed on THIS
    accessor treats them uniformly and reads members via the type's own API
    -- never re-derived per site: two sites re-deriving the pair drift the
    moment one is widened, which is exactly the failure a single accessor
    exists to prevent. Returns the unwrapped type, or None otherwise."""
    if t is None:
        return None
    t = _resolve_plain_alias(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))), analyzer)
    if isinstance(t, UnionType) and t.needs_wrapper():
        return t
    if isinstance(t, RecursiveAliasInstanceType):
        return t
    return None


def _eligible_wrapper_union(t: TpyType | None,
                            analyzer=None) -> 'UnionType | None':
    """The F6 slice: a recursive-alias WRAPPER union (`needs_wrapper()` --
    emitted as `struct Alias { std::variant<...> value; }`, stored by value
    everywhere). The variant access is the VALUE form reached via `.value`
    (`_narrow_variant_cpp`), so the
    isinstance test and the extraction alias render member-shape-blind
    (`holds_alternative<M>(v.value)` / `std::get<M>(v.value)`); no member
    restriction is needed for the render itself -- the narrowed FACT retypes
    the subject for the branch walk, where the ordinary per-construct gates
    apply. With `analyzer`, a non-generic `AliasRef` slot (the placeholder
    container elements and annotations carry) resolves through the registry
    to its union body first."""
    if t is None:
        return None
    t = _resolve_plain_alias(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))), analyzer)
    if not (isinstance(t, UnionType) and t.needs_wrapper()):
        return None
    return t

def _union_storage_val_cpp(ptr_u: 'UnionType') -> str:
    """The value-variant STORAGE spelling for a ptr-variant union slot
    (`__slot_N`): a NAMED plain union alias (`type Shape = A | B`) spells its
    C++ name, everything else the expanded `std::variant<...>`. The codegen
    `union_alias_names` registry is not populated until header emission (after
    lowering), so the sema-time `union_display_names` is the lowering-visible
    source. The two agree for every slot lowering reaches: a module-LOCAL alias keys
    the same short name in both, and a NON-renamed cross-module alias registers
    that same short name under both the defining module's `setdefault` (display)
    and the importing module's `register_union_alias` (alias). A RENAMED
    cross-module alias (`from mod import Shape as MyShape`) is the only shape
    where they DIVERGE -- display keeps "Shape", alias becomes "MyShape" -- but
    that form is currently rejected at sema (the local-decl annotation cannot
    resolve the renamed name; see BUGS.md + error_union_type_alias_cross_module
    _renamed), so no such slot reaches this function. If that rejection is
    lifted, this must read `union_alias_names` instead."""
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
    reads `x` as Int32), which would need a `std::get` the bare variant name
    does not carry (see BUGS.md). No corpus case exercises it, so such a read
    rejects as `name.union_binding_divergent`."""
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

def _narrow_subject_union(dt: 'TpyType | None',
                          analyzer) -> 'UnionType | None':
    """The routed union a narrowing subject's DECLARED type binds, or None
    when the subject is outside the extraction slice. One definition so the
    condition-shape reader and the fact-driven post-if reader cannot disagree
    about which subjects have an extraction."""
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
    if isinstance(dt, OwnType):
        # An `Own[A | B]` param binds the STORAGE variant
        # (`std::variant<A, B>&&`): sema peels Own before the union check
        # (`_filter_union_codegen_facts`), and the binding is never in
        # `ptr_variant_locals`, so the extraction reads by value
        # (`std::get<A>(u)`) for free.
        dt = unwrap_readonly(dt.wrapped)
    u = (_eligible_value_union(dt) or _eligible_ptr_union_wide(dt, analyzer)
         or _eligible_wrapper_union(dt, analyzer))
    if u is None:
        # A None-narrowed `Optional[wrapper]` POINTER subject
        # (`isinstance(t, int)` after the None test on `t: Tree | None`):
        # the wrapper test reads through the pointer deref
        # (`holds_alternative<M>((*t).value)` -- the name arm's deref
        # composes with the F6 `.value` access), so the WRAPPER is the
        # subject union.
        opt = _optional_ptr_borrow_wide(dt, analyzer)
        if opt is not None:
            w = _wrapper_union_like(unwrap_readonly(opt.inner), analyzer)
            if isinstance(w, UnionType):
                u = w
    return u

def _isinstance_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool] | None':
    """The F4 U3 isinstance-condition shape: `isinstance(v, A)` /
    `isinstance(v, (A, B))` on a declared local/param of a routed union (U1
    value / U2 pointer-variant). Returns `(var, union, check_members,
    folded_true)` or None. `folded_true` is sema's exhaustiveness constant-fold
    (`macro_expansion == True`, the last elif of an exhausted union): the
    condition renders `true` and the dead implicit-else is suppressed
    (`_condition_static_true`). Out of the slice: Any / polymorphic /
    deref-view / type-param subjects (different extraction machinery),
    readonly-qualified subjects (the const-conversion chain), and
    global slots. A resumable FRAME member is in: its
    variant spelling (bare member, or the frame_slot `(*v)` deref) comes
    from `_narrow_variant_cpp` like any other subject's. A recursive-alias
    wrapper union takes the F6 slice
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
    u = _narrow_subject_union(declared.get(var), analyzer)
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
    cannot exhaust an open type), and a `NoneType` check member (the
    typeid(std::monostate) arm) is not lowered yet."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    var = cond.isinstance_var
    dt = declared.get(var)
    if dt is None:
        # A module-level `Any` GLOBAL subject (`isinstance(g, int)`): the
        # value-typed global reads bare (`g.value...`) and the extraction
        # alias is name-based, so the render matches a local's exactly.
        gb = analyzer.global_ns.lookup(var)
        if not (gb is not None and gb.kind is BindingKind.VARIABLE
                and isinstance(gb.type, AnyType)):
            return None
        dt = gb.type
    if unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))) is not dt:
        return None
    if not isinstance(dt, AnyType):
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    if any(isinstance(m, NoneType) for m in members):
        return None
    return var, members


def _deref_view_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, NominalType, TpyType, TpyType, int] | None':
    """The deref-view isinstance if condition (`isinstance(b, Dog)` on a
    Deref-wrapper NAME subject -- Box[Pet] / Rc[Pet] -- with
    isinstance_deref_depth > 0): the payload narrows, the wrapper keeps
    its own type (sema stamps the fact under deref_view_key(var), never
    retyping the name). Returns (var, member, wrapper_decl,
    dispatch_inner, depth) or None."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (not cond.isinstance_deref_depth or cond.isinstance_type_param
            or cond.macro_expansion is not None):
        return None
    member = cond.isinstance_type
    if not isinstance(member, NominalType):
        return None
    var = cond.isinstance_var
    dt = declared.get(var)
    if dt is None:
        return None
    dtu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    depth = cond.isinstance_deref_depth
    cur: 'TpyType | None' = dtu
    for _ in range(depth):
        if cur is None:
            return None
        cur = analyzer.type_ops.get_deref_target_type(cur)
        if cur is not None:
            cur = unwrap_readonly(cur)
    if cur is None:
        return None
    return var, member, dtu, cur, depth


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
    tuple checks, negation, and compound conditions are out."""
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
    fact / identity fact extracts nothing); reads inside
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
    """The poly-subject declared type after the admitted wrappers: bare, the
    param-borrow Ref (a polymorphic-CLASS record param is seeded `Ref[Pet]`;
    dyn-protocol params are never Ref-wrapped), or READONLY-qualified
    (`readonly[Optional[Base]]` -- the const-pointee spelling rides
    `_poly_subject_readonly`). Any other wrapper declines."""
    if dt is None:
        return None
    if isinstance(dt, RefType):
        dt = dt.wrapped
    db = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    if db is not dt and not isinstance(
            unwrap_ref_type(unwrap_send_sync(dt)), ReadonlyType):
        return None
    return db


def _poly_subject_readonly(dt: 'TpyType | None') -> bool:
    """Whether the subject's RAW declared type is readonly-qualified -- the
    key the const arm reads."""
    return (dt is not None
            and isinstance(unwrap_ref_type(unwrap_send_sync(dt)),
                           ReadonlyType))


def _poly_isinstance_value_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, tuple[NominalType, ...], TpyType] | None':
    """A VALUE-position polymorphic isinstance (`return isinstance(e, VE)`):
    the bare null-check chain -- no branch, no alias, so members may be
    strict subclasses or the root, single or tuple. Returns
    `(var, members, var_decl)` or None. A Ref-wrapped (borrowed-param) CLASS
    subject stays out: a `&&` RHS read of the subject may be sema-narrowed,
    needs the inline `(*static_cast<const Sub*>(&p))` read that no
    value-position arm renders, so the `_poly_subject_decl` Ref peel must not
    widen THIS arm."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if (cond.isinstance_type_param or cond.isinstance_deref_depth
            or cond.macro_expansion is not None):
        return None
    var = cond.isinstance_var
    if isinstance(declared.get(var), RefType):
        return None
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
    form's A|B branch fact aliases nothing, as on the union path)."""
    ft = facts.get(var)
    if ft is None or isinstance(ft, UnionType) or is_void_like_type(ft):
        return None
    return ft if any(m == ft for m in members) else None


def _any_narrow_facts_ok(members: tuple[TpyType, ...],
                         facts: dict[str, TpyType], var: str) -> bool:
    """A branch facts map the Any slice can extract: facts describe only the
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
    remaining-union / void fact -- the extraction skips those). A fact that is
    neither a member of `u` nor union/void has no extraction; the caller
    gate-rejects on it via `_narrow_facts_ok`."""
    ft = facts.get(var)
    if ft is None or isinstance(ft, UnionType) or is_void_like_type(ft):
        return None
    return ft if any(m == ft for m in u.members) else None

def _narrow_facts_ok(u: UnionType, facts: dict[str, TpyType], var: str) -> bool:
    """A branch facts map the slice can extract: facts describe only the checked
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
    """Whether a facts map would emit an extraction -- the elif-flattening
    gate (a concrete else-fact forces `} else {` + a nested if instead of a
    flat `else if`)."""
    return any(
        not (isinstance(ty, (UnionType, LiteralType)) or is_void_like_type(ty))
        and not is_protocol_type(ty)
        for ty in facts.values()
    )

def _facts_emit_alias(facts: dict[str, TpyType]) -> bool:
    """Whether `emit_isinstance_extractions` would DECLARE an alias for these
    branch facts: `_facts_have_concrete`'s rows minus the deref-view keys,
    whose narrowing rides `deref_narrowed_to` with no local of its own.
    A branch arm that emits no extraction must fence on this, or the alias
    is silently dropped."""
    return any(
        parse_deref_view_key(k) is None
        and not (isinstance(ty, (UnionType, LiteralType))
                 or is_void_like_type(ty))
        and not is_protocol_type(ty)
        for k, ty in facts.items()
    )

def _is_elif_link(outer: TpyIf, inner: TpyIf) -> bool:
    """Mirror of `emit._is_elif`: an elif keeps the outer's column; a
    nested `else: if` sits deeper. Both-locs-None (macro
    fragments) counts as elif."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column

def _elif_link(stmt: TpyIf) -> 'TpyIf | None':
    """The single elif continuation in `stmt`'s else body (the
    is_elif_continuation shape: one same-column TpyIf), or None for a genuine
    else block. Whether the link then FLATTENS to `else if` additionally
    requires no concrete else-fact (`_facts_have_concrete`) -- the callers
    that flatten check that separately."""
    if (len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], TpyIf)
            and _is_elif_link(stmt, stmt.else_body[0])):
        return stmt.else_body[0]
    return None


def _flatten_binop_leaves(cond: TpyExpr, op: str) -> 'list[TpyExpr]':
    """Flatten a single-op boolean tree into its leaves, in source order;
    a nested different-op subtree stays one leaf."""
    leaves: list[TpyExpr] = []

    def flat(e: TpyExpr) -> None:
        if isinstance(e, TpyBinOp) and e.op == op:
            flat(e.left)
            flat(e.right)
        else:
            leaves.append(e)

    flat(cond)
    return leaves


def _or_chain_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, list, bool] | None':
    """An `or` chain with isinstance checks on ONE subject (`isinstance(v,
    A) or isinstance(v, B)`, `isinstance(v, (A, B)) or flag`), possibly
    under a leading `not`: every isinstance leaf narrows the same
    var/union (a sema-FOLDED leaf -- the exhaustiveness constant --
    renders `true` in place); other leaves are ordinary bool conditions,
    lowered with the left-siblings' COMPLEMENT fact installed (the
    false-branch remainder). Returns `(var, union, [(members, folded,
    leaf)...], negated)` -- isinstance leaves only -- or None; branch
    facts ride the shared narrow-if skeleton (a union then-fact extracts
    nothing; concrete facts, including sema's dead-branch ones on a folded
    chain, emit their extractions)."""
    negated = isinstance(cond, TpyUnaryOp) and cond.op == "!"
    inner = cond.operand if negated else cond
    if not (isinstance(inner, TpyBinOp) and inner.op == "||"):
        return None
    var: 'str | None' = None
    u: 'UnionType | None' = None
    leaf_infos = []
    for leaf in _flatten_binop_leaves(inner, "||"):
        info = _isinstance_narrow_info(leaf, declared, analyzer)
        if info is None:
            continue
        lvar, lu, members, folded = info
        if var is None:
            var, u = lvar, lu
        elif lvar != var or lu != u:
            return None
        leaf_infos.append((members, folded, leaf))
    if var is None or u.needs_wrapper():
        return None
    return var, u, leaf_infos, negated


def _post_if_chain_tail(stmt: TpyIf) -> TpyIf:
    """The link post-narrowing runs on: the flat elif chain is collected
    first and `chain[-1]` taken, so the fact belongs to the last
    FLATTENABLE link (same column, no concrete intermediate else-fact),
    whatever the head condition's kind. A nested `else: if` is a body
    statement of its else block and handles its own post-if there."""
    last = stmt
    while ((nxt := _elif_link(last)) is not None
           and not _facts_have_concrete(last.else_type_facts)):
        last = nxt
    return last

def _protocol_isinstance_condition(cond: TpyExpr) -> bool:
    """A protocol isinstance condition: a sema STAMP read under any number
    of `not` peels, never a match on the condition's tree."""
    if isinstance(cond, TpyCall) and cond.isinstance_is_protocol:
        return True
    if isinstance(cond, TpyUnaryOp) and cond.op == "!":
        return _protocol_isinstance_condition(cond.operand)
    return False

def _condition_static_true(cond: TpyExpr) -> bool:
    """Sema folded the WHOLE condition to `True`, so its implicit-else
    fall-through is dead and post-narrowing there
    would extract a member the enclosing flow already excluded."""
    me = getattr(cond, "macro_expansion", None)
    return isinstance(me, TpyBoolLiteral) and me.value is True

def _post_if_subject_decl(var: str, declared: dict[str, TpyType],
                          narrow) -> 'TpyType | None':
    """The DECLARED type of a post-if subject, which narrowing never retypes.
    Lowering's `declared` DOES retype a live narrow to its member, so a
    narrowed subject reads the parked original -- and it seeds a
    polymorphic-class record PARAM as `Ref[Rec]` where the fact is stated
    against the bare type, so the borrow wrapper comes off.

    Locals and params only, with no fall-through to the global namespace.
    Sound because no global whose fact this arm would extract gets this far: a
    union global is seeded into `declared` by neither global-seeding
    predicate, so an isinstance on one rejects at the CONDITION; an `Any`
    global is dropped as well; a polymorphic global IS seeded and resolves
    identically."""
    if var in narrow.narrowed or var in narrow.spelled:
        dt = (narrow.subject_union.get(var) or narrow.poly_source.get(var)
              or declared.get(var))
    else:
        dt = declared.get(var)
    return dt.wrapped if isinstance(dt, RefType) else dt

def _post_if_ast_facts(stmt: TpyIf, declared: dict[str, TpyType], narrow,
                       analyzer) -> dict[str, TpyType]:
    """The else-facts the post-narrowing arm extracts after `stmt`, in fact
    order.

    Driven by the FACTS: sema builds them by a compositional recursion over
    the whole boolean algebra, so any reader that recovers the subject from
    the condition's syntax instead covers strictly less than the producer and
    silently drops what it cannot spell. The condition is consulted for two
    whole-condition filters, and both read a sema stamp rather than the tree.

    Two further filters are NOT applied here, and both omissions bite a
    widener:

    - The post-if extraction must be suppressed ENTIRELY when an elif
      condition needs hoisted temps. Not applied, and unreachable only because
      every construct that forces the hoist rejects first -- so admitting one
      of those admits a shape that must emit NO extraction while this map
      still names one.
    - Neither this nor `_facts_emit_alias` applies
      `emit_isinstance_extractions`' `overload_param_types` skip (the param is
      already the concrete type there), so both OVER-reject inside an
      @overload specialization. Over-rejection is a compile error, never wrong
      code, and costs nothing today."""
    last = _post_if_chain_tail(stmt)
    if last.else_body or not last.else_type_facts:
        return {}
    if not _facts_have_concrete(last.else_type_facts):
        return {}
    if not (last.then_body
            and isinstance(last.then_body[-1], (TpyReturn, TpyRaise))):
        return {}
    if (_protocol_isinstance_condition(last.condition)
            or _condition_static_true(last.condition)):
        return {}
    registry = analyzer.registry
    facts: dict[str, TpyType] = {}
    for k, ft in last.else_type_facts.items():
        dt = _post_if_subject_decl(k, declared, narrow)
        if not (_recursive_union_shape(dt)
                or is_polymorphic_subclass_fact(dt, ft, registry)
                # A union member cannot be re-narrowed to a different concrete
                # type, so an already-live alias is correct as-is.
                or (_narrows_to_union_member(dt, ft)
                    and k not in narrow.narrowed and k not in narrow.spelled)):
            continue
        if (isinstance(ft, UnionType) or is_void_like_type(ft)
                or parse_deref_view_key(k) is not None):
            # `emit_isinstance_extractions`' pure skips: no alias, no scope
            # change, so these keys need no extraction here.
            continue
        facts[k] = ft
    return facts

def _recursive_union_shape(vt: 'TpyType | None') -> bool:
    """The subject's storage form is a recursive-union wrapper struct. The
    post-`_fix_recursive_optional_annotations` shape `OptionalType(AliasRef)`
    counts too -- its `needs_wrapper()` is False despite the aliased body
    being a wrapper struct."""
    if vt is None:
        return False
    if vt.needs_wrapper():
        return True
    return isinstance(vt, OptionalType) and isinstance(vt.inner, AliasRef)

def _narrows_to_union_member(vt: 'TpyType | None', narrowed: TpyType) -> bool:
    """A plain union left narrowed to one concrete member. Member identity is
    `==` -- a looser match here would extract where the extraction arm does
    not."""
    base = unwrap_readonly(vt) if vt is not None else None
    return (isinstance(base, UnionType)
            and not isinstance(narrowed, UnionType)
            and any(m == narrowed for m in base.members))

def _post_if_narrow_plan(
        stmt: TpyIf, declared: dict[str, TpyType], narrow, analyzer,
) -> 'tuple[tuple[str, TpyType, UnionType | None], ...]':
    """The persistent extractions to append after `stmt`, in fact order:
    `(var, member, union)` for a variant extraction, `(var, member, None)` for
    a polymorphic cast-and-cache.

    Raises `ThirUnsupported` (`if.post_narrow_unmirrored`) for any fact that
    must be extracted but has no arm here. Silence is not an option at this
    arm: lowering either generates or raises, and a drop would lose the alias
    without a diagnostic."""
    ast_facts = _post_if_ast_facts(stmt, declared, narrow, analyzer)
    if not ast_facts:
        return ()
    poly = _poly_post_if_fact(stmt, declared, narrow.poly_source, analyzer)
    plan: list[tuple[str, TpyType, UnionType | None]] = []
    for var, member in ast_facts.items():
        if poly is not None and poly[0] == var:
            plan.append((var, poly[1], None))
            continue
        u = _narrow_subject_union(_post_if_subject_decl(var, declared, narrow),
                                  analyzer)
        if (u is None or var in narrow.narrowed or var in narrow.spelled
                or not any(m == member for m in u.members)):
            note_detail("if.post_narrow_unmirrored")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        plan.append((var, member, u))
    return tuple(plan)

def _poly_anchor_declared(
        cond: TpyExpr, declared: dict[str, TpyType],
        poly_source: dict[str, TpyType]) -> dict[str, TpyType]:
    """`declared` with the condition's subject re-anchored to its ORIGINAL
    declared type after a prior poly narrow retyped it (narrowing never
    retypes the declared type). Shared by
    every poly re-narrowing detector so the subclass-fact and cast-input
    verdicts key on the source decl, not the live member."""
    var = getattr(cond, "isinstance_var", None)
    if var is None or var not in poly_source:
        return declared
    anchored = dict(declared)
    anchored[var] = poly_source[var]
    return anchored

def _poly_post_if_fact(
        stmt: TpyIf, declared: dict[str, TpyType],
        poly_source: dict[str, TpyType], analyzer,
) -> 'tuple[str, NominalType] | None':
    """The POLYMORPHIC early-return implicit-else fact: `if not
    isinstance(v, Sub): return/raise` on a poly-dispatch subject leaves code
    after the `if` narrowed to Sub, so the persistent cast-and-cache alias
    is emitted at the enclosing scope (the post-narrowing arm's
    `is_polymorphic_subclass_fact` filter). Runs on the same chain
    tail the union arm does, `not`-peel included; unlike the union arm, an
    already-narrowed subject RE-narrows (the alias suffix-bumps, anchored to
    the original decl via `poly_source`). Returns `(var, member)` or None;
    a facts map beyond the single `{var: member}` entry stays out of the
    slice (the caller's arm gate rejected the compound shapes already)."""
    last = _post_if_chain_tail(stmt)
    if last.else_body or not last.else_type_facts:
        return None
    if not (last.then_body
            and isinstance(last.then_body[-1], (TpyReturn, TpyRaise))):
        return None
    cond = last.condition
    if isinstance(cond, TpyUnaryOp) and cond.op == "!":
        cond = cond.operand
    info = _poly_narrow_info(
        cond, _poly_anchor_declared(cond, declared, poly_source), analyzer)
    if info is None:
        return None
    var, member, _dt = info
    facts = last.else_type_facts
    if list(facts) != [var] or facts[var] != member:
        return None
    return var, member

def _reassert_bump_info(
        stmt: TpyAssert, declared: dict[str, TpyType],
        persistent_narrowed, analyzer,
) -> 'tuple[str, TpyType] | None':
    """The U4 re-assert: `assert isinstance(v, A)` on a subject already
    PERSISTENTLY extracted to that same member. Sema folds the condition
    (`macro_expansion == True`, so the test emits `if (!(true)) ...`) and the
    extraction re-runs with a suffix-bumped alias (`__v` -> `__v_2`)
    targeting the original variant. `persistent_narrowed` answers whether
    the subject's live alias is statement-level (eligibility's set /
    lowering's derived var set) -- after a branch/loop-scoped extraction the
    re-run would REDECLARE the alias in the same C++ block (see BUGS.md), so
    those reject. Returns `(var, member)` or None."""
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

    The literal-negation fold (op `-`, a `TpyIntLiteral` operand of
    `IntLiteralType`): the negated value renders as one int literal against
    the target, so lowering carries that spelling on the resulting
    `THIRLiteral`, including wide-value casts and suffixes."""
    if not (isinstance(e, TpyUnaryOp) and e.op == "-"
            and isinstance(e.operand, TpyIntLiteral)
            and isinstance(analyzer.get_expr_type(e.operand), IntLiteralType)):
        return None
    return -e.operand.value

def _resolved_scalar(t: TpyType | None, analyzer) -> bool:
    """`_eligible_scalar` over a type that may still be an IntLiteralType: a
    literal-seeded container leaves IntLiteral element types on its use sites
    (the sema-resolved method fi's slots, a `pop`/subscript result, print args of
    its loop var); such a type resolves through TypeResolver/default-int, and
    the emitted value is the same bare literal either way. Readonly/Ref
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

def _resolve_tuple_pending(tt: TupleType, analyzer) -> TupleType:
    """Mirror `TypeResolver._resolve_tuple_pending`: `to_cpp*` do not resolve
    pending slots (a str element carries PendingStr until usage-resolved), so
    every tuple wrap spelling resolves its elements first."""
    resolve_lit = analyzer.ctx.default_int_for_literal
    resolved = []
    for et in tt.element_types:
        rv = _resolve_pending_view(et, analyzer)
        resolved.append(rv if rv is not None
                        else resolve_int_literals(et, resolve_lit))
    return TupleType(tuple(resolved))

def _resolved_str_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The sema-RESOLVED str-slice type -- owned `str` (`std::string` storage /
    `std::string_view` param), `StrView` (`std::string_view`), or `tpy.String`
    (`std::string` everywhere, including the `const std::string&` param slot
    the SKELETON emitter spells -- no body arm renders it) -- or None outside
    the slice. A str local's binding type stays `PendingStrType` on the
    sema side; resolve it like `_resolve_pending_view` does. `Char` and the
    bytes family are out of the slice; a str-based `Literal[...]`
    resolves as its base. Callers that key on the FORM axis must treat
    String as owned:
    `is_str_type` is False for it, so a bare `is_str_type(resolved)` test
    reads it as a view -- see `_str_name_form`."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, LiteralType):
        # A str-based Literal binding is VIEW-form everywhere: its values
        # are static-lifetime string literals, so view-safety inference
        # gives locals AND params `std::string_view` storage (pinned by
        # `calls/literal_local_from_literal_call`). The dead-branch folds
        # are decided at the compare lowering via `lc.literal_facts`.
        b = unwrap_readonly(t.base_type)
        if isinstance(b, NominalType) and (is_str_type(b)
                                           or is_str_view_type(b)):
            return STR_FAMILY.view_type
        return None
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
    `Literal[bytes]` bindings are out of the slice."""
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

def _own_viewfam_param(t: TpyType | None) -> 'TpyType | None':
    """The owned buffer type behind an `Own[str]` / `Own[bytes]` PARAM
    declaration, or None. Such a param's C++ signature spells the OWNED type
    by value (`std::string` / `std::vector<uint8_t>`), so its name reads are
    STORAGE form -- unlike a plain `str`/`bytes` param, whose signature is the
    view (`std::string_view` / `std::span<const uint8_t>`) and whose reads are
    BORROW. View-ness keys on `is_str_type(<declared param type>)`, which is
    False for the Own wrapper. (`Own[StrView]` / `Own[BytesView]` params are
    the no-op Own
    spelling over a value view -- still view-form, excluded here.)"""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(u, OwnType):
        return None
    inner = unwrap_readonly(u.wrapped)
    if isinstance(inner, NominalType) and (is_str_type(inner)
                                           or is_bytes_type(inner)):
        return inner
    return None

def _bytes_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-slice comparison operand: a bytes literal (rendered OWNED --
    no compare target is threaded for bytes, so the literal takes its owned
    render) or a bytes/BytesView value.
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
    inside `_resolved_str_value`'s slice (its `const std::string&` param slot
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
    receiver/param target is owned `bytes`, never `BytesView`, so the
    target-threaded render takes the owned arm) or a bytes/BytesView value
    (param, local, nested concat, or owned-bytes call result -- names and
    spans render bare; the vector->span conversion at the span params is
    implicit). A `bytearray` operand also resolves to the native
    `bytes_concat` dunder; it is not a bytes-slice value, so its own leg
    keys on the bytearray binding directly (its `std::vector<uint8_t>`
    converts to the helper's span param implicitly, so the read is bare
    too)."""
    if isinstance(e, TpyBytesLiteral):
        return True
    if _resolved_bytes_value(t, analyzer) is not None:
        return True
    tu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
          if t is not None else None)
    return bool(tu is not None and is_bytearray_type(tu)
                and _witness("binop.bytearray_operand"))

def _peel_coerce(e: TpyExpr) -> TpyExpr:
    """The expression under any stack of TpyCoerce wrappers."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e

def _str_self_append_rhs(target_name: str, value: TpyExpr) -> 'TpyExpr | None':
    """The `x = x + y` self-append trigger: peel any TpyCoerce wrappers, then
    match `TpyBinOp("+", TpyName(target), rhs)`. Returns the rhs (`y`) the
    peephole appends, or None when the shape does not match (the assignment
    then lowers as a plain reassign). The TARGET-type half of the condition is
    checked by the caller via `_owned_str_append_target`."""
    inner = _peel_coerce(value)
    if (isinstance(inner, TpyBinOp) and inner.op == "+"
            and isinstance(inner.left, TpyName)
            and inner.left.name == target_name):
        return inner.right
    return None

def _owned_str_append_target(t: TpyType | None, analyzer) -> bool:
    """The target-type half of the in-place-append conditions (the str `+=`
    branch and the `x = x + y` peephole): the binding is owned-str-family --
    `str`, `String`, or a `PendingStrType`. A pending binding must RESOLVE
    owned -- the very reassign/aug-assign being checked forces that
    resolution -- which keeps the emitted `t += v;` honest."""
    st = _resolved_str_value(t, analyzer)
    if st is not None and is_str_type(st):
        return True
    # A String binding resolves INSIDE the str slice now, so the owned check
    # must run on the resolved type too -- otherwise `s += v` on a String
    # target reads as a view and loses the in-place append.
    return _is_string_owned(st if st is not None else t)

def _str_name_form(name: str, resolved: TpyType, param_names: set[str],
                   owned_params: 'set[str] | frozenset[str]' = frozenset()
                   ) -> Form:
    """The C++ shape of a str-slice NAME read: a `StrView`-resolved binding
    and a `str`-typed param
    (the signature spells `std::string_view`) are view/BORROW; an owned local is
    `std::string` (STORAGE). The owned-sink copy (`std::string(x)` at a decl
    init / return) fires only on a BORROW source; a str literal is const
    char[N] (implicitly convertible both ways) and stays VALUE, never wrapped.
    A `String` binding is owned in EVERY position -- its param slot is
    `const std::string&`, so the param arm must not read it as a view.
    `owned_params` (prescan's `owned_viewfam_params`) carves the `Own[str]`
    params out of the param arm: their signature spells the OWNED type by
    value, so their reads are STORAGE like any owned local (the
    view-at-runtime test keys on the declared type, which the Own wrapper
    fails)."""
    if is_string_type(resolved):
        return Form.STORAGE
    if is_str_view_type(resolved) or (name in param_names
                                      and name not in owned_params):
        return Form.BORROW
    return Form.STORAGE

def _bytes_name_form(name: str, resolved: TpyType, param_names: set[str],
                     owned_params: 'set[str] | frozenset[str]' = frozenset()
                     ) -> Form:
    """The bytes twin of `_str_name_form`: a `BytesView`-resolved binding and
    a `bytes`-typed
    param (the signature spells `std::span<const uint8_t>`) are view/BORROW --
    they drive the owned-sink `::tpy::Bytes(x)` -- while an owned local is
    `std::vector<uint8_t>` (STORAGE). `owned_params` carves out the
    `Own[bytes]` params (owned `std::vector<uint8_t>` by value -- STORAGE),
    exactly like the str twin."""
    if is_bytes_view_type(resolved) or (name in param_names
                                        and name not in owned_params):
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
    `std::string` global is STORAGE and a `StrView` one BORROW, per the
    view-source rule); a `Ptr[T]` global is a `T*` VALUE
    slot (PtrType is a value type, so no pointer-slot indirection arises).
    A VALUE-TUPLE global (scalar / owned-str / nested value-tuple elements,
    `_value_tuple` recursively -- the `Final[tuple[...]]` constants) is also
    a plain namespace-scope value whose read renders bare; its routed
    consumers (the standalone tuple-unpack source, tuple subscript reads)
    render `std::get` over the bare name. Everything else rejects:
    containers/records are pointer slots, Optional-value globals have no
    read/narrow arms yet, and unions/enums are unprobed.
    Returns None when out of the family."""
    if gt is None:
        return None
    gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
    # An unannotated literal-init global carries IntLiteralType; its storage
    # resolves through the module default int, so resolve before the
    # family check.
    gt = resolve_int_literals(gt, analyzer.ctx.default_int_for_literal)
    if _eligible_scalar(gt) or _eligible_char(gt):
        return gt
    if (_resolved_str_value(gt, analyzer) is not None
            or _resolved_bytes_value(gt, analyzer) is not None
            or _eligible_ptr_value(gt, analyzer)):
        return gt
    if _value_tuple_global(gt, analyzer) is not None:
        return gt
    if _f1_tuple(gt, analyzer) is not None:
        # A pointer-repr F3 tuple global is ALSO a plain namespace-scope
        # value (`std::tuple<std::optional<T>, ..> g;` -- tuples are value
        # types, never pointer slots); its read is a STORAGE lvalue, so the
        # seeding call site must register the name in `storage_tuple_locals`
        # (the borrow-vs-storage NAME partition) alongside this admission.
        return gt
    # A VALUE-record global (`UTC: timezone = timezone(timedelta())`) is a
    # plain namespace-scope object like any value global: never a pointer
    # slot, so reads render the bare (same-module) or qualified (imported)
    # name with no indirection. The F1 slice pins the type spelling;
    # non-value records take the pointer-slot branch.
    if gt.is_value_type() and _f1_record(gt, analyzer):
        return gt
    # A WRAPPER-union global (`g: Expr = [...]` -> `Expr g;`): the wrapper
    # struct is a direct-storage namespace-scope object -- the generator's
    # pointer_globals excludes needs_wrapper -- so reads render bare
    # exactly like a wrapper LOCAL's.
    if isinstance(gt, UnionType) and gt.needs_wrapper():
        return gt
    # A value-repr Optional[scalar] global (`std::optional<T>` at namespace
    # scope) reads exactly like a value-opt LOCAL: bare whole-optional
    # (None-tests, opt slots), `(*g)` on a narrowed occurrence, and
    # `deref_optional_check(g)` unproven -- the narrowed-global deref
    # family renders through the same local-shaped sites, keyed on
    # `_declared_type_incl_globals`. Callers register the name in
    # lc.value_opt_bindings so the reads ride _value_opt_scalar_binding.
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
    or a reference-form builtin container; Optional/union/protocol and
    iterator-typed globals have no read arms here and stay rejected. Pass `name`
    (with the module's `native_globals`) for a same-module candidate so
    the Final / native-linkage exclusion lives here (those names are
    namespace-scope VALUES, never slots); imported
    candidates key finality on their own registry facts and pass no name.
    Returns None when out of the family."""
    if gt is None:
        return None
    if name is not None and (name in native_globals
                             or name in analyzer.ctx.final_globals):
        return None
    gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
    # A ptr-repr Optional global is the SAME `T* g{};` slot as its plain
    # sibling (nullable); reads ride the pointer-binding arms (bare copy,
    # `g == nullptr` tests, narrowed derefs) like an opt-ptr LOCAL's.
    if (isinstance(gt, OptionalType) and gt.uses_pointer_repr()
            and not isinstance(gt.inner, ReadonlyType)
            and _f1_record(gt.inner, analyzer)):
        return gt
    if gt.is_value_type() or gt.needs_wrapper():
        return None
    if (_f1_ref(gt, analyzer)
            # A @dynamic-protocol global is the same `T* g{};` slot; its
            # reads ride the pointer-local arms exactly like the erased
            # dyn LOCAL's (`(*g)` deref, `g->` receiver).
            or (isinstance(gt, NominalType) and is_dyn_protocol(gt))
            # A STRUCTURAL-protocol global (`it = iter(d)` at module scope)
            # is the `decltype(...)* it{};` slot: reads ride the same
            # pointer-local arms -- the for-head's `auto& __src_N = (*it);`
            # deref capture is the witnessed one; the slot's own decl/init
            # render is the top-level global-slot-proto arm's.
            or (isinstance(gt, NominalType) and gt.is_protocol
                and not is_dyn_protocol(gt))):
        return gt
    return None

def _open_value_tuple(t: TpyType | None) -> 'TupleType | None':
    """A tuple carrying an UNBOUND type param alongside value elements
    (`tuple[T, Int32]` inside a generic body). The C++ spelling is
    `std::tuple<::tpy::val_or_ptr_t<T>, int32_t>`, which the whole family
    passes bare -- there is no borrow/storage duality to resolve until T
    binds. The concrete tuple classifiers all decline it, since none of them
    can answer `is_value_type()` for the open element.

    Elements arrive `RefType(T)` at a param slot and bare at a return, so the
    per-element `RefType` peel is required, not cosmetic."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    els = [el.wrapped if isinstance(el, RefType) else el
           for el in t.element_types]
    if not any(isinstance(el, TypeParamRef) for el in els):
        return None
    return t if all(isinstance(el, TypeParamRef) or el.is_value_type()
                    for el in els) else None


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
    positions whose render NARROWS a BigInt (`.to_fixed_check<int32_t>()`
    at subscript indices / slice bounds, the range-counter machinery) -- the
    slice pins those to fixed-int operands."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_big_int_type(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))

_BIGINT_NARROW = "bigint_narrow"  # synthetic THIRCoerce tag (not a sema coercion)

# The narrow-key answer for a shape this slice has no render for.
_NARROW_UNMIRRORED = object()


def _has_widened_int_name(e: TpyExpr, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """True when a NAME whose declared type is a runtime BigInt but whose
    per-occurrence type is not sits inside `e` where codegen's
    `get_resolved_type` would recompute the result from it (the arithmetic
    binop and ternary arms recurse into operands; comparisons and coercions
    stop at sema's type). Such a composite would emit ill-formed C++ --
    `p + 1` on a retro-widened `p` renders `add_check<int32_t>(p, 1)`
    against a `BigInt p` -- so the slice rejects it."""
    if isinstance(e, TpyName):
        d = declared.get(e.name)
        return (d is not None and _runtime_bigint(d, analyzer)
                and not _runtime_bigint(analyzer.get_expr_type(e), analyzer))
    if isinstance(e, TpyBinOp):
        if e.op in ("==", "!=", "<", ">", "<=", ">="):
            return False
        return (_has_widened_int_name(e.left, declared, analyzer)
                or _has_widened_int_name(e.right, declared, analyzer))
    if isinstance(e, TpyIfExpr):
        return (_has_widened_int_name(e.then_expr, declared, analyzer)
                or _has_widened_int_name(e.else_expr, declared, analyzer))
    return False


def _narrow_key_type(e: TpyExpr, declared: dict[str, TpyType],
                     analyzer) -> 'TpyType | None | object':
    """The type `is_runtime_bigint` keys on at a checked-narrow position --
    `get_resolved_type`, which reads a NAME's DECLARED type. Only the name arm
    is covered: a COMPOSITE over a retro-widened local returns
    `_NARROW_UNMIRRORED` and the caller must reject."""
    if not isinstance(e, TpyName) and _has_widened_int_name(e, declared,
                                                            analyzer):
        return _NARROW_UNMIRRORED
    t = _declared_type(e, declared, analyzer)
    # `get_resolved_type` strips readonly at every arm, and the consumers here
    # test the bare int type.
    return unwrap_readonly(t) if t is not None else None

def _unwrap_lit_coerce(e: TpyExpr) -> TpyExpr:
    """Strip sema's int-literal slot coercions (fixed-int / BigInt targets) so
    literal-shape checks see the digit token itself."""
    while (isinstance(e, TpyCoerce)
           and e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)):
        e = e.expr
    return e

def _bigint_index_disposition(index: TpyExpr, obj_type: 'TpyType | None',
                              analyzer,
                              declared: dict[str, TpyType]) -> 'str | TpyType':
    """How a subscript index renders when its type half is a runtime BigInt --
    the subscript render's decision, written once so the gates and the wrap
    sites cannot drift. obj_type is the receiver (its declared key/index type picks
    the narrow width via bigint_index_narrow_type):

      * 'bare' -- no narrow: not runtime-BigInt, an int32-range (possibly
        negated) int literal (a plain int constant is exempt, and the
        emitter renders an unresolved IntLiteralType literal as the bare
        token), or a BigInt-keyed receiver (the index passes through
        unnarrowed; a big literal renders through the shared
        render_int_literal_value);
      * a fixed-int TpyType -- the `{0}.to_fixed_check<T>()` wrap at the
        receiver's declared key width (no outer parens: any composite render
        already carries its own);
      * 'reject' -- an out-of-int32-range literal headed for a narrow: that
        needs the BigInt ctor wrap inside the narrow, which the literal emit
        does not render.

    The type half keys on the DECLARED type (`_declared_type`), as
    `is_runtime_bigint` does -- a literal-seeded local widened to BigInt by a later
    assignment still types Int32 at this occurrence."""
    key = _narrow_key_type(index, declared, analyzer)
    if key is _NARROW_UNMIRRORED:
        return "reject"
    if not _runtime_bigint(key, analyzer):
        return "bare"
    narrow = (INT32 if obj_type is None
              else bigint_index_narrow_type(obj_type, analyzer))
    c = _const_index(index)
    if c is not None:
        if -(2**31) <= c <= 2**31 - 1:
            return "bare"
        return "bare" if narrow is None else "reject"
    if _const_index(_unwrap_lit_coerce(index)) is not None:
        # A coerce-wrapped literal is not a plain int constant, so the
        # narrow would wrap the literal's target-typed render -- a shape
        # not observed at index positions (sema leaves indices unwrapped);
        # a defensive reject rather than a guess.
        return "reject"
    return "bare" if narrow is None else narrow

def _narrow_bigint_index(idx: 'THIRExpr', e: TpyExpr, obj_type: 'TpyType | None',
                         analyzer, loc,
                         declared: dict[str, TpyType]) -> 'THIRExpr':
    """Wrap a lowered runtime-BigInt index in the `.to_fixed_check<T>()`
    narrow when its disposition says so (reads, del-item); 'reject' never
    reaches lowering (the gates exclude it)."""
    disp = _bigint_index_disposition(e, obj_type, analyzer, declared)
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
    """The rendered `E::A` spelling for a type-level enum member access (a
    BindingKind.ENUM head, or a chained nested access):
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
    """The registered module a name/field chain resolves to (`m` after
    `import m`, `pkg.sub` after `import pkg.sub`), else None. A name in
    `declared` (a local/param) is a VARIABLE binding, so it short-circuits to
    None before the module-level lookup."""
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
    """The chain names a static C++ type (record/enum, nested, imported, or
    module-qualified), so a class-constant read off it needs no receiver eval.
    `declared` stands in for the function-namespace VARIABLE bindings (a
    shadowing local makes the chain a runtime expression)."""
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
    """The registered module a BARE `mod.X` receiver names (a MODULE
    binding, aliased imports resolved through import_source), or None. A
    shadowing local/global VARIABLE binding makes the read a normal field
    access -- `declared` covers the local half, the global_ns kind check the
    module-level half."""
    if not isinstance(obj, TpyName) or obj.name in declared:
        return None
    binding = analyzer.global_ns.lookup(obj.name)
    if binding is None or binding.kind != BindingKind.MODULE:
        return None
    return binding.import_source[0] if binding.import_source else obj.name

def _module_var_read_cpp(module_name: str, var_name: str,
                         analyzer) -> 'str | None':
    """The render of a module-variable read (`mod.X` / `pkg.sub.X`): the
    declared native symbol, the `(*slot)` deref for a non-value pointer slot,
    or the qualified `cpp_expr`. None when the (module, var) pair is not
    registered -- other arms own those, so the caller rejects instead of
    guessing."""
    mi = analyzer.registry.get_module(module_name)
    if mi is None or var_name not in mi.variables:
        return None
    vi = mi.variables[var_name]
    if vi.native_cpp_name is not None:
        return vi.native_cpp_name
    if vi.is_pointer:
        return f"(*{vi.cpp_expr})"
    return vi.cpp_expr

def _module_var_recv(recv: TpyExpr, locals_: dict[str, TpyType],
                     analyzer) -> bool:
    """A module-attr GLOBAL as a subscript/setitem/method receiver
    (`os.environ[...]` -- the cross-module twin of `_global_record_recv`):
    the receiver renders through the module-variable arm (`(*slot)` deref
    / native symbol), so the element machinery composes over it exactly
    as over a local record name. Covers the bare `mod.X` form and the
    dotted `pkg.sub.X` form -- NB the dotted arm's LOWERING never gets
    allow_ref_pointer threaded, so a dotted non-value receiver gate-admits
    here and then rejects at field.module_var_type (the safe over-admit
    direction; thread the use through `_lower_module_var`'s dotted call site
    when a witness appears)."""
    if not isinstance(recv, TpyFieldAccess):
        return False
    if recv.module_var_access is not None:
        return _module_var_read_cpp(*recv.module_var_access,
                                    analyzer) is not None
    bm = _bare_module_recv(recv.obj, locals_, analyzer)
    return (bm is not None
            and _module_var_read_cpp(bm, recv.field, analyzer) is not None)


def _global_record_recv(obj: TpyExpr, declared: dict[str, TpyType],
                        analyzer) -> 'TpyType | None':
    """A SAME-module non-value F1-record global used as a field-access
    receiver (`time.x` off a top-level `time: Timer = Timer()`): the read
    renders the bare pointer-slot name with an arrow (`time->x`, an
    indirect name). Returns the record type, or None. Locals/params
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
    receiver requirement, so a global field write rejects."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _global_record_recv(e.obj, declared, analyzer) is not None)

def _class_const_pure_receiver(e: TpyFieldAccess, declared: dict[str, TpyType],
                               analyzer) -> bool:
    """The class-constant receiver shapes whose render is the BARE
    qualified name (no receiver eval): a name receiver (`C.X`, `c.X`,
    `self.X`) or a static-type chain
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
    the qualified-name half: @native rename, generic per-instantiation
    spelling (off the receiver's typed instantiation via `render_type`,
    codegen's type_to_cpp), the
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
    the per-side post-generation casts)."""
    ie = e.int_enum_coercion
    if ie is not None:
        if _eligible_enum(ie, analyzer) is None:
            return False
        return all(_eligible_enum(t, analyzer) is not None
                   or _resolved_scalar(t, analyzer) for t in (lt, rt))
    el, er = _eligible_enum(lt, analyzer), _eligible_enum(rt, analyzer)
    return el is not None and er is not None and el == er

def _binop_operand_casts(e: TpyBinOp, analyzer) -> 'tuple[str | None, str | None]':
    """The per-side post-generation operand casts of the comparison render,
    as `{0}` wraps:

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
        # Per-side `get_resolved_type`: a bare
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
    """The truthiness render for an enum-typed operand, as a `{0}` wrap:
    an IntEnum tests its underlying value
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

def _opt_record_dunder(t: TpyType | None, analyzer) -> bool:
    """A pointer-repr `Optional[user record]` whose inner carries
    `__bool__`/`__len__` -- the type half of the un-narrowed truthiness
    dispatch. Builtin containers fail `is_user_record` there, so they keep
    the bare non-null test (BUGS.md's un-narrowed container entry)."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))) if t else None
    if not (isinstance(u, OptionalType) and u.uses_pointer_repr()):
        return False
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(u.inner)))
    if not (isinstance(inner, NominalType) and inner.is_user_record):
        return False
    rec = analyzer.registry.get_record_for_type(inner)
    return bool(rec and (rec.get_method_overloads("__bool__")
                         or rec.get_method_overloads("__len__")))

def _storage_form_opt_source(e: TpyExpr, analyzer,
                             storage_opt_locals: set[str]) -> bool:
    """`is_storage_form_optional_source` over the shapes truthiness sees."""
    if isinstance(e, (TpyFieldAccess, TpySubscript)):
        return reads_storage_form_optional(analyzer, e)
    return isinstance(e, TpyName) and e.name in storage_opt_locals

def _ptr_truthy_source(e: TpyExpr, t: TpyType | None, analyzer,
                       storage_opt_locals: set[str]) -> bool:
    """The raw-`T*` half of that dispatch: `::tpy::ptr_truthy(x)` -- the null
    check AND the dunder in one evaluation. A storage-form source is
    `std::optional<T>`, which has no pointer dispatch, so it is excluded
    here."""
    return (_opt_record_dunder(t, analyzer)
            and not _storage_form_opt_source(e, analyzer, storage_opt_locals))

def _storage_opt_record_truthy(e: TpyExpr, t: TpyType | None, analyzer,
                               storage_opt_locals: set[str]) -> bool:
    """The storage-form complement: `std::optional<T>` reads truthy through
    its own bool conversion, so no truthiness wrap applies and the
    read renders BARE (`if (h.f)` / `if (__getitem__(xs, 0))`). That skips
    the inner's dunder -- a filed divergence, not a render accident."""
    return (_opt_record_dunder(t, analyzer)
            and _storage_form_opt_source(e, analyzer, storage_opt_locals))

def _native_cond_scalar(t: TpyType | None, analyzer) -> bool:
    """Whether a value of this type IS its own boolean test.

    C++'s contextual conversion makes the ordinary value render a valid
    condition for these types, so the truthiness render needs no wrap. What
    decides it is the operand's TYPE alone, never its syntactic shape -- a
    subscript read, a call result, a field read and a bare name of the same
    type all render the same test. `_truthiness_mode` reads this predicate
    for its own bare-render arm so the two cannot drift apart.
    """
    if t is None:
        return False
    resolved = resolve_pending_container(t, analyzer) or _resolve_pending_view(
        t, analyzer) or t
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(resolved)))
    # Primitives are checked before any record lookup: bool's builtin record
    # carries __bool__, but still renders bare here.
    return (is_bool_type(u) or is_fixed_int_type(u) or is_big_int_type(u)
            or is_float_type(u) or is_char_type(u)
            or isinstance(u, IntLiteralType))

def _truthiness_mode(t: TpyType | None, analyzer) -> TruthinessMode | None:
    """The non-identity truthiness arms.

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
    if _native_cond_scalar(u, analyzer):
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
    render: `c.value` -> `static_cast<U>({0})`
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
    """IntEnum unary negation `-p` -> `(-static_cast<U>({0}))` (sema leaves
    resolved_unaryop None there). None otherwise."""
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
    literal (a CHAR compare target renders it as `'x'`). A multi-char literal
    never renders as a char literal and rejects. The str-pair arm is checked
    first, so a
    literal-vs-literal compare stays a plain string compare (no char target
    arises without a Char-typed operand)."""
    if isinstance(e, TpyStrLiteral):
        return len(e.value) == 1
    return _eligible_char(t)

def _span_value(t: TpyType | None) -> bool:
    """A value-view `Span[scalar]` / `Span[readonly[scalar]]` value
    (`std::span<T>` / `std::span<const T>`). A span is a value type -- reads,
    copies, decls and returns all render bare (no owning wrap, no per-element
    convert). Only SOURCES whose spelling already matches route: a bare span
    name / call result / storage-form field read whose
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
    if not args:
        return False
    return _eligible_scalar(_peel_readonly(args[0]))

def _span_open_t_value(t: TpyType | None) -> bool:
    """A `Span[T]` / `Span[readonly[T]]` over a bare TYPE PARAM -- the open
    sibling of `_span_value`. The spelling resolves per-instantiation and a
    span is a value view whose render is bare wherever it lands, so the
    element family (which `_span_value` pins for the concrete case) only
    constrains sources that could carry a per-element convert; none exists
    for an open element."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType) or not is_span(u):
        return False
    args = getattr(u, "type_args", None)
    return bool(args) and _is_type_param_slot(unwrap_readonly(args[0]))

def _span_return(t: TpyType | None) -> bool:
    """The span RETURN slot -- see `_span_value` (the return is one of its
    bare-render positions). A `Span[T]` / `Span[readonly[T]]` over a TYPE
    PARAM admits too (the auto_readonly getter pair): the slot renders
    per-instantiation and the witnessed sources are the element-blind
    spanlike coerces (`as_span` / `as_mut_span`)."""
    return _span_value(t) or _span_open_t_value(t)

def _f1_record_type_arg_ok(a: 'TpyType | int', analyzer) -> bool:
    """A generic user-record type-arg whose lowered spelling equals the
    resolver's. The resolver recurses args via `type_to_cpp`, lowering via each
    arg's own `to_cpp()`; the two coincide on this slice -- a raw INT type param, an
    eligible value scalar, a (recursively) F1-renderable record, and the arms
    below: str/container/None, spelling-equal enums, element-wise-admitted
    tuples, and alias-registered unions. The genuinely divergent leftovers
    reject the outer generic: PendingView args (`to_cpp()` raises),
    LOCAL plain union aliases (registered mid-emission AFTER lowering), and
    resolver-only-qualified @dynamic-protocol / enum spellings. A
    `TypeParamRef` arg (`Pair[T]` -- the generic record's OWN definition
    context) renders `Pair<T>` under both spellings (each names the bare param
    `T`); admitting it opens the sig/ctor gate for the record's templated
    bodies, whose T-typed slots are gated separately."""
    if isinstance(a, int):
        return True
    if _is_type_param_slot(a):
        return True
    if isinstance(a, TpyType):
        # A CONCRETE str-family arg (`Box[str]` / `Box[StrView]`): both
        # spellings give the storage form (`std::string` / `std::string_view`) -- the
        # view/owned param split never applies inside a type-arg list.
        u = unwrap_readonly(a)
        if isinstance(u, NominalType) and (is_str_type(u)
                                           or is_str_view_type(u)):
            return True
        # A CONCRETE `bytes` arg (`Pair[bytes]` / `Poll[bytes]`): both
        # spellings give the OWNED storage form `std::vector<uint8_t>`, the str arm's
        # argument one family over -- the view/owned param split never
        # applies inside a type-arg list. `BytesView` spells the view form
        # and is admitted by no shape here, so it keeps rejecting; the
        # `bytearray` sibling shares the spelling but is a reference type,
        # whose borrow form is a separate question.
        if isinstance(u, NominalType) and is_bytes_type(u):
            return True
        # A builtin-container arg (`Box[list[Int32]]`): both spellings give
        # the formatter form (`std::vector<...>`) recursing element args through
        # the same slice; union/enum/tuple elements keep their divergent
        # spellings out via the recursion.
        if _f1_container_ref(u):
            return bool(u.type_args) and all(
                _f1_record_type_arg_ok(ea, analyzer) for ea in u.type_args)
        # `Rc[None]` / `Box[None]`: the unit arg spells `std::monostate` on
        # both spellings; an unresolved float literal arg (`Rc.new(3.14)`)
        # resolves to the default double under both.
        if isinstance(u, (NoneType, FloatLiteralType)):
            return True
        # Enum arg (`Box[Color]`): the resolver spells via `enum_cpp_name`,
        # THIR via `to_cpp()`'s native-name lookup; both maps are populated
        # before lowering, so admit exactly the measured-equal slice. A bare
        # local spelling falls through the resolver to `to_cpp()` itself.
        if isinstance(u, NominalType) and is_enum_type(u):
            spelled = enum_cpp_name(u, analyzer.ctx.module_name)
            return spelled == u.name or spelled == u.to_cpp()
        # Tuple arg (`Box[tuple[Int32, Point]]`): both spellings give
        # `std::tuple<...>` recursing elements, so the spellings coincide
        # iff every element is itself in the spelling-equal slice (a
        # PendingView element rejects here BEFORE anything can call its
        # raising `to_cpp()`).
        if isinstance(u, TupleType):
            return all(_f1_record_type_arg_ok(e, analyzer)
                       for e in u.element_types)
        # Union arg: both spellings read the SAME `union_alias_names` map, so
        # the spellings coincide whenever an alias is ALREADY registered at
        # lowering time (recursive / cross-module aliases). A module-LOCAL
        # plain alias registers mid-emission AFTER lowering, so its members
        # miss the map here and the arg keeps rejecting -- admitting it
        # would pre-spell `std::variant<...>` where the resolver spells the
        # alias name.
        if isinstance(u, UnionType):
            compiler = get_current_compiler()
            return (compiler is not None
                    and compiler.union_alias_names.get(u.members) is not None)
        # A generic recursive-alias INSTANCE arg (`Box[Tree[int]]` ->
        # `Box<Tree<::tpy::BigInt>>`): both spellings go through the same
        # per-compilation recursive_alias_cpp_names map (`to_cpp`'s render
        # key; the resolver has no dedicated arm and falls through to the
        # same `to_cpp`), so the spellings coincide whenever the
        # instantiation args are themselves in the slice.
        if isinstance(u, RecursiveAliasInstanceType):
            return all(_f1_record_type_arg_ok(ea, analyzer)
                       for ea in u.type_args)
        # A Pending view arg (`Box(s)` on a str local -> `Box[PendingStr]`):
        # sema's usage resolution is FINAL pre-lowering, so resolve through
        # the same per-analyzer view_vars the resolver reads and recurse.
        # The RAW type's `to_cpp()` raises -- callers that admit through
        # this arm must spell via the resolver (`lc.render_type`), never
        # the bare `to_cpp()`.
        if isinstance(u, PendingViewType):
            rv = _resolve_pending_view(u, analyzer)
            return rv is not None and _f1_record_type_arg_ok(rv, analyzer)
    return (_eligible_scalar(a) or _eligible_char(a)
            or _f1_record(a, analyzer)
            or _f1_dyn_protocol_type_arg(a, analyzer))

def _f1_dyn_protocol_type_arg(a: 'TpyType | int', analyzer) -> bool:
    """A `@dynamic` protocol type-arg (`Box[Conn]` / `Rc[Conn]`) that THIR
    spells exactly as the resolver does. The resolver spells the arg via
    `dynamic_base_name`; THIR via its bare `to_cpp()`. They coincide only for a
    same-module, non-`@native` (no `cpp_concept`), non-shadowed protocol -- a
    cross-module / shadowed / native protocol qualifies on the resolver side
    only, so it rejects the outer generic."""
    if not isinstance(a, TpyType):
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
    if not (isinstance(u, NominalType) and is_dyn_protocol(u)):
        return False
    return dynamic_base_name(u, analyzer) == u.to_cpp()

def _method_rvalue_f1_record(init: 'TpyExpr | None', analyzer) -> bool:
    """An F1-record rvalue produced by a METHOD call (`a.clone()`): the
    owned-decl / REBIND_SLOT / rebind-reseat gates share this disjunct, so
    the three admissions cannot drift; the method-call lowering's own gates
    validate callee/args downstream."""
    return (isinstance(init, TpyMethodCall)
            and _f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init))

def _binding_peel(t: TpyType) -> TpyType:
    """The receiver-BINDING peel shared by the record-slice gate and the
    declared-field lookup: strip the transparent wrappers, then `Own` -- an
    `Own[T]` binding is by-value storage whose members read exactly like any
    other binding's. ONE definition, because a receiver gate and a field-type
    lookup that peel the same binding differently disagree about which
    bindings have fields, and the gated consumers then reject silently."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return t.wrapped if isinstance(t, OwnType) else t


def _f1_record(t: TpyType | None, analyzer) -> bool:
    """The spelling-equal record slice: any concrete user record whose
    `TpyType.to_cpp()` == `TypeResolver.type_to_cpp()` and whose field /
    method names lowering reproduces. Record kinds that satisfy that:

    - same-module records -- no qualification / rename at all;
    - `@native` records -- type spelled via `Compiler.native_cpp_names` (the same
      map the resolver reads), field renames via sema's `native_field_name`
      (stamped into THIR field access by `_field_cpp`), methods via
      `fi.native_name` (already honored); the module axis is irrelevant
      (native_cpp_names qualifies a cross-module native record too);
    - cross-module non-native records -- `native_cpp_names` qualifies them by
      qname exactly as the resolver's `imported_record_qualification_for_type`
      does;
    - GENERIC records with concrete args (`Pair[int]`) -- the base name resolves
      as above and `to_cpp()`'s type-arg recursion agrees with the resolver iff
      every arg is itself in the spelling-equal slice (`_f1_record_type_arg_ok`:
      scalar / F1-record / INT). That is the type-spelling half only; the
      generic record's OWN templated bodies (TypeParamRef args) are gated
      separately;
    - `@builtin_type` records WITH real bodies of RECORD category (Poll,
      Waker): a RECORD-category TypeDef renders as a plain nominal type on
      both spellings -- either through `{name}<{args}>` + `native_cpp_names`
      like a user record, or through a `cpp_formatter` the resolver falls
      straight through to, since its non-formatter arms all key on a shape
      a RECORD category does not have. `builtin_type_key` keys TypeDef
      payload dispatch, never body-position rendering. The categories that
      DO carry a divergent C++ shape (`list` -> `std::vector`, `dict_keys`
      -> `::tpy::dict_keys_view`) are excluded by their category, not by
      name and not by whether a formatter happens to be attached."""
    if t is None:
        return False
    t = _binding_peel(t)
    if not isinstance(t, NominalType):
        return False
    is_builtin_record = False
    if not t.is_user_record:
        td = type_def_of(t)
        if (td is None or td.record is None
                or td.category is not TypeCategory.RECORD
                or td.is_compile_time_only):
            return False
        is_builtin_record = True
    if t.type_args and not all(
            _f1_record_type_arg_ok(a, analyzer) for a in t.type_args):
        return False
    if analyzer.registry.get_record_for_type(t) is None:
        return False
    return not is_builtin_record or _witness("recv.builtin_record")


def _f1_container_ref(t: TpyType | None) -> bool:
    """The NON-record half of the reference axis: a reference-form builtin
    that carries a `cpp_formatter` -- `list`, `dict`, `set`, `bytearray`,
    `Array`. Membership comes from the TypeDef facts, not from a category
    enumeration.

    Two things it deliberately does NOT do. It does not peel
    (`_plain_container_read` excludes `Own` at its own gate), and it does not
    check type-arg spelling: the resolver falls
    straight through a `cpp_formatter`, so a container slot's C++ comes from
    the signature rather than from a lowered type-arg. Imposing `_f1_record`'s
    type-arg check here rejects code that compiles today -- measured on
    `Own[list[Int32 | str]]` returns and on a `list[Int32 | None]` global,
    where the union's alias registers only after lowering. The RECORD category
    is excluded even when a formatter is attached (`tpy.coro.Waker`), so the
    record and container halves stay disjoint -- the return ladder runs its
    record arms first, and a type in both halves would silently take the
    record render."""
    if not isinstance(t, NominalType):
        return False
    td = type_def_of(t)
    return (td is not None and td.cpp_formatter is not None
            and td.category is not TypeCategory.RECORD
            and not td.is_compile_time_only
            and t.value_form() is ValueForm.BORROW_REF)


def _bytes_family_ref(t: 'TpyType | None') -> bool:
    """The bytes family's REFERENCE-typed member -- `bytearray`, named by two
    facts rather than by qname: it is on the reference axis
    (`_f1_container_ref`) and it is in the bytes category. `bytes` and
    `BytesView` are value types, so the intersection cannot widen past the
    mutable buffer. It is the one bytes-family type whose methods render like
    a container's (member renames / free natives over a bare receiver) rather
    than like a view's."""
    return bool(t is not None and _f1_container_ref(t)
                and is_any_bytes_type(t))


def _const_default_param_form(t: 'TpyType | None') -> bool:
    """A type whose PARAM slot is const by DEFAULT, with a separate mutable
    spelling on the TypeDef (`param_cpp_formatter` + `param_mut_cpp_formatter`
    -- `const std::vector<uint8_t>&` / `std::vector<uint8_t>&`). The render
    that follows: an RVALUE binds such a slot in place, because a `const T&`
    parameter extends a temporary where a plain `T&` cannot bind one at all.
    Read off the registry rather than spelled per site, so a second type
    adopting the pair gets the same admission."""
    td = type_def_of(t) if isinstance(t, NominalType) else None
    return bool(td is not None and td.param_cpp_formatter is not None
                and td.param_mut_cpp_formatter is not None)


def _f1_ref(t: TpyType | None, analyzer) -> bool:
    """The REFERENCE axis: the spelling-equal record slice OR its container
    half -- user records, the RECORD-category builtins, and the builtin
    containers, which are the same thing (a `@native` stub class with a
    `record` payload) distinguished only by carrying a `cpp_formatter`.

    A strict SUPERSET of `_f1_record` by construction, which is what a shared
    gate has to be: merging a record leg and a container leg must not lose the
    value-form RECORD builtins the record leg admits."""
    if not isinstance(t, TpyType):
        return False
    return _f1_record(t, analyzer) or _f1_container_ref(_binding_peel(t))


def _record_class_binding(t: 'TpyType | None') -> bool:
    """Whether a pointer-bound name's pointee renders as a RECORD -- the one
    class whose bare-name reads stay bare at value positions (field/method
    consumers spell `->` themselves). The record-ness legs of `_f1_record`
    WITHOUT its spelling constraints (type args / qualification bound
    RENDERING, not arrow-ness). A formatter-carrying builtin
    (`list` -> `std::vector`) is NOT a record: its pointer binding (an F2d
    rebound container) derefs at value positions like any other pointer.

    The formatter check is NARROWER than `_f1_record`'s category check, and
    only reachability makes that safe: a name reaches `lc.pointers` through
    the non-value-type binding forms, so a formatter-carrying RECORD-category
    builtin can only arrive here once one exists that is not a ValueType.
    Widen this to the category question at the same time as admitting one."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = t.wrapped
    if not isinstance(t, NominalType) or is_protocol_type(t):
        return False
    if t.is_user_record:
        return True
    td = type_def_of(t)
    return (td is not None and td.record is not None
            and td.cpp_formatter is None and not td.is_compile_time_only)


def _protocol_subscript_recv(recv: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """A bare protocol-typed NAME receiver whose `__getitem__` resolves to
    the shared checked-dunder protocol template (the dunder fallback
    `::tpy::__getitem__({self}, {0})`) -- the Sequence-family
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


def _nullable_protocol_slot(ptype: 'TpyType | None') -> 'list | None':
    """The NULLABLE all-protocols slot's member list -- an
    Optional[protocol] normalization or a union with a None member whose
    other members are all protocols -- or None. The protocol-arg render
    splits on has_none, NOT on the call kind: a REQUIRED protocol union
    monomorphizes to one template param and takes the plain deref
    render; only the nullable form lifts."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if isinstance(slot, OptionalType):
        members = [unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            slot.inner)))]
    elif isinstance(slot, UnionType):
        members = [m for m in slot.members if not is_void_like_type(m)]
        if len(members) == len(slot.members):
            return None
    else:
        return None
    if not members or not all(
            isinstance(m, NominalType) and m.is_protocol for m in members):
        return None
    return members


def _protocol_union_arg(arg: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType],
                             analyzer) -> 'str | None':
    """A NAME into a call slot (free / ctor / method) whose non-None members
    are all PROTOCOLS:
    an F1-record name, a Span name, and a builtin-container name all take
    the address-of lift (`&(a)` / `&(s)` / `&(words)` -- the C++ ctor's
    protocol overload binds the pointer; a bare record render is wrong
    here, and the corpus's seeming bare witness is `copy(a)`'s
    copy-construct, a different construct; the verdict serves free-call and
    method slots too -- nullability is what keys it, and only nullable slots
    reach the lifts). A None LITERAL takes the typed-null spelling
    (`static_cast<std::nullptr_t*>(nullptr)` -- the 'nullproto' verdict; a
    STATIC-protocols-only slot, the has_none spelling on the shapes it can
    reach).
    Returns 'addr' / 'nullproto' or None."""
    if isinstance(arg, TpyNoneLiteral):
        members = _nullable_protocol_slot(ptype) or []
        if members and all(isinstance(m, NominalType)
                           and not is_dyn_protocol(m) for m in members):
            return "nullproto"
        return None
    if not isinstance(arg, TpyName) or arg.name not in locals_:
        return None
    if _nullable_protocol_slot(ptype) is None:
        return None
    # An `Own[container]` PARAM (`items: Own[list[T]]`, a `vector<T>&&`
    # slot) is the same C++ lvalue inside the body -- the render asks
    # the Own-stripped expr type, so the addr lift covers it identically
    # (`_items(ArrayList<T, 8>(&(items)))`).
    at = _unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        locals_[arg.name]))))
    # A STRUCTURAL conformer of a @dynamic member is NOT Base-derived in
    # C++, so `&(name)` cannot bind the base pointer -- that name takes
    # the RefAdapter temp face (`_optional_ptr_arg_face`'s adapter_name).
    members = _nullable_protocol_slot(ptype) or []
    if any(isinstance(m, NominalType) and is_dyn_protocol(m)
           and not (isinstance(at, NominalType) and at.is_user_record
                    and record_inherits_dynamic(at, m, analyzer.registry))
           for m in members):
        return None
    if _f1_ref(at, analyzer) or is_span(at):
        return "addr"
    return None


def _nullable_static_protocol_param(t: 'TpyType | None') -> 'NominalType | None':
    """The STRUCTURAL protocol of a nullable static-protocol param
    (`items: Sized | None`), or None. The C++ binding is the monomorphized
    `const T_x*` (default `std::nullptr_t`), so the param joins the
    pointer set: narrowed reads deref `(*x)` and the None test swaps to
    the `std::same_as<T_x, std::nullptr_t>` constexpr guard. @dynamic
    inners stay with the wide accessor's dyn class (nullable `Base*`).
    Covers both the Optional spelling and the nullable all-static
    protocol UNION (2+ protocols + None, mirroring is_protocol_union's
    arity; a 1-protocol union normalizes to Optional upstream)."""
    if not isinstance(t, TpyType):
        return None
    u = _unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))
    if isinstance(u, OptionalType):
        inner = unwrap_readonly(u.inner)
        if (isinstance(inner, NominalType) and is_protocol_type(inner)
                and not is_dyn_protocol(inner)):
            return inner
        return None
    # The nullable protocol-UNION flavor (`Sized | Sequence[T] | None`):
    # the same monomorphized `const T_x*` binding, one template param
    # bounded by the disjunction.
    if isinstance(u, UnionType):
        others = [unwrap_readonly(m) for m in u.members
                  if not is_void_like_type(m)]
        if (len(others) >= 2 and len(others) < len(u.members)
                and all(isinstance(m, NominalType) and is_protocol_type(m)
                        and not is_dyn_protocol(m) for m in others)):
            return others[0]
    return None


def _static_protocol_union_binding(t: 'TpyType | None') -> bool:
    """A guard-retyped protocols-only union binding (the None member
    narrowed away): every member a STRUCTURAL protocol. The shared verdict
    behind the name-read deref and the required-union pass-onward."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if isinstance(t, TpyType) else None)
    return (isinstance(u, UnionType) and bool(u.members)
            and all(is_protocol_type(unwrap_readonly(m))
                    for m in u.members))


def _protocol_binding(t: 'TpyType | None') -> 'NominalType | None':
    """The protocol a bare protocol-typed binding names, or None.

    Both flavors bind as a C++ REFERENCE -- a structural protocol param is the
    template `T_p&` / `const T_p&`, a @dynamic one the abstract `Base&` -- so a
    bare name reads bare and a method call takes the `.` accessor, exactly like
    an F1-record binding. `Own[P]` is deliberately NOT unwrapped: it lowers to
    `std::unique_ptr<P>` (structural: a `T_p&&` forwarding ref), whose method
    calls render `->` and whose reads move.

    A `Send[P]` param binds `Ref[Send[P]]` -- the marker is NOT canonically
    outermost -- so the wrappers peel to fixpoint (Send is erased wherever
    it sits, leaving the bare protocol binding).
    """
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    u = unwrap_readonly(unwrap_send_sync(u))
    return u if is_protocol_type(u) else None

def _bounded_tparam_protocol(t: 'TpyType | None',
                             bounds: 'dict | None') -> 'NominalType | None':
    """The protocol BOUND of a TYPE-kind type-param binding (`item: T` under
    `[T: Stringable]`), or None. Inside the template such a receiver behaves
    exactly like a structural-protocol binding: the user-record guard skips
    a TypeParamRef, so the call renders the bare member over the free
    `_args()` loop -- the same emit `_protocol_method_call_supported`
    admits. Sema does not stamp bounds on expression-type TypeParamRefs,
    so `bounds` is the in-scope name->bound dict (`lc.tparam_bounds`); a
    stamped `t.bound` wins.
    A marker-only bound (Send/Sync) IS a protocol and resolves here, but
    carries no methods -- sema rejects any method call against it, so no
    valid body reaches the method checker through one."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        # An `Own[T]` / `Send[Own[T]]` param binds by value
        # (own_param_t<T>) and dispatches the bound's members with the
        # same bare `.` call as the plain-T binding.
        u = unwrap_readonly(unwrap_send_sync(u.wrapped))
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
    or single required structural (which the protocol pre-arm hands straight
    back to the ordinary call-argument render). Rejects `Own[P]`
    (`::tpy::make_adapter<Base>` / `std::make_unique`) and the `Optional[P]`
    / protocol-union slots, whose renders (typed null, `&(...)` address-of,
    `optional_to_ptr`) are not lowered here.

    An `Own[...]` TYPE ARG (`Iterable[Own[T]]`, a `T_p&&` forwarding-ref
    slot) is a structural slot like any other: the call-argument render --
    not a protocol pre-arm -- rewrites a movable last-use arg into the consuming
    `::tpy::own_iter(std::move(x))`, and `_lower_call_arg`'s Iterable arm
    carries that wrap position-blind; every other arg takes the same
    bare/temp verdicts as a plain structural slot.
    """
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(ptype))
    # A `Send[P]` slot arrives as `Ref[Send[P]]` -- the marker is not
    # canonically outermost -- so peel the Ref and the marker again (Send
    # is erased wherever it sits).
    u = unwrap_readonly(unwrap_send_sync(unwrap_ref_type(u)))
    if isinstance(u, OwnType) or not is_protocol_type(u):
        return None
    return u

def _protocol_arg_temp(proto: 'NominalType', arg_type: 'TpyType | None',
                       arg_cpp_type: str, analyzer, *,
                       rvalue: bool) -> 'tuple[str | None, bool] | None':
    """The `(cpp_type, brace_init)` of the `__tmp_N` a protocol slot hoists for
    this arg, or None when the arg passes bare.

    The dynamic-protocol arg rule plus the free-call `is_ref_param() +
    is_temporary_expr` temp arm. `arg_cpp_type` is the arg's rendered concrete
    C++ spelling (the caller renders it; the gate never needs the string):

    - the arg is already protocol-typed -> bare (the deref render forwards);
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
    """Coarse type-family tag for the reject-detail sub-classifiers
    (diagnostic labels only, never a gate/emit fact): one shared chain so
    the sig.param_type / call.arg_shape details name families consistently.
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
        # detail names them apart even though their BODY renders coincide.
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
    """The borrow-form REFERENCE return slot (`-> Box` -> C++ `Box&` /
    `const Box&`; `-> list[T]` -> `std::vector<T>&` the same way), or None.
    `Own[T]` is the storage (by-value) direction (`_record_storage_return`) --
    checked before `_f1_ref`'s own Own-unwrap can admit it.

    One slot for the whole reference axis: the return ladder's source arms
    render the same C++ for a record and for a container (bare name, field
    read, element lvalue, borrow-returning call), so a second container-only
    slot would only decide which of two identical renders fires."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return None
    if isinstance(t, RecursiveAliasInstanceType):
        # A bare generic-instance slot (`-> Tree[Int32]`) is the same
        # borrow direction: `Tree<int32_t>&` off owned storage, the field
        # read rendering `return this->t;` like any record member.
        return t
    if not isinstance(t, NominalType):
        return None
    return t if _f1_ref(t, analyzer) else None

def _eligible_ptr_value(t: 'TpyType | None', analyzer) -> bool:
    """A `Ptr[T]` value (`T*` by value -- copied around like a scalar),
    admitted at the value slots (param / return / field-read result) when the
    pointee spells identically to the resolver: an F1-record (native /
    cross-module included via native_cpp_names), an eligible scalar, Char,
    void (`Ptr[None]` -> `void*`), or a @dynamic-protocol base (`Ptr[
    _RcCellBase]` -> `_RcCellBase*`); `Ptr[readonly[T]]` -> `const T*` rides
    the same arms. The slice renders only bare passes and field reads -- a
    MEMBER access THROUGH the Ptr takes the `::tpy::deref_check(p)`
    non-null render (or the proven `->`), which the Ptr-receiver method-call
    arm carries; other deref shapes take their own arms.

    The dyn-protocol arm needs no per-flavor spelling check: a Ptr type is
    spelled through the one `PtrType.to_cpp()` (`type_to_cpp` has no PtrType
    arm and falls through to it, and `render_type` IS `type_to_cpp`), so the
    pointee reads the same `NominalType.to_cpp()` -- bare local name,
    cross-module and
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
    # param name (`T*` -- the _f1_record_type_arg_ok rule), so
    # the value slice is pointee-blind here like everywhere else.
    return ((is_void_like_type(inner) or _eligible_scalar(inner)
             or _eligible_char(inner) or _is_type_param_slot(inner)
             or _f1_record(inner, analyzer))
            and _witness("ptr.value_slot"))

def _dyn_proto_ptr(t: 'TpyType | None') -> bool:
    """A `Ptr[T]` whose pointee is a @dynamic protocol. The one Ptr-value
    flavor whose LOCAL DECL spells `auto` (the decl type's
    contains_protocol_type arm recurses through the pointee), unlike the
    record/scalar pointees' spelled `T*`; the None-init first decl rejects
    (see the decl gate)."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(t, PtrType) and is_dyn_protocol(t.inner_pointee)

def _chained_field_read_ok(e: TpyExpr, analyzer) -> bool:
    """A markers-clean field read whose receiver is itself a markers-clean
    F1-record field read (`self.inner.node` -- the inner hop renders bare,
    the outer member chains postfix). The predicates-side twin of checks'
    `_field_over_field_ok` (predicates cannot import checks)."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpyFieldAccess)
            and _field_markers_clean(e.obj)):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    return isinstance(ft, NominalType) and _f1_record(ft, analyzer)

def _subscript_field_recv_ok(e: TpyExpr, analyzer) -> bool:
    """A `.field` access whose receiver is a SUBSCRIPT (`pp[0][0].fd`,
    `h.pair[0].v`, `xs[0].v`, `make_pair(a, b)[1].v`): the field render emits
    the receiver and appends `.field`, so the row is the composition of the
    subscript's own render with the member tail -- the subscript arm gates its
    own shapes, and an unsupported one rejects there.

    Admitted only when the element resolves to an F1 record. A `Ptr` element
    takes `::tpy::deref_check(<recv>).f` (the sibling Ptr arm's wrap) and an
    Optional one its own check; neither is spelled here, and a blanket
    subscript admission was measured to DROP the deref_check on a
    `tuple[P | None, Ptr[Tag]]` element -- a missing null check, not merely a
    byte divergence.

    Marker-clean like every sibling subscript row. Four of the nine markers
    (property getter, dyn getattr, module var, class constant) early-return
    further up the field arm and could not reach here anyway, but
    `deref_depth` / `deref_narrowed_to` DO reach it, and the render tail
    drops the `__deref__()` hops for a non-NAME receiver -- measured, on a
    shape no corpus case reaches."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    if not isinstance(e.obj, TpySubscript):
        return False
    if e.needs_optional_runtime_check:
        return False
    t = analyzer.get_expr_type(e.obj)
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _f1_record(t, analyzer) and bool(_witness("field.subscript_recv"))


def _ptr_value_field_recv_ok(e: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """A `.field` access whose receiver evaluates to an explicit `Ptr[record]`
    VALUE -- a local/param NAME (`p.x` on `p: Ptr[Point]`) or an F1-record's
    `Ptr` FIELD read (`m._a.x` on `_a: Ptr[A]`): the field render's pointer
    arm emits `<recv>->x` when sema proved the pointer non-null
    (`ptr_non_null`) else `::tpy::deref_check(<recv>).x`. A NAME receiver must
    be bound to an eligible `Ptr[F1-record]` (globals take the `(*g)->`
    wrapper arm, out of slice); a FIELD receiver must itself be an admitted
    F1-record field read whose value is such a Ptr (it renders bare, then this
    arm wraps it). The pointee's F1-ness makes the outer `.field` spell. Read
    AND write target alike -- the render is position-independent, picked at
    lowering from `ptr_non_null`.

    `deref_depth` (sema's auto-deref marker) IS set on a Ptr member access and
    is EXPECTED here -- the `obj_type.is_pointer()` arm renders one `->`
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
        # A depth-two chain receiver (`self.inner.node.value`) admits like
        # the plain chained read: the inner hops render bare, this arm
        # wraps the Ptr-valued tail (`deref_check(this->inner.node).value`).
        if not (_field_receiver_ok(recv, declared, analyzer)
                or _chained_field_read_ok(recv, analyzer)
                # A Ptr-valued field read that is ITSELF taken through a
                # pointer (`seg.sector_front.floor_h`): the inner hop
                # renders its own `deref_check(seg).sector_front` and this
                # arm wraps that whole render, so the deref nests one level
                # per Ptr hop.
                or _ptr_value_field_recv_ok(recv, declared, analyzer)):
            return False
        rt = analyzer.get_expr_type(recv)
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A Ptr-RETURNING call receiver (`h.get_node().value` ->
        # `::tpy::deref_check(h.get_node()).value`): the call lowers
        # through its own arms (RECEIVER use); this arm wraps the Ptr
        # result exactly like a Ptr NAME's.
        rt = analyzer.get_expr_type(recv)
    elif isinstance(recv, TpySubscript):
        # A Ptr-ELEMENT tuple subscript receiver (`h.pair[1].name` ->
        # `::tpy::deref_check(std::get<1>(h.pair)).name`): the subscript's
        # own arm gates the tuple read (the Ptr-element value row); this
        # arm supplies the wrap the plain subscript-receiver row
        # deliberately does NOT spell (its docstring's measured
        # deref_check-drop hazard).
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
    (the field render's deref_chain arm). The receiver is a NAME bound to an
    F1 user record: a plain value binding spells the bare `.` chain; a
    pointer-local (a PROVEN narrowed-Optional local or an F2-reseated `T*`)
    spells the indirect `recv->__deref__().field` -- the lowering keys the
    first hop on the pointer set like the method twin. NOT
    isinstance-narrowed; the record must carry a `__deref__` overload.
    `deref_depth` is the auto-deref count (the chain length);
    `deref_narrowed_to` (a deref-view cast) rejects. Read AND
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


def _deref_wrapper_record_ok(u: 'TpyType | None', analyzer) -> bool:
    """`_f1_record` widened with SAME-MODULE @dynamic-protocol type-args
    (`Box[Pet]`) -- SCOPED to the user-Deref receiver resolution, where no
    wrapper type spelling is at stake (the call renders `.__deref__()`
    chains, never the template args). The GLOBAL F1 slice keeps its
    protocol-arg fence: admitting it corpus-wide pushes F1-False shapes off
    the arms that currently handle them."""
    if not isinstance(u, NominalType):
        return False
    if _f1_record(u, analyzer):
        return True
    if not u.is_user_record or not u.type_args:
        return False

    def arg_ok(a) -> bool:
        if _f1_record_type_arg_ok(a, analyzer):
            return True
        au = unwrap_readonly(a) if isinstance(a, TpyType) else None
        if isinstance(au, NominalType) and is_dyn_protocol(au):
            qn = au.qualified_name()
            mod = qn.rsplit(".", 1)[0] if "." in qn else None
            return au.to_cpp() == au.name and mod == analyzer.ctx.module_name
        return False

    return (all(arg_ok(a) for a in u.type_args)
            and analyzer.registry.get_record_for_type(u) is not None)


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
    the Optional's inner; a markers-clean FIELD read of one (or one behind a
    `Ptr[record]` hop, which carries an auto-deref marker of its own); or a
    container ELEMENT read of one. None outside the slice."""
    if isinstance(recv, TpyFieldAccess):
        # A markers-clean FIELD receiver (`self.val.speak()` off
        # `val: Optional[Box[Pet]]` proven non-None): the field lowering
        # renders its own storage-optional unwrap via narrowed_deref
        # (`(*this->val).__deref__()...`); an UNPROVEN read carries
        # needs_optional_runtime_check and fails the markers gate.
        if (_field_markers_clean(recv)
                and _field_receiver_ok(recv, declared, analyzer)):
            ft = _field_decl_type(recv, declared, analyzer)
        elif _ptr_value_field_recv_ok(recv, declared, analyzer):
            # A field read THROUGH a `Ptr[record]` binding (`hit.material`
            # on `hit: Ptr[Body]`): the field's own pointer arm renders the
            # `hit->material` hop and the `.__deref__()` chain composes
            # postfix off it, exactly as off a plain member read. The Ptr
            # hop is itself an auto-deref marker on the field node, which is
            # why the markers-clean leg above cannot reach this shape; the
            # declared map holds the POINTER, so the field's own type is
            # what carries the wrapper record.
            ft = analyzer.get_expr_type(recv)
            _witness("deref.ptr_field_recv")
        else:
            return None
        if ft is None:
            return None
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
        if isinstance(u, OwnType):
            u = unwrap_readonly(u.wrapped)
        if isinstance(u, OptionalType):
            u = unwrap_readonly(_unwrap_own(u.inner))
        if not _deref_wrapper_record_ok(u, analyzer):
            return None
        return u
    if isinstance(recv, TpySubscript):
        # A container ELEMENT receiver (`self._pool[k].close()` off
        # `dict[str, Box[Conn]]`): the element read lowers at RECEIVER and
        # spells its own checked read, then the `.__deref__()` hops compose
        # postfix off that lvalue. No pointer first hop can arise, so the
        # `->` join the NAME leg picks off the pointer set is unreachable
        # here. The element read's OWN arm gates its shape -- a slice
        # result, a record's own `__getitem__` -- so a receiver whose render
        # is not this bare composition rejects there rather than being
        # pre-screened here. An Optional element is not a wrapper
        # record, and an unproven one carries the null-check marker both
        # consumers already reject.
        et = analyzer.get_expr_type(recv)
        u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
             if et is not None else None)
        if isinstance(u, OwnType):
            u = unwrap_readonly(u.wrapped)
        return u if _deref_wrapper_record_ok(u, analyzer) else None
    if not isinstance(recv, TpyName) or recv.name not in declared:
        return None
    if recv.name in narrowed:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[recv.name])))
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if isinstance(u, OptionalType):
        if _own_opt_storage_binding(declared[recv.name]):
            # A VALUE-repr `Own[wrapper] | None` binding (`std::optional<Box>`
            # by value): its narrowed read already derefs in place (`(*name)`),
            # so the chain composes off that lvalue exactly like the storage-
            # optional FIELD receiver above. An UN-narrowed occurrence has no
            # payload lvalue to deref -- keep it out.
            rt = analyzer.get_expr_type(recv)
            if rt is None or isinstance(
                    unwrap_readonly(unwrap_ref_type(rt)), OptionalType):
                return None
            _witness("recv.deref_value_opt_name")
        elif recv.name not in pointers or not u.uses_pointer_repr():
            return None
        u = unwrap_readonly(_unwrap_own(u.inner))
    if not _deref_wrapper_record_ok(u, analyzer):
        return None
    return u

def _typed_dict_recv_ok(obj: TpyExpr, declared: dict[str, TpyType],
                        pointers: 'AbstractSet[str]',
                        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """The typed-dict subscript RECEIVER set, shared by the read arm and the
    write-target gate so the two cannot drift: a bare declared NAME (not a
    pointer-local / narrowed -- those read through their own unwraps), or a
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
    deref emit spells `recv.__deref__()...member(args)` exactly;
    function=True natives and cpp_template stubs thread the receiver
    through a symbol/template slot the deref branch cannot reach, so they
    reject."""
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
    (the method render's deref_chain arm; N = deref_depth). The method arm
    (plain member, no marker) takes the same receiver set as the field read's
    `_user_deref_field_recv_ok`: a NAME bound to an F1 user record with a
    `__deref__` overload -- a plain value binding spells the bare `.` chain,
    a pointer-local (proven narrowed-Optional / F2-reseated) the indirect
    `recv->__deref__()` first hop; isinstance-narrowed rejects. The
    called method's fi is a plain user method on the DEREFFED type; every
    special-emit marker (static/module/template/native/type-args/nested/
    optional-check) rejects."""
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
    """The storage-form REFERENCE return slot (`-> Own[Box]` -> C++ `Box` by
    value; `-> Own[list[T]]` -> a by-value `std::vector<T>` the same way), or
    None. Bare name sources return bare (`return b;` -- NRVO for an owned
    local, C++ implicit move for an `Own` rvalue-ref param; a borrowed source
    without copy() is a sema error, so no copy shape arises); an rvalue ctor /
    by-value call returns its bare expansion (`return Box(n);`), and the
    container-only source shapes (literals, comprehensions, the repeat build)
    render position-independently off the same slot.

    The whole reference axis, like its borrow twin: element families are
    bounded at the return ARM per source shape (a literal re-checks the decl
    gate's element slice; a bare name spells nothing), never here."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, OwnType):
        return None
    inner = unwrap_readonly(t.wrapped)
    if not isinstance(inner, NominalType):
        return None
    return inner if _f1_ref(inner, analyzer) else None

def _unwrap_own(t: TpyType) -> TpyType:
    """The payload of an `Own[T]` wrapper, else `t` unchanged -- the recurring unwrap
    the `Optional`-inner helpers apply before an `_f1_record` check."""
    return t.wrapped if isinstance(t, OwnType) else t

def _unown_type_args(t: TpyType) -> TpyType:
    """`t` with each generic type-arg's `Own[...]` peeled -- builtin-stub
    slots spell their element as `Own[V]` (the move-in marker) where the
    matching source type and the render both carry the bare `V`."""
    if not (isinstance(t, NominalType) and t.type_args):
        return t
    return replace(t, type_args=tuple(
        ta.wrapped if isinstance(ta, OwnType) else ta
        for ta in t.type_args))

def _res_container_return(t: TpyType | None, analyzer) -> 'TpyType | None':
    """The RESUMABLE container return slot (`Poll<std::vector<T>>` /
    `expected<std::vector<T>, StopIteration>`), or None. Wider than the sync
    `_record_storage_return` at the `Own` axis: a coroutine's return slot
    holds `T` by value whether or not the signature spells `Own`, so a bare
    `-> list[T]` (reachable when the value comes from an await, the only
    source sema admits without `Own`) shares the render. The value rides the
    position-blind tail -- the scaffolding's `{ret_cpp} __tpy_async_ret =
    <value>;` decl supplies the type, so names/calls/literals emit bare.

    That plain-`T` binding is also why a container return COPIES where CPython
    aliases (the reference-type divergence `_res_capture_ok`'s RETURN bullet
    records against BUGS.md) -- an existing divergence this arm does not
    change; when the fix routes returns through
    `val_or_ref_t<T>`, re-check THIS arm together with the capture arm and the
    return leaf, exactly as that bullet instructs."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return t if _f1_ref(t, analyzer) else None

def _plain_container_read(t: TpyType | None) -> bool:
    """A plain (non-`Own`) reference-form container read at a position where
    the bare read IS the whole render -- the for-head, whose begin()/end() are taken
    off it directly, and the `std::ranges::contains` haystack. `Own` is
    excluded: a consuming iteration moves the container
    (`own_iter(std::move(..))`), a different render."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return not isinstance(u, OwnType) and _f1_container_ref(u)

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
    a ptr-variant return (`::tpy::Union<monostate, A*, B*>` by value) or
    an `Own[union]` factory's storage variant -- no per-member conversion
    fires."""
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if not isinstance(t, UnionType):
        return False
    return (_eligible_value_union(t) is not None
            or _eligible_ptr_union_wide(t, analyzer) is not None)


def _opt_pointee_wide(inner: 'TpyType | None', analyzer) -> bool:
    """The WIDENED Optional-pointee class for the RETURN/DECL/COND-scoped
    rows: F1 records (the base slice), wrapper-union-likes, open type
    params (the force_pointer_repr `T | None` slots), @dynamic protocols,
    containers, and concrete scalars/Chars.

    The class exists because the renders that key on it are typically
    member-shape-blind -- `T*` spellings via render_type, `nullptr`
    compares, `optional_to_ptr` lifts, the bare pointer pass -- so one
    predicate serves the return facts, the decl/reseat rows, the None-test
    rows, the print OPT_PTR row (whose Formatter template args are a pure
    function of the pointee type), the param pointer seed and the
    deref-name arg row. The narrow `_optional_ptr_borrow` keeps the F1
    slice for the arg faces whose renders ARE pointee-shaped (the ctor
    temp's spelled type).

    "Typically", not "always", and the list above is illustrative, not the
    consumer set: this and `_optional_ptr_borrow_wide` are called from ~30
    sites across checks / statements / expressions / resumable / context.
    Three of them are NOT member-shape-blind, so do not widen the class on
    the strength of the blindness argument alone -- check them:
      * `resumable.py` re-narrows the wide verdict to list/dict/set inners
        for the OPT_PTR frame-local arm;
      * `resumable.py`'s narrowed-for-iterable arm uses it to select the
        pointer form, whose leaf carries a deref the value form does not;
      * one `statements.py` BRANCH_RVALUE reseat row uses it NEGATIVELY, to
        EXCLUDE a shape -- there a widening removes admission instead of
        adding it."""
    if inner is None:
        return False
    iu = unwrap_readonly(inner)
    return bool(
        _f1_record(iu, analyzer)
        or _wrapper_union_like(iu, analyzer) is not None
        or isinstance(iu, TypeParamRef)
        or (isinstance(iu, NominalType) and is_dyn_protocol(iu))
        or _f1_container_ref(iu)
        # A CONCRETE scalar/Char pointee (`dict_get(d, k)` -> `int32_t*`):
        # the same member-shape-blind renders -- `int32_t*` spellings,
        # nullptr compares, `(*v)` derefs -- with no member machinery at
        # all. The open-T row above already carried instantiated scalars;
        # this admits the concretely-typed twin.
        or _eligible_scalar(iu) or _eligible_char(iu))


def _optional_ptr_borrow_wide(t: TpyType | None,
                              analyzer) -> 'OptionalType | None':
    """`_optional_ptr_borrow` over `_opt_pointee_wide` -- see the scoping
    contract there. The narrow F1 accessor keeps every binding-level
    consumer."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    if isinstance(inner, OwnType):
        return None
    # A VALUE-scalar pointee is reachable only through force_pointer_repr
    # (`T | None` instantiated at Int32) -- the uses_pointer_repr guard
    # above keeps a normal value-repr `Int32 | None` out, so the scalar
    # class is safe HERE and only here (the storage flavor must leave
    # scalars to the value-opt facts). That admission now rides
    # `_opt_pointee_wide`'s own scalar row, over the same unwrapped inner:
    # the trailing `or` below is REDUNDANT, not a second class. Left in
    # place rather than removed as a drive-by; do not read it as a widening
    # this function performs on top of the accessor.
    return t if (_opt_pointee_wide(inner, analyzer)
                 or _eligible_scalar(inner)) else None


def _optional_ptr_borrow_wide_name(e: TpyExpr,
                                   locals_: dict[str, TpyType],
                                   analyzer) -> 'OptionalType | None':
    """`e` is a bare NAME whose DECLARED type is a wide-class ptr-repr
    Optional -- the `_optional_ptr_borrow_name` sibling over the wide
    accessor, shared by the None-test gate and the print rows."""
    if not (isinstance(e, TpyName) and e.name in locals_):
        return None
    return _optional_ptr_borrow_wide(locals_[e.name], analyzer)


def _storage_optional_return_wide(t: TpyType | None,
                                  analyzer) -> 'OptionalType | None':
    """The storage-form Optional return slot over the F1|wrapper pointee
    pair: the classic `Own[T] | None` nesting (value optional of the Own
    payload) and the REVERSE `Own[Optional[W]]` spelling wrapper returns
    use (`-> Own[Optional[Tree[Int32]]]` -> `std::optional<Tree<int32_t>>`
    -- ownership makes the whole optional a value at the boundary even
    though bare `Optional[W]` is ptr-repr). The pointee is on the reference
    axis: a container inner renders the same `std::optional<T>` slot a record
    inner does, with `render_type` spelling the payload either way. Scalar
    inners stay with the value-opt facts; tparam/dyn inners have no
    storage-opt witness and stay out."""
    def _sp(inner) -> bool:
        iu = unwrap_readonly(inner)
        return bool(_f1_ref(iu, analyzer)
                    or _wrapper_union_like(iu, analyzer) is not None)

    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(t))
    if isinstance(u, OwnType):
        ow = unwrap_readonly(u.wrapped)
        if isinstance(ow, OptionalType) and _sp(_unwrap_own(ow.inner)):
            return ow
        return None
    if not isinstance(u, OptionalType) or u.uses_pointer_repr():
        return None
    return u if _sp(_unwrap_own(u.inner)) else None


def _comp_shadow_pointers(pointers, declared, analyzer) -> frozenset:
    """The comprehension shadow-check pointer set: every pointer-local name
    EXCEPT the Optional-ptr-borrow bindings (their loop-var shadowing rules
    differ). One shared source for the ~7 comprehension call sites -- the
    hand-copied filter drifted once (an unfiltered set slipped through in a
    review round), so new call sites must use this."""
    return frozenset(n for n in pointers
                     if _optional_ptr_borrow(declared.get(n), analyzer)
                     is None)


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
    Keyed on the declared type, not the flow-narrowed expr type: the
    None-test and field/method dispatch key on the C++ binding shape, which
    narrowing does not change."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return None
    return _optional_ptr_borrow(declared[e.name], analyzer)

def generic_opt_trait_type(t: 'TpyType | None') -> 'OptionalType | None':
    """The generic `T | None` slot type (`::tpy::opt_param_t<T>` in C++), or
    None. Its form is decided per instantiation, so neither the pointer
    compare nor `has_value()` is spellable in the template -- reads of such a
    binding go through the runtime's form-neutral helpers."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OptionalType) and t.uses_generic_param_trait():
        return t
    return None


def generic_opt_trait_name(e: TpyExpr, declared: dict[str, TpyType]) -> bool:
    """`e` is a bare name DECLARED as a generic `T | None` slot."""
    return (isinstance(e, TpyName) and e.name in declared
            and generic_opt_trait_type(declared[e.name]) is not None)


def unit_opt_instantiation(t: 'TpyType | None') -> 'OptionalType | None':
    """`None | None` -- a generic `T | None` slot instantiated at `T = None`,
    or None. It cannot be written by hand (`None | None` IS `None`), so it
    reaches a slot only through that substitution. `std::optional<
    std::monostate>`: a value-repr Optional over the unit type, so its
    capture and its reads are the value form's, exactly like the fixed-int
    instantiation beside it."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OptionalType) and isinstance(t.inner, NoneType):
        return t
    return None


def _ptr_value_none_name(e: TpyExpr, declared: dict[str, TpyType],
                         analyzer) -> bool:
    """`e` is a bare name whose DECLARED type is an eligible `Ptr[T]` VALUE
    (a nullable raw pointer). Its `is [not] None` test renders the
    type-agnostic pointer compare `(p != nullptr)` / `(p == nullptr)` (the
    identity fallback -- a PtrType is neither Optional nor union), the
    non-null narrowing riding the deref sites, not this test."""
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
    `seed_param_locals` movable face, carried by the context.py movable
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

def _value_opt_span(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[Span[...]]` binding type (`values:
    Span[readonly[Int32]] | None` -> `std::optional<std::span<const T>>`
    by value), or None. The None test reads has_value over the bare
    binding, like the scalar/view/callable kinds. Only the None test is
    lowered: narrowed READS of the binding carry deref renders no arm
    claims, so they keep rejecting."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    return t if is_span(unwrap_readonly(t.inner)) else None


def _value_opt_tuple(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[value tuple]` binding type (`tup:
    tuple[int, Int32] | None` -> `std::optional<std::tuple<...>>` by value),
    or None. Scoped like the Span kind: only the WHOLE-optional read routes
    (the bare binding into a same-optional slot / the has_value None test);
    a narrowed read carries a deref render no arm claims, so name lowering
    keeps rejecting it."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    return t if _value_tuple(unwrap_readonly(t.inner),
                             analyzer) is not None else None


def _value_opt_value_record(t: 'TpyType | None',
                            analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[ValueType record]` binding type
    (`tz: Fixed | None` on a ValueType record -> `std::optional<Fixed>` by
    value), or None. The None test reads has_value over the bare binding
    and a narrowed member read derefs (`(*tz).off`) -- the registered
    RECORD-kind locals' renders, reachable as a plain param/local because
    the inner is a VALUE type (a non-value record optional is pointer-repr
    at these bindings instead)."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    # USER records only: builtin value nominals (Int32, str, ...) carry
    # record info too but belong to the scalar/view/callable rows.
    if not (isinstance(inner, NominalType) and inner.is_user_record
            and inner.is_value_type()):
        return None
    rec = analyzer.registry.get_record_for_type(inner)
    return t if rec is not None else None

def _value_opt_call_ret_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """A value-opt-returning CALL rvalue at the SAME value-opt slot
    (`unwrap_or(first_positive(xs), 0)`): the by-value `std::optional<T>`
    prvalue binds the slot bare -- no lift, no temp. Scalar/enum inners
    only (the `_value_opt_scalar` class); the view/own flavors keep their
    shims, and a borrow-returning callee stays out (its `&` result is not
    the by-value bind)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = _value_opt_scalar(ptype, analyzer)
    if pt is None:
        return False
    if call_returns_cpp_ref(analyzer, a.resolved_function_info):
        return False
    at = analyzer.get_expr_type(a)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if at is not None else None)
    return at_u == pt


def _opt_view_identity_coerce_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """The Optional view<->str identity coerce at a plain ARG slot
    (`takes_str_opt(returns_view_opt())` -- `str | None` and `StrView | None`
    both spell `std::optional<std::string_view>`, and the coercion lambda
    passes the expression through bare exactly in the non-Own ARG position).
    CALL rvalue inners only -- the witnessed shape; a NAME inner stays out
    until witnessed."""
    if not (isinstance(a, TpyCoerce)
            and a.coercion.name in ("optional_strview_to_str",
                                    "optional_str_to_strview")
            and a.context_kind == CoercionContext.ARG):
        return False
    if isinstance(a.expected_type, OwnType):
        return False
    return isinstance(a.expr, (TpyCall, TpyMethodCall))


def _value_opt_callable(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[Callable]` binding type -- a
    `Callable[...] | None` param bound `std::optional<std::function<...>>`
    by value, or None. Non-template callables only (`_callable_value`); an
    `Fn` template slot has no value binding. The routed reads are the WHOLE-
    optional ones (a bare pass into a matching value-opt slot); a NARROWED
    read (`cb(x)` under `cb is not None`) needs a deref no arm renders and
    is guarded per-use at name lowering."""
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
    (`a.settimeout(0.5)` into `float | None`): the arg renders bare -- the
    implicit `std::optional<T>` ctor absorbs the value, and the user-record
    method loop renders numeric literals target-less. The arg's own type
    must be non-Optional (a whole-optional pass is
    the binding row in _lower_call_arg); a `None` literal rides
    `_none_value_opt_arg`; a str literal is excluded (the Char-literal
    render keys on `_eligible_char(ptype)`, which an Optional slot fails)."""
    if _value_opt_scalar(ptype, analyzer) is None:
        return False
    # A scalar literal into a fixed-int/enum value-opt slot arrives wrapped in
    # a TpyCoerce to the Optional target (the implicit widening); the render
    # is the bare source (`Counter(10, 4)`), so key on the source type.
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
    no lift, no shim, no temp). `slot_check` picks the
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

def _whole_value_opt_field_arg(a: TpyExpr, ptype: 'TpyType | None',
                               locals_: dict[str, TpyType], analyzer) -> bool:
    """The FIELD twin of `_whole_value_opt_name_arg`: a member read whose
    DECLARED type is exactly the value-repr `Optional[scalar]` slot binds
    BARE (`create_connection(addr, this->timeout)`) -- the optional is a
    value passed by value, so nothing lifts, shims or hoists. A
    sema-NARROWED read is excluded by the exact-type pin: its analyzed type
    is the payload, and the render derefs."""
    if not isinstance(a, TpyFieldAccess):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if _value_opt_scalar(slot, analyzer) is None:
        return False
    if not (_field_markers_clean(a)
            and _field_receiver_ok(a, locals_, analyzer)):
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if isinstance(at, TpyType) else None)
    if at != slot:
        return False
    fdt = _field_decl_type(a, locals_, analyzer)
    fdt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))
           if isinstance(fdt, TpyType) else None)
    return fdt == slot and bool(_witness("arg.value_opt_field"))

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

def _value_opt_tuple_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType],
                              narrowed: 'AbstractSet[str]',
                              analyzer) -> bool:
    """The value-TUPLE-inner row (`s.request(.., auth)` at an `auth:
    tuple[str, str] | None` param): a value tuple is a value type, so the
    whole optional passes bare like the scalar and callable inners."""
    return bool(_whole_value_opt_name_arg(a, ptype, locals_, narrowed,
                                          analyzer, _value_opt_tuple)
                and _witness("arg.value_opt_tuple"))

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
    split never fires and the whole optional passes BARE -- unlike
    the free-call position, whose slot threading takes the shim
    (`_opt_view_arg_shim`). The free ladder keeps its shim rows, and the
    stub loops DO thread the raw param (see the shim arm's exclusion note in
    `_lower_call_arg`), so the render arm also keys off `method_arg_stub`."""
    return _whole_value_opt_name_arg(a, ptype, locals_, narrowed, analyzer,
                                     _value_opt_view)

def _str_literal_value_opt_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A str LITERAL into a value-repr `Optional[str]` slot
    (`Info("Alice")` into `str | None` -- the total=False TypedDict ctor
    face): the bare literal renders; C++'s implicit
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

def _bytes_literal_value_opt_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """The bytes twin of `_str_literal_value_opt_arg`: a bytes LITERAL into a
    value-repr `Optional[bytes]` slot renders bare with the OWNED spelling
    (`Bag(::tpy::bytes_literal_owned("hi", 2))`) -- the target threaded into
    the literal is the Optional, not view-typed, so the literal keeps its
    default owned render and the optional's converting ctor absorbs it."""
    if not isinstance(a, TpyBytesLiteral):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if not (isinstance(pt, OptionalType) and not pt.uses_pointer_repr()):
        return False
    inner = unwrap_readonly(pt.inner)
    return isinstance(inner, NominalType) and is_bytes_type(inner)

def _tuple_literal_value_opt_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 analyzer) -> bool:
    """A tuple LITERAL into a value-repr `Optional[value tuple]` slot: the
    spelled brace-init (`Bag(std::tuple<::tpy::BigInt, int32_t>{10, 20})`)
    converts into the optional in place -- the same spelled render the decl
    and return sinks give a value-tuple literal, with no storage lift (borrow
    and storage coincide for a value tuple)."""
    if not isinstance(a, TpyTupleLiteral):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if not (isinstance(pt, OptionalType) and not pt.uses_pointer_repr()):
        return False
    vt = _value_tuple(unwrap_readonly(pt.inner), analyzer)
    return vt is not None and len(a.elements) == len(vt.element_types)

def _unrouted_binding_read(t: 'TpyType | None', analyzer, *,
                           is_param: bool = False,
                           movable_local: bool = False) -> 'str | None':
    """A declared binding kind whose bare NAME read has no THIR arm -- reachable
    through function and constructor parameters (no local-decl arm produces such
    a binding), and through the Own-typed loop var (`movable_local`: the name
    sits in the working movable set). Actual uses decide whether the body
    routes. Returns the reject detail, or None for every binding the slice
    routes today:

    - a VALUE-repr Optional whose inner the scalar slice does not admit (a
      str/bytes view -- the `optional<string_view>`/`optional<string>` ARG
      split): a narrowed read needs the `(*p)` deref and the view->owned
      copy, which no arm renders (the eligible scalar / BigInt inners route
      via `_value_opt_scalar`);
    - an `Own[...]` whose payload has no routed read arm (anything but a
      TypeParamRef / F1-record / eligible ptr-union, or an Optional of
      those): `seed_param_locals` marks such a param movable, so the
      last-use read needs `std::move(p)`, which no arm renders. The ctor MIL move
      arm is unaffected (it lowers the source name directly, not through the
      expr gate)."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if ((isinstance(u, OwnType) and isinstance(u.wrapped, OptionalType))
            or (isinstance(u, OptionalType)
                and isinstance(u.inner, OwnType))):
        # The storage-form `Own[P | None]` param (`std::optional<P>&&`)
        # routes: arrow reads via the pointers seed, has_value None tests,
        # whole-optional moves at its consumers (_own_storage_opt_param).
        if _own_storage_opt_param(u, analyzer) is not None:
            return None
        return "name.own_optional_read"
    own = unwrap_optional_own(u)
    if own is not None:
        inner = own.wrapped
        if isinstance(inner, OptionalType):
            inner = inner.inner
        inner = unwrap_readonly(inner)
        if (isinstance(inner, TypeParamRef)
                or _f1_record(inner, analyzer)
                or _eligible_ptr_union(inner, analyzer) is not None
                # An `Own[@dynamic P]` param (`std::unique_ptr<P>`): the
                # routed reads are the bare forward return and the arrow
                # receiver; an unsupported Own-slot arg still rejects at
                # its own ladder (no row admits the protocol-typed name).
                or (isinstance(inner, NominalType)
                    and is_dyn_protocol(inner))
                # An `Own[structural protocol]` PARAM (`items:
                # Own[Iterable[T]]`): the monomorphized `T_items&&` slot is
                # a plain C++ lvalue inside the body, so name reads render
                # bare (the for-head's `auto& __src_N = items;` capture) --
                # the sync twin of the sgen/frame Own-protocol admission.
                or (is_param and isinstance(inner, NominalType)
                    and inner.is_protocol
                    and not is_dyn_protocol(inner))
                # Own on a VALUE type is the no-op spelling (`Own[Int32]`
                # -> `int32_t x` by value): a PARAM's reads render the
                # bare name. A LOCAL routes only when MOVABLE-SEEDED (an
                # Own-typed loop var): the last-use read moves at owning
                # sinks (the harmless `std::move(x)`), which the move-source
                # rows key off the same working set.
                or ((is_param or movable_local)
                    and (_eligible_scalar(inner) or _eligible_char(inner)
                         or _eligible_enum(inner, analyzer) is not None))
                # An Own[CONTAINER] binding -- a LOOP VAR (`for row in
                # csv.reader(..)` at `Own[list[str]]`, whose elem binds
                # `auto&& row`) or a PARAM (whose signature spells the
                # container by value): both join the movable working set, so
                # reads render bare and the last one moves at an owning
                # sink -- which the move-source rows key off that same set.
                # The one position where a PARAM differs is the
                # SIMPLE-GENERATOR for-head, whose skeleton picks its
                # iteration strategy off the un-unwrapped binding; that seam
                # declines the name itself.
                or ((movable_local or is_param)
                    and _f1_container_ref(inner))
                # An Own[str]/Own[bytes] PARAM: the signature spells the
                # OWNED type by value, so the name reads are STORAGE
                # (`_own_viewfam_param` -- the owned-sink view->owned copy
                # never fires, and an owning sink hoists the copy+move temp
                # through the needs_copy cascade). Never movable:
                # `seed_param_locals` seeds only non-value Own payloads, and
                # str/bytes are value types.
                or (is_param
                    and isinstance(inner, NominalType)
                    and (is_str_type(inner) or is_bytes_type(inner)))
                # An `Own[pointer-repr F1 tuple]` PARAM: the signature
                # spells the storage tuple by value, so name reads are
                # STORAGE (the storage_tuple_locals seed); the unpack
                # lift, the optional-element decl lift, and the
                # storage-name arg rows gate their own uses.
                or (is_param and isinstance(inner, TupleType)
                    and inner.has_pointer_repr_element()
                    and _f1_tuple(inner, analyzer) is not None)
                ):
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
        # Own-optional inners still reject (no Own-axis read arm).
        if (_value_opt_scalar(u, analyzer) is not None
                or _value_opt_view(u, analyzer) is not None
                # A value-repr Optional[Callable] param routes its WHOLE-
                # optional reads (bare into a matching value-opt slot); the
                # narrowed read needs a deref no arm renders and is guarded
                # per-use at name lowering (name.optcallable_narrowed_read).
                or _value_opt_callable(u, analyzer) is not None
                # A value-repr Optional[ValueType record] binding routes its
                # has_value None test and narrowed member reads (`(*tz).off`).
                or _value_opt_value_record(u, analyzer) is not None
                # A value-repr Optional[Span] binding routes its has_value
                # None test only; narrowed and unproven whole reads are
                # guarded per-use at name lowering (the callable structure).
                or _value_opt_span(u, analyzer) is not None
                # A value-repr Optional[value tuple] binding, same scoping:
                # whole-optional reads only.
                or _value_opt_tuple(u, analyzer) is not None
                # An `Optional[String]` binding: has_value None test,
                # STORAGE `(*x)` narrowed deref, movable move at owned
                # sinks (the registered VIEW-kind owned-deref semantics).
                or _value_opt_string_owned(u) is not None):
            return None
        return "name.optval_read"
    if (isinstance(u, OptionalType) and u.uses_pointer_repr()
            and _optional_ptr_borrow_wide(u, analyzer) is None):
        # A nullable static-protocol param reads through the pointer set
        # (narrowed `(*x)` derefs; the None test is the constexpr swap).
        if _nullable_static_protocol_param(u) is not None:
            return None
        return "name.optional_ptr_read"
    return None

def _storage_optional_return_type(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The storage-form `Optional[F1-record]` return slot (F2c): `Own[T] | None`,
    which lowers to a `std::optional<T>` returned by value. `Inner | None` is
    pointer-repr (the function returns a borrow `Inner*`, a different direction)
    and is excluded. The caller passes None for a non-`TpyType` (unresolved)
    return annotation."""
    if not isinstance(t, OptionalType) or t.uses_pointer_repr():
        return None
    return t if _f1_record(_unwrap_own(t.inner), analyzer) else None

def _f1_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """A tuple element the F1 slice can spell: an eligible value scalar (`T`,
    same in both forms), an F1-record (BORROW_REF: `T*` borrow / `T` storage),
    or a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL: `T*` borrow /
    `std::optional<T>` storage). Each keeps `to_cpp_return()` / `to_cpp()`
    recursion off cross-module / native / generic / pending types, where bare
    `to_cpp()` would mis-spell. Union / container / generic elements are not
    lowered yet."""
    if _eligible_scalar(e) or _f1_record(e, analyzer):
        return True
    # An owned-str element spells `std::string` in BOTH forms (str is a
    # value type; only the pointer-repr siblings split), so it rides the
    # per-element conversions untouched.
    st = _resolved_str_value(e, analyzer)
    if st is not None and is_str_type(st):
        return True
    # A `Ptr[T]` element spells `T*` in BOTH forms (a pointer VALUE, copied
    # like a scalar); the runtime tuple_to_storage/tuple_to_pointer helpers
    # leave the slot alone -- the mixed Optional+Ptr regression case pins it.
    if _eligible_ptr_value(unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(e))), analyzer):
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
    tuple needs no conversion). Other tuples reject."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or not t.has_pointer_repr_element():
        return None
    if not all(_f1_tuple_element_ok(e, analyzer) for e in t.element_types):
        return None
    return t

def _open_slot_match(at_open: 'TpyType | None',
                     ptype: 'TpyType | None') -> bool:
    """The still-open-slot bare bind: the source's type IS the unsubstituted
    slot type (both spell the same template param), so the ref slot binds the
    render directly. Shared by the free-call and record-ctor arg ladders --
    the two spell the identical rule and must not drift."""
    p_open = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
              if isinstance(ptype, TpyType) else None)
    return (at_open is not None and p_open is not None
            and contains_type_param(p_open)
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at_open)))
            == p_open)


def _union_elem_tuple(t: 'TpyType | None', analyzer) -> 'TupleType | None':
    """A tuple whose only non-value slot is a POINTER-VARIANT union element
    (`tuple[Dog | Cat, Int32]`): borrow form
    `std::tuple<::tpy::Union<const Cat*, const Dog*>, int32_t>`, storage form
    `std::tuple<std::variant<Cat, Dog>, int32_t>`. A union element is
    excluded from `_element_is_pointer_repr` BY DESIGN (its borrow form is a
    variant, not a bare `T*`), so this family never routes through
    tuple_to_pointer / tuple_to_storage -- the WHOLE-tuple
    `tuple_value_to_borrow` conversion is its only boundary render. None
    otherwise; the wrapper-REF element family is `_wrapper_ref_tuple_return`'s.
    """
    if t is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(u, TupleType):
        return None
    has_union = False
    for e in u.element_types:
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if _eligible_ptr_union(eu, analyzer) is not None:
            has_union = True
            continue
        if not _eligible_scalar(eu):
            return None
    return u if has_union else None


def _optional_borrow_tuple(t: 'TpyType | None',
                           analyzer) -> 'TupleType | None':
    """The borrow TupleType under a nullable borrow-form tuple binding
    (`tuple[int, Box] | None` -> `std::optional<std::tuple<BigInt, Box*>>`),
    or None: an Optional whose bare inner is a ptr-repr F1 tuple. Non-Own
    tuples only -- the Optional of a MIXED owned+borrow tuple is a broken
    shape and a recorded design fork (BUGS.md), and `_f1_tuple` keeps Own
    elements out by construction."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(u, OptionalType):
        return None
    return _f1_tuple(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        u.inner))), analyzer)


def _nested_storage_tuple(t: 'TpyType | None', analyzer) -> 'TupleType | None':
    """A TupleType with NO direct pointer-repr element but at least one
    NESTED tuple element -- the no-borrow-form family (a nested reference
    gives no borrow form, so the tuple is storage-only; `std::get` chains
    read `.` at every level). Every element must be F1-renderable or itself
    a value / F1 / nested-storage tuple; exotic members (unions,
    containers, Optionals-of-non-record) stay out until witnessed."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or t.has_pointer_repr_element():
        return None
    has_nested = False
    for e in t.element_types:
        eb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if isinstance(eb, TupleType):
            has_nested = True
            if (_value_tuple(eb, analyzer) is None
                    and _f1_tuple(eb, analyzer) is None
                    and _nested_storage_tuple(eb, analyzer) is None):
                return None
        elif not _f1_tuple_element_ok(e, analyzer):
            return None
    return t if has_nested else None


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
    keys on BINDING-set membership; it agrees only for the rows whose set the
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
            # The owned-BYTES element (`tuple[bytes, bytes]` --
            # `std::vector<uint8_t>` storage) reads bare like the owned-str
            # element -- the owned form at every position.
            or _owned_bytes_slot(e, analyzer)
            # A UNIT element (`tuple[None, int]`) is `std::monostate` -- a
            # value type with no storage/borrow split, read bare like a
            # scalar.
            or is_void_like_type(e)
            # A value-repr Optional[scalar] element (`tuple[int, int|None]`)
            # is `std::optional<T>` value storage, read bare; None-tests and
            # unwraps gate per-shape at the read sinks. Pointer-repr
            # Optionals (`Box | None`) stay out -- their element form is a
            # borrow.
            or _value_opt_scalar(e, analyzer) is not None
            # An ENUM element (`tuple[Color, Int64]` -> `std::tuple<Color,
            # int64_t>`): a value scalar in C++ terms -- borrow and storage
            # forms coincide, so `std::get<N>(t)` reads it bare.
            or _eligible_enum(e, analyzer) is not None
            or isinstance(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(e))), AnyType))

def _value_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The value tuple of scalar / owned-str elements (`tuple[int, bool]` /
    `tuple[str, int]`), or None: a value type rendered `std::tuple<...>`
    where borrow and storage forms coincide at the tuple level, so a
    subscript read of any element needs no lift (a str element is an owned
    `std::string` inside the tuple storage; its read is an owned lvalue --
    bare in every sink). Admitted at the param slot (`const std::tuple<...>&`,
    spelled by the signature emitter), the return slot (a by-value
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

def _opt_owned_view_elem_tuple(t: TpyType | None,
                               analyzer) -> 'TupleType | None':
    """A storage tuple carrying at least one value-repr `Optional[str]` /
    `Optional[bytes]` element beside plain value-tuple elements
    (`tuple[bytes | None, str | None]` -> `std::tuple<std::optional<
    std::vector<uint8_t>>, std::optional<std::string>>`), or None.

    Such an element is OWNED inside the tuple's storage, so the whole
    optional copies by value like a scalar and the tuple has no borrow form
    to resolve -- but `_value_tuple` still declines it, because a bare str
    element there is an owned lvalue while a view-INNER optional would not
    be. Kept separate rather than widening that family, whose callers reach
    param and return slots this element has not been verified at.
    """
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or t.has_pointer_repr_element():
        return None
    any_opt = False
    for e in t.element_types:
        if _value_opt_owned_view(e, analyzer) is not None:
            any_opt = True
            continue
        if not _value_tuple_element_ok(e, analyzer):
            return None
    return t if any_opt else None

def _value_tuple_owned_str_elem(e: 'TpyExpr', analyzer) -> bool:
    """An OWNED-str element read out of a value tuple (`pair[1]` on
    `tuple[str, str]`): the element is a `std::string` held in the tuple's own
    storage, so `std::get<N>(pair)` is an owned lvalue that binds a str slot
    bare -- the view->owned copy a view-form source takes does not fire. The
    element read's own arm renders it; this only answers the FORM question a
    str sink has to ask before it decides whether to copy."""
    if not isinstance(e, TpySubscript) or isinstance(e.index, TpySlice):
        return False
    if e.needs_optional_runtime_check or e.slice_function_info is not None:
        return False
    if _value_tuple(analyzer.get_expr_type(e.obj), analyzer) is None:
        return False
    return _owned_str_slot(analyzer.get_expr_type(e), analyzer)

def _open_t_tuple_slot(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A tuple carrying an OPEN type-param element inside a generic body
    (`tuple[T, Int32]` -> `std::tuple<T, int32_t>`), or None: the decl slot
    spells the BARE `T`, so borrow and storage coincide there and the decl
    is the plain spelled copy like a value tuple's.

    Distinct from `_value_tuple` (no open element) and from
    `_own_record_tuple` (per-element `Own[record]`), so it gets its own
    predicate rather than widening either -- both are read at sinks whose
    render depends on the element being CLOSED.

    NOTE the decl spelling is narrower than the producer's: a protocol
    method returns `std::tuple<val_or_cptr_t<T>, int32_t>`, which coincides
    with `std::tuple<T, int32_t>` only for a value `T`. Sema blocks the
    non-value instantiation (a record arg fails protocol conformance), so
    the narrower spelling costs no reach."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or t.has_pointer_repr_element():
        return None
    open_elem = False
    for e in t.element_types:
        if isinstance(unwrap_readonly(unwrap_send_sync(e)), TypeParamRef):
            open_elem = True
            continue
        if not _value_tuple_element_ok(e, analyzer):
            return None
    return t if open_elem else None

def _tuple_has_own_element(t: 'TupleType') -> bool:
    """Any `Own[...]` element at any depth through nested tuples."""
    for e in t.element_types:
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if isinstance(eu, OwnType):
            return True
        if isinstance(eu, TupleType) and _tuple_has_own_element(eu):
            return True
    return False


def _own_record_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A STORAGE tuple with per-element `Own[record]` ownership
    (`tuple[Own[A], Own[B]]` -- `std::tuple<A, B>`), or None: the record
    elements are owned by value, so borrow and storage coincide at the
    tuple level and a decl / call result is the plain spelled copy
    (`std::tuple<Counter, Counter> t = make_pair();`). At least one
    Own-record element is required -- all-value tuples are `_value_tuple`,
    and plain-record-element tuples are the pointer-repr `_f1_tuple`
    family (distinct borrow form)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or t.has_pointer_repr_element():
        return None
    own_rec = False
    for e in t.element_types:
        eu = unwrap_readonly(unwrap_send_sync(e))
        if (isinstance(eu, OwnType)
                and _f1_record(unwrap_readonly(eu.wrapped), analyzer)):
            own_rec = True
            continue
        # NESTED own-record tuple elements stay OUT until the
        # tuple-over-tuple chain read lands: admitting the decl with no
        # routable consumer is unwitnessed surface (no body completes
        # through it -- the SpanIter-return treatment).
        if not _value_tuple_element_ok(e, analyzer):
            return None
    return t if own_rec else None

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
      sink is `_value_opt_view`-routed; the decl/print sinks reject."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    inner = t.inner
    if _is_string_owned(inner):
        # `Optional[String]` is NOT part of this borrow/owned split: its
        # narrowed deref MOVES at an owned sink (`return std::move((*x));`)
        # rather than taking the view->owned copy the family's emit spells.
        return None
    return t if _resolved_str_value(inner, analyzer) is not None else None

def _value_opt_string_owned(t: 'TpyType | None') -> 'OptionalType | None':
    """The value-repr `Optional[String]` binding type (`x: Optional[String]`
    -> `std::optional<std::string>` by value AT THE PARAM TOO, unlike the
    `str | None` view/owned split `_value_opt_str` serves). Its narrowed
    deref `(*x)` is an OWNED lvalue (STORAGE) that MOVES at owned sinks
    (`return std::move((*x));` -- seed_param_locals' expensive-copy
    movable face). Registered as the VIEW binding kind (a VIEW-kind
    LOCAL's owned-deref semantics), with the param-vs-local form split
    overridden to STORAGE for this shape."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return None
    return t if _is_string_owned(unwrap_readonly(t.inner)) else None


def _value_opt_bytes(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The value-repr `Optional[bytes]` type: `bytes | None` / `BytesView | None`,
    bound `std::optional<std::span<const uint8_t>>` at the param boundary (borrow
    form) and an owned `std::optional<std::vector<uint8_t>>` elsewhere -- the
    bytes twin of `_value_opt_str`. Every position renders family-neutrally
    (`has_value()`, the narrowed `(*b)` span read, `::tpy::is_truthy(b)`) except
    the view->owned copy, which the view-family emit spells `::tpy::Bytes`
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
    the `std::string` vs `::tpy::Bytes` split, so these sites need no
    per-family branch."""
    return (_value_opt_str(t, analyzer)
            or _value_opt_bytes(t, analyzer))

def _view_inner_value_opt(t: 'TpyType | None') -> bool:
    """A value-repr `Optional[view]` whose INNER is the view family itself
    (`StrView | None` / `BytesView | None` -> `std::optional<std::string_view>`),
    the complement of `_value_opt_owned_view`. A view-typed source lands bare
    in such a slot; the OWNED-inner sibling needs a view->owned copy."""
    if not isinstance(t, TpyType):
        return False
    tb = unwrap_readonly(unwrap_send_sync(t))
    if not isinstance(tb, OptionalType):
        return False
    inner = unwrap_readonly(tb.inner)
    return is_str_view_type(inner) or is_bytes_view_type(inner)

def _value_opt_view_ret_src(e: 'TpyExpr', ret_opt: 'OptionalType',
                            analyzer) -> bool:
    """A call / method-call / subscript RESULT that lands bare in a value-repr
    `Optional[view-family]` return slot.

    Two ways to land bare, both leaving the render to the source's own gates:
    the callee already hands back the SAME optional (nothing to convert), or it
    hands back an inner value whose owned-vs-view form already matches the
    inner's, so the optional's converting constructor absorbs it. A form
    MISMATCH (a view result into an OWNED inner) needs a materializing copy
    this row does not render, and sema spells that one as a coercion anyway."""
    if not isinstance(e, (TpyCall, TpyMethodCall, TpySubscript)):
        return False
    st = analyzer.get_expr_type(e)
    if not isinstance(st, TpyType):
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
    if isinstance(u, OptionalType):
        return (_value_opt_view(u, analyzer) is not None
                and u == unwrap_readonly(unwrap_send_sync(ret_opt)))
    src_v = _resolved_viewfam_value(u, analyzer)
    inner_v = _resolved_viewfam_value(unwrap_readonly(ret_opt.inner), analyzer)
    return src_v is not None and src_v == inner_v


def _value_opt_owned_view(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """A value-repr `Optional[view]` whose inner is an OWNED family (`str`/`bytes`,
    NOT `StrView`/`BytesView`) -- the shape a LOCAL binds `std::optional<std::string>`
    / `<std::vector<uint8_t>>` (owned inner), so its narrowed deref `(*acc)` is
    already OWNED (STORAGE). A view-INNER optional (`optional<string_view>`) is
    excluded: its LOCAL narrowed read stays on the str/bytes-name arm (no
    deref), so it must not enter the VIEW-kind binding set."""
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
    `slot` fires `_maybe_convert_opt_view_param`'s ARG split: both must be
    value-repr Optional whose inner resolves to an OWNED view family
    (`view_family_for_type` non-None, e.g. `str` / `bytes`), of the SAME family. A
    `StrView` / `BytesView` inner keys `view_family_for_type` to None (the map is
    owned-qname-keyed) and passes BARE -- excluded here, so that shape rejects.
    The same-family check routes a str src to a str slot and a bytes src to a
    bytes slot (their families differ)."""
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
        # An Own[genrec] element is the same by-value slot as Own[record]
        # (`std::tuple<Tree<int32_t>, ...>`); its literal source takes the
        # ru-instance spelled render.
        return (_f1_record(inner.wrapped, analyzer)
                or isinstance(unwrap_readonly(inner.wrapped),
                              RecursiveAliasInstanceType))
    return (_value_opt_scalar(e, analyzer) is not None
            or _value_opt_view(e, analyzer) is not None)

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

def _union_elem_value_tuple(t: TpyType | None,
                            analyzer) -> 'TupleType | None':
    """A VALUE tuple with at least one VALUE-UNION element, every other
    element a narrow value element (`tuple[int | str, int]` ->
    `std::tuple<std::variant<..>, BigInt>`): borrow and storage coincide,
    so the literal / call result renders the plain spelled tuple and each
    variant element rides its converting ctor."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or t.has_pointer_repr_element():
        return None
    has_union = False
    for e in t.element_types:
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if _eligible_value_union(eu) is not None:
            has_union = True
            continue
        if not _value_tuple_element_ok(e, analyzer):
            return None
    return t if has_union else None


def _generic_value_tuple_return(t: TpyType | None,
                                analyzer) -> 'TupleType | None':
    """The GENERIC tuple RETURN slot: a tuple with at least one bare
    TypeParamRef element (its slot spells `::tpy::val_or_ptr_t<T>` and the
    element wraps in `::tpy::to_val_or_ptr` -- the slot-info ladder's
    TypeParamRef row, leaving value-vs-pointer to instantiation), every
    other element a narrow value-tuple element (scalar / owned-str / Any --
    bare by-value slots). Disjoint from `_value_tuple_return` (which has no
    TypeParamRef row); wrapped generic elements (`readonly[T]` / `Ref[T]`)
    and wider concrete mixes (Own / Optional / nested tuples beside a T)
    have no witnesses and reject."""
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

def _storage_opt_ternary_result(rtype: 'TpyType | None',
                                analyzer) -> 'OptionalType | None':
    """An Optional TERNARY result at an IMMEDIATE container-element position,
    where the slot is the `std::optional<T>` STORAGE form whatever
    `uses_pointer_repr()` says -- the ternary's `in_container_element`
    carve-out, which wraps both arms in the spelled optional. Restricted to
    the two inners whose arms are self-contained VALUES (a dict's
    `ordered_map<K, V>({...})`, a value tuple's spelled brace). Every other
    inner -- a record above all -- has arms that can render in BORROW form,
    where that wrap is ill-formed C++."""
    if rtype is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if not isinstance(u, OptionalType):
        return None
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(u.inner)))
    if isinstance(inner, OwnType):
        return None
    if is_dict(inner) or _value_tuple_nested(inner, analyzer) is not None:
        return u
    return None


def _opt_ternary_tuple_arm_ok(arm: TpyExpr, vt: 'TupleType', analyzer) -> bool:
    """A value-tuple LITERAL arm of a container-element Optional ternary whose
    brace-init cannot take the view->owned element copy. That copy fires when
    the element SLOT resolved to an owned str/bytes while the SOURCE reads as
    a view (a `str` PARAM is exactly that pair) -- the source spells bare
    inside the tuple, so admitting one would drop the copy. A FIELD read is
    immune: its slot IS the member's own resolved type, so the forms agree,
    and it is what the `astuple()` expansion emits."""
    if not (isinstance(arm, TpyTupleLiteral)
            and len(arm.elements) == len(vt.element_types)):
        return False
    for el, et in zip(arm.elements, vt.element_types):
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        if isinstance(eu, TupleType):
            if not _opt_ternary_tuple_arm_ok(el, eu, analyzer):
                return False
        elif (_resolved_str_value(et, analyzer) is not None
                or _resolved_bytes_value(et, analyzer) is not None):
            if not isinstance(el, TpyFieldAccess):
                return False
    return True


def _storage_opt_ternary_elem(e: TpyExpr, analyzer) -> bool:
    """An Optional-result TERNARY container element (the asdict/astuple
    expansion's `{...} if f.has_value() else None`). The ternary renders
    against its OWN type and ignores the element target, so the SLOT plays no
    part -- an Optional slot and a union slot holding it take the identical
    `std::optional<T>(<arm>)` render, absorbed by the storage slot or the
    variant's converting ctor. Arm shapes gate in `_lower_if_expr`."""
    return (isinstance(e, TpyIfExpr)
            and _storage_opt_ternary_result(
                analyzer.get_expr_type(e), analyzer) is not None)


def _decl_tuple_nested(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The tuple-literal DECL sink's widened element family: everything
    `_value_tuple_nested` admits plus plain scalar-read containers
    (`tuple[dict[str, Int32], Int32]` -- the element literal renders with
    its typed ctor / brace) and value-form unions (`tuple[Int32 | str,
    ...]` -- the variant's converting ctor absorbs the bare member).
    Decl-only: the print/subscript sinks keep the narrow family."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType):
        return None
    for e in t.element_types:
        if not (_value_tuple_element_ok(e, analyzer)
                or _value_tuple_nested(e, analyzer) is not None
                or _container_scalar_read(e, analyzer)
                # A set element (`tuple[set[Int32], str]`): sets have no
                # subscript, but the DECL only renders the literal.
                or (is_set(_dt_b := unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(e))))
                    and bool(_dt_args := getattr(_dt_b, "type_args", None))
                    and (_eligible_scalar(_dt_args[0])
                         or _owned_str_slot(_dt_args[0], analyzer)))
                or _eligible_value_union(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e))))
                is not None
                # A recursive-union WRAPPER member (`pair = (leaf, 0)` at
                # `tuple[Own[Tree[Int32]], Int32]`): the storage tuple holds
                # the wrapper struct by value; the member name copies bare /
                # moves at its last use through the shared elem move facts.
                # `_wrapper_union_like` covers the generic instance too.
                or _wrapper_union_like(
                    _unwrap_own(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(e)))), analyzer) is not None):
            return None
    return t


def _tuple_container_elem_read(e: TpyExpr, locals_: dict[str, TpyType],
                               analyzer) -> 'TpyType | None':
    """`t[N]` whose element is a scalar-read CONTAINER, read at a RECEIVER
    position (`t[0][0] = 9` off a storage tuple local, `xs[0][1][0] = 8`
    off a container element): `std::get<N>(...)` yields the container
    lvalue bare. Value positions stay on `_tuple_subscript_value_read`'s
    families (a container element read at a copy sink is unwitnessed).
    Receiver shapes match `_subscript_recv_tuple`'s name /
    container-element branches without its element-family tail."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if isinstance(recv, TpyName):
        if recv.name not in locals_:
            return None
    elif (isinstance(recv, TpySubscript)
          and isinstance(recv.obj, TpyName)
          and recv.obj.name in locals_):
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
    el = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        resolve_int_literals(recv_t.element_types[idx],
                             analyzer.ctx.default_int_for_literal))))
    return el if _container_scalar_read(el, analyzer) else None


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
    (and the derived ordering ops) compares them element-wise, emitted as
    the bare `(left op right)` fall-through. A pointer-repr tuple (record /
    Optional[record] element) whose slots are bare pointers would compare
    ADDRESSES, so it needs the `::tpy::tuple_eq` / `tuple_lt` deref-aware
    helpers -- a separate face (`_value_tuple_nested` excludes pointer-repr
    tuples)."""
    # Literal tuple operands carry IntLiteral elements pre-resolution
    # (`(1, 0) < (1, 1)`); resolve like the subscript/needle rows.
    if lt is not None:
        lt = resolve_int_literals(lt, analyzer.ctx.default_int_for_literal)
    if rt is not None:
        rt = resolve_int_literals(rt, analyzer.ctx.default_int_for_literal)
    return (_value_tuple_nested(lt, analyzer) is not None
            and _value_tuple_nested(rt, analyzer) is not None)

def _resolve_pending_tuple_elems(tt: TupleType, analyzer) -> TupleType:
    """A TupleType with every pending element (IntLiteral / view) resolved
    -- resolve_tuple_pending runs before any type spelling (an
    IntLiteral element would otherwise leak its VALUE into
    `std::tuple<1, Box*>`). Shared by the compare pair, the membership
    needle, and the print tuple-literal arm."""
    return TupleType(tuple(
        (_resolve_pending_view(et, analyzer)
         or resolve_int_literals(et, analyzer.ctx.default_int_for_literal))
        for et in tt.element_types))

def _ptr_tuple_literal_compare_pair(
        e: TpyBinOp, analyzer) -> 'tuple[TupleType, TupleType] | None':
    """Both compare operands are tuple LITERALS whose type carries a
    pointer-repr element (`(1, a) == (1, b)` with record members): raw
    std::tuple operators would compare ADDRESSES, so the pair renders
    through the deref-aware `::tpy::tuple_eq` / `tuple_lt` helpers over
    borrow-form literal renders (`std::tuple<int32_t, Box*>{1, &(a)}`).
    Literal operands only -- the witnessed slice; name/field tuple operands
    keep rejecting (their reads carry form conversions this pair does not
    render)."""
    if not (isinstance(e.left, TpyTupleLiteral)
            and isinstance(e.right, TpyTupleLiteral)):
        return None
    lt = analyzer.get_expr_type(e.left)
    rt = analyzer.get_expr_type(e.right)
    ltu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
           if lt is not None else None)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if not (isinstance(ltu, TupleType) and isinstance(rtu, TupleType)):
        return None
    if not (ltu.has_pointer_repr_element()
            or rtu.has_pointer_repr_element()):
        return None
    return (_resolve_pending_tuple_elems(ltu, analyzer),
            _resolve_pending_tuple_elems(rtu, analyzer))

def _ptr_tuple_field_compare_pair(
        e: TpyBinOp, locals_: dict[str, TpyType],
        analyzer) -> 'tuple[TupleType, TupleType] | None':
    """Both compare operands are FIELD reads of pointer-repr tuples
    (`this->pair == other.pair`, the dataclass __eq__ chain): the same
    deref-aware `tuple_eq` / `tuple_lt` composition, over the BARE member
    reads (the helpers bridge the storage form -- the member renders with
    no lift). DECLARED-type keyed: a narrowed Optional-declared field reads
    with an unwrap this pair does not render, and a property read is a call
    in disguise -- both stay out."""
    if not (isinstance(e.left, TpyFieldAccess)
            and isinstance(e.right, TpyFieldAccess)):
        return None
    if (e.left.property_getter_call is not None
            or e.right.property_getter_call is not None):
        return None
    pair = []
    for side in (e.left, e.right):
        at = analyzer.get_expr_type(side)
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if not (isinstance(atu, TupleType)
                and atu.has_pointer_repr_element()):
            return None
        ft = _field_decl_type(side, locals_, analyzer)
        ftu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
               if ft is not None else None)
        if ftu != atu:
            return None
        pair.append(atu)
    return (pair[0], pair[1])


def _const_index(index: TpyExpr) -> 'int | None':
    """The compile-time integer index of a tuple subscript: a bare int
    literal or a negated int literal. A
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

def _borrow_tuple_param_elem_subscript(sub: TpyExpr, prescan, analyzer) -> bool:
    """A pointer-repr record element read off a borrow-form tuple PARAM
    (`b = p[1]` off `readonly[tuple[Int32, Counter]]` -> `const Counter& b =
    (*std::get<1>(p));`): `std::get` on the borrow tuple yields the element
    `T*`, so the REF_ALIAS decl binds its referent via the deref-flagged
    subscript render -- the value-context wrap. PARAM receivers only:
    the storage-vs-borrow membership question that needs walk state (an
    `auto&&` alias local holds elements by value) cannot arise for a param;
    an `Own[tuple]` param is storage form and excluded the same way."""
    if not isinstance(sub, TpySubscript) or sub.needs_optional_runtime_check:
        return False
    if not (isinstance(sub.obj, TpyName)
            and sub.obj.name in prescan.param_names):
        return False
    # `get_expr_type` strips the Own wrapper, so the Own exclusion must key
    # on the DECLARED param fact: an Own[tuple] param binds the storage
    # tuple by value -- `std::get` yields a `T&`, and the deref-flagged
    # render would be ill-formed.
    if sub.obj.name in prescan.own_tuple_params:
        return False
    pt = unwrap_readonly(unwrap_send_sync(analyzer.get_expr_type(sub.obj)))
    if isinstance(pt, OwnType):
        return False
    res = _subscript_index_and_tuple(sub, analyzer)
    if res is None:
        return False
    recv_t, idx = res
    et = recv_t.element_types[idx]
    return (et.value_form() is ValueForm.BORROW_REF
            and TupleType._element_is_pointer_repr(et)
            and _f1_record(et, analyzer))


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
    ineligible tuples, and non-const indices reject."""
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
    elif isinstance(recv, TpySubscript):
        # A CONTAINER-element root (`items[i][N]` -- the inner receiver is
        # not itself a tuple), or a tuple-over-tuple CHAIN
        # (`q[1][1]` / `xs[0][1][1]` -> `std::get<1>(std::get<1>(..))`):
        # the inner get resolves through this ladder recursively and must
        # yield a NESTED TupleType element (held by value, `.` access).
        _rt_root_ok = False
        ib = None
        if (isinstance(recv.obj, TpyName)
                and recv.obj.name in locals_):
            it = locals_.get(recv.obj.name)
            ib = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
                  if isinstance(it, TpyType) else None)
            _rt_root_ok = ib is not None and not isinstance(ib, TupleType)
        if not _rt_root_ok:
            ib = None
            inner_res = _subscript_recv_tuple(recv, locals_, analyzer)
            if inner_res is None:
                return None
            in_t, in_idx = inner_res
            mid = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                resolve_int_literals(in_t.element_types[in_idx],
                                     analyzer.ctx.default_int_for_literal))))
            if not isinstance(mid, TupleType):
                return None
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A MIXED-own-tuple CALL receiver (`make_mixed(b)[1].val` ->
        # `std::get<1>(make_mixed(b))->val`): the call renders bare in
        # place; the element arrow comes from the mixed render
        # (`_subscript_yields_borrow_ptr`'s call leg).
        if not _renders_own_borrow_tuple(recv, frozenset(), analyzer):
            return None
    else:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    if isinstance(recv, TpySubscript) and ib is not None:
        # The container-element receiver's analyzer type can carry
        # unresolved literal elements (`items = [(7, Box(10))]` types
        # `items[0]` with an IntLiteralType member); the container's
        # DECLARED element tuple is the resolved authority (a tuple-over-
        # tuple CHAIN receiver has no container to consult -- ib is None
        # there and the analyzer type stands). A dict
        # receiver's subscript yields its VALUE slot (iteration would
        # yield keys, the wrong axis for `d[k][N]`).
        if is_dict(ib):
            _dk_args = getattr(ib, "type_args", None)
            et = _dk_args[1] if _dk_args and len(_dk_args) > 1 else None
        else:
            et = get_iterable_element_type(ib, analyzer.registry)
        eb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
              if isinstance(et, TpyType) else None)
        if (not isinstance(eb, TupleType)
                or len(eb.element_types) != len(recv_t.element_types)):
            return None
        recv_t = eb
        res = (recv_t, idx)
    if not (_value_tuple(recv_t, analyzer) is not None
            or _f1_tuple(recv_t, analyzer) is not None
            # Own-record-element tuples store by value: `std::get<N>(t)`
            # yields the record lvalue, `.` member access.
            or _own_record_tuple(recv_t, analyzer) is not None
            # The NESTED-storage family (`tuple[Int32, tuple[Int32, P]]`
            # -- no direct pointer-repr element, so no borrow form; the
            # get chain reads `.` all the way down).
            or _nested_storage_tuple(recv_t, analyzer) is not None):
        return None
    return res

def _open_sibling_value_tuple(t: 'TpyType | None', analyzer) -> bool:
    """A tuple with at least one bare TypeParamRef element whose remaining
    elements are value-family (`tuple[T, str]` -- a generic lambda's param):
    `std::get<N>(p)` of a NON-open element reads bare exactly like the
    value-tuple arm; the open element only changes its OWN slot spelling
    (`val_or_ptr_t<T>`), never a sibling read. Scoped to the subscript
    value-read predicate -- storage/borrow classifiers must not use this."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))) \
        if t is not None else None
    if not isinstance(u, TupleType):
        return False
    saw_open = False
    for el in u.element_types:
        eu = unwrap_readonly(el)
        if isinstance(eu, TypeParamRef):
            saw_open = True
            continue
        if not (_eligible_scalar(eu) or _owned_str_slot(eu, analyzer)
                or _value_tuple_nested(eu, analyzer) is not None):
            return False
    return saw_open


def _slot_free_ptr_reseat_ok(init: 'TpyExpr | None', lc) -> bool:
    """A frame pointer-member reseat that never touches the sync-only
    `__slot_N`: a None reseat (`prev = nullptr;`) or an un-narrowed
    pointer-name copy (`prev = it;` -- both already `T*` members). The one
    admission fact shared by the leaf guard (resumable.py, which delegates
    to the sync arms) and the branch-nested interception (statements.py,
    which constructs the renders directly)."""
    if isinstance(init, TpyNoneLiteral):
        return True
    return (isinstance(init, TpyName) and init.name in lc.pointers
            and init.name not in lc.narrow.narrowed)


def _value_tuple_needle_ok(lt_bare: 'TpyType | None', analyzer) -> bool:
    """A VALUE-TUPLE membership needle (`(1, 2) in d` on a tuple-keyed
    dict/set): the spelled tuple render lands bare inside `contains(...)`.
    Literal elements resolve to the default int first. Pure predicate --
    the two membership arms witness at their call sites."""
    if lt_bare is None:
        return False
    return _value_tuple(
        resolve_int_literals(lt_bare, analyzer.ctx.default_int_for_literal),
        analyzer) is not None


def _container_record_elem_style_tuple_read(recv: 'TpySubscript',
                                            locals_: dict[str, TpyType],
                                            analyzer) -> bool:
    """The inner `pairs[0]` of a `pairs[0][0]` chain: a single-index
    container-element read off a bare in-scope list/Array/dict NAME -- or
    off a clean FIELD read of one (`self._store[lk][1]`) -- whose element
    is a value tuple, the `__getitem__(pairs, 0)` lvalue the outer
    `std::get<N>` consumes. The receiver only decides how the container
    itself renders (`pairs` / `this->_store`, both bare lvalues), never
    what the element read spells, so the two receiver shapes share this
    verdict. Slices stay out (their own arms)."""
    if isinstance(recv.index, TpySlice) or recv.slice_function_info is not None:
        return False
    if isinstance(recv.obj, TpyName):
        if recv.obj.name not in locals_:
            return False
        ct = locals_[recv.obj.name]
    elif isinstance(recv.obj, TpyFieldAccess):
        if not _field_receiver_ok(recv.obj, locals_, analyzer):
            return False
        ct = _field_decl_type(recv.obj, locals_, analyzer)
    else:
        return False
    ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ct)))
    ct = _resolve_literal_seeded(ct, analyzer)
    if not (is_list(ct) or is_array(ct) or is_dict(ct)):
        return False
    args = getattr(ct, "type_args", None)
    if not args:
        return False
    # A dict holds its element in the SECOND type arg; list/Array in the first.
    if is_dict(ct):
        if len(args) < 2:
            return False
        args = args[1:]
    elem = resolve_int_literals(args[0],
                                analyzer.ctx.default_int_for_literal)
    if _value_tuple_nested(elem, analyzer) is not None:
        return True
    # A tuple with CONTAINER elements (`list[tuple[Int32, list[Int32]]]`):
    # stored inline in the list, so `__getitem__` yields the same lvalue
    # the value flavor gets -- the outer container-element read
    # (`_tuple_subscript_container_elem_read`) consumes it.
    eb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem)))
    def _elem_ok(et: TpyType) -> bool:
        etb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        return (_eligible_scalar(etb)
                or _resolved_str_value(etb, analyzer) is not None
                or _f1_container_ref(etb))

    return (isinstance(eb, TupleType) and bool(eb.element_types)
            and all(_elem_ok(et) for et in eb.element_types))


def _genfac_like_call(a: TpyExpr, analyzer) -> bool:
    """A call producing a generator frame: the resolved fi is a generator,
    or -- the overload seam, whose per-signature fis carry
    is_generator=False while the impl is the generator -- a PLAIN-TPy
    callee returning the `typing.Iterator` protocol (sema forbids that
    return on non-generator plain functions, so only overload-seam fis
    reach the leg; `is_stub` below is the DECLARATION-stub flag, a
    different notion). @native / @cpp_template callees (map/zip/filter/
    reversed/iter -- Iterator[T] returns over non-frame C++ objects) are
    EXCLUDED: their renders ride their own combinator rows. Shared by the comp genfac routes, the
    marker gate's iterable admission, and the gen-factory arg-temp leg,
    so the consumers stay in lockstep."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    fi = a.resolved_function_info
    if fi is None:
        return False
    if fi.is_generator:
        return True
    if fi.native_function or fi.cpp_template or fi.is_stub:
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.qualified_name() == "typing.Iterator")


def _tuple_subscript_container_elem_read(
        e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """A CONTAINER-element tuple read chain (`print(pairs[1][1])` on
    `list[tuple[Int32, list[Int32]]]`): the outer `std::get<N>` over the
    inner `__getitem__` lvalue yields the container element (`T&`,
    BORROW) -- ListPrinter's operand / a REF_ALIAS bind. Int-literal
    index; the inner read is the container-element tuple shape."""
    if not isinstance(e, TpySubscript) or not isinstance(e.obj, TpySubscript):
        return False
    if not _container_record_elem_style_tuple_read(e.obj, locals_, analyzer):
        return False
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return False
    recv_t, idx = res
    eb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        recv_t.element_types[idx])))
    # LIST elements only until a dict/set witness appears -- the wrap legs
    # keyed on this predicate must not admit unwitnessed kinds.
    return is_list(eb) or is_array(eb)


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
        # A nested read `t[i][j]`: the inner `t[i]` is a value-tuple
        # subscript read -- or a CONTAINER-element tuple read
        # (`pairs[0][0]` on `list[tuple[...]]`, the `std::get<0>(
        # __getitem__(pairs, 0))` render) -- yielding a (possibly nested)
        # value tuple. Literal-seeded elements resolve first.
        _ntv = resolve_int_literals(
            analyzer.get_expr_type(recv),
            analyzer.ctx.default_int_for_literal) \
            if analyzer.get_expr_type(recv) is not None else None
        if (_value_tuple_nested(_ntv, analyzer) is None
                or (_tuple_subscript_value_read(recv, locals_,
                                                analyzer) is None
                    and not _container_record_elem_style_tuple_read(
                        recv, locals_, analyzer))):
            return None
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A value-tuple-returning CALL receiver (`getsockname()[1]`):
        # `std::get<N>(<call>)` emits the call in place, so the read is
        # position-neutral iff the call itself lowers -- the call gates in
        # _lower_expr, and a non-routable one rejects there.
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
    if isinstance(recv, (TpyNamedExpr, TpySubscript)):
        # A literal-init walrus type carries IntLiteral elements
        # (`(t := (1, b))` -> tuple[IntLiteral(1), Box]), and a
        # container-element read off a literal-seeded list does too
        # (`pairs[1][0]` on `[(1, 2), (Int32(3), 4)]`); `get_resolved_type`
        # collapses them, so resolve before the family check.
        recv_t = resolve_int_literals(recv_t,
                                      analyzer.ctx.default_int_for_literal)
    if (_value_tuple_nested(recv_t, analyzer) is None
            # A view element keeps a tuple out of `_value_tuple` because it
            # pins the tuple LITERAL's render to static storage -- a
            # constraint no element READ carries: `std::get<N>(t)` spells
            # the same whatever the SIBLING elements are, and the element
            # actually read is still gated below.
            and _value_tuple_global(recv_t, analyzer) is None
            and _f1_tuple(recv_t, analyzer) is None
            # A per-element-Own record tuple (`tuple[Own[A], Int32]` ->
            # `std::tuple<A, int32_t>`): borrow and storage coincide, so
            # element reads spell the same bare `std::get<N>(p)`.
            and _own_record_tuple(recv_t, analyzer) is None
            # A ptr-variant UNION element tuple: the union element only
            # changes its OWN slot spelling, never a sibling's, so a value
            # slot still reads the bare `std::get<N>(pair)` -- the same rule
            # `_open_sibling_value_tuple` applies to an open type param.
            and not (_union_elem_tuple(recv_t, analyzer) is not None
                     and _witness("subscript.union_elem_tuple"))
            and not _open_sibling_value_tuple(recv_t, analyzer)):
        return None
    el = recv_t.element_types[idx]
    # An owned-str element reads as an owned lvalue (`std::get<N>(t)` yields
    # `const std::string&`) -- bare in every sink, so it rides
    # the same value-read arm as a scalar element. A nested value-tuple element
    # reads bare as a whole `std::tuple<...>` value (recursively value-tuple),
    # consumed by print / decl-init / a further subscript. A value-repr
    # Optional[scalar] element reads bare as `std::optional<T>` STORAGE
    # (None-tests / unwraps gate at the value-opt consumers); pointer-repr
    # Optionals stay on the borrow paths.
    return (idx if (_eligible_scalar(el) or _owned_str_slot(el, analyzer)
                    or _value_opt_scalar(el, analyzer) is not None
                    or _value_tuple_nested(el, analyzer) is not None
                    # An enum element is a value scalar in C++ terms:
                    # `std::get<N>(t)` reads it bare in every value sink.
                    or _eligible_enum(el, analyzer) is not None
                    # A `Ptr[T]` element is a pointer VALUE (`T*`, copied
                    # like a scalar): `std::get<N>(t)` reads it bare; the
                    # member-access wrap (deref_check / `->`) belongs to the
                    # consuming field arm, never to this read.
                    or _eligible_ptr_value(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(el))), analyzer))
            else None)

def _subscript_record_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> 'int | None':
    """`t[N]` whose element is a plain F1-record (a `BORROW_REF` pointer-repr
    slot) or an `Own[record]` stored by value -- a borrow / value lvalue
    usable as a scalar-field-read receiver (`t[N].field`; the arrow-vs-dot
    decision keys on the element's pointer-repr-ness downstream, so the Own
    element reads `.`). Returns the normalized index, or None.
    `Optional[record]` elements take the null-check member path
    (`_subscript_optional_field_recv`) and are excluded here (`_f1_record`
    rejects them)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    el = unwrap_readonly(unwrap_send_sync(recv_t.element_types[idx]))
    if isinstance(el, OwnType):
        el = unwrap_readonly(el.wrapped)
    return idx if _f1_record(el, analyzer) else None

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
    rejects. Markers-clean excludes the Optional null-check / property /
    setattr shapes, so an Optional-element write and a property-setter write
    reject too."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _subscript_record_field_recv(e.obj, locals_, analyzer) is not None)

def _optional_field_over_container_subscript_ok(
        e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """A scalar field read off a STORAGE-form Optional container subscript
    with an unproven None (`d["a"].x` on `dict[str, P | None]` ->
    `::tpy::deref_optional_check(::tpy::__getitem__(d, "a")).x`) -- the
    container twin of `_optional_field_over_subscript_ok` (whose tuple
    receiver pre-lifts to `T*` and takes deref_check instead). Read only."""
    return (isinstance(e, TpyFieldAccess) and e.needs_optional_runtime_check
            and _field_markers_clean(e, allow_optional_check=True)
            and isinstance(e.obj, TpySubscript)
            and reads_storage_form_optional(analyzer, e.obj))


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
                                pointers: 'AbstractSet[str]', *,
                                ptr_recv_ok: bool = False,
                                opt_ptr_recv_ok: bool = False) -> bool:
    """The record-getitem subscript arm's index/receiver admission, written
    once for the subscript arm and the field-over-getitem gate: a scalar
    index (a runtime-BigInt one against a fixed-int key param carries the
    `.to_fixed_check` narrow the arm applies) or a str value, off a declared
    non-pointer NAME or clean-field receiver
    (pointer-local receivers render `(*p)[...]`, so a caller admits them only
    by opting in -- `ptr_recv_ok` for a module-var slot, `opt_ptr_recv_ok` for
    a None-narrowed pointer-repr Optional -- and rendering the deref itself).
    An UNPROVEN Optional receiver rejects, explicitly here rather than
    relying on `_record_getitem_key`'s unwrap staying narrow."""
    if sub.needs_optional_runtime_check:
        return False
    idx_type = analyzer.get_expr_type(sub.index)
    if idx_type is not None:
        # A bare literal index types IntLiteral pre-resolution
        # (`moved[0].name`); it renders at the default int.
        idx_type = resolve_int_literals(idx_type,
                                        analyzer.ctx.default_int_for_literal)
    # The disposition runs UNCONDITIONALLY: it keys the BigInt half on the
    # DECLARED type, so a per-occurrence pre-test here would short-circuit
    # past it for a composite over a retro-widened local and admit a shape
    # whose narrow this slice does not render.
    idx_ok = ((_resolved_scalar(idx_type, analyzer)
               and _bigint_index_disposition(
                       sub.index, analyzer.get_expr_type(sub.obj),
                       analyzer, locals_) != "reject")
              or _resolved_str_value(idx_type, analyzer) is not None)
    recv_ok = ((isinstance(sub.obj, TpyName) and sub.obj.name in locals_
                and (sub.obj.name not in pointers or ptr_recv_ok
                     or opt_ptr_recv_ok))
               or (isinstance(sub.obj, TpyFieldAccess)
                   and _field_receiver_ok(sub.obj, locals_, analyzer))
               # A module-attr GLOBAL receiver (`os.environ[k]`): renders
               # through the module-variable arm's `(*slot)` deref.
               or _module_var_recv(sub.obj, locals_, analyzer))
    return bool(idx_ok and recv_ok)


def _open_ref_return(fi) -> bool:
    """A method declared to return a REFERENCE to a bare type param (`->
    auto_readonly[T]` on a generic record, which sema resolves to
    `RefType(T)`). `call_returns_cpp_ref` answers no for it because the
    unwrapped return is a `TypeParamRef` -- but the RefType IS the declared
    C++ reference convention, and at an instantiation whose T is a
    non-value type the monomorphized `val_or_ref_t<T>` return is `T&`. The
    caller must therefore have proven the SUBSTITUTED result non-value; on a
    value-typed T the same method returns by value."""
    return (fi is not None and not fi.is_constructor
            and isinstance(fi.return_type, RefType)
            and isinstance(fi.return_type.wrapped, TypeParamRef))


def _record_getitem_borrow_subscript(sub: TpyExpr,
                                     locals_: dict[str, TpyType], analyzer,
                                     pointers: 'AbstractSet[str]') -> bool:
    """A borrow-returning user-record `__getitem__` subscript source
    (`r = e[k]` -> `Node& r = e[k];`): the record's bare operator[] `T&`
    lvalue binds a REF_ALIAS decl directly (the prechecked record-getitem
    emit renders it form-BORROW). Index/receiver shapes are the
    record-getitem arm's, widened with a record-typed NAME key -- the
    borrow shim's `T&` key param takes the bare name render. Slices and
    value-returning getitems keep their own arms."""
    if not isinstance(sub, TpySubscript) or isinstance(sub.index, TpySlice):
        return False
    if sub.needs_optional_runtime_check:
        return False
    if _record_getitem_key(analyzer.get_expr_type(sub.obj), analyzer) is None:
        return False
    # The subscript node carries no resolved fi, so the return convention
    # reads off the record's own `__getitem__` in the registry.
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(sub.obj))))
    ri = analyzer.registry.get_record_for_type(t)
    fi = (_record_method_with_parents(ri, "__getitem__", analyzer)
          if ri is not None else None)
    if fi is None or not (call_returns_cpp_ref(analyzer, fi)
                          or _open_ref_return(fi)):
        return False
    if not _f1_record(analyzer.get_expr_type(sub), analyzer):
        return False
    if _record_getitem_idx_recv_ok(sub, locals_, analyzer, pointers):
        return True
    idx = sub.index
    recv_ok = (isinstance(sub.obj, TpyName) and sub.obj.name in locals_
               and sub.obj.name not in pointers)
    return bool(recv_ok and isinstance(idx, TpyName)
                and idx.name in locals_ and idx.name not in pointers
                and _f1_record(locals_.get(idx.name), analyzer))


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
    # A pointer-slot NAME receiver derefs at the name render (`(*acc)[0].x`
    # -- the rebind-slot local's read), so the member-access consumer is
    # safe for it too.
    return bool(_record_getitem_idx_recv_ok(sub, locals_, analyzer, pointers,
                                            ptr_recv_ok=True)
                and _f1_record(analyzer.get_expr_type(sub), analyzer))

def _owned_str_slot(t: TpyType | None, analyzer) -> bool:
    """An owned `str` container element/key/value slot (S5). Only the owned
    nominals are admitted -- `str` and `tpy.String` (both `std::string`
    storage; the form axis treats String as owned, see `_resolved_str_value`):
    a `StrView`/`BytesView` slot makes the container hold views, whose literal
    keys/elements pin to static storage (`view_key_target` threads the key
    type into the literal render) -- a shape this slice does not render; the
    bytes family rides S6."""
    st = _resolved_str_value(t, analyzer)
    return st is not None and (is_str_type(st) or is_string_type(st))

def _owned_bytes_slot(t: TpyType | None, analyzer) -> bool:
    """The owned-str slot's bytes twin (`bytes` -- `std::vector<uint8_t>`
    storage): a value-tuple element / unpack-elem slot whose reads are the
    bare owned vector. `BytesView` slots stay out for the same reason
    `_owned_str_slot` excludes views (a view element would make the
    container hold spans -- unreachable today)."""
    bt = _resolved_bytes_value(t, analyzer)
    return bt is not None and is_bytes_type(bt)


def _value_opt_owned_str(t: 'TpyType | None', analyzer) -> bool:
    """A value-repr `Optional[str]` FIELD slot (`std::optional<std::string>`).

    `_value_opt_scalar` excludes the str family for a PARAM-shape reason (the
    `optional<string_view>` vs `optional<string>` arg split needs the
    `_maybe_convert_opt_view_param` shim); a FIELD has no such split -- its
    storage is always the owned `std::optional<std::string>`, into which a str
    literal assigns bare like any scalar. Scoped to the field sinks for exactly
    that reason: do NOT reuse this at a param/arg position."""
    if not isinstance(t, TpyType):
        return False
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and not t.uses_pointer_repr()):
        return False
    return _owned_str_slot(unwrap_readonly(t.inner), analyzer)

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
    this slice's emit assumes, and it is not lowered yet (unlike the Own
    unwrap in `_f1_record`, which admits Own where this deliberately does
    not)."""
    return _container_elem_family(
        t, analyzer,
        lambda a: _eligible_scalar(a) or _owned_str_slot(a, analyzer),
        span_ok=True)

def _container_del_recv(t: 'TpyType | None', analyzer) -> bool:
    """A container receiver for `del c[k]`, ELEMENT-BLIND. The del emit
    (`::tpy::__delitem__(c, k)`) never constructs, converts or reads the
    element slot, so the key alone decides the render -- the same reasoning
    `_any_value_dict` already applies to a `dict[K, Any]` value, generalized
    to every element family. Key shapes still ride the shared dict slice, and
    `Own[container]` still rejects (move-in ABI)."""
    return _container_elem_family(t, analyzer, lambda _a: True,
                                  span_elem_ok=lambda _a: True)


def _dict_key_shape_ok(key: 'TpyType', analyzer) -> bool:
    """The ADMITTED dict/set key slice, written once for the dict-literal
    gate, the container elem-family dispatch, and the subscript reject
    namer: fixed-int / runtime-BigInt / owned-str / F1-record / Any keys --
    every render is key-type-neutral (index-position exprs gate their own
    shapes). `_any_value_dict` deliberately keeps the narrower int/BigInt/
    str trio: record/Any-KEYED Any-dict writes have no witness."""
    kb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(key)))
    return (is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
            or _owned_str_slot(key, analyzer)
            or _f1_record(kb, analyzer)
            or isinstance(kb, AnyType)
            # A VALUE-TUPLE key (`dict[tuple[Int32, Int32], str]`): the
            # literal key renders its spelled `std::tuple<...>{...}` and
            # the membership/subscript renders are key-type-neutral.
            or _value_tuple(kb, analyzer) is not None
            # A VIEW key (`set[StrView]` / `dict[BytesView, T]`): the
            # key-position renders thread `view_key_target`, so a str
            # literal lands bare and a bytes
            # literal takes its static view spelling
            # (`::tpy::bytes_literal("hello", 5)`).
            or is_str_view_type(kb) or is_bytes_view_type(kb)
            # An OWNED-bytes key (`dict[bytes, T]`): no view target, so a
            # literal key keeps the default owned spelling
            # (`bytes_literal_owned`) -- key-type-neutral renders.
            or is_bytes_type(kb)
            # An ENUM key (`dict[Color, Int64]` / `set[Color]`): the enum
            # value renders bare like a scalar in every key position (a
            # member spelling `Color::RED` from the shared `enum_cpp_name`,
            # a variable bare), so the key-type-neutral renders hold.
            # `Own[enum]` stays out -- `_eligible_enum` does not peel Own.
            or _eligible_enum(kb, analyzer) is not None
            # An open-T key inside a generic body (`dict[T, int]` under
            # `[T: Hashable]`): the key renders by name per instantiation
            # and every consumer render is key-type-neutral; a T index is
            # never runtime-BigInt, so no narrow wrap can fire on it.
            or _is_type_param_slot(key))

_NATIVE_GETITEM = "tpy::__getitem__"


def _native_getitem_index_param(t: 'TpyType', analyzer) -> 'TpyType | None':
    """The INDEX/KEY parameter of `t`'s `__getitem__` when that subscript
    resolves to the shared builtin `::tpy::__getitem__(recv, i)`, else None
    -- the membership fact of the element family, read off the resolved stub
    rather than named per category.

    What it excludes, and why each is a different render, not a different
    category: `set` has no `__getitem__` at all; `bytes` / `bytearray` /
    `BytesView` resolve to `tpy::bytes_getitem`; the str family resolves to a
    `cpp_template`; a user record with `__getitem__` (`tplib.ArrayList`)
    resolves to an ordinary TPy method, emitted as the member/free call its
    own `FunctionInfo` chooses -- forcing `::tpy::__getitem__(al, i)` on it
    would be wrong code. The slice overloads (`basic_slice` / `slice`) carry
    their own templates and are skipped by the same test."""
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None:
        return None
    for fi in ri.get_method_overloads("__getitem__"):
        if (fi.native_function and fi.native_name == _NATIVE_GETITEM
                and len(fi.params) == 1):
            return fi.params[0].type
    return None


def _native_getitem_key(t: 'TpyType', idx_param: 'TpyType', analyzer
                        ) -> 'TpyType | None':
    """The receiver's KEY type when its builtin subscript takes a key rather
    than an integer index (`dict.__getitem__(self, key: readonly[K])`), else
    None. The stub's parameter names the record type param, so the concrete
    key is the type arg bound to it -- no positional guess about which arg is
    the key.

    `TypeDef.element_of` is the first-class channel for the ELEMENT half of
    this fact; the registry has no `key_of` sibling to read the KEY off (filed
    in TODO), so the binding is recovered here from the stub instead."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(idx_param)))
    if not isinstance(u, TypeParamRef):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    names = list(getattr(ri, "type_params", None) or ())
    if u.name not in names:
        return None
    args = getattr(t, "type_args", ()) or ()
    i = names.index(u.name)
    return args[i] if i < len(args) and isinstance(args[i], TpyType) else None


def _container_elem_family(t: 'TpyType | None', analyzer, elem_ok,
                           *, span_ok: bool = False,
                           span_elem_ok=None, dict_key_ok=None) -> bool:
    """The shared native-indexable dispatch behind the per-element-family
    predicates (`_container_scalar_read` / `_bytes_elem_container` /
    `_container_record_elem`): a receiver whose `__getitem__` IS the builtin
    `::tpy::__getitem__` admits on `elem_ok` over the element the type system
    reports, and a KEYED one (dict) additionally on the shared key slice
    (fixed-int / runtime-BigInt / owned-str). `Own[container]` always rejects
    (move-in ABI -- its receiver is a `T&&`, not the borrow this emit
    assumes).

    This is NOT the reference axis: the VALUE-typed `Span` and `varargs`
    belong precisely because the fact holds for them -- their element read
    renders the same bounds-safe `::tpy::__getitem__(recv, i)` a list's does
    -- while the reference-typed `set` is out because it has no
    `__getitem__`. The value-form split is what the two element gates key
    on: a view's element slice is the scalar one under `span_ok`, and a
    caller with a witnessed non-scalar view element passes its own
    `span_elem_ok` (the record family). str / bytes span elements are not
    lowered yet. Adding an element family means one new thin front, not a
    fourth copy of this dispatch."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    idx_param = _native_getitem_index_param(t, analyzer)
    if idx_param is None:
        return False
    elem = t.get_element_type()
    if not isinstance(elem, TpyType):
        return False
    if t.is_value_type():
        args = getattr(t, "type_args", None)
        if span_elem_ok is not None:
            return bool(args) and span_elem_ok(args[0])
        # An OWNED-str element joins the scalar slice: it reads bare off a
        # view exactly as it does off the list both `span_ok` callers admit
        # it in. VIEW-typed str elements stay out (the static-storage pin).
        return (span_ok
                and (_eligible_scalar(_peel_readonly(elem))
                     or _owned_str_slot(_peel_readonly(elem), analyzer)))
    key = _native_getitem_key(t, idx_param, analyzer)
    if key is not None:
        # A caller with a witnessed non-shared key slice (the setitem
        # write's open-K generic field) passes its own dict_key_ok.
        if not (dict_key_ok(key) if dict_key_ok is not None
                else _dict_key_shape_ok(key, analyzer)):
            return False
    return elem_ok(elem)

def _bytes_elem_container(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is OWNED `bytes` -- admitted for
    subscript READS only (the io.py `chunks[0]` family). The element lvalue
    (`const std::vector<uint8_t>&`) lands bare in every admitted sink: an
    owned decl/return copies implicitly, a view-resolved binding / span arg
    converts implicitly, compare/print wrap by type -- so the read is STORAGE
    form (never the S6 `::tpy::Bytes` view wrap). Writes / literals /
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
    them VALUE but they do not land bare), so both stay off this arm.
    `Span` keeps the shared `span_ok` scalar-only arm."""
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
    generic-record-element containers reject at `_f1_record` -- those reads
    take a different wrap."""
    def elem_ok(a: 'TpyType | int') -> bool:
        return _f1_record(a, analyzer) if isinstance(a, TpyType) else False
    return _container_elem_family(t, analyzer, elem_ok, span_elem_ok=elem_ok)

def _container_opt_record_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a pointer-repr `Optional[F1-record]`
    (`dict[str, P | None]` / `list[P | None]`): iteration yields the
    STORAGE-form `std::optional<P>` element, so the loop var registers in
    the storage-opt binding set (reads render the bare optional, `T*` arg
    slots lift via optional_to_ptr)."""
    def elem_ok(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
        return (isinstance(au, OptionalType) and au.uses_pointer_repr()
                and _f1_record(_unwrap_own(unwrap_readonly(au.inner)),
                               analyzer))
    return _container_elem_family(t, analyzer, elem_ok)

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
    # span_elem_ok: a Span-of-container receiver (`s[0][0]` on
    # `Span[Array[Int32, 2]]`) reads the same checked `__getitem__`
    # element lvalue -- the render is receiver-family-blind.
    return _container_elem_family(t, analyzer, container_elem,
                                  span_elem_ok=container_elem)

def _container_genrec_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a generic-recursive-alias
    instance (`dict[K, DictTree[K, V]]` -- open or concrete): the
    view/iteration renders are element-family-blind (`auto&& v = *__beg;`),
    and the loop var's reads ride the wrapper rows. The genrec sibling of
    `_container_record_elem`."""
    def genrec_elem(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        a = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
        if isinstance(a, OwnType):
            a = unwrap_readonly(a.wrapped)
        return isinstance(a, RecursiveAliasInstanceType)

    def genrec_dict_key(k: 'TpyType | int') -> bool:
        # An OPEN generic key (`dict[K, DictTree[K, V]]` in a generic
        # body) admits alongside the shared slice: the values()-view
        # iteration this predicate gates is key-blind.
        return (isinstance(k, TypeParamRef)
                or _dict_key_shape_ok(k, analyzer))

    return _container_elem_family(t, analyzer, genrec_elem,
                                  dict_key_ok=genrec_dict_key)

def _container_tparam_elem(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value is a bare type-param (`list[T]` inside
    a generic record body): the element subscript yields the per-instantiation
    `T&` lvalue, bindable as a REF_ALIAS local (`v = self.items[self.pos]` ->
    `T& v = ::tpy::__getitem__(this->items, this->pos);`). The open-T sibling
    of `_container_record_elem`."""
    return _container_elem_family(
        t, analyzer, lambda a: isinstance(a, TpyType)
        and _is_type_param_slot(a))

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
                               unwrap_send_sync(args[0]))), AnyType)
                           # A VIEW element (`set[StrView]` / `set[BytesView]`
                           # -- the view-key family): inserts thread the view
                           # target into the literal arg render.
                           or is_str_view_type(unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(args[0]))))
                           or is_bytes_view_type(unwrap_readonly(
                               unwrap_ref_type(unwrap_send_sync(args[0]))))
                           # An ENUM element (`set[Color]`): the value
                           # renders bare like a scalar at every method
                           # arg/result position (`out.insert(c)`), the same
                           # slice `_container_value_leaf_read` already
                           # admits for a list/dict-value enum element.
                           or _eligible_enum(args[0], analyzer) is not None
                           # An open-T element (`set[T]` in a generic body):
                           # the method receiver renders bare; per-method
                           # args and results still gate (the list family's
                           # open-T admission).
                           or isinstance(unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(args[0]))), TypeParamRef))

def _cpp_noncopyable_type(t: 'TpyType | None', analyzer) -> bool:
    """A type C++ cannot copy, from sema facts only: @nocopy, `__del__`
    (deletes copy ops in C++ though sema's nocopy system doesn't track it),
    or a noncopyable field, with the `__copy__` escape hatch."""
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
    """A @nocopy member anywhere in the element slot forces the
    make_vector/make_ordered_*
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
    field access (`unbound_self_parent_type` set), which renders through a
    receiver-INDEPENDENT spelling, `this->{parent.to_cpp()}::{field}`. The
    syntactic class-name receiver has
    no value type at all, so none of the receiver-shape rows can describe it;
    this is its own admission, deliberately NOT a row inside
    `_field_receiver_ok` (whose 70-odd callers read the receiver binding).

    Every other field marker takes its own emit path and is excluded:
    property / dyn-attr are resolved BEFORE the unbound-self spelling,
    module-var / class-constant after it, and a deref chain or Optional
    null-check would wrap a render this arm spells whole.

    The receiver SPELLING (`this` vs a resumable frame's `__self`) is the
    lowering arm's check, not admission's."""
    if not isinstance(e, TpyFieldAccess) or e.unbound_self_parent_type is None:
        return False
    # A @native field RENAME is excluded: the `native_field_name` override
    # does not reach the unbound-self spelling, which would name the
    # UNRENAMED member and emit uncompilable C++ (BUGS.md). The shape
    # rejects until that is fixed.
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
    marker is clear, so the render is the plain indirect access `p->field`),
    with no special-emit marker -- read or write position. The receiver's
    pointer-vs-reference shape (`->` vs `.`) is decided at lowering from the
    pointer set; eligibility only needs the receiver binding to be in the slice.
    An UNPROVEN Optional access carries `needs_optional_runtime_check` and is
    rejected by the marker guard here -- its `deref_check` face rides
    `_optional_checked_field` at the positions that render it. The
    property-setter / `__setattr__` markers guard the write position (a
    property/setattr field assign takes a method-call emit)."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    recv = e.obj
    if not isinstance(recv, TpyName):
        return False
    return (_f1_record(declared.get(recv.name), analyzer)
            or _optional_ptr_borrow_name(recv, declared, analyzer) is not None
            # An `Own[P | None]` param: the same proven `->` access, spelled
            # through optional<P>::operator-> (the pointers seed).
            or _own_storage_opt_param(declared.get(recv.name),
                                      analyzer) is not None)

def _tparam_protocol_field_recv_ok(e: TpyExpr, declared: dict[str, TpyType],
                                   bounds: 'dict | None') -> bool:
    """`item.value` off a protocol-BOUND type-param binding (`item: T` under
    `[T: HasValue]`). Inside the template the receiver is a
    `param_val_or_ref_t<T>` value-or-reference -- never a pointer -- and the
    concept requires the member, so the field render is the plain
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
    the return convert) -- both render at the positions that admit the read
    (the str-family return slot, a print / f-string arg). A `String`
    field resolves INSIDE the slice but is excluded here by the
    `is_str_type`/`is_str_view_type` filter below -- its owned form has no
    view sink to convert at. An unbound-self `BaseN.field` read joins the
    row: its render is the same bare member read behind a fixed qualifier."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_receiver_or_unbound_self_ok(e, declared, analyzer)):
        return False
    return _str_view_family_value(analyzer.get_expr_type(e), analyzer)

def _str_view_family_value(t: TpyType | None, analyzer) -> bool:
    """The str/StrView slice of `_resolved_str_value` -- `String` resolves
    inside the slice but is excluded (its owned form has no view sink to
    convert at, so no body arm renders it)."""
    st = _resolved_str_value(t, analyzer)
    return st is not None and (is_str_type(st) or is_str_view_type(st))

def _bytes_field_value_read(e: TpyExpr, declared: dict[str, TpyType],
                            analyzer) -> bool:
    """A value-position read of a bytes-family field off an admitted receiver
    (`recv.field`, `_field_receiver_ok`) -- the bytes sibling of
    `_str_field_value_read`. An owned `std::vector<uint8_t>` member reads bare
    as STORAGE, a `BytesView` (span) member as BORROW; the render is bare
    `.field` at every position that admits the read (print sink, compare/concat
    operands, membership needle). An unbound-self `BaseN.field` read joins
    the row like its str twin."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_receiver_or_unbound_self_ok(e, declared, analyzer)):
        return False
    bt = _resolved_bytes_value(analyzer.get_expr_type(e), analyzer)
    return bt is not None and (is_bytes_type(bt) or is_bytes_view_type(bt))

def _const_exact_field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType],
                                   analyzer) -> bool:
    """`_field_receiver_ok` minus Optional-ptr borrow-name receivers -- for
    the const-SPELLING sinks (storage-tuple aliases, union locals from
    fields, borrow-tuple returns), whose emitted decl spells the receiver's
    const verdict. An Optional-ptr receiver's const-ness comes from
    `seed_param_locals`' const_indirect_locals seeding (readonly annotation
    or the DEEP-const verdict); the borrow-local decl sink reads that via
    `_opt_ptr_param_deep_const` and admits these receivers separately, while
    the remaining sinks keep the reject pending their own const wiring.
    Render-const-blind consumers (field reads/writes, arg lifts, reseats,
    MIL copies) keep the plain `_field_receiver_ok`."""
    if not _field_receiver_ok(e, declared, analyzer):
        # An explicit `Ptr[record]` receiver (`fl = sector.flags` on
        # `sector: Ptr[Sector]`, `pic = seg.sector_front.ceil_pic` off a Ptr
        # FIELD) spells its const-ness the same way a reference receiver
        # does: the verdict comes from the init's raw sema type, which
        # carries `readonly[..]` for a `Ptr[readonly[T]]` binding.
        return _ptr_value_field_recv_ok(e, declared, analyzer)
    return _optional_ptr_borrow_name(e.obj, declared, analyzer) is None


def _storage_tuple_alias_src_ok(init: TpyExpr, lc, declared: dict[str, TpyType],
                                analyzer) -> bool:
    """The source-side gate for an `auto&&` storage-tuple alias, per shape.

    A FIELD source keeps the const-spelling receiver gate, because the decl
    below derives the alias's const-ness from it. So does a subscript off a
    FIELD receiver (`self.store[k]`), whose const verdict comes from the
    `_btuple_const_storage` walk -- the counterpart of
    `is_const_storage_source` -- that the decl runs beside it.

    The plain-NAME receiver and bare NAME shapes are admitted CONST-FREE only:
    deriving their const-ness would mean reproducing that set's whole consumer
    topology, not just its value here. Rejecting const sources keeps them from
    contributing a member at all, which is what makes those arms
    const-topology-neutral.

    Both const-free arms also consult `const_storage_tuple_locals`, where a loop
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
        if isinstance(recv, TpyFieldAccess):
            return True
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
    (the runtime-check render over the already-`T*` receiver;
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

def _optional_checked_recv_call(recv: TpyExpr, analyzer) -> bool:
    """The checked-receiver core shared by the field and method flavors:
    a BORROW-returning ptr-repr Optional[F1-record] CALL whose raw `T*`
    result feeds `::tpy::deref_check(...)` directly. An Own-declared
    return (a materialized storage optional) stays out."""
    if not isinstance(recv, (TpyCall, TpyMethodCall)):
        return False
    fi = recv.resolved_function_info
    if fi is None:
        return False
    frt = (unwrap_readonly(unwrap_send_sync(fi.return_type))
           if fi.return_type is not None else None)
    if isinstance(frt, OwnType):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if not (isinstance(rt, OptionalType) and rt.uses_pointer_repr()):
        return False
    return _f1_record(_unwrap_own(rt.inner), analyzer)

def _optional_checked_field_over_call_ok(e: TpyExpr, analyzer) -> bool:
    """An UNPROVEN field access whose receiver is a BORROW-returning
    ptr-repr Optional CALL (`find(points, 5).x` -- sema could not prove
    the result non-None): the raw `T*` result is wrapped --
    `::tpy::deref_check(find(...)).x` -- the call sibling of the
    Optional-ptr NAME receiver."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if not e.needs_optional_runtime_check:
        return False
    if not _field_markers_clean(e, allow_optional_check=True):
        return False
    return _optional_checked_recv_call(e.obj, analyzer)

def _optional_checked_field_over_field_ok(e: TpyExpr,
                                          declared: dict[str, TpyType],
                                          analyzer) -> bool:
    """An UNPROVEN field access whose receiver is a clean field READ of a
    STORAGE `Optional[F1-record]` member (`h.opt.x` where sema could not
    prove `h.opt` non-None): the whole optional lvalue is wrapped --
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
    `.field` read or write. Each marker takes its own emit path, out of the slice.
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
    """The field's DECLARED type off the gate's declared map, for the
    admitted receiver shapes (a record / proven Optional-ptr NAME; the
    receiver gate pinned that), inherited fields included. Consumers type on
    it rather than the flow-narrowed expr type, so a narrowed Optional/union
    field -- whose read takes the `(*recv.field)` unwrap -- types at the
    un-narrowed declared type and rejects at the caller's family check.

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
    rt = _binding_peel(base)
    if isinstance(rt, OptionalType):
        if rt.inner.is_value_type():
            return None
        rt = rt.inner
    if isinstance(rt, PtrType):
        # An explicit `Ptr[record]` binding declares the same members as the
        # pointee; which of `->field` / `deref_check(recv).field` renders is
        # the field arm's call off `ptr_non_null`, not a difference in what
        # the field IS. Receiver ADMISSION stays with the callers -- the ones
        # that do not name a Ptr receiver shape reject before reaching here.
        rt = unwrap_readonly(rt.pointee)
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

def _unbound_self_field_decl_type(e: TpyExpr, analyzer) -> 'TpyType | None':
    """The declared field type of an UNBOUND-SELF access (`BaseN.field`),
    which `_field_decl_type` cannot answer -- there is no receiver binding to
    walk, only the parent type the access carries."""
    if not (isinstance(e, TpyFieldAccess)
            and e.unbound_self_parent_type is not None):
        return None
    record = analyzer.registry.get_record_for_type(e.unbound_self_parent_type)
    if record is None:
        return None
    for f in reversed(analyzer.registry.get_all_fields(record)):
        if f.name == e.field:
            return f.type
    return None

def _container_field_bare_read(e: TpyExpr, declared: dict[str, TpyType],
                               analyzer) -> bool:
    """A container FIELD read whose bare member render IS the whole
    argument -- the `std::ranges::contains` haystack and the callable-field
    call's `T&` arg slot. The caller witnesses its own face; DECLARED-type
    keyed so a narrowed Optional-container read (which unwraps) stays
    out."""
    return (isinstance(e, TpyFieldAccess)
            and _plain_container_read(_field_decl_type(e, declared, analyzer))
            and _field_receiver_ok(e, declared, analyzer))

def _method_member_cpp(fi, name: str) -> str:
    """The member-name rule for a resolved call: the @native rename over the
    escaped source name (a native FUNCTION's rename rides
    native_function_name instead, so it never lands here)."""
    return (fi.native_name if fi.native_name and not fi.native_function
            else escape_cpp_name(name))

def _subscript_container_recv_type(recv: TpyExpr, locals_: dict[str, TpyType],
                                   analyzer,
                                   narrowed_ok: bool = False
                                   ) -> 'TpyType | None':
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
            and (_field_receiver_ok(recv, locals_, analyzer)
                 # A deeper chain of plain record members
                 # (`self.scene.objects[i]`) resolves the same way: the
                 # container binding is the last link's DECLARED type and
                 # the receiver renders as the flat postfix chain the
                 # Ptr-field arm already composes.
                 or (_chained_field_read_ok(recv, analyzer)
                     and _witness("subscript.recv_field_chain"))
                 # ... and a member off an explicit `Ptr[record]` binding
                 # (`sector.flags[i]` on `sector: Ptr[Sector]`): the field
                 # arm renders `->field` / `deref_check(recv).field` off
                 # `ptr_non_null`, and the container binding is the field's
                 # declared type exactly as for a reference receiver.
                 or (_ptr_value_field_recv_ok(recv, locals_, analyzer)
                     and _witness("subscript.recv_ptr_field")))):
        dt = _field_decl_type(recv, locals_, analyzer)
        if isinstance(dt, TpyType) and contains_type_param(dt):
            # A monomorphized generic receiver's field declares the RAW T
            # (`box.value[0]` on Box[list[int]]) or a T-bearing composite
            # (`box._items[0]` on `_items: list[T]`); the substituted expr
            # type carries the concrete spelling. A type-param-bearing
            # decl is never a pending-resolution carrier (pending types
            # are literal-seeded locals), so the substitution cannot skip
            # the declared-type pending semantics this resolver otherwise
            # preserves.
            et = analyzer.get_expr_type(recv)
            if et is not None:
                return et
        dtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
               if isinstance(dt, TpyType) else None)
        if (narrowed_ok and isinstance(dtu, OptionalType)
                and dtu.uses_pointer_repr()):
            # A sema-NARROWED Optional[container] field receiver
            # (`a2.coord[0]` after the assert-narrow): the receiver read
            # renders the `(*recv.field)` deref (the narrowed-field arm),
            # so it types at the narrowed INNER. Opt-in (`narrowed_ok`)
            # because the callers that do NOT thread it (the print-form
            # classifier, the borrow-element shapes) have no witnessed
            # narrowed render.
            et = analyzer.get_expr_type(recv)
            etu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
                   if et is not None else None)
            if (etu is not None and not isinstance(etu, OptionalType)
                    and etu == unwrap_readonly(dtu.inner)):
                return et
        return dt
    if _module_var_recv(recv, locals_, analyzer):
        # A module-attr GLOBAL receiver (`del os.environ[k]`): typed at
        # the registered variable's declared type; renders through the
        # module-variable arm's `(*slot)` deref.
        return analyzer.get_expr_type(recv)
    return None


def _container_elem_lvalue_subscript(e: TpyExpr, locals_: dict[str, TpyType],
                                     analyzer) -> bool:
    """`e` is a plain container-ELEMENT subscript whose read is a live lvalue:
    a single index (no slice), no Optional runtime check pending on it, off a
    receiver shape `_subscript_container_recv_type` resolves, and an accessor
    that hands back storage rather than a value (a native container, or a user
    `__getitem__` returning a C++ reference). Positions that alias or take the
    address of an element read need exactly this proof -- a slice yields a fresh
    view, and a by-value accessor result or an unproven-Optional element read
    would address a dying temporary.

    A TUPLE receiver is excluded (the carve-out `reads_storage_form_optional`
    makes for the same reason): a borrow-form tuple's `std::get<N>` yields the
    element POINTER, not the referent, so aliasing it drops a deref and
    addressing it yields `T**`. Whether a given tuple binding renders borrow or
    storage form is walk state this predicate cannot see, so every tuple is out."""
    if not (isinstance(e, TpySubscript)
            and not isinstance(e.index, TpySlice)
            and e.slice_function_info is None
            and not e.needs_optional_runtime_check):
        return False
    recv_t = _subscript_container_recv_type(e.obj, locals_, analyzer)
    if recv_t is None or isinstance(unwrap_qualifiers(recv_t), TupleType):
        return False
    return (e.getitem_function_info is None
            or call_returns_cpp_ref(analyzer, e.getitem_function_info))


def _narrowed_ptr_opt_recv(recv: TpyExpr, recv_t: 'TpyType | None',
                           pointers) -> 'TpyType | None':
    """The narrowed INNER container of a None-narrowed pointer-repr
    `Optional[container]` NAME receiver, or None.

    The name binds `T*` and the subscript renders through the `(*recv)`
    deref, so the family / element / dunder checks downstream must key on
    the inner container -- keying on the Optional misses `__getitem__` and
    drops to the raw `operator[]`. The un-narrowed flavor carries
    `needs_optional_runtime_check` and rejects upstream, so reaching here
    implies sema's proof. Gated on the pointer BINDING set, which is what
    the deref render itself keys on.
    """
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t)))
           if recv_t is not None else None)
    if (isinstance(rtu, OptionalType) and rtu.uses_pointer_repr()
            and isinstance(recv, TpyName) and recv.name in pointers):
        return rtu.inner
    return None


def _narrowed_ptr_opt_name(recv: TpyExpr, declared: dict[str, TpyType],
                           pointers) -> bool:
    """The `declared`-typed twin of `_narrowed_ptr_opt_recv`: a NAME bound to a
    pointer-repr `Optional[T]` whose subscript reads through the `(*recv)`
    deref. Used where the receiver family is resolved off the DECLARED slot
    (the record-getitem arm, the general subscript emit) rather than off the
    container-receiver resolver."""
    if not (isinstance(recv, TpyName) and recv.name in pointers
            and recv.name in declared):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[recv.name])))
    return isinstance(t, OptionalType) and t.uses_pointer_repr()


def _record_method_with_parents(ri, name: str, analyzer):
    """`ri.get_method(name)` with the parent-traversing fallback: an
    INHERITED dunder (incl. through a GENERIC parent, which `get_method`
    misses on the subclass record) resolves through the registry's
    overload traversal. The four record-dunder predicates share it so
    their claimed same-slice parity actually holds."""
    m = ri.get_method(name)
    if m is None:
        ovl = analyzer.registry.get_method_overloads_with_parents(ri, name)
        m = ovl[0] if ovl else None
    return m


def _record_getitem_key(obj_type: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The key param type of a user-record subscript receiver's `__getitem__`
    (so `recv[index]` spells the record's generated bare `operator[]`), or
    None. Non-@native user records (including monomorphized generic records
    like `FixStr[16]`, whose C++ operator[] renders bare identically -- no
    type-args guard is needed): a @native record wrapping an STL type has a
    C++ operator[] taking size_t, which needs a cast."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return None
    m = _record_method_with_parents(ri, "__getitem__", analyzer)
    if m is not None and len(m.params) >= 1:
        # Method params exclude the implicit self, so params[0] is the key.
        return m.params[0].type
    return None


def _record_setitem_value(obj_type: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The VALUE param type of a user-record subscript receiver's `__setitem__`
    (so `recv[key] = v` spells the no-container fallback
    `::tpy::__setitem__(recv, key, v)`), or None. Same non-@native restriction
    as `_record_getitem_key` (monomorphized generic records included) -- a
    native STL wrapper keeps its own emit path."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return None
    m = _record_method_with_parents(ri, "__setitem__", analyzer)
    if m is not None and len(m.params) >= 2:
        # (key, value) after the implicit self -- the value is the last param.
        return m.params[-1].type
    return None


def _record_has_delitem(obj_type: 'TpyType | None', analyzer) -> bool:
    """A user record defining `__delitem__` (so `del recv[key]` spells
    `::tpy::__delitem__(recv, key)`). Non-@native only, and monomorphized
    generic records included -- the del render spells the receiver name bare
    and never the record type, so an instantiation reaches no part of the
    emit. Same slice as `_record_getitem_key`, which likewise has no
    type-args guard."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(obj_type)))
    if not (isinstance(t, NominalType) and t.is_user_record):
        return False
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return False
    return _record_method_with_parents(ri, "__delitem__",
                                       analyzer) is not None

def _f2_reseat_ok(init: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """A pointer-local reseat value: an lvalue field read off an F1-record receiver
    whose field is itself an F1-record (the new pointee), so it reseats as
    `p = &(recv.field);`. rvalue / `None` / name-alias reseats need the rebind-slot
    (`__slot_N`) machinery and reject here. The borrow-CALL reseat flavor
    (`first = &(get_first(data));`) has its own link in the reseat chain --
    this predicate's consumers lower through the FIELD-source builder."""
    return (_field_receiver_ok(init, declared, analyzer)
            and _f1_record(analyzer.get_expr_type(init), analyzer))

def _f1_param_lvalue_reseat_ok(init: TpyExpr, pointee: TpyType,
                               declared: dict[str, TpyType], lc, analyzer) -> bool:
    """A pointer-repr `Optional` local reseat source that lifts via `&(name)`: a
    bare record PARAM name whose stripped type is the exact F1-record pointee. A
    param renders as a plain lvalue (`T&` / `const T&`), so `&(p)` is well-formed
    -- the pointer-local rebind's address-of arm. A pointer-local source
    (bare copy) or an owned-local / rvalue source takes a different emit and
    rejects here."""
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
    REF_ALIAS is excluded -- not a pointer, so it emits differently."""
    if not isinstance(e, TpyName):
        return False
    if e.name in pointers:
        return True
    t = declared.get(e.name)
    return isinstance(t, OptionalType) and t.uses_pointer_repr()

def _tuple_literal_has_ref_elements(e: TpyTupleLiteral,
                                    slot: 'TupleType') -> bool:
    """The tuple literal's `has_ref_elements` verdict, which picks the
    BORROW slot ladder (`std::tuple<T*, ...>`) over the plain value tuple:
    any sema-annotated non-VALUE capture, any TypeParamRef slot, or -- with
    no annotation at all -- any element slot that is neither a value type nor
    an `Own[...]`. One definition, so the container-element and field-write
    arms split on the same fact."""
    if e.elem_capture:
        return any(c is not TupleElemCapture.VALUE for c in e.elem_capture)
    return any(isinstance(t, TypeParamRef)
               or (not t.is_value_type() and not isinstance(t, OwnType))
               for t in slot.element_types)


def _wrapper_ref_tuple_return(rt: 'TpyType | None',
                              analyzer) -> 'TupleType | None':
    """The REFERENCE-element tuple RETURN slot (`-> tuple[Tree[Int32],
    Int32]` -> `std::tuple<Tree<int32_t>&, int32_t>`): a wrapper-union
    element with NO Own marker borrows by C++ reference (`&`, not `T*` --
    wrapper structs store by value, so the borrow form is a reference
    member). Every element must be such a wrapper or a value scalar; the
    literal source renders the spelled borrow brace (`{t, 0}`, members
    bare). None otherwise."""
    if not isinstance(rt, TpyType):
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    if not isinstance(t, TupleType):
        return None
    has_wrapper = False
    for e in t.element_types:
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if isinstance(eu, OwnType):
            return None
        if _wrapper_union_like(eu, analyzer) is not None:
            has_wrapper = True
            continue
        if not _eligible_scalar(eu):
            return None
    return t if has_wrapper else None


def _own_ptr_union_element(e: 'TpyType', analyzer) -> bool:
    """An `Own[A | B]` tuple element whose union is pointer-variant: the Own
    marker pushes the element to STORAGE form (`std::variant<A, B>`), so the
    whole tuple is a by-value `std::tuple<std::variant<A, B>, ...>` -- borrow
    and storage differ only by the per-element `to_ptr_variant` an unpack
    applies."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if not isinstance(u, OwnType):
        return False
    return _eligible_ptr_union(unwrap_readonly(u.wrapped), analyzer) is not None


def _owned_container_slot(t: 'TpyType | None') -> bool:
    """An owned CONTAINER slot on the reference axis once the Own marker is
    peeled: a self-contained by-value C++ container, so it moves in and out of
    a storage tuple exactly like an owned record does."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _f1_container_ref(u)


def _own_container_element(e: 'TpyType') -> bool:
    """An `Own[list/dict/set]` tuple element: the Own marker pushes the owned
    container to STORAGE form, so the tuple is a by-value
    `std::tuple<std::vector<double>, ...>` whose literal returns the same
    spelled brace-init the pointer-repr flavors do. The `Own[record]` sibling
    is already a by-value slot that `_value_tuple_return` admits."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    return isinstance(u, OwnType) and _owned_container_slot(u.wrapped)


def _own_storage_tuple_return(rt: 'TpyType | None',
                              analyzer) -> 'TupleType | None':
    """An `Own[tuple[...]]` STORAGE return slot with a NON-VALUE member
    (`-> Own[tuple[str, Resource]]` -> a by-value `std::tuple<...>`): a
    tuple LITERAL whose members pass the storage-direct container-element
    rules returns the spelled brace-init
    (`return std::tuple<std::string, Resource>{...};`). None otherwise;
    all-value tuples keep the `ret_value_tuple` family. The per-element-own
    sibling (`-> tuple[Own[P | None], Int32]`) rides too: the Own element
    pushes the whole tuple to storage form (`own_tuple_target`), so
    the return renders the same spelled storage brace-init
    (`std::tuple<std::optional<P>, int32_t>{P(42), 99}`)."""
    if not isinstance(rt, OwnType):
        t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
             if isinstance(rt, TpyType) else None)
        if (isinstance(t, TupleType) and t.is_owned_movable()
                and any(is_own_pointer_repr_optional(unwrap_readonly(e))
                        # ... and the `Own[A | B]` element flavor: Own pushes
                        # the union to its VALUE variant, so the tuple is
                        # storage form and the literal returns the same
                        # spelled brace-init (the variant's converting ctor
                        # absorbs the member rvalue).
                        or _own_ptr_union_element(e, analyzer)
                        # ... and the owned-CONTAINER element flavor, whose
                        # storage tuple holds the container by value.
                        or _own_container_element(e)
                        for e in t.element_types)):
            return t
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt.wrapped)))
    if not isinstance(t, TupleType):
        return None
    members = [unwrap_readonly(unwrap_ref_type(unwrap_send_sync(m)))
               for m in t.element_types]
    if all(m.is_value_type() for m in members):
        return None
    return t


def _ptr_optional_tuple(t: 'TpyType | None',
                        analyzer=None) -> 'TupleType | None':
    """The matched ptr-Optional-element storage tuple, or None: a TupleType
    with a pointer-repr element whose every non-value slot is a pointer-repr
    Optional -- the Own[tuple[T | None, ..]] consuming family's shape key
    (matched-type-or-None like `_eligible_ptr_union` / `_value_tuple`)."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(u, TupleType) and u.has_pointer_repr_element()
            and _tuple_elem_slots_ptr_optional(u)):
        return None
    return u


def _tuple_elem_slots_record_lvalue(e: 'TpyTupleLiteral', slot: 'TupleType',
                                    analyzer) -> bool:
    """The plain-record sibling of the all-Optional borrow admission
    (`_tuple_elem_slots_ptr_optional`): every non-value element slot is a
    plain BORROW_REF pointer-repr record whose element is a simple lvalue --
    the storage-context CONST_REF row (`const P*` slot + `&(c)` lift,
    `_tuple_literal_slot_info`'s storage rule, carried by the borrow ladder's
    `storage_context`). Rvalue members keep the storage-direct arm."""
    if len(e.elements) != len(slot.element_types):
        return False
    for i, t in enumerate(slot.element_types):
        tb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if tb.is_value_type():
            continue
        if isinstance(tb, (OptionalType, UnionType, TypeParamRef)):
            return False
        if (tb.value_form() is not ValueForm.BORROW_REF
                or not TupleType._element_is_pointer_repr(tb)):
            return False
        if not emit_prims.is_simple_lvalue(e.elements[i]):
            return False
    return True


def _tuple_elem_slots_ptr_optional(slot: 'TupleType') -> bool:
    """Every non-value element slot of `slot` is a pointer-repr `Optional`.

    `_tuple_literal_slot_info` forces REF (non-const) for those so the slot
    shape stays uniform, but sends any OTHER non-value simple lvalue to
    CONST_REF in a storage context -- a rule `_lower_borrow_tuple_literal`
    does not carry. Restricting the container-element borrow arm to the
    all-Optional shape keeps it on the branch both ladders agree on; the
    plain-record member (`const T*`) rejects."""
    def elem_ok(t: TpyType) -> bool:
        if t.is_value_type():
            return True
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        return isinstance(bare, OptionalType) and bare.uses_pointer_repr()

    return all(elem_ok(t) for t in slot.element_types)


def _mixed_own_borrow_tuple(t: 'TpyType | None',
                            analyzer) -> 'TupleType | None':
    """The mixed own/borrow F1 tuple, or None: a pointer-repr TupleType
    whose every element is a value type, an `Own[F1-record]` (storage by
    value), a bare F1-record (the borrow `T*` element), or a pointer-repr
    `Optional[F1-record]`. The shape key of the `tuple[Own[Box], Box]`
    param family -- a matching call rvalue binds the slot BARE (the
    element-blind pass), same render as the all-Optional
    `_ptr_optional_tuple` family."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(u, TupleType) and u.has_pointer_repr_element()):
        return None
    for e in u.element_types:
        eu = unwrap_readonly(unwrap_send_sync(e))
        if isinstance(eu, OwnType):
            if not _f1_record(unwrap_readonly(eu.wrapped), analyzer):
                return None
            continue
        if not _f1_tuple_element_ok(e, analyzer):
            return None
    return u


def _renders_own_borrow_tuple(e: TpyExpr, own_locals: 'AbstractSet[str]',
                              analyzer) -> bool:
    """Mirror of codegen context's `renders_own_borrow_tuple`: `e` yields the
    MIXED borrow render of a per-element-Own tuple (`std::tuple<A, B*>` --
    owned elements by value, ref elements as pointers). Only a
    per-element-Own RETURN produces the shape, so it survives in the call
    result, a both-arms ternary of such calls, and a local bound straight
    from one (`own_locals` -- the lc.own_borrow_tuple_locals set). Every
    storage sink materializes the ref element via `tuple_to_storage`, so a
    container element / field / loop var is NOT this shape even though its
    tuple type still carries the Own."""
    if isinstance(e, (TpyCall, TpyMethodCall)):
        fi = e.resolved_function_info
        rt = (unwrap_readonly(fi.return_type)
              if fi is not None and fi.return_type is not None else None)
        return isinstance(rt, TupleType) and rt.is_mixed_own()
    if isinstance(e, TpyIfExpr):
        return (_renders_own_borrow_tuple(e.then_expr, own_locals, analyzer)
                and _renders_own_borrow_tuple(e.else_expr, own_locals,
                                              analyzer))
    if isinstance(e, TpyName):
        return e.name in own_locals
    return False


def _mixed_own_btuple_call(e: TpyExpr, analyzer) -> bool:
    """`e` is a CALL whose result already IS the mixed own+borrow borrow
    render (`std::tuple<A, B*>`) -- owned elements by value, borrowed ones
    pointing at storage the caller keeps alive -- so a borrow-form sink binds
    it directly, with no slot and no lift (materializing would copy the
    borrowed half). An `Own[...]`-declared return is excluded: that one is
    consumed whole and rides the owned-slot arms."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        return False
    fi = e.resolved_function_info
    rt = getattr(fi, "return_type", None) if fi is not None else None
    if rt is None or isinstance(unwrap_readonly(unwrap_send_sync(rt)),
                                OwnType):
        return False
    bt = _f1_tuple(analyzer.get_expr_type(e), analyzer)
    return bt is not None and bt.is_mixed_own()


def _mixed_own_storage_source(e: TpyExpr, slot: 'TupleType',
                              own_locals: 'AbstractSet[str]',
                              analyzer) -> 'TpyExpr | None':
    """The mixed-own SOURCE a storage tuple sink materializes via the
    NON-move `tuple_to_storage<S>(..)` -- a mixed-own-returning call /
    both-arms ternary (a `copy()` wrapper peels: the wrap IS the copy), with
    the Own-collapsed source type equal to the sink's storage tuple `slot`.
    Returns the peeled source expression, or None."""
    src = e
    ca = copy_call_arg(e, analyzer)
    if ca is not None:
        src = ca
    if not isinstance(src, (TpyCall, TpyMethodCall, TpyIfExpr)):
        return None
    if not _renders_own_borrow_tuple(src, own_locals, analyzer):
        return None
    at = analyzer.get_expr_type(src)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if not isinstance(atu, TupleType):
        return None
    # F1-RECORD elements only: an `Own[list]`-element tuple would pass the
    # equality below, but its materialization has no verified render (the
    # own-container boundary pins) -- keep the family keyed like the F3
    # slice.
    if not all(_f1_tuple_element_ok(unwrap_readonly(_unwrap_own(et)),
                                    analyzer)
               for et in atu.element_types):
        return None
    # Element-wise Own-stripped equality: `collapse_tuple_own_elements`
    # deliberately leaves a MIXED tuple alone (its one live shape IS the
    # mixed render), but the SINK slot spells the materialized storage
    # (`tuple[Box, Box]`), so the comparison strips the per-element Own.
    return src if _own_stripped_tuple_eq(atu, slot) else None


def _own_stripped_tuple_eq(a: 'TupleType', b: 'TupleType') -> bool:
    """Element-wise tuple equality with per-element `Own` stripped on both
    sides -- the comparison every mixed-own sink row needs (a mixed source
    type retains its Own markers; the sink slot spells the materialized
    storage)."""
    if len(a.element_types) != len(b.element_types):
        return False
    return all(
        unwrap_readonly(_unwrap_own(ae)) == unwrap_readonly(_unwrap_own(be))
        for ae, be in zip(a.element_types, b.element_types))


def _btuple_literal_elems_rvalue(a: 'TpyTupleLiteral', slot: 'TupleType',
                                 analyzer) -> bool:
    """The plain-record sibling of `_tuple_elem_slots_ptr_optional`: every
    non-value element slot is an F1 record AND its literal element is an
    rvalue source (a fresh ctor call). The CONST_REF storage rule that
    keeps plain-record LVALUE members out (see the sibling's docstring)
    cannot fire on an rvalue -- it rides the `tuple_value_to_borrow`
    source-tuple path, whose per-element admission the borrow builder
    still owns. F1 (not just is_user_record) because the double-convert
    spells the element types."""
    if len(a.elements) != len(slot.element_types):
        return False
    for el, t in zip(a.elements, slot.element_types):
        if t.is_value_type():
            continue
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if not _f1_record(bare, analyzer):
            return False
        if not is_rvalue_source(analyzer, el):
            return False
    return True


def copy_call_arg(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """The single argument of a `copy(x)` builtin call, or None.

    The three copy-render predicates (`copy_construct_source`,
    `copy_ctor_rvalue_source`, `copy_ptr_optional_peel`) each discriminate a
    DIFFERENT branch of that render, but they share this entry test -- keeping
    it in one place stops the guard itself from drifting between them."""
    if not isinstance(e, TpyCall):
        return None
    fi = e.resolved_function_info
    if (fi is None or fi.qualified_name != COPY_QNAME
            or len(e.args) != 1 or e.kwargs):
        return None
    return e.args[0]


def copy_ptr_optional_peel(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """`copy(x)` where `x` is a pointer-repr `Optional` -- the identity case,
    which hands the argument straight back and makes
    no copy at all (the borrow `T*` reaches the sink unchanged; the sink's own
    storage lift is what copies). Returns the inner expression so consumers
    render the bare argument; None when this is not that shape."""
    arg = copy_call_arg(e, analyzer)
    if arg is None:
        return None
    at = analyzer.get_expr_type(arg)
    return (arg if isinstance(at, OptionalType) and at.uses_pointer_repr()
            else None)


def _owned_optional_call_source(value: TpyExpr, ftype: TpyType,
                                analyzer) -> bool:
    """An `Own[T] | None`-returning CALL landing in a pointer-repr
    `Optional[T]` field. The `Own` inner makes the VALUE a value-repr
    `std::optional<T>` while the FIELD is pointer-repr, so the same-repr leg
    cannot see the pair -- yet the assign is BARE, keyed on exactly this
    shape (`is_owned_optional`). One predicate for both halves: the admission
    and the tail's lift-suppression must never drift, because a
    `ptr_to_optional` here would be handed a `std::optional<T>` where it
    takes a `T*` -- ill-formed C++, not a mere spelling difference."""
    if not isinstance(value, (TpyCall, TpyMethodCall)):
        return False
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    vt = analyzer.get_expr_type(value)
    return (isinstance(vt, OptionalType) and not vt.uses_pointer_repr()
            and isinstance(vt.inner, OwnType)
            and vt.inner.wrapped == ftype.inner)


def _f2b_optional_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """An optional-field write `recv.field = <value>`: the target is a pointer-repr
    `Optional[record]` field off an F1-record receiver and the value is either a
    bare borrow `T*` local (`recv.field = ::tpy::ptr_to_optional[_move](p)`, copy
    or move per last-use), a `None` literal (`recv.field = std::nullopt`), a
    CALL returning the same pointer-repr Optional (a borrow `T*` rvalue taking
    the same lift -- `h.value = ptr_to_optional(find_point(pts, 1))`), an
    `Own[T] | None`-returning call assigning BARE
    (`_owned_optional_call_source`), or a
    FIELD read of the same Optional (already `std::optional<T>` STORAGE, so it
    copies bare). The generic tail picks lift-vs-bare off the LOWERED form, so
    both new rows share one emit. A TYPE-PARAM inner is admitted for the
    borrow-`T*` source only.

    A `copy()` wrapper peels first: for a pointer-repr Optional argument the
    copy is the identity, so `copy(src)` and `src` render the same string."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    value = copy_ptr_optional_peel(stmt.value, analyzer) or stmt.value
    if not _f1_record(ftype.inner, analyzer):
        # A TYPE-PARAM inner takes the identical `ptr_to_optional` lift (the
        # lift keys on the field's repr, not the inner), but only for the
        # borrow-`T*` local source -- the None / call / field rows below were
        # never witnessed off a generic field.
        return (isinstance(ftype.inner, TypeParamRef)
                and _is_borrow_ptr_local(value, declared, pointers)
                and _witness("field_write.opt_lift_tparam"))
    if isinstance(value, TpyNoneLiteral) or _is_borrow_ptr_local(
            value, declared, pointers):
        return True
    if _owned_optional_call_source(value, ftype, analyzer):
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

def _storage_tuple_write_source(e: TpyExpr, ft: 'TupleType',
                                storage_tuple_locals: 'AbstractSet[str]',
                                analyzer) -> bool:
    """A STORAGE-form source of the same F3 tuple type at a tuple sink: a
    container-element subscript, a field read, or a storage-form tuple NAME
    (a storage local / seeded read-only global). `needs_tuple_storage_lift`
    is False for each, so the copy is bare -- no `tuple_to_storage`."""
    if isinstance(e, TpySubscript):
        if isinstance(e.index, TpySlice):
            return False
    elif isinstance(e, TpyName):
        if e.name not in storage_tuple_locals:
            return False
    elif not isinstance(e, TpyFieldAccess):
        return False
    at = analyzer.get_expr_type(e)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return atu == ft


def _f1_tuple_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                             storage_tuple_locals: set[str], analyzer) -> bool:
    """A tuple-field write `recv.field = <tuple source>`: an F3 tuple field off
    an F1-record receiver, written from a borrow tuple source (the field-write
    lifts borrow->storage via `tuple_to_storage` -- a copy) or from a
    storage-form source (subscript / field / storage name -- the direct bare
    copy, no wrap; the `Own[tuple]` move arm is not lowered yet)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ft = _f1_tuple(analyzer.get_expr_type(target), analyzer)
    if ft is None:
        return False
    # A tuple LITERAL renders the spelled value-form brace-init and the
    # assign wraps it (`tuple_to_storage<S>(std::tuple<..>{std::move(a), ..})`
    # -- the storage wrap over the literal's all-VALUE-elements path).
    # The REF-element ladder spells its slots differently and stays out.
    if isinstance(stmt.value, TpyTupleLiteral):
        return (len(stmt.value.elements) == len(ft.element_types)
                and not _tuple_literal_has_ref_elements(stmt.value, ft))
    if _storage_tuple_write_source(stmt.value, ft, storage_tuple_locals,
                                   analyzer):
        return True
    return _is_borrow_tuple_source(stmt.value, declared, storage_tuple_locals, analyzer)


def _nested_tuple_field_literal_write_ok(stmt: TpyAssign,
                                         declared: dict[str, TpyType],
                                         analyzer) -> bool:
    """A NESTED-storage tuple field written from a tuple LITERAL
    (`h.q = (9, (8, c))` -> `h.q = std::tuple<...>{9,
    ::tpy::tuple_to_storage<S2>(std::tuple<int32_t, const P*>{8, &(c)})};`):
    the outer tuple has no borrow form, so the spelled brace-init assigns
    directly with NO outer wrap; nested members replay the wrap decision
    per level (the container-literal elem recursion)."""
    if not isinstance(stmt.value, TpyTupleLiteral):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    nt = _nested_storage_tuple(analyzer.get_expr_type(stmt.target), analyzer)
    return (nt is not None
            and len(stmt.value.elements) == len(nt.element_types))


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
    tt = analyzer.get_expr_type(stmt.target)
    ut = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt))) \
        if tt is not None else None
    if isinstance(ut, OptionalType):
        # An Optional[value-tuple] FIELD stores `std::optional<std::tuple
        # <..>>`, whose operator= absorbs the same spelled brace-init the
        # plain tuple field gets (`s.auth = std::tuple<..>{"u", "p"};`).
        tt = ut.inner
    vt = _value_tuple(tt, analyzer)
    if vt is None:
        return False
    return (len(stmt.value.elements) == len(vt.element_types)
            and not _tuple_literal_has_ref_elements(stmt.value, vt))

def _owning_fi(func: TpyFunction, analyzer,
               record_name: str | None) -> 'FunctionInfo | None':
    """The registry FunctionInfo a callable's param verdicts live on -- on the
    owning record for a method, in the function registry otherwise. `[-1]` is
    codegen's own last-overload pick (`_get_method_mutated_params`)."""
    if record_name is not None:
        ri = analyzer.registry.get_record(record_name)
        overloads = ri.get_method_overloads(func.name) if ri is not None else None
    else:
        overloads = analyzer.registry.get_function(func.name)
    return overloads[-1] if overloads else None

def _param_const_verdict(name: str, func: TpyFunction, analyzer,
                         record_name: str | None, attr: str) -> bool:
    """Whether param `name` is in the function's `attr` verdict set
    (`const_borrow_params`, a param-index set on the registry
    FunctionInfo). A method's FunctionInfo
    lives on the owning record (`record_name`), a free function's in the
    function registry -- the same lookup codegen's
    `_get_method_mutated_params` uses. A property pair shares one overload
    list (getter + setter); [-1] is safe only because a getter has no
    non-self params (this lookup is never consulted for it) and the setter's
    non-value param is forced Own[...] (routing around const entirely)."""
    fi = _owning_fi(func, analyzer, record_name)
    verdict = getattr(fi, attr, None) if fi is not None else None
    if not verdict:
        return False
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    return idx is not None and idx in verdict

def _const_verdict_func(name: str, lc) -> TpyFunction:
    """The function whose param const verdict decides how `name` is BOUND.

    Normally `lc.func`, but a nested def captures the enclosing param by
    reference and keeps its outer render, so inside a lambda the verdict
    belongs to the defining function -- the same fact
    `_nested_def_lowering_scope` carries when it unions the outer param
    names into the nested prescan. Asking `lc.func` there answered
    not-const for every capture, which spelled a mutable `std::get<A*>`
    against a deep-const parameter. `lc.func` is asked FIRST so an inner
    param of the same name shadows."""
    if any(n == name for n, _ in lc.func.params):
        return lc.func
    for outer in reversed(lc.capture_funcs):
        if any(n == name for n, _ in outer.params):
            return outer
    return lc.func

def _param_is_deep_const(name: str, func: TpyFunction, analyzer,
                         record_name: str | None = None) -> bool:
    """Whether param `name` carries the const verdict read for its INNER
    surface (`FunctionInfo.const_borrow_params` -- discriminant-only use, no
    address escape), which deep-consts a pointer-variant param's pointees
    (`::tpy::Union<const A*, const B*>`) in the signature and every
    narrowed-member spelling. The separate name is the QUESTION, not a
    second set: `_param_is_const` asks the same verdict for the signature
    spelling and adds the inplace-dunder force."""
    return _param_const_verdict(name, func, analyzer, record_name,
                                "const_borrow_params")

def _opt_ptr_param_deep_const(name: str, func: TpyFunction, analyzer,
                              record_name: str | None) -> bool:
    """An Optional-ptr PARAM receiver spelled `const H*`: seed_param_locals
    seeds const_indirect_locals for a pointer-repr Optional param from the
    readonly annotation (raw-sema ReadonlyType, caught by the upstream
    checks) or the DEEP-const verdict -- this is the inferred half, gated
    to the pointer-repr-Optional param shape that seeding arm covers."""
    pt = next((t for n, t in func.params if n == name), None)
    if pt is None:
        return False
    bare = unwrap_readonly(unwrap_send_sync(pt))
    return (isinstance(bare, OptionalType) and bare.uses_pointer_repr()
            and _param_is_deep_const(name, func, analyzer, record_name))

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
    divergence. Inplace dunders (`__iadd__` ...) take the FORCED
    const-params verdict (`use_const_params` via CONST_PARAMS_METHODS --
    a codegen-side force sema's `const_borrow_params` does not record),
    minus the slices `decide_param_const` short-circuits out from under
    the force -- see `_forced_const_dropped`."""
    if (func.is_method and func.name in CONST_PARAMS_METHODS
            and name != "self"):
        return not _forced_const_dropped(name, func, analyzer, record_name)
    return _param_const_verdict(name, func, analyzer, record_name,
                                "const_borrow_params")

def _forced_const_dropped(name: str, func: TpyFunction, analyzer,
                          record_name: str | None) -> bool:
    """The slices `decide_param_const` drops const for even when the caller
    forces it (`const_params=True`, the inplace-dunder body set) -- asked of
    the canonical decision itself rather than re-derived here. Its
    `reassigned_params` short-circuit is left unfed on purpose: a reassigned
    copy-for-reassign param has no observable counterpart here, since every
    copy-for-reassign type is either a value type, which that body set
    never admits, or a reference type sema forbids rebinding.
    `is_ptr_variant_union` is likewise unfed -- under a forced const both of
    its branches agree on `signature_const`, and only the deep axis splits.

    `Atomic[T].__iadd__` is the shape that needs it: `other` forwards into a
    body-less @native stub, so mutation propagation marks it mutated and the
    param emits non-const while a flat force would call it const."""
    ptype = next((t for n, t in func.params if n == name), None)
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    fi = _owning_fi(func, analyzer, record_name)
    if ptype is None or idx is None or fi is None:
        return False
    return not decide_param_const(
        ptype,
        index=idx,
        pname=name,
        mutated_params=fi.mutated_params,
        addr_escapes_params=fi.addr_escapes_params or frozenset(),
        const_params=True,
    ).signature_const

def _const_borrow_name(name: str, lc) -> bool:
    """The const-borrow-source verdict for a bare name: a param under the
    deep-const or const-borrow verdicts. The third arm
    (ReadonlyType declared type) never fires for admitted subjects -- the
    poly/dyn admission requires the declared entry fully unwrapped, so a
    readonly-declared name rejects before const-ness is consulted."""
    f = _const_verdict_func(name, lc)
    return (_param_is_deep_const(name, f, lc.analyzer, lc.record_name)
            or _param_is_const(name, f, lc.analyzer, lc.record_name))

def _already_pointer_source(expr: TpyExpr, lc) -> bool:
    """`ctx.is_already_pointer_source` mirror: True when `expr` renders as a
    `T*` with no further lifting, so an `&(...)` lift would produce `T**`.

    Both of codegen's disjuncts. The name half is `lc.pointers` plus the
    `self` receiver whose `this` is a prvalue pointer; the `Ptr[T]` half is
    the one an inline membership test misses --
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
    """Whether a polymorphic dispatch subject's pointee is const, so the
    cast targets `const Sub*`. True for
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

    A field source is const through the ReadonlyType reads (the optional
    inner, the init's raw sema type, the var_types entry); OPTIONAL_TO_PTR
    adds the storage-optional const bump (`is_const_union_source`: the
    receiver in const_ref_params (param) or const_indirect_locals (a const F1
    local, tracked in `const_locals`)); a method-call source adds the
    readonly-method ref-return branch. The bare-NAME const rule does not
    apply to a field/call source.

    The arms that read only the init EXPRESSION live in
    `_expr_is_const_source`, shared with the walrus derivation; what stays
    here is what needs the decl node or the binding kind."""
    if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
        return True
    svt = analyzer.var_types.get(stmt)
    if isinstance(svt, OptionalType) and isinstance(svt.inner, ReadonlyType):
        return True
    if _expr_is_const_source(stmt.init, func, analyzer, const_locals,
                             record_name):
        return True
    if binding is LocalBinding.OPTIONAL_TO_PTR:
        if isinstance(stmt.init, TpyName):
            # The name-copy row: const-ness follows the SOURCE pointer
            # (`const Point* q = a;` off a const Optional param/local).
            if (stmt.init.name in const_locals
                    or _param_is_const(stmt.init.name, func, analyzer,
                                       record_name)):
                return True
        elif isinstance(stmt.init, (TpyFieldAccess, TpySubscript)):
            # The storage-optional const bump reads the field/subscript
            # receiver; other init shapes (a ternary) have no receiver and
            # get const only via the shared raw-sema branches above --
            # exactly `is_const_union_source`'s reach (it returns False
            # for them).
            recv = stmt.init.obj
            if isinstance(recv, TpyName):
                if (recv.name in const_locals
                        or _param_is_const(recv.name, func, analyzer,
                                           record_name)
                        or _opt_ptr_param_deep_const(recv.name, func,
                                                     analyzer, record_name)):
                    return True
            # A method-call receiver (`child.get().parent`) contributes NO
            # const of its own: `is_const_union_source` stops at a call
            # node unconditionally, so the lift is const only via the
            # raw-sema branches above (a readonly receiver makes the field
            # read's sema type ReadonlyType, caught there). Adding a
            # const-rooted or readonly-fi disjunct here gives the WRONG
            # const-ness on an inferred-readonly inner method.
    # Borrow-alias of an lvalue rooted in a const source (`p = ps[i]`,
    # `r = obj.field`, `c = self.store[k]`): REF_ALIAS const propagation
    # via `is_const_union_source` -- recurse through chained field/subscript
    # access to the base name. The reassigned POINTER sibling (`x = a` off a
    # const-ref param -> `const T* x = &(a);`) roots the same way -- the
    # bare-name branch.
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
        # of the indirect-const rule.
        return (expr.name in const_locals
                or _param_is_const(expr.name, func, analyzer, record_name)
                or _param_is_deep_const(expr.name, func, analyzer, record_name))
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        obj = expr.obj
        if isinstance(obj, TpyName):
            # The Optional-ptr param disjunct mirrors const_indirect_locals
            # membership (seed_param_locals' deep-const seeding); a const
            # OPTIONAL_TO_PTR local rides `const_locals` like any F1 local.
            return (obj.name in const_locals
                    or _param_is_const(obj.name, func, analyzer, record_name)
                    or _opt_ptr_param_deep_const(obj.name, func, analyzer,
                                                 record_name))
        return _f1_const_rooted_source(obj, func, analyzer, const_locals, record_name)
    return False

def _expr_is_const_source(src: TpyExpr, func: TpyFunction, analyzer,
                          const_locals: set[str],
                          record_name: str | None) -> bool:
    """The const-ness a borrow binding's SOURCE EXPRESSION carries on its own,
    independent of the binding kind and of any decl node: a readonly raw sema
    type, a readonly method / dunder whose borrow return is const-projected on
    the shim, and a subscript / method call on a const-rooted receiver.

    Shared by the decl (`_f1_is_const`) and walrus (`_walrus_src_is_const`)
    derivations so the two cannot drift; each adds the arms only it can see."""
    if isinstance(analyzer.get_expr_type(src), ReadonlyType):  # raw sema type
        return True
    # A readonly method's ref return binds `const T&` -- the method-call
    # branch of the indirect-const rule.
    if isinstance(src, TpyMethodCall):
        fi = src.resolved_function_info
        if (fi is not None and fi.is_readonly
                and call_returns_cpp_ref(analyzer, fi)):
            return True
    # Operator dispatch follows that arm: a readonly dunder's borrow return is
    # const-projected on the friend shim, so `c = a + b` / `c = -a` bind
    # `const T&`. Keyed on the RAW `fi.is_readonly`
    # -- readonly-ness here is usually INFERRED, so it never shows up as a
    # ReadonlyType on the source and the raw-sema branch above misses it.
    if isinstance(src, TpyBinOp) and src.resolved_binop is not None:
        fi = src.resolved_binop.method
        if fi.is_readonly and call_returns_cpp_ref(analyzer, fi):
            return True
    if isinstance(src, TpyUnaryOp) and src.resolved_unaryop is not None:
        fi = src.resolved_unaryop.method
        if fi.is_readonly and call_returns_cpp_ref(analyzer, fi):
            return True
    # A subscript / method call on a const-rooted receiver binds const even
    # when sema resolved the MUTABLE twin (the enclosing method's
    # readonly-ness is INFERRED post body-analysis, so fi.is_readonly above
    # misses; C++ overload resolution on the const receiver picks the const
    # twin regardless) -- the receiver-const arm of the indirect-const
    # rule.
    return (isinstance(src, (TpySubscript, TpyMethodCall))
            and _f1_const_rooted_source(src.obj, func, analyzer,
                                        const_locals, record_name))

def _walrus_src_is_const(src: TpyExpr, func: TpyFunction, analyzer,
                        const_locals: set[str],
                        record_name: str | None) -> bool:
    """`const T*` for a walrus borrow-alias / pointer-Optional predecl: the
    source expression is const on its own (`_expr_is_const_source`), it is a
    select with a const arm, or its lvalue is rooted in a const param / const
    F1 local.

    The decl sibling of this is `_f1_is_const`, which cannot serve a walrus: it
    keys `var_types` and `stmt.init` off a TpyVarDecl node a walrus has not
    got. What the two share is the expression-keyed core; the rest of
    `_f1_is_const` reads a binding kind the walrus ladder decides for itself."""
    if _expr_is_const_source(src, func, analyzer, const_locals, record_name):
        return True
    if isinstance(src, TpyIfExpr):
        # A select is const-rooted iff EITHER arm is: the C++ `?:` over a const
        # and a non-const pointer yields the const type. The recursion lives
        # here rather than in `_f1_const_rooted_source`, which deliberately
        # mirrors codegen's `is_const_union_source` and answers False for a
        # ternary.
        return (_walrus_src_is_const(src.then_expr, func, analyzer,
                                     const_locals, record_name)
                or _walrus_src_is_const(src.else_expr, func, analyzer,
                                        const_locals, record_name))
    return _f1_const_rooted_source(src, func, analyzer, const_locals,
                                   record_name)

def _declared_type(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> TpyType | None:
    # Codegen's `get_resolved_type` for a NAME: the tracked DECLARED type, not
    # sema's per-occurrence cache. ctx.var_types holds a retro-widened
    # literal-seeded local's FINAL type (a later `p = <int>` widens `p = 0` to
    # BigInt), whereas analyzer.get_expr_type returns the pre-widen seed
    # (Int32). Every render keyed on the declared type must read this: the
    # mixed-sign comparison gate would over-exclude same-sign-after-widen
    # loops, and the checked-narrow family (`.to_fixed_check<T>()`) would DROP
    # a required narrow -- ill-formed C++ at a BigInt subscript index.
    if isinstance(e, TpyName):
        t = locals_.get(e.name)
        if t is not None:
            return t
    return analyzer.get_expr_type(e)

def _mixed_sign_compare(left: TpyType | None, right: TpyType | None) -> bool:
    # A signed-vs-unsigned fixed-int comparison emits std::cmp_* (and a
    # mixed-sign one with a coercion target emits a cast), never the bare
    # `(l op r)` the slice emits -- so exclude it. Keyed on int_traits_of,
    # the same primitive the cmp_* render reads.
    if not (is_fixed_int_type(left) and is_fixed_int_type(right)):
        return False
    lt, rt = int_traits_of(left), int_traits_of(right)
    return lt is not None and rt is not None and lt.signed != rt.signed

def _union_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two SAME-TYPE value-union compare operands: `::tpy::Union`'s own
    comparison operators, the rb=None bare-operator arm -- `(a == b)`. A
    union-vs-member compare would render the bare mixed pair (invalid C++, see
    BUGS.md); the equal-union requirement rejects it."""
    u = _eligible_value_union(lt)
    if u is not None and u == _eligible_value_union(rt):
        return True
    # The generic-instance wrapper pair (`a == b` on two `Tree[int]`
    # locals): the wrapper struct's own comparison operator, rendered bare.
    # Same-instance only; the NON-generic wrapper pair stays out
    # (no corpus witness -- keep the boundary measurable).
    lb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
          if lt is not None else None)
    rb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return isinstance(lb, RecursiveAliasInstanceType) and lb == rb

def _ptr_union_borrow_const(name: str, declared: dict[str, TpyType],
                            lc) -> 'bool | None':
    """Whether a borrow-form union NAME is bound with CONST pointees, or None
    when the name is not bound as a borrow form at all.

    Once two operands share a union type, the pointee const-ness is the whole
    remaining difference between their renders -- `::tpy::Union<const A*,
    const B*>` against `::tpy::Union<A*, B*>`, with no conversion either
    way. Three sources give the const one: a `readonly[...]` annotation, a
    param the callable only discriminates (`const_borrow_params`), and a
    local lifted out of a const source (`to_const_ptr_variant`, recorded at
    the decl in `const_locals`).

    Those three are the same three `emit_prims.seed_param_locals` seeds
    `const_indirect_locals` from, which is why this combinator has to move
    with that one: a fourth const source added there and not here would let a
    mixed pair through as a compare that does not build."""
    if name not in lc.ptr_variant_locals:
        return None
    dt = declared.get(name)
    return bool(
        name in lc.const_locals
        or (dt is not None
            and isinstance(unwrap_ref_type(unwrap_send_sync(dt)),
                           ReadonlyType))
        or _param_is_deep_const(name, _const_verdict_func(name, lc),
                                lc.analyzer, lc.record_name))

def _ptr_union_const_wrap(name: str, decl: UnionType, slot: UnionType,
                          declared: dict[str, TpyType], lc) -> 'str | None':
    """How an already-union NAME reaches a DEEP-CONST union slot, or None when
    it needs no conversion at all.

    One union type has three borrow renders and none converts to another
    implicitly, so the source's BINDING picks the converter, never its TPy
    type: a storage-bound name (a `list[A | B]` loop element, an `Own[union]`
    param) is a `::tpy::Union<A, B>` and takes `to_const_ptr_variant`, while a
    borrow-bound one is the borrow form and takes its own `as_const()`.
    A name already bound with const pointees is the slot's type, so it passes
    bare.

    `as_const()` answers with `Union::const_form`, which is the slot
    because the slot union IS the source's: a narrower union at a wider slot
    (`f(u)` with `u: A | B` into an `A | B | None` parameter) never reaches
    a render -- it rejects at admission, `call.arg_shape.union` for a free
    call, `call.ctor_arg.union` for a constructor and `method.arg_shape` for
    a method."""
    borrow_const = _ptr_union_borrow_const(name, declared, lc)
    if borrow_const is None:
        return "storage"
    return None if borrow_const else "as_const"

def _ptr_union_compare_pair(e: TpyBinOp, lt: TpyType | None,
                            rt: TpyType | None, lc, declared: dict,
                            analyzer) -> bool:
    """The BORROW-form twin of `_union_compare_pair`: two same-type reference
    union operands, rendered as the bare `(a == b)` on `::tpy::Union`,
    which owns Python's rule through the pointee (its `__eq__`, or identity
    when it defines none -- the answer only a borrow position can give).

    Both operands must be bound as the borrow form AND render the SAME C++
    type. One union type has three borrow renders here -- the storage form a
    comprehension loop variable binds, the mutable borrow, and the
    const-pointee read borrow -- and none of them converts to another, so a
    pair admitted on the TPy type alone emits a compare that does not build.
    Requiring the renders to agree leaves every mixed pair rejecting exactly
    where it rejected before (`BUGS.md#ref-union-loop-var-vs-borrow-compare`
    covers the loop-variable one)."""
    pu = _eligible_ptr_union_wide(lt, analyzer)
    if pu is None or pu != _eligible_ptr_union_wide(rt, analyzer):
        return False
    if not (isinstance(e.left, TpyName) and isinstance(e.right, TpyName)):
        return False
    l_const = _ptr_union_borrow_const(e.left.name, declared, lc)
    return l_const is not None and l_const == _ptr_union_borrow_const(
        e.right.name, declared, lc)

def _container_compare_pair(op: str, lt: TpyType | None,
                            rt: TpyType | None, analyzer) -> bool:
    """Two SAME-TYPE container equality operands (`self.tags == other.tags`
    on `list[str]` fields -- the @dataclass __eq__ chain): the container's
    own `operator==`, the rb=None bare-operator arm -- `(a == b)`. SETS
    also admit the ordering ops (`c <= a` -- Python's
    subset/superset comparisons ride ordered_set's raw operators); other
    containers stay equality-only. The same-type requirement mirrors the
    union pair's slice guard."""
    lb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
          if lt is not None else None)
    rb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    if lb is None or lb != rb:
        return False
    if op not in ("==", "!="):
        return bool(is_set(lb))
    return bool(is_list(lb) or is_dict(lb) or is_set(lb) or is_array(lb))

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
    `{self} OP {0}` template -- the record arm / the resolved-binop arm,
    uniform across non-generic and generic records (a generic record's
    dunder like `Box.__eq__[T: Equatable]` resolves to the same operator
    template, `((a) == (b))`). The same-type equality at the pair
    keeps a mixed-instantiation compare (a sema error) out."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, NominalType) and t.is_user_record:
        return t
    return None

def _record_compare_pair(lt: TpyType | None, rt: TpyType | None,
                         rb: 'ResolvedBinop | None' = None) -> bool:
    """Two SAME user-record compare operands: the record's generated C++
    comparison (bare operator for a derived `!=`/`<=`, or the dunder template).

    A MIXED pair is admitted only when sema resolved the compare to a record
    dunder declaring that other operand (`__eq__(self, other: str)`, and the
    `!=` derived from it): the records driver emits
    `friend bool operator OP (const T& lhs, <declared slot> other)`, so the
    render is the same bare operator -- sema's resolution is the proof that
    the pair fills the dunder's slots. Without a resolution a mixed pair is a
    sema error, so the equal-record requirement stays the fallback."""
    lu = _record_compare_operand(lt)
    if lu is not None and lu == _record_compare_operand(rt):
        return True
    if rb is None:
        return False
    recv = _record_compare_operand(getattr(rb, "receiver_type", None))
    # The receiver is the side sema pinned into `{self}`.
    return recv is not None and _record_compare_operand(
        rt if rb.is_reverse else lt) == recv


def _compare_op_dunders(op: str) -> tuple[str, ...]:
    """The comparison dunder(s) whose emitted friend operator serves `op`.

    `!=` also lists `__eq__`: C++20 rewrites `a != b` from `operator==`, and
    sema resolves no binop for that derived spelling."""
    name = BINOP_TO_METHOD.get(op)
    if name is None:
        return ()
    return (name, "__eq__") if op == "!=" else (name,)


def _record_dunder_operand_slot(
        op: str, e: TpyBinOp, lt: TpyType | None, rt: TpyType | None,
        analyzer) -> 'tuple[TpyExpr, TpyType] | None':
    """A user record compared against a NON-record operand its own comparison
    dunder DECLARES (`__eq__(self, other: str)`), for the spelling sema
    resolves no binop for -- the `!=` C++20 rewrites from `operator==`.

    The records driver emits `friend bool operator OP (const T& lhs,
    <the dunder's first param slot>)`, so the admission reads that same param
    and tests it against the other operand with sema's OWN argument check --
    the predicate the explicit `t.__eq__(x)` spelling goes through -- plus the
    compare gate's family predicates for the operand's renderability. A record
    compared to something no dunder declares, or to an operand that dunder's
    slot does not accept, therefore rejects HERE with a located diagnostic
    instead of narrowing silently in C++ (`t == 2.5` against an `Int32` slot
    prints True where CPython prints False).

    The REFLECTED spelling (`s == t`) rides C++20's reversed candidate, which
    exists for `==`/`!=` only -- a relational op has no reversed form without
    a spaceship, so the record stays pinned left there (CPython reflects the
    same pair through `Tag.__eq__`).

    Returns the non-record operand paired with that dunder param slot, so the
    render can thread the slot the way a call arg threads its param (a bytes
    literal's owned-vs-span spelling is target-driven); None when no dunder
    admits the pair."""
    recv, other_e, other_t = _record_compare_operand(lt), e.right, rt
    if recv is None and op in ("==", "!="):
        recv, other_e, other_t = _record_compare_operand(rt), e.left, lt
    if recv is None or _record_compare_operand(other_t) is not None:
        return None
    ri = analyzer.registry.get_record_for_type(recv)
    if ri is None:
        return None
    for name in _compare_op_dunders(op):
        m = _record_method_with_parents(ri, name, analyzer)
        if m is None or not m.params:
            continue
        pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            m.params[0].type)))
        # Family agreement is not slot agreement: every scalar matches every
        # other one, so the operator spelling would admit an argument sema
        # rejects at the explicit call. Ask sema instead.
        if other_t is None or not analyzer.compat.is_type_compatible(
                other_t, pt):
            continue
        if ((_resolved_scalar(pt, analyzer)
             and _resolved_scalar(other_t, analyzer))
                or (_resolved_str_value(pt, analyzer) is not None
                    and _str_compare_operand(other_e, other_t, analyzer))
                or (_resolved_bytes_value(pt, analyzer) is not None
                    and _bytes_compare_operand(other_e, other_t, analyzer))
                or (_eligible_char(pt)
                    and _char_compare_operand(other_e, other_t, analyzer))):
            return (other_e, pt)
    return None


def _record_dunder_operand_pair(op: str, e: TpyBinOp, lt: TpyType | None,
                                rt: TpyType | None, analyzer) -> bool:
    """The admission half of `_record_dunder_operand_slot` -- see it for the
    shape."""
    return _record_dunder_operand_slot(op, e, lt, rt, analyzer) is not None


def _bytes_literal_view_slot(pt: 'TpyType | None') -> bool:
    """Whether a bytes literal landing in PARAM slot `pt` takes the
    static-storage span render (`bytes_literal`) instead of the owning vector:
    both `bytes` and `BytesView` are view-shaped at a parameter, so the owning
    render would build a heap vector the callee only reads through.
    `Own[BytesView]` is the same no-op spelling over the value view. Says
    nothing about a non-param sink -- an owned binding init / return keeps the
    owning render, which is why the plain retag helper asks a different
    question."""
    if (isinstance(pt, OwnType)
            and is_bytes_view_type(unwrap_readonly(pt.wrapped))):
        pt = unwrap_readonly(pt.wrapped)
    return isinstance(pt, TpyType) and (is_bytes_type(pt)
                                        or is_bytes_view_type(pt))


def _optional_narrow_facts_ok(facts: dict[str, TpyType],
                              declared: dict[str, TpyType], analyzer) -> bool:
    """Assert-condition narrowing facts the slice needs no emit for: every
    fact narrows a pointer-repr `Optional[F1-record]` borrow name to its inner
    record (`assert p is not None` / `assert p`). Optional narrowing is
    sema-side only -- downstream reads arrive retyped with the runtime-check
    marker cleared, so the check is per-node and the assert emits just its
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
    None-test reads `.has_value()` over it -- the same storage-form arm the
    value-opt NAME/FIELD rows key. No narrowing applies to an rvalue,
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

def _tuple_field_opt_elem_subscript(init: TpyExpr,
                                    declared: dict[str, TpyType],
                                    analyzer) -> bool:
    """A ptr-repr Optional element read off a TUPLE-typed field
    (`first = h.t[0]` at `tuple[Optional[Box], Box]`): the storage element
    lifts to the borrow `T*` via `optional_to_ptr(std::get<0>(h.t))` -- the
    tuple-field sibling of the field-source OPTIONAL_TO_PTR lift. Literal
    index only (the std::get spelling); the receiver is an admitted field
    read whose bare type is a TupleType with a ptr-Optional element at that
    index."""
    if not (isinstance(init, TpySubscript)
            and not isinstance(init.index, TpySlice)
            and init.slice_function_info is None):
        return False
    if isinstance(init.obj, TpyName):
        # The `Own[tuple]`-PARAM NAME sibling (`first = t[0]` on an
        # `Own[tuple[P | None, ..]]` param): the binding is the storage
        # tuple by value, so the element lifts
        # `optional_to_ptr(std::get<0>(t))` exactly like a tuple FIELD's.
        # Keyed to the Own-WRAPPED binding: a plain borrow-tuple param's
        # element is already `T*` (no lift -- admitting it here routed a
        # divergent render, caught by the standalone-bind pin), and a
        # storage `auto&&` alias local stays unwitnessed.
        bt = declared.get(init.obj.name)
        btu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
               if bt is not None else None)
        if not (isinstance(btu, OwnType)
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(btu.wrapped))), TupleType)):
            return False
    elif not (isinstance(init.obj, TpyFieldAccess)
              and _field_markers_clean(init.obj)
              and _field_receiver_ok(init.obj, declared, analyzer)):
        return False
    rt = analyzer.get_expr_type(init.obj)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if not isinstance(rtu, TupleType):
        return False
    idx = fixed_int_literal_value_from_expr(init.index)
    if idx is None or not (0 <= idx < len(rtu.element_types)):
        return False
    et = unwrap_readonly(rtu.element_types[idx])
    return (isinstance(et, OptionalType) and et.uses_pointer_repr()
            and _f1_record(_unwrap_own(unwrap_readonly(et.inner)), analyzer))


def _tuple_local_ptr_elem_subscript(init: TpyExpr,
                                    locals_: dict[str, TpyType],
                                    own_borrow_tuple_locals: 'AbstractSet[str]',
                                    analyzer) -> bool:
    """A ptr-repr Optional element read off a MIXED own-borrow tuple LOCAL
    (`p[1]` on `p = make_mixed(b)` at `tuple[Own[Box], Box | None]`):
    std::get already yields the bare `Box*` borrowed half, so consumers
    bind / compare it DIRECTLY -- no optional_to_ptr lift (the FIELD /
    Own-param flavor keeps its lift in `_tuple_field_opt_elem_subscript`).
    Keyed on the own_borrow_tuple_locals BINDING set: a fully-owned
    storage-tuple local stores `std::optional<Box>` at that slot (a
    different, has_value render) and must stay out. Literal index only
    (the std::get spelling)."""
    if not (isinstance(init, TpySubscript)
            and not isinstance(init.index, TpySlice)
            and init.slice_function_info is None
            and isinstance(init.obj, TpyName)
            and init.obj.name in own_borrow_tuple_locals):
        return False
    bt = locals_.get(init.obj.name)
    btu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
           if bt is not None else None)
    if not isinstance(btu, TupleType):
        return False
    idx = fixed_int_literal_value_from_expr(init.index)
    if idx is None or not (0 <= idx < len(btu.element_types)):
        return False
    et = unwrap_readonly(btu.element_types[idx])
    return (isinstance(et, OptionalType) and et.uses_pointer_repr()
            and _f1_record(_unwrap_own(unwrap_readonly(et.inner)), analyzer))


def _empty_instantiation_family(t: 'TpyType | None') -> bool:
    """The builtin container families whose ZERO-ARG instantiation renders
    the bare `type_cpp()` default ctor -- one list for both entry paths
    (the call_type empty-instantiation arm and `_ctor_instantiation_ok`'s
    constructor-fi row), so a family addition cannot update one and miss
    the other."""
    return (t is not None
            and (is_list(t) or is_dict(t) or is_set(t) or is_array(t)))


def _owned_tuple_call_source(value: 'TpyExpr', analyzer) -> bool:
    """Whether `value` is a CALL whose DECLARED return is an owned tuple --
    a whole `Own[tuple[..]]` or a per-element owned-movable tuple. Sema
    strips the Own layers from the EXPR type, so the fact reads off the
    callee's declared return (the btuple alias-decl arm's `cru` rule).
    Consumed by the walrus owned-slot arm and its btuple-arm exclusion."""
    if not isinstance(value, (TpyCall, TpyMethodCall)):
        return False
    fi = value.resolved_function_info
    if fi is None or fi.return_type is None:
        return False
    ret = unwrap_readonly(unwrap_send_sync(fi.return_type))
    if isinstance(ret, OwnType):
        return isinstance(unwrap_readonly(ret.wrapped), TupleType)
    return isinstance(ret, TupleType) and ret.is_owned_movable()


def _own_storage_opt_param(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """The `Own[P | None]` param binding -- OwnType(pointer-repr Optional),
    the REVERSE of `_own_opt_storage_binding`'s Optional[Own] nesting. The
    C++ binding is the storage-form `std::optional<P>&&`: member reads spell
    `->` via `optional<P>::operator->` (the pointers seed), the None test
    reads has_value (the optional_locals seed), a whole-optional forward
    moves. F1-record pointee only; other pointees keep rejecting at
    `_unrouted_binding_read`. Returns the bare Optional or None."""
    if not isinstance(t, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(u, OwnType):
        return None
    ow = unwrap_readonly(u.wrapped)
    if not (isinstance(ow, OptionalType) and ow.uses_pointer_repr()):
        return None
    return ow if _f1_record(unwrap_readonly(ow.inner), analyzer) else None


def _own_opt_storage_binding(t: 'TpyType | None') -> bool:
    """An `Own[Point] | None` param binding -- sema's `Optional[Own[T]]`
    nesting, a VALUE-repr optional (`std::optional<Point>` by value): the
    None-test reads has_value and a narrowed read derefs `(*p)`
    position-blind (the unwrap keys on `not uses_pointer_repr()`, not
    on the branch). Deliberately NOT the reverse `Own[Optional[T]]`
    nesting (`Own[A | None]` -- `std::optional<A>&&` + the pointer_locals
    seed): its reads spell `a->` (operator->) and ride
    `_own_storage_opt_param`'s own rows."""
    if t is None:
        return False
    tu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(tu, OptionalType) and not tu.uses_pointer_repr()):
        return False
    ou = unwrap_readonly(tu.inner)
    # A value-type Own is a no-op spelling (`Own[Int32] | None` is the
    # plain value-opt SCALAR family) -- only a reference-type payload
    # makes this the storage-form record binding.
    return isinstance(ou, OwnType) and not ou.wrapped.is_value_type()


def _is_none_compare_operand(e: TpyBinOp, locals_: dict[str, TpyType],
                             analyzer) -> 'TpyExpr | None':
    """The Optional operand of an admitted `is [not] None` test, or None. The
    shape is exactly one `None` literal against a pointer-repr
    `Optional[F1-record]` borrow NAME (the declared type, as
    `get_resolved_type` reads it -- a flow-narrowed `p` still renders the
    pointer compare), a value-repr Optional name, a value-repr Optional call
    RVALUE, or an Optional FIELD subject (`self.f is None` -- storage is
    std::optional<T> whatever the repr, so the compare is `.has_value()`, the
    storage-form arm). Shared by `_lower_binop` and the lowering
    (`_lower_expr`'s is-arm) so both key one verdict."""
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
                or _value_opt_scalar(wt, analyzer) is not None
                # The view family (`str | None` / `bytes | None`) tests the
                # same way -- has_value is repr-blind, so owned and view
                # inners share the arm.
                or _value_opt_view(wt, analyzer) is not None):
            return operand
        return None
    if (isinstance(operand, TpyFieldAccess)
            and operand.property_getter_call is not None):
        # A @property read subject (`w.node is None`): the Optional
        # detection sees the field-access node (an OptionalType, whatever
        # the repr) and the storage-form classifier admits any FieldAccess,
        # so the render is has_value over the getter call
        # (`!w.node().has_value()`). A static-protocol optional takes the
        # pointer-compare arm instead -- keep it rejecting. Keyed on the
        # ANALYZED type: a sema-narrowed subject is no longer Optional and
        # falls out here (the narrowed-field recovery excludes property
        # getters too).
        pt = analyzer.get_expr_type(operand)
        ptu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
               if pt is not None else None)
        if (isinstance(ptu, OptionalType)
                and not is_protocol_type(unwrap_readonly(ptu.inner))):
            return operand
        return None
    if (_optional_ptr_borrow_name(operand, locals_, analyzer) is None
            # The WIDE ptr-repr pointee class (a T/wrapper/dyn-protocol
            # pointee binding): the same pointee-blind `!= nullptr` compare.
            and _optional_ptr_borrow_wide_name(operand, locals_,
                                               analyzer) is None
            and _value_opt_scalar_name(operand, locals_, analyzer) is None
            and _value_opt_view_name(operand, locals_, analyzer) is None
            # An `Own[Optional[T_ref]]` param binding (`std::optional<T> p`
            # by value): the RECORD-kind value-repr registration -- the
            # has_value test, like the owned-optional-call decls.
            and not (isinstance(operand, TpyName)
                     and _own_opt_storage_binding(locals_.get(operand.name)))
            # The REVERSE nesting `Own[P | None]` param
            # (`std::optional<P>&&` by rvalue-ref): the same has_value test
            # over the bare name.
            and not (isinstance(operand, TpyName)
                     and _own_storage_opt_param(locals_.get(operand.name),
                                                analyzer) is not None)
            # A `Callable[...] | None` param (`std::optional<std::function>`
            # by value): the same has_value test over the bare name.
            and not (isinstance(operand, TpyName)
                     and _value_opt_callable(locals_.get(operand.name),
                                             analyzer) is not None)
            # A value-repr `Optional[ValueType record]` binding
            # (`std::optional<Fixed>`): the same has_value test.
            and not (isinstance(operand, TpyName)
                     and _value_opt_value_record(locals_.get(operand.name),
                                                 analyzer) is not None)
            # A value-repr `Optional[Span[...]]` binding
            # (`std::optional<std::span<const T>>`): the same has_value test.
            and not (isinstance(operand, TpyName)
                     and _value_opt_span(locals_.get(operand.name),
                                         analyzer) is not None)
            # A value-repr `Optional[value tuple]` binding
            # (`std::optional<std::tuple<...>>`): the same has_value test.
            and not (isinstance(operand, TpyName)
                     and _value_opt_tuple(locals_.get(operand.name),
                                          analyzer) is not None)
            # An `Optional[String]` binding (`std::optional<std::string>`):
            # the same has_value test over the bare name.
            and not (isinstance(operand, TpyName)
                     and _value_opt_string_owned(locals_.get(operand.name))
                     is not None)
            and _value_opt_rvalue(operand, analyzer) is None
            and not _ptr_value_none_name(operand, locals_, analyzer)
            and not _ptr_value_none_field(operand, locals_, analyzer)
            and not _optional_field_none_subject(operand, locals_, analyzer)
            and _union_none_name(operand, locals_, analyzer) is None
            and _union_none_field(operand, locals_, analyzer) is None
            # A ptr-Optional TUPLE-field element read (`h.t[0] is None`):
            # the pre-lifted pointer compare
            # (`optional_to_ptr(std::get<0>(h.t)) == nullptr`).
            and not _tuple_field_opt_elem_subscript(operand, locals_,
                                                    analyzer)
            # A nullable borrow-form tuple LOCAL (`t: tuple[int, Box] |
            # None`, `std::optional<std::tuple<.., T*>>`): the has_value
            # test over the bare binding.
            and not (isinstance(operand, TpyName)
                     and _optional_borrow_tuple(locals_.get(operand.name),
                                                analyzer) is not None)
            # A STORAGE-form Optional container subscript (`d["a"] is not
            # None` on `dict[str, P | None]` -- `__getitem__` returns
            # `std::optional<P>`): the has_value test over the bare read.
            and not (isinstance(operand, TpySubscript)
                     and reads_storage_form_optional(analyzer, operand))
            # A `Ptr[T]`-returning CALL rvalue subject
            # (`_get_current_executor() is None`): the same type-agnostic
            # pointer compare (`== nullptr`) over the bare call render --
            # the name row's rvalue sibling.
            and not (isinstance(operand, (TpyCall, TpyMethodCall))
                     and _eligible_ptr_value(analyzer.get_expr_type(operand),
                                             analyzer))
            # ... and its ptr-repr-Optional flavor (`find(items, 99) is
            # None` on a `-> Node | None` callee): the `T*` return takes
            # the same bare pointer compare.
            and not (isinstance(operand, (TpyCall, TpyMethodCall))
                     and ((_ipoc := unwrap_readonly(
                              analyzer.get_expr_type(operand))) is not None)
                     and isinstance(_ipoc, OptionalType)
                     and _ipoc.uses_pointer_repr())):
        return None
    return operand

def _union_none_name(e: TpyExpr, locals_: dict[str, TpyType],
                     analyzer) -> 'UnionType | None':
    """A union-typed NAME subject of an `is [not] None` test (`v is None` on
    `v: Int32 | Dog | None`): the monostate arm renders
    `std::holds_alternative<std::monostate>(v)` over the bare binding --
    identical for value- and pointer-variant reprs (monostate is a value
    member in both). Keyed on the DECLARED type, as
    `get_resolved_type` reads it (a flow-narrowed subject still tests the variant
    binding; the lowering rejects narrowed names whose READ is an extraction
    alias). Wrapper (recursive-alias) unions read the variant through
    `.value` (VariantAccess.variant_expr's wrapper indirection) -- the emit
    arm carries that respell on the node. A binding declared through a bare
    `AliasRef` placeholder resolves to its union body first. Locals only
    (union globals are never seeded, so a global subject rejects)."""
    if not (isinstance(e, TpyName) and e.name in locals_):
        return None
    dt = _resolve_plain_alias(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[e.name]))),
        analyzer)
    if not isinstance(dt, UnionType):
        return None
    if not any(is_void_like_type(m) for m in dt.members):
        return None
    return dt

def _union_none_field(e: TpyExpr, locals_: dict[str, TpyType],
                      analyzer) -> 'UnionType | None':
    """A union-typed FIELD subject of an `is [not] None` test (`h.un is
    None` -> `std::holds_alternative<std::monostate>(h.un)`): the monostate
    holds test over the bare member read, `_union_none_name`'s field twin.
    Markers-clean + the plain-read receiver admission; wrapper unions
    (`.value` respell) and protocol members stay out. DECLARED-field-type
    keyed like the name flavor."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)):
        return None
    if isinstance(e.obj, TpyName):
        if not _field_receiver_ok(e, locals_, analyzer):
            return None
    elif not _chained_field_read_ok(e, analyzer):
        return None
    fdt = _field_decl_type(e, locals_, analyzer)
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))
         if fdt is not None else None)
    if not isinstance(u, UnionType) or u.needs_wrapper():
        return None
    if any(is_protocol_type(m) for m in u.members):
        return None
    if not any(is_void_like_type(m) for m in u.members):
        return None
    return u

def _any_none_subject(e: TpyBinOp, locals_: dict[str, TpyType],
                      analyzer) -> 'TpyExpr | None':
    """The `Any` operand of an `Any is [not] None` test, or None. The subject
    renders bare and is substituted twice into the D15 typeid probe, so it is
    restricted to a bare in-scope Any NAME (no double-eval side effect)."""
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

def _plain_record_field_link(link: 'TpyFieldAccess',
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A chain's INTERMEDIATE field link (`o.inner` in `o.inner.value`):
    its DECLARED type must be a plain (non-Optional) user record so the
    read carries no unwrap. Shared by the None-subject and truthy chain
    admissions so the two cannot drift."""
    idt = _field_decl_type(link, locals_, analyzer)
    idtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(idt)))
            if idt is not None else None)
    return (isinstance(idtu, NominalType)
            and analyzer.registry.get_record_for_type(idtu) is not None)


def _optional_field_none_subject(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A one-level Optional FIELD subject of an `is [not] None` test
    (`self.f is None` / `cfg.port is not None`): declared field storage is
    `std::optional<T>` whatever the repr, so the render is the storage-form
    `.has_value()` compare over the bare member read. Keyed on the DECLARED
    field type only: a sema-NARROWED subject (analyzed type is the payload)
    renders the identical bare-member `.has_value()` through the
    narrowed-field recovery arm, so it is admitted the same way. Same
    receiver/marker shape as the plain field arm."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if _ptr_value_field_recv_ok(e, locals_, analyzer):
        # An explicit `Ptr[record]` receiver -- a NAME or a Ptr-valued field
        # one link up (`seg.sector_front.ceil_pic is not None`): the member
        # is reached through the field arm's `->` / `deref_check(..)`, and
        # the optional it names is the same storage `std::optional<T>`.
        # (That arm owns the marker guard: a Ptr member access carries
        # `deref_depth`, which the plain-receiver guard below excludes.)
        pass
    elif not _field_markers_clean(e):
        return False
    elif isinstance(e.obj, TpyName):
        if not _field_receiver_ok(e, locals_, analyzer):
            return False
    elif (isinstance(e.obj, TpyFieldAccess)
          and isinstance(e.obj.obj, TpyName)
          and _field_markers_clean(e.obj)
          and _field_receiver_ok(e.obj, locals_, analyzer)):
        # ONE chain link (`o.inner.value is not None`): the same bare-read
        # `.has_value()` over the chain; the shared link rule keeps the
        # intermediate read unwrap-free.
        if not _plain_record_field_link(e.obj, locals_, analyzer):
            return False
    else:
        return False
    fdt = _field_decl_type(e, locals_, analyzer)
    return fdt is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt))),
        OptionalType)

def _ptr_value_none_field(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> bool:
    """A `Ptr[T]`-value FIELD subject of an `is [not] None` test
    (`s.p is None` on a `p: Ptr[T]` field): declared storage is a raw `T*`,
    so the render is the `(s.p == nullptr)` pointer compare over the bare
    member read -- NOT the `.has_value()` of the Optional-field arm. Same
    receiver/marker admission as the plain field read; a field-CHAIN
    subject (`self.inner.node is not None` -> `this->inner.node !=
    nullptr`) admits like the chained read (`_chained_field_read_ok`)."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if isinstance(e.obj, TpyName):
        if not (_field_markers_clean(e)
                and _field_receiver_ok(e, locals_, analyzer)):
            return False
    elif not _chained_field_read_ok(e, analyzer):
        return False
    return _eligible_ptr_value(_field_decl_type(e, locals_, analyzer), analyzer)

def _narrowed_opt_field_read(e: TpyFieldAccess, rtype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A sema-NARROWED Optional field read: the declared field type is
    Optional but the analyzed read type is not (the flow proof), so a value
    position unwraps `(*recv.field)` -- the is_narrowed_optional_field
    deref. Stateless: keyed on the declared-vs-analyzed type mismatch, no
    narrowing scope involved. A one-link CHAIN receiver (`o.inner.value`)
    carries the same verdict -- the rule is receiver-shape-blind AND
    link-type-blind, so this READ predicate must NOT carry the subject
    gates' _plain_record_field_link rule: a macro-generated chain read
    (dataclass_asdict_mixed's Optional-field expansion, whose link receiver
    is a macro temp outside `locals_`) is legitimately narrowed by sema and
    derefs on the leaf mismatch alone -- adding the link rule here rejects
    that case. The link rule belongs to the SUBJECT gates (which render
    has_value over the link read) only."""
    if isinstance(e.obj, TpyFieldAccess):
        if not isinstance(e.obj.obj, TpyName):
            return False
    elif not isinstance(e.obj, TpyName):
        return False
    if rtype is None or isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
            OptionalType):
        return False
    fdt = _field_decl_type(e, locals_, analyzer)
    return fdt is not None and isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt))),
        OptionalType)

def _narrowed_opt_container_field(e: TpyExpr, rtype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer) -> bool:
    """A sema-NARROWED `Optional[list/dict/set]` field read: it unwraps
    (`(*this->d)`) and takes begin()/end() off the container inside, so
    the for-head's container route claims it and the bare-container row's
    DECLARED-type key deliberately does not.

    Narrowing is keyed on `_narrowed_opt_field_read` and NOT re-derived
    here: `_field_decl_type` is munged (it unwraps an Optional/readonly
    RECEIVER and resolves inherited fields) where the narrowed-field rule
    keys the raw declared type, and adding a receiver rule to that
    predicate rejects a real case. `_field_decl_type` decides only the
    container FAMILY of the Optional's inner."""
    if not (isinstance(e, TpyFieldAccess)
            and _narrowed_opt_field_read(e, rtype, locals_, analyzer)):
        return False
    fdt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _field_decl_type(e, locals_, analyzer))))
    return (isinstance(fdt, OptionalType)
            and _plain_container_read(fdt.inner))

def _nonvalue_container_ret(ret: TpyType | None) -> bool:
    """A reference-form builtin-container call return admitted as a for-each
    iterable: an `Own[...]` return arrives Own-stripped from
    `get_expr_type` (a by-value rvalue), a borrow return Ref-stripped (a C++
    lvalue), a readonly borrow return ReadonlyType-wrapped -- all three capture
    into `__obj_N` and iterate identically past the `auto`-vs-`auto&` verdict
    (`_call_iterable_lvalue`). `Array` is on the axis and admitted with the
    rest; `Span` is a value type and stays out, as does bytes (admitted by
    the value-position check already)."""
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_send_sync(ret))
    return _f1_container_ref(t)

def _resolve_literal_seeded(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """Resolve a literal-seeded analyzer type to its final form: a PENDING
    container (PendingListType -> list, or the read-only demoted Array) and
    IntLiteral element types (through the module default) -- the pair
    `get_resolved_type` applies at render time. One helper so the storage
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
    Each renders the bare `f(args)` into `T x = f(...);` / `return f(...);`.
    Optional (the `__slot_N` + optional_to_ptr hoist),
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
            # A value-tuple-element list (`c.most_common(3)` ->
            # `std::vector<std::tuple<std::string, int32_t>>`): the bare
            # rvalue render is element-blind like the bytes row; element
            # reads gate their own consumers (the unpack head, the
            # tuple-subscript rows).
            if (args and isinstance(args[0], TpyType)
                    and _value_tuple(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(args[0]))), analyzer) is not None):
                return t
        # A dict whose VALUE is itself an admitted scalar-read container
        # (`d = asdict(line)` -> `dict[str, dict[str, Int32]]`): the bare
        # rvalue copy is element-blind like the rows above; element reads
        # gate their own consumers.
        if is_dict(t):
            args = getattr(t, "type_args", None)
            if (args and len(args) == 2
                    and (_eligible_scalar(args[0])
                         or _owned_str_slot(args[0], analyzer))):
                vin = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    args[1])))
                if ((is_dict(vin) or is_list(vin))
                        and _container_scalar_read(vin, analyzer)):
                    return t
        return None
    if _bytes_family_ref(t):
        # `ba = bytearray(...)` -- the bytes family's reference member is a
        # scalar container (`std::vector<uint8_t>`, element fixed at `UInt8`),
        # so it takes the same plain-copy decl render a scalar list does. It
        # is a row of its own rather than part of `_container_scalar_read`
        # because that family keys on the receiver's `__getitem__` resolving
        # to the container template and this one's resolves to
        # `::tpy::bytes_getitem`.
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
    # The recursive sibling (`t = astuple(line)` -> a tuple of value
    # tuples): the bare rvalue copy, element reads gating their consumers.
    if _value_tuple_nested(t, analyzer) is not None:
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
    return _f1_container_ref(t)


def _owned_tuple_call_ret(ret: TpyType | None, analyzer) -> 'TupleType | None':
    """A call-result tuple with at least one `Own[F1-record]` element, every
    other element a value scalar or str -- the tuple-unpack move-out family
    (`a, b = socket.socketpair()`). Admitted ONLY where the sink takes the
    tuple WHOLE (`SinkForm.TUPLE_SOURCE`) -- the standalone tuple-unpack
    SOURCE emits the bare `auto __tup_N = f(...);` capture + per-element
    `std::move(std::get<i>)` decls. Deliberately NOT folded into
    `_storage_call_ret`: the decl-init `storage_call` escape bypasses the
    decl slot gate, and an Own-tuple LOCAL decl is an unrouted slot."""
    if ret is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if not isinstance(t, TupleType):
        return None
    if not any(isinstance(e, OwnType) for e in t.element_types):
        return None
    for e in t.element_types:
        if isinstance(e, OwnType):
            # The Own[genrec] element moves out of the capture exactly like
            # an Own[record] one (`Tree<T> t = std::move(std::get<i>(..));`).
            ew = unwrap_readonly(e.wrapped)
            if not (_f1_record(e.wrapped, analyzer)
                    or isinstance(ew, RecursiveAliasInstanceType)
                    # The Own[P | None] element (`pair() ->
                    # tuple[Own[P|None], Int32]`): a storage-optional
                    # element the pointer-lift unpack consumes (opt_ptr
                    # targets off the tuple_to_pointer'd capture).
                    or (isinstance(ew, OptionalType)
                        and ew.uses_pointer_repr()
                        and _f1_record(unwrap_readonly(ew.inner),
                                       analyzer))
                    # The Own[A | B] element (`pair() -> tuple[Own[A|B],
                    # Int32]`): the capture holds the VALUE variant, and
                    # the target lifts it per element via to_ptr_variant.
                    or _eligible_ptr_union(ew, analyzer) is not None
                    # The Own[list/dict/set] element: the capture holds the
                    # container by value and the target moves it out, the
                    # same shape the Own[record] row spells.
                    or _own_container_element(e)):
                return None
        elif not (_eligible_scalar(e)
                  or _resolved_str_value(unwrap_ref_type(e), analyzer)
                  is not None
                  # A nested VALUE-tuple element (`accept() ->
                  # tuple[Own[socket], tuple[str, Int32]]`): the capture
                  # holds it by value; a kept target copies it out like a
                  # scalar, a discarded one emits nothing.
                  or _value_tuple_nested(
                      unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e))),
                      analyzer) is not None):
            return None
    return t


def _own_ref_mix_call_ret(ret: TpyType | None,
                          analyzer) -> 'TupleType | None':
    """A call-result tuple mixing an `Own[F1-record]` element with a BORROWED
    F1-record one (`split(p) -> tuple[Point, Own[Point]]`, returned as
    `std::tuple<Point*, Point>`) -- `_owned_tuple_call_ret`'s sibling for the
    case where a non-Own element is a REFERENCE type rather than a value
    scalar. One plain `auto __tup_N = f(..);` capture serves both slot shapes
    with no lift: the borrowed element is already the `T*` a ref target
    aliases, the Own element the by-value slot a move target drains.

    The borrow row keys on the SOURCE element's `is_borrow_ref` fact --
    `value_form()` plus pointer-repr, exactly as the unpack spells it
    -- because that is what picks the `auto&& a = unwrap_ref(
    tuple_elem_ref(..))` arm; the target type answers a different question and
    the two can disagree. Admitted only where the sink takes the tuple WHOLE
    (`SinkForm.TUPLE_SOURCE`): a whole-tuple DECL of this shape is an
    unrouted slot.
    """
    if ret is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if not isinstance(t, TupleType):
        return None
    saw_own = False
    saw_borrow = False
    for e in t.element_types:
        if isinstance(e, OwnType):
            if not _f1_record(e.wrapped, analyzer):
                return None
            saw_own = True
        elif (e.value_form() is ValueForm.BORROW_REF
              and TupleType._element_is_pointer_repr(e)
              and _f1_record(e, analyzer)):
            saw_borrow = True
        elif not (_eligible_scalar(e)
                  or _resolved_str_value(unwrap_ref_type(e), analyzer)
                  is not None):
            return None
    return t if (saw_own and saw_borrow) else None


def _nested_owned_tuple_call_ret(ret: TpyType | None,
                                 analyzer) -> 'TupleType | None':
    """A call-result tuple OF owned tuples (`two_pairs() ->
    tuple[tuple[Own[Handle], Own[Handle]], ...]`): the outer tuple has no
    direct Own element, so `_owned_tuple_call_ret` cannot see it, but each
    inner tuple is exactly that family (or a plain value tuple), and the
    whole result lands as one storage copy (`std::tuple<std::tuple<Handle,
    Handle>, ...> pp = two_pairs();`). Scoped to the STORAGE sinks that take
    the tuple whole (`SinkForm.TUPLE_SOURCE`) alongside its flat sibling."""
    if ret is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if not isinstance(t, TupleType):
        return None
    saw_owned_inner = False
    for e in t.element_types:
        eb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
        if isinstance(eb, TupleType) and eb.has_own_element():
            if _owned_tuple_call_ret(eb, analyzer) is None:
                return None
            saw_owned_inner = True
        elif not (_eligible_scalar(eb)
                  or _resolved_str_value(eb, analyzer) is not None
                  or _value_tuple_nested(eb, analyzer) is not None):
            return None
    return t if saw_owned_inner else None

def _storage_call_container(t: TpyType) -> bool:
    """Whether a `_storage_call_ret` verdict is the container family -- the
    one whose reassigned locals take the pointer-local machinery
    (tuples/unions are value types; their reassign is a plain value assign)."""
    return _f1_container_ref(t)

def _container_rebind_call_ret(call: TpyExpr, ret: TpyType | None,
                               analyzer) -> bool:
    """A container-returning call filling a rebindable two-slot local
    (`r = make(3)` reseated by `r = other(7)` -> `r = &*(__slot_2 =
    other(7));`). RVALUE sources only: a borrow (`T&`) return reseats via
    `&(call)`, the pointer arm's lift, not this slot. One predicate for the
    decl-binding admission and the call-render gate so the two layers cannot
    drift."""
    if not is_rvalue_source(analyzer, call):
        return False
    fam = _storage_call_ret(ret, analyzer)
    return fam is not None and _storage_call_container(fam)

def _btuple_owning_call_init(init: TpyExpr, analyzer) -> bool:
    """An init call that OWNS its tuple result WHOLE -- the decl-position
    admission for the owning-slot render (`__slot_N.emplace(call)`): an
    Own-declared return, or an all-Own/value per-element tuple. A MIXED
    Own+ref-element return renders the own-borrow hybrid and a plain
    borrow-tuple return is an F1 alias -- both excluded (the same owning
    -signal split as the storage_call_tuple decl arm)."""
    if not isinstance(init, (TpyCall, TpyMethodCall)):
        return False
    fi = getattr(init, "resolved_function_info", None)
    if fi is None or call_returns_cpp_ref(analyzer, fi):
        return False
    if _own_declared_call_ret(init):
        return True
    crt = getattr(fi, "return_type", None)
    cru = (unwrap_readonly(unwrap_send_sync(crt))
           if isinstance(crt, TpyType) else None)
    return (isinstance(cru, TupleType) and cru.has_own_element()
            and all(isinstance(unwrap_readonly(et), OwnType)
                    or not TupleType._element_is_pointer_repr(et)
                    for et in cru.element_types))


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
    picks the bare pass-through over the slot + `optional_to_ptr` lift."""
    return (isinstance(ret, OptionalType) and ret.uses_pointer_repr()
            and not _own_declared_call_ret(e))


def _call_iterable_lvalue(e: TpyCall, analyzer) -> bool:
    """`is_lvalue_iterable`'s call arm over the admitted iterable calls: an
    `Own[...]` return is a by-value rvalue even though `get_expr_type` strips
    the Own -- consult the fi instead; a value-type (str) return is
    an rvalue (both take the owning `auto __obj_N =` capture); the remaining
    admitted shape is a borrow return (`T&` / `const T&`), a C++ lvalue
    (`auto& __obj_N =`)."""
    rfi = e.resolved_function_info
    if rfi is not None and isinstance(rfi.return_type, OwnType):
        return False
    if is_constructor_call(e, analyzer.registry.get_record):
        return False
    return not analyzer.get_expr_type(e).is_value_type()

def _member_valued_union_slot(a: TpyExpr, ptype: TpyType | None,
                              analyzer) -> bool:
    """A union param slot receiving a MEMBER-valued scalar arg (`take_vu(k)` /
    `take_vu(2.5)` -- the arg's expr type is a member, not the union):
    the union-arg render's value branch hoists a `std::variant<...> __tmp_N = v;`
    temp -- the arg-temp row (`_value_union_temp_arg`), admitted where the
    statement position flushes and rejected otherwise. An
    already-union arg (a same-union name, the union-coerced literal) is NOT
    member-valued and rides its own pass-through arm. Guards the bare-scalar
    arg disjunct, which is slot-blind by construction. A generic recursive
    instance slot (`Tree[int]`) guards the same way through the
    `_wrapper_union_like` accessor: `Tree<T> __tmp_N = v;` is hoisted
    there too, so a bare pass would drop the wrapper temp."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(pt_u, UnionType)
            or _wrapper_union_like(pt_u) is not None):
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

def _single_member_of_family(members, pred) -> bool:
    """Exactly one union member satisfies `pred` -- the shared rule behind
    the bare-literal admissions: a literal lands in the variant's SINGLE
    matching member; two candidates would make the converting-ctor pick
    ambiguous, so both the decl and arg-temp literal arms reject."""
    return sum(1 for m in members if pred(unwrap_readonly(m))) == 1


def _value_union_temp_slot(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType],
                           analyzer) -> 'UnionType | None':
    """The value-union arg-temp row (the union-arg render's value branch): a
    MEMBER-valued eligible-scalar arg into a non-pointer union slot hoists
    `std::variant<...> __tmp_N = <arg>;` and passes the bare temp name
    (`_maybe_move` is a no-op for the scalar shapes admitted). The slot
    unwraps Ref then readonly and no further -- no Send/Sync peel, so a
    wrapped slot falls to the default render. An already-union arg (a same-union
    name, the union-coerced int literal) is not member-valued and rides its
    pass-through arms; membership is a slice guard (sema already typed the
    arg against the union). Consumed by `_lower_call_arg` so admission and
    temp selection key on one verdict. A NARROWED name shares this slot
    verdict but not the temp: it reads the member-typed extraction alias, so
    it passes BARE through the `value_union_narrowed_pass` row
    (`_value_union_narrowed_pass_arg`) and lowering skips the hoist for it.
    Only the INLINE (ternary) narrowing still rejects, because the check
    phase records no inline-narrowed names -- the gate reads `ws.narrowed`,
    lowering `lc.narrow` plus `lc.inline_narrowed`
    (BUGS.md#inline-narrowed-value-union-arg-rejects)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, UnionType):
        return None
    ut = _eligible_value_union(pt)
    if ut is None:
        return None
    # the union-arg render's `already_union` verdict keys on the C++ DECLARED type:
    # a union-declared name whose read type sema retyped to a member
    # (`x: A | B = A(); f(x)`) is still the variant in C++ and passes bare (no
    # temp) -- it rides `_union_pass_through_arg`, not this member-valued row.
    if isinstance(a, TpyName):
        # `f(self)` rides the row: the receiver read in a value position is
        # already the DEREF'd `(*this)` the temp is hoisted from, so the
        # by-value variant is initialized from the object, never from the
        # pointer.
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
        # `W("x", None)` at a value-union-with-None slot: the value
        # branch hoists the monostate temp (`std::variant<...> __tmp_N =
        # std::monostate{};`); the arm's init is the union-typed None
        # literal (emit's monostate render).
        if any(is_void_like_type(m) for m in ut.members):
            return ut
        return None
    if isinstance(at, IntLiteralType):
        # A bare int literal at a value-union ctor slot is NOT sema-coerced
        # (unlike the free-call row): the temp is hoisted with the
        # target-less literal render (`__tmp_N = 1;`, the variant's
        # converting ctor picks the single int-family member).
        if _single_member_of_family(
                ut.members, lambda m: is_fixed_int_type(m)
                or is_big_int_type(m)):
            return ut
        return None
    if isinstance(a, TpyStrLiteral):
        # A bare str literal at a value-union ctor slot hoists the same
        # temp; the variant's converting ctor picks the single str member
        # (`std::variant<int32_t, std::string> __tmp_N = "hello";`).
        if _single_member_of_family(ut.members, is_str_type):
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

def _ru_wrapper_arg_slot(ptype: TpyType | None) -> 'TpyType | None':
    """A recursive-union WRAPPER arg slot (`v: JsonValue` -- the expanded
    non-generic UnionType whose C++ form is the alias's wrapper struct --
    OR a generic instance `Tree[int]`, admitted through the
    `_wrapper_union_like` accessor: same wrapper-struct C++ form, spelled
    by the qname-keyed printer), or None. The union-arg render's value branch
    hoists `JsonValue __tmp_N = <arg>;` (create_typed, `= init` form) and
    passes the bare temp name. Unwraps exactly as that branch does
    (readonly only -- a Ref/SendSync wrapper leaves it inert, so the
    default render applies). Members of the returned type
    read via `_wrapper_like_members` -- never `.members` directly (the
    generic instance carries them as `alternatives()`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if isinstance(pt, UnionType):
        if not pt.needs_wrapper() or is_ptr_variant_union(pt):
            return None
        return pt
    # The generic instance is NOT a value type (its substituted body carries
    # a container member), so its param slot arrives Ref-wrapped where the
    # non-generic wrapper's does not -- peel Ref for this arm only, keeping
    # the UnionType path's unwrap byte-frozen.
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if isinstance(pt, RecursiveAliasInstanceType):
        return pt
    return None


def _wrapper_like_members(t: 'TpyType') -> tuple:
    """The variant member tuple of a `_wrapper_union_like` type, in the
    wrapper struct's template ordering -- `wrapper_info().full_members`,
    which both classes carry (the generic instance substitutes each
    original body member positionally)."""
    wi = t.wrapper_info()
    return wi.full_members if wi is not None else ()

def _ru_wrapper_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                         locals_: dict[str, TpyType],
                         narrowed: 'AbstractSet[str]',
                         analyzer=None) -> bool:
    """A wrapper-union NAME at a same-wrapper arg slot (`json.dumps(v)` on
    `v: JsonValue`): the binding is already the wrapper struct, so both
    paths render the bare name (no lift, no temp). A binding that carries
    the unresolved `AliasRef` placeholder (a for-each element over a
    wrapper union) resolves through the registry first. An F6-NARROWED
    name passes only when its branch fact is a MEMBER of the slot's union:
    the extraction alias renders bare and the wrapper's converting ctor
    absorbs it (`dumps(__d, ...)`); any other narrowed shape keeps
    rejecting."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, TpyName) or a.name not in locals_:
        return False
    dt = _resolve_plain_alias(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name]))),
        analyzer)
    if a.name in narrowed:
        return (any(dt == m for m in _wrapper_like_members(ut)
                    if not is_void_like_type(m))
                and _witness("arg.ru_wrapper_narrowed"))
    if dt == ut:
        return True
    # A ptr-repr `Optional[wrapper]` pointer local (`leaf_count(t)` on
    # `Tree[int] | None` after the None test): sema admits the Optional at
    # the same-wrapper slot only on a proven-non-null occurrence, so the
    # pointer-local deref (`(*t)`) binds the slot bare.
    return (isinstance(dt, OptionalType)
            and dt.uses_pointer_repr()
            and _resolve_plain_alias(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    dt.inner))), analyzer) == ut
            and _witness("arg.ru_wrapper_opt_narrowed"))

def _ru_wrapper_borrow_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A BORROW-returning wrapper-like call at a same-wrapper slot
    (`leaf_count(h.get())` -- the `Tree<int32_t>&` return binds the
    `const Tree<int32_t>&` param directly): the call renders bare, no temp
    and no lift. OWN-returning calls stay out (they need
    the argtemp materialization)."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    fi = getattr(a, "resolved_function_info", None)
    if fi is None:
        return False
    # Two spellings of "returns a C++ reference": the record-keyed helper
    # (a genrec method's `Tree<T>&`) and the bare wrapper-union return
    # slot (`-> Expr` -> `Expr&` on a free function).
    if not (call_returns_cpp_ref(analyzer, fi)
            or _wrapper_borrow_return(fi.return_type, analyzer) is not None):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    return at == ut and bool(_witness("arg.ru_wrapper_borrow_call"))


def _ru_wrapper_value_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A VALUE-returning wrapper-union call at a same-wrapper slot
    (`leaf_count(build())` where `build() -> Own[Expr]`): the NON-generic
    wrapper's call result types at the expanded UnionType, so
    the union-arg render's `already_union` verdict falls to the default bare
    render -- no temp. The non-generic twin of
    `_ru_wrapper_own_call_arg`: a generic instance's result types at
    `RecursiveAliasInstanceType` (NOT already_union) and hoists there."""
    ut = _ru_wrapper_arg_slot(ptype)
    if not isinstance(ut, UnionType):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    if isinstance(a, TpyCall) and a.isinstance_var is not None:
        return False
    fi = getattr(a, "resolved_function_info", None)
    if fi is None:
        return False
    # The borrow-returning flavor rides its own row (BORROW_BIND use).
    if (call_returns_cpp_ref(analyzer, fi)
            or _wrapper_borrow_return(fi.return_type, analyzer) is not None):
        return False
    at = analyzer.get_expr_type(a)
    wi = ut.wrapper_info()
    already = ((isinstance(at, UnionType) and at == ut)
               or (isinstance(at, AliasRef) and wi is not None
                   and wi.name == at.name))
    return already and bool(_witness("arg.ru_wrapper_value_call"))


def _ru_wrapper_field_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-wrapper FIELD read at a wrapper slot (`leaf_count(self.t)` /
    `leaf_count(h.t)` -- the member read binds the `const Tree<T>&` param
    bare, no lift and no temp). Plain-name receivers only,
    like the sibling field rows."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, TpyFieldAccess):
        return False
    if not (isinstance(a.obj, TpyName)
            and (a.obj.name in locals_ or a.obj.name == "self")):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    return at == ut and bool(_witness("arg.ru_wrapper_field"))


def _ru_wrapper_member_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                                locals_: dict[str, TpyType],
                                narrowed: 'AbstractSet[str]',
                                ) -> 'UnionType | None':
    """A member-typed NAME into a wrapper-union slot (`head(b)` on
    `b: list[Tree]`): the union-arg render's value branch hoists the typed
    temp (`Tree __tmp_N = <name>;`, create_typed `= init` form) and passes
    the bare temp name; the init takes the `_maybe_move` wrap at a movable
    last use. A same-union name is already_union (bare, no temp) and rides
    `_ru_wrapper_name_arg`; narrowed names keep rejecting (the
    already_union verdict reads the C++ DECLARED type, while the lowered
    read is the extraction alias)."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None or not isinstance(a, TpyName) or a.name not in locals_:
        return None
    if a.name in narrowed:
        return None
    dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if isinstance(dt, OptionalType):
        # A None-narrowed `Optional[wrapper]` POINTER binding reads its
        # deref (`depth((*t))`), never this typed-temp row -- the wide
        # ptr-opt deref-name row owns it.
        return None
    # The value branch has NO membership check (sema already typed
    # the arg against the union) -- only already_union routes it elsewhere:
    # a union binding (bare pass-through), a same-alias AliasRef
    # self-reference, or a generic-instance binding (all bare). Everything
    # else hoists the typed temp.
    if isinstance(dt, (UnionType, AliasRef, RecursiveAliasInstanceType)):
        return None
    return ut


def _ru_wrapper_scalar_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> 'UnionType | None':
    """A scalar / str / bytes / bool LITERAL into a wrapper-union slot
    (`show(42)` on `Value = int | str | Neg` -> `Value __tmp_N = 42;` +
    the bare temp name) -- the literal sibling of
    `_ru_wrapper_member_name_arg`. The union-arg render's value branch renders
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
    # `already_union` routes a union-typed / same-alias / generic-instance
    # source elsewhere (bare, no temp); a literal can only be one of those
    # through a sema coerce.
    if at is None or isinstance(at, (UnionType, AliasRef,
                                     RecursiveAliasInstanceType)):
        return None
    return ut


def _ru_wrapper_own_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> 'UnionType | None':
    """A scalar / str / bool LITERAL into an `Own[wrapper]` slot
    (`Holder(7)` at `value: Own[V]` -- the `V&&` slot binds the
    converting-ctor prvalue, so the render is the bare target-less
    literal; no temp, unlike the borrow slot's create_typed hoist).
    `None` stays out (the monostate spelling is its own row)."""
    ut = _own_wrapper_return(ptype, analyzer)
    if ut is None:
        return None
    if not isinstance(a, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                          TpyStrLiteral)):
        return None
    return ut


def _ru_wrapper_member_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  analyzer) -> 'UnionType | None':
    """A member-typed record-CTOR rvalue into a wrapper-union slot
    (`eval_expr(Lit(42))` -> `Expr __tmp_N = Lit(::tpy::BigInt(42));` + the
    bare temp name) -- the ctor sibling of the literal / NAME rows.
    The union-arg render's value branch hoists the same create_typed temp; the
    init is the ctor's ordinary folded render, no move (a prvalue init).
    Ctor rvalues of a MEMBER type only: a general call source brings
    result-form questions the neighbouring rows own."""
    ut = _ru_wrapper_arg_slot(ptype)
    if ut is None:
        return None
    if not isinstance(a, TpyCall) or not is_rvalue_source(analyzer, a):
        return None
    at = analyzer.get_expr_type(a)
    if at is None or isinstance(at, (UnionType, AliasRef,
                                     RecursiveAliasInstanceType)):
        return None
    atu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if not any(atu == m for m in _wrapper_like_members(ut)
               if not is_void_like_type(m)):
        return None
    return ut


def _ru_wrapper_own_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                             analyzer) -> 'TpyType | None':
    """An `Own[genrec]`-RETURNING call into the same-wrapper borrow slot
    (`leaf_count(make_leaf())` / `leaf_count(s.sprout())` ->
    `Tree<int32_t> __tmp_N = <call>;` + the bare temp name) -- the call
    sibling of the ctor-rvalue row. The Own-declared return is already the
    by-value storage shape, so the temp binds the prvalue directly (no
    move). Method-call sources ride the same hoist; their receiver/result
    admission is the method machinery's own (the temp's STORAGE init use
    asks its result gate)."""
    ut = _ru_wrapper_arg_slot(ptype)
    if not isinstance(ut, RecursiveAliasInstanceType):
        return None
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return None
    if isinstance(a, TpyCall) and a.isinstance_var is not None:
        return None
    fi = a.resolved_function_info
    if fi is None:
        return None
    ret = _own_genrec_return(fi.return_type)
    if ret is None or ret != ut:
        return None
    return ut


def _ru_container_literal_ok(a: TpyExpr, analyzer) -> bool:
    """A list/dict literal coercible into a recursive-union wrapper slot,
    admitted when the whole tree renders: the container's sema type carries
    the NON-generic `AliasRef` placeholder element (`list[JsonValue]` /
    `dict[str, JsonValue]` -- the typed-prefix, empty-spelling, and
    monostate arms all key on that shape), and every element is a scalar
    literal (target-less render), None (the wrapper's monostate), or a
    nested list/dict literal of the same family. Dict keys are str literals
    only (the owned-key `"k"` render). Everything else -- names, calls,
    f-strings, generic `RecursiveAliasInstanceType` elements -- rejects."""
    a = _peel_coerce(a)
    at = analyzer.get_expr_type(a)
    at = resolve_pending_container(at, analyzer) or at
    if isinstance(a, TpyArrayLiteral):
        # An OUTER literal at a wrapper-annotated slot types AS the wrapper
        # itself (`tree: Expr = [1, [2, 3], 4]` -> `Expr`), a nested (or
        # element-position) literal as `list[Expr]` -- both spell the same
        # container of wrapper members (the generic-instance arm's split).
        if is_list(at):
            et = at.type_args[0] if getattr(at, "type_args", None) else None
        elif (isinstance(at, (UnionType, AliasRef))
              and _wrapper_union_like(at, analyzer) is not None):
            et = at
        else:
            return False
        if not _ru_wrapper_elem_copyable(et, analyzer):
            return False
        return all(_ru_elem_ok(x, analyzer) for x in a.elements)
    if isinstance(a, TpyDictLiteral):
        if is_dict(at):
            kt, vt = at.type_args[0], at.type_args[1]
        elif (isinstance(at, (UnionType, AliasRef))
              and _wrapper_union_like(at, analyzer) is not None):
            kt, vt = None, at
        else:
            return False
        if not ((kt is None or is_str_type(kt))
                and _ru_wrapper_elem_copyable(vt, analyzer)):
            return False
        return (all(isinstance(k, TpyStrLiteral) for k in a.keys)
                and all(_ru_elem_ok(v, analyzer) for v in a.values))
    return False

def _ru_wrapper_elem_copyable(et: 'TpyType | None', analyzer) -> bool:
    """A literal's wrapper element slot with no noncopyable member: sema
    carries either the non-generic `AliasRef` placeholder or (at some
    positions -- a return literal) the resolved wrapper UnionType itself;
    both spell the same member type. A nocopy member would flip the render
    to make_vector / make_ordered_map (`_container_nocopy_elem`'s AliasRef
    arm), which this row does not spell."""
    # The raw-UnionType flavor must itself be wrapper-repr; an AliasRef
    # resolves to whatever its body is (the pre-resolution admission).
    if (isinstance(et, UnionType) and not et.needs_wrapper()):
        return False
    alias = _resolve_plain_alias(et, analyzer)
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
    (`list[Tree[int]]`). Both render the same way -- a container of wrapper
    members -- so the lowering synthesises the container
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
    Int literals stay inside int32 so the render is the bare token (a wider
    literal takes the width-pinned ctor spelling); floats stay finite
    (inf/nan take their own spellings). A WRAPPER-typed NAME element copies
    in bare (`{branch, leaf}`), with the movable-last-use move applied at
    the element lowering."""
    if isinstance(x, TpyName):
        at = analyzer.get_expr_type(x)
        atr = (_resolve_plain_alias(
                   unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))),
                   analyzer)
               if at is not None else None)
        return isinstance(atr, UnionType) and atr.needs_wrapper()
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
    _rnv = _folded_neg_int_literal(x, analyzer)
    if _rnv is not None:
        # `-3`: the unary-minus literal fold renders the bare negated
        # token, same bounds as the raw literal.
        return -(2 ** 31) < _rnv < 2 ** 31
    if isinstance(x, TpyFloatLiteral):
        return math.isfinite(x.value)
    if isinstance(x, TpyCall):
        # A fixed-int ctor element (`Int32(1)`) folds to its bare token
        # (the scalar-ctor literal fold), same bounds as the raw literal.
        v = fixed_int_literal_value_from_expr(x)
        if v is not None:
            return -(2 ** 31) < v < 2 ** 31
        # A member-record CTOR rvalue (`Leaf(1)` in `[Leaf(1), [...]]`):
        # renders bare inside the spelled container (the wrapper's
        # converting ctor absorbs the prvalue -- sema already proved
        # membership); its arg shapes re-validate in the ctor lowering.
        # Non-ctor calls keep rejecting.
        fi = x.resolved_function_info
        it = (unwrap_readonly(analyzer.get_expr_type(x))
              if fi is not None and fi.is_constructor else None)
        return isinstance(it, NominalType) and it.is_user_record
    return isinstance(x, (TpyBoolLiteral, TpyStrLiteral))

def _record_rvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                             analyzer, *,
                             frame_capturing: bool = False,
                             upcast_ok: bool = False) -> 'NominalType | None':
    """The record-rvalue arg-temp row (the free-call `is_ref_param() +
    is_temporary_expr` cascade arm): a record RVALUE -- a ctor
    `A(7)` or a by-value record-returning call `make(7)` -- into a
    SAME-nominal plain record slot hoists `A __tmp_N = A(7);` and passes the
    temp name -- mutated (`A&`) and const (`const A&`) slots alike (the
    hoist is mutation-blind). A readonly (`readonly[A]`) slot hoists only for
    a frame-capturing callee (`frame_capturing`) -- a sync callee binds the
    rvalue inline on the const ref (statement lifetime, CPython drop
    timing; the `own.readonly_ctor` bare arm admits that shape).
    `TempState.create` renders the slot type's bare `to_cpp()`, which the F1
    restriction keeps equal to the ctor's own spelling (raw name
    same-module, `native_cpp_names` qualification cross-module).
    A SUBCLASS-typed rvalue declares the CHILD's type (`Dog __tmp_1 =
    Dog();` into a `const Animal&` slot -- the `temps.create(arg_type, ..)`
    upcast temp; C++'s implicit derived-to-base binding does the rest), so
    the CHILD type is returned -- but ONLY on the FREE-call row
    (`upcast_ok`): the ctor mutated-slot row spells the SLOT type instead
    (`Base __tmp_1 = Child();`), so the ctor consumers keep the
    same-nominal slice. A borrow-returning call is not an rvalue source (it
    binds or copies without this temp) and rejects. Shared by the local slot
    classifier
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

def _strview_coerce_name(a: TpyExpr) -> 'TpyName | None':
    """The `strview_to_str` coerce over a bare NAME, peeled -- the SHAPE
    half shared by the owned-str arg/setitem gates and their render arms
    (the coerce IS the `std::string(x)` copy; a declared-StrView source
    arrives wrapped where a pending-resolved local goes bare). Each gate
    layers its own semantic admission (view resolution, param_names) on
    top; the renders peel on this shape alone because their gate already
    ran that check."""
    if (isinstance(a, TpyCoerce) and a.coercion.name == "strview_to_str"
            and isinstance(a.expr, TpyName)):
        return a.expr
    return None

def _factory_borrow_temp_arg(a: TpyExpr, analyzer) -> bool:
    """`ctx.is_temporary_expr` restricted to the shapes that can occupy a
    non-value ref / readonly-ref slot of a generator/coro factory method:
    the frame borrows the param past the statement, so every temporary must
    materialize as a named scope-local. Scalar-literal arms are omitted
    (a value-typed slot is never a ref param under the caller's guard)."""
    if isinstance(a, _CONTAINER_LITERAL_NODES):
        return True
    if isinstance(a, (TpyBinOp, TpyUnaryOp)):
        return True
    if isinstance(a, TpyCoerce):
        # A Ptr deref coercion is an lvalue; every other coercion produces
        # a temporary (is_temporary_expr's coerce arm, non-recursive).
        return not isinstance(a.actual_type, PtrType)
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return is_rvalue_source(analyzer, a)
    if isinstance(a, TpySubscript):
        ct = analyzer.get_expr_type(a.obj)
        ct = unwrap_readonly(ct) if ct is not None else None
        return isinstance(ct, NominalType) and ct.is_user_record
    return False

def _own_cascade_fires(ptype: TpyType | None) -> bool:
    """Whether the call-argument ownership cascade fires for this slot: an
    `Own[T]` (or `Own[T] | None`) payload survives the cascade's own unwrap
    (readonly + Ref -- NOT Send/Sync: a Send/Sync-wrapped slot leaves the
    cascade inert and renders bare). The scalar pass-through arms must
    reject such slots -- a bare name would silently skip the copy+move temp
    (the Own analog of `_member_valued_union_slot`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    return unwrap_optional_own(unwrap_readonly(unwrap_ref_type(pt))) is not None

def _plain_own_slot(ptype: TpyType | None) -> TpyType | None:
    """The readonly-unwrapped payload of a PLAIN `Own[T]` call-arg slot, or
    None. The `Own[T] | None` face is excluded -- its indirect-name args need
    the `ptr_to_optional[_move]` wrap, which this row does not spell."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, OwnType):
        return None
    return unwrap_readonly(pt.wrapped)

def _own_proto_container_slot(a: 'TpyExpr', ptype: TpyType | None,
                              analyzer,
                              locals_: dict[str, TpyType]
                              ) -> 'NominalType | None':
    """A CONTAINER conformer NAME at an `Own[protocol]` slot (open or
    substituted: `indexed(nums)` at `Own[Iterable[T]]`): the monomorphized
    `T_items&&` param consumes the container, so `_maybe_move` moves the
    last-use lvalue (`indexed<int32_t>(std::move(nums))`).
    Returns the protocol slot; the render arm enforces the move verdict
    (a still-live copy is unwitnessed and rejects). @dynamic slots keep
    their adapter machinery."""
    w = _plain_own_slot(ptype)
    if not (isinstance(w, NominalType) and w.is_protocol
            and not is_dyn_protocol(w)):
        return None
    if not (isinstance(a, TpyName) and a.name != "self"
            and a.name in locals_):
        return None
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return w if _f1_container_ref(au) else None


def _plain_or_opt_own_slot(ptype: TpyType | None) -> TpyType | None:
    """`_plain_own_slot` plus the value-repr Own-OPTIONAL flavor
    (`Own[Box] | None` -- the by-value `std::optional<Box>` whose
    converting ctor absorbs the same record rvalue). Shared by the
    copy-arg gate and the lowering intercept so the peel cannot
    drift."""
    w = _plain_own_slot(ptype)
    if w is not None:
        return w
    pt = (unwrap_readonly(unwrap_send_sync(ptype))
          if isinstance(ptype, TpyType) else None)
    if isinstance(pt, OptionalType) and not pt.uses_pointer_repr():
        return _plain_own_slot(pt.inner)
    return None


def _owned_form_bytes_name(a: TpyExpr, locals_: 'dict[str, TpyType] | None',
                           param_names: 'AbstractSet[str]',
                           analyzer) -> bool:
    """An OWNED-form bytes NAME -- `_owned_form_str_name`'s bytes twin, read
    off the sema-resolved type. A `bytes` PARAM's slot is the
    `std::span<const uint8_t>` view the copy rows convert from, unless the
    signature declared it `Own[bytes]` (owned `std::vector<uint8_t>` by
    value); everything else resolving to `bytes` is owned storage."""
    if not isinstance(a, TpyName) or locals_ is None:
        return False
    if a.name not in locals_:
        return False
    at = _resolved_bytes_value(analyzer.get_expr_type(a), analyzer)
    if at is None or not is_bytes_type(at):
        return False
    return (a.name not in param_names
            or _own_viewfam_param(locals_.get(a.name)) is not None)


def _own_bytes_identity_move_slot(a: TpyExpr, ptype: TpyType | None,
                                  analyzer, *,
                                  locals_: 'dict[str, TpyType] | None' = None,
                                  param_names: 'AbstractSet[str]' = frozenset()
                                  ) -> 'TpyType | None':
    """The MOVE-ONLY bytes sibling of `_own_lvalue_temp_slot`'s identity-
    chain name branch: an all-identity coerce chain over a NAME into a plain
    `Own[bytes]` slot -- or the BARE owned-form bytes NAME at the same slot
    (`self._chunks.append(owned)` in a resumable body
    -> `push_back(std::move(owned))`). Serves ONLY the temp-free last-use
    move; the non-move halves stay unshared deliberately -- a non-moved
    bytes lvalue passes BARE to an inline_template callee, which the
    copy-temp row would not render, so the shared slot verdict must not see
    bytes."""
    w = _plain_own_slot(ptype)
    if w is None or not is_bytes_type(unwrap_readonly(w)):
        return None
    if not isinstance(a, TpyCoerce):
        return w if _owned_form_bytes_name(a, locals_, param_names,
                                           analyzer) else None
    c = a
    while isinstance(c, TpyCoerce):
        if _coerce_disposition(c, own_slot_arg=True) != "identity":
            return None
        c = c.expr
    return w if isinstance(c, TpyName) else None


def _owned_form_str_name(a: TpyExpr, locals_: 'dict[str, TpyType] | None',
                         param_names: 'AbstractSet[str]',
                         analyzer=None) -> bool:
    """An OWNED-form str NAME -- the view-at-runtime test in the negative.
    A declared `String` binding is owned outright; a `str`
    binding is owned unless it is a PARAMETER, whose slot is the
    `std::string_view` the view rows convert from. Global-vs-local is not
    the axis (both render the same copy temp), so `declared` holding module
    globals alongside locals is harmless here.

    The form is read off the RESOLVED type, so a binding still held as
    a `PendingStrType` (a str local first assigned inside a nested block
    keeps its unresolved type in `declared`) must be resolved here too --
    sema's usage resolution is final pre-lowering. Without an `analyzer` to
    resolve through, a pending binding has no form and stays out."""
    if not isinstance(a, TpyName) or locals_ is None:
        return False
    t = locals_.get(a.name)
    if t is None:
        return False
    t = unwrap_readonly(unwrap_send_sync(t))
    if isinstance(t, PendingViewType):
        if analyzer is None or t.family is not STR_FAMILY:
            return False
        t = _resolve_pending_view(t, analyzer)
        if t is None:
            return False
    if is_string_type(t):
        return True
    return is_str_type(t) and a.name not in param_names


def _own_borrow_call_temp_slot(a: TpyExpr, w: TpyType,
                               analyzer) -> 'TpyType | None':
    """The CALL shape of the Own-slot copy+move row: a BORROW-returning call
    (`Box(self.get())`) is a non-simple lvalue the `T&&` slot cannot bind, so
    `auto __tmp_N = <call>;` + the move always hoists -- the temp-free
    move arm needs a NAME and never fires here.

    Payload-restricted to the type-param and same-nominal record slots: a
    str/bytes or view payload takes a CONVERTING temp spelling instead, a
    union payload a variant lift, and a value payload binds by value with no
    temp at all. An Own-returning call is an rvalue that binds the slot
    directly."""
    if w.is_value_type():
        return None
    if not (_is_type_param_slot(w) or _f1_record(w, analyzer)):
        return None
    if is_rvalue_source(analyzer, a):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return w if at is not None and at == w else None


def _own_lvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                          analyzer,
                          locals_: 'dict[str, TpyType] | None' = None,
                          param_names: 'AbstractSet[str]' = frozenset()
                          ) -> TpyType | None:
    """Slot/shape verdict for the Own-slot copy+move row -- a NAME / eligible
    field read (possibly coerce-wrapped, see below) into a plain `Own[T]`
    slot of eligible-scalar, str, or same-nominal F1-record payload. Renders
    `auto __tmp_N = <arg>;` + `f(std::move(__tmp_N))` (a str payload declares
    the owned type instead: `std::string __tmp_N{<arg>};` -- the copy is the
    view->owned CONVERSION a str-family payload carries) -- or
    the temp-free `f(std::move(name))` when the name is movable at its last
    use (decided at lowering from the
    `movable_locals` + last-use facts; a scalar is never movable, a
    pointer-local is a non-owning borrow -- both always copy). Shared by the
    gate (`_own_lvalue_arg`, which adds the locals_/narrowing rejects) and
    `_lower_call_arg` (which adds the lc-side narrowing reject and picks
    `THIRMove` vs `THIRArgTemp`).

    A COERCE-WRAPPED lvalue splits on the `needs_copy` rendered-string
    identity test, decided here by coercion KIND via `_coerce_disposition`
    (a KIND's render either always equals its inner or always wraps it, so
    the two tests agree wherever the name itself renders plain): an
    all-identity chain renders as the bare
    lvalue, so the copy temp still applies (`a.append(v)` under an
    int-literal coerce); a wrapping link over a NAME produces an rvalue that
    binds the slot bare (needs_copy=False) -- unwitnessed, so it rejects;
    over a FIELD that test never runs (needs_copy stays True), so
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
            # type (what the render==name test certifies), so the
            # peeled name stands in for the at-check below.
            if _eligible_scalar(w):
                return w
            # A `String`-typed local (`t = a + b`) reaches an `Own[str]` slot
            # under a String->str identity coerce; its owned form takes the
            # same copy temp the bare owned name below does.
            return w if (is_str_type(w) and _owned_form_str_name(
                bare, locals_, param_names, analyzer)) else None
        # FIELD inner: that test never runs, the copy temp is
        # unconditional and its init carries the (possibly wrapping) chain
        # render; str is the witnessed payload.
        return w if is_str_type(w) else None
    # A TERNARY is admitted for the COPY half only: it binds as an lvalue
    # reference the `T&&` slot cannot take, so `_maybe_move` never fires and
    # the cascade always hoists `auto __tmp_N = ((c) ? (a) : (b));` + the
    # move wrap. The move-source rows below all require a NAME, so widening
    # the shape here cannot hand a ternary the temp-free render. A VALUE
    # payload is excluded: its Own slot is a plain by-value param an lvalue
    # binds directly, so the copy arm (guarded on a non-value payload)
    # never fires and the whole render is the bare ternary.
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return _own_borrow_call_temp_slot(a, w, analyzer)
    if not isinstance(a, (TpyName, TpyFieldAccess, TpyIfExpr)):
        return None
    if isinstance(a, TpyIfExpr) and w.is_value_type():
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
    if _eligible_char(w):
        # A `char` payload takes the plain `auto` copy temp like a fixed-int
        # one; it is not an `_eligible_scalar` member only because a str
        # literal in a Char slot renders as a target-typed char literal, and
        # this row admits no literal source.
        return w if _eligible_char(at) else None
    if is_str_type(w):
        # An owned-str slot fed by a str-family FIELD read
        # (`dropped.append(self.label)`): the copy temp declares the owned
        # type (`std::string __tmp_N{this->label};`) -- the view->owned
        # conversion the str-family branch spells. Of the str
        # NAMES the OWNED-form ones are admitted -- an `Own[str]` param, or
        # any owned-declared name (`_owned_form_str_name`) -- because their
        # binding form is position-independent: `kept.append(v)` hoists the
        # same typed copy temp + move in every ladder. VIEW-form names stay
        # out: they take the S1 inline `std::string(name)` convert row.
        if not isinstance(a, TpyFieldAccess):
            if (locals_ is not None and isinstance(a, TpyName)
                    and (ovp := _own_viewfam_param(
                        locals_.get(a.name))) is not None
                    and is_str_type(ovp)):
                return w
            return w if _owned_form_str_name(a, locals_, param_names,
                                             analyzer) else None
        return w if (isinstance(at, NominalType)
                     and (is_str_type(at) or is_str_view_type(at)
                          or is_string_type(at))) else None
    w_res = _resolve_pending_view(w, analyzer)
    if w_res is not None:
        w = w_res
    at_res = _resolve_pending_view(at, analyzer)
    if at_res is not None:
        at = at_res
    if isinstance(w, NominalType) and is_str_view_type(w):
        # A VIEW payload (`Box(s)` on `Box[StrView]` -- the Own[T] slot's T
        # resolved through the pending registry): the copy temp declares
        # the view type with brace init (`std::string_view __tmp_N{s};`,
        # the str-family branch at the view-resolved slot).
        # Form-blind for the NAME: an owned or view local renders the same
        # bare name inside the braces.
        if not isinstance(a, TpyName):
            return None
        return w if (isinstance(at, NominalType)
                     and (is_str_type(at) or is_str_view_type(at)
                          or is_string_type(at))) else None
    if _f1_record(w, analyzer):
        # Same-nominal is a slice guard: sema rejects an upcast into an Own
        # slot outright, so no other pairing reaches codegen.
        return w if at == w else None
    if isinstance(w, RecursiveAliasInstanceType):
        # A generic-instance wrapper payload (`Holder(seed)` at
        # `Own[Tree[Int32]]`): the same copy+move cascade as the record
        # payload -- a movable name's last use renders the temp-free
        # `std::move(seed)`, everything else hoists the copy temp.
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
    # The SLOT can be pending too when the receiver container is itself an
    # unannotated literal (`outer = []` fed only by `outer.append(inner)`):
    # both sides then carry the same unresolved literal type, so resolve the
    # slot exactly as the argument is resolved below.
    wu = resolve_pending_container(wu, analyzer) or wu
    if _f1_container_ref(wu):
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
    """The pointer-repr `Optional[F1-record]` call-arg slot of the
    optional-ptr arg render's non-protocol tail, or None. Unwraps readonly
    only -- the arm keys on the raw param type, no Send/Sync
    peel. A protocol inner (the typed-null / adapter faces) fails the F1
    check; an `Own[T] | None` slot is storage-repr and never reaches here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if not (isinstance(pt, OptionalType) and pt.uses_pointer_repr()):
        return None
    # WIDE pointee class: every face render is pointee-blind (`nullptr`,
    # the bare pointer pass, `&(name)`, the optional_to_ptr lift); the
    # F1-specific ctor-temp face self-excludes (a wrapper/T pointee has
    # no admitted ctor shape).
    if not _opt_pointee_wide(unwrap_readonly(pt.inner), analyzer):
        return None
    return pt

def _scalar_temp_arg_value(a: TpyExpr, inner: TpyType | None, analyzer) -> bool:
    """The value half of the optional-ptr 'scalar_temp' face: an rvalue whose
    resolved type IS the slot's scalar pointee, so the optional-ptr arg
    render's temporary face hoists a typed temp and lifts its address.

    That hoist keys on `ctx.is_temporary_expr`, this on
    `is_rvalue_source` -- different questions, so the callers supply only node
    kinds where the former answer subsumes this one: a CALL, where
    `is_temporary_expr` delegates to `is_rvalue_source` verbatim, and a
    binop/unop, where it is unconditionally True. Shapes the two disagree on
    (a ternary / field-of-temporary source) reach neither call site."""
    if not _eligible_scalar(inner):
        return False
    at = analyzer.get_expr_type(a)
    if not _resolved_scalar(at, analyzer):
        return False
    if unwrap_readonly(at) != inner:
        return False
    return is_rvalue_source(analyzer, a)

def _optional_ptr_arg_face(a: TpyExpr, ptype: TpyType | None,
                           declared: dict[str, TpyType], analyzer) -> str | None:
    """Classify a call arg against a pointer-repr `Optional[record]` slot into
    its optional-ptr arg face: 'none' (the `nullptr` literal), 'pass'
    (a pointer-repr Optional binding -- already `T*`, renders bare), 'name'
    (a plain record name -- `&(name)`, or bare for an F2 pointer-local, split
    at LOWERING from `lc.pointers`), 'lift' (a storage-form Optional field
    read, `::tpy::optional_to_ptr(...)`), or 'ctor' (a same-nominal
    record-ctor rvalue -- the `&(__tmp_N)` temp face, flushable positions
    only), or 'scalar_temp' (a call/binop rvalue at a SCALAR pointee -- the
    same typed-temp hoist, flushable positions only; the render is
    pointee-blind, so only this classifier had to enumerate the family). A
    NARROWED union subject classifies 'name' (its expr type arrives
    member-stamped): the read renames to the extraction alias / inline get
    inside `_lower_expr`, wrapped in `&(...)` either way.
    A record-element lvalue SUBSCRIPT off an lvalue container (`items[i]`, a
    `T&` off an in-scope name / admitted-field container) classifies
    'subscript': `&(<subscript>)`, the arg render's `&(gen)` tail. The
    subscript guards match `_borrow_elem_subscript_shape`
    (checks.py): an unproven-Optional container is rejected (it needs the
    receiver wrapped in `deref_check`, which the `subscript_prechecked`
    render drops -> null-container UB), and the container receiver must resolve
    through `_subscript_container_recv_type` (an lvalue name / admitted field)
    -- an rvalue container (`make_list()[i]`) is NOT caught by
    `is_rvalue_source` (it does not recurse into the subscript receiver), so
    `&(<dying temp>[i])` would dangle. None rejects: rvalue-container /
    unproven-Optional / slice subscripts, coerced args, `self`, non-ctor
    rvalues (`Ptr[T]`-typed calls etc. reject). Shared by the eligibility
    gate (which adds receiver checks) and `_lower_call_arg` so both key one
    verdict; constructor args are validated during recursive lowering."""
    ot = _optional_ptr_arg_slot(ptype, analyzer)
    if ot is None:
        return None
    inner = unwrap_readonly(ot.inner)
    if isinstance(a, TpyNoneLiteral):
        return 'none'
    if isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        # A container LITERAL at an Optional[container] slot hoists the
        # spelled typed temp and lifts its address
        # (`::tpy::ordered_map<...> __tmp_N = ...; _send(.., &(__tmp_N));`)
        # -- the ctor face's container sibling, flushable positions only.
        at = analyzer.get_expr_type(a)
        at = (resolve_pending_container(at, analyzer) or at
              if at is not None else None)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if at is not None and at == inner:
            return 'container_temp'
        # A bare list literal's own sema type is the Array-inferred flavor
        # (`["a"]` -> Array[str, 1]); the temp render is SLOT-threaded (it
        # spells the slot type), so KIND-match suffices -- element shapes
        # gate inside the literal's target-typed lowering. An Array slot
        # itself is value-repr and never reaches this face.
        if ((isinstance(a, TpyArrayLiteral) and is_list(inner))
                or (isinstance(a, TpyDictLiteral) and is_dict(inner))
                or (isinstance(a, TpySetLiteral) and is_set(inner))):
            return 'container_temp'
        return None
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
            # A BORROW-returning call already typed as the same ptr-repr
            # Optional (`Edge(find(pts, 3))` at a `Point | None` slot): the
            # result IS the `T*` the slot binds, so the call renders
            # bare (the Optional-arg arm returns the argument
            # unwrapped). An Own-returning call is storage-form and takes
            # the optional_to_ptr lift instead -- excluded here.
            at = analyzer.get_expr_type(a)
            at_u = unwrap_readonly(at) if at is not None else None
            if at_u == ot and _ptr_opt_borrow_call_ret(a, at_u):
                return 'call_pass'
            # A resolved-SCALAR call rvalue (`c.set(Int32(99))`) at a scalar
            # pointee: the ctor face's scalar sibling -- the render is
            # pointee-blind and hoists the same `int32_t __tmp_N = 99;` +
            # `&(__tmp_N)`. Flushable positions only.
            if _scalar_temp_arg_value(a, inner, analyzer):
                return 'scalar_temp'
            # A CONTAINER-returning rvalue call at a container pointee
            # (`read(bytearray(b"abc"))` at a `bytearray | None` slot): the
            # literal face's call sibling -- the render is pointee- and
            # source-blind and hoists the same typed temp + `&(__tmp_N)`.
            # Rvalue sources only: a borrow return is already an lvalue whose
            # address is taken without a temp.
            at_c = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
                    if at is not None else None)
            if (at_c is not None and at_c == inner
                    and _f1_container_ref(inner)
                    and is_rvalue_source(analyzer, a)):
                return 'container_temp'
            # A RECORD-returning rvalue call at a record pointee
            # (`Conn(host, port, _context_for(v))`): the ctor face's
            # free-call sibling -- the render is source-blind and hoists
            # the same slot-typed temp + `&(__tmp_N)`. An `Own[T]` return
            # unwraps to the same pointee; an INEXACT one (a subclass
            # return, whose temp class is re-derived from the arg type to
            # avoid slicing) stays out.
            at_o = at_c.wrapped if isinstance(at_c, OwnType) else at_c
            at_o = unwrap_readonly(at_o) if at_o is not None else None
            if (isinstance(inner, NominalType) and at_o == inner
                    and _f1_record(inner, analyzer)
                    and is_rvalue_source(analyzer, a)):
                return 'ctor'
            return None
        at = analyzer.get_expr_type(a)
        if at == inner:
            return 'ctor'
        # A STRUCTURAL-conformer ctor rvalue at an Optional[@dynamic P]
        # slot: not Base-derived in C++, so the vtable rides an owning
        # Adapter temp (`::tpy::Adapter<P, C> __tmp{C(..)};` + `&(__tmp)`).
        # Checked BEFORE the subclass-fact branch: a dyn-protocol inner
        # passes polymorphic_source_inner for ANY distinct record, but the
        # bare child temp upcast is only valid for an INHERITING conformer.
        if (isinstance(at, NominalType) and at.is_user_record
                and isinstance(inner, NominalType) and is_dyn_protocol(inner)
                and not record_inherits_dynamic(at, inner,
                                                analyzer.registry)):
            return 'adapter_rvalue'
        # A SUBCLASS ctor rvalue: the CHILD-typed temp's address binds the
        # base pointer implicitly (`ClickEvent __tmp_2 = ClickEvent(..);
        # describe(&(__tmp_2))` -- the upcast temp).
        if (isinstance(at, NominalType)
                and is_polymorphic_subclass_fact(inner, at,
                                                 analyzer.registry)):
            return 'ctor'
        return None
    if isinstance(a, TpyMethodCall):
        # A record-returning marker-call rvalue (`HTTPSConnection(..,
        # ssl.create_default_context())`): the tail hoists the same
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
        # A sema-NARROWED storage-Optional field: the analyzed type is the
        # (Ref-wrapped) payload, but the member's C++ storage stays
        # `std::optional<T>` -- the narrowed-value-optional rule keys the
        # DECLARED field type and applies the same lift over the bare
        # member read (never `&(field)`).
        fdt = _field_decl_type(a, declared, analyzer)
        fdt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))
               if fdt is not None else None)
        if (isinstance(fdt, OptionalType)
                and unwrap_readonly(fdt.inner) == inner
                and at is not None and not isinstance(at, OptionalType)):
            return 'lift'
        return None
    if isinstance(a, (TpyBinOp, TpyUnaryOp)):
        # The arithmetic sibling of the scalar call rvalue (`c.set(n + 1)`):
        # `is_temporary_expr` is unconditionally True for these, so the
        # temp hoists wherever this admits.
        return ('scalar_temp'
                if _scalar_temp_arg_value(a, inner, analyzer) else None)
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    # Normalize the AliasRef-vs-resolved-union duality on BOTH sides (a
    # wrapper local's expr type arrives resolved while the slot's pointee
    # carries the placeholder, or vice versa).
    inner_r = _resolve_plain_alias(inner, analyzer)
    if isinstance(at, OptionalType):
        # An unnarrowed pointer-repr Optional binding (a `T | None` param /
        # OPTIONAL_TO_PTR local) is already `T*` -- the bare pass face.
        return 'pass' if (at.uses_pointer_repr()
                          and _resolve_plain_alias(
                              unwrap_readonly(at.inner), analyzer)
                          == inner_r) else None
    if isinstance(at, PtrType):
        # A `Ptr[T]` binding IS `T*` -- the bare pass into the ptr-opt
        # slot (`consume(p)`); a mutable pointer widens into a
        # readonly-inner slot the same way (the reverse direction is a
        # sema error, so it never reaches this face).
        if (_resolve_plain_alias(unwrap_readonly(at.pointee), analyzer)
                == inner_r):
            return 'pass'
        return None
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    if _resolve_plain_alias(at, analyzer) == inner_r:
        return 'name'
    # A STRUCTURAL-conformer NAME lvalue at an Optional[@dynamic P] slot:
    # the non-owning RefAdapter temp (`::tpy::RefAdapter<P, C> __tmp{x};`
    # + `&(__tmp)`) -- the lvalue sibling of the adapter_rvalue face. The
    # RAW expr type must be the bare record: the face keys the unstripped
    # get_expr_type, so a readonly-wrapped conformer takes the generic tail
    # instead of the RefAdapter (whose ctor wants `impl&`).
    if (isinstance(a, TpyName)
            and isinstance(analyzer.get_expr_type(a), NominalType)
            and isinstance(at, NominalType) and at.is_user_record
            and isinstance(inner, NominalType) and is_dyn_protocol(inner)
            and not record_inherits_dynamic(at, inner, analyzer.registry)):
        return 'adapter_name'
    return None

def _union_literal_temp_arg(a: TpyExpr, ptype: TpyType | None, analyzer,
                            *, literal_type) -> 'UnionType | None':
    """The shared body of the union literal-temp ARG rows (the
    `_borrow_tuple_arg` factoring): a LITERAL of `literal_type` at a
    pointer-variant union slot with a matching member. the union-arg render's
    member-shape-blind rvalue branch hoists the typed temp and lifts its
    address (`pv{&__tmp_N}`). Readonly/Own slots stay out (their
    spellings are unwitnessed). Returns the union or None."""
    if not isinstance(a, literal_type):
        return None
    pt = unwrap_send_sync(ptype) if isinstance(ptype, TpyType) else None
    if pt is None or isinstance(pt, ReadonlyType):
        return None
    ut = unwrap_ref_type(pt)
    if not (isinstance(ut, UnionType) and is_ptr_variant_union(ut)):
        return None
    at = analyzer.get_expr_type(a)
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return None
    return ut

def _union_dict_literal_temp_arg(a: TpyExpr, ptype: TpyType | None,
                                 analyzer) -> 'UnionType | None':
    """The dict flavor (`send(url, {"user": "ann"})` at
    `bytes | dict[str, str] | None` -> `::tpy::ordered_map<..> __tmp_N =
    ..;` + `pv{&__tmp_N}`); the dict literal's render self-describes, so
    the temp init is the ordinary literal lowering. Other literal kinds
    stay unwitnessed."""
    return _union_literal_temp_arg(a, ptype, analyzer,
                                   literal_type=TpyDictLiteral)

def _union_bytes_literal_temp_arg(a: TpyExpr, ptype: TpyType | None,
                                  analyzer) -> 'UnionType | None':
    """The bytes flavor (`s.post(url, b"payload")` at `bytes | dict |
    None` -> `std::vector<uint8_t> __tmp_N =
    ::tpy::bytes_literal_owned(..);` + `pv{&__tmp_N}`)."""
    return _union_literal_temp_arg(a, ptype, analyzer,
                                   literal_type=TpyBytesLiteral)


def _arg_ptr_union_slot(ptype: TpyType | None, analyzer,
                        *, readonly_target: bool = False,
                        ) -> 'tuple[UnionType, bool] | None':
    """The pointer-variant union of a non-Own call-arg slot (the member/None
    inline-lift target) plus its deep-const verdict (a `readonly[...]`
    annotation or the callee's `const_borrow_params` fact, threaded as
    `readonly_target`), or None. A deep-const slot spells const pointees on
    the lift and takes one of the const conversions on already-union args
    (`_ptr_union_const_wrap`). An `Own[union]` slot is the value-variant auto-move cascade and
    rejects. Consumed by
    `_lower_call_arg` so admission and lift selection key on one verdict.

    The member class comes from `_eligible_ptr_union_either`, the one
    `_union_pass_through_arg` admits on. Keying the const bridge on the
    narrow class alone left a union with a zero-type-arg container member
    (`bytearray | Int32`) admitted at the pass-through row and skipped at
    the wrap, so the argument reached a const-pointee slot un-wrapped."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_send_sync(pt)
    deep_const = isinstance(pt, ReadonlyType) or readonly_target
    if isinstance(unwrap_readonly(pt), OwnType):
        return None
    ut = _eligible_ptr_union_either(pt, analyzer)
    if ut is None:
        return None
    return ut, deep_const

def _scalar_pass_through_slot(ptype: TpyType | None, analyzer) -> bool:
    """A method param slot the inline-template arg path passes a scalar into
    bare: a value scalar or `Own[scalar]`. Scalars are value types -- copied,
    never moved -- and the Own arg handling skips the copy+move temp for
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
    directly, so the emit is the bare read -- the field twin of
    `_container_pass_through_arg`'s bare-NAME row, and the reason it covers
    containers and open-T alike is that the rule is slot/field type EQUALITY,
    not a family list.

    Keyed on the DECLARED field type, so a narrowed field types at its
    un-narrowed type and stays out (its read takes the unwrap). A
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


def _native_iterable_source(t: 'TpyType | None') -> bool:
    """A source whose C++ form IS a std range, so a runtime template overload
    at a structural `Iterable[T]` / `Sequence[T]` / `Sized` slot binds it BARE
    -- no adapter wrap, no span conversion.

    The reference axis (`_f1_container_ref`: list / dict / set / bytearray /
    Array) plus the borrowing VIEWS over one (`Span`, the dict views), which
    carry the same range interface by value. Stated once because the three
    source-shape siblings below (NAME / FIELD / CALL) ask it of three
    expression kinds.

    A strict SUBSET of `is_native_iterable` (`tpyc/modules/type_resolution.py`
    -- the registry's `extends NativeIterable` fact, which also holds for
    `str` / `String` / `StrView` / `bytes` / `BytesView` / `Range` /
    `varargs`). Those extra members are VALUE types the generic value-argument
    rows already pass bare (`sum(b)` on a `bytes` name emits
    `::tpy::builtin_sum<uint8_t>(b)` without reaching here), so this is the
    narrower arg-SHAPE question -- does the expression need a conversion at
    the slot -- which only the reference axis and the views can get wrong."""
    if not isinstance(t, TpyType):
        return False
    return bool(_f1_container_ref(t) or is_span(t) or is_dict_view(t))


def _container_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType], analyzer) -> bool:
    """A container arg that passes as the bare name: a bare in-scope name on
    the reference axis (or a span), into a NON-Own concrete same-axis
    param (`std::vector<T>&` / `const ordered_map<K, V>&` / ...). The
    ownership cascade never fires for that slot shape (`own is None`), so the
    emit is the bare name. An `Own[container]` slot auto-moves at last use
    (`f(std::move(xs))`), a `Span` / protocol (`Iterable`) slot converts
    (`::tpy::as_mut_span(xs)` / adapter wrap), and an `Optional[container]`
    slot lifts (`&(xs)`), so those reject here.
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
    if not (span_src or _f1_container_ref(at)):
        return False
    if ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    # Explicit Own/Optional rejects (the move / address-of lift slots); the
    # reference-axis check below would also exclude them, but the invariant
    # should be self-evident, matching the cascade's own Own detection.
    if isinstance(pt, (OwnType, OptionalType)):
        return False
    if span_src:
        # A span NAME into the SAME span slot: both sides are the by-value
        # view, so the arg renders bare (no as_span / const widen -- those
        # arrive as coerces and ride `_span_coerce_arg`). EXACT, not
        # kind-equal: a span is the one source here whose element and
        # const-ness are part of the by-value slot.
        return at == pt
    # A reference-axis slot takes the reference-axis name bare (a
    # `std::vector<T>&` / `ordered_map<K, V>&` / `std::vector<uint8_t>&`
    # bind). `bytes` is NOT on the axis, so the bytearray-into-bytes pairing
    # -- the BytesView coerce, which arrives as its own node -- stays out.
    return _f1_container_ref(pt)

def _container_ternary_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A container-typed ternary of declared container NAMES at a native
    protocol slot (`len(a if flag else b)` -> `::tpy::__len__(((flag) ?
    ((*a)) : ((*b))))`): the ifexpr's container arm renders the lvalue
    ternary and the template binds it bare, like the single-name rows."""
    if not isinstance(a, TpyIfExpr):
        return False
    if not (isinstance(a.then_expr, TpyName) and a.then_expr.name in locals_
            and isinstance(a.else_expr, TpyName)
            and a.else_expr.name in locals_):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    if not _f1_container_ref(at):
        return False
    return _protocol_binding(ptype) is not None


def _native_iterable_container_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   locals_: dict[str, TpyType]) -> bool:
    """A `_native_iterable_source` NAME into a NATIVE builtin's structural
    `Iterable[T]` (or `Sequence[T]` -- `reversed(xs)`) protocol param
    (`all(xs)` / `any(xs)` / `sum(xs)` -> `::tpy::builtin_all(xs)`):
    the runtime overload is a C++ template that binds the range BARE, so no
    adapter/span conversion runs -- unlike a plain-TPy `Iterable` param, which
    `_container_pass_through_arg` rejects for exactly that
    conversion. Native/@cpp_template loop ONLY (the caller gates the branch); in
    the plain loop the same slot would need the adapter wrap."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not _native_iterable_source(at):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")

def _value_opt_member_arg(a: TpyExpr, ptype: 'TpyType | None',
                          declared: dict[str, TpyType], analyzer) -> bool:
    """A member-typed arg into a VALUE-repr Optional slot (`std::optional<T>`
    by value): the call-argument render has no value-optional arm at all, so
    the arg falls to the generic tail and the optional's converting ctor
    absorbs the bare member render (`f(5)`, `f("hi")`, `f(Color.Red)`, a
    bytes rvalue) -- position-blind, matched by `_lower_call_arg`'s own tail.
    Excluded: `Optional[Own[...]]` slots (the Own cascade), optional-typed
    args (the whole-optional rows), and any NAME/FIELD whose C++ binding is
    still the optional (a narrowed read must pass the WHOLE optional, while
    the plain lowered read would deref)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not isinstance(u, OptionalType) or u.uses_pointer_repr():
        return False
    _vom_inner = unwrap_readonly(unwrap_send_sync(u.inner))
    if isinstance(_vom_inner, OwnType):
        # Own on a VALUE payload is a no-op spelling (`Own[Int32] | None`
        # is the same by-value `std::optional<int32_t>` slot), so the bare
        # member render stands (`Container.wrap_optional(99)`); a non-value
        # payload keeps the Own cascade.
        if not _vom_inner.wrapped.is_value_type():
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
    """The FIELD sibling of `_native_iterable_container_arg`: a
    `_native_iterable_source` member read at a native builtin's structural
    `Iterable[T]` / `Sequence[T]` slot (`",".join(self._parts)` ->
    `::tpy::str_join(",", this->_parts)`). The member read renders bare exactly
    like the name row, and the runtime overload is the same
    binding-by-template, so no adapter conversion runs."""
    if not _field_receiver_ok(a, declared, analyzer):
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not _native_iterable_source(at):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")


def _native_iterable_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                              analyzer) -> bool:
    """A `_native_iterable_source`-returning CALL rvalue into a NATIVE
    builtin's structural `Iterable[T]` / `Sequence[T]` slot
    (`zip(get_names(), get_scores())` ->
    `::tpy::builtin_zip<...>(get_names(), get_scores())`): the call-branch twin
    of `_native_iterable_container_arg`'s bare-name row. The runtime overload
    is a C++ template that binds the container BARE, so an `Own[list]`-returning
    free/method call renders in place with no move temp -- the Iterable slot is
    not `Own[T]`, so no ownership cascade fires. Native/@cpp_template loop ONLY
    (the caller gates the branch); the inner call is re-validated by its own
    value-position lowering, so an unroutable source rejects."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pb = _protocol_binding(ptype)
    # Sized joins the slice: `len(empty_set())` binds the container-call
    # rvalue bare into `::tpy::__len__(...)` exactly like the Iterable
    # slots.
    if pb is None or pb.name not in ("Iterable", "Sequence", "Sized"):
        return False
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    # The DICT-VIEW result (`sorted(d.keys())`) is on the same axis: the view
    # is a borrowing value object the same template param binds bare, and the
    # runtime's range overloads accept it. NATIVE/@cpp_template callees only,
    # which is what the docstring's "the caller gates the branch" means: a
    # USER callee's structural slot hoists an `auto __tmp_N =` temp for a view
    # rvalue, so the render arm re-checks.
    return _native_iterable_source(at)


def _dict_view_call_result(a: TpyExpr, analyzer) -> bool:
    """Whether a call's result is a dict VIEW -- the one member of the
    native Iterable-slot family whose USER-callee render differs (the
    structural-slot temp hoist)."""
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    return is_dict_view(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))))

def _native_iterable_range_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A `range(...)` rvalue into a NATIVE builtin's structural `Iterable[T]`
    / `Sequence[T]` slot (`zip(range(3), names)` ->
    `::tpy::Range<int32_t>(3)` bound bare by the template): the
    instantiation ladder's range row on the native ladder. Shape (arity /
    counter-scalar) is validated at the render branch, which applies the
    instantiation arm's checks and rejects outside them."""
    if not _is_range_call(a):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name in ("Iterable", "Sequence")


def _native_iterable_genexpr_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A generator expression into a NATIVE builtin's `Iterable[T]` slot
    (`all(x > 0 for x in xs)`): the make_generator IIFE binds directly. Admitted
    broadly here; `_lower_genexpr` raises for the shapes outside its slice
    (range / filter / non-lvalue / unpack / owned / narrowed), so an unsupported
    genexpr rejects rather than misrouting."""
    if not isinstance(a, TpyGeneratorExpression):
        return False
    pb = _protocol_binding(ptype)
    return pb is not None and pb.name == "Iterable"

def _template_positional_indices(tmpl: str) -> 'set[int]':
    """The positional placeholder indices a `@cpp_template` body actually
    substitutes. Walks the same brace grammar `_positional_only_template`
    does, so a `{{0}}` literal-brace escape is NOT counted as a reference --
    which a substring test would get wrong."""
    seen: set[int] = set()
    i, n = 0, len(tmpl)
    while i < n:
        c = tmpl[i]
        if c == "{":
            if i + 1 < n and tmpl[i + 1] == "{":
                i += 2
                continue
            close = tmpl.find("}", i + 1)
            if close == -1:
                break
            field = tmpl[i + 1:close]
            if field.isdigit():
                seen.add(int(field))
            i = close + 1
        elif c == "}" and i + 1 < n and tmpl[i + 1] == "}":
            i += 2
        else:
            i += 1
    return seen


def _positional_only_template(tmpl: str, n_args: int) -> bool:
    """Whether a `@cpp_template` body contains only in-range positional
    placeholders (`{0}`, `{1}`, ...) and `{{`/`}}` literal-brace escapes --
    the subset `expand_cpp_template` can render with no receiver and no
    substitution context. A surviving named field (`{cpp}`, `{self}`, a type
    param) means sema's substitution did not fully resolve the template, so
    the call rejects."""
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
    x: T)`, the `int_cast_check` arm). The argument target hint is inert for
    a TypeParamRef slot -- not Own, not fixed-int, no borrow/storage lift -- so
    an eligible-scalar arg emits bare exactly as into a concrete scalar slot.
    The Ref/readonly peel matches the argument render's own `ptype_inner`
    unwrap (the stub stores the generic param as `Ref(TypeParamRef)`)."""
    if _is_type_param_slot(ptype):
        return True
    # A Char slot (`int(chr(65))` -- the int(Char) overload): the ctor's
    # cast template consumes the char arg bare, like any scalar slot.
    if _eligible_char(ptype):
        return True
    return _scalar_pass_through_slot(ptype, analyzer)

def _template_init_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` FunctionInfo of a bare-name `@cpp_template`
    type-constructor call in the pure-template-expansion shape
    (`fi.cpp_template` with no `call_type`), or None. Shared by the scalar
    and slice-object ctor gates; each adds its own result/arg checks."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Markers that take earlier / different call branches. Explicit
    # type_args are rejected; INFERRED type args (the generic int_cast_check
    # overload) are fine -- the positional-only template makes the
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
    # reproduce (the same fi rejects method-call lowering applies).
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
    in-scope container / array / span NAMES. These are VALUE views: the
    target type is spelled and direct-initialized (`std::span<const
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
    """An `Array[T, N]([...])` instantiation over an array LITERAL. The
    resolved-ctor template arm skips an array-literal first arg outright, so
    this lands in the call_type tail, which spells the target type and hands
    the literal `call_type` as its brace target (`std::array<int32_t, 3>({10,
    20, 30})`). Restricted to Array's own param-less `__init__`: a user ctor
    taking a container param reaches the same tail with a real slot type and
    still threads `call_type` -- a different render this arm must not
    claim."""
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
    the call_type branch's resolved-template arm. Shape only: it makes no
    judgement about the RESULT family, which is what decides whether that arm
    is reached, so each caller adds its own family gate."""
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
    `set(copy(b))`, `list(copy_iter(it))`, `list(heapq.merge(a, b))`. It
    renders through the ordinary call-arg dispatch, inline into the
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
    container ctor whose single arg is an array LITERAL -- or None. The
    resolved-ctor template arm skips a literal first arg outright, so this
    lands in the call_type tail, which spells the result type around the
    literal's own braces (`::tpy::ordered_set<Node>({Node(2)})`).

    The literal must be lowered against a LIST of this slot, never against its
    own sema-resolved type (a read-only literal demotes to `Array[T, N]`, whose
    element target does not apply here) and never against the call's
    own type (a `set` result would render the literal as a second
    `ordered_set`). Only scalar / F1-record element slots are admitted: those
    are exactly the families for which `target_type` yields no
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
    that carries `call_type` (`StrView("x")` / `String("x")`) -- the
    call_type branch's resolved-template arm, which for a viewfam/owned-str
    result renders the ctor's
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
    because the resolved-template arm comes FIRST -- a span construction
    that resolves a template ctor never gets the spelled direct-init
    render."""
    if not is_span(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))):
        return None
    return _template_ctor_call_fi(e)

def _tparam_value(t: 'TpyType | None') -> bool:
    """A bare type-param value (`T` after the ro/ref/send unwraps): renders
    by name (the `_f1_record_type_arg_ok` rule), so T-typed
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
    stub (sema's `func_info` pick), a single-stub group to its only entry;
    the kind classifier already pinned the targs/type-params arity."""
    fis = analyzer.registry.get_function(e.func_name)
    root = (e.resolved_function_info
            if len(fis) > 1 and e.resolved_function_info is not None
            else fis[0])
    subst = dict(zip(root.type_params, e.inferred_type_args))
    # An @overload group resolves to a per-call fi whose params sema already
    # SUBSTITUTED, while the emitted function is the impl TEMPLATE, whose slots
    # are still `T`. Recover the declared stub -- the one whose params
    # reproduce the resolved signature under this substitution -- so every
    # consumer asks about the slot the template actually spells. Without it a
    # bare-`T` slot is invisible on this route and the verdicts that key on it
    # (the arg rows, the frame-borrow temp) silently do not apply.
    if any(not _is_type_param_slot(unwrap_ref_type(p.type))
           for p in root.params) and subst:
        for cand in fis:
            if len(cand.params) != len(root.params):
                continue
            if any(_is_type_param_slot(unwrap_ref_type(p.type))
                   for p in cand.params) and all(
                    substitute_type_params_simple(unwrap_ref_type(c.type),
                                                  subst)
                    == unwrap_ref_type(r.type)
                    for c, r in zip(cand.params, root.params)):
                root = cand
                break
    return root, subst

def _is_range_call(it: TpyExpr) -> bool:
    """The `range(...)` iterable form -- the range-vs-container discriminator
    shared by the for-loop lowering, the comprehension routes, and the
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
    the `call_type` branch's resolved-template arm (sema already
    substituted the class type params, e.g. `::tpy::construct<std::vector<
    int32_t>>({0})`), or None. The subscript-spelled form (`Stack[Int32]()`,
    `subscript_callee` set), the Ptr null ctor, the list-repeat and
    array-literal arms, native ctors, and the float-str constant fold all
    take different renders and reject here."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    if e.call_type is None or isinstance(e.call_type, PtrType):
        return None
    # `e.type_args` is NOT excluded: an explicitly spelled `list[Int32](it)`
    # renders exactly like the inferred `list(it)` -- sema folds the spelling
    # into `call_type` and into the ctor template, and no call branch reads
    # the node's own type args.
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
    """No special-emit marker: every marker takes a different method-call
    path (static / super / module-qualified / typed-dict / nested-ctor /
    callable-field / macro / fstr / deref chain). The Optional runtime-check
    marker is NOT in this set -- callers dispose of it themselves (the
    optional-ptr borrow receiver renders it as the deref_check face; every
    other caller must reject it explicitly). `targs_ok` admits a plain
    generic METHOD call's explicit/inferred type args -- they spell as
    `recv.method<targs>(args)` on the same plain-member path (the
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
                        ret_cast_ok: bool = False,
                        literal_mangled_ok: bool = False) -> bool:
    """Shared fi rejects. A consuming method moves the receiver
    (`std::move(xs)`) -- `consuming_ok` admits it (set only by the record
    method arm for a bare non-pointer, non-narrowed name receiver, whose
    move_receiver render carries the wrap); `cpp_return_type` wraps the call in a static_cast;
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
                # `static_cast<declared>(...)` wrap.
                or (fi.native_cpp_return_type is not None
                    and not ret_cast_ok)
                # A LiteralType param mangles the member name; only the
                # record method arm carries that spelling
                # (`literal_mangled_ok`, method_literal_mangled_cpp).
                or (any(isinstance(p.type, LiteralType) for p in fi.params)
                    and not literal_mangled_ok)
                # A generic async METHOD is admitted at factory positions:
                # the call spells inline (member call + method targs, the
                # sync generic-method render) and the positions that would
                # spell the coro FRAME type gate generics themselves (the
                # decl arm's coro_inferred_type_args check, the await
                # gate's res.await_generic).
                or (fi.is_async and not coro_factory_ok)
                or (fi.is_generator and not generator_ok)
                or (fi.is_property_getter and not property_getter_ok)
                or (fi.is_property_setter and not property_setter_ok))

def _dict_view_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                           analyzer,
                           methods: tuple[str, ...] = ("values", "keys"),
                           field_recv_ok: bool = False) -> bool:
    """`d.values()` / `d.keys()` as a for-loop iterable: a zero-arg dict-view
    method on a bare-name eligible dict binding. The view result is an rvalue
    (the owning `auto __obj_N = ::tpy::dict_values(d);` capture); the loop var
    is the dict's value/key, checked by the caller's shared elem gate.
    `.items()` yields tuples -- the tuple-unpack gate passes it explicitly."""
    if e.method not in methods or e.args:
        return False
    if isinstance(e.obj, TpyName):
        if e.obj.name not in locals_:
            return False
        recv_t = locals_[e.obj.name]
    elif field_recv_ok and isinstance(e.obj, TpyFieldAccess):
        # The FIELD-receiver flavor (`::tpy::dict_items(d.items)` -- the
        # asdict dict recursion), DECLARED-type keyed like the C3 field
        # iterable. A ONE-level field read only: a chained receiver is a
        # different render family. Opt-in because the callers that do NOT
        # render the view themselves must not see it.
        if not _field_receiver_ok(e.obj, locals_, analyzer):
            return False
        recv_t = _field_decl_type(e.obj, locals_, analyzer)
        if recv_t is None:
            return False
    else:
        return False
    # An Optional-checked receiver (`d.values()` on a narrowable Optional
    # dict) takes the deref_check method face, not the bare view call.
    if not _plain_member_call_markers_ok(e) or e.needs_optional_runtime_check:
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi) or fi.params:
        return False
    if isinstance(e.obj, TpyName):
        # A PROVEN-narrowed pointer-repr `Optional[container]` NAME receiver:
        # the pointer local derefs bare into the same view render
        # (`::tpy::dict_values((*items))`, the indirect-name receiver arm),
        # so the element predicates below apply to the INNER container. The
        # runtime-check reject above is what keeps an UNPROVEN receiver --
        # which needs the deref_check method face -- off this row.
        _rb = unwrap_readonly(unwrap_send_sync(recv_t))
        if isinstance(_rb, OptionalType) and _rb.uses_pointer_repr():
            recv_t = unwrap_readonly(_rb.inner)
            _witness("iter.narrowed_opt_container_view")
    # Record-VALUE dicts admit too (`[p for _, p in point_map.items()]`):
    # the caller's element/unpack-target gates decide what the loop var can
    # bind; the view call render is value-family-blind. Optional-record
    # and CONTAINER values (`dict[str, list[Int32]]` -- the items/values
    # element aliases the live container) ride the same blind render.
    return (_container_scalar_read(recv_t, analyzer)
            or _container_record_elem(recv_t, analyzer)
            or _container_opt_record_elem(recv_t, analyzer)
            or _container_ref_alias_elem(recv_t, analyzer)
            # A genrec-element dict (`dict[K, DictTree[K, V]]`): the view
            # render is element-family-blind; the loop var binds `auto&&`.
            or _container_genrec_elem(recv_t, analyzer)
            # A value-opt-scalar-valued dict (`dict[str, Int32 | None]`):
            # the loop var binds the storage optional by value
            # (`std::optional<int32_t> val = *__beg_N;`).
            or _container_value_opt_scalar_elem(recv_t, analyzer)
            # ... and `keys()` over ANY value family: the view yields the
            # KEY, so neither `::tpy::dict_keys(d)` nor what the consumer
            # binds off it depends on what the dict maps to. Last, so the
            # rows above keep owning the verdicts they already had. The
            # shared dispatch still applies the key slice and the
            # `Own[container]` exclusion; `values()` / `items()` yield the
            # VALUE and stay on the rows above.
            or (e.method == "keys"
                and is_dict(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(recv_t))))
                and _container_elem_family(recv_t, analyzer, lambda _v: True)
                and bool(_witness("iter.dict_keys_value_blind"))))

def _plain_scalar_slot(ptype: TpyType | None, analyzer) -> bool:
    """A NON-Own value-scalar param slot. The user-record sibling of
    `_scalar_pass_through_slot`: a plain user method is not an inline
    template, so the argument render's Own arm copies an `Own[scalar]` arg
    into a temp and moves it (`auto __tmp_N = n; ...(std::move(__tmp_N))`) --
    Own slots reject here."""
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
    # The decl target type (value-scalar subset): the binding
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
    # A pending container binding (an alias of a not-yet-promoted literal
    # local, `b = a`) resolves through the shared sema record, as the
    # decl-type normalizer does; an unresolved pending stays put and keeps
    # rejecting at the family gates.
    target = resolve_pending_container(target, analyzer) or target
    return resolve_int_literals(target, analyzer.ctx.default_int_for_literal)


def _inst_slice_arg_ok(arg: 'TpyExpr', analyzer) -> bool:
    """A list/Span-yielding slice subscript admitted as a container
    instantiation arg (`list(argv[i:])`) or a native/template value arg
    (`len(items[1:1])` -> `__len__(::tpy::list_slice(items, ..))`) -- the
    inline rvalue render is position-independent."""
    if not (isinstance(arg, TpySubscript)
            and arg.slice_function_info is not None
            and isinstance(arg.index, TpySlice)):
        return False
    sb = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(arg))))
    return is_list(sb) or is_span(sb)
