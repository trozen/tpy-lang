"""Expression lowering and admission for `_lower_expr` and its recursive arms.

Composite consumer-shape helpers live in `checks.py`; `_lower_expr` invokes them
only from the node arm being lowered.
"""

from __future__ import annotations
import math
from dataclasses import field, replace
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
    TpyDictLiteral,
    TpyExpr,
    TpyGeneratorExpression,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyFString,
    TpyIfExpr,
    TpyIntLiteral,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TupleElemCapture,
    TpyTupleLiteral,
    TpyUnaryOp,
)
from ...typesys import (
    BOOL,
    CHAR,
    INT32,
    IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType,
    OwnType,
    TpyType,
    TupleType,
    TypeParamRef,
    ValueForm,
    VoidType,
    contains_type_param,
    is_void_like_type,
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
    is_str_type,
    is_str_view_type,
    is_string_type,
    is_set,
)
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.context import (
    enum_cpp_name,
    escape_cpp_name,
    qualified_cpp_name,
    view_key_target,
)
from ...compilation_context import get_current_compiler
from ..fallback import ThirUnsupported, expr_kind_tag, note_detail
from ..faces import witness as _witness
from ...codegen_cpp.expressions import ExpressionGenerator
from ..nodes import (
    Form,
    THIRArgTemp,
    THIRBinOp,
    THIRChainedCompareStmtExpr,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRCoerce,
    THIRContainerLiteral,
    THIRCtorCall,
    THIREnumMember,
    THIREnumWrap,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRIfExpr,
    THIRFString,
    THIRFStringArg,
    THIRIsNone,
    THIRMembership,
    THIROptTruthy,
    THIROptViewArg,
    THIRLiteral,
    THIRMethodCall,
    THIRMove,
    THIRName,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRSelf,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRTupleLiteral,
    THIRUnaryNot,
    THIRUnionArgLift,
)
from .predicates import (
    _BIGINT_INDEX_NARROW_WRAP,
    _BIGINT_LIT_COERCION,
    _BIGINT_NARROW,
    _ARITH_OPS,
    _BITWISE_OPS,
    _COMPARE_OPS,
    _FLOAT32_LIT_COERCION,
    _IS_OPS,
    _LOGICAL_OPS,
    _MEMBERSHIP_OPS,
    _arg_ptr_union_slot,
    _bigint_index_disposition,
    _binop_operand_casts,
    _bytes_compare_operand,
    _bytes_concat_operand,
    _char_compare_operand,
    _bytes_name_form,
    _coerce_disposition,
    _coerce_wrap,
    _container_nocopy_elem,
    _container_record_elem,
    _container_scalar_read,
    _container_value_leaf_read,
    _const_index,
    _ctor_arg_slot_ok,
    _dict_view_iterable_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_value,
    _eligible_scalar,
    _enum_compare_pair,
    _enum_member_cpp,
    _enum_neg_wrap,
    _enum_prop_wrap,
    _enum_truthy_wrap,
    _reject_nonbare_truthy,
    _f1_record,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _folded_neg_int_literal,
    _generic_root_subst,
    _instantiation_call_fi,
    _is_borrow_form_name,
    _is_type_param_slot,
    _is_range_call,
    _is_none_compare_operand,
    _is_string_owned,
    _mixed_sign_compare,
    _nonvalue_container_ret,
    _narrow_bigint_index,
    _optional_ptr_arg_face,
    _optional_ptr_arg_slot,
    _optional_ptr_borrow_name,
    _optional_checked_field,
    _optional_field_over_subscript_ok,
    _own_lvalue_temp_slot,
    _operand_type,
    _peel_coerce,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_own_slot,
    _protocol_arg_slot,
    _protocol_arg_temp,
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
    _str_compare_operand,
    _str_concat_operand,
    _str_field_value_read,
    _str_name_form,
    _storage_call_container,
    _storage_call_ret,
    _subscript_index_and_tuple,
    _subscript_container_recv_type,
    _template_init_call_fi,
    _tuple_subscript_value_read,
    _tparam_value,
    _opt_view_arg_shim,
    _none_value_opt_arg,
    _callable_value,
    _value_opt_scalar,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_view,
    _value_tuple,
    _value_tuple_return,
    _value_union_temp_slot,
    _union_binding_divergent,
    _union_compare_pair,
    _unrouted_binding_read,
    _unwrap_lit_coerce,
    _value_opt_view_name,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx, _RecordCtorUse
from .generics import expand_fi_template


_NESTED_ARG_USE = _ExprUse(record_ctor=_RecordCtorUse.NESTED_ARG)
_RECORD_TEMP_USE = _ExprUse(record_ctor=_RecordCtorUse.RECORD_TEMP)


from .checks import (
    _FSTRING_INELIGIBLE,
    _call_arity_ok,
    _call_ret_reject,
    _container_lit_elem_ok,
    _container_lit_slot_family,
    _container_literal_shape_ok,
    _container_method_call_supported,
    _ctor_instantiation_ok,
    _ctor_shape_ok,
    _field_over_container_subscript_ok,
    _field_over_field_ok,
    _free_callee_kind,
    _fstring_arg_wrap,
    _generic_plain_arg_ok,
    _is_len_call,
    _is_len_native,
    _marker_call_kind,
    _marker_call_supported,
    _marker_reject,
    _method_call_arg_ok,
    _method_nonname_receiver_ok,
    _method_receiver_type,
    _native_call_arg_ok,
    _optional_ptr_arg,
    _plain_call_arg_ok,
    _ptr_deref_method_call,
    _ptr_deref_recv_ok,
    _protocol_method_call_supported,
    _record_rvalue_call_shape,
    _record_rvalue_temp_arg,
    _shared_pass_through_arg,
    _str_pass_through_arg,
    _subscript_elem_reject,
    _subscript_recv_reject,
    _str_aug_append_ok,
    _str_list_method_iterable_ok,
    _record_method_call_supported,
    _recv_shape_reject,
    _view_method_call_supported,
    _value_tuple_pass_through_arg,
    _value_union_temp_arg,
)


def _call_use_supported(e: TpyCall, lc: '_LowerCtx',
                        declared: dict[str, TpyType], use: _ExprUse) -> bool:
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
              # A value-repr Optional[scalar] result lands bare in its
              # value-optional slot (`r = h(true);`); mismatched consumers
              # reject at their own slot arms.
              or _value_opt_scalar(ret, analyzer) is not None
              or (result is _ExprResultUse.DISCARD
                  and is_void_like_type(ret))
              or (result is _ExprResultUse.ITERABLE
                  and _nonvalue_container_ret(ret))
              or (result is _ExprResultUse.STORAGE
                  and _storage_call_ret(ret, analyzer) is not None)
              or (result is _ExprResultUse.BORROW_BIND
                  and _f1_record(record, analyzer))
              or _record_rvalue_call_shape(e, analyzer))
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


