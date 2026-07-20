"""Sequential statement lowering with per-shape validation for declarations,
branches, loops, with, try, raise, and narrowing statements.
"""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, replace
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyAugAssign,
    TpyAwait,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBreak,
    TpyBytesLiteral,
    TpyCall,
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
    TpyGlobal,
    TpyIf,
    TpyIntLiteral,
    TpyLambda,
    TpyMatch,
    TpyMethodCall,
    TpyFunction,
    TpyName,
    TpyNestedDef,
    TpyNoneLiteral,
    TpyNonlocal,
    TpyPassStmt,
    TpyRaise,
    TpyReturn,
    TpySetLiteral,
    TpySlice,
    TpyStmt,
    TpyStrLiteral,
    TpySubscript,
    TpyTry,
    TpyTupleLiteral,
    TpyTupleUnpack,
    TpyUnaryOp,
    TupleElemCapture,
    TpyVarDecl,
    TpyWhile,
    TpyWith,
    VarLinkage,
    is_docstring,
    is_super_del_call,
)
from ...typesys import (
    AnyType,
    NominalType,
    OptionalType,
    OwnType,
    ReadonlyType,
    TpyType,
    TypeParamRef,
    TupleType,
    UnionType,
    VoidType,
    error_return_to_cpp,
    is_dyn_protocol,
    is_protocol_type,
    is_return_exception,
    is_union_or_optional_type,
    qualify_exception_name,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_bytearray_type,
    is_bytes_type,
    is_dict,
    is_fixed_int_type,
    is_list,
    is_set,
    is_str_type,
)
from ...modules.type_resolution import is_native_iterable
from ...sema.literal_utils import fixed_int_literal_value_from_expr
from ...codegen_cpp.forms import (
    LocalBinding,
    is_plain_nonvalue,
    is_ptr_variant_union,
    is_storage_tuple_alias_decl,
)
from ...codegen_cpp.protocols import (
    dynamic_adapter_type,
    dynamic_base_name,
    record_inherits_dynamic,
)
from ...codegen_cpp.context import escape_cpp_name
from ...codegen_cpp.types import resolve_pending_container
from ...liveness import stmts_terminate, try_terminates_ignoring_finally
from ...value_category import call_returns_cpp_ref, is_rvalue_source
from ..faces import witness as _witness
from ..fallback import (
    ThirUnsupported,
    _walk,
    begin_stmt,
    expr_kind_tag,
    note,
    note_detail,
    stmt_reject_reason,
)
from ..nodes import (
    Form,
    PrintForm,
    PtrSlotKind,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCoerce,
    THIRContainerLiteral,
    THIRContinue,
    THIRDelVar,
    THIRErrorReturnBind,
    THIRErrorReturnDiscard,
    THIRExpr,
    THIRExprStmt,
    THIRFieldAccess,
    THIRConsumingIter,
    THIRCopy,
    THIRForEach,
    THIRForIterProto,
    THIRForRange,
    THIRFormConvert,
    THIRIf,
    THIRIsinstance,
    THIRLiteral,
    THIRMethodCall,
    THIRModuleVar,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRNestedDef,
    THIRNoOpStmt,
    THIROptionalPtrArg,
    THIROptViewArg,
    THIRPrint,
    THIRExceptHandler,
    THIRPrintArg,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRRaise,
    THIRInplaceContainerOp,
    THIRResumableReturn,
    THIRReturn,
    THIRSelf,
    THIRSetItem,
    THIRSliceAssign,
    THIRStmt,
    THIRStrAppend,
    THIRStrLiteral,
    THIRTry,
    THIRTupleUnpack,
    TupleSourceBind,
    THIRUnaryNot,
    THIRVarDecl,
    THIRWhile,
    THIRWith,
    THIRWithItem,
    WithTargetArm,
)
from .predicates import (
    _bigint_index_disposition,
    _call_iterable_lvalue,
    _chain_post_if_fact,
    _const_exact_field_receiver_ok,
    _opt_view_arg_shim,
    _container_enum_spell,
    _container_scalar_read,
    _dict_view_iterable_ok,
    _elif_link,
    _eligible_char,
    _dyn_proto_ptr,
    _eligible_enum,
    _eligible_ptr_union,
    _union_storage_val_cpp,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _f1_is_const,
    _f1_param_lvalue_reseat_ok,
    _f1_record,
    _f1_tuple,
    _f1_tuple_field_write_ok,
    _f2_reseat_ok,
    _f2b_optional_field_write_ok,
    _facts_have_concrete,
    _field_decl_type,
    _field_receiver_ok,
    _for_each_elem_binding_ok,
    _foreach_value_opt_elem,
    _is_borrow_form_name,
    _is_borrow_ptr_local,
    _is_string_owned,
    _narrow_bigint_index,
    _narrow_fact_member,
    _narrow_facts_ok,
    _nonvalue_container_ret,
    _callable_value,
    _optional_narrow_facts_ok,
    _optional_ptr_borrow,
    _optional_ptr_borrow_name,
    _unwrap_own,
    _owned_str_append_target,
    _owned_str_slot,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _param_is_const,
    _value_opt_scalar,
    _value_opt_str,
    _value_opt_owned_view,
    _param_is_deep_const,
    _peel_stale_view_owned_coerce,
    _reassert_bump_info,
    _record_borrow_return,
    _resolve_pending_view,
    _resolved_bytes_value,
    _resolved_str_value,
    _resolved_viewfam_value,
    _is_range_call,
    _runtime_bigint,
    _range_object_value,
    _slice_object_type,
    _owned_tuple_call_ret,
    _storage_call_container,
    _storage_call_ret,
    _span_value,
    _str_field_value_read,
    _str_self_append_rhs,
    _type_family_tag,
    _peel_coerce,
    _subscript_container_recv_type,
    _record_has_delitem,
    _unwrap_lit_coerce,
    _storage_record_tuple,
    _value_tuple,
    _value_tuple_global,
    _value_tuple_nested,
    _var_decl_type,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    _LowerScope,
    _Prescan,
)
from .checks import (
    _assert_narrow_info,
    _borrow_local_binding,
    _bytes_aug_concat_ok,
    _class_const_aug_assign_ok,
    _class_const_write_target_ok,
    _container_aug_setitem_ok,
    _container_field_write_ok,
    _container_record_elem_subscript,
    _container_literal_decl_ok,
    _container_literal_shape_ok,
    _bytearray_recv,
    _container_setitem_ok,
    _user_record_setitem_ok,
    copy_plain_record_source,
    _func_ref_routable,
    _print_kwarg_token,
    _setitem_widened_elem_ok,
    _is_builtin_print,
    _is_len_call,
    _iter_proto_call_ret,
    _record_rvalue_source_shape,
    _rvalue_free_call_shape,
    _narrow_cond_info,
    _optional_record_field_inner,
    _optional_record_field_write_ok,
    _optional_field_none_write_ok,
    _print_arg_form,
    _print_arg_ok,
    _wrap_print_form,
    _print_optval_form,
    _print_optval_opt,
    _ptr_union_field_write_ok,
    _ptr_union_source_ok,
    _ctor_shape_ok,
    _native_ctx_manager_ok,
    _native_record_rvalue_call_shape,
    _record_field_write_ok,
    _scalar_aug_assign_ok,
    _scalar_field_write_ok,
    _user_deref_field_write_ok,
    _str_aug_append_ok,
    _bytes_field_write_ok,
    _str_field_write_ok,
    _str_list_method_iterable_ok,
    _user_iterator_iterable,
)
from .expressions import (
    _container_slice_recv_ok,
    _flush_witness,
    _narrow_member_cpp,
    _narrow_subject_const,
    _param_declared_type,
    _is_move_source,
    _lower_call_arg,
    _lower_char_targeted,
    _lower_class_const_write_target,
    _lower_ctor_call_args,
    _lower_expr,
    lower_print_sink,
    _lower_field_source,
    _lower_lambda,
    _lower_truthy,
    _lower_borrow_tuple_literal,
    _lower_tuple_literal,
    _rb_operand_slots,
    _retag_bytes_literal_view,
    _slice_bound_supported,
    _slot_literal_retype,
    _value_opt_scalar_binding,
    _value_opt_view_binding,
    _value_opt_view_param,
)
from . import comprehensions as _comprehensions
from . import match as _match

def _kind_detail(prefix: str, e: TpyExpr) -> bool:
    """`note_detail` with the expr's node kind as the suffix -- the
    dispatch-tail catch-all for a gate arm with no finer reject reason
    (set-if-empty, so a finer inner detail always wins)."""
    return note_detail(prefix + expr_kind_tag(e).removeprefix("expr."))


def _record_source_reject_detail(
        v: TpyExpr, pointers: set[str], narrowed: AbstractSet[str],
        borrow_slot: bool) -> str:
    """Sub-classify an unrouted record-return SOURCE for the fallback tally --
    the coarse `return.record_source` mass is dominated by construct-disjoint
    frontiers that sequence differently (record-valued ctor/call args, record
    method-call value sources, deref/move of a pointer-local), so name the
    shape and slot rather than collapsing them into one bucket. Diagnostic
    only: `note_detail` feeds the summary tally, never lowering/emit."""
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
    """`del x` where x's resolved type is trivially destructible -- the AST's
    first `_gen_del_var_code` skip, which emits NO code (only the source
    comment). A pending str/bytes local must resolve first: its view
    resolution is trivial (`std::string_view`/span), its owned resolution
    (`std::string`/vector) takes the move-sink face instead."""
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
    runtime-BigInt / owned-str -- `_container_elem_family`'s dict keys). The
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
    return ((is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
             or _owned_str_slot(key, analyzer))
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
    if not _any_value_dict(
            _subscript_container_recv_type(recv, declared, analyzer),
            analyzer):
        return False
    return _bigint_index_disposition(sub.index, analyzer) != "reject"

def _any_dict_setitem_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """`d[k] = v` into a `dict[K, Any]` value slot: the value must ALREADY be
    Any-typed (a bare name -- an `Any` param/local), so the AST's element-slot
    `wrap_into_any` chokepoint cannot fire and the value renders bare
    (`::tpy::__setitem__(d, k, v);`). A not-yet-Any value arrives as an
    `into_any` coerce and stays on the AST path."""
    if not _any_dict_subscript_shape_ok(
            stmt.target, declared, pointers, narrowed, analyzer):
        return False
    v = stmt.value
    return (isinstance(v, TpyName) and v.name in declared
            and v.name not in pointers and v.name not in narrowed
            and _is_any_type(analyzer.get_expr_type(v))
            and _witness("setitem.any_value"))

def _container_name_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: 'AbstractSet[str]', analyzer) -> bool:
    """`recv.field = name` where field and name are the same builtin container
    family: the AST's default field assign renders the bare name and
    `_maybe_move` moves an owned local at its last use
    (`this->_data = std::move(d);`) -- the container sibling of the F1
    record-name field write, riding the generic storage-convert tail."""
    v = stmt.value
    if not isinstance(v, TpyName):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(stmt.target))))
    if not (is_dict(ft) or is_list(ft) or is_set(ft)):
        return False
    if v.name in pointers or v.name in narrowed or v.name not in declared:
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    return (((is_dict(ft) and is_dict(vt)) or (is_list(ft) and is_list(vt))
             or (is_set(ft) and is_set(vt)))
            and _witness("field_write.container_name"))

