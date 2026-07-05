"""Statement gate + lowering: _stmt_eligible/_body_eligible and
_lower_stmt(s) with the per-shape arms (var-decl, branches, loops,
with, try, raise, narrowing statements).
"""

from __future__ import annotations
from dataclasses import field, fields as dataclass_fields, replace
from ...parse.nodes import (
    TpyAsPattern,
    TpyAssert,
    TpyAssign,
    TpyCapturePattern,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBreak,
    TpyBytesLiteral,
    TpyCall,
    TpyClassPattern,
    TpyContinue,
    TpyDelItem,
    TpyDelVar,
    TpyExpr,
    TpyExceptHandler,
    TpyExprStmt,
    TpyFieldAccess,
    TpyForEach,
    TpyGlobal,
    TpyIf,
    TpyIntLiteral,
    TpyLiteralPattern,
    TpyMatch,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpyOrPattern,
    TpyRaise,
    TpyReturn,
    TpyStmt,
    TpyStrLiteral,
    TpyTry,
    TpyValuePattern,
    TpyWildcardPattern,
    TpyTupleUnpack,
    TpyUnaryOp,
    TpyVarDecl,
    TpyWhile,
    TpyWith,
    VarLinkage,
)
from ...typesys import (
    IntLiteralType,
    LiteralType,
    NominalType,
    NoneType,
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
    is_bool_type,
    is_bytes_type,
    is_fixed_int_type,
    is_list,
    is_str_type,
)
from ...modules.type_resolution import is_native_iterable
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    is_storage_tuple_alias_decl,
)
from ...codegen_cpp.context import cpp_string_literal_expr, enum_cpp_name
from ...codegen_cpp.string_dispatch import STRING_SWITCH_THRESHOLD
from ...liveness import stmts_terminate
from ..faces import witness as _witness
from ..nodes import (
    Form,
    PrintForm,
    THIRAssert,
    THIRAssign,
    THIRBinOp,
    THIRBreak,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRContinue,
    THIRExpr,
    THIRExprStmt,
    THIRForEach,
    THIRForRange,
    THIRFormConvert,
    THIRIf,
    THIRIsinstance,
    THIRLiteral,
    THIRMatch,
    THIRMatchArm,
    THIRMatchArmEntry,
    THIRMatchBinding,
    THIRName,
    THIRNarrowAlias,
    THIRNarrowedRead,
    THIRNoOpStmt,
    THIROptionalPtrArg,
    THIRPrint,
    THIRExceptHandler,
    THIRPrintArg,
    THIRRaise,
    THIRReturn,
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
    _container_scalar_read,
    _dict_view_iterable_ok,
    _elif_link,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_scalar,
    _eligible_value_union,
    _enum_member_cpp,
    _f1_is_const,
    _f1_record,
    _f1_tuple,
    _f1_tuple_field_write_ok,
    _f2_reseat_ok,
    _f2b_optional_field_write_ok,
    _facts_have_concrete,
    _field_receiver_ok,
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
    _resolve_pending_view,
    _resolved_bytes_value,
    _resolved_str_value,
    _resolved_viewfam_value,
    _runtime_bigint,
    _slice_object_type,
    _str_self_append_rhs,
    _unwrap_lit_coerce,
    _var_decl_type,
)
from .context import (
    _LowerCtx,
    _Prescan,
    _WalkState,
)
from .expressions import (
    _assert_narrow_info,
    _borrow_local_binding,
    _bytes_aug_concat_ok,
    _call_eligible,
    _condition_eligible,
    _container_literal_decl_ok,
    _expr_eligible,
    _flush_witness,
    _is_builtin_print,
    _is_len_call,
    _is_move_source,
    _is_record_rvalue_source,
    _lower_call_arg,
    _lower_char_targeted,
    _lower_expr,
    _lower_field_source,
    _lower_truthy,
    _method_call_eligible,
    _narrow_cond_info,
    _print_arg_form,
    _print_eligible,
    _ptr_union_field_write_ok,
    _ptr_union_source_ok,
    _rb_operand_slots,
    _record_ctor_call_eligible,
    _retag_bytes_literal_view,
    _scalar_aug_assign_ok,
    _scalar_field_write_ok,
    _slot_literal_retype,
    _stmt_value_temps_call,
    _str_aug_append_ok,
)

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

def _container_scalar_tuple_iter(t: TpyType | None, analyzer) -> bool:
    """A `list[tuple[scalar, ...]]` / `Array[tuple[scalar, ...], N]` binding --
    admitted as the tuple-unpack loop's iterable. Element-TOUCHING read gates
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
            and all(_eligible_scalar(et) for et in elem.element_types))

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
    return _range_bound_literal_value(arg) is not None

def _is_range_call(it: TpyExpr) -> bool:
    """The `range(...)` iterable form -- the for-loop cell's range-vs-container
    discriminator. Shared by `_for_range_eligible` and `_lower_stmt` so eligibility and
    lowering can't drift on which shape a for-loop takes."""
    return isinstance(it, TpyCall) and it.func_name == "range"

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

def _for_range_eligible(stmt: TpyForEach, analyzer, ws: _WalkState,
                        prescan: _Prescan) -> bool:
    # Only a plain `for v in range(stop | start, stop)` with step 1 over a fixed-int
    # counter (loop var not used after the loop). 3-arg/stepped range stays on the AST
    # path; the shared shape guards exclude the other richer for-shapes.
    it = stmt.iterable
    if not _is_range_call(it) or not _for_loop_shape_ok(stmt, analyzer, ws.declared):
        return False
    if it.kwargs or it.double_star_unpack is not None or len(it.args) not in (1, 2):
        return False
    et = unwrap_ref_type(stmt.elem_type) if stmt.elem_type is not None else None
    # A BigInt counter (a BigInt bound / module default int) shares the step-1
    # emit shape -- `cpp_elem` renders `::tpy::BigInt`, literal bounds retype
    # to the elem slot (`::tpy::BigInt(3)`), non-literal bounds hoist to
    # `__start/__stop` temps like fixed ints. The overflow-check helpers only
    # fire for step != +-1, which this gate never admits.
    if not _eligible_scalar(et):
        return False
    if _runtime_bigint(et, analyzer):
        _witness("range.bigint_counter")
    nargs = len(it.args)
    if nargs == 2 and not _range_bound_eligible(it.args[0], ws.declared, analyzer):
        return False
    stop_arg = it.args[0] if nargs == 1 else it.args[1]
    if not _range_bound_eligible(stop_arg, ws.declared, analyzer):
        return False
    body_ws = ws.branch_copy()
    body_ws.declared[stmt.var] = et  # loop var's resolved (fixed-int) type
    return _body_eligible(stmt.body, analyzer, body_ws, prescan, in_branch=True,
                          in_loop=True)

def _for_each_container_eligible(stmt: TpyForEach, analyzer, ws: _WalkState,
                                 prescan: _Prescan) -> bool:
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
    if not _for_loop_shape_ok(stmt, analyzer, ws.declared):
        return False
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
    if isinstance(it, TpyCall) and not _is_range_call(it):
        if not _call_eligible(it, ws.declared, analyzer, container_ret_ok=True):
            return False
        ret = analyzer.get_expr_type(it)
        it_type = _resolved_str_value(ret, analyzer)
        if it_type is None:
            # container_ret_ok widened _call_eligible past str returns; the
            # bytes returns it admits in value position are filtered here.
            if not _nonvalue_container_ret(ret):
                return False
            it_type = unwrap_readonly(unwrap_send_sync(ret))
    elif isinstance(it, TpyMethodCall):
        # `for v in d.values():` / `for k in d.keys():` -- the view result is
        # an rvalue (owning `auto __obj_N =` capture, iterable_lvalue=False).
        if not _dict_view_iterable_ok(it, ws.declared, analyzer):
            return False
        it_type = analyzer.get_expr_type(it)
    elif isinstance(it, TpyName):
        if it.name not in ws.declared:
            return False
        # declared (the binding type) rather than get_expr_type: a container-literal
        # local's use sites carry the pre-resolution PendingListType (see
        # _method_call_eligible). A param binding is Ref/readonly-wrapped -- unwrap
        # like _container_scalar_read does internally.
        it_type = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ws.declared[it.name])))
        # A str/bytes local's binding is a Pending view type the registry lookup
        # can't see through; resolve to the concrete view/owned nominal first.
        it_view = _resolved_viewfam_value(it_type, analyzer)
        if it_view is not None:
            it_type = it_view
    elif isinstance(it, TpyFieldAccess):
        if not _field_receiver_ok(it, ws.declared, analyzer):
            return False
        it_type = _resolved_viewfam_value(analyzer.get_expr_type(it), analyzer)
        if it_type is None:
            return False
    else:
        return False
    if not is_native_iterable(it_type, analyzer.registry):
        return False
    # The loop var (list/set/Span/Array element, or dict key) is a value scalar (typed
    # copy) or an F1-record (a borrow alias -- `auto&&`/`const auto&`, read/written
    # `.field` exactly like a record param, so it flows through the body constructs
    # identically). No rebinding guard is needed: sema forbids reassigning a non-value
    # loop var (`_check_nonvalue_rebinding` -- `p = other` is a hard error), so an
    # eligible record loop var is only ever read or field-mutated through the alias, both
    # matching Python's reference semantics.
    # resolve_int_literals: a literal-seeded container's elem_type is still
    # IntLiteral (IntLiteralType.to_cpp() would emit the VALUE); the AST binding
    # emits the resolved default-int spelling.
    et = (resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                               analyzer.ctx.default_int_for_literal)
          if stmt.elem_type is not None else None)
    if (not _eligible_scalar(et) and not _eligible_char(et)
            and _resolved_str_value(et, analyzer) is None
            and not _f1_record(et, analyzer)):
        return False
    body_ws = ws.branch_copy()
    body_ws.declared[stmt.var] = et
    return _body_eligible(stmt.body, analyzer, body_ws, prescan, in_branch=True,
                          in_loop=True)

def _for_tuple_unpack_eligible(stmt: TpyForEach, analyzer, ws: _WalkState,
                               prescan: _Prescan) -> bool:
    """`for a, b in <iterable>:` -- the parser desugars to a ForEach over a
    synthetic `__for_tup_N` var whose body leads with a TpyTupleUnpack from
    it. Slice: an lvalue `list[tuple[scalar]]`-family name or a `d.items()`
    dict-view call as the iterable; all-new plain value-scalar targets (no
    ref/owned/const-ref elements, no discard restrictions -- `_` slots skip).
    Record/str elements take the borrow/view target branches of
    _gen_tuple_unpack -- deferred rows."""
    if not stmt.is_tuple_unpack:
        return False
    if (stmt.is_async or stmt.orelse or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return False
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    if stmt.var in ws.declared:
        return False
    if not stmt.body or not isinstance(stmt.body[0], TpyTupleUnpack):
        return False
    up = stmt.body[0]
    if not (isinstance(up.value, TpyName) and up.value.name == stmt.var):
        return False
    # All-new plain value targets: the ref/owned/const-ref element forms and
    # reused (predeclared/assign) targets take other _gen_tuple_unpack
    # branches.
    if any(up.is_ref) or any(up.is_owned) or any(up.is_const_ref):
        return False
    if not all(up.is_new):
        return False
    target_types = []
    for i, name in enumerate(up.targets):
        tt = unwrap_ref_type(up.target_types[i])
        if name is None:
            target_types.append(None)
            continue
        if name in ws.declared or name in ws.narrowed:
            return False
        if not _eligible_scalar(tt):
            return False
        target_types.append(tt)
    it = stmt.iterable
    if isinstance(it, TpyMethodCall):
        # `for k, v in d.items():` -- the items view is an rvalue capture.
        if not _dict_view_iterable_ok(it, ws.declared, analyzer,
                                      methods=("items",)):
            return False
        it_type = analyzer.get_expr_type(it)
    elif isinstance(it, TpyName):
        if it.name not in ws.declared:
            return False
        it_type = unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(ws.declared[it.name])))
        if not _container_scalar_tuple_iter(it_type, analyzer):
            return False
        elem = unwrap_readonly(it_type.type_args[0])
        if len(elem.element_types) != len(up.targets):
            return False  # defensive: sema errors on arity mismatch
    else:
        return False
    if not is_native_iterable(it_type, analyzer.registry):
        return False
    body_ws = ws.branch_copy()
    body_ws.declared[stmt.var] = unwrap_ref_type(stmt.elem_type)
    for name, tt in zip(up.targets, target_types):
        if name is not None:
            body_ws.declared[name] = tt
    return _body_eligible(stmt.body[1:], analyzer, body_ws, prescan,
                          in_branch=True, in_loop=True)