def _record_ctor_arg_supported(
        arg: TpyExpr, param_type: TpyType, index: int, fi,
        lc: '_LowerCtx', declared: dict[str, TpyType], use: _ExprUse) -> bool:
    analyzer = lc.analyzer
    mutation_unknown = fi.mutated_params is None
    mutated = fi.mutated_params or frozenset()
    is_mutated = index in mutated
    if use.record_ctor is not _RecordCtorUse.NESTED_ARG:
        temps_ok = (use.allow_temps
                    if use.record_ctor is _RecordCtorUse.DIRECT else False)
        if _str_pass_through_arg(
                arg, param_type, declared, analyzer, mutated=is_mutated):
            _witness("ctor.str_arg")
            return True
        return (_shared_pass_through_arg(
                    arg, param_type, declared, analyzer, mutated=is_mutated)
                or _none_value_opt_arg(arg, param_type, analyzer) is not None
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
            param_names=lc.prescan.param_names):
        raise ThirUnsupported("expr.method_call")


def _lower_marker_method_arg(
        e: TpyMethodCall, a: TpyExpr, ptype: 'TpyType | None', index: int,
        lc: '_LowerCtx', declared: dict[str, TpyType], *,
        temp_args: bool) -> THIRExpr:
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

    rb = e.resolved_binop
    if e.op in _ARITH_OPS or e.op in _BITWISE_OPS:
        if rb is None or not getattr(rb.method, "cpp_template", None):
            if (rb is None or e.op != "+"
                    or not rb.method.native_function
                    or not rb.method.native_name):
                reject()
            bt = _resolved_bytes_value(rtype, analyzer)
            lt = _operand_type(e.left, declared, analyzer)
            rt = _operand_type(e.right, declared, analyzer)
            if (bt is None or not is_bytes_type(bt)
                    or not _bytes_concat_operand(e.left, lt, analyzer)
                    or not _bytes_concat_operand(e.right, rt, analyzer)):
                reject()
        elif e.op == "+" and _is_string_owned(rtype):
            lt = _operand_type(e.left, declared, analyzer)
            rt = _operand_type(e.right, declared, analyzer)
            if (not _str_concat_operand(e.left, lt, analyzer)
                    or not _str_concat_operand(e.right, rt, analyzer)):
                reject()
        elif not _resolved_scalar(rtype, analyzer):
            reject()
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
              and not isinstance(e.left, TpyName)
              and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
              and not isinstance(e.right, TpyName)):
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
        lt = _operand_type(e.left, declared, analyzer)
        rt = _operand_type(e.right, declared, analyzer)
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
                or _enum_compare_pair(e, lt, rt, analyzer)):
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
        if _is_none_compare_operand(e, declared, analyzer) is None:
            reject()
    elif e.op in _MEMBERSHIP_OPS:
        fi = e.resolved_contains
        if (fi is None or e.typed_dict_in_field is not None
                or fi.cpp_template or fi.native_function
                or not fi.native_name
                or not isinstance(e.right, TpyName)
                or e.right.name not in declared):
            reject()
        ct = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(declared[e.right.name])))
        lt = _operand_type(e.left, declared, analyzer)
        # A str needle renders bare into `contains(...)` on both paths
        # (literal / view name / owned local -- the container's transparent
        # lookup absorbs the form), exactly like a scalar needle. A VIEW-keyed
        # container (`set[StrView]`) threads view_key_target into the needle's
        # literal render (the static-storage pin) -- not mirrored, reject.
        if not ((is_dict(ct) or is_set(ct))
                and (_resolved_scalar(lt, analyzer)
                     or (_resolved_str_value(lt, analyzer) is not None
                         and view_key_target(ct) is None))):
            reject()
    else:
        reject()

    if e.op in _MEMBERSHIP_OPS:
        _witness("binop.membership")
        return THIRMembership(
            result_type=rtype,
            receiver=_lower_expr(e.right, lc, declared),
            needle=_lower_expr(e.left, lc, declared),
            method_cpp=e.resolved_contains.native_name,
            negate=e.op == "not in",
            loc=loc)
    if e.op in _IS_OPS:
        operand = e.right if isinstance(e.left, TpyNoneLiteral) else e.left
        # A value-repr Optional binding (param OR declared local -- e.g. a
        # try-hoisted `std::optional<T> r;` slot) None-tests via has_value;
        # pointer-repr bindings via `!= nullptr`.
        value_repr = (
            isinstance(operand, TpyName)
            and (_value_opt_scalar_param(operand.name, lc)
                 or _value_opt_view_param(operand.name, lc)
                 or (operand.name in declared
                     and _value_opt_scalar(declared[operand.name],
                                           lc.analyzer) is not None)))
        return THIRIsNone(
            result_type=rtype,
            operand=_lower_expr(
                operand, lc, declared, allow_whole_optional=True),
            negate=e.op == "is not",
            value_repr=value_repr,
            form=Form.VALUE,
            loc=loc)
    if e.op in _COMPARE_OPS:
        left = _lower_char_targeted(
            e.left, analyzer.get_expr_type(e.right), lc, declared)
        right = _lower_char_targeted(
            e.right, analyzer.get_expr_type(e.left), lc, declared)
    else:
        lslot, rslot = _rb_operand_slots(e.resolved_binop)
        left = _slot_literal_retype(
            _lower_expr(e.left, lc, declared), lslot)
        right = _slot_literal_retype(
            _lower_expr(e.right, lc, declared), rslot)
    bt = _resolved_bytes_value(rtype, analyzer)
    lcast, rcast = _binop_operand_casts(e, analyzer)
    return THIRBinOp(
        result_type=rtype,
        left=left,
        op=e.op,
        right=right,
        resolved=e.resolved_binop,
        divisor_non_zero=e.divisor_non_zero,
        left_cast=lcast,
        right_cast=rcast,
        form=(Form.STORAGE if _is_string_owned(rtype)
              or (bt is not None and is_bytes_type(bt)) else Form.VALUE),
        loc=loc)

