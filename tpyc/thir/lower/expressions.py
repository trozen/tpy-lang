"""Expression lowering and admission for `_lower_expr` and its recursive arms.

Composite consumer-shape helpers live in `checks.py`; `_lower_expr` invokes them
only from the node arm being lowered.
"""

from __future__ import annotations
import math
from dataclasses import field, fields as dataclass_fields, replace
from ... import qnames
from ...parse.nodes import (
    FSTRING_CONV_NONE,
    FSTRING_CONV_REPR,
    FSTRING_CONV_STR,
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
    TpyUnaryOp,
    TpyVarargPack,
)
from ...typesys import (
    RecursiveAliasInstanceType,
    AnyType,
    BOOL,
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
)
from ...codegen_cpp.builtins import _FLOAT_STR_CONSTANTS
from ...codegen_cpp.types import TypeResolver, resolve_pending_container
from ...modules.defs import BINOP_TO_METHOD
from ...modules.type_resolution import (
    get_iterable_element_type,
    is_native_iterable,
)
from ...codegen_cpp.context import (
    enum_cpp_name,
    escape_cpp_name,
    qualified_cpp_name,
    qualify_native_name,
    view_key_target,
)
from ...codegen_cpp.protocols import dynamic_base_name, narrow_cast_rhs
from ...typesys import (
    polymorphic_source_inner,
    polymorphic_source_is_pointer,
)
from ...symbol_binding import SymbolKind, lookup_imported
from ...coercions import wrap_into_any, CoercionContext
from ...compilation_context import get_current_compiler
from ...value_category import call_returns_cpp_ref, is_rvalue_source
from ..fallback import ThirUnsupported, expr_kind_tag, note_detail
from ..faces import witness as _witness
from ...codegen_cpp.expressions import ExpressionGenerator
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
    THIRFString,
    THIRFStringArg,
    THIRIsNone,
    THIRLambda,
    THIRMembership,
    THIRStrMembership,
    THIRTupleMembership,
    THIRTruthy,
    THIROptViewArg,
    THIRListRepeat,
    THIRCopy,
    THIRLiteral,
    THIRMethodCall,
    THIRModuleVar,
    THIRMove,
    THIRName,
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
    _tparam_protocol_field_recv_ok,
    _tparam_protocol_field_over_field_ok,
    _call_ret_union_ok,
    _ptr_opt_borrow_call_ret,
    _BIGINT_LIT_COERCION,
    _BIGINT_NARROW,
    _eligible_ptr_union,
    _eligible_wrapper_union,
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
    _bare_module_recv,
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
    _set_method_recv,
    _container_scalar_read,
    _container_value_leaf_read,
    _container_value_opt_scalar_elem,
    _const_index,
    _ctor_arg_slot_ok,
    _dict_view_iterable_ok,
    _eligible_char,
    _field_decl_type,
    _eligible_enum,
    _eligible_ptr_value,
    _eligible_scalar,
    _enum_compare_pair,
    _enum_member_cpp,
    _enum_neg_wrap,
    _enum_prop_wrap,
    _enum_truthy_wrap,
    _plain_enum_truthy,
    _truthiness_mode,
    _f1_const_rooted_source,
    _plain_container_read,
    _f1_record,
    _f1_tuple,
    _field_read_ref_ctor_arg,
    _field_over_global_record_ok,
    _field_over_subscript_ok,
    _field_markers_clean,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    _tuple_literal_has_ref_elements,
    _tuple_elem_slots_ptr_optional,
    _unbound_self_field_ok,
    _ptr_value_field_recv_ok,
    _ptr_value_none_field,
    _user_deref_field_recv_ok,
    _user_deref_method_call_ok,
    _typed_dict_recv_ok,
    _user_deref_stub_method_ok,
    _global_record_recv,
    _folded_neg_int_literal,
    _generic_root_subst,
    _instantiation_call_fi,
    _is_borrow_form_name,
    _is_type_param_slot,
    _is_range_call,
    _native_iterable_range_arg,
    _is_none_compare_operand,
    _value_opt_rvalue,
    _narrowed_opt_field_read,
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
    _const_borrow_name,
    _poly_isinstance_value_info,
    _poly_subject_decl,
    _poly_subject_readonly,
    _optional_ptr_arg_slot,
    _optional_ptr_borrow,
    _optional_ptr_borrow_name,
    _optional_checked_field,
    _optional_field_over_subscript_ok,
    _optional_checked_field_over_field_ok,
    _field_over_record_getitem_ok,
    _record_getitem_idx_recv_ok,
    _own_lvalue_temp_slot,
    _operand_type,
    _peel_coerce,
    _type_family_tag,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_own_slot,
    _protocol_arg_slot,
    _protocol_arg_temp,
    _bounded_tparam_protocol,
    _protocol_binding,
    _protocol_subscript_recv,
    _range_counter_type,
    _record_call_rvalue_operand,
    _record_rvalue_temp_slot,
    _resolve_pending_view,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _resolved_viewfam_value,
    _runtime_bigint,
    _slice_object_type,
    _span_value,
    _str_compare_operand,
    _str_concat_operand,
    _str_field_value_read,
    _bytes_field_value_read,
    _str_name_form,
    _storage_call_container,
    _container_storage_return_call_ret,
    _owned_tuple_call_ret,
    _storage_call_ret,
    _record_getitem_key,
    _subscript_index_and_tuple,
    _subscript_container_recv_type,
    _template_init_call_fi,
    _view_ctor_bare_source,
    _viewfam_ctor_call_fi,
    _tuple_subscript_value_read,
    _tparam_value,
    _opt_view_arg_shim,
    _none_value_opt_arg,
    _str_literal_value_opt_arg,
    _value_opt_scalar_value_arg,
    _callable_value,
    _value_opt_scalar,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_bytes,
    _value_opt_view,
    _value_opt_owned_view,
    _value_tuple,
    _value_tuple_element_ok,
    _value_tuple_nested,
    _tuple_compare_pair,
    _value_tuple_global,
    _value_tuple_return,
    _ru_container_literal_ok,
    _ru_wrapper_member_name_arg,
    _ru_wrapper_arg_slot,
    _value_union_temp_slot,
    _union_binding_divergent,
    _any_compare_pair,
    _any_none_subject,
    _union_none_name,
    _union_compare_pair,
    _record_compare_pair,
    _unrouted_binding_read,
    _unwrap_lit_coerce,
    _value_opt_view_name,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx, _RecordCtorUse
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
    _alias_ref_container,
    _coro_factory_structural_arg,
    _deref_coerce_arg,
    _iter_rvalue_structural_arg,
    _opt_own_record_name_arg,
    _readonly_container_rvalue_arg,
    copy_plain_record_source,
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
    _own_container_literal_arg,
    _tuple_literal_arg,
    _ref_param_dictset_literal_arg,
    _container_method_arg_ok,
    _stub_method_ret_ok,
    _native_iterable_iterator_call_arg,
    _native_iterable_comp_arg,
    _native_iterable_literal_arg,
    _native_protocol_field_arg,
    _borrow_tuple_field_arg,
    _borrow_tuple_name_arg,
    _container_comp_arg,
    _record_borrow_call_arg,
    _record_elem_subscript_arg,
    _record_getitem_rvalue_arg,
    _record_field_ref_arg,
    _container_lit_slot_family,
    _container_literal_shape_ok,
    _ctor_instantiation_ok,
    _ctor_effective_params,
    _ctor_shape_ok,
    _dyn_own_coro_factory_arg,
    _dyn_own_conformer_arg,
    _covariant_temp_arg,
    _dyn_own_handle_arg,
    _field_over_call_ok,
    _field_over_property_call_ok,
    _field_over_binop_ok,
    _field_over_walrus_ok,
    _field_over_container_subscript_ok,
    _func_ref_routable,
    _lambda_routable,
    _subscript_over_container_subscript_ok,
    _field_over_field_ok,
    _free_callee_kind,
    _fstring_arg_wrap,
    _generic_plain_arg_ok,
    _is_len_call,
    _is_len_native,
    _iter_proto_call_ret,
    _marker_call_kind,
    _marker_call_supported,
    _marker_reject,
    _module_qual_ctor_shape,
    _member_gen_call_iterable_ok,
    _method_call_arg_ok,
    _method_recv_family,
    _none_unit_arg,
    _raw_record_method_fi,
    _tparam_name_pass_arg,
    _tparam_slot_temp_arg,
    _method_nonname_receiver_ok,
    _method_receiver_type,
    _native_call_arg_ok,
    _native_container_call_arg,
    _native_record_call_arg,
    _optional_ptr_arg,
    _required_protocol_union_slot,
    _union_member_lift_arg,
    _union_pass_through_arg,
    _union_coerced_literal_arg,
    _own_union_ctor_arg,
    _own_lvalue_arg,
    _own_move_arg,
    _own_dyn_method_rvalue_ok,
    _value_record_rvalue_arg,
    _own_optional_record_rvalue_arg,
    _own_record_rvalue_arg,
    _protocol_slot_arg,
    _plain_call_arg_ok,
    _ptr_deref_method_call,
    _ptr_deref_recv_ok,
    _ptr_template_method_supported,
    _record_rvalue_call_shape,
    _native_record_rvalue_call_shape,
    _record_rvalue_temp_arg,
    _typed_dict_ctor_call,
    _native_ctx_manager_ok,
    _shared_pass_through_arg,
    _str_pass_through_arg,
    _strlit_method_pin_arg,
    _strlit_overload_pin_arg,
    _strlit_overload_pin_fires,
    _subscript_elem_reject,
    _subscript_recv_reject,
    _str_aug_append_ok,
    _str_list_method_iterable_ok,
    _record_method_call_supported,
    _recv_shape_reject,
    _value_tuple_pass_through_arg,
    _value_union_temp_arg,
)


_COPY_SRC_USE = _ExprUse(result=_ExprResultUse.BORROW_BIND)


def _lower_copy_record(e: TpyExpr, lc: '_LowerCtx',
                       declared: dict[str, TpyType], *,
                       slot_type: 'TpyType | None' = None,
                       exact: bool = False,
                       pointers: 'set[str] | None' = None,
                       use: _ExprUse = _COPY_SRC_USE,
                       loc: 'SourceLocation | None' = None
                       ) -> 'THIRCopy | None':
    """`copy(name)` of a plain F1-record as the copy-construct rvalue (`T(x)`,
    _gen_copy_expr's bare-record arm). Every sink that takes this row has to
    intercept it itself -- the special-builtin call gate rejects `copy()` in
    the generic call tail -- so decl init, setitem value, return and the
    `Own[record]` arg slot all land here. None = not this shape; the caller
    falls through to its own tail.

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
    crec = copy_plain_record_source(
        e, lc.analyzer, lc.pointers if pointers is None else pointers)
    if crec is None or (exact and crec != slot_type):
        return None
    return THIRCopy(result_type=crec if slot_type is None else slot_type,
                    value=_lower_expr(e.args[0], lc, declared, use=use),
                    cpp_type=lc.render_type(crec), form=Form.STORAGE, loc=loc)


def _call_use_supported(e: TpyCall, lc: '_LowerCtx',
                        declared: dict[str, TpyType], use: _ExprUse,
                        allow_whole_optional: bool = False) -> bool:
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
        ok = result is _ExprResultUse.STORAGE
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
              # An `Any`-returning call: `tpy::Any` is a value type returned by
              # value, landing bare in a value / storage slot.
              or isinstance(record, AnyType)
              # An open-T result inside a generic body (`make_default()`,
              # `copy(self.value)` -- Own[T] unwraps to T): renders by name,
              # the composing position gates its own family.
              or _tparam_value(ret)
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
              or (use.ptr_opt_passthrough
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_opt_passthrough"))
              # The `Optional[record]` FIELD-write sink: the same borrowed
              # `T*` result, but lifted by the sink (`ptr_to_optional(...)`)
              # rather than landing bare.
              or (use.ptr_opt_lift
                  and _ptr_opt_borrow_call_ret(e, ret)
                  and _witness("call.ptr_opt_lift"))
              or (result is _ExprResultUse.DISCARD
                  and is_void_like_type(ret))
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
              # A union-returning call lands bare in its same-union slot: a
              # ptr-variant return copies the variant by value
              # (`got = cycle(start)`), an `Own[union]` factory fills the
              # UNION_RVALUE storage slot (`__slot_N = make_pet(..)` --
              # the decl arm's BORROW_BIND init).
              or (result in (_ExprResultUse.STORAGE,
                             _ExprResultUse.BORROW_BIND)
                  and _call_ret_union_ok(ret, analyzer))
              or (result is _ExprResultUse.STORAGE and use.tuple_source
                  and _owned_tuple_call_ret(ret, analyzer) is not None)
              # An F1 BORROW-tuple call result (`first_two(xs) ->
              # tuple[Box, Box]` returning `std::tuple<Box*, Box*>`) at
              # the tuple-source sink: the call renders bare into the
              # `auto __tup_N = <call>;` capture and the unpack's alias
              # targets re-point off the elements; tuple_source-only, so
              # no other consumer can bind the borrow tuple.
              or (result is _ExprResultUse.STORAGE and use.tuple_source
                  and _f1_tuple(ret, analyzer) is not None)
              # A value-tuple call result (`split(p)`) in a plain VALUE
              # position (a call arg / nested expr): the tuple is a value
              # type returned by value and renders bare, binding a
              # `const std::tuple<...>&` slot directly. The STORAGE decl sink
              # rides the tuple_source arm above; other consumers gate their
              # own shape.
              or (result is _ExprResultUse.VALUE
                  and _value_tuple(ret, analyzer) is not None)
              # The borrow-tuple local decl (`auto p = pair_of(b);`): the
              # `auto` slot binds the pointer-repr result whole, so the call
              # renders bare with no form conversion.
              or (use.btuple_slot and _f1_tuple(ret, analyzer) is not None
                  and _witness("call.btuple_slot"))
              or (result is _ExprResultUse.BORROW_BIND
                  and _f1_record(record, analyzer))
              # An async-def FACTORY call under the make_adapter wrap: the
              # concrete coro frame is consumed whole by the adapter.
              or (use.coro_factory and fi is not None and fi.is_async)
              # A sync `with` manager that resolves to a @native record-
              # returning call (`with open(path, mode)`): stored in the
              # `__ctx_N` slot via the native free-call arm.
              or (use.ctx_manager and _native_ctx_manager_ok(e, analyzer))
              or _record_rvalue_call_shape(e, analyzer)
              # A @native free call returning a by-value record (`f =
              # open(path)`): the native residue of the plain record-rvalue
              # arm above, landing bare in a STORAGE value slot.
              or (result is _ExprResultUse.STORAGE
                  and _native_record_rvalue_call_shape(e, analyzer)))
        if not ok:
            note_detail(_call_ret_reject(e, ret, analyzer))
    if result is _ExprResultUse.CONDITION:
        return ok and is_bool_type(lc.analyzer.get_expr_type(e))
    return ok


def _record_ctor_shape_supported(e: TpyCall, lc: '_LowerCtx',
                                 use: _ExprUse) -> bool:
    if _ctor_shape_ok(e, lc.analyzer):
        return True
    # The instantiation render (`type_to_cpp(call_type)(args)`) is
    # position-independent on the AST path, so NESTED_ARG positions admit it
    # too (`Rc.new(Box(Box(Dog(..))))` -- the inner Box); its ARGS still
    # gate per position (a temp-needing arg without the ridden flush right
    # rejects in the arg rows, not here).
    return _ctor_instantiation_ok(e, lc.analyzer)


def _protocol_union_ctor_arg(arg: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType],
                             analyzer) -> 'str | None':
    """A NAME into a ctor slot whose non-None members are all PROTOCOLS:
    an F1-record name, a Span name, and a builtin-container name all take
    the address-of lift (`&(a)` / `&(s)` / `&(words)` -- the C++ ctor's
    protocol overload binds the pointer; a bare record render was
    probe-caught divergent, the corpus's seeming bare witness was
    `copy(a)`'s copy-construct, a different construct). Returns 'addr'
    or None."""
    if not isinstance(arg, TpyName) or arg.name not in locals_:
        return None
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if isinstance(slot, OptionalType):
        # `Iterable[str] | None` normalizes to Optional[protocol]: the same
        # protocol-overload pointer bind, so the same addr lift.
        members = [unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            slot.inner)))]
    elif isinstance(slot, UnionType):
        members = [m for m in slot.members if not is_void_like_type(m)]
        if len(members) == len(slot.members):
            # `_gen_protocol_arg` splits on has_none, NOT on the call kind: a
            # REQUIRED protocol union monomorphizes to one template param and
            # takes the plain `gen_expr_deref` render. Only the nullable form
            # (a None member, or the Optional normalization above) lifts.
            return None
    else:
        return None
    if not members or not all(
            isinstance(m, NominalType) and m.is_protocol for m in members):
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        locals_[arg.name])))
    if (_f1_record(at, analyzer) or is_span(at)
            or is_list(at) or is_dict(at) or is_set(at)):
        return "addr"
    return None


def _own_move_source_slice(a: TpyExpr, ptype: 'TpyType | None',
                           lc: '_LowerCtx',
                           declared: dict[str, TpyType]) -> bool:
    """The temp-free MOVE-SOURCE slice of the Own-slot copy+move row: a bare
    non-self, non-narrowed NAME of NON-VALUE payload at its last movable use
    into an eligible Own slot -- renders `std::move(name)` position-
    independently. Shared by the NESTED ctor tail (which admits exactly this
    slice; the flushable copy half stays DIRECT-only) and `_lower_call_arg`'s
    Own-slot arm (which picks THIRMove on the same facts), so the two cannot
    drift."""
    if _own_lvalue_temp_slot(a, ptype, lc.analyzer) is None:
        return False
    if not isinstance(a, TpyName) or a.name == "self":
        return False
    if (a.name in lc.narrow.narrowed or a.name in lc.inline_narrowed
            or a.name not in declared):
        return False
    if not _is_move_source(a, lc):
        return False
    at = lc.analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    return at is not None and not at.is_value_type()


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


def _record_ctor_arg_supported(
        arg: TpyExpr, param_type: TpyType, index: int, fi,
        lc: '_LowerCtx', declared: dict[str, TpyType], use: _ExprUse) -> bool:
    # fi is None for a TypedDict ctor (no synthetic constructor fi; params
    # come from the registry's init_params, which carry no mutation facts).
    analyzer = lc.analyzer
    mutation_unknown = fi is not None and fi.mutated_params is None
    mutated = (fi.mutated_params if fi is not None else None) or frozenset()
    is_mutated = index in mutated
    if use.record_ctor is not _RecordCtorUse.NESTED_ARG or use.allow_temps:
        # temps_ok tracks whether THIS ctor position flushes. DIRECT threads
        # its own allow_temps; a RECORD_TEMP source ctor flushes at the
        # enclosing statement too when it was reached via the flush-enabled
        # recursion (`_RECORD_TEMP_FLUSH_USE`), so `use.allow_temps` is the
        # single source of truth for both. A NESTED_ARG ctor with the ridden
        # flush right (allow_temps threads through call-shaped args) gates
        # like DIRECT -- its temps flush at the same enclosing statement;
        # the restricted branch below serves only flush-less nested slots.
        temps_ok = use.allow_temps
        if _str_pass_through_arg(
                arg, param_type, declared, analyzer, mutated=is_mutated):
            _witness("ctor.str_arg")
            return True
        return (_shared_pass_through_arg(
                    arg, param_type, declared, analyzer, mutated=is_mutated)
                # A scalar VALUE into a value-repr Optional[scalar] ctor slot
                # renders bare (the implicit std::optional ctor); `None` rides
                # the row below -- mirrors the method-arg loop.
                or _value_opt_scalar_value_arg(arg, param_type, analyzer)
                or (_value_opt_pass_arg(arg, param_type, declared,
                                        lc.narrow.narrowed, analyzer)
                    and _witness("ctor.value_opt_pass_arg"))
                # A str LITERAL into a value-repr Optional[str] ctor slot
                # (the total=False TypedDict face) renders bare the same way.
                or _str_literal_value_opt_arg(arg, param_type)
                or _none_value_opt_arg(arg, param_type, analyzer) is not None
                # `Box(None)`: the unit ctor arg renders the bare
                # `std::monostate{}` like the marker-call row.
                or _none_unit_arg(arg, param_type) is not None
                # A nested open-T instantiation inside a generic body
                # (`self.inner = Box[T](value)`): a NAME bound to the same
                # bare T passes the form-neutral slot bare, the ctor-face
                # sibling of the nested generic call rule.
                or _tparam_name_pass_arg(arg, param_type, declared)
                # The Own-slot cascade rows, mirrored from the free/method
                # plain-arg loop: the temp-free last-use move lands in any
                # position; the copy half hoists `__tmp_N` and so needs the
                # enclosing flush point (temps_ok), exactly like the
                # record-rvalue temp row below.
                or (_own_move_arg(arg, param_type, declared, analyzer)
                    and _witness("ctor.own_arg"))
                or (temps_ok
                    and _own_lvalue_arg(
                        arg, param_type, declared,
                        frozenset(lc.narrow.narrowed), analyzer)
                    and _witness("ctor.own_arg"))
                # The temp-free move-source slice also serves the flush-LESS
                # direct position (`[Box(h1)]` -- a ctor element in a container
                # literal): `std::move(name)` is position-independent, exactly
                # the NESTED tail's admission.
                or (not temps_ok
                    and _own_move_source_slice(arg, param_type, lc, declared)
                    and _witness("ctor.own_arg"))
                # A record/container-returning CALL rvalue into an Own slot
                # (`Appender(data.clone(), ..)`): binds the T&& slot inline,
                # exactly the free/method plain-arg row -- same predicate,
                # same bare render in `_lower_call_arg`'s Own-slot arms.
                or _own_record_rvalue_arg(arg, param_type, declared, analyzer)
                or (_field_read_ref_ctor_arg(arg, param_type, declared,
                                             analyzer, mutated=is_mutated)
                    and _witness("ctor.field_read_ref_arg"))
                or (not is_mutated
                    and _container_literal_arg(arg, param_type, analyzer)
                    and _witness("ctor.container_literal_arg"))
                # A list literal into an `Own[list]` ctor slot renders the
                # same bare in-place brace (`Summer([1, 2, 3])` ->
                # `Summer({1, 2, 3})` -- prvalue into the by-value Own slot),
                # the qualcall row's ctor face.
                or (_own_container_literal_arg(arg, param_type, analyzer)
                    and _witness("ctor.container_literal_arg"))
                # The pointer-variant union rows, mirrored from the plain
                # arg loop: a member-typed record NAME / None lifts bare
                # (`pv{&(name)}` -- temp-free); a member ctor RVALUE hoists a
                # named temp and lifts its address (`pv{&__tmp_N}`), so it
                # needs the enclosing flush point (temps_ok). Both lower
                # through `_lower_union_arg_lift` in `_lower_call_arg`.
                or _union_member_lift_arg(arg, param_type, declared, analyzer)
                or (temps_ok
                    and _union_ctor_temp_arg(arg, param_type, analyzer))
                # The remaining free-call union rows, same lowering arms:
                # an already-union NAME passes bare into a same-union slot
                # (`Zoo(init_pet, ..)`); a coerced union literal renders its
                # member; a member ctor RVALUE into an `Own[union]` slot
                # binds the bare expansion (`Sink(A(7))`).
                or (_union_pass_through_arg(arg, param_type, declared,
                                            analyzer)
                    and _witness("ctor.union_pass_arg"))
                or _union_coerced_literal_arg(arg, param_type, declared,
                                              analyzer)
                or _own_union_ctor_arg(arg, param_type, declared, analyzer)
                # A record/Span NAME into a slot whose non-None members are
                # all PROTOCOLS (`ArrayList[Int32, 8](a)` / `(s)` -- `src:
                # Iterable[Own[T]] | Spannable[T] | None`): both take the
                # address-of lift (the arm in `_lower_call_arg`).
                or (_protocol_union_ctor_arg(arg, param_type, declared,
                                             analyzer) is not None
                    and _witness("ctor.protocol_union_arg"))
                # A record RVALUE into an `Own[record | None]` slot binds bare
                # (`Outer("a", Inner(42))` -- prvalue -> optional<Inner>), the
                # @dataclass Optional-record-field row.
                or _own_optional_record_rvalue_arg(arg, param_type, analyzer)
                # A record rvalue into a BY-VALUE record slot (a ValueType
                # record param -- `timezone(timedelta(...), "IST")`): no ref
                # param, no temp cascade, bare on both paths.
                or _value_record_rvalue_arg(arg, param_type, analyzer)
                # A member-valued arg into a VALUE-union ctor slot hoists the
                # `std::variant<...> __tmp_N = v;` temp (`datetime(...,
                # tzinfo=ist)`) -- the free-call arg-temp row, flush-gated.
                or (temps_ok and _value_union_temp_arg(
                    arg, param_type, declared,
                    frozenset(lc.narrow.narrowed), analyzer))
                # A tuple LITERAL at a tuple ctor slot: the borrow/value tuple
                # builders own the per-element admission (a bad element raises
                # inside lowering and falls the body back whole), exactly the
                # free-call row -- the gate checks only the slot/arity pairing.
                or _tuple_literal_arg(arg, param_type)
                # A routable lambda into a Callable ctor slot renders its
                # inline closure, temp-free -- the free-call ladder's row.
                or (_lambda_routable(arg, analyzer)
                    and _witness("ctor.lambda_arg"))
                # The pointer-repr Optional slot faces (`n` into a `record |
                # None` ctor param -> `&(n)`), mirroring the free-call gate's
                # `_optional_ptr_arg` row: `_gen_record_ctor_args` runs the same
                # `_gen_optional_ptr_arg` dispatch as the plain call loop. The
                # temp-bearing 'ctor' face is temps_ok-gated like the rows above.
                or _optional_ptr_arg(arg, param_type, declared, analyzer,
                                     temps_ok=temps_ok)
                # A protocol-conformer arg into a @dynamic/structural protocol
                # ctor slot: a bare NAME / already-protocol lvalue passes
                # through, and a @dynamic RVALUE hoists the adapter temp
                # (`_gen_dynamic_protocol_arg` runs in the ctor loop too).
                # A STRUCTURAL rvalue is ctor-inline on the AST path
                # (`_gen_protocol_arg` hands single-required slots back to
                # gen_call_arg, and the ctor loop has no structural temp
                # arm) -- the shared temp row would diverge, so it rejects.
                or ((_pslot := _protocol_arg_slot(param_type)) is not None
                    and (isinstance(arg, TpyName)
                         or is_dyn_protocol(_pslot))
                    and _protocol_slot_arg(arg, param_type, declared,
                                           analyzer, temps_ok=temps_ok))
                # A concrete conformer into an `Own[@dynamic P]` ctor slot
                # (`Box(Dog(...))`): the make_unique / make_adapter wrap,
                # verdict-keyed via the shared classifier (the
                # `_dyn_own_conformer_arg` row in `_lower_call_arg`).
                or _dyn_own_conformer_arg(arg, param_type, declared,
                                          analyzer) is not None
                or (_record_rvalue_temp_arg(
                        arg, param_type, declared, analyzer)
                    and (temps_ok if is_mutated else True)
                    # The module-qualified ctor slice is const-slot-only: at
                    # a MUTATED ref slot the rec ArgTemp's init would lower
                    # with plain use and dead-end at the marker result gate
                    # -- reject honestly instead of admitting a dead path.
                    and not (is_mutated and isinstance(arg, TpyMethodCall))))

    slot = unwrap_readonly(unwrap_ref_type(param_type))
    if _eligible_scalar(slot):
        return _resolved_scalar(analyzer.get_expr_type(arg), analyzer)
    if _own_record_rvalue_arg(arg, param_type, declared, analyzer):
        # Temp-free like the const-rvalue row below (the rvalue binds the
        # T&& slot inline), so the nested position admits it too.
        return True
    if (_own_move_arg(arg, param_type, declared, analyzer)
            and _witness("ctor.own_arg")):
        # The temp-free last-use move (`std::move(name)`) is
        # position-independent, so the nested position admits it too; the
        # flushable copy half stays DIRECT-only.
        return True
    if _own_move_source_slice(arg, param_type, lc, declared):
        # A body-movable local at its last use: lowering's Own-slot arm
        # picks the temp-free `std::move(name)` (`Producer(tx, 0)` nested
        # in `spawn(...)`), so the nested position admits exactly the
        # move-source slice of the copy+move row.
        _witness("ctor.own_arg")
        return True
    if _dyn_own_conformer_arg(arg, param_type, declared,
                              analyzer) is not None:
        # The make_unique / make_adapter conformer wraps are temp-free
        # in-place renders (`Box(Box(Dog(..)))` -- the inner Box's Dog arg),
        # so the nested position admits them like the rvalue rows above.
        return True
    if _value_record_rvalue_arg(arg, param_type, analyzer):
        # A record rvalue into a BY-VALUE record slot renders bare
        # (`timezone(timedelta(...))` -- no ref param, no temp), so the
        # nested position admits it like the direct loop.
        return True
    if (_record_rvalue_temp_slot(arg, param_type, analyzer) is not None
            and not is_mutated):
        if not isinstance(arg, TpyCall):
            return False
        arg_fi = arg.resolved_function_info
        if arg_fi is not None and arg_fi.is_constructor:
            if not _ctor_shape_ok(arg, analyzer):
                return False
        elif not _record_rvalue_call_shape(arg, analyzer):
            return False
        _witness("ctor.const_rvalue_arg")
        return True
    slot_type = unwrap_send_sync(slot)
    if (isinstance(slot_type, NominalType) and is_string_type(slot_type)
            and (mutation_unknown or is_mutated)):
        return False
    if not _str_pass_through_arg(arg, param_type, declared, analyzer):
        return False
    _witness("ctor.str_arg")
    return True


def _require_method_call_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool, error_return_ok: bool = False) -> None:
    if not _method_call_arg_ok(
            e, a, ptype, index, declared, lc.analyzer,
            temps_ok=temp_args, narrowed=frozenset(lc.narrow.narrowed),
            param_names=lc.prescan.param_names,
            tparam_bounds=lc.tparam_bounds,
            error_return_ok=error_return_ok):
        raise ThirUnsupported("expr.method_call")


def _lower_marker_method_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool, error_return_ok: bool = False) -> THIRExpr:
    if isinstance(a, TpyVarargPack):
        # A `*args` pack into a variadic module function (math.hypot(3, 4)):
        # the qualcall arg loop forwards the pack unchanged, rendered via the
        # same _gen_vararg_pack helper as a free call. The pack's own lowering
        # validates the element shapes and raises otherwise.
        return _lower_vararg_pack(a, ptype, lc, declared, temp_args=temp_args)
    _require_method_call_arg(
        e, a, ptype, index, lc, declared, temp_args=temp_args,
        error_return_ok=error_return_ok)
    return _lower_call_arg(
        a, ptype, lc, declared, temp_args=temp_args)



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


def _str_slice_receiver_supported(
        recv: TpyExpr, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> bool:
    analyzer = lc.analyzer
    if isinstance(recv, TpyBytesLiteral):
        # A bytes-literal receiver renders OWNED (`::tpy::bytes_literal_owned`),
        # the `{self}` substitution the slice @cpp_template threads -- matching
        # the AST's target-less default for the literal.
        return True
    if isinstance(recv, TpyName):
        return (recv.name in declared
                and _resolved_viewfam_value(
                    declared[recv.name], analyzer) is not None)
    if isinstance(recv, TpyFieldAccess):
        return (_field_receiver_ok(recv, declared, analyzer)
                and _resolved_viewfam_value(
                    analyzer.get_expr_type(recv), analyzer) is not None)
    if isinstance(recv, TpyCall):
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
    rt = _operand_type(e.right, declared, analyzer)
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
    lt = _operand_type(e.left, declared, analyzer)
    if lt is None:
        return False
    return (_resolved_bytes_value(lt, analyzer) is not None
            or _resolved_scalar(lt, analyzer))


def _str_membership_ok(e, declared: dict[str, TpyType], analyzer) -> bool:
    """`needle in s` / `not in` over a str-family container (str/String/StrView):
    no `__contains__` member, so the AST renders `(s.find(needle) != npos)` /
    `== npos` (`not in`), the `is_any_str_type` arm of _gen_binop. The receiver
    is a lowerable str value -- a local NAME, an F1-field read, or a str LITERAL
    (wrapped in string_view at emit). The needle is a char or str value, which
    renders bare into `.find(...)` on both paths. Takes precedence over the
    generic `resolved_contains` arm only when there is no such member (str has
    none), so the two never overlap."""
    if (e.op not in _MEMBERSHIP_OPS or isinstance(e.right, TpyTupleLiteral)
            or e.resolved_contains is not None
            or e.typed_dict_in_field is not None):
        return False
    rt = _operand_type(e.right, declared, analyzer)
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
    lt = _operand_type(e.left, declared, analyzer)
    if lt is None:
        return False
    return (_eligible_char(lt)
            or _resolved_str_value(lt, analyzer) is not None)


def _container_slice_recv_ok(recv: TpyExpr, lc: '_LowerCtx',
                             declared: dict[str, TpyType]) -> bool:
    """The receiver of a list/Array/Span slice read (`items[a:b:c]` ->
    `list_slice`/`list_stepped_slice`): a bare-name / F1-field container the
    `__getitem__` @cpp_template threads unchanged (mirrors _gen_subscript's
    `subscript_obj`). The owned-list result renders STORAGE at every sink."""
    analyzer = lc.analyzer
    if isinstance(recv, TpyName):
        t = declared.get(recv.name)
    elif isinstance(recv, TpyFieldAccess):
        if not _field_receiver_ok(recv, declared, analyzer):
            return False
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
    """Mirror ExpressionGenerator._tuple_subscript_yields_borrow_ptr: `std::get<N>(t)`
    is a bare `T*` (member access `->`) iff element N is a plain non-value BORROW_REF
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
        if sub.obj.name in lc.storage_tuple_locals:
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
    return (isinstance(obj, TpyName)
            and (_ptr_read_derefs(obj.name, lc)
                 or (obj.name == lc.self_receiver and lc.self_is_pointer
                     # A poly-narrowed `self` reads through the cast
                     # pointer's DEREF (`(*__self_ptr)`), so the access is
                     # `.` -- `this->` belongs to the un-narrowed spelling.
                     and obj.name not in lc.narrow.spelled)))

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
    unary operand the AST unwraps through the runtime-checked
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
    `list[Int32 | None]`) wraps the whole-optional element read the same way."""
    analyzer = lc.analyzer
    if (isinstance(e, TpySubscript) and not isinstance(e.index, TpySlice)
            and e.slice_function_info is None):
        et = analyzer.get_expr_type(e)
        opt = _value_opt_scalar(et, analyzer) if et is not None else None
        if opt is None or not _container_value_opt_scalar_elem(
                _subscript_container_recv_type(e.obj, declared, analyzer),
                analyzer):
            return None
        lowered = _lower_expr(e, lc, declared, allow_whole_optional=True)
        if not isinstance(lowered, THIRSubscript):
            return None
        return replace(lowered, result_type=opt.inner, opt_deref_check=True)
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
    (`_operand_type` returns the latter). Un-narrowed reads keep the
    declared type -- sema rejects a bare Optional compare anyway."""
    if not (isinstance(e, TpyName) and _value_opt_scalar_binding(e.name, lc)):
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
    """A list/set whose elements are scalar/str values, or None -- the
    container `==` renders member-wise via the std container operator."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
         if t is not None else None)
    if u is None or not (is_list(u) or is_set(u)):
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
    (`x == y` with `x: Int32 | None`): C++ `std::optional`'s native mixed
    comparison, both sides rendered bare -- the AST's `_comparison_targets`
    optional_safe_eq arms. Returns the (left, right) operand TARGET types
    (the plain side opposite an UN-narrowed optional is target-typed to that
    optional's inner -- drives char/numeric literal renders; every other
    slot is None), or None when the pair is outside the slice. `lt`/`rt`
    arrive post-`_narrowed_opt_operand`, so a NARROWED optional side is
    already its plain inner -- exactly the AST's resolved-vs-analyzed split
    (a narrowed side derefs at the name arm, no target needed). Only
    scalar/Char/enum inners are admitted (`_value_opt_scalar`); the
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
                # this optional branch). Subscripts/ternaries stay AST.
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
        # A single-char str literal opposite an Optional[Char] renders the
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
        t = _operand_type(operand, declared, analyzer)
        if t is None:
            continue
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if contains_type_param(t):
            return ".tparam"
        if isinstance(t, NominalType) and t.is_user_record and not fam:
            fam = ".genrec" if t.type_args else ".record"
    return fam


def _sv_at_runtime(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """Mirror ExpressionGenerator._is_str_view_at_runtime for the
    value-select cast decisions: whether the operand spells
    std::string_view at C++ runtime (a `str` param, a str literal, a
    StrView value; and/or / ternary chains recurse)."""
    analyzer = lc.analyzer
    if isinstance(e, TpyName):
        pt = next((t for n, t in lc.params if n == e.name), None)
        if pt is not None and is_str_type(
                unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))):
            return True
        rs = _resolved_str_value(analyzer.get_expr_type(e), analyzer)
        return rs is not None and is_str_view_type(rs)
    if isinstance(e, TpyStrLiteral):
        return True
    if isinstance(e, TpyBinOp) and e.op in _LOGICAL_OPS:
        return _sv_at_runtime(e.left, lc) and _sv_at_runtime(e.right, lc)
    if isinstance(e, TpyIfExpr):
        return (_sv_at_runtime(e.then_expr, lc)
                and _sv_at_runtime(e.else_expr, lc))
    rs = _resolved_str_value(analyzer.get_expr_type(e), analyzer)
    return rs is not None and is_str_view_type(rs)


def _contains_isinstance_fact(e: TpyExpr) -> bool:
    """Whether an isinstance call sits anywhere in the operand tree -- the
    AST's inline-fact collection would narrow the RHS render, unmirrored."""
    if (isinstance(e, TpyCall)
            and (e.isinstance_var is not None
                 or e.isinstance_type is not None)):
        return True
    return isinstance(e, TpyExpr) and any(
        _contains_isinstance_fact(c) for c in e.children())


