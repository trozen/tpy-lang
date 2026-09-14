"""Resumable-body (async def) lowering: the leaf half of the seam.

The state-machine SKELETON -- CFG build, frame struct, case labels, region
replay, suspend/resume plumbing -- is shared machinery in `gen_async`,
alongside the other structural emitters (signatures, record layout). What
THIR lowers is every LEAF the skeleton delegates to it: BB leaf
statements, Branch terminator conditions, ReturnT value renders, and the
sub-coro emplace arguments at each suspension.

`lower_resumable` walks the already-built CFG (cached on the function by
`gen_async._build_resumable_cfg`), lowers every leaf through the shared
statement/expression lowering plus the resumable-only rejects below, and
returns a `THIRResumableBody` keyed by the parse-tree nodes the
skeleton holds -- or None (with a `res.*` / composed `stmt.*` reject
reason) when any leaf or frame feature falls outside the slice.

Slice currently lowered:
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
inside THIR emit).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields as dc_fields, replace

from ...identity_map import IdentityMap

from ..reject import (ThirUnsupported, begin_stmt, note, note_detail,
                        stmt_reject_reason)
from ..faces import witness as _witness
from ..validate import validate_resumable_body, validate_stmts
from ..nodes import (
    THIRDynIsinstanceMulti,
    THIRAssign,
    THIRCoroHandleMove,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRFrameSlotWrite,
    THIRNoOpStmt,
    THIRIfExpr,
    THIRMove,
    THIRName,
    THIRResumableBody,
    THIRStmt,
    THIRStmtSeq,
)
from ...parse.nodes import (
    SourceLocation,
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
    TpyNamedExpr,
    TpyNoneLiteral,
    TpyReturn,
    TpyStrLiteral,
    TpyStmt,
    TpySubscript,
    TpyTupleLiteral,
    TpyTupleUnpack,
    TpyVarDecl,
)
from ...typesys import (
    AnyType,
    collapse_tuple_own_elements,
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
from ...type_def_registry import is_borrowing_view_type, is_list
from ...codegen_cpp import emit_prims
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
    _own_return_call_shape,
    check_polymorphic_rvalue_opt_rebind,
)
from .context import (_ExprResultUse, _ExprUse, _LowerCtx,
                      _ONLY_CORO_FACTORY, _Prescan,
                      SinkPos, ValueOptKind)
from .expressions import (
    _is_move_source,
    _poly_cast_checks,
    _lower_call_arg,
    _lower_expr,
    _cond_mixed_walrus_temps,
    _lower_truthy,
    _lower_yield_tuple_literal,
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
    _post_if_narrow_plan,
    _eligible_char,
    _eligible_enum,
    _eligible_scalar,
    _f1_container_ref,
    _f1_record,
    _f1_ref,
    _f1_tuple,
    _generic_value_tuple_return,
    _narrowed_opt_field_read,
    _optional_ptr_borrow,
    _optional_ptr_borrow_wide,
    _own_declared_call_ret,
    _peel_stale_view_owned_coerce,
    _reassert_bump_info,
    _record_class_binding,
    _res_container_return,
    _resolved_bytes_value,
    _resolved_str_value,
    _slot_free_ptr_reseat_ok,
    _str_field_value_read,
    _type_family_tag,
    _wrap_view_owned_sink,
    _unwrap_own,
    _value_opt_callable,
    _value_opt_scalar,
    _value_opt_view,
    _value_tuple,
    _value_tuple_return,
    generic_opt_trait_type,
    unit_opt_instantiation,
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
    _lower_stmts,
    _make_narrow_alias,
    _nested_def_entry_reject,
    _nested_def_lowering_scope,
    _persistent_alias_name,
    _resumable_deferred_recipe,
    _return_carries_value,
    _var_decl_type,
)


def _res_value_ok(t: 'TpyType | None', analyzer) -> bool:
    """Foundation value families: plain value scalars, char, and enums.

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
    - YIELD: the yield slot bridges storage->pointer only for tuple slots
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


