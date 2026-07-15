"""Resumable-body (async def) lowering: the leaf half of the seam.

The state-machine SKELETON -- CFG build, frame struct, case labels, region
replay, suspend/resume plumbing -- is shared machinery in `gen_async` (the
structural-emission precedent: signatures and record layout also stay on
the AST path). What routes through THIR is every LEAF the skeleton would
otherwise delegate to the AST statement/expression emitters: BB leaf
statements, Branch terminator conditions, ReturnT value renders, and the
sub-coro emplace arguments at each suspension.

`lower_resumable` walks the already-built CFG (cached on the function by
`gen_async._build_resumable_cfg`), lowers every leaf through the shared
statement/expression lowering plus the resumable-only rejects below, and
returns a `THIRResumableBody` keyed by id() of the AST nodes the skeleton
holds -- or None (with a `res.*` / composed `stmt.*` fallback reason) when
any leaf or frame feature falls outside the slice.

Foundation slice (deliberately tight; the fan-out cells widen it):
free `async def` only, value-scalar/str/bytes/F1-record params,
value-scalar/str/bytes locals plus owning frame_slot locals,
value-scalar returns, INLINE await payloads on plain or module-qualified
async-def calls (arg slots share the param families) and ERASED/BORROWED
awaitables as whole-operand renders, try/except/finally regions
(catch wraps and sub resets are skeleton; handler bodies and finally
bodies -- helper- or CFG-based -- are ordinary BB leaves), with regions
over F1-record managers (the manager bind is the one leaf render;
__exit__ replay is skeleton),
no narrowed resume points, no returns inside leaf compounds (a ReturnT
terminator's scaffolding is skeleton; a return nested in a leaf would
need the async-return render inside THIR emit -- a cell).
"""

from __future__ import annotations