def _lower_value_select(e: TpyBinOp, rtype: 'TpyType | None',
                        lc: '_LowerCtx', declared: dict[str, TpyType],
                        loc) -> THIRExpr:
    """Value-position and/or -> THIRValueSelect (_gen_logical_value's VALUE
    slice, tier A: scalar / float / BigInt / str results). Record results,
    the rvalue-RHS pointer-select, inline-isinstance LHS facts, and every
    non-tier-A truthiness family raise (whole-body fallback)."""
    analyzer = lc.analyzer

    def rej(d: str) -> None:
        note_detail(d)
        raise ThirUnsupported(f"binop.shape.{e.op}", detail=True)

    res_str = _resolved_str_value(rtype, analyzer)
    res_owned = _is_string_owned(rtype)
    if not (_resolved_scalar(rtype, analyzer) or res_str is not None
            or res_owned):
        rej("valuesel.result_type")
    if _contains_isinstance_fact(e.left):
        rej("valuesel.isinstance_lhs")
    lt_res = analyzer.get_expr_type(e.left)
    rt_res = analyzer.get_expr_type(e.right)
    l_str = _resolved_str_value(lt_res, analyzer)
    l_owned = _is_string_owned(unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(lt_res)))) if lt_res is not None else False
    if l_str is not None or l_owned:
        truthy_nonempty = True
    elif _resolved_scalar(lt_res, analyzer):
        truthy_nonempty = False
    else:
        rej("valuesel.lhs_truthy")
    lowered_lhs = _lower_expr(e.left, lc, declared,
                              use=_ExprUse(literal_fold_ok=True))
    lhs_temp_cpp = None
    if not isinstance(e.left, TpyName):
        lhs_temp_cpp = ("std::string_view"
                        if l_str is not None
                        and isinstance(e.left, TpyStrLiteral)
                        else "auto&&")
    lowered_rhs = _lower_expr(e.right, lc, declared,
                              use=_ExprUse(literal_fold_ok=True))
    r_str = _resolved_str_value(rt_res, analyzer)
    rhs_sv = (not isinstance(e.right, TpyName) and r_str is not None
              and isinstance(e.right, TpyStrLiteral))
    # The mixed-operand conversion wraps: mirror _gen_logical_value's
    # lhs_cpp_cmp/rhs_cpp_cmp comparison over the runtime spellings.
    def _cmp_cpp(side: TpyExpr, st: 'TpyType | None',
                 s_str) -> str:
        if s_str is not None and _sv_at_runtime(side, lc):
            return "std::string_view"
        stu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
               if st is not None else None)
        if stu is None:
            rej("valuesel.operand_type")
        stu = resolve_int_literals(stu, analyzer.ctx.default_int_for_literal)
        return lc.render_type(stu)

    lhs_cmp = _cmp_cpp(e.left, lt_res, l_str)
    rhs_cmp = _cmp_cpp(e.right, rt_res, r_str)
    lhs_cast = rhs_cast = None
    if lhs_cmp != rhs_cmp:
        rtu = resolve_int_literals(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype))),
            analyzer.ctx.default_int_for_literal)
        cpp_result = lc.render_type(rtu)
        if cpp_result != lhs_cmp:
            lhs_cast = cpp_result
        if cpp_result != rhs_cmp:
            rhs_cast = cpp_result
    # A str-family select's FORM tag drives the owned-sink copy exactly
    # like a name read: the select's runtime spelling is the common operand
    # spelling (the cast target when the operands differed) -- a
    # string_view result is BORROW (an owned decl / return wraps
    # `std::string(...)` around it), an owned spelling STORAGE (no
    # re-copy). Scalars are plain VALUE.
    if res_str is not None or res_owned:
        common_cpp = (lhs_cmp if lhs_cmp == rhs_cmp
                      else lc.render_type(resolve_int_literals(
                          unwrap_readonly(unwrap_ref_type(
                              unwrap_send_sync(rtype))),
                          analyzer.ctx.default_int_for_literal)))
        form = (Form.BORROW if common_cpp == "std::string_view"
                else Form.STORAGE)
    else:
        form = Form.VALUE
    _witness("binop.value_select")
    return THIRValueSelect(
        result_type=rtype, lhs=lowered_lhs, rhs=lowered_rhs, op=e.op,
        truthy_nonempty=truthy_nonempty, lhs_temp_cpp=lhs_temp_cpp,
        lhs_cast=lhs_cast, rhs_cast=rhs_cast, rhs_sv=rhs_sv,
        form=form, loc=loc)


def _lower_binop(e: TpyBinOp, rtype: 'TpyType | None', lc: '_LowerCtx',
                 declared: dict[str, TpyType], loc, *,
                 fold_ok: bool = False, slot_threaded: bool = False,
                 temps_ok: bool = False) -> THIRExpr:
    # `temps_ok` rides the enclosing use's allow_temps: operand temps flush
    # at the enclosing statement, so a flushable position's right extends
    # into call-shaped operands. Cond positions thread False (unchanged).
    analyzer = lc.analyzer

    def reject() -> None:
        raise ThirUnsupported(
            f"binop.shape.{e.op}"
            f"{_binop_operand_suffix(e, declared, analyzer)}",
            detail=True)

    # A `__contains__`-unresolved native-set membership routes via the AST's
    # `std::ranges::contains` fallback (set by the membership gate below).
    ranges_contains = False
    # A plain user `__contains__` member (no native spelling): the member
    # call renders `fi.name` instead of `fi.native_name`.
    user_contains = False
    rb = e.resolved_binop
    # sema's optional_safe_eq pair (value-repr Optional[scalar] ==/!=): the
    # per-side literal targets, or None outside the slice (set in the
    # compare arm, consumed by the operand render below).
    opt_eq_targets: 'tuple[TpyType | None, TpyType | None] | None' = None
    if e.op in _ARITH_OPS or e.op in _BITWISE_OPS:
        if rb is None or not getattr(rb.method, "cpp_template", None):
            if (rb is None or e.op not in ("+", "*")
                    or not rb.method.native_function
                    or not rb.method.native_name):
                reject()
            bt = _resolved_bytes_value(rtype, analyzer)
            lt = _operand_type(e.left, declared, analyzer)
            rt = _operand_type(e.right, declared, analyzer)
            if bt is None or not is_bytes_type(bt):
                reject()
            if e.op == "+":
                # bytes concat -> ::tpy::bytes_concat(l, r): both operands
                # bytes-family.
                if not (_bytes_concat_operand(e.left, lt, analyzer)
                        and _bytes_concat_operand(e.right, rt, analyzer)):
                    reject()
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
            lt = _operand_type(e.left, declared, analyzer)
            rt = _operand_type(e.right, declared, analyzer)
            if (not _str_concat_operand(e.left, lt, analyzer)
                    or not _str_concat_operand(e.right, rt, analyzer)):
                reject()
        elif e.op == "*" and _resolved_str_value(rtype, analyzer) is not None:
            # `s * n` / `n * s` -> str_repeat: one operand is a str value, the
            # other an int count. The resolved __mul__/__rmul__ cpp_template
            # (`::tpy::str_repeat({self}, {0})`) renders through the operator
            # arm; is_reverse pins the str into {self} for the reversed form.
            lt = _operand_type(e.left, declared, analyzer)
            rt = _operand_type(e.right, declared, analyzer)
            if _str_concat_operand(e.left, lt, analyzer):
                if not _resolved_scalar(rt, analyzer):
                    reject()
            elif not (_str_concat_operand(e.right, rt, analyzer)
                      and _resolved_scalar(lt, analyzer)):
                reject()
        elif (_f1_record(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                  rtype))) if rtype is not None else None, analyzer)
              and rb is not None):
            # A RECORD-result dunder (`a // b` -> Meters via the injected
            # `({self}).__floordiv__({0})` template; `td1 + td2` via the
            # @native operator template): the template render is shared with
            # the scalar path (_emit_binop mirrors _gen_binop_from_result,
            # is_reverse swap included). Admitted only where the consumer
            # pinned the record rvalue (print arg / field receiver).
            _witness("binop.record_dunder")
        elif not _resolved_scalar(rtype, analyzer):
            reject()
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
              and not isinstance(e.left, TpyName)
              and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
              and not isinstance(e.right, TpyName)):
            # The AST constant-folds a both-literal int binop at a TARGET-LESS
            # position (`_gen_binop`'s pure-literal arm): a BigInt context
            # renders `::tpy::BigInt(static_cast<int64_t>(...LL))` /
            # `from_str`, a default-int one the plain decimal (`print(1 +
            # (2 + 3))` -> `6`) -- both via the shared
            # render_int_literal_value. Mirror it in flagged positions; a
            # slot retarget downstream overwrites int_cpp wholesale, so the
            # pre-render cannot double-wrap. Slot-THREADED fixed-int sinks
            # never fold (the retype rebuilds `::tpy::add_check<intN>(...)`
            # instead), so the operator route below stays their path.
            lit_val = getattr(analyzer.get_expr_type(e), "value", None)
            # The fold is SYNTACTIC (_gen_binop's involves_variables walk):
            # sema computes a constant value for a subscript over a
            # literal-seeded container too (`xs[0] + xs[1]`), but the AST
            # renders those as runtime reads -- only variable-free literal
            # trees fold.
            if lit_val is not None and TypeResolver.involves_variables(e):
                lit_val = None
            fits_i64 = lit_val is not None and -(2**63) <= lit_val <= 2**63 - 1
            resolved = resolve_int_literals(
                rtype, analyzer.ctx.default_int_for_literal)
            # The fold covers every magnitude in a flagged position:
            # render_int_literal_value spells BigInt fits-i64 values
            # `BigInt(static_cast<int64_t>(..LL))` and beyond-int64 values
            # `BigInt::from_str("...")` -- both the AST's fold renders.
            if fold_ok and lit_val is not None:
                _witness("binop.literal_fold")
                return THIRLiteral(
                    result_type=resolved, value=lit_val,
                    int_cpp=render_int_literal_value(
                        lit_val, resolved,
                        default_int_type=analyzer.ctx.default_int_type,
                        type_to_cpp=lambda t: t.to_cpp()),
                    loc=loc)
            # Un-flagged positions keep rejecting the fits-i64 slice: the
            # AST may fold there too (an unwitnessed target-less sink), so
            # routing the full operator render would silently diverge. The
            # beyond-int64 slice keeps its pre-existing operator route
            # (correct at slot-threaded sinks; a target-less unflagged sink
            # would diverge the same way -- flag it before routing). The
            # FIXED-resolvable slice routes on: slot-threaded sinks are its
            # witnessed positions (the retype rebuild), and its target-less
            # flagged sinks folded above.
            # `_gen_binop` folds ONLY when `target_type is None`, so a
            # SLOT-THREADED position renders the full operator on both paths
            # (`b: int = 1 << 40` -> `((BigInt(1)) << (BigInt(40)))`) and the
            # fits-i64 slice routes there like the beyond-int64 slice already
            # does. Target-LESS positions keep rejecting: the AST folds there,
            # and only `fold_ok` marks the ones whose fold THIR mirrors.
            if (is_big_int_type(resolved) and (lit_val is None or fits_i64)
                    and not slot_threaded):
                reject()
        if e.op in _BITWISE_OPS:
            _witness("binop.bitwise")
    elif e.op in _COMPARE_OPS:
        if rtype is None or not is_bool_type(rtype):
            reject()
        if (rb is not None and not getattr(rb.method, "cpp_template", None)
                and not (rb.method.native_function
                         and rb.method.native_name)):
            reject()
        lt = _narrowed_opt_operand(
            e.left, _operand_type(e.left, declared, analyzer), lc, analyzer)
        rt = _narrowed_opt_operand(
            e.right, _operand_type(e.right, declared, analyzer), lc, analyzer)
        opt_eq_targets = _opt_scalar_eq_pair(e, lt, rt, analyzer)
        if (opt_eq_targets is None
                and _bare_field_eq_pair(e, lt, rt, analyzer)):
            # Both members read bare into the compare parens -- the same
            # target-less render as the optional_safe_eq pair.
            opt_eq_targets = (None, None)
        if e.op not in ("==", "!="):
            # An UNPROVEN value-opt scalar ORDERING operand unwraps through
            # `deref_optional_check(x)` (its inner scalar); `==`/`!=` stays on
            # the bare optional (`optional_safe_eq`), so it is left untouched.
            lt = _unproven_opt_scalar_inner(e.left, lc, analyzer) or lt
            rt = _unproven_opt_scalar_inner(e.right, lc, analyzer) or rt
        if not ((_resolved_scalar(lt, analyzer)
                 and _resolved_scalar(rt, analyzer))
                or (_str_compare_operand(e.left, lt, analyzer)
                    and _str_compare_operand(e.right, rt, analyzer))
                or (_bytes_compare_operand(e.left, lt, analyzer)
                    and _bytes_compare_operand(e.right, rt, analyzer))
                or (_char_compare_operand(e.left, lt, analyzer)
                    and _char_compare_operand(e.right, rt, analyzer))
                or (_tparam_value(lt) and _tparam_value(rt))
                or _union_compare_pair(lt, rt)
                or (e.op in ("==", "!=") and _any_compare_pair(lt, rt))
                or _record_compare_pair(lt, rt)
                or _enum_compare_pair(e, lt, rt, analyzer)
                or _tuple_compare_pair(lt, rt, analyzer)
                or opt_eq_targets is not None):
            reject()
        if _mixed_sign_compare(lt, rt):
            reject()
    elif e.op in _LOGICAL_OPS:
        if rtype is None or not is_bool_type(rtype):
            # Value-position and/or: Python operand semantics via the
            # once-evaluated-LHS ternary (_gen_logical_value's slice) --
            # the AST splits on the RESULT type alone.
            return _lower_value_select(e, rtype, lc, declared, loc)
        lt = _operand_type(e.left, declared, analyzer)
        rt = _operand_type(e.right, declared, analyzer)
        if (lt is None or not is_bool_type(lt)
                or rt is None or not is_bool_type(rt)):
            # A bool RESULT over non-bool operands takes the AST's direct
            # C++ `&&`/`||` with truthiness reasoning not carried here.
            reject()
    elif e.op in _IS_OPS:
        if (_is_none_compare_operand(e, declared, analyzer) is None
                and _any_none_subject(e, declared, analyzer) is None
                and not _opt_record_none_subject(e, lc)):
            reject()
    elif e.op in _MEMBERSHIP_OPS and isinstance(e.right, TpyTupleLiteral):
        # `x in (a, b, ...)` / `not in`: a tuple-literal membership expands to an
        # OR-chain of `==` compares (no `__contains__`). Value-comparable
        # (scalar / str) needle + elements only, so each `==` renders plainly.
        lt = _operand_type(e.left, declared, analyzer)
        left_scalar = lt is not None and _resolved_scalar(lt, analyzer)
        left_str = (lt is not None
                    and _resolved_str_value(lt, analyzer) is not None)
        if not (left_scalar or left_str) or not e.right.elements:
            reject()
        for el in e.right.elements:
            et = analyzer.get_expr_type(el)
            if et is None or (
                    left_scalar and not _resolved_scalar(et, analyzer)) or (
                    left_str and _resolved_str_value(et, analyzer) is None):
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
        lt = _operand_type(e.left, declared, analyzer)
        if fi is None:
            # No resolved `__contains__` member (`readonly[set]` strips it;
            # list/array/span have none): the AST's `is_native_in` fallback
            # renders `std::ranges::contains(recv, x)`. Any native NativeIterable
            # receiver (set/list/array/span) with a scalar / owned-str needle;
            # the universal iterator-loop form is a later rung.
            rt = _operand_type(e.right, declared, analyzer)
            rt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
                       if rt is not None else None)
            rec = (analyzer.registry.get_record_for_type(rt_bare)
                   if rt_bare is not None else None)
            # str/bytes iterate as char/byte sequences so they qualify as
            # native-iterable, but membership on them is SUBSTRING (`.find() !=
            # npos`), not element-containment (`std::ranges::contains`) -- that
            # is the str/bytes-membership arms' job, so exclude them here.
            if not (rec is not None and rec.is_native
                    and is_native_iterable(rt_bare, analyzer.registry)
                    and _resolved_str_value(rt, analyzer) is None
                    and _resolved_bytes_value(rt, analyzer) is None
                    and (_resolved_scalar(lt, analyzer)
                         or _resolved_str_value(lt, analyzer) is not None)):
                reject()
            ranges_contains = True
        elif (e.typed_dict_in_field is not None
                or fi.cpp_template or fi.native_function):
            reject()
        elif not fi.native_name:
            # A plain USER `__contains__` (record / generic record): the AST's
            # gen_call_from_fi member tail, `(recv.__contains__(needle))`. A
            # declared record name or an admitted field-read receiver; a
            # scalar / str-value needle renders bare (a user record is never
            # view-keyed, so the AST threads no literal target).
            recv_ok = ((isinstance(e.right, TpyName)
                        and e.right.name in declared)
                       or (isinstance(e.right, TpyFieldAccess)
                           and _field_receiver_ok(e.right, declared,
                                                  analyzer)))
            if not (recv_ok
                    and (_resolved_scalar(lt, analyzer)
                         or _resolved_str_value(lt, analyzer) is not None)):
                reject()
            user_contains = True
        elif isinstance(e.right, (TpyName, TpySetLiteral)):
            if isinstance(e.right, TpyName):
                if e.right.name not in declared:
                    reject()
                ct = unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(declared[e.right.name])))
            else:
                # A SET-LITERAL rvalue receiver (`d in {date(..), date(..)}`)
                # renders its spelled ctor and takes the same `.contains`
                # member; the literal's own lowering gates the elements.
                ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(e.right))))
                if ct is not None:
                    ct = resolve_pending_container(ct, analyzer) or ct
            # A str needle renders bare into `contains(...)` on both paths
            # (literal / view name / owned local -- the container's transparent
            # lookup absorbs the form), exactly like a scalar needle. An
            # F1-RECORD needle name reads bare the same way (the record-keyed
            # set/dict slice). A VIEW-keyed container (`set[StrView]`) threads
            # view_key_target into the needle's literal render (the
            # static-storage pin) -- not mirrored, reject.
            lt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(lt)))
                       if lt is not None else None)
            if not ((is_dict(ct) or is_set(ct))
                    and (_resolved_scalar(lt, analyzer)
                         or (isinstance(e.left, TpyName)
                             and _f1_record(lt_bare, analyzer))
                         or (_resolved_str_value(lt, analyzer) is not None
                             and view_key_target(ct) is None))):
                reject()
        elif (isinstance(e.right, TpyMethodCall)
              and _dict_view_iterable_ok(
                  e.right, declared, analyzer, methods=("values", "keys"))):
            # `x in d.values()` / `x in d.keys()`: the view rvalue renders
            # `::tpy::dict_values(d).contains(x)`; the needle is the dict's
            # value/key (scalar or bare-str). `.items()` needs a tuple needle,
            # a later cell.
            if not (_resolved_scalar(lt, analyzer)
                    or _resolved_str_value(lt, analyzer) is not None):
                reject()
        else:
            reject()
    else:
        reject()

    if e.op in _MEMBERSHIP_OPS and isinstance(e.right, TpyTupleLiteral):
        # LATENT GAP: this emits the `==` OR-chain unconditionally; the AST's
        # `_try_fold_literal_in` folds `x in (a,b,c)` to a compile-time
        # `true`/`false` when `x` carries literal-overload narrowing facts.
        # Unreachable today (the only source, match-over-`Literal[...]`
        # subjects, is excluded from THIR routing in `match.py`), so no
        # divergence -- but a future generics/overload-literal migration that
        # routes such a body must gate those facts here (reject or fold).
        _witness("binop.tuple_membership")
        elems = e.right.elements
        # A non-trivial needle binds to a `__in_lhs` temp in a statement
        # expression (mirrors _gen_binop's need_temp).
        need_temp = (len(elems) > 1
                     and not ExpressionGenerator._is_trivial_needle(e.left))
        return THIRTupleMembership(
            result_type=rtype,
            left=_lower_expr(e.left, lc, declared),
            elements=tuple(_lower_expr(el, lc, declared) for el in elems),
            negate=e.op == "not in",
            need_temp=need_temp,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and _bytes_membership_ok(e, declared, analyzer):
        # Bytes/BytesView membership -> the native free function
        # `::tpy::bytes_contains[_sub](recv, needle)`. The receiver reads bare;
        # a bytes-literal needle renders OWNED (the AST's target-less default),
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
        _witness("binop.str_membership")
        return THIRStrMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared),
            needle=_lower_expr(
                e.left, lc, declared,
                field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
            negate=e.op == "not in",
            wrap_receiver_sv=isinstance(e.right, TpyStrLiteral),
            loc=loc)
    if e.op in _MEMBERSHIP_OPS and ranges_contains:
        _witness("binop.set_ranges_membership")
        # A container FIELD haystack (`item in self.xs`) reads bare into
        # `std::ranges::contains(this->xs, item)` -- prechecked here rather
        # than admitted at RECEIVER in the ladder, which would also re-route
        # method receivers this arm says nothing about.
        recv_prechecked = (
            isinstance(e.right, TpyFieldAccess)
            and _plain_container_read(
                _field_decl_type(e.right, declared, analyzer))
            and _field_receiver_ok(e.right, declared, analyzer)
            and _witness("binop.membership_container_field"))
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared,
                                 field_prechecked=recv_prechecked),
            needle=_lower_expr(e.left, lc, declared),
            method_cpp="",
            negate=e.op == "not in",
            ranges_contains=True,
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
        # A str-family FIELD needle renders the bare member read into the
        # contains(...) template on both paths (same owned-str-field-ok
        # position as the compare operands above).
        needle = _lower_expr(
            e.left, lc, declared,
            field_owned_str_ok=isinstance(e.left, TpyFieldAccess))
        if user_contains and e.resolved_contains.params:
            # Mirror _convert_to_fixed_int_arg: a runtime-BigInt needle
            # against the user __contains__'s declared fixed-int param takes
            # the checked call-arg narrow (an int literal stays bare, like
            # the AST's literal exemption).
            pt = unwrap_readonly(e.resolved_contains.params[0].type)
            if (is_fixed_int_type(pt)
                    and not isinstance(e.left, TpyIntLiteral)
                    and _runtime_bigint(analyzer.get_expr_type(e.left),
                                        analyzer)):
                needle = THIRCoerce(
                    result_type=pt, expr=needle,
                    coercion_name=_BIGINT_NARROW,
                    wrap=f"({{0}}).to_fixed_check<{pt.to_cpp()}>()", loc=loc)
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared, use=recv_use),
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
            # AST arm is order-blind, so `None is v` lands here too). A
            # NARROWED subject's read is its extraction alias -- the AST
            # still tests the variant binding, so reject rather than
            # route-and-diverge.
            assert isinstance(operand, TpyName)
            if (operand.name in lc.narrow.narrowed
                    or operand.name in lc.inline_narrowed
                    or operand.name in lc.narrow.spelled):
                raise ThirUnsupported("binop.union_none_narrowed_subject")
            # A protocol-member union is no variant: the AST's protocol
            # ptr-compare arm precedes the monostate arm (both polarities,
            # every position), so route-and-diverge is the alternative.
            if any(is_protocol_type(m) for m in ut_none.members):
                raise ThirUnsupported("binop.union_none_protocol_subject")
            _witness("isnone.union_monostate")
            return THIRIsNone(
                result_type=rtype,
                operand=_lower_expr(operand, lc, declared,
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
        is_field = isinstance(operand, TpyFieldAccess)
        # A raw `Ptr[T]` field (storage `T*`) None-tests via `== nullptr`, not
        # the Optional field's `.has_value()`.
        ptr_field = is_field and _ptr_value_none_field(
            operand, declared, lc.analyzer)
        if is_field:
            _witness("narrow.ptr_field_test" if ptr_field
                     else "narrow.opt_field_test")
        value_repr = (is_field and not ptr_field) or (
            isinstance(operand, TpyName)
            and (_value_opt_scalar_binding(operand.name, lc)
                 or _value_opt_view_binding(operand.name, lc)
                 or operand.name in lc.value_opt_record_locals
                 or (operand.name in declared
                     and _value_opt_scalar(declared[operand.name],
                                           lc.analyzer) is not None))) or (
            isinstance(operand, TpyNamedExpr)
            and _value_opt_scalar(analyzer.get_expr_type(operand),
                                  lc.analyzer) is not None) or (
            not isinstance(operand, (TpyName, TpyFieldAccess))
            and _value_opt_rvalue(operand, lc.analyzer) is not None)
        lowered_operand = _lower_expr(
            operand, lc, declared, allow_whole_optional=True,
            field_prechecked=is_field)
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
    if e.op in _COMPARE_OPS and opt_eq_targets is not None:
        # The optional_safe_eq render: each optional side reads bare (a
        # narrowed side derefs at the name arm), the plain side opposite an
        # UN-narrowed optional renders against that optional's inner (the
        # AST's _comparison_targets target threading -- char/numeric literal
        # renders). std::optional's mixed operator handles the compare. The
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
        # compare/concat/needle templates on both paths, so binop operands
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
            if isinstance(side, TpyFieldAccess):
                st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(side))))
                if _f1_record(st, analyzer):
                    return _ExprUse(result=_ExprResultUse.BORROW_BIND)
            # Compare operands are target-less on the AST path, so a
            # both-literal sub-binop folds there.
            return _ExprUse(literal_fold_ok=True, allow_temps=temps_ok)

        left = (_lower_unproven_opt_scalar(e.left, lc, declared)
                if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.left, rt_a, lc, declared, use=_cmp_operand_use(e.left),
            field_owned_str_ok=isinstance(e.left, TpyFieldAccess))
        right = (_lower_unproven_opt_scalar(e.right, lc, declared)
                 if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.right, lt_a, lc, declared, use=_cmp_operand_use(e.right),
            field_owned_str_ok=isinstance(e.right, TpyFieldAccess))
        # A bare `self` record operand reads by value here (`(*this) < other`,
        # the synthesized @total_ordering / dataclass compares) -- gen_expr_deref
        # derefs the pointer receiver in value position, unlike a field/method
        # receiver where THIRSelf stays bare.
        if isinstance(left, THIRSelf):
            left = replace(left, deref=lc.self_is_pointer)
        if isinstance(right, THIRSelf):
            right = replace(right, deref=lc.self_is_pointer)
    else:
        lslot, rslot = _rb_operand_slots(e.resolved_binop)

        def _arith_operand_use(side: TpyExpr) -> _ExprUse:
            # A record-rvalue operand (`timedelta(...) + timedelta(...)`)
            # rides BORROW_BIND so the ctor/call's record result is
            # admitted -- the compare arm's `_cmp_operand_use` twin.
            if _record_call_rvalue_operand(side, analyzer):
                return _ExprUse(result=_ExprResultUse.BORROW_BIND,
                                allow_temps=temps_ok)
            return _ExprUse(allow_temps=temps_ok)

        left = _lower_unproven_opt_scalar(e.left, lc, declared)
        if left is None:
            left = _slot_literal_retype(
                _lower_expr(e.left, lc, declared,
                            use=_arith_operand_use(e.left),
                            field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
                lslot, lc)
        right = _lower_unproven_opt_scalar(e.right, lc, declared)
        if right is None:
            right = _slot_literal_retype(
                _lower_expr(e.right, lc, declared,
                            use=_arith_operand_use(e.right),
                            field_owned_str_ok=isinstance(e.right,
                                                          TpyFieldAccess)),
                rslot, lc)
    bt = _resolved_bytes_value(rtype, analyzer)
    lcast, rcast = _binop_operand_casts(e, analyzer)
    # gen_binop's dedicated fixed-int literal arm fires only where a
    # fixed-int TARGET is threaded (decl inits, returns, call/ctor args,
    # resolved-binop operand slots -- exactly `_slot_literal_retype`'s
    # positions), so the verdict lives THERE: lowering stamps the
    # position-independent operand fact (both operands IntLiteral-typed
    # non-names) and the retype rebuilds the node paren-free with the
    # target-resolved `gen_call_from_fi` template. A target-less position
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
            and operand.name in lc.value_opt_record_locals)