def _lower_dyn_setattr_call(call: TpyMethodCall, lc: '_LowerCtx',
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The sema-synthesized `obj.__setattr__("name", <value>)` behind a
    dynamic-attr write (`obj.x = v`, D16). The generic method-call arm cannot
    admit it -- the `Any` value slot is outside the arg-family slice -- so
    this narrow mirror reproduces `_gen_method_call`'s user-record tail for a
    bare non-pointer F1-record receiver name, the literal name arg, and an
    `into_any`-coerced str-literal value (the coercion's `make_any` wrap
    carried as the THIRCoerce `{0}` template -- the same codegen lambda the
    AST calls, applied to a placeholder)."""
    analyzer = lc.analyzer
    loc = getattr(call, "loc", None)
    recv = call.obj
    if not (isinstance(recv, TpyName) and recv.name in declared
            and recv.name != lc.self_receiver
            and recv.name not in lc.pointers
            and recv.name not in lc.narrow.narrowed
            and recv.name not in lc.frame_slots
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
    # Only str/int-literal sources pin the coercion lambda's wrap to a
    # placeholder-transparent template (`::tpy::make_any(std::string({0}))` /
    # `::tpy::make_any(::tpy::BigInt({0}))`) with a bare inner render on both
    # paths; other actual types take value-dependent renders.
    if not (isinstance(value_arg, TpyCoerce)
            and value_arg.coercion.name == "into_any"
            and isinstance(value_arg.expr, (TpyStrLiteral, TpyIntLiteral))):
        note_detail("setattr.value_shape")
        raise ThirUnsupported("stmt.assign")
    wrap = value_arg.coercion.codegen(
        "{0}", value_arg.actual_type, value_arg.expected_type,
        value_arg.context_kind)
    lowered_value = THIRCoerce(
        result_type=analyzer.get_expr_type(value_arg),
        expr=_lower_expr(value_arg.expr, lc, declared),
        coercion_name=value_arg.coercion.name,
        wrap=wrap,
        loc=getattr(value_arg, "loc", None),
    )
    _witness("method.dyn_setattr")
    return THIRMethodCall(
        result_type=VoidType(),
        receiver=_lower_expr(
            recv, lc, declared,
            use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
        method_cpp=escape_cpp_name(call.method),
        args=(_lower_call_arg(name_arg, fi.params[0].type, lc, declared,
                              method_arg=True),
              lowered_value),
        loc=loc,
    )

def _scalar_or_str_unpack_elem(t: TpyType | None, analyzer) -> bool:
    """A tuple-unpack target / source-tuple element this cell admits: a value
    scalar, or a str -- the view-form target `std::string_view name =
    std::get<i>(tup)` binds a view into the source tuple's element, valid for
    the tuple's scope (which encloses the targets), exactly as a str loop var /
    str decl views its source. `render_type(target_types[i])` spells the view
    (the same `type_to_cpp` the AST arm calls), so the lowering needs no str
    arm. Record / bytes / Optional / union elements take the borrow-alias /
    other _gen_tuple_unpack branches -- deferred rungs."""
    return (_eligible_scalar(t)
            or _resolved_str_value(unwrap_ref_type(t) if t is not None else None,
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
                                 allow_record: bool = False) -> bool:
    """A `list[tuple[scalar-or-str, ...]]` / `Array[tuple[scalar-or-str, ...], N]`
    binding -- admitted as the tuple-unpack loop's iterable (element family gated
    by `_scalar_or_str_unpack_elem`). Element-TOUCHING read gates
    (subscript, plain iteration, method calls) check their own element family
    and reject tuple elements; `len(xs)` IS lit up (`_is_len_call` is
    element-agnostic) but renders the identical bare-name `::tpy::__len__`
    on both paths."""
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
                    # `allow_record` (the borrow-tuple for-head unpack): an
                    # F1-record element aliases into an is_ref target via the
                    # loop element's tuple_to_pointer lift.
                    or (allow_record and _f1_record(
                        unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et))),
                        analyzer))
                    for et in elem.element_types))

def _range_bound_literal_value(arg: TpyExpr) -> int | None:
    # The AST's inline-vs-hoist decision for a range bound (_is_literal_range_arg):
    # an inlinable bare int literal (possibly behind the int_literal coerce) vs a
    # name/expr hoisted to a temp. No magnitude clamp: both paths render the bound
    # through the shared literal rules, so only this choice must agree with
    # _extract_int_literal, including fixed-int ctor literals (`Int32(3)`).
    return fixed_int_literal_value_from_expr(arg)

def _lower_range_arg(arg: TpyExpr, et: TpyType, lc: _LowerCtx,
                     declared: dict[str, TpyType]) -> THIRExpr:
    """Lower one range bound/step. A folded fixed-int-ctor literal
    (`Int32(3)`) lowers to the bare-token literal directly -- lowering the
    ctor CALL would render `Int32(3)`'s call shape, but the AST inlines the
    folded token. Every other admitted shape lowers normally; the counter
    slot retype applies either way."""
    peeled = _unwrap_lit_coerce(arg)
    if isinstance(peeled, TpyCall):
        v = _range_bound_literal_value(arg)
        if v is not None:
            lit = THIRLiteral(result_type=lc.analyzer.get_expr_type(peeled),
                              value=v, loc=getattr(arg, "loc", None))
            return _slot_literal_retype(lit, et, lc)
    return _slot_literal_retype(_lower_expr(arg, lc, declared), et, lc)

def _range_step_kind(step_arg: TpyExpr, declared: dict[str, TpyType]) -> str | None:
    # Classify a 3-arg range's step into the AST's _gen_range_counter_loop arm,
    # or None to defer. Conservative slice, mirroring the fixed-int subset the
    # emitter reproduces byte-for-byte:
    #   * a bare (possibly negated) int literal or a fixed-int-ctor literal
    #     (`Int32(2)` -- folded like _extract_int_literal) -> plus_one (+1) /
    #     unit_neg (-1) / literal_pos / literal_neg; a zero step is rejected
    #     (the AST falls to the Range ctor there, not this counter loop).
    #   * a bare fixed-int name -> variable (captured into `__step_N`).
    # A ctor-literal step (`Int32(2)`) folds like a bound: the fold yields
    # the same bare token as a plain literal, so the stepped arms' overflow
    # helpers receive an identical render (pinned by the ctor-literal-step
    # unit byte-diff and the corpus exec run). Binop/call steps are deferred
    # for the same net-confidence reason as the bound slice.
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
    return None

def _for_loop_shape_ok(stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
                       allow_hoist: bool = False,
                       allow_branch_decls: bool = False) -> bool:
    """The for-loop shape guards shared by the range-for and container-for cells: no
    async / tuple-unpack / enum / consuming; and a loop-scoped var (not shadowing
    an outer local, whose `was_declared` handling the emitter does not reproduce).
    A for/else clause is NOT a shape reject: the else block emits outside the loop
    machinery.

    `allow_hoist` admits the hidden-counter / assign-binding rebind
    (`hoist_loop_var`, a loop var used after the loop); `stmt.var in declared` is
    then expected (the rebind case) and not a reject. `allow_branch_decls` admits
    the branch-first-decl predecls (`if_branch_decls`) the caller renders through
    `hoist_decls` (mirrors _emit_branch_decls); without it those reject (no
    predecl equivalent on the route)."""
    if (stmt.is_async or stmt.is_tuple_unpack
            or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None):
        return False
    if not allow_branch_decls and analyzer.if_branch_decls.get(id(stmt)):
        return False
    if stmt.hoist_loop_var:
        return allow_hoist
    return stmt.var not in declared

@dataclass(frozen=True)
class _ForEachRoute:
    route: str
    elem_type: TpyType
    iterable_lvalue: bool = False
    step_kind: str = "plus_one"
    unpack_target_types: tuple['TpyType | None', ...] = ()
    str_list_method: bool = False
    container_field: bool = False
    value_tuple_elem: bool = False
    bigint_counter: bool = False
    str_literal_iterable: bool = False
    # tuple_unpack over a generator/iterator call: the head unpack rides the
    # universal __iter__/__next__ loop (THIRForIterProto), not begin/end.
    iter_proto: bool = False
    # Native auto-consuming iterable (`::tpy::own_iter(std::move(name))`): the
    # consuming `__iter__`'s C++ symbol. Set only by `_for_consuming_route`; the
    # iterable is wrapped in THIRConsumingIter (rvalue capture) and the loop var
    # joins movable_locals for the body.
    consuming_native_name: 'str | None' = None


def _for_range_route(stmt: TpyForEach, analyzer,
                     declared: dict[str, TpyType]) -> '_ForEachRoute | None':
    # `for v in range(stop | start, stop [, step])` over a fixed-int or runtime-
    # BigInt counter (loop var not used after the loop). The shared shape guards
    # exclude the other richer for-shapes. A 3-arg stepped range is admitted only
    # for a fixed-int counter with a slice-eligible step (see _range_step_kind);
    # the BigInt-counter stepped emit (a `__step_N` temp even for a literal, no
    # overflow check) stays on the AST path.
    it = stmt.iterable
    if not _is_range_call(it) or not _for_loop_shape_ok(
            stmt, analyzer, declared, allow_hoist=True,
            allow_branch_decls=True):
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
        # The stepped emit's overflow / nonzero checks are fixed-int only; the
        # BigInt-counter variant differs (a literal-step temp, no overflow) and
        # is deferred.
        if not is_fixed_int_type(et):
            return None
        step_kind = _range_step_kind(it.args[2], declared)
        if step_kind is None:
            return None
    lowered_et = resolve_int_literals(
        et, analyzer.ctx.default_int_for_literal)
    return _ForEachRoute(
        route="range", elem_type=lowered_et, step_kind=step_kind,
        bigint_counter=bigint_counter)

def _resolved_loop_elem_type(stmt: TpyForEach, analyzer) -> 'TpyType | None':
    # resolve_int_literals: a literal-seeded container's elem_type is still
    # IntLiteral (IntLiteralType.to_cpp() would emit the VALUE); the AST binding
    # emits the resolved default-int spelling. A str loop var (list[str] element
    # / owned-str dict key) resolves its PendingStrType like the AST's
    # resolve_type, matching the lowering's `et`.
    if stmt.elem_type is None:
        return None
    et = resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                              analyzer.ctx.default_int_for_literal)
    str_et = _resolved_str_value(et, analyzer)
    return str_et if str_et is not None else et

def _for_each_container_route(
        stmt: TpyForEach, analyzer,
        declared: dict[str, TpyType]) -> '_ForEachRoute | None':
    # `for v in <container>` over a NativeIterable with a value-scalar (`list[scalar]` /
    # `dict[fixed-int-key]`, a typed copy; bytes/BytesView are
    # NativeIterable[UInt8] -- the same typed-copy loop var), Char (str/StrView,
    # NativeIterable[Char]),
    # str (a `list[str]` element / owned-str dict key -- the loop var is a fresh
    # view var, usage-resolved to `std::string_view` or an owned `std::string`
    # copy; `loop_var_binding` spells both), or F1-record (`list[record]`, a
    # borrow alias) loop var. A generator/user-iterator
    # (the `__iter__`/`__next__` fallback) and the
    # shared richer for-shapes stay on the AST path.
    if not _for_loop_shape_ok(stmt, analyzer, declared, allow_hoist=True,
                              allow_branch_decls=True):
        return None
    it = stmt.iterable
    # A plain in-scope container name, or a str/bytes-family field off an
    # F1-record receiver (`for c in h.name:`) -- both C++ lvalues
    # (is_lvalue_iterable: a name, or a field access over one), taking the
    # `auto& __obj_N =` capture -- or an eligible str- or container-returning
    # call (`for c in full(s):` / `for x in make_list():`). The call's capture
    # verdict rides `iterable_lvalue` (`_call_iterable_lvalue`: a str return
    # and an `Own[...]` container return are rvalues, the owning `auto
    # __obj_N =` capture; a borrow container return is an lvalue). A
    # slice-subscript iterable is the owning-capture rvalue arm below;
    # bytes-returning calls stay deferred.
    if _is_range_call(it):
        return None
    iterable_lvalue = True
    str_list_method = False
    container_field = False
    str_literal_iterable = False
    if isinstance(it, TpyStrLiteral):
        # `for ch in "abc"`: the str literal is an rvalue captured as
        # `std::string_view("abc")` (C string literals carry the NUL
        # terminator, so the wrap trims it); Char elements.
        it_type = _resolved_str_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            return None
        iterable_lvalue = False
        str_literal_iterable = True
    elif isinstance(it, TpyCall):
        ret = analyzer.get_expr_type(it)
        it_type = _resolved_str_value(ret, analyzer)
        if it_type is None:
            # container_ret_ok widens TpyCall lowering past str returns; the
            # bytes returns it admits in value position are filtered here.
            if not _nonvalue_container_ret(ret):
                return None
            it_type = unwrap_readonly(unwrap_send_sync(ret))
        iterable_lvalue = _call_iterable_lvalue(it, analyzer)
    elif isinstance(it, TpyMethodCall):
        # `for v in d.values():` / `for k in d.keys():` / `for kv in d.items():`
        # (dict view -- items yields a value tuple, admitted by the elem gate
        # below) or `for w in s.split():` (a str method returning
        # `Own[list[str]]`) -- all rvalues (owning `auto __obj_N =` capture,
        # iterable_lvalue=False).
        dict_view = _dict_view_iterable_ok(
            it, declared, analyzer, methods=("values", "keys", "items"))
        str_list_method = _str_list_method_iterable_ok(
            it, declared, analyzer)
        if not (dict_view or str_list_method):
            return None
        it_type = analyzer.get_expr_type(it)
        iterable_lvalue = False
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
    elif isinstance(it, TpyArrayLiteral):
        # A list-literal iterable: the AST renders it target-less
        # (gen_expr_deref threads no container target) and captures the
        # initializer list by value -- `auto __obj_N = {a, b, c};`, an
        # rvalue. The literal lowers through the container-literal arm with
        # threading OFF so the elements spell identically (a threaded str
        # element would take the owned-copy wrap the AST never emits here).
        rt = analyzer.get_expr_type(it)
        it_type = resolve_pending_container(rt, analyzer) or rt
        if it_type is None:
            note_detail("foreach.iter_literal_type")
            return None
        it_type = unwrap_readonly(unwrap_send_sync(it_type))
        iterable_lvalue = False
    elif isinstance(it, TpyFieldAccess):
        if not _field_receiver_ok(it, declared, analyzer):
            note_detail("foreach.field_parent")
            return None
        it_type = _resolved_viewfam_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            # Container field (`for x in self.xs:`): the DECLARED field type,
            # unwrapped like the name arm -- a narrowed Optional[container]
            # field (the AST's `(*recv.field)` unwrap) stays OptionalType here
            # and rejects at is_native_iterable. The iterable renders as its
            # own THIRFieldAccess inside the same lvalue `auto& __obj_N =`
            # capture a name takes.
            ft = _field_decl_type(it, declared, analyzer)
            if ft is None:
                note_detail("foreach.field_family")
                return None
            it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
            container_field = True
    elif (isinstance(it, TpySubscript)
          and it.slice_function_info is not None
          and isinstance(it.index, TpySlice)):
        # A slice-subscript iterable (`for b in items[1:3]:`): the slice
        # rvalue takes the owning `auto __obj_N =` capture; the subscript
        # arm re-validates the receiver / bounds and falls the body back
        # on a shape outside its slice.
        rt = analyzer.get_expr_type(it)
        it_type = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
                   if rt is not None else None)
        if it_type is None:
            return None
        iterable_lvalue = False
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
    # Compositional element gate: admit any element with a byte-identical
    # loop_var_binding arm (every resolved concrete type -- the shared helper
    # picks the form). Unresolved pending elements stay on the AST path.
    if not _for_each_elem_binding_ok(et):
        note_detail("foreach.elem_family." + _type_family_tag(et, analyzer))
        return None
    return _ForEachRoute(
        route="container", elem_type=et,
        iterable_lvalue=iterable_lvalue,
        str_list_method=str_list_method,
        container_field=container_field,
        str_literal_iterable=str_literal_iterable,
        value_tuple_elem=_value_tuple(et, analyzer) is not None)

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
    if fi is None or not fi.native_name:
        return None
    if (stmt.is_async or stmt.is_tuple_unpack or stmt.enum_iterable is not None
            or stmt.hoist_loop_var or stmt.const_loop_var):
        return None
    if analyzer.if_branch_decls.get(id(stmt)):
        return None
    if stmt.var in declared:
        return None
    it = stmt.iterable
    if not isinstance(it, TpyName) or it.name not in declared:
        return None
    it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[it.name])))
    if not is_native_iterable(it_type, analyzer.registry):
        return None
    et = _resolved_loop_elem_type(stmt, analyzer)
    if not _for_each_elem_binding_ok(et):
        return None
    return _ForEachRoute(route="container", elem_type=et, iterable_lvalue=False,
                         consuming_native_name=fi.native_name)

def _for_enum_route(stmt: TpyForEach, analyzer,
                    declared: dict[str, TpyType]) -> '_ForEachRoute | None':
    """`for c in Color:` -- range over `::tpy::EnumUtil<E>::members` (an lvalue
    static array), mirroring `_gen_for_each_loop`'s enum arm. The loop var binds
    the enum value (`Color c = *__beg_N;`). The other shape guards
    (`_for_loop_shape_ok` minus its blanket enum exclusion) still apply."""
    if stmt.enum_iterable is None:
        return None
    if (stmt.is_async or stmt.is_tuple_unpack
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return None
    if analyzer.if_branch_decls.get(id(stmt)):
        return None
    if stmt.var in declared:
        return None
    et = stmt.enum_iterable
    if not _for_each_elem_binding_ok(et):
        return None
    return _ForEachRoute(route="enum", elem_type=et, iterable_lvalue=True)

def _for_tuple_unpack_route(
        stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str]) -> '_ForEachRoute | None':
    """`for a, b in <iterable>:` -- the parser desugars to a ForEach over a
    synthetic `__for_tup_N` var whose body leads with a TpyTupleUnpack from
    it. Slice: an lvalue `list[tuple[scalar-or-str]]`-family name or a
    `d.items()` dict-view call as the iterable; all-new plain value scalar-or-str
    targets (no ref/owned/const-ref elements, no discard restrictions -- `_`
    slots skip). Record elements take the borrow target branch of
    _gen_tuple_unpack -- a deferred row."""
    if not stmt.is_tuple_unpack:
        return None
    if (stmt.is_async or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return None
    # if_branch_decls here are the unpack TARGETS used after the loop -- the
    # ForEach lowering predecls them and the head assigns; the synthetic loop
    # var (`__for_tup_N`) is never hoisted.
    if stmt.var in declared:
        return None
    if not stmt.body or not isinstance(stmt.body[0], TpyTupleUnpack):
        return None
    up = stmt.body[0]
    if not (isinstance(up.value, TpyName) and up.value.name == stmt.var):
        return None
    hoisted_names = analyzer.if_branch_decls.get(id(stmt), {}).keys()
    target_types = _tuple_unpack_targets(up, analyzer, declared, narrowed,
                                         hoisted_names)
    if target_types is None:
        return None
    it = stmt.iterable
    if (isinstance(it, (TpyCall, TpyMethodCall))
            and not _is_range_call(it)
            and it.resolved_function_info is not None
            and (it.resolved_function_info.is_generator
                 or _iter_proto_call_ret(it, analyzer))):
        # `for a, b in zip(xs, ys):` / `for a, b in gen(n):` -- the
        # universal __iter__/__next__ loop over the call result; the head
        # unpack reads the loop var like the container arm's. Element
        # arity is defensive like the name arm (sema errors on mismatch).
        et = _resolved_loop_elem_type(stmt, analyzer)
        elem = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
                if et is not None else None)
        if (not isinstance(elem, TupleType)
                or len(elem.element_types) != len(up.targets)
                or not _for_each_elem_binding_ok(et)):
            return None
        # A generator / zip source yields BORROW-form tuples (`std::tuple<T*>`);
        # a ref / fresh-const-ref target reads them with the unwrapped emit, NOT
        # the storage container's tuple_to_pointer lift, so it defers (only the
        # value-tuple / scalar unpack routes over an iter-proto source).
        if any(up.is_ref) or any(
                up.is_const_ref[i] and n not in hoisted_names
                for i, n in enumerate(up.targets) if n is not None):
            return None
        return _ForEachRoute(
            route="tuple_unpack", elem_type=et,
            iterable_lvalue=_iter_call_lvalue(it, analyzer),
            unpack_target_types=tuple(target_types),
            value_tuple_elem=_value_tuple(et, analyzer) is not None,
            iter_proto=True)
    if isinstance(it, TpyMethodCall):
        # `for k, v in d.items():` -- the items view is an rvalue capture.
        if not _dict_view_iterable_ok(it, declared, analyzer,
                                      methods=("items",)):
            return None
        it_type = analyzer.get_expr_type(it)
        iterable_lvalue = False
    elif isinstance(it, TpyName):
        if it.name not in declared:
            return None
        it_type = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(declared[it.name])))
        if not _container_scalar_tuple_iter(it_type, analyzer,
                                            allow_record=True):
            return None
        elem = unwrap_readonly(it_type.type_args[0])
        if len(elem.element_types) != len(up.targets):
            return None  # defensive: sema errors on arity mismatch
        iterable_lvalue = True
    else:
        return None
    if not is_native_iterable(it_type, analyzer.registry):
        return None
    et = _resolved_loop_elem_type(stmt, analyzer)
    if et is None:
        return None
    return _ForEachRoute(
        route="tuple_unpack", elem_type=et,
        iterable_lvalue=iterable_lvalue,
        unpack_target_types=tuple(target_types),
        value_tuple_elem=_value_tuple(et, analyzer) is not None)


def _iter_call_lvalue(it: 'TpyCall | TpyMethodCall', analyzer) -> bool:
    """`is_lvalue_iterable`'s call arm for the iter_proto route's admitted
    calls (generator or iterator-returning): a builtin-typed call
    (`call_type`), a record CTOR, an `Own[...]` return, a protocol return
    (generators' `Iterator[T]`), and a value-type return are all rvalues
    (the owning `auto __src_N =` capture in the brace scope); the remaining
    shape -- a borrow record return -- is a C++ lvalue (`auto& __src_N =`).
    The type reads mirror the AST's `get_resolved_type` default tail
    (readonly-unwrapped `get_expr_type`; the call node hits none of the
    special arms)."""
    if isinstance(it, TpyCall):
        if it.call_type is not None:
            return False
        if (isinstance(it.func, TpyName)
                and analyzer.registry.get_record(it.func_name)):
            return False
    rfi = it.resolved_function_info
    if rfi is not None and isinstance(rfi.return_type, OwnType):
        return False
    ret = analyzer.get_expr_type(it)
    if ret is None:
        return False
    ret = unwrap_readonly(ret)
    if is_protocol_type(ret):
        return False
    return not ret.is_value_type() and not is_union_or_optional_type(ret)


def _for_iter_proto_route(
        stmt: TpyForEach, analyzer,
        declared: dict[str, TpyType],
        iterator_object_locals: 'AbstractSet[str]' = frozenset()
        ) -> '_ForEachRoute | None':
    """The universal `::tpy::__iter__` + `__next__` protocol loop
    (`_gen_direct_next_loop_with_iter`), for the iterables the container
    route's NativeIterable gate excludes. Slice: a free GENERATOR or
    iterator-returning call (`for x in gen(n):` / `for x in reversed(xs):`
    / `for x in SimpleIter(4):` -- the capture verdict mirrors
    is_lvalue_iterable's call arm; the callee admission itself is the call
    classifier's `generator_ok` / the ITERABLE-use result widening) or a
    USER-ITERATOR local name (a concrete non-generic record with
    `__iter__`/`__next__` -- a C++ lvalue, `auto& __src_N`). Protocol-typed
    params (the template-param spelling), fields, and gen-valued locals
    stay later cells."""
    if not _for_loop_shape_ok(stmt, analyzer, declared):
        return None
    if stmt.is_tuple_unpack:
        return None
    it = stmt.iterable
    if isinstance(it, TpyCall):
        if _is_range_call(it):
            return None
        fi = it.resolved_function_info
        if fi is None:
            return None
        if not fi.is_generator and not _iter_proto_call_ret(it, analyzer):
            return None
        iterable_lvalue = _iter_call_lvalue(it, analyzer)
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
        iterable_lvalue = _iter_call_lvalue(it, analyzer)
    elif isinstance(it, TpyName):
        # `for x in self:` renders the receiver DEREFERENCED (`auto& __src_N
        # = (*this);`, gen_expr_deref) -- the self-iterable rung is deferred.
        if it.name not in declared or it.name == "self":
            return None
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[it.name])))
        # User-iterator records (monomorphized generic ones included -- their
        # `::tpy::__iter__` universal loop renders identically): a
        # protocol-typed binding (Iterator[T] param) spells through the deduced
        # template param on the AST path -- deferred. An iterator-object
        # LOCAL (`it = g()`, the `auto` decl) is exempt: its protocol type
        # never reaches a C++ spelling, the loop captures the plain lvalue
        # (`auto& __src_N = it;`).
        if it.name in iterator_object_locals:
            pass
        elif (not isinstance(u, NominalType) or u.is_protocol
                or not _user_iterator_iterable(u, analyzer)):
            return None
        iterable_lvalue = True
    else:
        return None
    et = _resolved_loop_elem_type(stmt, analyzer)
    if not _for_each_elem_binding_ok(et):
        note_detail("foreach.elem_family." + _type_family_tag(et, analyzer))
        return None
    return _ForEachRoute(route="iter_proto", elem_type=et,
                         iterable_lvalue=iterable_lvalue)


def _tuple_unpack_reject_tag(stmt: TpyForEach, analyzer,
                             declared: dict[str, TpyType],
                             narrowed: AbstractSet[str]) -> str:
    """Name the blocked rung of a tuple-unpack loop head -- mirrors
    `_for_tuple_unpack_route`'s reject order, tags only."""
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
        tt = unwrap_ref_type(up.target_types[i])
        if not _scalar_or_str_unpack_elem(tt, analyzer):
            return "tuple.target_family." + _type_family_tag(tt, analyzer)
    return "tuple.iter_shape"


def _for_each_reject_detail(stmt: TpyForEach, analyzer,
                            declared: dict[str, TpyType],
                            narrowed: AbstractSet[str]) -> str:
    """Sub-classify a rejected for-each for the fallback tally (diagnostic
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
    if analyzer.if_branch_decls.get(id(stmt)):
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


def _select_for_each_route(
        stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str],
        iterator_object_locals: 'AbstractSet[str]' = frozenset()
        ) -> _ForEachRoute:
    """Select the lowering strategy or reject from the lowering boundary."""
    if _is_range_call(stmt.iterable):
        route = _for_range_route(stmt, analyzer, declared)
    elif stmt.is_tuple_unpack:
        route = _for_tuple_unpack_route(stmt, analyzer, declared, narrowed)
    elif stmt.enum_iterable is not None:
        route = _for_enum_route(stmt, analyzer, declared)
    else:
        route = _for_consuming_route(stmt, analyzer, declared)
        if route is None:
            route = _for_each_container_route(stmt, analyzer, declared)
        if route is None:
            route = _for_iter_proto_route(stmt, analyzer, declared,
                                          iterator_object_locals)
    if route is None:
        note_detail(_for_each_reject_detail(stmt, analyzer, declared, narrowed))
        raise ThirUnsupported(stmt_reject_reason(stmt))
    return route

def _tuple_unpack_targets(stmt: TpyTupleUnpack, analyzer,
                          declared: dict[str, TpyType],
                          narrowed: AbstractSet[str],
                          hoisted_names: AbstractSet[str] = frozenset()
                          ) -> 'list[TpyType | None] | None':
    """The all-new plain value-scalar-or-str target slice of
    `_gen_tuple_unpack`'s `const auto& __tup_N = <name>;` arm: no
    ref/owned elements, every target a fresh scalar or str local (a
    discard `_` slot skips). Returns the per-target unwrapped types (None at a
    discard slot), or None when a target takes another _gen_tuple_unpack branch
    (borrow/record/reused) -- deferred rows. The source-form check (a value
    scalar-or-str tuple name) is the caller's.

    `hoisted_names` are targets used after the loop: they predecl a plain value
    slot and ASSIGN, so their `is_const_ref` flag (the fresh expensive-copy
    bind) is inert. A fresh const-ref target binds `const T& = std::get<i>`
    (the "cref" arm) and a borrow F1-record target aliases the element (the
    "ref" arm via the head's tuple_to_pointer lift); the lowering derives the
    per-target bind from is_const_ref / is_ref."""
    if any(stmt.is_owned):
        return None
    if not all(stmt.is_new):
        return None
    types: list[TpyType | None] = []
    for i, name in enumerate(stmt.targets):
        if name is None:
            types.append(None)
            continue
        if name in declared or name in narrowed:
            return None
        tt = unwrap_ref_type(stmt.target_types[i])
        if i < len(stmt.is_ref) and stmt.is_ref[i]:
            # Borrow F1-record target -> the "ref" alias bind.
            if not _f1_record(tt, analyzer):
                return None
            types.append(tt)
            continue
        # A scalar/str element: a fresh expensive-copy target binds cref, every
        # other value target binds by value; the lowering picks the arm.
        if not _scalar_or_str_unpack_elem(tt, analyzer):
            return None
        types.append(tt)
    return types

def _standalone_unpack_target_binds(
        stmt: TpyTupleUnpack, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str], blocked: AbstractSet[str]
        ) -> 'list[tuple[TpyType | None, str | None]] | None':
    """The STANDALONE unpack's per-target (unwrapped type, bind arm) list, or
    None when a target takes an unmirrored `_gen_tuple_unpack` branch. Extends
    `_tuple_unpack_targets` (kept as-is for the for-each head) with three rungs:

    - "move": an `Own[F1-record]` element moved out of the source tuple
      (`Rec a = std::move(std::get<i>(tmp));`); the target is a fresh owned
      record local (sema seeded it movable, which `lc.movable_locals` already
      carries -- the AST's sema_movable_locals promotion at this site);
    - "cref": sema's is_const_ref (a fresh expensive-copy value target, e.g.
      BigInt) -- `const T& a = std::get<i>(tmp);`, zero-copy off the tuple.
    - "assign": a REUSED plain scalar/str local (`a, b = pair()` after both
      names exist) -- the AST's declared-name tail, `name = std::get<i>(tmp);`
      (no decl; the declared entry keeps its original type, so later reads
      classify unchanged). `blocked` carries the caller's special name
      classes (pointer / rebind-slot / alias / storage-tuple / value-opt /
      frame names) whose reassign takes slot machinery, not the plain assign.

    A borrow (`is_ref`) F1-record target aliases the source tuple element,
    lifted through the caller's `tuple_to_pointer` source wrap; other borrow
    targets stay deferred."""
    out: list[tuple[TpyType | None, str | None]] = []
    for i, name in enumerate(stmt.targets):
        if name is None:
            out.append((None, None))
            continue
        if name in narrowed:
            return None
        tt = unwrap_ref_type(stmt.target_types[i])
        if i < len(stmt.is_ref) and stmt.is_ref[i]:
            # `auto&& a = unwrap_ref(tuple_elem_ref(std::get<i>(__tup)))` over
            # the borrow pointer tuple: a fresh F1-record alias only. The caller
            # gates the source form (must be a storage-form pointer-repr tuple).
            if not (stmt.is_new[i] and name not in declared
                    and _f1_record(tt, analyzer)):
                return None
            out.append((tt, "ref"))
            continue
        if not stmt.is_new[i]:
            if (name not in declared or name in blocked
                    or stmt.is_owned[i]
                    or not _scalar_or_str_unpack_elem(tt, analyzer)):
                return None
            out.append((tt, "assign"))
            continue
        if name in declared:
            return None
        if stmt.is_owned[i]:
            # target_types is already Own-stripped (sema unwraps at append).
            if not _f1_record(tt, analyzer):
                return None
            out.append((tt, "move"))
            continue
        if not (_scalar_or_str_unpack_elem(tt, analyzer)
                # A nested value-tuple target (`inner, outer = NESTED`) is a
                # plain value copy of the element.
                or _value_tuple_global(tt, analyzer) is not None):
            return None
        cref = (i < len(stmt.is_const_ref) and stmt.is_const_ref[i])
        out.append((tt, "cref" if cref else "value"))
    return out

def _tuple_unpack_source(
        stmt: TpyTupleUnpack, analyzer, declared: dict[str, TpyType],
        pointers: set[str], narrowed: AbstractSet[str]) -> 'TupleType | None':
    """The value-scalar tuple type of an admitted `a, b = <source>` source, or
    None. Four source shapes render byte-identically to `_gen_tuple_unpack`:

    - a bare name (or synthetic loop var) -> `const auto& __tup_N = name;`;
    - a value-tuple-returning free call -> `auto __tup_N = f(args);`;
    - a value-tuple-returning method call -> `auto __tup_N = obj.m(args);`
      (the same rvalue capture; storage_ret_ok threads the tuple return
      past the method result gate);
    - a value-tuple field read off an F1-record receiver -> `auto __tup_N =
      recv.field;`.

    The call/method/field arms admit exactly the shapes the `T t = f()` /
    `T t = recv.field` value-tuple decl already routes (`_storage_call_ret` +
    TpyCall / method admission and `_field_receiver_ok`), so the
    source expr lowers the same; only the source bind differs (`auto` rvalue
    capture vs the name's const-ref). Every element must be a scalar or a str
    (`_scalar_or_str_unpack_elem`): a str element's view-form target aliases the
    source tuple's element for the tuple's scope, kept aligned with
    `_tuple_unpack_targets`' matching family. Swap / literal-parallel / nested /
    starred / record-element / Optional-element / reused-target forms take other
    arms."""
    v = stmt.value
    call_src = False
    if isinstance(v, TpyName):
        if v.name not in declared or v.name in pointers or v.name in narrowed:
            _kind_detail("tuple_unpack.src_", v)
            return None
        src_raw: 'TpyType | None' = declared[v.name]
    elif isinstance(v, (TpyCall, TpyMethodCall)):
        # The method arm is the free-call sibling: `a, b = obj.pair()` binds
        # the same `auto __tup_N = <rvalue call>;` capture. The owned-tuple
        # family (Own[F1-record] elements) is call-source-only -- see below.
        src_raw = analyzer.get_expr_type(v)
        if (_storage_call_ret(src_raw, analyzer) is None
                and _owned_tuple_call_ret(src_raw, analyzer) is None):
            _kind_detail("tuple_unpack.src_", v)
            return None
        call_src = True
    elif (isinstance(v, TpyFieldAccess)
          and (_field_receiver_ok(v, declared, analyzer)
               # A value-tuple class constant (`Version.SEMVER`): the same
               # `auto __tup_N = <rvalue>;` capture over the bare qualified
               # static (the class-const arm spells it; tuple admission is
               # threaded via use.tuple_source).
               or (v.class_constant_owner is not None
                   and _value_tuple_global(
                       analyzer.get_expr_type(v), analyzer) is not None))):
        src_raw = analyzer.get_expr_type(v)
    else:
        # The source's node kind (a call that failed its gates keeps any
        # finer detail those noted -- set-if-empty).
        _kind_detail("tuple_unpack.src_", v)
        return None
    src_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(src_raw)))
    if not isinstance(src_t, TupleType):
        note_detail("tuple_unpack.source_family")
        return None
    if any(isinstance(e, OwnType) for e in src_t.element_types):
        # Own elements move OUT of the source: only the call-rvalue capture
        # is mirrored (a name source takes the AST's one-shot / last-use-move
        # / copy bind arms, a field source would copy an Own member out).
        if not call_src or _owned_tuple_call_ret(src_raw, analyzer) is None:
            note_detail("tuple_unpack.own_source_form")
            return None
        return src_t
    if not all(_scalar_or_str_unpack_elem(e, analyzer)
               or _value_tuple_global(e, analyzer) is not None
               # A pointer-repr F1-record element: the borrow-tuple unpack
               # (`a, b = it`) lifts a storage-form source via tuple_to_pointer
               # and aliases the record element into an `is_ref` target. The
               # bind admission gates non-ref record targets out; the lowering
               # gates the source form (must read from storage).
               or _f1_record(
                   unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e))),
                   analyzer)
               for e in src_t.element_types):
        note_detail("tuple_unpack.source_family")
        return None
    return src_t

def _iteration_yields_const(it: TpyExpr, lc: '_LowerCtx', analyzer) -> bool:
    """Whether iterating `it` binds the loop var const (element pointers spell
    `const T*`) -- the SUBSET of `context.iteration_yields_const` this wave
    gates: a const-ref param / alias, or a readonly method's `self.field`. The
    AST's other const-source rungs are intentionally out of scope here -- a
    const container/indirect LOCAL (`const_indirect_locals`) and a
    borrowing-view accessor call (`d.items()` / `d.values()`, const tracking
    the receiver). A case hitting one of those computes a non-const wrap that
    diverges from the AST snapshot, so the byte-diff keeps it un-migrated
    rather than shipping a mismatch (never a miscompile)."""
    if (isinstance(it, TpyFieldAccess) and isinstance(it.obj, TpyName)
            and it.obj.name == "self"
            and _param_is_const("self", lc.func, analyzer, lc.record_name)):
        return True
    if isinstance(it, TpyName):
        return _param_is_const(it.name, lc.func, analyzer, lc.record_name)
    return False

def _borrow_tuple_wrap_cpp(target_types: 'tuple', analyzer, *,
                           const_source: bool = False) -> 'str | None':
    """The borrow pointer-tuple spelling (`std::tuple<std::string_view, T*>`)
    for a `tuple_to_pointer` unpack wrap, or None if it has no pointer-repr
    element. Mirrors the AST's `resolve_tuple_pending` before `to_cpp_return`:
    `to_cpp*` do not resolve pending slots (a str element carries PendingStr
    until usage-resolved), so resolve view / int-literal elements first. A
    const source (a const loop var) spells `const T*` element pointers."""
    resolve_lit = analyzer.ctx.default_int_for_literal
    resolved = []
    for et in target_types:
        rv = _resolve_pending_view(et, analyzer)
        resolved.append(rv if rv is not None
                        else resolve_int_literals(et, resolve_lit))
    ptr_form = TupleType(tuple(resolved))
    if not ptr_form.has_pointer_repr_element():
        return None
    return (ptr_form.to_cpp_return_const() if const_source
            else ptr_form.to_cpp_return())

def _with_target_arm(item, declared: dict[str, TpyType], prescan: _Prescan,
                     analyzer, pointers: AbstractSet[str],
                     rebind_slots: AbstractSet[str],
                     ) -> 'tuple[WithTargetArm, TpyType | None] | None':
    """Classify a `with` item's as-target against `_gen_with`'s binding arms,
    or None when the item is out of the slice -- the ONE routing fact shared
    by the gate and lowering (both sides pass their own declared/pointer
    state). A reassigned F1-record target takes the `T* name = &(...)`
    pointer-local arm (PTR_DECL; the name joins the F2 pointer set), and a
    REUSE of that name by a later `with` takes the already-declared
    `name = &(...)` assign (ASSIGN_PTR) -- admitted only when the declared
    entry is the SAME record (the AST keeps the first enter type for reads)
    and the name is a plain pointer-local (a rebind-slot's reseats are rvalue
    rebinds, a different render). Every other already-declared reuse -- a
    value or optional-slot target -- is the BUGS.md ill-formed
    `x = &(__enter__())` family: unsupported, never mirrored. Bodies that
    first-declare post-with-visible vars still reject via `if_branch_decls`
    (branch-hoist machinery the emitter does not reproduce)."""
    if item.target is None:
        return WithTargetArm.NONE, None
    et = item.enter_type
    if not isinstance(et, TpyType):
        return None
    resolved = unwrap_readonly(unwrap_send_sync(et))
    if item.target in declared:
        if (item.target in pointers
                and item.target not in rebind_slots
                and not et.is_value_type()
                and _f1_record(resolved, analyzer)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    declared[item.target]))) == resolved):
            return WithTargetArm.ASSIGN_PTR, resolved
        return None
    if et.is_value_type():
        # `auto <name> = __enter__();` -- a value copy. Scalars, Char, enums,
        # and str-slice values route; the declared entry carries the RESOLVED
        # enter type, so body reads classify exactly like the AST's
        # `var_types[name] = enter_type` (an owned `str` return deduces
        # `std::string` -> STORAGE, a `StrView` return `std::string_view` ->
        # BORROW via `_str_name_form`; the target is never a param name).
        if not (_eligible_scalar(resolved)
                or _eligible_char(resolved)
                or _eligible_enum(resolved, analyzer) is not None
                or _resolved_str_value(resolved, analyzer) is not None):
            return None
        return WithTargetArm.VALUE, resolved
    if not _f1_record(resolved, analyzer):
        return None
    if item.target in prescan.reassigned:
        # NB the scan also counts the with-rebind itself in
        # `rvalue_reassigned`, so that set cannot distinguish the pure
        # two-with shape from a mixed plain-rvalue reassign; the mixed
        # family is kept out by the ASSIGN/rebind gates at the reassign
        # site instead.
        return WithTargetArm.PTR_DECL, resolved
    return WithTargetArm.REF, resolved

def _try_hoist_type_ok(vtype: TpyType, analyzer) -> bool:
    """A hoisted predecl type the slice renders -- the plain-value tail arm of
    `_emit_branch_decls` (`{cpp_type} {name};`), restricted to the same value
    family a first var-decl admits. Readonly wrappers reject: the AST routes
    those through the const/pointer arms. A VALUE-repr Optional[scalar]
    predecls the bare `std::optional<T> r;` slot (default-empty), its later
    assigns riding the value-opt rows; pointer-repr Optionals stay out."""
    if isinstance(vtype, ReadonlyType):
        return False
    if isinstance(vtype, OptionalType):
        return _value_opt_scalar(vtype, analyzer) is not None
    return (_eligible_scalar(vtype) or _eligible_char(vtype)
            or _eligible_enum(vtype, analyzer) is not None
            or _resolved_str_value(vtype, analyzer) is not None
            or _resolved_bytes_value(vtype, analyzer) is not None
            or _is_string_owned(vtype)
            or _eligible_value_union(vtype) is not None
            or _slice_object_type(vtype))

def _lower_hoist_predecls(hoists: dict, declared: dict[str, TpyType],
                          lc: '_LowerCtx', witness_tag: str
                          ) -> list[tuple[str, str]]:
    """Chain-head predecls for the branch-first-decls shared by if / try /
    with. A name spells its sema-resolved view type (render_type's default
    resolves neither PendingStr nor PendingBytes), enters the CALLER's
    `declared` at function scope, and keeps the raw binding type there --
    the same shape a normal str/bytes first-decl stores. A name already in
    `declared` (bound outside an enclosing loop) skips its predecl, matching
    the AST's declared_vars check. The witness distinguishes the call site."""
    hoist_decls: list[tuple[str, str]] = []
    for name, raw in hoists.items():
        if name in declared:
            continue
        vtype = unwrap_ref_type(raw)
        hoist_decls.append(_value_hoist_entry(name, vtype, declared, lc))
    if hoist_decls:
        _witness(witness_tag)
    return hoist_decls


def _rebind_rvalue_source_ok(init: TpyExpr, target_t: TpyType,
                             analyzer) -> bool:
    """The rvalue shapes a slot rebind admits (`__slot_N = <init>`): a
    shape-checked container literal, an F1-record rvalue, or a
    container-returning by-value/Own call (the storage-call family -- the
    AST rebind path is source-shape-blind past is_rvalue_source; these are
    the vetted slices of it)."""
    if isinstance(init, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
        return _container_literal_shape_ok(init, target_t, analyzer)
    if _record_rvalue_source_shape(init, analyzer):
        return True
    if (isinstance(init, (TpyCall, TpyMethodCall))
            and is_rvalue_source(analyzer, init)):
        fam = _storage_call_ret(analyzer.get_expr_type(init), analyzer)
        return fam is not None and (_storage_call_container(fam)
                                    or is_bytearray_type(fam))
    return False


def _rebind_rvalue_use(init: TpyExpr) -> '_ExprUse':
    """A slot assign is a storage sink at statement position: a
    container-returning call needs the STORAGE result use (the storage-decl
    sink's admission) and may hoist arg temps."""
    if isinstance(init, (TpyCall, TpyMethodCall)):
        return _ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True)
    return _ExprUse()


def _value_hoist_entry(name: str, vtype: TpyType,
                       declared: dict[str, TpyType],
                       lc: '_LowerCtx') -> tuple[str, str]:
    """One value-family hoist predecl entry: resolve the str/bytes view
    spelling, render, and register the raw binding type in the caller's
    `declared` -- the single render shared by the try/with tail and the
    if-flavor cascade so the two cannot drift."""
    render_src = (_resolved_str_value(vtype, lc.analyzer)
                  or _resolved_bytes_value(vtype, lc.analyzer)
                  or vtype)
    declared[name] = vtype
    return (name, lc.render_type(render_src))


def _lower_if_hoist_predecls(stmt: TpyIf, hoists: dict,
                             declared: dict[str, TpyType], lc: '_LowerCtx',
                             in_branch: bool
                             ) -> tuple[list[tuple[str, str]],
                                        list[tuple[str, str]]]:
    """The if-chain flavor of `_emit_branch_decls`: classify each sema-hoisted
    branch-first decl in dict order (the AST's cascade order) and register its
    read/write model on `lc`. Returns `(hoist_decls, hoist_slots)` for THIRIf:
    every predecl renders `{cpp_type} {name};` at the chain head; a slot entry
    additionally pre-decls that name's `std::optional<T> __slot_N;` rebind slot
    (allocated at emit, mirroring `ctx.slots`). Non-value flavors:

      * @dynamic protocol -> `Base* name;` + the DYN_PROTOCOL reseat arm.
      * plain non-value, not reassigned, not borrow-only -> the
        OPTIONAL_STORAGE local (`std::optional<T> name;`, plain assigns,
        deref reads -- `_emit_branch_decls`' optional_locals arm).
      * reassigned / borrow-only -> `T* name;` pointer-local; an
        rvalue-reassigned name gets the if-head rebind slot, otherwise
        reseats allocate one lazily at function top (BRANCH_RVALUE).

    Raises ThirUnsupported (whole-body fallback) for the shapes outside this
    slice: const/readonly hoists, borrow-form tuples, resumable bodies (the
    rebind hoists have no drain point in the leaf emitters)."""
    analyzer = lc.analyzer
    borrow_decls = analyzer.function_stmt_borrow_decls.get(id(lc.func), {})
    ever_owned = analyzer.function_ever_owned_locals.get(id(lc.func), set())
    hoist_decls: list[tuple[str, str]] = []
    hoist_slots: list[tuple[str, str]] = []
    for name, raw in hoists.items():
        if name in declared:
            continue
        if name in lc.prescan.native_globals:
            note_detail("if.hoist_native_global")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        var_type = unwrap_ref_type(raw)
        # An un-annotated container hoist (`xs = [1]` / `xs = [2]`) carries
        # the pending literal type; resolve it up front so the registered
        # binding, the flavor gates, and the render all see the final
        # container (the reseat shape checks compare against `declared`).
        var_type = resolve_pending_container(var_type, analyzer) or var_type
        is_nonvalue_flavor = (
            (isinstance(var_type, NominalType) and is_dyn_protocol(var_type))
            or is_plain_nonvalue(var_type)
            or (isinstance(var_type, OptionalType)
                and var_type.uses_pointer_repr()))
        if is_nonvalue_flavor and in_branch:
            # An INNER-scope if (any in_branch body: branch, loop, with,
            # try) registers its hoist names into that scope's `declared`
            # COPY, but Python names are function-scoped and the AST tracks
            # them in flat declared_vars -- a sibling or post-scope
            # statement on the same name would classify differently on the
            # two paths. Non-value hoists stay AST there until THIR's hoist
            # registration is function-scoped; only function-top-level ifs
            # route.
            note_detail("if.hoist_inner_scope")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if isinstance(var_type, NominalType) and is_dyn_protocol(var_type):
            # `Base* name;` + per-reseat adapter slots (the DYN_PROTOCOL
            # rebind arm). Those slots hoist to function top -- no drain
            # point in a resumable leaf emitter, same guard as the reseat.
            if lc.func.is_generator or lc.func.is_async:
                note_detail("if.hoist_dyn_resumable")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            hoist_decls.append(
                (name, f"{dynamic_base_name(var_type, analyzer)}*"))
            lc.pointers.add(name)
            lc.dyn_protocol_locals.add(name)
            declared[name] = var_type
            _witness("if.hoist_dyn_protocol")
            continue
        # Pointer-repr Optional predecls the bare inner `T* name;` (nullable
        # pointer-local); readonly inners are the const-indirect arm (a later
        # rung, like every const hoist below).
        resolve_type = var_type
        if (isinstance(var_type, OptionalType)
                and var_type.uses_pointer_repr()):
            resolve_type = var_type.inner
        if isinstance(resolve_type, ReadonlyType):
            note_detail("if.hoist_const")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if is_plain_nonvalue(var_type) or resolve_type is not var_type:
            if lc.func.is_generator or lc.func.is_async:
                # Both non-value flavors hoist storage to function top on
                # reseats; resumable leaves cannot drain those lines.
                note_detail("if.hoist_nonvalue_resumable")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if name in lc.prescan.move_through:
                # `_needs_indirection` exempts move-through names -- the AST
                # gives them the plain storage decl, a different arm.
                note_detail("if.hoist_move_through")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            borrow_only = (name in borrow_decls and name not in ever_owned)
            if borrow_decls.get(name, False):
                # The borrow-decl const bit makes the pointer-local
                # const-indirect (`const T* name;`) -- the const rung.
                note_detail("if.hoist_const")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            reassigned = name in lc.prescan.reassigned
            cpp = lc.render_type(resolve_type)
            if (is_plain_nonvalue(var_type) and not reassigned
                    and not borrow_only):
                # OPTIONAL_STORAGE: single-bind rvalue local -- plain
                # assigns engage the optional, reads deref through it.
                hoist_decls.append((name, f"std::optional<{cpp}>"))
                lc.pointers.add(name)
                lc.optional_locals.add(name)
                lc.movable_locals.add(name)
                declared[name] = var_type
                _witness("if.hoist_optional_storage")
                continue
            hoist_decls.append((name, f"{cpp}*"))
            lc.pointers.add(name)
            if name in lc.prescan.rvalue_reassigned:
                hoist_slots.append((name, cpp))
                lc.rebind_slot_locals.add(name)
            else:
                lc.branch_hoisted.add(name)
            declared[name] = var_type
            _witness("if.hoist_ptr_local")
            continue
        if not _try_hoist_type_ok(var_type, analyzer):
            note_detail("if.hoist_type")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        hoist_decls.append(_value_hoist_entry(name, var_type, declared, lc))
        _witness("if.hoist_decl")
    return hoist_decls, hoist_slots

def _handler_binding_type(h: TpyExceptHandler, analyzer) -> 'NominalType | None':
    """The `as`-binding's type, exactly as sema binds it (`NominalType` over
    the registry record; `h.exception_type` is sema-qualified in place)."""
    rec = analyzer.registry.find_record_by_qname(h.exception_type)
    if rec is None:
        return None
    return NominalType(rec.name, _module_qname=rec.qualified_name())

def _error_return_stmt_fi(expr: TpyExpr, analyzer):
    """Mirror of `_get_error_return_fi`: the FunctionInfo when `expr` is an
    @error_return call the AST statement handlers intercept (coerce-peeled
    call or method call). Anything this matches is statement-handled on the
    AST path, so lowering must either take the bind/discard arm or fall
    back -- letting it ride the generic expression path would render the
    expression-level unwrap where the AST renders the `__try_tmp_N` block."""
    if isinstance(expr, TpyCoerce):
        return _error_return_stmt_fi(expr.expr, analyzer)
    fi = None
    if isinstance(expr, (TpyCall, TpyMethodCall)):
        fi = getattr(expr, 'resolved_function_info', None)
    if fi is not None and fi.error_return_type:
        return fi
    return None

def _reject_nested_error_return_arg(stmt: TpyStmt, call: TpyCall,
                                    analyzer) -> None:
    """GATE (never mirror): a raw-lowered (statement-handled) @error_return
    call whose ARG SUBTREE carries another @error_return call. The AST
    render for this shape is ILL-FORMED C++ (its statement-handled flag is
    consumed by whichever call renders first -- the inner arg -- so the
    outer call double-unwraps), and an ill-formed AST render is
    gate-rejected, never mirrored, never silently fixed. Shared by all
    three error_return_raw sites (bind / discard / return
    pass-through)."""
    for arg in call.args:
        for node in _walk(arg):
            fi = (getattr(node, 'resolved_function_info', None)
                  if isinstance(node, (TpyCall, TpyMethodCall)) else None)
            if fi is not None and fi.error_return_type:
                note_detail("error_return.nested_call")
                raise ThirUnsupported(stmt_reject_reason(stmt))

def _lower_error_return_bind(stmt, name: str, init: TpyExpr, er_fi, vtype,
                             lc: '_LowerCtx', declared: dict[str, TpyType],
                             loc) -> THIRErrorReturnBind:
    """The statement-level unwrap bind (var-decl or name-assign init'd by a
    direct @error_return call). Routed slice: a bare free-call init into a
    plain owned local -- the borrow-aliasing result (the unwrap must alias
    live storage, `_error_return_result_aliases`), pointer/rebind/alias/
    frame targets, and method-call / coerce-wrapped inits stay AST."""
    analyzer = lc.analyzer
    if not isinstance(init, TpyCall):
        note_detail("error_return.stmt_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    _reject_nested_error_return_arg(stmt, init, analyzer)
    if call_returns_cpp_ref(analyzer, er_fi):
        note_detail("error_return.alias_bind")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if (name in lc.pointers or name in lc.rebind_slot_locals
            or name in lc.ref_alias_locals
            or name in lc.storage_tuple_locals
            or name in lc.value_opt_locals
            or name in lc.frame_slots):
        note_detail("error_return.bind_target")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    decl_cpp = None
    if name not in declared:
        # First binding: predecl `T name;` before the unwrap block (the goto
        # / early return would cross an initialized declaration). The AST
        # spells the callee's SUCCESS type (`fi.return_type`), not the decl
        # annotation.
        var_type = er_fi.return_type
        bare = (unwrap_readonly(unwrap_ref_type(var_type))
                if isinstance(var_type, TpyType) else None)
        inner = bare.wrapped if isinstance(bare, OwnType) else bare
        # The predecl needs a default-constructible plain-value slot whose
        # later reads route on the declared type: the scalar/char/enum/
        # F1-record families. Owned str/bytes first-decls stay AST (their
        # owned-local read model keys on the hoist machinery's registration).
        if not (_eligible_scalar(inner) or _eligible_char(inner)
                or _eligible_enum(inner, analyzer) is not None
                or _f1_record(inner, analyzer)):
            note_detail("error_return.bind_slot")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        decl_cpp = unwrap_ref_type(var_type).to_cpp()
        declared[name] = vtype
    call = _lower_expr(init, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.STORAGE,
                                    allow_temps=True),
                       error_return_raw=True)
    return THIRErrorReturnBind(name=name, call=call, decl_cpp=decl_cpp,
                               loc=loc)

def _owned_record_decl_ok(stmt: TpyVarDecl, vtype: 'TpyType | None',
                          prescan: _Prescan,
                          declared: dict[str, TpyType], analyzer,
                          narrowed: 'set[str] | frozenset[str]'
                          = frozenset()) -> bool:
    """A single-assignment owned record local from a record-rvalue init --
    `Box b = Box(n);` / `Box x = make(1);`, the binding classifier's
    plain-value-local arm (no indirection, dot access; sema's movable set
    already tracks it for last-use moves). Reassigned names take the
    REBIND_SLOT machinery, hoisted / move-through ones their own AST arms.
    Wrapper-annotated decls (`readonly[T]` / `Own[T]` locals) are sema
    errors, so `_var_decl_type`'s unwrapping never smuggles one in; the
    bare-NominalType check is defensive."""
    return (stmt.name not in prescan.reassigned
            and stmt.name not in prescan.hoisted
            and stmt.name not in prescan.move_through
            and isinstance(vtype, NominalType)
            and _f1_record(vtype, analyzer)
            and stmt.init is not None
            and (_record_rvalue_source_shape(stmt.init, analyzer)
                 or (isinstance(stmt.init, TpyMethodCall)
                     and _f1_record(analyzer.get_expr_type(stmt.init), analyzer)
                     and is_rvalue_source(analyzer, stmt.init))))

def _lower_borrow_local(stmt: TpyVarDecl, vtype: TpyType, binding: 'LocalBinding',
                        is_const: bool, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc) -> THIRVarDecl:
    """Lower a non-value borrow local's first declaration. REF_ALIAS binds a `T&`
    alias of the field's storage directly (no conversion node). POINTER lifts a
    plain-record lvalue to a reseatable `T*` via THIRFormConvert (`&(...)`).
    OPTIONAL_TO_PTR lifts the storage `optional<Inner>` to a borrow `Inner*` via
    THIRFormConvert (`::tpy::optional_to_ptr`), so its decl type is the inner.
    REBIND_SLOT (F2d) binds a plain-record rvalue (a ctor / by-value call) and
    the emitter materializes the two-slot `__slot_N` machinery; the init lowers
    as a plain value-form call (no conversion node)."""
    if binding is LocalBinding.REBIND_SLOT:
        # The slot init is target-threaded like the AST's
        # `gen_expr(init, target_type)` -- load-bearing for a container
        # literal's element renders, inert for the record-rvalue call.
        if isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                  TpySetLiteral)):
            _witness("decl.container_rebind_slot")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype,
            init=_lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                target_type=vtype),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if isinstance(stmt.init, (TpyCall, TpyMethodCall)):
        # REF_ALIAS from a borrow-record-returning call: the `T&` binds the
        # callee's returned reference directly (`Pair& p = shared(x);`,
        # `MyNumber& num = h.get_item();`), so the init is the plain
        # value-form call -- no conversion node.
        _witness("decl.record_borrow_call")
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype,
            init=_lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
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
            src = _lower_expr(stmt.init, lc, declared, subscript_prechecked=True)
        elif isinstance(stmt.init, TpyName):
            src = _lower_expr(stmt.init, lc, declared)
        else:
            src = _lower_field_source(stmt.init, lc, declared)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=src,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.POINTER and isinstance(stmt.init, TpyName):
        # A reassigned bare record-name alias: `[const] T* x = &(a);`. The bare
        # name renders as a plain lvalue; the THIRFormConvert emits the `&(...)`.
        src = _lower_expr(stmt.init, lc, declared)
        if (src.form is Form.BORROW and src.result_type == vtype
                and not lc.resumable_leaf_mode):
            # Same trap as the reseat arms: a same-type BORROW-form name
            # makes the convert the no-op node validate_function
            # hard-rejects -- the T&/T* representation change has no form
            # spelling yet, so the shape stays on AST. Resumable leaves are
            # exempt like the twin guards (they never run whole-function
            # validation).
            note_detail("decl.ptr_alias_borrow")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        convert = THIRFormConvert(
            result_type=vtype, value=src,
            form=Form.BORROW, is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    field = _lower_field_source(stmt.init, lc, declared)
    if binding is LocalBinding.POINTER:
        convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                                  is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    inner = vtype.inner  # OptionalType(Inner) -- the borrow points at Inner
    convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                              is_const=is_const, loc=loc)
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=convert,
        cpp_type=lc.render_type(inner), form=Form.BORROW, is_const=is_const,
        cpp_local_representation=binding, loc=loc)

