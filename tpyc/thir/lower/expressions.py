"""Expression lowering and admission for `_lower_expr` and its recursive arms.

Composite consumer-shape helpers live in `checks.py`; `_lower_expr` invokes them
only from the node arm being lowered.
"""

from __future__ import annotations
import math
from dataclasses import field, replace
from ... import qnames
from ...parse.nodes import (
    FSTRING_CONV_NONE,
    FSTRING_CONV_REPR,
    FSTRING_CONV_STR,
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
    is_protocol_type,
    is_void_like_type,
    make_array,
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
from ...codegen_cpp.types import resolve_pending_container
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
from ...codegen_cpp.protocols import dynamic_base_name
from ...symbol_binding import SymbolKind, lookup_imported
from ...coercions import wrap_into_any, CoercionContext
from ...compilation_context import get_current_compiler
from ...value_category import is_rvalue_source
from ..fallback import ThirUnsupported, expr_kind_tag, note_detail
from ..faces import witness as _witness
from ...codegen_cpp.expressions import ExpressionGenerator
from ...codegen_cpp.int_literals import render_int_literal_value
from ..nodes import (
    Form,
    TruthinessMode,
    THIRArgTemp,
    THIRBinOp,
    THIRChainedCompareStmtExpr,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRClassConstant,
    THIRCoerce,
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
from ...codegen_cpp.forms import is_ptr_variant_union
from .predicates import (
    _BIGINT_LIT_COERCION,
    _BIGINT_NARROW,
    _eligible_ptr_union,
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
    _truthiness_mode,
    _f1_record,
    _f1_tuple,
    _field_over_global_record_ok,
    _field_over_subscript_ok,
    _field_receiver_ok,
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
    _is_none_compare_operand,
    _value_opt_rvalue,
    _narrowed_opt_field_read,
    _ADDR_PTR_COERCIONS,
    _PTR_IDENTITY_COERCIONS,
    _SPANLIKE_COERCIONS,
    _SPAN_METHOD_COERCIONS,
    _is_string_owned,
    _mixed_sign_compare,
    _module_var_read_cpp,
    _nonvalue_container_ret,
    _narrow_bigint_index,
    _optional_ptr_arg_face,
    _optional_ptr_arg_slot,
    _optional_ptr_borrow,
    _optional_ptr_borrow_name,
    _optional_checked_field,
    _optional_field_over_subscript_ok,
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
    _range_counter_type,
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
    _value_union_temp_slot,
    _union_binding_divergent,
    _any_compare_pair,
    _any_none_subject,
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
    _container_method_arg_ok,
    _stub_method_ret_ok,
    _native_iterable_literal_arg,
    _record_field_ref_arg,
    _container_lit_slot_family,
    _container_literal_shape_ok,
    _ctor_instantiation_ok,
    _ctor_effective_params,
    _ctor_shape_ok,
    _dyn_own_coro_factory_arg,
    _dyn_own_handle_arg,
    _field_over_call_ok,
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
    _optional_ptr_arg,
    _union_member_lift_arg,
    _own_lvalue_arg,
    _own_move_arg,
    _own_optional_record_rvalue_arg,
    _protocol_slot_arg,
    _plain_call_arg_ok,
    _ptr_deref_method_call,
    _ptr_deref_recv_ok,
    _ptr_template_method_supported,
    _record_rvalue_call_shape,
    _native_record_rvalue_call_shape,
    _record_rvalue_temp_arg,
    _native_ctx_manager_ok,
    _shared_pass_through_arg,
    _str_pass_through_arg,
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
    return (use.record_ctor is not _RecordCtorUse.NESTED_ARG
            and _ctor_instantiation_ok(e, lc.analyzer))


def _protocol_union_ctor_arg(arg: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType],
                             analyzer) -> 'str | None':
    """A NAME into a ctor slot whose non-None members are all PROTOCOLS:
    an F1-record name and a Span name both take the address-of lift
    (`&(a)` / `&(s)` -- the C++ ctor's protocol overload binds the
    pointer; a bare record render was probe-caught divergent, the
    corpus's seeming bare witness was `copy(a)`'s copy-construct, a
    different construct). Container names are unwitnessed and stay
    deferred. Returns 'addr' or None."""
    if not isinstance(arg, TpyName) or arg.name not in locals_:
        return None
    slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
            if ptype is not None else None)
    if not isinstance(slot, UnionType):
        return None
    members = [m for m in slot.members if not is_void_like_type(m)]
    if not members or not all(
            isinstance(m, NominalType) and m.is_protocol for m in members):
        return None
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        locals_[arg.name])))
    if _f1_record(at, analyzer) or is_span(at):
        return "addr"
    return None