def _value_opt_scalar_binding(name: str, lc: '_LowerCtx') -> bool:
    """A value-repr `Optional[cheap scalar]` BINDING (`std::optional<T>`):
    a param of the function being lowered, or a registered local -- a
    for-each loop var over `list[T | None]`, or a chain-optional match
    capture binding the full subject (the AST registers the latter in
    `ctx.var_types` exactly like a param). Reads render bare (un-narrowed) /
    `(*p)` (narrowed); the None-test and truthiness carry the value-repr
    renders -- identical for every binding kind. The movable-seeded
    last-use moves stay param-only through `_is_move_source`'s movable
    guard (codegen never seeds a loop var or capture movable)."""
    if name in lc.value_opt_locals:
        return True
    for n, t in lc.params:
        if n == name:
            return _value_opt_scalar(t, lc.analyzer) is not None
    return False

def _value_opt_view_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is a value-repr `Optional[view]` param -- str OR bytes
    (`std::optional<std::string_view>` / `std::optional<std::span<const
    uint8_t>>`) -- of the function being lowered, the view twin of
    `_value_opt_scalar_binding`. A narrowed read unwraps `(*x)` (a borrow view),
    the None-test/truthiness carry the same value-repr renders, and a pass into
    another same-family `Optional[view]` slot takes the arg-split shim."""
    return _value_opt_view(_param_declared_type(name, lc), lc.analyzer) is not None

def _value_opt_view_binding(name: str, lc: '_LowerCtx') -> bool:
    """A value-repr `Optional[view]` BINDING -- a param OR a registered LOCAL
    (`lc.value_opt_view_locals`), the view twin of `_value_opt_scalar_binding`.
    The two differ only in the narrowed-deref form: a param's `(*s)` is a BORROW
    view (an owned sink adds the family copy), a local's `(*s)` is already OWNED
    (STORAGE, no copy) -- the read arm branches on `param_names` for that."""
    return name in lc.value_opt_view_locals or _value_opt_view_param(name, lc)

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
    deep-const verdict (`deep_const_borrow_params` -- the inferred
    discriminant-only-use fact that also deep-consts the signature's variant
    spelling) applies to. Shared by the isinstance condition and every
    narrowed-member `std::get` template arg, so the renders cannot drift."""
    if var in lc.const_locals:
        return True
    return _param_is_deep_const(var, lc.func, lc.analyzer, lc.record_name)