def _opt_slot_rvalue_shape(init: TpyExpr, inner: TpyType, analyzer) -> bool:
    """The rvalue init/reseat shapes an OPT_PTR_SLOT local admits: an F1-record
    ctor / by-value free call (`_record_rvalue_source_shape`) or by-value method
    call whose expression type is EXACTLY the optional's inner record. A
    subclass rvalue would retype the slot (the polymorphic-Optional arm) and an
    Optional-returning call needs the `optional_to_ptr` slot lift -- both stay
    on the AST path (the type-equality check keeps them out: their expression
    types are a subclass / an OptionalType, never `inner`)."""
    it = analyzer.get_expr_type(init)
    if it is None:
        return False
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    if it_u != inner:
        return False
    return (_record_rvalue_source_shape(init, analyzer)
            or (isinstance(init, TpyMethodCall)
                and _f1_record(it_u, analyzer)
                and is_rvalue_source(analyzer, init)))


def _ptr_union_slot_kind(init: TpyExpr, ptr_u: 'UnionType',
                         declared: dict[str, TpyType], lc: '_LowerCtx',
                         analyzer) -> 'PtrSlotKind | None':
    """Classify a ptr-variant union init/reseat source outside the bare-copy /
    field-lift slice: a concrete-MEMBER F1-record rvalue (UNION_RVALUE -- the
    value-variant `__slot_N` + `to_ptr_variant` lift) or a concrete-member
    lvalue NAME (UNION_ADDR -- `v{&(name)}`). None for everything else
    (const-rooted sources, pointer-form names, non-member types)."""
    it = analyzer.get_expr_type(init)
    if it is None:
        return None
    it_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(it)))
    if isinstance(it_u, OwnType):
        it_u = unwrap_readonly(it_u.wrapped)
    # A whole-union rvalue (an `Own[A | B]`-returning call) takes the same
    # value-variant slot + lift as a member rvalue -- the AST rvalue branch
    # is source-type-blind (member F1-ness is already `_eligible_ptr_union`'s
    # admission). Anything else must be a concrete F1 member.
    whole_union = it_u == ptr_u
    if not whole_union:
        if not any(m == it_u for m in ptr_u.members):
            return None
        if not _f1_record(it_u, analyzer):
            return None
    if whole_union:
        # An `Own[A | B]`-returning free call: the F1-record type gate of
        # `_record_rvalue_source_shape` cannot apply, so run the
        # return-type-blind callee/arg-shape half directly.
        if (isinstance(init, TpyCall)
                and is_rvalue_source(analyzer, init)
                and _rvalue_free_call_shape(init, analyzer)):
            return PtrSlotKind.UNION_RVALUE
        return None
    if (is_rvalue_source(analyzer, init)
            and _record_rvalue_source_shape(init, analyzer)):
        return PtrSlotKind.UNION_RVALUE
    if isinstance(init, TpyName) and init.name in declared:
        # The name must render bare (a plain storage local) and mutable:
        # pointer-form and const-rooted sources are later rungs.
        if (init.name in lc.pointers or init.name in lc.rebind_slot_locals
                or init.name in lc.const_locals
                or _param_is_const(init.name, lc.func, analyzer,
                                   lc.record_name)):
            return None
        return PtrSlotKind.UNION_ADDR
    return None


def _lower_opt_ptr_slot_decl(stmt: TpyVarDecl, vtype: 'OptionalType',
                             lc: _LowerCtx, declared: dict[str, TpyType],
                             loc) -> THIRPtrLocalDecl:
    """Gate + lower an OPT_PTR_SLOT first declaration: a pointer-repr
    `Optional[T]` local whose init is a None literal (`T* x = nullptr;`) or an
    F1-record rvalue (`T __slot_N = ...; T* x = &__slot_N;`), mirroring
    `_gen_pointer_local_init`'s None / rvalue branches. The emitter allocates
    the `__slot_N` storage and, when the prescan marks the name
    rvalue-reassigned, the `std::optional<T>` rebind-slot pre-decl (reseats
    then ride THIRAssign's rebind-slot arm / the None-reseat rebind node).
    Const indirection, non-F1 pointees, Own[Opt] lifts and polymorphic slots
    reject with named details -- their arms are later rungs."""
    analyzer = lc.analyzer
    inner = vtype.inner
    # `const T*` when the pointee is a readonly source: an explicit readonly[T]
    # inner, or sema holding Optional[readonly[T]] for a plain-annotated decl (a
    # readonly-seeded None local). These are the only two arms of the AST's
    # `_is_const_indirect` an OPT_NONE (None-init) decl can reach -- the
    # init-alias / method-ref / const-name arms all need a non-None init, which
    # is the rvalue branch (inherently non-const: `_opt_slot_rvalue_shape`
    # admits only a fresh F1-record rvalue, never a readonly-typed source).
    sema_var_t = analyzer.var_types.get(id(stmt))
    is_const = (
        isinstance(inner, ReadonlyType)
        or (isinstance(sema_var_t, OptionalType)
            and isinstance(sema_var_t.inner, ReadonlyType)))
    pointee = unwrap_readonly(inner)
    if not _f1_record(pointee, analyzer):
        note_detail("decl.opt_slot_pointee")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    needs_rebind = stmt.name in lc.prescan.rvalue_reassigned
    if isinstance(stmt.init, TpyNoneLiteral):
        kind = PtrSlotKind.OPT_NONE
        init: THIRExpr | None = None
        _witness("decl.opt_slot_none")
    else:
        # A const rvalue source is not part of this slice (the rvalue arm below
        # emits mutable-pointee storage); keep it a later rung.
        if is_const:
            note_detail("decl.opt_slot_const")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if not _opt_slot_rvalue_shape(stmt.init, pointee, analyzer):
            note_detail("decl.opt_slot_source")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        kind = PtrSlotKind.OPT_RVALUE
        init = _lower_expr(stmt.init, lc, declared,
                           use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                           target_type=pointee)
        _witness("decl.opt_slot_rvalue")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=kind, init=init,
        cpp_type=lc.render_type(pointee), needs_rebind_slot=needs_rebind,
        is_const=is_const, loc=loc)


def _lower_record_ptr_slot_decl(stmt: TpyVarDecl, vtype: 'TpyType | None',
                                lc: _LowerCtx, declared: dict[str, TpyType],
                                loc) -> 'THIRPtrLocalDecl | None':
    """First decl of an escape-hoist PLAIN-record pointer-local from a
    record-rvalue init -- the two flavors of `_gen_pointer_local_init`'s
    rvalue branch the shared classifier leaves at OTHER: a HOISTED name
    (`T* x = &*(__slot_N = init);` over a function-top `std::optional<T>`
    pre-decl) and a name-reassigned (not rvalue-reassigned) local
    (`T __slot_N = init;\\nT* x = &__slot_N;` -- the REBIND_SLOT render
    minus the rebind slot). Returns None when the decl is not this shape
    (normal decl flow continues). The rvalue type must EQUAL the declared
    record -- a subclass rvalue retypes the slot (the polymorphic arm,
    rejected), mirroring `_opt_slot_rvalue_shape`'s discipline."""
    analyzer = lc.analyzer
    if not isinstance(vtype, NominalType) or not _f1_record(vtype, analyzer):
        return None
    hoisted = stmt.name in lc.prescan.hoisted
    reassigned = stmt.name in lc.prescan.reassigned
    rvalue_reassigned = stmt.name in lc.prescan.rvalue_reassigned
    if not hoisted and (not reassigned or rvalue_reassigned):
        # Single-assignment rvalues are plain value decls; rvalue-reassigned
        # ones are the REBIND_SLOT binding -- both other paths.
        return None
    if stmt.init is None or not _record_rvalue_source_shape(stmt.init,
                                                            analyzer):
        return None
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
    init = _lower_expr(stmt.init, lc, declared,
                       use=_ExprUse(result=_ExprResultUse.BORROW_BIND),
                       target_type=vtype)
    if hoisted:
        # A resumable body's leaves emit through non-draining leaf emitters
        # (hoist_lines has no function-top drain there) -- reject like the
        # DYN_PROTOCOL rebind hoist.
        if lc.func.is_generator or lc.func.is_async:
            note_detail("decl.record_slot_resumable")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        kind = PtrSlotKind.RECORD_HOISTED
        needs_rebind = rvalue_reassigned
        lc.unhandled_hoists.discard(stmt.name)
        _witness("decl.record_slot_hoisted")
    else:
        kind = PtrSlotKind.RECORD_RVALUE
        needs_rebind = False
        _witness("decl.record_slot_rvalue")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=kind, init=init,
        cpp_type=lc.render_type(vtype), needs_rebind_slot=needs_rebind,
        loc=loc)


def _lower_dyn_protocol_decl(stmt: TpyVarDecl, vtype: 'TpyType | None',
                            lc: _LowerCtx, declared: dict[str, TpyType],
                            loc) -> 'THIRPtrLocalDecl | None':
    """First decl of a @dynamic protocol local (`p: P = Concrete(...)`): a
    concrete/adapter `__slot_N{init}` storage + a protocol `Base* p = &__slot_N;`
    alias, mirroring `_gen_dynamic_protocol_init`'s non-erased arm. Returns None
    when `vtype` is not a @dynamic protocol (normal decl flow continues); raises
    ThirUnsupported for the deferred @dynamic variants (rebind, already-erased)
    so the whole body falls back with a named detail."""
    if not (isinstance(vtype, NominalType) and is_dyn_protocol(vtype)):
        return None
    analyzer = lc.analyzer
    # An `Own[dyn-protocol]` local is the HEAP-OWNED `unique_ptr<Base>` form
    # (the AST's `_gen_dynamic_protocol_own_arg`), NOT this non-owning
    # stack-slot `Base*` alias -- the AST's `_resolve_target_type` keeps the
    # `Own` for exactly this reason. `_var_decl_type` strips it before we see
    # `vtype`, so re-check the sema binding type and defer, keeping the two
    # ownership shapes distinct rather than relying on the erased-source guard
    # below to catch it incidentally.
    sema_var_t = analyzer.var_types.get(id(stmt))
    if sema_var_t is not None and isinstance(
            unwrap_readonly(unwrap_send_sync(sema_var_t)), OwnType):
        note_detail("decl.dyn_protocol_own")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if stmt.init is None:
        note_detail("decl.dyn_protocol_noinit")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # A HOISTED (escaping) @dynamic local's first decl may take a different
    # shape than this direct init slot -- a later rung.
    if stmt.name in lc.prescan.hoisted:
        note_detail("decl.dyn_protocol_hoisted")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # A REASSIGNED local's FIRST decl emits identically to a non-reassigned one
    # (the direct init slot); its reseats take the rebind arm (registered below).
    reassigned = (stmt.name in lc.prescan.reassigned
                  or stmt.name in lc.prescan.rvalue_reassigned)
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
        if reassigned:
            lc.dyn_protocol_locals.add(stmt.name)
        _witness("decl.dyn_protocol_erased")
        return THIRPtrLocalDecl(
            name=stmt.name, resolved_type=vtype,
            kind=PtrSlotKind.DYN_PROTOCOL_ERASED, init=erased,
            base_cpp=dynamic_base_name(vtype, analyzer), loc=loc)
    concrete_cpp = lc.render_type(concrete_type)
    if record_inherits_dynamic(concrete_type, vtype, analyzer):
        slot_cpp = concrete_cpp  # direct inheritance -- plain concrete slot
    else:
        slot_cpp = dynamic_adapter_type(vtype, concrete_cpp, analyzer)
    init = _lower_expr(stmt.init, lc, declared, target_type=concrete_type)
    if reassigned:
        lc.dyn_protocol_locals.add(stmt.name)
    _witness("decl.dyn_protocol")
    return THIRPtrLocalDecl(
        name=stmt.name, resolved_type=vtype, kind=PtrSlotKind.DYN_PROTOCOL,
        init=init, cpp_type=slot_cpp,
        base_cpp=dynamic_base_name(vtype, analyzer), loc=loc)


def _lower_dyn_erased_source(init: TpyExpr, lc: _LowerCtx,
                             declared: dict[str, TpyType]) -> 'THIRExpr | None':
    """The deref'd source of an already-erased @dynamic assign (`p2 = p1`):
    `(*p1)` for a protocol pointer-local name, a bare `pet` for a `Pet&`
    param/reference. Returns None for a non-name source (a protocol call /
    field / subscript keeps its own deref rules, and a protocol-returning call
    does not itself route yet -- a later rung)."""
    if not isinstance(init, TpyName):
        return None
    src = _lower_expr(init, lc, declared)
    if not isinstance(src, THIRName):
        return None
    return replace(src, deref=True) if init.name in lc.pointers else src


@contextmanager
def _nested_def_lowering_scope(lc: _LowerCtx, func: TpyFunction):
    """The mirror of codegen's `nested_def_emission_scope` + the local-scope
    snapshot: swap in the nested function's per-function state (its own
    prescan return slots / reassigned sets, param-seeded pointer and movable
    entries), INHERIT the outer classification sets (the AST lambda body
    inherits ctx local state -- a captured name keeps its outer render), and
    restore everything after so body-added classifications don't leak out.
    The receiver is cleared: a `self` read inside the lambda has no capture
    (gate-rejected up front; clearing keeps a slipped-through read failing
    loudly instead of spelling `this->`)."""
    saved = (lc.func, lc.prescan, lc.self_receiver,
             set(lc.const_locals), set(lc.pointers),
             set(lc.rebind_slot_locals), set(lc.dyn_protocol_locals),
             set(lc.iterator_object_locals),
             set(lc.ref_alias_locals),
             set(lc.value_opt_locals), set(lc.value_opt_view_locals),
             set(lc.storage_tuple_locals),
             set(lc.movable_locals), lc.narrow, dict(lc.inline_narrowed))
    # The hoist-residue bookkeeping is per-function: seed the NESTED
    # function's own hoist facts (its try lowering drains them;
    # `_lower_nested_def` residue-checks after the body, mirroring
    # lower_function) and restore the OUTER set object untouched -- a
    # same-named nested try-hoist must not drain the outer's entry.
    saved_hoists = lc.unhandled_hoists
    lc.unhandled_hoists = set(
        lc.analyzer.function_hoisted_vars.get(id(func), ()))
    outer_prescan = lc.prescan
    prescan = _Prescan(func, lc.analyzer)
    # Module-level facts carry over; the nested func has no global decls
    # (gate-rejected), so the seeded-globals gating fields stay empty.
    prescan.native_globals = outer_prescan.native_globals
    prescan.global_readonly = outer_prescan.global_readonly
    prescan.global_cpp = outer_prescan.global_cpp
    # codegen's nested_def_emission_scope swaps ONLY the return/error/frame
    # facts -- the reassigned/hoisted/move-through seeding stays the OUTER
    # function's (setup_body_scope runs once per outer body), so the body's
    # binding classification must read the same sets to stay byte-identical.
    prescan.reassigned = outer_prescan.reassigned
    prescan.rvalue_reassigned = outer_prescan.rvalue_reassigned
    prescan.hoisted = outer_prescan.hoisted
    prescan.move_through = outer_prescan.move_through
    lc.func = func
    lc.prescan = prescan
    lc.self_receiver = None
    # Deliberately NO param seeding: the AST's `_gen_nested_def` never runs
    # `seed_param_locals` for a lambda (it only adds names to
    # local_scope_names), so a nested param must not enter `pointers` /
    # `movable_locals` -- THIR classifying it would move/deref where the AST
    # does not. The param shapes that would NEED seeding (Optional / Own /
    # value-opt) are rejected by `_lower_nested_def`'s param gate.
    # NB `function_movable_locals` / `function_hoisted_vars` hold NO entries
    # for nested funcs today (sema's nested_def_scope discards the nested
    # body's facts unstored), so the hoist seed above is empty by
    # construction -- kept so the residue check self-activates if sema ever
    # stores them.
    lc.narrow = lc.narrow.snapshot()
    lc.inline_narrowed = {}
    try:
        yield
    finally:
        (lc.func, lc.prescan, lc.self_receiver, lc.const_locals, lc.pointers,
         lc.rebind_slot_locals, lc.dyn_protocol_locals,
         lc.iterator_object_locals,
         lc.ref_alias_locals, lc.value_opt_locals,
         lc.value_opt_view_locals,
         lc.storage_tuple_locals, lc.movable_locals,
         lc.narrow, lc.inline_narrowed) = saved
        lc.unhandled_hoists = saved_hoists


def _lower_nested_def(stmt: TpyNestedDef, scope: '_LowerScope') -> THIRStmt:
    """Lower `def name(...)` in a function body to `_gen_nested_def`'s lambda:
    the capture list spelled purely from sema's node facts, params and the
    non-void trailing return type through the resolver, and the body lowered
    under the nested function's own per-function state over the outer
    `declared`. Out-of-slice shapes raise and fall the OUTER body back."""
    lc = scope.lc
    analyzer = lc.analyzer
    func = stmt.func
    loc = getattr(stmt, "loc", None)
    begin_stmt()
    if func.is_async or func.is_generator:
        note_detail("nesteddef.resumable_func")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if func.type_params or func.error_return is not None:
        note_detail("nesteddef.signature")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if any(d is not None for d in func.defaults):
        note_detail("nesteddef.param_default")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if analyzer.function_global_decls.get(id(func)):
        note_detail("nesteddef.global_decl")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    if "self" in stmt.captured_names:
        note_detail("nesteddef.self_capture")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # A narrowed capture's reads rename to an OUTER extraction alias the
    # capture list does not carry -> AST path.
    if any(n in lc.narrow.narrowed for n in stmt.captured_names):
        note_detail("nesteddef.narrowed_capture")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    # Capture list -- _gen_nested_def's spelling over the node facts.
    if stmt.captured_names:
        if stmt.escapes:
            parts = []
            for n in stmt.captured_names:
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
                f"&{escape_cpp_name(n)}" for n in stmt.captured_names) + "]"
    else:
        capture = "[]"
    # A nested func whose name collides with a registry function (or the
    # owning record's methods) makes the body's const-verdict lookups
    # (`_param_is_const` keyed by name) consult the WRONG FunctionInfo ->
    # AST path rather than risk a wrong spelling.
    if (analyzer.registry.get_function(func.name)
            or (lc.record_name is not None
                and (ri := analyzer.registry.get_record(lc.record_name))
                is not None
                and ri.get_method_overloads(func.name))):
        note_detail("nesteddef.name_collision")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    params_cpp = []
    body_declared = dict(scope.declared)
    for pname, ptype in func.params:
        if not isinstance(ptype, TpyType):
            note_detail("nesteddef.param_unresolved")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Param families the lambda body renders EXACTLY like a top-level
        # function without any param seeding (the AST never seeds lambda
        # params): value scalars / Char / enums, str family, and F1-record
        # refs. Optional (either repr) / Own / value-opt / union / tuple
        # params would need the pointer/movable classification the AST
        # does not perform -- and their AST emit is ill-formed today
        # (BUGS.md) -- so they reject.
        pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
        if not (_eligible_scalar(pt) or _eligible_char(pt)
                or _eligible_enum(pt, analyzer) is not None
                or _resolved_str_value(pt, analyzer) is not None
                or _resolved_bytes_value(pt, analyzer) is not None
                or _f1_record(pt, analyzer)):
            note_detail("nesteddef.param_type")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        resolved = lc.render_resolve(ptype)
        params_cpp.append(resolved.to_cpp_param(escape_cpp_name(pname)))
        body_declared[pname] = ptype
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    resolved_ret = lc.render_resolve(rt)
    ret_cpp = (None if isinstance(resolved_ret, VoidType)
               else lc.render_type(resolved_ret))
    # The name binds BEFORE the body (matching the AST) and survives the
    # scope restore -- a later sibling closure captures it like any local.
    lc.nested_def_locals.add(func.name)
    with _nested_def_lowering_scope(lc, func):
        body = _lower_stmts(func.body, lc, body_declared)
        # lower_function's hoist-residue mirror: a nested-body hoisted name
        # no try predecl accounted for is a shape THIR does not reproduce.
        if lc.unhandled_hoists:
            note_detail("nesteddef.hoisted_vars")
            raise ThirUnsupported(stmt_reject_reason(stmt))
    _witness("stmt.nested_def")
    return THIRNestedDef(name=escape_cpp_name(func.name), capture_cpp=capture,
                         params_cpp=tuple(params_cpp), ret_cpp=ret_cpp,
                         body=body, loc=loc)


def _lower_stmt(stmt: TpyStmt, lc: _LowerCtx, declared: dict[str, TpyType],
                *, in_branch: bool = False,
                branch_decls_ok: bool = False,
                loop_depth: int = 0) -> THIRStmt:
    # Single chokepoint: lower the statement, then carry the AST's
    # `no_source_comment` desugar flag onto the THIR node so the emitter dedups
    # the shared source comment (nested statements route through here too).
    scope = _LowerScope(lc, declared, in_branch=in_branch,
                        branch_decls_ok=branch_decls_ok,
                        loop_depth=loop_depth)
    begin_stmt()
    try:
        result = _lower_stmt_dispatch(stmt, scope)
    except ThirUnsupported as ex:
        if not ex.detail:
            raise
        raise ThirUnsupported(stmt_reject_reason(stmt, ex.reason)) from None
    if getattr(stmt, "no_source_comment", False) and not result.no_source_comment:
        return replace(result, no_source_comment=True)
    return result


def _persistent_alias_name(var: str, lc: _LowerCtx) -> str:
    """Mirror of `_fresh_alias_local(persistent=True)`: `__{var}`, suffix-bumped
    past persistent aliases already declared at the enclosing C++ scope. (The
    AST's frame-field rename arm is unreachable: the resumable post-if caller
    rejects a frame-field-colliding alias before calling.)"""
    base = f"__{var}"
    if base not in lc.narrow.persistent_aliases:
        return base
    n = 2
    while f"{base}_{n}" in lc.narrow.persistent_aliases:
        n += 1
    return f"{base}_{n}"

def _make_narrow_alias(alias: str, var: str, member: TpyType, u: UnionType,
                       lc: _LowerCtx, loc) -> THIRNarrowAlias:
    """One U3 extraction alias -- mirrors `_emit_isinstance_extractions`'
    variant arm. The member template arg carries the pointer-variant `*` and
    the const-pointee prefix (`_narrow_subject_const`: a const local via the
    U2 field-lift chain, or a param under the deep-const verdict); the
    `const auto&` qualifier fires for value-type union PARAMS (the `const
    std::variant<...>` signature slot)."""
    member_cpp, is_ptr = _narrow_member_cpp(var, member, u, lc)
    const_ref = var in lc.prescan.param_names and u.is_value_type()
    return THIRNarrowAlias(alias=alias, variant_cpp=var, member_cpp=member_cpp,
                           is_ptr_variant=is_ptr, const_ref=const_ref,
                           no_source_comment=True, loc=loc)

def _lower_stmts(body, lc: _LowerCtx, declared: dict[str, TpyType],
                 *, in_branch: bool = False,
                 branch_decls_ok: bool = False,
                 loop_depth: int = 0) -> tuple[THIRStmt, ...]:
    """Lower a statement list, appending the U3 post-if extraction after an
    early-return narrowing `if` (`_gen_if`'s post-narrowing arm: a persistent,
    comment-less, statement-level alias) and extending the narrowing scope /
    retyping the subject for the REST of the list -- the enclosing branch/loop
    save-restore pops both (the AST's scope-snapshot semantics)."""
    out: list[THIRStmt] = []
    for s in body:
        out.append(_lower_stmt(s, lc, declared, in_branch=in_branch,
                               branch_decls_ok=branch_decls_ok,
                               loop_depth=loop_depth))
        if isinstance(s, TpyAssert):
            _append_assert_narrow(s, out, lc, declared)
        if not isinstance(s, TpyIf):
            continue
        pf = _chain_post_if_fact(s, declared, lc.narrow.narrowed, lc.analyzer)
        if pf is None:
            continue
        var, u, post = pf
        alias = _persistent_alias_name(var, lc)
        out.append(_make_narrow_alias(alias, var, post, u, lc,
                                      getattr(s, "loc", None)))
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.narrowed[var] = alias
        lc.narrow.subject_union[var] = u
        lc.narrow.persistent_narrowed.add(var)
        declared[var] = post
    return tuple(out)

def _append_assert_narrow(stmt: TpyAssert, out: 'list[THIRStmt]',
                          lc: _LowerCtx,
                          declared: dict[str, TpyType]) -> None:
    """Append the persistent extraction alias after a narrowing assert and
    extend the narrowing scope for the rest of the statement walk (mirrors
    `_gen_assert`'s `_emit_isinstance_extractions(persistent=True)` -- no
    restore; the enclosing branch/loop snapshot pops it). A re-assert on a
    persistently extracted subject re-runs the extraction with a
    suffix-bumped alias against the original union
    (`lc.narrow.subject_union`)."""
    loc = getattr(stmt, "loc", None)
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
    if rb is None:
        return
    var, m = rb
    alias = _persistent_alias_name(var, lc)
    out.append(_make_narrow_alias(alias, var, m, lc.narrow.subject_union[var],
                                  lc, loc))
    lc.narrow.persistent_aliases.add(alias)
    lc.narrow.narrowed[var] = alias

def _owned_view_opt_whole_src(stmt: TpyVarDecl, vtype: 'TpyType | None',
                              lc: _LowerCtx) -> bool:
    """Whether an owned-view Optional slot (`str | None` ->
    `optional<string>`) may take the bare WHOLE-optional copy of its init
    (`flat = rec.key;`). Two source shapes mis-render as a bare copy and
    stay deferred (both probe-caught): a view-form PARAM source needs the
    AST's view->owned shim (`s ? make_optional(string(*s)) : nullopt`), and
    a NARROWED source occurrence (sema retyped the read to the inner) takes
    the deref path -- `optional<string> y = (*s);` does not even compile
    (string_view has no implicit conversion into optional<string>)."""
    analyzer = lc.analyzer
    if _value_opt_owned_view(vtype, analyzer) is None:
        return False
    src = stmt.init
    if isinstance(src, TpyName) and _opt_view_arg_shim(
            _param_declared_type(src.name, lc), vtype, analyzer):
        return False
    st = analyzer.get_expr_type(src) if src is not None else None
    if st is None or not isinstance(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st))),
            OptionalType):
        return False
    return True


def _lower_scoped_stmts(body, lc: _LowerCtx,
                        declared: dict[str, TpyType], *,
                        branch_decls_ok: bool = False,
                        loop_depth: int = 0) -> tuple[THIRStmt, ...]:
    """`_lower_stmts` under a narrowing-scope snapshot: a branch or loop body.
    A post-if / assert narrowing made inside pops at the closing brace (the
    AST's `narrowed_vars` / `declared_persistent_aliases` body restores).
    Branch-local POINTER registrations pop too: the caller copies `declared`
    per branch, so a branch-first rebind-slot/pointer decl's lc-set entries
    must not outlive the branch either (a same-named sibling-branch decl
    would otherwise classify as a reseat of a name its scope never saw)."""
    saved = lc.narrow.snapshot()
    saved_pointers = set(lc.pointers)
    saved_rebind = set(lc.rebind_slot_locals)
    saved_optional = set(lc.optional_locals)
    saved_branch_hoisted = set(lc.branch_hoisted)
    saved_dyn = set(lc.dyn_protocol_locals)
    try:
        return _lower_stmts(body, lc, declared, in_branch=True,
                            branch_decls_ok=branch_decls_ok,
                            loop_depth=loop_depth)
    finally:
        lc.narrow = saved
        lc.pointers = saved_pointers
        lc.rebind_slot_locals = saved_rebind
        lc.optional_locals = saved_optional
        lc.branch_hoisted = saved_branch_hoisted
        lc.dyn_protocol_locals = saved_dyn

def _lower_loop_orelse(orelse, lc: _LowerCtx, declared: dict[str, TpyType],
                       scope: '_LowerScope', face: str
                       ) -> tuple[THIRStmt, ...]:
    """Lower a loop's else block. It runs AFTER the loop frames pop (the AST
    emits it past the loop's close brace), so it lowers at the OUTER loop
    depth -- a break inside it targets the enclosing loop -- over a fresh
    declared copy (its decls are block-scoped to the else `{...}`)."""
    if not orelse:
        return ()
    _witness(face)
    return _lower_scoped_stmts(orelse, lc, dict(declared),
                               branch_decls_ok=True,
                               loop_depth=scope.loop_depth)


def _lower_narrowed_branch(body, fact: 'TpyType | None', var: str,
                           u: UnionType, lc: _LowerCtx,
                           declared: dict[str, TpyType],
                           alias_loc, *,
                           loop_depth: int = 0) -> tuple[THIRStmt, ...]:
    """Lower one narrow-if branch under a narrowing-scope snapshot: a concrete
    member fact prepends the extraction alias (reads rename via
    `lc.narrow.narrowed`, the subject retypes for the branch walk); the
    snapshot pops at the closing brace. `alias_loc` is the if's loc for the
    then arm, but else_body[0]'s loc for the else arm -- emit_else_comment's
    backward scan for the `else:` line starts from the else body's leading
    statement."""
    saved = lc.narrow.snapshot()
    # Branch-local POINTER registrations pop with the branch, like
    # _lower_scoped_stmts (the declared copy is per-branch, so the lc-set
    # entries must be too -- a same-named sibling-branch decl would
    # otherwise classify as a reseat of a name its scope never saw).
    saved_pointers = set(lc.pointers)
    saved_rebind = set(lc.rebind_slot_locals)
    saved_optional = set(lc.optional_locals)
    saved_branch_hoisted = set(lc.branch_hoisted)
    saved_dyn = set(lc.dyn_protocol_locals)
    branch_declared = dict(declared)
    out: list[THIRStmt] = []
    try:
        if fact is not None:
            alias = f"__{var}"  # branch-scoped: shadowing an outer alias is fine
            out.append(_make_narrow_alias(alias, var, fact, u, lc, alias_loc))
            lc.narrow.narrowed[var] = alias
            branch_declared[var] = fact
        out.extend(_lower_stmts(body, lc, branch_declared, in_branch=True,
                                branch_decls_ok=True,
                                loop_depth=loop_depth))
    finally:
        lc.narrow = saved
        lc.pointers = saved_pointers
        lc.rebind_slot_locals = saved_rebind
        lc.optional_locals = saved_optional
        lc.branch_hoisted = saved_branch_hoisted
        lc.dyn_protocol_locals = saved_dyn
    return tuple(out)