def _value_opt_scalar_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is a value-repr `Optional[cheap scalar]` param
    (`std::optional<T>`) of the function being lowered -- the binding whose
    reads render bare (un-narrowed) / `(*p)` (narrowed) and whose None-test
    and truthiness carry the value-repr renders."""
    for n, t in lc.func.params:
        if n == name:
            return _value_opt_scalar(t, lc.analyzer) is not None
    return False

def _value_opt_view_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is a value-repr `Optional[view]` param -- str OR bytes
    (`std::optional<std::string_view>` / `std::optional<std::span<const
    uint8_t>>`) -- of the function being lowered, the view twin of
    `_value_opt_scalar_param`. A narrowed read unwraps `(*x)` (a borrow view),
    the None-test/truthiness carry the same value-repr renders, and a pass into
    another same-family `Optional[view]` slot takes the arg-split shim."""
    return _value_opt_view(_param_declared_type(name, lc), lc.analyzer) is not None

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
                field_prechecked: bool = False,
                field_owned_str_ok: bool = False,
                subscript_prechecked: bool = False,
                container_threaded: bool = True,
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
        if (not allow_whole_optional
                and isinstance(unwrap_readonly(analyzer.get_expr_type(e)),
                               OptionalType)):
            if _value_opt_scalar_name(e, declared, analyzer) is not None:
                raise ThirUnsupported(
                    "name.optval_unproven_read", detail=True)
            if _value_opt_view_name(e, declared, analyzer) is not None:
                raise ThirUnsupported(
                    "name.optstr_unproven_read", detail=True)
        if (e.name not in lc.inline_narrowed
                and _union_binding_divergent(e, declared, analyzer)):
            raise ThirUnsupported("name.union_binding_divergent", detail=True)
        if e.name == lc.self_receiver:
            # The method receiver -> `this` (plain method) or `__self` (a
            # resumable method coro's `Record&` frame field). Only ever
            # reached as a field-access / method-call receiver (other `self`
            # positions are gated out), so its form tag is informational.
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
        if _value_opt_scalar_param(e.name, lc):
            # A value-repr Optional[scalar] param (`std::optional<T>`). An
            # UN-narrowed read renders the bare optional (`p`, into an optional
            # slot); a NARROWED read (sema retyped it to the inner scalar,
            # `rtype` no longer Optional) unwraps `(*p)` -- gen_expr_deref's
            # value-optional deref. The bare-target positions where the AST does
            # NOT deref a narrowed read (a value-optional call slot) strip this
            # deref at the arg boundary (_lower_call_arg).
            narrowed = not isinstance(unwrap_readonly(rtype), OptionalType)
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=Form.VALUE, deref=narrowed, loc=loc)
        if _value_opt_view_param(e.name, lc):
            # A value-repr Optional[view] param -- str
            # (`std::optional<std::string_view>`) or bytes
            # (`std::optional<std::span<const uint8_t>>`).
            # A NARROWED read (sema retyped it to the inner view, `rtype` no
            # longer Optional) unwraps `(*s)` -- a BORROW-form view, so an
            # owned sink still gets the family copy (`std::string`/`bytes_copy`).
            # An UN-narrowed read stays the bare whole optional (VALUE), reached
            # only inside the None-test / truthiness / arg-shim wrappers (the bare
            # value position is unsupported). This must precede the str-name arm
            # below, which keys on the narrowed view rtype and would drop the deref.
            narrowed = not isinstance(unwrap_readonly(rtype), OptionalType)
            return THIRName(result_type=rtype, name=e.name, cpp=gcpp,
                            form=Form.BORROW if narrowed else Form.VALUE,
                            deref=narrowed, loc=loc)
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
        return THIRName(result_type=rtype, name=e.name, cpp=gcpp, form=form,
                        deref=e.name in lc.frame_slots, loc=loc)
    if isinstance(e, TpyFieldAccess):
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
                    or (field_owned_str_ok
                        and _str_field_value_read(e, declared, analyzer)
                        and _witness("fstr.str_field")))
                if not result_ok:
                    raise ThirUnsupported("field.result_type", detail=True)
                if not (
                        _field_receiver_ok(e, declared, analyzer)
                        or _optional_checked_field(e, declared, analyzer)
                        or _field_over_subscript_ok(e, declared, analyzer)
                        or _optional_field_over_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_container_subscript_ok(
                            e, declared, analyzer)
                        or _field_over_field_ok(e, declared, analyzer)):
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
        # honest for form-keyed sinks (the return BORROW-wrap stays off).
        fa_str = _resolved_str_value(rtype, analyzer)
        return THIRFieldAccess(
            result_type=rtype,
            receiver=_lower_expr(
                e.obj, lc, declared,
                use=_ExprUse(result=_ExprResultUse.RECEIVER),
                subscript_prechecked=isinstance(e.obj, TpySubscript)),
            field_cpp=_field_cpp(e),
            is_arrow=_field_is_arrow(e, lc),
            form=(Form.STORAGE if fa_str is not None and is_str_type(fa_str)
                  else Form.VALUE),
            loc=loc,
        )
    if isinstance(e, TpySubscript):
        if not subscript_prechecked and e.needs_optional_runtime_check:
            raise ThirUnsupported("subscript.optional_check", detail=True)
        if e.slice_function_info is not None:
            if not subscript_prechecked:
                fi = e.slice_function_info
                slice_ok = (
                    bool(fi.cpp_template)
                    and "{cpp}" not in fi.cpp_template
                    and _str_slice_receiver_supported(e.obj, lc, declared)
                    and _resolved_viewfam_value(rtype, analyzer) is not None)
                if slice_ok and isinstance(e.index, TpySlice):
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
                elif slice_ok:
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
                                  wrap=_BIGINT_INDEX_NARROW_WRAP, loc=loc)

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
                    field_prechecked=isinstance(e.obj, TpyFieldAccess)),
                index=THIRLiteral(result_type=analyzer.get_expr_type(e.index),
                                  value=idx, loc=loc),
                form=form,
                loc=loc,
            )
        if not subscript_prechecked:
            recv_t = _subscript_container_recv_type(
                e.obj, declared, analyzer)
            index_ok = (
                _bigint_index_disposition(e.index, analyzer) != "reject")
            ret_ok = (
                _resolved_scalar(rtype, analyzer)
                or _eligible_char(rtype)
                or _eligible_enum(rtype, analyzer) is not None
                or _eligible_ptr_value(rtype, analyzer)
                or _resolved_str_value(rtype, analyzer) is not None
                or _resolved_bytes_value(rtype, analyzer) is not None)
            container_ok = (
                recv_t is not None
                and _container_value_leaf_read(recv_t, analyzer)
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
            if not (container_ok or str_ok or bytes_ok):
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
        if isinstance(e.obj, TpyFieldAccess):
            _witness("subscript.field_recv")
            if _resolved_bytes_value(analyzer.get_expr_type(e.obj),
                                     analyzer) is not None:
                _witness("subscript.bytes_field")
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(
                e.obj, lc, declared,
                field_prechecked=isinstance(e.obj, TpyFieldAccess)),
            index=_narrow_bigint_index(_lower_expr(e.index, lc, declared), e.index,
                                       analyzer, loc),
            bounds_safe=e.bounds_safe,
            form=form,
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        if isinstance(e, TpyIntLiteral) and not -2**31 <= e.value <= 2**31 - 1:
            raise ThirUnsupported("expr.int_literal.range")
        if isinstance(e, TpyFloatLiteral) and not math.isfinite(e.value):
            raise ThirUnsupported("expr.float_literal.nonfinite")
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
            return THIRLiteral(result_type=rtype, value=neg, loc=loc)
        # IntEnum negation: `(-static_cast<U>(p))` (_gen_unaryop's enum arm).
        enum_neg = _enum_neg_wrap(e, analyzer)
        if enum_neg is None and e.op != "!":
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
        if not (_resolved_scalar(rtype, analyzer) or _eligible_char(rtype)
                or _eligible_enum(rtype, analyzer) is not None
                or _resolved_str_value(rtype, analyzer) is not None
                or _is_string_owned(rtype)):
            note_detail("ifexpr.result_type")
            raise ThirUnsupported("expr.ifexpr")
        return _lower_if_expr(e, rtype, lc, declared, loc)
    if isinstance(e, TpyCall):
        if not _call_use_supported(e, lc, declared, use):
            raise ThirUnsupported("expr.call")
        if e.macro_expansion is not None:
            # `@call_macro` / getattr / hasattr: the AST renders the
            # sema-synthesized replacement in place (gen_expr's macro arm), so
            # the call node lowers to its expansion.
            _witness("call.macro_expansion")
            return _lower_expr(e.macro_expansion, lc, declared)
        if e.cast_target_type is not None:
            if (e.cast_source_is_any or len(e.args) != 2 or e.kwargs
                    or e.double_star_unpack is not None):
                raise ThirUnsupported("expr.call")
            # `typing.cast(T, x)` non-Any: a compile-time no-op rendering the
            # bare source; Any uses the unsupported any_cast_or_panic wrap.
            _witness("call.cast_passthrough")
            return _lower_expr(e.args[1], lc, declared)
        if e.enum_from_value is not None:
            if (_eligible_enum(e.enum_from_value, analyzer) is None
                    or len(e.args) != 1 or e.kwargs):
                raise ThirUnsupported("expr.call")
            enum_arg_type = analyzer.get_expr_type(e.args[0])
            if (not _resolved_scalar(enum_arg_type, analyzer)
                    or (_runtime_bigint(enum_arg_type, analyzer)
                        and _const_index(
                            _unwrap_lit_coerce(e.args[0])) is not None)):
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
                qual = lc.analyzer.registry.record_qualification(
                    ri, lc.analyzer.ctx.module_name)
                if qual is not None:
                    _witness("ctor.cross_module")
                    type_cpp = qualified_cpp_name(*qual)
                else:
                    _witness("ctor.call")
                    type_cpp = e.func_name
            ctor_mut = fi.mutated_params or frozenset()
            args = []
            for i, (a, p) in enumerate(zip(e.args, fi.params)):
                if not _record_ctor_arg_supported(
                        a, p.type, i, fi, lc, declared, use):
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
                    args.append(_lower_call_arg(a, p.type, lc, declared))
            return THIRCtorCall(
                result_type=rtype, type_cpp=type_cpp,
                args=tuple(args), form=Form.STORAGE, loc=loc)
        if e.call_type is not None:
            # A generic-type INSTANTIATION (`list(it)` / `set(xs)`): the
            # resolved ctor's sema-substituted @cpp_template expanded over
            # per-slot args after validating this arm's shape. A
            # range(...) arg renders as its substituted Range template (the
            # comprehension begin/end iterable's render); every other admitted
            # arg lowers through the shared call-arg machinery.
            inst_fi = _instantiation_call_fi(e)
            fam = _storage_call_ret(rtype, analyzer)
            if (inst_fi is None or fam is None
                    or not _storage_call_container(fam)):
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
        if fi is not None and fi.is_method and fi.name == "__init__":
            # A scalar or slice-object type-constructor call (`Int32(x)` /
            # `basic_slice(1, 3)`): the emit is the resolved __init__ overload's
            # @cpp_template expanded over the args with no receiver. Sema
            # already substituted {cpp} / class type params; positional-only
            # templates are carried verbatim. A `None` bound in a slice-ctor's
            # value-repr `Int32 | None` slot renders `std::nullopt` (the
            # STORAGE-form None).
            template_fi = _template_init_call_fi(e)
            if template_fi is None:
                raise ThirUnsupported("expr.call")
            scalar_ctor = _eligible_scalar(rtype)
            slice_ctor = _slice_object_type(rtype)
            owned_str_ctor = _is_string_owned(rtype)
            if not (scalar_ctor or slice_ctor or owned_str_ctor
                    or _resolved_viewfam_value(rtype, analyzer) is not None):
                raise ThirUnsupported("expr.call")
            lowered_args = []
            for a, p in zip(e.args, template_fi.params):
                if scalar_ctor and not (
                        _ctor_arg_slot_ok(p.type, analyzer)
                        and _resolved_scalar(
                            analyzer.get_expr_type(a), analyzer)):
                    raise ThirUnsupported("expr.call")
                if slice_ctor and not (
                        isinstance(a, TpyNoneLiteral)
                        or _resolved_scalar(
                            analyzer.get_expr_type(a), analyzer)):
                    raise ThirUnsupported("expr.call")
                if owned_str_ctor and not _str_pass_through_arg(
                        a, p.type, declared, analyzer):
                    # `String(view)` -- the positional `std::string({0})`
                    # expansion over a bare str-family arg.
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
        k = _free_callee_kind(e, analyzer)
        len_call = _is_len_call(e, declared, analyzer)
        if not len_call:
            if k is None or fi is None:
                raise ThirUnsupported("expr.call")
            if not _call_arity_ok(e, fi):
                note_detail("call.arity_defaults")
                raise ThirUnsupported("expr.call")
            if any(isinstance(_peel_coerce(a), TpyStrLiteral)
                   for a in e.args):
                overloads = analyzer.registry.get_function(e.func_name)
                if overloads is not None and len(overloads) > 1:
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
        # there.
        params = (fi.params if fi is not None
                  and len(fi.params) == len(e.args) else None)
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
                return _lower_generic_plain_call(e, k[1] or None, lc, declared,
                                                 temp_args=temp_args,
                                                 form=form, loc=loc)
        return THIRCall(
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
        )
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
        retype = isinstance(e, TpySetLiteral) or is_array(container_type)
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
                    allow_record=not isinstance(e, TpySetLiteral),
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
                    allow_nested=True, allow_optional=True)
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
                 or _str_list_method_iterable_ok(e, declared, analyzer)))
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
        if not _plain_member_call_markers_ok(e):
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
            # A receiver-less marker call (module-qualified / static): the
            # classifier selected it through _marker_call_kind, so the same
            # classification names the emit arm -- the pre-rendered
            # qualified spelling on callee_cpp or the @native symbol on
            # native_name, both existing THIRCall arms. Args lower against
            # their param slots like a free call's, but dcbp-BLIND
            # (readonly_target stays False): the method-call arg loop calls
            # _gen_union_arg without the deep-const verdict.
            mk = _marker_call_kind(e, analyzer)
            if (mk is None or not _marker_call_supported(
                    e, mk, declared, analyzer,
                    stmt_position=result_use is _ExprResultUse.DISCARD,
                    temps_ok=use.allow_temps,
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
            if fi is None or not _plain_method_fi_ok(fi):
                note_detail("method.fi_kind")
                raise ThirUnsupported("expr.method_call")
            if not _call_arity_ok(e, fi):
                note_detail("method.arity_defaults")
                raise ThirUnsupported("expr.method_call")

            recv_type = _method_receiver_type(e.obj, declared, analyzer)
            stmt_position = result_use is _ExprResultUse.DISCARD
            storage_ret_ok = result_use is _ExprResultUse.STORAGE
            if (_container_scalar_read(recv_type, analyzer)
                    or _container_record_elem(recv_type, analyzer)):
                shape_ok = _container_method_call_supported(
                    e, fi, analyzer, stmt_position=stmt_position,
                    storage_ret_ok=storage_ret_ok)
            elif _protocol_binding(recv_type) is not None:
                shape_ok = _protocol_method_call_supported(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position)
            elif _resolved_str_value(recv_type, analyzer) is not None:
                shape_ok = _view_method_call_supported(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position)
            else:
                shape_ok = _record_method_call_supported(
                    e, fi, declared, analyzer,
                    stmt_position=stmt_position,
                    temps_ok=use.allow_temps,
                    record_ret_ok=(
                        result_use is _ExprResultUse.BORROW_BIND),
                    storage_ret_ok=storage_ret_ok,
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
        # pointer-local `(*p)` retag); arity was gated exact, so params always
        # pair. A user-record F2 pointer-local receiver renders `->`, as does
        # the method receiver itself (`self.helper()` -> `this->helper()`).
        # `temp_args` admits only the VALUE-union temp row here (the other
        # temp rows are free-call shapes -- a method ctor rvalue INLINES).
        params = (fi.params if fi is not None
                  and len(fi.params) == len(e.args) else None)
        # A protocol receiver misses `_gen_method_call`'s user-record arg loop
        # (guarded by `is_user_record`) and falls to the lazy `_args()`
        # fallback, which renders args like a FREE call's -- literals take
        # their slot's coercion instead of the method path's target-less
        # spelling. Same source of truth as the gate's `_protocol_binding`
        # check: a narrowed subject reads as its member on both sides.
        proto_recv = _protocol_binding(analyzer.get_expr_type(e.obj)) is not None
        if proto_recv:
            _witness("method.protocol")

        def _method_arg(a: TpyExpr, ptype: 'TpyType | None',
                        index: int) -> THIRExpr:
            _require_method_call_arg(
                e, a, ptype, index, lc, declared, temp_args=temp_args)
            if temp_args:
                ut = _value_union_temp_slot(a, ptype, lc.analyzer)
                if ut is not None and not (isinstance(a, TpyName)
                                           and (a.name in lc.narrow.narrowed
                                                or a.name in lc.inline_narrowed)):
                    _witness("argtemp.value_union_method")
                    return THIRArgTemp(result_type=ut, cpp_type=ut.to_cpp(),
                                       init=_lower_expr(a, lc, declared), form=Form.VALUE,
                                       loc=getattr(a, "loc", None))
            return _lower_call_arg(a, ptype, lc, declared,
                                   method_arg=not proto_recv)

        if isinstance(e.obj, TpyName) and e.obj.name == lc.self_receiver:
            _witness("call.self_method")
        # An unproven Optional-ptr borrow receiver takes the runtime-check
        # render (`::tpy::deref_check(p).method(args)`); the marker carve-out
        # local admission permits it only on such a receiver. Mutually exclusive
        # with the indirect (`->`) arm -- the checked deref yields a reference.
        deref_check = e.needs_optional_runtime_check
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
            is_arrow=not deref_check and isinstance(e.obj, TpyName)
                     and (e.obj.name in lc.pointers
                          or (e.obj.name == lc.self_receiver
                              and lc.self_is_pointer)),
            deref_check=deref_check,
            form=_viewfam_result_form(m_str),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        disp = _coerce_disposition(e)
        if disp is None:
            raise ThirUnsupported("expr.coerce")
        inner = _lower_expr(e.expr, lc, declared)
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
            # Float32 `f` suffix / the BigInt ctor wraps); mirror by retyping
            # the literal so the emitter picks the wrapped arm. Only a literal
            # source reaches here; other shapes reject during child lowering.
            inner = replace(inner, result_type=rtype)
        # Identity passthrough: the node's form is the wrapped expression's
        # form -- carried honestly (not the VALUE default) so the owned-sink
        # BORROW checks read the real source shape through the coerce (e.g.
        # string_to_str wraps a STORAGE String) -- EXCEPT a view-target coerce
        # (str_to_strview / string_to_strview): its value is a view into the
        # source's buffer whatever the source's form, so it sets BORROW
        # itself (an owned sink downstream must re-copy, like any view).
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
    raise ThirUnsupported(expr_kind_tag(e))

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
        retype_scalars: bool = True) -> THIRExpr:
    if not _container_lit_elem_ok(
            e, slot, declared, lc.analyzer, threaded=threaded, forced=forced,
            allow_record=allow_record, allow_nested=allow_nested,
            allow_optional=allow_optional):
        raise ThirUnsupported("expr.container_literal")
    return _lower_container_elem(
        e, slot, lc, declared, retype_scalars=retype_scalars)


def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx', declared: dict[str, TpyType], *,
                          retype_scalars: bool = True) -> THIRExpr:
    """Lower one container-literal element / dict key / dict value into its
    slot. A view-form str source (BORROW -- a string_view param/local, a slice,
    a StrView-returning call) into an owned `std::string` slot copies
    explicitly via the S1 view->owned `THIRFormConvert` (`std::string(x)`) --
    the `_wrap_for_owned_slot`/`_view_source_to_owned` chokepoint at element
    positions. A literal (VALUE, const char[N]) and an owned source (STORAGE --
    an owned local, a String local, a concat/f-string rvalue) land bare, like
    the AST's brace-init pass-through; scalar slots never wrap."""
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
        if vt is None:
            raise ThirUnsupported("expr.tuple_literal.slot")
        return _lower_tuple_literal(e, vt, lc, declared)
    el = _lower_expr(
        e, lc, declared, container_threaded=retype_scalars)
    if retype_scalars:
        el = _slot_literal_retype(el, slot)
    # A record-name element mirrors gen_expr_deref + _maybe_move: an F2
    # pointer-local name derefs (`(*p)`), and a movable owned local at its
    # last use moves into the element slot -- the same `movable_locals` +
    # `all_last_uses` facts the AST reads.
    if (isinstance(e, TpyName) and isinstance(el, THIRName)
            and e.name in lc.pointers):
        el = replace(el, deref=True)
    if _container_elem_move_source(e, lc):
        _witness("containerlit.move")
        el = THIRMove(result_type=el.result_type, value=el, form=el.form,
                      loc=getattr(e, "loc", None))
    # A value-`Optional[str]` slot (a widened value-tuple RETURN element) wraps
    # its str-view source exactly like the bare owned-str slot: the tuple
    # brace-init relies on the implicit `std::string -> std::optional<std::string>`
    # conversion, so the element renders `std::string(view)` (not a spelled
    # optional wrap), matching the AST. Resolve the str target through the
    # Optional inner.
    ou = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
          if slot is not None else None)
    str_slot = (ou.inner if isinstance(ou, OptionalType)
                and not ou.uses_pointer_repr() else slot)
    st = _resolved_str_value(str_slot, lc.analyzer) if str_slot is not None else None
    if st is not None and is_str_type(st) and el.form is Form.BORROW:
        return THIRFormConvert(result_type=st, value=el, form=Form.STORAGE,
                               loc=getattr(e, "loc", None))
    # The bytes sibling (S6): a view-form source into an owned `bytes`
    # element slot copies via `::tpy::bytes_copy` (a bytes literal lowers
    # STORAGE and renders its owned form bare).
    bt = _resolved_bytes_value(slot, lc.analyzer) if slot is not None else None
    if bt is not None and is_bytes_type(bt) and el.form is Form.BORROW:
        return THIRFormConvert(result_type=bt, value=el, form=Form.STORAGE,
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
    if isinstance(e, TpyName) and _value_opt_scalar_param(e.name, lc):
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
    <target-typed init>;` (TempState.create). Lowering admits only
    literal temporaries and bare scalar names, so the init render is the
    slot-retyped literal."""
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
        if (isinstance(ptype, TypeParamRef)
                and isinstance(_peel_coerce(a), (TpyIntLiteral,
                                                 TpyFloatLiteral,
                                                 TpyBoolLiteral))):
            if not temp_args:
                raise ThirUnsupported(
                    "generic ref-slot literal temp outside a flush position")
            _witness("argtemp.generic_ref_slot")
            args.append(THIRArgTemp(
                result_type=resolved, cpp_type=resolved.to_cpp(),
                init=_slot_literal_retype(_lower_expr(a, lc, declared), resolved),
                form=Form.VALUE, loc=getattr(a, "loc", None)))
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


def _lower_free_call_arg(e: TpyCall, a: TpyExpr,
                         ptype: 'TpyType | None', kind: 'tuple[str, str] | None',
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         temp_args: bool,
                         readonly_target: bool) -> THIRExpr:
    analyzer = lc.analyzer
    len_call = _is_len_call(e, declared, analyzer)
    if not len_call:
        if kind is not None and kind[0] in ("native", "template"):
            ok = _native_call_arg_ok(a, ptype, declared, analyzer)
        else:
            ok = _plain_call_arg_ok(
                a, ptype, declared, analyzer, temps_ok=temp_args,
                narrowed=frozenset(lc.narrow.narrowed))
        if not ok:
            raise ThirUnsupported("expr.call")
    if (_is_len_native(e) and isinstance(a, TpyFieldAccess)):
        return _lower_expr(a, lc, declared, field_prechecked=True)
    return _lower_call_arg(
        a, ptype, lc, declared, temp_args=temp_args,
        protocol_slots=kind is not None and kind[0] not in ("native", "template"),
        readonly_target=readonly_target)


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
    args = tuple(_slot_literal_retype(_lower_expr(a, lc, declared), counter)
                 for a in call.args)
    return THIRCall(result_type=lc.analyzer.get_expr_type(call),
                    callee=call.func_name, args=args, cpp_template=template,
                    loc=getattr(call, "loc", None))


def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                    declared: dict[str, TpyType], *, temp_args: bool = False,
                    readonly_target: bool = False,
                    method_arg: bool = False,
                    protocol_slots: bool = False) -> THIRExpr:
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
    if (isinstance(a, TpyName) and _value_opt_scalar_param(a.name, lc)
            and _value_opt_scalar(ptype, lc.analyzer) is not None):
        # A value-repr Optional[scalar] name into a value-repr Optional slot
        # passes the WHOLE optional bare (`take_opt(p)`), even when sema
        # narrowed the read -- the AST's gen_call_arg derefs only for a
        # NON-optional slot. Strip the name arm's deref-on-narrow.
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
    if isinstance(a, TpyStrLiteral) and _eligible_char(ptype):
        return _lower_char_targeted(a, ptype, lc, declared)
    if isinstance(a, TpyTupleLiteral):
        # A value-tuple literal into a value-tuple slot: the spelled
        # brace-init render, target-threaded per element by gen_call_arg
        # exactly like the return/decl positions (gate-admitted via
        # `_value_tuple_pass_through_arg`).
        vt = _value_tuple(ptype, lc.analyzer)
        if vt is not None:
            return _lower_tuple_literal(a, vt, lc, declared)
    if isinstance(ptype, TpyType) and (is_bytes_type(ptype)
                                       or is_bytes_view_type(ptype)):
        # Peel coerce wrappers exactly like gen_call_arg's span pin (the pin
        # renders the bare literal; the coercion's own codegen never runs).
        lit = _peel_coerce(a)
        if isinstance(lit, TpyBytesLiteral):
            lowered = _lower_expr(lit, lc, declared)
            return replace(lowered, form=Form.BORROW)
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
        rec_pt = _record_rvalue_temp_slot(a, ptype, lc.analyzer)
        if rec_pt is not None:
            _witness("argtemp.record_rvalue")
            return THIRArgTemp(
                result_type=rec_pt, cpp_type=rec_pt.to_cpp(),
                init=_lower_expr(a, lc, declared, use=_RECORD_TEMP_USE),
                form=Form.BORROW,
                loc=getattr(a, "loc", None))
        ut = _value_union_temp_slot(a, ptype, lc.analyzer)
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
    lift = _lower_union_arg_lift(a, ptype, lc, declared, readonly_target=readonly_target)
    if lift is not None:
        return lift
    # The pointer-repr Optional slot faces (must run BEFORE the pointer-local
    # deref retag: an already-pointer name passes BARE into the `T*` slot).
    # A narrowed subject is NOT skipped: its read renames to the extraction
    # alias inside _lower_expr and the 'name' face's `&(...)` wrap mirrors
    # the AST's `&(__u)` render (see _optional_ptr_arg).
    opt_face = _optional_ptr_arg_face(a, ptype, lc.analyzer)
    if opt_face is not None:
        ot = _optional_ptr_arg_slot(ptype, lc.analyzer)
        loc = getattr(a, "loc", None)
        if opt_face == 'none':
            _witness("optptr.none")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW, loc=loc)
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
    # A float literal into a Float32 (or Own[Float32]) slot renders with the
    # `f` suffix, and an int literal into a FREE-call BigInt slot takes the
    # ctor wrap -- gen_call_arg threads the param type into the render. A
    # METHOD arg's int literal stays BARE: gen_call_from_fi's
    # `_convert_to_fixed_int_arg` emits IntLiterals as plain C++ integers
    # (`items.push_back(2)` -- BigInt's implicit int ctor absorbs it).
    lowered = _lower_expr(a, lc, declared, use=_NESTED_ARG_USE)
    if (method_arg and isinstance(lowered, THIRLiteral)
            and isinstance(lowered.value, (int, float))
            and not isinstance(lowered.value, bool)):
        # Both numeric families: a method arg's int literal must not take
        # the BigInt ctor wrap AND its float literal must not take the
        # Float32 `f` suffix -- the method path renders literals target-less.
        return lowered
    return _slot_literal_retype(lowered, ptype)

def _lower_union_arg_lift(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                          declared: dict[str, TpyType], *,
                          readonly_target: bool = False,
                          ) -> 'THIRUnionArgLift | None':
    """The pointer-variant union-slot arg lift, or None when the arg renders
    bare. Mirrors `_gen_union_arg`'s dispatch over the gate-admitted shapes:
    a None literal is the monostate member; a member-typed name lifts
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
    if (not isinstance(a, TpyName) or a.name in lc.narrow.narrowed
            or a.name in lc.inline_narrowed):
        return None
    at = lc.analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        if deep_const and at == ut:
            _witness("unionlift.const_wrap")
            return THIRUnionArgLift(result_type=ut, variant_cpp=variant_cpp,
                                    value=_lower_expr(a, lc, declared), const_wrap=True,
                                    form=Form.BORROW, loc=loc)
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

def _lower_char_targeted(e: TpyExpr, target: TpyType | None,
                         lc: '_LowerCtx', declared: dict[str, TpyType], *,
                         use: _ExprUse = _ExprUse()) -> THIRExpr:
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
    return _lower_expr(e, lc, declared, use=use, target_type=target)

def _lower_truthy(e: TpyExpr, lc: '_LowerCtx',
                  declared: dict[str, TpyType], *,
                  unary_operand: bool = False) -> THIRExpr:
    """Lower a truthiness position (an if/while/assert condition, or a `not`
    operand). An enum-typed operand takes its truthiness wrap (THIREnumWrap;
    the plain-enum arm renders `true` and DROPS the operand, mirroring
    gen_truthy_expr); every other admitted shape's truthiness render equals
    its value render, so it lowers as a plain expression."""
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                      TpyStrLiteral, TpyBytesLiteral, TpyNoneLiteral)):
        if not (unary_operand and isinstance(e, TpyBoolLiteral)):
            raise ThirUnsupported("truthy.literal")
    if isinstance(e, TpyName) and (_value_opt_scalar_param(e.name, lc)
                                   or _value_opt_view_param(e.name, lc)):
        # A value-repr Optional[scalar] / Optional[view] read in a condition /
        # `not` operand takes gen_truthy_expr's optional arm: `::tpy::is_truthy(p)`
        # on the BARE optional, narrowed or not -- codegen's `narrowed_vars` is
        # not populated for Optional None-narrowing, so the truthiness always sees
        # the `std::optional<...>` binding (the deref-on-narrow is stripped here).
        return THIROptTruthy(result_type=BOOL,
                             operand=replace(
                                 _lower_expr(
                                     e, lc, declared,
                                     allow_whole_optional=True),
                                 deref=False),
                             loc=getattr(e, "loc", None))
    et = lc.analyzer.get_expr_type(e)
    wrap = _enum_truthy_wrap(et, lc.analyzer)
    if wrap is not None and not isinstance(e, (TpyName, TpyFieldAccess)):
        raise ThirUnsupported("truthy.enum_shape")
    if wrap is None:
        ptr_optional = (
            isinstance(e, TpyName) and isinstance(et, OptionalType)
            and _optional_ptr_borrow_name(
                e, declared, lc.analyzer) is not None)
        if ptr_optional:
            pass
        elif unary_operand:
            if et is None or not is_bool_type(et):
                raise ThirUnsupported("truthy.unary_operand")
        elif isinstance(e, TpyName):
            if (e.name not in declared or et is None or not is_bool_type(et)
                    or _unrouted_binding_read(
                        declared.get(e.name), lc.analyzer) is not None):
                raise ThirUnsupported("truthy.name")
        elif isinstance(e, TpyFieldAccess):
            if et is None or not is_bool_type(et):
                raise ThirUnsupported("truthy.field_nonbool")
            _witness("cond.bool_field")
        elif isinstance(e, TpyBinOp):
            if e.op not in (_COMPARE_OPS | _LOGICAL_OPS
                            | _IS_OPS | _MEMBERSHIP_OPS):
                raise ThirUnsupported("truthy.binop")
        elif isinstance(e, TpyMethodCall):
            if et is None or not is_bool_type(et):
                raise ThirUnsupported("truthy.method_nonbool")
            _witness("cond.bool_method")
        elif isinstance(e, TpyIfExpr):
            if et is None or not is_bool_type(et):
                raise ThirUnsupported("truthy.ifexpr_nonbool")
            _witness("ifexpr.cond_pos")
        elif not isinstance(e, (TpyUnaryOp, TpyChainedCompare)):
            raise ThirUnsupported("truthy.shape")
        # Non-enum, non-value-opt-param shapes whose truthiness render is not the
        # bare value render (str/bytes, storage Optional, record __bool__/__len__)
        # have no THIR wrap node yet -- reject so the body falls back to AST
        # rather than emitting the value bare (`if (s)` on a std::string).
        _reject_nonbare_truthy(et, lc.analyzer)
        return _lower_expr(
            e, lc, declared,
            use=_ExprUse(result=_ExprResultUse.CONDITION))
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
    then = _slot_literal_retype(_lower_char_targeted(e.then_expr, slot, lc, declared),
                                slot)
    orelse = _slot_literal_retype(_lower_char_targeted(e.else_expr, slot, lc, declared),
                                  slot)
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
    else:
        _witness("ifexpr.value")
    return THIRIfExpr(result_type=slot if slot is not None else rtype,
                      cond=cond, then=then, orelse=orelse, form=form, loc=loc)

def _slot_literal_retype(v: 'THIRExpr | None',
                         slot: 'TpyType | None') -> 'THIRExpr | None':
    """Mirror gen_expr's target threading for target-typed literal renders:
    a float literal against a Float32 slot takes the `f` suffix; an int
    literal against a BigInt slot takes the `::tpy::BigInt(...)` ctor wraps.
    The AST threads the slot type at decl inits/reassigns, returns,
    call/ctor args, field writes, MIL inits, container elements, and
    resolved-binop operands (the gen_expr_deref receiver/param targets) --
    comparison operands do NOT thread it (the compare block renders literal
    operands bare; a fixed-int/double context absorbs them). Applied
    post-lowering: only a float/int THIRLiteral is retyped, every other node
    passes through."""
    if slot is None or not isinstance(slot, TpyType):
        return v
    if not isinstance(v, THIRLiteral):
        return v
    st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if isinstance(st, OwnType):
        st = unwrap_readonly(st.wrapped)
    if isinstance(v.value, float) and is_float32_type(st):
        return replace(v, result_type=st)
    if (isinstance(v.value, int) and not isinstance(v.value, bool)
            and is_big_int_type(st)):
        return replace(v, result_type=st)
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
    `->` vs `.` is decided the same way as a value read."""
    return THIRFieldAccess(
        result_type=lc.analyzer.get_expr_type(e),
        receiver=_lower_expr(
            e.obj, lc, declared,
            field_prechecked=isinstance(e.obj, TpyFieldAccess),
            subscript_prechecked=isinstance(e.obj, TpySubscript)),
        field_cpp=_field_cpp(e),
        is_arrow=_field_is_arrow(e, lc),
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