def _poly_cast_context(var: str, lc: '_LowerCtx',
                       declared: dict[str, TpyType]
                       ) -> 'tuple[bool, str, TpyType | None]':
    """The (const, cast_arg, source_inner) triple shared by every
    polymorphic dynamic_cast composition (the if-init form, the no-alias
    OR-chain, the value-position chain), so the spellings cannot drift.
    Const pointees come from the param verdicts OR a readonly-qualified
    subject; the cast arg spells bare for pointer-shaped subjects, `&name`
    for `T&` ones."""
    var_raw = declared.get(var)
    var_decl = _poly_subject_decl(var_raw)
    const = (_const_borrow_name(var, lc)
             or _poly_subject_readonly(var_raw))
    if var == lc.self_receiver:
        # `self` is already pointer-shaped in a plain method (`this`) and in
        # a simple generator (`(*this)`, whose address folds back to `this`);
        # a resumable coro's `__self` is a `Record&` field, so it takes the
        # address. Mirrors polymorphic_cast_arg's self arms. A readonly
        # method's `this` is const, so the cast targets `const Sub*`.
        cast_arg = ("this" if lc.self_cpp in ("this", "(*this)")
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


def _narrow_variant_cpp(var: str, u: UnionType, lc: '_LowerCtx') -> str:
    """The C++ expression yielding the narrowing subject's `std::variant`: a
    recursive-alias wrapper union (F6) reaches it via `.value` (the
    VariantAccess.variant_expr indirection); every other routed union is the
    variant itself. A POINTER-bound subject (a resumable pointer-form loop var
    over `list[A | B]`) derefs first, like any other read of it. Shared by the
    isinstance condition and the extraction alias so the two spellings cannot
    drift."""
    base = f"(*{var})" if _ptr_read_derefs(var, lc) else var
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


def _param_declared_type(name: str, lc: '_LowerCtx') -> 'TpyType | None':
    """The declared type of param `name` on the function being lowered, or None
    when `name` is not a param -- the source-type lookup the arg-split shim keys
    its family match on."""
    for n, t in lc.params:
        if n == name:
            return t if isinstance(t, TpyType) else None
    return None

def _lower_expr(e: TpyExpr, lc: '_LowerCtx',
                declared: dict[str, TpyType], *,
                use: _ExprUse = _ExprUse(),
                allow_whole_optional: bool = False,
                allow_unrouted_name: bool = False,
                allow_union_divergent: bool = False,
                field_prechecked: bool = False,
                field_owned_str_ok: bool = False,
                subscript_prechecked: bool = False,
                container_threaded: bool = True,
                array_retype: bool = True,
                error_return_raw: bool = False,
                target_type: TpyType | None = None) -> THIRExpr:
    # `allow_temps` admits the arg-temp rows for THIS expression's args only
    # when it is a free call: set by the five flushable statement positions
    # over their direct value, never propagated into subexpressions (each of
    # those positions is where the AST's single pre-statement flush point
    # places the `__tmp_N` decls).
    # The name flags likewise apply only to THIS expression: specialized
    # consumers opt into a whole binding or a constructor-only move source.
    analyzer = lc.analyzer
    temp_args = use.allow_temps
    # A container-literal local's use sites keep the pre-resolution pending type
    # on the expr (the AST path unwraps it in TypeResolver.get_resolved_type);
    # THIR nodes must carry fully-resolved types. Same for a str local's
    # PendingStrType (sema's view/owned usage resolution is final pre-lowering).
    rtype = analyzer.get_expr_type(e)
    rtype = resolve_pending_container(rtype, analyzer) or rtype
    rtype = _resolve_pending_view(rtype, analyzer) or rtype
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        if (e.is_function_ref and e.name not in lc.nested_def_locals
                and _func_ref_routable(e, analyzer)):
            # A function used as a value -> `_function_ref_name`'s plain render,
            # carried on the pre-spelled `cpp` slot: the qualified name for a
            # cross-module import, else the bare escaped name.
            qual = lookup_imported(analyzer.ctx.module_attributes, e.name,
                                   SymbolKind.FUNCTION)
            cpp = (qualified_cpp_name(*qual) if qual is not None
                   else escape_cpp_name(e.name))
            _witness("name.func_ref")
            return THIRName(result_type=rtype, name=e.name, cpp=cpp,
                            form=Form.VALUE, loc=loc)
        if e.is_function_ref and e.name in lc.nested_def_locals:
            # A closure local read as a value (`callbacks.append(add_offset)`):
            # the nested def bound a lambda under its own name, so the read is
            # the bare local -- never `_function_ref_name`'s module spelling.
            # The return arm's `ret.closure_name` shape at expression level.
            _witness("name.closure_local")
            return THIRName(result_type=rtype, name=e.name, form=Form.VALUE,
                            loc=loc)
        if e.name in lc.forbidden_reads:
            raise ThirUnsupported("stmt.match")
        if e.name not in declared:
            raise ThirUnsupported("name.global_read", detail=True)
        binding_type = _param_declared_type(e.name, lc)
        if binding_type is None:
            binding_type = declared.get(e.name)
        unrouted = _unrouted_binding_read(binding_type, analyzer)
        if (unrouted is not None and not allow_unrouted_name
                # A REGISTERED owned-optional record local has a routed read
                # arm (the value_opt_record_locals branch below); the
                # unrouted-binding verdict covers params and unregistered
                # bindings only.
                and e.name not in lc.value_opt_record_locals):
            raise ThirUnsupported(unrouted, detail=True)
        # A view-INNER value-opt LOCAL (`StrView`/`BytesView | None` ->
        # `optional<string_view>`) is not routed: unlike its owned-inner twin
        # (`str`/`bytes | None`, routed via `value_opt_view_locals`), the AST's
        # narrowed read is a whole-optional-wrap quirk we don't mirror and its
        # None-test would key value-repr off the param-only predicate, so defer
        # the whole body. A value-opt-view PARAM keeps its existing routing.
        if (e.name not in lc.prescan.param_names and e.name in declared
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
            # resumable method coro's `Record&` frame field). Reached as a
            # field-access / method-call receiver and as a record call-arg
            # (whose tail retags deref for the `(*this)` render), so its
            # form tag is informational.
            _witness("self.this")
            return THIRSelf(result_type=rtype, form=Form.BORROW,
                            cpp=lc.self_cpp, loc=loc)
        gcpp = lc.prescan.global_cpp.get(e.name)
        if gcpp is None and lc.top_level_scope:
            # Module init reads an imported name that this module REDEFINES
            # later: statements before the redefinition still see the import
            # (the AST's `current_stmt_line >= decl_line` test -- the one
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
            member_cpp, is_ptr = inr
            return THIRNarrowedRead(
                result_type=rtype, variant_cpp=e.name, member_cpp=member_cpp,
                is_ptr_variant=is_ptr,
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
        if e.name in lc.value_opt_record_locals:
            # An owned-optional RECORD local (`std::optional<Rc<T>>`,
            # registered at its call-init decl). A NARROWED read (sema
            # retyped it to the record) unwraps `(*upgraded)` -- a record
            # lvalue consumed by a receiver/member position; a whole-optional
            # read (None-test) stays bare.
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
            # `rtype` no longer Optional) unwraps `(*p)` -- gen_expr_deref's
            # value-optional deref. The bare-target positions where the AST does
            # NOT deref a narrowed read (a value-optional call slot) strip this
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
            # or bytes (`<std::span<const uint8_t>>` param / `<vector>` local).
            # A NARROWED read (sema retyped it to the inner view, `rtype` no
            # longer Optional) unwraps `(*s)`. For a PARAM that deref is a
            # BORROW view, so an owned sink still gets the family copy
            # (`std::string`/`bytes_copy`); for a LOCAL it is already OWNED
            # (STORAGE), so a sink takes it bare. An UN-narrowed read stays the
            # bare whole optional (VALUE), reached only inside the None-test /
            # truthiness / arg-shim wrappers (the bare value position is
            # unsupported). This must precede the str-name arm below, which keys
            # on the narrowed view rtype and would drop the deref.
            narrowed = not isinstance(unwrap_readonly(rtype), OptionalType)
            if not narrowed:
                form = Form.VALUE
            elif e.name in lc.prescan.param_names:
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
                                                lc.prescan.param_names),
                            loc=loc)
        # A bytes-slice name carries the same load-bearing view/owned form tag
        # as str: BORROW (span param / view local) drives the owned-sink
        # `::tpy::bytes_copy(x)`, STORAGE (owned vector local) suppresses it.
        bytes_t = _resolved_bytes_value(rtype, analyzer)
        if bytes_t is not None:
            return THIRName(result_type=bytes_t, name=e.name, cpp=gcpp,
                            form=_bytes_name_form(e.name, bytes_t,
                                                  lc.prescan.param_names),
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
        # A resumable frame_slot local (R1c): the read is `(*name)` (the
        # slot's operator*). Member access off it is `.` (the slot is not a
        # pointer, so `_field_is_arrow` / the method-call arrow stay False),
        # giving `(*b).method()` -- the AST's frame_slot deref render.
        # A rebound CONTAINER local (F2d) is a bare `T*` whose VALUE reads
        # deref `(*name)` -- the AST's pointer_value_expr render at subscript
        # receivers, len args, and arg slots. Method receivers stay bare (the
        # method arm renders `items->m(...)`, matching the AST's arrow on the
        # pointer), and record / Optional-ptr pointer-locals stay bare
        # everywhere (their `->` access rides the field/method arrow arms).
        container_ptr = False
        if (e.name in lc.pointers and binding_type is not None
                and use.result not in (_ExprResultUse.RECEIVER,
                                       _ExprResultUse.BORROW_BIND)):
            bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                binding_type)))
            # A pointer-slot GLOBAL derefs at every value position whatever
            # its family (the AST's gen_expr_deref indirect render); locals
            # keep the container-only deref (record locals stay bare, their
            # `->` access rides the arrow arms).
            container_ptr = (is_list(bt) or is_dict(bt) or is_set(bt)
                             or is_array(bt)
                             or e.name in lc.prescan.global_slots)
        return THIRName(result_type=rtype, name=e.name, cpp=gcpp, form=form,
                        deref=(e.name in lc.frame_slots
                               or e.name in lc.walrus_slot_locals
                               or container_ptr),
                        loc=loc)
    if isinstance(e, TpyFieldAccess):
        if e.property_getter_call is not None:
            # A `@property` read is a getter method call in disguise
            # (`c.radius` -> `c.radius()`): _gen_field_access delegates to
            # _gen_method_call, so lower the synthesized call through the
            # method-call arm, preserving this read's use position. sema types
            # the field access, not the synthesized call, so seed the call's
            # result type from the field's (the getter's return type) before
            # the method-call arm reads it.
            pg = e.property_getter_call
            if rtype is not None and analyzer.get_expr_type(pg) is None:
                analyzer.ctx.set_expr_type(pg, rtype)
            return _lower_expr(pg, lc, declared, use=use)
        if e.dyn_getattr_call is not None:
            return _lower_dyn_getattr_call(e, rtype, lc, declared)
        if e.module_var_access is not None:
            return _lower_module_var(e, rtype, lc, *e.module_var_access,
                                     loc=loc)
        if e.class_constant_owner is not None:
            return _lower_class_constant(e, rtype, lc, declared, loc,
                                         tuple_ok=use.tuple_source)
        bare_mod = _bare_module_recv(e.obj, declared, analyzer)
        if (bare_mod is not None
                and _module_var_read_cpp(bare_mod, e.field, analyzer)
                is not None):
            # `mod.X` off a MODULE binding -- the same render as the dotted
            # form; a module receiver whose field is NOT a registered
            # variable falls through to the normal arms like the AST does.
            return _lower_module_var(e, rtype, lc, bare_mod, e.field, loc=loc)
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
                    # A CONTAINER field in a for-head that reaches the generic
                    # iterable position -- the RESUMABLE frame's
                    # (`(__self.nodes).begin()`). The sync for-head and the
                    # simple-generator peephole have their own arms and never
                    # land here. The bare member read is what begin()/end()
                    # are taken off, so the read itself is the whole render.
                    # ITERABLE only -- at the generic VALUE position a
                    # container field read is a copy-vs-alias decision its own
                    # consumers make.
                    # DECLARED-type keyed: a NARROWED `Optional[list]` field
                    # types as a plain container on the expr, but the AST
                    # unwraps that read -- keep it out (same rule as the
                    # str-family field rows).
                    or (use.result is _ExprResultUse.ITERABLE
                        and _plain_container_read(
                            _field_decl_type(e, declared, analyzer))
                        and _witness("field.container_iterable"))
                    # An F3 tuple FIELD read consumed by a borrow lift
                    # (`t = h.pair` -> `tuple_to_pointer<..>(h.pair)`): the
                    # bare member read feeds the wrap.
                    or (use.result is _ExprResultUse.BORROW_BIND
                        and _f1_tuple(rtype, analyzer) is not None)
                    or (use.result is _ExprResultUse.TRUTHY
                        and _truthiness_mode(rtype, analyzer) is not None)
                    or (field_owned_str_ok
                        and (_str_field_value_read(e, declared, analyzer)
                             # The same str-family member read off a CALL
                             # receiver (`Box("hi").msg`) -- the receiver
                             # admission is _field_over_call_ok's.
                             or (_resolved_str_value(rtype, analyzer)
                                 is not None
                                 and _field_over_call_ok(e, analyzer)))
                        and _witness("fstr.str_field"))
                    or (field_owned_str_ok
                        and _bytes_field_value_read(e, declared, analyzer)
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
                             or _value_elem_container(rtype, analyzer)
                             is not None)
                        and _witness("field.whole_optional")))
                if not result_ok:
                    raise ThirUnsupported("field.result_type", detail=True)
                if not (
                        _field_receiver_or_unbound_self_ok(
                            e, declared, analyzer)
                        or _ptr_value_field_recv_ok(e, declared, analyzer)
                        or _user_deref_field_recv_ok(
                            e, declared, lc.narrow.narrowed, analyzer,
                            lc.pointers)
                        or _optional_checked_field(e, declared, analyzer)
                        or _optional_checked_field_over_field_ok(
                            e, declared, analyzer)
                        or _field_over_subscript_ok(e, declared, analyzer)
                        or _optional_field_over_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_record_getitem_ok(
                            e, declared, analyzer, lc.pointers)
                        or _field_over_container_subscript_ok(
                            e, declared, analyzer)
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
                        is not None):
                    raise ThirUnsupported("field.receiver_shape", detail=True)
        if _unbound_self_field_ok(e):
            # `BaseN.field` -> `this->BaseN::field`: _gen_field_access's
            # early return, which never looks at the syntactic class-name
            # receiver (it has no value type). The explicit base qualifier
            # picks one ancestor subobject in non-virtual MI, so the parent
            # spelling IS the member render -- carried in `field_cpp`, the
            # node's "rendered C++ member name". Read and write target alike.
            if lc.self_cpp != "this":
                # A resumable method coro spells its receiver `__self` (a
                # `Record&`, read with `.`), but the AST hardcodes `this->`
                # here -- an unwitnessed render, so it keeps falling back.
                raise ThirUnsupported("field.unbound_self_receiver",
                                      detail=True)
            viewfam = _resolved_str_value(rtype, analyzer)
            if viewfam is None:
                viewfam = _resolved_bytes_value(rtype, analyzer)
            _witness("field.unbound_self")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=THIRSelf(result_type=e.unbound_self_parent_type,
                                  form=Form.BORROW, cpp=lc.self_cpp, loc=loc),
                field_cpp=(f"{e.unbound_self_parent_type.to_cpp()}::"
                           f"{_field_cpp(e)}"),
                is_arrow=True,
                form=_viewfam_result_form(viewfam),
                loc=loc)
        if e.enum_member_of is not None:
            # Type-level enum member access: `Color.RED` -> `Color::RED`
            # (gen_expr's BindingKind.ENUM arm, spelled at lowering).
            return THIREnumMember(result_type=rtype,
                                  cpp=_enum_member_cpp(e, analyzer), loc=loc)
        prop = _enum_prop_wrap(e, analyzer)
        if prop is not None:
            # `c.value`: a plain underlying-int value. `c.name`: a
            # static-storage string_view -- BORROW, so owned-str sinks
            # copy it (the S1 view->owned convert), mirroring the AST's
            # `_is_str_view_source` on the StrView-typed read.
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
        if e.needs_optional_runtime_check and isinstance(e.obj,
                                                         (TpySubscript, TpyName)):
            # Unproven `Optional[record]` member access -> `deref_check(<T*>).field`.
            # A NAME receiver (an Optional-ptr param / OPTIONAL_TO_PTR local) is
            # already a bare `T*` (pointer_value_expr is the identity for it). A
            # subscript is a `T*` off a borrow tuple, or a `std::optional<T>` off a
            # storage alias lifted to `T*` via optional_to_ptr (the STORAGE-form
            # convert). Mirrors _gen_field_access's runtime-check path.
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
                and isinstance(e.obj, TpyFieldAccess)
                and _optional_checked_field_over_field_ok(
                    e, declared, analyzer)):
            # Unproven access off a STORAGE `Optional[record]` member lvalue:
            # the AST wraps the whole optional -- `deref_optional_check(
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
        # narrowed the occurrence -- the AST's gen_expr-vs-gen_expr_deref
        # split, mirrored by allow_whole_optional like the name arm's.
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
                    is_ptr_variant=is_ptr, form=Form.BORROW, loc=loc),
                field_cpp=_field_cpp(e),
                is_arrow=False,
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
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                field_cpp=_field_cpp(e),
                is_arrow=e.ptr_non_null,
                deref_check=not e.ptr_non_null,
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
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                field_cpp=_field_cpp(e),
                is_arrow=(isinstance(e.obj, TpyName)
                          and _ptr_read_derefs(e.obj.name, lc)),
                deref_chain=e.deref_depth,
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            )
        return THIRFieldAccess(
            result_type=rtype,
            # A call-shaped receiver's own arg temps flush at the enclosing
            # statement (`Holder(__tmp_1).kind`), so allow_temps rides
            # through; inert for every non-call receiver shape.
            receiver=_lower_expr(
                e.obj, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER,
                             allow_temps=use.allow_temps),
                subscript_prechecked=isinstance(e.obj, TpySubscript)),
            field_cpp=_field_cpp(e),
            is_arrow=_field_is_arrow(e, lc),
            narrowed_deref=narrowed_opt,
            form=_viewfam_result_form(fa_str),
            loc=loc,
        )
    if isinstance(e, TpySubscript):
        if e.typed_dict_field is not None:
            # `d["key"]` on a TypedDict -> the field access `d.key`
            # (_gen_subscript's typed-dict arm); a total=False field wraps
            # the KeyError-panicking unwrap
            # `::tpy::typed_dict_field_check(d.key)`. Receivers: a bare
            # declared NAME, or a one-level FIELD off an admitted binding
            # (`p.addr["city"]` -> `p.addr.city`) -- indirect /
            # narrowed-optional receivers take AST-side unwraps this arm
            # does not mirror.
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
                    and isinstance(e.index, TpySlice)
                    and (is_list(rbare) or is_span(rbare)
                         or is_varargs(rbare))
                    and _container_slice_recv_ok(e.obj, lc, declared)
                    and _slice_bound_supported(e.index.lower, analyzer)
                    and _slice_bound_supported(e.index.upper, analyzer)
                    and (e.index.step is None
                         or _slice_bound_supported(e.index.step, analyzer)))
                if container_slice:
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
            # decl-init BORROW wrap -> `::tpy::bytes_copy(...)`); the stepped /
            # slice-var owned result (std::string / std::vector<uint8_t>) is
            # STORAGE, landing bare in every sink. Absent bounds emit
            # std::nullopt.
            rt_view = _resolved_viewfam_value(rtype, analyzer)
            form = (Form.BORROW if rt_view is not None
                    and (is_str_view_type(rt_view) or is_bytes_view_type(rt_view))
                    else Form.STORAGE)
            recv = _lower_expr(
                e.obj, lc, declared,
                field_prechecked=isinstance(e.obj, TpyFieldAccess))
            tpl = e.slice_function_info.cpp_template
            if not isinstance(e.index, TpySlice):
                return THIRStrSlice(
                    result_type=rtype, receiver=recv, cpp_template=tpl,
                    index=_lower_expr(e.index, lc, declared), form=form, loc=loc)
            sl = e.index

            def _bound(b: 'TpyExpr | None') -> 'THIRExpr | None':
                # `_gen_slice_bound`: a runtime-BigInt bound appends the
                # `.to_fixed_check<int32_t>()` narrow (non-literal only --
                # the gate rejects literal BigInt bounds, whose AST render
                # is ill-formed).
                if b is None:
                    return None
                lowered = _lower_expr(b, lc, declared)
                if not _runtime_bigint(analyzer.get_expr_type(b), analyzer):
                    return lowered
                _witness("narrow.slice_bound")
                return THIRCoerce(result_type=INT32, expr=lowered,
                                  coercion_name=_BIGINT_NARROW,
                                  wrap="{0}.to_fixed_check<int32_t>()", loc=loc)

            return THIRStrSlice(
                result_type=rtype,
                receiver=recv,
                cpp_template=tpl,
                lower=_bound(sl.lower),
                upper=_bound(sl.upper),
                step=_bound(sl.step),
                stepped=e.is_stepped_slice,
                form=form,
                loc=loc,
            )
        tup = _subscript_index_and_tuple(e, analyzer)
        if tup is not None:
            if (not subscript_prechecked
                    and _tuple_subscript_value_read(
                        e, declared, analyzer) is None):
                raise ThirUnsupported("subscript.tuple_shape", detail=True)
            # Tuple subscript -> `std::get<N>(t)`. Eligibility guaranteed a const index
            # and an eligible-tuple receiver; the shared helper re-derives the
            # normalized index (negatives folded), mirroring _gen_subscript. The
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
                         if isinstance(e.obj, TpySubscript) else _ExprUse()),
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                index=THIRLiteral(result_type=analyzer.get_expr_type(e.index),
                                  value=idx, loc=loc),
                form=form,
                loc=loc,
            )
        tuple_elem_recv = False
        rec_key = _record_getitem_key(
            analyzer.get_expr_type(e.obj), analyzer)
        if not subscript_prechecked:
            if rec_key is not None:
                # User-record `recv[index]` -> the record's bare operator[].
                # Value-scalar/char/enum/str/bytes/ptr results only (a
                # record-returning getitem is a borrow-form seam, deferred). The
                # index renders plainly against the key param (str-view/int,
                # or a BigInt index against a BigInt key param -- no narrow on
                # either path), mirroring _gen_subscript's fallback; a
                # runtime-BigInt index against a FIXED-int key param takes the
                # `.to_fixed_check` narrow -- excluded.
                ret_ok = (_resolved_scalar(rtype, analyzer)
                          or _eligible_char(rtype)
                          or _eligible_enum(rtype, analyzer) is not None
                          or _resolved_str_value(rtype, analyzer) is not None
                          or _resolved_bytes_value(rtype, analyzer) is not None
                          or _eligible_ptr_value(rtype, analyzer)
                          # A BY-VALUE F1-record result bound at a
                          # BORROW_BIND sink (the protocol arg-temp init
                          # `auto __tmp_N = container[1];`): the raw
                          # operator[] prvalue. Borrow-returning getitems
                          # keep the borrow-form-seam reject.
                          or (use.result is _ExprResultUse.BORROW_BIND
                              and _record_getitem_rvalue_arg(e, analyzer)))
                if not (ret_ok and _record_getitem_idx_recv_ok(
                        e, declared, analyzer, lc.pointers)):
                    note_detail("subscript.record_getitem")
                    raise ThirUnsupported("subscript.record_getitem", detail=True)
                _witness("subscript.record_getitem")
                return THIRSubscript(
                    result_type=rtype,
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                    index=_lower_expr(e.index, lc, declared),
                    record_getitem=True,
                    form=Form.VALUE,
                    loc=loc)
            recv_t = _subscript_container_recv_type(
                e.obj, declared, analyzer)
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
            # `T&` lvalue. The AST has ONE element emitter and it is
            # consumer-blind, so this read lands bare in every value position
            # -- there is no per-sink render to mirror, and admitting it here
            # replaces the per-sink `subscript_prechecked` bypasses (which
            # skipped the unproven-Optional receiver guard the gate applies).
            nested_container_elem = (
                _container_ref_alias_elem(recv_peeled, analyzer)
                and bool(_witness("subscript.container_elem")))
            index_ok = (
                _bigint_index_disposition(e.index, analyzer.get_expr_type(e.obj),
                                          analyzer) != "reject")
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
                # `list[Int32 | None]`) read into a WHOLE-optional consumer
                # (a value-opt decl slot): the bare `std::optional<T>` element.
                # Gated on allow_whole_optional so an arithmetic/None-test
                # consumer -- which needs the deref_optional_check unwrap --
                # never reaches this bare read.
                or (allow_whole_optional
                    and _value_opt_scalar(rtype, analyzer) is not None))
            container_ok = (
                recv_t is not None
                and (_container_value_leaf_read(recv_peeled, analyzer)
                     or nested_container_elem
                     or (allow_whole_optional
                         and _container_value_opt_scalar_elem(
                             recv_t, analyzer)))
                and ret_ok and index_ok)
            str_ok = (
                _str_slice_receiver_supported(e.obj, lc, declared)
                and _eligible_char(rtype) and index_ok)
            recv = e.obj
            bytes_recv_ok = False
            if isinstance(recv, TpyName):
                bytes_recv_ok = (
                    recv.name in declared
                    and _resolved_bytes_value(
                        declared[recv.name], analyzer) is not None)
            elif isinstance(recv, TpyFieldAccess):
                bytes_recv_ok = (
                    _field_receiver_ok(recv, declared, analyzer)
                    and _resolved_bytes_value(
                        analyzer.get_expr_type(recv), analyzer) is not None)
            bytes_ok = (
                bytes_recv_ok and _eligible_scalar(rtype) and index_ok)
            # `m[i][j]`: the receiver `m[i]` is a nested-container borrow lvalue
            # (a subscript the one-level recv-type resolver rejects), indexed
            # again -> nested `__getitem__`. Emit lowers it prechecked below.
            nested_ok = (
                _subscript_over_container_subscript_ok(e, declared, analyzer)
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
                _tr_et = get_iterable_element_type(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                        recv_t))), analyzer.registry)
                _tr_eb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    _tr_et))) if isinstance(_tr_et, TpyType) else None)
                tuple_elem_recv = (
                    isinstance(_tr_eb, TupleType)
                    and (_value_tuple(_tr_eb, analyzer) is not None
                         or _f1_tuple(_tr_eb, analyzer) is not None))
            # A bare protocol-typed NAME receiver (a Sequence-family
            # template param) subscripts through the SAME checked dunder
            # (`::tpy::__getitem__(s, i)` -- get_type_method_fi's protocol
            # fallback table); a protocol carrying its OWN template-bearing
            # __getitem__ keeps rejecting (a different render).
            proto_ok = (
                _protocol_subscript_recv(e.obj, declared, analyzer)
                and ret_ok and index_ok
                and bool(_witness("subscript.protocol_recv")))
            # A `*args` varargs view receiver with a range-PROVEN index:
            # the bounds-safe `args[static_cast<std::size_t>(i)]` render
            # the emit already carries; unproven varargs reads stay AST.
            varargs_ok = (
                recv_t is not None
                and is_varargs(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(recv_t))))
                and e.bounds_safe and ret_ok and index_ok
                and bool(_witness("subscript.varargs_recv")))
            # An F1-record ELEMENT read consumed as a borrow (the record
            # ref-slot arg row: `add_a(::tpy::__getitem__(a.bs, 0), ..)`):
            # the checked element lvalue renders bare -- BORROW_BIND-only,
            # so value-position record elements keep rejecting.
            record_elem_ok = (
                use.result is _ExprResultUse.BORROW_BIND
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled))
                and _f1_record(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(rtype))), analyzer)
                and index_ok
                and bool(_witness("subscript.record_elem_borrow")))
            # A VALUE-tuple element read consumed whole by the standalone
            # unpack capture (`a, b = addrs[0]` -> `auto __tup_N =
            # ::tpy::__getitem__(addrs, 0);`): the element is a
            # self-contained `std::tuple<...>`, so the checked read renders
            # bare. Scoped to the tuple-source position -- a value-position
            # tuple element keeps its own rows.
            tuple_elem_src_ok = (
                use.tuple_source
                and recv_t is not None
                and (is_list(recv_peeled) or is_array(recv_peeled)
                     or is_dict(recv_peeled))
                and _value_tuple(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(rtype))), analyzer) is not None
                and index_ok
                and bool(_witness("subscript.value_tuple_source")))
            if not (container_ok or str_ok or bytes_ok or nested_ok
                    or tuple_elem_src_ok
                    or record_recv_ok or tuple_elem_recv or proto_ok
                    or varargs_ok or record_elem_ok):
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
        # `form` is VALUE for a scalar / Char element; a str element/value read
        # (S5) carries its resolved shape -- BORROW when the read's view var
        # resolved `StrView` (the AST's `_is_str_view_source`, driving the
        # owned-sink `std::string(x)` copy), STORAGE when it resolved owned (the
        # `const std::string&` element lands in owned sinks via the implicit
        # copy ctor, bare on both paths).
        sub_str = _resolved_str_value(rtype, analyzer)
        if sub_str is None:
            # An owned-BYTES element read (list[bytes]) is an owned lvalue:
            # STORAGE via the shared form map, so owned decl/return sinks
            # land it bare (implicit copy), never the S6 bytes_copy wrap.
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
        # A U3-NARROWED receiver: gen_subscript keys the subscript on the
        # narrowed MEMBER (sema's analyzed type), so a dict/list/array/span
        # member routes the checked `::tpy::__getitem__` (a missing dict key
        # raises KeyError; a list index normalizes/bounds-checks) with the
        # index keyed on the member (BigInt narrow, view-key pin). Mirror that:
        # key on the member, never the declared union. Non-container narrows
        # (tuple std::get, record operator[], str/bytes) key differently --
        # fall back to AST for those. The element is a `T&` lvalue (BORROW).
        narrowed_recv_u = (lc.narrow.subject_union.get(e.obj.name)
                           if isinstance(e.obj, TpyName)
                           and e.obj.name in lc.narrow.narrowed else None)
        idx_obj_type = analyzer.get_expr_type(e.obj)
        if narrowed_recv_u is not None:
            if not (is_dict(idx_obj_type) or is_list(idx_obj_type)
                    or is_array(idx_obj_type) or is_span(idx_obj_type)):
                raise ThirUnsupported("subscript.ru_narrowed_nonmap")
            form = Form.BORROW
            _witness("subscript.ru_narrowed_recv")
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(
                e.obj, lc, declared,
                field_prechecked=isinstance(e.obj, TpyFieldAccess),
                subscript_prechecked=isinstance(e.obj, TpySubscript),
                allow_unrouted_name=own_recv),
            index=_narrow_bigint_index(_lower_expr(e.index, lc, declared), e.index,
                                       idx_obj_type,
                                       analyzer, loc),
            bounds_safe=e.bounds_safe,
            record_getitem=False,
            form=form,
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        if isinstance(e, TpyFloatLiteral) and not math.isfinite(e.value):
            raise ThirUnsupported("expr.float_literal.nonfinite")
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
                                         part.format_spec is not None)
                if wrap is _FSTRING_INELIGIBLE:
                    note_detail("fstring.arg_wrap")
                    raise ThirUnsupported("expr.fstring")
                if part.format_spec is not None:
                    _witness("fstr.spec")
                try:
                    lowered_part = _lower_expr(
                        part.expr, lc, declared,
                        field_owned_str_ok=isinstance(
                            part.expr, TpyFieldAccess))
                except ThirUnsupported:
                    raise ThirUnsupported("expr.fstring") from None
                parts.append(THIRFStringArg(
                    expr=lowered_part, wrap=wrap,
                    format_spec=part.format_spec))
        # An owned std::string result: STORAGE form, so it lands bare in owned
        # sinks (no view->owned wrap), like an owned-str call result.
        return THIRFString(result_type=rtype, parts=tuple(parts),
                           form=Form.STORAGE, loc=loc)
    if isinstance(e, TpyBinOp):
        return _lower_binop(e, rtype, lc, declared, loc,
                            fold_ok=use.literal_fold_ok,
                            slot_threaded=target_type is not None,
                            temps_ok=use.allow_temps)
    if isinstance(e, TpyUnaryOp):
        # A negated int literal folds to a plain literal (the AST's
        # _gen_unaryop literal-negation branch renders the negated value
        # directly); otherwise only logical `not` is admitted (bool operand).
        neg = _folded_neg_int_literal(e, analyzer)
        if neg is not None:
            return _lower_int_literal(neg, rtype, lc, loc)
        # IntEnum negation: `(-static_cast<U>(p))` (_gen_unaryop's enum arm).
        enum_neg = _enum_neg_wrap(e, analyzer)
        if enum_neg is None and e.op != "!":
            # A resolved arithmetic dunder (`- + ~` on int/float/fixed-int):
            # expand its cpp_template over the operand -- _gen_unaryop's
            # gen_call_from_fi tail. A value-Optional operand takes the AST's
            # narrow-or-runtime-check path (unary_target=inner + warning), so
            # keep it on AST.
            resolved = e.resolved_unaryop
            operand_type = analyzer.get_expr_type(e.operand)
            if resolved is not None and resolved.method.cpp_template:
                # An UNPROVEN value-opt scalar operand unwraps through the
                # runtime-checked `deref_optional_check(x)` (the AST's
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
        if all(ExpressionGenerator._is_duplicable_expr(c)
               for c in e.comparators[:-1]):
            # Inline arm of _gen_chained_compare: left-fold the sema pairs with
            # the bare && (resolved None), reproducing `((a < b) && (b < c))`.
            # Each pair is a full TpyBinOp (sema-analyzed), so it lowers like any
            # comparison.
            folded = _lower_expr(e.pairs[0], lc, declared)
            for pair in e.pairs[1:]:
                folded = THIRBinOp(result_type=rtype, left=folded, op="&&",
                                   right=_lower_expr(pair, lc, declared),
                                   resolved=None, loc=loc)
            return folded
        return _lower_chained_compare_stmtexpr(e, rtype, lc, declared, loc)
    if isinstance(e, TpyNamedExpr):
        # _gen_named_expr's target-class ladder. Lowered classes: value
        # scalars (`(n = v)`), pointer-repr Optional targets
        # (`T* n = nullptr;` + the borrow-lifted assign), borrow-alias
        # pointer targets (`(n = &(v), *n)`). The remaining classes
        # (tuples, non-value optional slots, hoisted names) stay AST --
        # those renders carry registry side effects not yet mirrored.
        # `walrus_predeclared` is FUNCTION-scoped like the AST's
        # walrus_pre_declared (not branch-restored), so a sibling-branch
        # re-bind assigns in place instead of re-declaring.
        vt = (resolve_int_literals(rtype, analyzer.ctx.default_int_for_literal)
              if rtype is not None else None)
        vtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vt)))
               if vt is not None else None)
        if vtu is None or e.target in lc.prescan.hoisted:
            raise ThirUnsupported("expr.walrus")
        need_predecl = (e.target not in lc.walrus_predeclared
                        and e.target not in declared)
        borrow_decls = analyzer.function_stmt_borrow_decls.get(
            id(lc.func), {})
        ever_owned = analyzer.function_ever_owned_locals.get(
            id(lc.func), set())
        # The pointer/slot/viewfam rungs are SYNC-only: a resumable body's
        # locals are frame fields (`(*b)` source reads, no named-row drain
        # for the predecls), a different render model -- only the
        # value-scalar tail below stays available there (sgen cond flush).
        resumable = lc.func.is_generator or lc.func.is_async
        if (not resumable
                and e.target in borrow_decls and e.target not in ever_owned
                and is_plain_nonvalue(vtu)):
            # Borrow-alias pointer target (`(q := b)` on a record/container
            # lvalue, `(v := h.view())` on a ref-returning call):
            # `[const ]T* q = nullptr;` + `(q = &(v), *q)`; an
            # already-pointer NAME source assigns bare. Slot globals stay
            # AST (the AST's `&(g)` render addresses the slot pointer, an
            # un-mirrored shape).
            src = e.value
            if not (_f1_record(vtu, analyzer)
                    or _alias_ref_container(vtu)):
                note_detail("walrus.borrow_alias_shape")
                raise ThirUnsupported("expr.walrus")
            if isinstance(src, TpyName):
                if (src.name not in declared
                        or src.name in lc.prescan.global_slots):
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
            else:
                note_detail("walrus.borrow_alias_shape")
                raise ThirUnsupported("expr.walrus")
            if isinstance(lowered_value, THIRName) and lowered_value.deref:
                # The comma form consumes the raw pointer; the value-read
                # deref belongs to the `*n` tail, not the source.
                lowered_value = replace(lowered_value, deref=False)
            is_const = borrow_decls[e.target]
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
            # Pointer-repr Optional target (`(t := h.opt)`): `T* t =
            # nullptr;` + the borrow-lifted assign -- a storage-Optional
            # FIELD source lifts via optional_to_ptr (THIRFormConvert
            # BORROW), `None` assigns nullptr, an already-pointer NAME
            # passes bare. Other sources stay AST.
            src_t = analyzer.get_expr_type(e.value)
            src_t = unwrap_readonly(src_t) if src_t is not None else None
            if (isinstance(e.value, TpyFieldAccess)
                    and _field_markers_clean(e.value)
                    and _field_receiver_ok(e.value, declared, analyzer)
                    and isinstance(src_t, OptionalType)
                    and unwrap_readonly(src_t.inner)
                    == unwrap_readonly(opt_ptr.inner)
                    and reads_storage_form_optional(analyzer, e.value)):
                lowered_value: THIRExpr = THIRFormConvert(
                    result_type=vtu,
                    value=_lower_field_source(e.value, lc, declared),
                    form=Form.BORROW, loc=loc)
            elif isinstance(e.value, TpyNoneLiteral):
                lowered_value = THIRLiteral(result_type=vtu, value=None,
                                            form=Form.BORROW, loc=loc)
            elif (isinstance(e.value, TpyName)
                  and e.value.name in lc.pointers
                  and e.value.name not in lc.prescan.global_slots):
                lowered_value = _lower_expr(e.value, lc, declared)
            else:
                note_detail("walrus.opt_ptr_src")
                raise ThirUnsupported("expr.walrus")
            declared[e.target] = vtu
            lc.pointers.add(e.target)
            cpp_type = None
            if need_predecl:
                cpp_type = lc.render_type(unwrap_readonly(opt_ptr.inner)) + "*"
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
            # Non-call sources and hoisted/const targets stay AST.
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
            # tuple_source: the owning `__slot_N.emplace(...)` consumes the
            # call result whole, exactly like the tuple-unpack capture.
            lowered_value = _lower_expr(
                e.value, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             tuple_source=True))
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
        if (not resumable
                and isinstance(vtu, TupleType)
                and vtu.has_pointer_repr_element()
                and e.target not in lc.prescan.reassigned
                and need_predecl):
            # Non-reassigned borrow-tuple walrus (`(t := (1, b))` /
            # `(t := items[0])`): predecl `std::tuple<..., T*> t;` + the
            # plain assign -- a literal renders borrow-form, a storage
            # lvalue subscript lifts via tuple_to_pointer (the btuple
            # reseat arm's value split). Reassigned (rebind-slot) walruses
            # and rvalue-call sources stay AST.
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
                and is_plain_nonvalue(vtu) and e.target in ever_owned
                and need_predecl
                and (_f1_record(vtu, analyzer)
                     or _alias_ref_container(vtu))
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
                comp_ptrs = {n for n in lc.pointers
                             if _optional_ptr_borrow(declared.get(n),
                                                     analyzer) is None}
                lowered_value: THIRExpr = _lower_comprehension(
                    e.value, vtu, lc, declared, comp_ptrs)
            else:
                lowered_value = _lower_expr(
                    e.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE))
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
        if (not resumable
                and _value_opt_scalar(vtu, analyzer) is not None
                and not need_predecl):
            # Value-opt scalar REASSIGN (`(x := None)` on a declared
            # `Int32 | None` local): plain in-place assign -- `None` renders
            # `std::nullopt`, a scalar RHS assigns bare (the optional's
            # converting assignment). First-decl walruses stay AST.
            if isinstance(e.value, TpyNoneLiteral):
                lowered_value = THIRLiteral(result_type=vtu, value=None,
                                            form=Form.STORAGE, loc=loc)
            else:
                lowered_value = _lower_expr(e.value, lc, declared,
                                            target_type=vtu,
                                            allow_whole_optional=True)
            declared[e.target] = vtu
            lc.value_opt_locals.add(e.target)
            _witness("expr.walrus_value_opt")
            return THIRWalrus(
                result_type=vtu, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, loc=loc)
        wal_str = _resolved_str_value(vtu, analyzer)
        wal_bytes = _resolved_bytes_value(vtu, analyzer)
        wal_owned = (wal_str if wal_str is not None
                     and not is_str_view_type(wal_str)
                     else wal_bytes if wal_bytes is not None
                     and not is_bytes_view_type(wal_bytes) else None)
        if wal_owned is not None and not resumable:
            # Owned str/bytes REASSIGN (`(s := s + "!")` -- the view->owned
            # promotion already resolved the binding owned): plain in-place
            # assign of the owned RHS. First-decl and view-form targets
            # stay AST (pending-view predecl unmirrored).
            if need_predecl:
                note_detail("walrus.viewfam_first_decl")
                raise ThirUnsupported("expr.walrus")
            lowered_value = _lower_expr(e.value, lc, declared,
                                        target_type=wal_owned)
            declared[e.target] = wal_owned
            _witness("expr.walrus_owned_viewfam")
            return THIRWalrus(
                result_type=wal_owned, name=e.target,
                cpp_name=escape_cpp_name(e.target),
                value=lowered_value, loc=loc)
        if (not (_eligible_scalar(vtu) or _eligible_char(vtu))
                or isinstance(vtu, OptionalType)):
            raise ThirUnsupported("expr.walrus")
        lowered_value = _lower_expr(e.value, lc, declared, target_type=vtu)
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
                              TupleType)):
            note_detail("ifexpr.result_type")
            raise ThirUnsupported("expr.ifexpr")
        return _lower_if_expr(e, rtype, lc, declared, loc,
                              cond_temps_ok=use.allow_temps)
    if isinstance(e, TpyCall):
        if not isinstance(e.func, TpyName):
            # An expression callee (`make_adder(10)(5)`, `fns[i](x)`) has a
            # non-Name func; every call arm below reads `e.func_name` (which
            # asserts a Name callee), and no arm supports expression callees,
            # so reject early -- it falls back to AST as before.
            raise ThirUnsupported("expr.call")
        if (e.func.name == "ord"
                and len(e.args) == 1
                and isinstance(e.args[0], TpyStrLiteral)
                and len(e.args[0].value) == 1):
            # ord("X") over a single-char literal folds to its constant
            # ordinal, matching the AST's `str(ord(arg.value))` -- a bare int
            # in every position (the runtime `ord(s)` rides the native arm).
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
            # those route via the narrow-if arms or fall back whole.
            # Narrowed / re-spelled subjects and non-pointer-shaped
            # pointer-locals keep the fallback.
            vvar, vmembers, _vd = vinfo
            if (vvar in lc.narrow.narrowed or vvar in lc.narrow.spelled
                    or (vvar in lc.pointers
                        and not polymorphic_source_is_pointer(
                            _poly_subject_decl(declared.get(vvar))))):
                raise ThirUnsupported("expr.call")
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
            raise ThirUnsupported("expr.call")
        if e.dunder_call is not None:
            # `obj(args)` where obj has a __call__ method: sema synthesized the
            # equivalent `obj.__call__(args)` method call. The AST's _gen_call
            # dunder_call arm delegates to _gen_method_call, so lowering routes
            # the synthetic node through the same method-call path (which gates
            # its own use), rather than the free-call machinery that rejects it.
            _witness("call.dunder_call")
            return _lower_expr(e.dunder_call, lc, declared, use=use)
        # `own_iter(x)` -> `::tpy::own_iter(std::move(x))` -- the
        # special-builtin arm (_gen_own_iter_expr). A bare un-narrowed,
        # non-pointer NAME source only (the corpus face); other sources
        # keep the AST arm.
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
        if not _call_use_supported(e, lc, declared, use,
                                   allow_whole_optional=allow_whole_optional):
            raise ThirUnsupported("expr.call")
        if e.macro_expansion is not None:
            # `@call_macro` / getattr / hasattr: the AST renders the
            # sema-synthesized replacement in place (gen_expr's macro arm), so
            # the call node lowers to its expansion.
            _witness("call.macro_expansion")
            return _lower_expr(e.macro_expansion, lc, declared)
        if e.dyn_hasattr_call is not None:
            # `hasattr(obj, name)` runtime probe: the try/catch stmt-expr
            # over the synthesized `obj.__getattr__(name)`
            # (_gen_dyn_hasattr_block).
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
            # yielding the dunder's result or the default on AttributeError
            # (_gen_dyn_getattr_default_block). The default renders against
            # the dunder's return type, like the AST's gen_expr target.
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
                raise ThirUnsupported("expr.call")
            if e.cast_source_is_any:
                # `typing.cast(T, x)` from Any: the runtime checked-extract
                # `::tpy::any_cast_or_panic<T>(x)` (_gen_call's Any arm).
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
                raise ThirUnsupported("expr.call")
            enum_arg_type = analyzer.get_expr_type(e.args[0])
            if (not _resolved_scalar(enum_arg_type, analyzer)
                    or (_runtime_bigint(enum_arg_type, analyzer)
                        and _const_index(
                            _unwrap_lit_coerce(e.args[0])) is not None)):
                note_detail("call.enum_from_value.arg")
                raise ThirUnsupported("expr.call")
            # `E(x)` -> `::tpy::EnumUtil<E>::from_value(x)` (gen_expr's
            # enum_from_value arm). A runtime-BigInt arg takes the checked
            # `({0}).to_fixed_check<U>()` wrap over the enum's underlying
            # type (the gate keeps literal-BigInt args out). Rides THIRCall's
            # cpp_template expansion like a scalar type-constructor.
            spelled = enum_cpp_name(e.enum_from_value,
                                    analyzer.ctx.module_name)
            arg = _lower_expr(e.args[0], lc, declared)
            if _runtime_bigint(analyzer.get_expr_type(e.args[0]), analyzer):
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
                        raise ThirUnsupported("expr.call")
                    td_args.append(_lower_call_arg(
                        a, p_type, lc, declared, temp_args=False,
                        protocol_slots=True))
                return THIRCtorCall(
                    result_type=rtype, type_cpp=td_type_cpp,
                    args=tuple(td_args), form=Form.STORAGE, loc=loc)
        if fi is not None and fi.is_constructor:
            if not _record_ctor_shape_supported(e, lc, use):
                raise ThirUnsupported("expr.call")
            # A same-module user-record ctor rvalue (the `Own[union]`-slot
            # arg): _gen_call's record-branch tail renders the RAW source
            # name over the (gate-restricted) args. `fi` is sema's synthetic
            # constructor fi, whose params mirror the resolved __init__'s.
            # A record-rvalue arg keys on the slot's mutation
            # (_gen_record_ctor_args's ctor_mutated arm): a MUTATED ref slot
            # hoists the named temp (`A __tmp_N = A(1); Cls(__tmp_N)`,
            # gate-admitted only at flush positions), a const slot binds the
            # inline prvalue expansion through the plain arg path.
            # The INSTANTIATION form (`Cell[Int32]()` / `Poll[T]()` /
            # inferred `Pair(1, 2)`, call_type set) spells the rendered
            # type over the same arg machinery (_gen_call's call_type-arm
            # tail: `type_to_cpp(call_type)(args)`); the raw-name form
            # renders the bare source name.
            brace_ctor = False
            if e.call_type is not None:
                _witness("ctor.instantiation")
                type_cpp = lc.render_type(e.call_type)
            else:
                # A cross-module record spells the declaring module's
                # qualification (_gen_call's record-branch qual arm); the
                # same-module face keeps the RAW source name. Derive the
                # record from the ctor's sema RESULT type -- the AST's
                # qname-corrected lookup -- so a short-name collision can't
                # split the two paths (lowering rejects those shapes, but the
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
                    # A plain @native record spells record_info.native_name
                    # (the AST record-branch native arm); @native_c PODs
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
                        type_cpp = e.func_name
            ctor_mut = fi.mutated_params or frozenset()
            eff_params = fi.params
            if not eff_params and e.args:
                # Inherited `__init__` (param-less synthetic fi): read the
                # registry triples like the AST arg loop; the gate pinned
                # the arity against the same source.
                eff_params = _ctor_effective_params(
                    e, lc.analyzer.registry.get_record_for_type(rtype))
            args = []
            for i, (a, p) in enumerate(zip(e.args, eff_params)):
                if not _record_ctor_arg_supported(
                        a, p.type, i, fi, lc, declared, use):
                    note_detail(
                        "call.ctor_arg." + _type_family_tag(p.type, analyzer))
                    raise ThirUnsupported("expr.call")
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
                        result_type=rec, cpp_type=rec.to_cpp(),
                        init=_lower_expr(a, lc, declared), form=Form.BORROW,
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
                    # temp_args is scoped to the specific temp-hoisting rows
                    # the gate admitted: the Own-slot copy cascade and the
                    # member-ctor-rvalue union lift (`pv{&__tmp_N}`). A blanket
                    # temp_args would re-shape the other temp rows in
                    # _lower_call_arg (record rvalue into a const ref slot,
                    # member-NAME union) whose ctor renders are BARE on the AST
                    # path.
                    flush_slot = (
                        _own_lvalue_temp_slot(a, p.type, lc.analyzer) is not None
                        or _union_ctor_temp_arg(a, p.type, lc.analyzer)
                        or _protocol_arg_slot(p.type) is not None
                        # The member-valued VALUE-union temp (`std::variant
                        # <...> __tmp_N = v;`), gate-admitted above.
                        or _value_union_temp_slot(
                            a, p.type, declared, lc.analyzer) is not None
                        # The optional-ptr 'ctor' face's ArgTemp (`T __tmp_N =
                        # <rvalue>; ...&__tmp_N`), gate-admitted under temps_ok.
                        or _optional_ptr_arg_face(
                            a, p.type, declared, lc.analyzer) == 'ctor')
                    args.append(_lower_call_arg(
                        a, p.type, lc, declared,
                        temp_args=temp_args and flush_slot,
                        protocol_slots=True))
            return THIRCtorCall(
                result_type=rtype, type_cpp=type_cpp,
                args=tuple(args), brace_init=brace_ctor,
                form=Form.STORAGE, loc=loc)
        if (isinstance(e.call_type, PtrType) and not e.args and not e.kwargs
                and e.double_star_unpack is None):
            # A pointer null constructor `Ptr[T]()` / `Ptr[readonly[T]]()` ->
            # a typed nullptr `static_cast<T*>(nullptr)` (_gen_call's PtrType
            # call_type arm); the spelling rides render_type like the AST's
            # type_to_cpp.
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
            if (not e.args and not e.kwargs and e.double_star_unpack is None
                    and e.subscript_callee is None and not e.type_args
                    and ((fam is not None and _storage_call_container(fam))
                         # The zero-arg render is the default ctor spelled off
                         # `call_type` -- it does not read the ELEMENT type, so
                         # the element-keyed `_storage_call_ret` verdict (which
                         # exists for the downstream READ shapes) does not gate
                         # it. Record-element containers route here too; their
                         # consumers gate themselves.
                         or (rt_bare is not None
                             and (is_list(rt_bare) or is_dict(rt_bare)
                                  or is_set(rt_bare))))):
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
                # result is not a storage container, so the AST renders the
                # resolved ctor's @cpp_template over inline args -- the same
                # expansion as the bare-name __init__ type-ctor path below.
                _witness("call.viewfam_instantiation")
                owned_str_ctor = _is_string_owned(rtype)
                lowered_args = []
                for a, p in zip(e.args, vf_fi.params):
                    if owned_str_ctor and not _str_pass_through_arg(
                            a, p.type, declared, analyzer):
                        note_detail("call.type_ctor.owned_str_arg")
                        raise ThirUnsupported("expr.call")
                    lowered_args.append(_lower_call_arg(a, p.type, lc, declared))
                return THIRCall(
                    result_type=rtype,
                    callee=e.func_name,
                    args=tuple(lowered_args),
                    cpp_template=vf_fi.cpp_template,
                    loc=loc,
                )
            if _view_ctor_bare_source(e, rtype, declared, analyzer):
                # A `Span[...]` / `Array[...]` instantiation over a bare
                # container/view NAME (`Span[readonly[Int32]](lst)` ->
                # `std::span<const int32_t>(lst)`): a VALUE view, so the AST
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
            inst_fi = _instantiation_call_fi(e)
            if (inst_fi is None or fam is None
                    or not _storage_call_container(fam)):
                note_detail("call.inst_shape")
                raise ThirUnsupported("expr.call")
            lowered_args: list[THIRExpr] = []
            for arg, param in zip(e.args, inst_fi.params):
                if _is_range_call(arg):
                    range_fi = arg.resolved_function_info
                    if (len(arg.args) not in (1, 2, 3) or range_fi is None
                            or not range_fi.cpp_template
                            or not _eligible_scalar(
                                _range_counter_type(arg, analyzer))):
                        note_detail("call.inst_range_shape")
                        raise ThirUnsupported("expr.call")
                    lowered_args.append(_lower_range_object(arg, lc, declared))
                elif (isinstance(arg, TpyCall)
                      and arg.resolved_function_info is not None
                      and arg.resolved_function_info.is_constructor
                      and (_ctor_shape_ok(arg, analyzer)
                           or _ctor_instantiation_ok(arg, analyzer))):
                    # A user-iterator ctor rvalue (`dict(PairIter(3))`): the AST
                    # renders the bare ctor into the dict_construct template, a
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
                    _witness("call.inst_gen_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
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
                elif isinstance(arg, TpyGeneratorExpression):
                    # A genexpr rvalue (`list(x * x for x in xs)`): the
                    # make_generator IIFE binds directly; _lower_genexpr
                    # rejects the shapes outside its slice (body fallback).
                    _witness("call.inst_genexpr_arg")
                    lowered_args.append(_lower_expr(
                        arg, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.ITERABLE)))
                else:
                    if (not isinstance(arg, TpyName) or arg.name == "self"
                            or arg.name not in declared):
                        note_detail("call.inst_arg_shape")
                        raise ThirUnsupported("expr.call")
                    arg_type = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(declared[arg.name])))
                    if not (is_list(arg_type) or is_dict(arg_type)
                            or is_set(arg_type)):
                        note_detail("call.inst_arg_shape")
                        raise ThirUnsupported("expr.call")
                    if id(arg) in analyzer.ctx.all_last_uses:
                        # A movable last-use name takes the consuming-
                        # __iter__ wrap (`dict(pairs)` ->
                        # `::tpy::own_iter(std::move(pairs))`); every other
                        # last-use shape keeps rejecting.
                        wrapped = _consuming_iter_wrap(
                            arg, param.type, lc, declared)
                        if wrapped is None:
                            note_detail("call.inst_arg_lastuse")
                            raise ThirUnsupported("expr.call")
                        lowered_args.append(wrapped)
                    else:
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
            # A scalar or slice-object type-constructor call (`Int32(x)` /
            # `basic_slice(1, 3)`): the emit is the resolved __init__ overload's
            # @cpp_template expanded over the args with no receiver. Sema
            # already substituted {cpp} / class type params; positional-only
            # templates are carried verbatim. A `None` bound in a slice-ctor's
            # value-repr `Int32 | None` slot renders `std::nullopt` (the
            # STORAGE-form None). A native-FUNCTION ctor (`int(str)` ->
            # `tpy::BigInt::from_str`, float/bytes from_str, `Char(s)` ->
            # `char_from_str`) carries no cpp_template and is excluded by the
            # `not fi.native_function` guard above, routing through the native
            # free-call path (`native_free_ctor` in checks.py) instead.
            template_fi = _template_init_call_fi(e)
            if template_fi is None:
                note_detail("call.type_ctor.no_template_fi")
                raise ThirUnsupported("expr.call")
            scalar_ctor = _eligible_scalar(rtype)
            slice_ctor = _slice_object_type(rtype)
            owned_str_ctor = _is_string_owned(rtype)
            # `bytearray()` -- the template expands to the plain
            # `std::vector<uint8_t>()` construction, no different in kind from
            # the scalar ctors above; it was simply absent from the result-kind
            # list. ARG-LESS only: `bytearray(n)` / `bytearray(b"..")` never
            # reach this arm at all (they resolve as plain calls and are
            # decided by the free-call arg ladder), so an arg rule here would
            # be dead code.
            bytearray_ctor = (not e.args and rtype is not None
                              and is_bytearray_type(unwrap_readonly(
                                  unwrap_ref_type(unwrap_send_sync(rtype)))))
            if not (scalar_ctor or slice_ctor or owned_str_ctor
                    or bytearray_ctor
                    or _resolved_viewfam_value(rtype, analyzer) is not None):
                note_detail("call.type_ctor.result_kind")
                raise ThirUnsupported("expr.call")
            if bytearray_ctor:
                _witness("call.type_ctor.bytearray")
            lowered_args = []
            for a, p in zip(e.args, template_fi.params):
                if scalar_ctor and not (
                        _ctor_arg_slot_ok(p.type, analyzer)
                        and _resolved_scalar(
                            analyzer.get_expr_type(a), analyzer)):
                    note_detail("call.type_ctor.scalar_arg")
                    raise ThirUnsupported("expr.call")
                if slice_ctor and not (
                        isinstance(a, TpyNoneLiteral)
                        or _resolved_scalar(
                            analyzer.get_expr_type(a), analyzer)):
                    note_detail("call.type_ctor.slice_arg")
                    raise ThirUnsupported("expr.call")
                if owned_str_ctor and not (
                        _str_pass_through_arg(a, p.type, declared, analyzer)
                        # `String(Int32(42))` / `String(True)`: sema resolved
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
                    raise ThirUnsupported("expr.call")
                lowered_args.append(_lower_call_arg(a, p.type, lc, declared))
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

        # `copy(x)` of an open-T source: the bespoke special-builtin arm's
        # general tail (`{arg_type.to_cpp()}({deref})` -> `T(this->value)`).
        # NAME/FIELD sources only, un-narrowed, non-pointer -- concrete
        # copies (records / containers / tuples) keep their AST sub-arms.
        if (fi is not None and fi.qualified_name == qnames.COPY
                and len(e.args) == 1 and not e.kwargs):
            src = e.args[0]
            st = analyzer.get_expr_type(src)
            if (_tparam_value(st)
                    and isinstance(src, (TpyName, TpyFieldAccess))
                    and not (isinstance(src, TpyName)
                             and (src.name in lc.pointers
                                  or src.name in lc.narrow.narrowed))):
                stu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
                _witness("call.copy_tparam")
                return THIRCall(
                    result_type=rtype, callee=e.func_name,
                    args=(_lower_expr(src, lc, declared),),
                    cpp_template=f"{stu.to_cpp()}({{0}})",
                    loc=loc)
            stu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
            # `copy(x)` of a concrete container source (`copy(d.get(k, dflt))`):
            # the AST's general `{arg_type.to_cpp()}(gen_expr_deref(arg))` tail
            # -> `std::vector<T>(<src>)`. Containers are never pointer-locals, so
            # the deref is a no-op; a pointer-local / narrowed NAME source is
            # excluded defensively, matching the tparam arm's guard.
            if ((is_list(stu) or is_dict(stu) or is_set(stu))
                    and not (isinstance(src, TpyName)
                             and (src.name in lc.pointers
                                  or src.name in lc.narrow.narrowed))):
                _witness("call.copy_container")
                # STORAGE use: the container-returning source is copied into the
                # `T(...)` rvalue, so a container-returning method/free call is
                # admitted at the storage sink (its `_storage_call_ret` gate).
                return THIRCall(
                    result_type=rtype, callee=e.func_name,
                    args=(_lower_expr(
                        src, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE)),),
                    cpp_template=f"{stu.to_cpp()}({{0}})",
                    loc=loc)
            raise ThirUnsupported("expr.call")
        fold_fi = e.resolved_function_info
        if (fold_fi is not None
                and fold_fi.owning_type_qname == "builtins.float"
                and len(e.args) == 1 and not e.kwargs
                and isinstance(e.args[0], TpyStrLiteral)):
            fold_cpp = _FLOAT_STR_CONSTANTS.get(
                e.args[0].value.strip().lower())
            if fold_cpp is not None:
                # `float("nan")` / `float("inf")`: the AST folds the str
                # constant to its spelled numeric-limits expression.
                _witness("call.float_str_fold")
                return THIRCall(result_type=rtype, callee=e.func_name,
                                args=(), cpp_template=fold_cpp, loc=loc)
        k = _free_callee_kind(
            e, analyzer,
            generator_ok=use.result is _ExprResultUse.ITERABLE,
            error_return_ok=True,
            coro_factory_ok=use.coro_factory,
            ret_cast_ok=True)
        len_call = _is_len_call(e, declared, analyzer)
        if not len_call:
            if k is None or fi is None:
                raise ThirUnsupported("expr.call")
            if not _call_arity_ok(e, fi):
                note_detail("call.arity_defaults")
                raise ThirUnsupported("expr.call")
            # A str literal into a str/StrView slot of a multi-overload callee
            # takes gen_call_arg's `param_view_t("...")` pin (mirrored per-arg
            # in `_lower_free_call_arg`); a str literal into ANY other slot (a
            # `Literal[...]` mode selector, `Char`, `String`) renders through
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
        # Args lower against their param slots: a str literal in a Char slot
        # renders as a char literal, a bytes literal into a bytes/BytesView
        # slot takes gen_call_arg's static-span pin, a union-slot arg reads
        # the callee's deep-const verdict (`deep_const_borrow_params`, the
        # AST's `is_readonly_target`) for the const-pointee spelling. A `len`
        # call bypasses the arity gate, so fall back to slot-less lowering
        # there. Provided args pair the LEADING params (the AST loop
        # zip-truncates): the arity gate admits omitted trailing defaults.
        params = (fi.params if fi is not None
                  and len(fi.params) >= len(e.args) else None)
        dcbp = fi.root.deep_const_borrow_params if fi is not None else None
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
                    # narrowing static_cast (_maybe_native_return_cast).
                    # The error_return guard mirrors the admission gate
                    # (ret_cast_ok): the AST SKIPS the cast under
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
        return _er_wrap(THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(
                _lower_free_call_arg(
                    e, a, params[i].type if params else None, k, lc, declared,
                    temp_args=temp_args,
                    readonly_target=(params is not None
                                     and dcbp is not None
                                     and i in dcbp))
                for i, a in enumerate(e.args)),
            native_name=native_name,
            cpp_template=cpp_template,
            callee_cpp=callee_cpp,
            form=form,
            loc=loc,
        ))
    if isinstance(e, TpyListRepeat):
        # `[elems] * count` -- the _gen_list_repeat mirror. A None/protocol/Span
        # target uses the sema-RESOLVED type (protocols have no concrete
        # container; a Span can't be range-constructed); otherwise the annotated
        # target drives the element/result types.
        use_resolved = (target_type is None
                        or is_protocol_type(target_type)
                        or is_span(target_type))
        # `rtype` is already resolve_pending_container'd above; a Span/protocol
        # target uses it (they have no range-constructible container form).
        result_type = rtype if use_resolved else target_type
        elem_type = (result_type.get_element_type()
                     if result_type is not None else None)
        if isinstance(elem_type, IntLiteralType):
            elem_type = analyzer.ctx.default_int_type
            if is_list(result_type):
                result_type = make_list(elem_type)
        if (elem_type is None or result_type is None
                or not (is_list(result_type) or is_array(result_type))):
            # Materialized list + Array only. A lazy `ListRepeatType` result
            # (`x = [v]*n` kept unmaterialized) stays on the AST path: a lazy
            # local passed to a protocol param crashes render_type on its still-
            # pending arg type (a pre-existing latent crash, TODO). A pending /
            # off-family result also falls back here rather than reaching an
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
        return THIRListRepeat(
            result_type=result_type, elements=elements, count=count,
            count_bigint=count_bigint, elem_cpp=elem_cpp,
            result_cpp=result_type.to_cpp(), loc=loc)
    if isinstance(e, (TpyArrayLiteral, TpySetLiteral)):
        container_type = target_type or rtype
        if not _container_literal_shape_ok(
                e, container_type, analyzer, threaded=container_threaded):
            raise ThirUnsupported("expr.container_literal")
        # A container-literal decl init / return / nested element. result_type
        # is the RESOLVED container (list vs Array already decided by sema);
        # the emit dispatches on its family. Elements lower through the
        # per-slot owned-str/bytes wraps (S5/S6), the F2 pointer-local deref,
        # and the last-use move mirror. A LIST literal's SCALAR elements
        # render target-less on the AST path (bare `{10, 20}` into the
        # vector's brace init) -- but a demoted/annotated ARRAY's and a set's
        # DO thread the element target (probe-verified: `std::array` elements
        # take the Float32 `f` suffix / `::tpy::BigInt(N)` wraps a vector's
        # elements never get), so retype keys on the RESOLVED container kind,
        # not the literal's source shape.
        args = getattr(container_type, "type_args", None)
        slot = args[0] if args else None
        # `array_retype=False` is the native CALL-ARG position: the AST
        # threads an element target only at Array DECLS (the `::tpy::BigInt(N)`
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
        # the Array family never switches (mirrors _gen_array_literal /
        # _gen_set_literal).
        make = False
        elem_cpp = None
        if e.elements and not is_array(container_type):
            if (any(_container_elem_move_source(x, lc) for x in e.elements)
                    or _container_nocopy_elem(slot, lc.analyzer)):
                make = True
                _witness("containerlit.make")
                if not isinstance(e, TpySetLiteral):
                    # make_vector spells its element type via the resolver
                    # (the AST's types.type_to_cpp); make_ordered_set derives
                    # its spelling from result_type in the emit (to_cpp, like
                    # the brace arm).
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
        return THIRContainerLiteral(
            result_type=container_type,
            elements=tuple(
                _lower_checked_container_elem(
                    x, slot, lc, declared,
                    threaded=(True if isinstance(e, TpySetLiteral)
                              else container_threaded),
                    forced=(isinstance(e, TpySetLiteral)
                            or is_array(container_type)),
                    # Set RECORD elements route: the copyable-record moves /
                    # make_ordered_set path renders like the list rows
                    # (probe-verified vs the oracle). Nested/optional set
                    # elements stay conservative.
                    allow_record=True,
                    allow_nested=not isinstance(e, TpySetLiteral),
                    allow_optional=not isinstance(e, TpySetLiteral),
                    retype_scalars=retype,
                    # SET literals excluded: this arm serves list, Array AND
                    # set, and only the first two are probed against the AST's
                    # frame-emplace render. A non-value tuple in a set needs a
                    # hashable record element, so the shape may not exist at
                    # all -- but "probably unreachable" is what made the
                    # previous keying look safe, so it keeps the wrap.
                    frame_bare_tuple=not isinstance(e, TpySetLiteral))
                for x in e.elements),
            make_container=make,
            elem_cpp=elem_cpp,
            loc=loc,
        )
    if isinstance(e, TpyDictLiteral):
        container_type = target_type or rtype
        if not _container_literal_shape_ok(
                e, container_type, analyzer, threaded=container_threaded):
            raise ThirUnsupported("expr.container_literal")
        args = getattr(container_type, "type_args", None)
        kslot = args[0] if args else None
        vslot = args[1] if args and len(args) > 1 else None
        if e.keys:
            _witness_container_elem_fam(vslot, lc.analyzer)
        # make_ordered_map for a non-copyable or last-use-movable key/value
        # (the nocopy check reads the VALUE type only -- mirrors
        # _gen_dict_literal).
        make = bool(e.keys) and (
            any(_container_elem_move_source(x, lc)
                for pair in zip(e.keys, e.values) for x in pair)
            or _container_nocopy_elem(vslot, lc.analyzer))
        if make:
            _witness("containerlit.make")
        return THIRContainerLiteral(
            result_type=container_type,
            elements=tuple(_lower_container_elem(k, kslot, lc, declared) for k in e.keys),
            values=tuple(
                _lower_checked_container_elem(
                    v, vslot, lc, declared, threaded=True, forced=True,
                    allow_record=True, allow_nested=True, allow_optional=True)
                for v in e.values),
            make_container=make,
            loc=loc,
        )
    if isinstance(e, TpyMethodCall):
        result_use = use.result
        if (result_use is _ExprResultUse.CONDITION
                and not is_bool_type(rtype)):
            raise ThirUnsupported("expr.method_call")
        iterable_override = (
            result_use is _ExprResultUse.ITERABLE
            and (_dict_view_iterable_ok(
                    e, declared, analyzer,
                    methods=("values", "keys", "items"))
                 or _str_list_method_iterable_ok(e, declared, analyzer)
                 or _member_gen_call_iterable_ok(e, declared, analyzer)))
        if e.is_nested_enum_constructor:
            if (not e.nested_type_name or e.kwargs
                    or e.double_star_unpack is not None or len(e.args) != 1):
                raise ThirUnsupported("expr.method_call")
            nested_t = analyzer.registry.get_enum(e.nested_type_name)
            arg_type = analyzer.get_expr_type(e.args[0])
            if (nested_t is None or _eligible_enum(nested_t, analyzer) is None
                    or not _resolved_scalar(arg_type, analyzer)
                    or _runtime_bigint(arg_type, analyzer)):
                raise ThirUnsupported("expr.method_call")
            # `Outer.Kind(v)` -> `::tpy::EnumUtil<Outer::Kind>::from_value(v)`
            # (_gen_method_call's nested-enum arm). Spelled via enum_cpp_name
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
            # `Outer.Inner(args)` -> `Outer::Inner(args)` (_gen_method_call's
            # nested-record-constructor arm). The type spelling is the AST's
            # own `NominalType(nested_type_name).to_cpp()` (module-qualified
            # cross-module); args render plainly, exactly like the enum arm and
            # the AST's bare gen_expr over each arg.
            if (not e.nested_type_name or e.kwargs
                    or e.double_star_unpack is not None):
                raise ThirUnsupported("expr.method_call")
            _witness("ctor.nested_record")
            type_cpp = NominalType(e.nested_type_name).to_cpp()
            return THIRCtorCall(
                result_type=rtype, type_cpp=type_cpp,
                args=tuple(_lower_expr(a, lc, declared) for a in e.args),
                form=Form.STORAGE, loc=loc)
        # A plain generic METHOD call's type args ride the member spelling
        # (`recv.method<targs>(args)`), so they are not a marker here. The
        # runtime-check receiver + targs combination is conservatively
        # rejected pending a corpus witness (the AST render does compose
        # them; this arm's deref_check face does not carry targs yet).
        if (e.type_args or e.inferred_type_args) and \
                e.needs_optional_runtime_check:
            raise ThirUnsupported("expr.method_call")
        if e.is_callable_field and not e.kwargs \
                and e.double_star_unpack is None:
            # Callable-field invocation (`h.cb(3)` -> `h.cb(3)`): the bare
            # member call over target-less args. Optional[Callable] fields
            # (the `.value()` unwrap) and non-name receivers stay AST.
            recv = e.obj
            # A `self` receiver renders the deref (`(*this).callback(x)`,
            # gen_expr_deref) -- unmirrored; local receivers only.
            cf_ok = (isinstance(recv, TpyName) and recv.name in declared
                     and recv.name not in lc.pointers
                     and recv.name not in lc.narrow.narrowed
                     and not (lc.prescan.has_self and recv.name == "self"))
            if cf_ok:
                rt0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[recv.name])))
                ft = None
                ri = (analyzer.registry.get_record_for_type(rt0)
                      if isinstance(rt0, NominalType) else None)
                if ri is not None:
                    for fld in ri.fields:
                        if fld.name == e.method:
                            ft = fld.type
                            break
                cf_ok = (ft is not None
                         and not isinstance(ft, OptionalType)
                         and _f1_record(rt0, analyzer))
            if not cf_ok:
                note_detail("method.marker.callable_field")
                raise ThirUnsupported("expr.method_call")
            _witness("method.callable_field")
            return THIRMethodCall(
                result_type=rtype if rtype is not None else VoidType(),
                receiver=_lower_expr(recv, lc, declared),
                method_cpp=escape_cpp_name(e.method),
                args=tuple(_lower_expr(x, lc, declared) for x in e.args),
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
                            temps_ok=use.allow_temps,
                            # A record-returning Ptr method under a postfix
                            # member (`s.Config.v` -- the property-getter
                            # receiver) renders bare; only the RECEIVER
                            # slice is witnessed.
                            record_ret_ok=(
                                result_use is _ExprResultUse.RECEIVER),
                            narrowed=frozenset(lc.narrow.narrowed))):
                    raise ThirUnsupported("expr.method_call")
                p_str = _resolved_str_value(rtype, analyzer)
                if p_str is None:
                    p_str = _resolved_bytes_value(rtype, analyzer)
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
                    is_arrow=e.ptr_non_null,
                    deref_check=not e.ptr_non_null,
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
                        temps_ok=use.allow_temps,
                        record_ret_ok=(
                            result_use is _ExprResultUse.RECEIVER
                            or use.ctx_manager
                            or (result_use in (_ExprResultUse.BORROW_BIND,
                                               _ExprResultUse.STORAGE)
                                and is_rvalue_source(analyzer, e))),
                        narrowed=frozenset(lc.narrow.narrowed)):
                    raise ThirUnsupported("expr.method_call")
                d_str = _resolved_str_value(rtype, analyzer)
                if d_str is None:
                    d_str = _resolved_bytes_value(rtype, analyzer)
                _witness("method.user_deref_chain")
                return THIRMethodCall(
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
                    is_arrow=(isinstance(e.obj, TpyName)
                              and _ptr_read_derefs(e.obj.name, lc)),
                    deref_chain=e.deref_depth,
                    form=_viewfam_result_form(d_str),
                    loc=loc,
                )
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
                    raise ThirUnsupported("expr.method_call")
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
                    raise ThirUnsupported("expr.method_call")
                for i, a in enumerate(e.args):
                    if not _container_method_arg_ok(
                            a,
                            sfi.params[i].type if i < len(sfi.params)
                            else None,
                            declared, analyzer,
                            param_names=lc.prescan.param_names,
                            narrowed=frozenset(lc.narrow.narrowed)):
                        raise ThirUnsupported("expr.method_call")
                s_str = _resolved_str_value(rtype, analyzer)
                if s_str is None:
                    s_str = _resolved_bytes_value(rtype, analyzer)
                _witness("method.user_deref_stub")
                return THIRMethodCall(
                    result_type=rtype if rtype is not None else VoidType(),
                    receiver=_lower_expr(
                        e.obj, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                    is_arrow=(isinstance(e.obj, TpyName)
                              and _ptr_read_derefs(e.obj.name, lc)),
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
                )
            # A receiver-less marker call (module-qualified / static): the
            # classifier selected it through _marker_call_kind, so the same
            # classification names the emit arm -- the pre-rendered
            # qualified spelling on callee_cpp or the @native symbol on
            # native_name, both existing THIRCall arms. Args lower against
            # their param slots like a free call's, but dcbp-BLIND
            # (readonly_target stays False): the method-call arg loop calls
            # _gen_union_arg without the deep-const verdict.
            iterable_gen = (result_use is _ExprResultUse.ITERABLE
                            and e.resolved_function_info is not None
                            and e.resolved_function_info.is_generator)
            mk = _marker_call_kind(e, analyzer, generator_ok=iterable_gen,
                                   coro_factory_ok=use.coro_factory,
                                   error_return_ok=error_return_raw)
            # An F1-record result renders bare under a postfix member
            # (RECEIVER) and, when it is an RVALUE source, directly into the
            # owned-record decl / storage slot (`Rc<A> r = Rc.new_(...);`) --
            # the free-call value-position mirror. A borrow-returning callee
            # is not an rvalue source and stays AST (the decl sink copies it
            # through machinery this arm does not mirror).
            record_ret = (result_use is _ExprResultUse.RECEIVER
                          # A with-manager rvalue (`with io.StringIO(s) as f:`):
                          # the record lands in the owned `auto __ctx_N` capture
                          # -- the record-method ladder's ctx_manager twin.
                          or use.ctx_manager
                          or (result_use in (_ExprResultUse.BORROW_BIND,
                                             _ExprResultUse.STORAGE)
                              and is_rvalue_source(analyzer, e)))
            if (mk is None or not _marker_call_supported(
                    e, mk, declared, analyzer,
                    stmt_position=result_use is _ExprResultUse.DISCARD,
                    temps_ok=use.allow_temps,
                    record_ret_ok=record_ret,
                    moved_ret_ok=result_use is _ExprResultUse.SUSPEND,
                    iterable_gen_ok=iterable_gen,
                    owned_tuple_ret_ok=(
                        result_use is _ExprResultUse.STORAGE
                        and use.tuple_source),
                    storage_ret_ok=result_use is _ExprResultUse.STORAGE,
                    value_opt_ret_ok=allow_whole_optional,
                    coro_factory_ok=use.coro_factory,
                    iterable_ret_ok=result_use is _ExprResultUse.ITERABLE,
                    narrowed=frozenset(lc.narrow.narrowed))):
                if mk is None:
                    note_detail(_marker_reject(e, analyzer))
                raise ThirUnsupported("expr.method_call")
            mfi = e.resolved_function_info
            _witness("call.module_native" if mk[0] == "native"
                     else "call.static_template" if mk[0] == "template"
                     else "call.generic_qualified" if mk[0] == "generic_qualified"
                     else "call.generic_static" if mk[0] in (
                         "generic_static", "generic_module_static")
                     else "call.marker_qualified")
            mk_str = _resolved_str_value(rtype, analyzer)
            if mk_str is None:
                mk_str = _resolved_bytes_value(rtype, analyzer)
            # A module-qualified generic call spells explicit template args
            # the way the AST does: type_to_cpp(unwrap_ref_type) per
            # inferred arg (NOT the free-call arm's to_cpp_stored). A
            # same-module generic STATIC splits them into class/method args
            # over the composed callee (`Cls<CA>::template m<MA>`).
            callee_cpp = mk[1] if mk[0] in ("qualified",
                                            "generic_qualified") else None
            mk_targs = (tuple(lc.render_type(unwrap_ref_type(t))
                              for t in e.inferred_type_args)
                        if mk[0] == "generic_qualified" else None)
            if mk[0] == "generic_static":
                callee_cpp, mk_targs = _generic_static_callee(e, lc)
            elif mk[0] == "generic_module_static":
                callee_cpp, mk_targs = _generic_module_static_callee(e, lc)
            return THIRCall(
                result_type=rtype if rtype is not None else VoidType(),
                callee=e.method,
                args=tuple(
                    _lower_marker_method_arg(
                        e, a, mfi.params[i].type, i, lc, declared,
                        temp_args=temp_args,
                        error_return_ok=error_return_raw)
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
        if not iterable_override:
            if isinstance(e.obj, TpyName):
                if e.obj.name not in declared:
                    note_detail("method.recv.name_absent")
                    raise ThirUnsupported("expr.method_call")
            elif not _method_nonname_receiver_ok(e.obj, declared, analyzer):
                note_detail(_recv_shape_reject(e.obj, declared, analyzer))
                raise ThirUnsupported("expr.method_call")
            if (e.needs_optional_runtime_check
                    and _optional_ptr_borrow_name(
                        e.obj, declared, analyzer) is None):
                raise ThirUnsupported("expr.method_call")
            # A consuming method's receiver-move render is mirrored only for
            # a bare non-pointer, non-narrowed name (never `self`): the AST
            # wraps exactly that shape in `std::move(name)`; indirect
            # receivers compose a deref the emit does not carry. An RVALUE
            # call receiver (`poll_once(aw).value()`) is already an rvalue
            # -- the AST moves NAME receivers only, so it renders bare
            # (move_receiver stays off for it below).
            consuming_ok = (
                (isinstance(e.obj, TpyName)
                 and e.obj.name in declared
                 and e.obj.name != lc.self_receiver
                 and e.obj.name not in lc.pointers
                 and e.obj.name not in lc.narrow.narrowed
                 and e.obj.name not in lc.inline_narrowed)
                or (isinstance(e.obj, (TpyCall, TpyMethodCall))
                    and is_rvalue_source(analyzer, e.obj)))
            if fi is None or not _plain_method_fi_ok(
                    fi, property_getter_ok=True, property_setter_ok=True,
                    coro_factory_ok=use.coro_factory,
                    consuming_ok=consuming_ok,
                    error_return_ok=error_return_raw):
                note_detail("method.fi_kind")
                raise ThirUnsupported("expr.method_call")
            if not _call_arity_ok(e, fi):
                note_detail("method.arity_defaults")
                raise ThirUnsupported("expr.method_call")

            recv_type = _method_receiver_type(e.obj, declared, analyzer)
            stmt_position = (result_use is _ExprResultUse.DISCARD
                             or use.truthy_discard)
            storage_ret_ok = result_use is _ExprResultUse.STORAGE
            # Builtin-stub receivers (container/set/str-view) render args
            # through gen_call_arg's `_args()` loop, which THREADS the raw
            # param type into literal renders; user-record methods pass
            # target_type=None (target-less literals). The flag picks the
            # literal render in _lower_call_arg.
            fam = _method_recv_family(recv_type, analyzer, lc.tparam_bounds)
            if fam is not None:
                shape_ok = fam.shape_ok(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position,
                    storage_ret_ok=storage_ret_ok,
                    borrow_ret_ok=result_use in (_ExprResultUse.RECEIVER,
                                                 _ExprResultUse.BORROW_BIND))
                stub_recv = fam.stub_recv
            elif recv_type is not None and recv_type.is_pointer():
                shape_ok = _ptr_template_method_supported(
                    e, fi, recv_type, analyzer,
                    stmt_position=stmt_position,
                    record_ret_ok=result_use in (_ExprResultUse.BORROW_BIND,
                                                  _ExprResultUse.RECEIVER))
                if shape_ok:
                    _witness("method.ptr_template")
            else:
                shape_ok = _record_method_call_supported(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position,
                    temps_ok=use.allow_temps,
                    record_ret_ok=(
                        result_use in (_ExprResultUse.BORROW_BIND,
                                       _ExprResultUse.RECEIVER)
                        # A with-manager rvalue (`with m.lock() as g:`): the
                        # guard record lands in the owned `__ctx_N` slot --
                        # the storage-sink twin of the owned-record decl.
                        or use.ctx_manager
                        # An owned-record RVALUE at a storage sink
                        # (`a.get().next = b.clone()` -- the Own return lands
                        # bare; the position gate pinned the slot).
                        or (result_use is _ExprResultUse.STORAGE
                            and is_rvalue_source(analyzer, e))),
                    storage_ret_ok=storage_ret_ok,
                    coro_factory_ok=use.coro_factory,
                    suspend_ok=(result_use is _ExprResultUse.SUSPEND),
                    iterable_ret_ok=(result_use is _ExprResultUse.ITERABLE),
                    value_opt_ret_ok=allow_whole_optional,
                    ptr_opt_passthrough=use.ptr_opt_passthrough,
                    narrowed=frozenset(lc.narrow.narrowed))
            if not shape_ok:
                raise ThirUnsupported("expr.method_call")
        if fi is None:
            raise ThirUnsupported("expr.method_call")
        # The member name mirrors _gen_method_call's resolution: @native rename
        # over the escaped source name (the LiteralType-mangled overload form is
        # gated out). A void method call carries no resolved expr type (None);
        # normalize so the node keeps a non-None result_type.
        member = (fi.native_name if fi.native_name and not fi.native_function
                  else escape_cpp_name(e.method))
        # A str-slice result carries its C++ shape like a THIRCall's (S5): an
        # owned-str method result (`xs.pop()`, std::string by value) is STORAGE
        # and lands bare in owned sinks. A bytes-family result rides the same
        # view/owned form tag (`bs.pop()` owned STORAGE, a bytes-view BORROW).
        m_str = _resolved_str_value(rtype, analyzer)
        if m_str is None:
            m_str = _resolved_bytes_value(rtype, analyzer)
        # Args lower against their param slots like a free call's (the record
        # pointer-local `(*p)` retag); provided args pair the LEADING params
        # (the AST loop zip-truncates; the arity gate admits omitted trailing
        # defaults). A user-record F2 pointer-local receiver renders `->`, as does
        # the method receiver itself (`self.helper()` -> `this->helper()`).
        # `temp_args` admits only the VALUE-union temp row here (the other
        # temp rows are free-call shapes -- a method ctor rvalue INLINES).
        params = (fi.params if fi is not None
                  and len(fi.params) >= len(e.args) else None)
        # A protocol receiver misses `_gen_method_call`'s user-record arg loop
        # (guarded by `is_user_record`) and falls to the lazy `_args()`
        # fallback, which renders args like a FREE call's -- literals take
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
                # `_gen_vararg_pack` render as a free call (the method arg loop
                # forwards the pack unchanged). Protocol receivers take the
                # free-call `_args()` fallback and are left on the AST path.
                # The slot's const-ness reads off the RAW method fi like the
                # AST's iter_params loop -- on a GENERIC method the resolved
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
                                       cpp_type=lc.render_type(ut),
                                       init=init, form=Form.VALUE,
                                       loc=getattr(a, "loc", None))
                # A temporary into a generic-record method's RAW T slot
                # hoists the named temp with the substituted type
                # (`Point __tmp_N = Point(10, 20);`) BEFORE the inline
                # renders -- the AST's TypeParamRef temp branch precedes its
                # arg tail the same way.
                tt = _tparam_slot_temp_arg(a, ptype, index, raw_method_fi,
                                           lc.analyzer)
                if tt is not None:
                    _witness("argtemp.generic_ref_slot")
                    return THIRArgTemp(
                        result_type=tt, cpp_type=tt.to_cpp(),
                        init=_lower_expr(a, lc, declared, target_type=tt),
                        form=Form.VALUE, loc=getattr(a, "loc", None))
                # A container LITERAL into a pointer-repr Optional[container]
                # slot: the typed `__tmp_N` + `&(__tmp_N)` face
                # (`s.get(url, None, {...})` -- _gen_optional_ptr_arg's
                # temporary face); the temp init is the target-threaded
                # literal render spelled at the slot's INNER.
                mcont = _optional_ptr_container_slot(ptype, lc.analyzer)
                if (mcont is not None
                        and _optional_ptr_container_literal_arg(
                            a, mcont, lc.analyzer)):
                    inner = unwrap_readonly(mcont.inner)
                    _witness("argtemp.optptr_container_literal")
                    return THIRArgTemp(
                        result_type=inner, cpp_type=lc.render_type(inner),
                        init=_lower_expr(a, lc, declared, target_type=inner,
                                         use=_NESTED_ARG_USE),
                        addr_of=True, form=Form.BORROW,
                        loc=getattr(a, "loc", None))
            # The Own-slot copy half needs the flush threaded into
            # `_lower_call_arg`'s copy+move arm -- scoped to the own-lvalue
            # slot on a USER-record method (a stub receiver's cpp_template
            # binds lvalues natively, so its copy renders bare; a protocol
            # receiver admits no Own slots). A blanket temp_args would
            # re-shape the record-rvalue / union temp rows, whose method
            # renders are BARE on the AST path.
            own_flush = (temp_args and not stub_recv and not proto_recv
                         and _own_lvalue_temp_slot(a, ptype, lc.analyzer)
                         is not None)
            # The deep-const verdict rides into the union const-wrap arm
            # (`z.names(other)` -> `ptr_variant_to_const<...>(other)`), like
            # the free-call loop. Read it off the RAW method fi: the
            # call-site substituted copy drops deep_const_borrow_params
            # (substitute_method_type_params does not carry it), so a
            # generic receiver's inferred verdict lives only on the raw fi
            # -- the same source the arg gate checks. The `fi` fallback is
            # LIVE, not defensive: the property-setter path has no registry
            # overload, so gate and arm both read the resolved setter fi.
            dcbp_fi = raw_method_fi if raw_method_fi is not None else fi
            ro_slot = bool(dcbp_fi is not None
                           and dcbp_fi.deep_const_borrow_params
                           and index in dcbp_fi.deep_const_borrow_params)
            return _lower_call_arg(a, ptype, lc, declared,
                                   temp_args=own_flush,
                                   nested_temps=temp_args,
                                   method_arg=not proto_recv,
                                   method_arg_stub=stub_recv and not proto_recv,
                                   readonly_target=ro_slot)

        if isinstance(e.obj, TpyName) and e.obj.name == lc.self_receiver:
            _witness("call.self_method")
        # An unproven Optional-ptr borrow receiver takes the runtime-check
        # render (`::tpy::deref_check(p).method(args)`); the marker carve-out
        # local admission permits it only on such a receiver. Mutually exclusive
        # with the indirect (`->`) arm -- the checked deref yields a reference.
        deref_check = e.needs_optional_runtime_check
        # A generic method's explicit template args (`b.transform<T>(42)`):
        # the AST's method_targs suffix, spelled type_to_cpp over each
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
        recv_lowered = _lower_expr(
            e.obj, lc, declared,
            # A call-shaped receiver's own arg temps flush at the enclosing
            # statement like any nested arg's, so allow_temps rides through.
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                         allow_temps=temp_args),
            field_prechecked=isinstance(e.obj, TpyFieldAccess),
            subscript_prechecked=isinstance(e.obj, TpySubscript))
        if (isinstance(e.obj, TpyName) and isinstance(recv_lowered, THIRName)
                and not recv_lowered.deref
                and (fi.cpp_template is not None
                     or (fi.native_function and fi.native_name))
                and _ptr_read_derefs(e.obj.name, lc)):
            # A cpp_template expansion and an @native free function both
            # consume the receiver as an ARGUMENT, a value position -- so an
            # indirect name derefs there (`::tpy::__len__((*xs))`), where a
            # real member call spells `->` instead. Both emit branches ignore
            # `is_arrow`, so the deref has to ride the name.
            recv_lowered = replace(recv_lowered, deref=True)
        return THIRMethodCall(
            result_type=rtype if rtype is not None else VoidType(),
            receiver=recv_lowered,
            method_cpp=member,
            args=tuple(
                _method_arg(a, params[i].type if params else None, i)
                for i, a in enumerate(e.args)),
            native_function_name=fi.native_name if fi.native_function else None,
            cpp_template=fi.cpp_template,
            method_targs_cpp=method_targs,
            is_arrow=not deref_check and isinstance(e.obj, TpyName)
                     and (_ptr_read_derefs(e.obj.name, lc)
                          # A poly-narrowed `self` reads through the cast
                          # pointer's deref (`(*__self_ptr)`), so the call is
                          # `.` -- the `this->` spelling is the un-narrowed
                          # receiver's.
                          or (e.obj.name == lc.self_receiver
                              and lc.self_is_pointer
                              and e.obj.name not in lc.narrow.spelled)),
            deref_check=deref_check,
            move_receiver=(bool(fi.is_consuming)
                           and isinstance(e.obj, TpyName)
                           and _witness("method.consuming_move")),
            form=_viewfam_result_form(m_str),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        if e.coercion.name == "into_any":
            return _lower_into_any(e, lc, declared, rtype, loc)
        if e.coercion.name == "from_any":
            return _lower_from_any(e, lc, declared, rtype, loc)
        disp = _coerce_disposition(e)
        if disp is None:
            raise ThirUnsupported("expr.coerce")
        if (e.coercion.name in _SPAN_METHOD_COERCIONS
                and isinstance(e.expr, TpyName)
                and (e.expr.name in lc.pointers
                     or e.expr.name == lc.self_receiver)):
            # The AST pre-derefs an indirect receiver ((*name).__span__(),
            # is_indirect_name); THIR record names lower bare here -> defer.
            raise ThirUnsupported("expr.coerce")
        # A materializing coerce IS the owned sink for its view source, so a
        # StrView field inner is admitted here (the AST's std::string(x) over
        # the bare member read renders through the S1 chokepoint below).
        if (e.coercion.name in _SPANLIKE_COERCIONS
                and isinstance(e.expr, TpyArrayLiteral)
                and is_span(e.expected_type)
                and getattr(e.expected_type, "type_args", None)):
            # _gen_span_coercion's literal arm: the helper wraps a
            # make_array-typed brace literal
            # (`as_mut_span(std::array<T, N>{...})`) -- thread the
            # synthesized Array target (element retype rides the Array
            # family) and stamp its spelled prefix on the brace init.
            arr_t = make_array(e.expected_type.type_args[0],
                               len(e.expr.elements))
            inner = _lower_expr(e.expr, lc, declared, target_type=arr_t)
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
            # would render `(*recv.field)` here, while the AST spells the bare
            # member read (uncompilable -- BUGS.md), so it must stay unrouted.
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
                # Whatever the str-family coerce does with it, the member read
                # itself renders bare -- the wrap (materializing copy or
                # nothing) composes around it. Typed on the DECLARED field
                # type, so a narrowed `str | None` field stays unrouted.
                field_owned_str_ok=(
                    view_str_field
                    or str_field_inner
                    or (disp == "materialize"
                        and isinstance(e.expr, TpyFieldAccess))))
        if (e.coercion.name in _INDIRECT_DEREF_COERCIONS
                and isinstance(e.expr, TpyName)
                and isinstance(inner, THIRName)
                and inner.name == e.expr.name
                and _ptr_read_derefs(e.expr.name, lc)
                and not inner.deref):
            # The AST pre-derefs an indirect-name inner for these coercions
            # (`&(*q)` -- the "need dereferencing for globals" arm); a slot
            # GLOBAL already carries the deref from the name arm, this covers
            # the pointer-LOCAL inner the name arm leaves bare.
            inner = replace(inner, deref=True)
        if disp == "materialize":
            # The cross-type view->owned copy (`std::string(x)`) IS the S1
            # view->owned form transfer -- one emit chokepoint. The coerce
            # adds only the family-internal type respelling (StrView -> str /
            # String), carried on result_type.
            return THIRFormConvert(result_type=rtype, value=inner,
                                   form=Form.STORAGE, loc=loc)
        if (e.coercion.name in (_FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION)
                and isinstance(inner, THIRLiteral)):
            # The AST forwards the coerce target into the literal render (the
            # Float32 `f` suffix / the BigInt ctor wraps). Only a literal source
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
            # A literal-tree widening coerce (`b: Int64 = (4 + 5) + 6`): the
            # AST forwards the coerce target into gen_binop, whose dedicated
            # literal arm re-resolves the operators at the TARGET width
            # (`add_check<int64_t>`); the rebuild mirrors that recursion.
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
    raise ThirUnsupported(expr_kind_tag(e))


def _lower_into_any(e: TpyCoerce, lc: '_LowerCtx',
                    declared: dict[str, TpyType],
                    rtype: 'TpyType | None', loc) -> THIRExpr:
    """`into_any` coercion: store the value in a `tpy::Any` cell via `make_any`.
    The wrap is the coercion's own render taken with a `{0}` placeholder, which
    is placeholder-transparent for scalar / str / bytes / None sources (their
    `_any_storage_form` wraps embed the render unchanged). A LIST-literal source
    renders as a bare brace-init (`{1,2,3}`) the `std::vector<T>{0}` prefix wraps
    correctly; a name / set / dict / make_vector source needs a paren copy the
    placeholder wrap cannot express, so it stays on the AST path."""
    actual = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e.actual_type)))
    if is_list(actual) or is_dict(actual) or is_set(actual):
        if not (isinstance(e.expr, TpyArrayLiteral) and is_list(actual)):
            note_detail("coerce.into_any_container")
            raise ThirUnsupported("expr.coerce")
        lit = _lower_expr(e.expr, lc, declared, target_type=actual)
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
    if isinstance(e.expr, TpyNoneLiteral):
        # `None` into Any stores `std::monostate{}` -- the unit-typed STORAGE
        # None render; `_any_storage_form` leaves it bare inside make_any.
        inner: THIRExpr = THIRLiteral(result_type=NONE, value=None,
                                      form=Form.STORAGE, loc=loc)
    else:
        inner = _lower_expr(e.expr, lc, declared)
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
    `make_any(...)` with the int/float-literal type override the AST applies so
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
        # The AST's `resolved` override at an Any element: int/float literals
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
    split for tuple NAMES; every other tuple name (loop var, alias local,
    frame field) is a storage source and needs the `tuple_to_pointer` lift.
    Mirrors `_borrow_form_tuple_param`'s param scan, which the tuple-unpack
    arm uses for the same discrimination."""
    return frozenset(
        n for n, t in lc.params
        if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))),
                      TupleType)
        and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        .has_pointer_repr_element())


def _self_captures_this(lc: '_LowerCtx') -> bool:
    """Whether a `self` captured by a LAMBDA spells `this` in the capture
    list. Both the plain method receiver (`this`) and the simple-generator
    wrapper (`(*this)`) hold the receiver POINTER, so both capture it that
    way -- only the body READ spelling differs, and that comes from
    `lc.self_cpp`. The resumable frame's `__self` member is a different
    receiver entirely and stays out."""
    return lc.self_receiver == "self" and lc.self_cpp in ("this", "(*this)")


def _lower_lambda(e: TpyLambda, lc: '_LowerCtx',
                  declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a lambda expression to `_gen_lambda`'s C++ closure. Params,
    the capture list, and the trailing return type spell EXACTLY like the AST's
    `_gen_lambda` (same helpers); the single-expression body is the AST's
    `gen_expr(body, ret_type)`. `captures_by_value` (Callable/std::function --
    escaping) picks the `_callable_param_cpp` param spelling and a by-value
    capture list; the default (Fn template) uses `to_cpp_param` and a
    by-reference capture; `readonly_params` (the key-function context)
    spells each param const via `_callable_param_cpp(readonly[T])` and the
    trailing return via `to_cpp_return_const`. Deferred to AST: the
    pointer-repr tuple return arm and the void-body statement render."""
    analyzer = lc.analyzer
    loc = getattr(e, "loc", None)
    if not _lambda_routable(e, analyzer, self_this=_self_captures_this(lc)):
        raise ThirUnsupported("expr.lambda")
    ret_type = e.inferred_return_type
    params_cpp: list[str] = []
    body_declared = dict(declared)
    for pname, ptype in zip(e.param_names, e.inferred_param_types):
        cpp_name = escape_cpp_name(pname)
        if e.readonly_params:
            # Key-function context (min/max/sorted): const params
            # regardless of the callable-default mutability -- the AST's
            # `_gen_lambda` readonly arm, same helper.
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
        # A captured `self` IS the receiver pointer, so it spells `this` in
        # both capture modes -- alias semantics either way (the AST's
        # `self_captures_this` arm). The gate admits the shape only where the
        # receiver IS that pointer (`_self_captures_this`).
        prefix = "" if e.captures_by_value else "&"
        capture = "[" + ", ".join(
            "this" if (n == lc.self_receiver and _self_captures_this(lc))
            else f"{prefix}{escape_cpp_name(n)}"
            for n in e.captured_names) + "]"
    else:
        capture = "[]"
    if is_void_like_type(ret_type):
        # The statement-body closure: the body IS a builtin print call
        # (gate-checked by _lambda_routable). Its args lower TEMP-FREE --
        # a hoisted temp would flush at the ENCLOSING statement, outside
        # the closure. Narrowed-alias args keep the fallback (the
        # subject_union print keying is a statement-scope fact).
        # Late import: statements.py imports this module at top level.
        from .statements import _lower_print_arg
        pargs = []
        for parg in e.body.args:
            if (isinstance(parg, TpyName)
                    and (parg.name in lc.narrow.narrowed
                         or parg.name in lc.narrow.spelled)):
                raise ThirUnsupported("expr.lambda")
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
    body = _lower_expr(e.body, lc, body_declared, target_type=ret_type)
    _witness("expr.lambda")
    return THIRLambda(
        result_type=analyzer.get_expr_type(e),
        capture_cpp=capture,
        params_cpp=tuple(params_cpp),
        body=body,
        ret_cpp=(ret_type.to_cpp_return_const() if e.readonly_params
                 else ret_type.to_cpp()),
        loc=loc,
    )

