"""Expression lowering and admission for `_lower_expr` and its recursive arms.

Composite consumer-shape helpers live in `checks.py`; `_lower_expr` invokes them
only from the node arm being lowered.
"""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import field, fields as dataclass_fields, replace
from .callables import resolved_callee
from .storage import direct_field, global_name_binding, module_global_binding, optional_layout, tuple_layout, union_layout
from ... import qnames
from ...parse.nodes import (
    FSTRING_CONV_NONE,
    FSTRING_CONV_REPR,
    FSTRING_CONV_STR,
    FunctionLinkage,
    SourceLocation,
    TpyArrayLiteral,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyChainedCompare,
    TpyCoerce,
    TpyDictComprehension,
    TpyDictLiteral,
    TpyExpr,
    TpyGeneratorExpression,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyFString,
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
    TpyStarUnpack,
    TupleElemCapture,
    TpyTupleLiteral,
    TpyTypeParamConstruct,
    TpyUnaryOp,
    TpyVarargPack,
)
from ...typesys import (
    AliasRef,
    RecursiveAliasInstanceType,
    AnyType,
    RefType,
    BOOL,
    BYTES_FAMILY,
    CallableType,
    CHAR,
    FloatLiteralType,
    INT32,
    IntLiteralType,
    ListRepeatType,
    LiteralType,
    NominalType,
    NONE,
    OptionalType,
    OwnType,
    PendingListType,
    PendingViewType,
    collapse_tuple_own_elements,
    contains_pending_leaf,
    PtrType,
    ReadonlyType,
    is_readonly_ref_param,
    STR_FAMILY,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    ValueForm,
    VoidType,
    contains_type_param,
    is_any_str_type,
    is_dyn_protocol,
    is_protocol_type,
    is_void_like_type,
    make_array,
    make_dict,
    make_list,
    resolve_int_literals,
    substitute_type_params_simple,
    unwrap_optional_own,
    unwrap_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    enum_info_of,
    is_array,
    is_big_int_type,
    is_bool_type,
    is_bytes_type,
    is_bytes_view_type,
    is_bytearray_type,
    is_dict,
    is_float32_type,
    is_fixed_int_type,
    is_list,
    is_span,
    is_spanlike_view,
    is_str_type,
    is_str_view_type,
    is_string_type,
    is_set,
    is_varargs,
    view_to_owned_conv,
)
from ...codegen_cpp import emit_prims
from ...codegen_cpp.types import TypeResolver, resolve_pending_container
from ...modules.defs import BINOP_TO_METHOD
from ...modules.type_resolution import (
    get_iterable_element_type,
    is_native_iterable,
)
from ...codegen_cpp.context import (
    enum_cpp_name,
    escape_cpp_name,
    free_callee_cpp,
    qualified_cpp_name,
    qualify_native_name,
    view_key_target,
)
from ...codegen_cpp.protocols import (dynamic_adapter_type,
                                      dynamic_base_name,
                                      dynamic_ref_adapter_type,
                                      narrow_cast_rhs,
                                      record_inherits_dynamic)
from ...typesys import (
    polymorphic_source_inner,
    polymorphic_source_is_pointer,
)
from ...symbol_binding import SymbolKind, lookup_imported
from ...coercions import wrap_into_any, CoercionContext
from ...compilation_context import get_current_compiler
from ...value_category import (
    frame_factory_callee, call_returns_cpp_ref, is_rvalue_source,
    materializing_temp_source,
)
from ..reject import (ThirUnsupported, call_reject_reason, expr_kind_tag,
                        note_detail)
from ..faces import witness as _witness
from .arg_table import (_ArgReq, _ArgRow, _ArgSink, arg_ok, register_sink)
from ...sema.literal_utils import literal_value_from_expr
from ...codegen_cpp.int_literals import render_int_literal_value
from ..nodes import (
    Form,
    TruthinessMode,
    THIRArgTemp,
    THIRNode,
    THIRBinOp,
    THIRChainedCompareStmtExpr,
    THIRBytesLiteral,
    THIRPrintChain,
    THIRCall,
    THIRCharLiteral,
    THIRClassConstant,
    THIRCoerce,
    THIRDynIsinstanceMulti,
    THIRContainerLiteral,
    THIRCtorCall,
    THIREnumMember,
    THIREnumWrap,
    THIRErrorReturnUnwrap,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRIfExpr,
    THIRIsinstance,
    THIRFString,
    THIRFStringArg,
    THIRIsNone,
    THIRLambda,
    THIRMembership,
    THIRStrMembership,
    THIRTupleMembership,
    THIRTruthy,
    THIROptViewArg,
    THIROwnOptRebuild,
    THIRListRepeat,
    THIRCopy,
    THIRDefaultConstruct,
    THIRLiteral,
    THIRMethodCall,
    THIRModuleVar,
    THIRDecayCopy,
    THIRMove,
    THIRName,
    THIROptionalRead,
    THIRUnionLayout, THIRUnionTest, THIRUnionExtraction,
    THIRWalrus,
    THIRValueSelect,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRSelf,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRBorrowTupleLiteral,
    THIRTupleValueToBorrow,
    THIRTupleLiteral,
    THIRUnaryNot,
    THIRUnaryArith,
    THIRUnionArgLift,
    THIRVarargPack,
)
from ...codegen_cpp.forms import (is_plain_nonvalue, is_ptr_variant_union,
                                  reads_storage_form_optional)
from .predicates import (
    _poly_narrow_info,
    _comp_shadow_pointers,
    _btuple_owning_call_init,
    _empty_instantiation_family,
    _tuple_field_opt_elem_subscript,
    _tuple_local_ptr_elem_subscript,
    _template_positional_indices,
    _tparam_protocol_field_recv_ok,
    _tparam_protocol_field_over_field_ok,
    _call_ret_union_ok,
    _ptr_opt_borrow_call_ret,
    _BIGINT_LIT_COERCION,
    _BIGINT_NARROW,
    _eligible_ptr_union,
    _union_elem_tuple,
    _open_slot_match,
    _own_opt_storage_binding,
    _eligible_ptr_union_wide,
    _eligible_wrapper_union,
    _module_var_recv,
    _param_is_const,
    _const_verdict_func,
    _param_is_deep_const,
    _ARITH_OPS,
    _BITWISE_OPS,
    _COMPARE_OPS,
    _FLOAT32_LIT_COERCION,
    _INT_LIT_COERCION,
    _IS_OPS,
    _LOGICAL_OPS,
    _MEMBERSHIP_OPS,
    _arg_ptr_union_slot,
    _union_bytes_literal_temp_arg,
    _union_dict_literal_temp_arg,
    _bare_module_recv,
    _module_var_access_pair,
    _bigint_index_disposition,
    _binop_operand_casts,
    _bytes_compare_operand,
    _bytes_concat_operand,
    _char_compare_operand,
    _bytes_name_form,
    _class_const_pure_receiver,
    _class_constant_cpp,
    _coerce_disposition,
    _coerce_wrap,
    _container_nocopy_elem,
    _container_record_elem,
    _container_ref_alias_elem,
    _union_storage_val_cpp,
    _container_scalar_read,
    _container_value_leaf_read,
    _container_opt_record_elem,
    _container_value_optional_elem,
    _const_index,
    _cpp_noncopyable_type,
    _ctor_arg_slot_ok,
    _int_type_param_value,
    _dict_view_iterable_ok,
    _eligible_char,
    _field_decl_type,
    _genfac_like_call,
    _inst_slice_arg_ok,
    _isinstance_narrow_info,
    _eligible_enum,
    _eligible_ptr_value,
    _eligible_scalar,
    _enum_compare_pair,
    _enum_member_cpp,
    _enum_neg_wrap,
    _enum_prop_wrap,
    _enum_truthy_wrap,
    _plain_enum_truthy,
    _opt_record_dunder,
    _opt_record_inner,
    _ptr_truthy_source,
    _storage_opt_record_truthy,
    _native_cond_scalar,
    _truthiness_mode,
    _f1_const_rooted_source,
    _walrus_src_is_const,
    _plain_container_read,
    _container_field_bare_read,
    _method_member_cpp,
    _bytes_family_ref,
    _const_default_param_form,
    _f1_container_ref,
    _f1_record,
    _f1_ref,
    _f1_tuple,
    _union_member_match,
    frame_temp_arg_slot,
    _field_read_ref_ctor_arg,
    _field_over_global_record_ok,
    _field_over_subscript_ok,
    _field_markers_clean,
    _plain_record_field_link,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    _tuple_literal_has_ref_elements,
    _tuple_elem_slots_ptr_optional,
    _tuple_elem_slots_record_lvalue,
    _mixed_own_borrow_tuple,
    _mixed_own_storage_source,
    _renders_own_borrow_tuple,
    _mixed_own_btuple_call,
    _wrapper_ref_tuple_return,
    _ptr_optional_tuple,
    _unbound_self_field_ok,
    _ptr_value_field_recv_ok,
    _subscript_field_recv_ok,
    _ptr_value_none_field,
    _user_deref_field_recv_ok,
    _user_deref_method_call_ok,
    _typed_dict_recv_ok,
    _user_deref_stub_method_ok,
    _global_record_recv,
    _folded_neg_int_literal,
    _generic_root_subst,
    _instantiation_call_fi,
    _native_iter_value_slot,
    _unwrap_own,
    _unown_type_args,
    _own_record_tuple,
    _open_t_tuple_slot,
    _protocol_auto_slot,
    _is_borrow_form_name,
    _is_type_param_slot,
    _is_range_call,
    _native_iterable_range_arg,
    _is_none_compare_operand,
    _value_opt_rvalue,
    _narrowed_opt_field_read,
    _narrowed_opt_container_field,
    _ADDR_PTR_COERCIONS,
    _INDIRECT_DEREF_COERCIONS,
    _PTR_IDENTITY_COERCIONS,
    _SPANLIKE_COERCIONS,
    _SPAN_METHOD_COERCIONS,
    _VIEW_TARGET_STR_COERCIONS,
    _is_string_owned,
    _mixed_sign_compare,
    _module_var_read_cpp,
    _nonvalue_container_ret,
    _narrow_bigint_index,
    _optional_ptr_arg_face,
    _nullable_static_protocol_param,
    _nullable_protocol_slot,
    _protocol_union_arg,
    _own_declared_call_ret,
    _own_storage_opt_param,
    _record_class_binding,
    _already_pointer_source,
    _const_borrow_name,
    _poly_isinstance_value_info,
    _poly_subject_decl,
    _poly_subject_readonly,
    _optional_ptr_arg_slot,
    _opt_pointee_wide,
    _optional_ptr_borrow,
    _optional_ptr_borrow_wide,
    _optional_ptr_borrow_wide_name,
    _storage_optional_return_wide,
    _optional_ptr_borrow_name,
    generic_opt_trait_name,
    _optional_checked_field,
    _optional_checked_field_over_call_ok,
    _optional_checked_recv_call,
    _optional_field_over_container_subscript_ok,
    _optional_field_over_subscript_ok,
    _optional_checked_field_over_field_ok,
    _field_over_record_getitem_ok,
    _record_getitem_idx_recv_ok,
    _own_bytes_identity_move_slot,
    _own_lvalue_temp_slot,
    _declared_type,
    _narrow_key_type,
    _NARROW_UNMIRRORED,
    _peel_coerce,
    _type_family_tag,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_or_opt_own_slot,
    _plain_own_slot,
    _own_proto_container_slot,
    _union_elem_value_tuple,
    _protocol_arg_slot,
    _protocol_arg_temp,
    _bounded_tparam_protocol,
    _protocol_binding,
    _protocol_subscript_recv,
    _range_counter_type,
    _record_call_rvalue_operand,
    _record_rvalue_temp_slot,
    _resolve_pending_view,
    _resolve_tuple_pending,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _resolved_viewfam_value,
    _runtime_bigint,
    _slice_object_type,
    _span_value,
    _str_compare_operand,
    _str_concat_operand,
    _strview_coerce_name,
    _bytes_view_family_value,
    _str_name_form,
    _storage_call_container,
    _container_rebind_call_ret,
    _container_storage_return_call_ret,
    _nested_owned_tuple_call_ret,
    _owned_tuple_call_ret,
    _owned_tuple_call_source,
    _storage_call_ret,
    _storage_optional_return_type,
    _record_getitem_key,
    _subscript_index_and_tuple,
    _subscript_container_recv_type,
    _container_elem_lvalue_subscript,
    _narrowed_ptr_opt_name,
    _template_init_call_fi,
    _view_ctor_bare_source,
    _array_literal_ctor_source,
    _container_literal_inst_slot,
    _inst_call_container_arg,
    _span_ctor_call_fi,
    _viewfam_ctor_call_fi,
    _tuple_container_elem_read,
    _tuple_subscript_container_elem_read,
    _tuple_subscript_value_read,
    _subscript_recv_tuple,
    _tparam_value,
    _opt_view_arg_shim,
    _none_value_opt_arg,
    _callable_value,
    _value_opt_callable,
    _value_opt_view_whole_arg,
    _value_opt_call_ret_arg,
    _value_opt_member_arg,
    _value_opt_scalar,
    _value_opt_span,
    _value_opt_tuple,
    _narrowed_value_opt_tuple_read,
    _value_opt_tuple_pass_arg,
    _view_inner_value_opt,
    _value_opt_string_owned,
    _value_opt_value_record,
    _dict_view_call_result,
    _native_iterable_call_arg,
    _native_iterable_source,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_bytes,
    _value_opt_view,
    _value_opt_owned_view,
    _open_value_tuple,
    _value_tuple,
    _value_tuple_needle_ok,
    _eligible_value_union,
    _value_tuple_element_ok,
    _value_tuple_nested,
    _opt_ternary_tuple_arm_ok,
    _storage_opt_ternary_result,
    _tuple_compare_pair,
    _ptr_tuple_field_compare_pair,
    _ptr_tuple_literal_compare_pair,
    _resolve_pending_tuple_elems,
    _value_tuple_global,
    _value_tuple_return,
    _generic_value_tuple_return,
    _tuple_has_own_element,
    _ru_container_literal_ok,
    _dyn_borrow_return,
    _own_dyn_return,
    _own_genrec_return,
    _wrapper_borrow_return,
    _wrapper_union_like,
    _wrapper_value_return,
    _ru_wrapper_borrow_call_arg,
    _ru_wrapper_own_call_arg,
    _ru_wrapper_own_literal_arg,
    _ru_wrapper_value_call_arg,
    _ru_instance_literal_ok,
    _ru_wrapper_member_name_arg,
    _ru_wrapper_field_arg,
    _ru_wrapper_name_arg,
    _ru_wrapper_member_rvalue_arg,
    _ru_wrapper_scalar_literal_arg,
    _ru_wrapper_arg_slot,
    _value_union_temp_slot,
    _union_binding_divergent,
    _any_compare_pair,
    _any_none_subject,
    _union_none_name,
    _union_none_field,
    _container_compare_pair,
    _ptr_union_compare_pair,
    _ptr_union_const_wrap,
    _union_compare_pair,
    _record_compare_pair,
    _record_dunder_operand_pair,
    _record_dunder_operand_slot,
    _bytes_literal_view_slot,
    _unrouted_binding_read,
    _unwrap_lit_coerce,
    _value_opt_view_name,
)
from .context import (_call_arg_forms, _ExprResultUse, _ExprUse, _LowerCtx,
                      _recv_forms, _NO_FORMS,
                      _ONLY_BTUPLE_SLOT,
                      _ONLY_FIELD_RECV_BORROW, _ONLY_INDIRECT_READ,
                      _ONLY_PTR_OPT_PASSTHROUGH, _ONLY_TUPLE_SOURCE,
                      _RecordCtorUse, CallArgKind,
                      SinkForm, SinkPos, ValueOptKind)
from .generics import expand_fi_template


_NESTED_ARG_USE = _ExprUse(record_ctor=_RecordCtorUse.NESTED_ARG)
_RECORD_TEMP_USE = _ExprUse(record_ctor=_RecordCtorUse.RECORD_TEMP)
# A record-rvalue temp whose SOURCE ctor is itself at the enclosing statement's
# flush point (`describe(Canvas(Circle(5)))` -- the outer free call flushes, so
# the inner Canvas ctor's own arg temps land at the SAME point, innermost-first).
# allow_temps threads through so the nested ctor loop can hoist its member-ctor
# temps (`Circle __tmp_1`); only reached from a position already flushing.
_RECORD_TEMP_FLUSH_USE = _ExprUse(record_ctor=_RecordCtorUse.RECORD_TEMP,
                                  allow_temps=True)


from .checks import (
    _is_move_source_facts,
    _own_opt_ptr_name_move_arg_facts,
    _r_own_opt_ptr_name_move,
    _r_opt_own_ptr_opt_name_move,
    _r_container_comp,
    _r_own_container_comp,
    _r_record_elem_subscript,
    _own_container_comp_arg,
    _own_container_comp_slot,
    _container_module_var_arg,
    _x_temps_ok,
    _r_callable_value_pass,
    _r_container_literal,
    _r_copy_own,
    _r_dyn_own_conformer,
    _r_func_ref,
    _r_lambda,
    _r_none_unit,
    _r_none_value_opt,
    _r_opt_own_record_name,
    _r_optional_ptr,
    _r_own_container_literal,
    _r_own_lvalue,
    _r_own_move,
    _r_own_optional_record_rvalue,
    _r_own_record_rvalue,
    _r_own_tparam_call_rvalue,
    _r_own_union_ctor,
    _r_bytes_literal_value_opt,
    _r_ptr_pass_through,
    _r_shared_pass_through,
    _r_str_literal_value_opt,
    _r_str_owned_slot,
    _r_str_pass_through,
    _r_tuple_literal,
    _r_tuple_literal_value_opt,
    _r_union_coerced_literal,
    _r_union_ctor_temp,
    _r_union_member_lift,
    _r_union_pass_through,
    _r_value_opt_member,
    _r_value_opt_scalar_value,
    _r_value_record_rvalue,
    _r_value_union_temp,
    _r_value_union_narrowed_pass,
    _builtin_value_record,
    _wrapper_union_elem_name_arg,
    _alias_ref_container,
    _container_record_elem_subscript,
    _storage_form_tuple_return,
    _container_field_pass_arg,
    _value_tuple_field_pass_arg,
    _coro_factory_structural_arg,
    _deref_coerce_arg,
    _iter_rvalue_structural_arg,
    _record_rvalue_structural_arg,
    _opt_own_record_name_arg,
    _opt_own_container_name_arg,
    _opt_own_ptr_opt_name_arg,
    _readonly_container_rvalue_arg,
    _record_rvalue_source_shape,
    copy_construct_source,
    copy_construct_form,
    copy_call_arg,
    _optional_ptr_container_slot,
    _owned_str_slot,
    _optional_ptr_container_arg,
    _optional_ptr_container_literal_arg,
    _FSTRING_INELIGIBLE,
    _call_arity_ok,
    _call_ret_reject,
    _container_lit_elem_ok,
    _container_literal_arg,
    _container_literal_method_arg,
    _container_storage_call_rvalue,
    _own_container_construct_arg,
    _own_container_instantiation_arg,
    _ref_param_dictset_literal_arg,
    _stub_method_arg_ok,
    _stub_method_ret_ok,
    _native_iterable_iterator_call_arg,
    _comp_own_container,
    _native_iterable_comp_arg,
    _stub_comp_target,
    _native_iterable_literal_arg,
    _native_protocol_field_arg,
    _whole_value_opt_field_arg,
    _native_protocol_tuple_literal_arg,
    _proto_tuple_elem_borrow,
    _borrow_tuple_field_arg,
    _borrow_tuple_subscript_arg,
    _borrow_tuple_local_type,
    _borrow_tuple_storage_name_arg,
    _mixed_own_tuple_name_arg,
    _borrow_tuple_name_arg,
    _union_elem_tuple_name_arg,
    _container_comp_arg,
    _record_borrow_call_arg,
    _recursive_union_borrow_call_arg,
    _own_opt_ptr_name_arg,
    _record_elem_subscript_arg,
    _record_getitem_rvalue_arg,
    _record_field_ref_arg,
    _container_lit_slot_family,
    _container_literal_shape_ok,
    _ctor_instantiation_ok,
    _ctor_effective_params,
    _ctor_shape_ok,
    _non_ctor_call,
    _dyn_own_coro_factory_arg,
    _dyn_own_conformer_arg,
    _covariant_temp_arg,
    _dyn_own_handle_arg,
    _field_over_call_ok,
    _field_over_property_call_ok,
    _field_over_binop_ok,
    _field_over_walrus_ok,
    _field_over_container_subscript_ok,
    _chained_subscript_recv_type,
    _func_ref_routable,
    _copy_own_arg,
    _copy_open_elem_arg,
    _lambda_reject_reason,
    _subscript_over_container_subscript_ok,
    _field_over_field_ok,
    _free_callee_kind,
    _fstring_arg_wrap,
    _fstring_container_call_arg,
    _generic_arg_slot,
    _generic_plain_arg_ok,
    _raw_record_ctor_fi,
    _is_len_call,
    _is_len_native,
    _iter_proto_call_ret,
    _marker_call_kind,
    _marker_call_supported,
    _marker_reject,
    _module_qual_ctor_shape,
    gen_recv_ctor_temp,
    _member_gen_call_iterable_ok,
    _method_call_arg_ok,
    _opt_strview_to_str_own_elem_arg,
    _opt_view_param_own_elem_arg,
    _wrapper_ref_tuple_elem_arg,
    _method_recv_family,
    _none_unit_arg,
    _raw_record_method_fi,
    _tparam_name_pass_arg,
    _tparam_slot_temp_arg,
    _method_nonname_receiver_ok,
    _method_receiver_type,
    _native_call_arg_ok,
    _container_slot_call_rvalue_arg,
    _native_container_call_arg,
    _native_record_call_arg,
    _borrow_ret_record_marker_arg,
    _required_protocol_union_slot,
    _union_ctor_temp_arg,
    _own_record_rvalue_arg,
    _own_slot_payload,
    _own_tuple_call_rvalue_slot,
    _own_union_call_pass_arg,
    _own_union_storage_name_arg,
    _own_dyn_method_rvalue_ok,
    _protocol_slot_arg,
    _plain_call_arg_ok,
    _native_record_recv,
    _ptr_deref_method_call,
    _ptr_deref_recv_ok,
    _ptr_native_member_core,
    _ptr_template_call_recv_ok,
    _ptr_template_method_supported,
    _record_rvalue_call_shape,
    _native_own_record_rvalue_call_shape,
    _native_record_rvalue_call_shape,
    _template_record_rvalue_call_shape,
    _er_record_rvalue_call_shape,
    _record_rvalue_temp_arg,
    _method_rvalue_f1_record,
    _typed_dict_ctor_call,
    _native_ctx_manager_ok,
    _open_tparam_param_read,
    _open_tparam_storage_slot,
    _str_owned_slot_arg,
    _bytes_owned_slot_arg,
    _own_bytes_literal_arg,
    _own_str_literal_arg,
    _str_pass_through_arg,
    _strlit_method_pin_arg,
    _strlit_overload_pin_arg,
    _strlit_overload_pin_fires,
    _subscript_elem_reject,
    _subscript_recv_reject,
    _str_aug_append_ok,
    _str_list_method_iterable_ok,
    free_literal_mangled_name,
    method_literal_mangled_cpp,
    _record_method_call_supported,
    _recv_own_dyn,
    _recv_shape_reject,
    _value_tuple_pass_through_arg,
)


_COPY_SRC_USE = _ExprUse(result=_ExprResultUse.BORROW_BIND)


def _lower_copy_record(e: TpyExpr, lc: '_LowerCtx',
                       declared: dict[str, TpyType], *,
                       slot_type: 'TpyType | None' = None,
                       exact: bool = False,
                       pointers: 'set[str] | None' = None,
                       loc: 'SourceLocation | None' = None
                       ) -> 'THIRCopy | None':
    """`copy(x)` of a plain reference-typed source as the copy-construct
    rvalue (`T(x)`) -- a record spells `Point(p)`, a container spells
    `std::vector<int32_t>(data)`, one node shape either way. Every sink that
    takes this row has to intercept it itself -- the special-builtin call gate
    rejects `copy()` in the generic call tail -- so decl init, setitem value,
    return, the field write and the `Own[record]` arg slot all land here.
    None = not this shape; the caller falls through to its own tail.

    `slot_type` is the sink's own type when it differs from the source
    record's (the C++ spelling always follows the SOURCE); `exact` also
    demands the source match `slot_type` nominally; `pointers` overrides the
    excluded-source set for a sink whose admission uses a narrower one.

    Exactly ONE caller passes `pointers`: the return sink, which classifies
    against `scope.admission_pointers()`. That is not a knob to propagate --
    it is preserved verbatim because changing it would alter behaviour inside
    a no-behaviour-change fold, and the narrower set is itself the subject of
    a filed defect (BUGS.md: `copy()` of a narrowed pointer-repr
    `Optional[record]` at that sink drops the deref). Delete the parameter
    when that is fixed rather than adding a second user."""
    pts = lc.pointers if pointers is None else pointers
    crec = copy_construct_source(e, lc.analyzer, pts)
    if crec is not None and (isinstance(e.args[0], TpyName)
                             and e.args[0].name in lc.ptr_variant_locals):
        # An assign-narrowed ptr-variant binding reads as its member
        # record, but its copy is the variant deep copy (to_value_variant,
        # the copy-special ptr-variant arm) -- not the record
        # copy-construct this row spells. The container leg needs no twin:
        # sema types `copy(u)` of a narrowed union NAME as the whole union,
        # so a ptr-variant binding never reaches here with a container read
        # type.
        return None
    if crec is None or (exact and crec != slot_type):
        return None
    return lower_copy_construct(e.args[0], crec, lc, declared,
                                slot_type=slot_type, loc=loc)


def lower_copy_construct(src: TpyExpr, payload: TpyType, lc: '_LowerCtx',
                         declared: dict[str, TpyType], *,
                         slot_type: 'TpyType | None' = None,
                         loc: 'SourceLocation | None' = None) -> THIRCopy:
    """The copy-construct node itself (`T(<source read>)`), shared by the
    explicit `copy()` row and the IMPLICIT copy an owning slot performs on a
    borrowed source. One node either way: the two spellings must not be able
    to render the same copy differently.

    The source's VALUE FORM picks the read, never the sink's convention: the
    sink consumes the `T(...)` construct, and what goes INSIDE it is a borrow
    of the copied object (BORROW_BIND) unless the source is a prvalue, which
    materializes at the STORAGE sink the construct is."""
    src_use = (_ExprUse(result=_ExprResultUse.STORAGE)
               if is_rvalue_source(lc.analyzer, src) else _COPY_SRC_USE)
    return THIRCopy(result_type=payload if slot_type is None else slot_type,
                    value=_lower_expr(src, lc, declared, use=src_use),
                    cpp_type=lc.render_type(payload), form=Form.STORAGE,
                    loc=loc)


def _call_use_supported(e: TpyCall, lc: '_LowerCtx',
                        declared: dict[str, TpyType], use: _ExprUse,
                        allow_whole_optional: bool = False,
                        error_return_raw: bool = False) -> bool:
    result = use.result
    analyzer = lc.analyzer
    fi = e.resolved_function_info
    if e.macro_expansion is not None:
        ok = True
    elif e.cast_target_type is not None:
        ok = True
    elif e.enum_from_value is not None:
        ok = True
    elif fi is not None and fi.is_constructor:
        ok = True
    elif fi is None and _typed_dict_ctor_call(e, analyzer) is not None:
        ok = True
    elif e.call_type is not None:
        _ct_ret = analyzer.get_expr_type(e)
        _ct_b = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_ct_ret)))
                 if _ct_ret is not None else None)
        ok = (result is _ExprResultUse.STORAGE
              # A container-type ctor at a VALUE sink renders the bare
              # prvalue (`print(asdict(p))` -- the asdict expansion's
              # `dict(...)` under the printer wrap; an Array ctor rvalue at
              # a Span slot rides its as_mut_span coerce the same way).
              or (result is _ExprResultUse.VALUE
                  and _ct_b is not None
                  and (is_dict(_ct_b) or is_list(_ct_b) or is_set(_ct_b)
                       or is_array(_ct_b))
                  and _witness("call.container_ctor_value"))
              # ... and at an ITERABLE sink (`for it in list(each(items)):`)
              # the same bare prvalue under the for-head's owning
              # `auto __obj_N =` capture (the container route classes the
              # construct call an rvalue via _call_iterable_lvalue).
              or (result is _ExprResultUse.ITERABLE
                  and _ct_b is not None
                  and (is_dict(_ct_b) or is_list(_ct_b) or is_set(_ct_b)
                       or is_array(_ct_b))
                  and _witness("call.container_ctor_iterable"))
              # A StrView instantiation (`pick_view(StrView("x"))` ->
              # `pick_view("x")`): the fold to the source render is
              # position-independent, so it lands bare at any value/arg
              # sink exactly as at the routed decl slot. (No bytes twin:
              # `BytesView(...)` takes no args in sema, so that leg would
              # be unwitnessable surface.)
              or (_ct_b is not None and is_str_view_type(_ct_b)
                  and _witness("call.view_ctor_value")))
    elif fi is not None and fi.is_method and fi.name == "__init__":
        ok = True
    else:
        ret = analyzer.get_expr_type(e)
        record = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
                  if ret is not None else None)
        ok = (_eligible_scalar(ret) or _eligible_char(ret)
              or _eligible_enum(ret, analyzer) is not None
              or _resolved_str_value(ret, analyzer) is not None
              or _resolved_bytes_value(ret, analyzer) is not None
              or _eligible_ptr_value(ret, analyzer)
              or _callable_value(ret)
              # A readonly[scalar] result (`deref(rp)` on
              # `Ptr[readonly[int32]]` -> const int32_t): a const VALUE
              # copy, scalar in every sink.
              or (_eligible_scalar(unwrap_readonly(ret)
                                   if isinstance(ret, TpyType) else None)
                  and _witness("call.readonly_scalar_ret"))
              # A VALUE Array result (`b.set_data(get_global_array())`):
              # std::array is a value container, the prvalue lands bare at
              # any sink -- span-like, no borrow/storage duality.
              or (record is not None and is_array(record)
                  and _witness("call.array_value_ret"))
              # A Span result (`len(span(x))` -> `::tpy::__len__(
              # ::tpy::as_span(x))`): a value VIEW returned by value, the
              # prvalue lands bare at any sink -- the Array row's verbatim
              # sibling.
              or (record is not None and is_span(record)
                  and _witness("call.span_value_ret"))
              # An `Any`-returning call: `tpy::Any` is a value type returned by
              # value, landing bare in a value / storage slot.
              or isinstance(record, AnyType)
              # An open-T result inside a generic body (`make_default()`,
              # `copy(self.value)` -- Own[T] unwraps to T): renders by name,
              # the composing position gates its own family.
              or _tparam_value(ret)
              # A structural-protocol result (`iter(s)` -> Iterator[T]):
              # the native helper's render carries the concrete C++ type;
              # the decl slot spells `auto`.
              or _protocol_auto_slot(record)
              # A borrow-returning record call at the field-write COPY sink
              # (`h.p = identity(pt);`): the copy-assign absorbs the `T&`,
              # so the call renders bare -- sink-flagged, decls keep
              # rejecting (they bind REF_ALIAS off the same result).
              or (use.admits(SinkForm.RECORD_COPY)
                  and fi is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _f1_record(ret, analyzer)
                  and _witness("call.field_copy_borrow_ret"))
              # A per-element-Own record tuple result (`make_pair()` ->
              # `std::tuple<Counter, Counter>`): borrow and storage
              # coincide, the call lands bare in its spelled slot.
              or (result is _ExprResultUse.STORAGE
                  and _own_record_tuple(ret, analyzer) is not None)
              # A BUILTIN value-record result (`_make_waker(...)` ->
              # `::tpystd::coro::Waker`): the by-value return lands bare
              # in its spelled slot -- the return twin of the builtin
              # value-record decl row, same record-info guard.
              or (result is _ExprResultUse.STORAGE
                  and _builtin_value_record(record, analyzer)
                  and _witness("call.builtin_value_record_ret"))
              # A value-repr Optional[scalar] result lands bare in its
              # value-optional slot (`r = h(true);`); mismatched consumers
              # reject at their own slot arms.
              or _value_opt_scalar(ret, analyzer) is not None
              # A value-repr Optional[str]/[bytes] result feeding an `is [not]
              # None` test lands bare; `.has_value()` reads the whole optional
              # (the operand never derefs), so the str/bytes view-vs-owned slot
              # split that gates a value-slot consumer does not apply here.
              or (allow_whole_optional
                  and _value_opt_rvalue(e, analyzer) is not None)
              # The module-init pass-through write of a pointer-slot global
              # (`g = find(xs, k);`): a BORROW-returning ptr-repr Optional
              # result IS the `T*` the slot holds, so it lands bare.
              or (use.admits(SinkForm.PTR_OPT_PASSTHROUGH)
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_opt_passthrough"))
              # The `Optional[record]` FIELD-write sink: the same borrowed
              # `T*` result, but lifted by the sink (`ptr_to_optional(...)`)
              # rather than landing bare.
              or (use.admits(SinkForm.PTR_OPT_LIFT)
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_opt_lift"))
              # The ptr_truthy operand: the borrowed `T*` result lands bare
              # inside `::tpy::ptr_truthy(...)`, evaluated exactly once.
              # Only the dunder-carrying record inner reaches TRUTHY with a
              # ptr-optional result -- `_lower_truthy`'s ladder rejects the
              # rest before this gate.
              or (result is _ExprResultUse.TRUTHY
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_truthy_operand"))
              # A BORROW-returning ptr-repr Optional result at a
              # whole-optional consumer (`find(items, 99) is None` -> the
              # bare `== nullptr` compare): the result IS the `T*` the
              # consumer tests, so it lands bare like the passthrough rows.
              or (allow_whole_optional
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_opt_whole"))
              # The value-variant UNION field-write sink: a ptr-variant
              # union result consumed whole by the sink's
              # `to_value_variant` lift.
              or (use.admits(SinkForm.UNION_VALUE_LIFT)
                  and isinstance(record, UnionType)
                  and record.uses_pointer_repr()
                  and not record.needs_wrapper()
                  and _witness("call.union_value_lift_ret"))
              # The Own[union] ARG slot: a same-union Own[A|B]-returning
              # call rvalue moves through the `&&` slot bare
              # (`describe(pick(True))`) -- the arg row threads OWN_SLOT.
              or (result is _ExprResultUse.OWN_SLOT
                  and _eligible_ptr_union(
                      unwrap_readonly(record.wrapped)
                      if isinstance(record, OwnType) else record,
                      analyzer) is not None
                  and _witness("own.union_call_pass"))
              or (result is _ExprResultUse.DISCARD
                  and is_void_like_type(ret))
              # The union-switch CALL-subject sink (`match choose(d):`):
              # the non-wrapper ptr-variant union return is consumed whole
              # by the by-value dispatch local -- the free-call twin of the
              # method gate's union_subject_ret_ok row.
              or (use.admits(SinkForm.UNION_SUBJECT)
                  and isinstance(ret, TpyType)
                  and isinstance(unwrap_readonly(unwrap_ref_type(
                      unwrap_send_sync(ret))), UnionType)
                  and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      ret))).uses_pointer_repr()
                  and not unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      ret))).needs_wrapper()
                  and _witness("call.union_subject_ret"))
              # A DISCARDED @native record-rvalue call (`open(missing)` for
              # its raise, result unused): the bare call statement -- the
              # record temporary dies at the semicolon.
              or (result is _ExprResultUse.DISCARD
                  and _native_record_rvalue_call_shape(e, analyzer)
                  and _witness("call.discard_native_record"))
              or (result is _ExprResultUse.ITERABLE
                  and _nonvalue_container_ret(ret))
              # A generator or iterator factory call in iterable position:
              # the result feeds the universal __iter__/__next__ loop's
              # source capture, never a typed value slot.
              or (result is _ExprResultUse.ITERABLE
                  and fi is not None and fi.is_generator)
              or (result is _ExprResultUse.ITERABLE
                  and _iter_proto_call_ret(e, analyzer))
              or (result is _ExprResultUse.STORAGE
                  and _storage_call_ret(ret, analyzer) is not None)
              # A CONTAINER result consumed as the next RECEIVER
              # (`make_list()[0]` -- the checked dunder interpolates the
              # rvalue / borrow render): the view and protocol families'
              # composition row, the free-call twin.
              or (result is _ExprResultUse.RECEIVER
                  and (_storage_call_ret(ret, analyzer) is not None
                       or _alias_ref_container(ret))
                  and _witness("call.container_recv_ret"))
              # The BORROW-record RETURN passthrough only (the dedicated
              # flag -- never a general record-at-RECEIVER admission,
              # which was reverted for shadowing / opening the REF_ALIAS
              # design stop): `return get_first(items);` renders the
              # T&-returning call bare.
              or (use.admits(SinkForm.BORROW_RET_PASSTHROUGH)
                  and fi is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _f1_record(ret, analyzer)
                  and _witness("call.borrow_ret_passthrough"))
              # The FIELD-READ receiver twin (the dedicated flag --
              # `ret_param_ref(shared).n` composes the member read over the
              # bare T&-returning call; transient, nothing binds).
              or (use.admits(SinkForm.FIELD_RECV_BORROW)
                  and result is _ExprResultUse.RECEIVER
                  and fi is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _f1_record(ret, analyzer)
                  and _witness("call.field_recv_borrow_ret"))
              # The pointer-repr-tuple LAMBDA-return body (`lambda p:
              # label(str(p.x), p)` at an `Fn[..., tuple[str, Ref[T]]]`
              # slot): the generic callee's val_or_ptr_t tuple IS the
              # closure's borrow-form return, rendered bare. The gate
              # (`_lambda_routable`) validated the modulo-Ref tuple match.
              or (use.admits(SinkForm.LAMBDA_BTUPLE_RET)
                  and fi is not None and bool(fi.type_params)
                  and isinstance(record, TupleType)
                  and record.has_pointer_repr_element()
                  and _witness("call.lambda_btuple_ret"))
              # An Own-optional-returning call lands bare in its storage
              # `std::optional<T>` decl slot (`r = move_out(True);`) -- the
              # method gate's escape, the free-call twin. WIDE pointee
              # class: the OPT_STORAGE_CALL slot consumes the whole
              # optional, member-shape-blind. `fi.return_type` covers the
              # `Own[Optional[W]]` spelling get_expr_type strips.
              or (result is _ExprResultUse.STORAGE
                  and (_storage_optional_return_wide(
                          unwrap_readonly(unwrap_ref_type(
                              unwrap_send_sync(ret)))
                          if isinstance(ret, TpyType) else None,
                          analyzer) is not None
                       or _storage_optional_return_wide(
                          fi.return_type if fi is not None else None,
                          analyzer) is not None)
                  and _witness("call.storage_opt_ret"))
              # An Own[genrec]-returning call at a STORAGE sink (the
              # argtemp.ru_wrapper_call temp): the by-value wrapper return
              # lands bare in the typed temp slot.
              or (result is _ExprResultUse.STORAGE
                  and _own_genrec_return(
                      fi.return_type if fi is not None else None) is not None
                  and _witness("call.genrec_own_ret"))
              # An `Own[@dynamic P]`-returning call (`std::unique_ptr<P>` by
              # value) at a STORAGE/VALUE/DISCARD sink: the handle is
              # consumed whole (a forward return / ternary arm / forward
              # arg) or dropped at the semicolon; renders bare either way.
              or (result in (_ExprResultUse.STORAGE, _ExprResultUse.VALUE,
                             _ExprResultUse.DISCARD)
                  and _own_dyn_return(
                      fi.return_type if fi is not None else None) is not None
                  and _witness("call.dyn_own_ret"))
              # A @dynamic-protocol call result composing as a method
              # RECEIVER / borrow bind: an Own[P] handle arrows
              # (`make_parrot()->name()`), a borrow `P&` result dots
              # (`echo_readonly(dog).name()`) -- both bare. The erased-decl
              # `&`-lift consumes the same borrow result under addr_call.
              or (result in (_ExprResultUse.RECEIVER,
                             _ExprResultUse.BORROW_BIND)
                  and fi is not None
                  and (_own_dyn_return(fi.return_type) is not None
                       or _dyn_borrow_return(fi.return_type) is not None)
                  and _witness("call.dyn_recv_ret"))
              # A wrapper-union BORROW-returning call composing in place
              # (`count(passthru(tree))` -- the `Expr&` result binds the
              # const-ref arg slot directly): renders bare, no temp or
              # lift. Keyed on the bare (non-Own) wrapper return slot --
              # the same fact that renders the signature `Expr&`.
              or (result in (_ExprResultUse.VALUE,
                             _ExprResultUse.BORROW_BIND)
                  and fi is not None
                  and _wrapper_borrow_return(fi.return_type, analyzer)
                  is not None
                  and _witness("call.wrapper_borrow_ret"))
              # A container-returning call at the STORAGE return sink lands
              # bare regardless of element family -- the whole container is
              # returned by value, no per-element conversion happens (unlike
              # `_storage_call_ret`, whose scalar-read guard is a DECL-consumer
              # concern). Return-position only; a record-element container decl
              # is blocked at its own slot before this gate.
              or (result is _ExprResultUse.STORAGE
                  and _container_storage_return_call_ret(ret, analyzer))
              # A span result is a by-value view landing bare in its decl
              # slot (`std::span<T> s = get_span(a);`); storage sinks only,
              # like the container/tuple/union storage_call rows.
              or (result is _ExprResultUse.STORAGE and _span_value(ret))
              # A VIEW-inner value-optional result
              # (`std::optional<std::string_view>`) at the same storage sink:
              # a by-value view optional, bare like the span row.
              or (result is _ExprResultUse.STORAGE
                  and _view_inner_value_opt(ret)
                  and _witness("call.view_inner_opt_ret"))
              # A union-returning call lands bare in its same-union slot: a
              # ptr-variant return copies the variant by value
              # (`got = cycle(start)`), an `Own[union]` factory fills the
              # UNION_RVALUE storage slot (`__slot_N = make_pet(..)` --
              # the decl arm's BORROW_BIND init).
              or (result in (_ExprResultUse.STORAGE,
                             _ExprResultUse.BORROW_BIND)
                  and _call_ret_union_ok(ret, analyzer))
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and _owned_tuple_call_ret(ret, analyzer) is not None)
              # An OPEN value-tuple result (`min(a, b, key=..)` returning
              # `tuple[T, int32]` inside a generic body): the by-value tuple
              # renders bare into the storage sink, exactly as the concrete
              # rows above -- only their classifiers decline the open element.
              or (result is _ExprResultUse.STORAGE
                  and _open_value_tuple(ret) is not None
                  and _witness("call.open_value_tuple_ret"))
              # A tuple OF owned tuples (`two_pairs()`) at the same sink:
              # one storage copy of the whole nested result.
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and _nested_owned_tuple_call_ret(ret, analyzer) is not None)
              # An F1 BORROW-tuple call result (`first_two(xs) ->
              # tuple[Box, Box]` returning `std::tuple<Box*, Box*>`) at
              # the tuple-source sink: the call renders bare into the
              # `auto __tup_N = <call>;` capture and the unpack's alias
              # targets re-point off the elements; whole-tuple sinks only,
              # so no other consumer can bind the borrow tuple.
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and _f1_tuple(ret, analyzer) is not None)
              # A REFERENCE-element tuple result (`pair(t) ->
              # tuple[Tree[int32], int32]` returning
              # `std::tuple<Tree<int32_t>&, int32_t>`) at the tuple-source
              # sink: the call lands bare in the `auto __tup_N = <call>;`
              # capture and the unwrap_ref target aliases the live member.
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and _wrapper_ref_tuple_return(ret, analyzer) is not None
                  and _witness("call.wrapper_ref_tuple_ret"))
              # A VALUE tuple with a value-union element (`remake() ->
              # tuple[int | str, int]`) at the tuple-source sink: borrow
              # and storage coincide, the call lands bare in the
              # `auto __tup_N = <call>;` capture.
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and _union_elem_value_tuple(ret, analyzer) is not None
                  and _witness("call.union_elem_tuple_ret"))
              # A ptr-Optional-element borrow-tuple result
              # (`make_pair() -> tuple[P | None, ..]` returning
              # `std::tuple<P*, ..>`) at the tuple-source sink: same bare
              # render, consumed by the storage lift / unpack capture /
              # matching borrow param.
              or (result is _ExprResultUse.STORAGE
                  and use.admits(SinkForm.TUPLE_SOURCE)
                  and isinstance(
                      _ptb := unwrap_readonly(unwrap_ref_type(
                          unwrap_send_sync(ret))), TupleType)
                  and _ptb.has_pointer_repr_element()
                  and _tuple_elem_slots_ptr_optional(_ptb))
              # A value-tuple call result (`split(p)`) in a plain VALUE
              # position (a call arg / nested expr): the tuple is a value
              # type returned by value and renders bare, binding a
              # `const std::tuple<...>&` slot directly. The STORAGE decl sink
              # rides the whole-tuple arm above; other consumers gate their
              # own shape.
              or (result is _ExprResultUse.VALUE
                  and _value_tuple(ret, analyzer) is not None)
              # An Own[tuple]-declared callee at the STORAGE sink (the
              # owning frame-slot emplace value, `t.emplace(make_pair(9))`):
              # the by-value tuple return constructs in place.
              or (result is _ExprResultUse.STORAGE
                  and _own_declared_call_ret(e)
                  and isinstance(
                      unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                          ret))), TupleType)
                  and _witness("call.own_tuple_storage_ret"))
              # A wrapper-union VALUE return (`build() -> Own[Expr]`) in a
              # plain VALUE position: the by-value wrapper struct renders
              # bare (the _ru_wrapper_value_call_arg admission's callee
              # half).
              or (result is _ExprResultUse.VALUE
                  and fi is not None
                  and _wrapper_value_return(fi.return_type, analyzer)
                  is not None
                  and _witness("call.wrapper_value_ret"))
              # The borrow-tuple local decl (`auto p = pair_of(b);`): the
              # `auto` slot binds the pointer-repr result whole, so the call
              # renders bare with no form conversion.
              or (use.admits(SinkForm.BTUPLE_SLOT)
                  and (_f1_tuple(ret, analyzer) is not None
                       # ... and the REFERENCE-element tuple result
                       # (`auto p = keep_param(tree);` off
                       # `std::tuple<Tree&, int32_t>`).
                       or _wrapper_ref_tuple_return(ret, analyzer)
                       is not None)
                  and _witness("call.btuple_slot"))
              or (result is _ExprResultUse.BORROW_BIND
                  and _f1_record(record, analyzer))
              # A BORROW container return at the alias-decl sink
              # (`std::vector<T>& items = identity<...>(t);` -- the
              # container twin of the record borrow-bind rung).
              or (result is _ExprResultUse.BORROW_BIND
                  and fi is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _nonvalue_container_ret(ret)
                  and _witness("call.container_borrow_ret"))
              # ... and the OWNING container return at the same bind, the
              # container twin of the record row above -- the same predicate
              # the decl-binding admission uses, so gate and render agree.
              or (result is _ExprResultUse.BORROW_BIND
                  and _container_rebind_call_ret(e, ret, analyzer)
                  and _witness("call.container_rebind_ret"))
              # An async-def FACTORY call under the make_adapter wrap: the
              # concrete coro frame is consumed whole by the adapter.
              or (use.admits(SinkForm.CORO_FACTORY)
                  and fi is not None and fi.is_async)
              # A sync `with` manager that resolves to a @native record-
              # returning call (`with open(path, mode)`): stored in the
              # `__ctx_N` slot via the native free-call arm.
              or (use.admits(SinkForm.CTX_MANAGER)
                  and _native_ctx_manager_ok(e, analyzer))
              or _record_rvalue_call_shape(e, analyzer)
              # A @native free call returning a by-value record (`f =
              # open(path)`): the native residue of the plain record-rvalue
              # arm above, landing bare in a STORAGE value slot and under a
              # postfix member read (`deref(s).kind` -- the pascal frontend's
              # explicit deref call; `<call>.field` composes on the bare
              # render).
              or (result is _ExprResultUse.STORAGE
                  and _native_record_rvalue_call_shape(e, analyzer))
              or (result is _ExprResultUse.RECEIVER
                  and _native_record_rvalue_call_shape(e, analyzer)
                  and _witness("call.native_record_recv"))
              # ... and at the plain VALUE position, but only where the
              # native callee's DECLARED return spells the transfer
              # (`JoinHandle[R](spawn_native(t))` at an `Own[record]` ctor
              # slot). A bare `-> V` native leaves its C++ return convention
              # unspelled, so it stays on the two sinks above; the arg gate
              # reads the same predicate.
              or (result is _ExprResultUse.VALUE
                  and _native_own_record_rvalue_call_shape(e, analyzer)
                  and _witness("call.native_own_record_value"))
              # The @cpp_template residue (`make_default[Point]()` ->
              # `Point p = Point{};`, `abs(t).v` -> `(t).__abs__().v`,
              # `unsafe_load(p, 0).x` -> `p[0].x`): the expanded
              # positional-only template renders bare in the STORAGE slot
              # and under the postfix member alike.
              or (result in (_ExprResultUse.STORAGE,
                             _ExprResultUse.RECEIVER)
                  and _template_record_rvalue_call_shape(e, analyzer))
              # An @error_return record-rvalue callee at the field-RECEIVER
              # position (the member read composes on the er-unwrap
              # stmt-expr, `make_data(v).value`) and at the RAW statement
              # bind (`b = decode(ok)` -- the try/er `__try_tmp_N` block,
              # which takes the call un-wrapped).
              or ((result is _ExprResultUse.RECEIVER or error_return_raw)
                  and _er_record_rvalue_call_shape(e, analyzer))
              # The REF-returning @error_return callee at the raw er-bind
              # (`result = &(::tpy::unwrap_ref(*__try_tmp_N));`): the
              # alias-bind arm owns the address-of and pinned the pointer
              # target; the call itself renders bare inside the try block.
              or (error_return_raw and fi is not None
                  and fi.error_return_type is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _f1_record(ret, analyzer)
                  and _witness("call.er_ref_bind"))
              # A BORROW-returning record call consumed under the global
              # ptr-slot's address-of lift only (`p = &(get_item<Point>(
              # (*points), 0));`, the ADDR_CALL form). The call renders bare;
              # the position owns the `&(...)`. NOT bare RECEIVER: a field read
              # off the same call shape is design-stopped (REF_ALIAS
              # place/loan frontier).
              or (use.admits(SinkForm.ADDR_CALL)
                  and fi is not None
                  and call_returns_cpp_ref(analyzer, fi)
                  and _f1_record(ret, analyzer)
                  and _witness("call.recv_borrow_ret"))
              # The `copy()` builtin at a value-consuming sink. Every
              # callee-shape classifier above rejects a special builtin, so
              # admission is the SOURCE's -- the same classifier the
              # intercepting sinks use, and pointer-blind like the arg
              # table's row (lowering re-runs with the live set and picks
              # the deref arm). LAST in the chain, so no spelling an
              # existing arm renders is re-claimed.
              or (result in (_ExprResultUse.STORAGE, _ExprResultUse.VALUE,
                             _ExprResultUse.BORROW_BIND)
                  and copy_construct_source(e, analyzer, frozenset())
                  is not None
                  and _witness("call.copy_construct_ret")))
        if not ok:
            note_detail(_call_ret_reject(e, ret, analyzer))
    if result is _ExprResultUse.CONDITION:
        return ok and is_bool_type(lc.analyzer.get_expr_type(e))
    return ok


def _record_ctor_shape_supported(e: TpyCall, lc: '_LowerCtx',
                                 use: _ExprUse) -> bool:
    # A plain @native record's ctor emits through the same record branch as a
    # user one, so the face is position-blind: the decl / value slot, the
    # ctor MIL, and an rvalue at an Own slot all spell the bare expansion.
    if _ctor_shape_ok(e, lc.analyzer, native_ok=True):
        return True
    # The instantiation render (`type_to_cpp(call_type)(args)`) is
    # position-independent, so NESTED_ARG positions admit it
    # too (`Rc.new(Box(Box(Dog(..))))` -- the inner Box); its ARGS still
    # gate per position (a temp-needing arg without the ridden flush right
    # rejects in the arg rows, not here).
    return _ctor_instantiation_ok(e, lc.analyzer)


def _protocol_union_literal_temp_arg(arg: TpyExpr, ptype: 'TpyType | None',
                                     analyzer) -> 'TpyType | None':
    """The LITERAL sibling of the 'addr' face: a container literal into the
    same nullable all-protocols ctor slot (`Counter(["a", "x", "x"])` at
    `Iterable[T] | None`). The slot hoists a temp typed as the literal's
    OWN sema type (the fixed-list Array demotion:
    `std::array<std::string, 3> __tmp_N = {..};`) and lifts its address.
    Returns the temp's type or None."""
    if not isinstance(arg, TpyArrayLiteral):
        return None
    if _nullable_protocol_slot(ptype) is None:
        return None
    at = analyzer.get_expr_type(arg)
    at = resolve_pending_container(at, analyzer) or at
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not (isinstance(at, NominalType) and is_array(at)):
        return None
    return at


def _protocol_union_iter_temp_arg(arg: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer) -> 'TpyType | None':
    """The ITERATOR-RVALUE sibling of the literal temp arm: a dict-view /
    gen-factory rvalue into the same nullable all-protocols slot
    (`ArrayList[tuple[str, int32], 16](d.items())` at `Spannable[T] |
    Iterable[Own[T]] | None`). The temp is SPELLED with the rvalue's own
    sema type rather than `auto` -- the address-of lift needs an addressable
    named object of exactly the type the slot's template param deduces on.
    Returns the temp's type or None."""
    members = _nullable_protocol_slot(ptype)
    if members is None:
        return None
    if not all(_iter_rvalue_structural_arg(arg, m, locals_, analyzer)
               for m in members):
        return None
    at = analyzer.get_expr_type(arg)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return at if isinstance(at, TpyType) else None


def _own_tuple_shape_match(a: TpyExpr, ptype: 'TpyType | None',
                           lc: '_LowerCtx',
                           declared: dict[str, TpyType]) -> 'TupleType | None':
    """The shared shape half of the Own-element tuple NAME arg arms: a
    non-self, non-narrowed in-scope NAME whose declared tuple matches the
    slot per-element modulo-Own (`tuple[Box, int32]` vs `tuple[Own[Box],
    int32]` -- both spell std::tuple<Box, int32_t>), with at least one Own
    element on the slot side. Returns the BINDING's tuple type (whose form
    the caller keys its verdict on), or None."""
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    if (a.name in lc.narrow.narrowed or a.name in lc.inline_narrowed
            or a.name not in declared):
        return None
    pu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    au = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[a.name])))
    if not (isinstance(pu, TupleType) and isinstance(au, TupleType)
            and len(au.element_types) == len(pu.element_types)
            and all(_unwrap_own(unwrap_readonly(ae))
                    == _unwrap_own(unwrap_readonly(pe))
                    for ae, pe in zip(au.element_types, pu.element_types))):
        return None
    if not any(isinstance(unwrap_readonly(et), OwnType)
               for et in pu.element_types):
        return None
    if pu.is_mixed_own():
        # A MIXED slot is a const& of the mixed render -- it binds the
        # hybrid verbatim, never through the storage move/lift/decay rows
        # (the lift row wrapped `show((*p))` in tuple_to_storage).
        return None
    return au

def _own_tuple_move_arg(a: TpyExpr, ptype: 'TpyType | None',
                        lc: '_LowerCtx',
                        declared: dict[str, TpyType]) -> bool:
    """A storage OWN-element tuple NAME at the matching rvalue tuple slot
    (`consume(std::move(t))` on `tuple[Own[A], Own[A]]` -> the
    `std::tuple<A, A>&&` param): the movable binding is consumed whole at
    its last use. `_is_move_source` carries the movable/last-use
    verdict."""
    if _own_tuple_shape_match(a, ptype, lc, declared) is None:
        return False
    return _is_move_source(a, lc)

def _own_tuple_borrow_lift_arg(a: TpyExpr, ptype: 'TpyType | None',
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType]) -> bool:
    """A BORROW-form Own-element tuple NAME at the `std::tuple<...>&&`
    slot: the F3 `tuple_to_storage` lift copies the referents in (the
    warned copy). STORAGE-form bindings stay out -- a movable last use
    rides `_own_tuple_move_arg`, and a still-live storage binding needs
    the `auto(p)` decay-copy instead."""
    au = _own_tuple_shape_match(a, ptype, lc, declared)
    if au is None:
        return False
    if (a.name in lc.pointers or a.name in lc.storage_tuple_locals
            or _is_own_param(a.name, lc)):
        return False
    # BORROW form only: the binding's tuple must carry a pointer-repr
    # element (the `std::tuple<Box*, ...>` local); an all-storage binding
    # would need the decay-copy.
    return au.has_pointer_repr_element()

def _own_tuple_decay_copy_arg(a: TpyExpr, ptype: 'TpyType | None',
                              lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> bool:
    """A STORAGE-form Own-element tuple NAME still live at the
    `std::tuple<...>&&` slot: the render decay-copies (`sink(auto(p))` --
    the warned copy; sema rejected the @nocopy case). A movable last use
    rides `_own_tuple_move_arg`, a borrow-form binding the
    `tuple_to_storage` lift."""
    au = _own_tuple_shape_match(a, ptype, lc, declared)
    if au is None or _is_move_source(a, lc):
        return False
    if a.name in lc.pointers or au.has_pointer_repr_element():
        return False
    # The auto() gate keys on is_owned_movable (ALL non-value
    # elements Own) -- a MIXED slot is a const& of the mixed render,
    # never a && slot, so it must stay out.
    pu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    return isinstance(pu, TupleType) and pu.is_owned_movable()


def _own_move_source_slice(a: TpyExpr, ptype: 'TpyType | None',
                           lc: '_LowerCtx',
                           declared: dict[str, TpyType]) -> bool:
    """The temp-free MOVE-SOURCE slice of the Own-slot copy+move row: a bare
    non-self, non-narrowed NAME at its last movable use -- of ANY payload,
    value-typed or not -- into an eligible Own slot; renders `std::move(name)`
    position-
    independently. Shared by the NESTED ctor tail (which admits exactly this
    slice; the flushable copy half stays DIRECT-only) and `_lower_call_arg`'s
    Own-slot arm (which picks THIRMove on the same facts), so the two cannot
    drift."""
    return _own_move_source_slice_facts(
        a, ptype, lc.analyzer, declared, lc.prescan.param_names,
        lc.narrow.narrowed, lc.inline_narrowed, lc.movable_locals,
        getattr(lc.func, "name", None))


def _own_move_source_slice_facts(
        a: TpyExpr, ptype: 'TpyType | None', analyzer,
        declared: dict[str, TpyType],
        param_names: 'set[str] | frozenset[str]',
        narrowed: 'set[str] | frozenset[str]',
        inline_narrowed,
        movable_locals: 'set[str] | frozenset[str]',
        func_name: 'str | None') -> bool:
    """`_own_move_source_slice` over the discrete facts -- see
    `_is_move_source_facts` for why the split exists."""
    if (_own_lvalue_temp_slot(a, ptype, analyzer, declared,
                              param_names) is None
            and _own_bytes_identity_move_slot(
                a, ptype, analyzer, locals_=declared,
                param_names=param_names) is None):
        return False
    # Coerces are peeled before the last-use check, so an
    # all-identity chain over a movable name moves the same way the bare
    # name does.
    bare = _peel_coerce(a)
    if not isinstance(bare, TpyName) or bare.name == "self":
        return False
    if (bare.name in narrowed or bare.name in inline_narrowed
            or bare.name not in declared):
        return False
    # No value-type filter: movability alone decides, because the working set
    # only ever holds names an arm actually promoted. A sync scalar is never
    # promoted (the tier-1 arm's own value filter), while a frame-promoted or
    # await-bound scalar IS, and moves here. Filtering by payload
    # type instead of trusting the set is what made `asyncio_queue`'s
    # `out.append(x)` render bare where it must be `std::move(x)`.
    return _is_move_source_facts(a, movable_locals, analyzer, func_name)


def _template_arg_unreferenced(kind: 'tuple[str, str] | None',
                               arg_index: int) -> bool:
    """A @cpp_template callee whose body never substitutes `{arg_index}`: the
    expansion cannot contain that arg's render, so its shape is unobservable
    (`filter(None, xs)` -> `::tpy::builtin_filter_truthy<T>({1})`, where the
    `None` predicate selects the callee and then vanishes). Asks the shared
    brace-grammar scanner rather than testing for a substring -- a `{{0}}`
    literal-brace escape is not a reference."""
    if kind is None or kind[0] != "template":
        return False
    return arg_index not in _template_positional_indices(kind[1])


def _own_opt_ptr_name_move_arg(a: TpyExpr, ptype: 'TpyType | None',
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType]) -> 'OptionalType | None':
    """The LAST-USE slice of `_own_opt_ptr_name_arg`: a pointer-repr Optional
    name whose occurrence moves. Only that slice renders the null-safe rebuild
    (a prvalue moving the pointee); a non-last-use occurrence needs the Own-slot copy
    -TEMP cascade (`Holder(std::move(__tmp_N))`), an entirely different render,
    so it stays a reject."""
    return _own_opt_ptr_name_move_arg_facts(
        a, ptype, declared, frozenset(lc.narrow.narrowed), lc.analyzer,
        lc.movable_locals, getattr(lc.func, "name", None))


def _value_opt_pass_arg(arg: TpyExpr, ptype: 'TpyType | None',
                        declared: dict[str, TpyType],
                        narrowed: 'set[str] | frozenset[str]',
                        analyzer) -> bool:
    """A whole value-repr Optional NAME into the SAME Optional slot
    (`Args(scale, bias, gain)` -- the argparse builder): the bare same-type
    copy. Narrowed names read as the extraction and stay out."""
    if not isinstance(arg, TpyName) or arg.name not in declared:
        return False
    if arg.name in narrowed:
        return False
    if _value_opt_scalar(ptype, analyzer) is None:
        return False
    return (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared[arg.name])))
            == unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype))))


def _const_param_rvalue_ctor_arg(arg: TpyExpr, ptype: TpyType | None,
                                 analyzer) -> bool:
    """A CALL result into a CTOR slot whose param form is const by default
    (`Holder(bytearray(b"xy"))`): the rvalue binds the `const vector&` slot
    inline, temp-free, which is exactly what
    `_const_default_param_form` licenses. CTOR-only on purpose -- the
    free/method arg loop hoists a `__tmp_N` for the same shape, so the shared
    pass-through predicate would diverge there."""
    if not isinstance(arg, (TpyCall, TpyMethodCall)) or ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if (isinstance(pt, (OwnType, OptionalType))
            or not _const_default_param_form(pt)):
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(arg))))
    return bool(_const_default_param_form(at))


# ---------------------------------------------------------------------------
# THE RECORD-CTOR ARG TABLE (see `arg_table.py` for the shapes and the
# invariant; the other nine families' tables live in `checks.py`, beside
# their predicates).
#
# TWO families, not one, and the ctor USE picks which: a DIRECT position --
# every `X(...)` that is not an argument of another call, plus a nested one
# that rode the enclosing statement's flush right in -- carries the full row
# set; a NESTED position with no flush point carries the temp-FREE slice of
# it, in its own order and behind a slot prologue the direct family has no
# counterpart for. The shared cells reach the identical predicate, which
# `register_sink` enforces across both modules.
#
# ORDER IS LOAD-BEARING: `witness` fires while the gate walks, so reordering
# a family's rows changes the recorded face census even when admission is
# unchanged.
# ---------------------------------------------------------------------------

def _x_temps_and_mutated(req: _ArgReq) -> bool:
    """The `temps_ok and is_mutated and` prefix: a MUTATED ref slot cannot
    bind a prvalue, so its hoisted temp needs a statement to flush into."""
    return req.temps_ok and req.mutated_slots


def _x_not_temps_ok(req: _ArgReq) -> bool:
    """The flush-LESS half of a row pair: the direct position takes the
    copy+move cell above when it can flush, and only the temp-free move
    slice when it cannot."""
    return not req.temps_ok


def _x_not_mutated(req: _ArgReq) -> bool:
    """A const ref slot, which binds a container literal's prvalue inline --
    the mutated slot takes the hoisted-temp cell at the top of the family."""
    return not req.mutated_slots


def _x_ctor_const_rvalue_slot(req: _ArgReq) -> bool:
    """The gate of the one DECISIVE cell: an unmutated record-rvalue temp
    slot. Once it fires, the two source shapes its cell names are the whole
    answer -- anything else at that slot is refused, not passed on."""
    return (_record_rvalue_temp_slot(req.a, req.ptype, req.analyzer)
            is not None and not req.mutated_slots)


def _x_ctor_str_source_allowed(req: _ArgReq) -> bool:
    """The mutated-`String&` refusal, as this row's pre-guard: a mutated (or
    unknown-mutation) `std::string&` slot takes no source here rather than
    reproduce the known miscompile for it."""
    slot_type = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(req.ptype)))
    return not (isinstance(slot_type, NominalType)
                and is_string_type(slot_type)
                and (req.mutation_unknown or req.mutated_slots))


def _r_mutated_container_literal(req: _ArgReq) -> bool:
    return (isinstance(req.a, (TpyArrayLiteral, TpyDictLiteral,
                               TpySetLiteral))
            and _container_literal_arg(req.a, req.ptype, req.analyzer))


def _r_own_opt_container_ptr(req: _ArgReq) -> bool:
    return _own_opt_container_ptr_arg_facts(
        req.a, req.ptype, req.locals_, req.pointers, req.analyzer,
        req.movable_locals, req.func_name) is not None


def _r_own_str_literal_bare(req: _ArgReq) -> bool:
    # The BARE spelling: the registered `own_str_literal` peels the coerce
    # off the argument and the ref/readonly wrappers off the slot, this cell
    # does neither -- so the two admit different shapes and stay separate
    # cells, as `str_literal_value_opt` and its `_coerced` twin do.
    return _own_str_literal_arg(req.a, req.ptype)


def _r_own_bytes_literal(req: _ArgReq) -> bool:
    return _own_bytes_literal_arg(req.a, req.ptype)


def _r_bytearray_rvalue_ctor(req: _ArgReq) -> bool:
    return _const_param_rvalue_ctor_arg(req.a, req.ptype, req.analyzer)


def _r_value_opt_name_pass(req: _ArgReq) -> bool:
    # Not the registered `value_opt_pass_through`: that one falls back to the
    # analyzer for a name the body did not bind and excludes function refs,
    # where this one requires the DECLARED binding. Near-duplicate shapes,
    # not one shape -- reconciling them moves admissions, so it is not part
    # of the fold.
    return _value_opt_pass_arg(req.a, req.ptype, req.locals_, req.narrowed,
                               req.analyzer)


def _r_tparam_name_pass(req: _ArgReq) -> bool:
    return _tparam_name_pass_arg(req.a, req.ptype, req.locals_)


def _r_own_move_source_slice(req: _ArgReq) -> bool:
    return _own_move_source_slice_facts(
        req.a, req.ptype, req.analyzer, req.locals_, req.param_names,
        req.narrowed, req.inline_narrowed, req.movable_locals, req.func_name)


def _r_opt_own_container_name(req: _ArgReq) -> bool:
    return _opt_own_container_name_arg(req.a, req.ptype, req.locals_,
                                       req.analyzer) is not None


def _r_copy_open_elem(req: _ArgReq) -> bool:
    return _copy_open_elem_arg(req.a, req.ptype, req.analyzer) is not None


def _r_generic_open_slot_elem(req: _ArgReq) -> bool:
    return (isinstance(req.a, TpySubscript)
            and _open_slot_match(req.analyzer.get_expr_type(req.a), req.ptype))


def _r_async_factory_wrap(req: _ArgReq) -> bool:
    return (isinstance(req.a, TpyName)
            and _async_factory_wrap_cpp_facts(
                req.a, req.analyzer.get_expr_type(req.a),
                req.analyzer) is not None)


def _r_field_read_ref_ctor(req: _ArgReq) -> bool:
    return _field_read_ref_ctor_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer, mutated=req.mutated_slots)


def _r_own_container_instantiation(req: _ArgReq) -> bool:
    return _own_container_instantiation_arg(req.a, req.ptype, req.analyzer)


def _r_own_container_construct(req: _ArgReq) -> bool:
    return _own_container_construct_arg(req.a, req.ptype, req.analyzer)


def _r_ru_wrapper_name_no_alias(req: _ArgReq) -> bool:
    # No `analyzer`, where the registered `ru_wrapper_name` passes one: the
    # alias-placeholder resolution inside the predicate no-ops without it, so
    # this cell admits strictly less. Transcribed as found -- widening it is
    # a change of its own.
    return _ru_wrapper_name_arg(req.a, req.ptype, req.locals_, req.narrowed)


def _r_ru_wrapper_own_literal(req: _ArgReq) -> bool:
    return _ru_wrapper_own_literal_arg(req.a, req.ptype,
                                       req.analyzer) is not None


def _r_own_genrec_literal(req: _ArgReq) -> bool:
    return (_own_genrec_return(req.ptype) is not None
            and isinstance(req.a, (TpyArrayLiteral, TpyDictLiteral))
            and _ru_instance_literal_ok(req.a, req.analyzer))


def _r_protocol_union(req: _ArgReq) -> bool:
    return _protocol_union_arg(req.a, req.ptype, req.locals_,
                               req.analyzer) is not None


def _r_protocol_union_literal_temp(req: _ArgReq) -> bool:
    return _protocol_union_literal_temp_arg(req.a, req.ptype,
                                            req.analyzer) is not None


def _r_protocol_union_iter_temp(req: _ArgReq) -> bool:
    return _protocol_union_iter_temp_arg(req.a, req.ptype, req.locals_,
                                         req.analyzer) is not None


def _r_protocol_slot_ctor(req: _ArgReq) -> bool:
    # The shared `protocol_slot` cell RESTRICTED to a bare NAME or a
    # @dynamic slot: a STRUCTURAL rvalue is ctor-inline here (a
    # single-required slot takes the plain arg render and the ctor loop
    # has no structural temp arm), so the shared cell would diverge here.
    pslot = _protocol_arg_slot(req.ptype)
    return (pslot is not None
            and (isinstance(req.a, TpyName) or is_dyn_protocol(pslot))
            and _protocol_slot_arg(req.a, req.ptype, req.locals_,
                                   req.analyzer, temps_ok=req.temps_ok))


def _r_record_rvalue_temp_ctor(req: _ArgReq) -> bool:
    # Neither the free ladder's `record_rvalue_temp` (which takes
    # `upcast_ok=True`, a CHILD-typed temp) nor the record-method's factory
    # cell: same-nominal, and flush-gated only at a MUTATED slot -- a const
    # ref slot binds the rvalue inline with no temp to flush. The guard sits
    # inside the cell because the ladder evaluated it AFTER the shape, and
    # the shape predicate witnesses as it runs.
    return (_record_rvalue_temp_arg(req.a, req.ptype, req.locals_,
                                    req.analyzer)
            and (req.temps_ok if req.mutated_slots else True))


def _r_const_rvalue(req: _ArgReq) -> bool:
    if not isinstance(req.a, TpyCall):
        return False
    arg_fi = req.a.resolved_function_info
    if arg_fi is not None and arg_fi.is_constructor:
        return _ctor_shape_ok(req.a, req.analyzer)
    return _record_rvalue_call_shape(req.a, req.analyzer)


def _r_str_pass_through_unmutated(req: _ArgReq) -> bool:
    # `mutated=False` hardcoded, like the record-method ladder's
    # `optional_ptr_no_temp`: the nested tail refuses a mutated String slot
    # outright in this row's pre-guard rather than passing the fact down.
    return _str_pass_through_arg(req.a, req.ptype, req.locals_, req.analyzer)


def _pre_ctor_nested_slot_family(req: _ArgReq) -> 'bool | None':
    """The nested tail's slot prologue: an `Own[scalar]` or plain scalar slot
    is DECIDED here -- a resolved scalar source passes, anything else is
    refused rather than falling through to the rows. A no-op spelling on a
    value type, so the by-value slot takes the scalar bare exactly as a plain
    slot does. Returns None for every other slot, which is what puts the row
    tuple in play at all."""
    slot = unwrap_readonly(unwrap_ref_type(req.ptype))
    if isinstance(slot, OwnType) and _eligible_scalar(
            unwrap_readonly(slot.wrapped)):
        return (_resolved_scalar(req.analyzer.get_expr_type(req.a),
                                 req.analyzer)
                and _witness("ctor.own_scalar_peel"))
    if _eligible_scalar(slot):
        return _resolved_scalar(req.analyzer.get_expr_type(req.a),
                                req.analyzer)
    return None


_CTOR_ARG_SINK = register_sink(_ArgSink(
    family="record_ctor",
    # The reject detail belongs to the CALLER: the three ctor gates tag their
    # own (`call.ctor_arg.<family>` / `ctor.arg.<family>`), and `note_detail`
    # is set-if-empty, so a tail spelled here would take their tag.
    note=None,
    # The one family that consults the callee's mutation facts, and it does
    # so per ARGUMENT: `arg_ok` folds this flag with the caller's
    # `is_mutated`. The sibling families produce the same render without
    # the rule; turning it on for them moves what they admit, so it is a
    # change of its own.
    mutated_slots=True,
    rows=(
        # A container LITERAL at a MUTATED ctor ref slot: the mutable ref
        # cannot bind a prvalue, so the temp is hoisted
        # (`std::vector<T> __tmp_N = {..}; Holder(__tmp_N)`) -- rendered
        # (and witnessed) at the ctor loop's argtemp row. A const slot keeps
        # the inline brace-init arm, further down.
        _ArgRow("mutated_container_literal", _r_mutated_container_literal,
                extra=_x_temps_and_mutated),
        _ArgRow("str_pass_through", _r_str_pass_through, face="ctor.str_arg"),
        # Witnessed at the render row (arg.own_opt_container_move), not here.
        _ArgRow("own_opt_container_ptr", _r_own_opt_container_ptr),
        _ArgRow("own_str_literal_bare", _r_own_str_literal_bare,
                face="ctor.own_str_literal"),
        _ArgRow("own_bytes_literal", _r_own_bytes_literal,
                face="ctor.own_bytes_literal"),
        _ArgRow("shared_pass_through", _r_shared_pass_through),
        _ArgRow("bytearray_rvalue_ctor", _r_bytearray_rvalue_ctor,
                face="ctor.bytearray_rvalue"),
        # A scalar VALUE into a value-repr Optional[scalar] ctor slot
        # renders bare (the implicit std::optional ctor); `None` rides the
        # row below -- mirrors the method-arg loop.
        _ArgRow("value_opt_scalar_value", _r_value_opt_scalar_value),
        _ArgRow("value_opt_name_pass", _r_value_opt_name_pass,
                face="ctor.value_opt_pass_arg"),
        # A str LITERAL into a value-repr Optional[str] ctor slot (the
        # total=False TypedDict face) renders bare the same way.
        _ArgRow("str_literal_value_opt", _r_str_literal_value_opt),
        # ... and its bytes / value-tuple siblings: the owned bytes literal
        # and the spelled tuple brace-init both convert into the optional
        # slot in place.
        _ArgRow("bytes_literal_value_opt", _r_bytes_literal_value_opt,
                face="ctor.bytes_literal_value_opt"),
        _ArgRow("tuple_literal_value_opt", _r_tuple_literal_value_opt,
                face="ctor.tuple_literal_value_opt"),
        # ... and the non-literal member sources at the same slot -- a
        # non-optional NAME, an enum member, a member-typed rvalue
        # (`timezone(off, tz_intern.name_at(id))` at `str | None`). The
        # marker ladder's row: the optional's converting ctor absorbs the
        # bare member render wherever the arg loop puts it.
        _ArgRow("value_opt_member", _r_value_opt_member),
        _ArgRow("none_value_opt", _r_none_value_opt),
        # `Box(None)`: the unit ctor arg renders the bare `std::monostate{}`
        # like the marker-call row.
        _ArgRow("none_unit", _r_none_unit),
        # A nested open-T instantiation inside a generic body
        # (`self.inner = Box[T](value)`): a NAME bound to the same bare T
        # passes the form-neutral slot bare, the ctor-face sibling of the
        # nested generic call rule.
        _ArgRow("tparam_name_pass", _r_tparam_name_pass),
        # The S1 view->owned convert at an `Own[str]` ctor slot
        # (`Box(x)` on a `str` param -> `Box<std::string>(std::string(x))`),
        # the free/method ladders' row -- ahead of the Own cascade for the
        # same reason it is there: the view->owned convert runs before the
        # move/copy-temp cascade.
        _ArgRow("str_owned_slot", _r_str_owned_slot),
        # The Own-slot cascade rows, mirrored from the free/method plain-arg
        # loop: the temp-free last-use move lands in any position; the copy
        # half hoists `__tmp_N` and so needs the enclosing flush point,
        # exactly like the record-rvalue temp row at the end.
        _ArgRow("own_move", _r_own_move, face="ctor.own_arg"),
        _ArgRow("own_lvalue", _r_own_lvalue, extra=_x_temps_ok,
                face="ctor.own_arg"),
        # The temp-free move-source slice also serves the flush-LESS direct
        # position (`[Box(h1)]` -- a ctor element in a container literal):
        # `std::move(name)` is position-independent, exactly the NESTED
        # family's admission.
        _ArgRow("own_move_source_slice", _r_own_move_source_slice,
                extra=_x_not_temps_ok, face="ctor.own_arg"),
        # A record/container-returning CALL rvalue into an Own slot
        # (`Appender(data.clone(), ..)`): binds the T&& slot inline, exactly
        # the free/method plain-arg row -- same predicate, same bare render
        # in `_lower_call_arg`'s Own-slot arms.
        _ArgRow("own_record_rvalue", _r_own_record_rvalue),
        # ... and its OPEN-slot sibling (`Box(p.value())` inside a generic
        # body): a T-returning call rvalue at a bare `Own[T]` ctor slot
        # binds the `T&&` prvalue with no temp and no move wrap, exactly as
        # at the method ladder's same slot -- the render is settled by the
        # rvalue-ness, not by the callee kind or the slot's spelling.
        _ArgRow("own_tparam_call_rvalue", _r_own_tparam_call_rvalue),
        # The pointer-repr Optional NAME half of the Own[Optional[record]]
        # slot: rebuild null-safely, then move in. The ctor-rvalue half is
        # `own_optional_record_rvalue` further down.
        _ArgRow("own_opt_ptr_name_move", _r_own_opt_ptr_name_move),
        # ... and the Optional[Own[record]] BY-VALUE slot sibling: a
        # ptr-repr Optional NAME lifts via ptr_to_optional_move at a movable
        # last use (`Boxed(tmp)`).
        _ArgRow("opt_own_ptr_opt_name_move", _r_opt_own_ptr_opt_name_move),
        # A record NAME moved into an `Optional[Own[T]]` ctor slot
        # (`Wrapper(p, tag)` at `Own[Point] | None` -- the by-value
        # `std::optional<Point>` param absorbs the bare `std::move(p)`): the
        # free/marker ladders' row; the shared lowering arm enforces the
        # move verdict and rejects the copy shape.
        _ArgRow("opt_own_record_name", _r_opt_own_record_name),
        # ... and its CONTAINER twin (`DictReader(buf, fn)` at
        # `Own[list[str]] | None`): same bare move, one arm.
        _ArgRow("opt_own_container_name", _r_opt_own_container_name,
                face="ctor.opt_own_container_name"),
        # `copy(name)` of a plain record into a same-nominal `Own[record]`
        # ctor slot (`Holder(copy(b))` -> `Holder(Box(b))`): the
        # copy-construct rvalue binds the T&& slot -- the free/method
        # gates' row.
        _ArgRow("copy_own", _r_copy_own),
        # `copy(src[i])` of an open-T element into an `Own[T]` slot
        # (`Owned(copy(src[0]))` -> `Owned<T>(T(__getitem__(src, 0)))`):
        # the generic copy tail around the element read.
        _ArgRow("copy_open_elem", _r_copy_open_elem),
        # A BARE container-element subscript at a still-open ctor slot
        # (`Bare(src[0])` -> `Bare<T>(__getitem__(src, 0))`): the element
        # read binds the `const U&` slot directly -- the free-call ladder's
        # `call.generic_open_slot_elem`, one callee family over (same
        # shared `_open_slot_match` rule).
        _ArgRow("generic_open_slot_elem", _r_generic_open_slot_elem,
                face="ctor.generic_open_slot_elem"),
        # A func-ref / callable-value NAME into a Callable ctor slot
        # (`Handler(double)` -> `Handler(double_)`): the free-call ladder's
        # rows, same bare renders through the tail.
        _ArgRow("func_ref", _r_func_ref),
        # An async-def NAME at a Callable ctor slot (`Dispatcher(handle)`):
        # the coroutine-factory wrapper lambda, the name arm's
        # async_factory_wrap row.
        _ArgRow("async_factory_wrap", _r_async_factory_wrap),
        _ArgRow("callable_value_pass", _r_callable_value_pass),
        _ArgRow("field_read_ref_ctor", _r_field_read_ref_ctor,
                face="ctor.field_read_ref_arg"),
        # A checked container-element read at a record ref slot
        # (`Player(things[0])`): the element lvalue binds the slot inline,
        # the free-call family's render.
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        # A comprehension at a container slot hoists the slot-typed ArgTemp
        # the free-call family renders (an lvalue, so a mutated `T&` slot
        # binds it too); flush-gated like every other temp row here.
        _ArgRow("container_comp", _r_container_comp, extra=_x_temps_ok),
        _ArgRow("container_literal", _r_container_literal,
                extra=_x_not_mutated, face="ctor.container_literal_arg"),
        # A list literal into an `Own[list]` ctor slot renders the same bare
        # in-place brace (`Summer([1, 2, 3])` -> `Summer({1, 2, 3})` --
        # prvalue into the by-value Own slot), the qualcall row's ctor face.
        _ArgRow("own_container_literal", _r_own_container_literal,
                face="ctor.container_literal_arg"),
        _ArgRow("own_container_comp", _r_own_container_comp),
        # The @dataclass default_factory fill: an empty container
        # instantiation into an `Own[container]` ctor slot renders the
        # spelled default ctor (`Foo(std::vector<int32_t>(), 1)`).
        _ArgRow("own_container_instantiation", _r_own_container_instantiation),
        # ... and its one-source sibling (`_WalkEmit(cur, list(names), ..)`):
        # the construct template renders in place, temp-free.
        _ArgRow("own_container_construct", _r_own_container_construct),
        # The M4c wrapper-slot NAME row, mirrored from the free-call ladder:
        # a same-wrapper NAME (non-generic alias or generic instance) binds
        # the borrow ctor slot bare (`Summary(seed)` -> `Summary(seed)`).
        _ArgRow("ru_wrapper_name_no_alias", _r_ru_wrapper_name_no_alias),
        # A scalar/str LITERAL into an `Own[wrapper]` ctor slot
        # (`Holder(7)` at `Own[V]` -- `V&&` binds the converting-ctor
        # prvalue): the bare target-less literal, no temp.
        _ArgRow("ru_wrapper_own_literal", _r_ru_wrapper_own_literal,
                face="ctor.ru_wrapper_own_literal"),
        # A container literal into an `Own[genrec]` ctor slot: the
        # ru-instance spelled render, inline (no temp).
        _ArgRow("own_genrec_literal", _r_own_genrec_literal),
        # The pointer-variant union rows, mirrored from the plain arg loop:
        # a member-typed record NAME / None lifts bare (`pv{&(name)}` --
        # temp-free); a member ctor RVALUE hoists a named temp and lifts its
        # address (`pv{&__tmp_N}`), so it needs the enclosing flush point.
        # Both lower through `_lower_union_arg_lift` in `_lower_call_arg`.
        _ArgRow("union_member_lift", _r_union_member_lift),
        _ArgRow("union_ctor_temp", _r_union_ctor_temp, extra=_x_temps_ok),
        # The remaining free-call union rows, same lowering arms: an
        # already-union NAME passes bare into a same-union slot
        # (`Zoo(init_pet, ..)`); a coerced union literal renders its member;
        # a member ctor RVALUE into an `Own[union]` slot binds the bare
        # expansion (`Sink(A(7))`).
        _ArgRow("union_pass_through", _r_union_pass_through,
                face="ctor.union_pass_arg"),
        _ArgRow("union_coerced_literal", _r_union_coerced_literal),
        _ArgRow("own_union_ctor", _r_own_union_ctor),
        # A record/Span NAME into a slot whose non-None members are all
        # PROTOCOLS (`ArrayList[int32, 8](a)` / `(s)` -- `src:
        # Iterable[Own[T]] | Spannable[T] | None`): both take the address-of
        # lift (the arm in `_lower_call_arg`).
        _ArgRow("protocol_union", _r_protocol_union,
                face="ctor.protocol_union_arg"),
        # ... and its literal sibling: a container literal at the same
        # nullable-protocol slot hoists the typed temp + addr lift
        # (`Counter(["a", "x", "x"])`), so it needs the flush.
        _ArgRow("protocol_union_literal_temp", _r_protocol_union_literal_temp,
                extra=_x_temps_ok),
        # ... and its iterator-rvalue sibling (`ArrayList[..](d.items())`):
        # the same typed temp + addr lift, so the same flush requirement.
        _ArgRow("protocol_union_iter_temp", _r_protocol_union_iter_temp,
                extra=_x_temps_ok),
        # A record RVALUE into an `Own[record | None]` slot binds bare
        # (`Outer("a", Inner(42))` -- prvalue -> optional<Inner>), the
        # @dataclass Optional-record-field row.
        _ArgRow("own_optional_record_rvalue", _r_own_optional_record_rvalue),
        # A record rvalue into a BY-VALUE record slot (a ValueType record
        # param -- `timezone(timedelta(...), "IST")`): no ref param, no temp
        # cascade, bare.
        _ArgRow("value_record_rvalue", _r_value_record_rvalue),
        # A member-valued arg into a VALUE-union ctor slot hoists the
        # `std::variant<...> __tmp_N = v;` temp (`datetime(..., tzinfo=ist)`)
        # -- the free-call arg-temp row, flush-gated.
        _ArgRow("value_union_temp", _r_value_union_temp, extra=_x_temps_ok),
        # Its narrowed sibling needs no temp (the alias is member-typed and
        # the variant's converting ctor takes it), so no flush gate. Not in
        # `_CTOR_NESTED_ARG_SINK`: a nested ctor arg is hoisted to a temp that
        # the DIRECT sink then admits, so the nested family never sees the
        # shape (no reject reachable there).
        _ArgRow("value_union_narrowed_pass", _r_value_union_narrowed_pass),
        # A tuple LITERAL at a tuple ctor slot: the borrow/value tuple
        # builders own the per-element admission (a bad element raises
        # inside lowering and rejects the body), exactly the
        # free-call row -- the gate checks only the slot/arity pairing.
        _ArgRow("tuple_literal", _r_tuple_literal),
        # A routable lambda into a Callable ctor slot renders its inline
        # closure, temp-free -- the free-call ladder's row.
        _ArgRow("lambda", _r_lambda, face="ctor.lambda_arg"),
        # The pointer-repr Optional slot faces (`n` into a `record | None`
        # ctor param -> `&(n)`), mirroring the free-call gate's row: the
        # ctor loop runs the same optional-ptr dispatch as the plain call
        # loop, and the temp-bearing 'ctor' face
        # is flush-gated inside the predicate.
        _ArgRow("optional_ptr", _r_optional_ptr),
        # A protocol-conformer arg into a @dynamic/structural protocol ctor
        # slot: a bare NAME / already-protocol lvalue passes through, and a
        # @dynamic RVALUE hoists the adapter temp (the ctor loop runs the
        # same @dynamic dispatch).
        _ArgRow("protocol_slot_ctor", _r_protocol_slot_ctor),
        # A concrete conformer into an `Own[@dynamic P]` ctor slot
        # (`Box(Dog(...))`): the make_unique / make_adapter wrap,
        # verdict-keyed via the shared classifier.
        _ArgRow("dyn_own_conformer", _r_dyn_own_conformer),
        _ArgRow("record_rvalue_temp_ctor", _r_record_rvalue_temp_ctor),
    )))


_CTOR_NESTED_ARG_SINK = register_sink(_ArgSink(
    family="record_ctor_nested",
    note=None,
    mutated_slots=True,
    pre=_pre_ctor_nested_slot_family,
    # A flush-less nested ctor position (`f(Holder(x))` inside an expression
    # that is not a statement): every admitted cell renders TEMP-FREE, which
    # is the whole rule the family expresses. It is not a prefix of the
    # direct family's tuple -- the shared cells sit in a different order and
    # the two decisive slot blocks have no direct-family counterpart.
    rows=(
        # Temp-free bare render, so the nested position admits it too.
        _ArgRow("own_str_literal_bare", _r_own_str_literal_bare,
                face="ctor.own_str_literal"),
        # `Dog(None)` nested in a call arg: the STORAGE-form `std::nullopt`
        # is temp-free and position-independent, so the nested position
        # admits it like the direct family.
        _ArgRow("none_value_opt", _r_none_value_opt,
                face="ctor.nested_none_value_opt"),
        # ... and the str-literal sibling (`Dog("rex")` at `str | None`):
        # the bare literal binds the optional's converting ctor, equally
        # temp-free.
        _ArgRow("str_literal_value_opt", _r_str_literal_value_opt,
                face="ctor.nested_none_value_opt"),
        # ... and the non-literal member sources at the same slot: the
        # bare member render is temp-free too, which is this family's
        # whole rule.
        _ArgRow("value_opt_member", _r_value_opt_member),
        # Temp-free like the const-rvalue cell below (the rvalue binds the
        # T&& slot inline), so the nested position admits it too.
        _ArgRow("own_record_rvalue", _r_own_record_rvalue),
        # The temp-free last-use move (`std::move(name)`) is
        # position-independent, so the nested position admits it too; the
        # flushable copy half stays DIRECT-only.
        _ArgRow("own_move", _r_own_move, face="ctor.own_arg"),
        # A body-movable local at its last use: lowering's Own-slot arm
        # picks the temp-free `std::move(name)` (`Producer(tx, 0)` nested in
        # `spawn(...)`), so the nested position admits exactly the
        # move-source slice of the copy+move row.
        _ArgRow("own_move_source_slice", _r_own_move_source_slice,
                face="ctor.own_arg"),
        # The make_unique / make_adapter conformer wraps are temp-free
        # in-place renders (`Box(Box(Dog(..)))` -- the inner Box's Dog arg),
        # so the nested position admits them like the rvalue rows above.
        _ArgRow("dyn_own_conformer", _r_dyn_own_conformer),
        # A record rvalue into a BY-VALUE record slot renders bare
        # (`timezone(timedelta(...))` -- no ref param, no temp), so the
        # nested position admits it like the direct family.
        _ArgRow("value_record_rvalue", _r_value_record_rvalue),
        # A pointer value at a by-value `Ptr[T]` slot: the ownership cascade
        # never fires there, so every leg renders in place (bare value,
        # `nullptr`, the storage-optional lift), which is this family's rule.
        _ArgRow("ptr_pass_through", _r_ptr_pass_through),
        # A checked container-element read binds the record ref slot inline
        # at a flush-less nested position too: it hoists nothing.
        _ArgRow("record_elem_subscript", _r_record_elem_subscript),
        # DECISIVE: an unmutated record-rvalue temp slot is answered HERE.
        # The ladder spelled it as an early `return`, so a source that is
        # not one of the two ctor/call shapes is REFUSED rather than falling
        # through to the str row below it.
        _ArgRow("const_rvalue", _r_const_rvalue,
                extra=_x_ctor_const_rvalue_slot, face="ctor.const_rvalue_arg",
                decisive=True),
        _ArgRow("str_pass_through_unmutated", _r_str_pass_through_unmutated,
                extra=_x_ctor_str_source_allowed, face="ctor.str_arg"),
    )))


def _record_ctor_arg_supported(
        arg: TpyExpr, param_type: TpyType, index: int, fi,
        lc: '_LowerCtx', declared: dict[str, TpyType], use: _ExprUse) -> bool:
    """Rows: `_CTOR_ARG_SINK` / `_CTOR_NESTED_ARG_SINK`.

    A NESTED_ARG position that carries the enclosing statement's flush right
    (`use.allow_temps`, threaded through call-shaped args, and set on the
    RECORD_TEMP recursion) gates as a DIRECT one -- its temps flush at the
    same statement. The restricted family serves only flush-less nested
    slots.
    """
    # fi is None for a TypedDict ctor (no synthetic constructor fi; params
    # come from the registry's init_params, which carry no mutation facts).
    mutation_unknown = fi is not None and fi.mutated_params is None
    mutated = (fi.mutated_params if fi is not None else None) or frozenset()
    direct = (use.record_ctor is not _RecordCtorUse.NESTED_ARG
              or use.allow_temps)
    return arg_ok(
        _CTOR_ARG_SINK if direct else _CTOR_NESTED_ARG_SINK,
        arg, param_type, declared, lc.analyzer,
        param_names=lc.prescan.param_names,
        narrowed=frozenset(lc.narrow.narrowed),
        # `temps_ok` tracks whether THIS ctor position flushes. DIRECT
        # threads its own allow_temps; a RECORD_TEMP source ctor flushes at
        # the enclosing statement too when it was reached via the
        # flush-enabled recursion, so `use.allow_temps` is the single source
        # of truth for both (and is False for the restricted family).
        temps_ok=use.allow_temps,
        index=index, is_mutated=index in mutated,
        mutation_unknown=mutation_unknown,
        inline_narrowed=lc.inline_narrowed,
        movable_locals=lc.movable_locals,
        pointers=lc.pointers,
        func_name=getattr(lc.func, "name", None))


def _require_method_call_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool, error_return_ok: bool = False) -> None:
    # The temp-free last-use MOVE at an Own slot fires before every other
    # arg consideration, so the gate admits the slice up front -- the
    # render arm picks THIRMove on the same shared verdict. Covers the
    # coerce-wrapped movable name (`out.append(buf)` under
    # bytearray->bytes) the per-family ladders' bare-name rows cannot.
    if _own_move_source_slice(a, ptype, lc, declared):
        return
    if not _method_call_arg_ok(
            e, a, ptype, index, declared, lc.analyzer,
            temps_ok=temp_args, narrowed=frozenset(lc.narrow.narrowed),
            param_names=lc.prescan.param_names,
            tparam_bounds=lc.tparam_bounds,
            error_return_ok=error_return_ok,
            movable_locals=lc.movable_locals,
            func_name=getattr(lc.func, "name", None)):
        raise ThirUnsupported(call_reject_reason("expr.method_call"))


def _btuple_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                     analyzer) -> bool:
    """A borrow-tuple-returning call whose result EXACTLY matches a
    borrow-form ptr-Optional tuple param -- or the mixed own/borrow F1
    flavor (`take_mixed(make_mixed(b))` at `tuple[Own[Box], Box]`) -- the
    bare-bind admission shared by the free-call ladder and its render arm
    (call.btuple_pass). Element-blind render, so both shape keys bind the
    same way."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = _ptr_optional_tuple(ptype)
    if pt is None:
        pt = _mixed_own_borrow_tuple(ptype, analyzer)
    if pt is None:
        return False
    at = analyzer.get_expr_type(a)
    ab = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return ab == pt


def _own_movable_tuple_pass_arg(a: TpyExpr, ptype: 'TpyType | None',
                                analyzer) -> bool:
    """An owned-movable tuple call RVALUE at the matching `&&` slot
    (`take_owned(make_owned())` at `std::tuple<Box, Box>&&`): the prvalue
    binds the rvalue-ref slot bare -- no move wrap (only NAME sources take
    the std::move / auto() decay pair)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)):
        return False
    pt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
          if isinstance(ptype, TpyType) else None)
    if not (isinstance(pt, TupleType) and pt.is_owned_movable()):
        return False
    if not is_rvalue_source(analyzer, a):
        return False
    at = analyzer.get_expr_type(a)
    ab = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return ab == pt


def _protocol_spelled_container_cpp(atu: 'TpyType',
                                    proto: 'NominalType') -> str:
    """The self-spelled C++ of a container literal hoisted at a protocol
    slot: an unresolved Int/FloatLiteralType ELEMENT (a call-arg literal
    sema never slot-resolves) would render its VALUE into the template-arg
    position (`std::array<1.0, 3>`), so substitute the protocol's element
    type, keeping the container flavor the resolver chose."""
    container_type = atu
    if (isinstance(proto, NominalType) and proto.type_args
            and len(proto.type_args) == 1
            and isinstance(atu, NominalType) and atu.type_args):
        elem = atu.type_args[0]
        if isinstance(elem, (FloatLiteralType, IntLiteralType)):
            container_type = replace(
                atu, type_args=(proto.type_args[0],) + atu.type_args[1:])
    return container_type.to_cpp()


def _lower_marker_method_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool, error_return_ok: bool = False,
        protocol_hoist: bool = False) -> THIRExpr:
    if isinstance(a, TpyVarargPack):
        # A `*args` pack into a variadic module function (math.hypot(3, 4)):
        # the qualcall arg loop forwards the pack unchanged, rendered via the
        # same pack render as a free call. The pack's own lowering
        # validates the element shapes and raises otherwise.
        return _lower_vararg_pack(a, ptype, lc, declared, temp_args=temp_args)
    if protocol_hoist:
        # The qualcall loop's protocol hoist (a PLAIN module callee only --
        # native/template callees skip that loop's first pass): a temporary
        # arg at a structural protocol slot hoists `auto __tmp_N = <rvalue>;`
        # -- C++ cannot deduce a template param from a braced-init-list, and
        # the temp must outlive the call. @dynamic slots take the adapter
        # path ahead of that arm and stay rejected here. The arm claims its
        # shapes ahead of the arg gate: it fully determines the render and
        # raises on anything it cannot lower.
        proto = _protocol_arg_slot(ptype)
        if (proto is not None and not is_dyn_protocol(proto)
                and isinstance(a, TpyArrayLiteral)):
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            at = lc.analyzer.get_expr_type(a)
            at = resolve_pending_container(at, lc.analyzer) or at
            atu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
            init = _lower_literal_arg(
                a, atu, lc, declared,
                "container-literal marker protocol arg on the make path",
                array_retype=False)
            if (isinstance(init, THIRContainerLiteral)
                    and init.typed_brace_cpp is None
                    and (is_list(atu) or is_array(atu))):
                init = replace(
                    init,
                    typed_brace_cpp=_protocol_spelled_container_cpp(atu,
                                                                    proto))
            _witness("argtemp.marker_protocol_literal")
            return THIRArgTemp(result_type=proto, cpp_type=None, init=init,
                               form=Form.BORROW, loc=getattr(a, "loc", None))
        if (proto is not None
                and _record_rvalue_structural_arg(a, proto, lc.analyzer)):
            # A record RVALUE at a structural slot
            # (`json.load(io.StringIO(s))`): the monomorphized param binds
            # `T&`, which the temporary cannot, so the same un-spelled
            # `auto __tmp_N = <rvalue>;` materialize.
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.marker_protocol_record")
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(
                    a, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE)),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        if (proto is not None and not is_dyn_protocol(proto)
                and _native_iterable_range_arg(a, ptype)):
            # The range-rvalue rung of the same hoist
            # (`auto __tmp_N = ::tpy::Range<int32_t>(1, 11);`), shape checks
            # mirroring the free-call native range row.
            range_fi = a.resolved_function_info
            if (len(a.args) not in (1, 2, 3) or range_fi is None
                    or not range_fi.cpp_template
                    or not _eligible_scalar(_range_counter_type(a,
                                                                lc.analyzer))):
                note_detail("call.native_range_shape")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.marker_protocol_range")
            return THIRArgTemp(result_type=proto, cpp_type=None,
                               init=_lower_range_object(a, lc, declared),
                               form=Form.BORROW, loc=getattr(a, "loc", None))
    # A GENERATOR-FACTORY rvalue at a generator callee's by-reference
    # iterable slot (`islice(count(), 4)`): the frame rvalue hoists into
    # a named statement temp (`auto __tmp_N = count();`) so the
    # reference param can bind it -- the readonly-ref-generator
    # flavor of the temporary-arg hoist. The factory call
    # lowers at ITERABLE use (the same admission a for-head source gets).
    if _genfac_like_call(a, lc.analyzer) and _genfac_like_call(e,
                                                               lc.analyzer):
        if not temp_args:
            raise ThirUnsupported(
                "protocol arg-temp outside a flush position")
        init = _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                        allow_temps=True))
        _witness("argtemp.genfac_ref_slot")
        return THIRArgTemp(result_type=lc.analyzer.get_expr_type(a),
                           cpp_type=None, init=init, form=Form.BORROW,
                           loc=getattr(a, "loc", None))
    _require_method_call_arg(
        e, a, ptype, index, lc, declared, temp_args=temp_args,
        error_return_ok=error_return_ok)
    return _lower_call_arg(
        a, ptype, lc, declared, temp_args=temp_args, marker_arg=True)


def _slice_bound_supported(b: 'TpyExpr | None', analyzer) -> bool:
    if b is None:
        return True
    bt = analyzer.get_expr_type(b)
    if bt is None:
        return False
    if _runtime_bigint(bt, analyzer):
        return _const_index(_unwrap_lit_coerce(b)) is None
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return is_fixed_int_type(
        resolve_int_literals(bt, analyzer.ctx.default_int_for_literal))


def _lower_slice_bound(b: 'TpyExpr | None', lc: '_LowerCtx',
                       declared: dict[str, TpyType], loc) -> 'THIRExpr | None':
    """One slice BOUND (lower / upper / step), for the READ arm and the
    slice-ASSIGN arm alike -- `::tpy::Slice` / `::tpy::BasicSlice` take
    `std::optional<int>` members either way, so a runtime-BigInt bound
    appends the `.to_fixed_check<int32_t>()` narrow (non-literal only -- the
    gate rejects literal BigInt bounds, whose render is ill-formed). The
    BigInt test keys on the DECLARED type, like `is_runtime_bigint`.
    A composite over a retro-widened local has no mirrored render, so it
    rejects; the enclosing statement composes the tag."""
    if b is None:
        return None
    analyzer = lc.analyzer
    key = _narrow_key_type(b, declared, analyzer)
    if key is _NARROW_UNMIRRORED:
        note_detail("slice.bound_widened_local")
        raise ThirUnsupported("expr.subscript", detail=True)
    lowered = _lower_expr(b, lc, declared)
    if not _runtime_bigint(key, analyzer):
        return lowered
    _witness("narrow.slice_bound")
    return THIRCoerce(result_type=INT32, expr=lowered,
                      coercion_name=_BIGINT_NARROW,
                      wrap="{0}.to_fixed_check<int32_t>()", loc=loc)


def _str_slice_receiver_supported(
        recv: TpyExpr, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> bool:
    analyzer = lc.analyzer
    if isinstance(recv, TpyBytesLiteral):
        # A bytes-literal receiver renders OWNED (`::tpy::bytes_literal_owned`),
        # the `{self}` substitution the slice @cpp_template threads -- matching
        # the target-less default for the literal.
        return True
    if isinstance(recv, TpyStrLiteral):
        # A str-literal receiver is position-neutral (const char[N]), so it
        # lands bare in the template like any other value read.
        return True
    if isinstance(recv, TpyName):
        return (recv.name in declared
                and (_resolved_viewfam_value(
                         declared[recv.name], analyzer) is not None
                     # A bytearray local: bytes_slice takes the container
                     # by reference and yields the view result the slice
                     # arm's viewfam check keys on.
                     or (recv.name not in lc.pointers
                         and is_bytearray_type(unwrap_readonly(
                             unwrap_ref_type(unwrap_send_sync(
                                 declared[recv.name])))))
                     # A None-NARROWED Optional[view] binding reads its
                     # `(*s)` deref (a borrow view); the slice/getitem
                     # templates interpolate the deref'd read. No narrow-set
                     # test: subscripting an un-narrowed Optional is a sema
                     # error, so reaching here implies the proof (the
                     # wide-opt arg row's rule).
                     or _value_opt_view_binding(recv.name, lc)))
    if isinstance(recv, TpyFieldAccess):
        return (_field_receiver_ok(recv, declared, analyzer)
                and _resolved_viewfam_value(
                    analyzer.get_expr_type(recv), analyzer) is not None)
    if isinstance(recv, TpyCall):
        return (_resolved_viewfam_value(
            analyzer.get_expr_type(recv), analyzer) is not None)
    if isinstance(recv, TpySubscript) and not isinstance(recv.index, TpySlice):
        # A container ELEMENT read (`self.chunks[0][a:b]`): the checked
        # element read is what the slice template interpolates, and the
        # element's own lowering owns its shape -- one it has no arm for
        # raises there and falls the body back whole. A SLICE index is
        # excluded: a slice-of-a-slice receiver is a view rvalue, whose
        # render is unwitnessed here.
        return (_resolved_viewfam_value(
            analyzer.get_expr_type(recv), analyzer) is not None)
    return False


def _bytes_membership_ok(e, declared: dict[str, TpyType], analyzer) -> bool:
    """`needle in b` over a bytes / BytesView container -- the native
    free-function `bytes_contains` (single-byte needle) / `bytes_contains_sub`
    (bytes-substring needle) arm. The receiver is a lowerable bytes-family
    value (a local name, a bytes literal, or an F1-field read); a global
    (indirect) receiver is not in `declared`, so it is excluded. The needle is
    a bytes-family value (substring form) or an eligible integer (single-byte
    form). The tuple-literal membership shape is handled separately."""
    if e.op not in _MEMBERSHIP_OPS or isinstance(e.right, TpyTupleLiteral):
        return False
    fi = e.resolved_contains
    if (fi is None or e.typed_dict_in_field is not None
            or not fi.native_function or not fi.native_name):
        return False
    rt = _declared_type(e.right, declared, analyzer)
    if rt is None or _resolved_bytes_value(rt, analyzer) is None:
        return False
    recv = e.right
    if isinstance(recv, TpyName):
        if (recv.name not in declared
                or _resolved_bytes_value(declared[recv.name], analyzer) is None):
            return False
    elif isinstance(recv, TpyFieldAccess):
        if not (_field_receiver_ok(recv, declared, analyzer)
                and _resolved_bytes_value(analyzer.get_expr_type(recv), analyzer)
                is not None):
            return False
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A bytes-returning call haystack (`b"..." in sock.recv(n)`): the
        # call renders inline as the first bytes_contains[_sub] operand; its
        # own lowering gates the callee/arg shapes.
        if _resolved_bytes_value(analyzer.get_expr_type(recv),
                                 analyzer) is None:
            return False
    elif not isinstance(recv, TpyBytesLiteral):
        return False
    lt = _declared_type(e.left, declared, analyzer)
    if lt is None:
        return False
    return (_resolved_bytes_value(lt, analyzer) is not None
            or _resolved_scalar(lt, analyzer))


def _str_membership_ok(e, declared: dict[str, TpyType], analyzer) -> bool:
    """`needle in s` / `not in` over a str-family container (str/String/StrView):
    no `__contains__` member, so it renders `(s.find(needle) != npos)` /
    `== npos` (`not in`), the `is_any_str_type` arm. The receiver
    is a lowerable str value -- a local NAME, an F1-field read, or a str LITERAL
    (wrapped in string_view at emit). The needle is a char or str value, which
    renders bare into `.find(...)`. Takes precedence over the
    generic `resolved_contains` arm only when there is no such member (str has
    none), so the two never overlap."""
    if (e.op not in _MEMBERSHIP_OPS or isinstance(e.right, TpyTupleLiteral)
            or e.resolved_contains is not None
            or e.typed_dict_in_field is not None):
        return False
    rt = _declared_type(e.right, declared, analyzer)
    if rt is None:
        return False
    bare_rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    if not is_any_str_type(bare_rt):
        return False
    recv = e.right
    if isinstance(recv, TpyName):
        if recv.name not in declared:
            return False
    elif isinstance(recv, TpyFieldAccess):
        if not _field_receiver_ok(recv, declared, analyzer):
            return False
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A str-returning CALL rvalue receiver (`"timed out" in str(e)` ->
        # `(std::string(::tpy::__str__(e)).find("timed out") != npos)`):
        # the call renders through its own lowering arm, whose reject falls
        # the body back whole -- admission only pins the shape.
        pass
    elif not isinstance(recv, TpyStrLiteral):
        return False
    lt = _declared_type(e.left, declared, analyzer)
    if lt is None:
        return False
    return (_eligible_char(lt)
            or _resolved_str_value(lt, analyzer) is not None)


def _container_slice_recv_ok(recv: TpyExpr, lc: '_LowerCtx',
                             declared: dict[str, TpyType]) -> bool:
    """The receiver of a list/Array/Span slice read (`items[a:b:c]` ->
    `list_slice`/`list_stepped_slice`): a bare-name / F1-field container the
    `__getitem__` @cpp_template threads unchanged. The owned-list result
    renders STORAGE at every sink."""
    analyzer = lc.analyzer
    if isinstance(recv, TpyName):
        t = declared.get(recv.name)
    elif isinstance(recv, TpyFieldAccess):
        if _module_var_access_pair(recv, declared, analyzer) is not None:
            # A pointer-slot module-var container (`sys.argv[1:]`): the
            # slice template is a PINNED consumer of the `(*slot)` read,
            # like the native-slot arg the module-var arm documents.
            t = analyzer.get_expr_type(recv)
        elif _field_receiver_ok(recv, declared, analyzer):
            t = analyzer.get_expr_type(recv)
        else:
            return False
    elif isinstance(recv, (TpyCall, TpyMethodCall)):
        # A container/span-returning CALL rvalue (`self.__span__()[index]`):
        # the template interpolates the bare call, and the call's own
        # lowering owns its shape -- the str-slice arm's call leg.
        t = analyzer.get_expr_type(recv)
    else:
        return False
    if t is None:
        return False
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    # varargs included: `items[1:3]` on a `*items` param takes the same
    # list_slice template (mode-preserving, yields another varargs).
    return (is_list(bare) or is_array(bare) or is_span(bare)
            or is_varargs(bare))


def _subscript_yields_borrow_ptr(sub: TpySubscript, lc: '_LowerCtx') -> bool:
    """`std::get<N>(t)` is a bare `T*` (member access `->`) iff element N is a
    plain non-value BORROW_REF
    pointer-repr slot read from a borrow-form tuple. An owned (`Own`) or value element
    is held by value in the tuple (`std::get` yields a `T&`, `.` access), and a storage
    `auto&&` alias receiver likewise holds its elements by value -- both take `.`.
    A REASSIGNED borrow-tuple WALRUS receiver reads its elements from the
    COLLAPSED borrow type (`Own[T]` element -> `T*`, `->` access -- the
    walrus arm registers the same collapsed binding)."""
    res = _subscript_index_and_tuple(sub, lc.analyzer)
    if res is None:
        return False
    recv_t, idx = res
    if isinstance(sub.obj, TpyName):
        # A literal-bound frame slot's ownership is not in the declared type,
        # so the per-element verdict below reads the effective one.
        _eff = lc.frame_own_tuple_types.get(sub.obj.name)
        if isinstance(_eff, TupleType):
            recv_t = _eff
        if (sub.obj.name in lc.storage_tuple_locals
                # A walrus-slot tuple (`std::optional<std::tuple<..>>`)
                # holds its elements BY VALUE: `std::get<i>((*t))` yields
                # a `T&`, `.` access -- the storage-alias rule.
                or sub.obj.name in lc.walrus_slot_locals):
            return False
    elif isinstance(sub.obj, (TpyCall, TpyMethodCall)):
        # A MIXED-own-tuple CALL receiver holds its ref elements as bare
        # pointers (`std::get<1>(make_mixed(b))->val`) -- the mixed render
        # survives in the call result itself.
        if not _renders_own_borrow_tuple(sub.obj, lc.own_borrow_tuple_locals,
                                         lc.analyzer):
            return False
    elif (isinstance(sub.obj, TpyNamedExpr)
          and sub.obj.target in lc.prescan.reassigned):
        collapsed = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            collapse_tuple_own_elements(recv_t))))
        if not isinstance(collapsed, TupleType):
            return False
        recv_t = collapsed
    else:
        return False
    et = recv_t.element_types[idx]
    return (et.value_form() is ValueForm.BORROW_REF
            and TupleType._element_is_pointer_repr(et))

def _field_over_walrus_subscript_ok(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """`(t := make_pair(9))[1].val` -- a value-scalar field read off a
    record-element subscript over a REASSIGNED borrow-tuple walrus: the
    collapsed borrow element is a `T*`
    (`std::get<1>((t = ..., t))->val`, the `_subscript_yields_borrow_ptr`
    arrow). The walrus arm validates its own target/source classes."""
    if not (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and isinstance(e.obj, TpySubscript)
            and isinstance(e.obj.obj, TpyNamedExpr)
            and e.obj.obj.target in lc.prescan.reassigned):
        return False
    if not _subscript_yields_borrow_ptr(e.obj, lc):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        lc.analyzer.get_expr_type(e))))
    return bool((_eligible_scalar(ft) or _eligible_char(ft))
                and _witness("field.walrus_subscript_recv"))


def _subscript_result_form(sub: TpySubscript, rtype: TpyType, lc: '_LowerCtx') -> Form:
    """The form a tuple subscript result renders as. A value scalar is VALUE; a record
    element is BORROW (a `T*`/`T&`). An Optional element read off a storage-tuple alias
    or a storage-form tuple FIELD (`c.data[N]` -- elements held by value) is STORAGE
    (`std::optional<T>`, lifted by the consumer via optional_to_ptr); off a
    borrow tuple param it is already `T*` (BORROW)."""
    if not _is_borrow_form_name(rtype):
        return Form.VALUE
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if (isinstance(inner, OptionalType)
            and ((isinstance(sub.obj, TpyName)
                  and sub.obj.name in lc.storage_tuple_locals)
                 or isinstance(sub.obj, TpyFieldAccess))):
        return Form.STORAGE
    return Form.BORROW

def _whole_optional_bare(x: THIRExpr) -> THIRExpr:
    """Strip a NAME read's deref-on-narrow: the WHOLE optional passes bare
    into a matching optional slot. Scoped to THIRName so it cannot strip
    another node kind's deref -- a THIRSelf's deref is the receiver's own
    pointer read, not a narrowing artifact."""
    return replace(x, deref=False, optional_read=(
        replace(x.optional_read, extract=False) if x.optional_read is not None else None
    )) if isinstance(x, THIRName) else x


def _raw_pointer_slot(x: THIRExpr) -> THIRExpr:
    """Strip the receiver read's deref at a slot that binds the POINTER
    itself -- a `T*` borrow-tuple element, where `this` passes bare because
    `&(this)` would be a `T**`. An address-of slot keeps the deref instead:
    `&((*this))` IS the pointer."""
    return replace(x, deref=False) if isinstance(x, THIRSelf) else x


def _self_recv_positioned(
        node: 'THIRFieldAccess | THIRMethodCall',
) -> 'THIRFieldAccess | THIRMethodCall':
    """Strip the receiver's own deref where the member is reached THROUGH the
    receiver pointer. `THIRSelf` carries the plain-method deref intrinsically
    so that every VALUE sink renders `(*this)` without having to know about
    the receiver; a member behind an arrow wants the raw pointer, and
    `(*this)->x` would be ill-formed. Keyed on the node's own
    `receiver_through_pointer`, the same predicate `validate` enforces, so
    the render and its guard cannot drift. Raw `T*` slots are the OTHER
    consumer that needs the bare pointer -- `_raw_pointer_slot`, with no
    guard behind it."""
    recv = node.receiver
    if (isinstance(recv, THIRSelf) and recv.deref
            and node.receiver_through_pointer):
        return replace(node, receiver=replace(recv, deref=False))
    return node


def _name_recv_is_arrow(obj: TpyExpr, lc: '_LowerCtx') -> bool:
    """`recv->member` for a NAME receiver: a plain `T*` pointer-local (F2), a
    proven pointer-repr Optional borrow name (both in `lc.pointers`), or the
    `self` receiver (a `this` pointer). A poly-narrowed `self` reads through
    the cast pointer's DEREF (`(*__self_ptr)`), so the access is `.`.
    Codegen's `is_indirect_name` minus its narrowed carve-out, in one place
    so the arrow arms cannot disagree with each other."""
    return (isinstance(obj, TpyName)
            and (_ptr_read_derefs(obj.name, lc)
                 or (obj.name == lc.self_receiver and lc.self_is_pointer
                     and obj.name not in lc.narrow.spelled)))


def _ptr_recv_access(ptr_non_null: bool) -> 'tuple[bool, bool]':
    """The `(is_arrow, deref_check)` pair for an explicit `Ptr[record]`
    receiver: `recv->member` where sema proved the pointer non-null,
    `::tpy::deref_check(recv).member` otherwise. One helper so the arrow and
    the check stay each other's complement at every Ptr receiver."""
    return ptr_non_null, not ptr_non_null


def _field_is_arrow(e: TpyFieldAccess, lc: '_LowerCtx') -> bool:
    """`recv->field` vs `recv.field`: a plain `T*` pointer-local (F2), a proven
    pointer-repr Optional borrow name (an Optional-ptr param / OPTIONAL_TO_PTR
    local -- both in `lc.pointers`), or the `self` receiver (a `this` pointer)
    renders `->`; a record param / `T&` alias receiver renders `.`. Decided from
    the pointer set lowering tracks plus the method receiver. (An UNPROVEN
    Optional access never reaches this -- it takes the deref_check arm.)

    A record-element tuple subscript receiver (`t[N].field`) renders `->` only when the
    element is a borrow `T*` (`_subscript_yields_borrow_ptr`): a bare-reference element
    off a borrow-form tuple param. An owned element (`std::get` yields `T&`) or a
    storage `auto&&` alias receiver reads `.`."""
    obj = e.obj
    if isinstance(obj, TpySubscript):
        return _subscript_yields_borrow_ptr(obj, lc)
    return _name_recv_is_arrow(obj, lc)

def _is_own_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is an `Own[...]`-declared param of the function being
    lowered (incl. the own-optional shapes) -- the storage-owning binding."""
    for n, t in lc.params:
        if n == name:
            return (isinstance(t, TpyType)
                    and unwrap_optional_own(unwrap_readonly(t)) is not None)
    return False


def _unproven_opt_scalar_inner(e: TpyExpr, lc: '_LowerCtx',
                               analyzer) -> 'TpyType | None':
    """The INNER scalar type when `e` is an UNPROVEN value-repr Optional[scalar]
    name read (occurrence still Optional) -- an arithmetic / ordering-compare /
    unary operand unwrapped through the runtime-checked
    `::tpy::deref_optional_check(x)`. None for a narrowed read (the name arm's
    `(*x)` deref handles it) or any non-value-opt operand."""
    if not (isinstance(e, TpyName) and _value_opt_scalar_binding(e.name, lc)):
        return None
    et = analyzer.get_expr_type(e)
    opt = _value_opt_scalar(et, analyzer) if et is not None else None
    return opt.inner if opt is not None else None


def _lower_unproven_opt_scalar(e: TpyExpr, lc: '_LowerCtx',
                               declared: dict[str, TpyType]) -> 'THIRExpr | None':
    """Lower an unproven value-opt scalar operand to the checked-unwrap read
    (`::tpy::deref_optional_check(x)`); None when `e` is not one, so the caller
    lowers it normally. The subscript twin (`items[1] + 1` on
    `list[int32 | None]`) wraps the whole-optional element read the same way."""
    analyzer = lc.analyzer
    if (isinstance(e, TpySubscript) and not isinstance(e.index, TpySlice)
            and e.slice_function_info is None):
        et = analyzer.get_expr_type(e)
        opt = _value_opt_scalar(et, analyzer) if et is not None else None
        if opt is None or not _container_value_optional_elem(
                _subscript_container_recv_type(e.obj, declared, analyzer),
                analyzer):
            return None
        lowered = _lower_expr(e, lc, declared, allow_whole_optional=True)
        if not isinstance(lowered, THIRSubscript):
            return None
        return replace(lowered, result_type=opt.inner, opt_deref_check=True)
    if isinstance(e, TpyFieldAccess):
        # The FIELD twin (`local.value + 1` on an unproven `int32 | None`
        # field): the whole-optional member read wraps in the same checked
        # unwrap -- `deref_optional_check(local->value)`. The receiver
        # shape gates in the field lowering (fail-closed).
        et = analyzer.get_expr_type(e)
        opt = _value_opt_scalar(et, analyzer) if et is not None else None
        if opt is None:
            return None
        lowered = _lower_expr(e, lc, declared, allow_whole_optional=True)
        if not isinstance(lowered, THIRFieldAccess):
            return None
        _witness("field.opt_deref_check_read")
        return THIRCall(result_type=opt.inner, callee="deref_optional_check",
                        native_name="tpy::deref_optional_check",
                        args=(lowered,), form=Form.VALUE,
                        loc=getattr(e, "loc", None))
    inner = _unproven_opt_scalar_inner(e, lc, lc.analyzer)
    if inner is None:
        return None
    return THIRName(result_type=inner, name=e.name, form=Form.VALUE,
                    opt_deref_check=True, loc=getattr(e, "loc", None))


def _narrowed_opt_operand(e: TpyExpr, t: 'TpyType | None', lc: '_LowerCtx',
                          analyzer) -> 'TpyType | None':
    """The compare gate's operand type for a NARROWED value-opt scalar name:
    its read renders the `(*x)` deref (the name arm's deref-on-narrow), so
    the gate must judge the inner scalar, not the declared Optional binding
    (`_declared_type` returns the latter). Un-narrowed reads keep the
    declared type -- sema rejects a bare Optional compare anyway.

    A narrowed value-opt `Optional[str]` binding reads the same way (`(*ka)`,
    a str the compare templates take bare), so it judges its inner too. The
    bytes flavor's deref is a span/vector pair with no witnessed compare
    render, so it keeps the declared type and rejects."""
    if not isinstance(e, TpyName):
        return t
    if not (_value_opt_scalar_binding(e.name, lc)
            or (_value_opt_view_binding(e.name, lc)
                and _resolved_str_value(analyzer.get_expr_type(e), analyzer)
                is not None)):
        return t
    et = analyzer.get_expr_type(e)
    return t if isinstance(unwrap_readonly(et), OptionalType) else et


def _opt_f1_record(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The F1-record inner of a value/storage Optional operand, or None."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    if not isinstance(u, OptionalType):
        return None
    inner = unwrap_readonly(u.inner)
    return inner if _f1_record(inner, analyzer) else None


def _value_elem_container(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """A list/set/dict whose elements are scalar/str values, or None -- the
    container `==` renders member-wise via the std container operator, and
    the whole-member field read (`repr(self.lookup)`) passes it bare the
    same way."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    if u is None or not (is_list(u) or is_set(u) or is_dict(u)):
        return None
    args = getattr(u, "type_args", ())
    if not args:
        return None
    ok = all(
        _eligible_scalar(unwrap_readonly(a))
        or _owned_str_slot(unwrap_readonly(a), analyzer) is not None
        for a in args if isinstance(a, TpyType))
    return u if ok else None


def _bare_field_eq_pair(e: TpyBinOp, lt: 'TpyType | None',
                        rt: 'TpyType | None', analyzer) -> bool:
    """An `==`/`!=` over two UN-narrowed FIELD reads whose members compare
    bare via the std operator: an Optional[F1-record] pair (`this->inner ==
    other.inner`, std::optional's operator== over the record's friend
    operator==) or a value-element list/set pair (`this->items ==
    other.items`) -- the dataclass __eq__ shapes sema does not tag
    optional_safe_eq. A NARROWED field's analyzed type is its inner, so it
    never reaches these optional/container classes."""
    if e.op not in ("==", "!="):
        return False
    if not (isinstance(e.left, TpyFieldAccess)
            and isinstance(e.right, TpyFieldAccess)):
        return False
    lo = _opt_f1_record(lt, analyzer)
    if lo is not None:
        return _opt_f1_record(rt, analyzer) == lo
    lc_t = _value_elem_container(lt, analyzer)
    rc_t = _value_elem_container(rt, analyzer)
    return lc_t is not None and lc_t == rc_t


def _opt_scalar_eq_pair(
        e: TpyBinOp, lt: 'TpyType | None', rt: 'TpyType | None', analyzer
        ) -> 'tuple[TpyType | None, TpyType | None] | None':
    """sema's optional_safe_eq over value-repr `Optional[scalar]` operands
    (`x == y` with `x: int32 | None`): C++ `std::optional`'s native mixed
    comparison, both sides rendered bare. Returns the (left, right) operand
    TARGET types (the plain side opposite an UN-narrowed optional is
    target-typed to that optional's inner -- drives char/numeric literal
    renders; every other slot is None), or None when the pair is outside
    the slice. `lt`/`rt` arrive post-`_narrowed_opt_operand`, so a NARROWED
    optional side is already its plain inner (it derefs at the name arm, no
    target needed). Only
    scalar/char/enum inners are admitted (`_value_opt_scalar`); the
    Optional[view] families keep their own arg-split machinery."""
    if e.op not in ("==", "!=") or not e.optional_safe_eq:
        return None
    lo = _value_opt_scalar(lt, analyzer)
    ro = _value_opt_scalar(rt, analyzer)
    if lo is None and ro is None:
        # The OWNED-str/bytes optional slice (`expandvars(..) ==
        # os.getenv(..)` -- `std::string == std::optional<std::string>`
        # via the std mixed operator, both sides bare). RVALUE optional
        # sides only: a NAME/FIELD optional keeps its narrowing arms.
        lov = _value_opt_owned_view(lt, analyzer)
        rov = _value_opt_owned_view(rt, analyzer)

        def ok_str_side(side: TpyExpr, t: 'TpyType | None',
                        ov: 'OptionalType | None') -> bool:
            if ov is not None:
                # Witnessed shapes only (the whitelist discipline): call
                # rvalues, plus UN-narrowed FIELD reads (`this->label ==
                # other.label`, the dataclass __eq__ pair -- a NARROWED
                # field's analyzed type is its inner, so it never reaches
                # this optional branch). Subscripts/ternaries stay out.
                return isinstance(side, (TpyCall, TpyMethodCall,
                                         TpyFieldAccess))
            return (_resolved_str_value(t, analyzer) is not None
                    or _is_string_owned(t))

        if ((lov is not None or rov is not None)
                and ok_str_side(e.left, lt, lov)
                and ok_str_side(e.right, rt, rov)):
            return (None, None)
        return None

    def plain_ok(side: TpyExpr, t: 'TpyType | None',
                 other: 'OptionalType | None') -> bool:
        # A single-char str literal opposite an Optional[char] renders the
        # target-typed `'x'` (the _comparison_targets char arm over the
        # optional's inner); multi-char literals never char-render.
        if isinstance(side, TpyStrLiteral):
            return (other is not None and _eligible_char(other.inner)
                    and len(side.value) == 1)
        return bool(_resolved_scalar(t, analyzer) or _eligible_char(t)
                    or _eligible_enum(t, analyzer) is not None)

    if lo is None and not plain_ok(e.left, lt, ro):
        return None
    if ro is None and not plain_ok(e.right, rt, lo):
        return None
    l_tgt = ro.inner if ro is not None and lo is None else None
    r_tgt = lo.inner if lo is not None and ro is None else None
    return (l_tgt, r_tgt)


def _binop_operand_suffix(e: TpyBinOp, declared: dict[str, TpyType],
                          analyzer) -> str:
    fam = ""
    for operand in (e.left, e.right):
        t = _declared_type(operand, declared, analyzer)
        if t is None:
            continue
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if contains_type_param(t):
            return ".tparam"
        if isinstance(t, NominalType) and t.is_user_record and not fam:
            fam = ".genrec" if t.type_args else ".record"
    return fam


def _view_at_runtime(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """Whether a str/bytes-family operand spells its family's VIEW type at
    C++ runtime (`std::string_view` / `::tpy::BytesView`).

    The RESOLVED type is not the answer: a plain `str`/`bytes` PARAM is
    declared owned but its signature slot is the view, so judging an operand
    at its resolved type spells conversions that do not exist -- the two
    families drift the same way, which is why the value-select cast decision
    and `copy()`'s source spelling read ONE predicate. A str literal is
    view-form everywhere; a bytes literal's own render is target-driven
    (`bytes_literal` vs `bytes_literal_owned`), so it answers through its
    resolved type like any other operand. and/or / ternary chains recurse.

    A REBOUND param still answers "view" here although the prologue declares
    an owned local under its name. Answering "owned" was tried: against a
    bytes LITERAL (also "owned") the pair then MATCHED, and the select's
    view-typed sink bound a span over the literal's vector temporary -- a
    dangling render with no diagnostic; against a still-view param it split
    the pair into the mixed row, which for str casts toward the OWNED
    spelling and would dangle at a view sink the same way. Until the cast
    takes its direction from the sink's form (a TODO item), a rebound param
    against an owned operand rejects at the mixed row.

    The if-expression rows ask the same question through their own
    `_str_view_arm` / `_bytes_view_arm` pair, which additionally models the
    Optional-param deref and coerce arms."""
    analyzer = lc.analyzer

    def _is_view(t: 'TpyType | None') -> bool:
        rv = _resolved_viewfam_value(t, analyzer)
        return rv is not None and (is_str_view_type(rv)
                                   or is_bytes_view_type(rv))

    if isinstance(e, TpyName):
        pt = next((t for n, t in lc.params if n == e.name), None)
        if pt is not None:
            ptu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
            if is_str_type(ptu) or is_bytes_type(ptu):
                return True
        return _is_view(analyzer.get_expr_type(e))
    if isinstance(e, TpyStrLiteral):
        return True
    if isinstance(e, TpyBinOp) and e.op in _LOGICAL_OPS:
        return (_view_at_runtime(e.left, lc)
                and _view_at_runtime(e.right, lc))
    if isinstance(e, TpyIfExpr):
        return (_view_at_runtime(e.then_expr, lc)
                and _view_at_runtime(e.else_expr, lc))
    return _is_view(analyzer.get_expr_type(e))


def _contains_isinstance_fact(e: TpyExpr) -> bool:
    """Whether an isinstance call sits anywhere in the operand tree -- its
    inline facts would narrow the RHS render, which this lowering does not
    model."""
    if (isinstance(e, TpyCall)
            and (e.isinstance_var is not None
                 or e.isinstance_type is not None)):
        return True
    return isinstance(e, TpyExpr) and any(
        _contains_isinstance_fact(c) for c in e.children())


def _lower_value_select(e: TpyBinOp, rtype: 'TpyType | None',
                        lc: '_LowerCtx', declared: dict[str, TpyType],
                        loc, *, temps_ok: bool = False) -> THIRExpr:
    """Value-position and/or -> THIRValueSelect (tier A: scalar / float /
    BigInt / str / bytes results -- the two view families share the
    `.empty()` truthiness and the view-vs-owned form split). Record results,
    the rvalue-RHS pointer-select, inline-isinstance LHS facts, and every
    non-tier-A truthiness family raise `binop.shape.<op>`."""
    analyzer = lc.analyzer

    def rej(d: str) -> None:
        note_detail(d)
        raise ThirUnsupported(f"binop.shape.{e.op}", detail=True)

    res_str = _resolved_str_value(rtype, analyzer)
    res_owned = _is_string_owned(rtype)
    res_bytes = _resolved_bytes_value(rtype, analyzer)
    res_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
             if rtype is not None else None)
    res_u = resolve_pending_container(res_u, analyzer) or res_u
    # A `Span[T]` result is a value view copied like a scalar, so the select
    # is the same bare ternary -- only its truthiness differs, and that comes
    # from the shared classifier below.
    res_span = res_u is not None and is_span(res_u)
    if not (_resolved_scalar(rtype, analyzer) or res_str is not None
            or res_owned or res_bytes is not None or res_span):
        # Any reference-form result aliases the chosen operand, so the axis
        # IS the admission -- the per-type residue (which truthiness test
        # the LHS spells) comes from the shared classifier inside the arm.
        if res_u is not None and _f1_ref(res_u, analyzer):
            if _contains_isinstance_fact(e.left):
                rej("valuesel.isinstance_lhs")
            return _lower_container_select(e, res_u, lc, declared, loc, rej)
        rej("valuesel.result_type")
    if _contains_isinstance_fact(e.left):
        rej("valuesel.isinstance_lhs")
    lt_res = analyzer.get_expr_type(e.left)
    rt_res = analyzer.get_expr_type(e.right)
    l_str = _resolved_str_value(lt_res, analyzer)
    # The LHS truthiness spelling comes from the SHARED classifier, so a
    # select and a plain `if` over the same operand cannot drift apart (that
    # drift is what left `bytes`/`Span` operands with no admitted render).
    # A None mode is the operand's own value render, which only a scalar
    # has; the two whole-cell modes have no ValueSelect emit arm.
    truthy_mode = _truthiness_mode(lt_res, analyzer)
    if (truthy_mode in (TruthinessMode.TO_BOOL, TruthinessMode.IS_TRUTHY)
            or (truthy_mode is None
                and not _resolved_scalar(lt_res, analyzer))):
        rej("valuesel.lhs_truthy")
    lowered_lhs = _lower_expr(e.left, lc, declared,
                              use=_ExprUse(pos=SinkPos.OPERAND))
    lhs_temp_cpp = None
    if not isinstance(e.left, TpyName):
        lhs_temp_cpp = ("std::string_view"
                        if l_str is not None
                        and isinstance(e.left, TpyStrLiteral)
                        else "auto&&")
    # The RHS evaluates only in its branch: the conditional-operand right
    # (audited temps defer through the emit's ValueSelect region, unaudited
    # would-defer ones reject at the exit check), gated on the enclosing
    # flush right like every other grant site.
    lowered_rhs = _lower_expr(e.right, lc, declared,
                              use=_ExprUse(pos=SinkPos.OPERAND,
                                           allow_temps=temps_ok),
                              cond_eager=temps_ok)
    r_str = _resolved_str_value(rt_res, analyzer)
    rhs_sv = (not isinstance(e.right, TpyName) and r_str is not None
              and isinstance(e.right, TpyStrLiteral))
    # The mixed-operand conversion wraps: compare the two sides' runtime
    # C++ spellings.
    def _cmp_cpp(side: TpyExpr, st: 'TpyType | None',
                 s_str) -> str:
        stu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
               if st is not None else None)
        if stu is None:
            rej("valuesel.operand_type")
        # A view-family operand's binding stays the family's PENDING view type
        # until the resolver runs, which has no C++ spelling of its own --
        # compare at the RESOLVED view/owned type, corrected by the
        # runtime-view test (a `str`/`bytes` param's resolved type is the
        # owned string/vector, its signature slot the view). One row for both
        # families: the only family-dependent term is the view spelling.
        s_fam = (s_str if s_str is not None
                 else _resolved_bytes_value(stu, analyzer))
        if s_fam is not None:
            fam = STR_FAMILY if s_str is not None else BYTES_FAMILY
            return lc.render_type(fam.view_type
                                  if _view_at_runtime(side, lc)
                                  else s_fam)
        stu = resolve_int_literals(stu, analyzer.ctx.default_int_for_literal)
        return lc.render_type(stu)

    lhs_cmp = _cmp_cpp(e.left, lt_res, l_str)
    rhs_cmp = _cmp_cpp(e.right, rt_res, r_str)
    lhs_cast = rhs_cast = None
    if (lhs_cmp != rhs_cmp
            and (_resolved_bytes_value(lt_res, analyzer) is not None
                 or _resolved_bytes_value(rt_res, analyzer) is not None)):
        # The mixed-operand wrap is a one-argument conversion, and the bytes
        # family has none in either direction: `std::vector<uint8_t>(span)`
        # does not exist, and a span over the owned side's temporary would
        # dangle. `std::string(sv)` is why the str family can cast.
        rej("valuesel.bytes_mixed")
    if lhs_cmp != rhs_cmp:
        rtu = resolve_int_literals(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
            analyzer.ctx.default_int_for_literal)
        cpp_result = lc.render_type(rtu)
        if cpp_result != lhs_cmp:
            lhs_cast = cpp_result
        if cpp_result != rhs_cmp:
            rhs_cast = cpp_result
    # A view-family select's FORM tag drives the owned-sink copy exactly
    # like a name read: the select's runtime spelling is the COMMON OPERAND
    # spelling (the cast target when the operands differed) -- a view
    # spelling is BORROW (an owned decl / return wraps the family's
    # `view_to_owned_conv`), an owned spelling STORAGE (no re-copy).
    # Scalars are plain VALUE. Reading the tag off the RESULT type instead
    # drops the copy at every owned sink whose operands still spell the
    # view: `bytes`' resolved result is the owned vector while `a or b` over
    # two `bytes` params renders a span, so the return slot would hold a
    # span into nothing. Both families ask it the same way.
    if res_str is not None or res_owned or res_bytes is not None:
        common_cpp = (lhs_cmp if lhs_cmp == rhs_cmp
                      else lc.render_type(resolve_int_literals(
                          unwrap_readonly(unwrap_ref_type(
                              unwrap_send_sync(rtype))),
                          analyzer.ctx.default_int_for_literal)))
        _fam = STR_FAMILY if res_bytes is None else BYTES_FAMILY
        form = (Form.BORROW
                if common_cpp == lc.render_type(_fam.view_type)
                else Form.STORAGE)
        if res_bytes is not None:
            _witness("binop.value_select_bytes")
    else:
        form = Form.VALUE
        if res_span:
            _witness("binop.value_select_span")
    _witness("binop.value_select")
    return THIRValueSelect(
        result_type=rtype, lhs=lowered_lhs, rhs=lowered_rhs, op=e.op,
        truthy_mode=truthy_mode,
        lhs_temp_cpp=lhs_temp_cpp,
        lhs_cast=lhs_cast, rhs_cast=rhs_cast, rhs_sv=rhs_sv,
        form=form, loc=loc)


def _lower_container_select(e: TpyBinOp, rtu: 'TpyType', lc: '_LowerCtx',
                            declared: dict[str, TpyType], loc,
                            rej) -> THIRExpr:
    """Non-value and/or -- the reference slice over a
    container (list/dict/set) or F1-record result. An all-lvalue select is
    the truthy ternary aliasing the chosen operand
    (`((::tpy::__len__(a) != 0) ? a : b)`, a BORROW lvalue); an RVALUE RHS
    takes the hoisted-`__logical_slot` pointer-select (`ptr_select_cpp`),
    materializing lazily so short-circuit holds. Truthiness comes from the
    shared classifier over the LHS operand's type, so the select and a plain
    `if` spell the same test (`.empty()` for bytearray, `__bool__` /
    `__len__` for a dunder-carrying record, the folded `true` otherwise).
    Operands: a bare NAME, a nested select (the chain's
    `auto&& __tmp_N` LHS hoist), a container literal (temp-lifted LHS /
    emplaced RHS, spelled via typed_brace), or any rvalue RHS whose own
    lowering arm admits it. Same-spelling operands only: a differing
    render would take the conversion cast, a COPY that breaks aliasing."""
    analyzer = lc.analyzer

    def _ref_operand_type(side: TpyExpr) -> 'TpyType':
        t = analyzer.get_expr_type(side)
        tu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
              if t is not None else None)
        if isinstance(tu, OwnType):
            tu = unwrap_readonly(tu.wrapped)
        tu = resolve_pending_container(tu, analyzer) or tu
        if tu is None:
            rej("valuesel.ref_operand")
        return tu

    def _lower_operand(side: TpyExpr, side_t: 'TpyType') -> THIRExpr:
        # The operand renders with ITS OWN resolved type, per side:
        # a LIST literal spells its type
        # (`std::vector<T>{1, 2}` -- a bare brace-init cannot deduce);
        # dict/set/Array renders self-describe.
        lowered = _lower_expr(side, lc, declared,
                              use=_ExprUse(slot_target=side_t))
        if isinstance(lowered, THIRContainerLiteral) and is_list(side_t):
            lowered = replace(lowered, typed_brace_cpp=lc.render_type(side_t))
        return lowered

    lt = _ref_operand_type(e.left)
    # The shared truthiness classifier, not a private dunder ladder: keying
    # on the record's dunders alone spelled `bytearray` as the len test where
    # a plain `if` spells `.empty()`.
    truthy_mode = _truthiness_mode(lt, analyzer)
    if truthy_mode not in (TruthinessMode.RECORD_BOOL,
                           TruthinessMode.RECORD_LEN,
                           TruthinessMode.NONEMPTY,
                           TruthinessMode.ALWAYS_TRUE):
        rej("valuesel.ref_lhs")

    lhs_ok = (isinstance(e.left, TpyName)
              or (isinstance(e.left, TpyBinOp) and e.left.op in ("&&", "||"))
              or isinstance(e.left, (TpyArrayLiteral, TpyDictLiteral,
                                     TpySetLiteral)))
    if not lhs_ok:
        rej("valuesel.ref_shape")
    rhs_rvalue = is_rvalue_source(analyzer, e.right)
    if not rhs_rvalue and not isinstance(e.right, TpyName):
        rej("valuesel.ref_shape")
    rt = _ref_operand_type(e.right)
    # The lhs arm binds/aliases as the result type (`&(lhs)` / the bare
    # ternary arm), so its render must match; the rvalue RHS is
    # target-blind (the emplace converts), while an inline lvalue RHS
    # aliases and must match like the LHS.
    if lc.render_type(lt) != lc.render_type(rtu) \
            or (not rhs_rvalue
                and lc.render_type(rt) != lc.render_type(rtu)):
        rej("valuesel.ref_mixed")
    lowered_lhs = _lower_operand(e.left, lt)
    lhs_temp_cpp = None if isinstance(e.left, TpyName) else "auto&&"
    lowered_rhs = _lower_operand(e.right, rt)
    _witness("binop.container_select")
    if truthy_mode is TruthinessMode.RECORD_BOOL:
        _witness("binop.select_bool_dunder")
    return THIRValueSelect(
        result_type=rtu, lhs=lowered_lhs, rhs=lowered_rhs, op=e.op,
        truthy_mode=truthy_mode, lhs_temp_cpp=lhs_temp_cpp,
        ptr_select_cpp=lc.render_type(rtu) if rhs_rvalue else None,
        form=Form.BORROW, loc=loc)


def _fold_literal_comparison(e: TpyBinOp,
                             literal_facts: dict) -> 'bool | None':
    """Mirror of `_try_fold_literal_comparison`: fold ==/!= when a name
    operand carries a LiteralType fact. Single-value: decided either way;
    multi-value: decided only when the compared value is NOT in the set."""
    for var_side, lit_side in ((e.left, e.right), (e.right, e.left)):
        if not isinstance(var_side, TpyName):
            continue
        lit_type = literal_facts.get(var_side.name)
        if not isinstance(lit_type, LiteralType):
            continue
        lit_val = literal_value_from_expr(lit_side)
        if lit_val is None:
            continue
        in_set = lit_val in lit_type.values
        if len(lit_type.values) == 1:
            return in_set if e.op == "==" else not in_set
        if not in_set:
            return False if e.op == "==" else True
    return None


def _fold_literal_chain(e: TpyBinOp, literal_facts: dict) -> 'bool | None':
    """Mirror of `_try_fold_literal_chain`: operand folds decide first,
    then the coverage/contradiction combiner (`check_literal_chain`).
    Membership operands stay None -- their decided fold rejects at the
    membership fence, so folding them here would skip that gate."""
    def operand(x) -> 'bool | None':
        if isinstance(x, TpyBinOp):
            if x.op in ("&&", "||"):
                return _fold_literal_chain(x, literal_facts)
            if x.op in ("==", "!="):
                return _fold_literal_comparison(x, literal_facts)
        if isinstance(x, TpyUnaryOp) and x.op == "!":
            inner = operand(x.operand)
            if inner is not None:
                return not inner
        return None

    left = operand(e.left)
    right = operand(e.right)
    if e.op == "||":
        if left is True or right is True:
            return True
        if left is False and right is False:
            return False
    else:
        if left is False or right is False:
            return False
        if left is True and right is True:
            return True
    if left is None and right is None:
        return emit_prims.check_literal_chain(e, literal_facts)
    return None


def _lower_binop(e: TpyBinOp, rtype: 'TpyType | None', lc: '_LowerCtx',
                 declared: dict[str, TpyType], loc, *,
                 fold_ok: bool = False, slot_threaded: bool = False,
                 temps_ok: bool = False) -> THIRExpr:
    # `temps_ok` rides the enclosing use's allow_temps: operand temps flush
    # at the enclosing statement, so a flushable position's right extends
    # into call-shaped operands. Cond positions thread False (unchanged).
    analyzer = lc.analyzer
    logical_rhs_temps = False
    if e.op in _LOGICAL_OPS:
        # Both operands keep the incoming flush right: an LHS
        # temp hoists at the enclosing statement (it always evaluates) or
        # banks into an ENCLOSING region when this logical nests inside a
        # conditional operand. The RHS additionally rides the cond_eager
        # exit check: an audited temp defers through the emit's own region,
        # an unaudited would-defer one rejects.
        logical_rhs_temps = temps_ok

    def reject() -> None:
        raise ThirUnsupported(
            f"binop.shape.{e.op}"
            f"{_binop_operand_suffix(e, declared, analyzer)}",
            detail=True)

    def _literal_fold_name(s: TpyExpr) -> bool:
        # A name the dead-branch fold may hold a literal fact for: a
        # match-arm fact (`lc.literal_facts`), or ANY binding whose
        # declared/read type is a LiteralType -- `==`/`!=` narrowing seeds
        # facts from those too, so a nested compare under the narrowing
        # folds to a constant.
        if not isinstance(s, TpyName):
            return False
        if s.name in lc.literal_facts:
            return True
        dt = declared.get(s.name)
        if dt is not None and isinstance(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))),
                LiteralType):
            return True
        at = analyzer.get_expr_type(s)
        return at is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))),
            LiteralType)

    if e.op in ("==", "!=") and lc.literal_facts:
        # The expression-level fold renders a DECIDED verdict as the bare
        # "true"/"false"; an undecided compare over the flow-sensitive
        # facts renders plain. `lc.literal_facts` carries every seeding
        # site (branch facts, match arms, overload stubs; the assert/while
        # seeds are fenced at their statements).
        lit_fold = _fold_literal_comparison(e, lc.literal_facts)
        if lit_fold is not None:
            _witness("binop.literal_fact_fold")
            return THIRLiteral(result_type=BOOL, value=lit_fold,
                               form=Form.VALUE, loc=loc)

    if (e.op in _MEMBERSHIP_OPS and _literal_fold_name(e.left)
            and emit_prims.check_literal_in(e, lc.literal_facts) is not None):
        # A DECIDED membership fold has no witnessed render -- it rejects
        # as `match.literal_fold`; the undecided flavor renders plain.
        raise ThirUnsupported("match.literal_fold")

    if e.op in ("&&", "||") and lc.literal_facts:
        # &&/|| chains fold at EXPRESSION position too (operand folds
        # first, then the coverage/contradiction combiner) -- a decided
        # chain renders the bare "true"/"false". Membership operands stay
        # undecided here (their own decided fold rejects above).
        chain_fold = _fold_literal_chain(e, lc.literal_facts)
        if chain_fold is not None:
            _witness("binop.literal_fact_fold")
            return THIRLiteral(result_type=BOOL, value=chain_fold,
                               form=Form.VALUE, loc=loc)

    if (e.op in _MEMBERSHIP_OPS and e.typed_dict_in_field is not None
            and ((isinstance(e.right, TpyName)
                  and e.right.name in declared
                  and e.right.name not in lc.pointers
                  and e.right.name not in lc.narrow.narrowed)
                 # A CALL receiver (`"a" in make()`): the fold keeps the
                 # operand evaluated (`(static_cast<void>(make()), true)`);
                 # the call lowers through its own arms.
                 or isinstance(e.right, (TpyCall, TpyMethodCall)))):
        # TypedDict membership (`"verbose" in kwargs` ->
        # `kwargs.verbose.has_value()`, negated `(!...)`): the compile-time
        # field presence check over the bare receiver. A total=True field is
        # the always-true FOLD: the operand-effect comma form keeps the
        # receiver evaluated (`(static_cast<void>(r), true)` / `false` for
        # `not in`).
        if e.typed_dict_in_always_true:
            td_const = "false" if e.op == "not in" else "true"
            td_tpl = f"(static_cast<void>({{0}}), {td_const})"
            _witness("binop.typed_dict_in_total")
        else:
            td_fld = escape_cpp_name(e.typed_dict_in_field)
            td_hv = f"{{0}}.{td_fld}.has_value()"
            td_tpl = f"(!{td_hv})" if e.op == "not in" else td_hv
            _witness("binop.typed_dict_in")
        return THIRCall(
            result_type=rtype, callee="in",
            args=(_lower_expr(
                e.right, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER)),),
            cpp_template=td_tpl,
            loc=loc)
    # A `__contains__`-unresolved native-set membership renders
    # `::tpy::seq_contains` (set by the membership gate below).
    ranges_contains = False
    # A REFERENCE-typed needle, which is what admits the record leg of that
    # gate -- the render is the same for every needle kind.
    record_needle = False
    # A user iterable with no `__contains__` renders the universal
    # `__iter__`+`__next__` statement-expression loop.
    iter_loop = False
    # A plain user `__contains__` member (no native spelling): the member
    # call renders `fi.name` instead of `fi.native_name`.
    user_contains = False
    rb = e.resolved_binop
    # sema's optional_safe_eq pair (value-repr Optional[scalar] ==/!=): the
    # per-side literal targets, or None outside the slice (set in the
    # compare arm, consumed by the operand render below).
    opt_eq_targets: 'tuple[TpyType | None, TpyType | None] | None' = None
    # Pointer-repr tuple-LITERAL compare pair: set in the compare gate,
    # consumed by the operand render below (the tuple_eq/tuple_lt arm).
    ptr_tuple_pair: 'tuple[TupleType, TupleType] | None' = None
    ptr_tuple_fields = False
    # The mixed-sign fixed-int compare's `::std::cmp_*` spelling, set in
    # the compare gate for the target-less non-literal slice.
    mixed_cmp_tpl: 'str | None' = None
    # The open-`T` equality's `::tpy::eq` spelling, same seam.
    open_eq_tpl: 'str | None' = None
    # This is the CONTAINER-result native dunder leg (list concat / the set
    # operators), whose operands render inline inside the dunder template.
    # Set in the gate, consumed by the operand render below.
    container_native_binop = False
    if e.op in _ARITH_OPS or e.op in _BITWISE_OPS:
        if rb is None or not getattr(rb.method, "cpp_template", None):
            # A structural-protocol operand pair (`result = a + b` in a
            # monomorphized template body): sema resolves no dunder (the
            # constraint is structural) and the render falls through to the
            # raw C++ operator -- `(a + b)`, the resolved-None render.
            # Bare NAME operands only; the `auto` decl slot carries it.
            if (rb is None
                    and isinstance(e.left, TpyName)
                    and isinstance(e.right, TpyName)
                    and _protocol_auto_slot(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(
                            _declared_type(e.left, declared, analyzer)))))
                    and _protocol_auto_slot(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(
                            _declared_type(e.right, declared, analyzer)))))):
                _witness("binop.protocol_raw")
                return THIRBinOp(
                    result_type=(rtype if rtype is not None
                                 else analyzer.get_expr_type(e.left)),
                    left=_lower_expr(e.left, lc, declared),
                    # Floor division maps to C++ `/` at this protocol arm;
                    # a verbatim `//` would be a comment.
                    op="/" if e.op == "//" else e.op,
                    right=_lower_expr(e.right, lc, declared),
                    resolved=None, loc=loc)
            if (rb is None
                    and isinstance(e.left, TpyName)
                    and isinstance(e.right, TpyName)
                    and _resolved_scalar(
                        _declared_type(e.left, declared, analyzer), analyzer)
                    and _resolved_scalar(
                        _declared_type(e.right, declared, analyzer),
                        analyzer)):
                # A resolver-less SCALAR binop (a post-sema function macro
                # synthesizes `a + b` after sema's binop resolution): the
                # record-dunder fallback renders the raw C++ operator
                # (`(a + b)` -- builtin scalars are NominalType records).
                # Bare NAME operands only, the witnessed slice.
                _witness("binop.scalar_raw")
                return THIRBinOp(
                    result_type=(rtype if rtype is not None
                                 else analyzer.get_expr_type(e.left)),
                    left=_lower_expr(e.left, lc, declared),
                    op="/" if e.op == "//" else e.op,
                    right=_lower_expr(e.right, lc, declared),
                    resolved=None, loc=loc)
            if (rb is None or e.op not in ("+", "*", "|", "&", "-", "^")
                    or not rb.method.native_function
                    or not rb.method.native_name):
                reject()
            bt = _resolved_bytes_value(rtype, analyzer)
            lt = _declared_type(e.left, declared, analyzer)
            rt = _declared_type(e.right, declared, analyzer)
            # A bytearray RESULT takes the bytes-family arms below rather
            # than the container ladder: its operator dunders are native free
            # functions over the same view family, and the arms that follow
            # validate the OPERANDS (`_bytes_concat_operand` / the repeat
            # arm's scalar count), so the result type plus a resolved native
            # dunder is the whole rule -- no dunder name is spelled here. The
            # only difference from bytes is the operand slot, which spells the
            # OWNED bytes literal (target-threaded through the overload).
            _ba_res = (rtype is not None
                       and is_bytearray_type(unwrap_readonly(unwrap_ref_type(
                           unwrap_send_sync(rtype))))
                       and bool(_witness("binop.bytearray_result")))
            if not _ba_res and (bt is None or not is_bytes_type(bt)):
                # Container-result native free-function dunders: list
                # concat -> ::tpy::list_concat(l, r), the set operators ->
                # set_union / set_intersection / set_difference /
                # set_symmetric_difference. A NAME reads bare, a list
                # LITERAL takes the typed-brace prefix (applied at the
                # operand render below, the is_list(receiver) arm).
                native_tail = rb.method.native_name.rsplit("::", 1)[-1]
                if not ((e.op == "+" and native_tail == "list_concat")
                        or (e.op in ("|", "&", "-", "^")
                            and native_tail in (
                                "set_union", "set_intersection",
                                "set_difference",
                                "set_symmetric_difference"))):
                    reject()

                def _container_binop_operand(side: TpyExpr,
                                             t: 'TpyType | None') -> bool:
                    if isinstance(side, (TpyArrayLiteral, TpySetLiteral)):
                        return True
                    tu = (unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(t))) if t is not None else None)
                    if not (is_list(tu) or is_set(tu)):
                        return False
                    # A COMPREHENSION operand (`[2] + [el for el in xs if
                    # el]`): the stmt-expr renders inline inside the dunder
                    # template, target-typed by the comprehension's own
                    # container -- the same inline shape the native call
                    # slot takes, and like the call row it needs no temp.
                    if _comp_own_container(side, analyzer) is not None:
                        return True
                    # A container-producing free CALL operand
                    # (`set(range(1, 4)) | {7}`): the fresh value renders
                    # inline inside the dunder template, with no temp and
                    # no receiver form to pick. Borrow-returning
                    # callees (`T&`) are lvalues and stay out.
                    if isinstance(side, TpyCall):
                        return is_rvalue_source(analyzer, side)
                    return isinstance(side, TpyName)

                if not (_container_binop_operand(e.left, lt)
                        and _container_binop_operand(e.right, rt)):
                    reject()
                container_native_binop = True
                _witness("binop.list_concat")
            elif e.op == "+":
                # bytes concat -> ::tpy::bytes_concat(l, r): both operands
                # bytes-family. A NARROWED value-opt binding operand reads
                # `(*b)`, the span the concat template takes bare -- the
                # str arm's narrow legs at the bytes family.
                l_bnarrow = (not _bytes_concat_operand(e.left, lt, analyzer)
                             and _opt_bytes_narrowed_concat_operand(e.left, lc))
                r_bnarrow = (not _bytes_concat_operand(e.right, rt, analyzer)
                             and _opt_bytes_narrowed_concat_operand(e.right, lc))
                if (not (_bytes_concat_operand(e.left, lt, analyzer)
                         or l_bnarrow)
                        or not (_bytes_concat_operand(e.right, rt, analyzer)
                                or r_bnarrow)):
                    reject()
                if l_bnarrow or r_bnarrow:
                    _witness("binop.opt_view_narrowed")
            else:
                # bytes repeat (`b * n` / `n * b`) -> ::tpy::bytes_repeat: one
                # operand a bytes value, the other an int count. The resolved
                # __mul__/__rmul__ pins the bytes into the receiver slot.
                if _bytes_concat_operand(e.left, lt, analyzer):
                    if not _resolved_scalar(rt, analyzer):
                        reject()
                elif not (_bytes_concat_operand(e.right, rt, analyzer)
                          and _resolved_scalar(lt, analyzer)):
                    reject()
        elif e.op == "+" and _is_string_owned(rtype):
            lt = _declared_type(e.left, declared, analyzer)
            rt = _declared_type(e.right, declared, analyzer)
            l_narrow = (not _str_concat_operand(e.left, lt, analyzer)
                        and _opt_view_narrowed_concat_operand(e.left, lc))
            r_narrow = (not _str_concat_operand(e.right, rt, analyzer)
                        and _opt_view_narrowed_concat_operand(e.right, lc))
            # A char-TYPED operand ("hello" + c, chr(8) + "x"): the resolved
            # overload's operand wrapper spells `char_to_str(...)` around the
            # operand's own render (emit applies rb wrappers).
            # char-typed only -- a single-char str literal stays a plain
            # string operand (the str-pair overload resolves first).
            l_char = _eligible_char(lt)
            r_char = _eligible_char(rt)
            if (not (_str_concat_operand(e.left, lt, analyzer) or l_narrow
                     or l_char)
                    or not (_str_concat_operand(e.right, rt, analyzer)
                            or r_narrow or r_char)):
                reject()
            if l_narrow or r_narrow:
                _witness("binop.opt_view_narrowed")
            if l_char or r_char:
                _witness("binop.char_concat")
        elif e.op == "*" and _resolved_str_value(rtype, analyzer) is not None:
            # `s * n` / `n * s` -> str_repeat: one operand is a str value, the
            # other an int count. The resolved __mul__/__rmul__ cpp_template
            # (`::tpy::str_repeat({self}, {0})`) renders through the operator
            # arm; is_reverse pins the str into {self} for the reversed form.
            lt = _declared_type(e.left, declared, analyzer)
            rt = _declared_type(e.right, declared, analyzer)
            # A char-typed str side (`c * 3` -> `str_repeat(char_to_str(c),
            # 3)`) rides the resolved overload's operand wrapper, like the
            # concat arm's char leg.
            if (_str_concat_operand(e.left, lt, analyzer)
                    or (_eligible_char(lt) and _witness("binop.char_concat"))):
                if not _resolved_scalar(rt, analyzer):
                    reject()
            elif not ((_str_concat_operand(e.right, rt, analyzer)
                       or (_eligible_char(rt)
                           and _witness("binop.char_concat")))
                      and _resolved_scalar(lt, analyzer)):
                reject()
        elif (_f1_record(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                  rtype))) if rtype is not None else None, analyzer)
              and rb is not None):
            # A RECORD-result dunder (`a // b` -> Meters via the injected
            # `({self}).__floordiv__({0})` template; `td1 + td2` via the
            # @native operator template): the template render is shared with
            # the scalar path (is_reverse swap included). Admitted only
            # where the consumer pinned the record rvalue (print arg /
            # field receiver).
            _witness("binop.record_dunder")
        elif not _resolved_scalar(rtype, analyzer):
            reject()
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
              and not isinstance(e.left, TpyName)
              and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
              and not isinstance(e.right, TpyName)):
            # A both-literal int binop constant-folds at a TARGET-LESS
            # position: a BigInt context renders
            # `::tpy::BigInt(static_cast<int64_t>(...LL))` / `from_str`, a
            # default-int one the plain decimal (`print(1 + (2 + 3))` ->
            # `6`) -- both via the shared render_int_literal_value. Only
            # flagged positions fold; a slot retarget downstream overwrites
            # int_cpp wholesale, so the pre-render cannot double-wrap.
            # Slot-THREADED fixed-int sinks
            # never fold (the retype rebuilds `::tpy::add_check<intN>(...)`
            # instead), so the operator route below stays their path.
            lit_val = getattr(analyzer.get_expr_type(e), "value", None)
            # The fold is SYNTACTIC (the involves_variables walk): sema
            # computes a constant value for a subscript over a
            # literal-seeded container too (`xs[0] + xs[1]`), but those
            # render as runtime reads -- only variable-free literal trees
            # fold.
            if lit_val is not None and TypeResolver.involves_variables(e):
                lit_val = None
            fits_i64 = lit_val is not None and -(2**63) <= lit_val <= 2**63 - 1
            resolved = resolve_int_literals(
                rtype, analyzer.ctx.default_int_for_literal)
            # The fold covers every magnitude in a flagged position:
            # render_int_literal_value spells BigInt fits-i64 values
            # `BigInt(static_cast<int64_t>(..LL))` and beyond-int64 values
            # `BigInt::from_str("...")`.
            if fold_ok and lit_val is not None:
                _witness("binop.literal_fold")
                return THIRLiteral(
                    result_type=resolved, value=lit_val,
                    int_cpp=render_int_literal_value(
                        lit_val, resolved,
                        default_int_type=analyzer.ctx.default_int_type,
                        type_to_cpp=lambda t: t.to_cpp()),
                    loc=loc)
            # An un-flagged TARGET-LESS position rejects the fits-i64
            # slice: the fold belongs there, and only `fold_ok` marks the
            # positions whose fold is spelled above. A SLOT-THREADED
            # position renders the full operator instead (`b: int = 1 << 40`
            # -> `((BigInt(1)) << (BigInt(40)))`), which is why both the
            # fits-i64 and the beyond-int64 slice pass through it. The
            # FIXED-resolvable slice needs no guard: its sinks are either
            # slot-threaded (the retype rebuild) or flagged and folded
            # above.
            if (is_big_int_type(resolved) and (lit_val is None or fits_i64)
                    and not slot_threaded):
                reject()
        if e.op in _BITWISE_OPS:
            _witness("binop.bitwise")
    elif e.op in _COMPARE_OPS:
        if rtype is None or not is_bool_type(rtype):
            reject()
        ptr_tuple_pair = _ptr_tuple_literal_compare_pair(e, analyzer)
        ptr_tuple_fields = False
        if ptr_tuple_pair is None:
            # The FIELD-pair flavor (`this->pair == other.pair`, the
            # dataclass __eq__ chain): the same tuple_eq/tuple_lt
            # composition over the BARE member reads.
            ptr_tuple_pair = _ptr_tuple_field_compare_pair(
                e, declared, analyzer)
            ptr_tuple_fields = ptr_tuple_pair is not None
        if (ptr_tuple_pair is None
                and rb is not None
                and not getattr(rb.method, "cpp_template", None)
                and not (rb.method.native_function
                         and rb.method.native_name)):
            reject()
        _lt_raw = _declared_type(e.left, declared, analyzer)
        _rt_raw = _declared_type(e.right, declared, analyzer)
        lt = _narrowed_opt_operand(e.left, _lt_raw, lc, analyzer)
        rt = _narrowed_opt_operand(e.right, _rt_raw, lc, analyzer)
        # A condition-scope-narrowed union NAME operand gates and renders
        # as its concrete member (the inline get).
        l_nu = _narrowed_union_compare_operand(e.left, declared, lc)
        r_nu = _narrowed_union_compare_operand(e.right, declared, lc)
        if l_nu is not None:
            lt = l_nu[1]
        if r_nu is not None:
            rt = r_nu[1]
        opt_eq_targets = _opt_scalar_eq_pair(e, lt, rt, analyzer)
        if (opt_eq_targets is None
                and _bare_field_eq_pair(e, lt, rt, analyzer)):
            # Both members read bare into the compare parens -- the same
            # target-less render as the optional_safe_eq pair.
            opt_eq_targets = (None, None)
        if (opt_eq_targets is None and e.op in ("==", "!=")
                and isinstance(e.left, (TpyCall, TpyMethodCall))
                and isinstance(e.right, (TpyCall, TpyMethodCall))
                and _value_opt_value_record(lt, analyzer) is not None
                and _value_opt_value_record(rt, analyzer) is not None):
            # Both sides value-repr Optional[ValueType record] call rvalues
            # (`fixed.utcoffset() == fixed1.utcoffset()` on
            # `std::optional<timedelta>`): each renders bare into the
            # resolved template -- std::optional's mixed operator compares
            # payloads.
            opt_eq_targets = (None, None)
        _l_unproven = _r_unproven = None
        if e.op not in ("==", "!="):
            # An UNPROVEN value-opt scalar ORDERING operand unwraps through
            # `deref_optional_check(x)` (its inner scalar); `==`/`!=` stays on
            # the bare optional (`optional_safe_eq`), so it is left untouched.
            _l_unproven = _unproven_opt_scalar_inner(e.left, lc, analyzer)
            _r_unproven = _unproven_opt_scalar_inner(e.right, lc, analyzer)
            lt = _l_unproven or lt
            rt = _r_unproven or rt
        if not ((_resolved_scalar(lt, analyzer)
                 and _resolved_scalar(rt, analyzer))
                or (_str_compare_operand(e.left, lt, analyzer)
                    and _str_compare_operand(e.right, rt, analyzer))
                or (_bytes_compare_operand(e.left, lt, analyzer)
                    and _bytes_compare_operand(e.right, rt, analyzer))
                or (_char_compare_operand(e.left, lt, analyzer)
                    and _char_compare_operand(e.right, rt, analyzer))
                # An `Own[T]` operand is the same `T` VALUE at a compare:
                # the wrapper marks a transfer the consuming SINK performs,
                # and reading one to compare it transfers nothing.
                or (_tparam_value(_unwrap_own(lt))
                    and _tparam_value(_unwrap_own(rt)))
                or _union_compare_pair(lt, rt)
                or _ptr_union_compare_pair(e, lt, rt, lc, declared,
                                           analyzer)
                or (_container_compare_pair(e.op, lt, rt, analyzer)
                    and _witness("binop.container_eq"))
                or (e.op in ("==", "!=") and _any_compare_pair(lt, rt))
                or _record_compare_pair(lt, rt)
                # A record compared against a NON-record operand its own
                # dunder declares (`t == s` off `__eq__(self, other: str)`):
                # sema's resolution proves the pair, or -- for the DERIVED
                # `!=`, which sema resolves no binop for -- the dunder's own
                # param slot does.
                or ((_record_compare_pair(lt, rt, rb)
                     or _record_dunder_operand_pair(e.op, e, lt, rt,
                                                    analyzer))
                    and _witness("binop.record_dunder_operand"))
                or _enum_compare_pair(e, lt, rt, analyzer)
                or _tuple_compare_pair(lt, rt, analyzer)
                or ptr_tuple_pair is not None
                or opt_eq_targets is not None):
            reject()
        if (e.op in ("==", "!=") and _tparam_value(_unwrap_own(lt))
                and _tparam_value(_unwrap_own(rt))):
            # Both operands are the SAME open `T`, whose two C++ spellings can
            # differ at an instantiation: a `T` parameter arrives in its own
            # type's parameter form (a view for `str` / `bytes`) while a `T`
            # element or field is the storage form. `::tpy::eq` is `==` where
            # the two forms compare and element-wise where they do not, so
            # the one emitted body serves every instantiation. Every builtin
            # family compares across its forms now (the bytes view is its
            # own type with the family's `==` / `<=>`), so ordering renders
            # the bare operator.
            _witness("binop.open_tparam_eq")
            open_eq_tpl = ("::tpy::eq({self}, {0})" if e.op == "=="
                           else "(!::tpy::eq({self}, {0}))")
        if _mixed_sign_compare(lt, rt):
            # The `::std::cmp_*` slice: TARGET-LESS, non-literal fixed-int
            # operands. A coercion-targeted pair casts instead, a literal
            # side folds cleanly, an unproven-opt OR PROVEN-NARROWED
            # optional side reads its INNER (the bare `(*a) < b` render,
            # whose target suppresses cmp_*), and a condition-narrowed
            # union side keeps its union type -- all of those reject.
            if (opt_eq_targets is not None
                    or _l_unproven is not None or _r_unproven is not None
                    or lt is not _lt_raw or rt is not _rt_raw
                    or l_nu is not None or r_nu is not None
                    or isinstance(analyzer.get_expr_type(e.left),
                                  IntLiteralType)
                    or isinstance(analyzer.get_expr_type(e.right),
                                  IntLiteralType)):
                reject()
            _witness("binop.mixed_sign_cmp")
            mixed_cmp_tpl = f"{emit_prims.CMP_HELPER[e.op]}({{self}}, {{0}})"
    elif e.op in _LOGICAL_OPS:
        if rtype is None or not is_bool_type(rtype):
            # Value-position and/or: Python operand semantics via the
            # once-evaluated-LHS ternary -- the split is on the RESULT
            # type alone. logical_rhs_temps
            # carries the incoming flush right (temps_ok itself was left
            # unchanged for the operand path above).
            return _lower_value_select(e, rtype, lc, declared, loc,
                                       temps_ok=logical_rhs_temps)
        lt = _declared_type(e.left, declared, analyzer)
        rt = _declared_type(e.right, declared, analyzer)
        if (lt is None or not is_bool_type(lt)
                or rt is None or not is_bool_type(rt)):
            # A bool RESULT over non-bool operands needs the direct C++
            # `&&`/`||` with truthiness reasoning this lowering lacks.
            reject()
        if e.op == "&&":
            _pinf = _poly_narrow_info(e.left, declared, analyzer)
            if (_pinf is not None
                    and _pinf[0] not in lc.narrow.narrowed
                    and _pinf[0] not in lc.narrow.spelled
                    and _pinf[0] not in lc.pointers
                    and _pinf[0] != lc.self_receiver):
                # An inline poly-isinstance LEFT under `&&`: the check
                # lowers via the shared cast chokepoint and the subject's
                # RHS reads SPELL the validated cast inline
                # (`(*static_cast<const Dog*>(&p))` -- static_cast is
                # well-defined after the LHS dynamic_cast validated).
                # INHERIT conformers only:
                # the structural flavor re-casts through the adapter (a
                # different spell) and keeps the fence.
                pvar, pmember, _pdecl = _pinf
                _c_p, _cast_arg, _inner_src = _poly_cast_context(
                    pvar, lc, declared)
                if (isinstance(pmember, NominalType)
                        and _inner_src is not None
                        and not is_protocol_type(_inner_src)):
                    _witness("binop.poly_inline_narrow")
                    left = THIRDynIsinstanceMulti(
                        result_type=analyzer.get_expr_type(e.left),
                        checks_cpp=_poly_cast_checks(pvar, (pmember,), lc,
                                                     declared),
                        loc=getattr(e.left, "loc", None))
                    _cpx = "const " if _c_p else ""
                    _spell = (f"(*static_cast<{_cpx}"
                              f"{lc.render_type(pmember)}*>({_cast_arg}))")
                    # The guard above proved pvar is un-spelled, so the
                    # pop is unconditional.
                    lc.narrow.spelled[pvar] = _spell
                    _act = dict(declared)
                    _act[pvar] = pmember
                    try:
                        right = _lower_expr(e.right, lc, _act,
                                            use=_ExprUse(
                                                allow_temps=False))
                    finally:
                        lc.narrow.spelled.pop(pvar, None)
                    return THIRBinOp(
                        result_type=rtype, left=left, op=e.op,
                        right=right, resolved=None, loc=loc)
    elif e.op in _IS_OPS:
        _isn_operand = (e.right if isinstance(e.left, TpyNoneLiteral)
                        else e.left)
        if (_is_none_compare_operand(e, declared, analyzer) is None
                and _any_none_subject(e, declared, analyzer) is None
                and not _opt_record_none_subject(e, lc)
                # A ptr-Optional element off a MIXED own-borrow tuple
                # LOCAL: the bare pointer compare (std::get already
                # yields `Box*` -- no lift). One-sided None tests only.
                and not (
                    (isinstance(e.left, TpyNoneLiteral)
                     != isinstance(e.right, TpyNoneLiteral))
                    and _tuple_local_ptr_elem_subscript(
                        _isn_operand, declared, lc.own_borrow_tuple_locals,
                        analyzer))):
            reject()
    elif e.op in _MEMBERSHIP_OPS and isinstance(e.right, TpyTupleLiteral):
        # `x in (a, b, ...)` / `not in`: a tuple-literal membership expands to an
        # OR-chain of `==` compares (no `__contains__`). Value-comparable
        # (scalar / str / bytes) needle + elements only, so each `==` renders
        # plainly.
        lt = _declared_type(e.left, declared, analyzer)
        left_scalar = lt is not None and _resolved_scalar(lt, analyzer)
        left_str = (lt is not None
                    and _resolved_str_value(lt, analyzer) is not None)
        left_bytes = (lt is not None
                      and _resolved_bytes_value(lt, analyzer) is not None)
        if not (left_scalar or left_str or left_bytes) or not e.right.elements:
            reject()
        for el in e.right.elements:
            et = analyzer.get_expr_type(el)
            if et is None or (
                    left_scalar and not _resolved_scalar(et, analyzer)) or (
                    left_str and _resolved_str_value(et, analyzer) is None) or (
                    left_bytes and not _bytes_compare_operand(el, et, analyzer)):
                reject()
    elif e.op in _MEMBERSHIP_OPS and _bytes_membership_ok(e, declared, analyzer):
        # `needle in b` over a bytes / BytesView container: the native
        # free-function `bytes_contains` (single-byte needle) /
        # `bytes_contains_sub` (bytes-substring needle). Validated by the
        # helper; emitted below through the free-function THIRMembership arm.
        pass
    elif e.op in _MEMBERSHIP_OPS and _str_membership_ok(e, declared, analyzer):
        # `needle in s` over a str-family value: the `.find()` arm (str has no
        # `__contains__`). Validated by the helper; emitted below.
        pass
    elif e.op in _MEMBERSHIP_OPS:
        fi = e.resolved_contains
        lt = _declared_type(e.left, declared, analyzer)
        if fi is None:
            # No resolved `__contains__` member (`readonly[set]` strips it;
            # list/array/span have none): the `is_native_in` arm renders
            # `::tpy::seq_contains(recv, x)`. Any native NativeIterable
            # receiver (set/list/array/span) with a scalar / owned-str needle;
            # the universal iterator-loop form is a later rung.
            rt = _declared_type(e.right, declared, analyzer)
            rt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
                       if rt is not None else None)
            rec = (analyzer.registry.get_record_for_type(rt_bare)
                   if rt_bare is not None else None)
            lt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
                       if lt is not None else None)
            # str/bytes iterate as char/byte sequences so they qualify as
            # native-iterable, but membership on them is SUBSTRING (`.find() !=
            # npos`), not element-containment (`seq_contains`) -- that
            # is the str/bytes-membership arms' job, so exclude them here.
            native_recv = (rec is not None and rec.is_native
                           and is_native_iterable(rt_bare, analyzer.registry)
                           and _resolved_str_value(rt, analyzer) is None
                           and _resolved_bytes_value(rt, analyzer) is None)
            # A record needle is a reference type, so Python's `x is e` leg
            # of containment is observable on it -- which is what admits the
            # form here; the render is the same helper every needle takes.
            record_needle = native_recv and _f1_record(lt_bare, analyzer)
            if (native_recv
                    and (_resolved_scalar(lt, analyzer)
                         or _resolved_viewfam_value(lt, analyzer) is not None
                         # An open-T needle inside a generic body (`key in
                         # self._data` on `dict[T, int]`): the needle
                         # renders by name per instantiation and the helper
                         # picks the identity leg per instantiation too.
                         or (_is_type_param_slot(lt)
                             and _witness("binop.contains_tparam_needle"))
                         # A pointer-repr tuple-LITERAL needle: the
                         # tuple_to_storage lift over the borrow-form
                         # literal render (the compare pair's sibling).
                         or (isinstance(e.left, TpyTupleLiteral)
                             and isinstance(lt_bare, TupleType)
                             and lt_bare.has_pointer_repr_element())
                         or (_value_tuple_needle_ok(lt_bare, analyzer)
                             and _witness("binop.contains_tuple_needle"))
                         # A RECORD needle (`p in item.pts`): sema already
                         # required the element to be Equatable, so the
                         # record's `friend operator==` exists, and the
                         # helper's identity leg supplies the `x is e` half
                         # of Python's containment for it.
                         or (record_needle
                             and _witness("binop.contains_record_needle")))):
                ranges_contains = True
            elif ((rec is not None and not rec.is_native
                   # A structural protocol param (`target in items` on
                   # `Iterable[T]`): the is_native_in test admits only
                   # `tpy.NativeIterable` to the `seq_contains` render, so
                   # every other protocol receiver takes the universal loop
                   # over the bare monomorphized param. Dynamic protocols
                   # read through adapters, unwitnessed.
                   or (rec is None and isinstance(rt_bare, NominalType)
                       and rt_bare.is_protocol
                       and not rt_bare.is_dynamic_protocol
                       and rt_bare.qualified_name() != "tpy.NativeIterable"))
                    and isinstance(e.right, TpyName)
                    and e.right.name in declared
                    and e.right.name not in lc.pointers
                    and e.right.name not in lc.prescan.global_slots
                    and _resolved_str_value(rt, analyzer) is None
                    and _resolved_bytes_value(rt, analyzer) is None
                    and _resolved_scalar(lt, analyzer)):
                # A USER iterable with no `__contains__` (`20 in b` on a
                # __iter__/__next__ record): the universal
                # statement-expression loop over the bare receiver name.
                # An indirect receiver (pointer local / global slot) needs
                # the `(*...)` deref flavor, unwitnessed.
                iter_loop = True
            else:
                reject()
        elif (e.typed_dict_in_field is not None
                or fi.cpp_template or fi.native_function):
            reject()
        elif not fi.native_name:
            # A plain USER `__contains__` (record / generic record): the
            # member-call tail, `(recv.__contains__(needle))`. A declared
            # record name or an admitted field-read receiver; a scalar /
            # str-value needle renders bare (a user record is never
            # view-keyed, so no literal target is threaded).
            recv_ok = ((isinstance(e.right, TpyName)
                        and e.right.name in declared)
                       or (isinstance(e.right, TpyFieldAccess)
                           and (_field_receiver_ok(e.right, declared,
                                                   analyzer)
                                # A field haystack off an F1-record-returning
                                # CALL receiver (`"a" in one(...).cookies`):
                                # the member call chains off the bare postfix
                                # field read, whose inner call gates apply at
                                # receiver lowering.
                                or _field_over_call_ok(e.right, analyzer)
                                # A module-var receiver's `__contains__`
                                # (`"K" in os.environ` -> the deref read
                                # composing the member call).
                                or ((_in_mod := _bare_module_recv(
                                        e.right.obj, declared, analyzer))
                                    is not None
                                    and _module_var_read_cpp(
                                        _in_mod, e.right.field, analyzer)
                                    is not None))))
            if not (recv_ok
                    and (_resolved_scalar(lt, analyzer)
                         or _resolved_viewfam_value(lt, analyzer) is not None)):
                reject()
            user_contains = True
        elif (isinstance(e.right, (TpyName, TpySetLiteral))
              # A container FIELD haystack (`Tag("a", 1) in si.tags` on a
              # `set[Tag]` field): the bare member read composes into the
              # same `.contains` member as a name receiver.
              or (isinstance(e.right, TpyFieldAccess)
                  and _container_field_bare_read(e.right, declared,
                                                 analyzer)
                  and _witness("binop.contains_field_recv"))):
            if isinstance(e.right, TpyName):
                if e.right.name not in declared:
                    reject()
                ct = unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(declared[e.right.name])))
                if isinstance(ct, OwnType):
                    # An `Own[container]` binding (the movable loop var)
                    # spells the payload in C++, so the `.contains` member
                    # composes on the bare name exactly as for a plain
                    # container name -- the receiver peel the family gate
                    # and the record arm already do.
                    ct = unwrap_readonly(ct.wrapped)
            else:
                # A SET-LITERAL rvalue receiver (`d in {date(..), date(..)}`)
                # renders its spelled ctor and takes the same `.contains`
                # member (the literal's own lowering gates the elements); a
                # FIELD haystack's expression type is the same container.
                ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(e.right))))
                if ct is not None:
                    ct = resolve_pending_container(ct, analyzer) or ct
            # A str or bytes needle renders bare into `contains(...)`
            # (literal / view name / owned local -- the container's transparent
            # lookup absorbs the form), exactly like a scalar needle. An
            # F1-RECORD needle name reads bare the same way (the record-keyed
            # set/dict slice). A VIEW-keyed container (`set[StrView]`) threads
            # view_key_target into the needle's literal render (the
            # static-storage pin) -- not lowered yet, so it rejects.
            lt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
                       if lt is not None else None)
            if not ((is_dict(ct) or is_set(ct))
                    and (_resolved_scalar(lt, analyzer)
                         or (isinstance(e.left, TpyName)
                             and _f1_record(lt_bare, analyzer))
                         # A record ctor RVALUE needle (`time(0) in {time(0),
                         # time(1)}`) renders bare inside `contains(...)` just
                         # like the name row -- it is a value position, so the
                         # ctor's own emit is the whole render.
                         or (_f1_record(lt_bare, analyzer)
                             and _record_rvalue_source_shape(e.left, analyzer))
                         or (_resolved_str_value(lt, analyzer) is not None
                             and ((_vkc := view_key_target(ct)) is None
                                  # A STR-VIEW key target: a str needle
                                  # (literal / view name) renders bare
                                  # either way -- the pin is a no-op for
                                  # the str family.
                                  or (is_str_view_type(_vkc)
                                      and bool(_witness(
                                          "binop.contains_view_key")))))
                         or (_resolved_bytes_value(lt, analyzer) is not None
                             and view_key_target(ct) is None)
                         # A BYTES literal needle at a BYTES-VIEW key
                         # target: the static view spelling
                         # (`contains(::tpy::bytes_literal("hello", 5))`)
                         # -- the needle lowering retags it below.
                         or (isinstance(_peel_coerce(e.left), TpyBytesLiteral)
                             and (_vkb := view_key_target(ct)) is not None
                             and is_bytes_view_type(_vkb)
                             and bool(_witness(
                                 "binop.contains_view_key")))
                         or (_value_tuple_needle_ok(lt_bare, analyzer)
                             and _witness("binop.contains_tuple_needle")))):
                reject()
        elif (isinstance(e.right, TpyMethodCall)
              and _dict_view_iterable_ok(
                  e.right, declared, analyzer, methods=("values", "keys"))):
            # `x in d.values()` / `x in d.keys()`: the view rvalue renders
            # `::tpy::dict_values(d).contains(x)`; the needle is the dict's
            # value/key (scalar or bare-str / bytes). `.items()` needs a
            # tuple needle, a later cell.
            if not (_resolved_scalar(lt, analyzer)
                    or _resolved_viewfam_value(lt, analyzer) is not None):
                reject()
        else:
            reject()
    else:
        reject()

    if e.op in _MEMBERSHIP_OPS and isinstance(e.right, TpyTupleLiteral):
        # LATENT GAP: this emits the `==` OR-chain unconditionally, with no
        # literal-fact fold -- `x in (a,b,c)` collapses to a compile-time
        # `true`/`false` when `x` carries literal-overload narrowing facts.
        # Unreachable today: the only source, match-over-`Literal[...]`
        # subjects, has its decided folds fenced at the compare/membership
        # gate above (`match.literal_fold`). A body that ever reaches here
        # with such facts must gate them (reject or fold).
        _witness("binop.tuple_membership")
        elems = e.right.elements
        # A non-trivial needle binds to a `__in_lhs` temp in a statement
        # expression.
        need_temp = (len(elems) > 1
                     and not emit_prims.is_trivial_needle(e.left))
        # A bytes-family chain compares through `::tpy::BytesView`, so its
        # literal elements take the static span (the compare arm's rule).
        retag = (_retag_bytes_literal_span
                 if _resolved_bytes_value(
                     _declared_type(e.left, declared, analyzer), analyzer)
                 is not None else (lambda v: v))
        return THIRTupleMembership(
            result_type=rtype,
            left=retag(_lower_expr(e.left, lc, declared)),
            elements=tuple(retag(_lower_expr(el, lc, declared))
                           for el in elems),
            negate=e.op == "not in",
            need_temp=need_temp,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and _bytes_membership_ok(e, declared, analyzer):
        # Bytes/BytesView membership -> the native free function
        # `::tpy::bytes_contains[_sub](recv, needle)`. The receiver reads bare;
        # a bytes-literal needle renders OWNED (the target-less default),
        # matching `bytes_contains_sub`'s owned `bytes` parameter.
        _witness("binop.bytes_membership")
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared),
            needle=_lower_expr(e.left, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.STORAGE)),
            method_cpp=e.resolved_contains.native_name,
            negate=e.op == "not in",
            free_function=True,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and _str_membership_ok(e, declared, analyzer):
        # Str-family membership -> the `.find()` arm. A str-literal receiver
        # wraps in string_view (C string literals lack `.find`); a str-field
        # needle renders its bare owned member read, like the compare operands.
        # The HAYSTACK field read is the same bare member read -- `.find` is
        # taken off it directly, owned `String` storage included -- so the
        # receiver threads the flag exactly as the needle does.
        _witness("binop.str_membership")
        return THIRStrMembership(
            result_type=rtype,
            receiver=_lower_expr(
                e.right, lc, declared,
                field_owned_str_ok=isinstance(e.right, TpyFieldAccess)),
            needle=_lower_expr(
                e.left, lc, declared,
                field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
            negate=e.op == "not in",
            wrap_receiver_sv=isinstance(e.right, TpyStrLiteral),
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and ranges_contains:
        _witness("binop.set_ranges_membership")
        # A container FIELD haystack (`item in self.xs`) reads bare into
        # `::tpy::seq_contains(this->xs, item)` -- prechecked here rather
        # than admitted at RECEIVER in the ladder, which would also re-route
        # method receivers this arm says nothing about.
        recv_prechecked = (
            _container_field_bare_read(e.right, declared, analyzer)
            and _witness("binop.membership_container_field"))
        needle_t = analyzer.get_expr_type(e.left)
        needle_tu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            needle_t))) if needle_t is not None else None)
        if (isinstance(e.left, TpyTupleLiteral)
                and isinstance(needle_tu, TupleType)
                and needle_tu.has_pointer_repr_element()):
            # The borrow-form literal lifts to the stored shape
            # (`tuple_to_storage<std::tuple<int32_t, Box>>(std::tuple<
            # int32_t, Box*>{1, &(b)})`) so the element comparison is
            # value-level -- the needle lift.
            storage_t = _resolve_pending_tuple_elems(needle_tu, analyzer)
            _witness("binop.tuple_ptr_needle")
            needle: THIRExpr = THIRFormConvert(
                result_type=storage_t,
                value=_lower_borrow_tuple_literal(e.left, storage_t, lc,
                                                  declared),
                form=Form.STORAGE, loc=loc)
        else:
            # A bytes literal needle takes the static span: the element
            # compare reads through `::tpy::BytesView` (a str literal is
            # bare for the same reason).
            needle = _retag_bytes_literal_span(
                _lower_expr(e.left, lc, declared))
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared,
                                 field_prechecked=recv_prechecked),
            needle=needle,
            method_cpp="",
            negate=e.op == "not in",
            ranges_contains=True,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and iter_loop:
        # The universal loop over the bare receiver name; the scalar needle
        # renders bare (view_key_target is None for a user record haystack).
        _witness("binop.iter_membership")
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared),
            needle=_lower_expr(e.left, lc, declared),
            method_cpp="",
            negate=e.op == "not in",
            iter_loop=True,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS:
        _witness("binop.user_membership" if user_contains
                 else "binop.membership")
        # A dict-view receiver (`d.values()`) renders the bare native view call
        # via the for-loop's ITERABLE override; a name/container receiver reads
        # bare; a field-read receiver (`"sid" in s.cookies`) takes the bare
        # member read (receiver position).
        if isinstance(e.right, TpyMethodCall):
            recv_use = _ExprUse(result=_ExprResultUse.ITERABLE)
        elif isinstance(e.right, TpyFieldAccess):
            recv_use = _ExprUse(result=_ExprResultUse.RECEIVER)
        else:
            recv_use = _ExprUse()
        # A native-contains CONTAINER-field haystack was admitted by the
        # bare-member-read verdict above; precheck it past the field arm's
        # result gate (the ranges arm does the same) so it renders as the
        # bare member read. User-__contains__ receivers keep the gate.
        recv_prechecked = (not user_contains
                           and isinstance(e.right, TpyFieldAccess)
                           and _container_field_bare_read(e.right, declared,
                                                          analyzer))
        # A str-family FIELD needle renders the bare member read into the
        # contains(...) template (same owned-str-field-ok
        # position as the compare operands above).
        needle = _lower_expr(
            e.left, lc, declared,
            field_owned_str_ok=isinstance(e.left, TpyFieldAccess))
        # A bytes literal needle takes the static view spelling
        # (`::tpy::bytes_literal("hello", 5)`): the container's transparent
        # lookup and a user `__contains__`'s `bytes` slot both read it
        # through `::tpy::BytesView`, so the owned render would build a
        # buffer per lookup that nothing keeps. A user slot that OWNS the
        # argument (`Own[bytes]`, `::tpy::Bytes` by value) keeps the owned
        # render: the view does not convert to the owner. An open `T` slot
        # is the instantiation's parameter form, the view at `bytes`.
        if not (user_contains and e.resolved_contains.params
                and not (_bytes_literal_view_slot(unwrap_readonly(
                             e.resolved_contains.params[0].type))
                         or _is_type_param_slot(
                             e.resolved_contains.params[0].type))):
            needle = _retag_bytes_literal_span(needle)
        if user_contains and e.resolved_contains.params:
            # A runtime-BigInt needle against the user __contains__'s
            # declared fixed-int param takes the checked call-arg narrow
            # (an int literal stays bare -- the literal exemption).
            pt = unwrap_readonly(e.resolved_contains.params[0].type)
            needle_key = _narrow_key_type(e.left, declared, analyzer)
            if needle_key is _NARROW_UNMIRRORED:
                reject()
            if (is_fixed_int_type(pt)
                    and not isinstance(e.left, TpyIntLiteral)
                    and _runtime_bigint(needle_key, analyzer)):
                needle = THIRCoerce(
                    result_type=pt, expr=needle,
                    coercion_name=_BIGINT_NARROW,
                    wrap=f"({{0}}).to_fixed_check<{pt.to_cpp()}>()", loc=loc)
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared, use=recv_use,
                                 field_prechecked=recv_prechecked),
            needle=needle,
            method_cpp=(e.resolved_contains.name if user_contains
                        else e.resolved_contains.native_name),
            negate=e.op == "not in",
            loc=loc)
    if e.op in _IS_OPS:
        any_subj = _any_none_subject(e, declared, analyzer)
        if any_subj is not None:
            _witness("isnone.any_typeid")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(any_subj, lc, declared),
                negate=e.op == "is not",
                any_typeid=True,
                form=Form.VALUE,
                loc=loc)
        operand = e.right if isinstance(e.left, TpyNoneLiteral) else e.left
        ut_none = _union_none_name(operand, declared, analyzer)
        if ut_none is not None:
            # `v is None` on a union binding: the monostate holds test over
            # the bare variant read (identical for value/pointer reprs; the
            # arm is order-blind, so `None is v` lands here too). A
            # NARROWED subject's read is its extraction alias, not the
            # variant binding this test needs, so it rejects.
            assert isinstance(operand, TpyName)
            if (operand.name in lc.narrow.narrowed
                    or operand.name in lc.inline_narrowed
                    or operand.name in lc.narrow.spelled):
                raise ThirUnsupported("binop.union_none_narrowed_subject")
            # A protocol-member union is no variant: it needs the protocol
            # ptr-compare (both polarities, every position), not the
            # monostate holds test, so it rejects.
            if any(is_protocol_type(m) for m in ut_none.members):
                raise ThirUnsupported("binop.union_none_protocol_subject")
            # A WRAPPER union reads the variant through `.value`
            # (VariantAccess.variant_expr) -- same holds test, respelled.
            _witness("isnone.union_wrapper_monostate"
                     if ut_none.needs_wrapper() else "isnone.union_monostate")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(operand, lc, declared,
                                    allow_union_divergent=True),
                negate=e.op == "is not",
                union_monostate=True,
                union_wrapper=ut_none.needs_wrapper(),
                form=Form.VALUE,
                loc=loc)
        if _union_none_field(operand, declared, analyzer) is not None:
            # A union-typed FIELD subject: the same monostate holds test
            # over the bare member read (`holds_alternative<monostate>(
            # h.un)`), the name flavor's field twin.
            _witness("isnone.union_monostate")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(operand, lc, declared,
                                    field_prechecked=True,
                                    allow_union_divergent=True),
                negate=e.op == "is not",
                union_monostate=True,
                form=Form.VALUE,
                loc=loc)
        # A value-repr Optional binding (param OR declared local -- e.g. a
        # try-hoisted `std::optional<T> r;` slot) None-tests via has_value;
        # pointer-repr bindings via `!= nullptr`. An Optional FIELD subject
        # (storage std::optional<T> whatever the repr) also takes has_value
        # over the bare member read -- the gate above already pinned the
        # receiver/marker shape, so the field lowers prechecked.
        if _tuple_local_ptr_elem_subscript(operand, declared,
                                           lc.own_borrow_tuple_locals,
                                           analyzer):
            # A ptr-Optional element subject off a MIXED own-borrow tuple
            # LOCAL: std::get already yields the bare `Box*`, so the
            # compare is the plain `std::get<1>(p) != nullptr` -- NO
            # optional_to_ptr lift (the FIELD flavor's arm below keeps
            # its FormConvert).
            _witness("isnone.tuple_elem_bare")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(operand, lc, declared,
                                    subscript_prechecked=True),
                negate=e.op == "is not",
                form=Form.VALUE,
                loc=loc)
        if _tuple_field_opt_elem_subscript(operand, declared, analyzer):
            # A ptr-Optional tuple-field element subject: the pre-lifted
            # pointer compare (`optional_to_ptr(std::get<0>(h.t)) ==
            # nullptr`) -- the FormConvert BORROW carries the lift.
            ot = analyzer.get_expr_type(operand)
            _witness("isnone.tuple_elem_lift")
            return THIRIsNone(
                result_type=rtype,
                operand=THIRFormConvert(
                    result_type=unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(ot))),
                    value=_lower_expr(operand, lc, declared,
                                      subscript_prechecked=True),
                    form=Form.BORROW, loc=loc),
                negate=e.op == "is not",
                form=Form.VALUE,
                loc=loc)
        if generic_opt_trait_name(operand, declared):
            # A generic `T | None` slot: the runtime's form-neutral reader,
            # since the template cannot know whether the slot is an optional
            # or a pointer at this instantiation.
            _witness("isnone.generic_opt_trait")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(operand, lc, declared,
                                    allow_whole_optional=True),
                negate=e.op == "is not",
                trait_repr=True,
                form=Form.VALUE,
                loc=loc)
        is_field = isinstance(operand, TpyFieldAccess)
        # A @property subject reads through the getter call (the field arm
        # delegates to the method-call lowering); the None-test is has_value
        # over the materialized/ref-returned storage optional, any repr.
        prop_subject = is_field and operand.property_getter_call is not None
        # A raw `Ptr[T]` field (storage `T*`) None-tests via `== nullptr`, not
        # the Optional field's `.has_value()`.
        ptr_field = is_field and not prop_subject and _ptr_value_none_field(
            operand, declared, lc.analyzer)
        if prop_subject:
            _witness("isnone.property_subject")
        elif is_field:
            _witness("narrow.ptr_field_test" if ptr_field
                     else "narrow.opt_field_test")
        # A storage-form Optional container subscript subject (`d["a"] is
        # not None`): the has_value test over the bare `__getitem__` read.
        sub_storage = (isinstance(operand, TpySubscript)
                       and reads_storage_form_optional(analyzer, operand))
        if sub_storage:
            _witness("isnone.subscript_storage")
        value_repr = sub_storage or (is_field and not ptr_field) or (
            isinstance(operand, TpyName)
            and (_value_opt_binding_kind(operand.name, lc) is not None
                 # A storage-opt loop var / unpack target: the bare
                 # `std::optional<P>` binding None-tests via has_value.
                 or operand.name in lc.storage_opt_locals
                 # A nullable borrow-form tuple local
                 # (`std::optional<std::tuple<.., T*>>`): has_value too.
                 or operand.name in lc.optional_borrow_tuple_locals
                 or (operand.name in declared
                     and (_value_opt_scalar(declared[operand.name],
                                            lc.analyzer) is not None
                          # A value-repr `Optional[ValueType record]`
                          # binding: the same has_value test.
                          or _value_opt_value_record(declared[operand.name],
                                                     lc.analyzer) is not None
                          # A value-repr `Optional[Span[...]]` binding:
                          # the same has_value test over the bare name.
                          or _value_opt_span(declared[operand.name],
                                             lc.analyzer) is not None
                          # A value-repr `Optional[value tuple]` binding:
                          # the same has_value test over the bare name.
                          or _value_opt_tuple(declared[operand.name],
                                              lc.analyzer) is not None
                          # A value-repr `Optional[str/bytes]` binding --
                          # including a VIEW-inner LOCAL, which carries no
                          # registered kind (its narrowed read differs from
                          # the owned twin's) but None-tests the same way.
                          or _value_opt_view(declared[operand.name],
                                             lc.analyzer) is not None
                          # A `Callable | None` param: the same has_value
                          # test over the `std::optional<std::function>`.
                          or _value_opt_callable(declared[operand.name],
                                                 lc.analyzer)
                          is not None
                          # An `Own[P | None]` param (`std::optional<P>&&`):
                          # has_value over the bare name (optional_locals
                          # binding, not the pointer compare).
                          or _own_storage_opt_param(declared[operand.name],
                                                    lc.analyzer)
                          is not None)))) or (
            isinstance(operand, TpyNamedExpr)
            and (_value_opt_scalar(analyzer.get_expr_type(operand),
                                   lc.analyzer) is not None
                 # The view family's walrus target: the same has_value test.
                 or _value_opt_view(analyzer.get_expr_type(operand),
                                    lc.analyzer) is not None)) or (
            not isinstance(operand, (TpyName, TpyFieldAccess))
            and _value_opt_rvalue(operand, lc.analyzer) is not None)
        lowered_operand = _lower_expr(
            operand, lc, declared, allow_whole_optional=True,
            field_prechecked=is_field,
            subscript_prechecked=sub_storage)
        if (isinstance(operand, TpyName)
                and operand.name in lc.prescan.global_slots
                and isinstance(lowered_operand, THIRName)):
            # A pointer-slot global's None test compares the SLOT POINTER
            # (`g == nullptr`), so this position opts out of the
            # value-position deref the name arm applies everywhere else.
            lowered_operand = replace(lowered_operand, deref=False)
            _witness("isnone.global_slot")
        return THIRIsNone(
            result_type=rtype,
            operand=lowered_operand,
            negate=e.op == "is not",
            value_repr=value_repr,
            form=Form.VALUE,
            loc=loc)
    if e.op in _COMPARE_OPS and ptr_tuple_pair is not None:
        # The deref-aware tuple compare: borrow-form literal operands
        # (`std::tuple<int32_t, Box*>{1, &(a)}`) into the tuple_eq /
        # tuple_lt helper composition (derived ops negate / swap sides;
        # the templates carry their own parens).
        # The FIELD-pair flavor passes the BARE member reads instead --
        # the helpers bridge the storage form.
        lt_t, rt_t = ptr_tuple_pair
        if ptr_tuple_fields:
            left = _lower_expr(
                e.left, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
            right = _lower_expr(
                e.right, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
            _witness("binop.tuple_field_compare")
        else:
            left = _lower_borrow_tuple_literal(e.left, lt_t, lc, declared)
            right = _lower_borrow_tuple_literal(e.right, rt_t, lc, declared)
            _witness("binop.tuple_ptr_compare")
        tpl = {
            "==": "::tpy::tuple_eq({self}, {0})",
            "!=": "(!::tpy::tuple_eq({self}, {0}))",
            "<": "::tpy::tuple_lt({self}, {0})",
            ">": "::tpy::tuple_lt({0}, {self})",
            "<=": "(!::tpy::tuple_lt({0}, {self}))",
            ">=": "(!::tpy::tuple_lt({self}, {0}))",
        }[e.op]
        return THIRBinOp(result_type=rtype, left=left, op=e.op, right=right,
                         resolved=None, template_override=tpl, loc=loc)
    if e.op in _COMPARE_OPS and opt_eq_targets is not None:
        # The optional_safe_eq render: each optional side reads bare (a
        # narrowed side derefs at the name arm), the plain side opposite an
        # UN-narrowed optional renders against that optional's inner (the
        # target threading that drives char/numeric literal renders).
        # std::optional's mixed operator handles the compare. The
        # >int32-literal-vs-BigInt case is retargeted here by _slot_literal_retype
        # (l_tgt/r_tgt is the optional's inner type).
        l_tgt, r_tgt = opt_eq_targets
        _witness("binop.opt_scalar_eq")
        left = _slot_literal_retype(
            _lower_char_targeted(e.left, l_tgt, lc, declared,
                                 allow_whole_optional=True),
            l_tgt, lc)
        right = _slot_literal_retype(
            _lower_char_targeted(e.right, r_tgt, lc, declared,
                                 allow_whole_optional=True),
            r_tgt, lc)
    elif e.op in _COMPARE_OPS:
        if (_narrowed_opt_char_vs_str_literal(e.left, e.right, lc, declared)
                or _narrowed_opt_char_vs_str_literal(
                    e.right, e.left, lc, declared)):
            raise ThirUnsupported("binop.narrowed_char_eq_literal")
        lt_a = analyzer.get_expr_type(e.left)
        rt_a = analyzer.get_expr_type(e.right)
        # A str-family FIELD operand renders the bare member read into the
        # compare/concat/needle templates, so binop operands
        # are an owned-str-field-ok position (the flag is inert for every
        # non-str-field operand).
        # An UNPROVEN value-opt scalar ordering operand unwraps through
        # `deref_optional_check(x)`; every other operand keeps its targeted
        # render (the `==`/`!=` bare-optional path never reaches here).
        def _cmp_operand_use(side: TpyExpr) -> _ExprUse:
            # A record-returning call OPERAND (`datetime.combine(d, t) == dt`
            # -- operator== over the prvalue) rides BORROW_BIND so the
            # call's rvalue record result is admitted; every other operand
            # keeps the default value use.
            if _record_call_rvalue_operand(side, analyzer):
                return _ExprUse(result=_ExprResultUse.BORROW_BIND,
                                allow_temps=temps_ok)
            # An F1-record FIELD operand (`r.headers == other`) renders the
            # bare member read into the compare parens, like a record name.
            # A CONTAINER field operand (`self.tags == other.tags` -- the
            # @dataclass __eq__ chain) reads bare the same way. So does a
            # VALUE-tuple field: storage and borrow form coincide there, and
            # the narrowed-Optional unwrap is a separate declared-keyed fact
            # the field row carries, so this needs no declared-type key.
            if isinstance(side, TpyFieldAccess):
                st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(side))))
                if (_f1_ref(st, analyzer)
                        or _value_tuple(st, analyzer) is not None):
                    return _ExprUse(result=_ExprResultUse.BORROW_BIND)
            # Compare operands are target-less, so a both-literal
            # sub-binop folds.
            return _ExprUse(pos=SinkPos.OPERAND,
                            allow_temps=temps_ok)

        def _narrowed_union_read(side: TpyExpr, nu) -> 'THIRExpr | None':
            # The condition-scope-narrowed union NAME operand: the inline
            # get (`std::get<ZoneInfo>(tz)` / `(*std::get<Z*>(tz))`), the
            # compound-condition read's render at the compare slot.
            if nu is None:
                return None
            u, member = nu
            member_cpp, is_ptr = _narrow_member_cpp(side.name, member, u, lc)
            _witness("binop.narrowed_union_operand")
            return THIRNarrowedRead(
                result_type=member, variant_cpp=side.name,
                union_extraction=_union_extraction(side.name, u, member, lc),
                member_cpp=member_cpp, is_ptr_variant=is_ptr,
                form=(Form.BORROW if _is_borrow_form_name(member)
                      else Form.VALUE),
                loc=getattr(side, "loc", None))

        left = _narrowed_union_read(e.left, l_nu) or (
            _lower_unproven_opt_scalar(e.left, lc, declared)
                if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.left, rt_a, lc, declared, use=_cmp_operand_use(e.left),
            field_owned_str_ok=isinstance(e.left, TpyFieldAccess))
        right = _narrowed_union_read(e.right, r_nu) or (
            _lower_unproven_opt_scalar(e.right, lc, declared)
                 if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.right, lt_a, lc, declared, use=_cmp_operand_use(e.right),
            field_owned_str_ok=isinstance(e.right, TpyFieldAccess))
        # Compare operands are target-less EXCEPT opposite a record's own
        # comparison dunder: the emitted friend operator declares a real
        # param slot (`friend bool operator==(const Blob&,
        # ::tpy::BytesView)`), so a bytes literal there takes the
        # non-allocating static span the identically-shaped call arg spells
        # -- the owning render builds a heap vector per comparison for a span
        # the operator only reads through.
        _dun = _record_dunder_operand_slot(e.op, e, lt, rt, analyzer)
        if _dun is not None and _bytes_literal_view_slot(_dun[1]):
            _dun_e = _dun[0]
            if _dun_e is e.left:
                left = _retag_bytes_literal_span(left)
            else:
                right = _retag_bytes_literal_span(right)
        if (_bytes_compare_operand(e.left, lt, analyzer)
                and _bytes_compare_operand(e.right, rt, analyzer)):
            # A bytes-family pair compares through `::tpy::BytesView`, which
            # only reads the buffer, so a literal operand takes the same
            # static span for the same reason.
            left = _retag_bytes_literal_span(left)
            right = _retag_bytes_literal_span(right)
    else:
        lslot, rslot = _rb_operand_slots(e.resolved_binop)

        def _operand_slot(side: TpyExpr) -> 'TpyType | None':
            """The slot a NESTED-BINOP operand renders into: the resolved
            binop's receiver/param type, or -- for a native operator, which
            carries no method -- the operator's own result type. Never None
            there, because a target-less sub-binop would fold both-literal
            operands the full operator has to spell. Every other operand
            shape renders TARGET-LESS and takes the slot from
            `_slot_literal_retype` below instead: a container literal handed
            its slot up front retargets the brace init the concat arm spells
            afterwards, and rejects."""
            if not isinstance(side, TpyBinOp):
                return None
            slot = lslot if side is e.left else rslot
            return slot if slot is not None else rtype

        def _arith_operand_use(side: TpyExpr) -> _ExprUse:
            # A record-rvalue operand (`timedelta(...) + timedelta(...)`)
            # rides BORROW_BIND so the ctor/call's record result is
            # admitted -- the compare arm's `_cmp_operand_use` twin.
            if _record_call_rvalue_operand(side, analyzer):
                return _ExprUse(result=_ExprResultUse.BORROW_BIND,
                                allow_temps=temps_ok,
                                slot_target=_operand_slot(side))
            # An F1-record FIELD operand (`self.end - self.start`) renders
            # the bare member read into the operator parens, exactly as the
            # compare arm's `_cmp_operand_use` field row does.
            if isinstance(side, TpyFieldAccess):
                st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(side))))
                if _f1_record(st, analyzer):
                    return _ExprUse(result=_ExprResultUse.BORROW_BIND,
                                    allow_temps=temps_ok,
                                    slot_target=_operand_slot(side))
            # An arithmetic operand reads through a full deref, so a
            # pointer-bound RECORD name derefs here (`((*v)) + (inc)`)
            # rather than taking the bare arrow-receiver form its other
            # consumers want. Only NAME reads read the flag, so the two
            # shape-guarded rows above cannot need it.
            return _ExprUse(allow_temps=temps_ok,
                            slot_target=_operand_slot(side),
                            pos=SinkPos.OPERAND, forms=_ONLY_INDIRECT_READ)

        def _comp_operand(side: TpyExpr) -> 'THIRExpr | None':
            # The comprehension operand the container gate admitted: the
            # stmt-expr renders INLINE into the dunder template, target-typed
            # by the comprehension's own container (the const ref binds the
            # prvalue for the full expression, so no temp hoists).
            if not container_native_binop:
                return None
            comp_c = _comp_own_container(side, analyzer)
            if comp_c is None:
                return None
            from .comprehensions import _lower_comprehension
            _witness("binop.comprehension_operand")
            return _lower_comprehension(
                side, comp_c, lc, declared,
                _comp_shadow_pointers(lc.pointers, declared, analyzer))

        left = _comp_operand(e.left) or _lower_unproven_opt_scalar(
            e.left, lc, declared)
        if left is None:
            left = _slot_literal_retype(
                _lower_expr(e.left, lc, declared,
                            use=_arith_operand_use(e.left),
                            field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
                lslot, lc)
        right = _comp_operand(e.right) or _lower_unproven_opt_scalar(
            e.right, lc, declared)
        if right is None:
            right = _slot_literal_retype(
                _lower_expr(e.right, lc, declared,
                            use=_arith_operand_use(e.right),
                            cond_eager=logical_rhs_temps,
                            field_owned_str_ok=isinstance(e.right,
                                                          TpyFieldAccess)),
                rslot, lc)
        if e.resolved_binop is not None:
            # `_convert_to_fixed_int_arg` at the resolved binop's PARAM slot:
            # a BigInt operand against a declared fixed-int param takes the
            # checked narrow (an int literal stays bare). The BigInt test keys
            # on the DECLARED type, so a retro-widened literal-seeded local
            # narrows here too.
            _pside = e.left if e.resolved_binop.is_reverse else e.right
            _pslot = lslot if e.resolved_binop.is_reverse else rslot
            _pt = unwrap_readonly(_pslot) if _pslot is not None else None
            if is_fixed_int_type(_pt) and not isinstance(_pside, TpyIntLiteral):
                _pkey = _narrow_key_type(_pside, declared, analyzer)
                if _pkey is _NARROW_UNMIRRORED:
                    reject()
                if is_big_int_type(_pkey):
                    _narrowed = THIRCoerce(
                        result_type=_pt,
                        expr=(left if e.resolved_binop.is_reverse else right),
                        coercion_name=_BIGINT_NARROW,
                        wrap=f"({{0}}).to_fixed_check<{_pt.to_cpp()}>()",
                        loc=loc)
                    _witness("narrow.binop_param")
                    if e.resolved_binop.is_reverse:
                        left = _narrowed
                    else:
                        right = _narrowed
            # A container-literal operand of a LIST binop cannot deduce from
            # a bare brace-init, so it spells its type (`std::vector<T>{x}`)
            # -- the is_list(receiver_type) literal prefix.
            recv_bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                e.resolved_binop.receiver_type)))
            if is_list(recv_bare):
                if isinstance(left, THIRContainerLiteral):
                    left = replace(left,
                                   typed_brace_cpp=lc.render_type(recv_bare))
                if isinstance(right, THIRContainerLiteral):
                    right = replace(right,
                                    typed_brace_cpp=lc.render_type(recv_bare))
    bt = _resolved_bytes_value(rtype, analyzer)
    lcast, rcast = _binop_operand_casts(e, analyzer)
    # The dedicated fixed-int literal arm fires only where a
    # fixed-int TARGET is threaded (decl inits, returns, call/ctor args,
    # resolved-binop operand slots -- exactly `_slot_literal_retype`'s
    # positions), so the verdict lives THERE: lowering stamps the
    # position-independent operand fact (both operands IntLiteral-typed
    # non-names) and the retype rebuilds the node paren-free with the
    # target-resolved operator template. A target-less position
    # (print arg) keeps the generic parens even when the operands' sema
    # types are IntLiteral (a subscript over a literal-seeded array).
    both_lit = (e.op in _ARITH_OPS or e.op in _BITWISE_OPS) and (
        isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
        and not isinstance(e.left, TpyName)
        and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
        and not isinstance(e.right, TpyName))
    return THIRBinOp(
        result_type=rtype,
        left=left,
        op=e.op,
        right=right,
        resolved=e.resolved_binop,
        divisor_non_zero=e.divisor_non_zero,
        left_cast=lcast,
        right_cast=rcast,
        both_literal_int_operands=both_lit,
        template_override=mixed_cmp_tpl or open_eq_tpl,
        form=(Form.STORAGE if _is_string_owned(rtype)
              or (bt is not None and is_bytes_type(bt)) else Form.VALUE),
        loc=loc)

def _opt_record_none_subject(e: TpyBinOp, lc: '_LowerCtx') -> bool:
    """An `is [not] None` test whose non-None side is a registered
    owned-optional RECORD local (`upgraded is None` on a
    `std::optional<Rc<T>>` binding): the storage-form `.has_value()`
    compare, exactly the value-repr scalar/view render."""
    left_none = isinstance(e.left, TpyNoneLiteral)
    right_none = isinstance(e.right, TpyNoneLiteral)
    if left_none == right_none:  # both or neither
        return False
    operand = e.right if left_none else e.left
    return (isinstance(operand, TpyName)
            and lc.value_opt_bindings.get(operand.name)
            is ValueOptKind.RECORD)


def _value_opt_binding_kind(name: str, lc: '_LowerCtx') -> 'ValueOptKind | None':
    """The value-repr `std::optional<T>` binding kind of `name`, or None:
    a registered LOCAL (`lc.value_opt_bindings` -- for-each loop vars,
    chain-optional match captures, owned-view/record decls) or a PARAM of
    the function being lowered (scalar/view; a param is never RECORD).
    Kind-blind consumers (the None-test / truthiness renders, identical
    for every kind) test `is not None`; the read/move/reassign arms key on
    the kind, whose only difference is the narrowed-deref FORM verdict.
    The movable-seeded last-use moves stay param-only through
    `_is_move_source`'s movable guard (codegen never seeds a loop var or
    capture movable)."""
    k = lc.value_opt_bindings.get(name)
    if k is not None:
        return k
    t = _param_declared_type(name, lc)
    if t is not None:
        if _value_opt_scalar(t, lc.analyzer) is not None:
            return ValueOptKind.SCALAR
        if _value_opt_view(t, lc.analyzer) is not None:
            return ValueOptKind.VIEW
    return None


def _value_opt_scalar_binding(name: str, lc: '_LowerCtx') -> bool:
    """A value-repr `Optional[cheap scalar]` BINDING (`std::optional<T>`):
    the SCALAR kind of `_value_opt_binding_kind` (narrowed reads deref a
    VALUE)."""
    return _value_opt_binding_kind(name, lc) is ValueOptKind.SCALAR

def _value_opt_view_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is a value-repr `Optional[view]` param -- str OR bytes
    (`std::optional<std::string_view>` / `std::optional<std::span<const
    uint8_t>>`) -- of the function being lowered. A narrowed read unwraps
    `(*x)` (a borrow view), and a pass into another same-family
    `Optional[view]` slot takes the arg-split shim; a registered view LOCAL's
    deref is already OWNED, which is what the param/local split keys on."""
    return _value_opt_view(_param_declared_type(name, lc), lc.analyzer) is not None

def _value_opt_view_binding(name: str, lc: '_LowerCtx') -> bool:
    """A value-repr `Optional[view]` BINDING -- the VIEW kind of
    `_value_opt_binding_kind` (a param's narrowed deref is a BORROW view an
    owned sink copies; a registered local's is already OWNED storage -- the
    read arm branches on `param_names` for that)."""
    return _value_opt_binding_kind(name, lc) is ValueOptKind.VIEW


def _opt_viewfam_narrowed_concat_operand(e: TpyExpr, lc: '_LowerCtx',
                                         resolve) -> bool:
    """A NARROWED read of a registered value-repr `Optional[view]` BINDING at
    a concat operand: the VIEW name arm derefs (`(*t)`), which the family's
    concat helper takes exactly as spelled.
    Registration is REQUIRED -- an unregistered optional name would render
    bare off the family's name arm. `resolve` is the family's resolved-value
    reader, which is also what keeps the two families' rows apart."""
    if not isinstance(e, TpyName) or not _value_opt_view_binding(e.name, lc):
        return False
    at = lc.analyzer.get_expr_type(e)
    atu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    return (atu is not None and not isinstance(atu, OptionalType)
            and resolve(atu, lc.analyzer) is not None)

def _opt_view_narrowed_concat_operand(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """The str-concat flavor: the deref feeds `str_concat`."""
    return _opt_viewfam_narrowed_concat_operand(e, lc, _resolved_str_value)

def _opt_bytes_narrowed_concat_operand(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """The bytes-concat flavor: the deref feeds `bytes_concat`."""
    return _opt_viewfam_narrowed_concat_operand(e, lc, _resolved_bytes_value)

def _narrow_subject_is_ptr(var: str, u: UnionType, lc: '_LowerCtx') -> bool:
    """Whether a narrowing SUBJECT's `std::get` reads the POINTER variant.
    The verdict is the binding's, not the type's: codegen keys its extraction
    on `ctx.ptr_variant_locals` (params and pointer-variant local decls), so a
    ptr-variant-TYPED union reaching a name some other way -- a for-loop
    element over `list[A | B]`, whose storage form is the value variant --
    still extracts by value, which is what a False here selects. The verdict
    is only as good as the mirror: `lc.ptr_variant_locals` must stay complete
    against codegen's registrations (see its declaration)."""
    return is_ptr_variant_union(u) and var in lc.ptr_variant_locals


def _narrow_subject_const(var: str, lc: '_LowerCtx') -> bool:
    """Whether a pointer-variant narrowing SUBJECT spells const pointees: a
    const local (the U2 field-lift chain), or a param the function's
    deep-const verdict (`const_borrow_params` -- the inferred
    discriminant-only-use fact that also deep-consts the signature's variant
    spelling) applies to. Shared by the isinstance condition and every
    narrowed-member `std::get` template arg, so the renders cannot drift."""
    if var in lc.const_locals:
        return True
    return _param_is_deep_const(var, _const_verdict_func(var, lc),
                                lc.analyzer, lc.record_name)


def _poly_cast_context(var: str, lc: '_LowerCtx',
                       declared: dict[str, TpyType]
                       ) -> 'tuple[bool, str, TpyType | None]':
    """The (const, cast_arg, source_inner) triple shared by every
    polymorphic dynamic_cast composition (the if-init form, the no-alias
    OR-chain, the value-position chain), so the spellings cannot drift.
    Const pointees come from the param verdicts OR a readonly-qualified
    subject; the cast arg spells bare for pointer-shaped subjects, `&name`
    for `T&` ones. A post-if/assert-narrowed subject anchors to its ORIGINAL
    declared type (`lc.narrow.poly_source`) -- the declared-type lookup,
    which narrowing never retypes."""
    var_raw = lc.narrow.poly_source.get(var, declared.get(var))
    var_decl = _poly_subject_decl(var_raw)
    const = (_const_borrow_name(var, lc)
             or _poly_subject_readonly(var_raw))
    if var == lc.self_receiver:
        # `self` is already pointer-shaped in a plain method (`this`); a
        # resumable coro's `__self` is a `Record&` field, so it takes the
        # address. Mirrors polymorphic_cast_arg's self arms. A readonly
        # method's `this` is const, so the cast targets `const Sub*`.
        cast_arg = ("this" if lc.self_cpp == "this"
                    else f"&{lc.self_cpp}")
        const = const or "self" in lc.const_locals
    else:
        cast_arg = (escape_cpp_name(var)
                    if polymorphic_source_is_pointer(var_decl)
                    else f"&{escape_cpp_name(var)}")
    return const, cast_arg, polymorphic_source_inner(var_decl,
                                                     lc.analyzer.registry)


def _poly_cast_checks(var: str, members, lc: '_LowerCtx',
                      declared: dict[str, TpyType]) -> tuple[str, ...]:
    """The per-member `({dynamic_cast} != nullptr)` checks of a no-alias
    polymorphic isinstance (tuple / root-class / value-position forms),
    composed via the shared `narrow_cast_rhs` chokepoint over
    `_poly_cast_context`."""
    const, cast_arg, inner_src = _poly_cast_context(var, lc, declared)
    return tuple(
        f"({narrow_cast_rhs(lc.render_type(m), m, inner_src, cast_arg, is_const=const, analyzer=lc.analyzer)} != nullptr)"
        for m in members)


def _ptr_read_derefs(name: str, lc: '_LowerCtx') -> bool:
    """Whether a pointer-local's bare-name read still renders `(*p)` at the
    consumer re-tag sites: a live narrowing binding (the poly `(*__p_ptr)`
    spelling or a U3 extraction alias) replaces the read WHOLESALE, so the
    pointer deref must not re-fire on top of it (the double-deref seam)."""
    return (name in lc.pointers and name not in lc.narrow.spelled
            and name not in lc.narrow.narrowed)


def _name_read_deref(name: str, binding_type: 'TpyType | None',
                     lc: '_LowerCtx', use: _ExprUse,
                     whole_optional: bool = False,
                     analyzed_optional: bool = True) -> bool:
    """THE resolution point for a bare NAME read's indirection: True renders
    `(*name)`, False the bare name (an arrow receiver stays bare -- the
    field/method arms spell `->` themselves). An `indirect_read` position
    fully derefs any pointer-local, unless a live narrowing binding
    replaced the read wholesale. Every other position
    derefs any pointer-set name -- resumable frame slots and owned-slot
    walrus targets everywhere, and, outside receiver / borrow-bind
    positions, every other pointer binding -- EXCEPT the two classes whose
    consumers apply their own indirection: the RECORD class (bare by
    default, `->` rides the field/method arrow arms) and the ptr-repr
    Optional binding (None tests key on the pointer, pass-throughs forward
    it; the nullable-static-protocol param is NOT in that class: its
    monomorphized
    `const T_x*` derefs at every value position)."""
    # A SCALAR/char-pointee ptr-Optional binding first: its PROVEN-narrowed
    # value read derefs `(*v)` (no member arm carries the indirection),
    # wired whole-optional sinks read the bare pointer, and an UNPROVEN
    # whole read at any UNWIRED sink (`return v` into a value-repr Optional
    # slot, a value-opt param arg -- those sinks lift via ptr_to_optional)
    # REJECTS before either generic branch below could deref it (UB on the
    # None case) or forward it bare.
    if name in lc.pointers and binding_type is not None:
        bt0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(binding_type)))
        if isinstance(bt0, OwnType):
            bt0 = unwrap_readonly(bt0.wrapped)
        if (isinstance(bt0, OptionalType)
                and _nullable_static_protocol_param(bt0) is None):
            inner0 = unwrap_readonly(bt0.inner)
            if _eligible_scalar(inner0) or _eligible_char(inner0):
                if whole_optional:
                    return False
                if analyzed_optional:
                    raise ThirUnsupported("name.scalar_ptr_opt_unwired",
                                          detail=True)
                return True
    if use.admits(SinkForm.INDIRECT_READ) and _ptr_read_derefs(name, lc):
        return True
    if name in lc.frame_slots or name in lc.walrus_slot_locals:
        return True
    if (name in lc.pointers and binding_type is not None
            and use.result not in (_ExprResultUse.RECEIVER,
                                   _ExprResultUse.BORROW_BIND)):
        if name in lc.prescan.global_slots:
            return True
        bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(binding_type)))
        if _record_class_binding(bt):
            return False
        # The Own axis is repr-transparent for the carve-out: an
        # `Own[P | None]` param binds the storage optional and its
        # whole-optional reads render bare exactly like a ptr-repr borrow's.
        if isinstance(bt, OwnType):
            bt = unwrap_readonly(bt.wrapped)
        if (isinstance(bt, OptionalType)
                and _nullable_static_protocol_param(bt) is None):
            # Scalar/char pointees were fully resolved by the top guard;
            # the wide classes keep the bare-pointer carve-out.
            return False
        return True
    return False


def _narrow_variant_cpp(var: str, u: UnionType, lc: '_LowerCtx') -> str:
    """The C++ expression yielding the narrowing subject's `std::variant`: a
    recursive-alias wrapper union (F6) reaches it via `.value` (the
    VariantAccess.variant_expr indirection); every other routed union is the
    variant itself. A POINTER-bound subject (a resumable pointer-form loop var
    over `list[A | B]`) derefs first, like any other read of it. Shared by the
    isinstance condition and the extraction alias so the two spellings cannot
    drift."""
    # A frame_slot subject derefs like a pointer local (R1c: reads render
    # `(*name)`), with the same live-narrowing seam guard.
    _fs_deref = (var in lc.frame_slots
                 and var not in lc.narrow.spelled
                 and var not in lc.narrow.narrowed)
    base = f"(*{var})" if (_ptr_read_derefs(var, lc) or _fs_deref) else var
    return f"{base}.value" if u.needs_wrapper() else base


def _narrow_member_cpp(var: str, member: TpyType, u: UnionType,
                       lc: '_LowerCtx') -> tuple[str, bool]:
    """The final `std::get` template arg for a narrowed member read, plus the
    pointer-variant verdict: the ptr `*` suffix and the const-pointee prefix
    (`_narrow_subject_const`) applied at lowering. Shared by the extraction
    alias, the compound-condition inline read, and the assign-narrowed
    field-receiver read."""
    member_cpp = lc.render_type(member)
    is_ptr = _narrow_subject_is_ptr(var, u, lc)
    if is_ptr:
        const = "const " if _narrow_subject_const(var, lc) else ""
        member_cpp = f"{const}{member_cpp}*"
    return member_cpp, is_ptr


def _union_source_layout(var: str, typ: TpyType, lc: '_LowerCtx') -> THIRUnionLayout | None:
    bare = unwrap_readonly(unwrap_ref_type(typ))
    if not isinstance(bare, UnionType) and var not in lc.narrow.subject_union:
        return None
    original = lc.narrow.union_layouts.get(var)
    if (original is not None and isinstance(bare, UnionType)
            and not set(bare.members) <= set(original.type.members)):
        original = None
    layout = union_layout(original.type if original is not None else typ, lc.analyzer,
                          borrow=var in lc.ptr_variant_locals,
                          readonly=_narrow_subject_const(var, lc))
    if layout is not None:
        lc.narrow.union_layouts[var] = layout
    return layout


def _union_extraction(var: str, typ: TpyType, member: TpyType,
                      lc: '_LowerCtx') -> THIRUnionExtraction | None:
    layout = _union_source_layout(var, typ, lc)
    if layout is None:
        return None
    indices = [i for i, m in enumerate(layout.type.members)
               if unwrap_readonly(m) == unwrap_readonly(member)]
    return THIRUnionExtraction(var, layout, indices[0]) if len(indices) == 1 else None


def _union_test(var: str, typ: TpyType, members: list[TpyType] | tuple[TpyType, ...],
                lc: '_LowerCtx') -> THIRUnionTest | None:
    layout = _union_source_layout(var, typ, lc)
    if layout is None:
        return None
    indices = tuple(i for i, m in enumerate(layout.type.members)
                    if any(unwrap_readonly(m) == unwrap_readonly(t) for t in members))
    return THIRUnionTest(var, layout, indices) if indices else None


def _inline_union_member(var: str, member: TpyType, typ: UnionType,
                         lc: '_LowerCtx') -> tuple[str, bool, THIRUnionExtraction | None]:
    cpp, pointer = _narrow_member_cpp(var, member, typ, lc)
    return cpp, pointer, _union_extraction(var, typ, member, lc)


def _lower_isinstance_cond(info, condition: TpyExpr,
                           lc: '_LowerCtx') -> THIRExpr:
    """The isinstance-condition render shared by the narrow if / while /
    assert / ternary arms: the holds_alternative OR-chain (ptr `*` +
    const-pointee in the template args for pointer variants), or the bare
    `true` literal for sema's exhaustiveness fold."""
    var, u, members, folded = info
    cond_loc = getattr(condition, "loc", None)
    result_type = lc.analyzer.get_expr_type(condition)
    if folded:
        return THIRLiteral(result_type=result_type, value=True, loc=cond_loc)
    is_ptr = _narrow_subject_is_ptr(var, u, lc)
    const = "const " if (is_ptr and _narrow_subject_const(var, lc)) else ""
    if u.needs_wrapper():
        _witness("narrow.wrapper_union")
    return THIRIsinstance(
        result_type=result_type,
        union_test=_union_test(var, u, members, lc),
        variant_cpp=_narrow_variant_cpp(var, u, lc),
        member_cpps=tuple(
            f"{const}{lc.render_type(m)}*" if is_ptr else lc.render_type(m)
            for m in members),
        loc=cond_loc)


def _assign_narrowed_union_recv(obj: TpyExpr, declared: dict[str, TpyType],
                                lc: '_LowerCtx'
                                ) -> 'tuple[UnionType, TpyType] | None':
    """An ASSIGN-narrowed pointer-variant union NAME as a field receiver
    (`c: Circle | Rect = Circle(5.0); print(c.radius)`): sema's flow
    narrowing retyped the read to the concrete member with no isinstance
    alias in scope, so the receiver renders the inline bare get --
    `(*std::get<Circle*>(c)).radius`. Returns (union, member) or None."""
    if not isinstance(obj, TpyName) or obj.name not in declared:
        return None
    if obj.name in lc.narrow.narrowed or obj.name in lc.inline_narrowed:
        return None
    u = _eligible_ptr_union(declared[obj.name], lc.analyzer)
    if u is None:
        return None
    mt = lc.analyzer.get_expr_type(obj)
    mtb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(mt)))
           if mt is not None else None)
    if mtb is None or not any(
            mtb == m for m in u.members if not is_void_like_type(m)):
        return None
    return u, mtb


def _narrowed_union_compare_operand(side: TpyExpr,
                                    declared: dict[str, TpyType],
                                    lc: '_LowerCtx'
                                    ) -> 'tuple[UnionType, TpyType] | None':
    """A NAME compare operand sema retyped to a concrete union member with
    NO narrow entry in scope: the condition-scoped inline narrow (an
    isinstance leaf earlier in the enclosing `&&`/`||` chain installed the
    narrowing fact -- `print(tz is not None and isinstance(tz,
    ZoneInfo) and tz == waw)`), so the operand renders the inline get.
    The compare-operand sibling of `_assign_narrowed_union_recv`, widened
    to value unions (`std::get<M>(v)` -- no deref) beside the pointer
    variants. Returns (union, member) or None."""
    if not isinstance(side, TpyName) or side.name not in declared:
        return None
    if (side.name in lc.narrow.narrowed or side.name in lc.inline_narrowed
            or side.name in lc.narrow.spelled):
        return None
    dt = declared[side.name]
    u = (_eligible_value_union(dt)
         or _eligible_ptr_union_wide(dt, lc.analyzer))
    if u is None:
        return None
    mt = lc.analyzer.get_expr_type(side)
    mtb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(mt)))
           if mt is not None else None)
    if mtb is None or isinstance(mtb, UnionType) or not any(
            mtb == m for m in u.members if not is_void_like_type(m)):
        return None
    return u, mtb


def _async_factory_wrap_cpp(e: 'TpyName', target: 'TpyType | None',
                            lc: '_LowerCtx') -> 'str | None':
    """The coroutine-factory wrapper lambda for an `async def` referenced as
    a VALUE at a Callable slot returning `Own[@dynamic]`, spelled
    verbatim:

        [](<params>) -> <ret_cpp> {
            return ::tpy::make_adapter<Base>(factory(<args>)); }

    Own / non-value params move into the by-value frame-factory slot; value
    scalars pass through. None when the ref/slot is not that shape (a
    direct `await factory()` position never reaches the fn-ref value arm)."""
    return _async_factory_wrap_cpp_facts(e, target, lc.analyzer)


def _async_factory_wrap_cpp_facts(e: 'TpyName', target: 'TpyType | None',
                                  analyzer) -> 'str | None':
    """`_async_factory_wrap_cpp` over the analyzer alone -- the lowering
    context was the only thing it needed one for, and the arg table carries
    discrete facts rather than the context."""
    fi = e.function_ref_info
    if fi is None or not fi.is_async:
        return None
    if (e.function_ref_type_args or fi.native_function or fi.type_params
            or fi.linkage != FunctionLinkage.DEFAULT):
        return None
    if not isinstance(target, CallableType):
        return None
    ret = target.return_type
    if not (isinstance(ret, OwnType) and is_dyn_protocol(ret.wrapped)):
        return None
    # The lambda body CALLS the factory, so a bare same-module spelling would
    # be ADL-visible inside `namespace tpyapp::<mod>`.
    factory = free_callee_cpp(analyzer.ctx.module_attributes,
                              analyzer.ctx.module_name,
                              analyzer.ctx.cpp_module_name, e.name, fi)
    if factory is None:
        return None
    base_cpp = dynamic_base_name(ret.wrapped, analyzer)
    params = []
    forwards = []
    for i, ptype in enumerate(target.param_types):
        pname = f"__a{i}"
        params.append(CallableType._callable_param_cpp(ptype) + " " + pname)
        if isinstance(ptype, OwnType) or not ptype.is_value_type():
            forwards.append(f"std::move({pname})")
        else:
            forwards.append(pname)
    return (f"[]({', '.join(params)}) -> {ret.to_cpp()} {{ "
            f"return ::tpy::make_adapter<{base_cpp}>("
            f"{factory}({', '.join(forwards)})); }}")


def _param_declared_type(name: str, lc: '_LowerCtx') -> 'TpyType | None':
    """The declared type of param `name` on the function being lowered, or None
    when `name` is not a param -- the source-type lookup the arg-split shim keys
    its family match on."""
    for n, t in lc.params:
        if n == name:
            return t if isinstance(t, TpyType) else None
    return None

def _check_cond_eager_temps(node: THIRExpr) -> None:
    """The conditional-operand exit check: a temp that materializes an
    operand of a conditionally evaluated position lives INSIDE that
    position's region, never at the enclosing statement.

    The question is whether the emit will BANK the temp into the region
    (`THIRArgTemp.would_bank`, which asks the same `banks_in_region`
    predicate both temp sinks act on), not
    whether it merely COULD -- reading "cannot defer" as "eager is
    correct here" is what hoisted an owning copy ahead of its guard. A
    banking row passes. A row that does NOT bank lands ahead of the guard
    and so passes only where deferral is impossible: an AUDITED
    non-movable payload, which `std::optional::emplace` cannot take, and
    sema warns at the argument that it is built unconditionally. An
    UNAUDITED row never decided the question, so it stays fenced."""
    if isinstance(node, THIRArgTemp):
        if not node.would_bank():
            if node.movable is None:
                raise ThirUnsupported("argtemp.cond_defer", detail=True)
            _witness("argtemp.cond_eager")
        else:
            _witness("argtemp.cond_defer_audited")
    if isinstance(node, THIRUnionArgLift) and node.temp_cpp is not None:
        # The union-lift temp is hoisted EAGERLY at the enclosing statement,
        # but a conditional operand is exactly where it must be banked
        # instead (an uninit `std::optional<T>` slot plus a deferred
        # `emplace`), so the two placements disagree. The node carries no
        # audited defer fact to decide between them, and where the member's
        # own ctor arg hoists a deferred temp, the eager decl reads it before
        # its emplace -- so the whole row stays fenced here.
        raise ThirUnsupported("unionlift.cond_defer", detail=True)
    for f in dataclass_fields(node):
        v = getattr(node, f.name)
        if isinstance(v, THIRExpr):
            _check_cond_eager_temps(v)
        elif isinstance(v, tuple):
            for item in v:
                if isinstance(item, THIRExpr):
                    _check_cond_eager_temps(item)


def _lower_computed_call(
        e: TpyCall | TpyMethodCall, callee: TpyExpr, rtype: TpyType,
        lc: _LowerCtx, declared: dict[str, TpyType], use: _ExprUse) -> THIRExpr:
    ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        lc.analyzer.get_expr_type(callee))))
    if (not isinstance(ct, CallableType) or ct.is_template
            or e.kwargs or e.double_star_unpack is not None):
        note_detail("call.expr_callee_shape")
        raise ThirUnsupported(call_reject_reason("expr.call"))
    cparams = list(ct.param_types)
    if len(cparams) != len(e.args):
        note_detail("call.expr_callee_arity")
        raise ThirUnsupported(call_reject_reason("expr.call"))
    _witness("call.expr_callee")
    return THIRCall(
        result_type=rtype, callee="",
        callee_expr=_lower_expr(callee, lc, declared),
        args=tuple(_lower_free_call_arg(
            e, a, cparams[i], ("local", ""), lc, declared,
            temp_args=use.allow_temps, readonly_target=False,
            arg_index=i) for i, a in enumerate(e.args)),
        loc=e.loc)


def _lower_expr(e: TpyExpr, lc: '_LowerCtx',
                declared: dict[str, TpyType], *,
                use: _ExprUse = _ExprUse(),
                cond_eager: bool = False,
                **kwargs) -> THIRExpr:
    """`_lower_expr_impl` plus the conditional-operand exit check:
    `cond_eager` (a per-call lowering mode, deliberately NOT an _ExprUse
    flag -- it is consumed right here and never propagates) walks the
    lowered subtree once, so the grant sites (the logical RHS, ternary
    scalar arms, chained comparators i>=2) need no per-row threading."""
    lowered = _lower_expr_impl(e, lc, declared, use=use, **kwargs)
    if (isinstance(e, TpyName) and isinstance(lowered, THIRName)
            and lowered.name == e.name and lowered.cpp is None):
        binding = lc.narrow.subject_union.get(e.name, declared.get(e.name))
        if binding is not None:
            layout = _union_source_layout(e.name, binding, lc)
            if layout is not None:
                lowered = replace(lowered, union_read=layout)
    if (isinstance(e, TpyName) and isinstance(lowered, THIRName)
            and lowered.cpp is None and not lowered.opt_deref_check):
        binding = declared.get(e.name)
        scalar = _value_opt_scalar_binding(e.name, lc)
        pointer = (e.name in lc.pointers or e.name in lc.prescan.param_names)
        layout = optional_layout(binding, lc.analyzer, borrow=pointer,
                                 readonly=e.name in lc.const_locals or _param_is_const(
                                     e.name, lc.func, lc.analyzer, lc.record_name)) if binding is not None else None
        if layout is not None and (scalar or pointer):
            extract = (lowered.deref if scalar else
                       not kwargs.get("allow_whole_optional", False)
                       and not isinstance(unwrap_readonly(unwrap_ref_type(lowered.result_type)), OptionalType))
            lowered = replace(lowered, optional_read=THIROptionalRead(layout, extract))
    if isinstance(lowered, (THIRName, THIRWalrus)):
        fact = global_name_binding(lowered.name, lowered.result_type, lc)
        if fact is not None:
            lowered = replace(lowered, global_binding=fact)
    if cond_eager:
        _check_cond_eager_temps(lowered)
    return lowered


def _lower_expr_impl(e: TpyExpr, lc: '_LowerCtx',
                declared: dict[str, TpyType], *,
                use: _ExprUse = _ExprUse(),
                allow_whole_optional: bool = False,
                allow_unrouted_name: bool = False,
                allow_union_divergent: bool = False,
                field_prechecked: bool = False,
                field_owned_str_ok: bool = False,
                own_slot_coerce: bool = False,
                subscript_prechecked: bool = False,
                container_threaded: bool = True,
                array_retype: bool = True,
                error_return_raw: bool = False,
                er_expr_unwrap: bool = False,
                elem_storage: bool = False) -> THIRExpr:
    # `elem_storage` is the IMMEDIATE container-element / dict key-value /
    # tuple-element position, where an Optional-result ternary renders the
    # VALUE spelling (`std::optional<T>(<arm>)`) even at pointer repr because
    # the element STORAGE slot is the value optional -- the
    # `in_container_element` carve-out. A per-call lowering mode like
    # `cond_eager`, NOT an _ExprUse flag: it is consumed at the ternary
    # dispatch below and never propagates, because a sticky version of the
    # fact would reach deeper positions than it should
    # (`_LowerCtx.in_container_elem` rejects those positions instead).
    # `allow_temps` admits the arg-temp rows for THIS expression's args only
    # when it is a free call: set by the five flushable statement positions
    # over their direct value, never propagated into subexpressions (each of
    # those positions is where the single pre-statement flush point
    # places the `__tmp_N` decls).
    # The name flags likewise apply only to THIS expression: specialized
    # consumers opt into a whole binding or a constructor-only move source.
    analyzer = lc.analyzer
    temp_args = use.allow_temps
    # A container-literal local's use sites keep the pre-resolution pending type
    # on the expr (the resolver unwraps it in get_resolved_type);
    # THIR nodes must carry fully-resolved types. Same for a str local's
    # PendingStrType (sema's view/owned usage resolution is final pre-lowering).
    rtype = analyzer.get_expr_type(e)
    rtype = resolve_pending_container(rtype, analyzer) or rtype
    rtype = _resolve_pending_view(rtype, analyzer) or rtype
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        if (e.name in lc.forwarded_map
                and not e.is_function_ref
                and e.name != lc.self_receiver
                and e.name not in lc.narrow.narrowed
                and e.name not in lc.inline_narrowed
                and e.name not in lc.narrow.spelled):
            # A forwarded proto-param alias read (`xs` for `xs = it`): the
            # compile-time rename to the backing param -- delegate so every
            # downstream fact (binding type, frame classification, deref
            # verdicts) is the PARAM's own. NARROWED / spelled / self /
            # function-ref flavors keep the ordinary arms: the rename
            # applies at the render TAIL, so those arms take precedence.
            fwd = TpyName(lc.forwarded_map[e.name])
            fwd.loc = getattr(e, "loc", None)
            if rtype is not None and analyzer.get_expr_type(fwd) is None:
                analyzer.ctx.set_expr_type(fwd, rtype)
            _witness("name.forwarded_alias")
            return _lower_expr(fwd, lc, declared, use=use,
                               allow_whole_optional=allow_whole_optional)
        if (e.is_function_ref and e.name not in lc.nested_def_locals
                and _func_ref_routable(e, analyzer)):
            # A function used as a value -> the plain function-ref render,
            # carried on the pre-spelled `cpp` slot: the qualified name for a
            # cross-module import, else the bare escaped name; a generic ref
            # appends the template-args suffix (`identity<int32_t>`).
            qual = lookup_imported(analyzer.ctx.module_attributes, e.name,
                                   SymbolKind.FUNCTION)
            cpp = (qualified_cpp_name(*qual) if qual is not None
                   else escape_cpp_name(e.name))
            if e.function_ref_type_args:
                cpp += "<" + ", ".join(
                    lc.render_type(unwrap_ref_type(t))
                    for t in e.function_ref_type_args) + ">"
                _witness("name.func_ref_targs")
            _witness("name.func_ref")
            return THIRName(result_type=rtype, name=e.name, cpp=cpp,
                            form=Form.VALUE, loc=loc)
        if e.is_function_ref and e.name not in lc.nested_def_locals:
            wrap_cpp = _async_factory_wrap_cpp(e, rtype, lc)
            if wrap_cpp is not None:
                # An `async def` referenced as a value at a Callable slot
                # returning `Own[@dynamic]`: the coroutine-factory wrapper
                # lambda, pre-spelled on cpp.
                _witness("name.async_factory_wrap")
                return THIRName(result_type=rtype, name=e.name, cpp=wrap_cpp,
                                form=Form.VALUE, loc=loc)
        if e.is_function_ref and e.name in lc.nested_def_locals:
            # A closure local read as a value (`callbacks.append(add_offset)`):
            # the nested def bound a lambda under its own name, so the read is
            # the bare local -- never the module-qualified spelling.
            # The return arm's `ret.closure_name` shape at expression level.
            _witness("name.closure_local")
            return THIRName(result_type=rtype, name=e.name, form=Form.VALUE,
                            loc=loc)
        if e.name in lc.forbidden_reads:
            raise ThirUnsupported("stmt.match")
        if e.name not in declared:
            raise ThirUnsupported("name.global_read", detail=True)
        binding_type = _param_declared_type(e.name, lc)
        _bt_is_param = binding_type is not None
        if binding_type is None:
            binding_type = declared.get(e.name)
        unrouted = _unrouted_binding_read(binding_type, analyzer,
                                          is_param=_bt_is_param,
                                          movable_local=(
                                              e.name in lc.movable_locals))
        # An Own param that became a plain RESUMABLE frame FIELD: the
        # `name.own_read` verdict exists because `seed_param_locals` marks a
        # sync Own param movable, so its last-use read renders `std::move(p)`.
        # A frame body has no such binding -- the payload was moved into the
        # frame at construction and every body read is a bare member read.
        _frame_own_read = (unrouted == "name.own_read"
                           and lc.resumable_leaf_mode
                           and (e.name in lc.plain_frame_fields
                                # ... and the frame SLOT sibling (a loop var
                                # over an Own[container] source): the payload
                                # lives in the frame's optional slot, so every
                                # body read is the bare `(*row)` peel and
                                # nothing is ever moved out of it.
                                or e.name in lc.frame_slots))
        if (unrouted is not None and not allow_unrouted_name
                and not _frame_own_read
                # A REGISTERED owned-optional record local has a routed read
                # arm (the RECORD-kind branch below), and a storage-optional
                # unpack target its own STORAGE row; the unrouted-binding
                # verdict covers params and unregistered bindings only.
                and lc.value_opt_bindings.get(e.name)
                is not ValueOptKind.RECORD
                and e.name not in lc.storage_opt_locals
                # ... and the nullable borrow-form tuple local, whose
                # whole/narrowed reads have their own arm below.
                and e.name not in lc.optional_borrow_tuple_locals):
            raise ThirUnsupported(unrouted, detail=True)
        if _frame_own_read:
            _witness("name.frame_own_field")
        # A view-INNER value-opt LOCAL (`StrView`/`BytesView | None` ->
        # `optional<string_view>`) admits only its WHOLE-optional read: the
        # narrowed read is a whole-optional-wrap quirk this arm does not
        # spell and the None-test would key value-repr off the param-only
        # predicate, so those reject. (Its owned-inner twin, `str`/`bytes
        # | None`, is handled by the VIEW-kind binding; a value-opt-view
        # PARAM keeps its own arm.)
        if (not allow_whole_optional
                and e.name not in lc.prescan.param_names and e.name in declared
                and _value_opt_view(declared[e.name], analyzer) is not None
                and _value_opt_owned_view(declared[e.name], analyzer) is None):
            raise ThirUnsupported("name.value_opt_view_inner_local", detail=True)
        if (not allow_whole_optional
                and isinstance(unwrap_readonly(analyzer.get_expr_type(e)),
                               OptionalType)):
            if _value_opt_scalar_name(e, declared, analyzer) is not None:
                raise ThirUnsupported(
                    "name.optval_unproven_read", detail=True)
            if _value_opt_view_name(e, declared, analyzer) is not None:
                raise ThirUnsupported(
                    "name.optstr_unproven_read", detail=True)
            if _value_opt_span(binding_type, analyzer) is not None:
                # An UN-narrowed whole-optional-span read outside the
                # whole-optional positions -- unwitnessed, keep out.
                raise ThirUnsupported(
                    "name.optspan_unproven_read", detail=True)
            if _value_opt_tuple(binding_type, analyzer) is not None:
                # The value-tuple kind's NARROWED read is routed by the arm
                # below; an UN-narrowed whole read outside the
                # whole-optional positions is still unwitnessed -- keep it
                # out, like the span kind above.
                raise ThirUnsupported(
                    "name.opttuple_unproven_read", detail=True)
        if (not allow_whole_optional
                and _value_opt_span(binding_type, analyzer) is not None
                and not isinstance(unwrap_readonly(rtype), OptionalType)):
            # A NARROWED value-opt-span read derefs `(*values)`, which this
            # arm does not spell; only the whole-optional None-test is
            # admitted (the binding admission in _unrouted_binding_read is
            # scoped to it), the callable kind's structure.
            raise ThirUnsupported("name.optspan_narrowed_read", detail=True)
        if (not allow_whole_optional
                and _value_opt_callable(binding_type, analyzer) is not None
                and not isinstance(unwrap_readonly(rtype), OptionalType)):
            # A NARROWED value-opt-callable read (`cb(x)` under `cb is not
            # None`) derefs `(*cb)`, which this arm does not spell; only
            # the whole-optional bare read is admitted (the binding
            # admission in _unrouted_binding_read is scoped to it).
            raise ThirUnsupported("name.optcallable_narrowed_read",
                                  detail=True)
        if (not allow_union_divergent
                and e.name not in lc.inline_narrowed
                and _union_binding_divergent(e, declared, analyzer)):
            raise ThirUnsupported("name.union_binding_divergent", detail=True)
        if e.name == lc.self_receiver:
            self_spelled = lc.narrow.spelled.get(e.name)
            if self_spelled is not None:
                # Inside an `isinstance(self, Sub)` branch the read routes
                # through the pre-bound cast pointer (`(*__self_ptr)`), not
                # the receiver spelling -- the same substitution any
                # polymorphic-narrowed name takes.
                _witness("self.poly_narrowed")
                return THIRName(
                    result_type=rtype, name=e.name, cpp=self_spelled,
                    form=Form.BORROW, loc=loc)
            # The method receiver -> `this` (plain method) or `__self` (a
            # resumable method coro's `Record&` frame field). A plain
            # method's receiver is a POINTER, so the read carries its
            # value-position deref intrinsically (`(*this)`); the field /
            # method receiver positions that reach a member through the
            # pointer clear it via `_self_recv_positioned`.
            _witness("self.this")
            return THIRSelf(result_type=rtype, form=Form.BORROW,
                            cpp=lc.self_cpp, deref=lc.self_is_pointer,
                            loc=loc)
        gcpp = lc.prescan.global_cpp.get(e.name)
        if gcpp is None and lc.top_level_scope:
            # Module init reads an imported name that this module REDEFINES
            # later: statements before the redefinition still see the import
            # (the `current_stmt_line >= decl_line` test -- the one
            # order-dependent spelling in the module-init walk, resolved
            # against the statement line lowering is at).
            pre = lc.pre_decl_import_cpp.get(e.name)
            if pre is not None and lc.top_level_line < pre[0]:
                gcpp = pre[1]
        if e.name in lc.prescan.global_slots:
            # A read-only-seeded pointer-slot global: rides the pointer-local
            # arms via lc.pointers (`(*g)` derefs below, `->` receivers, the
            # addr-coerce pre-deref); an imported one spells through gcpp.
            _witness("name.global_slot")
        elif gcpp is not None:
            # A read-only-seeded native/imported value global: routes through
            # the same arms below, the fixed spelling riding THIRName.cpp.
            _witness("name.global_native" if e.name in lc.prescan.native_globals
                     else "name.global_imported")
        elif e.name in lc.prescan.global_readonly:
            # A read-only-seeded same-module value global: renders bare like a
            # local of the same resolved type (the arms below), so the witness
            # is the only distinguishing site.
            _witness("name.global_seeded")
        inr = lc.inline_narrowed.get(e.name)
        if inr is not None:
            # A compound-condition read of the narrowed subject: no alias
            # exists yet, so it lowers to the structural bare-get node.
            member_cpp, is_ptr, extraction = inr
            return THIRNarrowedRead(
                result_type=rtype, variant_cpp=e.name, member_cpp=member_cpp,
                is_ptr_variant=is_ptr,
                union_extraction=extraction,
                form=Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE,
                loc=loc)
        spelled = lc.narrow.spelled.get(e.name)
        if spelled is not None:
            # A polymorphic-isinstance-narrowed read: the pre-bound cast
            # pointer's deref, rendered verbatim (`(*__p_ptr)`); rtype is the
            # narrowed subclass -- sema retyped the read.
            return THIRName(
                result_type=rtype, name=e.name, cpp=spelled,
                form=Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE,
                loc=loc)
        alias = lc.narrow.narrowed.get(e.name)
        if alias is not None:
            # A U3 isinstance-narrowed read renames to the extraction alias
            # (`ctx.narrowed_vars`): a `T&` record alias (BORROW, like a
            # REF_ALIAS local) or a scalar ref (VALUE). rtype is already the
            # narrowed member -- sema retyped the read.
            return THIRName(
                result_type=rtype, name=alias,
                form=Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE,
                loc=loc)
        if e.name in lc.optional_borrow_tuple_locals:
            # A nullable borrow-form tuple local: a NARROWED occurrence
            # (sema retyped the read to the tuple) derefs the optional
            # (`(*t)` -- the borrow tuple its std::get consumers read); a
            # whole-optional read (the None test) stays the bare
            # `std::optional<std::tuple<.., T*>>` binding.
            _obt_narrowed = (not isinstance(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
                OptionalType) and not allow_whole_optional)
            _witness("name.opt_btuple_deref" if _obt_narrowed
                     else "name.opt_btuple_whole")
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=(Form.BORROW if _obt_narrowed
                                  else Form.STORAGE),
                            deref=_obt_narrowed, loc=loc)
        if _value_opt_tuple(binding_type, analyzer) is not None:
            # A value-repr `Optional[value tuple]` local
            # (`std::optional<std::tuple<int32_t, int32_t, std::string>>`):
            # a NARROWED occurrence (sema retyped the read to the tuple)
            # derefs `(*coord)` -- the std::get / arg-slot consumers read
            # the bare tuple; a whole-optional read (the None test, the
            # whole-binding write) stays the bare optional. The value-tuple
            # twin of the nullable BORROW-tuple arm above. The narrowed
            # verdict is the predicate's, shared with the unpack source.
            _vot_narrowed = (
                not allow_whole_optional
                and _narrowed_value_opt_tuple_read(binding_type, rtype,
                                                   analyzer) is not None)
            _witness("name.opt_vtuple_deref" if _vot_narrowed
                     else "name.opt_vtuple_whole")
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=(Form.BORROW if _vot_narrowed
                                  else Form.STORAGE),
                            deref=_vot_narrowed, loc=loc)
        if e.name in lc.storage_opt_locals:
            # A storage-optional comp/genexpr unpack target (`auto& p =
            # std::get<0>(t);` -- the storage_form_optional_locals mirror):
            # an UN-narrowed read is the bare storage optional its `T*`-slot
            # consumers lift via optional_to_ptr; a NARROWED occurrence is
            # not lowered yet -- it rejects rather than deref-render.
            if not isinstance(unwrap_readonly(rtype), OptionalType):
                raise ThirUnsupported("name.storage_opt_narrow", detail=True)
            _witness("name.storage_opt_whole")
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=Form.STORAGE, loc=loc)
        if (lc.value_opt_bindings.get(e.name) is ValueOptKind.RECORD
                or _value_opt_value_record(binding_type,
                                           analyzer) is not None):
            # An owned-optional RECORD local (`std::optional<Rc<T>>`,
            # registered at its call-init decl) or a value-repr
            # `Optional[ValueType record]` binding (`std::optional<Fixed>`).
            # A NARROWED read (sema retyped it to the record) unwraps
            # `(*upgraded)` -- a record lvalue consumed by a receiver/member
            # position; a whole-optional read (None-test) stays bare.
            narrowed = (not isinstance(unwrap_readonly(rtype), OptionalType)
                        and not allow_whole_optional)
            _witness("name.opt_record_deref" if narrowed
                     else "name.opt_record_whole")
            return THIRName(
                result_type=rtype, name=e.name, cpp=gcpp,
                form=Form.BORROW if narrowed else Form.STORAGE,
                deref=narrowed, loc=loc)
        if _value_opt_scalar_binding(e.name, lc):
            # A value-repr Optional[scalar] binding (`std::optional<T>` --
            # a param or a registered loop var). An
            # UN-narrowed read renders the bare optional (`p`, into an optional
            # slot); a NARROWED read (sema retyped it to the inner scalar,
            # `rtype` no longer Optional) unwraps `(*p)` -- the
            # value-optional deref. The bare-target positions that must NOT
            # deref a narrowed read (a value-optional call slot) strip this
            # deref at the arg boundary (_lower_call_arg).
            # A whole-optional consumer (None-test / print_optional_val / a
            # value-opt slot) reads the bare optional even where sema narrowed
            # the occurrence -- so `allow_whole_optional` suppresses the deref
            # (a value-opt LOCAL's None-test occurrence is narrowed, unlike a
            # param's, which stays Optional-typed here).
            narrowed = (not isinstance(unwrap_readonly(rtype), OptionalType)
                        and not allow_whole_optional)
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=Form.VALUE, deref=narrowed, loc=loc)
        if _value_opt_view_binding(e.name, lc):
            # A value-repr Optional[view] binding -- str
            # (`std::optional<std::string_view>` param / `<std::string>` local)
            # or bytes (`<::tpy::BytesView>` param / `<::tpy::Bytes>` local).
            # A NARROWED read (sema retyped it to the inner view, `rtype` no
            # longer Optional) unwraps `(*s)`. For a PARAM that deref is a
            # BORROW view, so an owned sink still gets the family copy
            # (`std::string`/`Bytes(x)`); for a LOCAL it is already OWNED
            # (STORAGE), so a sink takes it bare. An UN-narrowed read stays the
            # bare whole optional (VALUE), reached only inside the None-test /
            # truthiness / arg-shim wrappers (the bare value position is
            # unsupported). This must precede the str-name arm below, which keys
            # on the narrowed view rtype and would drop the deref.
            # A whole-optional consumer strips the deref off a NARROWED
            # occurrence exactly like the scalar/record rows: the None-test
            # keys on the C++ BINDING (`src1.has_value()`), so a
            # literal-init-narrowed local must not deref-render
            # `(*src1).has_value()` there.
            narrowed = (not isinstance(unwrap_readonly(rtype), OptionalType)
                        and not allow_whole_optional)
            if not narrowed:
                form = Form.VALUE
            elif (e.name in lc.prescan.param_names
                  and _value_opt_string_owned(binding_type) is None):
                # An `Optional[String]` PARAM is `std::optional<std::string>`
                # by value at the param too, so its deref is OWNED (STORAGE)
                # like a local's -- the one owned-inner param of this kind.
                form = Form.BORROW
            else:
                form = Form.STORAGE
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=form, deref=narrowed, loc=loc)
        # A non-value name (a record param / REF_ALIAS / POINTER local used as a
        # field receiver) is a borrow; scalars are value form. A pointer-repr tuple
        # name is a borrow tuple param (`std::tuple<..., T*>`) UNLESS it is an F3
        # storage-tuple alias local (`auto&& t = ...`, which aliases storage and reads
        # as STORAGE). The tag is informational for the field-access / convert emit,
        # but kept honest so a convert source is never mislabeled. A str-slice name
        # is the exception where the tag is LOAD-BEARING: BORROW (string_view param /
        # view local) drives the owned-sink `std::string(x)` copy, STORAGE (owned
        # local) suppresses it.
        str_t = _resolved_str_value(rtype, analyzer)
        if str_t is not None:
            return THIRName(result_type=str_t, name=e.name, cpp=gcpp,
                            form=_str_name_form(e.name, str_t,
                                                lc.prescan.param_names,
                                                lc.prescan.owned_viewfam_params),
                            loc=loc)
        # A bytes-slice name carries the same load-bearing view/owned form tag
        # as str: BORROW (span param / view local) drives the owned-sink
        # `::tpy::Bytes(x)`, STORAGE (owned vector local) suppresses it.
        bytes_t = _resolved_bytes_value(rtype, analyzer)
        if bytes_t is not None:
            return THIRName(result_type=bytes_t, name=e.name, cpp=gcpp,
                            form=_bytes_name_form(e.name, bytes_t,
                                                  lc.prescan.param_names,
                                                  lc.prescan.owned_viewfam_params),
                            loc=loc)
        if _is_string_owned(rtype):
            # A String local (a concat-result binding): an owned std::string
            # lvalue, so STORAGE -- the owned-sink copy never fires on it and
            # the tag stays honest ( _is_borrow_form_name would mislabel it).
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=Form.STORAGE, loc=loc)
        if e.name in lc.storage_tuple_locals:
            form = Form.STORAGE
        elif _is_own_param(e.name, lc):
            # An `Own[...]` param owns its storage (a by-value / rvalue-ref
            # slot): STORAGE, not a borrow of someone else's -- keeps the MIL
            # move source and the validator's storage-sink rule honest.
            form = Form.STORAGE
        else:
            form = Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE
        # The indirection verdict (frame slots' `(*name)` slot reads, the F2d
        # rebound-container value deref, pointer-slot globals, the
        # indirect_read positions) lives in _name_read_deref -- one resolver
        # for every consumer, so a position cannot re-derive it differently.
        return THIRName(result_type=rtype, name=e.name, cpp=gcpp, form=form,
                        deref=_name_read_deref(
                            e.name, binding_type, lc, use,
                            whole_optional=allow_whole_optional,
                            analyzed_optional=isinstance(
                                unwrap_readonly(unwrap_ref_type(
                                    unwrap_send_sync(rtype))), OptionalType)
                            if rtype is not None else True),
                        loc=loc)
    if isinstance(e, TpyFieldAccess):
        if e.property_getter_call is not None:
            # A `@property` read is a getter method call in disguise
            # (`c.radius` -> `c.radius()`): lower the synthesized call
            # through the method-call arm, preserving this read's use
            # position. sema types
            # the field access, not the synthesized call, so seed the call's
            # result type from the field's (the getter's return type) before
            # the method-call arm reads it.
            pg = e.property_getter_call
            if rtype is not None and analyzer.get_expr_type(pg) is None:
                analyzer.ctx.set_expr_type(pg, rtype)
            # A whole-optional sink (is-None subject) threads through: the
            # getter's Optional return is consumed as the materialized/
            # ref-returned storage optional, same as a plain method call's.
            return _lower_expr(pg, lc, declared, use=use,
                               allow_whole_optional=allow_whole_optional)
        if e.dyn_getattr_call is not None:
            return _lower_dyn_getattr_call(e, rtype, lc, declared)
        if e.module_var_access is not None:
            return _lower_module_var(
                e, rtype, lc, *e.module_var_access, loc=loc,
                # A container-literal ELEMENT is a pinned consumer like the
                # print sink: the slot holds the element by value, so the
                # pointer slot's `(*slot)` read copy-initializes it.
                allow_ref_pointer=use.pos is SinkPos.CONTAINER_ELEM)
        if e.class_constant_owner is not None:
            return _lower_class_constant(
                e, rtype, lc, declared, loc,
                tuple_ok=use.admits(SinkForm.TUPLE_SOURCE))
        bare_mod = _bare_module_recv(e.obj, declared, analyzer)
        if (bare_mod is not None
                and _module_var_read_cpp(bare_mod, e.field, analyzer)
                is not None):
            # `mod.X` off a MODULE binding -- the same render as the dotted
            # form; a module receiver whose field is NOT a registered
            # variable falls through to the normal arms.
            # A METHOD-RECEIVER consumer is pinned like the print sink
            # (`(*environ).update(...)`), so the pointer-slot deref read is
            # admitted there; the for-head ITERABLE capture
            # (`auto& __src_N = (*environ);`) is the same pinned lvalue
            # read.
            return _lower_module_var(
                e, rtype, lc, bare_mod, e.field, loc=loc,
                allow_ref_pointer=(use.result in (
                    _ExprResultUse.RECEIVER, _ExprResultUse.BORROW_BIND,
                    _ExprResultUse.ITERABLE)
                    or use.pos is SinkPos.CONTAINER_ELEM))
        if not field_prechecked:
            if e.enum_member_of is not None:
                if _eligible_enum(e.enum_member_of, analyzer) is None:
                    raise ThirUnsupported("field.enum_member", detail=True)
            elif _enum_prop_wrap(e, analyzer) is None:
                result_ok = (
                    _eligible_scalar(rtype)
                    or _eligible_char(rtype)
                    or _eligible_enum(rtype, analyzer) is not None
                    or _is_type_param_slot(rtype)
                    or _eligible_ptr_value(rtype, analyzer)
                    or (use.result is _ExprResultUse.RECEIVER
                        and _f1_record(rtype, analyzer))
                    # A user-iterator F1-record field in the for-head
                    # (`for k in r.headers:`, the bare `auto& __src_N =`
                    # member bind) and an F1-record field compare operand
                    # (`r.headers == other`, the bare read in the compare
                    # parens) both render the plain member read.
                    or (use.result in (_ExprResultUse.ITERABLE,
                                       _ExprResultUse.BORROW_BIND)
                        and _f1_record(rtype, analyzer))
                    # A stored-awaitable field at the BORROWED suspend
                    # operand (`await self.evt` -> `__sub_0 = &(__self.evt)`)
                    # -- the skeleton owns the `&(..)` wrap; the operand is
                    # the bare member read.
                    or (use.result is _ExprResultUse.SUSPEND
                        and _f1_record(rtype, analyzer)
                        and _witness("field.suspend_borrow"))
                    # A field-access lvalue manager (`with self.mgr:`): the
                    # bare member read is the lvalue the `auto& __ctx_N`
                    # bind borrows (with.manager_borrowed_field's expr half).
                    or (use.admits(SinkForm.CTX_MANAGER)
                        and _f1_record(rtype, analyzer))
                    # The record FIELD-WRITE copy sink consumes the F1 field
                    # read whole (`h.p = h2.p;` -- the bare member read, the
                    # assign copies).
                    or (use.admits(SinkForm.RECORD_COPY)
                        and _f1_record(rtype, analyzer))
                    # A CONTAINER field in a for-head that reaches the generic
                    # iterable position -- the RESUMABLE frame's
                    # (`(__self.nodes).begin()`). The sync for-head has its
                    # own arms and never lands here. The bare member read is
                    # what begin()/end()
                    # are taken off, so the read itself is the whole render.
                    # ITERABLE only -- at the generic VALUE position a
                    # container field read is a copy-vs-alias decision its own
                    # consumers make.
                    # DECLARED-type keyed: a NARROWED `Optional[list]` field
                    # types as a plain container on the expr, but that read
                    # needs an unwrap -- keep it out (same rule as the
                    # str-family field rows).
                    or (use.result in (_ExprResultUse.ITERABLE,
                                       _ExprResultUse.BORROW_BIND)
                        and _plain_container_read(
                            _field_decl_type(e, declared, analyzer))
                        and _witness("field.container_iterable"))
                    # The NARROWED flavor of the row above: the declared
                    # Optional keeps it off that key, and the deref it needs
                    # is the same `field.narrowed_deref` flag every
                    # other narrowed-field read carries -- so the read is
                    # still the whole render, one unwrap deeper. Reached from
                    # the RESUMABLE for-head only (the sync route prechecks
                    # its field iterable, so that read skips this ladder);
                    # BORROW_BIND is excluded because no position reaches
                    # this arm through it, and admitting one would ship
                    # unwitnessed.
                    or (use.result is _ExprResultUse.ITERABLE
                        and _narrowed_opt_container_field(
                            e, rtype, declared, analyzer)
                        and _witness("field.narrowed_opt_container_iterable"))
                    # An F3 tuple FIELD read consumed by a borrow lift
                    # (`t = h.pair` -> `tuple_to_pointer<..>(h.pair)`): the
                    # bare member read feeds the wrap.
                    or (use.result is _ExprResultUse.BORROW_BIND
                        and _f1_tuple(rtype, analyzer) is not None)
                    # A VALUE-tuple field read consumed whole
                    # (`tuple_to_str(this->pair)` at `tuple[int32, str]`):
                    # forms coincide, the bare member read IS the render.
                    # The FIELD_VALUE_TUPLE verdict carries the same
                    # admission to the f-string interpolation, whose
                    # `tuple_to_str` wrap consumes the read whole exactly as
                    # the borrow lift does. A narrow verdict, not
                    # BORROW_BIND: that use also unlocks the record /
                    # container / pointer-repr-tuple legs above, which no
                    # interpolation witnesses.
                    or ((use.result is _ExprResultUse.BORROW_BIND
                         or use.admits(SinkForm.FIELD_VALUE_TUPLE))
                        and _value_tuple(rtype, analyzer) is not None
                        and _witness("field.value_tuple"))
                    or (use.result is _ExprResultUse.TRUTHY
                        and _truthiness_mode(rtype, analyzer) is not None)
                    # A Callable-VALUE field read (`apply(handler.cb, 10)`):
                    # the bare member read -- std::function is a value slot,
                    # no form split, the consuming sink gates itself.
                    or (_callable_value(rtype)
                        and _witness("field.callable_value"))
                    # The str-family member read off ANY receiver the ladder
                    # below admits -- a plain name/self, a record-element
                    # subscript (`src[0].name`), a record getitem
                    # (`xs[0].name` on ArrayList), a deref_check Optional
                    # pointer (`p.name`). The whole RESOLVED slice, owned
                    # `String` included: every sink that threads the flag
                    # renders the bare member read and composes its own wrap
                    # around it, so the form axis is the sink's business, not
                    # the result row's. A position that does NOT thread the
                    # flag (a container-literal ELEMENT) keeps rejecting;
                    # the membership receiver threads it, same as the needle.
                    or (field_owned_str_ok
                        and _resolved_str_value(rtype, analyzer) is not None
                        and _witness("fstr.str_field"))
                    # The bytes twin, off the SAME receiver set: the row is
                    # the value side only, so the ladder below answers the
                    # receiver for both families alike.
                    or (field_owned_str_ok
                        and _bytes_view_family_value(rtype, analyzer)
                        and _witness("print.bytes_field"))
                    # A WHOLE value-repr Optional field read into an optional
                    # sink (`flat = rec.key;` -- the bare member copy): only
                    # positions that consume the whole optional thread the
                    # flag, so narrowing-deref reads stay on their own arms.
                    # Optional[F1-record] fields store `std::optional<T>`
                    # and read bare the same way (`this->inner ==
                    # other.inner` / `repr_of(this->inner)`).
                    or (allow_whole_optional
                        and (_value_opt_scalar(rtype, analyzer) is not None
                             or _value_opt_owned_view(rtype, analyzer)
                             is not None
                             or _opt_f1_record(rtype, analyzer) is not None
                             # A value-tuple inner stores `std::optional<
                             # std::tuple<..>>` and copies whole just as bare
                             # (`use_auth = this->auth;`).
                             or _value_opt_tuple(rtype, analyzer) is not None
                             or _value_elem_container(rtype, analyzer)
                             is not None)
                        and _witness("field.whole_optional")))
                if not result_ok:
                    raise ThirUnsupported("field.result_type", detail=True)
                if not (
                        _subscript_field_recv_ok(e, analyzer)
                        or _field_receiver_or_unbound_self_ok(
                            e, declared, analyzer)
                        or _ptr_value_field_recv_ok(e, declared, analyzer)
                        or _user_deref_field_recv_ok(
                            e, declared, lc.narrow.narrowed, analyzer,
                            lc.pointers)
                        or _optional_checked_field(e, declared, analyzer)
                        or _optional_checked_field_over_call_ok(e, analyzer)
                        or _optional_checked_field_over_field_ok(
                            e, declared, analyzer)
                        or _field_over_subscript_ok(e, declared, analyzer)
                        or _optional_field_over_subscript_ok(
                            e, declared, analyzer)
                        or _optional_field_over_container_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_record_getitem_ok(
                            e, declared, analyzer, lc.pointers)
                        or _field_over_container_subscript_ok(
                            e, declared, analyzer, lc.pointers)
                        or _field_over_field_ok(e, declared, analyzer)
                        or _field_over_property_call_ok(e, analyzer)
                        or _field_over_binop_ok(e, analyzer)
                        or _field_over_walrus_ok(e, analyzer)
                        or _field_over_walrus_subscript_ok(e, lc)
                        or _field_over_global_record_ok(
                            e, declared, analyzer)
                        or _field_over_call_ok(e, analyzer)
                        or _tparam_protocol_field_recv_ok(
                            e, declared, lc.tparam_bounds)
                        or _tparam_protocol_field_over_field_ok(
                            e, lc.tparam_bounds, analyzer)
                        or _assign_narrowed_union_recv(e.obj, declared, lc)
                        is not None
                        # A slice-object NAME receiver (`index.start` in
                        # the split slice-overload body): the BasicSlice
                        # member reads bare.
                        or (isinstance(e.obj, TpyName)
                            and _slice_object_type(
                                analyzer.get_expr_type(e.obj))
                            and _witness("field.slice_recv"))
                        # A NARROWED owned-optional record NAME receiver
                        # (`r.v` on the registered `std::optional<Box>`
                        # local, or a value-repr `Optional[ValueType
                        # record]` binding): the name read derefs (`(*r)`),
                        # the field appends `.v` -- `(*r).v`, the narrowed
                        # read.
                        or (isinstance(e.obj, TpyName)
                            and (lc.value_opt_bindings.get(e.obj.name)
                                 is ValueOptKind.RECORD
                                 or _value_opt_value_record(
                                     declared.get(e.obj.name),
                                     analyzer) is not None)
                            and _witness("field.opt_record_recv"))):
                    raise ThirUnsupported("field.receiver_shape", detail=True)
        if _unbound_self_field_ok(e):
            # `BaseN.field` -> `this->BaseN::field`: an early return
            # that never looks at the syntactic class-name
            # receiver (it has no value type). The explicit base qualifier
            # picks one ancestor subobject in non-virtual MI, so the parent
            # spelling IS the member render -- carried in `field_cpp`, the
            # node's "rendered C++ member name". Read and write target alike.
            viewfam = _resolved_str_value(rtype, analyzer)
            if viewfam is None:
                viewfam = _resolved_bytes_value(rtype, analyzer)
            return _unbound_self_field_access(
                e, lc, rtype, _viewfam_result_form(viewfam), loc)
        if e.enum_member_of is not None:
            # Type-level enum member access: `Color.RED` -> `Color::RED`
            # (the ENUM binding read, spelled at lowering).
            return THIREnumMember(result_type=rtype,
                                  cpp=_enum_member_cpp(e, analyzer), loc=loc)
        prop = _enum_prop_wrap(e, analyzer)
        if prop is not None:
            # `c.value`: a plain underlying-int value. `c.name`: a
            # static-storage string_view -- BORROW, so owned-str sinks
            # copy it (the S1 view->owned convert), like any StrView-typed
            # read.
            if e.field == "name":
                _witness("enum.name")
                return THIREnumWrap(
                    result_type=rtype, wrap=prop,
                    operand=_lower_expr(e.obj, lc, declared), form=Form.BORROW,
                    loc=loc)
            _witness("enum.value")
            return THIREnumWrap(
                result_type=rtype, wrap=prop, operand=_lower_expr(e.obj, lc, declared),
                loc=loc)
        if (e.needs_optional_runtime_check and isinstance(e.obj, TpySubscript)
                and reads_storage_form_optional(analyzer, e.obj)):
            # Unproven access off a STORAGE-form Optional container
            # subscript: the whole optional lvalue is wrapped --
            # `deref_optional_check(::tpy::__getitem__(d, "a")).x` -- the
            # subscript sibling of the Optional-member-field arm below.
            _witness("field.opt_check_subscript_recv")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    subscript_prechecked=True,
                    allow_whole_optional=True),
                field_cpp=_field_cpp(e), opt_deref_check=True, loc=loc)
        if (e.needs_optional_runtime_check and isinstance(e.obj, TpyName)
                and e.obj.name in lc.storage_opt_locals):
            # Unproven access off a storage-opt NAME (a comp/genexpr loop var
            # or unpack target): a storage-form optional source wraps
            # the whole optional lvalue --
            # `deref_optional_check(item).x` -- no `T*` lift.
            _witness("field.opt_check_storage_name")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    allow_whole_optional=True),
                field_cpp=_field_cpp(e), opt_deref_check=True, loc=loc)
        if e.needs_optional_runtime_check and isinstance(e.obj,
                                                         (TpySubscript, TpyName)):
            # Unproven `Optional[record]` member access -> `deref_check(<T*>).field`.
            # A NAME receiver (an Optional-ptr param / OPTIONAL_TO_PTR local) is
            # already a bare `T*` (pointer_value_expr is the identity for it). A
            # subscript is a `T*` off a borrow tuple, or a `std::optional<T>` off a
            # storage alias lifted to `T*` via optional_to_ptr (the STORAGE-form
            # convert).
            # `deref_check` takes the RAW `T*` -- it applies its own null check
            # and member access -- so a pointer-slot GLOBAL name must not carry
            # the value-position `(*g)` deref it reads with everywhere else.
            sub = _strip_slot_leaf_deref(
                _lower_expr(e.obj, lc, declared, subscript_prechecked=True),
                lc)
            recv = (THIRFormConvert(result_type=sub.result_type, value=sub,
                                    form=Form.BORROW, loc=loc)
                    if sub.form is Form.STORAGE else sub)
            return THIRFieldAccess(
                result_type=rtype, receiver=recv,
                field_cpp=_field_cpp(e), deref_check=True, loc=loc)
        if (e.needs_optional_runtime_check
                and isinstance(e.obj, (TpyCall, TpyMethodCall))
                and _optional_checked_field_over_call_ok(e, analyzer)):
            # Unproven access off a BORROW-returning ptr-Optional CALL:
            # the raw `T*` result feeds deref_check directly
            # (`::tpy::deref_check(find(...)).x` -- the call sibling of
            # the Optional-ptr name receiver).
            _witness("field.opt_check_call_recv")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(pos=SinkPos.RECEIVER,
                                 forms=_recv_forms(True, False))),
                field_cpp=_field_cpp(e), deref_check=True, loc=loc)
        if (e.needs_optional_runtime_check
                and isinstance(e.obj, TpyFieldAccess)
                and _optional_checked_field_over_field_ok(
                    e, declared, analyzer)):
            # Unproven access off a STORAGE `Optional[record]` member lvalue:
            # the whole optional is wrapped -- `deref_optional_check(
            # h.opt).x` -- no `T*` lift (deref_check is the pointer sibling).
            _witness("field.opt_check_field_recv")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    field_prechecked=True),
                field_cpp=_field_cpp(e), opt_deref_check=True, loc=loc)
        # Scalar field read off a borrow receiver (value-form result). A plain
        # non-null `T*` pointer-local receiver renders `recv->field`; the non-value
        # field source for a borrow-local binding is built in _lower_field_source.
        # An owned-`str` field is STORAGE (a `std::string` member): the bare
        # read binds a view slot implicitly and copies into an owned slot by
        # value, so no form seam fires on it -- but the tag keeps the fact
        # honest for form-keyed sinks. A `StrView` field is BORROW: the
        # owned-str return sink fires its view->owned copy on the tag.
        fa_str = _resolved_str_value(rtype, analyzer)
        if fa_str is None:
            # A bytes-family field read (admitted only in the print sink) tags
            # its form off the bytes resolution: owned vector STORAGE, view span
            # BORROW -- the same view/owned mapping as str.
            fa_str = _resolved_bytes_value(rtype, analyzer)
        # A sema-narrowed Optional field read (declared std::optional<T>,
        # analyzed non-Optional) unwraps `(*recv.field)` in value positions;
        # plain-assign targets and the print_optional_val wrap strip the
        # flag. A WHOLE-optional consumer (the None-test's has_value over
        # the bare member) reads the storage un-derefed even where sema
        # narrowed the occurrence -- the deref-vs-bare split
        # `allow_whole_optional` carries, like the name arm's.
        narrowed_opt = (not allow_whole_optional
                        and _narrowed_opt_field_read(e, rtype, declared,
                                                     analyzer))
        if narrowed_opt:
            _witness("field.narrowed_deref")
        nu = _assign_narrowed_union_recv(e.obj, declared, lc)
        if nu is not None:
            # `.field` off an ASSIGN-narrowed ptr-variant union name: the
            # inline bare get -- `(*std::get<Circle*>(c)).radius` -- the
            # alias-free sibling of the isinstance-narrowed alias read.
            u, member = nu
            member_cpp, is_ptr = _narrow_member_cpp(e.obj.name, member, u, lc)
            _witness("field.assign_narrowed_union")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=THIRNarrowedRead(
                    result_type=analyzer.get_expr_type(e.obj),
                    variant_cpp=e.obj.name, member_cpp=member_cpp,
                    union_extraction=_union_extraction(e.obj.name, u, member, lc),
                    is_ptr_variant=is_ptr, form=Form.BORROW, loc=loc),
                field_cpp=_field_cpp(e),
                is_arrow=False,
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            )
        if (isinstance(e.obj, TpyName) and e.obj.name in lc.storage_opt_locals
                and not isinstance(
                    unwrap_readonly(analyzer.get_expr_type(e.obj)),
                    OptionalType)):
            # `.field` off a NARROWED storage-opt local (`item.x` under
            # `item is not None` in a comp): the `std::optional` receiver
            # derefs at the ACCESS site -- `(*item).x` --
            # while bare value positions (repr/print args) pass the whole
            # optional (the runtime overloads take it directly).
            _witness("field.storage_opt_recv")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=THIRName(
                    result_type=analyzer.get_expr_type(e.obj),
                    name=e.obj.name, form=Form.BORROW, deref=True, loc=loc),
                field_cpp=_field_cpp(e),
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            )
        if (isinstance(e.obj, TpyName)
                and e.obj.name not in lc.narrow.narrowed
                and e.obj.name not in lc.inline_narrowed):
            grt = _global_record_recv(e.obj, declared, analyzer)
            if grt is not None:
                # Same-module global-record receiver: a `T*` pointer slot
                # read bare with an arrow (`time->x`, is_indirect_name).
                # A read-only-seeded body sees the name in `declared` and
                # takes the general receiver path instead (name.global_slot);
                # this arm serves the un-seeded positions (`global g`-declared
                # bodies and other seeding exclusions).
                _witness("field.global_record_recv")
                return THIRFieldAccess(
                    result_type=rtype,
                    receiver=THIRName(result_type=grt, name=e.obj.name,
                                      form=Form.BORROW, loc=loc),
                    field_cpp=_field_cpp(e),
                    is_arrow=True,
                    narrowed_deref=narrowed_opt,
                    form=_viewfam_result_form(fa_str),
                    loc=loc,
                )
        if _ptr_value_field_recv_ok(e, declared, analyzer):
            # `.field` through an explicit `Ptr[record]` VALUE receiver (a Ptr
            # local/param name or an F1-record Ptr field read): the pointer arm
            # renders `<recv>->field` (proven non-null) or
            # `::tpy::deref_check(<recv>).field` (unproven), picked by sema's
            # `ptr_non_null`. Read and write target alike.
            _witness("field.ptr_value")
            fa_arrow, fa_deref = _ptr_recv_access(e.ptr_non_null)
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                field_cpp=_field_cpp(e),
                is_arrow=fa_arrow,
                deref_check=fa_deref,
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            )
        if _user_deref_field_recv_ok(e, declared, lc.narrow.narrowed, analyzer,
                                     lc.pointers):
            # `.field` auto-dereffed through a USER Deref wrapper:
            # `r.__deref__()...x` (N = deref_depth). A pointer-local receiver
            # (proven narrowed-Optional / F2-reseated) joins the first hop
            # with `->`; a plain value binding reads bare `.`.
            _witness("field.user_deref_chain")
            return _self_recv_positioned(THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                field_cpp=_field_cpp(e),
                is_arrow=_name_recv_is_arrow(e.obj, lc),
                deref_chain=e.deref_depth,
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            ))
        # A call-shaped receiver's own arg temps flush at the enclosing
        # statement; the receiver borrow form is independent of arrow/deref hops.
        receiver = _lower_expr(
            e.obj, lc, declared,
            use=_ExprUse(result=_ExprResultUse.RECEIVER,
                         allow_temps=use.allow_temps,
                         pos=SinkPos.RECEIVER, forms=_ONLY_FIELD_RECV_BORROW),
            subscript_prechecked=isinstance(e.obj, TpySubscript))
        return _self_recv_positioned(THIRFieldAccess(
            result_type=rtype,
            field_identity=direct_field(e, analyzer, receiver)
            if (isinstance(receiver, (THIRName, THIRSelf, THIRSubscript, THIRFieldAccess))
                or isinstance(receiver, THIRNarrowedRead) and receiver.union_extraction is not None) else None,
            receiver=receiver,
            field_cpp=_field_cpp(e),
            is_arrow=_field_is_arrow(e, lc),
            narrowed_deref=narrowed_opt,
            form=_viewfam_result_form(fa_str),
            loc=loc,
        ))
    if isinstance(e, TpySubscript):
        if e.typed_dict_field is not None:
            # `d["key"]` on a TypedDict -> the field access `d.key`; a
            # total=False field wraps the KeyError-panicking unwrap
            # `::tpy::typed_dict_field_check(d.key)`. Receivers: a bare
            # declared NAME, or a one-level FIELD off an admitted binding
            # (`p.addr["city"]` -> `p.addr.city`) -- an indirect /
            # narrowed-optional receiver needs an unwrap this arm does not
            # spell, so it rejects.
            if not _typed_dict_recv_ok(e.obj, declared, lc.pointers,
                                       lc.narrow.narrowed, analyzer):
                raise ThirUnsupported("subscript.typed_dict_recv",
                                      detail=True)
            td_str = _resolved_str_value(rtype, analyzer)
            if td_str is None:
                td_str = _resolved_bytes_value(rtype, analyzer)
            td_fa = THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                field_cpp=escape_cpp_name(e.typed_dict_field),
                form=_viewfam_result_form(td_str),
                loc=loc)
            if e.typed_dict_optional:
                _witness("subscript.typed_dict_check")
                return THIRCall(
                    result_type=rtype, callee="typed_dict_field_check",
                    native_name="tpy::typed_dict_field_check",
                    args=(td_fa,), form=_viewfam_result_form(td_str),
                    loc=loc)
            _witness("subscript.typed_dict")
            return td_fa
        if e.enum_from_name is not None:
            # `Color[name]` -> `::tpy::EnumUtil<E>::from_name(name)` (a static
            # lookup that panics KeyError on miss). The receiver is the enum
            # TYPE name (no value-position lowering); only the str index lowers.
            _witness("subscript.enum_from_name")
            cpp_type = e.enum_from_name.to_cpp()
            return THIREnumWrap(
                result_type=rtype,
                wrap=f"::tpy::EnumUtil<{cpp_type}>::from_name({{0}})",
                operand=_lower_expr(e.index, lc, declared),
                loc=loc)
        if not subscript_prechecked and e.needs_optional_runtime_check:
            raise ThirUnsupported("subscript.optional_check", detail=True)
        # Set by the read gate below for an Own[container] receiver; the
        # prechecked paths skip the gate and never need the pinned name.
        own_recv = False
        record_slice_tpl = None
        if e.slice_function_info is not None:
            if not subscript_prechecked:
                fi = e.slice_function_info
                # A list/Array/Span slice read yields an owned `list[T]`
                # (list_slice / list_stepped_slice) -- the viewfam gate below
                # excludes it (not a view result), so it takes its own arm:
                # same @cpp_template + BasicSlice/Slice initializer machinery,
                # STORAGE result. Bounds classify like the str-slice arm.
                rbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
                # A SPAN-yielding (non-stepped, view) list slice rides the
                # same template render: `::tpy::list_slice(xs, BasicSlice{..})`
                # -- a value-type view, so its borrow/storage forms coincide
                # and the STORAGE tag below stays correct.
                container_slice = (
                    bool(fi.cpp_template)
                    and "{cpp}" not in fi.cpp_template
                    and (is_list(rbare) or is_span(rbare)
                         or is_varargs(rbare))
                    and _container_slice_recv_ok(e.obj, lc, declared)
                    and ((isinstance(e.index, TpySlice)
                          and _slice_bound_supported(e.index.lower, analyzer)
                          and _slice_bound_supported(e.index.upper, analyzer)
                          and (e.index.step is None
                               or _slice_bound_supported(e.index.step,
                                                         analyzer)))
                         # A slice OBJECT index (`items[s]` on a declared
                         # basic_slice/slice binding): the same template
                         # over the bare index name -- the str-slice arm's
                         # slice-object leg.
                         or (isinstance(e.index, TpyName)
                             and e.index.name in declared
                             and _slice_object_type(
                                 declared[e.index.name]))))
                # A user record's own slice `__getitem__` overload (ArrayList
                # `a[1:4]` -> `a.__getitem__(::tpy::BasicSlice{1, 4})`):
                # the plain-method tail over the same BasicSlice
                # initializer, synthesized as the node's template.
                # Witnessed for a NAME receiver
                # of an F1 record with a Span result, non-stepped bounds.
                if (not container_slice
                        and fi.cpp_template is None
                        and not fi.native_function
                        and fi.is_method
                        and isinstance(e.index, TpySlice)
                        and is_span(rbare)
                        and isinstance(e.obj, TpyName)
                        # `self` renders `this` -- the bare `{self}.` member
                        # template would spell `.` on a pointer (it needs a
                        # `(*this)` pre-deref); unwitnessed, so it rejects.
                        and e.obj.name != lc.self_receiver
                        and _f1_record(declared.get(e.obj.name),
                                       analyzer)
                        and not _ptr_read_derefs(e.obj.name, lc)
                        and e.obj.name not in lc.frame_slots
                        and _slice_bound_supported(e.index.lower, analyzer)
                        and _slice_bound_supported(e.index.upper, analyzer)
                        # A STEPPED overload dispatches on the 3-part
                        # `::tpy::Slice{lo, hi, step}` initializer -- the
                        # emit picks the spelling off the stepped flag.
                        and (e.index.step is None
                             or (e.is_stepped_slice
                                 and _slice_bound_supported(e.index.step,
                                                            analyzer)))):
                    record_slice_tpl = ("{self}."
                                        + (fi.native_name or fi.name)
                                        + "({0})")
                if container_slice or record_slice_tpl is not None:
                    if record_slice_tpl is not None:
                        _witness("subscript.record_slice_method")
                    else:
                        _witness("subscript.container_slice")
                    slice_ok = True
                    fi = None  # skip the str-slice viewfam gate below
                else:
                    slice_ok = (
                        bool(fi.cpp_template)
                        and "{cpp}" not in fi.cpp_template
                        and _str_slice_receiver_supported(e.obj, lc, declared)
                        and _resolved_viewfam_value(rtype, analyzer) is not None)
                if fi is not None and slice_ok and isinstance(e.index, TpySlice):
                    sl = e.index
                    rt = _resolved_viewfam_value(rtype, analyzer)
                    if e.is_stepped_slice:
                        slice_ok = (
                            (is_str_type(rt) or is_bytes_type(rt))
                            and _slice_bound_supported(sl.step, analyzer))
                    else:
                        slice_ok = (
                            sl.step is None
                            and (is_str_view_type(rt)
                                 or is_bytes_view_type(rt)))
                    slice_ok = (
                        slice_ok
                        and _slice_bound_supported(sl.lower, analyzer)
                        and _slice_bound_supported(sl.upper, analyzer))
                elif fi is not None and slice_ok:
                    slice_ok = (
                        isinstance(e.index, TpyName)
                        and e.index.name in declared
                        and _slice_object_type(declared[e.index.name]))
                if not slice_ok:
                    raise ThirUnsupported("subscript.slice_shape", detail=True)
            # Str/bytes slice -> the resolved slice __getitem__'s @cpp_template
            # over a BasicSlice/Slice initializer (or a slice-typed variable
            # index rendered bare). The view result (string_view / span) is
            # BORROW -- an owned decl sink materializes it via the view->owned
            # THIRFormConvert (str: the strview_to_str coerce ->
            # `std::string(...)`; bytes: no coerce at a pending decl, the S6
            # decl-init BORROW wrap -> `::tpy::Bytes(...)`); the stepped /
            # slice-var owned result (std::string / std::vector<uint8_t>) is
            # STORAGE, landing bare in every sink. Absent bounds emit
            # std::nullopt.
            rt_view = _resolved_viewfam_value(rtype, analyzer)
            form = (Form.BORROW if rt_view is not None
                    and (is_str_view_type(rt_view) or is_bytes_view_type(rt_view))
                    else Form.STORAGE)
            mv = _module_var_access_pair(e.obj, declared, analyzer)
            if mv is not None:
                # The slice template is a pinned consumer of the module
                # var's `(*slot)` read (a native-slot substitution).
                recv = _lower_module_var(
                    e.obj, analyzer.get_expr_type(e.obj), lc, *mv,
                    loc=loc, allow_ref_pointer=True)
            else:
                recv = _lower_expr(
                    e.obj, lc, declared,
                    field_prechecked=isinstance(e.obj, TpyFieldAccess))
            tpl = record_slice_tpl or e.slice_function_info.cpp_template
            if not isinstance(e.index, TpySlice):
                return THIRStrSlice(
                    result_type=rtype, receiver=recv, cpp_template=tpl,
                    index=_lower_expr(e.index, lc, declared), form=form, loc=loc)
            sl = e.index

            return THIRStrSlice(
                result_type=rtype,
                receiver=recv,
                cpp_template=tpl,
                lower=_lower_slice_bound(sl.lower, lc, declared, loc),
                upper=_lower_slice_bound(sl.upper, lc, declared, loc),
                step=_lower_slice_bound(sl.step, lc, declared, loc),
                stepped=e.is_stepped_slice,
                form=form,
                loc=loc,
            )
        tup = _subscript_index_and_tuple(e, analyzer)
        if tup is not None:
            _gen_elem_ref = False
            _sub_elem_t = None
            if 0 <= tup[1] < len(tup[0].element_types):
                # The SAME derivation that decided routing eligibility
                # (`_subscript_index_and_tuple`), not a second path off
                # `declared` -- one fact, one source.
                _sub_elem_t = unwrap_readonly(unwrap_ref_type(
                    tup[0].element_types[tup[1]]))
            if isinstance(e.obj, TpyName) and e.obj.name in declared:
                # The GENERIC element read (`p[0]` on tuple[T, T]): the
                # val_or_ptr slot reads via tuple_elem_ref --
                # the TypeParamRef element arm.
                _gen_elem_ref = isinstance(_sub_elem_t, TypeParamRef)
            _own_elem_bare = (
                not _gen_elem_ref
                and isinstance(e.obj, TpyName)
                and e.obj.name in lc.storage_tuple_locals
                # An OPTIONAL element off a storage tuple is the ONE family
                # whose read is not bare std::get (it needs the
                # optional_to_ptr lift) -- unreachable through today's decl
                # arms, but the gate should enforce it, not the upstream
                # restriction.
                and not isinstance(_sub_elem_t, OptionalType))
            # A record/Own element read off a STORAGE-form tuple local
            # (`t2[1]` on `std::tuple<int32_t, Point>`): the by-value
            # member reads bare `std::get<1>(t2)` -- position-neutral
            # like the value-scalar row.
            if (not subscript_prechecked
                    and not _gen_elem_ref
                    and not _own_elem_bare
                    and _tuple_subscript_value_read(
                        e, declared, analyzer) is None
                    # A CONTAINER-element chain (`pairs[1][1]`): the
                    # borrow lvalue `std::get<N>(__getitem__(..))`.
                    and not _tuple_subscript_container_elem_read(
                        e, declared, analyzer)
                    # A MID link of a nested-storage tuple chain in
                    # RECEIVER position (`xs[0][1]` under `[1]` / `.n`):
                    # the receiver ladder resolves the chain; the final
                    # element family is gated by the outer consumer.
                    and not (use.result is _ExprResultUse.RECEIVER
                             and _subscript_recv_tuple(
                                 e, declared, analyzer) is not None)
                    # A wrapper element off a REFERENCE-element tuple
                    # binding in BORROW_BIND position (`count(p[0])` ->
                    # `count(std::get<0>(p))` -- the reference member IS
                    # the lvalue the const slot binds).
                    and not (use.result is _ExprResultUse.BORROW_BIND
                             and isinstance(e.obj, TpyName)
                             and e.obj.name in declared
                             and _wrapper_ref_tuple_return(
                                 declared.get(e.obj.name), analyzer)
                             is not None)):
                raise ThirUnsupported("subscript.tuple_shape", detail=True)
            # Tuple subscript -> `std::get<N>(t)`. Eligibility guaranteed a const index
            # and an eligible-tuple receiver; the shared helper re-derives the
            # normalized index (negatives folded). The
            # normalized offset rides a synthesized `THIRLiteral` (only its value is
            # read, for the `std::get<N>` template arg). `form` records the result
            # shape for the consumer: a value scalar is VALUE, a record element is a
            # borrow (`T*`/`T&`), and an Optional element read off a storage-tuple alias
            # is `std::optional<T>` (STORAGE, lifted to `T*` by the consuming deref_check
            # via optional_to_ptr) -- off a borrow tuple it is already `T*` (BORROW).
            _recv_t, idx = tup
            form = _subscript_result_form(e, rtype, lc)
            return THIRSubscript(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    # A container-element tuple receiver (`items[i][N]`)
                    # admits through the RECEIVER-position tuple-element row.
                    use=(_ExprUse(result=_ExprResultUse.RECEIVER)
                         if isinstance(e.obj, TpySubscript)
                         # A MIXED-own-tuple CALL receiver renders bare via
                         # the btuple-slot call admission
                         # (`std::get<1>(make_mixed(b))`). Outside
                         # `_recv_forms` for the same reason as the field
                         # slice: one fixed render this arm decides, on an
                         # axis the helper's two do not combine with.
                         else (_ExprUse(result=_ExprResultUse.VALUE,
                                        pos=SinkPos.RECEIVER, forms=_ONLY_BTUPLE_SLOT)
                               if (isinstance(e.obj, (TpyCall,
                                                      TpyMethodCall))
                                   and _renders_own_borrow_tuple(
                                       e.obj, lc.own_borrow_tuple_locals,
                                       lc.analyzer))
                               else _ExprUse())),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                index=THIRLiteral(result_type=analyzer.get_expr_type(e.index),
                                  value=idx, loc=loc),
                tuple_index=idx,
                form=form,
                elem_ref=_gen_elem_ref,
                loc=loc,
            )
        tuple_elem_recv = False
        rec_key = _record_getitem_key(
            analyzer.get_expr_type(e.obj), analyzer)
        if not subscript_prechecked:
            if rec_key is not None:
                # User-record `recv[index]` -> the record's bare operator[].
                # Value-scalar/char/enum/str/bytes/ptr results only (a
                # record-returning getitem is a borrow-form seam, not
                # lowered yet). The index renders plainly against the key
                # param (str-view/int, or a BigInt index against a BigInt
                # key param -- no narrow); a
                # runtime-BigInt index against a FIXED-int key param takes the
                # `.to_fixed_check` narrow -- excluded.
                # A ptr-repr Optional result (`b[1]` -> the getitem's bare
                # `T*`, wide pointee class) rides the passthrough decl
                # sink; the raw operator[] render is the same, only the
                # form tag differs (a borrow pointer, not a value).
                opt_ptr_ret = (use.admits(SinkForm.PTR_OPT_PASSTHROUGH)
                               and _optional_ptr_borrow_wide(rtype, analyzer)
                               is not None)
                ret_ok = (_resolved_scalar(rtype, analyzer)
                          or _eligible_char(rtype)
                          or _eligible_enum(rtype, analyzer) is not None
                          or _resolved_str_value(rtype, analyzer) is not None
                          or _resolved_bytes_value(rtype, analyzer) is not None
                          or _eligible_ptr_value(rtype, analyzer)
                          or opt_ptr_ret
                          # A BY-VALUE F1-record result bound at a
                          # BORROW_BIND sink (the protocol arg-temp init
                          # `auto __tmp_N = container[1];`): the raw
                          # operator[] prvalue. Borrow-returning getitems
                          # keep the borrow-form-seam reject.
                          or (use.result is _ExprResultUse.BORROW_BIND
                              and _record_getitem_rvalue_arg(e, analyzer))
                          # A setitem TARGET: the checked write consumes the
                          # receiver and index only, so a record element slot
                          # never renders an element read here.
                          or (use.result is _ExprResultUse.SETITEM_TARGET
                              and _f1_record(rtype, analyzer)))
                # A None-narrowed pointer-repr `Optional[record]` NAME reads
                # its deref (`(*g)[i]`), the same shape the container arm's
                # `_optrecv_deref` row renders one sink over.
                rec_optrecv = _narrowed_ptr_opt_name(e.obj, declared,
                                                     lc.pointers)
                if not (ret_ok and _record_getitem_idx_recv_ok(
                        e, declared, analyzer, lc.pointers,
                        # A pointer-slot GLOBAL receiver derefs
                        # (`(*al)[0]` -- the name arm's pointer render);
                        # plain pointer LOCALS keep the exclusion.
                        ptr_recv_ok=(isinstance(e.obj, TpyName)
                                     and e.obj.name
                                     in lc.prescan.global_slots),
                        opt_ptr_recv_ok=rec_optrecv)):
                    note_detail("subscript.record_getitem")
                    raise ThirUnsupported("subscript.record_getitem", detail=True)
                _witness("subscript.record_getitem")
                if rec_optrecv:
                    _witness("subscript.narrowed_ptr_opt_recv")
                return THIRSubscript(
                    result_type=rtype,
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        # A module-attr GLOBAL receiver reads its pointer
                        # slot (`(*environ)[k]`) -- the module-var arm's
                        # pinned-consumer admission keys on RECEIVER use.
                        use=(_ExprUse(result=_ExprResultUse.RECEIVER)
                             if _module_var_recv(e.obj, declared, analyzer)
                             else _ExprUse(
                                 pos=SinkPos.RECEIVER,
                                 forms=_recv_forms(False, rec_optrecv))),
                        field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                    # A runtime-BigInt key against a FIXED-int key param
                    # narrows here exactly as at a container read
                    # (`p[k]` -> `p[k.to_fixed_check<int64_t>()]`); the
                    # gate admits every disposition but 'reject'.
                    index=_narrow_bigint_index(
                        _lower_expr(e.index, lc, declared), e.index,
                        analyzer.get_expr_type(e.obj), analyzer, loc,
                        declared),
                    record_getitem=True,
                    form=Form.BORROW if opt_ptr_ret else Form.VALUE,
                    loc=loc)
            # A receiver that is itself a container-ELEMENT lvalue
            # (`cube[i][j]`, `a.bs[0].as_[0]`) resolves through the chained
            # resolver: the hop is rooted storage, so the outer read is an
            # ordinary element read of the hop's own container type and every
            # element-family row below reads it unchanged.
            # A None-narrowed ptr-repr Optional[container] NAME receiver reads
            # through the `(*recv)` deref (_optrecv_deref below), so the
            # resolver hands back the narrowed INNER the family and element
            # checks key on.
            recv_t = _chained_subscript_recv_type(
                e.obj, declared, analyzer, lc.pointers)
            recv_peeled = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                recv_t))) if recv_t is not None else None)
            own_recv = isinstance(recv_peeled, OwnType)
            if own_recv:
                # An `Own[list[T]]` PARAM's subscript READ renders exactly
                # the borrow shape (`::tpy::__getitem__(items, 0)` off the
                # bare name) -- the Own ABI difference is the param slot's,
                # not the read's. The name read is position-pinned bare
                # (allow_unrouted_name below, the truthiness precedent).
                recv_peeled = unwrap_readonly(recv_peeled.wrapped)
            # A NESTED-CONTAINER element (`groups["a"]` off
            # `dict[str, list[T]]`): the checked dunder yields the element's
            # `T&` lvalue. The element read is consumer-blind -- ONE
            # render, bare in every value position -- so admitting it here
            # replaces the per-sink `subscript_prechecked` bypasses (which
            # skipped the unproven-Optional receiver guard the gate applies).
            nested_container_elem = (
                _container_ref_alias_elem(recv_peeled, analyzer)
                and bool(_witness("subscript.container_elem")))
            index_ok = (
                _bigint_index_disposition(e.index, analyzer.get_expr_type(e.obj),
                                          analyzer, declared) != "reject")
            ret_ok = (
                nested_container_elem or
                _resolved_scalar(rtype, analyzer)
                or _eligible_char(rtype)
                or _eligible_enum(rtype, analyzer) is not None
                or _eligible_ptr_value(rtype, analyzer)
                or _resolved_str_value(rtype, analyzer) is not None
                or _resolved_bytes_value(rtype, analyzer) is not None
                # An `Any` value element read lands bare (`::tpy::__getitem__(
                # d, k)` -> `const Any&`), consumed by from_any / print.
                or isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(rtype))), AnyType)
                # An open-T element read (`self.data[idx]` off Array[T, N])
                # renders the bare checked dunder, landing form-neutrally in
                # its T sink.
                or _is_type_param_slot(rtype)
                # A `Callable` element read (`callbacks[0]`): the
                # `std::function` element is a value, so the checked read
                # lands bare in its own value slot.
                or _callable_value(rtype)
                # A value-repr Optional[scalar] element (`items[i]` off
                # `list[int32 | None]`) read into a WHOLE-optional consumer
                # (a value-opt decl slot): the bare `std::optional<T>` element.
                # Gated on allow_whole_optional so an arithmetic/None-test
                # consumer -- which needs the deref_optional_check unwrap --
                # never reaches this bare read.
                or (allow_whole_optional
                    and (_value_opt_scalar(rtype, analyzer) is not None
                         or _value_opt_callable(rtype, analyzer) is not None))
                # A storage-form `Optional[record]` element under the bare
                # truthy read (`if xs[0]:`): the whole `std::optional<T>`
                # element tests its own bool conversion, so the checked read
                # lands bare. TRUTHY-only -- every other consumer of that
                # element needs the optional_to_ptr lift.
                or (use.result is _ExprResultUse.TRUTHY
                    and allow_whole_optional
                    and _opt_record_inner(rtype) is not None)
                # A VALUE-repr union element (`v = d2["z"]` off
                # `dict[str, int32 | str]`): the whole `std::variant<...>`
                # element copies bare into its same-union sink -- the F4 U1
                # whole-variant read; the consuming position gates its own
                # family.
                or _eligible_value_union(rtype) is not None
                # ... and the value-TUPLE element (`pair = d[k]` off
                # `dict[str, tuple[str, str]]`): the same self-contained
                # value case -- borrow and storage coincide at the tuple
                # level, so the checked read copies bare into its sink.
                or _value_tuple(rtype, analyzer) is not None
                # A RECORD-member union element at a BORROW-lift consumer
                # (`pet = pets["rex"]` -- to_ptr_variant wraps the whole
                # variant lvalue): the read is the same checked getitem.
                or (use.result is _ExprResultUse.BORROW_BIND
                    and _eligible_ptr_union(rtype, analyzer) is not None))
            container_ok = (
                recv_t is not None
                and (_container_value_leaf_read(recv_peeled, analyzer)
                     or nested_container_elem
                     or (allow_whole_optional
                         and _container_value_optional_elem(
                             recv_t, analyzer))
                     # The storage-form Optional[record] element's container
                     # half (its ret_ok row).
                     or (use.result is _ExprResultUse.TRUTHY
                         and allow_whole_optional
                         and _container_opt_record_elem(recv_t, analyzer)
                         and bool(_witness("subscript.opt_record_truthy")))
                     # The value-union element family (its ret_ok row).
                     or (_eligible_value_union(rtype) is not None
                         and bool(_witness("subscript.value_union_elem")))
                     # ... and the value-TUPLE element family's half.
                     or (_value_tuple(rtype, analyzer) is not None
                         and bool(_witness("subscript.value_tuple_elem")))
                     # The RECORD-member union element family (the
                     # BORROW-lift ret_ok row's container half).
                     or (use.result is _ExprResultUse.BORROW_BIND
                         and _eligible_ptr_union(rtype, analyzer)
                             is not None
                         and bool(_witness("subscript.value_union_elem"))))
                and ret_ok and index_ok)
            # A container-returning CALL receiver (`x.get()[0]`,
            # `b.recv(n).split(sep)[0]`): the checked dunder interpolates
            # the inline call render -- the subscript emit is
            # receiver-shape-blind. READ-only (the setitem/del/aug gates
            # keep their name/field slice via the shared recv-type
            # resolver); the call re-validates itself recursively under
            # RECEIVER use, and the element family rides the same ret_ok.
            call_recv_ok = False
            if ((isinstance(e.obj, (TpyCall, TpyMethodCall))
                    # A @property container read is the same call shape
                    # (`c.items[0]` -> `::tpy::__getitem__(c.items(), 0)`
                    # -- the field arm delegates to the getter call).
                    or (isinstance(e.obj, TpyFieldAccess)
                        and e.obj.property_getter_call is not None))
                    and not e.needs_optional_runtime_check
                    and e.slice_function_info is None
                    and not isinstance(e.index, TpySlice)):
                _crt = analyzer.get_expr_type(e.obj)
                _crb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    _crt))) if _crt is not None else None)
                if isinstance(_crb, OwnType):
                    _crb = unwrap_readonly(_crb.wrapped)
                call_recv_ok = (
                    _crb is not None
                    and (is_list(_crb) or is_dict(_crb) or is_array(_crb)
                         or is_span(_crb))
                    and ret_ok and index_ok
                    and bool(_witness("subscript.call_recv")))
            # A container TERNARY receiver (`(a if c else b)[0]`): the same
            # receiver-shape-blind interpolation the call row relies on -- the
            # checked dunder takes the ternary render whole. Restricted to the
            # lvalue (bare-NAME) arm pair so the `?:` stays an lvalue and the
            # element read aliases the chosen container.
            ternary_recv_ok = (
                _lvalue_container_ternary(e.obj, analyzer)
                and not e.needs_optional_runtime_check
                and e.slice_function_info is None
                and not isinstance(e.index, TpySlice)
                and ret_ok and index_ok
                and bool(_witness("subscript.ternary_recv")))
            str_ok = (
                _str_slice_receiver_supported(e.obj, lc, declared)
                and _eligible_char(rtype) and index_ok)
            recv = e.obj
            bytes_recv_ok = False
            if isinstance(recv, TpyName):
                bytes_recv_ok = (
                    recv.name in declared
                    and (_resolved_bytes_value(
                             declared[recv.name], analyzer) is not None
                         # The bytes twin of the narrowed Optional[view]
                         # receiver: `b[0]` on a None-narrowed
                         # `bytes | None` reads `(*b)` (sema-implied
                         # proof, like the str branch).
                         or (_value_opt_view_binding(recv.name, lc)
                             and _value_opt_bytes(declared[recv.name],
                                                  analyzer) is not None)))
            elif isinstance(recv, TpyFieldAccess):
                bytes_recv_ok = (
                    _field_receiver_ok(recv, declared, analyzer)
                    and _resolved_bytes_value(
                        analyzer.get_expr_type(recv), analyzer) is not None)
            elif isinstance(recv, TpySubscript):
                # `app[0][0]` -- a byte read off a container-ELEMENT bytes
                # receiver: the inner checked read (`::tpy::__getitem__(app,
                # 0)`) interpolates into the bytes_getitem render. One nested
                # step, mirroring the container-in-container nested_ok row;
                # plain reads only.
                bytes_recv_ok = (
                    not recv.needs_optional_runtime_check
                    and recv.slice_function_info is None
                    and not isinstance(recv.index, TpySlice)
                    and _subscript_container_recv_type(
                        recv.obj, declared, analyzer) is not None
                    and _resolved_bytes_value(
                        analyzer.get_expr_type(recv), analyzer) is not None)
            bytes_ok = (
                bytes_recv_ok and _eligible_scalar(rtype) and index_ok)
            # A `bytearray` receiver spells the SAME @native free-function
            # dunder as bytes (`::tpy::bytes_getitem(b, i)`) -- only the WRITE
            # side has its own bytearray_* natives. Name / clean-field
            # receivers, the bytes rows' slice; `recv_peeled` already carries
            # the None-narrowed pointer-repr unwrap (`(*b)[i]`).
            bytearray_ok = (
                isinstance(e.obj, (TpyName, TpyFieldAccess))
                and not own_recv
                and recv_peeled is not None
                and is_bytearray_type(recv_peeled)
                and _eligible_scalar(rtype) and index_ok
                and bool(_witness("subscript.bytearray_recv")))
            # `m[i][j]`: the receiver `m[i]` is a nested-container borrow lvalue
            # (a subscript the one-level recv-type resolver rejects), indexed
            # again -> nested `__getitem__`. Emit lowers it prechecked below.
            # The same nest off a None-narrowed ptr-repr `Optional[container]`
            # name (`rows[-1][-1]`) rides this gate too: the chained receiver
            # resolver hands back the narrowed inner.
            nested_ok = (
                _subscript_over_container_subscript_ok(e, declared, analyzer,
                                                       lc.pointers)
                and ret_ok and index_ok)
            # An F1-record element read in a RECEIVER position (a member-access
            # receiver, or the inner of a `&c[i]` address-of coercion): the
            # `::tpy::__getitem__(c, i)` `T&` lvalue, rendered by the record-elem
            # emit below (form BORROW). Only under RECEIVER -- a value position
            # would copy the borrow.
            record_recv_ok = (
                use.result is _ExprResultUse.RECEIVER
                and _container_record_elem(recv_t, analyzer)
                and _f1_record(rtype, analyzer) and index_ok)
            # A whole TUPLE element read in RECEIVER position (the inner of
            # `items[i][N].field`): the `::tpy::__getitem__(items, i)`
            # storage-tuple `T&` lvalue, consumed by the outer `std::get`.
            # Eligibility reads the container's DECLARED element tuple (the
            # subscript's analyzer type can carry unresolved literal
            # members).
            if (use.result is _ExprResultUse.RECEIVER and index_ok
                    and recv_t is not None):
                _tr_recv_b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    recv_t)))
                if is_dict(_tr_recv_b):
                    # A dict SUBSCRIPT yields the VALUE type -- iteration
                    # (get_iterable_element_type) would yield the key.
                    _tr_args = getattr(_tr_recv_b, "type_args", None)
                    _tr_et = (_tr_args[1]
                              if _tr_args and len(_tr_args) > 1 else None)
                else:
                    _tr_et = get_iterable_element_type(
                        _tr_recv_b, analyzer.registry)
                _tr_eb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    _tr_et))) if isinstance(_tr_et, TpyType) else None)

                def _tr_member_ok(m: 'TpyType') -> bool:
                    mm = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(m)))
                    mm = resolve_pending_container(mm, analyzer) or mm
                    return (_resolved_scalar(mm, analyzer)
                            or _owned_str_slot(mm, analyzer)
                            # Any reference-form member: the whole-element
                            # `T&` read is member-agnostic, so what the
                            # member IS never reaches the render.
                            or _f1_ref(mm, analyzer)
                            # A NESTED tuple member: same argument, and a
                            # tuple is not on the axis.
                            or isinstance(mm, TupleType))
                tuple_elem_recv = (
                    isinstance(_tr_eb, TupleType)
                    and (_value_tuple(_tr_eb, analyzer) is not None
                         or _f1_tuple(_tr_eb, analyzer) is not None
                         # A CONTAINER-member storage tuple
                         # (`list[tuple[int, list[T]]]`, the jagged shape):
                         # the whole-element `T&` read is member-agnostic --
                         # the outer std::get consumer picks the member.
                         or all(_tr_member_ok(m)
                                for m in _tr_eb.element_types)))
            # A bare protocol-typed NAME receiver (a Sequence-family
            # template param) subscripts through the SAME checked dunder
            # (`::tpy::__getitem__(s, i)` -- get_type_method_fi's protocol
            # fallback table); a protocol carrying its OWN template-bearing
            # __getitem__ keeps rejecting (a different render).
            proto_ok = (
                _protocol_subscript_recv(e.obj, declared, analyzer)
                and ret_ok and index_ok
                and bool(_witness("subscript.protocol_recv")))
            # A `*args` varargs view receiver: a range-PROVEN index takes
            # the bounds-safe `args[static_cast<std::size_t>(i)]` render, an
            # unproven one the checked `::tpy::__getitem__(args, i)`. The
            # emit already picks between them off `bounds_safe`, exactly as
            # for a container receiver, so the gate does not need to.
            varargs_ok = (
                recv_t is not None
                and is_varargs(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(recv_t))))
                and ret_ok and index_ok
                and bool(_witness("subscript.varargs_recv")))
            # An F1-record ELEMENT read consumed as a borrow (the record
            # ref-slot arg row: `add_a(::tpy::__getitem__(a.bs, 0), ..)`):
            # the checked element lvalue renders bare -- BORROW_BIND-only,
            # so value-position record elements keep rejecting.
            record_elem_ok = (
                (use.result is _ExprResultUse.BORROW_BIND
                 # ... and the record FIELD-WRITE copy sink, which consumes
                 # the element read whole (`h.p = ::tpy::__getitem__(pts,
                 # 0);` -- the assign copies the reference read).
                 or use.admits(SinkForm.RECORD_COPY)
                 # ... and the ITERABLE capture (`for x in items[0]:` --
                 # the `auto& __src_N =` bind takes the element lvalue).
                 or use.result is _ExprResultUse.ITERABLE
                 # ... and the DISCARD statement (`items[0];` -- the `_`
                 # unpack half evaluated for its bounds check; the element
                 # lvalue is dropped, no alias escapes).
                 or use.result is _ExprResultUse.DISCARD)
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled)
                     # A dict VALUE element is the same checked
                     # `__getitem__` lvalue (`print(d[k])`).
                     or is_dict(recv_peeled))
                and (_f1_record(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(rtype))), analyzer)
                     # A WRAPPER-union element (`depth(zs[1])` on
                     # `zs: list[Tree]`): the same checked element lvalue
                     # binding a same-wrapper borrow slot bare.
                     or _eligible_wrapper_union(rtype, analyzer) is not None)
                and index_ok
                and bool(_witness("subscript.record_elem_borrow")))
            # A VALUE-record element read copied into a by-value slot
            # (`w = self._waiters[fd]` at `dict[int32, Waker]`): a ValueType
            # record has no borrow form, so the checked element lvalue
            # copies straight into the storage slot -- unlike the reference
            # records the BORROW_BIND row above aliases.
            _vr_rb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
            value_record_elem_ok = (
                use.result is _ExprResultUse.STORAGE
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled)
                     or is_dict(recv_peeled))
                and isinstance(_vr_rb, NominalType) and _vr_rb.is_record
                and _vr_rb.is_value_type()
                and _f1_record(_vr_rb, analyzer)
                and index_ok
                and bool(_witness("subscript.value_record_elem")))
            # A VALUE-tuple element read consumed whole by the standalone
            # unpack capture (`a, b = addrs[0]` -> `auto __tup_N =
            # ::tpy::__getitem__(addrs, 0);`): the element is a
            # self-contained `std::tuple<...>`, so the checked read renders
            # bare. Scoped to the tuple-source position -- a value-position
            # tuple element keeps its own rows.
            _ts_rb = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
            tuple_elem_src_ok = (
                use.admits(SinkForm.TUPLE_SOURCE)
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled)
                     or is_dict(recv_peeled))
                and (_value_tuple(_ts_rb, analyzer) is not None
                     # A ptr-Optional-element STORAGE tuple element reads
                     # the same bare `__getitem__` (self-contained
                     # `std::tuple<std::optional<P>, ..>` value) -- the
                     # whole-element pass into a same-tuple Own slot.
                     or (isinstance(_ts_rb, TupleType)
                         and _ts_rb.has_pointer_repr_element()
                         and (_tuple_elem_slots_ptr_optional(_ts_rb)
                              # The plain-record F3 sibling (`items[0]` at
                              # `list[tuple[int32, P]]`): the same bare
                              # self-contained element value.
                              or _f1_tuple(_ts_rb, analyzer) is not None))
                     # ... and the OPEN-T flavor inside a generic body
                     # (`ranked[i]` at `list[tuple[T, int]]`): the generic
                     # element has no pointer repr either, so the element is
                     # the same self-contained storage value.
                     or (_open_t_tuple_slot(_ts_rb, analyzer) is not None
                         and _witness("subscript.open_t_tuple_source")))
                and index_ok
                and bool(_witness("subscript.value_tuple_source")))
            # A BORROW-form tuple element read feeding the arg wrap
            # (`consume(items[0])`): the checked read renders bare and the
            # caller's `tuple_to_pointer` supplies the storage->borrow lift,
            # so nothing here depends on the element's own form. BORROW_BIND
            # only -- a value-position read of the same element keeps its
            # own rows.
            tuple_elem_borrow_ok = (
                use.result is _ExprResultUse.BORROW_BIND
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled)
                     or is_dict(recv_peeled))
                and (_f1_tuple(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(rtype))), lc.analyzer) is not None
                     # A VALUE-tuple element read consumed whole by the
                     # borrow-bind position (`TuplePrinter(__getitem__(
                     # pairs, 0))`): the same bare element lvalue.
                     or _value_tuple_nested(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(rtype))), lc.analyzer) is not None
                     # ... and the OPEN flavor inside a generic body
                     # (`copy(src[0])` on `list[tuple[T, int]]`): the
                     # element is still a self-contained tuple value, and
                     # its slot spelling stays open until T binds.
                     or (_open_value_tuple(rtype) is not None
                         and _witness("subscript.open_tuple_elem")))
                and index_ok
                and bool(_witness("subscript.borrow_tuple_elem")))

            if not (container_ok or call_recv_ok or ternary_recv_ok
                    or str_ok or bytes_ok
                    or bytearray_ok
                    or nested_ok
                    or tuple_elem_src_ok or tuple_elem_borrow_ok
                    or record_recv_ok or tuple_elem_recv or proto_ok
                    or varargs_ok or record_elem_ok
                    or value_record_elem_ok):
                if recv_t is None:
                    detail = "subscript." + _subscript_recv_reject(
                        e.obj, declared, analyzer)
                elif not index_ok:
                    detail = "subscript.index"
                else:
                    bare_recv = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(recv_t)))
                    if isinstance(bare_recv, TupleType):
                        detail = "subscript.tuple_shape"
                    elif _resolved_viewfam_value(
                            bare_recv, analyzer) is not None:
                        detail = "subscript.viewfam_shape"
                    elif (is_list(bare_recv) or is_array(bare_recv)
                          or is_span(bare_recv) or is_dict(bare_recv)):
                        detail = "subscript." + _subscript_elem_reject(
                            bare_recv, analyzer)
                    else:
                        detail = "subscript.recv_type"
                raise ThirUnsupported(detail, detail=True)
        if rec_key is not None:
            # A PRECHECKED record-getitem receiver (the field-receiver ladder
            # admitted `points[0].x` -- an F1-record-returning operator[]
            # consumed by one member access): the record construction, never
            # the container dunder. BORROW -- the `T&` operator[] lvalue.
            _witness("subscript.record_getitem")
            return THIRSubscript(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    # A pointer-slot LOCAL receiver derefs at the name read
                    # (`(*acc)[0].name` -- the rebind-slot read).
                    use=_ExprUse(
                        pos=SinkPos.RECEIVER,
                        forms=_recv_forms(
                            False,
                            isinstance(e.obj, TpyName)
                            and e.obj.name in lc.pointers)),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                index=_lower_expr(e.index, lc, declared),
                record_getitem=True,
                form=(Form.BORROW if _f1_record(rtype, analyzer)
                      else Form.VALUE),
                loc=loc)
        # Container or str subscript -> the checked dunder
        # `::tpy::__getitem__(c, i)` (str's __getitem__ @cpp_template spells the
        # same) or, when sema proved the index in-bounds,
        # `c[static_cast<std::size_t>(i)]` (a literal index needs no cast). The
        # index is a value-scalar expr (a runtime-BigInt one takes the
        # `.to_fixed_check<int32_t>()` narrow, inside the bounds-safe
        # static_cast when both fire) or, for an
        # owned-str-keyed dict, a str-slice expr rendered bare in the key slot.
        # `form` is VALUE for a scalar / char element; a str element/value read
        # (S5) carries its resolved shape -- BORROW when the read's view var
        # resolved `StrView` (a view source, driving the owned-sink
        # `std::string(x)` copy), STORAGE when it resolved owned (the
        # `const std::string&` element lands in owned sinks via the implicit
        # copy ctor, bare).
        sub_str = _resolved_str_value(rtype, analyzer)
        if sub_str is None:
            # An owned-BYTES element read (list[bytes]) is an owned lvalue:
            # STORAGE via the shared form map, so owned decl/return sinks
            # land it bare (implicit copy), never the S6 `Bytes(x)` wrap.
            sub_str = _resolved_bytes_value(rtype, analyzer)
            if sub_str is not None:
                _witness("subscript.bytes_elem")
        form = _viewfam_result_form(sub_str)
        if _f1_record(rtype, analyzer):
            # A record element (`ps[i]`) is a `T&` borrow, consumed by field
            # access / the REF_ALIAS alias bind (mirrors the tuple record
            # element's BORROW tag).
            form = Form.BORROW
            _witness("subscript.record_elem")
        elif tuple_elem_recv:
            # The storage-tuple element lvalue: elements held by value, so
            # the consuming get reads `.`-style off it.
            form = Form.STORAGE
            _witness("subscript.tuple_elem_recv")
        if isinstance(e.obj, TpyFieldAccess):
            _witness("subscript.field_recv")
            if _resolved_bytes_value(analyzer.get_expr_type(e.obj),
                                     analyzer) is not None:
                _witness("subscript.bytes_field")
        # A narrowed union member's container alias is an ordinary container
        # lvalue, so the ELEMENT's own form rules hold: only a reference-typed
        # element is the `T&` borrow the REF_ALIAS bind wants. Tagging a
        # value element BORROW here is a form lie the return validator trips
        # on (`return xs[0]` at a value-typed return).
        narrowed_recv_u = (lc.narrow.subject_union.get(e.obj.name)
                           if isinstance(e.obj, TpyName)
                           and e.obj.name in lc.narrow.narrowed else None)
        idx_obj_type = analyzer.get_expr_type(e.obj)
        if narrowed_recv_u is not None and _f1_ref(rtype, analyzer) and (
                is_dict(idx_obj_type) or is_list(idx_obj_type)
                or is_array(idx_obj_type) or is_span(idx_obj_type)):
            form = Form.BORROW
            _witness("subscript.ru_narrowed_recv")
        _optrecv_deref = _narrowed_ptr_opt_name(e.obj, declared, lc.pointers)
        if _optrecv_deref:
            _witness("subscript.narrowed_ptr_opt_recv")
        _sub_mv = _module_var_access_pair(e.obj, declared, analyzer)
        if _sub_mv is not None:
            # A module-variable container receiver (`sys.argv[1]`): the
            # element template is a PINNED consumer of the `(*slot)` read,
            # like the slice template and the native-slot arg, so the
            # pointer-slot deref is admitted here
            # (`__getitem__((*::tpystd::sys::argv), 1)`). Both spellings of
            # the read -- dotted and bare-module -- reach it through the
            # shared recognizer.
            _sub_recv = _lower_module_var(
                e.obj, analyzer.get_expr_type(e.obj), lc, *_sub_mv,
                loc=loc, allow_ref_pointer=True)
        else:
            _sub_recv = _lower_expr(
                e.obj, lc, declared,
                # A CALL receiver's borrow-container result binds like a
                # postfix-member receiver (`x.get()[0]` -- the `T&` return
                # consumed in place); a @property receiver IS that call
                # one shape over. Names/plain fields keep the default use.
                # A None-narrowed ptr-repr Optional[container] NAME
                # receiver reads its deref (`(*d)` -- indirect_read).
                use=(_ExprUse(result=_ExprResultUse.RECEIVER)
                     if (isinstance(e.obj, (TpyCall, TpyMethodCall))
                         or (isinstance(e.obj, TpyFieldAccess)
                             and e.obj.property_getter_call is not None))
                     else _ExprUse(
                         pos=SinkPos.RECEIVER,
                         forms=_recv_forms(False, _optrecv_deref))),
                field_prechecked=isinstance(e.obj, TpyFieldAccess),
                subscript_prechecked=isinstance(e.obj, TpySubscript),
                allow_unrouted_name=own_recv)
        return THIRSubscript(
            result_type=rtype,
            receiver=_sub_recv,
            # The INDEX inherits the enclosing position's flushability: a
            # statement-flushed subscript (a setitem write target) lets an
            # index-call's arg temps hoist before the statement (the
            # `std::variant<..> __tmp_N = ..;` decl + bare call), like
            # any statement-position arg.
            # A BYTES-VIEW-keyed receiver threads view_key_target into the
            # key literal (`__getitem__(d, ::tpy::bytes_literal("alice", 5))`
            # -- the static view spelling; setitem lowers this same node).
            # Str-view keys need no retag (str literals render bare).
            index=_retag_bytes_literal_view(
                _narrow_bigint_index(
                    _lower_expr(e.index, lc, declared,
                                use=_ExprUse(allow_temps=use.allow_temps),
                                # A view-family FIELD key renders the bare
                                # member read; the key slot binds a view of
                                # it for the lookup, so no wrap is owed.
                                field_owned_str_ok=isinstance(
                                    e.index, TpyFieldAccess)),
                    e.index, idx_obj_type, analyzer, loc, declared),
                (_ivk if (_ivk := view_key_target(unwrap_readonly(
                     unwrap_ref_type(unwrap_send_sync(idx_obj_type)))))
                 is not None and is_bytes_view_type(_ivk) else None)),
            bounds_safe=e.bounds_safe,
            record_getitem=False,
            form=form,
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        if isinstance(e, TpyIntLiteral):
            return _lower_int_literal(e.value, rtype, lc, loc)
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyStrLiteral):
        # const char[N] via cpp_string_literal_expr; VALUE form -- implicitly
        # convertible to both string_view and string slots, never wrapped.
        return THIRStrLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBytesLiteral):
        # Default owned render (bytes_literal_owned / empty vector) -- the
        # target-less positions (print/compare). View-targeted sinks (view
        # decl-init/reassign, bytes/BytesView call args) rewrite the flag at
        # their own lowering sites (_retag_bytes_literal_view / _lower_call_arg).
        return THIRBytesLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyFString):
        parts: list[str | THIRFStringArg] = []
        for part in e.parts:
            if isinstance(part, str):
                parts.append(part)
            else:
                if part.conversion not in (
                        FSTRING_CONV_NONE, FSTRING_CONV_STR,
                        FSTRING_CONV_REPR):
                    note_detail("fstring.conversion")
                    raise ThirUnsupported("expr.fstring")
                wrap = _fstring_arg_wrap(part.expr, analyzer, part.conversion,
                                         part.format_spec is not None,
                                         declared)
                if wrap is _FSTRING_INELIGIBLE:
                    note_detail("fstring.arg_wrap")
                    raise ThirUnsupported("expr.fstring")
                if part.format_spec is not None:
                    _witness("fstr.spec")
                # A container-returning CALL under the to_str wrap takes the
                # ITERABLE result use: the wrap consumes the bare call render
                # inline, exactly like the for-head capture.
                part_use = _ExprUse(
                    pos=(SinkPos.FSTRING_INTERP
                         if isinstance(part.expr, TpyFieldAccess)
                         else SinkPos.UNSPECIFIED))
                if _fstring_container_call_arg(part.expr, analyzer):
                    _witness("fstr.container_call_arg")
                    part_use = replace(part_use,
                                       result=_ExprResultUse.ITERABLE)
                try:
                    lowered_part = _lower_expr(
                        part.expr, lc, declared, use=part_use,
                        field_owned_str_ok=isinstance(
                            part.expr, TpyFieldAccess))
                except ThirUnsupported as ex:
                    # The landmark names the interpolation position; the
                    # operand's own reason rides it, or the tag hides which
                    # construct actually blocked.
                    raise ex.with_context(
                        f"expr.fstring:{ex.reason}") from None
                parts.append(THIRFStringArg(
                    expr=lowered_part, wrap=wrap,
                    format_spec=part.format_spec))
        # An owned std::string result: STORAGE form, so it lands bare in owned
        # sinks (no view->owned wrap), like an owned-str call result.
        return THIRFString(result_type=rtype, parts=tuple(parts),
                           form=Form.STORAGE, loc=loc)
    if isinstance(e, TpyBinOp):
        return _lower_binop(e, rtype, lc, declared, loc,
                            fold_ok=use.admits(SinkForm.LITERAL_FOLD),
                            slot_threaded=use.slot_target is not None,
                            temps_ok=use.allow_temps)
    if isinstance(e, TpyUnaryOp):
        # A negated int literal folds to a plain literal (the negated value
        # renders directly); otherwise only logical `not` is admitted (bool
        # operand).
        neg = _folded_neg_int_literal(e, analyzer)
        if neg is not None:
            return _lower_int_literal(neg, rtype, lc, loc)
        # IntEnum negation: `(-static_cast<U>(p))`.
        enum_neg = _enum_neg_wrap(e, analyzer)
        if enum_neg is None and e.op != "!":
            # A resolved arithmetic dunder (`- + ~` on int/float/fixed-int):
            # expand its cpp_template over the operand. A value-Optional
            # operand needs the narrow-or-runtime-check path
            # (unary_target=inner + warning): only the unwrap below spells
            # it, anything else rejects as `expr.unary`.
            resolved = e.resolved_unaryop
            operand_type = analyzer.get_expr_type(e.operand)
            if resolved is not None and resolved.method.cpp_template:
                # An UNPROVEN value-opt scalar operand unwraps through the
                # runtime-checked `deref_optional_check(x)` (the
                # narrow-or-runtime-check path); a narrowed / non-optional
                # operand lowers plainly.
                operand = _lower_unproven_opt_scalar(e.operand, lc, declared)
                if operand is None and isinstance(operand_type, OptionalType):
                    raise ThirUnsupported("expr.unary")
                if operand is None:
                    operand = _lower_expr(
                        e.operand, lc, declared,
                        # A record-rvalue operand (`-timedelta(...)` -- the
                        # __neg__ template over the ctor prvalue) rides
                        # BORROW_BIND like the binop operand twin.
                        use=(_ExprUse(result=_ExprResultUse.BORROW_BIND)
                             if _record_call_rvalue_operand(
                                 e.operand, analyzer)
                             else _ExprUse()))
                return THIRUnaryArith(
                    result_type=rtype,
                    cpp_template=resolved.method.cpp_template,
                    operand=operand,
                    loc=loc)
            raise ThirUnsupported("expr.unary")
        if enum_neg is not None:
            _witness("enum.neg")
            return THIREnumWrap(result_type=rtype, wrap=enum_neg,
                                operand=_lower_expr(e.operand, lc, declared), loc=loc)
        # `not`: an enum operand takes its truthiness wrap under `(!(...))`;
        # bool / Optional-ptr operands lower bare (their truthiness render is
        # their value render).
        return THIRUnaryNot(result_type=rtype,
                            operand=_lower_truthy(
                                e.operand, lc, declared,
                                unary_operand=True),
                            loc=loc)
    if isinstance(e, TpyChainedCompare):
        if e.pairs is None:
            raise ThirUnsupported("expr.chained_compare")
        assert e.pairs is not None
        if all(emit_prims.is_duplicable_expr(c)
               for c in e.comparators[:-1]):
            # The inline arm: left-fold the sema pairs with the bare &&
            # (resolved None), spelling `((a < b) && (b < c))`.
            # Each pair is a full TpyBinOp (sema-analyzed), so it lowers like any
            # comparison. Pairs past the first evaluate CONDITIONALLY (the
            # fold short-circuits): the eager-only right, like a logical RHS.
            folded = _lower_expr(e.pairs[0], lc, declared)
            _pair_use = _ExprUse(allow_temps=use.allow_temps)
            for pair in e.pairs[1:]:
                folded = THIRBinOp(result_type=rtype, left=folded, op="&&",
                                   right=_lower_expr(pair, lc, declared,
                                                     use=_pair_use,
                                                     cond_eager=use.allow_temps),
                                   resolved=None, loc=loc)
            return folded
        return _lower_chained_compare_stmtexpr(e, rtype, lc, declared, loc,
                                               temps_ok=use.allow_temps)
    if isinstance(e, TpyNamedExpr):
        # The walrus target-class ladder. Lowered classes: value
        # scalars (`(n = v)`), pointer-repr Optional targets
        # (`T* n = nullptr;` + the borrow-lifted assign), borrow-alias
        # pointer targets (`(n = &(v), *n)`). The remaining classes
        # (tuples, non-value optional slots, hoisted names) reject as
        # `expr.walrus` -- their renders carry registry side effects this
        # lowering does not model. `walrus_predeclared` is FUNCTION-scoped
        # (not branch-restored), so a sibling-branch re-bind assigns in
        # place instead of re-declaring.
        vt = (resolve_int_literals(rtype, analyzer.ctx.default_int_for_literal)
              if rtype is not None else None)
        vtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
               if vt is not None else None)
        if vtu is None:
            raise ThirUnsupported("expr.walrus")
        if (e.target in lc.rebind_slot_locals
                and e.target in declared
                and isinstance(vtu, TupleType)
                and vtu.has_pointer_repr_element()
                and _btuple_owning_call_init(e.value, analyzer)
                and not (lc.func.is_generator or lc.func.is_async)):
            # A HOISTED borrow-tuple walrus with an owning-call value
            # (`(t := make_pair(9))[0]`): the emplace-into-slot render with
            # the bare-name tail (`(t = ::tpy::tuple_to_pointer<{borrow}>(
            # __slot_N.emplace(...)), t)`); the if-head hoist pre-declared
            # both the local and the slot.
            lc.ensure_borrow_tuple_const()
            bc = (vtu.to_cpp_return_const()
                  if e.target in lc.const_borrow_tuple_locals
                  else vtu.to_cpp_return())
            wv = _lower_expr(e.value, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.STORAGE,
                                          allow_temps=True,
                                          pos=SinkPos.WALRUS_TARGET))
            _witness("expr.walrus_btuple_emplace")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=wv, slot_cpp=vtu.to_cpp(), borrow_cpp=bc,
                tail="name", loc=loc)
        if e.target in lc.prescan.hoisted:
            raise ThirUnsupported("expr.walrus")
        need_predecl = (e.target not in lc.walrus_predeclared
                        and e.target not in declared)
        borrow_decls = analyzer.function_stmt_borrow_decls.get(
            lc.func, {})
        ever_owned = analyzer.function_ever_owned_locals.get(
            lc.func, set())
        # The pointer/slot/viewfam rungs are SYNC-only: a resumable body's
        # locals are frame fields (`(*b)` source reads, no named-row drain
        # for the predecls), a different render model -- only the
        # value-scalar tail below stays available there.
        resumable = lc.func.is_generator or lc.func.is_async
        if e.target in lc.frame_local_types:
            # A RESUMABLE frame local (the map is empty for sync bodies):
            # the target is a struct FIELD, so every rung
            # below is wrong for it -- their pre-declaration would put a
            # case-block local in front of the field and the write would die
            # at the next suspension.
            return _lower_frame_walrus(e, vtu, lc, declared, loc)
        if (not resumable
                and e.target in borrow_decls and e.target not in ever_owned
                and is_plain_nonvalue(vtu)):
            # Borrow-alias pointer target (`(q := b)` on a record/container
            # lvalue, `(v := h.view())` on a ref-returning call):
            # `[const ]T* q = nullptr;` + `(q = &(v), *q)`; an
            # already-pointer NAME source assigns bare -- a slot GLOBAL is one
            # of those (its `T* g{}` slot is seeded into lc.pointers), so it
            # copies the slot pointer instead of addressing it.
            src = e.value
            if not _f1_ref(vtu, analyzer):
                note_detail("walrus.borrow_alias_shape")
                raise ThirUnsupported("expr.walrus")
            if isinstance(src, TpyName):
                if src.name not in declared:
                    note_detail("walrus.borrow_alias_shape")
                    raise ThirUnsupported("expr.walrus")
                lowered_value = _lower_expr(src, lc, declared)
                already_ptr = src.name in lc.pointers
            elif (isinstance(src, (TpyCall, TpyMethodCall))
                  and call_returns_cpp_ref(analyzer,
                                           src.resolved_function_info)):
                # A C++-ref-returning call source: the alias addresses the
                # callee's referent (`&(h.view())`); the call's own
                # lowering validates receiver/args.
                lowered_value = _lower_expr(
                    src, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                already_ptr = False
            elif _field_receiver_ok(src, declared, analyzer):
                # A record / container FIELD source (`(q := h.inner)`): the
                # alias addresses the member lvalue, the same storage-form
                # read a borrow-local decl binds by reference.
                lowered_value = _lower_field_source(src, lc, declared)
                already_ptr = False
                _witness("walrus.alias_field_src")
            else:
                note_detail("walrus.borrow_alias_shape")
                raise ThirUnsupported("expr.walrus")
            if isinstance(lowered_value, THIRName) and lowered_value.deref:
                # The comma form consumes the raw pointer; the value-read
                # deref belongs to the `*n` tail, not the source.
                lowered_value = replace(lowered_value, deref=False)
            # A const-rooted source makes the alias `const T*` whatever sema's
            # borrow-decl verdict says: that verdict is keyed on the target's
            # USES and never sees the source's indirect const.
            is_const = (borrow_decls[e.target]
                        or _walrus_src_is_const(src, lc))
            declared[e.target] = vtu
            lc.pointers.add(e.target)
            if is_const:
                lc.const_locals.add(e.target)
            cpp_type = None
            if need_predecl:
                cpp_type = (("const " if is_const else "")
                            + lc.render_type(vtu) + "*")
                lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_ptr_alias")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, cpp_type=cpp_type,
                init="nullptr" if cpp_type is not None else None,
                tail="deref", addr_of=not already_ptr, loc=loc)
        opt_ptr = (None if resumable
                   else _optional_ptr_borrow(vtu, analyzer))
        if opt_ptr is not None:
            # Pointer-repr Optional target (`(t := h.opt)`): `[const ]T* t =
            # nullptr;` + the borrow-lifted assign -- a storage-Optional
            # FIELD or container-ELEMENT source lifts via optional_to_ptr
            # (THIRFormConvert BORROW), `None` assigns nullptr, an
            # already-pointer NAME passes bare. Other sources reject.
            lowered_value = _walrus_opt_ptr_source(
                e.value, vtu, opt_ptr.inner, lc, declared, loc,
                allow_none=True)
            if lowered_value is None:
                note_detail("walrus.opt_ptr_src")
                raise ThirUnsupported("expr.walrus")
            declared[e.target] = vtu
            lc.pointers.add(e.target)
            # A const-rooted source lifts to `const T*`; sema's borrow-decl
            # verdict is keyed on the target's USES and does not see it.
            opt_const = _walrus_src_is_const(e.value, lc)
            if opt_const:
                lc.const_locals.add(e.target)
            cpp_type = None
            if need_predecl:
                cpp_type = (("const " if opt_const else "")
                            + lc.render_type(unwrap_readonly(opt_ptr.inner))
                            + "*")
                lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_opt_ptr")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, cpp_type=cpp_type,
                init="nullptr" if cpp_type is not None else None,
                loc=loc)
        if (not resumable and isinstance(vtu, TupleType)
                and e.target in lc.prescan.reassigned):
            # REASSIGNED borrow-tuple walrus (`(t := make_pair(9))` later
            # rebound to a storage alias): per-element-Own collapses to the
            # unified borrow type; the borrow local pre-declares and the
            # rvalue-call value materializes into the owning `__slot_N`
            # (`(t = tuple_to_pointer<..>(__slot_N.emplace(v)), t)`).
            # Non-call sources and hoisted/const targets reject.
            cvt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                collapse_tuple_own_elements(vtu))))
            if not (isinstance(cvt, TupleType)
                    and cvt.has_pointer_repr_element()
                    and need_predecl
                    and isinstance(e.value, (TpyCall, TpyMethodCall))
                    and not call_returns_cpp_ref(
                        analyzer, e.value.resolved_function_info)
                    and not contains_pending_leaf(cvt)):
                note_detail("walrus.btuple_reassign_shape")
                raise ThirUnsupported("expr.walrus")
            # A fixpoint-const name (a const field source at some OTHER
            # binding) would need the const borrow spelling this arm does
            # not render -- reject rather than spell the mutable form.
            lc.ensure_borrow_tuple_const()
            if e.target in lc.const_borrow_tuple_locals:
                note_detail("walrus.btuple_const")
                raise ThirUnsupported("expr.walrus")
            # The owning `__slot_N.emplace(...)` consumes the call result
            # whole, exactly like the tuple-unpack capture.
            lowered_value = _lower_expr(
                e.value, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             pos=SinkPos.WALRUS_TARGET))
            declared[e.target] = cvt
            lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_btuple_slot")
            return THIRWalrus(
                result_type=cvt, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value,
                cpp_type=cvt.to_cpp_return(),
                slot_cpp=cvt.to_cpp(), borrow_cpp=cvt.to_cpp_return(),
                tail="name", loc=loc)
        # An owned-tuple return off a CALL (`(t := make_pair(5))` at a
        # declared `Own[tuple[..]]` / owned-movable return) takes the
        # deferred-init OPTIONAL slot, not the borrow predecl.
        wal_owned_tuple = (_owned_tuple_call_source(e.value, analyzer)
                           and not contains_pending_leaf(vtu))
        if (not resumable
                and isinstance(vtu, TupleType)
                and vtu.has_pointer_repr_element()
                and e.target not in lc.prescan.reassigned
                and need_predecl
                and not wal_owned_tuple):
            # Non-reassigned borrow-tuple walrus (`(t := (1, b))` /
            # `(t := items[0])` / `(p := make_mixed(b))`): predecl
            # `std::tuple<..., T*> t;` + the plain assign -- a literal
            # renders borrow-form, a storage lvalue subscript lifts via
            # tuple_to_pointer (the btuple reseat arm's value split), and a
            # MIXED own+borrow CALL already IS the local's shape, so it
            # binds directly. Reassigned (rebind-slot) walruses and OWNING
            # tuple-call sources reject.
            if contains_pending_leaf(vtu) or any(
                    isinstance(t_, (PendingViewType, IntLiteralType,
                                    FloatLiteralType))
                    for t_ in vtu.inner_types()):
                note_detail("walrus.btuple_pending")
                raise ThirUnsupported("expr.walrus")
            if isinstance(e.value, TpyTupleLiteral):
                lowered_value: THIRExpr = _lower_borrow_tuple_literal(
                    e.value, vtu, lc, declared)
            elif (isinstance(e.value, TpySubscript)
                  and not isinstance(e.value.index, TpySlice)
                  and e.value.slice_function_info is None
                  and not e.value.needs_optional_runtime_check
                  and _subscript_container_recv_type(
                      e.value.obj, declared, analyzer) is not None):
                lowered_value = THIRFormConvert(
                    result_type=vtu,
                    value=_lower_expr(e.value, lc, declared,
                                      subscript_prechecked=True),
                    form=Form.BORROW, loc=loc)
            elif _mixed_own_btuple_call(e.value, analyzer):
                # No const-element rung: the borrow-tuple const fixpoint
                # only records REASSIGNED / HOISTED targets, and sema
                # rejects a walrus re-bind of a non-value local outright --
                # so a target reaching here is never const.
                lowered_value = _lower_expr(
                    e.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.VALUE,
                                 pos=SinkPos.WALRUS_TARGET, forms=_ONLY_BTUPLE_SLOT,
                                 allow_temps=True))
                _witness("expr.walrus_btuple_mixed_call")
            else:
                note_detail("walrus.btuple_src")
                raise ThirUnsupported("expr.walrus")
            declared[e.target] = vtu
            lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_btuple")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value,
                cpp_type=vtu.to_cpp_return(), loc=loc)
        if (not resumable
                and need_predecl
                and ((is_plain_nonvalue(vtu) and e.target in ever_owned
                      and _f1_ref(vtu, analyzer))
                     # An OWNED-MOVABLE tuple walrus (`(t := make_pair(5))`
                     # at a declared `tuple[int32, Own[Box]]` return): the
                     # same deferred-init slot; reads deref `(*t)` and
                     # elements read STORAGE (`std::get<i>((*t))`, dot
                     # access -- no borrow elements by definition of
                     # owned-movable).
                     or wal_owned_tuple)
                and ((isinstance(e.value, (TpyCall, TpyMethodCall))
                      and not call_returns_cpp_ref(
                          analyzer, e.value.resolved_function_info))
                     or isinstance(e.value, TpyListComprehension))):
            # Owned non-value walrus (`(fresh := make())` off an Own-return
            # call, `(cols := [..comp..])`): deferred-init slot
            # `std::optional<T> fresh;` + `(fresh = make(), *fresh)`; reads
            # deref `(*fresh)` with dot access via the OPTIONAL_STORAGE
            # registration. A comprehension source lowers directly (the
            # main dispatch has no comp arm; the stmt-expr render is
            # position-independent).
            if isinstance(e.value, TpyListComprehension):
                from .comprehensions import _lower_comprehension
                comp_ptrs = _comp_shadow_pointers(lc.pointers,
                                                  declared, analyzer)
                lowered_value: THIRExpr = _lower_comprehension(
                    e.value, vtu, lc, declared, comp_ptrs)
            else:
                lowered_value = _lower_expr(
                    e.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 # The owned-tuple call result is consumed
                                 # whole by the slot assign, like the
                                 # tuple-unpack capture; a non-tuple source
                                 # at the same slot takes no such form.
                                 pos=SinkPos.WALRUS_TARGET,
                                 forms=(None if wal_owned_tuple
                                        else _NO_FORMS)))
            declared[e.target] = vtu
            lc.walrus_slot_locals.add(e.target)
            lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_owned_slot")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value,
                cpp_type=f"std::optional<{lc.render_type(vtu)}>",
                tail="deref", loc=loc)
        wal_value_opt = (
            ValueOptKind.SCALAR
            if not resumable and _value_opt_scalar(vtu, analyzer) is not None
            # The OWNED-view twin (`str | None` / `bytes | None`): the same
            # bare `std::optional<std::string>` slot and in-place assign, one
            # binding kind over -- what differs is only the narrowed deref
            # verdict the binding registration carries.
            else ValueOptKind.VIEW
            if (not resumable
                and _value_opt_owned_view(vtu, analyzer) is not None)
            else None)
        if wal_value_opt is not None:
            # Value-opt scalar walrus: REASSIGN (`(x := None)` on a
            # declared `int32 | None` local) is the plain in-place assign
            # -- `None` renders `std::nullopt`, a scalar RHS assigns bare
            # (the optional's converting assignment). FIRST-DECL predecls
            # the bare `std::optional<int32_t> val;` slot at the flush
            # point (the scalar predecl machinery) and assigns in place;
            # a REUSE of the same target is the reassign on the
            # predeclared slot.
            if isinstance(e.value, TpyNoneLiteral):
                lowered_value = THIRLiteral(result_type=vtu, value=None,
                                            form=Form.STORAGE, loc=loc)
            elif (wal_value_opt is ValueOptKind.VIEW
                  and isinstance(e.value, TpyName)
                  and _opt_view_arg_shim(
                      _param_declared_type(e.value.name, lc), vtu, analyzer)):
                # A BORROW-form value-opt view PARAM source: the target's
                # owned `optional<owned>` slot needs the per-element copy the
                # decl sink spells (`optional`'s converting assignment does
                # not reach `span` -> `vector` at all, and would leave the
                # str face's copy implicit).
                lowered_value = THIROptViewArg(
                    result_type=vtu, name=e.value.name, form=Form.VALUE,
                    loc=loc)
            else:
                lowered_value = _lower_expr(
                    e.value, lc, declared,
                    use=_ExprUse(slot_target=vtu),
                    allow_whole_optional=True)
            declared[e.target] = vtu
            lc.value_opt_bindings[e.target] = wal_value_opt
            cpp_type = None
            if need_predecl:
                cpp_type = lc.render_type(vtu)
                lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_value_opt")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, cpp_type=cpp_type, loc=loc)
        wal_str = _resolved_str_value(vtu, analyzer)
        wal_bytes = _resolved_bytes_value(vtu, analyzer)
        wal_owned = (wal_str if wal_str is not None
                     and not is_str_view_type(wal_str)
                     else wal_bytes if wal_bytes is not None
                     and not is_bytes_view_type(wal_bytes) else None)
        if wal_owned is not None and not resumable:
            # Owned str/bytes walrus (`(s := s + "!")` reassign, or a
            # FIRST-DECL like `(hostname := host)` off a view param -- the
            # view->owned promotion already resolved the binding owned):
            # the bare owned slot on the named row plus the in-place
            # assign, the value-scalar shape at an owning buffer type.
            # A binding that resolved VIEW-form is a different predecl
            # (the pending-view slot) and never reaches here.
            lowered_value = _lower_expr(
                e.value, lc, declared,
                use=_ExprUse(slot_target=wal_owned))
            declared[e.target] = wal_owned
            cpp_type = None
            if need_predecl:
                cpp_type = lc.render_type(wal_owned)
                lc.walrus_predeclared.add(e.target)
            _witness("expr.walrus_owned_viewfam")
            return THIRWalrus(
                result_type=wal_owned, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, cpp_type=cpp_type, loc=loc)
        if (not (_eligible_scalar(vtu) or _eligible_char(vtu)
                 # An enum walrus (`(c := pick())`) is the scalar shape: a
                 # plain `Color c;` predecl + the in-place assign; the
                 # predecl spells via render_type like the enum decl arm.
                 or _eligible_enum(vtu, analyzer) is not None)
                or isinstance(vtu, OptionalType)):
            raise ThirUnsupported("expr.walrus")
        # The value rides the walrus's own use: an arg-temp-producing call
        # value flushes at the enclosing statement exactly like a bare call
        # in this position (the hoist is statement-level regardless).
        lowered_value = _lower_expr(
            e.value, lc, declared,
            use=_ExprUse(allow_temps=use.allow_temps, slot_target=vtu))
        declared[e.target] = vtu
        cpp_type = None
        if need_predecl:
            cpp_type = lc.render_type(vtu)
            lc.walrus_predeclared.add(e.target)
        _witness("expr.walrus_scalar")
        return THIRWalrus(
            result_type=vtu, name=e.target,
            cpp_name=escape_cpp_name(e.target),
            value=lowered_value,
            cpp_type=cpp_type,
            loc=loc)
    if isinstance(e, TpyIfExpr):
        bytes_rt = _resolved_bytes_value(rtype, analyzer)
        if not (_resolved_scalar(rtype, analyzer) or _eligible_char(rtype)
                or _eligible_enum(rtype, analyzer) is not None
                or _resolved_str_value(rtype, analyzer) is not None
                or _is_string_owned(rtype)
                # A bytes-family ternary (`a if a is not None else b`); the
                # both-view arm shape is the only one lowered (see
                # `_lower_if_expr`), mixed/owned arms defer there.
                or bytes_rt is not None
                # A tuple ternary renders bare (both arms target the
                # ternary's own type, no per-arm conversion); off-slice arm
                # shapes reject in their own lowering.
                or isinstance(unwrap_readonly(unwrap_ref_type(rtype)),
                              TupleType)
                # A container ternary renders bare too (`((c) ? (a) : (b))`
                # -- an lvalue when both arms are lvalues, the REF_ALIAS
                # decl's init); arm shapes gate in their own lowering.
                or _ifexpr_container(rtype, analyzer) is not None
                # A pointer-repr Optional ternary (WIDE pointee class):
                # each arm normalizes to the `T*` the result renders as
                # (the _ptr_optional_branch mirror in _lower_if_expr).
                or _optional_ptr_borrow_wide(rtype, analyzer) is not None
                # A VALUE-repr Optional ternary: both arms wrap in the
                # spelled optional (`std::optional<std::string>("hello")`)
                # for C++ ternary deduction -- the value-repr sibling; arm
                # shapes gate in _lower_if_expr.
                or _value_opt_ternary_result(rtype, analyzer) is not None
                # The container-ELEMENT storage flavor of the same wrap: at
                # the immediate element slot even a pointer-repr Optional
                # renders `std::optional<T>(<arm>)` on both arms.
                or (elem_storage
                    and _storage_opt_ternary_result(rtype, analyzer)
                    is not None)
                # A plain F1-record ternary of lvalue arms renders bare (an
                # lvalue when both arms are lvalues -- the Own-slot copy /
                # REF_ALIAS sources); arm shapes gate in _lower_if_expr.
                or _f1_record(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(rtype))), analyzer)
                # A ternary of `Own[@dynamic P]`-returning calls (sema
                # strips the Own from the ternary's own type): renders
                # bare, each arm a unique_ptr rvalue the ?: moves through.
                or _dyn_own_call_ternary(e, rtype, analyzer)
                # A `Ptr[T]` ternary: T* is a first-class value copied like
                # a scalar, arms render bare (`((cond) ? (call) : (call))`)
                # and gate in their own lowering.
                or _eligible_ptr_value(rtype, analyzer)
                # A WIDE ptr-union ternary: each arm normalizes to the
                # pointer variant (bare binding name / to_ptr_variant field
                # lift); arm shapes gate in _lower_if_expr.
                or _eligible_ptr_union_wide(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
                    analyzer) is not None):
            note_detail("ifexpr.result_type")
            raise ThirUnsupported("expr.ifexpr")
        return _lower_if_expr(e, rtype, lc, declared, loc,
                              cond_temps_ok=use.allow_temps,
                              elem_storage=elem_storage,
                              record_prvalue_ok=use.admits(
                                  SinkForm.RECORD_PRVALUE))
    if isinstance(e, TpyCall):
        if not isinstance(e.func, TpyName):
            return _lower_computed_call(e, e.func, rtype, lc, declared, use)
        if (e.func.name == "ord"
                and len(e.args) == 1
                and isinstance(e.args[0], TpyStrLiteral)
                and len(e.args[0].value) == 1):
            # ord("X") over a single-char literal folds to its constant
            # ordinal -- a bare int in every position (the runtime `ord(s)`
            # rides the native arm).
            _witness("call.ord_fold")
            return _lower_int_literal(ord(e.args[0].value), rtype, lc, loc)
        vinfo = (None if use.result in (_ExprResultUse.CONDITION,
                                        _ExprResultUse.TRUTHY)
                 else _poly_isinstance_value_info(e, declared, analyzer))
        if vinfo is not None:
            # A VALUE-position polymorphic isinstance (`return
            # isinstance(e, VE)`): the bare null-check chain -- no alias,
            # position-independent bool. CONDITION/TRUTHY positions stay
            # out: a statement condition (incl. the negated form's operand)
            # carries branch/post-if facts this arm would silently drop --
            # those take the narrow-if arms instead. Narrowed / re-spelled
            # subjects and non-pointer-shaped pointer-locals reject.
            vvar, vmembers, _vd = vinfo
            if (vvar in lc.narrow.narrowed or vvar in lc.narrow.spelled
                    or (vvar in lc.pointers
                        and not polymorphic_source_is_pointer(
                            _poly_subject_decl(declared.get(vvar))))):
                raise ThirUnsupported(call_reject_reason("expr.call"))
            _witness("narrow.poly_value")
            return THIRDynIsinstanceMulti(
                result_type=rtype,
                checks_cpp=_poly_cast_checks(vvar, vmembers, lc, declared),
                loc=loc)
        if _is_range_call(e):
            # A `range(...)` call in OBJECT position (decl init, print arg):
            # the same `::tpy::Range<T>(...)` ctor render the instantiation-arg
            # and comprehension routes use. For-loop iterables are intercepted
            # upstream, so a range reaching here is always object-position.
            range_fi = e.resolved_function_info
            if (len(e.args) in (1, 2, 3) and range_fi is not None
                    and range_fi.cpp_template
                    and _eligible_scalar(_range_counter_type(e, analyzer))):
                _witness("call.range_object")
                return _lower_range_object(e, lc, declared)
            note_detail("call.range_shape")
            raise ThirUnsupported(call_reject_reason("expr.call"))
        if e.dunder_call is not None:
            # `obj(args)` where obj has a __call__ method: sema synthesized the
            # equivalent `obj.__call__(args)` method call, so lowering sends
            # the synthetic node through the method-call path (which gates
            # its own use), not the free-call machinery that rejects it.
            _witness("call.dunder_call")
            return _lower_expr(e.dunder_call, lc, declared, use=use)
        # `own_iter(x)` -> `::tpy::own_iter(std::move(x))` -- the
        # special-builtin arm. A bare un-narrowed, non-pointer NAME source
        # only (the corpus face); other sources reject.
        oi_fi = e.resolved_function_info
        if (oi_fi is not None and oi_fi.qualified_name == qnames.OWN_ITER
                and len(e.args) == 1 and not e.kwargs
                and isinstance(e.args[0], TpyName)
                and e.args[0].name not in lc.pointers
                and e.args[0].name not in lc.narrow.narrowed):
            _witness("call.own_iter_explicit")
            return THIRCall(
                result_type=rtype, callee=e.func_name,
                args=(_lower_expr(e.args[0], lc, declared),),
                cpp_template="::tpy::own_iter(std::move({0}))",
                loc=loc)
        # `copy_iter(it)` -> `::tpy::copy_iter<Elem>(<it>)` -- the
        # special-builtin arm. The elem spelling comes off the arg's
        # iterable element type; an unresolvable elem would need the
        # plain copy() render -- unwitnessed, so it rejects. The
        # inner lowers ITERABLE (a combinator / name renders itself).
        ci_fi = e.resolved_function_info
        if (ci_fi is not None and ci_fi.qualified_name == qnames.COPY_ITER
                and len(e.args) == 1 and not e.kwargs):
            ci_at = analyzer.get_expr_type(e.args[0])
            if isinstance(ci_at, OwnType):
                ci_at = ci_at.wrapped
            ci_elem = get_iterable_element_type(
                ci_at, registry=analyzer.registry)
            if ci_elem is None:
                note_detail("call.copy_iter_elem")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            ci_elem = unwrap_ref_type(ci_elem)
            _witness("call.copy_iter_explicit")
            return THIRCall(
                result_type=rtype, callee=e.func_name,
                args=(_lower_expr(
                    e.args[0], lc, declared,
                    use=_ExprUse(result=_ExprResultUse.ITERABLE)),),
                cpp_template=(
                    f"::tpy::copy_iter<{lc.render_type(ci_elem)}>({{0}})"),
                loc=loc)
        # `try_parse(Color, name)` -> `::tpy::EnumUtil<Color>::try_parse(name)`
        # -- the TRY_PARSE special-builtin arm; the enum spelling comes from
        # the fi's Optional return inner, the value arg renders plain.
        tp_fi = e.resolved_function_info
        if (tp_fi is not None and tp_fi.qualified_name == qnames.TRY_PARSE
                and len(e.args) == 2 and not e.kwargs
                and isinstance(tp_fi.return_type, OptionalType)):
            _witness("call.try_parse")
            return THIRCall(
                result_type=rtype, callee=e.func_name,
                args=(_lower_expr(e.args[1], lc, declared),),
                cpp_template=(
                    "::tpy::EnumUtil<"
                    f"{lc.render_type(tp_fi.return_type.inner)}"
                    ">::try_parse({0})"),
                loc=loc)
        # The static type-param isinstance family at a VALUE position
        # (`return isinstance(x, (Dog, Cat))` on a bounded-T subject): the
        # same spelled trait disjunction the truthy-condition arm renders --
        # no extraction alias, position-independent. Non-tparam isinstance
        # stays with the narrowing machinery and keeps rejecting here.
        si_val = _lower_static_isinstance(e, lc, rtype)
        if si_val is not None:
            _witness("call.isinstance_static_value")
            return si_val
        # A UNION-subject isinstance at a VALUE position (`print(
        # isinstance(pu, A))` on a routed variant local): the same
        # holds_alternative chain the narrowing condition renders, with NO
        # extraction alias -- position-independent, spelled on the bare
        # original name -- the narrowed alias is deliberately skipped.
        # Protocol / Any / polymorphic / deref / indirect subjects keep
        # rejecting; overload bodies are never lowered
        # (sig.special_callable), so the overload_param_types const-fold
        # cannot be live here. A RESUMABLE frame member is in
        # (`yield isinstance(v, A)`): its variant spelling -- bare member,
        # or the frame_slot `(*v)` deref -- is the same one
        # `_narrow_variant_cpp` already gives the condition arms.
        if (e.isinstance_var is not None and e.isinstance_type is not None
                and not e.isinstance_type_param
                and not e.isinstance_is_protocol
                and not e.isinstance_deref_depth):
            iv = e.isinstance_var
            du = declared.get(iv)
            dub = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(du)))
                   if du is not None else None)
            if (isinstance(dub, UnionType)
                    and iv not in lc.pointers
                    and iv not in lc.narrow.narrowed
                    and iv not in lc.inline_narrowed):
                members = (list(e.isinstance_type.members)
                           if isinstance(e.isinstance_type, UnionType)
                           else [e.isinstance_type])
                is_ptr = _narrow_subject_is_ptr(iv, dub, lc)
                const = ("const " if (is_ptr and _narrow_subject_const(iv, lc))
                         else "")
                _witness("call.isinstance_union_value")
                return THIRIsinstance(
                    result_type=rtype,
                    variant_cpp=_narrow_variant_cpp(iv, dub, lc),
                    union_test=_union_test(iv, dub, members, lc),
                    member_cpps=tuple(
                        f"{const}{lc.render_type(m)}*" if is_ptr
                        else lc.render_type(m)
                        for m in members),
                    loc=loc)
        if not _call_use_supported(e, lc, declared, use,
                                   allow_whole_optional=allow_whole_optional,
                                   error_return_raw=error_return_raw):
            raise ThirUnsupported(call_reject_reason("expr.call"))
        if e.macro_expansion is not None:
            # `@call_macro` / getattr / hasattr: the sema-synthesized
            # replacement renders in place, so the call node lowers to its
            # expansion.
            exp = e.macro_expansion
            if isinstance(exp, TpyMethodCall) and exp.method == "__getattr__":
                # The 2-arg `getattr(obj, name)` builtin: delegate to the
                # dyn-attr read helper, which renders the same bare method
                # call as the general arm but is RESULT-blind -- the
                # dunder's return (Any / owned str / ...) lands bare in
                # whatever sink consumes the builtin. Runtime names all go
                # through the dunder, hence allow_name_arg.
                lowered_ga = _lower_dyn_synth_call(
                    exp, rtype, lc, declared, loc, allow_name_arg=True)
                _witness("call.dyn_getattr_builtin")
                return lowered_ga
            _witness("call.macro_expansion")
            return _lower_expr(e.macro_expansion, lc, declared)
        if e.dyn_hasattr_call is not None:
            # `hasattr(obj, name)` runtime probe: the try/catch stmt-expr
            # over the synthesized `obj.__getattr__(name)`.
            inner = _lower_dyn_synth_call(
                e.dyn_hasattr_call, analyzer.get_expr_type(e.dyn_hasattr_call),
                lc, declared, loc, allow_name_arg=True)
            _witness("call.dyn_hasattr")
            return THIRCall(
                result_type=rtype, callee="hasattr",
                args=(inner,),
                cpp_template=(
                    "({{ bool __ok = true; "
                    "try {{ (void)({0}); }} "
                    "catch (const ::tpy::AttributeError&) {{ __ok = false; }} "
                    "__ok; }})"),
                loc=loc)
        if e.dyn_getattr_default_call is not None:
            # `getattr(obj, name, default)`: the optional-deferred stmt-expr
            # yielding the dunder's result or the default on AttributeError.
            # The default renders against the dunder's return type.
            synth = e.dyn_getattr_default_call
            inner = _lower_dyn_synth_call(
                synth, analyzer.get_expr_type(synth), lc, declared, loc,
                allow_name_arg=True)
            fi = synth.resolved_function_info
            ret_cpp = lc.render_type(fi.return_type)
            default = _lower_call_arg(e.args[2], fi.return_type, lc, declared,
                                      method_arg=True)
            _witness("call.dyn_getattr_default")
            return THIRCall(
                result_type=rtype, callee="getattr",
                args=(inner, default),
                cpp_template=(
                    f"({{{{ std::optional<{ret_cpp}> __r; "
                    "try {{ __r.emplace({0}); }} "
                    "catch (const ::tpy::AttributeError&) "
                    "{{ __r.emplace({1}); }} "
                    "std::move(*__r); }})"),
                loc=loc)
        if e.cast_target_type is not None:
            if (len(e.args) != 2 or e.kwargs
                    or e.double_star_unpack is not None):
                note_detail("call.cast.shape")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            if e.cast_source_is_any:
                # `typing.cast(T, x)` from Any: the runtime checked-extract
                # `::tpy::any_cast_or_panic<T>(x)`.
                _witness("call.cast_any")
                return THIRCoerce(
                    result_type=rtype,
                    expr=_lower_expr(e.args[1], lc, declared),
                    coercion_name="any_cast",
                    wrap=("::tpy::any_cast_or_panic<"
                          f"{e.cast_target_type.to_cpp()}>({{0}})"),
                    loc=loc)
            # `typing.cast(T, x)` non-Any: a compile-time no-op rendering the
            # bare source.
            _witness("call.cast_passthrough")
            return _lower_expr(e.args[1], lc, declared)
        if e.enum_from_value is not None:
            if (_eligible_enum(e.enum_from_value, analyzer) is None
                    or len(e.args) != 1 or e.kwargs):
                note_detail("call.enum_from_value.shape")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            enum_arg_type = analyzer.get_expr_type(e.args[0])
            # The narrow keys on the DECLARED type (`is_runtime_bigint`), so a
            # retro-widened literal-seeded local narrows here too.
            enum_narrow_key = _narrow_key_type(e.args[0], declared, analyzer)
            if (not _resolved_scalar(enum_arg_type, analyzer)
                    or enum_narrow_key is _NARROW_UNMIRRORED
                    or (_runtime_bigint(enum_narrow_key, analyzer)
                        and _const_index(
                            _unwrap_lit_coerce(e.args[0])) is not None)):
                note_detail("call.enum_from_value.arg")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            # `E(x)` -> `::tpy::EnumUtil<E>::from_value(x)`.
            # A runtime-BigInt arg takes the checked
            # `({0}).to_fixed_check<U>()` wrap over the enum's underlying
            # type (the gate keeps literal-BigInt args out). Rides THIRCall's
            # cpp_template expansion like a scalar type-constructor.
            spelled = enum_cpp_name(e.enum_from_value,
                                    analyzer.ctx.module_name)
            arg = _lower_expr(e.args[0], lc, declared)
            if _runtime_bigint(enum_narrow_key, analyzer):
                _witness("narrow.enum_arg")
                einfo = enum_info_of(e.enum_from_value)
                assert einfo is not None
                u = einfo.underlying_type
                arg = THIRCoerce(result_type=u, expr=arg,
                                 coercion_name=_BIGINT_NARROW,
                                 wrap="({0})" + f".to_fixed_check<{u.to_cpp()}>()",
                                 loc=loc)
            return THIRCall(
                result_type=rtype, callee=e.func_name,
                args=(arg,),
                cpp_template=(f"::tpy::EnumUtil<{spelled}>"
                              "::from_value({0})"),
                loc=loc)
        fi = e.resolved_function_info
        if fi is None:
            td_ri = _typed_dict_ctor_call(e, analyzer)
            if td_ri is not None:
                # TypedDict ctor (the kwargs-pack rewrite): the record-branch
                # tail's spelling over init_params -- no fi, no mutation
                # facts, field-ordered positional args.
                _witness("ctor.typed_dict")
                qual = lc.analyzer.registry.record_qualification(
                    td_ri, lc.analyzer.ctx.module_name)
                td_type_cpp = (qualified_cpp_name(*qual) if qual is not None
                               else e.func_name)
                td_args = []
                for i, (a, trip) in enumerate(zip(e.args, td_ri.init_params)):
                    p_type = trip[1]
                    if not _record_ctor_arg_supported(
                            a, p_type, i, None, lc, declared,
                            _ExprUse(allow_temps=False)):
                        note_detail("call.ctor_arg."
                                    + _type_family_tag(p_type, analyzer))
                        raise ThirUnsupported(call_reject_reason("expr.call"))
                    td_args.append(_lower_call_arg(
                        a, p_type, lc, declared, temp_args=False,
                        protocol_slots=True))
                return THIRCtorCall(
                    result_type=rtype, type_cpp=td_type_cpp,
                    args=tuple(td_args), form=Form.STORAGE, loc=loc)
        if fi is not None and fi.is_constructor:
            if not _record_ctor_shape_supported(e, lc, use):
                raise ThirUnsupported(call_reject_reason("expr.call"))
            # A same-module user-record ctor rvalue (the `Own[union]`-slot
            # arg): the RAW source name renders over the (gate-restricted)
            # args. `fi` is sema's synthetic
            # constructor fi, whose params mirror the resolved __init__'s.
            # A record-rvalue arg keys on the slot's mutation
            # (the ctor_mutated rule): a MUTATED ref slot
            # hoists the named temp (`A __tmp_N = A(1); Cls(__tmp_N)`,
            # gate-admitted only at flush positions), a const slot binds the
            # inline prvalue expansion through the plain arg path.
            # The INSTANTIATION form (`Cell[int32]()` / `Poll[T]()` /
            # inferred `Pair(1, 2)`, call_type set) spells the rendered
            # type over the same arg machinery
            # (`type_to_cpp(call_type)(args)`); the raw-name form
            # renders the bare source name.
            brace_ctor = False
            if e.call_type is not None:
                _witness("ctor.instantiation")
                type_cpp = lc.render_type(e.call_type)
            else:
                # A cross-module record spells the declaring module's
                # qualification; the same-module face keeps the RAW source
                # name. Derive the record from the ctor's sema RESULT type
                # -- the qname-corrected lookup -- so a short-name collision
                # cannot pick the wrong record (lowering rejects those
                # shapes, but the
                # spelling must not lean on that from afar).
                ri = lc.analyzer.registry.get_record_for_type(rtype)
                assert ri is not None, "lowered ctor has no record"
                if ri.is_native and ri.implements_throwable:
                    # A native exception spells its `@native` name
                    # (`::tpy::OSError`) via to_cpp() / native_cpp_names --
                    # the module qualification would give the wrong
                    # `::tpystd::builtins::` path. _f1_record guarantees
                    # to_cpp() == the resolver's spelling.
                    _witness("ctor.native")
                    type_cpp = unwrap_readonly(rtype).to_cpp()
                elif ri.is_native:
                    # A plain @native record spells record_info.native_name;
                    # @native_c PODs
                    # take the aggregate `{args}` init.
                    _witness("ctor.native_plain")
                    type_cpp = ri.native_name
                    brace_ctor = ri.is_native_c
                else:
                    qual = lc.analyzer.registry.record_qualification(
                        ri, lc.analyzer.ctx.module_name)
                    if qual is not None:
                        _witness("ctor.cross_module")
                        type_cpp = qualified_cpp_name(*qual)
                    else:
                        _witness("ctor.call")
                        # Inside a record whose MEMBER shadows a same-named
                        # type, the whole body emits under
                        # `qualify_shadowed_nominals()`, so a local ctor
                        # callee spells fully-qualified. The flag is live
                        # during this lowering too, so `to_cpp()` already
                        # carries the right spelling -- but a bare name would
                        # not. Keyed on the ENCLOSING record (the one that
                        # shadows), not the constructed one.
                        encl = (lc.analyzer.registry.get_record(lc.record_name)
                                if lc.record_name else None)
                        if encl is not None and encl.shadows_local_type:
                            type_cpp = unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(rtype))).to_cpp()
                        else:
                            # The resolved record's own name, not the callee
                            # spelling: `cls(...)` inside a @classmethod names
                            # no C++ type (the two are equal elsewhere).
                            type_cpp = ri.name
            ctor_mut = fi.mutated_params or frozenset()
            eff_params = fi.params
            if not eff_params and e.args:
                # Inherited `__init__` (param-less synthetic fi): read the
                # registry triples directly; the gate pinned
                # the arity against the same source.
                eff_params = _ctor_effective_params(
                    e, lc.analyzer.registry.get_record_for_type(rtype))
            args = []
            raw_ctor_fi = _raw_record_ctor_fi(rtype, lc.analyzer)
            # The callee's deep-const verdict, read the way every other call
            # position reads it. Without it a member lift renders mutable
            # pointees into a const-pointee ctor slot -- the same arg, the
            # same slot, one position that did not ask.
            ctor_dcbp = _ctor_deep_const_params(fi)
            for i, (a, p) in enumerate(zip(e.args, eff_params)):
                if not _record_ctor_arg_supported(
                        a, p.type, i, fi, lc, declared, use):
                    note_detail(
                        "call.ctor_arg." + _type_family_tag(p.type, analyzer))
                    raise ThirUnsupported(call_reject_reason("expr.call"))
                rec = (_record_rvalue_temp_slot(a, p.type, lc.analyzer)
                       if i in ctor_mut else None)
                if rec is not None:
                    if not temp_args:
                        # A match guard admits calls but is never a flush point,
                        # so an arg needing a hoisted temp here cannot be lowered
                        # -- fall the body back rather than drop the temp.
                        raise ThirUnsupported(
                            "ctor mutated-slot rvalue temp outside a flush position")
                    _witness("argtemp.ctor_mut_rvalue")
                    args.append(THIRArgTemp(
                        result_type=rec, cpp_type=rec.to_cpp(), movable=unwrap_ref_type(rec).is_movable(),
                        # `R __tmp_N = <init>;` IS a storage decl sink, so
                        # the init takes the STORAGE result use -- the same
                        # thread the marker-protocol record hoist gives its
                        # `auto __tmp_N =` twin.
                        init=_lower_expr(
                            a, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE)),
                        form=Form.BORROW, loc=getattr(a, "loc", None)))
                elif (i in ctor_mut
                      and isinstance(a, (TpyArrayLiteral, TpyDictLiteral,
                                         TpySetLiteral))
                      and _container_literal_arg(a, p.type, analyzer)):
                    # The gate-admitted MUTATED-slot container-literal hoist
                    # (the ctor sibling of the free-call
                    # argtemp.container_literal rows); const slots keep the
                    # inline brace-init arm below.
                    if not temp_args:
                        raise ThirUnsupported(
                            "ctor container-literal temp outside a flush "
                            "position")
                    _ct_slot = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(p.type)))
                    _ct_lowered = _lower_literal_arg(
                        a, _ct_slot, lc, declared,
                        "container-literal ctor arg on the make path")
                    _witness("argtemp.container_literal")
                    args.append(THIRArgTemp(
                        result_type=_ct_slot, cpp_type=_ct_slot.to_cpp(),
                        init=_ct_lowered, form=Form.BORROW,
                        movable=unwrap_ref_type(_ct_slot).is_movable(),
                        loc=getattr(a, "loc", None)))
                elif _field_read_ref_ctor_arg(a, p.type, declared, analyzer,
                                              mutated=i in ctor_mut):
                    # The gate admitted this exact shape (declared field type
                    # == slot referent), so the render IS the bare member
                    # read. Lower it directly: the generic VALUE position the
                    # arg loop would use re-asks the result-type question and
                    # rejects a container field there, for copy-vs-alias
                    # reasons that cannot arise at a same-type ref slot.
                    args.append(_lower_expr(a, lc, declared,
                                            field_prechecked=True))
                else:
                    # temp_args is scoped to the temp-hoisting rows the gate
                    # admitted (the disjuncts below, one per row). A blanket
                    # temp_args would re-shape the other temp rows in
                    # _lower_call_arg (record rvalue into a const ref slot,
                    # member-NAME union) whose ctor renders are BARE.
                    flush_slot = (
                        _container_comp_arg(a, p.type)
                        or _own_lvalue_temp_slot(
                            a, p.type, lc.analyzer, declared,
                            lc.prescan.param_names) is not None
                        or _union_ctor_temp_arg(a, p.type, lc.analyzer)
                        or _protocol_arg_slot(p.type) is not None
                        # The member-valued VALUE-union temp (`std::variant
                        # <...> __tmp_N = v;`), gate-admitted above.
                        or _value_union_temp_slot(
                            a, p.type, declared, lc.analyzer) is not None
                        # The Own[protocol]-slot container copy temp
                        # (`auto __tmp_N = words;` + move -- the still-live
                        # half of the own_proto_container arm).
                        or _own_proto_container_slot(
                            a, p.type, lc.analyzer, declared) is not None
                        # The optional-ptr 'ctor' face's ArgTemp (`T __tmp_N =
                        # <rvalue>; ...&__tmp_N`), gate-admitted under temps_ok
                        # -- and its scalar-pointee sibling, which the arg gate
                        # admits SLOT-BLIND (a bare scalar pass into a `T*`
                        # slot does not compile).
                        or _optional_ptr_arg_face(
                            a, p.type, declared, lc.analyzer) in (
                                'ctor', 'scalar_temp',
                                # ... and the container-LITERAL face, whose
                                # spelled temp + address-of lift
                                # (`std::vector<T> __tmp_N = {..};
                                # Holder(&(__tmp_N))`) hoists at the
                                # enclosing statement exactly as for 'ctor'.
                                'container_temp')
                        # The nullable-protocol literal temp
                        # (`std::array<..> __tmp_N = {..};` + `&(__tmp_N)`).
                        or _protocol_union_literal_temp_arg(
                            a, p.type, lc.analyzer) is not None
                        # ... and its iterator-rvalue sibling.
                        or _protocol_union_iter_temp_arg(
                            a, p.type, declared, lc.analyzer) is not None)
                    # `nested_temps` rides the statement flush into a
                    # call-shaped arg's OWN args (`Holder(wrap(int32(42)))`
                    # -- the inner generic call's scalar ref-slot temp lands
                    # at the pre-statement flush); the ctor arg's own
                    # temp rows stay flush_slot-gated.
                    args.append(_lower_call_arg(
                        a, p.type, lc, declared,
                        temp_args=temp_args and flush_slot,
                        nested_temps=temp_args,
                        protocol_slots=True,
                        readonly_target=i in ctor_dcbp))
            return THIRCtorCall(
                result_type=rtype, type_cpp=type_cpp,
                args=tuple(args), brace_init=brace_ctor,
                form=Form.STORAGE, loc=loc)
        if (isinstance(e.call_type, PtrType) and not e.args and not e.kwargs
                and e.double_star_unpack is None):
            # A pointer null constructor `Ptr[T]()` / `Ptr[readonly[T]]()` ->
            # a typed nullptr `static_cast<T*>(nullptr)`; the spelling rides
            # render_type.
            _witness("ctor.ptr_null")
            return THIRCall(
                result_type=rtype, callee=e.func_name, args=(),
                cpp_template=(
                    f"static_cast<{lc.render_type(e.call_type)}>(nullptr)"),
                loc=loc)
        if e.call_type is not None:
            # A generic-type INSTANTIATION (`list(it)` / `set(xs)`): the
            # resolved ctor's sema-substituted @cpp_template expanded over
            # per-slot args after validating this arm's shape. A
            # range(...) arg renders as its substituted Range template (the
            # comprehension begin/end iterable's render); every other admitted
            # arg lowers through the shared call-arg machinery.
            fam = _storage_call_ret(rtype, analyzer)
            rt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
                       if rtype is not None else None)
            # `e.type_args` is NOT excluded (`items = list[int32]()`):
            # like the arg-ful instantiation fi, sema folds the explicit
            # spelling into `call_type`, and the zero-arg render reads
            # nothing else. The user-generic subscript form (`Stack[T]()`)
            # is excluded by `subscript_callee`, which builtins never set.
            if (not e.args and not e.kwargs and e.double_star_unpack is None
                    and e.subscript_callee is None
                    and ((fam is not None and _storage_call_container(fam))
                         # The zero-arg render is the default ctor spelled off
                         # `call_type` -- it does not read the ELEMENT type, so
                         # the element-keyed `_storage_call_ret` verdict (which
                         # exists for the downstream READ shapes) does not gate
                         # it. Record-element containers route here too; their
                         # consumers gate themselves.
                         or _empty_instantiation_family(rt_bare))):
                # An EMPTY instantiation (`s = set()` / `list()` / `dict()`,
                # element type sema-inferred): the spelled default ctor
                # `::tpy::ordered_set<int32_t>()` -- THIRCtorCall's zero-arg
                # render over the resolved call_type spelling.
                _witness("call.instantiation_empty")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=(), form=Form.STORAGE, loc=loc)
            vf_fi = _viewfam_ctor_call_fi(e, rtype, analyzer)
            if vf_fi is not None:
                # A str-family VALUE instantiation (`StrView("x")` -> `"x"`,
                # `String("x")` -> `std::string("x")`): call_type is set but the
                # result is not a storage container, so it renders the
                # resolved ctor's @cpp_template over inline args -- the same
                # expansion as the bare-name __init__ type-ctor path below.
                _witness("call.viewfam_instantiation")
                owned_str_ctor = _is_string_owned(rtype)
                lowered_args = []
                for a, p in zip(e.args, vf_fi.params):
                    if owned_str_ctor and not _str_pass_through_arg(
                            a, p.type, declared, analyzer):
                        note_detail("call.type_ctor.owned_str_arg")
                        raise ThirUnsupported(call_reject_reason("expr.call"))
                    lowered_args.append(_lower_call_arg(a, p.type, lc, declared))
                return THIRCall(
                    result_type=rtype,
                    callee=e.func_name,
                    args=tuple(lowered_args),
                    cpp_template=vf_fi.cpp_template,
                    loc=loc,
                )
            sp_fi = _span_ctor_call_fi(e, rtype)
            if sp_fi is not None:
                # `Span(p, 3)` -> `std::span<int32_t>(p, static_cast<size_t>(
                # 3))`: a VALUE view built by the resolved ctor's own
                # positional template over inline args, the same expansion the
                # str family takes.
                _witness("call.span_instantiation")
                return THIRCall(
                    result_type=rtype,
                    callee=e.func_name,
                    args=tuple(_lower_call_arg(a, p.type, lc, declared)
                               for a, p in zip(e.args, sp_fi.params)),
                    cpp_template=sp_fi.cpp_template,
                    loc=loc,
                )
            ni_fi = (_instantiation_call_fi(e)
                     if _native_iter_value_slot(
                         unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                             rtype))), analyzer)
                     else None)
            if ni_fi is not None:
                # `SpanIter(rs)` -> `::tpy::SpanIter<const int32_t>(rs)`:
                # a VALUE iterator built by the resolved ctor's own template
                # (sema pre-substituted {cpp} with the instantiation), the
                # same expansion as the span family.
                _witness("call.native_iter_instantiation")
                return THIRCall(
                    result_type=rtype,
                    callee=e.func_name,
                    args=tuple(_lower_call_arg(a, p.type, lc, declared)
                               for a, p in zip(e.args, ni_fi.params)),
                    cpp_template=ni_fi.cpp_template,
                    loc=loc,
                )
            if _view_ctor_bare_source(e, rtype, declared, analyzer):
                # A `Span[...]` / `Array[...]` instantiation over a bare
                # container/view NAME (`Span[readonly[int32]](lst)` ->
                # `std::span<const int32_t>(lst)`): a VALUE view, so it
                # spells the target type and direct-initializes it from the
                # source -- no `make_vector` machinery, which is what the
                # storage-container gate below exists to reach. There is no
                # resolved ctor fi, so the arg carries no slot type; a bare
                # name needs none.
                _witness("call.view_instantiation")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=tuple(_lower_expr(a, lc, declared) for a in e.args),
                    form=Form.VALUE, loc=loc)
            if _array_literal_ctor_source(e, rtype):
                # `Array[int32, 3]([10, 20, 30])` -> `std::array<int32_t, 3>({
                # 10, 20, 30})`: the spelled target type direct-initialized
                # from the literal, which takes `call_type` as its brace
                # target.
                _witness("call.array_literal_instantiation")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=(_lower_expr(
                        e.args[0], lc, declared,
                        use=_ExprUse(slot_target=e.call_type)),),
                    form=Form.VALUE, loc=loc)
            lit_slot = _container_literal_inst_slot(e, rtype, analyzer)
            if lit_slot is not None:
                # `set([Node(2)])` -> `::tpy::ordered_set<Node>({Node(2)})`:
                # the spelled result type around the literal's own braces. The
                # literal lowers against a LIST of the result's element slot,
                # which is what keeps it in the brace family for a `set`
                # result and off the Array element retype its own read-only
                # demoted type would carry.
                _witness("call.container_literal_instantiation")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=(_lower_expr(
                        e.args[0], lc, declared,
                        use=_ExprUse(slot_target=make_list(lit_slot))),),
                    form=Form.STORAGE, loc=loc)
            if (len(e.args) == 1 and isinstance(e.args[0], TpyDictLiteral)
                    and not e.kwargs
                    and getattr(e, "double_star_unpack", None) is None
                    and is_dict(unwrap_readonly(unwrap_send_sync(
                        e.call_type)))):
                # `dict({...})` (the asdict expansion's shape): the spelled
                # result type around the dict literal's OWN render -- no
                # element retarget, the literal spells its ordered_map.
                _witness("call.dict_literal_instantiation")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=(_lower_expr(e.args[0], lc, declared),),
                    form=Form.STORAGE, loc=loc)
            _dtl = (unwrap_readonly(unwrap_send_sync(e.call_type))
                    if e.call_type is not None else None)
            if (len(e.args) == 1 and isinstance(e.args[0], TpyArrayLiteral)
                    and not e.kwargs
                    and getattr(e, "double_star_unpack", None) is None
                    and _dtl is not None and is_dict(_dtl)
                    and len(getattr(_dtl, "type_args", ())) == 2
                    and e.args[0].elements
                    and all(isinstance(el, TpyTupleLiteral)
                            and len(el.elements) == 2
                            for el in e.args[0].elements)):
                # `dict[K, V]([(k, v), ...])`: the spelled ordered_map
                # around a brace list of SPELLED tuple elements, each
                # retargeted to tuple[K, V] (a union V absorbs via the
                # variant's converting ctor).
                _dtl_elem = TupleType(tuple(_dtl.type_args))
                _witness("call.dict_tuple_literal_instantiation")
                return THIRCtorCall(
                    result_type=rtype, type_cpp=lc.render_type(e.call_type),
                    args=(_lower_expr(
                        e.args[0], lc, declared,
                        use=_ExprUse(slot_target=make_list(_dtl_elem))),),
                    form=Form.STORAGE, loc=loc)
            inst_fi = _instantiation_call_fi(e)
            if inst_fi is None or not _container_storage_return_call_ret(
                    rtype, analyzer):
                note_detail("call.inst_shape")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            lowered_args: list[THIRExpr] = []
            for arg, param in zip(e.args, inst_fi.params):
                if _is_range_call(arg):
                    range_fi = arg.resolved_function_info
                    if (len(arg.args) not in (1, 2, 3) or range_fi is None
                            or not range_fi.cpp_template
                            or not _eligible_scalar(
                                _range_counter_type(arg, analyzer))):
                        note_detail("call.inst_range_shape")
                        raise ThirUnsupported(call_reject_reason("expr.call"))
                    lowered_args.append(_lower_range_object(arg, lc, declared))
                elif (isinstance(arg, TpyCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.is_constructor
                      and (_ctor_shape_ok(arg, analyzer)
                           or _ctor_instantiation_ok(arg, analyzer))):
                    # A user-iterator ctor rvalue (`dict(PairIter(3))`): the
                    # bare ctor renders into the dict_construct template, a
                    # prvalue needing no own_iter/move wrap.
                    _witness("call.inst_ctor_arg")
                    lowered_args.append(_lower_expr(arg, lc, declared))
                elif _inst_slice_arg_ok(arg, analyzer):
                    # A container-slice rvalue or SPAN view slice
                    # (`list(argv[i:])` -- a non-stepped list slice types as
                    # Span[T]): the bare `::tpy::list_slice(recv, ...)`
                    # renders inside the construct template. The subscript
                    # arm re-validates the receiver / bounds, so a shape
                    # outside its slice falls the body back.
                    _witness("call.inst_slice_arg")
                    lowered_args.append(_lower_expr(arg, lc, declared))
                elif (isinstance(arg, TpyCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.is_generator
                      and _free_callee_kind(arg, analyzer,
                                            generator_ok=True) is not None):
                    # A generator-factory rvalue (`list(gen())`): the bare
                    # factory call renders inside the construct template --
                    # the iterable-position render, consumed whole here.
                    # `allow_temps` rides the enclosing statement's flush in
                    # (the factory's own literal/rvalue args hoist their
                    # `__tmp_N` at the statement), like the qualified arm.
                    _witness("call.inst_gen_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                     allow_temps=temp_args)))
                elif (isinstance(arg, TpyMethodCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.is_generator):
                    # A module-qualified generator-factory rvalue
                    # (`list(heapq.merge(a, b))`): the marker-call machinery
                    # renders the qualified factory inline; ITERABLE use
                    # turns on its generator admission, and shapes outside
                    # its slice raise there. `allow_temps`
                    # rides the enclosing statement's flush in (the factory's
                    # vararg pack hoists its `std::array __tmp_N`).
                    _witness("call.inst_gen_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                     allow_temps=temp_args)))
                elif (isinstance(arg, TpyMethodCall)
                      and _dict_view_iterable_ok(
                          arg, declared, analyzer,
                          methods=("values", "keys", "items"))):
                    # A dict-view rvalue (`dict(m.items())` / `list(d.keys())`):
                    # the view call renders inline into the construct template
                    # (`::tpy::dict_items(original)`), the iterable-position
                    # render like the generator-factory arm.
                    _witness("call.inst_view_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif (isinstance(arg, TpyCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.cpp_template
                      and _iterator_protocol_result(arg, analyzer)):
                    # A combinator rvalue (`list(map(f, xs))`): the
                    # iterator-returning template call renders bare inside
                    # the construct template, iterable-position like the
                    # generator-factory arm (inner container names stay
                    # bare -- no own_iter outside this arm's last-use row).
                    _witness("call.inst_iter_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif (isinstance(arg, TpyCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.qualified_name
                      == qnames.COPY_ITER):
                    # A copy_iter rvalue (`list(copy_iter(map(f, xs)))`):
                    # the wrap renders bare inside the construct template
                    # via its own special-builtin arm.
                    _witness("call.inst_iter_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif (isinstance(arg, TpyCall)
                      and _iter_proto_call_ret(arg, analyzer)):
                    # An iterator-protocol call rvalue (`list(iter(words))`
                    # -> `construct<...>(::tpy::__iter__(words))`): renders
                    # inline in the construct template; the free-call
                    # gate's ITERABLE row owns the result admission.
                    _witness("call.inst_iter_proto_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif (isinstance(arg, TpyName) and arg.name in declared
                      and _protocol_binding(unwrap_readonly(unwrap_ref_type(
                          unwrap_send_sync(declared[arg.name]))))
                      is not None
                      # Defense-in-depth: the SLOT must itself be
                      # protocol-typed (every reachable single-arg
                      # cpp_template ctor is Iterable-shaped today; a
                      # future non-Iterable stub must re-verify, not
                      # silently reuse this bare render).
                      and isinstance(param.type, TpyType)
                      and is_protocol_type(unwrap_readonly(unwrap_ref_type(
                          unwrap_send_sync(param.type))))):
                    # A structural-protocol param NAME (`list(items)` on
                    # `items: Iterable[int32]` ->
                    # `::tpy::construct<std::vector<int32_t>>(items)`):
                    # the monomorphized lvalue binds bare in the template.
                    _witness("call.inst_proto_name_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif isinstance(arg, TpyGeneratorExpression):
                    # A genexpr rvalue (`list(x * x for x in xs)`): the
                    # make_generator IIFE binds directly; _lower_genexpr
                    # rejects the shapes outside its slice.
                    _witness("call.inst_genexpr_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                elif _inst_call_container_arg(arg, analyzer):
                    # A container-returning CALL (`set(make_nodes())`,
                    # `set(copy(b))`, `list(heapq.merge(a, b))`,
                    # `list(borrow(b))`): the ordinary call-arg render inline
                    # in the construct template. The own_iter / last-use rows
                    # below are about consuming a BINDING, which a call result
                    # is not, by value or by reference.
                    _witness("call.inst_call_rvalue_arg")
                    lowered_args.append(
                        _lower_call_arg(arg, param.type, lc, declared))
                elif _container_field_bare_read(arg, declared, analyzer):
                    # A container FIELD read (`list(item.dirnames)`): the
                    # member render binds the construct template bare. No
                    # move leg -- last-use movability is keyed on a NAME,
                    # so a field read is never a move source.
                    _witness("call.inst_field_arg")
                    lowered_args.append(
                        _lower_call_arg(arg, param.type, lc, declared))
                else:
                    if (not isinstance(arg, TpyName) or arg.name == "self"
                            or arg.name not in declared):
                        note_detail("call.inst_arg_shape")
                        raise ThirUnsupported(call_reject_reason("expr.call"))
                    arg_type = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(declared[arg.name])))
                    if not (is_list(arg_type) or is_dict(arg_type)
                            or is_set(arg_type)
                            # An Array source (`list(arr)`): the same
                            # construct<...> wrap over the (deref'd) name.
                            or is_array(arg_type)):
                        if (is_native_iterable(arg_type, analyzer.registry)
                                and not _f1_container_ref(arg_type)):
                            # A VALUE-form native iterable NAME (`list(r)` on
                            # a `range` local): the binding already IS a C++
                            # range, so it binds bare in the construct
                            # template. No move leg -- the consuming
                            # `own_iter` wrap drains an OWNING binding, which
                            # a value iterable never is.
                            _witness("call.inst_value_iter_arg")
                            lowered_args.append(
                                _lower_call_arg(arg, param.type, lc,
                                                declared))
                            continue
                        note_detail("call.inst_arg_shape")
                        raise ThirUnsupported(call_reject_reason("expr.call"))
                    if _is_move_source(arg, lc):
                        # A MOVABLE last-use name takes the consuming-
                        # __iter__ wrap (`dict(pairs)` ->
                        # `::tpy::own_iter(std::move(pairs))`). Last use
                        # alone is not the fact: a loop-unpack target
                        # (`for _, dirnames, _ in os.walk(..)`) is a last use
                        # but never movable, and renders bare --
                        # asking `all_last_uses` without movability rejected
                        # exactly that shape.
                        wrapped = _consuming_iter_wrap(
                            arg, param.type, lc, declared)
                        if wrapped is None:
                            # The wrap keys the consuming facts itself: a
                            # borrowing slot, or a family with no consuming
                            # `__iter__` (Array, Span), has nothing to drain
                            # and binds BARE -- the same verdict the
                            # Iterable-slot name arm reaches from the same
                            # helper returning None.
                            _witness("call.inst_bare_name_arg")
                            lowered_args.append(
                                _lower_call_arg(arg, param.type, lc,
                                                declared))
                        else:
                            lowered_args.append(wrapped)
                    else:
                        # A non-movable name (a loop-unpack target, a
                        # borrowed param) renders bare, under its own face
                        # so the census can tell this arm apart.
                        _witness("call.inst_bare_name_arg")
                        lowered_args.append(
                            _lower_call_arg(arg, param.type, lc, declared))
            _witness("call.instantiation_template")
            return THIRCall(
                result_type=rtype,
                callee=e.func_name,
                args=tuple(lowered_args),
                cpp_template=inst_fi.cpp_template,
                loc=loc,
            )
        if (fi is not None and fi.is_method and fi.name == "__init__"
                and not fi.native_function):
            # A scalar or slice-object type-constructor call (`int32(x)` /
            # `basic_slice(1, 3)`): the emit is the resolved __init__ overload's
            # @cpp_template expanded over the args with no receiver. Sema
            # already substituted {cpp} / class type params; positional-only
            # templates are carried verbatim. A `None` bound in a slice-ctor's
            # value-repr `int32 | None` slot renders `std::nullopt` (the
            # STORAGE-form None). A native-FUNCTION ctor (`int(str)` ->
            # `tpy::BigInt::from_str`, float/bytes from_str, `char(s)` ->
            # `char_from_str`) carries no cpp_template and is excluded by the
            # `not fi.native_function` guard above, routing through the native
            # free-call path (`native_free_ctor` in checks.py) instead.
            template_fi = _template_init_call_fi(e)
            if template_fi is None:
                note_detail("call.type_ctor.no_template_fi")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            scalar_ctor = _eligible_scalar(rtype)
            slice_ctor = _slice_object_type(rtype)
            owned_str_ctor = _is_string_owned(rtype)
            # `bytearray(...)` -- the template expands to the buffer type's own
            # construction, no different in kind from the scalar ctors above.
            # Its buffer-argument and uint8-iterable overloads reach here too,
            # since each carries a `@cpp_template` rather than naming a factory;
            # the size and int32-iterable overloads are native FUNCTIONS and so
            # are excluded by the `not fi.native_function` guard above, routing
            # through the free-call arg ladder instead.
            bytearray_ctor = (rtype is not None
                              and _bytes_family_ref(unwrap_readonly(
                                  unwrap_ref_type(unwrap_send_sync(rtype)))))
            if not (scalar_ctor or slice_ctor or owned_str_ctor
                    or bytearray_ctor or _eligible_char(rtype)
                    or _resolved_viewfam_value(rtype, analyzer) is not None):
                note_detail("call.type_ctor.result_kind")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            if bytearray_ctor:
                _witness("call.type_ctor.bytearray")
            lowered_args = []
            for a, p in zip(e.args, template_fi.params):
                # The from_str overload (`int32(tok)` on a str token ->
                # `::tpy::from_str_check<int32_t>(tok)`): the resolved
                # __init__'s own @cpp_template carries the parse render, so
                # a str-family arg at a str param slot passes bare too.
                str_parse = (
                    scalar_ctor
                    and _resolved_str_value(p.type, analyzer) is not None
                    and _resolved_str_value(
                        analyzer.get_expr_type(a), analyzer) is not None)
                # An INT-kind type-param arg (`int32(N)` under `[N: int]`):
                # the non-type template parameter is a constant that renders
                # as the bare name, which is exactly what the resolved
                # overload's `{0}` slot expands over -- the same bare pass a
                # scalar name takes.
                int_tparam = (
                    scalar_ctor
                    and _int_type_param_value(analyzer.get_expr_type(a))
                    and _ctor_arg_slot_ok(p.type, analyzer)
                    and _witness("arg.type_ctor_int_tparam"))
                # A TYPE-PARAM arg (`int32(x)` under `[T: AnyFixedInt]`):
                # the monomorphized binding renders as the bare name, which
                # is what the resolved overload's `{0}` slot expands over --
                # the same bare pass a concrete scalar takes. Sema has
                # already checked the caller's bound against the picked
                # overload's own slot bound.
                tparam_arg = (
                    scalar_ctor
                    and _is_type_param_slot(analyzer.get_expr_type(a))
                    and _ctor_arg_slot_ok(p.type, analyzer)
                    and _witness("arg.type_ctor_tparam"))
                if (scalar_ctor and not str_parse and not int_tparam
                        and not tparam_arg
                        and not (_ctor_arg_slot_ok(p.type, analyzer)
                                 and (_resolved_scalar(
                                          analyzer.get_expr_type(a), analyzer)
                                      # A char-valued arg (`int(chr(65))`):
                                      # the chr template expands inline and
                                      # the ctor's cast consumes the char
                                      # like any scalar.
                                      or _eligible_char(
                                          analyzer.get_expr_type(a))))):
                    note_detail("call.type_ctor.scalar_arg")
                    raise ThirUnsupported(call_reject_reason("expr.call"))
                if slice_ctor and not (
                        isinstance(a, TpyNoneLiteral)
                        or _resolved_scalar(
                            analyzer.get_expr_type(a), analyzer)):
                    note_detail("call.type_ctor.slice_arg")
                    raise ThirUnsupported(call_reject_reason("expr.call"))
                if owned_str_ctor and not (
                        _str_pass_through_arg(a, p.type, declared, analyzer)
                        # `String(int32(42))` / `String(True)`: sema resolved
                        # the numeric ctor overload, whose own template does
                        # the conversion (`::tpy::fixed_to_str<int32_t>({0})`,
                        # `std::string(::tpy::bool_to_str({0}))`), so the
                        # scalar arg renders bare inside it.
                        or (_ctor_arg_slot_ok(p.type, analyzer)
                            and _resolved_scalar(
                                analyzer.get_expr_type(a), analyzer))):
                    # `String(view)` -- the positional `std::string({0})`
                    # expansion over a bare str-family arg.
                    note_detail("call.type_ctor.owned_str_arg")
                    raise ThirUnsupported(call_reject_reason("expr.call"))
                if _native_protocol_field_arg(a, p.type, analyzer):
                    # The free-call ladder's protocol-field row, mirrored:
                    # this loop calls `_lower_call_arg` with the protocol
                    # pre-arms OFF (such a slot renders bare here), so the
                    # bare member read at a still-protocol template slot
                    # (`str(p.name)` -> `std::string(::tpy::__str__(
                    # p->name))`) would otherwise fall to the generic tail.
                    # No whole-optional/container leg like the free-call
                    # row's: sema rejects an Optional / container / bytes
                    # arg at every type-ctor slot, so only the record and
                    # open-T member reads can arrive here.
                    _witness("arg.type_ctor_protocol_field")
                    lowered_args.append(_lower_expr(
                        a, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND)))
                    continue
                # `str(a)` on a union binding renders `::tpy::__str__(a)` --
                # the resolved overload's slot consumes the WHOLE union
                # (a same-union param, or a protocol whose template deduces
                # from the argument), so an assign-narrowed divergent read
                # is safe. Keyed structurally on that slot: a member-typed
                # overload slot (`std::string({0})`, `fixed_to_str<..>`)
                # stays fenced (the BUGS.md miscompile family).
                _sctor_a = _peel_coerce(a)
                _sctor_du = (
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        declared.get(_sctor_a.name))))
                    if isinstance(_sctor_a, TpyName) else None)
                _sctor_pt = (
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        p.type))) if isinstance(p.type, TpyType) else None)
                _sctor_union = (
                    isinstance(_sctor_du, UnionType)
                    and (_sctor_pt == _sctor_du
                         or (isinstance(_sctor_pt, NominalType)
                             and _sctor_pt.is_protocol)))
                lowered_args.append(_lower_call_arg(
                    a, p.type, lc, declared,
                    union_divergent_ok=_sctor_union))
            return THIRCall(
                result_type=rtype,
                callee=e.func_name,
                args=tuple(lowered_args),
                cpp_template=template_fi.cpp_template,
                loc=loc,
            )
        # A @native free-function builtin (currently `len` -> `tpy::__len__`) carries
        # its resolved symbol so the emit dispatches on it, not the source name.
        # An @error_return callee is admitted HERE only: the tail wraps it in
        # the statement-expression unwrap (or, under `error_return_raw`, hands
        # the bare expected call to the statement-level handlers).
        def _er_wrap(node: THIRExpr) -> THIRExpr:
            if (fi is None or fi.error_return_type is None
                    or error_return_raw):
                return node
            ret = fi.return_type
            value_form = not (ret is not None and not ret.is_value_type()
                              and not isinstance(ret, VoidType))
            return THIRErrorReturnUnwrap(
                result_type=rtype, call=node, value_form=value_form, loc=loc)

        # `copy(x)`: the bespoke special-builtin arm, shared with the
        # module-qualified spelling (`t.copy(s)`) via _lower_copy_special.
        if (fi is not None and fi.qualified_name == qnames.COPY
                and len(e.args) == 1 and not e.kwargs):
            return _lower_copy_special(e.args[0], e.func_name, rtype, lc,
                                       declared, loc)
        ffold = _lower_float_str_fold(e.resolved_function_info, e.args,
                                      e.func_name, rtype, loc,
                                      kwargs=bool(e.kwargs))
        if ffold is not None:
            return ffold
        k = _free_callee_kind(
            e, analyzer,
            generator_ok=use.result is _ExprResultUse.ITERABLE,
            error_return_ok=True,
            coro_factory_ok=use.admits(SinkForm.CORO_FACTORY),
            ret_cast_ok=True)
        len_call = _is_len_call(e, declared, analyzer, lc.pointers)
        if not len_call:
            if k is None or fi is None:
                raise ThirUnsupported(call_reject_reason("expr.call"))
            if not _call_arity_ok(e, fi):
                note_detail("call.arity_defaults")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            # A str literal into a str/StrView slot of a multi-overload callee
            # takes the `param_view_t("...")` pin (applied per-arg in
            # `_lower_free_call_arg`); a str literal into ANY other slot (a
            # `Literal[...]` mode selector, `char`, `String`) renders through
            # its own arm.
        native_name = fi.native_name if _is_len_native(e) else None
        if native_name is not None and isinstance(e.args[0], TpyFieldAccess):
            _witness("len.field_recv")
        # A str/bytes-slice call result carries its C++ shape: a view-returning
        # call yields a string_view/span (BORROW -- an owned sink copies it), an
        # owned-returning call a string/vector by value (STORAGE -- lands bare).
        view_t = _resolved_str_value(rtype, analyzer)
        if view_t is None:
            view_t = _resolved_bytes_value(rtype, analyzer)
        form = _viewfam_result_form(view_t)
        # Args lower against their param slots: a str literal in a char slot
        # renders as a char literal, a bytes literal into a bytes/BytesView
        # slot takes the static-span pin, a union-slot arg reads the
        # callee's deep-const verdict (`const_borrow_params`) for the
        # const-pointee spelling. A `len` call bypasses the arity gate, so
        # it lowers slot-less there. Provided args pair the LEADING params
        # (the zip truncates): the arity gate admits omitted trailing
        # defaults.
        params = (fi.params if fi is not None
                  and len(fi.params) >= len(e.args) else None)
        dcbp = fi.root.const_borrow_params if fi is not None else None
        # The callee's emit kind: the same classification validation admitted
        # on (`_free_callee_kind`) -- cross-module spelling on callee_cpp,
        # a C++ @native symbol on native_name (joining the len hardcode),
        # a positional-only @cpp_template on cpp_template.
        callee_cpp = None
        cpp_template = None
        if native_name is None:
            if k is not None and k[0] == "imported":
                callee_cpp = k[1]
                _witness("call.imported")
            elif k is not None and k[0] == "native":
                if (fi is not None and fi.native_cpp_return_type is not None
                        and fi.return_type is not None
                        and fi.error_return_type is None):
                    # Declared cpp_return_type: the call wraps in the
                    # narrowing static_cast.
                    # The error_return guard mirrors the admission gate
                    # (ret_cast_ok): the cast is SKIPPED under
                    # error_return, so composing it with the er-unwrap here
                    # would nest the wraps wrongly if the gate ever widens.
                    ph = ", ".join("{%d}" % i for i in range(len(e.args)))
                    cpp_template = (
                        f"static_cast<{lc.render_type(fi.return_type)}>"
                        f"({qualify_native_name(k[1])}({ph}))")
                    _witness("call.native_ret_cast")
                else:
                    native_name = k[1]
                    _witness("call.native_free")
            elif k is not None and k[0] == "native_c":
                # A C-linkage symbol: verbatim, unqualified (the extern "C"
                # re-declaration is namespace-scoped; `::` would miss it).
                callee_cpp = k[1]
                _witness("call.native_c_free")
            elif k is not None and k[0] == "template":
                cpp_template = k[1]
                _witness("call.template_free")
            elif k is not None and k[0] == "generic":
                # A plain TPy generic callee: explicit template args
                # (type_to_cpp_stored per inferred arg) over the plain /
                # imported spelling; args resolve against the ROOT stub's
                # params with the inferred substitution, a temporary arg
                # into a TypeParamRef ref-slot hoisting the resolved-typed
                # `__tmp_N` (TempState.create's to_cpp render).
                _witness("call.generic_free")
                return _er_wrap(_lower_generic_plain_call(
                    e, k[1] or None, lc, declared, temp_args=temp_args,
                    form=form, loc=loc))
        if native_name is None and k is not None and k[0] == "plain":
            # A same-module callee: the absolute-qualified spelling
            # (`::tpyapp::main::f`, or the literal-specialized mangling
            # `::tpyapp::main::pick__lit_r__w`). Qualified, not bare, so ADL
            # cannot pull a same-named `std::`/`::tpy::` template into the
            # overload set (see `free_callee_cpp`).
            callee_cpp = k[1]
            _witness("call.same_module")
            if free_literal_mangled_name(e, fi, analyzer) is not None:
                # The mangling is a distinct rename inside the qualified
                # spelling; keep its own census face so requalification did
                # not swallow the discriminator.
                _witness("call.literal_mangled")
        if (callee_cpp is None and cpp_template is None
                and native_name is None
                and _value_opt_callable(declared.get(e.func_name),
                                        analyzer) is not None):
            # A `Callable | None` binding's invocation unwraps the optional
            # (`f.value()(x)` -- keyed on the DECLARED type,
            # position-blind).
            callee_cpp = f"{escape_cpp_name(e.func_name)}.value()"
            _witness("call.opt_callable_unwrap")
        # Late import: comprehensions imports this module.
        from .comprehensions import reject_nonmovable_genexpr_arg

        def _free_arg(i: int, a: TpyExpr) -> THIRExpr:
            lowered = _lower_free_call_arg(
                e, a, params[i].type if params else None, k, lc, declared,
                temp_args=temp_args,
                arg_index=i,
                readonly_target=(params is not None
                                 and dcbp is not None
                                 and i in dcbp))
            # A lazy combinator callee moves a genexpr argument's closure
            # into its own storage; a non-movable closure rejects here
            # rather than in the C++ build.
            reject_nonmovable_genexpr_arg(e, a, lowered, analyzer)
            return lowered

        return _er_wrap(THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(_free_arg(i, a) for i, a in enumerate(e.args)),
            resolved_callee=(resolved_callee(fi, analyzer, arity=len(e.args))
                             if k is not None and k[0] in ("plain", "imported")
                             and not e.inferred_type_args else None),
            native_name=native_name,
            cpp_template=cpp_template,
            callee_cpp=callee_cpp,
            form=form,
            loc=loc,
        ))
    if isinstance(e, TpyListRepeat):
        # `[elems] * count`. A None/protocol/Span
        # target uses the sema-RESOLVED type (protocols have no concrete
        # container; a Span can't be range-constructed); otherwise the annotated
        # target drives the element/result types.
        repeat_slot = use.slot_target
        use_resolved = (repeat_slot is None
                        or is_protocol_type(repeat_slot)
                        or is_span(repeat_slot))
        # `rtype` is already resolve_pending_container'd above; a Span/protocol
        # target uses it (they have no range-constructible container form).
        result_type = rtype if use_resolved else repeat_slot
        elem_type = (result_type.get_element_type()
                     if result_type is not None else None)
        if isinstance(elem_type, IntLiteralType):
            elem_type = analyzer.ctx.default_int_type
            if is_list(result_type):
                result_type = make_list(elem_type)
        lazy = isinstance(result_type, ListRepeatType)
        if (elem_type is None or result_type is None
                or not (is_list(result_type) or is_array(result_type)
                        or lazy)):
            # Materialized list + Array + the LAZY repeat_range. A pending /
            # off-family result rejects here rather than reaching an
            # unresolved .to_cpp().
            raise ThirUnsupported("expr.list_repeat")
        elements = tuple(
            _lower_checked_container_elem(
                x, elem_type, lc, declared, threaded=True, forced=True,
                allow_record=True, allow_nested=True, allow_optional=True,
                retype_scalars=True, suppress_move=True)
            for x in e.elements)
        elem_cpp = elem_type.to_cpp()
        if is_array(result_type):
            # Aggregate build via array_from_index (the __rep_N counter draws at
            # emit); the stop bound rides the N template arg, count is unused.
            return THIRListRepeat(
                result_type=result_type, elements=elements, elem_cpp=elem_cpp,
                array_size_cpp=str(result_type.type_args[1]), loc=loc)
        count = _lower_expr(e.count, lc, declared)
        count_bigint = is_big_int_type(analyzer.get_expr_type(e.count))
        if lazy:
            _witness("list_repeat.lazy")
        return THIRListRepeat(
            result_type=result_type, elements=elements, count=count,
            count_bigint=count_bigint, elem_cpp=elem_cpp, lazy=lazy,
            result_cpp="" if lazy else result_type.to_cpp(), loc=loc)
    if isinstance(e, (TpyArrayLiteral, TpySetLiteral)):
        container_type = use.slot_target or rtype
        if not _container_literal_shape_ok(
                e, container_type, analyzer, threaded=container_threaded):
            raise ThirUnsupported("expr.container_literal")
        # A container-literal decl init / return / nested element. result_type
        # is the RESOLVED container (list vs Array already decided by sema);
        # the emit dispatches on its family. Elements lower through the
        # per-slot owned-str/bytes wraps (S5/S6), the F2 pointer-local deref,
        # and the last-use move rule. A LIST literal's SCALAR elements
        # render target-less (bare `{10, 20}` into the
        # vector's brace init) -- but a demoted/annotated ARRAY's and a set's
        # DO thread the element target (probe-verified: `std::array` elements
        # take the float32 `f` suffix / `::tpy::BigInt(N)` wraps a vector's
        # elements never get), so retype keys on the RESOLVED container kind,
        # not the literal's source shape.
        args = getattr(container_type, "type_args", None)
        slot = args[0] if args else None
        # `array_retype=False` is the native CALL-ARG position: an element
        # target threads only at Array DECLS (the `::tpy::BigInt(N)`
        # / `f`-suffix wraps), never into an arg literal -- its elements land
        # bare in the spelled aggregate. Set literals retype in both positions.
        retype = isinstance(e, TpySetLiteral) or (is_array(container_type)
                                                  and array_retype)
        if e.elements:
            _witness_container_elem_fam(slot, lc.analyzer)
        # The make_vector / make_ordered_set switch: std::initializer_list
        # elements are const, so a `std::move` in a brace-init would silently
        # copy -- a non-copyable or last-use-movable element forces the
        # reserve+emplace helper. std::array aggregate-init moves fine, so
        # the Array family never switches.
        make = False
        elem_cpp = None
        if e.elements and not is_array(container_type):
            if (any(_elem_needs_emplace(x, slot, lc) for x in e.elements)
                    or _container_nocopy_elem(slot, lc.analyzer)):
                make = True
                _witness("containerlit.make")
                if not isinstance(e, TpySetLiteral):
                    # make_vector spells its element type via the resolver;
                    # make_ordered_set derives its spelling from result_type
                    # in the emit (to_cpp, like the brace arm).
                    _su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        slot))) if slot is not None else None
                    # A UNION element slot spells its ALIAS name
                    # (`make_vector<Item>`) -- but `union_alias_names`, which
                    # the resolver reads, is only populated at header
                    # emission, after lowering. The sema-time display map is
                    # the lowering-visible source of the same name.
                    elem_cpp = (_union_storage_val_cpp(_su)
                                if isinstance(_su, UnionType)
                                else lc.render_type(slot))
        if make and any(isinstance(x, TpyArrayLiteral) and not x.elements
                        for x in e.elements):
            # An empty nested literal is spelling-free only under a BRACE-init
            # parent, which hands its element type down. The reserve+emplace
            # helpers deduce each element from its own argument instead, so a
            # bare `{}` there has nothing to deduce from and spelling the type
            # would not preserve the helper's move.
            note_detail("containerlit.make_empty_elem")
            raise ThirUnsupported("expr.container_literal")
        return THIRContainerLiteral(
            result_type=container_type,
            elements=tuple(
                _lower_checked_container_elem(
                    x, slot, lc, declared,
                    allow_temps=use.allow_temps,
                    threaded=(True if isinstance(e, TpySetLiteral)
                              else container_threaded),
                    forced=(isinstance(e, TpySetLiteral)
                            or is_array(container_type)),
                    # Set RECORD elements are admitted: the copyable-record
                    # moves / make_ordered_set path renders like the list
                    # rows. Nested/optional set elements stay conservative.
                    allow_record=True,
                    allow_nested=not isinstance(e, TpySetLiteral),
                    allow_optional=not isinstance(e, TpySetLiteral),
                    retype_scalars=retype,
                    # SET literals excluded: this arm serves list, Array AND
                    # set, and only the first two have a witnessed
                    # frame-emplace render. A non-value tuple in a set needs a
                    # hashable record element, so the shape may not exist at
                    # all -- but "probably unreachable" is what made the
                    # previous keying look safe, so it keeps the wrap.
                    frame_bare_tuple=not isinstance(e, TpySetLiteral))
                for x in e.elements),
            make_container=make,
            elem_cpp=elem_cpp,
            # An IMMEDIATE container element (`elem_storage`, which does not
            # propagate deeper) of a BRACE-init parent: the outer brace deduces
            # the element type, so an empty list drops its spelling. The
            # make_container parent, which deduces per argument instead, is
            # rejected above rather than reaching this render.
            bare_empty=elem_storage and not e.elements,
            loc=loc,
        )
    if isinstance(e, TpyDictLiteral):
        container_type = use.slot_target or rtype
        if not _container_literal_shape_ok(
                e, container_type, analyzer, threaded=container_threaded):
            raise ThirUnsupported("expr.container_literal")
        args = getattr(container_type, "type_args", None)
        kslot = args[0] if args else None
        vslot = args[1] if args and len(args) > 1 else None
        if e.keys:
            _witness_container_elem_fam(vslot, lc.analyzer)
        # make_ordered_map for a non-copyable or last-use-movable key/value
        # (the nocopy check reads the VALUE type only).
        make = bool(e.keys) and (
            any(_elem_needs_emplace(k, kslot, lc) for k in e.keys)
            or any(_elem_needs_emplace(v, vslot, lc) for v in e.values)
            or _container_nocopy_elem(vslot, lc.analyzer))
        if make:
            _witness("containerlit.make")
        return THIRContainerLiteral(
            result_type=container_type,
            elements=tuple(_lower_container_elem(k, kslot, lc, declared) for k in e.keys),
            values=tuple(
                _lower_checked_container_elem(
                    v, vslot, lc, declared, threaded=True, forced=True,
                    allow_record=True, allow_nested=True,
                    allow_optional=True)
                for v in e.values),
            make_container=make,
            loc=loc,
        )
    if isinstance(e, TpyMethodCall):
        if (e.resolved_function_info is not None
                and e.resolved_function_info.is_callable_value
                and not e.is_callable_field):
            return _lower_computed_call(e, e.obj, rtype, lc, declared, use)
        if e.fstr_expansion is not None:
            # An `@inline` METHOD call: sema stores the substituted body's
            # expression here (a FREE `@inline` call sets `macro_expansion`
            # instead), and it renders in place ahead of everything
            # else in the method branch, unlike its macro_expansion
            # neighbour. The expansion IS this node's value, so it inherits
            # the consumer's use, slot target included (a void `@inline`
            # body at a statement needs the DISCARD sink). Witness AFTER
            # it lowers:
            # `_witness` has no rollback, so witnessing first would mark the
            # face for a body that then raises.
            fstr_lowered = _lower_expr(e.fstr_expansion, lc, declared,
                                       use=use)
            _witness("call.fstr_expansion")
            return fstr_lowered
        er_fi = e.resolved_function_info
        if (not er_expr_unwrap and not error_return_raw and er_fi is not None
                and er_fi.error_return_type is not None):
            # An @error_return METHOD call in an EXPRESSION position -- the
            # free-call arm's `_er_wrap` mirror (the statement-level raw
            # handoff is `error_return_raw`, handled by its own callers).
            # Lower the call with the ER fi admitted at the plain-method gate
            # and wrap it in the statement-expression unwrap the emit already
            # spells; only the plain-method arm may answer, so no other arm
            # can hand back an un-unwrapped expected value.
            inner = _lower_expr(
                e, lc, declared, use=use,
                allow_whole_optional=allow_whole_optional,
                allow_unrouted_name=allow_unrouted_name,
                allow_union_divergent=allow_union_divergent,
                field_prechecked=field_prechecked,
                field_owned_str_ok=field_owned_str_ok,
                own_slot_coerce=own_slot_coerce,
                subscript_prechecked=subscript_prechecked,
                container_threaded=container_threaded,
                array_retype=array_retype,
                er_expr_unwrap=True)
            if not isinstance(inner, THIRMethodCall):
                note_detail("method.er_expr_shape")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            er_ret = er_fi.return_type
            _witness("method.er_expr_unwrap")
            return THIRErrorReturnUnwrap(
                result_type=rtype, call=inner,
                value_form=not (er_ret is not None
                                and not er_ret.is_value_type()
                                and not isinstance(er_ret, VoidType)),
                loc=loc)
        result_use = use.result
        if (result_use is _ExprResultUse.CONDITION
                and not is_bool_type(rtype)):
            raise ThirUnsupported(call_reject_reason("expr.method_call"))
        if e.deref_narrowed_to is not None:
            # A deref-view NARROWED member call (`b.bark()` under
            # `isinstance(b, Dog)` through a Deref wrapper): the receiver
            # reads the if-init cast pointer (`(*__b_ptr).bark()`), never
            # the deref chain. First dispatch of the arm -- every later
            # classifier fences deref_narrowed_to. Zero-arg plain methods
            # only (the witnessed slice); other flavors reject.
            dn_fi = e.resolved_function_info
            if not (isinstance(e.obj, TpyName)
                    and e.obj.name in lc.deref_view_spelled
                    and dn_fi is not None
                    and _plain_method_fi_ok(dn_fi)
                    and not e.args and not e.kwargs
                    and not dn_fi.params):
                note_detail("method.deref_narrowed_shape")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            dn_str = _resolved_str_value(rtype, analyzer)
            if dn_str is None:
                dn_str = _resolved_bytes_value(rtype, analyzer)
            _witness("method.deref_view_narrowed")
            return THIRMethodCall(
                result_type=rtype if rtype is not None else VoidType(),
                receiver=THIRName(
                    result_type=analyzer.get_expr_type(e.obj),
                    name=e.obj.name,
                    cpp=lc.deref_view_spelled[e.obj.name],
                    form=Form.VALUE, loc=loc),
                method_cpp=_method_member_cpp(dn_fi, e.method),
                args=(),
                form=_viewfam_result_form(dn_str),
                loc=loc)
        if e.typed_dict_get_field is not None:
            # TypedDict `kwargs.get("k"[, default])` -- the typed-dict
            # arm: `.field.value_or(default)` (owned-str defaults
            # take the std::string wrap), the statically-present
            # `((void)default, .field)` form, the bare read, or the
            # `std::make_optional(.field)` lift -- composed as a positional
            # template over the receiver (and default) renders. Bare
            # non-pointer name receivers only (the indirect form derefs).
            td_recv = e.obj
            if not (isinstance(td_recv, TpyName) and td_recv.name in declared
                    and td_recv.name not in lc.pointers
                    and td_recv.name not in lc.narrow.narrowed
                    and td_recv.name != lc.self_receiver):
                note_detail("method.typed_dict.recv")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            td_field = escape_cpp_name(e.typed_dict_get_field)
            td_obj = _lower_expr(
                td_recv, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER))
            if len(e.args) == 2:
                td_default = _lower_expr(e.args[1], lc, declared)
                if e.typed_dict_get_optional:
                    dwrap = ("std::string({1})"
                             if (isinstance(rtype, TpyType)
                                 and (is_str_type(unwrap_readonly(rtype))
                                      or is_string_type(
                                          unwrap_readonly(rtype))))
                             else "{1}")
                    td_tmpl = f"{{0}}.{td_field}.value_or({dwrap})"
                else:
                    td_tmpl = f"((void){{1}}, {{0}}.{td_field})"
                td_args: tuple = (td_obj, td_default)
            else:
                if e.typed_dict_get_optional:
                    td_tmpl = f"{{0}}.{td_field}"
                else:
                    rt0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        declared[td_recv.name])))
                    rec = (analyzer.registry.get_record_for_type(rt0)
                           if isinstance(rt0, NominalType) else None)
                    already_opt = bool(rec and any(
                        fld.name == e.typed_dict_get_field
                        and isinstance(fld.type, OptionalType)
                        for fld in rec.fields))
                    td_tmpl = (f"{{0}}.{td_field}" if already_opt
                               else f"std::make_optional({{0}}.{td_field})")
                td_args = (td_obj,)
            _witness("method.typed_dict_get")
            return THIRCall(
                result_type=rtype, callee=e.method, args=td_args,
                cpp_template=td_tmpl, loc=loc)
        if e.macro_expansion is not None:
            # `dataclasses.asdict(p)` / `.astuple(p)`: a `@call_macro` whose
            # sema-synthesized replacement renders IN PLACE (receiver-blind),
            # so the call node lowers to its expansion -- the free-call
            # ladder's `call.macro_expansion` row, which the marker gate
            # rejected outright.
            #
            # Witness AFTER the expansion lowers, never before: `_witness` has
            # no rollback, so witnessing first would mark the face for a body
            # that then RAISES (an `astuple` expansion is a bare tuple
            # literal with no arm). That inflated signal is how a dead arm
            # once passed the zero-witness check.
            expansion = _lower_expr(e.macro_expansion, lc, declared)
            _witness("call.macro_expansion")
            return expansion
        iterable_override = (
            result_use is _ExprResultUse.ITERABLE
            and (_dict_view_iterable_ok(
                    e, declared, analyzer,
                    methods=("values", "keys", "items"),
                    # The FIELD-receiver flavor (`dict_items(h.m)`) takes
                    # the same bare view-call render as the NAME one -- the
                    # override is receiver-blind past this admission (the
                    # receiver lowers through the field arm).
                    field_recv_ok=True)
                 or _str_list_method_iterable_ok(e, declared, analyzer)
                 or _member_gen_call_iterable_ok(e, declared, analyzer)))
        if e.is_nested_enum_constructor:
            if (not e.nested_type_name or e.kwargs
                    or e.double_star_unpack is not None or len(e.args) != 1):
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            nested_t = analyzer.registry.get_enum(e.nested_type_name)
            arg_type = analyzer.get_expr_type(e.args[0])
            if (nested_t is None or _eligible_enum(nested_t, analyzer) is None
                    or not _resolved_scalar(arg_type, analyzer)
                    or _runtime_bigint(arg_type, analyzer)):
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            # `Outer.Kind(v)` -> `::tpy::EnumUtil<Outer::Kind>::from_value(v)`
            # -- the nested-enum arm. Spelled via enum_cpp_name
            # like the top-level E(x) arm -- `Outer::Kind` locally, qualified
            # cross-module.
            _witness("enum.nested_from_value")
            spelled = enum_cpp_name(nested_t, analyzer.ctx.module_name)
            return THIRCall(
                result_type=rtype, callee=e.method,
                args=(_lower_expr(e.args[0], lc, declared),),
                cpp_template=(f"::tpy::EnumUtil<{spelled}>"
                              "::from_value({0})"),
                loc=loc)
        if e.is_nested_constructor:
            # `Outer.Inner(args)` -> `Outer::Inner(args)`. The type spelling
            # is `NominalType(nested_type_name).to_cpp()` (module-qualified
            # cross-module); args render plainly, exactly like the enum arm.
            if (not e.nested_type_name or e.kwargs
                    or e.double_star_unpack is not None):
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            _witness("ctor.nested_record")
            _nested_res = unwrap_readonly(unwrap_ref_type(rtype))
            if (isinstance(_nested_res, NominalType)
                    and _nested_res.type_args):
                # A GENERIC nested record spells its resolved arguments, the
                # way the top-level sibling does. Leaving them to CTAD only
                # ever worked by accident: a constructor parameter whose slot
                # is derived from `T` (`::tpy::readonly_form_t<T>`) is a
                # non-deduced context, so `Outer::Box(7)` has nothing to
                # deduce from.
                type_cpp = lc.render_type(_nested_res)
            else:
                type_cpp = NominalType(e.nested_type_name).to_cpp()
            return THIRCtorCall(
                result_type=rtype, type_cpp=type_cpp,
                args=tuple(_lower_expr(a, lc, declared) for a in e.args),
                form=Form.STORAGE, loc=loc)
        # A plain generic METHOD call's type args ride the member spelling
        # (`recv.method<targs>(args)`), so they are not a marker here; they
        # compose with an unproven-Optional receiver's checked deref
        # (`::tpy::deref_check(p).conv<int32_t>(3)`).
        if e.is_callable_field and not e.kwargs \
                and e.double_star_unpack is None:
            # Callable-field invocation (`h.cb(3)` -> `h.cb(3)`): the bare
            # member call over target-less args. An Optional[Callable]
            # field appends an unconditional `.value()` unwrap
            # (`(*this).on_event.value()(msg)` -- narrowing-blind, a plain
            # string append). Non-name receivers reject.
            recv = e.obj
            # A `self` receiver renders the deref (`(*this).on_event(x)`)
            # -- THIRSelf.deref spells exactly that, so the plain-method
            # receiver is admitted alongside local receivers.
            cf_self = (isinstance(recv, TpyName) and recv.name == "self"
                       and lc.prescan.has_self and lc.self_is_pointer
                       and recv.name not in lc.narrow.narrowed)
            # A resumable method coro's `self` is the `Record&` frame field
            # (`__self`): the member call spells the plain dot
            # (`__self._cb(...)`) -- the non-pointer self flavor, no deref.
            cf_self_ref = (isinstance(recv, TpyName) and recv.name == "self"
                           and lc.prescan.has_self and not lc.self_is_pointer
                           and lc.resumable_leaf_mode
                           and recv.name not in lc.narrow.narrowed)
            cf_ok = (cf_self or cf_self_ref
                     or (isinstance(recv, TpyName) and recv.name in declared
                         and recv.name not in lc.pointers
                         and recv.name not in lc.narrow.narrowed
                         and not (lc.prescan.has_self
                                  and recv.name == "self")))
            if cf_ok:
                rt0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(recv) if (cf_self or cf_self_ref)
                    else declared[recv.name])))
                ft = None
                ri = (analyzer.registry.get_record_for_type(rt0)
                      if isinstance(rt0, NominalType) else None)
                if ri is not None:
                    for fld in ri.fields:
                        if fld.name == e.method:
                            ft = fld.type
                            break
                cf_ok = ft is not None and _f1_record(rt0, analyzer)
            if not cf_ok:
                note_detail("method.marker.callable_field")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            cf_unwrap = isinstance(ft, OptionalType)
            _witness("method.opt_callable_field" if cf_unwrap
                     else "method.callable_field")
            cf_recv = _lower_expr(recv, lc, declared)
            cf_info = e.resolved_function_info
            assert cf_info is not None
            cf_args = []
            for i, arg in enumerate(e.args):
                if _container_field_bare_read(arg, declared, analyzer):
                    _witness("cfield.container_arg")
                cf_args.append(_lower_free_call_arg(
                    e, arg, cf_info.params[i].type, ("local", ""), lc, declared,
                    temp_args=use.allow_temps, readonly_target=False,
                    arg_index=i))
            return THIRMethodCall(
                result_type=rtype if rtype is not None else VoidType(),
                receiver=cf_recv,
                method_cpp=escape_cpp_name(e.method),
                callable_value_unwrap=cf_unwrap,
                args=tuple(cf_args),
                form=Form.VALUE, loc=loc)
        if not _plain_member_call_markers_ok(e, targs_ok=True):
            if _ptr_deref_method_call(e, analyzer):
                # The Ptr-receiver Deref call: `p->m(args)` when proven
                # non-null, `::tpy::deref_check(p).m(args)` otherwise --
                # the node fact `ptr_non_null` picks the arm. Args lower
                # like the qualified-marker call's (the same first-pass
                # loop); the receiver is a raw value read (a Ptr local /
                # field renders bare -- never indirect).
                _witness("method.ptr_arrow" if e.ptr_non_null
                         else "method.ptr_checked")
                pfi = e.resolved_function_info
                if (not _ptr_deref_recv_ok(e, declared, analyzer)
                        or not _marker_call_supported(
                            e, ("qualified", ""), declared, analyzer,
                            stmt_position=result_use is _ExprResultUse.DISCARD,
                            # A record-returning Ptr method under a postfix
                            # member (`s.Config.v` -- the property-getter
                            # receiver) renders bare; so does an RVALUE
                            # result at the record field-write copy sink
                            # (`task._waker = handle->make_waker(..);` --
                            # the copy-assign absorbs the prvalue). A
                            # borrow-returning result keeps rejecting on
                            # both: a decl binds REF_ALIAS off it.
                            record_ret_ok=(
                                result_use is _ExprResultUse.RECEIVER
                                or (use.admits(SinkForm.RECORD_COPY)
                                    and is_rvalue_source(analyzer, e))),
                            # An owned-tuple result at the owning `Own[tuple]`
                            # arg slot binds bare, like every other family
                            # this arm forwards.
                            own_tuple_slot_ret_ok=(
                                result_use is _ExprResultUse.OWN_SLOT))):
                    raise ThirUnsupported(call_reject_reason("expr.method_call"))
                p_str = _resolved_str_value(rtype, analyzer)
                if p_str is None:
                    p_str = _resolved_bytes_value(rtype, analyzer)
                mc_arrow, mc_deref = _ptr_recv_access(e.ptr_non_null)
                return THIRMethodCall(
                    result_type=rtype if rtype is not None else VoidType(),
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                        field_prechecked=isinstance(e.obj, TpyFieldAccess),
                        subscript_prechecked=isinstance(e.obj, TpySubscript)),
                    method_cpp=escape_cpp_name(e.method),
                    args=tuple(
                        _lower_marker_method_arg(
                            e, a, pfi.params[i].type, i, lc, declared,
                            temp_args=temp_args)
                        for i, a in enumerate(e.args)),
                    is_arrow=mc_arrow,
                    deref_check=mc_deref,
                    form=_viewfam_result_form(p_str),
                    loc=loc,
                )
            if _user_deref_method_call_ok(
                    e, declared, lc.narrow.narrowed, analyzer, lc.pointers):
                # A method call auto-dereffed through a USER Deref wrapper:
                # `r.__deref__()...m(args)` (N = deref_depth), a bare `.`
                # receiver access -- the field-read arm's method twin. Args
                # lower like the qualified-marker call's. The record-result
                # verdict mirrors the plain member arm's (RECEIVER /
                # rvalue-into-storage sinks, plus the with-manager slot --
                # `with arc_m.lock() as g:` derefs through Arc).
                dfi = e.resolved_function_info
                if not _marker_call_supported(
                        e, ("qualified", ""), declared, analyzer,
                        stmt_position=result_use is _ExprResultUse.DISCARD,
                        record_ret_ok=(
                            result_use is _ExprResultUse.RECEIVER
                            or use.admits(SinkForm.CTX_MANAGER)
                            or (result_use in (_ExprResultUse.BORROW_BIND,
                                               _ExprResultUse.STORAGE)
                                and is_rvalue_source(analyzer, e)))):
                    raise ThirUnsupported(call_reject_reason("expr.method_call"))
                d_str = _resolved_str_value(rtype, analyzer)
                if d_str is None:
                    d_str = _resolved_bytes_value(rtype, analyzer)
                _witness("method.user_deref_chain")
                return _self_recv_positioned(THIRMethodCall(
                    result_type=rtype if rtype is not None else VoidType(),
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                    method_cpp=escape_cpp_name(e.method),
                    args=tuple(
                        _lower_marker_method_arg(
                            e, a, dfi.params[i].type, i, lc, declared,
                            temp_args=temp_args)
                        for i, a in enumerate(e.args)),
                    is_arrow=_name_recv_is_arrow(e.obj, lc),
                    deref_chain=e.deref_depth,
                    form=_viewfam_result_form(d_str),
                    loc=loc,
                ))
            if _user_deref_stub_method_ok(
                    e, declared, lc.narrow.narrowed, analyzer, lc.pointers):
                # A container-stub MEMBER call through the Deref chain
                # (`g.append(4)` -> `g.__deref__().push_back(4)`): the
                # payload's member-rename native over the deref receiver.
                # Shape/args ride the container family's gates directly
                # (the family dispatch keys on receiver TYPE, which here is
                # the wrapper, so the fns are called explicitly); args
                # render via the stub loop (raw param threading).
                sfi = e.resolved_function_info
                if not _call_arity_ok(e, sfi):
                    note_detail("method.arity_defaults")
                    raise ThirUnsupported(call_reject_reason("expr.method_call"))
                # The container family's ret core, with a DISCARDED
                # storage result admitted (`g.get();` renders the bare
                # member statement -- byte-verified; the family fn's
                # stmt_storage_ok=False predates a witness).
                if not _stub_method_ret_ok(
                        analyzer.get_expr_type(e), analyzer,
                        stmt_position=result_use is _ExprResultUse.DISCARD,
                        storage_ret_ok=result_use is _ExprResultUse.STORAGE,
                        enum_ok=True, ptr_ok=True, callable_ok=True,
                        span_storage_ok=True, stmt_storage_ok=True):
                    note_detail("method.ret_type")
                    raise ThirUnsupported(call_reject_reason("expr.method_call"))
                for i, a in enumerate(e.args):
                    if not _stub_method_arg_ok(
                            a,
                            sfi.params[i].type if i < len(sfi.params)
                            else None,
                            declared, analyzer,
                            param_names=lc.prescan.param_names,
                            narrowed=frozenset(lc.narrow.narrowed)):
                        raise ThirUnsupported(call_reject_reason("expr.method_call"))
                s_str = _resolved_str_value(rtype, analyzer)
                if s_str is None:
                    s_str = _resolved_bytes_value(rtype, analyzer)
                _witness("method.user_deref_stub")
                return _self_recv_positioned(THIRMethodCall(
                    result_type=rtype if rtype is not None else VoidType(),
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                    is_arrow=_name_recv_is_arrow(e.obj, lc),
                    method_cpp=sfi.native_name,
                    args=tuple(
                        _lower_call_arg(
                            a,
                            sfi.params[i].type if i < len(sfi.params)
                            else None,
                            lc, declared, method_arg=True,
                            method_arg_stub=True)
                        for i, a in enumerate(e.args)),
                    deref_chain=e.deref_depth,
                    form=_viewfam_result_form(s_str),
                    loc=loc,
                ))
            # A receiver-less marker call (module-qualified / static): the
            # classifier selected it through _marker_call_kind, so the same
            # classification names the emit arm -- the pre-rendered
            # qualified spelling on callee_cpp or the @native symbol on
            # native_name, both existing THIRCall arms. Args lower against
            # their param slots like a free call's, but dcbp-BLIND
            # (readonly_target stays False): the method-call arg loop
            # lowers union args without the deep-const verdict.
            # `float("nan"/"inf"/...)` spelled through the builtins-module
            # marker (`builtins.float(...)` resolves as a module-qualified
            # ctor): the same fold as the free-call arm, ahead of the marker
            # classification that would reject the builtin-module ctor.
            _ffi = e.resolved_function_info
            ffold = _lower_float_str_fold(
                _ffi, e.args, e.method,
                rtype if rtype is not None else VoidType(), loc,
                kwargs=bool(e.kwargs))
            if ffold is not None:
                return ffold
            # The module-qualified copy spelling (`t.copy(s)`): the same
            # special-builtin arm as the free call, intercepted ahead of
            # the module dispatch.
            if (_ffi is not None and _ffi.qualified_name == qnames.COPY
                    and len(e.args) == 1 and not e.kwargs):
                return _lower_copy_special(e.args[0], e.method, rtype, lc,
                                           declared, loc)
            # The overload-seam-aware verdict: a stub fi carries
            # is_generator=False while the impl is the generator.
            iterable_gen = (result_use is _ExprResultUse.ITERABLE
                            and _genfac_like_call(e, analyzer))
            mk = _marker_call_kind(
                e, analyzer, generator_ok=iterable_gen,
                coro_factory_ok=use.admits(SinkForm.CORO_FACTORY),
                error_return_ok=error_return_raw)
            # An F1-record result renders bare under a postfix member
            # (RECEIVER) and, when it is an RVALUE source, directly into the
            # owned-record decl / storage slot (`Rc<A> r = Rc.new_(...);`) --
            # the free-call value-position mirror. A borrow-returning callee
            # is not an rvalue source and rejects (the decl sink copies it
            # through machinery this arm does not spell).
            record_ret = (result_use is _ExprResultUse.RECEIVER
                          # A with-manager rvalue (`with io.StringIO(s) as f:`):
                          # the record lands in the owned `auto __ctx_N` capture
                          # -- the record-method ladder's ctx_manager twin.
                          or use.admits(SinkForm.CTX_MANAGER)
                          or (result_use in (_ExprResultUse.BORROW_BIND,
                                             _ExprResultUse.STORAGE,
                                             # A for-source capture
                                             # (`async for v in svc.Ticker(3)`)
                                             # consumes the record rvalue
                                             # whole, like the manager sink.
                                             _ExprResultUse.ITERABLE)
                              and is_rvalue_source(analyzer, e)))
            if (mk is None or not _marker_call_supported(
                    e, mk, declared, analyzer,
                    stmt_position=result_use is _ExprResultUse.DISCARD,
                    record_ret_ok=record_ret,
                    moved_ret_ok=result_use is _ExprResultUse.SUSPEND,
                    iterable_gen_ok=iterable_gen,
                    owned_tuple_ret_ok=(
                        result_use is _ExprResultUse.STORAGE
                        and use.admits(SinkForm.TUPLE_SOURCE)),
                    storage_ret_ok=result_use is _ExprResultUse.STORAGE,
                    value_opt_ret_ok=allow_whole_optional,
                    coro_factory_ok=use.admits(SinkForm.CORO_FACTORY),
                    iterable_ret_ok=result_use is _ExprResultUse.ITERABLE)):
                if mk is None:
                    note_detail(_marker_reject(e, analyzer))
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            mfi = e.resolved_function_info
            _witness("call.module_native" if mk[0] == "native"
                     else "call.static_template" if mk[0] == "template"
                     else "call.generic_qualified" if mk[0] == "generic_qualified"
                     else "call.generic_static" if mk[0] in (
                         "generic_static", "generic_module_static")
                     else "call.super_generic" if mk[0] == "super_generic"
                     else "call.marker_qualified")
            mk_str = _resolved_str_value(rtype, analyzer)
            if mk_str is None:
                mk_str = _resolved_bytes_value(rtype, analyzer)
            # A module-qualified generic call spells explicit template args
            # as type_to_cpp(unwrap_ref_type) per inferred arg (NOT the
            # free-call arm's to_cpp_stored). A
            # same-module generic STATIC splits them into class/method args
            # over the composed callee (`Cls<CA>::template m<MA>`).
            callee_cpp = mk[1] if mk[0] in ("qualified", "generic_qualified",
                                            "super_generic") else None
            mk_targs = (tuple(lc.render_type(unwrap_ref_type(t))
                              for t in e.inferred_type_args)
                        if mk[0] in ("generic_qualified", "super_generic")
                        else None)
            if mk[0] == "generic_static":
                callee_cpp, mk_targs = _generic_static_callee(e, lc)
            elif mk[0] == "generic_module_static":
                callee_cpp, mk_targs = _generic_module_static_callee(e, lc)
            return THIRCall(
                result_type=rtype if rtype is not None else VoidType(),
                callee=e.method,
                resolved_callee=(resolved_callee(mfi, analyzer, arity=len(e.args))
                                 if mk[0] == "qualified" and not e.inferred_type_args else None),
                args=tuple(
                    _lower_marker_method_arg(
                        e, a, mfi.params[i].type, i, lc, declared,
                        temp_args=temp_args,
                        error_return_ok=error_return_raw,
                        # GENERIC module callees run the same first-pass
                        # hoist (`auto __tmp_N = {literal};` before
                        # `cycle<int32_t>(__tmp_N)`); only
                        # native/template callees skip it.
                        protocol_hoist=mk[0] in ("qualified",
                                                 "generic_qualified"))
                    for i, a in enumerate(e.args)),
                native_name=mk[1] if mk[0] == "native" else None,
                callee_cpp=callee_cpp,
                cpp_template=mk[1] if mk[0] == "template" else None,
                template_args_cpp=mk_targs,
                form=_viewfam_result_form(mk_str),
                loc=loc,
            )
        fi = e.resolved_function_info
        # Builtin-stub receivers thread the raw param type into numeric
        # literal renders (set below by the family dispatch); the
        # iterable-override path keeps the user-method default.
        stub_recv = False
        # The literal-mangled member spelling (`r.get__lit_age("age")`);
        # computed ahead of the branch so the member tail can read it on
        # every path (the iterable-override gates reject literal fis, so it
        # stays None there).
        lit_member = method_literal_mangled_cpp(e, analyzer)
        if not iterable_override:
            if isinstance(e.obj, TpyName):
                if e.obj.name not in declared:
                    note_detail("method.recv.name_absent")
                    raise ThirUnsupported(call_reject_reason("expr.method_call"))
            elif not (_method_nonname_receiver_ok(e.obj, declared, analyzer,
                                                  frozenset(lc.pointers))
                      # An unproven ptr-Optional CALL receiver: the raw
                      # `T*` result feeds the deref_check member render
                      # (`::tpy::deref_check(find(...)).mag()` -- the call
                      # sibling of the checked NAME receiver).
                      or (e.needs_optional_runtime_check
                          and _optional_checked_recv_call(e.obj, analyzer)
                          and _witness("method.opt_check_call_recv"))
                      # A `Ptr[T]`-returning CALL receiver of a
                      # @cpp_template member: the template expands over the
                      # receiver render, so the rvalue pointer interpolates
                      # like the NAME/FIELD receivers already admitted.
                      or (_ptr_template_call_recv_ok(e, analyzer)
                          and _witness("method.ptr_template_call_recv"))):
                note_detail(_recv_shape_reject(e.obj, declared, analyzer))
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            if (e.needs_optional_runtime_check
                    # The WIDE pointee class: the checked deref is
                    # pointee-blind (a `T*` null check plus a `.` member
                    # call), so a @dynamic-protocol / wrapper pointee reads
                    # the same as an F1-record one.
                    and _optional_ptr_borrow_wide_name(
                        e.obj, declared, analyzer) is None
                    and not _optional_checked_recv_call(e.obj, analyzer)):
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            # A consuming method's receiver-move render is spelled only for
            # a bare non-pointer, non-narrowed name (never `self`): exactly
            # that shape wraps in `std::move(name)`; indirect receivers
            # compose a deref the emit does not carry. An RVALUE call
            # receiver (`poll_once(aw).value()`) is already an rvalue -- the
            # move is for NAME receivers only, so it renders bare
            # (move_receiver stays off for it below).
            consuming_ok = (
                (isinstance(e.obj, TpyName)
                 and e.obj.name in declared
                 and e.obj.name != lc.self_receiver
                 # A POINTER-LOCAL receiver moves its deref
                 # (`std::move(*w).take()` -- the emit folds the arrow
                 # into the deref); narrowed names keep their own spells.
                 and e.obj.name not in lc.narrow.narrowed
                 and e.obj.name not in lc.inline_narrowed)
                or (isinstance(e.obj, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, e.obj)))
            if fi is None:
                # A post-sema function-macro-synthesized member call carries
                # no FunctionInfo -- it renders the generic member tail
                # (`c.bump()`: a plain `.` member read, no fi facts).
                # Slice: a declared plain F1-record NAME
                # receiver, arg-free, marker-free, scalar result.
                if (isinstance(e.obj, TpyName) and e.obj.name in declared
                        and e.obj.name not in lc.pointers
                        and e.obj.name not in lc.narrow.narrowed
                        and e.obj.name not in lc.inline_narrowed
                        and not e.args and not e.kwargs
                        and e.super_parent_type is None
                        and e.unbound_self_parent_type is None
                        and not e.is_static_call
                        and not e.is_nested_constructor
                        and e.typed_dict_get_field is None
                        and not e.inferred_type_args
                        and lit_member is None
                        and not e.needs_optional_runtime_check
                        and _f1_record(unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(declared[e.obj.name]))),
                            analyzer)
                        and _eligible_scalar(rtype)):
                    _witness("method.no_fi_member")
                    return _self_recv_positioned(THIRMethodCall(
                        result_type=rtype,
                        receiver=_lower_expr(
                            e.obj, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                        method_cpp=escape_cpp_name(e.method),
                        # An indirect receiver reaches the member through the
                        # pointer, `self` included -- the fact-free tail still
                        # renders the same accessor.
                        is_arrow=_name_recv_is_arrow(e.obj, lc),
                        args=(), loc=loc))
                note_detail("method.fi_kind")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            if not _plain_method_fi_ok(
                    fi, property_getter_ok=True, property_setter_ok=True,
                    coro_factory_ok=use.admits(SinkForm.CORO_FACTORY),
                    consuming_ok=consuming_ok,
                    error_return_ok=error_return_raw or er_expr_unwrap,
                    # The plain-method tail composes the cpp_return_type
                    # static_cast wrap, so the annotation is admitted here.
                    ret_cast_ok=True,
                    # A literal-overloaded member spells the mangled name;
                    # only the RECORD arm below renders it, so the builtin
                    # receiver families re-fence before their arms.
                    literal_mangled_ok=lit_member is not None):
                note_detail("method.fi_kind")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            if not _call_arity_ok(e, fi):
                note_detail("method.arity_defaults")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))

            if (fi.native_function and fi.native_name and not fi.cpp_template
                    and not e.args and not fi.is_consuming
                    and isinstance(e.obj, TpyName)
                    and e.obj.name in declared
                    and e.obj.name not in lc.pointers
                    and e.obj.name not in lc.narrow.narrowed
                    and e.obj.name not in lc.inline_narrowed
                    and _protocol_auto_slot(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(rtype))))):
                # `b.__iter__()` -> `::tpy::__iter__(b)`: the free-function
                # form of a `@native(..., function=True)` method -- the
                # receiver is the first (only) arg of the qualified native
                # symbol (the native_function tail). Sliced
                # to the zero-arg protocol-result shape (the explicit
                # `__iter__()` family); everything else keeps rejecting at
                # the family gates below.
                _witness("method.native_function_form")
                return THIRCall(
                    result_type=rtype,
                    callee=e.method,
                    args=(_lower_expr(e.obj, lc, declared),),
                    cpp_template=(f"{qualify_native_name(fi.native_name)}"
                                  "({0})"),
                    loc=loc)

            recv_type = _method_receiver_type(e.obj, declared, analyzer)
            stmt_position = (result_use is _ExprResultUse.DISCARD
                             or use.admits(SinkForm.TRUTHY_DISCARD))
            storage_ret_ok = result_use is _ExprResultUse.STORAGE
            # Builtin-stub receivers (container/set/str-view) render args
            # through the `_args()` loop, which THREADS the raw
            # param type into literal renders; user-record methods pass
            # target_type=None (target-less literals). The flag picks the
            # literal render in _lower_call_arg.
            fam = _method_recv_family(recv_type, analyzer, lc.tparam_bounds,
                                      e.method)
            if fam is not None and lit_member is not None:
                # A literal-overloaded member on a builtin-family receiver:
                # only the record arm carries the mangled spelling.
                # Defense-in-depth -- no builtin stub carries a literal
                # overload group reachable through a family receiver today,
                # so the shape has no constructible witness.
                note_detail("method.fi_kind")
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
            # An EXPLICITLY spelled dunder on a plain user-record receiver
            # (`not self.__eq__(other)` inside `__ne__`): sema stamps every
            # user dunder with its C++ operator template, so the call
            # renders as that operator -- the same template expansion the
            # Ptr / @native-record arm performs.
            record_template = (fam is None and recv_type is not None
                               and fi.cpp_template is not None
                               and not recv_type.is_pointer()
                               and not _native_record_recv(recv_type, analyzer)
                               and _f1_record(recv_type, analyzer))
            if fam is not None:
                shape_ok = fam.shape_ok(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position,
                    storage_ret_ok=storage_ret_ok,
                    borrow_ret_ok=result_use in (_ExprResultUse.RECEIVER,
                                                 _ExprResultUse.BORROW_BIND))
                stub_recv = fam.stub_recv
            elif recv_type is not None and (
                    recv_type.is_pointer()
                    # A @cpp_template method OR a function=True native on a
                    # @native record receiver (`v.count` ->
                    # `static_cast<int32_t>(v.size())`; `v.pop_last()` ->
                    # `::tpy::pop_back(v)`) expands like the Ptr arm's;
                    # @native member renames keep the record path below.
                    or ((fi.cpp_template is not None
                         or (fi.native_function and bool(fi.native_name)))
                        and _native_record_recv(recv_type, analyzer))
                    or record_template):
                shape_ok = _ptr_template_method_supported(
                    e, fi, recv_type, analyzer, declared, lc.pointers,
                    stmt_position=stmt_position,
                    record_ret_ok=result_use in (_ExprResultUse.BORROW_BIND,
                                                  _ExprResultUse.RECEIVER),
                    record_template=record_template)
                if shape_ok:
                    _witness("method.record_template" if record_template
                             else ("method.ptr_template" if fi.cpp_template
                                   else "method.ptr_native_member"))
            else:
                shape_ok = _record_method_call_supported(
                    e, fi, declared, analyzer,
                    use=use, result_use=result_use,
                    stmt_position=stmt_position,
                    allow_whole_optional=allow_whole_optional,
                    error_return_raw=error_return_raw,
                    narrowed=frozenset(lc.narrow.narrowed))
            if not shape_ok:
                raise ThirUnsupported(call_reject_reason("expr.method_call"))
        if fi is None:
            raise ThirUnsupported(call_reject_reason("expr.method_call"))
        # The member name resolves as: @native rename over the
        # literal-mangled overload spelling over the escaped source
        # name. A void method call carries no resolved expr type (None);
        # normalize so the node keeps a non-None result_type.
        if lit_member is not None:
            # The mangled name is assigned raw (no escape_cpp_name).
            _witness("method.literal_mangled")
            member = lit_member
        else:
            member = _method_member_cpp(fi, e.method)
        # A str-slice result carries its C++ shape like a THIRCall's (S5): an
        # owned-str method result (`xs.pop()`, std::string by value) is STORAGE
        # and lands bare in owned sinks. A bytes-family result rides the same
        # view/owned form tag (`bs.pop()` owned STORAGE, a bytes-view BORROW).
        m_str = _resolved_str_value(rtype, analyzer)
        if m_str is None:
            m_str = _resolved_bytes_value(rtype, analyzer)
        # Args lower against their param slots like a free call's (the record
        # pointer-local `(*p)` retag); provided args pair the LEADING params
        # (the zip truncates; the arity gate admits omitted trailing
        # defaults). A user-record F2 pointer-local receiver renders `->`, as does
        # the method receiver itself (`self.helper()` -> `this->helper()`).
        # `temp_args` admits only the VALUE-union temp row here (the other
        # temp rows are free-call shapes -- a method ctor rvalue INLINES).
        params = (fi.params if fi is not None
                  and len(fi.params) >= len(e.args) else None)
        # A protocol receiver misses the user-record arg loop
        # (guarded by `is_user_record`) and takes the lazy `_args()`
        # path, which renders args like a FREE call's -- literals take
        # their slot's coercion instead of the method path's target-less
        # spelling. Same source of truth as the gate's `_protocol_binding`
        # check: a narrowed subject reads as its member on both sides.
        recv_sema_t = analyzer.get_expr_type(e.obj)
        proto_recv = (_protocol_binding(recv_sema_t) is not None
                      or _bounded_tparam_protocol(
                          recv_sema_t, lc.tparam_bounds) is not None)
        if proto_recv:
            _witness("method.protocol")

        raw_method_fi = _raw_record_method_fi(e, declared, analyzer)

        def _method_arg(a: TpyExpr, ptype: 'TpyType | None',
                        index: int) -> THIRExpr:
            if isinstance(a, TpyVarargPack) and not proto_recv:
                # A `*args` pack on a user-record method: the same
                # pack render as a free call (the method arg loop forwards
                # the pack unchanged). Protocol receivers take the
                # free-call `_args()` path and reject here.
                # The slot's const-ness reads off the RAW method fi
                # -- on a GENERIC method the resolved
                # fi's substituted slot lacks the phase-2 vararg-readonly
                # wrapper the raw slot carries (base type still comes from
                # the pack, so the unsubstituted slot is const-only input).
                vslot = ptype
                if (raw_method_fi is not None
                        and index < len(raw_method_fi.params)):
                    vslot = raw_method_fi.params[index].type
                return _lower_vararg_pack(a, vslot, lc, declared,
                                          temp_args=temp_args)
            _require_method_call_arg(
                e, a, ptype, index, lc, declared, temp_args=temp_args)
            pin_slot = _strlit_method_pin_arg(
                e, e.resolved_function_info, a, ptype, lc.analyzer)
            if pin_slot is not None:
                # The method-side twin of the free call's str-literal pin: an
                # overloaded callee would bind a competing overload through
                # the `const char[N]` conversions.
                _witness("call.strlit_overload_pin")
                pinned = _lower_expr(a, lc, declared)
                return THIRCoerce(
                    result_type=ptype, expr=pinned,
                    coercion_name="strlit_overload_pin",
                    wrap=f"{pin_slot.to_cpp_param_type()}({{0}})",
                    form=pinned.form, loc=getattr(a, "loc", None))
            if temp_args:
                ut = _value_union_temp_slot(a, ptype, declared, lc.analyzer)
                if ut is not None and not (isinstance(a, TpyName)
                                           and (a.name in lc.narrow.narrowed
                                                or a.name in lc.inline_narrowed)):
                    _witness("argtemp.value_union_method")
                    init = (THIRLiteral(result_type=ut, value=None,
                                        form=Form.VALUE,
                                        loc=getattr(a, "loc", None))
                            if isinstance(a, TpyNoneLiteral)
                            else _lower_expr(a, lc, declared))
                    return THIRArgTemp(result_type=ut,
                                       cpp_type=lc.render_type(ut), movable=unwrap_ref_type(ut).is_movable(),
                                       init=init, form=Form.VALUE,
                                       loc=getattr(a, "loc", None))
                # A temporary into a generic-record method's RAW T slot
                # hoists the named temp with the substituted type
                # (`Point __tmp_N = Point(10, 20);`) BEFORE the inline
                # renders -- the TypeParamRef temp precedes the arg tail.
                tt = _tparam_slot_temp_arg(
                    a, ptype, index, raw_method_fi, lc.analyzer,
                    locals_=declared,
                    param_names=lc.prescan.param_names)
                if tt is not None:
                    _witness("argtemp.generic_ref_slot")
                    t_init = _lower_expr(a, lc, declared,
                                         use=_ExprUse(slot_target=tt))
                    return THIRArgTemp(
                        result_type=tt, cpp_type=tt.to_cpp(), movable=unwrap_ref_type(tt).is_movable(),
                        init=t_init,
                        form=Form.VALUE, loc=getattr(a, "loc", None))
                # A container LITERAL into a pointer-repr Optional[container]
                # slot: the typed `__tmp_N` + `&(__tmp_N)` face
                # (`s.get(url, None, {...})` -- the optional-ptr arg's
                # temporary face); the temp init is the target-threaded
                # literal render spelled at the slot's INNER.
                mcont = _optional_ptr_container_slot(ptype, lc.analyzer)
                if (mcont is not None
                        and _optional_ptr_container_literal_arg(
                            a, mcont, lc.analyzer)):
                    inner = unwrap_readonly(mcont.inner)
                    minit = _lower_expr(
                        a, lc, declared,
                        use=replace(_NESTED_ARG_USE, slot_target=inner))
                    # This temp's init renders through
                    # the SPELLED container ctor; a bare `{...}` brace-init
                    # would be the decl-slot render, not this one. The dict /
                    # set / empty-list spellings are already self-describing,
                    # so only the non-empty list brace needs the prefix.
                    if (isinstance(minit, THIRContainerLiteral)
                            and minit.typed_brace_cpp is None
                            and is_list(inner)):
                        minit = replace(
                            minit, typed_brace_cpp=lc.render_type(inner))
                    _witness("argtemp.optptr_container_literal")
                    return THIRArgTemp(
                        result_type=inner, cpp_type=lc.render_type(inner), movable=unwrap_ref_type(inner).is_movable(),
                        init=minit,
                        addr_of=True, form=Form.BORROW,
                        loc=getattr(a, "loc", None))
            # A generator/coro factory METHOD borrows its ref args in the
            # frame past the statement, so a temporary at a ref /
            # readonly-ref slot materializes as a named scope-local
            # (`Rec __tmp_N = Rec(41);`, `std::vector<int32_t> __tmp_N =
            # {5, 6, 7};` -- a mutable T& cannot bind an rvalue; a
            # readonly const T& would dangle once the frame resumes).
            # Sync methods keep their inline renders. Any other temporary
            # shape RAISES rather than fall through to an inline render
            # where a hoist is required.
            frame_factory = (not proto_recv and not stub_recv and fi is not None
                             and (fi.is_generator or fi.is_async)
                             and isinstance(ptype, TpyType))
            if frame_factory:
                fpr = unwrap_ref_type(unwrap_send_sync(ptype))
                fslot = unwrap_readonly(fpr)
                # No view guard here: every type `is_borrowing_view_type`
                # answers for is a VALUE type, so a view slot passes by value
                # and neither ref-param test below can hold on one. Only the
                # three FLAG-bearing dict views have that checked at
                # registration (`type_def_registry.register`); `Span`,
                # `varargs`, `SpanIter`, `StrView` and `BytesView` are
                # recognized by category/qname and hold the property by
                # construction, unguarded (TODO.md, widen the invariant). A
                # view that stopped being a value type would need this arm
                # re-checked against the borrowing-view row further down.
                if ((fpr.is_ref_param() or is_readonly_ref_param(fpr))
                        and isinstance(fslot, NominalType)
                        and not fslot.is_protocol
                        and materializing_temp_source(a, lc.analyzer)):
                    if not temp_args:
                        raise ThirUnsupported(
                            "factory-method borrow temp outside a flush "
                            "position")
                    if isinstance(a, (TpyArrayLiteral, TpyDictLiteral,
                                      TpySetLiteral)):
                        _witness("argtemp.gen_factory")
                        # The temp hoists at the slot's type and is movable
                        # off it, so the conditional-region defer matches.
                        return THIRArgTemp(
                            result_type=fslot, cpp_type=fslot.to_cpp(),
                            init=_lower_literal_arg(
                                a, fslot, lc, declared,
                                "factory-method container-literal arg on "
                                "the make path"),
                            movable=unwrap_ref_type(fslot).is_movable(),
                            form=Form.BORROW, loc=getattr(a, "loc", None))
                    if _record_rvalue_temp_slot(
                            a, ptype, lc.analyzer,
                            frame_capturing=True) is not None:
                        _witness("argtemp.gen_factory")
                        return THIRArgTemp(
                            result_type=fslot, cpp_type=fslot.to_cpp(),
                            init=_lower_expr(a, lc, declared,
                                             use=_RECORD_TEMP_FLUSH_USE),
                            movable=unwrap_ref_type(fslot).is_movable(),
                            form=Form.BORROW, loc=getattr(a, "loc", None))
                    # Any other temporary at a ref slot falls through to the
                    # uniform frame-temp row below, which hoists it.
            # The Own-slot copy half needs the flush threaded into
            # `_lower_call_arg`'s copy+move arm -- scoped to the own-lvalue
            # slot on a USER-record method (a stub receiver's cpp_template
            # binds lvalues natively, so its copy renders bare; a protocol
            # receiver admits no Own slots). A blanket temp_args would
            # re-shape the record-rvalue / union temp rows, whose method
            # renders are BARE.
            own_slot_w = _own_lvalue_temp_slot(a, ptype, lc.analyzer,
                                               declared,
                                               lc.prescan.param_names)
            own_flush = (temp_args and not proto_recv
                         and ((own_slot_w is not None
                               # A cpp_template stub binds lvalues natively
                               # (no temp, the bare pass-through renders) --
                               # EXCEPT an `Own[str]` slot, whose copy is the
                               # view->owned CONVERSION (the str-family
                               # carve-out), so its temp
                               # hoists on stub receivers too.
                               and (not stub_recv
                                    or is_str_type(own_slot_w)))
                              # The member-ctor-rvalue union lift hoists its
                              # own `__tmp_N` before the `pv{&__tmp_N}` wrap,
                              # exactly like the ctor loop's flush_slot.
                              or (not stub_recv
                                  and _union_ctor_temp_arg(a, ptype,
                                                           lc.analyzer))
                              # ... and the bytes-literal sibling
                              # (`s.post(url, b"payload")`).
                              or (not stub_recv
                                  and _union_bytes_literal_temp_arg(
                                      a, ptype, lc.analyzer) is not None)))
            # The deep-const verdict rides into the union const-wrap arm
            # (`z.names(other)` -> `other.as_const()`), like
            # the free-call loop. Read it off the RAW method fi: the
            # call-site substituted copy drops const_borrow_params
            # (substitute_method_type_params does not carry it), so a
            # generic receiver's inferred verdict lives only on the raw fi
            # -- the same source the arg gate checks. The `fi` fallback is
            # LIVE, not defensive: the property-setter path has no registry
            # overload, so gate and arm both read the resolved setter fi.
            dcbp_fi = raw_method_fi if raw_method_fi is not None else fi
            ro_slot = bool(dcbp_fi is not None
                           and dcbp_fi.const_borrow_params
                           and index in dcbp_fi.const_borrow_params)
            # A protocol slot on a USER-record method takes the same
            # protocol / @dynamic pre-arms as the free-call loop
            # (`canvas().draw(square(4))` -> the `Adapter<shape, square>
            # __tmp_N{..}` hoist), so the flush rides in for exactly that
            # slot -- `_protocol_arg_slot` is None everywhere else, keeping
            # the record-rvalue / union rows' bare method renders intact.
            proto_slot_flush = (temp_args and not proto_recv and not stub_recv
                                and _protocol_arg_slot(ptype) is not None)
            # The optional-ptr 'scalar_temp' face hoists its own typed
            # `__tmp_N` ahead of the `&(__tmp_N)` lift, exactly like the ctor
            # loop's flush_slot, so the statement flush rides in for that slot
            # too. Guarded to user-record receivers like its siblings: the
            # protocol ladder consults the face at temps_ok=False and a stub
            # receiver's own family owns the arg gate, so neither admits the
            # temp -- without the guard the render would hoist where no gate
            # verified the shape.
            optptr_scalar_flush = (
                temp_args and not proto_recv and not stub_recv
                and _optional_ptr_arg_face(a, ptype, declared,
                                           lc.analyzer) == 'scalar_temp')
            # Every other TEMPORARY argument of the same factory: the frame
            # keeps it past the statement whatever its slot does with it, so
            # it hoists to a named local too.
            return _frame_temp_arg(
                a, ptype, lc, lc.analyzer, active=frame_factory,
                temp_args=temp_args,
                no_flush_reason=("factory-method temp arg outside a "
                                 "flush position"),
                lower=lambda: _lower_call_arg(
                    a, ptype, lc, declared,
                    temp_args=(own_flush or proto_slot_flush
                               or optptr_scalar_flush),
                    nested_temps=temp_args,
                    method_arg=not proto_recv,
                    method_arg_stub=stub_recv and not proto_recv,
                    protocol_slots=proto_slot_flush,
                    readonly_target=ro_slot),
                lower_owned=lambda owned: _lower_call_arg(
                    _peel_coerce(a), owned, lc, declared, temp_args=True))

        if isinstance(e.obj, TpyName) and e.obj.name == lc.self_receiver:
            _witness("call.self_method")
        # An unproven Optional-ptr borrow receiver takes the runtime-check
        # render (`::tpy::deref_check(p).method(args)`); the marker carve-out
        # local admission permits it only on such a receiver. Mutually exclusive
        # with the indirect (`->`) arm -- the checked deref yields a reference.
        deref_check = e.needs_optional_runtime_check
        if not deref_check and isinstance(e.obj, TpyName):
            # The unproven raw-`Ptr[T]` NAME receiver of a plain @native
            # member takes the same checked spelling
            # (`::tpy::deref_check(s).outer()` -- the is_pointer arm; the
            # ptr-arm gate admitted exactly this face).
            _rb = declared.get(e.obj.name)
            recv_ptr = (_rb is not None and unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(_rb))).is_pointer())
            if _ptr_native_member_core(e, fi, recv_ptr):
                deref_check = True
        # A generic method's explicit template args (`b.transform<T>(42)`):
        # the method_targs suffix, spelled type_to_cpp over each
        # inferred arg. The static/module marker faces spell their own.
        # The node asserts not (deref_check and method_targs_cpp); the
        # inferred-type-args + needs_optional_runtime_check combination is
        # already rejected upstream (the deref-over-type-args guard), so both
        # are never set together here.
        method_targs = None
        if (e.inferred_type_args and not e.user_module_call
                and not e.is_static_call):
            method_targs = tuple(
                lc.render_type(unwrap_ref_type(t))
                for t in e.inferred_type_args)
        _mnu = (_assign_narrowed_union_recv(e.obj, declared, lc)
                if isinstance(e.obj, TpyName) else None)
        if (fi is not None and fi.is_generator
                and gen_recv_ctor_temp(e.obj, lc.analyzer)):
            # The generator-factory receiver lift: the frame/peephole
            # captures the receiver by reference, so the ctor rvalue hoists
            # into a named local (`Counter __tmp_N = Counter(..);`) flushed
            # at the consuming position (the for-head brace scope / the
            # iterator-object decl) -- the is_temporary lift.
            recv_t = lc.analyzer.get_expr_type(e.obj)
            _witness("method.gen_recv_temp")
            recv_lowered = THIRArgTemp(
                result_type=recv_t, cpp_type=lc.render_type(recv_t), movable=unwrap_ref_type(recv_t).is_movable(),
                init=_lower_expr(e.obj, lc, declared,
                                 use=_RECORD_TEMP_FLUSH_USE),
                form=Form.BORROW, loc=getattr(e.obj, "loc", None))
        elif _mnu is not None:
            # An ASSIGN-narrowed ptr-variant union NAME receiver: the
            # inline bare-get read (`(*std::get<Circle*>(c)).area()`) --
            # the field row's method twin; the gate dispatched on the
            # member record.
            _mnu_u, _mnu_member = _mnu
            _mnu_cpp, _mnu_ptr = _narrow_member_cpp(
                e.obj.name, _mnu_member, _mnu_u, lc)
            _witness("method.assign_narrowed_union")
            recv_lowered = THIRNarrowedRead(
                result_type=analyzer.get_expr_type(e.obj),
                variant_cpp=e.obj.name, member_cpp=_mnu_cpp,
                union_extraction=_union_extraction(e.obj.name, _mnu_u, _mnu_member, lc),
                is_ptr_variant=_mnu_ptr, form=Form.BORROW,
                loc=getattr(e.obj, "loc", None))
        else:
            recv_lowered = _lower_expr(
                e.obj, lc, declared,
                # A call-shaped receiver's own arg temps flush at the
                # enclosing statement like any nested arg's, so allow_temps
                # rides through. A cpp_template expansion and an @native free
                # function both consume the receiver as an ARGUMENT, a value
                # position -- so an indirect name derefs there
                # (`::tpy::__len__((*xs))`), where a real member call spells
                # `->` instead; both emit branches ignore `is_arrow`, so the
                # deref has to ride the name.
                #
                # The checked CALL receiver's `T*` result lands bare into
                # deref_check. Safe outside the iterable_override branch
                # only because all three override predicates reject
                # needs_optional_runtime_check -- keep that invariant if
                # adding a fourth.
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                             allow_temps=temp_args,
                             pos=SinkPos.RECEIVER,
                             forms=_recv_forms(
                                 (e.needs_optional_runtime_check
                                  and isinstance(e.obj,
                                                 (TpyCall, TpyMethodCall))),
                                 (fi is not None
                                  and (fi.cpp_template is not None
                                       or bool(fi.native_function
                                               and fi.native_name))))),
                field_prechecked=isinstance(e.obj, TpyFieldAccess),
                subscript_prechecked=isinstance(e.obj, TpySubscript))
        method_node = _self_recv_positioned(THIRMethodCall(
            result_type=rtype if rtype is not None else VoidType(),
            receiver=recv_lowered,
            method_cpp=member,
            args=tuple(
                _method_arg(a, params[i].type if params else None, i)
                for i, a in enumerate(e.args)),
            native_function_name=fi.native_name if fi.native_function else None,
            cpp_template=fi.cpp_template,
            method_targs_cpp=method_targs,
            is_arrow=not deref_check and (
                (isinstance(e.obj, TpyName)
                 and (_ptr_read_derefs(e.obj.name, lc)
                      # A poly-narrowed `self` reads through the cast
                      # pointer's deref (`(*__self_ptr)`), so the call is
                      # `.` -- the `this->` spelling is the un-narrowed
                      # receiver's.
                      or (e.obj.name == lc.self_receiver
                          and lc.self_is_pointer
                          and e.obj.name not in lc.narrow.spelled)))
                # A tuple-element record receiver: `std::get<N>(t)` is a
                # bare `T*` off a borrow-form tuple (`t[0]->get()` spelled
                # `std::get<0>(t)->get()`); a storage/owned element is a
                # value (`.`) -- the field-access arrow's shared rule.
                or (isinstance(e.obj, TpySubscript)
                    and _subscript_yields_borrow_ptr(e.obj, lc))
                # An `Own[@dynamic P]` receiver -- name or call rvalue --
                # is a unique_ptr, so the member access arrows.
                or _recv_own_dyn(e.obj, declared, analyzer)),
            deref_check=deref_check,
            move_receiver=(bool(fi.is_consuming)
                           and isinstance(e.obj, TpyName)
                           and _witness("method.consuming_move")),
            form=_viewfam_result_form(m_str),
            loc=loc,
        ))
        if (fi.native_cpp_return_type is not None
                and fi.return_type is not None
                and fi.error_return_type is None):
            # Declared cpp_return_type wraps the member call in the
            # narrowing static_cast (`c = v.cap` ->
            # `static_cast<int32_t>(v.capacity())`) -- a post-process on
            # the member call; skipped under
            # @error_return like the free-call lane.
            _witness("method.native_ret_cast")
            return THIRCoerce(
                result_type=rtype, expr=method_node,
                coercion_name="native_ret_cast",
                wrap=(f"static_cast<{lc.render_type(fi.return_type)}>"
                      "({0})"),
                form=method_node.form, loc=loc)
        return method_node
    if isinstance(e, TpyCoerce):
        if e.coercion.name == "into_any":
            return _lower_into_any(e, lc, declared, rtype, loc)
        if e.coercion.name == "from_any":
            return _lower_from_any(e, lc, declared, rtype, loc)
        # `own_slot_coerce` is threaded only by the Own-slot copy row, whose
        # temp init consumes the coerce whole -- there the `Own[str]` ARG
        # face materializes (see _coerce_disposition's own_slot_arg).
        disp = _coerce_disposition(e, own_slot_arg=own_slot_coerce)
        if disp is None:
            raise ThirUnsupported("expr.coerce")
        # A materializing coerce IS the owned sink for its view source, so a
        # StrView field inner is admitted here (the `std::string(x)` over
        # the bare member read renders through the S1 chokepoint below).
        # An `Optional[Span[...]]` expected peels ONE level for the span
        # arms (the coerce unwrap; Span is a value type,
        # never wrapped in Own).
        span_expected = e.expected_type
        if (isinstance(span_expected, OptionalType)
                and is_span(span_expected.inner)):
            span_expected = span_expected.inner
        if (e.coercion.name in _SPANLIKE_COERCIONS
                and isinstance(e.expr, TpyArrayLiteral)
                and is_span(span_expected)
                and getattr(span_expected, "type_args", None)):
            # The span coercion's literal arm: the helper wraps a
            # make_array-typed brace literal
            # (`as_mut_span(std::array<T, N>{...})`) -- thread the
            # synthesized Array target (element retype rides the Array
            # family) and stamp its spelled prefix on the brace init.
            arr_t = make_array(span_expected.type_args[0],
                               len(e.expr.elements))
            inner = _lower_expr(e.expr, lc, declared,
                                use=_ExprUse(slot_target=arr_t))
            inner = replace(inner, typed_brace_cpp=arr_t.to_cpp())
            _witness("coerce.span_array_literal")
        elif (e.coercion.name in _SPANLIKE_COERCIONS
              and isinstance(e.expr, TpyFieldAccess)
              and _field_markers_clean(e.expr)
              and _field_receiver_ok(e.expr, declared, lc.analyzer)):
            # A container FIELD inner (`return self._data` at a Span return
            # slot -- the @auto_readonly pair's body): the as_span /
            # as_mut_span helper wraps the bare member read, so the coerce
            # IS the field's consumer -- result admission is skipped
            # (field_prechecked) but the receiver shape is pre-validated
            # here, mirroring the ret_container_borrow arm's discipline.
            inner = _lower_expr(e.expr, lc, declared, field_prechecked=True)
        elif (e.coercion.name in _ADDR_PTR_COERCIONS
              and isinstance(e.expr, (TpyFieldAccess, TpySubscript))):
            # A record `.field` / `c[i]` element taken by address
            # (`&outer.inner` / `&::tpy::__getitem__(arr, i)`): lower the inner
            # as a RECEIVER so its F1-record lvalue read renders bare (the same
            # position the field/subscript member-access receiver admits),
            # which the `&{0}` wrap then takes the address of.
            # Soundness of the `&c[i]` form depends on `_subscript_container_recv_type`
            # restricting `c` to a bare name / one-level field (never a
            # temporary-returning call): widening that predicate to a call
            # result would make this `&(temporary)[i]` dangle past the
            # full-expression -- keep them in lockstep.
            inner = _lower_expr(
                e.expr, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER))
        else:
            # A str-family FIELD inner is admitted under a view-TARGET coerce
            # too (`return self.s` at a StrView slot): the coerce renders its
            # inner bare, so the member read IS the emitted form -- the same
            # read the materializing sink already threads, minus the copy.
            # Typed on the DECLARED field type: a NARROWED `str | None` field
            # would render `(*recv.field)` here, while the bare member read
            # is what the coerce spells (uncompilable -- see BUGS.md), so it
            # stays out.
            str_field_inner = (
                isinstance(e.expr, TpyFieldAccess)
                and _resolved_str_value(
                    _field_decl_type(e.expr, declared, lc.analyzer),
                    lc.analyzer) is not None)
            view_str_field = (
                str_field_inner
                and e.coercion.name in _VIEW_TARGET_STR_COERCIONS
                and _witness("coerce.str_field_view"))
            inner = _lower_expr(
                e.expr, lc, declared,
                # These coercions pre-deref an indirect-name inner
                # (`&(*q)` -- globals need the deref); the others take the
                # bare read. The flush right rides THROUGH the coerce like
                # every other expression nesting (a wrapped call's arg temps
                # flush at the same statement).
                use=_ExprUse(pos=SinkPos.COERCE_INNER,
                             forms=(
                                 None
                                 if (e.coercion.name
                                     in _INDIRECT_DEREF_COERCIONS)
                                 else _NO_FORMS),
                             allow_temps=use.allow_temps),
                # The Optional view<->str identity coerce consumes the WHOLE
                # optional (bare pass-through, no deref), so its value-opt
                # call-rvalue inner is a whole-optional read.
                allow_whole_optional=(
                    e.coercion.name in ("optional_strview_to_str",
                                        "optional_str_to_strview")),
                # Whatever the str-family coerce does with it, the member read
                # itself renders bare -- the wrap (materializing copy or
                # nothing) composes around it. Typed on the DECLARED field
                # type, so a narrowed `str | None` field stays unrouted.
                field_owned_str_ok=(
                    view_str_field
                    or str_field_inner
                    or (disp == "materialize"
                        and isinstance(e.expr, TpyFieldAccess))))
        if disp == "materialize":
            # The cross-type view->owned copy (`std::string(x)`) IS the S1
            # view->owned form transfer -- one emit chokepoint. The coerce
            # adds only the family-internal type respelling (StrView -> str /
            # String), carried on result_type. The explicit materialize flag
            # is what admits the bytearray twin (a reference type, where the
            # family does not determine the render).
            return THIRFormConvert(result_type=rtype, value=inner,
                                   form=Form.STORAGE, materialize=True,
                                   loc=loc)
        if (e.coercion.name == "bytes_to_bytesview"
                and isinstance(inner, THIRBytesLiteral)):
            # A LITERAL source at the view coerce takes the static
            # `bytes_literal` span render (`bv: BytesView = b"hello"`) --
            # the same form flip the view-typed decl retag applies to a
            # bare literal init.
            inner = replace(inner, form=Form.BORROW)
        if (e.coercion.name in (_FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION)
                and isinstance(inner, THIRLiteral)):
            # The coerce target forwards into the literal render (the
            # float32 `f` suffix / the BigInt ctor wraps). Only a literal source
            # reaches here; other shapes reject during child lowering.
            inner = replace(inner, result_type=rtype)
        if (e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)
                and isinstance(inner, THIRLiteral)
                and isinstance(inner.value, int)
                and not isinstance(inner.value, bool)):
            inner = _retarget_int_literal(inner, rtype, lc)
        if (e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)
                and isinstance(inner, THIRBinOp)
                and inner.both_literal_int_operands):
            # A literal-tree widening coerce (`b: int64 = (4 + 5) + 6`): the
            # coerce target forwards into the binop, whose literal arm
            # re-resolves the operators at the TARGET width
            # (`add_check<int64_t>`); the rebuild is that same recursion.
            inner = _slot_literal_retype(inner, rtype, lc)
        # Identity passthrough: the node's form is the wrapped expression's
        # form -- carried honestly (not the VALUE default) so the owned-sink
        # BORROW checks read the real source shape through the coerce (e.g.
        # string_to_str wraps a STORAGE String) -- EXCEPT a view-target coerce
        # (str_to_strview / string_to_strview): its value is a view into the
        # source's buffer whatever the source's form, so it sets BORROW
        # itself (an owned sink downstream must re-copy, like any view).
        # The ptr/span coercion families produce VALUE types (`T*`,
        # std::span, Slice) whatever the inner's form -- carrying the
        # record/container inner's BORROW would trip the value-typed
        # return validation.
        if (e.coercion.name in _ADDR_PTR_COERCIONS
                or e.coercion.name in _SPANLIKE_COERCIONS
                or e.coercion.name in _SPAN_METHOD_COERCIONS
                or e.coercion.name in _PTR_IDENTITY_COERCIONS):
            vform = Form.VALUE
        else:
            vform = (Form.BORROW
                     if rtype is not None
                     and is_str_view_type(unwrap_readonly(unwrap_ref_type(
                         unwrap_send_sync(rtype))))
                     else inner.form)
        return THIRCoerce(
            result_type=rtype,
            expr=inner,
            coercion_name=e.coercion.name,
            wrap=_coerce_wrap(e) if disp == "template" else None,
            form=vform,
            loc=loc,
        )
    if isinstance(e, TpyLambda):
        return _lower_lambda(e, lc, declared)
    if isinstance(e, TpyGeneratorExpression):
        # A genexpr at an ITERABLE-consuming position (`list(x * x for x in
        # xs)`, `items.extend(x * 10 for x in range(3))`): the
        # make_generator render binds in place. Other value positions are
        # unwitnessed and keep the dispatch-tail reject. Late import:
        # comprehensions imports this module.
        if use.result is _ExprResultUse.ITERABLE:
            from .comprehensions import _lower_genexpr
            return _lower_genexpr(e, lc, declared)
    if isinstance(e, TpyTupleLiteral):
        # A value-tuple literal with no slot threaded from the position
        # (`t = astuple(p)`, an unpack source, an Own[union] return): the
        # tuple spells its own RESOLVED type (get_resolved_type --
        # int-literal elements resolve to the default int, so
        # `("hi", 7)` spells `std::tuple<std::string, int32_t>{...}`), so
        # the slot IS the resolved expression type. Every other tuple
        # literal reaches `_lower_tuple_literal` with its position's slot.
        vt_t = lc.analyzer.get_expr_type(e)
        if vt_t is not None:
            vt_t = resolve_int_literals(
                unwrap_readonly(vt_t),
                lc.analyzer.ctx.default_int_for_literal)
        vt = _value_tuple(vt_t, lc.analyzer)
        if vt is None:
            # The recursive sibling (`t = astuple(line)` -- a tuple of
            # value tuples): same self-typed spelling, the nested-elem
            # arm of _lower_tuple_literal renders each inner tuple.
            vt = _value_tuple_nested(vt_t, lc.analyzer)
        if vt is None:
            raise ThirUnsupported("expr.tuple_literal")
        _witness("expr.value_tuple_self_typed")
        return _lower_tuple_literal(e, vt, lc, declared)
    raise ThirUnsupported(expr_kind_tag(e))


def _walrus_opt_ptr_source(value: TpyExpr, vtu: TpyType, inner: TpyType,
                           lc: '_LowerCtx', declared: dict[str, TpyType],
                           loc, *, allow_none: bool) -> 'THIRExpr | None':
    """Source rungs shared by the sync and resumable-frame pointer-repr Optional
    walrus arms: a storage-form Optional FIELD lifts via optional_to_ptr, `None`
    assigns nullptr, an already-pointer NAME passes bare, a BORROWING call's
    `T*` result lands bare. Returns None when no rung fits, so each caller
    raises under its own reject tag.

    `allow_none` is False at the frame arm: sema rejects a walrus reassignment
    of a non-value resumable local ("not supported yet"), so a `None` source is
    unconstructible there and admitting it would be unwitnessed."""
    analyzer = lc.analyzer
    src_t = analyzer.get_expr_type(value)
    src_t = unwrap_readonly(src_t) if src_t is not None else None
    if (isinstance(value, TpyFieldAccess)
            and _field_markers_clean(value)
            and _field_receiver_ok(value, declared, analyzer)
            and isinstance(src_t, OptionalType)
            and unwrap_readonly(src_t.inner) == unwrap_readonly(inner)
            and reads_storage_form_optional(analyzer, value)):
        return THIRFormConvert(
            result_type=vtu,
            value=_lower_field_source(value, lc, declared),
            form=Form.BORROW, loc=loc)
    if (_container_elem_lvalue_subscript(value, declared, analyzer,
                                        lc.pointers)
            and isinstance(src_t, OptionalType)
            and unwrap_readonly(src_t.inner) == unwrap_readonly(inner)
            and reads_storage_form_optional(analyzer, value)):
        # A container ELEMENT whose element type IS the Optional: the raw
        # element read is the storage optional, lifted by the same convert the
        # OPTIONAL_TO_PTR decl uses for one. `subscript_prechecked` keeps the
        # ELEMENT's optional whole (its None check belongs to the walrus
        # target's narrowing, not to the read); the shared predicate is what
        # rules out an unproven-Optional RECEIVER, whose own deref check the
        # same flag would suppress.
        _witness("walrus.optptr_subscript_src")
        return THIRFormConvert(
            result_type=vtu,
            value=_lower_expr(value, lc, declared, subscript_prechecked=True),
            form=Form.BORROW, loc=loc)
    if (isinstance(value, (TpyCall, TpyMethodCall))
            and isinstance(src_t, OptionalType)
            and unwrap_readonly(src_t.inner) == unwrap_readonly(inner)
            and _ptr_opt_borrow_call_ret(value, src_t)):
        # A BORROWING call source (`(m = pick(nodes, i))`): its C++ result
        # already IS the `T*` the target holds, so it lands bare. The
        # Own-declared sibling stays out: its `std::optional<T>` rvalue has
        # neither a lift nor storage to address here (BUGS.md).
        _witness("walrus.optptr_call_src")
        return _lower_expr(value, lc, declared,
                           use=_ExprUse(pos=SinkPos.WALRUS_TARGET,
                                        forms=_ONLY_PTR_OPT_PASSTHROUGH))
    if (isinstance(value, TpyIfExpr)
            and isinstance(src_t, OptionalType)
            and unwrap_readonly(src_t.inner) == unwrap_readonly(inner)):
        # A TERNARY source: the ifexpr lowering normalized both arms to the
        # `T*` the target holds (per-arm optional_to_ptr / addr-of / nullptr),
        # so the select IS that shape and lands bare -- the OPTIONAL_TO_PTR
        # decl's ternary row, one sink over.
        _witness("walrus.optptr_ternary_src")
        return _lower_expr(value, lc, declared)
    if allow_none and isinstance(value, TpyNoneLiteral):
        return THIRLiteral(result_type=vtu, value=None, form=Form.BORROW,
                           loc=loc)
    if (isinstance(value, TpyName)
            and value.name in lc.pointers
            and value.name not in lc.prescan.global_slots):
        return _lower_expr(value, lc, declared)
    return None


def _lower_frame_walrus(e: 'TpyNamedExpr', vtu: TpyType, lc: '_LowerCtx',
                        declared: dict[str, TpyType], loc) -> THIRExpr:
    """Walrus whose target is a resumable-frame FIELD: write the field,
    declare nothing.

    The field's C++ form was decided once, by the frame-layout plan, and the
    `lc` sets below are seeded straight from that plan -- so the dispatch reads
    them rather than re-deriving a form from the type, and a walrus classified
    one way but rendered another is impossible by construction."""
    analyzer = lc.analyzer
    cpp_name = escape_cpp_name(e.target)
    if isinstance(vtu, TupleType) and e.target in lc.prescan.reassigned:
        # A REASSIGNED tuple target must be re-typed to its unified borrow
        # form (collapse_tuple_own_elements) before anything renders; with
        # no witness for that transform, it rejects.
        note_detail("walrus.frame_tuple_reassign")
        raise ThirUnsupported("expr.walrus")
    if e.target in lc.coro_handle_slots:
        # A concrete coro handle is a frame_slot too, but its writes carry a
        # factory-call-only admission (and no brace-init spelling: the handle
        # type has no self-contained C++ name) that only the statement arm
        # spells.
        note_detail("walrus.frame_coro_handle")
        raise ThirUnsupported("expr.walrus")
    if e.target in lc.frame_slots:
        # An owning `frame_slot<T>` field: emplace destroys any prior payload,
        # constructs in place and hands back the `T&` the expression evaluates
        # to (the slot has no operator= by design). The brace-init prefix
        # spells the WALRUS type (the frame DECL arm instead spells the
        # resolved slot).
        value = _lower_expr(
            e.value, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE,
                         pos=SinkPos.WALRUS_TARGET,
                         forms=(None if isinstance(vtu, TupleType)
                                else _NO_FORMS)))
        declared[e.target] = vtu
        _witness("expr.walrus_frame_slot")
        return THIRWalrus(
            result_type=vtu, name=e.target, cpp_name=cpp_name,
            value=value, emplace_cpp=lc.render_type(vtu), loc=loc)
    if e.target in lc.opt_ptr_frame_locals:
        # Pointer-repr Optional field (`P* m;` -- nullptr doubles as None):
        # the borrow-form assign, over the source rungs shared with the sync
        # walrus_opt_ptr arm. An `Own[T | None]` CALL source stays out: the
        # callee returns a `std::optional<T>` rvalue that would land in the
        # `T*` field with neither a lift nor storage to address (see
        # BUGS.md);
        # the borrowing call sibling lands bare via the shared source rung.
        frame_opt = _optional_ptr_borrow(vtu, analyzer)
        if frame_opt is None:
            note_detail("walrus.frame_opt_ptr_type")
            raise ThirUnsupported("expr.walrus")
        opt_value = _walrus_opt_ptr_source(
            e.value, vtu, frame_opt.inner, lc, declared, loc,
            allow_none=False)
        if opt_value is None:
            note_detail("walrus.frame_opt_ptr_src")
            raise ThirUnsupported("expr.walrus")
        value = opt_value
        declared[e.target] = vtu
        _witness("expr.walrus_frame_opt_ptr")
        return THIRWalrus(result_type=vtu, name=e.target, cpp_name=cpp_name,
                          value=value, loc=loc)
    if e.target in lc.alias_ptr_locals:
        # Bare `T*` alias field: point it at the LIVE source lvalue and read
        # back through the deref tail (`(row = &(rows[i]), *row)`). Only
        # proven-lvalue sources living past the suspension are admitted --
        # the proven container element (the alias-bind arm's guards: an
        # unproven-Optional / slice / rvalue-container element would take
        # the address of a dying temporary), or a frame_slot NAME whose
        # deref read is frame storage (`(x = &((*buf)), *x)`).
        if (isinstance(e.value, TpyName)
                and e.value.name in lc.frame_slots):
            value = _lower_expr(e.value, lc, declared)
            declared[e.target] = vtu
            _witness("expr.walrus_frame_alias_slot")
            return THIRWalrus(result_type=vtu, name=e.target,
                              cpp_name=cpp_name, value=value,
                              tail="deref", addr_of=True, loc=loc)
        if not (isinstance(e.value, TpySubscript)
                and not e.value.needs_optional_runtime_check
                and e.value.slice_function_info is None
                and not isinstance(e.value.index, TpySlice)
                and _subscript_container_recv_type(
                    e.value.obj, declared, analyzer) is not None):
            note_detail("walrus.frame_alias_src")
            raise ThirUnsupported("expr.walrus")
        value = _lower_expr(e.value, lc, declared, subscript_prechecked=True)
        declared[e.target] = vtu
        _witness("expr.walrus_frame_alias")
        return THIRWalrus(result_type=vtu, name=e.target, cpp_name=cpp_name,
                          value=value, tail="deref", addr_of=True, loc=loc)
    if e.target in lc.borrow_tuple_frame_locals:
        # Borrow-form tuple field (`std::tuple<..., T*> bt;`): the bare assign
        # with the name tail. Only a BORROWING call source is admitted -- its
        # C++ return IS the borrow tuple, so nothing needs lifting. A tuple
        # LITERAL is rejected on purpose: its element renders derive const-ness
        # from the source while the tuple type comes from the local's borrow
        # spelling, and over a `readonly` container the two disagree and emit
        # ill-formed C++ (BUGS.md). A storage-form source would need the
        # tuple_to_pointer lift.
        if not (isinstance(e.value, (TpyCall, TpyMethodCall))
                and _f1_tuple(analyzer.get_expr_type(e.value),
                              analyzer) is not None
                and not _own_declared_call_ret(e.value)):
            note_detail("walrus.frame_btuple_src")
            raise ThirUnsupported("expr.walrus")
        value = _lower_expr(e.value, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         pos=SinkPos.WALRUS_TARGET))
        declared[e.target] = vtu
        _witness("expr.walrus_frame_btuple")
        return THIRWalrus(result_type=vtu, name=e.target, cpp_name=cpp_name,
                          value=value, tail="name", loc=loc)
    if e.target not in lc.plain_frame_fields:
        # Every family whose write render is NOT the plain member assign
        # subtracts itself from plain_frame_fields (erased @dynamic handles,
        # pointer-form loop vars, unpack alias targets); their renders are
        # not spelled here, so they must not fall through to it.
        note_detail("walrus.frame_field_kind")
        raise ThirUnsupported("expr.walrus")
    # Plain frame field: the position-blind member assign. The value renders
    # against the TARGET type, unlike the frame decl arm's bare render.
    value = _lower_expr(e.value, lc, declared,
                        use=_ExprUse(slot_target=vtu))
    declared[e.target] = vtu
    _witness("expr.walrus_frame_field")
    return THIRWalrus(result_type=vtu, name=e.target, cpp_name=cpp_name,
                      value=value, loc=loc)


def _lower_into_any(e: TpyCoerce, lc: '_LowerCtx',
                    declared: dict[str, TpyType],
                    rtype: 'TpyType | None', loc) -> THIRExpr:
    """`into_any` coercion: store the value in a `tpy::Any` cell via `make_any`.
    The wrap is the coercion's own render taken with a `{0}` placeholder, which
    is placeholder-transparent for scalar / str / bytes / None sources (their
    `_any_storage_form` wraps embed the render unchanged). A LIST-literal source
    renders as a bare brace-init (`{1,2,3}`) the `std::vector<T>{0}` prefix wraps
    correctly; a name / set / dict / make_vector source needs a paren copy the
    placeholder wrap cannot express, so it rejects."""
    actual = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e.actual_type)))
    if is_list(actual) or is_dict(actual) or is_set(actual):
        if not (isinstance(e.expr, TpyArrayLiteral) and is_list(actual)):
            note_detail("coerce.into_any_container")
            raise ThirUnsupported("expr.coerce")
        lit = _lower_expr(e.expr, lc, declared,
                          use=_ExprUse(slot_target=actual))
        # Only the bare brace-init render (`{...}`) is placeholder-safe -- a
        # make_vector / typed-brace / empty spelling would double the type.
        if not (isinstance(lit, THIRContainerLiteral) and lit.elements
                and not lit.make_container and lit.typed_brace_cpp is None):
            note_detail("coerce.into_any_container")
            raise ThirUnsupported("expr.coerce")
        wrap = e.coercion.codegen(
            "{0}", e.actual_type, e.expected_type, e.context_kind)
        _witness("coerce.into_any")
        return THIRCoerce(result_type=rtype, expr=lit,
                          coercion_name="into_any", wrap=wrap,
                          form=Form.VALUE, loc=loc)
    src = e.expr
    # `a: Any = copy(c)` -> `make_any(c)`: make_any already copy-constructs
    # into the cell, so a redundant `copy()` is dropped at this coerce
    # rather than emitting `T(x)` on top of it. A ptr-variant union source is
    # excluded -- there `copy()` is a REPRESENTATION convert
    # (`to_value_variant`), and dropping it would store dangling pointers.
    peeled = copy_call_arg(src, lc.analyzer)
    if peeled is not None:
        pt = lc.analyzer.get_expr_type(peeled)
        if pt is None or not is_ptr_variant_union(pt):
            _witness("coerce.into_any_copy_peel")
            src = peeled
    if isinstance(src, TpyNoneLiteral):
        # `None` into Any stores `std::monostate{}` -- the unit-typed STORAGE
        # None render; `_any_storage_form` leaves it bare inside make_any.
        inner: THIRExpr = THIRLiteral(result_type=NONE, value=None,
                                      form=Form.STORAGE, loc=loc)
    else:
        inner = _lower_expr(src, lc, declared)
    wrap = e.coercion.codegen(
        "{0}", e.actual_type, e.expected_type, e.context_kind)
    _witness("coerce.into_any")
    return THIRCoerce(result_type=rtype, expr=inner,
                      coercion_name="into_any", wrap=wrap,
                      form=Form.VALUE, loc=loc)


def _lower_from_any(e: TpyCoerce, lc: '_LowerCtx',
                    declared: dict[str, TpyType],
                    rtype: 'TpyType | None', loc) -> THIRExpr:
    """`from_any` auto-coerce (`n: int = a` / `return a` at a concrete slot):
    runtime-checked extraction `any_cast_or_panic<T>(a)`. The render is a
    single-`{0}` template around the Any source, so it lowers like the scalar
    casts -- carry the source's own form (the extraction yields T by value; a
    plain Any name reads VALUE)."""
    target = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e.expected_type)))
    if is_list(target) or is_dict(target) or is_set(target):
        note_detail("coerce.from_any_container")
        raise ThirUnsupported("expr.coerce")
    inner = _lower_expr(e.expr, lc, declared)
    wrap = e.coercion.codegen(
        "{0}", e.actual_type, e.expected_type, e.context_kind)
    _witness("coerce.from_any")
    return THIRCoerce(result_type=rtype, expr=inner,
                      coercion_name="from_any", wrap=wrap,
                      form=inner.form, loc=loc)


def _lower_elem_into_any(e: TpyExpr, any_slot: TpyType, lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRExpr:
    """One container-literal element / dict key-value into an `Any` slot: the
    per-element into-Any wrap (`_wrap_for_owned_slot`'s AnyType arm). An
    already-Any source renders bare (it is a cell); every other element wraps
    `make_any(...)` with an int/float-literal type override so
    the cell's typeid is the canonical BigInt/double. A None element renders the
    default `nullptr` (NOT the decl-init `std::monostate`), matching the
    container-element position's target-free None render."""
    analyzer = lc.analyzer
    loc = getattr(e, "loc", None)
    actual = analyzer.get_expr_type(e)
    bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(actual)))
            if actual is not None else None)
    if isinstance(bare, AnyType):
        return _lower_expr(e, lc, declared)
    if isinstance(e, TpyNoneLiteral):
        inner: THIRExpr = THIRLiteral(result_type=NONE, value=None,
                                      form=Form.VALUE, loc=loc)
        wrap_actual: TpyType = bare
    else:
        inner = _lower_expr(e, lc, declared)
        # The `resolved` override at an Any element: int/float literals
        # store under the canonical BigInt / double typeid.
        if isinstance(e, TpyIntLiteral):
            wrap_actual = IntLiteralType()
        elif isinstance(e, TpyFloatLiteral):
            wrap_actual = FloatLiteralType()
        else:
            wrap_actual = bare
    wrap = wrap_into_any("{0}", wrap_actual, CoercionContext.INIT)
    _witness("coerce.into_any")
    return THIRCoerce(result_type=any_slot, expr=inner,
                      coercion_name="into_any", wrap=wrap,
                      form=Form.VALUE, loc=loc)


def _borrow_tuple_param_names(lc: '_LowerCtx') -> 'frozenset[str]':
    """Params whose C++ binding is ALREADY a borrow-form (pointer-repr) tuple
    -- `const std::tuple<T*, ..>&`. The positive half of the storage-vs-borrow
    split for tuple NAMES; a loop var / alias local is a storage source and
    needs the `tuple_to_pointer` lift instead.
    Mirrors `_borrow_form_tuple_param`'s param scan, which the tuple-unpack
    arm uses for the same discrimination."""
    return frozenset(
        n for n, t in lc.params
        if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))),
                      TupleType)
        and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        .has_pointer_repr_element())


def _borrow_tuple_bare_names(lc: '_LowerCtx',
                             declared: dict[str, TpyType]) -> 'frozenset[str]':
    """Every tuple NAME whose C++ binding is borrow form, so it feeds a
    borrow-tuple slot BARE: the params above plus a REASSIGNED pointer-repr
    tuple LOCAL (one fixed borrow shape across all its bindings).

    Both disjuncts are positive evidence. The local one pairs the reassign
    fact with `_borrow_tuple_local_type`'s storage carve-out AND excludes
    every resumable frame name: the lane's owning tuple slots reach neither
    `storage_tuple_locals` nor a valid borrow render, so the carve-out
    alone would read them as borrow and drop their lift."""
    names = set(_borrow_tuple_param_names(lc))
    names |= {n for n in lc.prescan.reassigned
              if n not in lc.frame_slots
              and n not in lc.borrow_tuple_frame_locals
              and _borrow_tuple_local_type(
                  n, declared, lc.storage_tuple_locals) is not None}
    if not (lc.func.is_generator or lc.func.is_async):
        # A SYNC body's single-assignment ptr-repr tuple DECL local
        # (`auto pair = make_pair(a, b);`) is borrow form by default --
        # the storage carve-outs ARE populated in sync lanes, so the
        # local-type verdict is positive evidence there. Generator/async
        # lanes keep the reassigned-only slice (their owning tuples reach
        # neither set; see the docstring's hazard).
        names |= {n for n in declared
                  if n not in lc.prescan.param_names
                  and _borrow_tuple_local_type(
                      n, declared, lc.storage_tuple_locals) is not None}
    return frozenset(names)


def _self_capture_cpp(lc: '_LowerCtx') -> 'str | None':
    """The capture-list entry that hands a LAMBDA the enclosing receiver, or
    None when this body holds none it can reach. A plain method (`this`)
    holds the receiver POINTER, so
    the lambda copies that pointer; a resumable frame holds the receiver as
    its `__self` reference member, so the lambda binds the same referent --
    a copy of the HANDLE, never of the frame that stores it. The body read
    spelling comes from `lc.self_cpp` and matches the entry's name."""
    if lc.self_receiver != "self":
        return None
    if lc.self_cpp == "this":
        return "this"
    if lc.self_cpp == "__self":
        return "&__self = __self"
    return None


def _uncopyable_capture(name: str, declared: dict[str, TpyType],
                        lc: '_LowerCtx') -> bool:
    """Whether a BY-VALUE capture entry for `name` would be a copy C++ has
    deleted. The wrappers peel first: it is the payload, not the wrapper,
    that the snapshot copies. `RefType` is in the chain because an ordinary
    (non-`Own`) reference-type PARAM is declared wrapped in one -- without
    the peel the question was asked of an opaque wrapper, answered False,
    and a `[g]` over a record with `__del__` reached the toolchain.

    A value TUPLE is asked of its elements: they are embedded inline, so one
    non-copyable element makes the tuple non-copyable."""
    dt = declared.get(name)
    if dt is None:
        return False
    payload = unwrap_own(unwrap_readonly(unwrap_ref_type(dt)))
    if isinstance(payload, TupleType):
        return payload.has_nested_element(
            lambda e: _cpp_noncopyable_type(
                unwrap_own(unwrap_readonly(unwrap_ref_type(e))), lc.analyzer))
    return _cpp_noncopyable_type(payload, lc.analyzer)


def _capture_entry_cpp(name: str, lc: '_LowerCtx',
                       declared: dict[str, TpyType],
                       by_value: bool) -> str:
    """The capture-list entry that hands a LAMBDA the name `name`, in either
    lane. An ordinary variable names itself (`[n]` / `[&n]`); a name the
    enclosing RESUMABLE FRAME owns is a member, not a variable, so `[n]` is
    ill-formed C++ and the entry names the member in an init-capture instead.
    The MODE is the sync one in both -- `by_value` is the lambda's own
    `captures_by_value` -- so the same source gets the same binding whether or
    not the enclosing body suspends: an escaping (`Callable`) closure
    snapshots, a non-escaping (`Fn`) one binds a reference, which for a
    borrowed param is the caller's object and for a frame local is the frame's
    own storage. Capturing the frame itself would be neither, and would tie
    the closure to a frame it does not own (a stored closure outlives it; a
    copied frame leaves it pointing at the original).

    Three names have no entry. A by-value entry over a type C++ cannot copy is
    an ill-formed copy in both lanes, so both reject here rather than hand it
    to the toolchain. A frame member whose read spelling is not the bare name
    (a `frame_slot<T>` local reads `(*n)`) would need that spelling in the
    initializer and the bare one in the body, which no single entry gives. And
    a frame member of unknown type is one this lane cannot vouch for -- an
    ordinary variable without a declared type is merely unproven, and keeps
    the entry the pre-frame lane always emitted."""
    cpp = escape_cpp_name(name)
    if by_value and _uncopyable_capture(name, declared, lc):
        raise ThirUnsupported("lambda.capture_nocopy")
    if name in lc.narrow.spelled or name in lc.deref_view_spelled:
        # A poly-narrowed subject reads as `(*__p_ptr)` inside the closure --
        # a spelling no capture entry can bind.
        raise ThirUnsupported("lambda.capture_narrowed_view")
    alias = lc.narrow.narrowed.get(name)
    if alias is not None:
        # Reads of a narrowed subject inside the closure render the
        # extraction alias -- a LOCAL of the enclosing branch even where the
        # subject itself is a frame member -- so the entry binds the alias.
        # Capturing the subject would leave the alias unnamed by the list.
        # By VALUE over a REFERENCE-typed member the entry would snapshot the
        # object the un-narrowed spelling of the same program aliases (that
        # one captures the pointer variant), so the narrowing alone would
        # change copy for alias, silently. Rejects until the escaping lane can
        # capture the subject and re-narrow inside
        # (BUGS.md#escaping-capture-of-narrowed-subject). A value-typed member
        # snapshots what the un-narrowed union snapshots, so it stays.
        narrowed_type = declared.get(name)
        if (by_value and narrowed_type is not None
                and is_plain_nonvalue(narrowed_type)):
            raise ThirUnsupported("lambda.capture_narrowed_reference")
        return f"{'' if by_value else '&'}{alias}"
    if name not in lc.frame_field_names:
        return f"{'' if by_value else '&'}{cpp}"
    if name not in lc.plain_frame_fields:
        raise ThirUnsupported("lambda.capture_frame_slot")
    if not by_value:
        return f"&{cpp} = {cpp}"
    if name not in declared:
        raise ThirUnsupported("lambda.capture_unresolved_frame_member")
    return f"{cpp} = {cpp}"


def _lower_lambda(e: TpyLambda, lc: '_LowerCtx',
                  declared: dict[str, TpyType]) -> THIRExpr:
    global_scope = lc.global_binding_scope
    lc.global_binding_scope = False
    try:
        return _lower_lambda_impl(e, lc, declared)
    except ThirUnsupported as ex:
        # A nested lambda already names its own failure and source location.
        if ex.loc is not None and ":lambda." in ex.reason:
            raise
        cause = (ex.reason if ex.reason.startswith("lambda.")
                 else f"lambda.body:{ex.reason}")
        raise ThirUnsupported(f"expr.lambda:{cause}", loc=e.loc) from None
    finally:
        lc.global_binding_scope = global_scope


def _lower_lambda_impl(e: TpyLambda, lc: '_LowerCtx',
                       declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a lambda expression to its C++ closure. The single-expression
    body renders against the lambda's return type.
    `captures_by_value` (Callable/std::function --
    escaping) picks the `_callable_param_cpp` param spelling and a by-value
    capture list; the default (Fn template) uses `to_cpp_param` and a
    by-reference capture; `readonly_params` (the key-function context)
    spells each param const via `_callable_param_cpp(readonly[T])` and the
    trailing return via `to_cpp_return_const`; a pointer-repr tuple return
    spells the borrow form via `to_cpp_return` (the same-typed bare-call
    body slice -- gate-checked). Not lowered yet: the void-body statement
    render (beyond the builtin-print closure)."""
    analyzer = lc.analyzer
    loc = getattr(e, "loc", None)
    self_cap = _self_capture_cpp(lc)
    reason = _lambda_reject_reason(e, analyzer,
                                   self_capturable=self_cap is not None)
    if reason is not None:
        raise ThirUnsupported(reason)
    ret_type = e.inferred_return_type
    params_cpp: list[str] = []
    body_declared = dict(declared)
    for pname, ptype in zip(e.param_names, e.inferred_param_types):
        cpp_name = escape_cpp_name(pname)
        if e.readonly_params:
            # Key-function context (min/max/sorted): const params
            # regardless of the callable-default mutability.
            ro = (ptype if isinstance(ptype, ReadonlyType)
                  else ReadonlyType(ptype))
            params_cpp.append(
                f"{CallableType._callable_param_cpp(ro)} {cpp_name}")
            body_declared[pname] = ro
            continue
        if e.captures_by_value:
            params_cpp.append(
                f"{CallableType._callable_param_cpp(ptype)} {cpp_name}")
        else:
            params_cpp.append(ptype.to_cpp_param(cpp_name))
        body_declared[pname] = ptype
    if e.captured_names:
        # The receiver is a handle the closure copies; a resumable frame's
        # own members name themselves in an init-capture. Everything else is a
        # variable the enclosing scope names directly.
        parts: list[str] = []
        for n in e.captured_names:
            if n == lc.self_receiver and self_cap is not None:
                entry = self_cap
            else:
                entry = _capture_entry_cpp(n, lc, declared,
                                           e.captures_by_value)
            if entry not in parts:
                parts.append(entry)
        capture = "[" + ", ".join(parts) + "]"
    else:
        capture = "[]"
    if is_void_like_type(ret_type):
        # The statement-body closure: the body IS a builtin print call
        # (gate-checked by _lambda_reject_reason). Its args lower TEMP-FREE --
        # a hoisted temp would flush at the ENCLOSING statement, outside
        # the closure. Narrowed-alias args reject (the subject_union print
        # keying is a statement-scope fact).
        # Late import: statements.py imports this module at top level.
        from .statements import _lower_print_arg
        pargs = []
        for parg in e.body.args:
            if (isinstance(parg, TpyName)
                    and (parg.name in lc.narrow.narrowed
                         or parg.name in lc.narrow.spelled)):
                raise ThirUnsupported("lambda.print_narrowed_argument")
            pargs.append(_lower_print_arg(parg, lc, body_declared,
                                          frozenset(), temps_ok=False))
        _witness("expr.lambda_void_print")
        return THIRLambda(
            result_type=analyzer.get_expr_type(e),
            capture_cpp=capture,
            params_cpp=tuple(params_cpp),
            body=THIRPrintChain(result_type=ret_type, args=tuple(pargs),
                                loc=loc),
            ret_cpp=None,
            loc=loc,
        )
    # A borrow-returning lambda (`-> Point&`, the map key/value-preserving
    # form): the body's call result binds the reference, so it lowers under
    # BORROW_BIND -- the same use a borrow-record consumer threads.
    _lam_ret_u = unwrap_readonly(ret_type)
    if isinstance(ret_type, RefType):
        body_use = _ExprUse(result=_ExprResultUse.BORROW_BIND)
    elif (isinstance(_lam_ret_u, TupleType)
            and _lam_ret_u.has_pointer_repr_element()):
        # The borrow-form tuple return: the body's generic call renders
        # bare under the dedicated flag (gate-validated shape).
        body_use = _ExprUse(pos=SinkPos.LAMBDA_RETURN)
    else:
        body_use = _ExprUse()
    body = _lower_expr(e.body, lc, body_declared,
                       use=replace(body_use, slot_target=ret_type))
    _witness("expr.lambda")
    ret_u = unwrap_readonly(ret_type)
    if e.readonly_params:
        ret_cpp = ret_type.to_cpp_return_const()
    elif isinstance(ret_u, TupleType) and ret_u.has_pointer_repr_element():
        # A tuple with borrow elements is produced in borrow form by the
        # body (the to_cpp_return spelling); the storage form would not
        # bind the returned pointers.
        _witness("lambda.btuple_ret")
        ret_cpp = ret_type.to_cpp_return()
    else:
        ret_cpp = ret_type.to_cpp()
    return THIRLambda(
        result_type=analyzer.get_expr_type(e),
        capture_cpp=capture,
        params_cpp=tuple(params_cpp),
        body=body,
        ret_cpp=ret_cpp,
        loc=loc,
    )

def _lower_dyn_synth_call(call: 'TpyMethodCall', rtype: 'TpyType | None',
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          loc, *, allow_name_arg: bool = False,
                          allow_self: bool = False) -> THIRExpr:
    """The shared shape gate + lowering for a sema-synthesized dunder call
    (`obj.__getattr__(name)` behind a dyn-attr read, a hasattr probe, or a
    getattr-with-default): a bare non-pointer F1-record receiver name --
    or, for the getattr READ, the plain `self` receiver (`return self.k`
    -> `this->__getattr__("k")`, the generic arm's self spelling) -- a
    literal (or, for the probe forms, str-NAME) name arg, and a
    single-overload plain user method, lowered as the plain method call."""
    analyzer = lc.analyzer
    recv = call.obj
    is_self = (allow_self and isinstance(recv, TpyName)
               and recv.name == lc.self_receiver
               and recv.name not in lc.narrow.spelled)
    if not (isinstance(recv, TpyName) and recv.name in declared
            and (is_self or recv.name != lc.self_receiver)
            and recv.name not in lc.pointers
            and recv.name not in lc.narrow.narrowed
            and recv.name not in lc.frame_slots
            and _f1_record(declared.get(recv.name), analyzer)):
        raise ThirUnsupported("getattr.recv_shape", detail=True)
    fi = call.resolved_function_info
    if (fi is None or not _plain_member_call_markers_ok(call)
            or call.needs_optional_runtime_check
            or not _plain_method_fi_ok(fi)
            or fi.cpp_template is not None or fi.native_function
            or fi.native_name or fi.type_params or fi.is_staticmethod
            or not fi.is_method
            or len(call.args) != 1 or len(fi.params) != 1):
        raise ThirUnsupported("getattr.call_shape", detail=True)
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[recv.name])))
    ri = analyzer.registry.get_record_for_type(recv_t)
    if ri is None or len(analyzer.registry.get_method_overloads_with_parents(
            ri, call.method)) != 1:
        raise ThirUnsupported("getattr.overloads", detail=True)
    name_arg = call.args[0]
    if not (isinstance(name_arg, TpyStrLiteral)
            or (allow_name_arg and isinstance(name_arg, TpyName)
                and name_arg.name in declared
                and _resolved_str_value(declared[name_arg.name], analyzer)
                is not None)):
        raise ThirUnsupported("getattr.name_shape", detail=True)
    return _self_recv_positioned(THIRMethodCall(
        result_type=rtype,
        receiver=_lower_expr(
            recv, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
        method_cpp=escape_cpp_name(call.method),
        args=(_lower_call_arg(name_arg, fi.params[0].type, lc, declared,
                              method_arg=True),),
        is_arrow=is_self and lc.self_is_pointer,
        loc=loc,
    ))


def _lower_dyn_getattr_call(e: TpyFieldAccess, rtype: 'TpyType | None',
                            lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The sema-synthesized `obj.__getattr__("name")` behind a dynamic-attr
    read (`obj.x`) -- the value-position twin of the dyn-setattr write
    arm, lowered as the plain method call (the post-process chain is
    identity in this slice per
    `_plain_method_fi_ok`). The READ admits the plain `self` receiver; the
    setattr twin keeps its self exclusion (unwitnessed)."""
    result = _lower_dyn_synth_call(e.dyn_getattr_call, rtype, lc, declared,
                                   getattr(e, "loc", None), allow_self=True)
    _witness("method.dyn_getattr")
    return result

def _lower_class_constant(e: TpyFieldAccess, rtype: 'TpyType | None',
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          loc, *, tuple_ok: bool = False) -> THIRExpr:
    """A class-constant read (`class_constant_owner` set) -> the bare
    qualified static, spelled at lowering like THIREnumMember. A pure
    receiver (name / static-type-chain, no runtime Optional check) reads the
    bare static; an INSTANCE receiver with observable cost takes the
    statement-expression wrapper (gen_class_constant's receiver_eval
    split) -- the unproven-Optional check (`({ ::tpy::deref_check(c);
    C::LIMIT; })`, a declared Optional-ptr borrow NAME only) or the effect
    discard (`({ static_cast<void>(<recv>); C::LIMIT; })`, the receiver
    lowered through its own arms at RECEIVER use -- an unroutable receiver
    falls the body back there). Result families are the value leaves plus
    str/bytes views (a constant is a static scalar / string_view -- never an
    owned str), so every admitted sink lands the bare name; a
    tuple/container constant's consumers wrap it in renders this arm does
    not pin, so those reject."""
    analyzer = lc.analyzer
    recv_eval: 'THIRExpr | None' = None
    recv_wrap: 'str | None' = None
    if not _class_const_pure_receiver(e, declared, analyzer):
        if e.needs_optional_runtime_check:
            if _optional_ptr_borrow_name(e.obj, declared, analyzer) is None:
                raise ThirUnsupported("field.class_const_receiver",
                                      detail=True)
            _witness("field.class_const_recv_check")
            recv_eval = THIRName(result_type=analyzer.get_expr_type(e.obj),
                                 name=e.obj.name, form=Form.BORROW, loc=loc)
            recv_wrap = "::tpy::deref_check({0})"
        else:
            _witness("field.class_const_recv_effect")
            recv_eval = _lower_expr(
                e.obj, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER))
            recv_wrap = "static_cast<void>({0})"
    viewfam = _resolved_viewfam_value(rtype, analyzer)
    ok = (_eligible_scalar(rtype) or _eligible_char(rtype)
          or _eligible_enum(rtype, analyzer) is not None
          or _eligible_ptr_value(rtype, analyzer)
          or viewfam is not None
          # The standalone tuple-unpack source consumes a value-tuple
          # constant whole (`auto __tup_N = Version::SEMVER;`).
          or (_value_tuple_global(rtype, analyzer) is not None))
    if not ok:
        raise ThirUnsupported("field.class_const_type", detail=True)
    cpp = _class_constant_cpp(e, analyzer, lc.render_type)
    if cpp is None:
        raise ThirUnsupported("field.class_const_spelling", detail=True)
    _witness("field.class_const")
    return THIRClassConstant(result_type=rtype, cpp=cpp,
                             recv_eval=recv_eval, recv_wrap=recv_wrap,
                             form=_viewfam_result_form(viewfam), loc=loc)

def _lower_class_const_write_target(
        e: TpyFieldAccess, lc: '_LowerCtx', declared: dict[str, TpyType],
        loc) -> 'tuple[THIRClassConstant, THIRExpr | None, str | None]':
    """The class-constant WRITE lvalue:
    (target, recv_eval, recv_wrap). The target is the bare qualified name as
    a real lvalue; a receiver with observable cost splits into a leading
    statement -- the unproven-Optional check (`::tpy::deref_check(c);`, a
    declared pointer-name receiver only: pointer_value_expr passes a local
    pointer through bare, and the deref_optional_check field-receiver shape
    stays out) or the effect discard (`static_cast<void>(<recv>);`, the
    receiver lowered through its own arms at RECEIVER use). Called only
    behind _class_const_write_target_ok (scalar lvalue + receiver shapes)."""
    analyzer = lc.analyzer
    cpp = _class_constant_cpp(e, analyzer, lc.render_type)
    if cpp is None:
        raise ThirUnsupported("field.class_const_spelling", detail=True)
    target = THIRClassConstant(result_type=analyzer.get_expr_type(e), cpp=cpp,
                               form=Form.VALUE, loc=loc)
    if e.needs_optional_runtime_check:
        _witness("field_write.cc_recv_check")
        recv: THIRExpr = THIRName(result_type=analyzer.get_expr_type(e.obj),
                                  name=e.obj.name, loc=loc)
        return target, recv, "::tpy::deref_check({0})"
    if _class_const_pure_receiver(e, declared, analyzer):
        return target, None, None
    _witness("field_write.cc_recv_effect")
    recv = _lower_expr(e.obj, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.RECEIVER),
                       subscript_prechecked=isinstance(e.obj, TpySubscript))
    return target, recv, "static_cast<void>({0})"

def _lower_module_var(e: TpyFieldAccess, rtype: 'TpyType | None',
                      lc: '_LowerCtx', module_name: str, var_name: str,
                      *, loc, allow_ref_pointer: bool = False) -> THIRExpr:
    """A module-variable read: the dotted `pkg.sub.X` (the sema-attached
    `module_var_access` pair) or the bare `mod.X` (a MODULE-binding
    receiver). Renders the fixed registered spelling; the value-leaf /
    str-bytes-view families land it bare in every admitted sink like a
    seeded global name read. A non-value pointer-slot var's `(*slot)` read
    stays out (its receiver/consumer wrapping is not pinned by this arm)
    UNLESS `allow_ref_pointer` -- set by the PINNED consumers: the print
    sink (`::tpy::as_ostream(<read>)`), a method receiver
    (`(*environ).update(..)`), a native-slot arg (`__len__((*environ))`),
    and a membership operand."""
    analyzer = lc.analyzer
    viewfam = _resolved_viewfam_value(rtype, analyzer)
    ok = (_eligible_scalar(rtype) or _eligible_char(rtype)
          or _eligible_enum(rtype, analyzer) is not None
          or _eligible_ptr_value(rtype, analyzer)
          or viewfam is not None)
    if not ok and not allow_ref_pointer:
        raise ThirUnsupported("field.module_var_type", detail=True)
    cpp = _module_var_read_cpp(module_name, var_name, analyzer)
    if cpp is None:
        raise ThirUnsupported("field.module_var_lookup", detail=True)
    _witness("field.module_var")
    return THIRModuleVar(result_type=rtype, cpp=cpp,
                         global_binding=module_global_binding(module_name, var_name, rtype, analyzer),
                         form=_viewfam_result_form(viewfam), loc=loc)

def lower_print_sink(file_val: TpyExpr, lc: '_LowerCtx',
                     declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a `print(file=...)` sink expression. A module-variable read
    (`sys.stderr` / `sys.stdout`) is a reference-type pointer slot the
    general module-var arm excludes, but the sink's consumer is pinned
    (`::tpy::as_ostream(<sink>)`), so route the bare `(*slot)` read here.
    Any other sink shape rejects as `print.file_sink_shape`."""
    analyzer = lc.analyzer
    if isinstance(file_val, TpyIfExpr):
        # A ternary sink (`file=sys.stdout if c else sys.stderr`): each arm is
        # an admitted sink read of its own and the `?:` over two same-type
        # lvalues is one too, so the pinned consumer takes the select whole.
        _witness("print.file_ternary_sink")
        return THIRIfExpr(
            result_type=analyzer.get_expr_type(file_val),
            cond=_lower_truthy(file_val.condition, lc, declared),
            then=lower_print_sink(file_val.then_expr, lc, declared),
            orelse=lower_print_sink(file_val.else_expr, lc, declared),
            form=Form.BORROW, loc=getattr(file_val, "loc", None))
    if isinstance(file_val, TpyFieldAccess):
        rtype = analyzer.get_expr_type(file_val)
        loc = getattr(file_val, "loc", None)
        mv = _module_var_access_pair(file_val, declared, analyzer)
        if mv is not None:
            return _lower_module_var(file_val, rtype, lc, *mv, loc=loc,
                                     allow_ref_pointer=True)
    if (isinstance(file_val, TpyName) and file_val.name in declared
            and file_val.name not in lc.pointers
            and file_val.name not in lc.narrow.narrowed
            and file_val.name not in lc.inline_narrowed):
        # A record-NAME sink (`file=f` on an open() TextFile local /
        # `file=s` on a user Writable record): the bare lvalue under the
        # pinned `::tpy::as_ostream(<sink>)` consumer.
        rtype = analyzer.get_expr_type(file_val)
        ru = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
              if rtype is not None else None)
        if (isinstance(ru, NominalType)
                and analyzer.registry.get_record_for_type(ru) is not None):
            _witness("print.file_name_sink")
            return THIRName(result_type=rtype, name=file_val.name,
                            form=Form.BORROW,
                            loc=getattr(file_val, "loc", None))
    if isinstance(file_val, TpySubscript):
        # A container-ELEMENT sink (`file=outs[0]`): the checked element read
        # is a record lvalue, which the pinned `::tpy::as_ostream(<sink>)`
        # consumer takes like any other record sink; the subscript arm owns
        # the receiver/index decision.
        rtype = analyzer.get_expr_type(file_val)
        if _f1_record(rtype, analyzer):
            _witness("print.file_elem_sink")
            return _lower_expr(file_val, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.RECEIVER))
    raise ThirUnsupported("print.file_sink_shape", detail=True)

def _viewfam_result_form(t: 'TpyType | None') -> Form:
    """The form of a RESOLVED str/bytes-family result value: a view
    (string_view / span) is BORROW -- an owned sink copies it -- an owned
    string/vector is STORAGE (lands bare in every sink), and None (outside
    the family) is VALUE. Shared by the subscript / free-call / marker-call /
    method-call result tagging; the slice and coerce arms keep their own
    mappings (a slice result is never outside the family, a coerce carries
    its inner form)."""
    if t is None:
        return Form.VALUE
    if is_str_view_type(t) or is_bytes_view_type(t):
        return Form.BORROW
    return Form.STORAGE

def _lower_checked_container_elem(
        e: TpyExpr, slot: TpyType | None, lc: '_LowerCtx',
        declared: dict[str, TpyType], *, threaded: bool, forced: bool,
        allow_record: bool = False, allow_nested: bool = False,
        allow_optional: bool = False,
        retype_scalars: bool = True,
        suppress_move: bool = False,
        tuple_elem: bool = False,
        frame_bare_tuple: bool = False,
        allow_temps: bool = False) -> THIRExpr:
    if not _container_lit_elem_ok(
            e, slot, declared, lc.analyzer, threaded=threaded, forced=forced,
            allow_record=allow_record, allow_nested=allow_nested,
            allow_optional=allow_optional, pointers=lc.pointers):
        raise ThirUnsupported("expr.container_literal")
    return _lower_container_elem(
        e, slot, lc, declared, retype_scalars=retype_scalars,
        suppress_move=suppress_move, tuple_elem=tuple_elem,
        frame_bare_tuple=frame_bare_tuple, allow_temps=allow_temps)


def _lower_ru_literal(e: TpyExpr, ut: 'UnionType', lc: '_LowerCtx',
                      declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a list/dict literal admitted by `_ru_container_literal_ok` for a
    recursive-union wrapper slot, with the element targeting that goes with
    it: a non-empty list spells the typed prefix (`std::vector<W>{...}` --
    the `list[AliasRef]` shape; the union_prefix arm spells
    the identical member type for nested literals), an empty list keeps the
    emit's bare-to_cpp empty spelling, a dict is self-describing
    (`::tpy::ordered_map<K, W>({{k, v}, ...})`, both spellings bare
    to_cpp), None renders the wrapper's monostate via the
    union-typed literal, and scalar literals keep their target-less render."""
    at = lc.analyzer.get_expr_type(e)
    at = resolve_pending_container(at, lc.analyzer) or at
    loc = getattr(e, "loc", None)
    # A GENERIC alias instance types the literal as the WRAPPER itself
    # (`Tree[int]`), not as `list[Tree[int]]` the way the non-generic
    # `AliasRef` form usually does -- but the render spells the same
    # container around it, so synthesise that container for the spelling
    # and the result type. An OUTER non-generic literal at a wrapper-annotated
    # slot (`tree: Expr = [...]` / `return {...}` at Own[V]) types AS the
    # wrapper too and takes the same synthesis.
    instance = (isinstance(at, RecursiveAliasInstanceType)
                or (isinstance(at, (UnionType, AliasRef))
                    and _wrapper_union_like(at, lc.analyzer) is not None))
    if isinstance(e, TpyArrayLiteral):
        elems = tuple(_lower_ru_elem(x, ut, lc, declared)
                      for x in e.elements)
        ct = make_list(at) if instance else at
        return THIRContainerLiteral(
            result_type=ct, elements=elems,
            typed_brace_cpp=lc.render_type(ct) if e.elements else None,
            loc=loc)
    assert isinstance(e, TpyDictLiteral)
    keys = tuple(_lower_expr(k, lc, declared, use=_NESTED_ARG_USE)
                 for k in e.keys)
    vals = tuple(_lower_ru_elem(v, ut, lc, declared) for v in e.values)
    ct = (make_dict(lc.analyzer.get_expr_type(e.keys[0]), at)
          if instance and e.keys else at)
    return THIRContainerLiteral(result_type=ct, elements=keys, values=vals,
                                loc=loc)


def _lower_ru_elem(x: TpyExpr, ut: 'UnionType', lc: '_LowerCtx',
                   declared: dict[str, TpyType]) -> THIRExpr:
    if isinstance(x, (TpyArrayLiteral, TpyDictLiteral)):
        return _lower_ru_literal(x, ut, lc, declared)
    if isinstance(x, TpyNoneLiteral):
        # The wrapper's None alternative: the union-typed literal takes the
        # emit's monostate render (the AliasRef-target None literal).
        return THIRLiteral(result_type=ut, value=None, form=Form.VALUE,
                           loc=getattr(x, "loc", None))
    lowered = _lower_expr(x, lc, declared, use=_NESTED_ARG_USE)
    if isinstance(x, TpyName) and _is_move_source(x, lc):
        # An element wraps ANY movable name at its last use, a wrapper
        # NAME element included.
        lowered = THIRMove(result_type=lowered.result_type, value=lowered,
                           form=lowered.form, loc=getattr(x, "loc", None))
    return lowered


def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          **kwargs) -> THIRExpr:
    """`_lower_container_elem_impl` under the container-element context flag.
    Only the IMMEDIATE element gets the storage-render grant
    (`elem_storage`, a per-call lowering mode that is consumed at the
    ternary dispatch and never propagates); the flag set here marks the
    whole subtree so a deeper Optional ternary can REJECT instead of
    guessing between a leaked storage wrap and the pointer render."""
    saved = lc.in_container_elem
    lc.in_container_elem = True
    try:
        return _lower_container_elem_impl(e, slot, lc, declared, **kwargs)
    finally:
        lc.in_container_elem = saved


def _lower_container_elem_impl(e: TpyExpr, slot: TpyType | None,
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType], *,
                               retype_scalars: bool = True,
                               suppress_move: bool = False,
                               tuple_elem: bool = False,
                               frame_bare_tuple: bool = False,
                               allow_temps: bool = False) -> THIRExpr:
    """Lower one container-literal element / dict key / dict value into its
    slot. A view-form str source (BORROW -- a string_view param/local, a slice,
    a StrView-returning call) into an owned `std::string` slot copies
    explicitly via the S1 view->owned `THIRFormConvert` (`std::string(x)`) --
    the `_wrap_for_owned_slot`/`_view_source_to_owned` chokepoint at element
    positions. A literal (VALUE, const char[N]) and an owned source (STORAGE --
    an owned local, a String local, a concat/f-string rvalue) land bare as
    a brace-init pass-through; scalar slots never wrap.
    A str/bytes FIELD source is admitted here for EVERY element position:
    its render is that same bare member read plus the form-keyed wrap, so
    the verdict is this chokepoint's, not each sink's own row."""
    su0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
        if slot is not None else None
    if isinstance(su0, AnyType):
        return _lower_elem_into_any(e, su0, lc, declared)
    # An open-`T` PARAM at a `T` element slot (`self.items = [first]` inside a
    # generic record): the param arrives in the instantiation's parameter form,
    # which at a view family is a view over the storage the element slot spells,
    # so the construction is explicit and keyed on `T` alone.
    if (isinstance(su0, TypeParamRef)
            and _open_tparam_param_read(e, declared, lc.prescan.param_names)):
        _witness("containerlit.open_tparam_elem")
        return THIRFormConvert(
            result_type=su0, value=_lower_expr(e, lc, declared),
            form=Form.STORAGE, loc=getattr(e, "loc", None))
    # A wrapper-union element slot (`list[V]` / `dict[str, V]` over a
    # recursive alias): None is the monostate member and a nested literal
    # spells its typed container -- the ru-literal element renders. Scalar
    # literals / names / member ctors keep their shared bare rows below.
    if isinstance(e, (TpyNoneLiteral, TpyArrayLiteral, TpyDictLiteral)):
        _wru_elem = _eligible_wrapper_union(su0, lc.analyzer)
        if _wru_elem is not None:
            _witness("containerlit.wrapper_elem")
            return _lower_ru_elem(e, _wru_elem, lc, declared)
    # `retype_scalars` says whether a scalar element carries a
    # target: dict keys/values and set elements do (target-typed float32/
    # BigInt literal wraps); list/Array elements do NOT (bare renders).
    if isinstance(e, TpyNoneLiteral):
        # Only the Optional[scalar] element slot admits a bare None: the
        # STORAGE-form None renders `std::nullopt` (the None-into-
        # optional-target render).
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
        return THIRLiteral(result_type=su, value=None, form=Form.STORAGE,
                           loc=getattr(e, "loc", None))
    su_tup = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
        if slot is not None else None
    if (isinstance(e, TpyName) and isinstance(su_tup, TupleType)
            and su_tup.has_pointer_repr_element()
            and _value_tuple(su_tup, lc.analyzer) is None):
        # A bare NAME at a NON-VALUE tuple element slot (`[t for t in src]`
        # at `list[tuple[int32, Cell]]`): the whole borrow-form binding
        # copies into storage via the non-move tuple_to_storage (the
        # element wrap over the loop-var read). Only a declared, plain
        # (non-pointer, non-narrowed) name is admitted.
        if (e.name in declared and e.name not in lc.pointers
                and e.name not in lc.narrow.narrowed
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[e.name]))) == su_tup):
            _witness("containerlit.tuple_name_storage")
            inner = _lower_expr(e, lc, declared)
            # A name that ALREADY reads storage form -- a loop var over a
            # storage container -- is copied by the element init itself, so
            # the bare read is the right render and a convert here would be
            # a redundant identity lift. Only a borrow-form source owes the
            # wrap.
            #
            # The storage-lift rule has two more NAME arms this does not
            # implement, both kept out upstream rather than here:
            # a pointer-repr tuple GLOBAL never reaches `declared` (only a
            # value-tuple global is admitted), and a MIXED-render local rejects
            # at the decl arm, so neither can enter `storage_tuple_locals`.
            if e.name in lc.storage_tuple_locals:
                return inner
            return THIRFormConvert(
                result_type=su_tup,
                value=inner,
                form=Form.STORAGE, move=False,
                loc=getattr(e, "loc", None))
        raise ThirUnsupported("expr.tuple_literal.slot")
    if (isinstance(e, TpySubscript) and isinstance(su_tup, TupleType)
            and su_tup.has_pointer_repr_element()
            and _value_tuple(su_tup, lc.analyzer) is None):
        # A whole storage-tuple ELEMENT read at a non-value tuple slot
        # (`[items[0]]` -> `{::tpy::__getitem__(items, 0)}`): the checked
        # element lvalue is the storage value already, so it copies bare --
        # no tuple_to_storage wrap (the gate admitted only a
        # same-type F1 element read).
        _witness("containerlit.tuple_subscript_storage")
        return _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE,
                         pos=SinkPos.CONTAINER_ELEM, forms=_ONLY_TUPLE_SOURCE))
    if (isinstance(su_tup, TupleType) and su_tup.has_pointer_repr_element()
            and _value_tuple(su_tup, lc.analyzer) is None):
        _mos = _mixed_own_storage_source(e, su_tup, frozenset(), lc.analyzer)
        if _mos is not None:
            # A MIXED-own-tuple call source: the owning slot materializes
            # the borrowed half via the NON-move lift
            # (`{::tpy::tuple_to_storage<std::tuple<Box, Box>>(
            # make_mixed(b))}`; a `copy()` wrapper peeled -- the wrap IS
            # the copy).
            _witness("containerlit.tuple_mixed_call")
            return THIRFormConvert(
                result_type=su_tup,
                value=_lower_expr(
                    _mos, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.VALUE,
                                 pos=SinkPos.CONTAINER_ELEM, forms=_ONLY_BTUPLE_SLOT)),
                form=Form.STORAGE, move=False, loc=getattr(e, "loc", None))
    if isinstance(e, TpyTupleLiteral):
        # A nested value-tuple element lowers against its slot TupleType;
        # tuple literals have no generic _lower_expr arm.
        vt = _value_tuple_return(slot, lc.analyzer)
        if vt is None:
            # Value-union elements too (`list[tuple[str, int32 | str]]`):
            # the literal member lands bare in the variant slot of the
            # spelled tuple ctor.
            _vtu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
                if slot is not None else None
            if (isinstance(_vtu, TupleType)
                    and all(_value_tuple_element_ok(_el, lc.analyzer)
                            or _eligible_value_union(unwrap_readonly(
                                unwrap_ref_type(unwrap_send_sync(_el))))
                            is not None
                            for _el in _vtu.element_types)):
                vt = _vtu
                _witness("containerlit.tuple_union_elem")
        if vt is not None:
            return _lower_tuple_literal(e, vt, lc, declared)
        # A non-value tuple element stores via `tuple_to_storage<S>(S{...})`:
        # the inner spells the storage tuple S with its members lowered as
        # storage container elements, and the STORAGE FormConvert emits the
        # helper (mirrors the MIL pointer-repr-tuple field write). The
        # resumable frame-EMPLACE position spells a SEQUENCE element BARE
        # instead (typed_brace_init: the emplaced brace is already
        # storage-typed, so no per-element wrap applies) -- vector
        # and fixed-size `Array[T, N]` alike. `frame_bare_tuple` is the
        # positive signal from the list/Array literal arm rather than a
        # proxy: the dict VALUE position, which KEEPS the wrap, differs from
        # a vector element on `retype_scalars` but from an Array element on
        # nothing else.
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
            if slot is not None else None
        if (isinstance(su, TupleType)
                and len(e.elements) == len(su.element_types)
                and _tuple_literal_has_ref_elements(e, su)
                and (_tuple_elem_slots_ptr_optional(su)
                     or _tuple_elem_slots_record_lvalue(e, su, lc.analyzer))):
            # The has_ref_elements path: the inner is the
            # BORROW form (`std::tuple<T*, T*>{&(t1), nullptr}`) and the
            # STORAGE convert emits `tuple_to_storage<S>(...)` over it. The
            # storage-direct sibling below serves the all-value-slot shape.
            # `storage_context` gives a simple-lvalue record member the
            # CONST_REF slot (`const P*` + `&(c)`).
            # `rvalue_ok`: a container-literal element is a flush position
            # for the `tuple_value_to_borrow` source tuple, so a ctor rvalue
            # at a pointer-repr slot renders there exactly as it does at a
            # call arg (`tuple_to_storage<S>(tuple_value_to_borrow<B>(
            # std::tuple<P, int32_t>{P(1), 10}))`).
            _witness("containerlit.tuple_borrow_storage")
            return THIRFormConvert(
                result_type=su,
                value=_lower_borrow_tuple_literal(e, su, lc, declared,
                                                  storage_context=True,
                                                  rvalue_ok=True),
                form=Form.STORAGE, move=False, loc=getattr(e, "loc", None))
        if isinstance(su, TupleType) and len(e.elements) == len(su.element_types):
            inner = THIRTupleLiteral(
                result_type=su,
                elements=tuple(
                    _lower_container_elem(e.elements[i], su.element_types[i],
                                          lc, declared, tuple_elem=True)
                    for i in range(len(e.elements))),
                loc=getattr(e, "loc", None))
            if lc.resumable_leaf_mode and frame_bare_tuple:
                _witness("containerlit.tuple_frame_elem")
                return inner
            if not su.has_pointer_repr_element():
                # The elem wrap keys on
                # `slot.has_pointer_repr_element()` (+ the always-true-for-
                # literals `needs_tuple_storage_lift`): a slot with no
                # pointer-repr element -- e.g. an outer tuple whose only
                # non-value member is a NESTED tuple -- stores its bare
                # brace directly; each member replays the decision at its
                # own slot.
                _witness("containerlit.tuple_storage_bare")
                return inner
            _witness("containerlit.tuple_storage")
            return THIRFormConvert(result_type=su, value=inner,
                                   form=Form.STORAGE, move=False,
                                   loc=getattr(e, "loc", None))
        raise ThirUnsupported("expr.tuple_literal.slot")
    if isinstance(e, (TpyListComprehension, TpySetComprehension,
                      TpyDictComprehension)):
        # A comprehension element renders the same position-independent
        # `({...})` stmt-expr as the decl-init arm, target-typed by the slot's
        # resolved container (reachable only through the dict-comp VALUE slot
        # widening -- every other elem-slot classifier rejects container
        # slots). Late import: comprehensions imports this module.
        from .comprehensions import _lower_comprehension
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
        if isinstance(su, UnionType) and not su.needs_wrapper():
            # The list-member union slot (the elem gate's comp sibling
            # row): the comp lowers against the MEMBER; the variant's
            # converting ctor absorbs the stmt-expr result. A
            # multi-list-member union picks the member matching the
            # comp's OWN sema type (the gate's disambiguation).
            _cml = [m for m in su.members if is_list(unwrap_readonly(m))]
            if len(_cml) != 1:
                _cet = lc.analyzer.get_expr_type(e)
                _cetu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    _cet))) if _cet is not None else None)
                _cml = [m for m in _cml
                        if unwrap_readonly(m) == _cetu]
            if len(_cml) != 1:
                raise ThirUnsupported("expr.container_literal")
            _witness("comp.union_member_source")
            su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_cml[0])))
        pointers = _comp_shadow_pointers(lc.pointers, declared,
                                         lc.analyzer)
        comp = _lower_comprehension(e, su, lc, declared, pointers)
        _witness("comp.nested")
        return comp
    su_elem = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
        if slot is not None else None
    if (_own_genrec_return(slot) is not None
            and isinstance(e, (TpyArrayLiteral, TpyDictLiteral))
            and _ru_instance_literal_ok(e, lc.analyzer)):
        # A container LITERAL at an `Own[genrec]` element slot (the widened
        # value-tuple RETURN element): the ru-instance spelled render, a
        # prvalue into the by-value slot -- the element twin of the
        # arg.genrec_own_literal row.
        _witness("containerlit.genrec_own_elem")
        return _lower_ru_literal(e, lc.analyzer.get_expr_type(e),
                                 lc, declared)
    if isinstance(su_elem, TypeParamRef):
        # A `T` element slot: the plain declared NAME renders bare
        # (`return {x};`), a MOVABLE one through the shared exit move
        # (`make_vector<T>(std::move(a))` -- the literal switched to the
        # reserve+emplace helper on the same verdict, and a brace-init's const
        # initializer_list elements would have copied). Narrowed and
        # pointer-form names carry alias / deref renders this row does not
        # spell.
        _tp_copy_src = copy_call_arg(e, lc.analyzer)
        if (isinstance(_tp_copy_src, TpyName)
                and _tp_copy_src.name in declared
                and _tp_copy_src.name not in lc.pointers
                and _tp_copy_src.name not in lc.narrow.narrowed
                and _tparam_value(lc.analyzer.get_expr_type(_tp_copy_src))):
            # `copy(x)` of an open-`T` source: the copy-construct rvalue
            # (`T(x)`), spelled from the SOURCE type -- the call's own open-T
            # arm renders it, so the slot only has to admit the shape.
            _witness("containerlit.tparam_copy_elem")
            return _lower_expr(e, lc, declared)
        if not (isinstance(e, TpyName) and e.name in declared
                and e.name not in lc.pointers
                and e.name not in lc.narrow.narrowed):
            raise ThirUnsupported("expr.container_literal")
        _witness("containerlit.tparam_elem")
        return _elem_maybe_move(_lower_expr(e, lc, declared), e, lc,
                                suppress_move=suppress_move,
                                tuple_elem=tuple_elem)
    if (isinstance(su_elem, UnionType) and not su_elem.needs_wrapper()
            and isinstance(e, TpyName)):
        # A NAME at a value-union element slot. The verdict is the BINDING's
        # (the union narrow binding-form fence): a narrowed alias reads the
        # bare member (`{__a, ..}` -- the converting ctor absorbs it, no
        # lift); a TRACKED ptr-variant binding lifts via to_value_variant;
        # a member-record binding renders bare/moved (the record twin).
        # Anything else -- an assign-narrowed or untracked union binding --
        # rejects rather than guessing a render.
        _eb = declared.get(e.name)
        _ebu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_eb)))
                if _eb is not None else None)
        if isinstance(_ebu, UnionType):
            if e.name in lc.narrow.narrowed:
                # The alias arm renders the extraction (`__a`); the
                # divergent-binding fence is exactly this shape, verified
                # here, so lift it for the read.
                _witness("containerlit.union_narrowed_elem")
                return _lower_expr(e, lc, declared,
                                   allow_union_divergent=True)
            if e.name in lc.ptr_variant_locals:
                # The sink is the UNION slot, so the read consumes the WHOLE
                # variant -- an assign-narrowed read type is immaterial here
                # (the member-typed-sink miscompile the fence guards cannot
                # arise), like the union call-slot caller.
                _witness("containerlit.union_name_lift")
                return THIRFormConvert(
                    result_type=su_elem,
                    value=_lower_expr(e, lc, declared,
                                      allow_union_divergent=True),
                    form=Form.STORAGE, move=False,
                    loc=getattr(e, "loc", None))
            raise ThirUnsupported("expr.container_literal")
    if (isinstance(e, TpyArrayLiteral)
            and ((isinstance(su_elem, UnionType)
                  and not su_elem.needs_wrapper())
                 or (isinstance(su_elem, OptionalType)
                     and (is_list(_oa := unwrap_readonly(unwrap_ref_type(
                          unwrap_send_sync(su_elem.inner))))
                          or is_array(_oa))))):
        # A nested ARRAY literal at a union value slot with a UNIQUE
        # list/array member -- or at an Optional[list] element (the same
        # union_prefix render through the Optional inner): the literal
        # lowers against the member and spells the typed ctor
        # (`std::vector<::tpy::BigInt>{1, 2}`).
        if isinstance(su_elem, OptionalType):
            _cms = [su_elem.inner]
        else:
            _cms = [m for m in su_elem.members
                    if is_list(unwrap_readonly(m))
                    or is_array(unwrap_readonly(m))]
        if len(_cms) != 1:
            raise ThirUnsupported("expr.container_literal")
        _member = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_cms[0])))
        _lit = _lower_expr(e, lc, declared,
                           use=_ExprUse(slot_target=_member))
        if isinstance(_lit, THIRContainerLiteral):
            _lit = replace(_lit, typed_brace_cpp=lc.render_type(_member))
        _witness("containerlit.union_member_prefix")
        return _lit
    if (isinstance(e, TpyDictLiteral) and isinstance(su_elem, UnionType)
            and not su_elem.needs_wrapper()):
        # The DICT sibling of the union member-prefix row: the literal
        # lowers against the unique dict member; its self-describing
        # ordered_map render lands in the variant via the converting ctor.
        _dms = [m for m in su_elem.members if is_dict(unwrap_readonly(m))]
        if len(_dms) != 1:
            raise ThirUnsupported("expr.container_literal")
        _dmember = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_dms[0])))
        _witness("containerlit.union_member_prefix")
        return _lower_expr(e, lc, declared,
                           use=_ExprUse(slot_target=_dmember))
    _cp_elem = (unwrap_readonly(_unwrap_own(su_elem))
                if isinstance(su_elem, OwnType) else su_elem)
    if _f1_record(_cp_elem, lc.analyzer):
        # `(copy(p), n)` -- the shared copy-construct row (`Point(p)`) at a
        # record element slot (the `Own[Point]` tuple-element flavor peels
        # -- the Own is the slot's ownership spelling, the copy render is
        # the record's own). Gated on the SLOT being that record: the copy
        # renders the source's own spelling, so a slot needing any element
        # wrap must keep taking the wrapping tail below.
        copy_row = _lower_copy_record(e, lc, declared, slot_type=_cp_elem,
                                      exact=True, loc=getattr(e, "loc", None))
        if copy_row is not None:
            _witness("containerlit.copy_record")
            return copy_row
    if (isinstance(e, TpyMethodCall)
            and _f1_record(lc.analyzer.get_expr_type(e), lc.analyzer)):
        # A record-returning method-call rvalue element (`rc.clone()`): the
        # bare call lands in the make_vector/make_ordered_set slot, so it
        # lowers under BORROW_BIND -- the record-return admission the
        # owned-record decl bind uses (the element consumes the rvalue by
        # value; no move wrap, a call rvalue is not a move source).
        return _lower_expr(e, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    # An element reads through a full deref and may move: an F2
    # pointer-local name derefs (`(*p)` -- indirect_read), and a movable
    # owned local at its last use moves into the element slot, off the
    # `movable_locals` + `all_last_uses` facts.
    # A non-wrapper-union slot's admitted field read (the elem gate's
    # containerlit.union_field_elem row) renders the bare member the same
    # way an owned-str sink's does -- grant it the field admission here so
    # gate and lowering agree.
    _vu_field = (isinstance(e, TpyFieldAccess)
                 and isinstance(su0, UnionType)
                 and not su0.needs_wrapper())
    if _vu_field:
        _vff = lc.analyzer.get_expr_type(e)
        _vffu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_vff)))
                 if _vff is not None else None)
        if is_list(_vffu) or is_dict(_vffu) or is_set(_vffu):
            # The CONTAINER flavor of the union-field element: the bare
            # member read copies into the value variant (`{"labels",
            # ml.labels}`) -- BORROW_BIND admits the container field
            # result, the record-methodcall arm's twin.
            return _lower_expr(
                e, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    if _container_storage_call_rvalue(e, su0, lc.analyzer):
        # A call whose result IS the slot's storage container: STORAGE use
        # so the call's own result gate admits the container return, then
        # the prvalue lands bare in the element slot -- the element face of
        # the `Own[container]` argument row (`out.append(make(i))`).
        return _lower_expr(e, lc, declared,
                           use=_ExprUse(pos=SinkPos.CONTAINER_ELEM,
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=allow_temps))
    el = _lower_expr(
        e, lc, declared, use=_ExprUse(pos=SinkPos.CONTAINER_ELEM,
                                      allow_temps=allow_temps),
        elem_storage=True,
        # A nested LIST element takes its type from the outer brace, so an
        # EMPTY literal deduces there (`{{}, {1}}`) even though scalar
        # retyping is off for a list's elements.
        container_threaded=retype_scalars or is_list(su0),
        # An Optional ELEMENT slot fed by a source that is still Optional-typed
        # (un-narrowed): the element IS the optional, so the read goes whole
        # rather than through the value-opt narrowed deref. A NARROWED source
        # keeps its deref -- that render is already witnessed here. A
        # str/bytes-family Optional PARAM stays out: it spells a VIEW inner
        # against the slot's owned buffer, so it needs the per-element
        # materialization shim, which is not this bare read.
        allow_whole_optional=(
            isinstance(su0, OptionalType)
            and isinstance(unwrap_readonly(lc.analyzer.get_expr_type(e)),
                           OptionalType)
            and not (isinstance(e, TpyName)
                     and _value_opt_view_param(e.name, lc))),
        field_owned_str_ok=isinstance(e, TpyFieldAccess))
    if retype_scalars:
        el = _slot_literal_retype(el, slot, lc)
    # A value-`Optional[str]` slot (a widened value-tuple RETURN element) wraps
    # its str-view source exactly like the bare owned-str slot: the tuple
    # brace-init relies on the implicit `std::string -> std::optional<std::string>`
    # conversion, so the element renders `std::string(view)` (not a spelled
    # optional wrap). Resolve the str target through the
    # Optional inner. The converts apply BEFORE the move wrap: the owned-slot
    # wrap comes first, then the move takes the WRAPPED temp
    # (`std::move(std::string((*a)))`, the make_vector face).
    ou = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
          if slot is not None else None)
    view_slot = (ou.inner if isinstance(ou, OptionalType)
                 and not ou.uses_pointer_repr() else slot)
    st = _resolved_str_value(view_slot, lc.analyzer) if view_slot is not None else None
    if st is not None and is_str_type(st) and el.form is Form.BORROW:
        el = THIRFormConvert(result_type=st, value=el, form=Form.STORAGE,
                             loc=getattr(e, "loc", None))
    else:
        # The bytes sibling (S6): a view-form source into an owned `bytes`
        # element slot copies via `::tpy::Bytes` (a bytes literal lowers
        # STORAGE and renders its owned form bare). Same Optional peel as the
        # str arm -- the value-`Optional[bytes]` slot takes the copy too.
        bt = (_resolved_bytes_value(view_slot, lc.analyzer)
              if view_slot is not None else None)
        if bt is not None and is_bytes_type(bt) and el.form is Form.BORROW:
            el = THIRFormConvert(result_type=bt, value=el, form=Form.STORAGE,
                                 loc=getattr(e, "loc", None))
    return _elem_maybe_move(el, e, lc, suppress_move=suppress_move,
                            tuple_elem=tuple_elem)


def _elem_maybe_move(el: THIRExpr, e: TpyExpr, lc: '_LowerCtx', *,
                     suppress_move: bool, tuple_elem: bool) -> THIRExpr:
    """Wrap one lowered container element in `std::move` if its source is
    movable -- the element-lowering exit rule, shared by the dispatch tail and
    the arms that return early.

    A list-repeat element is copied into EVERY slot (one source, N slots), so
    it must never move; moving would use-after-move the source for slots
    1..N-1 -- that is what `suppress_move` says."""
    if not suppress_move and _container_elem_move_source(
            e, lc, tuple_elem=tuple_elem):
        _witness("containerlit.move")
        return THIRMove(result_type=el.result_type, value=el, form=el.form,
                        loc=getattr(e, "loc", None))
    return el

def _container_elem_move_source(e: TpyExpr, lc: '_LowerCtx', *,
                                tuple_elem: bool = False) -> bool:
    """The move verdict for a container element / dict key-value / tuple
    element.

    THE TWO SINKS DISAGREE on a value-typed movable payload, and the split
    belongs to the sinks, not to the movable set:

    - CONTAINER element (a list/dict/set literal, a container insert) --
      ANY movable name moves, value-typed or not, so a frame-promoted
      BigInt reaches `make_vector<BigInt>(std::move(n))`.
    - TUPLE element (`tuple_elem=True`) -- such a name renders BARE,
      witnessed by `generators/yield_loop_body_local_borrow`. That is what
      removing the filter outright broke, and why it is keyed on the SINK
      the caller represents rather than on the payload type.

    The value-Optional binding is the exception on the tuple side: its narrowed
    `(*a)` element does move, including through the view->owned coerce wrap
    (`std::string((*a))`, the make_vector face).

    The ptr-variant clause this used to carry is GONE, and stays gone: no
    decl arm promotes a ptr-variant local, so the set excludes it already."""
    if not _is_move_source(e, lc):
        return False
    if not tuple_elem:
        return True
    inner = _peel_coerce(e)
    if isinstance(inner, TpyName) and (
            _value_opt_scalar_binding(inner.name, lc)
            or _value_opt_view_param(inner.name, lc)):
        return True
    t = lc.analyzer.get_expr_type(e)
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return not t.is_value_type()

def _elem_needs_emplace(e: TpyExpr, slot: 'TpyType | None',
                        lc: '_LowerCtx') -> bool:
    """Whether ONE element forces the reserve+emplace helpers over the
    brace-init (the `make_container` switch, asked per element/slot pair).

    std::initializer_list elements are const, so a brace-init COPY-constructs
    every element from them. Two sources lose by that:

    - a movable local at its last use -- its `std::move` wrap would be
      silently dropped (`_container_elem_move_source`);
    - a call whose prvalue result already IS the slot's storage container
      (`_container_storage_call_rvalue` -- `[make(1), make(2)]`). The element
      render is the bare call, so a brace-init copies the whole container and
      a non-copyable payload does not compile at all.

    The nocopy-SLOT switch is the callers' own leg: it reads the slot type
    alone, with no element to ask about."""
    return (_container_elem_move_source(e, lc)
            or _container_storage_call_rvalue(e, slot, lc.analyzer))


def _witness_container_elem_fam(slot: 'TpyType | None', analyzer) -> None:
    """Fold the container-literal element-family witness for a non-empty
    literal's admitted slot (the widened families only; the scalar/str slice
    predates the face set)."""
    fam = _container_lit_slot_family(slot, analyzer) if slot is not None else None
    if fam in ("enum", "optional", "tuple", "container", "record", "bytes"):
        _witness(f"containerlit.{fam}_elem")

def _lower_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                         lc: '_LowerCtx',
                         declared: dict[str, TpyType], *,
                         use: _ExprUse = _ExprUse()) -> THIRExpr:
    """Lower a value-tuple literal against its slot or reject its shape.

    The sink's `allow_temps` reaches the elements: it is the flush right the
    channel already carries, so the two lowerers do not spell a second one.
    Default OFF: most of this lowerer's callers sit at positions with no
    flush point to land a `__tmp_N` decl at."""
    if (len(e.elements) != len(slot.element_types)
            or (e.elem_capture
                and any(c is not TupleElemCapture.VALUE
                        for c in e.elem_capture))):
        raise ThirUnsupported("expr.tuple_literal")

    def lower_element(i: int) -> THIRExpr:
        elem_slot = slot.element_types[i]
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem_slot)))
        if isinstance(bare, TupleType):
            _witness("ret.tuple_nested_elem")
        elif isinstance(bare, OwnType):
            _witness("ret.tuple_own_elem")
        elif isinstance(bare, OptionalType) and not bare.uses_pointer_repr():
            if _resolved_str_value(bare.inner, lc.analyzer) is not None:
                _witness("ret.tuple_opt_str_elem")
            else:
                _witness("ret.tuple_opt_elem")
        return _lower_container_elem(
            e.elements[i], elem_slot, lc, declared, tuple_elem=True,
            allow_temps=use.allow_temps)

    return THIRTupleLiteral(
        # The spelling resolves pending elements: `to_cpp` does not, so a
        # slot carrying a str element's `PendingStrType` would raise at
        # emit -- the spelling comes from the ELEMENTS' resolved types.
        result_type=_resolve_tuple_pending(slot, lc.analyzer),
        elements=tuple(lower_element(i) for i in range(len(e.elements))),
        tuple_layout=tuple_layout(_resolve_tuple_pending(slot, lc.analyzer), lc.analyzer),
        loc=getattr(e, "loc", None))

def _lower_borrow_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                                lc: '_LowerCtx',
                                declared: dict[str, TpyType], *,
                                target_readonly: bool = False,
                                rvalue_ok: bool = False,
                                use: _ExprUse = _ExprUse(),
                                storage_context: bool = False,
                                consuming: bool = False) -> THIRExpr:
    """Lower a tuple literal at a BORROW-form slot (`std::tuple<..., T*>`) --
    the ref-element path reduced to the lvalue-NAME
    subset. Per element the slot-info ladder's sliced arms: VALUE mode lowers
    through the value container-elem rows (bare, no lift); REF/CONST_REF mode
    admits a plain pointer-repr non-value lvalue NAME -- `&(name)`, or bare
    for an already-pointer name -- and spells `T*` / `const T*`. Everything
    else (rvalue borrow elements and their tuple_value_to_borrow helper
    machinery, pointer-repr Optional / union / TypeParamRef element slots,
    non-name lvalues) rejects with a named detail.

    `consuming` marks the OWNING sink (`Own[tuple[...]]`), which keeps the
    tuple past the full-expression: an rvalue element must land in a value
    slot there, since a borrow's element address dies with the call, and a
    generic element takes the bare `T` its destination is spelled with -- it
    is never bare-pointer repr, so no later storage lift materializes it.
    The same flag moves out of the borrow slots the sink does keep."""
    analyzer = lc.analyzer
    n = len(e.elements)
    if n != len(slot.element_types):
        note_detail("btuple.arity")
        raise ThirUnsupported("expr.tuple_literal")
    parts: list[str] = []
    src_parts: list[str] = []
    lowered: list[THIRExpr] = []
    lifts: list[bool] = []
    # `consuming` (the Own[tuple] element sink, `pairs.append((a, b))`):
    # a movable last-use lvalue element moves over its pointer render
    # (`std::move(&(a))`), carried as a per-element wrap.
    wraps: list['str | None'] = []
    any_rvalue = False
    captures: list[TupleElemCapture] = []
    for i in range(n):
        et = unwrap_ref_type(slot.element_types[i])
        et_bare = unwrap_readonly(unwrap_send_sync(et))
        mode = (e.elem_capture[i] if i < len(e.elem_capture) else None)
        if consuming and isinstance(et, TypeParamRef):
            # Ahead of the sema capture, like the slot-info ladder: an owning
            # slot spells a generic element with the bare `T` its destination
            # already uses, not the borrow-form trait.
            mode = TupleElemCapture.VALUE
        elif mode is None:
            # No sema annotation (yield/arg contexts, target always provided
            # here): a value element picks VALUE, every other element the
            # target-provided REF arm (CONST_REF under a readonly target) --
            # the slot-info ladder's non-storage tail. In a STORAGE context
            # (a container element feeding tuple_to_storage) a simple-lvalue
            # element is CONST_REF: the borrow tuple is a read-only transient
            # and a const-pointer slot is required to bind a const member
            # source (the slot-info storage rule; a
            # readonly-typed sema read forces const the same way).
            if et_bare.is_value_type():
                mode = TupleElemCapture.VALUE
            elif (isinstance(et_bare, OptionalType)
                    and et_bare.uses_pointer_repr()):
                # The slot-info ladder FORCES REF for pointer-repr Optional
                # slots (uniform T* shape) ahead of the lvalue rule.
                mode = (TupleElemCapture.CONST_REF if target_readonly
                        else TupleElemCapture.REF)
            elif emit_prims.is_simple_lvalue(e.elements[i]):
                mode = (TupleElemCapture.CONST_REF
                        if (storage_context
                            or isinstance(
                                analyzer.get_expr_type(e.elements[i]),
                                ReadonlyType))
                        else (TupleElemCapture.CONST_REF if target_readonly
                              else TupleElemCapture.REF))
            elif consuming:
                # A non-lvalue element at an owning slot: the tuple outlives
                # the call, so the element is stored by value rather than as
                # a borrow whose address dies with the full-expression.
                mode = TupleElemCapture.VALUE
            else:
                mode = (TupleElemCapture.CONST_REF if target_readonly
                        else TupleElemCapture.REF)
        elif target_readonly and mode == TupleElemCapture.REF:
            mode = TupleElemCapture.CONST_REF
        captures.append(mode)
        if mode == TupleElemCapture.VALUE:
            lowered.append(_lower_container_elem(
                e.elements[i], slot.element_types[i], lc, declared,
                tuple_elem=True))
            lifts.append(False)
            wraps.append(None)
            parts.append(lc.render_type(et_bare))
            src_parts.append(lc.render_type(et_bare))
            continue
        # Borrow slot: the earlier slot-info arms (pointer-repr Optional,
        # pointer-variant union, TypeParamRef) each spell their own form --
        # sliced out; only the plain pointer-repr non-value arm is spelled.
        elem = e.elements[i]
        et_slot = et_bare
        if (isinstance(et_slot, OptionalType) and et_slot.uses_pointer_repr()
                and rvalue_ok and not isinstance(elem, TpyNoneLiteral)
                and is_rvalue_source(analyzer, elem)):
            # A pointer-repr Optional elem slot takes the same `Inner*` /
            # source-`Inner` spelling as the plain borrow elem for an RVALUE
            # element (`tuple[Box | None, ...]` <- `Box(...)`, the
            # elem_target.inner unwrap); NAME sources keep the slot-info
            # optional arm -> the reject below.
            et_slot = unwrap_readonly(unwrap_ref_type(et_slot.inner))
        if (isinstance(et_slot, OptionalType) and et_slot.uses_pointer_repr()
                and mode in (TupleElemCapture.REF, TupleElemCapture.CONST_REF)):
            # The pointer-repr Optional elem slot's witnessed faces: `None`
            # renders `nullptr`, a plain non-narrowed lvalue NAME lifts
            # `&(name)` (`(p, 42)` at a `tuple[Point | None, int32]` ctor
            # slot). Storage-form Optional names (optional_to_ptr), pointer
            # names, and non-name lvalues keep the reject below.
            inner_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                et_slot.inner)))
            if (inner_t.value_form() is ValueForm.BORROW_REF
                    and TupleType._element_is_pointer_repr(inner_t)):
                ptr_base = lc.render_type(inner_t)
                slot_part = (f"const {ptr_base}*"
                             if mode == TupleElemCapture.CONST_REF
                             else f"{ptr_base}*")
                node = None
                if isinstance(elem, TpyNoneLiteral):
                    node = THIROptionalPtrArg(
                        result_type=et_bare, form=Form.BORROW,
                        loc=getattr(elem, "loc", None))
                elif (isinstance(elem, TpyName) and elem.name in declared
                      and elem.name not in lc.narrow.narrowed
                      # This row only knows the addr_of render, so an
                      # already-pointer source defers rather than lifting.
                      and not _already_pointer_source(elem, lc)
                      and not isinstance(
                          unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                              declared[elem.name]))), OptionalType)):
                    node = THIROptionalPtrArg(
                        result_type=et_bare, form=Form.BORROW,
                        value=_lower_expr(
                            elem, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                        addr_of=True, loc=getattr(elem, "loc", None))
                elif (isinstance(elem, TpyName) and elem.name in declared
                      and elem.name not in lc.narrow.narrowed
                      # A SAME-repr pointer-Optional name (`n` at
                      # `tuple[Box | None, ..]`, an OPT_PTR/OPTIONAL_TO_PTR
                      # local): already the element's `T*`, passes bare
                      # (`{n, &(y)}`). A pointer-form local/loop var OF THE
                      # POINTEE (`it` over list[P] at a `P | None` slot) is
                      # the same raw `P*` and passes bare too
                      # (`{prev, it}`).
                      and _already_pointer_source(elem, lc)
                      and (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                          declared[elem.name]))) == et_bare
                           or unwrap_readonly(unwrap_ref_type(
                               unwrap_send_sync(declared[elem.name])))
                           == inner_t
                           # ... and the `Ptr[T]` collapse flavor: the
                           # binding IS the element's `T*`, bare too.
                           or (isinstance(
                                   (_pel := unwrap_readonly(unwrap_ref_type(
                                       unwrap_send_sync(
                                           declared[elem.name])))),
                                   PtrType)
                               and unwrap_readonly(_pel.pointee)
                               == inner_t))):
                    node = THIROptionalPtrArg(
                        result_type=et_bare, form=Form.BORROW,
                        value=_raw_pointer_slot(_lower_expr(
                            elem, lc, declared,
                            use=_ExprUse(
                                result=_ExprResultUse.RECEIVER,
                                pos=SinkPos.CONTAINER_ELEM,
                                forms=_ONLY_PTR_OPT_PASSTHROUGH))),
                        addr_of=False, loc=getattr(elem, "loc", None))
                elif (isinstance(elem, TpySubscript)
                      and not isinstance(elem.index, TpySlice)
                      and _container_record_elem_subscript(elem, declared,
                                                           analyzer,
                                                           lc.pointers)):
                    # A container-element lvalue (`(items[i], None)` at the
                    # yield slot): the element read's address lifts like
                    # the name row (`&(items[...])`).
                    node = THIROptionalPtrArg(
                        result_type=et_bare, form=Form.BORROW,
                        value=_lower_expr(
                            elem, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                        addr_of=True, loc=getattr(elem, "loc", None))
                if node is not None:
                    _witness("btuple.elem_optptr")
                    parts.append(slot_part)
                    src_parts.append(slot_part)
                    lowered.append(node)
                    lifts.append(False)
                    # The consuming sink moves a movable last-use lvalue's
                    # pointer render whole (`std::move(&(a))`); a None
                    # literal (`nullptr`) never moves.
                    wraps.append("std::move({0})"
                                 if (consuming and isinstance(elem, TpyName)
                                     and _is_move_source(elem, lc))
                                 else None)
                    continue
        if (isinstance(et_slot, UnionType)
                and _eligible_ptr_union(et_slot, analyzer) is not None
                and mode in (TupleElemCapture.REF,
                             TupleElemCapture.CONST_REF)
                and rvalue_ok and is_rvalue_source(analyzer, elem)):
            # A ptr-variant UNION element slot: std::variant has no
            # mutable->const converting ctor, so the destination slot is
            # the CONST pointer variant the param's borrow form spells
            # (`_tuple_literal_slot_info`'s union arm), while the helper's
            # source tuple holds the VALUE variant -- the member ctor
            # rvalue is absorbed by its converting ctor. Both spellings
            # come from the union type itself, never hand-assembled.
            _witness("btuple.elem_ptr_union")
            parts.append(et_slot.to_cpp_return_const())
            src_parts.append(lc.render_type(et_slot))
            lowered.append(_lower_expr(
                elem, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             allow_temps=use.allow_temps,
                             slot_target=et_slot)))
            lifts.append(False)
            wraps.append(None)
            any_rvalue = True
            continue
        if (isinstance(et_slot, (OptionalType, UnionType, TypeParamRef))
                or et_slot.value_form() is not ValueForm.BORROW_REF
                or not TupleType._element_is_pointer_repr(et_slot)):
            note_detail("btuple.elem_slot")
            raise ThirUnsupported("expr.tuple_literal")
        if is_rvalue_source(analyzer, elem):
            # Rvalue-into-borrow: the element renders VALUE-form into the
            # helper's source tuple; `tuple_value_to_borrow` takes its
            # address inside. Only the call-arg sink admits it (sema
            # pre-rejects rvalue elements at borrow yields; decl rvalues go
            # VALUE-capture) -- elsewhere the guard is defensive.
            if not rvalue_ok:
                note_detail("btuple.elem_rvalue")
                raise ThirUnsupported("expr.tuple_literal")
            ptr_base = lc.render_type(unwrap_readonly(unwrap_ref_type(
                et_slot)))
            parts.append(f"const {ptr_base}*"
                         if mode == TupleElemCapture.CONST_REF
                         else f"{ptr_base}*")
            src_parts.append(ptr_base)
            # The sink's flush right rides into a call-shaped rvalue element
            # (its nested arg temps hoist at the enclosing statement, like
            # any nested call arg's). A `copy(x)`
            # rvalue takes the shared copy-construct row (`P(keep)`) -- the
            # generic call tail rejects copy() by design, so this sink
            # intercepts it like every other.
            copy_row = _lower_copy_record(
                elem, lc, declared, loc=getattr(elem, "loc", None))
            lowered.append(copy_row if copy_row is not None else _lower_expr(
                elem, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             allow_temps=use.allow_temps)))
            lifts.append(False)
            wraps.append(None)
            any_rvalue = True
            continue
        _btuple_elem_passthrough = False
        if isinstance(elem, TpyName):
            if elem.name not in declared or elem.name in lc.narrow.narrowed:
                note_detail("btuple.elem_source")
                raise ThirUnsupported("expr.tuple_literal")
            # An already-pointer name renders bare into the `T*` slot: an
            # Optional-ptr param / pointer local, a `Ptr[T]` source, or
            # `self` in a sync method (`this` is a prvalue pointer --
            # `&(this)` is ill-formed; a resumable method's `__self` is a
            # `Record&` field and DOES lift). A plain lvalue takes `&(...)`.
            lift = not _already_pointer_source(elem, lc)
        elif isinstance(elem, TpyFieldAccess):
            # An F1-record FIELD element lifts `&(<member read>)` over the
            # bare member render (`&(this->inner)` at a @readonly
            # borrow-tuple return); markers-clean + admitted receiver like
            # every field consumer. An Optional-DECLARED field stays out:
            # the un-deref'd `&(this->maybe)` is what that shape would
            # spell (an optional<T>* into the T* slot -- ill-formed,
            # g++-verified; see BUGS.md), so it rejects until that is
            # fixed.
            if not (_field_markers_clean(elem)
                    and _field_receiver_ok(elem, declared, lc.analyzer)
                    and not isinstance(
                        unwrap_readonly(unwrap_send_sync(
                            _field_decl_type(elem, declared, lc.analyzer)
                            or et_bare)),
                        OptionalType)):
                note_detail("btuple.elem_source")
                raise ThirUnsupported("expr.tuple_literal")
            _witness("btuple.elem_field")
            lift = not _already_pointer_source(elem, lc)
        elif isinstance(elem, TpySubscript):
            # A container-element lvalue subscript lifts `&(<row render>)`
            # (`&(::tpy::__getitem__(items, i))`). A subscript whose OBJECT
            # is itself a borrow-form tuple already yields `T*`
            # (`std::get<i>(t)`) -- the pass-through face, not sliced.
            if _subscript_yields_borrow_ptr(elem, lc):
                # `std::get<i>(p)` is already the element POINTER, so it
                # passes through with no `&(...)` -- taking its address
                # would build a `T**`. The shared helper is what decides
                # that: a STORAGE-form tuple (an `auto&&` alias local) holds
                # its elements by value, so `std::get` there is a `T&` and
                # the address-of below is required.
                _witness("btuple.elem_btuple_subscript")
                lift = False
                _btuple_elem_passthrough = True
            else:
                lift = not _already_pointer_source(elem, lc)
        else:
            note_detail("btuple.elem_source")
            raise ThirUnsupported("expr.tuple_literal")
        ptr_base = lc.render_type(unwrap_readonly(unwrap_ref_type(et_bare)))
        slot_part = (f"const {ptr_base}*"
                     if mode == TupleElemCapture.CONST_REF else f"{ptr_base}*")
        parts.append(slot_part)
        src_parts.append(slot_part)
        # RECEIVER use: the element is consumed under the `&(...)` lift (an
        # address-of position), so a record-element subscript's `T&` lvalue
        # row admits -- a plain value position would reject it as a copy.
        # A borrow-tuple element read is prechecked by the lift decision
        # above (its object is a borrow-form tuple, so `std::get<i>` yields
        # the element pointer); the generic tuple-subscript arm has no row
        # for that read on its own.
        elem_lowered = _lower_expr(
            elem, lc, declared,
            use=_ExprUse(result=_ExprResultUse.RECEIVER),
            subscript_prechecked=_btuple_elem_passthrough)
        lowered.append(elem_lowered if lift
                       else _raw_pointer_slot(elem_lowered))
        if consuming and lift and _is_move_source(elem, lc):
            wraps.append("std::move(&({0}))")
            lifts.append(False)
        else:
            wraps.append(None)
            lifts.append(lift)
    spelled = f"std::tuple<{', '.join(parts)}>"
    elem_wraps = (tuple(wraps) if any(w is not None for w in wraps)
                  else None)
    if any_rvalue:
        # The value-form source tuple's slots: value cpp for rvalue elements,
        # the borrow (pointer) part for lvalue ones -- their lifts happen
        # inside the source, and the helper passes them through.
        _witness("btuple.value_to_borrow")
        return THIRTupleValueToBorrow(
            result_type=slot, dst_cpp=spelled,
            src_cpp=f"std::tuple<{', '.join(src_parts)}>",
            elements=tuple(lowered), addr_of=tuple(lifts),
            elem_wraps=elem_wraps,
            loc=getattr(e, "loc", None))
    _witness("btuple.literal")
    return THIRBorrowTupleLiteral(
        result_type=slot, spelled_cpp=spelled,
        elements=tuple(lowered), addr_of=tuple(lifts),
        elem_wraps=elem_wraps or (),
        tuple_layout=tuple_layout(slot, analyzer, captures=tuple(captures)),
        loc=getattr(e, "loc", None))


def _lower_yield_tuple_literal(yv_src: TpyTupleLiteral, yt_bare: 'TupleType',
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType], *,
                               generic_face: str, reject: str,
                               target_readonly: bool = False) -> THIRExpr:
    """The literal-vs-builder selection of the resumable tuple YIELD slot
    (and the return slot): the borrow builder for a pointer-repr slot, the generic
    val_or_ptr builder, else the spelled value literal -- widened by the
    Own-record storage elements the RETURN slot admits (`yield (i, Box(i*10))`
    -> `std::tuple<::tpy::BigInt, Box>{i, Box(..)}`; Own elements are held
    by value, so borrow and storage coincide and the render is
    position-independent). Ladder order is interchangeable across the two
    callers: a generic tuple admits only TypeParamRef + narrow value
    elements, so it can never be pointer-repr."""
    analyzer = lc.analyzer
    if yt_bare.has_pointer_repr_element():
        return _lower_borrow_tuple_literal(yv_src, yt_bare, lc, declared,
                                           target_readonly=target_readonly)
    gt = _generic_value_tuple_return(yt_bare, analyzer)
    if gt is not None:
        lowered = _lower_generic_tuple_literal(yv_src, gt, lc, declared)
        _witness(generic_face)
        return lowered
    vt = _value_tuple_nested(yt_bare, analyzer)
    if vt is None and _tuple_has_own_element(yt_bare):
        # Own-element gate: value-opt elements (the rest of the return
        # family) stay unwitnessed at the yield slot and keep rejecting.
        vt = _value_tuple_return(yt_bare, analyzer)
        if vt is not None:
            _witness("yield.own_tuple_literal")
    if vt is None:
        raise ThirUnsupported(reject)
    return _lower_tuple_literal(yv_src, vt, lc, declared)


def _lower_generic_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                                 lc: '_LowerCtx',
                                 declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a tuple literal at a slot with TypeParamRef elements -- the
    `want_val_or_ptr_form` arm reduced to its
    witnessed subset. A generic slot spells `::tpy::val_or_ptr_t<T>` and
    wraps its element `::tpy::to_val_or_ptr<slot>(...)` (address-of into a
    pointer slot, construct/copy into a value slot -- decided at
    instantiation); concrete value elements render bare into their base
    slots via the container-elem rows. VALUE captures only (sema stamps
    VALUE on return-tuple elements; a CONST_REF stamp would flip the slot
    to `val_or_cptr_t` -- unwitnessed). A generic element admits a plain
    declared NAME that is neither narrowed, pointer-form, nor a move
    source -- a movable source would move (VALUE capture + non-value
    slot_inner) and a narrowed or pointer-form one would deref, renders
    this slice does not spell -- or a container
    SUBSCRIPT lvalue off such a name (the bare checked `__getitem__`
    feeding the wrap)."""
    analyzer = lc.analyzer
    if len(e.elements) != len(slot.element_types):
        note_detail("gentuple.arity")
        raise ThirUnsupported("expr.tuple_literal")
    parts: list[str] = []
    lowered: list[THIRExpr] = []
    wraps: list['str | None'] = []
    for i in range(len(e.elements)):
        et = slot.element_types[i]
        mode = (e.elem_capture[i] if i < len(e.elem_capture)
                else TupleElemCapture.VALUE)
        if mode is not TupleElemCapture.VALUE:
            note_detail("gentuple.elem_capture")
            raise ThirUnsupported("expr.tuple_literal")
        elem = e.elements[i]
        if isinstance(et, TypeParamRef):
            # Pointer-form locals excluded like narrowed/movable names:
            # they deref before the to_val_or_ptr wrap, a render this
            # slice does not spell. A container
            # SUBSCRIPT off a plain declared name (`(ks[i], vs[i])` at a
            # generic yield slot) reads the bare checked `__getitem__` --
            # the wrap consumes it like the name read.
            elem_ok = (isinstance(elem, TpyName) and elem.name in declared
                       and elem.name not in lc.narrow.narrowed
                       and elem.name not in lc.pointers
                       and not _is_move_source(elem, lc))
            if (not elem_ok and isinstance(elem, TpySubscript)
                    and isinstance(elem.obj, TpyName)
                    and elem.obj.name in declared
                    and elem.obj.name not in lc.narrow.narrowed
                    and elem.obj.name not in lc.pointers
                    # An RVALUE subscript (value-returning user __getitem__,
                    # stepped slice) takes the owned-slot else-arm, not the
                    # to_val_or_ptr wrap -- lvalue reads only.
                    and not is_rvalue_source(analyzer, elem)):
                elem_ok = True
            if (not elem_ok and isinstance(elem, TpyFieldAccess)
                    # A `self.value` read at the open slot (`return
                    # (self.value, int32(1))`): an lvalue like the name read,
                    # so the to_val_or_ptr wrap consumes it identically. An
                    # rvalue field source (property getter) takes the
                    # owned-slot else-arm, and a movable one moves --
                    # both excluded like the name row's.
                    and not is_rvalue_source(analyzer, elem)
                    and not _is_move_source(elem, lc)):
                _witness("gentuple.field_elem")
                elem_ok = True
            if not elem_ok:
                note_detail("gentuple.elem_source")
                raise ThirUnsupported("expr.tuple_literal")
            part = f"::tpy::val_or_ptr_t<{lc.render_type(et)}>"
            parts.append(part)
            lowered.append(_lower_expr(elem, lc, declared))
            wraps.append(f"::tpy::to_val_or_ptr<{part}>({{0}})")
            continue
        if _value_tuple_element_ok(et, analyzer):
            parts.append(lc.render_type(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(et)))))
            lowered.append(_lower_container_elem(elem, et, lc, declared,
                                                 tuple_elem=True))
            wraps.append(None)
            continue
        note_detail("gentuple.elem_slot")
        raise ThirUnsupported("expr.tuple_literal")
    _witness("gentuple.literal")
    return THIRBorrowTupleLiteral(
        result_type=slot,
        spelled_cpp=f"std::tuple<{', '.join(parts)}>",
        elements=tuple(lowered), addr_of=tuple(False for _ in lowered),
        elem_wraps=tuple(wraps), loc=getattr(e, "loc", None))


def _static_repr_subst_cpp(e, lc: '_LowerCtx') -> dict[str, str]:
    """The per-param Adapter spelling for type-params sema marked
    representational (`Rc.new(cat)` at an `Rc[Pet]` hint -- U spells
    `::tpy::Adapter<Pet, Cat>`). Params without a bound or an inferred arg
    are skipped: they take the plain `type_to_cpp` render."""
    marked = getattr(e, "representational_subst_params", None)
    fi = e.resolved_function_info
    if not marked or fi is None or not fi.type_params \
            or not e.inferred_type_args:
        return {}
    canonical = fi.root
    subst = dict(zip(fi.type_params, e.inferred_type_args))
    out: dict[str, str] = {}
    for tp_name in marked:
        bound = canonical.type_param_bounds.get(tp_name)
        u_sub = subst.get(tp_name)
        if bound is None or u_sub is None:
            continue
        t_sub = substitute_type_params_simple(bound, subst)
        out[tp_name] = dynamic_adapter_type(
            t_sub, lc.render_type(u_sub), lc.analyzer)
    return out


def _compose_static_targs(cpp_class: str, record_info, cpp_method: str,
                          e, lc: '_LowerCtx') -> 'tuple[str, tuple[str, ...] | None]':
    """The class/method targs split shared by the two generic-static
    composers: class-level targs spell on the class (`Cls<CA>`),
    method-level targs ride template_args_cpp, and the dependent
    `template ` keyword fires ONLY when method targs follow -- a
    `template` keyword with no following `<...>` would be a C++ syntax
    error. A repr-subst-marked METHOD param spells its Adapter override;
    class args always render plain."""
    n_class = (len(record_info.type_params)
               if record_info is not None and record_info.type_params else 0)
    class_args = e.inferred_type_args[:n_class]
    method_args = e.inferred_type_args[n_class:]
    if class_args:
        spelled = ", ".join(lc.render_type(unwrap_ref_type(t))
                            for t in class_args)
        cpp_class = f"{cpp_class}<{spelled}>"
    template_kw = ("template "
                   if method_args and any(contains_type_param(t)
                                          for t in class_args)
                   else "")
    fi = e.resolved_function_info
    method_names = (list(fi.type_params[n_class:])
                    if fi is not None and fi.type_params else [])
    repr_cpp = _static_repr_subst_cpp(e, lc)
    targs = (tuple(
        repr_cpp[method_names[j]]
        if j < len(method_names) and method_names[j] in repr_cpp
        else lc.render_type(unwrap_ref_type(t))
        for j, t in enumerate(method_args)) or None)
    return (f"{cpp_class}::{template_kw}{cpp_method}", targs)


def _generic_static_callee(e, lc: '_LowerCtx') -> 'tuple[str, tuple[str, ...] | None]':
    """Compose a same-module generic STATIC call's callee spelling --
    the static tail: the class name (implicit-stdlib peers qualify) over
    the shared class/method targs split."""
    analyzer = lc.analyzer
    record_info = analyzer.registry.get_record(e.obj.name)
    # `cls` inside a @classmethod names no record of its own -- spell sema's
    # resolved owner, the same fallback the plain qualified arm takes.
    if record_info is None and e.static_call_owner is not None:
        record_info = e.static_call_owner
        class_name = record_info.name
    else:
        class_name = e.obj.name
    compiler = get_current_compiler()
    implicit = (compiler._implicit_stdlib_set() if compiler is not None
                else set())
    if (record_info is not None and record_info.module is not None
            and record_info.module in implicit
            and record_info.module != analyzer.ctx.module_name):
        class_name = qualified_cpp_name(record_info.module, record_info.name)
    return _compose_static_targs(class_name, record_info,
                                 escape_cpp_name(e.method), e, lc)


def _generic_module_static_callee(e, lc: '_LowerCtx') -> 'tuple[str, tuple[str, ...] | None]':
    """Compose a module-qualified generic STATIC call's callee spelling --
    the module-static arm: the class qualifies through the
    module namespace (or the native rename) and the method spells its
    @native rename when present, over the shared class/method targs split."""
    analyzer = lc.analyzer
    fi = e.resolved_function_info
    class_short = e.obj.field
    record_info = analyzer.registry.find_record_by_qname(
        f"{e.user_module_call}.{class_short}")
    if record_info is not None and record_info.is_native and record_info.native_name:
        cpp_class = record_info.native_name
    else:
        cpp_class = qualified_cpp_name(e.user_module_call, class_short)
    cpp_method = (fi.native_name if fi is not None and fi.native_name
                  else escape_cpp_name(e.method))
    return _compose_static_targs(cpp_class, record_info, cpp_method, e, lc)


def _lower_chained_compare_stmtexpr(e, rtype, lc: '_LowerCtx',
                                    declared: dict[str, TpyType], loc, *,
                                    temps_ok: bool = False):
    """The complex-intermediate arm of a chained compare (the GCC stmt-expr
    with single-eval `auto&& _cmpI` temps). Each pair lowers through the shared
    comparison path (`_lower_expr` -> THIRBinOp), so operand targets and the
    per-side casts are identical to a standalone comparison; the temp-vs-
    inline binding rule: intermediates always bind, the first endpoint binds
    iff non-simple, the last is always inlined. Operand i's init is read off
    its owning pair (pair 0's left for i=0, pair i-1's right otherwise) so it
    carries that pair's target."""
    _witness("chained_compare.stmt_expr")
    # Pairs past the first sit behind a passed compare: comparators i>=2
    # evaluate conditionally, so their pairs carry the flush right plus the
    # cond_eager exit check (audited temps defer through the emit's i>=2
    # regions; unaudited would-defer ones reject).
    _pair_use = _ExprUse(allow_temps=temps_ok)
    pairs = [_lower_expr(e.pairs[0], lc, declared)] + [
        _lower_expr(p, lc, declared, use=_pair_use, cond_eager=temps_ok)
        for p in e.pairs[1:]]
    n = len(pairs)
    all_operands = [e.left] + e.comparators
    inits = [pairs[0].left]
    for j in range(1, n + 1):
        inits.append(pairs[j - 1].right)
    bound = []
    for i in range(n + 1):
        if 0 < i < n:
            bound.append(True)
        elif i == 0:
            bound.append(not emit_prims.is_duplicable_expr(all_operands[0]))
        else:
            bound.append(False)
    return THIRChainedCompareStmtExpr(
        result_type=rtype,
        inits=tuple(inits),
        bound=tuple(bound),
        ops=tuple(p.op for p in pairs),
        left_casts=tuple(p.left_cast for p in pairs),
        right_casts=tuple(p.right_cast for p in pairs),
        form=Form.VALUE,
        loc=loc,
    )


def _lower_generic_plain_call(e, callee_cpp, lc: '_LowerCtx',
                              declared: dict[str, TpyType], *,
                              temp_args: bool, form, loc) -> THIRCall:
    """Lower a plain TPy generic free call (`pick(1, 2)` ->
    `pick<int32_t>(__tmp_1, __tmp_2)`): explicit template args spelled
    type_to_cpp_stored per inferred arg, args
    against the ROOT stub's substituted param slots. A temporary arg into a
    TypeParamRef slot (`param_val_or_ref_t<T>` in C++ -- an lvalue-ref
    binding) hoists a named temp: `<resolved.to_cpp()> __tmp_N =
    <target-typed init>;` (TempState.create) -- scalar/str/None literals
    and by-value call rvalues, each with its own init render; lvalue
    NAMEs and the remaining shapes ride `_lower_call_arg` against the
    substituted slot."""
    analyzer = lc.analyzer
    root, subst = _generic_root_subst(e, analyzer)
    dcbp = root.const_borrow_params
    args = []
    for i, (a, p) in enumerate(zip(e.args, root.params)):
        ptype = unwrap_ref_type(p.type)
        resolved = substitute_type_params_simple(ptype, subst)
        if isinstance(a, TpyVarargPack):
            # The pack render is callee-kind-independent (`std::array<const
            # T*, N> __tmp_N{...}` + `::tpy::varargs<const T>(__tmp_N)`) and
            # `_lower_vararg_pack` already applies the readonly-slot const
            # override; the generic path just never routed to it. The slot is
            # passed UNSUBSTITUTED like the plain path -- the element spelling
            # comes from the pack, the slot supplies only const-ness.
            _witness("call.generic_vararg_pack")
            args.append(_lower_vararg_pack(a, ptype, lc, declared,
                                           temp_args=temp_args))
            continue
        if isinstance(a, TpyTypeParamConstruct):
            # A `T()` default filling an omitted param renders the resolved
            # brace-init in place (`int32_t{}`), ahead of every other row.
            _witness("call.tparam_default_construct")
            args.append(THIRDefaultConstruct(
                result_type=resolved, cpp_type=lc.render_type(resolved),
                form=Form.VALUE, loc=getattr(a, "loc", None)))
            continue
        if not _generic_plain_arg_ok(
                a, ptype, subst, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed),
                param_names=lc.prescan.param_names,
                storage_tuple_locals=frozenset(lc.storage_tuple_locals),
                overload=root):
            raise ThirUnsupported(call_reject_reason("expr.call"))
        peeled = _peel_coerce(a)
        # The one verdict this seam takes, the same object the free-call
        # GATE and the method / ctor seams take: whether a bare-T slot's
        # instantiation owes a temp.
        gslot = _generic_arg_slot(a, ptype, resolved, declared,
                                  lc.prescan.param_names, analyzer)

        def _ref_slot_temp(init: THIRExpr,
                           slot: 'TpyType | None' = None) -> None:
            # The shared ref-slot named temp (`R __tmp_N = <init>;`) a
            # temporary hoists into a `param_val_or_ref_t<T>` binding. `slot`
            # overrides the declared type where the resolved slot carries a
            # decoration the TEMP must not (a `readonly[T]` slot's own const).
            if gslot is None or not gslot.needs_temp:
                # The INSTANTIATED slot is `const T&`, which binds the
                # rvalue for the full expression: the temp is the
                # reference-typed instantiation's binding aid, and the
                # monomorphic twin passes the same rvalue inline. Each row
                # keeps its own init render, so only the decl disappears.
                _witness("call.generic_rvalue_slot")
                args.append(init)
                return
            if not temp_args:
                raise ThirUnsupported(
                    "generic ref-slot literal temp outside a flush position")
            _witness("argtemp.generic_ref_slot")
            st = resolved if slot is None else slot
            # TempState.create's movable: the resolved slot's own type.
            args.append(THIRArgTemp(
                result_type=st, cpp_type=st.to_cpp(),
                init=init, form=Form.VALUE,
                movable=unwrap_ref_type(st).is_movable(),
                loc=getattr(a, "loc", None)))

        if (isinstance(ptype, TypeParamRef)
                and isinstance(peeled, (TpyIntLiteral,
                                        TpyFloatLiteral,
                                        TpyBoolLiteral))):
            _ref_slot_temp(_slot_literal_retype(
                _lower_expr(a, lc, declared), resolved, lc))
        elif (isinstance(ptype, TypeParamRef)
              and isinstance(peeled, TpyNoneLiteral)):
            # `identity[None](None)` -> `std::monostate __tmp_N =
            # std::monostate{};` -- the unit-typed STORAGE literal.
            _ref_slot_temp(THIRLiteral(result_type=resolved, value=None,
                                       form=Form.STORAGE,
                                       loc=getattr(a, "loc", None)))
        elif (isinstance(ptype, TypeParamRef)
              and (isinstance(peeled, TpyStrLiteral)
                   or isinstance(peeled, (TpyCall, TpyMethodCall)))):
            # A str literal / by-value call into a T slot: the init renders
            # bare (`std::string __tmp_N = "hello";` / `= make_str();`).
            _ref_slot_temp(_lower_expr(a, lc, declared))
        elif (isinstance(ptype, TypeParamRef)
              and isinstance(peeled, TpyArrayLiteral)):
            # A container literal into a T slot (`use([1, 2])` ->
            # `std::vector<int32_t> __tmp_2 = {1, 2};`): the same ref-slot
            # temp, its init the target-typed brace the decl sink renders.
            _ref_slot_temp(_lower_expr(a, lc, declared,
                                       use=_ExprUse(
                                           result=_ExprResultUse.STORAGE,
                                           slot_target=resolved)))
        elif (isinstance(ptype, TypeParamRef)
              and isinstance(a, TpyTupleLiteral)
              and (vt_slot := (_value_tuple(resolved, lc.analyzer)
                               or _value_tuple_nested(resolved, lc.analyzer)))
              is not None):
            # The inline spelled brace prvalue (`std::tuple<int32_t,
            # std::string>{2, "second"}`): unlike the str/list ref-slot
            # temps, the target-typed render passes straight through --
            # the const-ref template param binds it.
            _witness("call.generic_tuple_literal")
            args.append(_lower_tuple_literal(a, vt_slot, lc, declared))
        elif (isinstance(peeled, TpyArrayLiteral) and is_list(resolved)
              and temp_args
              and _container_literal_arg(peeled, resolved, lc.analyzer)):
            # The generic twin of `_lower_free_call_arg`'s container-literal
            # row: a `std::vector<T>&` param cannot bind a brace prvalue, so
            # the literal hoists the same named `__tmp_N` (BORROW form, slot
            # spelling) rather than rendering in place.
            slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(resolved)))
            _witness("argtemp.generic_container_literal")
            args.append(THIRArgTemp(
                result_type=slot, cpp_type=slot.to_cpp(), movable=unwrap_ref_type(slot).is_movable(),
                init=_lower_literal_arg(
                    a, slot, lc, declared,
                    "generic container-literal arg on the make_vector path"),
                form=Form.BORROW, loc=getattr(a, "loc", None)))
        else:
            args.append(_lower_call_arg(
                a, resolved, lc, declared, temp_args=temp_args,
                protocol_slots=True, kind=CallArgKind.GENERIC,
                readonly_target=dcbp is not None and i in dcbp))
    return THIRCall(
        result_type=lc.analyzer.get_expr_type(e),
        callee=e.func_name,
        args=tuple(args),
        callee_cpp=callee_cpp,
        template_args_cpp=tuple(lc.render_type_stored(t)
                                for t in e.inferred_type_args),
        form=form,
        loc=loc,
    )


def _container_call_temp_arg(a: TpyExpr, ptype: 'TpyType | None',
                             analyzer, *,
                             frame_capturing: bool = False) -> 'TpyType | None':
    """A container-returning rvalue CALL into a plain free call's concrete
    container ref param (`f(list(argv[i:]))`) hoists a `__tmp_N` ref-param
    temp. Returns the unwrapped slot type when the row applies --
    the ONE fact shared by gate admission and the ArgTemp lowering arm.
    Exact slot/result match only; borrow (`T&`) returns are excluded
    (they bind the ref param bare, no temp)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)) or ptype is None:
        return None
    pr = unwrap_ref_type(unwrap_send_sync(ptype))
    # The hoist applies to MUTABLE-ref params (`is_ref_param()`) always, and
    # for readonly reference slots only when the callee is a frame-capturing
    # factory (`frame_capturing`): there the statement-scoped inline
    # `const T&` bind would dangle. A sync callee keeps the inline bind
    # (CPython drop timing), so the row must decline it here too.
    if not (isinstance(pr, TpyType)
            and (pr.is_ref_param()
                 or (frame_capturing and is_readonly_ref_param(pr)))):
        return None
    slot = unwrap_readonly(pr)
    # The NON-RECORD half of the reference axis: a record rvalue at a ref
    # param has its own rows in the arg ladder, so the two stay disjoint.
    if not _f1_container_ref(slot):
        return None
    at = analyzer.get_expr_type(a)
    atb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if atb != slot or not is_rvalue_source(analyzer, a):
        return None
    return slot


def _lower_static_isinstance(e: TpyCall, lc: '_LowerCtx',
                             rtype: 'TpyType | None') -> 'THIRCall | None':
    """The STATIC type-param isinstance family (`isinstance(x, Animal)` /
    `isinstance(x, (Dog, Cat))` on a bounded-T subject): a
    per-instantiation compile-time trait -- a disjunction of
    `::tpy::isinstance_static<M, decltype(x)>()` over the (tuple-normalized
    union's) members -- with NO extraction alias, so the same spelled
    expression renders at every position. Shared by the truthy-condition
    arm and the value-position call arm; each witnesses its own face."""
    if not (e.isinstance_var is not None and e.isinstance_type_param
            and e.isinstance_type is not None
            and not e.isinstance_deref_depth):
        return None
    if isinstance(e.isinstance_type, UnionType):
        members = list(e.isinstance_type.members)
    else:
        members = [e.isinstance_type]
    var = escape_cpp_name(e.isinstance_var)
    checks = [
        f"::tpy::isinstance_static<{lc.render_type(m)}, "
        f"decltype({var})>()"
        for m in members
    ]
    spelled = (checks[0] if len(checks) == 1
               else "(" + " || ".join(checks) + ")")
    return THIRCall(
        result_type=rtype, callee="isinstance", args=(),
        cpp_template=spelled, loc=getattr(e, "loc", None))


def _lower_literal_arg(a: TpyExpr, target: 'TpyType | None', lc: '_LowerCtx',
                       declared: dict[str, TpyType], reject: str, *,
                       array_retype: bool = True) -> THIRExpr:
    """Lower a container-literal call arg against its resolved target slot.
    The make_vector / make_ordered_* element path (move-source / nocopy
    elements) rejects: its in-place arg render is unwitnessed. Each caller
    names its position in `reject`."""
    lowered = _lower_expr(a, lc, declared,
                          use=replace(_NESTED_ARG_USE, slot_target=target),
                          array_retype=array_retype)
    if getattr(lowered, "make_container", False):
        raise ThirUnsupported(reject)
    return lowered


def _iterator_protocol_result(e: TpyExpr, analyzer) -> bool:
    """The call's result is the structural Iterator protocol -- a combinator
    rvalue (`map(f, xs)` / `zip(..)` / `enumerate(..)` / `reversed(..)`)."""
    rt = analyzer.get_expr_type(e)
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return (isinstance(rt, NominalType) and rt.is_protocol
            and rt.name == "Iterator")


def _lower_free_call_arg(e: TpyCall | TpyMethodCall, a: TpyExpr,
                         ptype: 'TpyType | None', kind: 'tuple[str, str] | None',
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         temp_args: bool,
                         readonly_target: bool,
                         arg_index: int) -> THIRExpr:
    analyzer = lc.analyzer
    # Frame-capturing callee (generator/coro factory): its frame borrows ref
    # args past the statement, so readonly-slot rvalues hoist like mutable
    # ones; the fact comes off the callee's func_info, through the one
    # predicate that also sees an @overload-ed generator (whose per-signature
    # fi carries is_generator=False).
    _callee_fi = e.resolved_function_info
    frame_capturing = (_callee_fi is not None
                       and (frame_factory_callee(_callee_fi)
                            or _callee_fi.is_async))
    if isinstance(a, TpyVarargPack):
        # Kind-blind: the pack renders (`std::array` temp +
        # `::tpy::varargs<T>(__tmp_N)`) the same way for a @native callee,
        # and the temp hoists at the same enclosing flush point.
        return _lower_vararg_pack(a, ptype, lc, declared, temp_args=temp_args)
    len_call = _is_len_call(e, declared, analyzer, lc.pointers)
    if not len_call:
        if kind is not None and kind[0] in ("native", "native_c", "template"):
            if _template_arg_unreferenced(kind, arg_index):
                # A @cpp_template that never spells `{i}` DISCARDS this
                # arg's render (`filter(None, xs)` ->
                # `builtin_filter_truthy<T>({1})`), so no shape check can
                # matter -- the expansion cannot contain it.
                _witness("call.template_arg_dropped")
                return THIRLiteral(result_type=analyzer.get_expr_type(a),
                                   value=None, form=Form.VALUE,
                                   loc=getattr(a, "loc", None))
            ok = _native_call_arg_ok(
                a, ptype, declared, analyzer,
                storage_tuple_locals=lc.storage_tuple_locals)
        else:
            ok = _plain_call_arg_ok(
                a, ptype, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed),
                param_names=lc.prescan.param_names,
                self_capturable=_self_capture_cpp(lc) is not None,
                movable_locals=lc.movable_locals,
                func_name=getattr(lc.func, "name", None))
            if not ok and _borrow_tuple_name_arg(
                    a, ptype, declared, _borrow_tuple_bare_names(lc, declared),
                    analyzer):
                ok = True  # witnessed inside the predicate (arg.btuple_name)
            if not ok and _borrow_tuple_storage_name_arg(
                    a, ptype, declared, lc.storage_tuple_locals,
                    analyzer) is not None:
                ok = True  # witnessed at the lift arm (arg.btuple_storage_name)
            if not ok and _union_elem_tuple_name_arg(
                    a, ptype, declared, lc.prescan.param_names, analyzer):
                ok = True  # witnessed inside (call.union_elem_tuple_arg)
            if not ok and temp_args and isinstance(a, TpyListRepeat):
                # Admission for the list-repeat ref-param hoist; rendered
                # (and witnessed) at the argtemp.list_repeat arm below.
                _lr_slot = unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ptype)))
                ok = isinstance(_lr_slot, NominalType) and (
                    is_list(_lr_slot) or is_array(_lr_slot))
            if (not ok and temp_args and frame_capturing
                    and isinstance(a, TpyArrayLiteral)):
                # The frame-capturing readonly-slot LIST literal: hoists
                # like a mutable one (the plain ladder's row declines
                # readonly without the frame fact). ArrayLiteral ONLY --
                # the dict/set render arm is not frame-aware, so admitting
                # those here would fall through to the inline bind the
                # frame borrows past, which dangles.
                # witnessed at the ArgTemp arm (argtemp.container_literal)
                ok = _container_literal_arg(a, ptype, analyzer,
                                            frame_capturing=True)
            if not ok and _value_opt_member_arg(a, ptype, declared, analyzer):
                ok = _witness("call.optval_member")
            if not ok and _value_opt_call_ret_arg(a, ptype, analyzer):
                # A value-opt-returning call rvalue at the same value-opt
                # slot binds bare (`unwrap_or(first_positive(xs), 0)`).
                ok = _witness("call.optval_ret_pass")
            if not ok and _own_union_storage_name_arg(
                    a, ptype, declared, analyzer,
                    readonly_target=readonly_target) is not None:
                # witnessed at the arm (arg.own_union_storage_name)
                ok = True
            if not ok and _optional_ptr_container_arg(
                    a, ptype, declared, analyzer):
                ok = True  # witnessed at the lowering rows (optptr.none/name)
            if not ok and temp_args and _union_ctor_temp_arg(
                    a, ptype, analyzer):
                ok = True  # witnessed at the lowering arm (unionlift.ctor_temp)
            if not ok and temp_args and _union_bytes_literal_temp_arg(
                    a, ptype, analyzer) is not None:
                # witnessed at the arm (unionlift.bytes_literal_temp)
                ok = True
            if not ok and temp_args and _union_dict_literal_temp_arg(
                    a, ptype, analyzer) is not None:
                # witnessed at the arm (unionlift.dict_literal_temp)
                ok = True
            if not ok and temp_args and _container_call_temp_arg(
                    a, ptype, analyzer,
                    frame_capturing=frame_capturing) is not None:
                ok = True  # witnessed at the ArgTemp arm (argtemp.container_call)
            if not ok and isinstance(a, TpyName) and a.name != "self":
                # A NAME bound to the still-open slot type -- the callable
                # -param invocation inside a generic body (`f(init)` at a
                # synthetic `U` slot): binds bare, the free-call sibling of
                # the generic gate's composite open-slot rule.
                if _open_slot_match(declared.get(a.name), ptype):
                    ok = _witness("call.generic_open_slot_name")
            if not ok and isinstance(a, TpySubscript):
                # A container-element subscript at the still-open slot
                # (`f(xs[i])` at `T` inside a generic body): the element
                # read renders itself and binds the ref slot bare -- the
                # subscript arm gates the read (bounds, receiver family).
                if _open_slot_match(analyzer.get_expr_type(a), ptype):
                    ok = _witness("call.generic_open_slot_elem")
            if not ok and _mixed_own_tuple_name_arg(
                    a, ptype, declared, lc.own_borrow_tuple_locals,
                    analyzer) is not None:
                ok = True  # witnessed at the arm (arg.btuple_mixed_name)
            if not ok and _btuple_pass_arg(a, ptype, analyzer):
                # A borrow-tuple-returning call at a MATCHING borrow-form
                # tuple param (ptr-Optional or mixed own/borrow elements) --
                # the SAME predicate the render arm keys on, so gate and
                # render cannot drift.
                ok = True  # witnessed at the arm (call.btuple_pass)
            if not ok and _own_movable_tuple_pass_arg(a, ptype, analyzer):
                # An owned-movable tuple call rvalue binds the `&&` slot
                # bare -- same-predicate render arm below.
                ok = True  # witnessed at the arm (call.own_tuple_pass)
        if not ok and _own_move_source_slice(a, ptype, lc, declared):
            # The temp-free MOVE half of the Own-slot cascade, kind-blind
            # and position-independent (`std::move(<name read>)` -- native
            # `unsafe_store(p, 0, pt)`, plain flushless `take(p)` at a
            # resumable return alike); the COPY half stays temps_ok-gated
            # inside the kind branches (it hoists a temp).
            ok = True  # witnessed at the arm (move.own_last_use)
        if not ok and _copy_own_arg(a, ptype, analyzer):
            # `copy(name)` into a same-nominal Own slot: the copy-construct
            # rvalue (`consume(Box(b))`), rendered by _lower_call_arg's
            # copy intercept -- no temp, no move.
            ok = True  # witnessed at the row (own.copy_construct)
        if not ok and _own_tuple_move_arg(a, ptype, lc, declared):
            # An OWN-element tuple name moves whole at its last use
            # (`consume(std::move(t))`), rendered by _lower_call_arg's
            # tuple-move arm.
            ok = True  # witnessed at the arm (move.own_tuple)
        if not ok and _own_tuple_borrow_lift_arg(a, ptype, lc, declared):
            # A BORROW-form Own-element tuple name lifts via
            # tuple_to_storage, rendered by _lower_call_arg's arm.
            ok = True  # witnessed at the arm (arg.own_tuple_borrow_lift)
        if not ok and _own_tuple_decay_copy_arg(a, ptype, lc, declared):
            # A still-live STORAGE-form Own-tuple name decay-copies
            # (`sink(auto(p))`), rendered by _lower_call_arg's arm.
            ok = True  # witnessed at the arm (arg.own_tuple_decay_copy)
        if (not ok and isinstance(a, TpyNamedExpr)
                and a.target in lc.frame_local_types):
            # A walrus arg whose target is a resumable FRAME FIELD writes
            # the field in place, position-blind (`value_of((m = pick(
            # nodes, i)))` / `::tpy::__len__(xs.emplace(...))`); the
            # frame-walrus dispatch validates the leg and rejects the
            # un-landed ones itself. Kind-blind: the native and plain arg
            # loops render the walrus the same way.
            ok = True  # witnessed at the frame-walrus legs (expr.walrus_frame_*)
        if not ok:
            raise ThirUnsupported(call_reject_reason("expr.call"))
    pin_slot = (_strlit_overload_pin_arg(
        e, _callee_fi, a, ptype, analyzer)
        if isinstance(e, TpyCall) and isinstance(e.func, TpyName)
        else None)
    if pin_slot is not None:
        # A bare str literal is `const char[N]`, whose array-to-pointer /
        # boolean conversions outrank the user-defined string_view one, so an
        # overloaded callee would bind a competing overload: pin the literal
        # to its slot's view form (the `overloaded_call` branch).
        _witness("call.strlit_overload_pin")
        pinned = _lower_expr(a, lc, declared)
        return THIRCoerce(
            result_type=ptype, expr=pinned,
            coercion_name="strlit_overload_pin",
            wrap=f"{pin_slot.to_cpp_param_type()}({{0}})",
            form=pinned.form, loc=getattr(a, "loc", None))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and isinstance(a, (TpyCall, TpyMethodCall))
            and _native_container_call_arg(a, ptype, analyzer)):
        # A container-returning call rvalue bound bare by the native slot
        # (`::tpy::__len__(g.get())` / `::tpy::sorted(ml.get())`): STORAGE
        # use so the inner call's result gate admits the container -- the
        # print wrap-call path's twin. `allow_temps` rides through: every
        # nested arg temp flushes at the enclosing statement, so a
        # flushable position's right extends into the inner call's args.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=temp_args))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and isinstance(a, TpyFieldAccess)
            and a.property_getter_call is not None
            and _storage_call_ret(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    lc.analyzer.get_expr_type(a)))), lc.analyzer) is not None):
        # A container-returning PROPERTY read bound bare by the native slot
        # (`len(f.items)` -> `::tpy::__len__(f.items())`): BORROW_BIND use,
        # so the getter's borrow-container result rides the same admission
        # as the alias-decl sink -- the borrow is consumed inside the call
        # expression, no alias escapes.
        _witness("arg.native_property_container")
        return _lower_expr(
            a, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and isinstance(a, TpyFieldAccess)
            and (_mv_arg := _bare_module_recv(a.obj, declared, lc.analyzer))
            is not None
            and _module_var_read_cpp(_mv_arg, a.field, lc.analyzer)
            is not None):
        # A module-variable read at a native slot (`len(os.environ)` ->
        # `::tpy::__len__((*environ))`): the consumer is pinned, so the
        # pointer-slot deref read is admitted (RECEIVER use, like the
        # method-receiver and print-sink consumers).
        _witness("arg.native_module_var")
        return _lower_expr(
            a, lc, declared, use=_ExprUse(result=_ExprResultUse.RECEIVER))
    plain_kind = kind is None or kind[0] not in ("native", "native_c",
                                                 "template")
    if not plain_kind:
        comp_c = _native_iterable_comp_arg(a, ptype, lc.analyzer)
        if comp_c is not None:
            # The native/template twin of the ArgTemp row below: the template
            # slot takes the stmt-expr INLINE, target-typed by the
            # comprehension's own container rather than the protocol slot.
            from .comprehensions import _lower_comprehension
            pointers = _comp_shadow_pointers(lc.pointers, declared,
                                             lc.analyzer)
            _witness("arg.native_comprehension")
            return _lower_comprehension(a, comp_c, lc, declared, pointers)
    if (plain_kind and _wrapper_ref_tuple_elem_arg(a, ptype, declared,
                                                   lc.analyzer)):
        # The wrapper element off a REFERENCE-element tuple binding: the
        # bare `std::get<N>(p)` lvalue binds the const wrapper slot.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    bt_sub = (_borrow_tuple_subscript_arg(a, ptype, analyzer)
              if plain_kind else None)
    if bt_sub is not None:
        # The container-element twin of the field wrap below: the checked
        # element read is a storage lvalue, so it takes the same
        # storage->borrow `tuple_to_pointer`. No const-ROOTED source test --
        # a container element's const-ness comes from the slot alone.
        _witness("arg.borrow_tuple_subscript")
        return THIRFormConvert(
            result_type=bt_sub,
            value=_lower_expr(a, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            form=Form.BORROW,
            is_const=(readonly_target or isinstance(ptype, ReadonlyType)),
            loc=getattr(a, "loc", None))
    # Kind-blind: a native/template callee's tuple slot is the same borrow
    # form (`::tpy::tuple_to_str(tuple_to_pointer<..>(t))`), and the lift is
    # position-independent -- it wraps the read, it does not hoist.
    if plain_kind and _own_union_call_pass_arg(a, ptype, analyzer):
        # A same-union Own[A|B]-returning call rvalue moves through the
        # Own[union] `&&` slot bare; witnessed at the result-set arm
        # (own.union_call_pass).
        return _lower_expr(
            a, lc, declared,
            use=replace(_NESTED_ARG_USE, result=_ExprResultUse.OWN_SLOT,
                        allow_temps=temp_args))
    ou_slot = (_own_union_storage_name_arg(a, ptype, declared, analyzer,
                                           readonly_target=readonly_target)
               if plain_kind else None)
    if ou_slot is not None:
        # An Own[union] storage-variant NAME at a ptr-variant slot: the
        # to_ptr_variant lift over the bare name read, or its const-pointee
        # sibling when the slot is deep-const.
        _witness("arg.own_union_storage_name")
        return THIRFormConvert(
            result_type=ou_slot,
            value=_lower_expr(a, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            is_const=readonly_target,
            form=Form.BORROW,
            loc=getattr(a, "loc", None))
    bt_mixed = _mixed_own_tuple_name_arg(
        a, ptype, declared, lc.own_borrow_tuple_locals, analyzer)
    if bt_mixed is not None:
        # The MIXED-own-tuple LOCAL at a borrow-tuple param slot: the
        # whole-tuple lift is still owed (`take(::tpy::tuple_to_pointer<
        # std::tuple<const Box*, const Box*>>(p))`). No const-rooted-source
        # fold here, unlike the storage-name row below: own_borrow_tuple_
        # locals only ever holds fresh straight-line locals bound from a
        # mixed-own call/ternary, while const_storage_tuple_locals members
        # come from storage-alias decls and const-iterated loop vars --
        # disjoint decl shapes for any one name (and the `q = p` name copy
        # never joins the mixed set), so the fold term is always False.
        _witness("arg.btuple_mixed_name")
        return THIRFormConvert(
            result_type=bt_mixed,
            value=_lower_expr(a, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            form=Form.BORROW,
            is_const=(readonly_target or isinstance(ptype, ReadonlyType)),
            loc=getattr(a, "loc", None))
    bt_name = _borrow_tuple_storage_name_arg(
        a, ptype, declared, lc.storage_tuple_locals, analyzer)
    if bt_name is not None:
        # The NAME twin of the two lift rows above: a storage-form tuple local
        # takes the same `tuple_to_pointer`. Const-ness is the want_const
        # pair -- the callee's deep-const verdict OR a name bound from a
        # const-iterated source (`const_storage_tuple_locals`).
        _witness("arg.btuple_storage_name")
        return THIRFormConvert(
            result_type=bt_name,
            value=_lower_expr(a, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            form=Form.BORROW,
            is_const=(readonly_target or isinstance(ptype, ReadonlyType)
                      or a.name in lc.const_storage_tuple_locals),
            loc=getattr(a, "loc", None))
    if plain_kind and _btuple_pass_arg(a, ptype, analyzer):
        # A borrow-tuple-returning CALL at a MATCHING borrow-form tuple
        # param (`show(f(t1, t2))` at `tuple[T | None, int32, T | None]`):
        # the `std::tuple<const T*, ..>&` slot binds the call result bare.
        _witness("call.btuple_pass")
        return _lower_expr(
            a, lc, declared,
            use=replace(_NESTED_ARG_USE, pos=SinkPos.CALL_ARG,
                        forms=_call_arg_forms(whole_tuple=True,
                                              coro_factory=False,
                                              ptr_opt_pass=False),
                        result=_ExprResultUse.STORAGE,
                        allow_temps=temp_args))
    if plain_kind and _own_movable_tuple_pass_arg(a, ptype, analyzer):
        # The owned-movable sibling: `take_owned(make_owned())` -- the
        # prvalue binds the `std::tuple<Box, Box>&&` slot bare.
        _witness("call.own_tuple_pass")
        return _lower_expr(
            a, lc, declared,
            use=replace(_NESTED_ARG_USE, pos=SinkPos.CALL_ARG,
                        forms=_call_arg_forms(whole_tuple=True,
                                              coro_factory=False,
                                              ptr_opt_pass=False),
                        result=_ExprResultUse.STORAGE,
                        allow_temps=temp_args))
    # Kind-blind like the NAME row: a native/template callee's F3-tuple slot
    # takes the same storage->borrow lift over the bare member read.
    bt_slot = _borrow_tuple_field_arg(a, ptype, analyzer)
    if bt_slot is not None:
        # The storage->borrow tuple wrap over the bare member read
        # (`bump(::tpy::tuple_to_pointer<std::tuple<int32_t, Box*>>(
        # h.pair))`); const-ness is the want_const pair -- the
        # callee param's deep-const verdict (threaded readonly_target /
        # readonly spelling) OR a const-ROOTED source field
        # (is_const_storage_source: `h.pair` off a const receiver).
        _witness("arg.borrow_tuple_field")
        field = _lower_expr(a, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
        return THIRFormConvert(
            result_type=bt_slot, value=field, form=Form.BORROW,
            is_const=(readonly_target or isinstance(ptype, ReadonlyType)
                      or _f1_const_rooted_source(a, lc)),
            loc=getattr(a, "loc", None))
    _ru_borrow_call = (_recursive_union_borrow_call_arg(a, ptype, analyzer)
                       if plain_kind else False)
    _elem_sub = (_record_elem_subscript_arg(a, ptype, analyzer)
                 if plain_kind else False)
    if (plain_kind
            and (_ru_borrow_call
                 or _record_borrow_call_arg(a, ptype, analyzer)
                 or _elem_sub)):
        # The T&-returning call / checked record-element read binds the
        # record ref slot inline (`bump(find_first(pts))` /
        # `add_a(::tpy::__getitem__(a.bs, 0), ..)`); BORROW_BIND admits
        # the borrow-record result like the compare-operand twin. A
        # recursive-union WRAPPER slot binds the same way -- same render,
        # separate face so the boundary stays measurable; the element read
        # carries the face every other family witnesses it under.
        _witness("arg.recursive_union_borrow_call" if _ru_borrow_call
                 else "arg.record_elem_subscript" if _elem_sub
                 else "arg.record_borrow_call")
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and _native_protocol_field_arg(a, ptype, analyzer)):
        # The bare member read at the protocol slot: an optional/container
        # field passes WHOLE (the field arm's whole-member row); an
        # F1-record field takes the BORROW_BIND bare-member admission (the
        # compare-operand render).
        _witness("arg.native_protocol_field")
        at = analyzer.get_expr_type(a)
        tb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if (isinstance(tb, OptionalType) or is_list(tb) or is_set(tb)
                or is_dict(tb)):
            return _lower_expr(a, lc, declared, allow_whole_optional=True)
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and _native_iterable_range_arg(a, ptype)):
        # The instantiation ladder's range row on the native ladder
        # (`zip(range(3), names)` -> `::tpy::Range<int32_t>(3)` bound bare);
        # same shape checks as that arm, rejecting outside them.
        range_fi = a.resolved_function_info
        if (len(a.args) not in (1, 2, 3) or range_fi is None
                or not range_fi.cpp_template
                or not _eligible_scalar(_range_counter_type(a, analyzer))):
            note_detail("call.native_range_shape")
            raise ThirUnsupported(call_reject_reason("expr.call"))
        _witness("call.native_range_arg")
        return _lower_range_object(a, lc, declared)
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and _native_iterable_iterator_call_arg(a, ptype, analyzer)):
        # A nested combinator / generator-factory rvalue binds bare in
        # iterable position (the instantiation ladder's gen/iterator rows).
        # `allow_temps` rides the enclosing statement's flush in (a
        # factory's temporary ref args hoist their frame-borrowed
        # scope-locals), like the qualified-factory instantiation arm.
        _witness("call.native_iter_call_arg")
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                        allow_temps=temp_args))
    if (_is_len_native(e) and isinstance(a, TpyFieldAccess)):
        return _lower_expr(a, lc, declared, field_prechecked=True)
    if (_is_len_native(e) and isinstance(a, TpyMethodCall)
            and _dict_view_iterable_ok(
                a, declared, analyzer, methods=("values", "keys", "items"))):
        # `len(d.values())`: the dict-view rvalue takes the same bare native
        # call (`::tpy::dict_values(d)`) the for-loop's ITERABLE override
        # renders -- the runtime __len__ overloads accept it directly.
        return _lower_expr(
            a, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE))
    if (not frame_capturing
            and _readonly_container_rvalue_arg(a, ptype, lc.analyzer)
            is not None):
        # A DECLARED-readonly slot binds an empty container rvalue INLINE
        # (never the ref-param temp the plain-slot arms below hoist);
        # handled in _lower_call_arg's inline arm. A frame-capturing callee
        # hoists instead (the frame borrows past the statement) -- the
        # literal ArgTemp arm below takes it.
        return _lower_call_arg(
            a, ptype, lc, declared, temp_args=temp_args,
            protocol_slots=False, readonly_target=readonly_target,
            frame_capturing=frame_capturing)
    if (isinstance(a, TpyArrayLiteral) and temp_args
            and (kind is None or kind[0] not in ("native", "native_c", "template"))
            and _container_literal_arg(a, ptype, analyzer,
                                       frame_capturing=frame_capturing)):
        # A list literal into a plain free call's concrete container ref
        # param hoists a `__tmp_N` ref-param temp
        # (`std::vector<T> __tmp_N = {..}; f(__tmp_N)`) rather than the
        # ctor's bare in-place brace, so route the hoisted temp here before
        # `_lower_call_arg`'s in-place container-literal arm can fire.
        slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        lowered = _lower_literal_arg(
            a, slot, lc, declared,
            "container-literal free arg on the make_vector path")
        _witness("argtemp.container_literal")
        return THIRArgTemp(result_type=slot, cpp_type=slot.to_cpp(),
                           init=lowered, form=Form.BORROW,
                           movable=unwrap_ref_type(slot).is_movable(),
                           loc=getattr(a, "loc", None))
    if (isinstance(a, (TpyDictLiteral, TpySetLiteral, TpyArrayLiteral))
            and temp_args
            and (kind is None or kind[0] not in ("native", "native_c", "template"))
            and _ref_param_dictset_literal_arg(a, ptype, lc.analyzer)):
        # The dict / set / Array sibling of the list arm above: the spelled
        # container render hoisted into the ref-param `__tmp_N` temp
        # (`::tpy::ordered_map<...> __tmp_N = ::tpy::ordered_map<...>({{..}});`,
        # bare-brace for the `Array[T, N]` slot).
        slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        lowered = _lower_literal_arg(
            a, slot, lc, declared,
            "container-literal free arg on the make_ordered path")
        _witness("argtemp.container_literal")
        return THIRArgTemp(result_type=slot, cpp_type=slot.to_cpp(),
                           init=lowered, form=Form.BORROW,
                           movable=unwrap_ref_type(slot).is_movable(),
                           loc=getattr(a, "loc", None))
    if (isinstance(a, TpyListRepeat) and temp_args
            and (kind is None
                 or kind[0] not in ("native", "native_c", "template"))):
        # The list-REPEAT sibling of the literal arms above (`take([0] * n)`
        # -> `std::vector<T> __tmp_N = ::tpy::from_range(...); take(__tmp_N)`):
        # the is_temporary ref-param hoist over the repeat render. The
        # repeat arm itself gates the element/result families and rejects
        # the lazy ListRepeatType shape.
        slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        if isinstance(slot, NominalType) and (is_list(slot)
                                              or is_array(slot)):
            lowered = _lower_expr(a, lc, declared,
                                  use=_ExprUse(slot_target=slot))
            if isinstance(lowered, THIRListRepeat):
                _witness("argtemp.list_repeat")
                return THIRArgTemp(result_type=slot, cpp_type=slot.to_cpp(),
                                   init=lowered, form=Form.BORROW,
                                   movable=unwrap_ref_type(slot).is_movable(),
                                   loc=getattr(a, "loc", None))
    if (isinstance(a, (TpyCall, TpyMethodCall)) and temp_args
            and (kind is None or kind[0] not in ("native", "native_c", "template"))):
        # The call-rvalue sibling of the literal ArgTemp arm above:
        # `std::vector<T> __tmp_N = <call>; f(__tmp_N)`.
        slot = _container_call_temp_arg(a, ptype, lc.analyzer,
                                        frame_capturing=frame_capturing)
        if slot is not None:
            _witness("argtemp.container_call")
            lowered = _lower_expr(
                a, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE))
            return THIRArgTemp(result_type=slot, cpp_type=slot.to_cpp(), movable=unwrap_ref_type(slot).is_movable(),
                               init=lowered, form=Form.BORROW,
                               loc=getattr(a, "loc", None))
    dc = _deref_coerce_arg(a, ptype, declared, lc.analyzer)
    if dc is not None:
        # The deref auto-coercion name: a Ptr source renders the inline
        # `::tpy::deref_check(p)` lvalue; a record-wrapper source hoists the
        # slot-typed VALUE copy temp and passes the temp name.
        dkind, dslot = dc
        inner = _lower_expr(a.expr, lc, declared)
        wrap = ("::tpy::deref_check({0})" if dkind == "inline"
                else "{0}.__deref__()")
        coerced = THIRCoerce(result_type=dslot, expr=inner,
                             coercion_name=a.coercion.name, wrap=wrap,
                             form=inner.form, loc=getattr(a, "loc", None))
        if dkind == "inline":
            _witness("arg.deref_coerce_inline")
            return coerced
        if not temp_args:
            raise ThirUnsupported(
                "deref-coerce arg-temp outside a flush position")
        _witness("argtemp.deref_coerce")
        return THIRArgTemp(result_type=dslot, cpp_type=dslot.to_cpp(), movable=unwrap_ref_type(dslot).is_movable(),
                           init=coerced, form=Form.BORROW,
                           loc=getattr(a, "loc", None))
    # The frame-capturing TEMPORARY argument: the free-call twin of the
    # factory-METHOD row.
    return _frame_temp_arg(
        a, ptype, lc, analyzer, active=frame_capturing, temp_args=temp_args,
        no_flush_reason="frame temp arg outside a flush position",
        lower=lambda: _lower_call_arg(
            a, ptype, lc, declared, temp_args=temp_args,
            protocol_slots=kind is not None and kind[0] not in ("native", "native_c", "template"),
            readonly_target=readonly_target, frame_capturing=frame_capturing,
            inline_template=kind is not None
            and kind[0] in ("native", "native_c", "template")),
        lower_owned=lambda owned: _lower_call_arg(
            _peel_coerce(a), owned, lc, declared, temp_args=True))


def _frame_temp_arg(a: TpyExpr, ptype: 'TpyType | None',
                    lc: '_LowerCtx', analyzer, *, active: bool,
                    temp_args: bool, no_flush_reason: str,
                    lower: 'Callable[[], THIRExpr]',
                    lower_owned: 'Callable[[TpyType], THIRExpr]') -> THIRExpr:
    """The frame-capturing TEMPORARY-argument row, one body for both call seams
    (the generator/coro factory METHOD and the free call).

    A frame never receives a temporary: whatever it does with an argument, it
    does after the full expression that built the argument has ended, so the
    argument must already be a named local. The argument keeps its normal
    lowering -- whatever `lower()` builds -- and the temporary it materializes
    becomes a hoisted `__tmp_N`, so a borrowing sink views the local, an owning
    sink moves off it and a value sink copies it.

    `no_flush_reason` is the seam's own reject text: the row NEEDS a hoist
    position, and with none the honest answer is a reject rather than the
    inline prvalue whose dangle this row exists to remove."""
    slot = frame_temp_arg_slot(a, ptype, analyzer) if active else None
    if slot is None:
        return lower()
    # Inside a resumable body this row's flush point is the state's `case`
    # block, which a frame handle outlives -- so the hoist there is the
    # gen_async one (`_hoist_frame_factory_args`), which seats the argument on
    # a field of the ENCLOSING frame and has replaced it with a name before
    # this row sees it. What still reaches here in a resumable body is a
    # position that pass does not admit (a ternary arm, a comprehension body):
    # there the handle cannot outlive the full expression either, so the
    # case-block local is enough -- except for a `str`/`bytes` LITERAL, which
    # already renders over STATIC storage and would only trade a lifetime that
    # cannot end for one that ends early.
    if (isinstance(_peel_coerce(a), (TpyStrLiteral, TpyBytesLiteral))
            and (lc.func.is_generator or lc.func.is_async)):
        return lower()
    if not temp_args:
        raise ThirUnsupported(no_flush_reason)
    return _frame_temp_argtemp(lower(), lower_owned, slot, lc,
                               getattr(a, "loc", None))


def _already_hoisted(e: THIRExpr) -> bool:
    """Whether this lowered argument already names a hoisted local -- an earlier
    row (the Own copy+move cascade, the protocol adapter, the pointer-variant
    union lift, the `*args` pack, the factory ref-slot rows) got there first
    and the frame rule is already satisfied. Re-seating one of those on a
    second temp would nest an arg-temp inside a lift, which the validator
    rejects; the rows are enumerated because each spells its own hoist rather
    than sharing a node."""
    while isinstance(e, THIRCoerce):
        e = e.expr
    if isinstance(e, THIRUnionArgLift):
        return e.temp_cpp is not None or _already_hoisted_opt(e.value)
    if isinstance(e, THIROptionalPtrArg):
        return _already_hoisted_opt(e.value)
    return isinstance(e, (THIRArgTemp, THIRVarargPack))


def _already_hoisted_opt(e: 'THIRExpr | None') -> bool:
    return e is not None and _already_hoisted(e)


def _frame_temp_argtemp(lowered: THIRExpr,
                        lower_owned: 'Callable[[TpyType], THIRExpr]',
                        slot: 'TpyType', lc: '_LowerCtx', loc) -> THIRExpr:
    """Seat a frame-capturing temporary argument on a hoisted `__tmp_N`.

    A coerce-shaped argument keeps its coerce and only its SOURCE moves onto
    the local (`std::array<int32_t, 3> __tmp_1 = {1, 2, 3};
    gen(::tpy::as_mut_span(__tmp_1))`) -- that IS "the same per-param coercion,
    applied to the local". An argument with no coerce becomes the local itself,
    and its init is re-lowered at the OWNED slot type: the argument position's
    own render can be a view over static storage (`::tpy::bytes_literal(..)`)
    where the local must own (`::tpy::Bytes __tmp_1 =
    ::tpy::bytes_literal_owned(..)`)."""
    if isinstance(slot, PendingListType):
        raise ThirUnsupported(
            "generator/coroutine argument's backing type is unresolved")
    if _already_hoisted(lowered):
        return lowered
    _witness("argtemp.frame_temp")
    if isinstance(lowered, THIRCoerce):
        temp = THIRArgTemp(result_type=slot, cpp_type=lc.render_type(slot),
                           init=lowered.expr, form=Form.BORROW,
                           movable=unwrap_ref_type(slot).is_movable(), loc=loc)
        # A form-PASSTHROUGH coerce (one whose form IS its inner's, the
        # bytes-view family) must keep passing the new inner's form through:
        # the hoisted local is a borrow source where the prvalue it replaces
        # was self-contained storage. A form-PRODUCING coerce (`as_mut_span`'s
        # VALUE span, the str-view BORROW) keeps the form it produces, which
        # the source cannot change.
        form = temp.form if lowered.form is lowered.expr.form else lowered.form
        return replace(lowered, expr=temp, form=form)
    init = lower_owned(slot)
    if _already_hoisted(init):
        # The re-lowering is a PLAIN argument position, where a row that
        # spells its own hoist at this slot can fire even though the seam's
        # own lowering did not (a comprehension at a factory METHOD's
        # container slot renders inline there, and hoists here). That hoist
        # IS this row's, at this row's slot -- take it rather than nest a
        # second temp inside it, which the validator rejects.
        return init
    # `lower_owned` lowers at the slot TYPE but still at a call-ARGUMENT
    # position, where a bytes literal renders the static-storage span. The
    # hoisted decl is an owned init, so the literal takes its owning render.
    if isinstance(init, THIRBytesLiteral) and init.form is not Form.STORAGE:
        init = replace(init, form=Form.STORAGE)
    return THIRArgTemp(result_type=slot, cpp_type=lc.render_type(slot),
                       init=init, form=lowered.form,
                       movable=unwrap_ref_type(slot).is_movable(), loc=loc)


def _lower_vararg_pack(pack: TpyVarargPack, ptype: 'TpyType | None',
                       lc: '_LowerCtx', declared: dict[str, TpyType], *,
                       temp_args: bool) -> THIRExpr:
    """Lower a `*args` pack. The element spelling combines
    the pack's base type with the slot's const-ness (a readonly vararg slot
    adds const, a mutable one strips it); the
    ref/value split follows the element's value category. A sole `*expr`
    unpack forwards the container (direct span or borrowed span); the per-arg
    form hoists the std::array temp (flushable position only). Str/bytes
    value elements (which need the owned-copy wrap) and mixed *-unpack are
    rejected."""
    analyzer = lc.analyzer
    elem_type = pack.element_type
    slot_bare = unwrap_ref_type(ptype) if ptype is not None else None
    if (slot_bare is not None and is_varargs(slot_bare)
            and getattr(slot_bare, "type_args", None)):
        slot_elem = slot_bare.type_args[0]
        slot_is_const = isinstance(slot_elem, ReadonlyType)
        pack_is_const = isinstance(elem_type, ReadonlyType)
        if slot_is_const and not pack_is_const:
            elem_type = ReadonlyType(elem_type)
        elif not slot_is_const and pack_is_const:
            elem_type = elem_type.wrapped
    elem_cpp = (f"const {lc.render_type(elem_type.wrapped)}"
                if isinstance(elem_type, ReadonlyType)
                else lc.render_type(elem_type))
    bare_elem = unwrap_readonly(elem_type)
    is_ref = (not bare_elem.is_value_type()
              and not isinstance(bare_elem, TypeParamRef))

    if len(pack.args) == 1 and isinstance(pack.args[0], TpyStarUnpack):
        star = pack.args[0]
        inner_type = analyzer.get_expr_type(star.expr)
        inner = _lower_expr(star.expr, lc, declared)
        if is_spanlike_view(inner_type):
            span_fn = None
            _witness("vararg.star_direct")
        else:
            span_fn = ("as_span" if isinstance(elem_type, ReadonlyType)
                       else "as_mut_span")
            _witness("vararg.star_span")
        return THIRVarargPack(result_type=ptype, elem_cpp=elem_cpp,
                              is_ref=is_ref, star_source=inner, span_fn=span_fn,
                              form=Form.VALUE, loc=getattr(pack, "loc", None))
    if not pack.args:
        _witness("vararg.empty")
        return THIRVarargPack(result_type=ptype, elem_cpp=elem_cpp,
                              is_ref=is_ref, form=Form.VALUE,
                              loc=getattr(pack, "loc", None))
    if not temp_args:
        # The std::array temp flushes before the statement -- only a flushable
        # position admits it.
        raise ThirUnsupported("call.vararg_pack_flush")

    lowered: list[THIRExpr] = []
    ref_lvalue: list[bool] = []
    for a in pack.args:
        if isinstance(a, TpyStarUnpack):
            # Sema forbids mixing *expr with other varargs; defense in depth.
            raise ThirUnsupported("call.vararg_star_mixed")
        if is_ref:
            bare = a
            while isinstance(bare, TpyCoerce):
                bare = bare.expr
            ref_lvalue.append(
                isinstance(bare, (TpyName, TpyFieldAccess, TpySubscript)))
        else:
            at = analyzer.get_expr_type(a)
            atb = (unwrap_readonly(unwrap_ref_type(at))
                   if at is not None else None)
            # Pending view reads (a str loop var) resolve through the
            # usage-resolved family, like every str/bytes verdict.
            if atb is not None and (
                    _resolved_str_value(atb, analyzer) is not None
                    or _resolved_bytes_value(atb, analyzer) is not None
                    or is_string_type(atb)):
                # A str/bytes value element lands in the owned std::array. A
                # VIEW source (a view param/local, a view-resolved read)
                # takes the owned-copy wrap (`std::string()` /
                # `Bytes(x)`); a str/bytes LITERAL is const char[N] /
                # already-owned and an OWNED source lands bare.
                src = a
                while isinstance(src, TpyCoerce):
                    src = src.expr
                if not isinstance(src, (TpyStrLiteral, TpyBytesLiteral)):
                    bare_et = unwrap_readonly(elem_type)
                    if _resolved_str_value(bare_et, analyzer) is not None:
                        # STR family: strview_to_str is identity at ARG, so
                        # the coerce peels and a view-form read (BORROW --
                        # a view source) wraps in the
                        # owned copy; an owned STORAGE read lands bare.
                        lowered_elem = _lower_expr(src, lc, declared)
                        if lowered_elem.form is Form.BORROW:
                            _witness("vararg.view_elem_copy")
                            lowered_elem = THIRFormConvert(
                                result_type=elem_type, value=lowered_elem,
                                form=Form.STORAGE,
                                loc=getattr(a, "loc", None))
                        lowered.append(lowered_elem)
                        ref_lvalue.append(True)
                        continue
                    # BYTES family: the bytesview_to_bytes coerce IS the
                    # copy -- lower UNPEELED and land bare (a wrap would
                    # double-copy; an owned source has no coerce).
                    _witness("vararg.bytes_elem")
                    lowered.append(_lower_expr(a, lc, declared))
                    ref_lvalue.append(True)
                    continue
            ref_lvalue.append(True)
        # A REF pack element is an ADDRESS-OF lvalue (`&::tpy::__getitem__(
        # items, 0)`): a record-element subscript reads its `T&` at the
        # borrow consumer, so thread BORROW_BIND for the lvalue shapes.
        # NB this render is correct for the REACHABLE slice only
        # (F1-record pointees / record subscript elements); a
        # pointer-repr container NAME here is the `&xs` T** shape, which is
        # broken (see BUGS.md) and caught by the toolchain.
        lowered.append(_lower_expr(
            a, lc, declared,
            use=(_ExprUse(result=_ExprResultUse.BORROW_BIND)
                 if is_ref and ref_lvalue[-1] else _ExprUse())))
    _witness("vararg.pack_ref" if is_ref else "vararg.pack_value")
    return THIRVarargPack(result_type=ptype, elem_cpp=elem_cpp, is_ref=is_ref,
                          args=tuple(lowered), ref_lvalue=tuple(ref_lvalue),
                          form=Form.VALUE, loc=getattr(pack, "loc", None))


def _lower_range_object(call, lc: '_LowerCtx',
                        declared: dict[str, TpyType]) -> THIRCall:
    """A `range(...)` call in OBJECT position (a comprehension's begin/end
    iterable, an instantiation arg): the resolved range overload's
    cpp_template (`::tpy::Range<{T}>({0}, {1}, {2})`) with its type param
    substituted via `expand_fi_template`; bounds
    render against the counter slot exactly like the range-loop bounds."""
    fi = call.resolved_function_info
    template = expand_fi_template(
        fi, getattr(call, "inferred_type_args", None))
    counter = _range_counter_type(call, lc.analyzer)
    args = tuple(_slot_literal_retype(
                     _lower_expr(a, lc, declared), counter, lc)
                 for a in call.args)
    return THIRCall(result_type=lc.analyzer.get_expr_type(call),
                    callee=call.func_name, args=args, cpp_template=template,
                    loc=getattr(call, "loc", None))


def _consuming_iter_wrap(a: TpyExpr, ptype: 'TpyType | None',
                         lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> 'THIRCall | None':
    """A movable container NAME at its last use into an `Iterable[Own[T]]`
    slot takes the consuming-__iter__ wrap (`xs.extend(b)` / `dict(pairs)`
    -> `::tpy::own_iter(std::move(b))`). The wrap
    fires only for an `Own[...]`-element slot; a borrowing slot
    (`join(items: Iterable[str])`) binds the name bare even at its last
    use. Returns None when the slice does not apply; a consuming
    __iter__ WITHOUT a native symbol raises (the `std::move(x).__iter__()`
    spelling is not lowered)."""
    it_pb = _protocol_binding(ptype)
    if it_pb is None or it_pb.name != "Iterable":
        return None
    if not any(isinstance(t, OwnType)
               for t in getattr(it_pb, "type_args", ()) or ()):
        return None
    if not isinstance(a, TpyName) or not _is_move_source(a, lc):
        return None
    at = lc.analyzer.get_expr_type(a)
    if isinstance(at, OwnType):
        at = at.wrapped
    rec = lc.analyzer.registry.get_record_for_type(at)
    citer = next(
        (fi2 for fi2 in (rec.get_method_overloads("__iter__")
                         if rec is not None else [])
         if fi2.is_consuming), None)
    if citer is None:
        return None
    if not citer.native_name:
        raise ThirUnsupported(call_reject_reason("expr.call"))
    _witness("call.own_iter_arg")
    return THIRCall(
        result_type=lc.analyzer.get_expr_type(a) or VoidType(),
        callee="__iter__",
        cpp_template=(
            f"{qualify_native_name(citer.native_name)}"
            "(std::move({0}))"),
        args=(_lower_expr(a, lc, declared),),
        loc=getattr(a, "loc", None))


def _lower_dyn_own_conformer(a: TpyExpr, ptype: 'TpyType | None',
                             conf: 'tuple[NominalType, str]',
                             lc: '_LowerCtx', declared: dict[str, TpyType],
                             *, temp_args: bool) -> THIRExpr:
    """The concrete-conformer faces of an `Own[@dynamic P]` slot,
    verdict-keyed via the shared classifier: an inheritance conformer
    takes `std::make_unique<U>(x)` (unique_ptr<U> converts to
    unique_ptr<P>), a structural one the owning
    `::tpy::make_adapter<Base>(x)` Adapter wrap. A movable NAME source
    moves in (`_is_move_source`); a ctor rvalue lands bare. Shared by the
    `Own[P]` call-arg row and the `Own[P]` return arm."""
    conf_proto, verdict = conf
    inner = _lower_expr(a, lc, declared,
                        use=replace(_NESTED_ARG_USE,
                                    result=_ExprResultUse.BORROW_BIND,
                                    allow_temps=temp_args,
                                    pos=SinkPos.CALL_ARG),
                        allow_unrouted_name=True)
    if isinstance(a, TpyName) and _is_move_source(a, lc):
        inner = THIRMove(result_type=inner.result_type, value=inner,
                         form=inner.form,
                         loc=getattr(a, "loc", None))
    at = lc.analyzer.get_expr_type(a)
    at_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
    if isinstance(at_u, OwnType):
        at_u = unwrap_readonly(at_u.wrapped)
    if verdict == "inherit":
        _witness("dynown.make_unique")
        wrap = f"std::make_unique<{lc.render_type(at_u)}>({{0}})"
    else:
        _witness("dynown.adapter_conformer")
        base = dynamic_base_name(conf_proto, lc.analyzer)
        wrap = f"::tpy::make_adapter<{base}>({{0}})"
    return THIRCoerce(
        result_type=unwrap_send_sync(ptype), expr=inner,
        coercion_name="dyn_own_adapter",
        wrap=wrap, form=inner.form, loc=getattr(a, "loc", None))


def _own_opt_container_ptr_arg(a: TpyExpr, ptype: 'TpyType | None',
                               lc: '_LowerCtx',
                               declared: dict[str, TpyType]
                               ) -> 'TpyType | None':
    """A ptr-repr Optional[CONTAINER] LOCAL (`T*` + slot binding) at an
    `Own[Optional[container]]` ctor slot (the argparse builder's result
    construction): the storage optional materializes inline,
    MOVING the slot pointee --
    `std::move(p ? std::optional<V>(std::move(*p)) : std::nullopt)`.
    Returns the inner container type or None."""
    return _own_opt_container_ptr_arg_facts(
        a, ptype, declared, lc.pointers, lc.analyzer, lc.movable_locals,
        getattr(lc.func, "name", None))


def _own_opt_container_ptr_arg_facts(
        a: TpyExpr, ptype: 'TpyType | None', declared: dict[str, TpyType],
        pointers: 'set[str] | frozenset[str]', analyzer,
        movable_locals: 'set[str] | frozenset[str]',
        func_name: 'str | None') -> 'TpyType | None':
    """`_own_opt_container_ptr_arg` over the discrete facts -- see
    `_is_move_source_facts` for why the split exists."""
    pt = (unwrap_readonly(unwrap_send_sync(ptype))
          if isinstance(ptype, TpyType) else None)
    if not isinstance(pt, OwnType):
        return None
    ot = unwrap_readonly(pt.wrapped)
    if not (isinstance(ot, OptionalType) and ot.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(ot.inner)
    if not _f1_container_ref(inner):
        return None
    if not (isinstance(a, TpyName) and a.name in pointers):
        return None
    dt = declared.get(a.name)
    dtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
           if dt is not None else None)
    if not (isinstance(dtu, OptionalType)
            and unwrap_readonly(dtu.inner) == inner):
        return None
    # LAST-USE only: the inline form is the move render; a still-live
    # source would hoist a temp instead, which is not lowered.
    # Checked HERE so the gate and the render key one predicate -- the
    # split version admitted shapes the render then rejected.
    if not _is_move_source_facts(a, movable_locals, analyzer, func_name):
        return None
    return inner


def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                    declared: dict[str, TpyType], *, temp_args: bool = False,
                    nested_temps: bool = False,
                    readonly_target: bool = False,
                    method_arg: bool = False,
                    method_arg_stub: bool = False,
                    protocol_slots: bool = False,
                    frame_capturing: bool = False,
                    marker_arg: bool = False,
                    inline_template: bool = False,
                    kind: CallArgKind = CallArgKind.UNSPECIFIED,
                    union_divergent_ok: bool = False) -> THIRExpr:
    """Lower one call argument against its param slot. A str literal into a
    char slot renders as a target-typed char literal (via
    `_lower_char_targeted`); a bytes literal into a bytes/BytesView slot
    takes the static-storage span pin (`::tpy::bytes_literal(...)`,
    keyed on the RAW ptype); a None literal / member-typed
    record name into a pointer-variant union slot takes the union
    inline lift (`_lower_union_arg_lift` -- checked FIRST so a pointer-local
    member name lifts `&((*p))` rather than retagging; `readonly_target`
    threads the callee's `const_borrow_params` verdict for the
    const-pointee spelling); an F2 pointer-local
    record name passed by reference derefs (`take_rec((*p))`, the
    indirect-name render -- only records become pointer-locals, so the
    membership test alone keys the retag); every other arg lowers
    position-blind."""
    if (method_arg and not method_arg_stub and not temp_args
            and _container_comp_arg(a, ptype)):
        # A comprehension at a USER-record method's container slot: the
        # method arg loop has no ref-param hoist, so the same stmt-expr the
        # free-call position wraps in an ArgTemp renders INLINE and the
        # const ref binds it for the full expression. The gate's
        # `container_comp` cell owns the slot's const verdict
        # (`_x_comp_slot_const`), so a mutated `T&` slot never reaches here.
        from .comprehensions import _lower_comprehension
        comp_slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        comp_ptrs = _comp_shadow_pointers(lc.pointers, declared, lc.analyzer)
        return _lower_comprehension(a, comp_slot, lc, declared, comp_ptrs)
    if method_arg_stub:
        _stub_comp_c = _stub_comp_target(a, ptype, lc.analyzer)
        if _stub_comp_c is not None:
            # The STUB-receiver twin of the arm above (`primes.extend([i for
            # i in xs])`, `",".join([str(x) for x in xs])`, `d.update({k: k
            # for k in ks})`): the stub arg loop has no hoist either, so the
            # stmt-expr renders inline, target-typed by the comprehension's
            # OWN container -- the free-call loop's native/template row.
            from .comprehensions import _lower_comprehension
            _stub_ptrs = _comp_shadow_pointers(lc.pointers, declared,
                                               lc.analyzer)
            _witness("arg.native_comprehension")
            return _lower_comprehension(a, _stub_comp_c, lc, declared,
                                        _stub_ptrs)
    if _own_container_comp_arg(a, ptype, lc.analyzer):
        # A comprehension into an `Own[container]` slot: the stmt-expr
        # prvalue IS the slot's container, so it moves into the by-value
        # slot inline -- no temp, no flush, at every loop (the
        # own_container_literal row's comprehension twin).
        from .comprehensions import _lower_comprehension
        own_c = _own_container_comp_slot(ptype, lc.analyzer)
        own_ptrs = _comp_shadow_pointers(lc.pointers, declared, lc.analyzer)
        _witness("arg.own_container_comp")
        return _lower_comprehension(a, own_c, lc, declared, own_ptrs)
    if (temp_args and not method_arg and not inline_template
            and _container_comp_arg(a, ptype)):
        # The slot-typed comprehension ArgTemp
        # (`std::vector<int64_t> __tmp_N = ({ ... });`) -- the init is the
        # decl-init arm's stmt-expr, target-typed by the slot. Shared by the
        # flushing plain-callee loops (free call, ctor); the method loops
        # render the stmt-expr inline above, native/template slots take the
        # inline twin in the free-call loop.
        from .comprehensions import _lower_comprehension
        slot_c = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        pointers = _comp_shadow_pointers(lc.pointers, declared,
                                         lc.analyzer)
        comp = _lower_comprehension(a, slot_c, lc, declared, pointers)
        _witness("argtemp.comprehension")
        return THIRArgTemp(result_type=slot_c, cpp_type=slot_c.to_cpp(),
                           movable=unwrap_ref_type(slot_c).is_movable(),
                           init=comp, form=Form.BORROW,
                           loc=getattr(a, "loc", None))
    if (isinstance(a, TpyFieldAccess)
            and _container_module_var_arg(a, ptype, declared, lc.analyzer)):
        # A module-variable container at a plain container ref slot
        # (`take(sys.argv)` -> `take((*::tpystd::sys::argv))`): the slot is
        # the pinned consumer that makes the pointer-slot `(*slot)` read
        # legal, and the bind is by reference, so the render is the bare
        # read the gate admitted.
        return _lower_module_var(
            a, lc.analyzer.get_expr_type(a), lc,
            *_module_var_access_pair(a, declared, lc.analyzer),
            loc=getattr(a, "loc", None), allow_ref_pointer=True)
    if (kind is CallArgKind.GENERIC
            and _own_tuple_call_rvalue_slot(a, ptype, lc.analyzer)):
        # An owning call whose result IS the substituted `Own[tuple]` slot
        # binds the by-value slot bare -- the owning-slot use is what its own
        # result gate answers there (the union row's tuple twin). GENERIC
        # callees only: at a concrete `Own[tuple]` element slot the value
        # position already routes, and re-tagging it would move the verdict
        # to a gate that has no row for it.
        return _lower_expr(
            a, lc, declared,
            use=replace(_NESTED_ARG_USE, result=_ExprResultUse.OWN_SLOT,
                        allow_temps=temp_args))
    if (isinstance(a, (TpyCall, TpyMethodCall))
            and _native_iterable_call_arg(a, ptype, lc.analyzer)
            and (inline_template
                 or not _dict_view_call_result(a, lc.analyzer))):
        # A container-returning call rvalue at a native Iterable/Sized
        # slot binds bare -- the ITERABLE result family wires the inner
        # call's container return (the foreach capture's admission). A
        # dict-VIEW result binds bare only under the native/template loop:
        # at a USER callee's structural slot it hoists `auto __tmp_N =`
        # and passes the temp, which a bare bind would drop.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                        allow_temps=temp_args))
    if _whole_value_opt_field_arg(a, ptype, declared, lc.analyzer):
        # A whole value-repr Optional FIELD read at the exactly-matching
        # slot: the optional is a value passed by value, so the bare member
        # is the render -- the whole-member read must not deref.
        return _lower_expr(a, lc, declared, allow_whole_optional=True)
    _ptr_slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
                 if isinstance(ptype, TpyType) else None)
    if isinstance(_ptr_slot, PtrType) and isinstance(a, TpyNoneLiteral):
        # `passthrough(None)` at the (collapsed) `Ptr[T]` slot: the bare
        # `nullptr` (the None-into-Ptr-target render).
        _witness("arg.ptr_none")
        return THIRLiteral(result_type=_ptr_slot, value=None,
                           none_cpp="nullptr", form=Form.VALUE,
                           loc=getattr(a, "loc", None))
    if (isinstance(_ptr_slot, PtrType)
            and isinstance(a, TpyFieldAccess)
            and reads_storage_form_optional(lc.analyzer, a)):
        # A storage-form Optional FIELD at a `Ptr[T]` slot lifts via
        # `::tpy::optional_to_ptr(h.opt)` -- the PtrType-slot storage-opt
        # wrap (the gate matched the pointee).
        _witness("optptr.ptr_slot_lift")
        return THIROptionalPtrArg(
            result_type=lc.analyzer.get_expr_type(a), form=Form.BORROW,
            value=_lower_field_source(a, lc, declared),
            lift=True, loc=getattr(a, "loc", None))
    _ooc = _own_opt_container_ptr_arg(a, ptype, lc, declared)
    if _ooc is not None:
        # The inline storage-optional materialization moving the slot
        # pointee (see the predicate). The name renders BARE (the raw
        # pointer) inside the spelled wrap. LAST-USE-gated, like every
        # other move render.
        _witness("arg.own_opt_container_move")
        _ooc_nm = THIRName(result_type=declared.get(a.name), name=a.name,
                           form=Form.VALUE, loc=getattr(a, "loc", None))
        _ooc_cpp = lc.render_type(_ooc)
        return THIRCoerce(
            result_type=ptype, expr=_ooc_nm,
            coercion_name="own_opt_container_move",
            wrap=(f"std::move({{0}} ? std::optional<{_ooc_cpp}>("
                  f"std::move(*{{0}})) : std::nullopt)"),
            form=Form.VALUE, loc=getattr(a, "loc", None))
    if (method_arg_stub and isinstance(a, (TpyCall, TpyMethodCall))
            and _container_slot_call_rvalue_arg(a, ptype, lc.analyzer)):
        # A container-returning call at a stub method's concrete container
        # slot binds bare (`::tpy::dict_update(a, make_dict())`): STORAGE
        # use so the inner call's result gate admits the container -- the
        # free/marker native row's stub-method twin.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=nested_temps))
    if (_own_proto_container_slot(a, ptype, lc.analyzer, declared)
            is not None):
        # A CONTAINER conformer NAME at an `Own[protocol]` slot: the
        # monomorphized `T_items&&` param consumes it -- the last-use
        # move (`indexed<int32_t>(std::move(nums))`); a still-live name
        # hoists the copy temp and moves that (`auto __tmp_N = words;` +
        # `first<std::string>(std::move(__tmp_N))` -- the needs_copy
        # cascade).
        if a.name in lc.narrow.narrowed:
            raise ThirUnsupported("arg.own_proto_container_live")
        if _is_move_source(a, lc):
            lowered = _lower_expr(a, lc, declared)
            _witness("move.own_proto_container")
            return THIRMove(result_type=lowered.result_type, value=lowered,
                            form=Form.STORAGE, loc=getattr(a, "loc", None))
        if not temp_args:
            raise ThirUnsupported("arg.own_proto_container_live")
        lowered = _lower_expr(a, lc, declared)
        _witness("argtemp.own_proto_container")
        _opc = lowered.result_type
        return THIRArgTemp(result_type=_opc, cpp_type=lc.render_type(_opc),
                           init=lowered, move=True, form=Form.STORAGE,
                           movable=unwrap_ref_type(_opc).is_movable(),
                           loc=getattr(a, "loc", None))
    if (method_arg_stub
            and _wrapper_union_elem_name_arg(a, ptype, declared,
                                             lc.narrow.narrowed, lc.analyzer)
            and _is_move_source(a, lc)):
        # A movable wrapper-union NAME at its last use in a container-insert
        # element slot moves
        # (`a.push_back(std::move(item))`); a non-movable one falls through
        # to the bare name the insert copies. Scoped to the builtin-stub
        # member, which is the only position whose slot this predicate reads
        # as an ELEMENT -- a user callee's bare wrapper param binds by
        # reference and has its own rows.
        lowered = _lower_expr(a, lc, declared)
        _witness("move.wrapper_union_elem")
        return THIRMove(result_type=lowered.result_type, value=lowered,
                        form=Form.STORAGE, loc=getattr(a, "loc", None))
    if _own_tuple_move_arg(a, ptype, lc, declared):
        # An OWN-element tuple name at the matching rvalue tuple slot
        # moves whole at its last use (`consume(std::move(t))`).
        lowered = _lower_expr(a, lc, declared,
                              use=replace(_NESTED_ARG_USE,
                                          pos=SinkPos.CALL_ARG,
                                          forms=_call_arg_forms(
                                              whole_tuple=True,
                                              coro_factory=False,
                                              ptr_opt_pass=False)),
                              allow_unrouted_name=True)
        _witness("move.own_tuple")
        return THIRMove(result_type=lowered.result_type, value=lowered,
                        form=Form.STORAGE, loc=getattr(a, "loc", None))
    if _own_tuple_borrow_lift_arg(a, ptype, lc, declared):
        # A BORROW-form Own-element tuple name at the && slot lifts
        # through the F3 storage conversion (`sink(tuple_to_storage<
        # std::tuple<Box, int32_t>>(pair))` -- the warned copy).
        # Still-live STORAGE bindings take the auto(p) decay-copy row
        # instead -- excluded here by the storage/Own-param guards.
        _witness("arg.own_tuple_borrow_lift")
        _obl_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        return THIRFormConvert(
            result_type=_obl_t,
            value=_lower_expr(a, lc, declared, allow_unrouted_name=True),
            form=Form.STORAGE, loc=getattr(a, "loc", None))
    if _own_tuple_decay_copy_arg(a, ptype, lc, declared):
        # A still-live STORAGE-form Own-tuple name at the && slot
        # decay-copies into a prvalue (`sink(auto(p))` -- the warned
        # copy).
        _witness("arg.own_tuple_decay_copy")
        lowered = _lower_expr(a, lc, declared, allow_unrouted_name=True)
        return THIRDecayCopy(result_type=lowered.result_type, value=lowered,
                             form=Form.STORAGE, loc=getattr(a, "loc", None))
    if isinstance(a, TpyGeneratorExpression) and not (
            protocol_slots and _protocol_arg_slot(ptype) is not None):
        # A genexpr into a native builtin's Iterable slot -> the make_generator
        # IIFE renders INLINE (`all(x > 0 for x in xs)` -- the @cpp_template
        # `{0}` substitution). A STRUCTURAL user slot hoists the auto temp
        # instead (the protocol block's genexpr arm below). Late import:
        # comprehensions imports this module.
        from .comprehensions import _lower_genexpr
        return _lower_genexpr(a, lc, declared)
    if isinstance(a, TpyTupleLiteral):
        pslot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
                 if isinstance(ptype, TpyType) else None)
        if isinstance(pslot, OwnType):
            # An Own[value-tuple] element slot (`ps.append(("k", 9))`) takes
            # the same spelled value render as the bare tuple slot; an
            # Own[pointer-repr tuple] slot (`pairs.append((a, b))` at
            # `list[tuple[P | None, ..]]`) takes the CONSUMING storage lift:
            # the borrow tuple builds with per-element moves and
            # `tuple_to_storage_move<S>(..)` materializes it (per-element
            # ownership -- last-use lvalue / fresh rvalue / copy() / None --
            # enforced by the ladder's source rules; anything else rejects).
            own_inner = unwrap_readonly(pslot.wrapped)
            if _open_t_tuple_slot(own_inner, lc.analyzer) is not None:
                # An OPEN-T element slot (`pairs.append((copy(k), n))` at
                # `list[tuple[T, int]]`): the owning sink spells the generic
                # element `T`, so this is the by-value builder, NOT the
                # `val_or_ptr_t<T>` borrow render a plain `tuple[T, ..]` param
                # takes.
                _witness("arg.own_open_t_tuple_literal")
                return _lower_borrow_tuple_literal(
                    a, own_inner, lc, declared, rvalue_ok=True,
                    use=_ExprUse(allow_temps=temp_args or nested_temps),
                    consuming=True)
            if (isinstance(own_inner, TupleType)
                    and not own_inner.has_pointer_repr_element()):
                pslot = own_inner
            elif (isinstance(own_inner, TupleType)
                    and own_inner.has_pointer_repr_element()):
                inner = _lower_borrow_tuple_literal(
                    a, own_inner, lc, declared, rvalue_ok=True,
                    use=_ExprUse(allow_temps=temp_args or nested_temps),
                    consuming=True)
                _witness("arg.own_btuple_literal")
                return THIRFormConvert(
                    result_type=own_inner, value=inner, form=Form.STORAGE,
                    move=True, loc=getattr(a, "loc", None))
        if isinstance(pslot, TupleType):
            # A tuple literal at a tuple param slot: value tuples take the
            # spelled value render; pointer-repr slots take the borrow
            # builder -- lvalue elements lift `&(...)`, rvalue elements ride
            # the tuple_value_to_borrow source-tuple path (the arg is a full
            # expression, so the source's lifetime covers the call). Element
            # shapes outside the builder's slice raise.
            # Shared with the user-record METHOD arg loop (`h.set((x, y))`);
            # the native / marker arg gates still have no tuple-literal row.
            if (pslot.has_pointer_repr_element()
                    # A UNION-element tuple slot has no pointer-repr element
                    # (a variant is not a bare `T*`), but its borrow form
                    # still differs from its storage form -- so it takes the
                    # same builder, whose union arm spells the const
                    # ptr-variant destination.
                    or (_union_elem_tuple(pslot, lc.analyzer) is not None
                        and _witness("call.union_elem_tuple_arg"))):
                return _lower_borrow_tuple_literal(
                    a, pslot, lc, declared,
                    target_readonly=isinstance(
                        unwrap_ref_type(unwrap_send_sync(ptype)),
                        ReadonlyType),
                    rvalue_ok=True,
                    use=_ExprUse(allow_temps=temp_args or nested_temps))
            if _open_t_tuple_slot(pslot, lc.analyzer) is not None:
                # An OPEN-T tuple slot (`s.consume((v, int32(2)))` at
                # `tuple[T, int32]`): the element slot is `val_or_ptr_t<T>`,
                # decided at instantiation, so the generic builder spells
                # the wrap -- the same render its return/yield sinks use.
                _witness("arg.open_t_tuple_literal")
                return _lower_generic_tuple_literal(a, pslot, lc, declared)
            vt = _value_tuple_nested(pslot, lc.analyzer)
            if vt is not None:
                _witness("btuple.value_arg")
                return _lower_tuple_literal(a, vt, lc, declared)
            if (len(a.elements) == len(pslot.element_types)
                    and any(isinstance(unwrap_readonly(et), OwnType)
                            # ... and the `Own[P] | None` element flavor:
                            # STORAGE form (`std::optional<P>`), so the
                            # brace-init spells the same by-value slot.
                            or _own_opt_storage_binding(et)
                            for et in pslot.element_types)):
                # An Own-ELEMENT tuple slot (`read_owned((A(1), A(2)))` at
                # `std::tuple<A, A>&&`): the spelled brace-init with each
                # element rendered against its Own-peeled by-value slot --
                # the Own-storage-tuple RETURN arm's arg twin, same
                # storage-direct member rules (a non-value member must be
                # an rvalue or a movable last-use name).
                own_elems_ok = True
                for i, sub in enumerate(a.elements):
                    mslot = _unwrap_own(pslot.element_types[i])
                    mbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        mslot)))
                    if (not mbare.is_value_type()
                            # An `Own[P] | None` slot takes the element by
                            # VALUE, with copy-vs-move decided off sema's
                            # movability -- a
                            # borrowed lvalue is a legitimate copy here, so
                            # the storage-direct rvalue/last-use rule (which
                            # exists to keep an Own slot from silently
                            # copying) does not apply.
                            and not _own_opt_storage_binding(mslot)
                            and not is_rvalue_source(lc.analyzer, sub)
                            and not (isinstance(sub, TpyName)
                                     and _is_move_source(sub, lc))):
                        own_elems_ok = False
                        break
                    if not _container_lit_elem_ok(
                            sub, mslot, declared, lc.analyzer, threaded=True,
                            forced=True, allow_record=True, allow_nested=True,
                            allow_optional=True, pointers=lc.pointers):
                        own_elems_ok = False
                        break
                if own_elems_ok:
                    _witness("arg.own_elem_tuple_literal")
                    return THIRTupleLiteral(
                        result_type=pslot,
                        elements=tuple(
                            _lower_container_elem(
                                a.elements[i],
                                _unwrap_own(pslot.element_types[i]),
                                lc, declared, tuple_elem=True,
                                # An `Own[P] | None` slot resolves to the
                                # REF capture mode, so the slot_owned test
                                # is False there and the element renders
                                # a plain copy -- unlike
                                # the VALUE-mode `Own[P]` slot next to it.
                                suppress_move=_own_opt_storage_binding(
                                    pslot.element_types[i]))
                            for i in range(len(a.elements))),
                        loc=getattr(a, "loc", None))
            raise ThirUnsupported("expr.tuple_literal")
    if isinstance(a, (TpySubscript, TpyCall, TpyMethodCall)):
        # Own[ptr-repr tuple] element slot, non-literal sources:
        # - a whole storage-tuple ELEMENT read (`pairs2.append(pairs[0])`)
        #   passes bare (`push_back(__getitem__(..))`, storage-to-storage);
        # - a borrow-tuple-returning CALL (`pairs.append(make_pair(a, b))`)
        #   lifts via the NON-move `tuple_to_storage<S>(..)` -- moving from
        #   a returned pointer would alias storage the caller still owns
        #   (the copy-path rule).
        _sub_own = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
                    if isinstance(ptype, TpyType) else None)
        if isinstance(_sub_own, OwnType):
            _sub_inner = unwrap_readonly(_sub_own.wrapped)
            _sub_at = lc.analyzer.get_expr_type(a)
            _sub_ab = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                _sub_at))) if _sub_at is not None else None)
            if (isinstance(_sub_inner, TupleType)
                    and _sub_inner.has_pointer_repr_element()
                    and _sub_ab == _sub_inner):
                if isinstance(a, TpySubscript):
                    return _lower_expr(
                        a, lc, declared,
                        use=replace(_NESTED_ARG_USE,
                                    pos=SinkPos.TUPLE_ELEM,
                                    result=_ExprResultUse.STORAGE))
                if _storage_form_tuple_return(a.resolved_function_info):
                    # A storage-form tuple RETURN already matches the owning
                    # slot and passes bare (`push_back(make_pair(..))`); only
                    # a borrow-form or MIXED-render return owes the copy lift.
                    _witness("arg.own_btuple_call_storage")
                    return _lower_expr(
                        a, lc, declared,
                        use=replace(_NESTED_ARG_USE,
                                    pos=SinkPos.TUPLE_ELEM,
                                    result=_ExprResultUse.STORAGE))
                _witness("arg.own_btuple_call")
                return THIRFormConvert(
                    result_type=_sub_inner,
                    value=_lower_expr(
                        a, lc, declared,
                        use=replace(
                            _NESTED_ARG_USE,
                            pos=SinkPos.TUPLE_ELEM,
                            result=_ExprResultUse.STORAGE,
                            allow_temps=temp_args)),
                    form=Form.STORAGE, move=False,
                    loc=getattr(a, "loc", None))
            if (isinstance(a, TpySubscript)
                    and _open_t_tuple_slot(_sub_inner, lc.analyzer) is not None
                    and _sub_ab == _sub_inner):
                # The OPEN-T sibling of the storage-element read above
                # (`out.append(ranked[i])` at `list[tuple[T, int]]`): the
                # generic element has no pointer repr, so borrow and storage
                # coincide and the read passes bare with no conversion.
                _witness("arg.own_open_t_tuple_storage_source")
                return _lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                pos=SinkPos.TUPLE_ELEM,
                                result=_ExprResultUse.STORAGE))
            if (isinstance(_sub_inner, TupleType)
                    and _sub_inner.has_pointer_repr_element()
                    and isinstance(a, (TpyCall, TpyMethodCall))
                    and _mixed_own_storage_source(a, _sub_inner, frozenset(),
                                                  lc.analyzer) is not None):
                # A MIXED-own-tuple call at the Own element slot: the same
                # NON-move materialization (`push_back(tuple_to_storage<
                # std::tuple<Box, Box>>(make_mixed(b)))`); the call renders
                # bare via the btuple-slot admission.
                _witness("arg.own_btuple_mixed_call")
                return THIRFormConvert(
                    result_type=_sub_inner,
                    value=_lower_expr(
                        a, lc, declared,
                        use=_ExprUse(
                            result=_ExprResultUse.VALUE,
                            pos=SinkPos.TUPLE_ELEM, forms=_ONLY_BTUPLE_SLOT)),
                    form=Form.STORAGE, move=False,
                    loc=getattr(a, "loc", None))
    if (isinstance(a, TpyName) and _value_opt_scalar_binding(a.name, lc)
            and _value_opt_scalar(ptype, lc.analyzer) is not None):
        # A value-repr Optional[scalar] name into a value-repr Optional slot
        # passes the WHOLE optional bare (`take_opt(p)`), even when sema
        # narrowed the read -- an arg derefs only for a NON-optional slot.
        # Strip the name arm's deref-on-narrow.
        return _whole_optional_bare(
            _lower_expr(a, lc, declared, allow_whole_optional=True))
    if _value_opt_tuple_pass_arg(a, ptype, declared, lc.narrow.narrowed,
                                 lc.analyzer):
        # The value-TUPLE sibling of the row above (`s.request(.., auth)`):
        # a value tuple is a value type, so the whole `std::optional<
        # std::tuple<..>>` binds the by-value slot bare. The name arm fences
        # this kind to whole-optional positions, so thread that use -- the
        # SAME predicate the gate keys on, so the two cannot drift.
        return _lower_expr(a, lc, declared, allow_whole_optional=True)
    if (isinstance(a, TpyName) and a.name in declared
            and _param_declared_type(a.name, lc) is None
            and _opt_view_arg_shim(declared[a.name], ptype, lc.analyzer)):
        # An OWNED value-repr Optional[str/bytes] LOCAL into a matching owned
        # Optional slot: the local already holds `optional<string>`, so the
        # WHOLE optional passes bare -- no view->owned shim (that fires only
        # for a PARAM whose binding is the borrow `optional<string_view>`, the
        # arm below). Strip any deref-on-narrow like the scalar sibling.
        _witness("call.optview_local_whole")
        return _whole_optional_bare(
            _lower_expr(a, lc, declared, allow_whole_optional=True))
    if _opt_strview_to_str_own_elem_arg(a, ptype, declared, lc.analyzer):
        # The opposite-direction Own-slot face: a VIEW-inner value optional
        # (a local read whole off its binding, or a same-typed call rvalue)
        # into an `Own[Optional[str]]` element slot takes the coercion
        # lambda's non-identity branch -- the once-evaluated `__ov`
        # statement expression that materializes each element.
        inner = _whole_optional_bare(
            _lower_expr(a.expr, lc, declared, allow_whole_optional=True))
        _witness("arg.opt_strview_own_shim")
        return THIRCoerce(
            result_type=a.expected_type, expr=inner,
            coercion_name=a.coercion.name,
            wrap=("({{ auto __ov = ({0}); __ov ? "
                  "std::make_optional(std::string(*__ov)) : "
                  "std::nullopt; }})"),
            form=Form.VALUE, loc=getattr(a, "loc", None))
    if (isinstance(a, TpyCoerce)
            and a.coercion.name in ("str_to_strview",
                                    "optional_str_to_strview")
            and isinstance(a.expected_type, OwnType)
            and isinstance(a.expr, TpyName)
            and lc.value_opt_bindings.get(a.expr.name) is ValueOptKind.VIEW):
        # A whole owned value-opt VIEW LOCAL at an `Own[Optional[StrView]]`
        # element slot (`items.append(src)`), read whole off the BINDING
        # regardless of sema's narrow. A NARROWED occurrence arrives as the
        # identity `str_to_strview` coerce and passes bare
        # (`push_back(src1)` -- optional<string> converts into
        # optional<string_view> implicitly); an UN-narrowed one carries
        # `optional_str_to_strview`, whose Own-slot ARG face is the
        # once-evaluated statement-expression shim (the coercion lambda's
        # non-identity branch).
        inner = _whole_optional_bare(
            _lower_expr(a.expr, lc, declared, allow_whole_optional=True))
        if a.coercion.name == "str_to_strview":
            _witness("arg.opt_view_own_bare")
            return inner
        _witness("arg.opt_view_own_shim")
        # VALUE form: the statement expression produces a prvalue
        # `std::optional<std::string_view>` (a value type -- view inner),
        # exactly the whole-optional VALUE the bare inner read carries.
        return THIRCoerce(
            result_type=a.expected_type, expr=inner,
            coercion_name=a.coercion.name,
            wrap=("({{ auto __ov = ({0}); __ov ? "
                  "std::make_optional(std::string_view(*__ov)) : "
                  "std::nullopt; }})"),
            form=Form.VALUE, loc=getattr(a, "loc", None))
    if (isinstance(a, TpyName) and method_arg_stub
            and _opt_view_param_own_elem_arg(
                a, ptype, declared, lc.analyzer, lc.prescan.param_names)):
        # The container-insert twin of the free-call shim row below: a
        # BUILTIN-container stub method DOES thread its element slot, so a
        # value-opt view PARAM takes the same ARG split -- under the
        # consuming move the insert render applies (the setitem row's
        # `moved=True`). Record-method args stay excluded: their `_args()`
        # loop threads no target, so the optional goes bare there.
        _witness("arg.optview_param_own_elem")
        return THIROptViewArg(
            result_type=_plain_own_slot(ptype), name=a.name, form=Form.VALUE,
            moved=True, loc=getattr(a, "loc", None))
    if (isinstance(a, TpyName) and method_arg and not method_arg_stub
            and a.name not in lc.narrow.narrowed
            and _value_opt_view_whole_arg(
                a, ptype, declared, frozenset(lc.narrow.narrowed),
                lc.analyzer)):
        # A whole value-opt VIEW name at a matching slot in a USER-record
        # method position: that loop is target-less, so the
        # optional passes BARE -- no shim. Stub loops thread the raw param
        # (the exclusion note below) and keep rejecting at their gates.
        _witness("method.optview_whole_arg")
        return _whole_optional_bare(
            _lower_expr(a, lc, declared, allow_whole_optional=True))
    if (isinstance(a, TpyName)
            and not method_arg
            and _opt_view_arg_shim(_param_declared_type(a.name, lc), ptype,
                                   lc.analyzer)):
        # A value-repr Optional[str] name into another value-repr Optional[str]
        # slot takes `_maybe_convert_opt_view_param`'s ARG split on the WHOLE
        # optional (narrowed or not): the borrow `optional<string_view>` binding
        # -> the owned `optional<string>` the slot needs (`s ? std::make_optional(
        # std::string(*s)) : std::nullopt`). The family match (str inner, not the
        # bare `StrView` spelling) is pinned by `_opt_view_arg_shim`.
        # A RECORD-method arg is excluded because that `_args()` loop
        # passes `target_type=None`, so the shim sees no target and the
        # optional goes BARE. Stub receivers are subsumed by `method_arg`, and
        # DO thread `ptype` -- they are safe only because their arg gate
        # (`_stub_method_arg_ok`) admits no such row: its value-opt VIEW rows
        # all require an Own ELEMENT slot, which no stub PARAM slot is. Widening
        # that gate must revisit this exclusion.
        return THIROptViewArg(
            result_type=ptype, name=a.name, form=Form.VALUE,
            loc=getattr(a, "loc", None))
    own_opt = _own_opt_ptr_name_move_arg(a, ptype, lc, declared)
    if own_opt is not None:
        # The Own-slot half of the null-safe conversion: a pointer-repr
        # Optional binding rebuilt into the owning `std::optional<T>` the
        # slot wants, moved over the whole ternary. Gated to the last-use
        # slice: a non-last-use occurrence needs a copy TEMP instead, a
        # different render.
        # The rebuild is a prvalue (its pointee is moved inside the
        # ternary), so it binds an `&&` slot and initializes a by-value one
        # bare; a `std::move` around it would only defeat elision.
        _witness("own.opt_ptr_name_rebuild")
        return THIROwnOptRebuild(
            result_type=own_opt, name=a.name,
            inner_cpp=lc.render_type(unwrap_readonly(own_opt.inner)),
            form=Form.VALUE, loc=getattr(a, "loc", None))
    none_opt = _none_value_opt_arg(a, ptype, lc.analyzer)
    if none_opt is not None:
        # A `None` literal into a value-repr Optional slot -> `std::nullopt`
        # (STORAGE-form None), whatever the inner -- a bare None read at a
        # value-optional target. A bare None
        # has no `_lower_expr` arm, so this must intercept before the tail.
        _witness("call.none_value_opt")
        return THIRLiteral(result_type=none_opt, value=None, form=Form.STORAGE,
                           loc=getattr(a, "loc", None))
    unit_none = _none_unit_arg(a, ptype)
    if unit_none is not None:
        # A `None` literal into a unit slot (`Rc.new(None)` -> the
        # substituted `Own[None]` param): the bare `std::monostate{}` value,
        # like the base-init arg's target-less render but monostate-typed.
        _witness("call.none_unit")
        return THIRLiteral(result_type=unit_none, value=None,
                           form=Form.STORAGE, loc=getattr(a, "loc", None))
    proto_tup = _native_protocol_tuple_literal_arg(a, ptype, lc.analyzer)
    if proto_tup is not None:
        # The monomorphized protocol slot threads no target, so the literal
        # spells its own sema type -- but each element's capture still comes
        # off the ELEMENT, giving a non-value simple lvalue a `T*`
        # ref slot. Both builders own their per-element admission, so an
        # element neither can render rejects.
        borrow = _proto_tuple_elem_borrow(a, proto_tup, declared, lc.analyzer)
        if borrow is None:
            note_detail("btuple.proto_mixed")
            raise ThirUnsupported("expr.tuple_literal")
        if borrow:
            lowered = _lower_borrow_tuple_literal(a, proto_tup, lc, declared)
            _witness("btuple.proto_borrow")
            return lowered
        return _lower_tuple_literal(a, proto_tup, lc, declared)
    if isinstance(a, TpyStrLiteral) and _eligible_char(ptype):
        return _lower_char_targeted(a, ptype, lc, declared)
    if (isinstance(a, TpyFieldAccess)
            and _record_field_ref_arg(a, ptype, declared, lc.analyzer,
                                      lc.narrow.narrowed)):
        # An F1-record FIELD read binding a record ref slot: the bare
        # member read (`pass_both(h.a, h.b)`), aliasing semantics preserved.
        # The predicate re-ran the marker/receiver checks, so the gates are
        # prechecked.
        _witness("call.record_field_arg")
        return _lower_expr(a, lc, declared, field_prechecked=True)
    if (isinstance(a, TpyFieldAccess)
            and _container_field_pass_arg(a, ptype, declared, lc.analyzer)):
        # A container FIELD read binding a plain container ref slot
        # (`heapq.heappush(self.heap, ...)` -> bare `this->heap`): the
        # predicate owns the receiver + declared-container checks, and the
        # slot binds the member read by reference -- aliasing preserved, so
        # the copy-vs-alias fence on the generic field VALUE position does
        # not apply here.
        return _lower_expr(a, lc, declared, field_prechecked=True)
    if (isinstance(a, TpyFieldAccess)
            and _value_tuple_field_pass_arg(a, ptype, declared, lc.analyzer)):
        # A VALUE-tuple FIELD read binding the matching tuple ref slot
        # (`self._sock.connect(self._addr)` -> bare `this->_addr`): borrow
        # and storage forms coincide for a value tuple, so the member read
        # binds by reference with no lift. The predicate owns the receiver +
        # declared-type checks, so the gates are prechecked; a POINTER-REPR
        # tuple field is outside the predicate and keeps its own lift.
        return _lower_expr(a, lc, declared, field_prechecked=True)
    if (isinstance(a, TpyNoneLiteral)
            and _protocol_union_arg(a, ptype, declared, lc.analyzer)
            == "nullproto"):
        # None at a nullable STATIC-protocol slot: the typed null
        # (`static_cast<std::nullptr_t*>(nullptr)` -- selects the
        # T_x = std::nullptr_t default instantiation).
        _witness("arg.nullproto_none")
        return THIRModuleVar(cpp="static_cast<std::nullptr_t*>(nullptr)",
                             result_type=unwrap_send_sync(ptype),
                             form=Form.BORROW,
                             loc=getattr(a, "loc", None))
    if (isinstance(a, TpyName)
            and _protocol_union_arg(a, ptype, declared, lc.analyzer)
            == "addr"):
        # A Span/container name into a NULLABLE all-protocols slot: the
        # address-of lift (`&(s)` / `c2.update(nums, &(more))` -- the
        # nullable slot binds `const T_x*` on free, ctor, and method
        # positions alike). The REQUIRED union stays bare everywhere: it
        # has no None member, so the verdict is inert for it
        # (`a.extend(b)` keeps its bare template bind).
        # allow_unrouted_name: an Own[container] PARAM name has no general
        # read arm, but the `&(...)` lift consumes the bare name whole --
        # the position pins the render (the truthiness precedent).
        return THIROptionalPtrArg(
            result_type=ptype,
            value=_lower_expr(a, lc, declared, allow_unrouted_name=True),
            addr_of=True, form=Form.BORROW, loc=getattr(a, "loc", None))
    if (isinstance(a, TpyArrayLiteral) and not method_arg
            and (_pul := _protocol_union_literal_temp_arg(
                a, ptype, lc.analyzer)) is not None):
        # The literal sibling: hoist the literal into a temp typed as its
        # own (demoted) sema type and lift the address
        # (`std::array<std::string, 3> __tmp_N = {..};` + `&(__tmp_N)`) --
        # the protocol slot's temporary tail.
        if not temp_args:
            raise ThirUnsupported(
                "protocol literal temp outside a flush position")
        init = _lower_literal_arg(
            a, _pul, lc, declared,
            "container literal at a nullable protocol ctor slot")
        _witness("argtemp.protocol_union_literal")
        # Movable comes off the temp's own (demoted sema) type, so the
        # conditional-region defer decision matches.
        return THIRArgTemp(result_type=_pul, cpp_type=_pul.to_cpp(),
                           init=init, addr_of=True, form=Form.BORROW,
                           movable=unwrap_ref_type(_pul).is_movable(),
                           loc=getattr(a, "loc", None))
    if (not method_arg
            and (_pui := _protocol_union_iter_temp_arg(
                a, ptype, declared, lc.analyzer)) is not None):
        # The iterator-rvalue sibling: the same spelled temp + address-of
        # lift (`::tpy::dict_items_view<..> __tmp_N = ::tpy::dict_items(d);`
        # + `&(__tmp_N)`). The init lowers under ITERABLE result use, as at
        # the structural-slot temp -- the same callees.
        if not temp_args:
            raise ThirUnsupported(
                "protocol iterator temp outside a flush position")
        init = _lower_expr(
            a, lc, declared,
            use=replace(_NESTED_ARG_USE, result=_ExprResultUse.ITERABLE,
                        allow_temps=True))
        _witness("argtemp.protocol_union_iter")
        return THIRArgTemp(result_type=_pui, cpp_type=_pui.to_cpp(),
                           init=init, addr_of=True, form=Form.BORROW,
                           movable=unwrap_ref_type(_pui).is_movable(),
                           loc=getattr(a, "loc", None))
    if _required_protocol_union_slot(ptype):
        # Everywhere ELSE that same slot renders the plain value:
        # a required multi-protocol union is claimed ahead of the
        # Optional-ptr and union-lift arms and renders as a plain deref
        # read (the slot monomorphizes to one template param, so
        # nothing lifts). The ctor arm above now early-returns for a REQUIRED
        # union, so only the nullable form reaches its address-of lift.
        _witness("arg.protocol_union_plain")
        return _lower_expr(a, lc, declared)
    if (isinstance(a, TpyArrayLiteral)
            and _container_literal_arg(a, ptype, lc.analyzer)):
        # A list literal into a ctor's list slot: the bare brace-init in
        # place, target-threaded like the decl position. The make_vector
        # element path (move-source / nocopy elements) is NOT lowered at
        # the arg position -- reject rather than risk a wrong render.
        slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        return _lower_literal_arg(
            a, slot, lc, declared,
            "container-literal arg on the make_vector path")
    if (isinstance(a, (TpyArrayLiteral, TpySetLiteral))
            and not protocol_slots
            and _native_iterable_literal_arg(a, ptype, lc.analyzer)):
        # A container literal into a native Iterable/Sequence slot: the
        # RESOLVED container renders inline (aggregate std::array / spelled
        # ordered_set), bare into the template slot. NATIVE/stub loops only
        # (protocol_slots=False): a plain/qualified loop's structural slot
        # hoists the `auto __tmp_N =` temp via the protocol pre-arm instead.
        # Only the native arg
        # gate admits this shape (like the genexpr arm above); make-path
        # elements reject rather than risk a divergent in-place render.
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            lc.analyzer.get_expr_type(a))))
        lowered = _lower_literal_arg(
            a, at, lc, declared,
            "container-literal native arg on the make path",
            array_retype=False)
        # A list/Array brace-init self-describes (`std::array<int32_t, 1>
        # {300}`): a bare `{..}` cannot bind the template slot, so the
        # resolved type is spelled. Set/dict renders spell themselves.
        if (isinstance(lowered, THIRContainerLiteral)
                and lowered.typed_brace_cpp is None
                and (is_list(at) or is_array(at))):
            lowered = replace(lowered, typed_brace_cpp=lc.render_type(at))
        return lowered
    if (isinstance(a, (TpyDictLiteral, TpySetLiteral)) and method_arg
            and _container_literal_method_arg(a, ptype, lc.analyzer)):
        # A dict / set literal into a builtin-container stub method slot
        # (`d.update({...})`): the spelled container render in place, like the
        # decl position. The slot's value carries an `Own[V]` move-in marker
        # the render strips, so target the container with each type-arg's
        # Own peeled off. The make_ordered_* (move / nocopy element)
        # path rejects, like the list arm.
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            lc.analyzer.get_expr_type(a))))
        target = _unown_type_args(at)
        return _lower_literal_arg(
            a, target, lc, declared,
            "container-literal method arg on the make path")
    # The span pin's slot rule lives in `_bytes_literal_view_slot` -- the
    # record-dunder OPERAND row spells the same slot, so both ask one
    # predicate. (`Own[BytesView]` is a no-op spelling on the VALUE view --
    # the set/dict stubs' insert param at a BytesView element.)
    if _bytes_literal_view_slot(ptype):
        # Peel coerce wrappers exactly like the span pin does (the pin
        # renders the bare literal; the coercion's own codegen never runs).
        lit = _peel_coerce(a)
        if isinstance(lit, TpyBytesLiteral):
            lowered = _lower_expr(lit, lc, declared)
            return replace(lowered, form=Form.BORROW)
    # An async-def factory call into an `Own[@dynamic P]` slot: the erasure
    # boundary -- `::tpy::make_adapter<Base>(factory(args))`, moving the
    # concrete coro frame into the heap adapter (the one allocation, paid
    # exactly here). Base spells from the SLOT protocol via the shared
    # `dynamic_base_name` helper, so the spellings cannot drift. The
    # factory lowers as an ordinary plain/imported free call
    # (the CORO_FACTORY form lifts only the async-callee reject).
    coro_proto = _dyn_own_coro_factory_arg(a, ptype, lc.analyzer)
    if coro_proto is not None:
        _witness("call.coro_factory_adapter")
        inner = _lower_expr(a, lc, declared,
                            use=_ExprUse(pos=SinkPos.CALL_ARG,
                                         forms=_call_arg_forms(
                                             whole_tuple=False,
                                             coro_factory=True,
                                             ptr_opt_pass=False),
                                         allow_temps=temp_args))
        base = dynamic_base_name(coro_proto, lc.analyzer)
        # THIRCoerce is form-preserving by contract (validate.py); the
        # adapter rvalue is consumed in place by the call slot, so the
        # inner's form rides through untouched.
        return THIRCoerce(
            result_type=unwrap_send_sync(ptype), expr=inner,
            coercion_name="dyn_own_adapter",
            wrap=f"::tpy::make_adapter<{base}>({{0}})",
            form=inner.form, loc=getattr(a, "loc", None))
    handle_proto = _dyn_own_handle_arg(a, ptype, declared, lc.analyzer)
    if handle_proto is not None:
        # The bound-handle face: the optional slot unwraps + moves into the
        # adapter (`std::move(*(c))`, the bare
        # name inside the wrap -- not the frame-deref `(*c)` render).
        _witness("call.coro_handle_adapter")
        inner = _whole_optional_bare(_lower_expr(a, lc, declared))
        base = dynamic_base_name(handle_proto, lc.analyzer)
        return THIRCoerce(
            result_type=unwrap_send_sync(ptype), expr=inner,
            coercion_name="dyn_own_adapter",
            wrap=f"::tpy::make_adapter<{base}>(std::move(*({{0}})))",
            form=inner.form, loc=getattr(a, "loc", None))
    conf = _dyn_own_conformer_arg(a, ptype, declared, lc.analyzer)
    if conf is not None:
        return _lower_dyn_own_conformer(a, ptype, conf, lc, declared,
                                        temp_args=temp_args)
    # The two arg-temp rows, admitted only when the enclosing
    # statement position flushes (`temp_args`; see _lower_expr). The record
    # row follows the ref-param cascade arm: the temp declares the SLOT's
    # bare `to_cpp()` (same-nominal only, so no upcast Child spelling
    # arises). The union row follows the union arg's value branch: the temp
    # declares the slot variant (== `to_cpp()` on the scalar-member slice).
    # A narrowed subject reads its extraction alias while the
    # `already_union` verdict would render it bare -- rejected
    # (`_value_union_temp_arg`); the check here is defense in depth.
    # The protocol-slot wrap (the @dynamic adapter / the free-call
    # protocol temp arm). A bare verdict means the arg renders like any other
    # -- fall through to the tail, which carries the pointer-local deref retag.
    # `protocol_slots` marks the loops that actually run those pre-arms; the
    # builtins loop does not, and renders a protocol slot bare.
    proto = _protocol_arg_slot(ptype) if protocol_slots else None
    if proto is not None:
        if _coro_factory_structural_arg(a, proto, lc.analyzer):
            # The coro-factory structural temp (`auto __tmp_N = f();`):
            # gate-admitted under temps_ok only; the frame result lowers
            # under CORO_FACTORY (lifts only the async-callee reject).
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.protocol")
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE, pos=SinkPos.CALL_ARG,
                                forms=_call_arg_forms(whole_tuple=False,
                                                      coro_factory=True,
                                                      ptr_opt_pass=False))),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        if isinstance(a, TpyStrLiteral) and not is_dyn_protocol(proto):
            # The str-literal structural temp (`auto __tmp_N = "hello";`):
            # un-spelled, so the protocol deduces on the raw char array.
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.protocol")
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(a, lc, declared),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        if _iter_rvalue_structural_arg(a, proto, declared, lc.analyzer):
            # The iterator sibling (gen-factory / iter() / dict-view
            # rvalues): the same un-spelled auto temp; the init lowers
            # under ITERABLE result use -- the universal loop's
            # source-capture admission covers exactly these callees.
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.iter_proto")
            # allow_temps rides through: the factory's OWN ref-slot literal
            # temps flush at the same statement, before this temp (`int32_t
            # __tmp_2 = 7;` then `auto __tmp_3 = repeat_n<int32_t>(__tmp_2,
            # 5);` -- the cascade's creation order).
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                result=_ExprResultUse.ITERABLE,
                                allow_temps=True)),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        if (isinstance(a, TpyGeneratorExpression)
                and not is_dyn_protocol(proto)):
            # A genexpr at a STRUCTURAL slot (`sum_items(x * x for x in
            # range(5))`): the make_generator render hoists into the same
            # un-spelled auto temp as the iterator rvalues.
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            from .comprehensions import _lower_genexpr
            _witness("argtemp.genexpr_proto")
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_genexpr(a, lc, declared),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        at = lc.analyzer.get_expr_type(a)
        if isinstance(a, (TpyArrayLiteral, TpyName)):
            # A container literal's expr type is still PENDING, and so is a
            # literal-seeded local's binding (`xs = [1, 2]` read at a
            # protocol slot). Sema's resolution is final before lowering, so
            # asking for it here gives the resolved type.
            at = resolve_pending_container(at, lc.analyzer) or at
        if isinstance(unwrap_readonly(unwrap_ref_type(at)), PendingListType):
            # A literal-seeded local's binding can still be PENDING here;
            # render_type would crash on it, so reject instead.
            note_detail("call.protocol_arg_pending")
            raise ThirUnsupported(call_reject_reason("expr.call"))
        rvalue = not isinstance(a, TpyName)
        spec = _protocol_arg_temp(proto, at, lc.render_type(at), lc.analyzer,
                                  rvalue=rvalue)
        if spec is not None:
            # Admitted only under `temps_ok`; a silent fall-through would drop
            # the adapter wrap and pass the concrete. A match guard admits calls
            # but is never a flush point, so reject here instead of asserting.
            if not temp_args:
                raise ThirUnsupported("protocol arg-temp outside a flush position")
            cpp_type, brace_init = spec
            if isinstance(a, TpyArrayLiteral):
                # The structural-rvalue literal temp is `auto`-declared, so
                # the init must self-spell its resolved container
                # (`auto __tmp_N = std::array<double, 2>{0.0, 0.0};`).
                atu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
                init = _lower_literal_arg(
                    a, atu, lc, declared,
                    "container-literal protocol arg on the make path",
                    array_retype=False)
                if (isinstance(init, THIRContainerLiteral)
                        and init.typed_brace_cpp is None
                        and (is_list(atu) or is_array(atu))):
                    init = replace(init, typed_brace_cpp=lc.render_type(atu))
            elif isinstance(a, TpySubscript):
                # The record-getitem rvalue init binds like a compare
                # operand (BORROW_BIND admits the by-value record result).
                init = _lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                result=_ExprResultUse.BORROW_BIND))
            elif _module_qual_ctor_shape(a, lc.analyzer):
                # A module-qualified ctor rvalue (`via_protocol(
                # io.StringIO("..."))`): the temp is a storage sink taking
                # the bare call whole, so the marker gate's result check
                # needs it named -- the record-rvalue temp arm's twin.
                init = _lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                result=_ExprResultUse.STORAGE))
            else:
                init = _lower_expr(a, lc, declared,
                                   use=replace(_NESTED_ARG_USE,
                                               pos=SinkPos.CALL_ARG))
            _witness("argtemp.protocol")
            return THIRArgTemp(result_type=proto, cpp_type=cpp_type,
                               init=init, brace_init=brace_init,
                               form=Form.BORROW, loc=getattr(a, "loc", None))
    # A container LITERAL into an `Own[genrec]` slot renders the ru-instance
    # spelling INLINE (`Holder(std::vector<Tree<int32_t>>{1, 2})` -- a
    # prvalue into the by-value Own slot, no temp and no flush needed; the
    # ctor-gate row admits it).
    if (_own_genrec_return(ptype) is not None
            and isinstance(a, (TpyArrayLiteral, TpyDictLiteral))
            and _ru_instance_literal_ok(a, lc.analyzer)):
        _witness("arg.genrec_own_literal")
        return _lower_ru_literal(a, lc.analyzer.get_expr_type(a),
                                 lc, declared)
    # A same-wrapper FIELD read the gate admitted (`leaf_count(self.t)`):
    # the bare member read IS the render; precheck the field arm so its
    # result gate does not re-ask the wrapper question.
    if _ru_wrapper_field_arg(a, ptype, declared, lc.analyzer):
        return _lower_expr(a, lc, declared, field_prechecked=True)
    # A BORROW-returning wrapper-like call at the same-wrapper slot
    # (`leaf_count(h.data.get())` -- the `Tree<T>&` result binds the
    # const-ref param bare): BORROW_BIND so the callee's result gate
    # admits the wrapper return, the free-call arm's qualcall twin.
    if _ru_wrapper_borrow_call_arg(a, ptype, lc.analyzer):
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    # A checked container-element read at a record ref slot
    # (`Player(things[0])`, `bag.take(things[0])`): the element lvalue
    # binds the slot inline -- the free-call arm's render, which every
    # other family's gate now admits as the same row.
    if _record_elem_subscript_arg(a, ptype, lc.analyzer):
        _witness("arg.record_elem_subscript")
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    # The VALUE-returning sibling (`leaf_count(build())` -- Own[Expr]):
    # already_union, so the default bare call render IS the arg.
    if _ru_wrapper_value_call_arg(a, ptype, lc.analyzer):
        return _lower_expr(a, lc, declared,
                           use=replace(_NESTED_ARG_USE,
                                       allow_temps=temp_args))
    if temp_args:
        cov_slot = _covariant_temp_arg(a, ptype, declared, lc.analyzer)
        if cov_slot is not None:
            # The covariant-upcast typed temp (`Box<Shape> __tmp_N =
            # std::move(bc);`): the target-typed init
            # absorbs the converting move; the arg position reads the temp
            # bare. A movable NAME moves in, a ctor rvalue lands bare.
            inner = _lower_expr(a, lc, declared,
                                use=replace(_NESTED_ARG_USE,
                                            result=_ExprResultUse.BORROW_BIND,
                                            allow_temps=temp_args,
                                            pos=SinkPos.CALL_ARG),
                                allow_unrouted_name=True)
            if isinstance(a, TpyName) and _is_move_source(a, lc):
                inner = THIRMove(result_type=inner.result_type,
                                 value=inner, form=inner.form,
                                 loc=getattr(a, "loc", None))
            _witness("argtemp.covariant")
            return THIRArgTemp(result_type=cov_slot,
                               cpp_type=lc.render_type(cov_slot), movable=unwrap_ref_type(cov_slot).is_movable(),
                               init=inner, form=Form.BORROW,
                               loc=getattr(a, "loc", None))
        rec_pt = _record_rvalue_temp_slot(a, ptype, lc.analyzer,
                                          frame_capturing=frame_capturing,
                                          upcast_ok=True)
        if (rec_pt is not None and isinstance(a, TpyMethodCall)
                and not _module_qual_ctor_shape(a, lc.analyzer)):
            # Method-call sources split three ways: a module-qualified CTOR
            # hoists (above), a NATIVE/template record call renders INLINE
            # (`samestat(s, ::tpystd::os::stat(d))` -- the
            # _native_record_call_arg row below), and a PLAIN Own-returning
            # method (`read_rc(Rc.new(Counter(3)))`) hoists the
            # create-lend-drop temp like a ctor rvalue.
            _mfi = a.resolved_function_info
            plain_own_method = (
                # Marker/qualified callee args render INLINE
                # (`samestat(s, ::tpystd::os::stat(d))` -- the
                # qualcall loop has no temp machinery), so the hoist is a
                # FREE-call-position row only.
                not marker_arg
                and _method_rvalue_f1_record(a, lc.analyzer)
                and _mfi is not None and _mfi.native_name is None
                and not _mfi.native_function and _mfi.cpp_template is None)
            if not plain_own_method:
                rec_pt = None
        if rec_pt is not None:
            _witness("argtemp.record_rvalue")
            return THIRArgTemp(
                result_type=rec_pt, cpp_type=rec_pt.to_cpp(), movable=unwrap_ref_type(rec_pt).is_movable(),
                # A module-qualified ctor source (TpyMethodCall) lowers via
                # the marker path, whose result gate needs the temp's
                # STORAGE sink named; the TpyCall ctor path is result-blind.
                init=_lower_expr(
                    a, lc, declared,
                    use=(replace(_RECORD_TEMP_FLUSH_USE,
                                 result=_ExprResultUse.STORAGE)
                         if isinstance(a, TpyMethodCall)
                         else _RECORD_TEMP_FLUSH_USE)),
                form=Form.BORROW,
                loc=getattr(a, "loc", None))
        ut = _value_union_temp_slot(a, ptype, declared, lc.analyzer)
        if ut is not None and not (isinstance(a, TpyName)
                                   and (a.name in lc.narrow.narrowed
                                        or a.name in lc.inline_narrowed)):
            _witness("argtemp.value_union")
            # A None arg is the union-typed literal (emit's monostate
            # render); other members keep the target-less init.
            init = (THIRLiteral(result_type=ut, value=None, form=Form.VALUE,
                                loc=getattr(a, "loc", None))
                    if isinstance(a, TpyNoneLiteral)
                    else _lower_expr(a, lc, declared, use=_NESTED_ARG_USE))
            return THIRArgTemp(
                result_type=ut, cpp_type=lc.render_type(ut), movable=unwrap_ref_type(ut).is_movable(),
                init=init,
                form=Form.VALUE,
                loc=getattr(a, "loc", None))
        # A list/dict LITERAL into a recursive-union WRAPPER slot
        # (`json.dumps([1, 2, 3])`): the union arg's value branch hoists
        # `JsonValue __tmp_N = std::vector<JsonValue>{...};` (the `= init`
        # form) and passes the bare temp name.
        ru = _ru_wrapper_arg_slot(ptype)
        if ru is not None and _ru_container_literal_ok(a, lc.analyzer):
            _witness("argtemp.recursive_union_literal")
            return THIRArgTemp(result_type=ru, cpp_type=lc.render_type(ru), movable=unwrap_ref_type(ru).is_movable(),
                               init=_lower_ru_literal(_peel_coerce(a), ru,
                                                      lc, declared),
                               form=Form.VALUE,
                               loc=getattr(a, "loc", None))
        # The scalar/str LITERAL sibling: the init is the literal's own
        # target-less render, hoisted by the same create_typed branch.
        ru_l = _ru_wrapper_scalar_literal_arg(a, ptype, lc.analyzer)
        if ru_l is not None:
            _witness("argtemp.ru_wrapper_literal")
            return THIRArgTemp(
                result_type=ru_l, cpp_type=lc.render_type(ru_l), movable=unwrap_ref_type(ru_l).is_movable(),
                init=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE),
                form=Form.VALUE, loc=getattr(a, "loc", None))
        # The member-CTOR-rvalue sibling: `eval_expr(Lit(42))` hoists
        # `Expr __tmp_N = Lit(...);` -- the prvalue init needs no move.
        ru_c = _ru_wrapper_member_rvalue_arg(a, ptype, lc.analyzer)
        if ru_c is not None:
            _witness("argtemp.ru_wrapper_ctor")
            return THIRArgTemp(
                result_type=ru_c, cpp_type=lc.render_type(ru_c), movable=unwrap_ref_type(ru_c).is_movable(),
                init=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE),
                form=Form.VALUE, loc=getattr(a, "loc", None))
        # The Own[genrec]-returning CALL sibling: `leaf_count(make_leaf())`
        # hoists `Tree<int32_t> __tmp_N = make_leaf();` -- the by-value
        # return binds the prvalue directly, bare temp name passed.
        ru_call = _ru_wrapper_own_call_arg(a, ptype, lc.analyzer)
        if ru_call is not None:
            _witness("argtemp.ru_wrapper_call")
            return THIRArgTemp(
                result_type=ru_call, cpp_type=lc.render_type(ru_call), movable=unwrap_ref_type(ru_call).is_movable(),
                init=_lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                result=_ExprResultUse.STORAGE)),
                form=Form.VALUE, loc=getattr(a, "loc", None))
        # M4c: a member-typed NAME into the wrapper slot hoists the typed
        # temp (`Tree __tmp_N = std::move(b);`), the init `_maybe_move`-
        # wrapped at a movable last use.
        ru_m = _ru_wrapper_member_name_arg(a, ptype, declared, frozenset())
        if ru_m is not None and not (a.name in lc.narrow.narrowed
                                     or a.name in lc.inline_narrowed):
            _witness("argtemp.ru_wrapper_member")
            init = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
            if _is_move_source(a, lc):
                init = THIRMove(result_type=init.result_type, value=init,
                                form=init.form, loc=getattr(a, "loc", None))
            return THIRArgTemp(result_type=ru_m,
                               cpp_type=lc.render_type(ru_m), movable=unwrap_ref_type(ru_m).is_movable(),
                               init=init, form=Form.VALUE,
                               loc=getattr(a, "loc", None))
    # A str-slice arg into an `Own[str]` container element slot
    # (`xs.append(s)`): a VIEW-form source (BORROW -- a str param / StrView
    # local) materializes an owned copy `std::string(x)` via the S1 view->owned
    # THIRFormConvert, exactly like `_lower_container_elem`'s element wrap; a
    # str literal (VALUE, const char[N]) lands bare. The gate
    # (`_str_owned_slot_arg`) admits only these two -- an owned STORAGE source
    # needs the copy+move temp cascade, which this row does not spell.
    ow_str = _plain_own_slot(ptype)
    if (ow_str is not None and is_str_type(ow_str)
            and _str_owned_slot_arg(a, ptype, declared,
                                    lc.prescan.param_names, lc.analyzer,
                                    lc.pointers)):
        # Re-checked here (not gate-trusted): the Own[str] copy+move temp row
        # below shares the slot, so this bare/convert render must fire only
        # for its own literal/view/element/rvalue faces.
        _witness("arg.own_str_slot")
        # The sema coerce over a StrView NAME IS the S1 copy: peel it and
        # wrap the bare view read, like the bytes twin below. The row's own
        # render is temp-free, so the enclosing flush right rides into a
        # CALL source's nested args (`out.append(joins(*parts...))` -- the
        # pack temp hoists at the statement).
        src_s = _strview_coerce_name(a) or a
        lowered = _lower_expr(src_s, lc, declared,
                              use=replace(_NESTED_ARG_USE,
                                          allow_temps=(temp_args
                                                       or nested_temps)))
        if lowered.form is Form.BORROW:
            return THIRFormConvert(result_type=ow_str, value=lowered,
                                   form=Form.STORAGE, loc=getattr(a, "loc", None))
        return lowered
    # The bytes twin: a VIEW-form source (a bytes param / narrowed deref /
    # view slice) at an `Own[bytes]` element slot materializes
    # `::tpy::Bytes(x)` via the S6 view->owned THIRFormConvert. The
    # sema `bytesview_to_bytes` coerce IS that copy, so it peels and the
    # convert wraps the bare view render underneath. A `bytearray` source
    # never arrives here: an owning `bytes` sink handed one is refused in
    # sema, which asks for the explicit `bytes(...)`.
    ow_bytes = _plain_own_slot(ptype)
    if (ow_bytes is not None
            and is_bytes_type(unwrap_readonly(ow_bytes))
            and _bytes_owned_slot_arg(a, ptype, declared,
                                      lc.prescan.param_names, lc.analyzer)):
        _witness("arg.own_bytes_slot")
        src_b = a
        if (isinstance(src_b, TpyCoerce)
                and src_b.coercion.name == "bytesview_to_bytes"):
            src_b = src_b.expr
        lowered = _lower_expr(src_b, lc, declared, use=_NESTED_ARG_USE)
        if lowered.form is Form.BORROW:
            return THIRFormConvert(result_type=unwrap_readonly(ow_bytes),
                                   value=lowered, form=Form.STORAGE,
                                   loc=getattr(a, "loc", None))
        return lowered
    # The open-`T` sibling of the two rows above: a `T`-typed PARAM at an
    # `Own[T]` / `T` element slot. The param arrives in the instantiation's
    # parameter form, a view over the storage form the element slot spells for
    # `str` / `bytes`, so the construction is explicit and keyed on `T`.
    ow_tp = _open_tparam_storage_slot(ptype)
    if (ow_tp is not None
            and _open_tparam_param_read(a, declared, lc.prescan.param_names)):
        _witness("arg.open_tparam_storage")
        lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
        return THIRFormConvert(result_type=ow_tp, value=lowered,
                               form=Form.STORAGE, loc=getattr(a, "loc", None))
    # `copy(name)` of a plain F1-record into a SAME-nominal `Own[record]`
    # slot -- or its Own-OPTIONAL sibling (the shared
    # `_plain_or_opt_own_slot` peel): the copy-construct rvalue
    # (`push_back(Point(p))` / `consume_optional(Box(b))`) binds directly
    # -- no temp, no move. Re-runs the source check with the live pointer
    # set (the gate could not see it); a pointer-local source falls
    # through to the tail and rejects.
    own_slot = _plain_or_opt_own_slot(ptype)
    if own_slot is not None:
        crow = _lower_copy_record(a, lc, declared, slot_type=own_slot,
                                  exact=True,
                                  loc=getattr(a, "loc", None))
        if crow is not None:
            return crow
        open_t = _copy_open_elem_arg(a, ptype, lc.analyzer)
        if open_t is not None:
            # The open-T element copy (`T(<element read>)`): the generic
            # copy tail spelled off the slot's T, around the standard
            # checked subscript render.
            _witness("ctor.copy_open_tuple_elem"
                     if isinstance(open_t, TupleType)
                     else "ctor.copy_open_elem")
            return THIRCopy(
                result_type=open_t,
                value=_lower_expr(copy_call_arg(a, lc.analyzer), lc,
                                  declared, use=_COPY_SRC_USE),
                cpp_type=lc.render_type(open_t), form=Form.STORAGE,
                loc=getattr(a, "loc", None))
    # The Own-slot copy+move row: `auto __tmp_N = <arg>;` + the move wrap
    # at the arg position -- or the temp-free `std::move(name)` when the
    # name is movable at its last use (the move fires before the
    # copy arm; a scalar / pointer-local / field read is
    # never movable, so it always copies). A pointer-local name derefs in
    # the temp init (`auto __tmp_N = (*p);`), like the plain record-arg
    # retag below. The MOVE half is position-independent (no flush needed),
    # so it runs outside `temp_args` too -- the `heap_take(value)` ctor-MIL
    # shape; the copy half still needs the flush (gate-enforced).
    ow = _own_lvalue_temp_slot(a, ptype, lc.analyzer, declared,
                               lc.prescan.param_names)
    if ow is None:
        # The MOVE-ONLY bytes slot (a bytearray->bytes identity chain, or a
        # bare owned-form name): serves the last-use move arm below
        # exclusively -- the non-move halves must not see it (a non-moved
        # bytes lvalue passes BARE to an inline_template callee).
        ow_bytes = _own_bytes_identity_move_slot(
            a, ptype, lc.analyzer, locals_=declared,
            param_names=lc.prescan.param_names)
        if (ow_bytes is not None
                and _own_move_source_slice(a, ptype, lc, declared)):
            ow = ow_bytes
    if ow is not None and isinstance(a, TpyName):
        # The Own-cascade's union lift fires BEFORE the move and
        # before the inline_template lvalue skip: a ptr-variant-BOUND name
        # at the value-variant `Own[union]` slot copies the active member
        # out (`push_back(::tpy::to_value_variant<std::variant<A, B>>(p))`).
        # Keyed on the BINDING set, not the type -- a value-variant-bound
        # name (a for-each element) keeps the plain copy machinery below.
        _ow_u = unwrap_readonly(unwrap_send_sync(ow))
        if (isinstance(_ow_u, UnionType) and _ow_u.uses_pointer_repr()
                and not _ow_u.needs_wrapper()
                and a.name in lc.ptr_variant_locals
                # Defensive, not gate-trusted: a NARROWED name renders the
                # concrete alternative (no wrap there), so the
                # lift must never see one even if a future admission arm
                # lets it through.
                and a.name not in lc.narrow.narrowed
                and a.name not in lc.inline_narrowed):
            _witness("arg.union_value_lift")
            return THIRFormConvert(
                result_type=_ow_u,
                value=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                                  allow_unrouted_name=True),
                form=Form.STORAGE, loc=getattr(a, "loc", None))
    ow_bare = _peel_coerce(a)
    if (ow is not None and inline_template
            and isinstance(a, (TpyCall, TpyMethodCall))):
        # The copy+move arm for a NON-simple lvalue is skipped for a
        # cpp_template / native callee (it binds the lvalue natively), so
        # hoisting the temp here would be wrong.
        note_detail("call.own_template_call_arg")
        raise ThirUnsupported(call_reject_reason("expr.call"))
    if (ow is not None and inline_template
            and isinstance(a, TpyName)
            and not is_str_type(ow) and not is_str_view_type(ow)
            # SCALAR payloads only, restating the admission gate
            # (`_native_own_scalar_lvalue_arg`) locally: a live record
            # NAME must keep the copy+move temp -- the bare skip would
            # silently alias where a copy is required.
            and _resolved_scalar(lc.analyzer.get_expr_type(a), lc.analyzer)
            and not _own_move_source_slice(a, ptype, lc, declared)):
        # The inline_template Own arm: a cpp_template / native callee binds
        # a simple non-str lvalue natively -- no copy+move temp (str keeps
        # it: the copy is the view->owned conversion). The move half fired
        # above, via the move-source check; what remains renders bare.
        _witness("call.native_own_scalar_lvalue")
        ow = None
    if (ow is not None and isinstance(a, TpyCoerce)
            and isinstance(ow_bare, TpyName)
            and (ow_bare.name in lc.pointers
                 or ow_bare.name in lc.frame_slots)
            # The MOVE fires before the rendered-string test
            # (it precedes the needs_copy cascade), so a moving
            # last use takes the move arm below regardless of the inner's
            # `(*name)` render.
            and not _own_move_source_slice(a, ptype, lc, declared)):
        # The coerce face's needs_copy verdict assumed the name renders
        # plain; a pointer-local / frame-slot inner renders `(*name)`, where
        # the rendered-string test flips to needs_copy=False (bare, no
        # temp) -- unwitnessed, so it rejects.
        note_detail("call.own_coerce_nonplain_name")
        raise ThirUnsupported(call_reject_reason("expr.call"))
    if ow is not None and not (isinstance(ow_bare, TpyName)
                               and (ow_bare.name in lc.narrow.narrowed
                                    or ow_bare.name in lc.inline_narrowed)):
        own_form = (Form.VALUE
                    if (_eligible_scalar(ow) or _eligible_char(ow))
                    else Form.STORAGE)
        # VALUE payloads never move: the arms that promote a value-typed name
        # (frame locals, owned tuples, unpack targets) can put one in
        # `movable_locals`, and moving it here would render
        # `xs.append(std::move(n))` where the render must be bare. The filter
        # keys on the ARG's own declared payload (like the gate row and the
        # seeding), not the callee slot's type param: TypeParamRef.__eq__
        # ignores bounds, so a caller's value-bound T can slot-match an
        # unbound callee T while its OWN value-ness forbids the move.
        # `_own_move_source_slice` carries the whole verdict, shared with
        # the nested-ctor gate so the two cannot drift.
        # Both arms consume the bare name whole (`std::move(name)` / the
        # `auto __tmp_N = name;` init), so an UNROUTED binding kind (an
        # `Own[container]` param, whose general name read has no arm) is
        # safe here -- the position pins the render (the truthiness
        # precedent for allow_unrouted_name).
        if _own_move_source_slice(a, ptype, lc, declared):
            lowered = _lower_expr(a, lc, declared,
                                  use=replace(_NESTED_ARG_USE,
                                              pos=SinkPos.CALL_ARG),
                                  allow_unrouted_name=True)
            _witness("move.own_last_use")
            return THIRMove(result_type=ow, value=lowered, form=own_form,
                            loc=getattr(a, "loc", None))
        if temp_args:
            own_str = (is_str_type(ow)
                       # The VIEW payload takes the same brace-init temp
                       # (`std::string_view __tmp_N{s};`), spelled at the
                       # view-resolved slot type.
                       or is_str_view_type(ow))
            lowered = _lower_expr(a, lc, declared,
                                  use=replace(_NESTED_ARG_USE,
                                              pos=SinkPos.CALL_ARG),
                                  allow_unrouted_name=True,
                                  own_slot_coerce=isinstance(a, TpyCoerce),
                                  field_owned_str_ok=own_str)
            if own_str:
                # The str payload declares the owned type with brace init
                # (`std::string __tmp_N{this->label};` -- the view->owned
                # conversion), unlike the `auto` copy of the other payloads.
                _witness("argtemp.own_str")
                return THIRArgTemp(result_type=ow,
                                   cpp_type=lc.render_type(ow),
                                   brace_init=True, init=lowered, move=True,
                                   movable=ow.is_movable(),
                                   form=own_form, loc=getattr(a, "loc", None))
            _witness("argtemp.own_copy")
            if isinstance(a, (TpyCall, TpyMethodCall)):
                _witness("argtemp.own_borrow_call")
            return THIRArgTemp(result_type=ow, cpp_type=lc.render_type(ow),
                               init=lowered, move=True,
                               movable=ow.is_movable(),
                               form=own_form, loc=getattr(a, "loc", None))
        if is_str_type(ow):
            # The str copy temp is a needed CONVERSION in every position
            # (never elided for a cpp_template callee either); without a
            # flushing statement THIR cannot hoist it, and falling through
            # would let a pass-through arm render the bare (un-copied) read
            # -- reject honestly instead.
            note_detail("call.own_str_no_flush")
            raise ThirUnsupported(call_reject_reason("expr.call"))
    ro_cont = _readonly_container_rvalue_arg(a, ptype, lc.analyzer)
    if ro_cont is not None:
        # An empty container rvalue at a readonly slot binds INLINE: the
        # `[]` literal takes the typed empty spelling
        # (`std::vector<int32_t>{}`), the hint-typed `list()` renders
        # through the instantiation arm (`std::vector<int32_t>()`).
        _witness("arg.readonly_empty_container")
        if isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            lit = _lower_expr(a, lc, declared,
                              use=_ExprUse(slot_target=ro_cont))
            if (isinstance(lit, THIRContainerLiteral)
                    and lit.typed_brace_cpp is None):
                lit = replace(lit, typed_brace_cpp=lc.render_type(ro_cont))
            return lit
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
    po_opt = _opt_own_ptr_opt_name_arg(a, ptype, declared, lc.analyzer)
    if po_opt is not None and _is_move_source(a, lc):
        # A ptr-repr Optional NAME at the by-value `std::optional<T>` slot:
        # rebuild owning storage with the move lift
        # (`::tpy::ptr_to_optional_move(tmp)`).
        _witness("move.opt_own_ptr_lift")
        return THIRFormConvert(
            result_type=po_opt,
            value=_lower_expr(a, lc, declared, allow_unrouted_name=True),
            form=Form.STORAGE, move=True, loc=getattr(a, "loc", None))
    ow_opt = (_opt_own_record_name_arg(a, ptype, declared, lc.analyzer)
              or _opt_own_container_name_arg(a, ptype, declared, lc.analyzer))
    if ow_opt is not None:
        # A same-nominal record NAME into an `Optional[Own[T]]` slot: the
        # movable last use renders `std::move(name)` BARE (the optional's
        # converting ctor absorbs the move); the copy shape is unwitnessed
        # and rejects. The name may arrive under the Own-lift coerce (an
        # inferred-targ generic call) -- peel it like the gate does.
        a_own = _peel_coerce(a)
        if not _is_move_source(a_own, lc):
            note_detail("call.opt_own_copy")
            raise ThirUnsupported(call_reject_reason("expr.call"))
        lowered = _lower_expr(a_own, lc, declared, use=_NESTED_ARG_USE,
                              allow_unrouted_name=True)
        _witness("move.opt_own_last_use")
        return THIRMove(result_type=ow_opt, value=lowered,
                        form=Form.STORAGE, loc=getattr(a_own, "loc", None))
    own_opt_slot = _own_storage_opt_param(ptype, lc.analyzer)
    if own_opt_slot is not None:
        at_oo = lc.analyzer.get_expr_type(a)
        at_oo = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at_oo)))
                 if at_oo is not None else None)
        if (isinstance(a, TpyName)
                and at_oo == unwrap_readonly(own_opt_slot.inner)):
            # A same-nominal record NAME at the `std::optional<P>&&` slot:
            # the movable last use renders `std::move(a)` BARE (the
            # optional's converting ctor absorbs the moved record); the
            # copy shape is unwitnessed and rejects.
            if not _is_move_source(a, lc):
                note_detail("call.own_opt_copy")
                raise ThirUnsupported(call_reject_reason("expr.call"))
            _witness("move.own_opt_last_use")
            return THIRMove(result_type=own_opt_slot,
                            value=_lower_expr(a, lc, declared,
                                              use=_NESTED_ARG_USE),
                            form=Form.STORAGE, loc=getattr(a, "loc", None))
        if (isinstance(a, (TpyCall, TpyMethodCall)) and at_oo == own_opt_slot
                and _own_declared_call_ret(a)):
            # An Own[P|None]-returning call rvalue: the `std::optional<P>`
            # prvalue binds the rvalue-ref slot bare (`take(make(13))`).
            _witness("arg.own_opt_call_pass")
            return _lower_expr(a, lc, declared,
                               use=_ExprUse(result=_ExprResultUse.STORAGE,
                                            allow_temps=temp_args),
                               allow_whole_optional=True)
    it_pb = _protocol_binding(ptype)
    if (it_pb is not None and it_pb.name in ("Iterable", "Sequence")
            and isinstance(a, TpyName)
            # CONTAINERHOOD first, like the sibling predicates: a name whose
            # C++ form is not itself a range (a pointer-repr Optional local,
            # narrowed or not) needs the indirection the generic tail carries,
            # and this arm's bare read would drop it.
            and _native_iterable_source(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(declared.get(a.name)))))):
        # A container NAME into an Iterable/Sequence slot. The consuming
        # arm is POSITION-BLIND (it runs for stub methods, plain user fns,
        # ctors and native callees alike), so the
        # wrap attempt is unconditional here: a movable LAST-USE name at an
        # `Own[...]`-element slot takes the consuming-__iter__ wrap
        # (`xs.extend(b)` / `collect(items)` ->
        # `::tpy::own_iter(std::move(b))`); every
        # other pairing -- a borrowing slot like `sum(xs)` (no Own type
        # arg; corpus-verified bare), a still-live name, a family with NO
        # consuming __iter__ (Array, Span) -- binds bare, because the wrap
        # keys those facts itself and returns None.
        wrapped = _consuming_iter_wrap(a, ptype, lc, declared)
        if wrapped is not None:
            return wrapped
        return _lower_expr(a, lc, declared)
    if (it_pb is not None and it_pb.name in ("Iterable", "Sequence")
            and isinstance(a, (TpyCall, TpyMethodCall))
            and _native_iterable_source(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(lc.analyzer.get_expr_type(a)))))):
        # A container-returning call rvalue bound bare by the Iterable slot
        # (`a.extend(copy(b))` / `a.extend(make_nodes())`): STORAGE use so
        # the inner call's result gate admits the container. `allow_temps`
        # rides through (the statement flush covers nested args).
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=temp_args))
    if (isinstance(a, TpyCall)
            and _own_container_instantiation_arg(a, ptype, lc.analyzer)):
        # The @dataclass default_factory fill (`Foo(std::vector<int32_t>(),
        # 1)`): the empty instantiation renders its spelled default ctor
        # into the Own slot's storage.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
    ow_slot = (_own_slot_payload(ptype)
               if isinstance(a, (TpyCall, TpyMethodCall)) else None)
    ow_method = isinstance(a, TpyMethodCall)
    if (ow_slot is not None and ow_method
            and is_dyn_protocol(unwrap_readonly(unwrap_send_sync(ow_slot)))
            and _own_dyn_method_rvalue_ok(a)):
        # An Own[@dynamic P] slot fed by an Own[P]-returning method-call
        # rvalue (`Box(e.clone())` -- the unique_ptr<P> prvalue binds the
        # slot bare); BORROW_BIND so the protocol-return result gate admits
        # the dyn payload (the same use the owned-record decl sink threads).
        # A concrete-conformer / async-factory return takes the make_adapter
        # wrap instead (`_own_dyn_method_rvalue_ok`, shared with the gate).
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    if ow_slot is not None and ow_method and _f1_record(ow_slot, lc.analyzer):
        # An Own-slot record METHOD-call rvalue (admitted by
        # _own_record_rvalue_arg -- `Arc.new(Mutex.new(0))`): the inline
        # rvalue binds the T&& slot; lower under BORROW_BIND like the
        # owned-record decl's method row, whose record-result gate this
        # position shares. This row is itself temp-free, so `nested_temps`
        # rides the statement flush into the rvalue's OWN args (a generator /
        # coro factory nested there hoists its temporary at the statement).
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args or nested_temps))
    if (ow_slot is not None
            and _storage_call_ret(
                unwrap_readonly(unwrap_send_sync(ow_slot)),
                lc.analyzer) is not None
            and (ow_method
                 # The FREE-call face of the same row
                 # (`table.append(make_row(1.0))`). Gate-keyed rather than
                 # shape-keyed so a free call some OTHER row claims at this
                 # slot keeps the render it has.
                 or _own_record_rvalue_arg(a, ptype, declared,
                                           lc.analyzer))):
        # The container sibling (`g.set(acked.copy())`): STORAGE use, the
        # container-returning method result's storage sink.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=temp_args))
    lift = _lower_union_arg_lift(a, ptype, lc, declared,
                                 readonly_target=readonly_target,
                                 temp_args=temp_args)
    if lift is not None:
        return lift
    # The container twin of the pointer-repr Optional faces below: only the
    # `nullptr` literal and the bare-container-name address-of are lowered
    # (`sum_list(&(data))` / `sum_list(nullptr)`); admission pinned the shape.
    # The container-LITERAL typed-temp face lives on the METHOD arg path
    # (`_method_arg`'s temp rows), the position that witnesses it.
    cont_ot = _optional_ptr_container_slot(ptype, lc.analyzer)
    if cont_ot is not None and _optional_ptr_container_arg(
            a, cont_ot, declared, lc.analyzer):
        loc = getattr(a, "loc", None)
        if isinstance(a, TpyNoneLiteral):
            _witness("optptr.none")
            return THIROptionalPtrArg(result_type=cont_ot, form=Form.BORROW,
                                      loc=loc)
        _witness("optptr.name")
        return THIROptionalPtrArg(result_type=cont_ot, form=Form.BORROW,
                                  value=_lower_expr(a, lc, declared,
                                                    use=_NESTED_ARG_USE),
                                  addr_of=True, loc=loc)
    if (isinstance(a, (TpyCall, TpyMethodCall))
            and not (isinstance(a, TpyCall)
                     and a.resolved_function_info is not None
                     and a.resolved_function_info.is_constructor)
            and _native_record_call_arg(a, ptype, lc.analyzer)):
        # An F1-record-returning call rvalue bound bare by a plain record
        # slot (`os.path.samestat(s, os.stat(d))`): BORROW_BIND so the
        # inner call's rvalue record result is admitted; only fires for
        # gate-admitted shapes (the tail would reject the record result).
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    if ((marker_arg or (method_arg and not method_arg_stub))
            and isinstance(a, (TpyCall, TpyMethodCall))
            and _borrow_ret_record_marker_arg(a, ptype, lc.analyzer)):
        # The BORROW-returning twin of the row above (`log_dispatch(
        # svc.get_logger(), ...)`, `loop.sock_recv(self._sock.get(), n)`):
        # the `T&` result binds the `T&` slot bare, so it needs the same
        # BORROW_BIND result use -- the plain method gate's `record_ret` is
        # off at VALUE / NESTED_ARG. The user-record method position is the
        # other family carrying this row; a stub receiver's family does not.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    # The pointer-repr Optional slot faces (must run BEFORE the pointer-local
    # deref retag: an already-pointer name passes BARE into the `T*` slot).
    # A narrowed subject is NOT skipped: its read renames to the extraction
    # alias inside _lower_expr and the 'name' face's `&(...)` wrap spells
    # `&(__u)` over it (see _optional_ptr_arg).
    if (isinstance(a, TpyName)
            and (a.name in lc.storage_opt_locals
                 # An `Own[P | None]` param (`std::optional<P>&&`) at a `T*`
                 # slot: the same storage-form lift, keyed on
                 # optional_locals.
                 or (a.name in lc.optional_locals
                     and _own_storage_opt_param(declared.get(a.name),
                                                lc.analyzer) is not None))
            and _optional_ptr_arg_slot(ptype, lc.analyzer) is not None):
        # A storage-optional unpack target at a `T*` slot lifts the bare
        # storage read (`borrow(::tpy::optional_to_ptr(p))`) -- the NAME
        # sibling of the field 'lift' face below.
        _witness("optptr.storage_name_lift")
        return THIROptionalPtrArg(
            result_type=_optional_ptr_arg_slot(ptype, lc.analyzer),
            form=Form.BORROW,
            value=_lower_expr(a, lc, declared),
            lift=True, loc=getattr(a, "loc", None))
    opt_face = _optional_ptr_arg_face(a, ptype, declared, lc.analyzer)
    if opt_face is not None:
        ot = _optional_ptr_arg_slot(ptype, lc.analyzer)
        loc = getattr(a, "loc", None)
        if opt_face == 'none':
            _witness("optptr.none")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW, loc=loc)
        if opt_face == 'subscript':
            # A record-element lvalue subscript takes the address-of face:
            # `&(::tpy::__getitem__(c, i))`. subscript_prechecked lets the
            # record-element borrow read through the value-position gate (a
            # bare record read is not a value leaf); the addr_of wrap is
            # the `&(...)` tail.
            _witness("optptr.subscript")
            return THIROptionalPtrArg(
                result_type=ot, form=Form.BORROW,
                value=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                                  subscript_prechecked=True),
                addr_of=True, loc=loc)
        if opt_face == 'container_temp':
            # A container LITERAL -- or a container-returning rvalue CALL --
            # at the Optional[container] slot: the spelled typed temp +
            # address-of lift
            # (`::tpy::ordered_map<...> __tmp_N = ...; f(&(__tmp_N));`).
            if not temp_args:
                raise ThirUnsupported(
                    "optional-ptr container face outside a flush position")
            inner_ct = unwrap_readonly(ot.inner)
            lit = _lower_expr(
                a, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             slot_target=inner_ct))
            if (isinstance(lit, THIRContainerLiteral)
                    and lit.typed_brace_cpp is None):
                # The temp init self-spells
                # (`std::vector<std::string>{"a"}` -- no decl target is
                # threaded here); dict/set renders already
                # self-describe, and the emit applies the spell only to a
                # bare brace render.
                lit = replace(lit, typed_brace_cpp=lc.render_type(inner_ct))
            _witness("optptr.container_temp")
            if isinstance(a, TpyCall):
                _witness("optptr.container_call_temp")
            return THIRArgTemp(result_type=inner_ct,
                               cpp_type=lc.render_type(inner_ct),
                               init=lit, addr_of=True,
                               movable=unwrap_ref_type(inner_ct).is_movable(),
                               form=Form.BORROW, loc=loc)
        if opt_face == 'scalar_temp':
            # A scalar RVALUE at the Optional[scalar] slot: the typed temp +
            # address-of lift (`int32_t __tmp_N = 99; c.set(&(__tmp_N));`) --
            # the ctor face's pointee sibling.
            if not temp_args:
                raise ThirUnsupported(
                    "optional-ptr scalar face outside a flush position")
            inner_s = unwrap_readonly(ot.inner)
            init_s = _lower_expr(
                a, lc, declared,
                use=replace(_NESTED_ARG_USE, slot_target=inner_s))
            _witness("optptr.scalar_temp")
            return THIRArgTemp(result_type=inner_s,
                               cpp_type=lc.render_type(inner_s),
                               init=init_s, addr_of=True,
                               movable=unwrap_ref_type(inner_s).is_movable(),
                               form=Form.BORROW, loc=loc)
        if opt_face == 'ctor':
            # Admitted only under temps_ok; a silent fall-through would render
            # the bare (un-addressed) ctor. A match guard admits calls but is
            # never a flush point, so reject here instead of asserting.
            if not temp_args:
                raise ThirUnsupported("optional-ptr ctor face outside a flush position")
            inner = unwrap_readonly(ot.inner)
            _witness("optptr.ctor_rvalue")
            if _non_ctor_call(a):
                _witness("optptr.record_call_temp")
            # A marker-call rvalue init is the temp's storage sink
            # (`auto __tmp_N = <call>;`), so its F1-record result is
            # admitted like any owned-record storage slot. The temp's SOURCE
            # ctor flushes its own arg temps at the same statement point
            # (validator-blessed nesting), so the enclosing flush right
            # rides into the init use.
            init_use = (_ExprUse(record_ctor=_RecordCtorUse.NESTED_ARG,
                                 result=_ExprResultUse.STORAGE,
                                 allow_temps=temp_args)
                        if isinstance(a, TpyMethodCall)
                        else _ExprUse(record_ctor=_RecordCtorUse.NESTED_ARG,
                                      allow_temps=temp_args))
            # A subclass ctor rvalue declares the CHILD's type; &(child)
            # binds the base pointer implicitly (the upcast temp).
            at_c = lc.analyzer.get_expr_type(a)
            at_c = unwrap_readonly(at_c) if at_c is not None else None
            tmp_t = (at_c if isinstance(at_c, NominalType) and at_c != inner
                     else inner)
            # Movable comes off the temp's own type, so the defer decision
            # inside a conditional region matches the temp it describes.
            return THIRArgTemp(result_type=tmp_t, cpp_type=tmp_t.to_cpp(),
                               init=_lower_expr(a, lc, declared, use=init_use),
                               addr_of=True,
                               movable=unwrap_ref_type(tmp_t).is_movable(),
                               form=Form.BORROW, loc=loc)
        if opt_face in ('adapter_rvalue', 'adapter_name'):
            # Structural conformer at an Optional[@dynamic P] slot: the
            # vtable rides an adapter temp -- Adapter<P, C> (owning, ctor
            # rvalue) / RefAdapter<P, C> (non-owning, name lvalue), brace
            # init, then `&(__tmp_N)` binds the nullable base pointer.
            if not temp_args:
                raise ThirUnsupported(
                    "optional-ptr adapter face outside a flush position")
            if isinstance(a, TpyName) and a.name in lc.pointers:
                raise ThirUnsupported("optional-ptr adapter pointer source")
            inner = unwrap_readonly(ot.inner)
            at_c = lc.analyzer.get_expr_type(a)
            at_c = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at_c)))
                    if at_c is not None else None)
            if not isinstance(at_c, NominalType):
                raise ThirUnsupported("optional-ptr adapter source type")
            concrete_cpp = lc.render_type(at_c)
            adapter_cpp = (
                dynamic_adapter_type(inner, concrete_cpp, lc.analyzer)
                if opt_face == 'adapter_rvalue'
                else dynamic_ref_adapter_type(inner, concrete_cpp,
                                              lc.analyzer))
            _witness("optptr.adapter_temp")
            return THIRArgTemp(
                result_type=inner, cpp_type=adapter_cpp, brace_init=True,
                init=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE),
                addr_of=True, form=Form.BORROW, loc=loc)
        if opt_face == 'lift':
            _witness("optptr.lift")
            _lift_src = _lower_field_source(a, lc, declared)
            if _lift_src.narrowed_deref:
                # A sema-NARROWED storage-Optional field lifts the RAW
                # member (`optional_to_ptr(t.o)` -- the read over the
                # declared storage), never the `(*t.o)` unwrap.
                _lift_src = replace(_lift_src, narrowed_deref=False)
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lift_src,
                                      lift=True, loc=loc)
        if opt_face == 'call_pass':
            # The borrow-returning call IS the `T*` the slot binds -- bare,
            # the Optional-arg pass-through. The statement flush rides
            # into the passed call's OWN args (its container-literal temp
            # lands at the pre-statement flush point like any nested call
            # arg's).
            _witness("optptr.call_pass")
            return _lower_expr(
                a, lc, declared,
                use=_ExprUse(pos=SinkPos.CALL_ARG,
                             forms=_call_arg_forms(whole_tuple=False,
                                                   coro_factory=False,
                                                   ptr_opt_pass=True),
                             record_ctor=_RecordCtorUse.NESTED_ARG,
                             allow_temps=temp_args or nested_temps))
        elif opt_face == 'pass' or (isinstance(a, TpyName)
                                    and a.name in lc.pointers):
            _witness("optptr.pass")
            # Already `T*` -- bare, no deref retag. RECEIVER is what SAYS
            # that: the slot binds the pointer itself, so the value-position
            # deref (which a pointer-slot GLOBAL takes at every other value
            # sink) must not fire here.
            return _lower_expr(
                a, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER,
                             record_ctor=_RecordCtorUse.NESTED_ARG))
        else:  # 'name': a plain record lvalue takes the address-of
            _witness("optptr.name")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lower_expr(
                                          a, lc, declared,
                                          use=_NESTED_ARG_USE),
                                      addr_of=True,
                                      loc=loc)
    if isinstance(a, TpyName) and _ptr_read_derefs(a.name, lc):
        return _lower_expr(a, lc, declared,
                           use=replace(_NESTED_ARG_USE,
                                       pos=SinkPos.CALL_ARG))
    if isinstance(a, TpyName) and a.name == lc.self_receiver:
        # `self` passed by reference reads the receiver pointer as a VALUE
        # (`on_init((*this))`) -- the deref the THIRSelf node already
        # carries; a resumable method's `__self` frame field is already a
        # `Record&` and reads bare. Kept as its own arm so the receiver
        # picks the nested-arg use rather than a name-shape ladder below.
        return _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
    # A float literal into a float32 (or Own[float32]) slot renders with the
    # `f` suffix, and an int literal into a FREE-call BigInt slot takes the
    # ctor wrap -- the param type threads into the render. A
    # METHOD arg's int literal stays BARE: the fixed-int arg conversion
    # emits IntLiterals as plain C++ integers
    # (`items.push_back(2)` -- BigInt's implicit int ctor absorbs it).
    # A union slot admits an assignment-narrowed union-declared name's bare
    # variant read (the `already_union` pass-through) -- the member-typed-sink
    # reject stays in force for a NON-union slot.
    slot_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype))) \
        if isinstance(ptype, TpyType) else None
    if (isinstance(slot_u, NominalType) and slot_u.is_protocol
            and not slot_u.is_dynamic_protocol
            and isinstance(a, TpyFieldAccess)
            and _alias_ref_container(lc.analyzer.get_expr_type(a))):
        # A container FIELD lvalue into a structural protocol slot
        # (`iter(self.items)` -> `::tpy::__iter__(this->items)`): the bare
        # member render rides the ITERABLE result family, like the
        # dedicated iterable-position arms thread it.
        return _lower_expr(a, lc, declared,
                           use=replace(_NESTED_ARG_USE,
                                       result=_ExprResultUse.ITERABLE))
    if (isinstance(a, TpyName) and a.name in lc.storage_opt_locals
            and isinstance(slot_u, NominalType) and slot_u.is_protocol
            and not isinstance(
                unwrap_readonly(lc.analyzer.get_expr_type(a)),
                OptionalType)):
        # A NARROWED storage-opt local at a protocol slot (`repr(item)`
        # under `item is not None`): the WHOLE `std::optional` passes
        # bare -- the runtime overload takes it directly
        # (the same whole-optional pass the field-arg row above spells).
        _witness("arg.storage_opt_whole")
        return THIRName(result_type=declared.get(a.name), name=a.name,
                        form=Form.STORAGE, loc=getattr(a, "loc", None))
    # An owned str-family field read passed bare into a value slot (`repr(
    # self.name)` -> `::tpy::repr_of(this->name)`) admits as STORAGE, matching
    # the fstring arg's own owned-str field read; admission already validated
    # the shape via `_shared_pass_through_arg` / `_protocol_slot_arg`.
    # `allow_temps` rides into a call-shaped arg (nested arg temps flush at
    # the enclosing statement); inert for every non-call shape.
    # `nested_temps` grants the same ride when the arg's OWN rows are
    # temp-free (a method arg's record rvalue renders inline, but its nested
    # ctor args still flush their temps at the statement).
    lowered = _lower_expr(a, lc, declared,
                          use=replace(_NESTED_ARG_USE,
                                      allow_temps=temp_args or nested_temps,
                                      # The param threads into a
                                      # FREE-call/ctor arg render, so a
                                      # both-literal binop arg never folds
                                      # there; fixed-int slots only (the
                                      # slot retype rebuilds the template).
                                      slot_target=(
                                          slot_u
                                          if isinstance(a, TpyBinOp)
                                          and not method_arg
                                          and is_fixed_int_type(slot_u)
                                          else None)),
                          field_owned_str_ok=isinstance(a, TpyFieldAccess),
                          allow_union_divergent=(
                              union_divergent_ok
                              or isinstance(slot_u, UnionType)))
    if (method_arg and isinstance(lowered, THIRLiteral)
            and isinstance(lowered.value, (int, float))
            and not isinstance(lowered.value, bool)):
        # A USER-RECORD method arg renders numeric literals target-less
        # (that loop passes target_type=None -- `c.bump(5)` into a
        # BigInt param stays bare). A builtin-stub member threads the RAW
        # param through its `_args()` loop: an Own-wrapped slot hint
        # renders bare (the hint is never Own-unwrapped -- `s.insert(7)`,
        # `xs.push_back(2)`), a plain BigInt/float32 slot takes the
        # target-typed render (`s.erase(::tpy::BigInt(3))`).
        if method_arg_stub and isinstance(ptype, TpyType):
            st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if not isinstance(st, OwnType):
                return _slot_literal_retype(lowered, ptype, lc)
        return lowered
    if isinstance(slot_u, OwnType) and isinstance(lowered, THIRLiteral):
        # The RAW ptype threads into the literal render, and
        # every target predicate (is_big_int_type / is_float32_type) is
        # False on the Own wrapper -- an `Own[int]` slot's literal renders
        # BARE (`heappush<::tpy::BigInt>(h4, 42)`), never ctor-wrapped.
        return lowered
    return _slot_literal_retype(lowered, ptype, lc)

def _ctor_deep_const_params(fi) -> 'frozenset[int]':
    """The ctor's deep-const parameter indices, or an empty set when the
    callee carries no verdict (a TypedDict ctor has no FunctionInfo, an
    un-analyzed one no Phase-2 facts). The free-call loops read
    `fi.root.const_borrow_params`; a ctor FunctionInfo is its own root,
    so the same fact is read one hop shorter."""
    if fi is None:
        return frozenset()
    root = getattr(fi, "root", None) or fi
    return frozenset(getattr(root, "const_borrow_params", None) or ())

def _lower_ctor_call_args(args: list[TpyExpr], fi, lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          temp_args: bool) -> list[THIRExpr]:
    """Lower a resolved user-record ctor's args through the shared per-arg
    dispatch, used by both the
    `X(args)` construction path and the `raise X(args)` statement. `temp_args`
    is set at flush positions (statement level), admitting the mutated-slot /
    Own-copy / union-lift / protocol temp rows. Raises `ThirUnsupported` for an
    arg shape not lowered yet (the caller tags the reject)."""
    analyzer = lc.analyzer
    use = _ExprUse(allow_temps=temp_args)
    ctor_mut = fi.mutated_params or frozenset()
    dcbp = _ctor_deep_const_params(fi)
    lowered: list[THIRExpr] = []
    for i, (a, p) in enumerate(zip(args, fi.params)):
        if not _record_ctor_arg_supported(a, p.type, i, fi, lc, declared, use):
            note_detail("ctor.arg." + _type_family_tag(p.type, analyzer))
            raise ThirUnsupported("ctor arg shape")
        rec = (_record_rvalue_temp_slot(a, p.type, analyzer)
               if i in ctor_mut else None)
        if rec is not None:
            if not temp_args:
                raise ThirUnsupported(
                    "ctor mutated-slot rvalue temp outside a flush position")
            _witness("argtemp.ctor_mut_rvalue")
            lowered.append(THIRArgTemp(
                result_type=rec, cpp_type=rec.to_cpp(), movable=unwrap_ref_type(rec).is_movable(),
                init=_lower_expr(a, lc, declared), form=Form.BORROW,
                loc=getattr(a, "loc", None)))
        else:
            flush_slot = (
                _own_lvalue_temp_slot(a, p.type, analyzer, declared,
                                      lc.prescan.param_names) is not None
                or _union_ctor_temp_arg(a, p.type, analyzer)
                or _protocol_arg_slot(p.type) is not None
                # The optional-ptr 'ctor' face's ArgTemp, gate-admitted
                # under temps_ok (see the construction-site twin).
                or _optional_ptr_arg_face(
                    a, p.type, declared, analyzer) == 'ctor')
            # `nested_temps` rides the statement flush into a call-shaped
            # arg's OWN args (`Holder(wrap(int32(42)))` -- the inner generic
            # call's scalar ref-slot temp lands at the pre-statement
            # flush); the ctor arg's own temp rows stay flush_slot-gated.
            lowered.append(_lower_call_arg(
                a, p.type, lc, declared,
                temp_args=temp_args and flush_slot,
                nested_temps=temp_args,
                protocol_slots=True,
                readonly_target=i in dcbp))
    return lowered

def _lower_union_arg_lift(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          readonly_target: bool = False,
                          temp_args: bool = False,
                          ) -> 'THIRUnionArgLift | None':
    """The pointer-variant union-slot arg lift, or None when the arg renders
    bare. The dispatch over the gate-admitted shapes:
    a None literal is the monostate member; a member-typed ctor RVALUE hoists
    a named temp and lifts its address (`pv{&__tmp_N}`, the rvalue branch --
    flush positions only); a member-typed name lifts
    `pv{&(name)}` with the indirect deref for a pointer-local / `self`
    receiver (`&((*p))` / `&((*this))`, the deref render); an
    already-union name into a DEEP-CONST slot (a `readonly[...]` annotation
    or `readonly_target`, the threaded `const_borrow_params` verdict)
    takes the const conversion its binding calls for -- and a deep-const slot
    spells the const-pointee variant throughout. A narrowed subject's C++
    binding is the member-typed extraction alias (`auto& __v =
    *std::get<A*>(v);`), so it takes the member lift its monomorphic twin
    takes -- the same fact the RETURN position already decided
    (`ret.narrowed_union_addr`). Only an ASSIGN-narrowed name is still the
    variant in C++, and `already_union` keys on the declared type, which stays
    the union exactly there; a same-union name into a MUTABLE slot renders
    bare the same way."""
    bl = _union_bytes_literal_temp_arg(a, ptype, lc.analyzer)
    if bl is not None:
        # The bytes-literal rvalue at a beyond-the-slice union slot: its own
        # slot check (see the predicate), the same hoist+addr render as the
        # ctor-temp row. The slot's const-ness picks the variant spelling;
        # the address of the hoisted temp converts to a const pointee.
        if not temp_args:
            raise ThirUnsupported(
                "union ctor arg-temp outside a flush position")
        blt = unwrap_ref_type(lc.analyzer.get_expr_type(a))
        _witness("unionlift.bytes_literal_temp")
        return THIRUnionArgLift(
            result_type=bl,
            variant_cpp=(bl.to_cpp_const_ptr_variant() if readonly_target
                         else bl.to_cpp_ptr_variant()),
            value=_lower_expr(a, lc, declared, use=_RECORD_TEMP_USE),
            temp_cpp=blt.to_cpp(),
            form=Form.BORROW, loc=getattr(a, "loc", None))
    dl = _union_dict_literal_temp_arg(a, ptype, lc.analyzer)
    if dl is not None:
        # The dict-literal sibling of the bytes row: the self-describing
        # literal render inits the typed temp, the variant lifts its
        # address (`::tpy::ordered_map<..> __tmp_N = ..;` + `pv{&__tmp_N}`).
        if not temp_args:
            raise ThirUnsupported(
                "union ctor arg-temp outside a flush position")
        dlt = unwrap_ref_type(lc.analyzer.get_expr_type(a))
        _witness("unionlift.dict_literal_temp")
        return THIRUnionArgLift(
            result_type=dl,
            variant_cpp=(dl.to_cpp_const_ptr_variant() if readonly_target
                         else dl.to_cpp_ptr_variant()),
            value=_lower_expr(a, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.STORAGE)),
            temp_cpp=dlt.to_cpp(),
            form=Form.BORROW, loc=getattr(a, "loc", None))
    slot = _arg_ptr_union_slot(ptype, lc.analyzer, readonly_target=readonly_target)
    if slot is None:
        return None
    ut, deep_const = slot
    variant_cpp = (ut.to_cpp_const_ptr_variant() if deep_const
                   else ut.to_cpp_ptr_variant())
    loc = getattr(a, "loc", None)
    if isinstance(a, TpyNoneLiteral):
        _witness("unionlift.none")
        return THIRUnionArgLift(result_type=ut, variant_cpp=variant_cpp,
                                form=Form.BORROW, loc=loc)
    if _union_ctor_temp_arg(a, ptype, lc.analyzer):
        # The union arg's rvalue branch: the member ctor hoists a named
        # temp, the variant lifts its address. Admitted only under a flush
        # position; a match guard admits calls but is never a flush point,
        # so reject here instead of falling through to a bare render.
        if not temp_args:
            raise ThirUnsupported(
                "union ctor arg-temp outside a flush position")
        at = unwrap_ref_type(lc.analyzer.get_expr_type(a))
        _witness("unionlift.ctor_temp")
        return THIRUnionArgLift(
            result_type=ut, variant_cpp=variant_cpp,
            # A MEMBER ctor of the union member (`gen(A(Inner(5)))`) hoists
            # its own temp at the same statement flush point, innermost-first
            # -- the record-temp row's flush use. `temp_args` alone does not
            # establish that point: a conditional OPERAND still carries it
            # while the placement there must defer, which the
            # conditional-operand exit check rejects for this row.
            value=_lower_expr(a, lc, declared, use=_RECORD_TEMP_FLUSH_USE),
            temp_cpp=at.to_cpp(),
            form=Form.BORROW, loc=loc)
    if not isinstance(a, TpyName):
        return None
    # The `already_union` verdict keys on the C++ DECLARED type,
    # not the (assignment-narrowed) read type: a union-declared name whose read
    # type sema retyped to a member (`x: A | B = A(); f(x)`) is still the variant
    # in C++, so it renders bare into a mutable slot (or a const conversion
    # for a deep-const slot). Only a genuinely member-TYPED name lifts.
    decl = declared.get(a.name)
    decl = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(decl)))
            if decl is not None else None)
    if isinstance(decl, UnionType):
        wrap = (_ptr_union_const_wrap(a.name, decl, ut, declared, lc)
                if deep_const else None)
        if wrap is not None:
            _witness("unionlift.const_wrap")
            return THIRUnionArgLift(
                result_type=ut, variant_cpp=variant_cpp,
                value=_lower_expr(a, lc, declared, allow_union_divergent=True),
                const_wrap=wrap, form=Form.BORROW, loc=loc)
        return None  # bare render at the call-arg tail
    at = lc.analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not _union_member_match(at, ut.members, lc.analyzer):
        return None
    _witness("unionlift.member")
    return THIRUnionArgLift(
        result_type=ut, variant_cpp=variant_cpp,
        value=_lower_expr(a, lc, declared),
        # The receiver's own deref rides the THIRSelf node, so the lift must
        # not stack a second one.
        deref=_ptr_read_derefs(a.name, lc),
        form=Form.BORROW, loc=loc)

def _flush_witness(pos: str, value: THIRExpr) -> THIRExpr:
    """Witness a flushable statement position whose lowered value actually
    hoists an arg temp (`__tmp_N` decls land at this statement's flush
    point). Temps only ever sit in the DIRECT args of the position's call
    (temp_args never propagates into subexpressions), possibly behind a
    coerce/form-convert wrapper. Identity on `value` -- instrumentation only."""
    v = value
    while isinstance(v, (THIRCoerce, THIRFormConvert)):
        v = v.expr if isinstance(v, THIRCoerce) else v.value
    if (isinstance(v, (THIRCall, THIRMethodCall))
            and any(isinstance(x, THIRArgTemp) for x in v.args)):
        _witness(pos)
    return value

def _retag_bytes_literal_span(value: THIRExpr) -> THIRExpr:
    """Rewrite a bytes literal to its static-storage span render (BORROW) --
    the caller has already decided the sink is view-shaped, so this only
    carries the verdict onto the node."""
    if isinstance(value, THIRBytesLiteral):
        return replace(value, form=Form.BORROW)
    return value

def _retag_bytes_literal_view(value: THIRExpr, target: 'TpyType | None') -> THIRExpr:
    """`_retag_bytes_literal_span` gated on an explicitly VIEW-typed sink (a
    view-resolved binding / a BytesView return / a BytesView container key) --
    the target threads into the bytes-literal render. An owned `bytes` sink is
    deliberately NOT this shape: it stores the vector."""
    if is_bytes_view_type(target):
        return _retag_bytes_literal_span(value)
    return value

def _narrowed_opt_char_vs_str_literal(
        subj: TpyExpr, other: TpyExpr, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> bool:
    """A NARROWED value-repr Optional[char] operand compared against a str
    literal. The comparison targets key the char-literal coercion on the
    RESOLVED (un-narrowed) operand type, so the literal is never charred
    here and the render is the non-compiling `(*o) == "a"` (see BUGS.md);
    the narrowed read would type the slot char and silently emit the fixed
    `(*o) == 'a'` instead. Reject until that defect is fixed."""
    if not isinstance(other, TpyStrLiteral):
        return False
    if not _eligible_char(lc.analyzer.get_expr_type(subj)):
        return False
    if isinstance(subj, TpyName):
        decl = declared.get(subj.name)
    elif (isinstance(subj, TpyFieldAccess)
          and isinstance(subj.obj, TpyName)):
        decl = _field_decl_type(subj, declared, lc.analyzer)
    else:
        return False
    du = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(decl)))
          if decl is not None else None)
    return (isinstance(du, OptionalType) and not du.uses_pointer_repr()
            and _eligible_char(du.inner))

def _lower_float_str_fold(fi, args, callee: str, rtype: 'TpyType | None',
                          loc, *, kwargs: bool) -> 'THIRCall | None':
    """The `float("nan"/"inf"/...)` constexpr numeric-limits fold, shared by
    the free-call and module-qualified spellings (one fold at both entry
    points, so the twin arms cannot diverge by position). Returns None when
    the call is not
    the recognized builtins.float(str-literal) shape."""
    if (fi is None or fi.owning_type_qname != "builtins.float"
            or len(args) != 1 or kwargs
            or not isinstance(args[0], TpyStrLiteral)):
        return None
    fold_cpp = emit_prims.FLOAT_STR_CONSTANTS.get(args[0].value.strip().lower())
    if fold_cpp is None:
        return None
    _witness("call.float_str_fold")
    return THIRCall(result_type=rtype, callee=callee, args=(),
                    cpp_template=fold_cpp, loc=loc)


def _lower_copy_special(src: TpyExpr, callee: str, rtype: 'TpyType | None',
                        lc: '_LowerCtx', declared: dict[str, TpyType],
                        loc) -> THIRExpr:
    """The `copy(x)` special builtin, shared by the free-call
    (`copy(s)`) and module-qualified (`t.copy(s)`) spellings -- the
    `{arg_type.to_cpp()}(<deref read>)` general tail, sliced per
    source family. NAME/FIELD sources only for the lvalue arms, un-narrowed,
    non-pointer; concrete record copies take their own rows."""
    analyzer = lc.analyzer
    st = analyzer.get_expr_type(src)
    # A literal-seeded container reads as its pending type until sema's
    # resolution pass; the copy spells the RESOLVED container. A str/bytes
    # local (a loop variable over a literal list, say) carries the view
    # family's pending type for the same reason and needs the same
    # resolution -- `to_cpp()` on an unresolved pending type has no spelling.
    st = (resolve_pending_container(st, analyzer)
          or _resolve_pending_view(st, analyzer) or st)
    def _open_t_copy(face: str, use: '_ExprUse | None' = None) -> THIRCall:
        """The type-blind `U(<read>)` tail every open-T copy source shares.

        The payload is an open type param, so the render cannot depend on
        which source shape produced the read -- only on the substituted
        spelling. Keeping the tail in one place is what stops the source
        arms from drifting apart while claiming to emit the same thing."""
        stu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
        _witness(face)
        args = ((_lower_expr(src, lc, declared),) if use is None
                else (_lower_expr(src, lc, declared, use=use),))
        return THIRCall(result_type=rtype, callee=callee, args=args,
                        cpp_template=f"{stu.to_cpp()}({{0}})", loc=loc)

    if (_tparam_value(st)
            and isinstance(src, (TpyName, TpyFieldAccess))
            and not (isinstance(src, TpyName)
                     and src.name in lc.narrow.narrowed)):
        # A POINTER-FORM name (a resumable frame's loop var, say) is the same
        # source one indirection down, and the general tail's name read
        # already spells it (`T((*x))`). The concrete families need their own
        # `copy_*_ptr` rows only because a concrete record/container name
        # reads BARE and has to ask for the deref; an open `T` is spelled
        # through the `val_or_ptr_t<T>` traits, never as a bare `T*`.
        return _open_t_copy(
            "call.copy_tparam_ptr"
            if isinstance(src, TpyName) and src.name in lc.pointers
            else "call.copy_tparam")
    if (_tparam_value(st)
            and isinstance(src, (TpyCall, TpyMethodCall))):
        # The inner call lowers through its own arms -- an Fn-param
        # invocation renders itself. A BORROW-returning callee hands back an
        # alias, not a value, so it takes the lvalue arms' bind (the record
        # arm's tail splits the same way); the copy-construct tail is the
        # same either way, since it reads whatever spelling the callee's
        # return convention produced.
        return _open_t_copy("call.copy_tparam",
                            use=(_NESTED_ARG_USE
                                 if is_rvalue_source(analyzer, src)
                                 else _COPY_SRC_USE))
    if _tparam_value(st) and isinstance(src, TpySubscript):
        # The general tail's deref is a no-op whatever the receiver: an
        # open-T element is spelled through the `val_or_ptr_t<T>` traits,
        # never as a bare `T*`, so no subscript receiver reaches this row
        # already dereferenced.
        return _open_t_copy("call.copy_tparam_elem")
    stu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
    # The BINDING type, not the read type: an assign-narrowed ptr-variant
    # local reads as its member record, but the copy is still the whole
    # variant's (keyed on ptr_variant_locals + the binding).
    _cb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        resolve_pending_container(declared[src.name], analyzer)
        or declared[src.name])))
           if isinstance(src, TpyName) and src.name in declared else None)
    if (isinstance(src, TpyName) and src.name in lc.narrow.narrowed
            and lc.narrow.subject_union.get(src.name) is not None):
        # Inside a narrowing branch `declared` is retyped to the member;
        # the copy is binding-keyed, so read the ORIGINAL union from the
        # branch machinery's subject_union store.
        _cb = lc.narrow.subject_union[src.name]
    if (isinstance(src, TpyName) and src.name in lc.ptr_variant_locals
            and isinstance(_cb, UnionType)
            and src.name not in lc.inline_narrowed):
        stu = _cb
        # `copy(pet)` of a ptr-variant union binding: the active-member
        # deep copy is the `to_value_variant<VT>(name)` conversion
        # (the ptr-variant copy arm), i.e. the STORAGE FormConvert
        # render on the bare name. The copy consumes the WHOLE variant, so
        # an assign-narrowed read type is immaterial (the divergent fence's
        # member-typed-sink miscompile cannot arise -- the union field
        # sink's argument). An ISINSTANCE-narrowed name reads the ORIGINAL
        # binding too (the copy keys on ptr_variant_locals, never the
        # alias) -- construct the bare name past the rename.
        if src.name in lc.narrow.narrowed:
            _cval: THIRExpr = THIRName(result_type=stu, name=src.name,
                                       form=Form.BORROW, loc=loc)
        else:
            _cval = _lower_expr(src, lc, declared,
                                allow_union_divergent=True)
        _witness("call.copy_ptr_variant")
        return THIRFormConvert(result_type=stu, value=_cval,
                               form=Form.STORAGE, move=False, loc=loc)
    if (isinstance(stu, TupleType) and stu.has_pointer_repr_element()
            and isinstance(src, TpyTupleLiteral)
            and len(src.elements) == len(stu.element_types)):
        # `copy((1, b))` of a WHOLE tuple with a reference element: the
        # STORAGE form builds directly, each element straight into its
        # value slot (`std::tuple<int32_t, Box>{1, b}`), so a reference
        # element COPY-constructs. Lowering the elements through the
        # container-element row instead would MOVE a last-use name here,
        # inverting the copy the construct exists for.
        _copy_elems = tuple(
            _slot_literal_retype(
                _lower_expr(_el, lc, declared,
                            use=_ExprUse(slot_target=_et)), _et, lc)
            for _el, _et in zip(src.elements, stu.element_types))
        _witness("call.copy_tuple_storage")
        return THIRBorrowTupleLiteral(
            result_type=stu,
            spelled_cpp=_resolve_tuple_pending(stu, analyzer).to_cpp(),
            elements=_copy_elems,
            addr_of=tuple(False for _ in _copy_elems),
            elem_wraps=(), loc=loc)
    # `copy(x)` of a concrete container source (`copy(d.get(k, dflt))`):
    # the general `{arg_type.to_cpp()}(<deref read>)` tail
    # -> `std::vector<T>(<src>)`. Containers are never pointer-locals, so
    # the deref is a no-op; a pointer-local / narrowed NAME source is
    # excluded defensively, matching the tparam arm's guard.
    if (is_span(stu) and isinstance(src, TpyName)
            and src.name not in lc.pointers
            and src.name not in lc.narrow.narrowed):
        # `copy(span)` -> `std::span<int32_t>(span)`: the same
        # general tail; a span copy is a VIEW copy (aliasing
        # preserved by construction).
        _witness("call.copy_span")
        return THIRCall(
            result_type=rtype, callee=callee,
            args=(_lower_expr(src, lc, declared),),
            cpp_template=f"{stu.to_cpp()}({{0}})",
            loc=loc)
    _copy_ptr_ok = False
    _copy_rec_ptr_ok = False
    if (isinstance(src, TpyName) and src.name in lc.pointers
            and src.name not in lc.narrow.narrowed):
        # An OPT_PTR container pointer-local source (`copy(acc)` on the
        # argparse accumulator slot): the name read derefs (`V((*acc))`,
        # the indirect render) -- exactly the general tail.
        _cp_dt = declared.get(src.name)
        _cp_du = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(_cp_dt)))
                  if _cp_dt is not None else None)
        if (isinstance(_cp_du, OptionalType)
                and _cp_du.uses_pointer_repr()):
            _cp_du = unwrap_readonly(_cp_du.inner)
        # Both pointer-local container flavors deref the same way: the
        # OPT_PTR slot (declared Optional[container]) and the REBIND
        # slot (declared plain container, the argparse accumulator).
        _copy_ptr_ok = _f1_container_ref(_cp_du)
        _copy_rec_ptr_ok = _f1_record(_cp_du, analyzer)
    # The copy-CONSTRUCT tail over either reference family: the source's
    # value form carries the indirection, so one render serves an lvalue
    # (name / field / subscript / borrow-returning call / awaited borrow)
    # and a prvalue alike. A NARROWED name reads as its member and is not
    # this source; a pointer-local takes the deref rows beside this one.
    _cc_form = (copy_construct_form(src, lc.pointers, analyzer)
                and not (isinstance(src, TpyName)
                         and src.name in lc.narrow.narrowed))
    if _f1_container_ref(stu) and (_cc_form or _copy_ptr_ok):
        _witness("call.copy_container")
        # An RVALUE source materializes at the STORAGE sink the `T(...)`
        # construct is (its `_storage_call_ret` gate); every lvalue read --
        # a field, a borrow-returning call -- binds the copy ctor's
        # `const T&` under the BORROW_BIND copy-source use. A POINTER-LOCAL
        # is an lvalue whose read must still deref, and STORAGE is what
        # makes the container name arm spell `(*acc)`.
        return THIRCall(
            result_type=rtype, callee=callee,
            args=(_lower_expr(
                src, lc, declared,
                use=(_ExprUse(result=_ExprResultUse.STORAGE)
                     if _copy_ptr_ok or is_rvalue_source(analyzer, src)
                     else _COPY_SRC_USE)),),
            cpp_template=f"{stu.to_cpp()}({{0}})",
            loc=loc)
    if _f1_record(stu, analyzer) and (_cc_form or _copy_rec_ptr_ok):
        if _copy_rec_ptr_ok:
            # `copy(acc)` of a POINTER-LOCAL record source (the argparse
            # rebind-slot accumulator): the name read derefs, so the tail
            # spells `Tag((*acc))`. A record binding is bare at every other
            # use (the `->` rides the member arms), so only an explicit
            # deref position spells the indirection.
            _witness("call.copy_record_ptr")
            return THIRCall(
                result_type=rtype, callee=callee,
                args=(_lower_expr(src, lc, declared,
                                  use=_ExprUse(
                                      pos=SinkPos.CALL_ARG)),),
                cpp_template=f"{stu.to_cpp()}({{0}})",
                loc=loc)
        # The container arm's record twin, same tail: a bare record NAME is
        # `_lower_copy_record`'s `T(x)` row (every supporting sink
        # intercepts it first), so what lands here is the source shapes no
        # sink intercepts -- a field read, a borrow-returning call, an
        # awaited borrow.
        _witness("call.copy_record")
        return THIRCall(
            result_type=rtype, callee=callee,
            args=(_lower_expr(
                src, lc, declared,
                use=(_ExprUse(result=_ExprResultUse.STORAGE)
                     if is_rvalue_source(analyzer, src) else _COPY_SRC_USE)),),
            cpp_template=f"{stu.to_cpp()}({{0}})",
            loc=loc)
    _cp_str = _resolved_str_value(stu, analyzer)
    _cp_bytes = (None if _cp_str is not None
                 else _resolved_bytes_value(stu, analyzer))
    if ((_cp_str is not None or _cp_bytes is not None)
            and isinstance(src, TpyName)
            and src.name not in lc.pointers
            and src.name not in lc.narrow.narrowed):
        # `copy(s)` of a str/bytes NAME (`u = t.copy(s)` -> `std::string(s)`):
        # the same type-blind general tail over the source's RESOLVED
        # spelling. A str is immutable, so a VIEW source copies as a view
        # (`std::string_view(s)`) -- materializing it instead would leave the
        # same-typed sink holding a view into a dead temporary.
        _cp_res = _cp_str if _cp_str is not None else _cp_bytes
        _cp_tmpl = f"{_cp_res.to_cpp()}({{0}})"
        if (_cp_bytes is not None and not is_bytes_view_type(_cp_res)
                and _view_at_runtime(src, lc)):
            # A `bytes` PARAM is resolved OWNED but passed as the span, and
            # the owned type has no span ctor -- the family's own owning
            # conversion is the materializing tail here, spelled through the
            # registry's single source so it cannot drift from the sink
            # conversions. (`std::string(sv)` is why the str twin needs no
            # correction.)
            _cp_tmpl = f"{view_to_owned_conv(_cp_res)}({{0}})"
            _witness("call.copy_bytes_view_source")
        if _cp_bytes is not None or is_str_view_type(_cp_res):
            _witness("call.copy_viewfam")
        _witness("call.copy_str")
        return THIRCall(
            result_type=rtype, callee=callee,
            args=(_lower_expr(src, lc, declared),),
            cpp_template=_cp_tmpl,
            loc=loc)
    if (_eligible_scalar(stu) and isinstance(src, TpyName)
            and src.name not in lc.pointers
            and src.name not in lc.narrow.narrowed):
        # `copy(big)` of a scalar NAME (`::tpy::BigInt(big)`): the same
        # type-blind general tail -- scalars are value types, so the
        # copy-construct rvalue is the whole render.
        _witness("call.copy_scalar")
        return THIRCall(
            result_type=rtype, callee=callee,
            args=(_lower_expr(src, lc, declared),),
            cpp_template=f"{stu.to_cpp()}({{0}})",
            loc=loc)
    # The one copy() shape with no admitted row: name the copied payload's
    # family so the tally ranks the missing rows instead of one opaque tail.
    note_detail("call.copy_source." + _type_family_tag(stu, analyzer))
    raise ThirUnsupported(call_reject_reason("expr.call"))


def _lower_char_targeted(e: TpyExpr, target: TpyType | None,
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         use: _ExprUse = _ExprUse(),
                         cond_eager: bool = False,
                         allow_whole_optional: bool = False,
                         field_owned_str_ok: bool = False,
                         allow_union_divergent: bool = False) -> THIRExpr:
    """Lower an expression whose slot may be char-typed: a str literal in a
    char slot renders as a target-typed C++ char literal (`'x'`). Shared by
    the three positions that thread a char target into the render --
    comparison operands opposite a char-typed value, char-annotated decl
    inits, and call args into char param slots. The gates admitted the
    literal only single-char; the other comparison-target arms (Optional
    narrowing) cannot arise -- Optional operands are gated out of the
    slice."""
    if isinstance(e, TpyStrLiteral) and _eligible_char(target):
        return THIRCharLiteral(result_type=CHAR, value=e.value,
                               loc=getattr(e, "loc", None))
    return _lower_expr(e, lc, declared,
                       use=replace(use, slot_target=target),
                       cond_eager=cond_eager,
                       allow_whole_optional=allow_whole_optional,
                       field_owned_str_ok=field_owned_str_ok,
                       allow_union_divergent=allow_union_divergent)

def _cond_mixed_walrus_temps(cond: THIRExpr, *,
                             walrus_nested_ok: bool = False) -> bool:
    """True when a lowered condition carries BOTH a walrus binding and a
    hoisted arg temp -- the shape both restructured-head renders exclude (an
    in-head temp could run before the walrus assignment it reads; that
    shape needs the legacy single-eval flush), so lowering rejects it.
    Every node kind that
    registers a pending temp at EMIT time counts: THIRArgTemp, a
    temp-bearing THIRUnionArgLift, and THIRVarargPack (its per-arg hoist).

    `walrus_nested_ok` (the single-eval `if` head and its resumable Branch
    twin): a temp nested INSIDE a walrus's own value evaluates before the
    assignment on both paths, so it does not make the shape mixed -- the
    walrus predecl and the temp flush together at the statement's flush
    point in decl order. Only the SYNC head ever meets that shape: a frame
    walrus lowers its value with no flush right, so a temp inside one
    rejects at the walrus before any condition is assembled. A while head
    never sets the flag: ANY walrus+temp mix there keeps the legacy
    pre-loop single-eval flush (a known residual)."""
    has_walrus = False
    has_temp = False

    def walk(n, inside_walrus: bool) -> None:
        nonlocal has_walrus, has_temp
        if isinstance(n, THIRWalrus):
            has_walrus = True
            if walrus_nested_ok:
                inside_walrus = True
        if not (inside_walrus and walrus_nested_ok) and (
                isinstance(n, (THIRArgTemp, THIRVarargPack)) or (
                    isinstance(n, THIRUnionArgLift)
                    and n.temp_cpp is not None)):
            has_temp = True
        for f in dataclass_fields(n):
            v = getattr(n, f.name)
            if isinstance(v, THIRNode):
                walk(v, inside_walrus)
            elif isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, THIRNode):
                        walk(item, inside_walrus)

    walk(cond, False)
    return has_walrus and has_temp


def _strip_slot_leaf_deref(lowered: 'THIRExpr', lc: '_LowerCtx') -> 'THIRExpr':
    """Un-deref a pointer-name leaf consumed by machinery that applies its
    OWN indirect wrap (the async-for iterable, with managers, erased await
    operands): those seams read the bare name and apply their own indirect
    wrap, so a name-arm `(*g)` would double-deref."""
    if (isinstance(lowered, THIRName) and lowered.name in lc.pointers
            and lowered.deref):
        return replace(lowered, deref=False)
    return lowered


def _lower_truthy(e: TpyExpr, lc: '_LowerCtx',
                  declared: dict[str, TpyType], *,
                  unary_operand: bool = False,
                  temps_ok: bool = False) -> THIRExpr:
    """Lower one Python-truthiness position. `temps_ok` marks the cond
    positions whose emit places condition temps (the restructured while
    head, the pre-`if` flush, the nested-elif block); logical-op
    OPERANDS never thread it (a short-circuit RHS temp would hoist
    eagerly)."""
    if isinstance(e, (TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
        # A NUMERIC literal condition (`if 1:`) reaches the scalar arm below
        # and renders bare, like any other scalar. The str/bytes/None
        # literals keep rejecting: their render in this position is
        # unverified, and `None` has no boolean spelling at all.
        raise ThirUnsupported("truthy.literal")
    if isinstance(e, TpyName) and e.name in lc.narrow.any_narrowed:
        # A narrowed-Any subject read in truthy position: truthiness keys
        # on the DECLARED Any (`::tpy::to_bool(alias)`) even though sema
        # retyped the occurrence -- the declared-type dispatch, the truthy
        # sibling of the print-arg RAW read.
        return THIRTruthy(
            result_type=BOOL, mode=TruthinessMode.TO_BOOL,
            operand=_lower_expr(e, lc, declared,
                                use=_ExprUse(result=_ExprResultUse.TRUTHY)),
            deref=False, loc=getattr(e, "loc", None))
    et = lc.analyzer.get_expr_type(e)
    if isinstance(e, TpyBinOp) and e.op in _LOGICAL_OPS:
        return THIRBinOp(
            result_type=BOOL,
            left=_lower_truthy(e.left, lc, declared),
            op=e.op,
            right=_lower_truthy(e.right, lc, declared),
            resolved=None,
            loc=getattr(e, "loc", None),
        )
    wrap = _enum_truthy_wrap(et, lc.analyzer)
    mode = _truthiness_mode(et, lc.analyzer)
    if (mode is TruthinessMode.IS_TRUTHY
            and isinstance(e, TpyFieldAccess)):
        # A truthy Optional FIELD condition renders `::tpy::is_truthy(f)`
        # over the raw declared storage. The branch's narrowed reads need
        # NO THIR-side path fact: sema keys dotted-path narrows itself and
        # retypes each occurrence, which the stateless field-read arm
        # (`_narrowed_opt_field_read`, declared-vs-analyzed mismatch)
        # already consumes -- the same regime as the is-not-None field
        # subject.
        if (_field_markers_clean(e)
                and (( isinstance(e.obj, TpyName)
                       and _field_receiver_ok(e, declared, lc.analyzer))
                     # The one-link CHAIN flavor (`if o.inner.value:`):
                     # the same raw-storage is_truthy over the bare chain
                     # read; the intermediate link must be a plain
                     # DECLARED record (the None-subject chain rule).
                     or (isinstance(e.obj, TpyFieldAccess)
                         and isinstance(e.obj.obj, TpyName)
                         and _field_markers_clean(e.obj)
                         and _field_receiver_ok(e.obj, declared, lc.analyzer)
                         and _plain_record_field_link(e.obj, declared,
                                                      lc.analyzer))
                     # An ELEMENT receiver (`if hs[0].opt:`): the raw
                     # storage read again, over the checked element lvalue.
                     # The receiver is not prechecked here -- the subscript
                     # arm owns its own bounds/shape decision.
                     or isinstance(e.obj, TpySubscript))):
            operand = _lower_expr(
                e, lc, declared,
                field_prechecked=not isinstance(e.obj, TpySubscript),
                allow_whole_optional=True)
            _witness("truthy.optional_field_whole")
            return THIRTruthy(
                result_type=BOOL, mode=TruthinessMode.IS_TRUTHY,
                operand=operand, deref=False,
                loc=getattr(e, "loc", None))
        raise ThirUnsupported("truthy.optional_field_narrow")
    if _ptr_truthy_source(e, et, lc.analyzer, lc.storage_opt_locals):
        # Un-narrowed pointer-repr Optional[record]: `x is not None and
        # bool(x)` in one evaluation. Only the two source shapes with a
        # verified render admit -- anything else must keep rejecting here
        # rather than reach the ladder, whose bare render would drop the
        # dunder this position dispatches.
        if not isinstance(e, (TpyName, TpyCall)):
            raise ThirUnsupported("truthy.ptr_record_shape")
        operand = _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY, allow_temps=temps_ok))
        _witness("truthy.ptr_truthy")
        return THIRTruthy(
            result_type=BOOL, mode=TruthinessMode.PTR_TRUTHY,
            operand=operand, deref=False, loc=getattr(e, "loc", None))
    if isinstance(e, TpyName) and e.name in declared:
        du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[e.name])))
        eu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
              if et is not None else None)
        if (isinstance(du, OptionalType) and not isinstance(eu, OptionalType)
                and not du.uses_pointer_repr()):
            # A value-repr narrowed Optional name renders through
            # ::tpy::is_truthy on the WHOLE optional -- keyed on
            # the C++ BINDING, not the narrow, so a registered/param
            # value-opt binding takes the deref-stripped whole read.
            # Pointer-repr narrowed record optionals dispatch the narrowed
            # inner's __bool__/__len__ instead (the record-mode arms below).
            if _value_opt_binding_kind(e.name, lc) is not None:
                operand = replace(
                    _lower_expr(e, lc, declared, allow_whole_optional=True),
                    deref=False)
                _witness("truthy.value_opt_whole")
                return THIRTruthy(
                    result_type=BOOL, mode=TruthinessMode.IS_TRUTHY,
                    operand=operand, deref=False,
                    loc=getattr(e, "loc", None))
            raise ThirUnsupported("truthy.optional_name_narrow")
    storage_opt_bare = _storage_opt_record_truthy(
        e, et, lc.analyzer, lc.storage_opt_locals)
    if wrap is None:
        # The one question every modeless arm below asks: does an operand of
        # THIS type render its own boolean test? Computed once, so no arm can
        # answer it differently for its own syntactic shape.
        native_scalar = _native_cond_scalar(et, lc.analyzer)
        ptr_optional = (
            isinstance(e, TpyName) and isinstance(et, OptionalType)
            and _optional_ptr_borrow_name(
                e, declared, lc.analyzer) is not None)
        if ptr_optional:
            pass
        elif unary_operand:
            # A scalar operand truthy-tests via C++'s contextual conversion,
            # so `not x` renders the bare `(!(x))`. Other modeless operands
            # keep rejecting.
            if mode is None and not native_scalar:
                raise ThirUnsupported("truthy.unary_operand")
        elif isinstance(e, TpyBoolLiteral):
            pass
        elif isinstance(e, TpyName):
            unrouted = _unrouted_binding_read(
                declared.get(e.name), lc.analyzer)
            storage_optional = (
                mode is TruthinessMode.IS_TRUTHY
                and et is not None
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(et))), OptionalType))
            if (e.name not in declared or et is None
                    or (mode is None and not native_scalar)
                    or (unrouted is not None and not storage_optional)):
                raise ThirUnsupported("truthy.name")
        elif isinstance(e, TpyFieldAccess):
            if mode is None and not native_scalar:
                if not storage_opt_bare:
                    raise ThirUnsupported("truthy.field_nonbool")
                _witness("truthy.storage_opt_bare")
            elif mode is None:
                _witness("cond.bool_field")
        elif isinstance(e, TpyBinOp):
            # A comparison/logical/is/membership operand already yields bool.
            # Any OTHER operator yields a value whose truthiness is the
            # ordinary type dispatch -- bare for a scalar, the empty-test for
            # a str/bytes concat -- so the op set only has to carry the
            # already-bool ops, and the rest ride the type question.
            if (mode is None and not native_scalar
                    and e.op not in (_COMPARE_OPS | _LOGICAL_OPS
                                     | _IS_OPS | _MEMBERSHIP_OPS)):
                raise ThirUnsupported("truthy.binop")
        elif isinstance(e, TpyMethodCall):
            if mode is None and not native_scalar:
                raise ThirUnsupported("truthy.method_nonbool")
            if mode is None:
                _witness("cond.bool_method")
        elif isinstance(e, TpyCall):
            # The free-call twin of the bool method-call condition: `if f(x):`
            # renders the bare call (`if (f(x))`); a MODED record rvalue
            # (`if NegBig():` -- RECORD_LEN over the ctor prvalue) rides the
            # same THIRTruthy wrap as a name operand (the deref split below
            # keys on names only, and an rvalue is never a pointer-local).
            # isinstance calls are excluded: they belong to the narrowing
            # machinery (in-branch extraction aliases, statically-proven
            # `if (true)` folds), not the bare-call render. EXCEPT the
            # STATIC type-param family (`isinstance(x, Animal)` on a
            # bounded-T subject): a per-instantiation compile-time trait
            # (`::tpy::isinstance_static<M, decltype(x)>()`) with NO
            # extraction alias -- reads of the subject render the bare
            # name whatever sema narrowed, so the branch facts are inert.
            si = _lower_static_isinstance(e, lc, et)
            if si is not None:
                _witness("cond.isinstance_static")
                return si
            if (e.isinstance_var is not None
                    or e.isinstance_type is not None):
                raise ThirUnsupported("truthy.call_nonbool")
            if mode is None and not native_scalar:
                raise ThirUnsupported("truthy.call_nonbool")
            if mode is None:
                _witness("cond.bool_call")
        elif isinstance(e, TpyIfExpr):
            if mode is None and not native_scalar:
                raise ThirUnsupported("truthy.ifexpr_nonbool")
            _witness("ifexpr.cond_pos")
        elif isinstance(e, TpyNamedExpr):
            # A walrus truthy operand (`if (s := s + "!"):`): the mode wrap
            # composes over the inline assign (`(!(s = ...).empty())`); the
            # walrus arm validates its own target class. Only the empty-test
            # modes and bare scalar walruses admit -- an IS_TRUTHY
            # (whole-optional) walrus operand has no verified render.
            if mode is TruthinessMode.NONEMPTY:
                pass
            elif mode is None and native_scalar:
                pass
            elif mode is TruthinessMode.ALWAYS_TRUE:
                # An always-true walrus operand (`if (r := make(7)):` -- a
                # record/enum target): the wrap composes over the inline
                # assign (`(static_cast<void>((r = ..., *r)), true)`); the
                # walrus arm validates its own target class and supplies
                # the owned-slot comma-deref tail.
                _witness("cond.walrus_always_true")
            else:
                raise ThirUnsupported("truthy.walrus_shape")
        elif isinstance(e, TpySubscript):
            if storage_opt_bare:
                # A storage-form Optional ELEMENT read (`if xs[0]:`) renders
                # the bare checked `__getitem__`; the container twin of the
                # field arm.
                _witness("truthy.storage_opt_bare")
            elif not (native_scalar or mode in (TruthinessMode.NONEMPTY,
                                                TruthinessMode.RECORD_BOOL,
                                                TruthinessMode.RECORD_LEN)):
                # The empty-test and dunder modes wrap the element read the
                # same way they wrap any other operand, so the ELEMENT read
                # needs no arm of its own -- reachable for container elements,
                # whose read arm admits. An ALWAYS_TRUE element would have to
                # discard a render that can carry the bounds check, and a
                # whole-Optional element is the storage-form leg above.
                # Widening this set is not enough on its own for a USER-record
                # element: its read rejects in the subscript arm first.
                raise ThirUnsupported("truthy.subscript_mode")
        elif native_scalar:
            # Any remaining shape whose type renders its own test -- a scalar
            # ELEMENT read (`if xs[i]:`), a numeric literal (`if 1:`). The
            # value render is the condition, so there is nothing shape-
            # specific left to decide.
            pass
        elif not isinstance(e, (TpyUnaryOp, TpyChainedCompare)):
            raise ThirUnsupported("truthy.shape")
        operand = _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY, allow_temps=temps_ok,
                         pos=SinkPos.TRUTHINESS_OPERAND,
                         forms=(None
                                if mode is TruthinessMode.ALWAYS_TRUE
                                else _NO_FORMS)),
            allow_whole_optional=(mode is TruthinessMode.IS_TRUTHY
                                  or storage_opt_bare),
            allow_unrouted_name=mode is TruthinessMode.IS_TRUTHY,
        )
        if (mode is TruthinessMode.IS_TRUTHY
                and isinstance(operand, THIRName)):
            operand = replace(operand, deref=False)
        if mode is None:
            if (isinstance(e, TpyName) and isinstance(operand, THIRName)
                    and e.name in lc.prescan.global_slots
                    and isinstance(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(et))) if et is not None else None,
                        OptionalType)):
                # A pointer-repr Optional global is truthy exactly when its
                # slot pointer is non-null (`!(g)`), so this position opts
                # out of the value-position deref like the None test does.
                operand = replace(operand, deref=False)
                _witness("truthy.global_slot")
            return operand
        deref = (
            mode in (TruthinessMode.RECORD_BOOL, TruthinessMode.RECORD_LEN)
            and isinstance(e, TpyName)
            # The record __bool__/__len__ dispatch consumes the read at a
            # deref position, so ask the resolver for the indirect verdict.
            and _name_read_deref(
                e.name,
                _param_declared_type(e.name, lc) or declared.get(e.name),
                lc,
                _ExprUse(result=_ExprResultUse.TRUTHY,
                         pos=SinkPos.OPERAND, forms=_ONLY_INDIRECT_READ))
            # A rebound CONTAINER local's name read already derefs `(*xs)`
            # (the F2d value-use deref in the name arm), as does the
            # receiver read -- the wrap must not stack a second one.
            and not (isinstance(operand, (THIRName, THIRSelf))
                     and operand.deref))
        _witness("truthy." + mode.name.lower())
        return THIRTruthy(
            result_type=BOOL,
            mode=mode,
            operand=operand,
            deref=deref,
            loc=getattr(e, "loc", None),
        )
    loc = getattr(e, "loc", None)
    # Both enum wraps sit over the ordinary VALUE render (the operand
    # renders first, then wraps), so neither operand is a condition -- a
    # CONDITION use would demand a bool result the enum operand never has.
    _witness("enum.truthy_plain" if _plain_enum_truthy(et, lc.analyzer)
             else "enum.truthy_int")
    return THIREnumWrap(
        result_type=BOOL, wrap=wrap,
        operand=_lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY, allow_temps=temps_ok)),
        loc=loc)

def _str_ternary_form(str_rt: TpyType, then_view: bool,
                      else_view: bool) -> Form:
    """The whole-select form for a str-family ternary RESULT: BORROW iff the
    result is a runtime view (a StrView-resolved select, or both arms
    view-at-runtime), which is exactly when an owned sink must copy it. Shared
    by the general select and the isinstance-narrowed one so the copy verdict
    cannot drift between them."""
    return (Form.BORROW if is_str_view_type(str_rt) or (then_view and else_view)
            else Form.STORAGE)


def _str_view_arm(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """Whether an admitted ternary arm spells std::string_view at C++
    runtime: a str literal and a `str`-declared PARAM are runtime
    string_views; a nested ternary is a view iff both its arms are; a coerce
    reads its expected type; everything else
    keys on its resolved type. Drives the mixed-arm materialization and the
    whole-ternary form verdict in _lower_if_expr."""
    if isinstance(e, TpyStrLiteral):
        return True
    if isinstance(e, TpyIfExpr):
        return (_str_view_arm(e.then_expr, lc)
                and _str_view_arm(e.else_expr, lc))
    if isinstance(e, TpyName) and e.name in lc.prescan.param_names:
        pt = next((t for n, t in lc.params if n == e.name), None)
        if is_str_type(pt) or (isinstance(pt, LiteralType)
                               and pt.is_str_base()):
            return True
        # A value-repr Optional[str] param arm is the narrowed `(*s)` read -- a
        # string_view view. The resolved type narrows that read to StrView,
        # but sema's `get_expr_type` leaves the arm
        # Optional-typed, so key on the param binding instead. (An un-narrowed
        # whole-optional arm never reaches here -- it would make the ternary
        # Optional-typed, so `_lower_if_expr`'s str-result guard would not run.)
        if _value_opt_str(pt, lc.analyzer) is not None:
            return True
    if isinstance(e, TpyCoerce):
        rt = e.expected_type
    else:
        rt = lc.analyzer.get_expr_type(e)
    rt = unwrap_readonly(rt) if rt is not None else None
    rt = _resolve_pending_view(rt, lc.analyzer) or rt
    return rt is not None and is_str_view_type(rt)

def _bytes_view_arm(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """The bytes twin of `_str_view_arm`: a `bytes`/`BytesView` PARAM is a
    runtime span view, a value-repr Optional[bytes] param arm is the narrowed
    `(*b)` span, a nested ternary is a view iff both its arms are, a coerce reads
    its expected type; everything else keys on its resolved type. A bytes LITERAL
    is an OWNED render (`bytes_literal_owned`), not a view."""
    if isinstance(e, TpyBytesLiteral):
        return False
    if isinstance(e, TpyIfExpr):
        return (_bytes_view_arm(e.then_expr, lc)
                and _bytes_view_arm(e.else_expr, lc))
    if isinstance(e, TpyName) and e.name in lc.prescan.param_names:
        pt = next((t for n, t in lc.params if n == e.name), None)
        if is_bytes_type(pt) or is_bytes_view_type(pt):
            return True
        if _value_opt_bytes(pt, lc.analyzer) is not None:
            return True
    if isinstance(e, TpyCoerce):
        rt = e.expected_type
    else:
        rt = lc.analyzer.get_expr_type(e)
    rt = unwrap_readonly(rt) if rt is not None else None
    return rt is not None and is_bytes_view_type(rt)

def _bytes_owned_call_arm(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """A ternary arm that is a CALL returning OWNED `bytes` -- a fresh
    vector rvalue. A call can be neither a runtime span nor an
    Optional-param deref, the two facts the view->owned copy keys on,
    so a pair of these needs no copy at the owned sink."""
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        return False
    rt = _resolved_bytes_value(lc.analyzer.get_expr_type(e), lc.analyzer)
    return rt is not None and not is_bytes_view_type(rt)

def _ifexpr_container(rtype: 'TpyType | None', analyzer) -> 'TpyType | None':
    """A NON-RECORD reference-form ternary RESULT (pending containers resolve
    through the shared record): the ternary renders bare and its arms gate
    themselves, so the result family is the whole admission.

    The record half of the axis is deliberately excluded -- the reference arm
    in `_lower_if_expr` claims BOTH halves and this row is what a container
    it does not claim falls through to, so admitting records here would
    preempt the arm rather than share with it.
    `Array[T, N]` qualifies through the axis: a NAME binding of one already
    aliases (`std::array<T, N>& x = a;`), so its ternary is the same lvalue
    `?:` the containers render."""
    if rtype is None:
        return None
    tu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    tu = resolve_pending_container(tu, analyzer) or tu
    return tu if _f1_container_ref(tu) else None


def _lvalue_container_ternary(e: TpyExpr, analyzer) -> bool:
    """A container ternary of bare NAME arms -- the `ifexpr.record` arm's
    container leg, whose C++ `?:` over two same-type lvalues is itself an
    LVALUE.

    Receiver positions take only this arm pair: a MIXED lvalue/prvalue pair
    makes the `?:` a prvalue that silently copies the lvalue arm
    (BUGS.md#ternary-receiver-mixed-category-copies)."""
    return (isinstance(e, TpyIfExpr)
            and isinstance(e.then_expr, TpyName)
            and isinstance(e.else_expr, TpyName)
            and _ifexpr_container(analyzer.get_expr_type(e), analyzer)
            is not None)


def _dyn_own_call_ternary(e: 'TpyIfExpr', rtype: 'TpyType | None',
                          analyzer) -> bool:
    """A ternary of `Own[@dynamic P]`-returning calls (`return a() if c
    else b()`): sema strips the Own from the ternary's own type, so key on
    the ARMS' callee return slots. Renders bare (`((c) ? (a()) : (b()))`),
    each arm a unique_ptr rvalue the C++ ?: moves through."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
         if rtype is not None else None)
    if not (isinstance(u, NominalType) and is_dyn_protocol(u)):
        return False
    for arm in (e.then_expr, e.else_expr):
        if not isinstance(arm, TpyCall):
            return False
        fi = arm.resolved_function_info
        if fi is None or _own_dyn_return(fi.return_type) is None:
            return False
    return True


def _value_opt_ternary_result(rtype: 'TpyType | None',
                              analyzer) -> 'OptionalType | None':
    """A value-repr Optional ternary RESULT -- the scalar or owned-view
    family (`std::optional<T>` / `<std::string>`), whose C++ ?: needs both
    arms wrapped in the spelled optional (mismatched arm types otherwise:
    nullopt vs T). The wrap is unconditional for every value-repr
    Optional result."""
    if rtype is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if not (isinstance(u, OptionalType) and not u.uses_pointer_repr()):
        return None
    if (_value_opt_scalar(u, analyzer) is not None
            or _value_opt_owned_view(u, analyzer) is not None):
        return u
    return None


def _lower_value_opt_ternary_arm(arm: TpyExpr, vopt: 'OptionalType',
                                 cpp: str, lc: '_LowerCtx',
                                 declared: dict[str, TpyType],
                                 loc) -> THIRExpr:
    """One value-repr Optional ternary arm, wrapped in the spelled optional
    (`std::optional<std::string>(std::nullopt)` / `(...)("hello")`) so the
    C++ ternary deduces -- EVERY arm wraps, so the wrap composes
    over whatever the arm renders. Only the witnessed literal arms and a
    scalar name are in the slice; other shapes keep the named reject."""
    if isinstance(arm, TpyNoneLiteral):
        inner: THIRExpr = THIRLiteral(result_type=vopt, value=None,
                                      form=Form.STORAGE, loc=loc)
    elif isinstance(arm, (TpyStrLiteral, TpyBytesLiteral)):
        inner = _lower_expr(arm, lc, declared,
                            use=_ExprUse(slot_target=vopt))
    elif (isinstance(arm, TpyName) and arm.name in declared
          and _eligible_scalar(declared[arm.name])):
        # A scalar name renders bare and has no form facts, so the wrap
        # composes over it exactly as it does over a literal. Keyed on the
        # DECLARED type: a narrowed `int32 | None` name declares the
        # Optional and stays out, so no deref hazard is reachable.
        inner = _lower_expr(arm, lc, declared)
        _witness("ifexpr.value_opt_scalar_name")
    elif isinstance(arm, TpyDictLiteral) and is_dict(
            _vodi := unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(vopt.inner)))):
        # A dict-literal arm renders its self-describing
        # `ordered_map<K, V>({...})` against the Optional's INNER (the
        # branch target is the ternary's own Optional, and a dict literal
        # spells its own sema type, so the inner is what renders); the wrap
        # lands it in the optional.
        inner = _lower_expr(arm, lc, declared,
                            use=_ExprUse(slot_target=_vodi))
        _witness("ifexpr.storage_opt_dict_arm")
    elif (isinstance(arm, TpyTupleLiteral)
          and (_vot := _value_tuple_nested(vopt.inner, lc.analyzer))
          is not None
          and _opt_ternary_tuple_arm_ok(arm, _vot, lc.analyzer)):
        # The value-tuple sibling: the spelled brace (`std::tuple<int32_t,
        # int32_t>{a, b}`) against the Optional's inner tuple.
        inner = _lower_tuple_literal(arm, _vot, lc, declared)
        _witness("ifexpr.storage_opt_tuple_arm")
    elif (not isinstance(_vos := _peel_coerce(arm), TpyName)
          and _eligible_scalar(resolve_int_literals(
              unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                  lc.analyzer.get_expr_type(_vos)))),
              lc.analyzer.ctx.default_int_for_literal))):
        # A scalar-VALUED expression arm (`(a + b) if c else None`, a
        # scalar-returning call): scalars carry no form facts, so the wrap
        # composes over the arm's plain render exactly as over a literal. The
        # sema-inserted coercion to the Optional IS that wrap, so it peels --
        # lowering it would spell the optional twice. `cond_eager` is what
        # keeps a temp-producing arm out: the arm evaluates conditionally, and
        # only an audited temp may defer.
        inner = _lower_expr(_vos, lc, declared,
                            use=_ExprUse(slot_target=vopt.inner),
                            cond_eager=True)
        _witness("ifexpr.value_opt_scalar_expr")
    else:
        note_detail("ifexpr.value_opt_arm")
        raise ThirUnsupported("expr.ifexpr")
    return THIRCoerce(result_type=vopt, expr=inner,
                      coercion_name="value_opt_ternary_wrap",
                      wrap=f"{cpp}({{0}})", form=inner.form, loc=loc)


def _lower_ptr_opt_ternary_arm(arm: TpyExpr, popt: 'OptionalType',
                               lc: '_LowerCtx', declared: dict[str, TpyType],
                               loc) -> THIRExpr:
    """One ternary arm normalized to the `T*` a pointer-repr Optional result
    renders as -- the `_ptr_optional_branch` rule: `None` -> `nullptr`,
    an already-pointer Optional binding passes bare, a storage-form Optional
    field lifts via `optional_to_ptr`, and a plain F1-record name takes the
    address-of (the return ladder's position-independent addr_of node). The
    Optional arms key on the ANALYZED type, so a narrowed occurrence
    (retyped to the record) falls to the record-name arm's DECLARED-type
    check and rejects -- the narrowed-arm render is not lowered."""
    analyzer = lc.analyzer
    if isinstance(arm, TpyNoneLiteral):
        return THIRLiteral(result_type=popt, value=None, form=Form.BORROW,
                           loc=loc)
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(arm))))
    if (isinstance(arm, (TpyCall, TpyMethodCall))
            and _ptr_opt_borrow_call_ret(arm, at)):
        # A BORROW-returning ptr-Optional call arm (`first(xs) if c else
        # None`): the `T*` result IS the ternary's own shape, bare.
        _witness("ifexpr.optptr_call_arm")
        return _lower_expr(arm, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        pos=SinkPos.IF_EXPR_ARM,
                                        forms=_ONLY_PTR_OPT_PASSTHROUGH),
                           allow_whole_optional=True)
    if isinstance(at, OptionalType):
        if (isinstance(arm, TpyName)
                and _optional_ptr_borrow_name(arm, declared, analyzer)
                is not None):
            return _lower_expr(arm, lc, declared)
        if (isinstance(arm, TpyFieldAccess)
                and _field_receiver_ok(arm, declared, analyzer)
                and reads_storage_form_optional(analyzer, arm)):
            return THIRFormConvert(
                result_type=popt,
                value=_lower_field_source(arm, lc, declared),
                form=Form.BORROW, loc=loc)
        if (_container_elem_lvalue_subscript(arm, declared, analyzer,
                                             lc.pointers)
                and reads_storage_form_optional(analyzer, arm)):
            # An Optional container ELEMENT arm: the stored optional lifts
            # through the same convert the field arm takes.
            _witness("ifexpr.optptr_elem_lift")
            return THIRFormConvert(
                result_type=popt,
                value=_lower_expr(arm, lc, declared,
                                  subscript_prechecked=True),
                form=Form.BORROW, loc=loc)
    elif isinstance(arm, TpyName) and arm.name not in lc.narrow.narrowed:
        dt = declared.get(arm.name)
        dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
              if dt is not None else None)
        if (arm.name != "self" and not isinstance(dt, OwnType)
                and _f1_record(dt, analyzer)):
            return THIROptionalPtrArg(
                result_type=popt, form=Form.BORROW,
                value=_lower_expr(arm, lc, declared), addr_of=True, loc=loc)
    elif (_container_elem_lvalue_subscript(arm, declared, analyzer,
                                           lc.pointers)
          and _f1_record(at, analyzer)):
        # A plain record ELEMENT arm (`ns[0] if c else None`): the address of
        # the live element, the NAME arm's addr-of one receiver shape over.
        _witness("ifexpr.optptr_elem_addr")
        return THIROptionalPtrArg(
            result_type=popt, form=Form.BORROW,
            value=_lower_expr(arm, lc, declared, subscript_prechecked=True),
            addr_of=True, loc=loc)
    note_detail("ifexpr.optptr_arm")
    raise ThirUnsupported("expr.ifexpr")


def _lower_ptr_union_ternary_arm(arm: TpyExpr, u: 'UnionType',
                                 lc: '_LowerCtx',
                                 declared: dict[str, TpyType],
                                 loc) -> THIRExpr:
    """One ternary arm normalized to the pointer variant a WIDE ptr-union
    result renders as: a same-union ptr-variant BINDING name passes bare;
    a value-variant field lvalue lifts via `to_ptr_variant` (the
    FormConvert emit's union BORROW arm). A CONST source rejects -- its
    `to_const_ptr_variant` lift (or const variant spelling) would split
    the C++ ternary's operand types against the mutable sibling arm."""
    analyzer = lc.analyzer
    if isinstance(arm, TpyName):
        bt = declared.get(arm.name)
        bt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
              if bt is not None else None)
        if (bt == u and arm.name in lc.ptr_variant_locals
                and arm.name not in lc.const_locals
                and arm.name not in lc.narrow.narrowed
                and not _param_is_const(arm.name, lc.func, analyzer,
                                        lc.record_name)):
            return _lower_expr(arm, lc, declared)
    if (isinstance(arm, TpyFieldAccess)
            and _field_receiver_ok(arm, declared, analyzer)):
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(arm))))
        recv = arm.obj
        if (at == u and isinstance(recv, TpyName)
                and recv.name not in lc.const_locals
                and not _param_is_const(recv.name, lc.func, analyzer,
                                        lc.record_name)):
            return THIRFormConvert(
                result_type=u,
                value=_lower_field_source(arm, lc, declared),
                form=Form.BORROW, loc=loc)
    note_detail("ifexpr.union_arm")
    raise ThirUnsupported("expr.ifexpr")


def _ifexpr_isin_narrow_info(cond: TpyExpr, lc: '_LowerCtx',
                             declared: dict[str, TpyType]):
    """A ternary whose condition is a BARE single-member isinstance narrow
    on an un-narrowed union subject (`p.x if isinstance(p, A) else p.y`): the
    then arm reads the subject through the condition-scoped inline get as the
    checked member. The ELSE member is the exact complement (the
    inline-isinstance facts' false-branch remainder) when the union has
    exactly two members; on a wider union the complement is itself a union
    with no inline render, so the else arm gets NO fact and a subject read
    there rejects on its own un-narrowed path. Returns `(var, union,
    then_member, else_member_or_None)` or None. Compound conditions, wrapper
    unions and folded checks stay out."""
    info = _isinstance_narrow_info(cond, declared, lc.analyzer)
    if info is None:
        return None
    var, u, members, folded = info
    if folded or len(members) != 1 or u.needs_wrapper():
        return None
    if var in lc.narrow.narrowed or var in lc.narrow.spelled:
        return None
    remaining = [m for m in u.members if m != members[0]]
    if len(remaining) != 1 or isinstance(remaining[0], UnionType):
        return var, u, members[0], None
    if is_void_like_type(remaining[0]):
        return None
    return var, u, members[0], remaining[0]


def _lower_narrowed_ternary(e: TpyIfExpr, ifn, slot, rtype,
                            lc: '_LowerCtx',
                            declared: dict[str, TpyType],
                            loc) -> THIRIfExpr:
    """Lower an isinstance-condition ternary: the holds test condition,
    each arm under its condition-scoped inline fact (then = the checked
    member, else = the complement). Slice: value-scalar results only --
    every special result family (optional / union / container / record /
    str / bytes / tuple) lowers its arms with family-specific wraps that
    have no narrowed witness, so they keep rejecting."""
    analyzer = lc.analyzer
    var, u, then_m, else_m = ifn
    result_t = slot if slot is not None else rtype
    rbare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(result_t)))
    if not (_eligible_scalar(rbare)
            # A str result joins the slice: the arms take the same
            # slot-targeted literal render the scalar arms do, and the
            # owned/view decision belongs to the SINK (a `str` return wraps
            # the whole select in `std::string(...)`), not to either arm.
            or _resolved_str_value(rbare, analyzer) is not None):
        raise ThirUnsupported("ifexpr.narrow_result", detail=True)
    cond = _lower_isinstance_cond((var, u, (then_m,), False), e.condition,
                                  lc)

    def _arm(arm_expr, member):
        if member is None:
            # No inline fact for this arm (the complement is a union): a
            # subject read here takes its ordinary un-narrowed path.
            return _slot_literal_retype(
                _lower_char_targeted(arm_expr, slot, lc, declared), slot, lc)
        saved = dict(lc.inline_narrowed)
        arm_declared = dict(declared)
        arm_declared[var] = member
        try:
            lc.inline_narrowed[var] = _inline_union_member(var, member, u, lc)
            return _slot_literal_retype(
                _lower_char_targeted(arm_expr, slot, lc, arm_declared),
                slot, lc)
        finally:
            lc.inline_narrowed = saved

    then = _arm(e.then_expr, then_m)
    orelse = _arm(e.else_expr, else_m)
    form = Form.VALUE
    str_rt = _resolved_str_value(rbare, analyzer)
    if str_rt is not None:
        tv = _str_view_arm(e.then_expr, lc)
        ev = _str_view_arm(e.else_expr, lc)
        if tv != ev:
            # A MIXED view/owned arm pair needs the general select's per-arm
            # materialization, which has no narrowed witness.
            raise ThirUnsupported("ifexpr.narrow_str_mixed", detail=True)
        form = _str_ternary_form(str_rt, tv, ev)
        _witness("ifexpr.isin_narrow_str")
    _witness("ifexpr.isin_narrow")
    return THIRIfExpr(result_type=result_t, cond=cond, then=then,
                      orelse=orelse, form=form, loc=loc)


def _lower_record_prvalue_arm(arm: TpyExpr, rec_t: 'TpyType',
                              lc: '_LowerCtx',
                              declared: dict[str, TpyType]
                              ) -> 'THIRExpr | None':
    """One record-ternary arm that renders a PRVALUE of `rec_t`, or None.

    Two shapes: `copy(<record name>)`, the copy-construct `T(x)` -- including
    a POINTER-local source, whose deref the value carries (`T((*p))`); and a
    call / method-call returning the record BY VALUE (a ctor rvalue, an
    `Own[T]`-returning factory). A `T&`-returning call is an lvalue and rides
    the BORROW slice instead.

    No ptr-variant carve-out: a variant binding read as a member record needs
    a preceding narrowing statement, which would already have broken the
    member-init list's leading run."""
    analyzer = lc.analyzer
    loc = getattr(arm, "loc", None)
    carg = copy_call_arg(arm, analyzer)
    if carg is not None:
        if not isinstance(carg, TpyName):
            return None
        at = analyzer.get_expr_type(carg)
        if not (_f1_record(at, analyzer) and unwrap_readonly(
                unwrap_ref_type(unwrap_send_sync(at))) == rec_t):
            return None
        return THIRCopy(result_type=rec_t,
                        value=_lower_expr(
                            carg, lc, declared,
                            use=_ExprUse(pos=SinkPos.CALL_ARG)),
                        cpp_type=lc.render_type(rec_t), form=Form.STORAGE,
                        loc=loc)
    if (isinstance(arm, (TpyCall, TpyMethodCall))
            and not call_returns_cpp_ref(analyzer,
                                         arm.resolved_function_info)):
        at = analyzer.get_expr_type(arm)
        if unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at))) != rec_t:
            return None
        return _lower_expr(arm, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
    return None


def _lower_if_expr(e: TpyIfExpr, rtype: 'TpyType | None', lc: '_LowerCtx',
                   declared: dict[str, TpyType], loc, *,
                   cond_temps_ok: bool = False,
                   elem_storage: bool = False,
                   record_prvalue_ok: bool = False) -> THIRIfExpr:
    """`a if c else b` -> `((cond) ? (then) : (else))`.
    The arm slot is the ternary's OWN resolved type (`branch_target =
    result_type` -- the consumer's target is ignored), so the target-typed
    literal renders (BigInt ctor wrap, float32 `f` suffix, char literal)
    thread from here, not from the position. For an owned-str result with
    mixed view/owned arms, the view arm materializes (`std::string(a)`) so
    the C++ ternary deduces std::string -- a str literal is const char* in
    ternary context and converts natively, so it stays bare."""
    analyzer = lc.analyzer
    slot = rtype
    if slot is not None:
        slot = resolve_int_literals(unwrap_readonly(slot),
                                    analyzer.ctx.default_int_for_literal)
        # A pending container result resolves through the shared record so
        # the arm slot (and the node's result_type) is the final container.
        cont_slot = _ifexpr_container(slot, analyzer)
        if cont_slot is not None:
            slot = cont_slot
    ifn = _ifexpr_isin_narrow_info(e.condition, lc, declared)
    if ifn is not None:
        return _lower_narrowed_ternary(e, ifn, slot, rtype, lc, declared,
                                       loc)
    # The condition evaluates exactly once unconditionally, so the
    # enclosing flush right extends into it (its arg temps hoist before
    # the statement); the ARMS evaluate lazily and never get it.
    cond = _lower_truthy(e.condition, lc, declared, temps_ok=cond_temps_ok)
    result_t = slot if slot is not None else rtype
    sopt = (_storage_opt_ternary_result(result_t, analyzer)
            if elem_storage else None)
    popt = (None if sopt is not None
            else _optional_ptr_borrow_wide(result_t, analyzer))
    if popt is not None and lc.in_container_elem:
        # Inside a container element but NOT the immediate element: a
        # sticky `in_container_element` would wrap this ternary in the
        # value optional too -- over a `T*` arm, which does not compile.
        # Neither render is correct here, so the position rejects.
        note_detail("ifexpr.nested_container_elem")
        raise ThirUnsupported("expr.ifexpr")
    if popt is not None:
        # Pointer-repr Optional result: each arm normalizes to `T*` so the
        # C++ ?: operands match (the `_ptr_optional_branch` rule); the
        # whole ternary is a BORROW pointer its sinks bind bare.
        then = _lower_ptr_opt_ternary_arm(e.then_expr, popt, lc, declared, loc)
        orelse = _lower_ptr_opt_ternary_arm(e.else_expr, popt, lc, declared,
                                            loc)
        _witness("ifexpr.ptr_opt")
        return THIRIfExpr(result_type=result_t, cond=cond, then=then,
                          orelse=orelse, form=Form.BORROW, loc=loc)
    vopt = (sopt if sopt is not None
            else _value_opt_ternary_result(result_t, analyzer))
    if vopt is not None:
        # A VALUE-repr Optional result: both arms wrap in the spelled
        # optional for C++ ternary deduction (mismatched arm types
        # otherwise -- nullopt vs T); the whole ternary is a VALUE optional
        # its sinks take bare.
        cpp = lc.render_type(vopt)
        then = _lower_value_opt_ternary_arm(e.then_expr, vopt, cpp, lc,
                                            declared, loc)
        orelse = _lower_value_opt_ternary_arm(e.else_expr, vopt, cpp, lc,
                                              declared, loc)
        _witness("ifexpr.storage_opt_elem" if sopt is not None
                 else "ifexpr.value_opt")
        return THIRIfExpr(result_type=vopt, cond=cond, then=then,
                          orelse=orelse, form=Form.VALUE, loc=loc)
    u_res = _eligible_ptr_union_wide(
        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(result_t))),
        analyzer)
    if u_res is not None:
        # A WIDE ptr-union result: each arm normalizes to the pointer
        # variant so the C++ ?: operands match (`((c) ? (p)
        # : (::tpy::to_ptr_variant(h.pet)))`); the whole ternary is a
        # BORROW variant its sinks bind bare.
        then = _lower_ptr_union_ternary_arm(e.then_expr, u_res, lc, declared,
                                            loc)
        orelse = _lower_ptr_union_ternary_arm(e.else_expr, u_res, lc,
                                              declared, loc)
        _witness("ifexpr.ptr_union")
        return THIRIfExpr(result_type=u_res, cond=cond, then=then,
                          orelse=orelse, form=Form.BORROW, loc=loc)
    rec_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(result_t)))
    if (isinstance(rec_t, TupleType) and rec_t.is_mixed_own()
            and isinstance(e.then_expr, (TpyCall, TpyMethodCall))
            and isinstance(e.else_expr, (TpyCall, TpyMethodCall))
            and _renders_own_borrow_tuple(e.then_expr,
                                          lc.own_borrow_tuple_locals,
                                          analyzer)
            and _renders_own_borrow_tuple(e.else_expr,
                                          lc.own_borrow_tuple_locals,
                                          analyzer)):
        # A ternary of two MIXED-own-tuple calls: C++ evaluates one arm, so
        # the ternary carries the same mixed borrow render both arms do
        # (`((flag) ? (make_mixed(b)) : (make_mixed(c)))` -- the
        # `is_storage_form_source` composition rule); arms render bare via
        # the btuple-slot call admission. BORROW: the ref half aliases.
        then = _lower_expr(e.then_expr, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.VALUE,
                                        pos=SinkPos.IF_EXPR_ARM,
                                        forms=_ONLY_BTUPLE_SLOT))
        orelse = _lower_expr(e.else_expr, lc, declared,
                             use=_ExprUse(result=_ExprResultUse.VALUE,
                                          pos=SinkPos.IF_EXPR_ARM,
                                          forms=_ONLY_BTUPLE_SLOT))
        _witness("ifexpr.mixed_own_call")
        return THIRIfExpr(result_type=result_t, cond=cond, then=then,
                          orelse=orelse, form=Form.BORROW, loc=loc)
    # LIST comps only: the set/dict flavors have no witness, so they keep
    # rejecting until one exists (the machinery below is family-blind, so
    # widening is a gate edit + a unit when a case arrives).
    _comp_kinds = (TpyListComprehension,)
    if (is_list(rec_t)
            and any(isinstance(arm, _comp_kinds)
                    for arm in (e.then_expr, e.else_expr))
            and all(isinstance(arm, _comp_kinds + (TpyArrayLiteral,))
                    for arm in (e.then_expr, e.else_expr))):
        # A container ternary with a COMPREHENSION arm (`ys = [f(i) for i
        # in xs] if cond else [0]`): all-rvalue arms, so the ternary is a
        # VALUE the decl copies bare. The comp renders its stmt-expr inside
        # the arm; its per-iteration element flush is what relocates any
        # deferred temp into the loop body (the degrade seam). The literal
        # arm self-spells (`std::vector<int32_t>{0}` -- the array-literal
        # branch prefix).
        from .comprehensions import _lower_comprehension

        def _rv_container_arm(arm) -> THIRExpr:
            if isinstance(arm, _comp_kinds):
                _ptrs = _comp_shadow_pointers(lc.pointers, declared,
                                              lc.analyzer)
                lowered_arm = _lower_comprehension(arm, rec_t, lc, declared,
                                                   _ptrs)
            else:
                lowered_arm = _lower_expr(
                    arm, lc, declared,
                    use=_ExprUse(slot_target=rec_t))
                if (isinstance(lowered_arm, THIRContainerLiteral)
                        and lowered_arm.typed_brace_cpp is None):
                    lowered_arm = replace(lowered_arm,
                                          typed_brace_cpp=lc.render_type(
                                              rec_t))
            _check_cond_eager_temps(lowered_arm)
            return lowered_arm

        then = _rv_container_arm(e.then_expr)
        orelse = _rv_container_arm(e.else_expr)
        _witness("ifexpr.container_comp_arm")
        return THIRIfExpr(result_type=result_t, cond=cond, then=then,
                          orelse=orelse, form=Form.VALUE, loc=loc)
    if _f1_ref(rec_t, analyzer):
        # A plain reference-axis ternary -- a record or a container, one arm
        # on the same axis: a same-type lvalue ternary is itself an lvalue,
        # so bare NAME arms render with no per-arm conversion (each a plain
        # deref read) and the result is a BORROW lvalue its consumer copies
        # or aliases. NAME arms and BORROW-returning call arms are in the
        # slice (a `T&` call result is an lvalue too); a VALUE-returning
        # call/ctor arm makes the C++ ternary a prvalue whose consumers
        # (REF_ALIAS binds, the Own copy temp) have per-shape renders.
        #
        # Only the RECORD half rejects off-slice: a container result has the
        # generic tail below as its own renderer, so it falls through there
        # rather than de-routing bodies that compile today.
        _rec_result = _f1_record(rec_t, analyzer)

        def _rec_arm_borrow_call(arm) -> bool:
            # The shared value-category classifier has the last word: a
            # builtin container's ctor call resolves to its `__init__`, whose
            # VOID return makes `call_returns_cpp_ref` answer True (see
            # BUGS.md#void-method-reads-as-borrow-returning). Admitting one as
            # an lvalue arm renders a prvalue `?:` that silently COPIES the
            # other arm -- the mixed-category copy this arm must not make.
            return (isinstance(arm, (TpyCall, TpyMethodCall))
                    and call_returns_cpp_ref(
                        analyzer, arm.resolved_function_info)
                    and not is_rvalue_source(analyzer, arm))

        def _ref_arm_lvalue(arm) -> bool:
            return (isinstance(arm, TpyName)
                    or _rec_arm_borrow_call(arm)
                    # A container-ELEMENT subscript arm (`rs[0] if c else
                    # rs[1]`) is an lvalue too, so the `?:` stays one.
                    or _container_elem_lvalue_subscript(arm, declared,
                                                        analyzer,
                                                        lc.pointers))
        if record_prvalue_ok:
            prv = [_lower_record_prvalue_arm(a, rec_t, lc, declared)
                   for a in (e.then_expr, e.else_expr)]
            if all(p is not None for p in prv):
                # Every arm is a prvalue, so the C++ `?:` is a prvalue too --
                # a VALUE result the position direct-initializes. Admitted
                # only where the caller confirmed such a sink; the lvalue
                # slice below keeps the BORROW form.
                _witness("ifexpr.record_prvalue")
                return THIRIfExpr(result_type=result_t, cond=cond,
                                  then=prv[0], orelse=prv[1],
                                  form=Form.VALUE, loc=loc)
            if any(p is not None for p in prv):
                # A record NAME arm beside a prvalue arm
                # (`V3(0.0) if e is None else e`): C++ converts the lvalue
                # operand into the prvalue result, so the member-init still
                # direct-initializes from a VALUE -- a copy of the named
                # object, which is what a record FIELD stores anyway. TWO
                # lvalue arms make the `?:` an lvalue and belong to the
                # BORROW slice below, so at least one prvalue arm is
                # required here.
                mixed: 'list[THIRExpr] | None' = []
                for arm, p in zip((e.then_expr, e.else_expr), prv):
                    if p is not None:
                        mixed.append(p)
                        continue
                    at = analyzer.get_expr_type(arm)
                    if not (isinstance(arm, TpyName) and arm.name in declared
                            and unwrap_readonly(unwrap_ref_type(
                                unwrap_send_sync(at))) == rec_t):
                        mixed = None
                        break
                    # The same read the copy arm takes for its source: a
                    # pointer-bound name (a narrowed `T | None` param)
                    # carries its `(*p)` deref here too.
                    mixed.append(_lower_expr(
                        arm, lc, declared,
                        use=_ExprUse(pos=SinkPos.IF_EXPR_ARM)))
                if mixed is not None:
                    _witness("ifexpr.record_prvalue_name_arm")
                    return THIRIfExpr(result_type=result_t, cond=cond,
                                      then=mixed[0], orelse=mixed[1],
                                      form=Form.VALUE, loc=loc)
            if _rec_result:
                note_detail("ifexpr.record_prvalue_arm")
                raise ThirUnsupported("expr.ifexpr")
        elif all(_ref_arm_lvalue(arm)
                 for arm in (e.then_expr, e.else_expr)):
            def _rec_arm_lower(arm):
                if _rec_arm_borrow_call(arm):
                    return _lower_expr(
                        arm, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                if isinstance(arm, TpySubscript):
                    _witness("ifexpr.record_elem_arm")
                    return _lower_expr(arm, lc, declared,
                                       subscript_prechecked=True)
                return _lower_expr(arm, lc, declared,
                                   use=_ExprUse(
                                       pos=SinkPos.IF_EXPR_ARM,
                                       slot_target=rec_t))
            then = _rec_arm_lower(e.then_expr)
            orelse = _rec_arm_lower(e.else_expr)
            _witness("ifexpr.record")
            return THIRIfExpr(result_type=result_t, cond=cond, then=then,
                              orelse=orelse, form=Form.BORROW, loc=loc)
        elif _rec_result:
            note_detail("ifexpr.record_arm")
            raise ThirUnsupported("expr.ifexpr")
    # ARMS evaluate lazily: the eager-only conditional right (a
    # non-deferring temp hoists at the enclosing statement, the non-movable
    # arm; would-defer temps raise at the exit check). Gated on the
    # ternary's own flushable right, at the statement flush.
    _arm_use = _ExprUse(allow_temps=cond_temps_ok)
    _cont_slot = _ifexpr_container(slot, analyzer)

    def _container_arm_use(arm: TpyExpr) -> _ExprUse:
        # A container-returning CALL arm owns its result, so the ternary is
        # the arm's storage sink exactly as a direct decl init would be --
        # same bare render, so it takes the storage admission rather than
        # the value-position one, which claims no container return.
        if (_cont_slot is not None
                and isinstance(arm, (TpyCall, TpyMethodCall))):
            return _ExprUse(result=_ExprResultUse.STORAGE,
                            allow_temps=cond_temps_ok)
        return _arm_use
    # A str-family FIELD arm (`r.s if cond else t.s`) is the same bare
    # member read a binop operand admits -- thread the form tag exactly
    # like the binop operand sites.
    then = _slot_literal_retype(
        _lower_char_targeted(e.then_expr, slot, lc, declared,
                             use=_container_arm_use(e.then_expr),
                             cond_eager=cond_temps_ok,
                             field_owned_str_ok=isinstance(
                                 e.then_expr, TpyFieldAccess)),
        slot, lc)
    orelse = _slot_literal_retype(
        _lower_char_targeted(e.else_expr, slot, lc, declared,
                             use=_container_arm_use(e.else_expr),
                             cond_eager=cond_temps_ok,
                             field_owned_str_ok=isinstance(
                                 e.else_expr, TpyFieldAccess)),
        slot, lc)
    form = Form.VALUE
    str_rt = _resolved_str_value(rtype, analyzer)
    if str_rt is not None:
        tv = _str_view_arm(e.then_expr, lc)
        ev = _str_view_arm(e.else_expr, lc)
        if is_str_type(str_rt) and tv != ev:
            if tv and not isinstance(e.then_expr, TpyStrLiteral):
                then = THIRFormConvert(result_type=str_rt, value=then,
                                       form=Form.STORAGE, loc=loc)
            if ev and not isinstance(e.else_expr, TpyStrLiteral):
                orelse = THIRFormConvert(result_type=str_rt, value=orelse,
                                         form=Form.STORAGE, loc=loc)
            _witness("ifexpr.str_mixed")
        form = _str_ternary_form(str_rt, tv, ev)
        _witness("ifexpr.str")
    elif _is_string_owned(rtype):
        # A String result (both arms concat results): an owned rvalue.
        form = Form.STORAGE
        _witness("ifexpr.str")
    elif _resolved_bytes_value(rtype, analyzer) is not None:
        # A bytes-family ternary. The both-view arm shape (a narrowed
        # `(*a)` span and a bytes param `b`) is a BORROW span, so the owned
        # decl/return sink wraps `::tpy::Bytes`. Mixed / owned-literal
        # arms would need the str-mixed per-arm materialization and defer.
        form = Form.BORROW
        if not (_bytes_view_arm(e.then_expr, lc)
                and _bytes_view_arm(e.else_expr, lc)):
            # A NARROWED-deref view arm + a bytes-LITERAL arm renders the
            # raw mixed ternary (`(*b)` vs `bytes_literal_owned(..)`), and
            # the owned sink wraps the WHOLE ternary in `Bytes(x)`. A
            # PLAIN-param view arm is excluded: that flavor renders bare
            # (no copy wrap -- a span-vs-vector ternary with no exec
            # witness, see BUGS.md), so it keeps rejecting.
            _blv, _bll = ((e.then_expr, e.else_expr)
                          if _bytes_view_arm(e.then_expr, lc)
                          else (e.else_expr, e.then_expr))
            _blm = (_bytes_view_arm(_blv, lc)
                    and isinstance(_peel_coerce(_bll), TpyBytesLiteral)
                    and isinstance(_blv, TpyName)
                    and _value_opt_view_param(_blv.name, lc))
            if _blm:
                _witness("ifexpr.bytes_view_lit")
            elif all(_bytes_owned_call_arm(arm, lc)
                     for arm in (e.then_expr, e.else_expr)):
                # Both arms OWNED-bytes-returning CALLS (`f.read() if c else
                # f.read(n)`): each renders its own vector rvalue, so the
                # ternary is already owned storage and the owned sink must
                # not copy it. A call is neither view-at-runtime nor an
                # Optional-param deref, the two facts the copy wrap keys
                # on.
                form = Form.STORAGE
                _witness("ifexpr.bytes_owned_calls")
            elif all(isinstance(arm, TpySubscript)
                     and not _bytes_view_arm(arm, lc)
                     for arm in (e.then_expr, e.else_expr)):
                # Both arms OWNED container-element subscript lvalues
                # (`c[0] if cond else d[0]` on list[bytes] -- a
                # root-mutation-DEMOTED sink): arms render bare and the
                # owned decl copies the ternary with no `Bytes(x)` wrap.
                form = Form.STORAGE
                _witness("ifexpr.bytes_owned_elems")
            else:
                raise ThirUnsupported("ifexpr.bytes_mixed")
        _witness("ifexpr.bytes")
    elif isinstance(unwrap_readonly(unwrap_ref_type(slot)), TupleType):
        # A tuple ternary carries its ARMS' form: the render is form-blind
        # (`((c) ? (a) : (b))`), but a storage-form arm makes the whole
        # result storage, and sinks that lift (tuple_to_pointer) must see
        # that. Without the propagation a storage arm would report VALUE
        # and slip past a lifting sink's admission check.
        form = (Form.STORAGE
                if Form.STORAGE in (then.form, orelse.form) else form)
        _witness("ifexpr.tuple")
    elif _ifexpr_container(slot, analyzer) is not None:
        # The container ternary render is form-blind (`((c) ? (a) : (b))`);
        # its lvalue-arm shape is what the REF_ALIAS decl binds. A LIST
        # literal arm spells its type (a bare brace-init cannot deduce in
        # ternary context); dict/set renders self-describe.
        if is_list(slot):
            if isinstance(then, THIRContainerLiteral):
                then = replace(then, typed_brace_cpp=lc.render_type(slot))
            if isinstance(orelse, THIRContainerLiteral):
                orelse = replace(orelse, typed_brace_cpp=lc.render_type(slot))
        _witness("ifexpr.container")
    else:
        _witness("ifexpr.value")
    return THIRIfExpr(result_type=slot if slot is not None else rtype,
                      cond=cond, then=then, orelse=orelse, form=form, loc=loc)

def _lower_int_literal(value: int, result_type: TpyType, lc: '_LowerCtx',
                       loc) -> THIRLiteral:
    return THIRLiteral(
        result_type=result_type, value=value,
        int_cpp=render_int_literal_value(
            value, None,
            default_int_type=lc.analyzer.ctx.default_int_type,
            type_to_cpp=lambda t: t.to_cpp()),
        loc=loc)


def _fixed_int_binop_fi(t: 'TpyType', op: str, analyzer):
    """The single-overload fixed-int operator fi over `t`, looked up by
    `BINOP_TO_METHOD[op]` for the dedicated literal arm. ONLY the plain
    single-overload case resolves: a type carrying several overloads
    returns None rather than guess a selection."""
    method_name = BINOP_TO_METHOD.get(op)
    if method_name is None:
        return None
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None:
        return None
    overloads = ri.get_method_overloads(method_name)
    if len(overloads) != 1:
        return None
    fi = overloads[0]
    if fi.cpp_template or fi.native_function or fi.native_name:
        return fi
    return None


def _retarget_int_literal(v: THIRLiteral, slot: TpyType,
                          lc: '_LowerCtx') -> THIRLiteral:
    st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if isinstance(st, OwnType):
        st = unwrap_readonly(st.wrapped)
    return replace(
        v, result_type=st,
        int_cpp=render_int_literal_value(
            v.value, st,
            default_int_type=lc.analyzer.ctx.default_int_type,
            type_to_cpp=lambda t: t.to_cpp()))


def _slot_literal_retype(v: 'THIRExpr | None',
                         slot: 'TpyType | None',
                         lc: '_LowerCtx') -> 'THIRExpr | None':
    """Target threading for target-typed literal renders:
    a float literal against a float32 slot takes the `f` suffix; an integer
    literal takes fixed-width casts/suffixes or BigInt constructor wraps.
    The slot type threads at decl inits/reassigns, returns,
    call/ctor args, field writes, MIL inits, container elements, and
    resolved-binop operands (the receiver/param targets) --
    comparison operands do NOT thread it (the compare block renders literal
    operands bare; a fixed-int/double context absorbs them). Applied
    post-lowering: only a float/int THIRLiteral is retyped, every other node
    passes through."""
    if slot is None or not isinstance(slot, TpyType):
        return v
    st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if isinstance(st, OwnType):
        st = unwrap_readonly(st.wrapped)
    if (isinstance(v, THIRUnaryArith) and isinstance(v.operand, THIRLiteral)
            and isinstance(v.operand.value, float) and is_float32_type(st)):
        # A negated float literal (`-3.0` into a float32 slot): the `f` suffix
        # keys on the operand literal's type, so thread the slot into it, the
        # same target render the unary operand takes (`-(3.0f)`).
        return replace(v, operand=replace(v.operand, result_type=st))
    if (isinstance(v, THIRBinOp) and v.both_literal_int_operands
            and is_fixed_int_type(st)):
        # The dedicated fixed-int literal arm: a both-IntLiteral binop
        # meeting a fixed-int SLOT re-resolves the operator on the slot
        # type and renders that call -- target-width template, no parens.
        # Operands recurse (nested literal binops rebuild the same way);
        # an op outside the module system (bitwise) takes the bare
        # `(l op r)` render.
        left = _slot_literal_retype(v.left, st, lc)
        right = _slot_literal_retype(v.right, st, lc)
        fi = _fixed_int_binop_fi(st, v.op, lc.analyzer)
        if fi is not None and fi.cpp_template:
            return replace(v, left=left, right=right, result_type=st,
                           template_override=fi.cpp_template)
        return replace(v, left=left, right=right, result_type=st,
                       resolved=None, paren_wrap=True)
    if not isinstance(v, THIRLiteral):
        return v
    if isinstance(v.value, float) and is_float32_type(st):
        return replace(v, result_type=st)
    if (isinstance(v.value, int) and not isinstance(v.value, bool)
            and (is_fixed_int_type(st) or is_big_int_type(st))):
        return _retarget_int_literal(v, st, lc)
    return v

def _rb_operand_slots(rb) -> 'tuple[TpyType | None, TpyType | None]':
    """The (left, right) render targets of a resolved ARITHMETIC binop -- the
    receiver/param types threaded into the operand renders
    (forward: left={self}, right={0}; reverse swapped). Comparison operands
    never take these (the compare block renders them target-less)."""
    if rb is None or rb.method is None:
        return (None, None)
    param = rb.method.params[0].type if rb.method.params else None
    recv = rb.receiver_type
    return (param, recv) if rb.is_reverse else (recv, param)

def _field_cpp(e: TpyFieldAccess) -> str:
    """The rendered C++ member name for a field access. A `@native` record
    renames fields via `native_field("m_x")`; sema stamps the rename on
    `native_field_name` (own-fields-first, so a subclass redeclaration shadows
    an ancestor's), so read it here rather than always escaping the source
    name, else native-record field reads spell the wrong member."""
    return (e.native_field_name if e.native_field_name is not None
            else escape_cpp_name(e.field))

def _unbound_self_field_access(e: TpyFieldAccess, lc: '_LowerCtx',
                               rtype: 'TpyType | None', form: Form,
                               loc) -> THIRFieldAccess:
    """The one `this->BaseN::field` spelling (THIRSelf receiver + base-
    qualified field_cpp), shared by the read arm and the borrow-source arm
    so the render and its fence cannot drift. The spelling hardcodes
    `this->`, so a resumable receiver (`__self`, a `Record&` read with `.`)
    rejects."""
    if lc.self_cpp != "this":
        raise ThirUnsupported("field.unbound_self_receiver", detail=True)
    _witness("field.unbound_self")
    return THIRFieldAccess(
        result_type=rtype,
        receiver=THIRSelf(result_type=e.unbound_self_parent_type,
                          form=Form.BORROW, cpp=lc.self_cpp, loc=loc),
        field_cpp=(f"{e.unbound_self_parent_type.to_cpp()}::"
                   f"{_field_cpp(e)}"),
        is_arrow=True,
        form=form,
        loc=loc,
    )


def _reject_optional_receiver_link(e: 'TpyFieldAccess | TpySubscript',
                                   lc: '_LowerCtx', tag: str) -> None:
    """Refuse a borrow-source read whose RECEIVER chain crosses an unproven
    Optional link.

    The prechecked receiver recursion the two source helpers use skips the
    receiver ladders, so an Optional intermediate (`o.mid.f` with `o.mid`
    analyzed Optional) would silently drop the `deref_optional_check` panic.
    Vetted at this chokepoint so every caller is covered; a sema-narrowed
    link reads non-Optional here and passes."""
    link = e.obj
    while isinstance(link, (TpyFieldAccess, TpySubscript)):
        if isinstance(unwrap_readonly(lc.analyzer.get_expr_type(link)),
                      OptionalType):
            raise ThirUnsupported(tag, detail=True)
        link = link.obj


def _lower_field_source(e: TpyFieldAccess, lc: '_LowerCtx',
                        declared: dict[str, TpyType]) -> THIRFieldAccess:
    """The storage-form field read backing a borrow-local binding or an F3 tuple
    lift: `recv.field` where the field is a record (REF_ALIAS / POINTER), a
    storage-form `optional<T>` (OPTIONAL_TO_PTR), or a storage-form tuple (the F3
    `auto&&` alias decl + the borrow-tuple return source). form=STORAGE -- the bridge
    to borrow form is the `T&` reference bind (REF_ALIAS), the `auto&&` alias, or the
    wrapping THIRFormConvert (`&(...)` for POINTER, `optional_to_ptr` / `tuple_to_pointer`
    for the lifts). The receiver itself may be a pointer-local (a chained borrow), so
    `->` vs `.` is decided the same way as a value read. A sema-narrowed
    Optional field source (declared `std::optional<T>`, analyzed non-Optional
    -- a branch-first `n = w._node` alias under an is-not-None narrow) unwraps
    `(*recv.field)` exactly like the value-position read; the un-narrowed
    OPTIONAL_TO_PTR lift keeps the whole optional (its analyzed type stays
    Optional, so the predicate is inert there)."""
    if _unbound_self_field_ok(e):
        # `BaseN.field` as a borrow-local source: the class-name receiver
        # has no value type, so the recursion below cannot apply; form
        # STORAGE like every field source.
        return _unbound_self_field_access(
            e, lc, lc.analyzer.get_expr_type(e), Form.STORAGE,
            getattr(e, "loc", None))
    _reject_optional_receiver_link(e, lc, "field.opt_receiver")
    rtype = lc.analyzer.get_expr_type(e)
    narrowed_opt = _narrowed_opt_field_read(e, rtype, declared, lc.analyzer)
    if narrowed_opt:
        _witness("field.narrowed_deref")
    # An explicit `Ptr[record]` receiver reaches its member the same way here
    # as at a value read (`field.ptr_value`): `->` when sema proved the
    # pointer non-null, `::tpy::deref_check(<recv>).field` otherwise. The
    # pointer SET the arrow rule below reads holds borrow bindings, not
    # Ptr values, so without this the source would spell a `.` on a pointer.
    ptr_recv = _ptr_value_field_recv_ok(e, declared, lc.analyzer)
    ptr_arrow, ptr_deref = _ptr_recv_access(e.ptr_non_null)
    if ptr_recv:
        _witness("field.ptr_value_storage")
    receiver = _lower_expr(
        e.obj, lc, declared,
        use=_ExprUse(result=_ExprResultUse.RECEIVER),
        field_prechecked=isinstance(e.obj, TpyFieldAccess),
        subscript_prechecked=isinstance(e.obj, TpySubscript))
    return _self_recv_positioned(THIRFieldAccess(
        result_type=rtype,
        # RECEIVER, like every sibling field arm: an indirect receiver reaches
        # its member through `is_arrow`, so a value-position deref here would
        # compose into `(*h)->value`.
        receiver=receiver,
        field_identity=direct_field(e, lc.analyzer, receiver),
        field_cpp=_field_cpp(e),
        is_arrow=(ptr_arrow if ptr_recv else _field_is_arrow(e, lc)),
        deref_check=ptr_recv and ptr_deref,
        narrowed_deref=narrowed_opt,
        form=Form.STORAGE,
        loc=getattr(e, "loc", None),
    ))


def _lower_subscript_source(e: TpySubscript, lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The ELEMENT twin of `_lower_field_source`: the storage-form container
    read (`::tpy::__getitem__(xs, 0)`) backing a borrow lift, where the
    element is itself a storage-form `std::optional<T>`. The receiver chain
    is vetted the same way -- an Optional intermediate link would need a
    `deref_optional_check` the prechecked recursion does not emit."""
    _reject_optional_receiver_link(e, lc, "subscript.opt_receiver")
    return _lower_expr(e, lc, declared, subscript_prechecked=True,
                       allow_whole_optional=True)

        # Param names live on `prescan.param_names` (the single copy): a
        # `str`-typed PARAM name is a `std::string_view` in the C++ signature
        # while an owned str LOCAL of the same resolved type is a `std::string`
        # -- the str name-form classifier needs the distinction (see
        # _str_name_form), and the aug-append gate excludes params the same way
        # (see _str_aug_append_ok).


def _is_move_source(value: TpyExpr, lc: _LowerCtx,
                    movable_names: 'set[str] | None' = None) -> bool:
    """Whether a write / return / MIL source moves rather than copies: the last use
    of a movable (owned) name -- peel `TpyCoerce`, then a `TpyName` in the
    movable set whose node is a last use.
    `movable_names` defaults to the function's `movable_locals` (the
    F2b/F2e write/return case -- only an F2d REBIND_SLOT local is owned there); the
    ctor MIL passes `own_param_names` instead (M3b-move), since no locals exist yet at
    MIL time (the MIL runs before the body) and its movable sources are the Own params."""
    if movable_names is None:
        return _is_move_source_facts(value, lc.movable_locals, lc.analyzer,
                                     getattr(lc.func, "name", None))
    inner = _peel_coerce(value)
    return (isinstance(inner, TpyName)
            and inner.name in movable_names
            and inner in lc.analyzer.ctx.all_last_uses)