from ..fallback import ThirUnsupported, begin_stmt, note, stmt_reject_reason
from ..faces import witness as _witness
from ..nodes import (
    THIRAssign,
    THIRExpr,
    THIRFrameSlotWrite,
    THIRName,
    THIRResumableBody,
    THIRStmt,
)
from ...parse.nodes import (
    TpyAwait,
    TpyCall,
    TpyExpr,
    TpyForEach,
    TpyFunction,
    TpyMethodCall,
    TpyName,
    TpyReturn,
    TpyStrLiteral,
    TpyStmt,
    TpyVarDecl,
)
from ...typesys import (
    IntLiteralType,
    NominalType,
    OptionalType,
    TpyType,
    UnionType,
    VoidType,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp import resumable_cfg as rcfg
from ...codegen_cpp.forms import is_plain_nonvalue
from .checks import _ctor_shape_ok, _record_rvalue_source_shape
from .context import _ExprResultUse, _ExprUse, _LowerCtx
from .expressions import (
    _lower_call_arg,
    _lower_expr,
    _lower_truthy,
    _slot_literal_retype,
)
from .functions import _check_callable_structure, method_self_type_by_name
from .predicates import (
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _f1_record,
    _f1_tuple,
    _optional_ptr_borrow,
    _peel_stale_view_owned_coerce,
    _resolved_bytes_value,
    _resolved_str_value,
    _value_tuple,
)
from .statements import (
    _handler_binding_type,
    _lower_stmt,
    _var_decl_type,
)


def _res_value_ok(t: 'TpyType | None', analyzer) -> bool:
    """Foundation value families: plain value scalars, Char, and enums.

    A resumable param is captured as a frame FIELD, and only these families
    capture and read
    with the same spelling as a sync param (str captures owned, records
    borrow, Own moves -- each a fan-out cell of its own)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return bool(_eligible_scalar(t) or _eligible_char(t)
                or _eligible_enum(t, analyzer) is not None)


def _res_param_ok(t: 'TpyType | None', analyzer) -> bool:
    """Frame-PARAM families (R5c-param). Value scalars plus F1-record params:
    a record param captures as a `Record&` reference frame field and reads
    bare with `.` member access -- exactly like a sync record param, so the
    leaf needs no coro-specific form. str/bytes params capture OWNED
    (`std::string` / `std::vector<uint8_t>` frame fields, ctor-copied from
    the sync view param), but every leaf READ renders through the same
    form-agnostic helpers as the owned str/bytes locals R1a already routes
    (`__len__` / `bytes_getitem` / bare name), so they share that slice.

    Own[T] params (`_f1_record` unwraps the Own inner, so an `Own[F1-record]`
    payload already reads through the record branch below; the capture
    `b(std::move(b_))` is skeleton) and pointer-repr `Optional[F1-record]`
    params (`p: P | None` -> a `P*` frame field: `p != nullptr` predicates
    and `p->n` arrow reads route through `lc.pointers`, seeded from the
    params) are also admitted. Borrow-form and value tuple params ride too:
    a `std::tuple<..., T*>` / `std::tuple<...>` frame field reads bare with
    `std::get<N>(t)` (pointer-repr elements arrow, value elements bare),
    matching the sync tuple-subscript rows. Union (non-pointer-repr),
    static-protocol and generic params stay their own rungs."""
    if _res_value_ok(t, analyzer):
        return True
    if (_resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None):
        return True
    if _optional_ptr_borrow(t, analyzer) is not None:
        return True
    if (_f1_tuple(t, analyzer) is not None
            or _value_tuple(t, analyzer) is not None):
        return True
    unwrapped = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                 if isinstance(t, TpyType) else None)
    if unwrapped is not None and _f1_record(unwrapped, analyzer):
        return True
    # A pointer-repr union param's frame field is the SAME pointer-variant
    # shape as the sync param (`std::variant<A*, B*>`), so reads, isinstance
    # narrowing, and pass-through args take the sync union rows unchanged.
    return bool(isinstance(unwrapped, UnionType)
                and unwrapped.uses_pointer_repr())


def _res_local_ok(t: 'TpyType | None', analyzer) -> bool:
    """Frame-LOCAL families -- broader than `_res_value_ok` (params).

    R1a adds str/bytes locals: a str local's frame field is a bare
    `std::string` / `std::string_view` (owned / view, sema-resolved) with
    bare reads/writes -- the same shapes THIR's ported str slice already
    emits, no frame_slot machinery. Records / Optionals (frame_slot) and
    pointer-alias / tuple locals stay their own rungs (R1c)."""
    return bool(_res_value_ok(t, analyzer)
                or _resolved_str_value(t, analyzer) is not None
                or _resolved_bytes_value(t, analyzer) is not None)


def _region_reject(region: 'rcfg.Region') -> 'str | None':
    """Region-stack admission (R6). Every region kind is transparent to the
    leaf seam: catch headers, sub-future resets, region replay, the finally
    pending-return / rethrow dance and the with __exit__ calls are all
    skeleton. try/except handler bodies and finally bodies (helper- or
    CFG-based) are ordinary BB leaves; a WithRegion's only user render (the
    manager bind) is gated per WithEnter. Nothing left to reject at the
    region-stack level -- the residual gating lives on the pseudo-stmts and
    payload kinds (async-with, async-for) that carry their own renders."""
    return None


def _loop_elem_type(stmt: 'TpyForEach', analyzer) -> 'TpyType | None':
    et = stmt.elem_type
    if isinstance(et, IntLiteralType):
        return analyzer.ctx.default_int_type
    return et if isinstance(et, TpyType) else None


def _for_advance_reject(t: 'rcfg.AsyncForAdvance', analyzer) -> 'str | None':
    """Sync loop-advance admission (R3). The skeleton binds the loop var per
    strategy; only a value-scalar / str / bytes element binds bare (a plain
    frame field the leaf body reads with the ported shapes). Pointer-form,
    frame_slot and tuple loop vars imply non-value element reads the leaf
    can't mirror yet -- reject."""
    if not _res_local_ok(_loop_elem_type(t.stmt, analyzer), analyzer):
        return "res.loop_var"
    return None


def _for_iterable_narrowed_optional(iterable, declared, analyzer) -> bool:
    """A narrowed value-Optional iterable (`str|None`/`bytes|None` proven
    non-None) stays `std::optional<V>` in the frame; the skeleton's
    `_maybe_unwrap_narrowed_optional` renders `(*v)`, which the bare leaf
    render does not reproduce. Detect the frame-field case (declared storage
    is Optional, sema type narrowed non-Optional) and reject -- a named
    rung."""
    if not isinstance(iterable, TpyName):
        return False
    dt = declared.get(iterable.name)
    if dt is None:
        return False
    stored = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
    if not isinstance(stored, OptionalType):
        return False
    cur = analyzer.get_expr_type(iterable)
    cur = unwrap_readonly(cur) if cur is not None else None
    return not isinstance(cur, OptionalType)