def _with_target_arm(item, declared: dict[str, TpyType], prescan: _Prescan,
                     analyzer) -> 'tuple[WithTargetArm, TpyType | None] | None':
    """Classify a `with` item's as-target against `_gen_with`'s binding arms, or
    None when the item is out of the slice. Only the fresh, never-reassigned
    declaration arms are admitted: an already-declared target takes the AST's
    assign / optional-slot arms (branch-hoist machinery the emitter does not
    reproduce), and a reassigned non-value target takes the `T* name = &(...)`
    pointer-local arm, whose only constructible trigger -- a second `with`
    reusing the name -- puts an already-declared target in the same function
    (other reseat sources are borrow-checker-rejected), so it rides the
    already-declared cell."""
    if item.target is None:
        return WithTargetArm.NONE, None
    et = item.enter_type
    if not isinstance(et, TpyType):
        return None
    if item.target in declared:
        return None
    resolved = unwrap_readonly(unwrap_send_sync(et))
    if et.is_value_type():
        # `auto <name> = __enter__();` -- a value copy. Only the scalar slice
        # routes; str/Char/enum enter types (their name-form classification)
        # ride a later cell.
        if not _eligible_scalar(resolved):
            return None
        return WithTargetArm.VALUE, resolved
    if not _f1_record(resolved, analyzer) or item.target in prescan.reassigned:
        return None
    return WithTargetArm.REF, resolved

def _with_eligible(stmt: TpyWith, analyzer, ws: _WalkState, prescan: _Prescan,
                   *, in_branch: bool, in_loop: bool) -> bool:
    """The sync `with` gate arm -- mirrors `_gen_with`'s fresh-declaration
    slice. Rejected sub-shapes (each a later cell, not a redesign): async /
    resumable lowerings (the function gate rejects those bodies; the is_async
    check is defensive), bodies that first-declare variables (`if_branch_decls`
    -- the `_emit_branch_decls` hoist), already-declared as-targets, non-F1
    managers (cross-module / native / value-type managers, `self`, globals,
    `Ptr[T]` sources, field-access lvalues), and temp-registering manager
    expressions (the AST flushes temps BEFORE rendering managers with no
    second flush -- see the BUGS.md walrus-manager entry; `_is_record_
    rvalue_source`'s scalar-arg rule keeps those shapes out). Targets outlive
    the block (Python scoping), so they extend the OUTER scope after a
    successful walk -- which is why a first-declaring target inside a branch
    rejects, like the var-decl rule."""
    if stmt.is_async:
        return False
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    body_ws = ws.branch_copy()
    arms: list[tuple] = []
    for item in stmt.items:
        arm_et = _with_target_arm(item, body_ws.declared, prescan, analyzer)
        if arm_et is None:
            return False
        arm, et = arm_et
        if arm is not WithTargetArm.NONE and in_branch:
            return False
        ctx = item.context_expr
        if item.manager_borrowed:
            # An lvalue manager: a declared F1-record name (plain, or an F2
            # pointer-local -- the `*(...)` deref render). The other
            # is_already_pointer_source shapes (`self`, globals, Ptr[T]) and
            # field-access lvalue managers stay AST.
            if not (isinstance(ctx, TpyName) and ctx.name in body_ws.declared
                    and ctx.name not in body_ws.narrowed
                    and _f1_record(body_ws.declared[ctx.name], analyzer)):
                return False
        else:
            # An rvalue manager: a ctor call over scalar slots (literal args
            # resolve against the slot, like every ctor-call face) or a
            # by-value record-returning free call (`auto __ctx_N = CM(...);`).
            # Both lower through _lower_expr's existing call arms.
            if not (isinstance(ctx, TpyCall)
                    and _f1_record(analyzer.get_expr_type(ctx), analyzer)
                    and (_record_ctor_call_eligible(ctx, body_ws.declared,
                                                    analyzer)
                         or _is_record_rvalue_source(ctx, body_ws.declared,
                                                     analyzer))):
                return False
        arms.append((item, arm, et))
        if item.target is not None:
            body_ws.declared[item.target] = et
    if not _body_eligible(stmt.body, analyzer, body_ws, prescan,
                          in_branch=True, in_loop=in_loop):
        return False
    # Targets are declared at the enclosing C++ scope and stay visible after
    # the block -- extend the outer walk state like a top-level var-decl.
    for item, arm, et in arms:
        if item.target is not None:
            ws.declared[item.target] = et
    return True

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

def _try_eligible(stmt: TpyTry, analyzer, ws: _WalkState, prescan: _Prescan,
                  *, in_branch: bool, in_loop: bool) -> bool:
    """The `try` gate arm -- the finally_only tier (T1) and the throw tier
    (T2: C++ try/catch; handlers, bare `except:`, `as` bindings, `else`).
    The return tier is parked on the @error_return call rung (every such
    call is gate-rejected, and sema forces them into exactly these trys or
    propagating callers).

    Sema hoists EVERY name bound in the try/handler/else/finally bodies that
    its scan scope didn't already hold into `if_branch_decls[id(stmt)]` --
    including names declared OUTSIDE an enclosing loop (the loop body is its
    own sema scope). The AST emit then SKIPS any name already in
    `declared_vars`, so the gate mirrors both halves: an already-declared
    name skips (no predecl, its assigns hit the existing slot), and a
    genuinely fresh name is admitted for the plain-value tail arm of
    `_emit_branch_decls` only, and only when the try sits in straight-line
    function scope -- inside a branch the enclosing statement's own hoist
    rules would interact, and inside a loop a post-loop use would layer the
    scope_tracker storage hoist on top; both stay AST. Fresh names walk the
    bodies as already-declared (their first assigns lower as reassigns
    against the predecl slot) and stay visible after the statement per
    Python scoping. A throw-tier try-body first-declare that sema did NOT
    hoist (the da_new rule: no finally/else and some handler falls through)
    declares inside the C++ try scope -- the in-branch first-declare reject
    keeps it AST. Handler/else bodies walk fresh branch copies (the AST
    restores narrowed state between sibling blocks); the binding enters the
    handler's scope typed like sema binds it."""
    if stmt.tier == "return":
        return False
    hoist_declared: dict[str, TpyType] = {}
    for name, raw in analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in ws.declared:
            continue
        if name in prescan.native_globals:
            # The AST skips the predecl and renames later assigns to the C++
            # native global -- a shape the slice does not reproduce.
            return False
        if in_branch or in_loop:
            return False
        vtype = unwrap_ref_type(raw)
        if not _try_hoist_type_ok(vtype, analyzer):
            return False
        hoist_declared[name] = vtype
    body_ws = ws.branch_copy()
    body_ws.declared.update(hoist_declared)
    if not _body_eligible(stmt.try_body, analyzer, body_ws, prescan,
                          in_branch=True, in_loop=in_loop):
        return False
    for h in stmt.handlers:
        h_ws = ws.branch_copy()
        h_ws.declared.update(hoist_declared)
        if h.binding:
            bt = _handler_binding_type(h, analyzer)
            if bt is None:
                return False
            h_ws.declared[h.binding] = bt
        if not _body_eligible(h.body, analyzer, h_ws, prescan,
                              in_branch=True, in_loop=in_loop):
            return False
    if stmt.else_body:
        e_ws = ws.branch_copy()
        e_ws.declared.update(hoist_declared)
        if not _body_eligible(stmt.else_body, analyzer, e_ws, prescan,
                              in_branch=True, in_loop=in_loop):
            return False
    fin_ws = ws.branch_copy()
    fin_ws.declared.update(hoist_declared)
    if not _body_eligible(stmt.finally_body, analyzer, fin_ws, prescan,
                          in_branch=True, in_loop=in_loop):
        return False
    ws.declared.update(hoist_declared)
    return True

def _match_strategy(stmt: TpyMatch, analyzer) -> 'str | None':
    """The routed-tier discriminant -- the SINGLE complete mirror of
    _gen_match_dispatch's scalar routing, shared by the gate and lowering
    (an M3b bug came from each holding its own copy of the guard
    promotion; when both sides must agree on a routing fact, one function
    computes it and the byte-diff is only the backstop): a registered enum
    subject -> switch_enum, a plain fixed-int subject -> switch_primitive,
    and the remaining value scalars (bool -- -Wswitch-bool keeps it off
    the switch -- BigInt, floats) plus resolved-str subjects -> the `==`
    chain, if_elif or (any guard) if_elif_guarded -- EXCEPT a str subject
    at or above the switch-dispatch threshold, which the AST routes to
    the discriminator switch REGARDLESS of guards (deferred tier; the
    threshold counts only unguarded literal alternatives). A `Literal[...]`
    subject also dispatches on the AST path, but sema narrows the subject
    to a per-arm LiteralType (`case.type_facts`) whose dead-branch
    elimination can rewrite arm bodies that re-read the subject --
    rejected until THIR expression lowering learns the literal-fact fold.
    Union guard/shared-index routing is `_match_union_route`; record/
    Optional/polymorphic subjects are their own tiers (deferred)."""
    if stmt.subject_type is None:
        return None
    t = unwrap_readonly(stmt.subject_type)
    if isinstance(t, LiteralType):
        return None
    if isinstance(t, UnionType):
        # Recursive-alias wrappers (bare `Tree` unions, `Tree[Int32]`
        # instances -- the `.value` indirection) are PARKED against the
        # wrapper-form rung: wrapper-typed params/locals are themselves
        # function-gated, so no wrapper match can route until that form
        # lands (verified: a bare-arm Tree match rejects at the function
        # gate, never reaching this arm).
        if t.needs_wrapper():
            return None
        return "switch_union"
    if _eligible_enum(t, analyzer) is not None:
        return "switch_enum"
    if is_fixed_int_type(t):
        return "switch_primitive"
    if _eligible_scalar(t) or _resolved_str_value(t, analyzer) is not None:
        if _match_str_switches(stmt, analyzer):
            return None
        if any(c.guard is not None for c in stmt.cases):
            return "if_elif_guarded"
        return "if_elif"
    return None

def _union_index_members(u: UnionType) -> 'tuple | None':
    """The member ordering `_variant_index` scans: the wrapper's full
    member tuple when one exists (narrowed recursive subsets index against
    the full space), else the union's own members."""
    wrapper = u.wrapper_info()
    return wrapper.full_members if wrapper is not None else u.members

def _union_member_index(members, member) -> 'int | None':
    """`_variant_index`'s linear scan, None instead of raising."""
    for i, m in enumerate(members):
        if m == member:
            return i
    return None

def _match_str_switches(stmt: TpyMatch, analyzer) -> bool:
    """Mirrors _should_switch_str: a str subject with enough unguarded
    str-literal alternatives takes the discriminator-switch strategy
    (deferred tier), not the if/elif chain."""
    t = unwrap_readonly(stmt.subject_type)
    if _resolved_str_value(t, analyzer) is None:
        return False
    count = 0
    for case in stmt.cases:
        if case.guard is not None:
            continue
        pat = case.pattern
        if isinstance(pat, TpyLiteralPattern) and isinstance(pat.value, str):
            count += 1
        elif isinstance(pat, TpyOrPattern):
            if all(isinstance(a, TpyLiteralPattern)
                   and isinstance(a.value, str) for a in pat.patterns):
                count += len(pat.patterns)
    return count >= STRING_SWITCH_THRESHOLD

def _expr_any(e, pred) -> bool:
    """Whether `pred` holds for any node of the expression tree (generic
    dataclass walk over the TpyExpr children)."""
    stack = [e]
    while stack:
        cur = stack.pop()
        if pred(cur):
            return True
        for f in dataclass_fields(cur):
            v = getattr(cur, f.name)
            if isinstance(v, TpyExpr):
                stack.append(v)
            elif isinstance(v, (list, tuple)):
                stack.extend(x for x in v if isinstance(x, TpyExpr))
    return False

def _expr_contains_call(e) -> bool:
    return _expr_any(e, lambda n: isinstance(n, (TpyCall, TpyMethodCall)))

def _expr_mentions_name(e, name: str) -> bool:
    return _expr_any(e, lambda n: isinstance(n, TpyName) and n.name == name)

def _match_guard_ok(guard, declared: dict[str, TpyType], analyzer) -> bool:
    """A guard the slice renders: the AST emits `if ({gen_expr(guard)})` raw
    -- no truthy wrap -- so only a bool-TYPED expression is mirror-safe (a
    non-bool guard leans on C++ contextual conversion / operator bool).
    Call-free keeps arg temps out: the guard sits inside the arm block with
    no statement flush point (the same reason while-conditions gate them)."""
    t = analyzer.get_expr_type(guard)
    if t is None or not is_bool_type(unwrap_readonly(t)):
        return False
    if _expr_contains_call(guard):
        return False
    return _expr_eligible(guard, declared, analyzer)

def _match_arm_parts(case) -> 'tuple | None':
    """Split an arm into (test_pattern, binding_node): the label-generating
    pattern (None for an always-matching wildcard/capture arm -- the switch
    `default:` / the chain's final `} else {`) and the single whole-subject
    binding node (`TpyCapturePattern` / `TpyAsPattern`), or None when the
    shape is out of the slice (a capture or nested `as` under an `as` --
    two bindings; `_binding_pairs` emits both on the AST path)."""
    p = case.pattern
    if isinstance(p, TpyAsPattern):
        if isinstance(p.pattern, (TpyAsPattern, TpyCapturePattern)):
            return None
        return p.pattern, p
    if isinstance(p, TpyCapturePattern):
        return None, p
    if isinstance(p, TpyWildcardPattern):
        return None, None
    return p, None