def _lower_dyn_synth_call(call: 'TpyMethodCall', rtype: 'TpyType | None',
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          loc, *, allow_name_arg: bool = False) -> THIRExpr:
    """The shared shape gate + lowering for a sema-synthesized dunder call
    (`obj.__getattr__(name)` behind a dyn-attr read, a hasattr probe, or a
    getattr-with-default): a bare non-pointer F1-record receiver name, a
    literal (or, for the probe forms, str-NAME) name arg, and a
    single-overload plain user method, lowered as the plain method call."""
    analyzer = lc.analyzer
    recv = call.obj
    if not (isinstance(recv, TpyName) and recv.name in declared
            and recv.name != lc.self_receiver
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
    return THIRMethodCall(
        result_type=rtype,
        receiver=_lower_expr(
            recv, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
        method_cpp=escape_cpp_name(call.method),
        args=(_lower_call_arg(name_arg, fi.params[0].type, lc, declared,
                              method_arg=True),),
        loc=loc,
    )


def _lower_dyn_getattr_call(e: TpyFieldAccess, rtype: 'TpyType | None',
                            lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The sema-synthesized `obj.__getattr__("name")` behind a dynamic-attr
    read (`obj.x`, D16) -- the value-position mirror of the dyn-setattr write
    arm, lowered as the plain method call (`_gen_field_access` delegates to
    `_gen_method_call`; the post-process chain is identity in this slice per
    `_plain_method_fi_ok`)."""
    result = _lower_dyn_synth_call(e.dyn_getattr_call, rtype, lc, declared,
                                   getattr(e, "loc", None))
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
          or (tuple_ok and _value_tuple_global(rtype, analyzer) is not None))
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
    """The class-constant WRITE lvalue -- the AST's gen_class_constant_lvalue:
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
    UNLESS `allow_ref_pointer` -- the print-sink caller, whose consumer IS
    pinned (`::tpy::as_ostream(<read>)`), opts in to the bare `(*slot)`."""
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
                         form=_viewfam_result_form(viewfam), loc=loc)

def lower_print_sink(file_val: TpyExpr, lc: '_LowerCtx',
                     declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a `print(file=...)` sink expression. A module-variable read
    (`sys.stderr` / `sys.stdout`) is a reference-type pointer slot the
    general module-var arm excludes, but the sink's consumer is pinned
    (`::tpy::as_ostream(<sink>)`), so route the bare `(*slot)` read here.
    Any other sink shape stays on the AST path."""
    analyzer = lc.analyzer
    if isinstance(file_val, TpyFieldAccess):
        rtype = analyzer.get_expr_type(file_val)
        loc = getattr(file_val, "loc", None)
        if file_val.module_var_access is not None:
            return _lower_module_var(file_val, rtype, lc,
                                     *file_val.module_var_access, loc=loc,
                                     allow_ref_pointer=True)
        bare_mod = _bare_module_recv(file_val.obj, declared, analyzer)
        if (bare_mod is not None
                and _module_var_read_cpp(bare_mod, file_val.field, analyzer)
                is not None):
            return _lower_module_var(file_val, rtype, lc, bare_mod,
                                     file_val.field, loc=loc,
                                     allow_ref_pointer=True)
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
        frame_bare_tuple: bool = False) -> THIRExpr:
    if not _container_lit_elem_ok(
            e, slot, declared, lc.analyzer, threaded=threaded, forced=forced,
            allow_record=allow_record, allow_nested=allow_nested,
            allow_optional=allow_optional):
        raise ThirUnsupported("expr.container_literal")
    return _lower_container_elem(
        e, slot, lc, declared, retype_scalars=retype_scalars,
        suppress_move=suppress_move, frame_bare_tuple=frame_bare_tuple)


def _lower_ru_literal(e: TpyExpr, ut: 'UnionType', lc: '_LowerCtx',
                      declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a list/dict literal admitted by `_ru_container_literal_ok` for a
    recursive-union wrapper slot, mirroring the AST's element targeting: a
    non-empty list spells the typed prefix (`std::vector<W>{...}` -- the
    `list[AliasRef]` arm of `_gen_array_literal`; the union_prefix arm spells
    the identical member type for nested literals), an empty list keeps the
    emit's bare-to_cpp empty spelling, a dict is self-describing
    (`::tpy::ordered_map<K, W>({{k, v}, ...})`, both spellings bare to_cpp
    like `_gen_dict_literal`), None renders the wrapper's monostate via the
    union-typed literal, and scalar literals keep their target-less render."""
    at = lc.analyzer.get_expr_type(e)
    at = resolve_pending_container(at, lc.analyzer) or at
    loc = getattr(e, "loc", None)
    # A GENERIC alias instance types the literal as the WRAPPER itself
    # (`Tree[int]`), not as `list[Tree[int]]` the way the non-generic
    # `AliasRef` form does -- but the AST spells the same container around
    # it, so synthesise that container for the spelling and the result type.
    instance = isinstance(at, RecursiveAliasInstanceType)
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
        # emit's monostate render (the AliasRef-target arm of the AST's
        # None-literal emit).
        return THIRLiteral(result_type=ut, value=None, form=Form.VALUE,
                           loc=getattr(x, "loc", None))
    return _lower_expr(x, lc, declared, use=_NESTED_ARG_USE)


def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx', declared: dict[str, TpyType], *,
                          retype_scalars: bool = True,
                          suppress_move: bool = False,
                          field_str_ok: bool = False,
                          frame_bare_tuple: bool = False) -> THIRExpr:
    """Lower one container-literal element / dict key / dict value into its
    slot. A view-form str source (BORROW -- a string_view param/local, a slice,
    a StrView-returning call) into an owned `std::string` slot copies
    explicitly via the S1 view->owned `THIRFormConvert` (`std::string(x)`) --
    the `_wrap_for_owned_slot`/`_view_source_to_owned` chokepoint at element
    positions. A literal (VALUE, const char[N]) and an owned source (STORAGE --
    an owned local, a String local, a concat/f-string rvalue) land bare, like
    the AST's brace-init pass-through; scalar slots never wrap.
    `field_str_ok` threads the str/bytes FIELD-read admission
    (`field_owned_str_ok`) for sinks whose element render is the same bare
    member / view->owned wrap (the generic-tuple return builder); the
    default keeps field elements gate-rejected."""
    su0 = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
        if slot is not None else None
    if isinstance(su0, AnyType):
        return _lower_elem_into_any(e, su0, lc, declared)
    # `retype_scalars` mirrors whether the AST threads a scalar element
    # target: dict keys/values and set elements do (target-typed Float32/
    # BigInt literal wraps); list/Array elements do NOT (bare renders).
    if isinstance(e, TpyNoneLiteral):
        # Only the Optional[scalar] element slot admits a bare None: the
        # STORAGE-form None renders `std::nullopt` (gen_expr's None-into-
        # optional-target render).
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
        return THIRLiteral(result_type=su, value=None, form=Form.STORAGE,
                           loc=getattr(e, "loc", None))
    if isinstance(e, TpyTupleLiteral):
        # A nested value-tuple element lowers against its slot TupleType;
        # tuple literals have no generic _lower_expr arm.
        vt = _value_tuple_return(slot, lc.analyzer)
        if vt is not None:
            return _lower_tuple_literal(e, vt, lc, declared)
        # A non-value tuple element stores via `tuple_to_storage<S>(S{...})`:
        # the inner spells the storage tuple S with its members lowered as
        # storage container elements, and the STORAGE FormConvert emits the
        # helper (mirrors the MIL pointer-repr-tuple field write). The
        # resumable frame-EMPLACE position spells a SEQUENCE element BARE
        # instead (typed_brace_init: the emplaced brace is already
        # storage-typed, so the AST applies no per-element wrap) -- vector
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
                and _tuple_elem_slots_ptr_optional(su)):
            # `_gen_tuple_literal`'s has_ref_elements path: the inner is the
            # BORROW form (`std::tuple<T*, T*>{&(t1), nullptr}`) and the
            # STORAGE convert emits `tuple_to_storage<S>(...)` over it. The
            # storage-direct sibling below serves the all-value-slot shape.
            _witness("containerlit.tuple_borrow_storage")
            return THIRFormConvert(
                result_type=su,
                value=_lower_borrow_tuple_literal(e, su, lc, declared),
                form=Form.STORAGE, move=False, loc=getattr(e, "loc", None))
        if isinstance(su, TupleType) and len(e.elements) == len(su.element_types):
            inner = THIRTupleLiteral(
                result_type=su,
                elements=tuple(
                    _lower_container_elem(e.elements[i], su.element_types[i],
                                          lc, declared)
                    for i in range(len(e.elements))),
                loc=getattr(e, "loc", None))
            if lc.resumable_leaf_mode and frame_bare_tuple:
                _witness("containerlit.tuple_frame_elem")
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
        pointers = {n for n in lc.pointers
                    if _optional_ptr_borrow(declared.get(n),
                                            lc.analyzer) is None}
        comp = _lower_comprehension(e, su, lc, declared, pointers)
        _witness("comp.nested")
        return comp
    if (isinstance(e, TpyMethodCall)
            and _f1_record(lc.analyzer.get_expr_type(e), lc.analyzer)):
        # A record-returning method-call rvalue element (`rc.clone()`): the
        # bare call lands in the make_vector/make_ordered_set slot, so it
        # lowers under BORROW_BIND -- the record-return admission the
        # owned-record decl bind uses (the element consumes the rvalue by
        # value; no move wrap, a call rvalue is not a move source).
        return _lower_expr(e, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    el = _lower_expr(
        e, lc, declared, container_threaded=retype_scalars,
        field_owned_str_ok=field_str_ok and isinstance(e, TpyFieldAccess))
    if retype_scalars:
        el = _slot_literal_retype(el, slot, lc)
    # A record-name element mirrors gen_expr_deref + _maybe_move: an F2
    # pointer-local name derefs (`(*p)`), and a movable owned local at its
    # last use moves into the element slot -- the same `movable_locals` +
    # `all_last_uses` facts the AST reads.
    if (isinstance(e, TpyName) and isinstance(el, THIRName)
            and _ptr_read_derefs(e.name, lc)):
        el = replace(el, deref=True)
    # A value-`Optional[str]` slot (a widened value-tuple RETURN element) wraps
    # its str-view source exactly like the bare owned-str slot: the tuple
    # brace-init relies on the implicit `std::string -> std::optional<std::string>`
    # conversion, so the element renders `std::string(view)` (not a spelled
    # optional wrap), matching the AST. Resolve the str target through the
    # Optional inner. The converts apply BEFORE the move wrap: the AST wraps
    # for the owned slot first, then `_maybe_move` moves the WRAPPED temp
    # (`std::move(std::string((*a)))`, the make_vector face).
    ou = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
          if slot is not None else None)
    str_slot = (ou.inner if isinstance(ou, OptionalType)
                and not ou.uses_pointer_repr() else slot)
    st = _resolved_str_value(str_slot, lc.analyzer) if str_slot is not None else None
    if st is not None and is_str_type(st) and el.form is Form.BORROW:
        el = THIRFormConvert(result_type=st, value=el, form=Form.STORAGE,
                             loc=getattr(e, "loc", None))
    else:
        # The bytes sibling (S6): a view-form source into an owned `bytes`
        # element slot copies via `::tpy::bytes_copy` (a bytes literal lowers
        # STORAGE and renders its owned form bare).
        bt = (_resolved_bytes_value(slot, lc.analyzer)
              if slot is not None else None)
        if bt is not None and is_bytes_type(bt) and el.form is Form.BORROW:
            el = THIRFormConvert(result_type=bt, value=el, form=Form.STORAGE,
                                 loc=getattr(e, "loc", None))
    # A list-repeat element is copied into EVERY slot (one source, N slots), so
    # it must never move (the AST's `_gen_list_repeat` omits `_maybe_move`);
    # moving would use-after-move the source for slots 1..N-1.
    if not suppress_move and _container_elem_move_source(e, lc):
        _witness("containerlit.move")
        el = THIRMove(result_type=el.result_type, value=el, form=el.form,
                      loc=getattr(e, "loc", None))
    return el

def _container_elem_move_source(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """`_maybe_move` for a container-literal element/key/value: the sema
    movable set filtered to NON-VALUE sources. Codegen registers a
    sema-movable local into `ctx.movable_locals` only at the non-value decl
    arms (the tier-1 `not is_value_type()` filter in _gen_var_decl), so a
    sema-movable VALUE local (e.g. a view-resolved promoted `str`) never
    moves on the AST path -- `lc.movable_locals` (the unfiltered sema set)
    must not move it here either. The value-Optional param exception:
    seed_param_locals adds its (value-typed but expensive-copy) name to
    codegen's movable set, so a narrowed `(*p)` element does move despite the
    value-type filter."""
    if not _is_move_source(e, lc):
        return False
    inner = _peel_coerce(e)
    # The value-Optional binding exceptions to the value-type filter
    # (scalar AND view): the _LowerCtx mirror seeds expensive-copy
    # value-Optional params movable exactly like seed_param_locals, so
    # `_is_move_source` above is the movability authority; this row only
    # confirms the binding SHAPE so the narrowed `(*a)` element moves --
    # including through the view->owned coerce wrap
    # (`std::string((*a))`, the make_vector face).
    if isinstance(inner, TpyName) and (
            _value_opt_scalar_binding(inner.name, lc)
            or _value_opt_view_param(inner.name, lc)):
        return True
    t = lc.analyzer.get_expr_type(e)
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return not t.is_value_type()

def _witness_container_elem_fam(slot: 'TpyType | None', analyzer) -> None:
    """Fold the container-literal element-family witness for a non-empty
    literal's admitted slot (the widened families only; the scalar/str slice
    predates the face set)."""
    fam = _container_lit_slot_family(slot, analyzer) if slot is not None else None
    if fam in ("enum", "optional", "tuple", "container", "record", "bytes"):
        _witness(f"containerlit.{fam}_elem")

def _lower_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                         lc: '_LowerCtx',
                         declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a value-tuple literal against its slot or reject its shape."""
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
            e.elements[i], elem_slot, lc, declared)

    return THIRTupleLiteral(
        result_type=slot,
        elements=tuple(lower_element(i) for i in range(len(e.elements))),
        loc=getattr(e, "loc", None))

def _lower_borrow_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                                lc: '_LowerCtx',
                                declared: dict[str, TpyType], *,
                                target_readonly: bool = False,
                                rvalue_ok: bool = False,
                                elem_temps: bool = False) -> THIRExpr:
    """Lower a tuple literal at a BORROW-form slot (`std::tuple<..., T*>`) --
    the ref-element path of `_gen_tuple_literal` reduced to the lvalue-NAME
    subset. Per element the slot-info ladder's sliced arms: VALUE mode lowers
    through the value container-elem rows (bare, no lift); REF/CONST_REF mode
    admits a plain pointer-repr non-value lvalue NAME -- `&(name)`, or bare
    for an already-pointer name -- and spells `T*` / `const T*`. Everything
    else (rvalue borrow elements and their tuple_value_to_borrow helper
    machinery, pointer-repr Optional / union / TypeParamRef element slots,
    non-name lvalues) rejects with a named detail."""
    analyzer = lc.analyzer
    n = len(e.elements)
    if n != len(slot.element_types):
        note_detail("btuple.arity")
        raise ThirUnsupported("expr.tuple_literal")
    parts: list[str] = []
    src_parts: list[str] = []
    lowered: list[THIRExpr] = []
    lifts: list[bool] = []
    any_rvalue = False
    for i in range(n):
        et = unwrap_ref_type(slot.element_types[i])
        et_bare = unwrap_readonly(unwrap_send_sync(et))
        mode = (e.elem_capture[i] if i < len(e.elem_capture) else None)
        if mode is None:
            # No sema annotation (yield/arg contexts, target always provided
            # here): a value element picks VALUE, every other element the
            # target-provided REF arm (CONST_REF under a readonly target) --
            # the slot-info ladder's non-storage tail.
            if et_bare.is_value_type():
                mode = TupleElemCapture.VALUE
            else:
                mode = (TupleElemCapture.CONST_REF if target_readonly
                        else TupleElemCapture.REF)
        elif target_readonly and mode == TupleElemCapture.REF:
            mode = TupleElemCapture.CONST_REF
        if mode == TupleElemCapture.VALUE:
            lowered.append(_lower_container_elem(
                e.elements[i], slot.element_types[i], lc, declared))
            lifts.append(False)
            parts.append(lc.render_type(et_bare))
            src_parts.append(lc.render_type(et_bare))
            continue
        # Borrow slot: the earlier slot-info arms (pointer-repr Optional,
        # pointer-variant union, TypeParamRef) each spell their own form --
        # sliced out; only the plain pointer-repr non-value arm is mirrored.
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
            # `&(name)` (`(p, 42)` at a `tuple[Point | None, Int32]` ctor
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
                      and elem.name not in lc.pointers
                      and not (elem.name == lc.self_receiver
                               and lc.self_is_pointer)
                      and not isinstance(
                          unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                              declared[elem.name]))), OptionalType)):
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
            # `elem_temps` rides the arg position's flush right into a
            # call-shaped rvalue element (its nested arg temps hoist at the
            # enclosing statement, like any nested call arg's).
            lowered.append(_lower_expr(
                elem, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             allow_temps=elem_temps)))
            lifts.append(False)
            any_rvalue = True
            continue
        if isinstance(elem, TpyName):
            if elem.name not in declared or elem.name in lc.narrow.narrowed:
                note_detail("btuple.elem_source")
                raise ThirUnsupported("expr.tuple_literal")
            # An already-pointer name renders bare into the `T*` slot: an
            # Optional-ptr param / pointer local, or `self` in a sync method
            # (`this` is a prvalue pointer -- `&(this)` is ill-formed; a
            # resumable method's `__self` is a `Record&` field and DOES
            # lift). A plain lvalue takes `&(...)`.
            lift = (elem.name not in lc.pointers
                    and not (elem.name == lc.self_receiver
                             and lc.self_is_pointer))
        elif isinstance(elem, TpySubscript):
            # A container-element lvalue subscript lifts `&(<row render>)`
            # (`&(::tpy::__getitem__(items, i))`). A subscript whose OBJECT
            # is itself a borrow-form tuple already yields `T*`
            # (`std::get<i>(t)`) -- the pass-through face, not sliced.
            obj = elem.obj
            obj_t = (declared.get(obj.name) if isinstance(obj, TpyName)
                     else None)
            obj_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                obj_t))) if isinstance(obj_t, TpyType) else None)
            if (isinstance(obj_bare, TupleType)
                    and obj_bare.has_pointer_repr_element()):
                note_detail("btuple.elem_source")
                raise ThirUnsupported("expr.tuple_literal")
            lift = True
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
        lowered.append(_lower_expr(
            elem, lc, declared,
            use=_ExprUse(result=_ExprResultUse.RECEIVER)))
        lifts.append(lift)
    spelled = f"std::tuple<{', '.join(parts)}>"
    if any_rvalue:
        # The value-form source tuple's slots: value cpp for rvalue elements,
        # the borrow (pointer) part for lvalue ones -- their lifts happen
        # inside the source, and the helper passes them through.
        _witness("btuple.value_to_borrow")
        return THIRTupleValueToBorrow(
            result_type=slot, dst_cpp=spelled,
            src_cpp=f"std::tuple<{', '.join(src_parts)}>",
            elements=tuple(lowered), addr_of=tuple(lifts),
            loc=getattr(e, "loc", None))
    _witness("btuple.literal")
    return THIRBorrowTupleLiteral(
        result_type=slot, spelled_cpp=spelled,
        elements=tuple(lowered), addr_of=tuple(lifts),
        loc=getattr(e, "loc", None))