def _record_ctor_arg_supported(
        arg: TpyExpr, param_type: TpyType, index: int, fi,
        lc: '_LowerCtx', declared: dict[str, TpyType], use: _ExprUse) -> bool:
    analyzer = lc.analyzer
    mutation_unknown = fi.mutated_params is None
    mutated = fi.mutated_params or frozenset()
    is_mutated = index in mutated
    if use.record_ctor is not _RecordCtorUse.NESTED_ARG:
        # temps_ok tracks whether THIS ctor position flushes. DIRECT threads
        # its own allow_temps; a RECORD_TEMP source ctor flushes at the
        # enclosing statement too when it was reached via the flush-enabled
        # recursion (`_RECORD_TEMP_FLUSH_USE`), so `use.allow_temps` is the
        # single source of truth for both.
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
                or (not is_mutated
                    and _container_literal_arg(arg, param_type, analyzer)
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
                # The pointer-repr Optional slot faces (`n` into a `record |
                # None` ctor param -> `&(n)`), mirroring the free-call gate's
                # `_optional_ptr_arg` row: `_gen_record_ctor_args` runs the same
                # `_gen_optional_ptr_arg` dispatch as the plain call loop. The
                # temp-bearing 'ctor' face is temps_ok-gated like the rows above.
                or _optional_ptr_arg(arg, param_type, declared, analyzer,
                                     temps_ok=temps_ok)
                # A protocol-conformer arg into a @dynamic/structural protocol
                # ctor slot: a bare NAME / already-protocol lvalue passes
                # through, a conformer RVALUE hoists the adapter temp
                # (`_protocol_arg_temp` -- flush-gated). Lowered by
                # `_lower_call_arg`'s protocol pre-arm (protocol_slots=True).
                or _protocol_slot_arg(arg, param_type, declared, analyzer,
                                      temps_ok=temps_ok)
                or (_record_rvalue_temp_arg(
                        arg, param_type, declared, analyzer)
                    and (temps_ok if is_mutated else True)))

    slot = unwrap_readonly(unwrap_ref_type(param_type))
    if _eligible_scalar(slot):
        return _resolved_scalar(analyzer.get_expr_type(arg), analyzer)
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
        temp_args: bool) -> None:
    if not _method_call_arg_ok(
            e, a, ptype, index, declared, lc.analyzer,
            temps_ok=temp_args, narrowed=frozenset(lc.narrow.narrowed),
            param_names=lc.prescan.param_names,
            tparam_bounds=lc.tparam_bounds):
        raise ThirUnsupported("expr.method_call")


def _lower_marker_method_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool) -> THIRExpr:
    if isinstance(a, TpyVarargPack):
        # A `*args` pack into a variadic module function (math.hypot(3, 4)):
        # the qualcall arg loop forwards the pack unchanged, rendered via the
        # same _gen_vararg_pack helper as a free call. The pack's own lowering
        # validates the element shapes and raises otherwise.
        return _lower_vararg_pack(a, ptype, lc, declared, temp_args=temp_args)
    _require_method_call_arg(
        e, a, ptype, index, lc, declared, temp_args=temp_args)
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
    `auto&&` alias receiver likewise holds its elements by value -- both take `.`."""
    res = _subscript_index_and_tuple(sub, lc.analyzer)
    if res is None:
        return False
    recv_t, idx = res
    et = recv_t.element_types[idx]
    return (et.value_form() is ValueForm.BORROW_REF
            and TupleType._element_is_pointer_repr(et)
            and isinstance(sub.obj, TpyName)
            and sub.obj.name not in lc.storage_tuple_locals)

def _subscript_result_form(sub: TpySubscript, rtype: TpyType, lc: '_LowerCtx') -> Form:
    """The form a tuple subscript result renders as. A value scalar is VALUE; a record
    element is BORROW (a `T*`/`T&`). An Optional element read off a storage-tuple alias
    is STORAGE (`std::optional<T>`, lifted by the consumer via optional_to_ptr); off a
    borrow tuple param it is already `T*` (BORROW)."""
    if not _is_borrow_form_name(rtype):
        return Form.VALUE
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if (isinstance(inner, OptionalType) and isinstance(sub.obj, TpyName)
            and sub.obj.name in lc.storage_tuple_locals):
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
            and (obj.name in lc.pointers
                 or (obj.name == lc.self_receiver and lc.self_is_pointer)))

def _is_own_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is an `Own[...]`-declared param of the function being
    lowered (incl. the own-optional shapes) -- the storage-owning binding."""
    for n, t in lc.func.params:
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