def _lower_for_iter_setup(stmt: 'rcfg.AsyncForIterSetup', func, lc,
                          declared: dict[str, TpyType], analyzer,
                          region_exprs: dict) -> None:
    """Lower the user renders of a sync for-loop's iter setup into
    region_exprs. The skeleton's strategy (from the prescan) decides the
    render shape: range lowers each bound (== `gen_range_args`); every other
    strategy lowers the whole iterable ONCE (`gen_expr(iterable_expr)`, reused
    by the advance's re-render when no `__for_src` field). Rejects a
    narrowed-optional iterable (the skeleton's `(*v)` unwrap is not
    mirrored)."""
    it = stmt.iterable_expr
    info = rcfg.resumable_state(func).for_info_by_uid.get(stmt.uid)
    strat = info.strategy if info else "iter_next"
    if strat == "range":
        fi = getattr(it, "resolved_function_info", None)
        if fi is None:
            raise ThirUnsupported("res.for_range_shape")
        for i, arg in enumerate(it.args):
            region_exprs[id(arg)] = _lower_call_arg(
                arg, fi.params[i].type, lc, declared)
        return
    if _for_iterable_narrowed_optional(it, declared, analyzer):
        raise ThirUnsupported("res.for_narrowed_optional")
    region_exprs[id(it)] = _lower_expr(it, lc, declared)


def _with_enter_reject(stmt: 'rcfg.WithEnter | rcfg.AsyncWithSetup', analyzer,
                       declared: dict[str, TpyType]) -> 'str | None':
    """WithEnter / AsyncWithSetup admission (R6-with / R5-async-with). The
    bind's emplace / &(..) wrap and the __enter__ / __aenter__ yields are
    skeleton; the manager expression is the one leaf render, so admit exactly
    the shapes whose lowered render matches the AST's inline `gen_expr` -- the
    same manager families the sync `_lower_with` gates (borrowed F1-record
    lvalue name; owned F1-record ctor / record-rvalue call). The `as`-target
    bind is a skeleton frame write; its storage already passed the body-wide
    local gate."""
    item = stmt.item
    ctx = item.context_expr
    if item.manager_borrowed:
        ok = (isinstance(ctx, TpyName) and ctx.name in declared
              and _f1_record(declared[ctx.name], analyzer))
    else:
        ok = (isinstance(ctx, TpyCall)
              and _f1_record(analyzer.get_expr_type(ctx), analyzer)
              and (_ctor_shape_ok(ctx, analyzer)
                   or _record_rvalue_source_shape(ctx, analyzer)))
    if not ok:
        return "res.with_manager"
    if item.target is not None and not isinstance(item.enter_type, TpyType):
        return "res.with_target"
    return None


def _payload_reject(payload: 'rcfg.SuspensionPayload', analyzer) -> str | None:
    """Await-payload gate (async bodies). INLINE (statically-known async def,
    incl. bound methods -- R5b) is admitted; the receiver/args are lowered in
    the Yield loop. ERASED/BORROWED awaitables (a Task/Future value: an
    `asyncio.sleep(...)` rvalue, a Task-typed local, an already-pointer
    source) are admitted as a whole-operand render: the skeleton keeps its
    emplace(std::move(..)) / &(..) / .get() wrap and the leaf loop lowers
    `operand_expr` through the shared expression lowering (a reject there
    composes the fallback reason). Deferred: generic awaited callees,
    kwargs, and non-value INLINE arg slots (R5c)."""
    if isinstance(payload, rcfg.YieldPayload):
        return "res.generator_shape"
    if payload.prebuilt_slot is not None:
        return "res.await_prebuilt"
    if payload.async_with_kind is not None or payload.async_for_uid is not None:
        # Synthetic suspensions (async-with __aenter__/__aexit__, async-for
        # __anext__): the skeleton synthesizes the receiver/args from the CM /
        # iterator frame slot (`(*__with_ctx_n)` / `*__for_itr_uid`) -- no
        # arg lowering. The bind_target (as-var / loop var) registers like a
        # VARDECL bind and reads through the ordinary leaf forms.
        return None
    if getattr(payload.await_node, "awaited_inferred_type_args", None):
        # Generic awaited callee: the sub-coro frame is a template
        # instantiation (M7) -- template-arg capture is a cell.
        return "res.await_generic"
    if payload.mode is not rcfg.AwaitMode.INLINE:
        return None  # whole-operand render; gated at leaf lowering
    call = payload.operand_expr
    if not isinstance(call, (TpyCall, TpyMethodCall)):
        return "res.await_operand"
    if getattr(call, "kwargs", None) or getattr(call, "double_star_unpack",
                                                None):
        return "res.await_kwargs"
    fi = call.resolved_function_info
    if fi is None or len(call.args) > len(fi.params):
        return "res.await_callee"
    for i in range(len(call.args)):
        if not _res_param_ok(fi.params[i].type, analyzer):
            # Slots beyond the param families (Own / optional-ptr / protocol
            # adapter / union lift) trigger the emplace coercion ladder and,
            # for static-protocol params, the two-phase decltype capture
            # render -- R5c. str/bytes/F1-record slots take the same
            # `_lower_call_arg` rows as a sync call (the emplace ctor param
            # is the sync borrow shape: span / string_view / Record&), so
            # they share the DIRECT-param families; a bad arg SHAPE still
            # rejects inside the arg lowering.
            return "res.await_param_type"
    return None


