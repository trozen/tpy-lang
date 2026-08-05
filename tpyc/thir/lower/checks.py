"""Per-arm checks and classifiers shared by expression and statement lowering.

These helpers run from the lowering arm that consumes their result. They do not
predictively traverse a body or expression before lowering.
"""

from __future__ import annotations
import math
from collections.abc import Set as AbstractSet
from dataclasses import field
from typing import Callable, NamedTuple
from ...parse.nodes import (
    FSTRING_CONV_REPR,
    FSTRING_CONV_STR,
    FunctionLinkage,
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictComprehension,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyGeneratorExpression,
    TpyIfExpr,
    TpyIntLiteral,
    TpyLambda,
    TpyListComprehension,
    TpyListRepeat,
    TpyMethodCall,
    TpyName,
    TpyNamedExpr,
    TpyNoneLiteral,
    TpySetComprehension,
    TpySetLiteral,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
    TpyUnaryOp,
    TpyVarDecl,
)
from ...typesys import (
    AnyType,
    BOOL,
    CallableType,
    ConcreteCoroType,
    FLOAT,
    FloatLiteralType,
    IntLiteralType,
    LiteralType,
    NoneType,
    NominalType,
    OptionalType,
    OwnType,
    ParamInfo,
    RecursiveAliasInstanceType,
    PtrType,
    ReadonlyType,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    container_to_str_template,
    contains_type_param,
    recursive_union_alternatives,
    substitute_type_params_simple,
    del_suppresses_default_ctor,
    is_dyn_protocol,
    is_fn_type,
    is_float_type,
    is_protocol_type,
    is_void_like_type,
    resolve_int_literals,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...modules.type_resolution import is_native_iterable
from ...type_def_registry import (
    is_varargs,
    enum_info_of,
    int_traits_of,
    is_array,
    is_big_int_type,
    is_bool_type,
    is_bytearray_type,
    is_bytes_type,
    is_bytes_view_type,
    is_dict,
    is_enum_type,
    is_fixed_int_type,
    is_float32_type,
    is_list,
    is_set,
    is_span,
    is_str_type,
    is_str_view_type,
    is_string_type,
)
from ...codegen_cpp.builtins import _FLOAT_STR_CONSTANTS
from ...codegen_cpp.functions import literal_mangled_name
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.forms import (LocalBinding, classify_local_binding,
                                  reads_storage_form_optional)
from ...codegen_cpp.protocols import (classify_dyn_own_arg, dyn_forward_ok,
                                      resolve_own_source_type)
from ...value_category import call_returns_cpp_ref, is_rvalue_source
from ...codegen_cpp.context import (
    escape_cpp_name,
    imported_free_callee_cpp,
    module_qualified_callee_cpp,
    module_static_class_cpp,
    static_method_callee_cpp,
)
from ...compilation_context import get_current_compiler
from ... import qnames
from ..faces import witness as _witness
from ..fallback import expr_kind_tag, note_detail
from .generics import expand_fi_template
from ..nodes import (
    PrintForm,
    THIRBinOp,
    THIRCall,
    THIRCtorCall,
    THIRFieldAccess,
    THIRForEach,
    THIRFormConvert,
    THIRLiteral,
    THIRMethodCall,
    THIRSelf,
    THIRStrSlice,
)
from .predicates import (
    _container_ternary_arg,
    _protocol_union_arg,
    _nullable_static_protocol_param,
    _static_protocol_union_binding,
    _bare_module_recv,
    _module_var_recv,
    _module_var_read_cpp,
    _record_getitem_key,
    _record_getitem_idx_recv_ok,
    _subscript_index_and_tuple,
    _callable_value,
    _ptr_opt_borrow_call_ret,
    _f1_tuple,
    _arg_ptr_union_slot,
    _union_bytes_literal_temp_arg,
    _bigint_index_disposition,
    _call_ret_union_ok,
    _bytes_concat_operand,
    _coerce_disposition,
    _SPANLIKE_COERCIONS,
    _SPAN_METHOD_COERCIONS,
    _const_exact_field_receiver_ok,
    _const_index,
    _container_pass_through_arg,
    _native_iterable_container_arg,
    _native_iterable_field_arg,
    _native_iterable_call_arg,
    _native_iterable_range_arg,
    _native_iterable_genexpr_arg,
    _container_record_elem,
    _container_ref_alias_elem,
    _container_str_elem,
    _set_method_recv,
    _container_elem_family,
    _container_scalar_read,
    _container_value_opt_scalar_elem,
    _container_value_tuple_elem,
    _dict_view_iterable_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_wrapper_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_ptr_union_wide,
    _eligible_value_union,
    _union_member_ctor_slot,
    _wrapper_member_ctor_slot,
    _enum_neg_wrap,
    _f1_record,
    _method_rvalue_f1_record,
    _native_iter_value_slot,
    _protocol_auto_slot,
    _field_decl_type,
    _field_markers_clean,
    _field_over_subscript_ok,
    _field_over_record_getitem_ok,
    _record_getitem_borrow_subscript,
    _borrow_tuple_param_elem_subscript,
    _tuple_field_opt_elem_subscript,
    _empty_instantiation_family,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    copy_call_arg,
    _tuple_literal_has_ref_elements,
    _btuple_literal_elems_rvalue,
    _tuple_elem_slots_ptr_optional,
    _tuple_elem_slots_record_lvalue,
    _unbound_self_field_ok,
    _ptr_value_field_recv_ok,
    _user_deref_field_recv_ok,
    _folded_neg_int_literal,
    _is_bytes_family,
    _is_type_param_slot,
    _container_tparam_elem,
    _span_slot,
    _is_string_owned,
    _isinstance_narrow_info,
    _any_narrow_info,
    _member_valued_union_slot,
    _narrow_bigint_index,
    _narrow_fact_member,
    _narrow_facts_ok,
    _nonvalue_container_ret,
    _optional_checked_field,
    _optional_checked_field_over_field_ok,
    _optional_ptr_arg_face,
    _opt_view_arg_shim,
    _optional_ptr_borrow,
    _own_declared_call_ret,
    _own_storage_opt_param,
    _optional_ptr_borrow_name,
    _own_cascade_fires,
    _own_dyn_return,
    _own_lvalue_temp_slot,
    _owned_str_append_target,
    _owned_str_slot,
    _peel_coerce,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_or_opt_own_slot,
    _plain_own_slot,
    _plain_scalar_slot,
    _positional_only_template,
    _protocol_arg_slot,
    _protocol_arg_temp,
    _bounded_tparam_protocol,
    _protocol_binding,
    _record_rvalue_temp_slot,
    _record_setitem_value,
    _resolve_literal_seeded,
    _opt_pointee_wide,
    _optional_ptr_borrow_wide,
    _resolve_plain_alias,
    _ru_container_literal_ok,
    _ru_elem_ok,
    _ru_wrapper_arg_slot,
    _ru_wrapper_borrow_call_arg,
    _ru_wrapper_field_arg,
    _ru_wrapper_member_name_arg,
    _ru_wrapper_member_rvalue_arg,
    _ru_wrapper_own_call_arg,
    _ru_wrapper_name_arg,
    _ru_wrapper_scalar_literal_arg,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _generic_root_subst,
    _is_range_call,
    _range_counter_type,
    _runtime_bigint,
    _scalar_pass_through_slot,
    _range_object_value,
    _slice_object_type,
    _owned_tuple_call_ret,
    _span_value,
    _storage_call_ret,
    _storage_optional_return_type,
    _unwrap_own,
    _str_concat_operand,
    _subscript_container_recv_type,
    _tparam_value,
    _tuple_subscript_value_read,
    _type_family_tag,
    _union_binding_divergent,
    _unwrap_lit_coerce,
    _dict_key_shape_ok,
    _none_value_opt_arg,
    _str_literal_value_opt_arg,
    _value_opt_scalar_value_arg,
    _value_opt_callable_pass_arg,
    _value_opt_view_whole_arg,
    _value_opt_member_arg,
    _value_opt_pass_through_arg,
    _value_opt_owned_str,
    _value_opt_scalar,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_owned_view,
    _value_opt_view_name,
    _value_record_member,
    _tuple_container_elem_read,
    _value_tuple,
    _value_tuple_element_ok,
    _value_tuple_nested,
    _value_union_temp_slot,
    _var_decl_type,
)
from .context import (
    _Prescan,
)

def _ptr_union_source_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer,
                         u: 'UnionType', *, allow_field: bool) -> bool:
    """A source expression for a pointer-variant local decl/reseat or a
    union-field write: a bare name whose binding is the SAME union (a
    borrow-form copy, rendered bare), or -- when `allow_field` -- a
    value-variant field lvalue off an F1-record receiver. At a local decl the
    field source lifts via `to_[const_]ptr_variant` and is gated to
    single-assignment locals (a reseat's const verdict comes from its own
    receiver, a mixed-const reseat chain the slice does not reproduce); at a
    field write it copies storage-to-storage with no lift, so no const
    question arises."""
    if isinstance(e, TpyName):
        bt = declared.get(e.name)
        if bt is None:
            return False
        bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
        return bt == u
    if allow_field and isinstance(e, TpyFieldAccess):
        # Strict receiver: the local-decl consumer spells the receiver's
        # const verdict (see _const_exact_field_receiver_ok); the field-write
        # consumer is const-blind but shares the arm -- conservative.
        if not _const_exact_field_receiver_ok(e, declared, analyzer):
            return False
        ft = analyzer.get_expr_type(e)
        ft = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
              if ft is not None else None)
        return ft == u
    if isinstance(e, (TpyCall, TpyMethodCall)):
        # A call whose C++ return is ALREADY the pointer variant (a plain
        # same-union return, NOT an `Own[union]` factory -- that one
        # materializes through the UNION_RVALUE storage slot) assigns
        # bare: `got = cycle(start);`.
        fi = e.resolved_function_info
        if fi is not None and isinstance(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    fi.return_type))), OwnType):
            return False
        rt = analyzer.get_expr_type(e)
        rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
              if rt is not None else None)
        return rt == u
    if isinstance(e, TpyIfExpr):
        # A ternary joining two union sources renders as one expression
        # with PER-ARM normalization (`((c) ? (p) :
        # (::tpy::to_ptr_variant(h.pet)))` -- the ifexpr lowering's
        # ptr-union row); each arm re-classifies here, const/mixed arm
        # shapes gate there.
        return (_ptr_union_source_ok(e.then_expr, declared, analyzer, u,
                                     allow_field=allow_field)
                and _ptr_union_source_ok(e.else_expr, declared, analyzer, u,
                                         allow_field=allow_field))
    return False

def _compound_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], TpyExpr] | None':
    """The U4 compound narrowing condition: an `and` tree (`&&` TpyBinOp --
    the parser folds `a and b` to that) with EXACTLY ONE isinstance-narrow
    leaf (un-folded), every other leaf an eligible bool condition. Leaves
    AFTER the isinstance see the subject retyped to the single concrete
    member (sema narrowed their reads; they render as the inline deref);
    leaves before it see the un-narrowed subject. `or` trees and multiple
    isinstance leaves (facts on several vars) stay AST. Returns
    `(var, union, members, isinstance_leaf)` or None."""
    if not (isinstance(cond, TpyBinOp) and cond.op == "&&"):
        return None
    leaves: list[TpyExpr] = []

    def flat(e: TpyExpr) -> None:
        if isinstance(e, TpyBinOp) and e.op == "&&":
            flat(e.left)
            flat(e.right)
        else:
            leaves.append(e)

    flat(cond)
    hits = [(i, _isinstance_narrow_info(l, declared, analyzer))
            for i, l in enumerate(leaves)]
    hits = [(i, inf) for i, inf in hits if inf is not None]
    if len(hits) != 1:
        return None
    idx, (var, u, members, folded) = hits[0]
    if folded:
        return None
    # The inline THIRNarrowedRead render has no wrapper `.value` spelling;
    # compound conditions on wrapper-union subjects stay AST.
    if u.needs_wrapper():
        return None
    return var, u, members, leaves[idx]

def _narrow_cond_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool, TpyExpr | None] | None':
    """A narrowing if/while/assert condition, simple or compound:
    `(var, union, members, folded, isin_leaf)`. `isin_leaf` is None for the
    simple form (the whole condition is the isinstance test, possibly negated);
    a compound condition is never sema-folded (folded=False). A leading `not`
    over a bare isinstance is the negated-polarity simple form: sema's branch
    type-facts already carry the correct member per branch, so only the rendered
    condition flips (`_lower_narrow_cond` wraps it in `!`)."""
    negated = isinstance(cond, TpyUnaryOp) and cond.op == "!"
    inner = cond.operand if negated else cond
    info = _isinstance_narrow_info(inner, declared, analyzer)
    if info is not None:
        # A folded (exhaustiveness `true`) isinstance under a `not` is not this
        # shape -- the fold already suppressed the dead branch.
        if negated and info[3]:
            return None
        return (*info, None)
    c = _compound_narrow_info(cond, declared, analyzer)
    if c is None:
        return None
    var, u, members, isin = c
    return var, u, members, False, isin

def _any_narrow_cond_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, tuple[TpyType, ...], bool] | None':
    """The D15 Any-isinstance if condition, simple or negated-simple:
    `(var, check_members, negated)`. The Any sibling of `_narrow_cond_info`;
    compound (`&&`) conditions stay AST (no inline-read machinery for the
    Any slice)."""
    negated = isinstance(cond, TpyUnaryOp) and cond.op == "!"
    inner = cond.operand if negated else cond
    info = _any_narrow_info(inner, declared, analyzer)
    if info is None:
        return None
    var, members = info
    return var, members, negated


def _assert_narrow_info(
        stmt: TpyAssert, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, TpyType] | None':
    """The U4 first-narrow assert: `assert isinstance(v, A)` on an
    un-narrowed routed-union subject with a concrete member fact. The AST
    emits the negated holds test + a PERSISTENT extraction alias
    (`_gen_assert` -> `_emit_isinstance_extractions(persistent=True)`), and
    the narrowing holds for the rest of the enclosing scope. Returns
    `(var, union, member)`, or None for every other assert shape (a plain
    assert, a union fact -- which extracts nothing -- or a sema-folded
    condition, the re-assert arm)."""
    info = _narrow_cond_info(stmt.condition, declared, analyzer)
    if info is None:
        return None
    var, u, _members, folded, _isin = info
    if folded or not _narrow_facts_ok(u, stmt.then_type_facts, var):
        return None
    m = _narrow_fact_member(u, stmt.then_type_facts, var)
    if m is None:
        return None
    return var, u, m

def _enum_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """An enum value (name / member access / field read) into a same-enum
    by-value param slot: enums are value scalars for the ownership cascade
    (`own is None`), so both paths render the bare expression. An `Own[enum]`
    slot rejects (`_eligible_enum` does not peel Own)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    et = _eligible_enum(pt, analyzer)
    if et is None:
        return False
    at = _eligible_enum(analyzer.get_expr_type(a), analyzer)
    return at == et

def _container_literal_decl_ok(stmt: TpyVarDecl, declared: dict[str, TpyType],
                               prescan: '_Prescan', analyzer) -> bool:
    """First decl of a container-literal local: `xs = [1, 2]` / `xs: list[T] = []`
    / `d = {k: v}` / `s = {a, b}`. The decl's binding type is sema's RESOLVED
    container (a list literal's vector-vs-array decision -- the PendingListType
    resolution -- is final before lowering), so the emit is a pure function of
    that type + the elements. Element families and the per-slot rules live in
    `_container_lit_elem_ok` (scalars, owned str/bytes, enums,
    Optional[scalar], value tuples, nested list literals, F1 records); the
    `make_vector`/`make_ordered_*` move/nocopy switch is mirrored at lowering
    off the same `movable_locals` + last-use facts the AST reads. A `[0] * n`
    repeat (TpyListRepeat) is admitted too -- its own family gate + the
    _gen_list_repeat mirror in the lowering arm. The empty-literal-to-Array
    reject is defensive-only: sema errors on both routes to that shape (a bare
    `[]` is un-inferable; an `Array[T, 0]` annotation mismatches the literal),
    so only the empty LIST form (the spelled `std::vector<T>{}` emit) is
    reachable."""
    # A reassigned container local is a POINTER-LOCAL on the AST path (`a = b`
    # rebinds the alias -- `std::vector<T>* a = &__slot_N; ... a = &(b);` -- so a
    # later `a.append` mutates the aliased list, Python's rebinding semantics).
    # The plain value decl this cell emits would silently copy instead; reject
    # (hoisted / move-through conservatively ride along).
    is_lit = isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                    TpySetLiteral, TpyListRepeat))
    if (stmt.name in prescan.reassigned or stmt.name in prescan.hoisted
            or stmt.name in prescan.move_through):
        return note_detail("container_lit.rebound") if is_lit else False
    t = _var_decl_type(stmt, analyzer)
    if t is None:
        return note_detail("container_lit.decl_type") if is_lit else False
    return _container_literal_shape_ok(stmt.init, t, analyzer, note=True)

def _container_literal_shape_ok(init: TpyExpr, t: TpyType, analyzer, *,
                                note: bool = False,
                                threaded: bool = True) -> bool:
    """Check only the literal/target family and target slots.

    Element expressions are checked by their consuming lowering arm, where
    each child is lowered exactly once.
    """
    if isinstance(init, TpyDictLiteral):
        args = getattr(t, "type_args", None)
        if not is_dict(t) or not args or len(args) < 2:
            return _note_container_lit_reject(init, t, analyzer) if note else False
        key = args[0]
        if not _dict_key_shape_ok(key, analyzer):
            if note:
                fam = _container_lit_slot_family(key, analyzer) or "scalar"
                return note_detail(f"container_lit.key.{fam}")
            return False
        return True
    if isinstance(init, TpySetLiteral):
        args = getattr(t, "type_args", None)
        return bool(is_set(t) and args)
    if isinstance(init, TpyArrayLiteral):
        if is_dict(t) or is_set(t):
            return _note_container_lit_reject(init, t, analyzer) if note else False
        if not init.elements:
            if not is_list(t):
                return note_detail("container_lit.empty_array") if note else False
            if not threaded:
                return note_detail("container_lit.nested_empty") if note else False
        args = getattr(t, "type_args", None)
        if is_span(t):
            return bool(args) and _eligible_scalar(unwrap_readonly(args[0]))
        if not (is_list(t) or is_array(t)) or not args:
            return _note_container_lit_reject(init, t, analyzer) if note else False
        return True
    if isinstance(init, TpyListRepeat):
        # `[e] * n` -> materialized list or Array aggregate (the _gen_list_repeat
        # mirror in the lowering arm). A lazy `ListRepeatType` / Span / protocol
        # target stays on the AST path (see the lowering arm's reject), so gate
        # to the two routed families here.
        if not (is_list(t) or is_array(t)):
            return _note_container_lit_reject(init, t, analyzer) if note else False
        return True
    return False

def _record_source_call(e: TpyExpr, analyzer) -> bool:
    """A record RVALUE source for a container-literal element: a constructor
    whose ctor/instantiation shape routes, a record-returning call, or a
    record-returning METHOD-call rvalue (`rc.clone()` -- shallow like the
    owned-record decl's method row: the method-call lowering arm validates
    its receiver/args itself and falls the body back on its own rejects).
    Shared by the record element arm and the record-inner-Optional element
    arm."""
    if isinstance(e, TpyMethodCall):
        return (_f1_record(analyzer.get_expr_type(e), analyzer)
                and is_rvalue_source(analyzer, e))
    if not isinstance(e, TpyCall):
        return False
    rfi = e.resolved_function_info
    if rfi is not None and rfi.is_constructor:
        return _ctor_shape_ok(e, analyzer) or _ctor_instantiation_ok(e, analyzer)
    return _record_rvalue_call_shape(e, analyzer)

def _container_lit_elem_ok(e: TpyExpr, slot: 'TpyType | None',
                           declared: dict[str, TpyType], analyzer, *,
                           threaded: bool, forced: bool,
                           allow_record: bool = False,
                           allow_nested: bool = False,
                           allow_optional: bool = False,
                           note: bool = False,
                           pointers: 'AbstractSet[str]' = frozenset()) -> bool:
    """One container-literal element / dict value against its RESOLVED slot.

    `threaded` = the parent literal itself was rendered with a target;
    `forced` = the parent position threads every element target regardless of
    family (a demoted Array's elements, a dict's keys/values) -- a LIST
    threads only the special families (str/bytes/Optional/tuple), so a nested
    list element is threaded only under `forced`. The wrap-relevant arms
    (view->owned copies, the target-typed None/tuple renders) require their
    position to be threaded; the target-free families (scalar, enum, record)
    render identically either way.

    Reject arms record the permanent `container_lit.elem.*` drilldown detail
    (`note`, first-reject-wins) so the fallback tally names the blocking
    element family."""
    if slot is None:
        return note_detail("container_lit.slot_family") if note else False
    fam = _container_lit_slot_family(slot, analyzer)
    if fam is None:  # value scalar / owned str (the original S5 slice)
        if (not threaded and _owned_str_slot(slot, analyzer)
                and not isinstance(e, TpyStrLiteral)):
            # An un-threaded str slot gets no elem target on the AST path, so
            # the S5 view->owned wrap does not fire there; only the
            # form-neutral literal render is byte-identical.
            return note_detail("container_lit.nested_view") if note else False
        return True
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if fam == "bytes":
        bt = _resolved_bytes_value(su, analyzer)
        if bt is None or not is_bytes_type(bt):
            return note_detail("container_lit.elem.bytes") if note else False
        if not threaded and not isinstance(e, TpyBytesLiteral):
            # Mirrors the un-threaded str rule: the `::tpy::bytes_copy` wrap
            # fires only at threaded positions (a bytes literal renders its
            # owned form target-free, so it stays admitted).
            return note_detail("container_lit.nested_view") if note else False
        return True
    if fam == "enum":
        if (_eligible_enum(analyzer.get_expr_type(e), analyzer) is not None):
            return True
        return note_detail("container_lit.elem.enum") if note else False
    if fam == "optional":
        # Optional element slot. Compositional: a bare `None` renders
        # `std::nullopt`, and any other source routes iff the element
        # EXPRESSION routes -- the inner value's storage form (scalar bare,
        # owned-str view->owned wrap) is a pure function of the slot type that
        # `_lower_container_elem` threads through the Optional inner identically
        # to the AST's implicit `T -> std::optional<T>` conversion. `threaded`
        # gates the wrap-bearing str inner.
        if not (allow_optional and threaded and isinstance(su, OptionalType)):
            return note_detail("container_lit.elem.optional") if note else False
        if su.uses_pointer_repr():
            # A record-inner Optional element: the container STORAGE slot is
            # `std::optional<P>` (value), NOT the borrow-form `P*` that
            # uses_pointer_repr() describes -- so a record ctor lands via the
            # implicit `P -> std::optional<P>` and a bare None renders
            # std::nullopt, same as a value-inner Optional. Record NAMES /
            # container inners / force_pointer_repr value inners defer (a name
            # would need the pointer-local deref + move mirror threaded through
            # the Optional inner).
            inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(su.inner)))
            # A CONTAINER-inner Optional element (`list[list[int] | None]`):
            # a nested ARRAY literal takes the AST's union_prefix render
            # through the Optional inner (`std::vector<T>{...}` converts
            # implicitly); a bare None is the nullopt STORAGE store.
            if (is_list(inner) or is_array(inner)):
                if isinstance(e, (TpyNoneLiteral, TpyArrayLiteral)):
                    return True
                return note_detail(
                    "container_lit.elem.optional") if note else False
            if not (isinstance(inner, NominalType) and inner.is_user_record):
                return note_detail("container_lit.elem.optional") if note else False
            if isinstance(e, TpyNoneLiteral) or _record_source_call(e, analyzer):
                return True
            return note_detail("container_lit.elem.optional") if note else False
        if isinstance(e, TpyNoneLiteral):
            return True  # -> std::nullopt (the STORAGE-form None)
        return True
    if fam == "tparam":
        # A `T` element slot in a generic body (`return [x]` ->
        # `return {x};`): a bare declared NAME renders bare into the brace
        # init. The lowering row enforces the strict slice (non-narrowed,
        # non-pointer, non-move) and rejects the rest.
        if isinstance(e, TpyName) and e.name in declared:
            return True
        return note_detail("container_lit.elem.tparam") if note else False
    if fam == "union":
        # A VALUE-union element slot (`std::variant<...>`): literal elements
        # convert implicitly and render BARE on both paths (`{1, "two"}`
        # into `std::vector<std::variant<int32_t, std::string>>`).
        # Non-literal sources (names, calls -- the member-selection /
        # to_ptr_variant renders) stay AST.
        if (_eligible_value_union(su) is not None
                and isinstance(e, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyBoolLiteral, TpyStrLiteral))):
            return True
        # A MIXED (non-value) union stores a value variant at element
        # positions too: a scalar/str literal still renders bare (the
        # converting ctor picks the member), and a nested ARRAY literal
        # with a UNIQUE list/array member takes the AST's union_prefix
        # render (`std::vector<T>{...}` -- the typed member ctor).
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyBoolLiteral, TpyStrLiteral))):
            return True
        # `None` into a three-way union with a None member renders the
        # monostate alternative (`std::monostate{}` -- the emit's
        # UnionType None row).
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, TpyNoneLiteral)
                and any(is_void_like_type(unwrap_readonly(m))
                        for m in su.members)):
            return True
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, TpyArrayLiteral)
                and len([m for m in su.members
                         if is_list(unwrap_readonly(m))
                         or is_array(unwrap_readonly(m))]) == 1):
            return True
        # M4c: a wrapper element slot absorbs a member-record ctor rvalue
        # via the wrapper's template converting ctor -- bare render on both
        # paths (`{Leaf(1), Leaf(2)}` into `std::vector<Tree>`).
        if _wrapper_member_ctor_slot(e, su, analyzer):
            return True
        # A scalar LITERAL at a wrapper element slot renders bare the same
        # way (`std::vector<V> xs = {1, 2, 3}` -- the converting ctor
        # absorbs the literal), the wrapper twin of the value-union row.
        if (_eligible_wrapper_union(su, analyzer) is not None
                and isinstance(e, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyBoolLiteral, TpyStrLiteral))):
            return True
        # A SAME-WRAPPER NAME element (`[branch, leaf]` at `list[Tree]`):
        # the bare copy -- moved at a movable last use by the element
        # lowering's shared `_maybe_move` mirror -- the name twin of the
        # wrapper literal rows. Pointer-locals stay out.
        if (isinstance(e, TpyName) and e.name not in pointers
                and (_wl_su := _eligible_wrapper_union(su, analyzer))
                is not None):
            bt = declared.get(e.name)
            bu = (_resolve_plain_alias(
                      unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt))),
                      analyzer)
                  if bt is not None else None)
            if isinstance(bu, UnionType) and bu == _wl_su:
                return True
        # The plain-union twin: a member-record ctor rvalue into an
        # all-record value-variant element slot (`{Dog("Rex"), Cat("W")}`).
        if _union_member_ctor_slot(e, su, analyzer):
            return True
        # A member-record NAME into the same all-record value-variant slot
        # (`[r, Circle()]` -> `make_vector<variant<..>>(std::move(r),
        # Circle())`): the converting ctor absorbs the bare/moved name; the
        # make/move switch rides the shared move facts. Pointer-locals stay
        # out. A SAME-UNION name (`[a, b]` over `a: Cat | Dog`) admits too:
        # the lowering row discriminates the tracked ptr-variant binding
        # (the `to_value_variant` lift) from a narrowed alias (bare member)
        # and REJECTS untracked bindings -- the union narrow binding-form
        # fence -- so this admission alone never picks a render.
        if (isinstance(e, TpyName) and e.name not in pointers
                and isinstance(su, UnionType) and not su.needs_wrapper()):
            bt = declared.get(e.name)
            bu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
                  if bt is not None else None)
            members = [unwrap_readonly(m) for m in su.members]
            all_record = (bool(members)
                          and all(isinstance(m, NominalType)
                                  and m.is_user_record for m in members))
            if (all_record and isinstance(bu, NominalType)
                    and bu.is_user_record and any(m == bu for m in members)):
                return True
            if all_record and isinstance(bu, UnionType) and bu == su:
                return True
        return note_detail("container_lit.elem.union") if note else False
    if fam == "tuple":
        if not threaded:
            return note_detail("container_lit.elem.tuple") if note else False
        vt = _value_tuple(su, analyzer)
        if (vt is None and isinstance(su, TupleType)
                and isinstance(e, TpyTupleLiteral)
                and all(_value_tuple_element_ok(_te, analyzer)
                        or _eligible_value_union(unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(_te))))
                        is not None
                        for _te in su.element_types)):
            # Value-union tuple elements (`list[tuple[str, Int32 | str]]`):
            # the literal member lands bare in the variant slot of the
            # spelled tuple ctor (the render arm's tuple_union_elem row).
            return True
        if vt is not None:
            # A value-tuple NAME copies into the element slot (value type -- no
            # aliasing); `_container_elem_move_source` value-type-filters, so it
            # never moves, matching the AST's copy for a value-tuple. (An owned
            # `std::tuple<...>&&` param the AST's seed_param_locals would MOVE is
            # not a value-tuple binding, so it never resolves here.)
            if isinstance(e, TpyName):
                bt = declared.get(e.name)
                if bt is not None and _value_tuple(
                        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt))),
                        analyzer) is not None:
                    return True
            return (isinstance(e, TpyTupleLiteral)
                    or (note_detail("container_lit.elem.tuple") if note else False))
        # A non-value (pointer-repr-element) tuple LITERAL stores via
        # `tuple_to_storage<S>(S{...})`: borrow and storage forms differ. This
        # arm emits the STORAGE-form inner `S{...}` (bare member values), which
        # is byte-exact only when every non-value member is an RVALUE (ctor /
        # literal). A non-value member NAME/lvalue would build the BORROW-form
        # inner (`&name`, `T*` element) and defers. Members are otherwise
        # admitted compositionally (the recursion records the blocking member).
        def _tuple_member_ok(i: int) -> bool:
            sub = e.elements[i]
            mslot = su.element_types[i]
            mbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(mslot)))
            # Storage-direct is byte-exact only for scalar / owned-str members
            # (the None family) and F1-record members. Pointer-repr Optional /
            # union / nested-tuple members route through a borrow intermediate
            # (`tuple_value_to_borrow`, `&name`) the AST spells differently --
            # they defer, along with any non-value lvalue NAME member.
            mfam = _container_lit_slot_family(mbare, analyzer)
            if mfam == "container":
                # A nested list LITERAL member renders its bare brace inside
                # the storage tuple (`S{1, {2, 3}}` -- the jagged-list shape);
                # container NAMES / dict / set literals defer.
                if not (isinstance(sub, TpyArrayLiteral)
                        and (is_list(mbare) or is_array(mbare))):
                    return (note_detail("container_lit.elem.tuple")
                            if note else False)
            elif mfam not in (None, "record"):
                return note_detail("container_lit.elem.tuple") if note else False
            if (not mbare.is_value_type()
                    and not is_rvalue_source(analyzer, sub)):
                return note_detail("container_lit.elem.tuple") if note else False
            return _container_lit_elem_ok(
                sub, mslot, declared, analyzer, threaded=True, forced=True,
                allow_record=True, allow_nested=True, allow_optional=True,
                note=note, pointers=pointers)
        if (isinstance(e, TpyTupleLiteral) and isinstance(su, TupleType)
                and len(e.elements) == len(su.element_types)):
            # A ref-element tuple takes the BORROW ladder
            # (`tuple_to_storage<S>(std::tuple<T*, ..>{&(a), nullptr})`),
            # whose per-element admission lives in `_lower_borrow_tuple_literal`
            # and rejects there -- the storage-direct member rules below do not
            # describe it.
            if (_tuple_literal_has_ref_elements(e, su)
                    and (_tuple_elem_slots_ptr_optional(su)
                         or _tuple_elem_slots_record_lvalue(e, su, analyzer))):
                return True
            # Otherwise the storage-DIRECT member rules still apply: this arm
            # ADDS the borrow ladder, it does not replace the rows that were
            # already admitted (a ref-element tuple whose members are all
            # rvalues renders storage-direct on both paths).
            if all(_tuple_member_ok(i) for i in range(len(e.elements))):
                return True
        return note_detail("container_lit.elem.tuple") if note else False
    if fam == "container":
        # A nested container VALUE literal (list/array via array literal, dict
        # via dict literal, set via set literal) recurses through the same
        # shape check against its own resolved container slot -- the element's
        # consuming lowering re-lowers the literal target-typed by `su`.
        if allow_nested and is_dict(su) and isinstance(e, TpyDictLiteral):
            return _container_literal_shape_ok(e, su, analyzer, note=note)
        if allow_nested and is_set(su) and isinstance(e, TpySetLiteral):
            return _container_literal_shape_ok(e, su, analyzer, note=note)
        # A bare container NAME into an owned container slot: copies (or moves
        # at last use, via `_container_elem_move_source`) into the slot, like
        # any other name element. Restricted to a plain (non-pointer) local of
        # the matching container family so the emit is a bare `name`.
        if (allow_nested and isinstance(e, TpyName)):
            bt = declared.get(e.name)
            if bt is not None:
                bu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
                if ((is_list(su) and is_list(bu))
                        or (is_dict(su) and is_dict(bu))
                        or (is_set(su) and is_set(bu))):
                    return True
        if not (allow_nested and (is_list(su) or is_array(su))
                and isinstance(e, TpyArrayLiteral)):
            return note_detail("container_lit.elem.container") if note else False
        return True
    if fam == "record":
        if not (allow_record and _f1_record(su, analyzer)):
            return note_detail("container_lit.elem.record") if note else False
        if isinstance(e, TpyName):
            # A bare record name copies (brace-init), derefs for an F2
            # pointer-local, and moves at a movable local's last use -- all
            # mirrored at lowering off the same facts the AST reads.
            bt = declared.get(e.name)
            if (bt is not None and _f1_record(bt, analyzer)):
                return True
            return note_detail("container_lit.elem.record") if note else False
        # `copy(name)` -- the copy-construct rvalue (`P(p)`): the elem
        # lowering's `_lower_copy_record` row (exact slot match) renders it;
        # a shape that slips past this admission (pointer-local source not in
        # `pointers`) returns None there and the generic call tail rejects --
        # body fallback, never a divergent render.
        if copy_plain_record_source(e, analyzer, pointers) is not None:
            return True
        return (_record_source_call(e, analyzer)
                or (note_detail("container_lit.elem.record") if note else False))
    if fam == "any":
        # Any element slot: each element wraps into a `tpy::Any` cell via
        # make_any (`_lower_container_elem`'s Any arm). A nested container
        # element needs the brace-vs-paren prefix render (render-text
        # dependent), so it stays on the AST path; every scalar / str / bytes /
        # None / already-Any element is placeholder-safe.
        au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(e))))
        if is_list(au) or is_dict(au) or is_set(au):
            return note_detail("container_lit.elem.any_container") if note else False
        return True
    return note_detail(f"container_lit.elem.{fam}") if note else False

def _container_lit_slot_family(t: 'TpyType | None', analyzer) -> 'str | None':
    """Family tag for one container-literal element/key/value slot: None for
    the base slice (value scalar / owned str), else the family name. Doubles
    as `_container_lit_elem_ok`'s dispatch key and the permanent
    `container_lit.elem.*` drilldown sub-classifier for the rejected
    families."""
    if t is None:
        return "untyped"
    # _resolved_scalar, not _eligible_scalar: a native CALL-ARG literal keeps
    # Int/FloatLiteralType element slots (sema resolves decl slots, not arg
    # slots) -- the AST resolves them at render and the element lands bare,
    # so the base-scalar family is the byte-identical classification.
    if _resolved_scalar(t, analyzer) or _owned_str_slot(t, analyzer):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, TypeParamRef):
        return "tparam"
    if isinstance(u, OptionalType):
        return "optional"
    if isinstance(u, UnionType):
        return "union"
    if isinstance(u, TupleType):
        return "tuple"
    if is_list(u) or is_dict(u) or is_set(u) or is_array(u) or is_span(u):
        return "container"
    if is_enum_type(u):
        return "enum"
    if is_str_view_type(u) or is_bytes_view_type(u):
        return "view"
    if is_bytes_type(u):
        return "bytes"
    if isinstance(u, AnyType):
        return "any"
    if isinstance(u, NominalType) and u.is_user_record:
        return "record"
    return "other"

def _note_container_lit_reject(init: TpyExpr, t: TpyType, analyzer) -> bool:
    """Record the family-level `container_lit.*` sub-classifier detail for a
    declaration reject (always returns False, like `note_detail`): the DECL/slot
    type is outside the mirrored container families -- an `Own[container]`
    binding, or a union/Optional/protocol target / literal-vs-family mismatch
    (`slot_family`). Per-SLOT rejects are tagged by `_container_lit_elem_ok`."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        return note_detail("container_lit.own")
    return note_detail("container_lit.slot_family")

def _container_record_elem_subscript(e: TpyExpr, locals_: dict[str, TpyType],
                                     analyzer) -> bool:
    """A container subscript `c[i]` / `d[k]` whose element/value is a plain
    F1-record (`_container_record_elem`): `::tpy::__getitem__(c, k)` yields
    `T&` (or the bounds-safe operator[] lvalue) -- a borrow usable as a
    field-access receiver (`ps[i].x` / `ps[i].x = v`, `.` access -- never
    `->`) or a REF_ALIAS borrow-local source (`p = ps[i]` -> `P& p = ...`).
    The container analog of the tuple `_subscript_record_field_recv`.
    Receivers are the shared subscript set (`_subscript_container_recv_type`:
    an in-scope name or a one-level field off an admitted receiver);
    `Optional`-element containers reject at `_f1_record` (the AST wraps those
    reads differently)."""
    return _borrow_elem_subscript_shape(e, locals_, analyzer,
                                        _container_record_elem)


def _container_ref_alias_elem_subscript(e: TpyExpr,
                                        locals_: dict[str, TpyType],
                                        analyzer) -> bool:
    """A container subscript whose element/value is itself a plain list/dict/set
    (`row = matrix[0]`): the element lvalue (`T&`) binds a REF_ALIAS local. The
    nested-container analog of `_container_record_elem_subscript`."""
    return _borrow_elem_subscript_shape(e, locals_, analyzer,
                                        _container_ref_alias_elem)


def _container_wrapper_elem_subscript(e: TpyExpr,
                                      locals_: dict[str, TpyType],
                                      analyzer) -> bool:
    """A container subscript whose element/value is a recursive-union WRAPPER
    (`v: JsonValue = d["rows"]`): the element lvalue binds the wrapper's `T&`
    REF_ALIAS. The wrapper-union analog of
    `_container_ref_alias_elem_subscript`."""
    return _borrow_elem_subscript_shape(
        e, locals_, analyzer,
        lambda t, a: _container_elem_family(
            t, a, lambda m: _eligible_wrapper_union(m, a) is not None))


def _borrow_elem_subscript_shape(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer, elem_family) -> bool:
    """Shared shell for the record-element / nested-container-element subscript
    borrow sources: a plain `c[i]` / `d[k]` (no optional-check, slice, or
    slice-function) off a subscript-container receiver whose element satisfies
    `elem_family`, with a routable index."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None or isinstance(e.index, TpySlice):
        return False
    recv_t = _subscript_container_recv_type(e.obj, locals_, analyzer)
    if recv_t is None and isinstance(e.obj, TpyFieldAccess) \
            and _field_over_container_subscript_ok(e.obj, locals_, analyzer):
        # A CHAINED element receiver (`a.bs[0].as_[0]`): the inner
        # `a.bs[0].as_` is itself a field over an admitted record-element
        # subscript, so each level renders the same `__getitem__` / bare
        # member nest. Recursion is on strictly smaller receivers.
        recv_t = _field_decl_type(e.obj, locals_, analyzer)
    if recv_t is None and isinstance(e.obj, TpySubscript) \
            and _container_ref_alias_elem_subscript(e.obj, locals_, analyzer):
        # A DOUBLE-subscript receiver (`nested[0][0].x`): the inner
        # `nested[0]` is a nested-container element lvalue
        # (`::tpy::__getitem__(nested, 0)` -- `T&`), indexed again -- the
        # same __getitem__ nest at every level.
        rt = analyzer.get_expr_type(e.obj)
        recv_t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
                  if rt is not None else None)
    if recv_t is None or not elem_family(recv_t, analyzer):
        return False
    return (_bigint_index_disposition(e.index, analyzer.get_expr_type(e.obj),
                                      analyzer) != "reject")

def _field_over_container_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                       analyzer) -> bool:
    """A field access off a record-element CONTAINER subscript (`ps[i].field`):
    the receiver `ps[i]` is a plain-record borrow lvalue, so the access renders
    `::tpy::__getitem__(ps, i).field` on both paths (`.` -- `_field_is_arrow`'s
    subscript arm yields `->` only for borrow-`T*` tuple elements). Position-
    neutral like the tuple twin (`_field_over_subscript_ok`): a read (RHS) and
    a scalar-field write target (LHS) render off the same receiver. Markers-
    clean excludes the Optional null-check / property / setattr shapes."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _container_record_elem_subscript(e.obj, locals_, analyzer))

def _tuple_container_elem_over_subscript_ok(e: TpyExpr,
                                            locals_: dict[str, TpyType],
                                            analyzer) -> bool:
    """A tuple-element read over a container-element subscript, as a METHOD
    receiver (`xs[i][j].append(v)` over `list[tuple[.., list[T]]]`): the inner
    `xs[i]` is a container-element borrow lvalue whose element is a TUPLE, and
    the outer const-index read is `std::get<j>(::tpy::__getitem__(xs, i))` --
    a container element (`T&`, `.` access) on both paths, routing the
    container-method arm. Value/record elements keep their own rows; only a
    container element admits here (the shape's method consumer)."""
    if not (isinstance(e, TpySubscript)
            and not e.needs_optional_runtime_check
            and e.slice_function_info is None
            and not isinstance(e.index, TpySlice)):
        return False
    if not _borrow_elem_subscript_shape(
            e.obj, locals_, analyzer,
            # A literal-seeded binding is still PENDING here (the receiver
            # gates read the declared type); resolve before the family check.
            lambda t, a: _container_elem_family(
                resolve_pending_container(t, a) or t, a,
                lambda m: isinstance(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(m))),
                    TupleType))):
        return False
    if _subscript_index_and_tuple(e, analyzer) is None:
        return False
    rt = analyzer.get_expr_type(e)
    rt = resolve_pending_container(rt, analyzer) or rt
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    return bool((is_list(rt) or is_dict(rt) or is_set(rt))
                and _witness("method.recv.tuple_elem_subscript"))


def _subscript_over_container_subscript_ok(e: TpyExpr,
                                           locals_: dict[str, TpyType],
                                           analyzer) -> bool:
    """A subscript whose receiver is itself a container-element subscript
    yielding a container (`m[i][j]`): `m[i]` is a nested-container borrow
    lvalue (`::tpy::__getitem__(m, i)`), indexed again -> the nested
    `::tpy::__getitem__(::tpy::__getitem__(m, i), j)` on both paths. The
    subscript-receiver twin of `_field_over_container_subscript_ok` (whose
    consumer is a field access): the receiver-shape resolver
    (`_subscript_container_recv_type`) deliberately stops at one level, so this
    admits the single nested step the AST renders identically. Plain reads only
    -- optional-check / slice / slice-function receivers stay AST."""
    return (isinstance(e, TpySubscript)
            and not e.needs_optional_runtime_check
            and e.slice_function_info is None
            and not isinstance(e.index, TpySlice)
            and _container_ref_alias_elem_subscript(e.obj, locals_, analyzer))

def _field_over_field_ok(e: TpyExpr, locals_: dict[str, TpyType],
                         analyzer) -> bool:
    """Shallow field-chain receiver shape for one lowering arm."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_markers_clean(e)
            and isinstance(e.obj, TpyFieldAccess)
            and _field_markers_clean(e.obj)):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    return bool(isinstance(ft, NominalType) and _f1_record(ft, analyzer)
                and _witness("field.chain_recv"))

def _field_over_property_call_ok(e: TpyExpr, analyzer) -> bool:
    """A field read off a PROPERTY-GETTER receiver (`s.Config.v` -- the inner
    read is a getter call in disguise, lowered through the method-call arms):
    the field chains postfix `.` off the call render on both paths, so
    admission only needs the getter's return to be a record (native included
    -- the member spelling resolves through `_field_cpp` either way); every
    inner gate still applies when the receiver lowers."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpyFieldAccess)
            and e.obj.property_getter_call is not None):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    return bool(isinstance(rt, NominalType) and rt.is_user_record
                and _witness("field.property_call_recv"))

def _field_over_call_ok(e: TpyExpr, analyzer) -> bool:
    """A value field read off an F1-record-returning call / method-call
    receiver (`f().x`, `p.Box(10).n`, `h.boxed.get().x`): the AST renders
    the bare postfix member over the call render, rvalue and borrow returns
    alike. The receiver lowers through its own call arms (RECEIVER use), so
    every inner gate still applies."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, (TpyCall, TpyMethodCall))):
        return False
    return bool(_f1_record(analyzer.get_expr_type(e.obj), analyzer)
                and _witness("field.call_recv"))

def _field_over_binop_ok(e: TpyExpr, analyzer) -> bool:
    """A value field read off an F1-record-result user-dunder binop
    receiver (`(a // b).v` -> `((a).__floordiv__(b)).v`): the postfix
    member chains off the parenthesized template render on both paths.
    The receiver lowers through the binop's record-dunder arm (RECEIVER
    use), so its operand gates still apply."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpyBinOp)):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    return bool(_f1_record(rt, analyzer) and _witness("field.binop_recv"))

def _field_over_walrus_ok(e: TpyExpr, analyzer) -> bool:
    """A marker-clean field read off a WALRUS receiver (`(q := b).v`): the
    borrow-alias walrus comma form yields an lvalue (`(q = &(b), *q).v`,
    dot access); the walrus arm validates its own target/source classes
    during receiver lowering, so admission needs only the record shape."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpyNamedExpr)):
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    return bool(_f1_record(rt, analyzer) and _witness("field.walrus_recv"))

def _alias_ref_container(t: TpyType | None) -> bool:
    """A container whose borrow local binds a plain `T&` alias -- `list` / `dict`
    / `set`. The `bytes`/`bytearray` (span-borrow) and recursive-union-wrapper
    non-value families take a different borrow shape, so a name alias of those
    stays on the AST path (a later rung)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    # Array included: a demoted list literal keeps list ALIAS semantics
    # (`ys = xs` -> `std::array<...>& ys = xs;`).
    return is_list(t) or is_dict(t) or is_set(t) or is_array(t)


def _bare_nonvalue_name_alias_ok(init: TpyExpr, target_type: TpyType | None,
                                 declared: dict[str, TpyType], prescan: _Prescan,
                                 pointers: 'AbstractSet[str]', analyzer) -> bool:
    """A single-assignment REF_ALIAS whose source is a plain non-value LVALUE
    NAME rendering bare (`T& name = src;` / `const T& ...`): `y = x` (record),
    `alias = items` (container), and alias chains (`c = b`). The source must be
    an in-scope local/param of a plain record / list / dict / set that renders
    bare -- NOT a pointer-local / Optional-ptr name (those alias as `(*p)`) and
    NOT a module global (rendered `T*`, aliased via `(*g)`); both stay on the
    AST path (later rungs). The const verdict mirrors `_is_const_indirect`'s
    name branch (see `_f1_is_const` / `_f1_const_rooted_source`)."""
    if not isinstance(init, TpyName):
        return False
    if init.name in pointers:
        # A PLAIN pointer-local source (a reassigned record local) aliases
        # through the deref the name lowering already renders (`Point&
        # alias = (*p);`). An Optional-declared pointer name carries the
        # deref_check nullability machinery -- unmirrored, stays AST.
        dt = declared.get(init.name)
        dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
              if isinstance(dt, TpyType) else None)
        if isinstance(dt, OptionalType):
            note_detail("decl.name_alias_ptr_src")
            return False
        return bool(_f1_record(target_type, analyzer)
                    and _witness("decl.alias_ptr_deref_src"))
    if init.name not in declared and init.name not in prescan.param_names:
        note_detail("decl.name_alias_global_src")
        return False
    return (_f1_record(target_type, analyzer)
            or _alias_ref_container(target_type))


def _borrow_local_binding(stmt: TpyVarDecl, target_type: TpyType | None,
                          declared: dict[str, TpyType], prescan: _Prescan,
                          analyzer,
                          pointers: 'AbstractSet[str]' = frozenset()
                          ) -> 'LocalBinding | None':
    """The binding for a non-value local var-decl's *first* declaration, or None
    if it is outside the emit slice. The form decision comes from the shared
    classifier; the slice additionally requires a field-access source off an
    F1-record receiver and an F1-record local (REF_ALIAS / POINTER) / inner
    (OPTIONAL_TO_PTR) type. REF_ALIAS and POINTER are the single-assignment and
    reassigned shapes of the same plain-record lvalue lift; POINTER's reseats are
    validated by declaration lowering."""
    binding = classify_local_binding(
        target_type, stmt.init, analyzer, name=stmt.name,
        reassigned=prescan.reassigned, rvalue_reassigned=prescan.rvalue_reassigned,
        hoisted=prescan.hoisted, move_through=prescan.move_through)
    if binding is LocalBinding.OTHER:
        # A ptr-Optional element off a TUPLE-typed field (`first = h.t[0]`)
        # classifies OTHER via the classifier's tuple carve-out (the AST
        # pre-lifts tuple-element reads at the consumer), but the decl IS
        # that consumer: re-tag for the subscript-lift row
        # (`Box* first = ::tpy::optional_to_ptr(std::get<0>(h.t));`).
        if (stmt.name not in prescan.reassigned
                and isinstance(target_type, OptionalType)
                and target_type.uses_pointer_repr()
                and _tuple_field_opt_elem_subscript(stmt.init, declared,
                                                    analyzer)):
            return LocalBinding.OPTIONAL_TO_PTR
        # A pointer-repr Optional TERNARY source classifies OTHER
        # (`reads_storage_form_optional` does not walk ternary arms), but
        # the lowered ifexpr already IS the `T*` the binding binds bare
        # (`Box* t = ((c) ? (p) : (::tpy::optional_to_ptr(h.opt)));`) --
        # re-tag for the ternary row; the arm shapes gate in the ifexpr
        # lowering. Hoisted/move-through names returned OTHER above for a
        # reason the re-tag must not override, so they stay excluded.
        if (isinstance(stmt.init, TpyIfExpr)
                and stmt.name not in prescan.reassigned
                and stmt.name not in prescan.hoisted
                and stmt.name not in prescan.move_through
                and _optional_ptr_borrow(target_type, analyzer) is not None):
            return LocalBinding.OPTIONAL_TO_PTR
        # A SAME-repr pointer-Optional NAME source classifies OTHER (it is
        # not a storage-form optional read -- both sides are already `T*`),
        # but the decl is just the bare pointer copy (`const Point* q = a;`);
        # re-tag it for the name-copy row. POINTER-bound sources only: a
        # STORAGE-form binding of the same type (a storage-opt loop var)
        # needs the optional_to_ptr lift, not the bare copy.
        if (isinstance(stmt.init, TpyName)
                and stmt.init.name in pointers
                and stmt.name not in prescan.reassigned):
            src_t = declared.get(stmt.init.name)
            tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                target_type))) if target_type is not None else None)
            st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(src_t)))
                  if src_t is not None else None)
            if (isinstance(tt, OptionalType) and tt.uses_pointer_repr()
                    and st == tt
                    and _f1_record(_unwrap_own(unwrap_readonly(tt.inner)),
                                   analyzer)):
                return LocalBinding.OPTIONAL_TO_PTR
        return None
    if binding is LocalBinding.OPT_PTR_SLOT:
        # The slot-hoist Optional pointer-local: init/reseat sub-shapes are
        # gated by `_lower_opt_ptr_slot_decl` (the lowering consumes the
        # verdict directly, per the classifier-consumed-by-lowering rule).
        return binding
    if binding is LocalBinding.REBIND_SLOT:
        # F2d: the source is an rvalue F1-record ctor / by-value call (not a field
        # read), so it bypasses the field-receiver check the lvalue bindings need.
        if (_f1_record(target_type, analyzer)
                and _record_rvalue_source_shape(stmt.init, analyzer)):
            return binding
        # ... or an rvalue F1-record METHOD call (`cur = a.clone()` -> the
        # same two-slot machinery, `Rc<Node>* cur = &__slot_1;`): the
        # method-call lowering's own gates validate callee/args, the shared
        # `_owned_record_decl_ok` disjunct.
        if (_f1_record(target_type, analyzer)
                and _method_rvalue_f1_record(stmt.init, analyzer)):
            return binding
        # A rebound container-literal local rides the same two-slot machinery
        # (`std::vector<T>* xs = &__slot_1; ... xs = &*(__slot_2 = {...});`);
        # the literal itself lowers through the shared container-literal arm
        # (its per-element gates reject there).
        if (isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral))
                and target_type is not None
                and _container_literal_shape_ok(stmt.init, target_type,
                                                analyzer, note=True)):
            return binding
        return None
    if isinstance(stmt.init, (TpyCall, TpyMethodCall)):
        # A borrow-record-returning free call (`p = shared(x)` -> `Pair& p =
        # shared(x);` -- the classifier's lvalue-source verdict). REF_ALIAS
        # only: the reassigned POINTER shape reseats via `&(call)`, a lift
        # the slice does not carry (tagged). Const rides `_f1_is_const`'s
        # raw-sema check (a `readonly[T]` return arrives ReadonlyType-
        # wrapped) plus the readonly-method ref-return branch, mirroring
        # `_is_const_indirect`. A borrow-returning METHOD call binds the same
        # alias (`num = h.get_item()` -> `MyNumber& num = h.get_item();`,
        # a substituted T-return); a container-returning one binds the
        # container alias (`std::vector<T>& items = c.get_item();`). Other
        # call bindings (an OPTIONAL_TO_PTR optional return) fall through
        # untagged so the generic probe's family drilldown names them.
        if binding is LocalBinding.REF_ALIAS and (
                _f1_record(target_type, analyzer)
                # A borrow-returning genrec method call binds the same alias
                # (`g = h.get()` -> `Tree<int32_t>& g = h.get();`) -- the
                # wrapper struct is one C++ value type, the reference binds
                # like any record's.
                or isinstance(unwrap_readonly(unwrap_send_sync(target_type)),
                              RecursiveAliasInstanceType)
                or (isinstance(stmt.init, TpyMethodCall)
                    and _alias_ref_container(target_type))):
            return binding
        if binding is LocalBinding.POINTER and _f1_record(target_type,
                                                          analyzer):
            note_detail("decl.record_call_reassigned")
        return None
    # An Optional-ptr borrow-name receiver (`g = h.g` off a narrowed
    # `H | None` param / OPTIONAL_TO_PTR local) is a plain field lift off
    # the pointer (`h->g`); its const verdict is computable exactly --
    # seed_param_locals seeds const_indirect_locals from the readonly
    # annotation or the DEEP-const verdict, both mirrored in
    # `_f1_is_const` / `_f1_const_rooted_source`. The sibling
    # const-spelling sinks still pin on `_const_exact_field_receiver_ok`
    # pending their own const wiring.
    recv_opt_ptr = (isinstance(stmt.init, TpyFieldAccess)
                    and _field_receiver_ok(stmt.init, declared, analyzer)
                    and _optional_ptr_borrow_name(stmt.init.obj, declared,
                                                  analyzer) is not None)
    # A base-qualified field source (`nums = A.buf` -> `this->A::buf`) has no
    # value-typed receiver at all, so it rides its own admission beside the
    # receiver-shape gate (exactly like the read arm); const follows the raw
    # sema type (`readonly[...]` in an @readonly method) via `_f1_is_const`.
    if not (_const_exact_field_receiver_ok(stmt.init, declared, analyzer)
            or recv_opt_ptr
            or _unbound_self_field_ok(stmt.init)):
        # An OPTIONAL_TO_PTR lift whose field source hangs off an admitted
        # method-call receiver (`parent_ref = child.get().parent` ->
        # `Weak<Node>* parent_ref = ::tpy::optional_to_ptr(
        # child.get().parent);`): the inner call renders bare exactly like
        # the field-read arm, and the lift wraps it -- the indirect-receiver
        # twin of `recv_opt_ptr` above.
        if (binding is LocalBinding.OPTIONAL_TO_PTR
                and isinstance(stmt.init, TpyFieldAccess)
                and isinstance(stmt.init.obj, TpyMethodCall)
                and _indirect_field_receiver_ok(stmt.init, declared,
                                                analyzer)):
            return binding
        # An OPTIONAL_TO_PTR lift whose source is a @property read
        # (`n = w.node` -> `Node* n = ::tpy::optional_to_ptr(w.node());`):
        # the getter call renders bare (the field arm delegates to the
        # method-call lowering, whose own gates re-validate receiver/args)
        # and the lift wraps it -- the property twin of the row above.
        if (binding is LocalBinding.OPTIONAL_TO_PTR
                and isinstance(stmt.init, TpyFieldAccess)
                and stmt.init.property_getter_call is not None):
            return binding
        # A STORAGE-form Optional CONTAINER-subscript source (`a = d["a"]`
        # on `dict[str, P | None]`): the same optional_to_ptr lift over the
        # bare `__getitem__` read (`P* a = ::tpy::optional_to_ptr(
        # ::tpy::__getitem__(d, "a"));`) -- the subscript twin of the
        # field-source lift.
        if (binding is LocalBinding.OPTIONAL_TO_PTR
                and isinstance(stmt.init, TpySubscript)
                and reads_storage_form_optional(analyzer, stmt.init)):
            return binding
        # A record-element container subscript source (`p = ps[i]`) binds the
        # single-assignment `T&` alias (`P& p = ::tpy::__getitem__(ps, i);`) or,
        # reassigned, the reseatable `P* p = &(::tpy::__getitem__(ps, i));` --
        # the same lift the `reseat.subscript_elem` arm applies to every later
        # `p = ps[j]`. Optional sources keep the field-receiver pin. The const
        # verdict mirrors the AST's element-borrow propagation (`_f1_is_const`).
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and _f1_record(target_type, analyzer)
                and _container_record_elem_subscript(stmt.init, declared,
                                                     analyzer)):
            return binding
        # A borrow-returning user-record `__getitem__` subscript (`r = e[k]`)
        # binds the single-assignment `T&` alias of the bare operator[]
        # lvalue. REF_ALIAS only: the reassigned POINTER sibling needs the
        # `&(e[k])` reseat lift, unwitnessed.
        if (binding is LocalBinding.REF_ALIAS
                and _f1_record(target_type, analyzer)
                and _record_getitem_borrow_subscript(stmt.init, declared,
                                                     analyzer, pointers)):
            return binding
        # A pointer-repr record element off a borrow-form tuple PARAM
        # (`b = p[1]`) binds the `T&` alias of the element referent (the
        # deref-flagged std::get render). REF_ALIAS only -- a reassigned
        # sibling's reseat is unwitnessed.
        if (binding is LocalBinding.REF_ALIAS
                and _f1_record(target_type, analyzer)
                and _borrow_tuple_param_elem_subscript(stmt.init, prescan,
                                                       analyzer)):
            return binding
        # A nested-container element subscript (`row = matrix[0]`) binds the
        # `T&` alias of the element list/dict/set.
        if (binding is LocalBinding.REF_ALIAS
                and _alias_ref_container(target_type)
                and _container_ref_alias_elem_subscript(stmt.init, declared,
                                                        analyzer)):
            return binding
        # An open-T element subscript inside a generic body (`v =
        # self.items[self.pos]`) binds the per-instantiation `T&`, or the
        # reseatable `T*` when reassigned (`result = a[i]`) -- the type-param
        # twin of the record-element rows above.
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and _is_type_param_slot(target_type)
                and _borrow_elem_subscript_shape(stmt.init, declared, analyzer,
                                                 _container_tparam_elem)):
            return binding
        # A wrapper-union element subscript (`v: JsonValue = d["rows"]`)
        # binds the element lvalue as the wrapper's `T&` alias. LOCAL
        # narrowing-alias receivers only: a PARAM subject's extraction alias
        # is `const auto&`, and the AST still emits the non-const `T&` bind
        # off it -- uncompilable C++ (pre-existing AST bug, BUGS.md); the
        # shape stays gate-rejected rather than mirroring it.
        if (binding is LocalBinding.REF_ALIAS
                and _eligible_wrapper_union(target_type, analyzer) is not None
                and isinstance(stmt.init, TpySubscript)
                and isinstance(stmt.init.obj, TpyName)
                and stmt.init.obj.name not in prescan.param_names
                and _container_wrapper_elem_subscript(stmt.init, declared,
                                                      analyzer)):
            return binding
        # A bare non-value NAME alias (`y = x`, `alias = items`, `c = b`) binds
        # the single-assignment `T&` alias directly -- no field-receiver pin.
        if (binding is LocalBinding.REF_ALIAS
                and _bare_nonvalue_name_alias_ok(stmt.init, target_type,
                                                 declared, prescan, pointers,
                                                 analyzer)):
            return binding
        # A container / F1-record and/or select or ternary (`x = a or b`,
        # `x = a if c else b`): the classifier's lvalue verdict means the
        # select is itself an lvalue (an rvalue RHS rides the lazily
        # emplaced `__logical_slot` pointer-select, still an lvalue), so
        # it binds as the single-assignment `T&` alias. Operand shapes
        # gate inside the select / ternary lowering.
        if (binding is LocalBinding.REF_ALIAS
                and (_alias_ref_container(target_type)
                     or _f1_record(target_type, analyzer))
                and ((isinstance(stmt.init, TpyBinOp)
                      and stmt.init.op in ("&&", "||"))
                     or isinstance(stmt.init, TpyIfExpr))):
            return binding
        # The reassigned POINTER sibling of the bare-name alias: a plain record
        # NAME source lifts to a reseatable `[const] T* x = &(a);` (later
        # `x = &(b);`). Record only -- a reassigned container alias is a
        # later rung. The `&()` lift needs a bare-rendering lvalue, so the
        # source must not itself be a pointer-local / global (aliased `(*p)`).
        if (binding is LocalBinding.POINTER
                and isinstance(stmt.init, TpyName)
                and stmt.init.name not in pointers
                and (stmt.init.name in declared
                     or stmt.init.name in prescan.param_names)
                and _f1_record(target_type, analyzer)):
            return binding
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        # The FIELD-source aliases: an F1-record field (`r = self.inner` ->
        # `Inner& r = this->inner;`, REF_ALIAS or the reseatable POINTER)
        # and, REF_ALIAS-only, a container field (`xs = self.tags` ->
        # `std::vector<T>& xs = this->tags;`) -- the reassigned container
        # sibling is a later rung.
        if _f1_record(target_type, analyzer):
            return binding
        if (binding is LocalBinding.REF_ALIAS
                and _alias_ref_container(target_type)):
            return binding
        return None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner record.
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    return binding if _f1_record(inner, analyzer) else None

def _record_rvalue_source_shape(init: TpyExpr, analyzer) -> bool:
    """Classify an rvalue call producing an F1 record.

    This is intentionally shallow. The consuming expression lowering arm
    validates and lowers each argument with the actual temp/narrowing context.
    """
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    fi = init.resolved_function_info
    if fi is None:
        return False
    # The ctor face shares `_ctor_shape_ok`'s same-name free-fn collision,
    # generic / native / multi-overload / special-form `__init__` checks. Its
    # AST emit must be `Name(args)` or qualified `::ns::Name(args)`. It
    # owns the arity verdict too (`_ctor_arity_ok`, shared by the raw-name
    # and instantiation forms: omitted trailing defaults ride the C++ ctor
    # signature). The by-value record-returning free-call face shares
    # free-call lowering's callee-shape head (linkage, literal-overload mangling,
    # generics, error_return -- shapes whose AST emit is not the bare
    # `name(args)`) and its arity rule (`_call_arity_ok`: omitted trailing
    # defaults ride the emitted C++ signature on both paths).
    if fi.is_constructor:
        return (_ctor_shape_ok(init, analyzer)
                or _ctor_instantiation_ok(init, analyzer))
    # The by-value record-returning FREE-call face: the same callee-shape head
    # as free-call lowering (linkage / literal-overload / generics / error_return
    # via `_plain_free_callee_ok`) + exact arity, then the SHARED plain-call arg
    # cascade -- so `return make_rec(s, xs, r)` routes the str / container /
    # record / Own-move / optional-ptr / union arg shapes a free call already
    # carries, not the reduced scalar-only subset. Guard: a str-literal into a
    # multi-overload callee pins to the view form (`string_view("...")`), which
    # the bare emit does not reproduce -- mirror free-call lowering's pin.
    if not _record_rvalue_call_shape(init, analyzer):
        return False
    return True

def _method_recv_field_write_ok(target: TpyExpr, declared: dict[str, TpyType],
                                analyzer) -> bool:
    """A field-write target whose RECEIVER is a method call returning a
    mutable reference to an F1 record (`r.get().x = v` over an
    Rc/Arc/Box `.get()` -> `T&`). The receiver lowers through the ordinary
    method-call expr arm -- the READ `r.get().x` already routes; only the
    write target needed the non-Name receiver arm. Markers stay clean and the
    emitted access is `.field` (a `T&` receiver, not `->`)."""
    if not isinstance(target, TpyFieldAccess) or not _field_markers_clean(target):
        return False
    recv = target.obj
    if not isinstance(recv, TpyMethodCall):
        return False
    return _f1_record(analyzer.get_expr_type(recv), analyzer)


def _scalar_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer,
                           pointers: 'AbstractSet[str]' = frozenset()) -> bool:
    """A scalar-field write `recv.field = <scalar>`: a value-scalar field off an
    F1-record receiver (`_field_receiver_ok` also rejects the property-setter /
    __setattr__ write target), written with an eligible scalar expression. The
    scalar sibling of `_f2b_optional_field_write_ok` -- it emits as the AST's
    default field-assign path (`recv.field = <value>;`, no borrow<->storage lift). The
    target is a plain field off an F1-record receiver, or a record-element tuple
    subscript (`t[N].field = <scalar>` -> `std::get<N>(t)->field = ...`, the write analog
    of the record-element read); an Optional-element target stays on the AST path (its
    markers reject it). A Char field writes identically (`recv.c = z`); its
    str-literal value guard is defensive -- sema type-errors a literal into a
    Char field, but the target-typed `'x'` render would otherwise diverge.

    An Optional-ptr-receiver target is admitted on both faces: proven ->
    `p->field = <value>;` (arrow via _field_receiver_ok), unproven ->
    `::tpy::deref_check(p).field = <value>;` (_optional_checked_field)."""
    target = stmt.target
    if not (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
            or _ptr_value_field_recv_ok(target, declared, analyzer)
            or _optional_checked_field(target, declared, analyzer)
            # The STORAGE sibling (`h.opt.x = 5` ->
            # `::tpy::deref_optional_check(h.opt).x = 5;`): the AST wraps the
            # whole optional lvalue, and that render is position-independent
            # exactly like the already-`T*` name receiver beside it -- the
            # predicate was scoped to reads only by choice, not by render.
            or _optional_checked_field_over_field_ok(target, declared,
                                                     analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)
            or _field_over_container_subscript_ok(target, declared, analyzer)
            or _field_over_record_getitem_ok(target, declared, analyzer,
                                             pointers)
            # `a.inner.count = v` -- the receiver is itself a field read. The
            # aug-assign twin has always admitted it; the plain-assign half
            # never got the row, and both render the same target string.
            or _field_over_field_ok(target, declared, analyzer)
            or _method_recv_field_write_ok(target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        if isinstance(stmt.value, TpyStrLiteral):
            return False
    elif not (_eligible_scalar(ftype)
              or _eligible_enum(ftype, analyzer) is not None
              or _is_type_param_slot(ftype)
              or _eligible_ptr_value(ftype, analyzer)
              # A VALUE-repr `Optional[scalar]` field (`std::optional<T>`):
              # the scalar converts implicitly, so the store is bare like a
              # plain scalar's. Deliberately not the pointer-repr sibling --
              # that one needs the `ptr_to_optional` lift.
              or (_value_opt_scalar(ftype, analyzer) is not None
                  and not isinstance(stmt.value, TpyNoneLiteral))
              # The owned-str twin (`s.label = "hello"` at a `str | None`
              # field): same bare store into `std::optional<std::string>`.
              # LITERAL values only -- a view-form source would raise the
              # owned-vs-view conversion question the scalar row never has.
              or (_value_opt_owned_str(ftype, analyzer)
                  and isinstance(_peel_coerce(stmt.value), TpyStrLiteral))):
        # A generic record's `T` field write emits as a plain assign (`field = v`
        # / `field = std::move(v)`) -- the BORROW->STORAGE convert renders the
        # source bare/moved (its TypeParamRef emit arm), byte-identical to the
        # AST's `val_or_ref_t<T>` / `own_param_t<T>` copy/move per instantiation.
        return False
    return True

def _user_deref_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                               narrowed: 'AbstractSet[str]', analyzer,
                               pointers: 'AbstractSet[str]') -> bool:
    """A scalar-field write auto-dereffed through a USER Deref wrapper
    (`r.x = <scalar>` -> `r.__deref__().x = <scalar>`): the user-Deref sibling
    of `_scalar_field_write_ok`. Value set matches (scalar / Char / enum / Ptr
    value); the target render is `_user_deref_field_recv_ok`'s chain."""
    target = stmt.target
    if not _user_deref_field_recv_ok(target, declared, narrowed, analyzer,
                                     pointers):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        return not isinstance(stmt.value, TpyStrLiteral)
    return (_eligible_scalar(ftype)
            or _eligible_enum(ftype, analyzer) is not None
            or _eligible_ptr_value(ftype, analyzer))

def _ptr_union_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A union-field write `recv.field = <source>` (F4 U2): a value-variant
    field off an F1-record receiver written from a borrow-form pointer-variant
    name of the same union (lowers to the borrow->storage `THIRFormConvert`,
    `::tpy::to_value_variant<...>` -- the union sibling of the F2b Optional
    write), a `None` literal (a monostate store, `recv.field =
    std::monostate{};`), or a same-union field lvalue (a storage-to-storage
    copy: a field source is not a ptr-variant source on the AST path, so it
    assigns bare with no lift), or a member-typed CTOR rvalue (`h.pet =
    Cat(9)` -- the variant assignment absorbs the member, so the AST
    assigns the bare ctor with no lift; exact member type only)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    u = _eligible_ptr_union(analyzer.get_expr_type(target), analyzer)
    if u is None:
        return False
    if isinstance(stmt.value, TpyNoneLiteral):
        return True
    if _union_member_ctor_rvalue(stmt.value, u, analyzer):
        return True
    return _ptr_union_source_ok(stmt.value, declared, analyzer, u,
                                allow_field=True)


def _union_member_ctor_rvalue(value: TpyExpr, u: UnionType, analyzer) -> bool:
    """A constructor-call rvalue whose type is EXACTLY a member of `u` --
    the bare variant-absorbing store (`field = Cat(9);`)."""
    if not isinstance(value, TpyCall):
        return False
    fi = value.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    vt = analyzer.get_expr_type(value)
    if vt is None:
        return False
    vt = unwrap_readonly(vt)
    return any(m == vt for m in u.members)

def _nondef_ctor_field(ftype: 'TpyType | None', analyzer) -> bool:
    """The field's record type has a suppressed default ctor (`@nocopy` with
    `__del__`): a DEMOTED init of such a field makes the AST's
    `_reject_nondef_ctor_field_in_body` raise a CodeGenError, so THIR must
    keep the whole ctor on the AST path (routing would silently emit the
    uncompilable default-init instead of the diagnostic). Keyed on the field
    TYPE only -- an inherited field of such a type over-rejects (the AST
    skips non-own fields), which is safe."""
    rec = analyzer.registry.get_record_for_type(ftype)
    return rec is not None and del_suppresses_default_ctor(rec)

def _record_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], analyzer,
        pointers: set[str], narrowed: AbstractSet[str],
        prescan: '_Prescan') -> bool:
    """A plain F1-record field write `recv.field = <source>` off an F1-record
    receiver -- the AST's default field assign, no borrow<->storage lift.
    Source rows (beyond the two below: a borrow-returning call, a field
    read, a record-element subscript, and a pointer-local -- each copies
    bare, the last through its deref):

      * a **record rvalue** (the `_record_rvalue_source_shape` -- a ctor /
        by-value record-returning call): a direct copy
        `recv.field = Inner(args);`. The exact source-type == field-type check
        keeps a subclass rvalue (a slicing copy) out.
      * a **record NAME** (a declared borrow param / owned local of a record
        type, incl. `Own[T]` params): the bare copy `recv.field = p;` (plus
        sema's implicit-copy warning, path-independent), or `std::move(p)` at
        a movable name's last use (`_maybe_move`) -- the plain-record STORAGE
        convert arm. Narrowed names (`(*o)` deref renders), pointer-locals
        (`(*p)`), `self`, and coerce-wrapped sources stay on the AST path.

    In a constructor body this gate sees only DEMOTED inits (the MIL hoist
    already ran in `lower_constructor`), which the AST emits via the same
    default assign -- routed, except a field type with a suppressed default
    ctor (see `_nondef_ctor_field`: the AST raises there). An unbound-self
    `BaseN.field` target joins both source rows unchanged."""
    target = stmt.target
    if not _field_receiver_or_unbound_self_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not _f1_record(ftype, analyzer):
        return False
    if prescan.is_constructor and _nondef_ctor_field(ftype, analyzer):
        return False
    # `copy(T(...))` peels to its constructor (identical render); `copy(name)`
    # is the copy-CONSTRUCT rvalue `T(name)`. Both land at the field bare.
    ctor_peel = copy_ctor_rvalue_source(stmt.value, analyzer)
    if ctor_peel is not None:
        return (_record_rvalue_source_shape(ctor_peel, analyzer)
                and analyzer.get_expr_type(ctor_peel) == ftype)
    crec = copy_plain_record_source(stmt.value, analyzer, pointers)
    if crec is not None:
        return crec == ftype
    if _record_rvalue_source_shape(stmt.value, analyzer):
        return analyzer.get_expr_type(stmt.value) == ftype
    v = stmt.value
    # A BORROW-returning record call source copies bare on assignment
    # (`h.p = identity(pt);` -- the C++ copy-assign absorbs the `T&`).
    if (isinstance(v, TpyCall) and v.resolved_function_info is not None
            and call_returns_cpp_ref(analyzer, v.resolved_function_info)
            and analyzer.get_expr_type(v) == ftype
            and _f1_record(ftype, analyzer)):
        return _witness("field_write.borrow_call_copy")
    # A FIELD or record-element SUBSCRIPT source copies bare
    # (`h.p = h2.p;` / `h.p = ::tpy::__getitem__(pts, 0);` -- the reads
    # are references, the assign copies; never movable).
    if isinstance(v, TpyFieldAccess):
        return (_field_receiver_ok(v, declared, analyzer)
                and _f1_record(analyzer.get_expr_type(v), analyzer)
                and analyzer.get_expr_type(v) == ftype
                and _witness("field_write.field_copy"))
    if isinstance(v, TpySubscript):
        return (_container_record_elem_subscript(v, declared, analyzer)
                and analyzer.get_expr_type(v) == ftype
                and _witness("field_write.subscript_copy"))
    # A POINTER-local record source copies through the deref
    # (`this->result = (*saved);` -- gen_expr_deref's indirect-name read;
    # pointers are never movable, so no move wrap).
    if (isinstance(v, TpyName) and v.name in pointers
            and v.name in declared and v.name not in narrowed
            and not (prescan.has_self and v.name == "self")):
        vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[v.name])))
        return (_f1_record(vt, analyzer)
                and vt == unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ftype)))
                and _witness("field_write.ptr_local_copy"))
    if not (isinstance(v, TpyName) and v.name in declared
            and v.name not in narrowed and v.name not in pointers
            and not (prescan.has_self and v.name == "self")):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
    return _f1_record(vt, analyzer)

def _optional_record_field_inner(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The inner record type of a pointer-repr `Optional[F1-record]` field slot
    (stored `std::optional<inner>`, inner a non-value record) -- or None. Shared
    by the value-storage optional field-write gate and its lowering. The
    `uses_pointer_repr` guard keeps a VALUE-record inner out: its
    borrow->storage convert has no plain-non-value emit arm."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if (isinstance(u, OptionalType) and u.uses_pointer_repr()
            and _f1_record(u.inner, analyzer)):
        return u.inner
    return None

def _optional_value_record_field_inner(t: 'TpyType | None',
                                       analyzer) -> 'TpyType | None':
    """The field-write sibling of `_optional_record_field_inner` for a
    VALUE-record inner (`o: V | None` on a `ValueType` V, stored
    `std::optional<V>`), or None.

    The pointer-repr predicate excludes this inner on the grounds that its
    borrow->storage convert has no plain-non-value emit arm. For a VALUE
    record there IS no such convert: borrow and storage forms coincide, so
    the AST writes it bare (`this->o = v;`) and the optrec name arm applies
    no FormConvert either (the source lowers VALUE, not BORROW). Kept
    separate from the pointer-repr predicate because the SETITEM widened
    family also consumes that one and has its own convert arms.
    """
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if (isinstance(u, OptionalType) and not u.uses_pointer_repr()
            and _f1_record(u.inner, analyzer)):
        return u.inner
    return None


def _optional_record_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        analyzer, narrowed: AbstractSet[str], prescan: '_Prescan') -> bool:
    """A value-storage `Optional[record]` field write `recv.opt = <record>` off
    an F1-record receiver: the field stores `std::optional<inner>`, and the
    source is a record RVALUE (ctor / by-value call of the inner type -- copied
    bare, exact-type to keep a subclass slice out), an OWNED-record METHOD-call
    rvalue of the inner type (`a.get().next = b.clone()` -- the Own return
    lands bare, optional::operator= absorbs the move), or a record NAME (a
    record param / owned local, incl. `Own[T]` params) copied bare (`opt = p;`,
    optional::operator= absorbs the inner lvalue) or moved at a movable name's
    last use (`opt = std::move(p);`). The receiver is an admitted field-write
    receiver -- a NAME (`_field_receiver_ok`) or a mutable-ref-returning
    method call (`a.get().next`, `_method_recv_field_write_ok`). The
    record-field-write shape at an Optional
    slot; the F2b `T*`->ptr_to_optional lift (a pointer-local source) and the
    `None` store stay their own arms. Narrowed / pointer-local / `self` sources
    take other AST emit paths and stay on the AST path."""
    target = stmt.target
    if not (_field_receiver_ok(target, declared, analyzer)
            or _method_recv_field_write_ok(target, declared, analyzer)):
        return False
    tgt_t = analyzer.get_expr_type(target)
    inner = (_optional_record_field_inner(tgt_t, analyzer)
             or _optional_value_record_field_inner(tgt_t, analyzer))
    if inner is None:
        return False
    v = stmt.value
    # The `copy()` rows, exactly as at the plain-record slot: a constructor
    # argument peels (identical render), a record NAME copy-constructs
    # `T(name)`. `optional::operator=` absorbs either inner rvalue.
    ctor_peel = copy_ctor_rvalue_source(v, analyzer)
    if ctor_peel is not None:
        return (_record_rvalue_source_shape(ctor_peel, analyzer)
                and analyzer.get_expr_type(ctor_peel) == inner)
    crec = copy_plain_record_source(v, analyzer, pointers)
    if crec is not None:
        return crec == inner
    if _record_rvalue_source_shape(v, analyzer):
        return analyzer.get_expr_type(v) == inner
    if (isinstance(v, TpyMethodCall)
            and is_rvalue_source(analyzer, v)):
        vt = analyzer.get_expr_type(v)
        vt = (_unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            vt)))) if isinstance(vt, TpyType) else None)
        return vt == inner and _f1_record(vt, analyzer)
    if not (isinstance(v, TpyName) and v.name in declared
            and v.name not in pointers and v.name not in narrowed
            and not (prescan.has_self and v.name == "self")):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
    return _f1_record(vt, analyzer)

def _covariant_record_upcast_ok(vt: 'TpyType | None', target: 'TpyType | None',
                                analyzer) -> bool:
    """`vt -> target` is a covariant-generic record upcast (`Box[Impl] ->
    Box[Proto]`) per sema's `is_covariant_generic_upcast` -- the single
    covariance-vs-slice authority ("a representation-preserving converting
    move, NOT slicing"). The one THIR entry point into that verdict, shared
    by the optional-field write gate and the record-element setitem arm so
    the two admission sites cannot drift."""
    return (isinstance(vt, NominalType) and isinstance(target, NominalType)
            and analyzer.compat.is_covariant_generic_upcast(vt, target))


def _optional_record_field_upcast_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], analyzer) -> bool:
    """A covariant-generic record RVALUE written into a pointer-repr
    `Optional[generic]` field (`recv.opt = Box(conn)` at a `Box[Proto] | None`
    slot): the AST's default field assign renders the bare plain assign -- the
    source spells its OWN inferred type (`Box<HTTPConnection>(...)`) and
    `optional::operator=` absorbs the converting move -- so the field's inner
    type is never rendered, and its F1-ness does not gate the write. At emit
    the inner qualifies F1 (generation context), so the admitted write rides
    the optrec rvalue arm; corpus witness: the flipped tplib/requests_* cases'
    `s._connection = Box(conn)`. Name sources stay out: their move/copy
    renders ride the exact-type arm's rules."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(target))))
    if not (isinstance(ft, OptionalType) and ft.uses_pointer_repr()
            and isinstance(ft.inner, NominalType)):
        return False
    v = stmt.value
    if not _record_rvalue_source_shape(v, analyzer):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(v))))
    return _covariant_record_upcast_ok(vt, ft.inner, analyzer)

def _container_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A container-literal field write `recv.field = [...] / {...}` off an
    F1-record receiver: the AST's default field assign renders
    `gen_expr_deref(value, field_type)` -- the same target-threaded literal
    render a decl init gets (`{e1, e2}` consumed by the vector lvalue, the
    spelled empty list, the `::tpy::ordered_map<K, V>(...)` /
    `ordered_set<T>(...)` constructor forms) with no move wrap (a literal is
    never a movable name). Element admission is the shared
    container-literal slice."""
    if not isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral)):
        return False
    if not _field_receiver_or_unbound_self_ok(stmt.target, declared,
                                              analyzer):
        return False
    ftype = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(stmt.target))))
    return _container_literal_shape_ok(stmt.value, ftype, analyzer)

def _container_copy_field_write_ok(stmt: TpyAssign,
                                   declared: dict[str, TpyType],
                                   pointers: 'AbstractSet[str]',
                                   analyzer) -> bool:
    """`recv.field = copy(xs)` at a container field -- the copy-construct
    rvalue (`this->items = std::vector<int32_t>(data);`), the container
    sibling of the record `copy()` field write. `_gen_copy_expr`'s tail
    spells `{type}({arg})` for either."""
    if not _field_receiver_or_unbound_self_ok(stmt.target, declared, analyzer):
        return False
    ftype = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(stmt.target))))
    if not (is_list(ftype) or is_dict(ftype) or is_set(ftype)):
        return False
    return copy_plain_container_source(stmt.value, analyzer,
                                       pointers) is not None


def _str_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                        analyzer) -> bool:
    """A str-family field write `recv.field = <str literal | str name>` off an
    F1-record receiver: the AST's default field assign renders the value BARE
    (`recv.field = s;` / `= "lit";`) -- `std::string::operator=(string_view)`
    absorbs a view source into an owned field, so unlike a decl init there is
    NO view->owned `std::string(...)` construction, and str names are never in
    codegen's movable set (value-typed decl arms don't register), so no move
    wrap either. A str-typed BINOP value (`self.buf = self.buf + s`) also
    assigns its concat render bare, and a same-family COERCE wrap peels
    transparently before the rows (so a coerce-wrapped literal / name /
    binop admits like its bare form -- the wrap does not change the
    assign render). Call sources stay on the AST path; `String`-typed
    fields/sources keep their own emit shapes (excluded by
    `_resolved_str_value`)."""
    if not _field_receiver_or_unbound_self_ok(stmt.target, declared,
                                              analyzer):
        return False
    if _resolved_str_value(analyzer.get_expr_type(stmt.target),
                           analyzer) is None:
        return False
    v = stmt.value
    # A str-typed BINOP value assigns its concat render bare; the str-ness
    # reads off the OUTER (possibly coerce-wrapped) type -- the raw binop
    # node can carry an unresolved in-place type.
    outer_str = _resolved_str_value(analyzer.get_expr_type(v), analyzer)
    if isinstance(v, TpyCoerce):
        v = v.expr
    if isinstance(v, TpyStrLiteral):
        return True
    if isinstance(v, TpyBinOp) and outer_str is not None:
        return True
    return (isinstance(v, TpyName) and v.name in declared
            and _resolved_str_value(declared[v.name], analyzer) is not None)

def _bytes_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """The bytes twin of `_str_field_write_ok`: an owned `bytes` field write
    `recv.field = <bytes literal | bytes name>` off an F1-record receiver.
    Unlike str, `std::vector<uint8_t>` has no span ctor, so a view (span)
    source copies via the S6 `::tpy::bytes_copy(...)` STORAGE convert (the
    lowering picks it off the source form) -- an owned source (bytes literal /
    owned local) lands bare. `bytearray` (a reference type) is excluded by
    `is_bytes_type`."""
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ft = _resolved_bytes_value(analyzer.get_expr_type(stmt.target), analyzer)
    if ft is None or not is_bytes_type(ft):
        return False
    v = stmt.value
    if isinstance(v, TpyBytesLiteral):
        return True
    return (isinstance(v, TpyName) and v.name in declared
            and _resolved_bytes_value(declared[v.name], analyzer) is not None)

def _class_const_write_target_ok(target, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """A class-constant / classvar write lvalue (`C.X = v`, `obj.X = v`):
    the AST's gen_class_constant_lvalue arm -- the bare qualified
    `<owner>::<member>` with the receiver eval split into a leading
    statement. Scalar constants only (the sole slot family whose value
    render is the plain target-typed assign; str/tuple constants keep
    their own AST value shapes). Receiver shapes: pure (no eval),
    effectful (the receiver lowers through its own arms -- rejects there
    if unrouted), or an unproven-Optional DECLARED pointer name (the
    `deref_check(c)` render; pointer_value_expr's global `(*name)` wrap
    and the field-receiver deref_optional_check shape stay out)."""
    if not (isinstance(target, TpyFieldAccess)
            and target.class_constant_owner is not None):
        return False
    if not _eligible_scalar(analyzer.get_expr_type(target)):
        return False
    if target.needs_optional_runtime_check:
        # `pointers` is lc.pointers (NOT admission_pointers, which excludes
        # the Optional-ptr borrows this arm exists for): the receiver must
        # render as the bare `T*` name deref_check takes.
        return (isinstance(target.obj, TpyName)
                and target.obj.name in declared
                and target.obj.name in pointers)
    if isinstance(target.obj, TpySubscript):
        # An effectful subscript receiver is lowered prechecked (like the
        # field-read twin), so admission owns the receiver shape here.
        return _container_record_elem_subscript(target.obj, declared,
                                                analyzer)
    return True

def _class_const_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                               pointers: set[str], analyzer) -> bool:
    """Aug-assign on a class-constant lvalue: the AST substitutes the bare
    qualified name into `target = (target OP value)` (receiver eval split
    off, emitted at most once) -- the same binop-template admission as
    `_scalar_aug_assign_ok`, target shapes from
    `_class_const_write_target_ok` (scalar-only, so the str `+=` in-place
    branch is excluded for free)."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    return _class_const_write_target_ok(stmt.target, declared, pointers,
                                        analyzer)

def _scalar_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A scalar augmented assignment `x += y` / `recv.field += y` that is
    byte-identical to `target = (target OP value)` -- the plain
    `_gen_binop_from_result` branch of `_gen_aug_assign_code`, with every
    preprocessing branch of the AST path gated out:

      - an in-place dunder (`resolved_inplace`) mutates the target via a method
        call, not the binop substitution;
      - a missing/non-template `resolved_binop` emits a bare C++ `op=` fallback;
      - `str +=` takes the in-place-append optimization (excluded for free: a
        str target is not an eligible scalar);
      - a class-constant / narrowed-optional target is not a plain eligible-scalar
        lvalue (a record-element tuple-subscript target IS admitted, via
        `_field_over_subscript_ok` -- the target renders identically on both sides
        of the synthetic `target = (target OP value)`).

    The target is a declared scalar local, an F1-record scalar field, or a
    record-element tuple subscript (`t[N].field`); the value
    is an eligible scalar expression. Lowering synthesizes the binop with
    `divisor_non_zero=False` -- the AST aug-assign path never swaps
    `div_check`->`div_floor` (no `TpyBinOp` node carries the flag)."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    target = stmt.target
    if isinstance(target, TpyName):
        if target.name not in declared:
            return False
    elif not (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
              or _optional_checked_field(target, declared, analyzer)
              or _field_over_subscript_ok(target, declared, analyzer)
              or _field_over_container_subscript_ok(target, declared, analyzer)
              or _field_over_field_ok(target, declared, analyzer)
              # A scalar field through a BORROW-returning call receiver
              # (`o.b.get().v += 1`): the receiver renders identically on
              # both sides of the synthetic `target = (target OP value)`;
              # sema only admits writable (borrow) receivers here.
              or _field_over_call_ok(target, analyzer)
              # A raw-`Ptr[T]` receiver field (`p.n += 1`): proven renders
              # `p->n`, unproven `deref_check(p).n` -- the same render on
              # both sides, like the Optional-ptr row above.
              or _ptr_value_field_recv_ok(target, declared, analyzer)):
        return False
    # A narrowed-Optional or non-scalar target is rejected here (the AST unwraps
    # the former and never reaches the binop branch for the latter). An
    # Optional-ptr-receiver field target IS admitted (proven -> `p->x`,
    # unproven -> `deref_check(p).x` -- the same render lands on both sides of
    # the synthetic `target = (target OP value)`, exactly as the AST
    # substitutes its target string twice).
    target_type = analyzer.get_expr_type(target)
    if not _eligible_scalar(target_type):
        return False
    # FixedInt += BigInt converts the value via `.to_fixed_check<T>()` before
    # the binop -- mirrored as the synthetic THIRBinOp's right_cast at lowering
    # (the same target-type/value-type pair keys both, so gate and emit agree).
    return True

def _str_aug_append_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                       prescan: _Prescan, analyzer) -> bool:
    """A str in-place append `t += v` -> `t += v;` -- the string branch of
    `_gen_aug_assign_code`'s resolved-binop arm, mirrored condition for
    condition: no in-place dunder (checked first there), a resolved binop, op
    `+`, and an owned-str-family target. The value renders bare via
    `gen_expr(value, target_type)` for every admitted shape (str literal /
    str-family name / owned-str call / nested concat), so it is pinned to the
    concat-operand slice.

    The target must be a declared LOCAL: a str param's aug-assign would need
    the AST's owned-copy prologue. The prescan tracks aug-assign targets
    in `aug_assigned`, not `reassigned`. (The AST path itself emits `a += v` on
    the untouched `std::string_view` param there -- an invalid-C++ miscompile,
    tracked in BUGS.md -- so the reject also avoids reproducing it.)"""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    if stmt.resolved_binop is None:
        return False
    target = stmt.target
    if isinstance(target, TpyName):
        if not (target.name in declared
                and target.name not in prescan.param_names):
            return False
    elif isinstance(target, TpyFieldAccess):
        # An owned-str FIELD append `recv.field += v` -> `recv.field += v;` off
        # an F1-record receiver (the same receiver the str field WRITE admits).
        if not _field_receiver_ok(target, declared, analyzer):
            return False
    else:
        return False
    if not _owned_str_append_target(analyzer.get_expr_type(target), analyzer):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_str_concat_operand(stmt.value, vt, analyzer))

def _bytes_aug_concat_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                         prescan: _Prescan, analyzer) -> bool:
    """A bytes `t += v` -> the concat-and-assign
    `t = ::tpy::bytes_concat(t, v);` -- there is NO in-place append for bytes
    (`_gen_aug_assign_code`'s string branch is str-family-pinned), so the AST
    takes the resolved-binop arm and the lowering's generic
    `target = (target OP value)` desugar reproduces it (the native
    `bytes_concat` emit, unwrapped -- `paren_wrap=False`). Mirrored condition
    for condition with the str twin (`_str_aug_append_ok`): no in-place
    dunder, a resolved binop (here the template-less native dunder), op `+`,
    and an owned-bytes target.

    The target must be a declared LOCAL: an aug-assigned bytes PARAM skips the
    AST's owned-copy prologue (the prescan tracks aug-assign targets in
    `aug_assigned`, not `reassigned`) and emits
    `a = ::tpy::bytes_concat(a, b);` on the untouched span param -- the span
    silently rebinds to the concat's dying temporary vector (a dangling-view
    miscompile, the bytes face of the str aug-assign-param bug in BUGS.md) --
    so the reject avoids reproducing it."""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or getattr(rb.method, "cpp_template", None):
        return False
    if not (rb.method.native_function and rb.method.native_name):
        return False
    target = stmt.target
    if not (isinstance(target, TpyName) and target.name in declared
            and target.name not in prescan.param_names):
        return False
    bt = _resolved_bytes_value(analyzer.get_expr_type(target), analyzer)
    if bt is None or not is_bytes_type(bt):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_bytes_concat_operand(stmt.value, vt, analyzer))

def _subscript_recv_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                           analyzer) -> str:
    """Drilldown suffix for a non-admitted subscript receiver -- shared by the
    read (`subscript.recv.*`) and write (`setitem.recv.*`) gates so the
    fallback tally names WHICH receiver shape blocks (the `_recv_shape_reject`
    pattern: runs only on already-rejected shapes)."""
    if isinstance(recv, TpyName):
        return ("recv.name_absent" if recv.name not in locals_
                else "recv.name_shape")  # pointer-local / narrowed binding
    if isinstance(recv, TpyFieldAccess):
        if not isinstance(recv.obj, TpyName):
            return "recv.field_chain"
        if not _field_receiver_ok(recv, locals_, analyzer):
            return "recv.field_parent"
        if _field_decl_type(recv, locals_, analyzer) is None:
            return "recv.field_decl"
        return "recv.field_family"
    if isinstance(recv, TpySubscript):
        return "recv.subscript"
    if isinstance(recv, (TpyCall, TpyMethodCall)):
        return "recv.call"
    return "recv.other"

def _subscript_elem_reject(t: TpyType, analyzer) -> str:
    """Element-family drilldown under `subscript.elem.*`: WHICH non-admitted
    element (list/Array/Span) or key/value (dict) family blocks a subscript
    read whose container kind is already admitted -- splits the old
    `subscript.elem_family` blanket so the tally ranks the per-family cells.
    Runs only on already-rejected shapes (the `_recv_shape_reject` pattern)."""
    args = getattr(t, "type_args", None)
    if not args:
        return "elem.untyped"
    elem = args[0]
    if is_dict(t):
        key, val = args[0], args[1]
        key_ok = _dict_key_shape_ok(key, analyzer)
        if key_ok:
            elem = val
        elif _eligible_scalar(val) or _owned_str_slot(val, analyzer):
            return "elem.dict_key"  # value admitted, key family blocks
        else:
            elem = val  # both blocked; name the value family
    if not isinstance(elem, TpyType):
        return "elem.other"
    el = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem)))
    if isinstance(el, OptionalType):
        return "elem.optional"
    if isinstance(el, UnionType):
        return "elem.union"
    if isinstance(el, TupleType):
        return "elem.tuple"
    if is_list(el) or is_dict(el) or is_set(el) or is_array(el) or is_span(el):
        return "elem.container"
    if _resolved_bytes_value(el, analyzer) is not None:
        return "elem.bytes"
    if _resolved_str_value(el, analyzer) is not None:
        return "elem.strview"  # a view-typed slot (owned str is admitted)
    if isinstance(el, NominalType) and el.is_record:
        return ("elem.record" if _f1_record(el, analyzer)
                else "elem.record_nonf1")
    return "elem.other"

def _setitem_target_ok(
        sub: TpySubscript, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """The shared write-target half of the subscript-write gates: a
    single-index (non-slice) subscript off a bare in-scope container name --
    or a one-level container FIELD off an admitted receiver name
    (`self.xs[i] = v` / `h.d[k] = v`, the read gate's receiver widening) --
    of an admitted family (`_container_scalar_read`: list/Array/Span[scalar],
    dict[fixed-int|BigInt|str, scalar|str] -- so the written element/value
    slot is a value scalar or an owned str), with an eligible index. A
    TypedDict subscript writes a FIELD on the AST path; a narrowed/pointer
    receiver and slice assignment (`xs[a:b] = ...` -> list_set_slice) stay
    AST. A field receiver types at the DECLARED field type, so a narrowed
    Optional/union field (the AST's `(*recv.field)` unwrap) rejects at the
    family check."""
    if isinstance(sub.index, TpySlice) or sub.slice_function_info is not None:
        return note_detail("setitem.slice")
    if sub.typed_dict_field is not None or sub.needs_optional_runtime_check:
        return note_detail("setitem.receiver")
    recv = sub.obj
    if isinstance(recv, TpyName) and (recv.name in pointers
                                      or recv.name in narrowed):
        # A rebound CONTAINER pointer-local (F2d) writes through the deref
        # (`::tpy::__setitem__((*xs), i, v)` -- the name arm's
        # pointer_value_expr render). A narrowing ALIAS whose branch fact is
        # an admitted container renders the bare alias on both paths (the
        # fact retyped `declared` for the branch); a narrowed name whose
        # declared entry is still the union (post-if / assert scopes don't
        # retype) fails the container check below and keeps rejecting.
        rb = declared.get(recv.name)
        rbu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rb)))
               if rb is not None else None)
        if not (rbu is not None and (is_list(rbu) or is_dict(rbu)
                                     or is_set(rbu) or is_array(rbu))):
            return note_detail("setitem.recv.name_shape")
    recv_t = _subscript_container_recv_type(recv, declared, analyzer)
    if recv_t is None:
        # A nested container-element subscript receiver (`d[k][i] = v`): the
        # inner `d[k]` is a container/Array-element borrow lvalue written into.
        # Its own read shape is validated by
        # `_container_ref_alias_elem_subscript`; the written element/value
        # family is checked below off its resolved type.
        # A TUPLE-element container receiver (`t[0][0] = 9` ->
        # `__setitem__(std::get<0>(t), 0, 9)`) is the same lvalue write
        # through the get.
        _tce = (_tuple_container_elem_read(recv, declared, analyzer)
                if isinstance(recv, TpySubscript) else None)
        if (isinstance(recv, TpySubscript)
                and _container_ref_alias_elem_subscript(recv, declared,
                                                        analyzer)):
            recv_t = analyzer.get_expr_type(recv)
        elif _tce is not None:
            recv_t = _tce
        else:
            return note_detail(
                "setitem." + _subscript_recv_reject(recv, declared, analyzer))
    if not (_container_scalar_read(recv_t, analyzer)
            or _bytearray_recv(recv_t)
            or _setitem_widened_family_ok(recv_t, analyzer)):
        return note_detail("setitem.family")
    if (_bigint_index_disposition(sub.index, analyzer.get_expr_type(sub.obj),
                                  analyzer) == "reject"):
        return note_detail("setitem.index")
    return True

def _setitem_widened_elem_ok(elem_t: 'TpyType', analyzer) -> bool:
    """Element-level arm of _setitem_widened_family_ok. The lowering's
    widened_elem dispatch consumes this same predicate so gate and dispatch
    cannot drift apart."""
    return (_container_scalar_read(elem_t, analyzer)
            or _optional_record_field_inner(elem_t, analyzer) is not None
            or _eligible_ptr_union(elem_t, analyzer) is not None
            # A ptr-Optional-element tuple value slot (`d[k] = make_pair(..)`
            # on `dict[K, tuple[P | None, ..]]`): the non-move
            # tuple_to_storage lift; the value shape narrows at the arm.
            # A plain RECORD-element tuple (`dict[K, tuple[Box, Box]]`)
            # takes the same lift for a borrow-tuple CALL source (the
            # copies-into-container shape); other sources narrow at the arm.
            or (isinstance(_swe_b := unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(elem_t))), TupleType)
                and _swe_b.has_pointer_repr_element()
                and (_tuple_elem_slots_ptr_optional(_swe_b)
                     or all(isinstance(_e, TpyType)
                            and _f1_record(unwrap_readonly(_e), analyzer)
                            for _e in _swe_b.element_types)))
            # An open-T element write (`self.data[idx] = val` off
            # Array[T, N]): the bare checked `__setitem__` with the
            # form-neutral T value.
            or _is_type_param_slot(elem_t)
            # A value-repr Optional[scalar] element (`items[0] = None` on
            # `list[Int32 | None]`): the nullopt / bare-scalar STORAGE
            # store; the value shape narrows at the lowering arm.
            or _value_opt_scalar(elem_t, analyzer) is not None
            # An owned-bytes element/value slot (`out["k"] = a` on
            # `dict[str, bytes]`): the view-form source takes the S6
            # `::tpy::bytes_copy(...)` materialize, the bytes twin of the
            # owned-str `std::string(v)` chokepoint in the value tail.
            or _resolved_bytes_value(elem_t, analyzer) is not None
            # A value-repr Optional[str/bytes] value slot (`out["k"] = a`
            # on `dict[str, str | None]`): the WHOLE optional stores bare;
            # the value shape narrows at the lowering arm.
            or _value_opt_owned_view(elem_t, analyzer) is not None
            # An F1-record element/value slot (`s._pool[key] = Box(conn)`):
            # the checked `__setitem__` forwards a record RVALUE bare; the
            # value shape (exact / covariant-upcast rvalue) narrows at the
            # lowering arm.
            or _f1_record(elem_t, analyzer)
            # A recursive-union WRAPPER value slot (`d["c"] = 3` on
            # `dict[str, JsonValue]`): the wrapper's converting ctor absorbs
            # a scalar/str literal bare; the value shape narrows at the
            # lowering arm.
            or _eligible_wrapper_union(elem_t, analyzer) is not None
            # A UNIT value slot (`d["a"] = None` on `dict[str, None]`):
            # `std::monostate` is a value type stored bare, and the only
            # source sema admits is `None`, whose STORAGE literal already
            # renders `std::monostate{}`.
            or is_void_like_type(elem_t))

def _setitem_widened_family_ok(recv_t: 'TpyType | None', analyzer) -> bool:
    """The non-scalar element/value slots the setitem WRITE additionally
    admits (list/Array/dict receivers, same key slice): a nested scalar-read
    container (container-literal values, the type-prefix face), a
    pointer-repr Optional[F1] element (the ptr_to_optional lift), or a
    value-variant union of F1 members (the to_value_variant lift). The VALUE
    shape is narrowed at the lowering per slot kind -- an unsupported source
    rejects there. The aug gates stay scalar/str via their own element
    checks; reads keep the scalar family."""
    return _container_elem_family(
        recv_t, analyzer,
        lambda a: _setitem_widened_elem_ok(a, analyzer),
        # An open-K generic dict FIELD (`d.data["x"] = v` off the declared
        # `dict[K, V]`): the key renders bare against the substituted
        # instantiation, so a type-param key joins the shared slice here.
        dict_key_ok=lambda k: (_is_type_param_slot(k)
                               or _dict_key_shape_ok(k, analyzer)))

def _container_setitem_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """A container subscript write `c[k] = v` -> the checked
    `::tpy::__setitem__(c, k, v);` or (index proven in-bounds) the direct
    `c[static_cast<std::size_t>(k)] = v;`. The value is any eligible scalar /
    str-slice expr rendered against the element slot (literal retype; a
    view-form str source into an owned-str element takes the explicit
    `std::string(v)` copy -- the AST's `_view_source_to_owned` chokepoint),
    or a direct temp-hoisting call (the write is a flushable statement
    position, like a name assign). The AST's other value wraps cannot fire
    here: elements of admitted families are value scalars / owned str, so
    the tuple/Optional/union storage lifts and the last-use move
    (non-value-type locals only) have no admitted source."""
    if not _setitem_target_ok(
            stmt.target, declared, pointers, narrowed, analyzer):
        return False
    return True

def _user_record_setitem_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """A user-record subscript write `recv[key] = v` on a CONCRETE record
    defining `__setitem__` -> the AST's no-container fallback
    `::tpy::__setitem__(recv, key, v);` (checked, never operator[]). Mirrors the
    user-record `__getitem__` READ arm's receiver / key checks: a bare in-scope
    name or one-level field receiver, a value-scalar or str key (a
    runtime-BigInt key only against a BigInt key param -- no narrow on either
    path; a fixed-int key param takes the `.to_fixed_check` narrow, AST). The value slot is a value scalar / Char / enum / Ptr -- the bare-render
    families the checked setitem template forwards unchanged (str/bytes/record
    value slots, whose AST render adds an owned-copy / storage lift, stay AST)."""
    sub = stmt.target
    if isinstance(sub.index, TpySlice) or sub.slice_function_info is not None:
        return False
    if sub.needs_optional_runtime_check or sub.typed_dict_field is not None:
        return False
    vslot = _record_setitem_value(analyzer.get_expr_type(sub.obj), analyzer)
    if vslot is None:
        return False
    recv = sub.obj
    recv_ok = ((isinstance(recv, TpyName) and recv.name in declared
                and recv.name not in pointers and recv.name not in narrowed)
               or (isinstance(recv, TpyFieldAccess)
                   and _field_receiver_ok(recv, declared, analyzer))
               # A module-attr GLOBAL receiver (`os.environ[k] = v`): the
               # module-variable arm renders the `(*slot)` deref and the
               # checked setitem composes over it.
               or _module_var_recv(recv, declared, analyzer))
    if not recv_ok:
        return False
    idx_type = analyzer.get_expr_type(sub.index)
    # The write target lowers through the record_getitem READ arm, which
    # already applies `_narrow_bigint_index` -- so a runtime-BigInt key
    # against a FIXED-int key param takes the same `.to_fixed_check<T>()`
    # narrow here as it does on a read (`p[k] = 9` ->
    # `::tpy::__setitem__(p, k.to_fixed_check<int64_t>(), 9)`). Only the
    # 'reject' disposition (an out-of-int32-range literal headed for a
    # narrow) stays out, exactly as on the read side.
    idx_ok = ((_resolved_scalar(idx_type, analyzer)
               and (not _runtime_bigint(idx_type, analyzer)
                    or _bigint_index_disposition(
                           sub.index, analyzer.get_expr_type(sub.obj),
                           analyzer) != "reject"))
              or _resolved_str_value(idx_type, analyzer) is not None)
    if not idx_ok:
        return False
    vbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vslot)))
    # An open-T (possibly Own[T]) value slot: a MONOMORPHIZED generic
    # receiver (`a[1] = 99` on ArrayList[Int32]) exposes the RAW method fi
    # here, so eligibility keys on the SUBSTITUTED element -- the
    # subscript's own expr type. Scalar elements only: an Own[record]
    # element write is the move machinery, deferred.
    vopen = vbare
    if isinstance(vbare, OwnType):
        vopen = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            vbare.wrapped)))
    if _is_type_param_slot(vopen):
        et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(sub))))
        return (_eligible_scalar(et) or _eligible_char(et)
                or _eligible_enum(et, analyzer) is not None)
    return (_eligible_scalar(vbare) or _eligible_char(vbare)
            or _eligible_enum(vbare, analyzer) is not None
            or _eligible_ptr_value(vbare, analyzer)
            # A str LITERAL into a str-view value slot renders bare (no
            # owned-copy / storage lift fires on a literal) --
            # `other["CONTENT-TYPE"] = "application/json"`. Non-literal str
            # sources keep their view->owned machinery on the AST path.
            or (isinstance(stmt.value, TpyStrLiteral)
                and _resolved_str_value(vbare, analyzer) is not None))

def _record_aug_setitem_ok(
        sub: TpySubscript, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """The record twin of `_setitem_target_ok` for the AUG pair only: a
    single-index subscript off a user record carrying both a getitem (the
    `_record_getitem_key` read family -- the bare operator[] render) and a
    `__setitem__` method (the write renders the fixed `::tpy::__setitem__`
    dunder spelling THIRSetItem emits). The scalar-element / value checks
    stay with the caller."""
    if isinstance(sub.index, TpySlice) or sub.slice_function_info is not None:
        return False
    if sub.typed_dict_field is not None or sub.needs_optional_runtime_check:
        return False
    recv_t = analyzer.get_expr_type(sub.obj)
    if _record_getitem_key(recv_t, analyzer) is None:
        return False
    if not _record_getitem_idx_recv_ok(sub, declared, analyzer, pointers):
        return False
    # The same lookup the PLAIN record-setitem write uses (own method,
    # non-@native record) -- one fact for both paths so they cannot drift.
    if _record_setitem_value(recv_t, analyzer) is None:
        return False
    return bool(_witness("setitem.record_aug"))


def _container_aug_setitem_ok(
        stmt: TpyAugAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """An augmented container subscript write `c[k] OP= v` -> the AST's
    read-modify-write pair `::tpy::__setitem__(c, k, <read> OP v);` with the
    read the CHECKED `::tpy::__getitem__(c, k)` -- the aug arm never takes
    the bounds-safe operator[] even when the node fact is set
    (`_gen_aug_assign_subscript_code` ignores `bounds_safe`). Mirrored
    condition-for-condition with `_scalar_aug_assign_ok`: no in-place
    dunder, a templated resolved binop, an eligible value. The element is a
    resolved scalar (the FixedInt-elem += BigInt value takes the
    `({0}).to_fixed_check<T>()` cast, like the name arm) or an owned str
    (op `+` -- the resolved concat renders `::tpy::str_concat(<read>, v)`,
    pinned to the concat-operand slice like the name append)."""
    if not (_setitem_target_ok(
                stmt.target, declared, pointers, narrowed, analyzer)
            # A user-record getitem/setitem receiver (`items[0] += 5` on
            # ArrayList): the READ renders the record's bare operator[]
            # (the record-getitem arm) and the WRITE the fixed
            # `::tpy::__setitem__` dunder spelling THIRSetItem emits --
            # admitted when the record carries a __setitem__ method.
            or _record_aug_setitem_ok(stmt.target, declared, pointers,
                                      narrowed, analyzer)):
        return False
    if stmt.resolved_inplace is not None:
        return note_detail("setitem.aug_inplace")
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return note_detail("setitem.aug_binop")
    et = analyzer.get_expr_type(stmt.target)
    if _owned_str_slot(et, analyzer):
        vt = analyzer.get_expr_type(stmt.value)
        if not (stmt.op == "+" and _str_concat_operand(stmt.value, vt, analyzer)):
            return note_detail("setitem.aug_value")
    elif not _resolved_scalar(et, analyzer):
        return note_detail("setitem.aug_elem")
    return True

def _is_len_native(e: TpyExpr) -> bool:
    """Whether `e` is the builtin `len(...)` call -- it resolves to the `tpy::__len__`
    @native free function. A user function named `len` has a different (or no)
    native_name and is excluded, so the emit dispatch keys on the symbol, not the name."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "len"):
        return False
    fi = e.resolved_function_info
    return fi is not None and fi.native_name == "tpy::__len__"

def _module_var_len_arg(arg: TpyFieldAccess, locals_: dict[str, TpyType],
                        analyzer) -> bool:
    """Whether a `mod.X` field access reads a REGISTERED module variable --
    dotted (`os.path.X`) or off a bare module binding (`os.environ`). Such a
    read has its own render (the native symbol, the `(*slot)` pointer deref,
    or the qualified `cpp_expr`), never a bare pointer name."""
    if arg.module_var_access is not None:
        return True
    recv = _bare_module_recv(arg.obj, locals_, analyzer)
    return (recv is not None
            and _module_var_read_cpp(recv, arg.field, analyzer) is not None)


def _is_len_call(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """The eligible `len(name)` / `len(recv.field)` form: the builtin len over a
    single in-scope name -- or a one-level container/str/bytes field off an
    admitted receiver (`len(self.xs)`; the receiver renders as its own
    THIRFieldAccess inside the same call emit) -- of a builtin container type or
    a str-slice value (`::tpy::__len__(x)`, Int32 -- the runtime overloads cover
    std::string and std::string_view). The container/str restriction is
    load-bearing, not cosmetic: a container/str is a by-ref/by-value binding
    that emits bare, but a record (or `Optional`) with `__len__` bound to a
    pointer-local would need `(*p)` (the AST's is_indirect_name deref) that the
    bare emit misses -- so only list/dict/set/Array/str (never pointer-locals)
    are admitted. A protocol binding joins them from the other side, for the
    same reason: it is always a C++ reference (`const T_x&` / `Base&`), so it
    too can never be a pointer-local, and `len(items)` on a `Measurable` param
    emits the same `::tpy::__len__(items)`. A field arg types at the DECLARED
    field type, so a narrowed
    Optional[container] field (the AST's `(*recv.field)` unwrap) rejects at the
    family check. The receiver may also be a container-element subscript
    (`len(rows[0].cells)`), which renders the same bare member read off the
    checked element lvalue. A non-name arg (literal, call) rides a later
    cell."""
    if not _is_len_native(e):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    arg = e.args[0]
    if isinstance(arg, TpyName) and arg.name in locals_:
        bt = locals_[arg.name]
    elif (isinstance(arg, TpyFieldAccess)
          and _module_var_len_arg(arg, locals_, analyzer)):
        # `len(os.environ)` -> `::tpy::__len__((*::tpystd::os::_environ::
        # environ))`: the module-variable arm renders the pointer-slot deref
        # ITSELF, so the family restriction's pointer-local concern does not
        # apply -- which is why a `_Environ` record passes here but a record
        # bound to a plain local still rejects below.
        return True
    elif (isinstance(arg, TpyFieldAccess)
          and arg.property_getter_call is not None):
        # `len(f.items)` on a @property -> `::tpy::__len__(f.items())`: the
        # getter CALL renders in place of the member read, so the arg is a
        # call result -- never a pointer-local, which is what the family
        # restriction below actually guards. The method-call arm owns the
        # receiver admission (an unroutable one falls the body back).
        bt = analyzer.get_expr_type(arg)
        if bt is None:
            return False
    elif (isinstance(arg, TpyFieldAccess)
          and (_field_receiver_ok(arg, locals_, analyzer)
               or _field_over_container_subscript_ok(arg, locals_, analyzer))):
        bt = _field_decl_type(arg, locals_, analyzer)
        if bt is None:
            return False
    elif (isinstance(arg, TpyMethodCall)
          and _dict_view_iterable_ok(
              arg, locals_, analyzer, methods=("values", "keys", "items"))):
        # `len(d.values())` / `.keys()` / `.items()`: the view rvalue lowers
        # to `::tpy::dict_values(d)`, which the runtime `__len__` overloads
        # accept -- the arg render is view-neutral like a name/field.
        return True
    elif isinstance(arg, TpyStrLiteral):
        # `len("hello")` -> `::tpy::__len__("hello")`: the literal binds the
        # const char[N] the string_view __len__ overload accepts, bare.
        return True
    elif isinstance(arg, TpySubscript):
        # `len(s[a:b])` -> `::tpy::__len__(::tpy::str_slice(...))`: a str-slice
        # SUBSCRIPT result is a str value rendered bare, so the __len__ overload
        # accepts it like a name/field.
        st = analyzer.get_expr_type(arg)
        if _resolved_str_value(st, analyzer) is not None:
            return True
        # A nested-container ELEMENT read (`len(groups["a"])` ->
        # `::tpy::__len__(::tpy::__getitem__(groups, "a"))`): the checked read
        # is an lvalue into the container's storage, so it renders bare in the
        # native slot like a name/field. The REF_ALIAS borrow-source shape is
        # the same one the arg row prechecks (no optional check, no slice, a
        # routable index off an admitted receiver), so the value-position
        # element gate never has to see it. A SLICE result is a fresh
        # container rvalue rather than an element lvalue -- its own cell.
        # An owned-bytes/str element (`len(out["k"])` on `dict[str, bytes]`)
        # is the same lvalue read; the vector/string __len__ overloads
        # accept it bare.
        return (_container_ref_alias_elem_subscript(arg, locals_, analyzer)
                or _borrow_elem_subscript_shape(
                    arg, locals_, analyzer,
                    lambda t, a: _container_elem_family(
                        t, a,
                        lambda m: (((_lb := _resolved_bytes_value(m, a))
                                    is not None and is_bytes_type(_lb))
                                   or ((_ls := _resolved_str_value(m, a))
                                       is not None and is_str_type(_ls))))))
    elif isinstance(arg, TpyNamedExpr):
        # `len(v := h.view())` -- a container walrus arg: the comma form's
        # `*v` result is the same container lvalue a name renders; the
        # walrus arm validates its own target/source classes.
        bt = analyzer.get_expr_type(arg)
    else:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return (is_list(t) or is_dict(t) or is_set(t) or is_array(t) or is_span(t)
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None  # span/vector overloads
            or is_string_type(t)  # a String local: same std::string overload
            or (_protocol_binding(t) is not None
                and _witness("len.protocol")))

def _len_arg_reject(arg: TpyExpr, locals_: dict[str, TpyType],
                    analyzer) -> str:
    """Drilldown label for a builtin `len(...)` call `_is_len_call` rejected --
    splits the reject by ARG shape (the receiver axis), so the tally ranks the
    len widenings. Runs only on already-rejected shapes."""
    if isinstance(arg, TpyName):
        return ("len.name_global" if arg.name not in locals_
                else "len.name_family")  # pointer-local / record __len__
    if isinstance(arg, TpyFieldAccess):
        if not _field_receiver_ok(arg, locals_, analyzer):
            return "len.field_parent"
        ft = _field_decl_type(arg, locals_, analyzer)
        return ("len.field_family" if ft is not None else "len.field_decl")
    if isinstance(arg, TpySubscript):
        return "len.arg_subscript"
    if isinstance(arg, (TpyCall, TpyMethodCall)):
        return "len.arg_call"
    return "len.arg_other"

_SPECIAL_BUILTIN_QNAMES = frozenset({qnames.COPY, qnames.COPY_ITER,
                                     qnames.OWN_ITER, qnames.TRY_PARSE})


def copy_plain_record_source(init: TpyExpr, analyzer,
                             pointers: 'AbstractSet[str]') -> 'TpyType | None':
    """The `copy(x)` builtin over a bare plain F1-record NAME source -- the
    copy-construct rvalue arm of `_gen_copy_expr` (`T(x)`). Returns the source
    record's TpyType, or None. Excludes a pointer-local source (`gen_expr_deref`
    would `(*p)`), an Optional-ptr / pointer-variant / tuple source (the other
    `_gen_copy_expr` branches), and a record-ctor arg (its prvalue arm)."""
    arg = copy_call_arg(init, analyzer)
    if arg is None:
        return None
    if not isinstance(arg, TpyName) or arg.name in pointers:
        return None
    at = analyzer.get_expr_type(arg)
    return at if _f1_record(at, analyzer) else None


def copy_plain_container_source(init: TpyExpr, analyzer,
                                pointers: 'AbstractSet[str]') -> 'TpyType | None':
    """The CONTAINER sibling of `copy_plain_record_source`: `copy(xs)` over a
    bare list/dict/set NAME -> the copy-construct rvalue
    (`std::vector<int32_t>(data)`). `_gen_copy_expr`'s tail is
    `f"{arg_type.to_cpp()}({arg})"`, generic over the source type, so the
    container spells its own C++ form exactly as a record spells `T(x)`.
    Same exclusions: a pointer-local source would render `(*p)`.
    """
    arg = copy_call_arg(init, analyzer)
    if arg is None:
        return None
    if not isinstance(arg, TpyName) or arg.name in pointers:
        return None
    at = analyzer.get_expr_type(arg)
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if not (is_list(au) or is_dict(au) or is_set(au)):
        return None
    return au


def copy_ctor_rvalue_source(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """`copy(T(...))` over a record CONSTRUCTOR argument -- the prvalue arm of
    `_gen_copy_expr`, which returns the constructor's own render UNCHANGED (a
    prvalue is already an rvalue, so there is nothing to copy). Returns the
    inner constructor call, so the consuming sink lowers it exactly as it
    would the bare `T(...)` source; None when this is not that shape.

    The peel is sound only because the render is literally identical -- the
    copy-CONSTRUCT arm (`copy_plain_record_source`) spells `T(x)` and must
    never come through here."""
    arg = copy_call_arg(e, analyzer)
    if arg is None:
        return None
    if not isinstance(arg, TpyCall) or not isinstance(arg.func, TpyName):
        return None
    # `_gen_copy_expr` keys the prvalue arm on the registry lookup of the
    # callee NAME, not on the resolved fi -- mirror that exactly.
    if analyzer.registry.get_record(arg.func_name) is None:
        return None
    return arg


def _free_callee_kind(e: TpyCall, analyzer, *,
                      generator_ok: bool = False,
                      error_return_ok: bool = False,
                      coro_factory_ok: bool = False,
                      ret_cast_ok: bool = False
                      ) -> 'tuple[str, str] | None':
    """Classify a bare-name free callee into its emit kind + pre-rendered
    payload -- the routing fact consumed by lowering:
    `("plain", "")` the raw same-module `name(args)`; `("imported", cpp)`
    the cross-module qualified spelling (`imported_free_callee_cpp`, the
    decision shared with the AST emit -- lowering stamps
    `THIRCall.callee_cpp`); `("native", native_name)` gen_call_from_fi's
    `::native(args)` arm (C++ @native imports only -- extern-C spells the
    raw unqualified symbol, a different arm); `("template", tmpl)` the
    positional-only @cpp_template expansion (gen_call_from_fi's template
    arm with no substitution context). None = an emit shape the slice does
    not reproduce. Shared by free-call lowering and (through the plain/
    imported wrapper `_plain_free_callee_ok`) the record-rvalue classifier's
    by-value record-returning call face.

    `generator_ok` admits a free GENERATOR callee (set only by the iterable
    position): its factory call spells exactly like a plain/imported call
    (both the lambda peephole's `inline auto f(...)` and the resumable
    frame's factory), so only the callee-kind reject differs. A GENERIC
    generator callee rides the generic arm's explicit-targ spelling under
    the same flag.

    `coro_factory_ok` is the async sibling (the make_adapter arg position
    and the coro-handle frame write): an async-def CALL is a
    coroutine-FACTORY call spelling exactly like a plain/imported call,
    and a generic factory rides the generic arm the same way."""
    if not isinstance(e.func, TpyName):
        note_detail("call.expr_callee")
        return None
    if e.kwargs or e.double_star_unpack is not None:
        note_detail("call.kwargs")
        return None
    if e.call_type is not None:
        note_detail(f"call.special.call_type.{_call_type_fam(e.call_type)}")
        return None
    # Explicit / inferred type args ride only the NATIVE and cpp_template
    # kinds: the AST skips explicit template args for native imports (C++
    # deduction over natural param types) and substitutes them into the
    # template ({T} via expand_fi_template) -- both targ-blind emits the
    # existing arms carry. The plain `f<T>(args)` explicit spelling and the
    # repr-subst adapter spelling are rejected at the tail / here.
    has_targs = bool(e.type_args or e.inferred_type_args)
    if has_targs and getattr(e, "representational_subst_params", None):
        note_detail("call.special.type_args")
        return None
    # A lingering `subscript_callee` is the parser's fallback for an
    # `f[T](x)` shape sema resolved as a genuine explicit-targ call (sema
    # clears the field when it DOES rewrite to the expression callee), so
    # only a parse error on the type-arg list keeps the call out.
    if (e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.type_args_parse_error):
        sub = ("isinstance" if e.isinstance_var is not None
               else "dunder_call" if e.dunder_call is not None
               else "compile_time_assert" if e.compile_time_assert
               else "macro" if e.macro_expansion is not None
               else "targ_parse_error" if e.type_args_parse_error
               else "enum_or_cast")
        note_detail(f"call.special_form.{sub}")
        return None
    fi = e.resolved_function_info
    if fi is None:
        note_detail("call.unresolved")
        return None
    # Bespoke AST arms that fire BEFORE the fi dispatch: the four
    # @builtin_function specials (_maybe_gen_special_builtin_call, keyed on
    # qualified_name), the print stream chain (keyed on func_name), and the
    # ord single-char-literal constant fold.
    if fi.qualified_name in _SPECIAL_BUILTIN_QNAMES or e.func_name == "print":
        note_detail("call.builtin_special")
        return None
    if e.func_name == "ord" and len(e.args) == 1:
        a0 = _peel_coerce(e.args[0])
        if isinstance(a0, TpyStrLiteral) and len(a0.value) == 1:
            note_detail("call.builtin_special")
            return None
    # The error_return guard keeps fallible callees out of every face that
    # does not render the unwrap; `error_return_ok` is set only by the
    # free-call lowering arm, whose tail wraps the call in
    # THIRErrorReturnUnwrap (or hands it raw to the statement-level
    # bind/discard/pass-through handlers).
    if fi.error_return_type is not None and not error_return_ok:
        note_detail("call.error_return")
        return None
    # A literal-specialized overload mangles its DEFAULT-linkage callee to
    # `f__lit_N` (literal_mangled_name). The AST mangles only when there are
    # MULTIPLE overloads (a single Literal-param stub is a plain call) AND
    # the callee is not a @native import (a native literal overload's name
    # is its resolved native symbol, picked by sema's overload resolution --
    # no `__lit_` mangling): those two shapes keep the native/plain
    # spelling below. The mangled name threads into the plain (bare) and
    # imported (qualified) spellings; the generic pairing keeps its reject.
    lit_mangled = None
    if any(isinstance(p.type, LiteralType) for p in fi.params):
        overloads = analyzer.registry.get_function(e.func_name)
        if (overloads is not None and len(overloads) > 1
                and not (fi.native_function or fi.native_name)):
            lit_mangled = literal_mangled_name(e.func_name, fi)
    if fi.frame_captures is not None:
        # A closure local (nested def): the bare lambda-variable call --
        # spelled exactly like the plain arm, decided BEFORE the registry /
        # import spellings (the AST's nested_def_locals arm order). A
        # closure shadowing an imported OR builtin name is ambiguous here
        # (the AST's imported/builtin arm precedes its nested-def arm and
        # would call the BUILTIN, a pre-existing CPython divergence --
        # BUGS.md) -> reject the shadow, keeping the bytes on the AST path.
        if (e.func_name in analyzer.imported_names
                or imported_free_callee_cpp(analyzer.ctx.module_attributes,
                                            e.func_name)):
            note_detail("call.closure_import_shadow")
            return None
        return ("plain", "")
    # A native-FUNCTION `__init__` ctor (`int(str)` -> `tpy::BigInt::from_str`,
    # float/bytes from_str) is receiver-less -- it spells like a native free
    # call, so let it reach the native arm below rather than the method reject.
    # Excluded: exactly the folding shape -- `float("nan"/"inf"/...)` becomes
    # a constexpr numeric_limits constant on the AST path (not float_from_str),
    # so mirror `_try_float_str_fold`'s trigger (raw un-peeled literal, the
    # shared `_FLOAT_STR_CONSTANTS` table). Ordinary literals
    # (`int("not_a_number")`) render identically through the native arm.
    would_fold = (
        fi.owning_type_qname == "builtins.float" and len(e.args) == 1
        and isinstance(e.args[0], TpyStrLiteral)
        and e.args[0].value.strip().lower() in _FLOAT_STR_CONSTANTS)
    native_free_ctor = (
        fi.native_function and fi.is_method and fi.name == "__init__"
        and not would_fold)
    # A generic generator/coro FACTORY is admitted only where the factory
    # spelling is consumed (generator_ok / coro_factory_ok): the callee-kind
    # reject below subsumes the generic flavor, and the admitted flavor
    # rides the generic arm's explicit-targ spelling (`pair<int32_t>(..)`,
    # `ident<int32_t>(..)`).
    if (((fi.is_method or fi.is_staticmethod) and not native_free_ctor)
            or (fi.is_async and not coro_factory_ok)
            or (fi.is_generator and not generator_ok)
            or fi.is_property_getter or fi.is_property_setter):
        note_detail("call.callee_kind")
        return None
    if fi.cpp_template:
        # A generic template substitutes its named {T} placeholders exactly
        # like gen_call_from_fi (expand_fi_template); only a
        # fully-substituted positional-only result expands with no
        # receiver/substitution context (the scalar-ctor cell's rule).
        tmpl = (expand_fi_template(fi, e.type_args or e.inferred_type_args)
                if has_targs else fi.cpp_template)
        if _positional_only_template(tmpl, len(e.args)):
            return ("template", tmpl)
        note_detail("call.template_shape")
        return None
    if fi.native_function or fi.native_name or fi.linkage in (
            FunctionLinkage.NATIVE, FunctionLinkage.NATIVE_C):
        # C++ @native imports spell the absolute-qualified symbol -- the
        # AST's is_native_import arm (`qualify_native_name(native_name or
        # name)`, idempotent on `::`-prefixed stub names); a user-module
        # `@native def` carries only the NATIVE linkage (no stub flags) and
        # spells the same. @native(binding="C") emits the RAW unqualified
        # symbol (the extern "C" re-declaration is namespace-scoped, a `::`
        # would miss it). A declared cpp_return_type (the AST wraps the
        # call in the narrowing static_cast) routes only where the caller
        # renders the cast (`ret_cast_ok` -- the free-call lowering arm
        # composes the static_cast template); @export stays AST.
        if (fi.native_cpp_return_type is None
                or (ret_cast_ok and fi.linkage == FunctionLinkage.NATIVE
                    and fi.return_type is not None
                    and fi.error_return_type is None)):
            if fi.linkage == FunctionLinkage.NATIVE:
                # The emit's native_name arm applies qualify_native_name,
                # so the node carries the raw symbol (name when the stub
                # declares no rename -- the user-module `@native def` face).
                return ("native", fi.native_name or fi.name)
            if fi.linkage in (FunctionLinkage.NATIVE_C,
                              FunctionLinkage.EXPORT_C):
                # Distinct kind: the emit's native_name arm force-qualifies,
                # so the raw symbol rides callee_cpp (rendered verbatim).
                # A RENAMED @export(binding="C") (`@export("Helper_Add")`)
                # lands here via its native_name; the un-renamed export
                # takes the linkage arm below.
                return ("native_c", fi.native_name or fi.name)
        note_detail("call.native_shape")
        return None
    # Only a DEFAULT-linkage function emits as a bare/qualified `name(args)`.
    if fi.linkage != FunctionLinkage.DEFAULT:
        if fi.linkage is FunctionLinkage.EXPORT_C:
            # An @export(binding="C") callee spells the RAW unqualified C
            # symbol (`helper_add(42)` -> `Helper_Add(42)`; the extern "C"
            # definition is namespace-scoped, so no `::` qualification) --
            # the native_c verbatim render.
            return ("native_c", fi.native_name or fi.name)
        # Any future linkage is rejected rather than silently mis-emitted.
        note_detail("call.linkage")
        return None
    icc = imported_free_callee_cpp(analyzer.ctx.module_attributes, e.func_name,
                                   lit_mangled)
    if fi.type_params or has_targs:
        # A plain TPy generic callee spells explicit template args
        # (`f<int32_t>(args)`, type_to_cpp_stored per arg) over the plain /
        # imported spelling; the args resolve against the selected stub's
        # params with the inferred substitution (the TypeParamRef ref-slot
        # temp rule). An OVERLOAD GROUP resolves to the sema-selected stub --
        # the AST's `func_info` pick, which the arity of the template-arg
        # list depends on -- and still spells the plain name
        # (`apply<int32_t>(f1, 5)`).
        fis = analyzer.registry.get_function(e.func_name)
        if fis is None:
            note_detail("call.callee_kind.generic")
            return None
        if len(fis) > 1:
            # The AST's `is_literal_mangled` condition: a Literal-param stub
            # in a group renames the callee. No corpus case selects one from
            # a GENERIC group, so this mirrors the rename condition rather
            # than relying on sema never producing that pairing.
            if any(isinstance(p.type, LiteralType) for p in fi.params):
                note_detail("call.callee_kind.literal_mangled")
                return None
            root = fi
        else:
            root = fis[0]
        if (not root.type_params or not e.inferred_type_args
                or len(e.inferred_type_args) != len(root.type_params)):
            note_detail("call.callee_kind.generic")
            return None
        if icc is None and e.func_name in analyzer.imported_names:
            note_detail("call.imported_symbol")
            return None
        return ("generic", icc or "")
    if icc is not None:
        return ("imported", icc)
    if e.func_name in analyzer.imported_names:
        # An implicitly-imported builtin-module callee: the AST's
        # conditional-qualification arm (inert today, forward-looking for
        # pure-TPy builtins) -> AST path.
        note_detail("call.imported_symbol")
        return None
    # A local literal-specialized callee spells the bare mangled name
    # (the AST's `escape_cpp_name(mangled)`); the payload rides callee_cpp.
    if lit_mangled is not None:
        return ("plain", escape_cpp_name(lit_mangled))
    return ("plain", "")


def _call_type_fam(t: TpyType) -> str:
    """Coarse `call_type` family for the special-form drilldown detail --
    sizes the generics frontier's instantiation buckets; delete the split
    when the bucket empties."""
    if isinstance(t, PtrType):
        return "ptr"
    if isinstance(t, NominalType):
        if t.is_user_record:
            return "genrec" if t.type_args else "record"
        if t.type_args:
            return "builtin_generic"
    return "other"


def _plain_free_callee_ok(e: TpyCall, analyzer) -> bool:
    """The plain/imported/generic subset of `_free_callee_kind` -- the
    callee-shape head of the by-value record-returning call face
    (native/template record returns stay AST there). A generic callee's
    record rvalue rides the same bare spelling with the explicit targs
    (`BoxC cloned = clone_it<BoxC>(box);`)."""
    kind = _free_callee_kind(e, analyzer)
    return kind is not None and kind[0] in ("plain", "imported", "generic")


def _record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """Shallow shape of a by-value record-returning free call.

    Argument subtrees are lowered by their own call arms.
    """
    if not isinstance(e, TpyCall):
        return False
    if not (_f1_record(analyzer.get_expr_type(e), analyzer)
            and is_rvalue_source(analyzer, e)):
        return False
    return _rvalue_free_call_shape(e, analyzer)


def _rvalue_free_call_shape(e: 'TpyCall', analyzer) -> bool:
    """The return-type-blind callee/arg-shape half of
    `_record_rvalue_call_shape`: plain free callee, `_call_arity_ok` arity,
    no kwargs, no str-literal multi-overload pin. The union slot-hoist decl
    reuses it for `Own[A | B]`-returning calls (its type verdict lives at the
    caller)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    fi = e.resolved_function_info
    if (fi is None or fi.is_constructor or not _call_arity_ok(e, fi)
            or not _plain_free_callee_ok(e, analyzer)):
        return False
    if any(isinstance(_peel_coerce(a), TpyStrLiteral) for a in e.args):
        overloads = analyzer.registry.get_function(e.func_name)
        if overloads is not None and len(overloads) > 1:
            return False
    return True


def _call_arity_ok(e: 'TpyCall | TpyMethodCall', fi) -> bool:
    """Positional arity for a plain call / method call -- `_ctor_arity_ok`'s
    rule: exact arity, or fewer args when the OMITTED trailing params all
    carry a default. Each default rides the emitted C++ signature
    (`emit_defaults`), and BOTH paths pass only the provided args (the AST
    arg loops zip-truncate), so the truncated call is byte-identical. A
    variadic slot has no positional default to fall back on. Sema gap-fills
    kwargs / keyword-only slots into `e.args` before lowering, so the
    omitted tail here is always positional."""
    n = len(e.args)
    if n > len(fi.params):
        return False
    if not all(p.has_default and not p.is_variadic for p in fi.params[n:]):
        return False
    if n < len(fi.params):
        return _witness("call.omit_defaults")
    return True


def _strlit_overload_pin_fires(e: 'TpyCall', fi, analyzer) -> bool:
    """Whether a str-literal arg to this call takes gen_call_arg's overloaded
    pin (`param_view_t("...")`) -- the AST's `_wants_str_literal_pin` (a
    MULTI-overload, non-generic callee) plus the per-arg firing test (the
    pin fires only when the str literal's param slot renders `str`/`StrView`).
    A str literal into any OTHER slot -- a `Char`, a `Literal[...]` mode
    selector, an owned `String` -- renders through its own arm, which THIR
    reproduces, so only a str/StrView slot needs the AST path. Used to keep
    the plain / native / record-rvalue free-call faces from over-rejecting a
    str-literal overloaded call whose pin never actually fires."""
    if fi is None or fi.is_generic():
        return False
    overloads = analyzer.registry.get_function(e.func_name)
    if overloads is None or len(overloads) <= 1:
        return False
    params = fi.params
    for i, a in enumerate(e.args):
        if not isinstance(_peel_coerce(a), TpyStrLiteral):
            continue
        if i >= len(params):
            continue
        slot = unwrap_readonly(unwrap_ref_type(params[i].type))
        if is_str_type(slot) or is_str_view_type(slot):
            return True
    return False


def _strlit_pin_slot(a: TpyExpr, ptype: 'TpyType | None', fi,
                     overload_count: int) -> 'TpyType | None':
    """The param slot ONE str-literal arg must be pinned to
    (`std::string_view("x")`), or None when this arg takes no pin -- the
    per-arg half of `_strlit_overload_pin_fires`, keyed exactly like
    gen_call_arg's `overloaded_call` branch (`_wants_str_literal_pin` plus
    the per-arg str/StrView slot test)."""
    if not isinstance(_peel_coerce(a), TpyStrLiteral):
        return None
    if fi is None or fi.is_generic() or overload_count <= 1:
        return None
    slot = (unwrap_readonly(unwrap_ref_type(ptype))
            if isinstance(ptype, TpyType) else None)
    if slot is not None and (is_str_type(slot) or is_str_view_type(slot)):
        return slot
    return None


def _strlit_overload_pin_arg(e: 'TpyCall', fi, a: TpyExpr,
                             ptype: 'TpyType | None',
                             analyzer) -> 'TpyType | None':
    """`_strlit_pin_slot` over a FREE callee's overload set."""
    overloads = analyzer.registry.get_function(e.func_name)
    return _strlit_pin_slot(a, ptype, fi,
                            len(overloads) if overloads else 0)


def _strlit_method_pin_arg(e: 'TpyMethodCall', fi, a: TpyExpr,
                           ptype: 'TpyType | None',
                           analyzer) -> 'TpyType | None':
    """`_strlit_pin_slot` over a METHOD's overload set -- counted like the
    AST's `record_info.get_method_overloads` (own methods only, no parents,
    which is what `_wants_str_literal_pin` is handed)."""
    if fi is None or fi.cpp_template is not None or fi.native_function \
            or fi.native_name:
        # Builtin / @native methods emit through `gen_call_from_fi`, which
        # never applies the pin -- it is the USER-record arm's gen_call_arg.
        return None
    recv_t = analyzer.get_expr_type(e.obj)
    ri = (analyzer.registry.get_record_for_type(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t))))
        if recv_t is not None else None)
    if ri is None:
        return None
    return _strlit_pin_slot(a, ptype, fi,
                            len(ri.get_method_overloads(e.method)))


def _native_ctx_manager_ok(e: TpyExpr, analyzer) -> bool:
    """A @native record-returning free call admitted as a sync `with` manager
    (`with open(path, mode) as f: ...`). The callee resolves to the native arm
    (`_free_callee_kind` -> ('native', symbol)); the manager expr lowers
    through the ordinary native free-call arm (THIRCall.native_name), rendered
    `::tpy::symbol(args)` -- byte-identical to `_gen_with`'s stored `__ctx_N`
    manager. The plain/imported/ctor manager sources ride
    `_record_rvalue_source_shape`; this covers the native residue those two
    reject. Guards the callee kind, arity, and the str-literal overloaded pin
    (open's `Literal` mode arg does not pin -- a LiteralType slot). Args are
    validated by the manager expr's own lowering arm (shallow, like
    `_record_rvalue_source_shape`)."""
    if not isinstance(e, TpyCall):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    fi = e.resolved_function_info
    if fi is None or fi.is_constructor or not _call_arity_ok(e, fi):
        return False
    k = _free_callee_kind(e, analyzer)
    if k is None or k[0] != "native":
        return False
    return not _strlit_overload_pin_fires(e, fi, analyzer)


def _native_record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """A @native free call returning a by-value F1 record (`open(path)` ->
    `::tpy::TextFile f = ::tpy::builtin_open(path);`). The native residue of
    `_record_rvalue_call_shape`, whose `_plain_free_callee_ok` gate rejects
    native callees. Reuses `_native_ctx_manager_ok`'s callee-kind/arity/pin
    shape check and adds the record-rvalue return verdict, so the plain
    value-decl and the STORAGE value position admit the bare call. The record
    lands by value (an rvalue return, no `Own`/borrow lift), byte-identical to
    the AST's plain-value decl (`is_rvalue_source` -> `_needs_indirection`
    False)."""
    if not isinstance(e, TpyCall):
        return False
    ret = analyzer.get_expr_type(e)
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    return (_f1_record(t, analyzer) and is_rvalue_source(analyzer, e)
            and _native_ctx_manager_ok(e, analyzer))


def _er_record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """An @error_return record-rvalue free call at a position that composes
    on the er-unwrap stmt-expr (`return make_data(v).value` ->
    `({ auto __er_1 = make_data(v); ... unwrap_ref_move(*__er_1); }).value`):
    the same callee/arity shape as `_record_rvalue_call_shape`, er-tolerant
    -- the call lowering's `_er_wrap` renders the unwrap wherever the call
    routes."""
    if not isinstance(e, TpyCall):
        return False
    fi = e.resolved_function_info
    if fi is None or fi.error_return_type is None:
        return False
    if not (_f1_record(analyzer.get_expr_type(e), analyzer)
            and is_rvalue_source(analyzer, e)):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if not _call_arity_ok(e, fi):
        return False
    kind = _free_callee_kind(e, analyzer, error_return_ok=True)
    return (kind is not None and kind[0] in ("plain", "imported")
            and _witness("call.er_record_rvalue"))


def _template_record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """A @cpp_template free call returning a by-value F1 record
    (`make_default[Point]()` -> `Point p = Point{};`, `unsafe_load(p, 0)`
    -> `Point loaded = p[0];`): the TEMPLATE residue of
    `_record_rvalue_call_shape` -- the expanded positional-only template
    renders bare in the STORAGE slot; args gate at their own rows."""
    if not isinstance(e, TpyCall):
        return False
    if not (_f1_record(analyzer.get_expr_type(e), analyzer)
            and is_rvalue_source(analyzer, e)):
        return False
    kind = _free_callee_kind(e, analyzer)
    return (kind is not None and kind[0] == "template"
            and _witness("call.template_record_rvalue"))



def _rvalue_storage_decl_call(e: TpyExpr, analyzer) -> bool:
    """A call rvalue landing in a plain spelled value decl -- the general
    sibling of `_native_record_rvalue_call_shape`, which admits @native free
    calls only. An F1-record result (`Animal parent =
    ::tpy::any_cast_or_panic<Animal>(a);`) or an owned container / str / bytes
    result (`std::vector<uint8_t> data = r.read();`) returned BY VALUE decls as
    the plain copy on both paths. The ELEMENT family is deliberately not
    checked: it decides the local's downstream reads, which gate themselves --
    the decl render only spells the slot. The caller pairs this with the
    reassigned / hoisted / move_through guards (a rebound non-value local takes
    the AST's pointer form) and the callee's own arms re-validate at lowering."""
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        return False
    ret = analyzer.get_expr_type(e)
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    shape_ok = (_f1_record(t, analyzer)
                or is_list(t) or is_dict(t) or is_set(t) or is_array(t)
                or _resolved_str_value(t, analyzer) is not None
                or _resolved_bytes_value(t, analyzer) is not None)
    return shape_ok and is_rvalue_source(analyzer, e)


def _rvalue_storage_decl_binop(e: TpyExpr, analyzer) -> bool:
    """A record-returning dunder BINOP rvalue at a plain value decl
    (`r = 4 | f` -> `Flags r = ((4) | (f));`): the user dunder returns the
    record BY VALUE, so the decl is the same plain spelled copy the call rows
    take. A BORROW-returning dunder (`__add__ -> Acc&`) aliases an operand --
    the AST decls `const Acc& c = ((a) + (b));` there, so `is_rvalue_source`
    (which reads the dunder's return convention) is the whole discriminator."""
    if not isinstance(e, TpyBinOp):
        return False
    ret = analyzer.get_expr_type(e)
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _f1_record(t, analyzer) and is_rvalue_source(analyzer, e)


def _native_iterable_iterator_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                                       analyzer) -> bool:
    """An iterator-producing call rvalue into a NATIVE builtin's structural
    `Iterable[T]` slot: a nested combinator (`zip(enumerate(names), xs)` --
    an Iterator-protocol-returning @cpp_template call) or a generator
    factory (`map(f, gen())`). Both render bare in iterable position; the
    inner call's own lowering re-validates its args (container names stay
    bare there -- no own_iter wrap fires outside the instantiation arm's
    last-use handling)."""
    pb = _protocol_binding(ptype)
    if pb is None or pb.name not in ("Iterable", "Sequence"):
        return False
    if not isinstance(a, TpyCall) or a.resolved_function_info is None:
        return False
    fi = a.resolved_function_info
    if fi.is_generator:
        return _free_callee_kind(a, analyzer, generator_ok=True) is not None
    if not fi.cpp_template:
        return False
    rt = analyzer.get_expr_type(a)
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.name == "Iterator")


def _native_protocol_field_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A field-access arg at a native/template callee's protocol slot whose
    bare member read the slot binds directly. TWO regimes, checked in this
    order: an OPEN type-param field at a still-UNSUBSTITUTED protocol slot
    (form-neutral, so the bare read is whatever monomorphization resolves
    it to), and -- the rest of this docstring -- a SUBSTITUTED slot keyed on
    an exact type match
    (`repr(self.label)` -> `::tpy::repr_of(this->label)`): a value-repr
    Optional field passed WHOLE (repr_of/std::format take the optional
    directly), an Optional[F1-record] field (storage-form
    `std::optional<T>`), or an F1-record field (the ADL overload binds
    the record). The slot arrives SUBSTITUTED (Representable ->
    Optional[str]), so the protocol-ness is gone -- key on the slot
    matching the arg's analyzed type exactly. Value-typed fields already
    ride `_native_protocol_value_arg`; a NARROWED optional field's
    analyzed type is its inner, so it takes those value rows' deref
    render, not this bare-optional one."""
    if not isinstance(a, TpyFieldAccess):
        return False
    at = analyzer.get_expr_type(a)
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
         if at is not None else None)
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if (slot is not None and is_protocol_type(slot)
            and isinstance(t, TypeParamRef)):
        # An OPEN type-param field at a still-unsubstituted protocol slot
        # (`len(self.value)` inside a generic body -> `::tpy::__len__(
        # this->value)`): the member read is form-neutral for an open T, so
        # the bare render is whatever the monomorphization resolves it to --
        # the same one the AST emits. A SUBSTITUTED slot rides the
        # exact-match rows below.
        return _witness("arg.native_protocol_open_field")
    if (slot is not None and is_protocol_type(slot)
            and not is_dyn_protocol(slot)
            and _f1_record(t, analyzer)):
        # A concrete F1-record field at a still-PROTOCOL slot of a
        # TEMPLATE callee (`repr(self.origin)` with the `repr_of({0})`
        # cpp_template fi, whose param stays `Representable`): the bare
        # member read binds the template arg, exactly the substituted F1
        # row's render.
        return _witness("arg.protocol_record_field")
    if t is None or slot is None or t != slot:
        return False
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(t.inner)
        return bool(inner.is_value_type() or _f1_record(inner, analyzer))
    if is_list(t) or is_set(t) or is_dict(t):
        # A value-element container field (`repr(self.items)` ->
        # `::tpy::repr_of(this->items)`; a dict rides its own overload --
        # `::tpy::dict_to_str(this->lookup)`): the runtime repr/str
        # overloads take the container directly. (The bare-lvalue chain
        # read at an UNSUBSTITUTED structural slot -- `len(h.c.item)` --
        # is `_protocol_slot_arg`'s row, which runs first and is
        # element-blind by design; this exact-match row keeps its
        # value-element whitelist.)
        args = getattr(t, "type_args", ())
        return bool(args) and all(
            _eligible_scalar(unwrap_readonly(x))
            or _owned_str_slot(unwrap_readonly(x), analyzer) is not None
            for x in args if isinstance(x, TpyType))
    return _f1_record(t, analyzer)


def _native_own_scalar_lvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer) -> bool:
    """A value-scalar NAME into an `Own[..]` slot of a native/template
    callee (`unsafe_init(self._ptr, value)` at `Own[T]`): gen_call_arg's
    inline_template Own arm returns the bare lvalue -- the template binds
    it natively, no copy+move temp (str slots keep the temp: their copy is
    the view->owned conversion, and the raw generic `Own[T]` slot is not
    str). Value scalars never auto-move, so the _maybe_move interplay is
    vacuous; the ARG type carries the scalar judgment where the slot is
    the RAW `Own[T]`."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
         if isinstance(ptype, TpyType) else None)
    if not isinstance(t, OwnType):
        return False
    if is_str_type(unwrap_readonly(t.wrapped)):
        return False
    if not (isinstance(a, TpyName) and a.name in locals_):
        return False
    return _resolved_scalar(analyzer.get_expr_type(a), analyzer)


def _native_call_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                        locals_: dict[str, TpyType], analyzer, *,
                        storage_tuple_locals: 'AbstractSet[str]') -> bool:
    return (_shared_pass_through_arg(a, ptype, locals_, analyzer)
            # Callable args at a template callee (`builtin_filter<T>(
            # is_even, nums)`): a func-ref renders its bare C++ name and a
            # lambda its inline closure -- both loops emit them identically,
            # so the plain ladder's rows serve here too.
            or _func_ref_routable(a, analyzer)
            or _lambda_routable(a, analyzer)
            # ... and so does a Callable VALUE bound to a name (`map(h, xs)`
            # -> `::tpy::builtin_map<..>(h, ..)`): the std::function converts
            # implicitly into the template's Fn slot, bare on both loops.
            or _callable_value_pass_arg(a, locals_, analyzer)
            or _own_move_arg(a, ptype, locals_, analyzer)
            # witnessed at the lowering arm (call.native_own_scalar_lvalue)
            or _native_own_scalar_lvalue_arg(a, ptype, locals_, analyzer)
            or _native_iterable_container_arg(a, ptype, locals_)
            # A container ternary of declared names at a protocol slot
            # (`len(a if flag else b)`): the ifexpr container arm renders
            # the lvalue ternary, bound bare like the single-name row.
            or _container_ternary_arg(a, ptype, locals_, analyzer)
            or _native_iterable_call_arg(a, ptype, analyzer)
            or _native_iterable_range_arg(a, ptype)
            or _native_iterable_iterator_call_arg(a, ptype, analyzer)
            or _native_iterable_genexpr_arg(a, ptype)
            or _native_iterable_literal_arg(a, ptype, analyzer)
            or _native_value_call_arg(a, ptype, analyzer)
            or _native_container_call_arg(a, ptype, analyzer)
            or _native_record_call_arg(a, ptype, analyzer)
            # A bare-name conformer into a monomorphized protocol slot of a
            # native/template callee (`repr(p)` -> `::tpy::repr_of(p)`): the
            # native arg loop renders it bare (`protocol_slots=False`), no
            # adapter wrap, so only the no-temp bare row admits here.
            or _protocol_slot_arg(a, ptype, locals_, analyzer, temps_ok=False)
            # A value-typed arg (scalar / Char / str family) at a native
            # protocol slot (`hash("hello")` -> `::tpy::__hash__("hello")`):
            # the native loop renders the value bare, position-independent.
            or _native_protocol_value_arg(a, ptype, analyzer)
            # A union-typed NAME at the same slot (`repr(a)` over a
            # variant binding): bare, the runtime variant overload visits.
            or _native_union_name_arg(a, ptype, locals_, analyzer)
            or _native_protocol_tuple_literal_arg(
                a, ptype, analyzer) is not None
            or _native_protocol_field_arg(a, ptype, analyzer)
            or _native_iterable_comp_arg(a, ptype, analyzer) is not None
            # A storage-form tuple NAME at a native/template tuple slot takes
            # the same `tuple_to_pointer` lift as at a plain callee
            # (`str(t)` -> `::tpy::tuple_to_str(tuple_to_pointer<..>(t))`);
            # the lift wraps the read, so it is position-independent.
            or _borrow_tuple_storage_name_arg(
                a, ptype, locals_, storage_tuple_locals, analyzer) is not None
            or note_detail(_native_arg_reject(a, ptype, analyzer)))


def _readonly_container_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> 'TpyType | None':
    """An EMPTY container rvalue into a `readonly[list/dict/set]` slot: the
    const-ref slot binds the rvalue INLINE -- `f(std::vector<int32_t>())`
    for the hint-typed `list()` instantiation, `f(std::vector<int32_t>{})`
    for the `[]` literal (the typed empty spelling). Empty only: elements
    would take the mutable slots' temp machinery. Returns the container
    slot or None."""
    if not isinstance(ptype, ReadonlyType):
        return None
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if not (is_list(inner) or is_dict(inner) or is_set(inner)):
        return None
    if isinstance(a, TpyArrayLiteral) and not a.elements and is_list(inner):
        return inner
    if isinstance(a, TpySetLiteral) and not a.elements and is_set(inner):
        return inner
    if (isinstance(a, TpyDictLiteral) and not a.keys and is_dict(inner)):
        return inner
    if (isinstance(a, TpyCall) and a.call_type is not None
            and not a.args and not a.kwargs):
        ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a.call_type)))
        return inner if ct == inner else None
    return None


def _native_protocol_tuple_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                       analyzer) -> 'TupleType | None':
    """A tuple LITERAL at a native callee's protocol slot (`hash((1,
    Box(5)))` -> `::tpy::__hash__(std::tuple<int32_t, Box>{1, Box(5)})`).
    The slot is a monomorphized protocol, so it threads no target -- the
    literal spells its own sema type, and every element is captured BY
    VALUE there (the storage form), whatever the element family.

    Returns the storage TupleType, or None. `_lower_tuple_literal` owns the
    per-element admission from there, so an element shape it cannot render
    still falls the body back."""
    proto = _protocol_arg_slot(ptype)
    if proto is None or is_dyn_protocol(proto):
        return None
    if not isinstance(a, TpyTupleLiteral):
        return None
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if not isinstance(atu, TupleType):
        return None
    # The literal's own type can still carry unresolved element literals
    # (`tuple[IntLiteral(1), Box]`); the slot threads no target to resolve
    # them, so the spelling has to (`std::tuple<int32_t, Box>`).
    atu = resolve_int_literals(atu, analyzer.ctx.default_int_for_literal)
    if not isinstance(atu, TupleType):
        return None
    return atu if _witness("arg.native_protocol_tuple_literal") else None


def _native_protocol_value_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """The value-typed slice of a native callee's protocol slot: the arg's
    resolved type is an eligible scalar / Char / str value, whose bare
    render is position-independent (the ordinary lowering tail carries it).
    Gate row only -- an arg SHAPE the tail cannot route still falls the
    body back."""
    proto = _protocol_arg_slot(ptype)
    if proto is None or is_dyn_protocol(proto):
        return False
    # Unresolved scalar literals render their bare token (`__hash__(42)`,
    # `__hash__(3.14)`) -- the `_ru_elem_ok` leaf bounds (strict on both
    # ends BY DESIGN: INT32_MIN itself stays AST, like _ru_elem_ok).
    if isinstance(a, TpyIntLiteral):
        return (-(2 ** 31) < a.value < 2 ** 31
                and _witness("arg.native_protocol_value"))
    if isinstance(a, TpyFloatLiteral):
        return bool(math.isfinite(a.value)
                    and _witness("arg.native_protocol_value"))
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return bool(atu is not None
                and (_eligible_scalar(atu) or _eligible_char(atu)
                     or _resolved_str_value(atu, analyzer) is not None)
                and _witness("arg.native_protocol_value"))


def _native_union_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A union-typed NAME at a native/template callee's SAME-union slot
    (`repr(a)` -> `::tpy::repr_of(a)` -- the generic Representable slot
    substitutes to the arg's own union): renders bare, the runtime
    variant overloads visit the active alternative whatever the flavor
    (value or pointer variant; wide member class). A narrowed occurrence
    reads its member alias identically on both paths (the name arm's
    rename), so no narrow exclusion is needed."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not isinstance(pt, UnionType) or not isinstance(a, TpyName):
        return False
    dt = locals_.get(a.name)
    if dt is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    if u != pt:
        return False
    return bool((_eligible_value_union(u) is not None
                 or _eligible_ptr_union_wide(u, analyzer) is not None)
                and _witness("arg.native_union_name"))

def _lambda_routable(a: TpyExpr, analyzer, *,
                     self_this: bool = False) -> bool:
    """The lambda-expression shapes `_lower_lambda` reproduces byte-for-byte:
    a closure with a non-void, non-pointer-tuple return and param types in the
    families the body emit renders without seeding (value scalars / Char /
    enums / str / bytes / F1-record) -- `_gen_lambda`'s `(params) -> ret {
    return body; }` arm, in its by-reference (Fn template), by-value
    (Callable/std::function), and readonly-param (key-function -- const
    `_callable_param_cpp` spelling + `to_cpp_return_const` trailing) capture
    modes. A VOID body routes IFF it is a bare builtin-print call (the
    statement-body closure `{ std::cout << ...; }`); other void bodies stay
    on the AST path. The single source of truth for both the arg-admission
    gate and the lowering arm (they must agree, else the gate admits a
    shape lowering then rejects -- a needless fallback)."""
    if not isinstance(a, TpyLambda):
        return False
    # A captured `self` spells `this` in the capture list -- admitted only
    # where the caller confirmed the receiver IS that pointer (a plain method
    # or the simple-generator wrapper). A resumable coro's `__self` frame
    # field is a different receiver render.
    if "self" in a.captured_names and not self_this:
        return False
    rt = a.inferred_return_type
    if rt is None:
        return False
    if is_void_like_type(rt):
        b = a.body
        # A param/capture named `print` would shadow the builtin; kwargs
        # (sep/end/file) and star-unpack shapes stay AST.
        if not (isinstance(b, TpyCall) and isinstance(b.func, TpyName)
                and b.func_name == "print"
                and not b.kwargs and b.double_star_unpack is None
                and "print" not in a.param_names
                and "print" not in a.captured_names
                and _is_builtin_print(b, {}, analyzer)):
            return False
    else:
        ru = unwrap_readonly(rt)
        if isinstance(ru, TupleType) and ru.has_pointer_repr_element():
            return False
    for pt in a.inferred_param_types:
        if not isinstance(pt, TpyType):
            return False
        pu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
        if not (_eligible_scalar(pu) or _eligible_char(pu)
                or _eligible_enum(pu, analyzer) is not None
                or _resolved_str_value(pu, analyzer) is not None
                or _resolved_bytes_value(pu, analyzer) is not None
                or _f1_record(pu, analyzer)
                # A list param spells `std::vector<T>&` via the same
                # to_cpp_param helper; the body's own reads gate their
                # renders (an unroutable body still falls back whole).
                or is_list(pu)):
            return False
    return True

def _func_ref_routable(a: TpyExpr, analyzer) -> bool:
    """A named function used as a value (`apply(double, ...)` ->
    `apply(double_, ...)`; `apply(mod.f, ...)` -> the qualified spelling;
    `apply(identity, 42)` -> the generic `identity<int32_t>` -- the
    template-args suffix rides `type_to_cpp` on both paths, so any resolved
    targ spells identically): `_function_ref_name`'s plain and generic arms.
    The native / async-coro-factory wrappers stay on the AST path. Shared
    source of truth for the arg-admission gates and the name-lowering arm."""
    if not (isinstance(a, TpyName) and a.is_function_ref):
        return False
    fi = a.function_ref_info
    if fi is None:
        return False
    if a.function_ref_type_args:
        if not all(isinstance(t, TpyType) for t in a.function_ref_type_args):
            return False
    elif fi.type_params:
        # A generic ref with NO recorded targs has no C++ value spelling.
        return False
    return not (fi.is_async or fi.is_generator or fi.native_function
                or fi.linkage != FunctionLinkage.DEFAULT)

def _callable_value_pass_arg(a: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A bare name of Callable-value type (`std::function`) passed as a call
    arg into a callable slot (an `Fn` template param or another `Callable`):
    the AST renders it unchanged, the std::function converting implicitly. A
    func-ref name renders its own way (`_func_ref_routable`) and is excluded."""
    if not isinstance(a, TpyName) or a.is_function_ref:
        return False
    at = locals_.get(a.name)
    if at is None:
        at = analyzer.get_expr_type(a)
    return _callable_value(at)

def _callable_object_arg(a: TpyExpr, ptype: 'TpyType | None',
                         locals_: dict[str, TpyType], analyzer) -> bool:
    """A callable-object record name (a record with `__call__`) passed bare
    into a callable slot -- an `Fn` template param instantiated on the record.
    Sema has verified the record satisfies the callable contract, so a bare
    F1-record name into a `Callable`-typed slot is exactly this pass-through
    shape; the AST renders the name unchanged (no move/copy temp)."""
    if not isinstance(a, TpyName) or a.is_function_ref:
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if not isinstance(slot, CallableType):
        return False
    at = locals_.get(a.name)
    if at is None:
        at = analyzer.get_expr_type(a)
    return _f1_record(at, analyzer)

def _plain_call_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                       locals_: dict[str, TpyType], analyzer, *,
                       temps_ok: bool,
                       narrowed: 'set[str] | frozenset[str]',
                       self_this: bool = False) -> bool:
    return (_lambda_routable(a, analyzer, self_this=self_this)
            or _func_ref_routable(a, analyzer)
            or _callable_value_pass_arg(a, locals_, analyzer)
            # The FIELD twin of the callable-value name pass: a Callable
            # field read binds a callable slot bare (`apply(handler.cb, 10)`
            # -> `apply(handler.cb, 10)` -- the std::function member converts
            # implicitly, no temp on either path).
            or (isinstance(a, TpyFieldAccess)
                and _callable_value(analyzer.get_expr_type(a))
                and _field_receiver_ok(a, locals_, analyzer)
                and _witness("call.callable_field_arg"))
            or _callable_object_arg(a, ptype, locals_, analyzer)
            or _shared_pass_through_arg(a, ptype, locals_, analyzer)
            or (temps_ok and _value_union_temp_arg(
                a, ptype, locals_, narrowed, analyzer))
            or (temps_ok and _record_rvalue_temp_arg(
                a, ptype, locals_, analyzer, upcast_ok=True))
            or _own_move_arg(a, ptype, locals_, analyzer)
            or (temps_ok and _own_lvalue_arg(
                a, ptype, locals_, narrowed, analyzer))
            or (temps_ok and _container_literal_arg(a, ptype, analyzer))
            or (temps_ok and _ref_param_dictset_literal_arg(a, ptype, analyzer))
            or (temps_ok and _covariant_temp_arg(a, ptype, locals_, analyzer)
                is not None)
            or _optional_ptr_arg(a, ptype, locals_, analyzer,
                                 temps_ok=temps_ok)
            # A record name moved into an Optional[Own[T]] slot (bare
            # `std::move(name)`); the lowering enforces the move verdict.
            or _opt_own_record_name_arg(a, ptype, locals_, analyzer)
            is not None
            or _readonly_record_ctor_arg(a, ptype, locals_, analyzer)
            or _union_pass_through_arg(a, ptype, locals_, analyzer)
            or _required_protocol_union_arg(a, ptype, locals_, analyzer)
            or _union_member_lift_arg(a, ptype, locals_, analyzer)
            or _union_coerced_literal_arg(a, ptype, locals_, analyzer)
            # M4c wrapper-slot rows (the free-call twins of the qualcall
            # ladder's): a same-wrapper NAME passes bare; a member-typed
            # NAME hoists the typed temp (flush-gated).
            or _ru_wrapper_name_arg(a, ptype, locals_, narrowed, analyzer)
            or _ru_wrapper_borrow_call_arg(a, ptype, analyzer)
            or _ru_wrapper_field_arg(a, ptype, locals_, analyzer)
            or (temps_ok and _ru_wrapper_member_name_arg(
                a, ptype, locals_, narrowed) is not None)
            # The literal sibling of the row above: `show(42)` hoists
            # `Value __tmp_N = 42;` through the same create_typed branch.
            or (temps_ok and _ru_wrapper_scalar_literal_arg(
                a, ptype, analyzer) is not None)
            # ... and the member-CTOR-rvalue sibling: `eval_expr(Lit(42))`
            # hoists `Expr __tmp_N = Lit(...);` the same way.
            or (temps_ok and _ru_wrapper_member_rvalue_arg(
                a, ptype, analyzer) is not None)
            # ... and the Own[genrec]-returning CALL sibling:
            # `leaf_count(make_leaf())` hoists `Tree<T> __tmp_N = ...;`.
            or (temps_ok and _ru_wrapper_own_call_arg(
                a, ptype, analyzer) is not None)
            or _own_union_ctor_arg(a, ptype, locals_, analyzer)
            or _dyn_own_coro_factory_arg(a, ptype, analyzer) is not None
            or _dyn_own_handle_arg(a, ptype, locals_, analyzer) is not None
            or _dyn_own_conformer_arg(a, ptype, locals_, analyzer) is not None
            or _dyn_own_forward_call_arg(a, ptype, analyzer) is not None
            or _wide_opt_deref_name_arg(a, ptype, locals_, analyzer)
            or _none_value_opt_arg(a, ptype, analyzer) is not None
            or _value_opt_pass_through_arg(a, ptype, locals_,
                                           narrowed, analyzer)
            # The callable twin of the row above (`takes_opt(cb)` at a
            # `Callable[..] | None` slot) -- bare on both paths.
            or _value_opt_callable_pass_arg(a, ptype, locals_, narrowed,
                                            analyzer)
            or _protocol_slot_arg(a, ptype, locals_, analyzer,
                                  temps_ok=temps_ok)
            # A container/Span/record NAME at a NULLABLE static-protocol
            # slot (`count_if_sized(nums)` at `Sized | None`): the
            # address-of lift binds the monomorphized `const T_x*`
            # (`&(nums)` -- the shared 'addr' verdict, rendered by
            # _lower_call_arg's nullable-protocol arm).
            or (isinstance(a, (TpyName, TpyNoneLiteral))
                and _protocol_union_arg(a, ptype, locals_, analyzer)
                in ("addr", "nullproto")
                and _witness("arg.nullable_proto_addr"))
            # A tuple LITERAL at a tuple param slot: the borrow/value tuple
            # builders own the per-element admission (a bad element shape
            # raises inside lowering and falls the body back whole) -- the
            # gate checks only the slot/arity pairing.
            or _tuple_literal_arg(a, ptype)
            or _record_borrow_call_arg(a, ptype, analyzer)
            or _own_optional_record_rvalue_arg(a, ptype, analyzer)
            # ... and its NAME / same-Optional-call siblings at the
            # `Own[record | None]` slot (`take(a)` -> `std::move(a)`,
            # `take(make(13))` -> bare prvalue).
            or _own_opt_slot_arg(a, ptype, locals_, analyzer)
            # An Array-returning call rvalue at a matching Array slot binds
            # the std::array prvalue inline (a VALUE container).
            or _value_array_call_arg(a, ptype, analyzer)
            or _recursive_union_borrow_call_arg(a, ptype, analyzer)
            or _record_elem_subscript_arg(a, ptype, analyzer)
            or (temps_ok and _container_comp_arg(a, ptype))
            or _borrow_tuple_field_arg(a, ptype, analyzer) is not None
            or _borrow_tuple_subscript_arg(a, ptype, analyzer) is not None
            or _record_field_ref_arg(a, ptype, locals_, analyzer)
            # The deref auto-coercion name: inline Ptr deref_check, or the
            # slot-typed wrapper-`__deref__()` copy temp (flush-gated).
            or ((dc := _deref_coerce_arg(a, ptype, locals_, analyzer))
                is not None and (dc[0] == "inline" or temps_ok))
            # An empty container rvalue at a readonly slot binds inline.
            or _readonly_container_rvalue_arg(a, ptype, analyzer) is not None
            or note_detail(
                "call.arg_shape." + _type_family_tag(ptype, analyzer)))

def _record_borrow_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """A borrow-returning call arg at a record ref slot
    (`bump(find_first(pts))` / `bump(holder.get())` -- the T&-returning
    call binds the ref param directly, no temp): the call's F1-record
    result is a BORROW into caller-owned storage, so it renders inline.
    Rvalue-returning calls keep their ArgTemp row (a prvalue bound to a
    mutable ref slot needs the named temp)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if not _f1_record(slot, analyzer):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return (atu == slot and not is_rvalue_source(analyzer, a))


def _recursive_union_borrow_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                                     analyzer) -> bool:
    """The recursive-union twin of `_record_borrow_call_arg`: a borrow
    -returning call at a recursive-union WRAPPER slot (`show(v.inner.get())`
    -> `show(__v.inner.get())`). The wrapper is a struct bound `const Value&`
    exactly like a record ref slot, so the accessor's borrow result binds
    inline.

    Where the record row excludes rvalue results, this one cannot:
    `call_returns_cpp_ref` gives every union return value semantics, so a
    wrapper accessor reads as an rvalue no matter how it is spelled, and an
    rvalue binds the wrapper slot fine -- `to_cpp_param_type` renders a
    recursive-union param `const Value&` unconditionally (it dispatches on
    `needs_wrapper()` before any mutability test), so no mutable-ref slot of
    this family exists to exclude. If one ever does, this row needs a real
    mutability signal: `is_ref_param()` is NOT one here, since it answers
    `uses_pointer_repr()` first and so is always False for a wrapper."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if slot is None or recursive_union_alternatives(slot) is None:
        return False
    fi = getattr(a, "resolved_function_info", None)
    rt = fi.return_type if fi is not None else None
    if rt is not None and isinstance(
            unwrap_readonly(unwrap_send_sync(rt)), OwnType):
        # An Own-DECLARED return is a fresh by-value wrapper: the AST
        # hoists the argtemp (`Tree<T> __tmp_N = make_leaf();`), never
        # binds the prvalue inline -- the argtemp.ru_wrapper_call row
        # owns the genrec flavor; the non-generic flavor falls back.
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return atu == slot


def _container_comp_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A list/set/dict comprehension at a plain callee's concrete container
    ref slot (`accept_wide([x for x in items])`): the slot-typed
    `std::vector<int64_t> __tmp_N = ({ ... });` ArgTemp, its init the same
    stmt-expr the decl-init arm renders."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if slot is None:
        return False
    if isinstance(a, TpyListComprehension):
        return is_list(slot)
    if isinstance(a, TpySetComprehension):
        return is_set(slot)
    if isinstance(a, TpyDictComprehension):
        return is_dict(slot)
    return False


def _borrow_tuple_local_type(name: str, declared: dict[str, TpyType],
                             storage_tuple_locals: 'AbstractSet[str]'
                             ) -> 'TupleType | None':
    """The pointer-repr TupleType of a BORROW-form tuple local, or None.
    Borrow is the default form for a declared ptr-repr tuple name (params,
    btuple.decl literals, branch hoists); the storage registrations
    (`storage_tuple_locals`) carve out the owning locals."""
    t = declared.get(name)
    if not isinstance(t, TpyType):
        return None
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if (isinstance(bare, TupleType) and bare.has_pointer_repr_element()
            and name not in storage_tuple_locals):
        return bare
    return None


def _borrow_tuple_slot(ptype: 'TpyType | None', analyzer) -> 'TupleType | None':
    """The F3 borrow-tuple PARAM slot every row below shares -- a pointer-repr
    tuple whose elements are all F1-renderable, so storage and borrow form
    genuinely differ."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if not isinstance(slot, TupleType) or _f1_tuple(slot, analyzer) is None:
        return None
    return slot


def _borrow_tuple_arg(a: TpyExpr, ptype: 'TpyType | None', analyzer, *,
                      shape_ok, arg_type) -> 'TupleType | None':
    """The shared body of the borrow-tuple ARG rows: an argument whose own
    type EQUALS the F3 slot, so the only question left is which C++ form it
    already reads as. `shape_ok` picks the arg shapes a row claims, `arg_type`
    says where that shape's type comes from (the expression for an lvalue
    read, the locals table for a name). Adding a row means one thin front,
    not a fifth copy of this body."""
    slot = _borrow_tuple_slot(ptype, analyzer)
    if slot is None or not shape_ok(a):
        return None
    at = arg_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return slot if atu == slot else None


def _borrow_tuple_field_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> 'TupleType | None':
    """A storage F3-tuple FIELD read at a borrow-tuple param slot
    (`bump(h.pair)` -> `::tpy::tuple_to_pointer<std::tuple<int32_t,
    Box*>>(h.pair)`): the bare member read feeds the storage->borrow
    wrap. Returns the slot's TupleType, or None."""
    return _borrow_tuple_arg(
        a, ptype, analyzer,
        shape_ok=lambda x: isinstance(x, TpyFieldAccess),
        arg_type=analyzer.get_expr_type)


def _borrow_tuple_subscript_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> 'TupleType | None':
    """The container-element sibling of `_borrow_tuple_field_arg`: a storage
    tuple read out of a container at a borrow-tuple param slot
    (`consume(items[0])` -> `::tpy::tuple_to_pointer<std::tuple<const T*,
    const T*>>(::tpy::__getitem__(items, 0))`). The checked element read is
    the same storage lvalue the member read is, so it feeds the identical
    storage->borrow wrap. Slice subscripts are a different read entirely."""
    return _borrow_tuple_arg(
        a, ptype, analyzer,
        shape_ok=lambda x: (isinstance(x, TpySubscript)
                            and not isinstance(x.index, TpySlice)),
        arg_type=analyzer.get_expr_type)


def _borrow_tuple_storage_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   locals_: dict[str, TpyType],
                                   storage_tuple_locals: 'AbstractSet[str]',
                                   analyzer) -> 'TupleType | None':
    """The NAME sibling of the two lift rows above: a local ALREADY reading
    storage form (a loop var over a storage container, a local bound from
    another storage source) at a borrow-tuple param slot
    (`consume(it)` -> `::tpy::tuple_to_pointer<std::tuple<const T*, const
    T*>>(it)`).

    Keyed on `storage_tuple_locals` membership -- the positive fact the AST's
    `is_storage_form_source` reads for a Name. A borrow-form name is NOT in
    that set and rides `_borrow_tuple_name_arg`'s bare-bind row instead, so
    the two NAME rows partition on positive evidence, never on absence."""
    return _borrow_tuple_arg(
        a, ptype, analyzer,
        shape_ok=lambda x: (isinstance(x, TpyName)
                            and x.name in storage_tuple_locals),
        arg_type=lambda x: locals_.get(x.name))


def _required_protocol_union_slot(ptype: 'TpyType | None') -> bool:
    """A REQUIRED multi-protocol union param slot (`items: Sized |
    Sequence[int]`). `_gen_protocol_arg` claims it BEFORE the Optional-ptr
    and union-lift arms, and its required-union branch renders the plain
    `gen_expr_deref` value -- so this slot must never reach THIR's
    ptr-variant lift either, or the arg picks up a spurious `&(...)`.
    A NULLABLE protocol union has a None member, which is not a protocol,
    so it rejects here and keeps its own typed-null / address-of renders."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    return (isinstance(slot, UnionType) and len(slot.members) >= 2
            and all(isinstance(m, NominalType) and m.is_protocol
                    for m in slot.members))


def _required_protocol_union_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A bare NAME at a free call's REQUIRED multi-protocol union slot
    (`describe(nums)` -> `describe(nums)`): the slot monomorphizes to ONE
    template param (`const T_items&`), so the arg renders as the plain value
    -- no adapter wrap, no address-of."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    if not _required_protocol_union_slot(ptype):
        return False
    # A narrowed nullable-protocol param passed onward (`total(items)`
    # inside the guard): the pointer binding derefs at the value position
    # (`total((*items))` -- the name-read deref), then binds the required
    # slot's `const T&` like any plain value. The binding arrives either
    # as the RAW nullable type or GUARD-retyped to the protocols-only
    # union -- admit both spellings of the same param.
    if _nullable_static_protocol_param(locals_[a.name]) is not None:
        return bool(_witness("arg.required_protocol_union"))
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if _static_protocol_union_binding(at):
        return bool(_witness("arg.required_protocol_union"))
    return bool((_f1_record(at, analyzer) or is_span(at) or is_list(at)
                 or is_dict(at) or is_set(at))
                and _witness("arg.required_protocol_union"))


def _borrow_tuple_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType],
                           borrow_params: 'AbstractSet[str]',
                           analyzer) -> bool:
    """A ptr-repr tuple PARAM name -- already bound in BORROW form (`const
    std::tuple<T*, ..>&`) -- at the same borrow-tuple param slot: it renders
    BARE (`inner(p)`), no `tuple_to_pointer` lift.

    Keyed on POSITIVE evidence (the name is such a param) rather than on
    absence from `lc.storage_tuple_locals`. Absence proves borrow form only
    where that set is populated, and the resumable lane does not populate it
    (its owning tuples live in codegen's `storage_form_tuple_locals`).
    Reading absence as borrow form passed a storage source bare and dropped
    the lift -- a wrong-value render. Storage sources ride the lift rows
    instead.

    Lane-blind on purpose: a resumable's params are frame fields, but they
    reach this row through `lc.params` exactly like a plain function's and
    render byte-identically. The absence-keyed predecessor DID need a lane
    guard here; the positive key subsumes it, and the guard was suppressing
    a body that routes correctly."""
    ok = _borrow_tuple_arg(
        a, ptype, analyzer,
        shape_ok=lambda x: (isinstance(x, TpyName)
                            and x.name in borrow_params),
        arg_type=lambda x: locals_.get(x.name)) is not None
    return ok and _witness("arg.btuple_name")


def _record_getitem_rvalue_arg(a: TpyExpr, analyzer) -> bool:
    """A user-record `recv[index]` returning an F1 record: the raw
    operator[] read (a `T&` borrow for the default record return, a
    prvalue for an explicit value return) binds the protocol arg's
    `auto __tmp_N = container[i];` temp -- `auto` deduces the value
    either way, so the temp owns a copy exactly as the AST emits."""
    if not isinstance(a, TpySubscript) or isinstance(a.index, TpySlice):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if not _f1_record(atu, analyzer):
        return False
    rt_recv = analyzer.get_expr_type(a.obj)
    ru = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt_recv)))
          if rt_recv is not None else None)
    if not isinstance(ru, NominalType):
        return False
    ri = analyzer.registry.get_record_for_type(ru)
    if ri is None:
        return False
    return bool(analyzer.registry.get_method_overloads_with_parents(
        ri, "__getitem__"))


def _record_elem_subscript_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A record-element container subscript at a record ref slot
    (`add_a(a.bs[0], ..)` -> `add_a(::tpy::__getitem__(a.bs, 0), ..)`):
    the checked element read is an lvalue into the container's storage,
    binding the ref param inline like the borrow-returning call row."""
    if not isinstance(a, TpySubscript) or isinstance(a.index, TpySlice):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if not _f1_record(slot, analyzer):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return atu == slot


def _tuple_literal_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    if not isinstance(a, TpyTupleLiteral):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if isinstance(slot, OwnType):
        # An `Own[value-tuple]` slot takes the same spelled value render as
        # the bare tuple slot (the tuple-literal lowering arm's unwrap);
        # pointer-repr storage slots keep rejecting there.
        inner = unwrap_readonly(slot.wrapped)
        if (isinstance(inner, TupleType)
                and not inner.has_pointer_repr_element()):
            slot = inner
    return (isinstance(slot, TupleType)
            and len(a.elements) == len(slot.element_types))

def _call_ret_reject(e: TpyCall, ret: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a call result the value-position set does not
    admit -- splits call.ret_type by the return's type family so the tally
    ranks which result rung to open next (one bucket routinely hides
    several disjoint frontiers). Records split by value category: a borrow
    (`T&`) return is the REF_ALIAS decl frontier, an rvalue one the
    owned-record arm's residue (position/args/prescan rejects)."""
    if ret is None:
        return "call.ret_type.unresolved"
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t.wrapped)))
    if is_void_like_type(t):
        return "call.ret_type.void"
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t.inner)))
        return ("call.ret_type.optional_record"
                if _f1_record(inner, analyzer)
                else "call.ret_type.optional_other")
    if isinstance(t, UnionType):
        return ("call.ret_type.union_value"
                if _eligible_value_union(t) is not None
                else "call.ret_type.union_ptr")
    if isinstance(t, TupleType):
        return "call.ret_type.tuple"
    if is_list(t) or is_dict(t) or is_set(t):
        return "call.ret_type.container"
    if isinstance(t, NominalType):
        if not _f1_record(t, analyzer):
            return "call.ret_type.record_other"
        return ("call.ret_type.record_rvalue"
                if is_rvalue_source(analyzer, e)
                else "call.ret_type.record_borrow")
    return "call.ret_type.other"

def _native_arg_reject(a: TpyExpr, ptype: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a native/template callee arg that fails the shared
    pass-through set -- names WHICH plain-loop-only arg row it needs so the
    fallback tally ranks the native_arg_shape mass by shape (optptr / union /
    own / record-rvalue / other) rather than one opaque bucket."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
         if ptype is not None else None)
    if isinstance(t, OptionalType):
        return "call.native_arg.optptr"
    if isinstance(t, UnionType):
        return "call.native_arg.union"
    if isinstance(t, OwnType):
        return "call.native_arg.own"
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return "call.native_arg.call_rvalue"
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if is_list(at) or is_dict(at) or is_set(at):
        # Containers are NominalType too, so they must be split off before the
        # record branch below -- labelling them "record" sent a whole family
        # (non-name reads into a Sized/Iterable slot) to the wrong frontier.
        return "call.native_arg.container"
    if isinstance(at, NominalType):
        # Split F1 vs non-F1: an F1 record name here means a slot mismatch
        # (readonly/Own/unrelated slot); a non-F1 record is blocked on the
        # non-F1-record frontier (the shared elephant with receiver.field_nonf1
        # and sig.receiver_record).
        return ("call.native_arg.record_nonf1"
                if not _f1_record(at, analyzer)
                else "call.native_arg.record_f1_slot")
    return "call.native_arg.other"


def _generic_plain_arg_ok(a: TpyExpr, ptype: 'TpyType | None', subst,
                          locals_: dict[str, TpyType], analyzer, *,
                          temps_ok: bool,
                          narrowed: 'set[str] | frozenset[str]') -> bool:
    if ptype is None:
        return note_detail("call.generic_arg_slot")
    resolved = substitute_type_params_simple(ptype, subst)
    if contains_type_param(resolved):
        # A nested generic call inside a generic body substitutes the
        # caller's own T (`return inner_len<T>(x);`): a NAME whose binding
        # is that same bare T passes the form-neutral slot bare.
        if (isinstance(ptype, TypeParamRef) and isinstance(a, TpyName)
                and a.name != "self"):
            at = locals_.get(a.name)
            if at is not None:
                au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
                if (isinstance(au, TypeParamRef)
                        and isinstance(resolved, TypeParamRef)
                        and au.name == resolved.name):
                    return True
        # An `Own[T]` slot fed by the caller's own `Own[T]`-declared param
        # NAME (`sink(x)` forwarding inside a generic body -- the oracle's
        # `sink<T>(std::move(x))`): `_own_lvalue_temp_slot`'s type-param
        # row keys the same-T pairing, and the movable last-use facts
        # apply inside template bodies exactly like the concrete Own slot.
        if _own_move_arg(a, ptype, locals_, analyzer):
            return True
        # The COMPOSITE sibling of the bare-T row above: a NAME whose binding
        # is exactly the still-unsubstituted slot (`first(items)` at
        # `list[T]` -> `first<T>(items)`, `poll_once(aw)` at `Awaitable[T]`
        # -> `poll_once<T>(aw)`). Both spell an lvalue-ref template param, so
        # the name binds bare. Own slots are excluded above deliberately --
        # they move rather than bind, and that row already ran.
        if (isinstance(a, TpyName) and a.name != "self"
                and not isinstance(resolved, OwnType)):
            at = locals_.get(a.name)
            if at is not None:
                au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
                if au == resolved and _witness("call.generic_open_slot_name"):
                    return True
                # A conformer NAME into a still-open SINGLE structural
                # protocol slot (`drive_implicit(t)` at `Awaitable[T]`, t:
                # MyTask[T]): the monomorphized template param binds the
                # lvalue bare -- only temporaries hoist on the AST path
                # (is_temporary_expr), and a NAME is never one. @dynamic
                # slots keep their adapter temps on the AST path.
                if (a.name not in narrowed
                        and isinstance(resolved, NominalType)
                        and resolved.is_protocol
                        and not is_dyn_protocol(resolved)
                        and isinstance(au, NominalType)
                        and au.is_user_record and not au.is_protocol
                        and _witness("call.generic_open_proto_name")):
                    return True
        return note_detail("call.generic_arg_slot")
    if isinstance(ptype, TypeParamRef):
        if _eligible_scalar(resolved):
            lit = _peel_coerce(a)
            if isinstance(lit, (TpyIntLiteral, TpyFloatLiteral,
                                TpyBoolLiteral)):
                return temps_ok or note_detail("call.generic_arg_shape")
            if isinstance(a, TpyName):
                return ((a.name != "self" and a.name in locals_
                         and _resolved_scalar(locals_.get(a.name), analyzer))
                        or note_detail("call.generic_arg_shape"))
            # A scalar-typed call rvalue (`pair(Float64(2.5), x)`) hoists
            # the same ref-slot temp; the scalar-ctor arm folds the render.
            if (isinstance(lit, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, lit)
                    and _resolved_scalar(analyzer.get_expr_type(lit),
                                         analyzer)):
                return temps_ok or note_detail("call.generic_arg_shape")
            return note_detail("call.generic_arg_shape")
        # A non-scalar-resolved T slot is an lvalue-ref binding
        # (`param_val_or_ref_t<T>`): a record / container NAME lvalue binds
        # bare, exactly like the same name into the concrete `const R&`
        # slot.
        if isinstance(a, TpyName) and (
                _record_pass_through_arg(a, resolved, locals_, analyzer)
                or _container_pass_through_arg(a, resolved, locals_,
                                               analyzer)
                # A value-tuple NAME binds the ref slot bare exactly like a
                # record / container name -- the row the concrete-slot tail
                # below already reaches through `_shared_pass_through_arg`.
                or _value_tuple_pass_through_arg(a, resolved, locals_,
                                                 analyzer)
                # A str-slice NAME into a str-resolved T slot passes bare
                # (`get_length<std::string_view>(msg)`); the pending-aware
                # classifier resolves an inference-pending slot form.
                or (_resolved_str_value(resolved, analyzer) is not None
                    and _resolved_str_value(analyzer.get_expr_type(a),
                                            analyzer) is not None)):
            return True
        # Rvalue sources hoist the AST's ref-slot temp
        # (`R __tmp_N = <init>;`, TempState.create over resolved.to_cpp()):
        # a str literal / by-value str call into a str-resolved slot, and
        # `None` into a `std::monostate` slot -- flush-gated like the
        # scalar-literal row. Record rvalues stay out (the AST's covariant
        # upcast declares the temp with the CHILD type -- unmirrored).
        lit = _peel_coerce(a)
        if _resolved_str_value(resolved, analyzer) is not None and (
                isinstance(lit, TpyStrLiteral)
                or (isinstance(lit, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, lit))):
            return temps_ok or note_detail("call.generic_arg_shape")
        if isinstance(resolved, NoneType) and isinstance(lit, TpyNoneLiteral):
            return temps_ok or note_detail("call.generic_arg_shape")
        # A list literal into a container-resolved T slot (`use([1, 2])` ->
        # `std::vector<int32_t> __tmp_2 = {1, 2};`): the ref-slot temp with
        # the decl sink's target-typed brace init.
        if (isinstance(lit, TpyArrayLiteral) and is_list(resolved)
                and _container_literal_shape_ok(lit, resolved, analyzer)):
            return temps_ok or note_detail("call.generic_arg_shape")
        # An F1-record rvalue whose type is EXACTLY the resolved slot
        # (`take[IntBox](IntBox(42))` -> `IntBox __tmp_1 = IntBox(42);`):
        # the same ref-slot temp the str/call rows hoist. A COVARIANT
        # upcast stays out -- the AST declares that temp with the CHILD
        # type, a render this row does not reproduce.
        if (isinstance(lit, (TpyCall, TpyMethodCall))
                and _f1_record(resolved, analyzer)
                and is_rvalue_source(analyzer, lit)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(lit)))) == resolved):
            return temps_ok or note_detail("call.generic_arg_shape")
        return note_detail("call.generic_arg_slot")
    return (_shared_pass_through_arg(a, resolved, locals_, analyzer)
            # The callable family into an `Fn[...]` / `Callable` slot
            # (`reduce(add, xs, 0)`, `apply(f1, 5)`, a lambda literal): each
            # renders itself, independent of the slot, so the plain
            # free-call rows carry over to the substituted slot unchanged.
            or _lambda_routable(a, analyzer)
            or _func_ref_routable(a, analyzer)
            or _callable_value_pass_arg(a, locals_, analyzer)
            or _callable_object_arg(a, resolved, locals_, analyzer)
            or _own_move_arg(a, resolved, locals_, analyzer)
            # The Own-slot copy+move row (`auto __tmp_N = v;` +
            # `std::move(__tmp_N)`, or the temp-free last-use move):
            # `_lower_call_arg` owns both renders against the substituted
            # slot, exactly like the concrete free-call path.
            or (temps_ok and _own_lvalue_arg(
                a, resolved, locals_, narrowed, analyzer))
            # The `Own[@dynamic P]` erasure rows on a SUBSTITUTED slot
            # (`task_from_coro(yield_once())` at `Own[Cancellable[T]]`):
            # the same make_adapter / make_unique wraps the concrete
            # free-call gate admits -- the lowering arms are slot-keyed
            # and generic-blind.
            or _dyn_own_coro_factory_arg(a, resolved, analyzer) is not None
            or _dyn_own_conformer_arg(a, resolved, locals_, analyzer)
            is not None
            # `None` into a substituted unit slot (`poll_ready[None](None)`
            # at `Own[T]` resolved `Own[None]`): the bare `std::monostate{}`
            # render, position-independent.
            or _none_unit_arg(a, resolved) is not None
            # A tuple literal into a substituted `Own[value-tuple]` slot
            # (`heappush(pq, (3, "third"))`): the spelled value render the
            # tuple-literal lowering arm gives the Own-unwrapped slot.
            or _tuple_literal_arg(a, resolved)
            # A str literal into a substituted `Own[str]` slot
            # (`heappush(words, "cherry")`): binds the owned param bare --
            # `_str_owned_slot_arg`'s literal face; the other faces
            # (view-form locals, subscripts) stay unwitnessed here.
            or (isinstance(_peel_coerce(a), TpyStrLiteral)
                and (w_str := _plain_own_slot(resolved)) is not None
                and is_str_type(w_str))
            # A protocol slot in the substituted param list
            # (`poll_once(f())` -- `Awaitable[T]`): the same pre-arm the
            # concrete free-call loop runs (protocol_slots=True there and
            # in the generic lowering alike).
            or _protocol_slot_arg(a, resolved, locals_, analyzer,
                                  temps_ok=temps_ok)
            # The M4c wrapper-slot rows against the SUBSTITUTED slot
            # (`leaf_count(t)` at `Tree[T]` resolved `Tree[int]`): a
            # same-wrapper NAME binds bare; a member-typed NAME / literal /
            # member-ctor rvalue hoists the typed temp -- the same rows the
            # concrete ladders run, slot-keyed through `_ru_wrapper_arg_slot`
            # (the `_wrapper_union_like` accessor).
            or _ru_wrapper_name_arg(a, resolved, locals_, narrowed, analyzer)
            or _ru_wrapper_borrow_call_arg(a, resolved, analyzer)
            or _ru_wrapper_field_arg(a, resolved, locals_, analyzer)
            or (temps_ok and _ru_wrapper_member_name_arg(
                a, resolved, locals_, narrowed) is not None)
            or (temps_ok and _ru_wrapper_scalar_literal_arg(
                a, resolved, analyzer) is not None)
            or (temps_ok and _ru_wrapper_member_rvalue_arg(
                a, resolved, analyzer) is not None)
            # A list literal into a SUBSTITUTED container slot
            # (`doubled([1, 2, 3])` at `list[T]` -> `std::vector<int32_t>
            # __tmp_1 = {1, 2, 3};`): the generic callee's `std::vector<T>&`
            # param is the same non-const ref the concrete free-call row
            # hoists a named temp for, so it takes that row's admission.
            or (temps_ok and isinstance(_peel_coerce(a), TpyArrayLiteral)
                and is_list(resolved)
                and _container_literal_arg(_peel_coerce(a), resolved,
                                           analyzer))
            or note_detail("call.generic_arg_shape"))

def _container_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                           analyzer) -> bool:
    """A LIST literal into a same-family NON-mutated list slot at a CTOR call
    (`Numbers([1, 2, 3])` -> `Numbers({1, 2, 3});`): the AST renders the bare
    brace-init in place (probe-verified for scalar, str, and record-rvalue
    elements), which `_lower_expr`'s container-literal arm reproduces. CTOR
    args only -- a FREE-call literal arg hoists the ref-param `__tmp_N` temp
    on the AST path (probe-verified for const AND mutated slots). Dict / set
    literals take the SPELLED render (`::tpy::ordered_map<...>({{..}})`)
    rather than the list arm's bare brace, but the ctor position emits them
    INLINE like the stub-method twin (`_container_literal_method_arg`) --
    oracle `Config(::tpy::ordered_map<std::string, ::tpy::Any>({{..}}))`, no
    temp hoist -- so they ride this arm too. Array literals still do not.
    A MUTATED ctor slot rejects (a prvalue into a
    non-const `T&`). Element shapes are pre-checked so admission tracks
    lowerability; the make_vector element path is rejected at lowering (its
    ctor-arg render is unverified). A DECLARED-readonly slot binds the
    literal INLINE on the AST path (dualgen-verified `take_ro({1, 2, 3})`),
    so it must not ride the temp arms -- only the empty rvalue is mirrored
    (`_readonly_container_rvalue_arg`); non-empty stays AST."""
    if isinstance(ptype, TpyType) and isinstance(
            unwrap_ref_type(unwrap_send_sync(ptype)), ReadonlyType):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    if isinstance(a, TpyArrayLiteral):
        return is_list(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpyDictLiteral):
        return is_dict(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpySetLiteral):
        return is_set(pt) and _container_literal_shape_ok(a, pt, analyzer)
    return False

def _container_literal_method_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  analyzer) -> bool:
    """A dict / set / list literal into a matching builtin-container slot of a
    stub (builtin-container) METHOD call (`d.update({...})` ->
    `::tpy::dict_update(d, ::tpy::ordered_map<...>({{..}}))`): the AST's
    `_args()` loop threads the slot type and renders the spelled container in
    place, which `_lower_expr`'s container-literal arm reproduces. Unlike the
    ctor-arg row (`_container_literal_arg`, list-only bare brace) this admits
    the dict / set spelled renders too, because the stub method arg loop emits
    them inline (no ref-param `__tmp_N` hoist)."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    if isinstance(a, TpyDictLiteral):
        return is_dict(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpySetLiteral):
        return is_set(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpyArrayLiteral):
        return is_list(pt) and _container_literal_shape_ok(a, pt, analyzer)
    return False

def _ref_param_dictset_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> bool:
    """A dict / set literal into a matching container slot of a PLAIN free
    call: the AST hoists the ref-param `__tmp_N` temp with the spelled
    container init (`::tpy::ordered_map<...> __tmp_N = ::tpy::ordered_map<...>
    ({{..}});` -- gen_call_arg renders the literal target-typed, then the
    ref-param cascade hoists it). Flush positions only (the arm is
    temps_ok-gated); the list-literal sibling rides `_container_literal_arg`.
    DECLARED-readonly slots bind inline on the AST path -- excluded like the
    list sibling."""
    if isinstance(ptype, TpyType) and isinstance(
            unwrap_ref_type(unwrap_send_sync(ptype)), ReadonlyType):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    if isinstance(a, TpyDictLiteral):
        return is_dict(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpySetLiteral):
        return is_set(pt) and _container_literal_shape_ok(a, pt, analyzer)
    if isinstance(a, TpyArrayLiteral):
        # A list literal into an `Array[T, N]` slot (compile-time-sized
        # coercion): the same ref-param hoist, bare-brace init
        # (`std::array<int32_t, 3> __tmp_N = {10, 20, 30};`). The list-slot
        # literal rides `_container_literal_arg` -- keep the rows disjoint.
        return is_array(pt) and _container_literal_shape_ok(a, pt, analyzer)
    return False

def _own_literal_family(t: 'TpyType | None') -> bool:
    """The container families whose LITERAL renders identically off the slot
    and off its own resolved type -- the coincidence `_own_container_literal_arg`
    rests on."""
    return t is not None and (is_list(t) or is_array(t) or is_dict(t)
                              or is_set(t))


def _own_container_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A container LITERAL into an `Own[container]` slot
    (`Mutex.new([1, 2])` -> `new_({1, 2})`, `ds.append({3: 4})` ->
    `push_back(::tpy::ordered_map<int32_t, int32_t>({{3, 4}}))`).
    NOTE: `_lower_call_arg` has no dedicated render arm for this row (the
    explicit literal arms exclude Own slots); the literal falls to the
    generic tail and renders off its OWN resolved type, which is why the
    shape check below pins the literal's family to the peeled slot's --
    the two renders coincide only while they match."""
    if not isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if not isinstance(pt, OwnType):
        return False
    inner = unwrap_readonly(unwrap_send_sync(pt.wrapped))
    # A stub slot off a literal-seeded receiver can still be PENDING
    # (`rows.append([9, 9])` -- Own[PendingList], which resolves to the
    # read-only DEMOTED Array); resolve first. The bare brace renders the
    # same for the list and the demoted-Array spellings.
    inner = _resolve_literal_seeded(inner, analyzer)
    if _is_type_param_slot(inner):
        # A RAW `Own[T]` element slot (a builtin stub's unsubstituted T):
        # the bare brace renders off the literal's own resolved container,
        # so gate on that shape instead.
        ltu = _resolve_literal_seeded(analyzer.get_expr_type(a), analyzer)
        ltu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltu)))
               if ltu is not None else None)
        return (ltu is not None and _own_literal_family(ltu)
                and _container_literal_shape_ok(a, ltu, analyzer))
    return (_own_literal_family(inner)
            and _container_literal_shape_ok(a, inner, analyzer))

def _own_container_instantiation_arg(a: TpyExpr, ptype: 'TpyType | None',
                                     analyzer) -> bool:
    """An EMPTY container INSTANTIATION into an `Own[container]` ctor slot
    (the @dataclass default_factory fill: `Foo(x=Int32(1))` ->
    `Foo(std::vector<int32_t>(), 1)`): the spelled default ctor renders off
    `call_type` (the instantiation-empty arm), and the exact-type check
    keeps the render aligned with the slot."""
    if not (isinstance(a, TpyCall) and a.call_type is not None
            and not a.args and not a.kwargs
            and a.double_star_unpack is None
            and a.subscript_callee is None):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if not isinstance(pt, OwnType):
        return False
    inner = unwrap_readonly(unwrap_send_sync(pt.wrapped))
    if not (is_list(inner) or is_dict(inner) or is_set(inner)):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    return atu == inner and _witness("ctor.own_container_instantiation")

def _native_iterable_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 analyzer) -> bool:
    """A container LITERAL into a NATIVE builtin's structural `Iterable[T]` /
    `Sequence[T]` slot (`bytes([300])` / `all([True, False])` ->
    `::tpy::bytes_from_int_iterable(std::array<int32_t, 1>{300})`): the
    literal's RESOLVED container renders inline, bare into the template slot
    (probe-verified: array aggregates incl. BigInt / str / expr elements, and
    the spelled `::tpy::ordered_set<T>({..})`) -- the literal twin of
    `_native_iterable_container_arg`'s bare-name row. The shape check keys on
    the resolved type (sema's PendingListType decision, final before
    lowering); a make_container element rejects at the lowering arm.
    Lives here rather than beside its `_native_iterable_*` siblings in
    predicates.py because `_container_literal_shape_ok` is checks.py-local
    (predicates cannot import checks)."""
    if not isinstance(a, (TpyArrayLiteral, TpySetLiteral)):
        return False
    pb = _protocol_binding(ptype)
    if pb is None or pb.name not in ("Iterable", "Sequence"):
        return False
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    return _container_literal_shape_ok(a, at, analyzer)

def _native_iterable_comp_arg(a: TpyExpr, ptype: 'TpyType | None',
                              analyzer) -> 'TpyType | None':
    """A container COMPREHENSION into a NATIVE builtin's structural
    `Iterable[T]` / `Sequence[T]` slot (`sorted([n for n in os.listdir(p)])`
    -> `::tpy::builtin_sorted<std::string>(({ ... }))`): the stmt-expr
    renders INLINE into the template slot, target-typed by the
    comprehension's OWN resolved container -- not by the protocol slot, and
    with no `__tmp_N` (the named temp is the plain ladder's
    `_container_comp_arg` row, whose concrete container slot needs one).
    Like the genexpr row this admits broadly; an element shape
    `_lower_comprehension` cannot render raises and falls the body back.
    Returns the comprehension's container type, or None."""
    if not isinstance(a, (TpyListComprehension, TpySetComprehension,
                          TpyDictComprehension)):
        return None
    pb = _protocol_binding(ptype)
    if pb is None or pb.name not in ("Iterable", "Sequence"):
        return None
    at = analyzer.get_expr_type(a)
    if at is None:
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    return at if (is_list(at) or is_set(at) or is_dict(at)) else None


def _record_field_ref_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """An F1-record FIELD read into a record ref slot (`pass_both(h.a,
    h.b)`): the bare member read binds the `T&`/`const T&` param, aliasing
    the caller's object (gen_call_arg renders gen_expr's plain field
    access). Markers-clean fields off routed receivers only; exact
    slot/field type match (the subclass-upcast lvalue bind stays deferred
    until witnessed)."""
    if not isinstance(a, TpyFieldAccess):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if slot is None or not _f1_record(slot, analyzer):
        return False
    at = analyzer.get_expr_type(a)
    atb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if atb != slot:
        return False
    return (_field_markers_clean(a)
            and _field_receiver_ok(a, locals_, analyzer))


def _own_iter_special_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """An explicit `own_iter(x)` call into a structural Iterable/Sequence
    slot (`b.extend(own_iter(a))` ->
    `::tpy::list_extend(b, ::tpy::own_iter(std::move(a)))`): the
    special-builtin lowering arm renders it; the row only admits the
    pairing."""
    if not isinstance(a, TpyCall):
        return False
    pb = _protocol_binding(ptype)
    if pb is None or pb.name not in ("Iterable", "Sequence"):
        return False
    fi = a.resolved_function_info
    return fi is not None and fi.qualified_name == qnames.OWN_ITER

def _native_container_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A container-returning CALL rvalue into a native/template slot that is
    NOT the structural Iterable face (`len(g.get())` ->
    `::tpy::__len__(g.get())`, `sorted(...)` feeding a Sized-ish slot): the
    call renders bare in place on both paths, exactly like the value-family
    row; `_lower_free_call_arg` threads STORAGE use so the inner call's
    result gate admits the container. Own / Optional / Union slots keep
    their lift arms (excluded)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType, UnionType)):
        return False
    rt = analyzer.get_expr_type(a)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if isinstance(rtu, OwnType):
        rtu = unwrap_readonly(rtu.wrapped)
    return _storage_call_ret(rtu, analyzer) is not None

def _native_record_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """An F1-record CALL rvalue into a native/template slot (`len(NegBig())`
    -> `::tpy::__len__(NegBig())`, `repr(datetime.strptime(s, f))`): the
    prvalue binds the const-ref/template slot for the call and renders bare
    in place on both paths -- the record sibling of `_native_value_call_arg`.
    The ctor shape is pinned here; a free/marker call's own lowering
    re-validates callee and args recursively. Own / Optional / Union slots
    keep their lift arms (excluded)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType, UnionType)):
        return False
    rt = analyzer.get_expr_type(a)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if not (_f1_record(rtu, analyzer) and is_rvalue_source(analyzer, a)):
        return False
    return (_record_call_rvalue_shape_ok(a, analyzer)
            and _witness("call.native_record_arg"))

def _native_value_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                           analyzer) -> bool:
    """A value-family CALL rvalue into a native/template slot
    (`len(v.strip())` -> `::tpy::__len__(::tpy::bytes_strip_view(v))`):
    the call renders bare in place on both paths; its own value-position
    lowering re-validates the callee and args, so an unroutable inner
    falls the body back. Own / Optional / Union slots keep their lift
    arms (excluded)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType, UnionType)):
        return False
    rt = analyzer.get_expr_type(a)
    return (_resolved_scalar(rt, analyzer)
            or _eligible_char(rt)
            or _resolved_str_value(rt, analyzer) is not None
            or _resolved_bytes_value(rt, analyzer) is not None)


def _shared_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer,
                             *, mutated: bool = False) -> bool:
    """The arg rows whose render lives inside gen_call_arg itself --
    independent of the plain loop's pre-arms and of the dcbp/pin kwargs
    the builtins loop does not thread -- shared by the plain AND
    native/template arg loops. Their slot domains are disjoint from the
    plain-loop-only rows (arg-temps / optional-ptr / union / readonly-ctor),
    so hoisting them ahead of those rows never changes admission. A new
    arg row belongs here iff a native/template callee renders it
    identically; otherwise it goes in the plain loop only.

    `mutated` marks a MUTATED ctor slot (`T&`, non-const ref): a temp /
    prvalue source binds it ill-formed (the mutated-String-param AST
    miscompile, BUGS.md), so the temp-producing rows gate off -- the
    str->String coerce half, the opt-str shim, the bytes-literal pin, and
    the value-tuple literal. The by-value rows (scalars / float / BigInt
    literal / char / enum / Ptr / slice-rvalue / Own rvalues -- mutation
    is callee-local, the slot stays by value) and the lvalue-NAME rows
    (record / container / owned-String names bind a `T&` legally; sema's
    readonly system rejects a const violation upstream) stay admitted."""
    # _resolved_scalar, not _eligible_scalar: a ctor-position literal arg
    # (`tpy.Int32(10)`) keeps IntLiteralType on its expr type -- the slot
    # threads the render either way (_slot_literal_retype).
    return ((_resolved_scalar(analyzer.get_expr_type(a), analyzer)
             and not _member_valued_union_slot(a, ptype, analyzer)
             and not _own_cascade_fires(ptype))
            # A scalar-based `Literal[...]` SLOT (spelled as its base):
            # sema leaves the arg's literal type unresolved there, so the
            # resolved-scalar check applies -- slot-keyed on LiteralType,
            # a domain no other row admits, so admission stays disjoint.
            or (_literal_scalar_slot(ptype)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _witness("arg.literal_scalar_slot"))
            or _own_scalar_rvalue_arg(a, ptype, locals_, analyzer)
            or _own_record_rvalue_arg(a, ptype, locals_, analyzer)
            or _float_literal_pass_through_arg(a, ptype, locals_, analyzer)
            or _int_literal_bigint_arg(a, ptype, locals_, analyzer)
            or _str_pass_through_arg(a, ptype, locals_, analyzer,
                                     mutated=mutated)
            or (not mutated
                and _opt_str_shim_arg(a, ptype, locals_, analyzer))
            or (not mutated
                and _bytes_pass_through_arg(a, ptype, locals_, analyzer))
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _container_pass_through_arg(a, ptype, locals_, analyzer)
            or _span_coerce_arg(a, ptype, locals_, analyzer)
            or _slice_ctor_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _ptr_pass_through_arg(a, ptype, locals_, analyzer)
            or _value_tuple_pass_through_arg(a, ptype, locals_, analyzer,
                                             mutated=mutated)
            or _str_literal_literal_slot_arg(a, ptype, analyzer)
            or _any_pass_through_arg(a, ptype, locals_, analyzer)
            or _record_pass_through_arg(a, ptype, locals_, analyzer))

def _any_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """An `Any` value into an `Any` param slot (`extract(a)`, `s.add(a)`):
    `tpy::Any` is a value type, so a bare in-scope Any name / routable Any read
    binds the slot directly -- bare on both paths, no last-use move (value types
    are never move sources at call args). An `Own[Any]` slot (a container
    insert's move-in param) still renders bare: `_own_lvalue_temp_slot` returns
    None for a value payload, so the copy+move temp never fires."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    if not isinstance(pt, AnyType):
        return False
    at = analyzer.get_expr_type(a)
    return isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))),
                      AnyType)

def _str_literal_literal_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  analyzer) -> bool:
    """A str literal into a `Literal[str, ...]` selector slot (open's `mode`
    param, the literal-specialized overload's argument). The slot spells
    `std::string_view`, so the literal binds as the bare const char[N] --
    gen_call_arg's gen_expr_deref of a str literal at a LiteralType target
    (the overloaded pin is inert here: is_str_type(LiteralType) is False).
    Value-blind and identical on both call paths."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    return (isinstance(pt, LiteralType) and is_str_type(pt.base_type)
            and isinstance(_peel_coerce(a), TpyStrLiteral))

def _value_tuple_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer, *, mutated: bool = False) -> bool:
    """A value-tuple arg into a value-tuple slot (`const std::tuple<...>&`):
    a bare in-scope name of the same value-tuple family (an lvalue binding
    the ref slot directly -- bare on both paths; tuples are value types, so
    no move/temp cascade fires) or a tuple literal (the spelled brace-init
    render, target-threaded per element by gen_call_arg -- identical for
    plain and native/template callees, so the row is shared). An
    `Own[tuple]` slot is outside `_value_tuple` (the Own wrapper is not a
    TupleType), so the move cascade never reaches this row. A MUTATED slot
    (an address-escaped tuple param can drop the const) keeps the lvalue
    name and rejects the literal (a prvalue into a non-const ref)."""
    vt = _value_tuple(ptype, analyzer)
    if vt is None:
        return False
    if isinstance(a, TpyName):
        return (a.name in locals_
                and _value_tuple(locals_[a.name], analyzer) is not None)
    if isinstance(a, (TpyCall, TpyMethodCall)):
        # A value-tuple-returning call result (`split(p)`): the prvalue tuple
        # binds the `const std::tuple<...>&` slot directly (bare on both
        # paths; value tuples are always by-value, so no borrow-alias form
        # arises). A MUTATED slot is a non-const ref -- a prvalue can't bind
        # it -- so it stays rejected.
        return (not mutated
                and _value_tuple(analyzer.get_expr_type(a), analyzer)
                is not None)
    return not mutated and isinstance(a, TpyTupleLiteral)

def _ptr_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """A `Ptr[T]` value (name / field read) into a Ptr value slot: the
    ownership cascade never fires for the by-value pointer slot (`own is
    None`), so both paths render the bare value. An `Own[...]` slot is not a
    PtrType after the unwraps (auto-move cascade -> AST), a union slot lifts
    (-> AST), and unsupported coerce-wrapped args reject during lowering."""
    if not _eligible_ptr_value(ptype if isinstance(ptype, TpyType) else None,
                               analyzer):
        return False
    return (_eligible_ptr_value(analyzer.get_expr_type(a), analyzer))

def _span_coerce_arg(a: TpyExpr, ptype: TpyType | None,
                     locals_: dict[str, TpyType], analyzer) -> bool:
    """A span-family coerce (spanlike -> Span, span const-widening) into a
    by-value span slot: the coerce arm renders `::tpy::as_span(...)` /
    `as_mut_span(...)` (or the bare identity passthrough) exactly like
    gen_expr's is_span(coerce_target) arm; the ownership cascade never fires
    for the by-value slot. Bare span VALUES ride `_container_pass_through_arg`;
    array-literal inners (the make_array-prefixed target-threaded render)
    reject via the None disposition -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not is_span(pt):
        return False
    return (isinstance(a, TpyCoerce)
            and a.coercion.name in (_SPANLIKE_COERCIONS
                                    | _SPAN_METHOD_COERCIONS
                                    | {"span_to_readonly_span"})
            and _coerce_disposition(a) is not None)

def _slice_ctor_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                 locals_: dict[str, TpyType], analyzer) -> bool:
    """A slice-object ctor rvalue (`basic_slice(1, 3)` / `slice(a, b, c)`) into
    a by-value slice-object param slot: gen_call_arg's ownership cascade never
    fires for the value slot (`own is None`), so both paths render the bare
    template expansion (`use(s, ::tpy::BasicSlice{1, 3})`). `_slice_object_type`
    does not peel Own, so an `Own[...]` slot rejects (the auto-move cascade);
    a union slot (`Int32 | basic_slice`) lifts into the variant -> AST path.
    Slice-typed NAME args stay deferred with the other rvalue-ctor arg shapes."""
    if not _slice_object_type(ptype if isinstance(ptype, TpyType) else None):
        return False
    if not isinstance(a, TpyCall):
        return False
    fi = a.resolved_function_info
    return (_slice_object_type(analyzer.get_expr_type(a))
            and fi is not None and fi.is_method and fi.name == "__init__")

def _union_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name union arg into a non-Own slot of the SAME union type (F4).
    An already-union source skips `_gen_union_arg`'s member-lift arms and
    falls to the default bare-name render (a value union's `const
    std::variant<...>&` binds directly; a pointer variant copies by value) --
    or, for a DEEP-CONST pointer-variant slot (a `readonly[...]` annotation
    or the callee's `deep_const_borrow_params` verdict), the
    `ptr_variant_to_const` wrap, mirrored at lowering keyed on the same
    verdict -- so admission here is readonly-blind. A member-valued arg (a
    scalar name, a float literal, a record rvalue) hoists a temp on the AST
    path -- the arg-temp rows where the position flushes, AST otherwise; the
    temp-free member rows ride `_union_member_lift_arg`. `Own[union]` slots
    auto-move -> AST.
    NB a const-lifted pointer-variant LOCAL into a mutable slot renders the
    bare name on BOTH paths (a pre-existing AST miscompile, BUGS.md) --
    byte-identical, so the shape is not carved out here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if isinstance(pt, OwnType):
        return False
    ut = _eligible_value_union(pt)
    if ut is None:
        ut = _eligible_ptr_union(pt, analyzer)
        if ut is None:
            # WIDE member class for the bare same-union NAME pass: the
            # by-value variant copy is member-shape-blind (`f(v)` on
            # `v: int | set[int]`), so container/str members ride too --
            # the ptr-union decl arm's rationale.
            ut = _eligible_ptr_union_wide(pt, analyzer)
        if ut is None:
            return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return at == ut

def _value_union_temp_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          narrowed: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """Gate arm for the value-union temp row and narrowed-name reject."""
    if _value_union_temp_slot(a, ptype, locals_, analyzer) is None:
        return False
    if isinstance(a, TpyName) and a.name in narrowed:
        return False
    return True

def _protocol_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                       locals_: dict[str, TpyType], analyzer, *,
                       temps_ok: bool) -> bool:
    """An arg crossing into a protocol param slot -- the `_gen_protocol_arg` /
    `_gen_dynamic_protocol_arg` pre-arms of the plain call loop, reduced to the
    two arg shapes whose wrap `_protocol_arg_temp` spells: a bare in-scope NAME
    (lvalue) and a record-ctor RVALUE.

    A bare arg (a structural-slot lvalue, an inheritance-conformer lvalue, or
    an already-protocol name being forwarded) needs no lowering arm at all --
    it falls through `_lower_call_arg`'s ordinary tail, which carries the
    pointer-local `(*p)` retag exactly as `gen_call_arg` does. Every other
    shape hoists a `__tmp_N` and so needs a flushable position (`temps_ok`);
    note the @dynamic STRUCTURAL-conformer lvalue is in that set -- its
    zero-copy `RefAdapter` is still a temp.

    Out of slice: `Own[P]` / `Optional[P]` / protocol-union slots (rejected by
    `_protocol_arg_slot`), and any arg that is neither a name nor a ctor rvalue
    (a field read, a subscript, a nested call)."""
    proto = _protocol_arg_slot(ptype)
    if proto is None:
        return False
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    if isinstance(a, TpyName):
        rvalue = False
    elif (isinstance(a, TpyFieldAccess) and not is_dyn_protocol(proto)
          and (_field_receiver_ok(a, locals_, analyzer)
               # A field CHAIN reads the same bare lvalue (`len(h.c.item)`
               # -> `::tpy::__len__(h.c.item)`) -- the method-receiver
               # chain row's arg-position twin, same link constraints.
               or _chain_field_receiver_ok(a, locals_, analyzer))
          and (_f1_record(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                   at))), analyzer)
               # A CONTAINER field into a structural slot renders the same
               # bare lvalue (`return iter(self.items)` ->
               # `::tpy::__iter__(this->items)`).
               or _alias_ref_container(at))):
        # An F1-record / container FIELD read into a STRUCTURAL slot
        # (`len(r.cookies)` -> `::tpy::__len__(r.cookies)`): the same bare
        # lvalue render as a conformer name, through the ordinary tail.
        # @dynamic slots keep their adapter temps on the AST path.
        rvalue = False
    elif isinstance(a, TpyCall) and _ctor_shape_ok(a, analyzer):
        rvalue = True
    elif _coro_factory_structural_arg(a, proto, analyzer):
        # A coro-factory call rvalue at a STRUCTURAL slot
        # (`poll_once(f())`): always hoists the un-spelled `auto __tmp_N =
        # f();` structural rvalue temp -- sema types the frame result as
        # the protocol, so `_protocol_arg_temp`'s protocol-typed
        # bare-forward (an LVALUE rule) must not swallow it.
        return temps_ok and _witness("argtemp.protocol")
    elif _iter_rvalue_structural_arg(a, proto, locals_, analyzer):
        # The iterator sibling (gen-factory / iter() / dict-view rvalues):
        # the same un-spelled auto temp, always hoisted.
        return temps_ok and _witness("argtemp.iter_proto")
    elif (isinstance(a, TpyGeneratorExpression)
          and not is_dyn_protocol(proto)):
        # A genexpr at a STRUCTURAL slot (`sum_items(x * x for x in
        # range(5))`): the make_generator render hoists into the same
        # un-spelled auto temp; the genexpr's own lowering gates the
        # source/element shapes (a reject there falls the body back).
        return temps_ok
    elif (isinstance(a, TpySubscript) and not is_dyn_protocol(proto)
          and _record_getitem_rvalue_arg(a, analyzer)):
        # A by-value record-getitem subscript rvalue at a STRUCTURAL slot
        # (`show(container[1])`): the raw operator[] prvalue hoists the
        # `auto __tmp_N = container[1];` temp.
        rvalue = True
    elif (isinstance(a, TpyArrayLiteral) and not is_dyn_protocol(proto)):
        # A container literal into a STRUCTURAL slot (`math.dist([0.0, 0.0],
        # ..)`): the resolved literal hoists the un-spelled `auto __tmp_N =
        # std::array<double, 2>{..};` structural rvalue temp.
        at_res = resolve_pending_container(at, analyzer) or at
        if not _container_literal_shape_ok(a, unwrap_readonly(
                unwrap_ref_type(unwrap_send_sync(at_res))), analyzer):
            return False
        rvalue = True
    else:
        return False
    # The rendered concrete spelling only matters at lowering; the gate's
    # temp-vs-bare verdict is spelling-independent.
    spec = _protocol_arg_temp(proto, at, "", analyzer, rvalue=rvalue)
    if spec is None:
        return _witness("protoarg.bare")
    return temps_ok and _witness("argtemp.protocol")

def _protocol_bare_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free NAME slice of `_protocol_slot_arg` for the METHOD-arg
    loop (`cv.wait(g)` -- a structural slot deduces `T_p&` from the lvalue,
    an already-protocol / dyn inheritance-conformer name binds `Base&`):
    renders bare through the ordinary tail, so no ArgTemp arm is needed.
    The temp-hoisting faces (adapters, concrete rvalues) stay AST at the
    method position -- their ArgTemp arms live in the free/ctor loops only."""
    proto = _protocol_arg_slot(ptype)
    if proto is None:
        return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = analyzer.get_expr_type(a)
    if at is None:
        return False
    return (_protocol_arg_temp(proto, at, "", analyzer, rvalue=False) is None
            and _witness("protoarg.bare"))

def _typed_dict_ctor_call(e: TpyExpr, analyzer) -> 'RecordInfo | None':
    """A TypedDict constructor call (`Options("localhost", 8080)` -- sema's
    kwargs-pack rewrite leaves field-ordered positionals and NO synthetic
    ctor fi), or None. The AST's record-branch tail renders it exactly like
    a plain same/cross-module ctor, with `init_params` as the param source
    (`_gen_call`'s init_params fallback). Returns the RecordInfo."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)):
        return None
    if e.kwargs or e.double_star_unpack is not None or not e.args:
        return None
    if _ctor_call_special_form(e):
        return None
    if e.resolved_function_info is not None:
        return None
    if analyzer.registry.get_function(e.func_name):
        return None
    et = analyzer.get_expr_type(e)
    ri = (analyzer.registry.get_record_for_type(unwrap_readonly(et))
          if et is not None else None)
    if ri is None or not ri.is_typed_dict or ri.is_native:
        return None
    if not ri.init_params or len(e.args) != len(ri.init_params):
        return None
    return ri

def _record_rvalue_temp_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer, *,
                            upcast_ok: bool = False) -> bool:
    """Gate arm for the record-rvalue temp row. Constructor arguments are
    validated under `_RecordCtorUse.RECORD_TEMP` during recursive lowering;
    by-value record calls retain their callee and argument checks here.
    `upcast_ok` (the FREE-call gate only) admits the CHILD-typed upcast
    temp slice; the ctor gate/rows stay same-nominal (their mutated-slot
    row spells the SLOT type -- a different render)."""
    if _record_rvalue_temp_slot(a, ptype, analyzer,
                                upcast_ok=upcast_ok) is None:
        return False
    return ((isinstance(a, TpyCall)
             and (_ctor_shape_ok(a, analyzer)
                  or _ctor_instantiation_ok(a, analyzer)
                  or _typed_dict_ctor_call(a, analyzer) is not None))
            or _module_qual_ctor_shape(a, analyzer)
            or _record_rvalue_call_shape(a, analyzer)
            # An Own-rvalue METHOD call (`read_rc(Rc.new(Counter(3)))` ->
            # `Rc<Counter> __tmp_N = Rc<Counter>::new_<Counter>(...);` + the
            # bare temp name): the same create-lend-drop temp; the
            # method-call lowering validates callee/args recursively.
            or _method_rvalue_f1_record(a, analyzer))


def _coro_factory_structural_arg(a: TpyExpr, proto, analyzer) -> bool:
    """A coro-factory call RVALUE at a STRUCTURAL protocol slot -- the one
    verdict shared by the `_protocol_slot_arg` gate arm and
    `_lower_call_arg`'s auto-temp arm, so the two cannot drift."""
    return (isinstance(a, TpyCall) and not is_dyn_protocol(proto)
            and a.resolved_function_info is not None
            and a.resolved_function_info.is_async
            and is_rvalue_source(analyzer, a))


def _deref_coerce_arg(a: TpyExpr, ptype: 'TpyType | None',
                      locals_: dict[str, TpyType], analyzer
                      ) -> 'tuple[str, TpyType] | None':
    """A deref auto-coercion (`deref_to_target`) over a bare in-scope NAME
    into a plain F1-record slot (`print_point(r)` on a Deref-implementing
    `Ref` / `describe(p)` on `p: Ptr[Point]`). Two AST renders, keyed on the
    source: a `Ptr[T]` source is the inline `::tpy::deref_check(p)` lvalue
    (binds the const ref directly); a record-wrapper source hoists the
    slot-typed VALUE copy `Point __tmp_N = r.__deref__();` (the ref-param
    cascade's temporary row) and passes the temp. Returns
    `("inline" | "temp", slot)` or None."""
    if not (isinstance(a, TpyCoerce) and a.coercion.name == "deref_to_target"):
        return None
    src = a.expr
    if not (isinstance(src, TpyName) and src.name in locals_):
        return None
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if slot is None or not _f1_record(slot, analyzer):
        return None
    actual = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a.actual_type)))
    if isinstance(actual, PtrType):
        return "inline", slot
    if isinstance(actual, NominalType) and actual.is_user_record:
        return "temp", slot
    return None


def _iter_rvalue_structural_arg(a: TpyExpr, proto,
                                locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """An iterator/iterable-object RVALUE at a STRUCTURAL protocol slot
    (`make_toks(ints(3))` / `mutate_via_iterator(iter(pts2))` /
    `collect_items(d.keys())`): the AST hoists the un-spelled
    `auto __tmp_N = <rvalue>;` and passes the temp name -- the coro-factory
    temp's iterator sibling. Shared by the gate arm and the lowering; the
    init lowers under ITERABLE result use (the universal loop's
    source-capture admission)."""
    if is_dyn_protocol(proto):
        return False
    if isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is not None and fi.is_generator:
            return is_rvalue_source(analyzer, a)
        return (_iter_proto_call_ret(a, analyzer)
                and is_rvalue_source(analyzer, a))
    if isinstance(a, TpyMethodCall):
        return _dict_view_iterable_ok(a, locals_, analyzer,
                                      methods=("values", "keys", "items"))
    return False


def _module_qual_ctor_shape(a: TpyExpr, analyzer) -> bool:
    """A module-qualified plain record ctor rvalue (`pcre2.Code(7)` -- a
    TpyMethodCall resolving to `__init__`): the marker "qualified" kind's
    ctor slice, so the temp row's init lowers through that arm
    (`::tpyapp::_bindings::pcre2::Code(7)`)."""
    if not isinstance(a, TpyMethodCall):
        return False
    fi = a.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    return _marker_call_kind(a, analyzer) is not None

def _own_scalar_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """An rvalue-shaped eligible scalar into a plain `Own[scalar]` slot: the
    by-value slot binds the rvalue directly (no `_maybe_move` for a non-name,
    no copy temp for a non-simple-lvalue, the value-type `else` tail), so
    both paths render bare -- a coerced int literal (`takes(5)`), a scalar
    ctor / call rvalue, a binop. A bare NAME / field lvalue hoists the
    copy+move temp (`_own_lvalue_arg`); a coerce-WRAPPED lvalue splits on the
    AST's rendered-identity check (`needs_copy`) -- not mirrored, stays AST."""
    w = _plain_own_slot(ptype)
    # An inference-pending float slot (`Rc.new(3.14)` substitutes the
    # unresolved FloatLiteralType) resolves to the default double like the
    # concrete float slot.
    if w is None or not (_eligible_scalar(w)
                         or isinstance(w, FloatLiteralType)):
        return False
    peeled = _peel_coerce(a)
    if isinstance(peeled, (TpyName, TpyFieldAccess)):
        return False
    # A bare float literal keeps its FloatLiteralType in a matching float
    # slot (`Box(2.71)` -> `Box<double>(2.71)` bare) -- same render row.
    if isinstance(peeled, TpyFloatLiteral) and (
            is_float_type(w) or isinstance(w, FloatLiteralType)):
        return _witness("own.scalar_rvalue")
    at = analyzer.get_expr_type(a)
    if at is not None:
        # A BigInt slot leaves the int literal unwrapped by sema
        # (`heappush(h4, 42)` at `Own[T]` resolved `Own[int]`), so the
        # expr type is still an IntLiteralType; the render is the bare
        # literal either way.
        at = resolve_int_literals(at, analyzer.ctx.default_int_for_literal)
    return (_eligible_scalar(at)
            and _witness("own.scalar_rvalue"))

def _value_opt_ret(ret: 'TpyType | None') -> bool:
    """A value-repr Optional call result (`std::optional<T>` by value) --
    admitted only at WHOLE-optional sinks (is-none / opt-eq / IS_TRUTHY,
    threaded via allow_whole_optional), where the consuming render reads
    the materialized optional directly."""
    if ret is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    return isinstance(u, OptionalType) and not u.uses_pointer_repr()

def _record_call_rvalue_shape_ok(a: TpyExpr, analyzer) -> bool:
    """The shared call-shape tail of the record-rvalue arg rows: a ctor
    pins its shape/instantiation here, a free call must be a by-value
    record-returning call, and a method call's own lowering re-validates
    receiver/args recursively (the shallow pattern)."""
    if isinstance(a, TpyMethodCall):
        return True
    fi = a.resolved_function_info
    if fi is not None and fi.is_constructor:
        return (_ctor_shape_ok(a, analyzer)
                or _ctor_instantiation_ok(a, analyzer))
    return _record_rvalue_call_shape(a, analyzer)

def _value_record_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                             analyzer) -> bool:
    """A record rvalue (ctor / by-value record-returning call) into a
    BY-VALUE same-record slot (a ValueType record param --
    `timezone(timedelta(...), "IST")`): the slot is not a ref param, so
    gen_call_arg's temp cascade never fires and both paths render the bare
    expansion. The same-nominal check is the usual slice guard. Method-call
    rvalues stay out pending a witness."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not (isinstance(pt, NominalType) and pt.is_user_record
            and not pt.is_ref_param() and _f1_record(pt, analyzer)):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != pt or not is_rvalue_source(analyzer, a):
        return False
    return (_record_call_rvalue_shape_ok(a, analyzer)
            and _witness("call.value_record_arg"))

def _own_dyn_method_rvalue_ok(a: TpyMethodCall) -> bool:
    """Whether a method-call rvalue into an `Own[@dynamic P]` slot is
    ALREADY the erased `unique_ptr<P>` (renders bare -- `Box(e.clone())`,
    `Box(b1.take())`) rather than a concrete conformer needing the
    make_adapter wrap. Sema stamps BOTH shapes' expr type as the erased
    view, so the tell is the fi's DECLARED return: `Own[P]` itself, or an
    `Own[T]` whose substitution the caller's expr-type match then pins to P
    (a T substituting to a concrete record fails that match instead). An
    async factory (concrete coro frame) and a concrete-record return
    (`sock_accept -> Own[_SockAccept]`) both take the adapter wrap -> AST.
    Mirrors (narrower than) the AST's `_is_dyn_own_wrap_needed` /
    `dyn_protocol_forward_ok` provenance decision -- an edit there must
    revisit this classifier."""
    mfi = a.resolved_function_info
    if mfi is None or mfi.is_async:
        return False
    mret = mfi.return_type
    if isinstance(mret, OwnType):
        mret = mret.wrapped
    mret = unwrap_readonly(unwrap_send_sync(mret)) if mret is not None else None
    if isinstance(mret, TypeParamRef):
        return True
    return isinstance(mret, NominalType) and is_dyn_protocol(mret)

def _own_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record rvalue CALL (a ctor `A(7)` or a by-value
    record-returning free call) into a plain `Own[record]` SAME-nominal slot:
    an rvalue binds the `T&&` slot directly (gen_call_arg's rvalue-source
    `else` tail -- no temp), so both paths render the bare expansion,
    mirroring the `Own[union]`-slot ctor arm (`_own_union_ctor_arg`). A
    borrow-returning callee is not an rvalue source (the AST copies it
    through a temp) -> AST; the same-nominal check is a slice guard (sema
    rejects an upcast into an Own slot outright)."""
    w = _plain_own_slot(ptype)
    if w is None:
        return False
    w_container = _storage_call_ret(
        unwrap_readonly(unwrap_send_sync(w)), analyzer) is not None
    w_dyn = is_dyn_protocol(unwrap_readonly(unwrap_send_sync(w)))
    if not w_container and not w_dyn and not _f1_record(w, analyzer):
        return False
    if isinstance(a, TpyMethodCall):
        # A record-, container- or Own[@dynamic]-returning METHOD-call rvalue
        # (`Arc.new(Mutex.new(0))` / `g.set(acked.copy())` /
        # `Box(e.clone())` -- the unique_ptr<P> prvalue): binds the T&& slot
        # inline like a ctor rvalue; the method-call lowering validates its
        # receiver/args itself (the shallow _record_source_call pattern).
        if w_dyn and not _own_dyn_method_rvalue_ok(a):
            return False
        at = analyzer.get_expr_type(a)
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if isinstance(atu, OwnType):
            atu = unwrap_readonly(atu.wrapped)
        atu = _resolve_literal_seeded(atu, analyzer)
        return (atu == unwrap_readonly(unwrap_send_sync(w))
                and is_rvalue_source(analyzer, a)
                and _witness("own.record_rvalue"))
    if w_container or w_dyn:
        # Container / dyn-protocol Own slots admit only the method-rvalue
        # face here; names/literals ride the copy-temp and literal rows.
        return False
    if not isinstance(a, TpyCall):
        return False
    at = analyzer.get_expr_type(a)
    if at != w and not _covariant_record_upcast_ok(at, w, analyzer):
        # A covariant-generic upcast rvalue (`pets.append(Box(Parrot(..)))`
        # into `Own[Box[Pet]]`) binds the slot inline through the C++
        # converting move ctor -- the same bare expansion as the
        # same-nominal row.
        return False
    fi = a.resolved_function_info
    if fi is None:
        return False
    if fi.is_constructor:
        return ((_ctor_shape_ok(a, analyzer)
                 or _ctor_instantiation_ok(a, analyzer))
                and _witness("own.record_rvalue"))
    return (_record_rvalue_call_shape(a, analyzer)
            and _witness("own.record_rvalue"))

def _own_opt_record_slot(ptype: TpyType | None, analyzer) -> 'OptionalType | None':
    """An `Own[Optional[F1-record]]` param slot -- C++ `std::optional<T>&&`.
    Own forces the OWNING value form even though the bare Optional would use
    pointer repr, which is what makes both arg rows below differ from their
    plain-`Own[record]` siblings."""
    w = _plain_own_slot(ptype)
    if w is None:
        return None
    w = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(w)))
    if not (isinstance(w, OptionalType) and w.uses_pointer_repr()):
        return None
    return w if _f1_record(unwrap_readonly(w.inner), analyzer) else None


def _own_opt_ptr_name_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          narrowed: 'AbstractSet[str]',
                          analyzer) -> 'OptionalType | None':
    """A pointer-repr `Optional[record]` INDIRECT name at an
    `Own[Optional[record]]` slot (`Holder(r)` where `r: Own[Rec | None]` is a
    param): the `T*` binding cannot be dereferenced unconditionally, so
    `gen_expr_deref` rebuilds the owning optional null-safely
    (`THIROwnOptRebuild`). Returns the slot's OptionalType.

    A NARROWED occurrence reads as the extracted value and takes a different
    render, so it stays out."""
    w = _own_opt_record_slot(ptype, analyzer)
    if w is None or not isinstance(a, TpyName) or a.name in narrowed:
        return None
    at = locals_.get(a.name)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    if not (isinstance(atu, OptionalType) and atu.uses_pointer_repr()):
        return None
    return w if unwrap_readonly(atu.inner) == unwrap_readonly(w.inner) else None


def _copy_record_own_arg(a: TpyExpr, ptype: TpyType | None,
                         analyzer) -> bool:
    """`copy(name)` of a plain F1-record into a SAME-nominal plain
    `Own[record]` slot (`items.append(copy(p))` -> `push_back(Point(p))`)
    or its Own-OPTIONAL sibling (`Own[Box] | None` -- the by-value
    `std::optional<Box>` slot, whose converting ctor absorbs the same
    copy-construct rvalue: `consume_optional(Box(b))`): the rvalue binds
    like any record rvalue (`_own_record_rvalue_arg`'s row). The
    pointer-source split (`copy_plain_record_source` excludes
    pointer-locals, whose AST render derefs) re-runs at lowering with the
    live pointer set; a gate-admitted pointer source rejects there."""
    w = _plain_or_opt_own_slot(ptype)
    if w is None or not _f1_record(w, analyzer):
        return False
    # frozenset(): the gate is deliberately pointer-blind; lowering re-runs
    # with the live set and rejects pointer sources (gate-vs-lowering split).
    src = copy_plain_record_source(a, analyzer, frozenset())
    return src is not None and src == w and _witness("own.record_copy")

def _copy_open_elem_arg(a: TpyExpr, ptype: TpyType | None,
                        analyzer) -> 'TypeParamRef | None':
    """`copy(src[i])` of an open-T container element into an `Own[T]` slot
    inside a generic body (`Owned(copy(src[0]))` ->
    `Owned<T>(T(::tpy::__getitem__(src, 0)))`): `_gen_copy_expr`'s generic
    tail spells `{arg_type.to_cpp()}({read})` -- the open T -- around the
    standard checked element read. Returns the slot's TypeParamRef or None."""
    w = _plain_own_slot(ptype)
    if not isinstance(w, TypeParamRef):
        return None
    arg = copy_call_arg(a, analyzer)
    if not isinstance(arg, TpySubscript) or isinstance(arg.index, TpySlice):
        return None
    at = analyzer.get_expr_type(arg)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if not (isinstance(atu, TypeParamRef) and atu.name == w.name):
        return None
    return w

def _own_optional_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                                    analyzer) -> bool:
    """A same-nominal record RVALUE (a ctor `Inner(42)` or a by-value
    record-returning call) into an `Own[record | None]` ctor slot
    (`std::optional<Inner>&&` -- a @dataclass Optional-record field): the
    prvalue binds the rvalue-ref optional directly through C++'s implicit
    `Inner -> optional<Inner>` conversion, so both paths render the bare
    expansion (`Outer("a", Inner(42))`), mirroring `_own_record_rvalue_arg`'s
    plain `Own[record]` row. A record NAME would need the move/copy cascade
    (its own arm) and a borrow-returning callee is not an rvalue source -> AST."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not isinstance(u, OwnType):
        return False
    inner = unwrap_readonly(unwrap_send_sync(u.wrapped))
    if not isinstance(inner, OptionalType):
        return False
    rec = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(inner.inner)))
    if not (isinstance(rec, NominalType) and _f1_record(rec, analyzer)):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != rec:
        return False
    fi = a.resolved_function_info
    if fi is None:
        return False
    if fi.is_constructor:
        return (_ctor_shape_ok(a, analyzer)
                or _ctor_instantiation_ok(a, analyzer))
    return _record_rvalue_call_shape(a, analyzer)

def _value_array_call_arg(a: TpyExpr, ptype: TpyType | None,
                          analyzer) -> bool:
    """An Array-returning call/method rvalue at a matching Array slot
    (`use_array(b.get_data())` -- the generic `Array[T, N]` return
    substituted concrete): std::array is a VALUE container, so the prvalue
    binds the const-ref slot inline, bare on both paths -- no
    borrow/storage duality, no temp."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if pt is None or not is_array(pt):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return atu == pt


def _own_opt_slot_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-nominal record NAME (movable last use -> bare `std::move(a)`)
    or an Own[P|None]-returning call rvalue (the bare `std::optional<P>`
    prvalue) at an `Own[record | None]` slot (`std::optional<P>&&`). The
    ctor-rvalue face is `_own_optional_record_rvalue_arg`, `None` rides
    `_none_value_opt_arg`, and the Own[P|None] PARAM name forward is the
    borrow-slot side (`_optional_ptr_arg`'s storage-name lift). Gate is
    shape-only; the lowering enforces the move verdict on the NAME half."""
    slot = _own_storage_opt_param(ptype, analyzer)
    if slot is None:
        return False
    at = analyzer.get_expr_type(a)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if at is not None else None)
    if isinstance(a, TpyName) and a.name in locals_:
        return at_u == unwrap_readonly(slot.inner)
    if isinstance(a, (TpyCall, TpyMethodCall)) and _own_declared_call_ret(a):
        return at_u == slot
    return False


def _own_move_arg(a: TpyExpr, ptype: TpyType | None,
                  locals_: dict[str, TpyType], analyzer) -> bool:
    """The TEMP-FREE half of the Own-slot cascade: a movable OWN-param name
    at its LAST USE renders `std::move(name)` in ANY position (gen_call_arg's
    `_maybe_move` fires before the copy-temp arm, so no flush is needed) --
    the `heap_take(value)` ctor-MIL shape. Gate-side movability mirrors
    _LowerCtx's param seeding exactly: an `Own[...]`-declared binding of
    NON-VALUE payload (a value payload is never seeded, so its last use
    copies); body-movable locals keep riding the flushable copy+move row
    (`_own_lvalue_arg`), whose lowering picks the move when it applies.
    Deliberately narrowed-BLIND (unlike `_own_lvalue_arg`): sound today
    because a type-param bound cannot be a union (no isinstance-narrowing
    on an `Own[T]` name) and Optional-narrowed Own locals are not
    THIR-lowered yet -- re-add the narrowed exclusion if either lands."""
    if _own_lvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    own = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(
        locals_[a.name])))
    if own is None or own.wrapped.is_value_type():
        return False
    return id(a) in analyzer.ctx.all_last_uses

def _own_lvalue_arg(a: TpyExpr, ptype: TpyType | None,
                    locals_: dict[str, TpyType],
                    narrowed: 'set[str] | frozenset[str]', analyzer) -> bool:
    """Gate arm for the Own-slot copy+move row -- the slot/shape verdict plus
    the local argument checks. Both outcomes (the `__tmp_N` copy and the
    last-use `std::move(name)`) are expressible, so lowering admits the shape
    wholesale under `temps_ok` and lowering picks; restricting the temp-free
    move to the flushable positions is gate-narrowing only (a move arg in a
    condition stays AST -- deferred)."""
    if _own_lvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    bare = _peel_coerce(a)
    if isinstance(bare, TpyName):
        if (bare.name == "self" or bare.name in narrowed
                or bare.name not in locals_):
            return False
    return True

def _opt_own_record_name_arg(a: TpyExpr, ptype: TpyType | None,
                             locals_: dict[str, TpyType],
                             analyzer) -> 'TpyType | None':
    """A same-nominal F1-record NAME into an `Optional[Own[T]]` slot
    (`_urlopen(..., conn)` on `conn: Box[...]`): the AST's `_maybe_move`
    renders the movable last use `std::move(conn)` BARE -- the
    `std::optional` converting ctor absorbs the moved payload. Gate is
    shape-only; the lowering requires the move verdict (`_is_move_source`)
    and rejects the copy shape (unwitnessed). Returns the record payload or
    None."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not isinstance(pt, OptionalType):
        return None
    ow = unwrap_optional_own(pt)
    if ow is None:
        return None
    w = unwrap_readonly(ow.wrapped)
    if not _f1_record(w, analyzer):
        return None
    if not (isinstance(a, TpyName) and a.name in locals_):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return w if at == w else None


def _opt_own_ptr_opt_name_arg(a: TpyExpr, ptype: TpyType | None,
                              locals_: dict[str, TpyType],
                              analyzer) -> 'OptionalType | None':
    """A pointer-repr Optional NAME at an `Optional[Own[record]]` slot
    (`Boxed(tmp)` on `tmp: Box | None` -- the by-value `std::optional<Box>`
    param): the lift rebuilds owning storage from the `T*` binding,
    `::tpy::ptr_to_optional_move(tmp)` at a movable last use (the move half
    only -- the copy lift is `ptr_to_optional`, unwitnessed at this slot).
    Returns the STORAGE OptionalType the convert spells, or None."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not isinstance(pt, OptionalType):
        return None
    ow = unwrap_optional_own(pt)
    if ow is None:
        return None
    w = unwrap_readonly(ow.wrapped)
    if not _f1_record(w, analyzer):
        return None
    if not (isinstance(a, TpyName) and a.name in locals_):
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not (isinstance(at, OptionalType) and at.uses_pointer_repr()):
        return None
    return (at.with_inner(w)
            if unwrap_readonly(at.inner) == w else None)


def _readonly_record_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                              locals_: dict[str, TpyType], analyzer) -> bool:
    """A record-ctor rvalue into a readonly-ANNOTATED same-record slot
    (`take_ro(A(7))`, emitted `const A&`) with a SYNC callee: the const ref
    binds the rvalue for the statement, matching CPython's drop timing --
    both paths render the bare ctor expansion (no temp). Frame-capturing
    callees (generator/coro factories) take the argtemp.record_rvalue row
    instead (the frame outlives the statement). The NAME face (the readonly
    pass-through) stays deferred with the other deep-const rows."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(pt, ReadonlyType):
        return False
    inner = unwrap_readonly(pt)
    if not (isinstance(inner, NominalType) and _f1_record(inner, analyzer)):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != inner:
        return False
    return (_ctor_shape_ok(a, analyzer)
            and _witness("own.readonly_ctor"))

def _optional_ptr_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer,
                      *, temps_ok: bool) -> bool:
    """Gate arm for the pointer-repr Optional slot faces -- the shared face
    verdict plus the local checks per face. The 'name' face admits both
    renders (`&(name)` and the pointer-local bare pass); lowering splits on
    `lc.pointers`. A NARROWED union subject is admitted on the 'name' face
    too: its read renames to the `T&` extraction alias inside `_lower_expr`,
    and the AST's `_gen_optional_ptr_arg` tail wraps the same alias
    (`&(__u)`) -- the render mirrors, so no narrowed reject (the face is
    temp-free; rejecting it here would drift from lowering's shared verdict)."""
    face = _optional_ptr_arg_face(a, ptype, locals_, analyzer)
    if face is None:
        return False
    if face == 'none':
        return True
    if face == 'subscript':
        # `&(<lvalue record subscript>)` -- temp-free, so no flush position
        # needed; the face verdict already pinned the lvalue-borrow shape.
        return True
    if face == 'call_pass':
        # A borrow-returning call passed bare -- temp-free; the call's own
        # recursive lowering validates callee kind and args (the result
        # family is admitted under ptr_opt_passthrough).
        return True
    if face == 'ctor':
        if isinstance(a, TpyMethodCall):
            # Marker-call rvalue: the call's own lowering validates the
            # kind/args when the ArgTemp init lowers recursively.
            return temps_ok
        return temps_ok and _ctor_shape_ok(a, analyzer)
    if face == 'adapter_rvalue':
        # The structural-conformer Adapter temp -- flush positions only.
        return temps_ok and _ctor_shape_ok(a, analyzer)
    if face == 'adapter_name':
        # The RefAdapter lvalue temp -- flush positions only; pointer-local
        # sources keep rejecting at the lowering arm (deref unwitnessed).
        return temps_ok and a.name in locals_
    if face == 'lift':
        return _field_receiver_ok(a, locals_, analyzer)
    if face == 'container_temp':
        # The spelled container-literal temp + `&(...)` -- flush positions
        # only; the literal's element shapes gate inside its own lowering.
        return temps_ok
    # 'name' / 'pass'
    if a.name not in locals_:
        return False
    return True

def _union_member_lift_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free member rows of `_gen_union_arg`'s pointer-variant branch:
    a `None` literal (`pv{std::monostate{}}`) or a member-typed record NAME
    (`pv{&(name)}` -- never a temp: names are never rvalue sources) into a
    non-Own pointer-variant slot. A deep-const slot (a `readonly[...]`
    annotation or the callee's `deep_const_borrow_params` verdict) takes the
    same lift with the const-pointee variant spelling -- mirrored at
    lowering, so admission is const-blind. A record RVALUE (`take(A(n))`)
    hoists a named temp -> AST. A narrowed subject is admitted here too (its
    `locals_` type is the member): the AST's `already_union` verdict (the C++
    binding is still the variant) renders it as the bare extraction alias,
    which lowering mirrors -- another byte-identical pre-existing AST
    miscompile (an `A&` alias into a variant slot; see BUGS.md's
    union-operand class)."""
    slot = _arg_ptr_union_slot(ptype, analyzer)
    if slot is None:
        return False
    ut, _deep_const = slot
    if isinstance(a, TpyNoneLiteral):
        # Slice guard: sema only types None here when the union has a None
        # member (the monostate slot).
        return any(is_void_like_type(m) for m in ut.members)
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return (any(at == m for m in ut.members if not is_void_like_type(m)))

def _union_coerced_literal_arg(a: TpyExpr, ptype: TpyType | None,
                               locals_: dict[str, TpyType], analyzer) -> bool:
    """An int literal into a VALUE-union slot (`take_vu(3)`): sema coerces the
    literal to the union itself, so `_gen_union_arg`'s `already_union` verdict
    falls to the default gen_call_arg render -- the bare literal (the variant
    converting ctor does the work). A bare member-typed literal (`take_vu(2.5)`
    -- a float literal is typed at the member, not the union) hoists the
    `std::variant<...> __tmp_N` temp -> AST path. Only value unions arise: a
    numeric literal cannot coerce to a record union."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(a, TpyCoerce):
        return False
    ut = _eligible_value_union(unwrap_readonly(unwrap_send_sync(pt)))
    if ut is None:
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return at == ut

def _own_union_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record-ctor rvalue into an `Own[union]` value-variant slot
    (`take_own(A(7))` -> `take_own(A(7))`): gen_call_arg's Own cascade is inert
    for the shape -- the ctor is an rvalue (no `_maybe_move`, no copy temp) and
    no `to_value_variant` lift fires (a ctor is not a ptr-variant source) -- so
    both paths render the bare ctor expansion. The arg record must be a member
    of the union (slice guard; sema enforces). An Own[union] slot with a NAME
    arg is the auto-move cascade (`std::move(u)`) -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_send_sync(pt)
    if isinstance(pt, ReadonlyType):
        return False
    if not isinstance(pt, OwnType):
        return False
    ut = _eligible_ptr_union(pt.wrapped, analyzer)
    if ut is None:
        return False
    if not isinstance(a, TpyCall):
        return False
    rt = analyzer.get_expr_type(a)
    if not any(rt == m for m in ut.members if not is_void_like_type(m)):
        return False
    return (_ctor_shape_ok(a, analyzer)
            and _witness("own.union_ctor"))

def _ctor_effective_params(e: TpyCall, ri) -> 'list[ParamInfo]':
    """The param list the record-ctor arg loop reads: the synthetic fi's, or
    -- when the record has NO own `__init__` but registered init_params (an
    inherited param-ful `__init__`) -- the registry triples, mirroring
    `_gen_call`'s init_params fallback. `_ctor_shape_ok` already pinned the
    arity of whichever source applies."""
    fi = e.resolved_function_info
    if fi.params or not e.args:
        return fi.params
    if ri is None or ri.get_method_overloads("__init__") or not ri.init_params:
        return fi.params
    return [ParamInfo(n, t) for n, t, _ in ri.init_params]

def _ctor_arity_ok(e: TpyCall, fi) -> bool:
    """Positional arity for a raw-name record-ctor call. Exact arity is the
    common case; fewer args are admitted when the OMITTED trailing params all
    carry a default. Each default is rendered onto the C++ ctor signature
    (`records.py` emits it via `emit_defaults`), so the call passes only the
    provided args -- byte-identical to the exact-arity `Name(args)` emit. A
    variadic slot has no positional default to fall back on, and more args than
    params is a resolution the raw-name shape never produces -> AST."""
    n = len(e.args)
    if n > len(fi.params):
        return False
    if not all(p.has_default and not p.is_variadic for p in fi.params[n:]):
        return False
    if n < len(fi.params):
        return _witness("ctor.omit_defaults")
    return True

def _ctor_call_special_form(e: TpyCall) -> bool:
    """Whether a ctor-shaped call carries any of the special-form markers
    that take other emit shapes -- shared by `_ctor_shape_ok` and
    `_typed_dict_ctor_call` so a new TpyCall marker excludes both."""
    return (e.call_type is not None or bool(e.type_args)
            or bool(e.inferred_type_args)
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None)

def _ctor_shape_ok(e: TpyCall, analyzer) -> bool:
    """A bare-name plain user-record constructor call in the `Name(args)` /
    qualified `::ns::Name(args)` emit shape -- `_gen_call`'s record-branch
    tail: the RAW source name for a same-module record, or the
    `record_qualification` spelling for an imported one (mirrored at the
    THIRCtorCall lowering), args through `_gen_record_ctor_args` with every
    special arm structurally unreachable. This is the arg-blind
    shape/registry core used before recursive TpyCall lowering validates the
    constructor arguments. Native / cpp_template / multi-overload / TypedDict
    ctors take other emit shapes -> AST."""
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if _ctor_call_special_form(e):
        return False
    # A same-name free function wins _gen_call's registry branch before the
    # record lookup.
    if analyzer.registry.get_function(e.func_name):
        return False
    # Sema attaches a SYNTHETIC constructor fi (`is_constructor`, named after
    # the record, params = the resolved __init__'s); a builtin type ctor
    # (`Int32(x)`) resolves to the real @cpp_template __init__ instead and
    # rides the scalar-ctor arm of the `TpyCall` lowering.
    fi = e.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if fi.cpp_template or fi.native_function or fi.native_name:
        return False
    inherited_arity = False
    if not _ctor_arity_ok(e, fi):
        # A record with NO own `__init__` but a param-ful INHERITED one:
        # sema's synthetic ctor fi carries EMPTY params, so positional args
        # fail the fi-arity gate. The AST arg loop reads the registry's
        # init_params triples instead; defer the arity verdict to the
        # no-own-overloads arm below, which checks against those.
        if fi.params or not e.args:
            return False
        inherited_arity = True
    ri = analyzer.registry.get_record(e.func_name)
    rt = analyzer.get_expr_type(e)
    if not (isinstance(rt, NominalType) and rt.is_user_record):
        return False
    ri_t = analyzer.registry.get_record_for_type(rt)
    if ri is None:
        # `cls(...)` in a @classmethod spells no record; the sema result type
        # is authoritative (the lowering spelling resolves it the same way).
        ri = ri_t
    elif ri_t is not None and ri_t is not ri:
        # The short-name collision override: with two records sharing a
        # short name (`from world import Point as WorldPoint` beside
        # `from screen import Point`), the short-name lookup is
        # last-write-wins while sema resolved the TYPE qname-first. The AST
        # emits from the type, and the THIRCtorCall lowering reads
        # get_record_for_type too -- validate against the same record.
        # The AST conditions its override on the short-name record being a
        # plain user record; a native/builtin short-name hit keeps ITS emit
        # path there, a shape with no witness -- reject, don't mirror blind.
        if ri.builtin_type_key is not None or ri.is_native:
            return False
        ri = ri_t
    if ri is None or ri.builtin_type_key is not None:
        return False
    # A NATIVE exception record (Throwable subclass) constructs via the plain
    # `::tpy::Name(args)` emit: the resolved synthetic ctor fi is plain, and the
    # `@cpp_template` __init__ overloads only inform C++ overload resolution --
    # the call site emits args verbatim (byte-identical to a user-record ctor).
    # Any OTHER native record takes a divergent ctor emit shape -> AST.
    is_native_exc = ri.is_native and ri.implements_throwable
    if ri.is_native and not is_native_exc:
        # A plain @native record with NO own `__init__` constructs via
        # `native_name(args)` (@native_c: the `{args}` aggregate), args
        # typed from init_params -- the same no-own-init fallback the
        # inherited-init face reads. An overloaded / @native /
        # @cpp_template `__init__` takes other emit arms
        # (gen_call_from_fi / the builtin ctor path) -> AST.
        if ri.get_method_overloads("__init__"):
            return False
    if ri.type_params:  # generic ctor: substituted/spelled type args -> AST
        return False
    if is_native_exc:
        # Skip the single-overload / non-cpp_template init_fi checks below: a
        # native exception's `@cpp_template` overloads all expand to the plain
        # `::tpy::Name(args)` ctor, so the verbatim-arg emit is byte-identical
        # whichever overload C++ selects.
        return True
    # A multi-overload __init__ set: _gen_record_ctor_args reads the record's
    # init_info params, which may disagree with the resolved stub -> AST. The
    # special member forms are read off the REAL __init__ (the synthetic fi
    # carries only params + mutation facts).
    overloads = ri.get_method_overloads("__init__")
    if not overloads:
        # No OWN __init__: either the implicit default ctor (zero-arg call,
        # fi-arity already pinned it) or an INHERITED param-ful __init__
        # (sema attaches a synthetic ctor fi with EMPTY params; the real
        # param list lives in ri.init_params, which both arg loops read --
        # the AST via _gen_call's init_params fallback, the lowering via
        # _ctor_effective_params). Arity for the inherited face checks the
        # triples: exact, or omitted trailing params that carry a default.
        if not inherited_arity:
            return True
        ip = ri.init_params
        if not ip or len(e.args) > len(ip):
            return False
        return all(d is not None for _, _, d in ip[len(e.args):])
    if inherited_arity:
        # Own overloads exist but the synthetic fi is param-less: a shape
        # mismatch this gate does not model -> AST.
        return False
    if len(overloads) != 1:
        return False
    init_fi = overloads[0]
    if (init_fi.cpp_template or init_fi.native_function or init_fi.native_name
            or init_fi.is_consuming or init_fi.error_return_type is not None
            or init_fi.native_cpp_return_type is not None
            or any(isinstance(p.type, LiteralType) for p in init_fi.params)):
        return False
    return True

def _ctor_instantiation_ok(e: TpyCall, analyzer) -> bool:
    """The INSTANTIATION form of a record-ctor call -- spelled
    `Cell[Int32]()` / `Poll[T]()` or inferred `Pair(1, 2)` (`call_type`
    set) -- plus the zero-arg builtin-CONTAINER instantiation arriving
    with a constructor fi (the non-record carve-out below): `_gen_call`'s call_type-branch tail renders
    `type_to_cpp(call_type)(args)`, mirrored as `THIRCtorCall.type_cpp =
    lc.render_type(call_type)` -- byte-identical by construction, so
    cross-module and generic spellings need no extra gating beyond
    `_f1_record`'s type-arg slice. Shares the arg rows with the raw-name
    face (the scalar / record-rvalue slice coincides across the two AST
    arg loops); the None-literal / array-literal / `T()`-construct
    targeted arms and protocol/union/optional slots are excluded by those
    rows. Native records (native fi arms) and template/native ctor fis
    take other emit arms -> AST."""
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    ct = e.call_type
    if ct is None or not isinstance(ct, NominalType):
        return False
    if (e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert):
        return False
    if e.args and isinstance(e.args[0], TpyListRepeat):
        return False
    fi = e.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if (fi.cpp_template or fi.native_function or fi.native_name
            or fi.error_return_type is not None):
        return False
    # Omitted trailing defaults render on the C++ ctor signature exactly as
    # for the raw-name face (`ArrayList[Int32, 8]()` with a defaulted `items`
    # param calls the spelled zero-arg `ArrayList<int32_t, 8>()`).
    inherited_arity = False
    if not _ctor_arity_ok(e, fi):
        # A record with NO own `__init__` but a param-ful INHERITED one
        # (`TypedM[Int32](7)`): the synthetic ctor fi carries EMPTY params,
        # so positional args fail the fi-arity gate -- the raw-name face's
        # inherited-init situation on the instantiation spelling. Defer the
        # arity verdict to the registry triples below.
        if fi.params or not e.args:
            return False
        inherited_arity = True
    if not _f1_record(ct, analyzer):
        # A zero-arg builtin-CONTAINER instantiation reaching the ctor path
        # with a constructor fi (`Array[Int32, 8]()` -- e.g. the pascal
        # frontend's default-array init): the same `type_to_cpp(call_type)()`
        # render with no arg arms to diverge. Arg-ful container ctors keep
        # rejecting here (their args have their own AST arms).
        return (not e.args
                and _empty_instantiation_family(ct)
                and _witness("ctor.container_empty_instantiation"))
    ri = analyzer.registry.get_record_for_type(ct)
    if ri is None:
        return False
    if inherited_arity:
        # Mirror _ctor_shape_ok's inherited-init face: own overloads beside a
        # param-less synthetic fi is a shape mismatch this gate does not
        # model; otherwise arity checks the init_params triples (exact, or
        # omitted trailing params that carry a default). The arg loop reads
        # the same triples via _ctor_effective_params.
        if ri.get_method_overloads("__init__"):
            return False
        ip = ri.init_params
        if not ip or len(e.args) > len(ip):
            return False
        if not all(d is not None for _, _, d in ip[len(e.args):]):
            return False
        _witness("ctor.inherited_instantiation")
    # A NATIVE record's zero-arg instantiation (`UninitStorage[T]()`) renders
    # the same `type_to_cpp(call_type)()` (native_cpp_names spelling) with no
    # arg arms to diverge. An arg-ful native ctor is admitted only when its
    # REAL `__init__` is a plain stub -- an @native/@cpp_template overload (or
    # a multi-overload set) has its own emit arms -> AST.
    if ri.is_native and e.args:
        overloads = ri.get_method_overloads("__init__")
        if len(overloads) != 1:
            return False
        init_fi = overloads[0]
        if (init_fi.cpp_template or init_fi.native_function
                or init_fi.native_name or init_fi.is_consuming
                or init_fi.error_return_type is not None
                or init_fi.native_cpp_return_type is not None
                or any(isinstance(p.type, LiteralType)
                       for p in init_fi.params)):
            return False
    return True

def _str_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType], analyzer,
                          *, mutated: bool = False) -> bool:
    """A str-slice arg into a non-Own `str`/`StrView`/`String` param slot. A
    `str`/`StrView` param renders `std::string_view`, and every slice source
    lands in it bare: a param/view local IS a string_view, an owned local
    converts implicitly, a literal is const char[N]. A `String` slot takes
    String values bare and coerced str/StrView sources through the coerce
    arm. An `Own[...]` slot materializes an owned copy the bare emit does not
    reproduce (the gen_call_arg auto-move cascade) -> AST path.
    A MUTATED String slot (`std::string&`) keeps only an owned-String NAME
    (an lvalue binding the ref legally): the coerce half materializes a
    `std::string(x)` temp -- the mutated-String-param miscompile. The
    view-slot branch is mutation-blind (`std::string_view` stays by value)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, NominalType) and is_string_type(pt):
        # A `String` slot (`const std::string&`): an owned String value binds
        # bare; a str/StrView source arrives as a str_to_string /
        # strview_to_string coerce (identity for a NUL-free literal,
        # `std::string(x)` otherwise) -- rendered by the coerce arm itself.
        at = analyzer.get_expr_type(a)
        if mutated:
            return (isinstance(a, TpyName) and _is_string_owned(at))
        return ((_resolved_str_value(at, analyzer) is not None
                 or _is_string_owned(at)))
    if not (isinstance(pt, NominalType)
            and (is_str_type(pt) or is_str_view_type(pt))):
        # A generic callee's slot can still carry the parser's unresolved
        # view var (`repr(b)` at `PendingStrType`); it renders the same
        # `std::string_view` once resolved, so ask the resolver rather than
        # the nominal spelling.
        if _resolved_str_value(pt, analyzer) is None:
            return False
        _witness("arg.pending_str_slot")
    if isinstance(a, TpyStrLiteral):
        return True
    return (_resolved_str_value(analyzer.get_expr_type(a), analyzer) is not None)

def _literal_scalar_slot(ptype: TpyType | None) -> bool:
    """A param slot annotated `Literal[...]` over a scalar base -- spelled
    as the bare base type (`LiteralType` delegates `to_cpp`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    return isinstance(pt, LiteralType) and _eligible_scalar(pt.base_type)


def _own_str_literal_arg(a: TpyExpr, ptype: TpyType | None) -> bool:
    """A str LITERAL into an `Own[str]` ctor slot renders bare
    (`Box<std::string>("hello")` -- the prvalue converts into the by-value
    slot in place; the gen_call_arg auto-move cascade that keeps lvalue
    str sources off this slot never fires for a literal). Temp-free, so
    position-independent."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not isinstance(pt, OwnType):
        return False
    inner = unwrap_readonly(pt.wrapped)
    return (isinstance(a, TpyStrLiteral)
            and isinstance(inner, NominalType) and is_str_type(inner))


def _opt_str_shim_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer) -> bool:
    """A value-repr `Optional[str]` param NAME into another value-repr
    `Optional[str]` slot -- the AST's `_maybe_convert_opt_view_param` same-TPy-
    type ARG split (`s ? std::make_optional(std::string(*s)) : std::nullopt`).
    Fires for the WHOLE optional whether or not sema narrowed the read: gen_expr
    threads the slot type (Optional[str]), so the shim renders on the bare
    binding. `_opt_view_arg_shim` pins the exact source/slot family match the
    AST shim tests (an owned-`str` inner both sides; a `StrView` inner passes
    bare -> rejected here)."""
    if not (isinstance(a, TpyName)
            and a.name in locals_):
        return False
    return _opt_view_arg_shim(
        locals_.get(a.name),
        ptype if isinstance(ptype, TpyType) else None, analyzer)

def _str_owned_slot_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType],
                        param_names: 'set[str] | frozenset[str]',
                        analyzer) -> bool:
    """A str-slice arg into an `Own[str]` container element slot -- the
    `xs.append(s)` shape `_str_pass_through_arg` rejects (Own is its cutoff). A
    str LITERAL lands bare (const char[N] -> the vector's `std::string` ctor); a
    VIEW-form source materializes an owned copy `std::string(x)` via the S1
    view->owned THIRFormConvert (the same wrap `_lower_container_elem` applies at
    literal-element positions). Two view forms qualify: a `StrView`-resolved
    local, and a `str` PARAM -- resolved `str`, not `StrView`, but the signature
    spells `std::string_view`, so its read is BORROW too. They are told apart
    from an owned `str` local (the STORAGE form that would MISS the copy) by
    `_str_name_form`'s rule, mirrored: a `StrView` resolution OR the name being a
    param. An owned STORAGE source (an owned `str`/`String` local, a
    subscript-owned or call-owned result) rides gen_call_arg's copy+move-temp
    cascade, which the bare/convert emit does not reproduce -- left on the AST
    path."""
    w = _plain_own_slot(ptype)
    if w is None or not is_str_type(w):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    at = _resolved_str_value(analyzer.get_expr_type(a), analyzer)
    if at is None:
        return False
    if is_str_view_type(at):
        return True
    # An owned-`str`-typed source is STORAGE unless it is a str PARAM (BORROW --
    # `std::string_view` in the signature). A reassigned str param is already
    # whole-body-rejected, so the view form is stable at every use here.
    if isinstance(a, TpyName):
        return a.name in param_names
    # A container-ELEMENT owned-str read (`tag.append(argv[i])`): the element
    # lvalue lands bare in the element slot (`push_back(__getitem__(argv, i))`
    # -- the vector copies on insert), no cascade on either path.
    if (isinstance(a, TpySubscript)
            and _borrow_elem_subscript_shape(a, locals_, analyzer,
                                             _container_str_elem)):
        return True
    # An owned-str RVALUE source (a concat binop, an owned-returning call)
    # binds the `T&&` element slot bare -- gen_call_arg's is_temporary_expr
    # arm, no temp and no view convert. Owned LVALUES (locals, field reads)
    # keep riding the AST's copy+move-temp cascade.
    return (not isinstance(a, TpyFieldAccess)
            and is_rvalue_source(analyzer, a))

def _bytes_owned_slot_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          param_names: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """The bytes twin of `_str_owned_slot_arg`, VIEW-form sources only (the
    literal face is its own row): a span-form source at an `Own[bytes]`
    container element slot materializes `::tpy::bytes_copy(x)` via the S6
    view->owned THIRFormConvert. Three faces: a bytes PARAM name (the
    signature spells `std::span<const uint8_t>`, so its read is BORROW --
    including a narrowed `bytes | None` param whose deref reads `(*b)`), a
    `BytesView`-resolved local, and a SLICE rvalue arriving under the sema
    `bytesview_to_bytes` coerce (the coerce IS that copy; the render arm
    peels it and wraps the view slice). An owned bytes LOCAL is STORAGE and
    keeps riding the AST's copy+move-temp cascade."""
    w = _plain_own_slot(ptype)
    if w is None or not is_bytes_type(unwrap_readonly(w)):
        return False
    src = a
    if isinstance(src, TpyCoerce):
        if src.coercion.name != "bytesview_to_bytes":
            return False
        src = src.expr
    if isinstance(src, TpyName):
        at = _resolved_bytes_value(analyzer.get_expr_type(a), analyzer)
        if at is None:
            return False
        return is_bytes_view_type(at) or src.name in param_names
    if isinstance(src, TpySubscript) and isinstance(src.index, TpySlice):
        return _resolved_bytes_value(analyzer.get_expr_type(src),
                                     analyzer) is not None
    return False

def _bytes_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bytes-slice arg into a non-Own `bytes`/`BytesView` param slot. The
    param renders `std::span<const uint8_t>`; a param/view local IS a span, an
    owned local (vector) converts implicitly, and a literal takes gen_call_arg's
    static-span pin (`::tpy::bytes_literal(...)`, lowered BORROW). The
    pin keys on the RAW ptype (`is_bytes_type(ptype) or is_bytes_view_type(
    ptype)`), so a wrapped slot (readonly/Own) rejects the literal -- the AST
    renders it owned there, a shape this arm does not thread. An `Own[bytes]`
    slot materializes an owned copy for value args too -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    # gen_call_arg peels coerce wrappers before its literal span pin (a literal
    # into a BytesView slot arrives wrapped in the view coercion); mirror it.
    lit = _peel_coerce(a)
    if isinstance(lit, TpyBytesLiteral):
        return is_bytes_type(pt) or is_bytes_view_type(pt)
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(pt, NominalType) and (is_bytes_type(pt) or is_bytes_view_type(pt))):
        return False
    return (_resolved_bytes_value(analyzer.get_expr_type(a), analyzer) is not None)

def _char_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A Char value into a Char param slot -- both spell `char`, passed bare
    (a value scalar in all but name) -- or a single-char str literal into one
    (the target-typed `'x'` render, gen_expr's char-literal arm; lowered
    param-aware via `_lower_char_targeted`). A multi-char literal never
    renders as a char literal -> AST path (sema rejects it anyway). An
    `Own[Char]` slot is rejected conservatively (the unwrap chain does not
    peel Own)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not _eligible_char(pt):
        return False
    if isinstance(a, TpyStrLiteral):
        return len(a.value) == 1
    return (_eligible_char(analyzer.get_expr_type(a)))

def _int_literal_bigint_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare int literal (or folded `-3`) into a BigInt param slot: sema
    leaves it unwrapped (unlike a fixed-int slot's range-checked
    `int_literal_to_fixed_int` coerce), and gen_call_arg threads the slot
    into the literal render (`::tpy::BigInt(10)`) -- mirrored by the
    `_slot_literal_retype` at `_lower_call_arg`'s tail."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not is_big_int_type(pt):
        return False
    if isinstance(a, TpyIntLiteral) or _folded_neg_int_literal(a, analyzer):
        return True
    # A constant-valued int arithmetic expression (`2 ** 64 + 1`, `-2 ** 64`)
    # keeps its IntLiteralType through sema but codegen renders it as an
    # ordinary BigInt binop (each operand individually `::tpy::BigInt(..)`),
    # not a single retyped literal. Lower it as a normal expression -- the
    # BigInt slot is what makes the operands materialize wide.
    if isinstance(a, (TpyBinOp, TpyUnaryOp)):
        at = analyzer.get_expr_type(a)
        return isinstance(at, IntLiteralType)
    return False

def _float_literal_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                    locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A bare float literal (FloatLiteralType -- sema leaves it unwrapped in a
    matching float slot) into a float param slot: a double slot renders
    repr(v) bare on both paths (gen_expr's TpyFloatLiteral double branch ==
    _emit_literal's float arm); a Float32 slot takes the `f` suffix via the
    `_slot_literal_retype` at `_lower_call_arg`'s tail. inf/nan literals
    (`1e400`) reject during literal lowering."""
    if not _float_literal_operand(a, analyzer):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(a, TpyUnaryOp):
        # A negated float literal (`-1.0` -> `-(1.0)`) lowers through the
        # unary arm's resolved-dunder path to `THIRUnaryArith('-({0})', 1.0)`,
        # byte-identical to `_gen_unaryop`'s float negation. The inner literal
        # lowers UNtargeted (a bare double), so a Float32 slot -- which the AST
        # suffixes `1.0f` via its threaded target -- stays off this arm.
        return is_float_type(pt) and not is_float32_type(pt)
    return is_float_type(pt)


def _float_literal_operand(a: TpyExpr, analyzer) -> bool:
    """A bare float literal or a unary-minus over one, both of
    `FloatLiteralType` (sema leaves the literal unwrapped)."""
    if isinstance(a, TpyFloatLiteral):
        return isinstance(analyzer.get_expr_type(a), FloatLiteralType)
    return (isinstance(a, TpyUnaryOp) and a.op == "-"
            and isinstance(a.operand, TpyFloatLiteral)
            and isinstance(analyzer.get_expr_type(a.operand), FloatLiteralType))

def _record_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name F1-record arg into a non-Own param slot of the SAME record
    or a PARENT of it (`const A&` / `A&`): no gen_call_arg lift fires for
    either pairing (`own is None`, no protocol / Optional / union / covariant
    arm -- an upcast is C++'s implicit derived-to-base reference binding), so
    both paths render the bare name -- or `(*p)` for an F2 pointer-local, the
    gen_expr_deref indirect render `_lower_call_arg` retags. A narrowed
    subject's read arrives with `locals_` retyped to the member record and
    renames to its `T&` extraction alias at lowering (bare on both paths).
    `self` renders the receiver deref `(*this)` (the tail retag, like an F2
    pointer-local). An `Own[record]` slot auto-moves at last use -> its own
    rows; a `readonly[record]` slot binds the same bare name (`const T&` --
    no readonly lift exists for records, and the retags are const-blind).
    A record RVALUE (`take_rec(A(7))`) is not a
    name: the AST hoists it into a `__tmp_N` (free calls, typed at the CHILD
    for an upcast) or inlines it (method calls -- the const-slot ctor row
    rides `_method_ctor_rvalue_arg`; the mutated-ref-param shape is the
    miscompile in BUGS.md) -- rvalues stay off this arm."""
    if not isinstance(a, TpyName):
        return False
    if a.name == "self":
        # `self` into a same/parent record slot: the receiver deref
        # `(*this)` (probe-verified `on_init((*this))`), or the bare
        # `__self` frame-field read in a resumable method -- THIRSelf's
        # deref flag, set at `_lower_call_arg`'s tail.
        at = analyzer.get_expr_type(a)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
    elif a.name in locals_:
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            locals_[a.name])))
    else:
        return False
    if isinstance(at, OwnType):
        # An Own[record] PARAM binding is by-value storage the arg reads
        # bare, like any record name (the slot below is still non-Own).
        at = unwrap_readonly(at.wrapped)
    if _optional_ptr_borrow(at, analyzer) is not None:
        # A pointer-repr Optional borrow name proven non-None (sema retyped
        # the read; an unproven pass to a record slot is a sema type error):
        # the `(*p)` deref retag at lowering, gen_expr_deref's indirect render.
        at = analyzer.get_expr_type(a)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if isinstance(at, OptionalType):
            return False  # not narrowed -- defensive, sema rejects upstream
    if not (isinstance(at, NominalType) and _f1_record(at, analyzer)):
        return False
    if ptype is None or not isinstance(ptype, TpyType):
        return False
    pt = unwrap_ref_type(unwrap_send_sync(ptype))
    if isinstance(pt, OwnType):
        return False
    # A `readonly[record]` slot binds the same bare name (`const T&` --
    # gen_call_arg has no readonly lift for records; the F2 pointer-local
    # `(*p)` retag is const-blind too). Own slots keep the auto-move
    # cascade -> their own rows.
    pt = unwrap_readonly(pt)
    return ((pt == at or analyzer.registry.is_subclass_of(at, pt)))

def _method_receiver_type(recv: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> 'TpyType | None':
    """The method receiver's binding type. A bare name reads the declared
    binding (`locals_`, the pre-resolution container type -- mirrors
    method-call lowering's docstring note); a field-access receiver reads
    its sema-resolved type. A non-name receiver whose sema type is still a
    PENDING container (a literal-seeded element read, `xs[i][j]`) resolves
    it -- the AST reads get_resolved_type at emit, and an unresolved type
    here can only mis-classify the family to an auto-reject."""
    if isinstance(recv, TpyName):
        return locals_.get(recv.name)
    t = analyzer.get_expr_type(recv)
    return resolve_pending_container(t, analyzer) or t

def _indirect_field_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """A one-level field access whose own RECEIVER is a call or subscript
    result (`root.get().children.append(x)`, `g.get().log.append(1)`,
    `::tpy::__getitem__(a.bs, 0).as_.push_back(...)`): the inner expression
    yields a `.`-access F1-record borrow (exactly the shapes the method
    receiver rows already admit), so the field renders `.field` off the bare
    inner render as it would off a name, and the outer method access stays
    `.`. Pointer / Optional / non-record inner results keep their own unwraps
    and stay deferred."""
    if not isinstance(recv, TpyFieldAccess) or not _field_markers_clean(recv):
        return False
    inner = recv.obj
    if isinstance(inner, TpyMethodCall):
        return _method_call_receiver_ok(inner, locals_, analyzer)
    if isinstance(inner, TpySubscript):
        return _container_record_elem_subscript(inner, locals_, analyzer)
    if isinstance(inner, TpyCall):
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(inner))))
        if isinstance(rt, OwnType):
            rt = unwrap_readonly(rt.wrapped)
        return isinstance(rt, NominalType) and _f1_record(rt, analyzer)
    return False


def _chain_field_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A method receiver whose PARENT is itself a field chain
    (`h.c.item.add(x)` -- recv is `h.c.item`, parent `h.c`): every link down
    to the innermost one-level field must be a plain-value F1-record member
    (each renders `.field` off the previous link, the chained THIRFieldAccess
    the value-read arm already emits), and the innermost link must satisfy
    `_field_receiver_ok`'s admitted binding set. Consumed at the method
    RECEIVER position and at the bare-lvalue structural-protocol ARG read
    (`len(h.c.item)`) -- both render the untouched chain. NEVER wired into
    the shared `_field_receiver_ok`: its read/write sinks carry deref/lift
    decisions a chain render does not, and widening it there tripped their
    fence pins (the reverted first cut)."""
    if not isinstance(recv, TpyFieldAccess) or not _field_markers_clean(recv):
        return False
    link = recv.obj
    while isinstance(link, TpyFieldAccess):
        lt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(link))))
        # A plain inline-stored record member only: Optional / Own / any
        # non-record link would carry its own unwrap on the AST path.
        if not (isinstance(lt, NominalType) and not isinstance(lt, OptionalType)
                and _f1_record(lt, analyzer)):
            return False
        if isinstance(link.obj, TpyName):
            return (_field_receiver_ok(link, locals_, analyzer)
                    and _witness("method.recv.field_chain"))
        if not _field_markers_clean(link):
            return False
        link = link.obj
    return False


def _method_field_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """A one-level field-access method receiver `x.field.method(...)`: the field
    is a plain value F1-record off an F1-record receiver name (self / a record
    param / REF_ALIAS / F2 pointer-local, or a proven Optional-ptr borrow name
    -- `_field_receiver_ok`'s admitted set). The field's record type routes the
    call to the user-record arm, and the receiver renders bare as its own
    THIRFieldAccess (`this->field.m()` / `p->field.m()`, the `.`/`->` decided by
    that inner node) -- the outer method access is `.` (is_arrow keys on a NAME
    receiver). Container / Optional / non-value field receivers are deferred: an
    Optional field would need the outer `(*obj)` / deref_check unwrap."""
    if (getattr(recv, "property_getter_call", None) is not None
            and isinstance(recv.obj, TpyName)
            and (_resolved_str_value(analyzer.get_expr_type(recv), analyzer)
                 is not None
                 or _resolved_bytes_value(analyzer.get_expr_type(recv),
                                          analyzer) is not None)):
        # A view-valued PROPERTY receiver (`self.text.encode()`): the read
        # lowers as its getter call and the view family composes over the
        # rvalue like a str/bytes method-call receiver.
        return _witness("method.recv.view_field")
    if not (_field_receiver_ok(recv, locals_, analyzer)
            or _indirect_field_receiver_ok(recv, locals_, analyzer)
            or _chain_field_receiver_ok(recv, locals_, analyzer)
            # A MODULE-VARIABLE receiver (`os.environ.update(...)`): the
            # pointer-slot module global's deref render is exactly the
            # receiver spelling (`(*::tpystd::os::_environ::environ)`),
            # already carried by the module-var read arm -- derived like
            # that arm, not stamped (sema leaves module_var_access unset
            # in receiver position).
            or ((_mv_mod := _bare_module_recv(recv.obj, locals_, analyzer))
                is not None
                and _module_var_read_cpp(_mv_mod, recv.field, analyzer)
                is not None)):
        return False
    ft = analyzer.get_expr_type(recv)
    # A container-family field routes the container arm: the receiver renders
    # bare as its own THIRFieldAccess (`this->buf` / `parent.children`), exactly
    # as a bare-name container receiver renders `xs` -- the append / pop / update
    # emit inserts that receiver identically. `_method_receiver_type` reads the
    # same resolved field type downstream (no PendingListType round-trip that the
    # name arm dodges via `locals_`), so gating on the SAME family predicate the
    # method-family dispatch uses keeps shape/arg admission coherent: whatever
    # admits here routes the container family there, and the per-method /
    # per-arg gates decide the rest (record-elem moves, set adds, pop results).
    if _container_method_recv(ft, analyzer, None):
        return _witness("method.recv.container_field")
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    # A protocol-typed or bounded-tparam field (`self.factory.make()` on
    # `factory: T` with `T: FooMaker`): routes the protocol family (the
    # family dispatch holds the record's tparam bounds), whose own receiver
    # check re-admits the field shape; the read renders bare. An UNBOUNDED
    # T field classifies into no family and defers at the record arm.
    if _protocol_binding(ft) is not None or isinstance(ft, TypeParamRef):
        return True
    # A str/bytes-family field (`self.payload.decode()`): routes the view
    # family over the bare member read.
    if (_resolved_str_value(ft, analyzer) is not None
            or _resolved_bytes_value(ft, analyzer) is not None):
        return _witness("method.recv.view_field")
    return (isinstance(ft, NominalType) and _f1_record(ft, analyzer)
            and _witness("method.recv.record_field"))

def _tuple_record_elem_subscript_recv(recv: TpyExpr,
                                      locals_: dict[str, TpyType],
                                      analyzer) -> bool:
    """A tuple-element F1-RECORD subscript method receiver (`t[0].get()` /
    `pair[0].get()`): `std::get<N>` yields the record element -- a bare `T*`
    off a borrow-form tuple param (`->` access) or a value/`T&` element off a
    storage tuple local (`.` access); the method node's arrow decision reads
    `_subscript_yields_borrow_ptr` so the two render identically to the AST.
    NAME receivers only, plain (non-Own, non-Optional) record elements only
    -- an Own element carries consuming semantics this row does not mirror."""
    if not isinstance(recv, TpySubscript) or recv.needs_optional_runtime_check:
        return False
    if not isinstance(recv.obj, TpyName):
        return False
    res = _subscript_index_and_tuple(recv, analyzer)
    if res is None:
        return False
    recv_t, idx = res
    et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        recv_t.element_types[idx])))
    return (isinstance(et, NominalType) and not isinstance(et, OptionalType)
            and not isinstance(et, OwnType)
            and _f1_record(et, analyzer)
            and _witness("method.recv.tuple_record_elem"))


def _method_nonname_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                                analyzer,
                                pointers: 'AbstractSet[str]' = frozenset()
                                ) -> bool:
    """A non-name method receiver `<recv>.method(...)`. Two shapes admit:
    a one-level field access (`_method_field_receiver_ok`), and a
    container-element-record subscript `xs[i].m()` -- the subscript is a
    plain-record borrow lvalue (`::tpy::__getitem__(xs, i)`, `.` access,
    the receiver renders as its own THIRSubscript exactly as an `ps[i].field`
    read does). `_method_receiver_type` reads the resolved element record type
    downstream, so the outer call routes the user-record arm. Deeper subscript
    chains and non-record elements reject at `_container_record_elem_subscript`."""
    if isinstance(recv, TpySubscript):
        # A container-element subscript receiver: an F1-record element
        # (`ps[i].m()`) OR a nested-container element (`d[k].append(x)`,
        # `m[i].append(x)`) -- both render `::tpy::__getitem__(c, k)` as the
        # receiver with `.` access, and `_method_receiver_type` reads the
        # resolved element type so the outer call routes the record / container
        # method arm respectively.
        return ((_container_record_elem_subscript(recv, locals_, analyzer)
                 # A user-record __getitem__ receiver returning a record
                 # borrow (`srv.sockets[0].getsockname()`): the raw
                 # operator[] lvalue takes `.` access -- the method arm's
                 # BORROW_BIND receiver lowering already renders the
                 # getitem read; only this admission was missing (a
                 # pointer-local receiver still rejects at the getitem
                 # arm's idx/recv recheck).
                 or (_record_getitem_key(
                         analyzer.get_expr_type(recv.obj), analyzer)
                     is not None
                     and _f1_record(analyzer.get_expr_type(recv), analyzer)
                     # The CALLER'S pointer set is load-bearing here: the
                     # method receiver lowers PRECHECKED, so the getitem
                     # arm's own idx/recv recheck never runs -- a
                     # pointer-local NAME obj admitted at this gate would
                     # route the bare render where the AST derefs
                     # (boundary-probed divergent, 2026-08-05).
                     and _record_getitem_idx_recv_ok(recv, locals_, analyzer,
                                                     pointers))
                 or _container_ref_alias_elem_subscript(recv, locals_, analyzer)
                 or _subscript_over_container_subscript_ok(
                     recv, locals_, analyzer)
                 or _tuple_container_elem_over_subscript_ok(
                     recv, locals_, analyzer)
                 # A str-element read (`argv[i].startswith(...)`): the element
                 # lvalue feeds the native str view-method positionally, the
                 # subscript rendering as its own THIRSubscript.
                 or _borrow_elem_subscript_shape(recv, locals_, analyzer,
                                                _container_str_elem)
                 # A str/bytes SLICE receiver (`data[0:11].split(b" ")`):
                 # the slice renders its own view rvalue, and the view
                 # family gates the method over it.
                 or (isinstance(recv.index, TpySlice)
                     and recv.slice_function_info is not None
                     and (_resolved_str_value(
                              analyzer.get_expr_type(recv), analyzer)
                          is not None
                          or _resolved_bytes_value(
                              analyzer.get_expr_type(recv), analyzer)
                          is not None))
                 or _tuple_record_elem_subscript_recv(recv, locals_,
                                                      analyzer))
                and _witness("method.recv.subscript"))
    if isinstance(recv, TpyStrLiteral):
        # A str-LITERAL receiver (`"a,b,c".split(",")`): the AST's builtin-
        # method receiver render is the bare literal (const char[N]) prepended
        # / substituted into the resolved template, which the general lowering
        # reproduces; `_method_receiver_type` reads the literal's str type so
        # the view arm gates the method itself.
        return _witness("method.recv.str_literal")
    if isinstance(recv, TpyBytesLiteral):
        # The bytes twin (`b"a,b,c".split(b",")`): the receiver renders
        # OWNED (`::tpy::bytes_literal_owned`), the `{self}` substitution
        # the slice arm already spells; the view family gates the method.
        return _witness("method.recv.bytes_literal")
    if isinstance(recv, TpyBinOp):
        # A record-result dunder-binop receiver (`(dt + td).isoformat()`):
        # the postfix member chains off the parenthesized template render on
        # both paths; the receiver lowers through the binop record-dunder
        # arm (RECEIVER use), whose operand gates still apply.
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(recv))))
        return bool(_f1_record(rt, analyzer)
                    and _witness("method.recv.binop"))
    if isinstance(recv, TpyMethodCall):
        return _method_call_receiver_ok(recv, locals_, analyzer)
    if isinstance(recv, TpyCall):
        # A free-call-result receiver `make(3).get()`: same verdict as the
        # method-call receiver arm -- a plain non-pointer F1-record result
        # keeps `.` access on both paths (probe-verified bare render); the
        # inner call lowers via the shared free-call machinery at
        # BORROW_BIND use. Pointer / Optional / non-record results defer.
        if (_resolved_str_value(analyzer.get_expr_type(recv), analyzer)
                is not None
                # The bytes twin (`make_bytes().strip()`).
                or _resolved_bytes_value(analyzer.get_expr_type(recv),
                                         analyzer) is not None):
            # A str/bytes-VALUE free-call result feeding a view-method (the
            # free-call twin of the literal receiver arms).
            return _witness("method.recv.str_method")
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(recv))))
        if isinstance(rt, OwnType):
            rt = unwrap_readonly(rt.wrapped)
        if not (isinstance(rt, NominalType) and _f1_record(rt, analyzer)):
            # A @dynamic-protocol call result: an `Own[P]` handle composes
            # `f()->m()` (the own-dyn arrow at the emit node), a borrow
            # `P&` result `f().m()` -- both off the bare inner render.
            if isinstance(rt, NominalType) and is_dyn_protocol(rt):
                return _witness("method.recv.dyn_call")
            return False
        return _witness("method.recv.free_call")
    return _method_field_receiver_ok(recv, locals_, analyzer)

def _method_call_receiver_ok(recv: TpyMethodCall, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A method-call method receiver `a.b().c()`: the inner call `a.b()` yields
    a plain non-pointer, non-Optional F1-record borrow (`Box.get()` -> `T&`),
    so the outer access renders `.` on both paths -- AST's `use_arrow` stays
    False (the receiver is not a name / pointer / Optional-ptr / own-dyn /
    borrow-`T*` tuple element), and the THIR outer node keeps `is_arrow` False
    (keyed on a NAME receiver). The inner call renders via the shared method
    lowering (`_lower_expr`), byte-identical to the AST's `gen_expr(recv)`.
    A pointer / Optional / non-record inner result reads `->` or the `(*obj)`
    unwrap on the AST path and is deferred."""
    if _resolved_str_value(analyzer.get_expr_type(recv), analyzer) is not None:
        # A str-VALUE method-call result (`s.strip().lower()`): the inner str
        # method renders bare (`::tpy::str_strip(...)`) and feeds the outer
        # str view-method's receiver slot positionally -- both native
        # free-function str methods composing as nested calls.
        return _witness("method.recv.str_method")
    if _resolved_bytes_value(analyzer.get_expr_type(recv), analyzer) is not None:
        # The bytes twin (`srv.recv(32).decode()` ->
        # `::tpy::bytes_decode(srv.recv(32))`): the inner bytes-returning
        # method feeds the outer bytes method's receiver slot positionally.
        return _witness("method.recv.bytes_method")
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    if isinstance(rt, NominalType) and _f1_record(rt, analyzer):
        return _witness("method.recv.method")
    # A CONTAINER-returning inner call (`b1.take().append(4)`,
    # `groups.setdefault("a", []).append(1)`): the inner render is the bare
    # call and the outer stub method appends onto it with `.` -- the same
    # composition as a record borrow, with the container family deciding the
    # outer call's own gates.
    if _container_method_recv(rt, analyzer, None):
        return _witness("method.recv.container_method")
    # A protocol-typed inner result (`box.get()` -> `Pet&`): a protocol borrow
    # renders `.` access exactly like an F1-record borrow (@dynamic virtual
    # dispatch, or a structural template ref), so the outer method call composes
    # byte-identically.
    if _protocol_binding(rt) is not None:
        return _witness("method.recv.protocol")
    return False

def _recv_shape_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                       analyzer) -> str:
    """Drilldown label for a non-admitted method receiver -- names *which*
    receiver shape blocks so the fallback tally ranks the follow-on cells
    (the method.receiver_shape total is first-reject-masked: one-level
    value-record fields, the admitted shape, are the rare part; the mass is
    non-F1-record fields and receiver chains)."""
    if isinstance(recv, TpySubscript):
        return "method.recv.subscript"
    if isinstance(recv, TpyCall):
        return "method.recv.call"
    if isinstance(recv, TpyMethodCall):
        return "method.recv.method"
    if isinstance(recv, TpyFieldAccess):
        if not isinstance(recv.obj, TpyName):
            return "method.recv.field_chain"
        # A one-level field whose parent receiver is not itself an admitted
        # F1-record binding (a container elem, a non-slice local, ...).
        if not _field_receiver_ok(recv, locals_, analyzer):
            return "method.recv.field_parent"
        ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(recv))))
        if isinstance(ft, OwnType):
            ft = unwrap_readonly(ft.wrapped)
        if isinstance(ft, OptionalType):
            return "method.recv.field_optional"
        if isinstance(ft, NominalType):
            return "method.recv.field_nonf1"  # non-F1 / native / generic record
        return "method.recv.field_nonrecord"  # container / str / tuple / ...
    return "method.recv.other"

def _marker_reject(e: TpyMethodCall, analyzer) -> str:
    """Drilldown label for a marker-rejected method call -- names WHICH
    special-emit marker fires (each takes a different _gen_method_call arm)
    so the fallback tally ranks the marker mass by arm instead of one
    opaque method.marker bucket. Module-qualified calls sub-split by callee
    kind (they reduce to the free-callee emit family)."""
    if e.kwargs or e.double_star_unpack is not None:
        return "method.marker.kwargs"
    if e.typed_dict_get_field is not None:
        return "method.marker.typed_dict"
    if e.is_nested_constructor or e.is_nested_enum_constructor:
        return "method.marker.nested_ctor"
    if e.is_callable_field:
        return "method.marker.callable_field"
    if e.macro_expansion is not None:
        return "method.marker.macro"
    if e.fstr_expansion is not None:
        return "method.marker.fstr"
    if e.super_parent_type is not None or e.unbound_self_parent_type is not None:
        return "method.marker.super"
    if e.user_module_call is not None or e.builtin_module_call is not None:
        base = ("method.marker.module_static" if e.is_static_call
                else "method.marker.module" if e.user_module_call is not None
                else "method.marker.builtin_module")
        fi = e.resolved_function_info
        if fi is None:
            return base + ".unresolved"
        if fi.is_method and fi.name == "__init__":
            return base + ".ctor"
        if e.type_args or e.inferred_type_args or fi.type_params:
            return base + ".generic"
        if fi.cpp_template:
            return base + ".template"
        if fi.native_function or fi.native_name or fi.is_native_import or fi.is_extern_c:
            return base + ".native"
        return base + ".plain"
    if e.is_static_call:
        fi = e.resolved_function_info
        if fi is None:
            return "method.marker.static.unresolved"
        if e.type_args or e.inferred_type_args or fi.type_params:
            return _static_generic_reject(e, fi, analyzer)
        if fi.cpp_template:
            return "method.marker.static.template"
        if fi.native_function or fi.native_name:
            return "method.marker.static.native"
        return "method.marker.static.plain"
    if e.type_args or e.inferred_type_args:
        return "method.marker.type_args"
    if e.deref_depth or e.deref_narrowed_to is not None:
        return _deref_marker_reject(e, analyzer)
    return "method.marker.other"

def _static_generic_reject(e: TpyMethodCall, fi, analyzer) -> str:
    """Sub-split of a generic static call by what its AST spelling needs:
    the cpp_template expansion (gen_call_from_fi), NO explicit type args
    (the `if not expr.inferred_type_args` arm -- static_method_callee_cpp,
    the same spelling the plain slice already mirrors), or explicit `<T>`
    renders at the class / method level (type_to_cpp respectively
    _render_method_type_arg -- the generics frontier). `_dep` marks type
    args still containing a TypeParamRef (the dependent `template `
    keyword decision rides them)."""
    base = "method.marker.static.generic"
    if fi.cpp_template:
        # Positional-only templates are admitted upstream (_marker_call_kind),
        # so this tag names the residue: a surviving {T}/{cpp} placeholder
        # needing the substitution machinery.
        return base + ".template_typed"
    targs = e.type_args or e.inferred_type_args
    if not targs:
        return base + ".no_targs"
    rec = (analyzer.registry.get_record(e.obj.name)
           if isinstance(e.obj, TpyName) else None)
    n_class = len(rec.type_params) if rec is not None and rec.type_params else 0
    kind = (".both_targs" if targs[:n_class] and targs[n_class:]
            else ".class_targs" if targs[:n_class] else ".method_targs")
    dep = "_dep" if any(contains_type_param(t) for t in targs) else ""
    return base + kind + dep

def _deref_marker_reject(e: TpyMethodCall, analyzer) -> str:
    """Sub-split of a Deref-chain method call by its _gen_method_call arm:
    the narrowed-payload cast (_gen_deref_view_method_call), the Ptr[T]
    receiver arm (`p->m` / `::tpy::deref_check(p).m` -- no `.__deref__()`
    spelling), the builtin arm (cpp_template / native fi through
    gen_method_from_function_info), or the plain member tail
    (`recv.__deref__()...m(args)` over the _args() loop) -- split by
    receiver shape (bare name vs field/chain)."""
    if e.deref_narrowed_to is not None:
        return "method.marker.deref.narrowed"
    fi = e.resolved_function_info
    if fi is None:
        return "method.marker.deref.unresolved"
    if fi.cpp_template is not None or fi.native_function:
        return "method.marker.deref.builtin"
    recv_t = analyzer.get_expr_type(e.obj)
    if recv_t is not None:
        recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t)))
    if recv_t is not None and recv_t.is_pointer():
        return "method.marker.deref.ptr"
    if not isinstance(e.obj, TpyName):
        return "method.marker.deref.recv_shape"
    return "method.marker.deref.plain"

def _template_kind(fi, e: TpyMethodCall) -> 'tuple[str, str] | None':
    """The ("template", expanded) kind for a @cpp_template callee whose
    expansion is positional-only -- the render `gen_template_or_native_call`
    produces. None when a `{T}`/`{cpp}` placeholder survives, i.e. the call
    needs the substitution machinery this slice does not carry."""
    tmpl = (expand_fi_template(fi, e.inferred_type_args)
            if e.inferred_type_args else fi.cpp_template)
    if _positional_only_template(tmpl, len(e.args)):
        return ("template", tmpl)
    return None

def _marker_call_kind(e: TpyMethodCall, analyzer, *,
                      generator_ok: bool = False,
                      coro_factory_ok: bool = False,
                      error_return_ok: bool = False) -> 'tuple[str, str] | None':
    """Classify a marker-carrying method call whose emit is RECEIVER-LESS --
    module-qualified (`m.f(x)`) or same-module static (`Rec.m(x)`) -- into
    its THIRCall emit kind + pre-rendered payload, the ONE routing fact
    consumed by lowering (the `_free_callee_kind` analog for
    `_gen_method_call`'s marker arms): ("qualified", callee_cpp) the
    `<spelling>(args)` render whose args are `_args()`'s full first-pass
    loop (plain cross-module calls, via `module_qualified_callee_cpp`;
    plain static methods, via `static_method_callee_cpp`; and plain
    MODULE-qualified statics, via `module_static_class_cpp` -- probe-verified
    to interpolate the same loop: the Own move cascade fires); ("native",
    symbol) the `::symbol(args)` render for a bare-@native cross-module
    callee -- the same loop but with `inline_template` set (`_is_native_
    stub`), which skips the Own copy-temp, so Own-slot args are rejected by
    the caller; ("template", tmpl) a same-module static `@cpp_template`
    call (`UInt32.trunc(i)`) whose positional-only template expands over
    gen_template_or_native_call's builtins arg loop -- generic statics
    included, since the no-{T} template makes gen_call_from_fi's type-arg
    substitution a no-op. None = an
    emit arm the slice does not reproduce (super / typed-dict / macro /
    deref markers, module statics' cpp_template/native/non-default-linkage
    rows, `<T>`-spelled generics, ctors,
    extern-C / @native_c raw symbols, `function=True` natives whose args
    render slot-BLIND via gen_expr_deref, non-static cpp_template and
    builtin-module arms)."""
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Every OTHER special marker takes its own _gen_method_call arm.
    # EXPLICIT type args are rejected here; INFERRED ones flow to the
    # per-branch generic decisions below.
    if (e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None
            # EXPLICIT type args are rejected -- EXCEPT where sema already
            # folded the explicit spelling into the inferred list and the
            # AST arm renders from `inferred_type_args` alone: a STATIC call
            # (`Poll[Probe].ready(..)`), a module-qualified generic
            # (`helpers.identity[Int32](x)` -- the user-module generic arm),
            # and a builtin-module template (`tpy.unsafe.unsafe_cast[U](p)`
            # -- gen_call_from_fi's type-arg substitution). The PREFIX pin
            # keeps any diverging explicit list out: a partial explicit
            # spelling (`unsafe_cast[UInt32](p)` giving one of [T, U]) folds
            # as the inferred list's head, and the render still comes from
            # `inferred_type_args` alone.
            or (e.type_args
                and not ((e.is_static_call
                          or e.user_module_call is not None
                          or e.builtin_module_call is not None)
                         and tuple(e.type_args)
                         == tuple(e.inferred_type_args)[:len(e.type_args)]))
            or e.deref_depth
            or e.deref_narrowed_to is not None
            or e.needs_optional_runtime_check):
        return None
    fi = e.resolved_function_info
    if fi is None:
        return None
    # A module-qualified record ctor (`m.Rec(...)`) resolves to __init__ --
    # the record-ctor frontier, not this arm. EXCEPT the builtin-module
    # @cpp_template TYPE ctor (`tpy.Int32(10)`): its AST arm is the same
    # gen_template_or_native_call expansion the static template takes
    # (non-generic overloads only, mirroring that arm's `not fi.type_params`
    # guard; the specials/native rejects below still apply).
    if fi.is_method and fi.name == "__init__":
        if not (e.builtin_module_call is not None
                and fi.cpp_template is not None
                and not fi.type_params
                and not fi.native_function and not fi.native_name):
            return None
    # Bespoke sema/emit arms keyed on the resolved function: the four
    # @builtin_function specials and special-handling builtins. The asyncio
    # spawn pair (run/create_task) is NOT bespoke at emit time: sema analyzed
    # it like any module function (plus the coroutine-arg contract), so it
    # rides the generic_qualified kind below; its Own[Cancellable[T]] arg is
    # judged by the coro-factory arg row (_dyn_own_coro_factory_arg).
    if fi.qualified_name in _SPECIAL_BUILTIN_QNAMES or fi.special_handling:
        return None
    if (fi.is_consuming
            or (fi.error_return_type is not None and not error_return_ok)
            or fi.native_cpp_return_type is not None
            # `coro_factory_ok` lifts ONLY the async-callee reject (the
            # adapter-wrap position consumes the frame whole), mirroring
            # _free_callee_kind's flag. `error_return_ok` is the
            # statement-handled twin: the unwrap belongs to the enclosing
            # statement, so the call renders as the bare marker call.
            or (fi.is_async and not coro_factory_ok)
            or (fi.is_generator and not generator_ok)
            or fi.is_property_getter or fi.is_property_setter
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if fi.is_generator and e.is_static_call:
        # The static spelling is unprobed against generator fis.
        return None
    if fi.is_generator and (fi.type_params or e.inferred_type_args):
        # A GENERIC module-qualified factory (`heapq.merge(a, b)` ->
        # `::tpystd::heapq::merge<Item>(...)`): the generic_qualified kind
        # composes the targs at lowering exactly like a non-generator
        # generic module call, so `generator_ok` covers it; an unresolved
        # targ list still rejects at that kind's own checks below.
        if not generator_ok:
            return None
    parent = e.super_parent_type or e.unbound_self_parent_type
    if parent is not None:
        # `super().m(args)` / `Base.m(self, args)` -> `this->Base::m(args)`
        # (_gen_method_call's super/unbound-self arms; sema strips `self` from
        # the unbound form's args). The parent spells via its own `to_cpp()`
        # -- the AST's exact call -- so an F1 base and a generic instance
        # (`Base<T>` inside a generic child) are both byte-safe. The GENERIC
        # method form (`this->Base<T>::template m<U>(args)`) rides its own
        # kind so lowering composes the targs via lc.render_type; native /
        # cpp_template spellings stay AST.
        if (fi.cpp_template is not None or fi.native_function
                or fi.native_name
                or fi.linkage != FunctionLinkage.DEFAULT
                or not isinstance(parent, NominalType)):
            return None
        if fi.type_params or e.inferred_type_args:
            if (not e.inferred_type_args
                    or len(e.inferred_type_args) != len(fi.type_params or ())
                    or not all(isinstance(t, TpyType)
                               for t in e.inferred_type_args)):
                return None
            return ("super_generic",
                    f"this->{parent.to_cpp()}::template "
                    f"{escape_cpp_name(e.method)}")
        if not _f1_record(parent, analyzer):
            # The non-generic form keeps its F1 pin (a native/cross-module
            # base may spell differently than bare to_cpp).
            return None
        return ("qualified",
                f"this->{parent.to_cpp()}::{escape_cpp_name(e.method)}")
    if e.is_static_call:
        if e.builtin_module_call is not None:
            return None
        if e.user_module_call is not None:
            # The module-qualified static arm (`m.Cls.m(args)`): the GENERIC
            # form is composed at lowering (`::tpyapp::m::Cls<CA>::template
            # m<MA>(args)`); a template fi expands through the shared
            # substitution; the PLAIN form renders the same qualified callee
            # with no targs (`::tpyapp::m::Cls::m(args)` -- the AST arm's
            # tail with empty targs/template_kw), so it rides the
            # "qualified" kind. A cpp_template plain fi takes the AST's
            # gen_call_from_fi arm (different arg render) -- excluded.
            if not isinstance(e.obj, TpyFieldAccess):
                return None
            if getattr(e, "representational_subst_params", None):
                return None
            if not e.inferred_type_args:
                if (fi.cpp_template is not None or fi.native_function
                        or fi.linkage != FunctionLinkage.DEFAULT):
                    return None
                cpp_class = module_static_class_cpp(
                    analyzer.registry, e.user_module_call, e.obj.field)
                cpp_method = (fi.native_name if fi.native_name
                              else escape_cpp_name(e.method))
                return ("qualified", f"{cpp_class}::{cpp_method}")
            if fi.cpp_template is not None:
                return _template_kind(fi, e)
            if (fi.native_function
                    or fi.linkage != FunctionLinkage.DEFAULT):
                return None
            return ("generic_module_static", "")
        if not isinstance(e.obj, TpyName):
            return None
        # A @cpp_template static takes the builtins arm (_gen_method_call's
        # native/template block precedes its `Class::m` arm):
        # gen_template_or_native_call expands the template over
        # gen_call_arg(inline_template) args; a GENERIC static template
        # (`Poll.ready[T]`-style) substitutes its {T} placeholders through
        # the shared expand_fi_template first. Positional-only results only.
        if fi.cpp_template is not None:
            return _template_kind(fi, e)
        if e.inferred_type_args or fi.type_params:
            # A generic static call spells the class/method targs split
            # (`Cls<CA>::template m<MA>(args)`); the renders need the
            # resolver, so lowering composes the spelling
            # (_lower_generic_static_callee), including the repr-subst
            # Adapter override on marked METHOD params
            # (`Rc<Pet>::new_<::tpy::Adapter<Pet, Cat>>(...)` -- the
            # `_static_repr_subst_cpp` mirror). A NATIVE record's static
            # render is targ-blind (`cpp_class::method(args)`) and rides
            # the plain qualified kind.
            if not e.inferred_type_args:
                return None
            ri = (analyzer.registry.get_record(e.obj.name)
                  or e.static_call_owner)
            if ri is not None and ri.is_native:
                cpp_method = (fi.native_name if fi.native_name
                              else escape_cpp_name(e.method))
                return ("qualified", f"{ri.native_name}::{cpp_method}")
            if fi.native_function or fi.native_name:
                return None
            if fi.linkage != FunctionLinkage.DEFAULT:
                return None
            return ("generic_static", "")
        # A native static fi takes the receiver-threaded builtin
        # arm (gen_method_from_function_info) -- not the `Class::m` render.
        if fi.native_function or fi.native_name:
            return None
        if fi.linkage != FunctionLinkage.DEFAULT:
            return None
        compiler = get_current_compiler()
        implicit = (compiler._implicit_stdlib_set() if compiler is not None
                    else set())
        return ("qualified", static_method_callee_cpp(
            analyzer.registry, implicit, analyzer.ctx.module_name,
            e.obj.name, e.method, fi, owner=e.static_call_owner))
    if e.builtin_module_call is not None and fi.cpp_template is not None:
        # A builtin-module function/type call (`tpy.unsafe.unsafe_ptr(arr)`,
        # `tpy.Int32(10)`): every @cpp_template branch of the AST's
        # builtin-module arm routes through gen_template_or_native_call with
        # the RESOLVED fi, i.e. the same expansion the same-module static
        # template takes. The @native branches render a receiver-threaded or
        # gen_call_arg loop instead and stay out.
        if fi.native_function or fi.native_name:
            return None
        return _template_kind(fi, e)
    if fi.cpp_template is not None:
        return None
    if e.builtin_module_call is not None:
        return None
    if e.user_module_call is None:
        return None
    if fi.is_native:
        # Bare-@native cross-module callee (`m.sqrt(x)` -> `::std::sqrt(x)`).
        # `function=True` natives take the slot-blind gen_expr_deref arg
        # render instead (_skip_first_pass) -- a different loop, stays AST.
        # Targ-blind like the free-call native arm: the AST never spells
        # explicit template args for a native import.
        if fi.native_function:
            return None
        return ("native", fi.native_name or fi.name)
    if (fi.is_extern_c or fi.is_native_c or fi.native_function
            or fi.native_name):
        return None
    if fi.linkage != FunctionLinkage.DEFAULT:
        return None
    cpp = module_qualified_callee_cpp(
        analyzer.registry, analyzer.ctx.module_attributes,
        analyzer.ctx.module_name, e.user_module_call, e.method, fi)
    if e.inferred_type_args or fi.type_params:
        # A module-qualified generic call spells explicit template args
        # over the SAME qualified callee (`::tpyapp::m::gf<int32_t>(args)`,
        # targs = type_to_cpp(unwrap_ref_type) per inferred arg -- NOT the
        # free-call arm's to_cpp_stored); the args are the same qualified
        # first-pass loop over the RESOLVED (substituted) fi params.
        if (not e.inferred_type_args
                or getattr(e, "representational_subst_params", None)):
            return None
        return ("generic_qualified", cpp)
    return ("qualified", cpp)

def _stub_template_param(pt: 'TpyType | None') -> bool:
    """A param type that makes its @overload stub emit through the template-
    header path (a protocol / Fn param synthesizes type params), so its call
    site is not the plain named call this gate admits."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
         if isinstance(pt, TpyType) else None)
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    elif isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    if t is None:
        return False
    return is_fn_type(t) or is_protocol_type(t) or is_dyn_protocol(t)


def _marker_call_supported(e: TpyMethodCall, kind: 'tuple[str, str]',
                          locals_: dict[str, TpyType], analyzer,
                          *, stmt_position: bool = False,
                          temps_ok: bool = False,
                          record_ret_ok: bool = False,
                          moved_ret_ok: bool = False,
                          iterable_gen_ok: bool = False,
                          owned_tuple_ret_ok: bool = False,
                          storage_ret_ok: bool = False,
                          value_opt_ret_ok: bool = False,
                          coro_factory_ok: bool = False,
                          iterable_ret_ok: bool = False,
                          narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """Result/arg checks for a `_marker_call_kind`-classified receiver-less
    call. Mirrors free-call lowering's value-position result set and its arg
    rows MINUS the free-loop-only ref-temp hoist (`_record_rvalue_temp_arg`:
    the method-call loop's hoist condition is protocol/TypeParamRef only, so
    a record rvalue into a concrete ref slot renders differently) -- and,
    for the "native" kind, minus the Own rows (`inline_template` skips the
    copy-temp for a non-last-use lvalue). Union lifts are admitted dcbp-BLIND
    on BOTH sides: the method-call loop calls `_gen_union_arg(arg, ptype)`
    without the deep-const verdict, and lowering passes readonly_target=False
    to match."""
    fi = e.resolved_function_info
    if not _call_arity_ok(e, fi):
        # Omitted trailing defaults ride the C++ signature's defaults
        # (`datetime.now()` / `HTTPConnection("h", 80)` -- the shared
        # `_call_arity_ok` rule); a defaultless or variadic tail rejects.
        return note_detail("method.qualcall.arity_defaults")
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            # An open-T result (`val_or_cref_t<T> load_at(..)`): the
            # form-neutral slot renders the bare call, exactly as the
            # record-method gate's own `_tparam_value` row does. The two
            # gates' result disjunctions must stay in step.
            or (_tparam_value(ret)
                and _witness("method.qualcall.ret_tparam"))
            # The field-receiver position (`p.Box(10).n`): an F1-record
            # result renders bare under the postfix member.
            or (record_ret_ok and _f1_record(ret, analyzer))
            # The SUSPEND position (an ERASED await operand): the skeleton
            # immediately moves the result into the sub-future slot, so any
            # record-family result (incl. generic-concrete, e.g.
            # Own[Task[None]]) renders bare -- no value slot is involved.
            or (moved_ret_ok and _moved_record_ret(ret, analyzer))
            # A module-qualified generator factory in iterable position: the
            # Iterator-protocol result feeds the iter_proto route's `auto
            # __src_N` capture, never a typed value slot.
            or iterable_gen_ok
            # The standalone tuple-unpack SOURCE (`a, b = m.pair()`): the
            # `auto __tup_N =` capture takes the result whole, no value slot.
            or (owned_tuple_ret_ok
                and _owned_tuple_call_ret(ret, analyzer) is not None)
            # A container return at a STORAGE sink (`print(re.split(..))` --
            # the print wrap / owned-decl slot take the bare call), the
            # marker twin of free-call lowering's storage_ret_ok escape.
            or (storage_ret_ok
                and _storage_call_ret(ret, analyzer) is not None)
            # An owned-optional record return (`Container.wrap_optional(v)
            # -> Own[Container[T]] | None`) at a STORAGE sink lands bare in
            # its by-value `std::optional<T>` decl slot -- the record-method
            # ladder's twin row, STORAGE-gated for the same aliasing reason.
            or (storage_ret_ok
                and _storage_optional_return_type(
                        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                        if isinstance(ret, TpyType) else None,
                        analyzer) is not None
                and _witness("method.qualcall.storage_opt_ret"))
            # A value-repr Optional return at a WHOLE-optional sink
            # (`os.getenv("X") is None` -- the has_value render takes the
            # bare call), the record-method row's marker twin.
            or (value_opt_ret_ok and _value_opt_ret(ret))
            or (stmt_position and (ret is None or is_void_like_type(ret)))
            # A DISCARDED record-family result (`asyncio.create_task(...);`
            # -- the Task handle dropped at statement position): the render
            # is the same bare call whatever the ignored result, the
            # qualcall twin of the record-method discard row. Containers
            # stay out (storage/borrow duality; no oracle witness).
            or (stmt_position and _moved_record_ret(ret, analyzer)
                and _witness("method.qualcall.record_discard"))
            # A DISCARDED container result (`os.listdir(path);` for its
            # errors alone): no consumer exists, so the storage/borrow
            # duality that keeps containers out of the value positions
            # cannot bite -- the render is the same bare call statement.
            or (stmt_position and _nonvalue_container_ret(ret)
                and _witness("method.qualcall.container_discard"))
            # A record-family RVALUE result consumed whole by a storage sink
            # (`t1 = asyncio.create_task(reader(b1))` -- the frame-slot
            # emplace / owned decl takes the bare call): record_ret_ok at
            # STORAGE already pinned rvalue-source-ness; the F1 row above
            # keeps the borrow-bind/receiver positions F1-only.
            or (storage_ret_ok and record_ret_ok
                and _moved_record_ret(ret, analyzer)
                and _witness("method.qualcall.record_storage"))
            # A container return in ITERABLE position (`for e in
            # os.scandir(base)`): the for-each capture takes the bare call
            # whole (`auto __obj_N = ...`) -- the record-method ladder's
            # iterable_ret_ok twin, no value slot involved.
            or (iterable_ret_ok and _nonvalue_container_ret(ret)
                and _witness("method.qualcall.container_iterable"))
            # A module-qualified coro FACTORY at the adapter-wrap position
            # (`create_task(asyncio.wait_for(...))`): the erased dyn-protocol
            # result is consumed whole by make_adapter -- no value slot; the
            # record-method ladder's coro_factory_ok twin.
            or (coro_factory_ok and ret is not None
                and is_dyn_protocol(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret)))))):
        return note_detail(_qualcall_ret_reject(ret, analyzer))
    return True


def _container_field_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType], analyzer) -> bool:
    """A container FIELD read at a marker callee's plain container ref slot
    (`heapq.heappush(self.heap, ...)` -> `heappush<T>(this->heap, ...)`):
    the member read binds the `std::vector<T>&` slot directly -- bare on
    both paths, the field twin of `_container_pass_through_arg`'s bare-NAME
    row. Keyed on the DECLARED field type being a bare container of the
    slot's family (a narrowed `Optional[container]` field types its
    occurrence at the member and would need the unwrap the AST renders).
    The slot may be the callee's still-generic `list[T]` -- the AST threads
    it RAW, and no wrap arm claims a plain container slot, so admission
    needs no substitution; Own/Optional/Span slots keep their lifts."""
    if not isinstance(a, TpyFieldAccess):
        return False
    if not _field_receiver_ok(a, locals_, analyzer):
        return False
    ft = _field_decl_type(a, locals_, analyzer)
    ftu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
           if isinstance(ft, TpyType) else None)
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    ok = ((is_list(ftu) and is_list(pt))
          or (is_dict(ftu) and is_dict(pt))
          or (is_set(ftu) and is_set(pt))
          or (is_array(ftu) and is_array(pt)))
    return bool(ok and _witness("arg.container_field_marker"))


def _own_tparam_method_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  analyzer) -> bool:
    """An Own[T]-returning method-call RVALUE at an OPEN `Own[T]` method slot
    of the SAME T (`self._storage.init(ui, other._storage.take(ui))` inside
    a generic record body): the prvalue binds the `T&&` slot bare -- the Own
    cascade's rvalue tail -- and the same-T pairing mirrors the generic free
    lane's open-slot rule. A borrow-returning callee is not an rvalue source
    (the AST copies it through a temp) and stays out; the nested call
    validates itself at its own lowering."""
    slot = unwrap_send_sync(ptype) if isinstance(ptype, TpyType) else None
    if not isinstance(slot, OwnType):
        return False
    st = unwrap_readonly(slot.wrapped)
    if not isinstance(st, TypeParamRef):
        return False
    if not isinstance(a, TpyMethodCall):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    return bool(isinstance(atu, TypeParamRef) and atu.name == st.name
                and is_rvalue_source(analyzer, a)
                and _witness("arg.own_tparam_method_rvalue"))


def _marker_call_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                        kind: 'tuple[str, str]',
                        locals_: dict[str, TpyType], analyzer, *,
                        temps_ok: bool,
                        narrowed: 'set[str] | frozenset[str]') -> bool:
    if kind[0] == "template":
        return (_shared_pass_through_arg(a, ptype, locals_, analyzer)
                or note_detail(_native_arg_reject(a, ptype, analyzer)))
    own_ok = kind[0] in ("qualified", "generic_qualified", "generic_static",
                         "generic_module_static", "super_generic")
    return (_shared_pass_through_arg(a, ptype, locals_, analyzer)
            # A named function passed as a value (`os.walk(missing,
            # onerror=boom)`): `_function_ref_name`'s plain render, identical
            # in the qualcall `_args()` loop and the free-call loop (both
            # reach it through gen_expr) -- the free-call ladder's slot-blind
            # row.
            or _func_ref_routable(a, analyzer)
            # A whole value-repr Optional[Callable] name into a matching
            # value-opt slot passes bare (`os.walk(top, onerror=cb)`).
            or _value_opt_callable_pass_arg(a, ptype, locals_, narrowed,
                                            analyzer)
            or _none_unit_arg(a, ptype) is not None
            or (temps_ok and _value_union_temp_arg(
                a, ptype, locals_, narrowed, analyzer))
            or (own_ok and _own_record_rvalue_arg(a, ptype, locals_, analyzer))
            # `Factory.consume(copy(p))` -- the static-method face of the
            # copy-construct rvalue row.
            or (own_ok and _copy_record_own_arg(a, ptype, analyzer))
            or (own_ok and _own_move_arg(a, ptype, locals_, analyzer))
            or (own_ok and temps_ok and _own_lvalue_arg(
                a, ptype, locals_, narrowed, analyzer))
            or _optional_ptr_arg(a, ptype, locals_, analyzer,
                                 temps_ok=temps_ok)
            # A record name moved into an Optional[Own[T]] slot
            # (`_urlopen(..., conn)` -> bare `std::move(conn)`); the
            # lowering enforces the move verdict.
            or _opt_own_record_name_arg(a, ptype, locals_, analyzer)
            is not None
            or _readonly_record_ctor_arg(a, ptype, locals_, analyzer)
            or _union_pass_through_arg(a, ptype, locals_, analyzer)
            or _union_member_lift_arg(a, ptype, locals_, analyzer)
            or _union_coerced_literal_arg(a, ptype, locals_, analyzer)
            # A member-typed arg into a VALUE-repr Optional slot
            # (`socket.create_connection(addr, 2.0)` at `float | None`,
            # `io.BytesIO(b"xyz")` at `bytes | None`): gen_call_arg has no
            # value-optional arm at all, so the arg falls to its generic tail
            # and the optional's converting ctor absorbs the bare member
            # render -- position-blind, and `_lower_call_arg`'s own tail
            # mirrors it. The free-call ladder's row.
            or _value_opt_member_arg(a, ptype, locals_, analyzer)
            # A bare `None` into a value-repr Optional slot renders
            # `std::nullopt` whatever the inner -- the sema-filled default at
            # a kwargs call site arrives exactly this way (`os.walk(root,
            # followlinks=f)` fills `onerror=None` at the
            # `Optional[Callable]` slot). The ctor/record-method ladders' row.
            or _none_value_opt_arg(a, ptype, analyzer) is not None
            # A list/dict LITERAL into a recursive-union wrapper slot
            # (`json.dumps([1, 2, 3])`): `_gen_union_arg`'s value branch
            # hoists `JsonValue __tmp_N = <literal>;` -- flush-gated like
            # the value-union temp row.
            or (temps_ok and _ru_wrapper_arg_slot(ptype) is not None
                and _ru_container_literal_ok(a, analyzer))
            # A wrapper-union NAME at a same-wrapper slot (`json.dumps(v)`
            # on `v: JsonValue`): the binding is already the wrapper struct
            # -- passes bare like a same-union name.
            or _ru_wrapper_name_arg(a, ptype, locals_, narrowed, analyzer)
            or (own_ok and _own_union_ctor_arg(
                a, ptype, locals_, analyzer))
            or (own_ok and _dyn_own_coro_factory_arg(a, ptype, analyzer)
                is not None)
            or (own_ok and _dyn_own_handle_arg(a, ptype, locals_, analyzer)
                is not None)
            # An Own[P]-returning call rvalue at the Own[@dynamic P] slot
            # (`create_task(factory(7))` through a std::function binding):
            # the unique_ptr result forwards bare -- the free ladder's row.
            or (own_ok and _dyn_own_forward_call_arg(a, ptype, analyzer)
                is not None)
            # A container LITERAL into a matching builtin-container slot: a
            # qualified module function (os.path.commonprefix([...])) renders
            # the spelled container inline, like the stub-method arg loop -- no
            # ref-param temp hoist (a FREE call would hoist, but the qualcall
            # arg loop emits it in place).
            or _container_literal_method_arg(a, ptype, analyzer)
            # A container FIELD read binding a plain container ref slot
            # (`heapq.heappush(self.heap, ...)` -> bare `this->heap`; the
            # Own[T] item slot arrives SUBSTITUTED from sema, so the ctor
            # rvalue beside it rides the existing own.record_rvalue row).
            or _container_field_pass_arg(a, ptype, locals_, analyzer)
            # A NAME bound to the SAME bare type param as the slot
            # (`super().transform(other)` at `other: U`): the form-neutral
            # slot binds the name bare -- the generic free lane's same-T
            # rule (`inner_len<T>(x)`).
            or (isinstance(
                    (_pt := unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        ptype)))) if isinstance(ptype, TpyType) else None,
                    TypeParamRef)
                and isinstance(a, TpyName) and a.name != "self"
                and isinstance(
                    (_au := unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        locals_.get(a.name)))))
                    if isinstance(locals_.get(a.name), TpyType) else None,
                    TypeParamRef)
                and _au.name == _pt.name
                and _witness("arg.same_tparam_name"))
            or (own_ok and _own_container_literal_arg(a, ptype, analyzer))
            # An F1-record call rvalue into a plain record slot
            # (`os.path.samestat(s, os.stat(d))` -- the nested marker call
            # renders bare in place, the qualcall twin of the native row).
            or _native_record_call_arg(a, ptype, analyzer)
            # A protocol slot's temp-free NAME / flushable temp faces
            # (`math.dist([0.0, 0.0], [3.0, 4.0])` -- the structural
            # rvalue's `auto __tmp_N =` hoist), the plain-loop row.
            or _protocol_slot_arg(a, ptype, locals_, analyzer,
                                  temps_ok=temps_ok)
            or note_detail(_qualcall_arg_reject(a, ptype, analyzer)))


def _dyn_own_handle_arg(a: TpyExpr, ptype: 'TpyType | None',
                        locals_: dict[str, 'TpyType'],
                        analyzer) -> 'NominalType | None':
    """A BOUND coroutine-handle NAME into an `Own[@dynamic P]` slot
    (`asyncio.create_task(c)`): _gen_dynamic_protocol_own_arg's handle face
    -- `::tpy::make_adapter<Base>(std::move(*(c)))`, the optional-slot
    unwrap moved into the adapter. Returns the slot protocol, or None.
    An already-ERASED source (an `Own[Cancellable]` param forwarded)
    renders without the re-wrap -- a different face, sliced out."""
    if not isinstance(ptype, TpyType) or not isinstance(a, TpyName):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, like the AST's `_gen_dynamic_protocol_arg` key: a
    # ReadonlyType wrapper defeats is_dyn_protocol there, so the AST never
    # takes the adapter render for an `Own[readonly[P]]` slot -- unwrapping
    # here would route-and-diverge (probe-verified).
    proto = u.wrapped
    if not (isinstance(proto, NominalType) and is_dyn_protocol(proto)):
        return None
    at = locals_.get(a.name)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if isinstance(at, TpyType) else None)
    # The handle's declared spelling varies by scope source: generator_locals
    # carry Own[ConcreteCoroType], a decl-site registration the bare
    # ConcreteCoroType -- both are the same optional-slot handle.
    if isinstance(at_u, OwnType):
        at_u = unwrap_readonly(at_u.wrapped)
    if not isinstance(at_u, ConcreteCoroType):
        return None
    return proto


def _dyn_own_coro_factory_arg(a: TpyExpr, ptype: 'TpyType | None',
                              analyzer) -> 'NominalType | None':
    """A DIRECT async-def factory call into an `Own[@dynamic P]` slot
    (`asyncio.run(main_coro())`): _gen_dynamic_protocol_own_arg's erasure
    boundary -- `::tpy::make_adapter<Base>(factory(args))`, Base spelled
    from the SLOT protocol (dynamic_base_name, the helper the AST render
    shares). Returns that protocol, or None. Free-call factories only: a
    BOUND handle name takes the `std::move(*(x))` optional-slot unwrap
    (`_dyn_own_handle_arg`) and a method coro threads its receiver -- a
    different render, AST. The factory itself must classify plain/imported
    (`coro_factory_ok`); its own args are judged by the free-call loop at
    lowering."""
    if not isinstance(ptype, TpyType) or not isinstance(a, (TpyCall,
                                                            TpyMethodCall)):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, matching the AST key (see _dyn_own_handle_arg): an
    # `Own[readonly[P]]` slot never takes the adapter render there.
    proto = u.wrapped
    if not (isinstance(proto, NominalType) and is_dyn_protocol(proto)):
        return None
    fi = a.resolved_function_info
    if fi is None or not fi.is_async:
        return None
    # An Own-shaped factory return would take _resolve_own_source_type's
    # forward/move-unwrap branches instead; async-def returns register as
    # the bare structural wrap, so this is defense in depth.
    frt = (unwrap_send_sync(fi.return_type)
           if fi.return_type is not None else None)
    if isinstance(frt, OwnType):
        return None
    at = analyzer.get_expr_type(a)
    if not (isinstance(at, NominalType) and at.qualified_name()
            in (qnames.CANCELLABLE, qnames.AWAITABLE)):
        return None
    if isinstance(a, TpyMethodCall):
        # A module-qualified async factory (`asyncio.wait_for(slow(), 5.0)`
        # nested in `create_task(...)`) or a MEMBER async method
        # (`asyncio.run(b.take())`): the AST wraps either render in the
        # same adapter -- the method call spells inline, its receiver
        # riding the ordinary method-call arm. The marker lane's
        # dyn-protocol result rides the coro_factory_ok escape; a member
        # factory's receiver/arg shapes gate at its own lowering.
        if (_marker_call_kind(a, analyzer, coro_factory_ok=True) is None
                and not fi.is_method):
            return None
        return proto
    k = _free_callee_kind(a, analyzer, coro_factory_ok=True)
    if k is None or k[0] not in ("plain", "imported"):
        return None
    return proto

def _dyn_own_conformer_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, 'TpyType'],
                           analyzer) -> 'tuple[NominalType, str] | None':
    """A concrete-CONFORMER source into an `Own[@dynamic P]` slot: the
    `std::make_unique<U>(x)` (inheritance) / `::tpy::make_adapter<Base>(x)`
    (structural) wrap of _gen_dynamic_protocol_own_arg, keyed by the SHARED
    `classify_dyn_own_arg` verdict so the two paths cannot drift. Admitted
    shapes: a user-record CTOR rvalue and a record-typed local NAME (the
    AST's `_maybe_move` renders `std::move` at a movable last use -- the
    lowering arm mirrors it via `_is_move_source`). Returns
    (slot protocol, verdict) or None; the coro-handle / async-factory /
    forward verdicts keep their own rows."""
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, matching the AST key (see _dyn_own_handle_arg): an
    # `Own[readonly[P]]` slot never takes the adapter render there.
    proto = u.wrapped
    if not (isinstance(proto, NominalType) and is_dyn_protocol(proto)):
        return None
    declared = None
    if isinstance(a, TpyName):
        declared = locals_.get(a.name)
    elif isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is None or not fi.is_constructor:
            return None
    elif isinstance(a, TpyMethodCall):
        # An Own[record]-returning METHOD rvalue
        # (`wait_for(loop.sock_accept(srv), ..)`): the record prvalue feeds
        # the same make_unique/make_adapter wrap; the method call's own
        # BORROW_BIND lowering gates receiver/args/result.
        if (a.resolved_function_info is None
                or not is_rvalue_source(analyzer, a)):
            return None
    else:
        return None
    at = analyzer.get_expr_type(a)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if at is not None else None)
    if isinstance(at_u, OwnType):
        at_u = unwrap_readonly(at_u.wrapped)
    # A protocol-typed source can never take the conformer wraps
    # (Adapter<P, P> over abstract P has no sizeof) -- defensive: such
    # sources classify 'forward' unless sema let a cross-protocol bind
    # through, and then AST is the safe path.
    if not (isinstance(at_u, NominalType) and at_u.is_user_record
            and not at_u.is_protocol):
        return None
    verdict = classify_dyn_own_arg(a, proto, declared, analyzer)
    if verdict not in ("inherit", "structural"):
        return None
    if verdict == "inherit" and not _f1_record(at_u, analyzer):
        # make_unique spells the concrete type -- F1 pins to_cpp().
        return None
    return (proto, verdict)


def _recv_own_dyn(recv: TpyExpr, locals_: dict[str, TpyType],
                  analyzer) -> bool:
    """The AST's `_receiver_is_own_dyn` mirror for name/call receivers:
    True when the receiver renders as `std::unique_ptr<P>` (abstract
    @dynamic P), so the member access spells `->`. The subscript-element
    case (an `Own[P]` container element) is not mirrored -- such
    receivers keep rejecting at their own shape gates."""
    declared_t = (locals_.get(recv.name)
                  if isinstance(recv, TpyName) else None)
    src = resolve_own_source_type(recv, declared_t, analyzer)
    return src is not None and is_dyn_protocol(src.wrapped)


def _wide_opt_deref_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A WIDE ptr-opt NAME at its POINTEE's borrow slot (`depth(t)` on a
    None-narrowed `t: Tree | None` param at a `const Tree&` slot): the
    pointer binding derefs (`depth((*t))`), binding the slot inline.
    Sema's narrowing proved non-None -- an un-narrowed Optional at the
    bare-pointee slot is a sema error, so reaching here implies the
    proof. The F1-record flavor rides its own family rows; this row
    covers the widened pointee classes (wrapper/T/dyn/container)."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    opt = _optional_ptr_borrow_wide(locals_[a.name], analyzer)
    if opt is None:
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if slot is None or _resolve_plain_alias(
            unwrap_readonly(opt.inner), analyzer) != _resolve_plain_alias(
            slot, analyzer):
        return False
    return _witness("arg.wide_opt_deref_name")


def _dyn_own_forward_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                              analyzer) -> 'NominalType | None':
    """An `Own[P]`-returning CALL rvalue at an `Own[@dynamic P]` slot
    (`speak_and_forward(make_parrot())`): the 'forward' verdict -- the
    result is already unique_ptr<P>-shaped (same or inheriting protocol),
    so it renders bare: no wrap and no move (the result is an rvalue).
    A METHOD-shaped source joins: a callable-FIELD invocation
    (`create_task(self._cb(Conn(i)))`) carries the same Own[P]-returning
    fi and forwards the same bare unique_ptr rvalue.
    Returns the slot protocol, or None."""
    if not isinstance(ptype, TpyType) or not isinstance(
            a, (TpyCall, TpyMethodCall)):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, like the sibling rows: `Own[readonly[P]]` stays out.
    proto = u.wrapped
    if not (isinstance(proto, NominalType) and is_dyn_protocol(proto)):
        return None
    fi = a.resolved_function_info
    if fi is None:
        # A callable-FIELD invocation (`create_task(self._cb(Conn(i)))`,
        # fi-less): the shared classifier reads the field's declared
        # Callable return through resolve_own_source_type -- 'forward' is
        # the same bare-unique_ptr verdict.
        if not (isinstance(a, TpyMethodCall) and a.is_callable_field):
            return None
        return (proto if classify_dyn_own_arg(a, proto, None, analyzer)
                == 'forward' else None)
    if fi.is_constructor:
        return None
    src = _own_dyn_return(fi.return_type)
    if src is None or not dyn_forward_ok(src, proto, analyzer):
        return None
    return proto


def _covariant_temp_arg(a: TpyExpr, ptype: 'TpyType | None',
                        locals_: dict[str, 'TpyType'],
                        analyzer) -> 'NominalType | None':
    """A covariant-generic record source into the UPCAST slot
    (`print_area(bc)` at a `Box[Shape]` param, `bc: Box[Circle]`):
    _gen_covariant_arg's typed temp (`Box<Shape> __tmp_N = std::move(bc);`
    + the bare `__tmp_N` at the arg position). Flush positions only (the
    caller gates temps_ok). Shapes: a local NAME (moved at a movable last
    use) or a ctor RVALUE. Returns the slot type, or None."""
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if not (isinstance(slot, NominalType) and slot.is_user_record):
        return None
    if isinstance(a, TpyName):
        if a.name not in locals_:
            return None
    elif isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if (fi is None or not fi.is_constructor
                or not (_ctor_shape_ok(a, analyzer)
                        or _ctor_instantiation_ok(a, analyzer))):
            return None
    else:
        return None
    at = analyzer.get_expr_type(a)
    at_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            if at is not None else None)
    if isinstance(at_u, OwnType):
        at_u = unwrap_readonly(at_u.wrapped)
    if not _covariant_record_upcast_ok(at_u, slot, analyzer):
        return None
    return slot


def _moved_record_ret(ret: 'TpyType | None', analyzer) -> bool:
    """A nominal record result (generic-concrete included) at a MOVED
    position: the consumer takes the value whole (std::move into a slot),
    so the render is the bare call -- no value-slot spelling. Containers
    stay out (their storage/borrow duality is the container frontier's)."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return (isinstance(t, NominalType) and not t.is_protocol
            and not (is_list(t) or is_dict(t) or is_set(t) or is_array(t)))


def _qualcall_ret_reject(ret: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a marker-call result outside the value set --
    names the blocking result FAMILY so the qualcall ret mass ranks by the
    frontier it waits on (record / container / optional / union / tuple)."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if t is None or is_void_like_type(t):
        return "method.qualcall.ret.void_value_pos"
    if isinstance(t, OptionalType):
        return "method.qualcall.ret.optional"
    if isinstance(t, UnionType):
        return "method.qualcall.ret.union"
    if isinstance(t, TupleType):
        return "method.qualcall.ret.tuple"
    if isinstance(t, NominalType):
        if is_list(t) or is_dict(t) or is_set(t) or is_array(t):
            return "method.qualcall.ret.container"
        return ("method.qualcall.ret.record_f1"
                if _f1_record(t, analyzer)
                else "method.qualcall.ret.record")
    return "method.qualcall.ret.other"

def _qualcall_arg_reject(a: TpyExpr, ptype: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a marker-call arg that fails every admitted row --
    names the param-slot family (and the arg's kind for plain slots), like
    `_native_arg_reject` for the native/template loop."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
         if ptype is not None else None)
    if isinstance(t, OwnType):
        return "method.qualcall.arg.own"
    if isinstance(t, OptionalType):
        return "method.qualcall.arg.optional"
    if isinstance(t, UnionType):
        return "method.qualcall.arg.union"
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return "method.qualcall.arg.call_rvalue"
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, NominalType) and at.is_user_record:
        return ("method.qualcall.arg.record_f1"
                if _f1_record(at, analyzer)
                else "method.qualcall.arg.record_nonf1")
    return f"method.qualcall.arg.other.{expr_kind_tag(a)}"

def _ptr_deref_method_call(e: TpyMethodCall, analyzer) -> bool:
    """A single-level Deref method call THROUGH a `Ptr[T]` receiver
    (`cell.release_strong()` on `cell: Ptr[Cell]`): _gen_method_call's
    pointer tail renders `p->m(args)` when sema proved the pointer non-null
    (`e.ptr_non_null`) and `::tpy::deref_check(p).m(args)` otherwise -- the
    existing THIRMethodCall is_arrow / deref_check renders; a pointer
    receiver never spells the `.__deref__()` chain. The ONE discriminator
    consumed by lowering; the caller adds receiver-shape and arg/result
    admission on top (the qualified-marker rows: the same `_args()`
    first-pass loop, Own cascade included). Rejected here: any other marker,
    a deeper chain (the pointer arm emits ONE `->` regardless of depth, so a
    `Ptr[Ptr[T]]` render is not reproduced), template/native/renamed fi
    (the builtins arm / `_is_native_stub` arg loop), generics, and non-Ptr
    wrapper receivers (Box/Rc -- the `.__deref__()` spelling)."""
    if e.deref_depth != 1 or e.deref_narrowed_to is not None:
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
    rt = analyzer.get_expr_type(e.obj)
    return rt is not None and rt.is_pointer()

def _ptr_deref_recv_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                       analyzer) -> bool:
    """Gate-side receiver admission for `_ptr_deref_method_call`: a bare
    name declared as an eligible `Ptr[T]` value binding (renders raw, like
    the AST's gen_expr -- Ptr locals are never indirect or assign-narrowed),
    or an admitted field read whose value is such a Ptr (`self._cell.m()`
    -- the F1 field-read render; sema's interior unwrap already happened in
    get_expr_type)."""
    if isinstance(e.obj, TpyName):
        return (e.obj.name in locals_
                and _eligible_ptr_value(locals_[e.obj.name], analyzer))
    if isinstance(e.obj, TpyFieldAccess):
        return (_eligible_ptr_value(analyzer.get_expr_type(e.obj), analyzer))
    return False


def _native_record_recv(t: 'TpyType | None', analyzer) -> bool:
    """A @native record receiver (`Vec[Int32]` over std::vector): its
    @cpp_template methods expand over the bare receiver render exactly like
    a Ptr's (`v.count` -> `static_cast<int32_t>(v.size())`)."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if isinstance(t, TpyType) else None)
    if not isinstance(u, NominalType):
        return False
    ri = analyzer.registry.get_record_for_type(u)
    return ri is not None and ri.is_native


def _ptr_native_member_core(e: TpyMethodCall, fi, recv_ptr: bool) -> bool:
    """The shared discriminator of the unproven `Ptr[record]` @native-member
    face -- a cpp_template-less native-renamed member (method / property
    getter) on a NAME receiver of pointer binding, not proven non-null
    (sema's `ptr_non_null`, the test _gen_method_call's is_pointer arm
    reads). Consumed by the admission gate below (which adds its own
    type_params / args rejects) and by the method tail's deref_check render
    flag, so the two sides cannot drift on the core."""
    return (recv_ptr and fi is not None and fi.cpp_template is None
            and bool(fi.native_name) and not fi.native_function
            and isinstance(e.obj, TpyName) and not e.ptr_non_null)


def _ptr_template_method_supported(
        e: TpyMethodCall, fi, recv_type: 'TpyType | None', analyzer, *,
        stmt_position: bool, record_ret_ok: bool) -> bool:
    """The EXPLICIT `@cpp_template` method call on a `Ptr[T]` value receiver
    (`p.__deref__()` -> `::tpy::deref_check(p)`): _gen_method_call's general
    builtin-method arm (`gen_method_from_function_info`) expands the template
    over the bare receiver render, no `{self}`-deref (a Ptr local / field is
    never indirect at deref_depth 0). Restricted to the zero-arg,
    positional-only template shape sema resolves for `Ptr.__deref__`; a
    `{cpp}`/type-param template (none on Ptr) or an arg-taking builtin method
    (`.span(n)`) stays AST. The result rides the plain-method THIRMethodCall's
    `cpp_template` arm; its value set mirrors the container arm's."""
    if recv_type is None or not (recv_type.is_pointer()
                                 or _native_record_recv(recv_type, analyzer)):
        return False
    if fi.cpp_template is not None:
        if "{cpp}" in fi.cpp_template or fi.type_params:
            return False
        if fi.native_function or fi.native_name or e.inferred_type_args:
            return False
    else:
        # A plain @native MEMBER on a `Ptr[record]` NAME receiver -- the
        # deref-check face only (`::tpy::deref_check(s).outer()`,
        # `s.outer.inner.flag`'s first hop); the proven `s->outer()`
        # spelling is unwitnessed and stays AST. The shared core lives in
        # `_ptr_native_member_core`; the generic/args rejects are this
        # gate's own.
        if not (_ptr_native_member_core(e, fi, recv_type.is_pointer())
                and not fi.type_params
                and not e.args and not e.inferred_type_args):
            return False
    # Scalar args expand bare into the template's positional slots
    # (`p.span(3)` -> `std::span(p, static_cast<size_t>(3))` -- the cast
    # lives in the template itself); non-scalar args stay AST.
    if e.args and not all(
            _resolved_scalar(analyzer.get_expr_type(a2), analyzer)
            for a2 in e.args):
        return False
    ret = analyzer.get_expr_type(e)
    return (_resolved_scalar(ret, analyzer)
            or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            # A Span result is a by-value view landing bare in any admitted
            # sink (`s: Span[Int32] = p.span(3)`).
            or (_span_value(ret) and _witness("method.ptr_template_span"))
            or (record_ret_ok and _f1_record(ret, analyzer))
            or (stmt_position and (ret is None or is_void_like_type(ret)))
            or note_detail("method.ptr_template.ret_type"))


def _value_opt_scalar_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A scalar-valued arg into a value-repr `Optional[scalar]` element slot
    (`items.append(Int32(1))` on `list[Int32 | None]` -> `push_back(1)`):
    the AST renders the scalar bare and std::optional's converting ctor
    wraps it. The NON-None twin of `_none_value_opt_arg`."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    opt = _value_opt_scalar(pt, analyzer)
    if opt is None:
        return False
    if _resolved_scalar(analyzer.get_expr_type(a), analyzer):
        return True
    # A pending int LITERAL (`items.append(3)`): sema leaves it unresolved
    # at the Optional slot, but the render is the bare digits either way --
    # target-typed by the slot's fixed-int inner.
    return (isinstance(_peel_coerce(a), TpyIntLiteral)
            and is_fixed_int_type(unwrap_readonly(opt.inner)))


def _elem_slot_type(ptype: 'TpyType | None') -> 'TpyType | None':
    """The element slot behind a container stub's param (`Own[T]` for
    `list.append`): the Own peel the insert render sees through."""
    if not isinstance(ptype, TpyType):
        return None
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    return pt


def _callable_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                       locals_: dict[str, TpyType], analyzer) -> bool:
    """A callable source into a `Callable`-VALUE element slot
    (`callbacks.append(add_offset)` -> `push_back(add_offset)`): a plain
    function reference or a std::function-typed name, both rendering
    unchanged. The slot check keeps the func-ref row off non-callable
    slots, whose own families decide their renders."""
    if not _callable_value(_elem_slot_type(ptype)):
        return False
    return (_func_ref_routable(a, analyzer)
            or _callable_value_pass_arg(a, locals_, analyzer))


def _tparam_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                     locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-`T` source into an open type-param element slot
    (`result.append(make_default())` -> `push_back(T{})`): a bare name, or an
    rvalue call whose result is that same `T`. The slot is unsubstituted, so
    it threads no coercion -- the source renders exactly as its own arm
    spells it."""
    pt = _elem_slot_type(ptype)
    if not isinstance(pt, TypeParamRef):
        return False
    if _tparam_name_pass_arg(a, ptype, locals_):
        return True
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    at = _elem_slot_type(analyzer.get_expr_type(a))
    return isinstance(at, TypeParamRef) and at.name == pt.name


def _union_member_ctor_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A union-member ctor rvalue into a union element slot
    (`xs.append(Circle(Int32(1)))` on `list[Shape]` -> the bare
    `push_back(::tpyapp::shapes::Circle(1))`): the variant's converting
    constructor absorbs the member value, so no lift renders. Names and
    non-ctor rvalues stay out -- their copy/move shapes differ."""
    pt = _elem_slot_type(ptype)
    if not isinstance(pt, UnionType) or pt.needs_wrapper():
        return False
    return _union_member_ctor_rvalue(a, pt, analyzer)


def _container_method_arg_ok(
        a: TpyExpr, ptype: 'TpyType | None', locals_: dict[str, TpyType],
        analyzer, *, param_names: 'set[str] | frozenset[str]',
        narrowed: 'set[str] | frozenset[str]') -> bool:
    # A wrapper element slot may carry the unresolved alias placeholder
    # (`Own[Json]` on list[Json].append) -- resolve to its union body.
    _es = _resolve_plain_alias(_elem_slot_type(ptype), analyzer)
    if isinstance(_es, UnionType):
        if _es.needs_wrapper():
            # A WRAPPER-union element slot absorbs a scalar-literal insert
            # via the wrapper's converting ctor (`__arr.push_back(4);` at
            # `list[Json]`) -- the bare token render, same bounds as the
            # ru-literal elements. Other sources keep the named reject.
            _lit = _peel_coerce(a)
            if (isinstance(_lit, (TpyIntLiteral, TpyFloatLiteral,
                                  TpyBoolLiteral))
                    and _ru_elem_ok(_lit, analyzer)):
                return _witness("arg.ru_wrapper_elem_literal")
            return note_detail("method.arg_shape")
        # A plain UNION element slot takes exactly one mirrored row: the
        # member ctor rvalue the variant absorbs. Every other source
        # carries a storage lift the bare insert does not render (a
        # same-union NAME is `::tpy::to_value_variant<...>(p)` -- the
        # pointer-variant param converted into the value-variant element),
        # so the general rows below must not see a union slot at all.
        return (_union_member_ctor_slot_arg(a, ptype, analyzer)
                or note_detail("method.arg_shape"))
    return ((_scalar_pass_through_slot(ptype, analyzer)
             and _resolved_scalar(analyzer.get_expr_type(a), analyzer))
            or _str_pass_through_arg(a, ptype, locals_, analyzer)
            or _str_owned_slot_arg(
                a, ptype, locals_, param_names, analyzer)
            # The bytes twin of the owned-str-slot row, LITERAL only:
            # `bs.append(b"xyz")` at an `Own[bytes]` element slot renders
            # the owned literal (`bytes_literal_owned`) -- the THIR bytes
            # literal's default form, which the span pin correctly skips
            # for an Own slot. View-form NAMES need the S6 materialize
            # convert and stay deferred pending a witness.
            or (isinstance(_peel_coerce(a), TpyBytesLiteral)
                and (_own_bytes := _plain_own_slot(ptype)) is not None
                and is_bytes_type(unwrap_readonly(_own_bytes))
                and _witness("arg.bytes_owned_literal"))
            # ... and its VIEW-form sibling (the S6 witness arrived):
            # a bytes param / narrowed deref / view slice at the same slot
            # takes the `::tpy::bytes_copy(x)` materialize convert.
            or _bytes_owned_slot_arg(a, ptype, locals_, param_names,
                                     analyzer)
            or _bytes_pass_through_arg(a, ptype, locals_, analyzer)
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _ptr_pass_through_arg(a, ptype, locals_, analyzer)
            or _container_pass_through_arg(a, ptype, locals_, analyzer)
            or _own_record_rvalue_arg(a, ptype, locals_, analyzer)
            or _copy_record_own_arg(a, ptype, analyzer)
            or _own_move_arg(a, ptype, locals_, analyzer)
            or _own_lvalue_arg(a, ptype, locals_, narrowed, analyzer)
            # The structural Iterable/Sequence slot of a stub method
            # (`xs.extend([4, 5])` / `xs.extend(b)` -- the C++ template
            # binds the container bare; a movable last-use name takes the
            # consuming `::tpy::own_iter(std::move(b))` wrap at lowering).
            or _native_iterable_literal_arg(a, ptype, analyzer)
            or _native_iterable_container_arg(a, ptype, locals_)
            or _native_iterable_call_arg(a, ptype, analyzer)
            or _own_iter_special_arg(a, ptype)
            # A nested list literal into an Own[list] element slot
            # (`rows.append([9, 9])` -> `push_back({9, 9})`).
            or _own_container_literal_arg(a, ptype, analyzer)
            or _any_pass_through_arg(a, ptype, locals_, analyzer)
            or _container_literal_method_arg(a, ptype, analyzer)
            # `None` into a value-repr Optional element slot
            # (`items.append(None)` on `list[Int32 | None]`) -> the
            # STORAGE-form `std::nullopt`, like the free-call row.
            or _none_value_opt_arg(a, ptype, analyzer) is not None
            # A whole owned value-opt VIEW local at an Own[Optional[StrView]]
            # element slot (`items.append(src)`): bare for the narrowed
            # identity coerce, the `__ov` statement-expression shim for the
            # un-narrowed one -- the render row keys the registered VIEW
            # binding, so an unregistered name falls through there.
            or _opt_view_own_elem_arg(a, ptype, locals_, analyzer,
                                      param_names)
            # A scalar VALUE into that same slot (`items.append(Int32(1))`):
            # the AST passes it bare (`push_back(1)`) -- std::optional's
            # converting constructor does the wrap, no target thread.
            or _value_opt_scalar_elem_arg(a, ptype, analyzer)
            # A ptr VALUE at an `Own[Ptr[T]]` element slot
            # (`ps.append(p)` / `ps.append(unsafe_cast(q))`): Own on a
            # value type is a no-op spelling and the native stub's
            # inline-template arg renders bare -- name, field and call
            # sources alike, no copy temp (unlike a user method's
            # Own[scalar] slot).
            or ((_own_ptr := _plain_own_slot(ptype)) is not None
                and _eligible_ptr_value(_own_ptr, analyzer)
                # The PEELED arg must be ptr-typed itself: a record
                # lvalue coerce-lifted to the Ptr slot (`ps.append(
                # items[0])`) belongs to the `&(...)` lift row below.
                and _eligible_ptr_value(
                    analyzer.get_expr_type(_peel_coerce(a)), analyzer)
                and _witness("arg.own_ptr_value"))
            # `_container_method_elem`'s element families: every one lands
            # bare in the insert render -- `None` -> `std::monostate{}`, a
            # function/callable name unchanged, an open-T source by name.
            # (The union element slot answered above, on its own row.)
            or _none_unit_arg(a, ptype) is not None
            or _callable_slot_arg(a, ptype, locals_, analyzer)
            or _tparam_slot_arg(a, ptype, locals_, analyzer)
            # A value-tuple LITERAL at an Own[value-tuple] element slot
            # (`ps.append(("k", 9))` -> `push_back(std::tuple<std::string,
            # int32_t>{"k", 9})`): the spelled value render, target-typed
            # per element like the decl sink. LITERALS only -- a tuple NAME
            # at an Own slot rides the move cascade.
            or (isinstance(a, TpyTupleLiteral)
                and (_own_vt := _plain_own_slot(ptype)) is not None
                and _value_tuple(_own_vt, analyzer) is not None
                and _witness("arg.own_value_tuple_literal"))
            # A ref-element tuple LITERAL at an Own[tuple[T | None, ..]]
            # element slot (`pairs.append((a, b))`): the consuming storage
            # lift (`tuple_to_storage_move<S>(..)` over the borrow tuple
            # with per-element moves). Per-element admission (last-use
            # lvalue / fresh rvalue / copy() / None) lives in the ladder
            # and rejects there.
            or (isinstance(a, TpyTupleLiteral)
                and (_own_bt := _plain_own_slot(ptype)) is not None
                and isinstance(
                    _bt_bare := unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(_own_bt))), TupleType)
                and _bt_bare.has_pointer_repr_element()
                and (_tuple_elem_slots_ptr_optional(_bt_bare)
                     # Plain-record members admit for ALL-RVALUE literals
                     # only (`pairs.append((Item(1), Item(2)))` -- the
                     # double-convert tuple_to_storage_move over
                     # tuple_value_to_borrow); the CONST_REF lvalue rule
                     # the borrow builder does not carry never fires on
                     # an rvalue element.
                     or _btuple_literal_elems_rvalue(a, _bt_bare, analyzer))
                and _witness("arg.own_btuple_literal"))
            # A whole storage-tuple ELEMENT read (`pairs2.append(pairs[0])`,
            # bare `__getitem__`) or a borrow-tuple-returning CALL
            # (`pairs.append(make_pair(a, b))`, the non-move
            # tuple_to_storage lift) into the same tuple's Own slot; the
            # source's own gates re-check at lowering.
            or (isinstance(a, (TpySubscript, TpyCall, TpyMethodCall))
                and (_own_bt2 := _plain_own_slot(ptype)) is not None
                and isinstance(
                    _bt2_bare := unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(_own_bt2))), TupleType)
                and _bt2_bare.has_pointer_repr_element()
                and _tuple_elem_slots_ptr_optional(_bt2_bare)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(a)))) == _bt2_bare)
            # An owning CALL whose result IS the Own element slot
            # (`pairs.append(make_pair(1, 10))` at `Own[tuple[Int32,
            # Rc[Node]]]`): a prvalue binds the by-value slot directly, so
            # the insert renders bare whatever the element family -- the
            # tuple twin of `_own_record_rvalue_arg`'s call row. Element
            # families the rows above claim are matched there first.
            or _own_tuple_call_rvalue_arg(a, ptype, analyzer)
            or _ptr_addr_of_elem_arg(a, ptype, analyzer)
            or note_detail("method.arg_shape"))


def _opt_view_own_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType], analyzer,
                           param_names: 'set[str] | frozenset[str]') -> bool:
    """A whole owned value-opt VIEW LOCAL (declared `Optional[str]`, C++
    `std::optional<std::string>`) coerce-wrapped at an `Own[Optional[StrView]]`
    element slot -- the two coercion faces of `items.append(src)`: the
    NARROWED occurrence's identity `str_to_strview` (bare pass; C++'s
    optional converting ctor absorbs it) and the un-narrowed
    `optional_str_to_strview` (the `__ov` once-evaluated shim). Params stay
    out: their borrow `optional<string_view>` binding renders the OTHER shim
    family (`THIROptViewArg`) and is not witnessed at this slot."""
    if not (isinstance(a, TpyCoerce)
            and a.coercion.name in ("str_to_strview",
                                    "optional_str_to_strview")
            and isinstance(a.expected_type, OwnType)
            and isinstance(a.expr, TpyName)
            and a.expr.name in locals_
            and a.expr.name not in param_names):
        return False
    if _value_opt_owned_view(locals_[a.expr.name], analyzer) is None:
        return False
    own = _plain_own_slot(ptype)
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(own)))
            if own is not None else None)
    return (isinstance(slot, OptionalType)
            and is_str_view_type(unwrap_readonly(slot.inner)))


def _ptr_addr_of_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                          analyzer) -> bool:
    """A record-element lvalue at a `Ptr[record]` ELEMENT slot
    (`ps.append(items[0])` on `list[Ptr[Node]]` ->
    `ps.push_back(&::tpy::__getitem__(items, 0))`): TPy takes the address at
    a Ptr slot, and the checked element read is the lvalue to take it of.
    `_ptr_pass_through_arg` covers sources that ALREADY are pointers; this
    is the lift half."""
    w = _plain_own_slot(ptype)
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        w if w is not None else ptype))) if ptype is not None else None
    if not isinstance(slot, PtrType):
        return False
    pointee = unwrap_readonly(slot.pointee)
    if not _f1_record(pointee, analyzer):
        return False
    # Sema wraps the source in the address-of coercion; the lvalue under it
    # is what the render addresses.
    inner = _peel_coerce(a)
    if not (isinstance(inner, TpySubscript)
            and not isinstance(inner.index, TpySlice)):
        return False
    at = analyzer.get_expr_type(inner)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return atu == pointee and _witness("arg.ptr_addr_of_elem")


def _storage_form_tuple_return(fi) -> bool:
    """Whether a call's RETURN is a storage-form tuple -- `Own[tuple[..]]`
    or the per-element-Own synthesis with no borrowed half -- so an owning
    slot takes it bare with no `tuple_to_storage` lift. The call verdict of
    the AST's `needs_tuple_storage_lift`: `is_storage_form_source`'s two
    call shapes minus `renders_own_borrow_tuple`'s MIXED render."""
    rt = unwrap_readonly(fi.return_type) if fi is not None else None
    if (isinstance(rt, OwnType)
            and isinstance(unwrap_readonly(rt.wrapped), TupleType)):
        return True
    return (isinstance(rt, TupleType)
            and any(isinstance(et, OwnType) for et in rt.element_types)
            and not rt.is_mixed_own())


def _own_tuple_call_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """An owning-call rvalue whose result type IS the `Own[tuple]` element
    slot. The prvalue binds the by-value slot with no lift, so the element
    family is unobservable at the insert -- unlike the borrow-tuple rows
    above, which each render a conversion."""
    w = _plain_own_slot(ptype)
    if w is None or not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(w)))
    if not isinstance(slot, TupleType):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    return (atu == slot and is_rvalue_source(analyzer, a)
            and _witness("arg.own_tuple_call_rvalue"))


def _stub_method_ret_ok(
        ret: 'TpyType | None', analyzer, *, stmt_position: bool,
        storage_ret_ok: bool, enum_ok: bool, ptr_ok: bool, callable_ok: bool,
        span_storage_ok: bool, stmt_storage_ok: bool) -> bool:
    """The builtin-stub method families' shared result-shape core -- a value
    scalar / Char / str-value / bytes-value result, plus the extras each
    family flags on. `storage_ret_ok` admits container-family results at
    storage sinks (`parts = s.split(",")`); `stmt_storage_ok` additionally
    admits them DISCARDED (a statement-position call renders the same bare
    stub call whatever the ignored result family). Factored so a new stub
    family cannot hand-copy a drifting variant of this predicate."""
    return (_resolved_scalar(ret, analyzer)
            or _eligible_char(ret)
            or (enum_ok and _eligible_enum(ret, analyzer) is not None)
            or (ptr_ok and _eligible_ptr_value(ret, analyzer))
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or (callable_ok and _callable_value(ret))
            # An open-T result (`return self.items.pop()` inside a generic
            # record -> `return ::tpy::pop_back(this->items);`): the
            # form-neutral slot renders the bare call, in step with the
            # qualcall / protocol gates' `_tparam_value` rows; the composing
            # position gates its own family.
            or _tparam_value(ret)
            or (storage_ret_ok
                and (_storage_call_ret(ret, analyzer) is not None
                     or (span_storage_ok and _span_value(ret))))
            or (stmt_position
                and (ret is None or is_void_like_type(ret)
                     or (stmt_storage_ok
                         and _storage_call_ret(ret, analyzer) is not None))))


def _container_method_call_supported(
        e: TpyMethodCall, fi, locals_: dict[str, TpyType], analyzer, *,
        stmt_position: bool, storage_ret_ok: bool,
        borrow_ret_ok: bool = False) -> bool:
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.cpp_ret_substitution")
    ret = analyzer.get_expr_type(e)
    return (_stub_method_ret_ok(
                ret, analyzer,
                stmt_position=stmt_position, storage_ret_ok=storage_ret_ok,
                enum_ok=True, ptr_ok=True, callable_ok=True,
                span_storage_ok=True, stmt_storage_ok=False)
            # A DISCARDED open-T result (`self.items.pop()` as a statement):
            # the stub call renders bare whatever T instantiates to, since
            # nothing consumes it.
            or (stmt_position and _tparam_value(ret))
            # A CONTAINER result consumed as the next call's receiver
            # (`groups.setdefault("a", []).append(1)`): the outer stub method
            # composes onto the bare inner render, borrow or not.
            or (borrow_ret_ok and _container_method_recv(ret, analyzer, None))
            # An owned RVALUE result landing in a value sink (`second =
            # heap.pop()` -> `Box second = ::tpy::pop_back(heap);`, `c =
            # b.copy()` on `list[Node]`): the element families
            # `_storage_call_ret` leaves out decl as the same plain copy --
            # their downstream reads gate themselves. A BORROW-returning stub
            # keeps rejecting (the AST binds an alias there).
            or ((storage_ret_ok or borrow_ret_ok)
                and is_rvalue_source(analyzer, e)
                and (_f1_record(ret, analyzer)
                     or _container_method_recv(ret, analyzer, None)))
            # A BORROW-returning ptr-repr Optional result (`d.get("a")` ->
            # the bare `T*` from dict_get; WIDE pointee class): the render
            # is the bare stub call everywhere -- the consuming position
            # (passthrough decl / None test) owns the binding shape.
            or (_ptr_opt_borrow_call_ret(e, ret)
                and isinstance(ret, OptionalType)
                and _opt_pointee_wide(unwrap_readonly(ret.inner), analyzer)
                and _witness("method.container_opt_ptr_ret"))
            or note_detail("method.ret_type"))


def _method_call_arg_ok(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        locals_: dict[str, TpyType], analyzer, *, temps_ok: bool,
        narrowed: 'set[str] | frozenset[str]',
        param_names: 'set[str] | frozenset[str]',
        tparam_bounds: 'dict | None' = None,
        error_return_ok: bool = False) -> bool:
    if isinstance(a, TpyGeneratorExpression):
        # A genexpr arg renders its make_generator IIFE in place at any
        # method slot (`", ".join(str(x) for x in nums)` -- the AST's
        # gen_expr render is position-blind); the genexpr's own lowering
        # gates the source/element shapes, so a bad shape still falls the
        # body back.
        return True
    if not _plain_member_call_markers_ok(e, targs_ok=True):
        # generator_ok/coro_factory_ok unconditionally: the call-level gate
        # already decided whether the generator/async fi is admitted
        # (iterable / adapter-wrap position only) -- this arg-side
        # re-derivation only picks the arg rows, which are the same for a
        # generator or coro factory as for any qualified call.
        # A USER-deref chain (`r.__deref__().m(args)`) is the other marker
        # whose args ride the same `_args()` first-pass loop; its lowering arm
        # has already validated the call shape before reaching this gate, so
        # the deref marker alone identifies it. `_marker_call_kind` cannot:
        # it rejects every deref-marked call by construction.
        kind = (("qualified", "")
                if (_ptr_deref_method_call(e, analyzer) or e.deref_depth)
                else _marker_call_kind(e, analyzer, generator_ok=True,
                                       coro_factory_ok=True,
                                       error_return_ok=error_return_ok))
        return (kind is not None
                and _marker_call_arg_ok(
                    a, ptype, kind, locals_, analyzer,
                    temps_ok=temps_ok, narrowed=narrowed))

    recv_type = _method_receiver_type(e.obj, locals_, analyzer)
    fam = _method_recv_family(recv_type, analyzer, tparam_bounds)
    if fam is not None:
        return fam.arg_ok(a, ptype, locals_, analyzer,
                          param_names=param_names, narrowed=narrowed)

    if recv_type is not None and recv_type.is_pointer():
        # The ptr-template arm's args (`p.span(n)`): scalar positional
        # slots expanded bare -- the view family's rows.
        return _view_method_arg_ok(a, ptype, locals_, analyzer,
                                   param_names=param_names,
                                   narrowed=narrowed)
    recv = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
    if isinstance(recv, OwnType):
        recv = unwrap_readonly(recv.wrapped)
    opt_recv = _optional_ptr_borrow(recv, analyzer)
    if opt_recv is not None:
        recv = unwrap_readonly(opt_recv.inner)
    ri = analyzer.registry.get_record_for_type(recv)
    if ri is None:
        return False
    overloads = analyzer.registry.get_method_overloads_with_parents(
        ri, e.method)
    if not overloads:
        # A property SETTER call has a synthetic method name (`set_value`) --
        # the accessor is registered under the property name, so the registry
        # lookup misses; the resolved setter fi supplies the arg-temp slot.
        setter_fi = e.resolved_function_info
        if setter_fi is not None and setter_fi.is_property_setter:
            overloads = [setter_fi]
        else:
            return False
    return _record_method_arg_ok(
        a, ptype, index, overloads[0], locals_, analyzer,
        temps_ok=temps_ok, narrowed=narrowed)

def _protocol_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                   analyzer, *, stmt_position: bool,
                                   storage_ret_ok: bool,
                                   borrow_ret_ok: bool = False) -> bool:
    """A method call on a bare protocol receiver -- `pet.make_noise()` on a
    `@dynamic` `Base&` (a vtable call) or `count.length()` on a structural
    `const T_c&` (monomorphized). Both spell `recv.method(args)`: the flavor
    lives entirely in the AST-emitted param slot, not in the body.

    `_gen_method_call` has no protocol arm -- its user-record arg loop is
    guarded by `is_user_record`, so a protocol receiver falls to the LAZY
    `_args()` fallback loop, whose renders are the FREE call's, not the record
    method's. Two consequences the mirror must honor: literal args take their
    slot's coercion (an int literal into a BigInt slot wraps, a float literal
    into a Float32 slot gets the `f` suffix) rather than the method path's
    target-less spelling, and `overloaded_call` is never threaded -- harmless,
    since the str-literal pin fires only on overload sets, which reject here.

    The shared marker / receiver-shape / fi-kind / arity rejects already ran in
    method-call lowering; `_plain_member_call_markers_ok` also disposed of
    the deref chain, the explicit/inferred type args, and the kwargs, and the
    Optional runtime-check marker cannot reach a protocol name.

    Receiver: a bare in-scope name (a protocol param or a routed protocol
    local), never indirect -- `is_arrow` keys on the lowering pointer set,
    which a protocol binding never joins. An `Own[P]` receiver ALSO lands
    here (the `_own_dyn_method_recv` family row shares this shape gate);
    its `->` access comes from the emit node's own-dyn mirror
    (`_recv_own_dyn`), not from anything decided here.

    Method: a plain instance method. The member name is always
    `escape_cpp_name(e.method)` -- `_plain_method_fi_ok` already rejected the
    LiteralType params that would mangle it, and the @native rename is rejected
    below -- so no overload-set check is needed here (unlike the record arm,
    whose arg-temp decisions read `overloads[0]`).

    Args: the shared pass-through rows only (`_shared_pass_through_arg` -- the
    rows whose render lives inside `gen_call_arg` itself, so the `_args()`
    loop's missing dcbp/pin kwargs cannot change them). Result: the
    value-position set, or void in statement position.
    """
    if not isinstance(e.obj, TpyName):
        # A protocol method call on a METHOD-CALL receiver (`box.get().name()`):
        # the inner call yields a `.`-access borrow (`_method_call_receiver_ok`),
        # so the outer call composes exactly like a name receiver. A one-level
        # FIELD receiver off an admitted parent (`self.factory.make()` -- a
        # structural protocol member, `this->factory.make()`) reads the bare
        # member the same way. Subscript / free-call receivers keep their own
        # deref rules and stay on the AST path.
        if not ((isinstance(e.obj, TpyMethodCall)
                 and _method_call_receiver_ok(e.obj, locals_, analyzer))
                or (isinstance(e.obj, TpyFieldAccess)
                    and _field_receiver_ok(e.obj, locals_, analyzer)
                    and _witness("method.protocol_field_recv"))
                # A FREE-call receiver with a @dynamic-protocol result
                # (`make_parrot().name()`, `echo_readonly(dog).name()`):
                # the same verdict the nonname gate reached -- Own[P]
                # arrows, borrow `P&` dots, off the bare inner render.
                or (isinstance(e.obj, TpyCall)
                    and _method_nonname_receiver_ok(e.obj, locals_,
                                                    analyzer))):
            return note_detail("method.protocol.recv_shape")
    # A ZERO-ARG @cpp_template dunder stub on the protocol value
    # (`it.__next__()` on `Iterator[T]` -- template `{self}.__next__()`):
    # both paths expand the same template over the same receiver render
    # (gen_call_from_fi / the THIRMethodCall cpp_template arm), so admission
    # is a gate widening only. Arg-carrying templates render their args
    # through the AST's inline_template loop, which this family's free-call
    # arg rows do not mirror -- they keep rejecting. `{cpp}` substitutes the
    # return-type spelling (the generics frontier) and stays out too.
    proto_template = (fi.cpp_template is not None and not e.args
                      and "{cpp}" not in fi.cpp_template)
    if ((fi.cpp_template is not None and not proto_template)
            or fi.native_function or fi.native_name
            or fi.type_params or fi.is_staticmethod or not fi.is_method):
        return note_detail("method.fi_kind")
    if proto_template:
        _witness("method.protocol_template")
    ret = analyzer.get_expr_type(e)
    if not (_resolved_scalar(ret, analyzer) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            # A T result off a bounded-T receiver (`item.clone() -> T`):
            # renders by name; the composing position gates its own family.
            or _tparam_value(ret)
            # An Own[record] RVALUE at a STORAGE sink (`return
            # factory.create_point(x, y)` off a bounded-T protocol
            # receiver): the prvalue lands bare in the return/decl slot,
            # exactly like the record ladder's storage admission.
            or (storage_ret_ok and is_rvalue_source(analyzer, e)
                and _f1_record(
                    _unwrap_own(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(ret)))) if ret is not None else None,
                    analyzer)
                and _witness("method.protocol_own_storage_ret"))
            # The genrec sibling: an Own[Tree[T]]-returning protocol method
            # (`s.sprout()`) lands bare in the argtemp's STORAGE slot.
            or (storage_ret_ok and is_rvalue_source(analyzer, e)
                and isinstance(
                    _unwrap_own(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(ret)))) if ret is not None else None,
                    RecursiveAliasInstanceType)
                and _witness("method.protocol_genrec_storage_ret"))
            # An Own[Self] / protocol-typed result at a STORAGE sink
            # (`result = d.duplicate()` in the monomorphized template
            # body): the prvalue lands bare in the `auto` decl slot. Sema
            # may record no expression type for an open-Self result (ret
            # is None); the decl side pinned the slot to the auto family.
            or (storage_ret_ok and is_rvalue_source(analyzer, e)
                and (ret is None or _protocol_auto_slot(
                    _unwrap_own(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(ret))))))
                and _witness("method.protocol_self_storage_ret"))
            or (stmt_position and (ret is None or is_void_like_type(ret)))
            # A protocol-typed result at a RECEIVER/BORROW_BIND position
            # (`c.half().value()`, `a.to_b().tag()`): the rvalue feeds the
            # composing call's receiver slot bare; that call gates its own
            # family.
            or (borrow_ret_ok and _protocol_binding(ret) is not None
                and _witness("method.protocol_chain_ret"))
            # An `Own[@dynamic P]` result (`d.replicate()` -> unique_ptr):
            # the handle renders bare wherever it lands -- rvalue, no wrap,
            # no move; the consuming position (ctor arg / receiver / decl)
            # gates its own shape.
            or (_own_dyn_return(fi.return_type) is not None
                and _witness("method.protocol_own_dyn_ret"))
            # A DISCARDED result in statement position: nothing consumes it,
            # so the call renders bare whatever its type -- the same reason
            # the record-receiver ladder admits `method.record_discard` /
            # `method.container_discard`. The result-family set exists for
            # VALUE positions.
            or (stmt_position and _witness("method.protocol_discard"))):
        return note_detail("method.protocol.ret_type")
    return _witness("method.protocol")


def _protocol_method_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                            locals_: dict[str, TpyType], analyzer, *,
                            param_names: 'set[str] | frozenset[str]',
                            narrowed: 'set[str] | frozenset[str]') -> bool:
    return (_shared_pass_through_arg(a, ptype, locals_, analyzer)
            # A same-wrapper NAME at the protocol method's genrec slot
            # (`s.absorb(t)`): the bare-name pass-through, exactly the
            # record ladder's wrapper row.
            or _ru_wrapper_name_arg(a, ptype, locals_, narrowed, analyzer)
            or note_detail("method.protocol.arg_shape"))

def method_literal_mangled_cpp(e: TpyMethodCall, analyzer) -> 'str | None':
    """The method-call member resolution's literal-mangled arm -- the AST's
    `_is_overloaded_method` + a Literal param on the resolved fi
    (`r.get("age")` -> `r.get__lit_age("age")`). Returns the mangled member
    spelling, or None when the plain resolution applies. The @native rename
    outranks it (gen_method_call's order), so a renamed fi returns None and
    the caller falls to `_method_member_cpp`."""
    fi = e.resolved_function_info
    if fi is None or (fi.native_name and not fi.native_function):
        return None
    if not any(isinstance(p.type, LiteralType) for p in fi.params):
        return None
    obj_type = analyzer.get_expr_type(e.obj)
    if not isinstance(obj_type, NominalType):
        return None
    record = analyzer.registry.get_record_for_type(obj_type)
    if record is None or len(record.get_method_overloads(e.method)) <= 1:
        return None
    return literal_mangled_name(e.method, fi)


def _record_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                 analyzer, *, stmt_position: bool,
                                 temps_ok: bool = False,
                                 record_ret_ok: bool = False,
                                 storage_ret_ok: bool = False,
                                 coro_factory_ok: bool = False,
                                 suspend_ok: bool = False,
                                 iterable_ret_ok: bool = False,
                                 value_opt_ret_ok: bool = False,
                                 ptr_opt_passthrough: bool = False,
                                 owned_tuple_ret_ok: bool = False,
                                 btuple_ret_ok: bool = False,
                                 union_subject_ret_ok: bool = False,
                                 raw_stmt_handled: bool = False,
                                 narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """A plain user-record method call `recv.method(args)` -- the
    `_gen_method_call` user-record arm reduced to its pass-through subset. The
    shared marker / fi / arity rejects already ran in method-call lowering.

    Receiver: a bare in-scope F1-record name -- a record param, a REF_ALIAS /
    loop-var borrow, an F2 pointer-local (renders `p->method(args)`, the
    indirect-name arm; carried on `THIRMethodCall.is_arrow` at lowering), the
    method receiver itself (`self.helper()` -> `this->helper()`, the same
    indirect arm over the THIRSelf render), an
    isinstance-narrowed subject (`locals_` arrives retyped to the member; reads
    rename to the `T&` extraction alias), or a pointer-repr Optional[F1-record]
    borrow name (an `A | None` param / OPTIONAL_TO_PTR local): proven-non-None
    calls render the indirect arm (`p->method(args)`, is_arrow -- the name is
    in the lowering pointer set), unproven ones the runtime-check arm
    (`::tpy::deref_check(p).method(args)`, the caller's marker carve-out ->
    `THIRMethodCall.deref_check`).

    Method: a single-overload plain instance method, resolvable through the
    MRO (an inherited method emits identically for the admitted arg shapes --
    probe-verified; both AST arg paths reduce to the same gen_call_arg
    renders). Multi-overload sets are rejected wholesale: @auto_readonly
    clones, property pairs, and literal-specialized overloads all land there,
    and the AST arm builds its temp decisions from `overloads[0]` while
    rendering against the RESOLVED overload -- a pairing the slice does not
    reproduce. This also keeps `_wants_str_literal_pin` unreachable (the pin
    fires only at overload_count > 1). A native_name RENAME on an actually-native
    record IS admitted (the file-handle `fh.write` shape -- member = native_name,
    a plain member call); a native_function (free-function form) / cpp_template
    each takes a different `_gen_method_call` arm and stays rejected.

    Args: the free-call pass-through set minus bytes (a bytes-view result /
    arg form is not threaded through the method node) -- eligible scalars
    into NON-Own scalar slots (see `_plain_scalar_slot`), bare float
    literals, str-slice values, Char values, container names, F1-record
    names (`a.combine(b)`), record-ctor rvalues into CONST same-record
    slots (`a.combine(A(9))`, see `_method_ctor_rvalue_arg`), and the
    VALUE-union rows -- same-union names / coerced literals bare
    (`_method_value_union_arg`) and member-valued scalars through the
    `__tmp_N` variant temp under `temps_ok` (the free-call arg-temp row;
    value variants are const-blind, so the inherited-method first-pass AST
    loop, which omits `is_readonly_target`, renders identically). The
    mutated-ref-param rvalue shape (`a.absorb(A(4))`) is the AST miscompile
    tracked in BUGS.md and stays on the AST path.

    Result: an eligible scalar / Char / str-slice value, or void (None) in
    statement position, mirroring free-call lowering's value-position set."""
    recv = e.obj  # a name or a one-level field access -- checked by the caller
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _method_receiver_type(recv, locals_, analyzer))))
    if isinstance(recv_t, OwnType):
        recv_t = unwrap_readonly(recv_t.wrapped)
    # An Optional-ptr borrow receiver dispatches methods on the inner record
    # (proven -> arrow, unproven -> deref_check; both yield the inner).
    opt_recv = _optional_ptr_borrow(recv_t, analyzer)
    if opt_recv is not None:
        recv_t = unwrap_readonly(opt_recv.inner)
    if isinstance(recv_t, OptionalType) and isinstance(recv, TpyName):
        # A STORAGE-optional binding (`std::optional<Rc<T>>` local from an
        # owned-optional call) NARROWED at this occurrence dispatches on the
        # inner record over the `(*name)` deref receiver. The name arm only
        # renders that deref for REGISTERED locals -- an unregistered binding
        # (an Own-optional param) still rejects at its read, so admitting
        # the type here never mis-renders.
        so = _storage_optional_return_type(recv_t, analyzer)
        occ = analyzer.get_expr_type(recv)
        if (so is not None and occ is not None
                and not isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(occ))), OptionalType)):
            recv_t = unwrap_readonly(_unwrap_own(unwrap_readonly(so.inner)))
    if not (isinstance(recv_t, NominalType) and _f1_record(recv_t, analyzer)):
        # The drill's "which methods block" discriminant: name the receiver
        # family AND the method, so e.g. str methods rank individually.
        return note_detail(f"method.{_recv_family(recv_t, analyzer)}.{e.method}")
    ri = analyzer.registry.get_record_for_type(recv_t)
    if ri is None:
        return False
    # A @native record's instance method (the file-handle `fh.write(...)` /
    # `r.read(n)` shape) renders `recv.native_name(args)` -- the member name
    # resolves to `fi.native_name` in the method-call emit, byte-identical to a
    # plain member call. Only native_name on an actually-native record is
    # admitted; a native_function (free-function form, `::sym(recv, args)`) or
    # cpp_template stays on the AST path. The `inline_template=is_native_stub`
    # the AST threads for these args only affects Own[T] slots (a
    # redundant-copy skip); the admitted arg rows carry none.
    native_method = bool(fi.native_name) and ri.is_native and not fi.native_function
    # A generic method is a plain member call with the method_targs suffix
    # when sema inferred args (`b.transform<::tpy::BigInt>(42)`) and a bare
    # member call when it did not (`c.duplicate()` -- a class-T shadow bind,
    # T fixed by the receiver), exactly the AST's method_targs rule.
    # INT-kind args arrive as plain ints (not TpyTypes) and stay out.
    generic_method_ok = bool(fi.type_params) and (
        not e.inferred_type_args
        or (len(e.inferred_type_args) == len(fi.type_params)
            and all(isinstance(t, TpyType) for t in e.inferred_type_args)))
    # A @classmethod reached through an INSTANCE renders as an ordinary member
    # call (`p.origin()` -- C++ evaluates the receiver expression and calls the
    # static member), so it rides this arm despite carrying the parser's
    # `is_staticmethod` bit. A plain @staticmethod through an instance is a
    # distinct, unwitnessed shape and keeps rejecting.
    classmethod_member = fi.is_classmethod
    if (fi.cpp_template is not None or fi.native_function
            or (fi.native_name and not native_method)
            or (fi.type_params and not generic_method_ok)
            or (fi.is_staticmethod and not classmethod_member)
            or not (fi.is_method or classmethod_member)):
        return note_detail("method.fi_kind")
    overloads = analyzer.registry.get_method_overloads_with_parents(ri, e.method)
    if len(overloads) != 1:
        # An @auto_readonly / auto_own[Self] clone pair renders the same
        # plain `recv.method(args)` whichever member sema resolved -- C++
        # dispatches on receiver const-ness (or lvalue-ness), so the call
        # emit is member-blind. The mutable-clone flag is threaded onto its
        # FunctionInfo; an auto_own pair is the one-consuming-member shape
        # (its consuming DEF never routes, but calls on lvalue receivers
        # resolve to the borrowing member and render bare). Genuine stub
        # sets (incl. literal overloads, whose call sites MANGLE the
        # callee name) keep rejecting.
        is_clone_pair = (
            len(overloads) == 2
            and (any(fi2.is_auto_readonly_mutable_clone for fi2 in overloads)
                 or sum(1 for fi2 in overloads if fi2.is_consuming) == 1))
        # A property getter/setter pair shares the property name: the read
        # resolves to the getter specifically (`c.prop` -> `c.prop()`), so the
        # emit is unambiguous whatever the paired setter -- not a genuine
        # overload set. A setter call carries a synthetic name (`set_prop`)
        # not in the registry, so its lookup is empty -- the resolved fi's
        # accessor kind is the tell.
        is_property_pair = (
            (bool(overloads)
             and all(fi2.is_property_getter or fi2.is_property_setter
                     for fi2 in overloads))
            or (not overloads
                and (fi.is_property_getter or fi.is_property_setter)))
        # A genuine stub set whose members differ by C++ param TYPES renders
        # the plain `recv.method(args)` -- C++ overload resolution picks the
        # specialization the AST emitted per stub. LITERAL-typed params
        # spell a MANGLED member instead (`r.get__lit_age("age")` -- the
        # record arm's method_literal_mangled_cpp spelling). Template stubs
        # (own type params, or protocol/Fn params synthesizing them) emit
        # through the template-header path -- unmirrored either way.
        template_stub = any(getattr(fi2, "type_params", None)
                            or any(_stub_template_param(p.type)
                                   for p in fi2.params)
                            for fi2 in overloads)
        is_plain_stub_set = (
            not any(isinstance(p.type, LiteralType) for p in fi.params)
            and not template_stub)
        literal_stub_set = (
            not template_stub
            and method_literal_mangled_cpp(e, analyzer) is not None)
        if not (is_clone_pair or is_property_pair
                or (is_plain_stub_set
                    and _witness("method.overload_set_call"))
                or literal_stub_set):
            return note_detail("method.overload_set")
    ret = analyzer.get_expr_type(e)
    # A TypeParamRef result (`self.get() -> T` in a generic body) emits the
    # same bare `recv.method(args)`; the POSITIONS it can compose into gate
    # their own family checks (a T decl/arg rejects there), so admitting it
    # here only opens the T-operand compares and their siblings.
    if not (_resolved_scalar(ret, analyzer) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or _tparam_value(ret)
            or _callable_value(ret)
            # A Span result (`self.__span__()` feeding a SpanIter ctor):
            # a VALUE view, the same bare `recv.method(args)` render --
            # consuming positions gate their own family checks.
            or (_span_slot(ret, analyzer)
                and _witness("method.span_ret"))
            # A VALUE-tuple result (`getsockname() -> tuple[str, Int32]`)
            # emits the same bare `recv.method(args)` prvalue; like the
            # TypeParamRef admission above, every consuming position
            # (subscript read, decl slot, arg, unpack source) gates its own
            # family, so admitting it here only opens those gated sinks.
            or _value_tuple_nested(ret, analyzer) is not None
            # An F1-record return is admitted at the owned-record decl sink
            # (`Rec r = b.build();`, record_ret_ok -- `is_rvalue_source`,
            # checked at the decl gate, keeps a `T&` borrow return out there)
            # and at the field-receiver position (`h.boxed.get().x`, where
            # rvalue and borrow returns render the same bare postfix member).
            or (record_ret_ok and _f1_record(ret, analyzer))
            # A protocol borrow return used as a `.`-access receiver
            # (`box.get() -> Pet&`, then `.name()`): renders the bare postfix
            # member like an F1-record borrow return.
            or (record_ret_ok and _protocol_binding(ret) is not None)
            # A recursive-union WRAPPER borrow return at a BORROW_BIND sink
            # (`show(v.inner.get())` -- the accessor's `Value&` binds the
            # `const Value&` wrapper slot inline, the record-borrow row's
            # wrapper twin; the arg side is
            # `_recursive_union_borrow_call_arg`).
            or (record_ret_ok
                and recursive_union_alternatives(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                    if isinstance(ret, TpyType) else None) is not None
                and _witness("method.ru_wrapper_ret"))
            # A VALUE-typed native iterator result at a STORAGE sink
            # (`it: SpanIter[Int32] = a.__iter__()` -- the plain spelled
            # copy decl `_native_iter_value_slot` already admits; the bare
            # `recv.method(args)` render is a prvalue like the span row).
            or (storage_ret_ok
                and _native_iter_value_slot(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                    if isinstance(ret, TpyType) else None, analyzer)
                and _witness("method.native_iter_ret"))
            # A borrow container return at the alias-decl sink
            # (`std::vector<T>& items = c.get_item();` -- a substituted
            # T-return; the REF_ALIAS decl gate pinned the lvalue-ness).
            or (record_ret_ok and _alias_ref_container(ret))
            # Storage sinks only (the tuple-unpack source; the record-method
            # sibling of free-call lowering's storage_ret_ok escape). A span
            # result is a by-value view landing bare in its decl slot
            # (`std::span<T> s = b.as_span();`). An owned-optional record
            # return (`-> Own[Rc[T]] | None`) lands bare in its registered
            # `std::optional<T>` decl slot the same way.
            # The owned-tuple move-out family at the tuple-unpack source
            # ONLY (`conn, _ = srv.accept()` -> the bare rvalue capture +
            # per-element std::move decls); an Own-tuple DECL stays an
            # unrouted slot, so the plain storage escape must not admit it.
            or (owned_tuple_ret_ok
                and _owned_tuple_call_ret(ret, analyzer) is not None)
            # The borrow-tuple local decl (`auto p = m.pair(c);`): the
            # `auto` slot binds the pointer-repr result whole, so the call
            # renders bare -- the record-method twin of the free-call
            # `call.btuple_slot` row (the decl arm owns the sink gating).
            # The mixed owned+borrow return (`auto x = m.mixed(c);`) rides
            # the same bare render (`_f1_tuple` admits Own elements; the
            # alias-decl arm gated the slot).
            or (btuple_ret_ok and _f1_tuple(ret, analyzer) is not None
                and _witness("method.btuple_slot"))
            # The union-switch CALL-subject sink (`match p.choose(d):`): a
            # non-wrapper ptr-variant UNION return is consumed whole by the
            # by-value dispatch local (`auto __match_subject_N = <call>;`);
            # every other consumer of a union result keeps rejecting.
            or (union_subject_ret_ok
                and isinstance(ret, TpyType)
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret))), UnionType)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    ret))).uses_pointer_repr()
                and not unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    ret))).needs_wrapper()
                and _witness("method.union_subject_ret"))
            # A VALUE Array result (`use_array(b.get_data())` -- a
            # substituted `Array[T, N]` return): std::array is a VALUE
            # container, so the prvalue lands bare at any sink -- arg slots
            # bind the const-ref inline, decl slots take the spelled copy --
            # exactly like a span, with no borrow/storage duality.
            or (isinstance(ret, TpyType)
                and is_array(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret))))
                and _witness("method.array_value_ret"))
            or (storage_ret_ok
                and (_storage_call_ret(ret, analyzer) is not None
                     or _span_value(ret)
                     or _storage_optional_return_type(
                            unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(ret)))
                            if isinstance(ret, TpyType) else None,
                            analyzer) is not None
                     # A value-opt OWNED-view / SCALAR result (`host =
                     # full.hostname` -> `std::optional<std::string> host =
                     # ...;`, `port = full.port` -> `std::optional<BigInt>
                     # ...`): the by-value optional lands bare in its decl
                     # slot; the local registers as a value-opt binding.
                     or ((_value_opt_owned_view(ret, analyzer) is not None
                          or _value_opt_scalar(ret, analyzer) is not None)
                         and _witness("method.value_opt_view_ret"))))
            # A coro-factory result at the handle-binding sink (`m =
            # w.bump(5)` -> `m.emplace((*w).bump(5))`): the frame value is
            # consumed whole by the emplace; no value slot is involved. The
            # call expr's sema type is the ERASED protocol view
            # (`Cancellable[T]`) -- concreteness lives on the LOCAL, which is
            # what gates coro_factory_ok in the first place, and the fi gate
            # already required an async fi.
            or (coro_factory_ok and ret is not None
                and is_dyn_protocol(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret)))))
            # `suspend_ok` is the ERASED/BORROWED-await sibling (`await
            # tx.send(x)`): the whole operand is consumed by the skeleton's
            # emplace/move wrap -- no value slot exists, so the result TYPE
            # is vacuous (a concrete coro record like `_Send[T]`, an erased
            # view -- whatever sema stamped).
            or (suspend_ok and ret is not None)
            or (stmt_position and (ret is None or is_void_like_type(ret)))
            # A DISCARDED F1-record result (`s.get(url);` -- the Response
            # dropped at statement position): the render is the same bare
            # call whatever the ignored result, the record sibling of the
            # container family's stmt_storage_ok row.
            or (stmt_position and _f1_record(ret, analyzer)
                and _witness("method.record_discard"))
            # A DISCARDED container result (`g.get();` -- the guard payload
            # dropped): same bare call, the container sibling.
            or (stmt_position
                and _storage_call_ret(ret, analyzer) is not None
                and _witness("method.container_discard"))
            # A DISCARDED union result (`b.get_span();` -- the Own[union]
            # variant dropped at statement position): same bare call, the
            # union sibling.
            or (stmt_position and _call_ret_union_ok(ret, analyzer)
                and _witness("method.union_discard"))
            # A container return at the for-head ITERABLE sink (`for v in
            # g.get():`): a borrow return captures `auto& __obj_N =`, an
            # Own return the owning capture -- the route pinned the
            # lvalue-ness from the fi, the render is the same bare call.
            or (iterable_ret_ok and _nonvalue_container_ret(ret)
                and _witness("method.container_iterable"))
            # A value-repr Optional return at a WHOLE-optional sink
            # (`a.gettimeout() is None` / `== 0.0` -- the has_value /
            # std::optional mixed-compare renders take the bare call).
            or (value_opt_ret_ok and _value_opt_ret(ret))
            # A @property getter's PTR-repr Optional result at the same
            # whole-optional sink (`w.node is None`): the getter returns
            # the storage optional by cpp-ref -- the AST skips the
            # optional_to_ptr lift exactly on is_property_getter -- so the
            # has_value test reads the bare call. Plain methods keep the
            # borrow-form `T*` + nullptr compare and stay rejected here.
            or (value_opt_ret_ok and fi.is_property_getter
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret))), OptionalType))
            # The module-init pass-through write of a pointer-slot global
            # (`g = h.find(k);`): a BORROW-returning ptr-repr Optional
            # result IS the `T*` the slot holds, so it lands bare -- the
            # free-call row's method twin.
            or (ptr_opt_passthrough and _ptr_opt_borrow_call_ret(e, ret)
                and _witness("method.ptr_opt_passthrough"))
            # A RAW (statement-handled) @error_return call: the bare
            # `std::expected` member call renders whatever the SUCCESS type
            # -- the caller's unwrap block owns consumption, so the
            # ret-family rows above say nothing about this position.
            or (raw_stmt_handled and ret is not None)):
        return note_detail("method.ret_type")
    return True


def _optional_ptr_container_slot(ptype: 'TpyType | None',
                                 analyzer) -> 'OptionalType | None':
    """A pointer-repr Optional slot with a CONTAINER inner (`list[T] | None`
    -> `const std::vector<T>*`), or None -- the container twin of
    `_optional_ptr_arg_slot` (which is F1-record-only). Three faces are
    lowered for it: the `nullptr` literal, the bare-container-name
    address-of (`&(name)`), and the temps-gated container-LITERAL temp
    (`&(__tmp_N)`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if not (isinstance(pt, OptionalType) and pt.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(pt.inner)
    if is_list(inner) or is_dict(inner) or is_set(inner):
        return pt
    return None


def _optional_ptr_container_arg(a: TpyExpr, ptype: 'TpyType | None',
                                declared: dict[str, TpyType],
                                analyzer) -> bool:
    """Admission twin of the container-inner optional-ptr rows in
    `_lower_call_arg`: a `None` literal, or a NAME declared as the matching
    bare container (an optional-declared or narrowed name stays rejected --
    its C++ binding is already the pointer / needs the pass face)."""
    ot = _optional_ptr_container_slot(ptype, analyzer)
    if ot is None:
        return False
    if isinstance(a, TpyNoneLiteral):
        return True
    if not isinstance(a, TpyName) or a.name == "self":
        return False
    dt = declared.get(a.name)
    if dt is None:
        return False
    du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    return du == unwrap_readonly(ot.inner)


def _optional_ptr_container_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                        analyzer) -> bool:
    """A container LITERAL into a pointer-repr Optional[container] slot
    (`s.get(url, {"db": "das"})` at a `dict[str, str] | None` param): the
    AST hoists the typed temp at the statement flush and passes its address
    (`__tmp_N = ordered_map<...>({...}); s.get(url, &(__tmp_1))` --
    `_gen_optional_ptr_arg`'s temporary face). Temp-hoisting, so admitted
    only under temps_ok (callers gate); element admission is the shared
    container-literal slice against the slot's inner."""
    ot = _optional_ptr_container_slot(ptype, analyzer)
    if ot is None:
        return False
    if not isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        return False
    return _container_literal_shape_ok(a, unwrap_readonly(ot.inner), analyzer)


def _struct_proto_union_arg(a: TpyExpr, ptype: 'TpyType | None',
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare in-scope NAME into a slot that is a UNION of STRUCTURAL
    protocols (`ArrayList.extend`'s `Spannable[T] | Iterable[Own[T]]`):
    the C++ method is a template whose concept picks the branch, so the arg
    renders bare -- no adapter, no span conversion, no variant lift. Dynamic
    protocol members are excluded (they take the adapter wrap), as is any
    non-protocol member (a real variant slot).

    SOURCE FAMILIES are restricted to the verified ones (container / span /
    record), mirroring the ctor twin's restriction. The restriction is
    DEFENSIVE at today's slots -- sema rejects an off-family source against
    them (a str arg is a type error, not a fallback) -- but a str/bytes
    source would respell through the view family, so the row must not admit
    one if a future slot accepts it.

    LOAD-BEARING COINCIDENCE: a movable last-use container renders bare on
    BOTH paths only because the AST's consuming `own_iter(std::move(..))`
    rewrite is keyed on a BARE `Iterable[Own[T]]` ptype and misses a
    UnionType. Widening that keying to unions means this row needs the same
    rewrite; a pin covers the current agreement."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if not isinstance(pt, UnionType):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    is_container = (is_list(at) or is_dict(at) or is_set(at) or is_array(at)
                    or is_span(at))
    if not (is_container or _f1_record(at, analyzer)):
        return False
    for m in pt.members:
        mu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(m)))
        if isinstance(mu, OwnType):
            mu = unwrap_readonly(mu.wrapped)
        if not is_protocol_type(mu) or is_dyn_protocol(mu):
            return False
    return bool(_witness("method.struct_proto_union_arg"))


def _union_ctor_temp_arg(a: TpyExpr, ptype: 'TpyType | None', analyzer) -> bool:
    """A member-typed record-ctor rvalue into a (non-Own) pointer-variant
    union slot -- `_gen_union_arg`'s rvalue branch: the ctor hoists a named
    temp and the variant lifts its address (`pv{&__tmp_N}`). A SCALAR
    type-ctor rvalue (`check(Int32(1))` on a mixed union) takes the same
    branch -- the AST render is member-shape-blind
    (`int32_t __tmp_N = 1;` + `pv{&__tmp_N}`), the temp init being the
    ctor's ordinary folded render. Temp-hoisting, so the caller admits it
    only under `temp_args`."""
    slot = _arg_ptr_union_slot(ptype, analyzer)
    if slot is None:
        return False
    ut, _deep_const = slot
    if not isinstance(a, TpyCall) or not is_rvalue_source(analyzer, a):
        return False
    at = analyzer.get_expr_type(a)
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return False
    if _eligible_scalar(at):
        return True
    # A GENERIC member ctor (`Outer(Box("abc"))` -- the inferred Box[str]
    # instantiation) hoists the same named temp + address lift
    # (`Box<std::string> __tmp_N = Box<std::string>("abc");` +
    # `pv{&__tmp_N}`); the instantiation form owns its own callee checks.
    return _ctor_shape_ok(a, analyzer) or _ctor_instantiation_ok(a, analyzer)


def _record_method_arg_ok(
        a: TpyExpr, ptype: 'TpyType | None', index: int, overload,
        locals_: dict[str, TpyType], analyzer, *, temps_ok: bool,
        narrowed: 'set[str] | frozenset[str]') -> bool:
    return (_lambda_routable(a, analyzer)
            # The rest of the callable family: each renders itself
            # independently of the slot, so the free-call rows carry over to
            # an `Fn[...]` / `Callable` method slot unchanged.
            or _func_ref_routable(a, analyzer)
            or _callable_value_pass_arg(a, locals_, analyzer)
            or _callable_object_arg(a, ptype, locals_, analyzer)
            or (_plain_scalar_slot(ptype, analyzer)
             and _resolved_scalar(analyzer.get_expr_type(a), analyzer))
            # A scalar VALUE into a bare `T` slot (a still-PENDING deferred
            # generic's minimal fi carries the RAW method params, so the
            # slot arrives unsubstituted): `val_or_ref_t<T>` binds the
            # scalar by value and the render is bare on both paths -- the
            # arg twin of the T-field-write row. Non-scalar args keep
            # their per-shape gates (a record/container into T carries
            # borrow/move questions this row does not answer).
            or (_is_type_param_slot(ptype)
             and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
             and _witness("method.tparam_scalar_arg"))
            or _float_literal_pass_through_arg(a, ptype, locals_, analyzer)
            or _int_literal_bigint_arg(a, ptype, locals_, analyzer)
            or _str_pass_through_arg(a, ptype, locals_, analyzer)
            or _bytes_pass_through_arg(a, ptype, locals_, analyzer)
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _ptr_pass_through_arg(a, ptype, locals_, analyzer)
            or _value_tuple_pass_through_arg(a, ptype, locals_, analyzer)
            # A container/record NAME or None at a NULLABLE static-protocol
            # method slot (`c2.update(nums, more)` -> `&(more)` /
            # `update(nums, static_cast<std::nullptr_t*>(nullptr))`): the
            # same lift as the free/ctor positions (the required union has
            # no None member and stays bare).
            or (isinstance(a, (TpyName, TpyNoneLiteral))
                and _protocol_union_arg(a, ptype, locals_, analyzer)
                in ("addr", "nullproto")
                and _witness("arg.nullable_proto_addr"))
            or _span_coerce_arg(a, ptype, locals_, analyzer)
            or _slice_ctor_pass_through_arg(a, ptype, locals_, analyzer)
            or _own_scalar_rvalue_arg(a, ptype, locals_, analyzer)
            # A source already typed as the WHOLE value-repr Optional passes
            # bare into its same-optional method slot (`conn.request(m, u,
            # body)` on `body: bytes | None`) -- the free-call ladder's row.
            or _value_opt_pass_through_arg(a, ptype, locals_, narrowed,
                                           analyzer)
            # The callable twin of the row above -- the method loop is
            # target-less, so the whole optional goes bare exactly like the
            # free-call render.
            or _value_opt_callable_pass_arg(a, ptype, locals_, narrowed,
                                            analyzer)
            # The VIEW twin (`conn.request(m, u, body, hdrs)` at
            # `body: bytes | None`): target-less method loop -> the whole
            # optional passes bare; the free-call position keeps its shim.
            or _value_opt_view_whole_arg(a, ptype, locals_, narrowed,
                                         analyzer)
            # A record RVALUE into a BY-VALUE record method slot
            # (`waw.fromutc(datetime(..))`): a ValueType record param is no
            # ref slot, so there is no temp cascade -- bare on both paths,
            # exactly as at a ctor slot.
            or _value_record_rvalue_arg(a, ptype, analyzer)
            # An Array-returning call rvalue at a matching Array slot binds
            # the std::array prvalue inline -- the free ladder's row.
            or _value_array_call_arg(a, ptype, analyzer)
            or _own_record_rvalue_arg(a, ptype, locals_, analyzer)
            # An Own[T]-returning method rvalue at the record's own OPEN
            # `Own[T]` slot (`self._storage.init(ui, other._storage.take(
            # ui))`) binds the T&& slot bare -- same-T pairing.
            or _own_tparam_method_rvalue_arg(a, ptype, analyzer)
            # `h.store(copy(p))` -- the copy-construct rvalue (`Point(p)`)
            # binds the `T&&` slot exactly like any record rvalue, which is
            # why the container-method loop already carries the row.
            or _copy_record_own_arg(a, ptype, analyzer)
            or _own_move_arg(a, ptype, locals_, analyzer)
            # The Own-slot copy half (`auto __tmp_N = b;` +
            # `recv.m(std::move(__tmp_N))`): a user method's Own param is a
            # real by-value C++ slot, so the copy hoists exactly like the
            # free-call/ctor rows -- flush-gated; `_method_arg` threads the
            # scoped temp_args into `_lower_call_arg`'s copy+move arm.
            or (temps_ok and _own_lvalue_arg(a, ptype, locals_, narrowed,
                                             analyzer))
            # A record NAME moved into an `Optional[Own[T]]` method slot
            # (`c.take(p)` at `Own[Point] | None` -- bare `std::move(p)`
            # into the by-value `std::optional<Point>` param): the
            # free/marker ladders' row; the shared lowering arm enforces
            # the move verdict and rejects the copy shape.
            or _opt_own_record_name_arg(a, ptype, locals_, analyzer)
            is not None
            # The `Own[record | None]` method slot (`h.store(P(42))` bare
            # ctor rvalue; NAME moves / same-Optional call rvalues bare) --
            # the free/ctor ladders' rows.
            or _own_optional_record_rvalue_arg(a, ptype, analyzer)
            or _own_opt_slot_arg(a, ptype, locals_, analyzer)
            or _optional_ptr_arg(a, ptype, locals_, analyzer, temps_ok=False)
            or _container_pass_through_arg(a, ptype, locals_, analyzer)
            or _record_pass_through_arg(a, ptype, locals_, analyzer)
            or _method_ctor_rvalue_arg(
                a, ptype, index, overload, locals_, analyzer)
            # A concrete conformer into an `Own[@dynamic P]` method slot
            # (`b.set(Dog(...))`): the make_unique / make_adapter wrap,
            # verdict-keyed via the shared classifier (the
            # `_dyn_own_conformer_arg` row in `_lower_call_arg`).
            or _dyn_own_conformer_arg(a, ptype, locals_, analyzer) is not None
            or (temps_ok
                and _tparam_slot_temp_arg(a, ptype, index, overload,
                                          analyzer) is not None)
            or _struct_proto_union_arg(a, ptype, locals_, analyzer)
            # A tuple LITERAL at a tuple method slot (`h.set((x, y))` ->
            # `h.set(std::tuple<Box*, Box*>{&(x), &(y)})`). The free-call
            # ladder's row: `_lower_call_arg`'s tuple-literal arm is already
            # shared with the method path, so only this admission was missing;
            # the borrow/value builders own the per-element verdict.
            or _tuple_literal_arg(a, ptype)
            or _method_value_union_arg(a, ptype, locals_, analyzer)
            # A member ctor RVALUE into a POINTER-variant method slot
            # (`p.set_pet(Cat("Mittens"))` -> `Cat __tmp_N = Cat("Mittens");
            # p.set_pet(std::variant<Cat*, Dog*>{&__tmp_N});`) -- the
            # free-call ladder's row, lowered through the same
            # `_lower_union_arg_lift`. Temp-hoisting, so the method loop
            # threads its flush for this slot too.
            or (temps_ok and _union_ctor_temp_arg(a, ptype, analyzer))
            # ... and the bytes-literal rvalue at a beyond-the-slice union
            # slot (`s.post(url, b"payload")` at `bytes | dict | None`),
            # same hoist+addr render, own slot check.
            or (temps_ok and _union_bytes_literal_temp_arg(
                a, ptype, analyzer) is not None)
            # A same-union NAME into a POINTER-variant method slot passes
            # bare when the callee's param carries no deep-const verdict
            # (`p.set_pet(new_pet)`); a dcbp slot takes the
            # ptr_variant_to_const wrap (unionlift.const_wrap -- the
            # method loop threads readonly_target), un-narrowed NAMES only
            # (a narrowed arg renders the bare extraction, wrap skipped).
            or (_union_pass_through_arg(a, ptype, locals_, analyzer)
                and (not (overload is not None
                          and overload.deep_const_borrow_params
                          and index in overload.deep_const_borrow_params)
                     or (isinstance(a, TpyName) and a.name not in narrowed))
                and _witness("method.union_pass_arg"))
            or (temps_ok and _value_union_temp_arg(
                a, ptype, locals_, narrowed, analyzer))
            # A scalar value / `None` into a value-repr Optional[scalar]
            # slot renders bare / `std::nullopt` -- `sock.settimeout(0.5)`.
            or _value_opt_scalar_value_arg(a, ptype, analyzer)
            # A str LITERAL into a value-repr Optional[str] slot renders
            # bare the same way (`jar.get("missing", "fallback")`) -- the
            # TypedDict-ctor face's row, shared with the ctor arg loop.
            or _str_literal_value_opt_arg(a, ptype)
            or _none_value_opt_arg(a, ptype, analyzer) is not None
            # A `None` literal into a unit slot (`fut.set_result(None)` on a
            # `Future[None]` -- the substituted T=None param): the bare
            # `std::monostate{}`, the record-method twin of the qualcall row;
            # `_lower_call_arg`'s unit-none tail renders it.
            or _none_unit_arg(a, ptype) is not None
            # A list literal into an `Own[list]` user-method slot renders
            # the bare in-place brace (`g.set([7, 8, 9])` -> `set({7, 8,
            # 9})`), the record-method twin of the qualcall row.
            or _own_container_literal_arg(a, ptype, analyzer)
            # The pointer-repr Optional[container] slot faces: `None` ->
            # `nullptr`, a bare matching container name -> `&(name)` (the
            # free-call rows), and (temps only) a container literal ->
            # the `&(__tmp_N)` typed temp (`s.get(url, None, {...})`).
            # The shared optptr.none/name witnesses also fire from free-call
            # corpus sites, so the METHOD-position name face is guarded by
            # its unit pin, not the zero-witness metric.
            or _optional_ptr_container_arg(a, ptype, locals_, analyzer)
            or (temps_ok
                and _optional_ptr_container_literal_arg(a, ptype, analyzer))
            # A bare in-scope name into a protocol slot whose verdict is
            # temp-free (`cv.wait(g)` -- the structural monomorphized bind).
            or _protocol_bare_name_arg(a, ptype, locals_, analyzer)
            # A dict/set LITERAL at a record method's container slot renders
            # the spelled container in place (`os.environ.update({...})` ->
            # `update(::tpy::ordered_map<..>({{..}}))`), like the stub loop.
            or _container_literal_method_arg(a, ptype, analyzer)
            # The M4c wrapper-slot rows, the record-method twins of the
            # free-call ladder's: a same-wrapper NAME binds bare
            # (`h.matches(probe)`); a member-typed NAME / literal /
            # member-ctor rvalue hoists the typed temp (flush-gated).
            or _ru_wrapper_name_arg(a, ptype, locals_, narrowed, analyzer)
            or (temps_ok and _ru_wrapper_member_name_arg(
                a, ptype, locals_, narrowed) is not None)
            or (temps_ok and _ru_wrapper_scalar_literal_arg(
                a, ptype, analyzer) is not None)
            or (temps_ok and _ru_wrapper_member_rvalue_arg(
                a, ptype, analyzer) is not None)
            or note_detail("method.arg_shape"))

def _view_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                               analyzer, *, stmt_position: bool,
                               storage_ret_ok: bool,
                               borrow_ret_ok: bool = False) -> bool:
    """A str/StrView value-view receiver's builtin method call -- the
    `_gen_method_call` builtin-method arm (`native_function or cpp_template`,
    line-3700 block) reduced to its pass-through subset. The shared marker /
    receiver-shape / fi-kind / arity rejects already ran in
    method-call lowering; the receiver is a bare str-slice name or str field
    (whichever passed that receiver-shape check).

    The AST renders the receiver via `_gen_builtin_method_receiver` (a bare
    `gen_expr` for a plain str name / field) and the call via
    `gen_method_from_function_info` -> `gen_call_from_fi`: a @cpp_template
    expands `{self}`/`{0}`.. positionally, a @native(function=True) prepends
    the receiver (`::sym(recv, args)`). Lowering reaches the SAME general
    THIRMethodCall arm (receiver + args + cpp_template/native_function_name),
    which mirrors those two spellings exactly -- so admission is a gate
    widening only, no new emit.

    Method fi: @cpp_template (positional-only, no `{cpp}` return substitution)
    or @native(function=True). A `{cpp}` placeholder substitutes the return
    type (`str_family` templates carry none, but reject defensively). Owned-str
    results (`s.upper()`) land bare in the str sinks like a container `pop()`;
    a bytes result (`s.encode()`) rides the view/owned form tag.

    Args: the free-call pass-through set (str-slice / scalar / char / bytes /
    enum / ptr into non-Own slots). A str method's args render identically
    under `inline_template=True` (AST) and `_lower_call_arg(method_arg=True)`
    (THIR) for these shapes -- both bare. Own slots / temp-hoisting arg shapes
    stay AST (`_str_pass_through_arg` rejects Own).

    Result: an eligible scalar / char / str-value / bytes-value / enum / ptr,
    or void (None) in statement position."""
    if not (fi.native_function or fi.cpp_template):
        return note_detail("method.view.fi_kind")
    # A `{cpp}` template placeholder substitutes the return-type spelling -- the
    # generics-frontier machinery, not reproduced here.
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.view.cpp_ret_substitution")
    return (_stub_method_ret_ok(
                analyzer.get_expr_type(e), analyzer,
                stmt_position=stmt_position, storage_ret_ok=storage_ret_ok,
                enum_ok=True, ptr_ok=True, callable_ok=False,
                span_storage_ok=False, stmt_storage_ok=True)
            or note_detail("method.view.ret_type"))


def _bytearray_recv(recv_type: 'TpyType | None') -> bool:
    """A bytearray receiver -- the owned mutable twin of the bytes view
    family: every stub method is @native (member renames like `push_back` /
    bare `clear`, or function=True `::tpy::bytearray_*(recv, args)`), all
    rendered by the general THIRMethodCall arm."""
    if recv_type is None:
        return False
    return is_bytearray_type(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type))))


def _bytearray_method_call_supported(
        e: TpyMethodCall, fi, locals_: dict[str, TpyType], analyzer, *,
        stmt_position: bool, storage_ret_ok: bool,
        borrow_ret_ok: bool = False) -> bool:
    """A bytearray receiver's builtin method call: the view family's
    admission shape, plus MEMBER natives (`append` -> `recv.push_back(arg)`,
    bare `clear`) the view gate's function-only check would reject. The
    receiver-shape / marker / arity rejects already ran in method-call
    lowering; renders are the same two spellings the general THIRMethodCall
    arm mirrors (member rename / function=True receiver-prepend)."""
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.bytearray.cpp_ret_substitution")
    return (_stub_method_ret_ok(
                analyzer.get_expr_type(e), analyzer,
                stmt_position=stmt_position, storage_ret_ok=storage_ret_ok,
                enum_ok=False, ptr_ok=False, callable_ok=False,
                span_storage_ok=False, stmt_storage_ok=True)
            or note_detail("method.bytearray.ret_type"))


def _view_method_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                        locals_: dict[str, TpyType], analyzer, *,
                        param_names: 'set[str] | frozenset[str]',
                        narrowed: 'set[str] | frozenset[str]') -> bool:
    return ((_scalar_pass_through_slot(ptype, analyzer)
             and _resolved_scalar(analyzer.get_expr_type(a), analyzer))
            or _str_pass_through_arg(a, ptype, locals_, analyzer)
            or _bytes_pass_through_arg(a, ptype, locals_, analyzer)
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _ptr_pass_through_arg(a, ptype, locals_, analyzer)
            # A container literal into the structural Iterable slot of a str
            # view method (`",".join(["a", "b"])` -> the resolved
            # `std::array<std::string, N>{..}` inline) -- the same
            # `_lower_call_arg` literal arm the native free loop renders.
            # The container-NAME row binds bare here too: the consuming
            # `own_iter` wrap keys on an `Own[..]`-element slot, which the
            # borrowing `Iterable[str]` join slot is not.
            or _native_iterable_literal_arg(a, ptype, analyzer)
            or _native_iterable_container_arg(a, ptype, locals_)
            or _native_iterable_field_arg(a, ptype, locals_, analyzer)
            or note_detail("method.view.arg_shape"))


class _MethodRecvFamily(NamedTuple):
    """One builtin-stub / protocol receiver family's paired method-call gate
    dispatch. The SHAPE gate (the plain-method arm in expressions.py) and the
    ARG gate (`_method_call_arg_ok`) both dispatch through
    `_method_recv_family`, so a family's shape admission and arg admission
    cannot drift apart -- widening or adding a family is one table row. Shape
    fns share the signature (e, fi, locals_, analyzer, *, stmt_position,
    storage_ret_ok, borrow_ret_ok) and arg fns (a, ptype, locals_, analyzer,
    *, param_names, narrowed); a family ignores the knobs it has no rows for. `stub_recv`
    marks the builtin-stub receivers whose args render through gen_call_arg's
    `_args()` loop (raw param type threaded into literal renders)."""
    shape_ok: Callable[..., bool]
    arg_ok: Callable[..., bool]
    stub_recv: bool


def _container_method_elem(t: 'TpyType | None', analyzer) -> bool:
    """A container whose element family only the METHOD-CALL arm admits: an
    open `T`, the unit type, a `Callable` value or a non-wrapper union. The
    other element predicates are read-shaped -- they gate subscript / decl /
    for-each consumers too, where these elements would need lifts this slice
    does not mirror. A method receiver renders bare whatever the element is
    (`xs.push_back(...)`, `this->items.push_back(...)`), so the element only
    reaches the arg and result gates, which keep deciding per shape."""
    def elem_ok(a: 'TpyType | int') -> bool:
        if not isinstance(a, TpyType):
            return False
        au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a)))
        if isinstance(au, OwnType):
            au = unwrap_readonly(au.wrapped)
        return (_is_type_param_slot(a)
                or is_void_like_type(au)
                or _callable_value(a)
                # An owned-bytes element (`list[bytes]` stores
                # vector<uint8_t>): inserts render the owned literal /
                # value; reads stay gated per consumer.
                or is_bytes_type(au)
                # Recursive-alias wrappers hold their members indirectly --
                # their inserts carry a wrap the bare push_back does not.
                or (isinstance(au, UnionType) and not au.needs_wrapper())
                # A ptr-Optional-element tuple (`list[tuple[P | None, ..]]`):
                # the insert's consuming tuple_to_storage_move lift is the
                # arg gate's own row; reads keep their per-consumer gates.
                or (isinstance(au, TupleType)
                    and au.has_pointer_repr_element()
                    and _tuple_elem_slots_ptr_optional(au)))

    return _container_elem_family(t, analyzer, elem_ok)


def _container_method_recv(recv_type: 'TpyType | None', analyzer,
                           tparam_bounds: 'dict | None') -> bool:
    return (_container_elem_family(recv_type, analyzer, lambda _a: True,
                                   span_elem_ok=lambda _a: True)
            or _set_method_recv(recv_type, analyzer))


def _protocol_method_recv(recv_type: 'TpyType | None', analyzer,
                          tparam_bounds: 'dict | None') -> bool:
    if (_protocol_binding(recv_type) is not None
            or _bounded_tparam_protocol(recv_type, tparam_bounds) is not None):
        return True
    # A None-NARROWED Optional[@dynamic P] pointer binding (`p.name()` on
    # `p: Pet | None` proven non-None): the binding is the nullable `Pet*`
    # / `const Pet*`, the member access spells `->` via the lowering
    # pointer set (is_arrow), and shape/args share the protocol family's
    # gates. Sema forbids the un-narrowed call, so reaching lowering
    # implies the proof. Structural-protocol Optionals stay out (their
    # monomorphized spelling is unwitnessed).
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
         if isinstance(recv_type, TpyType) else None)
    if isinstance(u, OptionalType) and u.uses_pointer_repr():
        inner = unwrap_readonly(u.inner)
        return (isinstance(inner, NominalType) and is_dyn_protocol(inner)
                and _witness("method.opt_dyn_recv"))
    return False


def _own_dyn_method_recv(recv_type: 'TpyType | None', analyzer,
                         tparam_bounds: 'dict | None') -> bool:
    """An `Own[@dynamic P]` receiver (`std::unique_ptr<P>`): the member
    access spells `->` (the emit node's `_recv_own_dyn` mirror); shape and
    args share the protocol family's gates -- the AST's user-record guard
    skips a protocol type the same way, so args take the free-call
    `_args()` fallback renders."""
    if not isinstance(recv_type, TpyType):
        return False
    u = unwrap_send_sync(recv_type)
    return (isinstance(u, OwnType)
            and isinstance(u.wrapped, NominalType)
            and is_dyn_protocol(u.wrapped))


def _bytearray_method_recv(recv_type: 'TpyType | None', analyzer,
                           tparam_bounds: 'dict | None') -> bool:
    return _bytearray_recv(recv_type)


def _scalar_method_recv(recv_type: 'TpyType | None', analyzer,
                        tparam_bounds: 'dict | None') -> bool:
    """A SCALAR value receiver's builtin method call (`v.as_integer_ratio()`,
    `n.bit_length()`): every stub is @native(function=True) / @cpp_template,
    rendered by the general THIRMethodCall arm exactly like the view
    family's."""
    return _resolved_scalar(recv_type, analyzer)


def _scalar_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                  analyzer, *, stmt_position: bool,
                                  storage_ret_ok: bool,
                                  borrow_ret_ok: bool = False) -> bool:
    """The view family's admission shape over a scalar receiver, plus a
    VALUE-TUPLE result at storage/statement sinks (`num, den =
    v.as_integer_ratio()` -> the unpack's `auto __tup_N = ...` source;
    a discarded probe call renders the bare statement)."""
    if not (fi.native_function or fi.cpp_template):
        return note_detail("method.scalar.fi_kind")
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.scalar.cpp_ret_substitution")
    ret = analyzer.get_expr_type(e)
    if (_value_tuple(ret, analyzer) is not None
            and (storage_ret_ok or stmt_position)):
        return _witness("method.scalar_tuple_ret")
    return (_stub_method_ret_ok(
                ret, analyzer,
                stmt_position=stmt_position, storage_ret_ok=storage_ret_ok,
                enum_ok=True, ptr_ok=True, callable_ok=False,
                span_storage_ok=False, stmt_storage_ok=True)
            or note_detail("method.scalar.ret_type"))


def _view_method_recv(recv_type: 'TpyType | None', analyzer,
                      tparam_bounds: 'dict | None') -> bool:
    """A str/StrView or bytes/BytesView value receiver: both render args
    through the same builtin-stub loop, so they share the view rows."""
    return (_resolved_str_value(recv_type, analyzer) is not None
            or _resolved_bytes_value(recv_type, analyzer) is not None)


_METHOD_RECV_FAMILY_TABLE: tuple = (
    (_container_method_recv,
     _MethodRecvFamily(shape_ok=_container_method_call_supported,
                       arg_ok=_container_method_arg_ok, stub_recv=True)),
    (_protocol_method_recv,
     _MethodRecvFamily(shape_ok=_protocol_method_call_supported,
                       arg_ok=_protocol_method_arg_ok, stub_recv=False)),
    # The Own[@dynamic P] handle receiver shares the protocol family's
    # gates; only the `->` access differs (the emit node's own-dyn mirror).
    (_own_dyn_method_recv,
     _MethodRecvFamily(shape_ok=_protocol_method_call_supported,
                       arg_ok=_protocol_method_arg_ok, stub_recv=False)),
    # Bytearray shapes admit MEMBER natives the view gate rejects; its args
    # are the view set (same bare renders on both paths).
    (_bytearray_method_recv,
     _MethodRecvFamily(shape_ok=_bytearray_method_call_supported,
                       arg_ok=_view_method_arg_ok, stub_recv=True)),
    (_view_method_recv,
     _MethodRecvFamily(shape_ok=_view_method_call_supported,
                       arg_ok=_view_method_arg_ok, stub_recv=True)),
    # Scalar receivers' stub methods share the view family's arg rows (the
    # same builtin-stub loop renders them).
    (_scalar_method_recv,
     _MethodRecvFamily(shape_ok=_scalar_method_call_supported,
                       arg_ok=_view_method_arg_ok, stub_recv=True)),
)


def _method_recv_family(recv_type: 'TpyType | None', analyzer,
                        tparam_bounds: 'dict | None') -> '_MethodRecvFamily | None':
    """Classify a plain method call's receiver into its stub/protocol family
    -- the ONE family list both method gates consult. None -> the residual
    dispatch (the ptr-template arm at the shape gate, the user-record path at
    the arg gate; a routed ptr-template call admits no args, so the record
    fallback never fires for one)."""
    for pred, family in _METHOD_RECV_FAMILY_TABLE:
        if pred(recv_type, analyzer, tparam_bounds):
            return family
    return None


def _user_iterator_iterable(u: 'TpyType | None', analyzer) -> bool:
    """The `__iter__`/`__next__` protocol-loop family: a protocol
    Iterator/Iterable value, or a user record declaring (or inheriting)
    either dunder -- the AST's universal `::tpy::__iter__` default."""
    if not isinstance(u, NominalType):
        return False
    if u.is_protocol:
        return u.name in ("Iterator", "Iterable")
    rec = analyzer.registry.get_record_for_type(u)
    if rec is None:
        return False
    return bool(
        analyzer.registry.get_method_overloads_with_parents(rec, "__iter__")
        or analyzer.registry.get_method_overloads_with_parents(rec, "__next__"))


def _iter_proto_call_ret(it: 'TpyCall | TpyMethodCall', analyzer) -> bool:
    """A non-generator call admitted as the universal-loop iterable: its
    result is the `__iter__`/`__next__` family -- an Iterator/Iterable
    protocol value (`reversed(xs)`, `zip(xs, ys)`) or a concrete
    user-iterator record (`SimpleIter(4)`) -- and NOT NativeIterable (which
    takes the AST's begin/end peephole, the container route)."""
    ret = analyzer.get_expr_type(it)
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if u is None or is_native_iterable(u, analyzer.registry):
        return False
    return _user_iterator_iterable(u, analyzer)


def _gen_recv_ctor_temp(obj: TpyExpr, analyzer) -> bool:
    """A generator-method receiver the AST lifts into a named local
    (`for v in Counter(3).each():` -> `Counter __tmp_N = Counter(..);` +
    `__tmp_N.each()`): the resumable frame / peephole captures the receiver
    by reference, so a temporary would dangle (_gen_method_call's
    is_temporary lift). Mirrored for the witnessed slice: a same-nominal
    ctor RVALUE whose expansion the ctor rows already render."""
    if not isinstance(obj, TpyCall):
        return False
    fi = obj.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if not is_rvalue_source(analyzer, obj):
        return False
    t = analyzer.get_expr_type(obj)
    if not isinstance(t, NominalType):
        return False
    return _ctor_shape_ok(obj, analyzer) or _ctor_instantiation_ok(
        obj, analyzer)


def _member_gen_call_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A member GENERATOR (or iterator-factory) call as a for-each iterable
    (`for x in obj.gen(n):` -- the iter_proto route). The factory call
    spells like any plain member call (its Iterator-family return never
    lands in a value slot -- it feeds the route's `auto __src_N` capture),
    so only the fi generator-kind reject and the per-receiver result-family
    gates are bypassed (via `iterable_override`); the receiver and args
    still lower through the standard member tail. Receivers: a bare
    in-scope name (`self` included) or the ctor-rvalue lift slice
    (`_gen_recv_ctor_temp`). A generic method routes when its inferred
    targs spell through the member tail's method_targs suffix
    (`f.items<int32_t>(42)`); omitted trailing defaults ride the emitted
    C++ signature (`_call_arity_ok`). Native / template callees and
    marker-bearing calls stay rejected."""
    # targs_ok: the member tail threads method_targs_cpp, and the
    # fi.type_params condition below requires the INFERRED args that
    # suffix spells -- an explicit-targs-only generic call still rejects.
    if (not _plain_member_call_markers_ok(e, targs_ok=True)
            or e.needs_optional_runtime_check):
        return False
    fi = e.resolved_function_info
    if fi is None:
        return False
    if not fi.is_generator and not _iter_proto_call_ret(e, analyzer):
        return False
    if not _plain_method_fi_ok(fi, generator_ok=True):
        return False
    if (fi.cpp_template is not None or fi.native_function
            or fi.native_name or fi.linkage != FunctionLinkage.DEFAULT):
        return False
    if fi.type_params and not (e.inferred_type_args
                               and not e.user_module_call
                               and not e.is_static_call):
        return False
    if not (isinstance(e.obj, TpyName) and e.obj.name in locals_):
        if not _gen_recv_ctor_temp(e.obj, analyzer):
            return False
    return _call_arity_ok(e, fi)


def _str_list_method_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A str-view method returning `Own[list[str]]` as a for-each iterable
    (`for w in s.split():`). The result is an rvalue -- the owning
    `auto __obj_N = ::tpy::str_split_whitespace(s);` capture (iterable_lvalue
    False, the same verdict the dict-view branch takes), iterated like any
    list[str] name; the str ELEMENT is checked by the caller's shared elem
    gate. Mirrors the marker / receiver / fi / arity / arg rejects of
    method-call lowering and the view-method checks, but swaps the
    value-result check for `is_list` (the str-list return the expr gate rejects
    at ret_type). The receiver is a bare str-slice or bytes-view name (the
    field-receiver and non-list shapes defer); the caller's shared elem gate
    pins the str/bytes element either way."""
    if not _plain_member_call_markers_ok(e) or e.needs_optional_runtime_check:
        return False
    if not (isinstance(e.obj, TpyName) and e.obj.name in locals_):
        return False
    recv_t = _method_receiver_type(e.obj, locals_, analyzer)
    if (_resolved_str_value(recv_t, analyzer) is None
            # The bytes twin (`for line in sent.split(b"\r\n"):` ->
            # `auto __obj_N = ::tpy::bytes_split(sent, ..)`): same owning
            # capture, iterated like a list[bytes] name; the caller's elem
            # gate pins the bytes element.
            and _resolved_bytes_value(recv_t, analyzer) is None):
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi):
        return False
    # The builtin-method arm (@native(function=True) / @cpp_template); a `{cpp}`
    # return substitution is the generics machinery, not mirrored here.
    if not (fi.native_function or fi.cpp_template):
        return False
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return False
    if len(e.args) != len(fi.params):
        return False
    # Own-stripped by get_expr_type; the str-list return the value-result expr
    # gate rejects. Only a list return (`split`/`rsplit`/`splitlines`) admits --
    # the caller's elem gate then pins the str element.
    ret = analyzer.get_expr_type(e)
    st = unwrap_readonly(unwrap_send_sync(ret)) if ret is not None else None
    if not is_list(st):
        return False
    return True

def _recv_family(t: 'TpyType | None', analyzer) -> str:
    """Coarse receiver-family label for the method-call reject detail."""
    if t is None:
        return "untyped"
    if is_str_type(t):
        return "str"
    if is_bytes_type(t):
        return "bytes"
    if is_list(t):
        return "list"
    if is_dict(t):
        return "dict"
    if is_set(t):
        return "set"
    if is_array(t):
        return "array"
    if isinstance(t, OptionalType):
        return "optional"
    if isinstance(t, UnionType):
        return "union"
    if isinstance(t, TupleType):
        return "tuple"
    if is_protocol_type(t):
        return "protocol"
    if isinstance(t, NominalType):
        return "record"  # non-F1 record (or native nominal)
    return type(t).__name__.removesuffix("Type").lower()

def _method_value_union_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free VALUE-union method-arg rows: a same-union name / a
    union-coerced literal into a value-variant method slot renders bare on
    both paths. Restricted to value unions -- their renders are const-blind
    (no pointee const spelling), so the own-record AST loop (which threads
    `is_readonly_target`) and the inherited-method first-pass loop (which
    omits it) emit identically. Pointer-variant method slots stay AST with
    the other deep-const rows."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    if _eligible_value_union(unwrap_readonly(unwrap_send_sync(pt))) is None:
        return False
    return (_union_pass_through_arg(a, ptype, locals_, analyzer)
            or _union_coerced_literal_arg(a, ptype, locals_, analyzer))

def _tparam_name_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType]) -> bool:
    """A NAME bound to the same bare T as an (unsubstituted) TypeParamRef
    slot -- the form-neutral pass inside a generic body
    (`Box[T](value)` with `value: T`). Both paths render the bare name."""
    if not isinstance(a, TpyName) or a.name == "self":
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    if not isinstance(pt, TypeParamRef):
        return False
    at = locals_.get(a.name)
    if at is None:
        return False
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(au, OwnType):
        au = unwrap_readonly(au.wrapped)
    return isinstance(au, TypeParamRef) and au.name == pt.name

def _none_unit_arg(a: TpyExpr, ptype: 'TpyType | None') -> 'NoneType | None':
    """A `None` literal into a unit slot (`Own[None]` / bare `None` -- a
    generic call's substituted T=None param): renders the bare
    `std::monostate{}` value on both paths. Returns the slot's NoneType."""
    if not isinstance(_peel_coerce(a), TpyNoneLiteral):
        return None
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    return pt if isinstance(pt, NoneType) else None

def _raw_record_method_fi(e: TpyMethodCall, locals_: dict[str, TpyType],
                          analyzer):
    """The receiver record's RAW method fi (TypeParamRef params intact --
    not the substituted resolved stub), or None for a non-record receiver.
    `_tparam_slot_temp_arg` keys its temp decision on the RAW param being a
    bare T, exactly like the AST's user-record loop. Resolved through the
    MRO (`get_method_overloads_with_parents`) so an INHERITED generic
    method sees the same fi the shape gate admitted -- an own-methods-only
    lookup would skip the temp arm the gate promised."""
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _method_receiver_type(e.obj, locals_, analyzer))))
    if isinstance(recv_t, OwnType):
        recv_t = unwrap_readonly(recv_t.wrapped)
    if not isinstance(recv_t, NominalType):
        return None
    ri = analyzer.registry.get_record_for_type(recv_t)
    if ri is None:
        return None
    overloads = analyzer.registry.get_method_overloads_with_parents(
        ri, e.method)
    return overloads[0] if overloads else None

def _tparam_slot_temp_arg(a: TpyExpr, ptype: 'TpyType | None', idx: int,
                          method_fi, analyzer) -> 'TpyType | None':
    """A temporary arg into a generic-record method's T slot, resolved
    non-value at the call site (`printer.get_str(Point(10, 20))`,
    `box_list.set([4, 5, 6])`): the RAW method param is a bare TypeParamRef
    (`param_val_or_ref_t<T>` -- an lvalue-ref binding), so the AST hoists
    the named temp `R __tmp_N = <target-typed init>;` (temps.create over
    the receiver-substituted type). Returns that resolved type, or None.
    `ptype` arrives already substituted (the resolved fi's param); a
    value-type resolution passes bare through the scalar rows instead, and
    a still-open T (a generic body's own T) stays out."""
    if method_fi is None or idx >= len(method_fi.params):
        return None
    raw = method_fi.params[idx].type
    if not _is_type_param_slot(unwrap_ref_type(raw)):
        return None
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if contains_type_param(pt) or pt.is_value_type():
        return None
    if isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                      TpyStrLiteral)):
        return pt
    if isinstance(a, (TpyCall, TpyMethodCall)) and is_rvalue_source(
            analyzer, a):
        return pt
    return None

def _method_ctor_rvalue_arg(a: TpyExpr, ptype: TpyType | None, idx: int,
                            method_fi, locals_: dict[str, TpyType],
                            analyzer) -> bool:
    """A record-ctor rvalue arg into a CONST same-record method slot
    (`a.combine(A(9))` where `other` is emitted `const A&`): the method-call
    arg loop has no ref-param rvalue-temp arm (unlike the free-fn loop), so
    `gen_call_arg` renders the ctor expansion inline -- the THIRCtorCall
    bytes. Admission requires the callee param be signature-const
    (`const_borrow_params`, the materialized `decide_param_const` verdict,
    the same fact `_param_is_const` reads for body locals): the MUTATED-ref
    shape (`a.absorb(A(4))`) inlines identically on the AST path but that
    render cannot compile (an rvalue never binds `A&`) -- the miscompile
    tracked in BUGS.md ("method-call record rvalue into a mutated ref param
    never temps"), kept unsupported rather than mirrored. Same-nominal
    slots only (an rvalue UPCAST is deferred with the other rvalue rows); a
    FREE-fn ctor rvalue hoists a `__tmp_N` even into a const slot (the
    ref-param temp arm) and stays off free-call lowering's arms entirely."""
    if not isinstance(a, TpyCall):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_ref_type(unwrap_send_sync(pt))
    if isinstance(pt, (ReadonlyType, OwnType)):
        return False
    if not (isinstance(pt, NominalType) and pt.is_user_record):
        return False
    if pt != analyzer.get_expr_type(a):
        return False
    cbp = method_fi.const_borrow_params
    if cbp is None:
        # An UN-ANALYZED callee (a @native record's body-less stub method):
        # no const verdict exists. The AST inlines the ctor expansion
        # regardless (its ctor_mutated falls back to empty) and
        # compilability falls to the real C++ signature -- mirror it in
        # lockstep. Analyzed user methods always carry a materialized cbp
        # (populate_const_borrow_params runs for every body-bearing fi),
        # so this arm cannot smuggle the mutated-ref miscompile shape
        # past the const gate.
        if (method_fi.direct_mutated_params is None
                and method_fi.call_edges is None):
            return _ctor_shape_ok(a, analyzer)
        return False
    if idx not in cbp:
        return False
    return (_ctor_shape_ok(a, analyzer)
            or _typed_dict_ctor_call(a, analyzer) is not None)

def _is_builtin_print(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a call to the builtin `print` (not a user/local shadow): the builtin
    is in `imported_names` and `print` is not redefined as a same-module function /
    record or bound as a local. A shadowed `print` conservatively stays on the AST
    path (never a divergence). This is stricter than the AST print path, which
    intercepts `print(...)` unconditionally regardless of a user shadow."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "print"):
        return False
    reg = analyzer.registry
    return ("print" in analyzer.imported_names
            and reg.get_function("print") is None
            and reg.get_record("print") is None
            and "print" not in declared)

def _print_arg_form(t: TpyType) -> PrintForm:
    """The `std::cout <<` wrapper for a print arg's resolved type -- mirrors the
    gen_print per-type dispatch for the eligible subset. bool is checked before
    the 8-bit-int case (a `bool` has an 8-bit int trait but must format as
    `True`/`False`, not `static_cast<int>`)."""
    if is_bool_type(t):
        return PrintForm.BOOL
    if is_float_type(t):
        # print_float takes double; a float32 arg casts up first
        # (gen_print's is_float32_type arm).
        return PrintForm.FLOAT32 if is_float32_type(t) else PrintForm.FLOAT
    # A bytes-slice value (incl. a still-pending bytes local binding -- the
    # view/owned resolution doesn't change the printer) wraps in BytesPrinter
    # (gen_print's is_any_bytes_type arm; bytearray is gated out of the args).
    if _is_bytes_family(t):
        return PrintForm.BYTES
    if is_enum_type(t):
        # @native enums have no emitted operator<< (it would conflict with a
        # user-provided one) -- gen_print routes them through `::tpy::__repr__`;
        # tpy-defined enums stream raw via their emitted operator<<.
        einfo = enum_info_of(t)
        if einfo is not None and einfo.is_native:
            _witness("enum.repr_print")
            return PrintForm.REPR
        return PrintForm.RAW
    tr = int_traits_of(t)
    if tr is not None and tr.bits == 8:
        return PrintForm.INT8
    return PrintForm.RAW

def _print_optval_opt(a: TpyExpr, analyzer,
                      locals_: dict[str, TpyType]) -> 'OptionalType | None':
    """`a` is a print arg whose RESOLVED type is a value-repr `Optional[scalar]`
    or `Optional[str]` -- an UN-narrowed read that gen_print renders via
    `::tpy::print_optional_val(...)` over the whole optional (bare, no deref).
    A NARROWED value-opt SCALAR or STR name takes the SAME whole-optional wrap
    (the AST's gen_print ignores narrowing in print position --
    `print_optional_val(s)`, not `(*s)`, for params and locals alike,
    probe-verified), keyed on the DECLARED binding type when `locals_` is
    threaded. Limited to a
    bare name (param / local) or a plain field read -- the positions gen_print
    lowers via `_gen_expr` (bare optional storage). A container/tuple inner takes
    an explicit Formatter (a separate face) and is excluded: `_value_opt_scalar`/
    `_value_opt_str` only admit scalar / str inners."""
    if isinstance(a, (TpyCall, TpyMethodCall)):
        # A value-repr Optional[scalar]/[str] CALL result (`print(pick(...))`
        # on a `-> str | None` / `int | None` callee): the rvalue is the whole
        # `std::optional<T>` fed bare to `print_optional_val` -- no narrowing on
        # an rvalue, so the resolved type is authoritative. Optional[bytes] is
        # excluded (BytesPrinter arm), matching the name/field rows below.
        t = analyzer.get_expr_type(a)
        opt = _value_opt_scalar(t, analyzer) or _value_opt_str(t, analyzer)
        if opt is not None:
            return opt
        # A value-RECORD inner (`after.utcoffset()` -> `timedelta | None`,
        # `std::optional<timedelta>` by value): the same member-blind
        # `print_optional_val` wrap over the bare call render.
        tb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
              if t is not None else None)
        if (isinstance(tb, OptionalType) and not tb.uses_pointer_repr()
                and _value_record_member(unwrap_readonly(tb.inner))):
            return tb
        return None
    if not isinstance(a, (TpyName, TpyFieldAccess)):
        return None
    if isinstance(a, TpyFieldAccess) and not _field_markers_clean(a):
        return None
    t = analyzer.get_expr_type(a)
    opt = _value_opt_scalar(t, analyzer)
    if opt is None:
        opt = _value_opt_str(t, analyzer)
    if opt is not None:
        return opt
    if isinstance(a, TpyName):
        # A storage-form Optional[Own[record]] binding (`c3 =
        # Container.wrap_optional(99)` -> `std::optional<Container<T>>`):
        # the same member-blind print_optional_val wrap over the bare name
        # (the record streams via its own operator<<). Keyed on the
        # RESOLVED type, so a narrowed read (member-typed) self-excludes.
        tb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
              if t is not None else None)
        if (isinstance(tb, OptionalType) and not tb.uses_pointer_repr()
                and _f1_record(_unwrap_own(unwrap_readonly(tb.inner)),
                               analyzer)):
            return tb
    if isinstance(a, TpyName) and a.name in locals_:
        # A NARROWED value-opt scalar OR str name keeps the whole-optional wrap
        # keyed on the DECLARED binding (gen_print ignores narrowing in print
        # position -- `print_optional_val(s)`, not `(*s)`), matching the field
        # arm below.
        return (_value_opt_scalar(locals_[a.name], analyzer)
                or _value_opt_str(locals_[a.name], analyzer))
    if isinstance(a, TpyFieldAccess) and isinstance(a.obj, TpyName):
        # A NARROWED Optional FIELD keeps the whole-optional wrap keyed on the
        # DECLARED field type (gen_print swaps in the declared Optional for
        # fields) -- EXCEPT a narrowed BigInt inner, whose runtime-bigint
        # branch fires first off the narrowed read (the `(*this->f)` RAW
        # render; regression case narrowed_field_print_bigint).
        fdt = _field_decl_type(a, locals_, analyzer)
        if fdt is not None:
            fdt = unwrap_readonly(fdt)
            opt = _value_opt_scalar(fdt, analyzer) or _value_opt_str(fdt, analyzer)
            if opt is not None and is_big_int_type(opt.inner):
                return None
            return opt
    return None

def _print_optval_form(opt: 'OptionalType') -> 'tuple[PrintForm, str | None]':
    """The `print_optional_val` wrapper for a value-repr Optional print arg,
    mirroring gen_print's value-repr Optional arm: `Optional[bool]` /
    `Optional[float]` take an explicit Formatter + inner-type template
    (`<::tpy::print_bool, T>` / `<::tpy::print_float, T>`, both float widths on
    the float branch), every other inner (int / Char / str) the plain form. The
    inner C++ spelling is the raw `opt.inner.to_cpp()` -- the bool/float inners
    are never a view family, so the AST's `_optional_print_inner_cpp` shim
    (view-storage override) collapses to `inner.to_cpp()` here."""
    inner = opt.inner
    if is_bool_type(inner):
        return PrintForm.OPT_VAL_BOOL, inner.to_cpp()
    if is_float_type(inner):
        return PrintForm.OPT_VAL_FLOAT, inner.to_cpp()
    return PrintForm.OPT_VAL, None

def _wrap_print_form(a: TpyExpr, declared: dict[str, TpyType],
                     analyzer) -> 'PrintForm | None':
    """The kind-keyed printer wrap for a container / value-tuple / F1-record
    NAME print arg, or None outside the slice -- gen_print's per-kind arms:
    `Dict/Set/ListPrinter` (Array shares ListPrinter), `TuplePrinter`, a
    record streaming raw via its emitted operator<<. The ONE routing fact
    shared by local admission and `_lower_print_arg`, so admission and form
    selection cannot drift. NAMES only; the gate excludes pointer-locals.
    Span / dict-view / varargs printers stay AST; `self` renders `(*this)`,
    not the bare name -- excluded.

    A value-tuple SUBSCRIPT read yielding a whole (possibly nested) value tuple
    (`print(t[N])` -> `TuplePrinter(std::get<N>(t))`) routes too; a scalar-element
    read yields a bare value that the scalar print arm handles.

    A container-returning CALL (`print(list(range(0, 10, 0)))`) takes the same
    kind-keyed wrap around the inline call render -- gen_print's gen_expr of
    the arg is position-blind, so value category doesn't change the emit. A
    dict-view result (`d.keys()`) is not a container type and falls out."""
    if isinstance(a, (TpyCall, TpyMethodCall)):
        rt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if isinstance(rt, OwnType):
            rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt.wrapped)))
        if is_dict(rt):
            return PrintForm.DICT
        if is_set(rt):
            return PrintForm.SET
        if is_list(rt) or is_array(rt):
            return PrintForm.LIST
        # A value-tuple call result (`print(n.as_integer_ratio())` ->
        # `TuplePrinter(::tpy::bigint_as_integer_ratio(n))`): the same
        # kind-keyed wrap around the inline render as the subscript arm's.
        if _value_tuple_nested(rt, analyzer) is not None:
            return PrintForm.TUPLE
        return None
    if isinstance(a, TpySubscript):
        if (_tuple_subscript_value_read(a, declared, analyzer) is not None
                and _value_tuple_nested(
                    analyzer.get_expr_type(a), analyzer) is not None):
            return PrintForm.TUPLE
        # A list/Array/Span slice read (`print(items[a:b:c])`) yields an owned
        # container streamed via ListPrinter -- the container-slice lowering
        # arm renders `list_slice`/`list_stepped_slice`.
        if a.slice_function_info is not None and isinstance(a.index, TpySlice):
            rt = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(analyzer.get_expr_type(a))))
            if is_list(rt) or is_array(rt) or is_span(rt):
                return PrintForm.LIST
        # A nested-container ELEMENT read (`print(groups["a"])` ->
        # `ListPrinter(::tpy::__getitem__(groups, "a"))`): the element lvalue
        # streams through the same kind-keyed wrap a container name does.
        if _container_ref_alias_elem_subscript(a, declared, analyzer):
            et = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(analyzer.get_expr_type(a))))
            if is_dict(et):
                return PrintForm.DICT
            if is_set(et):
                return PrintForm.SET
            if is_list(et) or is_array(et):
                return PrintForm.LIST
        return None
    if isinstance(a, TpyFieldAccess):
        # A container FIELD read (`m._items`) streams via the same kind-keyed
        # printer as a name (`ListPrinter(m._items)`) -- the field render is the
        # bare `recv.field` lvalue the wrap consumes. Markers-clean + a routed
        # F1-record receiver; value-tuple / record fields ride other arms.
        if not (_field_markers_clean(a)
                and (_field_receiver_ok(a, declared, analyzer)
                     # A field off a call / element result
                     # (`print(gp.get().log)`) reads the same bare `.field`
                     # lvalue the printer wraps -- the receiver rows that
                     # admit it as a METHOD receiver admit it here.
                     or _indirect_field_receiver_ok(a, declared, analyzer))):
            return None
        fdt = _field_decl_type(a, declared, analyzer)
        if (fdt is not None
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(fdt))), OptionalType)):
            # A NARROWED value-Optional field prints print_optional_val over
            # the WHOLE field on the AST path (the analyzed type is the bare
            # container, the declared type still Optional) -- not the
            # bare-deref kind-keyed printer. Stay AST.
            return None
        ft = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if is_dict(ft):
            return PrintForm.DICT
        if is_set(ft):
            return PrintForm.SET
        if is_list(ft) or is_array(ft):
            return PrintForm.LIST
        if isinstance(ft, TupleType):
            # A STORAGE-form tuple field (`print(h1.pair)`) streams via
            # `TuplePrinter(recv.field)` -- gen_print's TupleType arm over
            # the bare field lvalue.
            return PrintForm.TUPLE
        return None
    if isinstance(a, TpyNamedExpr):
        # A container WALRUS arg (`print((cols := [..]))`): the kind-keyed
        # wrap composes over the walrus render; the walrus arm validates
        # its own target class during lowering.
        wt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if is_dict(wt):
            return PrintForm.DICT
        if is_set(wt):
            return PrintForm.SET
        if is_list(wt) or is_array(wt):
            return PrintForm.LIST
        return None
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    ct = _subscript_container_recv_type(a, declared, analyzer)
    if ct is not None:
        ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ct)))
        if is_dict(ct):
            return PrintForm.DICT
        if is_set(ct):
            return PrintForm.SET
        if is_list(ct) or is_array(ct):
            return PrintForm.LIST
        # A subscriptable non-container binding (tuple/...) falls through.
    t = declared.get(a.name)
    if t is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, TypeParamRef):
        # An open type-param value streams via ValuePrinter, which dispatches
        # the formatting at runtime -- a `bool` T must print True/False, not
        # 1/0, which a raw `<<` would give (gen_print's TypeParamRef arm).
        return PrintForm.VALUE_GENERIC
    if is_varargs(u):
        # A whole `*args` body view is a tuple in Python, so it streams via
        # VarargsPrinter (gen_print's is_varargs arm over the bare lvalue) --
        # NOT a container printer.
        return PrintForm.VARARGS
    if is_bytearray_type(u):
        # A bytearray NAME streams via ByteArrayPrinter (gen_print's
        # bytearray arm over the bare lvalue).
        return PrintForm.BYTEARRAY
    if isinstance(u, TupleType):
        # Any tuple NAME (value or non-value) streams via TuplePrinter over the
        # deref'd lvalue -- mirrors gen_print's `isinstance(arg_type, TupleType)`
        # arm; `_lower_expr` derefs a pointer-repr tuple local like
        # `_gen_expr_deref`, so the render matches for both forms.
        return PrintForm.TUPLE
    if isinstance(u, NominalType) and _f1_record(u, analyzer):
        return PrintForm.RAW
    if _range_object_value(u):
        # A range() object streams raw via its own operator<< (no ListPrinter).
        return PrintForm.RAW
    if (isinstance(u, UnionType)
            and (_eligible_value_union(u) is not None
                 or _eligible_ptr_union_wide(u, analyzer) is not None
                 or _eligible_wrapper_union(u, analyzer) is not None)):
        # A union-typed NAME streams via the `::tpy::__str__` visitor
        # (gen_print's UnionType arm, union-kind-blind) -- the same STR
        # form a narrowed alias takes.
        return PrintForm.STR
    return None

def _print_kwarg_token(
        kv: TpyExpr, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> 'tuple[str, str | None] | None':
    """Classify a print sep=/end= kwarg source. ("literal", value-or-None)
    for a str literal (empty -> None: the token is skipped entirely,
    gen_print's chain_token short-circuit); ("name", None) for a resolved
    str/StrView NAME, which renders bare on both paths (gen_expr_deref hits
    none of its special arms for a plain str local/param). Anything else is
    unrouted; the caller lowers a "name" source immediately."""
    if isinstance(kv, TpyStrLiteral):
        return ("literal", kv.value or None)
    if (isinstance(kv, TpyName)
            and kv.name not in pointers
            and kv.name not in narrowed
            and _resolved_str_value(declared.get(kv.name), analyzer)
            is not None):
        return ("name", None)
    return None

def _print_tuple_record_elem(a: TpyExpr, locals_: dict[str, TpyType],
                             storage_tuple_locals: 'AbstractSet[str]',
                             analyzer) -> 'str | None':
    """`print(t[1])` where the element is an F1 record: the record streams
    through its emitted operator<<, and the only question is whether the
    element read hands back the value or a pointer to it. Returns "deref" or
    "bare", or None when the shape is outside the row.

    A BORROW-form tuple holds `T*` elements, and print is a VALUE position, so
    the referent streams (`(*std::get<1>(t))`); a STORAGE-form one holds the
    element by value and streams bare (`std::get<0>(p.points)`). The split is
    the same storage-vs-borrow evidence the arg rows use: a field read is
    always a storage source, a NAME is one only when it is registered as such.
    The subscript twin of `print.record_call`."""
    if not isinstance(a, TpySubscript) or isinstance(a.index, TpySlice):
        return None
    recv = a.obj
    if isinstance(recv, TpyName):
        rt = locals_.get(recv.name)
        storage = recv.name in storage_tuple_locals
    elif isinstance(recv, TpyFieldAccess) and _field_receiver_ok(
            recv, locals_, analyzer):
        rt = _field_decl_type(recv, locals_, analyzer)
        storage = True
    else:
        return None
    rb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    if not (isinstance(rb, TupleType) and rb.has_pointer_repr_element()):
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    if not _f1_record(at, analyzer):
        return None
    _witness("print.tuple_record_elem")
    return "bare" if storage else "deref"


def _print_arg_ok(a: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """One print arg in the no-kwargs common-arg subset: a str/bytes literal,
    an eligible scalar (fixed-int / bool / double), a Char (streamed raw --
    gen_print's direct-output arm; Char has no int_traits, so no int8 cast),
    or a str-slice value (a str/StrView name or str-returning call -- string
    and string_view stream raw, like the AST's is_any_str_type arm)."""
    if isinstance(a, (TpyStrLiteral, TpyBytesLiteral)):
        # A bytes literal prints owned (gen_print threads no target).
        return True
    if _print_optval_opt(a, analyzer, locals_) is not None:
        # An UN-narrowed value-repr Optional[scalar/str] read -> the bare
        # `::tpy::print_optional_val(...)` over the whole optional (witnessed at
        # lowering, where the wrapper render actually fires).
        return True
    if _value_opt_scalar_name(a, locals_, analyzer) is not None:
        # A NARROWED value-repr Optional[scalar] param read (declared Optional,
        # rt already the inner scalar) prints its deref-on-narrow `(*p)` -- a
        # separate face; the un-narrowed whole-optional read routed above.
        return note_detail("print.optval")
    if _value_opt_view_name(a, locals_, analyzer) is not None:
        # A value-repr Optional[bytes] read in print position stays deferred: it
        # is excluded from the str-only `_print_optval_opt` route above, so it
        # must not fall to the raw BytesPrinter arm below. (Both narrowed and
        # un-narrowed Optional[str] reads already routed via _print_optval_opt.)
        return note_detail("print.optstr")
    if (isinstance(a, TpyName)
            and _optional_ptr_borrow_name(a, locals_, analyzer) is not None
            and isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(a)))), OptionalType)):
        # An UN-narrowed pointer-repr Optional[F1-record] NAME prints the
        # whole pointer via `::tpy::print_optional(...)` (the record inner
        # streams through its own operator<<, so the CTAD form -- container
        # inners never reach here, `_optional_ptr_borrow` is record-only).
        # A NARROWED occurrence stays deferred (witnessed at lowering).
        return True
    at = analyzer.get_expr_type(a)
    # A raw `Any` value streams via `tpy::Any`'s operator<< (PrintForm.RAW) --
    # gen_print's per-type-str dispatch; the inner render must lower bare (a
    # name / routable read), which the RAW tail self-gates.
    if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))),
                  AnyType):
        return _witness("print.any")
    if (isinstance(a, TpyName)
            and _protocol_auto_slot(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(at))))):
        # A protocol-typed local/param (`print(result)` off an `auto`
        # select result): streams RAW via the concrete type's operator<<.
        return _witness("print.protocol_name")
    if isinstance(a, (TpyCall, TpyMethodCall)):
        # An F1-record-returning call rvalue streams RAW via the record's
        # emitted operator<< (`print(datetime.fromtimestamp(x))` --
        # gen_print's fall-through `<< x`); `_lower_print_arg` threads
        # BORROW_BIND so the call's record result is admitted.
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if isinstance(atu, OwnType):
            atu = unwrap_readonly(atu.wrapped)
        if (_f1_record(atu, analyzer)
                and is_rvalue_source(analyzer, a)):
            return _witness("print.record_call")
        if _protocol_auto_slot(atu):
            # A structural-protocol-result call (`print(iter(s))`): the
            # native render streams RAW via the concrete type's operator<<
            # (gen_print's fall-through `<< x`), like the record-call row.
            return _witness("print.protocol_call")
    if isinstance(a, (TpyBinOp, TpyUnaryOp)):
        # An F1-record-result user-dunder binop / unary rvalue streams RAW
        # like the record-call row (`print(td1 + td2)`, `print(-td)` -- the
        # template render into gen_print's fall-through `<< x`).
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if _f1_record(atu, analyzer):
            return _witness("print.record_binop")
    if isinstance(a, TpyFieldAccess):
        # An F1-record FIELD streams RAW via the record's operator<<
        # (`print(cv.origin)` -> `<< cv.origin`, the bare member read) --
        # the field sibling of the record-call row.
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if (_f1_record(atu, analyzer)
                and _field_receiver_ok(a, locals_, analyzer)):
            return _witness("print.record_field")
    return ((_resolved_scalar(at, analyzer) or _eligible_char(at)
             # A tpy-defined enum streams via its emitted operator<< (RAW);
             # an @native enum takes `::tpy::__repr__` (PrintForm.REPR).
             or _eligible_enum(at, analyzer) is not None
             or _resolved_str_value(at, analyzer) is not None
             or _resolved_bytes_value(at, analyzer) is not None  # BytesPrinter
             or _is_string_owned(at)))  # a concat result / String local: raw <<

# Sentinel for an f-string arg type outside the mirrored wrapper rows.
_FSTRING_INELIGIBLE = object()

def _fstring_arg_wrap(a: TpyExpr, analyzer, conv: int,
                      has_spec: bool) -> 'str | None | object':
    """The Python-compatible formatting wrapper for one interpolated f-string
    arg, as a positional `{0}` template (None = pass through bare) -- the
    mirrored subset of `_gen_fstring`'s per-arg table -- or `_FSTRING_INELIGIBLE`
    for any row the slice does not reproduce. The mirrored-type row is
    established first:
    `!r` then overrides it with `repr_of` (the AST chain's conv row precedes
    every type row, and no mirrored type is a container, so `repr_of` fires
    for all of them); `!s` is a no-op outside the user-type row, which is not
    mirrored -- so an unmirrored type stays rejected under any conversion (its
    inner render is not pinned by the slice). A format spec flips the bool row
    to `static_cast<int>` and the float rows to bare (std::format handles the
    spec on double/float directly); the 8-bit-int and enum casts apply
    spec-or-not. bool is checked before the 8-bit-int row, mirroring the AST
    order (bool carries 8-bit int traits but must format as True/False). An
    IntLiteral-typed arg (`f"{5}"`) resolves through the module default int --
    fixed widths format bare like the AST's fall-through; a runtime BigInt
    takes the `.to_string()` row (a spec'd int/BigInt arg is a sema error, so
    the spec never reaches that row)."""
    row: 'str | None | object' = _FSTRING_INELIGIBLE
    if isinstance(a, TpyStrLiteral):
        row = None  # const char[N] formats directly
    else:
        t = analyzer.get_expr_type(a)
        if t is None:
            return _FSTRING_INELIGIBLE
        ctmpl = container_to_str_template(t)
        if ctmpl is not None:
            # Containers (tuple/list/span/dict/set) render via the runtime
            # to_str helpers irrespective of conversion: the AST's
            # _container_to_str arm precedes the conv rows, so !r/!s never
            # override it (Python str/repr of a container coincide).
            _witness("fstr.container_arg")
            return ctmpl
        if (_resolved_str_value(t, analyzer) is not None
                or _is_string_owned(t)):
            row = None  # string/string_view/concat-result format directly
        elif is_bool_type(t):
            row = ("static_cast<int>({0})" if has_spec
                   else "::tpy::bool_to_str({0})")
        elif _eligible_char(t):
            _witness("fstr.char_arg")
            row = None  # char formats directly (no int_traits, so no cast)
        # A bare float literal (FloatLiteralType) resolves to float64 in an
        # f-string slot -- there is no Float32-typed context inside one -- so
        # it takes the same row as a concrete double. A concrete Float32 arg
        # casts up first (float_to_str takes double; _gen_fstring's float32
        # arm) -- unless a spec routes it bare into std::format.
        elif isinstance(t, FloatLiteralType) or is_float_type(t):
            if has_spec:
                row = None
            elif is_float32_type(t):
                row = "::tpy::float_to_str(static_cast<double>({0}))"
            else:
                row = "::tpy::float_to_str({0})"
        elif _eligible_enum(t, analyzer) is not None:
            row = "static_cast<int>({0})"
        elif isinstance(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(t))), AnyType):
            # A raw Any arg formats bare -- std::format has a `tpy::Any`
            # formatter (per-type str dispatch); !r overrides to repr_of below.
            _witness("fstr.any_arg")
            row = None
        elif (isinstance(t, NominalType) and t.is_user_record) \
                or isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(t))), TypeParamRef):
            # A user record / bound type param renders via __str__ (its ADL
            # override binds the per-record definition); a !r conversion
            # overrides it with repr_of below, mirroring the AST's conv row.
            # The type-param half unwraps readonly/Send shells -- a const
            # method's `self.value: T` read arrives readonly-wrapped.
            _witness("fstr.user_arg")
            row = "::tpy::__str__({0})"
        elif isinstance(t, UnionType):
            # std::variant is not std::formattable: route through the runtime
            # __str__ visitor that dispatches per alternative; !r overrides
            # to repr_of below.
            _witness("fstr.union_arg")
            row = "::tpy::__str__({0})"
        else:
            rt = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
            if is_fixed_int_type(rt):
                tr = int_traits_of(rt)
                row = ("static_cast<int>({0})"
                       if tr is not None and tr.bits == 8 else None)
            elif is_big_int_type(rt):
                # A runtime BigInt (concrete, or an IntLiteral under a BigInt
                # module default) formats via `.to_string()` (the bigint row).
                row = "({0}).to_string()"
    if row is _FSTRING_INELIGIBLE:
        return _FSTRING_INELIGIBLE
    if conv == FSTRING_CONV_REPR:
        _witness("fstr.conv_repr")
        return "::tpy::repr_of({0})"
    if conv == FSTRING_CONV_STR:
        _witness("fstr.conv_str")
    return row