def _match_label_ok(pattern, kind: str) -> bool:
    """A pattern the tier renders inline: an enum-member value pattern (the
    ENUM render, `_enum_member_cpp`), an int literal for the primitive
    switch (`_switch_literal_label`'s spelling), or any renderable literal
    for the if/elif `==` chain (`_gen_literal_cond`'s four arms).
    Captures/wildcards inside an or-pattern route the whole arm to
    `default:` on the AST switch path -- reject."""
    if kind == "switch_enum":
        return (isinstance(pattern, TpyValuePattern)
                and isinstance(pattern.expr, TpyFieldAccess)
                and pattern.expr.enum_member_of is not None)
    if kind == "switch_primitive":
        return (isinstance(pattern, TpyLiteralPattern)
                and isinstance(pattern.value, int))
    return (isinstance(pattern, TpyLiteralPattern)
            and isinstance(pattern.value, (bool, int, float, str)))

def _match_union_route(stmt: TpyMatch, u: UnionType) -> str:
    """_gen_match_dispatch's union routing: any guard, or two arms landing
    on one variant index (`_has_shared_variant_index` -- class and or-class
    alternatives only), takes the guarded path. Field-value sub-patterns
    would too, but keywords are gated out everywhere in this slice."""
    if any(c.guard is not None for c in stmt.cases):
        return "guarded_union"
    members = _union_index_members(u)
    seen: set[int] = set()
    for case in stmt.cases:
        pat = case.pattern
        if isinstance(pat, TpyAsPattern):
            pat = pat.pattern
        indices: list[int] = []
        if isinstance(pat, TpyClassPattern) and pat.resolved_type is not None:
            idx = _union_member_index(members, pat.resolved_type)
            if idx is not None:
                indices.append(idx)
        elif isinstance(pat, TpyOrPattern):
            for alt in pat.patterns:
                if (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None):
                    idx = _union_member_index(members, alt.resolved_type)
                    if idx is not None:
                        indices.append(idx)
        for idx in indices:
            if idx in seen:
                return "guarded_union"
            seen.add(idx)
    return "switch_union"

def _match_guarded_union_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                                 prescan: _Prescan, subj: TpyName,
                                 u: UnionType, hoist_declared: dict, *,
                                 in_loop: bool) -> bool:
    """The guarded_union (M4b) arm walk -- `_gen_match_guarded_union`'s
    per-index grouping over bare class patterns, or-patterns of class
    alternatives (wildcard ALTERNATIVES inside an or-pattern -- the
    broadcast-to-uncovered machinery -- stay out), `case None:`, and
    wildcard/capture arms (broadcast to every index; all-wildcard indices
    coalesce into `default:`). Guards are the M3b rule (bool-typed,
    call-free) plus a subject-name reject: the AST renders the guard
    BEFORE the arm's narrowing applies, so a guard reading the subject
    would spell the raw variant name. Bodies walk once per source case
    (broadcast copies re-emit the same lowered body; truncated-away
    entries were still walked -- conservative)."""
    members = _union_index_members(u)
    if members is None:
        return False
    for case in stmt.cases:
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        member = None
        if test is None:
            pass
        elif isinstance(test, TpyClassPattern):
            member = test.resolved_type
            if member is None or test.keywords or test.positional:
                return False
            if _union_member_index(members, member) is None:
                return False
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:
                return False
            for alt in test.patterns:
                if not (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None
                        and not alt.keywords and not alt.positional):
                    return False
                if _union_member_index(members, alt.resolved_type) is None:
                    return False
        elif isinstance(test, TpyLiteralPattern) and test.value is None:
            if _union_member_index(members, NoneType()) is None:
                return False
        else:
            return False
        facts = case.type_facts or {}
        if facts:
            if set(facts) != {subj.name} or member is None:
                return False
            arm_ws.declared[subj.name] = facts[subj.name]
            arm_ws.narrowed.add(subj.name)
        if bnode is not None:
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = (member if member is not None
                                           else arm_ws.declared[subj.name])
        if case.guard is not None:
            if not _match_guard_ok(case.guard, arm_ws.declared, analyzer):
                return False
            if _expr_mentions_name(case.guard, subj.name):
                return False
        if not _body_eligible(case.body, analyzer, arm_ws, prescan,
                              in_branch=True, in_loop=in_loop):
            return False
    return True

def _match_union_arms_ok(stmt: TpyMatch, analyzer, ws: _WalkState,
                         prescan: _Prescan, subj: TpyName, u: UnionType,
                         hoist_declared: dict, *, in_loop: bool) -> bool:
    """The switch_union (M4a) arm walk: bare class patterns (sema resolved
    the member; field sub-patterns -- their bindings and value conditions
    -- are deferred), or-patterns of those, `case None:` (the monostate
    index), and the wildcard/capture default; `as` binds the extracted
    member. Guards and shared variant indices route to
    `_gen_match_guarded_union` on the AST path -- rejected (M4b). Each
    class arm's body walks with the subject retyped to sema's arm fact and
    marked narrowed (reads rename to the `__case_{i}` alias at lowering,
    the U3 mechanic)."""
    if any(c.guard is not None for c in stmt.cases):
        return False
    members = _union_index_members(u)
    if members is None:
        return False
    seen: set[int] = set()
    for case in stmt.cases:
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        member = None
        if test is None:
            pass  # wildcard/capture -> the `default:` arm
        elif isinstance(test, TpyClassPattern):
            member = test.resolved_type
            if member is None or test.keywords or test.positional:
                return False
            idx = _union_member_index(members, member)
            if idx is None or idx in seen:
                return False
            seen.add(idx)
        elif isinstance(test, TpyOrPattern):
            if bnode is not None:
                # The AST or-pattern branch never emits the `as` binding.
                return False
            for alt in test.patterns:
                if not (isinstance(alt, TpyClassPattern)
                        and alt.resolved_type is not None
                        and not alt.keywords and not alt.positional):
                    return False
                idx = _union_member_index(members, alt.resolved_type)
                if idx is None or idx in seen:
                    return False
                seen.add(idx)
        elif isinstance(test, TpyLiteralPattern) and test.value is None:
            idx = _union_member_index(members, NoneType())
            if idx is None or idx in seen:
                return False
            seen.add(idx)
        else:
            return False
        # The arm's narrowing fact must be exactly sema's {subject: member}
        # (or filtered away); any other shape is out of the slice.
        facts = case.type_facts or {}
        if facts:
            if set(facts) != {subj.name} or member is None:
                return False
            arm_ws.declared[subj.name] = facts[subj.name]
            arm_ws.narrowed.add(subj.name)
        if bnode is not None:
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = (member if member is not None
                                           else arm_ws.declared[subj.name])
        if not _body_eligible(case.body, analyzer, arm_ws, prescan,
                              in_branch=True, in_loop=in_loop):
            return False
    return True

def _match_eligible(stmt: TpyMatch, analyzer, ws: _WalkState,
                    prescan: _Prescan, *, in_branch: bool,
                    in_loop: bool) -> bool:
    """The `match` gate arm -- M1: the unguarded scalar switch tiers
    (switch_enum / switch_primitive), no captures. The subject is a bare
    declared name (an lvalue -- `auto&`; pointer-local / narrowed /
    tuple-alias names render indirect and reject). Arms are enum-member /
    int-literal patterns, or-patterns of those, and the wildcard; guards,
    `as`/capture bindings, and arms carrying narrowing `type_facts` (never
    set for plain enum/fixed-int subjects -- defensive) reject to the AST
    path, which keeps the guarded tiers' second counter draw
    (`__match_end_N`) and the bind-mode machinery unreachable. The sema
    hoist (`if_branch_decls`) follows the try arm's discipline exactly:
    already-declared names skip, fresh plain-value names admit in
    straight-line function scope only, and every arm body walks with them
    in scope."""
    if stmt.polymorphic_dispatch:
        return False
    kind = _match_strategy(stmt, analyzer)
    if kind is None:
        return False
    subj = stmt.subject
    if not isinstance(subj, TpyName) or subj.name not in ws.declared:
        return False
    if (subj.name in ws.pointers or subj.name in ws.narrowed
            or subj.name in ws.storage_tuple_locals):
        return False
    if not _expr_eligible(subj, ws.declared, analyzer):
        return False
    hoist_declared: dict[str, TpyType] = {}
    for name, raw in analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in ws.declared:
            continue
        if name in prescan.native_globals:
            return False
        if in_branch or in_loop:
            return False
        vtype = unwrap_ref_type(raw)
        if not _try_hoist_type_ok(vtype, analyzer):
            return False
        hoist_declared[name] = vtype
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        arms_ok = (_match_guarded_union_arms_ok
                   if _match_union_route(stmt, u) == "guarded_union"
                   else _match_union_arms_ok)
        if not arms_ok(stmt, analyzer, ws, prescan, subj, u,
                       hoist_declared, in_loop=in_loop):
            return False
        ws.declared.update(hoist_declared)
        return True
    always_match_arms = 0
    group_guards: dict[str, list[bool]] = {}
    for i, case in enumerate(stmt.cases):
        if case.type_facts:
            return False
        parts = _match_arm_parts(case)
        if parts is None:
            return False
        test, bnode = parts
        if test is None:
            # An always-matching arm: the switch `default:` / the plain
            # chain's final `} else {`. The plain chain emits source order,
            # so a non-final one would strand later arms. The guarded
            # chain's standalone blocks take any position and any count (a
            # failed guard falls through); the switch tiers merge them into
            # one default group, order-checked below.
            always_match_arms += 1
            if kind == "if_elif" and (always_match_arms > 1
                                      or i != len(stmt.cases) - 1):
                return False
            key = ""
        elif isinstance(test, TpyOrPattern):
            if not all(_match_label_ok(alt, kind) for alt in test.patterns):
                return False
            key = "|".join(_match_case_label(alt, kind, analyzer)
                           for alt in test.patterns)
        elif not _match_label_ok(test, kind):
            return False
        else:
            key = _match_case_label(test, kind, analyzer)
        if kind in ("switch_enum", "switch_primitive"):
            group_guards.setdefault(key, []).append(case.guard is not None)
        arm_ws = ws.branch_copy()
        arm_ws.declared.update(hoist_declared)
        if bnode is not None:
            # The binding aliases the whole subject; only _emit_binding's
            # value arms are in the slice -- a pointer/narrowed/tuple-alias
            # name would take the aliasing arms.
            if (bnode.name in ws.pointers or bnode.name in ws.narrowed
                    or bnode.name in ws.storage_tuple_locals):
                return False
            arm_ws.declared[bnode.name] = arm_ws.declared[subj.name]
        if case.guard is not None and not _match_guard_ok(
                case.guard, arm_ws.declared, analyzer):
            return False
        if not _body_eligible(case.body, analyzer, arm_ws, prescan,
                              in_branch=True, in_loop=in_loop):
            return False
    # Within each switch group, an unguarded entry only in the final
    # position (sema's duplicate-case rule guarantees it; a violated order
    # would emit an else-chain with no opened if) -- defensive.
    for guards in group_guards.values():
        if any(not g for g in guards[:-1]):
            return False
    ws.declared.update(hoist_declared)
    return True

def _raise_eligible(stmt: TpyRaise, analyzer, ws: _WalkState) -> bool:
    """Bare `raise` -> `throw;` and the ctor form `raise X(args)` ->
    `throw <cpp>(args);` (fresh construction -- _gen_raise's peephole, the
    static and dynamic types coincide). The expression form (`raise e` ->
    `e.__raise__()` + deref chain) is a deferred row. Return-tier raises
    (ReturnException -> make_unexpected through the finally-chain
    _make_return) reject -- they only occur inside @error_return bodies or
    return-tier trys, both gate-rejected, so the check is defensive. Ctor
    args admit value-scalar / resolved-str slots with eligible sources into
    non-mutated params, keeping every special `_gen_record_ctor_args` arm
    structurally unreachable."""
    if stmt.raise_expr is not None:
        return False
    if stmt.exception_type is None:
        return True
    if is_return_exception(stmt.exception_type):
        return False
    if stmt.args:
        init = stmt.resolved_ctor_init
        if init is None or len(stmt.args) > len(init.params):
            return False
        mutated = init.mutated_params or frozenset()
        for a, p in zip(stmt.args, init.params):
            if p.name in mutated:
                return False
            pt = unwrap_readonly(unwrap_ref_type(p.type))
            if not (_eligible_scalar(pt)
                    or _resolved_str_value(pt, analyzer) is not None):
                return False
            if not _expr_eligible(a, ws.declared, analyzer):
                return False
    return True

