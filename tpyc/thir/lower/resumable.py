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
narrowed resume points for the variant-get slice (a union frame field
proven a concrete member; leaves lower under the skeleton's extraction
alias -- `__{var}`, or the match tier's `__case_{i}` inside an arm chain),
no returns inside leaf compounds (a ReturnT terminator's scaffolding is
skeleton; a return nested in a leaf would need the async-return render
inside THIR emit -- a cell).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..fallback import ThirUnsupported, begin_stmt, note, stmt_reject_reason
from ..faces import witness as _witness
from ...binding_audit import publish_thir as publish_binding_facts
from ..nodes import (
    THIRDynIsinstanceMulti,
    THIRAssign,
    THIRCoroHandleMove,
    THIRExpr,
    THIRFieldAccess,
    THIRFrameSlotWrite,
    THIRNoOpStmt,
    THIRIfExpr,
    THIRName,
    THIRResumableBody,
    THIRStmt,
    THIRStmtSeq,
)
from ...parse.nodes import (
    TpyAssert,
    TpyAssign,
    TpyAugAssign,
    TpyAwait,
    TpyCall,
    TpyDelVar,
    TpyExpr,
    TpyFieldAccess,
    TpyForEach,
    TpyFunction,
    TpyIf,
    TpyIfExpr,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpyReturn,
    TpyStrLiteral,
    TpyStmt,
    TpyTupleLiteral,
    TpyTupleUnpack,
    TpyVarDecl,
)
from ...typesys import (
    ConcreteCoroType,
    IntLiteralType,
    NominalType,
    NoneType,
    OptionalType,
    OwnType,
    ReadonlyType,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    VoidType,
    is_dyn_protocol,
    is_fn_type,
    is_protocol_type,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import is_dict, is_list, is_set
from ...codegen_cpp import resumable_cfg as rcfg
from ...codegen_cpp.gen_generators import owned_view_frame_params
from ...codegen_cpp.forms import is_plain_nonvalue
from ...codegen_cpp.gen_async import collect_frame_nested_defs
from ..nodes import Form, THIRLiteral
from .checks import (
    _module_qual_ctor_shape,
    _assert_narrow_info,
    _ctor_shape_ok,
    _narrow_cond_info,
    _record_rvalue_source_shape,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx, ValueOptKind
from .expressions import (
    _poly_cast_checks,
    _lower_borrow_tuple_literal,
    _lower_call_arg,
    _lower_expr,
    _lower_generic_tuple_literal,
    _lower_truthy,
    _lower_tuple_literal,
    _slot_literal_retype,
    _strip_slot_leaf_deref,
)
from .functions import (
    _check_callable_structure,
    _seed_global_scope,
    method_self_type_by_name,
)
from . import match as _match
from ...typesys import polymorphic_source_inner
from .predicates import (
    _eligible_ptr_value,
    _poly_narrow_info,
    _storage_optional_return_type,
    _callable_value,
    _chain_post_if_fact,
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _f1_record,
    _f1_tuple,
    _generic_value_tuple_return,
    _narrowed_opt_field_read,
    _optional_ptr_borrow,
    _own_declared_call_ret,
    _peel_stale_view_owned_coerce,
    _reassert_bump_info,
    _res_container_return,
    _resolved_bytes_value,
    _resolved_str_value,
    _slot_free_ptr_reseat_ok,
    _str_field_value_read,
    _wrap_view_owned_sink,
    _unwrap_own,
    _value_opt_scalar,
    _value_opt_view,
    _value_tuple,
    _value_tuple_nested,
    _value_tuple_return,
)
from .statements import (
    _append_assert_narrow,
    _handler_binding_type,
    _lower_alias_bind,
    _lower_borrow_tuple_frame_write,
    _lower_erased_handle_write,
    _lower_frame_field_assign,
    _lower_frame_slot_write,
    _lower_narrow_cond,
    _lower_resumable_return_value,
    _lower_stmt,
    _make_narrow_alias,
    _persistent_alias_name,
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


def _value_opt_yield_slot(t: 'TpyType | None') -> 'OptionalType | None':
    """A VALUE-repr `Optional[T]` generator yield slot, or None. The whole
    `std::optional<T>` crosses the `__next__` boundary by value, which is
    what lets the sink pass a narrowed source un-dereferenced and a `None`
    as `std::nullopt`. The pointer-repr sibling is a plain `T*` and keeps
    the family gate's own rungs."""
    if t is None:
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OptionalType) and not u.uses_pointer_repr():
        return u
    return None


def _res_capture_ok(t: 'TpyType | None', analyzer) -> bool:
    """CAPTURE slots (param / return / yield): the value families plus a bare
    type param. `_res_value_ok`'s three siblings all read a bare `T` slot bare,
    but each for its OWN reason -- stated per position, because assuming one
    rationale spans them is how the bare `T` first (wrongly) reached locals:

    - PARAM: the form is deferred to the instantiation site
      (`param_val_or_ref_t<T>` ctor param -> `val_or_ref_t<T>` field), so
      value-typed T copies in, object-typed T borrows, and the field reads
      bare either way.
    - RETURN: NOT the trait chain -- `_make_async_return` binds a plain
      `{ret_cpp} __tpy_async_ret = <value>;`, so the slot is a plain `T` and
      the leaf renders its source bare. (That plain-T binding is also why an
      object-typed T return COPIES where CPython aliases -- see BUGS.md. When
      that fix routes the return through `val_or_ref_t<T>`, re-check this arm
      and the return leaf TOGETHER.)
    - YIELD: `gen_yield_value` bridges storage->pointer only for tuple slots
      and borrow-form loop vars; a bare `T` yield source is neither, so it
      passes through unchanged.

    A bare-`T` LOCAL rides NONE of these: it is emitted as a `T*` pointer
    ALIAS (`y = &(x)` / `(*y)`), so it must not ride -- hence `_res_local_ok`
    builds on `_res_value_ok`, not on this."""
    if t is None:
        return False
    if _res_value_ok(t, analyzer):
        return True
    return isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))),
                      TypeParamRef)


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
    matching the sync tuple-subscript rows. A bare `T` param rides via
    `_res_capture_ok`. Union (non-pointer-repr) and static-protocol params
    stay their own rungs."""
    if _res_capture_ok(t, analyzer):
        return True
    if (_resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None):
        return True
    if _optional_ptr_borrow(t, analyzer) is not None:
        return True
    # Optional-view (str|None / bytes|None) params capture OWNED
    # (`std::optional<std::string>` field, the skeleton's OWNED_COPY
    # capture); leaf reads take the existing value-opt-view arms
    # (.has_value() + narrowed (*s) through the view helpers).
    if _value_opt_view(t, analyzer) is not None:
        return True
    # Value-repr Optional[scalar] params (`std::optional<T>` value field,
    # moved capture); reads gate per-shape at the value-opt arms.
    if _value_opt_scalar(t, analyzer) is not None:
        return True
    if (_f1_tuple(t, analyzer) is not None
            or _value_tuple(t, analyzer) is not None):
        return True
    unwrapped = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                 if isinstance(t, TpyType) else None)
    if unwrapped is not None and _f1_record(unwrapped, analyzer):
        return True
    # A container param captures as a reference frame field (`std::vector<T>&`,
    # like the record `Record&`) and every leaf read/write/pass takes the sync
    # container rows unchanged; element-shape gating stays at the use sites.
    # An Own[container] param differs only in capture (the skeleton's
    # OWNED_VALUE move -> a `std::vector<T>` value field); reads are the
    # same bare container rows, so it rides the same admission.
    cont = _unwrap_own(unwrapped) if unwrapped is not None else None
    if cont is not None and (is_list(cont) or is_dict(cont)
                             or is_set(cont)):
        return True
    # A Callable/Fn param is a templated `F_pred pred;` frame field
    # (skeleton FN kind + template header); the only leaf read is the bare
    # call `pred(x)`.
    if unwrapped is not None and is_fn_type(unwrapped):
        return True
    # A non-template Callable param is a by-value `std::function<...>` frame
    # field (moved capture, skeleton); the leaf reads are the bare binding
    # call (`factory(a)`) and the bare pass -- the sync param's rows.
    if _callable_value(unwrapped):
        return True
    # A None-typed param (the `__aexit__(et, ev, tb)` triple) is a
    # `std::monostate` value field; capture and any read are position-blind.
    if isinstance(unwrapped, NoneType):
        return True
    # A static-protocol param monomorphizes the frame over the conforming
    # type (`param_val_or_ref_t<T_p>` ctor param -> `val_or_ref_t<T_p>`
    # field, all skeleton), and every leaf read is bare -- the bare-`T`
    # capture rationale. CAPTURE position only: protocol locals stay gated;
    # @dynamic protocols (Adapter machinery) stay their own rung.
    if (unwrapped is not None and is_protocol_type(unwrapped)
            and not is_dyn_protocol(unwrapped)):
        return True
    # An `Own[@dynamic P]` param captures as a bare `unique_ptr<P>` frame
    # field (the ctor's `p(std::move(p_))` is skeleton), and its leaf reads
    # are the forward arg renders (`std::move(p)` at a same-protocol Own
    # slot) -- gated per-shape at the arg rows. RAW wrapped, matching the
    # await-slot key: `Own[readonly[P]]` never takes the adapter renders.
    if (isinstance(unwrapped, OwnType)
            and is_dyn_protocol(unwrapped.wrapped)):
        return True
    # A pointer-repr union param's frame field is the SAME pointer-variant
    # shape as the sync param (`std::variant<A*, B*>`), so reads, isinstance
    # narrowing, and pass-through args take the sync union rows unchanged.
    # A VALUE union (`int | str` -> `std::variant<BigInt, std::string>`
    # moved capture) also admits: its narrowed reads ride the
    # entry-narrowings std::get/__{var} machinery, and other uses gate at
    # the leaf arms.
    return isinstance(unwrapped, UnionType)


def _res_local_ok(t: 'TpyType | None', analyzer) -> bool:
    """Frame-LOCAL families -- broader than the `_res_value_ok` base in one
    direction (str/bytes) and narrower than `_res_capture_ok` in another (NO
    bare `T`: a `T` local is a `T*` pointer alias, not a bare field).

    R1a adds str/bytes locals: a str local's frame field is a bare
    `std::string` / `std::string_view` (owned / view, sema-resolved) with
    bare reads/writes -- the same shapes THIR's ported str slice already
    emits, no frame_slot machinery. A value-repr `Optional[scalar]` local
    joins them: its frame field is the same bare `std::optional<T>` value the
    PARAM capture already admits, and every read gates per-shape at the
    value-opt arms. Records / pointer-repr Optionals (frame_slot) and
    pointer-alias / tuple locals stay their own rungs (R1c). A non-template
    Callable local joins the bare-value families: its frame field is the
    `std::function<...>` value itself (`factory = pick();` writes the plain
    frame assign, `factory(7)` reads through the ordinary callable-value
    call arm)."""
    return bool(_res_value_ok(t, analyzer)
                or _resolved_str_value(t, analyzer) is not None
                or _resolved_bytes_value(t, analyzer) is not None
                or _value_opt_scalar(t, analyzer) is not None
                or _callable_value(t))


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


def _var_decl_names(stmts: list) -> 'set[str]':
    """Every name a `TpyVarDecl` binds anywhere in the body, plus tuple-unpack
    targets -- the frame-local promotion's mirror of "a PROMOTING decl arm ran
    for this name" (the AST's `_gen_var_decl`, and `_gen_tuple_unpack`, which
    promotes each target the same way).

    Do NOT also exclude await inits: tried, and it breaks `await_in_with` +
    `await_in_with_multi_cm`, because a BigInt bound by `x = await f()` IS
    promoted on the AST path and moves at its return. The residual divergence
    is not about await at all -- it is a frame-field POINTER-set
    disagreement, so it must be fixed by aligning that membership."""
    out: 'set[str]' = set()
    for s in stmts:
        if isinstance(s, TpyVarDecl):
            out.add(s.name)
        elif isinstance(s, TpyTupleUnpack):
            out.update(n for n in s.targets if n is not None)
        for body in s.sub_bodies():
            out |= _var_decl_names(body)
    return out


def _bare_yield_tuple_name_ok(name: str, lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> bool:
    """A tuple NAME the yield hands out BARE (`return v;` / `return p;`):
    the AST's `_maybe_wrap_tuple_to_pointer` no-ops for a non-storage-form
    source. Admitted order-independently by TYPE, not by the (partially
    mirrored, lowering-order-populated) storage sets: a VALUE tuple (no
    pointer-repr element -- the wrap no-ops on the target alone), or a
    BORROW-form tuple PARAM (not Own-wrapped, not owned-movable -- the
    shapes seed_param_locals registers storage-form stay out). Locals with
    pointer-repr elements keep the `res.btuple_yield_source` fence (the
    tuple_to_pointer lift rung), as do pointer-form names."""
    if name not in declared or name in lc.pointers:
        return False
    if (name in lc.storage_tuple_locals
            or name in lc.const_storage_tuple_locals):
        return False
    tu = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[name])))
    if not isinstance(tu, TupleType):
        return False
    if not tu.has_pointer_repr_element():
        return True
    if name not in lc.prescan.param_names:
        return False
    raw = dict(lc.params).get(name)
    raw_u = unwrap_send_sync(raw) if raw is not None else None
    return not (isinstance(raw_u, OwnType) or tu.is_owned_movable())


def _alias_frame_collision(var: str, frame_fields: 'set[str]') -> bool:
    """Whether the `__{var}` extraction alias would collide with a real frame
    field (or `self`). On a collision the AST bumps the alias name
    (`_fresh_alias_local`'s `__{var}_narrowed` rename), which none of the THIR
    narrowing arms reproduce -- so every one of them rejects the shape
    instead. Shared so the three arms cannot drift apart."""
    return f"__{var}" in frame_fields or var == "self"


def _resume_alias_name(var: str, frame_fields: 'set[str]') -> str:
    """The extraction alias the AST emits for a resume-narrowed var:
    `__{var}`, bumped to `__{var}_narrowed` on a frame-field collision
    (`_fresh_alias_local`'s rename -- `self`'s `__self` always collides
    with the frame's receiver ref)."""
    base = f"__{var}"
    return (f"{base}_narrowed"
            if base in frame_fields or var == "self" else base)


def _entry_narrowings_reject(facts: 'dict[str, TpyType]',
                             param_types: 'dict[str, TpyType]',
                             gen_local_types: 'dict[str, TpyType]',
                             frame_fields: 'set[str]',
                             case_entry_ids: 'frozenset[int] | None',
                             self_type: 'TpyType | None' = None,
                             analyzer=None,
                             ) -> 'str | None':
    """Narrowed-BB admission: the variant-get slice only. Each fact must be
    a concrete non-protocol member narrowing a union-declared frame field --
    the shape `_emit_isinstance_extractions` re-establishes with a plain
    `std::get` alias whose name the lowering can mirror (`__{var}`, the
    non-persistent `_fresh_alias_local`). Everything else -- the polymorphic
    self/subclass dynamic_cast family, Optional `is not None`, literal /
    protocol / Any facts, narrowed-to-smaller-union -- keeps the named
    reject. Without the skeleton's case-entry set the match-arm alias
    environments can't be mirrored, so every narrowed body stays fallback."""
    if case_entry_ids is None:
        return "res.narrowed_resume"
    for var, fact in facts.items():
        if (var == "self" and self_type is not None and analyzer is not None
                and isinstance(fact, NominalType)
                and not is_protocol_type(fact)
                and polymorphic_source_inner(self_type, analyzer.registry)
                is not None):
            # The POLY-SELF fact: the skeleton re-extracts per resume case
            # (`const Dog& __self_narrowed = *dynamic_cast<...>(&__self);`
            # -- _emit_isinstance_extractions' poly arm with the
            # _fresh_alias_local bump), and the leaves read the SPELLED
            # alias -- no cross-BB state, the round C memo's finding.
            continue
        decl = param_types.get(var, gen_local_types.get(var))
        if not isinstance(decl, TpyType):
            return "res.narrowed_resume"
        du = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(decl)))
        if not isinstance(du, UnionType):
            return "res.narrowed_resume"
        if not isinstance(fact, NominalType) or is_protocol_type(fact):
            return "res.narrowed_resume"
        if _alias_frame_collision(var, frame_fields):
            return "res.narrowed_resume"
    return None