def _res_param_ok(t: 'TpyType | None', analyzer, gen_frame: bool) -> bool:
    """Frame-PARAM families (R5c-param). Value scalars plus the reference
    axis: a record or container param captures as a `Record&` /
    `std::vector<T>&` reference frame field and reads bare -- exactly like
    its sync param, so the leaf needs no coro-specific form. str/bytes
    params capture OWNED
    (`std::string` / `std::vector<uint8_t>` frame fields, ctor-copied from
    the sync view param), but every leaf READ renders through the same
    form-agnostic helpers as the owned str/bytes locals R1a already routes
    (`__len__` / `bytes_getitem` / bare name), so they share that slice.

    Own[T] params (`_f1_ref` peels the Own, so an `Own[F1-record]` or
    `Own[container]` payload already reads through the axis branch below; the
    capture `b(std::move(b_))` is skeleton) and pointer-repr
    `Optional[F1-record]` params (`p: P | None` -> a `P*` frame field:
    `p != nullptr` predicates and `p->n` arrow reads route through
    `lc.pointers`, seeded from the params) are also admitted. Borrow-form and
    value tuple params ride too: a `std::tuple<..., T*>` / `std::tuple<...>`
    frame field reads bare with
    `std::get<N>(t)` (pointer-repr elements arrow, value elements bare),
    matching the sync tuple-subscript rows. A bare `T` param rides via
    `_res_capture_ok`. Union (non-pointer-repr) and static-protocol params
    stay their own rungs.

    `gen_frame` says the frame is a GENERATOR's rather than an async body's.
    Only the bare `@dynamic` protocol arm reads it: every other family
    captures the argument itself, while that one captures a borrow of a
    codegen-synthesized adapter temp whose scope the async positions outlive
    (BUGS.md#res-param-dyn-protocol-frame)."""
    if _res_capture_ok(t, analyzer):
        return True
    if (_resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None):
        return True
    # A borrowing view is a VALUE-kind capture: the frame field is the same
    # view the sync param spells (ctor-moved), and every leaf read -- len,
    # subscript, iterate, whole-view forward -- takes the sync rows
    # unchanged. One arm for the whole family because their borrow standing
    # is one fact: the view aliases the caller's storage and the frame
    # neither owns nor copies it, so an argument that outlives the frame
    # keeps the same aliasing the sync call has.
    #
    # What this arm ADDS over the preceding ones: the `Span` flavours
    # (`Span[T]`, `Span[readonly[T]]`, `readonly[Span[T]]`) and the `*args`
    # pack, plus `SpanIter[T]` and the dict views -- which pass here but are
    # refused at the CALL SITE by the argument shape either way. A declared
    # `StrView` / `BytesView` param never reaches here: `_resolved_str_value`
    # / `_resolved_bytes_value` above already answer for it.
    if isinstance(t, TpyType) and is_borrowing_view_type(
            unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))):
        return True
    if _optional_ptr_borrow(t, analyzer) is not None:
        return True
    # A generic `T | None` param: the frame field is the same
    # `opt_param_t<T>` slot the sync signature spells, and every leaf read is
    # already form-neutral -- `*o` and `o->x` read the optional and the
    # pointer form alike, and the None test takes the runtime reader -- so no
    # coro-specific form is needed.
    if generic_opt_trait_type(t) is not None:
        return True
    # ... and its degenerate instantiation `T = None`, which the AWAIT-arg
    # gate meets as the SUBSTITUTED slot (`None | None`). The fixed-int and
    # record instantiations of the same slot are admitted above by the value-
    # opt and optional-ptr families; the unit one has no family of its own,
    # and its capture and reads are the value form's.
    if unit_opt_instantiation(t) is not None:
        return True
    # A raw `Ptr[T]` param is a VALUE-kind capture, so the frame field is the
    # bare `T*` the sync param already spells and every leaf read takes the
    # sync pointer rows -- the same standing the Ptr LOCAL slot has.
    if _eligible_ptr_value(t, analyzer):
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
    # ... and the CALLABLE inner (`onerror: Callable[..] | None`): the frame
    # field is the same moved `std::optional<std::function<..>>` the
    # non-optional callable param below captures, with the optional's own
    # value-opt read arms on top.
    if _value_opt_callable(t, analyzer) is not None:
        return True
    if (_f1_tuple(t, analyzer) is not None
            or _value_tuple(t, analyzer) is not None):
        return True
    unwrapped = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                 if isinstance(t, TpyType) else None)
    # The reference axis in one admission: a record param captures as a
    # `Record&` reference frame field, a container param as the same
    # `std::vector<T>&`, and every leaf read/write/pass takes the sync rows
    # unchanged; element-shape gating stays at the use sites. An
    # Own[container] param differs only in capture (the skeleton's
    # OWNED_VALUE move -> a `std::vector<T>` value field); reads are the
    # same bare container rows, so it rides the same admission -- `_f1_ref`
    # peels the Own for both halves.
    if unwrapped is not None and _f1_ref(unwrapped, analyzer):
        return True
    # An `Own[value]` / `Own[T]` param is the bare-value capture with
    # ownership transfer: the frame field is the payload spelling itself
    # (`T item;` / `int32_t x;`), the ctor takes it as `T&&` and member-inits
    # with std::move -- all skeleton -- so every leaf read is the same bare
    # name the sync param spells and the value families' read rows apply
    # unchanged. Own[str]/Own[bytes] stay out: their sync param is a VIEW
    # while the frame field would be OWNED, a form split rather than a
    # capture detail.
    if (isinstance(unwrapped, OwnType)
            and _res_capture_ok(unwrap_readonly(unwrapped.wrapped),
                                analyzer)):
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
    # capture rationale. CAPTURE position only: protocol locals stay gated.
    if unwrapped is not None and is_protocol_type(unwrapped):
        if not is_dyn_protocol(unwrapped):
            return True
        # A bare / `readonly` @dynamic protocol param captures as the
        # skeleton's REF field (`Src&` / `const Src&`, the record-borrow
        # shape) and every leaf read is the bare vtable call the sync param
        # already spells. GENERATOR frames only: the borrow may alias a
        # call-site `RefAdapter` temp rather than the argument, and no handle
        # that borrows one can outlive it today. The adapter is emitted as a
        # NAMED local of the block that holds the handle, immediately before
        # it (`RefAdapter<Src, Impl> __tmp_1{src}; auto g = free_gen(__tmp_1);`),
        # so it is destroyed after every handle in that block -- including a
        # copy into a sibling local, which shares the block (that copy copies
        # the frame, BUGS.md#generator-object-binds-copy-the-frame, and is
        # safe for the same scope reason). Returning the handle is refused
        # outright: `Iterator` is not a legal return type. The two ways it
        # could leave the block -- a container literal holding it
        # (`expr.container_literal`) and a re-seat into an outer binding, the
        # loop-body-creates / outer-pulls shape
        # (`stmt.var_decl:reseat.opt_storage_source`) -- are CODEGEN GAPS, not
        # escape checks, so a lowering arm for either has to re-examine this
        # admission. An async frame already leaves (a `create_task`ed frame
        # dangles even on a module global, and an inline `await` drops the
        # adapter wrap outright), so it keeps its reject --
        # BUGS.md#res-param-dyn-protocol-frame.
        return gen_frame
    # An OWN-wrapped static protocol (`s: Own[Sink]`) rides that same
    # monomorphized capture: the field is the deduced `T_s s;` and the ctor
    # member-inits `s(std::forward<T_s>(s_))`, so the ownership follows the
    # ARGUMENT's value category -- and an `Own` slot's call-arg render is
    # already `std::move(...)`, which deduces `T_s` to a value and makes the
    # frame own. The lvalue-borrow risk lives at the AWAIT-arg forward into a
    # sub-future, which re-deduces off the bare name and keeps its own reject
    # (BUGS.md#own-static-protocol-frame-capture-borrows).
    if (isinstance(unwrapped, OwnType)
            and is_protocol_type(unwrapped.wrapped)
            and not is_dyn_protocol(unwrapped.wrapped)):
        return True
    # An `Own[@dynamic P]` param captures as a bare `unique_ptr<P>` frame
    # field (the ctor's `p(std::move(p_))` is skeleton), and its leaf reads
    # are the forward arg renders (`std::move(p)` at a same-protocol Own
    # slot) -- gated per-shape at the arg rows. RAW wrapped, matching the
    # await-slot key: `Own[readonly[P]]` never takes the adapter renders.
    if (isinstance(unwrapped, OwnType)
            and is_dyn_protocol(unwrapped.wrapped)):
        return True
    # An `Any` param is a bare `::tpy::Any` value field (position-blind
    # copy capture, skeleton); leaf reads gate per-shape at the Any arms
    # (truthiness rides the shared to_bool render). Any LOCALS keep
    # res.local_storage.
    if isinstance(unwrapped, AnyType):
        return True
    # A pointer-repr union param's frame field is the SAME pointer-variant
    # shape as the sync param (`::tpy::Union<A*, B*>`), so reads, isinstance
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
    targets -- the names frame-local promotion applies to: a declaration
    promotes the name it binds, a tuple unpack each of its targets.

    Do NOT also exclude await inits: tried, and it breaks `await_in_with` +
    `await_in_with_multi_cm`, because a BigInt bound by `x = await f()` IS
    promoted and moves at its return."""
    out: 'set[str]' = set()
    for s in stmts:
        if isinstance(s, TpyVarDecl):
            out.add(s.name)
        elif isinstance(s, TpyTupleUnpack):
            out.update(n for n in s.targets if n is not None)
        for body in s.sub_bodies():
            out |= _var_decl_names(body)
    return out


def _first_var_decls(stmts: list,
                     out: 'dict[str, TpyVarDecl] | None' = None
                     ) -> 'dict[str, TpyVarDecl]':
    """Every name's source-first `TpyVarDecl`, anywhere in the body.

    A frame local is named by the layout plan, not by a statement, so a
    verdict that belongs to the DECL has to find its statement again. Built
    for the whole body at once: a per-name search would rewalk the body once
    per frame local."""
    if out is None:
        out = {}
    # Source order is statement-then-its-sub-bodies, and only the first
    # binding of a name counts, so a later decl must never overwrite it.
    for s in stmts:
        if isinstance(s, TpyVarDecl) and s.name not in out:
            out[s.name] = s
        for body in s.sub_bodies():
            _first_var_decls(body, out)
    return out


def _bare_yield_tuple_name_ok(name: str, lc: '_LowerCtx',
                              declared: dict[str, TpyType]) -> bool:
    """A tuple NAME the yield hands out BARE (`return v;` / `return p;`):
    the `tuple_to_pointer` lift no-ops for a non-storage-form
    source. Admitted order-independently by TYPE, not by the
    lowering-order-populated storage sets: a VALUE tuple (no
    pointer-repr element -- the wrap no-ops on the target alone), or a
    BORROW-form tuple PARAM (not Own-wrapped, not owned-movable -- the
    shapes seed_param_locals registers storage-form stay out). Locals with
    pointer-repr elements keep the `res.btuple_yield_source` reject, as do
    pointer-form names."""
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


def _bare_yield_param_ok(name: str, yt_bare: TpyType, lc: '_LowerCtx',
                         declared: 'dict[str, TpyType]') -> bool:
    """Whether a non-value PARAM yielded at its own slot type reads BARE.

    The frame captures such a param as a reference member (`std::vector<T>&
    xs;`, `P& b;`), so the plain name read already IS the borrow the
    `val_or_ref` slot wants -- no deref, no lift. Pointer-form and frame-slot
    names have their own arms; a narrowed name reads its alias; and an
    `Own[...]` param owns frame storage under a declared type that no longer
    equals the slot, so it stays out by the type check."""
    if name not in lc.prescan.param_names:
        return False
    if (name in lc.frame_slots or name in lc.pointers
            or name in lc.narrow.narrowed or name in lc.narrow.spelled):
        return False
    dt = declared.get(name)
    if dt is None:
        return False
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))) == yt_bare


def _alias_frame_collision(var: str, frame_fields: 'set[str]') -> bool:
    """Whether the `__{var}` extraction alias would collide with a real frame
    field (or `self`). On a collision the skeleton bumps the alias name
    (`fresh_alias_local`'s `__{var}_narrowed` rename), which none of the THIR
    narrowing arms reproduce -- so every one of them rejects the shape
    instead. Shared so the three arms cannot drift apart."""
    return f"__{var}" in frame_fields or var == "self"


def _resume_alias_name(var: str, frame_fields: 'set[str]') -> str:
    """The extraction alias the skeleton emits for a resume-narrowed var:
    `__{var}`, bumped to `__{var}_narrowed` on a frame-field collision
    (`fresh_alias_local`'s rename -- `self`'s `__self` always collides
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
    the shape `emit_isinstance_extractions` re-establishes with a plain
    `std::get` alias whose name the lowering can mirror (`__{var}`, the
    non-persistent `fresh_alias_local`). Everything else -- the polymorphic
    self/subclass dynamic_cast family, Optional `is not None`, literal /
    protocol / Any facts, narrowed-to-smaller-union -- keeps the named
    reject. Without the skeleton's case-entry set the match-arm alias
    environments can't be mirrored, so every narrowed body rejects."""
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
            # -- emit_isinstance_extractions' poly arm with the
            # fresh_alias_local bump), and the leaves read the SPELLED
            # alias -- no cross-BB state.
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


def _rebound_names(stmts, names: 'frozenset[str]') -> 'set[str]':
    """The subset of `names` directly rebound anywhere in a BB's leaf
    subtree (including non-suspending compounds). Field/subscript writes
    mutate THROUGH the alias and keep the narrowing. The generic body walk
    over-matches unknown compound kinds on purpose: a false positive only
    costs a reject, never a wrong render."""
    out: set[str] = set()
    for s in stmts:
        if isinstance(s, TpyVarDecl):
            if s.name in names:
                out.add(s.name)
        elif isinstance(s, (TpyAssign, TpyAugAssign)):
            if isinstance(s.target, TpyName) and s.target.name in names:
                out.add(s.target.name)
        elif isinstance(s, TpyTupleUnpack):
            out |= {t for t in s.targets if t is not None and t in names}
        elif isinstance(s, TpyDelVar):
            out |= {n for n in s.names if n in names}
        elif isinstance(s, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
            if s.item.target in names:
                out.add(s.item.target)
        for attr in ("then_body", "else_body", "body", "orelse",
                     "finally_body"):
            b = getattr(s, attr, None)
            if b:
                out |= _rebound_names(b, names)
        for h in getattr(s, "handlers", None) or ():
            if h.body:
                out |= _rebound_names(h.body, names)
        for c in getattr(s, "cases", None) or ():
            if c.body:
                out |= _rebound_names(c.body, names)
    return out


def _rebinds_narrowed(stmts, names: 'frozenset[str]') -> bool:
    return bool(_rebound_names(stmts, names))


def _expr_reads_name(e, names: 'frozenset[str]') -> bool:
    """Any bare NAME read of `names` anywhere under `e`."""
    if isinstance(e, TpyName):
        return e.name in names
    for f in dc_fields(e):
        v = getattr(e, f.name)
        if isinstance(v, TpyExpr) and _expr_reads_name(v, names):
            return True
        if isinstance(v, list):
            for item in v:
                if isinstance(item, TpyExpr) and _expr_reads_name(item, names):
                    return True
    return False


def _narrow_kill_plan(stmts, names: 'frozenset[str]'
                      ) -> 'dict[int, frozenset[str]] | None':
    """Per-statement narrow-KILL map for a BB whose leaves rebind narrowed
    names: {id(stmt): killed} for TOP-LEVEL simple rebinds (VarDecl / Assign
    / TupleUnpack) whose own value does not READ the killed name -- the
    write targets the raw variant, so the leaves past the kill read the
    bare binding. None = a shape with no kill plan (a rebind nested in a
    compound, a with/aug target, a value reading the killed name, a del)
    -- the BB rejects whole.
    {} = no rebind at all."""
    plan: dict[int, frozenset[str]] = {}
    for s in stmts:
        killed: set[str] = set()
        value = None
        if isinstance(s, TpyVarDecl):
            if s.name in names:
                killed.add(s.name)
                value = s.init
        elif isinstance(s, TpyAugAssign):
            if isinstance(s.target, TpyName) and s.target.name in names:
                return None  # reads its own target
        elif isinstance(s, TpyAssign):
            if isinstance(s.target, TpyName) and s.target.name in names:
                killed.add(s.target.name)
                value = s.value
        elif isinstance(s, TpyTupleUnpack):
            killed |= {t for t in s.targets if t is not None and t in names}
            value = s.value
        elif isinstance(s, TpyDelVar):
            if any(n in names for n in s.names):
                return None
        elif isinstance(s, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
            if s.item.target in names:
                return None
        if _rebinds_narrowed(
                [b for attr in ("then_body", "else_body", "body", "orelse",
                                "finally_body")
                 for b in (getattr(s, attr, None) or ())]
                + [st for h in (getattr(s, "handlers", None) or ())
                   for st in (h.body or ())]
                + [st for c in (getattr(s, "cases", None) or ())
                   for st in (c.body or ())], names):
            return None
        if killed:
            if value is not None and _expr_reads_name(value,
                                                      frozenset(killed)):
                return None
            plan[id(s)] = frozenset(killed)
    return plan


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
        # A rebind of a narrowed name anywhere in this BB kills its fact on
        # every OUT edge (nothing re-establishes it: the dead-alias case
        # entries carry only sema's stamped facts, which already dropped
        # the killed name). The BB's own entry env keeps the
        # fact for the leaves BEFORE the rebind; _lower_bb pops it at the
        # rebinding leaf.
        killed = _rebound_names(bb.stmts, frozenset(env))
        if killed:
            env = {v: a for v, a in env.items() if v not in killed}

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


def _loop_var_bind_ok(elem_type: 'TpyType | None', name: str, analyzer,
                      borrow_tuple_names: 'set[str]',
                      value_tuple_names: 'set[str]') -> bool:
    """The loop-var bind shapes admitted at BOTH loop seams -- the sync
    advance and its `async for` twin. A value-scalar / str / bytes element
    binds bare into a plain frame field; a borrow-tuple or value-tuple name
    binds the shape the frame LAYOUT classified it as, so both seams read
    that verdict rather than re-deriving a tuple form from the bind type.
    Families the sync skeleton alone spells (pointer-form and frame_slot
    loop vars) stay at their own seam."""
    if _res_local_ok(elem_type, analyzer):
        return True
    if name in borrow_tuple_names:
        # Proxy-ref holder (dict_items): the advance binds the borrow-form
        # tuple via tuple_to_pointer (skeleton) and reads ride the
        # borrow-tuple family off the frame field it re-binds.
        _witness("res.loop_btuple_bind")
        return True
    if name in value_tuple_names:
        # An ALL-VALUE tuple whose source cannot be address-taken (the
        # dict_items proxy): the bind is bare into the plain field and
        # element reads are the sync std::get rows. A tuple of values is
        # unobservably a copy, so the bind loses no aliasing; its
        # address-takeable sibling is a pointer-form loop var instead.
        _witness("res.loop_value_tuple_bind")
        return True
    return False


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
    type. Borrow-tuple loop vars admit in both the whole-tuple and
    head-unpack forms (the advance bind is skeleton; reads ride the
    borrow-tuple family); holders outside every admitted set keep the
    reject."""
    stmt = t.stmt
    elem_type = _loop_elem_type(stmt, analyzer)
    if _res_local_ok(elem_type, analyzer):
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
    if _loop_var_bind_ok(elem_type, stmt.var, analyzer,
                         borrow_tuple_loop_vars, value_tuple_holders):
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
    render shape: range lowers each bound separately; every other strategy
    lowers the whole iterable ONCE, reused by the advance's re-render when
    there is no `__for_src` field. Rejects a narrowed-optional iterable
    whose leaf cannot supply the bare source the skeleton's `(*v)` unwrap
    expects."""
    it = stmt.iterable_expr
    info = rcfg.resumable_state(func).for_info_by_uid.get(stmt.uid)
    strat = info.strategy if info else "iter_next"
    if strat == "range":
        fi = getattr(it, "resolved_function_info", None)
        if fi is None:
            raise ThirUnsupported("res.for_range_shape")
        for i, arg in enumerate(it.args):
            region_exprs[arg] = _lower_call_arg(
                arg, fi.params[i].type, lc, declared)
        return

    # ITERABLE result use, matching the sync for-head: the skeleton's
    # source capture consumes the render whole (position-blind), so
    # generator-factory calls admit here exactly like the sync route
    # (their render is the same bare call). The setup is a statement
    # position -- arg temps (`int32_t __tmp_N = 7;` ref-slot literals of a
    # generic factory call) flush before the source capture.
    lowered_it = _lower_expr(
        it, lc, declared, use=_ExprUse(result=_ExprResultUse.ITERABLE,
                                       allow_temps=True))
    if _for_iterable_narrowed_optional(it, declared, analyzer):
        # The begin_end skeleton owns the narrowed value-Optional unwrap
        # (`maybe_unwrap_narrowed_optional` over the leaf render), so the
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
        elif (isinstance(lowered_it, THIRName)
                and isinstance(it, TpyName) and it.name in lc.pointers
                and _optional_ptr_borrow_wide(declared.get(it.name),
                                              analyzer) is not None):
            # A narrowed PTR-repr container local (`P* h` -- the OPT_PTR
            # container flavor): no skeleton unwrap exists for the pointer
            # form, so the LEAF carries the deref (`((*h)).begin()`); the
            # ITERABLE name row lowers it bare, so re-add it here.
            _witness("res.for_narrowed_opt_ptr_src")
            lowered_it = replace(lowered_it, deref=True)
        else:
            raise ThirUnsupported("res.for_narrowed_optional")
    region_exprs[it] = lowered_it


def _with_enter_reject(stmt: 'rcfg.WithEnter | rcfg.AsyncWithSetup', analyzer,
                       declared: dict[str, TpyType]) -> 'str | None':
    """WithEnter / AsyncWithSetup admission (R6-with / R5-async-with). The
    bind's emplace / &(..) wrap and the __enter__ / __aenter__ yields are
    skeleton; the manager expression is the one leaf render, so admit exactly
    the manager families the sync `_lower_with` gates (borrowed F1-record
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
    composes the reject reason). Not lowered yet: generic awaited callees,
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
        # never takes the adapter render.
        own_dyn_slot = (isinstance(pt_u, OwnType)
                        and is_dyn_protocol(pt_u.wrapped))
        # An `Own[value]` slot (a generic Own[T] param at a value
        # instantiation, e.g. a Queue[int32] put): the emplace arg is the sync
        # call-arg render (arg temp + std::move), so it rides the same
        # `_lower_call_arg` rows.
        own_val_slot = (isinstance(pt_u, OwnType)
                        and _res_value_ok(unwrap_readonly(pt_u.wrapped),
                                          analyzer))
        if (isinstance(pt_u, OwnType)
                and _eligible_enum(unwrap_readonly(pt_u.wrapped),
                                   analyzer) is not None):
            # An ENUM payload is the one value family the sync Own-arg row
            # leaves unmodelled (a sync call rejects it outright), and no
            # call admission runs at this position -- admitting it here or
            # from the CAPTURE families below would render the arg BARE,
            # without the copy temp the enum payload needs.
            return "res.await_param_type"
        if (isinstance(pt_u, OwnType)
                and is_protocol_type(pt_u.wrapped)
                and not is_dyn_protocol(pt_u.wrapped)):
            # An `Own[static P]` slot clears the frame-PARAM families, but the
            # AWAIT position captures differently: the sub-future field is
            # spelled `__coro_inner<await_arg_capture_t<decltype((s))>>` and
            # the emplace re-deduces off the bare name, so an lvalue argument
            # makes the sub-frame BORROW what the slot says it owns
            # (BUGS.md#own-static-protocol-frame-capture-borrows). Carved out
            # here rather than left to the families below, which now admit it.
            return "res.await_param_type:own_protocol.static"
        # `gen_frame=False`: an awaited callee is never a generator, and the
        # sub-coro emplace leaf is exactly the path that drops a @dynamic
        # param's adapter wrap, so that arm must stay shut here.
        if not (own_dyn_slot or own_val_slot
                or _res_param_ok(pt, analyzer, gen_frame=False)):
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


def _check_suspending_poly_match(cfg: 'rcfg.CFG') -> None:
    """Refuse a `match` on a @dynamic / polymorphic subject that suspends.

    A suspending match becomes a `MatchDispatch` terminator, and a
    polymorphic one dispatches through a `dynamic_cast` chain that has no
    seam to route arm bodies back through the state machine -- so no
    lowering of the enclosing body is correct, on either emit path. That
    makes it a verdict rather than an admission decision, so it runs ahead of
    every admission gate below: rejecting the body instead would only defer
    the user-facing message to whichever layer emits it next, and the gates
    it would have to clear first are unrelated to the shape diagnosed here.

    A poly match with no suspension in it is untouched -- it is an ordinary
    statement in some block, dispatches inline, and compiles."""
    for bb_id in sorted(cfg.blocks):
        term = cfg.blocks[bb_id].terminator
        if (isinstance(term, rcfg.MatchDispatch)
                and term.match_stmt.polymorphic_dispatch):
            emit_prims.reject_suspending_polymorphic_match(
                term.match_stmt.loc)


def lower_resumable(func: TpyFunction, analyzer, render_type,
                    cfg: 'rcfg.CFG',
                    record_name: 'str | None' = None,
                    render_type_stored=None,
                    case_entry_ids: 'frozenset[int] | None' = None,
                    native_globals: 'Mapping[str, str] | None' = None,
                    frame_layout: 'rcfg.FrameLayoutPlan | None' = None,
                    ) -> 'THIRResumableBody | None':
    """Lower a resumable body, or None on a lowering reject."""
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
        return _reject(ex.reason, ex.loc)


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
    re-derived) so the skeleton and the leaves cannot disagree on the form;
    None (unit callers) rejects every body with hoisted locals.

    `case_entry_ids` is the skeleton's case-label set (`_compute_case_entries`
    keys, cached on the CFG): the emit positions that re-establish narrowing
    aliases as `__{var}`. Reused (vs re-derived) so the narrowed-BB alias
    environments match the walker exactly; None (unit callers) rejects
    every narrowed body."""
    _check_suspending_poly_match(cfg)
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
        return _reject(ex.reason, ex.loc)
    # A generic frame (`async def f[T]` / a coro method on a generic record)
    # needs no gate of its own: the template header, and the value-vs-reference
    # frame-field choice (`val_or_ref_t<T>`), are skeleton -- every leaf reads
    # the field bare, identically for both. What a T can appear IN is gated by
    # the per-slot param / return / yield families below.
    for _pname, ptype in func.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _res_param_ok(pt, analyzer, gen_frame=is_generator):
            pt_u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
                    if pt is not None else None)
            if pt_u is not None and is_dyn_protocol(pt_u):
                # Admitted on a generator frame, so the residual is the async
                # half alone (the adapter temp's lifetime, not the capture
                # shape) and gets its own landmark rather than sharing the
                # family's -- BUGS.md#res-param-dyn-protocol-frame.
                return _reject("res.param_type:protocol.dyn.async")
            # The family names WHICH param slot blocked; the bare landmark
            # collapses unrelated capture shapes into one tally line.
            return _reject(f"res.param_type:{_type_family_tag(pt, analyzer)}")
    if is_generator:
        # The yielded element type gates a generator (its `return_type` is
        # `Iterator[T]`, not a value slot). Value scalars, a bare `T`, and
        # str/bytes route (the owned return slot's ctor absorbs the bare
        # source render; the
        # slot-literal retype in the Yield arm threads the yield type into
        # the render). Records are interlocked with the non-value
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
        # A VALUE-repr Optional yield slot (`Iterator[int32 | None]`) is the
        # whole `std::optional<T>` by value, so it admits on its INNER's
        # capture -- the yield sink then passes the optional bare (no deref,
        # no monostate). Scoped to this gate: `_res_param_ok` and the async
        # return read the same predicate and keep their own slot rules.
        yt_valopt = _value_opt_yield_slot(yt_t)
        if not (_res_capture_ok(yt_t, analyzer)
                # ... and the Own-peeled capture families: an `Own[T]` slot
                # renders like the bare `T` it wraps, the ownership fact
                # living in the consumer's loop-var binding exactly as it
                # does for the container and record slots peeled below.
                or _res_capture_ok(yt_tuple, analyzer)
                or (yt_valopt is not None
                    and _res_capture_ok(yt_valopt.inner, analyzer))
                or _resolved_str_value(yt_t, analyzer) is not None
                or _resolved_bytes_value(yt_t, analyzer) is not None
                # Tuple slots gate per-yield in the Yield arm (literal
                # builder / borrow-name pass-through); the slot family alone
                # admits.
                or isinstance(yt_tuple, TupleType)
                # Reference-axis slots gate per-yield: a container at the
                # frame-slot borrow-name arm, a record at the routed
                # loop-var / frame_slot NAME borrow deref. Other value
                # shapes reject there.
                or _f1_ref(yt_tuple, analyzer)):
            return _reject("res.yield_type")
    else:
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        rt_inner = (unwrap_readonly(unwrap_send_sync(rt))
                    if rt is not None else None)
        # Owned str/bytes returns ride the shared form-keyed wrap
        # (`_wrap_view_owned_return`): the wrap fires iff the source is
        # view-form, which the lowered value carries as its form fact -- the
        # same wrap every sync str return takes. One render serves all
        # three async scaffolding
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
    # (the `generator_storage_name` substitution). Admitted whenever
    # each backing IS a param; lc.forwarded_map carries the swap.
    if func.forwarded_locals:
        param_names = {n for n, _t in func.params}
        if not all(backing in param_names
                   for backing in func.forwarded_locals.values()):
            return _reject("res.forwarded_local")
    # Classify each hoisted local off its FrameLayoutPlan verdict (the
    # skeleton's own placement decision, passed through the seam); a
    # verdict whose READ/WRITE render family is not lowered yet rejects
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
    # Pointer-form loop vars over a TUPLE element: the field points at the
    # source element's STORAGE tuple, so element reads are the value form
    # (`std::get<1>((*t)).v`) off the deref'd pointer. The `__for_tup_*`
    # unpack holder is excluded -- its reads are the head unpack's own
    # skeleton render, not the subscript family.
    ptr_storage_tuple_loop_vars: set[str] = set()
    for f_info in rstate.for_info_by_uid.values():
        if f_info.pointer_form_loop_var is not None:
            ptr_frame_locals.add(f_info.pointer_form_loop_var)
        if f_info.borrow_tuple_loop_var is not None:
            borrow_tuple_loop_vars.add(f_info.borrow_tuple_loop_var)
        # Tuple-unpack targets aliasing a non-value container member: `T*`
        # fields the head unpack re-points via `= &(std::get<i>(__tup_N));`
        # -- the skeleton's pointer_form_unpack_targets seeding.
        unpack_ptr_targets.update(f_info.pointer_form_unpack_targets)
    for lname, ltype in (func.generator_locals or []):
        if (lname in ptr_frame_locals
                and not lname.startswith("__for_tup_")
                and isinstance(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ltype))), TupleType)):
            ptr_storage_tuple_loop_vars.add(lname)
    frame_slots: set[str] = set()
    mixed_tuple_slots: set[str] = set()
    coro_handle_slots: set[str] = set()
    erased_handle_locals: set[str] = set()
    borrow_tuple_locals: set[str] = set()
    owning_tuple_slots: set[str] = set()
    _K = rcfg.FrameLocalKind
    # Whole-body scans, independent of any single local -- the classification
    # loop below only reads them, so they must not be rebuilt per local.
    _local_prescan = _Prescan(func, analyzer)
    _body_var_decls = _first_var_decls(func.body)
    # Per-element ownership the declared type cannot spell (a literal-bound
    # owning / mixed tuple slot). Collected for every kind so the read chooser
    # and the slot write agree with the field the skeleton emitted.
    frame_own_tuple_types: dict[str, TpyType] = {
        lname: v.effective_type
        for lname, v in frame_layout.bindings.items()
        if v.effective_type is not None}
    for lname, ltype in (func.generator_locals or []):
        kind = frame_layout.bindings[lname].kind
        if kind in (_K.VALUE, _K.OWNED_STR):
            # Bare value field (R1a: value scalars / str / bytes /
            # value-opt scalars). An OWNED_STR field is `std::string`, but
            # its reads/writes are the same sema-resolved str renders the
            # view field takes. Value types beyond the admitted families
            # (value records, Ptr, char arrays) have no lowered render.
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
        if kind is _K.MIXED_TUPLE_SLOT:
            # The owning slot of a MIXED tuple (`frame_slot<std::tuple<Box,
            # Box*>>`): writes emplace like any frame_slot; element reads
            # split per element off the deref'd slot -- `.` for the owned
            # element, `->` for the borrowed pointer -- keyed on the
            # ELEMENT types exactly like the sync mixed alias's chooser
            # (`_subscript_yields_borrow_ptr`), read directly off the
            # element types rather than off a storage-form membership set.
            frame_slots.add(lname)
            mixed_tuple_slots.add(lname)
            continue
        if kind is _K.SOURCE_FORM_SLOT:
            # The field's payload comes from the iteration source, so the
            # slot holds whichever form the trait picked; reads go through
            # the frame_slot deref either way, even for a value element.
            frame_slots.add(lname)
            continue
        if kind is _K.OPT_PTR:
            # The polymorphic-rebind verdict belongs to the decl, not to the
            # frame placement, so it is reached whether or not this local's
            # field form is one the leaves can render.
            _opt_decl = _body_var_decls.get(lname)
            check_polymorphic_rvalue_opt_rebind(
                lname, ltype,
                _opt_decl.init if _opt_decl is not None else None,
                _local_prescan.rvalue_reassigned, analyzer)
            # Pointer-repr Optional[NonValue] local (`P* x = nullptr;`
            # field): reads ride lc.pointers (null tests + arrow), writes
            # are bare `=` from P*-shaped sources -- the local twin of the
            # Optional-ptr param admission. Kept OUT of ptr_frame_locals:
            # the loop-var-only arms (advance admission, value-yield deref,
            # record-yield names) must not see it. CONTAINER inners join
            # the F1 records at this LOCAL arm only (`const std::vector<
            # int32_t>* h;` -- the optional_to_ptr bind + `(*h)` for-head
            # deref); `_optional_ptr_borrow` itself stays narrow -- many
            # binding-level consumers key F1 renders on it.
            _opt_b = _optional_ptr_borrow(ltype, analyzer)
            if _opt_b is None:
                _ow = _optional_ptr_borrow_wide(ltype, analyzer)
                if _ow is not None:
                    if _f1_container_ref(unwrap_readonly(_ow.inner)):
                        _opt_b = _ow
            if _opt_b is not None:
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
            # its own write shapes: a CONCRETE handle (`c = add_one(1)`)
            # writes `c.emplace(<factory call>)` -- the frame_slot shape,
            # admitted with a factory-call-only init gate in the decl arm;
            # an ERASED handle (`unique_ptr<P>` field) writes `=` through
            # the make_adapter wrap, a render family the leaves do not
            # lower -- reject.
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
            # Non-plain slot types (Callable etc.) have no lowered
            # emplace/read render.
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
        # this path) and any future kind: no lowered render family.
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
            # handle contract). The borrow-tuple set is the frame LAYOUT's
            # own BORROW_TUPLE verdict joined with the dict_items proxy-ref
            # producer: an address-takeable (`begin_end`) tuple loop var is
            # classified PTR_ALIAS, never BORROW_TUPLE, so the join cannot
            # re-route one away from its aliasing field.
            reason = _for_advance_reject(
                t, analyzer, ptr_frame_locals,
                frame_slots - coro_handle_slots,
                borrow_tuple_loop_vars | borrow_tuple_locals,
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
                        # async-for loop var: the shared bind admission (the
                        # skeleton's ASSIGN bind is a plain frame write for
                        # the value/str/bytes slice; non-value uses .emplace
                        # on an optional field). A VALUE-tuple unpack HOLDER
                        # (`async for k, sq in p:`) is the same bare-field
                        # ASSIGN bind; its head unpack ref-binds it via the
                        # value-tuple name-source arm. The borrow-tuple leg is
                        # unreachable from here today: it would need an
                        # `__anext__` returning a pointer-repr tuple, and that
                        # coroutine's own frame rejects at res.return_type.
                        bt = gen_local_types.get(payload.bind_target)
                        if not _loop_var_bind_ok(
                                bt, payload.bind_target, analyzer,
                                borrow_tuple_locals,
                                value_tuple_locals | opt_tuple_holders):
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
    lc.frame_own_tuple_types = frame_own_tuple_types
    lc.storage_tuple_locals |= owning_tuple_slots
    # A pointer-to-storage-tuple loop var reads the same value form off its
    # deref (`lc.pointers` supplies the `(*t)`), so it joins the same
    # binding-form membership the owning slot uses.
    lc.storage_tuple_locals |= ptr_storage_tuple_loop_vars
    # A frame-promoted STORAGE slot is movable with no value-type filter --
    # a last-use read moves out of the slot rather than copying it, which is
    # how a BigInt frame local moves at an async return. A pointer-form
    # local is promoted only when the frame owns its pointee: the
    # Optional-ptr local's `__ptr_slot_fN` storage is the frame's, so it
    # joins exactly as the plain body's OPT_PTR_SLOT decl arm promotes it
    # (the Own-slot rows rebuild and move out of the pointee at a last
    # use); loop-var, alias and unpack pointers borrow, so they stay out.
    # Promoted here rather than per-decl because the frame classification is
    # an up-front pass, not a decl-time one.
    # Restricted to names an actual `TpyVarDecl` binds: a for-loop variable
    # that happens to be frame-promoted is bound by the loop arm instead,
    # whose own promotion is gated on a CONSUMING iterable -- promoting it
    # here would move a yielded loop element that must be copied.
    _frame_ptr_locals = (ptr_frame_locals | alias_ptr_locals
                         | unpack_ptr_targets)
    _frame_decl_names = _var_decl_names(list(func.body))
    for _lname, _ in (func.generator_locals or []):
        if _lname not in _frame_ptr_locals and _lname in _frame_decl_names:
            lc.promote_movable(_lname)
    # Frame nested defs are struct members callable from EVERY resume
    # case, so their names register up front (the
    # `collect_frame_nested_defs` pass), not just at their statement.
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
    lc.frame_field_names = frozenset(frame_fields)
    # The frame ctx seeds var_types from generator_locals and then
    # OVERWRITES each branch-first-declared name with sema's branch-decl
    # snapshot (`if_branch_decls`), whose container types are forced off
    # the fixed-size Array optimization (sibling arms may bind different
    # lengths). Renders spelling the slot must see the same override.
    for _stmt in _iter_nested_stmts(list(func.body)):
        for _bname, _btype in (analyzer.if_branch_decls.get(_stmt)
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
    # `g = v;` as a sync body's (global writes are function-kind-blind).
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
                # frame write; reads classify against the sema enter type.
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
                    # Value-opt narrows have no extraction alias (the deref
                    # happens in place), so without the binding a narrowed
                    # read falls through to the plain-name arm and renders
                    # bare.
                    if _value_opt_scalar(declared[lv],
                                         analyzer) is not None:
                        lc.value_opt_bindings[lv] = ValueOptKind.SCALAR

    leaves: IdentityMap = IdentityMap()
    conds: IdentityMap = IdentityMap()
    await_args: IdentityMap = IdentityMap()
    return_values: IdentityMap = IdentityMap()
    yield_values: IdentityMap = IdentityMap()
    suspend_exprs: IdentityMap = IdentityMap()
    region_exprs: IdentityMap = IdentityMap()
    match_dispatches: IdentityMap = IdentityMap()
    deferred_returns: IdentityMap = IdentityMap()

    def _lower_leaf(stmt: TpyStmt) -> THIRStmt:
        try:
            return _lower_leaf_inner(stmt)
        except ThirUnsupported as ex:
            # The frame-field arms below bypass the sync statement chokepoint,
            # so this is the innermost frame that knows the rejecting line.
            if ex.loc is None:
                ex.loc = getattr(stmt, "loc", None)
            raise

    def _lower_leaf_inner(stmt: TpyStmt) -> THIRStmt:
        if isinstance(stmt, TpyVarDecl) and stmt.name in frame_fields:
            begin_stmt()
            if stmt.init is None:
                # An annotation-only decl (`x: int32`) of a name the frame
                # already declares as a field: nothing is emitted for it and
                # only leading trivia survives -- the sync global no-init
                # arm's shape, for the same reason (the slot exists already).
                _witness("res.decl_no_init")
                return THIRNoOpStmt()
            # A view-resolved frame field fed a stale view->owned coerce
            # renders the source bare -- same peel as the sync decl.
            init = _peel_stale_view_owned_coerce(
                stmt.init, declared[stmt.name], analyzer)
            if stmt.name in borrow_tuple_locals:
                return _lower_borrow_tuple_frame_write(stmt, lc, declared)
            # A concrete-coro handle slot (`c = add_one(1)`) writes
            # `c.emplace(<call>)` for a factory-call source -- the frame_slot
            # write shape for a call render -- while a NAME source is the
            # two-statement `emplace(std::move(*src)); src.reset();` pair
            # (and a self-write a no-op). coro_factory lifts only the
            # async-callee reject; arg slots and callee kind gate like any
            # call.
            if stmt.name in coro_handle_slots:
                if (isinstance(init, TpyName)
                        and init.name in coro_handle_slots
                        and init.name != stmt.name):
                    # A NAME source move-constructs from the source's
                    # payload and resets it (`d.emplace(std::move(*c));
                    # c.reset();` -- optional's move-assign is deleted
                    # when the frame holds reference members). A
                    # self-write is a Python no-op: nothing is emitted.
                    _witness("res.coro_handle_move")
                    return THIRCoroHandleMove(
                        target=stmt.name, source=init.name, loc=stmt.loc)
                if (isinstance(init, TpyName)
                        and init.name == stmt.name):
                    _witness("res.coro_handle_move")
                    return THIRNoOpStmt(loc=stmt.loc)
                if not isinstance(init, (TpyCall, TpyMethodCall)):
                    raise ThirUnsupported("res.coro_handle_source")
                # The decl is a statement position: a generic factory's
                # ref-slot literal temps (`int32_t __tmp_N = 41;`) flush
                # before the emplace.
                value = _lower_expr(
                    init, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.FRAME_SLOT_WRITE, forms=_ONLY_CORO_FACTORY,
                                 allow_temps=True))
                _witness("res.coro_handle_write")
                # ConcreteCoroType has no self-contained C++ spelling; the
                # brace-init prefix can never fire for a call render, so no
                # cpp_type is needed (and rendering one would be wrong).
                return THIRFrameSlotWrite(
                    name=stmt.name, value=value, cpp_type=None, loc=stmt.loc)
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
            # None at a scalar/char slot, are sema type errors that never
            # reach lowering; a single-char `c: char = 'a'` first decl
            # renders position-blind like any other frame write.
            return _lower_frame_field_assign(stmt, lc, declared)
        return _lower_stmt(stmt, lc, declared)

    narrow_envs = (_resume_narrow_envs(cfg, case_entry_ids, frame_fields)
                   if case_entry_ids is not None else {})
    # The driver's per-BB restore record, live while its _lower_bb runs;
    # _apply_leaf_post_if records the declared-types it overrides into it.
    postif_saved: 'list[dict[str, TpyType | None] | None]' = [None]
    # Per-BB narrow-KILL map ({id(stmt): killed names}) installed by the BB
    # loop; _lower_bb pops the alias + restores the declared union at the
    # rebinding leaf.
    narrow_kill: 'list[dict[int, frozenset[str]] | None]' = [None]

    def _flat_narrowing_assert(stmt: TpyStmt) -> bool:
        """A top-level narrowing (or re-assert bump) `assert isinstance`:
        a persistent extraction alias belongs inline after it, which only
        `_lower_stmts`' post-assert arm emits -- compound BODIES get
        it, but the flat BB and finally-helper walks lower per-statement and
        would silently miss the alias. Reject in both walks."""
        if not isinstance(stmt, TpyAssert):
            return False
        return (_assert_narrow_info(stmt, declared, analyzer) is not None
                or _reassert_bump_info(
                    stmt, declared, lc.narrow.persistent_narrowed,
                    analyzer) is not None)

    def _apply_post_if_narrow(stmt: TpyIf, leaf: THIRStmt,
                              saved: 'dict[str, TpyType | None]',
                              bb: 'rcfg.BB | None') -> THIRStmt:
        """Mirror `_lower_stmts`' early-return narrowing arm for an `if` at a
        flat walk position (the persistent extraction lands inline after the
        close brace and extends the live narrow scope). `saved` is the restore
        record for the scope the alias lives in.

        In the CFG walk (`bb` given) the scope must stay BB-LOCAL: the env walk
        (`_resume_narrow_envs`) doesn't model mid-BB facts, so only a BB whose
        control leaves the frame (ReturnT / RaiseT terminator) admits one --
        anything else rejects whole. A finally helper is a self-contained
        member function instead, so it passes no BB and the alias simply lives
        to the end of that body."""
        plan = _post_if_narrow_plan(stmt, declared, lc.narrow, analyzer)
        if not plan:
            return leaf
        if bb is not None and not isinstance(bb.terminator,
                                             (rcfg.ReturnT, rcfg.RaiseT)):
            raise ThirUnsupported("res.narrowed_resume")
        lc.narrow = lc.narrow.snapshot()
        nodes = []
        for var, post, u in plan:
            if u is None or _alias_frame_collision(var, frame_fields):
                # A poly cast-and-cache has no frame-aware maker here, and an
                # alias colliding with a frame field would shadow it. The poly
                # half has no constructible witness: the fact needs a negated
                # poly guard, which a resumable leaf rejects at the `if`
                # itself, before this runs.
                raise ThirUnsupported("res.narrowed_resume")
            alias = _persistent_alias_name(var, lc)
            nodes.append(_make_narrow_alias(alias, var, post, u, lc,
                                            getattr(stmt, "loc", None)))
            lc.narrow.persistent_aliases.add(alias)
            lc.narrow.narrowed[var] = alias
            lc.narrow.subject_union[var] = u
            lc.narrow.persistent_narrowed.add(var)
            if var not in saved:
                saved[var] = declared.get(var)
            declared[var] = post
        _witness("res.postif_narrow")
        return THIRStmtSeq(stmts=(leaf, *nodes))

    def _flat_assert_narrow_leaf(
            stmt: TpyStmt,
            saved: 'dict[str, TpyType | None]') -> THIRStmt:
        """A narrowing `assert isinstance` at a FLAT walk position, with the
        persistent extraction alias appended inline after it.

        The alias does NOT need to survive a BB: the CFG flows the assert's
        then_type_facts into every successor's entry_narrowings
        (resumable_cfg's `_active_narrowings`), and each resume case
        re-establishes ALL its stamped facts as `__{var}` -- which is exactly
        what `_resume_narrow_envs` walks. So the alias is walk-local here,
        unlike the post-`if` fact, whose scope the env walk does not model.
        `saved` is the caller's restore record for the scope the alias lives
        in (the BB for the CFG walk, the helper body for a finally helper)."""
        av = _assert_narrow_info(stmt, declared, analyzer)
        rv = _reassert_bump_info(
            stmt, declared, lc.narrow.persistent_narrowed, analyzer)
        nvar = (av or rv or (None,))[0]
        if nvar is None or _alias_frame_collision(nvar, frame_fields):
            raise ThirUnsupported("res.narrowed_resume")
        # SNAPSHOT before mutating, and record the declared entry --
        # `_append_assert_narrow` mutates lc.narrow IN PLACE and rebinds
        # declared[var] with no restore of its own. The BB driver's
        # `saved_narrow = lc.narrow` holds a REFERENCE, so without the
        # snapshot its restore is a no-op and the narrowing would leak into
        # every later BB in the walk (the driver's own snapshot at the
        # narrowed-BB scope is inside `if env:`, which is empty for the BB
        # that does the asserting). Same discipline as `_apply_leaf_post_if`.
        lc.narrow = lc.narrow.snapshot()
        if nvar not in saved:
            saved[nvar] = declared.get(nvar)
        leaf = _lower_leaf(stmt)
        post: list[THIRStmt] = []
        _append_assert_narrow(stmt, post, lc, declared)
        if not post:
            raise ThirUnsupported("res.narrowed_resume")
        _witness("res.flat_assert_narrow")
        return THIRStmtSeq(stmts=(leaf, *post))

    def _lower_bb(bb: 'rcfg.BB') -> None:
        for stmt in bb.stmts:
            _nk = narrow_kill[0]
            _kills = _nk.get(id(stmt)) if _nk else None
            if _kills:
                # Statement-ordered narrow kill: the rebind writes the raw
                # variant, so this and every later leaf in the BB reads the
                # bare union binding. The case-entry re-establish alias the
                # skeleton emitted stays (dead after the kill).
                saved = postif_saved[0]
                for _kv in _kills:
                    lc.narrow.narrowed.pop(_kv, None)
                    lc.narrow.subject_union.pop(_kv, None)
                    if saved is not None and _kv in saved:
                        _kt0 = saved[_kv]
                        if _kt0 is None:
                            declared.pop(_kv, None)
                        else:
                            declared[_kv] = _kt0
                _witness("res.narrow_kill")
            if isinstance(stmt, (rcfg.WithEnter, rcfg.AsyncWithSetup)):
                # Sync + async with: only the manager expression renders; the
                # bind wrap / __enter__ / __aenter__ yields are skeleton.
                begin_stmt()
                reason = _with_enter_reject(stmt, analyzer, declared)
                if reason is not None:
                    raise ThirUnsupported(reason)
                region_exprs[stmt.item.context_expr] = (
                    _strip_slot_leaf_deref(
                        _lower_expr(stmt.item.context_expr, lc, declared,
                                    # The owned `__with_ctx_N` emplace is a
                                    # manager sink, like the sync with
                                    # lane's (the marker lane's record_ret
                                    # admission).
                                    use=_ExprUse(
                                        pos=SinkPos.WITH_MANAGER)),
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
                    region_exprs[stmt.iterable_expr] = (
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
                saved = postif_saved[0]
                if saved is None:
                    raise ThirUnsupported("res.narrowed_resume")
                leaves[stmt] = _flat_assert_narrow_leaf(stmt, saved)
                continue
            leaf = _lower_leaf(stmt)
            if isinstance(stmt, TpyIf):
                saved = postif_saved[0]
                assert saved is not None
                leaf = _apply_post_if_narrow(stmt, leaf, saved, bb)
            leaves[stmt] = leaf
        t = bb.terminator
        if isinstance(t, rcfg.ReturnT):
            ret = t.return_stmt
            if not _return_carries_value(ret, lc):
                # Void scaffolding is skeleton-only -- a Void slot never
                # renders its value (the skeleton keys POLL_VOID_READY_RETURN
                # on VoidType, not on the value).
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
            return_values[ret] = _lower_resumable_return_value(
                ret, lc, declared)
            deferred = _resumable_deferred_recipe(ret, lc, declared)
            if deferred is not None:
                deferred_returns[ret] = deferred
            _witness("res.return_value")
        elif isinstance(t, rcfg.RaiseT):
            leaves[t.raise_stmt] = _lower_leaf(t.raise_stmt)
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
            match_dispatches[t.match_stmt] = m_node
            # Hook-mode captures bind frame fields BEFORE the arm's BB walk,
            # so the walk's flat `declared` must carry them; the frame slot
            # type is authoritative.
            # A capture with no frame entry stays unregistered -- its arm
            # reads keep rejecting fail-closed.
            # Every entry list a tier fills, not just `arms`: the str switch
            # keeps its guarded-prefix and trailing arms in their own tuples,
            # and a capture there is as much a frame write as a bucket's.
            for m_entry in (*(e for a in m_node.arms for e in a.entries),
                            *m_node.str_guarded, *m_node.str_trailing):
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
                    conds[t.cond] = _lower_narrow_cond(info, t.cond, lc,
                                                           declared)
                elif (pinfo is not None
                      and pinfo[0] not in lc.narrow.narrowed
                      and pinfo[0] not in lc.narrow.spelled):
                    # A POLY isinstance Branch cond renders the no-alias
                    # check (`(dynamic_cast<const Dog*>(&__self) !=
                    # nullptr)`) -- the extraction alias is the ARM
                    # entry's skeleton emission, not this condition's.
                    _witness("res.poly_cond")
                    conds[t.cond] = THIRDynIsinstanceMulti(
                        result_type=analyzer.get_expr_type(t.cond),
                        checks_cpp=_poly_cast_checks(
                            pinfo[0], (pinfo[1],), lc, declared),
                        loc=getattr(t.cond, "loc", None))
                else:
                    # The skeleton renders the cond, flushes pending temps,
                    # then writes the `if` -- all inside the `case` block, so
                    # a condition temp is a per-re-entry rebuild, the frame
                    # twin of the restructured sync while head.
                    _c = _lower_truthy(t.cond, lc, declared, temps_ok=True)
                    if _cond_mixed_walrus_temps(_c, walrus_nested_ok=True):
                        # The flush runs the temp BEFORE the walrus store it
                        # may read, so the mix rejects here exactly as it
                        # does at the sync heads
                        # (BUGS.md#cond-walrus-before-hoisted-temp). The
                        # nested-temp relaxation cannot fire on a frame: a
                        # frame walrus lowers its value with no flush right,
                        # so a temp inside one rejects at the walrus itself.
                        raise ThirUnsupported("cond.mixed_walrus_temps")
                    conds[t.cond] = _c
            except ThirUnsupported as ex:
                # The landmark names the branch position; the condition's own
                # reason rides it, or the tag names this catcher instead of
                # the construct that blocked.
                raise ThirUnsupported(f"res.cond:{ex.reason}") from None
            _witness("res.branch_cond")
        elif isinstance(t, rcfg.Yield) and is_generator:
            # Generator suspension: the skeleton emits `__state = S_RESUME_i;
            # return <value>;`. The yield type threads into the value
            # render: for the value-scalar slice that only shows on an
            # int/float literal value (a widened literal carries a sema-baked
            # coerce, but a BigInt-slot literal renders the target-typed ctor
            # wrap), hence the shared slot-literal retype. A char-typed
            # str-literal yield would need the char-targeted render --
            # reject, like the return arm. A bare `yield` (no value) never
            # arises for a value-scalar yield type.
            ys = t.payload.yield_stmt
            if ys is None or ys.value is None:
                raise ThirUnsupported("res.bare_yield")
            begin_stmt()
            yt = func.generator_yield_type
            # Own peel matches the eligibility gate: an Own[container] slot
            # renders like the plain container borrow.
            yt_bare = (_unwrap_own(unwrap_readonly(unwrap_ref_type(
                           unwrap_send_sync(yt))))
                       if isinstance(yt, TpyType) else None)
            if _eligible_char(yt_bare) and isinstance(ys.value, TpyStrLiteral):
                raise ThirUnsupported(stmt_reject_reason(ys))
            if isinstance(yt_bare, TupleType):
                # Tuple yield slot: the slot type threads into the render.
                # A literal takes the value or borrow builder per the slot's
                # element forms (a readonly slot bumps REF elements to
                # const); a borrow-tuple LOCAL name passes bare (not a
                # storage-form source, so the pointer lift no-ops). A
                # STORAGE-form frame binding -- an owning slot or a
                # pointer-to-storage loop var -- takes its own rung below.
                target_ro = isinstance(
                    unwrap_ref_type(unwrap_send_sync(yt)), ReadonlyType)
                yv_src = ys.value
                if isinstance(yv_src, TpyTupleLiteral):
                    # The literal-vs-builder selection of the tuple-yield
                    # ladder (borrow / generic / spelled value literal incl.
                    # Own-record storage elements).
                    yield_values[ys] = _lower_yield_tuple_literal(
                        yv_src, yt_bare, lc, declared,
                        generic_face="res.btuple_yield_generic",
                        reject="res.btuple_yield_source",
                        target_readonly=target_ro)
                elif (isinstance(yv_src, TpyName)
                        and (yv_src.name in borrow_tuple_locals
                             or _bare_yield_tuple_name_ok(
                                 yv_src.name, lc, declared))):
                    yield_values[ys] = _lower_expr(yv_src, lc, declared)
                elif (isinstance(yv_src, TpyName)
                        and yv_src.name in owning_tuple_slots
                        # STORAGE yield slots only: a ptr-repr slot needs the
                        # storage->borrow lift the arm below carries.
                        and not yt_bare.has_pointer_repr_element()
                        and collapse_tuple_own_elements(unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(
                                declared.get(yv_src.name)))))
                        == collapse_tuple_own_elements(yt_bare)):
                    # An owning frame_slot already holds the STORAGE tuple the
                    # slot spells (`yield t` off `t = (i, Box(...))` at an
                    # `Own[Box]` element), so the deref'd read IS the handed-out
                    # value. A
                    # dead-after-yield slot MOVES out rather than copying its
                    # reference elements; `frame_slot::emplace` destroys before
                    # it reconstructs, so the next bind is safe on a moved-from
                    # slot. A still-live slot copies -- the leg sema already
                    # warns on ("copies ... into owned storage"), never a
                    # silent one. Both sides of the type test collapse:
                    # per-element `Own` is an ownership spelling, not a C++
                    # shape, so a literal init (markers dropped) and an
                    # owning-CALL init (`t = mk(i)`, markers kept) bind the
                    # same storage tuple.
                    _sv = _lower_expr(yv_src, lc, declared)
                    if _is_move_source(yv_src, lc):
                        yield_values[ys] = THIRMove(
                            result_type=yt_bare, value=_sv,
                            form=Form.STORAGE,
                            loc=getattr(yv_src, "loc", None))
                        _witness("res.btuple_yield_storage_name_move")
                    else:
                        yield_values[ys] = _sv
                        _witness("res.btuple_yield_storage_name")
                elif (isinstance(yv_src, TpyName)
                        and yv_src.name in ptr_storage_tuple_loop_vars
                        and yt_bare.has_pointer_repr_element()
                        and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                            declared.get(yv_src.name)))) == yt_bare):
                    # A pointer-to-STORAGE tuple loop var at the slot's BORROW
                    # form (`yield pair` off `for pair in items` over
                    # `list[tuple[int32, C]]`): the same storage->borrow lift
                    # the container-ELEMENT arm below takes, over the loop
                    # var's deref. The pointer is into the CALLER's container,
                    # so the consumer aliases the source element exactly as the
                    # `for` loop itself does -- and it outlives the resume. Keyed
                    # on the loop-var set rather than the storage-tuple union an
                    # owning slot also joins: lifting THAT would hand out
                    # pointers into frame storage the next emplace destroys.
                    yield_values[ys] = THIRFormConvert(
                        result_type=yt_bare,
                        value=_lower_expr(yv_src, lc, declared),
                        form=Form.BORROW, move=False,
                        loc=getattr(yv_src, "loc", None))
                    _witness("res.btuple_yield_storage_name_lift")
                elif (isinstance(yv_src, TpySubscript)
                        and yt_bare.has_pointer_repr_element()
                        and isinstance(yv_src.obj, TpyName)
                        and yv_src.obj.name in declared
                        and is_list(_sub_ct := unwrap_readonly(
                            unwrap_ref_type(unwrap_send_sync(
                                declared[yv_src.obj.name]))))
                        and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                            _sub_ct.type_args[0]))) == yt_bare):
                    # A container-ELEMENT source (`yield items[0]`): the
                    # storage element lifts to the slot's borrow form --
                    # `return tuple_to_pointer<std::tuple<int32_t, Box*>>(
                    # ::tpy::__getitem__((*items), 0));` (the frame-slot
                    # deref rides the name read).
                    yield_values[ys] = THIRFormConvert(
                        result_type=yt_bare,
                        value=_lower_expr(yv_src, lc, declared,
                                          subscript_prechecked=True),
                        form=Form.BORROW, move=False,
                        loc=getattr(yv_src, "loc", None))
                    _witness("res.btuple_yield_elem_lift")
                else:
                    raise ThirUnsupported("res.btuple_yield_source")
                _witness("res.btuple_yield")
                return
            if _f1_ref(yt_bare, analyzer):
                # REFERENCE yield slot (`val_or_ref<T>` in the skeleton's
                # signature) -- records and containers alike, one ladder over
                # the whole axis. Both halves emit the same
                # `return <borrow-lvalue>;`, so a source shape either has a
                # borrow lvalue at this slot or it does not; splitting the
                # admission per half is what let the two drift apart into
                # different accepted shapes for an identical emit
                # (docs/PITFALLS.md#same-construct-every-position).
                #
                # Two legs really are half-specific, and only those branch:
                # an `Own` record ctor is a record-only slot shape, and a
                # RECORD pointer NAME reads bare (its field/method consumers
                # spell `->` themselves) so the yield has to add the deref a
                # container pointer name already carries.
                #
                # Sources with no borrow lvalue -- literals, calls,
                # subscripts, non-`self` fields -- need the target-typed
                # render and stay a named rung.
                rec_slot = _record_class_binding(yt_bare)
                # Every face below is composed by f-string over `half`, so the
                # literals are greppable only here and in the registry. The ten
                # they spell, in ladder order:
                #   res.yield_record_ternary    res.yield_container_ternary
                #   res.yield_record_walrus     res.yield_container_walrus
                #   res.yield_record_field      res.yield_container_field
                #   res.yield_record_param      res.yield_container_param
                #   res.yield_record_borrow     res.yield_container_borrow
                half = "record" if _f1_record(yt_bare, analyzer) else \
                    "container"
                yv_src = ys.value

                def _yield_borrow_name(e: TpyExpr) -> THIRExpr:
                    """A NAME source's borrow lvalue at this slot."""
                    v = _lower_expr(e, lc, declared)
                    if (rec_slot and isinstance(v, THIRName)
                            and not v.deref):
                        v = replace(v, deref=True)
                    return v

                if (isinstance(yv_src, TpyIfExpr)
                        and isinstance(yv_src.then_expr, TpyName)
                        and yv_src.then_expr.name in lc.frame_slots
                        and isinstance(yv_src.else_expr, TpyName)
                        and yv_src.else_expr.name in lc.frame_slots):
                    # A TERNARY of frame-slot names hands out the
                    # branch-picked borrow (`((flag) ? ((*a)) : ((*b)))`) --
                    # one THIRIfExpr, both operands lowered as branch
                    # expressions so neither hoists a temp.
                    yield_values[ys] = THIRIfExpr(
                        result_type=yt_bare,
                        cond=_lower_truthy(yv_src.condition, lc, declared),
                        then=_yield_borrow_name(yv_src.then_expr),
                        orelse=_yield_borrow_name(yv_src.else_expr),
                        form=Form.BORROW,
                        loc=getattr(yv_src, "loc", None))
                    _witness(f"res.yield_{half}_ternary")
                    return
                if isinstance(yv_src, TpyNamedExpr):
                    # A walrus delegates to the frame-walrus dispatch
                    # (`return (x = &((*buf)), *x);`) -- the comma tail hands
                    # out the alias's deref lvalue, and un-landed walrus legs
                    # reject inside the dispatch.
                    yield_values[ys] = _lower_expr(yv_src, lc, declared)
                    _witness(f"res.yield_{half}_walrus")
                    return
                if (isinstance(yv_src, TpyFieldAccess)
                        and isinstance(yv_src.obj, TpyName)
                        and yv_src.obj.name == "self"
                        and _f1_ref(unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(analyzer.get_expr_type(yv_src)))),
                            analyzer)):
                    # A reference FIELD off self reads bare (`return
                    # __self.a;`) -- the storage member binds the val_or_ref
                    # slot directly, no deref (BORROW_BIND, like the for-head
                    # member bind). `self` only: any other receiver has no
                    # frame-lifetime guarantee and stays a named rung
                    # (generators/error_yield_field_at_container_slot).
                    yield_values[ys] = _lower_expr(
                        yv_src, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.BORROW_BIND))
                    _witness(f"res.yield_{half}_field")
                    return
                if (isinstance(yv_src, TpyCall)
                        and isinstance(unwrap_readonly(unwrap_ref_type(
                            unwrap_send_sync(yt))), OwnType)
                        and ((_f1_record(yt_bare, analyzer)
                              and _ctor_shape_ok(yv_src, analyzer))
                             or _own_return_call_shape(yv_src, analyzer))):
                    # A CTOR call at an OWN record yield slot (`yield
                    # Node(i)` at `Iterator[Own[Node]]`) or any call whose
                    # callee DECLARES the transfer (`yield copy(p)`,
                    # `yield mk(i)` at `-> Own[T]`): the storage render lands
                    # bare (`return Node(i);` / `return Point((*p));`). The
                    # ctor face is record-only -- sema forbids a borrow-record
                    # ctor yield and a container ctor at an Own slot has no
                    # storage-ctor rung -- while the declared-Own face spans
                    # the whole reference axis, containers included. A
                    # BORROW-returning callee stays out: `is_rvalue_source`
                    # cannot tell it apart, and admitting it here would turn
                    # generators/error_yield_own_borrow_call_copy's reject
                    # into a warned silent copy.
                    yield_values[ys] = _lower_expr(
                        yv_src, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE))
                    _witness("res.yield_own_ctor"
                             if _ctor_shape_ok(yv_src, analyzer)
                             else "res.yield_own_rvalue")
                    return
                if (isinstance(yv_src, TpyName)
                        and _bare_yield_param_ok(yv_src.name, yt_bare, lc,
                                                 declared)):
                    yield_values[ys] = _lower_expr(yv_src, lc, declared)
                    _witness(f"res.yield_{half}_param")
                    return
                if not (isinstance(yv_src, TpyName)
                        and (yv_src.name in lc.frame_slots
                             or yv_src.name in ptr_frame_locals
                             or yv_src.name in alias_ptr_locals)):
                    raise ThirUnsupported("res.yield_type")
                # A frame_slot / pointer-form loop var / alias local NAME:
                # every one of the three is frame-owned storage or a pointer
                # into storage the frame outlives, so the borrow it hands out
                # stays live across the suspension. Sema already rejects
                # yielding an OWNING record local by reference ("declare
                # Iterator[Own[R]]").
                yield_values[ys] = _yield_borrow_name(yv_src)
                _witness(f"res.yield_{half}_borrow")
                return
            yv_valopt = _value_opt_yield_slot(yt)
            if yv_valopt is not None and isinstance(ys.value, TpyNoneLiteral):
                # `yield None` at a value-repr Optional slot: the storage
                # nullopt. SLOT-typed, not NoneType-typed -- a NoneType
                # STORAGE literal renders `std::monostate{}` instead.
                yield_values[ys] = THIRLiteral(
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
            # NAME must deref at an `int32` slot (`return (*val);`) and pass
            # whole at an `int32 | None` one.
            yv_lowered = _lower_expr(
                ys.value, lc, declared, field_prechecked=yv_str_field,
                allow_whole_optional=yv_valopt is not None)
            if (isinstance(ys.value, TpyName)
                    and ys.value.name in ptr_frame_locals
                    and isinstance(yv_lowered, THIRName)
                    and not yv_lowered.deref):
                # Pointer-form loop var at a VALUE yield slot takes the
                # `pointer_value_expr` deref (`return (*x);`); the name arm
                # keeps pointer names bare for the arrow/pass positions.
                yv_lowered = replace(yv_lowered, deref=True)
            # The iterator slot owns its str/bytes payload, so a borrow-form
            # value takes the same explicit view->owned copy the return sinks
            # take. A str FIELD read (prechecked above) is already storage form
            # and lands bare, as does a param the frame captured owned.
            if not (isinstance(ys.value, TpyName)
                    and ys.value.name in owned_view_params):
                yv_lowered = _wrap_view_owned_sink(yv_lowered, yt_bare, ys.loc)
            yield_values[ys] = _slot_literal_retype(yv_lowered, yt, lc)
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
                    # line.
                    suspend_exprs[operand] = _strip_slot_leaf_deref(
                        _lower_expr(
                            operand, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.SUSPEND,
                                         allow_temps=True)),
                        lc)
                except ThirUnsupported as ex:
                    # The landmark names the suspend position; the operand's
                    # own reason rides it, or the tag hides which construct
                    # actually blocked.
                    raise ThirUnsupported(
                        f"res.await_operand_shape:{ex.reason}") from None
                _witness("res.suspend_operand")
            else:
                fi = operand.resolved_function_info
                # A bound-method await (`await obj.method()`): the receiver
                # `(*obj)` is prepended as the __self ctor arg (R5b). Module /
                # free calls have no value receiver.
                if (isinstance(operand, TpyMethodCall)
                        and operand.user_module_call is None
                        and operand.builtin_module_call is None):
                    suspend_exprs[operand.obj] = _lower_expr(operand.obj, lc, declared)
                    _witness("res.suspend_expr")
                lowered_args = []
                # The const verdict lives on the RAW fi only (substitution
                # never copies it), so the emplace arg reads it there.
                dcbp = fi.root.const_borrow_params
                for i, a in enumerate(operand.args):
                    # An awaited callee is a coro factory -- always
                    # frame-capturing for its ref args. The emplace is a
                    # statement position: arg temps (the Own-slot copy+move
                    # `auto __tmp_N = ...;`) flush before the suspend line.
                    lowered_args.append(_lower_call_arg(
                        a, fi.params[i].type, lc, declared,
                        frame_capturing=True, temp_args=True,
                        readonly_target=(dcbp is not None and i in dcbp)))
                await_args[operand] = tuple(lowered_args)
                if lowered_args:
                    _witness("res.await_args")

    for bb_id in sorted(cfg.blocks):
        bb = cfg.blocks[bb_id]
        env = narrow_envs.get(bb_id) or {}
        # A stamped fact the env walk didn't model (or modeled with a
        # different fact) has no alias to read -- reject whole.
        for v, f in bb.entry_narrowings.items():
            if v not in env or env[v][0] is not f:
                raise ThirUnsupported("res.narrowed_resume")
        # Restore by VALUE snapshot: an arm-scoped install inside this BB
        # (branch_scope restores by REPLACING lc.narrow with a clean copy)
        # leaves the pre-BB object itself mutated, so an identity restore
        # would carry the last arm's alias into every later BB.
        saved_narrow = lc.narrow.snapshot()
        saved_decl: 'dict[str, TpyType | None]' = {}
        if env:
            _kill_plan = _narrow_kill_plan(bb.stmts, frozenset(env))
            if _kill_plan is None:
                raise ThirUnsupported("res.narrowed_resume")
            narrow_kill[0] = _kill_plan or None
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
            narrow_kill[0] = None
            lc.narrow = saved_narrow
            for v, t0 in saved_decl.items():
                if t0 is None:
                    declared.pop(v, None)
                else:
                    declared[v] = t0

    # Helper-based finally bodies live outside cfg.blocks (a member fn per
    # try); lower their statements into the SAME leaves table, keyed by
    # the nested def. `gen_coro_finally_top_def` emits them through the leaf seam.
    # A `return` inside a helper (async: Poll replay; generator: the
    # __finally_stop path) rejects via res.finally_return, keeping the
    # helper's render nuance out of the leaf-return hook.
    lc.in_finally_helper = True
    try:
        for _helper_name, body_stmts in cfg.finally_helpers:
            # The helper is its own C++ member function, so an alias declared
            # in it lives to the end of THAT body and no further: a per-helper
            # restore record, unwound before the next helper (and before the
            # nested-def member walk) sees the declared types.
            helper_saved: 'dict[str, TpyType | None]' = {}
            saved_narrow = lc.narrow
            try:
                for stmt in body_stmts:
                    if _flat_narrowing_assert(stmt):
                        leaves[stmt] = _flat_assert_narrow_leaf(
                            stmt, helper_saved)
                        continue
                    leaf = _lower_leaf(stmt)
                    if isinstance(stmt, TpyIf):
                        leaf = _apply_post_if_narrow(stmt, leaf, helper_saved,
                                                     None)
                    leaves[stmt] = leaf
            finally:
                lc.narrow = saved_narrow
                for _v, _t0 in helper_saved.items():
                    if _t0 is None:
                        declared.pop(_v, None)
                    else:
                        declared[_v] = _t0
            _witness("res.finally_helper")
    finally:
        lc.in_finally_helper = False

    # Frame nested defs are struct MEMBERS emitted by
    # `gen_coro_finally_top_def`; lower each member BODY here so the frame
    # emits it from THIR. The statement position keeps its
    # THIRFrameNestedDef marker; a member body outside the slice rejects the
    # WHOLE frame (all-or-nothing at the frame's granularity).
    nested_def_bodies: IdentityMap = IdentityMap()
    for _nd in collect_frame_nested_defs(list(func.body)):
        nested_def_bodies[_nd.func] = _lower_member_nested_def(
            _nd, lc, declared)

    # Nested leaf returns (THIRResumableReturn): register their values so
    # the skeleton's `_make_async_return` value render (`render_return_value`,
    # keyed by the ast node) finds them exactly like ReturnT terminator values.
    for nr in lc.nested_returns:
        if nr.value is not None:
            return_values[nr.ast_stmt] = nr.value
        if nr.deferred is not None:
            deferred_returns[nr.ast_stmt] = nr.deferred

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
    res_body = THIRResumableBody(
        leaves=leaves, conds=conds, await_args=await_args,
        return_values=return_values, yield_values=yield_values,
        suspend_exprs=suspend_exprs, region_exprs=region_exprs,
        match_dispatches=match_dispatches,
        nested_def_bodies=nested_def_bodies,
        deferred_returns=deferred_returns)
    validate_resumable_body(func.name, res_body)
    return res_body