def _stmt_eligible(stmt: TpyStmt, analyzer, ws: _WalkState,
                   prescan: _Prescan, *, in_branch: bool,
                   in_loop: bool = False) -> bool:
    if isinstance(stmt, TpyVarDecl):
        if stmt.linkage != VarLinkage.DEFAULT or stmt.init is None:
            return False
        # A direct rebind of an isinstance-narrowed name: the AST write targets
        # the extraction alias / the variant inconsistently across shapes --
        # out of the U3 slice (field writes THROUGH the alias stay eligible;
        # they rename like reads).
        if stmt.name in ws.narrowed:
            return False
        is_reassign = stmt.name in ws.declared
        # A var-decl inside a branch must reassign an already-declared local --
        # a name first-declared in a branch needs scope snapshot/restore (and
        # may hoist), which the slice does not reproduce.
        if in_branch and not is_reassign:
            return False
        if not is_reassign:
            # First decl of a non-value local (REF_ALIAS / OPTIONAL_TO_PTR /
            # POINTER), bound from a field read off an F1-record receiver. Its
            # non-value field init is not a value expression, so it is admitted
            # here, not via _expr_eligible (which rejects it). A POINTER local is
            # recorded so its later reassignments lower as reseats.
            binding = _borrow_local_binding(
                stmt, _var_decl_type(stmt, analyzer), ws.declared, prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.POINTER:
                    ws.pointers.add(stmt.name)
                elif binding is LocalBinding.REBIND_SLOT:
                    # An F2d rebind-slot local: in `pointers` for its `->` reads,
                    # in `rebind_slots` so its reseats lower as rvalue rebinds.
                    ws.pointers.add(stmt.name)
                    ws.rebind_slots.add(stmt.name)
                return True
            # F3 storage-tuple alias (`t = <storage tuple field>` -> `auto&& t = ...`):
            # a pointer-repr tuple local aliasing a storage tuple field off an
            # F1-record receiver. Tracked so its reads lift via tuple_to_pointer at
            # borrow boundaries (e.g. `return t`) and so the borrow-tuple write source
            # excludes it (it is storage form, a direct copy).
            if (is_storage_tuple_alias_decl(
                    _var_decl_type(stmt, analyzer), stmt.init, name=stmt.name,
                    reassigned=prescan.reassigned, hoisted=prescan.hoisted,
                    move_through=prescan.move_through)
                    and _const_exact_field_receiver_ok(stmt.init, ws.declared,
                                                       analyzer)
                    and _f1_tuple(analyzer.get_expr_type(stmt.init), analyzer) is not None):
                ws.storage_tuple_locals.add(stmt.name)
                return True
            # Container-literal local (`xs = [1, 2]` / `d = {k: v}`): the local
            # enters `declared` with its resolved container type, so the
            # receiver gates (subscript / len / iteration / method calls)
            # admit it exactly like a container param.
            if _container_literal_decl_ok(stmt, ws.declared, prescan, analyzer):
                return True
        elif stmt.name in ws.rebind_slots:
            # F2d rebind-slot reseat: an rvalue F1-record ctor / by-value source.
            return _is_record_rvalue_source(stmt.init, ws.declared, analyzer)
        elif stmt.name in ws.pointers:
            # F2a pointer-local reseat: an lvalue F1-record field source only.
            return _f2_reseat_ok(stmt.init, ws.declared, analyzer)
        # A str literal in a Char-typed slot renders as a target-typed C++
        # char literal (`c: Char = 'x'` -> `char c = 'x';`, lowered to
        # THIRCharLiteral). Only the single-char annotated DECL converts;
        # a reassign (`c = 'y'`) or multi-char literal is a sema type error,
        # so those rejects are defensive.
        decl_tgt = (ws.declared.get(stmt.name) if is_reassign
                    else _var_decl_type(stmt, analyzer))
        if isinstance(stmt.init, TpyStrLiteral) and _eligible_char(decl_tgt):
            if is_reassign or len(stmt.init.value) != 1:
                return False
        # `x = None` at a union binding renders `std::monostate{}`
        # (target-typed, like the Char literal above) -- for a value union AND
        # a pointer variant (the monostate member is form-neutral: both
        # spellings hold it directly). None at any other binding in the slice
        # is ineligible (Optional locals are not admitted), so this is the
        # only None-init arm.
        if isinstance(stmt.init, TpyNoneLiteral):
            return (_eligible_value_union(decl_tgt) is not None
                    or _eligible_ptr_union(decl_tgt, analyzer) is not None)
        # F4 U2: a pointer-variant union local. Sources are same-union names
        # (bare borrow copy) or -- for single-assignment locals -- a
        # value-variant field lvalue (the to_[const_]ptr_variant lift; a
        # reseat's const verdict would come from its own receiver, a chain
        # the slice does not reproduce).
        ptr_u = _eligible_ptr_union(decl_tgt, analyzer)
        if ptr_u is not None:
            return _ptr_union_source_ok(
                stmt.init, ws.declared, analyzer, ptr_u,
                allow_field=stmt.name not in prescan.reassigned)
        if not (_expr_eligible(stmt.init, ws.declared, analyzer)
                or _stmt_value_temps_call(stmt.init, ws, analyzer)):
            return False
        # First declaration: the local's type must be an eligible scalar (a
        # bare-literal init analyzes as IntLiteralType, pinning no width -> out)
        # / Char, or a str-slice binding (a `PendingStrType` whose view/owned
        # resolution is final pre-lowering -- `std::string_view` or
        # `std::string`).
        # A reassignment targets an already-validated local (its value just
        # renders into the existing slot), so the type check does not apply.
        if is_reassign:
            return True
        vtype = _var_decl_type(stmt, analyzer)
        # A `String` local only ever arises from an already-validated eligible
        # init (a concat result or another String local -- the only String
        # producers in the slice); it declares as `std::string`, byte-identical
        # to an owned str local.
        return (_eligible_scalar(vtype) or _eligible_char(vtype)
                or _eligible_enum(vtype, analyzer) is not None
                or _resolved_str_value(vtype, analyzer) is not None
                or _resolved_bytes_value(vtype, analyzer) is not None
                or _is_string_owned(vtype)
                or _eligible_value_union(vtype) is not None
                or _slice_object_type(vtype))
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyName):
            # A rebind of an isinstance-narrowed name: same reject as the
            # VarDecl/AugAssign arms. The parser emits TpyVarDecl for every
            # ordinary name-target assign, but macro-authored / frontend-IR
            # ASTs can build this shape directly.
            if stmt.target.name in ws.narrowed:
                return False
            # Char-targeted str literal: target-typed `'x'` render -> AST path
            # (mirrors the var-decl reassign guard).
            if (isinstance(stmt.value, TpyStrLiteral)
                    and _eligible_char(ws.declared.get(stmt.target.name))):
                return False
            # A bytes-literal value: this name-target TpyAssign only arises
            # from desugars (tuple-literal unpack), whose target-type threading
            # the owned/span flag does not mirror -> AST path. Plain
            # `name = b"..."` parses as TpyVarDecl and is handled there.
            if isinstance(stmt.value, TpyBytesLiteral):
                return False
            return (stmt.target.name in ws.declared
                    and (_expr_eligible(stmt.value, ws.declared, analyzer)
                         or _stmt_value_temps_call(stmt.value, ws, analyzer)))
        # F2b/F2c/F2e: an optional-field write `recv.field = <borrow>` / `= None`;
        # a plain scalar-field write `recv.field = <scalar>`; an F3 tuple-field
        # write `recv.field = <borrow tuple>` (tuple_to_storage); or an F4 U2
        # union-field write `recv.field = <borrow union name>` (to_value_variant).
        return (_f2b_optional_field_write_ok(stmt, ws.declared, ws.pointers, analyzer)
                or _scalar_field_write_ok(stmt, ws.declared, analyzer, ws)
                or _f1_tuple_field_write_ok(stmt, ws.declared,
                                            ws.storage_tuple_locals, analyzer)
                or _ptr_union_field_write_ok(stmt, ws.declared, analyzer))
    if isinstance(stmt, TpyReturn):
        if stmt.value is None:
            return True
        if prescan.ret_borrow_tuple is not None:
            # A borrow-form tuple return lifts a storage tuple lvalue via
            # `tuple_to_pointer` (F3). The source is a storage tuple lvalue: a field
            # read off an F1-record receiver, or a storage-tuple alias local (`auto&&`).
            # Subscript / call sources ride later F3 cells and stay on the AST path.
            if isinstance(stmt.value, TpyName):
                return stmt.value.name in ws.storage_tuple_locals
            return (_const_exact_field_receiver_ok(stmt.value, ws.declared,
                                                   analyzer)
                    and _f1_tuple(analyzer.get_expr_type(stmt.value), analyzer)
                    is not None)
        if prescan.ret_storage_opt is not None:
            # A storage-form Optional[F1-record] return admits `None`
            # (-> std::nullopt, F2c) or a borrow `T*` lift (-> ptr_to_optional[_move],
            # copy or move per last-use; F2c copy / F2e move). Storage-field / call
            # sources stay on the AST path.
            return (isinstance(stmt.value, TpyNoneLiteral)
                    or _is_borrow_ptr_local(stmt.value, ws.declared, ws.pointers))
        if prescan.ret_ptr_opt is not None:
            # A pointer-repr Optional[F1-record] return (`A | None` -> `A*`)
            # admits `None` (-> `nullptr`), an already-pointer borrow name
            # (an Optional-ptr param / OPTIONAL_TO_PTR local / F2
            # pointer-local -- bare, whether or not sema narrowed the read),
            # or a plain F1-record name (`&(name)`). Field / call / Own /
            # `self` / union-narrowed sources take other
            # _optional_pointer_form_value arms -> AST path.
            if isinstance(stmt.value, TpyNoneLiteral):
                return True
            if (not isinstance(stmt.value, TpyName)
                    or stmt.value.name == "self"
                    or stmt.value.name in ws.narrowed):
                return False
            if _optional_ptr_borrow_name(stmt.value, ws.declared,
                                         analyzer) is not None:
                return True
            dt = ws.declared.get(stmt.value.name)
            dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
                  if dt is not None else None)
            if isinstance(dt, OwnType):
                return False  # an Own source's move arm is not mirrored
            return _f1_record(dt, analyzer)
        # `return None` at a value-union return slot -> `std::monostate{}`
        # (F4 U1). Union names/literals flow through the generic arm below.
        if prescan.ret_union is not None and isinstance(stmt.value, TpyNoneLiteral):
            return True
        # A pointer-variant return (F4 U2) admits same-union borrow names
        # (bare) and `None` (`std::monostate{}`, target-typed -- the U1
        # return arm's pointer-variant twin). A MEMBER record name takes the
        # `&(...)` address-of lift, a storage field the (miscompiled,
        # BUGS.md) `&(h.u)` -- both AST-path shapes.
        if prescan.ret_ptr_union is not None:
            if isinstance(stmt.value, TpyNoneLiteral):
                return True
            return _ptr_union_source_ok(stmt.value, ws.declared, analyzer,
                                        prescan.ret_ptr_union,
                                        allow_field=False)
        # `return "x"` at a Char return renders a target-typed char literal
        # (`'x'`) on the AST path -- not reproduced outside compare position.
        if prescan.ret_char and isinstance(stmt.value, TpyStrLiteral):
            return False
        return (_expr_eligible(stmt.value, ws.declared, analyzer)
                or _stmt_value_temps_call(stmt.value, ws, analyzer))
    if isinstance(stmt, TpyIf):
        info = _narrow_cond_info(stmt.condition, ws.declared, analyzer)
        if info is not None:
            ok = _narrow_if_eligible(stmt, info, analyzer, ws, prescan,
                                     in_loop=in_loop)
        elif not _condition_eligible(stmt.condition, ws.declared, analyzer):
            return False
        else:
            # Branches do not extend the outer scope (no new-name decls
            # allowed in them), so each is checked against the same
            # declared-so-far set.
            ok = (_body_eligible(stmt.then_body, analyzer, ws, prescan,
                                 in_branch=True, in_loop=in_loop)
                  and _body_eligible(stmt.else_body, analyzer, ws, prescan,
                                     in_branch=True, in_loop=in_loop))
        if not ok:
            return False
        # The early-return implicit else of the (possibly plain-headed) elif
        # chain retypes its subject for the rest of the enclosing walk --
        # mutated in place like a POINTER decl mutates `pointers`.
        pf = _chain_post_if_fact(stmt, ws.declared, ws.narrowed, analyzer)
        if pf is not None:
            ws.declared[pf[0]] = pf[2]
            ws.narrowed.add(pf[0])
            ws.persistent_narrowed.add(pf[0])
        return True
    if isinstance(stmt, (TpyBreak, TpyContinue)):
        # Inside a routed loop the emit side carries every exit shape the
        # slice admits: the finally-frame walk renders the with/try cleanup
        # for frames pushed inside the innermost loop, and a `break` under a
        # live match switch reroutes via the lazily allocated
        # `goto __loop_break_N` (`_EmitState.switch_depth`). Only else-loops
        # (`goto __after_else_N`) stay gate-rejected. Outside a loop (a
        # shape sema rejects) stay AST defensively.
        return in_loop
    if isinstance(stmt, TpyWhile):
        # No while/else -- an else-loop breaks via `goto __after_else_N`.
        if stmt.orelse:
            return False
        info = _narrow_cond_info(stmt.condition, ws.declared, analyzer)
        if info is not None:
            # U4 `while isinstance(v, A)`: the loop-entry extraction is the
            # branch-alias shape (fresh block, non-persistent), popped at the
            # closing brace. A folded condition (`while (true)`) or an
            # already-narrowed subject stays AST (the re-extraction would
            # need the bump machinery at a non-statement scope).
            var, u, _members, folded, _isin = info
            if folded or var in ws.narrowed:
                return False
            if not _narrow_facts_ok(u, stmt.then_type_facts, var):
                return False
            m = _narrow_fact_member(u, stmt.then_type_facts, var)
            body_ws = ws.branch_copy()
            if m is not None:
                body_ws.declared[var] = m
                body_ws.narrowed.add(var)  # loop-scoped: NOT persistent
            return _body_eligible(stmt.body, analyzer, body_ws, prescan,
                                  in_branch=True, in_loop=True)
        if not _condition_eligible(stmt.condition, ws.declared, analyzer):
            return False
        return _body_eligible(stmt.body, analyzer, ws, prescan, in_branch=True,
                              in_loop=True)
    if isinstance(stmt, TpyAssert):
        # A computed message evaluates lazily inside an if block (a temps
        # shape the slice defers); constant conditions fold on the AST path
        # (`assert True` elides entirely) -- both stay AST.
        if stmt.message is not None and not isinstance(stmt.message,
                                                       TpyStrLiteral):
            return False
        if isinstance(stmt.condition, (TpyBoolLiteral, TpyNoneLiteral)):
            return False
        info = _narrow_cond_info(stmt.condition, ws.declared, analyzer)
        if info is not None:
            var, u, _members, folded, _isin = info
            # A fold on a still-union subject (exhaustiveness) stays AST;
            # the persistent re-assert rides _reassert_bump_info below
            # (its subject's declared type is the member, so no union is
            # found here). `var in ws.narrowed` is defensive: a narrowed
            # subject's declared type is never a routed union.
            if folded or var in ws.narrowed:
                return False
            if not _narrow_facts_ok(u, stmt.then_type_facts, var):
                return False
            m = _narrow_fact_member(u, stmt.then_type_facts, var)
            if m is not None:
                # Persistent: the subject retypes for the REST of the
                # enclosing walk (statement-level, like a post-if fact).
                ws.declared[var] = m
                ws.narrowed.add(var)
                ws.persistent_narrowed.add(var)
            return True
        if _reassert_bump_info(stmt, ws.declared, ws.persistent_narrowed,
                               analyzer) is not None:
            return True
        if stmt.then_type_facts and not _optional_narrow_facts_ok(
                stmt.then_type_facts, ws.declared, analyzer):
            return False  # non-mirrored narrowing facts stay AST
        return _condition_eligible(stmt.condition, ws.declared, analyzer)
    if isinstance(stmt, TpyAugAssign):
        # An aug-assign targeting an isinstance-narrowed name writes through
        # the extraction alias on the AST path -- out of the U3 slice.
        if isinstance(stmt.target, TpyName) and stmt.target.name in ws.narrowed:
            return False
        return (_scalar_aug_assign_ok(stmt, ws.declared, analyzer)
                or _str_aug_append_ok(stmt, ws.declared, prescan, analyzer)
                or _bytes_aug_concat_ok(stmt, ws.declared, prescan, analyzer))
    if isinstance(stmt, TpyForEach):
        return (_for_range_eligible(stmt, analyzer, ws, prescan)
                or _for_each_container_eligible(stmt, analyzer, ws, prescan)
                or _for_tuple_unpack_eligible(stmt, analyzer, ws, prescan))
    if isinstance(stmt, TpyGlobal):
        # Emits only its source comment (the AST returns "" -- comment, no
        # code). Admitted iff every name was seeded at entry (an eligible
        # same-module scalar global): the seeding is what makes the function's
        # reads/writes route as bare-name reassignments. An unseeded name
        # rejects here, which rejects the WHOLE body -- statement order is
        # irrelevant (the check reads prescan.global_seeded, and sema accepts
        # even a write BEFORE its `global` statement -- see lower_function's
        # seeding note), so no unseeded-global write survives to misroute.
        return all(n in prescan.global_seeded for n in stmt.names)
    if isinstance(stmt, TpyDelVar):
        # Only the trivially-destructible face, where the AST's FIRST skip in
        # _gen_del_var_code emits no code (so no other codegen state -- alias
        # sets, params, globals -- can disagree). The move-sink face for
        # owning locals (`{ auto __del_sink = std::move(x); }`) reads alias
        # bookkeeping the slice does not track -- those bodies stay AST.
        return all(n in ws.declared and n not in ws.narrowed
                   and _del_var_trivial(ws.declared[n], analyzer)
                   for n in stmt.names)
    if isinstance(stmt, TpyDelItem):
        # `del c[k]` on a bare-name builtin list/dict binding the read gate
        # already admits: the AST's fi lookup finds no `__delitem__` method fi
        # for list/dict, so the render is the fallback
        # `::tpy::__delitem__(c, k);` with the index through gen_index_expr --
        # bare for fixed-int / str keys, the `.to_fixed_check<int32_t>()`
        # narrow for a runtime-BigInt one (`_narrow_bigint_index` at lowering).
        # Multi-target del shares ONE source comment across N lines
        # -- a shape one THIR statement cannot carry -- so it stays AST.
        if len(stmt.targets) != 1:
            return False
        sub = stmt.targets[0]
        if sub.needs_optional_runtime_check or sub.slice_function_info is not None:
            return False
        recv = sub.obj
        if (not isinstance(recv, TpyName) or recv.name not in ws.declared
                or recv.name in ws.pointers or recv.name in ws.narrowed):
            return False
        return (_container_scalar_read(ws.declared[recv.name], analyzer)
                and _bigint_index_disposition(sub.index, analyzer) != "reject"
                and _expr_eligible(sub.index, ws.declared, analyzer))
    if isinstance(stmt, TpyExprStmt):
        # A bare expression statement: a builtin `print(...)` (common-arg subset)
        # or a same-module free-function call discarded for its side effects.
        if _is_builtin_print(stmt.expr, ws.declared, analyzer):
            return _print_eligible(stmt.expr, ws.declared, analyzer)
        if isinstance(stmt.expr, TpyCall):
            return _call_eligible(stmt.expr, ws.declared, analyzer,
                                  stmt_position=True, temps_ok=True,
                                  narrowed=ws.narrowed)
        if isinstance(stmt.expr, TpyMethodCall):
            # A container mutation call discarded for its side effect
            # (`xs.append(v)` / `xs.pop()` / ...).
            return _method_call_eligible(stmt.expr, ws.declared, analyzer,
                                         stmt_position=True, temps_ok=True,
                                         narrowed=ws.narrowed)
        return False
    if isinstance(stmt, TpyWith):
        return _with_eligible(stmt, analyzer, ws, prescan, in_branch=in_branch,
                              in_loop=in_loop)
    if isinstance(stmt, TpyTry):
        return _try_eligible(stmt, analyzer, ws, prescan, in_branch=in_branch,
                             in_loop=in_loop)
    if isinstance(stmt, TpyMatch):
        return _match_eligible(stmt, analyzer, ws, prescan,
                               in_branch=in_branch, in_loop=in_loop)
    if isinstance(stmt, TpyRaise):
        return _raise_eligible(stmt, analyzer, ws)
    return False