def _lower_binop(e: TpyBinOp, rtype: 'TpyType | None', lc: '_LowerCtx',
                 declared: dict[str, TpyType], loc) -> THIRExpr:
    analyzer = lc.analyzer

    def reject() -> None:
        raise ThirUnsupported(
            f"binop.shape.{e.op}"
            f"{_binop_operand_suffix(e, declared, analyzer)}",
            detail=True)

    # A `__contains__`-unresolved native-set membership routes via the AST's
    # `std::ranges::contains` fallback (set by the membership gate below).
    ranges_contains = False
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
        elif not _resolved_scalar(rtype, analyzer):
            reject()
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
              and not isinstance(e.left, TpyName)
              and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
              and not isinstance(e.right, TpyName)
              and not is_fixed_int_type(resolve_int_literals(
                  rtype, analyzer.ctx.default_int_for_literal))):
            # The AST constant-folds a both-literal int binop ONLY in a
            # target-less (BigInt/literal) context -- `2 + 3` -> `5`,
            # `2**63 - 1` -> `9223372036854775807LL` -- which THIR does not
            # reproduce, so reject. A FIXED-int target never folds (the slot
            # pins `::tpy::add_check<intN>(...)`), so those route. Only a BigInt
            # result whose value OVERFLOWS int64 (`2**64 + 1`) is rendered as a
            # full operator expr and routes.
            lit_val = getattr(analyzer.get_expr_type(e), "value", None)
            fits_i64 = lit_val is not None and -(2**63) <= lit_val <= 2**63 - 1
            is_bigint = is_big_int_type(resolve_int_literals(
                rtype, analyzer.ctx.default_int_for_literal))
            if not is_bigint or lit_val is None or fits_i64:
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
        lt = _operand_type(e.left, declared, analyzer)
        rt = _operand_type(e.right, declared, analyzer)
        if (rtype is None or not is_bool_type(rtype)
                or lt is None or not is_bool_type(lt)
                or rt is None or not is_bool_type(rt)):
            reject()
    elif e.op in _IS_OPS:
        if (_is_none_compare_operand(e, declared, analyzer) is None
                and _any_none_subject(e, declared, analyzer) is None):
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
                or fi.cpp_template or fi.native_function
                or not fi.native_name):
            reject()
        elif isinstance(e.right, TpyName):
            if e.right.name not in declared:
                reject()
            ct = unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(declared[e.right.name])))
            # A str needle renders bare into `contains(...)` on both paths
            # (literal / view name / owned local -- the container's transparent
            # lookup absorbs the form), exactly like a scalar needle. A
            # VIEW-keyed container (`set[StrView]`) threads view_key_target into
            # the needle's literal render (the static-storage pin) -- not
            # mirrored, reject.
            if not ((is_dict(ct) or is_set(ct))
                    and (_resolved_scalar(lt, analyzer)
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
        # Multiple evaluations of a non-trivial needle bind to a `__in_lhs` temp
        # in a statement expression (mirrors _gen_binop's need_temp).
        need_temp = (len(elems) > 1
                     and not isinstance(e.left, (TpyName, TpyIntLiteral,
                                                 TpyFloatLiteral, TpyStrLiteral,
                                                 TpyBoolLiteral)))
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
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared),
            needle=_lower_expr(e.left, lc, declared),
            method_cpp="",
            negate=e.op == "not in",
            ranges_contains=True,
            loc=loc)
    if e.op in _MEMBERSHIP_OPS:
        _witness("binop.membership")
        # A dict-view receiver (`d.values()`) renders the bare native view call
        # via the for-loop's ITERABLE override; a name/container receiver reads
        # bare.
        recv_use = (_ExprUse(result=_ExprResultUse.ITERABLE)
                    if isinstance(e.right, TpyMethodCall) else _ExprUse())
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared, use=recv_use),
            # A str-family FIELD needle renders the bare member read into the
            # contains(...) template on both paths (same owned-str-field-ok
            # position as the compare operands above).
            needle=_lower_expr(
                e.left, lc, declared,
                field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
            method_cpp=e.resolved_contains.native_name,
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
                 or (operand.name in declared
                     and _value_opt_scalar(declared[operand.name],
                                           lc.analyzer) is not None))) or (
            not isinstance(operand, (TpyName, TpyFieldAccess))
            and _value_opt_rvalue(operand, lc.analyzer) is not None)
        return THIRIsNone(
            result_type=rtype,
            operand=_lower_expr(
                operand, lc, declared, allow_whole_optional=True,
                field_prechecked=is_field),
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
        left = (_lower_unproven_opt_scalar(e.left, lc, declared)
                if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.left, rt_a, lc, declared,
            field_owned_str_ok=isinstance(e.left, TpyFieldAccess))
        right = (_lower_unproven_opt_scalar(e.right, lc, declared)
                 if e.op not in ("==", "!=") else None) or _lower_char_targeted(
            e.right, lt_a, lc, declared,
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
        left = _lower_unproven_opt_scalar(e.left, lc, declared)
        if left is None:
            left = _slot_literal_retype(
                _lower_expr(e.left, lc, declared,
                            field_owned_str_ok=isinstance(e.left, TpyFieldAccess)),
                lslot, lc)
        right = _lower_unproven_opt_scalar(e.right, lc, declared)
        if right is None:
            right = _slot_literal_retype(
                _lower_expr(e.right, lc, declared,
                            field_owned_str_ok=isinstance(e.right,
                                                          TpyFieldAccess)),
                rslot, lc)
    bt = _resolved_bytes_value(rtype, analyzer)
    lcast, rcast = _binop_operand_casts(e, analyzer)
    # A both-int-literal binop with a FIXED-int target renders through the AST's
    # dedicated `gen_call_from_fi` arm (`::tpy::add_check<intN>(l, r)`) with NO
    # wrapping parens -- unlike the generic resolved-binop path. Nested such
    # binops recurse the same way, so the whole tree is paren-free.
    paren_wrap = True
    if (e.resolved_binop is not None
            and getattr(e.resolved_binop.method, "cpp_template", None)
            and isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
            and not isinstance(e.left, TpyName)
            and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
            and not isinstance(e.right, TpyName)
            and rtype is not None
            and is_fixed_int_type(resolve_int_literals(
                rtype, analyzer.ctx.default_int_for_literal))):
        paren_wrap = False
    return THIRBinOp(
        result_type=rtype,
        left=left,
        op=e.op,
        right=right,
        resolved=e.resolved_binop,
        divisor_non_zero=e.divisor_non_zero,
        left_cast=lcast,
        right_cast=rcast,
        paren_wrap=paren_wrap,
        form=(Form.STORAGE if _is_string_owned(rtype)
              or (bt is not None and is_bytes_type(bt)) else Form.VALUE),
        loc=loc)

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
    for n, t in lc.func.params:
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


def _narrow_member_cpp(var: str, member: TpyType, u: UnionType,
                       lc: '_LowerCtx') -> tuple[str, bool]:
    """The final `std::get` template arg for a narrowed member read, plus the
    pointer-variant verdict: the ptr `*` suffix and the const-pointee prefix
    (`_narrow_subject_const`) applied at lowering. Shared by the extraction
    alias, the compound-condition inline read, and the assign-narrowed
    field-receiver read."""
    member_cpp = lc.render_type(member)
    is_ptr = is_ptr_variant_union(u)
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
    for n, t in lc.func.params:
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
        if e.name in lc.forbidden_reads:
            raise ThirUnsupported("stmt.match")
        if e.name not in declared:
            raise ThirUnsupported("name.global_read", detail=True)
        binding_type = _param_declared_type(e.name, lc)
        if binding_type is None:
            binding_type = declared.get(e.name)
        unrouted = _unrouted_binding_read(binding_type, analyzer)
        if unrouted is not None and not allow_unrouted_name:
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
            # The method receiver -> `this` (plain method) or `__self` (a
            # resumable method coro's `Record&` frame field). Reached as a
            # field-access / method-call receiver and as a record call-arg
            # (whose tail retags deref for the `(*this)` render), so its
            # form tag is informational.
            _witness("self.this")
            return THIRSelf(result_type=rtype, form=Form.BORROW,
                            cpp=lc.self_cpp, loc=loc)
        gcpp = lc.prescan.global_cpp.get(e.name)
        if gcpp is not None:
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
            container_ptr = (is_list(bt) or is_dict(bt) or is_set(bt)
                             or is_array(bt))
        return THIRName(result_type=rtype, name=e.name, cpp=gcpp, form=form,
                        deref=e.name in lc.frame_slots or container_ptr,
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
                    or (allow_whole_optional
                        and (_value_opt_scalar(rtype, analyzer) is not None
                             or _value_opt_owned_view(rtype, analyzer)
                             is not None)
                        and _witness("field.whole_optional")))
                if not result_ok:
                    raise ThirUnsupported("field.result_type", detail=True)
                if not (
                        _field_receiver_ok(e, declared, analyzer)
                        or _ptr_value_field_recv_ok(e, declared, analyzer)
                        or _user_deref_field_recv_ok(
                            e, declared, lc.narrow.narrowed, analyzer,
                            lc.pointers)
                        or _optional_checked_field(e, declared, analyzer)
                        or _field_over_subscript_ok(e, declared, analyzer)
                        or _optional_field_over_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_container_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_field_ok(e, declared, analyzer)
                        or _field_over_global_record_ok(
                            e, declared, analyzer)
                        or _field_over_call_ok(e, analyzer)
                        or _assign_narrowed_union_recv(e.obj, declared, lc)
                        is not None):
                    raise ThirUnsupported("field.receiver_shape", detail=True)
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
            sub = _lower_expr(e.obj, lc, declared, subscript_prechecked=True)
            recv = (THIRFormConvert(result_type=sub.result_type, value=sub,
                                    form=Form.BORROW, loc=loc)
                    if sub.form is Form.STORAGE else sub)
            return THIRFieldAccess(
                result_type=rtype, receiver=recv,
                field_cpp=_field_cpp(e), deref_check=True, loc=loc)
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
        # plain-assign targets and the print_optional_val wrap strip the flag.
        narrowed_opt = _narrowed_opt_field_read(e, rtype, declared, analyzer)
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
                # read bare with an arrow (`time->x`, is_indirect_name); the
                # name-read arm never sees it (not in `declared`).
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
            # `r.__deref__()...x` (N = deref_depth). Bare `.` receiver access.
            _witness("field.user_deref_chain")
            return THIRFieldAccess(
                result_type=rtype,
                receiver=_lower_expr(
                    e.obj, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                field_cpp=_field_cpp(e),
                deref_chain=e.deref_depth,
                narrowed_deref=narrowed_opt,
                form=_viewfam_result_form(fa_str),
                loc=loc,
            )
        return THIRFieldAccess(
            result_type=rtype,
            receiver=_lower_expr(
                e.obj, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER),
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
        if not subscript_prechecked:
            rec_key = _record_getitem_key(
                analyzer.get_expr_type(e.obj), analyzer)
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
                          or _eligible_ptr_value(rtype, analyzer))
                idx_type = analyzer.get_expr_type(e.index)
                idx_ok = ((_resolved_scalar(idx_type, analyzer)
                           and (not _runtime_bigint(idx_type, analyzer)
                                or _bigint_index_disposition(
                                       e.index, analyzer.get_expr_type(e.obj),
                                       analyzer) == "bare"))
                          or _resolved_str_value(idx_type, analyzer) is not None)
                recv_ok = (
                    (isinstance(e.obj, TpyName) and e.obj.name in declared
                     and e.obj.name not in lc.pointers)
                    or (isinstance(e.obj, TpyFieldAccess)
                        and _field_receiver_ok(e.obj, declared, analyzer)))
                if not (ret_ok and idx_ok and recv_ok):
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
            index_ok = (
                _bigint_index_disposition(e.index, analyzer.get_expr_type(e.obj),
                                          analyzer) != "reject")
            ret_ok = (
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
                and (_container_value_leaf_read(recv_t, analyzer)
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
            if not (container_ok or str_ok or bytes_ok or nested_ok
                    or record_recv_ok or tuple_elem_recv):
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
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(
                e.obj, lc, declared,
                field_prechecked=isinstance(e.obj, TpyFieldAccess),
                subscript_prechecked=isinstance(e.obj, TpySubscript)),
            index=_narrow_bigint_index(_lower_expr(e.index, lc, declared), e.index,
                                       analyzer.get_expr_type(e.obj),
                                       analyzer, loc),
            bounds_safe=e.bounds_safe,
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
        return _lower_binop(e, rtype, lc, declared, loc)
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
                    operand = _lower_expr(e.operand, lc, declared)
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
        if all(ExpressionGenerator._is_simple_expr(c)
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
    if isinstance(e, TpyIfExpr):
        bytes_rt = _resolved_bytes_value(rtype, analyzer)
        if not (_resolved_scalar(rtype, analyzer) or _eligible_char(rtype)
                or _eligible_enum(rtype, analyzer) is not None
                or _resolved_str_value(rtype, analyzer) is not None
                or _is_string_owned(rtype)
                # A bytes-family ternary (`a if a is not None else b`); the
                # both-view arm shape is the only one lowered (see
                # `_lower_if_expr`), mixed/owned arms defer there.
                or bytes_rt is not None):
            note_detail("ifexpr.result_type")
            raise ThirUnsupported("expr.ifexpr")
        return _lower_if_expr(e, rtype, lc, declared, loc)
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
        if not _call_use_supported(e, lc, declared, use,
                                   allow_whole_optional=allow_whole_optional):
            raise ThirUnsupported("expr.call")
        if e.macro_expansion is not None:
            # `@call_macro` / getattr / hasattr: the AST renders the
            # sema-synthesized replacement in place (gen_expr's macro arm), so
            # the call node lowers to its expansion.
            _witness("call.macro_expansion")
            return _lower_expr(e.macro_expansion, lc, declared)
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
                        or _protocol_arg_slot(p.type) is not None)
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
            if (not e.args and not e.kwargs and e.double_star_unpack is None
                    and e.subscript_callee is None and not e.type_args
                    and fam is not None and _storage_call_container(fam)):
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
                        note_detail("call.inst_arg_lastuse")
                        raise ThirUnsupported("expr.call")
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
            if not (scalar_ctor or slice_ctor or owned_str_ctor
                    or _resolved_viewfam_value(rtype, analyzer) is not None):
                note_detail("call.type_ctor.result_kind")
                raise ThirUnsupported("expr.call")
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
                if owned_str_ctor and not _str_pass_through_arg(
                        a, p.type, declared, analyzer):
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
        k = _free_callee_kind(
            e, analyzer,
            generator_ok=use.result is _ExprResultUse.ITERABLE,
            error_return_ok=True,
            coro_factory_ok=use.coro_factory)
        len_call = _is_len_call(e, declared, analyzer)
        if not len_call:
            if k is None or fi is None:
                raise ThirUnsupported("expr.call")
            if not _call_arity_ok(e, fi):
                note_detail("call.arity_defaults")
                raise ThirUnsupported("expr.call")
            if _strlit_overload_pin_fires(e, fi, analyzer):
                # A str literal into a str/StrView slot of a multi-overload
                # callee takes gen_call_arg's `param_view_t("...")` pin, a
                # spelling the arg lowering does not reproduce. A str literal
                # into ANY other slot (a `Literal[...]` mode selector, `Char`,
                # `String`) renders through its own arm and routes.
                note_detail("call.strlit_overload_pin")
                raise ThirUnsupported("expr.call")
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
        dcbp = fi.deep_const_borrow_params if fi is not None else None
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
                    elem_cpp = lc.render_type(slot)
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
                    retype_scalars=retype)
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
                              and e.obj.name in lc.pointers),
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
            mk = _marker_call_kind(e, analyzer, generator_ok=iterable_gen)
            # An F1-record result renders bare under a postfix member
            # (RECEIVER) and, when it is an RVALUE source, directly into the
            # owned-record decl / storage slot (`Rc<A> r = Rc.new_(...);`) --
            # the free-call value-position mirror. A borrow-returning callee
            # is not an rvalue source and stays AST (the decl sink copies it
            # through machinery this arm does not mirror).
            record_ret = (result_use is _ExprResultUse.RECEIVER
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
                        temp_args=temp_args)
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
            if fi is None or not _plain_method_fi_ok(
                    fi, property_getter_ok=True, property_setter_ok=True,
                    coro_factory_ok=use.coro_factory):
                note_detail("method.fi_kind")
                raise ThirUnsupported("expr.method_call")
            if not _call_arity_ok(e, fi):
                note_detail("method.arity_defaults")
                raise ThirUnsupported("expr.method_call")

            recv_type = _method_receiver_type(e.obj, declared, analyzer)
            stmt_position = result_use is _ExprResultUse.DISCARD
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
                    storage_ret_ok=storage_ret_ok)
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
                        or use.ctx_manager),
                    storage_ret_ok=storage_ret_ok,
                    coro_factory_ok=use.coro_factory,
                    suspend_ok=(result_use is _ExprResultUse.SUSPEND),
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
            if temp_args:
                ut = _value_union_temp_slot(a, ptype, declared, lc.analyzer)
                if ut is not None and not (isinstance(a, TpyName)
                                           and (a.name in lc.narrow.narrowed
                                                or a.name in lc.inline_narrowed)):
                    _witness("argtemp.value_union_method")
                    return THIRArgTemp(result_type=ut, cpp_type=ut.to_cpp(),
                                       init=_lower_expr(a, lc, declared), form=Form.VALUE,
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
            return _lower_call_arg(a, ptype, lc, declared,
                                   temp_args=own_flush,
                                   method_arg=not proto_recv,
                                   method_arg_stub=stub_recv and not proto_recv)

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
        return THIRMethodCall(
            result_type=rtype if rtype is not None else VoidType(),
            receiver=_lower_expr(
                e.obj, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                field_prechecked=isinstance(e.obj, TpyFieldAccess),
                subscript_prechecked=isinstance(e.obj, TpySubscript)),
            method_cpp=member,
            args=tuple(
                _method_arg(a, params[i].type if params else None, i)
                for i, a in enumerate(e.args)),
            native_function_name=fi.native_name if fi.native_function else None,
            cpp_template=fi.cpp_template,
            method_targs_cpp=method_targs,
            is_arrow=not deref_check and isinstance(e.obj, TpyName)
                     and (e.obj.name in lc.pointers
                          or (e.obj.name == lc.self_receiver
                              and lc.self_is_pointer)),
            deref_check=deref_check,
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
            inner = _lower_expr(
                e.expr, lc, declared,
                field_owned_str_ok=(disp == "materialize"
                                    and isinstance(e.expr, TpyFieldAccess)))
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


def _lower_lambda(e: TpyLambda, lc: '_LowerCtx',
                  declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a lambda expression to `_gen_lambda`'s C++ closure. Params,
    the capture list, and the trailing return type spell EXACTLY like the AST's
    `_gen_lambda` (same helpers); the single-expression body is the AST's
    `gen_expr(body, ret_type)`. `captures_by_value` (Callable/std::function --
    escaping) picks the `_callable_param_cpp` param spelling and a by-value
    capture list; the default (Fn template) uses `to_cpp_param` and a
    by-reference capture. Deferred to AST: the readonly-param (key-function)
    spelling and the pointer-repr tuple return arm."""
    analyzer = lc.analyzer
    loc = getattr(e, "loc", None)
    if not _lambda_routable(e, analyzer):
        raise ThirUnsupported("expr.lambda")
    ret_type = e.inferred_return_type
    params_cpp: list[str] = []
    body_declared = dict(declared)
    for pname, ptype in zip(e.param_names, e.inferred_param_types):
        cpp_name = escape_cpp_name(pname)
        if e.captures_by_value:
            params_cpp.append(
                f"{CallableType._callable_param_cpp(ptype)} {cpp_name}")
        else:
            params_cpp.append(ptype.to_cpp_param(cpp_name))
        body_declared[pname] = ptype
    if e.captured_names:
        prefix = "" if e.captures_by_value else "&"
        capture = "[" + ", ".join(
            f"{prefix}{escape_cpp_name(n)}" for n in e.captured_names) + "]"
    else:
        capture = "[]"
    body = _lower_expr(e.body, lc, body_declared, target_type=ret_type)
    _witness("expr.lambda")
    return THIRLambda(
        result_type=analyzer.get_expr_type(e),
        capture_cpp=capture,
        params_cpp=tuple(params_cpp),
        body=body,
        ret_cpp=ret_type.to_cpp(),
        loc=loc,
    )

def _lower_dyn_getattr_call(e: TpyFieldAccess, rtype: 'TpyType | None',
                            lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The sema-synthesized `obj.__getattr__("name")` behind a dynamic-attr
    read (`obj.x`, D16) -- the value-position mirror of the dyn-setattr write
    arm: the same bare non-pointer F1-record receiver name, literal name arg,
    and single-overload plain user method, lowered as the plain method call
    (`_gen_field_access` delegates to `_gen_method_call`; the post-process
    chain is identity in this slice per `_plain_method_fi_ok`)."""
    analyzer = lc.analyzer
    call = e.dyn_getattr_call
    loc = getattr(e, "loc", None)
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
    if not isinstance(name_arg, TpyStrLiteral):
        raise ThirUnsupported("getattr.name_shape", detail=True)
    _witness("method.dyn_getattr")
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

def _lower_class_constant(e: TpyFieldAccess, rtype: 'TpyType | None',
                          lc: '_LowerCtx', declared: dict[str, TpyType],
                          loc, *, tuple_ok: bool = False) -> THIRExpr:
    """A class-constant read (`class_constant_owner` set) -> the bare
    qualified static, spelled at lowering like THIREnumMember. Only the
    receiver_eval-None shapes route (name / static-type-chain receiver, no
    runtime Optional check); the statement-expression wrapper shapes reject.
    Result families are the value leaves plus str/bytes views (a constant is
    a static scalar / string_view -- never an owned str), so every admitted
    sink lands the bare name; a tuple/container constant's consumers wrap
    it in renders this arm does not pin, so those reject."""
    analyzer = lc.analyzer
    if not _class_const_pure_receiver(e, declared, analyzer):
        raise ThirUnsupported("field.class_const_receiver", detail=True)
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
        suppress_move: bool = False) -> THIRExpr:
    if not _container_lit_elem_ok(
            e, slot, declared, lc.analyzer, threaded=threaded, forced=forced,
            allow_record=allow_record, allow_nested=allow_nested,
            allow_optional=allow_optional):
        raise ThirUnsupported("expr.container_literal")
    return _lower_container_elem(
        e, slot, lc, declared, retype_scalars=retype_scalars,
        suppress_move=suppress_move)


def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx', declared: dict[str, TpyType], *,
                          retype_scalars: bool = True,
                          suppress_move: bool = False,
                          field_str_ok: bool = False) -> THIRExpr:
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
        # helper (mirrors the MIL pointer-repr-tuple field write). SYNC
        # positions only: the resumable frame-emplace position spells the
        # element BARE on the AST path (typed_brace_init, no per-element
        # wrap) -- a merge-caught divergence between the sync cell and the
        # resumable loop routing; the frame flavor is its own rung.
        su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot))) \
            if slot is not None else None
        if lc.resumable_leaf_mode and isinstance(su, TupleType):
            raise ThirUnsupported("expr.tuple_literal.frame_elem")
        if isinstance(su, TupleType) and len(e.elements) == len(su.element_types):
            inner = THIRTupleLiteral(
                result_type=su,
                elements=tuple(
                    _lower_container_elem(e.elements[i], su.element_types[i],
                                          lc, declared)
                    for i in range(len(e.elements))),
                loc=getattr(e, "loc", None))
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
            and e.name in lc.pointers):
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
                                rvalue_ok: bool = False) -> THIRExpr:
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
        if (isinstance(et_bare, (OptionalType, UnionType, TypeParamRef))
                or et_bare.value_form() is not ValueForm.BORROW_REF
                or not TupleType._element_is_pointer_repr(et_bare)):
            note_detail("btuple.elem_slot")
            raise ThirUnsupported("expr.tuple_literal")
        elem = e.elements[i]
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
                et_bare)))
            parts.append(f"const {ptr_base}*"
                         if mode == TupleElemCapture.CONST_REF
                         else f"{ptr_base}*")
            src_parts.append(ptr_base)
            lowered.append(_lower_expr(
                elem, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE)))
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
            bound.append(not ExpressionGenerator._is_simple_expr(all_operands[0]))
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
        if not _generic_plain_arg_ok(
                a, ptype, subst, declared, analyzer, temps_ok=temp_args):
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
        else:
            args.append(_lower_call_arg(
                a, resolved, lc, declared, temp_args=temp_args,
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
    temp and the variant lifts its address (`pv{&__tmp_N}`). Temp-hoisting,
    so the caller admits it only under `temp_args`."""
    slot = _arg_ptr_union_slot(ptype, analyzer)
    if slot is None:
        return False
    ut, _deep_const = slot
    if not isinstance(a, TpyCall) or not _ctor_shape_ok(a, analyzer):
        return False
    if not is_rvalue_source(analyzer, a):
        return False
    at = analyzer.get_expr_type(a)
    return any(at == m for m in ut.members if not is_void_like_type(m))


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
        else:
            ok = _plain_call_arg_ok(
                a, ptype, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed))
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
    if (kind is not None and kind[0] in ("native", "native_c", "template")
            and isinstance(a, (TpyCall, TpyMethodCall))
            and _native_container_call_arg(a, ptype, analyzer)):
        # A container-returning call rvalue bound bare by the native slot
        # (`::tpy::__len__(g.get())` / `::tpy::sorted(ml.get())`): STORAGE
        # use so the inner call's result gate admits the container -- the
        # print wrap-call path's twin.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
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


def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                    declared: dict[str, TpyType], *, temp_args: bool = False,
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
                    rvalue_ok=True)
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
            and _opt_view_arg_shim(_param_declared_type(a.name, lc), ptype,
                                   lc.analyzer)):
        # A value-repr Optional[str] name into another value-repr Optional[str]
        # slot takes `_maybe_convert_opt_view_param`'s ARG split on the WHOLE
        # optional (narrowed or not): the borrow `optional<string_view>` binding
        # -> the owned `optional<string>` the slot needs (`s ? std::make_optional(
        # std::string(*s)) : std::nullopt`). The family match (str inner, not the
        # bare `StrView` spelling) is pinned by `_opt_view_arg_shim`.
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
            and _protocol_union_ctor_arg(a, ptype, declared, lc.analyzer)
            == "addr"):
        # A Span name into an all-protocols union ctor slot: the address-of
        # lift (`&(s)` -- the Spannable overload binds the pointer).
        return THIROptionalPtrArg(
            result_type=ptype, value=_lower_expr(a, lc, declared),
            addr_of=True, form=Form.BORROW, loc=getattr(a, "loc", None))
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
            and _native_iterable_literal_arg(a, ptype, lc.analyzer)):
        # A container literal into a native Iterable/Sequence slot: the
        # RESOLVED container renders inline (aggregate std::array / spelled
        # ordered_set), bare into the template slot. Only the native arg
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
                            use=_ExprUse(coro_factory=True))
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
        at = lc.analyzer.get_expr_type(a)
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
            init = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
            if isinstance(a, TpyName) and a.name in lc.pointers:
                assert isinstance(init, THIRName)
                init = replace(init, deref=True)
            _witness("argtemp.protocol")
            return THIRArgTemp(result_type=proto, cpp_type=cpp_type,
                               init=init, brace_init=brace_init,
                               form=Form.BORROW, loc=getattr(a, "loc", None))
    if temp_args:
        rec_pt = _record_rvalue_temp_slot(a, ptype, lc.analyzer,
                                          frame_capturing=frame_capturing)
        if rec_pt is not None:
            _witness("argtemp.record_rvalue")
            return THIRArgTemp(
                result_type=rec_pt, cpp_type=rec_pt.to_cpp(),
                init=_lower_expr(a, lc, declared, use=_RECORD_TEMP_FLUSH_USE),
                form=Form.BORROW,
                loc=getattr(a, "loc", None))
        ut = _value_union_temp_slot(a, ptype, declared, lc.analyzer)
        if ut is not None and not (isinstance(a, TpyName)
                                   and (a.name in lc.narrow.narrowed
                                        or a.name in lc.inline_narrowed)):
            _witness("argtemp.value_union")
            return THIRArgTemp(
                result_type=ut, cpp_type=ut.to_cpp(),
                init=_lower_expr(a, lc, declared, use=_NESTED_ARG_USE),
                form=Form.VALUE,
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
    crec = copy_plain_record_source(a, lc.analyzer, lc.pointers)
    if crec is not None:
        w = _plain_own_slot(ptype)
        if w is not None and crec == w:
            return THIRCopy(
                result_type=w,
                value=_lower_expr(a.args[0], lc, declared,
                                  use=_NESTED_ARG_USE),
                cpp_type=lc.render_type(crec), form=Form.STORAGE,
                loc=getattr(a, "loc", None))
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
        at = lc.analyzer.get_expr_type(a)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if isinstance(at, OwnType):
            at = unwrap_readonly(at.wrapped)
        if (at is not None and not at.is_value_type()
                and _is_move_source(a, lc)):
            lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
            if isinstance(a, TpyName) and a.name in lc.pointers:
                assert isinstance(lowered, THIRName)
                lowered = replace(lowered, deref=True)
            _witness("move.own_last_use")
            return THIRMove(result_type=ow, value=lowered, form=own_form,
                            loc=getattr(a, "loc", None))
        if temp_args:
            lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
            if isinstance(a, TpyName) and a.name in lc.pointers:
                assert isinstance(lowered, THIRName)
                lowered = replace(lowered, deref=True)
            _witness("argtemp.own_copy")
            return THIRArgTemp(result_type=ow, init=lowered, move=True,
                               form=own_form, loc=getattr(a, "loc", None))
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
        lowered = _lower_expr(a, lc, declared)
        if method_arg_stub and _is_move_source(a, lc):
            at = lc.analyzer.get_expr_type(a)
            if isinstance(at, OwnType):
                at = at.wrapped
            rec = lc.analyzer.registry.get_record_for_type(at)
            citer = next(
                (fi2 for fi2 in (rec.get_method_overloads("__iter__")
                                 if rec is not None else [])
                 if fi2.is_consuming), None)
            if citer is not None:
                if not citer.native_name:
                    # A consuming __iter__ without a native symbol spells
                    # `std::move(x).__iter__()` -- not mirrored.
                    raise ThirUnsupported("expr.call")
                _witness("call.own_iter_arg")
                return THIRCall(
                    result_type=lc.analyzer.get_expr_type(a) or VoidType(),
                    callee="__iter__",
                    cpp_template=(
                        f"{qualify_native_name(citer.native_name)}"
                        "(std::move({0}))"),
                    args=(lowered,),
                    loc=getattr(a, "loc", None))
        return lowered
    if (it_pb is not None and it_pb.name in ("Iterable", "Sequence")
            and isinstance(a, (TpyCall, TpyMethodCall))):
        # A container-returning call rvalue bound bare by the Iterable slot
        # (`a.extend(copy(b))` / `a.extend(make_nodes())`): STORAGE use so
        # the inner call's result gate admits the container.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
    ow_slot = _plain_own_slot(ptype) if isinstance(a, TpyMethodCall) else None
    if ow_slot is not None and _f1_record(ow_slot, lc.analyzer):
        # An Own-slot record METHOD-call rvalue (admitted by
        # _own_record_rvalue_arg -- `Arc.new(Mutex.new(0))`): the inline
        # rvalue binds the T&& slot; lower under BORROW_BIND like the
        # owned-record decl's method row, whose record-result gate this
        # position shares.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
    if (ow_slot is not None
            and _storage_call_ret(
                unwrap_readonly(unwrap_send_sync(ow_slot)),
                lc.analyzer) is not None):
        # The container sibling (`g.set(acked.copy())`): STORAGE use, the
        # container-returning method result's storage sink.
        return _lower_expr(a, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.STORAGE))
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
            return THIRArgTemp(result_type=inner, cpp_type=inner.to_cpp(),
                               init=_lower_expr(
                                   a, lc, declared, use=_NESTED_ARG_USE),
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
            return _lower_expr(
                a, lc, declared,
                use=_NESTED_ARG_USE)  # already `T*` -- bare, no deref retag
        else:  # 'name': a plain record lvalue takes the address-of
            _witness("optptr.name")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lower_expr(
                                          a, lc, declared,
                                          use=_NESTED_ARG_USE),
                                      addr_of=True,
                                      loc=loc)
    if isinstance(a, TpyName) and a.name in lc.pointers:
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
    lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE,
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
                or _protocol_arg_slot(p.type) is not None)
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
        deref=a.name in lc.pointers or (a.name == lc.self_receiver
                                        and lc.self_is_pointer),
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

def _lower_truthy(e: TpyExpr, lc: '_LowerCtx',
                  declared: dict[str, TpyType], *,
                  unary_operand: bool = False) -> THIRExpr:
    """Lower one Python-truthiness position without condition temps."""
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                      TpyBytesLiteral, TpyNoneLiteral)):
        raise ThirUnsupported("truthy.literal")
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
    if wrap is not None and not isinstance(e, (TpyName, TpyFieldAccess)):
        raise ThirUnsupported("truthy.enum_shape")
    mode = _truthiness_mode(et, lc.analyzer)
    if (mode is TruthinessMode.IS_TRUTHY
            and isinstance(e, TpyFieldAccess)):
        # A truthy Optional field narrows its dotted path for later reads. THIR
        # does not carry that path fact yet, so routing the condition alone can
        # drop the AST's `(*field)` unwrap in the branch.
        raise ThirUnsupported("truthy.optional_field_narrow")
    if mode is TruthinessMode.ALWAYS_TRUE and not isinstance(e, TpyName):
        raise ThirUnsupported("truthy.constant_shape")
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
            # renders the bare call (`if (f(x))`). The call's own arms still
            # gate its callee/arg shapes; moded (non-bool) call operands stay
            # rejected pending a corpus witness. isinstance calls are
            # excluded: they belong to the narrowing machinery (in-branch
            # extraction aliases, statically-proven `if (true)` folds), not
            # the bare-call render.
            if (et is None or not is_bool_type(et)
                    or e.isinstance_var is not None
                    or e.isinstance_type is not None):
                raise ThirUnsupported("truthy.call_nonbool")
            _witness("cond.bool_call")
        elif isinstance(e, TpyIfExpr):
            if mode is None and (et is None or not is_bool_type(et)):
                raise ThirUnsupported("truthy.ifexpr_nonbool")
            _witness("ifexpr.cond_pos")
        elif not isinstance(e, (TpyUnaryOp, TpyChainedCompare)):
            raise ThirUnsupported("truthy.shape")
        operand = _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.TRUTHY),
            allow_whole_optional=mode is TruthinessMode.IS_TRUTHY,
            allow_unrouted_name=mode is TruthinessMode.IS_TRUTHY,
        )
        if (mode is TruthinessMode.IS_TRUTHY
                and isinstance(operand, THIRName)):
            operand = replace(operand, deref=False)
        if mode is None:
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
            operand=None if mode is TruthinessMode.ALWAYS_TRUE else operand,
            deref=deref,
            loc=getattr(e, "loc", None),
        )
    loc = getattr(e, "loc", None)
    if wrap == "true":
        _witness("enum.truthy_plain")
        return THIREnumWrap(result_type=BOOL, wrap=wrap, operand=None, loc=loc)
    _witness("enum.truthy_int")
    return THIREnumWrap(result_type=BOOL, wrap=wrap,
                        operand=_lower_expr(
                            e, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.CONDITION)),
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
        pt = next((t for n, t in lc.func.params if n == e.name), None)
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
        pt = next((t for n, t in lc.func.params if n == e.name), None)
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
                   declared: dict[str, TpyType], loc) -> THIRIfExpr:
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
    cond = _lower_truthy(e.condition, lc, declared)
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
    rtype = lc.analyzer.get_expr_type(e)
    narrowed_opt = _narrowed_opt_field_read(e, rtype, declared, lc.analyzer)
    if narrowed_opt:
        _witness("field.narrowed_deref")
    return THIRFieldAccess(
        result_type=rtype,
        receiver=_lower_expr(
            e.obj, lc, declared,
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
