"""Resumable-body (async def) lowering: the leaf half of the seam.

The state-machine SKELETON -- CFG build, frame struct, case labels, region
replay, suspend/resume plumbing -- is shared machinery in `gen_async` (the
structural-emission precedent: signatures and record layout also stay on
the AST path). What routes through THIR is every LEAF the skeleton would
otherwise delegate to the AST statement/expression emitters: BB leaf
statements, Branch terminator conditions, ReturnT value renders, and the
sub-coro emplace arguments at each suspension.

`lower_resumable` walks the already-built CFG (cached on the function by
`gen_async._build_resumable_cfg`), gates every leaf through the shared
statement/expression gates plus the resumable-only rejects below, and
returns a `THIRResumableBody` keyed by id() of the AST nodes the skeleton
holds -- or None (with a `res.*` / composed `stmt.*` fallback reason) when
any leaf or frame feature falls outside the slice.

Foundation slice (deliberately tight; the fan-out cells widen it):
free `async def` only, value-scalar params/locals/returns, INLINE await
payloads on plain or module-qualified async-def calls with value-scalar
params, no regions (try/with/finally), no narrowed resume points, no
returns inside leaf compounds (a ReturnT terminator's scaffolding is
skeleton; a return nested in a leaf would need the async-return render
inside THIR emit -- a cell).
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
    TpyFunction,
    TpyMatch,
    TpyMethodCall,
    TpyNestedDef,
    TpyReturn,
    TpyStrLiteral,
    TpyStmt,
    TpyTry,
    TpyTupleUnpack,
    TpyVarDecl,
    TpyWith,
    stmts_have_any_return,
)
from ...typesys import (
    NominalType,
    TpyType,
    VoidType,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp import resumable_cfg as rcfg
from ...codegen_cpp.forms import is_plain_nonvalue
from .context import _ExprResultUse, _ExprUse, _LowerCtx
from .expr_gates import _condition_eligible
from .expressions import _lower_call_arg, _lower_expr
from .functions import _function_eligible, method_self_type_by_name
from .predicates import (
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _f1_record,
    _resolved_bytes_value,
    _resolved_str_value,
)
from .statements import (
    _lower_stmt,
    _var_decl_type,
)


def _res_value_ok(t: 'TpyType | None', analyzer) -> bool:
    """Foundation value families: plain value scalars, Char, and enums.

    Deliberately tighter than `_f1_param_eligible`: a resumable param is
    captured as a frame FIELD, and only these families capture and read
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
    leaf needs no coro-specific form. str/bytes params (captured OWNED, so
    their read form differs from the sync view param's BORROW) and Own /
    tuple / union / protocol params stay their own rungs."""
    if _res_value_ok(t, analyzer):
        return True
    unwrapped = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                 if isinstance(t, TpyType) else None)
    return bool(unwrapped is not None and _f1_record(unwrapped, analyzer))


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


def _leaf_shape_reject(stmt: TpyStmt, *, nested: bool = False) -> str | None:
    """Resumable-only shape rejects INSIDE a leaf statement, checked before
    the shared statement gate. Returns the `res.*` reason or None.

    A leaf's nested `return` needs the async-return scaffolding
    (`_make_async_return`), which THIR's plain `return` render does not
    reproduce; a NESTED name-write (every local is a frame field) would go
    through the shared assign arm's target-typed render while the AST frame
    arm renders position-blind (`total = 7;`, never `::tpy::BigInt(7)`) --
    top-of-BB writes get the position-blind render in the walk below, but
    inside a leaf compound that needs the frame fact threaded through the
    shared lowering (the R1 cell); tuple-unpack targets and
    `with`/`try`/`match` interact with frame-field storage forms the slice
    does not carry yet."""
    if stmts_have_any_return([stmt]):
        return "res.leaf_return"
    if nested and isinstance(stmt, TpyVarDecl):
        return "res.leaf_field_write"
    if isinstance(stmt, TpyTupleUnpack):
        return "res.unpack"
    if isinstance(stmt, TpyTry):
        return "res.leaf_try"
    if isinstance(stmt, TpyWith):
        return "res.leaf_with"
    if isinstance(stmt, TpyMatch):
        return "res.leaf_match"
    if isinstance(stmt, TpyNestedDef):
        return "res.nested_def"
    if hasattr(stmt, "sub_bodies"):
        for body in stmt.sub_bodies():
            for s in body:
                r = _leaf_shape_reject(s, nested=True)
                if r is not None:
                    return r
    return None




def _payload_reject(payload: 'rcfg.SuspensionPayload', analyzer) -> str | None:
    """Await-payload gate (async bodies). INLINE (statically-known async def,
    incl. bound methods -- R5b) is admitted; the receiver/args are lowered in
    the Yield loop. Deferred: generic
    awaited callees, kwargs, non-value INLINE arg slots (R5c), and
    ERASED/BORROWED awaitables (res.await_mode -- an ERASED/BORROWED operand is
    a Task/Future that is either a non-value param or built via a gated
    create_task/Future() call, so no such body reaches routing today; the
    operand-render path stays deferred rather than a zero-witness success
    branch)."""
    if isinstance(payload, rcfg.YieldPayload):
        return "res.generator_shape"
    if payload.prebuilt_slot is not None:
        return "res.await_prebuilt"
    if payload.async_with_kind is not None or payload.async_for_uid is not None:
        # Unreachable behind the region/pseudo-stmt gates; defensive.
        return "res.await_synthetic"
    if getattr(payload.await_node, "awaited_inferred_type_args", None):
        # Generic awaited callee: the sub-coro frame is a template
        # instantiation (M7) -- template-arg capture is a cell.
        return "res.await_generic"
    if payload.mode is not rcfg.AwaitMode.INLINE:
        return "res.await_mode"
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
        if not _res_value_ok(fi.params[i].type, analyzer):
            # Non-value param slots trigger the emplace coercion ladder
            # (Own move / optional-ptr / protocol adapter / union lift) and,
            # for static-protocol params, the two-phase decltype capture
            # render -- R5c.
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
    if not _function_eligible(func, analyzer, self_type, allow_resumable=True):
        return None
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
        if not (rt is None or isinstance(rt_inner, VoidType)
                or _res_value_ok(rt, analyzer)):
            return _reject("res.return_type")
    if func.forwarded_locals:
        return _reject("res.forwarded_local")
    # Classify each non-value local. Value scalars / str / bytes are bare
    # fields (R1a); an owning plain-nonvalue local (record / list / dict /
    # set, NOT a borrow alias) is a frame_slot (R1c); everything else
    # (pointer-alias, Optional, tuple, union) is a later cell.
    frame_slots: set[str] = set()
    for lname, ltype in (func.generator_locals or []):
        lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
              if isinstance(ltype, TpyType) else None)
        if _res_local_ok(ltype, analyzer):
            continue  # value / str / bytes -- bare field
        if (lt is not None and is_plain_nonvalue(lt)
                and lname not in pointer_aliases):
            frame_slots.add(lname)
            continue
        return _reject("res.local_storage")
    if cfg.finally_helpers:
        return _reject("res.finally")

    # -- CFG shape gate ---------------------------------------------------
    for bb in cfg.blocks.values():
        if bb.region_stack:
            return _reject("res.region")
        if bb.entry_narrowings:
            return _reject("res.narrowed_resume")
        for stmt in bb.stmts:
            if isinstance(stmt, (rcfg.AsyncForIterSetup, rcfg.WithEnter,
                                 rcfg.AsyncWithSetup, rcfg.AsyncFinallyExit)):
                return _reject("res.pseudo_stmt")
        t = bb.terminator
        if isinstance(t, rcfg.AsyncForAdvance):
            return _reject("res.for_await")
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

    # -- Leaf gate + lowering ---------------------------------------------
    has_self = self_type is not None
    lc = _LowerCtx(func, analyzer, render_type,
                   render_type_stored=render_type_stored,
                   self_receiver="self" if has_self else None,
                   record_name=record_name if has_self else None,
                   self_cpp="__self", self_is_pointer=False)
    # R1c: frame_slot local reads render `(*name)` (THIRName.deref) and writes
    # `name.emplace(value)` (THIRFrameSlotWrite).
    lc.frame_slots = frame_slots
    # Scope grows like the sync walk: params seed, each leaf's own decl
    # registers as it lowers (so a first `total = 0` takes the DECL arm's
    # init render, which is what the AST frame path assigns -- pre-seeding
    # the whole frame scope would misroute it through the reassign arm's
    # target-typed wraps, e.g. `::tpy::BigInt(0)`). BB-id order is AST walk
    # order, so decl-before-use holds across blocks like it does in source.
    declared: dict[str, TpyType] = {
        n: unwrap_ref_type(t) for n, t in func.params
        if isinstance(t, TpyType)}
    if has_self:
        declared["self"] = self_type  # the record receiver, a field source
    # `self` is not a frame-field NAME write target (self-field writes lower
    # through THIRFieldAccess), so it stays out of frame_fields.
    frame_fields = set(n for n, _t in func.params) | {
        lname for lname, _lt in (func.generator_locals or [])}

    leaves: dict[int, THIRStmt] = {}
    conds: dict[int, THIRExpr] = {}
    await_args: dict[int, tuple[THIRExpr, ...]] = {}
    return_values: dict[int, THIRExpr] = {}
    yield_values: dict[int, THIRExpr] = {}
    suspend_exprs: dict[int, THIRExpr] = {}

    def _gate_leaf(stmt: TpyStmt) -> bool:
        shape_reason = _leaf_shape_reject(stmt)
        if shape_reason is not None:
            return note(shape_reason)
        return True

    def _lower_leaf(stmt: TpyStmt) -> THIRStmt:
        if isinstance(stmt, TpyVarDecl) and stmt.name in frame_fields:
            begin_stmt()
            if stmt.init is None:
                raise ThirUnsupported("stmt.decl.no_init")
            value = _lower_expr(stmt.init, lc, declared)
            if stmt.name not in declared:
                declared[stmt.name] = _var_decl_type(stmt, analyzer)
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
            if not _gate_leaf(stmt):
                return None
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
            if not _gate_leaf(t.raise_stmt):
                return None
            leaves[id(t.raise_stmt)] = _lower_leaf(t.raise_stmt)
        elif isinstance(t, rcfg.Branch):
            if not _condition_eligible(t.cond, declared, analyzer):
                return _reject("res.cond")
            conds[id(t.cond)] = _lower_expr(
                t.cond, lc, declared,
                use=_ExprUse(result=_ExprResultUse.CONDITION))
            _witness("res.branch_cond")
        elif isinstance(t, rcfg.Yield) and is_generator:
            # Generator suspension: the skeleton emits `__state = S_RESUME_i;
            # return <value>;`. The yield-type target is a baked TpyCoerce on
            # the value (sema), so lowering is position-blind (== the AST's
            # `gen_yield_value` -> `gen_expr(value, yield_type)` for the
            # value-scalar slice; the borrow-loop-var / tuple-to-pointer arms
            # are out of slice). A bare `yield` (no value) never arises for a
            # value-scalar yield type.
            ys = t.payload.yield_stmt
            if ys is None or ys.value is None:
                return _reject("res.bare_yield")
            yield_values[id(ys)] = _lower_expr(ys.value, lc, declared)
            _witness("res.yield_value")
        elif isinstance(t, rcfg.Yield):
            payload = t.payload
            # INLINE only (the payload gate rejects ERASED/BORROWED).
            operand = payload.operand_expr
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
            # A VARDECL-kind bind (`x = await f()` on a fresh name) writes
            # the frame field in the skeleton's resume step; register it so
            # later leaves' reads gate and lower with its type -- mirroring
            # the sync walk's decl-driven scope growth.
            if (payload.bind_target is not None
                    and payload.bind_target not in declared
                    and isinstance(payload.host_stmt, TpyVarDecl)):
                declared[payload.bind_target] = _var_decl_type(
                    payload.host_stmt, analyzer)

    _witness("res.body")
    return THIRResumableBody(
        leaves=leaves, conds=conds, await_args=await_args,
        return_values=return_values, yield_values=yield_values,
        suspend_exprs=suspend_exprs)


def _reject(reason: str):
    note(reason)
    return None