def _body_eligible(body, analyzer, ws: _WalkState, prescan: _Prescan, *,
                   in_branch: bool, in_loop: bool = False) -> bool:
    """Walk a statement list in source order, mirroring lowering's declared-scope
    growth: a top-level new-name var-decl extends scope; branch bodies don't.
    Walks its own `branch_copy()` so sibling branches never see each other's
    state (see `_WalkState` for what each container carries). `in_loop` is set
    while walking (any depth inside) a loop body -- the break/continue arm
    keys on it."""
    ws = ws.branch_copy()
    for stmt in body:
        if not _stmt_eligible(stmt, analyzer, ws, prescan, in_branch=in_branch,
                              in_loop=in_loop):
            return False
        if (not in_branch and isinstance(stmt, TpyVarDecl)
                and stmt.name not in ws.declared):  # first decl -- keep retro-widened type
            ws.declared[stmt.name] = _var_decl_type(stmt, analyzer)
    return True

def _narrow_if_eligible(stmt: TpyIf, info, analyzer, ws: _WalkState,
                        prescan: _Prescan, *, in_loop: bool = False) -> bool:
    """The U3 isinstance-narrowing `if`. Each branch with a concrete member
    fact is walked with the subject RETYPED to that member (reads then check
    like a record param / scalar local -- the same shapes the alias rename
    lowers to) and the subject marked narrowed (rebinding writes reject). A
    remaining-union fact keeps the subject un-retyped: its reads stay the bare
    variant on both paths. The elif continuation recurses whether it flattens
    (`else if`) or nests (`} else {` + if, when the outer else-fact is
    concrete) -- the split is an emit decision, taken again at lowering. The
    early-return implicit else is NOT handled here: it belongs to the last
    link of the whole (possibly plain-headed) chain and is applied by the
    caller's `_chain_post_if_fact` pass at statement level."""
    var, u, _members, folded, _isin = info
    if var in ws.narrowed and not folded:
        # Re-dispatch on an already-extracted subject: the AST still reads the
        # original variant here, but a same-scope re-extraction would bump the
        # alias suffix -- out of the slice (the exhaustiveness fold is safe:
        # it emits no new holds_alternative).
        return False
    if folded and stmt.else_body:
        # A dead else after an exhaustiveness fold never occurs in green code
        # (sema folded BECAUSE the chain exhausted the union) -- defensive.
        return False
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    if not (_narrow_facts_ok(u, stmt.then_type_facts, var)
            and _narrow_facts_ok(u, stmt.else_type_facts, var)):
        return False
    then_fact = _narrow_fact_member(u, stmt.then_type_facts, var)
    then_ws = ws.branch_copy()
    if then_fact is not None:
        then_ws.declared[var] = then_fact
        then_ws.narrowed.add(var)
    if not _body_eligible(stmt.then_body, analyzer, then_ws, prescan,
                          in_branch=True, in_loop=in_loop):
        return False
    if stmt.else_body:
        inner = _elif_link(stmt)
        if inner is not None:
            # Elif continuation: no extraction at this level
            # (is_elif_continuation) -- the inner if narrows for itself
            # against the original variant.
            return _stmt_eligible(inner, analyzer, ws.branch_copy(), prescan,
                                  in_branch=True, in_loop=in_loop)
        else_fact = _narrow_fact_member(u, stmt.else_type_facts, var)
        else_ws = ws.branch_copy()
        if else_fact is not None:
            else_ws.declared[var] = else_fact
            else_ws.narrowed.add(var)
        return _body_eligible(stmt.else_body, analyzer, else_ws, prescan,
                              in_branch=True, in_loop=in_loop)
    return True

def _lower_borrow_local(stmt: TpyVarDecl, vtype: TpyType, binding: 'LocalBinding',
                        is_const: bool, lc: _LowerCtx, loc) -> THIRVarDecl:
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
            name=stmt.name, resolved_type=vtype, init=_lower_expr(stmt.init, lc),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    field = _lower_field_source(stmt.init, lc)
    if binding is LocalBinding.REF_ALIAS:
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=field,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
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

def _lower_stmt(stmt: TpyStmt, lc: _LowerCtx, declared: dict[str, TpyType]) -> THIRStmt:
    # Single chokepoint: lower the statement, then carry the AST's
    # `no_source_comment` desugar flag onto the THIR node so the emitter dedups
    # the shared source comment (nested statements route through here too).
    result = _lower_stmt_dispatch(stmt, lc, declared)
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
                 stmt_fn=None) -> tuple[THIRStmt, ...]:
    """Lower a statement list, appending the U3 post-if extraction after an
    early-return narrowing `if` (`_gen_if`'s post-narrowing arm: a persistent,
    comment-less, statement-level alias) and extending the narrowing scope /
    retyping the subject for the REST of the list -- the enclosing branch/loop
    save-restore pops both (the AST's scope-snapshot semantics). `stmt_fn`
    lets the ctor body route its docstring/pass trivia arm."""
    fn = stmt_fn or _lower_stmt
    out: list[THIRStmt] = []
    for s in body:
        out.append(fn(s, lc, declared))
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
                        declared: dict[str, TpyType]) -> tuple[THIRStmt, ...]:
    """`_lower_stmts` under a narrowing-scope snapshot: a branch or loop body.
    A post-if / assert narrowing made inside pops at the closing brace (the
    AST's `narrowed_vars` / `declared_persistent_aliases` body restores)."""
    saved = lc.narrow.snapshot()
    try:
        return _lower_stmts(body, lc, declared)
    finally:
        lc.narrow = saved

def _lower_narrowed_branch(body, fact: 'TpyType | None', var: str,
                           u: UnionType, lc: _LowerCtx,
                           declared: dict[str, TpyType],
                           alias_loc) -> tuple[THIRStmt, ...]:
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
        out.extend(_lower_stmts(body, lc, branch_declared))
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
                         lc: _LowerCtx) -> THIRExpr:
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
        left = _lower_compound_cond(cond.left, isin, info, lc)
        right = _lower_compound_cond(cond.right, isin, info, lc)
        return THIRBinOp(result_type=lc.analyzer.get_expr_type(cond),
                         left=left, op="&&", right=right, resolved=None,
                         loc=getattr(cond, "loc", None))
    return _lower_expr(cond, lc)