def _lower_isinstance_cond(info, condition: TpyExpr, lc: _LowerCtx) -> THIRExpr:
    """The isinstance-condition render shared by the narrow if / while /
    assert arms: the holds_alternative OR-chain (ptr `*` + const-pointee in
    the template args for pointer variants), or the bare `true` literal for
    sema's exhaustiveness fold."""
    var, u, members, folded = info
    cond_loc = getattr(condition, "loc", None)
    result_type = lc.analyzer.get_expr_type(condition)
    if folded:
        return THIRLiteral(result_type=result_type, value=True, loc=cond_loc)
    is_ptr = is_ptr_variant_union(u)
    const = "const " if (is_ptr and _narrow_subject_const(var, lc)) else ""
    return THIRIsinstance(
        result_type=result_type,
        variant_cpp=var,
        member_cpps=tuple(
            f"{const}{lc.render_type(m)}*" if is_ptr else lc.render_type(m)
            for m in members),
        loc=cond_loc)

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
    if cond is isin:
        var, u, members, folded = info
        lowered = _lower_isinstance_cond(info, cond, lc)
        if len(members) == 1:
            lc.inline_narrowed[var] = _narrow_member_cpp(var, members[0], u, lc)
        return lowered
    if isinstance(cond, TpyBinOp) and cond.op == "&&":
        left = _lower_compound_cond(cond.left, isin, info, lc, declared)
        right = _lower_compound_cond(cond.right, isin, info, lc, declared)
        return THIRBinOp(result_type=lc.analyzer.get_expr_type(cond),
                         left=left, op="&&", right=right, resolved=None,
                         loc=getattr(cond, "loc", None))
    active_declared = declared
    var, _u, members, _folded = info
    if var in lc.inline_narrowed and len(members) == 1:
        active_declared = dict(declared)
        active_declared[var] = members[0]
    return _lower_truthy(cond, lc, active_declared)

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

def _lower_narrow_if(stmt: TpyIf, info, lc: _LowerCtx,
                     declared: dict[str, TpyType], loc, *,
                     loop_depth: int = 0) -> THIRIf:
    """Lower a U3 isinstance-narrowing `if`. The condition is the
    holds_alternative test (or
    the exhaustiveness fold's bare `true`); each branch lowers via
    `_lower_narrowed_branch`; an elif continuation recurses, breaking the
    emitter's flat `else if` chain when the outer else-fact would extract
    (`else_is_nested` -- the AST's `_has_concrete_isinstance_facts` gate)."""
    var, u, _members, _folded, _isin = info
    cond = _lower_narrow_cond(info, stmt.condition, lc, declared)
    then_fact = _narrow_fact_member(u, stmt.then_type_facts, var)
    then_stmts = _lower_narrowed_branch(stmt.then_body, then_fact, var, u, lc,
                                        declared, loc, loop_depth=loop_depth)
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
                # statement walk (_lower_stmts' _chain_post_if_fact pass), so
                # the link lowers bare.
                else_stmts = (_lower_stmt(
                    inner, lc, dict(declared), in_branch=True,
                    branch_decls_ok=True,
                    loop_depth=loop_depth),)
        else:
            else_fact = _narrow_fact_member(u, stmt.else_type_facts, var)
            else_stmts = _lower_narrowed_branch(
                stmt.else_body, else_fact, var, u, lc, declared,
                getattr(stmt.else_body[0], "loc", None),
                loop_depth=loop_depth)
    return THIRIf(condition=cond, then_body=then_stmts,
                  else_body=else_stmts, else_is_nested=else_is_nested, loc=loc)


def _written_names(stmt: TpyStmt) -> set[str]:
    def root(e: TpyExpr) -> 'str | None':
        while isinstance(e, (TpyFieldAccess, TpySubscript)):
            e = e.obj
        return e.name if isinstance(e, TpyName) else None

    if isinstance(stmt, (TpyAssign, TpyAugAssign)):
        name = root(stmt.target)
        return {name} if name is not None else set()
    if isinstance(stmt, TpyVarDecl):
        return {stmt.name}
    if isinstance(stmt, TpyTupleUnpack):
        return {name for name in stmt.targets if name is not None}
    if isinstance(stmt, TpyDelVar):
        return set(stmt.names)
    if isinstance(stmt, TpyDelItem):
        return {name for target in stmt.targets
                if (name := root(target)) is not None}
    return set()


def _lower_slice_assign(stmt: TpyAssign, lc: _LowerCtx,
                        declared: dict[str, TpyType], loc) -> THIRStmt:
    """`c[a:b] = v` / `c[a:b:s] = v` -> the slice `__setitem__`'s @cpp_template
    (list_set_slice / list_set_stepped_slice) over the receiver, the slice
    initializer, and the RHS (mirrors _gen_slice_assign). The receiver is a
    bare list/Array/Span name or F1-field (the read-path slice receiver gate);
    bounds are eligible fixed-int exprs. A non-empty array-literal RHS wears the
    std::vector<E>{...} type prefix the checked helper needs to deduce Range."""
    analyzer = lc.analyzer
    sub = stmt.target
    sl = sub.index
    if not (_container_slice_recv_ok(sub.obj, lc, declared)
            and _slice_bound_supported(sl.lower, analyzer)
            and _slice_bound_supported(sl.upper, analyzer)
            and (sl.step is None or _slice_bound_supported(sl.step, analyzer))):
        note_detail("setitem.slice")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    target_type = analyzer.get_expr_type(sub)
    recv = _lower_expr(
        sub.obj, lc, declared,
        field_prechecked=isinstance(sub.obj, TpyFieldAccess))
    value = _lower_expr(
        stmt.value, lc, declared, target_type=target_type,
        use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True))
    if _is_move_source(stmt.value, lc):
        value = THIRMove(result_type=value.result_type, value=value,
                         form=value.form, loc=loc)
    # A non-empty array-literal RHS renders `{...}`; the checked helper cannot
    # deduce its Range from a bare brace, so it takes the std::vector<E> prefix
    # (the AST's `_gen_slice_assign` guard). Empty literals already spell their
    # own type; other sources (names, calls) land bare.
    value_vector_cpp = None
    if (isinstance(stmt.value, TpyArrayLiteral) and stmt.value.elements
            and is_list(target_type) and target_type.type_args):
        value_vector_cpp = lc.render_type(target_type.type_args[0])
    _witness("setitem.slice")
    return THIRSliceAssign(
        receiver=recv,
        native_name=sub.slice_function_info.native_name,
        value=value,
        lower=_lower_expr(sl.lower, lc, declared) if sl.lower is not None else None,
        upper=_lower_expr(sl.upper, lc, declared) if sl.upper is not None else None,
        step=_lower_expr(sl.step, lc, declared) if sl.step is not None else None,
        stepped=sub.is_stepped_slice,
        value_vector_cpp=value_vector_cpp,
        loc=loc,
    )


def _list_inplace_extend(stmt: TpyAugAssign, lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> 'THIRStmt | None':
    """`c += v` on a list resolved to the mutating `__iadd__` dunder ->
    `::tpy::list_extend(c, v)` (mirrors _gen_aug_assign_code's resolved_inplace
    arm). Restricted to a bare list NAME target with a @native free-function
    dunder, so the receiver renders bare. A non-empty array-literal RHS gets
    the std::vector<E>{...} type prefix the two-parameter template needs.
    Returns None (not a reject) when the shape is outside this arm, so the
    generic aug-assign gate below runs."""
    analyzer = lc.analyzer
    inplace = stmt.resolved_inplace
    if inplace is None:
        return None
    method = inplace.method
    if not (method.native_function and method.native_name):
        return None
    if not (isinstance(stmt.target, TpyName)
            and stmt.target.name in declared
            and stmt.target.name not in lc.pointers):
        return None
    recv_t = declared[stmt.target.name]
    recv_bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t)))
    if not is_list(recv_bare):
        return None
    loc = getattr(stmt, "loc", None)
    value = _lower_expr(
        stmt.value, lc, declared,
        use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True))
    # The AST's array-literal vector prefix keys on the RAW target type
    # (`is_list(get_expr_type(target))`), so an inferred (still-pending) list
    # binding gets no prefix while an annotated `list[T]` local does -- mirror
    # that exactly (whole-list type_to_cpp, no non-empty guard).
    target_type = analyzer.get_expr_type(stmt.target)
    value_vector_cpp = None
    if isinstance(stmt.value, TpyArrayLiteral) and is_list(target_type):
        value_vector_cpp = lc.render_type(target_type)
    _witness("aug.container_inplace")
    return THIRInplaceContainerOp(
        receiver=THIRName(result_type=recv_t, name=stmt.target.name, loc=loc),
        native_name=method.native_name,
        value=value,
        value_vector_cpp=value_vector_cpp,
        loc=loc,
    )


def _wrap_view_owned_return(value: 'THIRExpr | None', lc: '_LowerCtx',
                            loc) -> 'THIRExpr | None':
    """An owned-str/bytes return slot (std::string / std::vector<uint8_t> by
    value) fed a view-form source (view param / view local) copies explicitly
    -- `return std::string(a);` / `return ::tpy::bytes_copy(a);` -- the
    view->owned construction being explicit. Mirrors _view_source_to_owned at
    the return boundary, keyed on the lowered value's own form fact; a literal
    (VALUE) or owned local / owned call result (STORAGE) returns bare. The ONE
    wrap decision for both return sinks: the sync return tail and the
    resumable return-value leaf (`_async_return_value_cpp` renders the same
    wrap at all three async scaffolding sites, and `_async_ret_to_borrow` is a
    no-op for str/bytes)."""
    ret_str = lc.prescan.ret_str
    ret_bytes = lc.prescan.ret_bytes
    if value is not None and value.form is Form.BORROW:
        if ret_str is not None and is_str_type(ret_str):
            return THIRFormConvert(result_type=ret_str, value=value,
                                   form=Form.STORAGE, loc=loc)
        if ret_bytes is not None and is_bytes_type(ret_bytes):
            return THIRFormConvert(result_type=ret_bytes, value=value,
                                   form=Form.STORAGE, loc=loc)
    return value


def _lower_resumable_return_value(ret: TpyReturn, lc: '_LowerCtx',
                                  declared: dict[str, TpyType]) -> 'THIRExpr':
    """POSITION-BLIND value render for `_make_async_return`'s scaffolding
    sites (pending-slot store / pre-finally capture / direct ready), shared
    by the ReturnT terminator arm and nested leaf returns: the scaffolding
    binds the value to a `<ret_cpp> __tpy_async_ret = <value>;` local (whose
    decl type supplies the conversion) and only then wraps it in Poll::ready
    -- so a literal at a wider slot stays bare `42`, NOT the sync-return
    arm's target-typed `::tpy::BigInt(42)`. Lower the value directly (== the
    AST's `gen_expr_deref`), bypassing `_lower_stmt`'s return-coercion arm.
    Two exceptions render target-typed on both paths: the owned str/bytes
    slot's view->owned copy, shared with the sync return tail
    (`_wrap_view_owned_return`), and value-tuple literals (the AST's
    tuple-literal-targeted arm in `_async_return_value_cpp`)."""
    if lc.prescan.ret_char and isinstance(ret.value, TpyStrLiteral):
        raise ThirUnsupported(stmt_reject_reason(ret))
    if (lc.prescan.ret_res_container is not None
            and isinstance(ret.value, (TpyArrayLiteral, TpyDictLiteral,
                                       TpySetLiteral))
            and not ret.value.children()):
        # An EMPTY container literal is the one shape the position-blind tail
        # cannot serve: the AST spells the type only when a target is passed
        # (`_gen_array_literal`'s T*-ambiguity guard), and this render has
        # none -- so it emits a bare `= {}` where the lowering spells
        # `std::vector<T>{}`. The sync return slot IS targeted, hence its
        # opposite rule.
        note_detail("return.empty_container_literal")
        raise ThirUnsupported(stmt_reject_reason(ret))
    ret_vopt = lc.prescan.ret_value_opt
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
                and _value_opt_scalar_binding(ret.value.name, lc)):
            return replace(
                _lower_expr(ret.value, lc, declared,
                            allow_whole_optional=True),
                deref=False)
        peeled = _peel_coerce(ret.value)
        if (isinstance(peeled, TpyName)
                and (peeled.name in lc.prescan.value_opt_params
                     or peeled.name in lc.value_opt_locals)):
            note_detail("return.optval_coerced_param")
            raise ThirUnsupported(stmt_reject_reason(ret))
    ret_vt = lc.prescan.ret_value_tuple
    if ret_vt is not None and isinstance(ret.value, TpyTupleLiteral):
        # A value-tuple literal renders the spelled brace-init against the
        # return slot (the sync return arm's render, matching the AST's
        # tuple-literal-targeted arm in _async_return_value_cpp: value-opt
        # element slots spell std::nullopt, Own-element names move); the
        # scaffolding's `<ret_cpp> __tpy_async_ret = <value>;` decl
        # consumes it position-independently, like the frame-field flavor.
        # Off-slice element shapes reject inside _lower_tuple_literal
        # (whole-body fallback); name/call sources ride the position-blind
        # tail below (bare renders -- the decl absorbs the copy/move).
        value = _lower_tuple_literal(ret.value, ret_vt, lc, declared)
        _witness("res.return_tuple_literal")
        return value
    return _wrap_view_owned_return(
        _lower_expr(ret.value, lc, declared), lc, getattr(ret, "loc", None))


def _lower_frame_tuple_unpack(stmt: TpyTupleUnpack,
                              scope: '_LowerScope') -> THIRStmt:
    """Resumable frame-target tuple unpack (the reactor `a, b =
    socketpair()` slice): a call/field RVALUE source materializes by value
    (`auto __tup_N = <expr>;`) and each target is a frame field --
    ASSIGNED, never re-declared (`frame_assign`), or `.emplace()`d for a
    frame_slot (`frame_emplace`), with the AST's per-element unwrap_ref /
    std::move wraps. Name sources route two rungs: a VALUE-tuple frame
    holder (const-ref bind) and an owned one-shot `__await_lift_*` holder
    (`auto&& __tup_N = (*<name>);`, owned elements move out). Off-slice
    shapes keep res.unpack: other name sources (the const-ref /
    loop-shadow bind ladder), pointer-repr source elements (the
    tuple_elem_ref arms), pointer-local / borrow-tuple / coro-handle
    targets, finally-helper position."""
    lc = scope.lc
    declared = scope.declared
    analyzer = lc.analyzer
    begin_stmt()
    if lc.in_finally_helper:
        note_detail("unpack.helper_position")
        raise ThirUnsupported("res.unpack")
    src_name: 'str | None' = None
    src_oneshot = False
    src_ref = False
    value: 'THIRExpr | None' = None
    loc = getattr(stmt, "loc", None)
    if isinstance(stmt.value, TpyName):
        nm = stmt.value.name
        st = declared.get(nm)
        st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
              if isinstance(st, TpyType) else None)
        if nm in lc.oneshot_lift_locals and nm in lc.frame_slots:
            source_type = st if isinstance(st, TupleType) else None
            if any(stmt.is_owned):
                # A one-shot `__await_lift_*` holder with OWNED elements:
                # the consumable source (`auto&& __tup_N = (*<name>);`,
                # the AST's source_is_oneshot arm) whose owned elements
                # move out at their frame targets.
                src_name = nm
                src_oneshot = True
            elif (source_type is not None
                    and source_type.has_pointer_repr_element()
                    and any(stmt.is_ref)):
                # An owning lift holder with BORROW (alias) elements: the
                # storage tuple lifts element-wise to borrow form (`auto
                # __tup_N = ::tpy::tuple_to_pointer<borrow>((*<name>));`,
                # the AST's _maybe_wrap_storage_tuple_source), and alias
                # targets re-point off the lifted elements.
                value = THIRFormConvert(
                    result_type=source_type,
                    value=THIRName(name=nm, result_type=source_type,
                                   deref=True, loc=loc),
                    form=Form.BORROW, loc=loc)
            else:
                note_detail("unpack.name_source")
                raise ThirUnsupported("res.unpack")
        elif (nm in lc.pointers and nm.startswith("__for_tup_")
                and isinstance(st, TupleType)):
            # The pointer-form tuple-unpack HOLDER (`idx, it = __for_tup_N`
            # after the skeleton's `__for_tup_N = &(*it++);` advance bind):
            # the head unpack MUTABLE-ref-binds the deref'd holder
            # (`auto& __tup_N = (*__for_tup_N);`) and the alias targets
            # re-point into the container's live storage tuple.
            src_name = nm
            src_ref = True
            source_type = st
        elif (isinstance(st, TupleType) and st.has_pointer_repr_element()
                and nm not in lc.pointers and any(stmt.is_ref)):
            # A stable borrow-tuple frame field source (`a, b = t`): the
            # MUTABLE ref-bind (`auto& __tup_N = t;` -- the AST's is_ref
            # rule drops the const), alias targets re-pointing off the
            # live elements.
            src_name = nm
            src_ref = True
            source_type = st
        elif nm not in lc.value_tuple_frame_locals:
            # The remaining name-source ladder (const-ref / loop-shadow)
            # keeps the reject.
            note_detail("unpack.name_source")
            raise ThirUnsupported("res.unpack")
        else:
            src_name = nm
            # Re-verify the registered type against the same family
            # predicate that admitted the local: `declared` is sourced
            # independently (the advance's elem type), so a drift rejects
            # here instead of rendering off a mismatched element list.
            source_type = _value_tuple(declared.get(src_name), analyzer)
    else:
        source_type = _tuple_unpack_source(
            stmt, analyzer, declared, scope.admission_pointers(),
            lc.narrow.narrowed.keys())
        if source_type is None and any(stmt.is_ref):
            # The borrow-tuple CALL source (`a, b = first_two(items)` /
            # the mixed `tag, it = pick(...)`): the same `auto __tup_N =
            # <call>;` rvalue capture; the call itself gates in
            # _lower_expr (a non-routable callee rejects the body there).
            raw = analyzer.get_expr_type(stmt.value)
            raw = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(raw)))
                   if isinstance(raw, TpyType) else None)
            if (isinstance(stmt.value, (TpyCall, TpyMethodCall))
                    and isinstance(raw, TupleType)
                    and raw.has_pointer_repr_element()):
                source_type = raw
    if (source_type is None
            or len(source_type.element_types) != len(stmt.targets)):
        note_detail("unpack.source_elems")
        raise ThirUnsupported("res.unpack")
    binds: list[str | None] = []
    wraps: list[str] = []
    for i, name in enumerate(stmt.targets):
        if name is None:
            binds.append(None)
            wraps.append("")
            continue
        elem_ptr = TupleType._element_is_pointer_repr(
            source_type.element_types[i])
        if name in lc.unpack_ptr_targets:
            # A loop-head alias target off the deref'd pointer holder:
            # `name = &(std::get<i>(__tup_N));` -- the element is a live
            # value lvalue inside the container's storage tuple.
            if not (elem_ptr and stmt.is_ref[i]):
                note_detail("unpack.alias_elem")
                raise ThirUnsupported("res.unpack")
            binds.append("frame_ptr_addr")
            wraps.append("")
            continue
        if name in lc.alias_ptr_locals:
            # A pointer-alias target aliases the LIVE element (`name =
            # &(unwrap_ref(tuple_elem_ref(get)));`) -- only off a
            # pointer-repr source element (a value element's get is a
            # copy inside the holder, the loop-var `&(get)` family).
            if not (elem_ptr and stmt.is_ref[i]):
                note_detail("unpack.alias_elem")
                raise ThirUnsupported("res.unpack")
            binds.append("frame_ptr_elem")
            wraps.append("")
            continue
        if elem_ptr or stmt.is_ref[i]:
            # A pointer-repr element needs an alias target; any other
            # target family would value-copy the borrow.
            note_detail("unpack.ref_element")
            raise ThirUnsupported("res.unpack")
        if (name not in declared
                or name in lc.pointers
                or not (name in lc.frame_slots
                        or name in lc.plain_frame_fields)):
            note_detail("unpack.target_family")
            raise ThirUnsupported("res.unpack")
        binds.append("frame_emplace" if name in lc.frame_slots
                     else "frame_assign")
        wraps.append("move" if stmt.is_owned[i] else "")
    if src_name is not None:
        _witness("res.unpack_oneshot" if src_oneshot else "res.frame_unpack")
        sb = (TupleSourceBind.ONESHOT_DEREF if src_oneshot
              else TupleSourceBind.NAME_REF if src_ref
              else TupleSourceBind.NAME_CREF)
        return THIRTupleUnpack(
            source=src_name, targets=tuple(stmt.targets),
            target_cpps=(None,) * len(stmt.targets),
            binds=tuple(binds), wraps=tuple(wraps),
            source_bind=sb,
            source_cpp=(f"(*{escape_cpp_name(src_name)})"
                        if src_ref and src_name in lc.pointers else None),
            loc=loc,
            no_source_comment=getattr(stmt, "no_source_comment", False))
    if value is None:
        value = _lower_expr(
            stmt.value, lc, declared,
            use=_ExprUse(result=_ExprResultUse.STORAGE, allow_temps=True,
                         tuple_source=True),
            field_prechecked=isinstance(stmt.value, TpyFieldAccess))
    _witness("res.frame_unpack")
    return THIRTupleUnpack(
        source="", targets=tuple(stmt.targets),
        target_cpps=(None,) * len(stmt.targets),
        binds=tuple(binds), wraps=tuple(wraps),
        source_expr=value, source_bind=TupleSourceBind.RVALUE, loc=loc,
        no_source_comment=getattr(stmt, "no_source_comment", False))


def _lower_alias_bind(stmt: TpyVarDecl, lc: '_LowerCtx',
                      declared: dict[str, TpyType]) -> THIRStmt:
    """Pointer-alias frame bind (`a = items[0]` -> `a = &(<lvalue>);`):
    the address of the LIVE source lvalue, the skeleton's `T*` alias-field
    contract. Admitted sources are the proven-lvalue shapes: a
    record-element container subscript (the optptr.subscript guards --
    unproven-Optional / slice / rvalue-container receivers reject, an
    address into a dying temp would dangle) and an F1-record field source.
    Everything else keeps the named reject."""
    init = stmt.init
    analyzer = lc.analyzer
    pointee = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        declared[stmt.name])))
    if (isinstance(init, TpySubscript)
            and not init.needs_optional_runtime_check
            and init.slice_function_info is None
            and not isinstance(init.index, TpySlice)
            and _subscript_container_recv_type(
                init.obj, declared, analyzer) is not None):
        src = _lower_expr(init, lc, declared, subscript_prechecked=True)
    elif (isinstance(init, TpyFieldAccess)
            and _f2_reseat_ok(init, declared, analyzer)):
        src = _lower_field_source(init, lc, declared)
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
        loc=stmt.loc,
        no_source_comment=getattr(stmt, "no_source_comment", False))


