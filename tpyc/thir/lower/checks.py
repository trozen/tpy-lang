"""Per-arm checks and classifiers shared by expression and statement lowering.

These helpers run from the lowering arm that consumes their result. They do not
predictively traverse a body or expression before lowering.
"""

from __future__ import annotations
import math
from collections.abc import Sequence, Set as AbstractSet
from dataclasses import field, replace
from typing import Callable, NamedTuple
from ...parse.nodes import (
    lambda_of,
    is_property_getter_read,
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
    TpyFString,
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
    ConcreteFrameType, ConcreteGenType,
    collapse_tuple_own_elements,
    recorded_return_borrow_sources,
    return_const_projected,
    AnyType,
    BOOL,
    CallableType,
    ConcreteCoroType,
    FLOAT,
    FloatLiteralType,
    IntLiteralType,
    ListRepeatType,
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
    is_any_bytes_type,
    is_float_type,
    is_protocol_type,
    is_readonly_ptr,
    is_void_like_type,
    polymorphic_subclass_into_optional,
    resolve_int_literals,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    view_family_for_type,
)
from ...coercions import CoercionContext
from ...modules.type_resolution import is_native_iterable
from ...sema.literal_utils import fixed_int_literal_value_from_expr
from ...type_def_registry import (
    is_varargs,
    type_def_of,
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
    is_dict_view,
    is_float32_type,
    is_list,
    is_set,
    is_span,
    is_str_type,
    is_str_view_type,
    is_string_type,
)
from ...codegen_cpp import emit_prims
from ...codegen_cpp.functions import literal_mangled_name
from ...codegen_cpp.types import resolve_pending_container
from ...typesys import make_list
from ...codegen_cpp.forms import (LocalBinding, classify_local_binding,
                                  reads_storage_form_optional)
from ...codegen_cpp.protocols import (classify_dyn_own_arg, dyn_forward_ok,
                                      resolve_own_source_type)
from ...value_category import (
    call_returns_cpp_ref, is_rvalue_source, property_getter_of,
)
from ...codegen_cpp.context import (
    escape_cpp_name,
    is_lvalue_iterable,
    free_callee_cpp,
    imported_free_callee_cpp,
    module_qualified_callee_cpp,
    module_static_class_cpp,
    qualified_cpp_name,
    static_method_callee_cpp,
)
from ...compilation_context import get_current_compiler
from ... import qnames
from ..faces import witness as _witness
from ..reject import expr_kind_tag, note_detail
from .arg_table import (_ArgReq, _ArgRow, _ArgSink, arg_ok, register_sink)
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
    _enum_prop_wrap,
    _rvalue_ref_init,
    _select_node,
    _container_ternary_arg,
    _inst_slice_arg_ok,
    _protocol_union_arg,
    _nullable_static_protocol_param,
    _static_protocol_union_binding,
    _bare_module_recv,
    _module_var_access_pair,
    _module_var_recv,
    _module_var_read_cpp,
    _record_getitem_key,
    _record_getitem_idx_recv_ok,
    _subscript_index_and_tuple,
    _callable_value,
    _ptr_opt_borrow_call_ret,
    _f1_tuple,
    _union_elem_tuple,
    _mixed_own_storage_source,
    _nested_storage_tuple,
    _own_stripped_tuple_eq,
    _wrapper_ref_tuple_return,
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
    _container_value_optional_elem,
    _container_value_tuple_elem,
    _dict_view_iterable_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _flatten_binop_leaves,
    _or_chain_narrow_info,
    _eligible_wrapper_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_ptr_union_either,
    _eligible_ptr_union_wide,
    _eligible_value_union,
    _union_member_ctor_slot,
    _wrapper_member_ctor_slot,
    _wrapper_union_like,
    _enum_neg_wrap,
    _bytes_family_ref,
    _f1_container_ref,
    _binding_peel,
    _f1_record,
    record_like,
    _method_rvalue_record_like,
    _union_member_match,
    _native_iter_value_slot,
    _protocol_auto_slot,
    _type_param_value_slot,
    _field_decl_type,
    _unbound_self_field_decl_type,
    _field_markers_clean,
    _chained_subscript_recv_type,
    _field_over_subscript_ok,
    _field_over_record_getitem_ok,
    _record_getitem_borrow_subscript,
    _getitem_container_lvalue,
    _nested_container_elem_type,
    _borrow_tuple_param_elem_subscript,
    _tuple_field_opt_elem_subscript,
    _empty_instantiation_family,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    copy_call_arg,
    storage_tuple_name_source,
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
    _open_tparam_pass_arg,
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
    _view_inner_value_opt,
    _optional_ptr_arg_slot,
    _optional_ptr_borrow,
    _own_declared_call_ret,
    _own_storage_opt_param,
    _optional_ptr_borrow_name,
    _own_cascade_fires,
    _own_dyn_return,
    _own_lvalue_temp_slot,
    _owned_form_bytes_name,
    _owned_str_append_target,
    _owned_str_slot,
    _peel_coerce,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_or_opt_own_slot,
    _plain_own_slot,
    _own_proto_container_slot,
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
    _ru_wrapper_value_call_arg,
    _ru_wrapper_scalar_literal_arg,
    _opt_view_identity_coerce_arg,
    _own_viewfam_param,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _resolved_viewfam_value,
    _strview_coerce_name,
    _generic_root_subst,
    _is_range_call,
    _range_counter_type,
    _runtime_bigint,
    _narrow_key_type,
    _NARROW_UNMIRRORED,
    _scalar_pass_through_slot,
    _range_object_value,
    _slice_object_type,
    _owned_tuple_call_ret,
    _span_open_t_value,
    _span_value,
    _container_rebind_call_ret,
    _storage_call_ret,
    _storage_optional_return_type,
    _unwrap_own,
    _unown_type_args,
    _str_concat_operand,
    _subscript_container_recv_type,
    _narrowed_ptr_opt_recv,
    _tparam_value,
    _tuple_subscript_container_elem_read,
    _tuple_subscript_value_read,
    _type_family_tag,
    _union_binding_divergent,
    _unwrap_lit_coerce,
    _dict_key_shape_ok,
    _none_value_opt_arg,
    _str_literal_value_opt_arg,
    _bytes_literal_value_opt_arg,
    _open_t_tuple_slot,
    _value_opt_scalar_value_arg,
    _value_opt_callable_pass_arg,
    _value_opt_callable,
    _value_opt_view_whole_arg,
    _value_opt_member_arg,
    _whole_value_opt_field_arg,
    _value_opt_pass_through_arg,
    _value_opt_tuple,
    _value_opt_tuple_pass_arg,
    _tuple_literal_value_opt_arg,
    _value_opt_owned_str,
    _value_opt_scalar,
    _value_opt_value_record,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_string_owned,
    _value_opt_owned_view,
    _value_opt_view_name,
    _value_record_member,
    _tuple_container_elem_read,
    _open_value_tuple,
    _value_tuple,
    _value_tuple_owned_str_elem,
    _value_tuple_element_ok,
    _value_tuple_nested,
    _opt_ternary_tuple_arm_ok,
    _storage_opt_ternary_elem,
    _value_union_temp_slot,
    _var_decl_type,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _Prescan,
    SinkForm,
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
    if allow_field and is_property_getter_read(e):
        # A ptr-union PROPERTY read (`s = c.shape`): the getter returns
        # the STORAGE variant by reference (the is_property_getter
        # signature arm), so the decl lifts it exactly like a plain
        # field lvalue (`to_[const_]ptr_variant(c.shape())`). Bare-NAME
        # receiver only -- the decl arm's const verdict reads it.
        if not isinstance(e.obj, TpyName):
            return False
        pft = analyzer.get_expr_type(e)
        pft = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pft)))
               if pft is not None else None)
        return pft == u
    if allow_field and isinstance(e, TpyFieldAccess):
        # Strict receiver: the local-decl consumer spells the receiver's
        # const verdict (see _const_exact_field_receiver_ok); the field-write
        # consumer is const-blind but shares the arm -- conservative. A
        # SELF-rooted chain (`p = self.inner.pet`) is admitted too: its
        # const verdict is the method's readonly-ness, computed exactly at
        # the lowering arm.
        if not (_const_exact_field_receiver_ok(e, declared, analyzer)
                or (isinstance(e.obj, TpyFieldAccess)
                    and isinstance(e.obj.obj, TpyName)
                    and e.obj.obj.name == "self"
                    and _field_markers_clean(e)
                    and _field_markers_clean(e.obj))):
            return False
        ft = analyzer.get_expr_type(e)
        ft = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
              if ft is not None else None)
        return ft == u
    if (allow_field and isinstance(e, TpySubscript)
            and isinstance(e.obj, TpyName)
            and not isinstance(e.index, TpySlice)
            and not e.needs_optional_runtime_check):
        # A union container-ELEMENT read (`pet = pets["rex"]`): the
        # checked `__getitem__` yields the value-variant lvalue the
        # to_ptr_variant lift wraps; the subscript's own lowering
        # re-validates receiver/index shapes. Bare-NAME receiver only:
        # the decl arm's const verdict reads the receiver name, and a
        # chained receiver (`self.store["k"]`) takes its const from the
        # root of the chain instead; a call receiver is the dangling
        # rvalue-container borrow shape lowering declines to route.
        et = analyzer.get_expr_type(e)
        et = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
              if et is not None else None)
        return et == u
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

def _compound_isin_hits(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'list[tuple[str, UnionType, tuple[TpyType, ...], TpyExpr]] | None':
    """Flatten an `and` tree (`&&` TpyBinOp) and collect its
    isinstance-narrow leaves as `(var, union, members, leaf)` hits, in
    source order. None for non-`&&` conditions, an empty/partial list
    otherwise; folded and wrapper-union hits disqualify the whole
    condition (folded leaves have no membership render here; the inline
    THIRNarrowedRead render has no wrapper `.value` spelling)."""
    if not (isinstance(cond, TpyBinOp) and cond.op == "&&"):
        return None
    hits = []
    for leaf in _flatten_binop_leaves(cond, "&&"):
        inf = _isinstance_narrow_info(leaf, declared, analyzer)
        if inf is None:
            continue
        var, u, members, folded = inf
        if folded or u.needs_wrapper():
            return None
        hits.append((var, u, members, leaf))
    return hits



def _compound_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], TpyExpr] | None':
    """The U4 compound narrowing condition: an `and` tree (`&&` TpyBinOp --
    the parser folds `a and b` to that) with EXACTLY ONE isinstance-narrow
    leaf (un-folded), every other leaf an eligible bool condition. Leaves
    AFTER the isinstance see the subject retyped to the single concrete
    member (sema narrowed their reads; they render as the inline deref);
    leaves before it see the un-narrowed subject. `or` trees are not admitted;
    multiple isinstance leaves take `_multi_narrow_cond_info`. Returns
    `(var, union, members, isinstance_leaf)` or None."""
    hits = _compound_isin_hits(cond, declared, analyzer)
    if hits is None or len(hits) != 1:
        return None
    return hits[0]

def _multi_narrow_cond_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[tuple[str, UnionType, tuple[TpyType, ...], TpyExpr], ...] | None':
    """The multi-var sibling of `_compound_narrow_info`: an `&&` tree with
    TWO OR MORE isinstance-narrow leaves on DISTINCT un-narrowed subjects
    (`isinstance(a, A) and isinstance(b, B)`). Each leaf renders its holds
    test in place; each single-member fact installs for the leaves after
    it; the branch extracts one alias per subject. A repeated subject
    (re-narrowing inside one condition) is not admitted."""
    hits = _compound_isin_hits(cond, declared, analyzer)
    if hits is None or len(hits) < 2:
        return None
    vars_ = [h[0] for h in hits]
    if len(set(vars_)) != len(vars_):
        return None
    return tuple(hits)

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
) -> 'tuple[str, tuple[TpyType, ...], bool, TpyExpr | None] | None':
    """The D15 Any-isinstance if condition:
    `(var, check_members, negated, isin_leaf)`. `isin_leaf` is None for the
    simple / negated-simple form; a compound form is a single-op `&&` or
    `||` tree with EXACTLY ONE single-member Any-isinstance leaf on a
    LOCAL/PARAM subject -- the `&&` RHS reads the subject through the
    condition-scoped any_cast spelling (`lc.narrow.spelled`); a `||` RHS
    has no false-branch fact for Any (an open type has no complement), so
    its leaves lower un-narrowed. Negated compounds and multi-leaf trees
    are not admitted."""
    negated = isinstance(cond, TpyUnaryOp) and cond.op == "!"
    inner = cond.operand if negated else cond
    info = _any_narrow_info(inner, declared, analyzer)
    if info is not None:
        var, members = info
        return var, members, negated, None
    if not (isinstance(cond, TpyBinOp) and cond.op in ("&&", "||")):
        return None
    op = cond.op
    hits = [(leaf, _any_narrow_info(leaf, declared, analyzer))
            for leaf in _flatten_binop_leaves(cond, op)]
    hits = [(leaf, inf) for leaf, inf in hits if inf is not None]
    if len(hits) != 1:
        return None
    leaf, (var, members) = hits[0]
    # A GLOBAL Any subject spells its read through gcpp -- the compound
    # install spells the bare name; locals/params only.
    if len(members) != 1 or var not in declared:
        return None
    return var, members, False, leaf


def _assert_narrow_info(
        stmt: TpyAssert, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, TpyType] | None':
    """The U4 first-narrow assert: `assert isinstance(v, A)` on an
    un-narrowed routed-union subject with a concrete member fact. It emits
    the negated holds test plus a PERSISTENT extraction alias, so the
    narrowing holds for the rest of the enclosing scope. Returns
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
    (`own is None`), so the render is the bare expression. An `Own[enum]`
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
    `make_vector`/`make_ordered_*` owning switch is decided at lowering, off
    the move/nocopy/storage-call element verdicts. A `[0] * n`
    repeat (TpyListRepeat) is admitted too -- its own family gate plus the
    repeat arm in lowering. The empty-literal-to-Array
    reject is defensive-only: sema errors on both routes to that shape (a bare
    `[]` is un-inferable; an `Array[T, 0]` annotation mismatches the literal),
    so only the empty LIST form (the spelled `std::vector<T>{}` emit) is
    reachable."""
    # A reassigned container local is a POINTER-LOCAL (`a = b`
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

def _storage_decl_src(init: TpyExpr, analyzer) -> bool:
    """The decl's initializer is a fresh value the local must OWN, so the
    decl takes the storage-form init sink rather than the alias cascade.

    Two spellings of one rule -- the init MINTS its result instead of
    lending existing storage: a call or method call whose callee returns by
    value (a by-value `@property` read is one, so it lands here with its
    spelled twin); and a container SLICE read (`sub = items[a:b:c]`, an
    owned `list[T]` out of list_slice).

    A borrow of storage that dies with the statement is refused earlier, at
    the sink channel (`SinkForm.DYING_SOURCE_LEND`), so this rule does not
    re-derive it.
    """
    if isinstance(init, (TpyCall, TpyMethodCall)):
        return True
    if isinstance(init, TpySubscript):
        return (init.slice_function_info is not None
                and isinstance(init.index, TpySlice))
    return False

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
        # `[e] * n` -> materialized list or Array aggregate. A lazy
        # `ListRepeatType` / Span / protocol target is rejected by the lowering
        # arm, so gate to the two routed families here.
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
        return (record_like(analyzer.get_expr_type(e), analyzer)
                and is_rvalue_source(analyzer, e))
    if not isinstance(e, TpyCall):
        return False
    rfi = e.resolved_function_info
    if rfi is not None and rfi.is_constructor:
        return _ctor_shape_ok(e, analyzer) or _ctor_instantiation_ok(e, analyzer)
    return _record_rvalue_call_shape(e, analyzer)

def _storage_tuple_name_ok(e: TpyExpr, su: 'TpyType',
                           declared: dict[str, TpyType],
                           pointers: 'AbstractSet[str]', analyzer) -> bool:
    """A plain tuple NAME (bare or under `copy()`) declared as exactly the
    storage tuple slot `su` -- the containerlit.tuple_name_storage row."""
    nm = storage_tuple_name_source(e, analyzer)
    return (nm is not None and nm.name in declared
            and nm.name not in pointers
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared[nm.name]))) == su)


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
    (`note`, first-reject-wins) so the reject names the blocking
    element family."""
    if slot is None:
        return note_detail("container_lit.slot_family") if note else False
    fam = _container_lit_slot_family(slot, analyzer)
    if fam is None:  # value scalar / owned str (the original S5 slice)
        if (not threaded and _owned_str_slot(slot, analyzer)
                and not isinstance(e, TpyStrLiteral)):
            # An un-threaded str slot gets no elem target, so the S5
            # view->owned wrap cannot fire; only a literal, whose render is
            # form-neutral, stays admitted.
            return note_detail("container_lit.nested_view") if note else False
        return True
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if fam == "bytes":
        bt = _resolved_bytes_value(su, analyzer)
        if bt is None or not is_bytes_type(bt):
            return note_detail("container_lit.elem.bytes") if note else False
        if not threaded and not isinstance(e, TpyBytesLiteral):
            # Mirrors the un-threaded str rule: the `::tpy::Bytes` wrap
            # fires only at threaded positions (a bytes literal renders its
            # owned form target-free, so it stays admitted).
            return note_detail("container_lit.nested_view") if note else False
        return True
    if fam == "enum":
        if (_eligible_enum(analyzer.get_expr_type(e), analyzer) is not None):
            return True
        return note_detail("container_lit.elem.enum") if note else False
    if fam == "callable":
        # A `std::function<...>` element renders bare in the brace init
        # (`{make_adder(1), make_negator()}`): a callable-returning call
        # rvalue, a routable lambda / func ref, or a callable-value NAME
        # -- each source re-validates in its own lowering.
        if ((isinstance(e, (TpyCall, TpyMethodCall))
                and is_rvalue_source(analyzer, e)
                and _callable_value(analyzer.get_expr_type(e)))
                or _lambda_routable(e, analyzer)
                or _func_ref_routable(e, analyzer)
                or (isinstance(e, TpyName)
                    and _callable_value(analyzer.get_expr_type(e)))):
            return True
        return note_detail("container_lit.elem.callable") if note else False
    if fam == "optional":
        # Optional element slot. Compositional: a bare `None` renders
        # `std::nullopt`, and any other source routes iff the element
        # EXPRESSION routes -- the inner value's storage form (scalar bare,
        # owned-str view->owned wrap) is a pure function of the slot type that
        # `_lower_container_elem` threads through the Optional inner, landing
        # via the implicit `T -> std::optional<T>` conversion. `threaded`
        # gates the wrap-bearing str inner.
        if not (allow_optional and threaded and isinstance(su, OptionalType)):
            return note_detail("container_lit.elem.optional") if note else False
        if _storage_opt_ternary_elem(e, analyzer):
            return True
        if su.uses_pointer_repr():
            # A record-inner Optional element: the container STORAGE slot is
            # `std::optional<P>` (value), NOT the borrow-form `P*` that
            # uses_pointer_repr() describes -- so a record ctor lands via the
            # implicit `P -> std::optional<P>` and a bare None renders
            # std::nullopt, same as a value-inner Optional. Record NAMES /
            # container inners / force_pointer_repr value inners defer (a name
            # would need the pointer-local deref + move threaded through
            # the Optional inner).
            inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(su.inner)))
            # A CONTAINER-inner Optional element (`list[list[int] | None]`):
            # a nested ARRAY literal takes the union_prefix render
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
        if _storage_opt_ternary_elem(e, analyzer):
            return True
        # A VALUE-union element slot (`std::variant<...>`): literal elements
        # convert implicitly and render BARE (`{1, "two"}`
        # into `std::vector<std::variant<int32_t, std::string>>`).
        # Non-literal sources (names, calls -- the member-selection /
        # to_ptr_variant renders) are not admitted.
        if (_eligible_value_union(su) is not None
                and isinstance(e, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyBoolLiteral, TpyStrLiteral))):
            return True
        # A scalar / owned-str FIELD read at a non-wrapper union element
        # slot renders bare the same way the literal rows do (`{{"name",
        # p.name}, {"age", p.age}}` -- the asdict expansion's dict values;
        # the converting ctor picks the member; a MIXED union stores a
        # value variant at element positions too). The field read
        # re-validates in its own lowering.
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, TpyFieldAccess)
                and ((_clfe := analyzer.get_expr_type(e)) is not None)
                and (_resolved_scalar(_clfe, analyzer)
                     or _owned_str_slot(_clfe, analyzer)
                     # A CONTAINER field read matching a container MEMBER
                     # copies bare into the value variant the same way
                     # (`{"labels", ml.labels}` -- the converting ctor).
                     or ((_clfu := unwrap_readonly(unwrap_ref_type(
                         unwrap_send_sync(_clfe)))) is not None
                         and (is_list(_clfu) or is_dict(_clfu)
                              or is_set(_clfu))
                         and any(unwrap_readonly(m) == _clfu
                                 for m in su.members)))
                and _witness("containerlit.union_field_elem")):
            return True
        # A MIXED (non-value) union stores a value variant at element
        # positions too: a scalar/str literal still renders bare (the
        # converting ctor picks the member), and a nested ARRAY literal
        # with a UNIQUE list/array member takes the union_prefix
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
        # The DICT sibling: a nested dict literal at a union slot with a
        # UNIQUE dict member (`{"pos": {"x": p.x, ...}}` -- the asdict
        # nesting) lowers against the member; its self-describing
        # ordered_map render lands in the variant via the converting ctor.
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, TpyDictLiteral)
                and len([m for m in su.members
                         if is_dict(unwrap_readonly(m))]) == 1):
            return True
        # The COMP sibling: a list comprehension at a list-member slot
        # lowers against the member (the `({...})` stmt-expr renders
        # inline; the variant converts) -- the asdict list recursion. A
        # multi-list-member union disambiguates by the comp's OWN sema
        # type -- the comp resolves against it, member-blind.
        if (isinstance(su, UnionType) and not su.needs_wrapper()
                and isinstance(e, TpyListComprehension)):
            _clms = [m for m in su.members if is_list(unwrap_readonly(m))]
            if len(_clms) == 1:
                return True
            _clet = analyzer.get_expr_type(e)
            _cletu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                _clet))) if _clet is not None else None)
            if _cletu is not None and any(
                    unwrap_readonly(m) == _cletu for m in _clms):
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
                and (isinstance(e, (TpyIntLiteral, TpyFloatLiteral,
                                    TpyBoolLiteral, TpyStrLiteral))
                     # `-3`: the unary-minus literal fold renders the bare
                     # negated token like the raw literal.
                     or _folded_neg_int_literal(e, analyzer) is not None)):
            return True
        # `None` at a wrapper element slot is the monostate member
        # (`{std::monostate{}, true, 42}`), and a NESTED list/dict literal
        # of the same recursive family spells its typed container
        # (`std::vector<V>{1, std::monostate{}}`) -- both the ru-literal
        # element renders (`_lower_ru_elem`). Generic alias INSTANCES keep
        # rejecting (their None render diverges, see
        # `_ru_instance_literal_ok`).
        if (_eligible_wrapper_union(su, analyzer) is not None
                and (isinstance(e, TpyNoneLiteral)
                     or (isinstance(e, (TpyArrayLiteral, TpyDictLiteral))
                         and _ru_container_literal_ok(e, analyzer)))):
            return True
        # A SAME-WRAPPER NAME element (`[branch, leaf]` at `list[Tree]`):
        # the bare copy -- moved at a movable last use by the element
        # lowering's shared `_maybe_move` -- the name twin of the
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
            # ... and the member-CONTAINER-typed NAME (`[1, inner]` at
            # `list[Tree]`, inner: `list[Tree]` -- the wrapper's unique
            # list member): the converting ctor absorbs it; the make/move
            # switch rides the shared move facts, so a movable last use
            # renders `make_vector<Tree>(1, std::move(inner))`. The member
            # spells `list[AliasRef]` while the binding is the one-level
            # expansion `list[<wrapper union>]`, so equality goes through
            # the binding's ELEMENT (== the wrapper) + the unique-list-
            # member shape, like the non-wrapper nested-list rows. The
            # `==` is STRUCTURAL (wrapper_info's dedup convention): two
            # source aliases with identical recursive member shapes
            # deliberately collapse to one wrapper.
            if (isinstance(bu, NominalType) and is_list(bu)
                    and getattr(bu, "type_args", None)
                    and unwrap_readonly(bu.type_args[0]) == _wl_su
                    and len([m for m in _wl_su.members
                             if is_list(unwrap_readonly(m))]) == 1):
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
        # A tuple LITERAL element spells its own type (`std::tuple<
        # std::string, int32_t>{"a", 1}`), so like the str/bytes literal rows
        # above its render is form-neutral and survives an un-threaded parent
        # (`for a, b in [("a", 1)]:`). Other sources need the elem target.
        if not threaded and not isinstance(e, TpyTupleLiteral):
            return note_detail("container_lit.elem.tuple") if note else False
        vt = _value_tuple(su, analyzer)
        if (vt is None and isinstance(su, TupleType)
                and isinstance(e, TpyTupleLiteral)
                and all(_value_tuple_element_ok(_te, analyzer)
                        or _eligible_value_union(unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(_te))))
                        is not None
                        for _te in su.element_types)):
            # Value-union tuple elements (`list[tuple[str, int32 | str]]`):
            # the literal member lands bare in the variant slot of the
            # spelled tuple ctor (the render arm's tuple_union_elem row).
            return True
        if vt is not None:
            # A value-tuple NAME copies into the element slot (value type -- no
            # aliasing); `_container_elem_move_source` value-type-filters, so it
            # never moves -- a value-tuple copies. (An owned
            # `std::tuple<...>&&` param, which WOULD move, is
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
            # Storage-direct is correct only for scalar / owned-str members
            # (the None family) and F1-record members. Pointer-repr Optional /
            # union / nested-tuple members route through a borrow intermediate
            # (`tuple_value_to_borrow`, `&name`) this arm does not build --
            # they reject, along with any non-value lvalue NAME member.
            mfam = _container_lit_slot_family(mbare, analyzer)
            if mfam == "container":
                # A nested list LITERAL member renders its bare brace inside
                # the storage tuple (`S{1, {2, 3}}` -- the jagged-list shape),
                # and a dict-CONSTRUCTION call member (`dict({...})`, the
                # asdict tuple recursion) its spelled instantiation rvalue;
                # container NAMES / bare dict / set literals defer.
                if not ((isinstance(sub, TpyArrayLiteral)
                         and (is_list(mbare) or is_array(mbare)))
                        # A nested DICT literal member renders its
                        # self-describing spelled form inline in the
                        # storage tuple (`S{ordered_map<..>({{..}}), ..}`
                        # -- the asdict tuple recursion); set literals and
                        # container NAMES keep deferring.
                        or (isinstance(sub, TpyDictLiteral)
                            and is_dict(mbare))
                        or (isinstance(sub, TpyCall)
                            and is_dict(mbare)
                            and sub.call_type is not None
                            and isinstance(sub.call_type, TpyType)
                            and unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(sub.call_type))) == mbare
                            and len(sub.args) == 1 and not sub.kwargs
                            and isinstance(sub.args[0], TpyDictLiteral))):
                    return (note_detail("container_lit.elem.tuple")
                            if note else False)
            elif mfam == "tuple":
                # A nested-tuple LITERAL member renders its own storage form
                # inside the outer tuple (`S{"a", tuple_to_storage<S2>(
                # S2{"b", Node(1)})}` -- the wrap decision replays per level
                # via the tail recursion); a MIXED-own-tuple CALL member
                # takes the same per-member storage lift
                # (`S{tuple_to_storage<S2>(make_mixed(b)), 1}`); other
                # non-literal nested members (the borrow-intermediate
                # spellings) defer.
                if not (isinstance(sub, TpyTupleLiteral)
                        or (isinstance(mbare, TupleType)
                            and _mixed_own_storage_source(
                                sub, mbare, frozenset(), analyzer)
                            is not None)
                        # A same-typed plain tuple NAME member (`(1, t)`
                        # with `t = (2, c)` a borrow-tuple local): the
                        # whole non-move tuple_to_storage copy -- the
                        # containerlit.tuple_name_storage render.
                        or _storage_tuple_name_ok(sub, mbare, declared,
                                                  pointers, analyzer)):
                    return (note_detail("container_lit.elem.tuple")
                            if note else False)
            elif mfam not in (None, "record"):
                return note_detail("container_lit.elem.tuple") if note else False
            if (not mbare.is_value_type()
                    and not isinstance(sub, TpyTupleLiteral)
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
            # rvalues renders storage-direct).
            if all(_tuple_member_ok(i) for i in range(len(e.elements))):
                return True
        # A whole storage-tuple ELEMENT read (`[items[0]]` at
        # `list[tuple[int32, P]]`): the checked element lvalue is already
        # the storage value and copies bare -- `needs_tuple_storage_lift`
        # is False for a subscript source, so nothing wraps it.
        if (isinstance(e, TpySubscript) and not isinstance(e.index, TpySlice)
                and _f1_tuple(su, analyzer) is not None
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(e)))) == su):
            return True
        # A MIXED-own-tuple call / ternary-of-calls source (`[make_mixed(b)]`,
        # copy() peels): the owning slot materializes the borrowed half via
        # the NON-move `tuple_to_storage<S>(..)`. Call shapes only -- a mixed
        # NAME source has no witnessed elem render (frozenset keeps ternary
        # name-arms out too).
        if (isinstance(su, TupleType)
                and _mixed_own_storage_source(e, su, frozenset(),
                                              analyzer) is not None):
            return True
        # A same-typed plain tuple NAME (`(1, t)`'s nested member / a loop
        # var element): the whole non-move tuple_to_storage copy (bare when
        # the binding already reads storage) -- the
        # containerlit.tuple_name_storage render arm's gate half.
        if _storage_tuple_name_ok(e, su, declared, pointers, analyzer):
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
        # A dict-CONSTRUCTION call element (`dict({...})` -- the asdict
        # macro's top expansion) at the SAME-type dict slot: the spelled
        # instantiation render (`ordered_map<..>(<literal>)`, the
        # dict_literal_instantiation arm); the call's own lowering
        # re-validates the literal.
        if (allow_nested and isinstance(e, TpyCall)
                and is_dict(su)
                and e.call_type is not None
                and isinstance(e.call_type, TpyType)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    e.call_type))) == su
                and len(e.args) == 1 and not e.kwargs
                and isinstance(e.args[0], TpyDictLiteral)):
            return True
        # The COMP sibling of the nested-literal rows (the asdict
        # recursion: `{"vertices": [<expansion> for p in ..]}`): the
        # `({...})` stmt-expr renders inline, target-typed by the slot --
        # the render arm already dispatches it; the comp's own lowering
        # re-validates its pieces. List/dict comps only (the witnessed
        # kinds); a set comp keeps rejecting.
        if (allow_nested
                and ((is_list(su) and isinstance(e, TpyListComprehension))
                     or (is_dict(su)
                         and isinstance(e, TpyDictComprehension)))):
            return True
        # A CALL that already yields the slot's storage container
        # (`[make_row(i), make_row(j)]` at `list[list[float]]`): the owning
        # element slot takes the prvalue bare, the same verdict the
        # `Own[container]` argument slot reads for `xs.append(make_row(i))`.
        # Not gated on `allow_nested`: that flag exists for the nested
        # LITERAL rows, whose render needs the parent to thread the slot
        # target -- a call renders bare at any position.
        if _container_storage_call_rvalue(e, su, analyzer):
            return True
        if not (allow_nested and (is_list(su) or is_array(su))
                and isinstance(e, TpyArrayLiteral)):
            return note_detail("container_lit.elem.container") if note else False
        return True
    if fam == "record":
        if not (allow_record and record_like(su, analyzer)):
            return note_detail("container_lit.elem.record") if note else False
        if isinstance(e, TpyName):
            # A bare record name copies (brace-init), derefs for an F2
            # pointer-local, and moves at a movable local's last use -- all
            # decided at lowering off the move and pointer-local facts.
            bt = declared.get(e.name)
            if (bt is not None and record_like(bt, analyzer)):
                return True
            return note_detail("container_lit.elem.record") if note else False
        # `copy(<source>)` -- the copy-construct rvalue (`P(p)`): the elem
        # lowering's `_lower_copy_record` row (exact slot match) renders it;
        # a shape that slips past this admission (pointer-local source not in
        # `pointers`) returns None there and the generic call tail rejects --
        # a compile error, never a divergent render.
        if copy_construct_source(e, analyzer, pointers) is not None:
            return True
        # A module-VARIABLE record read (`[sys.stdout, sys.stderr]`): the
        # element slot holds the record by value, so the pointer slot's
        # `(*slot)` read copy-initializes it like any other record source.
        if (_module_var_access_pair(e, declared, analyzer) is not None
                and record_like(analyzer.get_expr_type(e), analyzer)):
            return True
        return (_record_source_call(e, analyzer)
                or (note_detail("container_lit.elem.record") if note else False))
    if fam == "any":
        # Any element slot: each element wraps into a `tpy::Any` cell via
        # make_any (`_lower_container_elem`'s Any arm). A nested container
        # element needs the brace-vs-paren prefix render (render-text
        # dependent), so it rejects; every scalar / str / bytes /
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
    # slots) -- such a slot resolves at render and the element lands bare,
    # so the base-scalar family is the right classification.
    if _resolved_scalar(t, analyzer) or _owned_str_slot(t, analyzer):
        return None
    # A bare `AliasRef` placeholder slot (the element type sema carries for
    # `list[V]` / `dict[str, V]` over a recursive alias) classifies as its
    # union body -- the wrapper rows of the union family re-key on
    # `_eligible_wrapper_union`, which performs the same resolution.
    u = _resolve_plain_alias(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))), analyzer)
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
    if _callable_value(u):
        return "callable"
    if isinstance(u, NominalType) and u.is_user_record:
        return "record"
    return "other"

def _note_container_lit_reject(init: TpyExpr, t: TpyType, analyzer) -> bool:
    """Record the family-level `container_lit.*` sub-classifier detail for a
    declaration reject (always returns False, like `note_detail`): the DECL/slot
    type is outside the routed container families -- an `Own[container]`
    binding, or a union/Optional/protocol target / literal-vs-family mismatch
    (`slot_family`). Per-SLOT rejects are tagged by `_container_lit_elem_ok`."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        return note_detail("container_lit.own")
    return note_detail("container_lit.slot_family")

def _container_record_elem_subscript(e: TpyExpr, locals_: dict[str, TpyType],
                                     analyzer,
                                     pointers: "AbstractSet[str]") -> bool:
    """A container subscript `c[i]` / `d[k]` whose element/value is a plain
    F1-record (`_container_record_elem`): `::tpy::__getitem__(c, k)` yields
    `T&` (or the bounds-safe operator[] lvalue) -- a borrow usable as a
    field-access receiver (`ps[i].x` / `ps[i].x = v`, `.` access -- never
    `->`) or a REF_ALIAS borrow-local source (`p = ps[i]` -> `P& p = ...`).
    The container analog of the tuple `_subscript_record_field_recv`.
    Receivers are the shared subscript set (`_subscript_container_recv_type`:
    an in-scope name or a one-level field off an admitted receiver);
    `Optional`-element containers reject at `_f1_record` (an Optional element
    is not a plain `T&` borrow)."""
    return _borrow_elem_subscript_shape(e, locals_, analyzer,
                                        _container_record_elem, pointers)


def _container_ref_alias_elem_subscript(e: TpyExpr,
                                        locals_: dict[str, TpyType],
                                        analyzer,
                                        pointers: "AbstractSet[str]") -> bool:
    """A container subscript whose element/value is itself a plain list/dict/set
    (`row = matrix[0]`): the element lvalue (`T&`) binds a REF_ALIAS local. The
    nested-container analog of `_container_record_elem_subscript`. A user
    `__getitem__` returning a container by reference is the same lvalue."""
    return (_borrow_elem_subscript_shape(e, locals_, analyzer,
                                         _container_ref_alias_elem, pointers)
            or _getitem_container_lvalue(e, locals_, analyzer, pointers))


def _container_wrapper_elem_subscript(e: TpyExpr,
                                      locals_: dict[str, TpyType],
                                      analyzer,
                                      pointers: "AbstractSet[str]") -> bool:
    """A container subscript whose element/value is a recursive-union WRAPPER
    (`v: JsonValue = d["rows"]`): the element lvalue binds the wrapper's `T&`
    REF_ALIAS. The wrapper-union analog of
    `_container_ref_alias_elem_subscript`."""
    return _borrow_elem_subscript_shape(
        e, locals_, analyzer,
        lambda t, a: _container_elem_family(
            t, a, lambda m: _eligible_wrapper_union(m, a) is not None),
        pointers)


def _borrow_elem_subscript_shape(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer, elem_family,
                                 pointers: "AbstractSet[str]") -> bool:
    """Shared shell for the record-element / nested-container-element subscript
    borrow sources: a plain `c[i]` / `d[k]` (no optional-check, slice, or
    slice-function) off a subscript-container receiver whose element satisfies
    `elem_family`, with a routable index.

    A None-NARROWED pointer-repr `Optional[container]` receiver (`d["k"]`
    under `if d is not None`) resolves to its inner container: the element
    read is the same `::tpy::__getitem__((*d), "k")` lvalue, so only the
    receiver's type resolution differs. `pointers` is the body's pointer
    BINDING set, which is what the deref render itself keys on; the
    un-narrowed flavor carries `needs_optional_runtime_check` and rejects
    above."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None or isinstance(e.index, TpySlice):
        return False
    recv_t = _chained_subscript_recv_type(e.obj, locals_, analyzer, pointers)
    if recv_t is None or not elem_family(recv_t, analyzer):
        return False
    return (_bigint_index_disposition(e.index, analyzer.get_expr_type(e.obj),
                                      analyzer, locals_) != "reject")

def _field_over_container_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                       analyzer,
                                       pointers: "AbstractSet[str]") -> bool:
    """A field access off a record-element CONTAINER subscript (`ps[i].field`):
    the receiver `ps[i]` is a plain-record borrow lvalue, so the access renders
    `::tpy::__getitem__(ps, i).field` (`.` -- `_field_is_arrow`'s
    subscript arm yields `->` only for borrow-`T*` tuple elements). Position-
    neutral like the tuple twin (`_field_over_subscript_ok`): a read (RHS) and
    a scalar-field write target (LHS) render off the same receiver. Markers-
    clean excludes the Optional null-check / property / setattr shapes."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _container_record_elem_subscript(e.obj, locals_, analyzer,
                                                 pointers))


def _tuple_container_elem_over_subscript_ok(e: TpyExpr,
                                            locals_: dict[str, TpyType],
                                            analyzer,
                                            pointers: "AbstractSet[str]"
                                            ) -> bool:
    """A tuple-element read over a container-element subscript, as a METHOD
    receiver (`xs[i][j].append(v)` over `list[tuple[.., list[T]]]`): the inner
    `xs[i]` is a container-element borrow lvalue whose element is a TUPLE, and
    the outer const-index read is `std::get<j>(::tpy::__getitem__(xs, i))` --
    a container element (`T&`, `.` access), routing the
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
                    TupleType)),
            pointers):
        return False
    if _subscript_index_and_tuple(e, analyzer) is None:
        return False
    rt = analyzer.get_expr_type(e)
    rt = resolve_pending_container(rt, analyzer) or rt
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    return bool(_f1_container_ref(rt)
                and _witness("method.recv.tuple_elem_subscript"))


def _subscript_over_container_subscript_ok(e: TpyExpr,
                                           locals_: dict[str, TpyType],
                                           analyzer,
                                           pointers: "AbstractSet[str]"
                                           ) -> bool:
    """A subscript whose receiver is itself a container-element subscript
    yielding a container (`m[i][j]`): `m[i]` is a nested-container borrow
    lvalue (`::tpy::__getitem__(m, i)`), indexed again -> the nested
    `::tpy::__getitem__(::tpy::__getitem__(m, i), j)`. The
    subscript-receiver twin of `_field_over_container_subscript_ok` (whose
    consumer is a field access): the receiver-shape resolver
    (`_subscript_container_recv_type`) deliberately stops at one level, so this
    admits the single nested step. Plain reads only
    -- optional-check / slice / slice-function receivers reject."""
    return (isinstance(e, TpySubscript)
            and not e.needs_optional_runtime_check
            and e.slice_function_info is None
            and not isinstance(e.index, TpySlice)
            and _container_ref_alias_elem_subscript(e.obj, locals_, analyzer,
                                                    pointers))

def _field_over_field_ok(e: TpyExpr, locals_: dict[str, TpyType],
                         analyzer) -> bool:
    """Shallow field-chain receiver shape for one lowering arm.

    The inner hop may itself be a `Ptr`-valued field read (`self.s.a.q` on
    `s: Ptr[S]`): it carries the auto-deref marker, so it is not
    marker-clean, but it renders through the pointer arm this gate's own
    receiver resolution already admits (`::tpy::deref_check(this->s).a`, or
    `this->s->a` where sema proved the pointer non-null) and the outer hop
    just spells `.q` off it."""
    if not (isinstance(e, TpyFieldAccess)
            and _field_markers_clean(e)
            and isinstance(e.obj, TpyFieldAccess)):
        return False
    ptr_inner = not _field_markers_clean(e.obj)
    if ptr_inner and not _ptr_value_field_recv_ok(e.obj, locals_, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(e.obj))))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    return bool(isinstance(ft, NominalType) and _f1_record(ft, analyzer)
                and _witness("field.chain_ptr_recv" if ptr_inner
                             else "field.chain_recv"))

def _field_over_call_ok(e: TpyExpr, analyzer, *,
                        any_record_ok: bool = False) -> bool:
    """A value field read off a record-returning call / method-call receiver
    (`f().x`, `p.Box(10).n`, `h.boxed.get().x`, and `s.Config.v` off a
    `@property` getter): the bare postfix member spells over the call
    render, rvalue and borrow returns alike. The receiver lowers through its
    own call arms (RECEIVER use), so every inner gate still applies.

    `any_record_ok` widens the receiver's record class from F1 to ANY user
    record, NATIVE included -- the member spelling resolves through
    `_field_cpp` either way. The WRITE positions pass it (a getter hop is
    their admitted receiver); the read positions keep the F1 class they
    always had, so the widening changes nothing they route."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, (TpyCall, TpyMethodCall))):
        return False
    if any_record_ok:
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(e.obj))))
        return bool(isinstance(rt, NominalType) and rt.is_user_record
                    and _witness("field.property_call_recv"))
    return bool(_f1_record(analyzer.get_expr_type(e.obj), analyzer)
                and _witness("field.call_recv"))

def _field_over_binop_ok(e: TpyExpr, analyzer) -> bool:
    """A value field read off an F1-record-result user-dunder binop
    receiver (`(a // b).v` -> `((a).__floordiv__(b)).v`): the postfix
    member chains off the parenthesized template render.
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
    / `set` / `bytearray`. The recursive-union-wrapper non-value family takes a
    different borrow shape, so a name alias of one is not admitted. `bytes` is
    a VALUE type and never reaches here.

    The `bytearray` span-borrow shape is a PARAM/arg-slot fact, not a decl one:
    the REF_ALIAS decl is family-blind and spells the alias through
    `type_to_cpp`."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    # Array included: a demoted list literal keeps list ALIAS semantics
    # (`ys = xs` -> `std::array<...>& ys = xs;`).
    return _f1_container_ref(t)


def _bytearray_alias_target(t: TpyType | None) -> bool:
    """The bytes-family half of `_alias_ref_container`, for the face witness:
    the alias binds `std::vector<uint8_t>&` rather than a container
    instantiation."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _bytes_family_ref(t)


def _bare_nonvalue_name_alias_ok(init: TpyExpr, target_type: TpyType | None,
                                 declared: dict[str, TpyType], prescan: _Prescan,
                                 pointers: 'AbstractSet[str]', analyzer) -> bool:
    """A single-assignment REF_ALIAS whose source is a plain non-value LVALUE
    NAME rendering bare (`T& name = src;` / `const T& ...`): `y = x` (record),
    `alias = items` (container), and alias chains (`c = b`). The source must be
    an in-scope local/param of a plain record / list / dict / set that renders
    bare -- NOT a pointer-local / Optional-ptr name (those alias as `(*p)`) and
    NOT a module global (rendered `T*`, aliased via `(*g)`); both reject.
    The const verdict follows the source name's own
    const-ness (see `_f1_is_const` / `_f1_const_rooted_source`)."""
    if not isinstance(init, TpyName):
        return False
    if init.name in pointers:
        # A PLAIN pointer-local source (a reassigned record local) aliases
        # through the deref the name lowering already renders (`Point&
        # alias = (*p);`). An Optional-declared pointer name whose deref
        # is PROVEN renders the same bare deref (`const Seconds&
        # __val_deadline = (*__opt_deadline);` -- the model macro's
        # null-tested branch); an UNPROVEN read still carries the
        # deref_check marker and stays out.
        dt = declared.get(init.name)
        dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
              if isinstance(dt, TpyType) else None)
        if isinstance(dt, OptionalType):
            if getattr(init, "needs_optional_runtime_check", False):
                note_detail("decl.name_alias_ptr_src")
                return False
            return bool(record_like(target_type, analyzer)
                        and _witness("decl.alias_opt_ptr_deref_src"))
        return bool(record_like(target_type, analyzer)
                    and _witness("decl.alias_ptr_deref_src"))
    if init.name not in declared and init.name not in prescan.param_names:
        note_detail("decl.name_alias_global_src")
        return False
    return record_like(target_type, analyzer)


def _borrow_dunder_source(init: TpyExpr, analyzer) -> bool:
    """An operator expression resolved to a BORROW-returning record dunder
    (`a + b` off `__add__(self, o) -> Acc`, `-a` off `__neg__`), i.e. one whose
    C++ friend shim returns `[const] T&` and therefore hands out an alias of an
    operand. This is the operator flavor of the borrow-returning call source:
    `is_rvalue_source` already follows the dunder's return convention, so the
    classifier hands back REF_ALIAS -- the gate only has to say the operand
    render is one the borrow-decl arm can bind. An `Own[T]`-returning dunder is
    a fresh value and never reaches here (rvalue -> OTHER/REBIND_SLOT)."""
    if isinstance(init, TpyBinOp) and init.resolved_binop is not None:
        return call_returns_cpp_ref(analyzer, init.resolved_binop.method)
    if isinstance(init, TpyUnaryOp) and init.resolved_unaryop is not None:
        return call_returns_cpp_ref(analyzer, init.resolved_unaryop.method)
    return False


def check_polymorphic_rvalue_opt_rebind(
        name: str, target_type: 'TpyType | None', init: 'TpyExpr | None',
        rvalue_reassigned: 'AbstractSet[str]', analyzer) -> None:
    """Diagnose an rvalue of a polymorphic subclass initializing a local
    `Optional[Base]` slot that a later rvalue rebind reseats.

    The verdict must be reached before any admission decision on the same
    decl: a slot the rebind shares is typed once, so lowering it would slice
    the dynamic type instead of telling the user the shape is unavailable.
    """
    if init is None or name not in rvalue_reassigned:
        return
    if not is_rvalue_source(analyzer, init):
        return
    init_type = analyzer.get_expr_type(init)
    sub = polymorphic_subclass_into_optional(
        target_type,
        unwrap_readonly(init_type) if init_type is not None else None,
        analyzer.registry)
    if sub is not None:
        emit_prims.reject_polymorphic_rvalue_into_optional_local(
            name, target_type, sub, init.loc)


def _open_param_call_result(init: TpyExpr, target_type: 'TpyType | None',
                            analyzer) -> bool:
    """Whether a call init yields the SAME bare type param the slot is spelled
    with, seen through the wrappers a call result carries (`copy(x)` hands back
    `Ref[U]`, an `Own[U]`-returning callee an `OwnType`). Same-param equality is
    what makes the slot's per-instantiation spelling correct: a result of some
    other open param or of a resolved type would need its own conversion at the
    binding site."""
    it = analyzer.get_expr_type(init)
    if it is None:
        return False
    it = _unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it))))
    return isinstance(it, TypeParamRef) and it == target_type


def _opt_ptr_addr_of_record_source(init, declared: dict[str, TpyType],
                                   pointers: 'AbstractSet[str]',
                                   prescan, analyzer, *,
                                   target_type: 'TpyType | None' = None
                                   ) -> bool:
    """The SOURCE half of the address-of lift into a ptr-repr
    `Optional[record]` slot (`p: Pet | None = d`): an in-scope, non-pointer,
    non-global-slot NAME whose declared type is an F1 record.

    Both the classifier and the lowering ask this one predicate, so the row
    cannot be admitted by one and rendered by the other -- the drift would
    reach the bare pointer-copy leg and emit `Pet* p = d;` off a record
    value. The classifier additionally passes `target_type`, which adds the
    subclass-or-equal relation against the slot's inner (C++ binds the
    derived address to the base pointer implicitly); the lowering has
    already had that proved for it."""
    if not isinstance(init, TpyName):
        return False
    if init.name in pointers or init.name in prescan.global_slots:
        return False
    if not (init.name in declared or init.name in prescan.param_names):
        return False
    st = declared.get(init.name)
    st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
          if st is not None else None)
    if st is None or not _f1_record(st, analyzer):
        return False
    if target_type is None:
        return True
    tt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target_type)))
    return bool(isinstance(tt, OptionalType) and tt.uses_pointer_repr()
                and _f1_record(unwrap_readonly(tt.inner), analyzer)
                and analyzer.registry.is_subclass_of_or_equal(
                    st, unwrap_readonly(tt.inner)))


def _const_borrow_call_result(call: TpyMethodCall, analyzer) -> bool:
    """Whether a borrow-returning method call hands back a CONST reference --
    either sema typed the result `readonly[...]`, or the method is readonly
    (declared or inferred) and the emitted shim const-projects its return.

    The same pair of facts `_expr_is_const_source` reads off a DIRECT
    method-call init; spelled here because a consumer reached through a FIELD
    hop has no `_LowerCtx` to ask that derivation with."""
    if isinstance(analyzer.get_expr_type(call), ReadonlyType):
        return True
    fi = call.resolved_function_info
    return fi is not None and return_const_projected(fi)


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
        # classifies OTHER via the classifier's tuple carve-out (tuple-element
        # reads are lifted at the consumer), but the decl IS
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
        # needs the optional_to_ptr lift, not the bare copy. A REASSIGNED
        # alias binds the same bare copy: a direct pointer copy needs no
        # rebind-slot predecl, so
        # no slot exists for either flavor; the reseats ride the slotless
        # pointer arms, which gate their own source shapes.
        if (isinstance(stmt.init, TpyName)
                and stmt.init.name in pointers):
            src_t = declared.get(stmt.init.name)
            tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                target_type))) if target_type is not None else None)
            st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(src_t)))
                  if src_t is not None else None)
            if (isinstance(tt, OptionalType) and tt.uses_pointer_repr()
                    and st == tt
                    and record_like(_unwrap_own(unwrap_readonly(tt.inner)),
                                   analyzer)):
                return LocalBinding.OPTIONAL_TO_PTR
        # A plain record lvalue NAME into a ptr-repr `Optional[record]` slot
        # (`p: Pet | None = d` on a `Dog` binding) classifies OTHER too, but
        # the decl is the address-of lift (`Pet* p = &(d);`) -- a pointer
        # conversion, no representation change.
        if (stmt.name not in prescan.reassigned
                and stmt.name not in prescan.hoisted
                and stmt.name not in prescan.move_through
                and _opt_ptr_addr_of_record_source(
                    stmt.init, declared, pointers, prescan, analyzer,
                    target_type=target_type)):
            return LocalBinding.OPTIONAL_TO_PTR
        return None
    if binding is LocalBinding.OPT_PTR_SLOT:
        # The slot-hoist Optional pointer-local: init/reseat sub-shapes are
        # gated by `_lower_opt_ptr_slot_decl` (the lowering consumes the
        # verdict directly, per the classifier-consumed-by-lowering rule).
        return binding
    if binding is LocalBinding.REBIND_SLOT:
        # A bare TYPE-kind type-param slot rebound by an rvalue CALL
        # (`acc: U = copy(initial)` reseated by `acc = func(acc, x)`): the
        # same rebind-slot pointer-local an F1 record's REBIND_SLOT takes. `U` has
        # no borrow/storage split of its own -- the C++ template traits fix
        # the shape at instantiation -- so the pointer binding renders
        # per-instantiation (`U __slot_1 = <init>; U* acc = &__slot_1;`).
        # FREE-call sources only, and only ones whose result IS that same
        # open param: the call lowering validates callee and args, while a
        # literal / ctor / dunder rvalue and a differently-typed result each
        # spell a render this row does not answer. The method-call sibling
        # is held out for want of a witness, not because its render differs
        # -- the rvalue legs below keep the two source shapes apart
        # for the same reason.
        if (_type_param_value_slot(target_type)
                and isinstance(stmt.init, TpyCall)
                and _open_param_call_result(stmt.init, target_type, analyzer)):
            return binding
        # F2d: the source is an rvalue record / container ctor or by-value call (not a field
        # read), so it bypasses the field-receiver check the lvalue bindings need.
        if (record_like(target_type, analyzer)
                and _record_rvalue_source_shape(stmt.init, analyzer)):
            return binding
        if _frame_factory_source(stmt.init, target_type, analyzer):
            return binding
        # ... or an rvalue F1-record METHOD call (`cur = a.clone()` -> the
        # same rebind-slot pointer-local, `Rc<Node>* cur = &__slot_1;`): the
        # method-call lowering's own gates validate callee/args, the shared
        # `_owned_record_decl_ok` disjunct.
        if (record_like(target_type, analyzer)
                and _method_rvalue_record_like(stmt.init, analyzer)):
            return binding
        # ... or a fresh operator result / all-fresh select of either family
        # (`z = C(1) if c else make()`), its `?:` a prvalue into the slot.
        if _rvalue_ref_init(stmt.init, target_type, analyzer):
            return binding
        # A rebound container-literal local rides the same pointer-local
        # (`std::vector<T>* xs = &__slot_1; ... (*xs) = {...};`, or an own
        # slot where sema's storage verdict says so);
        # the literal itself lowers through the shared container-literal arm
        # (its per-element gates reject there).
        if (isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral))
                and target_type is not None
                and _container_literal_shape_ok(stmt.init, target_type,
                                                analyzer, note=True)):
            return binding
        # ... and the container-returning CALL beside it: the owning result
        # fills the rebind slot exactly as the literal does, and the call's
        # own gates validate callee and args.
        if (isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and _container_rebind_call_ret(
                    stmt.init, analyzer.get_expr_type(stmt.init), analyzer)):
            return binding
        # ... and a list/set/dict COMPREHENSION at its own container slot:
        # the same owning fill (`std::vector<T> __slot_1 = ({...});`), the
        # comp's route gating its element shapes.
        if _container_comp_arg(stmt.init, target_type):
            return binding
        return None
    if (isinstance(stmt.init, (TpyCall, TpyMethodCall))
            and not is_property_getter_read(stmt.init)):
        # A @property read is a call too, but its return CONVENTION is the
        # field's, not a method's: the storage-ref shapes come back by
        # reference and take the lift rows further down. It is held out here
        # rather than re-answered, so the two conventions stay one decision.
        #
        # A borrow-returning call (`p = shared(x)` -> `Pair& p = shared(x);`,
        # `std::vector<T>& items = c.get_item();` -- the classifier's
        # lvalue-source verdict). Const rides `_f1_is_const`'s raw-sema
        # check (a `readonly[T]` return arrives ReadonlyType-wrapped) plus
        # the readonly-method ref-return branch: a readonly callee returning
        # a C++ reference binds const. Other call bindings (an
        # OPTIONAL_TO_PTR optional return) fall through untagged so the
        # generic probe's family drilldown names them.
        if binding is LocalBinding.REF_ALIAS and (
                record_like(target_type, analyzer)
                # A borrow-returning genrec method call binds the same alias
                # (`g = h.get()` -> `Tree<int32_t>& g = h.get();`) -- the
                # wrapper struct is one C++ value type, the reference binds
                # like any record's.
                or isinstance(unwrap_readonly(unwrap_send_sync(target_type)),
                              RecursiveAliasInstanceType)):
            return binding
        if binding is LocalBinding.POINTER and record_like(target_type,
                                                           analyzer):
            # The reassigned flavor reseats via `&(call)`: admitted for a
            # borrow-returning call (`Point* first = &(get_first(data));`
            # -- the PTR_ADDR emit over the bare borrow-call render).
            fi = getattr(stmt.init, "resolved_function_info", None)
            if fi is not None and call_returns_cpp_ref(analyzer, fi):
                return binding
            note_detail("decl.record_call_reassigned")
        return None
    # An Optional-ptr borrow-name receiver (`g = h.g` off a narrowed
    # `H | None` param / OPTIONAL_TO_PTR local) is a plain field lift off
    # the pointer (`h->g`); its const verdict is computable exactly --
    # seed_param_locals seeds const_indirect_locals from the readonly
    # annotation or the DEEP-const verdict, both computed in
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
                                                analyzer, pointers)):
            return binding
        # An OPTIONAL_TO_PTR lift whose source is a @property read
        # (`n = w.node` -> `Node* n = ::tpy::optional_to_ptr(w.node());`):
        # the getter hands back the field's storage BY REFERENCE (the
        # property return convention), so the lift points into the record --
        # the field arm delegates to the method-call lowering, whose own
        # gates re-validate receiver/args.
        if (binding is LocalBinding.OPTIONAL_TO_PTR
                and is_property_getter_read(stmt.init)):
            return binding
        # A REF_ALIAS @property read (`v = c.items` ->
        # `std::vector<int32_t>& v = c.items();`): the borrow-returning
        # getter call IS the aliased lvalue -- the property twin of the
        # borrow-call REF_ALIAS row (the getter call's own gates
        # re-validate receiver/args; const rides the raw-sema readonly
        # read). REF_ALIAS only: the reassigned POINTER sibling needs the
        # `&(c.items())` reseat lift, unwitnessed. An OWN-returning getter
        # never reaches here: its read is an rvalue, so the binding is not
        # REF_ALIAS at all (binding `T&` to the by-value getter result off
        # a mutable receiver would be ill-formed C++).
        if (binding is LocalBinding.REF_ALIAS
                and is_property_getter_read(stmt.init)
                and record_like(target_type, analyzer)):
            return binding
        # A REF_ALIAS field off an admitted METHOD-CALL receiver
        # (`j = h.peek().jar` -> `Jar& j = h.peek().jar;`): the field
        # renders `.field` off the bare inner call exactly as the
        # OPTIONAL_TO_PTR twin above renders it under its lift, and the
        # receiver call hands back a C++ lvalue, so the alias names storage
        # the receiver owns rather than a member of a dying temporary. A
        # by-VALUE receiver (`-> Own[T]`, a protocol or generic return)
        # answers False at `call_returns_cpp_ref` and keeps rejecting --
        # that is a lifetime question, not a render one. A CONST-returning
        # receiver call keeps rejecting for a second reason: the decl's
        # const verdict does not travel through a field hop off a call
        # (`_f1_const_rooted_source` stops at the call node), so the alias
        # would be spelled `T&` over `const T` -- the defect the
        # OPTIONAL_TO_PTR twin above already has, filed as
        # BUGS.md#const-borrow-call-field-lift-loses-const.
        # REF_ALIAS only: the reassigned POINTER sibling
        # needs the `&(h.peek().jar)` reseat lift, unwitnessed.
        if (binding is LocalBinding.REF_ALIAS
                and isinstance(stmt.init, TpyFieldAccess)
                and isinstance(stmt.init.obj, TpyMethodCall)
                and call_returns_cpp_ref(
                    analyzer, stmt.init.obj.resolved_function_info)
                and not _const_borrow_call_result(stmt.init.obj, analyzer)
                and _indirect_field_receiver_ok(stmt.init, declared,
                                                analyzer, pointers)
                and record_like(target_type, analyzer)):
            return binding
        # An OPTIONAL_TO_PTR lift whose field source hangs off a CONTAINER-
        # ELEMENT subscript (`box = self.slots[i].box` -> `Box<AnyTask>* box
        # = ::tpy::optional_to_ptr(::tpy::__getitem__(this->slots, i).box);`):
        # `slots[i]` is a plain record borrow lvalue, so the field renders
        # `.` off the bare `__getitem__` -- the subscript-
        # receiver twin of the method-call receiver row above, sharing the
        # value-position field-read arm's own receiver predicate.
        if (binding is LocalBinding.OPTIONAL_TO_PTR
                and _field_over_container_subscript_ok(stmt.init, declared,
                                                       analyzer, pointers)):
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
        # verdict follows element-borrow propagation (`_f1_is_const`).
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and record_like(target_type, analyzer)
                and _container_record_elem_subscript(stmt.init, declared,
                                                     analyzer, pointers)):
            return binding
        # A borrow-returning user-record `__getitem__` subscript (`r = e[k]`)
        # binds the single-assignment `T&` alias of the bare operator[]
        # lvalue or, reassigned, the reseatable `T* r = &(e[k]);` -- the
        # record-element rung's two flavors. A container result takes the
        # nested-container rung below.
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and record_like(target_type, analyzer)
                and _record_getitem_borrow_subscript(stmt.init, declared,
                                                     analyzer, pointers)):
            return binding
        # A pointer-repr record element off a borrow-form tuple PARAM
        # (`b = p[1]`) binds the `T&` alias of the element referent (the
        # deref-flagged std::get render). REF_ALIAS only -- a reassigned
        # sibling's reseat is unwitnessed.
        if (binding is LocalBinding.REF_ALIAS
                and record_like(target_type, analyzer)
                and _borrow_tuple_param_elem_subscript(stmt.init, prescan,
                                                       analyzer)):
            return binding
        # A nested-container element subscript (`row = matrix[0]`) binds the
        # `T&` alias of the element list/dict/set, or -- reassigned -- the
        # reseatable `T* row = &(::tpy::__getitem__(matrix, 0));`, the same
        # PTR_ADDR lift the record-element rung above takes.
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and _alias_ref_container(target_type)
                and _container_ref_alias_elem_subscript(stmt.init, declared,
                                                        analyzer, pointers)):
            return binding
        # An open-T element subscript inside a generic body (`v =
        # self.items[self.pos]`) binds the per-instantiation `T&`, or the
        # reseatable `T*` when reassigned (`result = a[i]`) -- the type-param
        # twin of the record-element rows above.
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and _is_type_param_slot(target_type)
                and _borrow_elem_subscript_shape(stmt.init, declared, analyzer,
                                                 _container_tparam_elem,
                                                 pointers)):
            return binding
        # A wrapper-union element subscript (`v: JsonValue = d["rows"]`)
        # binds the element lvalue as the wrapper's `T&` alias. LOCAL
        # narrowing-alias receivers only: a PARAM subject's extraction alias
        # is `const auto&`, so a non-const `T&` bind off it would be
        # uncompilable C++ (see BUGS.md); the shape stays gate-rejected.
        if (binding is LocalBinding.REF_ALIAS
                and _eligible_wrapper_union(target_type, analyzer) is not None
                and isinstance(stmt.init, TpySubscript)
                and isinstance(stmt.init.obj, TpyName)
                and stmt.init.obj.name not in prescan.param_names
                and _container_wrapper_elem_subscript(stmt.init, declared,
                                                      analyzer, pointers)):
            return binding
        # A bare non-value NAME alias (`y = x`, `alias = items`, `c = b`) binds
        # the single-assignment `T&` alias directly -- no field-receiver pin.
        if (binding is LocalBinding.REF_ALIAS
                and _bare_nonvalue_name_alias_ok(stmt.init, target_type,
                                                 declared, prescan, pointers,
                                                 analyzer)):
            if _bytearray_alias_target(target_type):
                _witness("decl.bytearray_alias")
            return binding
        # A container / F1-record and/or select or ternary (`x = a or b`,
        # `x = a if c else b`): the classifier's lvalue verdict means the
        # select is itself an lvalue (an rvalue operand rides a lazily
        # emplaced `THIRSlotEmplace`, still an lvalue), so
        # it binds as the single-assignment `T&` alias. Operand shapes
        # gate inside the select / ternary lowering.
        #
        # A borrow-returning operator dunder (`c = a + b`, `c = -a`) joins the
        # same row: its friend shim returns `[const] Acc&`, so the result IS an
        # operand's lvalue and the alias binds the operator render directly
        # (`const Acc& c = ((a) + (b));`). The `[const]` comes from
        # `_f1_is_const`'s dunder arms: a readonly dunder's borrow
        # return binds const.
        if (binding is LocalBinding.REF_ALIAS
                and record_like(target_type, analyzer)
                and ((isinstance(stmt.init, TpyBinOp)
                      and stmt.init.op in ("&&", "||"))
                     or isinstance(stmt.init, TpyIfExpr)
                     or _borrow_dunder_source(stmt.init, analyzer))):
            return binding
        # The reassigned POINTER sibling of the bare-name alias: a plain
        # record or container NAME source lifts to a reseatable
        # `[const] T* x = &(a);` (later `x = &(b);`) -- the same pointee
        # families the single-assignment `T&` alias above binds, since the
        # `&()` lift only needs a bare-rendering lvalue. The source must not
        # itself be a pointer-local / global (aliased `(*p)`).
        if (binding is LocalBinding.POINTER
                and isinstance(stmt.init, TpyName)
                and stmt.init.name not in pointers
                and (stmt.init.name in declared
                     or stmt.init.name in prescan.param_names)
                and record_like(target_type, analyzer)):
            return binding
        # `p2: Point = ptr` off a `Ptr[Point]` binding -- the deref
        # auto-coercion's INLINE flavor as a borrow-local SOURCE. The
        # deref_check lvalue binds the single-assignment alias
        # (`Point& p2 = ::tpy::deref_check(ptr);`) or, reassigned, the
        # reseatable `Point* copy = &(::tpy::deref_check(ptr));`; both are the
        # same rows the container-element subscript source takes. Shares
        # `_deref_coerce_arg` with the ARG position so the key cannot drift;
        # the record-wrapper `__deref__()` flavor has no borrow-slot render
        # (it hoists a VALUE copy) and stays rejected.
        if (binding in (LocalBinding.REF_ALIAS, LocalBinding.POINTER)
                and _deref_coerce_borrow_slot(stmt.init, target_type,
                                              declared, analyzer)):
            return binding
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        # The FIELD-source aliases: a record field (`r = self.inner` ->
        # `Inner& r = this->inner;`) and a container field (`xs = self.tags`
        # -> `std::vector<T>& xs = this->tags;`), each in the REF_ALIAS and
        # the reseatable POINTER flavor (`&(this->tags)`).
        if record_like(target_type, analyzer):
            if _bytearray_alias_target(target_type):
                _witness("decl.bytearray_alias")
            return binding
        return None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner object,
    # record or container alike (`T* x = ::tpy::optional_to_ptr(<storage
    # opt>)`, the pointee spelled by `render_type`).
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    if record_like(inner, analyzer):
        if _f1_container_ref(_binding_peel(inner)):
            _witness("decl.opt_ptr_container")
        return binding
    return None

def builds_named_frame(call: TpyExpr, analyzer) -> bool:
    """A generator call sema typed as the concrete frame it builds: the
    call is a frame factory wherever its result lands (a decl, a slot, an
    argument), since the frame is one object that is moved into place
    before it starts, never copied."""
    t = analyzer.get_expr_type(call)
    return t is not None and isinstance(_binding_peel(t), ConcreteGenType)


def frame_object_slot(t: TpyType | None) -> bool:
    """A binding that holds a generator / coroutine frame object: one
    non-copyable, non-assignable frame, rebound by rebuilding its storage
    (THIRAssign.rebuild)."""
    return t is not None and isinstance(_binding_peel(t), ConcreteFrameType)


def _frame_factory_source(init: TpyExpr | None, target_t: TpyType | None,
                          analyzer) -> bool:
    """A generator call filling a slot that holds a generator object
    (`g = gen(n)` where `g` is the frame): the slot IS the frame, so the
    call's result lands whole -- the FRAME_FACTORY form, whose call lowering
    validates the callee and its args. Sema already refused a slot of a
    different generator function."""
    return (init is not None and isinstance(init, (TpyCall, TpyMethodCall))
            and frame_object_slot(target_t)
            and builds_named_frame(init, analyzer))


def _record_rvalue_source_shape(init: TpyExpr, analyzer) -> bool:
    """Classify an rvalue call producing a reference type -- an F1 record or
    a builtin container, which take the same slot renders (`T __slot_N =
    init;` / `T* x = &__slot_N;`, the pointee spelled by `render_type`).

    This is intentionally shallow. The consuming expression lowering arm
    validates and lowers each argument with the actual temp/narrowing context.
    """
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (record_like(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    fi = init.resolved_function_info
    if fi is None:
        return False
    # The ctor face shares `_ctor_shape_ok`'s same-name free-fn collision,
    # generic / native / multi-overload / special-form `__init__` checks. Its
    # emit must be `Name(args)` or qualified `::ns::Name(args)`. It
    # owns the arity verdict too (`_ctor_arity_ok`, shared by the raw-name
    # and instantiation forms: omitted trailing defaults ride the C++ ctor
    # signature). The by-value record-returning free-call face shares
    # free-call lowering's callee-shape head (linkage, literal-overload mangling,
    # generics, error_return -- shapes whose emit is not the bare
    # `name(args)`) and its arity rule (`_call_arity_ok`: omitted trailing
    # defaults ride the emitted C++ signature).
    if fi.is_constructor:
        return (_ctor_shape_ok(init, analyzer)
                or _ctor_instantiation_ok(init, analyzer))
    # The by-value reference-returning FREE-call face: the same callee-shape head
    # as free-call lowering (linkage / literal-overload / generics / error_return
    # via `_plain_free_callee_ok`) + exact arity, then the SHARED plain-call arg
    # cascade -- so `return make_rec(s, xs, r)` routes the str / container /
    # record / Own-move / optional-ptr / union arg shapes a free call already
    # carries, not the reduced scalar-only subset. Guard: a str-literal into a
    # multi-overload callee pins to the view form (`string_view("...")`), which
    # the bare emit does not reproduce -- mirror free-call lowering's pin.
    return _rvalue_free_call_shape(init, analyzer)


def _own_return_call_shape(e: TpyExpr, analyzer) -> bool:
    """A free call whose CALLEE DECLARES the ownership transfer (`-> Own[T]`):
    `copy(p)` and a `-> Own[Point]` factory alike, the shape an owning sink
    may take BY VALUE.

    Keyed on the declared return, never on `is_rvalue_source`: that is True
    for a BORROWING callee too, and admitting one at an owning slot turns a
    rejected aliasing bug into a silently warned copy
    (BUGS.md#own-slot-borrow-call-result). The type verdict on the slot and
    the render both live at the caller -- like `_record_rvalue_source_shape`
    this is shallow, and the consuming lowering arm validates the arguments.
    """
    if not isinstance(e, TpyCall):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    fi = e.resolved_function_info
    if fi is None or fi.is_constructor or not _call_arity_ok(e, fi):
        return False
    return isinstance(unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(fi.return_type))), OwnType)


def _own_return_getter_shape(e: TpyExpr) -> bool:
    """The ACCESSOR spelling of `_own_return_call_shape`: a `@property` read
    OFF `self` whose getter DECLARES `-> Own[T]`, so what it hands back is a
    value the owning sink may take.

    Separate from its sibling because the sibling is spelled for a `TpyCall`
    and a read is a method call. The two collapse into one predicate the day
    a SPELLED zero-arg method is admitted at an owning sink -- see TODO.md,
    "A borrow-returning zero-arg METHOD call is a chain hop".

    The `self` restriction holds a VERDICT, not a lifetime: the getter builds
    the value and moves it out, so the receiver's lifetime cannot matter after
    the call, and the same read off a parameter would be just as sound. It is
    the receiver at which this yield was admitted before a read became a call,
    and admitting the others is a widening nobody has measured.
    """
    fi = property_getter_of(e)
    return (fi is not None
            and isinstance(e.obj, TpyName) and e.obj.name == "self"
            and isinstance(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(fi.return_type))), OwnType))


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


def _field_write_receiver_ok(target: TpyExpr, declared: dict[str, TpyType],
                             analyzer,
                             pointers: 'AbstractSet[str]' = frozenset()
                             ) -> bool:
    """The receiver ladder the whole VALUE axis of the field write shares:
    is `target` a rooted lvalue chain the assign can land in, whatever the
    field's family is.

    Every rung here answers one question -- does the receiver render as an
    lvalue the store can be spelled against -- and none of them looks at the
    value. That is why the ladder is ONE predicate: the scalar, `str` and
    `bytes` writes differ only in how the VALUE reaches the slot (bare, or
    the `bytes` view->storage copy), and `_lower_field_write_target` renders
    the target for all three through the same path.

    An Optional-ptr-receiver target is admitted on both faces: proven ->
    `p->field = <value>;` (arrow via `_field_receiver_ok`), unproven ->
    `::tpy::deref_check(p).field = <value>;` (`_optional_checked_field`).

    Two receiver shapes are deliberately NOT here. A USER-Deref receiver
    (`rc.field = v`) needs the narrow set to decide its `__deref__()` hop,
    so it keeps its own admission (`_user_deref_field_write_ok`), which also
    pins its value set. A borrow-TUPLE element (`t[1].val = 99`) needs the
    lowering context for the element's borrow verdict, so it stays in
    `_btuple_elem_field_write_ok`."""
    return (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
            or _ptr_value_field_recv_ok(target, declared, analyzer)
            or _optional_checked_field(target, declared, analyzer)
            # The STORAGE sibling (`h.opt.x = 5` ->
            # `::tpy::deref_optional_check(h.opt).x = 5;`): the wrap goes
            # around the whole optional lvalue, and that render is position-independent
            # exactly like the already-`T*` name receiver beside it -- the
            # predicate was scoped to reads only by choice, not by render.
            or _optional_checked_field_over_field_ok(target, declared,
                                                     analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)
            or _field_over_container_subscript_ok(target, declared, analyzer,
                                                  pointers)
            or _field_over_record_getitem_ok(target, declared, analyzer,
                                             pointers)
            # `a.inner.count = v` -- the receiver is itself a field read. The
            # aug-assign twin has always admitted it; the plain-assign half
            # never got the row, and both render the same target string.
            or _field_over_field_ok(target, declared, analyzer)
            # `h.val.x = 8` -- the receiver is a PROPERTY getter returning
            # a borrow (`h.val().x = 8;`): the write chains the same
            # postfix member the read row admits.
            or _field_over_call_ok(target, analyzer,
                                   any_record_ok=True)
            or _method_recv_field_write_ok(target, declared, analyzer))


def _viewfam_field_write_receiver_ok(target: TpyExpr,
                                     declared: dict[str, TpyType],
                                     analyzer,
                                     pointers: 'AbstractSet[str]' = frozenset()
                                     ) -> bool:
    """The shared receiver ladder, minus the receivers through which a live
    VIEW of the written `str`/`bytes` field cannot be demoted.

    A one-hop field read of either family binds a view of the field's buffer
    (`is_view_compatible_source`, `tpyc/sema/local_deduction.py`), and what
    keeps that view valid is the demotion sema fires when the storage it
    borrows is written (`_mark_field_write_views`). That demotion resolves a
    write to a storage KEY -- a name, or a dotted field path rooted in one --
    so a receiver that reaches the record through anything the tracker cannot
    name keys nothing a view was registered under: the view survives the
    write and reads freed memory, with no diagnostic. Five such receivers:

      p.name = s          `p: Ptr[Inner]` (and `self.p.name = s`) -- a deref
      t[0].name = s       a tuple element
      rows[0].name = s    a container element (`d[k]`, and the `*args` pack
                          subscript, share this shape)
      b.get().name = s    a borrow-returning method call
      h.val.name = s      a `@property` getter -- the chain spells
                          `h.val.name`, which keys nothing, and the coarse
                          receiver fallback never fires because a field path
                          IS spellable (just the wrong one)

    Each is rejected at the write on master; re-admitting them needs escape
    to be a Place fact on the borrow tracker
    (BUGS.md#field-view-escape-needs-place). The receivers that stay are a
    name / `self` (master's own set), and three master rejects measured to
    demote the held view instead: a nested field, an unproven Optional name,
    and an unproven Optional over a field. One admitted receiver does NOT
    demote -- a user `__getitem__` element (`b[0].name = s`) -- and it stays
    because master's `str` write gate admits it too: the dangle there is
    master's, filed under the same slug, not this gate's to decide.

    The value-scalar rows beside this one keep the full ladder: no view
    borrows an `int32` field, so the same receivers are safe there."""
    if (_ptr_value_field_recv_ok(target, declared, analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)
            or _field_over_container_subscript_ok(target, declared, analyzer,
                                                  pointers)
            or _field_over_call_ok(target, analyzer,
                                   any_record_ok=True)
            or _method_recv_field_write_ok(target, declared, analyzer)):
        return False
    return _field_write_receiver_ok(target, declared, analyzer, pointers)


def _scalar_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer,
                           pointers: 'AbstractSet[str]' = frozenset()) -> bool:
    """A scalar-field write `recv.field = <scalar>`: a value-scalar field at a
    shared field-write receiver (`_field_receiver_ok` also rejects the
    property-setter / __setattr__ write target), written with an eligible
    scalar expression. The scalar sibling of `_f2b_optional_field_write_ok`
    -- it emits the default field assign (`recv.field = <value>;`, no
    borrow<->storage lift). A record-element tuple subscript target
    (`t[N].field = <scalar>` -> `std::get<N>(t)->field = ...`, the write
    analog of the record-element read) rides the shared ladder; an
    Optional-element target is rejected by its markers. A char field writes
    identically (`recv.c = z`); its str-literal value guard is defensive --
    sema type-errors a literal into a char field, but the target-typed `'x'`
    render would otherwise diverge."""
    target = stmt.target
    if not _field_write_receiver_ok(target, declared, analyzer, pointers):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        if isinstance(stmt.value, TpyStrLiteral):
            return False
    elif not (_eligible_scalar(ftype)
              or _eligible_enum(ftype, analyzer) is not None
              or _is_type_param_slot(ftype)
              or _eligible_ptr_value(ftype, analyzer)
              # The Callable-field twin of the value plan's lambda-NAME
              # row (`self.callback = add_offset;`).
              or (_callable_value(ftype)
                  and isinstance(_peel_coerce(stmt.value), TpyName))
              # ... and its Optional[Callable] flavor (`self.on_event =
              # cb;` -- `std::optional<std::function>`'s operator= absorbs
              # the same bare name; None rides the opt-none family).
              or (isinstance(ftype, OptionalType)
                  and _callable_value(unwrap_readonly(ftype.inner))
                  and isinstance(_peel_coerce(stmt.value), TpyName))
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
                  and isinstance(_peel_coerce(stmt.value), TpyStrLiteral))
              # A NoneType field (`slot: None` -> `std::monostate`): borrow
              # and storage coincide, so the store is bare like a scalar's
              # (a None literal renders the STORAGE `std::monostate{}`).
              or (isinstance(unwrap_readonly(ftype), NoneType)
                  and _witness("field.none_unit_write"))):
        # A generic record's `T` field write emits as a plain assign (`field = v`
        # / `field = std::move(v)`) -- the BORROW->STORAGE convert renders the
        # source bare/moved (its TypeParamRef emit arm); `val_or_ref_t<T>` /
        # `own_param_t<T>` fix the copy/move per instantiation.
        return False
    return True

def _bytearray_value_slot_init(init: 'TpyExpr | None',
                               vtype: 'TpyType | None', analyzer) -> bool:
    """A `bytearray` VALUE decl slot for a `bytesview_to_bytearray`-coerced
    view source: the materialize copy,
    `::tpy::ByteArray ba = ::tpy::ByteArray(<view>);`. NAME inits never
    reach the value ladder (the alias cascade binds them REF_ALIAS), and a
    `bytes` source at this slot is a sema error (it would alias under CPython
    and copy here), so the coerce leg routes only through the materialize
    disposition. The owned dunder RVALUE (`bb = ba + b"cd"`) is the shared
    fresh reference-init row's (`_rvalue_ref_init`), which renders the same
    fresh buffer into the same slot."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))
         if vtype is not None else None)
    if t is None or not _bytes_family_ref(t) or init is None:
        return False
    if (isinstance(init, TpyCoerce)
            and init.coercion.name == "bytesview_to_bytearray"
            # The designated chokepoint answers "does this coerce
            # materialize", so a future non-materializing bytearray coerce
            # cannot reach this render by accident.
            and _coerce_disposition(init) == "materialize"):
        return bool(_witness("decl.bytearray_view_copy"))
    return False


def _user_deref_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                               narrowed: 'AbstractSet[str]', analyzer,
                               pointers: 'AbstractSet[str]') -> bool:
    """A scalar-field write auto-dereffed through a USER Deref wrapper
    (`r.x = <scalar>` -> `r.__deref__().x = <scalar>`): the user-Deref sibling
    of `_scalar_field_write_ok`. Value set matches (scalar / char / enum / Ptr
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
    copy: a field source is not a ptr-variant source, so it
    assigns bare with no lift), or a member-typed CTOR rvalue (`h.pet =
    Cat(9)` -- the variant assignment absorbs the member, so the bare ctor
    assigns with no lift; exact member type only)."""
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
    `__del__`): a DEMOTED init of such a field must raise the
    `reject_nondef_ctor_field_in_body` diagnostic rather than emit the
    uncompilable default-init, so the plain-assign row declines it. Keyed on
    the field TYPE only -- an inherited field of such a type over-rejects (the
    diagnostic covers own fields only), which is safe."""
    rec = analyzer.registry.get_record_for_type(ftype)
    return rec is not None and del_suppresses_default_ctor(rec)

def _ref_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], analyzer,
        pointers: set[str], narrowed: AbstractSet[str],
        prescan: '_Prescan') -> bool:
    """A plain REFERENCE-axis field write `recv.field = <source>` -- ONE
    admission for the record and builtin-container halves of `record_like`,
    which take the same default field assign with no borrow<->storage lift.
    Source rows (beyond the two below: a borrow-returning call, a field
    read, an element subscript, and a pointer-local -- each copies
    bare, the last through its deref):

      * a **reference rvalue** (the `_record_rvalue_source_shape` -- a ctor /
        by-value reference-returning call): a direct copy
        `recv.field = Inner(args);`. The exact source-type == field-type check
        keeps a subclass rvalue (a slicing copy) out.
      * a **reference NAME** (a declared borrow param / owned local of a
        record or container type, incl. `Own[T]` params): the bare copy
        `recv.field = p;` (plus sema's implicit-copy warning,
        path-independent), or `std::move(p)` at a movable name's last use
        (`_maybe_move`) -- the plain STORAGE convert arm. Narrowed names
        (`(*o)` deref renders), pointer-locals (`(*p)`), `self`, and
        coerce-wrapped sources reject.

    Three rows come from the container half and name a builtin container on
    purpose: `copy(xs)`'s copy-CONSTRUCT rvalue, a container LITERAL /
    `[e] * n` repeat, and an owned-rvalue call whose result materializes the
    field's own storage. Each is a decision about how the VALUE is built,
    not about which family the SLOT is -- the one place a lowering gate may
    name a container -- and the lowering keeps them as their own render
    rows for the same reason.

    In a constructor body this gate sees only DEMOTED inits (the MIL hoist
    already ran in `lower_constructor`), which take the same
    default assign -- routed, except a field type with a suppressed default
    ctor (see `_nondef_ctor_field`, which raises). An unbound-self
    `BaseN.field` target joins both source rows unchanged.

    A USER-Deref receiver (`r.field = p` on `r: Rc[T]` ->
    `r.__deref__().field = p`) joins the same source rows: the deref chain is
    a target-position render decided by the receiver, orthogonal to the value
    row that decides copy-vs-move. Only the PLAIN `record_like` slot is widened
    -- a pointer-repr `Optional[record]` at a deref target needs the
    `ptr_to_optional` lift and keeps rejecting."""
    target = stmt.target
    if not (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
            or _user_deref_field_recv_ok(target, declared, narrowed,
                                         analyzer, pointers)):
        return False
    ftype = analyzer.get_expr_type(target)
    if not record_like(ftype, analyzer):
        return False
    if prescan.is_constructor and _nondef_ctor_field(ftype, analyzer):
        return False
    # `copy(T(...))` peels to its constructor (identical render); `copy(name)`
    # is the copy-CONSTRUCT rvalue `T(name)`. Both land at the field bare.
    ctor_peel = copy_ctor_rvalue_source(stmt.value, analyzer)
    if ctor_peel is not None:
        return (_record_rvalue_source_shape(ctor_peel, analyzer)
                and analyzer.get_expr_type(ctor_peel) == ftype)
    # One classifier, both families: the RECORD half still checks slot
    # equality (a subclass source would spell the wrong ctor), the container
    # half does not -- the copy render follows the SOURCE type, and no
    # builtin container has a subclass to slice.
    crec = copy_construct_source(stmt.value, analyzer, pointers)
    if crec is not None:
        return crec == ftype if _f1_record(crec, analyzer) else True
    # The container LITERAL / repeat and the owned-rvalue call whose result
    # materializes its own container -- tried BEFORE the exact-typed rvalue
    # row below, whose type check would otherwise turn an `Own[C]`-returning
    # free call into a reject rather than let its own render row claim it.
    if (_container_field_write_ok(stmt, declared, analyzer)
            or _container_prvalue_field_write_ok(stmt, declared, analyzer)):
        return True
    if _record_rvalue_source_shape(stmt.value, analyzer):
        vt = analyzer.get_expr_type(stmt.value)
        return (vt == ftype
                or _record_slice_upcast_ok(vt, ftype, analyzer))
    v = stmt.value
    # A BORROW-returning call source copies bare on assignment
    # (`h.p = identity(pt);` / `self.mirror = h.peek();` -- the C++
    # copy-assign absorbs the `T&`; sema warns the container copy, so the
    # alias-vs-copy divergence is declared rather than silent). Both call
    # kinds and both halves of the axis: the copy-assign absorbs a reference
    # the same way whichever produced it.
    if (isinstance(v, (TpyCall, TpyMethodCall))
            and v.resolved_function_info is not None
            and call_returns_cpp_ref(analyzer, v.resolved_function_info)
            and analyzer.get_expr_type(v) == ftype
            and record_like(ftype, analyzer)):
        return _witness("field_write.borrow_call_copy")
    # A reference-returning METHOD call rvalue copies bare too
    # (`task._waker = handle->make_waker_for_slot(..);`). Shallow like the
    # free-call rvalue shape: the method arm validates and lowers the callee
    # and args itself, gated on the same copy-sink flag this write threads.
    # RVALUE only -- a borrow-returning method result is the REF_ALIAS
    # frontier the lowering arms keep rejecting.
    if (isinstance(v, TpyMethodCall) and v.resolved_function_info is not None
            and is_rvalue_source(analyzer, v)
            and analyzer.get_expr_type(v) == ftype
            and record_like(ftype, analyzer)):
        return _witness("field_write.method_rvalue_copy")
    # A FIELD or element SUBSCRIPT source copies bare
    # (`h.p = h2.p;` / `h.p = ::tpy::__getitem__(pts, 0);` -- the reads
    # are references, the assign copies; never movable).
    if isinstance(v, TpyFieldAccess):
        return (_field_receiver_ok(v, declared, analyzer)
                and record_like(analyzer.get_expr_type(v), analyzer)
                and analyzer.get_expr_type(v) == ftype
                and _witness("field_write.field_copy"))
    if isinstance(v, TpySubscript):
        return (_container_record_elem_subscript(v, declared, analyzer,
                                                pointers)
                and analyzer.get_expr_type(v) == ftype
                and _witness("field_write.subscript_copy"))
    # A POINTER-local source copies through the deref
    # (`this->result = (*saved);` -- the indirect-name read;
    # pointers are never movable, so no move wrap).
    if (isinstance(v, TpyName) and v.name in pointers
            and v.name in declared and v.name not in narrowed
            and not (prescan.has_self and v.name == "self")):
        vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[v.name])))
        return (record_like(vt, analyzer)
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
    return record_like(vt, analyzer)

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
    the write is bare (`this->o = v;`) and the optrec name arm applies
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
    need other renders and reject."""
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
    crec = copy_construct_source(v, analyzer, pointers)
    if crec is not None:
        return crec == inner
    if _record_rvalue_source_shape(v, analyzer):
        vt = analyzer.get_expr_type(v)
        return vt == inner or _record_slice_upcast_ok(vt, inner, analyzer)
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

def _record_slice_upcast_ok(vt: 'TpyType | None', target: 'TpyType | None',
                            analyzer) -> bool:
    """`vt -> target` is a plain SUBCLASS upcast at a record slot, where the
    C++ assign slices to the base -- the narrowing sema admits for ASSIGN /
    INIT / RETURN under its "upcast narrows" warning, so the divergence from
    CPython (which keeps the derived object) is declared, not silent. The
    source keeps its own spelling; `operator=` does the slicing.

    Distinct from `_covariant_record_upcast_ok`, which is
    representation-PRESERVING and loses nothing. A polymorphic base never
    reaches here: sema rejects a `@dynamic`-protocol rvalue at a record slot
    before lowering."""
    return (isinstance(vt, NominalType) and isinstance(target, NominalType)
            and vt != target and _f1_record(vt, analyzer)
            and _f1_record(target, analyzer)
            and analyzer.registry.is_subclass_of(vt, target))


def _optional_record_field_upcast_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], analyzer) -> bool:
    """A covariant-generic record RVALUE written into a pointer-repr
    `Optional[generic]` field (`recv.opt = Box(conn)` at a `Box[Proto] | None`
    slot): the default field assign renders the bare plain assign -- the
    source spells its OWN inferred type (`Box<HTTPConnection>(...)`) and
    `optional::operator=` absorbs the converting move -- so the field's inner
    type is never rendered, and its F1-ness does not gate the write. At emit
    the inner qualifies F1 (generation context), so the admitted write rides
    the optrec rvalue arm; corpus witness: the tplib/requests_* cases'
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

def _container_storage_field(t) -> bool:
    """A reference-axis container FIELD type the container-literal slices
    admit: an instantiated `list` / `dict` / `set` / `Array`, or `bytearray`.

    Membership is the axis (`_f1_container_ref`), so `Span` stays out by the
    axis itself -- a borrowing view, not a reference container (a Span field
    aliasing its source is a lifetime shape these slices do not open, and sema
    rejects the useful forms anyway). The remaining test is about RESOLUTION,
    not family: `bytearray` is the axis's one non-generic member and has no
    type args to wait for, while an argument-less `list`/`dict`/`set`/`Array`
    is a still-pending literal type the literal rows resolve first."""
    if not isinstance(t, TpyType):
        return False
    if not _f1_container_ref(t):
        return False
    return bool(getattr(t, "type_args", None)) or _bytes_family_ref(t)


def _optional_container_storage_inner(t) -> 'TpyType | None':
    """The CONTAINER inner of an `Optional[list/dict/set/Array]` FIELD, or
    None -- the type a container literal is classified and lowered against at
    a field slot, since the literal render unwraps the Optional itself.

    No `uses_pointer_repr()` guard: that predicate answers for the BORROW
    positions (params/returns/locals), where a non-value inner takes `T*`;
    a field slot is storage form and spells `std::optional<C>` regardless.
    Reading the borrow verdict here would reject every container inner."""
    if not isinstance(t, OptionalType):
        return None
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t.inner)))
    return inner if _container_storage_field(inner) else None


def _container_field_write_slot(stmt: TpyAssign, declared: dict[str, TpyType],
                                analyzer) -> 'TpyType | None':
    """The container field's DECLARED slot -- the type the field assign
    threads into the value render -- or None when the receiver shape does not
    resolve one.

    Never the flow-narrowed read type: a narrowed `Optional[container]` field
    reads as the bare container through `get_expr_type`, but its C++ storage
    is still the `std::optional<C>` the assign threads, so classifying off the
    read type picks a render one unwrap too shallow
    (`from_range<std::vector<T>>` for `from_range<std::optional<...>>`, the
    bare brace for the typed one). Shared by the rows whose render consumes
    the slot type."""
    fdt = _field_decl_type(stmt.target, declared, analyzer)
    if fdt is None:
        # The UNBOUND-SELF form (`BaseN.field = ...`) has no receiver binding
        # to walk, so the declaration is read off the parent type the access
        # already carries.
        fdt = _unbound_self_field_decl_type(stmt.target, analyzer)
    if fdt is None:
        return None
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))

def _container_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A container-literal field write `recv.field = [...] / {...}` off an
    F1-record receiver: the default field assign renders the value against the
    FIELD type as target -- the same target-threaded literal
    render a decl init gets (`{e1, e2}` consumed by the vector lvalue, the
    spelled empty list, the `::tpy::ordered_map<K, V>(...)` /
    `ordered_set<T>(...)` constructor forms) with no move wrap (a literal is
    never a movable name). Element admission is the shared
    container-literal slice. A COMPREHENSION joins the row: its stmt-expr
    builds the field's own container in place (the member-init prefix's
    `mil.container_comp` render, one position down), so it lands bare too;
    the comprehension's own route owns every element reject.

    A storage-form `Optional[container]` ftype takes the same render one
    unwrap down -- the field is a `std::optional<C>` and the literal is
    classified against C."""
    comp = isinstance(stmt.value, (TpyListComprehension, TpySetComprehension,
                                   TpyDictComprehension))
    if not comp and not isinstance(stmt.value, (TpyArrayLiteral,
                                                TpyDictLiteral,
                                                TpySetLiteral)):
        return False
    if not _field_receiver_or_unbound_self_ok(stmt.target, declared,
                                              analyzer):
        return False
    ftype = _container_field_write_slot(stmt, declared, analyzer)
    if ftype is None:
        return False
    oc_inner = _optional_container_storage_inner(ftype)
    if oc_inner is not None:
        # A storage-form `std::optional<C>` field: the literal render unwraps
        # the Optional itself, so the
        # literal is classified (and lowered) against the INNER -- element
        # targets derived from the Optional would be wrong.
        if comp:
            return _container_comp_arg(stmt.value, oc_inner)
        return _container_literal_shape_ok(stmt.value, oc_inner, analyzer)
    if comp:
        return _container_comp_arg(stmt.value, ftype)
    return _container_literal_shape_ok(stmt.value, ftype, analyzer)

def _container_prvalue_field_write_ok(stmt: TpyAssign,
                                      declared: dict[str, TpyType],
                                      analyzer) -> bool:
    """A container field written from a value that MATERIALIZES its own owned
    container: `recv.field = [e] * n` (TpyListRepeat) or a container-returning
    METHOD-call rvalue (`self.lines = data.splitlines()`). Neither shape needs
    a name indirection or an
    Optional unwrap, and neither is a movable NAME, so the field assign
    lands the value render bare.

    Rvalue-only for the method call: a borrow-returning method aliases its
    receiver and the by-value field copy off that alias is not what the bare
    passthrough spells -- the same line the return-position row draws. A
    storage-form `Optional[container]` ftype stays out: its literal row unwraps
    the Optional before threading a target, a decision neither prvalue here
    makes. The slot is the DECLARED field type, so a flow-narrowed
    `Optional[container]` field stays out too.

    The CONTAINER half of the axis on purpose, and the one row of the merged
    field-write ladder that is not on `record_like`. Its render threads
    `_ExprResultUse.STORAGE` where the reference rvalue row threads the
    copy sink, and the reference rvalue row pins the source type to the slot
    -- the check that keeps a subclass rvalue (a slicing copy) out. Widening
    this row to `record_like` would claim every record ctor rvalue and move it
    onto the STORAGE render; dropping the record row's type pin to let this
    one claim only what that row refuses would reopen the slice. Containers
    have no subclass, which is why the two rules can differ at all."""
    if not _field_receiver_or_unbound_self_ok(stmt.target, declared, analyzer):
        return False
    ftype = _container_field_write_slot(stmt, declared, analyzer)
    if not _f1_container_ref(ftype):
        return False
    if isinstance(stmt.value, TpyListRepeat):
        return _container_literal_shape_ok(stmt.value, ftype, analyzer)
    # A FREE call joins the method call on the same rvalue-only terms: an
    # `Own[container]` return materializes its own container and lands bare,
    # while a borrow-returning free call aliases its argument and is not what
    # the bare passthrough spells.
    if not (isinstance(stmt.value, (TpyMethodCall, TpyCall))
            and is_rvalue_source(analyzer, stmt.value)):
        return False
    vt = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(stmt.value))))
    return _f1_container_ref(vt)


def _str_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                        analyzer,
                        pointers: 'AbstractSet[str]' = frozenset()) -> bool:
    """A str-family field write `recv.field = <str literal | str name>` at a
    shared field-write receiver: the default field assign renders the value BARE
    (`recv.field = s;` / `= "lit";`) -- `std::string::operator=(string_view)`
    absorbs a view source into an owned field, so unlike a decl init there is
    NO view->owned `std::string(...)` construction, and str names are never in
    codegen's movable set (value-typed decl arms don't register), so no move
    wrap either. A str-typed BINOP value (`self.buf = self.buf + s`) also
    assigns its concat render bare, and a same-family COERCE wrap peels
    transparently before the rows (so a coerce-wrapped literal / name /
    binop admits like its bare form -- the wrap does not change the
    assign render). Two further value shapes ride the same bare assign: a
    str-family SLICE subscript (`self.s = x[1:3]` -- the sema coerce node
    carries its own `std::string(::tpy::str_slice(...))` materialization,
    which is why the render must keep lowering the UNPEELED value) and a
    zero-arg `str()` ctor call. A `builds_fresh_value` coerce (`self.tag =
    CHARS[i]`) is the same argument stated as the FACT rather than as a
    source shape: its wrap is the construction, so whatever sits under it
    assigns bare. A NAME declared `str | None` and narrowed to
    `str` stays OUT: the deref moves at a last use
    (`this->s = std::move((*s));`) and this arm renders it bare. A str-typed
    call or method call of any other shape rides the same bare assign as
    well; `String`-typed fields/sources keep their own emit shapes (excluded
    by `_resolved_str_value`).

    The RECEIVER is the shared ladder's business, not this row's: a nested
    or element receiver (`o.inner.name = "b"`, `rows[0].name = "b"`) renders
    the same lvalue for a `str` field as for the `int32` beside it, so this
    arm decides only the value side -- through the view-family gate, which
    subtracts the receivers a live view of the field could not be demoted
    through (`_viewfam_field_write_receiver_ok`)."""
    if not _viewfam_field_write_receiver_ok(stmt.target, declared, analyzer,
                                            pointers):
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
    # A fresh-value coerce (`self.tag = CHARS[i]`) carries its own
    # `std::string(::tpy::char_to_str(...))` construction, so the UNPEELED
    # value assigns bare -- the str_slice row's argument, keyed on the fact
    # rather than on the source shape under the wrap.
    if (isinstance(stmt.value, TpyCoerce)
            and stmt.value.coercion.builds_fresh_value
            and outer_str is not None):
        return _witness("field_write.fresh_value_coerce")
    if isinstance(v, TpyStrLiteral):
        return True
    if isinstance(v, TpyBinOp) and outer_str is not None:
        return True
    if (isinstance(v, TpySubscript) and isinstance(v.index, TpySlice)
            and outer_str is not None):
        return _witness("field_write.str_slice")
    if (isinstance(v, TpyCall) and isinstance(v.func, TpyName)
            and v.func.name == "str" and not v.args and not v.kwargs):
        return _witness("field_write.str_ctor")
    if isinstance(v, (TpyCall, TpyMethodCall)) and outer_str is not None:
        return _witness("field_write.str_call")
    return (isinstance(v, TpyName) and v.name in declared
            and _resolved_str_value(declared[v.name], analyzer) is not None)

def _bytes_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                          analyzer,
                          pointers: 'AbstractSet[str]' = frozenset()) -> bool:
    """The bytes twin of `_str_field_write_ok`: an owned `bytes` field write
    `recv.field = <bytes literal | bytes name>` at a shared field-write
    receiver.
    Unlike str, `std::vector<uint8_t>` has no span ctor, so a view (span)
    source copies via the S6 `::tpy::Bytes(...)` STORAGE convert (the
    lowering picks it off the source form) -- an owned source (bytes literal /
    owned local) lands bare. `bytearray` (a reference type) is excluded by
    `is_bytes_type`. A NAME declared `bytes | None` and narrowed to `bytes`
    here joins the plain name row: its deref is a view either way, so it
    takes the same `Bytes(x)` convert -- never a move, which is what keeps
    the str twin of this row OUT of `_str_field_write_ok`. A bytes SLICE
    source (`self.b = x[1:3]`) mirrors the str slice row, but lands on the
    OTHER side of the split the str docstring names: the sema coerce carries
    no materialization for bytes, so the view-form slice takes this family's
    ordinary `Bytes(x)` STORAGE convert.

    A bytes BINOP (`self.buf = self.buf + chunk`) and a bytes-returning CALL
    both land on the same rule as every other source here: the family's
    verdict is read off the lowered source's FORM, so an owned concat rvalue
    assigns bare and a view-returning call takes the `Bytes(x)` convert. The
    two shapes need no row of their own beyond admission.

    The RECEIVER is the str twin's view-family gate, for the reason given
    there: which lvalue the store lands in decides nothing about the
    view->storage copy, but it does decide whether a live view of the field
    can be demoted at all."""
    if not _viewfam_field_write_receiver_ok(stmt.target, declared, analyzer,
                                            pointers):
        return False
    ft = _resolved_bytes_value(analyzer.get_expr_type(stmt.target), analyzer)
    if ft is None or not is_bytes_type(ft):
        return False
    v = stmt.value
    if isinstance(v, TpyBytesLiteral):
        return True
    # The shape rows below peel the sema coerce and type off the OUTER
    # (possibly coerce-wrapped) node; the literal/name rows key the RAW node,
    # since widening them to coerce-wrapped sources would admit shapes no
    # render row was measured against.
    sliced = v.expr if isinstance(v, TpyCoerce) else v
    outer = _resolved_bytes_value(analyzer.get_expr_type(v), analyzer)
    outer_bytes = outer is not None and is_bytes_type(outer)
    if isinstance(sliced, TpySubscript) and isinstance(sliced.index, TpySlice):
        return outer_bytes and _witness("field_write.bytes_slice")
    if isinstance(sliced, TpyBinOp):
        return outer_bytes and _witness("field_write.bytes_binop")
    if isinstance(sliced, (TpyCall, TpyMethodCall)):
        return outer_bytes and _witness("field_write.bytes_call")
    if not (isinstance(v, TpyName) and v.name in declared):
        return False
    dt = declared[v.name]
    if _resolved_bytes_value(dt, analyzer) is not None:
        return True
    return (isinstance(dt, OptionalType)
            and _resolved_bytes_value(dt.inner, analyzer) is not None
            and _resolved_bytes_value(analyzer.get_expr_type(v),
                                      analyzer) is not None
            and _witness("field_write.bytes_narrowed_opt"))

def _class_const_write_target_ok(target, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """A class-constant / classvar write lvalue (`C.X = v`, `obj.X = v`):
    the bare qualified
    `<owner>::<member>` with the receiver eval split into a leading
    statement. Scalar constants only (the sole slot family whose value
    render is the plain target-typed assign; str/tuple constants take
    other value shapes). Receiver shapes: pure (no eval),
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
                                                analyzer, pointers)
    return True

def _class_const_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                               pointers: set[str], analyzer) -> bool:
    """Aug-assign on a class-constant lvalue: the bare qualified name
    substitutes into `target = (target OP value)` (receiver eval split
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
                          analyzer, pointers: "AbstractSet[str]") -> bool:
    """A scalar augmented assignment `x += y` / `recv.field += y` that renders
    exactly as `target = (target OP value)` -- the plain binop substitution,
    with every other aug-assign branch gated out:

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
    `divisor_non_zero=False` -- an aug-assign never swaps
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
              or _field_over_container_subscript_ok(target, declared,
                                                   analyzer, pointers)
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
    # A narrowed-Optional or non-scalar target is rejected here (the former
    # needs an unwrap, the latter is not a binop-substitution shape). An
    # Optional-ptr-receiver field target IS admitted (proven -> `p->x`,
    # unproven -> `deref_check(p).x` -- the same render lands on both sides of
    # the synthetic `target = (target OP value)`, which substitutes the
    # target string twice).
    target_type = analyzer.get_expr_type(target)
    if not _eligible_scalar(target_type):
        return False
    # FixedInt += BigInt converts the value via `.to_fixed_check<T>()` before
    # the binop -- carried as the synthetic THIRBinOp's right_cast at lowering
    # (the same target-type/value-type pair keys both, so gate and emit agree).
    return True

def _record_aug_binop_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                         pointers: 'AbstractSet[str]',
                         narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """`a += b` on a RECORD with no `__iadd__`: sema falls back to the
    Own-returning `__add__` and the emit is the same synthetic
    `a = (a) + (b);` its scalar sibling gets -- one paren layer fewer than the
    decl render, because the tail substitutes the target into both slots.
    The Own return is what makes the rebind sound (a borrow-returning fallback
    would alias an operand, and sema rejects that outright), so the fresh
    value lands in the plain value local. Target shapes are the bare
    non-pointer, non-narrowed local only -- the render must be identical on
    both sides of the substitution; the value re-validates at lowering."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    if not (isinstance(stmt.target, TpyName)
            and stmt.target.name in declared
            and stmt.target.name not in pointers
            and stmt.target.name not in narrowed):
        return False
    tt = analyzer.get_expr_type(stmt.target)
    tt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
          if tt is not None else None)
    return (record_like(tt, analyzer)
            and not call_returns_cpp_ref(analyzer, rb.method))


def _str_aug_append_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                       prescan: _Prescan, analyzer) -> bool:
    """A str in-place append `t += v` -> `t += v;` -- the string row of the
    resolved-binop arm: no in-place dunder, a resolved binop, op
    `+`, and an owned-str-family target. The value renders bare against the
    target type for every admitted shape (str literal /
    str-family name / owned-str call / nested concat), so it is pinned to the
    concat-operand slice.

    The target must be a declared LOCAL: a str param's aug-assign would need
    an owned-copy prologue, and the prescan tracks aug-assign targets
    in `aug_assigned`, not `reassigned`. (Emitting `a += v` on
    the untouched `std::string_view` param would be invalid C++, tracked in
    BUGS.md.)"""
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
    (the in-place append row is str-family-pinned), so it
    takes the resolved-binop arm and the generic
    `target = (target OP value)` desugar (the native
    `bytes_concat` emit, unwrapped -- `paren_wrap=False`). Condition
    for condition with the str twin (`_str_aug_append_ok`): no in-place
    dunder, a resolved binop (here the template-less native dunder), op `+`,
    and an owned-bytes target.

    The target must be a declared LOCAL: an aug-assigned bytes PARAM gets no
    owned-copy prologue (the prescan tracks aug-assign targets in
    `aug_assigned`, not `reassigned`), so
    `a = ::tpy::bytes_concat(a, b);` on the untouched span param would rebind
    the span to the concat's dying temporary vector (a dangling view, the
    bytes face of the str aug-assign-param bug in BUGS.md)."""
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
    tt = analyzer.get_expr_type(target)
    bt = _resolved_bytes_value(tt, analyzer)
    if bt is None or not is_bytes_type(bt):
        # A BYTEARRAY target (`got += chunk` in a stream-read loop) takes
        # the same resolved-binop concat-and-assign render -- the vector
        # target reassigns from the concat's fresh vector, so the param
        # dangling-span concern above does not translate; the local-only
        # condition is kept anyway (no param witness).
        ttu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(tt)))
               if tt is not None else None)
        if not is_bytearray_type(ttu):
            return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_bytes_concat_operand(stmt.value, vt, analyzer))

def _subscript_recv_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                           analyzer) -> str:
    """Drilldown suffix for a non-admitted subscript receiver -- shared by the
    read (`subscript.recv.*`) and write (`setitem.recv.*`) gates so the
    reject names WHICH receiver shape blocks (the `_recv_shape_reject`
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
    TypedDict subscript writes a FIELD; a narrowed/pointer
    receiver and slice assignment (`xs[a:b] = ...` -> list_set_slice)
    reject. A sema-narrowed Optional FIELD receiver types at its inner
    (`narrowed_ok`, the `(*recv.field)` unwrap) -- the un-narrowed
    flavor is sema-rejected outright ("Cannot assign to elements of ...
    (read-only)"), so it cannot reach here unproven. A narrowed UNION field
    still types at the declared union and rejects at the family check."""
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
        # an admitted container renders the bare alias (the
        # fact retyped `declared` for the branch); a narrowed name whose
        # declared entry is still the union (post-if / assert scopes don't
        # retype) fails the container check below and keeps rejecting.
        rb = declared.get(recv.name)
        rbu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rb)))
               if rb is not None else None)
        if (isinstance(rbu, OptionalType) and rbu.uses_pointer_repr()):
            # A None-NARROWED ptr-repr Optional[container] receiver
            # writes through the same deref (`__setitem__((*d), k, v)`);
            # the un-narrowed flavor carries needs_optional_runtime_check
            # and rejected above, so reaching here implies the proof.
            rbu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                rbu.inner)))
        if not _f1_container_ref(rbu):
            return note_detail("setitem.recv.name_shape")
    recv_t = _subscript_container_recv_type(recv, declared, analyzer,
                                            narrowed_ok=True)
    # The family check below reads the narrowed INNER container.
    _nptr = _narrowed_ptr_opt_recv(recv, recv_t, pointers)
    if _nptr is not None:
        recv_t = _nptr
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
                                                        analyzer, pointers)):
            recv_t = analyzer.get_expr_type(recv)
        elif _tce is not None:
            recv_t = _tce
        elif (isinstance(recv, TpyFieldAccess)
              and _field_markers_clean(recv)
              and isinstance(recv.obj, TpySubscript)
              and _container_record_elem_subscript(recv.obj, declared,
                                                   analyzer, pointers)):
            # A container FIELD off a record-element borrow lvalue
            # (`root.kids["a"].kids["b"] = v`): the element read is the
            # checked `__getitem__` `T&`, the field chains `.` off it and
            # is itself the written container.
            recv_t = analyzer.get_expr_type(recv)
        else:
            return note_detail(
                "setitem." + _subscript_recv_reject(recv, declared, analyzer))
    if not (_container_scalar_read(recv_t, analyzer)
            or _bytearray_recv(recv_t)
            or _setitem_widened_family_ok(recv_t, analyzer)):
        return note_detail("setitem.family")
    if (_bigint_index_disposition(sub.index, analyzer.get_expr_type(sub.obj),
                                  analyzer, declared) == "reject"):
        return note_detail("setitem.index")
    return True

def _setitem_widened_elem_ok(elem_t: 'TpyType', analyzer) -> bool:
    """Element-level arm of _setitem_widened_family_ok. The lowering's
    widened_elem dispatch consumes this same predicate so gate and dispatch
    cannot drift apart."""
    return (_container_scalar_read(elem_t, analyzer)
            or _callable_value(elem_t)
            or _value_opt_callable(elem_t, analyzer) is not None
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
                            and record_like(unwrap_readonly(_e), analyzer)
                            for _e in _swe_b.element_types)))
            # A NESTED-storage tuple value slot (`d[0] = (9, (8, c))`):
            # the bare spelled literal, per-level lifts inside.
            or (isinstance(_swe_b2 := unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(elem_t))), TupleType)
                and _nested_storage_tuple(_swe_b2, analyzer) is not None)
            # A VALUE tuple value slot (`d[k] = (key, value)` on
            # `dict[str, tuple[str, str]]`): borrow and storage coincide, so
            # the spelled brace-init stores directly with no lift -- the
            # setitem sibling of the value-tuple FIELD write. The value shape
            # narrows at the lowering arm.
            or _value_tuple(elem_t, analyzer) is not None
            # An open-T element write (`self.data[idx] = val` off
            # Array[T, N]): the bare checked `__setitem__` with the
            # form-neutral T value.
            or _is_type_param_slot(elem_t)
            # A value-repr Optional[scalar] element (`items[0] = None` on
            # `list[int32 | None]`): the nullopt / bare-scalar STORAGE
            # store; the value shape narrows at the lowering arm.
            or _value_opt_scalar(elem_t, analyzer) is not None
            # An owned-bytes element/value slot (`out["k"] = a` on
            # `dict[str, bytes]`): the view-form source takes the S6
            # `::tpy::Bytes(...)` materialize, the bytes twin of the
            # owned-str `std::string(v)` chokepoint in the value tail.
            or _resolved_bytes_value(elem_t, analyzer) is not None
            # A value-repr Optional[str/bytes] value slot (`out["k"] = a`
            # on `dict[str, str | None]`): the WHOLE optional stores bare;
            # the value shape narrows at the lowering arm.
            or _value_opt_owned_view(elem_t, analyzer) is not None
            # A reference-typed element/value slot (`s._pool[key] =
            # Box(conn)`, `d["k"] = make_bytes()`): the checked
            # `__setitem__` forwards the RVALUE bare; the value shape
            # (exact / covariant-upcast rvalue) narrows at the lowering arm.
            or record_like(elem_t, analyzer)
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
    `std::string(v)` copy -- the view-source-to-owned chokepoint),
    or a direct temp-hoisting call (the write is a flushable statement
    position, like a name assign). The other value wraps cannot fire
    here: elements of admitted families are value scalars / owned str, so
    the tuple/Optional/union storage lifts and the last-use move
    (non-value-type locals only) have no admitted source."""
    if not _setitem_target_ok(
            stmt.target, declared, pointers, narrowed, analyzer):
        return False
    return True


def _record_setitem_own_value_slot(obj_type: 'TpyType | None',
                                   analyzer) -> bool:
    """Whether the record's `__setitem__` value param is an `Own[...]` slot
    (`ArrayList.__setitem__(self, index, value: Own[T])`, a `V&&` sink).
    A copy-shaped record NAME cannot bind such a slot, so only a move source
    or an rvalue is emittable there; a plain `T` slot binds `const T&` and
    copies like any other param."""
    v = _record_setitem_value(obj_type, analyzer)
    if v is None:
        return False
    return isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(v))),
                      OwnType)


def _record_setitem_record_source(v: TpyExpr, elem_t: 'TpyType',
                                  declared: dict[str, TpyType],
                                  pointers: 'AbstractSet[str]',
                                  narrowed: AbstractSet[str],
                                  analyzer) -> bool:
    """A record value source the checked setitem forwards BARE: a same-typed
    in-scope record NAME (moved at its last use, copied otherwise) or a
    record rvalue (a ctor / by-value call). A pointer-bound or narrowed name
    reads through a deref, which the bare forward does not spell."""
    if isinstance(v, TpyName):
        if (v.name not in declared or v.name in pointers
                or v.name in narrowed):
            return False
        vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[v.name])))
        return vt == unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            elem_t)))
    return (_record_rvalue_source_shape(v, analyzer)
            and analyzer.get_expr_type(v) == elem_t)


def _setitem_container_value_source(v: TpyExpr, elem_t: 'TpyType',
                                    analyzer) -> bool:
    """A non-NAME value the container-element write renders
    (`_lower_container_elem_value`) into a user `__setitem__`'s container
    value slot: a container literal, a comprehension of the slot's kind, or
    a by-value call of the slot's type. A NAME source is the record rows'
    (moved at its last use, copied otherwise)."""
    if not _nested_container_elem_type(elem_t):
        return False
    if isinstance(v, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        return True
    if _container_comp_arg(v, elem_t):
        return True
    vt = analyzer.get_expr_type(v)
    if not isinstance(v, (TpyCall, TpyMethodCall)) or vt is None:
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
    if isinstance(vt, OwnType):
        vt = unwrap_readonly(vt.wrapped)
    return (vt == unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem_t)))
            and is_rvalue_source(analyzer, v))


def _user_record_setitem_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """A user-record subscript write `recv[key] = v` on a CONCRETE record
    defining `__setitem__` -> the no-container
    `::tpy::__setitem__(recv, key, v);` (checked, never operator[]). Mirrors the
    user-record `__getitem__` READ arm's receiver / key checks: a bare in-scope
    name or one-level field receiver, a value-scalar or str key (a
    runtime-BigInt key against a fixed-int key param carries the same
    `.to_fixed_check` narrow the READ arm applies). The value slot is a value
    scalar / char / enum / Ptr -- the bare-render families the checked setitem
    template forwards unchanged (a str/bytes value slot, whose render adds an
    owned-copy / storage lift, rejects) -- or an F1 RECORD, whose admitted
    sources are a bare record NAME and a record rvalue, both of which the
    template forwards unchanged too -- or a CONTAINER, whose NAME source is
    the record rows' and whose other sources are the container-element
    write's (`_setitem_container_value_source`)."""
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
    # applies `_narrow_bigint_index` itself -- so a runtime-BigInt key against
    # a FIXED-int key param takes the same `.to_fixed_check<T>()` narrow here
    # as it does on a read (`p[k] = 9` ->
    # `::tpy::__setitem__(p, k.to_fixed_check<int64_t>(), 9)`), and this call
    # is REDUNDANT today: the read gate rejects every index this one would.
    # It is kept because both gates must answer one index question; no case
    # can observe the two parting, since a divergence here emits byte-identical
    # C++, so the gate itself is the pin. Like the read
    # side, it runs UNCONDITIONALLY: the disposition keys the BigInt half on
    # the DECLARED type, so a per-occurrence pre-test would short-circuit past
    # it for a composite over a retro-widened local.
    idx_ok = ((_resolved_scalar(idx_type, analyzer)
               and _bigint_index_disposition(
                       sub.index, analyzer.get_expr_type(sub.obj),
                       analyzer, declared) != "reject")
              or _resolved_str_value(idx_type, analyzer) is not None)
    if not idx_ok:
        return False
    vbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vslot)))
    # An open-T (possibly Own[T]) value slot: a MONOMORPHIZED generic
    # receiver (`a[1] = 99` on ArrayList[int32]) exposes the RAW method fi
    # here, so eligibility keys on the SUBSTITUTED element -- the
    # subscript's own expr type. Scalar elements, plus an F1 RECORD from a
    # bare name or an rvalue; for an `Own[...]` slot the write arm then
    # splits move from copy, since only the move binds a `V&&`.
    vopen = vbare
    if isinstance(vbare, OwnType):
        vopen = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            vbare.wrapped)))
    if _is_type_param_slot(vopen):
        et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(sub))))
        return (_eligible_scalar(et) or _eligible_char(et)
                or _eligible_enum(et, analyzer) is not None
                or (record_like(et, analyzer)
                    and _record_setitem_record_source(stmt.value, et,
                                                      declared, pointers,
                                                      narrowed, analyzer))
                or _setitem_container_value_source(stmt.value, et, analyzer))
    return (_eligible_scalar(vbare) or _eligible_char(vbare)
            or _eligible_enum(vbare, analyzer) is not None
            or _eligible_ptr_value(vbare, analyzer)
            # A plain F1-RECORD value slot binds `const T&`, so a record
            # NAME copies into it and an rvalue binds directly -- both are
            # the bare forward. The `Own[...]` flavor is a `T&&` sink and
            # takes the move split in the write arm, so its source matches
            # the payload type.
            or (record_like(vopen, analyzer)
                and _record_setitem_record_source(stmt.value, vopen, declared,
                                                  pointers, narrowed,
                                                  analyzer))
            or _setitem_container_value_source(
                stmt.value, analyzer.get_expr_type(sub), analyzer)
            # A str LITERAL into a str-view value slot renders bare (no
            # owned-copy / storage lift fires on a literal) --
            # `other["CONTENT-TYPE"] = "application/json"`. Non-literal str
            # sources keep their view->owned machinery and reject --
            # EXCEPT the sema `strview_to_str` coerce over a view NAME
            # (`os.environ[k] = CERT_PATH` on a declared-StrView module
            # constant): the coerce IS the owned copy (`std::string(x)`,
            # the view-source-to-owned chokepoint), rendered by the
            # setitem arm's FormConvert.
            or (_resolved_str_value(vbare, analyzer) is not None
                and (isinstance(stmt.value, TpyStrLiteral)
                     # ... and an OWNED-str element read out of a value
                     # tuple (`self[pair[0]] = pair[1]`): the element is a
                     # `std::string` inside the tuple storage, so the
                     # `std::get<N>` read binds the slot bare, with none of
                     # the view->owned machinery a view-form name needs.
                     or _value_tuple_owned_str_elem(stmt.value, analyzer)
                     or ((_svc := _strview_coerce_name(stmt.value))
                         is not None
                         and (_svv := _resolved_str_value(
                             analyzer.get_expr_type(_svc),
                             analyzer)) is not None
                         and is_str_view_type(_svv))
                     # ... and an OWNED-str RVALUE (a concat, an f-string,
                     # a call result typed `str`): the owned-copy wrap
                     # keys on the SOURCE being view-form, and none of these
                     # is, so the value binds the slot bare. A NAME is
                     # excluded -- its form is its binding's, which this
                     # gate does not resolve.
                     or _owned_str_rvalue_source(stmt.value, analyzer))))

def _owned_str_rvalue_source(e: TpyExpr, analyzer) -> bool:
    """A str value source the view->owned copy does NOT fire on: an
    expression that is not
    a NAME, a str literal or a short-circuit / ternary chain, and whose
    resolved str family is owned rather than `StrView`. A concat, an
    f-string and a `str`-returning call all land here and bind the slot
    bare.

    Names and literals are excluded because their form is their binding's,
    not the expression's; the chains because they are decided
    recursively over operands this gate does not walk."""
    if isinstance(e, (TpyName, TpyStrLiteral, TpyIfExpr)):
        return False
    if isinstance(e, TpyBinOp) and e.op in ("&&", "||"):
        return False
    rv = _resolved_str_value(analyzer.get_expr_type(e), analyzer)
    return rv is not None and not is_str_view_type(rv)

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
    # non-@native record) -- one fact, so gate and render cannot drift.
    if _record_setitem_value(recv_t, analyzer) is None:
        return False
    return bool(_witness("setitem.record_aug"))


def _container_aug_setitem_ok(
        stmt: TpyAugAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """An augmented container subscript write `c[k] OP= v` -> the
    read-modify-write pair `::tpy::__setitem__(c, k, <read> OP v);` with the
    read the CHECKED `::tpy::__getitem__(c, k)` -- the aug arm never takes
    the bounds-safe operator[] even when the node fact is set
    (`bounds_safe` is ignored here). Condition-for-condition
    with `_scalar_aug_assign_ok`: no in-place
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


def _is_len_call(e: TpyExpr, locals_: dict[str, TpyType], analyzer,
                 pointers: "AbstractSet[str]") -> bool:
    """The eligible `len(name)` / `len(recv.field)` form: the builtin len over a
    single in-scope name -- or a one-level container/str/bytes field off an
    admitted receiver (`len(self.xs)`; the receiver renders as its own
    THIRFieldAccess inside the same call emit) -- of a builtin container type or
    a str-slice value (`::tpy::__len__(x)`, int32 -- the runtime overloads cover
    std::string and std::string_view). The container/str restriction is
    load-bearing, not cosmetic: a container/str is a by-ref/by-value binding
    that emits bare, but a record (or `Optional`) with `__len__` bound to a
    pointer-local would need `(*p)` (the `is_indirect_name` deref) that the
    bare emit misses -- so only the reference-axis containers and str are
    admitted (never pointer-locals). A protocol binding joins them from the
    other side, for the
    same reason: it is always a C++ reference (`const T_x&` / `Base&`), so it
    too can never be a pointer-local, and `len(items)` on a `Measurable` param
    emits the same `::tpy::__len__(items)`. A field arg types at the DECLARED
    field type, so a narrowed
    Optional[container] field (which needs the `(*recv.field)` unwrap) rejects
    at the family check. The receiver may also be a container-element subscript
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
    elif is_property_getter_read(arg):
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
               or _field_over_container_subscript_ok(arg, locals_, analyzer,
                                                     pointers))):
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
    elif isinstance(arg, (TpyCall, TpyMethodCall)):
        # `len(p.bark())` / `len(make_str())`: a str-family CALL result is
        # an rvalue rendered in place -- never a pointer-local, which is
        # what the family restriction below guards. The callee's own gates
        # decide the receiver/args (an unroutable one falls the body back).
        ct = analyzer.get_expr_type(arg)
        return (_resolved_str_value(ct, analyzer) is not None
                or _resolved_bytes_value(ct, analyzer) is not None)
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
        return (_container_ref_alias_elem_subscript(arg, locals_, analyzer,
                                                    pointers)
                or _borrow_elem_subscript_shape(
                    arg, locals_, analyzer,
                    lambda t, a: _container_elem_family(
                        t, a,
                        lambda m: (((_lb := _resolved_bytes_value(m, a))
                                    is not None and is_bytes_type(_lb))
                                   or ((_ls := _resolved_str_value(m, a))
                                       is not None and is_str_type(_ls)))),
                    pointers))
    elif isinstance(arg, TpyNamedExpr):
        # `len(v := h.view())` -- a container walrus arg: the comma form's
        # `*v` result is the same container lvalue a name renders; the
        # walrus arm validates its own target/source classes.
        bt = analyzer.get_expr_type(arg)
    else:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return (_f1_container_ref(t) or is_span(t)
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


def _record_ctor_arg(arg: 'TpyExpr | None', analyzer) -> 'TpyCall | None':
    """A record-CONSTRUCTOR call, keyed on the registry lookup of the callee
    NAME rather than on the resolved fi. Shared by the two `copy()` readers
    that must agree on it: the prvalue arm claims the shape, the
    copy-construct classifier declines it."""
    if not isinstance(arg, TpyCall) or not isinstance(arg.func, TpyName):
        return None
    return (arg if analyzer.registry.get_record(arg.func_name) is not None
            else None)


def copy_construct_form(arg: TpyExpr, pointers: 'AbstractSet[str]',
                        analyzer) -> bool:
    """Whether a `copy()` argument's SOURCE FORM admits the copy-construct
    tail. Every value category does -- an lvalue binds the `const T&` the
    copy ctor takes, a prvalue move-constructs -- except the two that own
    arms whose render is NOT that tail: a pointer-local reads through a
    deref (`call.copy_record_ptr` and the container leg beside it), and a
    record CTOR argument is already the prvalue a copy would build, so it is
    handed back unchanged (`copy_ctor_rvalue_source`)."""
    if isinstance(arg, TpyName) and arg.name in pointers:
        return False
    return _record_ctor_arg(arg, analyzer) is None


def copy_construct_source(init: TpyExpr, analyzer,
                          pointers: 'AbstractSet[str]') -> 'TpyType | None':
    """The `copy(x)` builtin over a source the copy-CONSTRUCT tail renders --
    `{payload.to_cpp()}({source read})`, a record spelling `Point(p)` and a
    container `std::vector<int32_t>(data)`, one tail either way. Returns the
    copied payload's TpyType, or None.

    Admission is the source's VALUE FORM, not its syntax class: an lvalue
    (name, field, subscript, borrow-returning call, lvalue ternary, awaited
    borrow) binds the `const T&` the copy ctor takes, and a prvalue
    move-constructs into the same slot -- the source render carries the
    indirection either way, which is why the tail is type-blind. Family is a
    payload-type fact (`_f1_record` vs `_f1_container_ref`), so the four
    representation-special families whose copy is NOT this tail stay out by
    their own value form: a pointer-repr `Optional` is handed back unwrapped
    (`copy_ptr_optional_peel`), a pointer-repr-element tuple builds per
    element (`call.copy_tuple_storage`), and every view spells its own
    conversion (`call.copy_span` / `call.copy_str`). Only the ptr-variant
    union needs saying out loud, and it says it where the binding is known:
    an assign-narrowed ptr-variant local READS as its member record, so
    `_lower_copy_record` fences it off `ptr_variant_locals` before this
    classifier's record answer can claim it.

    Two source forms are excluded here because they own arms whose render is
    not this tail: a pointer-local reads through a deref
    (`call.copy_record_ptr` and the container leg beside it), and a record
    CTOR argument is already the prvalue a copy would build, so it is handed
    back unchanged (`copy_ctor_rvalue_source`)."""
    arg = copy_call_arg(init, analyzer)
    if arg is None or not copy_construct_form(arg, pointers, analyzer):
        return None
    # The PAYLOAD, not the read: a borrow-returning source reads as `T&`,
    # and the copy constructs a `T` -- an element slot spelled off the read
    # would be an array of references.
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(arg))))
    return au if (_f1_record(au, analyzer) or _f1_container_ref(au)) else None


def copy_ctor_rvalue_source(e: TpyExpr, analyzer) -> 'TpyExpr | None':
    """`copy(T(...))` over a record CONSTRUCTOR argument -- the prvalue arm of
    `copy()`, which returns the constructor's own render UNCHANGED (a
    prvalue is already an rvalue, so there is nothing to copy). Returns the
    inner constructor call, so the consuming sink lowers it exactly as it
    would the bare `T(...)` source; None when this is not that shape.

    The peel is sound only because the render is literally identical -- the
    copy-CONSTRUCT arm (`copy_construct_source`) spells `T(x)` and declines
    this shape through the same reader, so the two cannot both claim it."""
    return _record_ctor_arg(copy_call_arg(e, analyzer), analyzer)


def free_literal_mangled_name(e: TpyCall, fi, analyzer) -> 'str | None':
    """The `f__lit_N` rename of a literal-specialized free callee, or None
    when the plain name applies -- the free-call sibling of
    `method_literal_mangled_cpp`. Mangling applies only when there are
    MULTIPLE overloads (a single Literal-param stub is a plain call) AND the
    callee is not a @native import (a native literal overload's name is its
    resolved native symbol, picked by sema's overload resolution -- no
    `__lit_` mangling). The rename threads into the same-module and imported
    spellings alike; the generic pairing keeps its reject."""
    if not any(isinstance(p.type, LiteralType) for p in fi.params):
        return None
    overloads = analyzer.registry.get_function(e.func_name)
    if (overloads is None or len(overloads) <= 1
            or fi.native_function or fi.native_name):
        return None
    return literal_mangled_name(e.func_name, fi)


def _bare_callee_shadows_import(name: str, analyzer) -> bool:
    """Does a BARE-name callee (a nested def's frame lambda, a `Callable`/`Fn`
    value) also name an import?

    The bare spelling cannot say which of the two the call means, and
    resolving it to the imported/builtin one calls the shadowed callable
    where Python calls the local -- a silent divergence, so both legs reject
    on it."""
    return bool(name in analyzer.imported_names
                or imported_free_callee_cpp(analyzer.ctx.module_attributes,
                                            name))


def _free_callee_kind(e: TpyCall, analyzer, *,
                      generator_ok: bool = False,
                      error_return_ok: bool = False,
                      coro_factory_ok: bool = False,
                      ret_cast_ok: bool = False
                      ) -> 'tuple[str, str] | None':
    """Classify a bare-name free callee into its emit kind + pre-rendered
    payload -- the routing fact consumed by lowering:
    `("plain", cpp)` the same-module ABSOLUTE spelling
    (`free_callee_cpp`, never empty); `("local", "")` the bare
    `name(args)` of a callee no namespace can name (a nested def's
    frame lambda, a `Callable`/`Fn` value); `("imported", cpp)`
    the cross-module qualified spelling (`free_callee_cpp`; lowering
    stamps `THIRCall.callee_cpp`); `("native", native_name)` the
    `::native(args)` arm (C++ @native imports only -- extern-C spells the
    raw unqualified symbol, a different arm); `("template", tmpl)` the
    positional-only @cpp_template expansion (the template
    arm with no substitution context). None = an emit shape the slice does
    not reproduce. Shared by free-call lowering and (through the plain/
    imported wrapper `_plain_free_callee_ok`) the record-rvalue classifier's
    by-value record-returning call face.

    `generator_ok` admits a free GENERATOR callee (set only by the iterable
    position): its factory call spells exactly like a plain/imported call
    (the resumable frame's factory), so only the callee-kind reject
    differs. A GENERIC
    generator callee rides the generic arm's explicit-targ spelling under
    the same flag.

    `coro_factory_ok` is the async sibling (the make_adapter arg position
    and the coro-handle frame write): an async-def CALL is a
    coroutine-FACTORY call spelling exactly like a plain/imported call,
    and a generic factory rides the generic arm the same way."""
    # A call sema typed as its concrete frame is a factory wherever it
    # lands (see builds_named_frame).
    generator_ok = generator_ok or builds_named_frame(e, analyzer)
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
    # kinds: explicit template args are skipped for native imports (C++
    # deduction over natural param types) and substituted into the
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
    # Bespoke arms that fire BEFORE the fi dispatch: the four
    # @builtin_function specials (keyed on
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
    lit_mangled = free_literal_mangled_name(e, fi, analyzer)
    if fi.frame_captures is not None:
        # A closure local (nested def): the bare lambda-variable call --
        # the callee is a C++ LOCAL, so no namespace can name it. Decided
        # BEFORE the registry / import spellings. A
        # closure shadowing an imported OR builtin name is ambiguous here
        # -> reject the shadow.
        if _bare_callee_shadows_import(e.func_name, analyzer):
            note_detail("call.closure_import_shadow")
            return None
        return ("local", "")
    if fi.is_callable_value:
        # A `Callable`/`Fn` VALUE callee (param, local or field): sema
        # resolved the BINDING, so the render is the bare name. Decided
        # BEFORE the name-keyed namespace lookup below, which would find a
        # same-named module function and qualify THAT -- a different callee,
        # whose params would also zip against these args. A value shadowing
        # an IMPORTED name rejects like the closure case above, for the same
        # reason.
        if _bare_callee_shadows_import(e.func_name, analyzer):
            note_detail("call.callable_import_shadow")
            return None
        return ("local", "")
    # A native-FUNCTION `__init__` ctor (`int(str)` -> `tpy::BigInt::from_str`,
    # float/bytes from_str) is receiver-less -- it spells like a native free
    # call, so let it reach the native arm below rather than the method reject.
    # Excluded: exactly the folding shape -- `float("nan"/"inf"/...)` becomes
    # a constexpr numeric_limits constant (not float_from_str), keyed on a
    # raw un-peeled literal in the
    # shared `FLOAT_STR_CONSTANTS` table. Ordinary literals
    # (`int("not_a_number")`) render identically through the native arm.
    would_fold = (
        fi.owning_type_qname == "builtins.float" and len(e.args) == 1
        and isinstance(e.args[0], TpyStrLiteral)
        and e.args[0].value.strip().lower() in emit_prims.FLOAT_STR_CONSTANTS)
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
        # A generic template substitutes its named {T} placeholders
        # through `expand_fi_template`; only a
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
        # C++ @native imports spell the absolute-qualified symbol
        # (`qualify_native_name(native_name or
        # name)`, idempotent on `::`-prefixed stub names); a user-module
        # `@native def` carries only the NATIVE linkage (no stub flags) and
        # spells the same. @native(binding="C") emits the RAW unqualified
        # symbol (the extern "C" re-declaration is namespace-scoped, a `::`
        # would miss it). A declared cpp_return_type (the call wraps in
        # the narrowing static_cast) routes only where the caller
        # renders the cast (`ret_cast_ok` -- the free-call lowering arm
        # composes the static_cast template); @export rejects.
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
        # the `func_info` pick, which the arity of the template-arg
        # list depends on -- and still spells the plain name
        # (`apply<int32_t>(f1, 5)`).
        fis = analyzer.registry.get_function(e.func_name)
        if fis is None:
            note_detail("call.callee_kind.generic")
            return None
        if len(fis) > 1:
            # The `is_literal_mangled` condition: a Literal-param stub
            # in a group renames the callee. No corpus case selects one from
            # a GENERIC group, so the rename condition is checked rather
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
            # The tail's conditional-qualification refinement: a
            # LOCAL generic shadowing an imported/builtin name spells its OWN
            # namespace with the explicit targs
            # (`::tpyapp::main::enumerate<std::string>(words)`); only an fi
            # genuinely living in the imported module rejects.
            _gsrc_mod = analyzer.imported_names[e.func_name][0]
            if (fi.qualified_name or "").startswith(_gsrc_mod + "."):
                note_detail("call.imported_symbol")
                return None
        gcpp = free_callee_cpp(analyzer.ctx.module_attributes,
                               analyzer.ctx.module_name,
                               analyzer.ctx.cpp_module_name, e.func_name, fi)
        if gcpp is None:
            note_detail("call.callee_kind.generic")
            return None
        return ("generic", gcpp)
    if icc is not None:
        return ("imported", icc)
    if e.func_name in analyzer.imported_names:
        # The conditional-qualification arm: qualify ONLY when the
        # resolved fi actually lives in the imported source module (inert
        # today -- every such builtin is @native/@cpp_template, handled
        # above) -> reject. A LOCAL function shadowing an imported name
        # resolves to the local module and spells THAT module's namespace
        # (`from time import time` + `def time(): ...` ->
        # `::tpyapp::main::time()`), the plain kind.
        _src_mod = analyzer.imported_names[e.func_name][0]
        if (fi.qualified_name or "").startswith(_src_mod + "."):
            note_detail("call.imported_symbol")
            return None
    cpp = free_callee_cpp(analyzer.ctx.module_attributes,
                          analyzer.ctx.module_name,
                          analyzer.ctx.cpp_module_name, e.func_name, fi,
                          lit_mangled)
    if cpp is None:
        # No namespace names this callee. Both shapes that legitimately spell
        # the bare name -- a nested def's frame lambda and a `Callable`/`Fn`
        # VALUE -- returned above, so anything reaching here is a
        # registration-fact drift; a bare spelling would be ADL-visible, so it
        # rejects rather than falling back.
        note_detail("call.callee_unnamed")
        return None
    return ("plain", cpp)


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
    """The plain/local/imported/generic subset of `_free_callee_kind` -- the
    callee-shape head of the by-value record-returning call face
    (native/template record returns reject there). A generic callee's
    record rvalue rides the same spelling with the explicit targs appended
    (`BoxC cloned = ::tpyapp::main::clone_it<BoxC>(box);`)."""
    kind = _free_callee_kind(e, analyzer)
    return kind is not None and kind[0] in ("plain", "local", "imported",
                                            "generic")


def _record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """Shallow shape of a by-value free call returning a record or a
    container -- the one-object rvalue the hoisting rows name a temp for.

    Argument subtrees are lowered by their own call arms.
    """
    if not isinstance(e, TpyCall):
        return False
    if not (record_like(analyzer.get_expr_type(e), analyzer)
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
    carry a default. Each default rides the emitted C++ signature,
    and only the provided args are passed (the arg
    loop zip-truncates), so the truncated call still matches it. A
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
    """Whether a str-literal arg to this call takes the overloaded
    pin (`param_view_t("...")`): a MULTI-overload, non-generic callee, plus
    the per-arg firing test (the
    pin fires only when the str literal's param slot renders `str`/`StrView`).
    A str literal into any OTHER slot -- a `char`, a `Literal[...]` mode
    selector, an owned `String` -- renders through its own arm, so only a
    str/StrView slot takes the pin. Used to keep
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
    per-arg half of `_strlit_overload_pin_fires`: the overloaded-call
    condition (a multi-overload, non-generic callee) plus
    the per-arg str/StrView slot test."""
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
    """`_strlit_pin_slot` over a METHOD's overload set -- counted with
    `record_info.get_method_overloads` (own methods only, no
    parents)."""
    if fi is None or fi.cpp_template is not None or fi.native_function \
            or fi.native_name:
        # Builtin / @native methods emit through the native/template arm,
        # which never applies the pin -- it is the USER-record arm's.
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
    `::tpy::symbol(args)` -- the same render the stored `__ctx_N`
    manager gets. The plain/imported/ctor manager sources ride
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
    lands by value (an rvalue return, no `Own`/borrow lift) -- the plain
    value decl (`is_rvalue_source`, no indirection)."""
    if not isinstance(e, TpyCall):
        return False
    ret = analyzer.get_expr_type(e)
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    return (record_like(t, analyzer) and is_rvalue_source(analyzer, e)
            and _native_ctx_manager_ok(e, analyzer))


def _native_own_record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """`_native_record_rvalue_call_shape` restricted to a callee whose
    DECLARED return spells the ownership transfer (`-> Own[V]`).

    The restriction is what lets the shape reach an `Own[T]` (`T&&`) sink:
    `is_rvalue_source` is True for a BORROWING callee too, and binding a
    `T&` result into a `T&&` slot is ill-formed at a reference-type
    instantiation, so the value category alone cannot carry the admission.
    A bare `-> V` free native leaves its C++ return convention unspelled and
    a `-> T` one renders `val_or_ref_t<T>`; both stay out. One predicate for
    the arg gate and the result-use gate, so the two cannot drift apart."""
    if not isinstance(e, TpyCall) or e.resolved_function_info is None:
        return False
    rt = unwrap_send_sync(e.resolved_function_info.return_type)
    return (isinstance(rt, OwnType)
            and _native_record_rvalue_call_shape(e, analyzer))


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
    if not (record_like(analyzer.get_expr_type(e), analyzer)
            and is_rvalue_source(analyzer, e)):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if not _call_arity_ok(e, fi):
        return False
    kind = _free_callee_kind(e, analyzer, error_return_ok=True)
    return (kind is not None and kind[0] in ("plain", "local", "imported")
            and _witness("call.er_record_rvalue"))


def _template_record_rvalue_call_shape(e: TpyExpr, analyzer) -> bool:
    """A @cpp_template free call returning a by-value F1 record
    (`make_default[Point]()` -> `Point p = Point{};`, `unsafe_load(p, 0)`
    -> `Point loaded = p[0];`): the TEMPLATE residue of
    `_record_rvalue_call_shape` -- the expanded positional-only template
    renders bare in the STORAGE slot; args gate at their own rows."""
    if not isinstance(e, TpyCall):
        return False
    if not (record_like(analyzer.get_expr_type(e), analyzer)
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
    the plain copy. The ELEMENT family is deliberately not
    checked: it decides the local's downstream reads, which gate themselves --
    the decl render only spells the slot. The caller pairs this with the
    reassigned / hoisted / move_through guards (a rebound non-value local takes
    the pointer form) and the callee's own arms re-validate at lowering."""
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        return False
    ret = analyzer.get_expr_type(e)
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    shape_ok = (record_like(t, analyzer)
                or _resolved_str_value(t, analyzer) is not None
                or _resolved_bytes_value(t, analyzer) is not None)
    return shape_ok and is_rvalue_source(analyzer, e)


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
    if (not isinstance(a, (TpyCall, TpyMethodCall))
            or a.resolved_function_info is None):
        return False
    fi = a.resolved_function_info
    if fi.is_generator:
        if isinstance(a, TpyMethodCall):
            # A MEMBER generator factory (`sum(lim.first(xs, 2))`): the
            # method-call lowering gates which spellings route.
            return True
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
        # the bare render is whatever the monomorphization resolves it to.
        # A SUBSTITUTED slot rides the exact-match rows below.
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
    if _f1_container_ref(t):
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
    if isinstance(t, TupleType):
        # A VALUE-tuple field passed WHOLE (`repr(self.pair)` at
        # `tuple[int32, str]` -> `::tpy::tuple_to_str(this->pair)`): borrow
        # and storage forms coincide, so the bare member read binds the slot
        # directly. The pointer-repr (F3) sibling lifts via the
        # borrow-tuple FIELD row above instead.
        return bool(_value_tuple(t, analyzer) is not None
                    and _witness("arg.native_value_tuple_field"))
    return _f1_record(t, analyzer)


def _native_own_scalar_lvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer) -> bool:
    """A value-scalar NAME into an `Own[..]` slot of a native/template
    callee (`unsafe_init(self._ptr, value)` at `Own[T]`): the
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
    """The native / native_c / @cpp_template free-callee family's arg rows.
    Rows: `_NATIVE_ARG_SINK`."""
    return arg_ok(_NATIVE_ARG_SINK, a, ptype, locals_, analyzer,
                  param_names=frozenset(), narrowed=frozenset(),
                  temps_ok=False, storage_tuple_locals=storage_tuple_locals)


def _readonly_container_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> 'TpyType | None':
    """An EMPTY container rvalue into a `readonly[list/dict/set]` slot: the
    const-ref slot binds the rvalue INLINE -- `f(std::vector<int32_t>())`
    for the hint-typed `list()` instantiation, `f(std::vector<int32_t>{})`
    for the `[]` literal (the typed empty spelling). Returns the container
    slot or None."""
    if not isinstance(ptype, ReadonlyType):
        return None
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if not (is_list(inner) or is_dict(inner) or is_set(inner)):
        return None
    # EMPTY only. A non-empty literal would bind inline too, but whether that
    # is safe depends on the callee not lending the parameter back, and that
    # fact is not recorded for every callee
    # (BUGS.md#readonly-container-literal-arg-rejected).
    if isinstance(a, TpyArrayLiteral) and not a.elements and is_list(inner):
        return inner
    if isinstance(a, TpySetLiteral) and not a.elements and is_set(inner):
        return inner
    if isinstance(a, TpyDictLiteral) and not a.keys and is_dict(inner):
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
    literal spells its own sema type.

    The returned TupleType is the STORAGE spelling and does NOT decide the
    per-element capture: with no target each element's slot mode comes from
    the ELEMENT EXPRESSION (a non-value simple lvalue takes a
    `T*` ref slot, `hash((1, b))` -> `std::tuple<int32_t, Box*>{1, &(b)}`).
    `_proto_tuple_elem_borrow` is that fork; keying anything on this type
    alone moves an element that must be aliased.

    Returns the storage TupleType, or None. The tuple-literal lowerings own
    the per-element admission from there, so an element shape neither can
    render still rejects."""
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


def _proto_tuple_elem_borrow(a: 'TpyTupleLiteral', slot: 'TupleType',
                             declared: dict, analyzer) -> 'bool | None':
    """Which tuple-literal render a native protocol slot wants, with no
    target threaded: True for the BORROW spelling
    (`std::tuple<Box*, ..>{&(b), ..}`, at least one non-value SIMPLE LVALUE
    element), False for the all-value/rvalue STORAGE spelling. None means
    neither -- a MIXED lvalue+rvalue literal takes a
    per-element mixed slot tuple (`std::tuple<Box*, Box>`) that no
    render builds yet.

    `Own[T]` elements reject: the slot mode reads the element's own
    resolved type, and `get_expr_type` on an `Own[T]` NAME has already
    dropped the Own -- so the declared type is the only place the fact
    survives, and the storage render there is itself under review."""
    if a.elem_capture:
        # A sema capture annotation OVERRIDES the per-element
        # derivation, and nothing below reads it. No shape reaching this slot
        # carries one today, so this has no witness -- it is here so a future
        # annotated shape rejects instead of taking a render chosen from the
        # element expressions the annotation was meant to overrule.
        return None
    lvalues = 0
    unmirrored = False
    for i, et in enumerate(slot.element_types):
        el = a.elements[i]
        rt = declared.get(el.name) if isinstance(el, TpyName) else None
        if rt is None:
            rt = analyzer.get_expr_type(el)
        base = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        rbase = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
        if isinstance(rbase, OwnType) or isinstance(rt, OwnType):
            return None
        if base.is_value_type() or isinstance(base, OwnType):
            # An owned-view VALUE element renders an owning conversion
            # (`std::string(s)`) in the borrow spelling, not the bare
            # storage one.
            if _resolved_str_value(base, analyzer) is not None:
                unmirrored = True
            continue
        if emit_prims.is_simple_lvalue(el):
            lvalues += 1
        else:
            unmirrored = True
    if lvalues and unmirrored:
        return None
    return bool(lvalues)


def _native_optptr_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """A ptr-repr Optional[F1-record] NAME passed WHOLE at the native
    callee's SAME-optional slot (`repr(opt_none)` at `ReprOnly | None` ->
    the bare `T*`; the runtime overload prints None). Slot-equality-keyed:
    the callee's resolved param IS the arg's own optional type (repr is
    generic over the arg), so a mismatched slot has no constructible
    witness. Narrowed reads are retyped by sema and ride the record rows
    with the `(*name)` deref; no runtime-check marker exists on a bare
    NAME node, so there is nothing further to guard."""
    if not isinstance(a, TpyName) or not isinstance(ptype, TpyType):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    no = _optional_ptr_borrow(at, analyzer)
    if no is None or not record_like(unwrap_readonly(no.inner), analyzer):
        return False
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    return slot == at and bool(_witness("arg.native_protocol_optptr"))

def _native_protocol_value_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """The value-typed slice of a native callee's protocol slot: the arg's
    resolved type is an eligible scalar / char / str value, whose bare
    render is position-independent (the ordinary lowering tail carries it).
    Gate row only -- an arg SHAPE the tail cannot route still falls the
    body back."""
    proto = _protocol_arg_slot(ptype)
    if proto is None or is_dyn_protocol(proto):
        return False
    # Unresolved scalar literals render their bare token (`__hash__(42)`,
    # `__hash__(3.14)`) -- the `_ru_elem_ok` leaf bounds (strict on both
    # ends BY DESIGN: INT32_MIN itself rejects, like _ru_elem_ok).
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
                     # An enum member renders bare (`__hash__(Color::Red)`)
                     # -- the enum-member arm carries the spelling.
                     or _eligible_enum(atu, analyzer) is not None
                     or _resolved_str_value(atu, analyzer) is not None
                     # The bytes family rides the same bare render
                     # (`__hash__(bytes_literal_owned(..))` for a literal,
                     # the bare view for a BytesView name).
                     or _resolved_bytes_value(atu, analyzer) is not None)
                and _witness("arg.native_protocol_value"))


def _native_protocol_open_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   analyzer) -> bool:
    """A CALL rvalue whose result is a BARE TYPE PARAM at a native callee's
    protocol slot (`hash(self.get())` -> `::tpy::__hash__(this->get())`).

    An open-T result renders by name, so the slot
    monomorphizes to T and the call binds it inline -- no adapter wrap and
    no temp, the call-shaped sibling of the bare-name conformer row. Form is
    immaterial for the same reason: `T` has no borrow/storage duality until
    it binds, so a borrow- and a value-returning callee render alike here.
    The inner call's own lowering re-validates callee and args."""
    proto = _protocol_arg_slot(ptype)
    if proto is None or is_dyn_protocol(proto):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    return bool(_tparam_value(analyzer.get_expr_type(a))
                and _witness("arg.native_protocol_open_call"))


def _native_union_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A union-typed NAME at a native/template callee's SAME-union slot
    (`repr(a)` -> `::tpy::repr_of(a)` -- the generic Representable slot
    substitutes to the arg's own union): renders bare, the runtime
    variant overloads visit the active alternative whatever the flavor
    (value or pointer variant; wide member class). A narrowed occurrence
    reads its member alias identically (the name arm's
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
        # A UNION-fact narrowed occurrence (`repr(tz)` under `tz is not
        # None` on a 3-member union): the slot substitutes to the SMALLER
        # occurrence union, but the C++ binding is the full variant and
        # the render is the same bare name -- a union fact installs no
        # alias. Members must nest, declared stays the
        # render-eligibility key.
        if not (isinstance(u, UnionType)
                and all(any(m == dm for dm in u.members)
                        for m in pt.members)):
            return False
    return bool((_eligible_value_union(u) is not None
                 or _eligible_ptr_union_wide(u, analyzer) is not None)
                and _witness("arg.native_union_name"))

def _lambda_routable(a: TpyExpr, analyzer, *,
                     self_capturable: bool = False) -> bool:
    a = lambda_of(a)
    if not isinstance(a, TpyLambda):
        return False
    reason = _lambda_reject_reason(a, analyzer,
                                    self_capturable=self_capturable)
    return reason is None or note_detail(reason, loc=a.loc)


def _lambda_reject_reason(a: TpyLambda, analyzer, *,
                          self_capturable: bool = False) -> str | None:
    """The lambda-expression shapes `_lower_lambda` renders:
    a closure with a non-void, non-pointer-tuple return and param types in the
    families the body emit renders without seeding (value scalars / char /
    enums / str / bytes / F1-record) -- the `(params) -> ret {
    return body; }` form, in its by-reference (Fn template), by-value
    (Callable/std::function), and readonly-param (key-function -- const
    `_callable_param_cpp` spelling; only a `RefType` result takes the
    `to_cpp_return_const` borrow) capture modes. A VOID body routes IFF it is a bare builtin-print call (the
    statement-body closure `{ std::cout << ...; }`); other void bodies
    reject. The single source of truth for both the arg-admission
    gate and the lowering arm (they must agree, else the gate admits a
    shape lowering then rejects)."""
    # A captured `self` needs a receiver HANDLE the closure can copy --
    # admitted only where the caller confirmed the enclosing body holds one
    # (a plain method or a frame's `__self`).
    if "self" in a.captured_names and not self_capturable:
        return "lambda.self_capture"
    rt = a.inferred_return_type
    if rt is None:
        return "lambda.unresolved_return"
    if is_void_like_type(rt):
        b = a.body
        # A param/capture named `print` would shadow the builtin; kwargs
        # (sep/end/file) and star-unpack shapes reject.
        if not (isinstance(b, TpyCall) and isinstance(b.func, TpyName)
                and b.func_name == "print"
                and not b.kwargs and b.double_star_unpack is None
                and "print" not in a.param_names
                and "print" not in a.captured_names
                and _is_builtin_print(b, {}, analyzer)):
            return "lambda.void_body"
    else:
        ru = unwrap_readonly(rt)
        if isinstance(ru, TupleType) and ru.has_pointer_repr_element():
            # A pointer-repr tuple return spells the borrow form
            # (`-> std::tuple<std::string, Point*>`, to_cpp_return) and
            # routes ONLY when the body is a GENERIC call whose tuple
            # matches modulo Ref-wrapping: the monomorphized callee's
            # val_or_ptr_t return IS the borrow tuple, so the direct
            # return needs no per-element form conversion. A tuple
            # LITERAL body (element lifts) or a concrete-callee body
            # (storage-form tuple) rejects.
            if not isinstance(a.body, (TpyCall, TpyMethodCall)):
                return "lambda.borrow_tuple_body"
            fi = a.body.resolved_function_info
            if fi is None or not fi.type_params:
                return "lambda.borrow_tuple_callee"
            bt = analyzer.get_expr_type(a.body)
            bt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
                  if bt is not None else None)
            if not (isinstance(bt, TupleType)
                    and len(bt.element_types) == len(ru.element_types)
                    and all(unwrap_readonly(unwrap_ref_type(be))
                            == unwrap_readonly(unwrap_ref_type(re))
                            for be, re in zip(bt.element_types,
                                              ru.element_types))):
                return "lambda.borrow_tuple_result"
    for pt in a.inferred_param_types:
        if not isinstance(pt, TpyType):
            return "lambda.unresolved_parameter"
        pu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
        if not (_eligible_scalar(pu) or _eligible_char(pu)
                or _eligible_enum(pu, analyzer) is not None
                or _resolved_str_value(pu, analyzer) is not None
                or _resolved_bytes_value(pu, analyzer) is not None
                or record_like(pu, analyzer)
                # A reference-container param spells its `C&` through the
                # same to_cpp_param helper (`bytearray`'s const-by-default
                # form included); the body's own reads gate their renders (an
                # unroutable body rejects whole).
                or _f1_container_ref(pu)
                # A tuple param spells through the same helper (open-T
                # elements as `val_or_ptr_t<T>`); pointer-repr elements
                # reject -- that spelling is unverified here.
                or (isinstance(pu, TupleType)
                    and not pu.has_pointer_repr_element())):
            return "lambda.parameter_type"
    return None

def _func_ref_routable(a: TpyExpr, analyzer) -> bool:
    """A named function used as a value (`apply(double, ...)` ->
    `apply(double_, ...)`; `apply(mod.f, ...)` -> the qualified spelling;
    `apply(identity, 42)` -> the generic `identity<int32_t>` -- the
    template-args suffix rides `type_to_cpp`, so any resolved
    targ spells identically): the plain and generic function-ref renders.
    The native / async-coro-factory wrappers reject. Shared
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
    the name renders unchanged, the std::function converting implicitly. A
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
    shape; the name renders unchanged (no move/copy temp)."""
    if not isinstance(a, TpyName) or a.is_function_ref:
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if not isinstance(slot, CallableType):
        return False
    at = locals_.get(a.name)
    if at is None:
        at = analyzer.get_expr_type(a)
    return record_like(at, analyzer)

def _plain_call_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                       locals_: dict[str, TpyType], analyzer, *,
                       temps_ok: bool,
                       narrowed: 'set[str] | frozenset[str]',
                       param_names: 'set[str] | frozenset[str]' = frozenset(),
                       self_capturable: bool = False,
                       index: int = -1,
                       overload=None,
                       movable_locals: 'set[str] | frozenset[str]' = frozenset(),
                       func_name: 'str | None' = None) -> bool:
    """The plain (non-native, non-marker) free-callee family's arg rows --
    the reference ladder the other families were copied from.
    Rows: `_PLAIN_ARG_SINK`.

    `overload` is the callee the RETENTION question may name -- the same one
    the render side hands `arg_lend_ok`, so the in-place cell and the render
    cannot answer it differently."""
    return arg_ok(_PLAIN_ARG_SINK, a, ptype, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed,
                  temps_ok=temps_ok, self_capturable=self_capturable,
                  index=index, overload=overload,
                  movable_locals=movable_locals, func_name=func_name)


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
        # An Own-DECLARED return is a fresh by-value wrapper: it
        # hoists the argtemp (`Tree<T> __tmp_N = make_leaf();`), never
        # binds the prvalue inline -- the argtemp.ru_wrapper_call row
        # owns the genrec flavor; the non-generic flavor rejects.
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
    if atu is None:
        return None
    # Equal modulo per-element ownership: a storage name whose binding marks
    # a fresh element `Own` (a tuple global that owns its literal's record)
    # holds the slot's borrow element by value, which is what the lift
    # points at -- the marking is the storage fact, not a different slot.
    return slot if collapse_tuple_own_elements(atu) == slot else None


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


def _mixed_own_tuple_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType],
                              own_borrow_tuple_locals: 'AbstractSet[str]',
                              analyzer) -> 'TupleType | None':
    """A MIXED-own-tuple LOCAL at a borrow-tuple param slot (`take(p)` ->
    `::tpy::tuple_to_pointer<std::tuple<const Box*, const Box*>>(p)`): the
    owned half is held by value, so the whole-tuple storage->borrow lift is
    still owed -- `is_storage_form_source` stays True for the mixed render.
    Own-collapsed slot equality; keyed on the mixed binding set."""
    slot = _borrow_tuple_slot(ptype, analyzer)
    if slot is None:
        return None
    if not (isinstance(a, TpyName) and a.name in own_borrow_tuple_locals):
        return None
    bt = locals_.get(a.name)
    btu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
           if bt is not None else None)
    if not isinstance(btu, TupleType):
        return None
    return slot if _own_stripped_tuple_eq(btu, slot) else None


def _borrow_tuple_storage_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                                   locals_: dict[str, TpyType],
                                   storage_tuple_locals: 'AbstractSet[str]',
                                   analyzer) -> 'TupleType | None':
    """The NAME sibling of the two lift rows above: a local ALREADY reading
    storage form (a loop var over a storage container, a local bound from
    another storage source) at a borrow-tuple param slot
    (`consume(it)` -> `::tpy::tuple_to_pointer<std::tuple<const T*, const
    T*>>(it)`).

    Keyed on `storage_tuple_locals` membership -- the positive fact
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
    Sequence[int]`). The protocol-arg render claims it BEFORE the Optional-ptr
    and union-lift arms, and its required-union branch renders the plain
    value -- so this slot must never reach the
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
    return bool((record_like(at, analyzer) or is_span(at))
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
    render the same. The absence-keyed predecessor DID need a lane
    guard here; the positive key subsumes it, and the guard was suppressing
    a body that routes correctly."""
    ok = _borrow_tuple_arg(
        a, ptype, analyzer,
        shape_ok=lambda x: (isinstance(x, TpyName)
                            and x.name in borrow_params),
        arg_type=lambda x: locals_.get(x.name)) is not None
    return ok and _witness("arg.btuple_name")


def _union_elem_tuple_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                               locals_: dict[str, TpyType],
                               param_names: 'AbstractSet[str]',
                               analyzer) -> bool:
    """A UNION-element tuple PARAM name at the same union-element tuple slot
    (`read_second(pair)`): such a param is bound in borrow form
    (`const std::tuple<::tpy::Union<const Cat*, const Dog*>, int32_t>&`) --
    there is no storage-form binding of it to distinguish, because a union
    element never enters `borrow_form_tuple_locals`/`storage_tuple_locals`
    (it has no pointer-repr element) -- so the arg renders BARE. The family
    owns its own row rather than joining `_borrow_tuple_slot`: its
    storage->borrow conversion is the WHOLE-tuple `tuple_value_to_borrow`,
    not the element-wise `tuple_to_pointer` every row behind that slot
    emits."""
    slot = _union_elem_tuple(ptype, analyzer)
    if slot is None:
        return False
    if not (isinstance(a, TpyName) and a.name in param_names):
        return False
    at = locals_.get(a.name)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return bool(atu == slot and _witness("call.union_elem_tuple_arg"))


def _record_getitem_rvalue_arg(a: TpyExpr, analyzer) -> bool:
    """A user-record `recv[index]` returning an F1 record: the raw
    operator[] read (a `T&` borrow for the default record return, a
    prvalue for an explicit value return) binds the protocol arg's
    `auto __tmp_N = container[i];` temp -- `auto` deduces the value
    either way, so the temp owns a copy."""
    if not isinstance(a, TpySubscript) or isinstance(a.index, TpySlice):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if not record_like(atu, analyzer):
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
    """A record- or container-element subscript at a matching ref slot
    (`add_a(a.bs[0], ..)` -> `add_a(::tpy::__getitem__(a.bs, 0), ..)`;
    `push(g["b"], 7)` -> `push(::tpy::__getitem__(g, "b"), 7)`): the
    checked element read is an lvalue into the container's storage,
    binding the ref param inline like the borrow-returning call row."""
    if not isinstance(a, TpySubscript) or isinstance(a.index, TpySlice):
        return False
    if getattr(a, "needs_optional_runtime_check", False):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    wsl = _eligible_wrapper_union(slot, analyzer)
    if not (record_like(slot, analyzer)
            # ... or a WRAPPER-union element at a same-wrapper borrow slot
            # (`depth(zs[1])` on `zs: list[Tree]` -- the checked element
            # lvalue binds `const Tree&` bare, like the record flavor).
            or wsl is not None):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if wsl is not None:
        return _resolve_plain_alias(atu, analyzer) == wsl
    return atu == slot


def _tuple_literal_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    if not isinstance(a, TpyTupleLiteral):
        return False
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if isinstance(ptype, TpyType) else None)
    if isinstance(slot, OwnType):
        # An `Own[value-tuple]` slot takes the same spelled value render as
        # the bare tuple slot (the tuple-literal lowering arm's unwrap); an
        # `Own[pointer-repr tuple]` slot takes the CONSUMING
        # `tuple_to_storage_move` ladder, whose per-element source rules
        # (last-use lvalue / fresh rvalue / copy() / None) raise inside the
        # builder -- a bad element falls the body back whole, never a
        # divergent render.
        inner = unwrap_readonly(slot.wrapped)
        if isinstance(inner, TupleType):
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
    pass-through set -- names WHICH plain-loop-only arg row it needs, so the
    reject names the shape (optptr / union /
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
                          narrowed: 'set[str] | frozenset[str]',
                          param_names: 'AbstractSet[str]' = frozenset(),
                          storage_tuple_locals: 'AbstractSet[str]'
                          = frozenset(),
                          overload=None) -> bool:
    """The generic free-callee family's arg rows, decided against the
    SUBSTITUTED slot -- which is why the sink is handed `resolved` as its
    slot and keeps the unsubstituted one as `open_ptype`.
    Rows: `_GENERIC_PLAIN_ARG_SINK`, prologue `_pre_generic_slot_family`.
    `overload` is the resolved stub, which the prologue reads for the one
    verdict that depends on the CALLEE rather than the slot."""
    resolved = (substitute_type_params_simple(ptype, subst)
                if ptype is not None else None)
    return arg_ok(_GENERIC_PLAIN_ARG_SINK, a, resolved, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed,
                  temps_ok=temps_ok,
                  storage_tuple_locals=storage_tuple_locals,
                  open_ptype=ptype, overload=overload)


def _container_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                           analyzer, *, frame_capturing: bool = False) -> bool:
    """A LIST literal into a same-family NON-mutated list slot at a CTOR call
    (`Numbers([1, 2, 3])` -> `Numbers({1, 2, 3});`): the bare
    brace-init renders in place (scalar, str, and record-rvalue
    elements), which `_lower_expr`'s container-literal arm builds. CTOR
    args only -- a FREE-call literal arg hoists the ref-param `__tmp_N` temp
    instead (const AND mutated slots alike). Dict / set
    literals take the SPELLED render (`::tpy::ordered_map<...>({{..}})`)
    rather than the list arm's bare brace, but the ctor position emits them
    INLINE like the stub-method twin (`_container_literal_method_arg`) --
    `Config(::tpy::ordered_map<std::string, ::tpy::Any>({{..}}))`, no
    temp hoist -- so they ride this arm too. Array literals still do not.
    A MUTATED ctor slot rejects (a prvalue into a
    non-const `T&`). Element shapes are pre-checked so admission tracks
    lowerability; the make_vector element path is rejected at lowering (its
    ctor-arg render is unverified). A DECLARED-readonly slot binds the
    literal INLINE (`take_ro({1, 2, 3})`),
    so it must not ride the temp arms -- only the empty rvalue is admitted
    (`_readonly_container_rvalue_arg`); non-empty rejects. EXCEPT for a
    FRAME-CAPTURING callee (`frame_capturing`): there the statement-scoped
    inline `const T&` bind would dangle, so the readonly
    slot's literal hoists like a mutable one (`_record_rvalue_temp_slot`'s
    rule)."""
    if (not frame_capturing and isinstance(ptype, TpyType) and isinstance(
            unwrap_ref_type(unwrap_send_sync(ptype)), ReadonlyType)):
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
    `::tpy::dict_update(d, ::tpy::ordered_map<...>({{..}}))`): the arg
    loop threads the slot type and renders the spelled container in
    place, which `_lower_expr`'s container-literal arm builds. Unlike the
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
    call: the ref-param `__tmp_N` temp hoists with the spelled
    container init (`::tpy::ordered_map<...> __tmp_N = ::tpy::ordered_map<...>
    ({{..}});` -- the literal renders target-typed, then the
    ref-param cascade hoists it). Flush positions only (the arm is
    temps_ok-gated); the list-literal sibling rides `_container_literal_arg`.
    DECLARED-readonly slots bind inline -- excluded like the
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
    inner = _own_container_slot(ptype, analyzer)
    if inner is None:
        return False
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

def _own_container_slot(ptype: 'TpyType | None',
                        analyzer) -> 'TpyType | None':
    """The peeled payload of an `Own[...]` slot, or None when the slot is not
    one -- the peel both Own container twins (literal, comprehension) start
    from. A stub slot off a literal-seeded receiver can still be PENDING
    (`rows.append([9, 9])` -- Own[PendingList], which resolves to the
    read-only DEMOTED Array); resolve first, the renders agree for the
    list and the demoted-Array spellings."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if not isinstance(pt, OwnType):
        return None
    inner = unwrap_readonly(unwrap_send_sync(pt.wrapped))
    return _resolve_literal_seeded(inner, analyzer)


def _own_container_comp_slot(ptype: 'TpyType | None',
                             analyzer) -> 'TpyType | None':
    """The container an `Own[container]` slot moves a comprehension into,
    or None. Shared by the admission and the render so the two peel
    identically; a raw `Own[T]` stub slot is not one (the literal twin
    falls back to the literal's own container there, a comprehension has
    no such row)."""
    inner = _own_container_slot(ptype, analyzer)
    if inner is None or _is_type_param_slot(inner):
        return None
    return inner


def _own_container_comp_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """A comprehension into an `Own[container]` slot (`Keep([f(x) for x in
    xs])`): the stmt-expr is a prvalue of exactly the slot's container, so
    it moves into the by-value slot inline -- the comprehension twin of
    `_own_container_literal_arg`. A raw `Own[T]` stub slot is not admitted:
    the target type there is the comprehension's own, a different row."""
    inner = _own_container_comp_slot(ptype, analyzer)
    if inner is None:
        return False
    if isinstance(a, TpyListComprehension):
        return is_list(inner)
    if isinstance(a, TpySetComprehension):
        return is_set(inner)
    if isinstance(a, TpyDictComprehension):
        return is_dict(inner)
    return False


def _own_container_instantiation_arg(a: TpyExpr, ptype: 'TpyType | None',
                                     analyzer) -> bool:
    """An EMPTY container INSTANTIATION into an `Own[container]` ctor slot
    (the @dataclass default_factory fill: `Foo(x=int32(1))` ->
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

def _own_container_construct_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 analyzer) -> bool:
    """A one-source container INSTANTIATION into an `Own[container]` ctor slot
    (`_WalkEmit(cur, list(names), files)` -> `::tpy::construct<
    std::vector<std::string>>(names)`): the construct template IS the whole
    render and needs no temp, so the owning slot binds the prvalue directly.
    The EMPTY sibling above spells the default ctor instead, and the source's
    own admission stays with the instantiation arm."""
    if not (isinstance(a, TpyCall) and a.call_type is not None
            and len(a.args) == 1 and not a.kwargs
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
    return atu == inner and _witness("ctor.own_container_construct")

def _native_iterable_literal_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 analyzer) -> bool:
    """A container LITERAL into a NATIVE builtin's structural `Iterable[T]` /
    `Sequence[T]` slot (`bytes([300])` / `all([True, False])` ->
    `::tpy::bytes_from_int_iterable(std::array<int32_t, 1>{300})`): the
    literal's RESOLVED container renders inline, bare into the template slot
    (array aggregates incl. BigInt / str / expr elements, and
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
    pb = _protocol_binding(ptype)
    # Sized joins the iterable pair: `len([...comp...])` renders the same
    # inline stmt-expr into `::tpy::__len__` (the slot only sizes it).
    if pb is None or pb.name not in ("Iterable", "Sequence", "Sized"):
        return None
    return _comp_own_container(a, analyzer)


def _comp_own_container(a: TpyExpr, analyzer) -> 'TpyType | None':
    """A comprehension's OWN resolved container -- the render target every
    inline (no-`__tmp_N`) comprehension arg takes, whatever the slot's
    spelling. Returns None for anything that is not a container
    comprehension."""
    if not isinstance(a, (TpyListComprehension, TpySetComprehension,
                          TpyDictComprehension)):
        return None
    at = analyzer.get_expr_type(a)
    if at is None:
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    # A comp whose container is still PENDING resolves through the shared
    # record first (`sum([f() for ...])` -- the element decided the family
    # but the node kept the pending shell).
    at = resolve_pending_container(at, analyzer) or at
    if is_array(at):
        # The fixed-bound Array optimization is a DECL-position fact; at a
        # native Iterable slot the plain vector flavor renders
        # (`std::vector<T> __result; ... reserve(stop)`), so the render
        # target is the LIST form of the element.
        return make_list(unwrap_readonly(at.type_args[0]))
    return at if (is_list(at) or is_set(at) or is_dict(at)) else None


def _stub_comp_target(a: TpyExpr, ptype: 'TpyType | None',
                      analyzer) -> 'TpyType | None':
    """The render target for a comprehension at a builtin STUB method's arg
    slot -- structural (`primes.extend([i for i in xs])`) or concrete
    (`d.update({k: k for k in ks})`) alike. Always the comprehension's own
    container, never the slot: a stub's element slot spells the insert's
    ownership (`dict[K, Own[V]]`), which is a param-passing fact and not a
    container the comprehension can build."""
    if _protocol_binding(ptype) is not None:
        return _native_iterable_comp_arg(a, ptype, analyzer)
    if not _container_comp_arg(a, ptype):
        return None
    own = _comp_own_container(a, analyzer)
    if own is None:
        return None
    # sema stamps the comprehension with the SLOT's spelling, so a stub's
    # `dict[K, Own[V]]` / `list[Own[T]]` insert slot reaches here as the
    # comprehension's own type. Own on an element is the insert's ownership,
    # not part of the container the comprehension builds.
    unowned = _unown_type_args(own)
    return own if unowned == own else unowned


def _bare_field_read_shape(a: TpyExpr, locals_: dict[str, TpyType], analyzer,
                           narrowed: 'AbstractSet[str]') -> 'str | None':
    """Which admitted shape a FIELD read whose whole render is the bare
    member access has: "plain" (a markers-clean one-level member off a
    routed receiver) or "deref" (the same read one user-Deref hop deeper,
    `body.material.color` -> `body.material.__deref__().color`). None
    outside the slice.

    The arg GATE and the arg RENDER both ask, so the two cannot drift on
    which reads bind a ref slot bare. The pointer set is passed empty: a
    narrowed Optional-wrapper NAME receiver renders through its pointer,
    and an arg position cannot see that set."""
    if not isinstance(a, TpyFieldAccess):
        return None
    if _field_markers_clean(a) and _field_receiver_ok(a, locals_, analyzer):
        return "plain"
    if _user_deref_field_recv_ok(a, locals_, narrowed, analyzer, frozenset()):
        return "deref"
    return None


def _record_field_ref_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer,
                          narrowed: 'AbstractSet[str]' = frozenset()) -> bool:
    """An F1-record FIELD read into a record ref slot (`pass_both(h.a,
    h.b)`): the bare member read binds the `T&`/`const T&` param, aliasing
    the caller's object (the plain field-access
    render). Markers-clean fields off routed receivers, or the same read
    through a user-Deref hop (`_bare_field_read_shape`); exact
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
    return _bare_field_read_shape(a, locals_, analyzer, narrowed) is not None


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
    call renders bare in place, exactly like the value-family
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

def _container_slot_call_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                    analyzer) -> bool:
    """A container-returning CALL at a concrete same-family container slot
    of a builtin stub method (`a.update(make_dict())` / `a.update(copy(b))`
    -> the bare call under the cpp_template). The arg arm threads STORAGE
    use so the inner call's result gate admits the container -- the
    stub-method twin of `_native_container_call_arg`, tightened to concrete
    container slots (a protocol/Sized-ish stub slot has no witness here)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if pt is None or not (is_list(pt) or is_dict(pt) or is_set(pt)
                          or is_array(pt)):
        return False
    rt = analyzer.get_expr_type(a)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if isinstance(rtu, OwnType):
        rtu = unwrap_readonly(rtu.wrapped)
    # Same-type match MODULO element Own-wrapping (the stub's slot spells
    # `dict[K, Own[V]]` where the ret is `dict[K, V]`), element-blind
    # otherwise: the bare call binds the concrete slot whatever the
    # element family (`_storage_call_ret`'s element constraints are the
    # DECL slot's spelling concern, not this bind-in-place render's).
    return (isinstance(rtu, NominalType) and isinstance(pt, NominalType)
            and _unown_type_args(rtu) == _unown_type_args(pt))


def _container_storage_call_rvalue(e: TpyExpr, slot: 'TpyType | None',
                                   analyzer) -> bool:
    """A call whose result ALREADY IS the container slot's storage value.

    Keyed on the slot PAYLOAD rather than on a param spelling, so every
    owning container sink asks it the same way: the `Own[container]`
    argument slot (`out.append(make(i))`), the comprehension element and
    the container-literal element (`[make(i) for i in ..]`, `[make(1)]`).
    Three facts, no node-kind list:

    - the slot holds a container the storage sinks spell
      (`_storage_call_ret`);
    - the call's Own-peeled result type IS that container;
    - the call is an RVALUE source -- a borrow-returning callee hands back
      an alias of caller-durable storage, which the owning slot would copy;
      sema warns there and the sink keeps rejecting, so it stays out.

    A free call also rides the shared callee-shape head
    (`_rvalue_free_call_shape`: linkage, arity, no kwargs, no str-literal
    overload pin); a method call's own lowering validates its receiver and
    arguments, like the other shallow call-source rows.

    Distinct from `_container_slot_call_rvalue_arg`, whose slot is a
    BORROWED `const&` container param -- nothing is stored there, so it
    takes a borrow-returning callee too.
    """
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        return False
    su = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
          if slot is not None else None)
    if su is None or _storage_call_ret(su, analyzer) is None:
        return False
    at = analyzer.get_expr_type(e)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    atu = _resolve_literal_seeded(atu, analyzer)
    if atu != su or not is_rvalue_source(analyzer, e):
        return False
    return isinstance(e, TpyMethodCall) or _rvalue_free_call_shape(e, analyzer)


def _native_record_call_arg(a: TpyExpr, ptype: 'TpyType | None',
                            analyzer) -> bool:
    """An F1-record CALL rvalue into a native/template slot (`len(NegBig())`
    -> `::tpy::__len__(NegBig())`, `repr(datetime.strptime(s, f))`): the
    prvalue binds the const-ref/template slot for the call and renders bare
    in place -- the record sibling of `_native_value_call_arg`.
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
    the call renders bare in place; its own value-position
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
    """The arg rows whose render is decided by the arg itself --
    independent of the plain loop's pre-arms and of the dcbp/pin kwargs
    the builtins loop does not thread -- shared by the plain AND
    native/template arg loops. Their slot domains are disjoint from the
    plain-loop-only rows (arg-temps / optional-ptr / union / readonly-ctor),
    so hoisting them ahead of those rows never changes admission. A new
    arg row belongs here iff a native/template callee renders it
    identically; otherwise it goes in the plain loop only.

    `mutated` marks a MUTATED ctor slot (`T&`, non-const ref): a temp /
    prvalue source binds it ill-formed (the mutated-String-param
    miscompile, BUGS.md), so the temp-producing rows gate off -- the
    str->String coerce half, the opt-str shim, the bytes-literal pin, and
    the value-tuple literal. The by-value rows (scalars / float / BigInt
    literal / char / enum / Ptr / slice-rvalue / Own rvalues -- mutation
    is callee-local, the slot stays by value) and the lvalue-NAME rows
    (record / container / owned-String names bind a `T&` legally; sema's
    readonly system rejects a const violation upstream) stay admitted."""
    # _resolved_scalar, not _eligible_scalar: a ctor-position literal arg
    # (`tpy.int32(10)`) keeps IntLiteralType on its expr type -- the slot
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
    binds the slot directly -- bare, no last-use move (value types
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
    the render of a str literal at a LiteralType target
    (the overloaded pin is inert here: is_str_type(LiteralType) is False).
    Value-blind and identical at every call family."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    return (isinstance(pt, LiteralType) and is_str_type(pt.base_type)
            and isinstance(_peel_coerce(a), TpyStrLiteral))

def _value_tuple_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer, *, mutated: bool = False) -> bool:
    """A value-tuple arg into a value-tuple slot (`const std::tuple<...>&`):
    a bare in-scope name of the same value-tuple family (an lvalue binding
    the ref slot directly -- bare; tuples are value types, so
    no move/temp cascade fires) or a tuple literal (the spelled brace-init
    render, target-threaded per element -- identical for
    plain and native/template callees, so the row is shared). An
    `Own[tuple]` slot is outside `_value_tuple` (the Own wrapper is not a
    TupleType), so the move cascade never reaches this row. A MUTATED slot
    (an address-escaped tuple param can drop the const) keeps the lvalue
    name and rejects the literal (a prvalue into a non-const ref)."""
    vt = _value_tuple(ptype, analyzer)
    if vt is None:
        return False
    if isinstance(a, TpyName):
        if a.name not in locals_:
            return False
        if _value_tuple(locals_[a.name], analyzer) is not None:
            return True
        # A NARROWED value-repr `Optional[value tuple]` binding reads
        # `(*coord)` -- an lvalue tuple of the slot's own family, so it
        # binds the ref slot exactly like a plain tuple name (const or
        # not). Keyed on the narrowed EXPR type, since the binding is
        # still the optional.
        return (_value_opt_tuple(locals_[a.name], analyzer) is not None
                and _value_tuple(analyzer.get_expr_type(a), analyzer)
                is not None)
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
    None`), so the render is the bare value. An `Own[...]` slot is not a
    PtrType after the unwraps (auto-move cascade -> reject), a union slot
    lifts (-> reject), and unsupported coerce-wrapped args reject during
    lowering."""
    if not _eligible_ptr_value(ptype if isinstance(ptype, TpyType) else None,
                               analyzer):
        return False
    if isinstance(a, TpyNoneLiteral):
        # `passthrough(None)` at the (collapsed) `Ptr[T]` slot: `T*` is
        # already nullable, so the render is the bare `nullptr`
        # (witnessed at the render arm).
        return True
    if (isinstance(a, TpyFieldAccess)
            and reads_storage_form_optional(analyzer, a)):
        # A storage-form Optional FIELD at the Ptr slot lifts via
        # `::tpy::optional_to_ptr(h.opt)` -- the PtrType-slot
        # storage-opt wrap (witnessed at the render arm); the pointee must
        # match the slot's.
        at = analyzer.get_expr_type(a)
        atu = unwrap_readonly(at) if at is not None else None
        pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        return (isinstance(atu, OptionalType) and atu.uses_pointer_repr()
                and isinstance(pt, PtrType)
                and unwrap_readonly(atu.inner)
                == unwrap_readonly(pt.pointee))
    return (_eligible_ptr_value(analyzer.get_expr_type(a), analyzer))

def _span_coerce_arg(a: TpyExpr, ptype: TpyType | None,
                     locals_: dict[str, TpyType], analyzer) -> bool:
    """A span-family coerce (spanlike -> Span, span const-widening) into a
    by-value span slot: the coerce arm renders `::tpy::as_span(...)` /
    `as_mut_span(...)` (or the bare identity passthrough) at an
    `is_span` coerce target; the ownership cascade never fires
    for the by-value slot. Bare span VALUES ride `_container_pass_through_arg`;
    array-literal inners (the make_array-prefixed target-threaded render)
    reject via the None disposition."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, OptionalType) and is_span(pt.inner):
        # `Span[...] | None` slot: single-level Optional unwrap suffices
        # (Span is a value type, never wrapped in Own) -- the TpyCoerce
        # arm's peel; the `std::optional<span>` converting ctor absorbs the
        # same coerce render.
        pt = pt.inner
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
    a by-value slice-object param slot: the ownership cascade never
    fires for the value slot (`own is None`), so the render is the bare
    template expansion (`use(s, ::tpy::BasicSlice{1, 3})`). `_slice_object_type`
    does not peel Own, so an `Own[...]` slot rejects (the auto-move cascade);
    a union slot (`int32 | basic_slice`) lifts into the variant -> reject.
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
    An already-union source skips the member-lift arms and
    falls to the default bare-name render (a value union's `const
    std::variant<...>&` binds directly; a pointer variant copies by value) --
    or, for a DEEP-CONST pointer-variant slot (a `readonly[...]` annotation
    or the callee's `const_borrow_params` verdict), the
    const-conversion wrap, keyed at lowering on the same
    verdict -- so admission here is readonly-blind. A member-valued arg (a
    scalar name, a float literal, a record rvalue) hoists a temp -- the
    arg-temp rows where the position flushes, a reject otherwise; the
    temp-free member rows ride `_union_member_lift_arg`. `Own[union]` slots
    auto-move -> reject.
    NB a const-lifted pointer-variant LOCAL into a mutable slot renders the
    bare name (a pre-existing miscompile, BUGS.md), so the shape is not
    carved out here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if isinstance(pt, OwnType):
        return False
    value_union = _eligible_value_union(pt)
    ut = value_union
    if ut is None:
        # The WIDE member class rides too: the by-value variant copy is
        # member-shape-blind (`f(v)` on `v: int | set[int]`), so
        # container/str members pass -- the ptr-union decl arm's rationale.
        ut = _eligible_ptr_union_either(pt, analyzer)
        if ut is None:
            return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        if value_union is None:
            return False
        # A NON-NAME source already typed the SAME VALUE union (a property
        # getter read at a variant slot): the value-variant arm has exactly
        # one already-union render -- fall through to the default arg -- so
        # the source's own shape decides nothing and its lowering
        # re-validates it. Value unions only: a pointer variant's
        # already-union renders branch on the slot's const-ness and on the
        # source being an un-narrowed name, so admission there is not
        # source-blind.
        at = analyzer.get_expr_type(a)
        return (at is not None
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
                == ut)
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return at == ut

def _own_union_storage_name_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType], analyzer,
                                readonly_target: bool = False
                                ) -> 'UnionType | None':
    """A NAME bound `Own[union]` (the STORAGE `std::variant<A, B>` binding,
    the VALUE_VARIANT form) into a non-Own same-union slot in
    pointer-variant form: the `needs_to_ptr_variant_lift` arm --
    `borrow_union(::tpy::to_ptr_variant(u))`, or `to_const_ptr_variant` at a
    deep-const slot. The ONE fact shared by gate and render row; the slot's
    const-ness picks the helper at the render and decides nothing here, so
    `readonly_target` is carried for the caller rather than consulted."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_send_sync(pt)
    if isinstance(pt, ReadonlyType) or isinstance(pt, OwnType):
        return None
    ut = _eligible_ptr_union(pt, analyzer)
    if ut is None:
        return None
    if not (isinstance(a, TpyName) and a.name in locals_):
        return None
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not isinstance(bt, OwnType):
        return None
    return (ut if _eligible_ptr_union(unwrap_readonly(bt.wrapped),
                                      analyzer) == ut else None)

def _value_union_temp_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          narrowed: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """Gate arm for the value-union temp row. A NARROWED name is not this
    row's shape -- it needs no temp at all and rides
    `_value_union_narrowed_pass_arg`."""
    if _value_union_temp_slot(a, ptype, locals_, analyzer) is None:
        return False
    if isinstance(a, TpyName) and a.name in narrowed:
        return False
    return True

def _value_union_narrowed_pass_arg(a: TpyExpr, ptype: TpyType | None,
                                   locals_: dict[str, TpyType],
                                   narrowed: 'set[str] | frozenset[str]',
                                   analyzer) -> bool:
    """The value-union sibling of the member lift (`_union_member_lift_arg`):
    a NARROWED name at a same-union value-variant slot passes BARE. The
    narrowed name's C++ binding is the member-typed extraction alias
    (`const auto& __v = std::get<int32_t>(v)`) and the variant's converting
    constructor takes a member value directly, so no `std::variant<...>
    __tmp_N` is needed and the row carries no flush requirement -- unlike the
    temp row it shares the slot verdict with."""
    if not (isinstance(a, TpyName) and a.name in narrowed):
        return False
    return _value_union_temp_slot(a, ptype, locals_, analyzer) is not None

def _protocol_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                       locals_: dict[str, TpyType], analyzer, *,
                       temps_ok: bool) -> bool:
    """An arg crossing into a protocol param slot -- the structural and
    @dynamic protocol pre-arms of the plain call loop, reduced to the
    two arg shapes whose wrap `_protocol_arg_temp` spells: a bare in-scope NAME
    (lvalue) and a record-ctor RVALUE.

    A bare arg (a structural-slot lvalue, an inheritance-conformer lvalue, or
    an already-protocol name being forwarded) needs no lowering arm at all --
    it falls through `_lower_call_arg`'s ordinary tail, which carries the
    pointer-local `(*p)` retag. Every other
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
        # @dynamic slots keep their adapter temps and reject.
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
        # range(5))`): the frame creation hoists into the same un-spelled
        # auto temp; the genexpr's own lowering gates the source shapes.
        return temps_ok
    elif isinstance(a, TpyStrLiteral) and not is_dyn_protocol(proto):
        # A str LITERAL at a STRUCTURAL slot (`count_chars("hello")` at
        # `Iterable[char]`): the same un-spelled auto temp, binding the raw
        # `const char[N]` the protocol deduces on -- NOT the string_view the
        # spelled temp would give, so it takes the direct row rather than
        # `_protocol_arg_temp`. The @dynamic exclusion matches the sibling
        # arms but has no constructible witness: sema rejects a str at a
        # @dynamic slot for non-conformance before lowering sees it.
        return temps_ok and _witness("argtemp.protocol")
    elif (isinstance(a, TpySubscript) and not is_dyn_protocol(proto)
          and _record_getitem_rvalue_arg(a, analyzer)):
        # A by-value record-getitem subscript rvalue at a STRUCTURAL slot
        # (`show(container[1])`): the raw operator[] prvalue hoists the
        # `auto __tmp_N = container[1];` temp.
        rvalue = True
    elif (isinstance(a, (TpyCall, TpyMethodCall))
          and not is_dyn_protocol(proto)
          and is_rvalue_source(analyzer, a)):
        # A plain CALL rvalue at a STRUCTURAL slot
        # (`deref_protocol(take_ptr(z))` -> `auto __tmp_1 = &z;`): the
        # same un-spelled structural rvalue temp; the inner call
        # re-validates itself during the temp init's lowering.
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
    renders bare through the ordinary tail, so no ArgTemp arm is needed --
    and for a movable container NAME at an `Iterable[Own[T]]` slot the tail
    carries the consuming own_iter wrap (`_consuming_iter_wrap`).
    The temp-hoisting faces (adapters, concrete rvalues) reject at the
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

def _copy_iter_own_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A `copy_iter(..)` RVALUE at the stub's `Iterable[Own[T]]` slot
    (`a.extend(copy_iter(b))`): the copy-suppressing adapter conforms
    structurally and binds the monomorphized template param inline (the
    call renders through its special-builtin arm). A CopyIter NAME takes
    `_protocol_bare_name_arg`'s ordinary bare row instead (a CopyIter
    binding can never take the own_iter last-use rewrite -- only movable
    container bindings do)."""
    pu = (unwrap_readonly(unwrap_send_sync(ptype))
          if isinstance(ptype, TpyType) else None)
    if not (pu is not None and not isinstance(pu, OwnType)
            and is_protocol_type(pu)
            and any(isinstance(t, OwnType)
                    for t in getattr(pu, "type_args", ()))):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return (isinstance(atu, NominalType)
            and atu._module_qname == "tpy.CopyIter"
            and _witness("protoarg.copy_iter"))


def _typed_dict_ctor_call(e: TpyExpr, analyzer) -> 'RecordInfo | None':
    """A TypedDict constructor call (`Options("localhost", 8080)` -- sema's
    kwargs-pack rewrite leaves field-ordered positionals and NO synthetic
    ctor fi), or None. It renders exactly like
    a plain same/cross-module ctor, with `init_params` as the param source.
    Returns the RecordInfo."""
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
                            upcast_ok: bool = False,
                            frame_capturing: bool = False) -> bool:
    """Gate arm for the one-object rvalue temp row: the slot predicate is
    the whole verdict. It already requires a call or method-call source
    that is an rvalue of the slot's type; the source's own lowering
    validates callee and arguments when the temp's init lowers (a record
    ctor's under `_RecordCtorUse.RECORD_TEMP`), so no callee shape is
    pinned here. `upcast_ok` (the FREE-call gate only) admits the
    CHILD-typed upcast temp slice; the ctor gate/rows stay same-nominal
    (their mutated-slot row spells the SLOT type -- a different render).
    `frame_capturing` widens to readonly slots (a borrowing generator/coro
    factory hoists where a sync callee would bind the const ref inline)."""
    return _record_rvalue_temp_slot(a, ptype, analyzer,
                                    upcast_ok=upcast_ok,
                                    frame_capturing=frame_capturing) is not None


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
    `Ref` / `describe(p)` on `p: Ptr[Point]`). Two renders, keyed on the
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


def _deref_coerce_borrow_slot(a: TpyExpr, slot: 'TpyType | None',
                              locals_: dict[str, TpyType], analyzer) -> bool:
    """`_deref_coerce_arg`'s INLINE flavor at a BORROW-form slot that binds the
    `deref_check` lvalue by reference -- a record borrow RETURN or a borrow
    local's decl/reseat. The wrapper `__deref__()` flavor has no such render
    (it hoists a slot-typed VALUE copy) and stays out.

    A `Ptr[readonly[T]]` source at a NON-readonly slot is excluded: the deref
    yields `const T&` while the slot spells `T&` --
    ill-formed C++ ("binding reference of type 'T&' to 'const T' discards
    qualifiers", verified with g++ at both slots). Const-ness is decided on the
    coerce node itself, so the pair is read there rather than re-derived from
    the return type / local binding."""
    dc = _deref_coerce_arg(a, slot, locals_, analyzer)
    if dc is None or dc[0] != "inline":
        return False
    assert isinstance(a, TpyCoerce)
    # Same normalization the sibling applies before its PtrType test, so a
    # wrapped source cannot turn the `.pointee` read into an AttributeError
    # (which would escape as a crash instead of a reject).
    actual = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(a.actual_type)))
    if is_readonly_ptr(actual):
        return isinstance(a.expected_type, ReadonlyType)
    return True


def _iter_rvalue_structural_arg(a: TpyExpr, proto,
                                locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """An iterator/iterable-object RVALUE at a STRUCTURAL protocol slot
    (`make_toks(ints(3))` / `mutate_via_iterator(iter(pts2))` /
    `collect_items(d.keys())`): the un-spelled
    `auto __tmp_N = <rvalue>;` hoists and the temp name passes -- the coro-factory
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


def _record_rvalue_structural_arg(a: TpyExpr, proto, analyzer) -> bool:
    """An F1-record RVALUE at a STRUCTURAL protocol slot
    (`json.load(io.StringIO(s))`): the monomorphized param binds `T&`, which
    a temporary cannot, so the un-spelled `auto __tmp_N =
    <rvalue>;` hoists and the name passes -- the `is_temporary_expr` +
    protocol-slot arm of the arg loop. `auto`, not the record's spelling:
    the temp's type comes from `temps.create(ptype, ..)`, which spells a
    protocol slot `auto`. The dynamic flavor takes the adapter machinery
    instead and stays out."""
    if is_dyn_protocol(proto):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    if not is_rvalue_source(analyzer, a):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    return record_like(atu, analyzer)


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
    """An rvalue-shaped eligible scalar or char into a plain `Own[scalar]` /
    `Own[char]` slot: the by-value slot binds the rvalue directly (no
    `_maybe_move` for a non-name, no copy temp for a non-simple-lvalue, the
    value-type `else` tail), so the render is bare -- a coerced int literal
    (`takes(5)`), a scalar ctor / call rvalue, a binop, a select of two Chars.
    A bare NAME / field lvalue hoists the
    copy+move temp (`_own_lvalue_arg`); a coerce-WRAPPED lvalue splits on
    the rendered-identity check (`needs_copy`) -- the real-conversion
    NAME face routes via `_own_coerce_cast_arg`, the rest rejects.

    char rides the scalar row rather than a parallel one: it is a value type
    spelled `char`, so `Own[char]` is the same by-value slot binding the same
    bare render."""
    w = _plain_own_slot(ptype)
    # An inference-pending float slot (`Rc.new(3.14)` substitutes the
    # unresolved FloatLiteralType) resolves to the default double like the
    # concrete float slot.
    if w is None or not (_eligible_scalar(w) or _eligible_char(w)
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
    return ((_eligible_scalar(at) or _eligible_char(at))
            and _witness("own.scalar_rvalue"))

def _own_coerce_cast_arg(a: TpyExpr, ptype: TpyType | None,
                         locals_: dict[str, TpyType]) -> bool:
    """A REAL scalar-cast coerce over a plainly-DECLARED scalar local NAME
    at a plain `Own[scalar]` slot (`take(big)` ->
    `take((big).to_fixed_check<int32_t>())`): the wrap changes the rendered
    string, so the needs_copy test flips False and the cast rvalue
    binds the by-value slot bare -- the generic coerce-template render IS
    the whole emit, no dedicated arm. The declared-scalar requirement
    keeps every INDIRECT inner out (a narrowed `int | None` param spells
    `(*v)` under the coerce, not the bare `(v)` this row
    renders); identity coercions stay
    unadmitted (their copy-temp flavor is unwitnessed at the free-call
    ladder)."""
    if not isinstance(a, TpyCoerce):
        return False
    w = _plain_own_slot(ptype)
    if w is None or not _eligible_scalar(w):
        return False
    inner = _peel_coerce(a)
    if not isinstance(inner, TpyName):
        return False
    dt = locals_.get(inner.name)
    if dt is None or not _eligible_scalar(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))):
        return False
    return (_coerce_disposition(a, own_slot_arg=True) == "template"
            and _witness("call.own_coerce_cast"))

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
    the temp cascade never fires and the render is the bare
    expansion. The same-nominal check is the usual slice guard. Method-call
    rvalues stay out pending a witness. The slot's record-ness is
    `_f1_record`'s question, not user-record-ness: a builtin RECORD-category
    slot (`__poll__(Waker())`) copies by value exactly the same way."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not (isinstance(pt, NominalType) and not pt.is_ref_param()
            and record_like(pt, analyzer)):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != pt or not is_rvalue_source(analyzer, a):
        return False
    return (_record_call_rvalue_shape_ok(a, analyzer)
            and _witness("call.value_record_arg"))

def _value_record_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A ValueType-record NAME at a BY-VALUE same-record slot
    (`waw.utcoffset(summer)` on a datetime param): the by-value slot
    copies, so the bare name read is the shared render -- the NAME twin
    of `_value_record_rvalue_arg` (value semantics, no move/borrow
    question)."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not (isinstance(pt, NominalType) and pt.is_user_record
            and not pt.is_ref_param() and _f1_record(pt, analyzer)):
        return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        locals_[a.name])))
    return bool(at == pt and _witness("call.value_record_name_arg"))

def _value_opt_record_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 analyzer) -> bool:
    """A ValueType-record CTOR rvalue at a value-repr
    `Optional[ValueType record]` slot (`h.method(c, Vec(7))` on
    `v: Vec | None` -> the `std::optional<Vec>` param's converting ctor
    binds the prvalue INLINE) -- the optional-slot twin of
    `_value_record_rvalue_arg`."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    opt = _value_opt_value_record(pt, analyzer)
    if opt is None:
        return False
    inner = unwrap_readonly(opt.inner)
    if isinstance(a, TpyName):
        # A member-typed NAME (`waw.utcoffset(summer)` on
        # `dt: datetime | None`): the converting optional ctor copies the
        # value -- bare render, value semantics.
        at = analyzer.get_expr_type(a)
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        return bool(atu == inner
                    and _witness("call.value_opt_record_arg"))
    if not isinstance(a, TpyCall) or not is_rvalue_source(analyzer, a):
        return False
    if analyzer.get_expr_type(a) != inner:
        return False
    return (_record_call_rvalue_shape_ok(a, analyzer)
            and _witness("call.value_opt_record_arg"))

def _own_dyn_method_rvalue_ok(a: TpyMethodCall) -> bool:
    """Whether a method-call rvalue into an `Own[@dynamic P]` slot is
    ALREADY the erased `unique_ptr<P>` (renders bare -- `Box(e.clone())`,
    `Box(b1.take())`) rather than a concrete conformer needing the
    make_adapter wrap. Sema stamps BOTH shapes' expr type as the erased
    view, so the tell is the fi's DECLARED return: `Own[P]` itself, or an
    `Own[T]` whose substitution the caller's expr-type match then pins to P
    (a T substituting to a concrete record fails that match instead). An
    async factory (concrete coro frame) and a concrete-record return
    (`sock_accept -> Own[_SockAccept]`) both take the adapter wrap -> reject.
    Narrower than `dyn_protocol_forward_ok`'s provenance decision -- an edit
    there must revisit this classifier."""
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

def _own_slot_payload(ptype: 'TpyType | None') -> 'TpyType | None':
    """The payload a plain `Own[...]` slot binds, with a DOUBLE `Own`
    peeled: a literal-seeded container local can resolve its element to an
    `Own[...]` (the seeding call's own return spelling), so the substituted
    `Own[T]` insert slot arrives as `Own[Own[list[T]]]`. Ownership is the
    slot's ABI, not part of the type identity a bare rvalue bind matches
    on, so the second wrapper peels exactly as the source side peels the
    call's own `Own` return. Gate and render arm share it."""
    w = _plain_own_slot(ptype)
    if w is None:
        return None
    u = unwrap_readonly(unwrap_send_sync(w))
    return unwrap_readonly(u.wrapped) if isinstance(u, OwnType) else w


def _own_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record rvalue CALL (a ctor `A(7)` or a by-value
    record-returning free call) into a plain `Own[record]` SAME-nominal slot:
    an rvalue binds the `T&&` slot directly (the rvalue-source
    `else` tail -- no temp), so the render is the bare expansion,
    mirroring the `Own[union]`-slot ctor arm (`_own_union_ctor_arg`). A
    borrow-returning callee is not an rvalue source (it copies
    through a temp) -> reject; the same-nominal check is a slice guard (sema
    rejects an upcast into an Own slot outright)."""
    w = _own_slot_payload(ptype)
    if w is None:
        return False
    if _select_node(a) and _rvalue_ref_init(a, w, analyzer):
        # An all-fresh select, record or container: its prvalue `?:` binds
        # the `T&&` slot like a ctor rvalue, the chosen operand built
        # straight into the parameter.
        return bool(_witness("own.select_rvalue"))
    w_container = _storage_call_ret(
        unwrap_readonly(unwrap_send_sync(w)), analyzer) is not None
    w_dyn = is_dyn_protocol(unwrap_readonly(unwrap_send_sync(w)))
    if not w_container and not w_dyn and not record_like(w, analyzer):
        return False
    if w_container:
        # Both CALL faces of the container leg (`table.append(make_row(1.0))`
        # and `g.set(acked.copy())`): the rvalue binds the `T&&` slot bare,
        # and the call's own lowering validates callee and args. Names /
        # literals still ride the copy-temp and literal rows. The verdict is
        # the shared owning-container-sink one, which the comprehension and
        # container-literal element faces read too.
        return bool(_container_storage_call_rvalue(a, w, analyzer)
                    and _witness("own.container_call_rvalue"))
    if isinstance(a, TpyMethodCall):
        # A record- or Own[@dynamic]-returning METHOD-call rvalue
        # (`Arc.new(Mutex.new(0))` / `Box(e.clone())` -- the unique_ptr<P>
        # prvalue): binds the T&& slot
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
    if w_dyn:
        # A dyn-protocol Own slot admits only the method-rvalue face here;
        # names/literals ride the copy-temp and literal rows.
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
        # `native_ok`: a plain @native record's ctor emits through the same
        # record branch (`::tpy::MovableConditionVariable()`), and an rvalue
        # binds the `T&&` slot bare -- no temp whose @nocopy-ness could
        # matter, so the face is position-blind like the rest of this row.
        return ((_ctor_shape_ok(a, analyzer, native_ok=True)
                 or _ctor_instantiation_ok(a, analyzer))
                and _witness("own.record_rvalue"))
    if _record_rvalue_call_shape(a, analyzer):
        return bool(_witness("own.record_rvalue"))
    # The @native residue of the same row (`JoinHandle[R](spawn_native(t))`):
    # the native callee's prvalue binds the `T&&` slot bare, exactly as the
    # plain one does. @cpp_template callees stay out -- a template
    # text-substitutes the return spelling, so its divergence mechanism is
    # independent of the declared type the shared predicate reads.
    return (_native_own_record_rvalue_call_shape(a, analyzer)
            and bool(_witness("own.native_record_rvalue")))

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
    return w if record_like(unwrap_readonly(w.inner), analyzer) else None


def _own_opt_ptr_name_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          narrowed: 'AbstractSet[str]',
                          analyzer) -> 'OptionalType | None':
    """A pointer-repr `Optional[record]` INDIRECT name at an
    `Own[Optional[record]]` slot (`Holder(r)` where `r: Own[Rec | None]` is a
    param): the `T*` binding cannot be dereferenced unconditionally, so the
    owning optional is rebuilt null-safely
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


def _is_move_source_facts(value: TpyExpr,
                          movable_locals: 'set[str] | frozenset[str]',
                          analyzer, func_name: 'str | None') -> bool:
    """`_is_move_source`'s DEFAULT (movable-locals) question over the discrete
    facts rather than the lowering context, so the arg table can ask it: the
    table carries facts, never the context. `_is_move_source` delegates here
    so the two cannot answer differently."""
    inner = _peel_coerce(value)
    return (isinstance(inner, TpyName)
            and inner.name in movable_locals
            and inner in analyzer.ctx.all_last_uses)


def _own_opt_ptr_name_move_arg_facts(
        a: TpyExpr, ptype: 'TpyType | None', declared: dict[str, TpyType],
        narrowed: 'set[str] | frozenset[str]', analyzer,
        movable_locals: 'set[str] | frozenset[str]',
        func_name: 'str | None') -> 'OptionalType | None':
    """`_own_opt_ptr_name_move_arg` over the discrete facts -- see
    `_is_move_source_facts` for why the split exists."""
    slot = _own_opt_ptr_name_arg(a, ptype, declared, narrowed, analyzer)
    if slot is None or not _is_move_source_facts(a, movable_locals, analyzer,
                                                 func_name):
        return None
    return slot


def _r_own_opt_ptr_name_move(req: _ArgReq) -> bool:
    return _own_opt_ptr_name_move_arg_facts(
        req.a, req.ptype, req.locals_, req.narrowed, req.analyzer,
        req.movable_locals, req.func_name) is not None


def _r_opt_own_ptr_opt_name_move(req: _ArgReq) -> bool:
    return (_opt_own_ptr_opt_name_arg(req.a, req.ptype, req.locals_,
                                      req.analyzer) is not None
            and _is_move_source_facts(req.a, req.movable_locals, req.analyzer,
                                      req.func_name))


def _copy_own_arg(a: TpyExpr, ptype: TpyType | None,
                  analyzer) -> bool:
    """`copy(<source>)` into a SAME-nominal plain `Own[T]` slot
    (`items.append(copy(p))` -> `push_back(Point(p))`,
    `sink(copy(h.items()))` -> `std::vector<int32_t>(h.items())`) or its
    Own-OPTIONAL sibling (`Own[Box] | None` -- the by-value
    `std::optional<Box>` slot, whose converting ctor absorbs the same
    copy-construct rvalue: `consume_optional(Box(b))`): the rvalue binds
    like any rvalue at that slot (`_own_record_rvalue_arg`'s row). Both
    families, because the copy tail is one render and the slot peel is
    already family-blind. The pointer-source split
    (`copy_construct_source` excludes pointer-locals, whose render derefs)
    re-runs at lowering with the live pointer set; a gate-admitted pointer
    source rejects there."""
    w = _plain_or_opt_own_slot(ptype)
    if w is None or not (_f1_record(w, analyzer) or _f1_container_ref(w)):
        return False
    # frozenset(): the gate is deliberately pointer-blind; lowering re-runs
    # with the live set and rejects pointer sources (gate-vs-lowering split).
    src = copy_construct_source(a, analyzer, frozenset())
    return src is not None and src == w and _witness("own.copy_construct")

def _copy_open_elem_arg(a: TpyExpr, ptype: TpyType | None,
                        analyzer) -> 'TpyType | None':
    """`copy(src[i])` of an OPEN container element into an `Own[...]` slot
    inside a generic body (`Owned(copy(src[0]))` ->
    `Owned<T>(T(::tpy::__getitem__(src, 0)))`): the copy render's generic
    tail spells `{arg_type.to_cpp()}({read})` around the standard checked
    element read. Two slot shapes carry an open element: the bare type param
    itself, and a VALUE TUPLE with an open member (`Pair(copy(src[0]))` at
    `Own[tuple[A, B]]` substituted `tuple[T, int]` ->
    `std::tuple<T, ::tpy::BigInt>(<read>)`) -- the tail is type-blind, so both
    render off the same spelling. Returns the slot type or None."""
    w = _plain_own_slot(ptype)
    if not (isinstance(w, TypeParamRef)
            or (isinstance(w, TupleType) and contains_type_param(w))):
        return None
    arg = copy_call_arg(a, analyzer)
    if not isinstance(arg, TpySubscript) or isinstance(arg.index, TpySlice):
        return None
    at = analyzer.get_expr_type(arg)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(w, TypeParamRef):
        if not (isinstance(atu, TypeParamRef) and atu.name == w.name):
            return None
    elif atu != w:
        return None
    return w

def _own_optional_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                                    analyzer) -> bool:
    """A same-nominal record RVALUE (a ctor `Inner(42)` or a by-value
    record-returning call) into an `Own[record | None]` ctor slot
    (`std::optional<Inner>&&` -- a @dataclass Optional-record field): the
    prvalue binds the rvalue-ref optional directly through C++'s implicit
    `Inner -> optional<Inner>` conversion, so the render is the bare
    expansion (`Outer("a", Inner(42))`), mirroring `_own_record_rvalue_arg`'s
    plain `Own[record]` row. A record NAME would need the move/copy cascade
    (its own arm) and a borrow-returning callee is not an rvalue source, so
    both reject."""
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
    if not (isinstance(rec, NominalType) and record_like(rec, analyzer)):
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

def _opt_own_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                               analyzer) -> bool:
    """A same-nominal record RVALUE (ctor / by-value record call) into an
    `Optional[Own[record]]` BY-VALUE slot (`unwrap_record(Payload(..))` at
    `std::optional<Payload>`): the prvalue binds through C++'s implicit
    `Payload -> optional<Payload>` conversion, so the render is the
    bare expansion -- the value-repr sibling of
    `_own_optional_record_rvalue_arg`'s `Own[record | None]` nesting."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(u, OptionalType) and not u.uses_pointer_repr()):
        return False
    ow = unwrap_readonly(unwrap_send_sync(u.inner))
    if not isinstance(ow, OwnType):
        return False
    rec = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ow.wrapped)))
    if not (isinstance(rec, NominalType) and record_like(rec, analyzer)):
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
    binds the const-ref slot inline, bare -- no
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
    at its LAST USE renders `std::move(name)` in ANY position (the
    `_maybe_move` fires before the copy-temp arm, so no flush is needed) --
    the `heap_take(value)` ctor-MIL shape. Gate-side movability follows
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
    return a in analyzer.ctx.all_last_uses

def _own_lvalue_arg(a: TpyExpr, ptype: TpyType | None,
                    locals_: dict[str, TpyType],
                    narrowed: 'set[str] | frozenset[str]', analyzer,
                    param_names: 'AbstractSet[str]' = frozenset()) -> bool:
    """Gate arm for the Own-slot copy+move row -- the slot/shape verdict plus
    the local argument checks. Both outcomes (the `__tmp_N` copy and the
    last-use `std::move(name)`) are expressible, so lowering admits the shape
    wholesale under `temps_ok` and lowering picks; restricting the temp-free
    move to the flushable positions is gate-narrowing only (a move arg in a
    condition rejects)."""
    if _own_lvalue_temp_slot(a, ptype, analyzer, locals_,
                             param_names) is None:
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
    (`_urlopen(..., conn)` on `conn: Box[...]`): `_maybe_move`
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
    if not record_like(w, analyzer):
        return None
    # The Own-lift coerce sema wraps an INFERRED-targ generic call's arg
    # peels. TWO independent guards keep this sound against any future
    # coercion matching the shape: the payload-type equality below rejects
    # any MATERIALIZING coercion (its result type differs from the record
    # payload), and the render arm re-derives the move verdict on the
    # PEELED name (`_is_move_source` -- movable_locals membership + last
    # use), so a copy shape can never render the bare move.
    a = _peel_coerce(a)
    if not (isinstance(a, TpyName) and a.name in locals_):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return w if at == w else None


def _opt_own_container_name_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType],
                                analyzer) -> 'TpyType | None':
    """The CONTAINER twin of `_opt_own_record_name_arg`: a same-typed
    reference-typed container NAME into an `Optional[Own[container]]` slot
    (`DictReader(buf, fn)` on `fieldnames: Own[list[str]] | None`) -- the
    movable last use renders `std::move(fn)` bare and the `std::optional`
    converting ctor absorbs it. Payload-blind render, so the two share the
    lowering arm; the gate keeps them apart so the record ladders do not
    silently gain the container slice."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not isinstance(pt, OptionalType):
        return None
    ow = unwrap_optional_own(pt)
    if ow is None:
        return None
    w = unwrap_readonly(ow.wrapped)
    if not _f1_container_ref(w):
        return None
    a = _peel_coerce(a)
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
    if not record_like(w, analyzer):
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
    the render is the bare ctor expansion (no temp). Frame-capturing
    callees (generator/coro factories) take the argtemp.record_rvalue row
    instead (the frame outlives the statement). The NAME face (the readonly
    pass-through) stays deferred with the other deep-const rows."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(pt, ReadonlyType):
        return False
    inner = unwrap_readonly(pt)
    if not (isinstance(inner, NominalType) and record_like(inner, analyzer)):
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
    and the optional-ptr tail wraps the same alias
    (`&(__u)`), so no narrowed reject (the face is
    temp-free; rejecting it here would drift from lowering's shared verdict)."""
    face = _optional_ptr_arg_face(a, ptype, locals_, analyzer)
    if face is None:
        return False
    if face in ('none', 'select'):
        # A select's operands and form gate in its own lowering.
        return True
    if face == 'subscript':
        # `&(<lvalue record subscript>)` -- temp-free, so no flush position
        # needed; the face verdict already pinned the lvalue-borrow shape.
        return True
    if face == 'call_pass':
        # A borrow-returning call passed bare -- temp-free; the call's own
        # recursive lowering validates callee kind and args (the result
        # family is admitted under the PTR_OPT_PASSTHROUGH verdict).
        return True
    if face == 'ctor':
        if isinstance(a, TpyMethodCall) or _non_ctor_call(a):
            # Marker-call / free-call rvalue: the call's own lowering
            # validates the callee kind and args when the ArgTemp init
            # lowers recursively; only a record CTOR source needs the
            # ctor-shape verdict here.
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
    if face == 'scalar_temp':
        # The typed scalar temp + `&(...)` -- flush positions only, like its
        # ctor/container siblings; the rvalue's own lowering validates it.
        return temps_ok
    # 'name' / 'pass'
    if a.name not in locals_:
        return False
    return True

def _union_member_lift_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free member rows of the union-arg pointer-variant branch:
    a `None` literal (`pv{std::monostate{}}`) or a member-typed record NAME
    (`pv{&(name)}` -- never a temp: names are never rvalue sources; a name
    deriving from exactly one member counts, the ctor binding the base
    pointer) into a
    non-Own pointer-variant slot. A deep-const slot (a `readonly[...]`
    annotation or the callee's `const_borrow_params` verdict) takes the
    same lift with the const-pointee variant spelling, decided at
    lowering, so admission is const-blind. A record RVALUE (`take(A(n))`)
    hoists a named temp -> reject. A narrowed subject is admitted here too
    (its `locals_` type is the member) and takes the same member lift: its C++
    binding is the member-typed extraction alias, so the variant lifts the
    alias's address."""
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
    return _union_member_match(at, ut.members, analyzer)

def _union_coerced_literal_arg(a: TpyExpr, ptype: TpyType | None,
                               locals_: dict[str, TpyType], analyzer) -> bool:
    """An int literal into a VALUE-union slot (`take_vu(3)`): sema coerces the
    literal to the union itself, so the `already_union` verdict
    falls to the default arg render -- the bare literal (the variant
    converting ctor does the work). A bare member-typed literal (`take_vu(2.5)`
    -- a float literal is typed at the member, not the union) hoists the
    `std::variant<...> __tmp_N` temp -> reject. Only value unions arise: a
    numeric literal cannot coerce to a record union."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(a, TpyCoerce):
        return False
    ut = _eligible_value_union(unwrap_readonly(unwrap_send_sync(pt)))
    if ut is None:
        # The WRAPPER-union flavor (`eval_expr(42)` on a recursive
        # alias): the wrapper's converting ctor absorbs the INT literal,
        # so the coerced arg passes bare -- no temp (container literals
        # hoist instead, the _ru_container_literal_ok row; other literal
        # kinds type at the MEMBER, never arriving coerced-to-union).
        if _ru_wrapper_arg_slot(pt) is None:
            return False
        return isinstance(_peel_coerce(a), TpyIntLiteral)
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return at == ut

def _own_union_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record-ctor rvalue into an `Own[union]` value-variant slot
    (`take_own(A(7))` -> `take_own(A(7))`): the Own cascade is inert
    for the shape -- the ctor is an rvalue (no `_maybe_move`, no copy temp) and
    no `to_value_variant` lift fires (a ctor is not a ptr-variant source) -- so
    the render is the bare ctor expansion. The arg record must be a member
    of the union (slice guard; sema enforces). An Own[union] slot with a NAME
    arg is the auto-move cascade (`std::move(u)`) -> reject."""
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

def _own_union_call_pass_arg(a: TpyExpr, ptype: TpyType | None,
                             analyzer) -> bool:
    """A same-union `Own[A | B]`-RETURNING call rvalue at an `Own[union]`
    value-variant slot (`describe(pick(True))`): the prvalue moves through
    the `&&` slot bare -- no lift, no temp, just the bare
    call. The ONE fact shared by gate and render row."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_send_sync(pt)
    if isinstance(pt, ReadonlyType) or not isinstance(pt, OwnType):
        return False
    ut = _eligible_ptr_union(pt.wrapped, analyzer)
    if ut is None:
        return False
    if not isinstance(a, TpyCall):
        return False
    rt = analyzer.get_expr_type(a)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if isinstance(rtu, OwnType):
        rtu = unwrap_readonly(rtu.wrapped)
    return (_eligible_ptr_union(rtu, analyzer) == ut
            and not call_returns_cpp_ref(analyzer, a.resolved_function_info))

def _ctor_effective_params(e: TpyCall, ri) -> 'list[ParamInfo]':
    """The param list the record-ctor arg loop reads: the synthetic fi's, or
    -- when the record has NO own `__init__` but registered init_params (an
    inherited param-ful `__init__`) -- the registry triples, the
    init_params fallback. `_ctor_shape_ok` already pinned the
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
    provided args -- the same `Name(args)` emit as at exact arity. A
    variadic slot has no positional default to fall back on, and more args than
    params is a resolution the raw-name shape never produces -> reject."""
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

def _non_ctor_call(a: TpyExpr) -> bool:
    """A resolved FREE call that is not a record construction -- the shape
    whose own lowering (not a ctor-shape verdict) decides whether it can be
    emitted."""
    if not isinstance(a, TpyCall):
        return False
    fi = a.resolved_function_info
    return fi is not None and not fi.is_constructor


def _ctor_shape_ok(e: TpyCall, analyzer, *, native_ok: bool = False) -> bool:
    """A bare-name plain user-record constructor call in the `Name(args)` /
    qualified `::ns::Name(args)` emit shape: the RAW source name for a
    same-module record, or the
    `record_qualification` spelling for an imported one (stamped at the
    THIRCtorCall lowering), args through the ctor arg loop with every
    special arm structurally unreachable. This is the arg-blind
    shape/registry core used before recursive TpyCall lowering validates the
    constructor arguments. Native / cpp_template / multi-overload / TypedDict
    ctors take other emit shapes -> reject.

    `native_ok` opens the ONE native ctor face whose emit IS the record
    branch: a plain `@native` record (never `@native_c`, whose aggregate
    `{args}` init is a different render) whose `__init__` carries only a
    `native_name` -- `cpp_template` / `native_function` inits take the
    native/template call arm instead, which is checked FIRST. The lowering
    already spells `record_info.native_name`. Opted into per caller: of the
    call sites here, three pass `native_ok` (the decl/value slot, the ctor
    MIL, and the Own-arg rvalue row) and the rest take the default, so a
    native ctor is still refused at positions that admit a plain record
    one. The render itself reads the record's nativeness rather than the
    position, so widening a further caller needs no new render arm."""
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if _ctor_call_special_form(e):
        return False
    # A same-name free function wins the registry branch before the
    # record lookup.
    if analyzer.registry.get_function(e.func_name):
        return False
    # Sema attaches a SYNTHETIC constructor fi (`is_constructor`, named after
    # the record, params = the resolved __init__'s); a builtin type ctor
    # (`int32(x)`) resolves to the real @cpp_template __init__ instead and
    # rides the scalar-ctor arm of the `TpyCall` lowering.
    fi = e.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if fi.cpp_template or fi.native_function:
        return False
    if fi.native_name and not native_ok:
        return False
    inherited_arity = False
    if not _ctor_arity_ok(e, fi):
        # A record with NO own `__init__` but a param-ful INHERITED one:
        # sema's synthetic ctor fi carries EMPTY params, so positional args
        # fail the fi-arity gate. The arg loop reads the registry's
        # init_params triples instead; defer the arity verdict to the
        # no-own-overloads arm below, which checks against those.
        if fi.params or not e.args:
            return False
        inherited_arity = True
    ri = analyzer.registry.get_record(e.func_name)
    rt = analyzer.get_expr_type(e)
    if not isinstance(rt, NominalType):
        return False
    # A `@builtin_type` record of RECORD category constructs through the same
    # record branch as a user record: the declaring module's qualification
    # spells it, and `builtin_type_key` keys TypeDef payload dispatch rather
    # than the ctor emit. The builtin categories that DO carry their own C++
    # ctor shape (`list` -> `std::vector`) are the ones `_f1_record` rejects.
    builtin_record = not rt.is_user_record and _f1_record(rt, analyzer)
    if not (rt.is_user_record or builtin_record):
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
        # last-write-wins while sema resolved the TYPE qname-first. The emit
        # comes from the type, and the THIRCtorCall lowering reads
        # get_record_for_type too -- validate against the same record.
        # The override applies only when the short-name record is a
        # plain user record; a native/builtin short-name hit keeps ITS own
        # emit shape, which has no witness here -- reject.
        if ri.builtin_type_key is not None or ri.is_native:
            return False
        ri = ri_t
    if ri is None or (ri.builtin_type_key is not None and not builtin_record):
        return False
    # A NATIVE exception record (Throwable subclass) constructs via the plain
    # `::tpy::Name(args)` emit: the resolved synthetic ctor fi is plain, and the
    # `@cpp_template` __init__ overloads only inform C++ overload resolution --
    # the call site emits args verbatim, exactly like a user-record ctor.
    # Any OTHER native record takes a different ctor emit shape -> reject.
    is_native_exc = ri.is_native_exception_class
    native_named = (native_ok and ri.is_native and not ri.is_native_c
                    and not is_native_exc)
    if fi.native_name and not native_named:
        # The synthetic ctor fi's native_name only rides the record branch
        # for a plain `@native` class; anything else spells another emit.
        return False
    if ri.is_native and not is_native_exc:
        # A plain @native record with NO own `__init__` constructs via
        # `native_name(args)` (@native_c: the `{args}` aggregate), args
        # typed from init_params -- the same no-own-init fallback the
        # inherited-init face reads. An overloaded / @native /
        # @cpp_template `__init__` takes other emit arms
        # (the native/template call arm, or the builtin ctor) -> reject.
        if ri.get_method_overloads("__init__") and not native_named:
            return False
    if ri.type_params:  # generic ctor: substituted/spelled type args -> reject
        return False
    if is_native_exc:
        # Skip the single-overload / non-cpp_template init_fi checks below: a
        # native exception's `@cpp_template` overloads all expand to the plain
        # `::tpy::Name(args)` ctor, so the verbatim-arg emit is the same
        # whichever overload C++ selects.
        return True
    # A multi-overload __init__ set: the ctor arg loop reads the record's
    # init_info params, which may disagree with the resolved stub -> reject. The
    # special member forms are read off the REAL __init__ (the synthetic fi
    # carries only params + mutation facts).
    overloads = ri.get_method_overloads("__init__")
    if not overloads:
        # No OWN __init__: either the implicit default ctor (zero-arg call,
        # fi-arity already pinned it) or an INHERITED param-ful __init__
        # (sema attaches a synthetic ctor fi with EMPTY params; the real
        # param list lives in ri.init_params, which the lowering reads via
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
        # mismatch this gate does not model -> reject.
        return False
    if len(overloads) != 1:
        return False
    init_fi = overloads[0]
    if (init_fi.cpp_template or init_fi.native_function
            or (init_fi.native_name and not native_named)
            or init_fi.is_consuming or init_fi.error_return_type is not None
            or init_fi.native_cpp_return_type is not None
            or any(isinstance(p.type, LiteralType) for p in init_fi.params)):
        return False
    return True

def _ctor_instantiation_ok(e: TpyCall, analyzer) -> bool:
    """The INSTANTIATION form of a record-ctor call -- spelled
    `Cell[int32]()` / `Poll[T]()` or inferred `Pair(1, 2)` (`call_type`
    set) -- plus the zero-arg builtin-CONTAINER instantiation arriving
    with a constructor fi (the non-record carve-out below): the call_type
    branch renders
    `type_to_cpp(call_type)(args)`, carried as `THIRCtorCall.type_cpp =
    lc.render_type(call_type)`, so
    cross-module and generic spellings need no extra gating beyond
    `_f1_record`'s type-arg slice. Shares the arg rows with the raw-name
    face (the scalar / record-rvalue slice coincides across the two
    arg loops); the None-literal / array-literal / `T()`-construct
    targeted arms and protocol/union/optional slots are excluded by those
    rows. Native records (native fi arms) and template/native ctor fis
    take other emit arms -> reject."""
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
    # for the raw-name face (`ArrayList[int32, 8]()` with a defaulted `items`
    # param calls the spelled zero-arg `ArrayList<int32_t, 8>()`).
    inherited_arity = False
    if not _ctor_arity_ok(e, fi):
        # A record with NO own `__init__` but a param-ful INHERITED one
        # (`TypedM[int32](7)`): the synthetic ctor fi carries EMPTY params,
        # so positional args fail the fi-arity gate -- the raw-name face's
        # inherited-init situation on the instantiation spelling. Defer the
        # arity verdict to the registry triples below.
        if fi.params or not e.args:
            return False
        inherited_arity = True
    if not _f1_record(ct, analyzer):
        # A zero-arg builtin-CONTAINER instantiation reaching the ctor path
        # with a constructor fi (`Array[int32, 8]()` -- e.g. the pascal
        # frontend's default-array init): the same `type_to_cpp(call_type)()`
        # render with no arg arms to diverge. Arg-ful container ctors keep
        # rejecting here (their args have their own arms).
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
    # a multi-overload set) has its own emit arms -> reject.
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
    reproduce (the auto-move cascade) -> reject.
    A MUTATED String slot (`std::string&`) keeps only an owned-String NAME
    (an lvalue binding the ref legally): the coerce half materializes a
    `std::string(x)` temp -- the mutated-String-param miscompile. The
    view-slot branch is mutation-blind (`std::string_view` stays by value)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, OwnType):
        # `Own[StrView]` is a no-op spelling on the VALUE view (resolves
        # to plain StrView -- the set/dict stubs' `Own[T]` insert param at
        # a view element): a LITERAL renders bare (`s.insert("hello")`).
        # A NAME arg is NOT admitted -- the auto-move cascade hoists
        # `std::string_view __tmp_N{name};` + move there.
        # `Own[str]` -- a genuinely owned copy -- keeps rejecting below.
        _own_inner = unwrap_readonly(pt.wrapped)
        if (isinstance(_own_inner, NominalType)
                and is_str_view_type(_own_inner)
                and isinstance(_peel_coerce(a), TpyStrLiteral)):
            pt = _own_inner
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
        # the nominal spelling. An OWN slot is NOT that shape -- the
        # resolver sees through Own, but the auto-move cascade hoists
        # a temp there (the literal-only Own[view] arm above is the whole
        # admitted slice).
        if isinstance(pt, OwnType) or _resolved_str_value(pt, analyzer) is None:
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
    slot in place; the auto-move cascade that keeps lvalue
    str sources off this slot never fires for a literal). Temp-free, so
    position-independent."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not isinstance(pt, OwnType):
        return False
    inner = unwrap_readonly(pt.wrapped)
    return (isinstance(a, TpyStrLiteral)
            and isinstance(inner, NominalType) and is_str_type(inner))


def _own_bytes_literal_arg(a: TpyExpr, ptype: TpyType | None) -> bool:
    """The bytes twin of `_own_str_literal_arg`: a bytes LITERAL into an
    `Own[bytes]` ctor slot renders bare with the OWNED literal spelling
    (`FileField("a.txt", ::tpy::bytes_literal_owned("hello", 5))` -- the
    prvalue converts into the by-value `std::vector<uint8_t>` slot in place).
    The `Own` key is what splits it from a plain `bytes` slot, which is
    view-shaped and takes the static-span `bytes_literal` render instead."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not isinstance(pt, OwnType):
        return False
    inner = unwrap_readonly(pt.wrapped)
    return (isinstance(a, TpyBytesLiteral)
            and isinstance(inner, NominalType) and is_bytes_type(inner))


def _opt_str_shim_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer) -> bool:
    """A value-repr `Optional[str]` param NAME into another value-repr
    `Optional[str]` slot -- the same-TPy-type ARG split
    (`s ? std::make_optional(std::string(*s)) : std::nullopt`).
    Fires for the WHOLE optional whether or not sema narrowed the read: the
    slot type (Optional[str]) is threaded, so the shim renders on the bare
    binding. `_opt_view_arg_shim` pins the exact source/slot family the shim
    requires (an owned-`str` inner both sides; a `StrView` inner passes
    bare -> rejected here)."""
    if not (isinstance(a, TpyName)
            and a.name in locals_):
        return False
    return _opt_view_arg_shim(
        locals_.get(a.name),
        ptype if isinstance(ptype, TpyType) else None, analyzer)

def _own_peeled_container_str_elem(t: 'TpyType | None', analyzer) -> bool:
    """`_container_str_elem` with the binding's `Own[...]` peeled off. The
    element-family predicates reject `Own[container]` because the OWNING
    binding's ABI differs; an element READ does not -- the subscript is the
    same `__getitem__` lvalue either way."""
    tu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
          if t is not None else None)
    if isinstance(tu, OwnType):
        tu = unwrap_readonly(tu.wrapped)
    return _container_str_elem(tu, analyzer)


def _str_view_form_source(a: TpyExpr, locals_: dict[str, TpyType],
                          param_names: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """A str source whose C++ read is a BORROW (`std::string_view`), not the
    owned `std::string` an owning or element slot stores.

    Two view forms: a `StrView`-resolved binding, and a plain `str` PARAM
    (resolved `str`, but the signature spells the view). An `Own[str]` param
    and a `String` binding are STORAGE and carved out. The sema
    `strview_to_str` coerce IS the copy such a slot wants, so it peels
    first -- on ANY source shape, not only a NAME: `s[1:]` and `s.strip()`
    arrive under it exactly as a declared-StrView name does.
    """
    src = a
    if isinstance(src, TpyCoerce) and src.coercion.name == "strview_to_str":
        src = src.expr
    at = _resolved_str_value(analyzer.get_expr_type(src), analyzer)
    if at is None:
        return False
    if is_str_view_type(at):
        return True
    return (isinstance(src, TpyName) and src.name in param_names
            and _own_viewfam_param(locals_.get(src.name)) is None
            and not is_string_type(at))


def _bytes_view_form_source(a: TpyExpr, locals_: dict[str, TpyType],
                            param_names: 'set[str] | frozenset[str]',
                            analyzer) -> bool:
    """The bytes twin of `_str_view_form_source`: a span-form read (a
    `bytes` PARAM -- including a narrowed `bytes | None` deref -- or a
    `BytesView`-resolved binding) rather than the owned
    `std::vector<uint8_t>`. The `bytesview_to_bytes` coerce is that slot's
    copy and peels first; an `Own[bytes]` param is STORAGE."""
    src = a
    if isinstance(src, TpyCoerce) and src.coercion.name == "bytesview_to_bytes":
        src = src.expr
    at = _resolved_bytes_value(analyzer.get_expr_type(src), analyzer)
    if at is None:
        return False
    if is_bytes_view_type(at):
        return True
    return (isinstance(src, TpyName) and src.name in param_names
            and _own_viewfam_param(locals_.get(src.name)) is None)


class _GenericArgSlot(NamedTuple):
    """What a bare-`T` argument slot owes, answered once from the (source,
    resolved slot, callee) triple.

    `slot` is the peeled resolved type a temp would declare and `needs_temp`
    whether anything is owed at all."""
    slot: TpyType
    needs_temp: bool


def _generic_arg_slot(a: TpyExpr, raw_ptype: 'TpyType | None',
                      resolved: 'TpyType | None',
                      locals_: 'dict[str, TpyType] | None',
                      param_names: 'AbstractSet[str]', analyzer
                      ) -> '_GenericArgSlot | None':
    """The ONE verdict the three generic seams take -- the free call, the
    record method and the record ctor -- so a shape cannot owe a temp at one
    and render inline at another. None means the question does not arise:
    the declared param is not a bare `T`, or its instantiation is unknown or
    still open.

    `needs_temp` is decided at the INSTANTIATION, which is what the emitted
    slot resolves against: `param_val_or_ref_t<T>` (and a readonly method's
    `const T&`) is a CONST reference for a value-typed T, which binds a
    prvalue for the full expression exactly as the monomorphic twin's slot
    does, and the mutable `T&` for a reference-typed one, which binds no
    rvalue at all. Deciding on the OPEN T instead makes every instantiation
    pay the reference-typed one's temp. A generator or coroutine frame copies
    a value-typed instantiation into its `val_or_ref_t<T>` member inside the
    full expression, so a frame factory owes nothing more here either. A
    view-form source owes nothing at ANY `T` slot: each of the four str/bytes
    types has its own C++ type, so `param_val_or_ref_t<T>` resolves to the same
    view the monomorphic twin's slot spells, and a const slot spells
    `readonly_form_t<T>`, which is that form const-qualified only where it is a
    mutable reference.

    The verdict used to be re-asked as two separate predicates at each
    seam, which is how they could drift. Each seam still owns what is
    genuinely its own: which SOURCE shapes it renders as a temporary, its
    reject tag, and the ctor's extra copy."""
    # `resolved` is a param slot, which at a generic record can be an INT
    # type argument rather than a type -- not a slot this question is about.
    if (not _is_type_param_slot(unwrap_ref_type(raw_ptype))
            or not isinstance(resolved, TpyType)):
        return None
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(resolved)))
    if contains_type_param(slot):
        return None
    return _GenericArgSlot(slot, not slot.is_value_type())


def _str_owned_slot_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType],
                        param_names: 'set[str] | frozenset[str]',
                        analyzer, pointers: "AbstractSet[str]") -> bool:
    """A str-slice arg into an `Own[str]` container element slot -- the
    `xs.append(s)` shape `_str_pass_through_arg` rejects (Own is its cutoff). A
    str LITERAL lands bare (const char[N] -> the vector's `std::string` ctor); a
    VIEW-form source materializes an owned copy `std::string(x)` via the S1
    view->owned THIRFormConvert (the same wrap `_lower_container_elem` applies at
    literal-element positions). Two view forms qualify: a `StrView`-resolved
    local, and a `str` PARAM -- resolved `str`, not `StrView`, but the signature
    spells `std::string_view`, so its read is BORROW too. They are told apart
    from an owned `str` local (the STORAGE form that would MISS the copy) by
    `_str_name_form`'s rule: a `StrView` resolution OR the name being a
    param. An owned STORAGE source (an owned `str`/`String` local, a
    subscript-owned or call-owned result) rides the copy+move-temp
    cascade, which the bare/convert emit does not reproduce -- so it
    rejects."""
    w = _plain_own_slot(ptype)
    if w is None or not is_str_type(w):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    # The view forms are one shared rule (the coerce IS the S1 copy; the
    # render arm peels it and wraps the view read).
    if _str_view_form_source(a, locals_, param_names, analyzer):
        return True
    # A coerce over a NAME the view rule turned down is an owned source
    # wearing the copy: it rides the cascade, and must not reach the
    # rvalue tail below, which would judge the coerce rather than the name.
    if _strview_coerce_name(a) is not None:
        return False
    at = _resolved_str_value(analyzer.get_expr_type(a), analyzer)
    if at is None:
        return False
    # A NAME the view rule turned down is STORAGE (an owned local, an
    # `Own[str]` param whose signature spells the owned `std::string` by
    # value): it rides the copy+move temp cascade, not this inline convert.
    if isinstance(a, TpyName):
        return False
    # A container-ELEMENT owned-str read (`tag.append(argv[i])`): the element
    # lvalue lands bare in the element slot (`push_back(__getitem__(argv, i))`
    # -- the vector copies on insert), no cascade. The
    # receiver's Own is peeled: an owned container binding indexes through
    # the very same `__getitem__` lvalue (`__getitem__((*row), h)` for a
    # resumable frame slot, whose payload is never moved out), so ownership
    # of the buffer says nothing about how an element reads.
    if (isinstance(a, TpySubscript)
            and _borrow_elem_subscript_shape(a, locals_, analyzer,
                                             _own_peeled_container_str_elem,
                                             pointers)):
        return True
    # ... and the value-TUPLE element read of the same owned form
    # (`out.append(self._store[lk][0])`): `std::get<N>(...)` yields the
    # tuple's owned `std::string` member, an lvalue the element slot copies
    # on insert exactly like the container element above.
    if (isinstance(a, TpySubscript)
            and _tuple_subscript_value_read(a, locals_, analyzer) is not None
            and _owned_str_slot(analyzer.get_expr_type(a), analyzer)):
        return True
    # An owned-str RVALUE source (a concat binop, an owned-returning call)
    # binds the `T&&` element slot bare -- the is_temporary_expr
    # arm, no temp and no view convert. Owned LVALUES (locals, field reads)
    # keep riding the copy+move-temp cascade.
    return (not isinstance(a, TpyFieldAccess)
            and is_rvalue_source(analyzer, a))

def _bytes_owned_slot_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          param_names: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """The bytes twin of `_str_owned_slot_arg`, VIEW-form sources only (the
    literal face is its own row): a span-form source at an `Own[bytes]`
    container element slot materializes `::tpy::Bytes(x)` via the S6
    view->owned THIRFormConvert. Three faces: a bytes PARAM name (the
    signature spells `::tpy::BytesView`, so its read is BORROW --
    including a narrowed `bytes | None` param whose deref reads `(*b)`), a
    `BytesView`-resolved local, and a SLICE rvalue arriving under the sema
    `bytesview_to_bytes` coerce (the coerce IS that copy; the render arm
    peels it and wraps the view slice). An owned bytes LOCAL is STORAGE and
    keeps riding the copy+move-temp cascade.

    A `bytearray` source is NOT one of them: an owning `bytes` sink handed a
    bytearray is refused in sema, which asks for the explicit `bytes(...)`."""
    w = _plain_own_slot(ptype)
    if w is None or not is_bytes_type(unwrap_readonly(w)):
        return False
    src = a
    if isinstance(src, TpyCoerce):
        if src.coercion.name != "bytesview_to_bytes":
            return False
        src = src.expr
    # The view forms are one shared rule with the str twin.
    if _bytes_view_form_source(a, locals_, param_names, analyzer):
        return True
    if isinstance(src, TpySubscript) and isinstance(src.index, TpySlice):
        return _resolved_bytes_value(analyzer.get_expr_type(src),
                                     analyzer) is not None
    return False

def _bytes_owned_lvalue_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType],
                            param_names: 'set[str] | frozenset[str]',
                            analyzer) -> bool:
    """An OWNED-form bytes LVALUE at an `Own[bytes]` element slot binds BARE
    (`self._chunks.append(owned)` -> `push_back(owned)`,
    `acc.append(i.tag)` -> `push_back(i.tag)`): a cpp_template callee takes
    the lvalue natively and the container copies on insert, so the Own
    cascade owes no temp. The str twin at the same slot KEEPS the
    temp -- its copy is the view->owned conversion -- which is why the two
    families do not share a cell here. The movable last use is not this
    row either: `_maybe_move` fires ahead of the skip, and the shared move
    slice decides that half before the sink is walked; a FIELD read is
    never a move source, which is why the member face joins the bare row
    and not `_owned_form_bytes_name`'s move-only slot.

    The form is the shared resolved-value question, not a shape list: a
    VIEW-form member (`BytesView`) owes the materialize convert and is
    `_bytes_owned_slot_arg`'s row instead."""
    w = _plain_own_slot(ptype)
    if w is None or not is_bytes_type(unwrap_readonly(w)):
        return False
    if isinstance(a, TpyFieldAccess):
        at = _resolved_bytes_value(analyzer.get_expr_type(a), analyzer)
        return bool(at is not None and is_bytes_type(at)
                    and _witness("arg.bytes_owned_lvalue"))
    return bool(_owned_form_bytes_name(a, locals_, param_names, analyzer)
                and _witness("arg.bytes_owned_lvalue"))


def _bytes_owned_call_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                                 analyzer) -> bool:
    """An owned-bytes CALL rvalue at an `Own[bytes]` slot binds BARE
    (`self._chunks.append(bytes(initial))` ->
    `push_back(::tpy::Bytes((*initial)))`): a prvalue has nothing to
    move from and needs no conversion, so the Own cascade emits
    no temp -- the rvalue face of `_bytes_owned_lvalue_arg`. A VIEW-returning
    callee is excluded: that source still owes the view->owned materialize,
    which is `_bytes_owned_slot_arg`'s convert and a different render.
    Slot-keyed and position-blind, so the container-element and (substituted)
    generic-param slots share it."""
    w = _plain_own_slot(ptype)
    if w is None or not is_bytes_type(unwrap_readonly(w)):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    at = _resolved_bytes_value(analyzer.get_expr_type(a), analyzer)
    return bool(at is not None and is_bytes_type(at)
                and is_rvalue_source(analyzer, a)
                and _witness("arg.bytes_owned_call"))


def _bytes_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bytes-slice arg into a non-Own `bytes`/`BytesView` param slot. The
    param renders `::tpy::BytesView`; a param/view local IS a view, an
    owned local (vector) converts implicitly, and a literal takes the
    static-span pin (`::tpy::bytes_literal(...)`, lowered BORROW). The
    pin keys on the RAW ptype (`is_bytes_type(ptype) or is_bytes_view_type(
    ptype)`), so a wrapped slot (readonly/Own) rejects the literal -- it
    renders owned there, a shape this arm does not thread. An `Own[bytes]`
    slot materializes an owned copy for value args too -> reject."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    # Coerce wrappers peel before the literal span pin (a literal
    # into a BytesView slot arrives wrapped in the view coercion).
    lit = _peel_coerce(a)
    if isinstance(lit, TpyBytesLiteral):
        return is_bytes_type(pt) or is_bytes_view_type(pt)
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(pt, NominalType) and (is_bytes_type(pt) or is_bytes_view_type(pt))):
        return False
    return (_resolved_bytes_value(analyzer.get_expr_type(a), analyzer) is not None)

def _char_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A char value into a char param slot -- both spell `char`, passed bare
    (a value scalar in all but name) -- or a single-char str literal into one
    (the target-typed `'x'` char-literal render; lowered
    param-aware via `_lower_char_targeted`). A multi-char literal never
    renders as a char literal -> reject (sema rejects it anyway). An
    `Own[char]` slot is rejected conservatively (the unwrap chain does not
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
    `int_literal_to_fixed_int` coerce), and the slot threads
    into the literal render (`::tpy::BigInt(10)`) -- via the
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
    repr(v) bare (`_emit_literal`'s float arm); a float32 slot takes the
    `f` suffix via the
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
        # unary arm's resolved-dunder path to `THIRUnaryArith('-({0})', 1.0)`.
        # The inner literal
        # lowers UNtargeted (a bare double), so a float32 slot -- which needs
        # the `1.0f` suffix from a threaded target -- stays off this arm.
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

def _builtin_value_record(t: 'TpyType | None', analyzer) -> bool:
    """A BUILTIN ValueType record (tpy.coro.Waker): a TPy-IMPLEMENTED
    registry record, plain member renders -- the F1 family's builtin
    sibling. The non-native record-info guard keeps out both TypeDef-only
    value types (str / bytes, whose methods take dispatch arms) and
    @native value records (Span -- projections and element decls have
    their own gated rows)."""
    if not (isinstance(t, NominalType)
            and not t.is_user_record and not t.is_protocol
            and t.is_value_type()):
        return False
    ri = analyzer.registry.get_record_for_type(t)
    return ri is not None and not ri.is_native


def _record_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name F1-record arg into a non-Own param slot of the SAME record
    or a PARENT of it (`const A&` / `A&`): no arg lift fires for
    either pairing (`own is None`, no protocol / Optional / union / covariant
    arm -- an upcast is C++'s implicit derived-to-base reference binding), so
    the render is the bare name -- or `(*p)` for an F2 pointer-local, the
    indirect render. A narrowed
    subject's read arrives with `locals_` retyped to the member record and
    renames to its `T&` extraction alias at lowering (bare either way).
    `self` renders the receiver deref `(*this)`, like an F2
    pointer-local. An `Own[record]` slot auto-moves at last use -> its own
    rows; a `readonly[record]` slot binds the same bare name (`const T&` --
    no readonly lift exists for records, and the deref is const-blind).
    A record RVALUE (`take_rec(A(7))`) is not a
    name: it hoists into a `__tmp_N` (free calls, typed at the CHILD
    for an upcast) or inlines it (method calls -- the const-slot ctor row
    rides `_method_ctor_rvalue_arg`; the mutated-ref-param shape is the
    miscompile in BUGS.md) -- rvalues stay off this arm."""
    if not isinstance(a, TpyName):
        return False
    if a.name == "self":
        # `self` into a same/parent record slot: the receiver deref
        # `(*this)` (`on_init((*this))`), or the bare
        # `__self` frame-field read in a resumable method -- both carried
        # on the receiver read itself.
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
        # the `(*p)` deref retag at lowering, the indirect render.
        at = analyzer.get_expr_type(a)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if isinstance(at, OptionalType):
            return False  # not narrowed -- defensive, sema rejects upstream
    if not (isinstance(at, NominalType)
            and (_f1_record(at, analyzer)
                 or _builtin_value_record(at, analyzer))):
        return False
    if ptype is None or not isinstance(ptype, TpyType):
        return False
    pt = unwrap_ref_type(unwrap_send_sync(ptype))
    if isinstance(pt, OwnType):
        return False
    # A `readonly[record]` slot binds the same bare name (`const T&` --
    # there is no readonly lift for records; the F2 pointer-local
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
    it -- the resolved type is what emit reads, and an unresolved type
    here can only mis-classify the family to an auto-reject."""
    if isinstance(recv, TpyName):
        return locals_.get(recv.name)
    t = analyzer.get_expr_type(recv)
    return resolve_pending_container(t, analyzer) or t

def _indirect_field_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                                analyzer,
                                pointers: "AbstractSet[str]") -> bool:
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
        return _container_record_elem_subscript(inner, locals_, analyzer,
                                                pointers)
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
        # non-record link would carry its own unwrap.
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
                              analyzer,
                              pointers: "AbstractSet[str]") -> bool:
    """A one-level field-access method receiver `x.field.method(...)`: the field
    is a plain value F1-record off an F1-record receiver name (self / a record
    param / REF_ALIAS / F2 pointer-local, or a proven Optional-ptr borrow name
    -- `_field_receiver_ok`'s admitted set). The field's record type routes the
    call to the user-record arm, and the receiver renders bare as its own
    THIRFieldAccess (`this->field.m()` / `p->field.m()`, the `.`/`->` decided by
    that inner node) -- the outer method access is `.` (is_arrow keys on a NAME
    receiver). Container, bytearray, view, Ptr-valued and protocol/tparam fields
    route their own families over the same bare member read; an Optional field
    is deferred (it would need the outer `(*obj)` / deref_check unwrap)."""
    if _enum_prop_wrap(recv, analyzer) is not None:
        # A member's `.name` / `.value` (`e.name.lower()`): a str / scalar
        # VALUE wrapped over the member, whose own read keeps its gates;
        # the view / scalar family gates the method over it.
        return _witness("method.recv.enum_prop")
    if (getattr(recv, "dyn_getattr_call", None) is not None
            and isinstance(recv.obj, TpyName)
            and (_resolved_str_value(analyzer.get_expr_type(recv), analyzer)
                 is not None
                 or _resolved_bytes_value(analyzer.get_expr_type(recv),
                                          analyzer) is not None)):
        # The dyn-getattr sibling (`h.content_type.upper()` ->
        # `str_upper(h.__getattr__("content_type"))`): the read lowers as
        # the synthesized __getattr__ call, composing the same way.
        return _witness("method.recv.dyn_view_field")
    if not (_field_receiver_ok(recv, locals_, analyzer)
            or _indirect_field_receiver_ok(recv, locals_, analyzer, pointers)
            or _chain_field_receiver_ok(recv, locals_, analyzer)
            # A field off an explicit `Ptr[record]` binding: the pointer arm
            # renders the receiver itself (`p->field`, or
            # `::tpy::deref_check(p).field` when sema did not prove the
            # pointer), and the outer method access is `.` either way -- the
            # same composition every other admitted receiver here gets, one
            # deref down.
            or _ptr_value_field_recv_ok(recv, locals_, analyzer)
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
    # A Ptr[T]-VALUE field receiver (`self.ptr.__deref__()` ->
    # `::tpy::deref_check(this->ptr)`): the member read composes the
    # Ptr/@cpp_template family exactly as a Ptr NAME receiver does; the
    # receiver renders bare (`this->ptr` / `c.ptr`).
    if _eligible_ptr_value(ft, analyzer):
        return _witness("method.recv.ptr_field")
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
    `_subscript_yields_borrow_ptr` so the two spell the right access.
    NAME receivers only, plain (non-Own, non-Optional) record elements only
    -- an Own element carries consuming semantics this row does not model."""
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


def _tuple_str_elem_subscript_recv(recv: TpyExpr,
                                   locals_: dict[str, TpyType],
                                   analyzer) -> bool:
    """A tuple-element STR subscript method receiver (`kv[0].lower()` over a
    `tuple[str, str]` loop var): `std::get<N>` yields the element, which
    feeds the view family's receiver slot positionally
    (`::tpy::str_lower(std::get<0>(kv))`) -- the str sibling of
    `_tuple_record_elem_subscript_recv`, asking the same element question the
    CONTAINER-element str row asks. NAME receivers only. A bytes element has
    no witness here and keeps its own reject."""
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
    return (_resolved_str_value(et, analyzer) is not None
            and _witness("method.recv.tuple_str_elem"))


def _scalar_call_recv_ok(t: 'TpyType | None', analyzer) -> bool:
    """A scalar-VALUE rvalue receiver composing a stub member bare
    (`int(0).bit_length()`, `math.isqrt(x).bit_length()`,
    `(int(1) << 64).bit_length()`): ONE predicate for the ctor-call,
    marker-call and binop receiver sites, so the family cannot drift."""
    return bool(_resolved_scalar(t, analyzer)
                and _witness("method.recv.scalar_call"))


def _method_nonname_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                                analyzer,
                                pointers: 'AbstractSet[str]' = frozenset(),
                                binds_global: 'Callable[[str], bool] | None'
                                = None) -> bool:
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
        return ((_container_record_elem_subscript(recv, locals_, analyzer,
                                                  pointers)
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
                     and record_like(analyzer.get_expr_type(recv), analyzer)
                     # The CALLER'S pointer set is load-bearing here: the
                     # method receiver lowers PRECHECKED, so the getitem
                     # arm's own idx/recv recheck never runs -- a
                     # pointer-local NAME obj admitted at this gate would
                     # route the bare render where a deref is required.
                     and _record_getitem_idx_recv_ok(recv, locals_, analyzer,
                                                     pointers))
                 or _container_ref_alias_elem_subscript(recv, locals_,
                                                        analyzer, pointers)
                 or _subscript_over_container_subscript_ok(
                     recv, locals_, analyzer, pointers)
                 or _tuple_container_elem_over_subscript_ok(
                     recv, locals_, analyzer, pointers)
                 # A str-element read (`argv[i].startswith(...)`): the element
                 # lvalue feeds the native str view-method positionally, the
                 # subscript rendering as its own THIRSubscript.
                 or _borrow_elem_subscript_shape(recv, locals_, analyzer,
                                                _container_str_elem, pointers)
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
                                                      analyzer)
                 or _tuple_str_elem_subscript_recv(recv, locals_, analyzer))
                and _witness("method.recv.subscript"))
    if isinstance(recv, TpyStrLiteral):
        # A str-LITERAL receiver (`"a,b,c".split(",")`): the builtin-
        # method receiver render is the bare literal (const char[N]) prepended
        # / substituted into the resolved template;
        # `_method_receiver_type` reads the literal's str type so
        # the view arm gates the method itself.
        return _witness("method.recv.str_literal")
    if isinstance(recv, TpyBytesLiteral):
        # The bytes twin (`b"a,b,c".split(b",")`): the receiver renders
        # OWNED (`::tpy::bytes_literal_owned`), the `{self}` substitution
        # the slice arm already spells; the view family gates the method.
        return _witness("method.recv.bytes_literal")
    if isinstance(recv, TpyFString):
        # An f-string receiver (`f"<p>{n}</p>".encode()`): the interpolation
        # renders an owned-str rvalue (`std::format(...)`) that substitutes
        # into the native view-method's receiver slot exactly as the literal
        # and concat receivers do. The f-string's own parts keep gating at
        # its lowering arm, so an ineligible interpolation still rejects.
        return bool(_dot_receiver_value_kind(
            analyzer.get_expr_type(recv), analyzer) == "str"
            and _witness("method.recv.fstring"))
    if isinstance(recv, TpyBinOp):
        # A record-result dunder-binop receiver (`(dt + td).isoformat()`):
        # the postfix member chains off the parenthesized template
        # render; the receiver lowers through the binop record-dunder
        # arm (RECEIVER use), whose operand gates still apply.
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(recv))))
        return bool((_f1_record(rt, analyzer)
                     and _witness("method.recv.binop"))
                    # The scalar twin (`(int(1) << 64).bit_length()`): the
                    # stub member chains off the parenthesized binop render.
                    or _scalar_call_recv_ok(rt, analyzer)
                    # The str/bytes twin (`(a + b).upper()`): the concat
                    # rvalue substitutes into the native method template
                    # exactly as a slice rvalue does -- the same "str" family
                    # verdict the SELECT receiver row reads.
                    or (_dot_receiver_value_kind(rt, analyzer) == "str"
                        and _witness("method.recv.binop_str")))
    if isinstance(recv, TpyMethodCall):
        return _method_call_receiver_ok(recv, locals_, analyzer)
    if isinstance(recv, TpyCall):
        # A free-call-result receiver `make(3).get()`: same verdict as the
        # method-call receiver arm -- a plain non-pointer F1-record result
        # keeps `.` access (a bare render); the
        # inner call lowers via the shared free-call machinery at
        # BORROW_BIND use. Pointer / Optional / non-record results defer.
        kind = _dot_receiver_value_kind(analyzer.get_expr_type(recv), analyzer)
        if kind == "str":
            # A str/bytes-VALUE free-call result feeding a view-method (the
            # free-call twin of the literal receiver arms).
            return _witness("method.recv.str_method")
        if _scalar_call_recv_ok(analyzer.get_expr_type(recv), analyzer):
            # A scalar-VALUE call result receiver (`int(0).bit_length()`
            # -> `(::tpy::BigInt(0)).bit_length()`): the scalar family's
            # stub method composes over the bare inner render.
            return True
        if kind == "dyn":
            # A @dynamic-protocol call result: an `Own[P]` handle composes
            # `f()->m()` (the own-dyn arrow at the emit node), a borrow
            # `P&` result `f().m()` -- both off the bare inner render.
            return _witness("method.recv.dyn_call")
        if kind != "record":
            return False
        return _witness("method.recv.free_call")
    if isinstance(recv, TpyFieldAccess):
        return _method_field_receiver_ok(recv, locals_, analyzer, pointers)
    if isinstance(recv, (TpyIfExpr, TpyNamedExpr)):
        # A ternary / walrus renders as the plain C++ select / comma form
        # with `.` access. A ternary's operand renders are its own
        # lowering's (a pointer-bound operand reads `(*a)` there); a walrus
        # operand that is a pointer-local or a narrowed Optional name renders
        # through its pointer, which this bare row does not spell, so those
        # stay out.
        # Only the body's own gate threads `binds_global`; a caller without
        # the body's global seeding cannot tell a global operand apart.
        if not ((binds_global is not None
                 and _select_operands_local(recv, locals_, pointers,
                                            binds_global))
                if isinstance(recv, TpyIfExpr)
                else _select_operands_bare(recv, locals_, pointers)):
            return False
        kind = _dot_receiver_value_kind(analyzer.get_expr_type(recv), analyzer)
        if kind == "str":
            return _witness("method.recv.select_str")
        # A container select renders `.` access exactly like a record one:
        # both are one object the `?:` names.
        if kind == "record" or record_like(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(analyzer.get_expr_type(recv)))), analyzer):
            return _witness("method.recv.select_record")
        return False
    # Every other receiver kind (a non-str literal, a container literal, a
    # comprehension) has no admitted row yet.
    return False


def _dot_receiver_value_kind(t: 'TpyType | None', analyzer) -> 'str | None':
    """Which bare `.`-access receiver family a non-name receiver's TYPE puts
    it in: "str" (a str/bytes value, the view-method rows), "record" (a plain
    F1 record), "dyn" (a @dynamic protocol handle), or None. One peel chain
    for every arm that asks, so the arms cannot drift apart on it."""
    if (_resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None):
        return "str"
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    if isinstance(rt, NominalType) and _f1_record(rt, analyzer):
        return "record"
    if isinstance(rt, NominalType) and is_dyn_protocol(rt):
        return "dyn"
    return None


def _select_operands_bare(recv: TpyExpr, locals_: dict[str, TpyType],
                          pointers: 'AbstractSet[str]') -> bool:
    """Every value operand of a ternary / walrus tree is a name that renders
    bare (declared, not a pointer-local, not an Optional binding that a
    narrowing may have retyped) or a non-name operand. A pointer-bound
    operand spells through its pointer, which this row does not render."""
    if isinstance(recv, TpyIfExpr):
        return (_select_operands_bare(recv.then_expr, locals_, pointers)
                and _select_operands_bare(recv.else_expr, locals_, pointers))
    if isinstance(recv, TpyNamedExpr):
        return _select_operands_bare(recv.value, locals_, pointers)
    if isinstance(recv, TpyName):
        if recv.name not in locals_ or recv.name in pointers:
            return False
        dt = unwrap_readonly(unwrap_ref_type(locals_[recv.name]))
        return not isinstance(dt, OptionalType)
    return True


def _select_operands_local(recv: TpyExpr, locals_: dict[str, TpyType],
                           pointers: 'AbstractSet[str]',
                           binds_global: Callable[[str], bool]) -> bool:
    """Every name operand of a ternary tree is a body-local binding. A module
    global operand stays out: its select render has no witness as a
    receiver. A walrus operand keeps the bare-operand rule."""
    if isinstance(recv, TpyIfExpr):
        return (_select_operands_local(recv.then_expr, locals_, pointers,
                                       binds_global)
                and _select_operands_local(recv.else_expr, locals_, pointers,
                                           binds_global))
    if isinstance(recv, TpyName):
        return recv.name in locals_ and not binds_global(recv.name)
    return _select_operands_bare(recv, locals_, pointers)

def _method_call_receiver_ok(recv: TpyMethodCall, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A method-call method receiver `a.b().c()`: the inner call `a.b()` yields
    a plain non-pointer, non-Optional F1-record borrow (`Box.get()` -> `T&`),
    so the outer access renders `.`: the receiver is not a name / pointer /
    Optional-ptr / own-dyn / borrow-`T*` tuple element, so the outer node
    keeps `is_arrow` False
    (keyed on a NAME receiver). The inner call renders via the shared method
    lowering (`_lower_expr`).
    A pointer / Optional / non-record inner result reads `->` or the `(*obj)`
    unwrap instead, and rejects."""
    if _resolved_str_value(analyzer.get_expr_type(recv), analyzer) is not None:
        # A str-VALUE method-call result (`s.strip().lower()`): the inner str
        # method renders bare (`::tpy::str_strip(...)`) and feeds the outer
        # str view-method's receiver slot positionally -- both native
        # free-function str methods composing as nested calls.
        return _witness("method.recv.str_method")
    if _scalar_call_recv_ok(analyzer.get_expr_type(recv), analyzer):
        # The scalar twin (`math.isqrt(x).bit_length()`): the qualified
        # call's scalar result composes the stub member the same way.
        return True
    if _resolved_bytes_value(analyzer.get_expr_type(recv), analyzer) is not None:
        # The bytes twin (`srv.recv(32).decode()` ->
        # `::tpy::bytes_decode(srv.recv(32))`): the inner bytes-returning
        # method feeds the outer bytes method's receiver slot positionally.
        return _witness("method.recv.bytes_method")
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    if isinstance(rt, NominalType) and record_like(rt, analyzer):
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
    # dispatch, or a structural template ref), so the outer method call
    # composes the same way.
    if _protocol_binding(rt) is not None:
        return _witness("method.recv.protocol")
    return False

def _recv_shape_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                       analyzer) -> str:
    """Drilldown label for a non-admitted method receiver -- names *which*
    receiver shape blocks (the method.receiver_shape total is
    first-reject-masked: one-level
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
            # Any nominal no receiver family claims -- a non-F1 / native /
            # generic record, but also a nominal CONTAINER whose family the
            # field gate does not carry.
            return "method.recv.field_nonf1"
        return "method.recv.field_nonrecord"  # container / str / tuple / ...
    return "method.recv.other"

def _marker_reject(e: TpyMethodCall, analyzer) -> str:
    """Drilldown label for a marker-rejected method call -- names WHICH
    special-emit marker fires (each takes a different method-call arm)
    so the reject names the arm instead of one
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
    """Sub-split of a generic static call by what its spelling needs:
    the cpp_template expansion, NO explicit type args
    (the `if not expr.inferred_type_args` arm -- static_method_callee_cpp,
    the spelling the plain slice already carries), or explicit `<T>`
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
    """Sub-split of a Deref-chain method call by its emit arm:
    the narrowed-payload cast, the Ptr[T]
    receiver arm (`p->m` / `::tpy::deref_check(p).m` -- no `.__deref__()`
    spelling), the builtin arm (cpp_template / native fi), or the plain
    member tail
    (`recv.__deref__()...m(args)` over the arg loop) -- split by
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
    consumed by lowering (the `_free_callee_kind` analog for the
    method-call marker arms): ("qualified", callee_cpp) the
    `<spelling>(args)` render whose args are the full first-pass arg
    loop (plain cross-module calls, via `module_qualified_callee_cpp`;
    plain static methods, via `static_method_callee_cpp`; and plain
    MODULE-qualified statics, via `module_static_class_cpp` -- all
    interpolating the same loop: the Own move cascade fires); ("native",
    symbol) the `::symbol(args)` render for a bare-@native cross-module
    callee -- the same loop but with `inline_template` set (`_is_native_
    stub`), which skips the Own copy-temp, so Own-slot args are rejected by
    the caller; ("template", tmpl) a same-module static `@cpp_template`
    call (`uint32.trunc(i)`) whose positional-only template expands over
    the builtins arg loop -- generic statics
    included, since the no-{T} template makes the type-arg
    substitution a no-op. None = an
    emit arm the slice does not reproduce (super / typed-dict / macro /
    deref markers, module statics' cpp_template/native/non-default-linkage
    rows, `<T>`-spelled generics, ctors,
    extern-C / @native_c raw symbols, `function=True` natives whose args
    render slot-BLIND, non-static cpp_template and
    builtin-module arms)."""
    # A call sema typed as its concrete frame is a factory wherever it
    # lands (see builds_named_frame).
    generator_ok = generator_ok or builds_named_frame(e, analyzer)
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Every OTHER special marker takes its own method-call arm.
    # EXPLICIT type args are rejected here; INFERRED ones flow to the
    # per-branch generic decisions below.
    if (e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None
            # EXPLICIT type args are rejected -- EXCEPT where sema already
            # folded the explicit spelling into the inferred list and the
            # arm renders from `inferred_type_args` alone: a STATIC call
            # (`Poll[Probe].ready(..)`), a module-qualified generic
            # (`helpers.identity[int32](x)` -- the user-module generic arm),
            # and a builtin-module template (`tpy.unsafe.unsafe_cast[U](p)`
            # -- the type-arg substitution). The PREFIX pin
            # keeps any diverging explicit list out: a partial explicit
            # spelling (`unsafe_cast[uint32](p)` giving one of [T, U]) folds
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
    # @cpp_template TYPE ctor (`tpy.int32(10)`): its arm is the same
    # template/native expansion the static template takes
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
        # (the super/unbound-self arms; sema strips `self` from
        # the unbound form's args). The parent spells via its own `to_cpp()`,
        # so an F1 base and a generic instance
        # (`Base<T>` inside a generic child) are both safe. The GENERIC
        # method form (`this->Base<T>::template m<U>(args)`) rides its own
        # kind so lowering composes the targs via lc.render_type; native /
        # cpp_template spellings reject.
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
            # with no targs (`::tpyapp::m::Cls::m(args)` -- the same tail
            # with empty targs/template_kw), so it rides the
            # "qualified" kind. A cpp_template plain fi takes the
            # native/template arm (different arg render) -- excluded.
            if not isinstance(e.obj, TpyFieldAccess):
                return None
            if getattr(e, "representational_subst_params", None):
                return None
            if not e.inferred_type_args:
                if (fi.cpp_template is not None or fi.native_function
                        or fi.linkage != FunctionLinkage.DEFAULT):
                    return None
                cpp_class = module_static_class_cpp(
                    analyzer.registry, e.user_module_call, e.obj.field,
                    owner=e.static_call_owner)
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
        # A @cpp_template static takes the builtins arm (the
        # native/template block precedes the `Class::m` arm):
        # the template expands over
        # inline-template args; a GENERIC static template
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
            # `_static_repr_subst_cpp` spelling). A NATIVE record's static
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
        # `tpy.int32(10)`): every @cpp_template branch of the
        # builtin-module arm expands the template with
        # the RESOLVED fi, i.e. the same expansion the same-module static
        # template takes. The @native branches render a receiver-threaded or
        # plain arg loop instead and stay out.
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
        # `function=True` natives take the slot-blind arg
        # render instead -- a different loop, rejects.
        # Targ-blind like the free-call native arm: a native import never
        # spells explicit template args.
        if fi.native_function:
            return None
        return ("native", fi.native_name or fi.name)
    if (fi.is_native_c or fi.is_extern_c) and not fi.native_function:
        # @native(binding="C") / @export callee (`lib.c_mul(6, 7)` ->
        # `::tpyapp::lib::c_multiply(6, 7)`): the extern "C" declaration
        # lives in the module namespace, so the spelling is the
        # module-qualified NATIVE symbol over the same lazy arg
        # loop the plain dotted call uses -- the existing qualified kind.
        return ("qualified", qualified_cpp_name(e.user_module_call,
                                                fi.native_name or fi.name))
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
                          record_ret_ok: bool = False,
                          moved_ret_ok: bool = False,
                          iterable_gen_ok: bool = False,
                          owned_tuple_ret_ok: bool = False,
                          own_tuple_slot_ret_ok: bool = False,
                          storage_ret_ok: bool = False,
                          value_opt_ret_ok: bool = False,
                          coro_factory_ok: bool = False,
                          iterable_ret_ok: bool = False) -> bool:
    """Result/arg checks for a `_marker_call_kind`-classified receiver-less
    call. Mirrors free-call lowering's value-position result set and its arg
    rows MINUS the free-loop-only ref-temp hoist (`_record_rvalue_temp_arg`:
    the method-call loop's hoist condition is protocol/TypeParamRef only, so
    a record rvalue into a concrete ref slot renders differently) -- and,
    for the "native" kind, minus the Own rows (`inline_template` skips the
    copy-temp for a non-last-use lvalue). Union lifts are admitted dcbp-BLIND:
    the method-call loop takes no deep-const verdict, so lowering passes
    readonly_target=False to match."""
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
            or (record_ret_ok and record_like(ret, analyzer))
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
            # The owning `Own[tuple]` ARG slot (`poll_ready(
            # sock._accept_nonblocking())`): the prvalue binds the by-value
            # slot bare. A DIFFERENT position from the unpack source above --
            # that one captures `auto __tup_N =` and moves per element -- so
            # it takes its own flag over the same result predicate.
            or (own_tuple_slot_ret_ok
                and _owned_tuple_call_ret(ret, analyzer) is not None
                and _witness("method.qualcall.own_tuple_slot"))
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
            # stay out (storage/borrow duality; no witness).
            or (stmt_position and _moved_record_ret(ret, analyzer)
                and _witness("method.qualcall.record_discard"))
            # A DISCARDED container result (`os.listdir(path);` for its
            # errors alone): no consumer exists, so the storage/borrow
            # duality that keeps containers out of the value positions
            # cannot bite -- the render is the same bare call statement.
            or (stmt_position and _nonvalue_container_ret(ret)
                and _witness("method.qualcall.container_discard"))
            # A DISCARDED wrapper-union result (`json.load(fp)` called for
            # its raise alone): the same bare call statement -- the by-value
            # wrapper dies at the semicolon, exactly like the
            # record and container discard rows above.
            or (stmt_position
                and _wrapper_union_like(_unwrap_own(unwrap_readonly(
                    unwrap_ref_type(unwrap_send_sync(ret)))), analyzer)
                is not None
                and _witness("method.qualcall.union_discard"))
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
    """A container FIELD read at a plain container ref slot
    (`heapq.heappush(self.heap, ...)` -> `heappush<T>(this->heap, ...)`):
    the member read binds the `std::vector<T>&` slot directly -- bare, the
    field twin of `_container_pass_through_arg`'s bare-NAME
    row. The bind is by REFERENCE and the render carries no callee-shaped
    decoration, so the row is the same cell at every callee family that
    admits the bare-name twin. Keyed on the DECLARED field type being a
    bare container of the
    slot's family (a narrowed `Optional[container]` field types its
    occurrence at the member and would need an unwrap).
    The slot may be the callee's still-generic `list[T]` -- it threads
    RAW, and no wrap arm claims a plain container slot, so admission
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
    # Same reference-container family on both sides -- the TypeDef identity,
    # because it is the family that fixes the C++ spelling the bare member
    # read has to bind (`bytes(self.buffer)` -> `Bytes(this->buffer)`).
    # `bytearray` is NOT paired with `bytes`: that crossing is the view
    # coerce, which arrives as its own node.
    ok = (_f1_container_ref(ftu) and _f1_container_ref(pt)
          and type_def_of(ftu) is type_def_of(pt))
    return bool(ok and _witness("arg.container_field"))


def _container_module_var_arg(a: TpyExpr, ptype: 'TpyType | None',
                              locals_: dict[str, TpyType], analyzer) -> bool:
    """A module-variable container at a plain container ref slot
    (`take(sys.argv)` -> `take((*::tpystd::sys::argv))`): the read binds the
    `std::vector<T>&` slot directly, bare -- the module-var twin of
    `_container_field_pass_arg`, keyed the same way on the two sides being
    the same container family. Own / Optional / Span / protocol slots keep
    their own lifts, so they stay out; the read spelling itself is the
    registered one the module-var arm renders."""
    if not isinstance(a, TpyFieldAccess):
        return False
    if not _module_var_recv(a, locals_, analyzer):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a))))
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    return (_f1_container_ref(at) and _f1_container_ref(pt)
            and type_def_of(at) is type_def_of(pt))


def _r_container_module_var(req: _ArgReq) -> bool:
    return _container_module_var_arg(req.a, req.ptype, req.locals_,
                                     req.analyzer)


def _record_field_marker_arg(a: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer,
                             narrowed: 'AbstractSet[str]' = frozenset()
                             ) -> bool:
    """An F1-record FIELD read at a plain record ref slot
    (`::mylog::log_dispatch(mod._logger, ...)`, `self.soak(other.inner)`):
    the member read binds the
    `T&` / `const T&` slot directly -- bare, the field twin
    of `_record_pass_through_arg`'s bare-NAME row and the record sibling of
    `_container_field_pass_arg`. The bind carries no callee-shaped
    decoration, so the marker and user-record-method families share the
    cell. Keyed on the DECLARED field type, so a
    narrowed `Optional[record]` field (whose occurrence types at the
    member) keeps needing the unwrap -- except through a user-Deref hop
    (`_bare_field_read_shape`'s "deref"), where the field lives on the
    DEREFFED record and only the read's own resolved type names it.
    `Own[T]` and Optional slots keep their own cascades -- the Own slot
    copies the field through a `__tmp_N`."""
    shape = _bare_field_read_shape(a, locals_, analyzer, narrowed)
    if shape is None:
        return False
    deref_hop = shape == "deref"
    # A Deref hop puts the field on the DEREFFED record, which
    # `_field_decl_type` (it peels the receiver's own type) cannot answer,
    # so that shape types on the read's own resolved type.
    ft = (analyzer.get_expr_type(a) if deref_hop
          else _field_decl_type(a, locals_, analyzer))
    ftu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
           if isinstance(ft, TpyType) else None)
    if not (isinstance(ftu, NominalType) and _f1_record(ftu, analyzer)):
        return False
    pt = (unwrap_ref_type(unwrap_send_sync(ptype))
          if isinstance(ptype, TpyType) else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType)):
        return False
    pt = unwrap_readonly(pt)
    if not (pt == ftu or analyzer.registry.is_subclass_of(ftu, pt)):
        return False
    return _witness("arg.record_deref_field" if deref_hop
                    else "arg.record_field_marker")


def _value_tuple_field_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                                locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """A VALUE-tuple FIELD read at the same value-tuple ref slot
    (`::tpy::deref_check(this->_sock).connect(this->_addr)` at
    `tuple[str, int32]`): borrow and storage forms coincide for a value
    tuple, so the member read binds the `const std::tuple<...>&` slot
    directly -- bare, the tuple sibling of
    `_container_field_pass_arg` / `_record_field_marker_arg`. A
    POINTER-REPR tuple field lifts through `tuple_to_pointer` instead,
    which is why the key is the value-tuple predicate rather than "a tuple
    field"; Own/Optional slots are not tuples and fall out of the same
    predicate, keeping their own cascades. Keyed on the DECLARED field
    type, so a narrowed `Optional[tuple]` field (whose occurrence types at
    the member) keeps needing the unwrap."""
    if not isinstance(a, TpyFieldAccess):
        return False
    if not _field_receiver_ok(a, locals_, analyzer):
        return False
    ft = _field_decl_type(a, locals_, analyzer)
    ftu = _value_tuple(ft, analyzer) if isinstance(ft, TpyType) else None
    if ftu is None:
        return False
    return bool(_value_tuple(ptype, analyzer) == ftu
                and _witness("arg.value_tuple_field"))


def _btuple_literal_marker_arg(a: TpyExpr, ptype: 'TpyType | None') -> bool:
    """A tuple LITERAL at a marker callee's pointer-repr tuple slot
    (`::mylog::log_dispatch(h, fmt, (defer_str(tag), i))` at
    `tuple[Ref[DeferredStr], int32]`): the borrow builder renders it --
    lvalue elements lift `&(...)`, rvalue elements ride the
    `tuple_value_to_borrow` source tuple, whose full-expression lifetime
    covers the call. Element SHAPES are the builder's own verdict:
    anything outside its slice raises and falls the body back. The plain /
    method arg loops already carry this row; only the marker gate lacked
    it."""
    if not isinstance(a, TpyTupleLiteral):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not (isinstance(pt, TupleType) and pt.has_pointer_repr_element()):
        return False
    if len(a.elements) != len(pt.element_types):
        return False
    return _witness("arg.btuple_literal_marker")


def _borrow_ret_record_marker_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  analyzer) -> bool:
    """A BORROW-returning (`T&`) F1-record call at a marker callee's plain
    record ref slot (`::mylog::log_dispatch(svc.get_logger(), ...)`): the
    qualcall arg loop renders the call bare in place and its `T&` result
    binds the `T&` slot directly -- no temp, no copy. An RVALUE source
    takes `_native_record_call_arg`'s row instead, and an `Own[T]` slot
    copies the result through a `__tmp_N` (excluded, as
    are Optional / union slots with their lifts)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    fi = a.resolved_function_info
    if fi is None or not call_returns_cpp_ref(analyzer, fi):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if pt is None or isinstance(pt, (OwnType, OptionalType, UnionType)):
        return False
    rt = analyzer.get_expr_type(a)
    rtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
           if rt is not None else None)
    if not (isinstance(rtu, NominalType) and _f1_record(rtu, analyzer)):
        return False
    if not (pt == rtu or analyzer.registry.is_subclass_of(rtu, pt)):
        return False
    return _witness("arg.record_borrow_ret_marker")


def _own_tparam_call_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A T-returning CALL RVALUE at an OPEN `Own[T]` slot of the SAME T
    (`self._storage.init(ui, other._storage.take(ui))` inside a generic
    record body, `poll_ready(self._state._pop())` at a generic free callee,
    `self.push(copy(item))` / `self.push(make_default())`): the prvalue
    binds the `T&&` slot bare -- the Own cascade's rvalue tail -- and the
    same-T pairing mirrors the generic free lane's open-slot rule. Free and
    method callees alike, since an open T fixes the render at instantiation
    and the callee kind cannot change it. A BORROW-returning callee stays
    out on its DECLARED return type: `is_rvalue_source` answers True for
    both, so it cannot discriminate them, and binding a borrowed
    `val_or_ref_t<T>` result straight into the `T&&` slot is ill-formed at
    any reference-type instantiation, so nothing may route it. The nested
    call validates itself at its own lowering."""
    slot = unwrap_send_sync(ptype) if isinstance(ptype, TpyType) else None
    if not isinstance(slot, OwnType):
        return False
    st = unwrap_readonly(slot.wrapped)
    if not isinstance(st, TypeParamRef):
        return False
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    at = analyzer.get_expr_type(a)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if isinstance(atu, OwnType):
        atu = unwrap_readonly(atu.wrapped)
    return bool(isinstance(atu, TypeParamRef) and atu.name == st.name
                and is_rvalue_source(analyzer, a)
                and _own_declared_call_ret(a)
                and _witness("arg.own_tparam_call_rvalue"))


# The marker kinds whose arg loop carries Own slots. The `native` kind
# renders through the same loop with `inline_template` set, which skips the
# Own copy temp, and `template` expands over the builtins loop instead --
# neither has an Own render, so neither carries those rows.
_MARKER_OWN_SLOT_KINDS = ("qualified", "generic_qualified", "generic_static",
                          "generic_module_static", "super_generic")


def _marker_call_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                        kind: 'tuple[str, str]',
                        locals_: dict[str, TpyType], analyzer, *,
                        temps_ok: bool,
                        narrowed: 'set[str] | frozenset[str]',
                        param_names: 'AbstractSet[str]'
                        = frozenset(),
                        index: int = -1,
                        overload=None,
                        movable_locals: 'set[str] | frozenset[str]' = frozenset(),
                        func_name: 'str | None' = None) -> bool:
    """The receiver-less marker-call families' arg rows.

    THREE families, not one, and `kind[0]` picks which: a `template` callee
    expands over the builtins arg loop and admits only the shared
    pass-through set; a `native` callee runs the qualcall loop with
    `inline_template` set and so carries every row except the Own-slot ones;
    a qualified callee (plain, generic, module-static or super) carries all
    of them. Rows: `_MARKER_TEMPLATE_ARG_SINK` / `_MARKER_NATIVE_ARG_SINK` /
    `_MARKER_QUALIFIED_ARG_SINK`.
    """
    if kind[0] == "template":
        sink = _MARKER_TEMPLATE_ARG_SINK
    elif kind[0] in _MARKER_OWN_SLOT_KINDS:
        sink = _MARKER_QUALIFIED_ARG_SINK
    else:
        sink = _MARKER_NATIVE_ARG_SINK
    # The signature-reading cells see a callee only on the QUALIFIED kind:
    # that is the one whose callee is TPy code, so its per-parameter const
    # facts exist. A @native / @cpp_template callee has none, and handing one
    # in would turn "unknown" into a decline for the cells that read them.
    qualified = sink is _MARKER_QUALIFIED_ARG_SINK
    return arg_ok(sink, a, ptype, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed,
                  temps_ok=temps_ok, movable_locals=movable_locals,
                  index=index if qualified else -1,
                  overload=overload if qualified else None,
                  func_name=func_name)


def _dyn_own_handle_arg(a: TpyExpr, ptype: 'TpyType | None',
                        locals_: dict[str, 'TpyType'],
                        analyzer) -> 'NominalType | None':
    """A BOUND coroutine-handle NAME into an `Own[@dynamic P]` slot
    (`asyncio.create_task(c)`): the dyn-Own handle face
    -- `::tpy::make_adapter<Base>(std::move(*(c)))`, the optional-slot
    unwrap moved into the adapter. Returns the slot protocol, or None.
    An already-ERASED source (an `Own[Cancellable]` param forwarded)
    renders without the re-wrap -- a different face, sliced out."""
    if not isinstance(ptype, TpyType) or not isinstance(a, TpyName):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, like the dyn-protocol arg key: a
    # ReadonlyType wrapper defeats is_dyn_protocol, so an `Own[readonly[P]]`
    # slot never takes the adapter render -- unwrapping
    # here would admit a shape this row does not spell.
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
    (`asyncio.run(main_coro())`): the dyn-Own erasure
    boundary -- `::tpy::make_adapter<Base>(factory(args))`, Base spelled
    from the SLOT protocol (`dynamic_base_name`).
    Returns that protocol, or None. Free-call factories only: a
    BOUND handle name takes the `std::move(*(x))` optional-slot unwrap
    (`_dyn_own_handle_arg`) and a method coro threads its receiver -- a
    different render, so both reject. The factory itself must classify
    plain/imported
    (`coro_factory_ok`); its own args are judged by the free-call loop at
    lowering."""
    if not isinstance(ptype, TpyType) or not isinstance(a, (TpyCall,
                                                            TpyMethodCall)):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, matching the handle-arg key (see _dyn_own_handle_arg): an
    # `Own[readonly[P]]` slot never takes the adapter render.
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
        # (`asyncio.run(b.take())`): either render wraps in the
        # same adapter -- the method call spells inline, its receiver
        # riding the ordinary method-call arm. The marker lane's
        # dyn-protocol result rides the coro_factory_ok escape; a member
        # factory's receiver/arg shapes gate at its own lowering.
        if (_marker_call_kind(a, analyzer, coro_factory_ok=True) is None
                and not fi.is_method):
            return None
        return proto
    k = _free_callee_kind(a, analyzer, coro_factory_ok=True)
    if k is None or k[0] not in ("plain", "local", "imported"):
        return None
    return proto

def _dyn_own_conformer_arg(a: TpyExpr, ptype: 'TpyType | None',
                           locals_: dict[str, 'TpyType'],
                           analyzer) -> 'tuple[NominalType, str] | None':
    """A concrete-CONFORMER source into an `Own[@dynamic P]` slot: the
    `std::make_unique<U>(x)` (inheritance) / `::tpy::make_adapter<Base>(x)`
    (structural) wrap of the dyn-Own arg, keyed by the SHARED
    `classify_dyn_own_arg` verdict so gate and render cannot drift. Admitted
    shapes: a user-record CTOR rvalue and a record-typed local NAME
    (`_maybe_move` renders `std::move` at a movable last use -- the
    lowering arm keys it on `_is_move_source`). Returns
    (slot protocol, verdict) or None; the coro-handle / async-factory /
    forward verdicts keep their own rows."""
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_send_sync(ptype)
    if not isinstance(u, OwnType):
        return None
    # RAW wrapped, matching the handle-arg key (see _dyn_own_handle_arg): an
    # `Own[readonly[P]]` slot never takes the adapter render.
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
    # through, and then rejecting is the safe answer.
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
    """The own-dyn receiver test for name/call receivers:
    True when the receiver renders as `std::unique_ptr<P>` (abstract
    @dynamic P), so the member access spells `->`. The subscript-element
    case (an `Own[P]` container element) is not covered -- such
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
    the covariant-arg typed temp (`Box<Shape> __tmp_N = std::move(bc);`
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
            and not _f1_container_ref(t))


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
    (`cell.release_strong()` on `cell: Ptr[Cell]`): the method-call
    pointer tail renders `p->m(args)` when sema proved the pointer non-null
    (`e.ptr_non_null`) and `::tpy::deref_check(p).m(args)` otherwise -- the
    existing THIRMethodCall is_arrow / deref_check renders; a pointer
    receiver never spells the `.__deref__()` chain. The ONE discriminator
    consumed by lowering; the caller adds receiver-shape and arg/result
    admission on top (the qualified-marker rows: the same
    first-pass arg loop, Own cascade included). Rejected here: any other marker,
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
    name declared as an eligible `Ptr[T]` value binding (renders raw --
    Ptr locals are never indirect or assign-narrowed),
    or an admitted field read whose value is such a Ptr (`self._cell.m()`
    -- the F1 field-read render; sema's interior unwrap already happened in
    get_expr_type)."""
    if isinstance(e.obj, TpyName):
        return (e.obj.name in locals_
                and _eligible_ptr_value(locals_[e.obj.name], analyzer))
    if isinstance(e.obj, TpyFieldAccess):
        return (_eligible_ptr_value(analyzer.get_expr_type(e.obj), analyzer))
    return False


def _ptr_template_call_recv_ok(e: TpyMethodCall, analyzer) -> bool:
    """A CALL receiver whose result is an eligible `Ptr[T]` value, feeding a
    `@cpp_template` member (`self._storage.ptr().span(n)` ->
    `std::span(this->_storage.ptr(), ...)`). The template expands over the
    receiver's own render, so an rvalue `T*` interpolates exactly like the
    NAME and FIELD receivers already admitted -- there is no member access
    through the pointer to deref-check. The receiver call's own shape is
    validated when it lowers recursively."""
    if not isinstance(e.obj, (TpyCall, TpyMethodCall)):
        return False
    if e.needs_optional_runtime_check:
        return False
    fi = e.resolved_function_info
    if fi is None or fi.cpp_template is None:
        return False
    return _eligible_ptr_value(analyzer.get_expr_type(e.obj), analyzer)


def _native_record_recv(t: 'TpyType | None', analyzer) -> bool:
    """A @native record receiver (`Vec[int32]` over std::vector): its
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
    (sema's `ptr_non_null`, the test the is_pointer arm
    reads). Consumed by the admission gate below (which adds its own
    type_params / args rejects) and by the method tail's deref_check render
    flag, so the two sides cannot drift on the core."""
    return (recv_ptr and fi is not None and fi.cpp_template is None
            and bool(fi.native_name) and not fi.native_function
            and isinstance(e.obj, TpyName) and not e.ptr_non_null)


def _ptr_template_method_supported(
        e: TpyMethodCall, fi, recv_type: 'TpyType | None', analyzer,
        locals_: dict[str, TpyType], pointers: 'AbstractSet[str]', *,
        stmt_position: bool, record_ret_ok: bool,
        record_template: bool = False) -> bool:
    """The EXPLICIT `@cpp_template` method call on a `Ptr[T]` value receiver
    (`p.__deref__()` -> `::tpy::deref_check(p)`): the general
    builtin-method arm expands the template
    over the bare receiver render, no `{self}`-deref (a Ptr local / field is
    never indirect at deref_depth 0). Restricted to the zero-arg,
    positional-only template shape sema resolves for `Ptr.__deref__`; a
    `{cpp}`/type-param template (none on Ptr) or an arg-taking builtin method
    (`.span(n)`) rejects. The result rides the plain-method THIRMethodCall's
    `cpp_template` arm; its value set mirrors the container arm's."""
    if recv_type is None or not (recv_type.is_pointer()
                                 or _native_record_recv(recv_type, analyzer)
                                 # An EXPLICITLY spelled dunder on a user
                                 # record (`self.__eq__(other)` inside
                                 # `__ne__`): sema stamps every user dunder
                                 # with its C++ operator template, so that
                                 # template expands over the bare
                                 # receiver render just as it does for a Ptr.
                                 # A POINTER-bound record receiver stays out
                                 # -- its `{self}` render is the deref, which
                                 # this arm does not spell.
                                 or record_like(recv_type, analyzer)):
        return note_detail("method.ptr_template.recv_shape")
    if fi.cpp_template is not None:
        if "{cpp}" in fi.cpp_template or fi.type_params:
            return note_detail("method.ptr_template.template_shape")
        if fi.native_function or fi.native_name or e.inferred_type_args:
            return note_detail("method.ptr_template.template_kind")
    elif (fi.native_function and fi.native_name
          and not recv_type.is_pointer()):
        # `@native("sym", function=True)` on a @native-record receiver
        # (`v.pop_last()` -> `::tpy::pop_back(v)`): the general
        # THIRMethodCall's receiver-prepend arm, the view family's
        # spelling on a record receiver. Scalar positional args only
        # (the shared check below); generics reject.
        if fi.type_params or e.inferred_type_args or e.kwargs:
            return note_detail("method.ptr_template.native_generic")
    else:
        # A plain @native MEMBER on a `Ptr[record]` NAME receiver -- the
        # deref-check face only (`::tpy::deref_check(s).outer()`,
        # `s.outer.inner.flag`'s first hop); the proven `s->outer()`
        # spelling is unwitnessed and rejects. The shared core lives in
        # `_ptr_native_member_core`; the generic/args rejects are this
        # gate's own.
        if not (_ptr_native_member_core(e, fi, recv_type.is_pointer())
                and not fi.type_params
                and not e.args and not e.inferred_type_args):
            return note_detail("method.ptr_template.native_member")
    if e.args and not all(_ptr_template_arg_ok(a2, analyzer, locals_,
                                               pointers,
                                               record_template=record_template)
                          for a2 in e.args):
        return note_detail("method.ptr_template.arg_shape")
    ret = analyzer.get_expr_type(e)
    return (_resolved_scalar(ret, analyzer)
            or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            # A Span result is a by-value view landing bare in any admitted
            # sink (`s: Span[int32] = p.span(3)`).
            or (_span_value(ret) and _witness("method.ptr_template_span"))
            # ... and its OPEN-element sibling (`p.span(n)` inside a generic
            # body): the template expansion is element-blind and a span is a
            # value view, so the result renders bare per instantiation.
            or (_span_open_t_value(ret)
                and _witness("method.ptr_template_span_open_t"))
            # An OPEN-T tuple result (`tuple[bool, T]` off a @native CAS):
            # borrow and storage coincide at the tuple level, so the result
            # lands as the plain spelled copy -- the protocol ladder's row.
            or (_open_t_tuple_slot(ret, analyzer) is not None
                and _witness("method.ptr_template_open_t_tuple_ret"))
            or (record_ret_ok and record_like(ret, analyzer))
            or (stmt_position and (ret is None or is_void_like_type(ret)))
            or note_detail("method.ptr_template.ret_type"))


def _ptr_template_arg_ok(a: TpyExpr, analyzer,
                         locals_: dict[str, TpyType],
                         pointers: 'AbstractSet[str]', *,
                         record_template: bool = False) -> bool:
    """One positional arg of the ptr / @native-record template family.

    The arg interpolates into the template's positional slot (or the
    prepended-receiver native call), so only shapes that render as a
    bare expression belong here: a scalar (`p.span(3)` ->
    `std::span(p, static_cast<size_t>(3))`, the cast living in the template),
    an ENUM value (the single `enum_cpp_name` spelling), or an OPEN
    type-param value (a bare `T` slot takes no borrow/storage lift, so its
    render is the name). Everything else rejects."""
    at = analyzer.get_expr_type(a)
    return bool(_resolved_scalar(at, analyzer)
                or (_eligible_enum(at, analyzer) is not None
                    and _witness("method.ptr_template_enum_arg"))
                or (_is_type_param_slot(at)
                    and _witness("method.ptr_template_tparam_arg"))
                # A str-family NAME at a USER-RECORD dunder template
                # (`other` in `({self}) == ({0})`): the binding renders as the
                # bare name. NAME only -- a str literal at a
                # template slot leaves the `param_view_t` pin question open,
                # which no template expansion answers here. Off for
                # the Ptr / @native-record receivers: their view-family args
                # respell through the view rows, which this bare interpolation
                # does not build.
                or (record_template and isinstance(a, TpyName)
                    and _resolved_str_value(at, analyzer) is not None
                    and _witness("method.ptr_template_str_name_arg"))
                # ... and a RECORD NAME at that same user-record dunder
                # template (`a.__add__(b)` -> `(a) + (b)`): the binding is a
                # `const T&` param or an owned local, so the interpolated
                # render is the bare name. In-scope, non-pointer names only
                # -- a pointer-bound record reads `(*p)`, which this bare
                # interpolation does not spell.
                or (record_template and isinstance(a, TpyName)
                    and a.name in locals_ and a.name not in pointers
                    and record_like(at, analyzer)
                    and _witness("method.ptr_template_record_arg")))


def _value_opt_scalar_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """A scalar-valued arg into a value-repr `Optional[scalar]` element slot
    (`items.append(int32(1))` on `list[int32 | None]` -> `push_back(1)`):
    the scalar renders bare and std::optional's converting ctor
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


def _wrapper_union_elem_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 locals_: dict[str, TpyType],
                                 narrowed: 'AbstractSet[str]',
                                 analyzer) -> bool:
    """A same-wrapper-union NAME at a wrapper-union container ELEMENT slot
    (`a.append(item)` / `d[k] = item` on a `JsonValue` container). The
    wrapper is a by-value struct the insert takes bare -- or under
    `std::move` at a movable last use, which is why the gate admits the
    shape whole and the render arm picks. Both read this one predicate."""
    if not (isinstance(a, TpyName) and a.name not in narrowed
            and a.name in locals_):
        return False
    w = _wrapper_union_like(
        _resolve_plain_alias(_elem_slot_type(ptype), analyzer), analyzer)
    if w is None:
        return False
    at = _unwrap_own(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        locals_[a.name]))))
    return _wrapper_union_like(at, analyzer) == w


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
    # A container-element SUBSCRIPT source (`out.append(xs[i])` inside a
    # generic body -- the warned copy into owned storage) reads bare like
    # the name row; the subscript arm gates the read itself.
    if not isinstance(a, (TpyCall, TpyMethodCall, TpySubscript)):
        return False
    at = _elem_slot_type(analyzer.get_expr_type(a))
    return isinstance(at, TypeParamRef) and at.name == pt.name


def _union_member_ctor_slot_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A union-member ctor rvalue into a union element slot
    (`xs.append(Circle(int32(1)))` on `list[Shape]` -> the bare
    `push_back(::tpyapp::shapes::Circle(1))`): the variant's converting
    constructor absorbs the member value, so no lift renders. Names and
    non-ctor rvalues stay out -- their copy/move shapes differ."""
    pt = _elem_slot_type(ptype)
    if not isinstance(pt, UnionType) or pt.needs_wrapper():
        return False
    return _union_member_ctor_rvalue(a, pt, analyzer)


def _stub_method_arg_ok(
        a: TpyExpr, ptype: 'TpyType | None', locals_: dict[str, TpyType],
        analyzer, *, param_names: 'set[str] | frozenset[str]',
        narrowed: 'set[str] | frozenset[str]',
        movable_locals: 'set[str] | frozenset[str]' = frozenset(),
        func_name: 'str | None' = None) -> bool:
    """The builtin-stub receivers' call into the one method-arg sink --
    container, bytearray, str/bytes view, scalar, and the ptr-template arm,
    which all render their args through the builtin-stub loop. No resolved
    overload and no flush slot to thread, which is the whole difference from
    the record entry (`_record_method_arg_ok`); prologue + rows:
    `_METHOD_ARG_SINK`.

    This wrapper is the ONLY entry that threads `overload=None`, which is why
    `req.overload is None` reads as "a builtin-stub slot" in the cells that
    key on the stub's C++ (`_x_insert_own_slot`, `_x_own_lvalue_flush`,
    `_x_comp_slot_const`, `_method_ctor_rvalue_arg`) and in the prologue's
    view fence. The premise is structural, not a runtime fact: an assert here
    could only restate the argument this call spells."""
    return arg_ok(_METHOD_ARG_SINK, a, ptype, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed, temps_ok=False,
                  movable_locals=movable_locals, func_name=func_name)


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


def _opt_strview_to_str_own_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                                     locals_: dict[str, TpyType],
                                     analyzer) -> bool:
    """The OPPOSITE-direction face of `_opt_view_own_elem_arg`: an
    `optional_strview_to_str` coerce landing in an owned `Optional[str]`
    slot (`items.append(local)` / `items[0] = maybe_prefix(..)`). Both sides
    of the coercion lambda's non-identity branch render the once-evaluated
    `__ov` statement expression, so a NAME (a VIEW-inner value-opt local) and
    a same-typed CALL rvalue take the identical wrap. The lambda passes
    through bare only at a NON-Own ARG slot; that is the one shape excluded
    here."""
    if not (isinstance(a, TpyCoerce)
            and a.coercion.name == "optional_strview_to_str"):
        return False
    # The coercion lambda passes through bare ONLY at a non-Own ARG slot;
    # every other position renders the `__ov` statement expression.
    if (a.context_kind == CoercionContext.ARG
            and not isinstance(a.expected_type, OwnType)):
        return False
    et = a.expected_type
    if isinstance(et, OwnType):
        et = et.wrapped
    slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if not (isinstance(slot, OptionalType)
            and is_str_type(unwrap_readonly(slot.inner))):
        return False
    src = a.expr
    if isinstance(src, TpyName):
        return _view_inner_value_opt(locals_.get(src.name))
    if isinstance(src, (TpyCall, TpyMethodCall)):
        return _view_inner_value_opt(analyzer.get_expr_type(src))
    return False


def _opt_view_param_own_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                                 locals_: dict[str, TpyType], analyzer,
                                 param_names: 'set[str] | frozenset[str]'
                                 ) -> bool:
    """The PARAM half of `_opt_view_own_elem_arg`: a value-repr
    `Optional[str/bytes]` PARAM at an `Own[Optional[same owned family]]`
    element slot (`items.append(s)`). Its binding is the BORROW
    `optional<view>`, so the slot takes `_maybe_convert_opt_view_param`'s ARG
    split (`s ? std::make_optional(std::string(*s)) : std::nullopt`) under the
    insert's consuming move -- the setitem row's method-call twin. A LOCAL is
    already owned and rides the coerce faces above."""
    if not (isinstance(a, TpyName) and a.name in param_names
            and a.name in locals_):
        return False
    own = _plain_own_slot(ptype)
    if own is None:
        return False
    return _opt_view_arg_shim(locals_[a.name], own, analyzer)


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
    if not record_like(pointee, analyzer):
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
    `needs_tuple_storage_lift`: `is_storage_form_source`'s two
    call shapes minus `renders_own_borrow_tuple`'s MIXED render."""
    rt = unwrap_readonly(fi.return_type) if fi is not None else None
    if (isinstance(rt, OwnType)
            and isinstance(unwrap_readonly(rt.wrapped), TupleType)):
        return True
    return (isinstance(rt, TupleType)
            and any(isinstance(et, OwnType) for et in rt.element_types)
            and not rt.is_mixed_own())


def _borrow_form_tuple_call(v: TpyExpr, analyzer) -> bool:
    """A call whose F3 tuple result is ALREADY in borrow form, so a borrow
    sink relays it bare -- `is_storage_form_source`'s NEGATIVE verdict for a
    call, which is what decides `_maybe_wrap_tuple_to_pointer`'s wrap. The
    two storage-return ABIs (`Own[tuple[..]]` and the per-element-Own
    synthesis) keep owing the lift and stay out. Distinct from
    `_storage_form_tuple_return`, which answers the OWNING sink's question
    and additionally subtracts the mixed render -- here a mixed-own return
    is storage, exactly as `is_storage_form_source` claims.

    Those two exclusions are defensive, not load-bearing today: sema
    refuses to relay a storage-ABI tuple into a borrow-tuple return at all
    ("Cannot return this tuple: its non-value elements are returned by
    reference into storage owned by the function"), so the shape has no
    constructible witness to pin. They stay because the fork they guard
    does."""
    if not isinstance(v, (TpyCall, TpyMethodCall)):
        return False
    fi = v.resolved_function_info
    if fi is None:
        return False
    rt = unwrap_readonly(fi.return_type)
    if isinstance(rt, OwnType) and isinstance(rt.wrapped, TupleType):
        return False
    if isinstance(rt, TupleType) and any(
            isinstance(et, OwnType) for et in rt.element_types):
        return False
    return _f1_tuple(analyzer.get_expr_type(v), analyzer) is not None


def _storage_field_ternary(v: TpyExpr, declared: dict[str, TpyType],
                           analyzer) -> bool:
    """A ternary whose BOTH arms are the same admitted storage field read the
    plain F3 return source already takes -- `is_storage_form_source`'s
    TpyIfExpr arm, so the whole conditional is one storage lvalue taking ONE
    `tuple_to_pointer` around it. A MIXED pair (a local arm beside a field
    arm) is not a single storage lvalue and stays out, matching the
    all-arms-storage conjunction."""
    if not isinstance(v, TpyIfExpr):
        return False
    arms = (v.then_expr, v.else_expr)
    return all(isinstance(a, TpyFieldAccess)
               and _const_exact_field_receiver_ok(a, declared, analyzer)
               for a in arms)


def _wrapper_ref_tuple_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                                locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """A wrapper element read off a REFERENCE-element tuple binding at a
    wrapper borrow slot (`count(p[0])` off `auto p = keep_param(tree);` ->
    `count(std::get<0>(p))`): the reference member IS the lvalue the
    `const Tree&` slot binds -- bare pass, no lift."""
    if not (isinstance(a, TpySubscript) and not isinstance(a.index, TpySlice)
            and isinstance(a.obj, TpyName) and a.obj.name in locals_):
        return False
    bt = _wrapper_ref_tuple_return(locals_[a.obj.name], analyzer)
    if bt is None:
        return False
    slot = _wrapper_union_like(ptype, analyzer)
    if slot is None:
        return False
    idx = fixed_int_literal_value_from_expr(a.index)
    if idx is None or not (0 <= idx < len(bt.element_types)):
        return False
    eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        bt.element_types[idx])))
    return (_wrapper_union_like(eu, analyzer) is not None
            and bool(_witness("arg.wrapper_ref_tuple_elem")))


def _own_tuple_storage_elem_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """A whole storage-tuple ELEMENT read at an `Own[tuple]` param slot
    (`take(pairs[0])` -> the bare `__getitem__`): the container keeps
    ownership of its elements, so the call COPIES the storage value --
    no per-element move, no lift (`needs_tuple_storage_lift` is False
    for a subscript source)."""
    if not (isinstance(a, TpySubscript) and not isinstance(a.index, TpySlice)):
        return False
    w = _plain_own_slot(ptype)
    inner = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(w)))
             if w is not None else None)
    if not (isinstance(inner, TupleType)
            and inner.has_pointer_repr_element()
            and _f1_tuple(inner, analyzer) is not None):
        return False
    return (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(a)))) == inner
            and bool(_witness("arg.own_tuple_storage_elem")))


def _own_tuple_call_rvalue_arg(a: TpyExpr, ptype: 'TpyType | None',
                               analyzer) -> bool:
    """An owning-call rvalue whose result type IS the `Own[tuple]` element
    slot. The prvalue binds the by-value slot with no lift, so the element
    family is unobservable at the insert -- unlike the borrow-tuple rows
    above, which each render a conversion.

    The row must stay closed to a BORROW-returning callee: a `T&` result
    bound into the slot's `T&&` is ill-formed once the tuple carries a
    reference-type element. `is_rvalue_source` folds the callee's
    `call_returns_cpp_ref` verdict in for a method call and for a call
    resolved through the function registry, which covers every shape
    witnessed here. It does NOT for the residual leg -- a call whose callee
    is an expression (a `Callable`-typed local) or a name the registry does
    not resolve -- which it defaults to rvalue unconditionally. That leg is
    tolerated rather than guarded: a free `@native` function is
    unconditionally not-cpp_ref, so nothing reaching it today can be a borrow
    return, and if one ever does the failure is the ill-formed C++ above -- a
    build error, not a silent miscompile. A separate declared-return check
    here is provably unreachable (ablated), which is why there is none."""
    return (_own_tuple_call_rvalue_slot(a, ptype, analyzer)
            and bool(_witness("arg.own_tuple_call_rvalue")))


def _own_tuple_call_rvalue_slot(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """`_own_tuple_call_rvalue_arg` without the witness -- for the render arm,
    which asks the same question the gate already answered and must not
    double-count the row."""
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
    return atu == slot and is_rvalue_source(analyzer, a)


def _stub_method_ret_ok(
        ret: 'TpyType | None', analyzer, *, stmt_position: bool,
        storage_ret_ok: bool, enum_ok: bool, ptr_ok: bool, callable_ok: bool,
        span_storage_ok: bool, stmt_storage_ok: bool) -> bool:
    """The builtin-stub method families' shared result-shape core -- a value
    scalar / char / str-value / bytes-value result, plus the extras each
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
                span_storage_ok=True, stmt_storage_ok=True)
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
            # keeps rejecting (it binds an alias, not a copy).
            or ((storage_ret_ok or borrow_ret_ok)
                and is_rvalue_source(analyzer, e)
                and (record_like(ret, analyzer)
                     or _container_method_recv(ret, analyzer, None)))
            # A BORROW-returning ptr-repr Optional result (`d.get("a")` ->
            # the bare `T*` from dict_get; WIDE pointee class): the render
            # is the bare stub call everywhere -- the consuming position
            # (passthrough decl / None test) owns the binding shape.
            or (_ptr_opt_borrow_call_ret(e, ret)
                and isinstance(ret, OptionalType)
                and _opt_pointee_wide(unwrap_readonly(ret.inner), analyzer)
                and _witness("method.container_opt_ptr_ret"))
            # A UNION-element rvalue popped into a STORAGE sink
            # (`t = work.pop()` at a union frame slot -- the by-value
            # `std::variant<...>` lands bare in the emplace/decl slot).
            or (storage_ret_ok
                and is_rvalue_source(analyzer, e)
                and isinstance(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                    if isinstance(ret, TpyType) else None, UnionType)
                and _witness("method.container_union_ret"))
            or note_detail("method.ret_type"))


def _method_call_arg_ok(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        locals_: dict[str, TpyType], analyzer, *, temps_ok: bool,
        narrowed: 'set[str] | frozenset[str]',
        param_names: 'set[str] | frozenset[str]',
        tparam_bounds: 'dict | None' = None,
        error_return_ok: bool = False,
        movable_locals: 'set[str] | frozenset[str]' = frozenset(),
        func_name: 'str | None' = None) -> bool:
    if isinstance(a, TpyGeneratorExpression):
        # A genexpr arg renders its frame creation in place at any method
        # slot (`", ".join(str(x) for x in nums)` -- the render is
        # position-blind); the genexpr's own lowering gates the source
        # shapes, so a bad shape still rejects.
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
                    temps_ok=temps_ok, narrowed=narrowed,
                    param_names=param_names,
                    index=index, overload=e.resolved_function_info,
                    movable_locals=movable_locals, func_name=func_name))

    recv_type = _method_receiver_type(e.obj, locals_, analyzer)
    fam = _method_recv_family(recv_type, analyzer, tparam_bounds, e.method)
    if fam is not None:
        return fam.arg_ok(a, ptype, locals_, analyzer,
                          param_names=param_names, narrowed=narrowed,
                          movable_locals=movable_locals, func_name=func_name)

    if recv_type is not None and recv_type.is_pointer():
        # The ptr-template arm's args (`p.span(n)`): scalar positional
        # slots expanded bare -- the builtin-stub rows.
        return _stub_method_arg_ok(a, ptype, locals_, analyzer,
                                   param_names=param_names,
                                   narrowed=narrowed,
                                   movable_locals=movable_locals,
                                   func_name=func_name)
    recv = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
    if isinstance(recv, OwnType):
        recv = unwrap_readonly(recv.wrapped)
    opt_recv = _optional_ptr_borrow(recv, analyzer)
    if opt_recv is not None:
        recv = unwrap_readonly(opt_recv.inner)
    if isinstance(recv, OptionalType) and isinstance(e.obj, TpyName):
        # The NARROWED storage-optional receiver's arg-gate half (`m.group(1)`
        # on `m: Own[Match] | None`): the shape gate resolves the same deref
        # receiver to the inner record, and without this the registry lookup
        # below misses and every ARGUMENT silently rejects.
        _so = _storage_optional_return_type(recv, analyzer)
        _so_occ = analyzer.get_expr_type(e.obj)
        if (_so is not None and _so_occ is not None
                and not isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(_so_occ))), OptionalType)):
            recv = unwrap_readonly(_unwrap_own(unwrap_readonly(_so.inner)))
    recv = _enum_receiver(e.obj, analyzer) or recv
    if (isinstance(recv, UnionType) and isinstance(e.obj, TpyName)
            and e.obj.name not in narrowed):
        # The assign-narrowed union receiver's arg-gate half: dispatch the
        # args on the member sema retyped the read to (the shape gate's
        # twin resolution).
        _anu_occ = analyzer.get_expr_type(e.obj)
        _anu_b = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            _anu_occ))) if _anu_occ is not None else None)
        if (_anu_b is not None
                and any(_anu_b == m for m in recv.members
                        if not is_void_like_type(m))
                and record_like(_anu_b, analyzer)):
            recv = _anu_b
    ri = analyzer.registry.receiver_record(recv)
    if ri is None:
        # An unresolved receiver rejects EVERY arg of the call, so without a
        # tag the whole body rejects under the bare statement reason and the
        # cause has nothing greppable (the narrowed storage-opt
        # unwrap above was found exactly that way).
        return note_detail("method.arg_recv_unresolved")
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
    mfi = e.resolved_function_info
    return _record_method_arg_ok(
        a, ptype, index, overloads[0], locals_, analyzer,
        temps_ok=temps_ok, narrowed=narrowed, param_names=param_names,
        frame_capturing=(mfi is not None
                         and (mfi.is_generator or mfi.is_async)),
        movable_locals=movable_locals, func_name=func_name)

def _protocol_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                   analyzer, *, stmt_position: bool,
                                   storage_ret_ok: bool,
                                   borrow_ret_ok: bool = False) -> bool:
    """A method call on a bare protocol receiver -- `pet.make_noise()` on a
    `@dynamic` `Base&` (a vtable call) or `count.length()` on a structural
    `const T_c&` (monomorphized). Both spell `recv.method(args)`: the flavor
    lives entirely in the param slot, not in the body.

    There is no protocol arm in the method-call render -- the user-record arg
    loop is guarded by `is_user_record`, so a protocol receiver falls to the
    LAZY fallback arg loop, whose renders are the FREE call's, not the record
    method's. Two consequences this gate must honor: literal args take their
    slot's coercion (an int literal into a BigInt slot wraps, a float literal
    into a float32 slot gets the `f` suffix) rather than the method path's
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
    its `->` access comes from the emit node's own-dyn test
    (`_recv_own_dyn`), not from anything decided here.

    Method: a plain instance method. The member name is always
    `escape_cpp_name(e.method)` -- `_plain_method_fi_ok` already rejected the
    LiteralType params that would mangle it, and the @native rename is rejected
    below -- so no overload-set check is needed here (unlike the record arm,
    whose arg-temp decisions read `overloads[0]`).

    Args: the shared pass-through rows only (`_shared_pass_through_arg` -- the
    rows whose render is decided by the arg itself, so the fallback
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
        # deref rules and reject.
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
    # the THIRMethodCall cpp_template arm expands it over the same receiver
    # render, so admission
    # is a gate widening only. Arg-carrying templates render their args
    # through the inline_template loop, which this family's free-call
    # arg rows do not build -- they keep rejecting. `{cpp}` substitutes the
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
            # A `Ptr[T]` result (`lock._raw_mutex() -> Ptr[_RawMutex]`): a
            # by-value pointer landing bare in any admitted sink, exactly as
            # the ptr-template ladder's return set carries it.
            or (_eligible_ptr_value(ret, analyzer)
                and _witness("method.protocol_ptr_ret"))
            # A T result off a bounded-T receiver (`item.clone() -> T`):
            # renders by name; the composing position gates its own family.
            or _tparam_value(ret)
            # ... and its compound sibling, an OPEN-T tuple result
            # (`s.pair() -> tuple[T, int32]`): borrow and storage coincide
            # at the tuple level, so the result lands as the plain spelled
            # copy; the composing position gates its own family too.
            or (_open_t_tuple_slot(ret, analyzer) is not None
                and _witness("method.protocol_open_t_tuple_ret"))
            # An Own[record] RVALUE at a STORAGE sink (`return
            # factory.create_point(x, y)` off a bounded-T protocol
            # receiver): the prvalue lands bare in the return/decl slot,
            # exactly like the record ladder's storage admission.
            or (storage_ret_ok and is_rvalue_source(analyzer, e)
                and record_like(
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
            # A CONTAINER result consumed as the next RECEIVER
            # (`x.get()[0]` off a bounded-T protocol receiver -- the
            # borrow `list[R]&` interpolates into the checked dunder):
            # the container family's composition row.
            or (borrow_ret_ok and _alias_ref_container(ret)
                and _witness("method.protocol_container_ret"))
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


# ---------------------------------------------------------------------------
# THE ARG TABLE (see `arg_table.py` for the shapes and the invariant).
#
# Every callee family's argument ladder folds into an ordered row tuple here,
# beside the predicates the rows call. A row adapter is written ONCE and
# shared by every family that carries the shape, so a widening cannot land in
# one ladder and miss its siblings; `register_sink` fails the import if two
# families name the same row and reach different predicates.
#
# ORDER IS LOAD-BEARING: `_witness` fires while the gate walks, so reordering
# a family's rows changes the recorded face census even when admission is
# unchanged.
# ---------------------------------------------------------------------------

def _x_temps_ok(req: _ArgReq) -> bool:
    """The `temps_ok and` prefix as a cell PRE-guard: this position has a
    statement to flush a hoisted temp into. A per-cell guard rather than a
    family flag because it is a property of the ARGUMENT POSITION, not of
    the callee family."""
    return req.temps_ok


def _x_own_lvalue_flush(req: _ArgReq) -> bool:
    """The Own-slot copy+move cell's flush guard, which the two method halves
    stated differently because their `Own[T]` slots are different C++.

    A user method's `Own[T]` param is a real by-value slot spelled `T&&`, so a
    non-movable lvalue must hoist `auto __tmp_N = <arg>;` and bind the move --
    and that needs a statement to flush into. Without the guard the bare
    lvalue renders straight into the `T&&` slot (`s.take(b)` inside a call
    arg), which does not compile.

    A builtin stub's `Own[T]` slot is a container INSERT, whose const-ref
    overload takes the lvalue copy with no temp at all (`xs.push_back(b)`), so
    it needs no flush position -- and it has none to ask for: the stub entry
    threads `temps_ok=False` at every position."""
    return req.temps_ok or req.overload is None


def _x_insert_own_slot(req: _ArgReq) -> bool:
    """A cell whose `Own[T]` slot premise is the builtin INSERT's C++ rather
    than a user signature's.

    A stub's `Own[T]` slot is `push_back` / `insert` / `__setitem__`: a
    template whose const-ref overload binds an LVALUE and copies it, with no
    temp and no move. A user method's `Own[T]` param is a real by-value slot
    spelled `own_param_t<T>` (`T&&`), which no lvalue binds at all -- its
    sources are the Own cascade's (a move, a prvalue, or a flushed copy
    temp), each with its own cell. So these cells decide the stub slot only,
    and at a record signature the argument walks on to the cells that answer
    for a by-value param. Probed: without this, an element read at an open
    `Own[T]` method slot emitted `push(::tpy::__getitem__(src, i))` into an
    `own_param_t<T>` slot, which does not compile."""
    return req.overload is None


def _x_comp_slot_const(req: _ArgReq) -> bool:
    """The slot guard of the cells whose render binds a PRVALUE in place
    (`container_comp`, `container_slot_call_rvalue`): the slot must be a
    const borrow -- a mutated
    `std::vector<T>&` slot cannot bind one and the C++ is ill-formed. A
    builtin stub carries no signature-const facts and every container slot it
    spells is a `const T&` (the cell's own premise, unstated until the two
    method halves shared it); a resolved record overload states the verdict
    per position in `const_borrow_params`."""
    if req.overload is None:
        return True
    cbp = getattr(req.overload, "const_borrow_params", None)
    return bool(cbp is not None and req.index in cbp)


def _x_arg_not_lent(req: _ArgReq) -> bool:
    """The IN-PLACE container cells' lifetime guard: a container rvalue bound
    inline lives only to the end of the full expression, so a callee that
    hands a borrow of it back leaves the caller holding a reference into dead
    storage. `head([Rec(1), Rec(2)])` against `def head(xs:
    readonly[list[Rec]]) -> readonly[Rec]` bound `const Rec& r` into the
    temporary and printed garbage where CPython printed 1.

    Reads the callee's recorded return-borrow sources for THIS position, off
    the root fi like every other provenance reader. Facts that were never
    computed DECLINE: a None there means the body has not been analyzed, and
    "not measured" is not evidence of an owning result. A builtin stub (no
    resolved overload) admits, the same None leg the slot-const guard beside
    it takes -- the stub container slots are const borrows and their results
    are minted, not borrows of what they were handed.

    There is no hoisting alternative here on purpose: a named temporary would
    only move the question to how long THAT lives, which is a lifetime
    analysis the language does not have yet. The shape stops being admitted.
    """
    if req.frame_capturing:
        # A generator / coroutine factory's argument never binds inline: the
        # render hoists it to a named temporary the frame can borrow.
        return True
    fi = req.overload
    if fi is None:
        return True
    if fi.root.return_borrows_from is None:
        return False
    return req.index not in recorded_return_borrow_sources(fi)


def _x_inline_container_literal(req: _ArgReq) -> bool:
    """The method / qualified families' in-place container-literal cell: the
    slot must be a const borrow AND the callee must not lend it back."""
    return _x_comp_slot_const(req) and _x_arg_not_lent(req)


def _r_scalar_at_template_slot(req: _ArgReq) -> bool:
    return (_scalar_pass_through_slot(req.ptype, req.analyzer)
            and _resolved_scalar(req.analyzer.get_expr_type(req.a),
                                 req.analyzer))


def _r_protocol_bare_name(req: _ArgReq) -> bool:
    return _protocol_bare_name_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_str_pass_through(req: _ArgReq) -> bool:
    return _str_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer,
                                 mutated=req.mutated_slots)


def _r_bytes_owned_lvalue(req: _ArgReq) -> bool:
    return _bytes_owned_lvalue_arg(req.a, req.ptype, req.locals_,
                                   req.param_names, req.analyzer)


def _r_bytes_owned_call_rvalue(req: _ArgReq) -> bool:
    return _bytes_owned_call_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_bytes_pass_through(req: _ArgReq) -> bool:
    return _bytes_pass_through_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_char_pass_through(req: _ArgReq) -> bool:
    return _char_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_enum_pass_through(req: _ArgReq) -> bool:
    return _enum_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_ptr_pass_through(req: _ArgReq) -> bool:
    return _ptr_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_native_iterable_literal(req: _ArgReq) -> bool:
    return _native_iterable_literal_arg(req.a, req.ptype, req.analyzer)


def _r_native_iterable_container(req: _ArgReq) -> bool:
    return _native_iterable_container_arg(req.a, req.ptype, req.locals_)


def _r_native_iterable_field(req: _ArgReq) -> bool:
    return _native_iterable_field_arg(req.a, req.ptype, req.locals_,
                                      req.analyzer)


def _r_native_iterable_call(req: _ArgReq) -> bool:
    return _native_iterable_call_arg(req.a, req.ptype, req.analyzer)


def _r_shared_pass_through(req: _ArgReq) -> bool:
    return _shared_pass_through_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer, mutated=req.mutated_slots)


def _r_ru_wrapper_name(req: _ArgReq) -> bool:
    return _ru_wrapper_name_arg(req.a, req.ptype, req.locals_, req.narrowed,
                                req.analyzer)


def _r_optional_ptr(req: _ArgReq) -> bool:
    return _optional_ptr_arg(req.a, req.ptype, req.locals_, req.analyzer,
                             temps_ok=req.temps_ok)


def _r_tuple_literal(req: _ArgReq) -> bool:
    return _tuple_literal_arg(req.a, req.ptype)


def _r_func_ref(req: _ArgReq) -> bool:
    return _func_ref_routable(req.a, req.analyzer)


def _r_lambda(req: _ArgReq) -> bool:
    # `self_capturable` defaults False, which is what every family but
    # `plain` spelled -- so threading it here leaves the shape one shared
    # cell.
    return _lambda_routable(req.a, req.analyzer,
                            self_capturable=req.self_capturable)


def _r_callable_value_pass(req: _ArgReq) -> bool:
    return _callable_value_pass_arg(req.a, req.locals_, req.analyzer)


def _r_own_move(req: _ArgReq) -> bool:
    return _own_move_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_native_own_scalar_lvalue(req: _ArgReq) -> bool:
    return _native_own_scalar_lvalue_arg(req.a, req.ptype, req.locals_,
                                         req.analyzer)


def _r_inst_slice(req: _ArgReq) -> bool:
    return _inst_slice_arg_ok(req.a, req.analyzer)


def _r_container_ternary(req: _ArgReq) -> bool:
    return _container_ternary_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_native_iterable_range(req: _ArgReq) -> bool:
    return _native_iterable_range_arg(req.a, req.ptype)


def _r_native_iterable_iterator_call(req: _ArgReq) -> bool:
    return _native_iterable_iterator_call_arg(req.a, req.ptype, req.analyzer)


def _r_native_iterable_genexpr(req: _ArgReq) -> bool:
    return _native_iterable_genexpr_arg(req.a, req.ptype)


def _r_native_value_call(req: _ArgReq) -> bool:
    return _native_value_call_arg(req.a, req.ptype, req.analyzer)


def _r_native_container_call(req: _ArgReq) -> bool:
    return _native_container_call_arg(req.a, req.ptype, req.analyzer)


def _r_native_record_call(req: _ArgReq) -> bool:
    return _native_record_call_arg(req.a, req.ptype, req.analyzer)


def _r_protocol_slot(req: _ArgReq) -> bool:
    return _protocol_slot_arg(req.a, req.ptype, req.locals_, req.analyzer,
                              temps_ok=req.temps_ok)


def _r_native_protocol_value(req: _ArgReq) -> bool:
    return _native_protocol_value_arg(req.a, req.ptype, req.analyzer)


def _r_native_optptr_name(req: _ArgReq) -> bool:
    return _native_optptr_name_arg(req.a, req.ptype, req.analyzer)


def _r_native_union_name(req: _ArgReq) -> bool:
    return _native_union_name_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_native_protocol_tuple_literal(req: _ArgReq) -> bool:
    return _native_protocol_tuple_literal_arg(
        req.a, req.ptype, req.analyzer) is not None


def _r_native_protocol_open_call(req: _ArgReq) -> bool:
    return _native_protocol_open_call_arg(req.a, req.ptype, req.analyzer)


def _r_native_protocol_field(req: _ArgReq) -> bool:
    return _native_protocol_field_arg(req.a, req.ptype, req.analyzer)


def _r_native_iterable_comp(req: _ArgReq) -> bool:
    return _native_iterable_comp_arg(
        req.a, req.ptype, req.analyzer) is not None


def _r_borrow_tuple_storage_name(req: _ArgReq) -> bool:
    return _borrow_tuple_storage_name_arg(
        req.a, req.ptype, req.locals_, req.storage_tuple_locals,
        req.analyzer) is not None


def _r_borrow_tuple_field(req: _ArgReq) -> bool:
    return _borrow_tuple_field_arg(req.a, req.ptype, req.analyzer) is not None


def _r_open_value_tuple_name(req: _ArgReq) -> bool:
    return (isinstance(req.a, TpyName)
            and _open_value_tuple(req.ptype) is not None)


# --- the container family's own rows -----------------------------------

def _r_copy_iter_own_elem(req: _ArgReq) -> bool:
    return _copy_iter_own_elem_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_str_owned_slot(req: _ArgReq) -> bool:
    return _str_owned_slot_arg(req.a, req.ptype, req.locals_,
                               req.param_names, req.analyzer, req.pointers)


def _r_bytes_owned_literal(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (isinstance(_peel_coerce(req.a), TpyBytesLiteral)
            and own is not None
            and is_bytes_type(unwrap_readonly(own)))


def _r_bytes_view_literal(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (isinstance(_peel_coerce(req.a), TpyBytesLiteral)
            and own is not None
            and is_bytes_view_type(unwrap_readonly(own)))


def _r_bytes_owned_slot(req: _ArgReq) -> bool:
    return _bytes_owned_slot_arg(req.a, req.ptype, req.locals_,
                                 req.param_names, req.analyzer)


def _r_own_enum_elem(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (own is not None
            and _enum_pass_through_arg(req.a, own, req.locals_, req.analyzer))


def _r_container_pass_through(req: _ArgReq) -> bool:
    return _container_pass_through_arg(req.a, req.ptype, req.locals_,
                                       req.analyzer)


def _r_container_slot_call_rvalue(req: _ArgReq) -> bool:
    return _container_slot_call_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_own_record_rvalue(req: _ArgReq) -> bool:
    return _own_record_rvalue_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_copy_own(req: _ArgReq) -> bool:
    return _copy_own_arg(req.a, req.ptype, req.analyzer)


def _r_own_lvalue(req: _ArgReq) -> bool:
    return _own_lvalue_arg(req.a, req.ptype, req.locals_, req.narrowed,
                           req.analyzer, req.param_names)


def _r_own_iter_special(req: _ArgReq) -> bool:
    return _own_iter_special_arg(req.a, req.ptype)


def _r_own_container_literal(req: _ArgReq) -> bool:
    return _own_container_literal_arg(req.a, req.ptype, req.analyzer)


def _r_own_container_comp(req: _ArgReq) -> bool:
    return _own_container_comp_arg(req.a, req.ptype, req.analyzer)


def _r_any_pass_through(req: _ArgReq) -> bool:
    return _any_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_container_literal_method(req: _ArgReq) -> bool:
    return _container_literal_method_arg(req.a, req.ptype, req.analyzer)


def _r_none_value_opt(req: _ArgReq) -> bool:
    return _none_value_opt_arg(req.a, req.ptype, req.analyzer) is not None


def _r_opt_view_own_elem(req: _ArgReq) -> bool:
    return _opt_view_own_elem_arg(req.a, req.ptype, req.locals_, req.analyzer,
                                  req.param_names)


def _r_opt_view_param_own_elem(req: _ArgReq) -> bool:
    return _opt_view_param_own_elem_arg(req.a, req.ptype, req.locals_,
                                        req.analyzer, req.param_names)


def _r_opt_strview_to_str_own_elem(req: _ArgReq) -> bool:
    return _opt_strview_to_str_own_elem_arg(req.a, req.ptype, req.locals_,
                                            req.analyzer)


def _r_value_opt_scalar_elem(req: _ArgReq) -> bool:
    return _value_opt_scalar_elem_arg(req.a, req.ptype, req.analyzer)


def _r_own_ptr_value(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (own is not None
            and _eligible_ptr_value(own, req.analyzer)
            # The PEELED arg must be ptr-typed itself: a record lvalue
            # coerce-lifted to the Ptr slot (`ps.append(items[0])`) belongs
            # to the `&(...)` lift row below.
            and _eligible_ptr_value(
                req.analyzer.get_expr_type(_peel_coerce(req.a)),
                req.analyzer))


def _r_none_unit(req: _ArgReq) -> bool:
    return _none_unit_arg(req.a, req.ptype) is not None


def _r_callable_slot(req: _ArgReq) -> bool:
    return _callable_slot_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_tparam_slot(req: _ArgReq) -> bool:
    return _tparam_slot_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_own_value_tuple_literal(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (isinstance(req.a, TpyTupleLiteral)
            and own is not None
            and _value_tuple(own, req.analyzer) is not None)


def _r_own_open_t_tuple_literal(req: _ArgReq) -> bool:
    own = _plain_own_slot(req.ptype)
    return (isinstance(req.a, TpyTupleLiteral)
            and own is not None
            and _open_t_tuple_slot(own, req.analyzer) is not None)


def _own_slot_ptr_repr_tuple(ptype: 'TpyType | None') -> 'TupleType | None':
    """The Own[tuple[..]] element slot shared by the borrow-tuple rows, when
    the tuple has a pointer-repr element (i.e. a borrow form exists)."""
    own = _plain_own_slot(ptype)
    if own is None:
        return None
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(own)))
    if not isinstance(bare, TupleType) or not bare.has_pointer_repr_element():
        return None
    return bare


def _r_own_btuple_literal(req: _ArgReq) -> bool:
    if not isinstance(req.a, TpyTupleLiteral):
        return False
    bare = _own_slot_ptr_repr_tuple(req.ptype)
    return (bare is not None
            and (_tuple_elem_slots_ptr_optional(bare)
                 # Plain-record members admit for ALL-RVALUE literals only
                 # (`pairs.append((Item(1), Item(2)))` -- the double-convert
                 # tuple_to_storage_move over tuple_value_to_borrow); the
                 # CONST_REF lvalue rule the borrow builder does not carry
                 # never fires on an rvalue element.
                 or _btuple_literal_elems_rvalue(req.a, bare, req.analyzer)))


def _r_own_btuple_storage_source(req: _ArgReq) -> bool:
    if not isinstance(req.a, (TpySubscript, TpyCall, TpyMethodCall)):
        return False
    bare = _own_slot_ptr_repr_tuple(req.ptype)
    return (bare is not None
            and (_tuple_elem_slots_ptr_optional(bare)
                 # The plain-record F3 sibling, SUBSCRIPT only
                 # (`out.append(items[0])` at `list[tuple[int32, P]]` -- the
                 # bare storage element pass); mixed-own CALL sources keep
                 # their own admission.
                 or (isinstance(req.a, TpySubscript)
                     and _f1_tuple(bare, req.analyzer) is not None))
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                req.analyzer.get_expr_type(req.a)))) == bare)


def _r_own_open_t_tuple_storage_source(req: _ArgReq) -> bool:
    """A whole tuple ELEMENT read at an `Own[open-T tuple]` element slot.

    An open element has no pointer repr, so borrow and storage forms
    coincide and the read passes bare -- unlike the pointer-repr sibling
    above, which owes a form conversion. SUBSCRIPT only: a call source
    would owe the return-shape question the ptr-repr row answers
    separately.
    """
    if not isinstance(req.a, TpySubscript):
        return False
    own = _plain_own_slot(req.ptype)
    if own is None:
        return False
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(own)))
    if _open_t_tuple_slot(bare, req.analyzer) is None:
        return False
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        req.analyzer.get_expr_type(req.a)))) == bare


def _r_own_btuple_mixed_call(req: _ArgReq) -> bool:
    if not isinstance(req.a, (TpyCall, TpyMethodCall)):
        return False
    bare = _own_slot_ptr_repr_tuple(req.ptype)
    return (bare is not None
            and _mixed_own_storage_source(req.a, bare, frozenset(),
                                          req.analyzer) is not None)


def _r_own_btuple_nested_name(req: _ArgReq) -> bool:
    a = req.a
    if not (isinstance(a, TpyName) and a.name in req.locals_):
        return False
    own = _plain_own_slot(req.ptype)
    if own is None:
        return False
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(own)))
    return (isinstance(bare, TupleType)
            and _nested_storage_tuple(bare, req.analyzer) is not None
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                req.locals_[a.name]))) == bare)


def declared_name_copy(a: TpyExpr, analyzer) -> bool:
    """A tuple NAME whose whole-tuple copy into an owning slot sema declared:
    it warned every element the copy takes, and the binding holds no element
    by value (`own_element_mixed`). The one admission test every name lift
    shares -- a lift that skipped it copied undeclared or moved out of a
    borrow."""
    return (a in analyzer.ctx.own_element_copies
            and a not in analyzer.ctx.own_element_mixed)


def own_btuple_borrow_name_arg(a: TpyExpr, ptype: 'TpyType | None',
                               locals_: dict, narrowed: 'AbstractSet[str]',
                               storage_tuple_locals: 'AbstractSet[str]',
                               analyzer) -> 'TupleType | None':
    """A BORROW-form tuple NAME (`t = (v, v)`, a tuple param) at a whole
    `Own[ptr-repr tuple]` element slot (`xs.append(t)`) whose copy sema
    declared: it lifts through the non-move `tuple_to_storage<S>(t)`, the
    same lift a borrow-tuple-returning call takes there. Returns the slot's
    tuple. An undeclared name keeps rejecting rather than copying unwarned."""
    if not (isinstance(a, TpyName) and a.name in locals_
            and a.name != "self" and a.name not in narrowed
            and a.name not in storage_tuple_locals
            and declared_name_copy(a, analyzer)):
        return None
    bare = _own_slot_ptr_repr_tuple(ptype)
    if bare is None:
        return None
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return bare if au == bare else None


def _r_own_btuple_borrow_name(req: _ArgReq) -> bool:
    return own_btuple_borrow_name_arg(
        req.a, req.ptype, req.locals_, req.narrowed,
        req.storage_tuple_locals, req.analyzer) is not None


def _r_own_tuple_call_rvalue(req: _ArgReq) -> bool:
    return _own_tuple_call_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_ptr_addr_of_elem(req: _ArgReq) -> bool:
    return _ptr_addr_of_elem_arg(req.a, req.ptype, req.analyzer)


# --- the marker families' rows -----------------------------------------

def _r_value_opt_callable_pass(req: _ArgReq) -> bool:
    return _value_opt_callable_pass_arg(req.a, req.ptype, req.locals_,
                                        req.narrowed, req.analyzer)


def _r_value_union_temp(req: _ArgReq) -> bool:
    return _value_union_temp_arg(req.a, req.ptype, req.locals_, req.narrowed,
                                 req.analyzer)


def _r_value_union_narrowed_pass(req: _ArgReq) -> bool:
    return _value_union_narrowed_pass_arg(req.a, req.ptype, req.locals_,
                                          req.narrowed, req.analyzer)


def _r_opt_own_record_name(req: _ArgReq) -> bool:
    return _opt_own_record_name_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer) is not None


def _r_readonly_record_ctor(req: _ArgReq) -> bool:
    return _readonly_record_ctor_arg(req.a, req.ptype, req.locals_,
                                     req.analyzer)


def _r_union_pass_through(req: _ArgReq) -> bool:
    return _union_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_union_member_lift(req: _ArgReq) -> bool:
    return _union_member_lift_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_union_coerced_literal(req: _ArgReq) -> bool:
    return _union_coerced_literal_arg(req.a, req.ptype, req.locals_,
                                      req.analyzer)


def _r_value_opt_member(req: _ArgReq) -> bool:
    return _value_opt_member_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_value_opt_field_pass(req: _ArgReq) -> bool:
    return _whole_value_opt_field_arg(req.a, req.ptype, req.locals_,
                                      req.analyzer)


def _r_ru_container_literal(req: _ArgReq) -> bool:
    return (_ru_wrapper_arg_slot(req.ptype) is not None
            and _ru_container_literal_ok(req.a, req.analyzer))


def _r_ru_wrapper_field(req: _ArgReq) -> bool:
    return _ru_wrapper_field_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_own_union_ctor(req: _ArgReq) -> bool:
    return _own_union_ctor_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_dyn_own_coro_factory(req: _ArgReq) -> bool:
    return _dyn_own_coro_factory_arg(req.a, req.ptype,
                                     req.analyzer) is not None


def _r_dyn_own_handle(req: _ArgReq) -> bool:
    return _dyn_own_handle_arg(req.a, req.ptype, req.locals_,
                               req.analyzer) is not None


def _r_dyn_own_forward_call(req: _ArgReq) -> bool:
    return _dyn_own_forward_call_arg(req.a, req.ptype,
                                     req.analyzer) is not None


def _r_container_field_pass(req: _ArgReq) -> bool:
    return _container_field_pass_arg(req.a, req.ptype, req.locals_,
                                     req.analyzer)


def _r_record_field_marker(req: _ArgReq) -> bool:
    return _record_field_marker_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer, req.narrowed)


def _r_value_tuple_field_pass(req: _ArgReq) -> bool:
    return _value_tuple_field_pass_arg(req.a, req.ptype, req.locals_,
                                       req.analyzer)


def _r_borrow_ret_record_marker(req: _ArgReq) -> bool:
    return _borrow_ret_record_marker_arg(req.a, req.ptype, req.analyzer)


def _r_btuple_literal_marker(req: _ArgReq) -> bool:
    return _btuple_literal_marker_arg(req.a, req.ptype)


def _r_same_tparam_name(req: _ArgReq) -> bool:
    """A NAME bound to the SAME bare type param as the slot
    (`super().transform(other)` at `other: U`): the form-neutral slot binds
    the name bare -- the generic free lane's same-T rule
    (`inner_len<T>(x)`)."""
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(req.ptype)))
          if isinstance(req.ptype, TpyType) else None)
    if not isinstance(pt, TypeParamRef):
        return False
    a = req.a
    if not (isinstance(a, TpyName) and a.name != "self"):
        return False
    at = req.locals_.get(a.name)
    au = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if isinstance(at, TpyType) else None)
    return isinstance(au, TypeParamRef) and au.name == pt.name


# --- the plain free-call family's own rows -----------------------------

def _r_callable_field(req: _ArgReq) -> bool:
    return (isinstance(req.a, TpyFieldAccess)
            and _callable_value(req.analyzer.get_expr_type(req.a))
            and _field_receiver_ok(req.a, req.locals_, req.analyzer))


def _r_callable_object(req: _ArgReq) -> bool:
    return _callable_object_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_record_rvalue_temp(req: _ArgReq) -> bool:
    return _record_rvalue_temp_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer, upcast_ok=True)


def _r_own_coerce_cast(req: _ArgReq) -> bool:
    return _own_coerce_cast_arg(req.a, req.ptype, req.locals_)


def _r_container_literal(req: _ArgReq) -> bool:
    return _container_literal_arg(req.a, req.ptype, req.analyzer)


def _r_ref_param_dictset_literal(req: _ArgReq) -> bool:
    return _ref_param_dictset_literal_arg(req.a, req.ptype, req.analyzer)


def _r_covariant_temp(req: _ArgReq) -> bool:
    return _covariant_temp_arg(req.a, req.ptype, req.locals_,
                               req.analyzer) is not None


def _r_required_protocol_union(req: _ArgReq) -> bool:
    return _required_protocol_union_arg(req.a, req.ptype, req.locals_,
                                        req.analyzer)


def _r_ru_wrapper_borrow_call(req: _ArgReq) -> bool:
    return _ru_wrapper_borrow_call_arg(req.a, req.ptype, req.analyzer)


def _r_ru_wrapper_value_call(req: _ArgReq) -> bool:
    return _ru_wrapper_value_call_arg(req.a, req.ptype, req.analyzer)


def _r_ru_wrapper_member_name(req: _ArgReq) -> bool:
    return _ru_wrapper_member_name_arg(req.a, req.ptype, req.locals_,
                                       req.narrowed) is not None


def _r_ru_wrapper_scalar_literal(req: _ArgReq) -> bool:
    return _ru_wrapper_scalar_literal_arg(req.a, req.ptype,
                                          req.analyzer) is not None


def _r_ru_wrapper_member_rvalue(req: _ArgReq) -> bool:
    return _ru_wrapper_member_rvalue_arg(req.a, req.ptype,
                                         req.analyzer) is not None


def _r_ru_wrapper_own_call(req: _ArgReq) -> bool:
    return _ru_wrapper_own_call_arg(req.a, req.ptype,
                                    req.analyzer) is not None


def _r_own_union_call_pass(req: _ArgReq) -> bool:
    return _own_union_call_pass_arg(req.a, req.ptype, req.analyzer)


def _r_dyn_own_conformer(req: _ArgReq) -> bool:
    return _dyn_own_conformer_arg(req.a, req.ptype, req.locals_,
                                  req.analyzer) is not None


def _r_wide_opt_deref_name(req: _ArgReq) -> bool:
    return _wide_opt_deref_name_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer)


def _r_value_opt_pass_through(req: _ArgReq) -> bool:
    return _value_opt_pass_through_arg(req.a, req.ptype, req.locals_,
                                       req.narrowed, req.analyzer)


def _r_opt_view_identity_coerce(req: _ArgReq) -> bool:
    return _opt_view_identity_coerce_arg(req.a, req.ptype)


def _r_value_opt_tuple_pass(req: _ArgReq) -> bool:
    return _value_opt_tuple_pass_arg(req.a, req.ptype, req.locals_,
                                     req.narrowed, req.analyzer)


def _r_list_repeat_proto(req: _ArgReq) -> bool:
    if not isinstance(req.a, TpyListRepeat):
        return False
    proto = _protocol_arg_slot(req.ptype)
    return proto is not None and not is_dyn_protocol(proto)


def _r_nullable_proto_addr(req: _ArgReq) -> bool:
    return (isinstance(req.a, (TpyName, TpyNoneLiteral))
            and _protocol_union_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer) in ("addr", "nullproto"))


def _r_tuple_literal_value_opt(req: _ArgReq) -> bool:
    return _tuple_literal_value_opt_arg(req.a, req.ptype, req.analyzer)


def _r_own_tuple_storage_elem(req: _ArgReq) -> bool:
    return _own_tuple_storage_elem_arg(req.a, req.ptype, req.analyzer)


def _r_wrapper_ref_tuple_elem(req: _ArgReq) -> bool:
    return _wrapper_ref_tuple_elem_arg(req.a, req.ptype, req.locals_,
                                       req.analyzer)


def _r_record_borrow_call(req: _ArgReq) -> bool:
    return _record_borrow_call_arg(req.a, req.ptype, req.analyzer)


def _r_own_optional_record_rvalue(req: _ArgReq) -> bool:
    return _own_optional_record_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_opt_own_record_rvalue(req: _ArgReq) -> bool:
    return _opt_own_record_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_str_literal_value_opt_coerced(req: _ArgReq) -> bool:
    # This ladder peels the coerce where the method/ctor ladders pass the
    # argument bare, so the two admit DIFFERENT shapes and cannot share a row
    # name -- `register_sink` rejects one name reaching two predicates.
    return _str_literal_value_opt_arg(_peel_coerce(req.a), req.ptype)


def _r_opt_string_literal(req: _ArgReq) -> bool:
    return (isinstance(_peel_coerce(req.a), TpyStrLiteral)
            and _value_opt_string_owned(req.ptype) is not None)


def _r_own_opt_slot(req: _ArgReq) -> bool:
    return _own_opt_slot_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_value_array_call(req: _ArgReq) -> bool:
    return _value_array_call_arg(req.a, req.ptype, req.analyzer)


def _r_recursive_union_borrow_call(req: _ArgReq) -> bool:
    return _recursive_union_borrow_call_arg(req.a, req.ptype, req.analyzer)


def _r_record_elem_subscript(req: _ArgReq) -> bool:
    return _record_elem_subscript_arg(req.a, req.ptype, req.analyzer)


def _r_container_comp(req: _ArgReq) -> bool:
    return _container_comp_arg(req.a, req.ptype)


def _r_borrow_tuple_subscript(req: _ArgReq) -> bool:
    return _borrow_tuple_subscript_arg(req.a, req.ptype,
                                       req.analyzer) is not None


def _r_record_field_ref(req: _ArgReq) -> bool:
    return _record_field_ref_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_deref_coerce(req: _ArgReq) -> bool:
    # The flush test reads the PREDICATE'S verdict (the wrapper-`__deref__()`
    # form needs a temp, the inline Ptr deref does not), so it cannot be a
    # pre-guard -- the row owns the whole conjunct, like the temps_ok-taking
    # `optional_ptr` / `protocol_slot` rows.
    dc = _deref_coerce_arg(req.a, req.ptype, req.locals_, req.analyzer)
    return dc is not None and (dc[0] == "inline" or req.temps_ok)


def _r_readonly_container_rvalue(req: _ArgReq) -> bool:
    return _readonly_container_rvalue_arg(req.a, req.ptype,
                                          req.analyzer) is not None


# --- the generic (substituted-slot) free-call family's own rows ---------

def _r_generic_btuple_name(req: _ArgReq) -> bool:
    a, ptype, resolved = req.a, req.open_ptype, req.ptype
    if not (isinstance(ptype, TupleType)
            and all(isinstance(unwrap_readonly(unwrap_ref_type(_pt)),
                               TypeParamRef)
                    for _pt in ptype.element_types)
            and isinstance(resolved, TupleType)
            and resolved.has_pointer_repr_element()
            and isinstance(a, TpyName) and a.name in req.locals_
            # BINDING-form keyed, not type-keyed: an Own-element literal
            # decl registers storage form (std::tuple<int32_t, Box>) and
            # needs the tuple_to_pointer lift.
            # NB if a future decl arm registers such bindings in a SECOND
            # registry (the walrus_slot_locals precedent), that set must
            # join this exclusion.
            and a.name not in req.storage_tuple_locals):
        return False
    _bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        req.locals_[a.name])))
    return (isinstance(_bt, TupleType)
            and len(_bt.element_types) == len(resolved.element_types)
            and _bt.has_pointer_repr_element())


def _r_generic_own_btuple_literal(req: _ArgReq) -> bool:
    if not isinstance(req.a, TpyTupleLiteral):
        return False
    _own_res = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(req.ptype)))
    if not isinstance(_own_res, OwnType):
        return False
    _own_inner = unwrap_readonly(_own_res.wrapped)
    return (isinstance(_own_inner, TupleType)
            and _own_inner.has_pointer_repr_element()
            and len(req.a.elements) == len(_own_inner.element_types))


def _r_own_proto_container_slot(req: _ArgReq) -> bool:
    return _own_proto_container_slot(req.a, req.ptype, req.analyzer,
                                     req.locals_) is not None


def _r_own_str_literal(req: _ArgReq) -> bool:
    if not isinstance(_peel_coerce(req.a), TpyStrLiteral):
        return False
    w_str = _plain_own_slot(req.ptype)
    return w_str is not None and is_str_type(w_str)


def _r_generic_list_literal(req: _ArgReq) -> bool:
    lit = _peel_coerce(req.a)
    return (isinstance(lit, TpyArrayLiteral) and is_list(req.ptype)
            and _container_literal_arg(lit, req.ptype, req.analyzer))


def _r_generic_own_list_literal(req: _ArgReq) -> bool:
    lit = _peel_coerce(req.a)
    if not isinstance(lit, TpyArrayLiteral):
        return False
    _own_l = _plain_own_slot(req.ptype)
    return (_own_l is not None and is_list(_own_l)
            and _container_literal_shape_ok(lit, _own_l, req.analyzer))


# The record-method family's own cells. Each is a shape no other family
# carries today; the ones whose name echoes a shared row (`optional_ptr`,
# `union_pass_through`, `str_literal_value_opt_coerced`,
# `union_member_lift`, `record_rvalue_temp`) hold a DIFFERENT predicate and
# so get their own name -- `register_sink` would reject reusing the shared
# one.

def _x_temps_and_frame(req: _ArgReq) -> bool:
    """`temps_ok and frame_capturing and` as a PRE-guard, in the ladder's
    order: the factory-temp row hoists a scope-local the frame borrows, so
    it needs both a flush position and a frame to borrow into."""
    return req.temps_ok and req.frame_capturing


def _r_plain_scalar_slot(req: _ArgReq) -> bool:
    return (_plain_scalar_slot(req.ptype, req.analyzer)
            and _resolved_scalar(req.analyzer.get_expr_type(req.a),
                                 req.analyzer))


def _r_tparam_scalar(req: _ArgReq) -> bool:
    return (_is_type_param_slot(req.ptype)
            and _resolved_scalar(req.analyzer.get_expr_type(req.a),
                                 req.analyzer))


def _r_tparam_open_pass(req: _ArgReq) -> bool:
    return _open_tparam_pass_arg(req.a, req.ptype, req.locals_, req.analyzer,
                                 req.param_names)


def _r_float_literal_pass_through(req: _ArgReq) -> bool:
    return _float_literal_pass_through_arg(req.a, req.ptype, req.locals_,
                                           req.analyzer)


def _r_int_literal_bigint(req: _ArgReq) -> bool:
    return _int_literal_bigint_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_value_tuple_pass_through(req: _ArgReq) -> bool:
    # `mutated` threaded like the sibling `str_pass_through` cell, and inert
    # today: no family carrying this row sets `mutated_slots`, so it is the
    # ladder's bare call either way.
    return _value_tuple_pass_through_arg(req.a, req.ptype, req.locals_,
                                         req.analyzer,
                                         mutated=req.mutated_slots)


def _r_span_coerce(req: _ArgReq) -> bool:
    return _span_coerce_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_slice_ctor_pass_through(req: _ArgReq) -> bool:
    return _slice_ctor_pass_through_arg(req.a, req.ptype, req.locals_,
                                        req.analyzer)


def _r_own_scalar_rvalue(req: _ArgReq) -> bool:
    return _own_scalar_rvalue_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_value_opt_view_whole(req: _ArgReq) -> bool:
    return _value_opt_view_whole_arg(req.a, req.ptype, req.locals_,
                                     req.narrowed, req.analyzer)


def _r_value_record_rvalue(req: _ArgReq) -> bool:
    return _value_record_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_value_opt_record_rvalue(req: _ArgReq) -> bool:
    return _value_opt_record_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_value_record_name(req: _ArgReq) -> bool:
    return _value_record_name_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _r_own_tparam_call_rvalue(req: _ArgReq) -> bool:
    return _own_tparam_call_rvalue_arg(req.a, req.ptype, req.analyzer)


def _r_union_member_lift_none(req: _ArgReq) -> bool:
    # The method position admits the `None` LEG only: the member-NAME leg of
    # the shared `union_member_lift` row has no witness here (deliberate
    # conservatism, not a hole), so this is a narrower shape with its own
    # name rather than a reuse of that cell.
    return (isinstance(req.a, TpyNoneLiteral)
            and _union_member_lift_arg(req.a, req.ptype, req.locals_,
                                       req.analyzer))


def _r_optional_ptr_no_temp(req: _ArgReq) -> bool:
    # Hardcoded temps_ok=False where the shared `optional_ptr` cell threads
    # the position's own flag: this ladder admits the pointer-repr Optional
    # faces that need no temp, and gives its two temp-bearing faces
    # (container literal, scalar pointee) their own flush-gated rows below.
    return _optional_ptr_arg(req.a, req.ptype, req.locals_, req.analyzer,
                             temps_ok=False)


def _r_record_pass_through(req: _ArgReq) -> bool:
    return _record_pass_through_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer)


def _r_method_ctor_rvalue(req: _ArgReq) -> bool:
    return _method_ctor_rvalue_arg(req.a, req.ptype, req.index, req.overload,
                                   req.locals_, req.analyzer)


def _r_record_rvalue_temp_factory(req: _ArgReq) -> bool:
    # `frame_capturing=True`, where the shared `record_rvalue_temp` cell
    # passes `upcast_ok=True`: a different render (the frame borrows a
    # scope-local) and so a different shape.
    return _record_rvalue_temp_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer, frame_capturing=True)


def _r_tparam_slot_temp(req: _ArgReq) -> bool:
    return _tparam_slot_temp_arg(
        req.a, req.ptype, req.index, req.overload, req.analyzer) is not None


def _r_struct_proto_union(req: _ArgReq) -> bool:
    return _struct_proto_union_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_method_value_union(req: _ArgReq) -> bool:
    return _method_value_union_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer)


def _r_union_ctor_temp(req: _ArgReq) -> bool:
    return _union_ctor_temp_arg(req.a, req.ptype, req.analyzer)


def _r_union_bytes_literal_temp(req: _ArgReq) -> bool:
    return _union_bytes_literal_temp_arg(req.a, req.ptype,
                                         req.analyzer) is not None


def _r_union_pass_deep_const(req: _ArgReq) -> bool:
    # The shared `union_pass_through` shape RESTRICTED by the callee's
    # deep-const-borrow verdict: at a dcbp slot the arg takes the
    # const-conversion wrap, which the method loop only threads for an
    # un-narrowed NAME. Guard and predicate in the ladder's order -- the
    # pass-through test runs first -- so it is one row, not the shared cell
    # under an `extra`.
    if not _union_pass_through_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer):
        return False
    overload, index = req.overload, req.index
    dcbp = (overload is not None
            and overload.const_borrow_params
            and index in overload.const_borrow_params)
    return (not dcbp
            or (isinstance(req.a, TpyName) and req.a.name not in req.narrowed))


def _r_value_opt_scalar_value(req: _ArgReq) -> bool:
    return _value_opt_scalar_value_arg(req.a, req.ptype, req.analyzer)


def _r_str_literal_value_opt(req: _ArgReq) -> bool:
    # The BARE spelling. The plain ladder peels the coerce first and owns
    # the `str_literal_value_opt_coerced` name, so the two admit different
    # shapes and stay separate cells.
    return _str_literal_value_opt_arg(req.a, req.ptype)


def _r_bytes_literal_value_opt(req: _ArgReq) -> bool:
    return _bytes_literal_value_opt_arg(req.a, req.ptype)


def _r_optional_ptr_container_temp(req: _ArgReq) -> bool:
    # The literal half of the shared 'container_temp' face: the method
    # position's render arm hoists a LITERAL's typed temp; a
    # container-returning call at the same slot has no method-position
    # render yet.
    return (isinstance(req.a, (TpyArrayLiteral, TpyDictLiteral,
                               TpySetLiteral))
            and _optional_ptr_arg_face(req.a, req.ptype, req.locals_,
                                       req.analyzer) == 'container_temp')


def _r_optional_ptr_scalar_temp(req: _ArgReq) -> bool:
    return _optional_ptr_arg_face(req.a, req.ptype, req.locals_,
                                  req.analyzer) == 'scalar_temp'


def _pre_container_slot_family(req: _ArgReq) -> 'bool | None':
    """The method family's slot-keyed PROLOGUE -- three container-ELEMENT slot
    families whose verdict is decided entirely by the slot, ahead of the row
    walk. Named for the slots it reads, not for the receiver: a view or scalar
    stub has no element slot, so every leg falls through for one.

    Two of them REJECT everything they do not name, which is why they are a
    prologue and not rows: a row tuple can only admit. The view fence is
    also what makes the family's `own_lvalue` cell safe without a temps_ok
    guard -- it takes the VIEW payloads out of the Own cascade before they
    reach it, leaving `Own[str]` as the only position-sensitive payload, and
    that one has its guard re-derived at `_lower_call_arg`. That argument is
    about the STUB slot, which is where the fence rejects and where
    `temps_ok` is False; at a record signature the view payloads do reach the
    cascade and `own_lvalue`'s own flush guard answers for them.

    Every insert slot the legs are about is spelled `Own[T]` in the stubs
    (`list.append`, `list.insert`, `list.__setitem__`, `set.add`,
    `dict.__setitem__`), so a genuine `Own` slot is the head test -- not
    `_elem_slot_type`'s peel-Own-IF-PRESENT, under which a record method's
    plain union param (`datetime.astimezone(tz: timezone | ZoneInfo | None)`)
    reads as an element slot and takes leg 3's reject.
    """
    a, ptype, analyzer = req.a, req.ptype, req.analyzer
    if _plain_own_slot(ptype) is None:
        return None
    # An Own[view] INSERT slot (`set[StrView].add` -- the view-key family)
    # admits LITERALS only: a name/expr arg takes the copy+move view
    # temp (`std::string_view __tmp_N{name};` + move), which this row
    # does not build. The fence is about the container insert, so it
    # rejects at a STUB slot only -- a user record's `Own[StrView]` param
    # is an ordinary by-value slot whose NAME source the Own cascade rows
    # answer for (`calls/method_arg_own_view_slot`).
    _ovv = _plain_own_slot(ptype)
    if (_ovv is not None
            and (is_str_view_type(unwrap_readonly(_ovv))
                 or is_bytes_view_type(unwrap_readonly(_ovv)))
            and not isinstance(_peel_coerce(a),
                               (TpyStrLiteral, TpyBytesLiteral))):
        if req.overload is None:
            return note_detail("method.arg_shape")
        return None
    # A wrapper element slot may carry the unresolved alias placeholder
    # (`Own[Json]` on list[Json].append) -- resolve to its union body.
    _es = _resolve_plain_alias(_elem_slot_type(ptype), analyzer)
    if _wrapper_union_like(_es, analyzer) is not None:
        # A WRAPPER-union element slot -- non-generic OR a generic
        # instance (`list[Tree[int32]].append`) -- absorbs a
        # scalar-literal insert via the wrapper's converting ctor
        # (`__arr.push_back(4);` at `list[Json]`) -- the bare token
        # render, same bounds as the ru-literal elements. Other sources
        # keep the named reject.
        _lit = _peel_coerce(a)
        if (isinstance(_lit, (TpyIntLiteral, TpyFloatLiteral,
                              TpyBoolLiteral))
                and _ru_elem_ok(_lit, analyzer)):
            return _witness("arg.ru_wrapper_elem_literal")
        # A same-wrapper NAME binding (`a.append(item)` on
        # `list[JsonValue]`): the wrapper is a by-value struct, so the
        # insert takes it exactly as the plain-union leg below takes a
        # value-variant name -- with the arg arm owning the copy-vs-move
        # spelling off the binding set.
        if _wrapper_union_elem_name_arg(a, ptype, req.locals_, req.narrowed,
                                        analyzer):
            return True
        return note_detail("method.arg_shape")
    if isinstance(_es, UnionType):
        # A plain UNION element slot takes two rows: the member
        # ctor rvalue the variant absorbs, and a same-union NAME (the
        # `to_value_variant` lift for a ptr-variant binding, the bare copy
        # for a value-variant one -- the arg arm keys the split on the
        # BINDING set). Every other source stays out.
        if (isinstance(a, TpyName) and a.name not in req.narrowed
                and a.name in req.locals_):
            _at = analyzer.get_expr_type(a)
            _at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_at)))
                   if _at is not None else None)
            if isinstance(_at, OwnType):
                _at = unwrap_readonly(_at.wrapped)
            if _at == _es:
                return True  # witnessed at the arg arm / copy machinery
        return (_union_member_ctor_slot_arg(a, ptype, analyzer)
                or note_detail("method.arg_shape"))
    return None


def _pre_generic_slot_family(req: _ArgReq) -> 'bool | None':
    """The generic family's slot-keyed PROLOGUE: the two slot shapes whose
    verdict is settled before the row walk, transcribed from the ladder's
    leading branches.

    Both REJECT everything they do not name, which is why they are a
    prologue and not rows -- a row tuple can only admit. Both also decide
    against the UNSUBSTITUTED slot (`open_ptype`) where every row decides
    against the substituted one, so neither shape could be spelled as a
    cell shared with another family even if one carried it.
    """
    a, ptype, resolved = req.a, req.open_ptype, req.ptype
    locals_, analyzer = req.locals_, req.analyzer
    narrowed, temps_ok = req.narrowed, req.temps_ok
    if ptype is None:
        return note_detail("call.generic_arg_slot")
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
        # The FIELD twin of the bare-T name row (`identity[T](self.val)` ->
        # `identity<T>(this->val)`): a markers-clean same-T field read
        # renders bare into the open ref slot.
        if (isinstance(ptype, TypeParamRef) and isinstance(a, TpyFieldAccess)
                and _field_markers_clean(a)
                and _field_receiver_ok(a, locals_, analyzer)):
            _ft = analyzer.get_expr_type(a)
            _ftu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_ft)))
                    if _ft is not None else None)
            if (isinstance(_ftu, TypeParamRef)
                    and isinstance(resolved, TypeParamRef)
                    and _ftu.name == resolved.name):
                return _witness("call.generic_open_slot_field")
        # An `Own[T]` slot fed by the caller's own `Own[T]`-declared param
        # NAME (`sink(x)` forwarding inside a generic body ->
        # `sink<T>(std::move(x))`): `_own_lvalue_temp_slot`'s type-param
        # row keys the same-T pairing, and the movable last-use facts
        # apply inside template bodies exactly like the concrete Own slot.
        if _own_move_arg(a, ptype, locals_, analyzer):
            return True
        # ... and the record ladder's rvalue-CALL row at the same slot,
        # deciding against the SUBSTITUTED payload since that is the type
        # the argument carries. Called, not restated: this family's
        # prologue settles every open slot before the row walk runs, so
        # the row cannot be spelled as a cell here.
        if _own_tparam_call_rvalue_arg(a, resolved, analyzer):
            return True
        # ... and the Own-slot copy+move row at a slot whose payload is
        # still COMPOSITE in T (`poll_ready(empty)` with `empty: list[T]`
        # at `Own[T]` resolved `Own[list[T]]`). The open slot's bare `T`
        # never matches the composite binding, so the temp-free row above
        # cannot reach it; the SUBSTITUTED payload does, and it is the type
        # `_lower_call_arg` renders against -- the same helper, the same
        # slot, so the two cannot disagree. A borrow-form source (param,
        # field) resolves the slot through the ref wrapper instead, which
        # this payload compare rejects: exactly right, since that slot
        # hoists a defensive copy passing the name bare would drop.
        # A payload that is still a BARE type param stays out: that slot
        # belongs to the temp-free rows above, and the copy+move helper
        # also admits shapes (a ternary) the `T&&` slot cannot take.
        _own_payload = _plain_own_slot(resolved)
        if (temps_ok and _own_payload is not None
                and not _is_type_param_slot(_own_payload)
                and _own_lvalue_arg(a, resolved, locals_, narrowed,
                                    analyzer,
                                    param_names=req.param_names)):
            return _witness("call.generic_own_composite_slot")
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
                # lvalue bare -- only temporaries hoist
                # (is_temporary_expr), and a NAME is never one. @dynamic
                # slots keep their adapter temps and reject.
                if (a.name not in narrowed
                        and isinstance(resolved, NominalType)
                        and resolved.is_protocol
                        and not is_dyn_protocol(resolved)
                        and isinstance(au, NominalType)
                        and au.is_user_record and not au.is_protocol
                        and _witness("call.generic_open_proto_name")):
                    return True
        # ... and the NAME row's FIELD twin (`_parse_rows(self._fp, ..)` at
        # `Ptr[R]` resolved `Ptr[W]`): a markers-clean field read of exactly
        # the substituted slot binds the same lvalue-ref template param bare.
        # The bare-T field row above covers only an unparameterized slot.
        if (isinstance(a, TpyFieldAccess) and not isinstance(resolved, OwnType)
                and _field_markers_clean(a)
                and _field_receiver_ok(a, locals_, analyzer)):
            _cft = analyzer.get_expr_type(a)
            _cftu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_cft)))
                     if _cft is not None else None)
            if (_cftu is not None and _cftu == resolved
                    and _witness("call.generic_open_slot_field_composite")):
                return True
        # A lambda at a still-open Fn slot (`map_keys(pairs, lambda p:
        # p[1])` in a generic caller): the lambda renders itself -- its
        # param spelling (incl. `val_or_ptr_t<T>`) comes from its own
        # sema types, not the slot.
        if (_lambda_routable(a, analyzer)
                and _witness("call.generic_open_slot_lambda")):
            return True
        return note_detail("call.generic_arg_slot")
    gslot = _generic_arg_slot(
        a, ptype, resolved, locals_, req.param_names, analyzer)
    if isinstance(ptype, TypeParamRef):
        # A row hoisting a temp is flush-gated; one whose INSTANTIATED slot
        # binds the rvalue outright renders inline, so it needs no flush
        # position (a `while` condition takes it).
        def _rvalue_ok() -> bool:
            if gslot is None or not gslot.needs_temp:
                return True
            return temps_ok or note_detail("call.generic_arg_shape")
        if _eligible_scalar(resolved):
            lit = _peel_coerce(a)
            if isinstance(lit, (TpyIntLiteral, TpyFloatLiteral,
                                TpyBoolLiteral)):
                return _rvalue_ok()
            if isinstance(a, TpyName):
                return ((a.name != "self" and a.name in locals_
                         and _resolved_scalar(locals_.get(a.name), analyzer))
                        or note_detail("call.generic_arg_shape"))
            # A scalar-typed call rvalue (`pair(float64(2.5), x)`) renders
            # inline at a value-typed slot; the scalar-ctor arm folds it.
            if (isinstance(lit, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, lit)
                    and _resolved_scalar(analyzer.get_expr_type(lit),
                                         analyzer)):
                return _rvalue_ok()
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
                # A view-family NAME into a slot resolved into the same
                # family passes bare (`get_length<std::string_view>(msg)`,
                # `has_item<::tpy::Bytes>(keys, k)`): the slot resolves to the
                # twin's own parameter form, which the name's read already is.
                # The pending-aware classifier resolves an inference-pending
                # slot form.
                or (_resolved_viewfam_value(resolved, analyzer) is not None
                    and _resolved_viewfam_value(analyzer.get_expr_type(a),
                                                analyzer) is not None)
                # A NESTED value-tuple NAME (`less(a, b)` on
                # `((1, 2), "x")` at a bounded T): the recursive value
                # family binds the ref slot bare like the flat row.
                # NAMES only -- a nested literal's spelled render is the
                # flat family's.
                or (_value_tuple_nested(resolved, analyzer) is not None
                    and a.name in locals_
                    and _value_tuple_nested(locals_.get(a.name), analyzer)
                    is not None
                    and _witness("call.generic_nested_tuple_name"))):
            return True
        # Rvalue sources at a view-family-resolved slot (a str / bytes literal
        # or a by-value call of either family) and `None` into a
        # `std::monostate` slot: those instantiations are value-typed, so the
        # slot is the twin's own parameter form and the rvalue renders inline
        # -- `_rvalue_ok` re-asks per instantiation and flush-gates only where
        # a temp is still owed. Record rvalues stay out (a covariant upcast
        # declares that temp with the CHILD type, a render this row does not
        # build).
        lit = _peel_coerce(a)
        if _resolved_viewfam_value(resolved, analyzer) is not None and (
                isinstance(lit, (TpyStrLiteral, TpyBytesLiteral))
                or (isinstance(lit, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, lit))):
            return _rvalue_ok()
        if isinstance(resolved, NoneType) and isinstance(lit, TpyNoneLiteral):
            return _rvalue_ok()
        # A list literal into a container-resolved T slot (`use([1, 2])` ->
        # `std::vector<int32_t> __tmp_2 = {1, 2};`): the ref-slot temp with
        # the decl sink's target-typed brace init.
        if (isinstance(lit, TpyArrayLiteral) and is_list(resolved)
                and _container_literal_shape_ok(lit, resolved, analyzer)):
            return temps_ok or note_detail("call.generic_arg_shape")
        # An F1-record rvalue whose type is EXACTLY the resolved slot
        # (`take[IntBox](IntBox(42))` -> `IntBox __tmp_1 = IntBox(42);`):
        # the same ref-slot temp the str/call rows hoist. A COVARIANT
        # upcast stays out -- that temp is declared with the CHILD
        # type, a render this row does not build.
        if (isinstance(lit, (TpyCall, TpyMethodCall))
                and _f1_record(resolved, analyzer)
                and is_rvalue_source(analyzer, lit)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(lit)))) == resolved):
            return temps_ok or note_detail("call.generic_arg_shape")
        # A tuple LITERAL into a value-tuple-resolved T slot renders the
        # spelled brace prvalue INLINE (`push_t<...>(pq, std::tuple<...>{2,
        # "second"})`) -- unlike the str/list rows, no ref-slot temp: the
        # const-ref template param binds the prvalue for the full
        # expression. The recursive value family covers nested tuple
        # elements; pointer-repr elements keep rejecting. Bare literals only
        # (no outer coerce -- unwitnessed).
        if (isinstance(a, TpyTupleLiteral)
                and (_value_tuple(resolved, analyzer) is not None
                     or _value_tuple_nested(resolved, analyzer) is not None)):
            return True
        return note_detail("call.generic_arg_slot")
    return None

_PROTOCOL_ARG_SINK = register_sink(_ArgSink(
    family="protocol",
    note="method.protocol.arg_shape",
    rows=(
        _ArgRow("shared_pass_through", _r_shared_pass_through),
        # A same-wrapper NAME at the protocol method's genrec slot
        # (`s.absorb(t)`): the bare-name pass-through, exactly the
        # record ladder's wrapper row.
        _ArgRow("ru_wrapper_name", _r_ru_wrapper_name),
        # The pointer-repr Optional slot faces (`s.get().total(d)` at a
        # `dict[str, int32] | None` protocol-method param -> `&(d)`):
        # a protocol method call runs the same optional-ptr
        # dispatch as the record ladder, whose row this mirrors. Temp-free
        # faces only -- the family gate has no flush position to thread.
        _ArgRow("optional_ptr", _r_optional_ptr,
                face="method.protocol_optional_ptr"),
        # A record RVALUE at a BY-VALUE record slot of the protocol method
        # (`aw.__poll__(Waker())`): a ValueType record param is no ref slot,
        # so no temp cascade fires and the ctor expansion binds inline --
        # the record ladder's row, over the same lazy arg loop.
        _ArgRow("value_record_rvalue", _r_value_record_rvalue),
        # A tuple LITERAL at the protocol method's tuple slot
        # (`s.consume((v, int32(2)))`): the free-call ladder's row --
        # the lazy arg loop this family uses IS the free-call
        # loop, so the borrow/value builders own the per-element
        # verdict here exactly as they do there.
        _ArgRow("tuple_literal", _r_tuple_literal),
        # A narrowed wide ptr-opt NAME at its POINTEE slot: the plain
        # ladder's row -- the deref renders in place, so it is temp-free
        # (see `_PLAIN_ARG_SINK`'s cell for the whole reason).
        _ArgRow("wide_opt_deref_name", _r_wide_opt_deref_name),
        # The sibling families' TEMP-FREE cells. Temp-free is the whole
        # admission rule here -- the family gate threads `temps_ok=False`,
        # so a cell that needs a flush position cannot fire even if it is
        # listed -- and each of these renders its source in place through
        # the same slot-keyed lowering arm a record method's argument takes.
        # The temp-hoisting cells (the container comprehension, the record
        # rvalue, the value-union and wrapper temps, the covariant upcast)
        # stay out: there is no flush position to thread them to.
        _ArgRow("func_ref", _r_func_ref),
        _ArgRow("lambda", _r_lambda),
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        # AHEAD of the Own cascade, for the reason it is there in every
        # other family: the view->owned convert runs before the move/copy
        # cascade, so a VIEW-form str name must be absorbed here.
        _ArgRow("str_owned_slot", _r_str_owned_slot),
        _ArgRow("str_pass_through", _r_str_pass_through),
        _ArgRow("container_field_pass", _r_container_field_pass),
        _ArgRow("container_module_var", _r_container_module_var),
        _ArgRow("record_field_marker", _r_record_field_marker),
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        _ArgRow("container_literal_method", _r_container_literal_method),
        _ArgRow("none_unit", _r_none_unit),
        _ArgRow("none_value_opt", _r_none_value_opt),
        _ArgRow("value_opt_member", _r_value_opt_member),
        _ArgRow("union_pass_through", _r_union_pass_through),
        _ArgRow("union_member_lift", _r_union_member_lift),
        _ArgRow("union_coerced_literal", _r_union_coerced_literal),
        _ArgRow("protocol_slot", _r_protocol_slot),
    )))


_NATIVE_ARG_SINK = register_sink(_ArgSink(
    family="native",
    # Ranked by SLOT SHAPE rather than one opaque bucket -- the drilldown
    # `probe_corpus.py` histograms the native_arg mass on.
    note=lambda req: _native_arg_reject(req.a, req.ptype, req.analyzer),
    rows=(
        _ArgRow("shared_pass_through", _r_shared_pass_through),
        # Callable args at a template callee (`builtin_filter(
        # is_even, nums)`): a func-ref renders its bare C++ name and a
        # lambda its inline closure -- both loops emit them identically,
        # so the plain ladder's rows serve here too.
        _ArgRow("func_ref", _r_func_ref),
        _ArgRow("lambda", _r_lambda),
        # ... and so does a Callable VALUE bound to a name (`map(h, xs)`
        # -> `::tpy::builtin_map<..>(h, ..)`): the std::function converts
        # implicitly into the template's Fn slot, bare on both loops.
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        _ArgRow("own_move", _r_own_move),
        # witnessed at the lowering arm (call.native_own_scalar_lvalue)
        _ArgRow("native_own_scalar_lvalue", _r_native_own_scalar_lvalue),
        _ArgRow("native_iterable_container", _r_native_iterable_container),
        # A container FIELD read binding a plain container ref slot
        # (`bytes(self.buffer)` -> `::tpy::Bytes(this->buffer)`): the
        # member read binds the slot bare, exactly as the bare-NAME twin
        # already in the shared row. Same cell the marker families carry.
        _ArgRow("container_field_pass", _r_container_field_pass),
        # A list/Span-yielding SLICE subscript (`len(items[1:1])` ->
        # `__len__(::tpy::list_slice(items, BasicSlice{1, 1}))`): the
        # inline rvalue render is position-independent, same row the
        # container-instantiation arg takes.
        _ArgRow("inst_slice", _r_inst_slice,
                face="arg.native_slice_subscript"),
        # A container ternary of declared names at a protocol slot
        # (`len(a if flag else b)`): the ifexpr container arm renders
        # the lvalue ternary, bound bare like the single-name row.
        _ArgRow("container_ternary", _r_container_ternary),
        _ArgRow("native_iterable_call", _r_native_iterable_call),
        _ArgRow("native_iterable_range", _r_native_iterable_range),
        _ArgRow("native_iterable_iterator_call",
                _r_native_iterable_iterator_call),
        _ArgRow("native_iterable_genexpr", _r_native_iterable_genexpr),
        _ArgRow("native_iterable_literal", _r_native_iterable_literal),
        _ArgRow("native_value_call", _r_native_value_call),
        _ArgRow("native_container_call", _r_native_container_call),
        _ArgRow("native_record_call", _r_native_record_call),
        # A bare-name conformer into a monomorphized protocol slot of a
        # native/template callee (`repr(p)` -> `::tpy::repr_of(p)`): the
        # native arg loop renders it bare (`protocol_slots=False`), no
        # adapter wrap, so only the no-temp bare row admits here -- the
        # family threads temps_ok=False, so the row reads it and gets it.
        _ArgRow("protocol_slot", _r_protocol_slot),
        # A value-typed arg (scalar / char / str family) at a native
        # protocol slot (`hash("hello")` -> `::tpy::__hash__("hello")`):
        # the native loop renders the value bare, position-independent.
        _ArgRow("native_protocol_value", _r_native_protocol_value),
        # ... and the OPEN-T CALL rvalue at the same slot
        # (`hash(self.get())` inside a `[T: Hashable]` body): the slot
        # monomorphizes to T, so the call binds inline exactly as the
        # value rows above bind their concrete results.
        _ArgRow("native_protocol_open_call", _r_native_protocol_open_call),
        _ArgRow("native_optptr_name", _r_native_optptr_name),
        # A union-typed NAME at the same slot (`repr(a)` over a
        # variant binding): bare, the runtime variant overload visits.
        _ArgRow("native_union_name", _r_native_union_name),
        _ArgRow("native_protocol_tuple_literal",
                _r_native_protocol_tuple_literal),
        _ArgRow("native_protocol_field", _r_native_protocol_field),
        _ArgRow("native_iterable_comp", _r_native_iterable_comp),
        # A storage-form tuple NAME at a native/template tuple slot takes
        # the same `tuple_to_pointer` lift as at a plain callee
        # (`str(t)` -> `::tpy::tuple_to_str(tuple_to_pointer<..>(t))`);
        # the lift wraps the read, so it is position-independent.
        _ArgRow("borrow_tuple_storage_name", _r_borrow_tuple_storage_name),
        # An F3-tuple FIELD read at the native/template tuple slot takes
        # the same kind-blind `tuple_to_pointer` lift as the NAME row
        # (`repr(self.pair)` -> `::tpy::tuple_to_str(tuple_to_pointer<
        # std::tuple<const Point*, ..>>(this->pair))`).
        _ArgRow("borrow_tuple_field", _r_borrow_tuple_field),
        # A bare NAME at an OPEN value-tuple slot (`min(a, b, key=..)` on
        # `tuple[T, int32]` params): no lift -- the open tuple has no
        # borrow/storage duality until T binds, so the name passes bare
        # like the concrete value-tuple rows.
        _ArgRow("open_value_tuple_name", _r_open_value_tuple_name,
                face="arg.open_value_tuple_name"),
    )))


# THE method argument. One row listing for every receiver a method call can
# have -- the builtin stubs (containers, bytearray, the str/bytes views, the
# scalars, the ptr template) and user records alike. What decides an argument
# is the resolved SLOT and the argument's shape, never which receiver kind led
# to the call, so one listing rather than one per receiver kind: a shape opened
# for a container receiver cannot be closed to a record receiver by omission.
#
# ORDER: the builtin-stub rows first, then the rows only a user record's
# signature reaches. Both halves only admit, so where a shape is in both the
# leading cell is the one that decides it -- measured render-identical over
# the whole corpus in that order (`own_lvalue` and `container_comp` are the
# two cells whose guards differ between the halves; see their comments).
_METHOD_ARG_SINK = register_sink(_ArgSink(
    family="method_arg",
    note="method.arg_shape",
    pre=_pre_container_slot_family,
    rows=(
        _ArgRow("scalar_at_template_slot", _r_scalar_at_template_slot),
        # A structural-protocol param NAME at the stub's Iterable slot
        # (`target.extend(items)` -> `::tpy::list_extend(target, items)`,
        # `sep.join(items)` -> `::tpy::str_join(sep, items)`): the
        # monomorphized lvalue binds bare.
        _ArgRow("protocol_bare_name", _r_protocol_bare_name),
        # ... and the CopyIter conformer at the Iterable[Own[T]] slot
        # (`a.extend(copy_iter(b))` / a CopyIter NAME): binds bare too.
        _ArgRow("copy_iter_own_elem", _r_copy_iter_own_elem),
        _ArgRow("str_pass_through", _r_str_pass_through),
        # AHEAD of `own_lvalue` on purpose: it absorbs the VIEW-form str
        # sources into the inline `std::string(x)` convert, so only
        # OWNED-form str names reach the Own cascade below.
        _ArgRow("str_owned_slot", _r_str_owned_slot),
        # The bytes twin of the owned-str-slot row, LITERAL only:
        # `bs.append(b"xyz")` at an `Own[bytes]` element slot renders
        # the owned literal (`bytes_literal_owned`) -- the THIR bytes
        # literal's default form, which the span pin correctly skips
        # for an Own slot. View-form NAMES need the S6 materialize
        # convert and stay deferred pending a witness.
        _ArgRow("bytes_owned_literal", _r_bytes_owned_literal,
                face="arg.bytes_owned_literal"),
        # The BYTES-VIEW sibling (`set[BytesView].add(b"x")` -- the
        # view-key family): the literal takes the static view spelling
        # via the span pin (`s.insert(::tpy::bytes_literal("x", 1))`).
        _ArgRow("bytes_view_literal", _r_bytes_view_literal,
                face="arg.bytes_view_literal"),
        # ... and its VIEW-form sibling (the S6 witness arrived):
        # a bytes param / narrowed deref / view slice at the same slot
        # takes the `::tpy::Bytes(x)` materialize convert.
        _ArgRow("bytes_owned_slot", _r_bytes_owned_slot),
        # ... and the OWNED-form source at the same slot -- name or
        # member read -- which needs no convert and no copy temp: the
        # cpp_template callee binds the lvalue natively.
        _ArgRow("bytes_owned_lvalue", _r_bytes_owned_lvalue,
                extra=_x_insert_own_slot),
        # ... and its RVALUE face: a prvalue has nothing to move from, so
        # it binds the slot with no temp either.
        _ArgRow("bytes_owned_call_rvalue", _r_bytes_owned_call_rvalue),
        _ArgRow("bytes_pass_through", _r_bytes_pass_through),
        _ArgRow("char_pass_through", _r_char_pass_through),
        _ArgRow("enum_pass_through", _r_enum_pass_through),
        # ... and the `Own[enum]` ELEMENT slot (`roles.append(r)` at
        # `list[Role]`): Own on a value type is a no-op spelling, so
        # the insert renders the bare name -- no copy temp (the Own
        # cascade's `_own_lvalue_temp_slot` is None for a value
        # payload). Container-insert position only; the by-value
        # `Own[enum]` PARAM slot of a user method has no witness.
        _ArgRow("own_enum_elem", _r_own_enum_elem, face="arg.own_enum_elem",
                extra=_x_insert_own_slot),
        _ArgRow("ptr_pass_through", _r_ptr_pass_through),
        _ArgRow("container_pass_through", _r_container_pass_through),
        # The bare render binds a PRVALUE, which only a const slot takes --
        # the same slot guard the comprehension cell carries.
        _ArgRow("container_slot_call_rvalue", _r_container_slot_call_rvalue,
                face="arg.container_call_rvalue", extra=_x_comp_slot_const),
        _ArgRow("own_record_rvalue", _r_own_record_rvalue),
        _ArgRow("copy_own", _r_copy_own),
        _ArgRow("own_move", _r_own_move),
        # The flush guard is the ONE thing the two halves spelled
        # differently at this cell, because their Own slots are different
        # C++ -- see `_x_own_lvalue_flush`. At a stub slot the copy is the
        # insert's own const-ref overload, so no temp and no flush position
        # is needed: the prologue's view fence removes the view payloads,
        # `str_owned_slot` above absorbs the VIEW-form str sources and
        # `own_move` takes the temp-free MOVE half, leaving `Own[str]` as
        # the only position-sensitive payload -- and `_lower_call_arg`
        # re-derives its flush requirement, raising `call.own_str_no_flush`.
        _ArgRow("own_lvalue", _r_own_lvalue, extra=_x_own_lvalue_flush),
        # The structural Iterable/Sequence slot of a stub method
        # (`xs.extend([4, 5])` / `xs.extend(b)` -- the C++ template
        # binds the container bare; a movable last-use name takes the
        # consuming `::tpy::own_iter(std::move(b))` wrap at lowering).
        _ArgRow("native_iterable_literal", _r_native_iterable_literal),
        _ArgRow("native_iterable_container", _r_native_iterable_container),
        # ... and the container FIELD read at the same slot
        # (`",".join(self._parts)` -> `::tpy::str_join(",", this->_parts)`):
        # the member read binds the template bare like the NAME row.
        _ArgRow("native_iterable_field", _r_native_iterable_field),
        _ArgRow("native_iterable_call", _r_native_iterable_call),
        # ... and the COMPREHENSION source at the same structural slot
        # (`primes.extend([i for i in xs if p(i)])`): the stmt-expr renders
        # INLINE into the template slot, target-typed by the comprehension's
        # own container -- the native family's row, and the same
        # `_lower_call_arg` arm renders it here.
        _ArgRow("native_iterable_comp", _r_native_iterable_comp),
        _ArgRow("own_iter_special", _r_own_iter_special),
        # A nested list literal into an Own[list] element slot
        # (`rows.append([9, 9])` -> `push_back({9, 9})`).
        _ArgRow("own_container_literal", _r_own_container_literal),
        # ... and the comprehension into the same Own[container] slot:
        # the stmt-expr prvalue moves in inline.
        _ArgRow("own_container_comp", _r_own_container_comp),
        _ArgRow("any_pass_through", _r_any_pass_through),
        # The IN-PLACE render, so the slot must be a const borrow for the
        # same reason the comprehension cell beside it must: a brace-init is
        # a prvalue and a mutated `std::vector<T>&` cannot bind one. Same
        # guard, same source of truth (`const_borrow_params`, with a stub's
        # always-const container slot as the None leg) -- plus the lifetime
        # half, since a temporary bound in place dies with the statement and
        # a callee that lends it back would leave a dangling reference.
        _ArgRow("container_literal_method", _r_container_literal_method,
                extra=_x_inline_container_literal),
        # ... and the HOISTING sibling for the slots that guard now declines
        # (`k.fill([3, 4])` -> `std::vector<int32_t> __tmp_N = {3, 4};`):
        # the free-call family's row, shared here rather than spelled again,
        # so a mutated slot hoists where there is a statement to flush into
        # and rejects where there is none.
        _ArgRow("container_literal", _r_container_literal, extra=_x_temps_ok),
        # ... and the COMPREHENSION at the same concrete container slot
        # (`d.update({k: k for k in ks})`): the free-call family's row, with
        # no flush guard -- the method arg loops render the stmt-expr inline
        # where the free-call position hoists an ArgTemp. The stmt-expr is a
        # prvalue, so the slot must be a const borrow: a stub's container
        # slot always is, and `_x_comp_slot_const` reads a record
        # signature's verdict for the position.
        _ArgRow("container_comp", _r_container_comp,
                extra=_x_comp_slot_const),
        # `None` into a value-repr Optional element slot
        # (`items.append(None)` on `list[int32 | None]`) -> the
        # STORAGE-form `std::nullopt`, like the free-call row.
        _ArgRow("none_value_opt", _r_none_value_opt),
        # A whole owned value-opt VIEW local at an Own[Optional[StrView]]
        # element slot (`items.append(src)`): bare for the narrowed
        # identity coerce, the `__ov` statement-expression shim for the
        # un-narrowed one -- the render row keys the registered VIEW
        # binding, so an unregistered name falls through there.
        _ArgRow("opt_view_own_elem", _r_opt_view_own_elem,
                extra=_x_insert_own_slot),
        # ... and the PARAM half (`items.append(s)` with `s:
        # Optional[str]`): the borrow `optional<view>` binding rebuilds
        # into the owned element slot under the insert's move.
        _ArgRow("opt_view_param_own_elem", _r_opt_view_param_own_elem,
                extra=_x_insert_own_slot),
        # ... and the opposite direction (a VIEW-inner source into the
        # owned element slot): the `__ov` shim, name and call alike.
        _ArgRow("opt_strview_to_str_own_elem", _r_opt_strview_to_str_own_elem,
                extra=_x_insert_own_slot),
        # A scalar VALUE into that same slot (`items.append(int32(1))`):
        # it passes bare (`push_back(1)`) -- std::optional's
        # converting constructor does the wrap, no target thread.
        _ArgRow("value_opt_scalar_elem", _r_value_opt_scalar_elem,
                extra=_x_insert_own_slot),
        # A ptr VALUE at an `Own[Ptr[T]]` element slot
        # (`ps.append(p)` / `ps.append(unsafe_cast(q))`): Own on a
        # value type is a no-op spelling and the native stub's
        # inline-template arg renders bare -- name, field and call
        # sources alike, no copy temp (unlike a user method's
        # Own[scalar] slot).
        _ArgRow("own_ptr_value", _r_own_ptr_value, face="arg.own_ptr_value",
                extra=_x_insert_own_slot),
        # `_container_method_elem`'s element families: every one lands
        # bare in the insert render -- `None` -> `std::monostate{}`, a
        # function/callable name unchanged, an open-T source by name.
        # (The union element slot answered in the prologue, on its own leg.)
        _ArgRow("none_unit", _r_none_unit),
        _ArgRow("callable_slot", _r_callable_slot),
        _ArgRow("tparam_slot", _r_tparam_slot, extra=_x_insert_own_slot),
        # A value-tuple LITERAL at an Own[value-tuple] element slot
        # (`ps.append(("k", 9))` -> `push_back(std::tuple<std::string,
        # int32_t>{"k", 9})`): the spelled value render, target-typed
        # per element like the decl sink. LITERALS only -- a tuple NAME
        # at an Own slot rides the move cascade.
        _ArgRow("own_value_tuple_literal", _r_own_value_tuple_literal,
                face="arg.own_value_tuple_literal"),
        # Its OPEN-T sibling (`pairs.append((copy(k), n))` at
        # `list[tuple[T, int]]` -> `push_back(std::tuple<T, ::tpy::BigInt>{
        # T(k), ..})`): the owning slot spells the generic element bare, so
        # the render is the by-value builder, not the `val_or_ptr_t<T>` one
        # a borrowing `tuple[T, ..]` param takes. Per-element admission
        # lives in the ladder and rejects there.
        _ArgRow("own_open_t_tuple_literal", _r_own_open_t_tuple_literal,
                face="arg.own_open_t_tuple_literal"),
        # A ref-element tuple LITERAL at an Own[tuple[T | None, ..]]
        # element slot (`pairs.append((a, b))`): the consuming storage
        # lift (`tuple_to_storage_move<S>(..)` over the borrow tuple
        # with per-element moves). Per-element admission (last-use
        # lvalue / fresh rvalue / copy() / None) lives in the ladder
        # and rejects there.
        _ArgRow("own_btuple_literal", _r_own_btuple_literal,
                face="arg.own_btuple_literal"),
        # A whole storage-tuple ELEMENT read (`pairs2.append(pairs[0])`,
        # bare `__getitem__`) or a borrow-tuple-returning CALL
        # (`pairs.append(make_pair(a, b))`, the non-move
        # tuple_to_storage lift) into the same tuple's Own slot; the
        # source's own gates re-check at lowering.
        _ArgRow("own_btuple_storage_source", _r_own_btuple_storage_source,
                extra=_x_insert_own_slot),
        # Its OPEN-T sibling (`out.append(ranked[i])` at
        # `list[tuple[T, int]]`): the generic element has no pointer repr,
        # so the read passes bare with no form conversion at all.
        _ArgRow("own_open_t_tuple_storage_source",
                _r_own_open_t_tuple_storage_source,
                face="arg.own_open_t_tuple_storage_source",
                extra=_x_insert_own_slot),
        # A MIXED-own-tuple call at the same Own element slot
        # (`xs.append(make_mixed(b))` -> `push_back(tuple_to_storage<
        # S>(make_mixed(b)))` -- the non-move materialization).
        _ArgRow("own_btuple_mixed_call", _r_own_btuple_mixed_call),
        # A NESTED-storage tuple NAME at the same Own element slot
        # (`xs.append(q)` -> `push_back(q)`): the outer tuple has NO
        # pointer-repr element (a nested reference gives no borrow
        # form), so the local owns its members and copies bare (or
        # moves at a movable name's last use).
        _ArgRow("own_btuple_nested_name", _r_own_btuple_nested_name,
                extra=_x_insert_own_slot),
        # A BORROW-form tuple NAME at the same Own element slot
        # (`xs.append(t)` -> `push_back(tuple_to_storage<S>(t))`): the
        # warned copy, lifted like the borrow-tuple CALL above.
        _ArgRow("own_btuple_borrow_name", _r_own_btuple_borrow_name,
                face="arg.own_btuple_borrow_name",
                extra=_x_insert_own_slot),
        # An owning CALL whose result IS the Own element slot
        # (`pairs.append(make_pair(1, 10))` at `Own[tuple[int32,
        # Rc[Node]]]`): a prvalue binds the by-value slot directly, so
        # the insert renders bare whatever the element family -- the
        # tuple twin of `_own_record_rvalue_arg`'s call row. Element
        # families the rows above claim are matched there first.
        _ArgRow("own_tuple_call_rvalue", _r_own_tuple_call_rvalue),
        _ArgRow("ptr_addr_of_elem", _r_ptr_addr_of_elem),
        # ---- the rows only a USER RECORD's signature reaches ----
        # No `shared_pass_through` fold: the record half spelled that fold's
        # members out one by one, in its own order and without its
        # `opt_str_shim` / `any_pass_through` / `str_literal_literal_slot` /
        # `literal_scalar_slot` members, so those stay separate cells.
        _ArgRow("lambda", _r_lambda),
        # The rest of the callable family: each renders itself
        # independently of the slot, so the free-call rows carry over to
        # an `Fn[...]` / `Callable` method slot unchanged.
        _ArgRow("func_ref", _r_func_ref),
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        _ArgRow("callable_object", _r_callable_object),
        _ArgRow("plain_scalar_slot", _r_plain_scalar_slot),
        # A scalar VALUE into a bare `T` slot (a still-PENDING deferred
        # generic's minimal fi carries the RAW method params, so the
        # slot arrives unsubstituted): `val_or_ref_t<T>` binds the
        # scalar by value and the render is bare -- the
        # arg twin of the T-field-write row. Non-scalar args keep
        # their per-shape gates (a record/container into T carries
        # borrow/move questions this row does not answer).
        _ArgRow("tparam_scalar", _r_tparam_scalar,
                face="method.tparam_scalar_arg"),
        # ... and the still-OPEN sibling: a param typed as the same `T`
        # as the slot forwards bare (`self._raw.store(value, order)` in
        # `Atomic[T].store`), the shape resolving only at instantiation.
        _ArgRow("tparam_open_pass", _r_tparam_open_pass,
                face="method.tparam_open_pass_arg"),
        _ArgRow("float_literal_pass_through", _r_float_literal_pass_through),
        _ArgRow("int_literal_bigint", _r_int_literal_bigint),
        _ArgRow("value_tuple_pass_through", _r_value_tuple_pass_through),
        # A container/record NAME or None at a NULLABLE static-protocol
        # method slot (`c2.update(nums, more)` -> `&(more)` /
        # `update(nums, static_cast<std::nullptr_t*>(nullptr))`): the
        # same lift as the free/ctor positions (the required union has
        # no None member and stays bare).
        _ArgRow("nullable_proto_addr", _r_nullable_proto_addr,
                face="arg.nullable_proto_addr"),
        _ArgRow("span_coerce", _r_span_coerce),
        _ArgRow("slice_ctor_pass_through", _r_slice_ctor_pass_through),
        _ArgRow("own_scalar_rvalue", _r_own_scalar_rvalue),
        # A source already typed as the WHOLE value-repr Optional passes
        # bare into its same-optional method slot (`conn.request(m, u,
        # body)` on `body: bytes | None`) -- the free-call ladder's row.
        _ArgRow("value_opt_pass_through", _r_value_opt_pass_through),
        # The callable twin of the row above -- the method loop is
        # target-less, so the whole optional goes bare exactly like the
        # free-call render.
        _ArgRow("value_opt_callable_pass", _r_value_opt_callable_pass),
        # ... and the value-TUPLE twin (`s.request(.., auth)` at
        # `auth: tuple[str, str] | None`): a value tuple is a value
        # type, so the whole optional binds the by-value slot bare.
        _ArgRow("value_opt_tuple_pass", _r_value_opt_tuple_pass),
        # The VIEW twin (`conn.request(m, u, body, hdrs)` at
        # `body: bytes | None`): target-less method loop -> the whole
        # optional passes bare; the free-call position keeps its shim.
        _ArgRow("value_opt_view_whole", _r_value_opt_view_whole),
        # A record RVALUE into a BY-VALUE record method slot
        # (`waw.fromutc(datetime(..))`): a ValueType record param is no
        # ref slot, so there is no temp cascade -- bare,
        # exactly as at a ctor slot.
        _ArgRow("value_record_rvalue", _r_value_record_rvalue),
        _ArgRow("value_opt_record_rvalue", _r_value_opt_record_rvalue),
        _ArgRow("value_record_name", _r_value_record_name),
        # An Array-returning call rvalue at a matching Array slot binds
        # the std::array prvalue inline -- the free ladder's row.
        _ArgRow("value_array_call", _r_value_array_call),
        # Any Own[T]-returning call rvalue -- method or free -- at the
        # record's own OPEN `Own[T]` slot (`self._storage.init(ui,
        # other._storage.take(ui))`) binds the T&& slot bare: the
        # pairing is same-T, so the callee's spelling does not matter.
        _ArgRow("own_tparam_call_rvalue", _r_own_tparam_call_rvalue),
        # A record NAME moved into an `Optional[Own[T]]` method slot
        # (`c.take(p)` at `Own[Point] | None` -- bare `std::move(p)`
        # into the by-value `std::optional<Point>` param): the
        # free/marker ladders' row; the shared lowering arm enforces
        # the move verdict and rejects the copy shape.
        _ArgRow("opt_own_record_name", _r_opt_own_record_name),
        # The `Own[record | None]` method slot (`h.store(P(42))` bare
        # ctor rvalue; NAME moves / same-Optional call rvalues bare) --
        # the free/ctor ladders' rows.
        _ArgRow("own_optional_record_rvalue", _r_own_optional_record_rvalue),
        _ArgRow("own_opt_slot", _r_own_opt_slot),
        # The pointer-repr Optional NAME (un-narrowed, may be null) at the
        # same `Own[record | None]` slot: rebuilt null-safely and moved at
        # its last use -- the constructor sink's row, one render for every
        # loop.
        _ArgRow("own_opt_ptr_name_move", _r_own_opt_ptr_name_move),
        # ... and the `Optional[Own[record]]` by-value spelling's sibling.
        _ArgRow("opt_own_ptr_opt_name_move", _r_opt_own_ptr_opt_name_move),
        # A `None` literal at a pointer-variant union method slot
        # (`s.post(url, None, ..)` at `bytes | dict[str, str] | None` ->
        # the fully spelled `pv{std::monostate{}}`, NOT `std::nullopt`):
        # the free ladder's `unionlift.none` row, whose render arm
        # `_lower_call_arg` already shares with this position. The
        # member-NAME leg of that row has no method-position witness and
        # stays out.
        _ArgRow("union_member_lift_none", _r_union_member_lift_none),
        _ArgRow("optional_ptr_no_temp", _r_optional_ptr_no_temp),
        # ... and its FIELD twin (`self._w.writerow(self.fieldnames)`): the
        # member read binds the same container ref slot bare.
        _ArgRow("container_field_pass", _r_container_field_pass),
        _ArgRow("record_pass_through", _r_record_pass_through),
        # ... and its FIELD twin (`self.soak(other.inner)`): the member
        # read binds the same record ref slot bare.
        _ArgRow("record_field_marker", _r_record_field_marker),
        # ... and its ELEMENT twin (`bag.take(things[0])`, the stub
        # `things.count(things[0])`): the checked element lvalue binds the
        # record ref slot inline.
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        _ArgRow("method_ctor_rvalue", _r_method_ctor_rvalue),
        # A record rvalue at a generator/coro factory method's ref /
        # readonly-ref slot hoists the named scope-local the frame
        # borrows (`Rec __tmp_N = Rec(41);`) -- the render lives in
        # `_method_arg`'s factory-temp arm, ahead of the inline rows.
        _ArgRow("record_rvalue_temp_factory", _r_record_rvalue_temp_factory,
                extra=_x_temps_and_frame),
        # A concrete conformer into an `Own[@dynamic P]` method slot
        # (`b.set(Dog(...))`): the make_unique / make_adapter wrap,
        # verdict-keyed via the shared classifier (the
        # `_dyn_own_conformer_arg` row in `_lower_call_arg`).
        _ArgRow("dyn_own_conformer", _r_dyn_own_conformer),
        _ArgRow("tparam_slot_temp", _r_tparam_slot_temp, extra=_x_temps_ok),
        _ArgRow("struct_proto_union", _r_struct_proto_union),
        # A tuple LITERAL at a tuple method slot (`h.set((x, y))` ->
        # `h.set(std::tuple<Box*, Box*>{&(x), &(y)})`). The free-call
        # ladder's row: `_lower_call_arg`'s tuple-literal arm is already
        # shared with the method path, so only this admission was missing;
        # the borrow/value builders own the per-element verdict.
        _ArgRow("tuple_literal", _r_tuple_literal),
        _ArgRow("method_value_union", _r_method_value_union),
        # A member ctor RVALUE into a POINTER-variant method slot
        # (`p.set_pet(Cat("Mittens"))` -> `Cat __tmp_N = Cat("Mittens");
        # p.set_pet(::tpy::Union<Cat*, Dog*>{&__tmp_N});`) -- the
        # free-call ladder's row, lowered through the same
        # `_lower_union_arg_lift`. Temp-hoisting, so the method loop
        # threads its flush for this slot too.
        _ArgRow("union_ctor_temp", _r_union_ctor_temp, extra=_x_temps_ok),
        # ... and the bytes-literal rvalue at a beyond-the-slice union
        # slot (`s.post(url, b"payload")` at `bytes | dict | None`),
        # same hoist+addr render, own slot check.
        _ArgRow("union_bytes_literal_temp", _r_union_bytes_literal_temp,
                extra=_x_temps_ok),
        # A same-union NAME into a POINTER-variant method slot passes
        # bare when the callee's param carries no deep-const verdict
        # (`p.set_pet(new_pet)`); a dcbp slot takes the
        # const-conversion wrap (unionlift.const_wrap -- the
        # method loop threads readonly_target), un-narrowed NAMES only
        # (a narrowed name binds the member, so it is not a same-union
        # source at all and this row is not its route).
        _ArgRow("union_pass_deep_const", _r_union_pass_deep_const,
                face="method.union_pass_arg"),
        _ArgRow("value_union_temp", _r_value_union_temp, extra=_x_temps_ok),
        _ArgRow("value_union_narrowed_pass", _r_value_union_narrowed_pass),
        # A scalar value / `None` into a value-repr Optional[scalar]
        # slot renders bare / `std::nullopt` -- `sock.settimeout(0.5)`.
        _ArgRow("value_opt_scalar_value", _r_value_opt_scalar_value),
        # A str LITERAL into a value-repr Optional[str] slot renders
        # bare the same way (`jar.get("missing", "fallback")`) -- the
        # TypedDict-ctor face's row, shared with the ctor arg loop.
        _ArgRow("str_literal_value_opt", _r_str_literal_value_opt),
        # ... and its bytes twin (`h.store(b"abc")` at `bytes | None`):
        # the owned literal spelling converts into the optional in place,
        # the same row the ctor arg ladder carries.
        _ArgRow("bytes_literal_value_opt", _r_bytes_literal_value_opt,
                face="method.bytes_literal_value_opt"),
        # The temp-bearing container LITERAL face of the pointer-repr
        # Optional slot (`s.get(url, None, {...})` -> the `&(__tmp_N)` typed
        # temp); the `None` / bare-name faces ride `optional_ptr_no_temp`
        # above for a container pointee as for a record one.
        _ArgRow("optional_ptr_container_temp",
                _r_optional_ptr_container_temp, extra=_x_temps_ok),
        # ... and the scalar-pointee sibling of that temp face
        # (`c.set(int32(99))` at a `T | None` slot resolved to
        # `const int32_t*`). The ladder's own `optional_ptr_no_temp` row
        # above is hardcoded temps_ok=False, so the temp-bearing face
        # needs its own flush-gated row, like the container literal's.
        _ArgRow("optional_ptr_scalar_temp", _r_optional_ptr_scalar_temp,
                extra=_x_temps_ok),
        # The full protocol-slot faces (`canvas().draw(square(4))` ->
        # the `Adapter<shape, square> __tmp_N{..}` hoist): the
        # method loop runs the same protocol / dynamic-protocol
        # pre-arms as the free-call loop; `_method_arg` threads the
        # flush and protocol_slots for exactly this slot.
        _ArgRow("protocol_slot", _r_protocol_slot),
        # The M4c wrapper-slot rows, the record-method twins of the
        # free-call ladder's: a same-wrapper NAME binds bare
        # (`h.matches(probe)`); a member-typed NAME / literal /
        # member-ctor rvalue hoists the typed temp (flush-gated).
        _ArgRow("ru_wrapper_name", _r_ru_wrapper_name),
        _ArgRow("ru_wrapper_member_name", _r_ru_wrapper_member_name,
                extra=_x_temps_ok),
        _ArgRow("ru_wrapper_scalar_literal", _r_ru_wrapper_scalar_literal,
                extra=_x_temps_ok),
        _ArgRow("ru_wrapper_member_rvalue", _r_ru_wrapper_member_rvalue,
                extra=_x_temps_ok),
        # A BORROW-returning (`T&`) record call at a plain record ref slot
        # (`loop.sock_recv(self._sock.get(), n)`): the result binds the slot
        # directly, no temp and no copy -- the CALL sibling of the bare-NAME
        # and FIELD rows above, and the same cell the marker family carries.
        _ArgRow("borrow_ret_record_marker", _r_borrow_ret_record_marker),
        # A narrowed wide ptr-opt NAME at its POINTEE slot: the plain
        # ladder's row, temp-free and position-blind.
        _ArgRow("wide_opt_deref_name", _r_wide_opt_deref_name),
    )))


# The marker-call families' shared row listing. Written ONCE, in ladder
# order; the Own-slot cells are named in `_MARKER_OWN_ROWS` and spliced out
# for the family that has no Own slots, which is the whole content of the
# `own_ok and` prefix the ladder re-typed on every Own row. Keeping one
# listing is also the proof that the split changed no row's position: the two
# non-template families are filters of this tuple, not re-typings of it.
_MARKER_ROWS: 'tuple[_ArgRow, ...]' = (
    _ArgRow("shared_pass_through", _r_shared_pass_through),
    # A named function passed as a value (`os.walk(missing,
    # onerror=boom)`): the plain function-ref render, identical
    # in the qualcall arg loop and the free-call loop -- the free-call
    # ladder's slot-blind row.
    _ArgRow("func_ref", _r_func_ref),
    # A lambda at a qualcall Fn slot (`itertools.takewhile(
    # lambda n: n < 3, nums)`): the inline closure render is
    # loop-blind -- the native ladder's row, same shapes.
    _ArgRow("lambda", _r_lambda),
    # A whole value-repr Optional[Callable] name into a matching
    # value-opt slot passes bare (`os.walk(top, onerror=cb)`).
    _ArgRow("value_opt_callable_pass", _r_value_opt_callable_pass),
    # ... and the SCALAR twin (`requests.request(.., timeout, ..)` at
    # `timeout: float | None`): the un-narrowed binding IS the
    # `std::optional<double>` the slot takes, so it passes bare.
    _ArgRow("value_opt_pass_through", _r_value_opt_pass_through),
    # ... and the value-TUPLE twin (`requests.request(.., auth, ..)` at
    # `auth: tuple[str, str] | None`): a value tuple is a value type, so the
    # whole optional binds the by-value slot bare -- the free-call and
    # record-method families' row, rendered by the same position-blind arm.
    _ArgRow("value_opt_tuple_pass", _r_value_opt_tuple_pass),
    _ArgRow("none_unit", _r_none_unit),
    _ArgRow("value_union_temp", _r_value_union_temp, extra=_x_temps_ok),
    _ArgRow("value_union_narrowed_pass", _r_value_union_narrowed_pass),
    _ArgRow("own_record_rvalue", _r_own_record_rvalue),
    # An Own[T]-returning call rvalue at an OPEN `Own[T]` slot reached
    # through a qualified receiver (`self._state._push(self._value.take())`
    # -- the Rc deref): the prvalue binds the `T&&` slot bare, the same
    # render the record-method family carries at the same slot, and the
    # qualcall arg loop hoists no temp around it.
    _ArgRow("own_tparam_call_rvalue", _r_own_tparam_call_rvalue),
    # `Factory.consume(copy(p))` -- the static-method face of the
    # copy-construct rvalue row.
    _ArgRow("copy_own", _r_copy_own),
    # The S1 view->owned convert at an `Own[str]` slot
    # (`Rc.new(inner)` on a `str` param -> `std::string(inner)`), the
    # free ladder's row. AHEAD of the Own cascade below for the same
    # reason it is there: the view->owned convert runs before
    # the move/copy-temp cascade, so a VIEW-form str name must be
    # absorbed here rather than reach `own_lvalue`.
    _ArgRow("str_owned_slot", _r_str_owned_slot),
    _ArgRow("own_move", _r_own_move),
    _ArgRow("own_lvalue", _r_own_lvalue, extra=_x_temps_ok),
    _ArgRow("optional_ptr", _r_optional_ptr),
    # A record name moved into an Optional[Own[T]] slot
    # (`_urlopen(..., conn)` -> bare `std::move(conn)`); the
    # lowering enforces the move verdict.
    _ArgRow("opt_own_record_name", _r_opt_own_record_name),
    # A pointer-repr Optional NAME (may be null) at an `Own[record | None]`
    # slot and at the `Optional[Own[record]]` spelling: the null-safe
    # rebuild moved in at its last use -- the ctor/method/free rows.
    _ArgRow("own_opt_ptr_name_move", _r_own_opt_ptr_name_move),
    _ArgRow("opt_own_ptr_opt_name_move", _r_opt_own_ptr_opt_name_move),
    _ArgRow("readonly_record_ctor", _r_readonly_record_ctor),
    _ArgRow("union_pass_through", _r_union_pass_through),
    _ArgRow("union_member_lift", _r_union_member_lift),
    _ArgRow("union_coerced_literal", _r_union_coerced_literal),
    # A member-typed arg into a VALUE-repr Optional slot
    # (`socket.create_connection(addr, 2.0)` at `float | None`,
    # `io.BytesIO(b"xyz")` at `bytes | None`): there is no
    # value-optional arg arm at all, so the arg falls to the generic tail
    # and the optional's converting ctor absorbs the bare member
    # render -- position-blind, which is what `_lower_call_arg`'s own tail
    # spells. The free-call ladder's row.
    _ArgRow("value_opt_member", _r_value_opt_member),
    # ... and the WHOLE value-opt FIELD read at an exactly-matching slot
    # (`socket.create_connection(addr, self.timeout)` at `float | None`):
    # the optional is a value passed by value, so the bare member read
    # binds it -- the NAME pass-through rows' field twin.
    _ArgRow("value_opt_field_pass", _r_value_opt_field_pass),
    # A bare `None` into a value-repr Optional slot renders
    # `std::nullopt` whatever the inner -- the sema-filled default at
    # a kwargs call site arrives exactly this way (`os.walk(root,
    # followlinks=f)` fills `onerror=None` at the
    # `Optional[Callable]` slot). The ctor/record-method ladders' row.
    _ArgRow("none_value_opt", _r_none_value_opt),
    # A list/dict LITERAL into a recursive-union wrapper slot
    # (`json.dumps([1, 2, 3])`): the union-arg value branch
    # hoists `JsonValue __tmp_N = <literal>;` -- flush-gated like
    # the value-union temp row.
    _ArgRow("ru_container_literal", _r_ru_container_literal,
            extra=_x_temps_ok),
    # A wrapper-union NAME at a same-wrapper slot (`json.dumps(v)`
    # on `v: JsonValue`): the binding is already the wrapper struct
    # -- passes bare like a same-union name.
    _ArgRow("ru_wrapper_name", _r_ru_wrapper_name),
    # ... and its FIELD twin (`pkg_v.kind(h.value)`): the member
    # read binds the borrow slot bare, temp-free.
    _ArgRow("ru_wrapper_field", _r_ru_wrapper_field),
    _ArgRow("own_union_ctor", _r_own_union_ctor),
    _ArgRow("dyn_own_coro_factory", _r_dyn_own_coro_factory),
    _ArgRow("dyn_own_handle", _r_dyn_own_handle),
    # An Own[P]-returning call rvalue at the Own[@dynamic P] slot
    # (`create_task(factory(7))` through a std::function binding):
    # the unique_ptr result forwards bare -- the free ladder's row.
    _ArgRow("dyn_own_forward_call", _r_dyn_own_forward_call),
    # A container LITERAL into a matching builtin-container slot: a
    # qualified module function (os.path.commonprefix([...])) renders
    # the spelled container inline, like the stub-method arg loop -- no
    # ref-param temp hoist (a FREE call would hoist, but the qualcall
    # arg loop emits it in place).
    # ... under the method family's guard (const slot, and a callee that does
    # not lend the argument back), for the same reason: only the QUALIFIED
    # kind threads a resolved overload, so a @native / @cpp_template marker
    # (whose fi carries no per-parameter facts) keeps the inline render its
    # hand-written C++ expects.
    _ArgRow("container_literal_method", _r_container_literal_method,
            extra=_x_inline_container_literal),
    # ... and the hoisting sibling, the method family's cell.
    _ArgRow("container_literal", _r_container_literal, extra=_x_temps_ok),
    # A container FIELD read binding a plain container ref slot
    # (`heapq.heappush(self.heap, ...)` -> bare `this->heap`; the
    # Own[T] item slot arrives SUBSTITUTED from sema, so the ctor
    # rvalue beside it rides the existing own.record_rvalue row).
    _ArgRow("container_field_pass", _r_container_field_pass),
    # ... and its RECORD twin (`log_dispatch(mod._logger, ...)`):
    # the member read binds the `T&` slot bare -- the same cell the
    # record-method family carries.
    _ArgRow("record_field_marker", _r_record_field_marker),
    # ... and its ELEMENT twin (`Bag.peek(things[0])`): the checked
    # element lvalue binds the record ref slot inline.
    _ArgRow("record_elem_subscript", _r_record_elem_subscript),
    # ... and its VALUE-TUPLE twin (`self._sock.connect(self._addr)`):
    # borrow and storage forms coincide, so the member read binds the
    # `const std::tuple<..>&` slot bare.
    _ArgRow("value_tuple_field_pass", _r_value_tuple_field_pass),
    # A borrow-returning record call at the same slot
    # (`log_dispatch(svc.get_logger(), ...)`): the `T&` result
    # binds the `T&` slot in place.
    _ArgRow("borrow_ret_record_marker", _r_borrow_ret_record_marker),
    # A tuple LITERAL at a pointer-repr tuple slot: the borrow
    # builder renders it (the plain/method loops' existing row).
    _ArgRow("btuple_literal_marker", _r_btuple_literal_marker),
    _ArgRow("same_tparam_name", _r_same_tparam_name,
            face="arg.same_tparam_name"),
    _ArgRow("own_container_literal", _r_own_container_literal),
    _ArgRow("own_container_comp", _r_own_container_comp),
    # An F1-record call rvalue into a plain record slot
    # (`os.path.samestat(s, os.stat(d))` -- the nested marker call
    # renders bare in place, the qualcall twin of the native row).
    _ArgRow("native_record_call", _r_native_record_call),
    # A protocol slot's temp-free NAME / flushable temp faces
    # (`math.dist([0.0, 0.0], [3.0, 4.0])` -- the structural
    # rvalue's `auto __tmp_N =` hoist), the plain-loop row.
    _ArgRow("protocol_slot", _r_protocol_slot),
    # A narrowed wide ptr-opt NAME at its POINTEE slot: the plain
    # ladder's row, temp-free and position-blind.
    _ArgRow("wide_opt_deref_name", _r_wide_opt_deref_name),
    # A comprehension at a container slot, BEHIND the inline-render
    # `container_literal_method` cell above so the shapes that family
    # already decides keep their cell: the free-call ladder's row, which
    # hoists the slot-typed ArgTemp and is flush-gated for it. The
    # container LITERAL beside it needs no cell here -- the inline-render
    # sibling above already decides it.
    _ArgRow("container_comp", _r_container_comp, extra=_x_temps_ok),
)

# The cells the `native` marker family does NOT carry: its arg loop sets
# `inline_template`, which skips the Own copy temp, so an Own slot has no
# render there. Exactly the rows the ladder prefixed with
# `own_ok and`.
_MARKER_OWN_ROWS = frozenset({
    "own_record_rvalue", "own_tparam_call_rvalue", "copy_own",
    "str_owned_slot", "own_move", "own_lvalue", "own_union_ctor",
    "dyn_own_coro_factory", "dyn_own_handle", "dyn_own_forward_call",
    "own_container_literal", "own_container_comp",
    "own_opt_ptr_name_move", "opt_own_ptr_opt_name_move",
})

assert _MARKER_OWN_ROWS <= {r.row for r in _MARKER_ROWS}


def _marker_rows(own_slots: bool) -> 'tuple[_ArgRow, ...]':
    """`_MARKER_ROWS`, minus the Own cells for a family without Own slots.
    A filter, so every surviving row keeps its position -- the face census
    is order-sensitive and the two families must interleave exactly as the
    one ladder did."""
    if own_slots:
        return _MARKER_ROWS
    return tuple(r for r in _MARKER_ROWS if r.row not in _MARKER_OWN_ROWS)


_MARKER_TEMPLATE_ARG_SINK = register_sink(_ArgSink(
    family="marker_template",
    # Same slot-shape drilldown the native free-call family spells: a
    # @cpp_template callee expands over the SAME builtins arg loop.
    note=lambda req: _native_arg_reject(req.a, req.ptype, req.analyzer),
    rows=(
        _ArgRow("shared_pass_through", _r_shared_pass_through),
    )))

_MARKER_NATIVE_ARG_SINK = register_sink(_ArgSink(
    family="marker_native",
    note=lambda req: _qualcall_arg_reject(req.a, req.ptype, req.analyzer),
    rows=_marker_rows(own_slots=False),
    ))

_MARKER_QUALIFIED_ARG_SINK = register_sink(_ArgSink(
    family="marker_qualified",
    note=lambda req: _qualcall_arg_reject(req.a, req.ptype, req.analyzer),
    rows=_marker_rows(own_slots=True),
    ))


_PLAIN_ARG_SINK = register_sink(_ArgSink(
    family="plain",
    # The reject drilldown `probe_corpus.py` histograms the plain free-call
    # mass on -- one bucket per slot type family.
    note=lambda req: ("call.arg_shape."
                      + _type_family_tag(req.ptype, req.analyzer)),
    rows=(
        # The only family that reads `self_capturable`: a lambda capturing
        # `self` copies the enclosing body's receiver handle, which only the
        # call site knows the body has.
        _ArgRow("lambda", _r_lambda),
        _ArgRow("func_ref", _r_func_ref),
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        # The FIELD twin of the callable-value name pass: a Callable
        # field read binds a callable slot bare (`apply(handler.cb, 10)`
        # -> `apply(handler.cb, 10)` -- the std::function member converts
        # implicitly, no temp).
        _ArgRow("callable_field", _r_callable_field,
                face="call.callable_field_arg"),
        _ArgRow("callable_object", _r_callable_object),
        _ArgRow("shared_pass_through", _r_shared_pass_through),
        # The FIELD twin of the bare-container NAME pass that
        # `shared_pass_through` carries: the member read binds the same
        # container ref slot by reference, so the render is the bare
        # `this->waiters`.
        _ArgRow("container_field_pass", _r_container_field_pass),
        # ... and its MODULE-VARIABLE twin (`take(sys.argv)`): the
        # registered `(*slot)` read binds the same ref slot bare.
        _ArgRow("container_module_var", _r_container_module_var),
        _ArgRow("value_union_temp", _r_value_union_temp, extra=_x_temps_ok),
        _ArgRow("value_union_narrowed_pass", _r_value_union_narrowed_pass),
        _ArgRow("record_rvalue_temp", _r_record_rvalue_temp,
                extra=_x_temps_ok),
        # The S1/S6 view->owned convert rows, free-call twins of the
        # method ladder's: a VIEW-form str/bytes source at an `Own[str]`
        # / `Own[bytes]` slot materializes the inline owned copy
        # (`take(std::string(x))` / `take_bytes(::tpy::Bytes(y))`)
        # -- the view->owned convert, which runs BEFORE the
        # move/copy-temp cascade, so these rows sit above it too.
        _ArgRow("str_owned_slot", _r_str_owned_slot),
        _ArgRow("bytes_owned_slot", _r_bytes_owned_slot),
        _ArgRow("own_move", _r_own_move),
        _ArgRow("own_coerce_cast", _r_own_coerce_cast),
        _ArgRow("own_lvalue", _r_own_lvalue, extra=_x_temps_ok),
        _ArgRow("container_literal", _r_container_literal,
                extra=_x_temps_ok),
        _ArgRow("ref_param_dictset_literal", _r_ref_param_dictset_literal,
                extra=_x_temps_ok),
        # A container literal at an `Own[container]` FREE-call slot
        # renders inline spelled (`consume_dict(::tpy::ordered_map<..>
        # ({{..}}));` -- the prvalue moves in; the method ladder's row).
        _ArgRow("own_container_literal", _r_own_container_literal),
        _ArgRow("own_container_comp", _r_own_container_comp),
        _ArgRow("covariant_temp", _r_covariant_temp, extra=_x_temps_ok),
        _ArgRow("optional_ptr", _r_optional_ptr),
        # A record name moved into an Optional[Own[T]] slot (bare
        # `std::move(name)`); the lowering enforces the move verdict.
        _ArgRow("opt_own_record_name", _r_opt_own_record_name),
        _ArgRow("readonly_record_ctor", _r_readonly_record_ctor),
        _ArgRow("union_pass_through", _r_union_pass_through),
        _ArgRow("required_protocol_union", _r_required_protocol_union),
        _ArgRow("union_member_lift", _r_union_member_lift),
        _ArgRow("union_coerced_literal", _r_union_coerced_literal),
        # M4c wrapper-slot rows (the free-call twins of the qualcall
        # ladder's): a same-wrapper NAME passes bare; a member-typed
        # NAME hoists the typed temp (flush-gated).
        _ArgRow("ru_wrapper_name", _r_ru_wrapper_name),
        _ArgRow("ru_wrapper_borrow_call", _r_ru_wrapper_borrow_call),
        # ... and the VALUE-returning call sibling (`leaf_count(build())`
        # with `-> Own[Expr]`): already_union -> default bare render.
        _ArgRow("ru_wrapper_value_call", _r_ru_wrapper_value_call),
        _ArgRow("ru_wrapper_field", _r_ru_wrapper_field),
        _ArgRow("ru_wrapper_member_name", _r_ru_wrapper_member_name,
                extra=_x_temps_ok),
        # The literal sibling of the row above: `show(42)` hoists
        # `Value __tmp_N = 42;` through the same create_typed branch.
        _ArgRow("ru_wrapper_scalar_literal", _r_ru_wrapper_scalar_literal,
                extra=_x_temps_ok),
        # ... and the member-CTOR-rvalue sibling: `eval_expr(Lit(42))`
        # hoists `Expr __tmp_N = Lit(...);` the same way.
        _ArgRow("ru_wrapper_member_rvalue", _r_ru_wrapper_member_rvalue,
                extra=_x_temps_ok),
        # ... and the Own[genrec]-returning CALL sibling:
        # `leaf_count(make_leaf())` hoists `Tree<T> __tmp_N = ...;`.
        _ArgRow("ru_wrapper_own_call", _r_ru_wrapper_own_call,
                extra=_x_temps_ok),
        # ... and the container-LITERAL sibling (`eval_expr([1,
        # "two", [3]])` hoists `Expr __tmp_N = <literal>;`) -- the
        # marker ladder's row, same flush gating (the predicate peels
        # the literal->wrapper coerce internally).
        _ArgRow("ru_container_literal", _r_ru_container_literal,
                extra=_x_temps_ok),
        _ArgRow("own_union_ctor", _r_own_union_ctor),
        _ArgRow("own_union_call_pass", _r_own_union_call_pass),
        _ArgRow("dyn_own_coro_factory", _r_dyn_own_coro_factory),
        _ArgRow("dyn_own_handle", _r_dyn_own_handle),
        _ArgRow("dyn_own_conformer", _r_dyn_own_conformer),
        _ArgRow("dyn_own_forward_call", _r_dyn_own_forward_call),
        # A narrowed wide ptr-opt NAME at its POINTEE slot: sema's
        # narrowing is the proof of non-null (an un-narrowed source is a
        # sema error here), and the deref renders in place, so the cell
        # owes no flush and is shared by every family.
        _ArgRow("wide_opt_deref_name", _r_wide_opt_deref_name),
        # `None` at a plain unit slot (`takes_none(None)` on
        # `x: None`): the bare `std::monostate{}` render -- the free
        # -call twin of the method/generic ladders' row.
        _ArgRow("none_unit", _r_none_unit),
        _ArgRow("none_value_opt", _r_none_value_opt),
        _ArgRow("value_opt_pass_through", _r_value_opt_pass_through),
        # The Optional view<->str identity coerce over a CALL rvalue
        # (`takes_str_opt(returns_view_opt())`): both sides spell
        # `optional<string_view>`, bare pass-through in exactly the
        # non-Own ARG position (the coercion lambda's identity face).
        _ArgRow("opt_view_identity_coerce", _r_opt_view_identity_coerce,
                face="arg.optview_identity_coerce"),
        # The callable twin of the row above (`takes_opt(cb)` at a
        # `Callable[..] | None` slot) -- bare.
        _ArgRow("value_opt_callable_pass", _r_value_opt_callable_pass),
        # ... and the value-TUPLE twin: a value tuple is a value type,
        # so the whole optional binds the by-value slot bare here too
        # (no target-threaded shim, unlike the VIEW inner).
        _ArgRow("value_opt_tuple_pass", _r_value_opt_tuple_pass),
        _ArgRow("protocol_slot", _r_protocol_slot),
        # A repeat RVALUE at a STRUCTURAL slot (`consume([3] * 4)`):
        # the shared structural-rvalue temp renders it un-spelled
        # (`auto __tmp_N = ({ .. array_from_index .. });`). FREE-call
        # only -- the method loop passes the repeat INLINE, so
        # this cannot live in the position-shared protocol gate. The
        # repeat arm gates the element/result families itself.
        _ArgRow("list_repeat_proto", _r_list_repeat_proto,
                extra=_x_temps_ok, face="argtemp.list_repeat_proto"),
        # A container/Span/record NAME at a NULLABLE static-protocol
        # slot (`count_if_sized(nums)` at `Sized | None`): the
        # address-of lift binds the monomorphized `const T_x*`
        # (`&(nums)` -- the shared 'addr' verdict, rendered by
        # _lower_call_arg's nullable-protocol arm).
        _ArgRow("nullable_proto_addr", _r_nullable_proto_addr,
                face="arg.nullable_proto_addr"),
        # A tuple LITERAL at a tuple param slot: the borrow/value tuple
        # builders own the per-element admission (a bad element shape
        # raises inside lowering and falls the body back whole) -- the
        # gate checks only the slot/arity pairing.
        _ArgRow("tuple_literal", _r_tuple_literal),
        # ... and at a value-repr `Optional[value tuple]` slot
        # (`send(.., ("user", "pw"))`): the spelled brace-init converts
        # into the optional in place, the ctor ladder's row.
        _ArgRow("tuple_literal_value_opt", _r_tuple_literal_value_opt,
                face="arg.tuple_literal_value_opt"),
        _ArgRow("own_tuple_storage_elem", _r_own_tuple_storage_elem),
        _ArgRow("wrapper_ref_tuple_elem", _r_wrapper_ref_tuple_elem),
        _ArgRow("record_borrow_call", _r_record_borrow_call),
        _ArgRow("own_optional_record_rvalue",
                _r_own_optional_record_rvalue),
        # ... and its `Optional[Own[record]]` BY-VALUE sibling
        # (`unwrap_record(Payload(..))` -- the bare prvalue through the
        # implicit optional conversion).
        _ArgRow("opt_own_record_rvalue", _r_opt_own_record_rvalue,
                face="arg.opt_own_record_rvalue"),
        # A str LITERAL at a value-repr `Optional[str]` / `Optional[
        # String]` slot (`unwrap_string("world")`): the bare literal
        # through the implicit `const char* -> optional<string>` chain
        # -- the ctor/method ladders' row plus its String flavor
        # (scoped HERE, not in the shared predicate, so the ctor MIL
        # rows keep their current shape).
        _ArgRow("str_literal_value_opt_coerced",
                _r_str_literal_value_opt_coerced),
        _ArgRow("opt_string_literal", _r_opt_string_literal,
                face="arg.opt_string_literal"),
        # ... and its NAME / same-Optional-call siblings at the
        # `Own[record | None]` slot (`take(a)` -> `std::move(a)`,
        # `take(make(13))` -> bare prvalue).
        _ArgRow("own_opt_slot", _r_own_opt_slot),
        # The pointer-repr Optional NAME (un-narrowed, may be null) at the
        # same `Own[record | None]` slot: rebuilt null-safely and moved at
        # its last use -- the constructor sink's row, one render for every
        # loop.
        _ArgRow("own_opt_ptr_name_move", _r_own_opt_ptr_name_move),
        # ... and the `Optional[Own[record]]` by-value spelling's sibling.
        _ArgRow("opt_own_ptr_opt_name_move", _r_opt_own_ptr_opt_name_move),
        # An Array-returning call rvalue at a matching Array slot binds
        # the std::array prvalue inline (a VALUE container).
        _ArgRow("value_array_call", _r_value_array_call),
        _ArgRow("recursive_union_borrow_call",
                _r_recursive_union_borrow_call),
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        _ArgRow("container_comp", _r_container_comp, extra=_x_temps_ok),
        _ArgRow("borrow_tuple_field", _r_borrow_tuple_field),
        _ArgRow("borrow_tuple_subscript", _r_borrow_tuple_subscript),
        _ArgRow("record_field_ref", _r_record_field_ref),
        # The deref auto-coercion name: inline Ptr deref_check, or the
        # slot-typed wrapper-`__deref__()` copy temp (flush-gated).
        _ArgRow("deref_coerce", _r_deref_coerce),
        _ArgRow("readonly_container_rvalue", _r_readonly_container_rvalue),
    )))


_GENERIC_PLAIN_ARG_SINK = register_sink(_ArgSink(
    family="generic_plain",
    note="call.generic_arg_shape",
    pre=_pre_generic_slot_family,
    # Every cell decides against the SUBSTITUTED slot, which is what the
    # family hands `arg_ok` -- so a row shared with `plain` is the identical
    # predicate over a concrete slot, not a generic-aware variant of it. The
    # order is NOT plain's: this ladder opens with `shared_pass_through`
    # where plain opens with the callable family, and it carries neither
    # plain's callable-FIELD cell nor its temp-hoisting rows for value
    # unions / record rvalues / str-and-bytes-owned slots. Transcribed as
    # found; the absences are holes to fill after the fold, not here.
    rows=(
        # The two cells the ladder spelled as fall-through blocks between
        # the prologue's branches and its tail -- admit-only, so they are
        # rows, and they keep their position ahead of the tail.
        _ArgRow("generic_btuple_name", _r_generic_btuple_name,
                face="call.generic_btuple_name"),
        # A tuple LITERAL at an Own[T]-resolved pointer-repr tuple slot
        # (`poll_ready((xs, int32(2)))` at Own[tuple[list, int32]]): the
        # CONSUMING storage lift (`tuple_to_storage_move` over the borrow
        # build) -- `_lower_call_arg`'s own_btuple_literal row; per-element
        # ownership stays enforced by the builder's ladder.
        _ArgRow("generic_own_btuple_literal", _r_generic_own_btuple_literal),
        _ArgRow("shared_pass_through", _r_shared_pass_through),
        # The callable family into an `Fn[...]` / `Callable` slot
        # (`reduce(add, xs, 0)`, `apply(f1, 5)`, a lambda literal): each
        # renders itself, independent of the slot, so the plain free-call
        # rows carry over to the substituted slot unchanged.
        _ArgRow("lambda", _r_lambda),
        _ArgRow("func_ref", _r_func_ref),
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        _ArgRow("callable_object", _r_callable_object),
        _ArgRow("own_move", _r_own_move),
        # A pointer-repr Optional slot in the substituted param list
        # (`is_def_gen(t.o)` at `U | None` resolved `Pod | None`): the
        # same faces the concrete free-call ladder admits -- the
        # `optional_to_ptr` field lift, the `&(name)` address-of, the
        # `nullptr` None. `_lower_call_arg`'s face rows are slot-keyed
        # and generic-blind, so they render the substituted slot the
        # same way.
        _ArgRow("optional_ptr", _r_optional_ptr),
        # A CONTAINER conformer NAME into an `Own[protocol]` slot
        # (`indexed(nums)` at `Own[Iterable[T]]`): the monomorphized
        # `T_items&&` param consumes the container at the
        # last-use move (`indexed<int32_t>(std::move(nums))`);
        # `_lower_call_arg`'s arm enforces the move verdict.
        _ArgRow("own_proto_container_slot", _r_own_proto_container_slot),
        # The Own-slot copy+move row (`auto __tmp_N = v;` +
        # `std::move(__tmp_N)`, or the temp-free last-use move):
        # `_lower_call_arg` owns both renders against the substituted
        # slot, exactly like the concrete free-call path.
        _ArgRow("own_lvalue", _r_own_lvalue, extra=_x_temps_ok),
        # A record NAME moved into a substituted `Own[T] | None` slot
        # (`take_optional[Box](b, 99)` -> `take_optional<Box>(
        # std::move(b), 99)`): the concrete ladders' row against the
        # resolved slot; the lowering arm enforces the move verdict.
        _ArgRow("opt_own_record_name", _r_opt_own_record_name),
        # The `Own[@dynamic P]` erasure rows on a SUBSTITUTED slot
        # (`task_from_coro(yield_once())` at `Own[Cancellable[T]]`):
        # the same make_adapter / make_unique wraps the concrete
        # free-call gate admits -- the lowering arms are slot-keyed
        # and generic-blind.
        _ArgRow("dyn_own_coro_factory", _r_dyn_own_coro_factory),
        _ArgRow("dyn_own_conformer", _r_dyn_own_conformer),
        # ... and the FORWARD verdict on a substituted slot
        # (`create_task(cb(..))` where `cb`'s declared return IS the erased
        # `Own[Cancellable[T]]`): the result is already unique_ptr<P>-shaped,
        # so it renders bare -- no adapter, no make_unique, nothing for the
        # substitution to change. A CONFORMER source is a different verdict
        # and keeps riding `dyn_own_conformer`'s adapter wrap.
        _ArgRow("dyn_own_forward_call", _r_dyn_own_forward_call),
        # `None` into a substituted unit slot (`poll_ready[None](None)`
        # at `Own[T]` resolved `Own[None]`): the bare `std::monostate{}`
        # render, position-independent.
        _ArgRow("none_unit", _r_none_unit),
        # `None` into a substituted value-repr Optional slot
        # (`take_optional[Box](None, 77)` at `Own[T] | None` ->
        # `std::nullopt`): the concrete ladders' row.
        _ArgRow("none_value_opt", _r_none_value_opt),
        # A tuple literal into a substituted `Own[value-tuple]` slot
        # (`heappush(pq, (3, "third"))`): the spelled value render the
        # tuple-literal lowering arm gives the Own-unwrapped slot.
        _ArgRow("tuple_literal", _r_tuple_literal),
        # A str literal into a substituted `Own[str]` slot
        # (`heappush(words, "cherry")`): binds the owned param bare --
        # `_str_owned_slot_arg`'s literal face; the other faces
        # (view-form locals, subscripts) stay unwitnessed here, which is
        # why this is its own cell and not plain's `str_owned_slot`.
        _ArgRow("own_str_literal", _r_own_str_literal),
        # An owned-bytes CALL rvalue into a substituted `Own[bytes]` slot
        # (`poll_ready(sock.recv(n))`): a prvalue has nothing to move from
        # and owes no view->owned convert, so the Own cascade emits no temp
        # and the arg binds bare -- the render is slot-keyed and blind to
        # the callee being generic, so the concrete row carries over.
        _ArgRow("bytes_owned_call_rvalue", _r_bytes_owned_call_rvalue),
        # A protocol slot in the substituted param list
        # (`poll_once(f())` -- `Awaitable[T]`): the same pre-arm the
        # concrete free-call loop runs (protocol_slots=True there and
        # in the generic lowering alike).
        _ArgRow("protocol_slot", _r_protocol_slot),
        # The M4c wrapper-slot rows against the SUBSTITUTED slot
        # (`leaf_count(t)` at `Tree[T]` resolved `Tree[int]`): a
        # same-wrapper NAME binds bare; a member-typed NAME / literal /
        # member-ctor rvalue hoists the typed temp -- the same rows the
        # concrete ladders run, slot-keyed through `_ru_wrapper_arg_slot`
        # (the `_wrapper_union_like` accessor).
        _ArgRow("ru_wrapper_name", _r_ru_wrapper_name),
        _ArgRow("ru_wrapper_borrow_call", _r_ru_wrapper_borrow_call),
        _ArgRow("ru_wrapper_field", _r_ru_wrapper_field),
        _ArgRow("ru_wrapper_member_name", _r_ru_wrapper_member_name,
                extra=_x_temps_ok),
        _ArgRow("ru_wrapper_scalar_literal", _r_ru_wrapper_scalar_literal,
                extra=_x_temps_ok),
        _ArgRow("ru_wrapper_member_rvalue", _r_ru_wrapper_member_rvalue,
                extra=_x_temps_ok),
        # A list literal into a SUBSTITUTED container slot
        # (`doubled([1, 2, 3])` at `list[T]` -> `std::vector<int32_t>
        # __tmp_1 = {1, 2, 3};`): the generic callee's `std::vector<T>&`
        # param is the same non-const ref the concrete free-call row
        # hoists a named temp for, so it takes that row's admission --
        # over the PEELED literal, which is why it is not plain's
        # `container_literal` cell.
        _ArgRow("generic_list_literal", _r_generic_list_literal,
                extra=_x_temps_ok),
        # A list literal into a substituted `Own[list[T]]` slot
        # (`make_bag([10, 20, 30])` ->
        # `make_bag<int32_t>({10, 20, 30})`): the owning by-value
        # param takes the prvalue brace INLINE -- no ref-slot temp.
        _ArgRow("generic_own_list_literal", _r_generic_own_list_literal,
                face="call.generic_own_list_literal"),
        # An owning CALL whose result IS the substituted `Own[tuple]` slot
        # (`poll_ready(sock._accept_nonblocking())` at `Own[T]` resolved
        # `Own[tuple[Own[socket], tuple[str, int32]]]`): the prvalue binds
        # the by-value slot with no lift, so the render is blind to the
        # callee being generic -- the container family's cell, verbatim.
        _ArgRow("own_tuple_call_rvalue", _r_own_tuple_call_rvalue),
        # A narrowed wide ptr-opt NAME at its POINTEE slot: the plain
        # ladder's row, decided against the substituted slot like every
        # other cell here.
        _ArgRow("wide_opt_deref_name", _r_wide_opt_deref_name),
        # The free-call ladder's FIELD / ELEMENT / COMPREHENSION rows, which
        # this family was transcribed without. Each decides against the
        # SUBSTITUTED slot, like every cell above, and each renders through
        # the same slot-keyed lowering arm the concrete free call uses -- so
        # the substitution has nothing to change. They sit at the END so
        # every shape a cell above already decides keeps its cell;
        # `str_owned_slot` therefore sits BEHIND the Own cascade here rather
        # than ahead of it as at the concrete ladder, and only picks up the
        # view-form sources that cascade refuses.
        # NOT plain's `container_literal`: at a substituted slot the literal
        # renders INLINE, and an inline prvalue cannot bind the mutated
        # `std::vector<T>&` / `ordered_map<..>&` parameter -- which is why
        # `generic_list_literal` above exists as its own hoisting cell. The
        # dict/set literals have no such cell, so they keep rejecting.
        _ArgRow("container_field_pass", _r_container_field_pass),
        _ArgRow("container_module_var", _r_container_module_var),
        _ArgRow("record_field_ref", _r_record_field_ref),
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        _ArgRow("container_comp", _r_container_comp, extra=_x_temps_ok),
        _ArgRow("record_rvalue_temp", _r_record_rvalue_temp,
                extra=_x_temps_ok),
        _ArgRow("str_owned_slot", _r_str_owned_slot),
        _ArgRow("bytes_owned_slot", _r_bytes_owned_slot),
    )))


def _protocol_method_arg_ok(a: TpyExpr, ptype: 'TpyType | None',
                            locals_: dict[str, TpyType], analyzer, *,
                            param_names: 'set[str] | frozenset[str]',
                            narrowed: 'set[str] | frozenset[str]',
                            movable_locals: 'set[str] | frozenset[str]' = frozenset(),
                            func_name: 'str | None' = None) -> bool:
    """The bare-protocol / Own[@dynamic P] receiver family's arg rows.
    Rows: `_PROTOCOL_ARG_SINK`."""
    return arg_ok(_PROTOCOL_ARG_SINK, a, ptype, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed, temps_ok=False,
                  movable_locals=movable_locals, func_name=func_name)


def method_literal_mangled_cpp(e: TpyMethodCall, analyzer) -> 'str | None':
    """The method-call member resolution's literal-mangled arm -- an
    overloaded method plus a Literal param on the resolved fi
    (`r.get("age")` -> `r.get__lit_age("age")`). Returns the mangled member
    spelling, or None when the plain resolution applies. The @native rename
    outranks it, so a renamed fi returns None and
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


def _enum_receiver(recv: TpyExpr, analyzer) -> 'TpyType | None':
    """The enum a method receiver reads as at this occurrence, or None. An
    enum member is a value with no binding form, so its receiver is keyed on
    the occurrence type (a narrowed `Optional[enum]` name reads the enum)
    and admitted and lowered as any enum argument is."""
    t = analyzer.get_expr_type(recv)
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))) if t is not None else None
    return t if t is not None and is_enum_type(t) else None


def _record_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                 analyzer, *, use: _ExprUse,
                                 result_use: _ExprResultUse,
                                 stmt_position: bool,
                                 allow_whole_optional: bool = False,
                                 error_return_raw: bool = False,
                                 narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """A plain user-record method call `recv.method(args)` -- the
    user-record method arm reduced to its pass-through subset. The
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
    MRO (an inherited method emits identically for the admitted arg shapes).
    Multi-overload sets are rejected wholesale: @auto_readonly
    clones, property pairs, and literal-specialized overloads all land there,
    and the temp decisions come from `overloads[0]` while
    rendering against the RESOLVED overload -- a pairing the slice does not
    reproduce. This also keeps the str-literal pin unreachable (the pin
    fires only at overload_count > 1). A native_name RENAME on an actually-native
    record IS admitted (the file-handle `fh.write` shape -- member = native_name,
    a plain member call); a native_function (free-function form) / cpp_template
    each takes a different method-call arm and stays rejected.

    Args: the free-call pass-through set minus bytes (a bytes-view result /
    arg form is not threaded through the method node) -- eligible scalars
    into NON-Own scalar slots (see `_plain_scalar_slot`), bare float
    literals, str-slice values, char values, container names, F1-record
    names (`a.combine(b)`), record-ctor rvalues into CONST same-record
    slots (`a.combine(A(9))`, see `_method_ctor_rvalue_arg`), and the
    VALUE-union rows -- same-union names / coerced literals bare
    (`_method_value_union_arg`) and member-valued scalars through the
    `__tmp_N` variant temp at a flushable position (the free-call arg-temp
    row; value variants are const-blind, so the inherited-method first-pass
    loop, which omits `is_readonly_target`, renders identically). The
    mutated-ref-param rvalue shape (`a.absorb(A(4))`) is the miscompile
    tracked in BUGS.md and rejects.

    Result: an eligible scalar / char / str-slice value, or void (None) in
    statement position, mirroring free-call lowering's value-position set.
    The RETURN half is SHARED with the builtin-stub families -- the chain
    below opens on `_stub_method_ret_ok`, so a value-result row added for
    the stub receivers reaches a user record's methods too. The RECEIVER and
    OVERLOAD halves are deliberately not shared: they are what this gate is
    for, and routing a user record through the stub loop instead rejects
    loudly rather than mis-rendering (measured -- a scalar-arg method emits
    byte-identically, a RECORD-argument one rejects at `method.arg_shape`,
    because the stub arg sink carries no record pass-through row)."""
    # Every result gate is a projection of the sink spec, derived here and
    # not at the call site: the caller holds the spec, so a gate it spells
    # is a derivation this gate cannot check. The record row is the one
    # verdict that is not a single projection, hence its length.
    record_ret_ok = (
        result_use in (_ExprResultUse.BORROW_BIND, _ExprResultUse.RECEIVER)
        # A with-manager rvalue (`with m.lock() as g:`): the guard record
        # lands in the owned `__ctx_N` slot -- the storage-sink twin of the
        # owned-record decl.
        or use.admits(SinkForm.CTX_MANAGER)
        # An owned-record RVALUE at a storage sink (`a.get().next =
        # b.clone()` -- the Own return lands bare; the position gate pinned
        # the slot).
        or ((result_use is _ExprResultUse.STORAGE
             # ... and the record FIELD-WRITE copy sink, whose copy-assign
             # absorbs the prvalue (`task._waker = handle->make_waker(..);`).
             # A borrow-returning result keeps rejecting on both: a decl
             # binds REF_ALIAS off it.
             or use.admits(SinkForm.RECORD_COPY))
            and is_rvalue_source(analyzer, e))
        # ... and a BORROW-returning CONTAINER result at that same copy sink
        # (`self.mirror = h.peek();`): the field's copy-assign absorbs the
        # `C&`, and sema warns the copy. `_f1_record` is False for a
        # container, so this reaches only the alias-ref-container leg of the
        # ret gate.
        or (use.admits(SinkForm.RECORD_COPY)
            and _alias_ref_container(analyzer.get_expr_type(e))))
    storage_ret_ok = result_use is _ExprResultUse.STORAGE
    coro_factory_ok = use.admits(SinkForm.FRAME_FACTORY)
    suspend_ok = result_use is _ExprResultUse.SUSPEND
    iterable_ret_ok = result_use is _ExprResultUse.ITERABLE
    ptr_opt_passthrough = use.admits(SinkForm.PTR_OPT_PASSTHROUGH)
    owned_tuple_ret_ok = (result_use is _ExprResultUse.STORAGE
                          and use.admits(SinkForm.TUPLE_SOURCE))
    btuple_ret_ok = use.admits(SinkForm.BTUPLE_SLOT)
    union_subject_ret_ok = use.admits(SinkForm.UNION_SUBJECT)
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
    recv_t = _enum_receiver(recv, analyzer) or recv_t
    if (isinstance(recv_t, UnionType) and isinstance(recv, TpyName)
            and recv.name not in narrowed):
        # An ASSIGN-narrowed ptr-variant union NAME receiver (`c: Circle |
        # Rect = Circle(5.0); c.area()`): sema retyped the read to the
        # member, and the render is the inline bare get receiver
        # (`(*std::get<Circle*>(c)).area()`) -- dispatch on the member,
        # the field row's method twin. Isinstance-narrowed names read via
        # their alias and stay on their own arms.
        _anu_occ = analyzer.get_expr_type(recv)
        _anu_b = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_anu_occ)))
                  if _anu_occ is not None else None)
        if (_anu_b is not None
                and any(_anu_b == m for m in recv_t.members
                        if not is_void_like_type(m))
                and record_like(_anu_b, analyzer)):
            recv_t = _anu_b
    # A BUILTIN value-record receiver (`w.wake()` on tpy.coro.Waker): its
    # TPy-defined methods render the same plain member call as an F1
    # record's; the record-info guard keeps TypeDef-only value types (str /
    # bytes / span -- their methods take dispatch arms) out.
    # An enum member's methods are its companion record's; the method-call
    # lowering wraps the member in the companion.
    if not (isinstance(recv_t, NominalType)
            and (_f1_record(recv_t, analyzer)
                 or _builtin_value_record(recv_t, analyzer)
                 or is_enum_type(recv_t))):
        # The drill's "which methods block" discriminant: name the receiver
        # family AND the method, so e.g. str methods rank individually.
        return note_detail(f"method.{_recv_family(recv_t, analyzer)}.{e.method}")
    ri = analyzer.registry.receiver_record(recv_t)
    if ri is None:
        return False
    # A @native record's instance method (the file-handle `fh.write(...)` /
    # `r.read(n)` shape) renders `recv.native_name(args)` -- the member name
    # resolves to `fi.native_name` in the method-call emit, exactly like a
    # plain member call. Only native_name on an actually-native record is
    # admitted; a native_function (free-function form, `::sym(recv, args)`) or
    # cpp_template rejects. The `inline_template=is_native_stub`
    # threaded for these args only affects Own[T] slots (a
    # redundant-copy skip); the admitted arg rows carry none.
    native_method = bool(fi.native_name) and ri.is_native and not fi.native_function
    # A generic method is a plain member call with the method_targs suffix
    # when sema inferred args (`b.transform<::tpy::BigInt>(42)`) and a bare
    # member call when it did not (`c.duplicate()` -- a class-T shadow bind,
    # T fixed by the receiver), exactly the method_targs rule.
    # INT-kind args arrive as plain ints (not TpyTypes) and stay out.
    generic_method_ok = bool(fi.type_params) and (
        not e.inferred_type_args
        or (len(e.inferred_type_args) == len(fi.type_params)
            and all(isinstance(t, TpyType) for t in e.inferred_type_args)))
    # A @classmethod OR plain @staticmethod reached through an INSTANCE
    # renders as an ordinary member call (`p.origin()` / `c.zero()` -- C++
    # evaluates the receiver expression and calls the static member), so
    # both ride this arm despite the parser's `is_staticmethod` bit.
    static_member = fi.is_classmethod or fi.is_staticmethod
    if (fi.cpp_template is not None or fi.native_function
            or (fi.native_name and not native_method)
            or (fi.type_params and not generic_method_ok)
            or not (fi.is_method or static_member)):
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
        # specialization emitted per stub. LITERAL-typed params
        # spell a MANGLED member instead (`r.get__lit_age("age")` -- the
        # record arm's method_literal_mangled_cpp spelling). Template stubs
        # (own type params, or protocol/Fn params synthesizing them) emit
        # through the template-header path -- both reject.
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
    # The value-result CORE is the one the builtin-stub families already
    # share (`_stub_method_ret_ok`): scalar / char / enum / Ptr / str-value /
    # bytes-value / Callable, plus the TypeParamRef result (`self.get() -> T`
    # in a generic body), which emits the same bare `recv.method(args)` --
    # the POSITIONS it can compose into gate their own family checks, so
    # admitting it here only opens the T-operand compares and their
    # siblings. Only the flag-free half is taken from the shared core: the
    # storage and statement-position extras are spelled below because they
    # fire THIS family's witnesses, which a shared predicate cannot.
    if not (_stub_method_ret_ok(
                ret, analyzer, stmt_position=False, storage_ret_ok=False,
                enum_ok=True, ptr_ok=True, callable_ok=True,
                span_storage_ok=False, stmt_storage_ok=False)
            # An `Any`-returning method call (`b.peek()` under a cast
            # wrap): `tpy::Any` is a value type returned by value, landing
            # bare in a value / storage slot -- the free-call twin's
            # `isinstance(record, AnyType)` row.
            or (isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    ret))) if isinstance(ret, TpyType) else None, AnyType)
                and _witness("method.any_ret"))
            # A Span result (`self.__span__()` feeding a SpanIter ctor):
            # a VALUE view, the same bare `recv.method(args)` render --
            # consuming positions gate their own family checks.
            or (_span_slot(ret, analyzer)
                and _witness("method.span_ret"))
            # A VALUE-tuple result (`getsockname() -> tuple[str, int32]`)
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
            or (record_ret_ok and record_like(ret, analyzer))
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
            # (`it: SpanIter[int32] = a.__iter__()` -- the plain spelled
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
            # A PROPERTY getter's ptr-variant union result at a
            # RECEIVER/BORROW_BIND sink (`s = c.shape` -- the getter
            # returns the STORAGE variant by reference): renders bare,
            # consumed by the decl's to_[const_]ptr_variant lift.
            or (record_ret_ok and fi.is_property_getter
                and isinstance(ret, TpyType)
                and _eligible_ptr_union(ret, analyzer) is not None
                and _witness("method.union_property_ret"))
            # ... and its VALUE-variant sibling (`self.tzinfo` at a
            # `std::variant<...>` slot), at ANY result position. A value
            # variant is a plain by-value C++ object, so its render is
            # position-independent -- unlike the pointer-variant flavour
            # above, whose borrow/storage duality is what confines that row
            # to the receiver sink. Value unions ONLY: that split is the
            # fence.
            or (fi.is_property_getter and isinstance(ret, TpyType)
                and _eligible_value_union(ret) is not None
                and _witness("method.value_union_property_ret"))
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
            or (stmt_position and record_like(ret, analyzer)
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
            # The same borrow-returning container read TRANSIENTLY (`if
            # b.items_m():`, `1 in b.items_m()`): the position holds nothing
            # past the full expression, so the `std::vector<T>&` renders
            # BARE -- the free-call row's method twin, on the one verdict
            # the transient sinks carry. An OWN return is a prvalue and is
            # not this row (it keeps the storage sinks' own verdicts).
            or (use.admits(SinkForm.BORROW_RET_PASSTHROUGH)
                and call_returns_cpp_ref(analyzer, fi)
                and _alias_ref_container(ret)
                and _witness("method.borrow_ret_passthrough"))
            # A value-repr Optional return at a WHOLE-optional sink
            # (`a.gettimeout() is None` / `== 0.0` -- the has_value /
            # std::optional mixed-compare renders take the bare call).
            or (allow_whole_optional and _value_opt_ret(ret))
            # A @property getter's PTR-repr Optional result at the same
            # whole-optional sink (`w.node is None`): the getter returns
            # the storage optional by cpp-ref -- the
            # optional_to_ptr lift is skipped exactly on is_property_getter -- so the
            # has_value test reads the bare call. Plain methods keep the
            # borrow-form `T*` + nullptr compare and stay rejected here.
            or (allow_whole_optional and fi.is_property_getter
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
            or (error_return_raw and ret is not None)):
        return note_detail("method.ret_type")
    return True


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

    LOAD-BEARING COINCIDENCE: a movable last-use container renders bare only
    because the consuming `own_iter(std::move(..))`
    rewrite is keyed on a BARE `Iterable[Own[T]]` ptype and misses a
    UnionType. Widening that keying to unions means this row needs the same
    rewrite; a pin covers the current behavior."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if ptype is not None else None)
    if not isinstance(pt, UnionType):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    is_container = _f1_container_ref(at) or is_span(at)
    # A source already typed as the SAME protocol union at the SAME slot
    # (`self.extend(items)` forwarding the ctor's narrowed parameter): the
    # template's concept picks the branch off the caller's concrete type, so
    # there is nothing to convert and the name forwards through whatever the
    # binding renders. Exact identity only -- a DIFFERENT union would have to
    # re-select a branch.
    if not (is_container or record_like(at, analyzer) or at == pt):
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
    union slot -- the union-arg rvalue branch: the ctor hoists a named
    temp and the variant lifts its address (`pv{&__tmp_N}`). A SCALAR
    type-ctor rvalue (`check(int32(1))` on a mixed union) takes the same
    branch -- the render is member-shape-blind
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
        narrowed: 'set[str] | frozenset[str]',
        param_names: 'AbstractSet[str]' = frozenset(),
        frame_capturing: bool = False,
        movable_locals: 'set[str] | frozenset[str]' = frozenset(),
        func_name: 'str | None' = None) -> bool:
    """A user record's method call into the one method-arg sink: the position,
    the resolved overload and the flush slot the signature-reading rows want.
    Prologue + rows: `_METHOD_ARG_SINK`."""
    return arg_ok(_METHOD_ARG_SINK, a, ptype, locals_, analyzer,
                  param_names=param_names, narrowed=narrowed,
                  temps_ok=temps_ok, index=index, overload=overload,
                  frame_capturing=frame_capturing,
                  movable_locals=movable_locals, func_name=func_name)


def _view_method_call_supported(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                               analyzer, *, stmt_position: bool,
                               storage_ret_ok: bool,
                               borrow_ret_ok: bool = False) -> bool:
    """A str/StrView value-view receiver's builtin method call -- the
    builtin-method arm (`native_function or cpp_template`)
    reduced to its pass-through subset. The shared marker /
    receiver-shape / fi-kind / arity rejects already ran in
    method-call lowering; the receiver is a bare str-slice name or str field
    (whichever passed that receiver-shape check).

    The receiver renders bare (a plain str name / field) and the call takes
    one of two spellings: a @cpp_template
    expands `{self}`/`{0}`.. positionally, a @native(function=True) prepends
    the receiver (`::sym(recv, args)`). Lowering reaches the general
    THIRMethodCall arm (receiver + args + cpp_template/native_function_name),
    which spells both -- so admission is a gate
    widening only, no new emit.

    Method fi: @cpp_template (positional-only, no `{cpp}` return substitution)
    or @native(function=True). A `{cpp}` placeholder substitutes the return
    type (`str_family` templates carry none, but reject defensively). Owned-str
    results (`s.upper()`) land bare in the str sinks like a container `pop()`;
    a bytes result (`s.encode()`) rides the view/owned form tag.

    Args: the free-call pass-through set (str-slice / scalar / char / bytes /
    enum / ptr into non-Own slots). A str method's args render bare under
    `_lower_call_arg(method_arg=True)`. Own slots / temp-hoisting arg shapes
    reject (`_str_pass_through_arg` rejects Own).

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
            # A CONTAINER result consumed as the next RECEIVER
            # (`b.recv(n).split(sep)[0]` -- the Own[list[bytes]] rvalue
            # interpolates into the checked dunder): the container
            # family's borrow_ret_ok composition row.
            or (borrow_ret_ok
                and _storage_call_ret(analyzer.get_expr_type(e), analyzer)
                is not None)
            # A structural-protocol result at a STORAGE sink
            # (`char_it = chars.__iter__()`): C++ concepts cannot type a
            # variable, so the decl spells `auto` and the @cpp_template
            # rvalue carries the concrete type -- the protocol family's
            # self_storage_ret row, applied to the view receivers.
            or (storage_ret_ok and is_rvalue_source(analyzer, e)
                and _protocol_auto_slot(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(analyzer.get_expr_type(e)))))
                and _witness("method.view_self_storage_ret"))
            or note_detail("method.view.ret_type"))


def _bytearray_recv(recv_type: 'TpyType | None') -> bool:
    """A bytearray receiver -- the owned mutable twin of the bytes view
    family: every stub method is @native (member renames like `push_back` /
    bare `clear`, or function=True `::tpy::bytearray_*(recv, args)`), all
    rendered by the general THIRMethodCall arm."""
    if recv_type is None:
        return False
    return _bytes_family_ref(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type))))


class _MethodRecvFamily(NamedTuple):
    """One builtin-stub / protocol receiver family's paired method-call gate
    dispatch. The SHAPE gate (the plain-method lowering arm) and the
    ARG gate (`_method_call_arg_ok`) both dispatch through
    `_method_recv_family`, so a family's shape admission and arg admission
    cannot drift apart -- widening or adding a family is one table row. Shape
    fns share the signature (e, fi, locals_, analyzer, *, stmt_position,
    storage_ret_ok, borrow_ret_ok) and arg fns (a, ptype, locals_, analyzer,
    *, param_names, narrowed); a family ignores the knobs it has no rows for. `stub_recv`
    marks the builtin-stub receivers whose args render through the
    builtin-stub arg loop (raw param type threaded into literal renders)."""
    shape_ok: Callable[..., bool]
    arg_ok: Callable[..., bool]
    stub_recv: bool


def _container_method_elem(t: 'TpyType | None', analyzer) -> bool:
    """A container whose element family only the METHOD-CALL arm admits: an
    open `T`, the unit type, a `Callable` value or a non-wrapper union. The
    other element predicates are read-shaped -- they gate subscript / decl /
    for-each consumers too, where these elements would need lifts this slice
    does not build. A method receiver renders bare whatever the element is
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


def _builtin_container_type(t: 'TpyType | None', analyzer) -> bool:
    """A builtin-container METHOD RECEIVER: the native-indexable dispatch
    (list / dict / Array / Span / varargs), the scalar-element set slice, and
    the bytes family's reference-typed member.

    `bytearray` needs its own leg because `_container_elem_family` keys
    membership on the receiver's `__getitem__` resolving to the container
    template, and bytearray's resolves to `::tpy::bytes_getitem` -- the
    resolved-dunder item, not a family difference. The element / key gates
    the other two legs carry stay: dropping them (the pure reference-axis
    membership) admits `set[bytes].discard(b"x")`, whose arg row renders a
    view literal into a `const std::vector<uint8_t>&` slot and fails the C++
    build. Shared with `_inherited_container_base` so the two cannot
    drift."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if isinstance(t, TpyType) else None)
    return bool(_container_elem_family(t, analyzer, lambda _a: True,
                                       span_elem_ok=lambda _a: True)
                or _set_method_recv(t, analyzer)
                or _bytes_family_ref(u))


def _inherited_container_base(recv_type: 'TpyType | None',
                              method: 'str | None',
                              analyzer) -> 'TpyType | None':
    """The builtin-container base a user record INHERITS `method` from, or
    None. The method-call render forks on `record_info.get_method(
    method) is None`: a method the record does not DECLARE falls past the
    user-record arm into the same builtin-stub branches a plain container
    receiver takes (`ml.append(10)` -> `ml->push_back(10)`, the identical
    render a `list` binding gets), while an OVERRIDE takes the user-record
    arm (`o.append(3)`). Keying on that raw fact rather than on the receiver
    type alone is what keeps this row paired with the render it claims.

    `method=None` (the chained-result callers, which ask only "is this a
    container") never matches: without a name there is no declared-method
    fork to key on."""
    if method is None:
        return None
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
         if isinstance(recv_type, TpyType) else None)
    if not (isinstance(t, NominalType) and t.is_user_record):
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.get_method(method) is not None:
        return None
    for p in ri.parents:
        if _builtin_container_type(p, analyzer):
            return p
    return None


def _container_method_recv(recv_type: 'TpyType | None', analyzer,
                           tparam_bounds: 'dict | None',
                           method: 'str | None' = None) -> bool:
    # No face here: a classification predicate runs BEFORE the family's shape
    # gate can still reject, so a witness at this point would read as routed
    # for a body that then rejects. The routing pin carries the claim instead.
    if _inherited_container_base(recv_type, method, analyzer) is not None:
        return True
    if _builtin_container_type(recv_type, analyzer):
        return True
    # A NARROWED pointer-repr `Optional[container]` NAME receiver (`xs.append(
    # x)` under `xs is not None` on `list[str] | None` -> the `T*` binding's
    # `xs->push_back`): the proven pointer dispatches on the payload -- the
    # shared Optional-ptr binding, asked here which method TABLE its pointee
    # dispatches to. Sema forbids the un-narrowed call, so reaching lowering
    # implies the proof.
    opt = _optional_ptr_borrow(recv_type, analyzer) if method is not None else None
    if (opt is not None
            and _builtin_container_type(unwrap_readonly(opt.inner), analyzer)
            and _witness("method.opt_ptr_container_recv")):
        return True
    # An `Own[container]` binding dispatches its methods on the payload
    # (`row.append(x)` on an `Own[list[str]]` loop var -> the same
    # `push_back` a plain list binding gets) -- the receiver peel every
    # other gate already does. The membership core rejects `Own` outright
    # because it also answers ELEMENT-slot questions, where the move-in ABI
    # matters; at a receiver it does not. `method`-gated like the inherited
    # row above: the name-less callers ask "is this a container RESULT",
    # a different question this peel would silently re-answer.
    if method is None:
        return False
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
         if isinstance(recv_type, TpyType) else None)
    return (isinstance(u, OwnType)
            and _builtin_container_type(unwrap_readonly(u.wrapped), analyzer))


def _protocol_method_recv(recv_type: 'TpyType | None', analyzer,
                          tparam_bounds: 'dict | None',
                          method: 'str | None' = None) -> bool:
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
                         tparam_bounds: 'dict | None',
                         method: 'str | None' = None) -> bool:
    """An `Own[@dynamic P]` receiver (`std::unique_ptr<P>`): the member
    access spells `->` (the emit node's `_recv_own_dyn` test); shape and
    args share the protocol family's gates -- the user-record guard
    skips a protocol type, so args take the free-call
    fallback arg renders."""
    if not isinstance(recv_type, TpyType):
        return False
    u = unwrap_send_sync(recv_type)
    return (isinstance(u, OwnType)
            and isinstance(u.wrapped, NominalType)
            and is_dyn_protocol(u.wrapped))


def _scalar_method_recv(recv_type: 'TpyType | None', analyzer,
                        tparam_bounds: 'dict | None',
                        method: 'str | None' = None) -> bool:
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
                      tparam_bounds: 'dict | None',
                      method: 'str | None' = None) -> bool:
    """A str/StrView or bytes/BytesView value receiver: both render args
    through the same builtin-stub loop, so they share its rows. A
    None-NARROWED `Optional[view]` binding joins: its read is the `(*s)`
    deref of the same view value, and calling a view method on an
    un-narrowed Optional is a sema error, so reaching the gate implies
    the proof (the subscript receiver's rule)."""
    if (_resolved_str_value(recv_type, analyzer) is not None
            or _resolved_bytes_value(recv_type, analyzer) is not None):
        return True
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_type)))
         if recv_type is not None else None)
    return (isinstance(u, OptionalType)
            and (_resolved_str_value(u.inner, analyzer) is not None
                 or _resolved_bytes_value(u.inner, analyzer) is not None))


_METHOD_RECV_FAMILY_TABLE: tuple = (
    (_container_method_recv,
     _MethodRecvFamily(shape_ok=_container_method_call_supported,
                       arg_ok=_stub_method_arg_ok, stub_recv=True)),
    (_protocol_method_recv,
     _MethodRecvFamily(shape_ok=_protocol_method_call_supported,
                       arg_ok=_protocol_method_arg_ok, stub_recv=False)),
    # The Own[@dynamic P] handle receiver shares the protocol family's
    # gates; only the `->` access differs (the emit node's own-dyn test).
    (_own_dyn_method_recv,
     _MethodRecvFamily(shape_ok=_protocol_method_call_supported,
                       arg_ok=_protocol_method_arg_ok, stub_recv=False)),
    (_view_method_recv,
     _MethodRecvFamily(shape_ok=_view_method_call_supported,
                       arg_ok=_stub_method_arg_ok, stub_recv=True)),
    (_scalar_method_recv,
     _MethodRecvFamily(shape_ok=_scalar_method_call_supported,
                       arg_ok=_stub_method_arg_ok, stub_recv=True)),
)


def _method_recv_family(recv_type: 'TpyType | None', analyzer,
                        tparam_bounds: 'dict | None',
                        method: 'str | None' = None) -> '_MethodRecvFamily | None':
    """Classify a plain method call's receiver into its stub/protocol family
    -- the ONE family list both method gates consult. None -> the residual
    dispatch (the ptr-template arm at the shape gate, the user-record path at
    the arg gate; a routed ptr-template call admits no args, so the record
    tail never fires for one).

    `method` is the called name, which the container row needs for the
    declared-vs-inherited fork (`_inherited_container_base`); families
    keying on the receiver type alone ignore it. Both gates must pass the
    SAME name, or the pairing the table exists to guarantee breaks."""
    for pred, family in _METHOD_RECV_FAMILY_TABLE:
        if pred(recv_type, analyzer, tparam_bounds, method):
            return family
    return None


def _native_iterator_callee(fi, result: 'TpyType | None') -> bool:
    """A builtin iterator COMBINATOR callee (`zip`/`map`/`filter`/`reversed`/
    `enumerate`/`iter`): @native or @cpp_template, not a generator, and its
    result is the structural `typing.Iterator` protocol over a C++ object
    that has begin()/end()."""
    if fi is None or fi.is_generator:
        return False
    if not (fi.native_name or fi.native_function or fi.cpp_template):
        return False
    if result is None:
        return False
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(result)))
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.qualified_name() == "typing.Iterator")


def _native_iter_combinator(it, analyzer) -> bool:
    """A call to a `_native_iterator_callee`, which is what the comp loop
    calls begin()/end() on unconditionally. The complement of
    `_genfac_like_call`, which excludes exactly these callees so the frame
    rows stay theirs; both verdicts key on the same fi flags so the two arms
    cannot claim one call."""
    if not isinstance(it, (TpyCall, TpyMethodCall)):
        return False
    return _native_iterator_callee(it.resolved_function_info,
                                   analyzer.get_expr_type(it))


def _self_iterator_record(ri, analyzer) -> bool:
    """Whether a compiled record is its own iterator: every `__iter__` it
    declares or inherits returns a REFERENCE to exactly this record. An
    ancestor's type does not count -- `return self` inherited from a base and
    `return self.inner` delegating to a base-typed member have the same
    signature -- and neither does a fresh instance of its own class
    (`-> Own[Cur]`). The runtime asks the same question of the C++ type
    (`is_self_iterator_v`, dunder.hpp)."""
    overloads = analyzer.registry.get_method_overloads_with_parents(
        ri, "__iter__")
    if not overloads:
        return False
    for fi in overloads:
        rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            fi.return_type)))
        if isinstance(rt, OwnType):
            return False
        if analyzer.registry.get_record_for_type(rt) is not ri:
            return False
    return True


def _separate_iterator_record_temp(a: TpyExpr, analyzer) -> 'TpyType | None':
    """A TEMPORARY of a compiled record whose `__iter__` returns a separate
    iterator object: the record type, else None. Its `__iter__` is user code
    that runs at the combinator call, and the iterator may point into the
    record, so a combinator that OWNS one is pinned -- the pair cannot move.
    A self-iterator record, a runtime or @native type and an lvalue are never
    this kind."""
    at = analyzer.get_expr_type(a)
    if at is None:
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    if not isinstance(at, NominalType) or at.is_protocol:
        return None
    ri = analyzer.registry.get_record_for_type(at)
    if ri is None or ri.is_native:
        return None
    if not analyzer.registry.get_method_overloads_with_parents(ri, "__iter__"):
        return None
    if _self_iterator_record(ri, analyzer):
        return None
    if is_lvalue_iterable(a, analyzer.registry.get_record,
                          analyzer.get_expr_type):
        return None
    return at


def _separate_iterator_temp_arg(a: TpyExpr, ptype: 'TpyType | None',
                                callee, analyzer) -> 'TpyType | None':
    """A `_separate_iterator_record_temp` worth binding to a local at a native
    iterator callee's `Iterable[T]` slot (`zip(Noisy(2), xs)`): the record
    type to declare the local as, else None. Bound first, the argument is an
    lvalue, the borrowing flavor is selected and the combinator stays
    movable. A render choice only: the argument is admitted by the same rows
    either way, and where no local can be made it keeps the owned, pinned
    form. Only a constructor or free-function call is a candidate (the
    sources the temp's init lowers), and only a MOVABLE record: a non-movable
    temp cannot bank in a conditional operand and would be built on the arm
    not taken."""
    if callee is None or not _native_iterator_callee(callee,
                                                     callee.return_type):
        return None
    if not isinstance(a, TpyCall):
        return None
    pb = _protocol_binding(ptype)
    if pb is None or pb.name != "Iterable":
        return None
    at = _separate_iterator_record_temp(a, analyzer)
    if at is None or not at.is_movable():
        return None
    return at


def _combinator_pins_source(it, analyzer) -> bool:
    """Whether the combinator call `it`, lowered where no arg temp can be made
    (a genexpr's source), OWNS a `_separate_iterator_record_temp` and is
    therefore pinned: it cannot be moved, and neither can a closure built
    over it."""
    return any(_separate_iterator_record_temp(a, analyzer) is not None
               for a in it.args)


def _user_iterator_iterable(u: 'TpyType | None', analyzer) -> bool:
    """The `__iter__`/`__next__` protocol-loop family: a protocol
    Iterator/Iterable value, or a user record declaring (or inheriting)
    either dunder -- the universal `::tpy::__iter__` default."""
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


def _user_iterable_ctor_arg(a: TpyExpr, declared: dict[str, TpyType],
                            analyzer) -> bool:
    """A user iterable LVALUE -- a name, `self` or a field read off an
    admitted receiver -- at a container constructor's `Iterable[T]` slot
    (`list(bag)`): a record in the `__iter__`/`__next__` family with no
    begin()/end() of its own, which the construct template drains through
    the iterator its `__iter__()` returns. Binds bare, like a protocol param
    name at the same slot."""
    if isinstance(a, TpyName):
        if a.name == "self":
            t = analyzer.get_expr_type(a)
        elif a.name in declared:
            t = declared[a.name]
        else:
            return False
    elif isinstance(a, TpyFieldAccess):
        if not _field_receiver_ok(a, declared, analyzer):
            return False
        t = _field_decl_type(a, declared, analyzer)
    else:
        return False
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    return (isinstance(u, NominalType) and not u.is_protocol
            and not is_native_iterable(u, analyzer.registry)
            and _user_iterator_iterable(u, analyzer))


def _iter_proto_call_ret(it: 'TpyCall | TpyMethodCall', analyzer) -> bool:
    """A non-generator call admitted as the universal-loop iterable: its
    result is the `__iter__`/`__next__` family -- an Iterator/Iterable
    protocol value (`reversed(xs)`, `zip(xs, ys)`) or a concrete
    user-iterator record (`SimpleIter(4)`) -- and NOT NativeIterable (which
    takes the begin/end peephole, the container route)."""
    ret = analyzer.get_expr_type(it)
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if u is None or is_native_iterable(u, analyzer.registry):
        return False
    return _user_iterator_iterable(u, analyzer)


def gen_recv_ctor_temp(obj: TpyExpr, analyzer) -> bool:
    """A generator-method receiver lifted into a named local
    (`for v in Counter(3).each():` -> `Counter __tmp_N = Counter(..);` +
    `__tmp_N.each()`): the resumable frame / peephole captures the receiver
    by reference, so a temporary would dangle (the is_temporary lift).
    Restricted to the witnessed slice: a same-nominal
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
    (`gen_recv_ctor_temp`). A generic method routes when its inferred
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
        if not gen_recv_ctor_temp(e.obj, analyzer):
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
    # return substitution is the generics machinery, not handled here.
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
    union-coerced literal into a value-variant method slot renders bare.
    Restricted to value unions -- their renders are const-blind
    (no pointee const spelling), so the own-record loop (which threads
    `is_readonly_target`) and the inherited-method first-pass loop (which
    omits it) emit identically. Pointer-variant method slots reject with
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

def _open_tparam_param_read(a: TpyExpr, locals_: 'dict[str, TpyType] | None',
                            param_names: 'AbstractSet[str]') -> bool:
    """A bare-`T` value read from a PARAMETER of the enclosing generic body.

    Such a param is spelled `param_val_or_ref_t<T>`, so at an instantiation
    whose parameter form is a distinct view over its storage form the read is
    NOT the storage form a `T` sink spells -- unlike a `T` LOCAL, which is
    declared as the storage form and needs no construction. An `Own[T]` param
    is out too: `own_param_t<T>` already IS the storage form."""
    if not isinstance(a, TpyName) or a.name == "self":
        return False
    if a.name not in param_names:
        return False
    at = (locals_ or {}).get(a.name)
    if not isinstance(at, TpyType):
        return False
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    return isinstance(au, TypeParamRef)


def _open_tparam_storage_slot(slot: 'TpyType | None') -> 'TypeParamRef | None':
    """The bare `T` behind a `T`-STORAGE slot, or None.

    A BARE `T` parameter is deliberately not one: that slot is
    `param_val_or_ref_t<T>`, the instantiation's own parameter form, which the
    caller's read already is -- forwarding a `T` param into another generic's
    `T` slot needs no construction, and constructing there would hand a prvalue
    to a `T&` slot at a reference instantiation. `own_only=False` is for the
    RETURN slot, whose `val_or_ref_t<T>` spelling IS the storage form."""
    if not isinstance(slot, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if not isinstance(u, OwnType):
        return None
    u = unwrap_readonly(u.wrapped)
    return u if isinstance(u, TypeParamRef) else None


def _open_tparam_return_slot(slot: 'TpyType | None') -> 'TypeParamRef | None':
    """The bare `T` behind a `val_or_ref_t<T>` RETURN slot, or None. `Own[T]`
    is excluded by the caller: `own_return_t<T>` is already the storage form."""
    if not isinstance(slot, TpyType):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    return u if isinstance(u, TypeParamRef) else None


def _none_unit_arg(a: TpyExpr, ptype: 'TpyType | None') -> 'NoneType | None':
    """A `None` literal into a unit slot (`Own[None]` / bare `None` -- a
    generic call's substituted T=None param): renders the bare
    `std::monostate{}` value. Returns the slot's NoneType."""
    if not isinstance(_peel_coerce(a), TpyNoneLiteral):
        return None
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, OwnType):
        pt = unwrap_readonly(pt.wrapped)
    return pt if isinstance(pt, NoneType) else None

def _raw_record_fi_for_type(t: 'TpyType | None', method_name: str,
                            analyzer) -> 'object | None':
    """The record's RAW fi for `method_name` (TypeParamRef params intact --
    not the substituted resolved stub), or None for a non-record type.
    `_tparam_slot_temp_arg` keys its temp decision on the RAW param being a
    bare T, exactly like the user-record loop. Resolved through the
    MRO (`get_method_overloads_with_parents`) so an INHERITED generic
    method sees the same fi the shape gate admitted -- an own-methods-only
    lookup would skip the temp arm the gate promised."""
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
          if t is not None else None)
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    if not isinstance(rt, NominalType):
        return None
    ri = analyzer.registry.receiver_record(rt)
    if ri is None:
        return None
    overloads = analyzer.registry.get_method_overloads_with_parents(
        ri, method_name)
    return overloads[0] if overloads else None


def _raw_record_method_fi(e: TpyMethodCall, locals_: dict[str, TpyType],
                          analyzer) -> 'object | None':
    """The RECEIVER record's raw fi for the called method."""
    return _raw_record_fi_for_type(
        _method_receiver_type(e.obj, locals_, analyzer), e.method, analyzer)


def _raw_record_ctor_fi(rtype: 'TpyType | None',
                        analyzer) -> 'object | None':
    """The CONSTRUCTED record's raw `__init__` fi. The ctor arg loop is
    handed the SUBSTITUTED slots, so the genericity of the emitted parameter
    (`explicit Boxed(const T& value)`) is only readable here."""
    return _raw_record_fi_for_type(rtype, "__init__", analyzer)


def _tparam_slot_temp_arg(a: TpyExpr, ptype: 'TpyType | None', idx: int,
                          method_fi, analyzer, *,
                          locals_: 'dict[str, TpyType] | None' = None,
                          param_names: 'AbstractSet[str]' = frozenset()
                          ) -> 'TpyType | None':
    """A temporary arg into a generic-record method's T slot, resolved
    non-value at the call site (`printer.get_str(Point(10, 20))`,
    `box_list.set([4, 5, 6])`): the RAW method param is a bare TypeParamRef
    (`param_val_or_ref_t<T>` -- an lvalue-ref binding), so the call hoists
    the named temp `R __tmp_N = <target-typed init>;` (temps.create over
    the receiver-substituted type). Returns that resolved type, or None.
    `ptype` arrives already substituted (the resolved fi's param); a
    value-type resolution passes bare through the scalar rows instead, and
    a still-open T (a generic body's own T) stays out.

    `_generic_arg_slot` is the verdict, the same one the free-callee seam
    takes."""
    if method_fi is None or idx >= len(method_fi.params):
        return None
    g = _generic_arg_slot(a, method_fi.params[idx].type, ptype, locals_,
                          param_names, analyzer)
    if g is None:
        return None
    if g.slot.is_value_type():
        # A @native / @cpp_template callee spells its own C++ signature by
        # hand (the runtime's lookups take a key BY VIEW), so its bare-T slot
        # is NOT the emitted `param_val_or_ref_t<T>` this leg is about.
        if (method_fi.cpp_template is not None or method_fi.native_function
                or method_fi.native_name):
            return None
        return g.slot if g.needs_temp else None
    # A non-value resolution owes the temp only for the SOURCE shapes this
    # seam renders as a temporary: the arg loop here judges the source, where
    # the free-call prologue asks the shared verdict from inside its
    # per-shape arms and so needs no test of its own.
    if isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                      TpyStrLiteral)):
        return g.slot
    if isinstance(a, (TpyCall, TpyMethodCall)) and is_rvalue_source(
            analyzer, a):
        return g.slot
    return None

def _method_ctor_rvalue_arg(a: TpyExpr, ptype: TpyType | None, idx: int,
                            method_fi, locals_: dict[str, TpyType],
                            analyzer) -> bool:
    """A record RVALUE arg into a CONST same-record method slot
    (`a.combine(A(9))` where `other` is emitted `const A&`): the method-call
    arg loop has no ref-param rvalue-temp arm (unlike the free-fn loop), so
    the source expansion renders inline -- the THIRCtorCall
    bytes for a ctor, the bare call render for a record-returning free /
    method call (`a.add(b.muls(x))`), which the const ref binds for the
    full expression. Admission requires the callee param be signature-const
    (`const_borrow_params`, the materialized `decide_param_const` verdict,
    the same fact `_param_is_const` reads for body locals): the MUTATED-ref
    shape (`a.absorb(A(4))`) inlines the same way but that
    render cannot compile (an rvalue never binds `A&`) -- the miscompile
    tracked in BUGS.md ("method-call record rvalue into a mutated ref param
    never temps"), so it stays unsupported. Same-nominal
    slots only (an rvalue UPCAST is deferred with the other rvalue rows); a
    FREE-fn ctor rvalue hoists a `__tmp_N` even into a const slot (the
    ref-param temp arm) and stays off free-call lowering's arms entirely."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
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
    if method_fi is None:
        # A builtin-stub receiver: no signature-const facts exist, and every
        # bare-T slot the stubs spell is a LOOKUP argument the runtime takes
        # as `const T&` (list.remove/index/count, set.remove/discard, the
        # dict key slot) or BY VALUE (the dict get/pop default), and a ctor
        # prvalue binds either without a copy. Same
        # reasoning as the un-analyzed-callee arm below, and narrowed the
        # same way -- the ctor shape only.
        return isinstance(a, TpyCall) and _ctor_shape_ok(a, analyzer)
    cbp = method_fi.const_borrow_params
    if cbp is None:
        # An UN-ANALYZED callee (a @native record's body-less stub method):
        # no const verdict exists. The ctor expansion inlines regardless
        # (ctor_mutated falls back to empty) and
        # compilability falls to the real C++ signature.
        # Analyzed user methods always carry a materialized cbp
        # (populate_const_borrow_params runs for every body-bearing fi),
        # so this arm cannot smuggle the mutated-ref miscompile shape
        # past the const gate.
        if (method_fi.direct_mutated_params is None
                and method_fi.call_edges is None):
            return isinstance(a, TpyCall) and _ctor_shape_ok(a, analyzer)
        return False
    if idx not in cbp:
        return False
    # The CALL sources of the same rvalue: a record-returning free call and
    # a record-returning method call each render bare, so the const ref
    # binds the returned prvalue exactly as it binds the ctor expansion.
    if isinstance(a, TpyMethodCall):
        return bool(_method_rvalue_record_like(a, analyzer)
                    and _witness("method.record_method_rvalue_arg"))
    return bool(_ctor_shape_ok(a, analyzer)
                or _typed_dict_ctor_call(a, analyzer) is not None
                or (_record_rvalue_call_shape(a, analyzer)
                    and _witness("method.record_call_rvalue_arg")))

def _is_builtin_print(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a call to the builtin `print` (not a user/local shadow): the builtin
    is in `imported_names` and `print` is not redefined as a same-module function /
    record or bound as a local. A shadowed `print` is not this shape and
    rejects rather than being intercepted as the builtin."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "print"):
        return False
    reg = analyzer.registry
    return ("print" in analyzer.imported_names
            and reg.get_function("print") is None
            and reg.get_record("print") is None
            and "print" not in declared)

def _print_arg_form(t: TpyType) -> PrintForm:
    """The `std::cout <<` wrapper for a print arg's resolved type -- the
    per-type print dispatch for the eligible subset. bool is checked before
    the 8-bit-int case (a `bool` has an 8-bit int trait but must format as
    `True`/`False`, not `static_cast<int>`)."""
    if is_bool_type(t):
        return PrintForm.BOOL
    if is_float_type(t):
        # print_float takes double; a float32 arg casts up first
        # (the print emit's is_float32_type arm).
        return PrintForm.FLOAT32 if is_float32_type(t) else PrintForm.FLOAT
    # A bytes-slice value (incl. a still-pending bytes local binding -- the
    # view/owned resolution doesn't change the printer) wraps in BytesPrinter
    # (the print emit's is_any_bytes_type arm; bytearray is gated out of the args).
    if _is_bytes_family(t):
        return PrintForm.BYTES
    if is_enum_type(t):
        # @native enums have no emitted operator<< (it would conflict with a
        # user-provided one) -- the print emit routes them through `::tpy::__repr__`;
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
    or `Optional[str]` -- an UN-narrowed read that the print emit renders via
    `::tpy::print_optional_val(...)` over the whole optional (bare, no deref).
    A NARROWED value-opt SCALAR or STR name takes the SAME whole-optional wrap
    (print position ignores narrowing --
    `print_optional_val(s)`, not `(*s)`, for params and locals alike),
    keyed on the DECLARED binding type when `locals_` is
    threaded. Limited to a
    bare name (param / local) or a plain field read -- the positions that
    render as bare optional storage. A container/tuple inner takes
    an explicit Formatter (a separate face) and is excluded: `_value_opt_scalar`/
    `_value_opt_str` only admit scalar / str inners."""
    if isinstance(a, (TpyCall, TpyMethodCall)):
        # A value-repr Optional[scalar]/[str] CALL result (`print(pick(...))`
        # on a `-> str | None` / `int | None` callee): the rvalue is the whole
        # `std::optional<T>` fed bare to `print_optional_val` -- no narrowing on
        # an rvalue, so the resolved type is authoritative. Optional[bytes] is
        # excluded (BytesPrinter arm), matching the name/field rows below.
        t = analyzer.get_expr_type(a)
        opt = (_value_opt_scalar(t, analyzer) or _value_opt_str(t, analyzer)
               or _value_opt_formatted(t, analyzer))
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
    if opt is None:
        # An `Optional[String]` binding takes the same member-blind
        # print_optional_val wrap over the bare name.
        opt = _value_opt_string_owned(t)
    if opt is None:
        # ... and a FORMATTER inner (bytes / value tuple / Array / Span):
        # the same whole-optional wrap, with the kind-keyed Formatter the
        # emit spells (the inner's C++ type has no plain operator<<).
        opt = _value_opt_formatted(t, analyzer)
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
        # keyed on the DECLARED binding (the print emit ignores narrowing in print
        # position -- `print_optional_val(s)`, not `(*s)`), matching the field
        # arm below. The Optional[String] binding rides the same wrap.
        return (_value_opt_scalar(locals_[a.name], analyzer)
                or _value_opt_str(locals_[a.name], analyzer)
                or _value_opt_string_owned(locals_[a.name])
                or _value_opt_formatted(locals_[a.name], analyzer))
    if isinstance(a, TpyFieldAccess) and isinstance(a.obj, TpyName):
        # A NARROWED Optional FIELD keeps the whole-optional wrap keyed on the
        # DECLARED field type (the print emit swaps in the declared Optional for
        # fields) -- EXCEPT a narrowed BigInt inner, whose runtime-bigint
        # branch fires first off the narrowed read (the `(*this->f)` RAW
        # render; regression case narrowed_field_print_bigint).
        fdt = _field_decl_type(a, locals_, analyzer)
        if fdt is not None:
            fdt = unwrap_readonly(fdt)
            opt = _value_opt_scalar(fdt, analyzer) or _value_opt_str(fdt, analyzer)
            if opt is not None and is_big_int_type(opt.inner):
                return None
            # A POINTER-repr Optional FIELD takes `print_optional_val` too --
            # field storage IS `std::optional<T>`, so the print emit's pointer-repr
            # arm swaps the `_val` spelling in for exactly this source shape.
            return (opt or _value_opt_formatted(fdt, analyzer)
                    or _optional_ptr_borrow_wide(fdt, analyzer))
    return None

def _print_optptr_form(
        opt: 'OptionalType') -> 'tuple[PrintForm, str | None, str | None]':
    """The `print_optional` wrapper for a POINTER-repr Optional print arg
    (the print emit's pointer-repr arm): CTAD when the pointee streams through its
    own `operator<<`, `<Formatter, Inner>` when it does not (containers,
    bytearray). The inner spelling is the raw `inner.to_cpp()` -- this arm
    never takes the view-storage override, which only the value-repr
    arm consults."""
    inner = opt.inner
    inner_cpp = inner.to_cpp()
    fmt = _optional_print_formatter(inner, inner_cpp)
    if fmt is None:
        return PrintForm.OPT_PTR, None, None
    return PrintForm.OPT_PTR_FMT, inner_cpp, fmt


def _value_opt_formatted(t: 'TpyType | None', analyzer) -> 'OptionalType | None':
    """A value-repr Optional whose inner needs an explicit print Formatter
    (`bytes` / `bytearray` / a value tuple / Array / Span): the print emit's
    value-repr arm wraps the WHOLE optional exactly as for a scalar inner,
    only with the kind-keyed template args. Pointer-repr inners (list / dict /
    set / bytearray bindings) are a different arm -- `print_optional`."""
    if not isinstance(t, TpyType):
        return None
    tb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(tb, OptionalType) and not tb.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(tb.inner)
    return tb if _optional_print_formatter(inner, inner.to_cpp()) else None


def _optional_print_formatter(inner: TpyType, inner_cpp: str) -> 'str | None':
    """The container-formatter rule: the Formatter a
    print_optional / print_optional_val needs when the Optional inner's C++
    type has no plain `operator<<` (containers, tuples, bytes, bytearray).
    None means the CTAD/plain form. bytearray is checked before bytes -- it is
    also `is_any_bytes_type`, and the two reprs differ."""
    if is_bytearray_type(inner):
        return "::tpy::ByteArrayPrinter"
    if is_any_bytes_type(inner):
        return "::tpy::BytesPrinter"
    if isinstance(inner, TupleType):
        elem_cpps = ", ".join(et.to_cpp() for et in inner.element_types)
        return f"::tpy::TuplePrinter<{elem_cpps}>"
    if is_dict(inner):
        args = inner.type_args
        if args and len(args) >= 2:
            return (f"::tpy::DictPrinter<{args[0].to_cpp()}, "
                    f"{args[1].to_cpp()}>")
        return None
    if is_set(inner):
        args = inner.type_args
        if args:
            return f"::tpy::SetPrinter<{args[0].to_cpp()}>"
        return None
    if (is_list(inner) or is_array(inner) or is_span(inner)
            or isinstance(inner, ListRepeatType)):
        return f"::tpy::ListPrinter<{inner_cpp}>"
    return None


def _optional_print_inner_cpp(a: TpyExpr, inner: TpyType,
                              params: dict[str, TpyType]) -> str:
    """A borrow-form Optional
    VIEW param renders as the view storage (`optional<string_view>` /
    `optional<::tpy::BytesView>`), so its explicit template arg must be the
    view, not the owned inner. `params` is the declared PARAM map, so a local
    of the same type is unaffected."""
    fam = view_family_for_type(inner)
    if fam is not None and isinstance(a, TpyName):
        declared = params.get(a.name)
        if (isinstance(declared, OptionalType)
                and view_family_for_type(declared.inner) is fam):
            return fam.view_type.to_cpp()
    return inner.to_cpp()


def _print_optval_form(
        opt: 'OptionalType', a: 'TpyExpr | None' = None,
        params: 'dict[str, TpyType] | None' = None
) -> 'tuple[PrintForm, str | None, str | None]':
    """The `print_optional_val` wrapper for a value-repr Optional print arg:
    `Optional[bool]` /
    `Optional[float]` take an explicit Formatter + inner-type template
    (`<::tpy::print_bool, T>` / `<::tpy::print_float, T>`, both float widths on
    the float branch), a container / tuple / bytes inner the kind-keyed
    Formatter, every other inner (int / char / str) the plain form. Returns
    `(form, inner_cpp, fmt_cpp)`. The bool/float inners are never a view
    family, so their inner spelling collapses to `inner.to_cpp()`; the
    Formatter arm threads the view-storage override."""
    inner = opt.inner
    if is_bool_type(inner):
        return PrintForm.OPT_VAL_BOOL, inner.to_cpp(), None
    if is_float_type(inner):
        return PrintForm.OPT_VAL_FLOAT, inner.to_cpp(), None
    inner_cpp = (_optional_print_inner_cpp(a, inner, params)
                 if a is not None and params is not None else inner.to_cpp())
    fmt = _optional_print_formatter(inner, inner_cpp)
    if fmt is not None:
        return PrintForm.OPT_VAL_FMT, inner_cpp, fmt
    return PrintForm.OPT_VAL, None, None

def _print_tuple_opt_ternary(a: 'TpyTupleLiteral', rt: 'TpyType | None',
                             analyzer) -> 'TupleType | None':
    """A printed tuple LITERAL carrying an Optional-TERNARY element -- the
    astuple expansion over an `Optional[dataclass]` field. Deliberately NOT a
    widening of `_value_tuple_nested`, whose 28 call sites include reads and
    receivers this render says nothing about: the Optional element slot is
    admitted only where the element EXPRESSION is the ternary whose storage
    wrap renders it, so nothing but the shape `_lower_value_opt_ternary_arm`
    emits gets in. Every other element stays a plain value-tuple element."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    if not isinstance(u, TupleType):
        return None
    if len(u.element_types) != len(a.elements):
        return None
    found = False
    for el, et in zip(a.elements, u.element_types):
        if _storage_opt_ternary_elem(el, analyzer):
            found = True
            continue
        if not (_value_tuple_element_ok(et, analyzer)
                or _value_tuple_nested(et, analyzer) is not None):
            return None
        # The same view->owned element split `_opt_ternary_tuple_arm_ok`
        # keeps out of the ternary's own arm, on the PRINTED tuple's slots.
        eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        if isinstance(eu, TupleType):
            if not _opt_ternary_tuple_arm_ok(el, eu, analyzer):
                return None
        elif ((_resolved_str_value(et, analyzer) is not None
               or _resolved_bytes_value(et, analyzer) is not None)
                and not isinstance(el, TpyFieldAccess)):
            return None
    return u if found else None

def _wrap_print_form(a: TpyExpr, declared: dict[str, TpyType],
                     analyzer,
                     pointers: "AbstractSet[str]") -> 'PrintForm | None':
    """The kind-keyed printer wrap for a container / value-tuple / F1-record
    NAME print arg, or None outside the slice -- the print emit's per-kind arms:
    `Dict/Set/ListPrinter` (Array shares ListPrinter), `TuplePrinter`, a
    record streaming raw via its emitted operator<<. The ONE routing fact
    shared by local admission and `_lower_print_arg`, so admission and form
    selection cannot drift. NAMES only; the gate excludes pointer-locals.
    Span names share the ListPrinter sequence arm; dict-view printers
    reject; `self` renders `(*this)`, not the bare name -- excluded.

    A value-tuple SUBSCRIPT read yielding a whole (possibly nested) value tuple
    (`print(t[N])` -> `TuplePrinter(std::get<N>(t))`) routes too; a scalar-element
    read yields a bare value that the scalar print arm handles.

    A container-returning CALL (`print(list(range(0, 10, 0)))`) takes the same
    kind-keyed wrap around the inline call render -- the arg render is
    position-blind, so value category doesn't change the emit. A
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
        # A bytearray-returning call (`print(bytearray(xs))`,
        # `print(ba.upper())`) wraps in ByteArrayPrinter; a dict-view
        # result (`print(d.keys())`) streams raw via its own operator<<
        # (the print emit's is_dict_view arm).
        if is_bytearray_type(rt):
            return PrintForm.BYTEARRAY
        if is_dict_view(rt):
            return PrintForm.RAW
        # A range()-returning call streams raw via range's own operator<<,
        # like the range NAME leg below.
        if _range_object_value(rt):
            return PrintForm.RAW
        return None
    if isinstance(a, TpyArrayLiteral):
        # A list/Array LITERAL print arg wraps its typed brace-init
        # (`ListPrinter(std::vector<int32_t>{10, 20, 30})` -- the
        # explicit-type CTAD arm); dict/set literal renders self-describe
        # and ride their own arms.
        rt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if is_list(rt) or is_array(rt):
            return PrintForm.LIST
        return None
    if isinstance(a, TpyDictLiteral):
        # A dict LITERAL print arg (`print(asdict(..))`'s expansion) wraps
        # its self-describing ordered_map render in DictPrinter.
        rt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if is_dict(rt):
            return PrintForm.DICT
        return None
    if isinstance(a, TpyBinOp):
        # A container-result BINOP arg (`print(a | b)` ->
        # `SetPrinter((::tpy::set_union(a, b)))`): the kind-keyed wrap
        # over the binop render; the binop's own gate admits the operator.
        rt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if is_set(rt):
            return PrintForm.SET
        if is_list(rt) or is_array(rt):
            return PrintForm.LIST
        if is_dict(rt):
            return PrintForm.DICT
        # A bytearray-result BINOP (`print(ba + ba)` / `print(ba * 2)`)
        # takes ByteArrayPrinter -- the binop sibling of the CALL arm's
        # bytearray row above.
        if is_bytearray_type(rt):
            return PrintForm.BYTEARRAY
        return None
    if isinstance(a, TpyTupleLiteral):
        # A pointer-repr tuple LITERAL wraps its borrow-form render
        # (`TuplePrinter(std::tuple<Both*, ReprOnly*>{&(b), &(r)})`);
        # the lowering keys the borrow builder on the element repr. A
        # VALUE tuple literal (the all-value astuple expansion) wraps its
        # spelled brace render the same way.
        rt = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(analyzer.get_expr_type(a))))
        if isinstance(rt, TupleType) and rt.has_pointer_repr_element():
            return PrintForm.TUPLE
        if _value_tuple_nested(rt, analyzer) is not None:
            return PrintForm.TUPLE
        if _print_tuple_opt_ternary(a, rt, analyzer) is not None:
            return PrintForm.TUPLE
        return None
    if isinstance(a, TpySubscript):
        if (_tuple_subscript_value_read(a, declared, analyzer) is not None
                and _value_tuple_nested(
                    analyzer.get_expr_type(a), analyzer) is not None):
            return PrintForm.TUPLE
        # A CONTAINER-element read yielding a whole value tuple
        # (`print(pairs[0])` -> `TuplePrinter(__getitem__(pairs, 0))`):
        # the same kind-keyed wrap; the subscript arm gates the read.
        if (not isinstance(a.index, TpySlice)
                and _value_tuple_nested(
                    analyzer.get_expr_type(a), analyzer) is not None):
            return PrintForm.TUPLE
        # A list/Array/Span slice read (`print(items[a:b:c])`, or a slice
        # OBJECT index `print(items[s])`) yields an owned container
        # streamed via ListPrinter -- the container-slice lowering arm
        # renders `list_slice`/`list_stepped_slice`; slice_function_info
        # marks the slice read whatever the index spelling.
        if a.slice_function_info is not None:
            rt = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(analyzer.get_expr_type(a))))
            if is_list(rt) or is_array(rt) or is_span(rt):
                return PrintForm.LIST
        # A CONTAINER-element tuple chain (`print(pairs[1][1])` ->
        # `ListPrinter(std::get<1>(__getitem__(pairs, 1)))`): the borrow
        # lvalue takes the same kind-keyed wrap. The predicate is
        # list/Array-only until a dict/set witness appears.
        if _tuple_subscript_container_elem_read(a, declared, analyzer):
            return PrintForm.LIST
        # A nested-container ELEMENT read (`print(groups["a"])` ->
        # `ListPrinter(::tpy::__getitem__(groups, "a"))`): the element lvalue
        # streams through the same kind-keyed wrap a container name does.
        if _container_ref_alias_elem_subscript(a, declared, analyzer,
                                               pointers):
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
        # A module-variable container (`print(sys.argv)` ->
        # `ListPrinter((*::tpystd::sys::argv))`): the printer wrap is
        # kind-keyed and receiver-blind, and the read is the fixed
        # registered spelling, so the two compose. A pinned consumer of the
        # pointer-slot `(*slot)` read, like the slice template and the
        # native-slot arg.
        if _module_var_recv(a, declared, analyzer):
            mt = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(analyzer.get_expr_type(a))))
            if is_dict(mt):
                return PrintForm.DICT
            if is_set(mt):
                return PrintForm.SET
            if is_list(mt) or is_array(mt):
                return PrintForm.LIST
            return None
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
                     or _indirect_field_receiver_ok(a, declared, analyzer,
                                                    pointers))):
            return None
        fdt = _field_decl_type(a, declared, analyzer)
        if (fdt is not None
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(fdt))), OptionalType)):
            # A NARROWED value-Optional field prints print_optional_val over
            # the WHOLE field (the analyzed type is the bare
            # container, the declared type still Optional) -- not the
            # bare-deref kind-keyed printer, so reject.
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
            # `TuplePrinter(recv.field)` -- the print emit's TupleType arm over
            # the bare field lvalue.
            return PrintForm.TUPLE
        if (isinstance(ft, UnionType)
                and (_eligible_value_union(ft) is not None
                     or _eligible_ptr_union_wide(ft, analyzer) is not None
                     or _eligible_wrapper_union(ft, analyzer) is not None)):
            # A union-typed FIELD streams via the `::tpy::__str__` visitor
            # over the bare member read (the print emit's UnionType arm) -- the
            # same STR form a union NAME takes.
            return PrintForm.STR
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
    if isinstance(u, OwnType) and isinstance(unwrap_readonly(u.wrapped),
                                             TypeParamRef):
        # The print emit keys on the RESOLVED expression type, which sema already
        # stripped `Own` from; this keys on the DECLARED binding, which keeps
        # it. Peeled for the type-param payload only -- `_wrap_print_form`
        # fans out to ~10 forms, and a blanket peel would newly admit
        # Own[list] / Own[bytearray] / Own[tuple] against unwitnessed renders.
        u = unwrap_readonly(u.wrapped)
    if isinstance(u, TypeParamRef):
        # An open type-param value streams via ValuePrinter, which dispatches
        # the formatting at runtime -- a `bool` T must print True/False, not
        # 1/0, which a raw `<<` would give (the print emit's TypeParamRef arm).
        return PrintForm.VALUE_GENERIC
    if is_varargs(u):
        # A whole `*args` body view is a tuple in Python, so it streams via
        # VarargsPrinter (the print emit's is_varargs arm over the bare lvalue) --
        # NOT a container printer.
        return PrintForm.VARARGS
    if is_bytearray_type(u):
        # A bytearray NAME streams via ByteArrayPrinter (the print emit's
        # bytearray arm over the bare lvalue).
        return PrintForm.BYTEARRAY
    if is_span(u):
        # A Span NAME (`Span[int32]` / `Span[readonly[int32]]`) shares
        # the print emit's sequence arm: ListPrinter over the bare lvalue.
        return PrintForm.LIST
    if isinstance(u, ListRepeatType):
        # A LAZY repeat NAME shares the same sequence arm
        # (`ListPrinter(r)`); the decl registers the resolved binding.
        return PrintForm.LIST
    if isinstance(u, TupleType):
        # Any tuple NAME (value or non-value) streams via TuplePrinter over the
        # deref'd lvalue -- the TupleType print arm; `_lower_expr` derefs a
        # pointer-repr tuple local, so the render matches for both forms.
        return PrintForm.TUPLE
    if isinstance(u, NominalType) and record_like(u, analyzer):
        return PrintForm.RAW
    if _range_object_value(u):
        # A range() object streams raw via its own operator<< (no ListPrinter).
        return PrintForm.RAW
    if _slice_object_type(u):
        # A basic_slice/slice object streams raw via its own operator<<.
        return PrintForm.RAW
    if (isinstance(u, UnionType)
            and (_eligible_value_union(u) is not None
                 or _eligible_ptr_union_wide(u, analyzer) is not None
                 or _eligible_wrapper_union(u, analyzer) is not None)):
        # A union-typed NAME streams via the `::tpy::__str__` visitor
        # (the print emit's UnionType arm, union-kind-blind) -- the same STR
        # form a narrowed alias takes.
        return PrintForm.STR
    return None

def _print_kwarg_token(
        kv: TpyExpr, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> 'tuple[str, str | None] | None':
    """Classify a print sep=/end= kwarg source. ("literal", value-or-None)
    for a str literal (empty -> None: the token is skipped entirely,
    the chain_token short-circuit); ("name", None) for a resolved
    str/StrView NAME, which renders bare (no special arm fires for a
    plain str local/param); ("expr", None) for any other str-valued
    expression, which the caller hoists into a temp -- the chain repeats the
    sep token once per gap, so an in-place render would evaluate it N-1
    times. A StrView-typed expression is excluded: the hoisted binding
    outlives the full expression the view may point into. Anything else is
    unrouted; the caller lowers a "name"/"expr" source immediately."""
    if isinstance(kv, TpyStrLiteral):
        return ("literal", kv.value or None)
    if (isinstance(kv, TpyName)
            and kv.name not in pointers
            and kv.name not in narrowed
            and _resolved_str_value(declared.get(kv.name), analyzer)
            is not None):
        return ("name", None)
    resolved = _resolved_str_value(analyzer.get_expr_type(kv), analyzer)
    if resolved is not None and not is_str_view_type(resolved):
        return ("expr", None)
    return None


def _expr_evaluation_inert(exprs: 'Sequence[TpyExpr]',
                           declared: dict[str, TpyType], analyzer) -> bool:
    """Does evaluating every one of these expressions produce nothing
    observable? A literal, a name read and a marker-free member read do
    not; every other shape can run user code (a call, a property read, an
    element read that panics). The question is about EVALUATION ORDER: an
    arm that reorders the evaluation of a list of expressions may do so
    only when none of them can be observed running.

    Its print caller is the concrete instance -- CPython evaluates the
    positional args BEFORE the keyword values, while the chain evaluates an
    inline arg after the hoisted kwarg temp, so an evaluated kwarg is
    admitted only over inert args; the order of a side-effecting print
    ARGUMENT is already wrong (BUGS.md#subexpression-right-to-left-eval),
    and the gate keeps that defect from gaining a new shape. The str
    conversions happen inside the chain in both languages, so a record NAME
    whose `__str__` has side effects keeps CPython's order."""
    return all(isinstance(a, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                              TpyBytesLiteral, TpyBoolLiteral, TpyNoneLiteral,
                              TpyName))
               or _field_receiver_ok(a, declared, analyzer)
               for a in exprs)

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
    # A print-FORM selector, not an axis gate: the element streams through
    # the record's own `operator<<`, which a container does not have (it
    # prints through the ListPrinter / DictPrinter wrap that no tuple-element
    # print row threads yet), so only a record takes the bare / deref form.
    if not _f1_record(at, analyzer):
        return None
    _witness("print.tuple_record_elem")
    return "bare" if storage else "deref"


def _print_arg_ok(a: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """One print arg in the no-kwargs common-arg subset: a str/bytes literal,
    an eligible scalar (fixed-int / bool / double), a char (streamed raw --
    the print emit's direct-output arm; char has no int_traits, so no int8 cast),
    or a str-slice value (a str/StrView name or str-returning call -- string
    and string_view stream raw, the is_any_str_type arm)."""
    if isinstance(a, (TpyStrLiteral, TpyBytesLiteral)):
        # A bytes literal prints owned (the print emit threads no target).
        return True
    if isinstance(a, TpyNoneLiteral):
        # `print(None)` -> the bare "None" string literal (the print emit's
        # first arm).
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
        # An UN-narrowed pointer-repr Optional NAME prints the whole pointer
        # via `::tpy::print_optional(...)` -- the record inner streams
        # through its own operator<<, a container inner through the wrapper
        # the print-form classifier picks for the pointee. A NARROWED
        # occurrence stays deferred (witnessed at lowering).
        return True
    at = analyzer.get_expr_type(a)
    # A raw `Any` value streams via `tpy::Any`'s operator<< (PrintForm.RAW) --
    # the print emit's per-type-str dispatch; the inner render must lower bare (a
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
    # The four rows below choose the RAW print form: the value streams through
    # the record's own emitted `operator<<`. That is a form selection, not a
    # reference-axis question -- a container has no `operator<<` and prints
    # through the ListPrinter / DictPrinter wrap -- so they stay on the record
    # predicate until the print form comes off the TypeDef (the RULE's first
    # mechanism, TODO.md).
    if isinstance(a, (TpyCall, TpyMethodCall)):
        # An F1-record-returning call rvalue streams RAW via the record's
        # emitted operator<< (`print(datetime.fromtimestamp(x))` --
        # the print emit's fall-through `<< x`); `_lower_print_arg` threads
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
            # (the print emit's fall-through `<< x`), like the record-call row.
            return _witness("print.protocol_call")
    if isinstance(a, (TpyBinOp, TpyUnaryOp)):
        # An F1-record-result user-dunder binop / unary rvalue streams RAW
        # like the record-call row (`print(td1 + td2)`, `print(-td)` -- the
        # template render into the print emit's fall-through `<< x`).
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
    if (isinstance(a, TpySubscript) and isinstance(a.obj, TpyName)
            and not isinstance(a.index, TpySlice)
            and not a.needs_optional_runtime_check):
        # An F1-record container ELEMENT streams RAW via the record's
        # operator<< (`print(d[k])` -> `<< ::tpy::__getitem__(d, k)`) --
        # the subscript sibling of the field row; the subscript's own
        # lowering re-validates receiver/index shapes.
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if _f1_record(atu, analyzer):
            return _witness("print.record_subscript")
    return ((_resolved_scalar(at, analyzer) or _eligible_char(at)
             # A tpy-defined enum streams via its emitted operator<< (RAW);
             # an @native enum takes `::tpy::__repr__` (PrintForm.REPR).
             or _eligible_enum(at, analyzer) is not None
             or _resolved_str_value(at, analyzer) is not None
             or _resolved_bytes_value(at, analyzer) is not None  # BytesPrinter
             or _is_string_owned(at)))  # a concat result / String local: raw <<

# Sentinel for an f-string arg type outside the supported wrapper rows.
_FSTRING_INELIGIBLE = object()


def _fstring_container_call_arg(a: TpyExpr, analyzer) -> bool:
    """A CALL-shaped f-string interp arg whose list/dict/set result feeds the
    `_container_to_str` wrap (`f"{m.groups()}"` -> `list_to_str((*m).groups())`).
    The wrap consumes the bare call render inline, so the arg lowers under the
    ITERABLE result use -- the same override the for-head and the membership
    haystack grant a container-returning call. Type-INSTANTIATION calls
    (`f"{list(map(..))}"`) and the value families the wrap also covers (tuple,
    span) already render bare at the plain VALUE sink and keep it."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    if getattr(a, "call_type", None) is not None:
        return False
    return _nonvalue_container_ret(analyzer.get_expr_type(a))

def _fstring_arg_wrap(a: TpyExpr, analyzer, conv: int,
                      has_spec: bool,
                      declared: dict[str, TpyType]) -> 'str | None | object':
    """The Python-compatible formatting wrapper for one interpolated f-string
    arg, as a positional `{0}` template (None = pass through bare) -- the
    supported subset of the per-arg wrapper table -- or `_FSTRING_INELIGIBLE`
    for any row outside it. The type row is
    established first:
    `!r` then overrides it with `repr_of` (the conversion row precedes
    every type row, and no supported type is a container, so `repr_of` fires
    for all of them); `!s` is a no-op outside the user-type row, which is not
    supported -- so an unsupported type stays rejected under any conversion (its
    inner render is not pinned by the slice). A format spec flips the bool row
    to `static_cast<int>` and the float rows to bare (std::format handles the
    spec on double/float directly); the 8-bit-int and enum casts apply
    spec-or-not. bool is checked before the 8-bit-int row, keeping the table
    order (bool carries 8-bit int traits but must format as True/False). An
    IntLiteral-typed arg (`f"{5}"`) resolves through the module default int --
    fixed widths format bare at the fall-through; a runtime BigInt
    takes the `.to_string()` row (a spec'd int/BigInt arg is a sema error, so
    the spec never reaches that row)."""
    row: 'str | None | object' = _FSTRING_INELIGIBLE
    if isinstance(a, TpyStrLiteral):
        row = None  # const char[N] formats directly
    else:
        t = analyzer.get_expr_type(a)
        if t is None:
            return _FSTRING_INELIGIBLE
        bigint_key = _narrow_key_type(a, declared, analyzer)
        if bigint_key is _NARROW_UNMIRRORED:
            return _FSTRING_INELIGIBLE
        ctmpl = container_to_str_template(t)
        if ctmpl is not None:
            # Containers (tuple/list/span/dict/set) render via the runtime
            # to_str helpers irrespective of conversion: the
            # container arm precedes the conv rows, so !r/!s never
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
        # f-string slot -- there is no float32-typed context inside one -- so
        # it takes the same row as a concrete double. A concrete float32 arg
        # casts up first (float_to_str takes double) -- unless a spec routes
        # it bare into std::format.
        elif isinstance(t, FloatLiteralType) or is_float_type(t):
            if has_spec:
                row = None
            elif is_float32_type(t):
                row = "::tpy::float_to_str(static_cast<double>({0}))"
            else:
                row = "::tpy::float_to_str({0})"
        elif _runtime_bigint(bigint_key, analyzer) and not has_spec:
            # A runtime BigInt formats via `.to_string()`, keyed on the
            # DECLARED type (`is_runtime_bigint`) -- a
            # retro-widened literal-seeded local takes this row even though
            # sema types the occurrence int32. Placed ahead of the
            # 8-bit-int and enum casts.
            _witness("narrow.fstring_arg")
            row = "({0}).to_string()"
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
            # overrides it with repr_of below, the conversion row.
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
    if row is _FSTRING_INELIGIBLE:
        return _FSTRING_INELIGIBLE
    if conv == FSTRING_CONV_REPR:
        _witness("fstr.conv_repr")
        return "::tpy::repr_of({0})"
    if conv == FSTRING_CONV_STR:
        _witness("fstr.conv_str")
    return row