def _lower_narrow_cond(cinfo, condition: TpyExpr, lc: _LowerCtx) -> THIRExpr:
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
        return _lower_compound_cond(condition, isin, (var, u, members, folded), lc)
    finally:
        lc.inline_narrowed = saved

def _lower_narrow_if(stmt: TpyIf, info, lc: _LowerCtx,
                     declared: dict[str, TpyType], loc) -> THIRIf:
    """Lower a U3 isinstance-narrowing `if` (gate-admitted by
    `_narrow_if_eligible`). The condition is the holds_alternative test (or
    the exhaustiveness fold's bare `true`); each branch lowers via
    `_lower_narrowed_branch`; an elif continuation recurses, breaking the
    emitter's flat `else if` chain when the outer else-fact would extract
    (`else_is_nested` -- the AST's `_has_concrete_isinstance_facts` gate)."""
    var, u, _members, _folded, _isin = info
    cond = _lower_narrow_cond(info, stmt.condition, lc)
    then_fact = _narrow_fact_member(u, stmt.then_type_facts, var)
    then_stmts = _lower_narrowed_branch(stmt.then_body, then_fact, var, u, lc,
                                        declared, loc)
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
                                                 dict(declared))
            else:
                # Flat chain: the chain-level post-if belongs to the enclosing
                # statement walk (_lower_stmts' _chain_post_if_fact pass), so
                # the link lowers bare.
                else_stmts = (_lower_stmt(inner, lc, dict(declared)),)
        else:
            else_fact = _narrow_fact_member(u, stmt.else_type_facts, var)
            else_stmts = _lower_narrowed_branch(
                stmt.else_body, else_fact, var, u, lc, declared,
                getattr(stmt.else_body[0], "loc", None))
    return THIRIf(condition=cond, then_body=then_stmts,
                  else_body=else_stmts, else_is_nested=else_is_nested, loc=loc)

def _lower_stmt_dispatch(stmt: TpyStmt, lc: _LowerCtx,
                         declared: dict[str, TpyType]) -> THIRStmt:
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if isinstance(stmt, TpyVarDecl):
        vtype = _var_decl_type(stmt, analyzer)
        # First decl of a non-value borrow local (REF_ALIAS / OPTIONAL_TO_PTR /
        # POINTER).
        if stmt.name not in declared:
            binding = _borrow_local_binding(stmt, vtype, declared, lc.prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.REBIND_SLOT:
                    # rvalue ctor source: an owned, mutable pointer-local (never
                    # const). Recorded in both sets, as eligibility did.
                    lc.pointers.add(stmt.name)
                    lc.rebind_slot_locals.add(stmt.name)
                    declared[stmt.name] = vtype
                    return _lower_borrow_local(stmt, vtype, binding, False, lc, loc)
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
                return _lower_borrow_local(stmt, vtype, binding, is_const, lc, loc)
            # F3 storage-tuple alias: `auto&& t = <storage tuple field>`. The local
            # aliases the source's storage, so a read off it is STORAGE form (lifted
            # via tuple_to_pointer at a borrow boundary); the init is the storage tuple
            # field source (no conversion node -- `auto&&` binds it directly). The
            # `storage_tuple_locals` membership is what makes a later read lift.
            if is_storage_tuple_alias_decl(
                    vtype, stmt.init, name=stmt.name,
                    reassigned=lc.prescan.reassigned, hoisted=lc.prescan.hoisted,
                    move_through=lc.prescan.move_through):
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
                    init=_lower_field_source(stmt.init, lc), form=Form.STORAGE,
                    cpp_local_representation=LocalBinding.STORAGE_TUPLE_ALIAS, loc=loc)
        # F2d rebind-slot reseat: an rvalue ctor / by-value source. It lowers as a
        # plain value-form call; emit wraps it as `p = &*(__slot_N = <value>)`
        # using the rebind slot allocated at the decl. Checked before the lvalue
        # POINTER reseat -- a rebind-slot local is in both `pointers` sets.
        if stmt.name in lc.rebind_slot_locals:
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=_lower_expr(stmt.init, lc), loc=loc)
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so this stays byte-identical).
        if stmt.name in lc.pointers:
            convert = THIRFormConvert(
                result_type=vtype,
                value=_lower_field_source(stmt.init, lc), form=Form.BORROW,
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
                    result_type=ptr_u, value=_lower_field_source(stmt.init, lc),
                    form=Form.BORROW, is_const=u_const, loc=loc)
            else:
                u_const = False
                u_init = _lower_expr(stmt.init, lc)
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
        # `x = x + y` self-append peephole (the AST's _try_str_inplace_append,
        # checked on every reassignment of an owned-str-family local before the
        # generic emit): the RHS concat's left operand is the target itself, so
        # the whole statement emits `x += y;` (buffer reuse) instead of the
        # concat-and-assign.
        if stmt.name in declared and _owned_str_append_target(vtype, analyzer):
            rhs = _str_self_append_rhs(stmt.name, stmt.init)
            if rhs is not None:
                return THIRStrAppend(target=stmt.name,
                                     value=_lower_expr(rhs, lc), loc=loc)
        # `x = None` at a value-union binding renders the monostate member --
        # target-typed at lowering, like the Char decl below (F4 U1).
        if isinstance(stmt.init, TpyNoneLiteral):
            ut = _eligible_value_union(declared[stmt.name]
                                       if stmt.name in declared else vtype)
            init: 'THIRExpr | None' = THIRLiteral(
                result_type=ut, value=None, form=Form.STORAGE, loc=loc)
        else:
            # A Char-annotated decl init lowers target-aware: `c: Char = 'x'` ->
            # `char c = 'x';` (the AST threads the decl type into the render);
            # a float literal into a Float32 binding (annotated decl or
            # reassign of a Float32 local) takes the `f` suffix the same way.
            # A flushable statement position: a direct call init may hoist
            # arg temps (temp_args, inert for non-call inits).
            src = _peel_stale_view_owned_coerce(
                stmt.init, declared.get(stmt.name, vtype), analyzer)
            init = (_flush_witness("flush.vardecl",
                                   _lower_char_targeted(src, vtype, lc,
                                                        temp_args=True))
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
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=init,
            cpp_type=(lc.render_type(vtype)
                      if _eligible_enum(vtype, analyzer) is not None else None),
            loc=loc)
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyFieldAccess):
            # A borrow `T*` stored into a storage `optional<T>` field lifts
            # borrow->storage via THIRFormConvert (`ptr_to_optional`, F2b); a
            # `None` literal stores as a STORAGE-form None (`std::nullopt`, F2c).
            # The target field-access renders `recv.field` / `recv->field`.
            ftype = analyzer.get_expr_type(stmt.target)
            # A scalar / Char field is a plain value assign -- no
            # borrow<->storage lift. The field write is the fifth flushable
            # statement position (`temp_args`, mirroring the gate's
            # `_scalar_field_write_ok` ws arm).
            if (_eligible_scalar(ftype) or _eligible_char(ftype)
                    or _eligible_enum(ftype, analyzer) is not None):
                return THIRAssign(target=_lower_expr(stmt.target, lc),
                                  value=_slot_literal_retype(
                                      _flush_witness(
                                          "flush.field_write",
                                          _lower_expr(stmt.value, lc,
                                                      temp_args=True)),
                                      ftype), loc=loc)
            if isinstance(stmt.value, TpyNoneLiteral):
                fvalue: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                               form=Form.STORAGE, loc=loc)
            elif (isinstance(stmt.value, TpyFieldAccess)
                    and _eligible_ptr_union(ftype, analyzer) is not None):
                # F4 U2 field-to-field union copy: a field source is not a
                # ptr-variant source on the AST path, so it assigns
                # storage-to-storage bare -- no to_value_variant lift.
                fvalue = _lower_field_source(stmt.value, lc)
            else:
                fvalue = THIRFormConvert(result_type=ftype,
                                         value=_lower_expr(stmt.value, lc),
                                         form=Form.STORAGE,
                                         move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRAssign(target=_lower_expr(stmt.target, lc), value=fvalue, loc=loc)
        # Name-target assign: the same self-append peephole as the var-decl
        # reassignment (the AST checks it at both sites).
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            rhs = _str_self_append_rhs(stmt.target.name, stmt.value)
            if rhs is not None:
                return THIRStrAppend(target=stmt.target.name,
                                     value=_lower_expr(rhs, lc), loc=loc)
        return THIRAssign(
            target=_lower_expr(stmt.target, lc),
            value=_slot_literal_retype(
                _flush_witness("flush.assign",
                               _lower_expr(stmt.value, lc, temp_args=True)),
                analyzer.get_expr_type(stmt.target)),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        # str `t += v`: the in-place append (the AST's string branch inside the
        # resolved-binop arm), not the synthetic binop below. The target-type
        # dispatch mirrors _str_aug_append_ok's admission.
        if (isinstance(stmt.target, TpyName)
                and _owned_str_append_target(
                    analyzer.get_expr_type(stmt.target), analyzer)):
            return THIRStrAppend(target=stmt.target.name,
                                 value=_lower_expr(stmt.value, lc), loc=loc)
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
        target = _lower_expr(stmt.target, lc)
        _, aug_rslot = _rb_operand_slots(stmt.resolved_binop)
        # FixedInt += BigInt: the AST wraps the value in
        # `({0}).to_fixed_check<T>()` BEFORE the binop substitution (sema
        # resolved the binop over the target width) -- mirrored as the
        # per-side operand cast. Same predicates as _gen_aug_assign_code's.
        right_cast = None
        if (is_fixed_int_type(analyzer.get_expr_type(stmt.target))
                and is_big_int_type(analyzer.get_expr_type(stmt.value))):
            _witness("narrow.aug_value")
            right_cast = ("({0}).to_fixed_check<"
                          f"{analyzer.get_expr_type(stmt.target).to_cpp()}>()")
        binop = THIRBinOp(
            result_type=tgt_type,
            left=_lower_expr(stmt.target, lc),
            op=stmt.op,
            right=_slot_literal_retype(_lower_expr(stmt.value, lc), aug_rslot),
            right_cast=right_cast,
            resolved=stmt.resolved_binop,
            paren_wrap=False,
            form=(Form.STORAGE if tgt_bytes is not None
                  and is_bytes_type(tgt_bytes) else Form.VALUE),
            loc=loc,
        )
        return THIRAssign(target=target, value=binop, loc=loc)
    if isinstance(stmt, TpyReturn):
        ret_tuple = lc.prescan.ret_borrow_tuple
        if stmt.value is not None and ret_tuple is not None:
            # Lift a storage tuple lvalue into the borrow-form tuple return via
            # `tuple_to_pointer` (F3). The element pointers' const-ness tracks the
            # source, mirroring the F1 OPTIONAL_TO_PTR const bump; sema forces a
            # mutable source when the return borrows mutably, so the const arm only
            # fires for a const source returning a const-element tuple. The source is
            # a storage-tuple alias local (`return t`) or a field read (`return h.pair`).
            if isinstance(stmt.value, TpyName):
                is_const = stmt.value.name in lc.const_locals
                inner: THIRExpr = _lower_expr(stmt.value, lc)  # STORAGE-form alias
            else:
                recv = stmt.value.obj  # TpyName (validated by _field_receiver_ok)
                is_const = (recv.name in lc.const_locals
                            or _param_is_const(recv.name, lc.func, analyzer,
                                               lc.record_name))
                inner = _lower_field_source(stmt.value, lc)
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
                value = THIRFormConvert(result_type=ret_opt,
                                        value=_lower_expr(stmt.value, lc),
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
            elif (isinstance(stmt.value, TpyName)
                    and stmt.value.name in lc.pointers):
                pvalue = _lower_expr(stmt.value, lc)  # already `T*` -- bare
            else:
                pvalue = THIROptionalPtrArg(result_type=ret_popt,
                                            form=Form.BORROW,
                                            value=_lower_expr(stmt.value, lc),
                                            addr_of=True, loc=loc)
            return THIRReturn(value=pvalue, loc=loc)
        # `return None` at a union slot -> `std::monostate{}`, target-typed
        # (F4 U1 value / U2 pointer variant -- the monostate member renders
        # the same in both spellings).
        ret_any_union = lc.prescan.ret_union or lc.prescan.ret_ptr_union
        if stmt.value is not None and ret_any_union is not None \
                and isinstance(stmt.value, TpyNoneLiteral):
            return THIRReturn(
                value=THIRLiteral(result_type=ret_any_union, value=None,
                                  form=Form.STORAGE, loc=loc), loc=loc)
        value = (_flush_witness("flush.return",
                                _lower_expr(stmt.value, lc, temp_args=True))
                 if stmt.value else None)
        # A float literal returned from a Float32 function takes the `f`
        # suffix (the AST threads the return type into the render).
        ret_t = lc.func.return_type if isinstance(lc.func.return_type,
                                                  TpyType) else None
        value = _slot_literal_retype(value, ret_t)
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
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        if info is not None:
            return _lower_narrow_if(stmt, info, lc, declared, loc)
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
                _lower_stmt(inner, lc, dict(declared)),)
        else:
            else_is_nested = inner is not None
            else_stmts = _lower_scoped_stmts(stmt.else_body, lc,
                                             dict(declared))
        return THIRIf(
            condition=_lower_truthy(stmt.condition, lc),
            then_body=_lower_scoped_stmts(stmt.then_body, lc, dict(declared)),
            else_body=else_stmts,
            else_is_nested=else_is_nested,
            loc=loc,
        )
    if isinstance(stmt, TpyBreak):
        return THIRBreak(loc=loc)
    if isinstance(stmt, TpyContinue):
        return THIRContinue(loc=loc)
    if isinstance(stmt, (TpyDelVar, TpyGlobal)):
        # Both gates admit only the no-code faces (trivially-destructible del
        # targets; seeded `global` names) -- the AST emits the source comment
        # and no code, exactly the `pass` shape.
        return THIRNoOpStmt(loc=loc)
    if isinstance(stmt, TpyDelItem):
        # `::tpy::__delitem__(c, k);` -- the AST's no-method-fi fallback in
        # _gen_del_item_code. The index rides gen_index_expr: bare for the
        # fixed-int / str-key shapes, the `.to_fixed_check<int32_t>()` narrow
        # for a runtime-BigInt one (the view-key pin still cannot fire --
        # view-typed keys are not admitted).
        sub = stmt.targets[0]
        return THIRExprStmt(
            expr=THIRCall(
                result_type=VoidType(),
                callee="__delitem__",
                native_name="tpy::__delitem__",
                args=(_lower_expr(sub.obj, lc),
                      _narrow_bigint_index(_lower_expr(sub.index, lc),
                                           sub.index, analyzer, loc)),
                loc=loc),
            loc=loc)
    if isinstance(stmt, TpyWhile):
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        if info is not None:
            # U4 while-isinstance: the loop-entry extraction is the branch
            # alias shape (fresh block, non-persistent); the subject retypes
            # for the body walk and pops at the closing brace.
            var, u, _members, _folded, _isin = info
            m = _narrow_fact_member(u, stmt.then_type_facts, var)
            return THIRWhile(
                condition=_lower_narrow_cond(info, stmt.condition, lc),
                body=_lower_narrowed_branch(stmt.body, m, var, u, lc,
                                            declared, loc),
                loc=loc,
            )
        return THIRWhile(
            condition=_lower_truthy(stmt.condition, lc),
            body=_lower_scoped_stmts(stmt.body, lc, dict(declared)),
            loc=loc,
        )
    if isinstance(stmt, TpyAssert):
        # The narrowing alias (if any) is appended by _lower_stmts'
        # _append_assert_narrow pass -- statement-level, like the post-if
        # alias. A re-assert's condition was sema-folded to `true`
        # (gate-admitted only via _reassert_bump_info).
        msg = stmt.message.value if isinstance(stmt.message, TpyStrLiteral) else None
        info = _narrow_cond_info(stmt.condition, declared, analyzer)
        if info is not None:
            cond = _lower_narrow_cond(info, stmt.condition, lc)
        elif (isinstance(stmt.condition, TpyCall)
              and stmt.condition.isinstance_var is not None):
            cond = THIRLiteral(
                result_type=analyzer.get_expr_type(stmt.condition), value=True,
                loc=getattr(stmt.condition, "loc", None))
        else:
            cond = _lower_truthy(stmt.condition, lc)
        return THIRAssert(condition=cond, message=msg, loc=loc)
    if isinstance(stmt, TpyForEach):
        it = stmt.iterable
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        # resolve_int_literals mirrors the eligibility gate: a literal-seeded
        # container's elem_type is still IntLiteral, whose to_cpp() emits the
        # value -- the binding must spell the resolved default int. A str loop
        # var (list[str] element / owned-str dict key) is a PendingStrType;
        # resolve it to its concrete view/owned type like the AST's
        # `resolve_type` does before loop_var_binding spells the binding (S5).
        et = resolve_int_literals(unwrap_ref_type(stmt.elem_type),
                                  analyzer.ctx.default_int_for_literal)
        str_et = _resolved_str_value(et, analyzer)
        if str_et is not None:
            et = str_et
        body_declared = dict(declared)
        body_declared[stmt.var] = et
        if stmt.is_tuple_unpack:
            # The head TpyTupleUnpack lowers to the dedicated node (a
            # per-target decl list); its targets enter the body scope. The
            # discard slots keep None through both tuples.
            up = stmt.body[0]
            target_cpps: list[str | None] = []
            for i, name in enumerate(up.targets):
                if name is None:
                    target_cpps.append(None)
                    continue
                tt = unwrap_ref_type(up.target_types[i])
                body_declared[name] = tt
                target_cpps.append(lc.render_type(tt))
            head = THIRTupleUnpack(
                source=stmt.var,
                targets=tuple(up.targets),
                target_cpps=tuple(target_cpps),
                loc=getattr(up, "loc", None))
            body = (head,) + _lower_scoped_stmts(stmt.body[1:], lc,
                                                 body_declared)
        else:
            body = _lower_scoped_stmts(stmt.body, lc, body_declared)
        if _is_range_call(it):
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
                start = _slot_literal_retype(_lower_expr(start_arg, lc), et)
                start_is_literal = _range_bound_literal_value(start_arg) is not None
                stop_arg = it.args[1]
            return THIRForRange(
                var=stmt.var,
                elem_type=et,
                stop=_slot_literal_retype(_lower_expr(stop_arg, lc), et),
                start=start,
                start_is_literal=start_is_literal,
                stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
                body=body,
                loc=loc,
            )
        # Container iteration -> the begin/end loop. The admitted iterable
        # shapes decide lvalue-ness statically (mirrors is_lvalue_iterable
        # over them): a name / F1-field read is an lvalue (`auto&` capture);
        # a call defers to `_call_iterable_lvalue` (a str return / `Own[...]`
        # container return is an rvalue -- owning `auto` capture; a borrow
        # container return is an lvalue).
        if isinstance(it, TpyCall):
            iterable_lvalue = _call_iterable_lvalue(it, lc.analyzer)
        elif isinstance(it, TpyMethodCall):
            # A dict-view method result (`d.values()` / `d.keys()`) is an
            # rvalue -- the owning `auto __obj_N =` capture.
            iterable_lvalue = False
        else:
            iterable_lvalue = True
        return THIRForEach(
            var=stmt.var,
            elem_type=et,
            iterable=_lower_expr(it, lc),
            body=body,
            const_loop_var=stmt.const_loop_var,
            iterable_lvalue=iterable_lvalue,
            loc=loc,
        )
    if isinstance(stmt, TpyExprStmt):
        if _is_builtin_print(stmt.expr, declared, lc.analyzer):
            return THIRPrint(
                args=tuple(_lower_print_arg(a, lc) for a in stmt.expr.args),
                loc=loc)
        return THIRExprStmt(expr=_flush_witness(
                                "flush.expr_stmt",
                                _lower_expr(stmt.expr, lc, temp_args=True)),
                            loc=loc)
    if isinstance(stmt, TpyWith):
        return _lower_with(stmt, lc, declared, loc)
    if isinstance(stmt, TpyTry):
        return _lower_try(stmt, lc, declared, loc)
    if isinstance(stmt, TpyMatch):
        return _lower_match(stmt, lc, declared, loc)
    if isinstance(stmt, TpyRaise):
        return _lower_raise(stmt, lc, declared, loc)
    raise AssertionError(f"ineligible stmt reached lowering: {type(stmt).__name__}")

def _lower_try(stmt: TpyTry, lc: _LowerCtx, declared: dict[str, TpyType],
               loc) -> THIRTry:
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
    hoist_decls: list[tuple[str, str]] = []
    for name, raw in lc.analyzer.if_branch_decls.get(id(stmt), {}).items():
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
            body=_lower_scoped_stmts(h.body, lc, h_declared)))
    last = stmt.finally_body[-1] if stmt.finally_body else None
    finally_terminates = isinstance(last, (TpyRaise, TpyReturn))
    if body_terminates and stmt.finally_body:
        _witness("try.body_terminates")
    if finally_terminates:
        _witness("try.finally_terminates")
    return THIRTry(
        tier=stmt.tier,
        try_body=_lower_scoped_stmts(stmt.try_body, lc, dict(declared)),
        handlers=tuple(handlers),
        else_body=_lower_scoped_stmts(stmt.else_body, lc, dict(declared)),
        finally_body=_lower_scoped_stmts(stmt.finally_body, lc, dict(declared)),
        hoist_decls=tuple(hoist_decls),
        body_terminates=body_terminates,
        finally_terminates=finally_terminates,
        loc=loc,
    )