def _lower_generic_tuple_literal(e: TpyTupleLiteral, slot: 'TupleType',
                                 lc: '_LowerCtx',
                                 declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a tuple literal at a slot with TypeParamRef elements -- the
    `want_val_or_ptr_form` arm of `_gen_tuple_literal` reduced to its
    witnessed subset. A generic slot spells `::tpy::val_or_ptr_t<T>` and
    wraps its element `::tpy::to_val_or_ptr<slot>(...)` (address-of into a
    pointer slot, construct/copy into a value slot -- decided at
    instantiation); concrete value elements render bare into their base
    slots via the container-elem rows. VALUE captures only (sema stamps
    VALUE on return-tuple elements; a CONST_REF stamp would flip the slot
    to `val_or_cptr_t` -- unwitnessed). A generic element admits a plain
    declared NAME that is neither narrowed, pointer-form, nor a move
    source: on the AST path a movable source takes `_maybe_move` (VALUE
    capture + non-value slot_inner) and a narrowed or pointer-form one
    derefs -- renders this slice does not reproduce."""
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
            # Pointer-form locals excluded like narrowed/movable names: the
            # AST derefs them (gen_expr_deref) before the to_val_or_ptr
            # wrap, a render this slice does not reproduce.
            if not (isinstance(elem, TpyName) and elem.name in declared
                    and elem.name not in lc.narrow.narrowed
                    and elem.name not in lc.pointers
                    and not _is_move_source(elem, lc)):
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
                                                 field_str_ok=True))
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


def _compose_static_targs(cpp_class: str, record_info, cpp_method: str,
                          e, lc: '_LowerCtx') -> 'tuple[str, tuple[str, ...] | None]':
    """The class/method targs split shared by the two generic-static
    composers -- `_gen_method_call`'s static tails: class-level targs spell
    on the class (`Cls<CA>`), method-level targs ride template_args_cpp, and
    the dependent `template ` keyword fires ONLY when method targs follow
    (the AST nests it under `if method_args:` -- a `template` keyword with
    no following `<...>` would be a C++ syntax error). repr-subst-marked
    calls were rejected during lowering, so `_render_method_type_arg`'s adapter
    override never applies."""
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
    targs = (tuple(lc.render_type(unwrap_ref_type(t)) for t in method_args)
             or None)
    return (f"{cpp_class}::{template_kw}{cpp_method}", targs)


def _generic_static_callee(e, lc: '_LowerCtx') -> 'tuple[str, tuple[str, ...] | None]':
    """Compose a same-module generic STATIC call's callee spelling --
    `_gen_method_call`'s static tail: the class name (implicit-stdlib peers
    qualify) over the shared class/method targs split."""
    analyzer = lc.analyzer
    record_info = analyzer.registry.get_record(e.obj.name)
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
    `_gen_method_call`'s module-static arm: the class qualifies through the
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
                                    declared: dict[str, TpyType], loc):
    """The complex-intermediate arm of _gen_chained_compare (the GCC stmt-expr
    with single-eval `auto&& _cmpI` temps). Each pair lowers through the shared
    comparison path (`_lower_expr` -> THIRBinOp), so operand targets and the
    per-side casts are byte-identical to a standalone comparison; the temp-vs-
    inline binding decision mirrors the AST (`_gen_chained_compare_lambda`):
    intermediates always bind, the first endpoint binds iff non-simple, the last
    is always inlined. Operand i's init is read off its owning pair (pair 0's
    left for i=0, pair i-1's right otherwise) so its target matches the AST's
    `operand_code`."""
    _witness("chained_compare.stmt_expr")
    pairs = [_lower_expr(p, lc, declared) for p in e.pairs]
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
            bound.append(not ExpressionGenerator._is_duplicable_expr(all_operands[0]))
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
    `pick<int32_t>(__tmp_1, __tmp_2)`): explicit template args rendered the
    way the AST spells them (type_to_cpp_stored per inferred arg), args
    against the ROOT stub's substituted param slots. A temporary arg into a
    TypeParamRef slot (`param_val_or_ref_t<T>` in C++ -- an lvalue-ref
    binding) hoists the AST's named temp: `<resolved.to_cpp()> __tmp_N =
    <target-typed init>;` (TempState.create) -- scalar/str/None literals
    and by-value call rvalues, each with its own init render; lvalue
    NAMEs and the remaining shapes ride `_lower_call_arg` against the
    substituted slot."""
    analyzer = lc.analyzer
    root, subst = _generic_root_subst(e, analyzer)
    dcbp = root.deep_const_borrow_params
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
        if not _generic_plain_arg_ok(
                a, ptype, subst, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed)):
            raise ThirUnsupported("expr.call")
        peeled = _peel_coerce(a)

        def _ref_slot_temp(init: THIRExpr) -> None:
            # The shared ref-slot named temp (`R __tmp_N = <init>;`) a
            # temporary hoists into a `param_val_or_ref_t<T>` binding.
            if not temp_args:
                raise ThirUnsupported(
                    "generic ref-slot literal temp outside a flush position")
            _witness("argtemp.generic_ref_slot")
            args.append(THIRArgTemp(
                result_type=resolved, cpp_type=resolved.to_cpp(),
                init=init, form=Form.VALUE, loc=getattr(a, "loc", None)))

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
            _ref_slot_temp(_lower_expr(a, lc, declared, target_type=resolved,
                                       use=_ExprUse(
                                           result=_ExprResultUse.STORAGE)))
        else:
            args.append(_lower_call_arg(
                a, resolved, lc, declared, temp_args=temp_args,
                protocol_slots=True,
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
    return _ctor_shape_ok(a, analyzer)


def _inst_slice_arg_ok(arg: TpyExpr, analyzer) -> bool:
    """A list/Span-yielding slice subscript admitted as a container
    instantiation arg (`list(argv[i:])`)."""
    if not (isinstance(arg, TpySubscript)
            and arg.slice_function_info is not None
            and isinstance(arg.index, TpySlice)):
        return False
    sb = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(arg))))
    return is_list(sb) or is_span(sb)


def _container_call_temp_arg(a: TpyExpr, ptype: 'TpyType | None',
                             analyzer, *,
                             frame_capturing: bool = False) -> 'TpyType | None':
    """A container-returning rvalue CALL into a plain free call's concrete
    container ref param (`f(list(argv[i:]))`): the AST hoists the `__tmp_N`
    ref-param temp. Returns the unwrapped slot type when the row applies --
    the ONE fact shared by gate admission and the ArgTemp lowering arm.
    Exact slot/result match only; borrow (`T&`) returns are excluded
    (they bind the ref param bare on the AST path, no temp)."""
    if not isinstance(a, (TpyCall, TpyMethodCall)) or ptype is None:
        return None
    pr = unwrap_ref_type(unwrap_send_sync(ptype))
    # The AST hoists for MUTABLE-ref params (`is_ref_param()`) always, and
    # for readonly reference slots only when the callee is a frame-capturing
    # factory (`frame_capturing`): there the statement-scoped inline
    # `const T&` bind would dangle. A sync callee keeps the inline bind
    # (CPython drop timing), so the row must decline it here too.
    if not (isinstance(pr, TpyType)
            and (pr.is_ref_param()
                 or (frame_capturing and is_readonly_ref_param(pr)))):
        return None
    slot = unwrap_readonly(pr)
    if not (is_list(slot) or is_dict(slot) or is_set(slot)):
        return None
    at = analyzer.get_expr_type(a)
    atb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
           if at is not None else None)
    if atb != slot or not is_rvalue_source(analyzer, a):
        return None
    return slot


def _lower_literal_arg(a: TpyExpr, target: 'TpyType | None', lc: '_LowerCtx',
                       declared: dict[str, TpyType], reject: str, *,
                       array_retype: bool = True) -> THIRExpr:
    """Lower a container-literal call arg against its resolved target slot.
    The make_vector / make_ordered_* element path (move-source / nocopy
    elements) rejects: its in-place arg render is unverified on the AST
    side. Each caller names its position in `reject`."""
    lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                          target_type=target, array_retype=array_retype)
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


