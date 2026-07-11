"""Sequential statement lowering with per-shape validation for declarations,
branches, loops, with, try, raise, and narrowing statements.
"""

from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from ...parse.nodes import (
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBreak,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyContinue,
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
    TpyMatch,
    TpyMethodCall,
    TpyName,
    TpyNestedDef,
    TpyNoneLiteral,
    TpyPassStmt,
    TpyRaise,
    TpyReturn,
    TpySetLiteral,
    TpyStmt,
    TpyStrLiteral,
    TpySubscript,
    TpyTry,
    TpyTupleLiteral,
    TpyTupleUnpack,
    TpyUnaryOp,
    TpyVarDecl,
    TpyWhile,
    TpyWith,
    VarLinkage,
    is_docstring,
)
from ...typesys import (
    NominalType,
    OptionalType,
    OwnType,
    ReadonlyType,
    TpyType,
    TupleType,
    UnionType,
    VoidType,
    error_return_to_cpp,
    is_return_exception,
    resolve_int_literals,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    is_array,
    is_big_int_type,
    is_bytes_type,
    is_dict,
    is_fixed_int_type,
    is_list,
    is_set,
    is_str_type,
)
from ...modules.type_resolution import is_native_iterable
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    is_storage_tuple_alias_decl,
)
from ...liveness import stmts_terminate
from ..faces import witness as _witness
from ..fallback import (
    ThirUnsupported,
    begin_stmt,
    expr_kind_tag,
    note,
    note_detail,
    stmt_reject_reason,
)
from ..nodes import (
    Form,
    PrintForm,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRContinue,
    THIRExpr,
    THIRExprStmt,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRIf,
    THIRIsinstance,
    THIRLiteral,
    THIRMove,
    THIRName,
    THIRNarrowAlias,
    THIRNoOpStmt,
    THIROptionalPtrArg,
    THIROptViewArg,
    THIRPrint,
    THIRExceptHandler,
    THIRPrintArg,
    THIRRaise,
    THIRReturn,
    THIRSelf,
    THIRSetItem,
    THIRStmt,
    THIRStrAppend,
    THIRStrLiteral,
    THIRTry,
    THIRTupleUnpack,
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
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _f1_is_const,
    _f1_record,
    _f1_tuple,
    _f1_tuple_field_write_ok,
    _f2_reseat_ok,
    _f2b_optional_field_write_ok,
    _facts_have_concrete,
    _field_decl_type,
    _field_receiver_ok,
    _for_each_elem_binding_ok,
    _is_borrow_form_name,
    _is_borrow_ptr_local,
    _is_string_owned,
    _narrow_bigint_index,
    _narrow_fact_member,
    _narrow_facts_ok,
    _nonvalue_container_ret,
    _optional_narrow_facts_ok,
    _optional_ptr_borrow_name,
    _owned_str_append_target,
    _param_is_const,
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
    _slice_object_type,
    _storage_call_container,
    _storage_call_ret,
    _str_field_value_read,
    _str_self_append_rhs,
    _type_family_tag,
    _peel_coerce,
    _subscript_container_recv_type,
    _unwrap_lit_coerce,
    _value_tuple,
    _var_decl_type,
)
from .context import (
    _ExprResultUse,
    _ExprUse,
    _LowerCtx,
    _LowerScope,
    _Prescan,
)
from .expr_gates import (
    _assert_narrow_info,
    _borrow_local_binding,
    _bytes_aug_concat_ok,
    _call_eligible,
    _condition_eligible,
    _container_aug_setitem_ok,
    _container_field_write_ok,
    _container_literal_decl_ok,
    _container_literal_ok,
    _container_setitem_ok,
    _is_builtin_print,
    _is_len_call,
    _is_record_rvalue_method_source,
    _is_record_rvalue_source,
    _method_call_eligible,
    _narrow_cond_info,
    _optional_record_field_inner,
    _optional_record_field_write_ok,
    _print_arg_form,
    _print_arg_ok,
    _wrap_print_form,
    _print_optval_form,
    _print_optval_opt,
    _ptr_union_field_write_ok,
    _ptr_union_source_ok,
    _record_ctor_call_eligible,
    _record_field_write_ok,
    _scalar_aug_assign_ok,
    _scalar_field_write_ok,
    _str_aug_append_ok,
    _str_field_write_ok,
    _str_list_method_iterable_ok,
    _tuple_literal_ok,
)
from .expressions import (
    _flush_witness,
    _is_move_source,
    _lower_call_arg,
    _lower_char_targeted,
    _lower_expr,
    _lower_field_source,
    _lower_truthy,
    _lower_tuple_literal,
    _rb_operand_slots,
    _retag_bytes_literal_view,
    _slot_literal_retype,
    _value_opt_scalar_param,
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

def _container_scalar_tuple_iter(t: TpyType | None, analyzer) -> bool:
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
                    for et in elem.element_types))

def _range_bound_literal_value(arg: TpyExpr) -> int | None:
    # The AST's inline-vs-hoist decision for a range bound (_is_literal_range_arg):
    # an inlinable bare int literal (possibly behind the int_literal coerce) vs a
    # name/expr hoisted to a temp. Only the bare-literal subset the slice admits is
    # mirrored, so it agrees with _extract_int_literal regardless of that helper's
    # evolution. The int32 bound keeps the value a bare token (no `ull`/cast).
    arg = _unwrap_lit_coerce(arg)
    if isinstance(arg, TpyIntLiteral) and -2**31 <= arg.value <= 2**31 - 1:
        return arg.value
    # A negated literal (`range(-3, 3)`) is inlined by the AST too
    # (_extract_int_literal's negated arm).
    if (isinstance(arg, TpyUnaryOp) and arg.op == "-"
            and isinstance(arg.operand, TpyIntLiteral)
            and -2**31 <= -arg.operand.value <= 2**31 - 1):
        return -arg.operand.value
    return None

def _range_bound_eligible(arg: TpyExpr, declared: dict[str, TpyType],
                          analyzer) -> bool:
    # Tight slice: an inlinable int literal, or a bare name of an
    # already-declared fixed-int local/param (hoisted to a __start/__stop temp).
    # Binop/call bounds are deferred -- they need the byte-identical net to
    # confirm gen_range_args' _gen_expr_deref(arg, ptype) matches _emit_expr.
    if isinstance(arg, TpyName):
        # `declared` holds bool/float locals too, so the bound's resolved type
        # must be checked int (it renders into a `cpp_elem` temp -- a BigInt
        # bound lands in a `::tpy::BigInt` temp, a fixed-int one converts
        # implicitly).
        dt = declared.get(arg.name)
        return is_fixed_int_type(dt) or _runtime_bigint(dt, analyzer)
    # `range(len(c))` -- the Int32-valued len builtin, hoisted into a `__stop_N` temp
    # like any non-literal bound; unblocks the bounds-safe container-subscript branch.
    if _is_len_call(arg, declared, analyzer):
        return True
    # An arithmetic / call bound (`range(len(xs) - 1)`, `range(n - 1)`,
    # `range(a + b)`, `range(abs(n))`), hoisted into a `__start/__stop_N`
    # temp exactly like a name/len bound. The bound must itself route as a
    # value expr and resolve to the counter's int family (fixed-int or runtime
    # BigInt), so the temp's `cpp_elem` slot absorbs it. A binop/call render is
    # target-independent -- the resolved binop threads its own operand slots,
    # a call its own param slots -- so `_lower_expr` reproduces the AST's
    # `gen_range_args` -> `_gen_expr_deref(arg, ptype)` byte for byte, and the
    # outer `_slot_literal_retype` is a no-op on a non-literal head.
    if isinstance(arg, (TpyBinOp, TpyCall)):
        rt = analyzer.get_expr_type(arg)
        if not (is_fixed_int_type(rt) or _runtime_bigint(rt, analyzer)):
            return False
        return True
    return _range_bound_literal_value(arg) is not None