def _match_case_label(pattern, kind: str, analyzer) -> str:
    """One pre-rendered arm spelling. Switch tiers: the AST's
    `gen_expr(pattern.expr)` for an enum member (`_enum_member_cpp` is that
    render's ENUM arm) or `_switch_literal_label`'s bare spelling for an
    int/bool literal. The if/elif tier: `_gen_literal_cond`'s RHS -- the
    emit composes `{subject} == {rhs}` per alternative."""
    if kind == "switch_enum":
        return _enum_member_cpp(pattern.expr, analyzer)
    val = pattern.value
    if isinstance(val, bool):
        return "true" if val else "false"
    if kind != "switch_primitive":
        if isinstance(val, float):
            return repr(val)
        if isinstance(val, str):
            return cpp_string_literal_expr(val)
    return str(val)

def _lower_match(stmt: TpyMatch, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc) -> THIRMatch:
    """Lower a scalar-tier `match` (see `THIRMatch` for the emit shapes).
    Labels/condition-RHS pre-render here. Switch tiers regroup the wildcard
    arm LAST regardless of source position (`_group_switch_arms` appends
    default_entries after the label groups); the if/elif tier keeps source
    order (its wildcard is the final `} else {` -- gate-enforced last). Arm
    bodies lower under narrowing-scope snapshots like every branch body
    (the AST's `_emit_case_body` narrowed_vars/alias restores); the hoisted
    predecls follow the try arm exactly and enter the caller's `declared`.
    `emit_unreachable` folds the AST's `_emit_match_unreachable_tail`
    condition at lowering; `synthetic_default` its `default: break;` rule
    (switch tiers only -- the if/elif chain has no default)."""
    kind = _match_strategy(stmt, lc.analyzer)
    assert kind is not None, "ineligible match reached lowering"
    if kind != "switch_union":  # the union lowerers witness their route
        _witness(f"match.{kind}")
    hoist_decls: list[tuple[str, str]] = []
    for name, raw in lc.analyzer.if_branch_decls.get(id(stmt), {}).items():
        if name in declared:
            continue
        vtype = unwrap_ref_type(raw)
        render_src = (_resolved_str_value(vtype, lc.analyzer)
                      or _resolved_bytes_value(vtype, lc.analyzer)
                      or vtype)
        hoist_decls.append((name, lc.render_type(render_src)))
        declared[name] = vtype
    if hoist_decls:
        _witness("match.hoist_decl")
    if kind == "switch_union":
        u = unwrap_readonly(stmt.subject_type)
        if _match_union_route(stmt, u) == "guarded_union":
            return _lower_match_guarded_union(stmt, lc, declared, loc,
                                              hoist_decls)
        return _lower_match_union(stmt, lc, declared, loc, hoist_decls)
    subj_type = declared.get(stmt.subject.name)
    is_chain = kind in ("if_elif", "if_elif_guarded")
    arms: list[THIRMatchArm] = []
    groups: dict[str, tuple[tuple[str, ...], list[THIRMatchArmEntry]]] = {}
    group_order: list[str] = []
    default_entries: list[THIRMatchArmEntry] = []
    for case in stmt.cases:
        test, bnode = _match_arm_parts(case)
        binding = None
        arm_declared = dict(declared)
        if bnode is not None:
            mode = ("assign" if bnode.name in declared
                    else "copy" if bnode.bind_by_value else "ref")
            _witness(f"match.bind_{mode}")
            binding = THIRMatchBinding(name=bnode.name, mode=mode)
            arm_declared[bnode.name] = subj_type
        guard = None
        if case.guard is not None:
            _witness("match.guard_arm")
            guard = _lower_expr(case.guard, lc)
        entry = THIRMatchArmEntry(
            body=_lower_scoped_stmts(case.body, lc, arm_declared),
            loc=case.loc, binding=binding, guard=guard)
        if test is None:
            labels: tuple[str, ...] = ()
        elif isinstance(test, TpyOrPattern):
            _witness("match.or_labels")
            labels = tuple(_match_case_label(alt, kind, lc.analyzer)
                           for alt in test.patterns)
        else:
            labels = (_match_case_label(test, kind, lc.analyzer),)
        if is_chain:
            # Source order, one entry per group (the always-match arm is
            # the plain chain's final `} else {` / a guarded standalone
            # block).
            if not labels and kind == "if_elif":
                _witness("match.if_elif_else")
            arms.append(THIRMatchArm(labels=labels, entries=(entry,)))
        elif not labels:
            _witness("match.wildcard_default")
            default_entries.append(entry)
        else:
            # _group_switch_arms: same-label cases (only guarded repeats
            # pass sema) merge into one group, first-appearance order.
            key = "|".join(labels)
            if key in groups:
                groups[key][1].append(entry)
            else:
                groups[key] = (labels, [entry])
                group_order.append(key)
    default_goto = False
    if not is_chain:
        for key in group_order:
            labels, entries = groups[key]
            arms.append(THIRMatchArm(labels=labels, entries=tuple(entries)))
        if any(len(a.entries) > 1 or a.entries[0].guard is not None
               for a in arms):
            _witness("match.switch_guard_chain")
        # _emit_switch_groups' needs_default_goto: a user default exists
        # and some labeled group is entirely guarded.
        default_goto = bool(default_entries) and any(
            all(e.guard is not None for e in a.entries) for a in arms)
        if default_goto:
            _witness("match.default_goto")
        if default_entries:
            arms.append(THIRMatchArm(labels=(),
                                     entries=tuple(default_entries)))
    synthetic_default = (not is_chain and not default_entries
                         and not stmt.is_exhaustive)
    if synthetic_default:
        _witness("match.synthetic_default")
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy=kind,
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=synthetic_default,
        default_goto=default_goto,
        loc=loc,
    )