def _lower_free_call_arg(e: TpyCall, a: TpyExpr,
                         ptype: 'TpyType | None', kind: 'tuple[str, str] | None',
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         temp_args: bool,
                         readonly_target: bool) -> THIRExpr:
    analyzer = lc.analyzer
    # Frame-capturing callee (generator/coro factory): its frame borrows ref
    # args past the statement, so readonly-slot rvalues hoist like mutable
    # ones (the AST arms key the same fact off func_info).
    _callee_fi = e.resolved_function_info
    frame_capturing = (_callee_fi is not None
                       and (_callee_fi.is_generator or _callee_fi.is_async))
    if (isinstance(a, TpyVarargPack)
            and (kind is None or kind[0] not in ("native", "native_c", "template"))):
        return _lower_vararg_pack(a, ptype, lc, declared, temp_args=temp_args)
    len_call = _is_len_call(e, declared, analyzer)
    if not len_call:
        if kind is not None and kind[0] in ("native", "native_c", "template"):
            ok = _native_call_arg_ok(a, ptype, declared, analyzer)
            if not ok and _own_move_source_slice(a, ptype, lc, declared):
                # The temp-free half of the Own-slot cascade also serves a
                # native/template slot (`unsafe_store(p, 0, pt)` ->
                # `p[0] = std::move(pt)`): `std::move(name)` is position-
                # independent, and the Own-slot lowering arm below is
                # kind-blind. The COPY half stays plain-only (it hoists a
                # temp the native loop has no flush point for).
                ok = True  # witnessed at the arm (move.own_last_use)
        else:
            ok = _plain_call_arg_ok(
                a, ptype, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed),
                self_this=_self_captures_this(lc))
            if not ok and _borrow_tuple_name_arg(
                    a, ptype, declared, _borrow_tuple_param_names(lc),
                    analyzer):
                ok = True  # witnessed inside the predicate (arg.btuple_name)
            if not ok and _value_opt_member_arg(a, ptype, declared, analyzer):
                ok = _witness("call.optval_member")
            if not ok and _optional_ptr_container_arg(
                    a, ptype, declared, analyzer):
                ok = True  # witnessed at the lowering rows (optptr.none/name)
            if not ok and temp_args and _union_ctor_temp_arg(
                    a, ptype, analyzer):
                ok = True  # witnessed at the lowering arm (unionlift.ctor_temp)
            if not ok and temp_args and _container_call_temp_arg(
                    a, ptype, analyzer,
                    frame_capturing=frame_capturing) is not None:
                ok = True  # witnessed at the ArgTemp arm (argtemp.container_call)
        if not ok:
            raise ThirUnsupported("expr.call")
    pin_slot = _strlit_overload_pin_arg(e, _callee_fi, a, ptype, analyzer)
    if pin_slot is not None:
        # A bare str literal is `const char[N]`, whose array-to-pointer /
        # boolean conversions outrank the user-defined string_view one, so an
        # overloaded callee would bind a competing overload: pin the literal
        # to its slot's view form (gen_call_arg's `overloaded_call` branch).
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
        # print wrap-call path's twin. `allow_temps` rides through: the AST
        # flushes every nested arg temp at the enclosing statement, so a
        # flushable position's right extends into the inner call's args.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=temp_args))
    plain_kind = kind is None or kind[0] not in ("native", "native_c",
                                                 "template")
    if not plain_kind:
        comp_c = _native_iterable_comp_arg(a, ptype, lc.analyzer)
        if comp_c is not None:
            # The native/template twin of the ArgTemp row below: the template
            # slot takes the stmt-expr INLINE, target-typed by the
            # comprehension's own container rather than the protocol slot.
            from .comprehensions import _lower_comprehension
            pointers = {n for n in lc.pointers
                        if _optional_ptr_borrow(declared.get(n),
                                                lc.analyzer) is None}
            _witness("arg.native_comprehension")
            return _lower_comprehension(a, comp_c, lc, declared, pointers)
    if plain_kind and _container_comp_arg(a, ptype) and temp_args:
        # The slot-typed comprehension ArgTemp
        # (`std::vector<int64_t> __tmp_N = ({ ... });`) -- the init is the
        # decl-init arm's stmt-expr, target-typed by the slot.
        from .comprehensions import _lower_comprehension
        slot_c = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        pointers = {n for n in lc.pointers
                    if _optional_ptr_borrow(declared.get(n),
                                            lc.analyzer) is None}
        comp = _lower_comprehension(a, slot_c, lc, declared, pointers)
        _witness("argtemp.comprehension")
        return THIRArgTemp(result_type=slot_c, cpp_type=slot_c.to_cpp(),
                           init=comp, form=Form.BORROW,
                           loc=getattr(a, "loc", None))
    bt_slot = (_borrow_tuple_field_arg(a, ptype, analyzer)
               if plain_kind else None)
    if bt_slot is not None:
        # The storage->borrow tuple wrap over the bare member read
        # (`bump(::tpy::tuple_to_pointer<std::tuple<int32_t, Box*>>(
        # h.pair))`); const-ness mirrors the AST's want_const pair -- the
        # callee param's deep-const verdict (threaded readonly_target /
        # readonly spelling) OR a const-ROOTED source field
        # (is_const_storage_source: `h.pair` off a const receiver).
        _witness("arg.borrow_tuple_field")
        field = _lower_expr(a, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
        return THIRFormConvert(
            result_type=bt_slot, value=field, form=Form.BORROW,
            is_const=(readonly_target or isinstance(ptype, ReadonlyType)
                      or _f1_const_rooted_source(a, lc.func, analyzer,
                                                 lc.const_locals,
                                                 lc.record_name)),
            loc=getattr(a, "loc", None))
    if (plain_kind
            and (_record_borrow_call_arg(a, ptype, analyzer)
                 or _record_elem_subscript_arg(a, ptype, analyzer))):
        # The T&-returning call / checked record-element read binds the
        # record ref slot inline (`bump(find_first(pts))` /
        # `add_a(::tpy::__getitem__(a.bs, 0), ..)`); BORROW_BIND admits
        # the borrow-record result like the compare-operand twin.
        _witness("arg.record_borrow_call")
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
        if isinstance(tb, OptionalType) or is_list(tb) or is_set(tb):
            return _lower_expr(a, lc, declared, allow_whole_optional=True)
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and _native_iterable_range_arg(a, ptype)):
        # The instantiation ladder's range row on the native ladder
        # (`zip(range(3), names)` -> `::tpy::Range<int32_t>(3)` bound bare);
        # same shape checks as that arm, body-fallback outside them.
        range_fi = a.resolved_function_info
        if (len(a.args) not in (1, 2, 3) or range_fi is None
                or not range_fi.cpp_template
                or not _eligible_scalar(_range_counter_type(a, analyzer))):
            note_detail("call.native_range_shape")
            raise ThirUnsupported("expr.call")
        _witness("call.native_range_arg")
        return _lower_range_object(a, lc, declared)
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and _native_iterable_iterator_call_arg(a, ptype, analyzer)):
        # A nested combinator / generator-factory rvalue binds bare in
        # iterable position (the instantiation ladder's gen/iterator rows).
        _witness("call.native_iter_call_arg")
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.ITERABLE))
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
    if _readonly_container_rvalue_arg(a, ptype, lc.analyzer) is not None:
        # A DECLARED-readonly slot binds an empty container rvalue INLINE
        # (never the ref-param temp the plain-slot arms below hoist);
        # handled in _lower_call_arg's inline arm.
        return _lower_call_arg(
            a, ptype, lc, declared, temp_args=temp_args,
            protocol_slots=False, readonly_target=readonly_target,
            frame_capturing=frame_capturing)
    if (isinstance(a, TpyArrayLiteral) and temp_args
            and (kind is None or kind[0] not in ("native", "native_c", "template"))
            and _container_literal_arg(a, ptype, analyzer)):
        # A list literal into a plain free call's concrete container ref
        # param: the AST hoists a `__tmp_N` ref-param temp
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
            return THIRArgTemp(result_type=slot, cpp_type=slot.to_cpp(),
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
        return THIRArgTemp(result_type=dslot, cpp_type=dslot.to_cpp(),
                           init=coerced, form=Form.BORROW,
                           loc=getattr(a, "loc", None))
    return _lower_call_arg(
        a, ptype, lc, declared, temp_args=temp_args,
        protocol_slots=kind is not None and kind[0] not in ("native", "native_c", "template"),
        readonly_target=readonly_target, frame_capturing=frame_capturing)


def _lower_vararg_pack(pack: TpyVarargPack, ptype: 'TpyType | None',
                       lc: '_LowerCtx', declared: dict[str, TpyType], *,
                       temp_args: bool) -> THIRExpr:
    """Lower a `*args` pack (`_gen_vararg_pack`). The element spelling combines
    the pack's base type with the slot's const-ness (a readonly vararg slot
    adds const, a mutable one strips it -- the AST's slot_type override); the
    ref/value split follows the element's value category. A sole `*expr`
    unpack forwards the container (direct span or borrowed span); the per-arg
    form hoists the std::array temp (flushable position only). Str/bytes value
    elements (the AST's owned-copy wrap) and mixed *-unpack are rejected."""
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
        # position (the AST's arg-temp positions) admits it.
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
            if atb is not None and (
                    is_str_type(atb) or is_str_view_type(atb)
                    or is_string_type(atb) or is_bytes_type(atb)
                    or is_bytes_view_type(atb)):
                # A str/bytes value element lands in the owned std::array. A
                # VIEW source (name/derived view) needs the AST's owned-copy
                # wrap (`std::string()` / `bytes_copy`) and stays AST; a str/
                # bytes LITERAL is const char[N] / already-owned and lands
                # bare (`_view_owned_copy_family` returns None for it), so
                # route it. This literal-vs-view split must stay in lockstep
                # with codegen's `_is_str_view_source` / `_view_owned_copy_family`
                # -- if their literal exclusion changes, this gate must follow.
                src = a
                while isinstance(src, TpyCoerce):
                    src = src.expr
                if not isinstance(src, (TpyStrLiteral, TpyBytesLiteral)):
                    raise ThirUnsupported("call.vararg_view_elem")
            ref_lvalue.append(True)
        lowered.append(_lower_expr(a, lc, declared))
    _witness("vararg.pack_ref" if is_ref else "vararg.pack_value")
    return THIRVarargPack(result_type=ptype, elem_cpp=elem_cpp, is_ref=is_ref,
                          args=tuple(lowered), ref_lvalue=tuple(ref_lvalue),
                          form=Form.VALUE, loc=getattr(pack, "loc", None))


def _lower_range_object(call, lc: '_LowerCtx',
                        declared: dict[str, TpyType]) -> THIRCall:
    """A `range(...)` call in OBJECT position (a comprehension's begin/end
    iterable, an instantiation arg): the resolved range overload's
    cpp_template (`::tpy::Range<{T}>({0}, {1}, {2})`) with its type param
    substituted the way gen_call_from_fi does (`expand_fi_template`); bounds
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
    -> `::tpy::own_iter(std::move(b))` -- _gen_consuming_iter). The wrap
    fires only for an `Own[...]`-element slot; a borrowing slot
    (`join(items: Iterable[str])`) binds the name bare even at its last
    use. Returns None when the slice does not apply; a consuming
    __iter__ WITHOUT a native symbol raises (the `std::move(x).__iter__()`
    spelling is not mirrored)."""
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
        raise ThirUnsupported("expr.call")
    _witness("call.own_iter_arg")
    return THIRCall(
        result_type=lc.analyzer.get_expr_type(a) or VoidType(),
        callee="__iter__",
        cpp_template=(
            f"{qualify_native_name(citer.native_name)}"
            "(std::move({0}))"),
        args=(_lower_expr(a, lc, declared),),
        loc=getattr(a, "loc", None))


def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                    declared: dict[str, TpyType], *, temp_args: bool = False,
                    nested_temps: bool = False,
                    readonly_target: bool = False,
                    method_arg: bool = False,
                    method_arg_stub: bool = False,
                    protocol_slots: bool = False,
                    frame_capturing: bool = False) -> THIRExpr:
    """Lower one call argument against its param slot. A str literal into a
    Char slot renders as a target-typed char literal (gen_expr's char arm,
    via `_lower_char_targeted`); a bytes literal into a bytes/BytesView slot
    takes gen_call_arg's static-storage span pin (`::tpy::bytes_literal(...)`,
    keyed on the RAW ptype exactly like the AST); a None literal / member-typed
    record name into a pointer-variant union slot takes `_gen_union_arg`'s
    inline lift (`_lower_union_arg_lift` -- checked FIRST so a pointer-local
    member name lifts `&((*p))` rather than retagging; `readonly_target`
    threads the callee's `deep_const_borrow_params` verdict for the
    const-pointee spelling); an F2 pointer-local
    record name passed by reference derefs (`take_rec((*p))`, gen_expr_deref's
    indirect-name render -- only records become pointer-locals, so the
    membership test alone keys the retag); every other arg lowers
    position-blind."""
    if isinstance(a, TpyGeneratorExpression):
        # A genexpr into a native builtin's Iterable slot -> the make_generator
        # IIFE (`all(x > 0 for x in xs)`). Late import: comprehensions imports
        # this module.
        from .comprehensions import _lower_genexpr
        return _lower_genexpr(a, lc, declared)
    if isinstance(a, TpyTupleLiteral):
        pslot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
                 if isinstance(ptype, TpyType) else None)
        if isinstance(pslot, TupleType):
            # A tuple literal at a tuple param slot: value tuples take the
            # spelled value render; pointer-repr slots take the borrow
            # builder -- lvalue elements lift `&(...)`, rvalue elements ride
            # the tuple_value_to_borrow source-tuple path (the arg is a full
            # expression, so the source's lifetime covers the call). Element
            # shapes outside the builder's slice raise and fall back whole.
            # FREE-call args only: the method/native arg gates have no
            # tuple-literal arm yet, so `obj.set((x, y))` stays AST.
            if pslot.has_pointer_repr_element():
                return _lower_borrow_tuple_literal(
                    a, pslot, lc, declared,
                    target_readonly=isinstance(
                        unwrap_ref_type(unwrap_send_sync(ptype)),
                        ReadonlyType),
                    rvalue_ok=True,
                    elem_temps=temp_args or nested_temps)
            vt = _value_tuple_nested(pslot, lc.analyzer)
            if vt is not None:
                _witness("btuple.value_arg")
                return _lower_tuple_literal(a, vt, lc, declared)
            raise ThirUnsupported("expr.tuple_literal")
    if (isinstance(a, TpyName) and _value_opt_scalar_binding(a.name, lc)
            and _value_opt_scalar(ptype, lc.analyzer) is not None):
        # A value-repr Optional[scalar] name into a value-repr Optional slot
        # passes the WHOLE optional bare (`take_opt(p)`), even when sema
        # narrowed the read -- the AST's gen_call_arg derefs only for a
        # NON-optional slot. Strip the name arm's deref-on-narrow.
        return replace(
            _lower_expr(a, lc, declared, allow_whole_optional=True),
            deref=False)
    if (isinstance(a, TpyName) and a.name in declared
            and _param_declared_type(a.name, lc) is None
            and _opt_view_arg_shim(declared[a.name], ptype, lc.analyzer)):
        # An OWNED value-repr Optional[str/bytes] LOCAL into a matching owned
        # Optional slot: the local already holds `optional<string>`, so the AST
        # passes the WHOLE optional bare -- no view->owned shim (that fires only
        # for a PARAM whose binding is the borrow `optional<string_view>`, the
        # arm below). Strip any deref-on-narrow like the scalar sibling.
        _witness("call.optview_local_whole")
        return replace(
            _lower_expr(a, lc, declared, allow_whole_optional=True),
            deref=False)
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
        # A RECORD-method arg is excluded because the AST's `_args()` loop
        # passes `target_type=None` there, so the shim sees no target and the
        # optional goes BARE. Stub receivers are subsumed by `method_arg`, and
        # DO thread `ptype` -- they are safe only because their arg gates
        # (`_container_method_arg_ok` / `_view_method_arg_ok`) admit no such
        # row, so widening either gate must revisit this exclusion.
        return THIROptViewArg(
            result_type=ptype, name=a.name, form=Form.VALUE,
            loc=getattr(a, "loc", None))
    none_opt = _none_value_opt_arg(a, ptype, lc.analyzer)
    if none_opt is not None:
        # A `None` literal into a value-repr Optional slot -> `std::nullopt`
        # (STORAGE-form None), whatever the inner -- gen_call_arg's
        # gen_expr_deref of a bare None at a value-optional target. A bare None
        # has no `_lower_expr` arm, so this must intercept before the tail.
        _witness("call.none_value_opt")
        return THIRLiteral(result_type=none_opt, value=None, form=Form.STORAGE,
                           loc=getattr(a, "loc", None))
    unit_none = _none_unit_arg(a, ptype)
    if unit_none is not None:
        # A `None` literal into a unit slot (`Rc.new(None)` -> the
        # substituted `Own[None]` param): the bare `std::monostate{}` value,
        # like the base-init arg's target-less render but monostate-typed.
        return THIRLiteral(result_type=unit_none, value=None,
                           form=Form.STORAGE, loc=getattr(a, "loc", None))
    if isinstance(a, TpyStrLiteral) and _eligible_char(ptype):
        return _lower_char_targeted(a, ptype, lc, declared)
    if (isinstance(a, TpyFieldAccess)
            and _record_field_ref_arg(a, ptype, declared, lc.analyzer)):
        # An F1-record FIELD read binding a record ref slot: the bare
        # member read (`pass_both(h.a, h.b)`), aliasing semantics preserved.
        # The predicate re-ran the marker/receiver checks, so the gates are
        # prechecked.
        _witness("call.record_field_arg")
        return _lower_expr(a, lc, declared, field_prechecked=True)
    if (isinstance(a, TpyName)
            and not method_arg
            and _protocol_union_ctor_arg(a, ptype, declared, lc.analyzer)
            == "addr"):
        # A Span name into an all-protocols union ctor slot: the address-of
        # lift (`&(s)` -- the Spannable overload binds the pointer). CTOR
        # positions only: a user METHOD over the same union slot is a C++
        # template whose concept picks the branch, so its arg renders bare
        # (`a.extend(b)`) -- the address-of would bind the wrong overload.
        return THIROptionalPtrArg(
            result_type=ptype, value=_lower_expr(a, lc, declared),
            addr_of=True, form=Form.BORROW, loc=getattr(a, "loc", None))
    if _required_protocol_union_slot(ptype):
        # Everywhere ELSE that same slot renders the plain value:
        # `_gen_protocol_arg` claims a required multi-protocol union ahead of
        # the Optional-ptr and union-lift arms and hands it to
        # `gen_expr_deref` (the slot monomorphizes to one template param, so
        # nothing lifts). The ctor arm above now early-returns for a REQUIRED
        # union, so only the nullable form reaches its address-of lift.
        _witness("arg.protocol_union_plain")
        return _lower_expr(a, lc, declared)
    if (isinstance(a, TpyArrayLiteral)
            and _container_literal_arg(a, ptype, lc.analyzer)):
        # A list literal into a ctor's list slot: the bare brace-init in
        # place, target-threaded like the decl position. The make_vector
        # element path (move-source / nocopy elements) is NOT mirrored at
        # the arg position -- reject rather than risk a divergent render.
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
        # {300}`): a bare `{..}` cannot bind the template slot, and the AST
        # spells the resolved type here. Set/dict renders spell themselves.
        if (isinstance(lowered, THIRContainerLiteral)
                and lowered.typed_brace_cpp is None
                and (is_list(at) or is_array(at))):
            lowered = replace(lowered, typed_brace_cpp=lc.render_type(at))
        return lowered
    if (isinstance(a, (TpyDictLiteral, TpySetLiteral)) and method_arg_stub
            and _container_literal_method_arg(a, ptype, lc.analyzer)):
        # A dict / set literal into a builtin-container stub method slot
        # (`d.update({...})`): the spelled container render in place, like the
        # decl position. The slot's value carries an `Own[V]` move-in marker
        # the AST strips for the render, so target the container with each
        # type-arg's Own peeled off. The make_ordered_* (move / nocopy element)
        # path stays AST, mirroring the list arm.
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            lc.analyzer.get_expr_type(a))))
        target = at
        if isinstance(at, NominalType) and at.type_args:
            target = replace(at, type_args=tuple(
                arg.wrapped if isinstance(arg, OwnType) else arg
                for arg in at.type_args))
        return _lower_literal_arg(
            a, target, lc, declared,
            "container-literal method arg on the make path")
    if isinstance(ptype, TpyType) and (is_bytes_type(ptype)
                                       or is_bytes_view_type(ptype)):
        # Peel coerce wrappers exactly like gen_call_arg's span pin (the pin
        # renders the bare literal; the coercion's own codegen never runs).
        lit = _peel_coerce(a)
        if isinstance(lit, TpyBytesLiteral):
            lowered = _lower_expr(lit, lc, declared)
            return replace(lowered, form=Form.BORROW)
    # An async-def factory call into an `Own[@dynamic P]` slot: the erasure
    # boundary -- `::tpy::make_adapter<Base>(factory(args))`, moving the
    # concrete coro frame into the heap adapter (the one allocation, paid
    # exactly here). Base spells from the SLOT protocol via the SAME helper
    # the AST render uses (dynamic_base_name), so the two cannot drift. The
    # factory lowers as an ordinary plain/imported free call
    # (use.coro_factory lifts only the async-callee reject).
    coro_proto = _dyn_own_coro_factory_arg(a, ptype, lc.analyzer)
    if coro_proto is not None:
        _witness("call.coro_factory_adapter")
        inner = _lower_expr(a, lc, declared,
                            use=_ExprUse(coro_factory=True,
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
        # adapter with the AST's exact spelling (`std::move(*(c))`, the bare
        # name inside the wrap -- not the frame-deref `(*c)` render).
        _witness("call.coro_handle_adapter")
        inner = replace(_lower_expr(a, lc, declared), deref=False)
        base = dynamic_base_name(handle_proto, lc.analyzer)
        return THIRCoerce(
            result_type=unwrap_send_sync(ptype), expr=inner,
            coercion_name="dyn_own_adapter",
            wrap=f"::tpy::make_adapter<{base}>(std::move(*({{0}})))",
            form=inner.form, loc=getattr(a, "loc", None))
    conf = _dyn_own_conformer_arg(a, ptype, declared, lc.analyzer)
    if conf is not None:
        # The concrete-conformer faces of _gen_dynamic_protocol_own_arg,
        # verdict-keyed via the shared classifier: an inheritance conformer
        # takes `std::make_unique<U>(x)` (unique_ptr<U> converts to
        # unique_ptr<P>), a structural one the owning
        # `::tpy::make_adapter<Base>(x)` Adapter wrap. A movable NAME source
        # moves in (`_maybe_move` -> _is_move_source); a ctor rvalue lands
        # bare.
        conf_proto, verdict = conf
        inner = _lower_expr(a, lc, declared,
                            use=replace(_NESTED_ARG_USE,
                                        result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args),
                            allow_unrouted_name=True)
        if isinstance(a, TpyName):
            if _ptr_read_derefs(a.name, lc):
                assert isinstance(inner, THIRName)
                inner = replace(inner, deref=True)
            if _is_move_source(a, lc):
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
    # The two arg-temp rows, admitted only when the enclosing
    # statement position flushes (`temp_args`; see _lower_expr). The record
    # row mirrors the ref-param cascade arm: the temp declares the SLOT's
    # bare `to_cpp()` (`TempState.create`'s render -- same-nominal only, so
    # no upcast Child spelling arises). The union row mirrors
    # `_gen_union_arg`'s value branch: the temp declares the slot variant
    # (`create_typed` with `types.type_to_cpp`, == `to_cpp()` on the
    # scalar-member slice). A narrowed subject reads its extraction alias
    # while the AST's `already_union` verdict renders it bare -- rejected
    # (`_value_union_temp_arg`); the check here is defense in depth.
    # The protocol-slot wrap (`_gen_dynamic_protocol_arg` / the free-call
    # protocol temp arm). A bare verdict means the arg renders like any other
    # -- fall through to the tail, which carries the pointer-local deref retag.
    # `protocol_slots` marks the loops that actually run those pre-arms; the
    # builtins loop does not, and renders a protocol slot bare.
    proto = _protocol_arg_slot(ptype) if protocol_slots else None
    if proto is not None:
        if _coro_factory_structural_arg(a, proto, lc.analyzer):
            # The coro-factory structural temp (`auto __tmp_N = f();`):
            # gate-admitted under temps_ok only; the frame result lowers
            # under use.coro_factory (lifts only the async-callee reject).
            if not temp_args:
                raise ThirUnsupported(
                    "protocol arg-temp outside a flush position")
            _witness("argtemp.protocol")
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE, coro_factory=True)),
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
            return THIRArgTemp(
                result_type=proto, cpp_type=None,
                init=_lower_expr(
                    a, lc, declared,
                    use=replace(_NESTED_ARG_USE,
                                result=_ExprResultUse.ITERABLE)),
                form=Form.BORROW, loc=getattr(a, "loc", None))
        at = lc.analyzer.get_expr_type(a)
        if isinstance(a, TpyArrayLiteral):
            # A container literal's expr type is still PENDING; the AST's
            # temp declares the RESOLVED array/list (sema's decision, final
            # before lowering).
            at = resolve_pending_container(at, lc.analyzer) or at
        if isinstance(unwrap_readonly(unwrap_ref_type(at)), PendingListType):
            # A literal-seeded local's binding can still be PENDING here
            # (the AST resolves it at its own later render point);
            # render_type would crash -- reject to the AST path instead.
            note_detail("call.protocol_arg_pending")
            raise ThirUnsupported("expr.call")
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
            else:
                init = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
            if isinstance(a, TpyName) and _ptr_read_derefs(a.name, lc):
                assert isinstance(init, THIRName)
                init = replace(init, deref=True)
            _witness("argtemp.protocol")
            return THIRArgTemp(result_type=proto, cpp_type=cpp_type,
                               init=init, brace_init=brace_init,
                               form=Form.BORROW, loc=getattr(a, "loc", None))
    if temp_args:
        cov_slot = _covariant_temp_arg(a, ptype, declared, lc.analyzer)
        if cov_slot is not None:
            # The covariant-upcast typed temp (`Box<Shape> __tmp_N =
            # std::move(bc);` -- _gen_covariant_arg): the target-typed init
            # absorbs the converting move; the arg position reads the temp
            # bare. A movable NAME moves in, a ctor rvalue lands bare.
            inner = _lower_expr(a, lc, declared,
                                use=replace(_NESTED_ARG_USE,
                                            result=_ExprResultUse.BORROW_BIND,
                                            allow_temps=temp_args),
                                allow_unrouted_name=True)
            if isinstance(a, TpyName):
                if _ptr_read_derefs(a.name, lc):
                    assert isinstance(inner, THIRName)
                    inner = replace(inner, deref=True)
                if _is_move_source(a, lc):
                    inner = THIRMove(result_type=inner.result_type,
                                     value=inner, form=inner.form,
                                     loc=getattr(a, "loc", None))
            _witness("argtemp.covariant")
            return THIRArgTemp(result_type=cov_slot,
                               cpp_type=lc.render_type(cov_slot),
                               init=inner, form=Form.BORROW,
                               loc=getattr(a, "loc", None))
        rec_pt = _record_rvalue_temp_slot(a, ptype, lc.analyzer,
                                          frame_capturing=frame_capturing,
                                          upcast_ok=True)
        if (rec_pt is not None and isinstance(a, TpyMethodCall)
                and not _module_qual_ctor_shape(a, lc.analyzer)):
            # Only the module-qualified CTOR slice of method-call sources
            # hoists here; a qualified NON-ctor record call renders INLINE
            # on the AST path (`samestat(s, ::tpystd::os::stat(d))` -- the
            # _native_record_call_arg row below).
            rec_pt = None
        if rec_pt is not None:
            _witness("argtemp.record_rvalue")
            return THIRArgTemp(
                result_type=rec_pt, cpp_type=rec_pt.to_cpp(),
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
                result_type=ut, cpp_type=lc.render_type(ut),
                init=init,
                form=Form.VALUE,
                loc=getattr(a, "loc", None))
        # A list/dict LITERAL into a recursive-union WRAPPER slot
        # (`json.dumps([1, 2, 3])`): `_gen_union_arg`'s value branch hoists
        # `JsonValue __tmp_N = std::vector<JsonValue>{...};` (create_typed,
        # `= init` form) and passes the bare temp name.
        ru = _ru_wrapper_arg_slot(ptype)
        if ru is not None and _ru_container_literal_ok(a, lc.analyzer):
            _witness("argtemp.recursive_union_literal")
            return THIRArgTemp(result_type=ru, cpp_type=lc.render_type(ru),
                               init=_lower_ru_literal(a, ru, lc, declared),
                               form=Form.VALUE,
                               loc=getattr(a, "loc", None))
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
                               cpp_type=lc.render_type(ru_m),
                               init=init, form=Form.VALUE,
                               loc=getattr(a, "loc", None))
    # A str-slice arg into an `Own[str]` container element slot
    # (`xs.append(s)`): a VIEW-form source (BORROW -- a str param / StrView
    # local) materializes an owned copy `std::string(x)` via the S1 view->owned
    # THIRFormConvert, exactly like `_lower_container_elem`'s element wrap; a
    # str literal (VALUE, const char[N]) lands bare. The gate
    # (`_str_owned_slot_arg`) admits only these two -- an owned STORAGE source
    # takes gen_call_arg's copy+move temp cascade, left on the AST path.
    ow_str = _plain_own_slot(ptype)
    if ow_str is not None and is_str_type(ow_str):
        if (isinstance(a, TpyName)
                and a.name in lc.pending_view_unpack_targets):
            # The AST's _is_str_view_source misses an unpack target's view
            # binding (pending-typed, absent from its runtime-view
            # bookkeeping) and takes the owned copy+move temp cascade --
            # unmirrored, so reject rather than route-and-diverge.
            note_detail("call.arg_unpack_pending_view")
            raise ThirUnsupported("call.arg_unpack_pending_view")
        lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
        if lowered.form is Form.BORROW:
            return THIRFormConvert(result_type=ow_str, value=lowered,
                                   form=Form.STORAGE, loc=getattr(a, "loc", None))
        return lowered
    # `copy(name)` of a plain F1-record into a SAME-nominal `Own[record]`
    # slot: the copy-construct rvalue (`push_back(Point(p))`) binds the
    # `T&&` slot directly -- no temp, no move. Re-runs the source check with
    # the live pointer set (the gate could not see it); a pointer-local
    # source falls through to the tail and rejects.
    own_slot = _plain_own_slot(ptype)
    if own_slot is not None:
        crow = _lower_copy_record(a, lc, declared, slot_type=own_slot,
                                  exact=True, use=_NESTED_ARG_USE,
                                  loc=getattr(a, "loc", None))
        if crow is not None:
            return crow
    # The Own-slot copy+move row: `auto __tmp_N = <arg>;` + the move wrap
    # at the arg position -- or the temp-free `std::move(name)` when the
    # name is movable at its last use (`_maybe_move` fires before the
    # copy arm on the AST path; a scalar / pointer-local / field read is
    # never movable, so it always copies). A pointer-local name derefs in
    # the temp init (`auto __tmp_N = (*p);`), like the plain record-arg
    # retag below. The MOVE half is position-independent (no flush needed),
    # so it runs outside `temp_args` too -- the `heap_take(value)` ctor-MIL
    # shape; the copy half still needs the flush (gate-enforced).
    ow = _own_lvalue_temp_slot(a, ptype, lc.analyzer)
    if ow is not None and not (isinstance(a, TpyName)
                               and (a.name in lc.narrow.narrowed
                                    or a.name in lc.inline_narrowed)):
        own_form = Form.VALUE if _eligible_scalar(ow) else Form.STORAGE
        # VALUE payloads never move: lc.movable_locals is the RAW sema set
        # (the _LowerCtx caveat), while codegen registers movables only at
        # NON-VALUE decl arms -- a sema-movable scalar local would over-move
        # (`xs.append(std::move(n))` where the AST renders bare). The filter
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
            lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                                  allow_unrouted_name=True)
            if isinstance(a, TpyName) and _ptr_read_derefs(a.name, lc):
                assert isinstance(lowered, THIRName)
                lowered = replace(lowered, deref=True)
            _witness("move.own_last_use")
            return THIRMove(result_type=ow, value=lowered, form=own_form,
                            loc=getattr(a, "loc", None))
        if temp_args:
            lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                                  allow_unrouted_name=True)
            if isinstance(a, TpyName) and _ptr_read_derefs(a.name, lc):
                assert isinstance(lowered, THIRName)
                lowered = replace(lowered, deref=True)
            _witness("argtemp.own_copy")
            return THIRArgTemp(result_type=ow, init=lowered, move=True,
                               form=own_form, loc=getattr(a, "loc", None))
    ro_cont = _readonly_container_rvalue_arg(a, ptype, lc.analyzer)
    if ro_cont is not None:
        # An empty container rvalue at a readonly slot binds INLINE: the
        # `[]` literal takes the typed empty spelling
        # (`std::vector<int32_t>{}`), the hint-typed `list()` renders
        # through the instantiation arm (`std::vector<int32_t>()`).
        _witness("arg.readonly_empty_container")
        if isinstance(a, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            lit = _lower_expr(a, lc, declared, target_type=ro_cont)
            if (isinstance(lit, THIRContainerLiteral)
                    and lit.typed_brace_cpp is None):
                lit = replace(lit, typed_brace_cpp=lc.render_type(ro_cont))
            return lit
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
    ow_opt = _opt_own_record_name_arg(a, ptype, declared, lc.analyzer)
    if ow_opt is not None:
        # A same-nominal record NAME into an `Optional[Own[T]]` slot: the
        # movable last use renders `std::move(name)` BARE (the optional's
        # converting ctor absorbs the move); the copy shape is unwitnessed
        # and rejects.
        if not _is_move_source(a, lc):
            note_detail("call.opt_own_copy")
            raise ThirUnsupported("expr.call")
        lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                              allow_unrouted_name=True)
        _witness("move.opt_own_last_use")
        return THIRMove(result_type=ow_opt, value=lowered,
                        form=Form.STORAGE, loc=getattr(a, "loc", None))
    it_pb = _protocol_binding(ptype)
    if (it_pb is not None and it_pb.name in ("Iterable", "Sequence")
            and isinstance(a, TpyName)):
        # A container NAME into a native/template Iterable slot (only the
        # native-loop gates admit this pairing; a plain-TPy Iterable param
        # rejects upstream, so this arm cannot fire for the adapter-wrap
        # shape). On a STUB-METHOD arg loop a movable LAST-USE name takes
        # the consuming-__iter__ wrap (`xs.extend(b)` ->
        # `::tpy::own_iter(std::move(b))` -- _gen_consuming_iter); the
        # FREE-native loop binds the name bare even at its last use
        # (`sum(xs)` -- corpus-verified, the wrap there would diverge),
        # as does a family with NO consuming __iter__ (Array, Span).
        # COUPLING: the AST wraps a consuming source on PLAIN user-fn and
        # generic-instantiation calls too; those shapes stay off THIR only
        # via unrelated gates (structural-protocol params reject the free
        # call wholesale, call.inst_arg_lastuse rejects the instantiation
        # face). Loosening either gate must revisit the method_arg_stub
        # key here, or the free-call face renders bare where AST wraps.
        if method_arg_stub:
            wrapped = _consuming_iter_wrap(a, ptype, lc, declared)
            if wrapped is not None:
                return wrapped
        return _lower_expr(a, lc, declared)
    if (it_pb is not None and it_pb.name in ("Iterable", "Sequence")
            and isinstance(a, (TpyCall, TpyMethodCall))):
        # A container-returning call rvalue bound bare by the Iterable slot
        # (`a.extend(copy(b))` / `a.extend(make_nodes())`): STORAGE use so
        # the inner call's result gate admits the container. `allow_temps`
        # rides through (the statement flush covers nested args).
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE,
                                        allow_temps=temp_args))
    ow_slot = _plain_own_slot(ptype) if isinstance(a, TpyMethodCall) else None
    if (ow_slot is not None
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
    if ow_slot is not None and _f1_record(ow_slot, lc.analyzer):
        # An Own-slot record METHOD-call rvalue (admitted by
        # _own_record_rvalue_arg -- `Arc.new(Mutex.new(0))`): the inline
        # rvalue binds the T&& slot; lower under BORROW_BIND like the
        # owned-record decl's method row, whose record-result gate this
        # position shares.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND,
                                        allow_temps=temp_args))
    if (ow_slot is not None
            and _storage_call_ret(
                unwrap_readonly(unwrap_send_sync(ow_slot)),
                lc.analyzer) is not None):
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
    # The pointer-repr Optional slot faces (must run BEFORE the pointer-local
    # deref retag: an already-pointer name passes BARE into the `T*` slot).
    # A narrowed subject is NOT skipped: its read renames to the extraction
    # alias inside _lower_expr and the 'name' face's `&(...)` wrap mirrors
    # the AST's `&(__u)` render (see _optional_ptr_arg).
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
            # bare record read is not a value leaf); the addr_of wrap matches
            # the AST's `&(gen)` tail.
            _witness("optptr.subscript")
            return THIROptionalPtrArg(
                result_type=ot, form=Form.BORROW,
                value=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
                                  subscript_prechecked=True),
                addr_of=True, loc=loc)
        if opt_face == 'ctor':
            # Admitted only under temps_ok; a silent fall-through would render
            # the bare (un-addressed) ctor. A match guard admits calls but is
            # never a flush point, so reject here instead of asserting.
            if not temp_args:
                raise ThirUnsupported("optional-ptr ctor face outside a flush position")
            inner = unwrap_readonly(ot.inner)
            _witness("optptr.ctor_rvalue")
            # A marker-call rvalue init is the temp's storage sink (the AST
            # hoists `auto __tmp_N = <call>;`), so its F1-record result is
            # admitted like any owned-record storage slot.
            init_use = (_ExprUse(record_ctor=_RecordCtorUse.NESTED_ARG,
                                 result=_ExprResultUse.STORAGE)
                        if isinstance(a, TpyMethodCall) else _NESTED_ARG_USE)
            # A subclass ctor rvalue declares the CHILD's type; &(child)
            # binds the base pointer implicitly (the AST's upcast temp).
            at_c = lc.analyzer.get_expr_type(a)
            at_c = unwrap_readonly(at_c) if at_c is not None else None
            tmp_t = (at_c if isinstance(at_c, NominalType) and at_c != inner
                     else inner)
            return THIRArgTemp(result_type=tmp_t, cpp_type=tmp_t.to_cpp(),
                               init=_lower_expr(a, lc, declared, use=init_use),
                               addr_of=True,
                               form=Form.BORROW, loc=loc)
        if opt_face == 'lift':
            _witness("optptr.lift")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lower_field_source(a, lc, declared),
                                      lift=True, loc=loc)
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
        lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
        assert isinstance(lowered, THIRName)
        return replace(lowered, deref=True)
    if isinstance(a, TpyName) and a.name == lc.self_receiver:
        # `self` passed by reference derefs the receiver pointer
        # (`on_init((*this))`); a resumable method's `__self` frame field is
        # already a `Record&` and reads bare.
        lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
        assert isinstance(lowered, THIRSelf)
        return replace(lowered, deref=lc.self_is_pointer)
    # A float literal into a Float32 (or Own[Float32]) slot renders with the
    # `f` suffix, and an int literal into a FREE-call BigInt slot takes the
    # ctor wrap -- gen_call_arg threads the param type into the render. A
    # METHOD arg's int literal stays BARE: gen_call_from_fi's
    # `_convert_to_fixed_int_arg` emits IntLiterals as plain C++ integers
    # (`items.push_back(2)` -- BigInt's implicit int ctor absorbs it).
    # A union slot admits an assignment-narrowed union-declared name's bare
    # variant read (the `already_union` pass-through) -- the member-typed-sink
    # reject stays in force for a NON-union slot.
    slot_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype))) \
        if isinstance(ptype, TpyType) else None
    # An owned str-family field read passed bare into a value slot (`repr(
    # self.name)` -> `::tpy::repr_of(this->name)`) admits as STORAGE, matching
    # the fstring arg's own owned-str field read; admission already validated
    # the shape via `_shared_pass_through_arg` / `_protocol_slot_arg`.
    # `allow_temps` rides into a call-shaped arg (the AST flushes nested arg
    # temps at the enclosing statement); inert for every non-call shape.
    # `nested_temps` grants the same ride when the arg's OWN rows are
    # temp-free (a method arg's record rvalue renders inline, but its nested
    # ctor args still flush their temps at the statement).
    lowered = _lower_expr(a, lc, declared,
                          use=replace(_NESTED_ARG_USE,
                                      allow_temps=temp_args or nested_temps),
                          field_owned_str_ok=isinstance(a, TpyFieldAccess),
                          allow_union_divergent=isinstance(slot_u, UnionType))
    if (method_arg and isinstance(lowered, THIRLiteral)
            and isinstance(lowered.value, (int, float))
            and not isinstance(lowered.value, bool)):
        # A USER-RECORD method arg renders numeric literals target-less (the
        # AST's record loop passes target_type=None -- `c.bump(5)` into a
        # BigInt param stays bare). A builtin-stub member threads the RAW
        # param like gen_call_arg's `_args()` loop: an Own-wrapped slot hint
        # renders bare (the hint is never Own-unwrapped -- `s.insert(7)`,
        # `xs.push_back(2)`), a plain BigInt/Float32 slot takes the
        # target-typed render (`s.erase(::tpy::BigInt(3))`).
        if method_arg_stub and isinstance(ptype, TpyType):
            st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if not isinstance(st, OwnType):
                return _slot_literal_retype(lowered, ptype, lc)
        return lowered
    return _slot_literal_retype(lowered, ptype, lc)

