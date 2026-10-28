"""Sequential statement lowering with per-shape validation for declarations,
branches, loops, with, try, raise, and narrowing statements.
"""

from __future__ import annotations
import copy
from collections.abc import Callable, Iterator, Set as AbstractSet
from .storage import alias_binding, borrowed_record, full_expression_record, native_container, native_container_field, owned_container_decl, global_name_binding, hoisted_binding, optional_layout, storage_borrow, tuple_layout, union_literal
from .callables import definition_signature, setitem_stub_callee, with_method_callee, with_method_stub
from .captures import capture_facts
from contextlib import contextmanager
from dataclasses import dataclass, replace
from ...diagnostics import SemanticError
from ...parse.nodes import (
    is_property_getter_read,
    RebindStorage,
    TryTier,
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    body_writes_name as _body_writes_name,
    is_none_return,
    written_names as _written_names,
    TpyAugAssign,
    TpyAwait,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBreak,
    TpyBytesLiteral,
    TpyCall,
    TpyChainedCompare,
    TpyCoerce,
    TpyContinue,
    TpyDelAttr,
    TpyDelItem,
    TpyDelVar,
    TpyDictLiteral,
    TpyExpr,
    TpyExceptHandler,
    TpyExprStmt,
    TpyFieldAccess,
    TpyForEach,
    TpyGeneratorExpression,
    TpyGlobal,
    TpyIf,
    TpyIfExpr,
    TpyIntLiteral,
    TpyFloatLiteral,
    TpyLambda,
    TpyMatch,
    TpyMethodCall,
    TpyFunction,
    TpyName,
    TpyNamedExpr,
    TpyNestedDef,
    TpyNoneLiteral,
    TpyNonlocal,
    TpyPassStmt,
    TpyListRepeat,
    TpyRaise,
    TpyReturn,
    TpySetLiteral,
    TpySlice,
    TpyStmt,
    TpyStarUnpack,
    TpyStrLiteral,
    TpySubscript,
    SubscriptDunder,
    TpyTry,
    TpyTupleLiteral,
    TpyTupleUnpack,
    TpyUnaryOp,
    TupleElemCapture,
    TpyImport,
    TpyVarDecl,
    TpyWhile,
    TpyWith,
    VarLinkage,
    is_docstring,
    is_super_del_call,
    read_names,
)
from ...typesys import (
    dyn_protocol_borrow_result,peel_binding, pointer_repr_optional, repr_shape,
                       returns_cpp_reference_shape, store_takes_elements, without_readonly)
from ...typesys import (
    CallableType,
    is_readonly_result_type,
    unwrap_qualifiers,
    unwrap_own,
    AnyType,
    BIGINT,
    BOOL,
    INT32,
    FloatLiteralType,
    IntLiteralType,
    LiteralType,
    ListRepeatType,
    NominalType,
    NoneType,
    OptionalType,
    OwnType,
    PendingListType,
    PendingViewType,
    ConcreteCoroType, ConcreteGenType,
    ReadonlyType,
    TpyType,
    TypeParamRef,
    TupleType,
    UnionType,
    VoidType,
    owned_tuple_storage_type,
    contains_pending_leaf,
    holds_borrowing_view,
    is_void_like_type,
    exception_record_cpp,
    handler_exception_record,
    is_dyn_protocol,
    is_protocol_type,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_borrowing_view_type,
    is_bytes_type,
    is_copy_iter,
    is_dict,
    is_dict_view,
    is_own_iter,
    is_fixed_int_type,
    is_list,
    is_set,
    is_span,
    is_str_type,
)
from ...modules.type_resolution import is_native_iterable
from ...codegen_cpp.gen_async import sub_struct_qualname
from ...sema.alias_rebind import bare_tuple
from ...sema.source_records import read_record, verdict
from ...sema.call_lends import Retention, expr_lends_storage
from ...sema.context import UnnamedHolder
from ...sema.aug_hold import holds_by_value
from ...sema.literal_utils import (fixed_int_literal_value_from_expr,
                                   numeric_literal_truth)
from ...codegen_cpp.forms import (
    LocalBinding,
    LoopBinding,
    is_plain_nonvalue,
    is_ptr_variant_union,
    reads_storage_form_optional,
)
from ...codegen_cpp.protocols import (
    dynamic_adapter_type,
    dynamic_base_name,
    protocol_param_template_name,
)
from ...sema.dyn_erasure import record_inherits_dynamic
from ...codegen_cpp.context import (escape_cpp_name,
                                    ternary_iterable_lends_storage)
from ...codegen_cpp.protocols import narrow_cast_rhs
from ...prescan import (
    collect_fact_kills,
    match_is_none,
    scope_bound_names,
)
from ...typesys import (is_polymorphic_subclass_fact,
                        polymorphic_source_inner,
                        polymorphic_source_is_pointer,
                        polymorphic_subclass_into_optional)
from ...codegen_cpp.type_resolution import resolve_stmt_recorded_type
from ...codegen_cpp.types import resolve_pending_container, resolve_tuple_pending
from ...codegen_cpp import emit_prims, resumable_cfg as rcfg
from ...identity_map import IdentityMap
from ...liveness import (
    collect_nested_def_nonlocal_rebinds, stmts_terminate,
    try_terminates_ignoring_finally, tuple_literal_leaves,
)
from ...value_category import (
    binds_owned_value, call_hands_back_value, call_result_holdable, call_returns_cpp_ref, for_source_is_rvalue,
    CONTAINER_LITERAL_NODES, is_rvalue_source,
    property_access_returns_cpp_ref,
    async_return_form, AsyncReturnForm,
)
from ..faces import witness as _witness
from ..reject import (
    ThirUnsupported,
    _walk,
    begin_stmt,
    expr_kind_tag,
    note,
    note_detail,
    stmt_reject_reason,
)
from ..scalar_leaves import (
    binds_cursor, binds_element, container_view, native_container_subject, native_container_type, record_owner,
    storage_leaf,
)
from ..nodes import (HoistDecl, THIRStoragePlacement, THIRHoistedBinding,
    THIROptionalRead, THIRCtorCall, THIRBorrowTupleLiteral, Form, PrintForm,
    PtrSlotKind, THIRArgTemp, THIRAssert, THIRAssign, THIRBinOp, THIRBreak,
    THIRBytesLiteral, THIRCall, THIRCoerce, THIRNoneCheck,
    THIRContainerLiteral, THIRContinue,
    THIRCloseLocal, THIRStmtSeq, THIRDelVar, THIRDelItem, THIRErrorReturnBind,
    THIRErrorReturnDiscard, THIRExpr, THIRExprStmt, THIRFieldAccess,
    THIRConsumingIter, THIRBorrowedRecord, THIRForEach, THIRNativeIteration,
    THIRForIterProto, THIRForRange, THIRFormConvert, THIRIf, THIRIfExpr,
    THIRIsinstance, THIRAnyIsinstance, THIRDynIsinstance,
    THIRDynIsinstanceMulti, THIRAnyNarrowAlias, THIRLiteral, THIRMethodCall,
    THIRModuleVar, THIRMove, THIRCopy, THIRName, THIRDynNarrowAlias,
    THIRNarrowAlias, THIRNestedDef, THIRConceptTest, THIRFoldedBlock,
    THIRFoldedIfChain, THIRFrameNestedDef, THIRFrameSlotWrite,
    THIRCoroHandleMove, THIRMatch, THIRImportInit, THIRNoOpStmt,
    THIROptViewArg, THIRPrint, THIRExceptHandler, THIRPrintArg,
    THIRPtrLocalDecl, THIRPtrLocalRebind, THIRRaise,
    THIRResumableReturn, THIRReturn, THIRFinallyDeferredReturn,
    DeferredPartKind, DeferredTuplePart, THIRTupleLiteral,
    THIRTupleStorageAlias, THIRSelf, THIRSetItem, THIRSubscript,
    THIRSliceAssign, THIRStmt, THIRStrAppend, THIRStrLiteral, THIRTry,
    THIRTupleElemTarget, THIRTupleUnpack, TupleElemBind, TupleSourceBind,
    THIRUnaryNot, THIRVarDecl, THIRWhile, THIRWith, THIRWithItem, THIRTempRead,
    WithTargetArm)
from .predicates import (_borrow_tuple_return_type, _own_genrec_return,
    _tuple_opt_elem_subscript, place_hops,
    _own_storage_tuple_return, _own_wrapper_return, _record_storage_return,
    _value_tuple_return, _viewfam_return_slots,
    _alias_call_source_ok, overload_if_fold, _call_storage_optional_return,
    receiver_is_const, _expr_is_const_source, _call_arm_variant, copy_call_arg,
    _storage_family_ok, _narrow_alias_name, _fresh_narrow_local,
    _generic_value_tuple_return, _comp_shadow_pointers, structural_protocol,
    _tuple_name_elem_subscript, _container_del_recv, _slot_free_ptr_reseat_ok,
    _module_var_recv, _post_if_narrow_plan, _opt_view_arg_shim,
    _container_enum_spell, _container_scalar_read, _dict_view_iterable_ok,
    _elif_link, _eligible_char, _dyn_proto_ptr,
    _eligible_enum, _eligible_ptr_union, _eligible_ptr_union_wide,
    _eligible_wrapper_union, _ru_elem_ok, _union_storage_val_cpp,
    _eligible_ptr_value, _eligible_scalar, _eligible_value_union,
    _wrapper_member_ctor_slot, _wrapper_member_literal_slot, _rvalue_ref_init,
    _select_node, _native_iter_value_slot, _own_record_tuple,
    _open_t_tuple_storage_decl, _union_elem_value_tuple, _protocol_auto_slot,
    _value_record_slot, _value_opt_record, _f1_param_lvalue_reseat_ok,
    _f1_record, record_like, _union_base_member_match, _f1_container_ref,
    _nested_container_elem_type, record_like, _method_rvalue_record_like,
    _single_member_of_family, _f1_tuple, _mixed_own_btuple_call,
    _renders_own_borrow_tuple, _nested_storage_tuple, _optional_borrow_tuple,
    _nullable_borrow_tuple_call, _alias_field_source_ok, _f2_reseat_ok,
    _facts_have_concrete, _field_decl_type,
    _narrowed_opt_container_field, _bound_call_of, _dunder_render,
    _subscript_call, _field_markers_clean, _field_receiver_ok,
    _chained_field_receiver_ok, _lvalue_chain_root, _typed_dict_recv_ok,
    _for_each_elem_binding_ok, _foreach_storage_opt_elem,
    _foreach_value_opt_elem, _is_borrow_ptr_local, _is_string_owned,
    _is_type_param_slot, _own_type_param_slot, _narrow_fact_member,
    _any_narrow_fact, _const_borrow_name, _poly_anchor_declared,
    _poly_narrow_info, _poly_post_if_fact,
    _poly_narrow_multi_info, _narrow_facts_ok,
    _any_narrow_facts_ok, _nonvalue_container_ret, _record_call_rvalue_operand,
    _callable_value, _optional_narrow_facts_ok, _opt_pointee_wide,
    _optional_ptr_borrow, _own_storage_opt_param,
    _nullable_static_protocol_param, _ptr_opt_borrow_call_ret,
    _ptr_value_field_recv_ok, _storage_optional_return_wide,
    _optional_ptr_borrow_name, _optional_ptr_borrow_wide,
    _optional_ptr_borrow_wide_name, _wrapper_union_like,
    _wrapper_ref_tuple_return, _storage_optional_return_type, _unwrap_own,
    _owned_str_append_target, _btuple_owning_call_init, _owned_viewfam_elem,
    _plain_member_call_markers_ok, _plain_method_fi_ok, _value_opt_scalar,
    _value_opt_callable, _value_opt_tuple, _value_opt_tuple_inner,
    _value_opt_tuple_copyable, _value_opt_owned_view,
    _peel_stale_view_owned_coerce, _reassert_bump_info, _resolve_pending_view,
    _resolve_plain_alias, _resolved_bytes_value,
    _resolved_str_value, _bytes_view_family_value, _coerce_disposition,
    _resolved_viewfam_value, _view_inner_value_opt, _value_opt_view,
    _is_range_call, _runtime_bigint, _narrow_key_type, _range_object_value,
    _slice_object_type, _storage_call_container, _storage_call_ret, _span_slot,
    _str_field_value_read, _wrap_view_owned_sink, _owned_viewfam_slot,
    _owned_copy_sink, _str_self_append_rhs, _type_family_tag,
    _type_param_value_slot, _peel_coerce, _subscript_container_recv_type,
    _unwrap_lit_coerce, _ru_container_literal_ok, _ru_instance_literal_ok,
    _value_tuple, _tuple_container_elem_read, _tuple_literal_has_ref_elements,
    _value_tuple_nested, _var_decl_type)
from .context import (
    FrameLocalClass,
    lends_elements,
    result_hosts_temps,
    return_slot,
    Slot,
    SlotHolds,
    SlotPosition,
    slot_for,
    storage_type,
    field_slot_use,
    SlotConstruct,
    _decl_slot_forms,
    _ExprResultUse,
    _LEND_OK,
    _NO_FORMS,
    _ONLY_ADDR_CALL,
    _ONLY_FIELD_RECV_BORROW,
    _ONLY_BTUPLE_SLOT,
    _ONLY_INDIRECT_READ,
    _ONLY_SELECT_PRVALUE,
    _ONLY_SELECT_FRESH_PRVALUE,
    _ONLY_SELECT_SLOT,
    _ONLY_SELF_TYPED,
    _ONLY_RECORD_COPY,
    _ExprUse,
    _recv_forms,
    _LowerCtx,
    _LowerScope,
    _Prescan,
    SinkPos,
    ValueOptKind,
    BindingTableError,
    binding_source_sites,
    nested_def_prescan,
)
from .checks import (_dunder_call_receiver_ok, owned_literal_layout,
    arg_retention,
    _declared_call_at_owning_elem, declared_call_elem_copy_ok,
    own_element_copy_member, own_element_payload,
    _chained_subscript_recv_type, _container_comp_arg,
    _value_opt_scalar_elem_arg, copy_construct_source,
    _print_tuple_record_elem, _borrow_tuple_local_type, _container_lit_elem_ok,
    _assert_narrow_info, _borrow_dunder_source,
    check_polymorphic_rvalue_opt_rebind, _bytes_aug_concat_ok,
    _class_const_aug_assign_ok, _container_aug_setitem_ok, aug_key_retention,
    _container_literal_decl_ok, _container_literal_shape_ok, _bytearray_recv,
    _bytearray_value_slot_init, _container_setitem_ok, _storage_decl_src,
    _deref_coerce_borrow_slot, _opt_ptr_addr_of_record_source,
    _user_record_setitem_ok,
    _expr_evaluation_inert, _print_kwarg_token, _record_slice_upcast_ok,
    _setitem_widened_elem_ok, _setitem_widened_family_ok,
    _tuple_elem_slots_ptr_optional, _field_over_call_ok, _is_builtin_print,
    _is_len_call, _iter_proto_call_ret, _record_rvalue_source_shape,
    _frame_factory_source, frame_object_slot, builds_named_frame,
    _record_aug_binop_ok, _rvalue_free_call_shape, _narrow_cond_info,
    _multi_narrow_cond_info, _or_chain_narrow_info, _any_narrow_cond_info,
    _optional_record_field_inner, _covariant_record_upcast_ok, _print_arg_form,
    _star_elem_print_form, _print_arg_ok, _print_tuple_opt_ternary,
    _wrap_print_form, _opt_strview_to_str_own_elem_arg, _print_optptr_form,
    _print_optval_form, _print_optval_opt, _ptr_union_source_ok,
    _ctor_shape_ok, _dyn_own_conformer_arg, _dyn_own_coro_factory_arg,
    _dyn_own_forward_call_arg, _dyn_own_handle_arg, _native_ctx_manager_ok,
    _native_record_rvalue_call_shape, _rvalue_storage_decl_call,
    _scalar_aug_assign_ok, _str_aug_append_ok, _str_list_method_iterable_ok,
    _comp_own_container, _user_iterator_iterable)
from ..source import (BindingConstruct, BindingForm, BindingRepr, Held,
                      NameFacts, Source, tuple_elems)
from .bindings import rebind_ptr_frame_field, rebind_ptr_frame_value
from .convert import (
    ElemRead, Plan, Refuse, borrowed_path_element_message, convert, converted,
    layout_tuple, open_param_result, spelled, store_slot, to_local,
    view_to_owned)
from .expressions import (
    _dunder_arg, _spell_brace, stamped, slot_payload_lent, lent_holder, _hoisted,
    _select_result, _operand_use_of,
    _self_capture_cpp, _whole_optional_bare, _is_own_param, _lower_module_var,
    _module_var_access_pair, _slice_recv_ok, _slice_index_ok,
    _lower_slice_recv, _constructs_value, _flush_witness, _hoists_arg_temp,
    _own_tuple_borrow_lift_arg, _deref_loop_source, _lower_isinstance_cond,
    _narrow_member_cpp, _narrow_subject_const, _narrow_subject_is_ptr,
    _union_source_layout, _union_extraction, _inline_union_member,
    _narrow_variant_cpp, _poly_cast_checks, _poly_cast_context,
    _poly_subject_is_pointer,
    _param_declared_type, _is_move_source, _node_moves, _lower_call_arg,
    _lower_char_targeted,
    _lower_class_const_write_target, _lower_copy_record, _lower_copy_special,
    lower_copy_construct, _lower_container_elem,
    _lower_expr, _to_opt_ptr, lower_print_sink, _lower_field_source,
    _lower_lambda, _strip_slot_leaf_deref, _strview_coerce_name,
    _lower_truthy, _cond_mixed_walrus_temps, _lower_borrow_tuple_literal,
    _lower_generic_tuple_literal, _lower_ru_literal, _lower_tuple_literal,
    _rb_operand_view_slots, _rb_operand_slots, _operator_literal_pins,
    _lower_slice_bound, _slot_literal_retype, _value_opt_scalar_binding,
    _value_opt_binding_kind, _value_opt_view_binding,
    _record_key, _lower_call_receiver,
    _record_receiver, _operator_receiver_pinned,
)
from . import bindings as _bindings
from . import comprehensions as _comprehensions
from . import field_write as _field_write
from . import match as _match

def _kind_detail(prefix: str, e: TpyExpr) -> bool:
    """`note_detail` with the expr's node kind as the suffix -- the
    dispatch-tail catch-all for a gate arm with no finer reject reason
    (set-if-empty, so a finer inner detail always wins)."""
    return note_detail(prefix + expr_kind_tag(e).removeprefix("expr."))


def _record_source_reject_detail(
        v: TpyExpr, pointers: set[str], narrowed: AbstractSet[str],
        borrow_slot: bool) -> str:
    """Sub-classify an unrouted record-return SOURCE for the reject detail --
    the coarse `return.record_source` mass is dominated by construct-disjoint
    frontiers that sequence differently (record-valued ctor/call args, record
    method-call value sources, deref/move of a pointer-local), so name the
    shape and slot rather than collapsing them into one bucket. Diagnostic
    only: `note_detail` refines the reject tag, never lowering/emit."""
    slot = "borrow" if borrow_slot else "storage"
    if isinstance(v, TpyMethodCall):
        return f"return.record_source.methodcall.{slot}"
    if isinstance(v, TpyName):
        if v.name in pointers:
            return f"return.record_source.ptrlocal.{slot}"
        if v.name in narrowed:
            return f"return.record_source.narrowed.{slot}"
        if v.name == "self":
            return f"return.record_source.self.{slot}"
        return f"return.record_source.name.{slot}"
    if isinstance(v, TpyFieldAccess):
        return f"return.record_source.field.{slot}"
    if isinstance(v, TpySubscript):
        return f"return.record_source.subscript.{slot}"
    if isinstance(v, TpyCall):
        return f"return.record_source.call.{slot}"
    return f"return.record_source.{type(v).__name__}.{slot}"

def _del_var_trivial(t: TpyType | None, analyzer) -> bool:
    """`del x` where x's resolved type is trivially destructible -- emits NO
    code, only the source comment. A pending str/bytes local must resolve
    first: its view resolution is trivial (`std::string_view`/span), its
    owned resolution (`std::string`/vector) takes the move-sink face
    instead."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    resolved = _resolved_str_value(t, analyzer) or _resolved_bytes_value(t, analyzer)
    if resolved is not None:
        t = resolved
    return t.is_trivially_destructible()

def _is_any_type(t: 'TpyType | None') -> bool:
    return isinstance(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))), AnyType
    ) if isinstance(t, TpyType) else False

def _any_value_dict(t: 'TpyType | None', analyzer) -> bool:
    """A `dict[K, Any]` whose KEY is in the shared key slice (fixed-int /
    runtime-BigInt / owned str or bytes -- `_container_elem_family`'s dict
    keys). The
    `Any` VALUE keeps the family out of `_container_scalar_read`, but the
    del-item / setitem-of-Any / return-of-read emits never construct or
    convert the value slot, so the key alone decides byte-parity there."""
    if t is None:
        return False
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType) or not is_dict(u):
        return False
    args = getattr(u, "type_args", None)
    if not args or len(args) < 2:
        return False
    key, val = args[0], args[1]
    # Deliberately NARROWER than the shared `_dict_key_shape_ok` slice:
    # record/Any-KEYED Any-dict writes are not exercised by the corpus, so
    # those keys reject here even though the read-side gates admit them.
    return ((is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
             or _owned_viewfam_elem(key, analyzer))
            and _is_any_type(val))

def _any_dict_subscript_shape_ok(
        sub: TpySubscript, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """The shared receiver/index half of the dict-Any subscript positions
    (setitem target / del-item / the Any-return read) -- `_setitem_target_ok`'s
    shape checks over the `_any_value_dict` family."""
    if isinstance(sub.index, TpySlice) or sub.slice_function_info is not None:
        return False
    if sub.typed_dict_field is not None or sub.needs_optional_runtime_check:
        return False
    recv = sub.obj
    if isinstance(recv, TpyName) and (recv.name in pointers
                                      or recv.name in narrowed):
        return False
    return _any_value_dict(
        _subscript_container_recv_type(recv, declared, analyzer), analyzer)

def _typed_dict_write_target(
        sub: 'TpyExpr', declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """`d["key"]` as a WRITE target on a total=True TypedDict -> the plain
    field lvalue `d.key` (the value assigns like a field write). total=False
    targets (whose READ render is the check wrap -- Python allows writing
    an absent key, a shape not modelled here) and receivers outside the
    read arm's set (a bare name / one-level admitted field) reject."""
    return (isinstance(sub, TpySubscript)
            and sub.typed_dict_field is not None
            and not sub.typed_dict_optional
            and _typed_dict_recv_ok(sub.obj, declared, pointers, narrowed,
                                    analyzer))

def _any_write_value_shape(v: 'TpyExpr', declared: dict[str, TpyType],
                           pointers: 'AbstractSet[str]',
                           narrowed: 'AbstractSet[str]',
                           analyzer) -> 'str | None':
    """The value slice every into-Any WRITE position shares (the Any-dict
    setitem row and the Any field write). Two rows: an ALREADY-Any bare
    declared name renders bare ("any_name"), and a not-yet-Any value
    arriving as an `into_any` coerce over a placeholder-transparent inner
    (a bare declared name or a str/int literal) rides `_lower_into_any`'s
    `{0}` template ("into_any"). Container-LITERAL inners reject: their
    brace-init is not placeholder-safe at these positions. Callers witness
    their own faces."""
    if (isinstance(v, TpyName) and v.name in declared
            and v.name not in pointers and v.name not in narrowed
            and _is_any_type(analyzer.get_expr_type(v))):
        return "any_name"
    if (isinstance(v, TpyCoerce) and v.coercion.name == "into_any"
            and (isinstance(v.expr, (TpyStrLiteral, TpyIntLiteral))
                 or (isinstance(v.expr, TpyName) and v.expr.name in declared
                     and v.expr.name not in pointers
                     and v.expr.name not in narrowed))):
        return "into_any"
    return None

def _any_dict_setitem_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """`d[k] = v` into a `dict[K, Any]` value slot, over the shared
    `_any_write_value_shape` slice. The bare-Any row needs no wrap at the
    element slot (`::tpy::__setitem__(d, k, v);`); the coerce row emits
    `::tpy::__setitem__(d, k, ::tpy::make_any(...));` (a movable inner
    name's last use moves the whole make_any value at the element
    store)."""
    if not _any_dict_subscript_shape_ok(
            stmt.target, declared, pointers, narrowed, analyzer):
        return False
    shape = _any_write_value_shape(stmt.value, declared, pointers, narrowed,
                                   analyzer)
    if shape == "any_name":
        return _witness("setitem.any_value")
    return shape == "into_any" and _witness("setitem.into_any")

def _lower_dyn_setattr_call(call: TpyMethodCall, lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The sema-synthesized `obj.__setattr__("name", <value>)` behind a
    dynamic-attr write (`obj.x = v`, D16). The generic method-call arm cannot
    admit it -- the `Any` value slot is outside the arg-family slice -- so
    this narrow arm handles a bare non-pointer F1-record receiver name, the
    literal name arg, and an `into_any`-coerced str-literal value (the
    coercion's `make_any` wrap carried as the THIRCoerce `{0}` template,
    applied to a placeholder)."""
    analyzer = lc.analyzer
    loc = getattr(call, "loc", None)
    recv = call.obj
    if not (isinstance(recv, TpyName) and recv.name in declared
            and recv.name != lc.self_receiver
            and not lc.binding(recv.name).pointer
            and recv.name not in lc.narrow.narrowed
            and not lc.binding(recv.name).frame_slot
            and _f1_record(declared.get(recv.name), analyzer)):
        note_detail("setattr.recv_shape")
        raise ThirUnsupported("stmt.assign")
    fi = call.resolved_function_info
    if (fi is None or not _plain_member_call_markers_ok(call)
            or call.needs_optional_runtime_check
            or not _plain_method_fi_ok(fi)
            or fi.cpp_template is not None or fi.native_function
            or fi.native_name or fi.type_params or fi.is_staticmethod
            or not fi.is_method
            or len(call.args) != 2 or len(fi.params) != 2):
        note_detail("setattr.call_shape")
        raise ThirUnsupported("stmt.assign")
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[recv.name])))
    ri = analyzer.registry.get_record_for_type(recv_t)
    if ri is None or len(analyzer.registry.get_method_overloads_with_parents(
            ri, call.method)) != 1:
        note_detail("setattr.overloads")
        raise ThirUnsupported("stmt.assign")
    name_arg, value_arg = call.args
    if not isinstance(name_arg, TpyStrLiteral):
        note_detail("setattr.name_shape")
        raise ThirUnsupported("stmt.assign")
    # The Any value slot takes the slice every into-Any WRITE position shares
    # (`_any_write_value_shape` -- the Any-dict setitem row and the Any field
    # write): an already-Any bare name passes bare, and an `into_any` coerce
    # over a placeholder-transparent inner (a bare declared name or a str/int
    # literal) rides `_lower_into_any`'s `{0}` template. A user
    # `__setattr__` whose value param is NOT Any sees no coerce at all -- the
    # bare literal is a plain arg (`s.__setattr__("_private", "bad")`) and
    # lowers like the name arg.
    any_shape = _any_write_value_shape(value_arg, declared, lc.names_with("pointer"),
                                       lc.narrow.narrowed, analyzer)
    if any_shape is not None:
        lowered_value: THIRExpr = _lower_expr(value_arg, lc, declared)
    elif (not _is_any_type(fi.params[1].type)
          and (isinstance(value_arg, (TpyStrLiteral, TpyIntLiteral))
               or (isinstance(value_arg, TpyName)
                   and value_arg.name in declared
                   and not lc.binding(value_arg.name).pointer
                   and value_arg.name not in lc.narrow.narrowed))):
        # The non-Any param's own slice, kept the same shape as the Any one
        # above (a str/int literal or a bare declared name): the generic
        # method-arg lowering renders it, and rejects what it cannot.
        lowered_value = _lower_call_arg(value_arg, fi.params[1].type, lc,
                                        declared, method_arg=True, retention=Retention.MEASURED)
    else:
        note_detail("setattr.value_shape")
        raise ThirUnsupported("stmt.assign")
    _witness("method.dyn_setattr")
    return THIRMethodCall(
        result_type=VoidType(),
        receiver=_lower_expr(
            recv, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
        method_cpp=escape_cpp_name(call.method),
        args=(_lower_call_arg(name_arg, fi.params[0].type, lc, declared,
                              method_arg=True, retention=Retention.MEASURED),
              lowered_value),
        loc=loc,
    )

def _scalar_or_str_unpack_elem(t: TpyType | None, analyzer) -> bool:
    """A tuple-unpack target / source-tuple element this cell admits: a value
    scalar, value-form record, callable, or str -- a view-form target
    `std::string_view name = std::get<i>(tup)` binds a view into a source
    tuple NAME that owns its storage (the one view rule), and an owned
    target off a disposable holder moves its element out
    (`std::string name = std::move(std::get<i>(__tup_N));`).
    `render_type(target_types[i])` spells the view, so the lowering needs no
    str arm. A value-opt SCALAR element and a value record bind the same
    plain typed copy (`std::optional<int32_t> a = std::get<0>(tup);`,
    `Coord v = std::get<1>(tup);`). Reference-record / bytes /
    pointer-repr-Optional / union elements need the borrow-alias and other
    unpack branches, which are not lowered yet."""
    return (_eligible_scalar(t)
            # An enum binds by value like a scalar.
            or _eligible_enum(t, analyzer) is not None
            or _value_record_slot(t)
            or _callable_value(t)
            or _value_opt_scalar(
                unwrap_ref_type(t) if t is not None else None,
                analyzer) is not None
            or _resolved_str_value(unwrap_ref_type(t) if t is not None else None,
                                   analyzer) is not None
            # The bytes twin: a view-form target (`::tpy::BytesView
            # tail = std::get<1>(tup);`) or an owned re-bind, spelled by
            # render_type exactly like the str flavor.
            or _resolved_bytes_value(
                unwrap_ref_type(t) if t is not None else None,
                analyzer) is not None
            # ... and their OPTIONAL flavor with an owned inner
            # (`std::optional<std::string> ctype = std::get<1>(tup);`): the
            # element is owned inside the tuple's storage, so the target is
            # the same plain by-value copy the value-opt SCALAR element
            # takes. A view-INNER optional stays out -- its copy would alias
            # the source tuple.
            or _value_opt_owned_view(
                unwrap_ref_type(t) if t is not None else None,
                analyzer) is not None)

def _unpack_target_decl(tt: TpyType, analyzer, render_type
                        ) -> 'tuple[TpyType, str]':
    """Resolve a tuple-unpack target type (a str PendingStrType -> its concrete
    view, which params never resolve in place) and render it. Shared by the
    standalone and for-each unpack lowerings so both spell a str target the
    same `std::string_view`; scalars pass through unchanged."""
    resolved = _resolved_str_value(tt, analyzer)
    if resolved is not None:
        tt = resolved
    return tt, render_type(tt)

def _container_scalar_tuple_iter(t: TpyType | None, analyzer, *,
                                 allow_record: bool = False,
                                 allow_storage_opt: bool = False) -> bool:
    """A `list[tuple[scalar-or-str, ...]]` / `Array[tuple[scalar-or-str, ...], N]`
    binding -- admitted as the tuple-unpack loop's iterable (element family gated
    by `_scalar_or_str_unpack_elem`). Element-TOUCHING read gates
    (subscript, plain iteration, method calls) check their own element family
    and reject tuple elements; `len(xs)` IS lit up (`_is_len_call` is
    element-agnostic) but renders the bare-name `::tpy::__len__`."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if not (is_list(t) or is_array(t)) or not args:
        return False
    elem = unwrap_readonly(args[0])
    return (isinstance(elem, TupleType) and bool(elem.element_types)
            and all(_scalar_or_str_unpack_elem(et, analyzer)
                    # `allow_record` (the borrow-tuple for-head unpack): a
                    # REFERENCE-typed element aliases into an is_ref target
                    # via the loop element's per-element lend -- a
                    # record or a container alike (`for n, xs in pairs:`
                    # over `list[tuple[int32, list[int32]]]`), the same ref
                    # alias the target gate's ref family already admits.
                    or (allow_record and record_like(
                        unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(et))), analyzer))
                    # `allow_storage_opt` (the COMP/genexpr unpack head
                    # only): a ptr-repr Optional[F1-record] element binds
                    # a storage_opt target; the for-STATEMENT head
                    # keeps rejecting it (unwitnessed).
                    or (allow_storage_opt
                        and _optional_ptr_borrow(et, analyzer) is not None)
                    for et in elem.element_types))

def _range_bound_literal_value(arg: TpyExpr) -> int | None:
    # The inline-vs-hoist decision for a range bound: an inlinable bare int
    # literal (possibly behind the int_literal coerce) vs a name/expr hoisted
    # to a temp. No magnitude clamp: both forms render the bound through the
    # shared literal rules, so only this choice must agree with
    # _extract_int_literal, including fixed-int ctor literals (`int32(3)`).
    return fixed_int_literal_value_from_expr(arg)

def _lower_range_arg(arg: TpyExpr, et: TpyType, lc: _LowerCtx,
                     declared: dict[str, TpyType]) -> THIRExpr:
    """Lower one range bound/step. A folded fixed-int-ctor literal
    (`int32(3)`) lowers to the bare-token literal directly -- lowering the
    ctor CALL would render `int32(3)`'s call shape, but a range bound inlines
    the folded token. Every other admitted shape lowers normally; the counter
    slot retype applies either way."""
    peeled = _unwrap_lit_coerce(arg)
    if isinstance(peeled, TpyCall):
        v = _range_bound_literal_value(arg)
        if v is not None:
            lit = THIRLiteral(result_type=lc.analyzer.get_expr_type(peeled),
                              value=v, loc=getattr(arg, "loc", None))
            return _slot_literal_retype(lit, et, lc)
    return _slot_literal_retype(_lower_expr(arg, lc, declared), et, lc)

def _range_step_kind(step_arg: TpyExpr, declared: dict[str, TpyType],
                     analyzer) -> str | None:
    # Classify a 3-arg range's step into a counter-loop arm, or None when the
    # step is not lowered here. Conservative slice over the fixed-int subset
    # the emitter renders:
    #   * a bare (possibly negated) int literal or a fixed-int-ctor literal
    #     (`int32(2)` -- folded like _extract_int_literal) -> plus_one (+1) /
    #     unit_neg (-1) / literal_pos / literal_neg; a zero step is rejected
    #     (a zero step takes the Range ctor, not this counter loop).
    #   * any other fixed-int-typed expression -> variable (captured once into
    #     `__step_N`, which is what makes an arbitrary expression safe here:
    #     Python reads range()'s step at call time and the C++ head would
    #     otherwise re-evaluate it every iteration).
    # A ctor-literal step (`int32(2)`) folds like a bound: the fold yields
    # the same bare token as a plain literal, so the stepped arms' overflow
    # helpers receive an identical render (pinned by the ctor-literal-step
    # unit and the corpus exec run).
    lit = _range_bound_literal_value(step_arg)
    if lit is not None:
        # A wide literal step is deferred: the stepped arms thread the step
        # token through the overflow-check helpers, a render pinned only for
        # the int32-range subset (bounds have no such clamp -- inline-vs-hoist
        # is the only decision there and the token render is shared).
        if not -2**31 <= lit <= 2**31 - 1:
            return None
        if lit == 0:
            return None
        if lit == 1:
            return "plus_one"
        if lit == -1:
            return "unit_neg"
        return "literal_pos" if lit > 0 else "literal_neg"
    if isinstance(step_arg, TpyName) and is_fixed_int_type(declared.get(step_arg.name)):
        return "variable"
    st = analyzer.get_expr_type(step_arg)
    if st is None:
        return None
    stu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
    if is_fixed_int_type(stu) or _runtime_bigint(stu, analyzer):
        return "variable"
    return None

def build_loop_node(shape: str, var: str, et: TpyType,
                    body: tuple[THIRStmt, ...], lc: _LowerCtx,
                    declared: dict[str, TpyType], *,
                    range_call: 'TpyCall | None' = None,
                    step_kind: str = "plus_one",
                    iterable: 'THIRExpr | None' = None,
                    source: 'TpyExpr | None' = None,
                    const_loop_var: bool = False,
                    iterable_lvalue: bool = True,
                    consuming: bool = False,
                    hoist_loop_var: bool = False,
                    binding: 'BindingRepr | None' = None,
                    **position_facts: object) -> THIRStmt:
    """The one loop assembly the for statement and a comprehension clause
    share, so a source renders the same loop wherever it is written;
    `position_facts` are node fields only one of the two positions sets.
    `binding` is the loop variable's record (an unpack's holder), whose
    `loop_binding` is how the loop binds each element."""
    if shape == "range":
        assert range_call is not None
        args = range_call.args
        start = None
        start_is_literal = True
        stop_arg = args[0]
        if len(args) >= 2:
            start = _lower_range_arg(args[0], et, lc, declared)
            start_is_literal = _range_bound_literal_value(args[0]) is not None
            stop_arg = args[1]
        # The unit-step arms reference no step expr; the other three render it.
        step = (_lower_range_arg(args[2], et, lc, declared)
                if len(args) == 3
                and step_kind in ("literal_pos", "literal_neg", "variable")
                else None)
        return THIRForRange(
            var=var, elem_type=et,
            stop=_lower_range_arg(stop_arg, et, lc, declared),
            start=start, start_is_literal=start_is_literal,
            stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
            body=body, step=step, step_kind=step_kind,
            hoist_loop_var=hoist_loop_var, **position_facts)
    assert iterable is not None
    if binding is None or binding.loop_binding is None:
        raise BindingTableError(
            f"loop variable {var!r} has no planned loop binding")
    if shape == "iter_proto":
        return THIRForIterProto(
            var=var, elem_type=et, iterable=iterable, body=body,
            const_loop_var=const_loop_var, iterable_lvalue=iterable_lvalue,
            binding=binding, **position_facts)
    assert shape == "each", shape
    iteration = (None if source is None else _native_iteration(
        iterable, source, et, lc, iterable_lvalue=iterable_lvalue,
        consuming=consuming))
    return THIRForEach(
        var=var, elem_type=et, iterable=iterable, body=body,
        const_loop_var=const_loop_var, iterable_lvalue=iterable_lvalue,
        consuming=consuming, hoist_loop_var=hoist_loop_var,
        iteration=iteration, binding=binding, **position_facts)


def _native_iteration(iterable: THIRExpr, source: TpyExpr, et: TpyType,
                      lc: _LowerCtx, *, iterable_lvalue: bool,
                      consuming: bool) -> 'THIRNativeIteration | None':
    """The native container a begin/end loop walks in place -- None for a
    fresh value or a consuming wrapper, which have no place."""
    analyzer = lc.analyzer
    source_fact = None
    if iterable_lvalue and not consuming:
        if isinstance(iterable, THIRName):
            source_fact = native_container(iterable.result_type,
                                           _const_borrow_name(iterable.name, lc, const_locals=True), analyzer)
        elif native_container_field(iterable) and isinstance(iterable.receiver, (THIRName, THIRSelf)):
            # A container field of a named record: const when the
            # receiver lends const (C++ propagates it through the member).
            source_fact = native_container(
                iterable.result_type,
                _iteration_yields_const(source, lc, analyzer)
                or isinstance(iterable.field_identity.type, ReadonlyType), analyzer)
        elif (isinstance(iterable, (THIRCall, THIRMethodCall)) and iterable.resolved_callee is not None
              and native_container_type(native_container_subject(iterable.result_type))):
            # The container a user callee returns by reference, walked in
            # place: const when its return type is, or, for a callable whose
            # result follows its receiver, when the call's receiver is
            # emitted const (`THIRMethodCall.receiver_access`).
            signature = iterable.resolved_callee.signature
            source_fact = native_container(
                iterable.result_type,
                is_readonly_result_type(signature.return_type)
                or signature.result_follows_receiver and isinstance(iterable, THIRMethodCall)
                and iterable.receiver_access.readonly, analyzer)
    elif (not consuming and isinstance(iterable, THIRMethodCall)
          and iterable.stub_callee is not None):
        # A container view a stub returns (`d.keys()`) walks its own
        # declared element; any other result is a fresh value, no place.
        if container_view(native_container_subject(iterable.result_type)):
            source_fact = native_container(
                iterable.result_type, _iteration_yields_const(source, lc, analyzer), analyzer)
    if source_fact is not None and (not binds_cursor(source_fact.type) or not binds_element(et, (
            source_fact.element.type if isinstance(source_fact.element, THIRBorrowedRecord)
            else source_fact.element))):
        source_fact = None
    if source_fact is None:
        return None
    return THIRNativeIteration(source_fact)


def _module_slot_global(name: str, lc: '_LowerCtx') -> bool:
    """A non-value global the module itself declares (`T* g{}` at
    namespace scope), read in its own init: a write allocates or reuses a
    `static __global_slot_N`. An imported global is never written there."""
    return lc.binding(name).module_slot


def _bare_pointer_name(name: str, lc: '_LowerCtx') -> bool:
    """A name already holding the `T*` it renders bare: the module's own
    pointer-slot global, or a pointer local the module spells bare (an
    imported global carries its qualified spelling, which a bare-name copy
    cannot produce)."""
    rec = lc.binding(name)
    return rec.module_slot or (rec.pointer and rec.global_cpp is None)


def _shadowable_globals(lc: '_LowerCtx',
                        declared: dict[str, TpyType]) -> 'AbstractSet[str]':
    """The module globals a loop variable may shadow: those whose read is a
    BARE name. A SPELLED global (imported / native-linkage) renders a fixed
    qualified name from `global_cpp`, so a shadowing loop var would make the
    body read the SHADOWED global -- a wrong-VALUE miscompile, not a spelling
    nit -- and a pointer-slot global derefs at value positions. Empty outside
    module scope, where a shadow rides the hoist arm instead."""
    if not lc.top_level_scope:
        return frozenset()
    return frozenset(
        n for n in declared
        if n not in lc.prescan.global_cpp
        and n not in lc.prescan.global_write_cpp
        and not lc.binding(n).global_slot)


def _loop_var_shadows_global_ok(stmt: TpyForEach, analyzer,
                                declared: dict[str, TpyType],
                                shadowable: 'AbstractSet[str]') -> bool:
    """At MODULE scope a name in `declared` is a pre-seeded global, and the loop
    still emits a fresh loop-scoped binding that shadows it (`was_declared`
    changes only post-loop bookkeeping, never the render). Admit that shadow
    only for a global whose read is BARE and whose type IS the element type:

      * a SPELLED global (imported / native-linkage) renders a fixed
        qualified name from `global_cpp`, so the body would read the SHADOWED
        global instead of the loop binding -- a wrong-value miscompile, not a
        spelling nit;
      * a pointer-slot global derefs at value positions;
      * a type mismatch would let the loop var's registration retype a read
        after the loop (defensive -- sema rejects that first).

    Everything outside that set keeps rejecting; the loop variable's record
    drops only the pointer facts of the binding it shadows
    (`bindings._LOOP_SCRUBBED`), so admission is where the spelled/slot
    families have to be excluded."""
    if stmt.var not in shadowable:
        return False
    et = _resolved_loop_elem_type(stmt, analyzer)
    gt = declared.get(stmt.var)
    if et is None or gt is None:
        return False
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt))) == \
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))


def _for_loop_shape_ok(stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
                       allow_hoist: bool = False,
                       allow_branch_decls: bool = False,
                       shadowable_globals: 'AbstractSet[str]'
                       = frozenset()) -> bool:
    """The for-loop shape guards shared by the range-for and container-for cells: no
    async / enum / consuming; and a loop-scoped var (not shadowing
    an outer local, whose `was_declared` handling the emitter does not reproduce).
    A for/else clause is NOT a shape reject: the else block emits outside the loop
    machinery.

    `allow_hoist` admits the hidden-counter / assign-binding rebind
    (`hoist_loop_var`, a loop var used after the loop); `stmt.var in declared` is
    then expected (the rebind case) and not a reject. `allow_branch_decls` admits
    the branch-first-decl predecls (`if_branch_decls`) the caller renders through
    `hoist_decls`; without it those reject (the route has no predecl
    equivalent)."""
    if (stmt.is_async or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None):
        return False
    if not allow_branch_decls and analyzer.if_branch_decls.get(stmt):
        return False
    if stmt.hoist_loop_var:
        return allow_hoist
    return (stmt.var not in declared
            or _loop_var_shadows_global_ok(stmt, analyzer, declared,
                                           shadowable_globals))

@dataclass(frozen=True)
class _ForEachRoute:
    route: str
    elem_type: TpyType
    # How the source is CAPTURED (`auto&` vs `auto`). Filled by
    # `_select_for_each_route` from the shared value-category verdict: an arm
    # classifies the loop SHAPE and says nothing about the capture. Two
    # spellings stay: the consuming arm, which does not classify the source at
    # all (it builds a new rvalue out of it), and the one arm that returns
    # before the selector reaches the shared verdict.
    iterable_lvalue: bool = False
    step_kind: str = "plus_one"
    str_list_method: bool = False
    container_field: bool = False
    value_tuple_elem: bool = False
    bigint_counter: bool = False
    str_literal_iterable: bool = False
    # Native auto-consuming iterable (`::tpy::own_iter(std::move(name))`): the
    # consuming `__iter__`'s C++ symbol. Set only by `_for_consuming_route`; the
    # iterable is wrapped in THIRConsumingIter (rvalue capture) and the loop var
    # joins movable_locals for the body.
    consuming_native_name: 'str | None' = None
    # An OwnIter-typed NAME iterable (`for x in oi:` off `oi = own_iter(src)`):
    # plain lvalue capture (`auto& __obj_N = oi;`), consuming `auto&&` elem
    # binding, no wrap.
    consuming_name: bool = False
    # A 3-arg range whose `variable` step is a computed expression rather than
    # a bare name -- witnessed apart so the name leg cannot stand in for it.
    step_expr_computed: bool = False
    # The literal iterable's RESOLVED container type, carried only when the
    # element binds by reference: a bare brace-init deduces
    # std::initializer_list, whose elements are const, so every mutating use of
    # the loop var would be ill-formed C++. A value element copies out of the
    # deref and keeps the bare brace.
    literal_src_type: 'TpyType | None' = None


def _for_range_route(stmt: TpyForEach, analyzer,
                     declared: dict[str, TpyType],
                     shadowable_globals: 'AbstractSet[str]'
                     = frozenset()) -> '_ForEachRoute | None':
    # `for v in range(stop | start, stop [, step])` over a fixed-int or runtime-
    # BigInt counter (loop var not used after the loop). The shared shape guards
    # exclude the other richer for-shapes. A 3-arg stepped range needs a
    # slice-eligible step (see _range_step_kind); both counter widths share the
    # `__step_N` capture, and only the upfront overflow check is fixed-int-only
    # (an arbitrary-precision counter cannot overflow).
    it = stmt.iterable
    if not _is_range_call(it) or not _for_loop_shape_ok(
            stmt, analyzer, declared, allow_hoist=True,
            allow_branch_decls=True,
            shadowable_globals=shadowable_globals):
        return None
    if it.kwargs or it.double_star_unpack is not None or len(it.args) not in (1, 2, 3):
        return None
    et = unwrap_ref_type(stmt.elem_type) if stmt.elem_type is not None else None
    # A BigInt counter (a BigInt bound / module default int) shares the step-1
    # emit shape -- `cpp_elem` renders `::tpy::BigInt`, literal bounds retype
    # to the elem slot (`::tpy::BigInt(3)`), non-literal bounds hoist to
    # `__start/__stop` temps like fixed ints. The overflow-check helpers only
    # fire for step != +-1, admitted below for a fixed-int counter only.
    if not _eligible_scalar(et):
        return None
    bigint_counter = _runtime_bigint(et, analyzer)
    nargs = len(it.args)
    step_kind = "plus_one"
    if nargs == 3:
        step_kind = _range_step_kind(it.args[2], declared, analyzer)
        if step_kind is None:
            return None
    lowered_et = resolve_int_literals(
        et, analyzer.ctx.default_int_for_literal)
    return _ForEachRoute(
        route="range", elem_type=lowered_et, step_kind=step_kind,
        bigint_counter=bigint_counter,
        step_expr_computed=(step_kind == "variable"
                            and not isinstance(it.args[2], TpyName)))

def _resolved_loop_elem_type(stmt: TpyForEach, analyzer) -> 'TpyType | None':
    # resolve_int_literals: a literal-seeded container's elem_type is still
    # IntLiteral (IntLiteralType.to_cpp() would emit the VALUE), so the loop
    # binding needs the resolved default-int spelling -- a list literal
    # written as the iterable, or a module-level one; a function-local
    # list's element is its cells' settled type. A str/bytes loop var
    # (list[str] element / owned-str dict key / a bytes-yielding iterator)
    # resolves its pending view type the same way, matching the lowering's
    # `et`.
    if stmt.elem_type is None:
        return None
    et = resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                              analyzer.ctx.default_int_for_literal)
    view_et = _resolved_viewfam_value(et, analyzer)
    return view_et if view_et is not None else et

def _narrowed_value_opt_view_iter(it: TpyName,
                                  declared: dict[str, TpyType],
                                  analyzer) -> 'TpyType | None':
    """A NAME iterable declared value-repr `str | None` / `bytes | None`
    whose read sema proved narrowed (get_expr_type resolves to the bare
    payload): the payload view iterates through the container begin/end
    loop over the deref read (`auto& __obj_N = (*b);`). Returns
    the payload's concrete view type, or None -- an un-narrowed read (still
    Optional) stays rejected. Shared by the for-each and comprehension
    name legs so the admission cannot drift."""
    du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[it.name])))
    if _value_opt_view(du, analyzer) is None:
        return None
    at = analyzer.get_expr_type(it)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if atu is None or isinstance(atu, OptionalType):
        return None
    return (_resolved_str_value(atu, analyzer)
            or _resolved_bytes_value(atu, analyzer))

def _for_each_container_route(
        stmt: TpyForEach, analyzer,
        declared: dict[str, TpyType],
        pointers: 'AbstractSet[str]',
        shadowable_globals: 'AbstractSet[str]' = frozenset(),
        tparam_bounds: 'dict | None' = None) -> '_ForEachRoute | None':
    # `for v in <container>` over a NativeIterable with a value-scalar (`list[scalar]` /
    # `dict[fixed-int-key]`, a typed copy; bytes/BytesView are
    # NativeIterable[uint8] -- the same typed-copy loop var), char (str/StrView,
    # NativeIterable[char]),
    # str (a `list[str]` element / owned-str dict key -- the loop var is a fresh
    # view var, usage-resolved to `std::string_view` or an owned `std::string`
    # copy; `loop_var_binding` spells both), or F1-record (`list[record]`, a
    # borrow alias) loop var. A generator/user-iterator
    # (the generic `__iter__`/`__next__` loop) and the
    # shared richer for-shapes take other routes.
    if not _for_loop_shape_ok(stmt, analyzer, declared, allow_hoist=True,
                              allow_branch_decls=True,
                              shadowable_globals=shadowable_globals):
        return None
    it = stmt.iterable
    # A plain in-scope container name, or a str/bytes-family field off an
    # F1-record receiver (`for c in h.name:`) -- both C++ lvalues
    # (is_lvalue_iterable: a name, or a field access over one), taking the
    # `auto& __obj_N =` capture -- or an eligible str- or container-returning
    # call (`for c in full(s):` / `for x in make_list():`). Which capture the
    # source takes is the selector's one verdict, not this arm's. A
    # slice-subscript iterable is admitted below; bytes-returning calls stay
    # deferred.
    if _is_range_call(it):
        # A ZERO-literal-step range (`range(1, 10, 0)`): the counter loop
        # declines (zero step panics at runtime), so the
        # `::tpy::Range<T>(...)` OBJECT iterates through the generic
        # begin/end loop -- the rvalue owning-capture foreach over the
        # range-object call render. Only that slice; every other declined
        # range shape rejects.
        if not (not it.kwargs and it.double_star_unpack is None
                and len(it.args) == 3
                and _range_bound_literal_value(it.args[2]) == 0):
            return None
        et = _resolved_loop_elem_type(stmt, analyzer)
        if not _for_each_elem_binding_ok(et):
            return None
        _witness("foreach.range_object")
        return _ForEachRoute(route="container", elem_type=et)
    str_list_method = False
    container_field = False
    str_literal_iterable = False
    if isinstance(it, TpyStrLiteral):
        # `for ch in "abc"`: the str literal is an rvalue captured as
        # `std::string_view("abc")` (C string literals carry the NUL
        # terminator, so the wrap trims it); char elements.
        it_type = _resolved_str_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            return None
        str_literal_iterable = True
    elif isinstance(it, TpyCall):
        ret = analyzer.get_expr_type(it)
        it_type = _resolved_str_value(ret, analyzer)
        if it_type is None:
            ret_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                     if ret is not None else None)
            if is_copy_iter(ret_u):
                # A copy_iter(...) call iterable: the iterator object already
                # exposes begin/end, so the call rvalue takes the owning
                # capture directly. This peephole binds the SEMA elem type
                # without the IntLiteral resolution the native peephole
                # applies, so a literal elem rejects; own_iter rejects too
                # (its consuming elem binding + movable-local registration
                # have no route here).
                if (stmt.elem_type is not None
                        and isinstance(unwrap_ref_type(stmt.elem_type),
                                       IntLiteralType)):
                    note_detail("foreach.copy_iter_literal_elem")
                    return None
                et = _resolved_loop_elem_type(stmt, analyzer)
                if not _for_each_elem_binding_ok(et):
                    note_detail("foreach.elem_family."
                                + _type_family_tag(et, analyzer))
                    return None
                _witness("foreach.copy_iter_call")
                return _ForEachRoute(route="container", elem_type=et)
            # container_ret_ok widens TpyCall lowering past str returns; the
            # bytes returns it admits in value position are filtered here.
            ret_span = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret))) \
                if ret is not None else None
            if is_span(ret_span):
                # A Span-returning call (`for v in span(x):`): the value
                # view rvalue takes the owning `auto __obj_N =` capture
                # into the same begin/end loop (call.span_value_ret is the
                # result-gate row; this is its for-head consumer).
                _witness("foreach.span_call")
                it_type = ret_span
            elif not _nonvalue_container_ret(ret):
                return None
            else:
                it_type = unwrap_readonly(unwrap_send_sync(ret))
    elif isinstance(it, TpyMethodCall):
        # `for v in d.values():` / `for k in d.keys():` / `for kv in d.items():`
        # (dict view -- items yields a value tuple, admitted by the elem gate
        # below) or `for w in s.split():` (a str method returning
        # `Own[list[str]]`).
        dict_view = _dict_view_iterable_ok(
            it, declared, analyzer, methods=("values", "keys", "items"),
            field_recv_ok=True)
        str_list_method = _str_list_method_iterable_ok(
            it, declared, analyzer)
        if dict_view or str_list_method:
            it_type = analyzer.get_expr_type(it)
        else:
            # A container-returning user METHOD call (`for v in g.get():`):
            # the TpyCall arm's twin -- a borrow return is a C++ lvalue
            # (`auto& __obj_N =`), an `Own[...]` return an owning rvalue
            # capture. The call's own lowering re-validates receiver/args
            # and rejects a shape outside its slice.
            ret = analyzer.get_expr_type(it)
            if not _nonvalue_container_ret(ret):
                return None
            it_type = unwrap_readonly(unwrap_send_sync(ret))
            # The lvalue question is the return CONVENTION's, not the
            # annotation's: `Own[...]` is only the spelling sema forces on a
            # by-value container return, and a getter spells the same fact
            # through the field's storage reference instead.
    elif isinstance(it, TpyName):
        if it.name not in declared:
            note_detail("foreach.name_global")
            return None
        # declared (the binding type) rather than get_expr_type: a container-literal
        # local's use sites carry the pre-resolution PendingListType (see
        # method-call validation). A param binding is Ref/readonly-wrapped -- unwrap
        # like _container_scalar_read does internally.
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[it.name])))
        # A str/bytes local's binding is a Pending view type the registry lookup
        # can't see through; resolve to the concrete view/owned nominal first.
        it_view = _resolved_viewfam_value(it_type, analyzer)
        if it_view is not None:
            it_type = it_view
        nv = _narrowed_value_opt_view_iter(it, declared, analyzer)
        if nv is not None:
            # A proven-narrowed value-repr `str|None` / `bytes|None` NAME:
            # the payload view iterates through this same begin/end
            # loop over the deref read (`auto& __obj_N = (*b);` -- the
            # value-opt param arms' narrowed `(*b)` render under the lvalue
            # capture). NOT the narrowed-alias universal-loop leg: that
            # render family is wrong for a container payload.
            _witness("foreach.narrowed_value_opt_view")
            it_type = nv
    elif (isinstance(it, TpySubscript)
            and _lvalue_chain_root(it, declared, analyzer,
                                   pointers) is not None
            and not getattr(it, "needs_optional_runtime_check", False)):
        # A container-ELEMENT subscript iterable (`for v in g["a"]:` off
        # `dict[str, list[T]]`): the checked element read is a C++ lvalue
        # (`auto& __obj_N = ::tpy::__getitem__(g, "a");`) captured into
        # the same begin/end loop as a container name; the read lowers
        # through the subscript arm's ITERABLE-use element row.
        # The receiver may be a rooted lvalue CHAIN (`self.rows[i]`,
        # `row.cells[i]`): every hop is itself an lvalue, so the capture
        # still binds storage that outlives the loop. A call or a slice in
        # the chain would mint a temporary and keeps the located reject.
        if stmt.iter_borrow_unplaceable:
            # Sema could file no loan for this iteration, so no mutation of
            # what it borrows can be matched against it: the loop would
            # hand out references into storage the invalidation check cannot
            # see (`for b in self.grid.rows[i]: self.grid.rows.append(...)`).
            note_detail("foreach.iter_borrow_unplaceable")
            return None
        _st = analyzer.get_expr_type(it)
        _stu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_st)))
                if _st is not None else None)
        if not _f1_container_ref(_stu):
            note_detail("foreach.subscript_elem_family")
            return None
        it_type = _stu
    elif isinstance(it, TpyArrayLiteral):
        # A list-literal iterable renders target-less (no container target is
        # threaded) and captures the initializer list by value --
        # `auto __obj_N = {a, b, c};`, an rvalue. The literal lowers through
        # the container-literal arm with threading OFF so the elements spell
        # identically (a threaded str element would take an owned-copy wrap,
        # which this position must not emit).
        rt = analyzer.get_expr_type(it)
        it_type = resolve_pending_container(rt, analyzer) or rt
        if it_type is None:
            note_detail("foreach.iter_literal_type")
            return None
        it_type = unwrap_readonly(unwrap_send_sync(it_type))
    elif isinstance(it, TpyFieldAccess):
        if not (_field_receiver_ok(it, declared, analyzer)
                # A container member reached THROUGH a Ptr binding
                # (`for seg in subsector.segs:` on `subsector:
                # Ptr[SubSector]`): the field's own pointer arm renders the
                # `none_attr(subsector, "segs").segs` hop, and the lvalue capture
                # binds to it exactly as to a plain member read.
                or _ptr_value_field_recv_ok(it, declared, analyzer)
                # ... or a rooted lvalue CHAIN receiver (`self.inner.xs`,
                # `rows[0].cells`, `table[k].cells`): each hop lowers
                # through its own arm and stays an lvalue, so the capture
                # binds live storage exactly as a one-hop member read does.
                or _chained_field_receiver_ok(it, declared, analyzer,
                                              pointers)):
            # A module-attr GLOBAL iterable is a user record in every
            # witnessed shape, so it rides the user-iterator route's own
            # module-var leg -- no admission here (a native-container
            # module var has no witness; this route would need one).
            note_detail("foreach.field_parent")
            return None
        if stmt.iter_borrow_unplaceable:
            # Same rule as the element arm: a chain deeper than a loan key can
            # be spelled (`self.inner.xs`, `table[k].cells`) leaves every
            # mutation of what is iterated invisible to the invalidation check.
            note_detail("foreach.iter_borrow_unplaceable")
            return None
        it_type = _resolved_viewfam_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            # Container field (`for x in self.xs:`): the DECLARED field type,
            # unwrapped like the name arm. The iterable renders as its own
            # THIRFieldAccess inside the same lvalue `auto& __obj_N =`
            # capture a name takes.
            ft = _field_decl_type(it, declared, analyzer)
            if ft is None:
                note_detail("foreach.field_family")
                return None
            it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
            if (isinstance(it_type, OptionalType)
                    and _narrowed_opt_container_field(
                        it, analyzer.get_expr_type(it), declared, analyzer)):
                # A NARROWED Optional[container] field carries its own
                # `(*this->d)` unwrap in the read, so begin()/end() come off
                # the container inside -- the route keys the INNER. This
                # route must claim the shape: the iter-proto route's field
                # leg lacks the is_native_iterable exclusion its call
                # sibling carries, so it would otherwise emit the universal
                # __iter__ loop where begin/end is required.
                it_type = unwrap_readonly(it_type.inner)
            container_field = True
            if isinstance(it_type, TypeParamRef):
                # An open-T FIELD whose bound is NativeIterable/Spannable
                # (`Wrap[T: NativeIterable[int32]]`, `for x in self.items:`):
                # the begin/end peephole reads the bound off
                # current_type_param_bounds, so the member takes the same
                # `auto& __obj_N =` begin/end loop a concrete container does.
                b = (tparam_bounds or {}).get(it_type.name)
                bu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(b)))
                      if b is not None else None)
                if not (isinstance(bu, NominalType)
                        and bu.qualified_name() in ("tpy.NativeIterable",
                                                    "tpy.Spannable")):
                    note_detail("foreach.field_family")
                    return None
                et = _resolved_loop_elem_type(stmt, analyzer)
                if not _for_each_elem_binding_ok(et):
                    note_detail("foreach.elem_family."
                                + _type_family_tag(et, analyzer))
                    return None
                _witness("foreach.native_bound_field")
                return _ForEachRoute(
                    route="container", elem_type=et,
                    container_field=True,
                    value_tuple_elem=_value_tuple(et, analyzer) is not None)
    elif (isinstance(it, TpySubscript)
          and it.slice_function_info is not None
          and isinstance(it.index, TpySlice)):
        # A slice-subscript iterable (`for b in items[1:3]:`): the slice
        # rvalue takes the owning `auto __obj_N =` capture; the subscript
        # arm re-validates the receiver / bounds and rejects a shape
        # outside its slice.
        rt = analyzer.get_expr_type(it)
        it_type = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
                   if rt is not None else None)
        if it_type is None:
            return None
    else:
        # Unhandled node kinds and non-native iterable families are
        # sub-classified at the reject boundary (_for_each_reject_detail).
        return None
    if not is_native_iterable(it_type, analyzer.registry):
        return None
    # The loop var (list/set/Span/Array element, or dict key) binds through the
    # SHARED loop_var_binding: a value scalar/tuple is a typed copy, a record a
    # borrow alias (`auto&&`/`const auto&`), a union the composite-ref arm. No
    # rebinding guard is needed: sema forbids reassigning a non-value loop var
    # (`_check_nonvalue_rebinding`), so a reference loop var is only read or
    # field-mutated through the alias, matching Python's reference semantics.
    et = _resolved_loop_elem_type(stmt, analyzer)
    # Compositional element gate: admit any element the shared
    # loop_var_binding renders (every resolved concrete type -- the helper
    # picks the form). Unresolved pending elements reject.
    # The storage-opt element family (ptr-repr Optional[F1-record]) admits
    # on THIS route only -- its loop var registers in the storage-opt set.
    if not (_for_each_elem_binding_ok(et)
            or _foreach_storage_opt_elem(et, analyzer)):
        note_detail("foreach.elem_family." + _type_family_tag(et, analyzer))
        return None
    return _ForEachRoute(
        route="container", elem_type=et,
        str_list_method=str_list_method,
        container_field=container_field,
        str_literal_iterable=str_literal_iterable,
        literal_src_type=(_owned_literal_type(it_type)
                          if isinstance(it, TpyArrayLiteral)
                          and _aliasing_loop_elem(et) else None),
        value_tuple_elem=_value_tuple(et, analyzer) is not None)


def _owned_literal_type(it_type: 'TpyType | None') -> 'TpyType | None':
    """A for-source literal OWNS its elements, so a reference-typed element
    source (a record PARAMETER) must not leak its `T&` into the container the
    capture spells: `std::array<T&, N>` is ill-formed."""
    args = getattr(it_type, "type_args", None)
    if not args:
        return it_type
    return replace(it_type, type_args=tuple(
        unwrap_ref_type(a) if isinstance(a, TpyType) else a for a in args))


def _aliasing_loop_elem(et: 'TpyType | None') -> bool:
    """Whether the loop var ALIASES the element rather than copying it -- the
    same value-ness `loop_var_binding` asks before choosing `auto&&` -- or a
    head's target aliases a reference member of it."""
    if et is None:
        return False
    eu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    if isinstance(eu, OwnType):
        eu = eu.wrapped
    return not eu.is_value_type() or (isinstance(eu, TupleType)
                                      and eu.has_pointer_repr_element())

def _for_consuming_route(stmt: TpyForEach, analyzer,
                         declared: dict[str, TpyType]) -> '_ForEachRoute | None':
    """`for x in items:` where sema flagged the iterable for native auto-
    consuming iteration (`stmt.consuming_iter_fi`, a movable container name at
    last use with a native consuming `__iter__`): the iterable becomes
    `::tpy::own_iter(std::move(items))` (rvalue, owning `auto __obj_N =`
    capture) and the loop var binds `auto&&` and joins `movable_locals` for the
    body, so a consuming element use (`result.append(x)`) moves. Only the
    NATIVE arm -- a user-defined consuming `__iter__` takes the `__next__`
    loop shape (a later cell). The other richer for-shapes are excluded."""
    fi = stmt.consuming_iter_fi
    it = stmt.iterable
    own_iter_name = False
    if fi is None:
        # The EXPLICIT `own_iter(items)` call (the not-at-last-use warn
        # case): sema sets no consuming_iter_fi, but the render is the
        # same `::tpy::own_iter(std::move(items))` + consuming begin/end
        # loop -- reuse the wrap with the call's ARG as the value.
        if (isinstance(it, TpyCall)
                and is_own_iter(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(analyzer.get_expr_type(it)))))
                and len(it.args) == 1 and not it.kwargs
                and isinstance(it.args[0], TpyName)):
            native_symbol = "tpy::own_iter"
            it = it.args[0]
        elif (isinstance(it, TpyName) and it.name in declared
              and is_own_iter(unwrap_readonly(unwrap_ref_type(
                  unwrap_send_sync(declared[it.name]))))):
            # An OwnIter-typed NAME (`for x in oi:`): the name captures as a
            # plain lvalue and the elem binds consuming `auto&&`; no wrap.
            # The CopyIter sibling declines -- its elem binds
            # non-consuming.
            native_symbol = None
            own_iter_name = True
            _witness("foreach.own_iter_name")
        else:
            return None
    elif not fi.native_name:
        return None
    else:
        native_symbol = fi.native_name
    if (stmt.is_async or stmt.enum_iterable is not None
            or stmt.hoist_loop_var
            # A read-only body marks const_loop_var, but the shared
            # loop_var_binding's consuming arm wins over const (`auto&&`),
            # so the OwnIter-NAME flavor admits it; the sema-flagged
            # flavors never co-occur with const (sema only flags a
            # mutated loop var).
            or (stmt.const_loop_var and not own_iter_name)):
        return None
    if analyzer.if_branch_decls.get(stmt):
        return None
    if stmt.var in declared:
        return None
    if not isinstance(it, TpyName) or it.name not in declared:
        return None
    it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[it.name])))
    if own_iter_name:
        if not is_own_iter(it_type):
            return None
    elif not is_native_iterable(it_type, analyzer.registry):
        return None
    et = _resolved_loop_elem_type(stmt, analyzer)
    if not _for_each_elem_binding_ok(et):
        return None
    return _ForEachRoute(route="container", elem_type=et,
                         iterable_lvalue=own_iter_name,
                         consuming_native_name=native_symbol,
                         consuming_name=own_iter_name)

def _for_enum_route(stmt: TpyForEach, analyzer,
                    declared: dict[str, TpyType]) -> '_ForEachRoute | None':
    """`for c in Color:` -- range over `::tpy::EnumUtil<E>::members` (an lvalue
    static array). The loop var binds
    the enum value (`Color c = *__beg_N;`). The other shape guards
    (`_for_loop_shape_ok` minus its blanket enum exclusion) still apply."""
    if stmt.enum_iterable is None:
        return None
    if (stmt.is_async or stmt.is_tuple_unpack
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return None
    if stmt.var in declared:
        return None
    et = stmt.enum_iterable
    if not _for_each_elem_binding_ok(et):
        return None
    return _ForEachRoute(route="enum", elem_type=et)


def _open_t_iterable_bound(u: 'TypeParamRef', tparam_bounds: 'dict | None',
                           analyzer) -> bool:
    """Whether an open-T iterable's BOUND is one the universal
    `::tpy::__iter__` loop serves: a structural non-@dynamic protocol that is
    not native-iterable. The NativeIterable / Spannable bounds take the
    begin/end peephole instead, and a concrete bound spells its own family --
    both keep rejecting. The bound is read off
    `ctx.current_type_param_bounds`."""
    b = (tparam_bounds or {}).get(u.name)
    if b is None:
        return False
    bu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(b)))
    return bool(isinstance(bu, NominalType) and bu.is_protocol
                and not is_dyn_protocol(bu)
                and bu.qualified_name() not in ("tpy.NativeIterable",
                                                "tpy.Spannable")
                and not is_native_iterable(bu, analyzer.registry)
                and _witness("foreach.open_t_field"))


def _unpack_head_iterable_ok(stmt: TpyForEach) -> bool:
    """Whether the protocol loop takes a tuple-unpacking head over this
    iterable: a call only, which keeps master's acceptance set for these
    heads. It is not a safety gate: the admitted call form loses a write
    the same way (`for a, n in iter(xs)`,
    BUGS.md#combinator-tuple-step-copies-record-element), and so would a
    name, a field, an element, a protocol parameter (a lent list is
    accepted there, BUGS.md#lent-list-at-own-iterable-param-copies), a
    select or a generator expression wrapping such an iterator. The fact
    that removes the gate is TODO.md "Container iterators lend their
    step"."""
    return isinstance(stmt.iterable, (TpyCall, TpyMethodCall))


def _for_iter_proto_route(
        stmt: TpyForEach, analyzer,
        declared: dict[str, TpyType],
        iterator_objects: 'AbstractSet[str]' = frozenset(),
        *, protocol_param_ok: bool = False,
        tparam_bounds: 'dict | None' = None,
        shadowable_globals: 'AbstractSet[str]'
        = frozenset()) -> '_ForEachRoute | None':
    """The universal `::tpy::__iter__` + `__next__` protocol loop, for the
    iterables the container route's NativeIterable gate excludes. Slice: a
    free GENERATOR or iterator-returning call (`for x in gen(n):` /
    `for x in reversed(xs):` / `for x in SimpleIter(4):` -- the callee
    admission is the call classifier's `generator_ok` / the ITERABLE-use
    result widening) or a
    USER-ITERATOR local name (a concrete non-generic record with
    `__iter__`/`__next__` -- a C++ lvalue, `auto& __src_N`). Protocol-typed
    params (the template-param spelling), fields, and gen-valued locals are
    not lowered yet."""
    if not _for_loop_shape_ok(stmt, analyzer, declared,
                              shadowable_globals=shadowable_globals):
        return None
    it = stmt.iterable
    if stmt.is_tuple_unpack and not _unpack_head_iterable_ok(stmt):
        return None
    if isinstance(it, TpyCall):
        if _is_range_call(it):
            return None
        fi = it.resolved_function_info
        if fi is None:
            return None
        if not fi.is_generator and not _iter_proto_call_ret(it, analyzer):
            return None
    elif isinstance(it, TpyMethodCall):
        # A member (`obj.gen(n)`) or module-qualified (`m.gen(n)`) generator
        # or iterator factory call. The expr lowering gates which spellings
        # route (_member_gen_call_iterable_ok / the marker arm's
        # generator_ok).
        fi = it.resolved_function_info
        if fi is None:
            return None
        if not fi.is_generator and not _iter_proto_call_ret(it, analyzer):
            return None
    elif isinstance(it, TpyIfExpr):
        # A TERNARY of iterator-returning calls (`for v in (gen([1, 2, 3])
        # if flag else gen([9])):`): the rvalue capture binds the chosen
        # frame (`auto __src_N = ((flag) ? (..) : (..));`), each arm's
        # deferred temps banking into its own emit region. RVALUE call arms
        # only -- an lvalue arm would need the alias capture the ternary
        # cannot carry.
        for arm in (it.then_expr, it.else_expr):
            if not isinstance(arm, (TpyCall, TpyMethodCall)):
                return None
            afi = arm.resolved_function_info
            if afi is None:
                return None
            if not afi.is_generator and not _iter_proto_call_ret(arm,
                                                                 analyzer):
                return None
        if ternary_iterable_lends_storage(it, analyzer.registry.get_record,
                                          analyzer.get_expr_type):
            return None
        et = _resolved_loop_elem_type(stmt, analyzer)
        if not _for_each_elem_binding_ok(et):
            note_detail("foreach.elem_family."
                        + _type_family_tag(et, analyzer))
            return None
        _witness("foreach.ifexpr_iterable")
        return _ForEachRoute(route="iter_proto", elem_type=et)
    elif isinstance(it, TpyFieldAccess):
        # A user-iterator FIELD read (`for k in r.headers:`) captures the
        # member lvalue (`auto& __src_N = r.headers;`) exactly like a local
        # user-iterator name; the field read lowers through the field arm.
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(it))))
        if not (_field_receiver_ok(it, declared, analyzer)
                # A module-attr GLOBAL iterable (`for k in os.environ:`):
                # the same lvalue capture over the `(*slot)` deref.
                or _module_var_recv(it, declared, analyzer)):
            return None
        if isinstance(u, TypeParamRef):
            # An open-T FIELD (`for x in self.items:` on `Summer[T:
            # Iterable[int32]]`): the C++ member is the deduced `T`, so the
            # loop captures it bare and `::tpy::__iter__` resolves through
            # the bound -- the same universal render a protocol-typed param
            # name takes, and gated the same way (SYNC bodies only, no
            # native-iterable / begin-end peephole bound).
            if not protocol_param_ok or not _open_t_iterable_bound(
                    u, tparam_bounds, analyzer):
                return None
        elif (not isinstance(u, NominalType) or u.is_protocol
                or not _user_iterator_iterable(u, analyzer)):
            return None
    elif isinstance(it, TpyGeneratorExpression):
        # A GENEXPR iterable (`for b in (n for n in data):`): the frame is
        # an rvalue the loop owns (`auto __src_N = <frame creation>;`); the
        # genexpr's own lowering gates which sources route.
        et = _resolved_loop_elem_type(stmt, analyzer)
        if not _for_each_elem_binding_ok(et):
            note_detail("foreach.elem_family."
                        + _type_family_tag(et, analyzer))
            return None
        _witness("foreach.genexpr_iterable")
        return _ForEachRoute(route="iter_proto", elem_type=et)
    elif isinstance(it, TpySubscript):
        # A user-iterator container-ELEMENT read (`for x in items[0]:`)
        # captures the element lvalue (`auto& __src_N =
        # ::tpy::__getitem__((*items), 0);`); the read lowers through the
        # subscript arm's record-element row. A NATIVE-iterable element
        # (`g["a"]` on dict[str, list[T]]) takes the begin/end loop,
        # a different render -- excluded.
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(it))))
        if not (isinstance(su, NominalType) and not su.is_protocol
                and not is_native_iterable(su, analyzer.registry)
                and _user_iterator_iterable(su, analyzer)):
            return None
    elif isinstance(it, TpyName):
        if it.name == "self":
            # `for x in self:` captures the receiver DEREFERENCED
            # (`auto& __src_N = (*this);`) -- the deref the
            # receiver read carries; the record must be a plain
            # user-iterator like the local-name leg.
            su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(it))))
            if not (isinstance(su, NominalType) and not su.is_protocol
                    and _user_iterator_iterable(su, analyzer)):
                return None
            et = _resolved_loop_elem_type(stmt, analyzer)
            if not _for_each_elem_binding_ok(et):
                note_detail("foreach.elem_family."
                            + _type_family_tag(et, analyzer))
                return None
            _witness("foreach.self_iterable")
            return _ForEachRoute(route="iter_proto", elem_type=et)
        if it.name not in declared:
            return None
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[it.name])))
        if (isinstance(u, OptionalType)
                and _optional_ptr_borrow_wide(u, analyzer) is not None):
            # A PROVEN-narrowed ptr-opt CONTAINER param (`for k in d:` under
            # `d is not None`): sema retyped the read to the bare container
            # and the capture derefs the pointer binding
            # (`auto& __src_N = (*d);`) into the universal loop, which is
            # container-family-blind.
            at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(it))))
            # Separate faces per family: one merged face would read as
            # witnessed off the dict traffic alone if the list/set leg
            # regressed.
            fam_face = ("foreach.narrowed_opt_dict" if is_dict(at)
                        else "foreach.narrowed_opt_listset"
                        if (is_list(at) or is_set(at)) else None)
            if fam_face is None:
                return None
            et = _resolved_loop_elem_type(stmt, analyzer)
            if not _for_each_elem_binding_ok(et):
                note_detail("foreach.elem_family."
                            + _type_family_tag(et, analyzer))
                return None
            _witness(fam_face)
            return _ForEachRoute(route="iter_proto", elem_type=et)
        # User-iterator records (monomorphized generic ones included -- their
        # `::tpy::__iter__` universal loop renders identically): a
        # protocol-typed binding (Iterator[T] param) spells through the deduced
        # template param and rides the `protocol_param_ok` leg below. An
        # iterator-object
        # LOCAL (`it = g()`, the `auto` decl) is exempt: its protocol type
        # never reaches a C++ spelling, the loop captures the plain lvalue
        # (`auto& __src_N = it;`).
        # An `Own[protocol]` PARAM (`items: Own[Iterable[T]]`): the
        # monomorphized `T_items&&` slot takes the universal loop below
        # unchanged (a non-protocol Own payload keeps its own legs).
        proto = structural_protocol(u)
        if isinstance(u, OwnType) and proto is not None:
            u = proto
            _witness("foreach.own_proto_param")
        if it.name in iterator_objects:
            pass
        elif proto is not None:
            # A STRUCTURAL protocol-typed param (`it: Iterator[int32]`) in a
            # SYNC body: the deduced `T_it&` param is a plain C++ lvalue, so
            # the loop captures it bare (`auto& __src_N = it;`) and
            # `::tpy::__iter__` resolves via ADL -- the same render as a
            # user-iterator record name. Excluded (the NativeIterable
            # peephole and its universal-default split): any
            # native-iterable verdict, @dynamic bindings (adapter dispatch),
            # and resumable bodies (protocol_param_ok is threaded False there
            # -- the frame emitters have no witnessed protocol-loop shape).
            elem = _resolved_loop_elem_type(stmt, analyzer)
            elem_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                elem))) if elem is not None else None)
            # An `Own[T] | None` element is excluded here directly rather
            # than relying on the elem-binding gate downstream (its Optional
            # bind + consuming seed are not paired here).
            if (isinstance(elem_bare, OptionalType)
                    and isinstance(unwrap_readonly(elem_bare.inner),
                                   OwnType)):
                return None
            if not protocol_param_ok:
                return None
            if isinstance(elem_bare, OwnType):
                # A plain `Own[T]` element (`for x in source:` over
                # `Iterable[Own[T]]`): the SAME universal-loop bind --
                # loop_var_binding peels Own, so a value T is the typed
                # copy, a reference T an OWNED binding and a tuple / union /
                # optional T an OWNED_STORAGE one (its own value either
                # way) -- and the binding table's loop row marks the
                # variable `movable` (`_loop_row`, bindings.py).
                # Split faces so the value leg cannot witness the ref leg.
                _witness("foreach.proto_own_elem_val"
                         if unwrap_readonly(
                             elem_bare.wrapped).is_value_type()
                         else "foreach.proto_own_elem_ref")
            if u.qualified_name() in ("tpy.NativeIterable", "tpy.Spannable"):
                # The NativeIterable peephole: these protocol params
                # take the plain begin/end range-for (records.py synthesizes
                # begin/end for Spannable conformers), not the universal
                # `::tpy::__iter__` loop.
                et = _resolved_loop_elem_type(stmt, analyzer)
                if not _for_each_elem_binding_ok(et):
                    note_detail("foreach.elem_family."
                                + _type_family_tag(et, analyzer))
                    return None
                _witness("foreach.native_proto_param")
                return _ForEachRoute(
                    route="container", elem_type=et,
                    value_tuple_elem=_value_tuple(et, analyzer) is not None)
            if is_native_iterable(u, analyzer.registry):
                return None
        elif isinstance(u, TypeParamRef):
            # An open-T PARAM iterable (`for x in items:` on
            # `[T: Iterable[int32]]`): the bare `param_val_or_ref_t<T>`
            # lvalue takes the same universal loop, gated like the open-T
            # FIELD flavor (SYNC bodies, Iterable bound only).
            if not protocol_param_ok or not _open_t_iterable_bound(
                    u, tparam_bounds, analyzer):
                return None
            _witness("foreach.open_t_param")
        elif isinstance(u, ListRepeatType):
            # A LAZY repeat local (`r = [10] * n; for v in r:`): the
            # `::tpy::repeat_range<T>` binding is a plain C++ lvalue the
            # universal loop captures by reference, like a user-iterator
            # name -- it has no begin/end peephole.
            _witness("foreach.list_repeat_local")
        elif (not isinstance(u, NominalType) or u.is_protocol
                or not _user_iterator_iterable(u, analyzer)):
            return None
    else:
        return None
    et = _resolved_loop_elem_type(stmt, analyzer)
    if not _for_each_elem_binding_ok(et):
        note_detail("foreach.elem_family." + _type_family_tag(et, analyzer))
        return None
    return _ForEachRoute(route="iter_proto", elem_type=et)


def _tuple_unpack_reject_tag(stmt: TpyForEach, analyzer,
                             declared: dict[str, TpyType],
                             narrowed: AbstractSet[str]) -> str:
    """Name the blocked rung of a tuple-unpack loop head, tags only."""
    up = stmt.body[0] if stmt.body else None
    if not (isinstance(up, TpyTupleUnpack) and isinstance(up.value, TpyName)
            and up.value.name == stmt.var):
        return "tuple.head_shape"
    if any(up.is_owned):
        return "tuple.own_target"
    if any(up.is_ref) or any(up.is_const_ref):
        return "tuple.ref_target"
    if not all(up.is_new):
        return "tuple.reused_target"
    for i, name in enumerate(up.targets):
        if name is None:
            continue
        if name in declared or name in narrowed:
            return "tuple.reused_target"
        tt = unwrap_ref_type(_bindings.unpack_target_type(up, i, analyzer))
        if not _scalar_or_str_unpack_elem(tt, analyzer):
            return "tuple.target_family." + _type_family_tag(tt, analyzer)
    return "tuple.iter_shape"


def _for_each_reject_detail(stmt: TpyForEach, analyzer,
                            declared: dict[str, TpyType],
                            narrowed: AbstractSet[str]) -> str:
    """Sub-classify a rejected for-each for the reject detail (diagnostic
    only: the tag feeds `note_detail`, never route selection). The orthogonal
    pre-route flags come first -- they fail `_for_loop_shape_ok` (or the
    tuple-unpack probe's inline copy) silently, BEFORE any iterable
    classification, so a shape-derived tag would misattribute those bodies
    to their iterable's node kind. Then the iterable's node/type family,
    then the tuple-unpack target rungs. The probes' finer set-if-empty
    details (name_global / field_parent / elem_family.*) win over this
    classifier's tag at the note_detail slot."""
    if stmt.is_async:
        return "foreach.async"
    if stmt.enum_iterable is not None:
        return "foreach.enum_iterable"
    if stmt.consuming_iter_fi is not None:
        return "foreach.consuming_iter"
    if stmt.hoist_loop_var:
        return "foreach.hoist_loop_var"
    if analyzer.if_branch_decls.get(stmt):
        return "foreach.branch_decls"
    if stmt.var in declared:
        return "foreach.var_shadow"
    it = stmt.iterable
    if _is_range_call(it):
        # Flags passed, so the blocker is the range call's own shape
        # (kwargs / arg count / counter or step family).
        return "iter.range_shape"
    kind = expr_kind_tag(it).removeprefix("expr.")
    t = analyzer.get_expr_type(it)
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    if isinstance(u, OwnType):
        u = unwrap_readonly(u.wrapped)
    if u is not None:
        u = resolve_pending_container(u, analyzer) or u
        rv = _resolved_viewfam_value(u, analyzer)
        if rv is not None:
            u = rv
    if u is None or not is_native_iterable(u, analyzer.registry):
        if isinstance(it, (TpyCall, TpyMethodCall)):
            fi = it.resolved_function_info
            return ("iter.call.generator"
                    if fi is not None and fi.is_generator
                    else "iter.call.plain")
        if isinstance(u, TupleType):
            return f"iter.tuple.{kind}"
        if _user_iterator_iterable(u, analyzer):
            return f"iter.user_iterator.{kind}"
        return f"iter.{kind}_family.{_type_family_tag(u, analyzer)}"
    if stmt.is_tuple_unpack:
        return _tuple_unpack_reject_tag(stmt, analyzer, declared, narrowed)
    # Admitted iterable family with no flags: the blocker is the node
    # shape / lvalue-ness or the element binding.
    return f"iter.{kind}_shape"


def protocol_iterable_ok(lc: '_LowerCtx', it: TpyExpr) -> bool:
    """Whether a protocol-typed iterable `it` (an `Iterable[T]` param, an
    open-`T` field) may be iterated where it is read. Always in a SYNC body;
    in a resumable leaf only a bare-rendering NAME, since a suspension-free
    loop is one leaf statement rendering the sync shape inside the case block
    (the param frame field reads bare). Slot/pointer-backed names and FIELD
    iterables (receiver respell) keep the fence -- except a SELF field, whose
    member renders `__self.items` through the ordinary field lowering."""
    if not (lc.resumable_leaf_mode or lc.any_frame_slot()):
        return True
    if not lc.resumable_leaf_mode:
        return False
    if isinstance(it, TpyName):
        return not lc.binding(it.name).frame_slot and not lc.binding(it.name).pointer
    return (isinstance(it, TpyFieldAccess) and isinstance(it.obj, TpyName)
            and it.obj.name == "self")


def _select_for_each_route(
        stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str],
        pointers: 'AbstractSet[str]',
        iterator_objects: 'AbstractSet[str]' = frozenset(),
        *, protocol_param_ok: bool = False,
        tparam_bounds: 'dict | None' = None,
        shadowable_globals: 'AbstractSet[str]'
        = frozenset()) -> _ForEachRoute:
    """Select the lowering strategy or reject from the lowering boundary."""
    if isinstance(stmt.iterable, TpyName) and stmt.iterable.name in narrowed:
        # For-dispatch keys the DECLARED binding (get_resolved_type reads
        # ctx.var_types), so a narrowed-alias iterable renders the
        # universal `__iter__`/`__next__` protocol loop, never the member's
        # begin/end peephole. That IS the iter_proto route -- select it
        # rather than rejecting; the alias binds as an lvalue source
        # (`auto& __src_N = __t;`) and the name lowering spells the alias.
        # The richer for-shapes keep their own routes and stay rejected here:
        # each re-dispatches on the iterable in ways this bypass would skip.
        if (stmt.is_tuple_unpack or stmt.is_async
                or stmt.enum_iterable is not None
                or stmt.consuming_iter_fi is not None):
            note_detail("foreach.narrowed_src")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        net = _resolved_loop_elem_type(stmt, analyzer)
        if not _for_each_elem_binding_ok(net):
            note_detail("foreach.elem_family."
                        + _type_family_tag(net, analyzer))
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("foreach.narrowed_proto_src")
        route = _ForEachRoute(route="iter_proto", elem_type=net)
    elif _is_range_call(stmt.iterable):
        route = _for_range_route(stmt, analyzer, declared,
                                 shadowable_globals)
        if route is None:
            # The counter loop declined: the container route's
            # range-object leg admits the zero-literal-step slice.
            route = _for_each_container_route(stmt, analyzer, declared,
                                              pointers, shadowable_globals,
                                              tparam_bounds=tparam_bounds)
    elif stmt.enum_iterable is not None:
        route = _for_enum_route(stmt, analyzer, declared)
    else:
        route = _for_consuming_route(stmt, analyzer, declared)
        if route is None:
            route = _for_each_container_route(stmt, analyzer, declared,
                                              pointers, shadowable_globals,
                                              tparam_bounds=tparam_bounds)
        if route is None:
            route = _for_iter_proto_route(stmt, analyzer, declared,
                                          iterator_objects,
                                          protocol_param_ok=protocol_param_ok,
                                          tparam_bounds=tparam_bounds,
                                          shadowable_globals=(
                                              shadowable_globals))
    if route is None:
        note_detail(_for_each_reject_detail(stmt, analyzer, declared, narrowed))
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if route.consuming_native_name is not None:
        # The one arm whose capture is NOT about the source as written: it
        # WRAPS the source (`::tpy::own_iter(std::move(xs))`), and the
        # `iterable_lvalue=False` it set describes that wrapper -- a fresh
        # value -- not the name it was built from. Everything else takes the
        # shared verdict, so the capture spelling and the resumable frame's
        # ownership decision cannot disagree about a slice or a ternary.
        return route
    return replace(route,
                   iterable_lvalue=not for_source_is_rvalue(stmt, analyzer))


def _frame_src_slot(stmt: TpyForEach, lc: '_LowerCtx') -> 'str | None':
    """The frame field owning this loop's source.

    Inside a resumable body a name bound out of a loop ELEMENT can be a frame
    field, and then the fresh source it points into has to outlive the state
    block the loop sits in -- so the frame holds it. Which loops those are is
    the frame prescan's call, the pass that owns the frame's layout and
    places the field; this reads the verdict rather than re-deriving it. A
    loop NO pass looked at is refused here rather than silently taking the
    block-local capture, which is the dangling read the holder prevents.
    """
    if not lc.resumable_leaf_mode or not for_source_is_rvalue(stmt,
                                                              lc.analyzer):
        return None
    verdicts = rcfg.resumable_state(lc.func).for_src_fields
    if stmt not in verdicts:
        note_detail("foreach.frame_src_unplaced")
        raise ThirUnsupported("res.for_src_unplaced")
    held = verdicts.get(stmt)
    return held[0] if held is not None else None


def self_typed_frame_source(iterable: 'THIRExpr', lc: '_LowerCtx') -> 'THIRExpr':
    """A container literal at a `for` head whose loop the frame spells off
    the source expression (`decltype((...))`) names its type: a bare brace
    is not an expression, and a `frame_slot` cannot deduce one either. (A
    `str` literal there takes its own view render from the same slot,
    `SinkForm.SELF_TYPED`.)"""
    if (isinstance(iterable, THIRContainerLiteral)
            and iterable.typed_brace_cpp is None):
        return replace(iterable,
                       typed_brace_cpp=lc.render_type(iterable.result_type))
    return iterable


def _discard_target_hoist(stmt: TpyTupleUnpack, name: str,
                          lc: '_LowerCtx') -> None:
    """A fresh target whose slot stands at the body's top (its record's
    `storage_hoisted`) consumes sema's escape hoist of it."""
    rec = _bindings.fresh_record(lc.table, stmt, name)
    if rec is not None and rec.storage_hoisted:
        lc.unhandled_hoists.discard(name)


def _iteration_yields_const(it: TpyExpr, lc: '_LowerCtx', analyzer) -> bool:
    """Whether iterating `it` binds the loop var const (element pointers spell
    `const T*`) -- the SUBSET of codegen's `is_const_storage_source` this gate
    admits: whatever the one assembler `_const_borrow_name` calls const (a
    const local included: a reference alias of a const container iterates
    its const elements), reached through a bare name, a one-hop field off
    such a name, or a borrowing-view accessor call (`d.items()` /
    `d.values()` -- const tracks the RECEIVER)."""
    if (isinstance(it, TpyMethodCall)
            and isinstance(it.obj, (TpyName, TpyFieldAccess))):
        # A view accessor call's elements alias the receiver's storage, so
        # its const verdict is the receiver's. Only a borrowing-view return
        # aliases; a method minting a fresh container yields non-const.
        fi = it.resolved_function_info
        if (fi is not None
                and is_borrowing_view_type(unwrap_ref_type(fi.return_type))):
            return _iteration_yields_const(it.obj, lc, analyzer)
        return False
    if isinstance(it, TpyFieldAccess) and isinstance(it.obj, TpyName):
        # C++ propagates const through member access, so a field of a const
        # receiver yields const elements. Any name, not just `self`: a
        # one-hop field off a param (`g.rows` over `readonly[Grid]`, over
        # `Ptr[readonly[Grid]]`) is a placeable iterable the for-each route
        # admits, and answering not-const there spelled a mutable borrow out
        # of a const container.
        return _const_borrow_name(it.obj.name, lc, const_locals=True)
    if isinstance(it, TpyName):
        # The one const-borrow assembler: a loop var over a const source
        # lends const on, so an INNER loop over one yields const too
        # (`for row in rows: for h in row:`), and every other const source
        # family comes along with it.
        return _const_borrow_name(it.name, lc, const_locals=True)
    return False


def _storage_tuple_lvalue_root(value: TpyExpr, declared: dict[str, TpyType],
                               analyzer, pointers: 'AbstractSet[str]', *,
                               resident: 'AbstractSet[str] | None' = None,
                               ) -> 'str | None':
    """The ROOT name of a STORAGE-form tuple LVALUE source (`a, b = pairs[0]`
    / `= h.pair` / `= hs[0].pair` / `= self.o.h.pair`), or None when the source
    is not that shape. Its reference elements must lift through
    the per-element lend off the lvalue itself: binding one into a by-VALUE
    holder copies the reference element, so every element pointer taken off
    that holder aliases the copy instead of the container and dies with the
    holder's scope.

    Any number of field / subscript hops, but the chain has to bottom out in a
    DECLARED name and no hop may mint a temporary -- a call result or a slice
    anywhere in it would leave the lift pointing into a dying object. The
    root's own const verdict is the caller's to read (the sync body and the
    frame keep it in different homes); the chain propagates it, since C++
    const travels through member and element access.

    `resident` is the frame arm's field set: a frame alias field outlives the
    case block, so there the root must be storage the FRAME holds."""
    root = _lvalue_chain_root(value, declared, analyzer, pointers,
                              resident=resident)
    if root is None:
        return None
    vt = analyzer.get_expr_type(value)
    bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
            if vt is not None else None)
    if not (isinstance(bare, TupleType) and bare.has_pointer_repr_element()):
        return None
    return root

# The with-binding arm a fresh target's form declares.
_WITH_ARMS: dict = {
    BindingForm.VALUE: WithTargetArm.VALUE,
    BindingForm.REFERENCE: WithTargetArm.REF,
    BindingForm.POINTER: WithTargetArm.PTR_DECL,
}


def _with_target_arm(item, declared: dict[str, TpyType], lc: '_LowerCtx'
                     ) -> 'tuple[WithTargetArm, TpyType | None] | None':
    """A sync `with` item's as-target arm, or None when the item is out of
    the slice. A target the item declares binds as its record's form says:
    `auto name = __enter__()` of a value (its declared entry carries the
    RESOLVED enter type, so an owned `str` return reads STORAGE, a `StrView`
    one BORROW), `T* name = &(...)` of a reassigned record (the name joins
    the pointer set), `auto& name` of any other record. A target already in
    scope is written only when it is a pointer local that re-points
    (ASSIGN_PTR; a rebind-slot name too: the with bind aliases the manager,
    never the slot) and the declared entry is the SAME record (the first
    enter type is kept for reads); a hoisted OPTIONAL_STORAGE slot is out
    (the `&`-assign would be ill-formed against it). Every other
    already-declared reuse -- a value target -- is the BUGS.md ill-formed
    `x = &(__enter__())` family: unsupported."""
    if item.target is None:
        return WithTargetArm.NONE, None
    et = item.enter_type
    if not isinstance(et, TpyType):
        return None
    resolved = unwrap_readonly(unwrap_send_sync(et))
    fresh = _bindings.fresh_record(lc.table, item, item.target)
    if fresh is None:
        rec = lc.binding(item.target)
        same_record = (
            not et.is_value_type()
            and record_like(resolved, lc.analyzer)
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared[item.target]))) == resolved)
        if rec.pointer and same_record and not rec.optional_storage:
            return WithTargetArm.ASSIGN_PTR, resolved
        return None
    arm = _WITH_ARMS.get(fresh.form)
    return (arm, resolved) if arm is not None else None


def _const_pointee(rec: BindingRepr, pointee_cpp: str) -> str:
    """A pointer record's pointee spelling, const as its slot
    (`_plan_const`)."""
    return "const " + pointee_cpp if rec.const else pointee_cpp


def _with_ptr_target_cpp(name: str, et: TpyType, lc: '_LowerCtx') -> str:
    """The pointee a with target's `T* name = &(...)` declares."""
    return _const_pointee(lc.declaring(name), lc.render_type(
        unwrap_readonly(unwrap_send_sync(et))))


def _frame_hoist_type(name: str, raw: TpyType, lc: '_LowerCtx') -> TpyType:
    """The type a CLAUSE's frame-declared hoist registers: the frame's own
    slot type, which carries the branch-decl (Array-off) override the
    clause's `raw` does not. `raw` stands in for a name with no slot of its
    own -- the gate above lets none through, so it is only the default that
    keeps this total."""
    return lc.frame_local_types.get(name, raw)


def _register_frame_hoist(name: str, raw: TpyType,
                          declared: dict[str, TpyType],
                          lc: '_LowerCtx') -> TpyType:
    """Enter a frame-declared name into the lowering scope WITHOUT a decl
    line: the CALLER's type, normalized exactly as the resumable pass-1
    registration normalizes it, which the caller gets back. A clause
    resolves the frame's slot type through `_frame_hoist_type` first;
    pass 1 keeps its first-decl-keyed type."""
    vtype = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(raw)))
    declared[name] = vtype
    _witness("hoist.frame_declared")
    return vtype


@dataclass(frozen=True)
class _Hoist:
    """One statement's predecl pass: the statement, its ladder (which names
    its witnesses and reject tags: 'if' 'while' 'foreach' 'try' 'with'
    'match'), and what its node collects -- the predecls' model for the
    THIRIf / THIRWhile / THIRForEach node, and the chain head's rebind
    slots for THIRIf."""
    stmt: TpyStmt
    ladder: str
    in_branch: bool = False
    bindings: 'list[THIRHoistedBinding] | None' = None
    slots: 'list[BindingRepr] | None' = None


def _hoist_record(stmt: TpyStmt, name: str, lc: '_LowerCtx') -> BindingRepr:
    """The record `stmt` predeclares `name` with: the statement's own hoist
    record, or the resumable frame member the name is -- the frame layout
    declares a resumable body's locals, so a predecl would shadow the member
    for the rest of the state block (only a frame member holds a frame
    slot). Any other binding is a planner defect."""
    rec = lc.binding(name)
    if rec.site_kind == "frame":
        return rec
    if rec.site is not stmt:
        raise BindingTableError(
            f"no hoist record for {name!r} at {type(stmt).__name__} in "
            f"{lc.func.name}")
    return rec


def _hoist_type(rec: BindingRepr, analyzer) -> TpyType:
    """The type a hoist predecls: an un-annotated container hoist (`xs =
    [1]` / `xs = [2]`) carries the pending literal type, resolved so the
    registered binding and the render see the final container."""
    return resolve_pending_container(rec.type, analyzer) or rec.type


def _opt_btuple_hoist_ok(h: _Hoist, name: str, rec: BindingRepr,
                         lc: '_LowerCtx') -> bool:
    """Storage-lvalue / None sources at the chain head's own scope only --
    an owning-call source would need a hoisted rebind slot (unwitnessed);
    const bindings keep rejecting."""
    srcs = _borrow_tuple_binding_sources(name, lc) or ()
    return (not h.in_branch and not rec.const and bool(srcs)
            and all(isinstance(s, TpyNoneLiteral)
                    or (isinstance(s, (TpyFieldAccess, TpySubscript))
                        and _borrow_tuple_source_ok(s, lc))
                    for s in srcs))


def _btuple_hoist_ok(h: _Hoist, name: str, rec: BindingRepr,
                     lc: '_LowerCtx') -> bool:
    return _borrow_tuple_hoist_ok(name, _hoist_type(rec, lc.analyzer), lc,
                                  rec)


def _never(h: _Hoist, name: str, rec: BindingRepr, lc: '_LowerCtx') -> bool:
    return False


# A hoist form's precondition at its statement, and the if chain's tag when
# it fails (every other ladder rejects with its one tag); a hoist the
# planner gives no C++ form never passes.
_HOIST_ADMIT: dict = {
    BindingForm.OPT_BORROW_TUPLE: (_opt_btuple_hoist_ok, "if.hoist_type"),
    BindingForm.BORROW_TUPLE: (_btuple_hoist_ok, "if.hoist_type"),
    BindingForm.NONE: (_never, "if.hoist_type"),
}
_HOIST_REJECT_TAG = {"while": "while.hoist_type",
                     "foreach": "foreach.hoist_type", "try": "try.hoist",
                     "with": "with.hoist"}


def _admit_hoist(h: _Hoist, name: str, rec: BindingRepr,
                 lc: '_LowerCtx') -> None:
    """Reject a hoist its statement cannot predeclare."""
    check, if_tag = (_HOIST_ADMIT.get(rec.form, (None, None))
                     if rec.site_kind == "hoist" else (None, None))
    if check is None or check(h, name, rec, lc):
        return
    if h.ladder != "if":
        tag = _HOIST_REJECT_TAG[h.ladder]
    else:
        tag = if_tag
    note_detail(tag)
    raise ThirUnsupported(stmt_reject_reason(h.stmt))


def _admit_hoists(h: _Hoist, hoists: dict, declared: dict[str, TpyType],
                  lc: '_LowerCtx') -> None:
    """Admit every name a statement predeclares before any predecl lowers
    (the statement's other gates sit between the two passes)."""
    for name in hoists:
        if name not in declared:
            _admit_hoist(h, name, _hoist_record(h.stmt, name, lc), lc)


def _build_hoist(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                 declared: dict[str, TpyType], lc: '_LowerCtx'
                 ) -> 'HoistDecl | None':
    """The predecl of an admitted hoist, by its record's form."""
    builder = _hoist_builder(rec)
    if builder is None:
        raise BindingTableError(
            f"no hoist predecl for {name!r} of form {rec.label}")
    return builder(h, name, raw, rec, declared, lc)


def _lower_hoist_predecls(h: _Hoist, hoists: dict,
                          declared: dict[str, TpyType], lc: '_LowerCtx',
                          witness_tag: str) -> list[HoistDecl]:
    """Chain-head predecls for the branch-first decls of a try / with /
    for-each / while, each by its record (`_HOIST_BUILDERS`), admitted
    first (`_admit_hoists`). A name enters the CALLER's `declared` at
    function scope; one already declared (bound outside an enclosing loop)
    needs no predecl."""
    hoist_decls: list[HoistDecl] = []
    for name, raw in hoists.items():
        # This predecl IS the sema hoist's declaration, so it settles the
        # residue ledger for the name -- including one already declared
        # further out, which needs no second declaration. Drained here, at
        # the shared emitter, rather than only in the callers that remember
        # to: the loop ladders did not, and rejected bodies whose hoist they
        # had in fact emitted.
        lc.unhandled_hoists.discard(name)
        if name in declared:
            continue
        decl = _build_hoist(h, name, raw, _hoist_record(h.stmt, name, lc),
                            declared, lc)
        if decl is not None:
            hoist_decls.append(decl)
    if hoist_decls:
        _witness(witness_tag)
    return hoist_decls


def _hoist_frame_member(h: _Hoist, name: str, raw: TpyType,
                        rec: BindingRepr, declared: dict[str, TpyType],
                        lc: '_LowerCtx') -> None:
    _register_frame_hoist(name, _frame_hoist_type(name, raw, lc), declared,
                          lc)
    return None


def _hoist_value(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                 declared: dict[str, TpyType], lc: '_LowerCtx') -> HoistDecl:
    if h.ladder == "if":
        _witness("if.hoist_decl")
    return _value_hoist_entry(rec, _hoist_type(rec, lc.analyzer), declared,
                              lc, h.bindings)


def _hoist_opt_storage(h: _Hoist, name: str, raw: TpyType,
                       rec: BindingRepr, declared: dict[str, TpyType],
                       lc: '_LowerCtx') -> HoistDecl:
    # A single-bind rvalue local: plain assigns engage the optional, reads
    # deref through it.
    _witness(f"{h.ladder if h.ladder in ('if', 'match') else 'with'}"
             ".hoist_optional_storage")
    return _optional_storage_hoist_entry(name, _hoist_type(rec, lc.analyzer),
                                         declared, lc, h.bindings)


def _hoist_pointer(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                   declared: dict[str, TpyType], lc: '_LowerCtx'
                   ) -> HoistDecl:
    """The pointer local (`T* name;`) of a reassigned or borrow-only hoist,
    `const T*` for a const one; a pointer-repr Optional is the same
    nullable pointer over its inner, `declared` keeping the Optional so
    reads narrow on it (unproven reads draw their None check). An rvalue
    reseat of one with a rebind slot (an if chain's or a match's
    rvalue-reassigned pointer) fills that slot; the others allocate one at
    function top (BRANCH_RVALUE). A match's borrow-only pointer re-seats
    ride the hoisted-record arms."""
    vtype = _hoist_type(rec, lc.analyzer)
    vopt = pointer_repr_optional(vtype)
    pointee = unwrap_readonly(vtype if vopt is None else vopt.inner)
    _record_hoisted_binding(h.bindings, rec, vtype, lc)
    declared[name] = vtype
    if h.ladder == "match":
        _witness("match.hoist_opt_ptr_local" if vopt is not None
                 else "match.hoist_ptr_slot" if rec.rebind_slot
                 else "match.hoist_ptr_local")
        if rec.const:
            _witness("match.hoist_ptr_const")
    else:
        head = ('if' if h.ladder == 'if' else 'try' if rec.const
                else 'with')
        _witness(f"{head}.hoist_{'const_ptr' if rec.const else 'ptr_local'}")
    return HoistDecl(name, _const_pointee(rec, lc.render_type(pointee)) + "*")


def _hoist_ptr_null(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                    declared: dict[str, TpyType], lc: '_LowerCtx'
                    ) -> HoistDecl:
    """The hoisted container unpack target of a for head: a null pointer the
    head re-points per iteration (its pointer record: post-loop reads
    deref)."""
    vtype = _hoist_type(rec, lc.analyzer)
    declared[name] = vtype
    _witness("foreach.hoist_ptr_null")
    return HoistDecl(name, f"{lc.render_type(vtype)}*", " = nullptr")


def _hoist_btuple_loop(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                       declared: dict[str, TpyType], lc: '_LowerCtx'
                       ) -> HoistDecl:
    """A hoisted borrow-tuple loop variable (`std::tuple<..., T*> name;`):
    the head lends it element by element per iteration;
    reads key on the declared TupleType like a branch-bound one's."""
    vtype = rec.ptr_tuple or _hoist_type(rec, lc.analyzer)
    declared[name] = vtype
    cpp = vtype.to_cpp_return()
    _witness("foreach.hoist_borrow_tuple")
    return HoistDecl(name, cpp, emit_prims.placeholder_init(vtype, cpp))


def _hoist_btuple_branch(h: _Hoist, name: str, raw: TpyType,
                         rec: BindingRepr, declared: dict[str, TpyType],
                         lc: '_LowerCtx') -> HoistDecl:
    _witness(f"{h.ladder}.hoist_borrow_tuple")
    return _borrow_tuple_hoist_entry(
        rec, _hoist_type(rec, lc.analyzer), declared, lc,
        f"{h.ladder}.hoist_mixed_own_tuple", h.slots, h.bindings)


def _hoist_opt_btuple(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
                      declared: dict[str, TpyType], lc: '_LowerCtx'
                      ) -> HoistDecl:
    """The nullable borrow-tuple hoist (`std::optional<std::tuple<..,
    T*>> t;`): the branch decls become optional-wrapped reseats."""
    vtype = _hoist_type(rec, lc.analyzer)
    obt = _optional_borrow_tuple(vtype, lc.analyzer)
    declared[name] = vtype
    _witness("if.hoist_opt_btuple")
    return HoistDecl(name, f"std::optional<{obt.to_cpp_return()}>")


def _hoist_dyn(h: _Hoist, name: str, raw: TpyType, rec: BindingRepr,
               declared: dict[str, TpyType], lc: '_LowerCtx') -> HoistDecl:
    """`Base* name;`; each reseat allocates its adapter slot (the
    DYN_PROTOCOL rebind arm)."""
    vtype = _hoist_type(rec, lc.analyzer)
    declared[name] = vtype
    _witness("if.hoist_dyn_protocol")
    return HoistDecl(name, f"{dynamic_base_name(vtype, lc.analyzer)}*")


_HoistBuilder = Callable[[_Hoist, str, TpyType, BindingRepr,
                          dict[str, TpyType], '_LowerCtx'],
                         'HoistDecl | None']
_HOIST_BUILDERS: dict[BindingForm, _HoistBuilder] = {
    BindingForm.VALUE: _hoist_value,
    BindingForm.OWNED_SLOT: _hoist_opt_storage,
    BindingForm.POINTER: _hoist_pointer,
    BindingForm.OPT_BORROW_TUPLE: _hoist_opt_btuple,
    BindingForm.DYN: _hoist_dyn,
}


def _hoist_builder(rec: BindingRepr) -> '_HoistBuilder | None':
    """The predecl builder of a hoist's record: a resumable frame member's,
    else by the hoist's form -- the for head's re-pointed null pointer, and
    the borrow tuple a loop variable or a branch binds, apart."""
    if rec.site_kind == "frame":
        return _hoist_frame_member
    if rec.site_kind != "hoist":
        return None
    if rec.head_target:
        return _hoist_ptr_null
    if rec.form is BindingForm.BORROW_TUPLE:
        return (_hoist_btuple_loop if isinstance(rec.site, TpyForEach)
                else _hoist_btuple_branch)
    return _HOIST_BUILDERS.get(rec.form)


def _frame_slot_roots_ok(name: str, lc: '_LowerCtx',
                         declared: 'AbstractSet[str] | None' = None) -> bool:
    """Whether storage for the frame-holding local `name` placed NOW is
    younger than everything its frame borrows (sema's
    `frame_local_roots`): a parameter the body never rebinds, the receiver,
    a module global -- or, for a slot declared at a block head, a name in
    `declared` there. A resumable frame orders its own fields by what they
    borrow, so it answers yes."""
    if lc.func.is_generator or lc.func.is_async:
        return True
    fact = lc.analyzer.function_frame_local_roots.get(lc.func, {}).get(name)
    if fact is None:
        return False
    for root in fact[0]:
        if root is None:
            return False
        if declared is not None and root in declared:
            continue
        if root == "self" and lc.self_receiver == "self":
            continue
        if root in lc.prescan.param_names:
            if root in lc.prescan.reassigned:
                return False
            continue
        if root in lc.prescan.bound_names:
            return False
    return True


def _rebind_rvalue_source_ok(init: TpyExpr, target_t: TpyType,
                             analyzer, *, frame_roots_ok: bool) -> bool:
    """The rvalue shapes a slot rebind admits (`__slot_N = <init>`): a
    shape-checked container literal or comprehension, an F1-record rvalue,
    or a container-returning by-value/Own call (the storage-call family --
    the rebind render is source-shape-blind past is_rvalue_source; these
    are the vetted slices of it). Every sink that admits through here
    lowers the source through `_lower_storage_value`."""
    if isinstance(init, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        return _container_literal_shape_ok(init, target_t, analyzer)
    if _container_comp_arg(init, target_t):
        return True
    if frame_object_slot(target_t):
        # The frame filling the slot may borrow only what is older than the
        # slot (`_frame_slot_roots_ok`, asked by the caller, which knows
        # where the slot stands).
        if not _frame_factory_source(init, target_t, analyzer):
            return False
        if not frame_roots_ok:
            note_detail("decl.frame_slot_roots")
            return False
        return True
    if _record_rvalue_source_shape(init, analyzer):
        return True
    # A fresh operator result or all-fresh select of the slot's type
    # (`v = v + inc`, `x = C(1) if c else make()`).
    if _rvalue_ref_init(init, target_t, analyzer):
        return True
    # `copy(x)` of a bare plain F1-record name: the copy-construct rvalue
    # (`__slot_N = Point(p)` / the engaging optional assign). The reseat
    # render intercepts the row itself (the special-builtin call gate
    # rejects copy() in the generic tail); a source outside the row's
    # slice raises there, keeping the fail-closed contract.
    if copy_construct_source(init, analyzer, frozenset()) is not None:
        return True
    # An rvalue F1-record METHOD call (`cur = nxt.clone()` ->
    # `cur = &*(__slot_N = nxt->clone());`): the reseat twin of the decl's
    # method-rvalue REBIND_SLOT admission, same shared disjunct.
    if _method_rvalue_record_like(init, analyzer):
        return True
    if (isinstance(init, (TpyCall, TpyMethodCall))
            and is_rvalue_source(analyzer, init)):
        rt = analyzer.get_expr_type(init)
        # A bare OPEN `T` result at the same bare `T` slot inside a template:
        # neither side has a form yet, so the C++ traits pick the same one for
        # both per instantiation and the plain assign engages the hoist. There
        # is no storage FAMILY to classify -- `_storage_call_ret` answers None
        # for a type-param result -- so the identity of the two slots is the
        # whole admission. Bare on both sides: a `readonly` / `Ref` decoration
        # spells its own C++ shape, which reopens the question this answers.
        if (_type_param_value_slot(rt)
                and _type_param_value_slot(target_t) and rt == target_t):
            return True
        fam = _storage_call_ret(rt, analyzer)
        if fam is None:
            return False
        if _storage_call_container(fam):
            return True
        # ... and an F1-record / wrapper-union by-value result at a slot of
        # its OWN type (`v2 = json.load(f)` into a `std::optional<JsonValue>`
        # hoist): the result engages the slot plainly, no lift. The
        # type-equality guard is what keeps a differently-shaped storage
        # result (which would need a conversion the plain assign omits) out.
        tt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target_t)))
        if isinstance(tt, OptionalType):
            tt = unwrap_readonly(tt.inner)
        return (fam == tt
                and (record_like(fam, analyzer)
                     or _wrapper_union_like(fam, analyzer) is not None))
    return False


def _closed_frame_first_decl(stmt: TpyVarDecl,
                             scope: '_LowerScope') -> THIRStmt:
    """The first declaration of a plain function's generator local that a
    `del` closes (sema's `function_closed_frames`): the empty optional,
    then the binding engaging it in place, so the close can end the frame
    where it stands."""
    lc, declared = scope.lc, scope.declared
    vtype = unwrap_ref_type(_var_decl_type(stmt, lc.analyzer))
    hoist = _optional_storage_hoist_entry(stmt.name, vtype, declared, lc)
    _witness("decl.closed_frame_optional")
    bind = _lower_stmt_dispatch(stmt, scope)
    return THIRStmtSeq(stmts=(
        THIRVarDecl(name=hoist.name, resolved_type=vtype, init=None,
                    cpp_type=hoist.cpp_type,
                    loc=getattr(stmt, "loc", None)),
        bind))


def _rebind_rvalue_use(init: TpyExpr, slot_t: TpyType) -> '_ExprUse':
    """A slot assign is a storage sink at statement position: a
    container-returning call needs the STORAGE result use (the storage-decl
    sink's admission) and may hoist arg temps. Only a fresh value reaches a
    slot, so a select there is all-fresh and renders its prvalue `?:`."""
    use = (_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True)
           if isinstance(init, (TpyCall, TpyMethodCall)) else _ExprUse())
    return replace(use, pos=SinkPos.LOCAL_DECL, slot_target=slot_t,
                   forms=_ONLY_SELECT_FRESH_PRVALUE)


def _rebind_storage(stmt: 'TpyVarDecl | TpyAssign', name: str,
                    lc: '_LowerCtx') -> RebindStorage:
    """Sema's alias-rebind verdict for this rvalue reseat. A site sema did
    not stamp, or a const-bound pointer (no non-const in-place write),
    takes storage of its own -- the conservative side. (A generator is
    never rebound while a `for` over the name runs: sema refuses it, since
    the verdict does not count that loop as a holder.)"""
    if (stmt.rebind_storage is RebindStorage.IN_PLACE
            and not lc.binding(name).const):
        return RebindStorage.IN_PLACE
    return RebindStorage.OWN


def _lower_rebind_ptr_frame_write(stmt: TpyVarDecl, lc: '_LowerCtx',
                                  declared: dict[str, TpyType]) -> THIRStmt:
    """An rvalue write to a REBIND_PTR frame local -- the frame twin of
    the sync rebind-slot pointer-local reseat. OWN (and the first write,
    which sema does not stamp) materializes the object in the site's
    prescanned frame field and re-points the pointer; IN_PLACE assigns
    through the pointer."""
    loc = getattr(stmt, "loc", None)
    vtype = declared[stmt.name]
    reason = stmt_reject_reason(stmt)
    storage = _rebind_storage(stmt, stmt.name, lc)
    value = rebind_ptr_frame_value(stmt.init, vtype, lc, declared, loc,
                                   reason, storage=storage)
    target = THIRName(result_type=vtype, name=stmt.name, loc=loc)
    if storage is RebindStorage.IN_PLACE:
        _witness("res.rebind_ptr_in_place")
        return THIRAssign(target=target, value=value,
                          rebind_storage=RebindStorage.IN_PLACE,
                          slot_cpp=_rebind_slot_cpp(vtype, lc),
                          rebuild=frame_object_slot(vtype), loc=loc)
    fld = rebind_ptr_frame_field(lc, stmt, reason)
    _witness("res.rebind_ptr_own")
    return THIRPtrLocalRebind(
        name=stmt.name, kind=PtrSlotKind.FRAME_RVALUE, value=value,
        val_cpp=fld, rebind_storage=RebindStorage.OWN,
        rebuild=frame_object_slot(vtype), loc=loc)


def _rebind_slot_target(target_t: TpyType, analyzer) -> TpyType:
    """The type an OWN reseat slot HOLDS: a pointer-repr Optional's
    pointee (the slot is `std::optional<T>`, never
    optional<optional<T>>), else the local's own type. The one home for
    that question -- the slot spelling, the source admission and the
    source's own lowering all read it, so a literal's element types
    resolve against the same slot the assign writes."""
    slot_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target_t)))
    slot_opt = pointer_repr_optional(slot_t)
    if slot_opt is not None:
        slot_t = unwrap_readonly(slot_opt.inner)
    return resolve_pending_container(slot_t, analyzer) or slot_t


def _rebind_slot_cpp(target_t: TpyType, lc: '_LowerCtx') -> str:
    """The pointee spelling an OWN reseat slot holds."""
    return lc.render_type(_rebind_slot_target(target_t, lc.analyzer))


def _lower_opt_reseat_literal(stmt: TpyVarDecl, lc: '_LowerCtx',
                              declared: dict[str, TpyType]
                              ) -> 'tuple[THIRExpr, TpyType]':
    """The container LITERAL a pointer-repr Optional reseat writes into its
    slot, with the slot's pointee type.

    Shared by the sync `reseat.opt_container_literal` arm and its resumable
    frame twin: both fill the same slot, so the literal's element types
    resolve against `_rebind_slot_target` in both -- pending-literal
    resolution is one of the two decisions the compiler owns. An Array
    literal additionally carries the slot's typed brace spelling (CTAD
    cannot deduce the prefix); if the render is not a container literal the
    prefix has nowhere to live, so the shape rejects rather than let a
    `replace` TypeError escape."""
    lit_pointee = _rebind_slot_target(declared[stmt.name], lc.analyzer)
    if not _container_literal_shape_ok(stmt.init, lit_pointee, lc.analyzer):
        note_detail("decl.opt_reseat_source")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    lit = _lower_expr(stmt.init, lc, declared,
                      use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                   allow_temps=True,
                                   slot_target=lit_pointee))
    if isinstance(stmt.init, TpyArrayLiteral):
        if not isinstance(lit, THIRContainerLiteral):
            note_detail("decl.opt_reseat_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        lit = _spell_brace(lit, lit_pointee, lc)
    return lit, lit_pointee


_LOCAL_MOVE_USE = _ExprUse(result=_ExprResultUse.BORROW_BIND,
                           pos=SinkPos.LOCAL_DECL, forms=_ONLY_INDIRECT_READ)


def _lower_storage_value(init: TpyExpr, slot_t: TpyType, lc: '_LowerCtx',
                          declared: dict[str, TpyType], loc, *,
                          use: '_ExprUse', move: bool = False) -> THIRExpr:
    """Lower an admitted operation with the caller's source-read policy."""
    if move:
        # Sema already chose the consume; read the payload without consuming twice.
        source = _lower_expr(init, lc, declared, use=use)
        return THIRMove(result_type=source.result_type, value=source,
                        form=source.form, loc=loc)
    # Generic expression lowering has no comprehension arm and rejects copy().
    if type(init) in _comprehensions._COMP_KINDS:
        _witness("reseat.rvalue_comp")
        return _comprehensions._lower_comprehension(
            init, slot_t, lc, declared,
            _comp_shadow_pointers(lc.names_with("pointer"), declared, lc.analyzer))
    # This adapter keeps the payload type; sinks needing a declared result
    # type pass slot_type to the same copy helper.
    value = _lower_copy_record(init, lc, declared, loc=loc)
    if value is None:
        value = _lower_expr(init, lc, declared, use=use)
    return value


def _slot_fill_value(value: THIRExpr, slot_t: TpyType,
                     storage: 'RebindStorage | None',
                     lc: '_LowerCtx') -> THIRExpr:
    """`value` as a rebind writes it: into the `std::optional` slot of its
    own unless it writes in place. A bare brace spells the slot type there,
    as a field write into an optional does: `opt = {...}` hands the brace
    to optional's converting assignment, where clang flags a nested
    std::array's elided braces."""
    if storage is RebindStorage.IN_PLACE:
        return value
    return _spell_brace(value, slot_t, lc)


def _move_through_source(stmt: TpyVarDecl, typ: TpyType,
                           lc: '_LowerCtx') -> bool:
    return (stmt.name in lc.prescan.move_through
            and isinstance(stmt.init, TpyName)
            and record_like(typ, lc.analyzer))


def _owned_local_decl(stmt: TpyVarDecl, vtype: TpyType, lc: '_LowerCtx',
                       declared: dict[str, TpyType], *,
                       value: THIRExpr | None = None) -> THIRVarDecl:
    declared[stmt.name] = vtype
    # Value records retain value-copy behavior even if sema proved ownership.
    if value is None:
        value = _lower_expr(stmt.init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                         pos=SinkPos.LOCAL_DECL, forms=_NO_FORMS,
                                         allow_temps=True))
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=value,
        owned_storage=borrowed_record(vtype, False, lc.analyzer),
        cpp_type=lc.render_type(vtype), form=Form.STORAGE, loc=stmt.loc)


def _record_hoisted_binding(bindings: list[THIRHoistedBinding] | None,
                            rec: BindingRepr, typ: TpyType,
                            lc: '_LowerCtx') -> None:
    """The node's model of one hoisted predecl, from its record: a pointer
    or a branch-bound borrow tuple borrows, const where the record is (a
    branch-bound borrow tuple by the whole-body const fixpoint)."""
    if bindings is None:
        return
    btuple = (rec.site_kind == "hoist" and rec.form is BindingForm.BORROW_TUPLE
              and not isinstance(rec.site, TpyForEach))
    fact = hoisted_binding(
        rec.name, typ, lc.analyzer, borrow=rec.pointer or btuple,
        readonly=rec.const)
    if fact is not None:
        bindings.append(fact)


def _value_hoist_entry(rec: BindingRepr, vtype: TpyType,
                       declared: dict[str, TpyType], lc: '_LowerCtx',
                       bindings: list[THIRHoistedBinding] | None = None
                       ) -> HoistDecl:
    """One value-family hoist predecl entry: resolve the str/bytes view
    spelling, render, and register the raw binding type in the caller's
    `declared`."""
    render_src = _value_slot_render_type(vtype, lc.analyzer)
    declared[rec.name] = vtype
    _record_hoisted_binding(bindings, rec, vtype, lc)
    cpp = lc.render_type(render_src)
    return HoistDecl(rec.name, cpp, emit_prims.placeholder_init(vtype, cpp))


def _value_slot_render_type(vtype: TpyType, analyzer) -> TpyType:
    """The type a default-constructed VALUE slot spells: the resolved
    str/bytes view for the view families, the binding type otherwise."""
    return (_resolved_str_value(vtype, analyzer)
            or _resolved_bytes_value(vtype, analyzer)
            or vtype)


def _borrow_tuple_binding_sources(name: str, lc: '_LowerCtx'
                                  ) -> 'list[TpyExpr] | None':
    """Every binding source of `name` in the function body
    (`binding_source_sites`), or None when a walrus binds it other than off
    an owning call (uncollected sources would make the const / route
    decision unsound). An OWNING-call walrus value is an ordinary source:
    the emplace reseat, owned slot storage with no const bit to
    contribute."""
    sources: list[TpyExpr] = []
    for tgt, src, _stmt, is_walrus, _b in binding_source_sites(lc.func.body):
        if tgt != name:
            continue
        if is_walrus and not _btuple_owning_call_init(src, lc.analyzer):
            return None
        sources.append(src)
    return sources


def _borrow_tuple_call_matches(init: TpyExpr, bt_t: 'TupleType',
                               analyzer) -> bool:
    """A CALL returning the plain borrow tuple the name holds (`-> tuple[
    Box, Box]`, no `Own` element, not readonly): its result is the pointer
    tuple itself, so a reseat assigns it bare."""
    fi = getattr(init, "resolved_function_info", None)
    if fi is None or call_returns_cpp_ref(analyzer, fi):
        return False
    rt = getattr(fi, "return_type", None)
    if not isinstance(rt, TpyType) or isinstance(rt, ReadonlyType):
        return False
    rtu = unwrap_ref_type(unwrap_send_sync(rt))
    return (isinstance(rtu, TupleType) and rtu.has_pointer_repr_element()
            and not rtu.has_own_element()
            and unwrap_readonly(rtu) == unwrap_readonly(bt_t))


def _value_capture_needs_storage(et: 'TpyType') -> bool:
    """A VALUE-captured tuple element of this type is an object with no
    storage of its own at a borrow-form tuple, which holds it as a pointer:
    a module-level write parks it, a local rebind refuses it."""
    et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
    return not et.is_value_type() and TupleType._element_is_pointer_repr(et)


def _reject_value_elem_at_borrow_tuple(stmt: TpyStmt, name: str,
                                       lit: TpyTupleLiteral,
                                       bt: 'TupleType') -> None:
    """A borrow-form tuple local that is not the pointer tuple (bound once,
    in a block a hoist predeclares) holds a pointer per reference-type
    element, so a VALUE-captured element there (a fresh object, a copy of a
    call result) has no storage to point at: refuse it in words
    (BUGS.md#try-body-tuple-bound-once-rejects)."""
    for i, cap in enumerate(lit.elem_capture or ()):
        if (cap is not TupleElemCapture.VALUE or i >= len(bt.element_types)
                or i >= len(lit.elements)):
            continue
        et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            bt.element_types[i])))
        if not _value_capture_needs_storage(et):
            continue
        elem = lit.elements[i]
        loc = getattr(elem, "loc", None) or lit.loc
        note_detail("btuple.value_element")
        raise ThirUnsupported(
            stmt_reject_reason(stmt),
            message=(f"tuple variable '{name}' refers to its '{et}' elements "
                     f"instead of holding them, and element {i} here would "
                     f"have to be held by the tuple; this is not supported "
                     f"yet -- use a separate tuple variable for this "
                     f"assignment"),
            loc=loc)


def _reject_inline_repoint(stmt: TpyStmt, name: str, src: str) -> None:
    """Refuse re-pointing a pointer-holding tuple at a tuple that holds an
    element inline: nothing proves the source's storage outlives the name
    (TODO.md "Storage placement from loans (scalar and tuple)")."""
    note_detail("reseat.borrow_tuple_inline_source")
    raise ThirUnsupported(
        stmt_reject_reason(stmt),
        message=(f"'{src}' holds an element of its own, so re-pointing "
                 f"'{name}' at it would leave '{name}' pointing into "
                 f"'{src}', which is not yet supported here; bind the new "
                 f"value to a name of its own"),
        loc=getattr(stmt, "loc", None))


def _reject_ptr_tuple_select(stmt: TpyStmt, name: str) -> None:
    """Refuse a reassigned pointer tuple's binding to a conditional
    expression in words: no binding row spells the select, while an `if`
    statement binding each arm re-points by name."""
    init = getattr(stmt, "init", None)
    if not isinstance(_peel_tpy_coerce(init), TpyIfExpr):
        return
    note_detail("ptr_tuple.select_source")
    raise ThirUnsupported(
        stmt_reject_reason(stmt),
        message=(f"binding '{name}', which holds the elements of the "
                 f"tuples it is bound to, to a conditional expression is "
                 f"not yet supported here; bind '{name}' in an if "
                 f"statement instead, one branch per value"),
        loc=getattr(stmt, "loc", None))


def _holds_elements_inline(name: str, lc: '_LowerCtx') -> bool:
    """A tuple local that holds a reference element by value -- a mixed
    call's render or a literal-bound local's inline element -- where the
    pointer tuple holds a pointer."""
    rec = lc.binding(name)
    return rec.borrow_tuple_mixed or any(rec.inline_elems or ())


def _written_ptr_tuple(name: str, lc: '_LowerCtx') -> 'TupleType | None':
    """The pointer tuple the statement being lowered writes `name` as: the
    record it reseats, else the one its own site declares."""
    key, node = lc.cursor
    rec = node.lookup(name)
    if rec is None and key is not None:
        rec = lc.table.declared_by(key, name)
    return rec.ptr_tuple if rec is not None else None


def _tuple_element_root(e: TpyExpr, analyzer) -> 'str | None':
    """The tuple name a place reads an element of (`q[1]`, `q[0].child`),
    else None."""
    root, hops = place_hops(e)
    if not (hops and isinstance(hops[-1], TpySubscript)
            and isinstance(root, TpyName)):
        return None
    ot = analyzer.get_expr_type(root)
    if ot is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_own(ot))), TupleType):
        return root.name
    return None


def _reject_ptr_tuple_literal_element(stmt: TpyStmt, name: str,
                                      lit: TpyTupleLiteral, ptt: 'TupleType',
                                      analyzer) -> None:
    """Refuse a literal element the pointer tuple has no binding route for
    yet: an element of a tuple (`p = (p[1], p[0])`, `p = (Box(9), q[1])`), a
    reference into other storage at a position the binding owns (it would be
    copied where CPython shares it), a call result that is no object of its
    own, or an expression no route models. A fresh object, a name, a value
    and -- at a borrowed position -- a place whose loan the binding records
    are its routes (TODO.md "A tuple element is a name")."""
    caps = lit.elem_capture or ()
    for i, raw in enumerate(lit.elements):
        if i >= len(ptt.element_types):
            break
        et = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            unwrap_own(ptt.element_types[i]))))
        if et.is_value_type():
            continue
        el = _peel_tpy_coerce(raw)
        owned_pos = i < len(caps) and caps[i] is TupleElemCapture.VALUE
        what = None
        src = _tuple_element_root(el, analyzer)
        if src is not None:
            what = (f"an element of '{name}' itself" if src == name
                    else f"an element of the tuple '{src}'")
        elif isinstance(el, TpyName):
            continue
        elif isinstance(el, (TpyCall, TpyMethodCall)):
            if binds_owned_value(analyzer, el):
                continue
            what = "a call result that is not an object of its own"
        elif isinstance(el, (TpySubscript, TpyFieldAccess)):
            if not owned_pos:
                continue
            what = "a reference into storage held elsewhere"
        else:
            what = "an expression whose storage is not known here"
        note_detail("ptr_tuple.literal_element")
        raise ThirUnsupported(
            stmt_reject_reason(stmt),
            message=(f"element {i} of the tuple bound to '{name}' here is "
                     f"{what}; storing it in '{name}', which holds an "
                     f"element of its own, is not yet supported here; bind "
                     f"it to a name of its own first and use that name"),
            loc=getattr(raw, "loc", None) or lit.loc)


def _lower_ptr_tuple_binding(stmt: TpyVarDecl, ptt: 'TupleType',
                             is_reassign: bool, lc: '_LowerCtx',
                             declared: dict[str, TpyType],
                             loc, *, binding: 'TpyStmt | None' = None,
                             module: bool = False,
                             frame: bool = False) -> 'THIRStmt | None':
    """A binding of a rebound pointer-tuple local (`ptr_tuple_form`) that
    owns an element: the whole right-hand side is evaluated into the unpack
    holder, then each owned element lands in the scalar reference local's
    storage for this site -- the first declaration's own slot, else sema's
    per-element verdict (`tuple_elem_storage`) -- and every other element
    re-points or copies. None for a binding that owns nothing: the
    pointer-tuple reseat and declaration arms take it.

    `module`: a module-level write of a tuple global, which is declared at
    namespace scope; its owned elements take `static` slots, and a write in
    a `for` body takes a slot hoisted to the module init, as the scalar
    global's does. `binding` is the statement sema stamped, when `stmt`
    stands in for it.

    `frame`: a write of a resumable frame's field, which the frame declares;
    each owned element's object lands in the frame field the slot prescan
    reserved for this site and element (`rcfg.ptr_slot_field`), or in place
    where it reserved none."""
    analyzer = lc.analyzer
    init = stmt.init
    facts = stmt if binding is None else binding
    mask = analyzer.ctx.tuple_elem_owned.get(facts) or ()
    if isinstance(init, TpyTupleLiteral):
        _reject_ptr_tuple_literal_element(facts, stmt.name, init, ptt,
                                          analyzer)
    # A literal reading anything but plain names and constants (`p =
    # (Box(9), p[0].n)`) is evaluated whole into the holder before any
    # element is rebound, owning or not; only a names-and-constants literal
    # writes its elements directly.
    staged_literal = (isinstance(init, TpyTupleLiteral)
                      and not all(isinstance(_peel_tpy_coerce(x),
                                             (TpyName, TpyIntLiteral,
                                              TpyFloatLiteral, TpyStrLiteral,
                                              TpyBoolLiteral, TpyNoneLiteral))
                                  for x in init.elements))
    if not any(mask) and not staged_literal:
        if init is not None and (
                _renders_own_borrow_tuple(
                    init, lc.names_with("borrow_tuple_mixed"), analyzer)
                or _btuple_owning_call_init(init, analyzer)):
            # An owning value the binding facts do not attribute to any
            # element (a select over owning calls) has no element storage.
            note_detail("ptr_tuple.unattributed_owner")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        return None
    if isinstance(init, TpyTupleLiteral):
        holder = _lower_borrow_tuple_literal(init, ptt, lc, declared)
        _witness("ptr_tuple.literal_holder")
    elif isinstance(init, (TpyCall, TpyMethodCall)) and _mixed_own_btuple_call(
            init, analyzer):
        holder = _lower_expr(init, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.VALUE,
                                          pos=SinkPos.LOCAL_DECL,
                                          forms=_decl_slot_forms(
                                              ptt, analyzer,
                                              whole_tuple_call=False,
                                              from_call=True,
                                              ptr_local=False),
                                          allow_temps=True))
        _witness("ptr_tuple.mixed_call_holder")
    elif _btuple_owning_call_init(init, analyzer):
        holder = _lower_expr(init, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.STORAGE,
                                          allow_temps=True,
                                          pos=SinkPos.LOCAL_DECL,
                                          forms=_decl_slot_forms(
                                              ptt, analyzer,
                                              whole_tuple_call=True,
                                              from_call=True,
                                              ptr_local=False)))
        _witness("ptr_tuple.owning_call_holder")
    else:
        note_detail("ptr_tuple.holder_source")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    elem_const = not frame and lc.declaring(stmt.name).const
    verdict = analyzer.ctx.tuple_elem_storage.get(facts)
    frame_fields = None
    if frame:
        first = False
        # The slot prescan decided each owned element's storage once.
        frame_fields = rcfg.ptr_tuple_slot_fields(lc.func, facts)
        if frame_fields is None:
            note_detail("res.rebind_ptr_slot")
            raise ThirUnsupported(stmt_reject_reason(stmt))
    elif module:
        # Every owned element of a module write takes storage of its own
        # (sema's verdict there): a fresh static that holds its object for
        # good -- except in a loop body, which runs the site again, so the
        # site's one hoisted slot takes it on every pass.
        first = not lc.in_for_body
    else:
        first = not is_reassign
    targets: list[THIRTupleElemTarget] = []
    for i, et in enumerate(ptt.element_types):
        if i < len(mask) and mask[i]:
            fld = None
            if frame_fields is not None:
                fld = frame_fields.get(i)
                storage = (RebindStorage.OWN if fld is not None
                           else RebindStorage.IN_PLACE)
            else:
                storage = _bindings.ptr_tuple_elem_storage(
                    verdict, i, et, first=first, const=elem_const)
            targets.append(THIRTupleElemTarget(
                index=i, elem_type=et, bind=TupleElemBind.OWNED,
                storage=storage,
                slot_cpp=lc.render_type(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(unwrap_own(et))))),
                field=fld, rebuild=frame and frame_object_slot(et)))
        elif TupleType._element_is_pointer_repr(et):
            targets.append(THIRTupleElemTarget(
                index=i, elem_type=et, bind=TupleElemBind.BORROW))
        else:
            targets.append(THIRTupleElemTarget(
                index=i, elem_type=et, bind=TupleElemBind.VALUE))
    declared[stmt.name] = ptt
    _witness("ptr_tuple.first_binding" if first else "ptr_tuple.rebind")
    return THIRTupleUnpack(
        source="", targets=(), target_cpps=(),
        source_bind=TupleSourceBind.RVALUE, source_expr=holder,
        tuple_target=THIRName(result_type=ptt, name=stmt.name, loc=loc),
        elem_targets=tuple(targets),
        tuple_decl_cpp=((ptt.to_cpp_return_const() if elem_const
                         else ptt.to_cpp_return())
                        if first and not module and not frame else None),
        loc=loc)


def _borrow_tuple_source_ok(src: TpyExpr, lc: '_LowerCtx',
                            own: 'tuple[str, BindingRepr] | None' = None
                            ) -> bool:
    """One binding source of a borrow-form tuple local: a REF/VALUE-capture
    tuple literal over plain non-const names, or a field/subscript storage
    lvalue. Field/subscript sources may be CONST -- the decl's element
    pointers take `const T*` from the planner's whole-body borrow-tuple const
    fixpoint, and every lift targets that declared const-ness. Literal REF
    captures stay non-const-only (`&(name)` into a `const T*` decl slot has
    no witnessed render), and a call source at reseat position needs the
    owning-slot emplace machinery (its own arm). NB const-ness here reads
    sema's frozen facts (borrow-decl bit, const params) plus the const
    locals registered so far, so a source that turns const only later is not
    covered. `own` is the name the caller declares and the record it
    declares it with: a source naming it (`t = t`) reads that record."""
    analyzer = lc.analyzer
    borrow_decls = analyzer.function_stmt_borrow_decls.get(lc.func, {})

    def name_const(n: str) -> bool:
        return lc.binding(n).const or borrow_decls.get(n, False)

    if isinstance(src, TpyTupleLiteral):
        if not (src.elem_capture
                and all(c in (TupleElemCapture.VALUE, TupleElemCapture.REF)
                        for c in src.elem_capture)):
            return False
        for elem, cap in zip(src.elements, src.elem_capture):
            if cap is TupleElemCapture.VALUE:
                continue
            if isinstance(elem, TpyNoneLiteral):
                # A `None` at a pointer-form Optional element is captured
                # REF so the literal keeps one pointer shape; it renders
                # `nullptr`, so there is no source to be const.
                continue
            if not isinstance(elem, TpyName) or name_const(elem.name):
                return False
            if isinstance(analyzer.get_expr_type(elem), ReadonlyType):
                return False
        return True
    if (isinstance(src, TpySubscript)
            and isinstance(src.obj, (TpyName, TpyFieldAccess))):
        return not isinstance(analyzer.get_expr_type(src.obj), ReadonlyType)
    if (isinstance(src, TpyFieldAccess)
            and isinstance(src.obj, TpyName)):
        # A storage-tuple FIELD source (`t = h.pair`, lent element by
        # element): the reseat_lift render; a const receiver is fine (the
        # decl's fixpoint verdict spells `const T*` and the lift targets it);
        # the field's own read admission runs during lowering.
        return not isinstance(analyzer.get_expr_type(src.obj), ReadonlyType)
    if isinstance(src, TpyName):
        # Another borrow-form tuple name of the same shape (`u = t`): the
        # reseat is the bare pointer-tuple copy. A parameter's const element
        # spelling is a const source, which the planner's const fixpoint
        # carries into the target's element pointers; its copy may copy
        # nothing but pointers and scalars.
        st = analyzer.get_expr_type(src)
        stu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
               if st is not None else None)
        rec = (own[1] if own is not None and src.name == own[0]
               else lc.binding(src.name))
        return ((not rec.const and src.name not in lc.prescan.param_names
                 or (rec.is_param and isinstance(stu, TupleType)
                     and stu.has_copy_free_borrow_form()))
                and not rec.storage_tuple
                and isinstance(stu, TupleType)
                and (stu.has_pointer_repr_element()
                     or rec.form is BindingForm.BORROW_TUPLE))
    return False


def _borrow_tuple_hoist_ok(name: str, bare: 'TupleType',
                           lc: '_LowerCtx', rec: BindingRepr) -> bool:
    """The borrow-tuple hoist admission SHARED by the if-cascade and the
    for-each arms -- the non-const routable slice (a resumable body's is
    the planner's `hoist.plain`). Rejects: move-through names; the
    borrow-decl const bit on the NAME itself (it ORs into the predecl's
    element const-ness, and the per-source walk below does NOT
    subsume it -- sema sets the bit from the init's RESULT type);
    pending/unresolved elements (`to_cpp_return` would render a raw
    literal digit as a template arg -- the resolve rung is deferred, see
    `_borrow_tuple_wrap_cpp` for the pattern); walrus binds; and any
    binding source outside `_borrow_tuple_source_ok`'s slice. A name with
    NO VarDecl/Assign sources is admitted -- the for-each loop bind IS
    its source."""
    analyzer = lc.analyzer
    if name in lc.prescan.move_through:
        return False
    if analyzer.function_stmt_borrow_decls.get(
            lc.func, {}).get(name, False):
        return False
    if contains_pending_leaf(bare) or any(
            isinstance(t, (PendingViewType, IntLiteralType, FloatLiteralType))
            for t in bare.inner_types()):
        return False
    srcs = _borrow_tuple_binding_sources(name, lc)
    # The pointer tuple's owning bindings (a call, a literal with a fresh
    # element) each back their elements at their own site, wherever the
    # site is. Otherwise an OWNING tuple-call source needs the record's
    # rebind slot, each call reseat emplacing into it, and a MIXED
    # own+borrow call the hybrid render the predecl holds, each branch a
    # plain assign.
    ptr_form = rec.ptr_tuple is not None
    return (srcs is not None
            and all(_borrow_tuple_source_ok(s, lc)
                    or (ptr_form and isinstance(
                        s, (TpyCall, TpyMethodCall, TpyTupleLiteral)))
                    or (rec.rebind_slot
                        and _btuple_owning_call_init(s, lc.analyzer))
                    or (rec.borrow_tuple_mixed
                        and _mixed_own_btuple_call(s, lc.analyzer))
                    for s in srcs))


def _borrow_tuple_hoist_entry(rec: BindingRepr, var_type: 'TupleType',
                              declared: dict[str, TpyType],
                              lc: '_LowerCtx', mixed_witness: str,
                              hoist_slots: 'list[BindingRepr] | None',
                              bindings: list[THIRHoistedBinding] | None = None,
                              ) -> HoistDecl:
    """One branch-BOUND borrow-form tuple hoist predecl (`std::tuple<...,
    T*> name;`, default-constructed null pointers) plus its read model: the
    local aliases whichever arm's source bound it, and reads key on the
    declared ptr-repr TupleType like a borrow-tuple param's. The pointer
    tuple (`rec.ptr_tuple`) is the same predecl over its own type, each
    owning binding backing its element at its own site. Otherwise an owning
    tuple-call source fills the chain head's rebind slot
    (`std::optional<std::tuple<...>> __slot_N;`, the record's
    `rebind_slot`), collected in `hoist_slots`. The element const-ness
    comes from the whole-body fixpoint (`const Box*` when nothing mutates
    through the local), the verdict the reseat renders target."""
    name = rec.name
    if rec.ptr_tuple is not None:
        var_type = rec.ptr_tuple
    if rec.rebind_slot:
        hoist_slots.append(rec)
    if rec.borrow_tuple_mixed:
        # A MIXED-call-bound hoist keeps the per-element arrow: the local
        # holds the hybrid render, so `std::get<1>(p)->val`.
        _witness(mixed_witness)
    declared[name] = var_type
    if not rec.borrow_tuple_mixed and not rec.rebind_slot:
        _record_hoisted_binding(bindings, rec, var_type, lc)
    cpp = (var_type.to_cpp_return_const() if rec.const
           else var_type.to_cpp_return())
    return HoistDecl(name, cpp, emit_prims.placeholder_init(var_type, cpp))


def _optional_storage_hoist_entry(name: str, var_type: TpyType,
                                  declared: dict[str, TpyType],
                                  lc: '_LowerCtx',
                                  bindings: list[THIRHoistedBinding] | None = None) -> HoistDecl:
    """One OPTIONAL_STORAGE hoist predecl (`std::optional<T> name;`) plus its
    read/write/move model (the record's `pointer`, `optional_storage` and
    `movable`): deref reads and `->` access, plain engaging assigns,
    last-use moves. Shared by the if cascade and the with family so the
    declarations cannot drift."""
    if frame_object_slot(var_type) and not _frame_slot_roots_ok(
            name, lc, declared):
        # What the frame borrows must be older than the optional, or it
        # would die first.
        note_detail("hoist.frame_slot_roots")
        raise ThirUnsupported("hoist.frame_slot_roots")
    declared[name] = var_type
    record = borrowed_record(var_type, False, lc.analyzer)
    if bindings is not None and record is not None:
        bindings.append(THIRHoistedBinding(name, var_type, optional_record_storage=record))
    return HoistDecl(name, f"std::optional<{lc.render_type(var_type)}>")


def _lower_if_hoist_predecls(stmt: TpyIf, hoists: dict,
                             declared: dict[str, TpyType], lc: '_LowerCtx',
                             in_branch: bool, bindings: list[THIRHoistedBinding],
                             ) -> tuple[list[HoistDecl],
                                        list[BindingRepr]]:
    """The if chain's branch-first predecls, each admitted and built by its
    record in cascade (dict) order. Returns `(hoist_decls, hoist_slots)`
    for THIRIf: every predecl renders `{cpp_type} {name};` at the chain
    head; a slot entry names a borrow tuple whose owning-call writes fill
    the binding's one slot (`StorageSlots.claim_shared`).

    An INNER-scope if (a loop / branch / with / try body) hoists at its own
    head like the with and try families: the predecl's C++ scope IS the
    enclosing block, and the name enters the caller's branch-local
    `declared`."""
    hoist = _Hoist(stmt, "if", in_branch, bindings, [])
    hoist_decls: list[HoistDecl] = []
    for name, raw in hoists.items():
        if name in declared:
            continue
        rec = _hoist_record(stmt, name, lc)
        _admit_hoist(hoist, name, rec, lc)
        decl = _build_hoist(hoist, name, raw, rec, declared, lc)
        if decl is not None:
            hoist_decls.append(decl)
    return hoist_decls, hoist.slots

def _handler_binding_type(h: TpyExceptHandler, analyzer) -> 'NominalType | None':
    """The `as`-binding's type, exactly as sema binds it: the class the
    clause resolved to."""
    return h.resolved_type

def _error_return_stmt_fi(expr: TpyExpr, analyzer):
    """The FunctionInfo when `expr` is an @error_return call handled at
    STATEMENT level (coerce-peeled call or method call). Anything this
    matches must take the bind/discard arm or reject -- letting it ride the
    generic expression path would render the expression-level unwrap where
    the `__try_tmp_N` block belongs."""
    if isinstance(expr, TpyCoerce):
        return _error_return_stmt_fi(expr.expr, analyzer)
    fi = None
    if isinstance(expr, (TpyCall, TpyMethodCall)):
        fi = getattr(expr, 'resolved_function_info', None)
    if fi is not None and fi.error_return_type:
        return fi
    return None

def _reject_nested_error_return_arg(stmt: TpyStmt,
                                    call: 'TpyCall | TpyMethodCall',
                                    analyzer) -> None:
    """GATE: a raw-lowered (statement-handled) @error_return call whose ARG
    SUBTREE carries another @error_return call. The render for this shape
    would be ILL-FORMED C++ (the statement-handled flag is consumed by
    whichever call renders first -- the inner arg -- so the outer call
    double-unwraps), so it is rejected, never silently fixed. Shared by all
    three error_return_raw sites (bind / discard / return
    pass-through). A method call's RECEIVER renders before its args, so it
    is swept by the same rule."""
    subtrees = list(call.args)
    if isinstance(call, TpyMethodCall):
        subtrees.append(call.obj)
    for arg in subtrees:
        for node in _walk(arg):
            fi = (getattr(node, 'resolved_function_info', None)
                  if isinstance(node, (TpyCall, TpyMethodCall)) else None)
            if fi is not None and fi.error_return_type:
                note_detail("error_return.nested_call")
                raise ThirUnsupported(stmt_reject_reason(stmt))

def _lower_error_return_bind(stmt, name: str, init: TpyExpr, er_fi, vtype,
                             lc: '_LowerCtx', declared: dict[str, TpyType],
                             loc) -> THIRErrorReturnBind:
    """The statement-level unwrap bind of a name already bound (a var-decl
    reassign or a name-assign init'd by a direct @error_return call). The
    name's record says how it is held; whether the unwrap aliases live
    storage is this call's own fact, and each write of the name may call a
    different callee, so the write decides it here."""
    _check_error_return_init(stmt, init, lc.analyzer)
    if call_returns_cpp_ref(lc.analyzer, er_fi):
        return _error_return_alias_bind(stmt, name, init, er_fi, vtype, lc,
                                        declared, loc)
    return _error_return_value_bind(stmt, name, init, er_fi, vtype, lc,
                                    declared, loc)


def _check_error_return_init(stmt, init: TpyExpr, analyzer) -> None:
    if not isinstance(init, (TpyCall, TpyMethodCall)):
        note_detail("error_return.stmt_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    _reject_nested_error_return_arg(stmt, init, analyzer)


def _error_return_alias_bind(stmt, name: str, init: TpyExpr, er_fi, vtype,
                             lc: '_LowerCtx', declared: dict[str, TpyType],
                             loc) -> THIRErrorReturnBind:
    """The unwrap of a borrow-aliasing result: it must alias live storage,
    so only a pointer target takes it."""
    # A declared pointer target takes the result's address
    # (`v = &(::tpy::unwrap_ref(*__try_tmp_N));`) and never a rebind slot,
    # which would copy and sever the alias. Any other target for an
    # aliasing result rejects (owned storage would copy too).
    if lc.prior_binding(name).pointer and name in declared:
        call_node = _lower_expr(
            init, lc, declared, error_return_raw=True,
            use=_ExprUse(allow_temps=True))
        return THIRErrorReturnBind(name=name, call=call_node,
                                   alias_bind=True,
                                   staged=lc.exit_staged, loc=loc)
    # A first declaration pre-declares the pointer and takes the address
    # (`T* v;` + `v = &(::tpy::unwrap_ref(*tmp));`). A const slot (its
    # record's verdict) rejects: the const pointer spelling is not rendered
    # here.
    bare = unwrap_readonly(unwrap_ref_type(er_fi.return_type)) \
        if isinstance(er_fi.return_type, TpyType) else None
    if (name not in declared and not lc.prior_binding(name).pointer
            and not (lc.func.is_generator or lc.func.is_async)
            and bare is not None and is_plain_nonvalue(bare)):
        if not lc.declaring(name).const:
            call_node = _lower_expr(
                init, lc, declared, error_return_raw=True,
                use=_ExprUse(allow_temps=True))
            declared[name] = vtype if vtype is not None \
                else er_fi.return_type
            _witness("er.alias_first_decl")
            return THIRErrorReturnBind(
                name=name, call=call_node,
                decl_cpp=f"{lc.render_type(bare)}*", alias_bind=True,
                staged=lc.exit_staged, loc=loc)
    note_detail("error_return.alias_bind")
    raise ThirUnsupported(stmt_reject_reason(stmt))


def _error_return_value_bind(stmt, name: str, init: TpyExpr, er_fi, vtype,
                             lc: '_LowerCtx', declared: dict[str, TpyType],
                             loc) -> THIRErrorReturnBind:
    """The unwrap of a by-value result. Routed slice: a plain owned local,
    or a REBIND-SLOT pointer local reseated through its optional slot (the
    rvalue-slot reseat arm); slot-less pointer targets and alias / tuple /
    value-opt / frame targets reject."""
    analyzer = lc.analyzer
    # A rebind-slot pointer local reseats through its own optional slot --
    # the same `p = &*(__slot_N = <rvalue>)` render THIRAssign emits, with
    # the unwrap as the rvalue. Emit reads the slot off `rebind_slots`, so
    # the name must already be declared (its decl allocated the slot); the
    # ptr-variant union locals are excluded because their reseat goes
    # through `to_ptr_variant`, not the bare slot address.
    ptr_rebind = (lc.prior_binding(name).pointer and lc.prior_binding(name).rebind_slot
                  and name in declared
                  and not is_ptr_variant_union(
                      unwrap_readonly(unwrap_ref_type(declared[name]))))
    # A TRY-HOISTED optional local (`std::optional<Point> p;` predecled
    # before the try; reads deref via the pointers machinery) takes the
    # bind arm's default render verbatim (`p = ::tpy::unwrap_ref_move(
    # *__try_tmp_N);` -- an assign into the hoisted optional, no decl).
    hoisted_opt = (lc.prior_binding(name).pointer
                   and lc.prior_binding(name).optional_storage
                   and name in declared
                   and not lc.prior_binding(name).rebind_slot)
    if (not ptr_rebind and not hoisted_opt
            and (lc.prior_binding(name).pointer or lc.prior_binding(name).rebind_slot)
            or lc.prior_binding(name).ref_alias
            or lc.prior_binding(name).storage_tuple
            or lc.prior_binding(name).value_opt
            is ValueOptKind.SCALAR
            or lc.prior_binding(name).frame_slot):
        note_detail("error_return.bind_target")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    decl_cpp = decl_type = None
    first_ptr_bind = False
    if name not in declared:
        # First binding: predecl `T name;` before the unwrap block (the goto
        # / early return would cross an initialized declaration). The
        # predecl spells the callee's SUCCESS type (`fi.return_type`), not
        # the decl annotation.
        var_type = er_fi.return_type
        bare = (unwrap_readonly(unwrap_ref_type(var_type))
                if isinstance(var_type, TpyType) else None)
        inner = bare.wrapped if isinstance(bare, OwnType) else bare
        # The predecl needs a default-constructible plain-value slot whose
        # later reads route on the declared type: the scalar/char/enum/
        # str-family/F1-record families.
        if not (_eligible_scalar(inner) or _eligible_char(inner)
                or _eligible_enum(inner, analyzer) is not None
                or _resolved_str_value(inner, analyzer) is not None
                or _resolved_bytes_value(inner, analyzer) is not None
                or _f1_record(inner, analyzer)
                # A recursive-alias WRAPPER union (`Own[JsonValue]`) is a
                # by-value struct with a defaulted ctor, so it predecls and
                # assigns exactly like the F1-record slot; its reads route
                # on the declared union like any other union binding.
                or (_eligible_wrapper_union(inner, analyzer) is not None
                    and _witness("er.bind_slot_wrapper_union"))):
            note_detail("error_return.bind_slot")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        decl_type = unwrap_ref_type(var_type)
        decl_cpp = lc.render_type(decl_type)
        if lc.declaring(name).pointer:
            # An rvalue-reassigned record binds as a pointer-local over
            # per-site storage (the F2d shape), so a later rebind under a
            # live alias can take storage of its own. This first bind
            # materializes the unwrapped value in a slot the pointer aims at.
            decl_cpp = f"{decl_cpp}*"
            decl_type = None
            ptr_rebind = True
            first_ptr_bind = True
        declared[name] = vtype
        # An unwrap-bound local is never auto-movable: `movable_locals` grows
        # at the var-decl arms only. Lowering seeds the whole sema fact up
        # front, so drop the name here or its last use would pick up a
        # `std::move` this bind must not emit.
    call = _lower_expr(init, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.STORAGE,
                                    allow_temps=True),
                       error_return_raw=True)
    if ptr_rebind:
        storage = (RebindStorage.OWN if first_ptr_bind
                   else _rebind_storage(stmt, name, lc))
        slot_cpp = _rebind_slot_cpp(declared[name], lc)
    else:
        storage = slot_cpp = None
    return THIRErrorReturnBind(
        name=name, call=call, decl_cpp=decl_cpp, decl_type=decl_type,
        ptr_rebind=ptr_rebind,
        rebind_storage=storage, slot_cpp=slot_cpp,
        staged=lc.exit_staged, loc=loc)

def _owned_record_decl_ok(stmt: TpyVarDecl, vtype: 'TpyType | None',
                          prescan: _Prescan,
                          declared: dict[str, TpyType], analyzer,
                          narrowed: 'set[str] | frozenset[str]'
                          = frozenset()) -> bool:
    """A single-assignment owned local from a record-or-container rvalue
    init -- `Box b = Box(n);` / `Box x = make(1);` / `std::vector<T> xs =
    mk();`, the binding classifier's plain-value-local arm (no indirection,
    dot access; sema's movable set already tracks it for last-use moves).
    Reassigned names take the REBIND_SLOT machinery, hoisted / move-through
    ones their own arms. Wrapper-annotated decls (`readonly[T]` / `Own[T]`
    locals) are sema errors, so `_var_decl_type`'s unwrapping never
    smuggles one in; the bare-NominalType check is defensive."""
    return (stmt.name not in prescan.reassigned
            and stmt.name not in prescan.hoisted
            and stmt.name not in prescan.move_through
            and isinstance(vtype, NominalType)
            and _storage_family_ok(vtype, analyzer)
            and stmt.init is not None
            and (_record_rvalue_source_shape(stmt.init, analyzer)
                 or _method_rvalue_record_like(stmt.init, analyzer)
                 # A field off an RVALUE call receiver (`jar =
                 # s.get(url).cookies` -- member of a dying temporary): the
                 # only legal emit is the plain copy decl (C++ moves the
                 # xvalue member). A BORROW-returning
                 # receiver keeps rejecting -- copying a live object's field
                 # is the REF_ALIAS value-position design stop.
                 or (isinstance(stmt.init, TpyFieldAccess)
                     and _field_over_call_ok(stmt.init, analyzer)
                     and _storage_family_ok(
                         analyzer.get_expr_type(stmt.init), analyzer)
                     and is_rvalue_source(analyzer, stmt.init))))

def _own_opt_record_call_slot(stmt: TpyVarDecl, vtype: 'TpyType | None',
                              lc: '_LowerCtx', analyzer) -> bool:
    """A single-assignment STORAGE `std::optional<T>` local from an
    owned-optional-returning call (`upgraded = w.upgrade()` on an
    `-> Own[Rc[T]] | None` accessor): the plain spelled copy decl; the name
    registers the RECORD-kind binding so narrowed reads deref
    `(*upgraded)` and the None-test reads has_value. Reassigned / hoisted /
    escaping names keep their own pointer machinery.

    The pointee is on the reference axis -- a container inner binds the same
    `std::optional<T>` slot and derefs the same way. The init is tested
    against `_storage_optional_return_wide`, the fact that built the callee's
    return slot, so the binding and the return cannot disagree about which
    calls produce it."""
    if (stmt.name in lc.prescan.reassigned
            or stmt.name in lc.prescan.hoisted
            or stmt.name in lc.prescan.move_through):
        return False
    if not isinstance(stmt.init, (TpyCall, TpyMethodCall)):
        return False
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))
         if isinstance(vtype, TpyType) else None)
    if not isinstance(u, OptionalType) or not record_like(
            _unwrap_own(unwrap_readonly(u.inner)), analyzer):
        return False
    it = analyzer.get_expr_type(stmt.init)
    return _storage_optional_return_wide(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
        if isinstance(it, TpyType) else None, analyzer) is not None


def _value_opt_record_slot(stmt: TpyVarDecl, vtype: 'TpyType | None',
                           lc: '_LowerCtx') -> bool:
    """The VALUE-record twin of `_own_opt_record_call_slot` (`g = t.goal`
    on `-> Optional[Coord]` where Coord is a ValueType record):
    `Optional[value-record]` is value-repr in every position, so the decl
    is the same plain spelled `std::optional<Coord>` copy and the name
    registers the same narrowed-deref / has_value read binding. Type-keyed
    like the bare value-record slot -- the init renders generically."""
    if (stmt.name in lc.prescan.reassigned
            or stmt.name in lc.prescan.hoisted
            or stmt.name in lc.prescan.move_through):
        return False
    return _value_opt_record(vtype) is not None


def _opt_btuple_wrap(inner: 'THIRExpr', opt_t: TpyType,
                     opt_cpp: str, loc) -> 'THIRExpr':
    """Wrap a borrow-tuple rhs in the optional spelling
    (`std::optional<B>{<rhs>}`)."""
    return THIRCoerce(result_type=opt_t, expr=inner,
                      coercion_name="opt_btuple_wrap",
                      wrap=opt_cpp + "{{{0}}}", form=Form.STORAGE, loc=loc)


def _lower_opt_btuple_decl(stmt: TpyVarDecl, opt_t: TpyType, bt: 'TupleType',
                           rec: BindingRepr, lc: _LowerCtx,
                           declared: dict[str, TpyType],
                           loc) -> THIRVarDecl:
    """First decl of a nullable borrow-form tuple local (`t: tuple[int, Box]
    | None = <src>` -> `std::optional<std::tuple<BigInt, Box*>> t = ...`,
    the OPTIONAL_BORROW_TUPLE LocalCppForm). A nullable-tuple call binds
    whole; otherwise `None` -> nullopt, a storage lvalue -> its
    per-element lend wrapped in the optional, an OWNING tuple call (the
    record's rebind slot) -> the per-target `__slot_N.emplace` alias (the
    btuple rebind-slot machinery under the optional wrap)."""
    analyzer = lc.analyzer

    def _register() -> None:
        declared[stmt.name] = opt_t

    init = stmt.init
    if (_nullable_borrow_tuple_call(init, analyzer) is not None
            and stmt.name not in lc.prescan.reassigned):
        # The call hands back this local's own layout, so `auto` binds it
        # whole (a `@readonly` method's const element pointers included)
        # and the elements alias what the callee lent.
        v = _lower_expr(init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.VALUE,
                                     allow_temps=True,
                                     pos=SinkPos.LOCAL_DECL,
                                     forms=_ONLY_BTUPLE_SLOT))
        _register()
        _witness("decl.opt_btuple_call")
        return THIRVarDecl(name=stmt.name, resolved_type=opt_t, init=v,
                           cpp_type="auto", form=Form.STORAGE, loc=loc)
    borrow_cpp = bt.to_cpp_return()
    opt_cpp = f"std::optional<{borrow_cpp}>"
    if isinstance(init, TpyNoneLiteral):
        _register()
        _witness("decl.opt_btuple_none")
        return THIRVarDecl(
            name=stmt.name, resolved_type=opt_t,
            init=THIRLiteral(result_type=opt_t, value=None,
                             form=Form.STORAGE, loc=loc),
            cpp_type=opt_cpp, form=Form.STORAGE, loc=loc)
    if not rec.rebind_slot:
        src = _lower_expr(init, lc, declared,
                          use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                          subscript_prechecked=True)
        _register()
        _witness("decl.opt_btuple_lift")
        return THIRVarDecl(
            name=stmt.name, resolved_type=opt_t,
            init=_opt_btuple_wrap(
                to_local(src, bt, BindingForm.BORROW_TUPLE, analyzer),
                opt_t, opt_cpp, loc),
            cpp_type=opt_cpp, form=Form.STORAGE, loc=loc)
    v = _lower_expr(init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 allow_temps=True,
                                 pos=SinkPos.LOCAL_DECL,
                                 forms=_decl_slot_forms(
                                     opt_t, analyzer,
                                     whole_tuple_call=True,
                                     from_call=True, ptr_local=False)))
    _register()
    _witness("decl.opt_btuple_slot")
    return THIRVarDecl(
        name=stmt.name, resolved_type=opt_t, init=v,
        cpp_type=opt_cpp, form=Form.STORAGE,
        btuple_slot_cpp=lc.render_type(bt),
        btuple_lent=slot_payload_lent(bt, lc), loc=loc)


def _lower_borrow_local(stmt: TpyVarDecl, vtype: TpyType, rec: BindingRepr,
                        lc: _LowerCtx, declared: dict[str, TpyType],
                        loc) -> 'THIRVarDecl | THIRPtrLocalDecl':
    """Lower a non-value borrow local's first declaration. REF_ALIAS binds a `T&`
    alias of the field's storage directly (no conversion node). POINTER lifts a
    plain-record lvalue to a reseatable `T*` via THIRFormConvert (`&(...)`).
    OPTIONAL_TO_PTR lifts the storage `optional<Inner>` to a borrow `Inner*` via
    THIRFormConvert (`::tpy::optional_to_ptr`), so its decl type is the inner.
    REBIND_SLOT (F2d) binds a plain-record rvalue (a ctor / by-value call) and
    the emitter materializes the init slot the pointer aims at; the init lowers
    as a plain value-form call (no conversion node)."""
    vopt = pointer_repr_optional(vtype)
    facts = _record_facts(rec)
    binding, is_const = facts["cpp_local_representation"], facts["is_const"]
    if binding is LocalBinding.REBIND_SLOT:
        # The slot init is target-threaded (`target_type=vtype`) --
        # load-bearing for a container literal's element renders, inert for
        # the record-rvalue call.
        if isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                  TpySetLiteral)):
            _witness("decl.container_rebind_slot")
        if type(stmt.init) in _comprehensions._COMP_KINDS:
            _witness("decl.comp_rebind_slot")
        # Declarations drain argument temporaries before writing their slot.
        init = _lower_storage_value(
            stmt.init, vtype, lc, declared, loc,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                         slot_target=vtype, allow_temps=True,
                         forms=_ONLY_SELECT_FRESH_PRVALUE))
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            owned_storage=borrowed_record(vtype, is_const, lc.analyzer),
            cpp_local_representation=binding, loc=loc)
    if (binding is LocalBinding.POINTER
            and isinstance(stmt.init, (TpyCall, TpyMethodCall))):
        # The reassigned flavor of the borrow-call decl: `[const] T* first
        # = &(get_first(data));` -- the PTR_ADDR emit takes the address of
        # the bare borrow-call lvalue; a later RVALUE reseat rides the
        # rebind-slot machinery (`first = &*(__slot_N = <rvalue>);`).
        _witness("decl.ptr_call_addr")
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.PTR_ADDR,
            init=_lower_expr(stmt.init, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                          pos=SinkPos.LOCAL_DECL,
                                          forms=_decl_slot_forms(
                                              vtype, lc.analyzer,
                                              whole_tuple_call=False,
                                              from_call=True,
                                              ptr_local=True))),
            cpp_type=lc.render_type(vtype), is_const=is_const, loc=loc)
    if (isinstance(stmt.init, (TpyCall, TpyMethodCall))
            and not (is_property_getter_read(stmt.init)
                     and binding is not LocalBinding.REF_ALIAS)):
        # REF_ALIAS from a borrow-record-returning call: the `T&` binds the
        # callee's returned reference directly (`Pair& p = shared(x);`,
        # `MyNumber& num = h.get_item();`), so the init is the plain
        # value-form call -- no conversion node. A @property read is the
        # same shape (`std::vector<int32_t>& v = c.items();` -- the field
        # arm delegates the read to the getter's method-call lowering) --
        # REF_ALIAS only, so the OPTIONAL_TO_PTR property row keeps its
        # optional_to_ptr lift arm below.
        _witness("decl.record_borrow_call")
        # Declarations drain argument temporaries before binding the alias:
        # a literal argument hoists to a named temp the borrow then outlives
        # (`std::vector<int32_t>& items = identity(__tmp_1);`).
        init = _lower_expr(
            stmt.init, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                         pos=SinkPos.LOCAL_DECL, forms=_NO_FORMS,
                         allow_temps=True))
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init,
            # The container a user callee returns by reference, aliased in place.
            native_container=(native_container(vtype, is_const, lc.analyzer)
                              if isinstance(init, (THIRCall, THIRMethodCall)) and init.resolved_callee is not None
                              and native_container_type(native_container_subject(vtype)) else None),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.REF_ALIAS:
        # A record-element container subscript source (`p = ps[i]`) lowers as
        # the plain subscript read (the `T&` alias binds the element lvalue);
        # a bare NAME source (`y = x`, `alias = items`) lowers to the bare
        # borrow-form name (never moved -- an alias source is in
        # `prescan.alias_sources`); a field source keeps the dedicated
        # borrow-source build.
        if isinstance(stmt.init, TpySubscript):
            src = _lower_expr(stmt.init, lc, declared,
                              use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                                           forms=_NO_FORMS),
                              subscript_prechecked=True)
            if (_tuple_name_elem_subscript(stmt.init, declared, lc.analyzer)
                    and src.reads_raw_pointer):
                # A borrow-form tuple name's element: std::get yields the
                # element `T*`, so the `T&` alias binds its referent --
                # the deref-flagged render (`(*std::get<1>(p))`). A
                # storage-form name's element is the lvalue itself.
                src = replace(src, deref=True)
                _witness("decl.btuple_elem_alias")
        elif isinstance(stmt.init, (TpyBinOp, TpyUnaryOp, TpyIfExpr)):
            # A container and/or select (`x = a or b`) or ternary
            # (`x = a if c else b`) of lvalues: the lowered ternary IS the
            # aliased lvalue -- the `T&` binds it directly. A borrow-returning
            # operator dunder (`c = a + b`, `c = -a`) binds the same way: the
            # friend shim's `[const] T&` return already IS the operand lvalue.
            if _borrow_dunder_source(stmt.init, lc.analyzer):
                _witness("decl.dunder_borrow_alias")
            # The alias is declared at this statement, in this block, and
            # never reseated: a select's fresh-operand slot lives exactly as
            # long.
            src = _lower_expr(stmt.init, lc, declared,
                              use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                                           forms=_ONLY_SELECT_SLOT))
        elif isinstance(stmt.init, TpyName):
            # A pointer-local source aliases through the deref
            # (`Point& alias = (*p);`) -- the shared pointer-name-source
            # render, also the erased-@dynamic assign's. The receiver
            # producer owns its lvalue spelling and dereference.
            src = _lower_ptr_name_src(stmt.init, lc, declared)
            if src is None:
                raise ThirUnsupported(stmt_reject_reason(stmt))
        elif isinstance(stmt.init, TpyCoerce):
            # The deref auto-coercion the gate admitted (`p2: Point = ptr`):
            # `::tpy::deref_check(ptr)` is itself the aliased lvalue, so the
            # `T&` binds it directly -- no address-of, no field build.
            _witness("decl.deref_coerce_alias")
            src = _lower_expr(stmt.init, lc, declared)
        else:
            src = _lower_field_source(stmt.init, lc, declared,
                                      pos=SinkPos.LOCAL_DECL)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=src,
            alias_binding=alias_binding(src, vtype, is_const, lc.analyzer),
            # An alias of a named container, or of a container field. A
            # subclass object (`x: dict[K, V] = sub`) is a record whose
            # container base MIR does not model as one.
            native_container=(native_container(vtype, is_const, lc.analyzer)
                              if (isinstance(src, THIRName)
                                  and native_container_subject(src.result_type)
                                  == native_container_subject(vtype))
                              or native_container_field(src) else None),
            storage_borrow=storage_borrow(src, vtype, is_const, lc.analyzer),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.POINTER and isinstance(stmt.init, TpyCoerce):
        # The reassigned sibling: the deref_check lvalue is reseatable, so the
        # decl takes its address (`Point* copy = &(::tpy::deref_check(ptr));`)
        # -- the container-element rung's PTR_ADDR emit over the coerce render.
        _witness("decl.deref_coerce_addr")
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.PTR_ADDR,
            init=_lower_expr(stmt.init, lc, declared),
            cpp_type=lc.render_type(vtype), is_const=is_const, loc=loc)
    if binding is LocalBinding.POINTER and isinstance(stmt.init, TpyName):
        # A reassigned bare record-name alias: `[const] T* x = &(a);`. The bare
        # name renders as a plain lvalue; the THIRFormConvert emits the `&(...)`.
        src = _lower_expr(stmt.init, lc, declared)
        if (src.form is Form.BORROW and src.result_type == vtype
                and not lc.resumable_leaf_mode):
            if (isinstance(src, THIRSelf) or (
                    isinstance(src, THIRName) and src.cpp is None
                    and not lc.binding(stmt.init.name).pointer
                    and not lc.binding(stmt.init.name).global_slot)):
                # The pointer slot addresses the live local, param or receiver.
                # Other spelled pointer/global sources keep their own admission.
                _witness("decl.ptr_name_addr")
                return THIRPtrLocalDecl(
                    name=stmt.name, resolved_type=vtype,
                    alias_binding=alias_binding(src, vtype, is_const, lc.analyzer),
                    kind=PtrSlotKind.PTR_ADDR, init=src,
                    cpp_type=lc.render_type(vtype),
                    is_const=is_const, loc=loc)
            # Same trap as the reseat arms: a same-type BORROW-form name
            # makes the convert the no-op node validate_function
            # hard-rejects -- the T&/T* representation change has no form
            # spelling yet, so the shape rejects. Resumable leaves are
            # exempt like the twin guards. NB the no-op rule now admits this
            # shape for every body kind, so this fence's original rationale
            # no longer holds; whether it still needs to reject is unverified.
            note_detail("decl.ptr_alias_borrow")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        convert = THIRFormConvert(
            result_type=vtype, value=src,
            form=Form.BORROW, is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            alias_binding=alias_binding(src, vtype, is_const, lc.analyzer),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.POINTER and isinstance(stmt.init, TpySubscript):
        # A container-element source reassigned later: `[const] T* p =
        # &(::tpy::__getitem__(ps, i));`. The element READ is already BORROW
        # form, so a FormConvert would be the no-op node the validator rejects
        # -- the `&(...)` lives in the PTR_ADDR emit, exactly as it does for
        # the `reseat.subscript_elem` arm every later `p = ps[j]` takes (whose
        # RECEIVER use this mirrors: a value use would copy the element).
        # A later RVALUE reseat needs its own `std::optional<T>` slot so the
        # alias to the init element is not overwritten -- it is pre-declared
        # here (held back until the reseat uses it).
        _witness("decl.subscript_elem_addr")
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.PTR_ADDR,
            init=_lower_expr(stmt.init, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.RECEIVER)),
            cpp_type=lc.render_type(vtype), is_const=is_const, loc=loc)
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and isinstance(stmt.init, TpyName)
            and _opt_ptr_addr_of_record_source(stmt.init, declared,
                                               lc.names_with("pointer"), lc.prescan,
                                               lc.analyzer)):
        # A plain record lvalue source (`p: Pet | None = d`): the decl takes
        # the address of the lvalue (`Pet* p = &(d);`) -- the same PTR_ADDR
        # emit the plain-record pointer alias uses, one slot family over. A
        # SUBCLASS source binds implicitly, so nothing spells the derived
        # type; the gate proved the relation.
        _witness("decl.opt_name_addr")
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.PTR_ADDR,
            init=_lower_expr(stmt.init, lc, declared),
            cpp_type=lc.render_type(unwrap_readonly(vopt.inner)),
            is_const=is_const, loc=loc)
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and isinstance(stmt.init, TpyName)
            and _f1_record(declared.get(stmt.init.name), lc.analyzer)):
        # A record-valued source that did NOT satisfy the address-of row
        # above (a pointer-bound or global-slot name). Falling through to
        # the bare pointer-copy leg would emit `Pet* p = d;` off a record
        # VALUE, which does not compile -- so this rejects rather than
        # drifting into a neighbouring render.
        note_detail("decl.opt_record_name_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and isinstance(stmt.init, TpyName)):
        # Same-repr pointer-Optional name source: the bare pointer copy
        # (`const Point* q = a;`) -- no lift, no convert node (a same-form
        # FormConvert is the no-op the validator rejects). The passthrough
        # keeps a narrowed source name from deref-rendering `(*a)`.
        src = _lower_expr(stmt.init, lc, declared,
                          use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                                       forms=_decl_slot_forms(
                                           vtype, lc.analyzer,
                                           whole_tuple_call=False,
                                           from_call=False, ptr_local=True)))
        _witness("decl.opt_name_copy")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=src,
            cpp_type=lc.render_type(vopt.inner), form=Form.BORROW,
            is_const=is_const, cpp_local_representation=binding, loc=loc)
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and isinstance(stmt.init, TpyIfExpr)):
        # A ternary source binds the lowered select as the `T*`: bare when
        # the ifexpr lowering already normalized each arm to it
        # (`Box* t = ((c) ? (p) : (::tpy::optional_to_ptr(h.opt)));`), the
        # address of the select when it is a pointee lvalue
        # (`C* t = &(((c) ? (a) : (b)));`). No select slot: a plain pointer
        # copy of the local can outlive the slot's block
        # (BUGS.md#select-pointer-slot-gaps).
        src = _to_opt_ptr(_lower_expr(stmt.init, lc, declared),
                          vtype, lc.analyzer, loc)
        _witness("decl.opt_ternary")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=src,
            cpp_type=lc.render_type(vopt.inner), form=Form.BORROW,
            is_const=is_const, cpp_local_representation=binding, loc=loc)
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and isinstance(stmt.init, TpySubscript)):
        # An element source (`first = t[0]`, `x = xs[0]`): the read converts
        # to the local's pointer -- passed where it already is one (a tuple
        # holding the element as `T*`), lifted through `optional_to_ptr`
        # where it is a stored optional.
        src = _lower_expr(stmt.init, lc, declared, subscript_prechecked=True)
        init = to_local(src, vtype, BindingForm.POINTER, lc.analyzer,
                        const=is_const)
        _witness("decl.mixed_elem_ptr" if init is src
                 else "decl.opt_tuple_elem_lift")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init,
            cpp_type=lc.render_type(vopt.inner), form=Form.BORROW,
            is_const=is_const, cpp_local_representation=binding, loc=loc)
    if (binding is LocalBinding.OPTIONAL_TO_PTR
            and is_property_getter_read(stmt.init)):
        # A @property source: the getter returns the FIELD's storage
        # optional BY REFERENCE (the property return convention), where a
        # plain method returns the `T*` borrow form already -- so this read
        # takes the lift over the bare call render and the method arm does
        # not.
        src = _lower_expr(stmt.init, lc, declared,
                          use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                                       forms=_NO_FORMS),
                          allow_whole_optional=True)
        convert = THIRFormConvert(result_type=vtype, value=src,
                                  form=Form.BORROW, is_const=is_const,
                                  loc=loc)
        _witness("decl.opt_property_lift")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            cpp_type=lc.render_type(vopt.inner), form=Form.BORROW,
            is_const=is_const, cpp_local_representation=binding, loc=loc)
    field = _lower_field_source(stmt.init, lc, declared)
    if binding is LocalBinding.POINTER:
        convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                                  is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            storage_borrow=storage_borrow(field, vtype, is_const, lc.analyzer),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    inner = vopt.inner  # OptionalType(Inner) -- the borrow points at Inner
    convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                              is_const=is_const, loc=loc)
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=convert,
        cpp_type=lc.render_type(inner), form=Form.BORROW, is_const=is_const,
        cpp_local_representation=binding, loc=loc)

def _root_elem_read_is_pointer(src: THIRExpr, root: str) -> bool:
    """Whether the lowered place read `src` reads its element of the tuple
    binding `root` as the bare pointer the tuple holds: down the read's
    receivers (through a check or conversion wrapping one) to the tuple
    element read whose receiver is `root` itself."""
    n: 'THIRExpr | None' = src
    while n is not None:
        if (isinstance(n, THIRSubscript) and n.tuple_index is not None
                and isinstance(n.receiver, THIRName)
                and n.receiver.name == root):
            return n.reads_raw_pointer
        if isinstance(n, (THIRSubscript, THIRFieldAccess)):
            n = n.receiver
        elif isinstance(n, (THIRFormConvert, THIRNoneCheck)):
            n = n.value
        elif isinstance(n, THIRCoerce):
            n = n.expr
        else:
            n = None
    return False


def _opt_slot_rvalue_shape(init: TpyExpr, inner: TpyType, analyzer) -> bool:
    """The rvalue init/reseat shapes an OPT_PTR_SLOT local admits: an F1-record
    ctor / by-value free call (`_record_rvalue_source_shape`) or by-value method
    call whose expression type is the optional's inner record, or a SUBCLASS
    of it: the slot spells the base, so its init slices exactly as every other
    storage-form upcast does, under sema's "upcast narrows" warning. An
    Optional-returning call needs the `optional_to_ptr` slot lift and still
    rejects -- its expression type is an OptionalType, neither `inner` nor a
    subclass of it."""
    it = analyzer.get_expr_type(init)
    if it is None:
        return False
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    if it_u != inner and not _record_slice_upcast_ok(it_u, inner, analyzer):
        return False
    return (_record_rvalue_source_shape(init, analyzer)
            # A by-value METHOD call at any reference-typed slot (the
            # argparse builder's accumulator reseat): the same
            # `p = &*(__slot_N = <rvalue>);` rebind render, type-blind past
            # the exact-inner equality above.
            or (isinstance(init, TpyMethodCall)
                and record_like(it_u, analyzer)
                and is_rvalue_source(analyzer, init))
            # A FREE call takes the container half only: a record-returning
            # free call rides `_record_rvalue_source_shape` above, which
            # vets the callee shape its bare `name(args)` emit needs.
            or (isinstance(init, TpyCall)
                and _f1_container_ref(it_u)
                and is_rvalue_source(analyzer, init)))


def _opt_storage_field_rvalue(init: TpyExpr, vtype: TpyType,
                              analyzer) -> bool:
    """A storage-form Optional read whose RECEIVER is an rvalue (`v =
    make_holder(p).value`, `q = mk().pp` off a getter):
    `is_storage_form_optional_source` is true but `is_rvalue_source` is too,
    so the lvalue lift does not apply and the whole `std::optional<T>`
    materializes in a slot first -- otherwise the pointer would outlive the
    receiver temporary. The two predicates ARE the shape: a field read and a
    getter call both reach here, and both spell the field's storage. The
    field type must match the local's declared Optional exactly."""
    if not reads_storage_form_optional(analyzer, init):
        return False
    if not is_rvalue_source(analyzer, init):
        return False
    it = analyzer.get_expr_type(init)
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it))) == \
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))


def _opt_proto_slot_cpp(init: TpyExpr, pointee: TpyType, vtype: TpyType,
                        analyzer) -> 'str | None':
    """The SLOT spelling for an Optional[@dynamic P] local's conformer ctor
    rvalue: the rvalue's class when the polymorphic-subclass fact
    retypes the slot (an inheriting conformer -- `&__slot` upcasts through
    the deduced `auto*`), else `auto` (a structural conformer stays
    monomorphized -- the deduced pointer dispatches statically). None when
    the init is outside the shape (names / erased sources are later
    rungs)."""
    if not (isinstance(pointee, NominalType) and is_dyn_protocol(pointee)):
        return None
    if not _record_rvalue_source_shape(init, analyzer):
        return None
    it = analyzer.get_expr_type(init)
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it))) \
        if it is not None else None
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    if not (isinstance(it_u, NominalType) and it_u.is_user_record):
        return None
    sub = polymorphic_subclass_into_optional(vtype, it, analyzer.registry)
    return sub.name if sub is not None else "auto"


def _ptr_union_slot_kind(init: TpyExpr, ptr_u: 'UnionType',
                         declared: dict[str, TpyType], lc: '_LowerCtx',
                         analyzer) -> 'PtrSlotKind | None':
    """Classify a ptr-variant union init/reseat source outside the bare-copy /
    field-lift slice: a concrete-MEMBER F1-record rvalue (UNION_RVALUE -- the
    value-variant `__slot_N` + `to_ptr_variant` lift) or a concrete-member
    lvalue NAME (UNION_ADDR -- `v{&(name)}`; a record NAME deriving from
    exactly one member counts, the variant's converting ctor binding the
    base pointer). None for everything else (const-rooted sources,
    pointer-form names, non-member types, a subclass rvalue or a source
    deriving from two members)."""
    it = analyzer.get_expr_type(init)
    if it is None:
        return None
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    # A scalar/str LITERAL init takes the bare-literal slot render whatever
    # type sema stamped on it (the member or the coerced whole union --
    # `a: int32 | str | Cat | Dog = 42` -> `std::variant<...> __slot_N =
    # 42;`, the converting ctor picking the SINGLE matching member; the
    # single-member-of-family key is what keeps an ambiguous variant out).
    lit = init
    while isinstance(lit, TpyCoerce):
        lit = lit.expr
    if isinstance(lit, TpyIntLiteral):
        return (PtrSlotKind.UNION_RVALUE
                if _single_member_of_family(
                    ptr_u.members, lambda m: is_fixed_int_type(m)
                    or is_big_int_type(m))
                else None)
    if isinstance(lit, TpyStrLiteral):
        return (PtrSlotKind.UNION_RVALUE
                if _single_member_of_family(ptr_u.members, is_str_type)
                else None)
    # A whole-union rvalue (an `Own[A | B]`-returning call) takes the same
    # value-variant slot + lift as a member rvalue -- the rvalue branch
    # is source-type-blind (member F1-ness is already `_eligible_ptr_union`'s
    # admission). Anything else must be a concrete F1 member.
    whole_union = it_u == ptr_u
    scalar_member = False
    if not whole_union:
        if not any(m == it_u for m in ptr_u.members):
            # A subclass-record lvalue NAME reaches the UNION_ADDR arm
            # only: `v{&(dog)}` at `Pet | Cat` converts `Dog*` to the
            # base member implicitly. NAME only -- a subclass rvalue would
            # slice into the base's value slot -- and a source deriving
            # from two members would be ambiguous to the converting ctor.
            if not (isinstance(init, TpyName)
                    and _union_base_member_match(it_u, ptr_u.members,
                                                 analyzer)):
                return None
        elif not record_like(it_u, analyzer):
            # A scalar MEMBER of a mixed union (`a: int32 | Dog | None =
            # int32(42)`): a type-ctor RVALUE takes the same value-variant
            # `__slot_N` + lift (the rvalue branch is member-shape-blind,
            # the slot init being the ctor's folded render). Scalar NAME
            # sources stay out (unwitnessed). A non-value NON-record member
            # lvalue NAME (a container member -- `v: int | set[int] = s`)
            # falls through to the UNION_ADDR arm below: the address bind
            # is member-shape-blind (`v{&(s)}`).
            # An OWNED-buffer member (`bytes`/`str`) rides the same branch:
            # its variant member spells the owned type, so the slot copies
            # whatever the call hands back and nothing borrows the callee's
            # temporary. A METHOD call is the same rvalue (`s.encode()`).
            # View members (StrView / BytesView / Span) stay out -- their
            # slot would hold a view of the dead temporary.
            if ((_eligible_scalar(it_u) or is_str_type(it_u)
                 or is_bytes_type(it_u))
                    and isinstance(init, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, init)):
                scalar_member = True
            elif not (isinstance(init, TpyName)
                      and not it_u.is_value_type()):
                return None
    if whole_union:
        # `copy(pet)` of a ptr-variant union NAME: the slot init is the
        # to_value_variant deep copy (the copy-special ptr-variant arm),
        # feeding the same value-variant `__slot_N` + lift as any rvalue.
        csrc = copy_call_arg(init, analyzer)
        if (isinstance(csrc, TpyName)
                and lc.binding(csrc.name).ptr_variant):
            return PtrSlotKind.UNION_RVALUE
        # An `Own[A | B]`-returning free call: the F1-record type gate of
        # `_record_rvalue_source_shape` cannot apply, so run the
        # return-type-blind callee/arg-shape half directly.
        if (isinstance(init, TpyCall)
                and is_rvalue_source(analyzer, init)
                and _rvalue_free_call_shape(init, analyzer)):
            return PtrSlotKind.UNION_RVALUE
        return None
    if scalar_member:
        return PtrSlotKind.UNION_RVALUE
    if (is_rvalue_source(analyzer, init)
            and _record_rvalue_source_shape(init, analyzer)):
        return PtrSlotKind.UNION_RVALUE
    if isinstance(init, TpyName) and init.name in declared:
        # The name must render bare (a plain storage local) and mutable:
        # pointer-form and const-rooted sources are later rungs.
        if (lc.binding(init.name).pointer or lc.binding(init.name).rebind_slot
                or lc.binding(init.name).const):
            return None
        return PtrSlotKind.UNION_ADDR
    return None


def _lower_opt_ptr_slot_decl(stmt: TpyVarDecl, vtype: 'OptionalType',
                             rec: BindingRepr, lc: _LowerCtx,
                             declared: dict[str, TpyType],
                             loc) -> THIRPtrLocalDecl:
    """Gate + lower an OPT_PTR_SLOT first declaration: a pointer-repr
    `Optional[T]` local whose init is a None literal (`T* x = nullptr;`) or an
    F1-record rvalue (`T __slot_N = ...; T* x = &__slot_N;`). The emitter
    allocates the `__slot_N` storage; rvalue reseats ride THIRAssign's storage
    verdict, None reseats the rebind node.
    Const indirection, non-F1 pointees, Own[Opt] lifts and polymorphic slots
    reject with named details -- their arms are later rungs."""
    analyzer = lc.analyzer
    inner = pointer_repr_optional(vtype).inner
    # `const T*` only off a readonly pointee, which an OPT_NONE (None-init)
    # decl alone reaches: the rvalue branch admits only a fresh F1-record
    # rvalue, never a readonly-typed source.
    is_const = rec.const
    pointee = unwrap_readonly(inner)
    # WIDE pointee class: the None flavor's `T* x = nullptr;` and the
    # rebind predecl are pointee-shape-blind; the RVALUE flavor keeps its
    # F1 shape gate below (`_opt_slot_rvalue_shape`).
    if not _opt_pointee_wide(pointee, analyzer):
        note_detail("decl.opt_slot_pointee")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    needs_rebind = rec.rebind_slot
    if stmt.init is None:
        # An annotation-only decl (`h: Handle | None`) renders the same
        # `T* x = nullptr;` as the explicit None init, but it allocates
        # NO rebind slot at the decl -- each rvalue reseat fills a slot of
        # its own (sema's `rebind_storage`).
        needs_rebind = False
        kind = PtrSlotKind.OPT_NONE
        init: THIRExpr | None = None
        _witness("decl.opt_slot_none")
    elif isinstance(stmt.init, TpyNoneLiteral):
        kind = PtrSlotKind.OPT_NONE
        init = None
        _witness("decl.opt_slot_none")
    else:
        # A const rvalue source is not part of this slice (the rvalue arm below
        # emits mutable-pointee storage); keep it a later rung.
        if is_const:
            note_detail("decl.opt_slot_const")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if _opt_storage_field_rvalue(stmt.init, vtype, analyzer):
            # A storage-form Optional FIELD read off an rvalue receiver
            # (`v = make_holder(p).value` -- the field of an Own-returning
            # call): the WHOLE optional must materialize before the
            # temporary dies, so the decl takes the same slot + lift render
            # as the Own-optional CALL source (`std::optional<T> __slot_N =
            # <field>; T* v = ::tpy::optional_to_ptr(__slot_N);`).
            _witness("decl.opt_storage_field")
            return THIRPtrLocalDecl(
                name=stmt.name, resolved_type=vtype,
                kind=PtrSlotKind.OPT_STORAGE_CALL,
                init=_lower_expr(stmt.init, lc, declared,
                                 use=_ExprUse(result=_ExprResultUse.STORAGE,
                                              pos=SinkPos.LOCAL_DECL,
                                              forms=_NO_FORMS,
                                              allow_temps=True),
                                 allow_whole_optional=True,
                                 field_prechecked=True),
                cpp_type=lc.render_type(pointee), is_const=is_const, loc=loc)
        if isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                  TpySetLiteral)):
            # A container LITERAL init at a pointer-repr Optional[container]
            # slot: the same `T __slot_N = init; T* x = &__slot_N;` render as
            # the record rvalue, only the literal's own spelling differs --
            # the OPTIONAL target is unwrapped at the literal, so a bare
            # list brace self-describes (dict/set literals
            # spell their container ctor already). A reseat-needing name takes
            # the inline `&*(__slot_N = ...)` rebind render instead, so it
            # stays out (the shared `_opt_slot_rvalue_shape` reseat sites do
            # NOT admit literals).
            if not _container_literal_shape_ok(stmt.init, pointee, analyzer):
                note_detail("decl.opt_slot_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            init = _lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                             allow_temps=True, slot_target=pointee))
            if isinstance(stmt.init, TpyArrayLiteral):
                if not isinstance(init, THIRContainerLiteral):
                    # The prefix has nowhere to live; reject rather than let a
                    # `replace` TypeError escape.
                    note_detail("decl.opt_slot_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                init = _spell_brace(init, pointee, lc)
            _witness("decl.opt_slot_container_literal")
            return THIRPtrLocalDecl(
                name=stmt.name, resolved_type=vtype,
                kind=PtrSlotKind.OPT_RVALUE, init=init,
                cpp_type=lc.render_type(pointee), is_const=is_const, loc=loc)
        if not _opt_slot_rvalue_shape(stmt.init, pointee, analyzer):
            # An Optional[@dynamic P] local from a CONFORMER ctor rvalue:
            # the slot types at the rvalue's class (inheriting -- the
            # implicit upcast through `auto*`) or `auto` (structural,
            # monomorphized -- static dispatch through the deduced
            # pointer); the pointer line is `auto* p = &__slot_N;`. A
            # reseat-needing name rejects (polymorphic rebind is a
            # sema-error tier).
            slot_cpp = _opt_proto_slot_cpp(stmt.init, pointee, vtype,
                                           analyzer)
            if slot_cpp is None or needs_rebind:
                note_detail("decl.opt_slot_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            init = _lower_expr(stmt.init, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                            allow_temps=True))
            _witness("decl.opt_slot_proto_rvalue")
            return THIRPtrLocalDecl(
                name=stmt.name, resolved_type=vtype,
                kind=PtrSlotKind.OPT_PROTO_RVALUE, init=init,
                cpp_type="auto", val_cpp=slot_cpp,
                is_const=is_const, loc=loc)
        kind = PtrSlotKind.OPT_RVALUE
        # The decl is a flush position: the ctor init's arg temps (a
        # value-union member temp) print before the slot line, the same
        # drain the union-slot arm takes.
        init = _lower_expr(
            stmt.init, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                         allow_temps=True, slot_target=pointee))
        _witness("decl.opt_slot_rvalue")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=kind, init=init,
        owned_storage=(borrowed_record(pointee, is_const, analyzer)
                       if kind is PtrSlotKind.OPT_RVALUE and isinstance(init, THIRCtorCall)
                       and init.result_type == pointee else None),
        cpp_type=lc.render_type(pointee), is_const=is_const, loc=loc)


def _lower_record_ptr_slot_decl(stmt: TpyVarDecl, vtype: TpyType,
                                rec: BindingRepr, lc: _LowerCtx,
                                declared: dict[str, TpyType],
                                loc) -> THIRPtrLocalDecl:
    """First decl of an escape-hoist PLAIN-record pointer-local from a
    record-rvalue init: a HOISTED name (DECL_RECORD_HOISTED_SLOT,
    `T* x = &*(__slot_N = init);` over a function-top `std::optional<T>`
    pre-decl) or a name-reassigned (not rvalue-reassigned) local
    (`T __slot_N = init;\\nT* x = &__slot_N;` -- the REBIND_SLOT render
    minus the rebind slot). The rvalue type must
    EQUAL the declared record -- a subclass rvalue retypes the slot (the
    polymorphic arm, rejected), mirroring `_opt_slot_rvalue_shape`'s
    discipline."""
    analyzer = lc.analyzer
    hoisted = rec.construct is BindingConstruct.RECORD_HOISTED_SLOT
    move = _move_through_source(stmt, vtype, lc)
    it = analyzer.get_expr_type(stmt.init)
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it))) \
        if it is not None else None
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    if it_u != vtype:
        note_detail("decl.record_slot_polymorphic")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if it is not None and isinstance(
            unwrap_ref_type(unwrap_send_sync(it)), ReadonlyType):
        # Structurally unreachable today (an rvalue ctor/by-value call is
        # never readonly-typed), but the OPT sibling rejects const sources
        # explicitly -- keep the invariant self-evident rather than implied.
        note_detail("decl.record_slot_const")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if (hoisted and frame_object_slot(vtype)
            and not _frame_slot_roots_ok(stmt.name, lc)):
        # The hoisted slot sits at the function's top, older than any local
        # the frame could borrow.
        note_detail("decl.frame_slot_roots")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    init = _lower_storage_value(
        stmt.init, vtype, lc, declared, loc,
        use=(_LOCAL_MOVE_USE if move else
             _ExprUse(result=_ExprResultUse.BORROW_BIND, slot_target=vtype)),
        move=move)
    if hoisted:
        # A resumable body's leaves emit through non-draining leaf emitters
        # (hoist_lines has no function-top drain there) -- reject like the
        # DYN_PROTOCOL rebind hoist.
        if lc.func.is_generator or lc.func.is_async:
            note_detail("decl.record_slot_resumable")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        kind = PtrSlotKind.RECORD_HOISTED
        lc.unhandled_hoists.discard(stmt.name)
        _witness("decl.record_slot_hoisted")
    else:
        kind = PtrSlotKind.RECORD_RVALUE
        _witness("decl.record_slot_rvalue")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=kind, init=init,
        owned_storage=borrowed_record(vtype, False, lc.analyzer),
        cpp_type=lc.render_type(vtype), rebuild=frame_object_slot(vtype),
        loc=loc)


def _lower_container_ptr_slot_decl(stmt: TpyVarDecl, rec: BindingRepr,
                                   lc: _LowerCtx, declared: dict[str, TpyType],
                                   loc) -> THIRPtrLocalDecl:
    """First decl of a NAME-reassigned container-LITERAL local -- the
    container flavor of the pointer-local binding class
    (`std::vector<T> __slot_N = {..};` + `std::vector<T>* xs = &__slot_N;`,
    the rvalue branch): reseats ride the pointer arms
    (`xs = &(b);`, the bare pointer copy `xs = xs;`), value reads deref via
    the F2d name render."""
    analyzer = lc.analyzer
    vtu = _decl_type(rec)
    if isinstance(vtu, PendingListType):
        # An unresolved pending binding would crash render_type -- reject.
        note_detail("container_lit.rebound")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if not _container_literal_shape_ok(stmt.init, vtu, analyzer):
        note_detail("container_lit.rebound")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    init = _lower_expr(
        stmt.init, lc, declared,
        use=_ExprUse(result=_ExprResultUse.STORAGE, slot_target=vtu,
                     copy_lift_make=False))
    if getattr(init, "make_container", False):
        # The make_vector / make_ordered_* element path's slot render is
        # unverified -- reject rather than risk a divergent `__slot_N` init.
        note_detail("container_lit.rebound")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    _witness("decl.container_slot_rvalue")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtu, kind=PtrSlotKind.RECORD_RVALUE,
        init=init, cpp_type=lc.render_type(vtu), loc=loc)


def _lower_global_slot_write(stmt: TpyVarDecl, lc: _LowerCtx,
                             declared: dict[str, TpyType],
                             loc, *, in_branch: bool = False) -> THIRStmt:
    """Module-init write of a NON-VALUE global -- the module-scope faces of
    the pointer-local rebind. The global is already declared at namespace
    scope, so every write is a REASSIGN (the name is pre-seeded); which
    render it takes depends on the source:

      * `None`            -> `g = nullptr;`
      * first rvalue      -> `static T __global_slot_N = init;`
                             `g = &__global_slot_N;`
      * later rvalue      -> `g = &(__global_slot_N = init);` (slot reuse)
      * hoisted name      -> `g = &*(__global_slot_N = init);` (an optional
                             slot on the hoist lines, reused by every write)
      * pointer-global    -> `g = other;`      (already a `T*`)
      * other lvalue      -> `g = &(other);`   (a name or a FIELD read)

    Slot reuse is recorded in `lc.global_slot_assigned`, which is
    branch-scoped: an allocation inside a branch is reusable by later writes
    in that same branch but not by a sibling branch or a write after it.

    Rejected here (each is a DIFFERENT render this arm does not carry): a
    ptr-repr Optional SOURCE other than the pass-through call and the
    storage-field lift, and a @dynamic-protocol or structural-protocol
    target (the adapter-slot `.emplace` rebind / `auto` slot)."""
    analyzer = lc.analyzer
    vtype = declared.get(stmt.name)
    # `check_escape` hoists the SOURCE of a `g2 = g1` binding, which makes
    # that global's slot re-assignable: the plain
    # `static T __global_slot_N = init;` decl becomes a function-top
    # `static std::optional<T> __global_slot_N;` and the write lifts through
    # it. `is_hoisted` matters at exactly ONE arm -- the first rvalue -- so
    # every other arm below stays hoist-independent and the flag is consumed
    # at this driver's tail, not at its dispatch.
    hoisted = stmt.name in lc.prescan.hoisted
    if stmt.init is None or vtype is None:
        note_detail("top_level.global_slot_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # A ptr-repr Optional global's slot carries the INNER spelling
    # (`T` of `T* g`), everything else its own.
    vt_bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))
    vt_opt = pointer_repr_optional(vtype)
    slot_t = vt_opt.inner if vt_opt is not None else vt_bare
    if is_protocol_type(slot_t) or is_dyn_protocol(slot_t):
        # A @dynamic-protocol target takes the adapter-slot rebind on EVERY
        # module-init write (the global is pre-declared at namespace scope,
        # so codegen sees each write as a reassign ->
        # the dynamic-protocol rebind with a `static` hoisted slot).
        # An already-erased source and the structural-protocol `auto` slot
        # stay rejected -- separate renders, unwitnessed.
        if isinstance(slot_t, NominalType) and is_dyn_protocol(slot_t):
            concrete_type = analyzer.get_expr_type(stmt.init)
            if (concrete_type is not None
                    and not is_protocol_type(concrete_type)):
                concrete_cpp = lc.render_type(concrete_type)
                if record_inherits_dynamic(concrete_type, slot_t,
                                           analyzer.registry):
                    slot_cpp = concrete_cpp
                else:
                    slot_cpp = dynamic_adapter_type(slot_t, concrete_cpp,
                                                    analyzer)
                _witness("top_level.global_dyn_rebind")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.DYN_PROTOCOL,
                    value=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(slot_target=concrete_type)),
                    val_cpp=slot_cpp, loc=loc)
        _gsp_arg = (stmt.init.args[0]
                    if (isinstance(stmt.init, TpyCall)
                        and stmt.init.func_name == "iter"
                        and len(stmt.init.args) == 1 and not stmt.init.kwargs)
                    else None)
        _gsp_arg_t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                          declared.get(_gsp_arg.name, None))))
                      if isinstance(_gsp_arg, TpyName)
                      and _gsp_arg.name in declared else None)
        if (isinstance(slot_t, NominalType) and slot_t.is_protocol
                and not is_dyn_protocol(slot_t)
                and _gsp_arg_t is not None
                and is_native_iterable(_gsp_arg_t, analyzer.registry)):
            # A STRUCTURAL-protocol global from `iter(<builtin container
            # NAME>)`: the un-spelled `static auto __global_slot_N =
            # <init>;` + addr assign -- the ordinary GLOBAL_RVALUE emit
            # with an `auto` slot (the namespace decl's decltype spelling
            # belongs to the module driver); a later write REUSES the slot
            # (`it = &(__global_slot_N = <init>);`, the GLOBAL_REBIND
            # emit). BUILTIN containers only: a user-record source's
            # `static auto` slot COPIES when its `__iter__` returns self
            # (the BUGS.md `iter(c)` self-copy entry), so it must keep
            # rejecting; combinator rvalues (map/zip) are unwitnessed and
            # stay out with it.
            if hoisted:
                # `iter(xs)` is an rvalue, so a HOISTED name would take the
                # optional-slot render, which cannot be spelled for the
                # `static auto` slot. Defensive, not load-bearing: binding
                # a second name to a
                # structural-protocol global (what hoists it) is itself a
                # codegen error today, so the shape has no witness to pin.
                note_detail("top_level.global_hoist_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            init = _lower_expr(stmt.init, lc, declared,
                               use=_ExprUse(allow_temps=True))
            _witness("top_level.global_slot_proto")
            if stmt.name in lc.global_slot_assigned:
                # `static auto` slot: no OWN spelling, so the write goes in
                # place whatever the verdict (the emit keys on val_cpp None).
                return THIRPtrLocalRebind(name=stmt.name,
                                          kind=PtrSlotKind.GLOBAL_REBIND,
                                          value=init, loc=loc)
            lc.global_slot_assigned.add(stmt.name)
            return THIRPtrLocalDecl(
                name=stmt.name, resolved_type=vtype,
                kind=PtrSlotKind.GLOBAL_RVALUE, init=init,
                cpp_type="auto", branch_scope=in_branch, loc=loc)
        note_detail("top_level.global_slot_protocol")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if isinstance(stmt.init, TpyNoneLiteral):
        if isinstance(vt_bare, UnionType):
            # The union monostate arms (`slot.emplace(monostate)` / the
            # slot-less `(*g) = monostate`) are separate renders.
            note_detail("top_level.global_slot_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("top_level.global_null")
        return THIRPtrLocalRebind(name=stmt.name,
                                  kind=PtrSlotKind.GLOBAL_NULL, loc=loc)
    init_t = analyzer.get_expr_type(stmt.init)
    init_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(init_t)))
                 if init_t is not None else None)
    if isinstance(init_bare, OwnType):
        init_bare = unwrap_readonly(init_bare.wrapped)
    if isinstance(init_bare, OptionalType):
        # A ptr-repr Optional result from a BORROW-returning callee is
        # already a `T*`, so the rebind's Optional branch
        # passes it through bare (`g = find(...);`). An `Own[Optional[T]]`
        # callee (storage form) takes the slot + `optional_to_ptr` lift, an
        # OPTIONAL_STORAGE name source the bare lift, and a value-emit
        # rvalue the materializing slot -- all separate renders. A slot this
        # global already allocated is irrelevant to the pass-through: the
        # bare assign does not touch it, and emit keeps the plain THIRAssign
        # off the `&*(__slot_N = ...)` reseat arm by its null `rebind_storage`.
        if (init_bare.uses_pointer_repr()
                and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and not call_hands_back_value(stmt.init)):
            _witness("top_level.global_opt_passthrough")
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name,
                                form=Form.BORROW, loc=loc),
                value=_lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.GLOBAL_SLOT_WRITE),
                    allow_whole_optional=True),
                loc=loc)
        if (isinstance(stmt.init, TpyFieldAccess)
                and _optional_ptr_borrow(init_bare, analyzer) is not None
                and unwrap_readonly(init_bare.inner) == slot_t):
            # An Optional[record] FIELD is STORAGE form
            # (`std::optional<T>`), so the pointer-slot global lifts the
            # member read (`g = ::tpy::optional_to_ptr(h->value);`) -- no
            # slot, the owner keeps the storage.
            _witness("top_level.global_opt_field_lift")
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name,
                                form=Form.BORROW, loc=loc),
                value=THIRFormConvert(
                    result_type=init_bare,
                    value=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE),
                        allow_whole_optional=True),
                    form=Form.BORROW, loc=loc),
                loc=loc)
        note_detail("top_level.global_slot_opt_source")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if not is_rvalue_source(analyzer, stmt.init):
        # Lvalue source: a pointer-slot global is already a `T*` and copies
        # bare; anything else is the address-of catch-all. Only NAME sources
        # are admitted -- a deeper lvalue's render at this position has no
        # witness.
        if (isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and init_bare is not None and init_bare == slot_t):
            # A BORROW-returning call -- method (`points.load(0)` ->
            # `pt = &(points->load(0));`) or free/generic (`get_item(points,
            # 0)` -> `p = &(get_item<Point>((*points), 0));`): the callee
            # owns the storage, so the slot points AT it -- the address-of
            # catch-all, with no `__global_slot_N` allocated. Same-type only:
            # a subclass borrow would retype the slot (the polymorphic arm's
            # business). RECEIVER use because the call is consumed under the
            # `&(...)` lift.
            _witness("top_level.global_addr_call")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                value=_lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER,
                                 pos=SinkPos.GLOBAL_SLOT_WRITE, forms=_ONLY_ADDR_CALL)),
                loc=loc)
        if (isinstance(stmt.init, TpyFieldAccess)
                and init_bare is not None and init_bare == slot_t):
            # A FIELD lvalue source (`ys = h.xs`): the owner holds the
            # storage, so the slot points AT the field -- the same
            # address-of catch-all as the borrow-returning call above, with
            # no `__global_slot_N` allocated. Same-type only, for the same
            # reason.
            _witness("top_level.global_addr_field")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                value=_lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
                loc=loc)
        if not isinstance(stmt.init, TpyName):
            note_detail("top_level.global_slot_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if _bare_pointer_name(stmt.init.name, lc):
            # Pointer-slot globals AND locally-declared pointer LOCALS
            # (`saved = p;` in a top-level loop) are already `T*`: the
            # both render as the bare copy. An IMPORTED pointer global
            # (spelled via global_cpp) stays out -- its spelling is
            # qualified, which this bare-name emit cannot produce.
            _witness("top_level.global_ptr_copy")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.GLOBAL_PTR_COPY,
                value=THIRName(result_type=vtype, name=stmt.init.name,
                               form=Form.BORROW, loc=loc),
                loc=loc)
        _gsw_it = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                       declared[stmt.init.name])))
                   if (stmt.init.name in declared
                       # An IMPORTED pointer-slot global has a pointer record
                       # (not a module slot) and needs its bare pointer
                       # copied -- not the address-of; keep it out until a
                       # witness lands the copy render.
                       and not lc.binding(stmt.init.name).pointer) else None)
        if _gsw_it is not None and _gsw_it == slot_t:
            # The address-of catch-all for a plain LOCAL lvalue -- the
            # desugared unpack alias (`a = &(__unpack_0_0);`): the local
            # owns (or aliases) the storage, the slot points AT it.
            _witness("top_level.global_addr_local")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                value=THIRName(result_type=declared[stmt.init.name],
                               name=stmt.init.name, form=Form.BORROW,
                               loc=loc),
                loc=loc)
        note_detail("top_level.global_slot_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if isinstance(slot_t, UnionType):
        # A VALUE-variant global slot (`u: Point | Line = Point(1)`): the
        # rvalue converts into the variant at its own member type, so the
        # slot spells the union and the init renders as the member -- one arm
        # for every rvalue source the union conversion admits. A container
        # literal has no union-typed lowering at all, so it renders at its
        # MEMBER type and the variant slot constructs from that.
        _u_target = slot_t
        if isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                  TpySetLiteral, TpyListRepeat)):
            _u_kind = (is_dict if isinstance(stmt.init, TpyDictLiteral)
                       else is_set if isinstance(stmt.init, TpySetLiteral)
                       else is_list)
            _u_cands = [m for m in slot_t.members
                        if _u_kind(unwrap_readonly(m))]
            if len(_u_cands) != 1:
                note_detail("top_level.global_slot_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _u_target = unwrap_readonly(_u_cands[0])
        _witness("top_level.global_slot_union")
        init = _lower_expr(stmt.init, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=True,
                                        slot_target=_u_target))
        if isinstance(init, THIRContainerLiteral):
            # A bare brace-init cannot pick the variant alternative, so the
            # literal self-describes (`std::vector<int32_t>{1, 2}`).
            init = replace(init, typed_brace_cpp=lc.render_type(_u_target))
    elif isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                TpySetLiteral, TpyListRepeat)):
        if not (is_list(slot_t) or is_dict(slot_t) or is_set(slot_t)
                or is_array(slot_t)):
            note_detail("top_level.global_slot_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if not _container_literal_shape_ok(stmt.init, slot_t, analyzer):
            note_detail("top_level.global_slot_elem")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # A make_vector / make_ordered_* init is admitted here (unlike at the
        # ptr-slot local sibling): the global slot line is an ordinary
        # initializer position, `static T __global_slot_N = <expr>;`, which
        # the comprehension branch below already fills with a statement
        # expression.
        init = _lower_expr(stmt.init, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        slot_target=slot_t))
    elif _f1_container_ref(slot_t):
        # Source-shape BLIND, unlike the local sibling's vetted
        # `_rebind_rvalue_source_ok` list: this arm threads the target type
        # for any rvalue, and the lowering of the init gates its own shape. The local sibling is the one that
        # should eventually lose its list, not this one gain one.
        if type(stmt.init) in _comprehensions._COMP_KINDS:
            # A comprehension init renders its stmt-expr inside the static
            # slot line (`static std::vector<T> __global_slot_N = ({...});`)
            # -- the frame-emplace comp branch's top-level twin.
            init = _comprehensions._lower_comprehension(
                stmt.init, slot_t, lc, declared,
                _comp_shadow_pointers(lc.names_with("pointer"), declared, lc.analyzer))
            _witness("top_level.global_slot_comp")
        else:
            init = _lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             pos=SinkPos.GLOBAL_SLOT_WRITE, forms=_NO_FORMS,
                             slot_target=slot_t))
    elif isinstance(slot_t, NominalType):
        if init_bare != slot_t and not (
                init_bare is not None
                and analyzer.registry.is_subclass_of(init_bare, slot_t)):
            note_detail("top_level.global_slot_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if init_bare != slot_t:
            # A SUBCLASS rvalue: the slot keeps the ANNOTATED type and the
            # construction slices into it, which is the upcast sema already
            # warns about ("only 'Base' fields and methods will be
            # accessible") -- so the slot spelling stays slot_t and no
            # separate polymorphic render is involved.
            _witness("top_level.global_slot_upcast")
        # allow_temps: the module-init body is an ordinary flush
        # position (`__tpy_init` hoists `__tmp_N` decls ahead of the
        # `static __global_slot_N` init like any statement), so the
        # generic ref-slot literal temps land as plain locals here.
        init = _lower_expr(stmt.init, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=True,
                                        slot_target=slot_t))
    else:
        note_detail("top_level.global_slot_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if hoisted:
        # A hoisted global's slot is an OPTIONAL on the hoist lines, so EVERY
        # write lifts through it (`g = &*(__global_slot_N = ..)`), not just
        # the first: emit keys that slot by NAME, so a later write reuses it
        # instead of allocating a second one. `in_branch` is deliberately NOT
        # consulted: it drops the `static` on the in-place GLOBAL_RVALUE
        # decl, but a hoist line keys off `slots.global_scope` and keeps it
        # in both paths -- and it sits at the body top, so a write inside a
        # branch still finds the slot in scope.
        first_write = stmt.name not in lc.global_slot_assigned
        lc.global_slot_assigned.add(stmt.name)
        lc.unhandled_hoists.discard(stmt.name)
        _witness("top_level.global_hoist_slot")
        storage = (None if first_write
                   else _rebind_storage(stmt, stmt.name, lc))
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.GLOBAL_HOIST_RVALUE,
            value=_slot_fill_value(init, slot_t, storage, lc),
            val_cpp=lc.render_type(slot_t), rebind_storage=storage,
            rebuild=frame_object_slot(slot_t), loc=loc)
    if stmt.name in lc.global_slot_assigned:
        _witness("top_level.global_slot_reuse")
        storage = _rebind_storage(stmt, stmt.name, lc)
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.GLOBAL_REBIND,
            value=_slot_fill_value(init, slot_t, storage, lc),
            val_cpp=lc.render_type(slot_t), rebind_storage=storage,
            rebuild=frame_object_slot(slot_t), loc=loc)
    lc.global_slot_assigned.add(stmt.name)
    _witness("top_level.global_slot")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype,
        kind=PtrSlotKind.GLOBAL_RVALUE, init=init,
        cpp_type=lc.render_type(slot_t), branch_scope=in_branch, loc=loc)


def _park_tuple_global(stmt: TpyVarDecl, scope: '_LowerScope',
                       lc: _LowerCtx, declared: dict[str, TpyType],
                       loc, *, binding: 'TpyStmt | None' = None
                       ) -> 'THIRStmt | None':
    """A module-level write of a tuple global whose value holds an object
    itself -- a literal with a fresh element, a call handing one back: the
    global is the pointer tuple, and each owned element is the scalar
    global's static slot, element-wise (`_lower_ptr_tuple_binding`):
    `auto __tup_N = make_mixed(1, (*V));`
    `static Box __global_slot_M = std::move(std::get<0>(__tup_N));`
    `std::get<0>(G) = &__global_slot_M; std::get<1>(G) = std::get<1>(__tup_N);`.
    A later write takes sema's per-element verdict; a site inside a `for`
    body takes a hoisted slot assigned on every pass (the alias-rebind pass
    warns a name an earlier pass bound). None when the value holds no
    object. `binding` is the statement sema stamped, when `stmt` stands in
    for it."""
    analyzer = lc.analyzer
    bt_t = (_borrow_tuple_local_type(stmt.name, declared,
                                     lc.names_with("storage_tuple"))
            if (lc.top_level_scope
                and stmt.name in analyzer.ctx.top_level_decls) else None)
    if bt_t is None:
        return None
    fact = analyzer.ctx.tuple_elem_owned.get(
        stmt if binding is None else binding)
    if fact is None or len(fact) != len(bt_t.element_types):
        return None
    if not any(fact):
        return None
    ptt = _bindings.pointer_tuple_of(resolve_tuple_pending(bt_t, analyzer))
    if ptt is None:
        return None
    for i, own in enumerate(fact):
        if own and not _bindings.bare_ptr_element(ptt.element_types[i]):
            note_detail("ptr_tuple.owned_element_form")
            raise ThirUnsupported(
                stmt_reject_reason(stmt if binding is None else binding),
                message=_bindings.ptr_tuple_element_message(
                    stmt.name, i, ptt.element_types[i]))
    if scope.loop_depth and not lc.in_for_body:
        # A `while` body keeps the slot `static`, as the scalar's does: it
        # would initialize once and freeze the first pass's value.
        note_detail("top_level.global_slot_branch")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if any(isinstance(et, TupleType) and et.has_nested_pointer_repr_element()
           for et in map(unwrap_readonly, bt_t.element_types)):
        # The element-by-element conversion does not descend into a nested
        # tuple (BUGS.md#nested-storage-tuple-element-read).
        note_detail("top_level.tuple_global_nested_ref")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    _witness("top_level.tuple_global_park_loop" if lc.in_for_body
             else "top_level.tuple_global_park")
    return _lower_ptr_tuple_binding(
        stmt, ptt, True, lc, declared, loc,
        binding=binding, module=True)


def _lower_dyn_own_erased_call_decl(stmt: TpyVarDecl, rec: BindingRepr,
                                    lc: _LowerCtx,
                                    declared: dict[str, TpyType],
                                    loc) -> THIRVarDecl:
    """An ALREADY-ERASED Own[dyn] call init (`c = make(41)` where make
    returns Own[Cancellable[T]] -- the 'forward' verdict): the plain
    spelled VALUE decl (`std::unique_ptr<Base> c = make(41);`); reads move
    at last use through the Own-slot arg rows, so the name must NOT join
    a pointer record (the Base* slot registration is for the borrowing decl
    forms)."""
    analyzer = lc.analyzer
    sema_var_t = _decl_type(rec)
    init_node = _lower_expr(
        stmt.init, lc, declared,
        use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True))
    declared[stmt.name] = sema_var_t
    _witness("decl.dyn_own_erased_call")
    _dyn_base = dynamic_base_name(
        unwrap_readonly(unwrap_send_sync(sema_var_t)).wrapped, analyzer)
    return THIRVarDecl(
        name=stmt.name, resolved_type=sema_var_t, init=init_node,
        cpp_type=f"std::unique_ptr<{_dyn_base}>", loc=loc)


def _lower_coro_frame_local_decl(stmt: TpyVarDecl, lc: _LowerCtx,
                                 declared: dict[str, TpyType],
                                 loc) -> THIRVarDecl:
    """An `Own[dyn-protocol]` local off an ASYNC-FACTORY init (`c =
    add_one(41)` at Own[Cancellable[T]]): erasure is DEFERRED -- the local
    holds the CONCRETE frame in optional storage (`std::optional<
    __coro_add_one> c = add_one(41);`) and the Own[dyn] CONSUMER arg erases
    (`make_adapter<Base>(std::move(*(c)))`). Slice: a plain free async-def
    call bound to a fresh un-hoisted name; every other Own[dyn] local is the
    heap-owned `unique_ptr<Base>` form, which is not lowered here."""
    analyzer = lc.analyzer
    sema_var_t = analyzer.var_types.get(stmt)
    af_fi = (stmt.init.resolved_function_info
             if isinstance(stmt.init, TpyCall) else None)
    own_wrapped = unwrap_readonly(
        unwrap_readonly(unwrap_send_sync(sema_var_t)).wrapped)
    if not (af_fi is not None and af_fi.is_async
            and not af_fi.is_method and not af_fi.is_staticmethod
            and isinstance(stmt.init.func, TpyName)
            and not stmt.init.kwargs
            # The DEFERRED-erasure shape ONLY: sema types the binding
            # Own[ConcreteCoroType]. An erased Own[Cancellable[T]] binding
            # (a protocol-param callee) takes the make_adapter/unique_ptr
            # decl -- a different render.
            and isinstance(own_wrapped, ConcreteCoroType)
            # The renderer-free spelling slice: no owner record and no
            # generic targs (both need the codegen TypeResolver).
            and own_wrapped.frame_owner is None
            and not own_wrapped.frame_inferred_type_args
            and stmt.name not in lc.prescan.hoisted
            and not (lc.func.is_generator or lc.func.is_async)):
        note_detail("decl.dyn_protocol_own")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    init_node = _lower_expr(
        stmt.init, lc, declared,
        use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                     forms=_decl_slot_forms(
                         sema_var_t, analyzer, whole_tuple_call=False,
                         from_call=True, ptr_local=False)))
    declared[stmt.name] = sema_var_t
    _witness("decl.coro_frame_local")
    frame_cpp = sub_struct_qualname(None, None, own_wrapped.frame_func_name,
                                    module_qual=own_wrapped.frame_module_qual)
    return THIRVarDecl(
        name=stmt.name, resolved_type=sema_var_t, init=init_node,
        # The SHARED naming helper (cross-module qualification via
        # frame_module_qual); the owner/targ flavors -- which need the
        # codegen TypeResolver -- are gate-excluded above.
        cpp_type=f"std::optional<{frame_cpp}>", loc=loc)


def _lower_dyn_protocol_decl(stmt: TpyVarDecl, vtype: TpyType,
                             rec: BindingRepr, lc: _LowerCtx,
                             declared: dict[str, TpyType],
                             loc) -> THIRPtrLocalDecl:
    """First decl of a @dynamic protocol local (`p: P = Concrete(...)`): a
    concrete/adapter `__slot_N{init}` storage + a protocol `Base* p =
    &__slot_N;` alias -- the non-erased arm -- or the alias of an
    already-erased source. A hoisted local rejects with a named detail."""
    analyzer = lc.analyzer
    # A HOISTED (escaping) @dynamic local's first decl may take a different
    # shape than this direct init slot -- a later rung.
    if stmt.name in lc.prescan.hoisted:
        note_detail("decl.dyn_protocol_hoisted")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # A REASSIGNED local's FIRST decl emits identically to a non-reassigned
    # one (the direct init slot); its reseats take the rebind arm (its
    # record).
    concrete_type = analyzer.get_expr_type(stmt.init)
    if concrete_type is None:
        note_detail("decl.dyn_protocol_erased")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if is_protocol_type(concrete_type):
        # Already-erased source: alias the same object (`Base* p2 = &(*p1);`
        # for a pointer-local source, `&(pet)` for a `Pet&` param), no slot.
        erased = _lower_dyn_erased_source(stmt.init, lc, declared)
        if erased is None:
            note_detail("decl.dyn_protocol_erased")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("decl.dyn_protocol_erased")
        # A const source (`ob.get()` on a const handle) is aliased const.
        const_pfx = "const " if rec.const else ""
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype,
            kind=PtrSlotKind.DYN_PROTOCOL_ERASED, init=erased,
            base_cpp=const_pfx + dynamic_base_name(vtype, analyzer),
            loc=loc)
    concrete_cpp = lc.render_type(concrete_type)
    if record_inherits_dynamic(concrete_type, vtype, analyzer.registry):
        slot_cpp = concrete_cpp  # direct inheritance -- plain concrete slot
    else:
        slot_cpp = dynamic_adapter_type(vtype, concrete_cpp, analyzer)
    init = _lower_expr(stmt.init, lc, declared,
                       use=_ExprUse(slot_target=concrete_type))
    _witness("decl.dyn_protocol")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.DYN_PROTOCOL,
        init=init, cpp_type=slot_cpp,
        base_cpp=dynamic_base_name(vtype, analyzer), loc=loc)


def _lower_ptr_name_src(init: TpyExpr, lc: _LowerCtx,
                        declared: dict[str, TpyType]) -> 'THIRExpr | None':
    """Lower a NAME source, forcing the deref render for a pointer-local
    (`(*p)` -- pointer names render bare by default, their access riding
    the arrow arms); bare for a non-pointer name. None for a non-name
    source or another render. Receiver lvalues keep the producer's deref."""
    if not isinstance(init, TpyName):
        return None
    # The one decl verdict `_decl_slot_forms` cannot give: the deref is the
    # SOURCE's, not the slot's, and this helper is handed no slot -- its
    # callers use it for several decl shapes.
    src = _lower_expr(init, lc, declared,
                      use=_ExprUse(pos=SinkPos.LOCAL_DECL, forms=_ONLY_INDIRECT_READ))
    return src if isinstance(src, (THIRName, THIRSelf)) else None


def _lower_dyn_erased_source(init: TpyExpr, lc: _LowerCtx,
                             declared: dict[str, TpyType]) -> 'THIRExpr | None':
    """The deref'd source of an already-erased @dynamic assign (`p2 = p1`):
    `(*p1)` for a protocol pointer-local name, a bare `pet` for a `Pet&`
    param/reference, or a BORROW-returning call (`result: Pet = echo(dog)`
    -> `Pet* result = &echo(dog);`, `p = rc.get()` -- the call renders bare, the decl's
    `&` lift owns the address-of). Returns None for other source shapes
    (a protocol field / subscript keeps its own deref rules)."""
    src = _lower_ptr_name_src(init, lc, declared)
    if src is not None:
        return src
    # A method's borrow of a `@dynamic` payload is the generic `T` of a
    # handle (`rc.get()` on `Rc[Pet]`) at that instantiation.
    if isinstance(init, (TpyCall, TpyMethodCall)):
        fi = getattr(init, "resolved_function_info", None)
        if fi is not None and dyn_protocol_borrow_result(fi.return_type) is not None:
            _witness("decl.dyn_erased_call")
            return _lower_expr(init, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.RECEIVER))
    return None


@contextmanager
def _nested_def_lowering_scope(lc: _LowerCtx, func: TpyFunction,
                               declared: dict[str, TpyType], *,
                               self_captured: bool = False,
                               nonlocal_names: 'set[str] | frozenset[str]'
                               = frozenset()):
    """Nested-def scope, shared by the lambda and frame-member forms. Yields
    the body's `declared`: the enclosing one minus every name the nested
    def binds, plus its params. Those names are the nested def's own locals
    for its whole body (Python scoping), so they leave every inherited
    per-name fact too (`lc.shadow_scope`) -- a local or param spelled like a
    module global, an enclosing param or a frame field reads and writes its
    own binding. `nonlocal` names stay inherited: they ARE the enclosing
    binding."""
    own = scope_bound_names(func.body, (p for p, _t in func.params))
    with lc.shadow_scope(own, declared) as body_declared:
        body_declared.update(func.params)
        with _nested_def_state_scope(lc, func, self_captured=self_captured,
                                     nonlocal_names=nonlocal_names), \
                _bindings.at(lc, func):
            yield body_declared


@contextmanager
def _nested_def_state_scope(lc: _LowerCtx, func: TpyFunction, *,
                            self_captured: bool,
                            nonlocal_names: 'set[str] | frozenset[str]'):
    """Swap in the nested function's per-function state (its own prescan
    return slots / reassigned sets), INHERIT the outer classification sets
    for the names it does not bind (a captured name keeps its outer render),
    and restore everything after so body-added classifications don't leak
    out.
    `self_captured` keeps the receiver alive for a lambda whose capture list
    spells `this`."""
    saved = (lc.func, lc.prescan, lc.self_receiver, dict(lc.inline_narrowed),
             lc.in_nested_def, lc.signature, lc.staged_results)
    # The hoist-residue bookkeeping is per-function: seed the NESTED
    # function's own hoist facts (its try lowering drains them;
    # `_lower_nested_def` residue-checks after the body, mirroring
    # lower_function) and restore the OUTER set object untouched -- a
    # same-named nested try-hoist must not drain the outer's entry.
    saved_hoists = lc.unhandled_hoists
    own_hoisted = lc.analyzer.function_hoisted_vars.get(func, set())
    lc.unhandled_hoists = set(own_hoisted)
    prescan = nested_def_prescan(func, lc.prescan, lc.analyzer,
                                 nonlocal_names)
    lc.in_nested_def = True
    lc.func = func
    lc.prescan = prescan
    # The nested def returns from its own frame: no enclosing finally /
    # `with` runs at its `return`.
    lc.staged_results = 0
    # A lambda's parameter passing is its resolver spelling's, not
    # published here.
    lc.signature = definition_signature(
        tuple(t for _, t in func.params),
        func.return_type if isinstance(func.return_type, TpyType) else VoidType(),
        None, lc.analyzer)
    # The receiver survives ONLY when the capture list carries it: a captured
    # `self` renders `this->` inside the lambda exactly as in the enclosing
    # method body (the name render is scope-independent). Without the
    # capture, clearing keeps a slipped-through read failing loudly instead of
    # spelling a `this` the lambda does not hold.
    if not self_captured:
        lc.self_receiver = None
    # Deliberately NO param seeding: a lambda's params never run
    # `seed_param_locals` (they only enter local_scope_names), so a nested
    # param must not enter `pointers` / `movable_locals` -- classifying it
    # would emit a move/deref the plain lambda param cannot take. The param
    # shapes that would NEED seeding (Optional / Own /
    # value-opt) are rejected by `_lower_nested_def`'s param gate.
    # NB sema's nested_def_scope discards the nested body's facts unstored;
    # the scope-escape hoists are the one fact it keeps, in
    # `function_hoisted_vars` like any other function's.
    lc.inline_narrowed = {}
    try:
        with lc.branch_scope():
            yield
    finally:
        (lc.func, lc.prescan, lc.self_receiver, lc.inline_narrowed,
         lc.in_nested_def, lc.signature, lc.staged_results) = saved
        lc.unhandled_hoists = saved_hoists


def _nested_def_entry_reject(func, lc: '_LowerCtx', reason_for) -> None:
    """The entry gates shared by the LAMBDA and frame-MEMBER nested-def
    forms (kept in one place so the two cannot drift): a resumable nested
    func, a generic / @error_return signature, param defaults, and a
    `global` declaration all reject -- the last two because the shared
    `_nested_def_lowering_scope` premises assume them away (no global
    decls; no param seeding). Callers keep their own form-specific gates
    (capture spelling, name collision, param-type ladder) in their own
    order so reject tags stay stable.

    `reason_for` is a ZERO-ARG callable: `stmt_reject_reason` composes the
    note_detail tag at its own call time, so the reason must render AFTER
    the detail is noted -- a pre-evaluated string would drop the tag."""
    analyzer = lc.analyzer
    if func.is_async or func.is_generator:
        note_detail("nesteddef.resumable_func")
        raise ThirUnsupported(reason_for())
    if func.type_params or func.error_return is not None:
        note_detail("nesteddef.signature")
        raise ThirUnsupported(reason_for())
    # A param DEFAULT is unreachable here -- sema requires the argument at
    # every call of a nested def -- so the lambda's param list carries none
    # and the default is simply not rendered.
    if any(d is not None for d in func.defaults):
        _witness("nesteddef.unused_default")
    if analyzer.function_global_decls.get(func):
        note_detail("nesteddef.global_decl")
        raise ThirUnsupported(reason_for())


def _lower_nested_def(stmt: TpyNestedDef, scope: '_LowerScope') -> THIRStmt:
    """Lower `def name(...)` in a function body to a C++ lambda: the capture
    list spelled purely from sema's node facts, params and the non-void
    trailing return type through the resolver, and the body lowered under the
    nested function's own per-function state (`_nested_def_lowering_scope`).
    Out-of-slice shapes raise, rejecting the OUTER body."""
    lc = scope.lc
    analyzer = lc.analyzer
    func = stmt.func
    loc = getattr(stmt, "loc", None)
    begin_stmt()
    _nested_def_entry_reject(func, lc, lambda: stmt_reject_reason(stmt))
    # A captured `self` spells `this` in the capture list
    # (`self_captures_this`: the body renders the receiver through `this`, so
    # the capture is the pointer -- alias semantics in every capture mode).
    # Only the PLAIN method receiver is admitted; the resumable `__self` frame
    # member spells its own form. An enum companion's `self` is a by-value
    # member of a temporary wrapper, so it is copied (`self = self`).
    self_capturable = ((lc.self_receiver == "self" and lc.self_cpp == "this"
                        and lc.self_is_pointer)
                       or lc.member_self)
    if "self" in stmt.captured_names and not self_capturable:
        note_detail("nesteddef.self_capture")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    self_capture = _self_capture_cpp(lc)
    # A narrowed capture's reads rename to an OUTER extraction alias (or the
    # poly-narrow `(*__p_ptr)` spelling) the capture list does not carry, so
    # it rejects.
    if any(n in lc.narrow.narrowed or n in lc.narrow.spelled
           for n in stmt.captured_names):
        note_detail("nesteddef.narrowed_capture")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # Capture list, spelled from the node facts. A by-value entry here is NOT
    # checked for copyability the way the lambda entry builder checks its own
    # (BUGS.md#nesteddef-copy-capture-uncheckable): a captured record with
    # `__del__` is spelled bare and g++ rejects the deleted copy.
    if stmt.captured_names:
        if stmt.escapes:
            parts = []
            for n in stmt.captured_names:
                if n == "self":
                    parts.append(self_capture)
                    continue
                cpp_n = escape_cpp_name(n)
                if n in stmt.ref_captures:
                    parts.append(f"&{cpp_n}")
                elif n in stmt.move_captures:
                    parts.append(f"{cpp_n} = std::move({cpp_n})")
                else:
                    parts.append(cpp_n)
            capture = f"[{', '.join(parts)}]"
        else:
            capture = "[" + ", ".join(
                self_capture if n == "self" else f"&{escape_cpp_name(n)}"
                for n in stmt.captured_names) + "]"
    else:
        capture = "[]"
    closure_id, captures = capture_facts(stmt, lc, scope.declared)
    params_cpp = []
    for pname, ptype in func.params:
        if not isinstance(ptype, TpyType):
            note_detail("nesteddef.param_unresolved")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Param families the lambda BODY renders like a top-level function
        # without any param seeding: value scalars / char / enums, str
        # family, and every REFERENCE type -- records and containers alike
        # bind `T&` and are read bare, so the body renders the same with or
        # without seeding. The SIGNATURE does not: a lambda
        # param never gets the const-borrow verdict a top-level param gets,
        # and the non-const `T&` propagates to the enclosing function
        # (BUGS.md#nested-def-container-param-drops-const). Optional (either
        # repr) / Own /
        # value-opt / union / tuple params would need a pointer/movable
        # classification no lambda param gets -- and their render is
        # ill-formed today (see BUGS.md) -- so they reject.
        pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        if not (_eligible_scalar(pt) or _eligible_char(pt)
                or _eligible_enum(pt, analyzer) is not None
                or _resolved_str_value(pt, analyzer) is not None
                or _resolved_bytes_value(pt, analyzer) is not None
                or record_like(pt, analyzer)):
            note_detail("nesteddef.param_type")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        resolved = lc.render_resolve(ptype)
        params_cpp.append(resolved.to_cpp_param(escape_cpp_name(pname)))
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    resolved_ret = lc.render_resolve(rt)
    ret_cpp = (None if isinstance(resolved_ret, VoidType)
               else lc.render_type(resolved_ret))
    with _nested_def_lowering_scope(
            lc, func, scope.declared,
            self_captured="self" in stmt.captured_names,
            nonlocal_names=stmt.nonlocal_names) as body_declared:
        body = _lower_stmts(func.body, lc, body_declared,
                            function_body=True)
        # lower_function's hoist-residue check: a nested-body hoisted name
        # no try predecl accounted for is not lowered here.
        if lc.unhandled_hoists:
            note_detail("nesteddef.hoisted_vars")
            raise ThirUnsupported(stmt_reject_reason(stmt))
    _witness("stmt.nested_def")
    return THIRNestedDef(name=escape_cpp_name(func.name), capture_cpp=capture,
                         params_cpp=tuple(params_cpp), ret_cpp=ret_cpp,
                         body=body, loc=loc, closure_id=closure_id, captures=captures)


# The statements whose emitter declares pending named slots (a select's
# `THIRSlotEmplace`) before the statement's first line, for every
# expression the statement lowers itself: a `for` head, an `assert`, a
# `with` item or a `match` guard writes its own lines first, so it is not
# here, and neither is anything reached without a statement (a member-init
# list, a lambda body).
_SLOT_HOIST_STMTS = (TpyExprStmt, TpyVarDecl, TpyAssign, TpyAugAssign,
                     TpyReturn, TpyRaise, TpyIf, TpyWhile)


def _lower_stmt(stmt: TpyStmt, lc: _LowerCtx, declared: dict[str, TpyType],
                *, in_branch: bool = False,
                branch_decls_ok: bool = False,
                loop_depth: int = 0) -> THIRStmt:
    # Single chokepoint (nested statements route through here too).
    scope = _LowerScope(lc, declared, in_branch=in_branch,
                        branch_decls_ok=branch_decls_ok,
                        loop_depth=loop_depth)
    if lc.top_level_scope:
        # The module-init walk's position, for the one order-dependent
        # spelling it carries (see `pre_decl_import_cpp`). Like the
        # `current_stmt_line` stamp: EVERY statement, nested ones included,
        # and never restored when a block ends.
        sl = getattr(stmt, "loc", None)
        if sl is not None:
            lc.top_level_line = sl.line
    begin_stmt()
    slot_hoist_ok = lc.slot_hoist_ok
    # A resumable frame's `return` is spliced into its block by the frame
    # skeleton, which declares pending slots after that block.
    lc.slot_hoist_ok = (isinstance(stmt, _SLOT_HOIST_STMTS)
                        and not (isinstance(stmt, TpyReturn)
                                 and lc.resumable_leaf_mode))
    cursor = lc.cursor
    _bindings.enter(lc, stmt)
    try:
        try:
            result = _lower_stmt_dispatch(stmt, scope)
        except ThirUnsupported as ex:
            # Attribute the reject to the INNERMOST statement it unwound through,
            # so the strict diagnostic points at the offending line rather than at
            # the enclosing `def`. Set-if-empty: an inner frame already stamped a
            # nested statement, and the outer one must not overwrite it.
            if ex.loc is None:
                ex.loc = getattr(stmt, "loc", None)
            if not ex.detail:
                raise
            raise ThirUnsupported(stmt_reject_reason(stmt, ex.reason),
                                  loc=ex.loc) from None
        finally:
            lc.slot_hoist_ok = slot_hoist_ok
        # The finishing reads ask about the name the statement binds,
        # so they run at the statement's own scope node.
        result = _finish_stmt(result, lc, declared)
        stamp_bindings(result, stmt, lc)
        lc.storage_slots.stamp(result, stmt)
        return result
    finally:
        lc.cursor = cursor


def _declared_record(name: 'str | None', key: object,
                     lc: _LowerCtx) -> 'BindingRepr | None':
    """The record of the binding `key` (a statement, a with item, a match
    case) declares for `name`, else the one `name` resolves to where the
    walk is: a reseat's, or `UNBOUND` for a name the table does not bind
    (a synthesized slot)."""
    if name is None:
        return None
    rec = lc.table.declared_by(key, name) if key is not None else None
    return rec if rec is not None else lc.binding(name)


def _loop_var_record(var: str, key: object,
                     lc: _LowerCtx) -> 'BindingRepr | None':
    """A for statement's variable: the record its body sees -- the
    statement's own site may also hold a hoist of the same name, the
    predeclaration the loop writes back to."""
    if isinstance(key, TpyForEach) and key.body:
        node = lc.table.scope_at.get(key.body[0])
        rec = node.lookup(var) if node is not None else None
        if rec is not None:
            return rec
    return _declared_record(var, key, lc)


def _set_binding(node: object, field_name: str, value: object) -> None:
    # Stamped after the node is built, like `THIRExpr.source`: the record
    # is a fact about the node, not part of its identity.
    object.__setattr__(node, field_name, value)


def stamp_bindings(node: object, key: object, lc: _LowerCtx) -> None:
    """Stamp the binding-emitting nodes a statement's lowering returns with
    the binding table's record of what each declares, which the validator
    checks its representation against. A part the table keys apart from its
    statement (a with item, a match case's captures) carries its record from
    where it is built, at its own key; a nested statement's nodes were
    stamped by its own `_lower_stmt`."""
    if isinstance(node, THIRStmtSeq):
        for sub in node.stmts:
            stamp_bindings(sub, key, lc)
    elif not isinstance(node, THIRStmt):
        return
    elif node.bound_name is not None:
        if node.binding is None:
            _set_binding(node, "binding",
                         _declared_record(node.bound_name, key, lc))
    elif isinstance(node, THIRTupleUnpack):
        if not node.bindings:
            _set_binding(node, "bindings", tuple(
                _declared_record(n, key, lc) for n in node.targets))
        if node.tuple_target is not None and node.tuple_binding is None:
            _set_binding(node, "tuple_binding", _declared_record(
                node.tuple_target.name, key, lc))
    elif isinstance(node, (THIRForEach, THIRForIterProto, THIRForRange)):
        if node.binding is None:
            _set_binding(node, "binding", _loop_var_record(node.var, key, lc))
        if node.body:
            # The head unpack is lowered with the loop statement; its
            # targets are the records it declares itself.
            head = (key.body[0] if isinstance(key, TpyForEach) and key.body
                    and isinstance(key.body[0], TpyTupleUnpack)
                    and key.body[0].is_loop_head else key)
            stamp_bindings(node.body[0], head, lc)


def stamp_case_bindings(entry: object, case: object, lc: _LowerCtx) -> None:
    """A match arm entry's captures carry their case's records (a name
    sibling cases bind has one record per case)."""
    for mb in (entry.binding, *entry.pre_bindings, *entry.field_bindings):
        if mb is not None and mb.binding is None:
            _set_binding(mb, "binding", _declared_record(mb.name, case, lc))


def _finish_stmt(result: THIRStmt, lc: _LowerCtx,
                 declared: dict[str, TpyType]) -> THIRStmt:
    if isinstance(result, (THIRVarDecl, THIRPtrLocalDecl, THIRPtrLocalRebind)):
        name = result.name
    elif isinstance(result, THIRAssign) and isinstance(result.target, THIRName):
        name = result.target.name
    else:
        return result
    typ = declared.get(name)
    if typ is not None:
        # The statement's own write: its record, declared here or reassigned.
        rec = lc.declaring(name)
        union = _union_source_layout(name, typ, lc, rec)
        if union is not None:
            value = result.init if isinstance(result, (THIRVarDecl, THIRPtrLocalDecl)) else result.value
            result = replace(result, union_layout=union, union_literal=union_literal(value, union))
        pointer = rec.pointer
        layout = optional_layout(typ, lc.analyzer, borrow=pointer,
                                 readonly=rec.const)
        if layout is not None and (pointer or _value_opt_binding_kind(name, lc, rec) is ValueOptKind.SCALAR):
            result = replace(result, optional_layout=layout)
            # A bare name at an Optional destination copies the whole payload.
            attr = "init" if isinstance(result, (THIRVarDecl, THIRPtrLocalDecl)) else "value"
            value = getattr(result, attr)
            if (isinstance(value, THIRName) and value.optional_read is not None
                    and not value.deref):
                result = replace(result, **{attr: replace(value, optional_read=replace(
                    value.optional_read, extract=False))})
    if (isinstance(result, THIRVarDecl) and owned_container_decl(result)
            and (fact := native_container(result.resolved_type, result.is_const, lc.analyzer)) is not None
            and fact.readonly is result.is_const):
        result = replace(result, native_container=fact)
    if isinstance(result, (THIRVarDecl, THIRPtrLocalDecl)):
        placement = None
        if isinstance(result, THIRVarDecl):
            if (result.owned_storage is not None
                    or result.optional_layout is not None and storage_leaf(result.optional_layout.payload)
                    or result.union_layout is not None and all(
                        m is None or storage_leaf(m) for m in result.union_layout.elements)):
                placement = THIRStoragePlacement.SCOPE
        elif result.owned_storage is not None:
            if result.kind in (PtrSlotKind.OPT_RVALUE, PtrSlotKind.RECORD_RVALUE):
                placement = THIRStoragePlacement.SCOPE
            elif result.kind is PtrSlotKind.RECORD_HOISTED:
                placement = THIRStoragePlacement.BODY
        if placement is not None:
            result = replace(result, storage_placement=placement)
    return result


def _rebranch_taken(var: str, lc: _LowerCtx) -> 'frozenset[str]':
    """`lc.alias_taken()` for a BRANCH extraction of `var`, minus the alias
    `var` already has live. A branch body is its own C++ scope, so a
    re-narrowing of the SAME subject (the folded `if (true)` re-extraction, a
    nested isinstance) may keep the outer spelling: the inner declaration
    shadows an alias for the same object, which is what the reads inside want.
    Every other live alias belongs to another subject and still blocks."""
    taken = lc.alias_taken()
    prior = lc.narrow.narrowed.get(var)
    return taken - {prior} if prior is not None else taken


def _persistent_alias_name(var: str, lc: _LowerCtx) -> str:
    """A persistent extraction alias name: the branch spelling
    (`_narrow_alias_name`, clear of every binding the body makes and of every
    live alias), then suffix-bumped past persistent aliases already declared
    at the enclosing C++ scope. This alias lands at the CALLER's scope, so a
    collision here is a same-scope redeclaration, not a shadow.

    The persistent set is held out of the BASE ladder on purpose: a
    re-narrowing chain on one subject reads as `__p` / `__p_2`, not as `__p` /
    `__p_narrowed` (the `_narrowed` rung means "the user took `__p`")."""
    taken = lc.alias_taken()
    base = _narrow_alias_name(var, taken - lc.narrow.persistent_aliases)
    if base not in lc.narrow.persistent_aliases:
        return base
    n = 2
    while (f"{base}_{n}" in lc.narrow.persistent_aliases
           or f"{base}_{n}" in taken):
        n += 1
    return f"{base}_{n}"

def _single_fact_narrow(facts: dict, declared: dict[str, TpyType],
                        lc: _LowerCtx
                        ) -> 'tuple[str, UnionType, TpyType] | None':
    """`(var, union, member)` for a branch-fact map that narrows exactly ONE
    declared union subject to one of its members, or None. For a condition no
    narrowing arm claimed: the facts are all that is left of sema's narrowing,
    so anything wider than one var (or a var already narrowed, whose live
    alias the extraction would shadow) has no render."""
    if len(facts) != 1:
        return None
    var, ft = next(iter(facts.items()))
    if (var not in declared
            or var in lc.narrow.narrowed or var in lc.narrow.spelled
            or var in lc.prescan.global_seeded):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[var])))
    if not isinstance(u, UnionType):
        return None
    member = _narrow_fact_member(u, facts, var)
    return (var, u, member) if member is not None else None

def _make_narrow_alias(alias: str, var: str, member: TpyType, u: UnionType,
                       lc: _LowerCtx, loc) -> THIRNarrowAlias:
    """One U3 extraction alias -- the isinstance extractions' variant
    arm. The member template arg carries the pointer-variant `*` and
    the const-pointee prefix (`_narrow_subject_const`: a const local via the
    U2 field-lift chain, or a param under the deep-const verdict); the
    `const auto&` qualifier fires for value-type union PARAMS (the `const
    std::variant<...>` signature slot)."""
    member_cpp, is_ptr = _narrow_member_cpp(var, member, u, lc)
    # The param-const verdict covers value-type unions AND wrapper
    # unions (both bind `const auto&` from the const signature slot) --
    # and an `Own[union]` param, whose var_types entry is the OwnType
    # (`is_value_type()` True) despite the mutable `variant<A, B>&&` slot.
    pt = next((t for n, t in lc.params if n == var), None)
    ptu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
           if pt is not None else None)
    const_ref = (var in lc.prescan.param_names
                 and (u.is_value_type() or u.needs_wrapper()
                      or isinstance(ptu, OwnType)))
    return THIRNarrowAlias(alias=alias, variant_cpp=_narrow_variant_cpp(var, u, lc),
                           member_cpp=member_cpp,
                           is_ptr_variant=is_ptr, const_ref=const_ref, loc=loc,
                           union_extraction=_union_extraction(var, u, member, lc))

def _make_dyn_narrow_alias(alias: str, var: str, member: TpyType,
                           lc: _LowerCtx, declared: dict[str, TpyType],
                           loc) -> THIRDynNarrowAlias:
    """One polymorphic cast-and-cache extraction -- the isinstance
    extractions' poly arm (`[const ]Sub& __v =
    *<cast_rhs>;`), composed via the shared `_poly_cast_context` /
    `narrow_cast_rhs` chokepoints (which anchor a re-narrowing chain to the
    subject's original decl through `lc.narrow.poly_source`)."""
    member_cpp = lc.render_type(member)
    const, cast_arg, inner_src = _poly_cast_context(var, lc, declared)
    cast_rhs = narrow_cast_rhs(member_cpp, member, inner_src, cast_arg,
                               is_const=const, analyzer=lc.analyzer)
    return THIRDynNarrowAlias(alias=alias, member_cpp=member_cpp,
                              cast_rhs_cpp=cast_rhs, is_const=const, loc=loc)

def _register_dyn_narrow(alias: str, var: str, member: TpyType,
                         lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> None:
    """Extend the narrowing scope after a poly persistent extraction: reads
    rename to the alias, the original decl parks in `poly_source` (the cast
    anchor for a re-narrowing chain), and `declared` retypes to the member
    for the rest of the walk. NOT in `subject_union`/`persistent_narrowed`
    -- those key union-only consumers (the U4 re-assert bump)."""
    lc.narrow.persistent_aliases.add(alias)
    lc.narrow.narrowed[var] = alias
    lc.narrow.poly_source.setdefault(var, declared[var])
    declared[var] = member

def _lower_stmts(body, lc: _LowerCtx, declared: dict[str, TpyType],
                 *, in_branch: bool = False,
                 branch_decls_ok: bool = False,
                 loop_depth: int = 0,
                 top_level: bool = False,
                 function_body: bool = False) -> tuple[THIRStmt, ...]:
    """Lower a statement list, appending the U3 post-if extraction after an
    early-return narrowing `if` (the post-narrowing arm: a persistent,
    comment-less, statement-level alias) and extending the narrowing scope /
    retyping the subject for the REST of the list -- the enclosing branch/loop
    save-restore pops both, C++ scope semantics. A `function_body` list is
    a whole body, each statement of which anchors the rebind slots filled
    inside it (`StorageSlots`)."""
    out: list[THIRStmt] = []
    # A folded-True terminating @overload branch truncates the list it DIRECTLY
    # lives in. A nested list (loop/branch/try body) never breaks on the flag
    # (only the FUNCTION-LEVEL top_level loop does, the single buffered body)
    # -- but a fold inside it must not leak the flag OUT to
    # the enclosing top_level loop, where a reachable post-compound tail would
    # then be wrongly truncated. Save on entry / restore on exit scopes it.
    saved_overload_terminated = lc.overload_terminated
    for s in body:
        if top_level and lc.overload_terminated:
            break
        node = _lower_stmt(s, lc, declared, in_branch=in_branch,
                           branch_decls_ok=branch_decls_ok,
                           loop_depth=loop_depth)
        out.append(node)
        # The narrowing a statement leaves behind reads the bindings
        # visible at it.
        with _bindings.at(lc, s):
            _append_post_narrow(s, out, lc, declared)
    if function_body:
        lc.storage_slots.anchor(out)
    if not top_level:
        lc.overload_terminated = saved_overload_terminated
    return tuple(out)


def _append_post_narrow(s: TpyStmt, out: list[THIRStmt], lc: _LowerCtx,
                        declared: dict[str, TpyType]) -> None:
    """The extractions an `assert` or an early-return narrowing `if`
    leaves for the rest of its list."""
    if isinstance(s, TpyAssert):
        _append_assert_narrow(s, out, lc, declared)
    if not isinstance(s, TpyIf):
        return
    if isinstance(out[-1], THIRFoldedBlock):
        # A per-stub dead-branch fold emits no `if`, so the post-if
        # narrowing machinery never runs on it.
        return
    for var, post, u in _post_if_narrow_plan(s, declared, lc.narrow,
                                             lc.analyzer):
        alias = _persistent_alias_name(var, lc)
        if u is None:
            out.append(_make_dyn_narrow_alias(alias, var, post, lc,
                                              declared,
                                              getattr(s, "loc", None)))
            _register_dyn_narrow(alias, var, post, lc, declared)
            _witness("narrow.dyn_post_if")
            continue
        out.append(_make_narrow_alias(alias, var, post, u, lc,
                                      getattr(s, "loc", None)))
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.narrowed[var] = alias
        lc.narrow.subject_union[var] = u
        lc.narrow.persistent_narrowed.add(var)
        declared[var] = post
        _witness("narrow.union_post_if")


def _seed_literal_facts(facts: dict, lc: _LowerCtx) -> 'dict | None':
    """LiteralType branch facts -> the flow-sensitive `lc.literal_facts`
    (the isinstance extractions' LiteralType row; ==/!= narrowing folds
    against these). Returns the saved map to restore at branch exit, or None
    when no literal fact applies."""
    lits = {k: v for k, v in facts.items() if isinstance(v, LiteralType)}
    if not lits:
        return None
    saved = dict(lc.literal_facts)
    lc.literal_facts = {**saved, **lits}
    _witness("narrow.literal_branch_facts")
    return saved


def _literal_fact_fence(facts: dict, stmt, tag: str) -> None:
    """Reject a statement whose LiteralType facts would seed at a site this
    lowering does not model (assert's persistent seed, the while-body seed)
    -- a compare under the fact would then render plain instead of
    folding."""
    if any(isinstance(v, LiteralType) for v in facts.values()):
        note_detail(tag)
        raise ThirUnsupported(stmt_reject_reason(stmt))


def _append_assert_narrow(stmt: TpyAssert, out: 'list[THIRStmt]',
                          lc: _LowerCtx,
                          declared: dict[str, TpyType]) -> None:
    """Append the persistent extraction alias after a narrowing assert and
    extend the narrowing scope for the rest of the statement walk (a
    persistent extraction -- no restore; the enclosing branch/loop snapshot
    pops it). A re-assert on a
    persistently extracted subject re-runs the extraction with a
    suffix-bumped alias against the original union
    (`lc.narrow.subject_union`)."""
    loc = getattr(stmt, "loc", None)
    _literal_fact_fence(stmt.then_type_facts, stmt, "assert.literal_fact")
    ai = _assert_narrow_info(stmt, declared, lc.analyzer)
    if ai is not None:
        var, u, m = ai
        alias = _persistent_alias_name(var, lc)
        out.append(_make_narrow_alias(alias, var, m, u, lc, loc))
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.narrowed[var] = alias
        lc.narrow.subject_union[var] = u
        lc.narrow.persistent_narrowed.add(var)
        declared[var] = m
        return
    rb = _reassert_bump_info(stmt, declared, lc.narrow.persistent_narrowed,
                             lc.analyzer)
    if rb is not None:
        var, m = rb
        alias = _persistent_alias_name(var, lc)
        out.append(_make_narrow_alias(alias, var, m,
                                      lc.narrow.subject_union[var], lc, loc))
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.narrowed[var] = alias
        return
    pai = _poly_assert_narrow_info(stmt, declared, lc)
    if pai is None:
        return
    var, member = pai
    alias = _persistent_alias_name(var, lc)
    out.append(_make_dyn_narrow_alias(alias, var, member, lc, declared, loc))
    if var == lc.self_receiver:
        # The SELF subject's reads route through the spelled map (the same
        # substitution the if-init `(*__self_ptr)` install uses -- the
        # receiver arm never consults `narrowed`): `__self.bark()` off the
        # `const Dog& __self` reference alias. poly_source anchors a
        # re-narrowing chain to the ORIGINAL receiver type.
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.spelled[var] = alias
        lc.narrow.poly_source.setdefault(var, declared[var])
        declared[var] = member
    else:
        _register_dyn_narrow(alias, var, member, lc, declared)
    _witness("narrow.dyn_assert")

def _poly_assert_narrow_info(
        stmt: TpyAssert, declared: dict[str, TpyType],
        lc: _LowerCtx) -> 'tuple[str, TpyType] | None':
    """The polymorphic narrowing assert: `assert isinstance(v, Sub)` on a
    poly-dispatch subject with the single `{v: Sub}` fact -- it emits the
    negated null-check plus a PERSISTENT cast-and-cache alias (the
    extractions' poly arm). A re-assert on an already-narrowed subject
    admits (the alias bumps; the subclass fact keys on the ORIGINAL decl via
    `poly_source`). Sync only -- a resumable frame's alias would need the
    frame-field rename, so it rejects. Returns `(var, member)` or None."""
    if lc.resumable_leaf_mode:
        return None
    cond = stmt.condition
    anchored = _poly_anchor_declared(cond, declared, lc.narrow.poly_source)
    info = _poly_narrow_info(cond, anchored, lc.analyzer)
    if info is None:
        return None
    var, member, _dt = info
    facts = stmt.then_type_facts
    if list(facts) != [var] or facts[var] != member:
        return None
    if ((lc.binding(var).pointer
         and not polymorphic_source_is_pointer(anchored.get(var)))
            or var in lc.prescan.global_seeded
            or var in lc.narrow.spelled):
        return None
    return var, member

def _view_inner_opt_call_slot(stmt: TpyVarDecl, vtype: 'TpyType | None',
                              lc: _LowerCtx) -> bool:
    """A VIEW-inner value-opt slot (`StrView | None` ->
    `std::optional<std::string_view>`) from a same-typed CALL rvalue
    (`local = maybe_prefix(..)`): borrow and storage coincide for a view, so
    the decl is the plain spelled copy. CALL sources only -- a name/field
    source would be an alias whose own render is unwitnessed here."""
    if not _view_inner_value_opt(vtype):
        return False
    if not isinstance(stmt.init, (TpyCall, TpyMethodCall)):
        return False
    st = lc.analyzer.get_expr_type(stmt.init)
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st))) == \
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype)))


def _owned_view_opt_whole_src(stmt: TpyVarDecl, vtype: 'TpyType | None',
                              lc: _LowerCtx) -> bool:
    """Whether an owned-view Optional slot (`str | None` ->
    `optional<string>`) may take the bare WHOLE-optional copy of its init
    (`flat = rec.key;`). Two source shapes mis-render as a bare copy and are
    excluded: a view-form PARAM source needs the view->owned shim
    (`s ? make_optional(string(*s)) : nullopt`), and
    a NARROWED source occurrence (sema retyped the read to the inner) takes
    the deref path -- `optional<string> y = (*s);` does not even compile
    (string_view has no implicit conversion into optional<string>)."""
    analyzer = lc.analyzer
    if _value_opt_owned_view(vtype, analyzer) is None:
        return False
    src = stmt.init
    # A str/bytes LITERAL init is the bare exact copy
    # (`std::optional<std::string> src1 = "one";`), and a None literal the
    # nullopt init (`= std::nullopt;`). The literal proves the name
    # non-None (all later reads are NARROWED occurrences), which is safe
    # now that the value-opt read arms key whole-optional consumers on the
    # BINDING, not the narrow.
    if isinstance(src, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
        return True
    if isinstance(src, TpyName) and _opt_view_arg_shim(
            _param_declared_type(src.name, lc), vtype, analyzer):
        # A view-form PARAM source needs the view->owned shim
        # (`s ? make_optional(string(*s)) : nullopt`), not the bare copy.
        return False
    st = analyzer.get_expr_type(src) if src is not None else None
    if st is None or not isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st))),
            OptionalType):
        # A NARROWED source occurrence (sema retyped the read to the inner)
        # takes the deref path -- `optional<string> y = (*s);` does not
        # even compile.
        return False
    return True


def _owned_view_opt_decl_src(stmt: TpyVarDecl, vtype: 'TpyType | None',
                             lc: _LowerCtx) -> bool:
    """Whether an owned-view Optional slot (`str | None` ->
    `optional<string>`) takes a str/bytes VALUE init -- the source whose own
    type is the INNER, not the whole optional (`t: str | None = s` on a `str`
    param, a `StrView` local, a str field read).

    `std::optional<std::string>`'s converting ctor from a view is EXPLICIT,
    so a BORROW-form source constructs the inner first; the wrap is the
    shared `_wrap_view_owned_sink`, keyed on the lowered form, so an already
    owned source stays bare. Same verdict the RETURN sink reached
    (`ret.value_opt_view_materialize`) and the branch-first hoist takes
    through its predecl-plus-assign: str and bytes are value types, so the
    copy is unobservable."""
    analyzer = lc.analyzer
    if _value_opt_owned_view(vtype, analyzer) is None or stmt.init is None:
        return False
    src = stmt.init
    if (isinstance(src, TpyCoerce)
            and _coerce_disposition(src) == "materialize"):
        src = src.expr
    st = analyzer.get_expr_type(src)
    if st is None or isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st))),
            OptionalType):
        # A whole-optional source is `_owned_view_opt_whole_src`'s bare copy.
        return False
    return _resolved_viewfam_value(st, analyzer) is not None


def _lower_scoped_stmts(body, lc: _LowerCtx,
                        declared: dict[str, TpyType], *,
                        branch_decls_ok: bool = False,
                        loop_depth: int = 0) -> tuple[THIRStmt, ...]:
    """`_lower_stmts` under a `branch_scope()` pop: a branch or loop body.
    A post-if / assert narrowing made inside pops at the closing brace, and
    every branch-scoped lc name-set pops with it -- the caller copies
    `declared` per branch, so a branch-first decl's lc-set entries must not
    outlive the branch either (a same-named sibling-branch decl would
    otherwise classify against state its scope never saw)."""
    with lc.branch_scope():
        return _lower_stmts(body, lc, declared, in_branch=True,
                            branch_decls_ok=branch_decls_ok,
                            loop_depth=loop_depth)

def _lower_loop_orelse(orelse, lc: _LowerCtx, declared: dict[str, TpyType],
                       scope: '_LowerScope', face: str
                       ) -> tuple[THIRStmt, ...]:
    """Lower a loop's else block. It runs AFTER the loop frames pop (emitted
    past the loop's close brace), so it lowers at the OUTER loop
    depth -- a break inside it targets the enclosing loop -- over a fresh
    declared copy (its decls are block-scoped to the else `{...}`)."""
    if not orelse:
        return ()
    _witness(face)
    return _lower_scoped_stmts(orelse, lc, dict(declared),
                               branch_decls_ok=True,
                               loop_depth=scope.loop_depth)


def _lower_narrowed_branch(body, fact: 'TpyType | None', var: str,
                           u: 'UnionType | None', lc: _LowerCtx,
                           declared: dict[str, TpyType],
                           alias_loc, *,
                           loop_depth: int = 0,
                           make_alias=None,
                           any_subject: bool = False,
                           bind_spelled: 'str | None' = None
                           ) -> tuple[THIRStmt, ...]:
    """Lower one narrow-if branch under a narrowing-scope snapshot: a concrete
    member fact binds the subject (reads rename via `lc.narrow.narrowed`, or
    the poly-narrow `(*__p_ptr)` spelling via `lc.narrow.spelled`; the
    subject retypes for the branch walk); the snapshot pops at the closing
    brace. `alias_loc` is the if's loc for the then arm, but else_body[0]'s
    loc for the else arm.
    `make_alias(alias, fact, loc)` overrides the union extraction (the Any
    arm's any_cast alias); `u` may be None only when it is given.
    `bind_spelled` is the poly-narrow read spelling -- no alias statement
    exists (the if-init pre-bound the cast pointer)."""
    branch_declared = dict(declared)
    out: list[THIRStmt] = []
    with lc.branch_scope():
        if fact is not None:
            if bind_spelled is not None:
                lc.narrow.spelled[var] = bind_spelled
            else:
                # A same-spelled binding anywhere in the body (a local
                # declared later inside this very branch, a frame field in a
                # resumable body) is the user's own variable, and an outer
                # alias that BUMPED may already hold this spelling -- so the
                # mint registers itself for the branch it opens.
                alias = _narrow_alias_name(var, _rebranch_taken(var, lc))
                lc.narrow.live_aliases.add(alias)
                if make_alias is not None:
                    out.append(make_alias(alias, fact, alias_loc))
                else:
                    out.append(_make_narrow_alias(alias, var, fact, u, lc,
                                                  alias_loc))
                    # Consumers that key on the DECLARED type (var_types
                    # ignores narrowing) need the original union;
                    # branch_scope pops the entry.
                    lc.narrow.subject_union[var] = u
                lc.narrow.narrowed[var] = alias
                if any_subject:
                    lc.narrow.any_narrowed.add(var)
            branch_declared[var] = fact
        out.extend(_lower_stmts(body, lc, branch_declared, in_branch=True,
                                branch_decls_ok=True,
                                loop_depth=loop_depth))
    return tuple(out)


def _lower_compound_cond(cond: TpyExpr, isin: TpyExpr, info,
                         lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> THIRExpr:
    """Lower the U4 compound narrowing condition (`&&` tree with one
    isinstance leaf) in source order: the isinstance leaf renders as the
    holds test, and subject reads in leaves AFTER it lower to
    `THIRNarrowedRead` (the alias-free condition-position get -- sema
    retyped those reads). The fact installs into `lc.inline_narrowed` when
    the walk passes the isinstance leaf; the CALLER saves/restores it
    around the whole condition. Non-`&&` leaves lower through the normal
    expression path (compare char-targeting, chained compares, `not`)."""
    var, u, members, _folded = info
    return _lower_multi_compound_cond(cond, ((var, u, members, isin),),
                                      lc, declared)

def _lower_cond_tree(inner: TpyExpr, op: str, lc: _LowerCtx, *,
                     leaf_hit, lower_leaf, lower_other,
                     on_advance=None) -> THIRExpr:
    """The shared descent for the compound-condition walkers (multi-var
    `&&`, Any compound, `||` or-chain): same-op BinOp nodes compose
    THIRBinOp in source order; a leaf `leaf_hit` recognizes renders via
    `lower_leaf(hit, node)` (the `&&` flavors install their fact there);
    `on_advance(left_subtree)` runs between a node's sides (the `||`
    flavor's complement-install point); every other leaf lowers via
    `lower_other(node)`. Fact SEMANTICS stay per-flavor in the callbacks
    -- only the descent that must stay in lockstep is shared."""
    def walk(c: TpyExpr) -> THIRExpr:
        hit = leaf_hit(c)
        if hit is not None:
            return lower_leaf(hit, c)
        if isinstance(c, TpyBinOp) and c.op == op:
            left = walk(c.left)
            if on_advance is not None:
                on_advance(c.left)
            right = walk(c.right)
            return THIRBinOp(result_type=lc.analyzer.get_expr_type(c),
                             left=left, op=op, right=right, resolved=None,
                             loc=getattr(c, "loc", None))
        return lower_other(c)

    return walk(inner)


def _lower_multi_compound_cond(cond: TpyExpr, hits, lc: _LowerCtx,
                               declared: dict[str, TpyType]) -> THIRExpr:
    """The N-leaf generalization `_lower_compound_cond` delegates to: each
    isinstance leaf (matched by node identity) renders its holds test and
    installs its single-member fact; every other leaf lowers with the
    so-far-installed subjects retyped (sema narrowed those reads)."""
    by_leaf = {id(leaf): (var, u, members)
               for var, u, members, leaf in hits}

    def lower_leaf(hit, c):
        var, u, members = hit
        lowered = _lower_isinstance_cond((var, u, members, False), c, lc)
        if len(members) == 1:
            lc.inline_narrowed[var] = _inline_union_member(var, members[0],
                                                         u, lc)
        return lowered

    def lower_other(c):
        retypes = {var: members[0] for var, _u, members, _leaf in hits
                   if var in lc.inline_narrowed and len(members) == 1}
        active_declared = declared
        if retypes:
            active_declared = dict(declared)
            active_declared.update(retypes)
        return _lower_truthy(c, lc, active_declared)

    return _lower_cond_tree(cond, "&&", lc,
                            leaf_hit=lambda c: by_leaf.get(id(c)),
                            lower_leaf=lower_leaf, lower_other=lower_other)

def _lower_narrow_cond(cinfo, condition: TpyExpr, lc: _LowerCtx,
                       declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a narrowing condition from `_narrow_cond_info`'s 5-tuple: the
    simple form is the bare isinstance render; the compound form walks the
    `&&` tree (under an `lc.inline_narrowed` snapshot -- the inline-read
    fact must not leak past the condition; the branch/loop alias re-narrows
    for the body)."""
    var, u, members, folded, isin = cinfo
    if isin is None:
        negated = isinstance(condition, TpyUnaryOp) and condition.op == "!"
        inner = condition.operand if negated else condition
        base = _lower_isinstance_cond((var, u, members, folded), inner, lc)
        if not negated:
            return base
        return THIRUnaryNot(
            result_type=lc.analyzer.get_expr_type(condition),
            operand=base, loc=getattr(condition, "loc", None))
    saved = dict(lc.inline_narrowed)
    try:
        return _lower_compound_cond(
            condition, isin, (var, u, members, folded), lc, declared)
    finally:
        lc.inline_narrowed = saved

def _lower_or_chain_cond(oinfo, condition: TpyExpr, lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> THIRExpr:
    """The or-chain condition render: each isinstance leaf's holds test (a
    folded leaf renders `true` -- _lower_isinstance_cond's literal arm),
    composed along the source `||` tree; a leading `not` wraps the whole
    chain. At each `||` node the RIGHT side lowers with the left
    subtree's COMPLEMENT installed when it is a single concrete member
    (the false-branch remainder is installed per site); the CALLER
    saves/restores lc.inline_narrowed around the whole condition."""
    var, u, leaf_infos, negated = oinfo
    inner = condition.operand if negated else condition
    by_leaf = {id(leaf): (members, folded)
               for members, folded, leaf in leaf_infos}

    def checked(c: TpyExpr) -> 'set':
        hit = by_leaf.get(id(c))
        if hit is not None:
            return set(hit[0])
        if isinstance(c, TpyBinOp) and c.op == "||":
            return checked(c.left) | checked(c.right)
        return set()

    # The live complement MEMBER for the truthy-arm retype; tracked beside
    # the inline_narrowed install because the install stores only the
    # rendered member_cpp, not the type the retype needs.
    cur_member: list = [None]

    def lower_leaf(hit, c):
        members, folded = hit
        return _lower_isinstance_cond((var, u, members, folded), c, lc)

    def on_advance(left_subtree):
        done = checked(left_subtree)
        remaining = [m for m in u.members
                     if m not in done and not is_void_like_type(m)]
        if done and len(remaining) == 1:
            lc.inline_narrowed[var] = _inline_union_member(
                var, remaining[0], u, lc)
            cur_member[0] = remaining[0]
        else:
            lc.inline_narrowed.pop(var, None)
            cur_member[0] = None

    def lower_other(c):
        active_declared = declared
        if cur_member[0] is not None:
            active_declared = dict(declared)
            active_declared[var] = cur_member[0]
        return _lower_truthy(c, lc, active_declared)

    base = _lower_cond_tree(inner, "||", lc,
                            leaf_hit=lambda c: by_leaf.get(id(c)),
                            lower_leaf=lower_leaf, lower_other=lower_other,
                            on_advance=on_advance)
    if not negated:
        return base
    return THIRUnaryNot(
        result_type=lc.analyzer.get_expr_type(condition),
        operand=base, loc=getattr(condition, "loc", None))


def _lower_or_chain_if(stmt: TpyIf, oinfo, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc, *,
                       loop_depth: int = 0) -> THIRIf:
    """Lower an or-chain isinstance `if` over the shared chain skeleton:
    branch facts extract exactly as the simple form's (a UNION fact
    extracts nothing; a concrete fact -- including sema's dead-branch
    facts on a folded chain -- emits the alias)."""
    var, u, _leaf_infos, _negated = oinfo
    saved = dict(lc.inline_narrowed)
    try:
        cond = _lower_or_chain_cond(oinfo, stmt.condition, lc, declared)
    finally:
        lc.inline_narrowed = saved

    def fact_of(facts):
        return _narrow_fact_member(u, facts, var)

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, fact, var, u, lc, declared,
                                      alias_loc, loop_depth=loop_depth)

    return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                  declared, loc, loop_depth=loop_depth)


def _lower_narrow_if_shape(stmt: TpyIf, cond: THIRExpr, fact_of, branch_of,
                           lc: _LowerCtx, declared: dict[str, TpyType],
                           loc, *, loop_depth: int = 0) -> THIRIf:
    """The shared narrowing-`if` chain skeleton (union U3 and Any D15
    subjects): each branch lowers via `branch_of(body, fact, alias_loc)`
    with `fact_of(facts)` supplying the extraction fact; an elif
    continuation recurses, breaking the emitter's flat `else if` chain when
    the outer else-fact would extract (`else_is_nested`, gated on the
    else-branch carrying a concrete isinstance fact)."""
    then_fact = fact_of(stmt.then_type_facts)
    then_stmts = branch_of(stmt.then_body, then_fact, loc)
    else_stmts: tuple[THIRStmt, ...] = ()
    else_is_nested = False
    if stmt.else_body:
        inner = _elif_link(stmt)
        if inner is not None:
            # Elif continuation: no extraction at this level
            # (is_elif_continuation); the inner if handles its own narrowing.
            else_is_nested = _facts_have_concrete(stmt.else_type_facts)
            if else_is_nested:
                # The nested if is a body STATEMENT of the else block: its own
                # early-return post-if alias emits inside the block and its
                # narrowing scope pops at the closing brace.
                else_stmts = _lower_scoped_stmts(stmt.else_body, lc,
                                                 dict(declared),
                                                 branch_decls_ok=True,
                                                 loop_depth=loop_depth)
            else:
                # Flat chain: the chain-level post-if belongs to the enclosing
                # statement walk (_lower_stmts' post-if plan pass), so
                # the link lowers bare.
                else_stmts = (_lower_stmt(
                    inner, lc, dict(declared), in_branch=True,
                    branch_decls_ok=True,
                    loop_depth=loop_depth),)
        else:
            else_fact = fact_of(stmt.else_type_facts)
            else_stmts = branch_of(
                stmt.else_body, else_fact,
                getattr(stmt.else_body[0], "loc", None))
    return THIRIf(condition=cond, then_body=then_stmts,
                  else_body=else_stmts, else_is_nested=else_is_nested, loc=loc)


def _lower_narrow_if(stmt: TpyIf, info, lc: _LowerCtx,
                     declared: dict[str, TpyType], loc, *,
                     loop_depth: int = 0) -> THIRIf:
    """Lower a U3 isinstance-narrowing `if`: the holds_alternative condition
    (or the exhaustiveness fold's bare `true`) over the shared chain
    skeleton."""
    var, u, _members, _folded, _isin = info
    cond = _lower_narrow_cond(info, stmt.condition, lc, declared)

    def fact_of(facts):
        return _narrow_fact_member(u, facts, var)

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, fact, var, u, lc, declared,
                                      alias_loc, loop_depth=loop_depth)

    return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                  declared, loc, loop_depth=loop_depth)


def _lower_multi_narrow_if(stmt: TpyIf, hits, lc: _LowerCtx,
                           declared: dict[str, TpyType], loc, *,
                           loop_depth: int = 0) -> THIRIf:
    """Lower a multi-var compound narrowing `if` (`isinstance(a, A) and
    isinstance(b, B)`): the &&-composed holds tests, then one extraction
    alias per subject in source order (one extraction per fact). The
    caller's gate guarantees a
    then-fact per subject and no else arm."""
    saved = dict(lc.inline_narrowed)
    try:
        cond = _lower_multi_compound_cond(stmt.condition, hits, lc, declared)
    finally:
        lc.inline_narrowed = saved
    branch_declared = dict(declared)
    out: list[THIRStmt] = []
    with lc.branch_scope():
        for var, u, _members, _leaf in hits:
            fact = _narrow_fact_member(u, stmt.then_type_facts, var)
            assert fact is not None  # dispatcher gate
            # The subjects' aliases share one C++ scope, so each must also
            # clear the ones the earlier subjects took -- the registry the
            # branch scope pops carries them.
            alias = _narrow_alias_name(var, _rebranch_taken(var, lc))
            lc.narrow.live_aliases.add(alias)
            out.append(_make_narrow_alias(alias, var, fact, u, lc, loc))
            lc.narrow.subject_union[var] = u
            lc.narrow.narrowed[var] = alias
            branch_declared[var] = fact
        out.extend(_lower_stmts(stmt.then_body, lc, branch_declared,
                                in_branch=True, branch_decls_ok=True,
                                loop_depth=loop_depth))
    _witness("narrow.multi_var")
    return THIRIf(condition=cond, then_body=tuple(out), else_body=(),
                  else_is_nested=False, loc=loc)


def _folded_narrow_info(
        cond: TpyExpr, lc: _LowerCtx,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool] | None':
    """The F1 fold: `isinstance(v, T)` on an ALREADY-narrowed subject --
    sema resolved the condition statically (`macro_expansion` is the
    True/False literal), so the render is the bare `if (true)`/`if (false)`
    plus a SHADOWING re-extraction from the ORIGINAL union (the extraction
    targets the original variable, never the live alias). Returns
    `(var, union, check_members, value)` or None;
    the original union comes from `lc.narrow.subject_union` (the branch
    fact retyped `declared`, so `_isinstance_narrow_info` cannot see it)."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if cond.isinstance_type_param:
        return None
    me = cond.macro_expansion
    if not isinstance(me, TpyBoolLiteral):
        return None
    var = cond.isinstance_var
    if var not in lc.narrow.narrowed:
        return None
    u0 = lc.narrow.subject_union.get(var)
    if u0 is None:
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    if not all(any(m == cm for m in u0.members) for cm in members):
        return None
    return var, u0, members, me.value


def _reject_const_narrowed_write_recv(tobj, lc: _LowerCtx, stmt) -> None:
    """A PARAM subject's extraction alias is `const auto&` (value-type and
    wrapper unions alike), so a WRITE through it is uncompilable C++
    (pre-existing const-verdict bug, see BUGS.md). Reject; shared by the
    plain and augmented subscript-write paths so the two cannot drift."""
    if (isinstance(tobj, TpyName)
            and (u0 := lc.narrow.subject_union.get(tobj.name)) is not None
            and tobj.name in lc.prescan.param_names
            and (u0.is_value_type() or u0.needs_wrapper())):
        note_detail("setitem.const_narrowed_recv")
        raise ThirUnsupported(stmt_reject_reason(stmt))


def _lower_folded_narrow_if(stmt: TpyIf, finfo, lc: _LowerCtx,
                            declared: dict[str, TpyType], loc, *,
                            loop_depth: int = 0) -> THIRIf:
    """Lower an F1 folded narrowing `if`: the literal condition over the
    shared chain skeleton, the branch alias re-extracted from the ORIGINAL
    union (folded-FALSE extracts the CHECKED member -- sema's dead-branch
    fact)."""
    var, u0, _members, value = finfo
    cond = THIRLiteral(result_type=lc.analyzer.get_expr_type(stmt.condition),
                       value=value, loc=getattr(stmt.condition, "loc", None))
    _witness("narrow.folded_isinstance")

    def fact_of(facts):
        return _narrow_fact_member(u0, facts, var)

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, fact, var, u0, lc, declared,
                                      alias_loc, loop_depth=loop_depth)

    return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                  declared, loc, loop_depth=loop_depth)


def _lower_any_isinstance_cond(ainfo, condition: TpyExpr,
                               lc: _LowerCtx) -> THIRExpr:
    """The D15 Any-isinstance condition render: the has_value + typeid
    check(s), with the negated-polarity `!` wrap mirroring the union path."""
    var, members, negated, _isin = ainfo
    inner = condition.operand if negated else condition
    base = THIRAnyIsinstance(
        result_type=lc.analyzer.get_expr_type(inner),
        subject_cpp=var,
        member_cpps=tuple(lc.render_type(m) for m in members),
        loc=getattr(inner, "loc", None))
    if not negated:
        return base
    return THIRUnaryNot(
        result_type=lc.analyzer.get_expr_type(condition),
        operand=base, loc=getattr(condition, "loc", None))


def _lower_any_compound_cond(cond: TpyExpr, isin: TpyExpr, var: str,
                             member: TpyType, op: str, lc: _LowerCtx,
                             declared: dict[str, TpyType]) -> THIRExpr:
    """The compound Any-isinstance condition walk: the isinstance leaf
    renders the typeid probe; on an `&&` spine, leaves after it read the
    subject through the condition-scoped any_cast spelling (an inline
    fact installed into `lc.narrow.spelled` -- the CALLER
    saves/restores). A `||` spine
    installs nothing (Any has no complement fact)."""
    def lower_leaf(_hit, c):
        lowered = THIRAnyIsinstance(
            result_type=lc.analyzer.get_expr_type(c),
            subject_cpp=var,
            member_cpps=(lc.render_type(member),),
            loc=getattr(c, "loc", None))
        if op == "&&":
            cpp = lc.render_type(member)
            lc.narrow.spelled[var] = (
                f"std::any_cast<const {cpp}&>({var}.value)")
        return lowered

    def lower_other(c):
        active_declared = declared
        if var in lc.narrow.spelled:
            active_declared = dict(declared)
            active_declared[var] = member
        return _lower_truthy(c, lc, active_declared)

    return _lower_cond_tree(cond, op, lc,
                            leaf_hit=lambda c: True if c is isin else None,
                            lower_leaf=lower_leaf, lower_other=lower_other)


def _lower_dyn_narrow_if(stmt: TpyIf, pinfo, lc: _LowerCtx,
                         declared: dict[str, TpyType], loc, *,
                         loop_depth: int = 0) -> THIRIf:
    """Lower a polymorphic-isinstance `if` (dyn-protocol / polymorphic-base
    subject): the C++17 if-init condition over the shared chain skeleton,
    composed via the shared `narrow_cast_rhs` chokepoint."""
    var, member, _var_decl = pinfo
    analyzer = lc.analyzer
    cpp_type = lc.render_type(member)
    # The shared const / cast-arg / source-inner triple (the if-init
    # clause consults the const-borrow-source rule -- the param const verdicts
    # + the readonly-declared arm -- and polymorphic_cast_arg's
    # pointer-vs-address split).
    const, cast_arg, inner_src = _poly_cast_context(var, lc, declared)
    const_pfx = "const " if const else ""
    ptr_local = _fresh_narrow_local(f"__{var}_ptr", lc.alias_taken())
    cast_rhs = narrow_cast_rhs(
        cpp_type, member, inner_src,
        cast_arg, is_const=const, analyzer=analyzer)
    inner = stmt.condition
    cond = THIRDynIsinstance(
        result_type=analyzer.get_expr_type(inner),
        init_cpp=f"{const_pfx}{cpp_type}* {ptr_local} = {cast_rhs}",
        ptr_local=ptr_local,
        loc=getattr(inner, "loc", None))

    def fact_of(facts):
        ft = facts.get(var)
        return ft if ft == member else None

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, fact, var, None, lc, declared,
                                      alias_loc, loop_depth=loop_depth,
                                      bind_spelled=f"(*{ptr_local})")

    # The if-init pointer's C++ scope is the whole `if`, both arms included,
    # so a narrowing nested in either must bump past it.
    with lc.live_alias(ptr_local):
        return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                      declared, loc, loop_depth=loop_depth)


def _lower_dyn_multi_if(stmt: TpyIf, minfo, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc, *,
                        loop_depth: int = 0) -> THIRIf:
    """The tuple-form polymorphic isinstance `if` (`isinstance(v, (A, B))`):
    the no-init OR-chain of `(dynamic_cast<const A*>(v) != nullptr)` checks
    composed via the shared `narrow_cast_rhs` chokepoint. No extraction
    alias -- the branch fact is the checked union, so the body lowers with
    the subject's own render."""
    var, members, _var_decl = minfo
    analyzer = lc.analyzer
    cond = THIRDynIsinstanceMulti(
        result_type=analyzer.get_expr_type(stmt.condition),
        checks_cpp=_poly_cast_checks(var, members, lc, declared),
        loc=getattr(stmt.condition, "loc", None))
    _witness("narrow.poly_tuple")

    def fact_of(facts):
        return None

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, None, var, None, lc, declared,
                                      alias_loc, loop_depth=loop_depth)

    return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                  declared, loc, loop_depth=loop_depth)


def _lower_any_narrow_if(stmt: TpyIf, ainfo, lc: _LowerCtx,
                         declared: dict[str, TpyType], loc, *,
                         loop_depth: int = 0) -> THIRIf:
    """Lower a D15 Any-isinstance-narrowing `if`: the typeid condition over
    the shared chain skeleton, with the any_cast extraction alias."""
    var, members, _negated, isin = ainfo
    if isin is None:
        cond = _lower_any_isinstance_cond(ainfo, stmt.condition, lc)
    else:
        saved_spelled = dict(lc.narrow.spelled)
        try:
            cond = _lower_any_compound_cond(
                stmt.condition, isin, var, members[0], stmt.condition.op,
                lc, declared)
        finally:
            lc.narrow.spelled = saved_spelled

    def fact_of(facts):
        return _any_narrow_fact(members, facts, var)

    def make_alias(alias, fact, alias_loc):
        return THIRAnyNarrowAlias(
            alias=alias, subject_cpp=var, member_cpp=lc.render_type(fact), loc=alias_loc)

    def branch_of(body, fact, alias_loc):
        return _lower_narrowed_branch(body, fact, var, None, lc, declared,
                                      alias_loc, loop_depth=loop_depth,
                                      make_alias=make_alias,
                                      any_subject=True)

    return _lower_narrow_if_shape(stmt, cond, fact_of, branch_of, lc,
                                  declared, loc, loop_depth=loop_depth)


def _lower_container_elem_value(stmt: TpyAssign, eu: TpyType,
                                target: THIRExpr, lc: _LowerCtx,
                                declared: dict[str, TpyType],
                                loc) -> THIRExpr:
    """The value of a write into a CONTAINER element slot (`g["a"] = ...`
    with a list/dict/set element), shared by the builtin container write and
    a user `__setitem__` whose value slot is a container.

    A MOVE-source same-type container NAME moves in whole (`g["a"] = a` ->
    `__setitem__(g, "a", std::move(a))`); a by-value CALL rvalue forwards
    bare, statement-flushed; a comprehension builds the container in place;
    a container LITERAL renders against the slot -- with the type prefix off
    the bounds-safe lvalue path, since the checked `__setitem__` template
    cannot deduce a bare brace-init. Copy-shaped names reject here (their
    warn-copy render is unwitnessed on the builtin write)."""
    analyzer = lc.analyzer
    v = stmt.value
    vt = analyzer.get_expr_type(v)
    vt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
          if vt is not None else None)
    # An `Own[T]`-returning callee is a by-value rvalue of T; the NAME row
    # keeps the un-peeled comparison.
    vt_own = unwrap_readonly(vt.wrapped) if isinstance(vt, OwnType) else vt
    if (isinstance(v, TpyName)
            and v.name not in lc.narrow.narrowed
            and not lc.binding(v.name).pointer
            and vt == eu
            and _is_move_source(v, lc)):
        _witness("setitem.container_move")
        return THIRMove(
            result_type=eu,
            value=_lower_expr(v, lc, declared, allow_unrouted_name=True),
            form=Form.STORAGE, loc=loc)
    if (isinstance(v, (TpyCall, TpyMethodCall))
            and vt_own == eu
            and is_rvalue_source(analyzer, v)):
        _witness("setitem.container_rvalue")
        return _flush_witness(
            "flush.assign",
            _lower_expr(v, lc, declared,
                        use=_ExprUse(
                            result=_ExprResultUse.STORAGE,
                            # The element SLOT holds the value past the
                            # statement; name the sink so a borrow of
                            # storage the statement kills cannot land in it.
                            pos=SinkPos.SETITEM_VALUE,
                            forms=_NO_FORMS,
                            allow_temps=True)))
    if declared_call_elem_copy_ok(v, eu, analyzer):
        # A borrow-declared call handing back the slot's container: the
        # owning element copies it, as the record value of the same store
        # does.
        _witness("setitem.declared_call_copy")
        return lower_copy_construct(v, eu, lc, declared, slot_type=eu,
                                    loc=loc)
    if (type(v) in _comprehensions._COMP_KINDS
            and _container_comp_arg(v, eu)):
        _witness("setitem.container_comp")
        return _comprehensions._lower_comprehension(
            v, eu, lc, declared,
            _comp_shadow_pointers(lc.names_with("pointer"), declared, analyzer))
    if not isinstance(v, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        note_detail("setitem.container_value_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    value = _lower_expr(v, lc, declared, use=_ExprUse(slot_target=eu))
    if not target.bounds_safe and isinstance(value, THIRContainerLiteral):
        value = replace(value, typed_brace_cpp=lc.render_type(eu))
    _witness("setitem.container_value")
    return value


def _lower_slice_assign(stmt: TpyAssign, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc) -> THIRStmt:
    """`c[a:b] = v` / `c[a:b:s] = v` -> the resolved slice `__setitem__`'s
    @native free function (list_set_slice / bytearray_set_stepped_slice /
    ...) over the receiver, the slice initializer, and the RHS. The receiver
    passes the read arm's gate (`_slice_recv_ok`); bounds are eligible
    fixed-int exprs. A non-empty array-literal RHS wears the
    std::vector<E>{...} type prefix the checked helper needs to deduce Range."""
    analyzer = lc.analyzer
    sub = stmt.target
    sl = sub.index
    fi = sub.slice_function_info
    # A user record's own slice `__setitem__` is a method, not the free
    # function this node renders.
    if not (fi.native_function and fi.native_name
            and _slice_recv_ok(sub, None, lc, declared, write=True)
            and _slice_index_ok(sl, declared, analyzer)):
        note_detail("setitem.slice")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # The variant's value parameter; one taking the value's elements (a
    # protocol, `Iterable[...]`) holds the value at its own type, as an
    # argument there is.
    target_type = analyzer.get_expr_type(
        stmt.value if store_takes_elements(fi) else sub)
    recv = _lower_slice_recv(sub.obj, lc, declared, loc)
    # A generator-FACTORY call RHS (`d[1:3] = gen_values()`) is consumed by
    # the helper as an iterable, not stored: it takes the iterable-position
    # admission (the same bare factory render a for-each source gets), which
    # the storage slot set does not carry.
    v_fi = getattr(stmt.value, "resolved_function_info", None)
    rhs_use = (_ExprUse(result=_ExprResultUse.ITERABLE)
               if isinstance(stmt.value, TpyCall) and v_fi is not None
               and v_fi.is_generator
               else _ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True))
    value = _lower_expr(
        stmt.value, lc, declared,
        use=replace(rhs_use, slot_target=target_type))
    if _node_moves(value):
        value = THIRMove(result_type=value.result_type, value=value,
                         form=value.form, loc=loc)
    # A non-empty array-literal RHS renders `{...}`; the checked helper cannot
    # deduce its Range from a bare brace, so it takes the std::vector<E>
    # prefix, E the element the variant's value parameter iterates. Empty
    # literals already spell their own type; other sources (names, calls)
    # land bare.
    value_vector_cpp = None
    value_param = unwrap_qualifiers(fi.params[-1].type)
    if (isinstance(stmt.value, TpyArrayLiteral) and stmt.value.elements
            and store_takes_elements(fi) and value_param.type_args):
        value_vector_cpp = lc.render_type(
            unwrap_own(unwrap_qualifiers(value_param.type_args[0])))
    _witness("setitem.slice")
    return THIRSliceAssign(
        receiver=recv,
        native_name=fi.native_name,
        value=value,
        lower=_lower_slice_bound(sl.lower, lc, declared, loc),
        upper=_lower_slice_bound(sl.upper, lc, declared, loc),
        step=_lower_slice_bound(sl.step, lc, declared, loc),
        stepped=sub.is_stepped_slice,
        value_vector_cpp=value_vector_cpp,
        loc=loc,
    )


def _leaf_finally_crossing_loop(stmt: TpyTry) -> bool:
    """Whether a leaf try/finally contains a `break`/`continue`: RETURN
    crossings ride the leaf finally bridge (the emit records the frame on
    the finally stack for _make_async_return's walk), but a break/continue
    crossing binds the loop-frame walk, which is not bridged -- those keep
    rejecting. The nested walk is deliberately conservative: it does not
    track which loop a break binds to, so a break bound by a loop INSIDE
    the try rejects too (over-rejecting costs a compile error,
    under-rejecting a wrong render)."""
    stack = [s for body in stmt.sub_bodies() for s in body]
    while stack:
        s = stack.pop()
        if isinstance(s, (TpyBreak, TpyContinue)):
            return True
        for body in s.sub_bodies():
            stack.extend(body)
    return False


def result_plan(value: THIRExpr, slot: 'Slot', lc: '_LowerCtx',
                reason: 'Callable[[], str]', *, explicit_copy: bool = False,
                detail: 'Callable[[], str] | None' = None) -> Plan:
    """How a body's result slot takes the lowered value it hands back
    (`convert`). A pair with no conversion rejects under `reason()`
    (asked once the detail is noted): the row's detail where it has one,
    else `return.<refusal>`."""
    # A read built beside `_lower_expr` (a field source, the receiver as
    # an object) is stamped here: what it IS still rides the node.
    plan = convert(stamped(value, lc), slot, lc.analyzer,
                   explicit_copy=explicit_copy)
    if isinstance(plan, Refuse):
        note_detail(detail() if detail is not None
                    else "return." + plan.key)
        raise ThirUnsupported(reason(), message=plan.message)
    if plan.row is not None:
        _witness("return." + plan.row)
    return replace(plan, expr=spelled(plan, lc.render_type))


def lower_result(v: TpyExpr, slot: 'Slot', lc: '_LowerCtx',
                 declared: dict[str, TpyType], reason: 'Callable[[], str]', *,
                 detail: 'Callable[[], str] | None' = None) -> THIRExpr:
    """THE value a body hands back -- a `return`'s or a lambda body's:
    the stored read of its source for the result slot (`read_stored`),
    taken to the slot (`convert`)."""
    # A result has no statement of its own to demote to: it takes a
    # temporary exactly where its region hosts one and it does not lend.
    read = _field_write.read_stored(v, slot, lc, declared,
                                    allow_temps=result_hosts_temps(slot, lc))
    if read.row is not None:
        _witness("ret." + read.row)
    # A folded literal renders against the result type (literal
    # construction).
    value = _slot_literal_retype(read.expr, slot.declared, lc)
    return result_plan(_flush_witness("flush.return", value), slot, lc,
                       reason, explicit_copy=read.explicit_copy,
                       detail=detail).expr


def _return_tuple_literal(v: TpyTupleLiteral, stmt: TpyStmt, ret_slot: 'Slot',
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          scope: '_LowerScope', loc) -> 'THIRExpr | None':
    """LITERAL construction of a tuple literal at a tuple result slot: the
    spelled brace-init against the slot, each element landing in its
    element slot. None for a slot no tuple builder spells (the generic
    literal render takes it)."""
    analyzer = lc.analyzer
    rdecl = ret_slot.declared

    def lowered(slot_t: TupleType) -> THIRExpr:
        try:
            # An element's call hoists its `__tmp_N` decl ahead of the
            # statement where the region hosts one.
            return _lower_tuple_literal(
                v, slot_t, lc, declared,
                use=_ExprUse(allow_temps=result_hosts_temps(ret_slot, lc)))
        except ThirUnsupported as ex:
            raise ex.with_context(stmt_reject_reason(
                stmt, f"return.tuple_source:{ex.reason}")) from None

    ret_nt = (unwrap_readonly(unwrap_send_sync(rdecl))
              if _optional_borrow_tuple(rdecl, analyzer) is not None
              else None)
    ret_tuple = _borrow_tuple_return_type(
        ret_nt.inner if ret_nt is not None else rdecl, analyzer)
    if ret_tuple is not None:
        # The borrow tuple built in place: the per-element `&(...)` and the
        # spelled `std::tuple<...>` prefix; a declared readonly result
        # spells const element pointers.
        value = _lower_borrow_tuple_literal(
            v, ret_tuple, lc, declared, target_readonly=ret_slot.const)
        _witness("ret.btuple_literal")
        return value
    ret_vot = _value_opt_tuple(rdecl, analyzer)
    if ret_vot is not None:
        # A value-repr Optional[value tuple]: the brace-init of the INNER
        # tuple, which the optional's converting ctor absorbs.
        value = lowered(_value_opt_tuple_inner(ret_vot, analyzer))
        _witness("ret.value_opt_tuple_literal")
        return value
    ret_vt = (_value_tuple_return(rdecl, analyzer)
              or _union_elem_value_tuple(rdecl, analyzer))
    if ret_vt is not None:
        value = lowered(ret_vt)
        _witness("ret.tuple_literal")
        return value
    ret_wrt = _wrapper_ref_tuple_return(rdecl, analyzer)
    if ret_wrt is not None:
        # The REFERENCE-element tuple (`return (t, 0)` at `-> tuple[
        # Tree[int32], int32]` -> `std::tuple<Tree<int32_t>&, int32_t>{t,
        # 0}`): wrapper members are plain lvalue NAMES rendered bare (the
        # reference member binds the lvalue), scalar members ride the
        # container-elem rows. The spelling is the borrow form.
        ok = len(v.elements) == len(ret_wrt.element_types)
        if ok:
            for i, sub in enumerate(v.elements):
                eslot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    ret_wrt.element_types[i])))
                if _wrapper_union_like(eslot, analyzer) is not None:
                    if not (isinstance(sub, TpyName)
                            and sub.name in declared
                            and sub.name not in scope.admission_pointers()
                            and sub.name not in lc.narrow.narrowed
                            and unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(declared[sub.name])))
                            == eslot):
                        ok = False
                        break
                elif not _container_lit_elem_ok(
                        sub, ret_wrt.element_types[i], declared, analyzer,
                        threaded=True, forced=True,
                        pointers=scope.admission_pointers()):
                    ok = False
                    break
        if not ok:
            note_detail("return.wrapper_ref_tuple_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("ret.wrapper_ref_tuple")
        return THIRBorrowTupleLiteral(
            result_type=ret_wrt, spelled_cpp=ret_wrt.to_cpp_return(),
            elements=tuple(
                _lower_container_elem(v.elements[i],
                                      ret_wrt.element_types[i], lc, declared,
                                      tuple_elem=True)
                if _wrapper_union_like(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        ret_wrt.element_types[i]))), analyzer) is None
                else _lower_expr(v.elements[i], lc, declared)
                for i in range(len(v.elements))),
            addr_of=(False,) * len(v.elements), form=Form.BORROW, loc=loc)
    ret_ost = _own_storage_tuple_return(rdecl, analyzer)
    if ret_ost is not None:
        # The Own[tuple] STORAGE result (`-> Own[tuple[str, Resource]]`):
        # the spelled brace-init of storage-direct members, each lowered
        # through the container-element rows against its slot; a
        # non-value member must be an rvalue, a movable name at its last
        # use, or a borrow sema declared a copy of.
        ok = len(v.elements) == len(ret_ost.element_types)
        if ok:
            for i, sub in enumerate(v.elements):
                mslot = _unwrap_own(ret_ost.element_types[i])
                mbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    mslot)))
                copied = own_element_copy_member(
                    sub, own_element_payload(ret_ost.element_types[i]),
                    analyzer)
                if copied is not None:
                    if not copied:
                        note_detail("return.own_element_copy_source")
                        ok = False
                        break
                    continue
                if (not mbare.is_value_type()
                        and not is_rvalue_source(analyzer, sub)
                        and not (isinstance(sub, TpyName)
                                 and _is_move_source(sub, lc))):
                    ok = False
                    break
                if not _container_lit_elem_ok(
                        sub, mslot, declared, analyzer, threaded=True,
                        forced=True, allow_record=True, allow_nested=True,
                        allow_optional=True,
                        pointers=scope.admission_pointers()):
                    ok = False
                    break
        if not ok:
            note_detail("return.tuple_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("ret.own_storage_tuple")
        return THIRTupleLiteral(
            result_type=ret_ost,
            elements=tuple(
                _lower_copy_special(
                    v.elements[i], "copy", _unwrap_own(
                        ret_ost.element_types[i]), lc, declared,
                    getattr(v.elements[i], "loc", None))
                if own_element_copy_member(
                    v.elements[i], own_element_payload(
                        ret_ost.element_types[i]),
                    analyzer) is not None
                else _lower_container_elem(
                    v.elements[i], _unwrap_own(ret_ost.element_types[i]),
                    lc, declared, tuple_elem=True)
                for i in range(len(v.elements))),
            loc=loc)
    ret_sgt = _generic_value_tuple_return(rdecl, analyzer)
    if ret_sgt is not None:
        # The generic tuple: per-element `to_val_or_ptr` wraps (the
        # resumable arm's exact builder).
        _witness("ret.generic_tuple_literal")
        return _lower_generic_tuple_literal(v, ret_sgt, lc, declared)
    ret_bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rdecl)))
    if isinstance(ret_bt, TupleType) and lends_elements(ret_slot):
        # The borrow tuple whose elements the F1 slice does not spell (a
        # container element): the same per-element builder, each borrowed
        # element taking the address of the lvalue it names.
        value = _lower_borrow_tuple_literal(
            v, ret_bt, lc, declared, target_readonly=ret_slot.const)
        _witness("ret.btuple_literal")
        return value
    return None


_RETURN_LITERALS = (TpyNoneLiteral, TpyIntLiteral, TpyFloatLiteral,
                    TpyBoolLiteral, TpyStrLiteral, TpyBytesLiteral,
                    TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                    TpyListRepeat, TpyTupleLiteral)


def result_value(v: TpyExpr, slot: 'Slot', lc: '_LowerCtx',
                 declared: dict[str, TpyType], scope: '_LowerScope',
                 stmt: TpyStmt) -> THIRExpr:
    """THE value a body hands out of a result slot -- a `return`'s or a
    generator `yield`'s (`stmt`, which rejects name): a literal built
    against the slot (None, a tuple, a container, a closure), else the
    stored read of the source taken to the slot (`lower_result`)."""
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    rh, rst, rdecl = slot.holds, slot.type, slot.declared
    if rh is SlotHolds.RELOCATE:
        # A result slot no conversion is known for.
        note_detail("return.slot_type")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    lit = _peel_coerce(v)
    if isinstance(v, TpyNoneLiteral) or (
            isinstance(lit, TpyNoneLiteral)
            and _own_wrapper_return(rdecl, analyzer) is not None):
        # LITERAL construction: None as the slot's own empty value --
        # `nullptr` where the slot is a pointer, else `std::nullopt` /
        # the empty alternative of a union.
        _witness("ret.none_literal")
        return THIRLiteral(
            result_type=_resolve_plain_alias(rst, analyzer), value=None,
            form=(Form.BORROW if rh in (SlotHolds.POINTER, SlotHolds.BORROWS)
                  else Form.STORAGE), loc=loc)
    if rh is SlotHolds.ERASED and isinstance(lit, CONTAINER_LITERAL_NODES):
        # LITERAL construction: a container literal's brace-init has no
        # type to deduce inside the erasing wrap.
        note_detail("return.slot_type")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if isinstance(v, TpyTupleLiteral):
        tuple_lit = _return_tuple_literal(v, stmt, slot, lc, declared,
                                          scope, loc)
        if tuple_lit is not None:
            return tuple_lit
    rec_storage = (_record_storage_return(rdecl, analyzer)
                   if rh is SlotHolds.OWNS else None)
    if rec_storage is not None and isinstance(
            v, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        # A container LITERAL at the storage slot renders
        # position-independently (the decl-init brace-init / spelled
        # ctor emits verbatim, the empty `std::vector<T>{}` spell
        # included), the slot as its target type; the literal re-checks
        # the decl gate's element slice.
        if not _container_literal_shape_ok(v, rec_storage, analyzer):
            note_detail(_record_source_reject_detail(
                v, scope.admission_pointers(), lc.narrow.narrowed.keys(),
                False))
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("ret.container_literal")
    elif rec_storage is not None and isinstance(v, TpyListRepeat):
        # `return [label] * 3` -- the repeat arm renders the from_range
        # build target-typed by the storage slot (the untargeted
        # resolve would demote to the Array flavor).
        _witness("ret.container_repeat")
        return _lower_expr(v, lc, declared,
                           use=_ExprUse(slot_target=rec_storage))
    elif (rec_storage is not None
          and type(v) in _comprehensions._COMP_KINDS):
        # A comprehension renders the decl-init arm's stmt-expr,
        # target-typed by the storage slot.
        comp = _comprehensions._lower_comprehension(
            v, rec_storage, lc, declared, scope.admission_pointers())
        _witness("ret.container_comp")
        return comp
    ret_ow = _own_wrapper_return(rdecl, analyzer)
    if (ret_ow is not None
            and isinstance(lit, (TpyArrayLiteral, TpyDictLiteral))
            and _ru_container_literal_ok(lit, analyzer)):
        _witness("ret.own_wrapper_literal")
        return _flush_witness(
            "flush.return", _lower_ru_literal(lit, ret_ow, lc, declared))
    if (_own_genrec_return(rdecl) is not None
            and isinstance(lit, (TpyArrayLiteral, TpyDictLiteral))
            and _ru_instance_literal_ok(lit, analyzer)):
        _witness("ret.genrec_literal")
        return _flush_witness(
            "flush.return", _lower_ru_literal(
                lit, analyzer.get_expr_type(lit), lc, declared))
    if (isinstance(v, TpyLambda) and isinstance(rdecl, CallableType)
            and not rdecl.is_template):
        # `return lambda x: ...` at a Callable slot: the escaping
        # by-value closure (_lower_lambda's captures_by_value arm).
        _witness("ret.closure_lambda")
        return _lower_lambda(v, lc, declared)
    if not isinstance(lit, _RETURN_LITERALS) or (
            rec_storage is None and isinstance(lit, (
                TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                TpyListRepeat))):
        return lower_result(v, slot, lc, declared,
                            lambda: stmt_reject_reason(stmt))
    # LITERAL construction: the literal renders against the result
    # slot (a container literal spells the record-storage slot).
    value = _flush_witness(
        "flush.return",
        _lower_expr(v, lc, declared, use=replace(
            _field_write.stored_use(slot, rst, analyzer,
                                    allow_temps=result_hosts_temps(slot, lc)),
            slot_target=(rec_storage if isinstance(
                v, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral))
                else None))))
    if rec_storage is not None:
        # A result may be wrapped (a generator's or an `@error_return`
        # body's `std::expected`), which no bare brace list-initializes.
        value = _spell_brace(value, rec_storage, lc)
    # A float literal returned from a float32 function takes the `f`
    # suffix (the result type threads into the render).
    return _slot_literal_retype(value, rdecl, lc)


def _lower_resumable_return_value(ret: TpyReturn, lc: '_LowerCtx',
                                  declared: dict[str, TpyType]) -> 'THIRExpr':
    """POSITION-BLIND value render for `_make_async_return`'s scaffolding
    sites (pending-slot store / pre-finally capture / direct ready), shared
    by the ReturnT terminator arm and nested leaf returns: the scaffolding
    binds the value to a `<ret_cpp> __tpy_async_ret = <value>;` local (whose
    decl type supplies the conversion) and only then wraps it in Poll::ready
    -- so a literal at a wider slot stays bare `42`, NOT the sync-return
    arm's target-typed `::tpy::BigInt(42)`. Lower the value directly,
    bypassing `_lower_stmt`'s return-coercion arm. Two exceptions render
    target-typed anyway: the owned str/bytes slot's view->owned copy, the
    result slot's row (`convert.view_to_owned`), and value-tuple /
    generic-tuple literals (the tuple-literal-targeted arm)."""
    rslot = return_slot(lc)
    if _eligible_char(rslot.type) and isinstance(ret.value, TpyStrLiteral):
        raise ThirUnsupported(stmt_reject_reason(ret))
    if (record_like(rslot.type, lc.analyzer)
            and isinstance(ret.value, (TpyArrayLiteral, TpyDictLiteral,
                                       TpySetLiteral))
            and not ret.value.children()):
        # An EMPTY container literal is the one shape the position-blind tail
        # cannot serve: the type is spelled only when a target is passed
        # (the T*-ambiguity guard), and this render has none -- so it would
        # emit a bare `= {}` instead of
        # `std::vector<T>{}`. The sync return slot IS targeted, hence its
        # opposite rule.
        note_detail("return.empty_container_literal")
        raise ThirUnsupported(stmt_reject_reason(ret))
    ret_vopt = _value_opt_return(rslot, lc.analyzer)
    if ret_vopt is not None:
        # Value-repr Optional[scalar] slot (`std::optional<T>
        # __tpy_async_ret = ...`): None spells the STORAGE literal
        # (std::nullopt), a value-opt param/local name passes the WHOLE
        # optional bare (deref-on-narrow stripped, the sync return arm's
        # rule); other scalar sources ride the position-blind tail (the
        # optional's converting ctor absorbs the bare scalar). A
        # coerce-wrapped value-opt name mirrors the sync arm's reject.
        if isinstance(ret.value, TpyNoneLiteral):
            return THIRLiteral(result_type=ret_vopt, value=None,
                               form=Form.STORAGE,
                               loc=getattr(ret.value, "loc", None))
        if (isinstance(ret.value, TpyName)
                and _value_opt_binding_kind(ret.value.name, lc)
                in (ValueOptKind.SCALAR, ValueOptKind.RECORD)):
            return replace(
                _lower_expr(ret.value, lc, declared,
                            allow_whole_optional=True),
                deref=False)
        peeled = _peel_coerce(ret.value)
        if (isinstance(peeled, TpyName)
                and (peeled.name in lc.prescan.value_opt_params
                     or lc.binding(peeled.name).value_opt
                     is ValueOptKind.SCALAR)):
            note_detail("return.optval_coerced_param")
            raise ThirUnsupported(stmt_reject_reason(ret))
    ret_vt = (_value_tuple_return(rslot.declared, lc.analyzer)
              or _union_elem_value_tuple(rslot.declared, lc.analyzer))
    if ret_vt is not None and isinstance(ret.value, TpyTupleLiteral):
        # A value-tuple literal renders the spelled brace-init against the
        # return slot (the sync return arm's render: value-opt element
        # slots spell std::nullopt, Own-element names move); the
        # scaffolding's `<ret_cpp> __tpy_async_ret = <value>;` decl
        # consumes it position-independently, like the frame-field flavor.
        # Off-slice element shapes reject inside _lower_tuple_literal
        # (rejecting the body); name/call sources ride the position-blind
        # tail below (bare renders -- the decl absorbs the copy/move).
        value = _lower_tuple_literal(ret.value, ret_vt, lc, declared)
        _witness("res.return_tuple_literal")
        return value
    if (ret_vt is not None and isinstance(ret.value, TpyName)
            and _own_tuple_borrow_lift_arg(ret.value, ret_vt, lc, declared)):
        # The sync return's declared-copy lift: a borrow-form tuple name
        # into Own element slots is copied into storage element by element.
        # The position-blind decl below would bind it bare, and a borrow
        # tuple does not convert to the storage one.
        _witness("ret.own_tuple_borrow_lift")
        return _own_tuple_name_copy(ret.value, ret_vt, lc, declared)
    ret_gt = _generic_value_tuple_return(rslot.declared, lc.analyzer)
    if ret_gt is not None and isinstance(ret.value, TpyTupleLiteral):
        # A generic tuple literal renders the spelled brace-init with
        # per-element `to_val_or_ptr` wraps (the val_or_ptr form under the
        # tuple-literal-targeted return); non-literal sources ride the
        # position-blind tail below.
        value = _lower_generic_tuple_literal(ret.value, ret_gt, lc, declared)
        _witness("res.return_generic_tuple")
        return value
    if _unlifted_declared_copy(ret.value, lc):
        note_detail("return.own_element_copy_source")
        raise ThirUnsupported(stmt_reject_reason(ret))
    # A str-family FIELD source reads bare into the scaffolding's
    # `<ret_cpp> __tpy_async_ret = <value>;` decl (`__self.name`), the same
    # read the sync return tail prechecks -- the frame's receiver respelling
    # is the receiver machinery's job, not this arm's.
    res_str_field = (isinstance(ret.value, TpyFieldAccess)
                     and _viewfam_return_slots(
                         rslot.declared, lc.analyzer)[0] is not None
                     and _str_field_value_read(ret.value, declared,
                                               lc.analyzer)
                     and _witness("res.return_str_field"))
    # `return copy(x)` at an owned-record async return: the copy-construct
    # rvalue (`C __tpy_async_ret = C(__self);`) -- the sync return sink's
    # interception row (the special-builtin gate rejects copy() in the
    # generic call tail, so every sink intercepts it itself). The default
    # excluded-source set, NOT the sync sink's narrower admission_pointers
    # override (preserved there verbatim for a filed defect).
    if (ret.value is not None
            and _record_storage_return(rslot.declared,
                                       lc.analyzer) is not None):
        copy_row = _lower_copy_record(ret.value, lc, declared,
                                      loc=getattr(ret, "loc", None))
        if copy_row is not None:
            _witness("res.return_copy_record")
            return copy_row
    form = async_return_form(lc.func.return_type)
    _ret_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        lc.func.return_type)))
        if isinstance(lc.func.return_type, TpyType) else None)
    # Storage Optional[F1-record] slot: `return None` spells the STORAGE
    # nullopt (the scalar value-opt row's record twin -- ret_value_opt
    # itself stays scalar-keyed for the sync arm); every other source
    # rides the position-blind tail (the optional's converting ctor).
    _ret_sopt = _storage_optional_return_type(_ret_bare, lc.analyzer)
    if _ret_sopt is not None and isinstance(ret.value, TpyNoneLiteral):
        _witness("res.return_opt_record_none")
        return THIRLiteral(result_type=_ret_sopt, value=None,
                           form=Form.STORAGE,
                           loc=getattr(ret.value, "loc", None))
    # The ptr-repr Optional FIELD lift (`return h.opt` at `-> Box | None`):
    # the whole `std::optional<T>` member lifts to the `T*` payload via
    # optional_to_ptr -- the sync ret.ptr_opt_field row's async twin.
    if (form is AsyncReturnForm.BORROW
            and isinstance(_ret_bare, OptionalType)
            and isinstance(ret.value, TpyFieldAccess)
            and not lc.func.is_property_getter
            and _field_receiver_ok(ret.value, declared, lc.analyzer)
            and _optional_ptr_borrow_wide(
                lc.analyzer.get_expr_type(ret.value), lc.analyzer)
            is not None):
        _witness("res.return_ptr_opt_field")
        return THIRFormConvert(
            result_type=_ret_bare,
            value=_lower_field_source(ret.value, lc, declared),
            form=Form.BORROW, loc=getattr(ret, "loc", None))
    # The self-FIELD borrow rung (`return self.inner` ->
    # `Server* __tpy_async_ret = &(__self.inner);`): the field read is a
    # borrow lvalue, so it lowers BORROW_BIND (the STORAGE use below
    # would take the record-copy family's reject).
    borrow_self_field = (
        form is AsyncReturnForm.BORROW
        and isinstance(ret.value, TpyFieldAccess)
        and isinstance(ret.value.obj, TpyName)
        and ret.value.obj.name == lc.self_receiver
        and not lc.self_is_pointer
        and _field_markers_clean(ret.value))
    # The scaffolding's `<ret_cpp> __tpy_async_ret = <value>;` decl is a
    # STORAGE sink like the sync return slot, so the value lowers with the
    # sync return arm's result use. allow_temps stays off: the skeleton
    # composes the render into its own line and has no verified flush
    # point -- a temp-hoisting value keeps rejecting.
    # An owned return direct-initializes the payload, so a ternary takes its
    # prvalue `?:`. A select's slot is not admitted: inside a nested block the
    # skeleton declares it after the block that uses it.
    # The move facts sit on the lowered name, which the view-owned wrap
    # may enclose. A record payload is taken as a value: a pointer-bound
    # source reads through its deref, as the sync result's stored read does.
    value_forms = (_ONLY_SELECT_PRVALUE | _ONLY_INDIRECT_READ
                   if _ret_bare is not None and record_like(_ret_bare, lc.analyzer)
                   else _ONLY_SELECT_PRVALUE)
    raw_value = _lower_expr(ret.value, lc, declared,
                            field_prechecked=res_str_field,
                            use=_ExprUse(result=(_ExprResultUse.BORROW_BIND
                                                 if borrow_self_field
                                                 else _ExprResultUse.STORAGE),
                                         forms=(_NO_FORMS
                                                if form is AsyncReturnForm.BORROW
                                                else value_forms)))
    # The result slot's own conversions the payload takes so far: a view
    # source's owned copy and the open-`T` param lift.
    plan = (view_to_owned(raw_value, rslot, lc.analyzer)
            or open_param_result(raw_value, rslot))
    value = raw_value if plan is None else plan.expr
    if plan is not None and plan.row is not None:
        _witness("return." + plan.row)
    if form is AsyncReturnForm.BORROW:
        # Pointer-payload family (bare reference-type returns): the SELF
        # rung -- `return self` lifts the receiver lvalue
        # (`Res* __tpy_async_ret = &(__self);`) -- and the self-FIELD
        # rung above. Every other source (alias names, the return-await
        # forward's `__ret0` scaffolding) rejects.
        if ((isinstance(ret.value, TpyName)
                and ret.value.name == lc.self_receiver
                and not lc.self_is_pointer)
                or borrow_self_field):
            _witness("res.return_self_borrow")
            return THIRCoerce(
                result_type=value.result_type, expr=value,
                coercion_name="async_ret_addr_of",
                wrap="&({0})",
                loc=getattr(ret, "loc", None))
        note_detail("return.borrow_form")
        raise ThirUnsupported(stmt_reject_reason(ret))
    if form is AsyncReturnForm.TRAIT:
        # Generic `-> T` slot (`val_or_ptr_t<T>`, `val_or_cptr_t<T>` for a
        # declared `-> readonly[T]` -- the frame's payload type): the trait
        # lift. The move gate skips non-STORAGE forms, so no THIRMove
        # composes with this wrap.
        declared = unwrap_ref_type(unwrap_send_sync(lc.func.return_type))
        t_cpp = lc.render_type(unwrap_readonly(declared))
        trait = ("val_or_cptr_t" if is_readonly_result_type(declared)
                 else "val_or_ptr_t")
        return THIRCoerce(
            result_type=value.result_type, expr=value,
            coercion_name="async_ret_val_or_ptr",
            wrap=(f"::tpy::to_val_or_ptr<::tpy::{trait}<{t_cpp}>>"
                  f"({{0}})"),
            loc=getattr(ret, "loc", None))
    # The direct-ready last-use move. The record's `movable` is deliberately
    # partial (owned-tuple params and the gen_async force-seeds are
    # missing), but those shapes reject upstream. Baked position-blind; the
    # emit hook unwraps the THIRMove at pre-finally scaffolding sites
    # (render_return_value's allow_move). Bare names only (a coerce-wrapped
    # source renders a fresh conversion temp); STORAGE-form slots only
    # (borrow/trait forms alias, which the move gate's form check excludes).
    if (isinstance(ret.value, TpyName)
            and _node_moves(raw_value)):
        vt = lc.analyzer.get_expr_type(ret.value)
        vt = unwrap_readonly(vt) if vt is not None else None
        if vt is not None and not vt.copy_is_cheap():
            value = THIRMove(result_type=value.result_type, value=value,
                             loc=getattr(ret, "loc", None))
    return value


def unpack_claim_agrees(stmt: TpyTupleUnpack, lc: _LowerCtx) -> bool:
    """Whether the targets an unpack of a tuple NAME owns (`is_owned`, the
    walk's tags taken before the body settled) are the elements its
    record moves: a target the record does not move would take an element
    out of storage the source does not own, so the unpack refuses it."""
    rec = read_record(lc.analyzer.ctx, stmt.value)
    if rec is None:
        return True
    return all(owned == rec.element_moves(i)
               for i, owned in enumerate(stmt.is_owned)
               if any(p[:1] == (i,) for p in rec.leaves))


@dataclass(frozen=True)
class _UnpackElems:
    """Where a statement unpack's targets read their elements: the source
    place itself, or the statement's holder of the source (`holder`,
    evaluated once: `ref` binds an lvalue by reference, else the value is
    the statement's own)."""
    receiver: THIRExpr
    layout: TupleType
    elems: 'tuple[Source, ...]'
    holder: 'THIRExpr | None' = None
    ref: bool = False
    read_id: 'int | None' = None
    # The receiver is a loop's step, which the head reads in place: the
    # elements the iteration hands over are the head's own.
    step: bool = False

    @property
    def owns(self) -> bool:
        """Whether the elements an owned target takes stay in storage the
        statement holds as its own for the target's life: its value holder,
        or the loop's step."""
        return self.step or (self.holder is not None and not self.ref)

    def read(self, i: int) -> ElemRead:
        return ElemRead(self.receiver, i,
                        storage_type(self.layout.element_types[i]),
                        self.elems[i], None, step=self.step)

    def refers_into(self, i: int) -> bool:
        """Whether a target referring to element `i` may: an element held
        inline in a value holder is the holder's COPY of a source no
        construction made fresh, where CPython aliases the source's, and a
        select's arms are no root sema follows a reference's writes to."""
        h = self.holder
        if h is None:
            return True
        if h.select_result is not None:
            return False
        return (self.ref or h.source.prvalue
                or self.elems[i].pointer_held)


def _target_refers(stmt: TpyTupleUnpack, i: int, lc: '_LowerCtx') -> bool:
    """Whether target `i` keeps referring into its element: a reference or
    pointer binding, or a view."""
    rec = _bindings.fresh_record(lc.table, stmt, stmt.targets[i])
    if rec is not None and rec.form in (BindingForm.REFERENCE,
                                        BindingForm.POINTER,
                                        BindingForm.POINTER_UNION):
        return True
    t = _bindings.unpack_target_type(stmt, i, lc.analyzer)
    return holds_borrowing_view(_resolve_pending_view(t, lc.analyzer) or t)


def _durable_lvalue(e: THIRExpr) -> bool:
    """An lvalue naming storage that outlives the statement. C++ makes a
    conditional over two such lvalues of one type an lvalue too, whatever
    its value-typed select reads as."""
    if isinstance(e, THIRIfExpr) and e.select_result is not None:
        return all(_durable_lvalue(a) and storage_type(a.result_type)
                   == storage_type(e.result_type)
                   for a in (e.then, e.orelse))
    s = e.source
    return not s.prvalue and s.temporary is False


def _unpack_elems(stmt: TpyTupleUnpack, value: THIRExpr,
                  lc: '_LowerCtx', *, consumes: bool = False
                  ) -> _UnpackElems:
    """The holder rule, decided once per source: a place no target writes a
    name of is read in place, element by element; any other lvalue the
    statement holds by reference (`auto&&`), a value by value (`auto`),
    each evaluated once. An owned target binds into a value holder, which
    lives as long as the statement's block. A place the statement
    `consumes` (a resumable frame's one-shot slot, read once) is held by
    reference, its elements the statement's to move."""
    s = value.source
    written = ({n for n in stmt.targets if n is not None}
               & read_names(stmt.value))
    if stmt.is_loop_head and not consumes:
        # A loop head reads its loop's step in place; an element sema's
        # per-target record owns (`is_owned`) is the head's to move.
        layout = layout_tuple(value)
        if layout is None:
            raise ThirUnsupported("stmt.tuple_unpack", loc=stmt.loc)
        elems = tuple(
            replace(e, movable=True, temporary=False)
            if stmt.owns_target(i)
            else replace(e, movable=False)
            for i, e in enumerate(tuple_elems(s, layout)))
        return _UnpackElems(value, layout, elems, step=True)
    if s.place and not written and not consumes:
        layout = layout_tuple(value)
        if layout is None:
            raise ThirUnsupported("stmt.tuple_unpack", loc=stmt.loc)
        return _UnpackElems(value, layout, tuple_elems(s, layout))
    ref = consumes or _durable_lvalue(value)
    if ref and value.select_result is not None and any(
            _target_refers(stmt, i, lc)
            for i, n in enumerate(stmt.targets) if n is not None):
        # A target referring into the select (a reference, a pointer, a
        # view) would refer to the selected source, whose reseat sema does
        # not follow through the select: the statement holds its own copy.
        ref = False
    read_id = next(lc.temp_read_ids)
    # The holder is storage of the statement's block, in the form its
    # source renders: a value holder's elements are the statement's to
    # move, a reference holder's the source's. A one-shot slot holds its
    # result in storage form.
    held = (Held.STORAGE if consumes or s.held is Held.FRESH else s.held)
    owns = consumes or not ref
    hs = replace(s, held=held, temporary=False, place=True, prvalue=False,
                 id_read=False, captured=False,
                 movable=True if owns else s.movable,
                 elems=None if owns else s.elems,
                 binding=None if owns else s.binding)
    receiver = THIRTempRead(result_type=value.result_type, form=value.form,
                            read_id=read_id, source=hs, loc=value.loc)
    layout = layout_tuple(receiver)
    if layout is None:
        raise ThirUnsupported("stmt.tuple_unpack", loc=stmt.loc)
    return _UnpackElems(receiver, layout, tuple_elems(hs, layout),
                        holder=value, ref=ref, read_id=read_id)


def _unpack_convert(r: ElemRead, slot: Slot, lc: '_LowerCtx', loc, *,
                    reason: str = "stmt.tuple_unpack") -> THIRExpr:
    """Element `r` converted at its target's slot."""
    plan = convert(r.at(slot), slot, lc.analyzer)
    if isinstance(plan, Refuse):
        note_detail("tuple_unpack.elem_" + plan.key)
        raise ThirUnsupported(reason, loc=loc, message=plan.message)
    return spelled(plan, lc.render_type)


def _local_slot(tt: TpyType, form: BindingForm, construct: SlotConstruct,
                lc: '_LowerCtx', *, const: bool = False) -> Slot:
    # A str / bytes local is the view or the owned buffer its usage decided.
    tt = _resolve_pending_view(tt, lc.analyzer) or tt
    slot = slot_for(tt, SlotPosition.LOCAL, lc.analyzer, construct=construct,
                    form=form, result_const=const)
    assert slot is not None
    return slot


def _owned_elem_movable(stmt: TpyTupleUnpack, i: int, el: Source,
                        loc) -> None:
    """An owned target takes its element's object only where the read
    moves it: a pointer element whose pointee no read record covers, or a
    borrowed one, has no object to take, and an element the read does not
    move would be copied where the target owns."""
    if not el.movable:
        if el.pointer_held and isinstance(stmt.value, TpyName):
            note_detail("ptr_tuple.borrowed_path_sink")
            moved = [j for j, o in enumerate(stmt.is_owned) if o]
            raise ThirUnsupported(
                stmt_reject_reason(stmt),
                message=borrowed_path_element_message(
                    stmt.value.name, i, arity=len(stmt.is_owned),
                    copies=moved),
                loc=loc)
        note_detail("tuple_unpack.owned_source_not_movable")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)


def _head_ref_cpp(stmt: TpyTupleUnpack, tt: TpyType,
                  lc: '_LowerCtx') -> str:
    """The pointee a reference or pointer target spells: a loop head's
    deduces the step's const (`auto&` / `auto*`) -- an iterator over a const
    source yields const elements its record cannot see -- a statement's
    names its type."""
    if stmt.is_loop_head:
        return "auto"
    return lc.render_type(unwrap_readonly(tt))


def _fresh_unpack_write(stmt: TpyTupleUnpack, i: int, rec: BindingRepr,
                        src: _UnpackElems, lc: '_LowerCtx',
                        declared: dict[str, TpyType], loc) -> THIRStmt:
    """A target the statement declares, written as its scalar declaration
    `x = <element i>` is: its record's form says what the local holds -- a
    reference into the source's element, a pointer to it, the element's
    own value, or (an owned element) its object, held in place in the
    statement's holder or moved into the local's own storage."""
    an = lc.analyzer
    name = stmt.targets[i]
    tt, cpp = _unpack_target_decl(unwrap_ref_type(rec.type), an,
                                  lc.render_type)
    owned = stmt.owns_target(i)
    r = src.read(i)
    refers = (rec.form in (BindingForm.POINTER, BindingForm.POINTER_UNION)
              or (rec.form is BindingForm.REFERENCE
                  and not unwrap_readonly(tt).is_value_type()))
    if not owned and refers and not src.refers_into(i):
        note_detail("tuple_unpack.ref_source_form")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    if rec.loop_binding is LoopBinding.OWNED:
        # The element's object stays in the statement's value holder (or
        # the loop's step), which the target refers to (one move at a
        # consuming read, none at a read-only one).
        if not src.owns:
            note_detail("tuple_unpack.owned_target_holder")
            raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
        value = _unpack_convert(r, _local_slot(tt, BindingForm.VALUE,
                                               SlotConstruct.DIRECT_INIT, lc),
                                lc, loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type="auto&&", form=Form.STORAGE,
                           binding=rec, loc=loc)
    if (owned and rec.rebind_slot and src.owns
            and not rec.storage_hoisted and not lc.top_level_scope):
        # A rebound target whose first storage is the statement's value
        # holder or the loop's step (which lives as long as the target's
        # block) points at the element in place; a rebind re-points it. A
        # module's names are module storage, which no init temporary is.
        value = _unpack_convert(r, _local_slot(tt, BindingForm.POINTER,
                                               SlotConstruct.DIRECT_INIT, lc,
                                               const=rec.const), lc, loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type=lc.render_type(unwrap_readonly(tt)),
                           form=Form.BORROW, is_const=rec.const,
                           cpp_local_representation=LocalBinding.POINTER,
                           binding=rec, loc=loc)
    if owned and rec.rebind_slot:
        # A rebound target keeps the object it takes over in a slot of its
        # own, which its pointer re-points away from at a rebind.
        value = _unpack_convert(r, _local_slot(tt, BindingForm.VALUE,
                                               SlotConstruct.DIRECT_INIT, lc),
                                lc, loc)
        declared[name] = tt
        if rec.storage_hoisted:
            return THIRPtrLocalDecl(name=name, resolved_type=tt,
                                    kind=PtrSlotKind.RECORD_HOISTED,
                                    init=value, cpp_type=cpp, binding=rec,
                                    loc=loc)
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type=cpp, form=Form.BORROW,
                           cpp_local_representation=LocalBinding.REBIND_SLOT,
                           binding=rec, loc=loc)
    if rec.form is BindingForm.POINTER_UNION:
        value = _unpack_convert(r, _local_slot(tt, rec.form,
                                               SlotConstruct.DIRECT_INIT, lc,
                                               const=rec.const), lc, loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type=tt.to_cpp_ptr_variant(), form=Form.BORROW,
                           cpp_local_representation=LocalBinding.PTR_VARIANT,
                           binding=rec, loc=loc)
    if rec.form is BindingForm.POINTER:
        opt = _optional_ptr_borrow(tt, an)
        value = _unpack_convert(r, _local_slot(opt or tt, rec.form,
                                               SlotConstruct.DIRECT_INIT, lc,
                                               const=rec.const), lc, loc)
        if opt is not None:
            # A pointer-repr Optional element: a plain nullable `T*` local.
            declared[name] = opt
            return THIRVarDecl(
                name=name, resolved_type=opt, init=value,
                cpp_type=lc.render_type(unwrap_readonly(opt.inner)),
                form=Form.BORROW, is_const=rec.const,
                cpp_local_representation=LocalBinding.OPTIONAL_TO_PTR,
                binding=rec, loc=loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type=_head_ref_cpp(stmt, tt, lc),
                           form=Form.BORROW, is_const=rec.const,
                           cpp_local_representation=LocalBinding.POINTER,
                           binding=rec, loc=loc)
    if rec.form is BindingForm.REFERENCE:
        if i < len(stmt.is_rebound) and stmt.is_rebound[i]:
            # A reassigned reference would write through into the element.
            note_detail("tuple_unpack.target_form")
            raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
        # A value element is aliased const (a tuple is immutable); a
        # reference element as const as the source is.
        const = (rec.const or isinstance(tt, ReadonlyType)
                 or unwrap_readonly(tt).is_value_type())
        value = _unpack_convert(r, _local_slot(tt, rec.form,
                                               SlotConstruct.DIRECT_INIT, lc,
                                               const=const), lc, loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type=_head_ref_cpp(stmt, tt, lc),
                           form=Form.BORROW, is_const=const,
                           cpp_local_representation=LocalBinding.REF_ALIAS,
                           binding=rec, loc=loc)
    if rec.form is BindingForm.STORAGE_OPT:
        # A comprehension clause's pointer-repr Optional element binds the
        # storage optional by reference, as the clause's scalar variable
        # over such elements does.
        value = _unpack_convert(r, _local_slot(tt, rec.form,
                                               SlotConstruct.DIRECT_INIT, lc,
                                               const=rec.const), lc, loc)
        declared[name] = tt
        return THIRVarDecl(name=name, resolved_type=tt, init=value,
                           cpp_type="auto", form=Form.STORAGE,
                           is_const=rec.const,
                           cpp_local_representation=LocalBinding.REF_ALIAS,
                           binding=rec, loc=loc)
    if rec.form is not BindingForm.VALUE:
        note_detail("tuple_unpack.target_form")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    value = _unpack_convert(r, _local_slot(tt, rec.form,
                                           SlotConstruct.DIRECT_INIT, lc),
                            lc, loc)
    declared[name] = tt
    return THIRVarDecl(name=name, resolved_type=tt, init=value, cpp_type=cpp,
                       binding=rec, loc=loc)


def _scope_unpack_write(stmt: TpyTupleUnpack, i: int, src: _UnpackElems,
                        lc: '_LowerCtx', declared: dict[str, TpyType],
                        blocked: AbstractSet[str],
                        fresh_global_slots: AbstractSet[str],
                        loc, *, rec: 'BindingRepr | None' = None
                        ) -> THIRStmt:
    """A target that writes a binding already in scope, as the scalar
    write `x = <element i>` of it does: a plain value local or global is
    assigned; a module's own pointer-slot global takes an owned element's
    object into a `static` slot of its own, or points at a borrowed element
    of a tuple global (static storage). Every other binding's write takes
    slot machinery an unpack does not build."""
    an = lc.analyzer
    name = stmt.targets[i]
    tt = unwrap_ref_type(_bindings.unpack_target_type(stmt, i, an))
    owned = stmt.owns_target(i)
    is_ref = i < len(stmt.is_ref) and stmt.is_ref[i]
    r = src.read(i)
    if rec is None:
        rec = lc.binding(name)
    if name in fresh_global_slots and record_like(tt, an):
        if owned:
            value = _unpack_convert(r, _local_slot(
                tt, BindingForm.VALUE, SlotConstruct.DIRECT_INIT, lc),
                lc, loc)
            lc.global_slot_assigned.add(name)
            _witness("stmt.tuple_unpack.global_slot_target")
            return THIRPtrLocalDecl(name=name, resolved_type=tt,
                                    kind=PtrSlotKind.GLOBAL_RVALUE,
                                    init=value, cpp_type=lc.render_type(tt),
                                    binding=rec, loc=loc)
        if is_ref and isinstance(stmt.value, TpyName) and src.refers_into(i):
            # A NAME is the only module-level source with static storage.
            value = _unpack_convert(r, _local_slot(
                tt, BindingForm.POINTER, SlotConstruct.ASSIGN, lc), lc, loc)
            _witness("stmt.tuple_unpack.global_ptr_target")
            return THIRAssign(target=THIRName(name=name, result_type=tt),
                              value=value, binding=rec, loc=loc)
    if (is_ref and not owned and rec.pointer and not rec.global_slot
            and not rec.optional_storage and name in declared
            and src.refers_into(i)):
        # A pointer binding in scope (a predeclared or reassigned record
        # local) is re-pointed at the element, as `x = t[i]` re-points it,
        # as const as its slot.
        slot = _local_slot(_optional_ptr_borrow(tt, an) or tt,
                           BindingForm.POINTER, SlotConstruct.ASSIGN, lc,
                           const=rec.const)
        if r.at(slot).source.const and not rec.const:
            note_detail("tuple_unpack.target_const")
            raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
        value = _unpack_convert(r, slot, lc, loc)
        return THIRAssign(target=THIRName(name=name,
                                          result_type=declared[name]),
                          value=value, binding=rec, loc=loc)
    if (name in blocked or owned or is_ref
            or not _scalar_or_str_unpack_elem(tt, an)):
        note_detail("tuple_unpack.target_form")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    dt = declared[name]
    if (stmt.is_loop_head and rec.site_kind != "hoist"
            and holds_borrowing_view(_resolve_pending_view(dt, an) or dt)):
        # A view slot standing before the loop would keep viewing the
        # step's element after the step moves on (the scalar loop
        # variable's twin is refused the same); the loop's own hoist of a
        # target read after it takes the view only where sema's hoist rule
        # proved the step's storage outlives it.
        raise ThirUnsupported("stmt.for_each:tuple.reused_target", loc=loc)
    value = _unpack_convert(r, _local_slot(dt, BindingForm.VALUE,
                                           SlotConstruct.ASSIGN, lc), lc, loc)
    _witness("stmt.tuple_unpack.assign_target")
    return THIRAssign(target=THIRName(name=name, result_type=dt),
                      value=value, binding=rec, loc=loc)


def _reseat_unpack_write(stmt: TpyTupleUnpack, i: int, rec: BindingRepr,
                         src: _UnpackElems, lc: '_LowerCtx',
                         declared: dict[str, TpyType], loc) -> THIRStmt:
    """A target naming a local an earlier target of the same statement
    declared (`x, x = ...`): reassigned in the form that declaration took,
    as `x = <element i>` would -- a value assigned, a pointer re-pointed."""
    an = lc.analyzer
    name = stmt.targets[i]
    tt = unwrap_ref_type(_bindings.unpack_target_type(stmt, i, an))
    r = src.read(i)
    if rec.form is BindingForm.VALUE and rec.loop_binding is None and not (
            rec.rebind_slot):
        slot = _local_slot(declared[name], BindingForm.VALUE,
                           SlotConstruct.ASSIGN, lc)
    elif rec.form is BindingForm.POINTER and not (
            stmt.owns_target(i)):
        if not src.refers_into(i):
            note_detail("tuple_unpack.ref_source_form")
            raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
        slot = _local_slot(_optional_ptr_borrow(tt, an) or tt,
                           BindingForm.POINTER, SlotConstruct.ASSIGN, lc,
                           const=rec.const)
    else:
        note_detail("tuple_unpack.target_form")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    value = _unpack_convert(r, slot, lc, loc)
    return THIRAssign(target=THIRName(name=name, result_type=declared[name]),
                      value=value, binding=rec, loc=loc)


def _lower_unpack_stmt(stmt: TpyTupleUnpack, scope: '_LowerScope'
                       ) -> THIRStmt:
    """A statement `a, b = <source>`, or a loop head over its step: each
    target binds as `a = <element i>` would -- the scalar write node of its
    binding, its value the element read converted at the target's slot --
    off the source place, the statement's holder of the source or the
    loop's step (`_unpack_elems`)."""
    lc = scope.lc
    declared = scope.declared
    an = lc.analyzer
    loc = getattr(stmt, "loc", None)
    narrowed = lc.narrow.narrowed.keys()
    if any(n is not None and n in narrowed for n in stmt.targets):
        note_detail("tuple_unpack.narrowed_target")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    # Straight-line module-init only: a for body drops `static` from the
    # slot line, an if/while body's first slot is branch-scoped, and a
    # HOISTED name's slot is an `std::optional` pre-decl.
    fresh_global_slots: AbstractSet[str] = frozenset()
    if lc.top_level_scope and not scope.in_branch:
        assigned = frozenset(lc.global_slot_assigned)
        fresh_global_slots = NameFacts(
            lambda n: (_module_slot_global(n, lc) and n not in assigned
                       and n not in lc.prescan.hoisted))
    blocked = (lc.names_with("pointer", prior=True)
               | lc.names_with("rebind_slot", prior=True)
               | lc.names_with("ref_alias", prior=True)
               | lc.names_with("storage_tuple", prior=True)
               | lc.names_with("value_opt", prior=True)
               | lc.names_with("frame_slot", prior=True))
    if isinstance(stmt.value, TpyName):
        value = _lower_expr(stmt.value, lc, declared,
                            allow_unrouted_name=True)
    else:
        value = _flush_witness("flush.unpack_source", _lower_expr(
            stmt.value, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True,
                         pos=SinkPos.UNPACK_SOURCE),
            field_prechecked=isinstance(stmt.value, TpyFieldAccess)))
    src = _unpack_elems(stmt, value, lc)
    if len(src.layout.element_types) != len(stmt.targets):
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    for i, name in enumerate(stmt.targets):
        if name is not None and stmt.owns_target(i):
            _owned_elem_movable(stmt, i, src.elems[i], loc)
    if (isinstance(stmt.value, TpyName) and any(stmt.is_owned)
            and not stmt.is_loop_head
            and not (_is_move_source(stmt.value, lc)
                     and unpack_claim_agrees(stmt, lc))):
        # A name's owned elements move out at its last use only, and only
        # those its read record moves; a loop head's step is its own.
        note_detail("tuple_unpack.owned_source_not_movable")
        raise ThirUnsupported("stmt.tuple_unpack", loc=loc)
    _witness("stmt.tuple_unpack")
    writes: list[THIRStmt] = []
    records: list[BindingRepr | None] = []
    targets = list(stmt.targets)
    seen: set[str] = set()
    for i, name in enumerate(stmt.targets):
        if name is None:
            records.append(None)
            continue
        rec = _bindings.fresh_record(lc.table, stmt, name)
        if (rec is not None and rec.site_kind == "comp"
                and name in stmt.targets[i + 1:]):
            # `x, x` in a comprehension clause binds a fresh name per
            # iteration: only its last element is ever read.
            targets[i] = None
            records.append(None)
            continue
        # A comprehension clause's targets are its own, never an outer
        # binding of the name.
        if (rec is not None and stmt.is_loop_head and name in declared
                and rec.site_kind != "comp"
                and _bindings.head_target_writes_outer(
                    stmt, i, _bindings.unpack_target_type(stmt, i, an),
                    lc.prior_binding(name), an, walk_declared=True)):
            # A head target over a frame field the walk declared before
            # the loop writes that field, as the planner could not see.
            write = _scope_unpack_write(stmt, i, src, lc, declared, blocked,
                                        fresh_global_slots, loc,
                                        rec=lc.prior_binding(name))
        elif rec is not None and name not in seen:
            _discard_target_hoist(stmt, name, lc)
            write = _fresh_unpack_write(stmt, i, rec, src, lc, declared,
                                        loc)
        else:
            # A name an earlier target of this statement declared is
            # assigned again (`x, x = ...`), as any local in scope is.
            write = (_reseat_unpack_write(stmt, i, rec, src, lc, declared,
                                          loc)
                     if rec is not None else
                     _scope_unpack_write(stmt, i, src, lc, declared, blocked,
                                         fresh_global_slots, loc))
        seen.add(name)
        records.append(write.binding)
        writes.append(write)
    return THIRTupleUnpack(
        source="", targets=tuple(targets),
        target_cpps=(None,) * len(stmt.targets), per_target=True,
        holder=src.holder, holder_ref=src.ref, holder_read=src.read_id,
        writes=tuple(writes), bindings=tuple(records), loc=loc)


def _frame_unpack_reject(detail: str, loc) -> ThirUnsupported:
    note_detail("unpack." + detail)
    return ThirUnsupported("res.unpack", loc=loc)


def _frame_unpack_write(stmt: TpyTupleUnpack, i: int, src: _UnpackElems,
                        lvalue_root: 'str | None', lc: '_LowerCtx',
                        declared: dict[str, TpyType], loc) -> THIRStmt:
    """A resumable frame's target, a frame field, written as the scalar
    write `x = <element i>` of the field is: an `frame_slot` emplaced, a
    plain field assigned, a pointer alias re-pointed at the element, a
    rebound reference's object moved into the field the frame reserved for
    this site."""
    an = lc.analyzer
    name = stmt.targets[i]
    if name not in declared:
        raise _frame_unpack_reject("target_family", loc)
    rec = lc.binding(name)
    tt = unwrap_ref_type(_bindings.unpack_target_type(stmt, i, an))
    owned = stmt.owns_target(i)
    is_ref = i < len(stmt.is_ref) and stmt.is_ref[i]
    r = src.read(i)
    fld = rcfg.ptr_slot_field(lc.func, stmt, i)
    if fld is not None:
        # A rebound reference local taking an element over: the object
        # moves into the field the frame's ptr-slot prescan reserved for
        # this site, as the local's rvalue assignments do -- out of the
        # statement's own value holder or the loop's step only.
        if not src.owns:
            raise _frame_unpack_reject("rebind_ptr_source", loc)
        value = _unpack_convert(r, _local_slot(tt, BindingForm.VALUE,
                                               SlotConstruct.DIRECT_INIT, lc),
                                lc, loc, reason="res.unpack")
        _witness("res.unpack_rebind_ptr")
        return THIRPtrLocalRebind(name=name, kind=PtrSlotKind.FRAME_RVALUE,
                                  value=value, val_cpp=fld, binding=rec,
                                  loc=loc)
    if lc.frame_local(name, FrameLocalClass.UNPACK_PTR) or (
            stmt.is_loop_head
            and lc.frame_local(name, FrameLocalClass.OPT_PTR)):
        # A loop head's pointer field points into the loop's step, which
        # the frame holds until the next advance: at an element it refers
        # to or one the step hands over, as const as the field.
        slot = _local_slot(_optional_ptr_borrow(tt, an) or tt,
                           BindingForm.POINTER, SlotConstruct.ASSIGN, lc,
                           const=rec.frame_const)
        if r.at(slot).source.const and not rec.frame_const:
            raise _frame_unpack_reject("alias_const_verdict", loc)
        value = _unpack_convert(r, slot, lc, loc, reason="res.unpack")
        return THIRAssign(target=THIRName(name=name,
                                          result_type=declared[name]),
                          value=value, binding=rec, loc=loc)
    if lc.frame_local(name, FrameLocalClass.ALIAS_PTR):
        # A pointer alias field outlives the case block: it may point only
        # into storage the frame holds -- a pointee the source points at,
        # or an element of a frame-resident place -- and is as const as
        # that storage.
        if not is_ref or not src.refers_into(i) or not (
                r.source.pointer_held or lvalue_root is not None):
            raise _frame_unpack_reject("alias_source_form", loc)
        read = r.at(_local_slot(tt, BindingForm.POINTER,
                                SlotConstruct.ASSIGN, lc))
        if read.source.const and not rec.frame_const:
            raise _frame_unpack_reject("alias_const_verdict", loc)
        value = _unpack_convert(r, _local_slot(
            _optional_ptr_borrow(tt, an) or tt, BindingForm.POINTER,
            SlotConstruct.ASSIGN, lc, const=rec.frame_const), lc, loc,
            reason="res.unpack")
        return THIRAssign(target=THIRName(name=name,
                                          result_type=declared[name]),
                          value=value, binding=rec, loc=loc)
    if is_ref or r.source.pointer_held or rec.pointer:
        # A borrowed element needs an alias field; any other field would
        # copy the borrow.
        raise _frame_unpack_reject("ref_element", loc)
    if not (rec.frame_slot or lc.plain_frame_field(name)):
        raise _frame_unpack_reject("target_family", loc)
    slot_t = unwrap_readonly(unwrap_ref_type(
        lc.frame_local_types.get(name, declared[name])))
    if rec.frame_slot:
        # The emplace direct-initializes the payload.
        value = _unpack_convert(r, _local_slot(slot_t, BindingForm.VALUE,
                                               SlotConstruct.DIRECT_INIT, lc),
                                lc, loc, reason="res.unpack")
        return THIRFrameSlotWrite(name=name, value=value,
                                  cpp_type=lc.render_type(slot_t),
                                  binding=rec, loc=loc)
    value = _unpack_convert(r, _local_slot(slot_t, BindingForm.VALUE,
                                           SlotConstruct.ASSIGN, lc), lc, loc,
                            reason="res.unpack")
    return THIRAssign(target=THIRName(name=name, result_type=declared[name]),
                      value=value, binding=rec, loc=loc)


def _lower_frame_unpack_stmt(stmt: TpyTupleUnpack,
                             scope: '_LowerScope') -> THIRStmt:
    """A statement unpack in a resumable frame: each target -- a frame
    field -- written as the scalar write of that field would take
    `<element i>`, off the source place or the statement's holder, which
    lives in the case block (`_unpack_elems`). A one-shot `__await_lift_*`
    slot is the statement's to consume: it is held by reference and its
    elements move."""
    lc = scope.lc
    declared = scope.declared
    an = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if lc.in_finally_helper:
        raise _frame_unpack_reject("helper_position", loc)
    oneshot = (isinstance(stmt.value, TpyName)
               and lc.frame_local(stmt.value.name,
                                  FrameLocalClass.ONESHOT_SLOT)
               and lc.binding(stmt.value.name).frame_slot)
    if oneshot and any(stmt.is_owned) and not unpack_claim_agrees(stmt, lc):
        raise _frame_unpack_reject("claim_disagrees_record", loc)
    if isinstance(stmt.value, TpyName):
        value = _lower_expr(stmt.value, lc, declared,
                            allow_unrouted_name=True)
    else:
        value = _flush_witness("flush.unpack_source", _lower_expr(
            stmt.value, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True,
                         pos=SinkPos.UNPACK_SOURCE),
            field_prechecked=isinstance(stmt.value, TpyFieldAccess)))
    src = _unpack_elems(stmt, value, lc, consumes=oneshot)
    if len(src.layout.element_types) != len(stmt.targets):
        raise _frame_unpack_reject("source_elems", loc)
    # The frame-resident root of the storage a reference to the source
    # reaches, for an alias field to point into.
    resident = NameFacts(lambda n: (lc.binding(n).frame_class is not None
                                    or n == lc.self_receiver))
    lvalue_root = None
    if src.holder is None or src.ref:
        if isinstance(stmt.value, TpyName):
            if stmt.value.name in resident:
                lvalue_root = stmt.value.name
        else:
            lvalue_root = _storage_tuple_lvalue_root(
                stmt.value, declared, an, lc.names_with("pointer"),
                resident=resident)
    if src.holder is not None and _hoists_arg_temp(src.holder) and any(
            n is not None and lc.frame_local(n, FrameLocalClass.ALIAS_PTR)
            for n in stmt.targets):
        # An alias field outlives the case block the temp is declared in.
        raise _frame_unpack_reject("alias_temp_arg", loc)
    for i, name in enumerate(stmt.targets):
        if name is not None and stmt.owns_target(i):
            if not src.elems[i].movable:
                raise _frame_unpack_reject("owned_source_not_movable", loc)
    writes: list[THIRStmt] = []
    for i, name in enumerate(stmt.targets):
        if name is None:
            continue
        writes.append(_frame_unpack_write(stmt, i, src, lvalue_root, lc,
                                          declared, loc))
    _witness("res.unpack_oneshot" if oneshot else "res.frame_unpack")
    return THIRTupleUnpack(
        source="", targets=tuple(stmt.targets),
        target_cpps=(None,) * len(stmt.targets), per_target=True,
        holder=src.holder, holder_ref=src.ref, holder_read=src.read_id,
        writes=tuple(writes),
        bindings=tuple(lc.binding(n) if n is not None else None
                       for n in stmt.targets),
        loc=loc)


def _lower_frame_tuple_unpack(stmt: TpyTupleUnpack,
                              scope: '_LowerScope') -> THIRStmt:
    """A tuple unpack in a resumable body: a statement's or a suspending
    loop's head binds its targets -- frame fields -- per target
    (`_lower_frame_unpack_stmt`); the head of a loop that does not suspend
    binds the body's own C++ locals, as a sync body's head does."""
    begin_stmt()
    if stmt.is_loop_head and any(
            _bindings.fresh_record(scope.lc.table, stmt, n) is not None
            for n in stmt.targets if n is not None):
        # A loop that does not suspend binds its head's targets as the
        # body's own C++ locals, as a sync body does.
        return _lower_unpack_stmt(stmt, scope)
    return _lower_frame_unpack_stmt(stmt, scope)


def _rebound_tuple_alias_reject(stmt: TpyVarDecl, init: TpySubscript,
                                src: THIRExpr, lc: '_LowerCtx',
                                declared: dict[str, TpyType]) -> str | None:
    """The reject message when the alias `stmt` (`a = root[i].f[j]...`)
    points into a tuple held by value in a frame slot that a rebind can
    overwrite while the alias is live; None when it does not. The path of
    subscript and field hops from the root to the element must cross a tuple
    (the root counts); a first hop that yields a borrowed pointer ends it,
    since the element then lives outside the slot. Paths through records and
    containers only are NOT covered, and a rebind of such a root clobbers a
    multi-hop alias the same way
    (BUGS.md#nested-list-literal-alias-rebind-clobbers)."""
    e, hops = place_hops(init)
    if not isinstance(e, TpyName):
        return None
    root = e.name
    if not lc.binding(root).frame_slot:
        return None
    hops.reverse()
    if (isinstance(hops[0], TpySubscript)
            and _root_elem_read_is_pointer(src, root)):
        return None
    holders = [declared.get(root)] + [lc.analyzer.get_expr_type(h)
                                      for h in hops[:-1]]
    bare = [None if t is None
            else unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
            for t in holders]
    if not any(isinstance(t, TupleType) for t in bare):
        return None
    # The reassigned-var scan does not see a nested def's `nonlocal` rebind.
    if (root not in lc.prescan.reassigned
            and root not in collect_nested_def_nonlocal_rebinds(
                lc.func.body)):
        return None
    if not _rebind_can_follow(lc.func.body, stmt, root):
        return None
    return _rebound_tuple_alias_message(stmt, root, lc)


def _rebound_tuple_alias_message(stmt: TpyVarDecl, root: str,
                                 lc: '_LowerCtx') -> str:
    """The user-facing sentence for the rebound-tuple alias reject: the
    limitation in the program's own names, and the one rewrite that compiles
    and keeps CPython's behavior for every path. `copy()` is not named: it
    compiles only for some paths
    (BUGS.md#tuple-elem-copy-mixed-or-list-rejects)."""
    func = lc.func
    kind = ("an async generator" if func.is_async and func.is_generator
            else "an async function" if func.is_async else "a generator")
    return (f"binding '{stmt.name}' to an element inside '{root}' is not "
            f"yet supported in {kind} when '{root}' can be reassigned while "
            f"'{stmt.name}' is live; bind '{stmt.name}' after the last "
            f"reassignment of '{root}'")


def _reject_tuple_site_own(stmt: TpyStmt, name: str, analyzer) -> None:
    """Refuse a tuple rebind whose sema verdict is OWN where the tuple is not
    the pointer tuple whose elements take storage of their own: an
    `Own[tuple]` parameter, a loop variable."""
    sites = analyzer.ctx.tuple_own_sites
    if stmt not in sites:
        return
    note_detail("reseat.tuple_site_own")
    raise ThirUnsupported(stmt_reject_reason(stmt),
                          message=_tuple_site_own_message(name, sites[stmt]))


def _tuple_site_own_message(name: str,
                            holder: str | UnnamedHolder | None) -> str:
    """The user-facing sentence for a tuple rebind whose new object needs
    storage of its own: what still refers to the object the old element was
    bound to, and the rewrite that always compiles. Each owned element is a
    scalar's object; the sentence names it, not the tuple."""
    if holder is UnnamedHolder.POINTER_OR_ITERATOR:
        why = (f"a pointer taken from an element of '{name}', or an iterator "
               f"over it, still refers to the object that element was bound to")
    elif holder is not None:
        why = (f"'{holder}' still refers to the object an element of '{name}' "
               f"was bound to")
    else:
        why = (f"'{name}' is stored elsewhere (a parameter, a loop variable or "
               f"a borrowed value), or was deleted")
    return (f"{why}, so the object the rebind of '{name}' creates needs "
            f"storage of its own, which is not yet supported here; bind the "
            f"new value to a name of its own")


def _rebind_can_follow(body: list[TpyStmt], at: TpyStmt, name: str) -> bool:
    """Whether some write of `name` can execute after statement `at`: one
    later in program order, or one in the BODY of a loop that also encloses
    `at` (the back edge runs it again after `at`; a loop's `else` runs once,
    after the body). A nested def that rebinds the name through `nonlocal`
    counts wherever it sits, since it can be called at any point. True when
    `at` is not found, the safe answer for the caller's reject."""
    at_loops: 'tuple[int, ...] | None' = None
    writes: list[tuple[bool, tuple[int, ...]]] = []

    def walk(stmts: list[TpyStmt], loops: tuple[int, ...]) -> None:
        nonlocal at_loops
        for s in stmts:
            if s is at:
                at_loops = loops
            if isinstance(s, TpyNestedDef):
                if name in collect_nested_def_nonlocal_rebinds([s]):
                    writes.append((True, loops))
                continue
            if name in _written_names(s):
                writes.append((at_loops is not None, loops))
            if isinstance(s, (TpyForEach, TpyWhile)):
                walk(s.body, loops + (id(s),))
                walk(s.orelse or [], loops)
                continue
            if isinstance(s, TpyTry):
                # `sub_bodies()` lists `finally` before the handlers, but it
                # runs after all of them.
                walk(s.try_body, loops)
                for h in s.handlers:
                    walk(h.body, loops)
                walk(s.else_body, loops)
                walk(s.finally_body, loops)
                continue
            for b in s.sub_bodies():
                walk(b, loops)

    walk(body, ())
    if at_loops is None:
        return True
    return any(after or set(loops) & set(at_loops)
               for after, loops in writes)


def _lower_alias_bind(stmt: TpyVarDecl, lc: '_LowerCtx',
                      declared: dict[str, TpyType]) -> THIRStmt:
    """Pointer-alias frame bind (`a = items[0]` -> `a = &(<lvalue>);`):
    the address of the LIVE source lvalue, the skeleton's `T*` alias-field
    contract. Admitted sources are the proven-lvalue shapes: a
    record-element container subscript (the optptr.subscript guards --
    unproven-Optional / slice / rvalue-container receivers reject, an
    address into a dying temp would dangle), a record field, the receiver,
    or an existing alias/pointer/frame slot. Other sources keep the reject."""
    init = stmt.init
    analyzer = lc.analyzer
    pointee = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[stmt.name])))
    if (isinstance(init, TpySubscript)
            and not init.needs_optional_runtime_check
            and init.slice_function_info is None
            and not isinstance(init.index, TpySlice)
            # A container-ELEMENT receiver (`cube[i][j]`) is rooted storage
            # too, so the address the alias takes stays live across the
            # suspension exactly as the one-hop receiver's does.
            and _chained_subscript_recv_type(
                init.obj, declared, analyzer, lc.names_with("pointer")) is not None):
        src = _lower_expr(init, lc, declared, subscript_prechecked=True)
        rebound_msg = _rebound_tuple_alias_reject(stmt, init, src, lc,
                                                  declared)
        if rebound_msg is not None:
            # An element held BY VALUE in a tuple inside a frame slot the
            # body can rebind after this alias: the rebind writes the new
            # value into the same slot, so the alias would observe the new
            # element where CPython's keeps the old object. A pointer
            # tuple's root is no frame slot; a container root has no
            # per-site storage (BUGS.md#resumable-alias-identity).
            note_detail("alias.rebound_tuple_slot_elem")
            raise ThirUnsupported(
                "res.alias_bind",
                message=rebound_msg)
        if src.reads_raw_pointer:
            # The element already IS the `T*`: the address-of below must
            # re-address its pointee (`&((*std::get<0>(p)))`), not the
            # element slot, which would make a `T**`.
            src = replace(src, deref=True)
    elif (isinstance(init, TpyName)
          and lc.frame_local(init.name, FrameLocalClass.ALIAS_PTR)):
        # An alias-of-an-alias bind (`a = __unpack_0_0;` -- the desugared
        # tuple-literal unpack's user-name assign): both fields are `T*`,
        # so the bind is a bare pointer copy of the live alias, no
        # address-of.
        _witness("res.alias_bind")
        return THIRAssign(
            target=THIRName(name=stmt.name, result_type=declared[stmt.name],
                            loc=stmt.loc),
            value=THIRName(name=init.name, result_type=declared[stmt.name],
                           loc=stmt.loc),
            loc=stmt.loc)
    elif (isinstance(init, TpyFieldAccess)
            and _alias_field_source_ok(init, declared, analyzer)):
        # A reference FIELD source (`a = self.plain`): the alias takes the
        # field's address (`a = &(__self.plain);`), so it stays LIVE -- a
        # later write through the field is observed through the alias, as
        # CPython's name binding does.
        src = _lower_field_source(init, lc, declared)
    elif isinstance(init, TpyName) and init.name == lc.self_receiver:
        # The frame already borrows the live receiver; its local alias
        # takes the address of that same lvalue across suspension.
        src = _lower_expr(init, lc, declared,
                          use=_ExprUse(pos=SinkPos.ALIAS_BIND))
    elif (isinstance(init, TpyName)
            and (lc.binding(init.name).pointer or lc.binding(init.name).frame_slot)
            and init.name not in lc.narrow.narrowed):
        # A POINTER-local or FRAME-SLOT source (`ys = xs` off an owned
        # container held as `T*` / `frame_slot<T>`): the alias re-addresses
        # the deref (`ys = &((*xs));`) -- the same live-lvalue contract as
        # the subscript arm.
        src = _lower_expr(init, lc, declared,
                          use=_ExprUse(pos=SinkPos.ALIAS_BIND))
    elif _alias_call_source_ok(init, analyzer):
        # A BORROW-returning call (`m = min(a, b, key=f)`, `m = pick(a, b)`):
        # the result is one of its lent arguments, all existing storage, so
        # the alias takes that operand's address and stays live across the
        # suspension exactly as a name or field source does.
        src = _lower_expr(init, lc, declared,
                          use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    else:
        note_detail("alias.bind_source")
        raise ThirUnsupported("res.alias_bind")
    _witness("res.alias_bind")
    # is_const stays False: the `&(...)` render is const-blind (the alias
    # field's const-ness comes from its declared `const T*` type, which the
    # skeleton spells) -- the readonly flavor renders identically.
    return THIRAssign(
        target=THIRName(name=stmt.name, result_type=declared[stmt.name],
                        loc=stmt.loc),
        value=THIRFormConvert(result_type=pointee, value=src,
                              form=Form.BORROW, loc=stmt.loc),
        loc=stmt.loc)


def _lower_borrow_tuple_frame_write(stmt: TpyVarDecl, lc: '_LowerCtx',
                                    declared: dict[str, TpyType]) -> THIRStmt:
    """Borrow-form tuple frame-field write (`t = std::tuple<int32_t, Box*>{1,
    &(b)};`): a position-blind value render, where a borrow-form source
    needs no per-element lend. Only
    LITERAL, borrow-form NAME and borrow-form CALL sources are admitted: a
    STORAGE-form source needs the per-element lend, and an
    `Own[tuple[...]]`-declared callee is the skeleton's third OWNING signal
    (a frame_slot with emplace renders) -- both named rungs. Shared by the
    top-level leaf decl arm and the branch-nested decl arm.

    A pointer tuple's binding that owns an element is the pointer tuple's
    own arm (`_lower_ptr_tuple_binding`), as in a plain body
    (`_decl_btuple_rebound`)."""
    analyzer = lc.analyzer
    ptt = lc.binding(stmt.name).ptr_tuple
    if ptt is not None:
        node = _lower_ptr_tuple_binding(stmt, ptt, True, lc, declared,
                                        stmt.loc, frame=True)
        if node is not None:
            return node
    if ((isinstance(stmt.init, TpySubscript)
            and isinstance(stmt.init.obj, TpyName)
            and not isinstance(analyzer.get_expr_type(stmt.init.obj),
                               ReadonlyType))
            or (ptt is not None and isinstance(stmt.init, TpyFieldAccess)
                and _borrow_tuple_source_ok(stmt.init, lc))):
        # A container-element storage-tuple lvalue: the bare element read
        # (`__getitem__((*items), 0)`) lent element by element -- the
        # non-resumable reseat_lift's frame twin; for a pointer tuple a
        # field too, as
        # the plain body's `_decl_btuple_rebound` lifts it.
        _sub_src = _lower_expr(stmt.init, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                               subscript_prechecked=True)
        _witness("res.btuple_write")
        return THIRAssign(
            target=THIRName(name=stmt.name,
                            result_type=declared[stmt.name], loc=stmt.loc),
            value=to_local(
                _sub_src, unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[stmt.name]))), BindingForm.BORROW_TUPLE,
                analyzer),
            loc=stmt.loc)
    if isinstance(stmt.init, TpyTupleLiteral):
        value = _lower_borrow_tuple_literal(
            stmt.init, declared[stmt.name], lc, declared)
    elif isinstance(stmt.init, (TpyName, TpyIfExpr)):
        # Key on the LOWERED form fact rather than the source shape: a
        # storage-form source
        # would need the wrap. A ternary qualifies because its lowering
        # propagates its arms' form for tuple results.
        value = _lower_expr(stmt.init, lc, declared)
        if value.form is Form.STORAGE:
            raise ThirUnsupported("res.btuple_source")
    elif (isinstance(stmt.init, (TpyCall, TpyMethodCall))
            and _f1_tuple(analyzer.get_expr_type(stmt.init),
                          analyzer) is not None
            and not call_hands_back_value(stmt.init)):
        # The C++ return type IS the borrow tuple, so the wrap no-ops. The
        # Own signal must be read off the callee's DECLARED return: sema
        # stamps the call EXPR with the peeled tuple, so the expr-type
        # predicates cannot see the Own.
        value = _lower_expr(
            stmt.init, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE,
                         allow_temps=True,
                         pos=SinkPos.FRAME_SLOT_WRITE))
    else:
        raise ThirUnsupported("res.btuple_source")
    _witness("res.btuple_write")
    return THIRAssign(
        target=THIRName(name=stmt.name, result_type=declared[stmt.name],
                        loc=stmt.loc),
        value=value, loc=stmt.loc)


def _unlifted_declared_copy(value: 'TpyExpr | None',
                            lc: '_LowerCtx') -> bool:
    """A BORROW-form tuple name whose element copy sema declared, reaching a
    return's bare tail: every slot that can build the copy lifted it before,
    so a bare render here would hand a tuple of pointers to a slot that owns
    (a mixed `-> tuple[Own[P], P]` return, say) -- reject it at the statement
    instead of in the C++ build. A storage-form binding (a loop variable over
    stored tuples) copies correctly bare."""
    return (isinstance(value, TpyName)
            and bare_tuple(lc.analyzer.get_expr_type(value)) is not None
            and bool(verdict(lc.analyzer.ctx, value).copied)
            and not lc.binding(value.name).storage_tuple)


def _own_tuple_name_copy(source: TpyName, slot: TpyType, lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRExpr:
    """The lift of a tuple NAME into an owning return, built element by
    element: each borrowed element is copied in, and the elements the read
    moves are moved (`convert`)."""
    value = _lower_expr(source, lc, declared, allow_unrouted_name=True)
    return converted(value, store_slot(unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(slot))), lc.analyzer), lc.analyzer)


def _lower_frame_slot_write(stmt: TpyVarDecl, lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRStmt:
    """R1c frame_slot write (`xs.emplace(std::vector<int32_t>{1, 2});` --
    first init and reassign alike, emplace destroys any prior payload). The
    emplace arg is a storage sink (the owned payload constructs in place),
    so admission matches the sync storage decl's; the emplace line is a
    flushable statement position, so arg temps hoist before it (the
    ptr-opt ctor arg's `Box __tmp_N = ...;`).
    Shared by the top-level leaf decl arm and the branch-nested decl arm."""
    init = _peel_stale_view_owned_coerce(stmt.init, declared[stmt.name],
                                         lc.analyzer)
    _fsw_eff = lc.frame_own_tuple_type(stmt.name)
    if isinstance(init, TpyTupleLiteral) and isinstance(_fsw_eff, TupleType):
        # A tuple LITERAL into an owning frame slot whose per-element
        # ownership was inferred from the literal's own captures. MIXED (some
        # element owned, some borrowed) has no whole-tuple value form, so it
        # takes the same per-element builder the mixed render uses elsewhere;
        # a fully-owned slot spells the storage tuple. `consuming` marks the
        # slot as outliving the statement, which is what makes an rvalue
        # element land in a value slot rather than as a dead borrow.
        if _fsw_eff.is_mixed_own():
            value = _lower_borrow_tuple_literal(
                init, _fsw_eff, lc, declared, consuming=True,
                use=_ExprUse(allow_temps=True))
            _witness("res.frame_mixed_tuple_literal")
        else:
            value = _lower_tuple_literal(
                init, owned_tuple_storage_type(_fsw_eff), lc, declared,
                use=_ExprUse(allow_temps=True))
            _witness("res.frame_own_tuple_literal")
        return THIRFrameSlotWrite(
            name=stmt.name, value=value,
            cpp_type=lc.render_type(unwrap_readonly(unwrap_ref_type(
                lc.frame_local_types.get(stmt.name, declared[stmt.name])))),
            loc=stmt.loc)
    if type(init) in _comprehensions._COMP_KINDS:
        # A comprehension init (`rows = [[i, i+1] for i in range(3)]`):
        # the ordinary comp statement-expression renders INSIDE the
        # emplace arg (`rows.emplace(({ ... })));` -- the sync decl's comp
        # branch at the frame sink. Reassigned names re-emplace the same
        # way; move-through keeps its own arm.
        if stmt.name in lc.prescan.move_through:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        value = _comprehensions._lower_comprehension(
            init, declared[stmt.name], lc, declared,
            _comp_shadow_pointers(lc.names_with("pointer"), declared, lc.analyzer))
        _witness("res.frame_comp_write")
    else:
        # The emplace is a statement position: arg temps (the ptr-opt ctor
        # arg's `Box __tmp_N = Box(...);`) flush before the emplace line,
        # ahead of the write. The whole-tuple verdict holds for a TUPLE
        # slot whose init is NOT an Own-declared call: the emplace
        # consumes the mixed / per-element-own call result whole
        # (`p.emplace(make_mixed(b))`); an Own[tuple]-declared callee
        # keeps its own dedicated result row (call.own_tuple_storage_ret).
        _fsw_slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[stmt.name])))
        _fsw_tuple_src = (isinstance(_fsw_slot, TupleType)
                          and not (isinstance(init, (TpyCall, TpyMethodCall))
                                   and call_hands_back_value(init)))
        # The emplace direct-initializes the payload, so an all-fresh
        # select renders its prvalue `?:` there.
        value = _lower_expr(init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True,
                                         pos=SinkPos.FRAME_SLOT_WRITE,
                                         forms=(None if _fsw_tuple_src
                                                else _ONLY_SELECT_FRESH_PRVALUE)))
    _witness("res.frame_slot_write")
    # The brace-init prefix spells the SLOT, so it reads the resolved frame
    # local type -- a branch-declared container
    # literal can carry a sized Array decl-site type at one arm while the
    # slot itself is the merged list.
    slot_t = unwrap_readonly(unwrap_ref_type(
        lc.frame_local_types.get(stmt.name, declared[stmt.name])))
    return THIRFrameSlotWrite(
        name=stmt.name, value=value, cpp_type=lc.render_type(slot_t),
        loc=stmt.loc)


def _lower_erased_handle_write(stmt: TpyVarDecl, init: TpyExpr,
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType]) -> THIRStmt:
    """Owned-erased @dynamic frame local (`unique_ptr<P>` bare field): the
    field is member-assigned the own-arg render of the RHS
    (`d = ::tpy::make_adapter<Base>(factory(..));`). Admit exactly the
    source shapes the Own[dyn P] arg rows classify; other sources reject
    (fail-closed; a probe found the reject sema-unconstructible today --
    ternary/name handle binds violate the single-use coroutine-reference
    rule first). The decl promotes movability, so a last-use read moves out
    of the field (`std::move(d)`) instead of copying. The slot keys the
    FRAME type (Own[P]); the declared map carries sema's bare structural
    wrap."""
    slot_t = lc.frame_local_types.get(stmt.name, declared[stmt.name])
    if (_dyn_own_coro_factory_arg(init, slot_t, lc.analyzer) is None
            and _dyn_own_handle_arg(init, slot_t, declared,
                                    lc.analyzer) is None
            and _dyn_own_conformer_arg(init, slot_t, declared,
                                       lc.analyzer) is None
            and _dyn_own_forward_call_arg(init, slot_t, lc.analyzer) is None):
        raise ThirUnsupported("res.erased_handle_source")
    value = _lower_call_arg(init, slot_t, lc, declared, retention=Retention.MEASURED)
    _witness("res.erased_handle_write")
    return THIRAssign(
        target=THIRName(name=stmt.name, result_type=slot_t, loc=stmt.loc),
        value=value, loc=stmt.loc)


def _lower_frame_field_assign(stmt: TpyVarDecl, lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> THIRStmt:
    """Position-blind plain frame-field write (`name = expr;` -- first decl
    and reassign alike): the init renders untargeted (a BigInt literal stays
    `0`, never the sync arms' target-typed
    `::tpy::BigInt(0)`); a stale view->owned coerce peels like the sync
    decl. Shared by the top-level leaf decl arm (`_lower_leaf`) and the
    branch-nested decl arm below."""
    init = _peel_stale_view_owned_coerce(stmt.init, declared[stmt.name],
                                         lc.analyzer)
    if isinstance(init, TpyTupleLiteral):
        # A value-tuple literal at a bare tuple frame field renders the
        # same spelled `std::tuple<...>{...}` as the sync decl arm, in the
        # position-blind member assign. Off-family slots fall through to
        # _lower_expr's bare-literal reject (expr.tuple_literal), and
        # _lower_tuple_literal itself rejects non-VALUE captures.
        tuple_t = _value_tuple_nested(declared[stmt.name], lc.analyzer)
        if tuple_t is not None:
            value = _lower_tuple_literal(init, tuple_t, lc, declared)
            _witness("res.frame_tuple_literal")
            return THIRAssign(
                target=THIRName(name=stmt.name,
                                result_type=declared[stmt.name],
                                loc=stmt.loc),
                value=value, loc=stmt.loc)
    if isinstance(init, TpyNoneLiteral):
        opt_slot = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(declared[stmt.name])))
        if (isinstance(opt_slot, OptionalType)
                and not opt_slot.uses_pointer_repr()):
            # The frame-field twin of the sync decl arm's `decl.opt_none`:
            # `v = None` at a value-repr Optional slot stores the
            # storage-form `std::nullopt`. A bare None has no `_lower_expr`
            # arm, so this must intercept before the tail below.
            _witness("res.frame_opt_none")
            return THIRAssign(
                target=THIRName(name=stmt.name,
                                result_type=declared[stmt.name],
                                loc=stmt.loc),
                value=THIRLiteral(result_type=declared[stmt.name], value=None,
                                  form=Form.STORAGE, loc=stmt.loc),
                loc=stmt.loc)
    if isinstance(init, (TpyCall, TpyMethodCall)):
        _fu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[stmt.name])))
        _iu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            lc.analyzer.get_expr_type(init))))
        if (isinstance(_fu, UnionType)
                and _eligible_value_union(_fu) is not None
                and _iu == _fu):
            # A same-union VALUE-union-returning call (`a = remake()`):
            # the variant return copies bare into the frame field -- the
            # STORAGE sink admission the sync reassign arm threads.
            value = _lower_expr(init, lc, declared,
                                use=_ExprUse(result=_ExprResultUse.STORAGE,
                                             allow_temps=True))
            _witness("res.frame_union_call")
            return THIRAssign(
                target=THIRName(name=stmt.name,
                                result_type=declared[stmt.name],
                                loc=stmt.loc),
                value=value, loc=stmt.loc)
    # A value-repr optional frame field takes the WHOLE optional bare
    # (`v = __self.f;`), so the source read must not deref on narrow -- the
    # same admission the sync decl sink threads for its optional slot.
    # allow_temps: the leaf statement IS a flush position -- emit_leaf_stmt
    # renders through the same render-then-flush assign arm as a sync body,
    # on the ctx-backed TempSink (shared __tmp_N numbering), exactly where
    # `auto __tmp_1 = (*p);` hoists inside the case block.
    # field_owned_str_ok: a view-family FIELD read into a view-family frame
    # field (`q = self.s` narrowed -> `q = (*__self.s);`) -- the same
    # admission the sync decl sink threads (str_field_init). str and bytes
    # share the row: both read the member bare and let the sink compose its
    # own wrap, so the slot family does not change the render.
    value = _lower_expr(
        init, lc, declared,
        use=_ExprUse(allow_temps=True),
        allow_whole_optional=_value_opt_target_binding(stmt.name, lc),
        field_owned_str_ok=(
            isinstance(init, TpyFieldAccess)
            and _resolved_viewfam_value(declared[stmt.name],
                                        lc.analyzer) is not None))
    # An owned-BYTES frame field fed a view source takes the family's
    # view->owned construction, the same wrap the sync reassign arm threads:
    # `std::vector<uint8_t>` has no `operator=` from a span. The FIELD's
    # declared storage asks -- a slice result arrives here as a plain value.
    # `std::string` does take a string_view, so the str twin stays bare.
    tgt_bytes = _resolved_bytes_value(declared[stmt.name], lc.analyzer)
    if tgt_bytes is not None and is_bytes_type(tgt_bytes):
        value = _wrap_view_owned_sink(value, tgt_bytes, stmt.loc)
    elif isinstance(init, TpyName) and _node_moves(value):
        # Sema's last-use move, as at the sync reassignment.
        value = THIRMove(result_type=value.result_type, value=value,
                         form=value.form, loc=stmt.loc)
    _witness("res.decl_assign")
    return THIRAssign(
        target=THIRName(name=stmt.name, result_type=declared[stmt.name],
                        loc=stmt.loc),
        value=value, loc=stmt.loc)


def _value_opt_target_binding(name: str, lc: '_LowerCtx') -> bool:
    """The reassignment TARGET's slot is a value-repr optional (scalar or
    owned-view) binding -- the whole-optional-copy admission all three
    reassign arms key on (whole_optional_reassign, the opt_slot override,
    the deref strip); keep them agreeing through this one predicate."""
    return (_value_opt_scalar_binding(name, lc)
            or _value_opt_view_binding(name, lc))


def _protocol_constexpr_info(cond: TpyExpr):
    """(var, check_type, negated) for a protocol-isinstance condition
    (negation included).
    None for every other condition, incl. @dynamic protocol checks (sema
    sets isinstance_is_protocol only for the static concept family)."""
    neg = False
    c = cond
    if isinstance(c, TpyUnaryOp) and c.op == "!":
        neg = True
        c = c.operand
    if (isinstance(c, TpyCall)
            and getattr(c, "isinstance_is_protocol", False)
            and c.isinstance_var is not None
            and c.isinstance_type is not None):
        return (c.isinstance_var, c.isinstance_type, neg)
    return None


def _nullproto_guard_condition(cond: TpyExpr,
                               lc: '_LowerCtx') -> 'tuple[str, bool] | None':
    """The nullable-protocol constexpr-guard trigger: `x is not None`
    on a nullable protocol PARAM swaps the runtime condition for an
    `if constexpr (!std::same_as<T_x, std::nullptr_t>)` guard, and the
    branch reads deref through the pointer set. Returns (var, swappable):
    swappable True for the STRUCTURAL flavors the swap arm renders (the
    `is_static_protocol_param` slice -- an Optional[static protocol] or a
    nullable all-static-protocols union); False for a @dynamic-member
    flavor, whose is-not-None render is not lowered (the caller rejects).
    None when the condition is not this trigger at all."""
    m = match_is_none(cond)
    if m is None:
        return None
    var, is_not_none = m
    if not is_not_none or "." in var:
        return None
    pt = next((t for n, t in lc.params if n == var), None)
    if pt is None:
        return None
    if _nullable_static_protocol_param(pt) is not None:
        return (var, True)
    # The remaining protocol-member flavors (@dynamic inner, mixed unions)
    # keep the runtime compare, which is not lowered here.
    dt = _unwrap_own(unwrap_readonly(unwrap_ref_type(pt)))
    if isinstance(dt, OptionalType) and is_protocol_type(dt.inner):
        return (var, False)
    if isinstance(dt, UnionType):
        if (any(is_void_like_type(mm) for mm in dt.members)
                and any(is_protocol_type(mm) for mm in dt.members)):
            return (var, False)
    return None


def _lower_constexpr_branches(stmt: TpyIf, var: str, lc: _LowerCtx,
                              declared: dict[str, TpyType], *,
                              loop_depth: int
                              ) -> 'tuple[tuple, tuple]':
    """The shared then/else walk of the two constexpr-if arms: each branch
    lowers with the subject DECLARED-retyped to its branch fact (no
    extraction alias). branch_decls_ok: the constexpr-hoist reject already
    turned away any if with AFTER-branch uses (if_branch_decls), so a first
    decl reaching the walk is branch-local -- a plain block-scoped decl
    inside the constexpr arm."""
    then_declared = dict(declared)
    tf = stmt.then_type_facts.get(var)
    if tf is not None:
        then_declared[var] = tf
    then_body = _lower_stmts(stmt.then_body, lc, then_declared,
                             in_branch=True, branch_decls_ok=True,
                             loop_depth=loop_depth)
    else_declared = dict(declared)
    ef = stmt.else_type_facts.get(var)
    if ef is not None:
        else_declared[var] = ef
    else_body = (_lower_stmts(stmt.else_body, lc, else_declared,
                              in_branch=True, branch_decls_ok=True,
                              loop_depth=loop_depth)
                 if stmt.else_body else ())
    return then_body, else_body


def _lower_nullproto_guard_if(stmt: TpyIf, var: str, lc: _LowerCtx,
                              declared: dict[str, TpyType], loc, *,
                              loop_depth: int) -> THIRIf:
    """`if x is not None:` on a nullable STATIC-protocol param -> the
    constexpr-guard swap: the runtime compare is REPLACED by
    `if constexpr (!std::same_as<T_x, std::nullptr_t>)`. The branch model
    is `_lower_constexpr_if`'s -- no extraction, the guarded var
    DECLARED-retyped to its branch fact (reads deref through the pointer
    set); the shape guards mirror it too."""
    if lc.analyzer.if_branch_decls.get(stmt):
        note_detail("if.nullproto_hoist")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if (var in lc.narrow.narrowed or var in lc.narrow.spelled
            or any(k != var for k in stmt.then_type_facts)
            or any(k != var for k in stmt.else_type_facts)):
        note_detail("if.nullproto_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    cond = THIRConceptTest(
        result_type=BOOL,
        cpp=(f"!std::same_as<{protocol_param_template_name(var)}, "
             f"std::nullptr_t>"),
        loc=getattr(stmt.condition, "loc", None))
    then_body, else_body = _lower_constexpr_branches(
        stmt, var, lc, declared, loop_depth=loop_depth)
    _witness("if.nullproto_guard")
    return THIRIf(condition=cond, then_body=then_body,
                  else_body=else_body, is_constexpr=True, loc=loc)


def _lower_constexpr_if(stmt: TpyIf, info, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc, *,
                        loop_depth: int) -> THIRIf:
    """`if isinstance(x, Protocol):` -> `if constexpr (<concept>)` -- a
    keyword swap over the ordinary if chain. The concept cpp comes from the
    render_concept hook;
    the protocol path never extracts, so each branch lowers with the
    subject DECLARED-retyped to its branch fact (member dispatch resolves
    against the checked protocol -- the child-protocol retype)."""
    var, check_type, negated = info
    if lc.render_concept is None:
        note_detail("if.constexpr_no_renderer")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if lc.analyzer.if_branch_decls.get(stmt):
        note_detail("if.constexpr_hoist")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if (var in lc.narrow.narrowed or var in lc.narrow.spelled
            or any(k != var for k in stmt.then_type_facts)
            or any(k != var for k in stmt.else_type_facts)):
        note_detail("if.constexpr_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # The nullptr_t special keys the RAW param type, never the
    # branch-retyped binding.
    param_ty = next((t for n, t in lc.params if n == var), None)
    cpp = lc.render_concept(var, check_type, param_ty, negated)
    cond = THIRConceptTest(result_type=BOOL, cpp=cpp,
                           loc=getattr(stmt.condition, "loc", None))
    then_body, else_body = _lower_constexpr_branches(
        stmt, var, lc, declared, loop_depth=loop_depth)
    return THIRIf(condition=cond, then_body=then_body,
                  else_body=else_body, is_constexpr=True, loc=loc)


def _fn_return_type(lc: _LowerCtx) -> 'TpyType | None':
    """The body's declared return type: the per-@overload stub's when this
    is a per-stub lowering, else the func's."""
    if lc.overload_stub_return is not None:
        return lc.overload_stub_return
    return (lc.func.return_type
            if isinstance(lc.func.return_type, TpyType) else None)


def _value_opt_return(slot: 'Slot', analyzer) -> 'OptionalType | None':
    """A value-repr `Optional` result slot over a cheap scalar, a value
    record or a value-bound type parameter (`std::optional<T>`): `None` is
    the empty optional, a value-opt name passes whole."""
    rt = slot.declared
    vopt = _value_opt_scalar(rt, analyzer) or _value_opt_record(rt)
    if vopt is None:
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
        if (isinstance(u, OptionalType) and not u.uses_pointer_repr()
                and _is_type_param_slot(unwrap_readonly(u.inner))):
            vopt = u
    return vopt


def _return_carries_value(stmt: TpyReturn, lc: _LowerCtx) -> bool:
    """Whether a return renders an operand. The parser canonicalizes the
    bare `return` to `return None`, so the void form is decided here, once:
    a `None` operand at a slot that takes no value -- `-> None`, an
    unannotated def, a generator's end-of-iteration return -- is the bare
    `return`. At any other slot the literal is a real value (an Optional's
    nullopt, a None-union's monostate) and rides the operand arms."""
    if not is_none_return(stmt):
        return True
    if lc.func.is_generator:
        return False
    rt = _fn_return_type(lc)
    if rt is not None:
        rt = unwrap_readonly(unwrap_ref_type(rt))
    return rt is not None and not is_void_like_type(rt)


def _finally_deferred_recipe(
        ret: TpyReturn, lc: _LowerCtx, declared: dict,
        loc) -> 'THIRFinallyDeferredReturn | None':
    """The two-recipe table over the lc binding facts; None for a stamped
    shape the table does not cover (the caller rejects).

    Shape A (Own[T] slot): `auto* p = &(lvalue);` ... `std::move(*p)`.
    Shape B (storage-Optional slot off a pointer-repr Optional local):
    `auto* p = name;` ... `::tpy::ptr_to_optional_move(p)`.
    """
    # Sema stamps a name under identity coercions only (`String` into `str`:
    # both spell std::string, so `std::move(*p)` converts at the return).
    value = ret.finally_deferred_name
    if value is None:
        return None
    name = value.name
    if _deferred_name_renamed(name, lc, declared):
        return None
    ret = _fn_return_type(lc)
    ret_u = unwrap_ref_type(ret) if ret is not None else None
    if ret_u is None:
        return None
    # A CONSUMING method's receiver is an owned source like any Own[T] local,
    # so sema stamps `return self` under a finally the same way -- but there
    # is no C++ local called `self`, and the deferral is what the shape needs:
    # a finally that mutates the receiver must be seen by the returned object
    # (CPython hands back the very object). So the receiver keeps the
    # deferred capture and only its BASE render changes, to the receiver read
    # every other `self` site emits. The move that follows is the recipe's own
    # `std::move(*p)`, so `return self` has one move path either way.
    if name == lc.self_receiver:
        if isinstance(ret_u, (OptionalType, TupleType, UnionType)):
            return None
        return THIRFinallyDeferredReturn(
            capture=_deferred_self_base(value, lc, declared),
            indirect=False, optional_move=False, loc=loc)
    captured = _deferred_capture_base(value, lc, declared)
    if captured is None:
        return None
    base, indirect = captured
    if isinstance(ret_u, OptionalType) and not ret_u.uses_pointer_repr():
        if indirect:
            return THIRFinallyDeferredReturn(capture=base, optional_move=True,
                                             loc=loc)
        # An owned nullable tuple local IS the slot's by-value optional:
        # shape A moves it out whole after the chain.
        if (_value_opt_tuple(declared.get(name), lc.analyzer) is not None
                and unwrap_readonly(unwrap_send_sync(declared[name]))
                == unwrap_readonly(unwrap_send_sync(ret_u))):
            return THIRFinallyDeferredReturn(capture=base, indirect=False,
                                             optional_move=False, loc=loc)
        return None
    if isinstance(ret_u, (OptionalType, TupleType, UnionType)):
        return None
    return THIRFinallyDeferredReturn(capture=base, indirect=indirect,
                                     optional_move=False, loc=loc)


def _deferred_self_base(value: TpyName, lc: _LowerCtx,
                        declared: dict) -> THIRSelf:
    """The consuming receiver's capture base: the receiver read every other
    `self` site emits (there is no C++ local called `self`)."""
    return stamped(THIRSelf(result_type=declared[value.name],
                            form=Form.BORROW, cpp=lc.self_cpp,
                            deref=lc.self_is_pointer,
                            pointer=lc.self_is_pointer,
                            loc=getattr(value, "loc", None)), lc)


def _deferred_name_renamed(name: str, lc: _LowerCtx, declared: dict) -> bool:
    """Whether a deferred local reads through a rename layer (a narrowing, a
    walrus slot, a forwarded proto-param alias), whose base render the
    capture's `&(name)` would get wrong -- both recipes decline such a
    name, which rejects the return."""
    if name == lc.self_receiver:
        return name not in declared
    return (name in lc.narrow.narrowed or name in lc.narrow.spelled
            or lc.binding(name).site_kind == "walrus"
            or name in lc.forwarded_map
            or name not in declared)


def _deferred_capture_base(value: TpyName, lc: _LowerCtx, declared: dict
                           ) -> 'tuple[THIRName, bool] | None':
    """The deferred local's own render and whether it is pointer-bound (the
    capture then takes the address of `(*name)`); None when the name carries
    a spelling the capture cannot take the address of."""
    name = value.name
    if name == lc.self_receiver and name in declared:
        return _deferred_self_base(value, lc, declared), False
    indirect = lc.binding(name).pointer
    # A resumable frame slot reads `(*name)`, which is the local's own
    # spelling rather than a pointer wrap -- so it rides the capture
    # expression and a C++-local shadow of the field can still suppress it at
    # emit. The two are exclusive by frame classification; a name carrying
    # both would render a double deref, so refuse it rather than guess.
    frame_slot = lc.binding(name).frame_slot
    if frame_slot and indirect:
        return None
    if name not in declared:
        return None
    return (THIRName(result_type=declared[name], name=name,
                     deref=frame_slot, loc=getattr(value, "loc", None)),
            indirect)


def _peel_tpy_coerce(e: TpyExpr) -> TpyExpr:
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def _mixed_local_is_result_form(rec: 'BindingRepr', ret_t: TpyType) -> bool:
    """A mixed own / borrow tuple local whose C++ is the result's own form:
    it holds inline exactly the elements the result owns
    (`std::tuple<A, B*>` for `-> tuple[Own[A], B]`)."""
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret_t)))
    return (rec.form is BindingForm.BORROW_TUPLE and rec.ptr_tuple is None
            and isinstance(rt, TupleType)
            and rec.inline_elems == tuple(
                isinstance(unwrap_readonly(e), OwnType)
                for e in rt.element_types))


def _finally_deferred_tuple_recipe(
        ret: TpyReturn, value: 'THIRExpr | None', lc: _LowerCtx,
        declared: dict, loc) -> 'THIRFinallyDeferredReturn | None':
    """The per-member form of the scalar recipe for a returned tuple literal
    (sema's `finally_deferred_leaves`), built over the literal's ordinary
    lowering `value`: a deferred member is captured by pointer, every other
    member is evaluated into a temporary before the chain, in the literal's
    order, and the tuple is rebuilt after it. None when the lowered literal
    is not a shape the parts can be read off."""
    src = _peel_tpy_coerce(ret.value)
    leaf_names = {path: _peel_tpy_coerce(leaf)
                  for path, leaf in tuple_literal_leaves(src)}
    deferred = set(ret.finally_deferred_leaves)
    if () in deferred:
        # `return t`: the scalar recipe over the whole local, when its
        # storage is the return's own C++ type; a borrow-form or differently
        # spelled local would need a conversion the capture cannot hold.
        inner = value.value if isinstance(value, THIRMove) else value
        ret_t = _fn_return_type(lc)
        if not (isinstance(src, TpyName) and isinstance(inner, THIRName)
                and src.name in declared and ret_t is not None
                and (_mixed_local_is_result_form(
                         lc.binding(src.name), ret_t)
                     or (lc.binding(src.name).storage_tuple
                         and lc.render_type(declared[src.name])
                         == lc.render_type(unwrap_ref_type(ret_t))))):
            return None
        captured = _deferred_capture_base(src, lc, declared)
        if captured is None:
            return None
        return THIRFinallyDeferredReturn(capture=captured[0],
                                         indirect=captured[1], loc=loc)
    if not all(isinstance(leaf_names.get(p), TpyName) for p in deferred):
        return None

    def build(node: THIRExpr, path: tuple[int, ...]
              ) -> 'tuple[str, tuple[DeferredTuplePart, ...]] | None':
        if isinstance(node, THIRTupleLiteral):
            tt = unwrap_qualifiers(node.result_type)
            if not isinstance(tt, TupleType):
                return None
            cpp = tt.to_cpp()
            elem_cpps = [unwrap_qualifiers(et).to_cpp()
                         for et in tt.element_types]
            wraps: list[str | None] = [None] * len(node.elements)
        elif isinstance(node, THIRBorrowTupleLiteral):
            cpp = node.spelled_cpp
            elem_cpps = list(node.elem_cpps)
            ew = node.elem_wraps or (None,) * len(node.elements)
            wraps = ["&({0})" if lift else w
                     for lift, w in zip(node.addr_of, ew)]
        else:
            return None
        if len(elem_cpps) != len(node.elements):
            return None
        parts: list[DeferredTuplePart] = []
        for i, elem in enumerate(node.elements):
            p = path + (i,)
            if p in deferred and _deferred_name_renamed(
                    leaf_names[p].name, lc, declared):
                # Declined like the scalar: an eager copy would lose the
                # finally's mutation silently.
                return None
            elif p in deferred:
                inner = elem.value if isinstance(elem, THIRMove) else elem
                if (not isinstance(inner, (THIRName, THIRSelf))
                        or wraps[i] is not None):
                    return None
                captured = _deferred_capture_base(leaf_names[p], lc,
                                                  declared)
                if captured is None:
                    return None
                base, indirect = captured
                parts.append(DeferredTuplePart(
                    kind=DeferredPartKind.CAPTURE, expr=base,
                    indirect=indirect))
            elif any(d[:len(p)] == p for d in deferred):
                if wraps[i] is not None:
                    return None
                sub = build(elem, p)
                if sub is None:
                    return None
                parts.append(DeferredTuplePart(
                    kind=DeferredPartKind.TUPLE, cpp_type=sub[0],
                    parts=sub[1]))
            elif (wraps[i] is None
                  and isinstance(_peel_thir_coerce(elem), THIRLiteral)):
                parts.append(DeferredTuplePart(kind=DeferredPartKind.INLINE,
                                               expr=elem))
            else:
                et = getattr(elem, "result_type", None)
                trivial = wraps[i] is not None or (
                    et is not None and unwrap_qualifiers(et).is_value_type()
                    and not unwrap_qualifiers(et).is_expensive_copy())
                parts.append(DeferredTuplePart(
                    kind=DeferredPartKind.TEMP, expr=elem,
                    cpp_type=elem_cpps[i], wrap=wraps[i], move=not trivial))
        return cpp, tuple(parts)

    built = build(value, ()) if value is not None else None
    if built is None:
        return None
    return THIRFinallyDeferredReturn(tuple_cpp=built[0], tuple_parts=built[1],
                                     loc=loc)


def _peel_thir_coerce(e: THIRExpr) -> THIRExpr:
    while isinstance(e, THIRCoerce):
        e = e.expr
    return e


def _lower_deferred_tuple_return(stmt: TpyReturn, scope: '_LowerScope',
                                 lc: _LowerCtx, declared: dict,
                                 loc) -> THIRStmt:
    """A stamped tuple return: the value lowers through the ordinary return
    arms, then its members are split into the deferred parts. Sema stamps
    only shapes the parts can be read off, so a value that does not split
    rejects rather than falling back to an eager capture, which would copy
    members the finally chain can still change."""
    eager = _lower_stmt_dispatch(stmt, scope, deferred_tuple_eager=True)
    if not (isinstance(eager, THIRReturn) and eager.value is not None):
        note_detail("return.finally_deferred_tuple")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    deferred = _finally_deferred_tuple_recipe(stmt, eager.value, lc,
                                              declared, loc)
    if deferred is None:
        note_detail("return.finally_deferred_tuple")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    _witness("ret.finally_deferred_tuple")
    return deferred


def _resumable_deferred_recipe(ret: TpyReturn, lc: _LowerCtx,
                               declared: dict,
                               value: 'THIRExpr | None' = None,
                               ) -> 'THIRFinallyDeferredReturn | None':
    """The finally-deferred capture recipe a routed frame's return
    scaffolding consults, or None when sema did not stamp this return.

    A stamped shape the recipe table does not cover rejects the whole body:
    the scaffolding runs after routing is committed, and the alternative --
    an emit-time retraction of the sema move mark -- mutates analysis state
    that no gate can observe."""
    if not ret.finally_deferred_capture:
        return None
    if ret.finally_deferred_leaves:
        recipe = _finally_deferred_tuple_recipe(ret, value, lc, declared,
                                                getattr(ret, "loc", None))
    else:
        recipe = _finally_deferred_recipe(ret, lc, declared,
                                          getattr(ret, "loc", None))
    if recipe is None:
        note_detail("return.finally_deferred_capture")
        raise ThirUnsupported(stmt_reject_reason(ret))
    _witness("res.leaf_deferred_capture")
    return recipe


def _lower_overload_folded_if(
        stmt: TpyIf, lc: _LowerCtx,
        declared: dict[str, TpyType], *,
        in_branch: bool,
        loop_depth: int) -> 'THIRFoldedBlock | THIRFoldedIfChain | None':
    """The @overload-specialized if fold, walking `overload_if_fold`'s plan:
    a flat plan splices its statements (no `// if` comment, no brace scope;
    a terminating true arm sets the body-global truncation flag), a live
    plan emits the surviving links as the trimmed `if / else if` chain
    (THIRFoldedIfChain). None when the chain does not fold, so the ordinary
    narrowing arms take over. Live-chain machinery outside this slice
    rejects: branch decls, concrete extraction facts, and a temp-carrying
    condition past the first link."""
    plan = overload_if_fold(stmt, lc.overload_narrowing,
                            lc.overload_literal_facts)
    if plan is None:
        return None
    if plan.reject is not None:
        note_detail(plan.reject)
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if plan.flat is not None:
        body = _lower_stmts(plan.flat, lc, declared,
                            in_branch=in_branch, loop_depth=loop_depth)
        if plan.terminates:
            lc.overload_terminated = True
        return THIRFoldedBlock(stmts=body)
    branches: list[tuple] = []
    for i, node in enumerate(_bindings.scoped(lc, plan.live)):
        if lc.analyzer.if_branch_decls.get(node):
            note_detail("if.overload_live_branch_decls")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if _facts_have_concrete(node.then_type_facts):
            note_detail("if.overload_live_extraction")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        try:
            cond = _lower_truthy(node.condition, lc, declared,
                                 temps_ok=(i == 0))
        except ThirUnsupported as ex:
            raise ex.with_context(stmt_reject_reason(
                stmt, f"if.overload_live_cond:{ex.reason}")) from None
        body = _lower_stmts(node.then_body, lc, declared,
                            in_branch=True, loop_depth=loop_depth)
        branches.append((cond, body))
    else_body: tuple = ()
    if plan.else_body:
        else_body = _lower_stmts(plan.else_body, lc, declared,
                                 in_branch=True, loop_depth=loop_depth)
    return THIRFoldedIfChain(branches=tuple(branches), else_body=else_body)


def _overload_adjusted_return(stmt: TpyReturn, lc: _LowerCtx) -> TpyReturn:
    """The @overload specialization pair for a return: compat validation
    against the stub's return type, plus stripping a coerce sema built
    against the impl's union return (which may target a different member
    than this stub). An incompatible return is a user error, except under a
    literal
    specialization, where it is dead code the live branches eliminated. A
    stripped value shallow-copies the node -- the shared AST must never be
    mutated."""
    stub_ret = lc.overload_stub_return
    if stmt.value is None or stmt.value_type is None:
        return stmt
    rt = stub_ret
    vt = stmt.value_type
    if isinstance(vt, IntLiteralType):
        vt = BIGINT
    elif isinstance(vt, PendingViewType):
        vt = vt.family.owned_type
    try:
        lc.analyzer.compat.check_type_compatible(
            vt, rt, "return value", loc=stmt.loc, is_return=True)
    except SemanticError:
        if lc.overload_literal_facts:
            # Dead code the literal branch elimination drops rather than
            # diagnoses; dropping a statement is not a shape this arm can
            # express, so the body rejects.
            note_detail("return.overload_mismatch")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        emit_prims.reject_overload_return_mismatch(stmt.value_type, rt,
                                                   stmt.loc)
    value = stmt.value
    if isinstance(value, TpyCoerce):
        shape = repr_shape(rt)
        keep = (value.expected_type in (rt, shape)
                or (isinstance(shape, OptionalType)
                    and value.expected_type == shape.inner))
        if not keep:
            value = value.expr
    if value is stmt.value:
        return stmt
    adjusted = copy.copy(stmt)
    adjusted.value = value
    return adjusted


def _record_dunder_call(sub: TpySubscript, kind: SubscriptDunder,
                        receiver: THIRExpr, args: tuple[THIRExpr, ...],
                        lc: '_LowerCtx', loc) -> THIRMethodCall:
    """A subscript store or `del` as the explicit `t.__setitem__(k, v)` /
    `t.__delitem__(k)` call it is: a method call on `receiver` to the
    variant sema resolved, rendered by that variant's own binding."""
    fi = sub.dunder(kind)
    assert fi is not None
    binding = _bound_call_of(fi, fi.name)
    call = THIRMethodCall(
        result_type=VoidType(), receiver=_record_receiver(sub, receiver, lc,
                                                          kind),
        method_cpp=binding.method_cpp,
        native_function_name=binding.native_function_name,
        cpp_template=binding.cpp_template, args=args, loc=loc)
    return with_method_callee(with_method_stub(call, fi, lc.analyzer), fi,
                              lc.analyzer,
                              lambda: receiver_is_const(sub.obj, lc))


def _set_item(stmt: TpyStmt, sub: TpySubscript, target: THIRSubscript,
              value: THIRExpr, lc: '_LowerCtx', loc,
              held: tuple[THIRArgTemp, ...] = ()) -> THIRSetItem:
    """The element store `sub = value` of `stmt`, rendered by the
    `__setitem__` variant sema stamped (`_dunder_render`)."""
    render = _dunder_render(sub, SubscriptDunder.SET)
    if render is None:
        note_detail("setitem.unresolved_dunder")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    return THIRSetItem(
        target=target, value=value, setter=render.binding,
        unchecked_setter=render.unchecked,
        stub_callee=setitem_stub_callee(sub, target, lc.analyzer),
        held=held, loc=loc)


@dataclass(frozen=True)
class _AugSubscript:
    """The operands of `t[k] op= v`, spelled as the explicit
    `t.__setitem__(k, t.__getitem__(k) op v)` spells them: `read` is the
    getter call, `receiver` the receiver as the store reads it (without the
    getter's const pin) and `store_index` the key as the store reads it (on
    a record, converted at the setter's parameter). A target part whose
    evaluation is observable when repeated is read by name from the
    statement's `held` temps (`_aug_holds`)."""
    read: THIRSubscript
    receiver: THIRExpr
    store_index: THIRExpr


def _held_temp(init: THIRExpr, cpp_type: str | None, lc: '_LowerCtx',
               loc) -> tuple[THIRArgTemp, THIRTempRead]:
    """`init` held in an argument temp its statement declares, and a read of
    it by name."""
    init = stamped(init, lc)
    temp = THIRArgTemp(
        result_type=init.result_type, init=init, form=init.form,
        cpp_type=cpp_type, movable=False, read_id=next(lc.temp_read_ids),
        loc=loc)
    # A reference temp reads what its init reads; a value temp is its own
    # storage. A read of it reads the same.
    temp = _hoisted(replace(temp, source=(
        init.source if cpp_type == "auto&&" else stamped(temp, lc).source)), lc)
    return temp, THIRTempRead(result_type=temp.result_type, form=temp.form,
                              read_id=temp.read_id, source=temp.source,
                              loc=loc)


@contextmanager
def _aug_holds(stmt: TpyAugAssign, lc: '_LowerCtx',
               declared: dict[str, TpyType], loc
               ) -> Iterator[tuple[THIRArgTemp, ...]]:
    """The temps `stmt` holds its `hold_parts` in, each lowered once over
    the ones before it, in order; while open, the rest of the statement
    reads each part by name (`lc.prelowered`), as the argument temp an
    ordinary call would bind it in: how a reader passes it is the reader's
    own lowering. A fixed-width scalar or a view is held by value; anything
    else binds `auto&&` -- a result the statement creates lives to the end
    of its block, a reference reads the storage in place."""
    held: list[THIRArgTemp] = []
    saved = lc.prelowered
    entries = list(saved.items()) if saved is not None else []
    try:
        for part in stmt.hold_parts:
            lc.prelowered = IdentityMap(entries) if entries else saved
            e = part.expr
            t = lc.analyzer.get_expr_type(e)
            if part.receiver:
                # A call's reference result is read in place as a receiver,
                # as a member read off it is.
                use = (_ExprUse(result=_ExprResultUse.RECEIVER,
                                allow_temps=True, pos=SinkPos.RECEIVER,
                                forms=_ONLY_FIELD_RECV_BORROW | _LEND_OK)
                       if isinstance(e, (TpyCall, TpyMethodCall))
                       else _ExprUse(pos=SinkPos.RECEIVER,
                                     forms=_recv_forms(False, False)))
            elif (t is not None and not _select_node(e)
                  and not unwrap_qualifiers(t).is_value_type()):
                # A reference-type result is bound where it is, as a local
                # alias of it is; a new one is stored as a local's value.
                use = _ExprUse(result=(
                    _ExprResultUse.STORAGE if is_rvalue_source(lc.analyzer, e)
                    else _ExprResultUse.BORROW_BIND), allow_temps=True)
            else:
                use = _ExprUse(allow_temps=True)
            # A comprehension held whole is typed by its own container.
            # A container literal spells its own type: no slot supplies it.
            init = _spell_brace(_lower_storage_value(
                e, _comp_own_container(e, lc.analyzer) or t, lc, declared,
                loc, use=use), t, lc)
            temp, read = _held_temp(
                init, "auto" if holds_by_value(t) else "auto&&", lc, loc)
            held.append(temp)
            entries.append((e, read))
        lc.prelowered = IdentityMap(entries) if entries else saved
        if held:
            _witness("aug.held")
        yield tuple(held)
    finally:
        lc.prelowered = saved


def _setter_consumes_held_key(stmt: TpyAugAssign,
                              store_key: THIRExpr) -> bool:
    """Whether the record setter of `t[k] op= v` consumes the key as
    written, held as a value the statement created (sema's verdict on the
    held part): it moves rather than copies."""
    sub = stmt.target
    assert isinstance(sub, TpySubscript)
    key = sub.written_index if sub.written_index is not None else sub.index
    return (isinstance(store_key, THIRTempRead)
            and any(key is p.expr and p.setter_consumes
                    for p in stmt.hold_parts))


def _aug_subscript_operands(stmt: TpyAugAssign, lc: '_LowerCtx',
                            declared: dict[str, TpyType], *,
                            key_keep: 'Retention | None') -> _AugSubscript:
    sub = stmt.target
    assert isinstance(sub, TpySubscript)
    # The store's key as the variant sema resolved receives it, built
    # whichever node renders the store (`_record_key`).
    setter = sub.dunder(SubscriptDunder.SET)
    # The read's key takes the verdict the statement's admission took
    # (`aug_key_retention`).
    read = _lower_expr(sub, lc, declared, key_verdict=(key_keep,))
    assert isinstance(read, THIRSubscript)
    getter = sub.dunder(SubscriptDunder.GET)
    set_key = None
    if setter is not None and sub.setitem_index is not None:
        if _subscript_call(sub, SubscriptDunder.SET) is not None:
            set_key = _record_key(sub, sub.setitem_index, setter, lc,
                                  declared,
                                  keep=arg_retention(
                                      setter, 0, None, lc.analyzer,
                                      sub.setitem_index, recv=sub.obj))
        elif not (getter is not None and without_readonly(
                getter.params[0].type) == without_readonly(
                    setter.params[0].type)):
            # An element store whose key parameter differs from the
            # getter's: its key converted at its own parameter, an operand
            # of the lookup as the read's is. One the getter shares reads
            # the read's key.
            set_key = _lower_expr(sub.setitem_index, lc, declared,
                                  use=_ExprUse(pos=SinkPos.OPERAND,
                                               forms=_LEND_OK),
                                  field_owned_str_ok=isinstance(
                                      sub.setitem_index, TpyFieldAccess))
    # The store's receiver is the read's without the getter's const pin.
    receiver = read.receiver
    if (isinstance(receiver, THIRCoerce)
            and receiver.coercion_name == "const_receiver"):
        receiver = receiver.expr
    return _AugSubscript(read, receiver,
                         set_key if set_key is not None else read.index)


def _received_value(received: TpyExpr, value: THIRExpr, lc: '_LowerCtx',
                    declared: dict[str, TpyType]) -> THIRExpr:
    """The operation's result `value` of `x op= v` as the store
    receives it (`TpyAugAssign.stored_value`): the conversion sema put on
    the stand-in for the result, lowered over `value`."""
    stand_in = received
    while isinstance(stand_in, TpyCoerce):
        stand_in = stand_in.expr
    saved = lc.prelowered
    lc.prelowered = IdentityMap([*(saved.items() if saved is not None else ()),
                                 (stand_in, value)])
    try:
        return _lower_expr(received, lc, declared,
                           use=_ExprUse(allow_temps=True))
    finally:
        lc.prelowered = saved


def _store_item(stmt: TpyStmt, target: THIRExpr, value: THIRExpr,
                lc: '_LowerCtx', loc,
                held: tuple[THIRArgTemp, ...] = ()) -> THIRStmt:
    """The store `t[k] = v` of `stmt` -- plain, or an augmented store's
    write-back -- over the store target's receiver and key and the value,
    answered by the setter's contract for every receiver: the call
    `_record_dunder_call` to it, or the element store (`_set_item`) when it
    declares the element-access contract."""
    sub = stmt.target
    assert isinstance(sub, TpySubscript) and isinstance(target, THIRSubscript)
    if _subscript_call(sub, SubscriptDunder.SET) is None:
        return _set_item(stmt, sub, target, value, lc, loc, held=held)
    return THIRExprStmt(expr=_record_dunder_call(
        sub, SubscriptDunder.SET, target.receiver, (target.index, value),
        lc, loc), held=held, loc=loc)


def _ptr_union_value(stmt: TpyVarDecl, ptr_u: 'UnionType', is_const: bool,
                     lc: _LowerCtx, declared: dict[str, TpyType],
                     loc) -> THIRExpr:
    """The value a pointer-variant binding (`::tpy::Union<A*, B*>`) takes
    off a slotless source: None stores the monostate member bare; a
    value-variant field or element lvalue lifts via
    `to_[const_]ptr_variant`; a same-union name or a ptr-variant-returning
    call lands bare."""
    init = stmt.init
    if isinstance(init, TpyNoneLiteral):
        return THIRLiteral(result_type=ptr_u, value=None, form=Form.STORAGE,
                           loc=loc)
    if isinstance(init, TpyFieldAccess) or is_property_getter_read(init):
        # A @property read is a getter CALL that hands back the FIELD's
        # storage variant by reference, so it lifts exactly as the plain
        # member read does.
        if is_property_getter_read(init):
            src = _lower_expr(init, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
        else:
            src = _lower_field_source(init, lc, declared)
        return THIRFormConvert(result_type=ptr_u, value=src,
                               form=Form.BORROW, is_const=is_const, loc=loc)
    if isinstance(init, TpySubscript):
        # A union container-element read (`pet = pets["rex"]`): the checked
        # getitem's value-variant lvalue, lifted.
        return THIRFormConvert(
            result_type=ptr_u,
            value=_lower_expr(init, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            form=Form.BORROW, is_const=is_const, loc=loc)
    # A call source takes the STORAGE use the call-ret union row admits; a
    # SAME-union name copy is a whole-variant assign, so an assign-narrowed
    # divergent source read is safe at this sink.
    divergent_ok = (isinstance(init, TpyName)
                    and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        declared.get(init.name)))) == ptr_u)
    return _lower_expr(
        init, lc, declared,
        use=(_ExprUse(result=_ExprResultUse.STORAGE)
             if isinstance(init, (TpyCall, TpyMethodCall)) else _ExprUse()),
        allow_union_divergent=divergent_ok)


# --- first declarations ------------------------------------------------------
#
# The binding planner decides how a first declaration's name is held
# (`BindingRepr.form`) and how its initializer constructs that form
# (`BindingRepr.construct`); the arm below builds the node for that
# construction. A builder keeps only what decides how THIS write builds its
# value from its initializer, and the shapes it rejects.

# A first-declaration builder: (the declaration, its declared type, its
# record, the scope it lowers in, its location) -> the declaration's node.
_DeclBuilder = Callable[[TpyVarDecl, TpyType, BindingRepr, '_LowerScope',
                         object], 'THIRStmt']


def _record_facts(rec: BindingRepr) -> dict:
    """The declaration node's fields its record decides: the local shape and
    its const. An `auto&&` storage-tuple alias deduces its const, so the node
    spells none."""
    shape = _bindings.decl_shape(rec)
    return {"cpp_local_representation": shape,
            "is_const": rec.const
            and shape is not LocalBinding.STORAGE_TUPLE_ALIAS}


def _decl_type(rec: BindingRepr) -> TpyType:
    """The type the binding holds: its record's effective type where the
    declaration's representation differs from the declared type."""
    return rec.effective_type if rec.effective_type is not None else rec.type


def _lower_no_init_decl(stmt: TpyVarDecl, scope: '_LowerScope',
                        loc) -> THIRStmt:
    """An annotation-only declaration (`x: int32`, `h: Handle | None`): a
    default-constructed value slot the later assignment writes, or the
    pointer-repr Optional's null pointer (`Handle* h = nullptr;`, its
    reseats riding the pointer-local assign arms)."""
    lc, declared = scope.lc, scope.declared
    construct = (lc.declaring(stmt.name).construct
                 if stmt.name not in declared else None)
    if (construct is BindingConstruct.NO_INIT_OPT_PTR
            and not scope.in_branch):
        rec = lc.declaring(stmt.name)
        node = _lower_opt_ptr_slot_decl(stmt, rec.type, rec, lc, declared,
                                        loc)
        declared[stmt.name] = rec.type
        return node
    if (construct is BindingConstruct.NO_INIT_VALUE
            and not (scope.in_branch and not scope.branch_decls_ok)):
        nt = _decl_type(lc.declaring(stmt.name))
        _witness("decl.no_init_value")
        declared[stmt.name] = nt
        return THIRVarDecl(name=stmt.name, resolved_type=nt, init=None,
                           loc=loc)
    note_detail("decl.no_init")
    raise ThirUnsupported(stmt_reject_reason(stmt))


def _lower_first_decl(stmt: TpyVarDecl, vtype: TpyType,
                      scope: '_LowerScope', loc) -> 'THIRStmt | None':
    """The first declaration of a name, built for how its record's form is
    constructed; None for a record the plain-value declaration arms build."""
    rec = scope.lc.declaring(stmt.name)
    if rec.frame_slot:
        # A frame slot is a member of the resumable frame: the frame's own
        # arms write it, and a local declared here would shadow it.
        raise BindingTableError(
            f"a first declaration of frame slot {stmt.name!r} in "
            f"{scope.lc.func.name} reached the local declaration arms")
    builder = (_FIRST_DECL_BUILDERS.get(rec.construct)
               if rec.site_kind == "decl" else None)
    if builder is None:
        return None
    return builder(stmt, vtype, rec, scope, loc)


def _decl_er_bind(stmt, vtype, rec, scope, loc):
    lc = scope.lc
    _check_error_return_init(stmt, stmt.init, lc.analyzer)
    er_fi = _error_return_stmt_fi(stmt.init, lc.analyzer)
    bind = (_error_return_alias_bind
            if rec.construct is BindingConstruct.ER_ALIAS
            else _error_return_value_bind)
    return bind(stmt, stmt.name, stmt.init, er_fi, vtype, lc, scope.declared,
                loc)


def _decl_record_slot(stmt, vtype, rec, scope, loc):
    node = _lower_record_ptr_slot_decl(stmt, vtype, rec, scope.lc,
                                       scope.declared, loc)
    scope.declared[stmt.name] = vtype
    return node


def _decl_container_slot(stmt, vtype, rec, scope, loc):
    node = _lower_container_ptr_slot_decl(stmt, rec, scope.lc,
                                          scope.declared, loc)
    scope.declared[stmt.name] = _decl_type(rec)
    return node


def _decl_storage_opt_lift(stmt, vtype, rec, scope, loc):
    """A storage-opt local's name lifted to `T*` (`first = it` off a `for it
    in pairs:` over `list[P | None]`): one line with no hoist or slot, so a
    branch-first declaration renders in place."""
    lc, declared = scope.lc, scope.declared
    facts = _record_facts(rec)
    declared[stmt.name] = vtype
    _witness("decl.storage_opt_name_lift")
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype,
        init=THIRFormConvert(
            result_type=vtype,
            value=_lower_expr(stmt.init, lc, declared,
                              allow_whole_optional=True),
            form=Form.BORROW, is_const=facts["is_const"], loc=loc),
        cpp_type=lc.render_type(unwrap_readonly(
            pointer_repr_optional(vtype).inner)),
        form=Form.BORROW, loc=loc, **facts)


def _decl_dyn_own_erased(stmt, vtype, rec, scope, loc):
    return _lower_dyn_own_erased_call_decl(stmt, rec, scope.lc,
                                           scope.declared, loc)


def _decl_coro_frame_local(stmt, vtype, rec, scope, loc):
    node = _lower_coro_frame_local_decl(stmt, scope.lc, scope.declared, loc)
    scope.declared[stmt.name] = vtype
    return node


def _decl_dyn(stmt, vtype, rec, scope, loc):
    node = _lower_dyn_protocol_decl(stmt, vtype, rec, scope.lc,
                                    scope.declared, loc)
    scope.declared[stmt.name] = vtype
    return node


def _decl_opt_btuple(stmt, vtype, rec, scope, loc):
    return _lower_opt_btuple_decl(
        stmt, vtype, _optional_borrow_tuple(vtype, scope.lc.analyzer), rec,
        scope.lc, scope.declared, loc)


def _decl_borrow_local(stmt, vtype, rec, scope, loc):
    scope.declared[stmt.name] = vtype
    return _lower_borrow_local(stmt, vtype, rec, scope.lc, scope.declared,
                               loc)


def _decl_opt_call_passthrough(stmt, vtype, rec, scope, loc):
    """A borrow-returning pointer-repr Optional call (or ternary of such):
    the `T*` result is already the binding's shape, so it binds bare
    (`Point* r1 = get_or_none(true, p);`) -- no slot materializes."""
    lc, declared = scope.lc, scope.declared
    check_polymorphic_rvalue_opt_rebind(
        stmt.name, vtype, stmt.init, lc.prescan.rvalue_reassigned,
        lc.analyzer)
    src = _lower_expr(
        stmt.init, lc, declared,
        # The decl is a flush position; a generic callee's ref-slot arg
        # temp hoists here like any statement's.
        use=_ExprUse(pos=SinkPos.LOCAL_DECL,
                     forms=_decl_slot_forms(
                         vtype, lc.analyzer, whole_tuple_call=False,
                         from_call=True, ptr_local=True),
                     allow_temps=True))
    _witness("decl.opt_call_passthrough")
    declared[stmt.name] = vtype
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=src,
        cpp_type=lc.render_type(pointer_repr_optional(vtype).inner),
        form=Form.BORROW, loc=loc, **_record_facts(rec))


def _decl_opt_storage_call(stmt, vtype, rec, scope, loc):
    """An Own-declared optional-returning call: the storage optional
    materializes in a slot, the binding lifts the pointer
    (`std::optional<T> __slot_N = make_some();`
    `T* s = ::tpy::optional_to_ptr(__slot_N);`)."""
    lc, declared = scope.lc, scope.declared
    check_polymorphic_rvalue_opt_rebind(
        stmt.name, vtype, stmt.init, lc.prescan.rvalue_reassigned,
        lc.analyzer)
    src = _flush_witness(
        "flush.vardecl",
        _lower_expr(
            stmt.init, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True),
            allow_whole_optional=True))
    _witness("decl.opt_storage_call")
    declared[stmt.name] = vtype
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype,
        kind=PtrSlotKind.OPT_STORAGE_CALL, init=src,
        cpp_type=lc.render_type(
            unwrap_readonly(pointer_repr_optional(vtype).inner)),
        loc=loc)


def _decl_opt_ptr(stmt, vtype, rec, scope, loc):
    lc = scope.lc
    check_polymorphic_rvalue_opt_rebind(
        stmt.name, vtype, stmt.init, lc.prescan.rvalue_reassigned,
        lc.analyzer)
    node = _lower_opt_ptr_slot_decl(stmt, vtype, rec, lc, scope.declared, loc)
    scope.declared[stmt.name] = vtype
    return node


def _decl_opt_own_param_lift(stmt, vtype, rec, scope, loc):
    """`y = x` off an `Own[P | None]` param (`P* y =
    ::tpy::optional_to_ptr(x);`): the param itself is the storage, so the
    declaration is a pure lift, no slot."""
    lc, declared = scope.lc, scope.declared
    src = THIRFormConvert(
        result_type=vtype,
        value=_lower_expr(stmt.init, lc, declared,
                          allow_whole_optional=True),
        form=Form.BORROW, loc=loc)
    _witness("decl.opt_own_param_lift")
    declared[stmt.name] = vtype
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=src,
        cpp_type=lc.render_type(unwrap_readonly(
            pointer_repr_optional(vtype).inner)),
        form=Form.BORROW, loc=loc, **_record_facts(rec))


def _decl_storage_tuple_alias(stmt, vtype, rec, scope, loc):
    """`auto&& t = <storage tuple lvalue>`: the local aliases the source's
    storage, so a read off it is STORAGE form (lent element by element at
    a borrow boundary); `auto&&` binds the source directly."""
    lc, declared = scope.lc, scope.declared
    if isinstance(stmt.init, TpyFieldAccess):
        src = _lower_field_source(stmt.init, lc, declared)
    elif isinstance(stmt.init, TpySubscript):
        src = _lower_expr(stmt.init, lc, declared, subscript_prechecked=True)
        if isinstance(stmt.init.obj, TpyFieldAccess):
            _witness("decl.storage_tuple_alias_field_subscript")
    else:
        # An alias of an alias (`u = t`): the name renders as the storage
        # name it already is, never moved -- an alias source is
        # single-assignment.
        src = _lower_expr(stmt.init, lc, declared)
    declared[stmt.name] = vtype
    tuple_alias = (THIRTupleStorageAlias(stmt.init.name, rec.tuple_layout)
                   if rec.tuple_layout is not None else None)
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=src, form=Form.STORAGE,
        tuple_storage_alias=tuple_alias, loc=loc, **_record_facts(rec))


def _decl_comprehension(stmt, vtype, rec, scope, loc):
    """A comprehension local: the init renders as the whole stmt-expr, a
    self-contained render wherever it lands."""
    lc, declared = scope.lc, scope.declared
    if (stmt.name in lc.prescan.reassigned
            or stmt.name in lc.prescan.hoisted
            or stmt.name in lc.prescan.move_through):
        raise ThirUnsupported(stmt_reject_reason(stmt))
    comp = _comprehensions._lower_comprehension(
        stmt.init, vtype, lc, declared, scope.admission_pointers())
    declared[stmt.name] = vtype
    return THIRVarDecl(name=stmt.name, resolved_type=vtype, init=comp,
                       loc=loc)


def _decl_tparam_call(stmt, vtype, rec, scope, loc):
    """An open-T local off a T-returning call in a generic body (`item =
    box.get()` -> `::tpy::val_or_ref_t<T> item = box.get();`): the trait
    resolves value-vs-ref per instantiation, so the local reads bare."""
    lc, declared = scope.lc, scope.declared
    trait = "::tpy::val_or_cref_t" if rec.const else "::tpy::val_or_ref_t"
    init = _lower_expr(stmt.init, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    declared[stmt.name] = vtype
    _witness("decl.tparam_call")
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=init,
        cpp_type=f"{trait}<{lc.render_type(vtype)}>", loc=loc)


def _decl_ptr_variant(stmt, vtype, rec, scope, loc):
    """A pointer-variant union local (`::tpy::Union<A*, B*>`) bound off a
    same-union value or a lifted value-variant lvalue."""
    lc, declared = scope.lc, scope.declared
    ptr_u = _eligible_ptr_union_wide(vtype, lc.analyzer)
    facts = _record_facts(rec)
    u_init = _ptr_union_value(stmt, ptr_u, facts["is_const"], lc, declared,
                              loc)
    declared[stmt.name] = vtype
    return THIRVarDecl(
        name=stmt.name, resolved_type=ptr_u, init=u_init,
        cpp_type=(ptr_u.to_cpp_const_ptr_variant() if facts["is_const"]
                  else ptr_u.to_cpp_ptr_variant()),
        form=Form.BORROW, loc=loc, **facts)


def _decl_ptr_variant_slot(stmt, vtype, rec, scope, loc):
    """A pointer-variant union local over a slot of its own: a concrete-MEMBER
    rvalue materializes a value-variant `__slot_N` and lifts it; a
    concrete-member lvalue NAME binds its address (`v{&(name)}`)."""
    lc, declared = scope.lc, scope.declared
    if scope.in_branch and rec.rebind_slot:
        # The rebind slot would hoist to function scope while the decl sits
        # in the branch -- a placement this arm does not render.
        note_detail("decl.branch_ptr_union")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    ptr_u = _eligible_ptr_union_wide(vtype, lc.analyzer)
    slot_kind = _ptr_union_slot_kind(stmt.init, ptr_u, declared, lc,
                                     lc.analyzer)
    if slot_kind is None:
        note_detail("decl.ptr_union_source")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if slot_kind is PtrSlotKind.UNION_RVALUE:
        # A flush position: a member-ctor init's own arg temps land before
        # the slot line.
        u_slot_init = _lower_expr(
            stmt.init, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                         allow_temps=True))
        _witness("decl.union_slot_rvalue")
    else:
        u_slot_init = _lower_expr(stmt.init, lc, declared)
        _witness("decl.union_addr")
    declared[stmt.name] = vtype
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=ptr_u, kind=slot_kind,
        init=u_slot_init,
        cpp_type=(ptr_u.to_cpp_const_ptr_variant() if rec.const
                  else ptr_u.to_cpp_ptr_variant()),
        val_cpp=_union_storage_val_cpp(ptr_u), loc=loc)


def _decl_storage_call_tuple(stmt, vtype, rec, scope, loc):
    """An owning tuple call (`t = make_pair(5)` for `-> Own[tuple[int32,
    Box]]`): the storage copy, `auto` when the tuple has ref elements, so
    element reads stay storage (`std::get<i>(t)`, no pointer lift)."""
    lc, declared = scope.lc, scope.declared
    ct = _decl_type(rec)
    init = _flush_witness(
        "flush.vardecl",
        _lower_expr(stmt.init, lc, declared,
                    use=_ExprUse(
                        result=_ExprResultUse.STORAGE, allow_temps=True,
                        pos=SinkPos.LOCAL_DECL,
                        # The record's construction IS the whole-consumption
                        # predicate; a tuple of owned tuples has no
                        # pointer-repr element, so the slot's shape alone
                        # cannot say so.
                        forms=_decl_slot_forms(
                            ct, lc.analyzer, whole_tuple_call=True,
                            from_call=True, ptr_local=False))))
    declared[stmt.name] = ct
    _witness("decl.storage_call_tuple")
    return THIRVarDecl(
        name=stmt.name, resolved_type=ct, init=init,
        cpp_type="auto" if ct.has_ref_elements() else None,
        form=Form.STORAGE, loc=loc)


def _decl_iterator_object(stmt, vtype, rec, scope, loc):
    """An iterator object off a generator call (`it = g()`): `auto it =
    g();`, fed to the universal __iter__/__next__ loop. The init lowers
    under the ITERABLE use, the one position the call gates admit a
    generator at."""
    lc, declared = scope.lc, scope.declared
    init = _lower_expr(stmt.init, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                    allow_temps=True))
    _witness("decl.iterator_object")
    declared[stmt.name] = vtype
    return THIRVarDecl(name=stmt.name, resolved_type=vtype, init=init,
                       cpp_type="auto", loc=loc)


def _decl_btuple_rebound(stmt, vtype, rec, scope, loc):
    """A reassigned pointer-repr tuple local, declared in the one borrow
    shape every binding of it holds (`std::tuple<.., T*> t`): a binding
    owning an element is the pointer tuple's own arm
    (`_lower_ptr_tuple_binding`); otherwise a storage lvalue lifts
    element-wise, and a REF-capture literal or a borrow-tuple name binds
    directly. Const element pointers follow the whole-body fixpoint."""
    lc, declared = scope.lc, scope.declared
    an = lc.analyzer
    bt = _decl_type(rec)
    if rec.ptr_tuple is not None:
        node = _lower_ptr_tuple_binding(stmt, rec.ptr_tuple, False, lc,
                                        declared, loc)
        if node is not None:
            return node
        if not _borrow_tuple_source_ok(stmt.init, lc):
            _reject_ptr_tuple_select(stmt, stmt.name)
            note_detail("decl.ptr_tuple_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
    elem_const = rec.const
    borrow_cpp = (bt.to_cpp_return_const() if elem_const
                  else bt.to_cpp_return())
    declared[stmt.name] = bt
    if isinstance(stmt.init, TpyTupleLiteral):
        _reject_value_elem_at_borrow_tuple(stmt, stmt.name, stmt.init, bt)
        binit = _lower_borrow_tuple_literal(stmt.init, bt, lc, declared)
        _witness("decl.btuple_literal")
        return THIRVarDecl(
            name=stmt.name, resolved_type=bt, init=binit,
            tuple_layout=tuple_layout(bt, an, borrow=True,
                                      readonly=elem_const),
            cpp_type=borrow_cpp, form=Form.BORROW, loc=loc)
    if isinstance(stmt.init, TpyName):
        # The source already holds the borrow form the decl spells, so the
        # pointer-tuple copy re-aliases the same elements.
        if _holds_elements_inline(stmt.init.name, lc):
            _reject_inline_repoint(stmt, stmt.name, stmt.init.name)
        init = _lower_expr(stmt.init, lc, declared)
        _witness("decl.btuple_name_copy")
        return THIRVarDecl(
            name=stmt.name, resolved_type=bt, init=init,
            tuple_layout=tuple_layout(bt, an, borrow=True,
                                      readonly=elem_const),
            cpp_type=borrow_cpp, form=Form.BORROW, loc=loc)
    src = _lower_expr(stmt.init, lc, declared,
                      use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                      subscript_prechecked=True)
    _witness("decl.btuple_lift")
    return THIRVarDecl(
        name=stmt.name, resolved_type=bt,
        init=to_local(src, bt, BindingForm.BORROW_TUPLE, an,
                      const=elem_const),
        cpp_type=borrow_cpp, form=Form.BORROW, loc=loc)


def _decl_btuple_alias(stmt, vtype, rec, scope, loc):
    """A borrow-tuple local bound off a borrow-tuple name, a ternary of two
    such names (or of two mixed own-borrow calls), or a call returning one:
    the source renders bare and the new binding points at the same elements
    -- `auto&` where a source holds a record element inline, which a copy
    would duplicate."""
    lc, declared = scope.lc, scope.declared
    an = lc.analyzer
    src_bt = _decl_type(rec)
    if (isinstance(stmt.init, (TpyIfExpr, TpyCall, TpyMethodCall))
            and src_bt.is_mixed_own()):
        _witness("decl.mixed_own_alias")
    from_call = isinstance(stmt.init, (TpyCall, TpyMethodCall))
    init = _lower_expr(
        stmt.init, lc, declared,
        use=_ExprUse(result=_ExprResultUse.VALUE, pos=SinkPos.LOCAL_DECL,
                     forms=_decl_slot_forms(
                         src_bt, an, whole_tuple_call=False,
                         from_call=from_call, ptr_local=False),
                     allow_temps=from_call))
    declared[stmt.name] = src_bt
    _witness("decl.btuple_alias")
    ref = rec.construct is BindingConstruct.BTUPLE_REF_ALIAS
    return THIRVarDecl(
        name=stmt.name, resolved_type=src_bt, init=init,
        tuple_layout=tuple_layout(src_bt, an, borrow=True,
                                  readonly=rec.const),
        cpp_type="auto&" if ref else "auto", form=Form.BORROW, loc=loc)


def _decl_btuple_inline_arms(stmt, vtype, rec, scope, loc):
    """A borrow-tuple alias whose source arms hold different elements
    inline: no one `auto&` aliases them all."""
    note_detail("decl.btuple_alias_inline_arms")
    raise ThirUnsupported(stmt_reject_reason(stmt))


def _decl_optview_shim(stmt, vtype, rec, scope, loc):
    """`local: Optional[str] = s` off a BORROW-form value-opt view param: the
    owned slot takes the view->owned shim (`s ?
    std::make_optional(<conv>(*s)) : std::nullopt`) the return and MIL sinks
    spell."""
    lc = scope.lc
    scope.declared[stmt.name] = vtype
    _witness("decl.optview_shim")
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype,
        init=THIROptViewArg(result_type=vtype, name=stmt.init.name,
                            form=Form.VALUE, loc=loc),
        cpp_type=lc.render_type(vtype), loc=loc)


def _decl_btuple_literal(stmt, vtype, rec, scope, loc):
    """A REF-capture tuple literal binds the BORROW form (`auto t =
    std::tuple<int32_t, Box*>{1, &(b)};`): the local aliases its element
    sources, its reads keyed on the declared pointer-repr type like a
    borrow-tuple param's."""
    lc, declared = scope.lc, scope.declared
    slot_bt = _decl_type(rec)
    try:
        binit = _lower_borrow_tuple_literal(stmt.init, slot_bt, lc, declared)
    except ThirUnsupported as ex:
        raise ex.with_context(stmt_reject_reason(
            stmt, f"decl.tuple_literal_shape:{ex.reason}")) from None
    declared[stmt.name] = slot_bt
    _witness("btuple.decl")
    return THIRVarDecl(
        name=stmt.name, resolved_type=slot_bt, init=binit,
        tuple_layout=binit.tuple_layout, cpp_type="auto", form=Form.BORROW,
        loc=loc)


def _decl_tuple_literal_wide(stmt, vtype, rec, scope, loc):
    """A VALUE-capture literal of container / value-union elements (`t =
    ({"a": 1}, 42)`): `auto` for ref elements."""
    lc, declared = scope.lc, scope.declared
    wide_t = _decl_type(rec)
    try:
        winit = _lower_tuple_literal(stmt.init, wide_t, lc, declared)
    except ThirUnsupported as ex:
        raise ex.with_context(stmt_reject_reason(
            stmt, f"decl.tuple_literal_shape:{ex.reason}")) from None
    declared[stmt.name] = wide_t
    _witness("decl.tuple_literal_wide")
    return THIRVarDecl(
        name=stmt.name, resolved_type=wide_t, init=winit,
        cpp_type="auto" if wide_t.has_ref_elements() else None,
        form=Form.STORAGE, loc=loc)


def _decl_tuple_literal_record(stmt, vtype, rec, scope, loc):
    """A VALUE-capture literal of record / Own elements binds the tuple BY
    VALUE: the local owns its elements, so element reads stay storage (no
    pointer lift). The literal's elements built in place make its owned
    layout."""
    lc, declared = scope.lc, scope.declared
    an = lc.analyzer
    tuple_t = _decl_type(rec)
    _witness("decl.tuple_literal")
    try:
        init = _lower_tuple_literal(stmt.init, tuple_t, lc, declared)
    except ThirUnsupported as ex:
        raise ex.with_context(stmt_reject_reason(
            stmt, f"decl.tuple_literal_shape:{ex.reason}")) from None
    declared[stmt.name] = tuple_t
    _witness("decl.storage_record_tuple")
    # A record with no layout (a reassigned name's writes build different
    # ones) leaves this write to lay out its own literal.
    layout = (rec.tuple_layout if rec.tuple_layout is not None
              else owned_literal_layout(stmt.init, tuple_t, an))
    if layout is not None:
        init = replace(init, tuple_layout=layout)
    return THIRVarDecl(
        name=stmt.name, resolved_type=tuple_t, init=init,
        cpp_type="auto" if tuple_t.has_ref_elements() else None,
        form=Form.STORAGE, loc=loc, tuple_layout=layout,
        storage_placement=(THIRStoragePlacement.SCOPE if layout is not None
                           else None))


_FIRST_DECL_BUILDERS: dict[BindingConstruct, _DeclBuilder] = {
    BindingConstruct.ER_ALIAS: _decl_er_bind,
    BindingConstruct.ER_BIND: _decl_er_bind,
    BindingConstruct.RECORD_SLOT: _decl_record_slot,
    BindingConstruct.RECORD_HOISTED_SLOT: _decl_record_slot,
    BindingConstruct.CONTAINER_SLOT: _decl_container_slot,
    BindingConstruct.STORAGE_OPT_LIFT: _decl_storage_opt_lift,
    BindingConstruct.DYN_OWN_ERASED: _decl_dyn_own_erased,
    BindingConstruct.CORO_FRAME_LOCAL: _decl_coro_frame_local,
    BindingConstruct.DYN: _decl_dyn,
    BindingConstruct.OPT_BTUPLE: _decl_opt_btuple,
    BindingConstruct.REF_ALIAS: _decl_borrow_local,
    BindingConstruct.POINTER: _decl_borrow_local,
    BindingConstruct.REBIND_SLOT: _decl_borrow_local,
    BindingConstruct.OPTIONAL_TO_PTR: _decl_borrow_local,
    BindingConstruct.OPT_CALL_PASSTHROUGH: _decl_opt_call_passthrough,
    BindingConstruct.OPT_STORAGE_CALL: _decl_opt_storage_call,
    BindingConstruct.OPT_PTR: _decl_opt_ptr,
    BindingConstruct.OPT_OWN_PARAM_LIFT: _decl_opt_own_param_lift,
    BindingConstruct.STORAGE_TUPLE_ALIAS: _decl_storage_tuple_alias,
    BindingConstruct.STORAGE_TUPLE_CHAIN: _decl_storage_tuple_alias,
    BindingConstruct.COMPREHENSION: _decl_comprehension,
    BindingConstruct.TPARAM_CALL: _decl_tparam_call,
    BindingConstruct.PTR_VARIANT: _decl_ptr_variant,
    BindingConstruct.PTR_VARIANT_SLOT: _decl_ptr_variant_slot,
    BindingConstruct.STORAGE_CALL_TUPLE: _decl_storage_call_tuple,
    BindingConstruct.ITERATOR_OBJECT: _decl_iterator_object,
    BindingConstruct.BTUPLE_REBOUND: _decl_btuple_rebound,
    BindingConstruct.BTUPLE_ALIAS: _decl_btuple_alias,
    BindingConstruct.BTUPLE_REF_ALIAS: _decl_btuple_alias,
    BindingConstruct.BTUPLE_INLINE_ARMS: _decl_btuple_inline_arms,
    BindingConstruct.OPTVIEW_SHIM: _decl_optview_shim,
    BindingConstruct.BTUPLE_LITERAL: _decl_btuple_literal,
    BindingConstruct.TUPLE_LITERAL_WIDE: _decl_tuple_literal_wide,
    BindingConstruct.TUPLE_LITERAL_RECORD: _decl_tuple_literal_record,
}


def _reseat_const_ok(name: str, lc: _LowerCtx) -> bool:
    """Whether a reseat of pointer slot `name` may take its source's const:
    a slot with a const source re-points only as a const pointer (the
    planner's slot verdict; a frame pointer field's own const)."""
    rec = lc.declaring(name)
    return (not rec.const_source or rec.const
            or ((lc.frame_local(name, FrameLocalClass.ALIAS_PTR)
                 or lc.frame_local(name, FrameLocalClass.REBIND_PTR))
                and lc.binding(name).frame_const))


@dataclass(frozen=True)
class _ConvertSource:
    """A reseat source the target's own render converts (`&(h.f)` through
    the BORROW convert) rather than an arm that re-points the local
    itself."""
    src: THIRExpr


def _reseat_lvalue_source(stmt: TpyVarDecl, want: TpyType, lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          opt_target: 'OptionalType | None'
                          ) -> 'THIRStmt | _ConvertSource | None':
    """The source arms of a slotless pointer local's reseat onto an lvalue,
    one list for both targets: a plain pointer local (`opt_target` None) and
    a pointer-repr Optional local (`opt_target` its type). `want` is the
    pointee the source must denote. An arm that re-points the local returns
    its statement; a field or a param that reads converted returns the
    source for the target's convert; None when no arm takes the source. The
    render is the target's where they differ: an Optional element of a tuple
    converts to the Optional local's pointer (`to_local`)."""
    analyzer = lc.analyzer
    loc = stmt.loc
    target = _var_decl_type(stmt, analyzer)
    const = lc.declaring(stmt.name).const
    # A bare record PARAM reseat (`x = b;` -> `x = &(b);`), the sibling
    # of the F1-record field reseat below.
    if _f1_param_lvalue_reseat_ok(stmt.init, want, declared, lc, analyzer):
        src_name = _lower_expr(stmt.init, lc, declared)
        if (src_name.form is Form.BORROW
                and analyzer.registry.is_subclass_of_or_equal(
                    src_name.result_type, want)):
            if isinstance(src_name, THIRName) and src_name.cpp is None:
                # A plain record-param `T&` lvalue: the same PTR_ADDR
                # address-of as the storage-local rung (`x = &(b);`), a
                # subclass param binding the base pointer implicitly. A
                # spelled (narrowed/deref) source stays out. (A type-differing
                # source -- e.g. a readonly-wrapped binding -- stays a real
                # convert.)
                _witness("reseat.param_name")
                return THIRPtrLocalRebind(
                    alias_binding=alias_binding(src_name, target, const,
                                                analyzer),
                    name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                    value=src_name, loc=loc)
            note_detail("decl.reseat_param_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        return _ConvertSource(src_name)
    if _f2_reseat_ok(stmt.init, declared, analyzer):
        return _ConvertSource(_lower_field_source(stmt.init, lc, declared))
    if (isinstance(stmt.init, TpyName)
            and stmt.init.name in declared
            and not lc.binding(stmt.init.name).pointer
            and not lc.binding(stmt.init.name).rebind_slot
            and not lc.binding(stmt.init.name).optional_storage
            and stmt.init.name not in lc.narrow.narrowed
            and _reseat_const_ok(stmt.name, lc)
            and is_plain_nonvalue(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(declared[stmt.init.name]))))
            and analyzer.registry.is_subclass_of_or_equal(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[stmt.init.name]))), want)):
        # A plain same-type -- or subclass-record -- storage LOCAL lvalue
        # (`items = base;` -> `items = &(base);`, `pet = &(cat);`): the
        # rebind's address-of catch-all for a name that renders bare
        # storage. The `&` lives in the PTR_ADDR emit (a FormConvert over
        # the BORROW-form name read would be the no-op node the validator
        # rejects). Pointer-form and narrowed sources stay on their own
        # rungs.
        _witness("reseat.storage_name")
        source = _lower_expr(stmt.init, lc, declared)
        return THIRPtrLocalRebind(
            alias_binding=alias_binding(source, target, const, analyzer),
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=source, loc=loc)
    if (isinstance(stmt.init, TpyName)
            and stmt.init.name in lc.narrow.narrowed
            and lc.binding(stmt.init.name).ptr_variant
            # A const-pointee subject's alias binds `const M&`; its address
            # would be a `const M*` at this mutable slot.
            and not _narrow_subject_const(stmt.init.name, lc)
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared.get(stmt.init.name)))) == want):
        # A narrowed ptr-variant union's extraction alias binds `M&`, so
        # re-pointing off it takes the plain address-of (`form =
        # &(__data);`) -- the same lift the subscript element arm uses, over
        # the renamed alias.
        _witness("reseat.narrow_alias_addr")
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=_lower_expr(stmt.init, lc, declared), loc=loc)
    if (isinstance(stmt.init, (TpyCall, TpyMethodCall))
            and call_result_holdable(analyzer, stmt.init)
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(stmt.init) or want))) == want):
        # A borrow-returning CALL reseat (`result = pick(seed);` ->
        # `result = &(pick(seed));`): the callee's `T&` result is an lvalue,
        # so the same PTR_ADDR address-of applies -- the reseat twin of
        # `decl.record_borrow_call`. Value-returning calls (an `Own[T]`/value
        # result would address a temp) stay on the rebind-slot machinery.
        _witness("reseat.borrow_call")
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=_lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            loc=loc)
    if (_borrow_dunder_source(stmt.init, analyzer)
            and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(stmt.init) or want))) == want):
        # The OPERATOR flavor of that row (`c = a + b;` / `c = -b;` -> `c =
        # &(((a) + (b)));`): a borrow-returning dunder's friend shim hands
        # back an operand's `[const] T&`, so the address-of is of live
        # storage, not a temp. The reseat twin of `decl.dunder_borrow_alias`.
        # Value-returning dunders are `is_rvalue_source`-True and were
        # already claimed by the BRANCH_RVALUE / rebind-slot rungs.
        _witness("reseat.dunder_borrow")
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=_lower_expr(stmt.init, lc, declared), loc=loc)
    if (isinstance(stmt.init, TpySubscript)
            and stmt.init.slice_function_info is None
            and not isinstance(stmt.init.index, TpySlice)):
        if (opt_target is not None
                and _tuple_opt_elem_subscript(stmt.init, declared, analyzer)):
            # An Optional element of a tuple re-points the Optional local as
            # its declaration binds it: the read converts to the pointer
            # (passed or lifted).
            _witness("reseat.subscript_elem")
            return THIRAssign(
                target=THIRName(result_type=target, name=stmt.name, loc=loc),
                value=to_local(_lower_expr(stmt.init, lc, declared,
                                           subscript_prechecked=True),
                               opt_target, BindingForm.POINTER, analyzer),
                loc=loc)
        if (_reseat_const_ok(stmt.name, lc)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(stmt.init) or want))) == want
                and is_plain_nonvalue(want)):
            # A mutable container ELEMENT lvalue (`p = points[0];` -> `p =
            # &(::tpy::__getitem__(points, 0));`): the same address-of
            # catch-all over the element read. RECEIVER use: the record-elem
            # read is admitted for address-of inners there (a value use would
            # copy the borrow). A readonly receiver's element re-points only
            # a const slot.
            _witness("reseat.subscript_elem")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                value=_lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                loc=loc)
    if _deref_coerce_borrow_slot(stmt.init, declared.get(stmt.name),
                                 declared, analyzer):
        # A deref auto-coercion reseat (`copy = ptr2;` -> `copy =
        # &(::tpy::deref_check(ptr2));`): the deref_check result is an
        # lvalue, so the same PTR_ADDR address-of the decl rung applies --
        # the reseat twin of `decl.deref_coerce_addr`.
        _witness("reseat.deref_coerce")
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=_lower_expr(stmt.init, lc, declared), loc=loc)
    if (isinstance(stmt.init, TpyIfExpr)
            and all(isinstance(arm, (TpyCall, TpyMethodCall))
                    and call_result_holdable(analyzer, arm)
                    and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        analyzer.get_expr_type(arm) or want))) == want
                    for arm in (stmt.init.then_expr, stmt.init.else_expr))):
        # A TERNARY of borrow-returning calls (`b = g.itself() if flag else
        # h.itself();` -> `b = &(((flag) ? (..) : (..)));`): both `T&` arms
        # make the ternary an lvalue, so the same PTR_ADDR address-of
        # applies over the whole conditional.
        _witness("reseat.borrow_call_ternary")
        return THIRPtrLocalRebind(
            name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
            value=_lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            loc=loc)
    return None


def _lower_stmt_dispatch(stmt: TpyStmt, scope: _LowerScope, *,
                         deferred_tuple_eager: bool = False) -> THIRStmt:
    lc = scope.lc
    declared = scope.declared
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if lc.forbidden_writes & _written_names(stmt):
        raise ThirUnsupported("stmt.match")
    # Trivia (docstring before the TpyExprStmt arm): `pass` keeps `loc`, so it
    # gets both its leading `#` trivia and its own source line. A docstring
    # gets trivia only -- it emits no code line, and the preceding comments
    # have already flushed before dispatch.
    if is_docstring(stmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt()
    if isinstance(stmt, TpyPassStmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt(loc=loc)
    if is_super_del_call(stmt):
        # `super().__del__()` inside a destructor: C++ invokes each base
        # destructor automatically, so these are filtered out of the
        # __del__ body (records.py's body_stmts). Emit nothing, no source
        # comment -- matching that drop.
        _witness("stmt.super_del")
        return THIRNoOpStmt()
    rebound = (stmt.name if isinstance(stmt, TpyVarDecl)
               else stmt.target.name
               if isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName)
               else None)
    if (rebound is not None and stmt.rebind_storage is not None
            and rebound in lc.prescan.nonlocal_names
            and (lc.binding(rebound).pointer
                 or any(analyzer.ctx.tuple_elem_owned.get(stmt) or ()))):
        # A `nonlocal` rebind of the enclosing function's POINTER-local, or
        # of a tuple whose new value owns an element (the enclosing pointer
        # tuple's element): an OWN slot would be declared in the lambda and
        # die with it while the captured pointer lives on; the enclosing
        # prologue is out of reach. A plain-storage enclosing local keeps
        # its in-place assign (the nonlocal-rebind entry in BUGS.md).
        raise ThirUnsupported("nested_def.rebind_slot_hoist")
    if (rebound is not None
            and (rebound in lc.prescan.nonlocal_names
                 or _written_ptr_tuple(rebound, lc) is None)):
        _reject_tuple_site_own(stmt, rebound, analyzer)
    if (isinstance(stmt, TpyVarDecl) and stmt.init is not None
            and lc.frame_local(stmt.name, FrameLocalClass.REBIND_PTR)
            and is_rvalue_source(analyzer, stmt.init)):
        # A REBIND_PTR frame local's rvalue write, wherever it sits (the
        # leaf entry and every compound body reach this dispatch).
        return _lower_rebind_ptr_frame_write(stmt, lc, declared)
    if lc.resumable_leaf_mode:
        if isinstance(stmt, TpyReturn):
            # A return nested in a non-suspending leaf compound: scaffolding
            # stays skeleton -- the node calls back into _make_async_return /
            # _make_generator_resumable_return via the emit hook, whose value
            # render re-enters the seam's return_values table (registered by
            # lower_resumable from lc.nested_returns).
            begin_stmt()
            if lc.in_finally_helper:
                # Generator helper returns render the fixed __finally_stop
                # pair (the hook's in_generator_finally_helper arm); an async
                # helper return (the Poll-replay render) stays a named rung.
                # Bare-only is exact: the parser rejects return-with-value
                # in generators.
                if (not lc.func.is_generator
                        or _return_carries_value(stmt, lc)):
                    raise ThirUnsupported("res.finally_return")
                node = THIRResumableReturn(ast_stmt=stmt, value=None, loc=loc)
                lc.nested_returns.append(node)
                _witness("res.finally_stop")
                return node
            if isinstance(stmt.value, TpyAwait):
                # Unreachable in a LEAF (a suspension splits the compound);
                # reject rather than assert if a CFG change ever leaks one.
                raise ThirUnsupported("res.leaf_return")
            value = (_lower_resumable_return_value(stmt, lc, declared)
                     if _return_carries_value(stmt, lc) else None)
            deferred = _resumable_deferred_recipe(stmt, lc, declared, value)
            node = THIRResumableReturn(
                ast_stmt=stmt, value=value, loc=loc, deferred=deferred)
            lc.nested_returns.append(node)
            _witness("res.nested_return")
            return node
        if scope.in_branch and isinstance(stmt, TpyVarDecl):
            begin_stmt()
            # A branch-nested decl of a frame field (a local first-assigned
            # inside an if/match/try arm that survives a suspension) renders
            # exactly like the top-level leaf decl -- every frame write is
            # position-blind -- so the three families share the leaf arm's
            # lowerings; the type was registered by lower_resumable's nested
            # pass-1 walk. Coro-handle slots (factory-call-only contract),
            # pointer/alias families (their leaf arm re-enters the sync
            # dispatch, which would recurse here) and true C++-block branch
            # locals keep the named reject.
            if (stmt.init is not None and stmt.name in declared
                    and stmt.name not in lc.narrow.narrowed):
                if lc.plain_frame_field(stmt.name):
                    _witness("res.branch_frame_write")
                    return _lower_frame_field_assign(stmt, lc, declared)
                if lc.frame_local(stmt.name, FrameLocalClass.BORROW_TUPLE):
                    _witness("res.branch_btuple_write")
                    return _lower_borrow_tuple_frame_write(stmt, lc, declared)
                if (lc.declaring(stmt.name).frame_slot
                        and not lc.frame_local(stmt.name,
                                               FrameLocalClass.CORO_HANDLE)):
                    _witness("res.branch_frame_slot_write")
                    return _lower_frame_slot_write(stmt, lc, declared)
                # The slot-free pointer reseats on a frame member render
                # position-blind; rvalue reseats (the sync-only __slot_N)
                # keep the reject below. Shared admission fact:
                # _slot_free_ptr_reseat_ok (the leaf guard's twin).
                if (lc.declaring(stmt.name).pointer
                        and _slot_free_ptr_reseat_ok(stmt.init, lc)):
                    if isinstance(stmt.init, TpyNoneLiteral):
                        _witness("reseat.opt_none")
                        return THIRPtrLocalRebind(
                            name=stmt.name, kind=PtrSlotKind.OPT_NONE,
                            loc=loc)
                    _fvt = declared.get(stmt.name)
                    _witness("reseat.ptr_copy")
                    return THIRAssign(
                        target=THIRName(result_type=_fvt,
                                        name=stmt.name, loc=loc),
                        value=THIRName(result_type=_fvt,
                                       name=stmt.init.name, loc=loc),
                        loc=loc)
                if (lc.declaring(stmt.name).pointer
                        and not lc.declaring(stmt.name).rebind_slot):
                    # An RVALUE reseat of a FRAME pointer field renders
                    # position-blind through the sync reseat family (the
                    # prescanned `__ptr_slot_fN` fill, reseat.opt_frame_slot)
                    # -- the top-level leaf arm's delegation rule; only the
                    # sync-only `__slot_N` rebinds keep the reject.
                    return _lower_stmt(stmt, lc, declared)
            if (stmt.init is not None
                    and stmt.name not in lc.frame_local_types
                    and stmt.name not in lc.prescan.param_names
                    and stmt.name not in lc.narrow.narrowed):
                # A name backed by NO frame slot (the frame's members are the
                # params plus the resolved generator locals) is a true C++
                # block local: its bind renders exactly where a sync body's
                # would -- typed decl at the first bind, bare assign at a
                # reassign -- so re-enter the sync ladder without in_branch.
                # Every frame-resident name keeps the reject above; its write
                # render is a member assign, not a decl.
                _witness("res.branch_block_local")
                return _lower_stmt(stmt, lc, declared)
            raise ThirUnsupported("res.leaf_field_write")
        if isinstance(stmt, TpyTupleUnpack):
            return _lower_frame_tuple_unpack(stmt, scope)
        if isinstance(stmt, TpyTry):
            # An except-only leaf try pushes NO finally frame, so its emit
            # never touches __state / finally_stack / pending-return slots
            # -- the sync tiers render it correctly mid-state; let it fall
            # through to the sync try arm.
            #
            # A finally tier is admitted on the same reasoning ONLY when no
            # control transfer leaves the try: what interlocks the finally
            # frame with the async return scaffolding (_push_finally <->
            # _make_async_return's chain walk) is a return/break/continue
            # crossing it. Without one the finally emits as the plain sync
            # duplicated-body try, correct mid-state.
            if stmt.finally_body:
                # A crossing return rides the leaf finally BRIDGE: the emit
                # records the frame on the skeleton's finally stack,
                # so _make_async_return inlines the finally with the shared
                # guard. break/continue crossings keep rejecting (their
                # loop-frame walk is not bridged).
                if _leaf_finally_crossing_loop(stmt):
                    raise ThirUnsupported("res.leaf_try")
                _witness("res.leaf_try_finally")
            else:
                _witness("res.leaf_try_except")
        # A leaf `with` (suspension-free body) falls through to the sync
        # with arm: `_lower_with` classifies the frame-resident target
        # binds (FRAME_SLOT/FRAME_FIELD) and the `__with_ctx_<K>` owned-
        # manager frame home itself; nested returns reject there (the ctx
        # return hook's finally_stack walk is not lowered).
        if isinstance(stmt, TpyMatch):
            # A leaf match is suspension-free by construction (the CFG
            # builder turns any suspending match into a MatchDispatch
            # terminator), and the dispatch touches no __state / finally
            # scaffolding -- so the sync match tiers render it correctly
            # mid-state, like the except-only leaf try. Arm bodies outside
            # those tiers' slices keep rejecting inside them.
            _witness("res.leaf_match_sync")
        if isinstance(stmt, TpyNestedDef):
            # A frame body's nested def is a struct MEMBER (declared and
            # emitted by the gen_async scaffolding); the statement position
            # keeps only the marker line.
            _witness("res.nested_def_member")
            return THIRFrameNestedDef(
                name=stmt.func.name,
                loc=getattr(stmt, "loc", None))
    if isinstance(stmt, TpyNestedDef):
        return _lower_nested_def(stmt, scope)
    if isinstance(stmt, TpyImport):
        # Module-init only: an import elsewhere is not a statement position.
        if not lc.top_level_scope:
            note_detail("top_level.import_scope")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        begin_stmt()
        calls = lc.import_calls.get(id(stmt), ())
        if not calls:
            # No chain: the empty render still keeps the source comment.
            _witness("top_level.import_init")
            return THIRNoOpStmt(loc=loc)
        return THIRImportInit(calls=calls, loc=loc)
    if isinstance(stmt, TpyVarDecl):
        begin_stmt()
        # Any write clears the flow-sensitive literal fact for the name.
        lc.literal_facts.pop(stmt.name, None)
        if stmt.name in lc.forwarded_map:
            # A forwarded proto-param alias (`xs = it`): a compile-time
            # rename -- the binding emits nothing (leading trivia only)
            # and every later read renders the backing param.
            declared[stmt.name] = declared.get(
                lc.forwarded_map[stmt.name], _var_decl_type(stmt, analyzer))
            _witness("decl.forwarded_alias")
            return THIRNoOpStmt()
        if (stmt.name not in declared and lc.declaring(stmt.name).construct
                is BindingConstruct.CLOSED_FRAME):
            return _closed_frame_first_decl(stmt, scope)
        if stmt.linkage != VarLinkage.DEFAULT:
            if lc.top_level_scope:
                # A `native_global(...)` binding declares nothing of its own
                # -- the C/C++ definition lives outside the module, so
                # the decl emits no line and only leading trivia survives
                # (the Final skip's shape).
                _witness("top_level.native_global_skip")
                return THIRNoOpStmt()
            note_detail("decl.linkage")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.is_final:
            # Final globals live at namespace scope -- nothing is emitted
            # here, so only leading trivia survives.
            _witness("top_level.final_skip")
            return THIRNoOpStmt()
        if stmt.init is None and lc.top_level_scope and stmt.name in declared:
            # A GLOBAL's annotation-only decl: the name already exists at
            # namespace scope, so nothing is emitted for it (the
            # `global_declared_vars` no-init arm) and only leading trivia
            # survives -- the Final skip's shape.
            _witness("top_level.global_no_init")
            return THIRNoOpStmt()
        if stmt.init is None:
            return _lower_no_init_decl(stmt, scope, loc)
        if lc.prescan.has_self and stmt.name == "self":
            note_detail("decl.self_rebind")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.name in lc.narrow.narrowed:
            # Rebinding the NARROWED subject off its member (`while
            # isinstance(t, A): ... t = B("done")`) is the ordinary
            # ptr-variant reseat over the ORIGINAL union slot; the narrowing
            # dies with the write (the extraction alias keeps pointing at
            # the old member -- nothing re-extracts inside the branch).
            # Restore the subject's union BEFORE the reseat arm classifies,
            # and only for the admitted shape: a routed ptr-variant local
            # taking a concrete-member RVALUE. Reads AFTER the kill lower
            # un-narrowed; sema re-narrows them to the assigned member, so
            # the divergent-binding fence is what rejects such a body.
            _nr_u = lc.narrow.subject_union.get(stmt.name)
            _nr_ptr = (_eligible_ptr_union_wide(_nr_u, analyzer)
                       if _nr_u is not None else None)
            if (_nr_ptr is None or stmt.init is None
                    or not lc.declaring(stmt.name).ptr_variant
                    or _ptr_union_slot_kind(stmt.init, _nr_ptr, declared, lc,
                                            analyzer)
                    is not PtrSlotKind.UNION_RVALUE):
                note_detail("decl.narrowed_rebind")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("reseat.narrow_kill")
            lc.narrow.narrowed.pop(stmt.name, None)
            lc.narrow.subject_union.pop(stmt.name, None)
            lc.narrow.any_narrowed.discard(stmt.name)
            declared[stmt.name] = _nr_u
        if _module_slot_global(stmt.name, lc):
            # A non-value global's write is never a plain assign: it either
            # allocates the static slot or rejects (see the helper). A
            # branch write rejects EXCEPT for a @dynamic-protocol target,
            # whose rebind hoists its `static` slot to the function top --
            # the emplace + reseat stay inline in the branch.
            if scope.in_branch:
                _gvt = declared.get(stmt.name)
                _gvu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    _gvt))) if _gvt is not None else None)
                # In-branch flavors that lower: the @dynamic rebind (static
                # slot hoists, emplace inline), an RVALUE write in a
                # non-loop branch (the `static` slot initializes on the
                # first pass through that branch, which is that branch's
                # only pass) or inside a FOR body (only there does the emit
                # leave global_ns and drop `static`), and a pointer-NAME
                # source (the bare `g = p;` copy). A WHILE body is the one
                # loop scope left out: it keeps `static`, so the slot would
                # initialize once and freeze the first iteration's value.
                if not (
                        (isinstance(_gvu, NominalType)
                         and is_dyn_protocol(_gvu))
                        or ((lc.in_for_body or scope.loop_depth == 0)
                            and stmt.init is not None
                            and is_rvalue_source(lc.analyzer, stmt.init))
                        or (isinstance(stmt.init, TpyName)
                            and _bare_pointer_name(stmt.init.name, lc))):
                    note_detail("top_level.global_slot_branch")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_global_slot_write(
                stmt, lc, declared, loc,
                in_branch=scope.in_branch and lc.in_for_body)
        is_reassign = stmt.name in declared
        in_branch_first = scope.in_branch and not is_reassign
        if in_branch_first and not scope.branch_decls_ok:
            note_detail("decl.branch_first_decl")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        vtype = _var_decl_type(stmt, analyzer)
        if vtype is None and resolve_stmt_recorded_type(
                stmt, analyzer,
                include_global_binding=lc.top_level_scope) is None:
            # No recorded type anywhere means the binding cannot be emitted at
            # all -- it is a user-facing rejection, not a shape this lowering
            # has yet to learn, so it reports the error rather than raising
            # a lowering reject. The global-binding leg is
            # the one `_var_decl_type` omits and is what a module-scope
            # binding resolves through.
            emit_prims.reject_undeducible_local_type(stmt.name, stmt.loc)
        # A first declaration builds the representation its record names;
        # the plain-value kinds take the arms below.
        if not is_reassign:
            node = _lower_first_decl(stmt, vtype, scope, loc)
            if node is not None:
                return node
        # A direct @error_return call init takes the statement-level unwrap
        # block.
        er_fi = _error_return_stmt_fi(stmt.init, analyzer)
        if er_fi is not None:
            return _lower_error_return_bind(
                stmt, stmt.name, stmt.init, er_fi, vtype, lc, declared, loc)
        ptt = (lc.declaring(stmt.name).ptr_tuple if stmt.init is not None
               else None)
        if ptt is not None:
            node = _lower_ptr_tuple_binding(stmt, ptt, is_reassign, lc,
                                            declared, loc)
            if node is not None:
                return node
        if is_reassign and lc.declaring(stmt.name).coro_frame:
            # Concrete coro handle rebind (`d = value(4)` -- sema warned it
            # drops the previous coroutine): always emplace into the
            # `std::optional<__coro_*>` slot -- optional's move-ASSIGN is
            # deleted when the frame holds reference members. A NAME source
            # move-constructs + resets; a self-write is a no-op render left
            # unwitnessed here.
            init0 = stmt.init
            if (isinstance(init0, TpyName)
                    and lc.binding(init0.name).coro_frame
                    and init0.name != stmt.name):
                _witness("decl.coro_frame_rebind")
                return THIRCoroHandleMove(
                    target=stmt.name, source=init0.name, loc=loc)
            if not isinstance(stmt.init, (TpyCall, TpyMethodCall)):
                note_detail("decl.coro_frame_rebind_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            value = _lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             pos=SinkPos.LOCAL_DECL,
                             forms=_decl_slot_forms(
                                 vtype, analyzer, whole_tuple_call=False,
                                 from_call=True, ptr_local=False),
                             allow_temps=True))
            _witness("decl.coro_frame_rebind")
            return THIRFrameSlotWrite(
                name=stmt.name, value=value, cpp_type=None, loc=loc)
        if not is_reassign:
            fn_top = not scope.in_branch
            # `b = copy(a)` of a plain F1-record source: an owned record local
            # from the copy-construct rvalue (`T b = T(a);`, the bare-record
            # copy arm). The source stays live (copy, not move).
            copy_row = (_lower_copy_record(stmt.init, lc, declared,
                                           slot_type=vtype, loc=loc)
                        if fn_top or (
                            stmt.name not in lc.prescan.hoisted
                            and stmt.name not in lc.prescan.reassigned
                            and stmt.name not in lc.prescan.move_through
                            and not lc.func.is_generator and not lc.func.is_async)
                        else None)
            if copy_row is not None:
                _witness("decl.copy_record")
                return _owned_local_decl(stmt, vtype, lc, declared,
                                          value=copy_row)
            # Owned record local: `Box b = Box(n);` -- the plain value decl,
            # cpp_type spelled the way codegen does (render_type qualifies
            # cross-module / native records). The name enters `declared` only
            # (not `pointers`): reads render `.`, passes render bare, and
            # sema's movable set drives its last-use moves.
            if _owned_record_decl_ok(stmt, vtype, lc.prescan, declared,
                                     lc.analyzer, narrowed=lc.narrow.narrowed):
                _witness("decl.owned_record_method"
                         if isinstance(stmt.init, TpyMethodCall)
                         else "decl.owned_record")
                return _owned_local_decl(stmt, vtype, lc, declared)
            # Move-through owned record local: `a = h` where `h` is a non-value
            # local consumed at its last use (`Handle a = std::move(h);`). Sema
            # marks the target in `move_through` only for a non-reassigned NAME
            # source at last use of a non-value local; the init's std::move
            # wrap keys on the target (not a fresh last-use test), so the
            # membership drives the move directly.
            _mt_u = unwrap_readonly(unwrap_send_sync(vtype))
            # Frame names already in declared cannot enter this fresh-local arm.
            if _move_through_source(stmt, vtype, lc):
                _witness("decl.move_through_array" if is_array(_mt_u)
                         else "decl.move_through_container"
                         if _f1_container_ref(_mt_u)
                         else "decl.move_through_record")
                return _owned_local_decl(
                    stmt, vtype, lc, declared,
                    value=_lower_storage_value(
                        stmt.init, vtype, lc, declared, loc,
                        use=_LOCAL_MOVE_USE, move=True))
        # Borrow-form tuple local reseat (`std::tuple<..., T*>` -- a branch
        # hoist or btuple.decl literal): a REF/VALUE-capture literal assigns
        # the borrow literal directly, a storage lvalue subscript is lent
        # element by element. Gated to the same non-const source slice as the
        # hoist admission. PARAM reassignment is excluded: it would assign
        # into the `const std::tuple<..., const T*>&` parameter (ill-formed
        # C++, the BUGS.md reassigned-borrow-tuple-param entry) -- keep
        # rejecting until that is fixed. Param READS keep the borrow
        # default.
        if (is_reassign and lc.declaring(stmt.name).optional_borrow_tuple):
            # Reseat of a nullable borrow-form tuple local: None ->
            # `t = std::nullopt;`, a storage lvalue -> the optional-wrapped
            # lift, an OWNING tuple call -> the pre-declared slot's emplace
            # under the optional wrap (slot allocated by the decl).
            obt_t = _optional_borrow_tuple(declared.get(stmt.name), analyzer)
            if obt_t is None:
                note_detail("reseat.opt_btuple_binding")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            ob_cpp = obt_t.to_cpp_return()
            oopt_cpp = f"std::optional<{ob_cpp}>"
            if isinstance(stmt.init, TpyNoneLiteral):
                _witness("reseat.opt_btuple_none")
                return THIRAssign(
                    target=THIRName(result_type=declared[stmt.name],
                                    name=stmt.name, loc=loc),
                    value=THIRLiteral(result_type=declared[stmt.name],
                                      value=None, form=Form.STORAGE,
                                      loc=loc),
                    loc=loc)
            if (isinstance(stmt.init, (TpyFieldAccess, TpySubscript))
                    and _borrow_tuple_source_ok(stmt.init, lc)):
                src = _lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                    subscript_prechecked=True)
                _witness("reseat.opt_btuple_lift")
                return THIRAssign(
                    target=THIRName(result_type=declared[stmt.name],
                                    name=stmt.name, loc=loc),
                    value=_opt_btuple_wrap(
                        to_local(src, obt_t, BindingForm.BORROW_TUPLE,
                                 analyzer),
                        declared[stmt.name], oopt_cpp, loc),
                    loc=loc)
            if (lc.declaring(stmt.name).rebind_slot
                    and _btuple_owning_call_init(stmt.init, analyzer)):
                v = _lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 allow_temps=True,
                                 pos=SinkPos.LOCAL_DECL,
                                 forms=_decl_slot_forms(
                                     obt_t, analyzer, whole_tuple_call=True,
                                     from_call=True, ptr_local=False)))
                _witness("reseat.opt_btuple_slot")
                return THIRAssign(
                    target=THIRName(result_type=declared[stmt.name],
                                    name=stmt.name, loc=loc),
                    value=v, btuple_lent=slot_payload_lent(obt_t, lc),
                    btuple_opt_cpp=oopt_cpp,
                    slot_cpp=lc.render_type(obt_t), loc=loc)
            note_detail("reseat.opt_btuple_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        bt_t = (_borrow_tuple_local_type(
                    stmt.name, declared, lc.names_with("storage_tuple"),
                    borrow_form=(lc.binding(stmt.name).form
                                 is BindingForm.BORROW_TUPLE))
                if is_reassign and stmt.name not in lc.prescan.param_names
                else None)
        if bt_t is not None:
            parked = _park_tuple_global(stmt, scope, lc, declared, loc)
            if parked is not None:
                return parked
            _mixed_call = (isinstance(stmt.init, (TpyCall, TpyMethodCall))
                           and _renders_own_borrow_tuple(
                               stmt.init, lc.names_with("borrow_tuple_mixed"),
                               analyzer))
            # A plain borrow-tuple return (`-> tuple[Box, Box]`) is the
            # pointer tuple itself, assigned bare -- into a name that IS the
            # pointer tuple (a literal-bound local holding an element inline
            # is `std::tuple<Box, Box*>`), from a call whose result outlives
            # the statement (a temporary argument fails closed, as the alias
            # decl's call source does: the result could point at it).
            _borrow_call = (
                not _mixed_call
                and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and _borrow_tuple_call_matches(stmt.init, bt_t, analyzer)
                and not lc.declaring(stmt.name).borrow_tuple_mixed
                and not any(lc.declaring(stmt.name).inline_elems or ())
                and expr_lends_storage(analyzer, stmt.init))
            if _mixed_call or _borrow_call:
                # A CALL whose result already IS the name's form hands it
                # back BY VALUE, so the reseat is a PLAIN assign: no lift, no
                # rebind slot. The MIXED own+borrow return is the hybrid
                # `std::tuple<Box, Box*>` of a name no rebind makes the
                # pointer tuple; the plain borrow-tuple return renders
                # `P = pick((*V), (*W));`, the scalar's `V2 = &(pick(..))`.
                # A call handing an object back into the pointer tuple is
                # its own arm (`_lower_ptr_tuple_binding`).
                _witness("btuple.reseat_mixed_call" if _mixed_call
                         else "btuple.reseat_borrow_call")
                return THIRAssign(
                    target=THIRName(result_type=bt_t, name=stmt.name,
                                    loc=loc),
                    value=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.VALUE,
                                     pos=SinkPos.LOCAL_DECL,
                                     forms=_decl_slot_forms(
                                         bt_t, analyzer,
                                         whole_tuple_call=False,
                                         from_call=True, ptr_local=False),
                                     allow_temps=_mixed_call)),
                    loc=loc)
            if not _borrow_tuple_source_ok(stmt.init, lc):
                if _written_ptr_tuple(stmt.name, lc) is not None:
                    _reject_ptr_tuple_select(stmt, stmt.name)
                note_detail("reseat.borrow_tuple_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if isinstance(stmt.init, TpyTupleLiteral):
                _reject_value_elem_at_borrow_tuple(stmt, stmt.name, stmt.init,
                                                   bt_t)
                value: THIRExpr = _lower_borrow_tuple_literal(
                    stmt.init, bt_t, lc, declared)
                _witness("btuple.reseat_literal")
            elif isinstance(stmt.init, TpyName):
                # A sibling borrow-tuple local: the bare pointer-tuple copy
                # (`u = t;`) -- same form, no lift.
                if _holds_elements_inline(stmt.init.name, lc):
                    _reject_inline_repoint(stmt, stmt.name, stmt.init.name)
                value = _lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                _witness("btuple.reseat_name_copy")
            else:
                # BORROW_BIND: the per-element lend consumes the bare
                # storage read (subscript element or F3 tuple field). The
                # lift targets the DECL's const-ness (the fixpoint verdict),
                # exactly like `_borrow_tuple_rhs`.
                src = _lower_expr(stmt.init, lc, declared,
                                  use=_ExprUse(
                                      result=_ExprResultUse.BORROW_BIND),
                                  subscript_prechecked=True)
                value = to_local(
                    src, bt_t, BindingForm.BORROW_TUPLE, analyzer,
                    const=lc.declaring(stmt.name).const)
                _witness("btuple.reseat_lift")
            return THIRAssign(
                target=THIRName(result_type=bt_t, name=stmt.name, loc=loc),
                value=value, loc=loc)
        # OPTIONAL_STORAGE branch-hoist assign: the single-bind non-value's
        # in-branch decl writes PLAIN into the if-head `std::optional<T>`
        # (`name = <storage rvalue>;` -- the OPTIONAL_STORAGE decl arm).
        # Checked before the rebind-slot/pointer
        # reseats: the name is in `pointers` for its read side only.
        if lc.declaring(stmt.name).optional_storage and stmt.name in declared:
            target_t = declared[stmt.name]
            if type(stmt.init) in _comprehensions._COMP_KINDS:
                # A comprehension reseat renders its whole stmt-expr
                # straight into the engaged optional -- no slot, no lift.
                # Dispatched HERE because the generic `_lower_expr` has no
                # comprehension arm at all; the admission alone would only
                # move the reject.
                _witness("reseat.opt_storage_comp")
                return THIRAssign(
                    target=THIRName(result_type=target_t, name=stmt.name,
                                    loc=loc),
                    value=_comprehensions._lower_comprehension(
                        stmt.init, target_t, lc, declared,
                        scope.admission_pointers()),
                    loc=loc)
            move = _move_through_source(stmt, target_t, lc)
            # The optional stands at the block head, where the predecl
            # checked what the frame borrows (`_optional_storage_hoist_entry`).
            if not (move or _rebind_rvalue_source_ok(
                    stmt.init, target_t, analyzer, frame_roots_ok=True)):
                note_detail("reseat.opt_storage_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("reseat.opt_storage")
            value = _lower_storage_value(
                stmt.init, target_t, lc, declared, loc,
                use=(_LOCAL_MOVE_USE if move else
                     _rebind_rvalue_use(stmt.init, target_t)), move=move)
            return THIRAssign(
                target=THIRName(result_type=target_t, name=stmt.name,
                                loc=loc),
                value=value,
                optional_record_assignment=(borrowed_record(target_t, False, analyzer)
                                            if isinstance(value, THIRCtorCall) or (
                                                isinstance(value, (THIRCopy, THIRMove))
                                                and unwrap_readonly(unwrap_ref_type(value.result_type)) == target_t)
                                            else None),
                rebuild=frame_object_slot(target_t),
                loc=loc)
        # A None reseat of a wide-opt POINTER binding nulls the pointer
        # (`z = nullptr;`) -- slot or no slot; checked before the
        # BRANCH_RVALUE arm, which would misclassify the literal as a
        # slot-needing rvalue.
        if (stmt.name in declared and lc.declaring(stmt.name).pointer
                and isinstance(stmt.init, TpyNoneLiteral)
                and _optional_ptr_borrow_wide(declared[stmt.name],
                                              analyzer) is not None):
            _witness("reseat.opt_none")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.OPT_NONE, loc=loc)
        # BRANCH_RVALUE reseat: a branch-hoisted `T*` local without an
        # if-head slot takes an rvalue via the lazily-allocated function-top
        # slot (`name = &*(__slot_N = <rvalue>);`); lvalue sources fall
        # through to the pointer reseat arms below. A BORROW-returning
        # ptr-opt call is `is_rvalue_source`-True but needs NO slot -- it
        # copies the pointer bare, so it skips to its own row below.
        if (lc.binding(stmt.name).lazy_rebind_slot
                and stmt.name in declared
                and is_rvalue_source(analyzer, stmt.init)
                and not (isinstance(stmt.init, (TpyCall, TpyMethodCall))
                         and _optional_ptr_borrow_wide(declared[stmt.name],
                                                       analyzer) is not None
                         and _ptr_opt_borrow_call_ret(
                             stmt.init, analyzer.get_expr_type(stmt.init)))):
            target_t = declared[stmt.name]
            slot_t = _rebind_slot_target(target_t, analyzer)
            if not _rebind_rvalue_source_ok(
                    stmt.init, slot_t, analyzer,
                    frame_roots_ok=_frame_slot_roots_ok(stmt.name, lc)):
                note_detail("reseat.branch_rvalue_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("reseat.branch_rvalue")
            storage = _rebind_storage(stmt, stmt.name, lc)
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.BRANCH_RVALUE,
                value=_slot_fill_value(_lower_storage_value(
                    stmt.init, slot_t, lc, declared, loc,
                    use=_rebind_rvalue_use(stmt.init, slot_t)),
                    slot_t, storage, lc),
                val_cpp=lc.render_type(slot_t),
                rebind_storage=storage,
                rebuild=frame_object_slot(slot_t), loc=loc)
        # An OPT_STORAGE_CALL-declared name reseats by re-filling ITS slot
        # and re-lifting (`__slot_1 = make(43); z =
        # ::tpy::optional_to_ptr(__slot_1);` -- the rebind-slot reuse).
        # Any other reseat shape rejects: the generic THIRAssign
        # rebind emit would hijack it into the `&*(__slot = ...)` render.
        if lc.declaring(stmt.name).opt_storage_call and stmt.name in declared:
            fi_r = (stmt.init.resolved_function_info
                    if isinstance(stmt.init, (TpyCall, TpyMethodCall))
                    else None)
            if (fi_r is not None
                    and _call_storage_optional_return(stmt.init, analyzer)
                    is not None):
                _witness("reseat.opt_storage_call")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.OPT_STORAGE_CALL,
                    value=_flush_witness(
                        "flush.assign",
                        _lower_expr(stmt.init, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True),
                                    allow_whole_optional=True)),
                    val_cpp=_rebind_slot_cpp(declared[stmt.name], lc),
                    loc=loc)
            note_detail("reseat.opt_storage_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # A BORROW-returning ptr-opt call reseat of a wide-opt POINTER
        # binding: the `T*` result IS the binding's shape, so the pointer
        # copies bare (`z = first<int32_t>(xs);`) -- no slot write, slot
        # or no slot (the branch-hoisted flavor allocates none).
        if (stmt.name in declared and lc.declaring(stmt.name).pointer
                and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and _optional_ptr_borrow_wide(declared[stmt.name],
                                              analyzer) is not None
                and _ptr_opt_borrow_call_ret(
                    stmt.init, analyzer.get_expr_type(stmt.init))
                # ... and the result must ALREADY be that `T*`. A
                # `@property` getter hands back the field's
                # `std::optional<T>` BY REFERENCE, so the bare copy assigns
                # an optional to a pointer and does not compile. The DECL
                # pass-through asks the same question one arm up; this is
                # its RESEAT sibling, which the storage-ref family has no
                # lift for either (TODO.md, "Four positions REJECT a
                # storage-ref `@property` read").
                and not reads_storage_form_optional(analyzer, stmt.init)):
            # Rebind-slot locals too: emit's slot render needs rebind_storage.
            _witness("reseat.opt_call_pass")
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=_lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.LOCAL_DECL,
                                 forms=_decl_slot_forms(
                                     vtype, analyzer, whole_tuple_call=False,
                                     from_call=True, ptr_local=True),
                                 # An argument temporary dies with the
                                 # statement's block while the pointer the
                                 # result re-points may outlive it: the
                                 # lvalue pointer reseat's refusal.
                                 allow_temps=False),
                    allow_whole_optional=True),
                loc=loc)
        # F2d rebind-slot reseat: a rebind sema stamps as putting an object
        # of its own into a rebound reference local (`rebind_storage`), or a
        # branch-first write into a hoisted pointer, which is no rebind and
        # asks the stamp's question itself. It lowers as a plain value-form
        # call; emit wraps it as `p = &*(__slot_N = <value>)`. Checked
        # before the lvalue POINTER reseat -- a rebind-slot local is in both
        # `pointers` sets.
        if (lc.declaring(stmt.name).rebind_slot
                and stmt.name in declared
                and (stmt.rebind_storage is not None
                     or (_match._mutable_pointer_hoist(
                             lc.declaring(stmt.name))
                         and binds_owned_value(analyzer, stmt.init)))
                and _eligible_ptr_union_wide(declared[stmt.name],
                                             lc.analyzer) is None):
            decl_u = pointer_repr_optional(declared[stmt.name])
            if decl_u is not None:
                # OPT_PTR_SLOT reseats: None -> `p = nullptr;`; an exact-type
                # F1 rvalue rides THIRAssign's rebind-slot arm
                # (`p = &*(__slot_N = <rvalue>);`). Lvalue reseats (pointer
                # copies / optional_to_ptr lifts / address-of) are later rungs.
                if isinstance(stmt.init, TpyNoneLiteral):
                    _witness("reseat.opt_none")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.OPT_NONE, loc=loc)
                if _opt_storage_field_rvalue(stmt.init, decl_u, analyzer):
                    # A storage-form Optional FIELD off an rvalue receiver:
                    # the decl-site rebind slot takes the whole optional
                    # INLINE and the pointer lifts off the assignment
                    # (`v = ::tpy::optional_to_ptr(__slot_1 =
                    # make_holder(p).value);`) -- one line, unlike the
                    # OPT_STORAGE_CALL fill-then-lift pair.
                    _witness("reseat.opt_storage_field")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.OPT_FIELD_RVALUE,
                        value=_lower_expr(
                            stmt.init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True),
                            allow_whole_optional=True,
                            field_prechecked=True),
                        val_cpp=lc.render_type(unwrap_readonly(decl_u.inner)),
                        loc=loc)
                if isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                          TpySetLiteral)):
                    # A container LITERAL at the rebind: an owned rvalue like
                    # the ctor result below, filling the same decl-site rebind
                    # slot (`xs = &*(__slot_N = {1, 2, 3});`) -- the same fill
                    # the first-binding decl arm
                    # (`decl.opt_slot_container_literal`) spells.
                    lit, lit_pointee = _lower_opt_reseat_literal(
                        stmt, lc, declared)
                    _witness("reseat.opt_container_literal")
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=lit,
                        rebind_storage=_rebind_storage(stmt, stmt.name, lc),
                        slot_cpp=lc.render_type(lit_pointee),
                        loc=loc)
                if not (_opt_slot_rvalue_shape(stmt.init, decl_u.inner,
                                               analyzer)
                        # `copy(x)` of the slot's own record: the same
                        # copy-construct rvalue (`&*(__slot_N = Point(p))`).
                        or copy_construct_source(
                            stmt.init, analyzer, frozenset())
                        == unwrap_readonly(decl_u.inner)):
                    note_detail("decl.opt_reseat_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("reseat.opt_rvalue")
                reseat_value = _lower_copy_record(stmt.init, lc, declared,
                                                  loc=loc)
                if reseat_value is None:
                    # The slot IS the storage sink (`&*(__slot_N = <rvalue>)`),
                    # so the source lowers at STORAGE like its
                    # `reseat.opt_storage_field` sibling -- the default VALUE
                    # use rejects a record-returning marker call here.
                    reseat_value = _lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE,
                                     allow_temps=True,
                                     slot_target=decl_u.inner))
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=reseat_value,
                    rebind_storage=_rebind_storage(stmt, stmt.name, lc),
                    slot_cpp=lc.render_type(unwrap_readonly(decl_u.inner)),
                    loc=loc)
            if not _rebind_rvalue_source_ok(
                    stmt.init, declared[stmt.name], analyzer,
                    frame_roots_ok=_frame_slot_roots_ok(stmt.name, lc)):
                note_detail("decl.rebind_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            storage = _rebind_storage(stmt, stmt.name, lc)
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name,
                                loc=loc),
                value=_slot_fill_value(_lower_storage_value(
                    stmt.init, declared[stmt.name], lc, declared, loc,
                    use=_rebind_rvalue_use(stmt.init, declared[stmt.name])),
                    _rebind_slot_target(declared[stmt.name], analyzer),
                    storage, lc),
                rebind_storage=storage,
                slot_cpp=_rebind_slot_cpp(declared[stmt.name], lc),
                rebuild=frame_object_slot(declared[stmt.name]),
                loc=loc)
        # An LVALUE reseat of a slot-holding name leaves the slot untouched
        # and re-points the alias (`p = &(lvalue);`): the pointer reseat
        # arms below.
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so the render is unaffected).
        if lc.declaring(stmt.name).pointer and stmt.name in declared:
            if (lc.declaring(stmt.name).form is BindingForm.DYN
                    and not lc.declaring(stmt.name).coro_frame):
                # @dynamic protocol local reseat (`pet = Cat()`): a FRESH hoisted
                # `std::optional<slot>` + `.emplace` + `pet = &*slot` (the
                # dynamic-protocol rebind). Slot type is the concrete (a
                # direct conformer) or the `Adapter<Base, Concrete>` (structural),
                # matching the first-decl choice. An already-erased protocol-typed
                # source (a pointer copy `pet = &q`) is a later rung.
                # The hoisted slot needs a drain point. `emit_thir_body` /
                # `emit_thir_constructor_tail` drain `hoist_lines`; the resumable
                # LEAF emitter (ResumableLeafEmitter) does NOT, so a reseat
                # there would drop the `std::optional<slot>` decl
                # (undeclared `__slot_N`) -- a generator/async body rejects.
                if lc.func.is_generator or lc.func.is_async:
                    note_detail("reseat.dyn_protocol")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                proto_vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[stmt.name])))
                concrete_type = analyzer.get_expr_type(stmt.init)
                if concrete_type is None:
                    note_detail("reseat.dyn_protocol_erased")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if is_protocol_type(concrete_type):
                    # Already-erased reseat (`p2 = p1`): re-alias, no slot.
                    erased = _lower_dyn_erased_source(stmt.init, lc, declared)
                    if erased is None:
                        note_detail("reseat.dyn_protocol_erased")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.dyn_protocol_erased")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.DYN_PROTOCOL_ERASED,
                        value=erased, loc=loc)
                concrete_cpp = lc.render_type(concrete_type)
                if record_inherits_dynamic(concrete_type, proto_vt, analyzer.registry):
                    slot_cpp = concrete_cpp
                else:
                    slot_cpp = dynamic_adapter_type(proto_vt, concrete_cpp,
                                                    analyzer)
                _witness("reseat.dyn_protocol")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.DYN_PROTOCOL,
                    value=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(slot_target=concrete_type)),
                    val_cpp=slot_cpp, loc=loc)
            reseat_u = pointer_repr_optional(declared[stmt.name])
            if reseat_u is not None:
                # A slotless OPT_PTR_SLOT local (never rvalue-reassigned --
                # the slot-holders reseat via the rebind-slot arm above)
                # reseats to None (`p = nullptr;`) or lifts a new pointee via
                # `&(...)`: a bare record PARAM (`x = p;` -> `x = &(p);`) or an
                # F1-record field source (`x = &(recv.field);`) -- the
                # nullptr / address-of rebind arms.
                # Pointer / rvalue / optional_to_ptr sources are later rungs.
                if isinstance(stmt.init, TpyNoneLiteral):
                    _witness("reseat.opt_none")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.OPT_NONE, loc=loc)
                pointee = unwrap_readonly(reseat_u.inner)
                if (lc.resumable_leaf_mode
                        and isinstance(stmt.init, (TpyArrayLiteral,
                                                   TpyDictLiteral,
                                                   TpySetLiteral))):
                    # The frame flavor of `reseat.opt_container_literal`: the
                    # literal materializes in the PRESCANNED frame field (an
                    # inline slot dies at the next suspension) and the pointer
                    # field re-points.
                    lit, lit_pointee = _lower_opt_reseat_literal(
                        stmt, lc, declared)
                    if (_rebind_storage(stmt, stmt.name, lc)
                            is RebindStorage.IN_PLACE):
                        _witness("reseat.opt_frame_literal_in_place")
                        return THIRAssign(
                            target=THIRName(result_type=declared[stmt.name],
                                            name=stmt.name, loc=loc),
                            value=lit,
                            rebind_storage=RebindStorage.IN_PLACE,
                            slot_cpp=lc.render_type(lit_pointee), loc=loc)
                    fld = rcfg.ptr_slot_field(lc.func, stmt)
                    if fld is None:
                        note_detail("decl.opt_reseat_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.opt_frame_container_literal")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.FRAME_RVALUE,
                        value=lit, val_cpp=fld,
                        rebind_storage=RebindStorage.OWN, loc=loc)
                if (lc.resumable_leaf_mode
                        and _opt_storage_field_rvalue(stmt.init, reseat_u,
                                                      analyzer)):
                    # The frame flavor of `reseat.opt_storage_field`: the whole
                    # optional lands in the prescanned frame field and the
                    # pointer lifts off it. The sync arm's decl-site slot is an
                    # inline one, which a suspension would outlive.
                    fld = rcfg.ptr_slot_field(lc.func, stmt)
                    if fld is None:
                        note_detail("decl.opt_reseat_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.opt_frame_storage_field")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.FRAME_STORAGE_CALL,
                        value=_lower_expr(
                            stmt.init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True),
                            allow_whole_optional=True,
                            field_prechecked=True),
                        val_cpp=fld, loc=loc)
                if (lc.resumable_leaf_mode
                        and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                        and _call_storage_optional_return(
                            stmt.init, analyzer) is not None):
                    # The Own-declared optional call fills the prescanned
                    # frame field and the pointer re-lifts
                    # (`__ptr_slot_f0 = make_opt(3); got =
                    # ::tpy::optional_to_ptr(__ptr_slot_f0);` -- the
                    # OPT_STORAGE_CALL decl's resumable twin).
                    fld = rcfg.ptr_slot_field(lc.func, stmt)
                    if fld is None:
                        note_detail("decl.opt_reseat_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.opt_frame_storage_call")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.FRAME_STORAGE_CALL,
                        value=_lower_expr(
                            stmt.init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE),
                            allow_whole_optional=True),
                        val_cpp=fld, loc=loc)
                frame_sub_rv = False
                if (lc.resumable_leaf_mode
                        and not _opt_slot_rvalue_shape(stmt.init, pointee,
                                                       analyzer)):
                    # A SUBCLASS ctor rvalue is admitted in the FRAME flavor
                    # only: the write's own base-typed field takes the
                    # warned slicing upcast (`__ptr_slot_f0 = Dog();` --
                    # sema's "upcast narrows"). The POLYMORPHIC flavor
                    # never reaches lowering: sema raises the
                    # polymorphic-rvalue-into-Optional error first.
                    _at_fr = analyzer.get_expr_type(stmt.init)
                    frame_sub_rv = (
                        isinstance(stmt.init, TpyCall)
                        and stmt.init.resolved_function_info is not None
                        and stmt.init.resolved_function_info.is_constructor
                        and isinstance(_at_fr, NominalType)
                        and analyzer.registry.is_subclass_of(_at_fr, pointee)
                        and _ctor_shape_ok(stmt.init, analyzer))
                if (lc.resumable_leaf_mode
                        and (frame_sub_rv
                             or _opt_slot_rvalue_shape(stmt.init, pointee,
                                                       analyzer))):
                    # Resumable frame body: the rvalue materializes in its
                    # prescanned FRAME-FIELD slot and the pointer field
                    # re-points (`saved = &*(__ptr_slot_fN = Point(9));` --
                    # the frame ptr-slot-field flavor; an inline slot would
                    # die at the next suspension).
                    value = _lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                     slot_target=pointee))
                    if (_rebind_storage(stmt, stmt.name, lc)
                            is RebindStorage.IN_PLACE):
                        # Nothing aliases the pointee: assign through the
                        # pointer, the superseded object dies here.
                        _witness("reseat.opt_frame_in_place")
                        return THIRAssign(
                            target=THIRName(result_type=declared[stmt.name],
                                            name=stmt.name, loc=loc),
                            value=value,
                            rebind_storage=RebindStorage.IN_PLACE,
                            slot_cpp=lc.render_type(pointee), loc=loc)
                    fld = rcfg.ptr_slot_field(lc.func, stmt)
                    if fld is None:
                        note_detail("decl.opt_reseat_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.opt_frame_slot")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.FRAME_RVALUE,
                        value=value, val_cpp=fld,
                        rebind_storage=RebindStorage.OWN, loc=loc)
                if (isinstance(stmt.init, TpyName)
                        and lc.binding(stmt.init.name).storage_opt
                        and _optional_ptr_borrow_wide(
                            declared.get(stmt.init.name), analyzer)
                        == reseat_u):
                    # A STORAGE-opt local source (a loop var over
                    # `list[P | None]`) is `std::optional<P>`, not a `T*`:
                    # it lifts (`first = ::tpy::optional_to_ptr(it);` --
                    # the rebind's storage-opt-lvalue branch), never the
                    # bare pointer copy below.
                    _witness("reseat.storage_opt_name_lift")
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=THIRFormConvert(
                            result_type=reseat_u,
                            value=_lower_expr(stmt.init, lc, declared,
                                              allow_whole_optional=True),
                            form=Form.BORROW, loc=loc),
                        loc=loc)
                if (isinstance(stmt.init, TpyName)
                        and stmt.init.name not in lc.narrow.narrowed
                        # A STORAGE-form binding of the same declared type
                        # takes the lift arm above, never this bare copy.
                        and not lc.binding(stmt.init.name).storage_opt
                        and (lc.binding(stmt.init.name).pointer
                             or _optional_ptr_borrow_name(
                                 stmt.init, declared, analyzer) is not None)
                        and _optional_ptr_borrow(declared.get(stmt.init.name),
                                                 analyzer) == reseat_u):
                    # Same-Optional pointer-source copy (`q = a;` -- a `T*`
                    # param/local source copies the pointer bare -- the
                    # pointer-local source branch). Const safety rides the
                    # structural equality: a readonly-inner source Optional
                    # is a DIFFERENT OptionalType than the mutable target's,
                    # so a const-dropping copy can never match this arm.
                    _witness("reseat.opt_ptr_copy")
                    source_type = declared[stmt.init.name]
                    source_layout = optional_layout(source_type, analyzer, borrow=True,
                        readonly=lc.binding(stmt.init.name).const)
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=THIRName(result_type=source_type, name=stmt.init.name,
                                       form=Form.BORROW if source_layout is not None else Form.VALUE,
                                       optional_read=THIROptionalRead(source_layout, False)
                                       if source_layout is not None else None, loc=loc),
                        loc=loc)
                if (isinstance(stmt.init, TpyName)
                        and stmt.init.name not in lc.narrow.narrowed
                        # A POINTEE-typed pointer LOCAL copies bare too
                        # (`saved = p;` -- the escape-hoisted `Point* p`
                        # is already the target's raw `T*`). The
                        # SAME-Optional ptr-repr GLOBAL flavor (`q = g;`)
                        # is the pointer-slot-global consumer class --
                        # blocked on TODO.md's one-place consumer guard,
                        # not admitted piecemeal here.
                        and lc.binding(stmt.init.name).pointer
                        and unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(
                                declared.get(stmt.init.name))))
                        == unwrap_readonly(reseat_u.inner)):
                    _witness("reseat.opt_ptr_copy")
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=_lower_expr(
                            stmt.init, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                        loc=loc)
                if (isinstance(stmt.init, TpyFieldAccess)
                        and reads_storage_form_optional(analyzer, stmt.init)
                        and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                            analyzer.get_expr_type(stmt.init)))) == reseat_u
                        and _field_receiver_ok(stmt.init, declared, analyzer)):
                    # Storage-form optional FIELD reseat: the same
                    # optional_to_ptr lift as the first decl, re-pointing the
                    # slotless local (`p = ::tpy::optional_to_ptr(h.value);`
                    # -- the rebind's storage-opt-lvalue branch, which
                    # spells no const on the assign line).
                    field = _lower_field_source(stmt.init, lc, declared)
                    _witness("reseat.opt_field_lift")
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=THIRFormConvert(
                            result_type=reseat_u, value=field,
                            form=Form.BORROW, loc=loc),
                        loc=loc)
                if (isinstance(stmt.init, TpyName)
                        and lc.binding(stmt.init.name).optional_storage
                        and _own_storage_opt_param(
                            declared.get(stmt.init.name), analyzer)
                        == reseat_u):
                    # An `Own[P | None]` param source: the same storage-form
                    # optional_to_ptr lift as the field reseat, re-pointing
                    # the slotless local (`y = ::tpy::optional_to_ptr(x);`).
                    _witness("reseat.opt_own_param_lift")
                    return THIRAssign(
                        target=THIRName(result_type=vtype, name=stmt.name,
                                        loc=loc),
                        value=THIRFormConvert(
                            result_type=reseat_u,
                            value=_lower_expr(stmt.init, lc, declared,
                                              allow_whole_optional=True),
                            form=Form.BORROW, loc=loc),
                        loc=loc)
                arm = _reseat_lvalue_source(stmt, pointee, lc, declared,
                                            opt_target=reseat_u)
                if arm is None:
                    note_detail("decl.opt_reseat_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if not isinstance(arm, _ConvertSource):
                    return arm
                _witness("reseat.opt_lvalue")
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                    value=THIRFormConvert(
                        result_type=pointee, value=arm.src, form=Form.BORROW,
                        is_const=lc.declaring(stmt.name).const, loc=loc),
                    loc=loc)
            # An OPTIONAL_STORAGE source sits in `pointers` for its deref
            # reads, but it is `std::optional<T>`, not a `T*`: the pointer
            # local lifts off it (`alias = ::tpy::optional_to_ptr(p);`),
            # the F1 storage-optional read, never the bare pointer copy.
            if (isinstance(stmt.init, TpyName)
                    and lc.binding(stmt.init.name).optional_storage
                    and stmt.init.name not in lc.narrow.narrowed):
                src_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[stmt.init.name])))
                if frame_object_slot(src_t):
                    # A generator's slot here is the enclosing BLOCK's
                    # `std::optional`, which an outer alias would outlive
                    # (BUGS.md#block-optional-slot-alias-dangles).
                    note_detail("reseat.frame_opt_storage_alias")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("reseat.opt_storage_lift")
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=THIRFormConvert(
                        result_type=OptionalType(src_t),
                        value=_lower_expr(stmt.init, lc, declared,
                                          allow_whole_optional=True),
                        form=Form.BORROW, loc=loc),
                    loc=loc)
            # A pointer-NAME copy reseat (`saved = p;` -- both `T*` locals):
            # the bare pointer copies, no address-of (a pointer-local source
            # renders the name verbatim).
            if (isinstance(stmt.init, TpyName)
                    and lc.binding(stmt.init.name).pointer
                    and stmt.init.name not in lc.narrow.narrowed):
                _witness("reseat.ptr_copy")
                source = THIRName(result_type=vtype, name=stmt.init.name, loc=loc)
                return THIRAssign(
                    alias_binding=alias_binding(source, vtype,
                                                lc.declaring(stmt.name).const, analyzer),
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=source,
                    loc=loc)
            arm = _reseat_lvalue_source(
                stmt, unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[stmt.name]))), lc, declared, opt_target=None)
            if arm is None:
                note_detail("decl.reseat_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if not isinstance(arm, _ConvertSource):
                return arm
            src_name = arm.src
            convert = THIRFormConvert(
                result_type=vtype,
                value=src_name, form=Form.BORROW,
                is_const=lc.declaring(stmt.name).const, loc=loc)
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                alias_binding=alias_binding(src_name, vtype,
                                            lc.declaring(stmt.name).const, analyzer),
                storage_borrow=storage_borrow(src_name, vtype,
                                              lc.declaring(stmt.name).const, analyzer),
                value=convert, loc=loc)
        # A pointer-variant union local's reseat: a concrete-member rvalue
        # through a slot (`.emplace` + re-lift), else the slotless value
        # `_ptr_union_value` builds.
        ptr_u = (_eligible_ptr_union_wide(declared[stmt.name], lc.analyzer)
                 if is_reassign else None)
        if ptr_u is not None and stmt.init is not None:
            if not isinstance(stmt.init, TpyNoneLiteral) and not _ptr_union_source_ok(
                    stmt.init, declared, analyzer, ptr_u,
                    allow_field=stmt.name not in lc.prescan.reassigned):
                slot_kind = _ptr_union_slot_kind(stmt.init, ptr_u, declared,
                                                 lc, analyzer)
                if slot_kind is None:
                    note_detail("decl.ptr_union_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                # The rvalue kind through the rebind slot pre-declared at
                # the decl (`.emplace` + re-lift), or -- when the decl had
                # no rvalue init and so no slot (`v: A|B|None = None; v =
                # A(...)`) -- the slotless inline form (a fresh `__slot_N`
                # declared at the reseat). The address reseat is a later
                # rung.
                if slot_kind is not PtrSlotKind.UNION_RVALUE:
                    note_detail("decl.union_reseat_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if lc.declaring(stmt.name).rebind_slot:
                    _witness("reseat.union_rvalue")
                    _rk = PtrSlotKind.UNION_RVALUE
                else:
                    _witness("reseat.union_inline_slot")
                    _rk = PtrSlotKind.UNION_INLINE_SLOT
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=_rk,
                    value=_lower_expr(stmt.init, lc, declared,
                                      use=_ExprUse(
                                          result=_ExprResultUse.BORROW_BIND)),
                    val_cpp=_union_storage_val_cpp(ptr_u), loc=loc)
            u_init = _ptr_union_value(
                stmt, ptr_u, lc.declaring(stmt.name).const, lc,
                declared, loc)
            return THIRAssign(
                target=THIRName(result_type=ptr_u, name=stmt.name,
                                form=Form.BORROW, loc=loc),
                value=u_init, loc=loc)
        decl_tgt = declared.get(stmt.name, vtype)
        if isinstance(stmt.init, TpyStrLiteral) and _eligible_char(decl_tgt):
            # A GLOBAL is pre-seeded into `declared`, so its initializing
            # write is a "reassign" here without being one in the source
            # (`c = 'a';` -- the global-declared assign arm, same char
            # literal render as a first decl). A genuine LOCAL reassign
            # keeps rejecting.
            if ((is_reassign and not lc.top_level_scope)
                    or len(stmt.init.value) != 1):
                note_detail("decl.char_literal")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        # Branch position included: a branch-FIRST literal decl reaching the
        # check is genuinely block-local (the LINCHPIN note below -- escaping
        # names are hoisted or rejected, and the check itself rejects
        # hoisted/reassigned/move-through), so it emits the same plain decl
        # at branch indent.
        # A list/dict literal at a recursive-union WRAPPER slot
        # (`t: Tree[int] = [1, [2, 3], 4]` / `p: JsonValue = {...}`): the
        # container of wrapper members is spelled and the wrapper's
        # converting ctor absorbs it. Its own arm because the ordinary
        # container-literal decl path keys on the SLOT being a container
        # family, which a wrapper is not. Same block-local guards as that
        # path -- a rebound / hoisted / move-through name is excluded.
        if (not is_reassign
                and isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral))
                and stmt.name not in lc.prescan.reassigned
                and stmt.name not in lc.prescan.hoisted
                and stmt.name not in lc.prescan.move_through):
            ru_w = _eligible_wrapper_union(vtype, analyzer)
            if ru_w is not None and _ru_container_literal_ok(stmt.init,
                                                             analyzer):
                _witness("decl.ru_wrapper_literal")
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=_lower_ru_literal(stmt.init, ru_w, lc, declared),
                    form=Form.VALUE, loc=loc)
            if _ru_instance_literal_ok(stmt.init, analyzer):
                inst = analyzer.get_expr_type(stmt.init)
                _witness("decl.ru_instance_literal")
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=_lower_ru_literal(stmt.init, inst, lc, declared),
                    form=Form.VALUE, loc=loc)
        container_literal = (
            not is_reassign
            and _container_literal_decl_ok(
                stmt, declared, lc.prescan, analyzer))
        storage_call = False
        storage_src = _storage_decl_src(stmt.init, analyzer)
        if storage_src:
            fam = _storage_call_ret(analyzer.get_expr_type(stmt.init), analyzer)
            if fam is not None:
                # Branch-FIRST decls included: block-local (the LINCHPIN
                # note), and the container guards below exclude every
                # escaping/rebinding shape -- same plain decl at branch
                # indent (like the native-record arm below).
                # A recursive-union WRAPPER shares the container guards for
                # every BORROWED source: the wrapper struct is a value type,
                # but a `T&` alias of one cannot be reseated any more than a
                # container alias can.
                wrapper_fam = (isinstance(fam, UnionType)
                               and fam.needs_wrapper())
                if _storage_call_container(fam) or wrapper_fam:
                    # ... an OWNED wrapper result, though, fills a plain value
                    # slot (`JsonValue v = json::loads(s);`), so a second
                    # assignment to the same name is an ordinary value assign
                    # with no slot or pointer to rebind. Only the CONTAINER
                    # family needs the pointer-local machinery a reassigned
                    # name would ask for, and the hoisted / move-through
                    # shapes need their own predecl either way.
                    wrapper_value_slot = (
                        wrapper_fam and not _storage_call_container(fam)
                        and is_rvalue_source(analyzer, stmt.init)
                        and stmt.name not in lc.prescan.hoisted
                        and stmt.name not in lc.prescan.move_through)
                    if (not wrapper_value_slot
                            and (is_reassign
                                 or stmt.name in lc.prescan.reassigned
                                 or stmt.name in lc.prescan.hoisted
                                 or stmt.name in lc.prescan.move_through)):
                        note_detail("decl.container_call_reassigned")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    # A BORROW container return binds the `T&` alias through
                    # the shared borrow-local rung (`decl.record_borrow_call`)
                    # before this cascade is reached; one that gets here is
                    # a borrow the storage decl below would COPY, so it stops.
                    if not is_rvalue_source(analyzer, stmt.init):
                        note_detail("decl.container_call_borrow")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    storage_call = True
                    _witness("decl.storage_call")
                else:
                    storage_call = True
                    _witness("decl.storage_call")
        # A @native free call returning a by-value record (`f = open(path)` ->
        # `::tpy::TextFile f = ::tpy::builtin_open(path);`): a plain-value decl,
        # like the container/tuple storage rows. The record is a non-value
        # reference type, so a reassigned / hoisted / escaping target would take
        # the pointer-local form -- the reassigned/hoisted/move_through
        # guards exclude those; a branch-FIRST inline decl (the block-local
        # `open()` in a try body) still emits the plain value decl.
        if (not storage_call
                and not is_reassign
                and stmt.name not in lc.prescan.reassigned
                and stmt.name not in lc.prescan.hoisted
                and stmt.name not in lc.prescan.move_through):
            if (isinstance(stmt.init, TpyCall)
                    and _native_record_rvalue_call_shape(stmt.init, analyzer)):
                storage_call = True
                _witness("decl.native_record_call")
            elif (_rvalue_storage_decl_call(stmt.init, analyzer)
                  or _rvalue_ref_init(stmt.init, vtype, analyzer)):
                storage_call = True
                _witness("decl.rvalue_storage_unary"
                         if isinstance(stmt.init, TpyUnaryOp)
                         else "decl.rvalue_storage_select"
                         if _select_node(stmt.init)
                         else "decl.rvalue_storage_call")
        # `x = x + y` self-append peephole (checked on every reassignment of
        # an owned-str-family local before the
        # generic emit): the RHS concat's left operand is the target itself, so
        # the whole statement emits `x += y;` (buffer reuse) instead of the
        # concat-and-assign.
        if (stmt.name in declared
                # The peephole is LOCAL-keyed: the global-decl write path
                # renders the plain concat-assign, so a write-seeded str
                # GLOBAL must not fold. An Own[str] PARAM must not fold
                # either -- the fold keys the DECLARED type, and the Own
                # wrapper fails its is_str_type test, so the plain
                # concat-assign renders there.
                and stmt.name not in lc.prescan.global_seeded
                and stmt.name not in lc.prescan.owned_viewfam_params
                and _owned_str_append_target(vtype, analyzer)):
            rhs = _str_self_append_rhs(stmt.name, stmt.init)
            if rhs is not None:
                return THIRStrAppend(target=stmt.name,
                                     value=_lower_expr(rhs, lc, declared), loc=loc)
        # `x = None` at a value-union binding renders the monostate member --
        # target-typed at lowering, like the char decl below (F4 U1). At a
        # `Ptr[T]` value binding it renders `nullptr` (a VALUE-form None --
        # _emit_literal's non-STORAGE arm; the gate pinned the binding type).
        if isinstance(stmt.init, TpyNoneLiteral):
            none_tgt = declared[stmt.name] if stmt.name in declared else vtype
            ut = _eligible_value_union(none_tgt)
            opt_slot = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(none_tgt)))
            if ut is not None:
                init: 'THIRExpr | None' = THIRLiteral(
                    result_type=ut, value=None, form=Form.STORAGE, loc=loc)
            elif ((_wrap_none := _eligible_wrapper_union(
                    none_tgt, analyzer)) is not None):
                # `a: V = None` at a recursive-union WRAPPER slot: the
                # monostate member, absorbed by the wrapper's forwarding
                # ctor -- `V a = std::monostate{};`. First decls of
                # non-reassigned names only: a REASSIGNED wrapper local is
                # the rebind-slot pointer binding (slot emplace + reseat),
                # which is not lowered here.
                if is_reassign or stmt.name in lc.prescan.reassigned:
                    note_detail("decl.wrapper_none_reassign")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("decl.wrapper_none")
                init = THIRLiteral(result_type=_wrap_none, value=None,
                                   none_cpp="std::monostate{}",
                                   form=Form.VALUE, loc=loc)
            elif (isinstance(opt_slot, OptionalType)
                  and not opt_slot.uses_pointer_repr()):
                # `x = None` at a value-repr Optional binding (a reassigned
                # `int | None` param) stores the storage-form `std::nullopt`;
                # the pointer-repr `A*` optional slot takes nullptr on its own
                # field-write / OTHER-local arm.
                _witness("decl.opt_none")
                init = THIRLiteral(result_type=none_tgt, value=None,
                                   form=Form.STORAGE, loc=loc)
            else:
                if not _eligible_ptr_value(none_tgt, analyzer):
                    note_detail("decl.none_init_slot")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if not is_reassign and _dyn_proto_ptr(none_tgt):
                    note_detail("decl.dyn_ptr_none")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("decl.ptr_none")
                init = THIRLiteral(result_type=none_tgt, value=None,
                                   form=Form.VALUE, loc=loc)
        elif isinstance(stmt.init, TpyTupleLiteral):
            tuple_t = _value_tuple_nested(declared.get(stmt.name, vtype),
                                          analyzer)
            if tuple_t is None:
                note_detail("decl.tuple_literal_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("decl.tuple_literal")
            try:
                init = _lower_tuple_literal(stmt.init, tuple_t, lc, declared)
            except ThirUnsupported as ex:
                raise ex.with_context(stmt_reject_reason(
                    stmt, f"decl.tuple_literal_shape:{ex.reason}")) from None
        else:
            # One verdict for this arm's three readers below. `vtype` is
            # the DECLARED slot until the str/bytes resolution past the
            # arm rebinds it, so the reader after that rebind asks again.
            owned_view_opt_src = _owned_view_opt_decl_src(stmt, vtype, lc)
            if not is_reassign and not container_literal and not storage_call:
                # A branch-FIRST inline decl (`in_branch_first`) is genuinely
                # block-local -- an escaping var is hoisted into `declared` and
                # never reaches here as a first decl -- so it decls the same
                # plain spelled copy as a function-scope decl.
                # (The `detail` tag distinguishes the reject site only.)
                # LINCHPIN: if/try/with/for-each pre-declare escaping names
                # via the hoist predecl helpers and match via its own hoist
                # admission; only `while` has no predecl path (it rejects
                # escaping hoists outright). The whole-function
                # `lc.unhandled_hoists` reject remains the backstop -- so an
                # un-hoisted escaping decl can never slip through this gate
                # as a block-local.
                slot_ok = (
                    _eligible_scalar(vtype) or _eligible_char(vtype)
                    or _eligible_enum(vtype, analyzer) is not None
                    or _resolved_str_value(vtype, analyzer) is not None
                    or _resolved_bytes_value(vtype, analyzer) is not None
                    or _is_string_owned(vtype)
                    or _eligible_value_union(vtype) is not None
                    # M4c: `a: Tree = Leaf(42)` -- the wrapper's template
                    # converting ctor absorbs the member ctor rvalue, so
                    # the decl is the plain spelled copy. The literal
                    # sibling (`leaf: Tree[int] = 9`) rides the same
                    # converting-ctor absorb. A REASSIGNED wrapper local is
                    # the rebind-slot pointer binding (`V* a = &__slot_N;`),
                    # so it must not take the plain spelled copy.
                    or (stmt.name not in lc.prescan.reassigned
                        and (_wrapper_member_ctor_slot(stmt.init, vtype,
                                                       analyzer)
                             or _wrapper_member_literal_slot(
                                 stmt.init, vtype, analyzer)))
                    # A value-repr Optional[scalar] slot (`y: int32 | None =
                    # items[i]`): the whole `std::optional<T>` lands bare, the
                    # value-repr twin of the plain-scalar decl. The owned-view
                    # twin (`cmd: str | None = acc` -> `std::optional
                    # <std::string> cmd = acc;`) takes the same bare copy,
                    # gated to genuinely whole-optional sources.
                    or _value_opt_scalar(vtype, analyzer) is not None
                    or _value_opt_callable(vtype, analyzer) is not None
                    # ... and the value-TUPLE inner (`use_auth = auth` at
                    # `tuple[str, str] | None`): the same bare
                    # `std::optional<std::tuple<...>>` copy. Its reads route
                    # only WHOLE (has_value test, same-optional pass), which
                    # the name arms already gate.
                    or (_value_opt_tuple(vtype, analyzer) is not None
                        # An owned-record inner binds only a fresh result:
                        # copying another binding would not share its
                        # records.
                        and (_value_opt_tuple_copyable(vtype, analyzer)
                             is not None
                             or is_rvalue_source(analyzer, stmt.init))
                        and _witness("decl.value_opt_tuple_slot"))
                    or _owned_view_opt_whole_src(stmt, vtype, lc)
                    # ... and the source whose type is the INNER rather than
                    # the whole optional (`t: str | None = s`), which
                    # constructs the owned inner explicitly.
                    or (owned_view_opt_src
                        and _witness("decl.value_opt_view_materialize"))
                    or (_view_inner_opt_call_slot(stmt, vtype, lc)
                        and _witness("decl.view_inner_opt_call"))
                    or _slice_object_type(vtype)
                    or _range_object_value(vtype)
                    or _eligible_ptr_value(vtype, analyzer)
                    or _callable_value(vtype)
                    # Value-form slots where borrow/storage coincide: a
                    # span and a value tuple both decl as the plain spelled
                    # copy (`std::span<T> s = sp;` / `std::tuple<...> u = t;`).
                    or _span_slot(vtype, analyzer)
                    # An `Any` slot (`a: Any = 42`): a value-type cell whose
                    # init is the `into_any` make_any wrap (or an already-Any
                    # source). The plain-copy decl spells `::tpy::Any a = ...`.
                    or _is_any_type(vtype)
                    or _value_tuple(vtype, analyzer) is not None
                    # An owned-optional record slot (`std::optional<Rc<T>>
                    # upgraded = w.upgrade();`) -- the record twin of the
                    # value-opt scalar row; registration below keys its
                    # narrowed/None-test reads.
                    or _own_opt_record_call_slot(stmt, vtype, lc, analyzer)
                    # The VALUE-record optional twin (`g = t.goal` on an
                    # `Optional[Coord]` property) -- the same plain spelled
                    # `std::optional<Coord>` copy.
                    or _value_opt_record_slot(stmt, vtype, lc)
                    # A bare VALUE-record slot, user or builtin (`bad5 =
                    # date(...) + td` -- `::tpystd::datetime::date bad5 =
                    # <init>;`, `w = _make_waker(...)`): the plain spelled
                    # copy, exactly a scalar decl's shape; the init rides
                    # its own arms (ctor/dunder-binop/method rvalues).
                    or _value_record_slot(vtype)
                    # A native-iterator value slot (`it = SpanIter(s)` /
                    # annotated `a.__iter__()`) spells the plain copy; a
                    # structural-protocol slot (`it = iter(c)`) spells
                    # `auto` and the init render carries the type.
                    or _native_iter_value_slot(vtype, analyzer)
                    # An OPEN type-param slot (`T newitem = copy(heap[pos])`):
                    # the plain spelled copy, its C++ shape deferred to
                    # instantiation. `T` is a plain non-value type, so the
                    # indirection rules apply in full: a reassigned / hoisted /
                    # move-through name and a non-rvalue source each bind an
                    # alias (`T* v = &__slot_N;`, `T& b = a;`) instead, and
                    # those renders are their own rows.
                    or (_type_param_value_slot(vtype)
                        and stmt.name not in lc.prescan.reassigned
                        and stmt.name not in lc.prescan.hoisted
                        and stmt.name not in lc.prescan.move_through
                        and stmt.init is not None
                        and is_rvalue_source(analyzer, stmt.init)
                        and _witness("decl.type_param_slot"))
                    or _protocol_auto_slot(vtype)
                    # A LAZY list-repeat slot (`x = [7] * n` never
                    # materialized): `::tpy::repeat_range<int32_t> x =
                    # <init>;` -- the plain spelled copy like a scalar's.
                    # The materialized families ride the container-literal
                    # decl path instead. A REBOUND name is a pointer-local
                    # (`repeat_range<T>* r = &__slot_N;`), so it is
                    # excluded exactly as the container-literal
                    # decl gate excludes its own.
                    or (isinstance(vtype, ListRepeatType)
                        and stmt.name not in lc.prescan.reassigned
                        and stmt.name not in lc.prescan.hoisted
                        and stmt.name not in lc.prescan.move_through
                        and _witness("decl.list_repeat_slot"))
                    # An OwnIter/CopyIter slot (`oi = own_iter(src)`):
                    # the TypeDef spells `auto`, the special-builtin init
                    # render carries the concrete type.
                    or (isinstance(vtype, NominalType)
                        and (is_own_iter(vtype) or is_copy_iter(vtype))
                        and _witness("decl.own_copy_iter_slot"))
                    # A per-element-Own record tuple slot
                    # (`t = make_pair()` -> `std::tuple<Counter, Counter>`)
                    # is the plain spelled copy like a value tuple.
                    or _own_record_tuple(vtype, analyzer) is not None
                    # ... and an open-T tuple slot that spells its storage
                    # (a value-bounded T, or a record keeping the storage
                    # copy, BUGS.md#open-t-tuple-reseat-storage-residue); a
                    # T that may be a reference otherwise holds its
                    # source's form (the binding rows).
                    or (_open_t_tuple_storage_decl(
                            vtype, lc.declaring(stmt.name), analyzer)
                        and _witness("decl.open_t_tuple_slot"))
                    # A `None`-annotated unit slot (`local: None = x`):
                    # `std::monostate local = x;` -- borrow and storage
                    # coincide (monostate has no borrow form), so the decl
                    # is the plain spelled copy like a scalar's.
                    or (isinstance(unwrap_readonly(vtype), NoneType)
                        and _witness("decl.none_unit_slot"))
                    # A `bytearray` slot with a DECIDED init shape: the
                    # bytesview_to_bytearray materialize copy or an owned
                    # dunder rvalue. NAME inits ride the alias cascade;
                    # the bytes-param identity coercion keeps rejecting
                    # (its render is wrong code, see BUGS.md).
                    or _bytearray_value_slot_init(stmt.init, vtype,
                                                  analyzer))
                if not slot_ok:
                    # Branch-first REBIND_SLOT and owned-record decls are
                    # handled by the borrow cascade above (its per-arm
                    # branch-first admission).
                    note_detail("decl.branch_slot_type" if in_branch_first
                                else "decl.slot_type")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            # A char-annotated decl init lowers target-aware: `c: char = 'x'` ->
            # `char c = 'x';` (the decl type threads into the render);
            # a float literal into a float32 binding (annotated decl or
            # reassign of a float32 local) takes the `f` suffix the same way.
            # A flushable statement position: a direct call init may hoist
            # arg temps (temp_args, inert for non-call inits).
            src = _peel_stale_view_owned_coerce(
                stmt.init, declared.get(stmt.name, vtype), analyzer)
            if (isinstance(src, TpyCoerce)
                    and _coerce_disposition(src) == "materialize"
                    and owned_view_opt_src):
                # Sema targets the view->owned coercion at the WHOLE optional,
                # but the copy constructs its INNER (the optional's own
                # converting ctor from a view is explicit). The wrap below
                # spells that, so the coerce node here would be a second,
                # ill-typed convert.
                src = src.expr
            whole_optional_reassign = (
                stmt.name in declared
                and isinstance(src, TpyName)
                and _value_opt_scalar_binding(src.name, lc)
                # Whole-optional only into an optional TARGET slot; a plain-T
                # binding reads the narrowed inner ((*p)).
                and _value_opt_target_binding(stmt.name, lc))
            ru_reassign = (
                _eligible_wrapper_union(declared.get(stmt.name, vtype),
                                        analyzer)
                if (stmt.name in declared
                    and isinstance(src, (TpyArrayLiteral, TpyDictLiteral)))
                else None)
            if ru_reassign is not None and _ru_container_literal_ok(
                    src, analyzer):
                # A container literal REASSIGNED to a wrapper-union binding
                # (the top-level global init `g = [1, [2, 3], 4];`): the
                # spelled container of wrapper members, absorbed by the
                # wrapper's converting assign -- the decl arm's
                # `decl.ru_wrapper_literal` render at the assign sink.
                _witness("assign.ru_wrapper_literal")
                init = _lower_ru_literal(src, ru_reassign, lc, declared)
            elif whole_optional_reassign:
                init = _flush_witness(
                    "flush.vardecl",
                    _lower_expr(
                        src, lc, declared, use=_ExprUse(allow_temps=True),
                        allow_whole_optional=True))
            else:
                # A value-repr Optional[scalar] slot consumes a WHOLE-optional
                # init source bare (`y: int32 | None = items[i]` -- the
                # `std::optional<T>` element / call result lands directly), so
                # thread allow_whole_optional to admit those reads at their
                # gate. The owned-view twin (`str | None` ->
                # `optional<string>`) takes the same bare whole-optional copy
                # (`flat = rec.key;`), gated to genuinely whole-optional
                # sources (`_owned_view_opt_whole_src`).
                # ... and the value-TUPLE and value-RECORD slots, whose
                # whole-optional copy is the same bare one.
                opt_slot = (_value_opt_scalar(vtype, analyzer) is not None
                            or _value_opt_record(vtype) is not None
                            or _value_opt_callable(vtype, analyzer) is not None
                            or _value_opt_tuple(vtype, analyzer) is not None
                            or _owned_view_opt_whole_src(stmt, vtype, lc))
                # On a reassignment the sink is the TARGET's existing slot:
                # only an optional binding takes the whole-optional copy; a
                # plain-T binding reads the narrowed inner ((*p)).
                # A value-repr optional tuple/callable target is asked off its
                # DECLARED type rather than the binding registry: the name
                # arm keys its deref-on-narrow read on the declared type
                # too, so these kinds are never registered as bindings.
                if (stmt.name in declared
                        and not _value_opt_target_binding(stmt.name, lc)
                        and _value_opt_tuple(declared[stmt.name],
                                             analyzer) is None
                        and _value_opt_callable(declared[stmt.name],
                                                analyzer) is None):
                    opt_slot = False
                # A view-family FIELD read into a view-family decl slot
                # (`s = p.name` -> `std::string_view s = p.name;` for a view
                # slot, `std::string s = p.name;` for an owned one): the bare
                # member read lands at the STORAGE decl sink like the print /
                # f-string sinks admit it. Both families, because the slot's
                # family is the WRAP's business -- the view->owned chokepoint
                # below spells `::tpy::Bytes(v)` for the bytes half exactly as
                # it spells `std::string(v)` for the str half.
                # The owned-inner value-repr Optional slot is the same sink one
                # unwrap deeper: `_owned_view_opt_decl_src` has already settled
                # that this source reaches the optional's INNER in borrow form
                # and the wrap below constructs that inner, so the read itself
                # is the same bare member read. The whole view family rides
                # that verdict -- the slot's family is the wrap's business,
                # not the read's.
                str_field_init = (
                    isinstance(src, TpyFieldAccess)
                    and (_resolved_str_value(vtype, analyzer) is not None
                         or (_bytes_view_family_value(vtype, analyzer)
                             and _witness("decl.bytes_field"))
                         or (owned_view_opt_src
                             and _witness("decl.viewfam_field_at_opt_slot"))))
                # A same-union NAME reassign (`pet = other;`) copies the
                # whole variant -- both sides render bare regardless of
                # sema's assign-narrow on the source, so the divergent
                # read is safe at this sink.
                _tgt_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                              declared[stmt.name])))
                          if stmt.name in declared else None)
                _union_reassign_name = (
                    isinstance(_tgt_u, UnionType)
                    and isinstance(src, TpyName)
                    and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        declared.get(src.name)))) == _tgt_u)
                # The init renders against the binding it lands in: on a
                # reassign that is the DECLARED local / param type
                # (`_var_decl_type` falls back to the init's own type
                # there, which for a bytes literal is owned `bytes`).
                init = (_flush_witness(
                            "flush.vardecl",
                            _lower_char_targeted(
                                src, decl_tgt, lc, declared,
                                use=_ExprUse(
                                    result=_ExprResultUse.STORAGE,
                                    allow_temps=True,
                                    # `Ptr[T]` IS `T*`, so a BORROW-returning
                                    # ptr-repr Optional result lands in the
                                    # slot bare (`Node* p = first(items);`)
                                    # -- the same collapse the param/return
                                    # sites spell, here at the decl. The
                                    # call-shape rows are the specialized
                                    # arms' above: this general arm has
                                    # vetted no whole-tuple consumption and
                                    # no pointer binding, so it claims
                                    # neither. A select of fresh operands
                                    # direct-initializes the binding's own
                                    # storage (the prvalue `?:`); one with
                                    # an existing-object operand would copy
                                    # that object where CPython binds it,
                                    # so it takes no select form.
                                    pos=SinkPos.LOCAL_DECL,
                                    forms=_decl_slot_forms(
                                        vtype, analyzer,
                                        whole_tuple_call=False,
                                        from_call=False, ptr_local=False)
                                    | _ONLY_SELECT_FRESH_PRVALUE),
                                allow_whole_optional=opt_slot,
                                field_owned_str_ok=str_field_init,
                                allow_union_divergent=_union_reassign_name))
                        if stmt.init else None)
            init = _slot_literal_retype(
                init, declared.get(stmt.name, vtype), lc)
        # A str/bytes local's binding type is a Pending view type; carry the
        # RESOLVED view/owned type (string_view/string, span/vector) on the nodes.
        str_t = _resolved_str_value(vtype, analyzer)
        if str_t is not None:
            vtype = str_t
        bytes_t = _resolved_bytes_value(vtype, analyzer)
        if bytes_t is not None:
            vtype = bytes_t
        # The parser emits TpyVarDecl for every `name = expr`; a write to an
        # already-declared name is a reassignment, not a re-declaration, so a
        # first decl emits `T x = ...` and a reassignment emits `x = ...`.
        if stmt.name in declared:
            assert init is not None  # eligibility requires a var-decl init
            # A value-repr Optional[scalar/view] name reassigned to an existing
            # OPTIONAL local passes the bare optional (`q = p;`) -- the correct
            # whole-optional copy, so strip the name arm's deref-on-narrow.
            # A plain-`T` target keeps the deref: the reassignment RHS
            # unwraps a proven-non-None value-Optional source to `(*p)`.
            if (isinstance(stmt.init, TpyName) and isinstance(init, THIRName)
                    and (_value_opt_scalar_binding(stmt.init.name, lc)
                         or _value_opt_view_binding(stmt.init.name, lc))
                    and _value_opt_target_binding(stmt.name, lc)):
                init = _whole_optional_bare(init)
            # An owned-BYTES target fed a view source takes the family's
            # view->owned construction: `std::vector<uint8_t>` has no
            # `operator=` from a span. The TARGET's declared storage asks --
            # the source's own shape does not decide it, and a slice result or
            # a view local arrives here as a plain value. `std::string` does
            # take a string_view, so the str twin stays the bare `t = s;` --
            # wrapping it would build a temporary the assignment does not need.
            tgt_bytes = _resolved_bytes_value(declared[stmt.name], analyzer)
            if tgt_bytes is not None and is_bytes_type(tgt_bytes):
                init = _wrap_view_owned_sink(init, tgt_bytes, loc)
            elif isinstance(stmt.init, TpyName) and _node_moves(init):
                # Sema's last-use move: the dead name hands its storage over.
                # Bare names only, as at a return: a coerce-wrapped source
                # renders a fresh value (`&b`), which a move cannot steal.
                init = THIRMove(result_type=init.result_type, value=init,
                                form=init.form, loc=loc)
            # A write-seeded native-linkage global's target spells the BARE
            # C name (`g_counter = val;` -- the global-write arm's
            # native_global_names target).
            return THIRAssign(
                target=THIRName(
                    result_type=vtype, name=stmt.name,
                    global_binding=global_name_binding(stmt.name, vtype, lc),
                    cpp=lc.prescan.global_write_cpp.get(stmt.name), loc=loc),
                value=init,
                loc=loc,
            )
        # Owned-str/bytes decl init off a view-form source copies explicitly --
        # `std::string u = std::string(v);` / `std::vector<uint8_t> u =
        # ::tpy::Bytes(v);` -- the view->owned CONSTRUCTION being explicit.
        # The view->owned chokepoint; a literal init
        # (VALUE form: const char[N] / an already-owned bytes render)
        # constructs directly and stays bare.
        if (init is not None and init.form is Form.BORROW
                and ((str_t is not None and is_str_type(str_t))
                     or (bytes_t is not None and is_bytes_type(bytes_t)))):
            init = THIRFormConvert(result_type=str_t if str_t is not None else bytes_t,
                                   value=init, form=Form.STORAGE, loc=loc)
        # The OPTIONAL twin of that chokepoint: an owned-inner value-opt slot
        # fed a view source constructs the INNER explicitly
        # (`std::optional<std::string> t = std::string(s);`) -- the optional's
        # own converting ctor from a view is explicit, so the bare init would
        # not compile. Same wrap the return sink spells.
        if _owned_view_opt_decl_src(stmt, vtype, lc):
            init = _wrap_view_owned_sink(
                init,
                unwrap_readonly(_value_opt_owned_view(vtype, analyzer).inner),
                loc)
        declared[stmt.name] = vtype
        # A first decl of a value-repr Optional binds `std::optional<T>`
        # (its record's `value_opt` kind); the record twins are counted.
        rec = lc.declaring(stmt.name)
        if (not is_reassign
                and rec.construct is BindingConstruct.OPT_RECORD_CALL):
            _witness("decl.opt_record_call")
        elif (not is_reassign
              and rec.construct is BindingConstruct.OPT_VALUE_RECORD):
            _witness("decl.opt_value_record")
        # An enum decl type spells via render_type (codegen's type_to_cpp):
        # its enum arm routes through enum_cpp_name -- the authoritative
        # spelling for cross-module (`::tpyapp::m::E`), @native (user qname),
        # and nested (`Outer::Kind`) enums; plain to_cpp() reads the
        # native_cpp_names view, which an aliased-import collision can skew.
        # A @dynamic-protocol-pointee Ptr decl spells `auto`: any
        # protocol-containing decl type spells `auto`
        # (`auto q = p;`), unlike the spelled `T*` of record/scalar pointees.
        # A container decl carrying an enum in its args needs the same
        # render_type spelling rule as a bare enum decl.
        if not is_reassign and rec.iterator_object:
            # A second name for a generator object whose frame type has no
            # spelling (its callee's frame takes deduced template args) is
            # the same object, as a record alias is; a frame is not copyable.
            cpp_type = "auto&"
            _witness("decl.iterator_object_alias")
        elif _dyn_proto_ptr(vtype) or _protocol_auto_slot(vtype):
            cpp_type = "auto"
        elif (_eligible_enum(vtype, analyzer) is not None
              or _container_enum_spell(vtype, analyzer)
              or _callable_value(vtype)
              # The native-iterator slot spells its generic instantiation
              # (`::tpy::SpanIter<const int32_t>`) via render_type.
              or _native_iter_value_slot(vtype, analyzer)
              # OwnIter/CopyIter: render_type spells `auto` (the TypeDef's
              # formatter), the init render carries the concrete type.
              or (isinstance(vtype, NominalType)
                  and (is_own_iter(vtype) or is_copy_iter(vtype)))
              # The owned-optional record slot spells via render_type
              # (generic instantiation + cross-module qualification --
              # `std::optional<::tpystd::tplib::rc::Rc<Cell>>`).
              or rec.value_opt is ValueOptKind.RECORD):
            cpp_type = lc.render_type(vtype)
        elif isinstance(vtype, TupleType):
            # A tuple slot spells its elements RESOLVED: the emit fallthrough's
            # bare `to_cpp()` RAISES on a PendingViewType element (`tup = (e, 1)`
            # off a pending-str local) and renders an IntLiteralType as its
            # value. So: `resolve_int_literals` (the helper's per-element
            # half) then `type_to_cpp`, whose own recursion reaches a pending
            # element nested in an inner tuple.
            cpp_type = lc.render_type(resolve_tuple_pending(vtype, analyzer))
        else:
            cpp_type = None
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init, cpp_type=cpp_type,
            tuple_layout=tuple_layout(vtype, analyzer),
            loc=loc)
    if isinstance(stmt, TpyAssign):
        begin_stmt()
        if isinstance(stmt.target, TpyName):
            # A reassign clears the flow-sensitive literal fact.
            lc.literal_facts.pop(stmt.target.name, None)
        pointers = scope.admission_pointers()
        narrowed = lc.narrow.narrowed.keys()
        er_fi = _error_return_stmt_fi(stmt.value, analyzer)
        if er_fi is not None:
            # A direct @error_return call RHS: the statement-level unwrap
            # block, before target classification. Declared plain-local name
            # targets and markers-clean FIELD targets route; subscript /
            # marked-field targets reject.
            if (isinstance(stmt.target, TpyFieldAccess)
                    and _field_markers_clean(stmt.target)):
                # `self.p = make(n)` -- the non-name target branch: the
                # target renders as a plain lvalue (RECEIVER use keeps it
                # bare) and the bind line assigns the moved unwrap into
                # it. An unroutable target raises, rejecting the body.
                _reject_nested_error_return_arg(stmt, stmt.value, analyzer)
                tgt = _lower_expr(stmt.target, lc, declared,
                                  use=_ExprUse(result=_ExprResultUse.RECEIVER))
                call_node = _lower_expr(
                    stmt.value, lc, declared, error_return_raw=True,
                    use=_ExprUse(allow_temps=True))
                _witness("er.field_target_bind")
                return THIRErrorReturnBind(name="", call=call_node,
                                           target=tgt,
                                           staged=lc.exit_staged, loc=loc)
            if (not isinstance(stmt.target, TpyName)
                    or stmt.target.name not in declared
                    or stmt.target.name in narrowed
                    or stmt.target.name
                    in analyzer.function_global_decls.get(lc.func, set())):
                note_detail("error_return.assign_target")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_error_return_bind(
                stmt, stmt.target.name, stmt.value, er_fi,
                declared.get(stmt.target.name), lc, declared, loc)
        if isinstance(stmt.target, TpyName):
            if (stmt.target.name in narrowed
                    or (lc.prescan.has_self and stmt.target.name == "self")
                    or (isinstance(stmt.value, TpyStrLiteral)
                        and _eligible_char(
                            declared.get(stmt.target.name)))
                    or isinstance(stmt.value, TpyBytesLiteral)
                    or stmt.target.name not in declared):
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if _module_slot_global(stmt.target.name, lc):
                # A non-value global's write is never a plain assign -- it
                # allocates or reuses the module-init `static
                # __global_slot_N`. Delegate to the same driver the var-decl
                # arm uses; the generic tail below would reuse a rebind slot
                # spelled `__slot_N`, which module scope never declares.
                # (Only frontend-IR ASTs reach a global write as a raw
                # assign.) A BRANCH write rejects: its flavors are decided
                # per-kind on the var-decl side and none is witnessed here.
                if scope.in_branch:
                    note_detail("assign.global_slot_branch")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                return _lower_global_slot_write(
                    TpyVarDecl(name=stmt.target.name, type=None,
                               init=stmt.value, loc=loc),
                    lc, declared, loc)
            parked = _park_tuple_global(
                TpyVarDecl(name=stmt.target.name, type=None,
                           init=stmt.value, loc=loc),
                scope, lc, declared, loc, binding=stmt)
            if parked is not None:
                return parked
        elif (isinstance(stmt.target, TpySubscript)
              and stmt.target.slice_function_info is not None
              and isinstance(stmt.target.index, TpySlice)):
            # `c[a:b] = v` / `c[a:b:s] = v` -> list_set_slice /
            # list_set_stepped_slice. Its own gate + node, distinct from the
            # single-index subscript-write arm below.
            return _lower_slice_assign(stmt, lc, declared, loc)
        elif _typed_dict_write_target(stmt.target, declared, pointers,
                                      narrowed, analyzer):
            # `d["key"] = v` -> `d.key = v;`: the target rides the
            # typed-dict subscript READ arm (a plain THIRFieldAccess
            # lvalue); the value renders against the field's sema slot.
            td_elem = analyzer.get_expr_type(stmt.target)
            _witness("setitem.typed_dict_field")
            return THIRAssign(
                target=_lower_expr(stmt.target, lc, declared),
                value=_slot_literal_retype(
                    _flush_witness(
                        "flush.assign",
                        _lower_expr(stmt.value, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True,
                                        slot_target=td_elem))),
                    td_elem, lc),
                loc=loc)
        elif isinstance(stmt.target, TpySubscript):
            # A store that is a call takes the call arm, whatever the
            # receiver (`_call_arm_variant`).
            any_dict_write = (
                _subscript_call(stmt.target, SubscriptDunder.SET) is None
                and _any_dict_setitem_ok(stmt, declared, pointers, narrowed,
                                         analyzer))
            user_setitem = (not any_dict_write and _user_record_setitem_ok(
                stmt, declared, pointers, narrowed, analyzer))
            if not (any_dict_write or user_setitem or _container_setitem_ok(
                    stmt, declared, pointers, narrowed, analyzer)):
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _reject_const_narrowed_write_recv(stmt.target.obj, lc, stmt)
        elif (isinstance(stmt.target, TpyFieldAccess)
              and stmt.target.property_setter_call is not None):
            # A `@prop.setter` write is a void setter method call in disguise
            # (`c.value = v` -> `c.set_value(v)`), so lower the synthesized
            # call through the method-call arm in discard (statement)
            # position.
            return THIRExprStmt(
                expr=_lower_expr(
                    stmt.target.property_setter_call, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.DISCARD,
                                 allow_temps=True)),
                loc=loc)
        elif (isinstance(stmt.target, TpyFieldAccess)
              and stmt.target.dyn_setattr_call is not None):
            # D16 dyn-attr write: the statement IS the sema-synthesized
            # `obj.__setattr__("name", <value>)`, lowered as a method call.
            return THIRExprStmt(
                expr=_lower_dyn_setattr_call(
                    stmt.target.dyn_setattr_call, lc, declared),
                loc=loc)
        elif isinstance(stmt.target, TpyFieldAccess):
            return _field_write.lower_field_write(stmt, lc, declared,
                                                  pointers, loc)
        else:
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if isinstance(stmt.target, TpySubscript) and user_setitem:
            # The call arm's `recv[key] = v` (a setter that is a call, or a
            # user record's) -> the setter's arguments, stored by
            # `_store_item`. The target lowers through the record_getitem READ arm
            # (NOT subscript_prechecked, which is the container-element path):
            # its receiver/index feed the checked write emit; the value renders
            # against the elem slot bare (a value scalar / char / enum, or a
            # record whose source moves or copies whole).
            target = _lower_expr(
                stmt.target, lc, declared,
                use=_ExprUse(result=_ExprResultUse.SETITEM_TARGET))
            elem_t = analyzer.get_expr_type(stmt.target)
            v = stmt.value
            if (_svn := _strview_coerce_name(v)) is not None:
                # The gate-admitted view->owned value (`os.environ[k] =
                # CERT_PATH`): the coerce IS the `std::string(x)` copy --
                # peel it and wrap the bare view read, the S1 chokepoint.
                value = THIRFormConvert(
                    result_type=(v.expected_type
                                 if v.expected_type is not None else elem_t),
                    value=_lower_expr(_svn, lc, declared),
                    form=Form.STORAGE, loc=loc)
                _witness("setitem.user_record")
                return _store_item(stmt, target, value, lc, loc)
            if (_nested_container_elem_type(elem_t)
                    and not isinstance(v, TpyName)):
                _witness("setitem.user_record")
                return _store_item(
                    stmt, target,
                    _lower_container_elem_value(
                        stmt, unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(elem_t))),
                        target, lc, declared, loc),
                    lc, loc)
            if record_like(elem_t, analyzer) and isinstance(v, TpyName):
                if not _is_move_source(v, lc):
                    # A copy-shaped name takes the argument body below: an
                    # `Own[...]` value slot gets its copy temp, a plain `T`
                    # slot binds `const T&`; sema warns the copy.
                    pass
                else:
                    # A record element slot fed by the last use of an owned
                    # local moves in
                    # (`::tpy::__setitem__(l, 0, std::move(z))`).
                    value = THIRMove(
                        result_type=elem_t,
                        value=_lower_expr(v, lc, declared,
                                          allow_unrouted_name=True),
                        form=Form.STORAGE, loc=loc)
                    _witness("setitem.record_move")
                    return _store_item(stmt, target, value, lc, loc)
            setitem = _call_arm_variant(stmt.target, SubscriptDunder.SET,
                                        analyzer)
            assert setitem is not None
            # The value is the explicit `recv.__setitem__(key, v)` call's
            # last argument, as the key is its first.
            _set_i = len(setitem.params) - 1
            value = _dunder_arg(
                stmt.target.obj, [stmt.target.index, stmt.value],
                _set_i, setitem, lc, declared, temp_args=True,
                keep=arg_retention(setitem, _set_i, None, analyzer,
                                   stmt.value, recv=stmt.target.obj))
            _witness("setitem.user_record")
            return _store_item(stmt, target, value, lc, loc)
        if isinstance(stmt.target, TpySubscript):
            # Container subscript write: the target lowers to the same
            # subscript node a read produces (bounds_safe + the BigInt index
            # narrow ride along); the value renders against the element slot
            # (literal retype), with the view->owned `std::string(v)` copy
            # for a view-form str source into an owned-str element -- the
            # view->owned chokepoint. A flushable position
            # (temp_args), like a name assign.
            elem_t = analyzer.get_expr_type(stmt.target)
            eu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem_t)))
                  if elem_t is not None else None)
            # A widened (non-scalar) element slot passed the setitem gate
            # (family/index/receiver all checked there); the subscript READ
            # arm's element-family gate does not apply to a WRITE target.
            widened_elem = eu is not None and _setitem_widened_elem_ok(
                eu, analyzer)
            # A bytearray write target passed the setitem gate; the subscript
            # READ arm's container-family gate does not know the receiver.
            ba_write = _bytearray_recv(_subscript_container_recv_type(
                stmt.target.obj, declared, analyzer))
            # An open-K/V generic dict FIELD target (`d.data["x"] = v` off
            # the declared `dict[K, V]`): the gate admitted it on the
            # type-param key/value slice, but the READ arm's family check
            # sees the DECLARED field type -- precheck it like the widened
            # elements (the expr-side eu is already substituted).
            tp_field_recv = (isinstance(stmt.target.obj, TpyFieldAccess)
                             and _setitem_widened_family_ok(
                                 _subscript_container_recv_type(
                                     stmt.target.obj, declared, analyzer),
                                 analyzer))
            # A tuple-element container receiver (`t[0][0] = 9`): the gate
            # admitted it via _tuple_container_elem_read; the READ arm's
            # receiver family does not know the tuple shape.
            tuple_elem_recv = (
                isinstance(stmt.target.obj, TpySubscript)
                and _tuple_container_elem_read(
                    stmt.target.obj, declared, analyzer) is not None)
            # A None-narrowed ptr-repr Optional[container] NAME receiver:
            # the setitem gate proved it (the un-narrowed flavor carries
            # the runtime check); precheck so the READ gate -- whose bare
            # reads keep their bounds-safe fence -- is skipped for the
            # WRITE target only.
            _optrecv_w = (
                isinstance(stmt.target.obj, TpyName)
                and lc.binding(stmt.target.obj.name).pointer
                and stmt.target.obj.name in declared
                and isinstance(
                    (_orw := unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(declared[stmt.target.obj.name])))),
                    OptionalType)
                and _orw.uses_pointer_repr())
            # The write target sits at a STATEMENT position: index-call
            # arg temps flush before the setitem (the statement hoist),
            # threaded through the subscript arm's index use.
            target = _lower_expr(
                stmt.target, lc, declared,
                use=_ExprUse(allow_temps=True),
                subscript_prechecked=(any_dict_write or widened_elem
                                      or ba_write or tp_field_recv
                                      or tuple_elem_recv or _optrecv_w))
            if eu is not None and _f1_container_ref(eu):
                value = _lower_container_elem_value(stmt, eu, target, lc,
                                                    declared, loc)
            elif _optional_record_field_inner(eu, analyzer) is not None:
                # Pointer-repr Optional[F1] element: a borrow `T*` NAME lifts
                # borrow->storage (`::tpy::ptr_to_optional(p)` -- the element
                # store COPIES, never moves; _lift_to_element_storage). Other
                # sources (None literal, narrowed names, rvalues) reject.
                v = stmt.value
                if isinstance(v, TpyNoneLiteral):
                    # `xs[i] = None` clears the slot: the STORAGE-form
                    # `std::nullopt`, the same spelling the value-repr
                    # optional element and the Optional param slot use --
                    # a None literal carries no source repr to convert.
                    value = THIRLiteral(result_type=eu, value=None,
                                        form=Form.STORAGE, loc=loc)
                    _witness("setitem.optional_none")
                elif not (isinstance(v, TpyName)
                          and v.name not in lc.narrow.narrowed
                          and _is_borrow_ptr_local(v, declared, lc.names_with("pointer"))):
                    note_detail("setitem.optional_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                else:
                    lowered_v = _lower_expr(v, lc, declared)
                    if _node_moves(lowered_v):
                        note_detail("setitem.optional_value_shape")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    value = THIRFormConvert(
                        result_type=eu, value=lowered_v,
                        form=Form.STORAGE, loc=loc)
                    _witness("setitem.borrow_lift")
            elif _value_opt_callable(eu, analyzer) is not None:
                if isinstance(stmt.value, TpyNoneLiteral):
                    value = THIRLiteral(result_type=eu, value=None,
                                        form=Form.STORAGE, loc=loc)
                else:
                    value = _lower_expr(
                        stmt.value, lc, declared, allow_whole_optional=True,
                        use=_ExprUse(result=_ExprResultUse.STORAGE,
                                     allow_temps=True))
            elif _value_opt_scalar(eu, analyzer) is not None:
                # Value-repr Optional[scalar] element: a None literal stores
                # the STORAGE-form `std::nullopt` (`::tpy::__setitem__(items,
                # 0, std::nullopt)` -- the decl.opt_none twin), and a
                # same-ELEMENT-TYPE whole-optional element read passes bare
                # (`items[i] = items[0]`, any admitted container -- the
                # `std::optional<T>` element copies whole, no lift). Other value sources (scalars,
                # narrowed reads) stay deferred until witnessed.
                v = stmt.value
                _vt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(v))))
                    if analyzer.get_expr_type(v) is not None else None)
                if (isinstance(v, TpySubscript)
                        and isinstance(v.obj, TpyName)
                        and not isinstance(v.index, TpySlice)
                        and not v.needs_optional_runtime_check
                        and _vt == eu):
                    value = _lower_expr(v, lc, declared,
                                        allow_whole_optional=True)
                    _witness("setitem.optval_elem_copy")
                elif (not isinstance(v, TpyNoneLiteral)
                      and _value_opt_scalar_elem_arg(v, eu, analyzer)):
                    # A SCALAR source into the optional element slot
                    # (`xs[0] = 5` on `list[int32 | None]`): the scalar
                    # renders bare and std::optional's converting ctor
                    # wraps it -- the same row the append/insert arg table
                    # already admits for this slot.
                    value = _lower_expr(v, lc, declared)
                    _witness("setitem.optval_scalar")
                elif not isinstance(v, TpyNoneLiteral):
                    note_detail("setitem.optval_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                else:
                    value = THIRLiteral(result_type=eu, value=None,
                                        form=Form.STORAGE, loc=loc)
                    _witness("setitem.optval_none")
            elif _value_opt_owned_view(eu, analyzer) is not None:
                # Value-repr Optional[str/bytes] value slot: the WHOLE
                # optional stores bare -- `None` as the STORAGE nullopt,
                # a same-type un-narrowed NAME as the plain copy
                # (`::tpy::__setitem__(out, "present", a)`). Narrowed and
                # other sources reject until witnessed.
                v = stmt.value
                if isinstance(v, TpyNoneLiteral):
                    value = THIRLiteral(result_type=eu, value=None,
                                        form=Form.STORAGE, loc=loc)
                elif (isinstance(v, TpyName) and v.name in declared
                      and not lc.binding(v.name).pointer
                      # Occurrence-typed: a NARROWED read renders the
                      # deref-spelled condition (`*a ? ...`), not lowered.
                      and isinstance(unwrap_readonly(unwrap_send_sync(
                          analyzer.get_expr_type(v) or elem_t)),
                          OptionalType)
                      and _opt_view_arg_shim(declared[v.name], elem_t,
                                             analyzer)):
                    # The view-inner binding rebuilds into the owned-inner
                    # value slot (`std::move(a ? std::make_optional(
                    # std::string(*a)) : std::nullopt)`) -- the ARG split
                    # with the consuming position's move wrap.
                    value = THIROptViewArg(result_type=elem_t, name=v.name,
                                           form=Form.VALUE, moved=True,
                                           loc=loc)
                elif _opt_strview_to_str_own_elem_arg(
                        v, elem_t, declared, analyzer):
                    # The opposite direction (a VIEW-inner source into the
                    # owned value slot): the coercion lambda's `__ov`
                    # statement expression, no move wrap -- the same render
                    # the container-insert arg row spells.
                    inner = _lower_expr(v.expr, lc, declared,
                                        allow_whole_optional=True)
                    if isinstance(inner, THIRName):
                        inner = replace(inner, deref=False)
                    value = THIRCoerce(
                        result_type=v.expected_type, expr=inner,
                        coercion_name=v.coercion.name,
                        wrap=("({{ auto __ov = ({0}); __ov ? "
                              "std::make_optional(std::string(*__ov)) : "
                              "std::nullopt; }})"),
                        form=Form.VALUE, loc=loc)
                    _witness("arg.opt_strview_own_shim")
                else:
                    note_detail("setitem.optview_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("setitem.optview_whole")
            elif is_void_like_type(eu):
                # UNIT value slot (`d["a"] = None` on `dict[str, None]`):
                # the element is `std::monostate`, and `None` is the only
                # source sema admits -- the STORAGE literal renders
                # `std::monostate{}`, stored bare by the checked setitem.
                if not isinstance(stmt.value, TpyNoneLiteral):
                    note_detail("setitem.unit_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = THIRLiteral(result_type=eu, value=None,
                                    form=Form.STORAGE, loc=loc)
                _witness("setitem.unit_none")
            elif _eligible_wrapper_union(eu, analyzer) is not None:
                # Recursive-union WRAPPER value slot: a scalar/str literal
                # constructs the wrapper via its converting ctor -- the bare
                # token (the `_ru_elem_ok` leaf slice). None (the monostate
                # spelling) and nested container literals reject until
                # witnessed.
                v = stmt.value
                if (isinstance(v, (TpyNoneLiteral, TpyArrayLiteral,
                                   TpyDictLiteral))
                        or not _ru_elem_ok(v, analyzer)):
                    note_detail("setitem.ru_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = _lower_expr(v, lc, declared)
                if _node_moves(value):
                    # A movable wrapper NAME is an insert position like any
                    # other container element: its last use moves, so a
                    # bare render would silently copy.
                    value = THIRMove(result_type=value.result_type,
                                     value=value, form=Form.STORAGE, loc=loc)
                    _witness("setitem.ru_move")
                _witness("setitem.ru_scalar")
            elif (isinstance(eu, TupleType)
                    and (eu.has_pointer_repr_element()
                         or _nested_storage_tuple(eu, analyzer) is not None)):
                # A tuple element slot holding a reference somewhere: an
                # OWNING place, so the value takes the field write's storage
                # conversion -- a literal is built into storage element by
                # element, any other source is lowered once and
                # rendered off its node (a storage read stores bare, a
                # borrow-form name or call lifts, an owned name at its last
                # use moves). The store COPIES what it borrows; sema warns
                # per element.
                slot_use = field_slot_use(eu, SlotConstruct.ASSIGN,
                                          analyzer, lc.placement)
                if isinstance(stmt.value, TpyTupleLiteral):
                    value = _field_write._storage_tuple_literal(
                        stmt.value, eu, lc, declared,
                        stmt_reject_reason(stmt))
                else:
                    value = _field_write._storage_source(
                        stmt, slot_use, lc, declared, loc)
                _witness("setitem.tuple_storage")
            elif (isinstance(eu, TupleType)
                    and _value_tuple(eu, analyzer) is not None):
                # VALUE tuple value slot (`self._store[lk] = (key, value)` on
                # `dict[str, tuple[str, str]]`): borrow and storage coincide,
                # so the spelled brace-init stores directly with no lift and
                # each element carries its own view->owned copy -- the same
                # render the value-tuple FIELD write and decl slots spell.
                # A ref-element literal needs the borrow ladder instead and
                # stays out, as do non-literal sources.
                if not (isinstance(stmt.value, TpyTupleLiteral)
                        and len(stmt.value.elements) == len(eu.element_types)
                        and not _tuple_literal_has_ref_elements(stmt.value,
                                                                eu)):
                    note_detail("setitem.value_tuple_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = _lower_tuple_literal(stmt.value, eu, lc, declared)
                _witness("setitem.value_tuple_literal")
            elif _eligible_ptr_union(eu, analyzer) is not None:
                # Value-variant union element: a same-union borrow NAME lifts
                # via `::tpy::to_value_variant<...>` (copy); a NARROWED member
                # name is already the concrete alternative and constructs the
                # element directly (bare -- the lift would be ill-formed).
                v = stmt.value
                if not (isinstance(v, TpyName) and v.name in declared
                        and not lc.binding(v.name).pointer):
                    note_detail("setitem.union_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                lowered = _lower_expr(v, lc, declared)
                if _node_moves(lowered):
                    note_detail("setitem.union_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if v.name in lc.narrow.narrowed or v.name in lc.inline_narrowed:
                    value = lowered
                else:
                    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        declared[v.name])))
                    if bt != eu:
                        note_detail("setitem.union_value_shape")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    value = THIRFormConvert(result_type=eu, value=lowered,
                                            form=Form.STORAGE, loc=loc)
                _witness("setitem.borrow_lift")
            elif record_like(eu, analyzer):
                # F1-record element/value slot, the checked `__setitem__`
                # forwarding the value bare. Three vetted sources: a record
                # RVALUE of the slot's own type -- or its covariant-generic
                # upcast (`Box(conn)` into a `dict[str, Box[Proto]]` value;
                # sema's `is_covariant_generic_upcast`, the converting move)
                # -- rendered with its OWN inferred type at this flushable
                # statement position; `copy(name)` as the copy-construct
                # rvalue (`T(x)`, the bare-record copy arm); a plain
                # record NAME copied bare / moved at a movable name's last
                # use, exact-type like the field-write twin. Other sources
                # reject.
                v = stmt.value
                vt = analyzer.get_expr_type(v)
                vtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
                       if vt is not None else None)
                if isinstance(vtu, OwnType):
                    vtu = unwrap_readonly(vtu.wrapped)
                copy_row = _lower_copy_record(v, lc, declared, slot_type=eu,
                                              loc=loc)
                if copy_row is not None:
                    value = copy_row
                    _witness("setitem.record_copy")
                elif (isinstance(v, TpyName) and v.name in declared
                      and not lc.binding(v.name).pointer
                      and v.name not in lc.narrow.narrowed
                      and not (lc.prescan.has_self and v.name == "self")
                      and vtu == eu):
                    value = _lower_expr(v, lc, declared)
                    if _node_moves(value):
                        value = THIRFormConvert(result_type=eu, value=value,
                                                form=Form.STORAGE, move=True,
                                                loc=loc)
                    _witness("setitem.record_name")
                elif (_record_rvalue_source_shape(v, analyzer)
                      and vtu is not None
                      and (vtu == eu
                           or _covariant_record_upcast_ok(vtu, eu, analyzer))):
                    value = _flush_witness(
                        "flush.assign",
                        _lower_expr(v, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True)))
                    _witness("setitem.record_rvalue")
                elif (isinstance(v, TpyMethodCall)
                      and is_rvalue_source(analyzer, v)
                      and vtu is not None and vtu == eu):
                    # A record-returning METHOD-call rvalue value
                    # (`grown["x"] = a.clone()` -- the insert_or_assign(V&&)
                    # move path): the bare call forwards into the value
                    # slot; BORROW_BIND is the record-return admission
                    # (like the container-literal element row).
                    value = _flush_witness(
                        "flush.assign",
                        _lower_expr(v, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=True)))
                    _witness("setitem.record_method_rvalue")
                elif ((isinstance(v, TpyCall)
                       or _declared_call_at_owning_elem(v, analyzer))
                      and v.resolved_function_info is not None
                      and call_returns_cpp_ref(analyzer,
                                               v.resolved_function_info)
                      and vtu is not None and vtu == eu):
                    # A borrow-returning call value copies into the element
                    # (`::tpy::__setitem__(pts, 0, identity(..));` -- the
                    # checked setitem's V parameter absorbs the `T&`); a
                    # borrow-declared method call is the same copy.
                    value = _flush_witness(
                        "flush.assign",
                        _lower_expr(v, lc, declared,
                                    use=_ExprUse(
                                        pos=SinkPos.SETITEM_VALUE,
                                        forms=_ONLY_RECORD_COPY,
                                        allow_temps=True)))
                    _witness("setitem.borrow_call_copy")
                else:
                    note_detail("setitem.record_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            else:
                value = _slot_literal_retype(
                    _flush_witness("flush.assign",
                                   _lower_expr(
                                       stmt.value, lc, declared,
                                       use=_ExprUse(
                                           result=_ExprResultUse.STORAGE,
                                           allow_temps=True),
                                       # A view-family FIELD source renders
                                       # the bare member read; the owned
                                       # copy below is this sink's own wrap.
                                       field_owned_str_ok=isinstance(
                                           stmt.value, TpyFieldAccess))),
                    elem_t, lc)
                if (_is_any_type(eu) and isinstance(stmt.value, TpyCoerce)
                        and stmt.value.coercion.name == "into_any"
                        # A `copy(name)` source lowers to the same coerced
                        # name node but copies; only a bare name moves.
                        and isinstance(_peel_coerce(stmt.value), TpyName)
                        and _node_moves(value)):
                    # The move peels the coerce: a movable inner name's
                    # last use moves the whole make_any value
                    # (`std::move(::tpy::make_any(n))` -- Any is not
                    # trivially destructible, so the move is not inert).
                    value = THIRMove(result_type=eu, value=value,
                                     form=Form.STORAGE, loc=loc)
                elif (not _owned_copy_sink(value, elem_t, analyzer)
                      and isinstance(stmt.value, TpyName)
                      and _node_moves(value)):
                    # The move at the element sink: a movable local's last
                    # use is stolen, not copied. The owned-copy sink below
                    # is the exception at this same position -- there the
                    # operand is a trivially-copyable VIEW, not the binding
                    # the name reads, so moving it would both be inert and
                    # name the wrong object. The verdict must not even be
                    # ASKED there.
                    value = THIRMove(result_type=value.result_type, value=value,
                                     form=value.form, loc=loc)
                elem_str = _resolved_str_value(elem_t, analyzer)
                if (value.form is Form.BORROW and elem_str is not None
                        and is_str_type(elem_str)):
                    _witness("setitem.str_owned_copy")
                    value = THIRFormConvert(result_type=elem_str, value=value,
                                            form=Form.STORAGE, loc=loc)
                elem_bytes = _resolved_bytes_value(elem_t, analyzer)
                if (value.form is Form.BORROW and elem_bytes is not None
                        and is_bytes_type(elem_bytes)):
                    # The bytes twin of the owned-str copy: the view-form
                    # source materializes via `::tpy::Bytes(...)`.
                    _witness("setitem.bytes_owned_copy")
                    value = THIRFormConvert(result_type=elem_bytes,
                                            value=value,
                                            form=Form.STORAGE, loc=loc)
            _witness("setitem.bounds_safe" if target.bounds_safe
                     else "setitem.checked")
            if isinstance(stmt.target.obj, TpyFieldAccess):
                _witness("setitem.field_recv")
            return _store_item(stmt, target, value, lc, loc)
        # Name-target assign: the same self-append peephole as the var-decl
        # reassignment -- it applies at both sites.
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            rhs = _str_self_append_rhs(stmt.target.name, stmt.value)
            if rhs is not None:
                return THIRStrAppend(target=stmt.target.name,
                                     value=_lower_expr(rhs, lc, declared), loc=loc)
        # A REGISTERED value-opt NAME target is a whole-BINDING write
        # (`color = __color_2;` -- the macro-generated raw-assign shape;
        # user code reaches the same binding via the var-decl reassign arm,
        # which never lowers the target as an expression), so the
        # unproven-read fence does not apply to it. Kind-blind (SCALAR/
        # VIEW/RECORD) on purpose: the write consumes the whole optional
        # for every kind, and a NARROWED target never reaches here (the
        # TpyAssign entry guard falls the statement back first).
        # `p = None` at a `Ptr[T]` slot, raw-assign flavor: the var-decl
        # arm's `decl.ptr_none` row, which the generic tail below cannot
        # reach (`_lower_expr` has no bare-None render). The decl arm's
        # dyn-protocol exclusion is about the DECL spelling (`auto` vs
        # `T*`), so it does not apply to a pure write.
        if (isinstance(stmt.value, TpyNoneLiteral)
                and isinstance(stmt.target, TpyName)
                and _eligible_ptr_value(declared.get(stmt.target.name),
                                        analyzer)):
            _witness("assign.ptr_none")
            return THIRAssign(
                target=_lower_expr(stmt.target, lc, declared),
                value=THIRLiteral(
                    result_type=declared[stmt.target.name], value=None,
                    form=Form.VALUE, loc=loc),
                loc=loc)
        tgt_whole_opt = (
            isinstance(stmt.target, TpyName)
            and (lc.binding(stmt.target.name).value_opt is not None
                 # The value-repr Optional[value TUPLE] binding carries no
                 # registered kind (its reads ride the binding-type
                 # predicate), and a NAME write consumes it whole just the
                 # same -- so the unproven-READ fence must not see it here.
                 or _value_opt_tuple(declared.get(stmt.target.name),
                                     analyzer) is not None)
            and _witness("assign.value_opt_target"))
        return THIRAssign(
            target=_lower_expr(stmt.target, lc, declared,
                               allow_whole_optional=tgt_whole_opt),
            value=_slot_literal_retype(
                _flush_witness("flush.assign",
                               _lower_expr(
                                   stmt.value, lc, declared,
                                   use=_ExprUse(
                                       result=_ExprResultUse.STORAGE,
                                       allow_temps=True))),
                analyzer.get_expr_type(stmt.target), lc),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        if isinstance(stmt.target, TpyName):
            # An augmented write clears the flow-sensitive literal fact.
            lc.literal_facts.pop(stmt.target.name, None)
        narrowed = lc.narrow.narrowed.keys()
        if (isinstance(stmt.target, TpyName)
                and stmt.target.name in narrowed):
            raise ThirUnsupported("stmt.aug_assign")
        if isinstance(stmt.target, TpySubscript):
            # A typed-dict subscript target is a FIELD lvalue: it rides the
            # generic `target = (target OP value)` tail as a THIRAssign
            # (`user.age = ::tpy::add_check<int32_t>(user.age, 1);`),
            # skipping the checked-subscript special arms below.
            aug_key_keep = aug_key_retention(stmt, analyzer)
            aug_ok = (_typed_dict_write_target(
                          stmt.target, declared,
                          scope.admission_pointers(), narrowed, analyzer)
                      or _container_aug_setitem_ok(
                          stmt, declared, scope.admission_pointers(),
                          narrowed, analyzer, key_keep=aug_key_keep))
            if aug_ok:
                # Unreachable today (a wrapper union's elements are never
                # the scalar family the aug gate requires), but the guard
                # must stay symmetric with the plain-write path.
                _reject_const_narrowed_write_recv(stmt.target.obj, lc, stmt)
        else:
            aug_ok = (_scalar_aug_assign_ok(stmt, declared, analyzer,
                                            lc.names_with("pointer"))
                      or (_record_aug_binop_ok(
                          stmt, declared, lc.names_with("pointer"), narrowed, analyzer)
                          and _witness("aug.record_binop"))
                      or _class_const_aug_assign_ok(
                          stmt, declared, lc.names_with("pointer"), analyzer)
                      or _str_aug_append_ok(
                          stmt, declared, lc.prescan, analyzer)
                      or _bytes_aug_concat_ok(
                          stmt, declared, lc.prescan, analyzer))
        if not aug_ok and stmt.resolved_inplace is not None:
            # An IN-PLACE dunder aug-assign: a call over the resolved
            # in-place method through THIRMethodCall's three arms -- a
            # @native free function renders `<native>(c, v)`
            # (`::tpy::list_extend(xs, v)`, `::tpy::bytearray_iadd(ba, v)`,
            # `::tpy::set_update(s, v)`), a method `c.__iadd__(v)`. A NAME
            # (a pointer-form one is written through) or a FIELD target; the
            # field arm's own receiver gates apply during lowering.
            _inp_fi = stmt.resolved_inplace.method
            _inp_target_ok = (
                (isinstance(stmt.target, TpyName)
                 and stmt.target.name in declared)
                or (isinstance(stmt.target, TpyFieldAccess)
                    and _field_markers_clean(stmt.target)))
            if _inp_target_ok and _inp_fi is not None and _inp_fi.params:
                _witness("aug.inplace_dunder")
                recv = _deref_loop_source(_lower_expr(
                    stmt.target, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER)), lc)
                # The operand is the explicit `c.__iadd__(v)` call's
                # argument, converted to what the dunder's parameter takes.
                val = _dunder_arg(stmt.target, [stmt.value], 0, _inp_fi, lc,
                                  declared, temp_args=True,
                                  keep=arg_retention(_inp_fi, 0, None,
                                                     lc.analyzer, stmt.value,
                                                     recv=stmt.target))
                _inp_bc = _bound_call_of(_inp_fi, _inp_fi.name)
                recv, _ = _operator_receiver_pinned(stmt.resolved_inplace,
                                                    recv, val, lc)
                return THIRExprStmt(
                    expr=with_method_callee(with_method_stub(THIRMethodCall(
                        result_type=VoidType(),
                        receiver=recv,
                        method_cpp=_inp_bc.method_cpp,
                        native_function_name=_inp_bc.native_function_name,
                        cpp_template=_inp_bc.cpp_template,
                        args=(val,),
                        loc=loc), _inp_fi, analyzer), _inp_fi, analyzer,
                        lambda: receiver_is_const(stmt.target, lc)),
                    loc=loc)
        if not aug_ok:
            raise ThirUnsupported("stmt.aug_assign")
        # str `t += v`: the in-place append (the string branch of the
        # resolved-binop arm), not the synthetic binop below. The target-type
        # dispatch follows `_str_aug_append_ok`'s admission. A str-FIELD target
        # (`recv.field += v`) carries its lowered lvalue on target_expr.
        if _owned_str_append_target(
                analyzer.get_expr_type(stmt.target), analyzer):
            if isinstance(stmt.target, TpyName):
                return THIRStrAppend(
                    target=stmt.target.name,
                    # `s += <v>`: the append copies the source's bytes inside
                    # the statement -- the OPERAND row, like the list extend.
                    value=_lower_expr(stmt.value, lc, declared,
                                      use=_ExprUse(pos=SinkPos.OPERAND,
                                                   forms=_LEND_OK)),
                    loc=loc)
            if isinstance(stmt.target, TpyFieldAccess):
                return THIRStrAppend(
                    target="",
                    target_expr=_lower_expr(
                        stmt.target, lc, declared,
                        field_prechecked=True),
                    value=_lower_expr(stmt.value, lc, declared,
                                      use=_ExprUse(pos=SinkPos.OPERAND,
                                                   forms=_LEND_OK)),
                    loc=loc)
        # `target OP= value` lowers to `target = (target OP value)` -- the
        # scalar branch -- and so does the bytes concat-and-assign
        # (`t = ::tpy::bytes_concat(t, v);`, the same resolved-binop arm with
        # the native emit, likewise unwrapped). The target expr is lowered
        # twice (once as the assign lvalue, once as the binop's left operand):
        # the same target string fills both slots.
        # `divisor_non_zero=False`: an aug-assign never swaps the checked
        # div/mod helper (no source `TpyBinOp` node carries the flag).
        # A bytes target's binding is a PendingBytesType; resolve it (and tag
        # the owned concat result STORAGE) so the nodes carry final types.
        # The target parts whose evaluation is observable when repeated
        # are evaluated once, ahead of the store (`hold_parts`).
        with _aug_holds(stmt, lc, declared, loc) as held:
            tgt_type = analyzer.get_expr_type(stmt.target)
            tgt_type = _resolve_pending_view(tgt_type, analyzer) or tgt_type
            tgt_bytes = _resolved_bytes_value(tgt_type, analyzer)
            target_prechecked = isinstance(stmt.target, TpyFieldAccess)
            recv_eval = recv_wrap = None
            if (isinstance(stmt.target, TpyFieldAccess)
                    and stmt.target.class_constant_owner is not None):
                # Class-constant lvalue: the receiver eval splits off ONCE; the
                # bare qualified name lands on both sides of the synthetic
                # `target = (target OP value)` -- the same lvalue string fills
                # both slots.
                target, recv_eval, recv_wrap = _lower_class_const_write_target(
                    stmt.target, lc, declared, loc)
                left: THIRExpr = target
                _witness("aug.class_const")
            elif (isinstance(stmt.target, TpySubscript)
                    and stmt.target.typed_dict_field is None):
                aug_sub = _aug_subscript_operands(stmt, lc, declared,
                                                  key_keep=aug_key_keep)
                left = target = aug_sub.read
            else:
                target = _lower_expr(
                    stmt.target, lc, declared,
                    field_prechecked=target_prechecked)
                left = _lower_expr(
                    stmt.target, lc, declared,
                    field_prechecked=target_prechecked)
            cast_t = analyzer.get_expr_type(stmt.target)
            if (isinstance(stmt.target, TpySubscript)
                    and stmt.target.typed_dict_field is None):
                # The subscript read-modify-write pair always renders the CHECKED
                # dunders -- a subscript aug-assign never takes the bounds-safe
                # operator[] -- so the node fact is forced off on both reads.
                # The cast keys on the RESOLVED element scalar (a
                # literal-seeded local's element read is still an IntLiteralType
                # here).
                target = replace(target, bounds_safe=False)
                left = replace(left, bounds_safe=False)
                if cast_t is not None:
                    cast_t = resolve_int_literals(
                        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(cast_t))),
                        analyzer.ctx.default_int_for_literal)
            _, aug_rslot = _rb_operand_slots(stmt.resolved_binop)
            # The subscript-write route flushes arg temps before its single
            # setitem line (the pre-statement flush point), so the value
            # position admits the temp rows there -- `d[i] += k.take(b)` hoists
            # the Own-slot copy `auto __tmp_N = b;` ahead of the full expression.
            # A NAME-target aug is the same flushable statement (`total +=
            # take_mut(b)` hoists the vararg pack's std::array ahead of the
            # assign).
            aug_value_use = _operand_use_of(
                stmt, stmt.value, analyzer,
                allow_temps=((isinstance(stmt.target, TpySubscript)
                              and stmt.target.typed_dict_field is None)
                             or isinstance(stmt.target, TpyName)))
            _, aug_pin = _operator_literal_pins(
                stmt.resolved_binop, None, stmt.value, lc, declared)
            _, aug_rview = _rb_operand_view_slots(stmt.resolved_binop)
            aug_right = aug_pin or _slot_literal_retype(
                _lower_expr(stmt.value, lc, declared,
                            use=replace(aug_value_use, slot_target=aug_rview)),
                aug_rslot, lc)
            left, aug_right = _operator_receiver_pinned(stmt.resolved_binop,
                                                        left, aug_right, lc)
            binop = THIRBinOp(
                result_type=tgt_type,
                left=left,
                op=stmt.op,
                right=aug_right,
                resolved=stmt.resolved_binop,
                paren_wrap=False,
                form=(Form.STORAGE if tgt_bytes is not None
                      and is_bytes_type(tgt_bytes) else Form.VALUE),
                loc=loc,
            )
            # The store receives the result under the conversion sema put
            # on its stand-in, where it has one.
            stored = (binop if stmt.stored_value is None
                      else _received_value(stmt.stored_value, binop, lc,
                                           declared))
            if (isinstance(stmt.target, TpySubscript)
                    and stmt.target.typed_dict_field is None):
                _witness("setitem.aug")
                if isinstance(stmt.target.obj, TpyFieldAccess):
                    _witness("setitem.field_recv")
                if stmt.target.setitem_index is not None:
                    store_key = aug_sub.store_index
                    received = stored
                    if _setter_consumes_held_key(stmt, store_key):
                        # Moving the key inside the call that also reads it
                        # for the getter would leave the getter an empty key
                        # (argument order is unspecified): the update is
                        # held first, then both move into the setter.
                        _witness("aug.held_key_moved")
                        update, update_read = _held_temp(received, "auto&&",
                                                         lc, loc)
                        held = (*held, update)
                        store_key = THIRMove(result_type=store_key.result_type,
                                             value=store_key,
                                             form=store_key.form, loc=loc)
                        received = THIRMove(
                            result_type=update_read.result_type,
                            value=update_read, form=update_read.form, loc=loc)
                    target = replace(target, receiver=aug_sub.receiver,
                                     index=store_key)
                    return _store_item(stmt, target, received, lc, loc,
                                       held=held)
                target = replace(target, receiver=aug_sub.receiver,
                                 index=aug_sub.store_index)
                return _store_item(stmt, target, stored, lc, loc, held=held)
            return THIRAssign(target=target, value=stored,
                              recv_eval=recv_eval, recv_wrap=recv_wrap,
                              held=held, loc=loc)
    if isinstance(stmt, TpyReturn):
        begin_stmt()
        if lc.overload_stub_return is not None:
            # Sema analyzes an `@overload` body once, against the impl's
            # return; each stub's specialization exists only while lowering.
            stmt = _overload_adjusted_return(stmt, lc)
        # Inside a finally / `with` region the result lands in a temporary
        # before the region's exits run, and is returned from there.
        staged = lc.exit_staged
        if not _return_carries_value(stmt, lc):
            _witness("ret.void")
            return THIRReturn(staged=staged, value=None, loc=loc)
        ret_slot = return_slot(lc, implicit_move=not staged)
        if (isinstance(stmt.value, TpyFieldAccess)
                and stmt.value.consuming_move):
            # A consuming method's `self` is an rvalue ref (`&&`), so returning
            # one of its fields MOVES (`return std::move(this->_value);`) --
            # where sema let the field move (`consuming_move`); anywhere else
            # the return reads it like any method's field.
            # Intercepted BEFORE the return ladder on purpose: the move
            # belongs to the FINAL return expression, so a specialized arm
            # (record / optional / tuple / container) would have to re-apply
            # it and a missed one drops the move silently.
            #
            # TWO axes must both be plain for the interception to be a no-op,
            # and they are NOT the same axis. The FIELD type decides whether the
            # bare read is the whole render; the RETURN SLOT decides whether
            # the ladder even REACHES the move (a pointer-repr / value
            # Optional slot, a ptr-variant union, and a property getter all
            # exit early and never see it). Gating on the field alone would
            # ADD a move where none belongs: `def take(self: Own[Self]) ->
            # int32 | None: return self.x` is exactly that pair, and no
            # corpus case has it.
            fld_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                analyzer.get_expr_type(stmt.value))))
            ret_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                _fn_return_type(lc))))

            def _plain_value(t) -> bool:
                # An open-T slot joins the plain families: a TypeParamRef is
                # none of the shapes the ladder exits early on, so it
                # reaches the move like any scalar.
                return bool(_eligible_scalar(t) or _eligible_char(t)
                            or _eligible_enum(t, analyzer) is not None
                            or _is_type_param_slot(t))

            # The RETURN axis additionally admits `Own[T]` (`own_return_t<T>`)
            # -- an auto_own consuming clone's slot, whose whole point is that
            # the field moves out (`return std::move(this->first_val);`). The
            # FIELD axis does not, but only defensively: sema rejects an
            # `Own[T]` FIELD outright ("a field owns its value inline"), so
            # that shape cannot reach here at all. Keeping the axes separate
            # documents which one the `Own` belongs to rather than guarding a
            # reachable case.
            # A BUILTIN-record field moving into its own Own[same-type]
            # return (`_slot: Own[Poll[int32]]` -> `-> Own[Poll[int32]]`):
            # the identity pair guarantees the bare
            # `return std::move(this->_slot);` -- container/str families
            # excluded (their consuming renders differ).
            _csf_pair = (
                isinstance(fld_t, NominalType)
                and not fld_t.is_protocol and not fld_t.is_user_record
                and not (is_list(fld_t) or is_dict(fld_t) or is_set(fld_t)
                         or is_array(fld_t))
                and _resolved_str_value(fld_t, analyzer) is None
                and _resolved_bytes_value(fld_t, analyzer) is None
                and fld_t == _unwrap_own(ret_t))
            if not ((_plain_value(fld_t)
                     and (_plain_value(ret_t) or _own_type_param_slot(ret_t)))
                    or _csf_pair):
                note_detail("return.consuming_self_field")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            src = _lower_field_source(stmt.value, lc, declared)
            _witness("ret.consuming_self_field")
            return THIRReturn(
                value=THIRMove(result_type=src.result_type, value=src,
                               form=src.form, loc=loc),
                staged=staged, loc=loc)
        if (stmt.finally_deferred_capture and stmt.finally_deferred_leaves
                and not deferred_tuple_eager):
            return _lower_deferred_tuple_return(stmt, scope, lc, declared,
                                                loc)
        if stmt.finally_deferred_capture and not stmt.finally_deferred_leaves:
            deferred = _finally_deferred_recipe(stmt, lc, declared, loc)
            if deferred is None:
                # Stamped but outside the two recipes (a rename layer on the
                # name, a slot shape the table does not cover): reject
                # rather than retract the move mark at emit time, which
                # would mutate `all_last_uses`.
                note_detail("return.finally_deferred_capture")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("ret.finally_deferred")
            return deferred
        if stmt.value is not None and lc.error_return_cpp is not None:
            rfi = _error_return_stmt_fi(stmt.value, analyzer)
            if rfi is not None:
                # Returning an @error_return call from an @error_return
                # function passes the std::expected through directly -- no
                # unwrap+rewrap. That arm sits past the Optional /
                # ptr-variant / property-getter returns, so gate it the
                # same way.
                if not isinstance(stmt.value, (TpyCall, TpyMethodCall)):
                    note_detail("error_return.ret_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _reject_nested_error_return_arg(stmt, stmt.value, analyzer)
                rtb = (unwrap_readonly(unwrap_ref_type(_fn_return_type(lc)))
                       if _fn_return_type(lc) is not None else None)
                if (lc.func.is_property_getter
                        or isinstance(rtb, OptionalType)
                        or (rtb is not None and is_ptr_variant_union(rtb))):
                    note_detail("error_return.ret_slot")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("er.return_passthrough")
                return THIRReturn(
                    value=_flush_witness(
                        "flush.return",
                        _lower_expr(stmt.value, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True),
                                    error_return_raw=True)),
                    staged=staged, loc=loc)
        return THIRReturn(
            staged=staged,
            value=result_value(stmt.value, ret_slot, lc, declared, scope,
                               stmt),
            loc=loc)
    if isinstance(stmt, TpyIf):
        begin_stmt()
        folded = _lower_overload_folded_if(
            stmt, lc, declared, in_branch=scope.in_branch,
            loop_depth=scope.loop_depth)
        if folded is not None:
            return folded
        cx = _protocol_constexpr_info(stmt.condition)
        if cx is not None:
            return _lower_constexpr_if(stmt, cx, lc, declared, loc,
                                       loop_depth=scope.loop_depth)
        npg = _nullproto_guard_condition(stmt.condition, lc)
        if npg is not None:
            npg_var, npg_swappable = npg
            if not npg_swappable:
                # A @dynamic-member flavor keeps the runtime compare, which
                # is not lowered for is-not-None.
                note_detail("if.constexpr_nullproto_guard")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_nullproto_guard_if(stmt, npg_var, lc, declared,
                                             loc,
                                             loop_depth=scope.loop_depth)
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        minfo = (None if info is not None
                 else _multi_narrow_cond_info(stmt.condition, declared,
                                              analyzer))
        oinfo = (None if info is not None or minfo is not None
                 else _or_chain_narrow_info(stmt.condition, declared,
                                            analyzer))
        ainfo = (None if info is not None or minfo is not None
                 or oinfo is not None
                 else _any_narrow_cond_info(stmt.condition, declared,
                                            analyzer))
        finfo = (None if info is not None or minfo is not None
                 or oinfo is not None or ainfo is not None
                 else _folded_narrow_info(stmt.condition, lc))
        hoists = analyzer.if_branch_decls.get(stmt, {})
        if hoists and (info is not None or minfo is not None
                       or oinfo is not None or ainfo is not None
                       or finfo is not None):
            note_detail("if.narrow_hoist")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Classification registers the hoisted names' read/write model on
        # `lc` and enters them in `declared` BEFORE the condition lowers
        # (the declaration order the reads need); a reject anywhere after a
        # partial registration is fine -- the whole body raises and the
        # partially-mutated lc is discarded with it.
        hoisted_bindings: list[THIRHoistedBinding] = []
        hoist_decls, hoist_slots = _lower_if_hoist_predecls(
            stmt, hoists, declared, lc, scope.in_branch, hoisted_bindings)
        # Per-branch narrowing facts for a condition NO narrowing arm claims
        # (set in the plain-condition arm below; every arm that returns from
        # here on emits its own extraction).
        then_fact = None
        else_fact = None
        if info is not None:
            var, u, _members, folded, _isin = info
            # A non-member ELSE fact (the remaining NULLABLE union, e.g.
            # `B | None` on an `A | B | None` subject) is inert when no
            # else branch exists: no else extraction runs, and the post-if
            # arms take concrete members only (`_narrow_fact_member`).
            # With a genuine else BODY the extraction arm does run on the
            # fact, so that shape keeps rejecting.
            else_facts_ok = _narrow_facts_ok(u, stmt.else_type_facts, var)
            if not else_facts_ok and not stmt.else_body:
                _witness("if.narrow_nc_else_fact")
                else_facts_ok = True
            if not else_facts_ok and _elif_link(stmt) is not None:
                # An elif continuation is the same inert case one link along:
                # an elif continuation skips the else extraction
                # unconditionally (concreteness only picks flat-vs-nested,
                # which `_lower_narrow_if_shape` handles), and the link seeds
                # its own facts. So no consumer of THIS level's else fact
                # exists whatever its shape.
                _witness("if.narrow_elif_else_fact")
                else_facts_ok = True
            narrow_ok = not (
                (var in lc.narrow.narrowed and not folded)
                or bool(hoists)
                or not _narrow_facts_ok(u, stmt.then_type_facts, var)
                or not else_facts_ok)
            if not narrow_ok:
                note_detail("if.narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if folded and stmt.else_body:
                # Sema's exhaustiveness fold with an EXPLICIT else: the
                # `true` condition over the normal chain skeleton; the
                # dead else arm still extracts its (excluded) member from
                # the else facts.
                _witness("if.narrow_folded_else")
            return _lower_narrow_if(stmt, info, lc, declared, loc,
                                    loop_depth=scope.loop_depth)
        if minfo is not None:
            # Tight slice: a concrete then-fact per subject, facts on no
            # OTHER name, no else arm at all (a conjunction's negation
            # narrows nothing, so an else body / else facts have no
            # extraction here), and no subject already narrowed.
            multi_ok = not (
                bool(hoists)
                or bool(stmt.else_body)
                or bool(stmt.else_type_facts)
                or any(var in lc.narrow.narrowed for var, *_ in minfo)
                or any(_narrow_fact_member(u, stmt.then_type_facts,
                                           var) is None
                       for var, u, _m, _l in minfo)
                or any(k not in {var for var, *_ in minfo}
                       for k in stmt.then_type_facts))
            if not multi_ok:
                note_detail("if.multi_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_multi_narrow_if(stmt, minfo, lc, declared, loc,
                                          loop_depth=scope.loop_depth)
        if oinfo is not None:
            ovar, ou, _oleaves, _oneg = oinfo
            or_ok = not (
                bool(hoists)
                or ovar in lc.narrow.narrowed
                or not _narrow_facts_ok(ou, stmt.then_type_facts, ovar)
                or not _narrow_facts_ok(ou, stmt.else_type_facts, ovar))
            if not or_ok:
                note_detail("if.or_chain_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_or_chain_if(stmt, oinfo, lc, declared, loc,
                                      loop_depth=scope.loop_depth)
        if ainfo is not None:
            avar, amembers, _negated, _aisin = ainfo
            any_ok = not (
                avar in lc.narrow.narrowed
                or not _any_narrow_facts_ok(amembers, stmt.then_type_facts,
                                            avar)
                or not _any_narrow_facts_ok(amembers, stmt.else_type_facts,
                                            avar))
            if not any_ok:
                note_detail("if.any_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_any_narrow_if(stmt, ainfo, lc, declared, loc,
                                        loop_depth=scope.loop_depth)
        if finfo is not None:
            fvar, fu0, _fmembers, _fvalue = finfo
            fold_ok = not (
                bool(stmt.else_body)
                or not _narrow_facts_ok(fu0, stmt.then_type_facts, fvar)
                or not _narrow_facts_ok(fu0, stmt.else_type_facts, fvar)
                or _body_writes_name(stmt.then_body, fvar))
            if not fold_ok:
                note_detail("if.folded_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_folded_narrow_if(stmt, finfo, lc, declared, loc,
                                           loop_depth=scope.loop_depth)
        pinfo = _poly_narrow_info(stmt.condition, declared, analyzer)
        if pinfo is not None:
            pvar, pmember, _pdecl = pinfo
            # Mirror _build_isinstance_init_clause's constraints: a bare
            # (non-pointer, non-self, non-global) subject, single-fact
            # branches, the then-fact being exactly the checked subclass,
            # and no concrete else-fact (the else keeps the base).
            poly_ok = not (
                bool(hoists)
                # A pointer-SHAPED declared subject (raw Ptr / Optional-ptr
                # borrow) or a `@dynamic` local's base pointer is fine -- the
                # cast arg spells the bare name; only a pointer-local whose
                # DECL is not pointer-shaped (an F2 rebind alias) keeps
                # rejecting.
                or (lc.binding(pvar).pointer
                    and not _poly_subject_is_pointer(
                        pvar, lc, declared.get(pvar)))
                # A reseat of the base pointer inside a branch would leave
                # the if-init cast stale.
                or (lc.binding(pvar).form is BindingForm.DYN
                    and (_body_writes_name(stmt.then_body, pvar)
                         or _body_writes_name(stmt.else_body, pvar)))
                # `self` is admitted, resumable included: `_poly_cast_context`
                # spells its cast arg (`this` / `&__self`), and this arm's
                # single-fact/then-only slice is exactly the if-INIT
                # form (`__self_ptr`, no frame-field collision). The
                # `__self_narrowed` rename belongs to the ALIAS emissions
                # (assert / early-return), which stay gated.
                or pvar in lc.prescan.global_seeded
                or pvar in lc.narrow.narrowed
                or pvar in lc.narrow.spelled
                or any(k != pvar for k in stmt.then_type_facts)
                or stmt.then_type_facts.get(pvar) != pmember
                or any(k == pvar for k in stmt.else_type_facts))
            if not poly_ok:
                note_detail("if.dyn_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_dyn_narrow_if(stmt, pinfo, lc, declared, loc,
                                        loop_depth=scope.loop_depth)
        minfo = _poly_narrow_multi_info(stmt.condition, declared, analyzer)
        if minfo is not None:
            mvar, _mmembers, mdecl = minfo

            def _poly_facts_extract(facts):
                # A strict-subclass fact would emit an extraction alias;
                # union / identity facts extract nothing. Foreign facts are
                # unmodeled.
                for k, ft in facts.items():
                    if k != mvar:
                        return True
                    if (isinstance(ft, NominalType)
                            and is_polymorphic_subclass_fact(
                                mdecl, ft, analyzer.registry)):
                        return True
                return False

            multi_ok = not (
                bool(hoists)
                or (lc.binding(mvar).pointer
                    and not _poly_subject_is_pointer(
                        mvar, lc, declared.get(mvar)))
                # `self` rides `_poly_cast_context`'s spelling here too; the
                # tuple form binds no alias, so the resumable rename that
                # gates the single-fact arm does not apply.
                or mvar in lc.prescan.global_seeded
                or mvar in lc.narrow.narrowed
                or mvar in lc.narrow.spelled
                or _poly_facts_extract(stmt.then_type_facts)
                or _poly_facts_extract(stmt.else_type_facts))
            if not multi_ok:
                note_detail("if.dyn_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_dyn_multi_if(stmt, minfo, lc, declared, loc,
                                       loop_depth=scope.loop_depth)
        ninfo = None
        if (isinstance(stmt.condition, TpyUnaryOp)
                and stmt.condition.op == "!"):
            ncond = stmt.condition.operand
            ninfo = _poly_narrow_info(
                ncond,
                _poly_anchor_declared(ncond, declared,
                                      lc.narrow.poly_source),
                analyzer)
        if ninfo is not None:
            # The negated poly guard (`if not isinstance(v, Sub):`): the
            # generic path -- `(!(<null-check>))` condition, plain
            # branches (no branch-entry extraction; the early-return
            # implicit-else alias is _lower_stmts' poly post-if arm).
            nvar, nmember, _ndecl = ninfo
            nanchored = _poly_anchor_declared(stmt.condition.operand,
                                              declared,
                                              lc.narrow.poly_source)
            neg_ok = not (
                # Sync only: a resumable frame's post-if alias would need
                # the `__self`/frame-field rename no alias maker produces.
                lc.resumable_leaf_mode
                # A `self` subject's post-guard reads route through the
                # receiver arms (`this->`), which the narrow rename does
                # not reach (they read the `__self` alias); fenced.
                or nvar == lc.self_receiver
                or (lc.binding(nvar).pointer
                    and not _poly_subject_is_pointer(
                        nvar, lc, nanchored.get(nvar)))
                or nvar in lc.prescan.global_seeded
                or nvar in lc.narrow.spelled
                # A then-entry or else-entry extraction has no emit on this
                # arm (else facts always carry the concrete member, so any
                # else body rejects).
                or _facts_have_concrete(stmt.then_type_facts)
                or any(k != nvar for k in stmt.else_type_facts)
                or bool(stmt.else_body))
            if not neg_ok:
                note_detail("if.dyn_narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("narrow.dyn_neg_guard")
            condition = THIRUnaryNot(
                result_type=analyzer.get_expr_type(stmt.condition),
                operand=THIRDynIsinstanceMulti(
                    result_type=analyzer.get_expr_type(
                        stmt.condition.operand),
                    checks_cpp=_poly_cast_checks(nvar, (nmember,), lc,
                                                 declared),
                    loc=getattr(stmt.condition.operand, "loc", None)),
                loc=getattr(stmt.condition, "loc", None))
        else:
            # No narrowing arm claimed this condition, so no branch-entry
            # extraction is emitted -- but sema stamps facts by a
            # compositional recursion over the whole boolean algebra, so a
            # condition no shape reader spells can still carry one that
            # needs an extraction. The else side follows the elif-continuation
            # skip: an elif link seeds its own facts instead.
            # A single-var concrete fact IS renderable: the branch carries
            # the extraction alias the narrowing arms would have emitted
            # (`_lower_narrowed_branch` below). Anything wider still rejects.
            if _facts_have_concrete(stmt.then_type_facts):
                then_fact = _single_fact_narrow(stmt.then_type_facts,
                                                declared, lc)
                if then_fact is None:
                    note_detail("if.cond_facts_unmirrored")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            if (stmt.else_body and _elif_link(stmt) is None
                    and _facts_have_concrete(stmt.else_type_facts)):
                else_fact = _single_fact_narrow(stmt.else_type_facts,
                                                declared, lc)
                if else_fact is None:
                    note_detail("if.cond_facts_unmirrored")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            try:
                condition = _lower_truthy(stmt.condition, lc, declared,
                                          temps_ok=True)
            except ThirUnsupported as ex:
                c = stmt.condition
                if isinstance(c, TpyBinOp):
                    lf = _type_family_tag(analyzer.get_expr_type(c.left),
                                          analyzer)
                    rf = _type_family_tag(analyzer.get_expr_type(c.right),
                                          analyzer)
                    # The operand families classify the condition's shape;
                    # the caught reason names what actually blocked. Without
                    # it the tag buckets unrelated operand shapes together
                    # and reads as one cluster. A reason the operand already
                    # recorded itself wins over both (set-if-empty).
                    note_detail(f"if.cond_binop.{c.op}.{lf}_{rf}:{ex.reason}")
                else:
                    _kind_detail("cond.", c)
                raise ex.with_context(stmt_reject_reason(stmt))
        if _cond_mixed_walrus_temps(condition, walrus_nested_ok=True):
            # Mixed walrus + OUTSIDE temps: the flat-flush (i==0) and
            # nested-elif renders need a clear-and-burn temp numbering this
            # arm does not produce, so the shape rejects (like the while
            # arm). A temp nested inside the walrus VALUE flushes with
            # the walrus predecl in decl order (single-eval if head), so
            # that flavor lowers.
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Branch-local `declared` copies: eligibility guarantees branches only
        # reassign already-declared locals, but a nested post-if narrowing may
        # retype its subject for the rest of ITS branch -- that must not leak
        # to the sibling or past the if (the per-branch scope restore).
        # The else side follows the chain collect: a flat elif link lowers
        # BARE (its chain-level post-if belongs to the enclosing statement
        # walk -- a plain-headed chain can still end in a narrowing elif); a
        # genuine else block (or a concrete-else-fact nested if) is a body.
        else_is_nested = False
        inner = _elif_link(stmt)
        if inner is not None and not _facts_have_concrete(stmt.else_type_facts):
            # An elif continuation seeds NO else facts (the
            # elif-continuation skip) -- the inner if seeds its own.
            else_stmts: tuple[THIRStmt, ...] = (
                _lower_stmt(inner, lc, dict(declared), in_branch=True,
                            branch_decls_ok=True,
                            loop_depth=scope.loop_depth),)
        else:
            else_is_nested = inner is not None
            saved_else_lf = _seed_literal_facts(stmt.else_type_facts, lc)
            try:
                if else_fact is not None:
                    _witness("if.cond_facts_alias")
                    else_stmts = _lower_narrowed_branch(
                        stmt.else_body, else_fact[2], else_fact[0],
                        else_fact[1], lc, declared,
                        getattr(stmt.else_body[0], "loc", loc),
                        loop_depth=scope.loop_depth)
                else:
                    else_stmts = _lower_scoped_stmts(
                        stmt.else_body, lc, dict(declared),
                        branch_decls_ok=True,
                        loop_depth=scope.loop_depth)
            finally:
                if saved_else_lf is not None:
                    lc.literal_facts = saved_else_lf
        saved_then_lf = _seed_literal_facts(stmt.then_type_facts, lc)
        try:
            if then_fact is not None:
                _witness("if.cond_facts_alias")
                then_stmts = _lower_narrowed_branch(
                    stmt.then_body, then_fact[2], then_fact[0], then_fact[1],
                    lc, declared, loc, loop_depth=scope.loop_depth)
            else:
                then_stmts = _lower_scoped_stmts(
                    stmt.then_body, lc, dict(declared),
                    branch_decls_ok=True,
                    loop_depth=scope.loop_depth)
        finally:
            if saved_then_lf is not None:
                lc.literal_facts = saved_then_lf
        return THIRIf(
            condition=condition,
            then_body=then_stmts,
            else_body=else_stmts,
            else_is_nested=else_is_nested,
            hoist_decls=tuple(hoist_decls),
            hoist_slots=tuple(hoist_slots),
            hoisted_bindings=tuple(hoisted_bindings),
            loc=loc,
        )
    if isinstance(stmt, TpyBreak):
        if scope.loop_depth == 0:
            raise ThirUnsupported("stmt.break")
        return THIRBreak(loc=loc)
    if isinstance(stmt, TpyContinue):
        if scope.loop_depth == 0:
            raise ThirUnsupported("stmt.continue")
        return THIRContinue(loc=loc)
    if isinstance(stmt, (TpyDelVar, TpyGlobal, TpyNonlocal)):
        # TpyGlobal / TpyNonlocal are no-code faces emitting only the source
        # comment (nonlocal's semantics live entirely in the capture list --
        # sema's node facts). `del x` follows a skip ladder:
        # a name that is not the sole owner of its value -- or whose
        # destruction is a no-op -- emits nothing; a generator or coroutine
        # closes in place (a started frame cannot move); the rest move-sink
        # (`{ auto __del_sink = std::move(name); }`, deref-first for an
        # owning pointer-local).
        if isinstance(stmt, TpyDelVar):
            globals_ = analyzer.function_global_decls.get(lc.func, set())
            sinks: list[tuple[str, bool]] = []
            closes: list[str] = []
            for name in stmt.names:
                # A narrowed binding has no sink render here; an unknown
                # name has no binding to sink; an `auto&&` storage-tuple
                # alias would sink THROUGH the alias, gutting the source's
                # storage. All reject.
                if (name not in declared or name in lc.narrow.narrowed
                        or lc.binding(name).storage_tuple):
                    # A FRAME-SLOT binding is NOT excluded: the sink
                    # renders the bare member move (`{ auto __del_sink =
                    # std::move(t); }` -- position-blind, the ordinary name
                    # spelling).
                    raise ThirUnsupported("stmt.del_var:binding")
                if (_del_var_trivial(declared[name], analyzer)
                        or lc.binding(name).ref_alias
                        or name in lc.prescan.alias_sources
                        or name in lc.prescan.param_names
                        or name in globals_):
                    continue
                if isinstance(peel_binding(declared[name]),
                              ConcreteGenType):
                    # A generator closes in place: its storage is an
                    # optional or a frame slot (`function_closed_frames`
                    # gives a plain function's one an optional), never a
                    # move of a started frame. (A coroutine handle only
                    # starts once awaited or handed on, so it keeps the
                    # move-sink below.)
                    if (lc.frame_local(name, FrameLocalClass.REBIND_PTR)
                            or not (lc.func.is_generator or lc.func.is_async
                                    or lc.binding(name).optional_storage)):
                        raise ThirUnsupported("stmt.del_var:frame_storage")
                    closes.append(escape_cpp_name(name))
                    continue
                if lc.binding(name).pointer:
                    # Only a pointer local every binding of which is fresh
                    # always points at storage it owns; any other may point
                    # at another binding's storage, so `del` only unbinds it
                    # and a slot it owns dies at scope end.
                    if not lc.binding(name).fresh_only:
                        continue
                    sinks.append((escape_cpp_name(name), True))
                    continue
                sinks.append((escape_cpp_name(name), False))
            if closes:
                _witness("stmt.close_local")
                close = THIRCloseLocal(names=tuple(closes), loc=loc)
                if not sinks:
                    return close
                return THIRStmtSeq(stmts=(
                    close, THIRDelVar(sinks=tuple(sinks), loc=loc)))
            if sinks:
                _witness("stmt.del_var_sink")
                return THIRDelVar(sinks=tuple(sinks), loc=loc)
        elif isinstance(stmt, TpyGlobal) and not all(
                name in lc.prescan.global_seeded for name in stmt.names):
            raise ThirUnsupported("stmt.global:global.unseeded")
        return THIRNoOpStmt(loc=loc)
    if isinstance(stmt, TpyDelItem):
        # The stamped `__delitem__` variant's binding over the receiver and
        # key (`::tpy::__delitem__(c, k);`), one statement line per target
        # in source order.
        # The index rides gen_index_expr: bare for the fixed-int / str-key
        # shapes, the `.to_fixed_check<int32_t>()` narrow for a runtime-BigInt
        # one (the view-key pin still cannot fire -- view-typed keys are not
        # admitted).
        calls = []
        for sub in stmt.targets:
            if (sub.needs_optional_runtime_check
                    or sub.slice_function_info is not None):
                raise ThirUnsupported("stmt.del_item:subscript_shape")
            recv = sub.obj
            if isinstance(recv, TpyName) and (lc.binding(recv.name).pointer
                                              or recv.name in lc.narrow.narrowed):
                raise ThirUnsupported("stmt.del_item:recv_shape")
            # Asked of the stamped winner first, as a read or store is
            # (`_call_arm_variant`): a deleter that is a call is the explicit
            # `t.__delitem__(k)`, its receiver and key prepared as that
            # call's are, whatever the receiver; the receiver form decides
            # only an element access.
            deleter = _call_arm_variant(sub, SubscriptDunder.DEL, analyzer)
            if deleter is not None:
                if not _dunder_call_receiver_ok(
                        recv, declared, analyzer, lc.names_with("pointer"),
                        lc.prescan.binds_global):
                    raise ThirUnsupported("stmt.del_item:recv_or_index")
                _witness("delitem.user_record")
                receiver = _lower_call_receiver(
                    sub.obj, lc, declared, allow_temps=False,
                    forms=_recv_forms(False, True))
                # The key is the explicit call's argument, by the one
                # argument sink (an `Own[str]` slot takes its owned copy).
                key = _lower_call_arg(
                    sub.index, deleter.params[0].type, lc, declared,
                    temp_args=True, method_arg=True, callee_fi=deleter,
                    retention=arg_retention(
                        deleter, 0, None, analyzer, sub.index,
                        recv=sub.obj))
                calls.append(_flush_witness(
                    "flush.expr_stmt",
                    _record_dunder_call(sub, SubscriptDunder.DEL, receiver,
                                        (key,), lc, loc)))
                continue
            recv_t = _subscript_container_recv_type(recv, declared, analyzer)
            if (recv_t is None and isinstance(recv, TpySubscript)
                    and recv.slice_function_info is None
                    and not recv.needs_optional_runtime_check):
                # One level deeper (`del d['a']['b']`): the inner read
                # renders `::tpy::__getitem__(d, "a")`, which the del takes
                # as its receiver lvalue -- the read arm gates its own shape,
                # so only the family check below is this row's business.
                recv_t = analyzer.get_expr_type(recv)
                if recv_t is not None:
                    _witness("delitem.subscript_recv")
            # The container receiver is admitted ELEMENT-BLIND: the del emit
            # never constructs, converts or reads the element slot, so the
            # key slice alone decides byte-parity (the reasoning
            # `_any_value_dict` applies to a dict[K, Any] value, generalized
            # to every element family).
            if not (recv_t is not None
                    and _container_del_recv(recv_t, analyzer)
                    and _witness("delitem.container")):
                raise ThirUnsupported("stmt.del_item:recv_or_index")
            if sub.dunder(SubscriptDunder.DEL) is None:
                note_detail("delitem.unresolved_dunder")
                raise ThirUnsupported("stmt.del_item:recv_or_index")
            receiver = _lower_expr(
                sub.obj, lc, declared,
                # The module-attr GLOBAL receiver's pointer-slot
                # read is a pinned native-slot consumer here
                # (`__delitem__((*environ), k)`).
                use=(_ExprUse(result=_ExprResultUse.RECEIVER)
                     if _module_var_recv(sub.obj, declared, analyzer)
                     else _ExprUse()),
                field_prechecked=isinstance(sub.obj, TpyFieldAccess))
            key = _lower_expr(sub.index, lc, declared)
            calls.append(_record_dunder_call(
                sub, SubscriptDunder.DEL, receiver, (key,), lc, loc))
        if len(calls) == 1:
            return THIRExprStmt(expr=calls[0], loc=loc)
        _witness("stmt.del_item_multi")
        return THIRDelItem(calls=tuple(calls), loc=loc)
    if isinstance(stmt, TpyDelAttr):
        begin_stmt()
        # `del obj.attr` (D16): sema resolved each target to a synthesized
        # `obj.__delattr__("attr")` call, one method-call statement line per
        # target. The generic method-call arm carries the emit (a
        # str-literal arg into a str slot, void result).
        lowered_dels = []
        for target in stmt.targets:
            call = target.dyn_delattr_call
            if call is None:
                note_detail("del_attr.unresolved")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            lowered_dels.append(_flush_witness(
                "flush.expr_stmt",
                _lower_expr(call, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.DISCARD,
                                         allow_temps=True))))
        _witness("stmt.del_attr")
        if len(lowered_dels) == 1:
            return THIRExprStmt(expr=lowered_dels[0], loc=loc)
        # One `recv.__delattr__("name");` line per target, in source order --
        # the del_item multi-target twin.
        _witness("stmt.del_attr_multi")
        return THIRDelItem(calls=tuple(lowered_dels), loc=loc)
    if isinstance(stmt, TpyWhile):
        # Body-declared locals read after the loop predecl before it, the
        # for-each ladder minus its loop-var flavors: the value family, and
        # the shared non-value admission (the RESUMABLE kind opens for leaf
        # mode as OPT_STORAGE, as at the for head).
        while_hoists = analyzer.if_branch_decls.get(stmt, {})
        while_bindings: list[THIRHoistedBinding] = []
        while_hoist = _Hoist(stmt, "while", bindings=while_bindings)
        _admit_hoists(while_hoist, while_hoists, declared, lc)
        while_hoist_decls = tuple(
            _lower_hoist_predecls(while_hoist, while_hoists, declared, lc,
                                  "while.hoist_decl"))
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        oinfo = (_or_chain_narrow_info(stmt.condition, declared, analyzer)
                 if info is None else None)
        if info is not None:
            # U4 while-isinstance: the loop-entry extraction is the branch
            # alias shape (fresh block, non-persistent); the subject retypes
            # for the body walk and pops at the closing brace. A sema-FOLDED
            # (exhaustiveness `true`) head renders `while (true)` and still
            # extracts -- `folded` is folded-TRUE only, the false fold never
            # reaches this info.
            var, u, _members, folded, _isin = info
            if (var in lc.narrow.narrowed
                    or not _narrow_facts_ok(
                        u, stmt.then_type_facts, var)):
                raise ThirUnsupported("stmt.while")
            if folded:
                _witness("while.narrow_folded")
            m = _narrow_fact_member(u, stmt.then_type_facts, var)
            condition = _lower_narrow_cond(info, stmt.condition, lc, declared)
            body = _lower_narrowed_branch(stmt.body, m, var, u, lc,
                                          declared, loc,
                                          loop_depth=scope.loop_depth + 1)
        elif oinfo is not None:
            # An isinstance leaf under `||`: the bare membership test
            # composed along the source tree, exactly as the or-chain `if`
            # renders it. A plain `or` installs no fact, so the body walks
            # UN-narrowed; a NEGATED chain (`while not (isinstance(u, A) or
            # flag)`) leaves one concrete fact, and the body carries its
            # loop-entry extraction alias like any narrowed body.
            var, u, _leaf_infos, _neg = oinfo
            if (var in lc.narrow.narrowed
                    or not _narrow_facts_ok(u, stmt.then_type_facts, var)):
                raise ThirUnsupported("stmt.while")
            saved_inline = dict(lc.inline_narrowed)
            try:
                condition = _lower_or_chain_cond(oinfo, stmt.condition, lc,
                                                 declared)
            finally:
                lc.inline_narrowed = saved_inline
            _witness("while.or_chain")
            om = _narrow_fact_member(u, stmt.then_type_facts, var)
            if om is not None:
                _witness("while.or_chain_narrowed")
            body = _lower_narrowed_branch(stmt.body, om, var, u, lc,
                                          declared, loc,
                                          loop_depth=scope.loop_depth + 1)
        else:
            try:
                condition = _lower_truthy(stmt.condition, lc, declared,
                                          temps_ok=True)
            except ThirUnsupported as ex:
                # The landmark names the loop head; the condition's own reason
                # rides it, or the tag names this catcher instead of the
                # construct that blocked.
                raise ex.with_context(f"stmt.while:{ex.reason}") from None
            if _cond_mixed_walrus_temps(condition):
                # The mixed shape needs the legacy single-eval flush --
                # reject rather than restructure it here.
                raise ThirUnsupported("stmt.while:while.mixed_walrus_temps")
            _literal_fact_fence(stmt.then_type_facts, stmt,
                                "while.literal_fact")
            wfact = (_single_fact_narrow(stmt.then_type_facts, declared, lc)
                     if _facts_have_concrete(stmt.then_type_facts) else None)
            if _facts_have_concrete(stmt.then_type_facts) and wfact is None:
                # A while head extracts its facts at loop entry; no
                # narrowing arm claimed this condition, and the facts are
                # wider than the one-var extraction below renders.
                raise ThirUnsupported(
                    "stmt.while:while.cond_facts_unmirrored")
            if wfact is not None:
                # A compound condition sema still narrowed through (`while
                # not isinstance(u, B) and flag`): the plain truthy render
                # installs no narrowing, so the BODY carries the extraction
                # alias the narrowing arms would have emitted at loop entry.
                wvar, wu, wmember = wfact
                _witness("while.cond_facts_alias")
                body = _lower_narrowed_branch(
                    stmt.body, wmember, wvar, wu, lc, declared, loc,
                    loop_depth=scope.loop_depth + 1)
            else:
                body = _lower_scoped_stmts(
                    stmt.body, lc, dict(declared),
                    branch_decls_ok=True,
                    loop_depth=scope.loop_depth + 1)
        return THIRWhile(
            condition=condition,
            body=body,
            orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                      "loop.while_else"),
            hoist_decls=while_hoist_decls,
            hoisted_bindings=tuple(while_bindings),
            loc=loc,
        )
    if isinstance(stmt, TpyAssert):
        # The narrowing alias (if any) is appended by _lower_stmts'
        # _append_assert_narrow pass -- statement-level, like the post-if
        # alias. A re-assert's condition was sema-folded to `true`
        # (gate-admitted only via _reassert_bump_info).
        if isinstance(stmt.condition, TpyBoolLiteral) and stmt.condition.value:
            msg = None
        elif isinstance(stmt.message, TpyStrLiteral):
            msg = stmt.message.value
        elif (isinstance(stmt.message, TpyFieldAccess)
              and _field_receiver_ok(stmt.message, declared, analyzer)):
            # Same eligibility as every other _lower_field_source site; a
            # marker-bearing access (property / dyn attr / unproven Optional
            # deref_check / ...) takes its own emit path -- the generic
            # expression arm below, which reaches the property's getter call.
            msg = _lower_field_source(stmt.message, lc, declared)
        elif stmt.message is not None:
            msg = _lower_expr(stmt.message, lc, declared)
        else:
            msg = None
        if isinstance(stmt.condition, TpyBoolLiteral):
            cond = THIRLiteral(
                result_type=analyzer.get_expr_type(stmt.condition),
                value=stmt.condition.value, loc=getattr(stmt.condition, "loc", None))
            return THIRAssert(condition=cond, message=msg, fold_constant=True,
                              loc=loc)
        if (lit := numeric_literal_truth(stmt.condition)) is not None:
            cond = THIRLiteral(result_type=BOOL, value=lit,
                               loc=getattr(stmt.condition, "loc", None))
            return THIRAssert(condition=cond, message=msg, fold_constant=True,
                              loc=loc)
        if isinstance(stmt.condition, TpyNoneLiteral):
            cond = THIRLiteral(
                result_type=analyzer.get_expr_type(stmt.condition), value=None,
                loc=getattr(stmt.condition, "loc", None))
            return THIRAssert(condition=cond, message=msg, fold_constant=True,
                              loc=loc)
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        if info is not None:
            var, u, _members, folded, _isin = info
            if (folded or var in lc.narrow.narrowed
                    or not _narrow_facts_ok(
                        u, stmt.then_type_facts, var)):
                raise ThirUnsupported("stmt.assert")
            cond = _lower_narrow_cond(info, stmt.condition, lc, declared)
        elif (pai := _poly_assert_narrow_info(stmt, declared,
                                              lc)) is not None:
            # The poly narrowing assert's condition: the bare null-check
            # (THIRAssert wraps it in `if (!(...))`); the persistent alias
            # follows via _append_assert_narrow's poly tail.
            cond = THIRDynIsinstanceMulti(
                result_type=analyzer.get_expr_type(stmt.condition),
                checks_cpp=_poly_cast_checks(pai[0], (pai[1],), lc,
                                             declared),
                loc=getattr(stmt.condition, "loc", None))
        elif _reassert_bump_info(
                stmt, declared, lc.narrow.persistent_narrowed,
                analyzer) is not None:
            cond = THIRLiteral(
                result_type=analyzer.get_expr_type(stmt.condition), value=True,
                loc=getattr(stmt.condition, "loc", None))
        elif (cxa := _protocol_constexpr_info(stmt.condition)) is not None:
            # The PROTOCOL-isinstance assert (`assert isinstance(items,
            # Spannable)` -> `if (!(<concept>)) raise_assertion_error();`):
            # the concept spelling is compile-time-constant, so THIRAssert's
            # plain negated-if wrap IS the render -- the assert flavor of
            # the F5 constexpr-if arm, same guards. The fact PERSISTS: the
            # downstream dispatch retypes via protocol_narrowings (the
            # NativeIterable/Spannable for-loop peephole reads it), so the
            # subject's declared entry retypes to the asserted protocol --
            # the constexpr-if branches' retype, scoped by the branch-copied
            # declared dict.
            cxa_var, cxa_type, cxa_neg = cxa
            if (lc.render_concept is None
                    or cxa_var in lc.narrow.narrowed
                    or cxa_var in lc.narrow.spelled
                    or any(k != cxa_var for k in stmt.then_type_facts)):
                raise ThirUnsupported("stmt.assert")
            _witness("assert.protocol_concept")
            cond = THIRConceptTest(
                result_type=BOOL,
                cpp=lc.render_concept(
                    cxa_var, cxa_type,
                    next((t for n, t in lc.params if n == cxa_var), None),
                    cxa_neg),
                loc=getattr(stmt.condition, "loc", None))
            if cxa_var in stmt.then_type_facts:
                declared[cxa_var] = stmt.then_type_facts[cxa_var]
        else:
            if (stmt.then_type_facts
                    and not _optional_narrow_facts_ok(
                        stmt.then_type_facts, declared, analyzer)):
                raise ThirUnsupported("stmt.assert")
            cond = _lower_truthy(stmt.condition, lc, declared)
        return THIRAssert(condition=cond, message=msg, loc=loc)
    if isinstance(stmt, TpyTupleUnpack):
        return _lower_unpack_stmt(stmt, scope)
    if isinstance(stmt, TpyForEach):
        route = _select_for_each_route(
            stmt, analyzer, declared, lc.narrow.narrowed.keys(),
            lc.names_with("pointer"), lc.names_with("iterator_object"),
            protocol_param_ok=protocol_iterable_ok(lc, stmt.iterable),
            tparam_bounds=lc.tparam_bounds,
            shadowable_globals=_shadowable_globals(lc, declared))
        it = stmt.iterable
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        et = route.elem_type
        # Branch-first-declared value locals used after the loop (sema's
        # `if_branch_decls`) predecl before the loop like _emit_branch_decls and
        # enter the CALLER's `declared` (function scope), so a nested loop and
        # the post-loop reads see them. Gated to the plain-value predecl family;
        # includes the loop var itself when it is hoisted (used after the loop).
        foreach_hoists = analyzer.if_branch_decls.get(stmt, {})
        foreach_bindings: list[THIRHoistedBinding] = []
        foreach_hoist = _Hoist(stmt, "foreach", bindings=foreach_bindings)
        _admit_hoists(foreach_hoist, foreach_hoists, declared, lc)
        foreach_hoist_decls = tuple(
            _lower_hoist_predecls(foreach_hoist, foreach_hoists, declared, lc,
                                  "foreach.hoist_decl"))
        body_declared = dict(declared)
        # An owned element stays in the iterator's step slot for the
        # iteration; the name binds it like a local, and the consuming move
        # keys on the source's element type, not on this binding.
        body_declared[stmt.var] = _unwrap_own(et)
        # In a resumable leaf the loop var (and the head's unpack targets)
        # bind C++ locals shadowing same-named frame fields for the body:
        # their records in the loop's scope node are no frame slots.
        with lc.branch_scope():
            # A value-repr Optional[scalar] loop var reads through the
            # same binding-keyed arms as a value-opt param
            # (deref-on-narrow, the whole-optional None-test/arg
            # renders); registered for the loop body's scope only.
            vopt_loop_var = _foreach_value_opt_elem(et) is not None
            if vopt_loop_var:
                _witness("foreach.value_opt_elem")
            # A ptr-repr Optional[F1-record] element (`for v in
            # d.values():` over `dict[str, P | None]`) binds the
            # STORAGE-form `std::optional<P>` -- the for-STATEMENT
            # producer of the storage-opt set (codegen's
            # register_loop_var_storage_form): reads render the bare
            # optional; `T*` arg slots lift via optional_to_ptr.
            # Container route only (an iter-proto yield is BORROW form).
            if (route.route == "container"
                    and _foreach_storage_opt_elem(et, analyzer)):
                # A CONST source (a @readonly method's field iteration,
                # a const-ref param) binds `const optional<P>&`, so its
                # consumers spell `const P*` -- registered in the const
                # twin so the lift-decl threads it. A dict-VIEW iterable
                # (`d.values()`) tracks its RECEIVER's const-ness. The
                # hop is taken HERE, for ANY method call, where the
                # predicate's own arm takes it only for a borrowing-view
                # return -- BUGS.md#comp-storage-opt-const-any-method-recv.
                _cs_src = (stmt.iterable.obj
                           if isinstance(stmt.iterable, TpyMethodCall)
                           else stmt.iterable)
                _witness("foreach.storage_opt_elem")
                if _iteration_yields_const(_cs_src, lc, analyzer):
                    _witness("foreach.storage_opt_const_elem")
            _pfb = lc.in_for_body
            lc.in_for_body = stmt.elem_type is not None or _pfb
            try:
                body = _lower_stmts(
                    stmt.body, lc, body_declared, in_branch=True,
                    branch_decls_ok=True,
                    loop_depth=scope.loop_depth + 1)
            finally:
                lc.in_for_body = _pfb
        if route.route == "range":
            if route.bigint_counter:
                _witness("range.bigint_counter")
            # Literal bounds retype to the elem slot (the counter type
            # threads into the arg render): a no-op for fixed-int
            # counters (bare token either way), the `::tpy::BigInt(N)`
            # ctor wrap for a BigInt one.
            if len(it.args) == 3:
                _witness("range.step_variable_expr"
                         if route.step_expr_computed
                         else f"range.step_{route.step_kind}")
            # Binding scans cover match targets; fact kills add closure writes.
            target_written = (
                _body_writes_name(stmt.body, stmt.var)
                or stmt.var in collect_fact_kills(stmt.body).names)
            return build_loop_node(
                "range", stmt.var, et, body, lc, declared,
                range_call=it, step_kind=route.step_kind,
                hoist_loop_var=stmt.hoist_loop_var,
                target_written=target_written,
                hoist_decls=foreach_hoist_decls,
                hoisted_bindings=tuple(foreach_bindings),
                orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                          "loop.for_else"),
                loc=loc,
            )
        if route.route == "iter_proto":
            _witness("foreach.iter_proto")
            proto_src_field = _frame_src_slot(stmt, lc)
            if isinstance(it, TpyIfExpr):
                # The route-admitted ternary of iterator-returning calls:
                # each arm lowers at the ITERABLE use (the same widening a
                # direct call source gets) under the conditional-operand
                # audit; the emit's per-arm regions defer their temps.
                _witness("foreach.ifexpr_iterable_lower")
                it_cond = _lower_truthy(it.condition, lc, declared,
                                        temps_ok=True)
                it_arms = tuple(
                    _lower_expr(
                        arm, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                     pos=SinkPos.ITER_SOURCE,
                                     allow_temps=True),
                        cond_eager=True)
                    for arm in (it.then_expr, it.else_expr))
                proto_iterable = THIRIfExpr(
                    result_type=lc.analyzer.get_expr_type(it),
                    cond=it_cond, then=it_arms[0], orelse=it_arms[1],
                    form=Form.VALUE,
                    normalized=_select_result(Held.VALUE, it, lc,
                                              temporary=True),
                    loc=getattr(it, "loc", None))
            else:
                proto_iterable = _lower_expr(
                    it, lc, declared,
                    # The emit flushes arg temps inside the rvalue brace
                    # scope, right before the `__src` bind, so temp-hoisting
                    # arg rows are safe here. A frame field holding the
                    # source is spelled off it.
                    use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                 pos=SinkPos.ITER_SOURCE,
                                 allow_temps=True,
                                 forms=(_ONLY_SELF_TYPED
                                        if proto_src_field is not None
                                        else None)))
            proto_iterable = _deref_loop_source(proto_iterable, lc)
            if proto_src_field is not None:
                proto_iterable = self_typed_frame_source(proto_iterable, lc)
            return build_loop_node(
                "iter_proto", stmt.var, et, body, lc, declared,
                iterable=proto_iterable,
                const_loop_var=stmt.const_loop_var,
                binding=_loop_var_record(stmt.var, stmt, lc),
                iterable_lvalue=route.iterable_lvalue,
                frame_src_field=proto_src_field,
                orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                          "loop.for_else"),
                loc=loc,
            )
        if route.route == "enum":
            _witness("foreach.enum")
            # The iterable is the fixed `EnumUtil<E>::members` lvalue (a static
            # array), spelled verbatim -- not lowered from `stmt.iterable` (a
            # bare enum TYPE name, which has no value-position lowering).
            members_cpp = (
                f"::tpy::EnumUtil<{lc.render_type(stmt.enum_iterable)}>::members")
            return build_loop_node(
                "each", stmt.var, et, body, lc, declared,
                iterable=THIRModuleVar(cpp=members_cpp, result_type=et),
                const_loop_var=stmt.const_loop_var,
                binding=_loop_var_record(stmt.var, stmt, lc),
                iterable_lvalue=True,
                hoist_decls=foreach_hoist_decls,
                hoisted_bindings=tuple(foreach_bindings),
                orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                          "loop.for_else"),
                loc=loc,
            )
        if route.str_list_method:
            _witness("foreach.str_list_method")
        if route.container_field:
            _witness("foreach.container_field")
        if route.value_tuple_elem:
            _witness("foreach.value_tuple_elem")
        if isinstance(it, TpyArrayLiteral):
            _witness("foreach.iter_literal")
        if route.str_literal_iterable:
            _witness("foreach.str_literal")
        consuming_src = it
        if (route.consuming_native_name is not None
                and stmt.consuming_iter_fi is None
                and isinstance(it, TpyCall) and it.args):
            # The EXPLICIT `own_iter(items)` call: the ConsumingIter wrap
            # re-spells the call, so the wrapped value is the ARG name.
            consuming_src = it.args[0]
        iterable = _lower_expr(
            consuming_src, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE,
                         pos=SinkPos.ITER_SOURCE),
            field_prechecked=isinstance(it, TpyFieldAccess),
            # A literal iterable renders target-less (no container target is
            # threaded here).
            container_threaded=not isinstance(
                it, (TpyArrayLiteral, TpyStrLiteral)))
        iterable = _deref_loop_source(iterable, lc)
        if (route.literal_src_type is not None
                and isinstance(iterable, THIRContainerLiteral)):
            # Self-describe the brace-init with the route's resolved container
            # type. The elements still render target-less; only the SOURCE's
            # own type is spelled, so the capture owns a real container whose
            # elements the aliasing loop var can mutate.
            iterable = replace(
                iterable, typed_brace_cpp=lc.render_type(route.literal_src_type))
        if route.consuming_native_name is not None:
            _witness("foreach.consuming_iter")
            iterable = THIRConsumingIter(
                result_type=iterable.result_type, value=iterable,
                native_name=route.consuming_native_name, form=Form.VALUE,
                loc=loc)
        hoisted_bt = (_borrow_tuple_local_type(stmt.var, declared,
                                             lc.names_with("storage_tuple"))
                      if stmt.hoist_loop_var else None)
        # A loop var used after the loop is predeclared as a borrow tuple:
        # each step (`*__beg_N`) is lent to it element by element.
        hoisted_step = (lent_holder(et, hoisted_bt, lc, loc=loc)
                        if hoisted_bt is not None else None)
        # The consuming arm wraps the source into a fresh value of its own
        # (`own_iter(std::move(xs))`); what it iterates is that wrapper, which
        # the frame does not place -- so it asks nothing here, exactly as it
        # sets its own `iterable_lvalue`.
        src_field = (None if route.consuming_native_name is not None
                     else _frame_src_slot(stmt, lc))
        if src_field is not None:
            iterable = self_typed_frame_source(iterable, lc)
        return build_loop_node(
            "each", stmt.var, et, body, lc, declared,
            iterable=iterable,
            source=stmt.iterable,
            const_loop_var=stmt.const_loop_var,
            binding=_loop_var_record(stmt.var, stmt, lc),
            iterable_lvalue=route.iterable_lvalue,
            consuming=(route.consuming_native_name is not None
                       or route.consuming_name),
            hoist_loop_var=stmt.hoist_loop_var,
            str_literal_iterable=route.str_literal_iterable,
            hoisted_step=hoisted_step,
            hoist_decls=foreach_hoist_decls,
            hoisted_bindings=tuple(foreach_bindings),
            frame_src_field=src_field,
            orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                      "loop.for_else"),
            loc=loc,
        )
    if isinstance(stmt, TpyExprStmt):
        begin_stmt()
        if (isinstance(stmt.expr, TpyCall)
                and stmt.expr.compile_time_assert):
            # assert_send/assert_sync: checked in sema, no emission.
            _witness("stmt.compile_time_assert")
            return THIRNoOpStmt(loc=None)
        er_fi = _error_return_stmt_fi(stmt.expr, analyzer)
        if er_fi is not None:
            # A discarded @error_return call: the `__try_tmp_N` block.
            # Bare free/method calls only; the coerce-wrapped shapes
            # reject.
            if not isinstance(stmt.expr, (TpyCall, TpyMethodCall)):
                note_detail("error_return.stmt_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _reject_nested_error_return_arg(stmt, stmt.expr, analyzer)
            return THIRErrorReturnDiscard(
                call=_lower_expr(stmt.expr, lc, declared,
                                 use=_ExprUse(result=_ExprResultUse.DISCARD,
                                              allow_temps=True),
                                 error_return_raw=True),
                staged=lc.exit_staged, loc=loc)
        if _is_builtin_print(stmt.expr, declared, lc.analyzer):
            e = stmt.expr
            narrowed = lc.narrow.narrowed.keys()
            pointers = scope.admission_pointers()
            if e.double_star_unpack is not None:
                note_detail("print.kwargs")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            sep_expr = end_expr = sink_expr = None
            sep_value: 'str | None' = " "
            end_value: 'str | None' = "\n"
            print_flush = False
            if e.kwargs:
                # sep=/end= (str literal or resolved str-value name), file=
                # (an ostream / stream-pointer sink read in value position, so
                # `sys.stderr` derefs), and a LITERAL flush= (True appends
                # `<< std::flush`, False is a no-op). With no args the chain
                # is just the end token, which `end=""` suppresses entirely
                # (the emitter's empty-parts arm writes nothing, which is
                # what `print(end="")` does); a sep with nothing to separate
                # never reaches the chain. Runtime flush values and
                # non-str-slice sep/end shapes reject.
                file_val = e.kwargs.get("file")
                rest = {k: v for k, v in e.kwargs.items() if k != "file"}
                flush_val = rest.pop("flush", None)
                if flush_val is not None:
                    # Sema pins flush= to a bool LITERAL (a runtime value is
                    # a SemanticError), so the isinstance restates a proven
                    # fact rather than a reachable reject.
                    if not isinstance(flush_val, TpyBoolLiteral):
                        note_detail("print.kwargs")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    print_flush = bool(flush_val.value)
                    if print_flush:
                        _witness("print.kw_flush")
                if any(k not in ("sep", "end") for k in rest):
                    note_detail("print.kwargs")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if not e.args and rest:
                    _witness("print.kw_no_args")
                evaluated_kws: list[str] = []
                for kw, kv in rest.items():
                    token = _print_kwarg_token(
                        kv, declared, pointers, narrowed, analyzer)
                    if token is None:
                        note_detail("print.kwargs")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    kind, value = token
                    if kind == "expr":
                        if not _expr_evaluation_inert(e.args, declared, analyzer):
                            note_detail("print.kwarg_expr_arg_order")
                            raise ThirUnsupported(stmt_reject_reason(stmt))
                        if evaluated_kws and kw == "sep":
                            # The chain renders sep ahead of end, so a source
                            # that spells them the other way round would swap
                            # two evaluations CPython orders by source.
                            note_detail("print.kwarg_expr_order")
                            raise ThirUnsupported(stmt_reject_reason(stmt))
                        evaluated_kws.append(kw)
                        _witness("print.kw_expr")
                        # Bound by reference: an lvalue source is not copied
                        # and a prvalue's lifetime extends to the end of the
                        # enclosing block, so the token stays valid through
                        # the chain.
                        lowered_kw = _hoisted(THIRArgTemp(
                            result_type=analyzer.get_expr_type(kv),
                            init=_lower_expr(kv, lc, declared,
                                             use=_ExprUse(allow_temps=True)),
                            cpp_type="const auto&",
                            movable=False), lc)
                    else:
                        lowered_kw = (_lower_expr(kv, lc, declared)
                                      if kind == "name" else None)
                    if kw == "sep":
                        sep_expr, sep_value = lowered_kw, value
                    else:
                        end_expr, end_value = lowered_kw, value
                if rest:
                    _witness("print.kw_sep_end")
                if file_val is not None:
                    sink_expr = lower_print_sink(file_val, lc, declared)
                    _witness("print.file_sink")
            lowered_args = []
            for arg in e.args:
                star: 'TpyStarUnpack | None' = None
                if isinstance(arg, TpyStarUnpack):
                    # The admission below asks about the unpacked SOURCE,
                    # which `_lower_print_arg` lowers as the positional
                    # `print(xs)` would.
                    star, arg = arg, arg.expr
                    if isinstance(arg, TpyName) and arg.name in narrowed:
                        note_detail("print.star_narrowed_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                if (isinstance(arg, (TpyCall, TpyMethodCall))
                        and isinstance(getattr(arg, "macro_expansion", None),
                                       TpyTupleLiteral)
                        and _wrap_print_form(arg, declared, analyzer, pointers)
                        is None):
                    # A TUPLE-producing call-macro arg (`print(astuple(x))`)
                    # whose CALL node does not classify (`_wrap_print_form`
                    # None -- e.g. a non-value tuple result outside
                    # `_value_tuple_nested`): the sema-synthesized expansion
                    # renders in place and the print arg classifies by TYPE,
                    # so fall through to the EXPANSION.
                    # When the call node DOES classify, keep the call path
                    # -- it lowers the expansion as an expression and its
                    # element admissions differ (a swap here rejected
                    # dataclass_asdict_mixed's str-field elements). Tuple
                    # literals ONLY: other expansion kinds
                    # (hasattr's probe synth, getattr) already ride the
                    # call-typed rows.
                    arg = arg.macro_expansion
                if isinstance(arg, TpyName) and arg.name in narrowed:
                    if arg.name in lc.narrow.any_narrowed:
                        # An Any-narrowed alias: print args classify by the
                        # DECLARED type (the pre-narrow binding), so an Any
                        # subject streams the alias RAW -- including the
                        # narrowed-float face (no print_float wrap; that
                        # formatting gap is filed in BUGS.md).
                        lowered_args.append(THIRPrintArg(
                            expr=_lower_expr(arg, lc, declared),
                            print_form=PrintForm.RAW))
                        continue
                    # A print arg classifies a NAME by its DECLARED binding
                    # (which narrowing never rewrites) -- so every
                    # U3-narrowed alias streams
                    # through the union-typed `::tpy::__str__` visitor arm,
                    # extraction alias notwithstanding.
                    if lc.narrow.subject_union.get(arg.name) is None:
                        note_detail("print.narrowed_arg")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("print.union_narrowed_arg")
                    lowered_args.append(THIRPrintArg(
                        expr=_lower_expr(arg, lc, declared),
                        print_form=PrintForm.STR))
                    continue
                if type(arg) in _comprehensions._COMP_KINDS:
                    ok = True
                elif _optional_ptr_borrow_wide_name(
                        arg, declared, analyzer) is not None:
                    # A ptr-repr Optional NAME prints through
                    # `::tpy::print_optional(v)` -- DECLARED-type keyed
                    # (a None-narrowed occurrence still wraps); the
                    # OPT_PTR row in _lower_print_arg renders it, adding
                    # the kind-keyed template args for a pointee with no
                    # plain operator<< (`print_optional<ListPrinter<..>,
                    # ..>(lst)`).
                    ok = True
                elif (isinstance(arg, (TpyCall, TpyMethodCall))
                        and _optional_ptr_borrow_wide(
                            analyzer.get_expr_type(arg),
                            analyzer) is not None):
                    # A ptr-repr Optional-returning CALL prints through
                    # `::tpy::print_optional(call(..))` -- the OPT_PTR
                    # call row in _lower_print_arg renders it.
                    ok = True
                else:
                    ok = (_print_arg_ok(arg, declared, analyzer)
                          or (isinstance(arg, TpyName)
                              and arg.name not in pointers
                              and (_wpf_arg := _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers)) is not None
                              # The union STR row stays out of RESUMABLE
                              # bodies: a persistent assert-narrow alias
                              # would leak past its branch in the flat CFG
                              # (`__str__(__a)` in the else arm), while the
                              # BB snapshot restores it. Keyed on
                              # the flat-CFG lane flag. is_async is
                              # belt-and-braces (always resumable).
                              and not (_wpf_arg is PrintForm.STR
                                       and (lc.resumable_leaf_mode
                                            or lc.func.is_async))
                              and _witness("print.wrap_arg"))
                          or (isinstance(arg, TpyName)
                              and (lc.binding(arg.name).optional_storage
                                   or lc.binding(arg.name).lazy_rebind_slot
                                   or lc.binding(arg.name).rebind_slot
                                   or lc.frame_storage_name(arg.name)
                                   or lc.binding(arg.name).global_slot)
                              and not isinstance(
                                  unwrap_readonly(unwrap_ref_type(
                                      unwrap_send_sync(
                                          declared.get(arg.name)))),
                                  OptionalType)
                              and _wrap_print_form(
                                  arg, declared, analyzer, pointers)
                              in (PrintForm.LIST, PrintForm.DICT,
                                  PrintForm.SET, PrintForm.BYTEARRAY)
                              # A branch-hoisted container pointer-local --
                              # or a pointer-SLOT container global at top
                              # level -- streams its deref inside the same
                              # kind-keyed wrap (`ListPrinter((*nums))`);
                              # pointer-repr Optional bindings stay out
                              # (their whole-name print is a different
                              # render).
                              and _witness("print.hoisted_container_arg"))
                          # `print(self)` streams the record raw via its
                          # emitted operator<<. The receiver renders
                          # `(*this)`, which is exactly what `THIRSelf.deref`
                          # spells in a value position.
                          or (isinstance(arg, TpyName)
                              and lc.self_receiver is not None
                              and arg.name == lc.self_receiver
                              and lc.record_name is not None
                              and record_like(
                                  analyzer.get_expr_type(arg), analyzer)
                              and _witness("print.self_arg"))
                          # A POINTER-bound F1-record/range NAME (the RAW
                          # wrap class) streams its deref through the same
                          # raw operator<< (`(*al)`) -- the wrap render
                          # lowers with the full-deref use.
                          or (isinstance(arg, TpyName)
                              and arg.name != lc.self_receiver
                              and arg.name in pointers
                              and _wrap_print_form(
                                  arg, declared, analyzer, pointers)
                              is PrintForm.RAW)
                          or (isinstance(arg, TpyFieldAccess)
                              and _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers) is not None
                              and _witness("print.wrap_field_arg"))
                          or _print_tuple_record_elem(arg, declared,
                                                      analyzer)
                          or (isinstance(arg, TpySubscript)
                              and _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers) is not None
                              and _witness(
                                  "print.container_slice_arg"
                                  if arg.slice_function_info is not None
                                  else "print.tuple_subscript_arg"))
                          or (isinstance(arg, (TpyCall, TpyMethodCall))
                              and _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers) is not None
                              and _witness("print.container_call_arg"))
                          # A list/Array LITERAL arg (the typed brace-init
                          # inside ListPrinter -- CTAD needs the type), a
                          # DICT literal (self-describing ordered_map render
                          # inside DictPrinter -- the asdict expansion), a
                          # pointer-repr tuple LITERAL (the borrow-form
                          # render inside TuplePrinter), or a
                          # container-result BINOP (the kind wrap over the
                          # operator render).
                          or (isinstance(arg, (TpyArrayLiteral,
                                               TpyDictLiteral,
                                               TpyTupleLiteral, TpyBinOp))
                              and _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers) is not None)
                          # A container WALRUS arg (`print((cols := [..]))`):
                          # the kind-keyed wrap composes over the walrus
                          # render (`ListPrinter((cols = .., *cols))`); the
                          # walrus arm validates its own target class.
                          or (isinstance(arg, TpyNamedExpr)
                              and _wrap_print_form(
                                  arg, declared, analyzer,
                                  pointers) is not None
                              and _witness("print.walrus_arg")))
                if not ok:
                    fam = _type_family_tag(
                        analyzer.get_expr_type(arg), analyzer)
                    _kind_detail(f"print.arg.{fam}_", arg)
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                try:
                    lowered_args.append(
                        _lower_print_arg(star if star is not None else arg,
                                         lc, declared, pointers))
                except ThirUnsupported as exc:
                    # The inner reject already names the blocker, but it
                    # travels in the exception -- the detail slot is
                    # first-wins, so a shape tag composed here would win by
                    # default and hide it.
                    note_detail(exc.reason)
                    raise exc.with_context(stmt_reject_reason(stmt)) from None
            return THIRPrint(args=tuple(lowered_args), sep_expr=sep_expr,
                             end_expr=end_expr, sep_value=sep_value,
                             end_value=end_value, sink_expr=sink_expr,
                             flush=print_flush, loc=loc)
        narrowed = lc.narrow.narrowed.keys()
        # A `@call_macro` whose expansion is itself a macro call -- peel
        # the macro_expansion chain so the renderer below is reached at
        # STATEMENT position. Without the peel the inner markers lower
        # through the expression ladders, which drop the DISCARD use.
        macro_src = stmt.expr
        chain_peeled = False
        while (isinstance(macro_src, TpyCall)
                and isinstance(macro_src.macro_expansion, TpyCall)):
            macro_src = macro_src.macro_expansion
            chain_peeled = True
        if (isinstance(macro_src, TpyCall)
                and isinstance(macro_src.macro_expansion, TpyMethodCall)):
            rt = analyzer.get_expr_type(stmt.expr)
            if rt is None or isinstance(rt, VoidType):
                # A void statement-position macro expansion (the setattr /
                # delattr builtins): the expansion renders in place, but
                # the expression macro arm drops the DISCARD use (a void
                # method call rejects in value position), so the expansion
                # dispatches here in statement position instead.
                if chain_peeled:
                    _witness("expr_stmt.marker_chain")
                exp = macro_src.macro_expansion
                if (exp.method == "__setattr__" and len(exp.args) == 2
                        and isinstance(exp.args[0], TpyStrLiteral)
                        and isinstance(exp.args[1], TpyCoerce)):
                    # `setattr(obj, "name", <coerced literal>)` synthesizes
                    # the SAME `obj.__setattr__(...)` call the dynamic-attr
                    # WRITE lowers -- delegate to its narrow arm (the Any
                    # value slot is outside the generic method-arg slice).
                    # ONLY the literal-name + coerced-value shape: a runtime
                    # name / uncoerced value (`setattr(h, name, value)` on a
                    # str-slotted __setattr__) already routes through the
                    # general method arm and must keep doing so.
                    lowered_sa = _lower_dyn_setattr_call(exp, lc, declared)
                    _witness("expr_stmt.macro_discard")
                    return THIRExprStmt(
                        expr=_flush_witness("flush.expr_stmt", lowered_sa),
                        loc=loc)
                _witness("expr_stmt.macro_discard")
                return THIRExprStmt(
                    expr=_flush_witness(
                        "flush.expr_stmt",
                        _lower_expr(
                            macro_src.macro_expansion, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.DISCARD,
                                         allow_temps=True))),
                    loc=loc)
        void_cast = False
        if isinstance(stmt.expr, TpyCall):
            eligible = True
        elif isinstance(stmt.expr, TpyMethodCall):
            eligible = True
        elif isinstance(stmt.expr, TpyBinOp) and record_like(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(stmt.expr)))), analyzer):
            # A DISCARDED record-result dunder binop (`timedelta(seconds=1)
            # / 0` evaluated for its ZeroDivisionError): the template render
            # + `;`, identical to its expression form.
            eligible = bool(_witness("expr_stmt.record_binop"))
        elif isinstance(stmt.expr, (TpyBinOp, TpyUnaryOp, TpyChainedCompare,
                                    TpyIfExpr, TpyFieldAccess)):
            # A DISCARDED operator/ternary/field read (`n + 1`, `-n`,
            # `0 < n < 5`, `n if c else 0`, `p.x`). Python evaluates and
            # discards these too, and the checked render is the effect
            # worth keeping (`add_check` raises on overflow, a field read
            # off a property calls the getter), so the statement form is
            # the expression render plus `;`. The operand ladders
            # re-validate their own shapes.
            # The cast to void is what keeps the PURE forms (`n < 2;`,
            # `p.x;`) buildable under -Wunused-value + -Werror; it discards
            # the value without suppressing the evaluation the row exists
            # for. Decided here, not re-derived from the shape at emit.
            eligible = bool(_witness("expr_stmt.value_discard"))
            void_cast = True
        elif (isinstance(stmt.expr, TpySubscript)
                and not isinstance(stmt.expr.index, TpySlice)
                and stmt.expr.slice_function_info is None):
            # A DISCARDED element read (`c, _ = (items[2], items[0])`
            # desugars the `_` half to a bare subscript statement evaluated
            # for its effect/bounds check): `::tpy::__getitem__(recv, i);`
            # -- the subscript arm re-validates receiver/index shapes.
            eligible = bool(_witness("expr_stmt.subscript_discard"))
        elif isinstance(stmt.expr, TpyName):
            # A bare NAME statement (`x`): the name's own read render,
            # exactly its expression form. Python evaluates and discards it
            # too, so nothing is dropped; the name arm re-validates the
            # binding. Cast to void for the same reason the pure operator
            # forms are -- a bare `x;` is what -Wunused-value rejects under
            # -Werror.
            eligible = bool(_witness("expr_stmt.name"))
            void_cast = True
        elif isinstance(stmt.expr, TpyIntLiteral):
            # A bare int-literal statement -- the `0` the REPL appends to
            # every compile so `main()` is always emitted. Discarding a
            # literal has no effect to sequence, so the render is the
            # literal itself, cast to void so -Wunused-value does not reject
            # it under -Werror.
            eligible = True
            void_cast = True
        else:
            eligible = _kind_detail("expr_stmt.", stmt.expr)
        if not eligible:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        discarded = _lower_expr(
            stmt.expr, lc, declared,
            use=_ExprUse(result=_ExprResultUse.DISCARD, allow_temps=True))
        discarded = full_expression_record(discarded, lc.analyzer)
        # A discarded CONSTRUCTION renders a functional cast, and `T(name);`
        # in statement position is a DECLARATION of `name` -- a build error
        # in the name's own block, a silently default-constructed shadow in a
        # nested one. Read off the LOWERED node: a macro expansion and a
        # module-qualified spelling both arrive there.
        return THIRExprStmt(expr=_flush_witness("flush.expr_stmt", discarded),
                            void_cast=void_cast or _constructs_value(discarded),
                            loc=loc)
    if isinstance(stmt, TpyWith):
        begin_stmt()
        return _lower_with(stmt, lc, declared, loc,
                           in_branch=scope.in_branch,
                           loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyTry):
        begin_stmt()
        return _lower_try(stmt, lc, declared, loc,
                          in_branch=scope.in_branch,
                          loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyMatch):
        begin_stmt()
        folded = _match._lower_overload_folded_match(
            stmt, lc, declared, loc, in_branch=scope.in_branch,
            loop_depth=scope.loop_depth)
        if folded is not None:
            return folded
        match_route = _match._select_match_route(
                stmt, analyzer, declared, scope.admission_pointers(),
                lc.narrow.narrowed.keys(), lc.names_with("storage_tuple"),
                lc.prescan, lc)
        return _match._lower_match(
            stmt, match_route, lc, declared, scope.admission_pointers(), loc,
                                   loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyRaise):
        return _lower_raise(stmt, lc, declared, loc)
    raise ThirUnsupported(f"stmt.unhandled:{type(stmt).__name__}")

def _lower_try(stmt: TpyTry, lc: _LowerCtx, declared: dict[str, TpyType],
               loc, *, in_branch: bool = False,
               loop_depth: int = 0) -> THIRTry:
    """Lower a `try` of any sync tier (see `THIRTry` for the emit
    shapes). The hoisted predecls render here (`render_type`, codegen's
    type_to_cpp; names spell RAW) in sema's sorted order,
    and enter the CALLER's `declared`. That dict is the enclosing block's,
    which for a try nested in a branch or loop is narrower than the
    function-scope Python gives the name -- see the placement comment on the
    hoist loop for why the gap is fail-safe. Each body
    lowers under its own narrowing-scope snapshot (narrowed state restores
    between sibling blocks); a handler's `as` binding enters its body's
    scope typed like sema binds it, the catch parameter being the binding.
    `finally_terminates` is the last-stmt raise/return fact;
    `body_terminates` is the frame-wrap fact -- the try body plus every
    handler, never the whole statement, so an always-terminating finally
    can't elide its own fall-through copy."""
    if stmt.tier is TryTier.RETURN and len(stmt.handlers) != 1:
        # The return tier dispatches on handlers[0] alone; sema confines it
        # to a single ReturnException handler -- defensive.
        note_detail("try.return_handlers")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    hoists = lc.analyzer.if_branch_decls.get(stmt, {})
    # A try inside a branch / loop hoists here rather than at function top:
    # the predecl is emitted at the try itself, so the C++ scope IS the
    # enclosing block. The name enters the caller's branch-local
    # `declared`, which is narrower than Python's function scope; that gap
    # is fail-safe, since a read after the branch finds no binding and
    # rejects (`name.global_read`) rather than mis-rendering.
    try_hoist = _Hoist(stmt, "try")
    _admit_hoists(try_hoist, hoists, declared, lc)
    for handler in stmt.handlers:
        if (handler.binding
                and _handler_binding_type(handler, lc.analyzer) is None):
            raise ThirUnsupported(stmt_reject_reason(stmt))
    hoist_decls = _lower_hoist_predecls(try_hoist, hoists, declared, lc,
                                        "try.hoist_decl")
    body_terminates = try_terminates_ignoring_finally(stmt)
    if stmt.tier is TryTier.FINALLY_ONLY:
        _witness("try.finally_only")
    elif stmt.tier is TryTier.RETURN:
        _witness("try.return_tier")
    else:
        _witness("try.throw_tier")
        if len(stmt.handlers) > 1:
            _witness("try.multi_handler")
        if any(h.exception_type is None for h in stmt.handlers):
            _witness("try.bare_except")
        if any(h.binding for h in stmt.handlers):
            _witness("try.binding")
        if stmt.else_body:
            _witness("try.else")
        if stmt.finally_body:
            _witness("try.except_finally")
    handlers: list[THIRExceptHandler] = []
    err_opt_cpp: 'str | None' = None
    # The finally frame wraps the handlers, the try and the else body.
    with lc.staged_region(bool(stmt.finally_body)):
        for h in _bindings.scoped(lc, stmt.handlers):
            cpp = None
            h_record = (handler_exception_record(
                h, lc.analyzer.registry, lc.analyzer.ctx.module_name)
                if h.exception_type is not None else None)
            if h_record is not None:
                cpp = exception_record_cpp(h_record,
                                           lc.analyzer.ctx.module_name)
            h_declared = dict(declared)
            if h.binding:
                bt = _handler_binding_type(h, lc.analyzer)
                assert bt is not None, "ineligible handler reached lowering"
                h_declared[h.binding] = bt
            if stmt.tier is TryTier.RETURN and h.binding:
                # The `__err_opt_N` capture decl spells the error type.
                err_opt_cpp = exception_record_cpp(
                    h_record, lc.analyzer.ctx.module_name)
            _bindings.require_entered(lc, h)
            handlers.append(THIRExceptHandler(
                cpp_type=cpp, binding=h.binding,
                body=_lower_scoped_stmts(
                    h.body, lc, h_declared, branch_decls_ok=True,
                    loop_depth=loop_depth),
                source_display=(h.exception_type
                                if stmt.tier is TryTier.RETURN else None)))
        try_body = _lower_scoped_stmts(
            stmt.try_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth)
        else_body = _lower_scoped_stmts(
            stmt.else_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth)
    last = stmt.finally_body[-1] if stmt.finally_body else None
    finally_terminates = isinstance(last, (TpyRaise, TpyReturn))
    if body_terminates and stmt.finally_body:
        _witness("try.body_terminates")
    if finally_terminates:
        _witness("try.finally_terminates")
    return THIRTry(
        tier=stmt.tier,
        try_body=try_body,
        handlers=tuple(handlers),
        else_body=else_body,
        finally_body=_lower_scoped_stmts(
            stmt.finally_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth),
        hoist_decls=tuple(hoist_decls),
        body_terminates=body_terminates,
        finally_terminates=finally_terminates,
        err_opt_cpp=err_opt_cpp,
        loc=loc,
    )

def _lower_raise(stmt: TpyRaise, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc) -> THIRRaise:
    """Lower a raise (see `THIRRaise`): a bare re-raise, the type form, a
    construction -- lowered as the initializer of `e = X(args)` is, a
    storage use at a flush position -- or a value operand."""
    if stmt.raise_expr is None and stmt.exception_type is None:
        _witness("raise.bare")
        return THIRRaise(staged=lc.exit_staged, loc=loc)
    if stmt.resolved_type is None:
        # `raise <expr>` (a bound var / call result) emits
        # `<expr>{.__deref__()*N}.__raise__();` (the source read plus the
        # virtual hop). The resumable-frame and @error_return contexts
        # render this IDENTICALLY -- the expr form has no frame- or
        # error-return-specific branch -- so they route here too; the
        # operand's own lowering still gates its shape.
        # A CALL source sits under the postfix `.__raise__()` member, so it
        # lowers in receiver position (`ea.Err(9).__raise__();` -- the result
        # never reaches a value slot). Every other source is a plain value
        # read (a pointer-repr record local derefs to a reference before
        # the `.__raise__()` member call -- `(*e).__raise__()`).
        # The non-call operand is the RECEIVER of `.__raise__()`, at a
        # position that derefs so a pointer-repr local still resolves first
        # (`_name_read_deref`'s indirect branch precedes the receiver
        # exclusion): the field gate's receiver rows admit the narrowed
        # stored-exception field (`(*r.exception).__deref__().__raise__()`).
        raised = _lower_expr(
            stmt.raise_expr, lc, declared,
            use=_ExprUse(result=_ExprResultUse.RECEIVER)
            if isinstance(stmt.raise_expr, (TpyCall, TpyMethodCall))
            else _ExprUse(result=_ExprResultUse.RECEIVER,
                          pos=SinkPos.RAISE_OPERAND))
        _witness("raise.expr")
        return THIRRaise(raise_expr=raised, via_virtual=True,
                         deref_depth=stmt.deref_depth, loc=loc)
    record = lc.analyzer.registry.get_record_for_type(stmt.resolved_type)
    if record is None:
        raise ThirUnsupported("stmt.raise")
    return_tier = record.is_return_exception
    if return_tier and lc.error_return_cpp is None:
        # Sema confines a ReturnException raise to @error_return bodies;
        # defensive -- any other context rejects.
        raise ThirUnsupported("stmt.raise")
    if stmt.raise_expr is None:
        _witness("raise.type")
        return THIRRaise(
            cpp_type=exception_record_cpp(record, lc.analyzer.ctx.module_name),
            via_virtual=stmt.raise_via_virtual, return_tier=return_tier,
            staged=lc.exit_staged, loc=loc)
    raised = _lower_expr(
        stmt.raise_expr, lc, declared,
        use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True))
    _witness("raise.ctor")
    return THIRRaise(raise_expr=raised, via_virtual=stmt.raise_via_virtual,
                     return_tier=return_tier, staged=lc.exit_staged, loc=loc)

def _lower_with(stmt: TpyWith, lc: _LowerCtx, declared: dict[str, TpyType],
                loc, *, in_branch: bool = False,
                loop_depth: int = 0) -> THIRWith:
    """Lower a sync `with` -- the item facts name the header arms
    (see `THIRWith` for the emit shape). Targets are declared at the enclosing
    C++ scope and stay visible after the block (Python scoping), so they extend
    the CALLER's `declared` dict -- unlike branch bodies, which lower over a
    copy. The body lowers under a narrowing-scope snapshot, restoring the
    narrowing and persistent-alias state around the try
    body. `body_terminates` comes from `stmts_terminate`, so
    the per-layer normal-exit elision folds at emit. Sema's hoist
    (`if_branch_decls`) renders like `_lower_try`'s -- the plain-value
    predecl family entering the CALLER's `declared` (body writes lower as
    reassigns against the predecl slot). A with nested in a branch or loop
    hoists at the with itself, like the try sibling, so the predecl's C++
    scope is the enclosing block and the name enters the caller's
    branch-local `declared` -- narrower than Python's function scope, but
    fail-safe (a read after the branch finds no binding and rejects).

    A resumable LEAF with (suspension-free body) lowers here too: targets
    are frame-resident (the FRAME_SLOT emplace / FRAME_FIELD assign arms),
    and an owned manager whose target
    field points into it takes the skeleton-declared `__with_ctx_<K>` frame
    home (`with_owned_ctx_map`) instead of the sync function-scope slot."""
    if stmt.is_async:
        raise ThirUnsupported(stmt_reject_reason(stmt))
    resumable = lc.func.is_generator or lc.func.is_async
    owned_ctx = None
    if resumable:
        owned_ctx = rcfg.resumable_state(lc.func).with_owned_ctx_map.get(
            stmt)
    hoists = lc.analyzer.if_branch_decls.get(stmt, {})
    # A with inside a branch / loop hoists here rather than at function top,
    # like the try sibling: the predecl is emitted at the with itself, and
    # the name enters the caller's branch-local `declared`.
    with_hoist = _Hoist(stmt, "with")
    _admit_hoists(with_hoist, hoists, declared, lc)
    items: list[THIRWithItem] = []
    for item_idx, item in enumerate(_bindings.scoped(lc, stmt.items)):
        ctx = item.context_expr
        arm_et = None
        if resumable and item.target is not None:
            # Frame-resident target, checked BEFORE the declared/pointer
            # rows: the name's storage IS the frame member, so the bind
            # writes through it and no local is declared. Frame families
            # beyond the slot / plain-field pair have no bind render here.
            et = item.enter_type
            if not isinstance(et, TpyType):
                raise ThirUnsupported(stmt_reject_reason(stmt))
            resolved = unwrap_readonly(unwrap_send_sync(et))
            if lc.binding(item.target).frame_slot:
                arm_et = (WithTargetArm.FRAME_SLOT, resolved)
            elif (lc.plain_frame_field(item.target)
                    and (_eligible_scalar(resolved)
                         or _eligible_char(resolved)
                         or _eligible_enum(resolved, lc.analyzer) is not None
                         or _resolved_str_value(resolved, lc.analyzer)
                         is not None
                         or _resolved_bytes_value(resolved, lc.analyzer)
                         is not None)):
                # Bare-read value families only (the sync VALUE arm's
                # eligibility): an Optional-enter target is also a plain
                # field for WRITES, but its reads deref through the
                # engaged optional -- a binding-kind registration this arm
                # does not make (probe-caught: the narrowed read rendered
                # bare).
                arm_et = (WithTargetArm.FRAME_FIELD, resolved)
            elif (lc.frame_local(item.target, FrameLocalClass.OPT_PTR)
                    and _optional_ptr_borrow(
                        lc.frame_local_types.get(
                            item.target, declared.get(item.target)),
                        lc.analyzer) is not None):
                # An Optional-enter POINTER field (`m: Box | None` -- the
                # `P* m;` frame member): the bind writes the bare pointer
                # (`m = __ctx_N.__enter__();`, the FRAME_FIELD render) and
                # reads ride the registered pointer-binding arms (null
                # tests + deref) -- the classification already seeded
                # a pointer record.
                arm_et = (WithTargetArm.FRAME_FIELD, resolved)
            elif (lc.frame_local(item.target, FrameLocalClass.REBIND_PTR)
                    or lc.frame_local(item.target,
                                      FrameLocalClass.ALIAS_PTR)):
                # A pointer-form field: a by-value enter result lands in
                # the item's own frame field (the ptr-slot prescan's), a
                # lent one is aimed at.
                if (not returns_cpp_reference_shape(item.enter_type)
                        and rcfg.ptr_slot_field(lc.func, item) is None):
                    note_detail("with.frame_pointer_field")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                # The pointer's pointee: ownership is the binding's fact.
                arm_et = (WithTargetArm.FRAME_POINTER, unwrap_own(resolved))
            elif lc.frame_local(item.target, FrameLocalClass.BORROW_TUPLE):
                # A borrow-form tuple frame field (`std::tuple<Item*,
                # int32>`): it is no plain frame field
                # because its DECL/reassign render differs, but the
                # with-bind is the plain member assign
                # (`p = __ctx_N.__enter__();`) like any other frame
                # field, and its reads ride the tuple_elem_ref unpack
                # rows.
                arm_et = (WithTargetArm.FRAME_FIELD, resolved)
            else:
                note_detail("with.frame_target_family")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        if arm_et is None:
            arm_et = _with_target_arm(item, declared, lc)
        if arm_et is None:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        arm, et = arm_et
        if item.manager_borrowed:
            manager_ok = (
                isinstance(ctx, TpyName) and ctx.name in declared
                and ctx.name not in lc.narrow.narrowed
                # A None-narrowed `A | None` name is a bare `A*` binding, so
                # the manager is the DEREF of it (`auto& __ctx_N = *(c);`).
                # Sema rejects the un-narrowed spelling outright ("cannot be
                # used as a context manager"), so reaching here IS the
                # non-null proof.
                and (record_like(declared[ctx.name], lc.analyzer)
                     or _optional_ptr_borrow_name(ctx, declared, lc.analyzer)
                     is not None))
            if (not manager_ok and isinstance(ctx, TpyFieldAccess)
                    and isinstance(ctx.obj, TpyName)
                    and ctx.obj.name not in lc.narrow.narrowed
                    and record_like(lc.analyzer.get_expr_type(ctx),
                                   lc.analyzer)):
                # A field-access lvalue manager (`with self.mgr:`): the
                # field read renders the lvalue the `auto&` bind borrows.
                # A PtrType field is not an F1 record, so the deref-manager
                # spelling cannot be reached from this row.
                manager_ok = True
                _witness("with.manager_borrowed_field")
            if not manager_ok and property_access_returns_cpp_ref(
                    lc.analyzer, ctx):
                # ... and its ACCESSOR spelling. A `@property` read that
                # hands back a reference into its receiver's storage IS that
                # field read one spelling over, so the `auto&` binds the same
                # lvalue; the row above keyed on the node KIND and a getter
                # read is a method call. Whether the RECEIVER survives the
                # statement is the WITH_MANAGER row's question, asked when the
                # manager lowers, not this gate's.
                manager_ok = True
                _witness("with.manager_borrowed_getter")
            if (not manager_ok
                    and isinstance(ctx, TpyMethodCall)
                    and call_returns_cpp_ref(lc.analyzer,
                                             ctx.resolved_function_info)
                    and not _expr_is_const_source(ctx, lc)
                    and not is_rvalue_source(lc.analyzer, ctx.obj)
                    and record_like(lc.analyzer.get_expr_type(ctx),
                                   lc.analyzer)):
                # ... and the plain METHOD spelling of the same convention
                # (`with b.guard_m() as q:`): a borrow-returning method hands
                # back the accessor's reference, so the `auto&` binds live
                # storage rather than a copy of it -- the getter row one
                # spelling over, keyed on the return convention the two
                # share. Two terms the getter row gets from elsewhere are
                # spelled here. A CONST result (declared `readonly[...]`,
                # or following a const receiver: `_expr_is_const_source`)
                # stays out: the bind would
                # deduce `const CM&` and the non-readonly
                # `__enter__`/`__exit__` would not compile through it. And
                # the RECEIVER must not be a temporary -- the term
                # `is_rvalue_source` applies to a getter read but concedes
                # for a plain method call
                # (BUGS.md#readonly-borrow-of-temporary-receiver), which
                # would leave `auto& __ctx_N = mk().guard_m();` naming a
                # destroyed object. A borrow-returning FREE call has no
                # receiver to ask that of and keeps rejecting.
                manager_ok = True
                _witness("with.manager_borrowed")
        else:
            manager_ok = (
                (isinstance(ctx, TpyCall)
                 and record_like(lc.analyzer.get_expr_type(ctx), lc.analyzer)
                 and (_ctor_shape_ok(ctx, lc.analyzer)
                      or _record_rvalue_source_shape(ctx, lc.analyzer)
                      or _native_ctx_manager_ok(ctx, lc.analyzer)))
                # A METHOD-call manager rvalue (`with m.lock() as g:` -- the
                # guard factory): the owned-record decl's method row; the
                # method lowering validates its receiver/args itself.
                or (isinstance(ctx, TpyMethodCall)
                    and record_like(lc.analyzer.get_expr_type(ctx),
                                   lc.analyzer)
                    and is_rvalue_source(lc.analyzer, ctx)))
        if not manager_ok:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # An OWNED manager whose already-declared target is READ after the
        # statement is kept alive past its block: the pointer slot aliases
        # `__enter__()`'s result, so the manager hoists to a function-scope
        # `std::optional<CM> __slot_N` and `__ctx_N` binds through it
        # (`_with_manager_needs_hoist`'s facts; the ASSIGN_PTR arm IS the
        # declared-and-not-optional case). The slot decl drains at the body
        # top, which at module-init scope spells the `static
        # __global_slot_N` lifetime the emit arm draws from the scope's own
        # prefix.
        frame_ctx_n = (owned_ctx[item_idx]
                       if owned_ctx is not None
                       and item_idx < len(owned_ctx) else None)
        mgr_hoist_cpp = None
        if (frame_ctx_n is None
                and not item.manager_borrowed and item.target is not None
                and arm is WithTargetArm.ASSIGN_PTR
                and item.manager_owns_enter_result
                and item.target_read_after):
            mgr_hoist_cpp = lc.render_type(
                unwrap_ref_type(lc.analyzer.get_expr_type(ctx)))
            _witness("with.manager_hoist")
        deref = (item.manager_borrowed and isinstance(ctx, TpyName)
                 and lc.binding(ctx.name).pointer)
        _witness("with.manager_borrowed" if item.manager_borrowed
                 else "with.manager_owned")
        if frame_ctx_n is not None:
            _witness("with.manager_frame_ctx")
        if deref:
            _witness("with.manager_deref")
        _witness({WithTargetArm.NONE: "with.no_target",
                  WithTargetArm.VALUE: "with.as_value",
                  WithTargetArm.REF: "with.as_ref",
                  WithTargetArm.PTR_DECL: "with.ptr_target",
                  WithTargetArm.ASSIGN_PTR: "with.ptr_target_reuse",
                  WithTargetArm.FRAME_SLOT: "with.frame_slot_target",
                  WithTargetArm.FRAME_FIELD: "with.frame_field_target",
                  WithTargetArm.FRAME_POINTER: "with.frame_pointer_target"}[arm])
        if (arm is WithTargetArm.FRAME_FIELD
                and lc.frame_local(item.target, FrameLocalClass.BORROW_TUPLE)):
            _witness("with.frame_btuple_target")
        if (arm is WithTargetArm.VALUE
                and _resolved_str_value(et, lc.analyzer) is not None):
            # The str-slice enter targets: the declared entry's resolved type
            # drives the body's `_str_name_form` classification (owned
            # `std::string` STORAGE vs `std::string_view` BORROW).
            _witness("with.str_target")
        if item.exit_can_suppress:
            _witness("with.suppress")
        if item.exit_takes_exc_val:
            _witness("with.exc_val")
        if not (item.exit_can_suppress or item.exit_takes_exc_val):
            _witness("with.cleanup_only")
        _bindings.require_entered(lc, item)
        items.append(THIRWithItem(
            binding=(lc.declaring(item.target)
                     if item.target is not None else None),
            ctx_expr=_strip_slot_leaf_deref(
                _lower_expr(ctx, lc, declared,
                            use=_ExprUse(pos=SinkPos.WITH_MANAGER)), lc),
            manager_borrowed=item.manager_borrowed,
            deref_manager=deref,
            target=item.target,
            target_arm=arm,
            can_suppress=item.exit_can_suppress,
            takes_exc_val=item.exit_takes_exc_val,
            target_cpp=(_with_ptr_target_cpp(item.target, et, lc)
                        if arm is WithTargetArm.PTR_DECL else None),
            manager_hoist_cpp=mgr_hoist_cpp,
            frame_ctx=frame_ctx_n,
            target_field=(rcfg.ptr_slot_field(lc.func, item)
                          if arm is WithTargetArm.FRAME_POINTER else None),
        ))
        if item.target is not None:
            declared[item.target] = et
    if len(stmt.items) > 1:
        _witness("with.multi")
    hoist_decls = _lower_hoist_predecls(with_hoist, hoists, declared, lc,
                                        "with.hoist_decl")
    n_returns_before = len(lc.nested_returns)
    # Sema declares every body name in front of the block except a
    # generator it keeps there, which nothing after the block reads. A
    # return the `__exit__` frame wraps stages its result first.
    with lc.staged_region(True):
        body = _lower_scoped_stmts(stmt.body, lc, dict(declared),
                                   branch_decls_ok=True,
                                   loop_depth=loop_depth)
    if resumable and len(lc.nested_returns) != n_returns_before:
        # A return nested in a resumable LEAF with renders through the ctx
        # return hook, which walks the skeleton's finally_stack -- this
        # with emit populates only its own finally_frames, so the __exit__
        # chain would be silently dropped. Reject until the hook walk is
        # covered.
        note_detail("with.leaf_return")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    return THIRWith(
        items=tuple(items),
        body=body,
        body_terminates=stmts_terminate(stmt.body),
        hoist_decls=tuple(hoist_decls),
        loc=loc,
    )

def _lower_print_star(a: TpyStarUnpack, lc: _LowerCtx,
                      declared: dict[str, TpyType],
                      pointers: AbstractSet[str], *,
                      temps_ok: bool) -> THIRPrintArg:
    """`print(*xs)`: the source lowers exactly as the positional `print(xs)`
    would -- the read a sequence printer borrows -- and each element takes
    the wrap a positional arg of the element's type gets."""
    source = _lower_print_arg(a.expr, lc, declared, pointers,
                              temps_ok=temps_ok)
    if (source.print_form not in (PrintForm.LIST, PrintForm.VARARGS)
            or source.deref):
        note_detail("print.star_source")
        raise ThirUnsupported("print.star_source")
    st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        lc.analyzer.get_expr_type(a.expr))))
    st = resolve_pending_container(st, lc.analyzer) or st
    elem_t = st.get_element_type() if isinstance(st, NominalType) else None
    if elem_t is None:
        note_detail("print.star_source")
        raise ThirUnsupported("print.star_source")
    elem_t = resolve_int_literals(unwrap_readonly(elem_t),
                                  lc.analyzer.ctx.default_int_for_literal)
    elem_form = _star_elem_print_form(elem_t, lc.analyzer)
    if elem_form is None:
        note_detail(
            f"print.star_elem.{_type_family_tag(elem_t, lc.analyzer)}")
        raise ThirUnsupported("print.star_elem")
    form, inner_cpp, fmt_cpp = elem_form
    _witness("print.star_arg")
    elem = THIRPrintArg(
        THIRName(name="__e", cpp="__e", result_type=elem_t), form,
        inner_cpp, opt_fmt_cpp=fmt_cpp)
    return THIRPrintArg(source.expr, PrintForm.EACH, star_elem=elem)


def _lower_print_arg(a: TpyExpr, lc: _LowerCtx,
                     declared: dict[str, TpyType],
                     pointers: AbstractSet[str], *,
                     temps_ok: bool = True) -> THIRPrintArg:
    """Lower one print arg + tag its `std::cout <<` wrapper form. A str literal
    lowers to a THIRStrLiteral (RAW: emitted via cpp_string_literal_expr); an
    eligible scalar lowers normally with its type-derived form. `temps_ok`
    is False inside a void-lambda body (the print chain is the closure
    body): a hoisted arg temp would flush at the ENCLOSING statement,
    outside the closure -- shapes that need one reject instead."""
    if isinstance(a, TpyStarUnpack):
        return _lower_print_star(a, lc, declared, pointers, temps_ok=temps_ok)
    if type(a) in _comprehensions._COMP_KINDS:
        # C3 comp print arg: the stmt-expr render inside its container
        # printer (List/Set/DictPrinter -- the container arms).
        _witness("comp.print_arg")
        form = {"list": PrintForm.LIST, "set": PrintForm.SET,
                "dict": PrintForm.DICT}[_comprehensions._COMP_KINDS[type(a)]]
        return THIRPrintArg(
            _comprehensions._lower_comprehension(
                a, lc.analyzer.get_expr_type(a), lc, declared, pointers), form)
    if isinstance(a, TpyStrLiteral):
        return THIRPrintArg(
            THIRStrLiteral(value=a.value, result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.RAW)
    if isinstance(a, TpyNoneLiteral):
        # `print(None)` -> the bare "None" string literal (the
        # NoneType-EXPRESSION flavor keeps rejecting).
        _witness("print.none_literal")
        return THIRPrintArg(
            THIRStrLiteral(value="None",
                           result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.RAW)
    if isinstance(a, TpyBytesLiteral):
        # A print sink threads no target, so the literal renders OWNED
        # (bytes_literal_owned / empty vector) inside the BytesPrinter wrap.
        return THIRPrintArg(
            THIRBytesLiteral(value=a.value, result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.BYTES)
    opt = _print_optval_opt(a, lc.analyzer, declared)
    if opt is not None:
        # A value-repr Optional[scalar/str] read -> the bare optional
        # (`p` / `this->fi`, no deref) inside a `::tpy::print_optional_val(...)`
        # wrap (the value-repr Optional arm). A NARROWED name's
        # deref-on-narrow is stripped -- the wrap takes the WHOLE optional
        # regardless of narrowing (params and locals alike, probe-verified).
        _witness("print.optval")
        form, inner_cpp, fmt_cpp = _print_optval_form(
            opt, a, {n: t for n, t in lc.params if isinstance(t, TpyType)})
        if fmt_cpp is not None:
            _witness("print.optval_fmt")
        lowered = _lower_expr(
            a, lc, declared, allow_whole_optional=True,
            field_prechecked=isinstance(a, TpyFieldAccess))
        if isinstance(lowered, THIRName) and lowered.deref:
            lowered = replace(lowered, deref=False)
        # A NARROWED Optional field likewise wraps the WHOLE optional storage
        # (the wrap keys on the declared field type), so the value-position
        # narrowed deref is stripped.
        if isinstance(lowered, THIRFieldAccess) and lowered.narrowed_deref:
            lowered = replace(lowered, narrowed_deref=False)
        return THIRPrintArg(lowered, form, inner_cpp, opt_fmt_cpp=fmt_cpp)
    if (_lpo := _optional_ptr_borrow_wide_name(a, declared,
                                               lc.analyzer)) is not None:
        # A ptr-repr Optional NAME (WIDE pointee class): the bare pointer
        # inside `::tpy::print_optional(...)` (the pointer-repr arm)
        # -- CTAD when the inner streams itself, kind-keyed template args
        # when it needs a Formatter. DECLARED-type keyed -- a None-narrowed
        # occurrence still wraps, so the narrowed deref is stripped like the
        # value-opt row's.
        _witness("print.opt_ptr_name")
        lowered = _lower_expr(a, lc, declared, allow_whole_optional=True)
        if isinstance(lowered, THIRName) and lowered.deref:
            lowered = replace(lowered, deref=False)
        _pf, _pi, _pfmt = _print_optptr_form(_lpo)
        return THIRPrintArg(lowered, _pf, _pi, opt_fmt_cpp=_pfmt)
    if (isinstance(a, (TpyCall, TpyMethodCall))
            and (_lpc := _optional_ptr_borrow_wide(
                lc.analyzer.get_expr_type(a), lc.analyzer)) is not None):
        # A ptr-repr Optional-RETURNING call (`print(find(points, 3))` ->
        # `::tpy::print_optional(find(..))`): the borrow-returning call's
        # bare `T*` result is exactly what the wrap consumes -- the same
        # pass-through admission the ptr-opt DECL bind threads.
        _witness("print.opt_ptr_call")
        _cf, _ci, _cfmt = _print_optptr_form(_lpc)
        return THIRPrintArg(
            _lower_expr(a, lc, declared,
                        use=_ExprUse(pos=SinkPos.PRINT_ARG,
                                     allow_temps=temps_ok)),
            _cf, _ci, opt_fmt_cpp=_cfmt)
    # A container / value-tuple / F1-record NAME: the kind-keyed printer
    # wrap (or the record's raw operator<<) around the bare name -- the
    # same routing fact lowering consumed (`_wrap_print_form`).
    if (isinstance(a, TpyName) and lc.self_receiver is not None
            and a.name == lc.self_receiver and lc.record_name is not None
            and record_like(lc.analyzer.get_expr_type(a), lc.analyzer)):
        # `print(self)` streams the record raw via its emitted operator<<:
        # a VALUE position, so the receiver read arrives dereferenced.
        return THIRPrintArg(_lower_expr(a, lc, declared), PrintForm.RAW)
    wrap = _wrap_print_form(a, declared, lc.analyzer, pointers)
    if wrap is not None and isinstance(a, (TpyCall, TpyMethodCall)):
        # A container-returning CALL wraps the inline call render; STORAGE
        # use admits the container-return call shapes (the storage-sink
        # gate), and a print is a flush position, so arg temps may hoist.
        # A DICT-VIEW method result (`print(d.keys())` ->
        # `::tpy::dict_keys(d)` streamed raw) rides the ITERABLE result
        # family instead -- the same override the for-loop grants it; the
        # STORAGE gate says nothing about view returns.
        at = lc.analyzer.get_expr_type(a)
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        # `forms=_LEND_OK` is the sink's verdict, not the row's: the printer
        # wrap reads the container inside the print expression, so a result
        # lending a dying receiver's storage is still alive where it streams.
        arg_use = (_ExprUse(result=_ExprResultUse.ITERABLE,
                            pos=SinkPos.PRINT_ARG, forms=_LEND_OK)
                   if atu is not None and is_dict_view(atu)
                   else _ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.PRINT_ARG, forms=_LEND_OK,
                                 allow_temps=temps_ok))
        return THIRPrintArg(
            _lower_expr(a, lc, declared, use=arg_use),
            wrap)
    if wrap is not None:
        # Every printer-wrap arm renders a plain value read, so a NAME
        # arg lowers with the full-deref use: inert for value bindings, the
        # `(*al)` deref for the pointer-bound record/container names the
        # gate admits.
        if (isinstance(a, TpyName) and wrap is PrintForm.RAW
                and lc.binding(a.name).pointer):
            _witness("print.record_name")
        if isinstance(a, TpyTupleLiteral):
            # The pointer-repr tuple literal renders borrow-form inside
            # TuplePrinter (pending elements resolved before spelling,
            # like the compare pair).
            at = lc.analyzer.get_expr_type(a)
            atu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            resolved_t = resolve_tuple_pending(atu, lc.analyzer)
            if _print_tuple_opt_ternary(a, atu, lc.analyzer) is not None:
                # An Optional-TERNARY element: the family predicate admits
                # nothing but VALUE element slots beside it, so the slot
                # ladder gives every element VALUE mode and the literal takes
                # the storage spelling whatever the other elements' value
                # category is -- the borrow ladder has no arm to pick here.
                _witness("print.opt_ternary_tuple_arg")
                return THIRPrintArg(
                    _lower_tuple_literal(a, resolved_t, lc, declared), wrap)
            if a.elements and all(is_rvalue_source(lc.analyzer, el)
                                  for el in a.elements):
                # The print sink threads no target, so the slot ladder
                # splits on the ELEMENT: a simple lvalue takes the ref slot
                # (`std::tuple<Both*, ReprOnly*>{&(b), &(r)}`), an rvalue the
                # storage one. All-rvalue is the storage half; keying the arm
                # on anything coarser turns that borrow spelling into a copy.
                lowered = _lower_tuple_literal(a, resolved_t, lc, declared)
                _witness("print.tuple_literal_storage_arg")
                return THIRPrintArg(lowered, wrap)
            _witness("print.tuple_literal_arg")
            return THIRPrintArg(
                _lower_borrow_tuple_literal(a, resolved_t, lc, declared),
                wrap)
        # A module-variable container arg lowers through the module-var
        # arm directly: the printer wrap is the pinned consumer that makes
        # the pointer-slot `(*slot)` read legal here, and the generic value
        # use below cannot say so.
        _pa_mv = _module_var_access_pair(a, declared, lc.analyzer)
        if _pa_mv is not None:
            return THIRPrintArg(
                _lower_module_var(a, lc.analyzer.get_expr_type(a), lc,
                                  *_pa_mv, loc=getattr(a, "loc", None),
                                  allow_ref_pointer=True),
                wrap)
        # A container-ELEMENT tuple read (`print(pairs[0])`) hands its
        # lvalue to TuplePrinter whole -- the borrow-bind consumption the
        # element gate keys on; every other wrap arg keeps the value use.
        # A `forms=` replaces the sink's row, so the print sink's own lend
        # verdict is restated on every row: the printer reads the value
        # inside the statement.
        print_position = _ExprUse(pos=SinkPos.PRINT_ARG, forms=_LEND_OK,
                                  allow_temps=temps_ok)
        if wrap is PrintForm.TUPLE and isinstance(a, TpySubscript):
            wrap_use = replace(print_position,
                               result=_ExprResultUse.BORROW_BIND)
        else:
            wrap_use = replace(print_position, pos=SinkPos.PRINT_ARG,
                               forms=_ONLY_INDIRECT_READ | _LEND_OK)
        # The union STR row is DECLARED-type keyed (`::tpy::__str__(a)`
        # consumes the whole variant), so an assign-narrowed divergent
        # read is safe here -- the sink never sees the member type.
        _str_union_name = (
            isinstance(a, TpyName) and wrap is PrintForm.STR
            and isinstance(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared.get(a.name)))), UnionType))
        lowered = _lower_expr(
            a, lc, declared,
            use=wrap_use,
            field_prechecked=isinstance(a, TpyFieldAccess),
            allow_union_divergent=_str_union_name)
        if isinstance(a, TpyArrayLiteral) and isinstance(
                lowered, THIRContainerLiteral):
            # The literal wraps its typed brace-init -- ListPrinter's CTAD
            # cannot deduce from a bare brace list (the explicit-type arm).
            at = lc.analyzer.get_expr_type(a)
            resolved = resolve_pending_container(at, lc.analyzer) or at
            _witness("print.container_literal_arg")
            lowered = replace(lowered,
                              typed_brace_cpp=lc.render_type(resolved))
        return THIRPrintArg(lowered, wrap)
    # resolve_int_literals: an IntLiteral-typed arg (a literal-seeded container's
    # loop var / pop result) must derive its stream form from the resolved type.
    arg_type = resolve_int_literals(
        unwrap_readonly(lc.analyzer.get_expr_type(a)),
        lc.analyzer.ctx.default_int_for_literal)
    # A print statement is a flush position (arg temps hoist before the
    # statement), so temp-hoisting arg rows admit here. A print
    # sink is a raw `<<` position, so an owned-str FIELD read streams bare --
    # the same admission the f-string arg site grants (field_owned_str_ok).
    # An F1-record-returning call arg (print.record_call) threads BORROW_BIND
    # so the call's record result gate admits the rvalue.
    # A print arg is target-less, so a both-literal binop folds. The
    # record-call branch names another position and so drops the fold --
    # fine while the two shapes stay mutually exclusive (a record-call arg
    # is never a both-literal int binop); revisit if that ever changes.
    # A print argument is streamed inside the print statement, so every one of
    # these arms carries the dying-source admission on top of its own verdict.
    use = _ExprUse(allow_temps=temps_ok, pos=SinkPos.PRINT_ARG,
                   forms=_LEND_OK)
    if _print_tuple_record_elem(a, declared, lc.analyzer):
        # A pointer element read hands back the POINTER and print is a
        # value position, so the referent streams; an inline one already
        # IS the value.
        elem = _lower_expr(a, lc, declared,
                           use=replace(use, result=_ExprResultUse.BORROW_BIND),
                           subscript_prechecked=True)
        return THIRPrintArg(elem, PrintForm.RAW, deref=elem.reads_raw_pointer)
    if _record_call_rvalue_operand(a, lc.analyzer):
        use = replace(use, result=_ExprResultUse.BORROW_BIND)
    elif isinstance(a, (TpyFieldAccess, TpySubscript, TpyCall,
                        TpyMethodCall)):
        at = lc.analyzer.get_expr_type(a)
        atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
               if at is not None else None)
        if record_like(atu, lc.analyzer):
            # The record-FIELD / record-ELEMENT / record BORROW-call print
            # arg (print.record_field / print.record_subscript /
            # print.record_borrow_call): the bare member read, checked
            # getitem or borrowed result streams RAW, so it lowers under
            # BORROW_BIND like the record-call row.
            use = replace(use, result=_ExprResultUse.BORROW_BIND)
    return THIRPrintArg(
        _lower_expr(a, lc, declared, use=use,
                    field_owned_str_ok=isinstance(a, TpyFieldAccess)),
        _print_arg_form(arg_type))