def _lower_match_union(stmt: TpyMatch, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc,
                       hoist_decls: 'list[tuple[str, str]]') -> THIRMatch:
    """Lower a switch_union `match` (M4a): arms in SOURCE order (no default
    regrouping -- `_gen_match_switch_union` emits `default:` in place),
    labels are numeric variant indices (`_variant_index` over the full
    member ordering), a class arm with sema's narrowing fact draws the
    `__case_{i}` extraction alias (i = source case index) and lowers its
    body with subject reads renamed to it (the U3 mechanic) and the
    subject retyped to the fact; `as` binds the alias (or the composed
    get). Or-pattern arms stack index labels over one shared block; `case
    None:` is the monostate index. `is_ptr_variant` folds the `*std::get`
    deref (type-level -- the admitted bare-name subjects are exactly the
    U3 slice's ptr/value split)."""
    u = unwrap_readonly(stmt.subject_type)
    _witness("match.switch_union")
    members = _union_index_members(u)
    subj_name = stmt.subject.name
    arms: list[THIRMatchArm] = []
    for i, case in enumerate(stmt.cases):
        test, bnode = _match_arm_parts(case)
        facts = case.type_facts or {}
        binding = None
        variant_index = None
        case_alias = None
        member = None
        arm_declared = dict(declared)
        saved = lc.narrow.snapshot()
        try:
            if test is None:
                labels: tuple[str, ...] = ()
                _witness("match.union_default")
            elif isinstance(test, TpyClassPattern):
                member = test.resolved_type
                variant_index = _union_member_index(members, member)
                labels = (str(variant_index),)
                if facts:
                    case_alias = f"__case_{i}"
                    _witness("match.union_alias")
                    lc.narrow.narrowed[subj_name] = case_alias
                    arm_declared[subj_name] = facts[subj_name]
            elif isinstance(test, TpyOrPattern):
                _witness("match.or_labels")
                labels = tuple(
                    str(_union_member_index(members, alt.resolved_type))
                    for alt in test.patterns)
            else:  # `case None:`
                _witness("match.union_none_arm")
                variant_index = _union_member_index(members, NoneType())
                labels = (str(variant_index),)
            if bnode is not None:
                mode = ("assign" if bnode.name in declared
                        else "copy" if bnode.bind_by_value else "ref")
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=member is not None)
                arm_declared[bnode.name] = (member if member is not None
                                            else arm_declared[subj_name])
            body = _lower_stmts(case.body, lc, arm_declared)
        finally:
            lc.narrow = saved
        arms.append(THIRMatchArm(labels=labels, entries=(THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding,
            variant_index=variant_index, case_alias=case_alias),)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="switch_union",
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        is_ptr_variant=is_ptr_variant_union(u),
        loc=loc,
    )

def _lower_match_guarded_union(stmt: TpyMatch, lc: _LowerCtx,
                               declared: dict[str, TpyType], loc,
                               hoist_decls: 'list[tuple[str, str]]',
                               ) -> THIRMatch:
    """Lower a guarded_union `match` (M4b) -- `_gen_match_guarded_union`'s
    per-index grouping: class arms land on their variant index, or-pattern
    class alternatives distribute per index, wildcard/capture arms
    broadcast to EVERY index (each list truncates after its first
    unguarded entry -- later entries are unreachable), indices whose
    entries are all wildcard/capture coalesce into one trailing `default:`
    block (the first such list -- broadcast makes them identical). Groups
    emit in ascending index order. The group's `__case_{idx}` alias (idx =
    VARIANT index, unlike the unguarded tier's source-case numbering)
    draws when any class entry narrows or `as`-binds; class-entry bodies
    lower under the alias rename + fact retype. Broadcast copies of one
    source case lower once per hosting group (wildcard bodies never
    narrow, so the copies are identical; emit-side counters advance per
    emission like the AST's repeated gen_stmt runs)."""
    u = unwrap_readonly(stmt.subject_type)
    _witness("match.guarded_union")
    members = _union_index_members(u)
    n = len(members)
    subj_name = stmt.subject.name

    type_arms: dict[int, list] = {i: [] for i in range(n)}
    for case in stmt.cases:
        test, bnode = _match_arm_parts(case)
        if test is None:
            for idx in range(n):
                type_arms[idx].append((case, "always", None, bnode))
        elif isinstance(test, TpyClassPattern):
            idx = _union_member_index(members, test.resolved_type)
            type_arms[idx].append((case, "class", test.resolved_type, bnode))
        elif isinstance(test, TpyOrPattern):
            for alt in test.patterns:
                idx = _union_member_index(members, alt.resolved_type)
                type_arms[idx].append((case, "class", alt.resolved_type,
                                       None))
        else:  # `case None:`
            _witness("match.union_none_arm")
            idx = _union_member_index(members, NoneType())
            type_arms[idx].append((case, "none", None, None))
    for idx in range(n):
        truncated = []
        for entry in type_arms[idx]:
            truncated.append(entry)
            if entry[0].guard is None:  # field guards are gated out
                break
        type_arms[idx] = truncated
    default_indices: set[int] = set()
    default_src = None
    for idx in range(n):
        entries = type_arms[idx]
        if entries and all(k == "always" for _, k, _, _ in entries):
            default_indices.add(idx)
            if default_src is None:
                default_src = entries

    def lower_entry(case, kind, member, bnode, alias, idx):
        arm_declared = dict(declared)
        saved = lc.narrow.snapshot()
        try:
            facts = case.type_facts or {}
            if kind == "class" and facts and alias is not None:
                lc.narrow.narrowed[subj_name] = alias
                arm_declared[subj_name] = facts[subj_name]
            binding = None
            if bnode is not None:
                mode = ("assign" if bnode.name in declared
                        else "copy" if bnode.bind_by_value else "ref")
                _witness(f"match.bind_{mode}")
                binding = THIRMatchBinding(name=bnode.name, mode=mode,
                                           from_case_var=kind == "class")
                arm_declared[bnode.name] = (member if kind == "class"
                                            else arm_declared[subj_name])
            guard = None
            if case.guard is not None:
                _witness("match.guard_arm")
                guard = _lower_expr(case.guard, lc)
            body = _lower_stmts(case.body, lc, arm_declared)
        finally:
            lc.narrow = saved
        return THIRMatchArmEntry(
            body=body, loc=case.loc, binding=binding, guard=guard,
            variant_index=idx if kind == "class" else None,
            case_alias=alias if kind == "class" else None)

    arms: list[THIRMatchArm] = []
    for idx in range(n):
        if idx in default_indices:
            continue
        entries_src = type_arms[idx]
        if not entries_src:
            continue
        # _gen_match_guarded_union's needs_extraction: a class entry that
        # narrows (type_facts) or `as`-binds (keywords are gated out).
        needs_extraction = any(
            k == "class" and (bool(case.type_facts) or b is not None)
            for case, k, _m, b in entries_src)
        alias = f"__case_{idx}" if needs_extraction else None
        if alias is not None:
            _witness("match.union_alias")
        arms.append(THIRMatchArm(
            labels=(str(idx),),
            entries=tuple(lower_entry(case, k, m, b, alias, idx)
                          for case, k, m, b in entries_src)))
    if default_indices and default_src:
        _witness("match.union_default")
        arms.append(THIRMatchArm(
            labels=(),
            entries=tuple(lower_entry(case, k, m, b, None, None)
                          for case, k, m, b in default_src)))
    emit_unreachable = (stmt.is_exhaustive and bool(stmt.cases)
                        and all(stmts_terminate(c.body) for c in stmt.cases))
    if emit_unreachable:
        _witness("match.unreachable_tail")
    return THIRMatch(
        strategy="guarded_union",
        subject=_lower_expr(stmt.subject, lc),
        subject_ref=True,
        arms=tuple(arms),
        hoist_decls=tuple(hoist_decls),
        is_exhaustive=stmt.is_exhaustive,
        emit_unreachable=emit_unreachable,
        synthetic_default=False,
        is_ptr_variant=is_ptr_variant_union(u),
        loc=loc,
    )

def _lower_raise(stmt: TpyRaise, lc: _LowerCtx, declared: dict[str, TpyType],
                 loc) -> THIRRaise:
    """Lower a bare re-raise or the ctor-form raise (see `THIRRaise`). Ctor
    args lower against the resolved `__init__`'s param slots through the
    shared call-arg machinery -- the gate admitted only scalar/str value
    slots, where `_gen_record_ctor_args` and `gen_call_arg` coincide."""
    if stmt.exception_type is None:
        _witness("raise.bare")
        return THIRRaise(loc=loc)
    _witness("raise.ctor")
    cpp = error_return_to_cpp(stmt.exception_type,
                              lc.analyzer.ctx.module_name,
                              lc.analyzer.registry)
    init = stmt.resolved_ctor_init
    params = init.params if init else []
    return THIRRaise(
        cpp_type=cpp,
        args=tuple(_lower_call_arg(a, p.type, lc)
                   for a, p in zip(stmt.args, params)),
        loc=loc,
    )

def _lower_with(stmt: TpyWith, lc: _LowerCtx, declared: dict[str, TpyType],
                loc) -> THIRWith:
    """Lower a sync `with` -- the item facts mirror `_gen_with`'s header arms
    (see `THIRWith` for the emit shape). Targets are declared at the enclosing
    C++ scope and stay visible after the block (Python scoping), so they extend
    the CALLER's `declared` dict -- unlike branch bodies, which lower over a
    copy. The body lowers under a narrowing-scope snapshot, mirroring the AST's
    `narrowed_vars` / `declared_persistent_aliases` restore around the try
    body. `body_terminates` calls the same `stmts_terminate` the AST reads, so
    the per-layer normal-exit elision folds identically at emit."""
    items: list[THIRWithItem] = []
    for item in stmt.items:
        ctx = item.context_expr
        arm_et = _with_target_arm(item, declared, lc.prescan, lc.analyzer)
        assert arm_et is not None, "ineligible with-item reached lowering"
        arm, et = arm_et
        deref = (item.manager_borrowed and isinstance(ctx, TpyName)
                 and ctx.name in lc.pointers)
        _witness("with.manager_borrowed" if item.manager_borrowed
                 else "with.manager_owned")
        if deref:
            _witness("with.manager_deref")
        _witness({WithTargetArm.NONE: "with.no_target",
                  WithTargetArm.VALUE: "with.as_value",
                  WithTargetArm.REF: "with.as_ref"}[arm])
        if item.exit_can_suppress:
            _witness("with.suppress")
        if item.exit_takes_exc_val:
            _witness("with.exc_val")
        if not (item.exit_can_suppress or item.exit_takes_exc_val):
            _witness("with.cleanup_only")
        items.append(THIRWithItem(
            ctx_expr=_lower_expr(ctx, lc),
            manager_borrowed=item.manager_borrowed,
            deref_manager=deref,
            target=item.target,
            target_arm=arm,
            can_suppress=item.exit_can_suppress,
            takes_exc_val=item.exit_takes_exc_val,
        ))
        if item.target is not None:
            declared[item.target] = et
    if len(stmt.items) > 1:
        _witness("with.multi")
    return THIRWith(
        items=tuple(items),
        body=_lower_scoped_stmts(stmt.body, lc, dict(declared)),
        body_terminates=stmts_terminate(stmt.body),
        loc=loc,
    )

def _lower_print_arg(a: TpyExpr, lc: _LowerCtx) -> THIRPrintArg:
    """Lower one print arg + tag its `std::cout <<` wrapper form. A str literal
    lowers to a THIRStrLiteral (RAW: emitted via cpp_string_literal_expr); an
    eligible scalar lowers normally with its type-derived form."""
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
    # resolve_int_literals: an IntLiteral-typed arg (a literal-seeded container's
    # loop var / pop result) must derive its stream form from the resolved type.
    arg_type = resolve_int_literals(
        unwrap_readonly(lc.analyzer.get_expr_type(a)),
        lc.analyzer.ctx.default_int_for_literal)
    return THIRPrintArg(_lower_expr(a, lc), _print_arg_form(arg_type))