def _lower_ctor_call_args(args: list[TpyExpr], fi, lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          temp_args: bool) -> list[THIRExpr]:
    """Lower a resolved user-record ctor's args through the shared per-arg
    dispatch -- the THIR mirror of `_gen_record_ctor_args`, used by both the
    `X(args)` construction path and the `raise X(args)` statement. `temp_args`
    is set at flush positions (statement level), admitting the mutated-slot /
    Own-copy / union-lift / protocol temp rows. Raises `ThirUnsupported` for an
    arg shape not yet routed (the caller tags the body-level fallback)."""
    analyzer = lc.analyzer
    use = _ExprUse(allow_temps=temp_args)
    ctor_mut = fi.mutated_params or frozenset()
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
                result_type=rec, cpp_type=rec.to_cpp(),
                init=_lower_expr(a, lc, declared), form=Form.BORROW,
                loc=getattr(a, "loc", None)))
        else:
            flush_slot = (
                _own_lvalue_temp_slot(a, p.type, analyzer) is not None
                or _union_ctor_temp_arg(a, p.type, analyzer)
                or _protocol_arg_slot(p.type) is not None
                # The optional-ptr 'ctor' face's ArgTemp, gate-admitted
                # under temps_ok (see the construction-site twin).
                or _optional_ptr_arg_face(
                    a, p.type, declared, analyzer) == 'ctor')
            lowered.append(_lower_call_arg(
                a, p.type, lc, declared,
                temp_args=temp_args and flush_slot,
                protocol_slots=True))
    return lowered

def _lower_union_arg_lift(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          readonly_target: bool = False,
                          temp_args: bool = False,
                          ) -> 'THIRUnionArgLift | None':
    """The pointer-variant union-slot arg lift, or None when the arg renders
    bare. Mirrors `_gen_union_arg`'s dispatch over the gate-admitted shapes:
    a None literal is the monostate member; a member-typed ctor RVALUE hoists
    a named temp and lifts its address (`pv{&__tmp_N}`, the rvalue branch --
    flush positions only); a member-typed name lifts
    `pv{&(name)}` with the indirect deref for a pointer-local / `self`
    receiver (`&((*p))` / `&((*this))`, gen_expr_deref's render); an
    already-union name into a DEEP-CONST slot (a `readonly[...]` annotation
    or `readonly_target`, the threaded `deep_const_borrow_params` verdict)
    takes the explicit `ptr_variant_to_const` wrap -- and a deep-const slot
    spells the const-pointee variant throughout. A narrowed
    subject's C++ binding is still the variant (`already_union` via the
    declared type), so the AST falls to the default render -- the bare
    extraction alias, the `is_narrowed` wrap skip -- which the plain
    `_lower_expr` read reproduces; a same-union name into a MUTABLE slot
    renders bare the same way."""
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
        # `_gen_union_arg`'s rvalue branch: the member ctor hoists a named
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
            value=_lower_expr(a, lc, declared, use=_RECORD_TEMP_USE),
            temp_cpp=at.to_cpp(),
            form=Form.BORROW, loc=loc)
    if (not isinstance(a, TpyName) or a.name in lc.narrow.narrowed
            or a.name in lc.inline_narrowed):
        return None
    # `_gen_union_arg`'s `already_union` verdict keys on the C++ DECLARED type,
    # not the (assignment-narrowed) read type: a union-declared name whose read
    # type sema retyped to a member (`x: A | B = A(); f(x)`) is still the variant
    # in C++, so it renders bare into a mutable slot (or `ptr_variant_to_const`
    # for a deep-const slot). Only a genuinely member-TYPED name lifts.
    decl = declared.get(a.name)
    decl = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(decl)))
            if decl is not None else None)
    if isinstance(decl, UnionType):
        if deep_const:
            _witness("unionlift.const_wrap")
            return THIRUnionArgLift(
                result_type=ut, variant_cpp=variant_cpp,
                value=_lower_expr(a, lc, declared, allow_union_divergent=True),
                const_wrap=True, form=Form.BORROW, loc=loc)
        return None  # bare render at the call-arg tail
    at = lc.analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return None
    _witness("unionlift.member")
    return THIRUnionArgLift(
        result_type=ut, variant_cpp=variant_cpp,
        value=_lower_expr(a, lc, declared),
        deref=(_ptr_read_derefs(a.name, lc)
               or (a.name == lc.self_receiver and lc.self_is_pointer)),
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

def _retag_bytes_literal_view(value: THIRExpr, target: 'TpyType | None') -> THIRExpr:
    """Rewrite a bytes literal to its static-storage span render (BORROW) when
    the sink (a view-resolved binding / a BytesView return) is view-typed --
    the AST threads the target into gen_expr's TpyBytesLiteral arm."""
    if isinstance(value, THIRBytesLiteral) and is_bytes_view_type(target):
        return replace(value, form=Form.BORROW)
    return value

def _narrowed_opt_char_vs_str_literal(
        subj: TpyExpr, other: TpyExpr, lc: '_LowerCtx',
        declared: dict[str, TpyType]) -> bool:
    """A NARROWED value-repr Optional[Char] operand compared against a str
    literal. The AST's _comparison_targets keys the char-literal coercion on
    the RESOLVED (un-narrowed) operand type, so it never chars the literal
    here and emits the non-compiling `(*o) == "a"` render (BUGS.md); THIR's
    narrowed read types the slot Char and would silently emit the fixed
    `(*o) == 'a'`. Reject so the body falls back and stays byte-mirrored;
    lift when the AST render is fixed."""
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

def _lower_char_targeted(e: TpyExpr, target: TpyType | None,
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         use: _ExprUse = _ExprUse(),
                         allow_whole_optional: bool = False,
                         field_owned_str_ok: bool = False) -> THIRExpr:
    """Lower an expression whose slot may be Char-typed, mirroring gen_expr's
    char-literal arm: a str literal in a Char slot renders as a target-typed
    C++ char literal (`'x'`). Shared by the three positions the AST threads a
    Char target into the render -- comparison operands opposite a Char-typed
    value (`_comparison_targets`' char arm), Char-annotated decl inits, and
    call args into Char param slots. The gates admitted the literal only
    single-char; the other `_comparison_targets` arms (Optional narrowing)
    cannot arise -- Optional operands are gated out of the slice."""
    if isinstance(e, TpyStrLiteral) and _eligible_char(target):
        return THIRCharLiteral(result_type=CHAR, value=e.value,
                               loc=getattr(e, "loc", None))
    return _lower_expr(e, lc, declared, use=use, target_type=target,
                       allow_whole_optional=allow_whole_optional,
                       field_owned_str_ok=field_owned_str_ok)

def _cond_mixed_walrus_temps(cond: THIRExpr) -> bool:
    """True when a lowered condition carries BOTH a walrus binding and a
    hoisted arg temp -- the shape both restructured-head renders exclude (an
    in-head temp could run before the walrus assignment it reads; the AST
    keeps the legacy single-eval flush for it, sgen raises CodeGenError), so
    lowering rejects it and the body falls back whole. Every node kind that
    registers a pending temp at EMIT time counts: THIRArgTemp, a
    temp-bearing THIRUnionArgLift, and THIRVarargPack (its per-arg hoist)."""
    has_walrus = False
    has_temp = False

    def walk(n) -> None:
        nonlocal has_walrus, has_temp
        if isinstance(n, THIRWalrus):
            has_walrus = True
        if isinstance(n, (THIRArgTemp, THIRVarargPack)) or (
                isinstance(n, THIRUnionArgLift) and n.temp_cpp is not None):
            has_temp = True
        for f in dataclass_fields(n):
            v = getattr(n, f.name)
            if isinstance(v, THIRNode):
                walk(v)
            elif isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, THIRNode):
                        walk(item)

    walk(cond)
    return has_walrus and has_temp


def _strip_slot_leaf_deref(lowered: 'THIRExpr', lc: '_LowerCtx') -> 'THIRExpr':
    """Un-deref a pointer-name leaf consumed by machinery that applies its
    OWN indirect wrap (the async-for iterable, with managers, erased await
    operands): the AST reads the bare name at those seams and wraps via
    is_indirect_name, so a name-arm `(*g)` would double-deref."""
    if (isinstance(lowered, THIRName) and lowered.name in lc.pointers
            and lowered.deref):
        return replace(lowered, deref=False)
    return lowered


def _lower_truthy(e: TpyExpr, lc: '_LowerCtx',
                  declared: dict[str, TpyType], *,
                  unary_operand: bool = False,
                  temps_ok: bool = False) -> THIRExpr:
    """Lower one Python-truthiness position. `temps_ok` marks the cond
    positions whose emit places condition temps (the restructured while /
    sgen loop head, the pre-`if` flush, the nested-elif block); logical-op
    OPERANDS never thread it (a short-circuit RHS temp would hoist
    eagerly)."""
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                      TpyBytesLiteral, TpyNoneLiteral)):
        raise ThirUnsupported("truthy.literal")
    if isinstance(e, TpyName) and e.name in lc.narrow.any_narrowed:
        # A narrowed-Any subject read in truthy position: the AST keys
        # truthiness on the DECLARED Any (`::tpy::to_bool(alias)`) even
        # though sema retyped the occurrence -- mirror the declared-type
        # dispatch (the truthy sibling of the print-arg RAW mirror).
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
        # A truthy Optional field narrows its dotted path for later reads. THIR
        # does not carry that path fact yet, so routing the condition alone can
        # drop the AST's `(*field)` unwrap in the branch.
        raise ThirUnsupported("truthy.optional_field_narrow")
    if isinstance(e, TpyName) and et is not None:
        eu_name = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
        if isinstance(eu_name, OptionalType) and eu_name.uses_pointer_repr():
            inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                eu_name.inner)))
            # is_user_record excludes builtin containers (the AST's un-narrowed
            # ptr_truthy gate); _truthiness_mode says whether that record has a
            # truthiness dunder (RECORD_BOOL/RECORD_LEN).
            if (isinstance(inner, NominalType) and inner.is_user_record
                    and _truthiness_mode(inner, lc.analyzer) in (
                        TruthinessMode.RECORD_BOOL, TruthinessMode.RECORD_LEN)):
                # Un-narrowed pointer-repr Optional[record] truthiness now
                # dispatches ::tpy::ptr_truthy in the AST (null-check plus the
                # inner __bool__/__len__); THIR has no matching node yet, so
                # fall back for this shape.
                raise ThirUnsupported("truthy.optional_ptr_record")
    if isinstance(e, TpyName) and e.name in declared:
        du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[e.name])))
        eu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
              if et is not None else None)
        if (isinstance(du, OptionalType) and not isinstance(eu, OptionalType)
                and not du.uses_pointer_repr()):
            # A value-repr narrowed Optional name renders through
            # ::tpy::is_truthy on the whole optional in the AST; THIR has no
            # matching arm. Pointer-repr narrowed record optionals now dispatch
            # the narrowed inner's __bool__/__len__ in the AST, which
            # _truthiness_mode(et) mirrors from the same narrowed occurrence
            # type, so they route through the record-mode arms below.
            raise ThirUnsupported("truthy.optional_name_narrow")
    if wrap is None:
        ptr_optional = (
            isinstance(e, TpyName) and isinstance(et, OptionalType)
            and _optional_ptr_borrow_name(
                e, declared, lc.analyzer) is not None)
        if ptr_optional:
            pass
        elif unary_operand:
            # An eligible-scalar operand truthy-tests via C++'s contextual
            # conversion, so `not x` renders the bare `(!(x))` -- the AST's
            # scalar arm. Other modeless non-bool operands keep rejecting.
            if mode is None and (et is None
                                 or not (is_bool_type(et)
                                         or _eligible_scalar(et))):
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
            # A native-truthy integer name (`assert x` / `if n:` where n is
            # int/fixed-int) renders bare -- its truthiness IS its value render
            # (`if (x)`), the same bare-name property bool already takes. `mode`
            # is None for these (ordinary value render), so admit them alongside
            # bool rather than falling the body back.
            eu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
                  if et is not None else None)
            native_int = eu is not None and (
                is_big_int_type(eu) or is_fixed_int_type(eu))
            if (e.name not in declared or et is None
                    or (mode is None and not is_bool_type(et)
                        and not native_int)
                    or (unrouted is not None and not storage_optional)):
                raise ThirUnsupported("truthy.name")
        elif isinstance(e, TpyFieldAccess):
            if mode is None and (et is None or not is_bool_type(et)):
                raise ThirUnsupported("truthy.field_nonbool")
            if mode is None:
                _witness("cond.bool_field")
        elif isinstance(e, TpyBinOp):
            if e.op not in (_COMPARE_OPS | _LOGICAL_OPS
                            | _IS_OPS | _MEMBERSHIP_OPS):
                raise ThirUnsupported("truthy.binop")
        elif isinstance(e, TpyMethodCall):
            if mode is None and (et is None or not is_bool_type(et)):
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
            # `if (true)` folds), not the bare-call render.
            if (e.isinstance_var is not None
                    or e.isinstance_type is not None):
                raise ThirUnsupported("truthy.call_nonbool")
            if mode is None and (et is None or not is_bool_type(et)):
                raise ThirUnsupported("truthy.call_nonbool")
            if mode is None:
                _witness("cond.bool_call")
        elif isinstance(e, TpyIfExpr):
            if mode is None and (et is None or not is_bool_type(et)):
                raise ThirUnsupported("truthy.ifexpr_nonbool")
            _witness("ifexpr.cond_pos")
        elif isinstance(e, TpyNamedExpr):
            # A walrus truthy operand (`if (s := s + "!"):`): the mode wrap
            # composes over the inline assign (`(!(s = ...).empty())`); the
            # walrus arm validates its own target class. Only the empty-test
            # modes and bare bool/int walruses admit -- an IS_TRUTHY
            # (whole-optional) walrus operand has no verified oracle.
            wtu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
                   if et is not None else None)
            native_int = wtu is not None and (
                is_big_int_type(wtu) or is_fixed_int_type(wtu))
            if mode is TruthinessMode.NONEMPTY:
                pass
            elif mode is None and (native_int
                                   or (et is not None and is_bool_type(et))):
                pass
            else:
                raise ThirUnsupported("truthy.walrus_shape")
        elif not isinstance(e, (TpyUnaryOp, TpyChainedCompare)):
            raise ThirUnsupported("truthy.shape")
        operand = _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY, allow_temps=temps_ok,
                         truthy_discard=mode is TruthinessMode.ALWAYS_TRUE),
            allow_whole_optional=mode is TruthinessMode.IS_TRUTHY,
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
            and (e.name in lc.pointers
                 or (e.name == lc.self_receiver and lc.self_is_pointer))
            # A rebound CONTAINER local's name read already derefs `(*xs)`
            # (the F2d value-use deref in the name arm) -- the wrap must not
            # stack a second one.
            and not (isinstance(operand, THIRName) and operand.deref))
        _witness("truthy." + mode.name.lower())
        return THIRTruthy(
            result_type=BOOL,
            mode=mode,
            operand=operand,
            deref=deref,
            loc=getattr(e, "loc", None),
        )
    loc = getattr(e, "loc", None)
    # Both enum wraps substitute the AST's ordinary VALUE render (gen_truthy_expr
    # calls gen_expr, then wraps), so neither operand is a condition -- a
    # CONDITION use would demand a bool result the enum operand never has.
    _witness("enum.truthy_plain" if _plain_enum_truthy(et, lc.analyzer)
             else "enum.truthy_int")
    return THIREnumWrap(
        result_type=BOOL, wrap=wrap,
        operand=_lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY, allow_temps=temps_ok)),
        loc=loc)

def _str_view_arm(e: TpyExpr, lc: '_LowerCtx') -> bool:
    """Mirror ExpressionGenerator._is_str_view_at_runtime over the admitted
    ternary-arm shapes: a str literal and a `str`-declared PARAM are runtime
    string_views; a nested ternary is a view iff both its arms are; a coerce
    reads its expected type (get_resolved_type's coerce arm); everything else
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
        # string_view view. The AST reads this off `get_resolved_type` (which
        # narrows the read to StrView); sema's `get_expr_type` leaves the arm
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

def _lower_if_expr(e: TpyIfExpr, rtype: 'TpyType | None', lc: '_LowerCtx',
                   declared: dict[str, TpyType], loc, *,
                   cond_temps_ok: bool = False) -> THIRIfExpr:
    """`a if c else b` -> `((cond) ? (then) : (else))`, _gen_if_expr's render.
    The arm slot is the ternary's OWN resolved type (`branch_target =
    result_type` -- the consumer's target is ignored), so the target-typed
    literal renders (BigInt ctor wrap, Float32 `f` suffix, Char literal)
    thread from here, not from the position. For an owned-str result with
    mixed view/owned arms, the view arm materializes (`std::string(a)`) so
    the C++ ternary deduces std::string -- a str literal is const char* in
    ternary context and converts natively, so it stays bare."""
    analyzer = lc.analyzer
    slot = rtype
    if slot is not None:
        slot = resolve_int_literals(unwrap_readonly(slot),
                                    analyzer.ctx.default_int_for_literal)
    # The condition evaluates exactly once unconditionally, so the
    # enclosing flush right extends into it (the AST hoists its arg temps
    # before the statement); the ARMS evaluate lazily and never get it.
    cond = _lower_truthy(e.condition, lc, declared, temps_ok=cond_temps_ok)
    then = _slot_literal_retype(
        _lower_char_targeted(e.then_expr, slot, lc, declared), slot, lc)
    orelse = _slot_literal_retype(
        _lower_char_targeted(e.else_expr, slot, lc, declared), slot, lc)
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
        # The whole-ternary owned-sink copy fires iff the RESULT is a runtime
        # view: a StrView-resolved ternary or a both-view arm pair -- mirrors
        # _is_str_view_source over TpyIfExpr (never a top-level literal).
        form = (Form.BORROW if is_str_view_type(str_rt) or (tv and ev)
                else Form.STORAGE)
        _witness("ifexpr.str")
    elif _is_string_owned(rtype):
        # A String result (both arms concat results): an owned rvalue.
        form = Form.STORAGE
        _witness("ifexpr.str")
    elif _resolved_bytes_value(rtype, analyzer) is not None:
        # A bytes-family ternary. Only the both-view arm shape lowers (a
        # narrowed `(*a)` span and a bytes param `b`): the whole ternary is a
        # BORROW span, so the owned decl/return sink wraps `::tpy::bytes_copy`.
        # Mixed / owned-literal arms would need the str-mixed per-arm
        # materialization and defer.
        if not (_bytes_view_arm(e.then_expr, lc)
                and _bytes_view_arm(e.else_expr, lc)):
            raise ThirUnsupported("ifexpr.bytes_mixed")
        form = Form.BORROW
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
    """The single-overload fixed-int operator fi over `t` -- the AST's
    `get_type_method_fi(target_type, BINOP_TO_METHOD[op])` lookup for the
    dedicated literal arm, without a live BuiltinsGen. Mirrors ONLY the
    plain single-overload path of `BuiltinGenerator.get_type_method_fi`
    (builtins.py) -- an overload-selection change there must be re-mirrored
    here (nothing but the byte-diff enforces lockstep)."""
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
    """Mirror gen_expr's target threading for target-typed literal renders:
    a float literal against a Float32 slot takes the `f` suffix; an integer
    literal takes fixed-width casts/suffixes or BigInt constructor wraps.
    The AST threads the slot type at decl inits/reassigns, returns,
    call/ctor args, field writes, MIL inits, container elements, and
    resolved-binop operands (the gen_expr_deref receiver/param targets) --
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
        # A negated float literal (`-3.0` into a Float32 slot): the `f` suffix
        # keys on the operand literal's type, so thread the slot into it, the
        # same target render the AST gives the unary operand (`-(3.0f)`).
        return replace(v, operand=replace(v.operand, result_type=st))
    if (isinstance(v, THIRBinOp) and v.both_literal_int_operands
            and is_fixed_int_type(st)):
        # gen_binop's dedicated fixed-int literal arm: a both-IntLiteral
        # binop meeting a fixed-int SLOT re-resolves the operator on the
        # slot type (`fi = get_type_method_fi(target, method)`) and renders
        # `gen_call_from_fi(fi, l, r)` -- target-width template, no parens.
        # Operands recurse (nested literal binops rebuild the same way, the
        # AST's `gen_expr(left, target_type)` recursion); an op outside the
        # module system (bitwise) takes the AST's bare `(l op r)` fallback.
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
    receiver/param types gen_expr_deref threads into the operand renders
    (forward: left={self}, right={0}; reverse swapped). Comparison operands
    never take these (the AST compare block renders them target-less)."""
    if rb is None or rb.method is None:
        return (None, None)
    param = rb.method.params[0].type if rb.method.params else None
    recv = rb.receiver_type
    return (param, recv) if rb.is_reverse else (recv, param)

def _field_cpp(e: TpyFieldAccess) -> str:
    """The rendered C++ member name for a field access. A `@native` record
    renames fields via `native_field("m_x")`; sema stamps the rename on
    `native_field_name` (own-fields-first, so a subclass redeclaration shadows
    an ancestor's) and `_gen_field_access` reads it -- mirror that here rather
    than always escaping the source name, else native-record field reads
    diverge."""
    return (e.native_field_name if e.native_field_name is not None
            else escape_cpp_name(e.field))

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
    # The prechecked receiver recursion below skips the receiver ladders --
    # an UNPROVEN Optional intermediate link (`o.mid.f` with `o.mid`
    # analyzed Optional) would silently drop the AST's deref_optional_check
    # panic; reject the chain at the chokepoint so every caller is covered.
    # (A sema-narrowed link reads non-Optional here and passes.)
    link = e.obj
    while isinstance(link, (TpyFieldAccess, TpySubscript)):
        if isinstance(unwrap_readonly(lc.analyzer.get_expr_type(link)),
                      OptionalType):
            raise ThirUnsupported("field.opt_receiver", detail=True)
        link = link.obj
    rtype = lc.analyzer.get_expr_type(e)
    narrowed_opt = _narrowed_opt_field_read(e, rtype, declared, lc.analyzer)
    if narrowed_opt:
        _witness("field.narrowed_deref")
    return THIRFieldAccess(
        result_type=rtype,
        # RECEIVER, like every sibling field arm: an indirect receiver reaches
        # its member through `is_arrow`, so a value-position deref here would
        # compose into `(*h)->value`.
        receiver=_lower_expr(
            e.obj, lc, declared,
            use=_ExprUse(result=_ExprResultUse.RECEIVER),
            field_prechecked=isinstance(e.obj, TpyFieldAccess),
            subscript_prechecked=isinstance(e.obj, TpySubscript)),
        field_cpp=_field_cpp(e),
        is_arrow=_field_is_arrow(e, lc),
        narrowed_deref=narrowed_opt,
        form=Form.STORAGE,
        loc=getattr(e, "loc", None),
    )

        # Param names live on `prescan.param_names` (the single copy): a
        # `str`-typed PARAM name is a `std::string_view` in the C++ signature
        # while an owned str LOCAL of the same resolved type is a `std::string`
        # -- the str name-form classifier needs the distinction (see
        # _str_name_form), and the aug-append gate excludes params the same way
        # (see _str_aug_append_ok).


def _is_move_source(value: TpyExpr, lc: _LowerCtx,
                    movable_names: 'set[str] | None' = None) -> bool:
    """Whether a write / return / MIL source moves rather than copies: the last use
    of a movable (owned) name. Mirrors the AST's `_is_last_use_movable(expr,
    movable_names)` (peel `TpyCoerce`; a `TpyName` in the movable set whose node is a
    last use). `movable_names` defaults to the function's `movable_locals` (the
    F2b/F2e write/return case -- only an F2d REBIND_SLOT local is owned there); the
    ctor MIL passes `own_param_names` instead (M3b-move), since no locals exist yet at
    MIL time (the MIL runs before the body) and its movable sources are the Own params."""
    names = lc.movable_locals if movable_names is None else movable_names
    inner = _peel_coerce(value)
    return (isinstance(inner, TpyName)
            and inner.name in names
            and id(inner) in lc.analyzer.ctx.all_last_uses)