def _rebinds_narrowed(stmts, names: 'frozenset[str]') -> bool:
    """A direct rebind of a narrowed name anywhere in a BB's leaf subtree
    (including non-suspending compounds) un-narrows MID-BB, while the scope
    this pass installs is entry-level for the whole BB -- so such a BB must
    fall back. Field/subscript writes mutate THROUGH the alias and keep the
    narrowing. The generic body walk over-matches unknown compound kinds
    on purpose: a false positive only costs AST fallback."""
    for s in stmts:
        if isinstance(s, TpyVarDecl):
            if s.name in names:
                return True
        elif isinstance(s, (TpyAssign, TpyAugAssign)):
            if isinstance(s.target, TpyName) and s.target.name in names:
                return True
        elif isinstance(s, TpyTupleUnpack):
            if any(t in names for t in s.targets if t is not None):
                return True
        elif isinstance(s, TpyDelVar):
            if any(n in names for n in s.names):
                return True
        elif isinstance(s, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
            if s.item.target in names:
                return True
        for attr in ("then_body", "else_body", "body", "orelse",
                     "finally_body"):
            b = getattr(s, attr, None)
            if b and _rebinds_narrowed(b, names):
                return True
        for h in getattr(s, "handlers", None) or ():
            if h.body and _rebinds_narrowed(h.body, names):
                return True
        for c in getattr(s, "cases", None) or ():
            if c.body and _rebinds_narrowed(c.body, names):
                return True
    return False


def _resume_narrow_envs(cfg: 'rcfg.CFG',
                        case_entry_ids: 'frozenset[int]',
                        frame_fields: 'set[str]' = frozenset(),
                        ) -> 'dict[int, dict[str, tuple[TpyType, str]] | None]':
    """Per-BB narrowing environments `{var: (fact, alias)}`, mirroring the
    walker's inline emission: an extraction local stays lexically live for
    the rest of its inline chain even where the CFG's stamped facts drop
    (the early-return join reads the else-arm's alias), so the env
    propagates along the same edges the walker follows instead of reading
    each BB's own `entry_narrowings`.

    Alias sources, matching the emit sites exactly: a case entry
    re-establishes ALL its stamped facts as `__{var}` (`_emit_case_body` ->
    `_emit_resume_narrowings(outer=None)`); a Branch arm adds the delta of
    its stamped facts vs the walk-start BB's (`_walk_inline_or_jump(outer=
    chain_entry)`) as `__{var}`; a match arm binds the subject to the
    tier's `__case_{i}` (i = source case index -- the rule
    `_lower_match_union` draws it by); the match join and the async-for
    body re-establish ALL their stamped facts as `__{var}` (walked with
    outer=None). A BB visited twice with different envs maps to None
    (unmodeled -- the caller rejects if it carries facts)."""
    envs: dict[int, 'dict[str, tuple[TpyType, str]] | None'] = {}

    def _record(bb_id: int,
                env: 'dict[str, tuple[TpyType, str]]') -> bool:
        if bb_id in envs:
            if envs[bb_id] != env:
                envs[bb_id] = None
            return False
        envs[bb_id] = env
        return True

    # Worklist of (bb_id, env, chain_entry) walk states.
    work: list[tuple[int, dict, dict]] = []
    for ce in case_entry_ids:
        bb = cfg.blocks.get(ce)
        if bb is None:
            continue
        env = {v: (f, _resume_alias_name(v, frame_fields))
               for v, f in bb.entry_narrowings.items()}
        work.append((ce, env, bb.entry_narrowings))
    while work:
        bb_id, env, chain_entry = work.pop()
        if not _record(bb_id, env):
            continue
        bb = cfg.blocks[bb_id]
        t = bb.terminator

        def _arm(target: int, outer: dict) -> None:
            if target in case_entry_ids:
                return
            tb = cfg.blocks[target]
            new_env = dict(env)
            new_env.update(
                {v: (f, _resume_alias_name(v, frame_fields))
                 for v, f in tb.entry_narrowings.items()
                 if outer.get(v) is not f})
            work.append((target, new_env, tb.entry_narrowings))

        if isinstance(t, rcfg.Fall):
            if t.next_bb not in case_entry_ids:
                work.append((t.next_bb, env, chain_entry))
        elif isinstance(t, rcfg.Branch):
            _arm(t.then_bb, chain_entry)
            _arm(t.else_bb, chain_entry)
        elif isinstance(t, rcfg.MatchDispatch):
            subj_expr = t.match_stmt.subject
            subj = subj_expr.name if isinstance(subj_expr, TpyName) else None
            for i, (case, arm_bb) in enumerate(
                    zip(t.match_stmt.cases, t.arm_bbs)):
                if arm_bb in case_entry_ids:
                    continue
                ab = cfg.blocks[arm_bb]
                new_env = dict(env)
                fact = (case.type_facts or {}).get(subj)
                if subj is not None and fact is not None:
                    new_env[subj] = (fact, f"__case_{i}")
                work.append((arm_bb, new_env, ab.entry_narrowings))
            if (t.join_bb is not None
                    and t.join_bb not in case_entry_ids):
                _arm(t.join_bb, {})
        elif isinstance(t, rcfg.AsyncForAdvance):
            _arm(t.has_value_bb, {})
            if t.exhausted_bb not in case_entry_ids:
                # Defensive walker path: plain inline walk, no
                # re-establishment.
                work.append((t.exhausted_bb, env, {}))
    return envs


def _for_advance_reject(t: 'rcfg.AsyncForAdvance', analyzer,
                        ptr_loop_vars: 'set[str]',
                        slot_locals: 'set[str]',
                        borrow_tuple_loop_vars: 'set[str]',
                        value_tuple_holders: 'set[str]') -> 'str | None':
    """Sync loop-advance admission (R3). The bind itself is skeleton for every
    family (`_for_advance_parts` composes it from the frame classification);
    what gates is whether the leaf READS mirror the bound shape. A
    value-scalar / str / bytes element binds bare (plain frame field, ported
    read shapes). A pointer-form loop var (`x = &(*it++);`) reads through
    `lc.pointers` (arrow / deref arms) and a frame_slot loop var
    (`x.emplace(..)` at the advance) reads `(*x)` -- both admitted, keyed on
    the skeleton's own classification, never re-derived from the element
    type. Proxy-ref borrow-tuple loop vars (dict_items) admit in both the
    whole-tuple and head-unpack forms (the advance bind is skeleton; reads
    ride the borrow-tuple family); holders outside every admitted set keep
    the reject."""
    stmt = t.stmt
    if _res_local_ok(_loop_elem_type(stmt, analyzer), analyzer):
        return None
    if stmt.is_tuple_unpack:
        # A VALUE-tuple holder (`__for_tup_N` bare field) binds bare at the
        # advance and its head unpack ref-binds it as a name source. A
        # POINTER-form holder (`__for_tup_N = &(*it++);`, non-value
        # elements) binds at the advance like any pointer-form loop var;
        # its head unpack derefs the holder and re-points the alias
        # targets.
        if stmt.var in value_tuple_holders:
            _witness("res.loop_tuple_bind")
            return None
        if stmt.var in ptr_loop_vars:
            _witness("res.loop_ptr_bind")
            return None
        if stmt.var in borrow_tuple_loop_vars:
            # Proxy-ref holder (dict_items): the advance binds the
            # borrow-form tuple via tuple_to_pointer (skeleton); the head
            # unpack mutable-ref-binds it as a borrow-tuple name source.
            _witness("res.loop_btuple_bind")
            return None
        return "res.loop_var"
    if stmt.var in borrow_tuple_loop_vars:
        # Whole-tuple proxy loop var (`for kv in d.items()`): element
        # reads ride the borrow-tuple subscript family off the frame
        # field the advance re-binds.
        _witness("res.loop_btuple_bind")
        return None
    if stmt.var in ptr_loop_vars:
        _witness("res.loop_ptr_bind")
        return None
    if stmt.var in slot_locals:
        _witness("res.loop_slot_bind")
        return None
    return "res.loop_var"


def _for_iterable_narrowed_optional(iterable, declared, analyzer) -> bool:
    """A narrowed value-Optional iterable (`str|None`/`bytes|None` proven
    non-None) stays `std::optional<V>` in the frame; the skeleton's
    `_maybe_unwrap_narrowed_optional` renders `(*v)`, which the bare leaf
    render does not reproduce. Detect the frame-field case (declared storage
    is Optional, sema type narrowed non-Optional) and reject -- a named
    rung."""
    if isinstance(iterable, TpyFieldAccess):
        # A narrowed Optional FIELD gets the same treatment: on the routed
        # path the skeleton calls `_maybe_unwrap_narrowed_optional` directly,
        # and that helper (unlike `render_for_iterable`, which short-circuits
        # fields and is only reached with no THIR leaf) DOES wrap a field --
        # so the leaf must hand over the un-derefed member read.
        return _narrowed_opt_field_read(
            iterable, analyzer.get_expr_type(iterable), declared, analyzer)
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

    # ITERABLE result use, mirroring the sync for-head: the skeleton's
    # source capture consumes the render whole (`gen_expr(iterable_expr)`,
    # position-blind), so generator-factory calls admit here exactly like
    # the sync route (their render is the same bare call). The setup is a
    # statement position -- arg temps (`int32_t __tmp_N = 7;` ref-slot
    # literals of a generic factory call) flush before the source capture,
    # exactly where the AST flushes them.
    lowered_it = _lower_expr(
        it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                       allow_temps=True))
    if _for_iterable_narrowed_optional(it, declared, analyzer):
        # The begin_end skeleton owns the narrowed value-Optional unwrap
        # (`_maybe_unwrap_narrowed_optional` over the leaf render), so the
        # leaf supplies the BARE name/member -- a deref'd leaf would
        # double-deref.
        if isinstance(lowered_it, THIRFieldAccess):
            if not lowered_it.narrowed_deref:
                raise ThirUnsupported("res.for_narrowed_optional")
            _witness("res.for_narrowed_opt_field_src")
            lowered_it = replace(lowered_it, narrowed_deref=False)
        elif isinstance(lowered_it, THIRName) and lowered_it.deref:
            _witness("res.for_narrowed_opt_src")
            lowered_it = replace(lowered_it, deref=False)
        else:
            raise ThirUnsupported("res.for_narrowed_optional")
    region_exprs[id(it)] = lowered_it


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
        ok = ((isinstance(ctx, TpyCall)
               and _f1_record(analyzer.get_expr_type(ctx), analyzer)
               and (_ctor_shape_ok(ctx, analyzer)
                    or _record_rvalue_source_shape(ctx, analyzer)))
              # A module-qualified ctor manager (`async with svc.Gate()`):
              # the qualified spelling renders through the marker ctor arm
              # (`::tpyapp::svc::Gate()`), same owned `__with_ctx_N`
              # emplace as the bare-name ctor.
              or (_f1_record(analyzer.get_expr_type(ctx), analyzer)
                  and _module_qual_ctor_shape(ctx, analyzer)))
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
        # Bound-handle await (`await c`): the skeleton polls/resets the
        # handle's own frame slot in place -- no __sub field, no emplace, no
        # operand render (the operand is the handle NAME, consumed only as
        # the slot name). Nothing for the seam to lower.
        return None
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
        pt = fi.params[i].type
        # An `Own[@dynamic P]` slot (wait_for's Own[Cancellable[T]]): the
        # emplace arg is byte-equal to the sync call-arg render (the
        # make_adapter erasure rides `_lower_call_arg`'s factory/handle
        # faces), so the slot admits at the AWAIT position without joining
        # `_res_param_ok` (the frame-PARAM capture for such a slot is a
        # `unique_ptr<P>` field with forwarded reads -- an unverified
        # position that stays gated).
        pt_u = (unwrap_send_sync(pt) if isinstance(pt, TpyType) else None)
        # RAW wrapped, matching the arg gates: an `Own[readonly[P]]` slot
        # never takes the adapter render on the AST side.
        own_dyn_slot = (isinstance(pt_u, OwnType)
                        and is_dyn_protocol(pt_u.wrapped))
        # An `Own[value]` slot (a generic Own[T] param at a value
        # instantiation, e.g. Queue[Int32].put): the emplace arg is the sync
        # call-arg render (arg temp + std::move via `gen_call_arg`), so it
        # rides the same `_lower_call_arg` rows; a bad arg SHAPE still
        # rejects inside the arg lowering.
        own_val_slot = (isinstance(pt_u, OwnType)
                        and _res_value_ok(unwrap_readonly(pt_u.wrapped),
                                          analyzer))
        if not (own_dyn_slot or own_val_slot or _res_param_ok(pt, analyzer)):
            # Slots beyond the param families (optional-ptr / protocol
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
                    case_entry_ids: 'frozenset[int] | None' = None,
                    native_globals: 'Mapping[str, str] | None' = None,
                    frame_layout: 'rcfg.FrameLayoutPlan | None' = None,
                    ) -> 'THIRResumableBody | None':
    """Lower a resumable body, falling back cleanly on a lowering reject."""
    try:
        return _lower_resumable(
            func, analyzer, render_type, cfg,
            record_name=record_name,
            render_type_stored=render_type_stored,
            case_entry_ids=case_entry_ids,
            native_globals=native_globals,
            frame_layout=frame_layout,
        )
    except ThirUnsupported as ex:
        return _reject(ex.reason)