def _range_step_kind(step_arg: TpyExpr, declared: dict[str, TpyType]) -> str | None:
    # Classify a 3-arg range's step into the AST's _gen_range_counter_loop arm,
    # or None to defer. Conservative slice, mirroring the fixed-int subset the
    # emitter reproduces byte-for-byte:
    #   * a bare (possibly negated) int literal -> plus_one (+1) / unit_neg (-1) /
    #     literal_pos / literal_neg; a zero step is rejected (the AST falls to the
    #     Range ctor there, not this counter loop).
    #   * a bare fixed-int name -> variable (captured into `__step_N`).
    # A ctor-literal step (`Int32(2)`) is deferred: the AST folds it via
    # _extract_int_literal (which _range_bound_literal_value does not), so
    # admitting it as a variable step would diverge. Binop/call steps are
    # deferred for the same net-confidence reason as the bound slice.
    lit = _range_bound_literal_value(step_arg)
    if lit is not None:
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

def _for_loop_shape_ok(stmt: TpyForEach, analyzer, declared: dict[str, TpyType]) -> bool:
    """The for-loop shape guards shared by the range-for and container-for cells: no
    async / tuple-unpack / for-else / enum / consuming / hoisted-loop-var; no branch-decl
    pre-declaration (`if_branch_decls`, set by `_promote_pending_loop_var` when a
    loop/body var is hoisted for post-loop use -- the emitter has no `_emit_branch_decls`
    equivalent); and a loop-scoped var (not shadowing an outer local, whose `was_declared`
    handling the emitter does not reproduce)."""
    if (stmt.is_async or stmt.is_tuple_unpack or stmt.orelse
            or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return False
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    return stmt.var not in declared

@dataclass(frozen=True)
class _ForEachPlan:
    route: str
    elem_type: TpyType
    iterable_lvalue: bool = False
    step_kind: str = "plus_one"
    unpack_target_types: tuple['TpyType | None', ...] = ()
    str_list_method: bool = False
    container_field: bool = False
    value_tuple_elem: bool = False
    bigint_counter: bool = False


def _for_range_plan(stmt: TpyForEach, analyzer,
                    declared: dict[str, TpyType]) -> '_ForEachPlan | None':
    # `for v in range(stop | start, stop [, step])` over a fixed-int or runtime-
    # BigInt counter (loop var not used after the loop). The shared shape guards
    # exclude the other richer for-shapes. A 3-arg stepped range is admitted only
    # for a fixed-int counter with a slice-eligible step (see _range_step_kind);
    # the BigInt-counter stepped emit (a `__step_N` temp even for a literal, no
    # overflow check) stays on the AST path.
    it = stmt.iterable
    if not _is_range_call(it) or not _for_loop_shape_ok(stmt, analyzer, declared):
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
    if nargs >= 2 and not _range_bound_eligible(it.args[0], declared, analyzer):
        return None
    stop_arg = it.args[0] if nargs == 1 else it.args[1]
    if not _range_bound_eligible(stop_arg, declared, analyzer):
        return None
    lowered_et = resolve_int_literals(
        et, analyzer.ctx.default_int_for_literal)
    return _ForEachPlan(
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

def _for_each_container_plan(
        stmt: TpyForEach, analyzer,
        declared: dict[str, TpyType]) -> '_ForEachPlan | None':
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
    if not _for_loop_shape_ok(stmt, analyzer, declared):
        return None
    it = stmt.iterable
    # A plain in-scope container name, or a str/bytes-family field off an
    # F1-record receiver (`for c in h.name:`) -- both C++ lvalues
    # (is_lvalue_iterable: a name, or a field access over one), taking the
    # `auto& __obj_N =` capture -- or an eligible str- or container-returning
    # call (`for c in full(s):` / `for x in make_list():`). The call's capture
    # verdict rides `iterable_lvalue` (`_call_iterable_lvalue`: a str return
    # and an `Own[...]` container return are rvalues, the owning `auto
    # __obj_N =` capture; a borrow container return is an lvalue). Bytes-
    # returning calls and other non-name iterables (subscript) ride a later
    # cell.
    if _is_range_call(it):
        return None
    iterable_lvalue = True
    str_list_method = False
    container_field = False
    if isinstance(it, TpyCall):
        if not _call_eligible(it, declared, analyzer, container_ret_ok=True):
            return None
        ret = analyzer.get_expr_type(it)
        it_type = _resolved_str_value(ret, analyzer)
        if it_type is None:
            # container_ret_ok widened _call_eligible past str returns; the
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
        # _method_call_eligible). A param binding is Ref/readonly-wrapped -- unwrap
        # like _container_scalar_read does internally.
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[it.name])))
        # A str/bytes local's binding is a Pending view type the registry lookup
        # can't see through; resolve to the concrete view/owned nominal first.
        it_view = _resolved_viewfam_value(it_type, analyzer)
        if it_view is not None:
            it_type = it_view
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
    else:
        note_detail("foreach.iter_shape")
        return None
    if not is_native_iterable(it_type, analyzer.registry):
        note_detail("foreach.iter_family")
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
        note_detail("foreach.elem_family")
        return None
    return _ForEachPlan(
        route="container", elem_type=et,
        iterable_lvalue=iterable_lvalue,
        str_list_method=str_list_method,
        container_field=container_field,
        value_tuple_elem=_value_tuple(et, analyzer) is not None)