def _lower_member_nested_def(nd, lc, declared) -> 'tuple':
    """Lower one frame nested def's MEMBER body, which
    `gen_coro_finally_top_def` emits.

    The member is a plain sync function over the frame struct: the frame
    classification sets stay live (an outer local reads as the frame's own
    field via implicit this, exactly like in the frame body), while the
    per-function return/prescan facts swap through the shared nested-def
    scope, and `resumable_leaf_mode` CLEARS so a `return` lowers plain
    (`return v;` -- the member is not a resume step). No capture gates:
    a member has no capture list; self reaches through the frame's
    receiver spelling."""
    func = nd.func
    analyzer = lc.analyzer
    _nested_def_entry_reject(func, lc, lambda: "res.nested_def_member")
    if (analyzer.registry.get_function(func.name)
            or (lc.record_name is not None
                and (ri := analyzer.registry.get_record(lc.record_name))
                is not None
                and ri.get_method_overloads(func.name))):
        # Same hazard as the lambda form (which keeps its own copy so its
        # reject-tag ORDER stays stable): a colliding name makes the body's
        # const-verdict lookups consult the wrong FunctionInfo.
        note_detail("nesteddef.name_collision")
        raise ThirUnsupported("res.nested_def_member")
    body_declared = dict(declared)
    for pname, ptype in func.params:
        if not isinstance(ptype, TpyType):
            note_detail("nesteddef.param_unresolved")
            raise ThirUnsupported("res.nested_def_member")
        # No param seeding, like the lambda form: the params only enter the
        # local scope by name. Unlike the lambda form there is no param-TYPE
        # ladder: the member's signature is skeleton emission
        # (`nested_def_signature`), and the body reads a param as a plain
        # name -- the lambda ladder exists to fence shapes whose LAMBDA emit
        # is ill-formed (BUGS.md), a hazard the member form does not share.
        body_declared[pname] = ptype
    saved_leaf = lc.resumable_leaf_mode
    lc.resumable_leaf_mode = False
    try:
        with _nested_def_lowering_scope(lc, func, self_captured=True):
            body = _lower_stmts(func.body, lc, body_declared)
            if lc.unhandled_hoists:
                note_detail("nesteddef.hoisted_vars")
                raise ThirUnsupported("res.nested_def_member")
    finally:
        lc.resumable_leaf_mode = saved_leaf
    _witness("res.nested_def_body")
    out = tuple(body)
    # The member's OWN return slot -- an unresolved annotation reaches the
    # walk as None, which makes the borrow-return rule vacuous rather than
    # feeding a non-type to the type predicates.
    validate_stmts(func.name, out,
                   func.return_type if isinstance(func.return_type, TpyType)
                   else None)
    return out


def _reject(reason: str, loc: 'SourceLocation | None' = None) -> None:
    note(reason, loc)
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