def _lower_resumable(func: TpyFunction, analyzer, render_type,
                     cfg: 'rcfg.CFG',
                     record_name: 'str | None' = None,
                     render_type_stored=None,
                     case_entry_ids: 'frozenset[int] | None' = None,
                     native_globals: 'Mapping[str, str] | None' = None,
                     frame_layout: 'rcfg.FrameLayoutPlan | None' = None,
                     ) -> 'THIRResumableBody | None':
    """Lower one resumable body's leaves, or None if outside the slice.

    `record_name` is the owning record for a method coro (R2): its frame
    captures the receiver as `__self: Record&`, so self-reads render
    `__self` / `__self.x` (`.` access) instead of `this` / `this->x`.

    A generator (`yield`) shares the state-machine skeleton with `async`
    (R4); its leaf seam is minimal -- the yield-value render -- since a
    generator returns only bare `return` (StopIteration, skeleton-only).

    `frame_layout` is the skeleton's frame-local placement plan
    (`gen_async._frame_layout`): each local's field form, reused (vs
    re-derived) so the form decision is identical on both paths; None (unit
    callers) keeps every body with hoisted locals on the fallback path.

    `case_entry_ids` is the skeleton's case-label set (`_compute_case_entries`
    keys, cached on the CFG): the emit positions that re-establish narrowing
    aliases as `__{var}`. Reused (vs re-derived) so the narrowed-BB alias
    environments match the walker exactly; None (unit callers) keeps every
    narrowed body on the fallback path."""
    is_generator = bool(func.is_generator)
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
    # A generic frame (`async def f[T]` / a coro method on a generic record)
    # needs no gate of its own: the template header, and the value-vs-reference
    # frame-field choice (`val_or_ref_t<T>`), are skeleton -- every leaf reads
    # the field bare, identically for both. What a T can appear IN is gated by
    # the per-slot param / return / yield families below.
    for _pname, ptype in func.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _res_param_ok(pt, analyzer):
            return _reject("res.param_type")
    if is_generator:
        # The yielded element type gates a generator (its `return_type` is
        # `Iterator[T]`, not a value slot). Value scalars, a bare `T`, and
        # str/bytes route (the owned return slot's ctor absorbs the bare
        # source render -- the `_sgen_yield_ok` families' reasoning; the
        # slot-literal retype in the Yield arm mirrors gen_yield_value's
        # target threading). Records are interlocked with the non-value
        # loop-var rung (`(*b)` deref) and tuples need the tuple_to_pointer
        # bridge + borrow literal builder -- each its own rung.
        yt = func.generator_yield_type
        yt_t = yt if isinstance(yt, TpyType) else None
        # An `Own[container]` yield slot renders exactly like the plain
        # container borrow (`return (*a);` -- the ownership fact lives in
        # the consumer's loop-var binding), so the family checks peel Own.
        yt_tuple = (_unwrap_own(unwrap_readonly(unwrap_ref_type(
                        unwrap_send_sync(yt_t))))
                    if yt_t is not None else None)
        # A VALUE-repr Optional yield slot (`Iterator[Int32 | None]`) is the
        # whole `std::optional<T>` by value, so it admits on its INNER's
        # capture -- the yield sink then passes the optional bare (no deref,
        # no monostate). Scoped to this gate: `_res_param_ok` and the async
        # return read the same predicate and keep their own slot rules.
        yt_valopt = _value_opt_yield_slot(yt_t)
        if not (_res_capture_ok(yt_t, analyzer)
                or (yt_valopt is not None
                    and _res_capture_ok(yt_valopt.inner, analyzer))
                or _resolved_str_value(yt_t, analyzer) is not None
                or _resolved_bytes_value(yt_t, analyzer) is not None
                # Tuple slots gate per-yield in the Yield arm (literal
                # builder / borrow-name pass-through); the slot family alone
                # admits.
                or isinstance(yt_tuple, TupleType)
                # Container slots also gate per-yield (the frame-slot
                # borrow-name arm; other value shapes reject there).
                or is_list(yt_tuple) or is_dict(yt_tuple)
                or is_set(yt_tuple)
                # Record slots gate per-yield too (routed loop-var /
                # frame_slot NAME borrow deref only).
                or _f1_record(yt_tuple, analyzer)):
            return _reject("res.yield_type")
    else:
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        rt_inner = (unwrap_readonly(unwrap_send_sync(rt))
                    if rt is not None else None)
        # Owned str/bytes returns ride the shared form-keyed wrap
        # (`_wrap_view_owned_return`): the AST's `_wrap_view_to_storage` fires
        # iff the source is view-form, and THIR's lowered value carries that
        # as its form fact -- the equivalence every routed sync str return
        # already validates. One render serves all three async scaffolding
        # sites (`_async_ret_to_borrow` is a no-op for str/bytes).
        if not (rt is None or isinstance(rt_inner, VoidType)
                or _res_capture_ok(rt, analyzer)
                or _resolved_str_value(rt, analyzer) is not None
                or _resolved_bytes_value(rt, analyzer) is not None
                # Value-tuple return slot (`std::tuple<...>` by value,
                # Own[F1-record] elements included): the return value
                # gates per-shape in _lower_resumable_return_value's
                # tuple arm (a literal renders the spelled brace-init,
                # position-independent like the frame-field flavor).
                or _value_tuple_return(rt, analyzer) is not None
                # Generic tuple slot (>=1 TypeParamRef element, spelled
                # `val_or_ptr_t<T>` per element): a literal renders the
                # spelled brace-init with `to_val_or_ptr` element wraps in
                # _lower_resumable_return_value's generic arm.
                or _generic_value_tuple_return(rt, analyzer) is not None
                # Value-repr Optional[scalar] (`std::optional<T>` slot):
                # the return value gates per-shape in
                # _lower_resumable_return_value's value-opt arm.
                or _value_opt_scalar(rt, analyzer) is not None
                # Container storage slot (`std::vector`/`map`/`set` by
                # value): sources ride the position-blind tail (a last-use
                # movable name with a THIRMove wrap), except the empty
                # literal the return arm rejects.
                or _res_container_return(rt, analyzer) is not None
                # Own[F1-record] slot (`Poll<Box>` payload, `<Box>
                # __tpy_async_ret = std::move(...)`): STORAGE form, so the
                # position-blind tail's last-use move serves the sources;
                # bare reference-type returns stay on return.borrow_form.
                or (isinstance(rt_inner, OwnType)
                    and _f1_record(_unwrap_own(rt_inner), analyzer))
                # Own[T] slot (generic ownership transfer, `Poll<T>` value
                # payload): STORAGE form like the bare-T return, so the
                # position-blind tail serves the sources; the return-await
                # forward (`__ret0`) is skeleton.
                or (isinstance(rt_inner, OwnType)
                    and isinstance(unwrap_readonly(rt_inner.wrapped),
                                   TypeParamRef))
                # Bare F1-record slot (Poll<T*> pointer payload): the value
                # tail admits only the SELF lift rung (`&(__self)`); every
                # other source keeps the return.borrow_form fence.
                or _f1_record(rt_inner, analyzer)
                # Storage Optional[F1-record] slot (`-> Own[Box] | None` ->
                # `Poll<std::optional<Box>>`): the sync F2c return family's
                # async twin -- None spells nullopt at the return arm's
                # record-opt rung, ctor rvalues ride the position-blind
                # tail (the optional's converting ctor absorbs them).
                or _storage_optional_return_type(rt_inner, analyzer)
                is not None
                # Pointer-repr Optional slot (`-> Box | None` -> Poll<Box*>,
                # BORROW form): sources gate in the return arm's BORROW
                # rungs (the field optional_to_ptr lift; others fence).
                or (isinstance(rt_inner, OptionalType)
                    and rt_inner.uses_pointer_repr())):
            return _reject("res.return_type")
    # Forwarded proto-param aliases (`xs = it`) are compile-time renames:
    # the decl emits nothing and every read renders the backing param
    # (the AST's generator_storage_name substitution). Admitted whenever
    # each backing IS a param; lc.forwarded_map carries the swap.
    if func.forwarded_locals:
        param_names = {n for n, _t in func.params}
        if not all(backing in param_names
                   for backing in func.forwarded_locals.values()):
            return _reject("res.forwarded_local")
    # Classify each hoisted local off its FrameLayoutPlan verdict (the
    # skeleton's own placement decision, passed through the seam); a
    # verdict whose READ/WRITE render family is not mirrored yet rejects
    # here. The alias ORIGIN (loop var vs unpack target vs statement
    # alias) is not a placement fact -- it comes from the same for-prescan
    # info the skeleton reads.
    #
    # Loop-var bind families come from the skeleton's for-prescan (the
    # mirror of `setup_resumable_frame_locals`): a pointer-form loop var is
    # a bare `T*` frame field the advance reseats (`x = &(*it++);`), so its
    # reads take the pointer arms, not a frame_slot; any other non-value
    # loop var classifies frame_slot through the plain-nonvalue branch below
    # (the skeleton's "other non-value" arm, `x.emplace(..)` binds).
    if frame_layout is None and func.generator_locals:
        return _reject("res.local_storage")
    rstate = rcfg.resumable_state(func)
    # str/bytes params whose view the frame copied into owned storage on the
    # way in: their reads are storage form here, so the owning-sink wrap must
    # not copy them a second time (the skeleton's `owned_view_frame_params`).
    owned_view_params = owned_view_frame_params(func.params)
    ptr_frame_locals: set[str] = set()
    value_opt_record_locals: set[str] = set()
    opt_ptr_locals: set[str] = set()
    alias_ptr_locals: set[str] = set()
    value_tuple_locals: set[str] = set()
    opt_tuple_holders: set[str] = set()
    borrow_tuple_loop_vars: set[str] = set()
    unpack_ptr_targets: set[str] = set()
    for f_info in rstate.for_info_by_uid.values():
        if f_info.pointer_form_loop_var is not None:
            ptr_frame_locals.add(f_info.pointer_form_loop_var)
        if f_info.borrow_tuple_loop_var is not None:
            borrow_tuple_loop_vars.add(f_info.borrow_tuple_loop_var)
        # Tuple-unpack targets aliasing a non-value container member: `T*`
        # fields the head unpack re-points via `= &(std::get<i>(__tup_N));`
        # -- the skeleton's pointer_form_unpack_targets seeding.
        unpack_ptr_targets.update(f_info.pointer_form_unpack_targets)
    frame_slots: set[str] = set()
    coro_handle_slots: set[str] = set()
    erased_handle_locals: set[str] = set()
    borrow_tuple_locals: set[str] = set()
    owning_tuple_slots: set[str] = set()
    _K = rcfg.FrameLocalKind
    for lname, ltype in (func.generator_locals or []):
        kind = frame_layout.bindings[lname].kind
        if kind in (_K.VALUE, _K.OWNED_STR):
            # Bare value field (R1a: value scalars / str / bytes /
            # value-opt scalars). An OWNED_STR field is `std::string`, but
            # its reads/writes are the same sema-resolved str renders the
            # view field takes. Value types beyond the admitted families
            # (value records, Ptr, Char arrays) have unmirrored renders.
            if _res_local_ok(ltype, analyzer):
                continue
            # A `std::optional<Record>` VALUE frame field (the await-bind
            # local of a storage-optional coro, `owned = await make()`):
            # the RECORD value-opt kind -- has_value None-tests, `(*o)`
            # narrowed derefs -- riding the same kind-tagged registration
            # as the Own-opt storage params.
            if _storage_optional_return_type(
                    unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
                    if isinstance(ltype, TpyType) else None,
                    analyzer) is not None:
                value_opt_record_locals.add(lname)
                continue
            # A raw `Ptr[T]` VALUE local (`h = _get_current_executor()`):
            # the frame field is the bare `T*` value (`Executor* h;`);
            # writes are the plain frame assign and reads/None-tests the
            # sync pointer rows (`(h == nullptr)`, bare arg pass).
            if _eligible_ptr_value(ltype, analyzer):
                continue
            if _value_tuple(ltype, analyzer) is not None:
                # Value/storage tuple local (`std::tuple<...>` bare field):
                # the await-result bind is skeleton, reads are the sync
                # tuple-subscript rows (std::get) -- same family the param
                # slot already admits. Collected so the unpack arm can
                # ref-bind it as a name source (`const auto& __tup_N =
                # <name>;`).
                value_tuple_locals.add(lname)
                continue
            # A `__for_tup_*` VALUE holder with STORAGE-optional elements
            # (`for a, b in pairs:` over `list[tuple[P | None, ...]]`): the
            # advance binds the storage tuple bare (skeleton) and the head
            # unpack mutable-ref-binds it, lifting each ptr-repr Optional
            # target via optional_to_ptr. Value elements ride the plain
            # frame-assign binds.
            lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
                  if isinstance(ltype, TpyType) else None)
            if (lname.startswith("__for_tup_")
                    and isinstance(lt, TupleType)
                    and lt.has_pointer_repr_element()
                    and all(
                        _optional_ptr_borrow(unwrap_ref_type(et),
                                             analyzer) is not None
                        or _res_local_ok(unwrap_ref_type(et), analyzer)
                        for et in lt.element_types)):
                opt_tuple_holders.add(lname)
                continue
            # A CONCRETE coro handle (`h = step(1)`) sits inside the
            # skeleton's VALUE arm -- its `type_to_cpp` renders the
            # `std::optional<__coro_...>` field directly -- but its writes
            # are the handle family (`h.emplace(<factory call>)`, gated by
            # a factory-call-only init arm) and its awaits the adapter
            # rows, so it needs the coro_handle_slots membership. The
            # ERASED handle (`unique_ptr<P>` bare field) writes `=` through
            # the own-arg wrap and reads move-at-last-use -- its own decl
            # arm (`_lower_erased_handle_write`), source shapes gated there.
            if (isinstance(lt, OwnType)
                    and is_dyn_protocol(unwrap_readonly(lt.wrapped))):
                if isinstance(unwrap_readonly(lt.wrapped), ConcreteCoroType):
                    frame_slots.add(lname)
                    coro_handle_slots.add(lname)
                else:
                    erased_handle_locals.add(lname)
                continue
            return _reject("res.local_storage")
        if kind is _K.PTR_ALIAS:
            if lname in ptr_frame_locals:
                continue  # pointer-form loop var -- lc.pointers, seeded below
            if lname in unpack_ptr_targets:
                continue  # pointer-form unpack target -- lc.pointers, below
            # A plain-nonvalue statement-level alias is a bare `T*` frame
            # field aliasing live storage: reads ride lc.pointers, and the
            # binds render `name = &(<lvalue>);` at their own leaf arms
            # (single-assign / unpack-target). The synthetic `__unpack_*`
            # decomposition temps (a tuple-literal unpack's desugared
            # element binds) take the same single-assign alias renders as
            # a user-named alias. Non-plain alias types keep the reject.
            lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
                  if isinstance(ltype, TpyType) else None)
            if lt is not None and is_plain_nonvalue(lt):
                alias_ptr_locals.add(lname)
                continue
            return _reject("res.local_storage")
        if kind is _K.SOURCE_FORM_SLOT:
            # The field's payload comes from the iteration source, so the
            # slot holds whichever form the trait picked; reads go through
            # the frame_slot deref either way, even for a value element.
            frame_slots.add(lname)
            continue
        if kind is _K.OPT_PTR:
            # Pointer-repr Optional[NonValue] local (`P* x = nullptr;`
            # field): reads ride lc.pointers (null tests + arrow), writes
            # are bare `=` from P*-shaped sources -- the local twin of the
            # Optional-ptr param admission. Kept OUT of ptr_frame_locals:
            # the loop-var-only arms (advance admission, value-yield deref,
            # record-yield names) must not see it. The admission predicate
            # is narrower than the placement (container inners reject).
            if _optional_ptr_borrow(ltype, analyzer) is not None:
                opt_ptr_locals.add(lname)
                continue
            return _reject("res.local_storage")
        if kind is _K.OWNING_TUPLE_SLOT:
            # A one-shot `__await_lift_*` tuple holder is an OWNING
            # frame_slot (an await result is always owned, so even a
            # reference-element tuple gets `frame_slot<std::tuple<...>>`
            # storage). Its bind/reset scaffolding is skeleton; the only
            # THIR-visible access is the single consuming statement, which
            # gates per-shape (the unpack's one-shot source arm / the sync
            # expr reads).
            if lname in rstate.one_shot_lift_names:
                frame_slots.add(lname)
                continue
            # The third owning signal (never-reassigned, bound from a
            # storage-form CALL): a real OWNING frame_slot -- reads deref
            # the slot, the decl write emplaces via the general frame-slot
            # write (res.frame_slot_write). Own-element
            # tuples ride the same slot; their write admission is the
            # emplace value's own result gate.
            lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
                  if isinstance(ltype, TpyType) else None)
            if (isinstance(lt, TupleType)
                    and not lname.startswith("__for_tup_")):
                frame_slots.add(lname)
                # Element reads must key VALUE-form off the BINDING (the
                # slot stores the storage tuple), not the pointer-repr
                # type -- register in storage_tuple_locals below.
                owning_tuple_slots.add(lname)
                continue
            return _reject("res.local_storage")
        if kind is _K.BORROW_TUPLE:
            # A pointer-repr tuple local is a BORROW-form frame field
            # (`std::tuple<..., T*>`, bare writes). A `__for_tup_*` loop
            # element holder is admitted only as the loop's own proxy-ref
            # borrow-tuple var (dict_items): its bind is the skeleton's
            # advance (`tuple_to_pointer(*(it)++)`), and the THIR-visible
            # accesses -- the head unpack's mutable ref-bind and element
            # reads -- are the same borrow-tuple family as a stable field.
            if (lname.startswith("__for_tup_")
                    and lname not in borrow_tuple_loop_vars):
                return _reject("res.local_storage")
            borrow_tuple_locals.add(lname)
            continue
        if kind is _K.FRAME_SLOT:
            lt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ltype)))
                  if isinstance(ltype, TpyType) else None)
            # An Own[dyn-protocol] local is a coroutine/adapter handle with
            # its own AST write arms: a CONCRETE handle (`c = add_one(1)`)
            # writes `c.emplace(<factory call>)` -- the frame_slot shape,
            # admitted with a factory-call-only init gate in the decl arm;
            # an ERASED handle (`unique_ptr<P>` field) writes `=` through
            # the make_adapter wrap, a render family the seam does not
            # mirror -- reject.
            if (isinstance(lt, OwnType)
                    and is_dyn_protocol(unwrap_readonly(lt.wrapped))):
                if isinstance(unwrap_readonly(lt.wrapped), ConcreteCoroType):
                    frame_slots.add(lname)
                    coro_handle_slots.add(lname)
                    continue
                return _reject("res.local_storage")
            # For-loop targets bind C++ locals that shadow their same-named
            # frame field; the for-each lowering masks them out of
            # frame_slots (the register_frame_field_shadow mirror), so they
            # classify like any other local here rather than rejecting.
            # Non-plain slot types (Callable etc.) have unmirrored
            # emplace/read renders.
            if lt is not None and is_plain_nonvalue(lt):
                frame_slots.add(lname)
                continue
            # A UNION frame slot (`frame_slot<std::variant<...>>`): writes
            # emplace, reads deref the slot -- and the deref'd subject is
            # the VALUE variant, so the narrow arms' value-union treatment
            # (holds_alternative<T>((*t)) / std::get<T>((*t))) applies as
            # long as the name never registers ptr-variant.
            if (isinstance(lt, UnionType) and not lt.needs_wrapper()
                    and lt.uses_pointer_repr()):
                frame_slots.add(lname)
                continue
            return _reject("res.local_storage")
        # PROTOCOL (the struct emit raises its user-facing error first on
        # this path) and any future kind: no mirrored render family.
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
    param_types = {n: t for n, t in func.params}

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
            reason = _entry_narrowings_reject(
                bb.entry_narrowings, param_types, gen_local_types,
                frame_fields, case_entry_ids,
                self_type=self_type, analyzer=analyzer)
            if reason is not None:
                return _reject(reason)
        # AsyncForIterSetup (sync + async), AsyncWithSetup and
        # AsyncFinallyExit all route: their only user render (the iterable /
        # manager expression) is lowered in pass 2; everything else (counters,
        # __aiter__/__anext__, pending-return replay) is skeleton.
        t = bb.terminator
        if isinstance(t, rcfg.AsyncForAdvance):
            # Sync loop advance: value/str/bytes binds plus the pointer-form
            # and frame_slot loop-var families (coro-handle slots excluded --
            # an advance emplace into one is outside the factory-call-only
            # handle contract).
            reason = _for_advance_reject(
                t, analyzer, ptr_frame_locals,
                frame_slots - coro_handle_slots, borrow_tuple_loop_vars,
                value_tuple_locals | opt_tuple_holders)
            if reason is not None:
                return _reject(reason)
            saw_sync_loop = True
        # MatchDispatch lowers in the leaf pass (the dispatch routes through
        # the sync match tiers with arm-body hooks; rejects raise there).
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
                        # on an optional field). A VALUE-tuple unpack HOLDER
                        # (`async for k, sq in p:`) is the same bare-field
                        # ASSIGN bind; its head unpack ref-binds it via the
                        # value-tuple name-source arm.
                        bt = gen_local_types.get(payload.bind_target)
                        if not (_res_local_ok(bt, analyzer)
                                or _value_tuple(bt, analyzer) is not None):
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
    if func.forwarded_locals:
        lc.forwarded_map = dict(func.forwarded_locals)
    # R1c: frame_slot local reads render `(*name)` (THIRName.deref) and writes
    # `name.emplace(value)` (THIRFrameSlotWrite).
    lc.frame_slots = frame_slots
    for _vor in value_opt_record_locals:
        lc.value_opt_bindings[_vor] = ValueOptKind.RECORD
    # An owning tuple slot stores the STORAGE tuple, so element reads off
    # the slot deref render value-form (`std::get<1>((*t)).val`, dot not
    # arrow) -- the binding-form fact, per-binding not per-type.
    lc.storage_tuple_locals |= owning_tuple_slots
    # The AST's frame-body var-decl arm promotes a frame-promoted STORAGE slot
    # (its pointer-form sibling returns before the promotion) with no
    # value-type filter -- a last-use read moves out of the slot rather than
    # copying it, which is how a BigInt frame local moves at an async return.
    # Promoted here rather than per-decl because the frame classification is
    # an up-front pass on this path, not a decl-time one.
    # Restricted to names an actual `TpyVarDecl` binds, because the AST's
    # frame promotion lives in `_gen_var_decl`: a for-loop variable that
    # happens to be frame-promoted is bound by the loop arm instead, whose own
    # promotion is gated on a CONSUMING iterable -- promoting it here moved a
    # yielded loop element the AST copies.
    # `_frame_ptr_locals` is this mirror's weak joint: it must equal the AST's
    # `pointer_locals` membership for frame fields, and for at least one
    # classification it does not, so an `Own[record]` frame local moves here
    # and copies there. Align the two memberships to fix it -- the
    # await bind is a red herring, see `_var_decl_names`.
    _frame_ptr_locals = (ptr_frame_locals | opt_ptr_locals | alias_ptr_locals
                         | unpack_ptr_targets)
    _frame_decl_names = _var_decl_names(list(func.body))
    for _lname, _ in (func.generator_locals or []):
        if _lname not in _frame_ptr_locals and _lname in _frame_decl_names:
            lc.promote_movable(_lname)
    # Frame nested defs are struct members callable from EVERY resume
    # case, so their names register up front (the AST's
    # collect_frame_nested_defs pass), not just at their statement.
    for _nd in collect_frame_nested_defs(list(func.body)):
        lc.nested_def_locals.add(_nd.func.name)
    # Pointer-form loop vars and Optional-ptr locals ride the same pointer
    # arms as the Optional-ptr params seeded in _LowerCtx; their writes are
    # not plain-field assigns (skeleton binds / pointer-slot sources), so
    # both stay out of plain_frame_fields.
    lc.pointers.update(ptr_frame_locals)
    lc.pointers.update(opt_ptr_locals)
    # Pointer-alias locals ride the same pointer read arms; their binds
    # render at the dedicated alias leaf arms (never plain field assigns),
    # so they subtract from plain_frame_fields below like every
    # divergent-write family.
    lc.pointers.update(alias_ptr_locals)
    lc.alias_ptr_locals = frozenset(alias_ptr_locals)
    lc.pointers.update(unpack_ptr_targets)
    lc.unpack_ptr_targets = frozenset(unpack_ptr_targets)
    lc.value_tuple_frame_locals = frozenset(value_tuple_locals)
    lc.opt_tuple_holders = frozenset(opt_tuple_holders)
    lc.opt_ptr_frame_locals = frozenset(opt_ptr_locals)
    lc.oneshot_lift_locals = frozenset(rstate.one_shot_lift_names)
    # value_tuple_locals stay IN plain_frame_fields deliberately: they are
    # bare member fields whose reassign is the same plain `name = expr;`
    # render (like value/str/bytes). Every family whose WRITE render
    # differs must subtract itself here.
    lc.plain_frame_fields = frozenset(
        frame_fields - frame_slots - borrow_tuple_locals - coro_handle_slots
        - erased_handle_locals - ptr_frame_locals - opt_ptr_locals
        - alias_ptr_locals - unpack_ptr_targets)
    lc.borrow_tuple_frame_locals = frozenset(borrow_tuple_locals)
    lc.coro_handle_slots = frozenset(coro_handle_slots)
    lc.frame_local_types = dict(gen_local_types)
    # The AST's frame ctx seeds var_types from generator_locals and then
    # OVERWRITES each branch-first-declared name with the sema branch-decl
    # snapshot (`_emit_branch_decls`), whose container types are forced off
    # the fixed-size Array optimization (sibling arms may bind different
    # lengths). Renders spelling the slot must see the same override.
    for _stmt in _iter_nested_stmts(list(func.body)):
        for _bname, _btype in (analyzer.if_branch_decls.get(id(_stmt))
                               or {}).items():
            if _bname in frame_fields and _btype is not None:
                lc.frame_local_types[_bname] = unwrap_ref_type(_btype)
    lc.resumable_leaf_mode = True
    declared: dict[str, TpyType] = {
        n: unwrap_ref_type(t) for n, t in func.params
        if isinstance(t, TpyType)}
    if has_self:
        declared["self"] = self_type  # the record receiver, a field source
        if func.is_readonly:
            # A readonly method's `__self` capture is `const T&`, so a
            # dynamic_cast off it targets `const Sub*` -- the sync seeders'
            # twin (`_poly_cast_context` reads the same const_locals key).
            lc.const_locals.add("self")
    declared.update(handler_bindings)
    # Global seeding, like lower_function's and the ctor entry's. A global
    # is never a frame field, so reads render exactly the sync spellings --
    # bare same-module, qualified native/imported -- with no frame peel,
    # and a `global`-declared name's write renders the same module-slot
    # `g = v;` as a sync body's (the AST resumable emit is
    # function-kind-blind there).
    _seed_global_scope(func, analyzer, lc, declared, native_globals or {})

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
        # Nested walk: a branch-nested first decl registers its frame field
        # too (the dispatch's plain_frame_fields arm lowers it; later BBs
        # read it). Pseudo-statements never nest, so including them in the
        # recursive walk is equivalent to the old top-level scan.
        top_level_ids = frozenset(id(s) for s in bb.stmts)
        for stmt in _iter_nested_stmts(bb.stmts):
            if (isinstance(stmt, TpyVarDecl) and stmt.init is not None
                    and stmt.name in frame_fields
                    and stmt.name not in declared):
                declared[stmt.name] = _var_decl_type(stmt, analyzer)
                # A value-repr `Optional[scalar]` frame field is the same
                # bare `std::optional<T>` binding a sync local declares, but
                # pass 1 has already put it in `declared`, so the decl arm
                # reads as a reassign and never registers it. Register here
                # so its reads take the binding-keyed value-opt arms.
                if _value_opt_scalar(declared[stmt.name], analyzer) is not None:
                    lc.value_opt_bindings[stmt.name] = ValueOptKind.SCALAR
            elif (isinstance(stmt, TpyTupleUnpack)
                    and id(stmt) in top_level_ids):
                # TOP-LEVEL frame-target unpack: register the targets from
                # the sema local types (the same source the frame
                # classification used), so later leaves' reads gate and
                # lower with them. Nested unpacks (for-loop heads inside
                # awaitless leaf loops, compound bodies) stay unregistered
                # -- their arms reject via the declared guard, and
                # registering a loop-shadow head would shift the for-each
                # gate's classification.
                for tname in stmt.targets:
                    if (tname is not None and tname in frame_fields
                            and tname not in declared):
                        lt = gen_local_types.get(tname)
                        if lt is not None:
                            declared[tname] = unwrap_readonly(
                                unwrap_ref_type(unwrap_send_sync(lt)))
                            # Same value-opt keying as the AsyncForAdvance
                            # loop var below: a value-opt-scalar unpack
                            # target's narrowed reads must deref.
                            if _value_opt_scalar(declared[tname],
                                                 analyzer) is not None:
                                lc.value_opt_bindings[tname] = (
                                    ValueOptKind.SCALAR)
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
                    # A value-repr Optional[scalar] loop var reads through
                    # the same binding-keyed arms as a value-opt param
                    # (deref-on-narrow, whole-optional None-test) -- the
                    # resumable twin of the sync foreach registration.
                    # Value-opt narrows have no extraction alias (the AST
                    # derefs in place), so without the binding a narrowed
                    # read falls through to the plain-name arm and renders
                    # bare.
                    if _value_opt_scalar(declared[lv],
                                         analyzer) is not None:
                        lc.value_opt_bindings[lv] = ValueOptKind.SCALAR

    leaves: dict[int, THIRStmt] = {}
    conds: dict[int, THIRExpr] = {}
    await_args: dict[int, tuple[THIRExpr, ...]] = {}
    return_values: dict[int, THIRExpr] = {}
    yield_values: dict[int, THIRExpr] = {}
    suspend_exprs: dict[int, THIRExpr] = {}
    region_exprs: dict[int, THIRExpr] = {}
    match_dispatches: dict[int, THIRStmt] = {}

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
            if stmt.name in borrow_tuple_locals:
                return _lower_borrow_tuple_frame_write(stmt, lc, declared)
            # A concrete-coro handle slot (`c = add_one(1)`) admits ONLY a
            # factory-call source: `_gen_concrete_coro_write`'s call arm is
            # `c.emplace(<gen_expr(call)>)` -- byte-equal to the frame_slot
            # write for a call render -- while its NAME-source arm is the
            # two-statement `emplace(std::move(*src)); src.reset();` pair
            # (and self-write a no-op), render shapes the seam does not
            # mirror yet. coro_factory lifts only the async-callee reject;
            # arg slots and callee kind gate like any call.
            if stmt.name in coro_handle_slots:
                if (isinstance(init, TpyName)
                        and init.name in coro_handle_slots
                        and init.name != stmt.name):
                    # A NAME source move-constructs from the source's
                    # payload and resets it (`d.emplace(std::move(*c));
                    # c.reset();` -- optional's move-assign is deleted
                    # when the frame holds reference members). A
                    # self-write is a Python no-op (the AST emits "").
                    _witness("res.coro_handle_move")
                    return THIRCoroHandleMove(
                        target=stmt.name, source=init.name, loc=stmt.loc,
                        no_source_comment=getattr(stmt, "no_source_comment",
                                                  False))
                if (isinstance(init, TpyName)
                        and init.name == stmt.name):
                    _witness("res.coro_handle_move")
                    return THIRNoOpStmt(loc=stmt.loc)
                if not isinstance(init, (TpyCall, TpyMethodCall)):
                    raise ThirUnsupported("res.coro_handle_source")
                # The decl is a statement position: a generic factory's
                # ref-slot literal temps (`int32_t __tmp_N = 41;`) flush
                # before the emplace, where the AST flushes them.
                value = _lower_expr(
                    init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 coro_factory=True, allow_temps=True))
                _witness("res.coro_handle_write")
                # ConcreteCoroType has no self-contained C++ spelling; the
                # brace-init prefix can never fire for a call render, so no
                # cpp_type is needed (and rendering one would be wrong).
                return THIRFrameSlotWrite(
                    name=stmt.name, value=value, cpp_type=None, loc=stmt.loc,
                    no_source_comment=getattr(stmt, "no_source_comment",
                                              False))
            if stmt.name in erased_handle_locals:
                return _lower_erased_handle_write(stmt, init, lc, declared)
            if stmt.name in frame_slots:
                return _lower_frame_slot_write(stmt, lc, declared)
            # An Optional-ptr frame local writes through the SYNC ladder's
            # reseat arms: pass-1 registered the name, so the sync dispatch
            # sees a reassign and takes the assign-only renders --
            # `x = nullptr;` (reseat.opt_none) and the `x = &(b);` lvalue
            # lift (reseat.opt_lvalue) -- exactly the frame's member-assign
            # shape; off-slice sources keep their named reseat rejects
            # there (the bare member assign would silently DROP the
            # address-of lift, the divergence the probe caught). Rebind-slot
            # holders stay a named rung: the sync arm's `&*(__slot_N = ..)`
            # references a slot only the sync THIRPtrLocalDecl pre-declares
            # -- no such decl exists on a frame.
            # A pointer-ALIAS bind renders the address of the live source
            # lvalue at its own arm (the sync ladder has no alias flavor
            # -- sync bodies bind these as reference DECLS, not member
            # assigns).
            if stmt.name in alias_ptr_locals:
                return _lower_alias_bind(stmt, lc, declared)
            if stmt.name in lc.pointers:
                if (stmt.name in lc.rebind_slot_locals
                        and not _slot_free_ptr_reseat_ok(stmt.init, lc)):
                    # Only RVALUE reseats touch the sync-only `__slot_N`.
                    raise ThirUnsupported("res.leaf_field_write")
                return _lower_stmt(stmt, lc, declared)
            # Plain frame-field write -- the shared position-blind member
            # assign (also the branch-nested decl arm's render). No char/None
            # reject guards it: a reassign or multi-char char literal, and
            # None at a scalar/Char slot, are sema type errors that never
            # reach lowering; a single-char `c: Char = 'a'` first decl
            # renders position-blind identically to the AST frame arm.
            return _lower_frame_field_assign(stmt, lc, declared)
        return _lower_stmt(stmt, lc, declared)

    narrow_envs = (_resume_narrow_envs(cfg, case_entry_ids, frame_fields)
                   if case_entry_ids is not None else {})
    # The driver's per-BB restore record, live while its _lower_bb runs;
    # _apply_leaf_post_if records the declared-types it overrides into it.
    postif_saved: 'list[dict[str, TpyType | None] | None]' = [None]

    def _flat_narrowing_assert(stmt: TpyStmt) -> bool:
        """A top-level narrowing (or re-assert bump) `assert isinstance`:
        the AST's `_gen_assert` emits a persistent extraction inline, which
        only `_lower_stmts`' post-assert arm mirrors -- compound BODIES get
        it, but the flat BB and finally-helper walks lower per-statement and
        would silently miss the alias. Reject in both walks."""
        if not isinstance(stmt, TpyAssert):
            return False
        return (_assert_narrow_info(stmt, declared, analyzer) is not None
                or _reassert_bump_info(
                    stmt, declared, lc.narrow.persistent_narrowed,
                    analyzer) is not None)

    def _apply_leaf_post_if(stmt: TpyIf, leaf: THIRStmt,
                            bb: 'rcfg.BB') -> THIRStmt:
        """Mirror `_lower_stmts`' early-return narrowing arm for a leaf `if`
        (the AST's `_gen_if` emits the persistent extraction inline after
        the close brace and extends the live narrow scope). The scope must
        stay BB-LOCAL: the env walk (`_resume_narrow_envs`) doesn't model
        mid-BB facts, so only a BB whose control leaves the frame (ReturnT /
        RaiseT terminator) admits one -- anything else falls back whole."""
        pf = _chain_post_if_fact(stmt, declared, lc.narrow.narrowed, analyzer)
        if pf is None:
            return leaf
        if not isinstance(bb.terminator, (rcfg.ReturnT, rcfg.RaiseT)):
            raise ThirUnsupported("res.narrowed_resume")
        var, u, post = pf
        if _alias_frame_collision(var, frame_fields):
            raise ThirUnsupported("res.narrowed_resume")
        saved = postif_saved[0]
        assert saved is not None
        lc.narrow = lc.narrow.snapshot()
        alias = _persistent_alias_name(var, lc)
        node = _make_narrow_alias(alias, var, post, u, lc,
                                  getattr(stmt, "loc", None))
        lc.narrow.persistent_aliases.add(alias)
        lc.narrow.narrowed[var] = alias
        lc.narrow.subject_union[var] = u
        lc.narrow.persistent_narrowed.add(var)
        if var not in saved:
            saved[var] = declared.get(var)
        declared[var] = post
        _witness("res.postif_narrow")
        return THIRStmtSeq(stmts=(leaf, node))

    def _lower_bb(bb: 'rcfg.BB') -> None:
        for stmt in bb.stmts:
            if isinstance(stmt, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
                # Sync + async with: only the manager expression renders; the
                # bind wrap / __enter__ / __aenter__ yields are skeleton.
                begin_stmt()
                reason = _with_enter_reject(stmt, analyzer, declared)
                if reason is not None:
                    raise ThirUnsupported(reason)
                region_exprs[id(stmt.item.context_expr)] = (
                    _strip_slot_leaf_deref(
                        _lower_expr(stmt.item.context_expr, lc, declared,
                                    # The owned `__with_ctx_N` emplace is a
                                    # manager sink -- the sync with lane's
                                    # ctx_manager flag (the marker lane's
                                    # record_ret admission).
                                    use=_ExprUse(ctx_manager=True)),
                        lc))
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
                    # The async skeleton owns the indirect deref
                    # ((*(g)).__aiter__(), gen_async's is_indirect wrap)
                    # -- the leaf renders bare.
                    region_exprs[id(stmt.iterable_expr)] = (
                        _strip_slot_leaf_deref(
                            _lower_expr(stmt.iterable_expr, lc, declared,
                                        # The `__for_src` capture consumes
                                        # the render whole -- the sync
                                        # for-head's ITERABLE use.
                                        use=_ExprUse(
                                            result=_ExprResultUse.ITERABLE)),
                            lc))
                else:
                    _lower_for_iter_setup(
                        stmt, func, lc, declared, analyzer, region_exprs)
                _witness("res.for_iter_setup")
                continue
            if isinstance(stmt, rcfg.AsyncFinallyExit):
                # Pure skeleton (pending-return replay + exc rethrow); no leaf.
                continue
            if _flat_narrowing_assert(stmt):
                # The alias the AST appends inline after the assert. It does
                # NOT need to survive the BB: the CFG flows the assert's
                # then_type_facts into every successor's entry_narrowings
                # (resumable_cfg's _active_narrowings), and each resume case
                # re-establishes ALL its stamped facts as `__{var}` -- which
                # is exactly what `_resume_narrow_envs` walks. So the alias
                # is BB-local here, unlike the post-`if` fact, whose scope
                # the env walk does not model.
                av = _assert_narrow_info(stmt, declared, analyzer)
                rv = _reassert_bump_info(
                    stmt, declared, lc.narrow.persistent_narrowed, analyzer)
                nvar = (av or rv or (None,))[0]
                if nvar is None or _alias_frame_collision(nvar, frame_fields):
                    raise ThirUnsupported("res.narrowed_resume")
                # SNAPSHOT before mutating, and record the declared entry --
                # `_append_assert_narrow` mutates lc.narrow IN PLACE and
                # rebinds declared[var] with no restore of its own. The BB
                # driver's `saved_narrow = lc.narrow` holds a REFERENCE, so
                # without the snapshot its restore is a no-op and the
                # narrowing would leak into every later BB in the walk (the
                # driver's own snapshot at the narrowed-BB scope is inside
                # `if env:`, which is empty for the BB that does the
                # asserting). Same discipline as `_apply_leaf_post_if`.
                saved = postif_saved[0]
                if saved is None:
                    raise ThirUnsupported("res.narrowed_resume")
                lc.narrow = lc.narrow.snapshot()
                if nvar not in saved:
                    saved[nvar] = declared.get(nvar)
                leaf = _lower_leaf(stmt)
                post: list[THIRStmt] = []
                _append_assert_narrow(stmt, post, lc, declared)
                if not post:
                    raise ThirUnsupported("res.narrowed_resume")
                _witness("res.flat_assert_narrow")
                leaves[id(stmt)] = THIRStmtSeq(stmts=(leaf, *post))
                continue
            leaf = _lower_leaf(stmt)
            if isinstance(stmt, TpyIf):
                leaf = _apply_leaf_post_if(stmt, leaf, bb)
            leaves[id(stmt)] = leaf
        t = bb.terminator
        if isinstance(t, rcfg.ReturnT):
            ret = t.return_stmt
            _rt = (unwrap_ref_type(func.return_type)
                   if isinstance(func.return_type, TpyType) else None)
            if ret.value is None or isinstance(_rt, VoidType):
                # Void scaffolding is skeleton-only -- a Void slot never
                # renders its value (`return None` included; the AST keys
                # POLL_VOID_READY_RETURN on VoidType, not on the value).
                return
            if isinstance(ret.value, TpyAwait):
                # RETURN-kind await: `_emit_resume_core` fully emits the
                # return from the polled result (`__ret<i>`); the ReturnT
                # on the resume BB is never consumed by the emitter.
                return
            begin_stmt()
            # Value render shared with nested leaf returns (see
            # `_lower_resumable_return_value` for the position-blind
            # contract).
            return_values[id(ret)] = _lower_resumable_return_value(
                ret, lc, declared)
            _witness("res.return_value")
        elif isinstance(t, rcfg.RaiseT):
            leaves[id(t.raise_stmt)] = _lower_leaf(t.raise_stmt)
        elif isinstance(t, rcfg.MatchDispatch):
            # The whole type-aware dispatch (subject + labels + guards)
            # lowers through the sync match tiers with arm BODIES replaced
            # by body_key hooks -- the skeleton walks the arm BBs through
            # its arm emitter at those points, and they lower as ordinary
            # BB leaves in this same loop.
            begin_stmt()
            m_route = _match._select_match_route(
                t.match_stmt, analyzer, declared, frozenset(lc.pointers),
                lc.narrow.narrowed.keys(), lc.storage_tuple_locals,
                lc.prescan, lc, in_branch=False, in_loop=False)
            m_node = _match._lower_match(
                t.match_stmt, m_route, lc, declared, frozenset(lc.pointers),
                getattr(t.match_stmt, "loc", None), arm_body_hooks=True)
            match_dispatches[id(t.match_stmt)] = m_node
            # Hook-mode captures bind frame fields BEFORE the arm's BB walk,
            # so the walk's flat `declared` must carry them (the AST's
            # var_types registration); the frame slot type is authoritative.
            # A capture with no frame entry stays unregistered -- its arm
            # reads keep rejecting fail-closed.
            for m_arm in m_node.arms:
                for m_entry in m_arm.entries:
                    for mb in (*m_entry.field_bindings,
                               *((m_entry.binding,)
                                 if m_entry.binding is not None else ())):
                        ft = lc.frame_local_types.get(mb.name)
                        if ft is not None and mb.name not in declared:
                            declared[mb.name] = ft
            _witness("res.match_dispatch")
        elif isinstance(t, rcfg.Branch):
            # A narrowing isinstance condition takes the sync narrow-cond
            # render (holds_alternative / compound &&); the arm-side scope
            # comes from the arms' stamped entry_narrowings, not from here.
            try:
                info = _narrow_cond_info(t.cond, declared, analyzer)
                pinfo = (None if info is not None
                         else _poly_narrow_info(t.cond, declared, analyzer))
                if info is not None:
                    conds[id(t.cond)] = _lower_narrow_cond(info, t.cond, lc,
                                                           declared)
                elif (pinfo is not None
                      and pinfo[0] not in lc.narrow.narrowed
                      and pinfo[0] not in lc.narrow.spelled):
                    # A POLY isinstance Branch cond renders the no-alias
                    # check (`(dynamic_cast<const Dog*>(&__self) !=
                    # nullptr)`) -- the extraction alias is the ARM
                    # entry's skeleton emission, not this condition's.
                    _witness("res.poly_cond")
                    conds[id(t.cond)] = THIRDynIsinstanceMulti(
                        result_type=analyzer.get_expr_type(t.cond),
                        checks_cpp=_poly_cast_checks(
                            pinfo[0], (pinfo[1],), lc, declared),
                        loc=getattr(t.cond, "loc", None))
                else:
                    conds[id(t.cond)] = _lower_truthy(t.cond, lc, declared)
            except ThirUnsupported:
                raise ThirUnsupported("res.cond") from None
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
                raise ThirUnsupported("res.bare_yield")
            begin_stmt()
            yt = func.generator_yield_type
            # Own peel mirrors the eligibility gate: an Own[container] slot
            # renders like the plain container borrow.
            yt_bare = (_unwrap_own(unwrap_readonly(unwrap_ref_type(
                           unwrap_send_sync(yt))))
                       if isinstance(yt, TpyType) else None)
            if _eligible_char(yt_bare) and isinstance(ys.value, TpyStrLiteral):
                raise ThirUnsupported(stmt_reject_reason(ys))
            if isinstance(yt_bare, TupleType):
                # Tuple yield slot: `gen_yield_value` target-threads the
                # render. A literal takes the value or borrow builder per the
                # slot's element forms (a readonly slot bumps REF elements to
                # const); a borrow-tuple LOCAL name passes bare (not a
                # storage-form source, so `_maybe_wrap_tuple_to_pointer`
                # no-ops). Storage-form sources (the tuple_to_pointer lift)
                # stay a named rung.
                target_ro = isinstance(
                    unwrap_ref_type(unwrap_send_sync(yt)), ReadonlyType)
                yv_src = ys.value
                if isinstance(yv_src, TpyTupleLiteral):
                    if yt_bare.has_pointer_repr_element():
                        yield_values[id(ys)] = _lower_borrow_tuple_literal(
                            yv_src, yt_bare, lc, declared,
                            target_readonly=target_ro)
                    else:
                        # A GENERIC tuple literal (>=1 TypeParamRef element)
                        # renders the spelled `val_or_ptr_t<T>` brace-init
                        # with `to_val_or_ptr` element wraps -- the return
                        # arm's generic builder at the yield slot.
                        gt = _generic_value_tuple_return(yt_bare, analyzer)
                        if gt is not None:
                            yield_values[id(ys)] = \
                                _lower_generic_tuple_literal(
                                    yv_src, gt, lc, declared)
                            _witness("res.btuple_yield_generic")
                        else:
                            # The VALUE-tuple path must pass the same nested
                            # value-tuple predicate its decl/return callers
                            # gate on.
                            vt = _value_tuple_nested(yt_bare, analyzer)
                            if vt is None:
                                raise ThirUnsupported(
                                    "res.btuple_yield_source")
                            yield_values[id(ys)] = _lower_tuple_literal(
                                yv_src, vt, lc, declared)
                elif (isinstance(yv_src, TpyName)
                        and (yv_src.name in borrow_tuple_locals
                             or _bare_yield_tuple_name_ok(
                                 yv_src.name, lc, declared))):
                    yield_values[id(ys)] = _lower_expr(yv_src, lc, declared)
                else:
                    raise ThirUnsupported("res.btuple_yield_source")
                _witness("res.btuple_yield")
                return
            if is_list(yt_bare) or is_dict(yt_bare) or is_set(yt_bare):
                # Container yield slot (val_or_ref<C> in the skeleton's
                # signature): a yielded frame_slot LOCAL hands out the
                # deref borrow -- `return (*buf);` -- the existing
                # frame-slot name read. A TERNARY of frame-slot locals
                # hands out the branch-picked borrow (`((flag) ? ((*a)) :
                # ((*b)))`). Literals/calls at this slot need the
                # target-typed render -- named rung.
                yv_src = ys.value
                if (isinstance(yv_src, TpyIfExpr)
                        and isinstance(yv_src.then_expr, TpyName)
                        and yv_src.then_expr.name in lc.frame_slots
                        and isinstance(yv_src.else_expr, TpyName)
                        and yv_src.else_expr.name in lc.frame_slots):
                    yield_values[id(ys)] = THIRIfExpr(
                        result_type=yt_bare,
                        cond=_lower_truthy(yv_src.condition, lc, declared),
                        then=_lower_expr(yv_src.then_expr, lc, declared),
                        orelse=_lower_expr(yv_src.else_expr, lc, declared),
                        form=Form.BORROW,
                        loc=getattr(yv_src, "loc", None))
                    _witness("res.yield_container_ternary")
                    return
                if not (isinstance(yv_src, TpyName)
                        and yv_src.name in lc.frame_slots):
                    raise ThirUnsupported("res.yield_type")
                yield_values[id(ys)] = _lower_expr(yv_src, lc, declared)
                _witness("res.yield_container_borrow")
                return
            if _f1_record(yt_bare, analyzer):
                # Record yield slot (borrow val_or_ref<R>): a yielded
                # pointer-form loop var or frame_slot NAME hands out the
                # deref borrow (`return (*b);`). Optional-ptr params (may
                # be narrowed to an alias), literals, calls and fields need
                # their own renders -- named rung. The frame_slots half is
                # defensive: sema rejects yielding an OWNING record local
                # by reference ("declare Iterator[Own[R]]"), so pointer-form
                # loop vars are the known-live source.
                yv_src = ys.value
                if (isinstance(yv_src, TpyFieldAccess)
                        and isinstance(yv_src.obj, TpyName)
                        and yv_src.obj.name == "self"
                        and _f1_record(unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(analyzer.get_expr_type(yv_src)))),
                            analyzer)):
                    # A record FIELD off self at the record yield slot reads
                    # bare (`return __self.a;`) -- the storage member binds
                    # the val_or_ref slot directly, no deref (BORROW_BIND,
                    # like the for-head member bind).
                    yield_values[id(ys)] = _lower_expr(
                        yv_src, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                    _witness("res.yield_record_field")
                    return
                if not (isinstance(yv_src, TpyName)
                        and (yv_src.name in lc.frame_slots
                             or yv_src.name in ptr_frame_locals)):
                    raise ThirUnsupported("res.yield_type")
                yv_lowered = _lower_expr(yv_src, lc, declared)
                if isinstance(yv_lowered, THIRName) and not yv_lowered.deref:
                    yv_lowered = replace(yv_lowered, deref=True)
                yield_values[id(ys)] = yv_lowered
                _witness("res.yield_record_borrow")
                return
            yv_valopt = _value_opt_yield_slot(yt)
            if yv_valopt is not None and isinstance(ys.value, TpyNoneLiteral):
                # `yield None` at a value-repr Optional slot: the storage
                # nullopt. SLOT-typed, not NoneType-typed -- a NoneType
                # STORAGE literal renders `std::monostate{}` instead.
                yield_values[id(ys)] = THIRLiteral(
                    result_type=yt, value=None, form=Form.STORAGE,
                    loc=ys.loc)
                _witness("res.yield_value_opt_none")
                return
            # A str-family FIELD at a str yield slot reads bare (`return
            # __case_0.name;`), the same precheck the return sinks thread. A
            # slot needing conversion arrives as a coerce, not a bare field,
            # so this admits only the no-conversion form.
            yv_str_field = (isinstance(ys.value, TpyFieldAccess)
                            and _resolved_str_value(yt_bare,
                                                    analyzer) is not None
                            and _str_field_value_read(ys.value, declared,
                                                      analyzer)
                            and _witness("res.yield_str_field"))
            # SLOT-keyed, never source-keyed: the same narrowed value-opt
            # NAME must deref at an `Int32` slot (`return (*val);`) and pass
            # whole at an `Int32 | None` one.
            yv_lowered = _lower_expr(
                ys.value, lc, declared, field_prechecked=yv_str_field,
                allow_whole_optional=yv_valopt is not None)
            if (isinstance(ys.value, TpyName)
                    and ys.value.name in ptr_frame_locals
                    and isinstance(yv_lowered, THIRName)
                    and not yv_lowered.deref):
                # Pointer-form loop var at a VALUE yield slot: the AST's
                # pointer_value_expr deref (`return (*x);`); the name arm
                # keeps pointer names bare for the arrow/pass positions.
                yv_lowered = replace(yv_lowered, deref=True)
            # The iterator slot owns its str/bytes payload, so a borrow-form
            # value takes the same explicit view->owned copy the return sinks
            # take. A str FIELD read (prechecked above) is already storage form
            # and lands bare, as does a param the frame captured owned.
            if not (isinstance(ys.value, TpyName)
                    and ys.value.name in owned_view_params):
                yv_lowered = _wrap_view_owned_sink(yv_lowered, yt_bare, ys.loc)
            yield_values[id(ys)] = _slot_literal_retype(yv_lowered, yt, lc)
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
            elif payload.prebuilt_slot is not None:
                # Bound-handle await: poll-in-place, zero renders (the
                # INLINE arms below would misread the NAME operand as a
                # call).
                _witness("res.await_prebuilt")
            elif payload.mode is not rcfg.AwaitMode.INLINE:
                # ERASED/BORROWED: the whole operand renders as one
                # expression; the skeleton wraps it (emplace(std::move(..)),
                # &(..), .get(), already-pointer). The prebuilt-slot flavor
                # never reaches here (gated as res.await_prebuilt).
                begin_stmt()
                try:
                    # The emplace is a statement position: arg temps (the
                    # vararg pack's std::array) flush before the suspend
                    # line, exactly where the AST flushes them.
                    suspend_exprs[id(operand)] = _strip_slot_leaf_deref(
                        _lower_expr(
                            operand, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.SUSPEND,
                                         allow_temps=True)),
                        lc)
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
                # The const verdict lives on the RAW fi only (substitution
                # never copies it) -- mirror the AST emplace-arg read.
                dcbp = fi.root.deep_const_borrow_params
                for i, a in enumerate(operand.args):
                    # An awaited callee is a coro factory -- always
                    # frame-capturing for its ref args. The emplace is a
                    # statement position: arg temps (the Own-slot copy+move
                    # `auto __tmp_N = ...;`) flush before the suspend line,
                    # exactly where the AST flushes them.
                    lowered_args.append(_lower_call_arg(
                        a, fi.params[i].type, lc, declared,
                        frame_capturing=True, temp_args=True,
                        readonly_target=(dcbp is not None and i in dcbp)))
                await_args[id(operand)] = tuple(lowered_args)
                if lowered_args:
                    _witness("res.await_args")

    for bb_id in sorted(cfg.blocks):
        bb = cfg.blocks[bb_id]
        env = narrow_envs.get(bb_id) or {}
        # A stamped fact the env walk didn't model (or modeled with a
        # different fact) has no mirrored alias -- fall back whole.
        for v, f in bb.entry_narrowings.items():
            if v not in env or env[v][0] is not f:
                raise ThirUnsupported("res.narrowed_resume")
        saved_narrow = lc.narrow
        saved_decl: 'dict[str, TpyType | None]' = {}
        if env:
            if _rebinds_narrowed(bb.stmts, frozenset(env)):
                raise ThirUnsupported("res.narrowed_resume")
            # Narrowed-BB scope: this BB's leaves lower with narrowed reads
            # renamed to the extraction alias live at its emit position and
            # retyped to the fact (the env fact, not the stamped one -- an
            # inline chain keeps the alias live past a fact pop, e.g. the
            # early-return join). The extraction local itself is skeleton
            # emission, never a leaf.
            lc.narrow = lc.narrow.snapshot()
            for var, (fact, alias) in env.items():
                saved_decl[var] = declared.get(var)
                if var == "self":
                    # The poly-self alias is a SPELLED replacement (the
                    # self read arm consults `spelled`, then falls back to
                    # THIRSelf) -- the reads rename to `__self_narrowed`.
                    lc.narrow.spelled[var] = alias
                else:
                    lc.narrow.narrowed[var] = alias
                declared[var] = fact
            _witness("res.narrow_scope")
        # A mid-BB post-if narrowing (`_apply_leaf_post_if`) records its
        # scope mutations into the same saved_decl; both restore here at
        # the BB boundary (its scope is BB-local by construction).
        postif_saved[0] = saved_decl
        try:
            _lower_bb(bb)
        finally:
            postif_saved[0] = None
            lc.narrow = saved_narrow
            for v, t0 in saved_decl.items():
                if t0 is None:
                    declared.pop(v, None)
                else:
                    declared[v] = t0

    # Helper-based finally bodies live outside cfg.blocks (a member fn per
    # try); lower their statements into the SAME leaves table, keyed by
    # id(stmt). `gen_coro_finally_top_def` emits them through the leaf seam.
    # A `return` inside a helper (async: Poll replay; generator: the
    # __finally_stop path) rejects via res.finally_return, keeping the
    # helper's render nuance out of the leaf-return hook.
    lc.in_finally_helper = True
    try:
        for _helper_name, body_stmts in cfg.finally_helpers:
            for stmt in body_stmts:
                if (isinstance(stmt, TpyIf) and _chain_post_if_fact(
                        stmt, declared, lc.narrow.narrowed,
                        analyzer) is not None):
                    # The AST emits the post-if extraction inside the helper
                    # too; the helper walk has no post-if arm -- fall back.
                    raise ThirUnsupported("res.narrowed_resume")
                if _flat_narrowing_assert(stmt):
                    raise ThirUnsupported("res.narrowed_resume")
                leaves[id(stmt)] = _lower_leaf(stmt)
            _witness("res.finally_helper")
    finally:
        lc.in_finally_helper = False

    # Nested leaf returns (THIRResumableReturn): register their values so
    # the skeleton's `_make_async_return` value render (`render_return_value`,
    # keyed by id(ast)) finds them exactly like ReturnT terminator values.
    for nr in lc.nested_returns:
        if nr.value is not None:
            return_values[id(nr.ast_stmt)] = nr.value

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
    publish_binding_facts(lc)
    return THIRResumableBody(
        leaves=leaves, conds=conds, await_args=await_args,
        return_values=return_values, yield_values=yield_values,
        suspend_exprs=suspend_exprs, region_exprs=region_exprs,
        match_dispatches=match_dispatches)


def _reject(reason: str):
    note(reason)
    return None


def _iter_nested_stmts(stmts):
    """Source-order walk of a statement list INCLUDING compound bodies --
    the pass-1 registration scope for branch-nested frame-field decls (a
    local first-assigned inside an if/match/try arm is still a frame field).
    The attribute set mirrors `_rebinds_narrowed`'s recursion; try_body is
    included because an except-only leaf try routes through the sync tiers
    (res.leaf_try_except), so its decls do reach lowering."""
    for s in stmts:
        yield s
        for attr in ("then_body", "else_body", "body", "orelse",
                     "try_body", "finally_body"):
            sub = getattr(s, attr, None)
            if sub:
                yield from _iter_nested_stmts(sub)
        for h in getattr(s, "handlers", None) or ():
            yield from _iter_nested_stmts(h.body)
        for c in getattr(s, "cases", None) or ():
            yield from _iter_nested_stmts(c.body)