def _for_tuple_unpack_plan(
        stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str]) -> '_ForEachPlan | None':
    """`for a, b in <iterable>:` -- the parser desugars to a ForEach over a
    synthetic `__for_tup_N` var whose body leads with a TpyTupleUnpack from
    it. Slice: an lvalue `list[tuple[scalar-or-str]]`-family name or a
    `d.items()` dict-view call as the iterable; all-new plain value scalar-or-str
    targets (no ref/owned/const-ref elements, no discard restrictions -- `_`
    slots skip). Record elements take the borrow target branch of
    _gen_tuple_unpack -- a deferred row."""
    if not stmt.is_tuple_unpack:
        return None
    if (stmt.is_async or stmt.orelse or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return None
    if analyzer.if_branch_decls.get(id(stmt)):
        return None
    if stmt.var in declared:
        return None
    if not stmt.body or not isinstance(stmt.body[0], TpyTupleUnpack):
        return None
    up = stmt.body[0]
    if not (isinstance(up.value, TpyName) and up.value.name == stmt.var):
        return None
    target_types = _tuple_unpack_targets(up, analyzer, declared, narrowed)
    if target_types is None:
        return None
    it = stmt.iterable
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
        if not _container_scalar_tuple_iter(it_type, analyzer):
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
    return _ForEachPlan(
        route="tuple_unpack", elem_type=et,
        iterable_lvalue=iterable_lvalue,
        unpack_target_types=tuple(target_types),
        value_tuple_elem=_value_tuple(et, analyzer) is not None)


def _classify_for_each(
        stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
        narrowed: AbstractSet[str]) -> '_ForEachPlan | None':
    if _is_range_call(stmt.iterable):
        plan = _for_range_plan(stmt, analyzer, declared)
    elif stmt.is_tuple_unpack:
        plan = _for_tuple_unpack_plan(stmt, analyzer, declared, narrowed)
    else:
        plan = _for_each_container_plan(stmt, analyzer, declared)
    if plan is None:
        _kind_detail("foreach.iter_", stmt.iterable)
    return plan

def _tuple_unpack_targets(stmt: TpyTupleUnpack, analyzer,
                          declared: dict[str, TpyType],
                          narrowed: AbstractSet[str]
                          ) -> 'list[TpyType | None] | None':
    """The all-new plain value-scalar-or-str target slice of
    `_gen_tuple_unpack`'s `const auto& __tup_N = <name>;` arm: no
    ref/owned/const-ref elements, every target a fresh scalar or str local (a
    discard `_` slot skips). Returns the per-target unwrapped types (None at a
    discard slot), or None when a target takes another _gen_tuple_unpack branch
    (borrow/record/reused) -- deferred rows. The source-form check (a value
    scalar-or-str tuple name) is the caller's."""
    if any(stmt.is_ref) or any(stmt.is_owned) or any(stmt.is_const_ref):
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
        if not _scalar_or_str_unpack_elem(tt, analyzer):
            return None
        types.append(tt)
    return types

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
    `_call_eligible` / `_method_call_eligible`, `_field_receiver_ok`), so the
    source expr lowers the same; only the source bind differs (`auto` rvalue
    capture vs the name's const-ref). Every element must be a scalar or a str
    (`_scalar_or_str_unpack_elem`): a str element's view-form target aliases the
    source tuple's element for the tuple's scope, kept aligned with
    `_tuple_unpack_targets`' matching family. Swap / literal-parallel / nested /
    starred / record-element / Optional-element / reused-target forms take other
    arms."""
    v = stmt.value
    if isinstance(v, TpyName):
        if v.name not in declared or v.name in pointers or v.name in narrowed:
            _kind_detail("tuple_unpack.src_", v)
            return None
        src_raw: 'TpyType | None' = declared[v.name]
    elif (isinstance(v, TpyCall)
          and _storage_call_ret(analyzer.get_expr_type(v), analyzer) is not None
          and _call_eligible(v, declared, analyzer, temps_ok=True,
                             storage_ret_ok=True, narrowed=narrowed)):
        src_raw = analyzer.get_expr_type(v)
    elif (isinstance(v, TpyMethodCall)
          and _storage_call_ret(analyzer.get_expr_type(v), analyzer) is not None
          and _method_call_eligible(v, declared, analyzer, temps_ok=True,
                                    storage_ret_ok=True,
                                    narrowed=narrowed)):
        # The method sibling of the free-call arm: `a, b = obj.pair()` binds
        # the same `auto __tup_N = <rvalue call>;` capture.
        src_raw = analyzer.get_expr_type(v)
    elif (isinstance(v, TpyFieldAccess)
          and _field_receiver_ok(v, declared, analyzer)):
        src_raw = analyzer.get_expr_type(v)
    else:
        # The source's node kind (a call that failed its gates keeps any
        # finer detail those noted -- set-if-empty).
        _kind_detail("tuple_unpack.src_", v)
        return None
    src_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(src_raw)))
    if not (isinstance(src_t, TupleType)
            and all(_scalar_or_str_unpack_elem(e, analyzer)
                    for e in src_t.element_types)):
        note_detail("tuple_unpack.source_family")
        return None
    return src_t

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
    `x = &(__enter__())` family: gate-rejected, never mirrored. Bodies that
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
    family a first var-decl admits. Readonly/Optional wrappers reject: the AST
    routes those through the const/pointer arms (or, for a value-repr
    Optional, declares a slot whose later assigns are ineligible anyway)."""
    if isinstance(vtype, (ReadonlyType, OptionalType)):
        return False
    return (_eligible_scalar(vtype) or _eligible_char(vtype)
            or _eligible_enum(vtype, analyzer) is not None
            or _resolved_str_value(vtype, analyzer) is not None
            or _resolved_bytes_value(vtype, analyzer) is not None
            or _is_string_owned(vtype)
            or _eligible_value_union(vtype) is not None
            or _slice_object_type(vtype))

def _handler_binding_type(h: TpyExceptHandler, analyzer) -> 'NominalType | None':
    """The `as`-binding's type, exactly as sema binds it (`NominalType` over
    the registry record; `h.exception_type` is sema-qualified in place)."""
    rec = analyzer.registry.find_record_by_qname(h.exception_type)
    if rec is None:
        return None
    return NominalType(rec.name, _module_qname=rec.qualified_name())

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
            and (_is_record_rvalue_source(stmt.init, declared, analyzer,
                                          temps_ok=True, narrowed=narrowed)
                 or _is_record_rvalue_method_source(stmt.init, declared,
                                                    analyzer, temps_ok=True)))

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
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype,
            init=_lower_expr(
                stmt.init, lc, declared,
                use=_ExprUse(result=_ExprResultUse.BORROW_BIND)),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if isinstance(stmt.init, TpyCall):
        # REF_ALIAS from a borrow-record-returning call: the `T&` binds the
        # callee's returned reference directly (`Pair& p = shared(x);`), so
        # the init is the plain value-form call -- no conversion node.
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
        # a field source keeps the dedicated borrow-source build.
        src = (_lower_expr(stmt.init, lc, declared, subscript_prechecked=True)
               if isinstance(stmt.init, TpySubscript)
               else _lower_field_source(stmt.init, lc, declared))
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=src,
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
    resumable frame-field rename arm is unreachable -- no resumable bodies
    route.)"""
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

def _lower_scoped_stmts(body, lc: _LowerCtx,
                        declared: dict[str, TpyType], *,
                        branch_decls_ok: bool = False,
                        loop_depth: int = 0) -> tuple[THIRStmt, ...]:
    """`_lower_stmts` under a narrowing-scope snapshot: a branch or loop body.
    A post-if / assert narrowing made inside pops at the closing brace (the
    AST's `narrowed_vars` / `declared_persistent_aliases` body restores)."""
    saved = lc.narrow.snapshot()
    try:
        return _lower_stmts(body, lc, declared, in_branch=True,
                            branch_decls_ok=branch_decls_ok,
                            loop_depth=loop_depth)
    finally:
        lc.narrow = saved

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
    return tuple(out)

def _narrow_subject_const(var: str, lc: _LowerCtx) -> bool:
    """Whether a pointer-variant narrowing SUBJECT spells const pointees: a
    const local (the U2 field-lift chain), or a param the function's
    deep-const verdict (`deep_const_borrow_params` -- the inferred
    discriminant-only-use fact that also deep-consts the signature's variant
    spelling) applies to. Shared by the isinstance condition and every
    narrowed-member `std::get` template arg, so the two renders cannot
    drift."""
    if var in lc.const_locals:
        return True
    return _param_is_deep_const(var, lc.func, lc.analyzer, lc.record_name)

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

def _narrow_member_cpp(var: str, member: TpyType, u: UnionType,
                       lc: _LowerCtx) -> tuple[str, bool]:
    """The final `std::get` template arg for a narrowed member read, plus the
    pointer-variant verdict: the ptr `*` suffix and the const-pointee prefix
    (`_narrow_subject_const`) applied at lowering. Shared by the extraction
    alias and the compound-condition inline read."""
    member_cpp = lc.render_type(member)
    is_ptr = is_ptr_variant_union(u)
    if is_ptr:
        const = "const " if _narrow_subject_const(var, lc) else ""
        member_cpp = f"{const}{member_cpp}*"
    return member_cpp, is_ptr

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
    return _lower_expr(
        cond, lc, active_declared,
        use=_ExprUse(result=_ExprResultUse.CONDITION))

def _lower_narrow_cond(cinfo, condition: TpyExpr, lc: _LowerCtx,
                       declared: dict[str, TpyType]) -> THIRExpr:
    """Lower a narrowing condition from `_narrow_cond_info`'s 5-tuple: the
    simple form is the bare isinstance render; the compound form walks the
    `&&` tree (under an `lc.inline_narrowed` snapshot -- the inline-read
    fact must not leak past the condition; the branch/loop alias re-narrows
    for the body)."""
    var, u, members, folded, isin = cinfo
    if isin is None:
        return _lower_isinstance_cond((var, u, members, folded), condition, lc)
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

def _lower_stmt_dispatch(stmt: TpyStmt, scope: _LowerScope) -> THIRStmt:
    lc = scope.lc
    declared = scope.declared
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    # Trivia (docstring before the TpyExprStmt arm): loc drives the source
    # comment -- `pass` keeps it, a docstring drops it (the AST emits neither).
    if is_docstring(stmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt()
    if isinstance(stmt, TpyPassStmt):
        _witness("stmt.trivia")
        return THIRNoOpStmt(loc=loc)
    if lc.resumable_leaf_mode:
        if isinstance(stmt, TpyReturn):
            raise ThirUnsupported("res.leaf_return")
        if scope.in_branch and isinstance(stmt, TpyVarDecl):
            raise ThirUnsupported("res.leaf_field_write")
        if isinstance(stmt, TpyTupleUnpack):
            raise ThirUnsupported("res.unpack")
        if isinstance(stmt, TpyTry):
            raise ThirUnsupported("res.leaf_try")
        if isinstance(stmt, TpyWith):
            raise ThirUnsupported("res.leaf_with")
        if isinstance(stmt, TpyMatch):
            raise ThirUnsupported("res.leaf_match")
        if isinstance(stmt, TpyNestedDef):
            raise ThirUnsupported("res.nested_def")
    if isinstance(stmt, TpyVarDecl):
        begin_stmt()
        if stmt.linkage != VarLinkage.DEFAULT:
            note_detail("decl.linkage")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if stmt.init is None:
            note_detail("decl.no_init")
            raise ThirUnsupported(stmt_reject_reason(stmt))
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
        # First decl of a non-value borrow local (REF_ALIAS / OPTIONAL_TO_PTR /
        # POINTER).
        if not is_reassign and not scope.in_branch:
            binding = _borrow_local_binding(stmt, vtype, declared, lc.prescan, analyzer)
            if binding is not None:
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
                comp_plan = _comprehensions._comp_decl_plan(
                        stmt, declared, scope.admission_pointers(),
                        lc.rebind_slot_locals, lc.storage_tuple_locals,
                        lc.narrow.narrowed.keys(), lc.prescan, analyzer)
                if comp_plan is None:
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                comp = _comprehensions._lower_comprehension(
                    stmt.init, comp_plan, lc, declared)
                declared[stmt.name] = vtype
                return THIRVarDecl(name=stmt.name, resolved_type=vtype,
                                   init=comp, loc=loc)
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
        # F2d rebind-slot reseat: an rvalue ctor / by-value source. It lowers as a
        # plain value-form call; emit wraps it as `p = &*(__slot_N = <value>)`
        # using the rebind slot allocated at the decl. Checked before the lvalue
        # POINTER reseat -- a rebind-slot local is in both `pointers` sets.
        if stmt.name in lc.rebind_slot_locals:
            if not _is_record_rvalue_source(stmt.init, declared, analyzer):
                note_detail("decl.rebind_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=_lower_expr(stmt.init, lc, declared), loc=loc)
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so this stays byte-identical).
        if stmt.name in lc.pointers:
            if not _f2_reseat_ok(stmt.init, declared, analyzer):
                note_detail("decl.reseat_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            convert = THIRFormConvert(
                result_type=vtype,
                value=_lower_field_source(stmt.init, lc, declared), form=Form.BORROW,
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
                note_detail("decl.ptr_union_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
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
        container_literal = (
            not is_reassign
            and not scope.in_branch
            and _container_literal_decl_ok(
                stmt, declared, lc.prescan, analyzer))
        storage_call = False
        if isinstance(stmt.init, TpyCall):
            fam = _storage_call_ret(analyzer.get_expr_type(stmt.init), analyzer)
            if fam is not None:
                if in_branch_first:
                    note_detail("decl.branch_storage_call")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if (_storage_call_container(fam)
                        and (is_reassign
                             or stmt.name in lc.prescan.reassigned
                             or stmt.name in lc.prescan.hoisted
                             or stmt.name in lc.prescan.move_through)):
                    note_detail("decl.container_call_reassigned")
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                if not _call_eligible(
                        stmt.init, declared, analyzer, temps_ok=True,
                        narrowed=lc.narrow.narrowed.keys(),
                        storage_ret_ok=True):
                    raise ThirUnsupported(stmt_reject_reason(stmt))
                storage_call = True
                _witness("decl.storage_call")
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
            if ut is not None:
                init: 'THIRExpr | None' = THIRLiteral(
                    result_type=ut, value=None, form=Form.STORAGE, loc=loc)
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
            # Gate-admitted value-tuple literal: the spelled brace-init against
            # the binding slot (decl and reassign alike; per-element targets
            # ride _lower_tuple_literal, so no outer retype applies).
            tuple_t = _value_tuple(declared.get(stmt.name, vtype), analyzer)
            if (tuple_t is None
                    or not _tuple_literal_ok(
                        stmt.init, tuple_t, declared, analyzer)):
                note_detail("decl.tuple_literal_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            _witness("decl.tuple_literal")
            init = _lower_tuple_literal(stmt.init, tuple_t, lc, declared)
        else:
            if not is_reassign and not container_literal and not storage_call:
                if in_branch_first:
                    slot_ok = (
                        _eligible_scalar(vtype) or _eligible_char(vtype)
                        or _eligible_enum(vtype, analyzer) is not None
                        or _resolved_str_value(vtype, analyzer) is not None
                        or _is_string_owned(vtype))
                    detail = "decl.branch_slot_type"
                else:
                    slot_ok = (
                        _eligible_scalar(vtype) or _eligible_char(vtype)
                        or _eligible_enum(vtype, analyzer) is not None
                        or _resolved_str_value(vtype, analyzer) is not None
                        or _resolved_bytes_value(vtype, analyzer) is not None
                        or _is_string_owned(vtype)
                        or _eligible_value_union(vtype) is not None
                        or _slice_object_type(vtype)
                        or _eligible_ptr_value(vtype, analyzer))
                    detail = "decl.slot_type"
                if not slot_ok:
                    note_detail(detail)
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
                and _value_opt_scalar_param(src.name, lc))
            if whole_optional_reassign:
                init = _flush_witness(
                    "flush.vardecl",
                    _lower_expr(
                        src, lc, declared, use=_ExprUse(allow_temps=True),
                        allow_whole_optional=True))
            else:
                init = (_flush_witness(
                            "flush.vardecl",
                            _lower_char_targeted(
                                src, vtype, lc, declared,
                                use=_ExprUse(
                                    result=_ExprResultUse.STORAGE,
                                    allow_temps=True)))
                        if stmt.init else None)
            init = _slot_literal_retype(init, declared.get(stmt.name, vtype))
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
            # A value-repr Optional[scalar] name reassigned to an existing
            # scalar local passes the bare optional (`q = p;`): the AST's
            # reassignment RHS threads no target type, so a narrowed read is NOT
            # unwrapped here (unlike a decl init / value-target position). This
            # is a pre-existing AST bug (`std::optional<T>` into a `T` slot is
            # invalid C++) mirrored byte-identically rather than fixed on one
            # path -- so strip the name arm's deref-on-narrow.
            if (isinstance(stmt.init, TpyName) and isinstance(init, THIRName)
                    and _value_opt_scalar_param(stmt.init.name, lc)):
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
              or _container_enum_spell(vtype, analyzer)):
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
        if isinstance(stmt.target, TpyName):
            if (stmt.target.name in narrowed
                    or (lc.prescan.has_self and stmt.target.name == "self")
                    or (isinstance(stmt.value, TpyStrLiteral)
                        and _eligible_char(
                            declared.get(stmt.target.name)))
                    or isinstance(stmt.value, TpyBytesLiteral)
                    or stmt.target.name not in declared):
                raise ThirUnsupported(stmt_reject_reason(stmt))
        elif isinstance(stmt.target, TpySubscript):
            if not _container_setitem_ok(
                    stmt, declared, pointers, narrowed, analyzer):
                raise ThirUnsupported(stmt_reject_reason(stmt))
        elif not (
                _f2b_optional_field_write_ok(
                    stmt, declared, pointers, analyzer)
                or _scalar_field_write_ok(stmt, declared, analyzer)
                or _f1_tuple_field_write_ok(
                    stmt, declared, lc.storage_tuple_locals, analyzer)
                or _ptr_union_field_write_ok(stmt, declared, analyzer)
                or _record_field_write_ok(
                    stmt, declared, analyzer, pointers, narrowed, lc.prescan)
                or _optional_record_field_write_ok(
                    stmt, declared, pointers, analyzer, narrowed, lc.prescan)
                or _container_field_write_ok(stmt, declared, analyzer)
                or _str_field_write_ok(stmt, declared, analyzer)):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        target_prechecked = isinstance(stmt.target, TpyFieldAccess)
        if isinstance(stmt.target, TpySubscript):
            # Container subscript write: the target lowers to the same
            # subscript node a read produces (bounds_safe + the BigInt index
            # narrow ride along); the value renders against the element slot
            # (literal retype), with the view->owned `std::string(v)` copy
            # for a view-form str source into an owned-str element -- the
            # AST's `_view_source_to_owned` chokepoint. A flushable position
            # (temp_args), like a name assign.
            target = _lower_expr(
                stmt.target, lc, declared,
                field_prechecked=target_prechecked)
            elem_t = analyzer.get_expr_type(stmt.target)
            value = _slot_literal_retype(
                _flush_witness("flush.assign",
                               _lower_expr(
                                   stmt.value, lc, declared,
                                   use=_ExprUse(
                                       result=_ExprResultUse.STORAGE,
                                       allow_temps=True))),
                elem_t)
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
            # A scalar / Char / Ptr field is a plain value assign -- no
            # borrow<->storage lift. The field write is the fifth flushable
            # statement position (`temp_args`).
            if (_eligible_scalar(ftype) or _eligible_char(ftype)
                    or _eligible_enum(ftype, analyzer) is not None
                    or _eligible_ptr_value(ftype, analyzer)):
                return THIRAssign(target=_lower_expr(
                                      stmt.target, lc, declared,
                                      field_prechecked=target_prechecked),
                                  value=_slot_literal_retype(
                                      _flush_witness(
                                          "flush.field_write",
                                          _lower_expr(stmt.value, lc, declared,
                                                      use=_ExprUse(
                                                          result=_ExprResultUse.STORAGE,
                                                          allow_temps=True))),
                                      ftype), loc=loc)
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
                    return THIRAssign(target=_lower_expr(
                                          stmt.target, lc, declared,
                                          field_prechecked=target_prechecked),
                                      value=lowered, loc=loc)
                _witness("field_write.record_rvalue")
                return THIRAssign(target=_lower_expr(
                                      stmt.target, lc, declared,
                                      field_prechecked=target_prechecked),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
            # A container-literal field write (`_container_field_write_ok`):
            # the target-threaded literal render assigns bare (a literal is
            # never a movable name) -- the same THIRContainerLiteral emit a
            # decl init gets, consumed by the field lvalue.
            if isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                       TpySetLiteral)):
                _witness("field_write.container_lit")
                return THIRAssign(target=_lower_expr(
                                      stmt.target, lc, declared,
                                      field_prechecked=target_prechecked),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
            # A str-family field write (`_str_field_write_ok`): the value
            # renders BARE -- `std::string::operator=(string_view)` absorbs a
            # view source, so no view->owned construction and no move wrap
            # (str names are never in codegen's movable set).
            if _resolved_str_value(ftype, analyzer) is not None:
                _witness("field_write.str")
                return THIRAssign(target=_lower_expr(
                                      stmt.target, lc, declared,
                                      field_prechecked=target_prechecked),
                                  value=_lower_expr(stmt.value, lc, declared), loc=loc)
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
                    return THIRAssign(target=_lower_expr(
                                          stmt.target, lc, declared,
                                          field_prechecked=target_prechecked),
                                      value=lowered, loc=loc)
                _witness("field_write.optrec_rvalue")
                return THIRAssign(target=_lower_expr(
                                      stmt.target, lc, declared,
                                      field_prechecked=target_prechecked),
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
                target=_lower_expr(
                    stmt.target, lc, declared,
                    field_prechecked=target_prechecked),
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
            target=_lower_expr(
                stmt.target, lc, declared,
                field_prechecked=target_prechecked),
            value=_slot_literal_retype(
                _flush_witness("flush.assign",
                               _lower_expr(
                                   stmt.value, lc, declared,
                                   use=_ExprUse(
                                       result=_ExprResultUse.STORAGE,
                                       allow_temps=True))),
                analyzer.get_expr_type(stmt.target)),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        narrowed = lc.narrow.narrowed.keys()
        if (isinstance(stmt.target, TpyName)
                and stmt.target.name in narrowed):
            raise ThirUnsupported("stmt.aug_assign")
        if isinstance(stmt.target, TpySubscript):
            aug_ok = _container_aug_setitem_ok(
                stmt, declared, scope.admission_pointers(), narrowed,
                analyzer)
        else:
            aug_ok = (_scalar_aug_assign_ok(stmt, declared, analyzer)
                      or _str_aug_append_ok(
                          stmt, declared, lc.prescan, analyzer)
                      or _bytes_aug_concat_ok(
                          stmt, declared, lc.prescan, analyzer))
        if not aug_ok:
            raise ThirUnsupported("stmt.aug_assign")
        # str `t += v`: the in-place append (the AST's string branch inside the
        # resolved-binop arm), not the synthetic binop below. The target-type
        # dispatch mirrors _str_aug_append_ok's admission.
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            return THIRStrAppend(target=stmt.target.name,
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
            right=_slot_literal_retype(_lower_expr(stmt.value, lc, declared), aug_rslot),
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
        return THIRAssign(target=target, value=binop, loc=loc)
    if isinstance(stmt, TpyReturn):
        begin_stmt()
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
            if isinstance(stmt.value, TpyNoneLiteral):
                value: THIRExpr = THIRLiteral(result_type=ret_opt, value=None,
                                              form=Form.STORAGE, loc=loc)
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
                    and _value_opt_scalar_param(stmt.value.name, lc)):
                _witness("ret.value_opt_name")
                return THIRReturn(
                    value=replace(
                        _lower_expr(
                            stmt.value, lc, declared,
                            allow_whole_optional=True),
                        deref=False),
                    loc=loc)
            peeled = _peel_coerce(stmt.value)
            if (isinstance(peeled, TpyName)
                    and peeled.name in lc.prescan.value_opt_params):
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
            if _is_record_rvalue_source(stmt.value, declared, analyzer):
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
                container_ok = bool(_container_literal_ok(
                    source, lc.prescan.ret_container_storage,
                    declared, analyzer)
                    and _witness("ret.container_literal"))
            elif isinstance(source, TpyCall):
                container_ok = bool(_call_eligible(
                    source, declared, analyzer, temps_ok=True,
                    narrowed=narrowed, storage_ret_ok=True)
                    and _witness("ret.container_call"))
            if not container_ok:
                note_detail("return.container_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
        # A value-tuple return's literal source renders the spelled brace-init
        # against the return slot (per-element targets ride
        # _lower_tuple_literal); bare value-tuple names ride the generic tail.
        ret_vt = lc.prescan.ret_value_tuple
        if stmt.value is not None and ret_vt is not None:
            source = stmt.value
            tuple_ok = False
            if isinstance(source, TpyTupleLiteral):
                tuple_ok = bool(_tuple_literal_ok(
                    source, ret_vt, declared, analyzer)
                    and _witness("ret.tuple_literal"))
            elif isinstance(source, TpyName):
                tuple_ok = bool(
                    source.name not in narrowed
                    and source.name in declared
                    and _value_tuple(declared[source.name], analyzer)
                    is not None
                    and _witness("ret.tuple_name"))
            elif isinstance(source, TpyCall):
                tuple_ok = bool(_call_eligible(
                    source, declared, analyzer, temps_ok=True,
                    narrowed=narrowed, storage_ret_ok=True)
                    and _witness("ret.tuple_call"))
            if not tuple_ok:
                note_detail("return.tuple_source")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if isinstance(source, TpyTupleLiteral):
                return THIRReturn(
                    value=_lower_tuple_literal(source, ret_vt, lc, declared),
                    loc=loc)
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
                        field_prechecked=field_prechecked))
                 if stmt.value else None)
        # A float literal returned from a Float32 function takes the `f`
        # suffix (the AST threads the return type into the render).
        ret_t = lc.func.return_type if isinstance(lc.func.return_type,
                                                  TpyType) else None
        value = _slot_literal_retype(value, ret_t)
        # An expensive-copy value-Optional param (`int | None`) returned at its
        # narrowed last use moves the unwrapped value (`return std::move((*p));`,
        # seed_param_locals' value-optional movable face). `_is_move_source`
        # only fires for the movable-seeded expensive-copy inner; the deref
        # guard scopes it to the narrowed `(*p)` read (an un-narrowed return
        # renders the bare optional into an Optional slot -- a different arm).
        if (isinstance(stmt.value, TpyName) and isinstance(value, THIRName)
                and value.deref
                and _value_opt_scalar_param(stmt.value.name, lc)
                and _is_move_source(stmt.value, lc)):
            value = THIRMove(result_type=value.result_type, value=value,
                             form=value.form, loc=loc)
        # NB a bytes literal (or bytes value) at a BytesView return arrives
        # wrapped in the cross-type view coercion and is gate-rejected (the
        # deferred coercion cell), so no view retag is needed here.
        # An owned-str/bytes return (std::string / std::vector<uint8_t> by
        # value) fed a view-form source (view param / view local) copies
        # explicitly -- `return std::string(a);` / `return ::tpy::bytes_copy(a);`
        # -- the view->owned construction being explicit. Mirrors
        # _view_source_to_owned at the return boundary; a literal (VALUE) or
        # owned local / owned call result (STORAGE) returns bare.
        ret_str = lc.prescan.ret_str
        ret_bytes = lc.prescan.ret_bytes
        if value is not None and value.form is Form.BORROW:
            if ret_str is not None and is_str_type(ret_str):
                value = THIRFormConvert(result_type=ret_str, value=value,
                                        form=Form.STORAGE, loc=loc)
            elif ret_bytes is not None and is_bytes_type(ret_bytes):
                value = THIRFormConvert(result_type=ret_bytes, value=value,
                                        form=Form.STORAGE, loc=loc)
        return THIRReturn(value=value, loc=loc)
    if isinstance(stmt, TpyIf):
        begin_stmt()
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        admitted_declared = dict(declared)
        hoists = analyzer.if_branch_decls.get(id(stmt), {})
        if hoists and info is not None:
            note_detail("if.narrow_hoist")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        for name, raw in hoists.items():
            if name in declared:
                continue
            if name in lc.prescan.native_globals:
                note_detail("if.hoist_native_global")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            vtype = unwrap_ref_type(raw)
            if not _try_hoist_type_ok(vtype, analyzer):
                note_detail("if.hoist_type")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            admitted_declared[name] = vtype
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
        if not _condition_eligible(stmt.condition, admitted_declared, analyzer):
            c = stmt.condition
            if isinstance(c, TpyBinOp):
                lf = _type_family_tag(analyzer.get_expr_type(c.left), analyzer)
                rf = _type_family_tag(analyzer.get_expr_type(c.right), analyzer)
                note_detail(f"if.cond_binop.{c.op}.{lf}_{rf}")
            else:
                _kind_detail("if.cond_", c)
            raise ThirUnsupported(stmt_reject_reason(stmt))
        # Hoisted predecls render at the chain head (see THIRIf); mirrors
        # _lower_try's loop -- names spell RAW, enter the CALLER's `declared`
        # (function-scope, visible in every branch and after the if).
        hoist_decls: list[tuple[str, str]] = []
        for name, raw in hoists.items():
            if name in declared:
                continue
            vtype = unwrap_ref_type(raw)
            render_src = (_resolved_str_value(vtype, lc.analyzer)
                          or _resolved_bytes_value(vtype, lc.analyzer)
                          or vtype)
            hoist_decls.append((name, lc.render_type(render_src)))
            declared[name] = vtype
        if hoist_decls:
            _witness("if.hoist_decl")
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
            condition=_lower_truthy(stmt.condition, lc, declared),
            then_body=_lower_scoped_stmts(
                stmt.then_body, lc, dict(declared),
                branch_decls_ok=True,
                loop_depth=scope.loop_depth),
            else_body=else_stmts,
            else_is_nested=else_is_nested,
            hoist_decls=tuple(hoist_decls),
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
    if isinstance(stmt, (TpyDelVar, TpyGlobal)):
        # Both no-code faces emit only the source comment.
        if isinstance(stmt, TpyDelVar):
            for name in stmt.names:
                if (name not in declared or name in lc.narrow.narrowed
                        or not _del_var_trivial(declared[name], analyzer)):
                    raise ThirUnsupported("stmt.del_var:nontrivial")
        elif not all(name in lc.prescan.global_seeded for name in stmt.names):
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
        if not (recv_t is not None
                and _container_scalar_read(recv_t, analyzer)
                and _bigint_index_disposition(sub.index, analyzer) != "reject"):
            raise ThirUnsupported("stmt.del_item:recv_or_index")
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
    if isinstance(stmt, TpyWhile):
        if (stmt.orelse or analyzer.if_branch_decls.get(id(stmt))):
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
            return THIRWhile(
                condition=_lower_narrow_cond(
                    info, stmt.condition, lc, declared),
                body=_lower_narrowed_branch(stmt.body, m, var, u, lc,
                                            declared, loc,
                                            loop_depth=scope.loop_depth + 1),
                loc=loc,
            )
        if not _condition_eligible(stmt.condition, declared, analyzer):
            raise ThirUnsupported("stmt.while")
        return THIRWhile(
            condition=_lower_truthy(stmt.condition, lc, declared),
            body=_lower_scoped_stmts(
                stmt.body, lc, dict(declared),
                branch_decls_ok=True,
                loop_depth=scope.loop_depth + 1),
            loc=loc,
        )
    if isinstance(stmt, TpyAssert):
        if (stmt.message is not None
                and not isinstance(stmt.message, TpyStrLiteral)):
            raise ThirUnsupported("stmt.assert")
        if isinstance(stmt.condition, (TpyBoolLiteral, TpyNoneLiteral)):
            raise ThirUnsupported("stmt.assert")
        # The narrowing alias (if any) is appended by _lower_stmts'
        # _append_assert_narrow pass -- statement-level, like the post-if
        # alias. A re-assert's condition was sema-folded to `true`
        # (gate-admitted only via _reassert_bump_info).
        msg = stmt.message.value if isinstance(stmt.message, TpyStrLiteral) else None
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
            if not _condition_eligible(stmt.condition, declared, analyzer):
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
        target_types = _tuple_unpack_targets(
            stmt, analyzer, declared, lc.narrow.narrowed.keys())
        if target_types is None:
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
        for i, name in enumerate(stmt.targets):
            if name is None:
                target_cpps.append(None)
                continue
            tt = target_types[i]
            assert tt is not None
            # A str target's type is still a PendingStrType (params never
            # resolve it in place); resolve it to the concrete view before
            # render, else `render_type` raises. Scalars pass through.
            tt, cpp = _unpack_target_decl(tt, analyzer, lc.render_type)
            declared[name] = tt
            target_cpps.append(cpp)
        _witness("stmt.tuple_unpack")
        if isinstance(stmt.value, TpyName):
            return THIRTupleUnpack(
                source=stmt.value.name, targets=tuple(stmt.targets),
                target_cpps=tuple(target_cpps), loc=loc)
        # A free call hoists its arg temps (temp_args, inert for a field read).
        _witness("stmt.tuple_unpack.rvalue_source")
        return THIRTupleUnpack(
            source="", targets=tuple(stmt.targets),
            target_cpps=tuple(target_cpps),
            source_expr=_lower_expr(
                stmt.value, lc, declared,
                use=_ExprUse(
                    result=_ExprResultUse.STORAGE,
                    allow_temps=True),
                field_prechecked=isinstance(stmt.value, TpyFieldAccess)),
            loc=loc)
    if isinstance(stmt, TpyForEach):
        plan = _classify_for_each(
            stmt, analyzer, declared, lc.narrow.narrowed.keys())
        if plan is None:
            raise ThirUnsupported("stmt.for_each")
        it = stmt.iterable
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        et = plan.elem_type
        body_declared = dict(declared)
        body_declared[stmt.var] = et
        if plan.route == "tuple_unpack":
            # The head TpyTupleUnpack lowers to the dedicated node (a
            # per-target decl list); its targets enter the body scope. The
            # discard slots keep None through both tuples.
            up = stmt.body[0]
            target_cpps: list[str | None] = []
            for i, name in enumerate(up.targets):
                if name is None:
                    target_cpps.append(None)
                    continue
                tt = plan.unpack_target_types[i]
                assert tt is not None
                tt, cpp = _unpack_target_decl(
                    tt, analyzer, lc.render_type)
                body_declared[name] = tt
                target_cpps.append(cpp)
            head = THIRTupleUnpack(
                source=stmt.var,
                targets=tuple(up.targets),
                target_cpps=tuple(target_cpps),
                loc=getattr(up, "loc", None))
            body = (head,) + _lower_scoped_stmts(stmt.body[1:], lc,
                                                 body_declared,
                                                 branch_decls_ok=True,
                                                 loop_depth=scope.loop_depth + 1)
        else:
            body = _lower_scoped_stmts(
                stmt.body, lc, body_declared,
                branch_decls_ok=True,
                loop_depth=scope.loop_depth + 1)
        if plan.route == "range":
            if plan.bigint_counter:
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
                start = _slot_literal_retype(_lower_expr(start_arg, lc, declared), et)
                start_is_literal = _range_bound_literal_value(start_arg) is not None
                stop_arg = it.args[1]
            step = None
            step_kind = plan.step_kind
            if nargs == 3:
                _witness(f"range.step_{step_kind}")
                # The unit-step arms (plus_one / unit_neg) reference no step expr;
                # the other three render it (retype is a no-op for the fixed-int
                # counter this arm requires).
                if step_kind in ("literal_pos", "literal_neg", "variable"):
                    step = _slot_literal_retype(_lower_expr(it.args[2], lc, declared), et)
            return THIRForRange(
                var=stmt.var,
                elem_type=et,
                stop=_slot_literal_retype(_lower_expr(stop_arg, lc, declared), et),
                start=start,
                start_is_literal=start_is_literal,
                stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
                body=body,
                step=step,
                step_kind=step_kind,
                loc=loc,
            )
        if plan.str_list_method:
            _witness("foreach.str_list_method")
        if plan.container_field:
            _witness("foreach.container_field")
        if plan.value_tuple_elem:
            _witness("foreach.value_tuple_elem")
        return THIRForEach(
            var=stmt.var,
            elem_type=et,
            iterable=_lower_expr(
                it, lc, declared,
                use=_ExprUse(result=_ExprResultUse.ITERABLE),
                field_prechecked=isinstance(it, TpyFieldAccess)),
            body=body,
            const_loop_var=stmt.const_loop_var,
            iterable_lvalue=plan.iterable_lvalue,
            loc=loc,
        )
    if isinstance(stmt, TpyExprStmt):
        begin_stmt()
        if _is_builtin_print(stmt.expr, declared, lc.analyzer):
            e = stmt.expr
            narrowed = lc.narrow.narrowed.keys()
            pointers = scope.admission_pointers()
            if e.kwargs or e.double_star_unpack is not None:
                note_detail("print.kwargs")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            if any(isinstance(a, TpyName) and a.name in narrowed
                   for a in e.args):
                note_detail("print.narrowed_arg")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            comp_plans: dict[int, _comprehensions._CompPlan] = {}
            for arg in e.args:
                if type(arg) in _comprehensions._COMP_KINDS:
                    comp_plan = _comprehensions._comp_print_arg_plan(
                        arg, declared, pointers, lc.rebind_slot_locals,
                        lc.storage_tuple_locals, narrowed, analyzer)
                    ok = comp_plan is not None
                    if comp_plan is not None:
                        comp_plans[id(arg)] = comp_plan
                else:
                    ok = (_print_arg_ok(arg, declared, analyzer)
                          or (isinstance(arg, TpyName)
                              and arg.name not in pointers
                              and _wrap_print_form(
                                  arg, declared, analyzer) is not None
                              and _witness("print.wrap_arg")))
                if not ok:
                    fam = _type_family_tag(
                        analyzer.get_expr_type(arg), analyzer)
                    _kind_detail(f"print.arg.{fam}_", arg)
                    raise ThirUnsupported(stmt_reject_reason(stmt))
            return THIRPrint(
                args=tuple(_lower_print_arg(
                    a, lc, declared, comp_plan=comp_plans.get(id(a)))
                    for a in e.args),
                loc=loc)
        narrowed = lc.narrow.narrowed.keys()
        if isinstance(stmt.expr, TpyCall):
            eligible = _call_eligible(
                stmt.expr, declared, analyzer, stmt_position=True,
                temps_ok=True, narrowed=narrowed)
        elif isinstance(stmt.expr, TpyMethodCall):
            eligible = _method_call_eligible(
                stmt.expr, declared, analyzer, stmt_position=True,
                temps_ok=True, narrowed=narrowed,
                param_names=lc.prescan.param_names)
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
        match_plan = _match._match_plan(
                stmt, analyzer, declared, scope.admission_pointers(),
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                lc.prescan,
                in_branch=scope.in_branch,
                in_loop=scope.loop_depth > 0)
        if match_plan is None:
            raise ThirUnsupported(stmt_reject_reason(stmt))
        return _match._lower_match(stmt, match_plan, lc, declared, loc,
                                   loop_depth=scope.loop_depth)
    if isinstance(stmt, TpyRaise):
        return _lower_raise(stmt, lc, declared, loc)
    raise ThirUnsupported(f"stmt.unhandled:{type(stmt).__name__}")

def _lower_try(stmt: TpyTry, lc: _LowerCtx, declared: dict[str, TpyType],
               loc, *, in_branch: bool = False,
               loop_depth: int = 0) -> THIRTry:
    """Lower a finally_only- or throw-tier `try` (see `THIRTry` for the emit
    shapes). The hoisted predecls render here (`render_type`, codegen's
    type_to_cpp; names spell RAW like the AST arm) in sema's sorted order,
    and enter the CALLER's `declared` -- hoisted names are function-scope per
    Python scoping, visible in every body and after the statement. Each body
    lowers under its own narrowing-scope snapshot (the AST restores narrowed
    state between sibling blocks); a handler's `as` binding enters its body's
    scope typed like sema binds it, the catch parameter being the binding.
    `finally_terminates` is the AST's last-stmt raise/return fact;
    `body_terminates` is the frame-wrap fact (try body for finally_only, the
    whole statement for the throw tier)."""
    if stmt.tier == "return":
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
    for handler in stmt.handlers:
        if (handler.binding
                and _handler_binding_type(handler, lc.analyzer) is None):
            raise ThirUnsupported(stmt_reject_reason(stmt))
    hoist_decls: list[tuple[str, str]] = []
    for name, raw in hoists.items():
        if name in declared:
            # Already a declared local (e.g. bound outside an enclosing
            # loop): the AST's declared_vars check skips the predecl.
            continue
        vtype = unwrap_ref_type(raw)
        # Render from the sema-resolved view type: codegen's type_to_cpp
        # resolves a PendingStr/PendingBytes internally, but the analyzer-only
        # render_type default (to_cpp) does not. `declared` keeps the raw
        # binding type -- the same shape a normal str/bytes first-decl stores.
        render_src = (_resolved_str_value(vtype, lc.analyzer)
                      or _resolved_bytes_value(vtype, lc.analyzer)
                      or vtype)
        hoist_decls.append((name, lc.render_type(render_src)))
        declared[name] = vtype
    if hoist_decls:
        _witness("try.hoist_decl")
    if stmt.tier == "finally_only":
        _witness("try.finally_only")
        body_terminates = stmts_terminate(stmt.try_body)
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
        body_terminates = stmts_terminate([stmt])
    handlers: list[THIRExceptHandler] = []
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
        handlers.append(THIRExceptHandler(
            cpp_type=cpp, binding=h.binding,
            body=_lower_scoped_stmts(
                h.body, lc, h_declared, loop_depth=loop_depth)))
    last = stmt.finally_body[-1] if stmt.finally_body else None
    finally_terminates = isinstance(last, (TpyRaise, TpyReturn))
    if body_terminates and stmt.finally_body:
        _witness("try.body_terminates")
    if finally_terminates:
        _witness("try.finally_terminates")
    return THIRTry(
        tier=stmt.tier,
        try_body=_lower_scoped_stmts(
            stmt.try_body, lc, dict(declared), loop_depth=loop_depth),
        handlers=tuple(handlers),
        else_body=_lower_scoped_stmts(
            stmt.else_body, lc, dict(declared), loop_depth=loop_depth),
        finally_body=_lower_scoped_stmts(
            stmt.finally_body, lc, dict(declared), loop_depth=loop_depth),
        hoist_decls=tuple(hoist_decls),
        body_terminates=body_terminates,
        finally_terminates=finally_terminates,
        loc=loc,
    )

def _lower_raise(stmt: TpyRaise, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc) -> THIRRaise:
    """Lower a bare re-raise or the ctor-form raise (see `THIRRaise`). Ctor
    args lower against the resolved `__init__`'s param slots through the
    shared call-arg machinery -- the gate admitted only scalar/str value
    slots, where `_gen_record_ctor_args` and `gen_call_arg` coincide."""
    if stmt.raise_expr is not None:
        raise ThirUnsupported("stmt.raise")
    if stmt.exception_type is None:
        _witness("raise.bare")
        return THIRRaise(loc=loc)
    if is_return_exception(stmt.exception_type):
        raise ThirUnsupported("stmt.raise")
    init = stmt.resolved_ctor_init
    if stmt.args:
        if init is None or len(stmt.args) > len(init.params):
            raise ThirUnsupported("stmt.raise")
        mutated = init.mutated_params or frozenset()
        for i, (_arg, param) in enumerate(zip(stmt.args, init.params)):
            pt = unwrap_readonly(unwrap_ref_type(param.type))
            if (i in mutated
                    or not (_eligible_scalar(pt)
                            or _resolved_str_value(pt, lc.analyzer)
                            is not None)):
                raise ThirUnsupported("stmt.raise")
    _witness("raise.ctor")
    cpp = error_return_to_cpp(stmt.exception_type,
                              lc.analyzer.ctx.module_name,
                              lc.analyzer.registry)
    params = init.params if init else []
    return THIRRaise(
        cpp_type=cpp,
        args=tuple(_lower_call_arg(a, p.type, lc, declared)
                   for a, p in zip(stmt.args, params)),
        via_virtual=stmt.raise_via_virtual,
        loc=loc,
    )

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
    the per-layer normal-exit elision folds identically at emit."""
    if stmt.is_async or lc.analyzer.if_branch_decls.get(id(stmt)):
        raise ThirUnsupported(stmt_reject_reason(stmt))
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
                and (_record_ctor_call_eligible(ctx, declared, lc.analyzer)
                     or _is_record_rvalue_source(
                         ctx, declared, lc.analyzer)))
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
            ctx_expr=_lower_expr(ctx, lc, declared),
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
    return THIRWith(
        items=tuple(items),
        body=_lower_scoped_stmts(
            stmt.body, lc, dict(declared), loop_depth=loop_depth),
        body_terminates=stmts_terminate(stmt.body),
        loc=loc,
    )

def _lower_print_arg(a: TpyExpr, lc: _LowerCtx,
                     declared: dict[str, TpyType], *,
                     comp_plan: '_comprehensions._CompPlan | None' = None,
                     ) -> THIRPrintArg:
    """Lower one print arg + tag its `std::cout <<` wrapper form. A str literal
    lowers to a THIRStrLiteral (RAW: emitted via cpp_string_literal_expr); an
    eligible scalar lowers normally with its type-derived form."""
    if type(a) in _comprehensions._COMP_KINDS:
        # C3 comp print arg: the stmt-expr render inside its container
        # printer (List/Set/DictPrinter -- gen_print's container arms).
        _witness("comp.print_arg")
        assert comp_plan is not None
        form = {"list": PrintForm.LIST, "set": PrintForm.SET,
                "dict": PrintForm.DICT}[comp_plan.kind]
        return THIRPrintArg(
            _comprehensions._lower_comprehension(
                a, comp_plan, lc, declared), form)
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
    opt = _print_optval_opt(a, lc.analyzer)
    if opt is not None:
        # An un-narrowed value-repr Optional[scalar/str] read -> the bare optional
        # (`p` / `this->fi`, no deref) inside a `::tpy::print_optional_val(...)`
        # wrap (gen_print's value-repr Optional arm).
        _witness("print.optval")
        form, inner_cpp = _print_optval_form(opt)
        return THIRPrintArg(
            _lower_expr(
                a, lc, declared, allow_whole_optional=True,
                field_prechecked=isinstance(a, TpyFieldAccess)),
            form, inner_cpp)
    # A container / value-tuple / F1-record NAME: the kind-keyed printer
    # wrap (or the record's raw operator<<) around the bare name -- the
    # same routing fact the gate admitted on (`_wrap_print_form`).
    wrap = _wrap_print_form(a, declared, lc.analyzer)
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
    return THIRPrintArg(
        _lower_expr(
            a, lc, declared,
            field_prechecked=isinstance(a, TpyFieldAccess)),
        _print_arg_form(arg_type))