def lower_resumable(func: TpyFunction, analyzer, render_type,
                    cfg: 'rcfg.CFG',
                    record_name: 'str | None' = None,
                    render_type_stored=None,
                    pointer_aliases: 'set[str] | None' = None,
                    ) -> 'THIRResumableBody | None':
    """Lower a resumable body, falling back cleanly on a lowering reject."""
    try:
        return _lower_resumable(
            func, analyzer, render_type, cfg,
            record_name=record_name,
            render_type_stored=render_type_stored,
            pointer_aliases=pointer_aliases,
        )
    except ThirUnsupported as ex:
        return _reject(ex.reason)


def _lower_resumable(func: TpyFunction, analyzer, render_type,
                     cfg: 'rcfg.CFG',
                     record_name: 'str | None' = None,
                     render_type_stored=None,
                     pointer_aliases: 'set[str] | None' = None,
                     ) -> 'THIRResumableBody | None':
    """Lower one resumable body's leaves, or None if outside the slice.

    `record_name` is the owning record for a method coro (R2): its frame
    captures the receiver as `__self: Record&`, so self-reads render
    `__self` / `__self.x` (`.` access) instead of `this` / `this->x`.

    A generator (`yield`) shares the state-machine skeleton with `async`
    (R4); its leaf seam is minimal -- the yield-value render -- since a
    generator returns only bare `return` (StopIteration, skeleton-only).

    `pointer_aliases` is the skeleton's borrow-alias classification (R1c): a
    plain-nonvalue local NOT in it is an owning frame_slot (`.emplace()` /
    `(*name)`); one in it is a `T*` alias (a later cell)."""
    is_generator = bool(func.is_generator)
    pointer_aliases = pointer_aliases or set()
    # R2: instance-method coros route with a `__self` receiver. Static /
    # property / dunder-operator kinds keep their own dispatch shapes;
    # defer them. A generator METHOD composes the self machinery (R2) with
    # the yield seam (R4) -- both halves are callable-kind-orthogonal, so no
    # generator carve-out is needed here.
    self_type: 'TpyType | None' = None
    if record_name is not None or func.is_method:
        if not func.is_method:
            return _reject("res.method")
        if func.is_staticmethod:
            return _reject("res.static_method")
        if func.is_property_getter or func.is_property_setter:
            return _reject("res.property")
        if record_name is None:
            return _reject("res.method")
        self_type = method_self_type_by_name(record_name, analyzer)
        if self_type is None:
            return _reject("res.method")
    try:
        _check_callable_structure(
            func, analyzer, self_type, allow_resumable=True)
    except ThirUnsupported as ex:
        return _reject(ex.reason)
    if func.type_params:
        # Generic async def: the frame is a template (M7); a cell.
        return _reject("res.generic")
    if isinstance(self_type, NominalType) and self_type.type_args:
        # Generic-record method coro: the frame folds the record's [T, ...]
        # (template frame) -- a cell.
        return _reject("res.generic_record")
    for _pname, ptype in func.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _res_param_ok(pt, analyzer):
            return _reject("res.param_type")
    if is_generator:
        # The yielded element type gates a generator (its `return_type` is
        # `Iterator[T]`, not a value slot); the value-scalar slice yields
        # only value scalars (a non-value yield needs the borrow-form slot
        # bridge in gen_yield_value -- a cell).
        yt = func.generator_yield_type
        if not _res_value_ok(yt if isinstance(yt, TpyType) else None, analyzer):
            return _reject("res.yield_type")
    else:
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        rt_inner = (unwrap_readonly(unwrap_send_sync(rt))
                    if rt is not None else None)
        # str/bytes returns stay deferred: `_async_return_value_cpp` wraps a
        # view-form value into the owned slot at CODEGEN time
        # (`_view_source_to_owned` -- `std::string(tag)`), keyed on the deep
        # `_is_str_view_source` classifier, so the leaf value render is NOT
        # position-blind for them. Their rung needs that view-source fact
        # threaded into lowering (not re-derived).
        if not (rt is None or isinstance(rt_inner, VoidType)
                or _res_value_ok(rt, analyzer)):
            return _reject("res.return_type")
    if func.forwarded_locals:
        return _reject("res.forwarded_local")
    # Classify each non-value local. Value scalars / str / bytes are bare
    # fields (R1a); an owning plain-nonvalue local (record / list / dict /
    # set, NOT a borrow alias) is a frame_slot (R1c); everything else
    # (pointer-alias, Optional, tuple, union) is a later cell.
    #
    # A non-value FOR-LOOP VAR also lands in generator_locals, but the AST
    # emits it as a shadowing C++ local inside the loop (`const auto& it =
    # *__beg_N;`, frame_field_shadows) and only post-loop reads peel the
    # optional-storage frame field -- neither the bare-field nor the
    # `(*name)` frame_slot read form. Reject it (its rung needs the
    # shadow/post-loop duality mirrored).
    frame_slots: set[str] = set()
    for lname, ltype in (func.generator_locals or []):
        if _res_local_ok(ltype, analyzer):
            continue  # value / str / bytes -- bare field
        lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
              if isinstance(ltype, TpyType) else None)
        # For-loop targets bind C++ locals that shadow their same-named
        # frame field; the for-each lowering masks them out of frame_slots
        # (the register_frame_field_shadow mirror), so they classify like
        # any other local here rather than rejecting.
        if (lt is not None and is_plain_nonvalue(lt)
                and lname not in pointer_aliases):
            frame_slots.add(lname)
            continue
        return _reject("res.local_storage")
    # Helper-based finally bodies (cfg.finally_helpers) route: their
    # statements lower into the leaves table below and emit through the seam
    # from gen_coro_finally_top_def. A CFG-based finally (its body suspends)
    # is not in this list -- its BBs are ordinary cfg.blocks members lowered
    # by the main block loop, under a transparent FinallyRegion stack.
    # `self` is not a frame-field NAME write target (self-field writes lower
    # through THIRFieldAccess), so it stays out of frame_fields.
    frame_fields = set(n for n, _t in func.params) | {
        lname for lname, _lt in (func.generator_locals or [])}
    gen_local_types = {n: t for n, t in (func.generator_locals or [])}

    # -- CFG shape classification ----------------------------------------
    # Handler `as`-bindings are catch-bound C++ locals (sema unbinds them
    # after the handler, so they are never frame fields); their reads only
    # occur inside handler BBs, so registering them function-wide is safe.
    # A name collision (params / frame fields / a sibling handler's binding
    # of another type) would mistype reads, so it rejects.
    handler_bindings: dict[str, TpyType] = {}
    saw_try_region = False
    saw_with_region = False
    saw_sync_loop = False
    saw_async_loop = False
    saw_async_with = False
    for bb in cfg.blocks.values():
        for region in bb.region_stack:
            reason = _region_reject(region)
            if reason is not None:
                return _reject(reason)
            if isinstance(region, rcfg.WithRegion):
                saw_with_region = True
            else:
                saw_try_region = True
            if (isinstance(region, rcfg.ExceptRegion)
                    and region.handler.binding is not None):
                bname = region.handler.binding
                btype = _handler_binding_type(region.handler, analyzer)
                if (btype is None or bname in frame_fields
                        or bname == "self"
                        or handler_bindings.get(bname, btype) != btype):
                    return _reject("res.handler_binding")
                handler_bindings[bname] = btype
        if bb.entry_narrowings:
            return _reject("res.narrowed_resume")
        # AsyncForIterSetup (sync + async), AsyncWithSetup and
        # AsyncFinallyExit all route: their only user render (the iterable /
        # manager expression) is lowered in pass 2; everything else (counters,
        # __aiter__/__anext__, pending-return replay) is skeleton.
        t = bb.terminator
        if isinstance(t, rcfg.AsyncForAdvance):
            # Sync loop advance: value/str/bytes loop var only (the skeleton
            # binds non-value forms -- pointer / frame_slot / tuple -- with
            # renders the leaf body reads can't mirror yet).
            reason = _for_advance_reject(t, analyzer)
            if reason is not None:
                return _reject(reason)
            saw_sync_loop = True
        if isinstance(t, rcfg.MatchDispatch):
            return _reject("res.match")
        if isinstance(t, rcfg.Yield):
            payload = t.payload
            if is_generator:
                # A generator body's suspensions are YieldPayloads (no
                # sub-coro). An async body must never carry one (async
                # generators are sema-rejected), and vice versa -- so the
                # payload kind must match the callable kind.
                if not isinstance(payload, rcfg.YieldPayload):
                    return _reject("res.mixed_payload")
            else:
                reason = _payload_reject(payload, analyzer)
                if reason is not None:
                    return _reject(reason)
                if payload.async_for_uid is not None:
                    saw_async_loop = True
                    if payload.bind_target is not None:
                        # async-for loop var: same value/str/bytes slice as the
                        # sync loop var (the skeleton's ASSIGN bind is a plain
                        # frame write only for those; non-value uses .emplace
                        # on an optional field).
                        if not _res_local_ok(
                                gen_local_types.get(payload.bind_target),
                                analyzer):
                            return _reject("res.loop_var")
                if payload.async_with_kind is not None:
                    saw_async_with = True

    # -- Leaf lowering ----------------------------------------------------
    has_self = self_type is not None
    lc = _LowerCtx(func, analyzer, render_type,
                   render_type_stored=render_type_stored,
                   self_receiver="self" if has_self else None,
                   record_name=record_name if has_self else None,
                   self_cpp="__self", self_is_pointer=False)
    # R1c: frame_slot local reads render `(*name)` (THIRName.deref) and writes
    # `name.emplace(value)` (THIRFrameSlotWrite).
    lc.frame_slots = frame_slots
    lc.resumable_leaf_mode = True
    declared: dict[str, TpyType] = {
        n: unwrap_ref_type(t) for n, t in func.params
        if isinstance(t, TpyType)}
    if has_self:
        declared["self"] = self_type  # the record receiver, a field source
    declared.update(handler_bindings)

    # Pass 1 -- scope registration. Each frame-field decl (and each await
    # bind on a fresh name) registers its FIRST decl's type, in BB-id order.
    # Registering while lowering is not enough: the CFG builder allocates
    # join BBs BEFORE the body BBs they join (try bodies, if arms past a
    # suspension), so a leaf in the join can read a name whose decl lives in
    # a higher-id BB. The registered type stays first-decl-keyed -- the same
    # decl-site type (`_var_decl_type`) the walk-order registration stored,
    # never the frame's resolved local type, so the frame arm's position-
    # blind decl render is unchanged. Scope setup consumed immediately by
    # pass 2; admission itself stays inside the lowering arms.
    for bb_id in sorted(cfg.blocks):
        bb = cfg.blocks[bb_id]
        for stmt in bb.stmts:
            if (isinstance(stmt, TpyVarDecl) and stmt.init is not None
                    and stmt.name in frame_fields
                    and stmt.name not in declared):
                declared[stmt.name] = _var_decl_type(stmt, analyzer)
            elif (isinstance(stmt, (rcfg.WithEnter, rcfg.AsyncWithSetup))
                    and stmt.item.target is not None
                    and stmt.item.target not in declared
                    and isinstance(stmt.item.enter_type, TpyType)):
                # The `as`-target bind (sync or async with) is a skeleton
                # frame write; reads classify against the sema enter type,
                # like the AST frame ctx's `var_types[target] = enter_type`.
                declared[stmt.item.target] = unwrap_readonly(
                    unwrap_ref_type(unwrap_send_sync(stmt.item.enter_type)))
        t = bb.terminator
        if isinstance(t, rcfg.Yield) and isinstance(t.payload,
                                                    rcfg.AwaitPayload):
            # A VARDECL-kind bind (`x = await f()` on a fresh name) or an
            # `async for y in ...` loop var writes the frame field in the
            # skeleton's resume step; register it so later leaves' reads gate
            # and lower with its type.
            payload = t.payload
            if (payload.bind_target is not None
                    and payload.bind_target not in declared):
                if payload.async_for_uid is not None:
                    lt = gen_local_types.get(payload.bind_target)
                    if lt is not None:
                        declared[payload.bind_target] = unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(lt)))
                elif isinstance(payload.host_stmt, TpyVarDecl):
                    declared[payload.bind_target] = _var_decl_type(
                        payload.host_stmt, analyzer)
        elif isinstance(t, rcfg.AsyncForAdvance):
            # The sync loop var is a bare frame field bound by the skeleton's
            # advance step; register it (elem type) so body leaves read it.
            lv = t.stmt.var
            if lv is not None and lv not in declared:
                et = _loop_elem_type(t.stmt, analyzer)
                if et is not None:
                    declared[lv] = unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(et)))

    leaves: dict[int, THIRStmt] = {}
    conds: dict[int, THIRExpr] = {}
    await_args: dict[int, tuple[THIRExpr, ...]] = {}
    return_values: dict[int, THIRExpr] = {}
    yield_values: dict[int, THIRExpr] = {}
    suspend_exprs: dict[int, THIRExpr] = {}
    region_exprs: dict[int, THIRExpr] = {}

    def _lower_leaf(stmt: TpyStmt) -> THIRStmt:
        if isinstance(stmt, TpyVarDecl) and stmt.name in frame_fields:
            begin_stmt()
            if stmt.init is None:
                raise ThirUnsupported("stmt.decl.no_init")
            # A view-resolved frame field fed a stale view->owned coerce
            # renders the source bare (the AST threads the binding type into
            # gen_expr's stale-coerce arm) -- same peel as the sync decl.
            init = _peel_stale_view_owned_coerce(
                stmt.init, declared[stmt.name], analyzer)
            value = _lower_expr(init, lc, declared)
            if stmt.name in frame_slots:
                # R1c: a frame_slot local write is `name.emplace(value)`
                # (first init and reassign alike -- emplace destroys any prior
                # payload). The value is the owning source (a record ctor /
                # container literal / by-value call). cpp_type reproduces the
                # AST's typed_brace_init prefix for a brace-init value.
                _witness("res.frame_slot_write")
                slot_t = unwrap_readonly(unwrap_ref_type(declared[stmt.name]))
                return THIRFrameSlotWrite(
                    name=stmt.name, value=value,
                    cpp_type=lc.render_type(slot_t), loc=stmt.loc,
                    no_source_comment=getattr(stmt, "no_source_comment", False))
            # Frame-field write (`name = expr` -- first decl and reassign
            # alike): the AST frame arm emits an assignment to the struct
            # member with a POSITION-BLIND init render (plain gen_expr; a
            # BigInt literal stays `0`, never the sync arms' target-typed
            # `::tpy::BigInt(0)`). No char/None reject guards this arm: a
            # reassign or multi-char char literal, and None at a scalar/Char
            # slot, are sema type errors that never reach lowering; a single-
            # char `c: Char = 'a'` first decl does reach it and renders
            # position-blind identically to the AST frame arm (so THIR
            # mirrors it rather than diverging).
            _witness("res.decl_assign")
            return THIRAssign(
                target=THIRName(name=stmt.name,
                                result_type=declared[stmt.name],
                                loc=stmt.loc),
                value=value, loc=stmt.loc,
                no_source_comment=getattr(stmt, "no_source_comment", False))
        return _lower_stmt(stmt, lc, declared)

    for bb_id in sorted(cfg.blocks):
        bb = cfg.blocks[bb_id]
        for stmt in bb.stmts:
            if isinstance(stmt, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
                # Sync + async with: only the manager expression renders; the
                # bind wrap / __enter__ / __aenter__ yields are skeleton.
                begin_stmt()
                reason = _with_enter_reject(stmt, analyzer, declared)
                if reason is not None:
                    raise ThirUnsupported(reason)
                region_exprs[id(stmt.item.context_expr)] = _lower_expr(
                    stmt.item.context_expr, lc, declared)
                _witness("res.with_ctx")
                continue
            if isinstance(stmt, rcfg.AsyncForIterSetup):
                # Sync loop setup: the skeleton owns the iteration strategy
                # (range counters / begin_end / next / iter_next); the only
                # user render is the iterable (or, for range, each bound). The
                # async flavor renders the whole iterable once (its `__aiter__`
                # call is skeleton).
                begin_stmt()
                if stmt.is_async:
                    region_exprs[id(stmt.iterable_expr)] = _lower_expr(
                        stmt.iterable_expr, lc, declared)
                else:
                    _lower_for_iter_setup(
                        stmt, func, lc, declared, analyzer, region_exprs)
                _witness("res.for_iter_setup")
                continue
            if isinstance(stmt, rcfg.AsyncFinallyExit):
                # Pure skeleton (pending-return replay + exc rethrow); no leaf.
                continue
            leaves[id(stmt)] = _lower_leaf(stmt)
        t = bb.terminator
        if isinstance(t, rcfg.ReturnT):
            ret = t.return_stmt
            if ret.value is None:
                continue  # void scaffolding is skeleton-only
            if isinstance(ret.value, TpyAwait):
                # RETURN-kind await: `_emit_resume_core` fully emits the
                # return from the polled result (`__ret<i>`); the ReturnT
                # on the resume BB is never consumed by the emitter.
                continue
            begin_stmt()
            if (lc.prescan.ret_char
                    and isinstance(ret.value, TpyStrLiteral)):
                note(stmt_reject_reason(ret))
                return None
            # POSITION-BLIND value render: `_make_async_return` binds the
            # value to a `<ret_cpp> __tpy_async_ret = <value>;` local (whose
            # decl type supplies the conversion) and only then wraps it in
            # Poll::ready -- so a literal at a wider slot stays bare `42`,
            # NOT the sync-return arm's target-typed `::tpy::BigInt(42)`.
            # Lower the value directly (== the AST's `gen_expr_deref`),
            # bypassing _lower_stmt's return-coercion arm.
            return_values[id(ret)] = _lower_expr(ret.value, lc, declared)
            _witness("res.return_value")
        elif isinstance(t, rcfg.RaiseT):
            leaves[id(t.raise_stmt)] = _lower_leaf(t.raise_stmt)
        elif isinstance(t, rcfg.Branch):
            try:
                conds[id(t.cond)] = _lower_truthy(t.cond, lc, declared)
            except ThirUnsupported:
                return _reject("res.cond")
            _witness("res.branch_cond")
        elif isinstance(t, rcfg.Yield) and is_generator:
            # Generator suspension: the skeleton emits `__state = S_RESUME_i;
            # return <value>;`. The AST's `gen_yield_value` threads the yield
            # type into the render (`gen_expr(value, yield_type)`): for the
            # value-scalar slice that only shows on an int/float literal
            # value (a widened literal carries a sema-baked coerce, but a
            # BigInt-slot literal renders the target-typed ctor wrap), so
            # mirror it with the shared slot-literal retype. A char-typed
            # str-literal yield would need the char-targeted render --
            # reject, like the return arm. A bare `yield` (no value) never
            # arises for a value-scalar yield type.
            ys = t.payload.yield_stmt
            if ys is None or ys.value is None:
                return _reject("res.bare_yield")
            begin_stmt()
            yt = func.generator_yield_type
            yt_bare = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(yt)))
                       if isinstance(yt, TpyType) else None)
            if _eligible_char(yt_bare) and isinstance(ys.value, TpyStrLiteral):
                note(stmt_reject_reason(ys))
                return None
            yield_values[id(ys)] = _slot_literal_retype(
                _lower_expr(ys.value, lc, declared), yt, lc)
            _witness("res.yield_value")
        elif isinstance(t, rcfg.Yield):
            payload = t.payload
            operand = payload.operand_expr
            if (payload.async_with_kind is not None
                    or payload.async_for_uid is not None):
                # Synthetic suspension: the skeleton synthesizes the
                # __aenter__/__aexit__/__anext__ call from the frame slot; no
                # operand args to lower (the iterable/manager render was
                # lowered at the setup pseudo-stmt).
                pass
            elif payload.mode is not rcfg.AwaitMode.INLINE:
                # ERASED/BORROWED: the whole operand renders as one
                # expression; the skeleton wraps it (emplace(std::move(..)),
                # &(..), .get(), already-pointer). The prebuilt-slot flavor
                # never reaches here (gated as res.await_prebuilt).
                begin_stmt()
                try:
                    suspend_exprs[id(operand)] = _lower_expr(
                        operand, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.SUSPEND))
                except ThirUnsupported:
                    raise ThirUnsupported("res.await_operand_shape") from None
                _witness("res.suspend_operand")
            else:
                fi = operand.resolved_function_info
                # A bound-method await (`await obj.method()`): the receiver
                # `(*obj)` is prepended as the __self ctor arg (R5b). Module /
                # free calls have no value receiver.
                if (isinstance(operand, TpyMethodCall)
                        and operand.user_module_call is None
                        and operand.builtin_module_call is None):
                    suspend_exprs[id(operand.obj)] = _lower_expr(operand.obj, lc, declared)
                    _witness("res.suspend_expr")
                lowered_args = []
                for i, a in enumerate(operand.args):
                    lowered_args.append(_lower_call_arg(a, fi.params[i].type, lc, declared))
                await_args[id(operand)] = tuple(lowered_args)
                if lowered_args:
                    _witness("res.await_args")

    # Helper-based finally bodies live outside cfg.blocks (a member fn per
    # try); lower their statements into the SAME leaves table, keyed by
    # id(stmt). `gen_coro_finally_top_def` emits them through the leaf seam.
    # A `return` inside a helper (async: Poll replay; generator: the
    # __finally_stop path) rejects via leaf-mode res.leaf_return, keeping the
    # value-render nuance out of the slice.
    for _helper_name, body_stmts in cfg.finally_helpers:
        for stmt in body_stmts:
            leaves[id(stmt)] = _lower_leaf(stmt)
        _witness("res.finally_helper")

    if saw_try_region:
        _witness("res.try_region")
    if saw_with_region:
        _witness("res.with_region")
    if saw_sync_loop:
        _witness("res.sync_loop")
    if saw_async_loop:
        _witness("res.async_loop")
    if saw_async_with:
        _witness("res.async_with")
    _witness("res.body")
    return THIRResumableBody(
        leaves=leaves, conds=conds, await_args=await_args,
        return_values=return_values, yield_values=yield_values,
        suspend_exprs=suspend_exprs, region_exprs=region_exprs)


def _reject(reason: str):
    note(reason)
    return None