def _lower_frame_field_assign(stmt: TpyVarDecl, lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> THIRStmt:
    """Position-blind plain frame-field write (`name = expr;` -- first decl
    and reassign alike): the AST frame arm renders the init with plain
    gen_expr (a BigInt literal stays `0`, never the sync arms' target-typed
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
                value=value, loc=stmt.loc,
                no_source_comment=getattr(stmt, "no_source_comment", False))
    value = _lower_expr(init, lc, declared)
    _witness("res.decl_assign")
    return THIRAssign(
        target=THIRName(name=stmt.name, result_type=declared[stmt.name],
                        loc=stmt.loc),
        value=value, loc=stmt.loc,
        no_source_comment=getattr(stmt, "no_source_comment", False))


def _lower_stmt_dispatch(stmt: TpyStmt, scope: _LowerScope) -> THIRStmt:
    lc = scope.lc
    declared = scope.declared
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if lc.forbidden_writes & _written_names(stmt):
        raise ThirUnsupported("stmt.match")
    # Trivia (docstring before the TpyExprStmt arm): loc drives the source
    # comment -- `pass` keeps it, a docstring drops it (the AST emits neither).
    if is_docstring(stmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt()
    if isinstance(stmt, TpyPassStmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt(loc=loc)
    if is_super_del_call(stmt):
        # `super().__del__()` inside a destructor: C++ invokes each base
        # destructor automatically, so the AST filters these out of the
        # __del__ body (records.py's body_stmts). Emit nothing, no source
        # comment -- matching that drop.
        _witness("stmt.super_del")
        return THIRNoOpStmt()
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
                # Bare-only is exact: sema rejects return-with-value in
                # generators.
                if not lc.func.is_generator or stmt.value is not None:
                    raise ThirUnsupported("res.finally_return")
                node = THIRResumableReturn(ast_stmt=stmt, value=None, loc=loc)
                lc.nested_returns.append(node)
                _witness("res.finally_stop")
                return node
            if isinstance(stmt.value, TpyAwait):
                # Unreachable in a LEAF (a suspension splits the compound);
                # reject rather than assert if a CFG change ever leaks one.
                raise ThirUnsupported("res.leaf_return")
            value = (None if stmt.value is None
                     else _lower_resumable_return_value(stmt, lc, declared))
            node = THIRResumableReturn(ast_stmt=stmt, value=value, loc=loc)
            lc.nested_returns.append(node)
            _witness("res.nested_return")
            return node
        if scope.in_branch and isinstance(stmt, TpyVarDecl):
            begin_stmt()
            # A branch-nested decl of a PLAIN frame field (a local
            # first-assigned inside an if/match arm that survives a
            # suspension) is the same position-blind member assign as the
            # top-level leaf decl arm; the type was registered by
            # lower_resumable's nested pass-1 walk. The frame_slot /
            # borrow-tuple / coro-handle families and true C++-block branch
            # locals keep the named reject.
            if (stmt.name in lc.plain_frame_fields and stmt.init is not None
                    and stmt.name in declared
                    and stmt.name not in lc.narrow.narrowed):
                _witness("res.branch_frame_write")
                return _lower_frame_field_assign(stmt, lc, declared)
            raise ThirUnsupported("res.leaf_field_write")
        if isinstance(stmt, TpyTupleUnpack):
            return _lower_frame_tuple_unpack(stmt, scope)
        if isinstance(stmt, TpyTry):
            # An except-only leaf try pushes NO finally frame, so its emit
            # never touches __state / finally_stack / pending-return slots
            # -- the sync tiers render it byte-identically mid-state; let it
            # fall through to the sync try arm. A finally tier interlocks
            # the finally-frame stack with the async return scaffolding
            # (_push_finally <-> _make_async_return's chain walk) -- a
            # named rung.
            if stmt.finally_body:
                raise ThirUnsupported("res.leaf_try")
            _witness("res.leaf_try_except")
        if isinstance(stmt, TpyWith):
            raise ThirUnsupported("res.leaf_with")
        if isinstance(stmt, TpyMatch):
            raise ThirUnsupported("res.leaf_match")
        if isinstance(stmt, TpyNestedDef):
            raise ThirUnsupported("res.nested_def")
    if isinstance(stmt, TpyNestedDef):
        return _lower_nested_def(stmt, scope)
    if isinstance(stmt, TpyVarDecl):
        begin_stmt()
        if stmt.linkage != VarLinkage.DEFAULT:
            note_detail("decl.linkage")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.init is None:
            # An annotation-only decl (`x: str` / `x: Int32`) default-
            # constructs the resolved slot (`std::string x;` / `int32_t x;`)
            # and the later assignment writes it. VALUE families only -- a
            # non-value no-init decl (record/container/Optional) is the
            # AST's pointer/slot machinery, not a bare default-construct.
            vt0 = _var_decl_type(stmt, analyzer)
            nt: 'TpyType | None' = None
            if vt0 is not None and stmt.name not in declared:
                if _eligible_scalar(vt0) or _eligible_char(vt0):
                    nt = vt0
                else:
                    nt = _resolved_str_value(vt0, analyzer)
            if nt is None or (scope.in_branch
                              and not scope.branch_decls_ok):
                note_detail("decl.no_init")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("decl.no_init_value")
            declared[stmt.name] = nt
            return THIRVarDecl(name=stmt.name, resolved_type=nt, init=None,
                               loc=loc)
        if lc.prescan.has_self and stmt.name == "self":
            note_detail("decl.self_rebind")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.name in lc.narrow.narrowed:
            note_detail("decl.narrowed_rebind")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        is_reassign = stmt.name in declared
        in_branch_first = scope.in_branch and not is_reassign
        if in_branch_first and not scope.branch_decls_ok:
            note_detail("decl.branch_first_decl")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        vtype = _var_decl_type(stmt, analyzer)
        # A direct @error_return call init takes the statement-level unwrap
        # block, BEFORE any binding classification (the AST's
        # _gen_simple_stmt checks _get_error_return_fi first).
        er_fi = _error_return_stmt_fi(stmt.init, analyzer)
        if er_fi is not None:
            return _lower_error_return_bind(
                stmt, stmt.name, stmt.init, er_fi, vtype, lc, declared, loc)
        # Escape-hoist record pointer-local first decls (hoisted, or
        # name-reassigned with an rvalue init -- the classifier's OTHER, the
        # AST's pointer path). Placed before the branch-scope gate: a
        # loop-hoisted decl arrives in_branch by definition.
        if not is_reassign:
            rec_node = _lower_record_ptr_slot_decl(stmt, vtype, lc, declared,
                                                   loc)
            if rec_node is not None:
                lc.pointers.add(stmt.name)
                if rec_node.needs_rebind_slot:
                    lc.rebind_slot_locals.add(stmt.name)
                declared[stmt.name] = vtype
                return rec_node
        # First decl of a non-value borrow local (REF_ALIAS / OPTIONAL_TO_PTR /
        # POINTER).
        if not is_reassign and not scope.in_branch:
            # @dynamic protocol local (`p: P = Concrete()`): slot + Base*
            # pointer, its own emit form (before the borrow classifier, which
            # has no protocol arm).
            dyn_node = _lower_dyn_protocol_decl(stmt, vtype, lc, declared, loc)
            if dyn_node is not None:
                lc.pointers.add(stmt.name)
                declared[stmt.name] = vtype
                return dyn_node
            binding = _borrow_local_binding(stmt, vtype, declared, lc.prescan,
                                            analyzer, lc.pointers)
            if binding is not None:
                if binding is LocalBinding.OPT_PTR_SLOT:
                    # Slot-hoist Optional pointer-local (None / rvalue init).
                    # An owned, mutable `T*` like REBIND_SLOT (const shapes
                    # reject inside); reseats need the rebind-slot arm, so the
                    # name joins both pointer sets when a slot is pre-declared.
                    node = _lower_opt_ptr_slot_decl(stmt, vtype, lc, declared,
                                                    loc)
                    lc.pointers.add(stmt.name)
                    if node.needs_rebind_slot:
                        lc.rebind_slot_locals.add(stmt.name)
                    declared[stmt.name] = vtype
                    return node
                if binding is LocalBinding.REBIND_SLOT:
                    # rvalue ctor source: an owned, mutable pointer-local (never
                    # const). Recorded in both sets, as eligibility did.
                    lc.pointers.add(stmt.name)
                    lc.rebind_slot_locals.add(stmt.name)
                    declared[stmt.name] = vtype
                    return _lower_borrow_local(
                        stmt, vtype, binding, False, lc, declared, loc)
                is_const = _f1_is_const(binding, vtype, stmt, lc.func, analyzer,
                                        lc.const_locals, lc.record_name)
                if is_const:
                    lc.const_locals.add(stmt.name)
                if binding is LocalBinding.POINTER:
                    lc.pointers.add(stmt.name)  # later assignments reseat this `T*`
                elif binding is LocalBinding.REF_ALIAS:
                    # `T& name = ...` -- tracked for the del-var skip (the
                    # alias never owns the value it names).
                    lc.ref_alias_locals.add(stmt.name)
                elif binding is LocalBinding.OPTIONAL_TO_PTR:
                    # A borrow `T*` binding too: its (proven) field/method
                    # reads render `->`, its record-slot passes `(*x)`, its
                    # `T*`-slot passes bare. Never reseated (a reassigned
                    # Optional local classifies OTHER at the gate).
                    lc.pointers.add(stmt.name)
                declared[stmt.name] = vtype
                return _lower_borrow_local(
                    stmt, vtype, binding, is_const, lc, declared, loc)
            # F3 storage-tuple alias: `auto&& t = <storage tuple field>`. The local
            # aliases the source's storage, so a read off it is STORAGE form (lifted
            # via tuple_to_pointer at a borrow boundary); the init is the storage tuple
            # field source (no conversion node -- `auto&&` binds it directly). The
            # `storage_tuple_locals` membership is what makes a later read lift.
            if (is_storage_tuple_alias_decl(
                    vtype, stmt.init, name=stmt.name,
                    reassigned=lc.prescan.reassigned, hoisted=lc.prescan.hoisted,
                    move_through=lc.prescan.move_through)
                    and _const_exact_field_receiver_ok(
                        stmt.init, declared, analyzer)
                    and _f1_tuple(
                        analyzer.get_expr_type(stmt.init), analyzer) is not None):
                lc.storage_tuple_locals.add(stmt.name)
                # The alias aliases its source's const-ness (`auto&&` deduces it): a
                # const-receiver source makes reads lift to `const T*`. Tracked in
                # `const_locals` so the borrow read at a return picks the const helper.
                src_recv = stmt.init.obj  # TpyName (FieldAccess receiver)
                if (src_recv.name in lc.const_locals
                        or _param_is_const(src_recv.name, lc.func, analyzer,
                                           lc.record_name)):
                    lc.const_locals.add(stmt.name)
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=_lower_field_source(stmt.init, lc, declared), form=Form.STORAGE,
                    cpp_local_representation=LocalBinding.STORAGE_TUPLE_ALIAS, loc=loc)
            # C1+C2 comprehension local: the init renders as the whole
            # stmt-expr; the decl line itself is the plain-value arm.
            if type(stmt.init) in _comprehensions._COMP_KINDS:
                if (stmt.name in lc.prescan.reassigned
                        or stmt.name in lc.prescan.hoisted
                        or stmt.name in lc.prescan.move_through):
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                comp = _comprehensions._lower_comprehension(
                    stmt.init, vtype, lc, declared,
                    scope.admission_pointers())
                declared[stmt.name] = vtype
                return THIRVarDecl(name=stmt.name, resolved_type=vtype,
                                   init=comp, loc=loc)
            # Open-T local from a T-returning call in a generic body:
            # `item = box.get()` -> `::tpy::val_or_ref_t<T> item = box.get();`
            # (val_or_cref_t for a readonly method). Form-neutral like a T
            # param slot: the trait resolves value-vs-ref per instantiation,
            # so the local reads bare. Reassigned/hoisted names fall through
            # (references can't rebind), mirroring the AST arm.
            if (isinstance(vtype, TypeParamRef) and not vtype.is_value_type()
                    and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                    and stmt.name not in lc.prescan.reassigned
                    and stmt.name not in lc.prescan.hoisted
                    and stmt.name not in lc.prescan.move_through):
                t_fi = stmt.init.resolved_function_info
                if (t_fi is not None and t_fi.cpp_template is None
                        and isinstance(unwrap_ref_type(t_fi.return_type),
                                       TypeParamRef)):
                    trait = ("::tpy::val_or_cref_t" if t_fi.is_readonly
                             else "::tpy::val_or_ref_t")
                    init = _lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                    declared[stmt.name] = vtype
                    _witness("decl.tparam_call")
                    return THIRVarDecl(
                        name=stmt.name, resolved_type=vtype, init=init,
                        cpp_type=f"{trait}<{lc.render_type(vtype)}>",
                        loc=loc)
            # `b = copy(a)` of a plain F1-record source: an owned record local
            # from the copy-construct rvalue (`T b = T(a);`, _gen_copy_expr's
            # bare-record arm). The source stays live (copy, not move).
            copy_rec = copy_plain_record_source(stmt.init, lc.analyzer,
                                                lc.pointers)
            if copy_rec is not None:
                _witness("decl.copy_record")
                src = _lower_expr(
                    stmt.init.args[0], lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=THIRCopy(result_type=vtype, value=src,
                                  cpp_type=lc.render_type(copy_rec),
                                  form=Form.STORAGE, loc=loc),
                    cpp_type=lc.render_type(vtype), form=Form.STORAGE, loc=loc)
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
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(
                            result=_ExprResultUse.BORROW_BIND,
                            allow_temps=True)),
                    cpp_type=lc.render_type(vtype), form=Form.STORAGE, loc=loc)
            # Move-through owned record local: `a = h` where `h` is a non-value
            # local consumed at its last use (`Handle a = std::move(h);`). Sema
            # marks the target in `move_through` only for a non-reassigned NAME
            # source at last use of a non-value local; the AST wraps the init in
            # std::move keyed on the target (not a fresh last-use test), so the
            # membership drives the move directly.
            if (stmt.name in lc.prescan.move_through
                    and isinstance(vtype, NominalType)
                    and _f1_record(vtype, analyzer)
                    and isinstance(stmt.init, TpyName)):
                _witness("decl.move_through_record")
                src = _lower_expr(
                    stmt.init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                declared[stmt.name] = vtype
                return THIRVarDecl(
                    name=stmt.name, resolved_type=vtype,
                    init=THIRMove(result_type=src.result_type, value=src,
                                  form=src.form, loc=loc),
                    cpp_type=lc.render_type(vtype), form=Form.STORAGE, loc=loc)
        # OPTIONAL_STORAGE branch-hoist assign: the single-bind non-value's
        # in-branch decl writes PLAIN into the if-head `std::optional<T>`
        # (`name = <storage rvalue>;` -- _gen_var_decl_code's
        # OPTIONAL_STORAGE arm). Checked before the rebind-slot/pointer
        # reseats: the name is in `pointers` for its read side only.
        if stmt.name in lc.optional_locals and stmt.name in declared:
            target_t = declared[stmt.name]
            if not _rebind_rvalue_source_ok(stmt.init, target_t, analyzer):
                note_detail("reseat.opt_storage_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("reseat.opt_storage")
            return THIRAssign(
                target=THIRName(result_type=target_t, name=stmt.name,
                                loc=loc),
                value=_lower_expr(stmt.init, lc, declared,
                                  target_type=target_t,
                                  use=_rebind_rvalue_use(stmt.init)),
                loc=loc)
        # BRANCH_RVALUE reseat: a branch-hoisted `T*` local without an
        # if-head slot takes an rvalue via the lazily-allocated function-top
        # slot (`name = &*(__slot_N = <rvalue>);`); lvalue sources fall
        # through to the pointer reseat arms below.
        if (stmt.name in lc.branch_hoisted and stmt.name in declared
                and is_rvalue_source(analyzer, stmt.init)):
            target_t = declared[stmt.name]
            # The slot holds the POINTEE: a pointer-repr Optional hoist's
            # lazy slot is `std::optional<T>`, not optional<optional<T>>.
            slot_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                target_t)))
            if (isinstance(slot_t, OptionalType)
                    and slot_t.uses_pointer_repr()):
                slot_t = unwrap_readonly(slot_t.inner)
            slot_t = resolve_pending_container(slot_t, analyzer) or slot_t
            if not _rebind_rvalue_source_ok(stmt.init, target_t, analyzer):
                note_detail("reseat.branch_rvalue_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("reseat.branch_rvalue")
            return THIRPtrLocalRebind(
                name=stmt.name, kind=PtrSlotKind.BRANCH_RVALUE,
                value=_lower_expr(stmt.init, lc, declared,
                                  target_type=target_t,
                                  use=_rebind_rvalue_use(stmt.init)),
                val_cpp=lc.render_type(slot_t), loc=loc)
        # F2d rebind-slot reseat: an rvalue ctor / by-value source. It lowers as a
        # plain value-form call; emit wraps it as `p = &*(__slot_N = <value>)`
        # using the rebind slot allocated at the decl. Checked before the lvalue
        # POINTER reseat -- a rebind-slot local is in both `pointers` sets.
        if (stmt.name in lc.rebind_slot_locals
                and stmt.name in declared
                and _eligible_ptr_union(declared[stmt.name],
                                        lc.analyzer) is None):
            decl_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared[stmt.name])))
            if (isinstance(decl_u, OptionalType)
                    and decl_u.uses_pointer_repr()):
                # OPT_PTR_SLOT reseats: None -> `p = nullptr;`; an exact-type
                # F1 rvalue rides THIRAssign's rebind-slot arm
                # (`p = &*(__slot_N = <rvalue>);`). Lvalue reseats (pointer
                # copies / optional_to_ptr lifts / address-of) are later rungs.
                if isinstance(stmt.init, TpyNoneLiteral):
                    _witness("reseat.opt_none")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.OPT_NONE, loc=loc)
                if not _opt_slot_rvalue_shape(stmt.init, decl_u.inner,
                                              analyzer):
                    note_detail("decl.opt_reseat_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("reseat.opt_rvalue")
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=_lower_expr(stmt.init, lc, declared,
                                      target_type=decl_u.inner), loc=loc)
            if is_rvalue_source(analyzer, stmt.init):
                if not _rebind_rvalue_source_ok(stmt.init,
                                                declared[stmt.name],
                                                analyzer):
                    note_detail("decl.rebind_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=_lower_expr(stmt.init, lc, declared,
                                      target_type=declared[stmt.name],
                                      use=_rebind_rvalue_use(stmt.init)),
                    loc=loc)
            # An LVALUE reseat of a slot-holding name leaves the slot
            # untouched and re-points the alias (`p = &(lvalue);`) -- fall
            # through to the pointer reseat arms below.
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so this stays byte-identical).
        if stmt.name in lc.pointers and stmt.name in declared:
            if stmt.name in lc.dyn_protocol_locals:
                # @dynamic protocol local reseat (`pet = Cat()`): a FRESH hoisted
                # `std::optional<slot>` + `.emplace` + `pet = &*slot` (the AST's
                # `_gen_dynamic_protocol_rebind`). Slot type is the concrete (a
                # direct conformer) or the `Adapter<Base, Concrete>` (structural),
                # matching the first-decl choice. An already-erased protocol-typed
                # source (a pointer copy `pet = &q`) is a later rung.
                # The hoisted slot needs a drain point. `emit_thir_body` /
                # `emit_thir_constructor_tail` drain `hoist_lines`; the generator
                # LEAF emitters (ResumableLeafEmitter, SimpleGenLeafEmitter) do
                # NOT, so a reseat there would drop the `std::optional<slot>` decl
                # (undeclared `__slot_N`). Defer any generator/async body to AST
                # (which drains via `pending_hoist_decls`).
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
                if record_inherits_dynamic(concrete_type, proto_vt, analyzer):
                    slot_cpp = concrete_cpp
                else:
                    slot_cpp = dynamic_adapter_type(proto_vt, concrete_cpp,
                                                    analyzer)
                _witness("reseat.dyn_protocol")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.DYN_PROTOCOL,
                    value=_lower_expr(stmt.init, lc, declared,
                                      target_type=concrete_type),
                    val_cpp=slot_cpp, loc=loc)
            reseat_u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                declared[stmt.name])))
            if (isinstance(reseat_u, OptionalType)
                    and reseat_u.uses_pointer_repr()):
                # A slotless OPT_PTR_SLOT local (never rvalue-reassigned --
                # the slot-holders reseat via the rebind-slot arm above)
                # reseats to None (`p = nullptr;`) or lifts a new pointee via
                # `&(...)`: a bare record PARAM (`x = p;` -> `x = &(p);`) or an
                # F1-record field source (`x = &(recv.field);`), mirroring the
                # AST `_gen_pointer_local_rebind` nullptr / address-of arms.
                # Pointer / rvalue / optional_to_ptr sources are later rungs.
                if isinstance(stmt.init, TpyNoneLiteral):
                    _witness("reseat.opt_none")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.OPT_NONE, loc=loc)
                pointee = unwrap_readonly(reseat_u.inner)
                if _f1_param_lvalue_reseat_ok(stmt.init, pointee, declared, lc,
                                              analyzer):
                    src: THIRExpr = _lower_expr(stmt.init, lc, declared)
                    if (src.form is Form.BORROW
                            and src.result_type == pointee
                            and not lc.resumable_leaf_mode):
                        # Same trap as the plain-record param reseat below:
                        # the BORROW convert over a same-type BORROW param
                        # name is the no-op node validate_function
                        # hard-rejects. Resumable leaves are exempt: they
                        # never run whole-function validation, and the
                        # routed async shape is pinned byte-identical
                        # (frame-field receivers render `x = &(b);`).
                        note_detail("decl.reseat_param_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                elif _f2_reseat_ok(stmt.init, declared, analyzer):
                    src = _lower_field_source(stmt.init, lc, declared)
                else:
                    note_detail("decl.opt_reseat_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _witness("reseat.opt_lvalue")
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                    value=THIRFormConvert(
                        result_type=pointee, value=src, form=Form.BORROW,
                        is_const=stmt.name in lc.const_locals, loc=loc),
                    loc=loc)
            # A pointer-NAME copy reseat (`saved = p;` -- both `T*` locals):
            # the bare pointer copies, no address-of (the AST's pointer-local
            # source branch renders the name verbatim).
            if (isinstance(stmt.init, TpyName)
                    and stmt.init.name in lc.pointers
                    and stmt.init.name not in lc.narrow.narrowed):
                _witness("reseat.ptr_copy")
                return THIRAssign(
                    target=THIRName(result_type=vtype, name=stmt.name,
                                    loc=loc),
                    value=THIRName(result_type=vtype, name=stmt.init.name,
                                   loc=loc),
                    loc=loc)
            # A bare record PARAM reseat (`x = b;` -> `x = &(b);`), the sibling
            # of the F1-record field reseat below.
            if _f1_param_lvalue_reseat_ok(stmt.init, vtype, declared, lc,
                                          analyzer):
                src_name = _lower_expr(stmt.init, lc, declared)
                if (src_name.form is Form.BORROW
                        and src_name.result_type == vtype):
                    # A same-type BORROW-form source (a record param's `T&`)
                    # would make the BORROW convert the same-form same-type
                    # node the validator rejects -- the form model cannot
                    # spell the `T&` -> `T*` representation change yet; keep
                    # the shape on AST. (A type-differing source -- e.g. a
                    # readonly-wrapped binding -- stays a real convert.)
                    note_detail("decl.reseat_param_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            elif _f2_reseat_ok(stmt.init, declared, analyzer):
                src_name = _lower_field_source(stmt.init, lc, declared)
            elif (isinstance(stmt.init, TpyName)
                  and stmt.init.name in declared
                  and stmt.init.name not in lc.pointers
                  and stmt.init.name not in lc.rebind_slot_locals
                  and stmt.init.name not in lc.optional_locals
                  and stmt.init.name not in lc.const_locals
                  and stmt.init.name not in lc.narrow.narrowed
                  and not _param_is_const(stmt.init.name, lc.func, analyzer,
                                          lc.record_name)
                  and is_plain_nonvalue(unwrap_readonly(unwrap_ref_type(
                      unwrap_send_sync(declared[stmt.init.name]))))
                  and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      declared[stmt.init.name]))) == unwrap_readonly(
                      unwrap_ref_type(unwrap_send_sync(
                          declared[stmt.name])))):
                # A plain same-type storage LOCAL lvalue (`items = base;` ->
                # `items = &(base);`): the AST rebind's address-of catch-all
                # for a name that renders bare storage. The `&` lives in the
                # PTR_ADDR emit (a FormConvert over the BORROW-form name read
                # would be the no-op node the validator rejects).
                # Pointer-form, const, and narrowed sources stay on their
                # own rungs.
                _witness("reseat.storage_name")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                    value=_lower_expr(stmt.init, lc, declared), loc=loc)
            elif (isinstance(stmt.init, TpySubscript)
                  and stmt.init.slice_function_info is None
                  and not isinstance(stmt.init.index, TpySlice)
                  and not isinstance(
                      unwrap_ref_type(unwrap_send_sync(
                          analyzer.get_expr_type(stmt.init.obj)
                          or vtype)), ReadonlyType)
                  and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      analyzer.get_expr_type(stmt.init) or vtype)))
                  == unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                      declared[stmt.name])))
                  and is_plain_nonvalue(unwrap_readonly(unwrap_ref_type(
                      unwrap_send_sync(declared[stmt.name]))))):
                # A mutable container ELEMENT lvalue (`p = points[0];` ->
                # `p = &(::tpy::__getitem__(points, 0));`): the same
                # address-of catch-all over the element read. RECEIVER use:
                # the record-elem read is admitted for address-of inners
                # there (a value use would copy the borrow). Readonly
                # receivers (const elements) stay on the const rung.
                _witness("reseat.subscript_elem")
                return THIRPtrLocalRebind(
                    name=stmt.name, kind=PtrSlotKind.PTR_ADDR,
                    value=_lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.RECEIVER)),
                    loc=loc)
            else:
                note_detail("decl.reseat_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            convert = THIRFormConvert(
                result_type=vtype,
                value=src_name, form=Form.BORROW,
                is_const=stmt.name in lc.const_locals, loc=loc)
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=convert, loc=loc)
        # F4 U2: a pointer-variant union local -- first decl or reseat. A
        # same-union name copies bare (borrow -> borrow); a value-variant
        # field lvalue lifts via to_[const_]ptr_variant, const from the
        # receiver (the F1 OPTIONAL_TO_PTR const bump's union sibling).
        ptr_u = _eligible_ptr_union(
            declared[stmt.name] if stmt.name in declared else vtype,
            lc.analyzer)
        if ptr_u is not None and stmt.init is not None:
            if in_branch_first:
                note_detail("decl.branch_ptr_union")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if not isinstance(stmt.init, TpyNoneLiteral) and not _ptr_union_source_ok(
                    stmt.init, declared, analyzer, ptr_u,
                    allow_field=stmt.name not in lc.prescan.reassigned):
                # Slot-hoist kinds: a concrete-MEMBER rvalue materializes a
                # value-variant `__slot_N` and lifts it (`to_ptr_variant`);
                # a concrete-member lvalue NAME binds its address into the
                # pointer variant (`v{&(name)}`). Mirrors
                # `_gen_ptr_variant_local_init/_reassign`'s rvalue and
                # concrete-lvalue branches.
                slot_kind = _ptr_union_slot_kind(stmt.init, ptr_u, declared,
                                                 lc, analyzer)
                if slot_kind is None:
                    note_detail("decl.ptr_union_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if stmt.name in declared:
                    # Reseat: only the rvalue kind through the rebind slot
                    # pre-declared at the decl (`.emplace` + re-lift); the
                    # slotless inline-slot fallback and the address reseat
                    # are later rungs.
                    if (slot_kind is not PtrSlotKind.UNION_RVALUE
                            or stmt.name not in lc.rebind_slot_locals):
                        note_detail("decl.union_reseat_source")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    _witness("reseat.union_rvalue")
                    return THIRPtrLocalRebind(
                        name=stmt.name, kind=PtrSlotKind.UNION_RVALUE,
                        value=_lower_expr(stmt.init, lc, declared,
                                          use=_ExprUse(
                                              result=_ExprResultUse.BORROW_BIND)),
                        val_cpp=_union_storage_val_cpp(ptr_u), loc=loc)
                needs_rebind = stmt.name in lc.prescan.rvalue_reassigned
                if slot_kind is PtrSlotKind.UNION_RVALUE:
                    u_slot_init = _lower_expr(
                        stmt.init, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                    _witness("decl.union_slot_rvalue")
                else:
                    u_slot_init = _lower_expr(stmt.init, lc, declared)
                    _witness("decl.union_addr")
                declared[stmt.name] = vtype
                if needs_rebind:
                    lc.rebind_slot_locals.add(stmt.name)
                return THIRPtrLocalDecl(
                    name=stmt.name, resolved_type=ptr_u, kind=slot_kind,
                    init=u_slot_init, cpp_type=ptr_u.to_cpp_ptr_variant(),
                    val_cpp=_union_storage_val_cpp(ptr_u),
                    needs_rebind_slot=needs_rebind, loc=loc)
            if isinstance(stmt.init, TpyNoneLiteral):
                # `x = None` at a pointer-variant binding stores the monostate
                # member bare (`x = std::monostate{};`) -- target-typed at
                # lowering like the value-union arm below; the emit's union
                # render is form-independent.
                u_const = False
                u_init: THIRExpr = THIRLiteral(result_type=ptr_u, value=None,
                                               form=Form.STORAGE, loc=loc)
            elif isinstance(stmt.init, TpyFieldAccess):
                recv = stmt.init.obj  # TpyName (validated by _field_receiver_ok)
                u_const = (recv.name in lc.const_locals
                           or _param_is_const(recv.name, lc.func, lc.analyzer,
                                              lc.record_name))
                u_init = THIRFormConvert(
                    result_type=ptr_u, value=_lower_field_source(stmt.init, lc, declared),
                    form=Form.BORROW, is_const=u_const, loc=loc)
            else:
                u_const = False
                u_init = _lower_expr(stmt.init, lc, declared)
            if stmt.name in declared:
                return THIRAssign(
                    target=THIRName(result_type=ptr_u, name=stmt.name,
                                    form=Form.BORROW, loc=loc),
                    value=u_init, loc=loc)
            if u_const:
                lc.const_locals.add(stmt.name)
            declared[stmt.name] = vtype
            u_cpp = (ptr_u.to_cpp_const_ptr_variant() if u_const
                     else ptr_u.to_cpp_ptr_variant())
            return THIRVarDecl(
                name=stmt.name, resolved_type=ptr_u, init=u_init,
                cpp_type=u_cpp, form=Form.BORROW, is_const=u_const,
                cpp_local_representation=LocalBinding.PTR_VARIANT, loc=loc)
        decl_tgt = declared.get(stmt.name, vtype)
        if isinstance(stmt.init, TpyStrLiteral) and _eligible_char(decl_tgt):
            if is_reassign or len(stmt.init.value) != 1:
                note_detail("decl.char_literal")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        # Branch position included: a branch-FIRST literal decl reaching the
        # check is genuinely block-local (the LINCHPIN note below -- escaping
        # names are hoisted or rejected, and the check itself rejects
        # hoisted/reassigned/move-through), so it emits the same plain decl
        # at branch indent.
        container_literal = (
            not is_reassign
            and _container_literal_decl_ok(
                stmt, declared, lc.prescan, analyzer))
        storage_call = False
        # A container-slice read (`sub = items[a:b:c]` -> an owned `list[T]`
        # from list_stepped_slice/list_slice) is an rvalue producing a fresh
        # container, so it takes the same storage decl-init sink as a
        # container-returning call.
        storage_src = isinstance(stmt.init, (TpyCall, TpyMethodCall)) or (
            isinstance(stmt.init, TpySubscript)
            and stmt.init.slice_function_info is not None
            and isinstance(stmt.init.index, TpySlice))
        if storage_src:
            fam = _storage_call_ret(analyzer.get_expr_type(stmt.init), analyzer)
            if fam is not None:
                # Branch-FIRST decls included: block-local (the LINCHPIN
                # note), and the container guards below exclude every
                # escaping/rebinding shape -- same plain decl at branch
                # indent (mirrors the native-record arm below).
                # bytearray rides the same guards: it is the one owned
                # container OUTSIDE `_storage_call_container` (that predicate
                # also gates the generic-instantiation arms, where bytearray
                # does not belong), and its reassigned locals take the AST's
                # pointer-rebind machinery exactly like list/dict/set.
                if _storage_call_container(fam) or is_bytearray_type(fam):
                    if (is_reassign
                            or stmt.name in lc.prescan.reassigned
                            or stmt.name in lc.prescan.hoisted
                            or stmt.name in lc.prescan.move_through):
                        note_detail("decl.container_call_reassigned")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    # A BORROW container return (`return self._items` -- a C++
                    # `T&`) binds a `T&` alias on the AST path, not the plain
                    # copy this arm emits; only rvalue (by-value / Own) returns
                    # take the storage decl. Tuples/unions are value types --
                    # always by-value, no alias form exists.
                    if not is_rvalue_source(analyzer, stmt.init):
                        note_detail("decl.container_call_borrow")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                storage_call = True
                _witness("decl.storage_call")
        # A @native free call returning a by-value record (`f = open(path)` ->
        # `::tpy::TextFile f = ::tpy::builtin_open(path);`): a plain-value decl,
        # like the container/tuple storage rows. The record is a non-value
        # reference type, so a reassigned / hoisted / escaping target would take
        # the AST's pointer-local form -- the reassigned/hoisted/move_through
        # guards exclude those; a branch-FIRST inline decl (the block-local
        # `open()` in a try body) still emits the plain value decl (oracle).
        if (not storage_call and isinstance(stmt.init, TpyCall)
                and not is_reassign
                and stmt.name not in lc.prescan.reassigned
                and stmt.name not in lc.prescan.hoisted
                and stmt.name not in lc.prescan.move_through
                and _native_record_rvalue_call_shape(stmt.init, analyzer)):
            storage_call = True
            _witness("decl.native_record_call")
        # An iterator-object decl (`it = g()` / `it = obj.gen()`): the
        # generator/iterator factory result lands in an `auto` local that
        # feeds the universal __iter__/__next__ loop -- the AST's plain
        # `auto it = g();`. The init lowers under the ITERABLE use (the one
        # position the call gates admit a generator fi); a decl init is a
        # flushable position, so temp-hoisting args are legal. Single
        # assignment only -- a reassigned/hoisted iterator local stays AST.
        # Function scope only: `iterator_object_locals` is outside the
        # branch snapshot, so a branch-first registration would leak past
        # its scope (the sibling-branch bug class).
        if (not is_reassign and not scope.in_branch
                and isinstance(stmt.init, (TpyCall, TpyMethodCall))
                and _user_iterator_iterable(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(vtype))),
                    analyzer)
                and getattr(stmt.init, "resolved_function_info", None)
                    is not None
                and stmt.init.resolved_function_info.is_generator
                and stmt.name not in lc.prescan.reassigned
                and stmt.name not in lc.prescan.hoisted
                and stmt.name not in lc.prescan.move_through):
            init = _lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.ITERABLE,
                             allow_temps=True))
            _witness("decl.iterator_object")
            declared[stmt.name] = vtype
            lc.iterator_object_locals.add(stmt.name)
            return THIRVarDecl(
                name=stmt.name, resolved_type=vtype, init=init,
                cpp_type="auto", loc=loc)
        # `x = x + y` self-append peephole (the AST's _try_str_inplace_append,
        # checked on every reassignment of an owned-str-family local before the
        # generic emit): the RHS concat's left operand is the target itself, so
        # the whole statement emits `x += y;` (buffer reuse) instead of the
        # concat-and-assign.
        if stmt.name in declared and _owned_str_append_target(vtype, analyzer):
            rhs = _str_self_append_rhs(stmt.name, stmt.init)
            if rhs is not None:
                return THIRStrAppend(target=stmt.name,
                                     value=_lower_expr(rhs, lc, declared), loc=loc)
        # `x = None` at a value-union binding renders the monostate member --
        # target-typed at lowering, like the Char decl below (F4 U1). At a
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
            slot_ty = declared.get(stmt.name, vtype)
            tuple_t = _value_tuple_nested(slot_ty, analyzer)
            storage_record = False
            # A first-decl VALUE-capture literal of record/Own elements binds the
            # tuple BY VALUE (storage form): a pointer-repr-TYPED tuple still
            # spells `std::tuple<..., T>` and lowers through the container-element
            # move/copy path. A REF/CONST_REF capture is the borrow form (kept on
            # the AST path).
            if (tuple_t is None and not is_reassign and stmt.init.elem_capture
                    and all(c is TupleElemCapture.VALUE
                            for c in stmt.init.elem_capture)):
                tuple_t = _storage_record_tuple(slot_ty, analyzer)
                storage_record = tuple_t is not None
            # A REF-capture literal binds the BORROW form (`auto t =
            # std::tuple<int32_t, Box*>{1, &(b)};`): the local aliases its
            # element sources, and reads key on the declared pointer-repr
            # TupleType like a borrow-tuple param's. First decls with
            # REF/VALUE captures only; CONST_REF decls (const read rows) and
            # reassigns stay named rejects.
            slot_bt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                slot_ty))) if isinstance(slot_ty, TpyType) else None)
            if (tuple_t is None and not is_reassign
                    and isinstance(slot_bt, TupleType)
                    and slot_bt.has_pointer_repr_element()
                    and stmt.init.elem_capture
                    and all(c in (TupleElemCapture.VALUE, TupleElemCapture.REF)
                            for c in stmt.init.elem_capture)
                    and any(c is TupleElemCapture.REF
                            for c in stmt.init.elem_capture)):
                try:
                    binit = _lower_borrow_tuple_literal(
                        stmt.init, slot_bt, lc, declared)
                except ThirUnsupported:
                    note_detail("decl.tuple_literal_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt)) from None
                declared[stmt.name] = slot_bt
                _witness("btuple.decl")
                return THIRVarDecl(
                    name=stmt.name, resolved_type=slot_bt, init=binit,
                    cpp_type="auto", form=Form.BORROW, loc=loc)
            if tuple_t is None:
                note_detail("decl.tuple_literal_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("decl.tuple_literal")
            try:
                init = _lower_tuple_literal(stmt.init, tuple_t, lc, declared)
            except ThirUnsupported:
                note_detail("decl.tuple_literal_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt)) from None
            if storage_record:
                # The local OWNS its elements, so downstream subscript / name
                # reads stay storage (`.field`, no `->`/pointer lift) -- register
                # it the way the AST registers `storage_form_tuple_locals`. A
                # ref-element tuple decl spells `auto` (its `has_ref_elements`
                # type would otherwise render the borrow spelling), mirroring the
                # AST's `_cpp_decl_type` auto arm.
                lc.storage_tuple_locals.add(stmt.name)
                declared[stmt.name] = tuple_t
                cpp = "auto" if tuple_t.has_ref_elements() else None
                _witness("decl.storage_record_tuple")
                return THIRVarDecl(
                    name=stmt.name, resolved_type=tuple_t, init=init,
                    cpp_type=cpp, form=Form.STORAGE, loc=loc)
        else:
            if not is_reassign and not container_literal and not storage_call:
                # A branch-FIRST inline decl (`in_branch_first`) is genuinely
                # block-local -- an escaping var is hoisted into `declared` and
                # never reaches here as a first decl -- so it decls the same
                # plain spelled copy as a function-scope decl, byte-identical.
                # (The `detail` tag distinguishes the fallback tally only.)
                # LINCHPIN: if/try/with pre-declare escaping names via
                # `_lower_hoist_predecls`; for/while/match don't, and lean on
                # the whole-function `lc.unhandled_hoists` reject as the
                # backstop -- so an un-hoisted escaping decl can never slip
                # through this gate as a block-local.
                slot_ok = (
                    _eligible_scalar(vtype) or _eligible_char(vtype)
                    or _eligible_enum(vtype, analyzer) is not None
                    or _resolved_str_value(vtype, analyzer) is not None
                    or _resolved_bytes_value(vtype, analyzer) is not None
                    or _is_string_owned(vtype)
                    or _eligible_value_union(vtype) is not None
                    # A value-repr Optional[scalar] slot (`y: Int32 | None =
                    # items[i]`): the whole `std::optional<T>` lands bare, the
                    # value-repr twin of the plain-scalar decl. The owned-view
                    # twin (`cmd: str | None = acc` -> `std::optional
                    # <std::string> cmd = acc;`) takes the same bare copy,
                    # gated to genuinely whole-optional sources.
                    or _value_opt_scalar(vtype, analyzer) is not None
                    or _owned_view_opt_whole_src(stmt, vtype, lc)
                    or _slice_object_type(vtype)
                    or _range_object_value(vtype)
                    or _eligible_ptr_value(vtype, analyzer)
                    or _callable_value(vtype)
                    # Value-form slots where borrow/storage coincide: a
                    # span and a value tuple both decl as the plain spelled
                    # copy (`std::span<T> s = sp;` / `std::tuple<...> u = t;`).
                    or _span_value(vtype)
                    # An `Any` slot (`a: Any = 42`): a value-type cell whose
                    # init is the `into_any` make_any wrap (or an already-Any
                    # source). The plain-copy decl spells `::tpy::Any a = ...`.
                    or _is_any_type(vtype)
                    or _value_tuple(vtype, analyzer) is not None)
                if not slot_ok:
                    if in_branch_first:
                        # A branch-FIRST rebind-slot record decl (reassigned
                        # with rvalues inside the branch): the two-slot
                        # machinery emits INLINE at branch indent -- the
                        # oracle's block-scoped `__slot_N` lines -- exactly
                        # the function-scope REBIND_SLOT render.
                        b_binding = _borrow_local_binding(
                            stmt, vtype, declared, lc.prescan, analyzer,
                            lc.pointers)
                        if b_binding is LocalBinding.REBIND_SLOT:
                            lc.pointers.add(stmt.name)
                            lc.rebind_slot_locals.add(stmt.name)
                            declared[stmt.name] = vtype
                            return _lower_borrow_local(
                                stmt, vtype, b_binding, False, lc, declared,
                                loc)
                    if (in_branch_first
                            and _owned_record_decl_ok(
                                stmt, vtype, lc.prescan, declared, analyzer,
                                narrowed=lc.narrow.narrowed)):
                        # A branch-FIRST owned record decl is genuinely
                        # block-local (the LINCHPIN note above: an escaping
                        # var is hoisted and never reaches here as a first
                        # decl) -- the same plain value decl as the
                        # function-scope arm, at branch indent.
                        _witness("decl.owned_record_method"
                                 if isinstance(stmt.init, TpyMethodCall)
                                 else "decl.owned_record")
                        declared[stmt.name] = vtype
                        return THIRVarDecl(
                            name=stmt.name, resolved_type=vtype,
                            init=_lower_expr(
                                stmt.init, lc, declared,
                                use=_ExprUse(
                                    result=_ExprResultUse.BORROW_BIND,
                                    allow_temps=True)),
                            cpp_type=lc.render_type(vtype),
                            form=Form.STORAGE, loc=loc)
                    note_detail("decl.branch_slot_type" if in_branch_first
                                else "decl.slot_type")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            # A Char-annotated decl init lowers target-aware: `c: Char = 'x'` ->
            # `char c = 'x';` (the AST threads the decl type into the render);
            # a float literal into a Float32 binding (annotated decl or
            # reassign of a Float32 local) takes the `f` suffix the same way.
            # A flushable statement position: a direct call init may hoist
            # arg temps (temp_args, inert for non-call inits).
            src = _peel_stale_view_owned_coerce(
                stmt.init, declared.get(stmt.name, vtype), analyzer)
            whole_optional_reassign = (
                stmt.name in declared
                and isinstance(src, TpyName)
                and _value_opt_scalar_binding(src.name, lc))
            if whole_optional_reassign:
                init = _flush_witness(
                    "flush.vardecl",
                    _lower_expr(
                        src, lc, declared, use=_ExprUse(allow_temps=True),
                        allow_whole_optional=True))
            else:
                # A value-repr Optional[scalar] slot consumes a WHOLE-optional
                # init source bare (`y: Int32 | None = items[i]` -- the
                # `std::optional<T>` element / call result lands directly), so
                # thread allow_whole_optional to admit those reads at their
                # gate. The owned-view twin (`str | None` ->
                # `optional<string>`) takes the same bare whole-optional copy
                # (`flat = rec.key;`), gated to genuinely whole-optional
                # sources (`_owned_view_opt_whole_src`).
                opt_slot = (_value_opt_scalar(vtype, analyzer) is not None
                            or _owned_view_opt_whole_src(stmt, vtype, lc))
                # A str-family FIELD read into a str-value decl slot
                # (`s = p.name` -> `std::string_view s = p.name;` for a view
                # slot, `std::string s = p.name;` for an owned one): the bare
                # member read lands at the STORAGE decl sink like the print /
                # f-string sinks admit it. Str only -- a bytes-field decl read
                # stays deferred.
                str_field_init = (
                    isinstance(src, TpyFieldAccess)
                    and _resolved_str_value(vtype, analyzer) is not None)
                init = (_flush_witness(
                            "flush.vardecl",
                            _lower_char_targeted(
                                src, vtype, lc, declared,
                                use=_ExprUse(
                                    result=_ExprResultUse.STORAGE,
                                    allow_temps=True),
                                allow_whole_optional=opt_slot,
                                field_owned_str_ok=str_field_init))
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
        # A bytes-literal init renders per the binding: a view binding takes the
        # static-storage span. On a reassign the binding is the DECLARED
        # local/param type (`_var_decl_type` falls back to the init's own type
        # there, which for a literal is owned `bytes`).
        init = _retag_bytes_literal_view(
            init,
            _resolved_bytes_value(declared[stmt.name], analyzer)
            if stmt.name in declared else bytes_t)
        # The parser emits TpyVarDecl for every `name = expr`; the AST codegen
        # treats a write to an already-declared name as a reassignment, not a
        # re-declaration. Mirror that here so first-decl emits `T x = ...` and a
        # reassignment emits `x = ...`.
        if stmt.name in declared:
            assert init is not None  # eligibility requires a var-decl init
            # A value-repr Optional[scalar/view] name reassigned to an existing
            # optional local passes the bare optional (`q = p;`): the AST's
            # reassignment RHS threads no target type, so a narrowed read is NOT
            # unwrapped here (unlike a decl init / value-target position) -- so
            # strip the name arm's deref-on-narrow. For scalars into a plain-`T`
            # slot this mirrors a pre-existing AST bug byte-identically; for an
            # optional-into-optional slot it is the correct whole-optional copy.
            if (isinstance(stmt.init, TpyName) and isinstance(init, THIRName)
                    and (_value_opt_scalar_binding(stmt.init.name, lc)
                         or _value_opt_view_binding(stmt.init.name, lc))):
                init = replace(init, deref=False)
            # No view->owned wrap on a plain reassignment: std::string has an
            # implicit operator=(string_view), and the AST emits the bare
            # `t = s;` here (the wrap is a decl-init/return-boundary shape).
            # The AST emits the same bare assign for an owned-BYTES target fed
            # a view source, which is invalid C++ (vector has no span
            # operator=) -- a pre-existing AST bug (BUGS.md); mirrored
            # byte-identically rather than silently fixed on one path.
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=init,
                loc=loc,
            )
        # Owned-str/bytes decl init off a view-form source copies explicitly --
        # `std::string u = std::string(v);` / `std::vector<uint8_t> u =
        # ::tpy::bytes_copy(v);` -- the view->owned CONSTRUCTION being explicit.
        # Mirrors the AST's `_view_source_to_owned` chokepoint; a literal init
        # (VALUE form: const char[N] / an already-owned bytes render)
        # constructs directly and stays bare.
        if (init is not None and init.form is Form.BORROW
                and ((str_t is not None and is_str_type(str_t))
                     or (bytes_t is not None and is_bytes_type(bytes_t)))):
            init = THIRFormConvert(result_type=str_t if str_t is not None else bytes_t,
                                   value=init, form=Form.STORAGE, loc=loc)
        declared[stmt.name] = vtype
        # A value-repr Optional[scalar] LOCAL binds `std::optional<T>` just like a
        # value-opt param, so register it so its reads ride the binding-keyed
        # arms (deref-on-narrow `(*y)`, the whole-optional None-test/print).
        if not is_reassign and _value_opt_scalar(vtype, analyzer) is not None:
            lc.value_opt_locals.add(stmt.name)
        # The view twin: an OWNED-inner `Optional[str]`/`Optional[bytes]` LOCAL
        # binds `std::optional<std::string>`/`<vector>`, so its None-test/deref
        # reads ride the value-repr view arms via `_value_opt_view_binding`
        # (STORAGE deref). A view-INNER optional (`StrView`/`BytesView | None`,
        # `optional<string_view>`) is excluded -- its narrowed read stays on the
        # str/bytes-name arm (no deref), matching the AST.
        elif not is_reassign and _value_opt_owned_view(vtype, analyzer) is not None:
            lc.value_opt_view_locals.add(stmt.name)
        # An enum decl type spells via render_type (codegen's type_to_cpp):
        # its enum arm routes through enum_cpp_name -- the authoritative
        # spelling for cross-module (`::tpyapp::m::E`), @native (user qname),
        # and nested (`Outer::Kind`) enums; plain to_cpp() reads the
        # native_cpp_names view, which an aliased-import collision can skew.
        # A @dynamic-protocol-pointee Ptr decl spells `auto`: the AST's
        # `_cpp_decl_type` sends any protocol-containing decl type to `auto`
        # (`auto q = p;`), unlike the spelled `T*` of record/scalar pointees.
        # A container decl carrying an enum in its args needs the same
        # render_type spelling rule as a bare enum decl.
        if _dyn_proto_ptr(vtype):
            cpp_type = "auto"
        elif (_eligible_enum(vtype, analyzer) is not None
              or _container_enum_spell(vtype, analyzer)
              or _callable_value(vtype)):
            cpp_type = lc.render_type(vtype)
        else:
            cpp_type = None
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init, cpp_type=cpp_type,
            loc=loc)
    if isinstance(stmt, TpyAssign):
        begin_stmt()
        pointers = scope.admission_pointers()
        narrowed = lc.narrow.narrowed.keys()
        er_fi = _error_return_stmt_fi(stmt.value, analyzer)
        if er_fi is not None:
            # A direct @error_return call RHS: the statement-level unwrap
            # block (before target classification, like the AST). Only the
            # declared plain-local name target routes; field/subscript
            # targets stay AST.
            if (not isinstance(stmt.target, TpyName)
                    or stmt.target.name not in declared
                    or stmt.target.name in narrowed
                    or stmt.target.name
                    in analyzer.function_global_decls.get(id(lc.func), set())):
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
        elif (isinstance(stmt.target, TpySubscript)
              and stmt.target.slice_function_info is not None
              and isinstance(stmt.target.index, TpySlice)):
            # `c[a:b] = v` / `c[a:b:s] = v` -> list_set_slice /
            # list_set_stepped_slice. Its own gate + node, distinct from the
            # single-index subscript-write arm below.
            return _lower_slice_assign(stmt, lc, declared, loc)
        elif isinstance(stmt.target, TpySubscript):
            any_dict_write = _any_dict_setitem_ok(
                stmt, declared, pointers, narrowed, analyzer)
            user_setitem = (not any_dict_write and _user_record_setitem_ok(
                stmt, declared, pointers, narrowed, analyzer))
            if not (any_dict_write or user_setitem or _container_setitem_ok(
                    stmt, declared, pointers, narrowed, analyzer)):
                raise ThirUnsupported(stmt_reject_reason(stmt))
        elif (isinstance(stmt.target, TpyFieldAccess)
              and stmt.target.property_setter_call is not None):
            # A `@prop.setter` write is a void setter method call in disguise
            # (`c.value = v` -> `c.set_value(v)`): _gen_assign_code delegates
            # to _gen_method_call, so lower the synthesized call through the
            # method-call arm in discard (statement) position.
            return THIRExprStmt(
                expr=_lower_expr(
                    stmt.target.property_setter_call, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.DISCARD,
                                 allow_temps=True)),
                loc=loc)
        elif (isinstance(stmt.target, TpyFieldAccess)
              and stmt.target.dyn_setattr_call is not None):
            # D16 dyn-attr write: the statement IS the sema-synthesized
            # `obj.__setattr__("name", <value>)` (the AST's early-return
            # method-call arm in _gen_assign_code).
            return THIRExprStmt(
                expr=_lower_dyn_setattr_call(
                    stmt.target.dyn_setattr_call, lc, declared),
                loc=loc)
        elif not (
                _f2b_optional_field_write_ok(
                    stmt, declared, pointers, analyzer)
                or _class_const_write_target_ok(
                    stmt.target, declared, lc.pointers, analyzer)
                or _scalar_field_write_ok(stmt, declared, analyzer)
                or _user_deref_field_write_ok(
                    stmt, declared, narrowed, analyzer, pointers)
                or _f1_tuple_field_write_ok(
                    stmt, declared, lc.storage_tuple_locals, analyzer)
                or _ptr_union_field_write_ok(stmt, declared, analyzer)
                or _record_field_write_ok(
                    stmt, declared, analyzer, pointers, narrowed, lc.prescan)
                or _optional_record_field_write_ok(
                    stmt, declared, pointers, analyzer, narrowed, lc.prescan)
                or _optional_field_none_write_ok(stmt, declared, analyzer)
                or _container_field_write_ok(stmt, declared, analyzer)
                or _str_field_write_ok(stmt, declared, analyzer)
                or _bytes_field_write_ok(stmt, declared, analyzer)
                or _container_name_field_write_ok(
                    stmt, declared, pointers, narrowed, analyzer)):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        target_prechecked = isinstance(stmt.target, TpyFieldAccess)
        if isinstance(stmt.target, TpySubscript) and user_setitem:
            # User-record `recv[key] = v` -> the checked THIRSetItem, which
            # emits `::tpy::__setitem__(recv, key, v)` (the AST's no-container
            # fallback). The target lowers through the record_getitem READ arm
            # (NOT subscript_prechecked, which is the container-element path):
            # its receiver/index feed the checked write emit; the value renders
            # against the elem slot bare (a value scalar / Char / enum).
            target = _lower_expr(stmt.target, lc, declared)
            elem_t = analyzer.get_expr_type(stmt.target)
            value = _slot_literal_retype(
                _flush_witness(
                    "flush.assign",
                    _lower_expr(stmt.value, lc, declared,
                                use=_ExprUse(result=_ExprResultUse.STORAGE,
                                             allow_temps=True))),
                elem_t, lc)
            _witness("setitem.user_record")
            return THIRSetItem(target=target, value=value, loc=loc)
        if isinstance(stmt.target, TpySubscript):
            # Container subscript write: the target lowers to the same
            # subscript node a read produces (bounds_safe + the BigInt index
            # narrow ride along); the value renders against the element slot
            # (literal retype), with the view->owned `std::string(v)` copy
            # for a view-form str source into an owned-str element -- the
            # AST's `_view_source_to_owned` chokepoint. A flushable position
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
            target = _lower_expr(
                stmt.target, lc, declared,
                field_prechecked=target_prechecked,
                subscript_prechecked=(any_dict_write or widened_elem
                                      or ba_write))
            if eu is not None and (is_list(eu) or is_dict(eu) or is_array(eu)):
                # Nested-container element slot: only a container-LITERAL
                # value routes (the target-threaded render). The checked
                # `__setitem__` template cannot deduce a bare brace-init, so
                # it takes the type prefix (typed_brace_init); the bounds-safe
                # lvalue path binds the brace directly (no prefix).
                if not isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                               TpySetLiteral)):
                    note_detail("setitem.container_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = _lower_expr(stmt.value, lc, declared, target_type=eu)
                if (not target.bounds_safe
                        and isinstance(value, THIRContainerLiteral)):
                    value = replace(value,
                                    typed_brace_cpp=lc.render_type(eu))
                _witness("setitem.container_value")
            elif _optional_record_field_inner(eu, analyzer) is not None:
                # Pointer-repr Optional[F1] element: a borrow `T*` NAME lifts
                # borrow->storage (`::tpy::ptr_to_optional(p)` -- the element
                # store COPIES, never moves; _lift_to_element_storage). Other
                # sources (None literal, narrowed names, rvalues) stay AST.
                v = stmt.value
                if not (isinstance(v, TpyName)
                        and v.name not in lc.narrow.narrowed
                        and _is_borrow_ptr_local(v, declared, lc.pointers)
                        and not _is_move_source(v, lc)):
                    note_detail("setitem.optional_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = THIRFormConvert(
                    result_type=eu, value=_lower_expr(v, lc, declared),
                    form=Form.STORAGE, loc=loc)
                _witness("setitem.borrow_lift")
            elif _value_opt_scalar(eu, analyzer) is not None:
                # Value-repr Optional[scalar] element: a None literal stores
                # the STORAGE-form `std::nullopt` (`::tpy::__setitem__(items,
                # 0, std::nullopt)` -- the decl.opt_none twin). Other value
                # sources (scalars, whole optionals, narrowed reads) stay
                # deferred until their renders are witnessed.
                if not isinstance(stmt.value, TpyNoneLiteral):
                    note_detail("setitem.optval_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = THIRLiteral(result_type=eu, value=None,
                                    form=Form.STORAGE, loc=loc)
                _witness("setitem.optval_none")
            elif _eligible_ptr_union(eu, analyzer) is not None:
                # Value-variant union element: a same-union borrow NAME lifts
                # via `::tpy::to_value_variant<...>` (copy); a NARROWED member
                # name is already the concrete alternative and constructs the
                # element directly (bare -- the lift would be ill-formed).
                v = stmt.value
                if not (isinstance(v, TpyName) and v.name in declared
                        and v.name not in lc.pointers
                        and not _is_move_source(v, lc)):
                    note_detail("setitem.union_value_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                lowered = _lower_expr(v, lc, declared)
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
            else:
                value = _slot_literal_retype(
                    _flush_witness("flush.assign",
                                   _lower_expr(
                                       stmt.value, lc, declared,
                                       use=_ExprUse(
                                           result=_ExprResultUse.STORAGE,
                                           allow_temps=True))),
                    elem_t, lc)
                elem_str = _resolved_str_value(elem_t, analyzer)
                if (value.form is Form.BORROW and elem_str is not None
                        and is_str_type(elem_str)):
                    _witness("setitem.str_owned_copy")
                    value = THIRFormConvert(result_type=elem_str, value=value,
                                            form=Form.STORAGE, loc=loc)
            _witness("setitem.bounds_safe" if target.bounds_safe
                     else "setitem.checked")
            if isinstance(stmt.target.obj, TpyFieldAccess):
                _witness("setitem.field_recv")
            return THIRSetItem(target=target, value=value, loc=loc)
        if isinstance(stmt.target, TpyFieldAccess):
            # A borrow `T*` stored into a storage `optional<T>` field lifts
            # borrow->storage via THIRFormConvert (`ptr_to_optional`, F2b); a
            # `None` literal stores as a STORAGE-form None (`std::nullopt`, F2c).
            # The target field-access renders `recv.field` / `recv->field`.
            ftype = analyzer.get_expr_type(stmt.target)
            # `recv.opt = None` at an Optional field: the storage is
            # `std::optional<T>` regardless of the (possibly flow-narrowed to the
            # inner) read type, so the store is `std::nullopt`. Keyed on the
            # DECLARED field type so a narrowed write site (`self.slot` proven
            # non-None) still stores nullopt, not a misrouted inner-typed None.
            if isinstance(stmt.value, TpyNoneLiteral):
                fdt = _field_decl_type(stmt.target, declared, analyzer)
                fdt_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))
                         if fdt is not None else None)
                if isinstance(fdt_u, OptionalType):
                    _witness("field_write.opt_none")
                    return THIRAssign(
                        target=_lower_field_write_target(stmt, lc, declared),
                        value=THIRLiteral(result_type=fdt_u, value=None,
                                          form=Form.STORAGE, loc=loc),
                        loc=loc)
            # A class-constant / classvar write: the bare qualified lvalue
            # (receiver eval split off), then the same target-typed value
            # render as the scalar field write -- the AST's class-constant
            # assign arm shares its value shape with the default field path.
            if stmt.target.class_constant_owner is not None:
                target, recv_eval, recv_wrap = _lower_class_const_write_target(
                    stmt.target, lc, declared, loc)
                _witness("field_write.class_const")
                return THIRAssign(
                    target=target,
                    value=_slot_literal_retype(
                        _flush_witness(
                            "flush.field_write",
                            _lower_expr(stmt.value, lc, declared,
                                        use=_ExprUse(
                                            result=_ExprResultUse.STORAGE,
                                            allow_temps=True))),
                        ftype, lc),
                    recv_eval=recv_eval, recv_wrap=recv_wrap, loc=loc)
            # A scalar / Char / Ptr field is a plain value assign -- no
            # borrow<->storage lift. The field write is the fifth flushable
            # statement position (`temp_args`).
            if (_eligible_scalar(ftype) or _eligible_char(ftype)
                    or _eligible_enum(ftype, analyzer) is not None
                    or _eligible_ptr_value(ftype, analyzer)):
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=_slot_literal_retype(
                                      _flush_witness(
                                          "flush.field_write",
                                          _lower_expr(stmt.value, lc, declared,
                                                      use=_ExprUse(
                                                          result=_ExprResultUse.STORAGE,
                                                          allow_temps=True))),
                                      ftype, lc), loc=loc)
            # A plain F1-record field write (`_record_field_write_ok`): a
            # record rvalue -- a ctor (STORAGE) or a by-value record-returning
            # call (VALUE) of the field's own type -- copies bare into the
            # field, no borrow<->storage lift (the AST's default field assign);
            # a record NAME source copies bare (`field = p;`) or moves at a
            # movable name's last use (`std::move(p)`, the plain-record
            # STORAGE convert arm -- `_maybe_move`'s mirror). A plain record
            # ftype reaching here is uniquely these shapes (scalar/char/enum/
            # ptr handled above; Optional/tuple/union are not NominalType
            # records, so `_f1_record` excludes them).
            if _f1_record(ftype, analyzer):
                if isinstance(stmt.value, TpyName):
                    _witness("field_write.record_name")
                    lowered = _lower_expr(stmt.value, lc, declared)
                    if _is_move_source(stmt.value, lc):
                        lowered = THIRFormConvert(result_type=ftype,
                                                  value=lowered,
                                                  form=Form.STORAGE,
                                                  move=True, loc=loc)
                    return THIRAssign(
                        target=_lower_field_write_target(stmt, lc, declared),
                        value=lowered, loc=loc)
                _witness("field_write.record_rvalue")
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=_lower_expr(
                                      stmt.value, lc, declared,
                                      target_type=ftype), loc=loc)
            # A container-literal field write (`_container_field_write_ok`):
            # the target-threaded literal render assigns bare (a literal is
            # never a movable name) -- the same THIRContainerLiteral emit a
            # decl init gets, consumed by the field lvalue.
            if isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                       TpySetLiteral)):
                _witness("field_write.container_lit")
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
            # A str-family field write (`_str_field_write_ok`): the value
            # renders BARE -- `std::string::operator=(string_view)` absorbs a
            # view source, so no view->owned construction and no move wrap
            # (str names are never in codegen's movable set).
            if _resolved_str_value(ftype, analyzer) is not None:
                _witness("field_write.str")
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
            # An owned `bytes` field write: a view (span) source copies via the
            # S6 `::tpy::bytes_copy(...)` STORAGE convert (vector has no span
            # ctor) -- an owned source (bytes literal / owned local) lands bare.
            bytes_ft = _resolved_bytes_value(ftype, analyzer)
            if bytes_ft is not None and is_bytes_type(bytes_ft):
                _witness("field_write.bytes")
                bval = _lower_expr(stmt.value, lc, declared)
                if bval.form is Form.BORROW:
                    bval = THIRFormConvert(result_type=bytes_ft, value=bval,
                                           form=Form.STORAGE, move=False, loc=loc)
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=bval, loc=loc)
            # A value-storage Optional[record] field (`std::optional<inner>`):
            # a record RVALUE / record NAME source copies bare
            # (optional::operator= absorbs the inner lvalue/rvalue) or moves the
            # inner at a movable name's last use -- the record-field-write shape
            # at an Optional slot (`_optional_record_field_write_ok`). The F2b
            # `T*`->ptr_to_optional lift keeps its pointer-local source, in
            # `lc.pointers`, on the generic tail below.
            opt_inner = _optional_record_field_inner(ftype, analyzer)
            if (opt_inner is not None
                    and not isinstance(stmt.value, TpyNoneLiteral)
                    and not (isinstance(stmt.value, TpyName)
                             and stmt.value.name in lc.pointers)):
                if isinstance(stmt.value, TpyName):
                    _witness("field_write.optrec_name")
                    lowered = _lower_expr(stmt.value, lc, declared)
                    mv = _is_move_source(stmt.value, lc)
                    # A BORROW source (a borrow record param / REF_ALIAS) lifts to
                    # the INNER record storage -- the `is_plain_nonvalue` STORAGE
                    # arm renders it bare, optional::operator= then absorbs it --
                    # so the pointer-lifted-sink validator sees no BORROW form
                    # lie. A move (an owned name at last use) wraps to render
                    # `std::move(inner)`. A move-free STORAGE source (an Own param
                    # NOT at last use) copies bare -- no (no-op) convert. Convert
                    # to the INNER record, NOT the Optional: that would spell the
                    # F2b `ptr_to_optional` lift.
                    if lowered.form is Form.BORROW or mv:
                        lowered = THIRFormConvert(result_type=opt_inner,
                                                  value=lowered,
                                                  form=Form.STORAGE, move=mv,
                                                  loc=loc)
                    return THIRAssign(
                        target=_lower_field_write_target(stmt, lc, declared),
                        value=lowered, loc=loc)
                _witness("field_write.optrec_rvalue")
                return THIRAssign(target=_lower_field_write_target(
                                      stmt, lc, declared),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
            if isinstance(stmt.value, TpyNoneLiteral):
                fvalue: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                               form=Form.STORAGE, loc=loc)
            elif (isinstance(stmt.value, TpyFieldAccess)
                    and _eligible_ptr_union(ftype, analyzer) is not None):
                # F4 U2 field-to-field union copy: a field source is not a
                # ptr-variant source on the AST path, so it assigns
                # storage-to-storage bare -- no to_value_variant lift.
                fvalue = _lower_field_source(stmt.value, lc, declared)
            else:
                lowered = _lower_expr(stmt.value, lc, declared)
                mv = _is_move_source(stmt.value, lc)
                # A storage-form source of the field's own type needing no move
                # is a bare copy (`field = v`); the borrow->storage convert would
                # be a no-op (validate.py rejects it). This is the value-bound
                # `Own[T]` field write: the param is passed by value and copied
                # here, mirroring the AST method body -- unlike the ctor MIL,
                # which moves. The movable case keeps the convert (it emits
                # `std::move`).
                if (not mv and lowered.form is Form.STORAGE
                        and lowered.result_type == ftype):
                    fvalue = lowered
                else:
                    fvalue = THIRFormConvert(result_type=ftype, value=lowered,
                                             form=Form.STORAGE, move=mv, loc=loc)
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=fvalue, loc=loc)
        # Name-target assign: the same self-append peephole as the var-decl
        # reassignment (the AST checks it at both sites).
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            rhs = _str_self_append_rhs(stmt.target.name, stmt.value)
            if rhs is not None:
                return THIRStrAppend(target=stmt.target.name,
                                     value=_lower_expr(rhs, lc, declared), loc=loc)
        return THIRAssign(
            target=(_lower_field_write_target(stmt, lc, declared)
                    if target_prechecked
                    else _lower_expr(stmt.target, lc, declared)),
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
        narrowed = lc.narrow.narrowed.keys()
        if (isinstance(stmt.target, TpyName)
                and stmt.target.name in narrowed):
            raise ThirUnsupported("stmt.aug_assign")
        inplace = _list_inplace_extend(stmt, lc, declared)
        if inplace is not None:
            return inplace
        if isinstance(stmt.target, TpySubscript):
            aug_ok = _container_aug_setitem_ok(
                stmt, declared, scope.admission_pointers(), narrowed,
                analyzer)
        else:
            aug_ok = (_scalar_aug_assign_ok(stmt, declared, analyzer)
                      or _class_const_aug_assign_ok(
                          stmt, declared, lc.pointers, analyzer)
                      or _str_aug_append_ok(
                          stmt, declared, lc.prescan, analyzer)
                      or _bytes_aug_concat_ok(
                          stmt, declared, lc.prescan, analyzer))
        if not aug_ok:
            raise ThirUnsupported("stmt.aug_assign")
        # str `t += v`: the in-place append (the AST's string branch inside the
        # resolved-binop arm), not the synthetic binop below. The target-type
        # dispatch mirrors _str_aug_append_ok's admission. A str-FIELD target
        # (`recv.field += v`) carries its lowered lvalue on target_expr.
        if _owned_str_append_target(
                analyzer.get_expr_type(stmt.target), analyzer):
            if isinstance(stmt.target, TpyName):
                return THIRStrAppend(
                    target=stmt.target.name,
                    value=_lower_expr(stmt.value, lc, declared), loc=loc)
            if isinstance(stmt.target, TpyFieldAccess):
                return THIRStrAppend(
                    target="",
                    target_expr=_lower_expr(
                        stmt.target, lc, declared,
                        field_prechecked=True),
                    value=_lower_expr(stmt.value, lc, declared), loc=loc)
        # `target OP= value` lowers to `target = (target OP value)`, matching the
        # AST's `_gen_aug_assign_code` scalar branch -- and the bytes
        # concat-and-assign (`t = ::tpy::bytes_concat(t, v);`, the same
        # resolved-binop arm with the native emit, likewise unwrapped). The
        # target expr is lowered
        # twice (once as the assign lvalue, once as the binop's left operand) --
        # the AST likewise substitutes the same target string into both slots.
        # `divisor_non_zero=False`: the AST aug-assign path never swaps the
        # checked div/mod helper (no source `TpyBinOp` node carries the flag).
        # A bytes target's binding is a PendingBytesType; resolve it (and tag
        # the owned concat result STORAGE) so the nodes carry final types.
        tgt_type = analyzer.get_expr_type(stmt.target)
        tgt_type = _resolve_pending_view(tgt_type, analyzer) or tgt_type
        tgt_bytes = _resolved_bytes_value(tgt_type, analyzer)
        target_prechecked = isinstance(stmt.target, TpyFieldAccess)
        recv_eval = recv_wrap = None
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            # Class-constant lvalue: the receiver eval splits off ONCE; the
            # bare qualified name lands on both sides of the synthetic
            # `target = (target OP value)` -- exactly as the AST substitutes
            # gen_class_constant_lvalue's string twice.
            target, recv_eval, recv_wrap = _lower_class_const_write_target(
                stmt.target, lc, declared, loc)
            left: THIRExpr = target
            _witness("aug.class_const")
        else:
            target = _lower_expr(
                stmt.target, lc, declared,
                field_prechecked=target_prechecked)
            left = _lower_expr(
                stmt.target, lc, declared,
                field_prechecked=target_prechecked)
        cast_t = analyzer.get_expr_type(stmt.target)
        if isinstance(stmt.target, TpySubscript):
            # The subscript read-modify-write pair always renders the CHECKED
            # dunders -- _gen_aug_assign_subscript_code never takes the
            # bounds-safe operator[] -- so the node fact is forced off on
            # both reads. The cast keys on the RESOLVED element scalar (the
            # AST reads get_resolved_type(obj).get_element_type(); a
            # literal-seeded local's element read is still an IntLiteralType
            # here).
            target = replace(target, bounds_safe=False)
            left = replace(left, bounds_safe=False)
            if cast_t is not None:
                cast_t = resolve_int_literals(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(cast_t))),
                    analyzer.ctx.default_int_for_literal)
        _, aug_rslot = _rb_operand_slots(stmt.resolved_binop)
        # FixedInt += BigInt: the AST wraps the value in
        # `({0}).to_fixed_check<T>()` BEFORE the binop substitution (sema
        # resolved the binop over the target width) -- mirrored as the
        # per-side operand cast. Same predicates as _gen_aug_assign_code's.
        right_cast = None
        if (is_fixed_int_type(cast_t)
                and is_big_int_type(analyzer.get_expr_type(stmt.value))):
            _witness("narrow.aug_value")
            right_cast = ("({0}).to_fixed_check<"
                          f"{cast_t.to_cpp()}>()")
        binop = THIRBinOp(
            result_type=tgt_type,
            left=left,
            op=stmt.op,
            right=_slot_literal_retype(
                _lower_expr(stmt.value, lc, declared), aug_rslot, lc),
            right_cast=right_cast,
            resolved=stmt.resolved_binop,
            paren_wrap=False,
            form=(Form.STORAGE if tgt_bytes is not None
                  and is_bytes_type(tgt_bytes) else Form.VALUE),
            loc=loc,
        )
        if isinstance(stmt.target, TpySubscript):
            _witness("setitem.aug")
            if isinstance(stmt.target.obj, TpyFieldAccess):
                _witness("setitem.field_recv")
            return THIRSetItem(target=target, value=binop, loc=loc)
        return THIRAssign(target=target, value=binop,
                          recv_eval=recv_eval, recv_wrap=recv_wrap, loc=loc)
    if isinstance(stmt, TpyReturn):
        begin_stmt()
        if stmt.value is not None and lc.error_return_cpp is not None:
            rfi = _error_return_stmt_fi(stmt.value, analyzer)
            if rfi is not None:
                # Returning an @error_return call from an @error_return
                # function passes the std::expected through directly -- no
                # unwrap+rewrap (the AST's current_error_return return arm).
                # The AST reaches that arm only past its Optional /
                # ptr-variant / property-getter returns; mirror the gate.
                if not isinstance(stmt.value, TpyCall):
                    note_detail("error_return.ret_shape")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                _reject_nested_error_return_arg(stmt, stmt.value, analyzer)
                rtb = (unwrap_readonly(unwrap_ref_type(lc.func.return_type))
                       if isinstance(lc.func.return_type, TpyType) else None)
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
                    loc=loc)
        if stmt.value is not None and not lc.prescan.ret_supported:
            # An `Any` return slot (`::tpy::Any` by value) has exactly one
            # routed source: an Any-valued dict subscript read
            # (`return self._data[name];` -> the bare checked
            # `::tpy::__getitem__(recv, k)` rvalue, no wrap -- both slot and
            # value are already Any, so no coercion can fire on either path).
            ret_t = (lc.func.return_type
                     if isinstance(lc.func.return_type, TpyType) else None)
            if (_is_any_type(ret_t)
                    and isinstance(stmt.value, TpySubscript)
                    and _is_any_type(analyzer.get_expr_type(stmt.value))
                    and _any_dict_subscript_shape_ok(
                        stmt.value, declared, scope.admission_pointers(),
                        lc.narrow.narrowed.keys(), analyzer)):
                _witness("ret.any_subscript")
                return THIRReturn(
                    value=_flush_witness(
                        "flush.return",
                        _lower_expr(stmt.value, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True),
                                    subscript_prechecked=True)),
                    loc=loc)
            # A bare Any NAME (`return a`): the Any cell is a value type, so it
            # returns bare (NRVO / value copy), no wrap and no last-use move
            # (value types are never move sources at returns).
            if (_is_any_type(ret_t)
                    and isinstance(stmt.value, TpyName)
                    and stmt.value.name in declared
                    and stmt.value.name not in scope.admission_pointers()
                    and stmt.value.name not in lc.narrow.narrowed
                    and _is_any_type(declared.get(stmt.value.name))):
                _witness("ret.any_name")
                return THIRReturn(
                    value=_flush_witness(
                        "flush.return",
                        _lower_expr(stmt.value, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.STORAGE,
                                        allow_temps=True))),
                    loc=loc)
            note_detail("return.slot_type")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        pointers = scope.admission_pointers()
        narrowed = lc.narrow.narrowed.keys()
        ret_tuple = lc.prescan.ret_borrow_tuple
        if stmt.value is not None and ret_tuple is not None:
            # Lift a storage tuple lvalue into the borrow-form tuple return via
            # `tuple_to_pointer` (F3). The element pointers' const-ness tracks the
            # source, mirroring the F1 OPTIONAL_TO_PTR const bump; sema forces a
            # mutable source when the return borrows mutably, so the const arm only
            # fires for a const source returning a const-element tuple. The source is
            # a storage-tuple alias local (`return t`) or a field read (`return h.pair`).
            if isinstance(stmt.value, TpyName):
                if stmt.value.name not in lc.storage_tuple_locals:
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                is_const = stmt.value.name in lc.const_locals
                inner: THIRExpr = _lower_expr(stmt.value, lc, declared)  # STORAGE-form alias
            else:
                if not (_const_exact_field_receiver_ok(
                            stmt.value, declared, analyzer)
                        and _f1_tuple(
                            analyzer.get_expr_type(stmt.value), analyzer)
                        is not None):
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                recv = stmt.value.obj  # TpyName (validated by _field_receiver_ok)
                is_const = (recv.name in lc.const_locals
                            or _param_is_const(recv.name, lc.func, analyzer,
                                               lc.record_name))
                inner = _lower_field_source(stmt.value, lc, declared)
            value: THIRExpr = THIRFormConvert(
                result_type=ret_tuple, value=inner,
                form=Form.BORROW, is_const=is_const, loc=loc)
            return THIRReturn(value=value, loc=loc)
        ret_opt = lc.prescan.ret_storage_opt
        if stmt.value is not None and ret_opt is not None:
            # Lift into a storage-form Optional[record] return slot. `None` lowers
            # to a STORAGE-form None literal (`std::nullopt`, F2c); a borrow `T*`
            # to the borrow->storage THIRFormConvert the F2b write uses --
            # `ptr_to_optional` (copy, F2c) or `ptr_to_optional_move` when the
            # source is an owned local at last use (move, F2e, via _is_move_source).
            opt_inner = unwrap_readonly(_unwrap_own(unwrap_readonly(ret_opt.inner)))
            if isinstance(stmt.value, TpyNoneLiteral):
                value: THIRExpr = THIRLiteral(result_type=ret_opt, value=None,
                                              form=Form.STORAGE, loc=loc)
            elif (_record_rvalue_source_shape(stmt.value, analyzer)
                    and analyzer.get_expr_type(stmt.value) == opt_inner):
                # A record RVALUE (ctor / by-value call) of the optional's
                # inner returns bare into the `std::optional<T>` slot -- its
                # converting ctor absorbs the record rvalue (F2c value-storage
                # / Own-optional return, `return Coord(0, 0)`).
                _witness("ret.storage_opt_rvalue")
                return THIRReturn(
                    value=_flush_witness(
                        "flush.return",
                        _lower_expr(
                            stmt.value, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True))),
                    loc=loc)
            else:
                if not _is_borrow_ptr_local(
                        stmt.value, declared, pointers):
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                value = THIRFormConvert(result_type=ret_opt,
                                        value=_lower_expr(stmt.value, lc, declared),
                                        form=Form.STORAGE,
                                        move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRReturn(value=value, loc=loc)
        ret_popt = lc.prescan.ret_ptr_opt
        if stmt.value is not None and ret_popt is not None:
            # A pointer-repr Optional[F1-record] return (`A*` by value) --
            # _optional_pointer_form_value's admitted subset: `None` ->
            # `nullptr` (a BORROW-form None literal), an already-pointer
            # borrow name (in lc.pointers: an Optional-ptr param /
            # OPTIONAL_TO_PTR local / F2 pointer-local) -> bare, a plain
            # F1-record name -> the `&(name)` lift (the optional-ptr arg
            # node's addr_of render, position-independent).
            if isinstance(stmt.value, TpyNoneLiteral):
                pvalue: THIRExpr = THIRLiteral(result_type=ret_popt, value=None,
                                               form=Form.BORROW, loc=loc)
            elif (isinstance(stmt.value, TpyFieldAccess)
                    and not lc.func.is_property_getter
                    and _field_receiver_ok(stmt.value, declared, analyzer)
                    and _optional_ptr_borrow(
                        analyzer.get_expr_type(stmt.value), analyzer)
                    is not None):
                # `return self.f` where f is a storage Optional[F1-record]
                # field lifts the whole `std::optional<T>` member to the `T*`
                # return via `optional_to_ptr` (the STORAGE->BORROW convert).
                # A @property getter is EXCLUDED: it returns the whole
                # `std::optional<T>` BY REFERENCE (`std::optional<T>&`), a
                # bare field read -- handled by the property-ref arm below.
                _witness("ret.ptr_opt_field")
                pvalue = THIRFormConvert(
                    result_type=ret_popt,
                    value=_lower_field_source(stmt.value, lc, declared),
                    form=Form.BORROW, loc=loc)
            elif (isinstance(stmt.value, TpyFieldAccess)
                    and lc.func.is_property_getter
                    and _field_receiver_ok(stmt.value, declared, analyzer)
                    and _optional_ptr_borrow(
                        analyzer.get_expr_type(stmt.value), analyzer)
                    is not None):
                # A @property getter returning `self.f` (Optional[F1-record]
                # field) returns the whole `std::optional<T>` member BY
                # REFERENCE (`std::optional<T>&`, is_property_getter's storage
                # override), so the field reads bare (STORAGE) -- no
                # optional_to_ptr lift.
                _witness("ret.opt_field_ref")
                pvalue = _lower_field_source(stmt.value, lc, declared)
            else:
                if (not isinstance(stmt.value, TpyName)
                        or stmt.value.name == "self"
                        or stmt.value.name in narrowed):
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if _optional_ptr_borrow_name(
                        stmt.value, declared, analyzer) is not None:
                    pvalue = _lower_expr(stmt.value, lc, declared)
                else:
                    dt = declared.get(stmt.value.name)
                    dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
                          if dt is not None else None)
                    if isinstance(dt, OwnType) or not _f1_record(dt, analyzer):
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    pvalue = THIROptionalPtrArg(
                        result_type=ret_popt, form=Form.BORROW,
                        value=_lower_expr(stmt.value, lc, declared),
                        addr_of=True, loc=loc)
            return THIRReturn(value=pvalue, loc=loc)
        ret_vopt = lc.prescan.ret_value_opt
        if stmt.value is not None and ret_vopt is not None:
            # A value-repr Optional[cheap scalar] return (`std::optional<T>`).
            # `None` -> a STORAGE-form None literal (`std::nullopt`); a value-opt
            # param name passes the WHOLE optional bare (`return p;`), stripping
            # the name arm's deref-on-narrow exactly as the value-optional call
            # slot does; every other scalar source rides the generic tail below
            # (its narrowed `(*p)` operand reads and any widening coerce render
            # the same as the AST's `gen_expr_deref` at a value-optional target).
            if isinstance(stmt.value, TpyNoneLiteral):
                _witness("ret.value_opt_none")
                return THIRReturn(
                    value=THIRLiteral(result_type=ret_vopt, value=None,
                                      form=Form.STORAGE, loc=loc), loc=loc)
            if (isinstance(stmt.value, TpyName)
                    and _value_opt_scalar_binding(stmt.value.name, lc)):
                _witness("ret.value_opt_name")
                return THIRReturn(
                    value=replace(
                        _lower_expr(
                            stmt.value, lc, declared,
                            allow_whole_optional=True),
                        deref=False),
                    loc=loc)
            if (isinstance(stmt.value, TpyFieldAccess)
                    and _field_receiver_ok(stmt.value, declared, analyzer)
                    and _value_opt_scalar(
                        analyzer.get_expr_type(stmt.value), analyzer)
                    is not None):
                # An un-narrowed whole value-Optional field read
                # (`return self.f`) passes the bare `std::optional<T>` member
                # into the matching value-Optional return slot -- the same
                # whole-optional read the print sink admits (field_prechecked
                # bypasses the value-position field-result gate).
                _witness("ret.value_opt_field")
                return THIRReturn(
                    value=_lower_expr(
                        stmt.value, lc, declared,
                        field_prechecked=True, allow_whole_optional=True),
                    loc=loc)
            peeled = _peel_coerce(stmt.value)
            if (isinstance(peeled, TpyName)
                    and (peeled.name in lc.prescan.value_opt_params
                         or peeled.name in lc.value_opt_locals)):
                note_detail("return.optval_coerced_param")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        ret_vopt_view = lc.prescan.ret_value_opt_view
        if stmt.value is not None and ret_vopt_view is not None:
            # `None` -> `std::nullopt` (STORAGE None literal, target-typed); a
            # value-repr Optional[view] param name -> the view->owned shim
            # (`x ? std::make_optional(<conv>(*x)) : std::nullopt`, family conv),
            # passing the whole optional. A str/bytes literal rides the generic
            # tail (the owned literal lands bare into the optional).
            if isinstance(stmt.value, TpyNoneLiteral):
                _witness("ret.value_opt_view_none")
                return THIRReturn(
                    value=THIRLiteral(result_type=ret_vopt_view, value=None,
                                      form=Form.STORAGE, loc=loc), loc=loc)
            if (isinstance(stmt.value, TpyName)
                    and _value_opt_view_param(stmt.value.name, lc)):
                _witness("ret.value_opt_view_shim")
                return THIRReturn(
                    value=THIROptViewArg(result_type=ret_vopt_view,
                                         name=stmt.value.name, form=Form.VALUE,
                                         loc=loc), loc=loc)
            if not isinstance(stmt.value, (TpyStrLiteral, TpyBytesLiteral)):
                note_detail("return.opt_view_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("ret.value_opt_view_literal")
        if (stmt.value is not None
                and (lc.prescan.ret_record_borrow is not None
                     or lc.prescan.ret_record_storage is not None)):
            record_ok = False
            if _record_rvalue_source_shape(stmt.value, analyzer):
                record_ok = bool(_witness("ret.record_storage"))
            elif (lc.prescan.ret_record_borrow is not None
                  and lc.prescan.has_self
                  and isinstance(stmt.value, TpyName)
                  and stmt.value.name == "self"
                  and _f1_record(declared.get("self"), analyzer)):
                record_ok = bool(_witness("ret.record_self"))
            elif (lc.prescan.ret_record_borrow is not None
                  and isinstance(stmt.value, TpyFieldAccess)
                  and _field_receiver_ok(stmt.value, declared, analyzer)
                  and _record_borrow_return(
                      analyzer.get_expr_type(stmt.value), analyzer)
                  is not None):
                record_ok = bool(_witness("ret.record_field"))
            elif (lc.prescan.ret_record_borrow is not None
                  and isinstance(stmt.value, TpySubscript)
                  and _container_record_elem_subscript(
                      stmt.value, declared, analyzer)):
                record_ok = bool(_witness("ret.record_subscript"))
            elif (isinstance(stmt.value, TpyName)
                  and stmt.value.name != "self"
                  and stmt.value.name not in narrowed
                  and stmt.value.name not in pointers):
                dt = declared.get(stmt.value.name)
                dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
                      if dt is not None else None)
                if not (lc.prescan.ret_record_borrow is not None
                        and isinstance(dt, OwnType)):
                    face = ("ret.record_borrow"
                            if lc.prescan.ret_record_borrow is not None
                            else "ret.record_storage")
                    record_ok = bool(
                        _f1_record(dt, analyzer) and _witness(face))
            if not record_ok:
                note_detail(_record_source_reject_detail(
                    stmt.value, pointers, narrowed,
                    lc.prescan.ret_record_borrow is not None))
                raise ThirUnsupported(stmt_reject_reason(stmt))
        if lc.prescan.ret_record_borrow is not None and stmt.value is not None:
            # The record borrow-return sources beyond a bare name: `return
            # self` derefs the receiver pointer (`return (*this);`, the AST's
            # indirect-name arm); `return recv.field` renders the bare
            # storage-form field read. Bare names ride the generic tail below.
            if (isinstance(stmt.value, TpyName)
                    and stmt.value.name == lc.self_receiver):
                # A VALUE record's slot returns by value -- the deref'd
                # receiver is read as a value (like a value-record name),
                # keeping the validator's no-BORROW-at-value-return rule
                # honest; a non-value record's slot binds `Box&` (BORROW).
                self_t = analyzer.get_expr_type(stmt.value)
                return THIRReturn(
                    value=THIRSelf(
                        result_type=self_t,
                        form=(Form.BORROW if _is_borrow_form_name(self_t)
                              else Form.VALUE),
                        deref=True, loc=loc),
                    loc=loc)
            if isinstance(stmt.value, TpyFieldAccess):
                return THIRReturn(value=_lower_field_source(stmt.value, lc, declared),
                                  loc=loc)
            if isinstance(stmt.value, TpySubscript):
                # `return c[i]` -- a container record-element subscript yields
                # the `T&` element lvalue (`::tpy::__getitem__(c, i)`), returned
                # bare into the `T&` borrow slot.
                return THIRReturn(
                    value=_lower_expr(stmt.value, lc, declared,
                                      subscript_prechecked=True),
                    loc=loc)
        if stmt.value is not None and lc.prescan.ret_container_storage is not None:
            source = stmt.value
            container_ok = False
            if isinstance(source, TpyName):
                if (source.name not in narrowed and source.name not in pointers
                        and source.name not in lc.prescan.reassigned
                        and source.name in declared):
                    dt = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(declared[source.name])))
                    if isinstance(dt, OwnType):
                        dt = unwrap_readonly(dt.wrapped)
                    container_ok = bool(
                        (is_list(dt) or is_dict(dt) or is_set(dt))
                        and _witness("ret.container_name"))
            elif isinstance(source, (TpyArrayLiteral, TpyDictLiteral,
                                     TpySetLiteral)):
                container_ok = bool(_container_literal_shape_ok(
                    source, lc.prescan.ret_container_storage, analyzer)
                    and _witness("ret.container_literal"))
            elif isinstance(source, TpyCall):
                container_ok = bool(_witness("ret.container_call"))
            elif type(source) in _comprehensions._COMP_KINDS:
                # A comprehension source renders the same position-independent
                # stmt-expr as the decl-init arm, target-typed by the storage
                # container slot; an out-of-slice comp raises inside the
                # lowering and falls the body back.
                comp = _comprehensions._lower_comprehension(
                    source, lc.prescan.ret_container_storage, lc, declared,
                    pointers)
                _witness("ret.container_comp")
                return THIRReturn(value=comp, loc=loc)
            if not container_ok:
                note_detail("return.container_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.value is not None and lc.prescan.ret_callable:
            # A Callable return slot: a bare closure-local name (`return add;`
            # -- the lambda converts to std::function implicitly, the plain
            # return tail) or a direct lambda literal (`return lambda x: ...;`
            # -- the escaping by-value closure, _lower_lambda's captures_by_value
            # arm). Other sources reject.
            if isinstance(stmt.value, TpyLambda):
                value = _lower_lambda(stmt.value, lc, declared)
                _witness("ret.closure_lambda")
                return THIRReturn(value=value, loc=loc)
            if (_func_ref_routable(stmt.value, analyzer)
                    and not (isinstance(stmt.value, TpyName)
                             and stmt.value.name in lc.nested_def_locals)):
                # `return double;` at a Callable slot -- the bare func-ref name
                # converts to std::function implicitly (like the closure name).
                # A nested-def local is also is_function_ref but must take the
                # closure-name arm below (the func-ref intercept in _lower_expr
                # excludes nested_def_locals, so routing it here would fall back).
                _witness("ret.closure_ref")
                return THIRReturn(
                    value=_lower_expr(stmt.value, lc, declared), loc=loc)
            if not (isinstance(stmt.value, TpyName)
                    and stmt.value.name in lc.nested_def_locals):
                note_detail("return.callable_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("ret.closure_name")
            return THIRReturn(
                value=THIRName(result_type=analyzer.get_expr_type(stmt.value),
                               name=stmt.value.name, form=Form.VALUE,
                               loc=getattr(stmt.value, "loc", None)),
                loc=loc)
        # A value-tuple return's literal source renders the spelled brace-init
        # against the return slot (per-element targets ride
        # _lower_tuple_literal); bare value-tuple names ride the generic tail.
        ret_vt = lc.prescan.ret_value_tuple
        if stmt.value is not None and ret_vt is not None:
            source = stmt.value
            tuple_ok = False
            if isinstance(source, TpyTupleLiteral):
                tuple_ok = True
            elif isinstance(source, TpyName):
                tuple_ok = bool(
                    source.name not in narrowed
                    and source.name in declared
                    and _value_tuple(declared[source.name], analyzer)
                    is not None
                    and _witness("ret.tuple_name"))
            elif isinstance(source, TpyCall):
                tuple_ok = bool(_witness("ret.tuple_call"))
            if not tuple_ok:
                note_detail("return.tuple_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if isinstance(source, TpyTupleLiteral):
                try:
                    value = _lower_tuple_literal(source, ret_vt, lc, declared)
                except ThirUnsupported:
                    note_detail("return.tuple_source")
                    raise ThirUnsupported(stmt_reject_reason(stmt)) from None
                _witness("ret.tuple_literal")
                return THIRReturn(value=value, loc=loc)
        if stmt.value is not None and lc.prescan.ret_union is not None:
            source = stmt.value
            while isinstance(source, TpyCoerce):
                source = source.expr
            if (not isinstance(source, TpyStrLiteral)
                    and _resolved_str_value(
                        analyzer.get_expr_type(source), analyzer) is not None):
                note_detail("return.union_view_insert")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        if (stmt.value is not None and lc.prescan.ret_ptr_union is not None
                and not isinstance(stmt.value, TpyNoneLiteral)
                and not _ptr_union_source_ok(
                    stmt.value, declared, analyzer,
                    lc.prescan.ret_ptr_union, allow_field=False)):
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if (stmt.value is not None and lc.prescan.ret_char
                and isinstance(stmt.value, TpyStrLiteral)):
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.value is not None and lc.prescan.ret_own_union is not None:
            # An `Own[A | B]` record-member union return: the routed source
            # is a member-record ctor rvalue, returned bare into the storage
            # variant (its converting ctor absorbs it) via the generic tail.
            if not (_record_rvalue_source_shape(stmt.value, analyzer)
                    and _f1_record(analyzer.get_expr_type(stmt.value),
                                   analyzer)):
                note_detail("return.own_union_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("ret.own_union_ctor")
        if (stmt.value is not None and lc.prescan.ret_str is not None
                and isinstance(stmt.value, TpyFieldAccess)):
            if _str_field_value_read(stmt.value, declared, analyzer):
                _witness("ret.str_field")
            elif _field_receiver_ok(stmt.value, declared, analyzer):
                note_detail("return.str_field_form")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        # `return None` at a union slot -> `std::monostate{}`, target-typed
        # (F4 U1 value / U2 pointer variant -- the monostate member renders
        # the same in both spellings).
        ret_any_union = lc.prescan.ret_union or lc.prescan.ret_ptr_union
        if stmt.value is not None and ret_any_union is not None \
                and isinstance(stmt.value, TpyNoneLiteral):
            return THIRReturn(
                value=THIRLiteral(result_type=ret_any_union, value=None,
                                  form=Form.STORAGE, loc=loc), loc=loc)
        field_prechecked = (
            isinstance(stmt.value, TpyFieldAccess)
            and lc.prescan.ret_str is not None
            and _str_field_value_read(stmt.value, declared, analyzer))
        value = (_flush_witness(
                    "flush.return",
                    _lower_expr(
                        stmt.value, lc, declared,
                        use=_ExprUse(
                            result=_ExprResultUse.STORAGE,
                            allow_temps=True),
                        field_prechecked=field_prechecked,
                        target_type=(lc.prescan.ret_container_storage
                                     if isinstance(
                                         stmt.value,
                                         (TpyArrayLiteral, TpyDictLiteral,
                                          TpySetLiteral))
                                     else None)))
                 if stmt.value else None)
        # A float literal returned from a Float32 function takes the `f`
        # suffix (the AST threads the return type into the render).
        ret_t = lc.func.return_type if isinstance(lc.func.return_type,
                                                  TpyType) else None
        value = _slot_literal_retype(value, ret_t, lc)
        # An expensive-copy value-Optional param (`int | None`) returned at its
        # narrowed last use moves the unwrapped value (`return std::move((*p));`,
        # seed_param_locals' value-optional movable face). `_is_move_source`
        # only fires for the movable-seeded expensive-copy inner; the deref
        # guard scopes it to the narrowed `(*p)` read (an un-narrowed return
        # renders the bare optional into an Optional slot -- a different arm).
        if (isinstance(stmt.value, TpyName) and isinstance(value, THIRName)
                and value.deref
                and _value_opt_scalar_binding(stmt.value.name, lc)
                and _is_move_source(stmt.value, lc)):
            value = THIRMove(result_type=value.result_type, value=value,
                             form=value.form, loc=loc)
        # NB a bytes literal (or bytes value) at a BytesView return arrives
        # wrapped in the cross-type view coercion and is rejected (the
        # deferred coercion cell), so no view retag is needed here.
        value = _wrap_view_owned_return(value, lc, loc)
        return THIRReturn(value=value, loc=loc)
    if isinstance(stmt, TpyIf):
        begin_stmt()
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        hoists = analyzer.if_branch_decls.get(id(stmt), {})
        if hoists and info is not None:
            note_detail("if.narrow_hoist")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Classification registers the hoisted names' read/write model on
        # `lc` and enters them in `declared` BEFORE the condition lowers
        # (the AST's declared_vars registration order); a reject anywhere
        # after a partial registration is fine -- the whole body falls back
        # and the partially-mutated lc is discarded with it.
        hoist_decls, hoist_slots = _lower_if_hoist_predecls(
            stmt, hoists, declared, lc, scope.in_branch)
        if info is not None:
            var, u, _members, folded, _isin = info
            narrow_ok = not (
                (var in lc.narrow.narrowed and not folded)
                or (folded and bool(stmt.else_body))
                or bool(hoists)
                or not _narrow_facts_ok(u, stmt.then_type_facts, var)
                or not _narrow_facts_ok(u, stmt.else_type_facts, var))
            if not narrow_ok:
                note_detail("if.narrow_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return _lower_narrow_if(stmt, info, lc, declared, loc,
                                    loop_depth=scope.loop_depth)
        try:
            condition = _lower_truthy(stmt.condition, lc, declared)
        except ThirUnsupported:
            c = stmt.condition
            if isinstance(c, TpyBinOp):
                lf = _type_family_tag(analyzer.get_expr_type(c.left), analyzer)
                rf = _type_family_tag(analyzer.get_expr_type(c.right), analyzer)
                note_detail(f"if.cond_binop.{c.op}.{lf}_{rf}")
            else:
                _kind_detail("cond.", c)
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Branch-local `declared` copies: eligibility guarantees branches only
        # reassign already-declared locals, but a nested post-if narrowing may
        # retype its subject for the rest of ITS branch -- that must not leak
        # to the sibling or past the if (the AST's per-branch scope restore).
        # The else side mirrors the AST chain collect: a flat elif link lowers
        # BARE (its chain-level post-if belongs to the enclosing statement
        # walk -- a plain-headed chain can still end in a narrowing elif); a
        # genuine else block (or a concrete-else-fact nested if) is a body.
        else_is_nested = False
        inner = _elif_link(stmt)
        if inner is not None and not _facts_have_concrete(stmt.else_type_facts):
            else_stmts: tuple[THIRStmt, ...] = (
                _lower_stmt(inner, lc, dict(declared), in_branch=True,
                            branch_decls_ok=True,
                            loop_depth=scope.loop_depth),)
        else:
            else_is_nested = inner is not None
            else_stmts = _lower_scoped_stmts(stmt.else_body, lc,
                                             dict(declared),
                                             branch_decls_ok=True,
                                             loop_depth=scope.loop_depth)
        return THIRIf(
            condition=condition,
            then_body=_lower_scoped_stmts(
                stmt.then_body, lc, dict(declared),
                branch_decls_ok=True,
                loop_depth=scope.loop_depth),
            else_body=else_stmts,
            else_is_nested=else_is_nested,
            hoist_decls=tuple(hoist_decls),
            hoist_slots=tuple(hoist_slots),
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
        # sema's node facts). `del x` mirrors _gen_del_var_code's skip ladder:
        # a name that is not the sole owner of its value -- or whose
        # destruction is a no-op -- emits nothing; the rest move-sink
        # (`{ auto __del_sink = std::move(name); }`, deref-first for an
        # owning pointer-local).
        if isinstance(stmt, TpyDelVar):
            globals_ = analyzer.function_global_decls.get(id(lc.func), set())
            sinks: list[tuple[str, bool]] = []
            for name in stmt.names:
                # A narrowed / frame-slot binding has no mirrored sink render;
                # an unknown name would sink where the AST's var_types miss
                # skips; an `auto&&` storage-tuple alias would sink THROUGH
                # the alias (gutting the source's storage -- bug-shaped, so
                # rejected rather than mirrored). All stay on the AST path.
                if (name not in declared or name in lc.narrow.narrowed
                        or name in lc.frame_slots
                        or name in lc.storage_tuple_locals):
                    raise ThirUnsupported("stmt.del_var:binding")
                if (_del_var_trivial(declared[name], analyzer)
                        or name in lc.ref_alias_locals
                        or name in lc.prescan.alias_sources
                        or name in lc.prescan.param_names
                        or name in globals_):
                    continue
                if name in lc.pointers:
                    # An alias-born pointer-local may point at another local's
                    # storage -- the AST skips it too.
                    if name in lc.prescan.alias_born:
                        continue
                    sinks.append((escape_cpp_name(name), True))
                    continue
                sinks.append((escape_cpp_name(name), False))
            if sinks:
                _witness("stmt.del_var_sink")
                return THIRDelVar(sinks=tuple(sinks), loc=loc)
        elif isinstance(stmt, TpyGlobal) and not all(
                name in lc.prescan.global_seeded for name in stmt.names):
            raise ThirUnsupported("stmt.global:global.unseeded")
        return THIRNoOpStmt(loc=loc)
    if isinstance(stmt, TpyDelItem):
        # `::tpy::__delitem__(c, k);` -- the AST's no-method-fi fallback in
        # _gen_del_item_code. The index rides gen_index_expr: bare for the
        # fixed-int / str-key shapes, the `.to_fixed_check<int32_t>()` narrow
        # for a runtime-BigInt one (the view-key pin still cannot fire --
        # view-typed keys are not admitted).
        if len(stmt.targets) != 1:
            raise ThirUnsupported("stmt.del_item:multi_target")
        sub = stmt.targets[0]
        if sub.needs_optional_runtime_check or sub.slice_function_info is not None:
            raise ThirUnsupported("stmt.del_item:subscript_shape")
        recv = sub.obj
        if isinstance(recv, TpyName) and (recv.name in lc.pointers
                                          or recv.name in lc.narrow.narrowed):
            raise ThirUnsupported("stmt.del_item:recv_shape")
        recv_t = _subscript_container_recv_type(recv, declared, analyzer)
        # A CONCRETE user record defining `__delitem__` takes the same
        # `::tpy::__delitem__(recv, key)` fallback the container path emits --
        # so the record receiver rides the container arm's key slice.
        user_del = recv_t is not None and _record_has_delitem(recv_t, analyzer)
        # A dict[K, Any] receiver is admitted alongside the scalar families:
        # the del emit never touches the value slot, so the key slice alone
        # decides byte-parity.
        if not (recv_t is not None
                and (user_del
                     or _container_scalar_read(recv_t, analyzer)
                     or (_any_value_dict(recv_t, analyzer)
                         and _witness("delitem.any_value")))
                and _bigint_index_disposition(sub.index, analyzer) != "reject"):
            raise ThirUnsupported("stmt.del_item:recv_or_index")
        if user_del:
            _witness("delitem.user_record")
        return THIRExprStmt(
            expr=THIRCall(
                result_type=VoidType(),
                callee="__delitem__",
                native_name="tpy::__delitem__",
                args=(_lower_expr(
                          sub.obj, lc, declared,
                          field_prechecked=isinstance(
                              sub.obj, TpyFieldAccess)),
                      _narrow_bigint_index(_lower_expr(sub.index, lc, declared),
                                           sub.index, analyzer, loc)),
                loc=loc),
            loc=loc)
    if isinstance(stmt, TpyDelAttr):
        begin_stmt()
        # `del obj.attr` (D16): sema resolved each target to a synthesized
        # `obj.__delattr__("attr")` call; the AST arm renders one method-call
        # statement line per target. The generic method-call arm reproduces
        # the emit (a str-literal arg into a str slot, void result).
        if len(stmt.targets) != 1:
            note_detail("del_attr.multi_target")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        call = stmt.targets[0].dyn_delattr_call
        if call is None:
            note_detail("del_attr.unresolved")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("stmt.del_attr")
        return THIRExprStmt(
            expr=_flush_witness(
                "flush.expr_stmt",
                _lower_expr(call, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.DISCARD,
                                         allow_temps=True))),
            loc=loc)
    if isinstance(stmt, TpyWhile):
        if analyzer.if_branch_decls.get(id(stmt)):
            raise ThirUnsupported("stmt.while")
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        if info is not None:
            # U4 while-isinstance: the loop-entry extraction is the branch
            # alias shape (fresh block, non-persistent); the subject retypes
            # for the body walk and pops at the closing brace.
            var, u, _members, folded, _isin = info
            if (folded or var in lc.narrow.narrowed
                    or not _narrow_facts_ok(
                        u, stmt.then_type_facts, var)):
                raise ThirUnsupported("stmt.while")
            m = _narrow_fact_member(u, stmt.then_type_facts, var)
            condition = _lower_narrow_cond(info, stmt.condition, lc, declared)
            body = _lower_narrowed_branch(stmt.body, m, var, u, lc,
                                          declared, loc,
                                          loop_depth=scope.loop_depth + 1)
        else:
            try:
                condition = _lower_truthy(stmt.condition, lc, declared)
            except ThirUnsupported:
                raise ThirUnsupported("stmt.while") from None
            body = _lower_scoped_stmts(
                stmt.body, lc, dict(declared),
                branch_decls_ok=True,
                loop_depth=scope.loop_depth + 1)
        return THIRWhile(
            condition=condition,
            body=body,
            orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                      "loop.while_else"),
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
        elif isinstance(stmt.message, TpyFieldAccess):
            # Same eligibility as every other _lower_field_source site: a
            # marker-bearing access (property / dyn attr / unproven Optional
            # deref_check / ...) takes its own AST emit path.
            if not _field_receiver_ok(stmt.message, declared, analyzer):
                raise ThirUnsupported("stmt.assert")
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
        elif _reassert_bump_info(
                stmt, declared, lc.narrow.persistent_narrowed,
                analyzer) is not None:
            cond = THIRLiteral(
                result_type=analyzer.get_expr_type(stmt.condition), value=True,
                loc=getattr(stmt.condition, "loc", None))
        else:
            if (stmt.then_type_facts
                    and not _optional_narrow_facts_ok(
                        stmt.then_type_facts, declared, analyzer)):
                raise ThirUnsupported("stmt.assert")
            cond = _lower_truthy(stmt.condition, lc, declared)
        return THIRAssert(condition=cond, message=msg, loc=loc)
    if isinstance(stmt, TpyTupleUnpack):
        source_type = _tuple_unpack_source(
            stmt, analyzer, declared, scope.admission_pointers(),
            lc.narrow.narrowed.keys())
        if (source_type is None
                or len(source_type.element_types) != len(stmt.targets)):
            raise ThirUnsupported("stmt.tuple_unpack")
        target_binds = _standalone_unpack_target_binds(
            stmt, analyzer, declared, lc.narrow.narrowed.keys(),
            blocked=(lc.pointers | lc.rebind_slot_locals
                     | lc.ref_alias_locals | lc.storage_tuple_locals
                     | lc.value_opt_locals | lc.frame_slots))
        if target_binds is None:
            note_detail("tuple_unpack.target_form")
            raise ThirUnsupported("stmt.tuple_unpack")
        # Standalone `a, b = <source>`: the same node the for-loop head builds.
        # A bare name binds by const-ref (the loop-head path too); a call / field
        # rvalue is captured by value via `source_expr` (the AST's non-name
        # `else` bind). The fresh targets enter `declared` so a later same-name
        # re-`decl` (sema keeps such a reassign a TpyVarDecl -- the AST emits an
        # assign because the unpack already put the name in scope) lowers as a
        # reassign, not a second declaration.
        target_cpps: list[str | None] = []
        bind_tags: list[str | None] = []
        for i, name in enumerate(stmt.targets):
            if name is None:
                target_cpps.append(None)
                bind_tags.append(None)
                continue
            tt, bind = target_binds[i]
            assert tt is not None
            if bind == "assign":
                # Reused target: bare assign, no decl -- the declared entry
                # keeps its original type (the AST leaves var_types alone).
                target_cpps.append(None)
                bind_tags.append(bind)
                _witness("stmt.tuple_unpack.assign_target")
                continue
            # A str target's type is still a PendingStrType (params never
            # resolve it in place); resolve it to the concrete view before
            # render, else `render_type` raises. Scalars/records pass through.
            tt, cpp = _unpack_target_decl(tt, analyzer, lc.render_type)
            declared[name] = tt
            target_cpps.append(cpp)
            bind_tags.append(bind)
            if bind == "move":
                _witness("stmt.tuple_unpack.own_target")
            elif bind == "cref":
                _witness("stmt.tuple_unpack.cref_target")
        _witness("stmt.tuple_unpack")
        source_wrap_cpp = None
        if any(b == "ref" for b in bind_tags):
            # Ref targets alias the source tuple elements: the source must be a
            # storage-form pointer-repr tuple NAME, lifted via tuple_to_pointer
            # so `std::get<i>` yields the `T*` each ref target aliases. Only a
            # name source routes here -- its const-ness (const element pointers)
            # is known via `const_storage_tuple_locals`; a field / subscript
            # source's const-ness is not tracked, so it defers.
            const_src = (isinstance(stmt.value, TpyName)
                         and stmt.value.name in lc.const_storage_tuple_locals)
            source_wrap_cpp = _borrow_tuple_wrap_cpp(
                stmt.target_types, analyzer, const_source=const_src)
            if not (isinstance(stmt.value, TpyName)
                    and stmt.value.name in lc.storage_tuple_locals
                    and source_wrap_cpp is not None):
                note_detail("tuple_unpack.ref_source_form")
                raise ThirUnsupported("stmt.tuple_unpack")
            _witness("stmt.tuple_unpack.ref_target")
        if isinstance(stmt.value, TpyName):
            # A spelled imported/native tuple global source renders its fixed
            # qualification (the same THIRName.cpp spelling a scalar global
            # read carries).
            return THIRTupleUnpack(
                source=stmt.value.name, targets=tuple(stmt.targets),
                target_cpps=tuple(target_cpps), binds=tuple(bind_tags),
                source_cpp=lc.prescan.global_cpp.get(stmt.value.name),
                source_bind=(TupleSourceBind.STORAGE_WRAP
                             if source_wrap_cpp is not None
                             else TupleSourceBind.NAME_CREF),
                source_wrap_cpp=source_wrap_cpp,
                loc=loc)
        # A free call hoists its arg temps (temp_args, inert for a field read).
        _witness("stmt.tuple_unpack.rvalue_source")
        return THIRTupleUnpack(
            source="", targets=tuple(stmt.targets),
            target_cpps=tuple(target_cpps), binds=tuple(bind_tags),
            source_bind=TupleSourceBind.RVALUE,
            source_expr=_lower_expr(
                stmt.value, lc, declared,
                use=_ExprUse(
                    result=_ExprResultUse.STORAGE,
                    allow_temps=True,
                    tuple_source=True),
                field_prechecked=isinstance(stmt.value, TpyFieldAccess)),
            loc=loc)
    if isinstance(stmt, TpyForEach):
        route = _select_for_each_route(
            stmt, analyzer, declared, lc.narrow.narrowed.keys(),
            lc.iterator_object_locals)
        it = stmt.iterable
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        et = route.elem_type
        # Branch-first-declared value locals used after the loop (sema's
        # `if_branch_decls`) predecl before the loop like _emit_branch_decls and
        # enter the CALLER's `declared` (function scope), so a nested loop and
        # the post-loop reads see them. Gated to the plain-value predecl family;
        # includes the loop var itself when it is hoisted (used after the loop).
        foreach_hoists = analyzer.if_branch_decls.get(id(stmt), {})
        for _hname, _hraw in foreach_hoists.items():
            if _hname in declared:
                continue
            if (_hname in lc.prescan.native_globals
                    or not _try_hoist_type_ok(unwrap_ref_type(_hraw), analyzer)):
                note_detail("foreach.hoist_type")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        foreach_hoist_decls = tuple(
            _lower_hoist_predecls(foreach_hoists, declared, lc,
                                  "foreach.hoist_decl"))
        body_declared = dict(declared)
        body_declared[stmt.var] = et
        # Mirror of the AST's register_frame_field_shadow: in a resumable
        # leaf the loop var (and tuple-unpack targets) bind C++ locals that
        # shadow same-named frame fields for the body's duration, so the
        # frame-slot `(*name)` deref must not apply inside the body.
        shadow_names = {stmt.var}
        if route.route == "tuple_unpack":
            shadow_names.update(
                n for n in stmt.body[0].targets if n is not None)
        frame_shadowed = shadow_names & lc.frame_slots
        lc.frame_slots -= frame_shadowed
        # The loop var also shadows a same-named POINTER local for the body's
        # duration (the `auto&&` binding is a reference, not a `T*`): the
        # pointer-keyed renders (bare ptr-copy reseat, deref reads) must not
        # fire on the shadowing var.
        ptr_shadowed = shadow_names & lc.pointers
        lc.pointers -= ptr_shadowed
        rebind_shadowed = shadow_names & lc.rebind_slot_locals
        lc.rebind_slot_locals -= rebind_shadowed
        try:
            if route.route == "tuple_unpack":
                # The head TpyTupleUnpack lowers to the dedicated node (a
                # per-target decl list); its targets enter the body scope. The
                # discard slots keep None through both tuples.
                up = stmt.body[0]
                target_cpps: list[str | None] = []
                target_binds: list[str | None] = []
                for i, name in enumerate(up.targets):
                    if name is None:
                        target_cpps.append(None)
                        target_binds.append(None)
                        continue
                    tt = route.unpack_target_types[i]
                    assert tt is not None
                    tt, cpp = _unpack_target_decl(
                        tt, analyzer, lc.render_type)
                    # A target hoisted for post-loop use is predeclared by the
                    # ForEach lowering (already in `declared`); the head assigns
                    # the slot rather than re-declaring (_gen_tuple_unpack's
                    # declared-name tail). A fresh borrow F1-record target
                    # aliases the element ("ref"); a fresh expensive-copy target
                    # binds `const T&` ("cref").
                    if i < len(up.is_ref) and up.is_ref[i]:
                        # A hoisted/reused ref target takes the AST's
                        # pointer-slot assign (`name = &(unwrap_ref(...))`), not
                        # the fresh `auto&&` alias -- not modeled, so defer
                        # (mirrors the standalone bind's is_new exclusion).
                        if name in declared:
                            raise ThirUnsupported("stmt.tuple_unpack")
                        target_cpps.append(cpp)
                        target_binds.append("ref")
                    elif name in declared:
                        target_cpps.append(None)
                        target_binds.append("assign")
                    elif i < len(up.is_const_ref) and up.is_const_ref[i]:
                        target_cpps.append(cpp)
                        target_binds.append("cref")
                    else:
                        target_cpps.append(cpp)
                        target_binds.append("value")
                    body_declared[name] = tt
                head_wrap_cpp = None
                if any(b == "ref" for b in target_binds):
                    # The loop element (`stmt.var`) is a storage-form tuple;
                    # lift it to the borrow pointer tuple so `std::get<i>`
                    # yields the `T*` each ref target aliases.
                    head_wrap_cpp = _borrow_tuple_wrap_cpp(
                        up.target_types, analyzer,
                        const_source=_iteration_yields_const(
                            stmt.iterable, lc, analyzer))
                    if head_wrap_cpp is None:
                        raise ThirUnsupported("stmt.tuple_unpack")
                    _witness("stmt.tuple_unpack.ref_target")
                head = THIRTupleUnpack(
                    source=stmt.var,
                    targets=tuple(up.targets),
                    target_cpps=tuple(target_cpps),
                    binds=tuple(target_binds),
                    source_bind=(TupleSourceBind.STORAGE_WRAP
                                 if head_wrap_cpp is not None
                                 else TupleSourceBind.NAME_CREF),
                    source_wrap_cpp=head_wrap_cpp,
                    loc=getattr(up, "loc", None))
                body = (head,) + _lower_scoped_stmts(
                    stmt.body[1:], lc, body_declared,
                    branch_decls_ok=True,
                    loop_depth=scope.loop_depth + 1)
            else:
                # A value-repr Optional[scalar] loop var reads through the
                # same binding-keyed arms as a value-opt param
                # (deref-on-narrow, the whole-optional None-test/arg
                # renders); registered for the loop body's scope only.
                vopt_loop_var = _foreach_value_opt_elem(et) is not None
                if vopt_loop_var:
                    _witness("foreach.value_opt_elem")
                    lc.value_opt_locals.add(stmt.var)
                # A loop var over a storage-form pointer-repr tuple CONTAINER is
                # itself a storage-form source (a body `a, b = var` unpack lifts
                # it via tuple_to_pointer) -- registered for the body scope,
                # mirror of the AST's storage_form_tuple_locals.add. The
                # `is_native_iterable` gate is load-bearing: a generator /
                # protocol iterator yields BORROW-form tuples (`std::tuple<T*>`),
                # so its loop var stays borrow (`->` element reads) and must NOT
                # be flagged storage.
                _et_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(et)))
                            if et is not None else None)
                _it_type = analyzer.get_expr_type(stmt.iterable)
                storage_tuple_loop_var = (
                    _it_type is not None
                    and is_native_iterable(_it_type, analyzer.registry)
                    and isinstance(_et_bare, TupleType)
                    and _et_bare.has_pointer_repr_element()
                    and stmt.var not in lc.storage_tuple_locals)
                if storage_tuple_loop_var:
                    lc.storage_tuple_locals.add(stmt.var)
                    if _iteration_yields_const(stmt.iterable, lc, analyzer):
                        lc.const_storage_tuple_locals.add(stmt.var)
                # A native auto-consuming loop var is bound `auto&&` into the
                # OwnIter storage and moves at its last use in the body (the
                # AST seeds movable_locals for the loop scope only).
                consuming_loop_var = route.consuming_native_name is not None
                if consuming_loop_var:
                    lc.movable_locals.add(stmt.var)
                try:
                    body = _lower_scoped_stmts(
                        stmt.body, lc, body_declared,
                        branch_decls_ok=True,
                        loop_depth=scope.loop_depth + 1)
                finally:
                    if vopt_loop_var:
                        lc.value_opt_locals.discard(stmt.var)
                    if consuming_loop_var:
                        lc.movable_locals.discard(stmt.var)
                    if storage_tuple_loop_var:
                        lc.storage_tuple_locals.discard(stmt.var)
                        lc.const_storage_tuple_locals.discard(stmt.var)
        finally:
            lc.frame_slots |= frame_shadowed
            lc.pointers |= ptr_shadowed
            lc.rebind_slot_locals |= rebind_shadowed
        if route.route == "range":
            if route.bigint_counter:
                _witness("range.bigint_counter")
            # Literal bounds retype to the elem slot (the AST's gen-args
            # render threads the counter type): a no-op for fixed-int
            # counters (bare token either way), the `::tpy::BigInt(N)`
            # ctor wrap for a BigInt one.
            nargs = len(it.args)
            if nargs == 1:
                start = None
                start_is_literal = True
                stop_arg = it.args[0]
            else:
                start_arg = it.args[0]
                start = _lower_range_arg(start_arg, et, lc, declared)
                start_is_literal = _range_bound_literal_value(start_arg) is not None
                stop_arg = it.args[1]
            step = None
            step_kind = route.step_kind
            if nargs == 3:
                _witness(f"range.step_{step_kind}")
                # The unit-step arms (plus_one / unit_neg) reference no step expr;
                # the other three render it (retype is a no-op for the fixed-int
                # counter this arm requires).
                if step_kind in ("literal_pos", "literal_neg", "variable"):
                    step = _lower_range_arg(it.args[2], et, lc, declared)
            return THIRForRange(
                var=stmt.var,
                elem_type=et,
                stop=_lower_range_arg(stop_arg, et, lc, declared),
                start=start,
                start_is_literal=start_is_literal,
                stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
                body=body,
                step=step,
                step_kind=step_kind,
                hoist_loop_var=stmt.hoist_loop_var,
                hoist_decls=foreach_hoist_decls,
                orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                          "loop.for_else"),
                loc=loc,
            )
        if route.route == "iter_proto" or route.iter_proto:
            if route.iter_proto:
                _witness("foreach.tuple_unpack_iter")
            else:
                _witness("foreach.iter_proto")
            return THIRForIterProto(
                var=stmt.var,
                elem_type=et,
                iterable=_lower_expr(
                    it, lc, declared,
                    # The emit flushes arg temps inside the rvalue brace
                    # scope, right before the `__src` bind -- the AST's
                    # flush point -- so temp-hoisting arg rows are safe here.
                    use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                 allow_temps=True)),
                body=body,
                const_loop_var=stmt.const_loop_var,
                iterable_lvalue=route.iterable_lvalue,
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
                f"::tpy::EnumUtil<{stmt.enum_iterable.to_cpp()}>::members")
            return THIRForEach(
                var=stmt.var,
                elem_type=et,
                iterable=THIRModuleVar(cpp=members_cpp, result_type=et),
                body=body,
                const_loop_var=stmt.const_loop_var,
                iterable_lvalue=True,
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
        iterable = _lower_expr(
            it, lc, declared,
            use=_ExprUse(result=_ExprResultUse.ITERABLE),
            field_prechecked=isinstance(it, TpyFieldAccess),
            # A literal iterable renders target-less (the AST threads no
            # container target into gen_expr_deref here).
            container_threaded=not isinstance(
                it, (TpyArrayLiteral, TpyStrLiteral)))
        if route.consuming_native_name is not None:
            _witness("foreach.consuming_iter")
            iterable = THIRConsumingIter(
                result_type=iterable.result_type, value=iterable,
                native_name=route.consuming_native_name, form=Form.VALUE,
                loc=loc)
        return THIRForEach(
            var=stmt.var,
            elem_type=et,
            iterable=iterable,
            body=body,
            const_loop_var=stmt.const_loop_var,
            iterable_lvalue=route.iterable_lvalue,
            str_literal_iterable=route.str_literal_iterable,
            hoist_loop_var=stmt.hoist_loop_var,
            hoist_decls=foreach_hoist_decls,
            orelse=_lower_loop_orelse(stmt.orelse, lc, declared, scope,
                                      "loop.for_else"),
            loc=loc,
        )
    if isinstance(stmt, TpyExprStmt):
        begin_stmt()
        er_fi = _error_return_stmt_fi(stmt.expr, analyzer)
        if er_fi is not None:
            # A discarded @error_return call: the `__try_tmp_N` block
            # (_gen_error_return_stmt_block). Bare free calls only; the
            # method-call / coerce-wrapped shapes stay AST.
            if not isinstance(stmt.expr, TpyCall):
                note_detail("error_return.stmt_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _reject_nested_error_return_arg(stmt, stmt.expr, analyzer)
            return THIRErrorReturnDiscard(
                call=_lower_expr(stmt.expr, lc, declared,
                                 use=_ExprUse(result=_ExprResultUse.DISCARD,
                                              allow_temps=True),
                                 error_return_raw=True),
                loc=loc)
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
            if e.kwargs:
                # sep=/end= (str literal or resolved str-value name) and file=
                # (an ostream / stream-pointer sink read in value position, so
                # `sys.stderr` derefs), only on a non-empty arg list (an
                # all-suppressed empty print would need gen_print's emit-nothing
                # arm). flush= and non-str-slice sep/end shapes stay AST.
                file_val = e.kwargs.get("file")
                rest = {k: v for k, v in e.kwargs.items() if k != "file"}
                if not e.args or any(k not in ("sep", "end") for k in rest):
                    note_detail("print.kwargs")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                for kw, kv in rest.items():
                    token = _print_kwarg_token(
                        kv, declared, pointers, narrowed, analyzer)
                    if token is None:
                        note_detail("print.kwargs")
                        raise ThirUnsupported(stmt_reject_reason(stmt))
                    kind, value = token
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
                if isinstance(arg, TpyName) and arg.name in narrowed:
                    note_detail("print.narrowed_arg")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if type(arg) in _comprehensions._COMP_KINDS:
                    ok = True
                else:
                    ok = (_print_arg_ok(arg, declared, analyzer)
                          or (isinstance(arg, TpyName)
                              and arg.name not in pointers
                              and _wrap_print_form(
                                  arg, declared, analyzer) is not None
                              and _witness("print.wrap_arg"))
                          or (isinstance(arg, TpyName)
                              and (arg.name in lc.optional_locals
                                   or arg.name in lc.branch_hoisted
                                   or arg.name in lc.rebind_slot_locals)
                              and not isinstance(
                                  unwrap_readonly(unwrap_ref_type(
                                      unwrap_send_sync(
                                          declared.get(arg.name)))),
                                  OptionalType)
                              and _wrap_print_form(arg, declared, analyzer)
                              in (PrintForm.LIST, PrintForm.DICT,
                                  PrintForm.SET, PrintForm.BYTEARRAY)
                              # A branch-hoisted container pointer-local
                              # streams its deref inside the same kind-keyed
                              # wrap (`ListPrinter((*items))`); pointer-repr
                              # Optional bindings stay out (their whole-name
                              # print is a different render).
                              and _witness("print.hoisted_container_arg"))
                          or (isinstance(arg, TpyFieldAccess)
                              and _wrap_print_form(
                                  arg, declared, analyzer) is not None
                              and _witness("print.wrap_field_arg"))
                          or (isinstance(arg, TpySubscript)
                              and _wrap_print_form(
                                  arg, declared, analyzer) is not None
                              and _witness(
                                  "print.container_slice_arg"
                                  if arg.slice_function_info is not None
                                  else "print.tuple_subscript_arg"))
                          or (isinstance(arg, (TpyCall, TpyMethodCall))
                              and _wrap_print_form(
                                  arg, declared, analyzer) is not None
                              and _witness("print.container_call_arg")))
                if not ok:
                    fam = _type_family_tag(
                        analyzer.get_expr_type(arg), analyzer)
                    _kind_detail(f"print.arg.{fam}_", arg)
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                try:
                    lowered_args.append(
                        _lower_print_arg(arg, lc, declared, pointers))
                except ThirUnsupported:
                    fam = _type_family_tag(
                        analyzer.get_expr_type(arg), analyzer)
                    _kind_detail(f"print.arg.{fam}_", arg)
                    raise ThirUnsupported(stmt_reject_reason(stmt)) from None
            return THIRPrint(args=tuple(lowered_args), sep_expr=sep_expr,
                             end_expr=end_expr, sep_value=sep_value,
                             end_value=end_value, sink_expr=sink_expr, loc=loc)
        narrowed = lc.narrow.narrowed.keys()
        if (isinstance(stmt.expr, TpyCall)
                and isinstance(stmt.expr.macro_expansion, TpyMethodCall)):
            rt = analyzer.get_expr_type(stmt.expr)
            if rt is None or isinstance(rt, VoidType):
                # A void statement-position macro expansion (the setattr /
                # delattr builtins): the AST renders the expansion in place;
                # the expression macro arm drops the DISCARD use (a void
                # method call rejects in value position), so the expansion
                # dispatches here in statement position instead.
                _witness("expr_stmt.macro_discard")
                return THIRExprStmt(
                    expr=_flush_witness(
                        "flush.expr_stmt",
                        _lower_expr(
                            stmt.expr.macro_expansion, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.DISCARD,
                                         allow_temps=True))),
                    loc=loc)
        if isinstance(stmt.expr, TpyCall):
            eligible = True
        elif isinstance(stmt.expr, TpyMethodCall):
            eligible = True
        else:
            eligible = _kind_detail("expr_stmt.", stmt.expr)
        if not eligible:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        return THIRExprStmt(expr=_flush_witness(
                                "flush.expr_stmt",
                                _lower_expr(
                                    stmt.expr, lc, declared,
                                    use=_ExprUse(
                                        result=_ExprResultUse.DISCARD,
                                        allow_temps=True))),
                            loc=loc)
    if isinstance(stmt, TpyWith):
        begin_stmt()
        return _lower_with(stmt, lc, declared, scope.admission_pointers(), loc,
                           in_branch=scope.in_branch,
                           loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyTry):
        begin_stmt()
        return _lower_try(stmt, lc, declared, loc,
                          in_branch=scope.in_branch,
                          loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyMatch):
        begin_stmt()
        match_route = _match._select_match_route(
                stmt, analyzer, declared, scope.admission_pointers(),
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                lc.prescan,
                in_branch=scope.in_branch,
                in_loop=scope.loop_depth > 0)
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
    type_to_cpp; names spell RAW like the AST arm) in sema's sorted order,
    and enter the CALLER's `declared` -- hoisted names are function-scope per
    Python scoping, visible in every body and after the statement. Each body
    lowers under its own narrowing-scope snapshot (the AST restores narrowed
    state between sibling blocks); a handler's `as` binding enters its body's
    scope typed like sema binds it, the catch parameter being the binding.
    `finally_terminates` is the AST's last-stmt raise/return fact;
    `body_terminates` is the frame-wrap fact -- the try body plus every
    handler, never the whole statement, so an always-terminating finally
    can't elide its own fall-through copy."""
    if stmt.tier == "return" and len(stmt.handlers) != 1:
        # _gen_try_return dispatches on handlers[0] alone; sema confines the
        # return tier to a single ReturnException handler -- defensive.
        note_detail("try.return_handlers")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    hoists = lc.analyzer.if_branch_decls.get(id(stmt), {})
    for name, raw in hoists.items():
        if name in declared:
            continue
        if (name in lc.prescan.native_globals
                or in_branch or loop_depth > 0
                or not _try_hoist_type_ok(
                    unwrap_ref_type(raw), lc.analyzer)):
            raise ThirUnsupported(stmt_reject_reason(stmt))
    lc.unhandled_hoists.difference_update(hoists)
    for handler in stmt.handlers:
        if (handler.binding
                and _handler_binding_type(handler, lc.analyzer) is None):
            raise ThirUnsupported(stmt_reject_reason(stmt))
    hoist_decls = _lower_hoist_predecls(hoists, declared, lc, "try.hoist_decl")
    body_terminates = try_terminates_ignoring_finally(stmt)
    if stmt.tier == "finally_only":
        _witness("try.finally_only")
    elif stmt.tier == "return":
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
    for h in stmt.handlers:
        cpp = None
        if h.exception_type is not None:
            cpp = error_return_to_cpp(h.exception_type,
                                      lc.analyzer.ctx.module_name,
                                      lc.analyzer.registry)
        h_declared = dict(declared)
        if h.binding:
            bt = _handler_binding_type(h, lc.analyzer)
            assert bt is not None, "ineligible handler reached lowering"
            h_declared[h.binding] = bt
        if stmt.tier == "return" and h.binding:
            # The `__err_opt_N` capture decl spells the QUALIFIED error type
            # (_gen_try_return's qualify_exception_name wrap).
            err_opt_cpp = error_return_to_cpp(
                qualify_exception_name(h.exception_type,
                                       lc.analyzer.registry,
                                       lc.analyzer.ctx.module_name),
                lc.analyzer.ctx.module_name,
                lc.analyzer.registry)
        handlers.append(THIRExceptHandler(
            cpp_type=cpp, binding=h.binding,
            body=_lower_scoped_stmts(
                h.body, lc, h_declared, branch_decls_ok=True,
                loop_depth=loop_depth),
            source_display=(h.exception_type
                            if stmt.tier == "return" else None)))
    last = stmt.finally_body[-1] if stmt.finally_body else None
    finally_terminates = isinstance(last, (TpyRaise, TpyReturn))
    if body_terminates and stmt.finally_body:
        _witness("try.body_terminates")
    if finally_terminates:
        _witness("try.finally_terminates")
    return THIRTry(
        tier=stmt.tier,
        try_body=_lower_scoped_stmts(
            stmt.try_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth),
        handlers=tuple(handlers),
        else_body=_lower_scoped_stmts(
            stmt.else_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth),
        finally_body=_lower_scoped_stmts(
            stmt.finally_body, lc, dict(declared), branch_decls_ok=True,
            loop_depth=loop_depth),
        hoist_decls=tuple(hoist_decls),
        body_terminates=body_terminates,
        finally_terminates=finally_terminates,
        err_opt_cpp=err_opt_cpp,
        loc=loc,
    )

def _raise_inherited_arg_ok(a: TpyExpr, lc: _LowerCtx) -> bool:
    """A `raise E(msg)` where E inherits its ctor (no own `__init__`, so
    `resolved_ctor_init` is None) spells the args position-blind, like the AST's
    `_gen_record_ctor_args` over empty init_params (`gen_call_arg(a, None)`).
    Admit only the plain str/scalar message forms the base Exception ctor takes;
    a richer inherited signature stays on the AST path."""
    if isinstance(a, TpyStrLiteral):
        return True
    t = lc.analyzer.get_expr_type(a)
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return (_eligible_scalar(t)
            or _resolved_str_value(t, lc.analyzer) is not None)

def _lower_raise(stmt: TpyRaise, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc) -> THIRRaise:
    """Lower a bare re-raise or the ctor-form raise (see `THIRRaise`). Ctor
    args lower against the resolved `__init__`'s param slots through the shared
    `_lower_ctor_call_args` dispatch -- the same per-arg machinery the `X(args)`
    construction path uses (raise is a flush statement position, so the temp
    rows are admitted). An inherited ctor (`resolved_ctor_init is None`) spells
    the args position-blind."""
    if stmt.raise_expr is not None:
        # `raise <expr>` (a bound var / call result): the AST emits
        # `<expr>{.__deref__()*N}.__raise__();` (gen_expr_deref of the source +
        # the virtual hop). Conservatively kept OFF the THIR path in a resumable
        # frame / @error_return body: the AST emit is actually identical there
        # (verified), but routing them is a separate widening left to a later
        # increment -- see TODO.md.
        if lc.resumable_leaf_mode or lc.error_return_cpp is not None:
            raise ThirUnsupported("stmt.raise")
        raised = _lower_expr(stmt.raise_expr, lc, declared)
        # A pointer-repr record local (rebind-slot reseat -> `Rec* e`) derefs
        # to a reference before the `.__raise__()` member call (`(*e).__raise__()`,
        # the AST's gen_expr_deref) -- the `_lower_expr` name arm leaves it bare
        # (its normal consumers use `->`), so apply the pointer deref here.
        if isinstance(stmt.raise_expr, TpyName) and stmt.raise_expr.name in lc.pointers:
            assert isinstance(raised, THIRName)
            raised = replace(raised, deref=True)
        _witness("raise.expr")
        return THIRRaise(raise_expr=raised,
                         deref_depth=getattr(stmt, "deref_depth", 0), loc=loc)
    if stmt.exception_type is None:
        _witness("raise.bare")
        return THIRRaise(loc=loc)
    return_tier = is_return_exception(stmt.exception_type)
    if return_tier and lc.error_return_cpp is None:
        # Sema confines a ReturnException raise to @error_return bodies;
        # defensive -- an unrouted context stays AST.
        raise ThirUnsupported("stmt.raise")
    init = stmt.resolved_ctor_init
    lowered_args: list[THIRExpr] = []
    if stmt.args:
        if init is None:
            for arg in stmt.args:
                if not _raise_inherited_arg_ok(arg, lc):
                    raise ThirUnsupported("stmt.raise")
                lowered_args.append(_lower_call_arg(arg, None, lc, declared))
        else:
            if len(stmt.args) > len(init.params):
                raise ThirUnsupported("stmt.raise")
            # `raise X(args)` is a flush statement position (the throw-tier
            # THIRRaise emit flushes arg temps before the throw), so the
            # mutated-ref-slot / Own-copy / union-ctor temp rows are admitted.
            lowered_args = _lower_ctor_call_args(
                stmt.args, init, lc, declared, temp_args=True)
    _witness("raise.ctor")
    cpp = error_return_to_cpp(stmt.exception_type,
                              lc.analyzer.ctx.module_name,
                              lc.analyzer.registry)
    return THIRRaise(
        cpp_type=cpp,
        args=tuple(lowered_args),
        via_virtual=stmt.raise_via_virtual,
        return_tier=return_tier,
        loc=loc,
    )

def _lower_field_write_target(stmt: TpyAssign, lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> THIRExpr:
    """A plain-assign FIELD target renders via gen_expr (not gen_expr_deref):
    a narrowed Optional field assigns into the bare optional storage
    (`this->f = v;`), so the value-position narrowed deref is stripped from
    the top-level node only -- a narrowed RECEIVER inside the chain keeps
    its unwrap (`(*this->opt).x = v;`, the AST's receiver-deref arm)."""
    target = _lower_expr(stmt.target, lc, declared, field_prechecked=True)
    if isinstance(target, THIRFieldAccess) and target.narrowed_deref:
        target = replace(target, narrowed_deref=False)
    return target

def _lower_with(stmt: TpyWith, lc: _LowerCtx, declared: dict[str, TpyType],
                pointers: set[str], loc, *, in_branch: bool = False,
                loop_depth: int = 0) -> THIRWith:
    """Lower a sync `with` -- the item facts mirror `_gen_with`'s header arms
    (see `THIRWith` for the emit shape). Targets are declared at the enclosing
    C++ scope and stay visible after the block (Python scoping), so they extend
    the CALLER's `declared` dict -- unlike branch bodies, which lower over a
    copy. The body lowers under a narrowing-scope snapshot, mirroring the AST's
    `narrowed_vars` / `declared_persistent_aliases` restore around the try
    body. `body_terminates` calls the same `stmts_terminate` the AST reads, so
    the per-layer normal-exit elision folds identically at emit. Sema's hoist
    (`if_branch_decls`) admits and renders like `_lower_try`'s: the
    plain-value predecl family, at statement level only, entering the
    CALLER's `declared` (function-scope names; body writes lower as
    reassigns against the predecl slot)."""
    if stmt.is_async:
        raise ThirUnsupported(stmt_reject_reason(stmt))
    hoists = lc.analyzer.if_branch_decls.get(id(stmt), {})
    for name, raw in hoists.items():
        if name in declared:
            continue
        if (name in lc.prescan.native_globals
                or in_branch or loop_depth > 0
                or not _try_hoist_type_ok(
                    unwrap_ref_type(raw), lc.analyzer)):
            note_detail("with.hoist")
            raise ThirUnsupported(stmt_reject_reason(stmt))
    lc.unhandled_hoists.difference_update(hoists)
    items: list[THIRWithItem] = []
    for item in stmt.items:
        ctx = item.context_expr
        arm_et = _with_target_arm(item, declared, lc.prescan, lc.analyzer,
                                  pointers, lc.rebind_slot_locals)
        if arm_et is None:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        arm, et = arm_et
        if arm is not WithTargetArm.NONE and in_branch:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if item.manager_borrowed:
            manager_ok = (
                isinstance(ctx, TpyName) and ctx.name in declared
                and ctx.name not in lc.narrow.narrowed
                and _f1_record(declared[ctx.name], lc.analyzer))
        else:
            manager_ok = (
                isinstance(ctx, TpyCall)
                and _f1_record(lc.analyzer.get_expr_type(ctx), lc.analyzer)
                and (_ctor_shape_ok(ctx, lc.analyzer)
                     or _record_rvalue_source_shape(ctx, lc.analyzer)
                     or _native_ctx_manager_ok(ctx, lc.analyzer)))
        if not manager_ok:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        deref = (item.manager_borrowed and isinstance(ctx, TpyName)
                 and ctx.name in lc.pointers)
        _witness("with.manager_borrowed" if item.manager_borrowed
                 else "with.manager_owned")
        if deref:
            _witness("with.manager_deref")
        _witness({WithTargetArm.NONE: "with.no_target",
                  WithTargetArm.VALUE: "with.as_value",
                  WithTargetArm.REF: "with.as_ref",
                  WithTargetArm.PTR_DECL: "with.ptr_target",
                  WithTargetArm.ASSIGN_PTR: "with.ptr_target_reuse"}[arm])
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
        items.append(THIRWithItem(
            ctx_expr=_lower_expr(ctx, lc, declared,
                                 use=_ExprUse(ctx_manager=True)),
            manager_borrowed=item.manager_borrowed,
            deref_manager=deref,
            target=item.target,
            target_arm=arm,
            can_suppress=item.exit_can_suppress,
            takes_exc_val=item.exit_takes_exc_val,
            target_cpp=(lc.render_type(et)
                        if arm is WithTargetArm.PTR_DECL else None),
        ))
        if item.target is not None:
            declared[item.target] = et
            if arm is WithTargetArm.PTR_DECL:
                lc.pointers.add(item.target)
                pointers.add(item.target)
    if len(stmt.items) > 1:
        _witness("with.multi")
    hoist_decls = _lower_hoist_predecls(hoists, declared, lc,
                                        "with.hoist_decl")
    return THIRWith(
        items=tuple(items),
        body=_lower_scoped_stmts(
            stmt.body, lc, dict(declared), loop_depth=loop_depth),
        body_terminates=stmts_terminate(stmt.body),
        hoist_decls=tuple(hoist_decls),
        loc=loc,
    )

def _lower_print_arg(a: TpyExpr, lc: _LowerCtx,
                     declared: dict[str, TpyType],
                     pointers: AbstractSet[str]) -> THIRPrintArg:
    """Lower one print arg + tag its `std::cout <<` wrapper form. A str literal
    lowers to a THIRStrLiteral (RAW: emitted via cpp_string_literal_expr); an
    eligible scalar lowers normally with its type-derived form."""
    if type(a) in _comprehensions._COMP_KINDS:
        # C3 comp print arg: the stmt-expr render inside its container
        # printer (List/Set/DictPrinter -- gen_print's container arms).
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
    if isinstance(a, TpyBytesLiteral):
        # gen_print threads no target, so the literal renders OWNED
        # (bytes_literal_owned / empty vector) inside the BytesPrinter wrap.
        return THIRPrintArg(
            THIRBytesLiteral(value=a.value, result_type=lc.analyzer.get_expr_type(a)),
            PrintForm.BYTES)
    opt = _print_optval_opt(a, lc.analyzer, declared)
    if opt is not None:
        # A value-repr Optional[scalar/str] read -> the bare optional
        # (`p` / `this->fi`, no deref) inside a `::tpy::print_optional_val(...)`
        # wrap (gen_print's value-repr Optional arm). A NARROWED name's
        # deref-on-narrow is stripped -- gen_print wraps the WHOLE optional
        # regardless of narrowing (params and locals alike, probe-verified).
        _witness("print.optval")
        form, inner_cpp = _print_optval_form(opt)
        lowered = _lower_expr(
            a, lc, declared, allow_whole_optional=True,
            field_prechecked=isinstance(a, TpyFieldAccess))
        if isinstance(lowered, THIRName) and lowered.deref:
            lowered = replace(lowered, deref=False)
        # A NARROWED Optional field likewise wraps the WHOLE optional storage
        # (gen_print keys on the declared field type), so the value-position
        # narrowed deref is stripped.
        if isinstance(lowered, THIRFieldAccess) and lowered.narrowed_deref:
            lowered = replace(lowered, narrowed_deref=False)
        return THIRPrintArg(lowered, form, inner_cpp)
    # A container / value-tuple / F1-record NAME: the kind-keyed printer
    # wrap (or the record's raw operator<<) around the bare name -- the
    # same routing fact lowering consumed (`_wrap_print_form`).
    wrap = _wrap_print_form(a, declared, lc.analyzer)
    if wrap is not None and isinstance(a, (TpyCall, TpyMethodCall)):
        # A container-returning CALL wraps the inline call render; STORAGE
        # use admits the container-return call shapes (the storage-sink
        # gate), and a print is a flush position, so arg temps may hoist.
        return THIRPrintArg(
            _lower_expr(a, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE,
                                     allow_temps=True)),
            wrap)
    if wrap is not None:
        return THIRPrintArg(
            _lower_expr(
                a, lc, declared,
                field_prechecked=isinstance(a, TpyFieldAccess)),
            wrap)
    # resolve_int_literals: an IntLiteral-typed arg (a literal-seeded container's
    # loop var / pop result) must derive its stream form from the resolved type.
    arg_type = resolve_int_literals(
        unwrap_readonly(lc.analyzer.get_expr_type(a)),
        lc.analyzer.ctx.default_int_for_literal)
    # A print statement is a flush position on the AST path (arg temps hoist
    # before the statement), so temp-hoisting arg rows admit here. A print
    # sink is a raw `<<` position, so an owned-str FIELD read streams bare --
    # the same admission the f-string arg site grants (field_owned_str_ok).
    return THIRPrintArg(
        _lower_expr(a, lc, declared, use=_ExprUse(allow_temps=True),
                    field_owned_str_ok=isinstance(a, TpyFieldAccess)),
        _print_arg_form(arg_type))
