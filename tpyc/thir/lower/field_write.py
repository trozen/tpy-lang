"""Field-write lowering: `recv.field = <value>`.

The write's slot contract is settled first, from the DECLARED field type
(`field_slot_use`); a family is then picked by the SLOT alone, each a
(classify, lower) pair over a frozen plan. The storage family -- the
reference axis, Optionals, unions, tuples -- lowers the source once under
the use its slot hands it and picks the render from the lowered node's
facts (form, type, last use, declared ownership): no source row names an
expression kind except literal construction. Families are tried in
declaration order -- the first non-None plan wins -- so a family whose
field-type slice overlaps a later one (None at any Optional field, class
constants before plain scalars) claims its statements by position.
"""
from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Callable

from ...parse.nodes import (
    TpyDictComprehension,
    TpyListComprehension,
    TpySetComprehension,
    TpyArrayLiteral,
    TpyAssign,
    TpyBoolLiteral,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyIntLiteral,
    TpyLambda,
    TpyListRepeat,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpySubscript,
    TpyTupleLiteral,
)
from ...type_def_registry import (
    is_bytes_type,
)
from ...typesys import (
    holds_borrowing_view,
    lands_in_view_member,
    NoneType,
    OptionalType,
    FloatLiteralType,
    IntLiteralType,
    LiteralType,
    OwnType,
    RecursiveAliasInstanceType,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..faces import witness as _witness
from ..reject import (
    ThirUnsupported,
    note_detail,
    stmt_reject_reason,
)
from ..nodes import (
    Form,
    THIRAssign,
    THIRContainerLiteral,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRLiteral,
    THIRCoerce,
    THIRMove,
    THIRName,
    THIROptViewArg,
    THIRSubscript,
)
from .context import (
    _NO_FORMS, _ONLY_BTUPLE_SLOT, _ExprResultUse, _ExprUse, _LowerCtx,
                      _slot_lift_forms, SinkForm, SinkPos,
                      SlotConstruct, SlotLifetime, SlotPlacement,
                      field_slot_use)
from .checks import (
    _borrow_tuple_local_type,
    _bytes_field_write_ok,
    _container_comp_arg,
    _container_literal_shape_ok,
    _container_storage_field,
    _covariant_record_upcast_ok,
    _record_slice_upcast_ok,
    _field_over_container_subscript_ok,
    _class_const_write_target_ok,
    _lambda_routable,
    _container_field_write_slot,
    _method_recv_field_write_ok,
    _nondef_ctor_field,
    _optional_container_storage_inner,
    _scalar_field_write_ok,
    _str_field_write_ok,
    _user_deref_field_write_ok,
    _viewfam_field_write_receiver_ok,
    copy_ctor_rvalue_source,
    view_slot_shape_ok,
)
from .predicates import (
    _comp_shadow_pointers,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    record_like,
    _f1_tuple,
    _nested_storage_tuple,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    _callable_value,
    _opt_view_arg_shim,
    _peel_coerce,
    _resolved_bytes_value,
    _resolved_str_value,
    _ru_instance_literal_ok,
    _span_value,
    _storage_copy_value,
    _tuple_literal_has_ref_elements,
    _user_deref_field_recv_ok,
    _value_opt_owned_view,
    _value_opt_scalar,
    _value_tuple,
    copy_call_arg,
    copy_ptr_optional_peel,
)
from ...value_category import CONTAINER_LITERAL_NODES, is_rvalue_source
from . import comprehensions as _comprehensions
from .expressions import (
    _flush_witness,
    _node_moves,
    _lower_class_const_write_target,
    _lower_copy_record,
    _lower_expr,
    _lower_ru_literal,
    _lower_tuple_literal,
    _param_declared_type,
    _slot_literal_retype,
    _subscript_yields_borrow_ptr,
)
from . import statements as _statements

# Every storage slot's copy-assign takes a stored lvalue whole -- a field
# read, a container element, a borrowed call result.
_STORAGE_COPY_FORMS: frozenset[SinkForm] = frozenset({SinkForm.RECORD_COPY})
# A member taken as a value reads a pointer-bound source through its deref.
_VALUE_READ_FORMS: frozenset[SinkForm] = frozenset({SinkForm.INDIRECT_READ})
# What a record / container member absorbs besides: a select of existing
# objects (sema warns the copy).
_REF_MEMBER_FORMS: frozenset[SinkForm] = frozenset({SinkForm.SELECT_PRVALUE})


def _lower_field_write_target(stmt: TpyAssign, lc: _LowerCtx,
                              declared: dict[str, TpyType]) -> THIRExpr:
    """A plain-assign FIELD target renders WITHOUT the value-position deref:
    a narrowed Optional field assigns into the bare optional storage
    (`this->f = v;`), so the narrowed deref is stripped from
    the top-level node only -- a narrowed RECEIVER inside the chain keeps
    its unwrap (`(*this->opt).x = v;`)."""
    target = _lower_expr(stmt.target, lc, declared, field_prechecked=True)
    if isinstance(target, THIRFieldAccess) and target.narrowed_deref:
        target = replace(target, narrowed_deref=False)
    return target


def _btuple_elem_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                lc: _LowerCtx, ft: 'TpyType | None') -> bool:
    """A value-scalar field write through a borrow-tuple element
    (`t[1].val = 99` -> `std::get<1>(t)->val = 99;`): the target rides the
    tuple-subscript field READ arm (the `_subscript_yields_borrow_ptr`
    arrow) off an in-scope borrow-tuple NAME receiver; only value-scalar /
    char field slots admit (record/container slots carry write machinery
    this family does not render)."""
    target = stmt.target
    if not (isinstance(target, TpyFieldAccess)
            and isinstance(target.obj, TpySubscript)
            and isinstance(target.obj.obj, TpyName)
            and _borrow_tuple_local_type(
                target.obj.obj.name, declared,
                lc.storage_tuple_locals) is not None
            # Plain borrow elements only: an Optional element write needs
            # the deref_check machinery (rejects, pinned).
            and _subscript_yields_borrow_ptr(target.obj, lc)):
        return False
    return bool((_eligible_scalar(ft) or _eligible_char(ft))
                and _witness("assign.btuple_elem_field"))


@dataclass(frozen=True)
class _OptNonePlan:
    """`recv.opt = None` at an Optional FIELD: storage is `std::optional<T>`
    whatever the inner repr, so None renders the storage-form `std::nullopt`.
    Keyed on the DECLARED field type -- a flow-narrowed write site retypes
    the read to the inner, but the storage stays optional."""
    ftype: TpyType


def _classify_opt_none(stmt: TpyAssign, slot: _ExprUse,
                       lc: _LowerCtx,
                       declared: dict[str, TpyType],
                       pointers: AbstractSet[str]) -> _OptNonePlan | None:
    if not isinstance(stmt.value, TpyNoneLiteral):
        return None
    # A SUBSCRIPT receiver -- a container element or a record-typed tuple
    # element -- is a plain record borrow lvalue, so the member spells the
    # same postfix off the bare element read that the scalar field write
    # already renders there. Only the target varies with the receiver; the
    # `std::nullopt` value render is receiver-blind.
    if not (_field_receiver_ok(stmt.target, declared, lc.analyzer)
            or _field_over_subscript_ok(stmt.target, declared, lc.analyzer)
            or _field_over_container_subscript_ok(stmt.target, declared,
                                                  lc.analyzer, lc.pointers)):
        return None
    ft = _slot_type(slot)
    if not isinstance(ft, OptionalType):
        return None
    return _OptNonePlan(ftype=ft)


def _lower_opt_none(stmt: TpyAssign, plan: _OptNonePlan,
                    slot: _ExprUse, lc: _LowerCtx,
                    declared: dict[str, TpyType], loc) -> THIRExpr:
    _witness("field_write.opt_none")
    return THIRLiteral(result_type=plan.ftype, value=None, form=Form.STORAGE,
                       loc=loc)


@dataclass(frozen=True)
class _ClassConstPlan:
    """A class-constant / classvar write: the bare qualified
    `<owner>::<member>` lvalue (receiver eval split off), then the same
    target-typed value render as the scalar field write."""
    ftype: TpyType


def _classify_class_const(stmt: TpyAssign, slot: _ExprUse,
                          lc: _LowerCtx,
                          declared: dict[str, TpyType],
                          pointers: AbstractSet[str]
                          ) -> _ClassConstPlan | None:
    if not _class_const_write_target_ok(stmt.target, declared, lc.pointers,
                                        lc.analyzer):
        return None
    return _ClassConstPlan(ftype=_slot_type(slot))


def _lower_class_const(stmt: TpyAssign, plan: _ClassConstPlan,
                       slot: _ExprUse, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    target, recv_eval, recv_wrap = _lower_class_const_write_target(
        stmt.target, lc, declared, loc)
    _witness("field_write.class_const")
    return THIRAssign(
        target=target,
        value=_slot_literal_retype(
            _flush_witness(
                "flush.field_write",
                _lower_expr(stmt.value, lc, declared,
                            use=replace(slot,
                                        result=_ExprResultUse.STORAGE))),
            plan.ftype, lc),
        recv_eval=recv_eval, recv_wrap=recv_wrap, loc=loc)


class _ValueRender(Enum):
    PLAIN = auto()      # scalar / char / enum / Ptr: target-typed flush
    VALUE_OPT = auto()  # value-repr Optional[scalar | owned-str literal]
    STR = auto()        # owned-str family: operator=(string_view) absorbs
    BYTES = auto()      # owned bytes: a view (span) source takes `Bytes(x)`


@dataclass(frozen=True)
class _ValueFieldPlan:
    """A value-family field write: no borrow<->storage lift beyond the bytes
    view copy; the render kind is decided from the field type once, in chain
    order, so overlapping slices (value-opt owned-str vs plain str) resolve
    by position exactly as the arm chain did."""
    render: _ValueRender
    ftype: TpyType
    bytes_ft: TpyType | None = None


def _opt_callable_field(ftype) -> bool:
    """An `Optional[Callable]` field slot (`std::optional<std::function>`
    by value): the operator= absorbs the bare callable-name render the
    plain Callable field row gets."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ftype)))
         if ftype is not None else None)
    return (isinstance(u, OptionalType)
            and _callable_value(unwrap_readonly(u.inner)))


def _closure_field_fence(lam: TpyLambda, slot: _ExprUse, analyzer) -> None:
    """A closure stored in a field copies its by-value captures silently
    (BUGS.md#callable-local-lambda-copies-capture-silently): a body field
    write refuses one, a member-init takes the shapes the lambda gate
    (`_lambda_routable`, no `self` capture) admits."""
    if not _is_direct_init(slot):
        raise ThirUnsupported("expr.lambda")
    if not _lambda_routable(lam, analyzer):
        raise ThirUnsupported("expr.lambda")


def _classify_value(stmt: TpyAssign, slot: _ExprUse,
                    lc: _LowerCtx,
                    declared: dict[str, TpyType],
                    pointers: AbstractSet[str]) -> _ValueFieldPlan | None:
    analyzer = lc.analyzer
    ftype = _slot_type(slot)
    # Kind first, admission second: a shape no render row claims (a
    # type-param slot, a readonly-wrapped elem field) must fall through to
    # the residual family WITHOUT evaluating the admission predicates --
    # some fire witnesses on success, and a witness must fire at most once
    # per statement.
    plan = None
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None
            or _eligible_ptr_value(ftype, analyzer)
            # A Callable field (`std::function` by value) and its Optional
            # flavor: the copy or converting assignment absorbs the closure
            # (None rides the opt-none family ahead of this chain).
            or _callable_value(ftype) or _opt_callable_field(ftype)
            # A Span field: a view slot, fenced at admission.
            or _span_value(ftype)):
        plan = _ValueFieldPlan(_ValueRender.PLAIN, ftype)
    elif (_value_opt_scalar(ftype, analyzer) is not None
          and not isinstance(stmt.value, TpyNoneLiteral)):
        plan = _ValueFieldPlan(_ValueRender.VALUE_OPT, ftype)
    elif _resolved_str_value(ftype, analyzer) is not None:
        plan = _ValueFieldPlan(_ValueRender.STR, ftype)
    else:
        bytes_ft = _resolved_bytes_value(ftype, analyzer)
        if bytes_ft is not None and is_bytes_type(bytes_ft):
            plan = _ValueFieldPlan(_ValueRender.BYTES, ftype,
                                   bytes_ft=bytes_ft)
    if plan is None:
        return None
    lam = _peel_coerce(stmt.value)
    if isinstance(lam, TpyLambda):
        _closure_field_fence(lam, slot, analyzer)
    view_slot = slot.lifetime is SlotLifetime.OUTLIVES_STATEMENT
    if view_slot and plan.render is _ValueRender.PLAIN:
        # A view field keeps pointing into its source: a PLAIN one (a Span)
        # admits only a same-typed PARAM, whose buffer is the caller's.
        v = _peel_coerce(stmt.value)
        ok = (isinstance(v, TpyName) and v.name in lc.prescan.param_names
              and v.name in declared
              and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                  declared[v.name]))) == unwrap_readonly(unwrap_ref_type(
                      unwrap_send_sync(ftype)))
              and _field_receiver_ok(stmt.target, declared, analyzer))
        return plan if ok else None
    narrowed = lc.narrow.narrowed.keys()
    if not (_scalar_field_write_ok(stmt, declared, analyzer, pointers,
                                   ftype)
            or _user_deref_field_write_ok(stmt, declared, narrowed, analyzer,
                                          pointers, ftype)
            or _str_field_write_ok(stmt, declared, analyzer, pointers, ftype,
                                   view_slot)
            or _bytes_field_write_ok(stmt, declared, analyzer, pointers,
                                     ftype)
            or _btuple_elem_field_write_ok(stmt, declared, lc, ftype)):
        return None
    return plan


def _lower_value_field(stmt: TpyAssign, plan: _ValueFieldPlan,
                       slot: _ExprUse, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRExpr:
    # The `forms=_NO_FORMS` beside each `pos=SinkPos.FIELD_WRITE` below is
    # the position's OWN row restated, not a narrowing of it: a field write
    # keeps the value past the statement, so the dying-source lend is refused
    # here on purpose, and restating it is what makes the site readable
    # beside the arms that DO hand out a verdict.
    # Every name's read is routed here, the unrouted Own payloads included:
    # the sink reads the node's move facts (`_value_moved`), so a last use
    # moves and any other read copies.
    if plan.render is _ValueRender.STR:
        _witness("field_write.str")
        # A field-read source (`self.f = o.name`) is the bare member read
        # the assign copies from; a direct-init constructs the owned string
        # from whatever the source renders (the explicit view ctor fires).
        return _value_moved(
            _lower_expr(stmt.value, lc, declared,
                        use=replace(slot, pos=_value_sink_pos(slot),
                                    forms=_NO_FORMS),
                        field_owned_str_ok=True,
                        allow_unrouted_name=True),
            plan.ftype, slot, loc)
    if plan.render is _ValueRender.BYTES:
        _witness("field_write.bytes")
        bval = _lower_expr(stmt.value, lc, declared,
                           use=replace(slot, pos=_value_sink_pos(slot),
                                       forms=_NO_FORMS),
                           field_owned_str_ok=True,
                           allow_unrouted_name=True)
        if bval.form is Form.BORROW:
            bval = THIRFormConvert(result_type=plan.bytes_ft, value=bval,
                                   form=Form.STORAGE, move=False, loc=loc)
        return _value_moved(bval, plan.ftype, slot, loc)
    if isinstance(stmt.value, TpyNoneLiteral):
        # LITERAL construction: `None` into a `Ptr[T]` field is the null
        # pointer, into a `None`-typed field the monostate.
        return THIRLiteral(
            result_type=plan.ftype, value=None,
            form=(Form.STORAGE
                  if isinstance(unwrap_readonly(plan.ftype), NoneType)
                  else Form.VALUE),
            loc=loc)
    if plan.render is _ValueRender.VALUE_OPT:
        _witness("field_write.value_opt_scalar")
    elif _opt_callable_field(plan.ftype):
        _witness("field_write.opt_callable_name")
    return _value_moved(_slot_literal_retype(
        _flush_witness(
            "flush.field_write",
            _lower_expr(stmt.value, lc, declared,
                        use=replace(slot, result=_ExprResultUse.STORAGE),
                        allow_unrouted_name=True,
                        # A source typed as the value-repr Optional itself is
                        # consumed WHOLE (the `std::optional<T>` member
                        # copies bare); a narrowed one reads its inner.
                        allow_whole_optional=(
                            plan.render is _ValueRender.VALUE_OPT
                            and isinstance(_source_type(stmt.value,
                                                        lc.analyzer),
                                           OptionalType)))),
        plan.ftype, lc), plan.ftype, slot, loc)


def _value_moved(value: THIRExpr, ftype: TpyType, slot: _ExprUse,
                 loc) -> THIRExpr:
    """A member-init moves a value-typed `Own` param NAME at its last use
    (`s(std::move(s))` for an `Own[str]`); a body field write copies a
    value-typed source even where `movable_now` holds -- a value-typed
    frame local is in `movable_locals` for the sinks that move whole
    storage, not for this one."""
    if (not _is_direct_init(slot)
            or not isinstance(value, THIRName) or not _node_moves(value)):
        return value
    return THIRMove(value=value, result_type=ftype, form=Form.STORAGE,
                    loc=loc)


def _object_source_materialize(lowered: THIRExpr, analyzer) -> 'bool | None':
    """The ONE fact a field write's borrow->storage convert cannot derive
    from (family, form): whether the SOURCE arrived as a view to copy into
    the slot's buffer, or as the object itself. A source on the REFERENCE
    axis is the object -- there is nothing to materialize, so the convert
    moves or copies it -- and `False` pins that. `None` for every other
    source leaves the emit's own (family, form) rule in charge, which is
    what a VALUE-form view source needs: `bytes` / `str` / `BytesView` /
    `StrView` all read as a span or a string_view and owe the owning slot
    the copy.

    Keyed on the source rather than on the slot because the slot's family
    is exactly what does not decide it: `bytearray` is a reference type
    whose storage is the same owned buffer `bytes` has, so at a bytearray
    slot the (family, BORROW->STORAGE) pair has two correct renders and the
    emitter refuses a convert that carries neither (validate.py). Read by
    both convert sites of the reference field write -- the NAME row's move
    wrap and the shared tail -- because such a write reaches whichever of
    the two its slot shape routes to."""
    rt = lowered.result_type
    if rt is None:
        return None
    return (False
            if record_like(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(rt))), analyzer)
            else None)


def _storage_slot(t: 'TpyType | None', analyzer) -> bool:
    """The field slots whose value render is read off the LOWERED source:
    the reference axis (records, builtin containers), an Optional over a
    reference / type-param / owned str-bytes / value-tuple inner, a union
    (pointer-variant or value), and every storage tuple. Scalar, str-family
    and bytes slots keep the value family's renders."""
    if t is None:
        return False
    if record_like(t, analyzer):
        return True
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(t.inner)
        return (record_like(inner, analyzer)
                or isinstance(inner, TypeParamRef)
                or _value_opt_owned_view(t, analyzer) is not None
                or _value_tuple(inner, analyzer) is not None)
    return (isinstance(t, RecursiveAliasInstanceType)
            or _storage_copy_value(t, analyzer))


def _holds_viewfam(t: TpyType, analyzer) -> bool:
    """Whether a slot stores an owned str / bytes buffer a live view of the
    field could borrow (an Optional inner, a tuple element, a union
    member): its write takes the view-family receiver fence."""
    if (_resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None):
        return True
    if isinstance(t, OptionalType):
        return _holds_viewfam(unwrap_readonly(t.inner), analyzer)
    if isinstance(t, TupleType):
        return any(_holds_viewfam(unwrap_readonly(e), analyzer)
                   for e in t.element_types)
    if isinstance(t, UnionType):
        return any(_holds_viewfam(unwrap_readonly(m), analyzer)
                   for m in t.members)
    return False


@dataclass(frozen=True)
class _StoragePlan:
    """A field write into a storage slot. The slot alone decides the plan;
    the source is lowered once, and its node facts -- form, type, last use
    -- pick the render (`_storage_value`)."""
    # The DECLARED slot: a flow-narrowed `Optional[C]` field reads as `C`
    # but still stores the optional.
    slot_t: TpyType


def _classify_storage(stmt: TpyAssign, slot: _ExprUse, lc: _LowerCtx,
                      declared: dict[str, TpyType],
                      pointers: AbstractSet[str]) -> _StoragePlan | None:
    analyzer = lc.analyzer
    slot_t = _slot_type(slot)
    if slot_t is None or not _storage_slot(slot_t, analyzer):
        return None
    if (slot.lifetime is SlotLifetime.OUTLIVES_STATEMENT
            and not view_slot_shape_ok(slot_t, stmt.value, analyzer)):
        return None
    target = stmt.target
    narrowed = lc.narrow.narrowed.keys()
    # The receivers each slot shape admits are the target side's own facts:
    # a view-family slot excludes the receivers the borrow tracker cannot
    # key (a live view of the field would survive the write), a reference
    # slot takes the unbound-self and user-Deref receivers, an Optional one
    # the mutable-ref method receiver.
    if record_like(slot_t, analyzer):
        ok = (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
              or _user_deref_field_recv_ok(target, declared, narrowed,
                                           analyzer, pointers))
    elif _holds_viewfam(slot_t, analyzer):
        ok = _viewfam_field_write_receiver_ok(target, declared, analyzer,
                                              pointers)
    elif isinstance(slot_t, OptionalType):
        ok = (_field_receiver_or_unbound_self_ok(target, declared, analyzer)
              or _method_recv_field_write_ok(target, declared, analyzer))
    else:
        ok = _field_receiver_ok(target, declared, analyzer)
    if not ok:
        return None
    # A demoted ctor init of a field with no default constructor has no
    # default state to assign over; the diagnostic belongs to the ctor.
    if (lc.prescan.is_constructor and not _is_direct_init(slot)
            and record_like(slot_t, analyzer)
            and _nondef_ctor_field(slot_t, analyzer)):
        return None
    return _StoragePlan(slot_t)


def _absorbed_member(slot_t: TpyType, src_t: 'TpyType | None',
                     analyzer) -> TpyType:
    """The part of the slot a source of type `src_t` lands in: the slot
    itself, or -- for a source typed as an Optional's inner or one union
    member -- that inner / member, which `optional::operator=` and the
    variant's converting assignment absorb. A str / bytes view lands in
    its owned twin (the view->owned copy is the member's)."""
    if src_t is None or src_t == slot_t:
        return slot_t
    if isinstance(slot_t, OptionalType):
        members: tuple = (unwrap_readonly(slot_t.inner),)
    elif isinstance(slot_t, UnionType):
        members = tuple(unwrap_readonly(m) for m in slot_t.members)
    else:
        return slot_t
    for m in members:
        if src_t == m:
            return m
    for m in members:
        if _same_viewfam(src_t, m, analyzer) or _record_upcast(src_t, m,
                                                               analyzer):
            return m
    return slot_t


def _record_upcast(src_t: TpyType, t: TpyType, analyzer) -> bool:
    """A record source a record slot takes by conversion: a covariant
    generic upcast (representation-preserving), or a subclass the assign
    slices (sema warns the narrowing)."""
    return (_covariant_record_upcast_ok(src_t, t, analyzer)
            or _record_slice_upcast_ok(src_t, t, analyzer))


def _same_viewfam(a: TpyType, b: TpyType, analyzer) -> bool:
    return ((_resolved_str_value(a, analyzer) is not None
             and _resolved_str_value(b, analyzer) is not None)
            or (_resolved_bytes_value(a, analyzer) is not None
                and _resolved_bytes_value(b, analyzer) is not None))


def _storage_type(t: 'TpyType | None') -> 'TpyType | None':
    """A type as the slot's storage sees it: the transparent wrappers and
    `Own` peeled, also under an Optional (`Own[T] | None` stores the same
    `std::optional<T>` a `T | None` field holds)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(t.inner)
        if isinstance(inner, OwnType):
            t = replace(t, inner=unwrap_readonly(inner.wrapped))
    if isinstance(t, TupleType) and any(
            isinstance(unwrap_readonly(e), OwnType) for e in t.element_types):
        elems = [unwrap_readonly(e) for e in t.element_types]
        t = replace(t, element_types=tuple(
            unwrap_readonly(e.wrapped) if isinstance(e, OwnType) else e
            for e in elems))
    return t


def _source_type(v: TpyExpr, analyzer) -> 'TpyType | None':
    return _storage_type(analyzer.get_expr_type(v))


def _storage_literal(stmt: TpyAssign, plan: _StoragePlan, slot: _ExprUse,
                     lc: _LowerCtx, declared: dict[str, TpyType],
                     loc) -> 'THIRExpr | None':
    """LITERAL construction: a container literal, a `[e] * n` repeat, a
    comprehension, a tuple literal or `None` is BUILT against the slot --
    its element types, its brace spelling and its pending-literal types
    come from the slot, not from the source -- so these rows name the
    literal's kind. None for every other source."""
    analyzer = lc.analyzer
    v = stmt.value
    st = plan.slot_t
    opt_inner = _optional_container_storage_inner(st)
    lit_t = opt_inner if opt_inner is not None else st
    if (isinstance(v, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                       TpyListRepeat))
            and _container_storage_field(lit_t)):
        if not _container_literal_shape_ok(v, lit_t, analyzer):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        if isinstance(v, TpyListRepeat):
            # The repeat threads the FIELD type -- an untargeted resolve
            # demotes it to the Array flavor.
            _witness("field_write.container_repeat")
            return _lower_expr(v, lc, declared,
                               use=replace(slot, slot_target=st))
        if opt_inner is not None:
            # A storage-form `std::optional<C>` field: lowered against the
            # INNER (threading the Optional would derive the element
            # targets from it); dict/set literals spell their container
            # already.
            value = _lower_expr(v, lc, declared,
                                use=replace(slot, slot_target=opt_inner))
            if isinstance(v, TpyArrayLiteral):
                value = _self_described_brace(value, opt_inner, stmt, lc)
            _witness("field_write.opt_container_lit")
            return value
        _witness("field_write.container_lit")
        if _is_direct_init(slot):
            # The member-init threads the field type as the literal's
            # target, like a decl init.
            value = _lower_expr(v, lc, declared,
                                use=replace(slot, slot_target=st))
            if isinstance(v, TpyArrayLiteral):
                value = _self_described_brace(value, st, stmt, lc)
            return value
        return _lower_expr(v, lc, declared,
                           use=replace(slot, pos=SinkPos.FIELD_WRITE,
                                       forms=_NO_FORMS))
    if isinstance(v, (TpyListComprehension, TpySetComprehension,
                      TpyDictComprehension)):
        if not (_container_storage_field(lit_t)
                and _container_comp_arg(v, lit_t)):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("field_write.opt_container_comp" if opt_inner is not None
                 else "field_write.container_comp")
        return _comprehensions._lower_comprehension(
            v, lit_t, lc, declared,
            _comp_shadow_pointers(lc.pointers, declared, analyzer))
    if isinstance(v, TpyTupleLiteral):
        return _storage_tuple_literal(stmt, st, lc, declared, loc)
    if (isinstance(st, RecursiveAliasInstanceType)
            and isinstance(v, (TpyArrayLiteral, TpyDictLiteral))):
        # A container literal into a recursive-alias wrapper field: the
        # wrapper-instance spelled render, target-threaded like a decl init.
        if not _ru_instance_literal_ok(v, analyzer):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        _witness("field_write.recursive_alias_literal")
        return _lower_ru_literal(v, analyzer.get_expr_type(v), lc, declared)
    if isinstance(v, TpyNoneLiteral):
        # `None` into a union slot stores the monostate member; into a
        # type-param slot the default. (An Optional slot's None is the
        # opt-none family's, ahead of this one.) A recursive-alias wrapper
        # has no None spelling of its own.
        if not isinstance(st, (UnionType, TypeParamRef)):
            note_detail("assign.field_write_shape")
            raise ThirUnsupported(stmt_reject_reason(stmt))
        return THIRLiteral(result_type=st, value=None, form=Form.STORAGE,
                           loc=loc)
    vu = _eligible_value_union(st)
    if vu is not None and isinstance(_peel_coerce(v), (
            TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        # A numeric literal into a value union takes its type from the
        # union (pending-literal typing): retyped to the union, the BigInt
        # wrap / float32 suffix keyed on the literal's own scalar type never
        # fires, and the variant's converting assignment picks the member.
        value = _lower_expr(v, lc, declared, use=slot)
        if isinstance(value, THIRLiteral) and isinstance(value.value,
                                                         (int, float)):
            value = replace(value, result_type=vu)
        _witness("field_write.value_union_literal")
        return value
    return None


def _self_described_brace(value: THIRExpr, slot_t: TpyType,
                           stmt: TpyAssign, lc: _LowerCtx) -> THIRExpr:
    """Spell a bracket literal's slot type in front of its brace-init
    (`xs(std::vector<BigInt>{1})`), where a bare brace would not be a
    list-init of the slot: a member-init's paren direct-init hands the brace
    to the slot type's constructor overloads (`xs({1})` picks the size
    constructor), and an `std::optional<C>`'s converting constructor has no
    type to deduce from `{10, 20}`. A bracket literal at a container slot
    lowers to a container literal or raises, so anything else here lost the
    prefix's home and rejects."""
    if not isinstance(value, THIRContainerLiteral):
        note_detail("assign.field_write_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    return replace(value, typed_brace_cpp=lc.render_type(slot_t))


def _is_direct_init(slot: _ExprUse) -> bool:
    return (slot.dest is not None
            and slot.dest.construct is SlotConstruct.DIRECT_INIT)


def _storage_tuple_literal(stmt: TpyAssign, st: TpyType, lc: _LowerCtx,
                           declared: dict[str, TpyType], loc) -> THIRExpr:
    """A tuple LITERAL at a tuple slot. A VALUE tuple (and an Optional of
    one) takes the spelled brace-init directly -- borrow and storage
    coincide; an F3 tuple wraps the literal in `tuple_to_storage`; a
    nested-storage tuple spells its bare brace-init with per-level lifts
    inside."""
    analyzer = lc.analyzer
    lit_st = unwrap_readonly(st.inner) if isinstance(st, OptionalType) else st
    vt = _value_tuple(lit_st, analyzer)
    ft = None if vt is not None else _f1_tuple(st, analyzer)
    nt = (None if vt is not None or ft is not None
          else _nested_storage_tuple(st, analyzer))
    slot_t = vt or ft or nt
    v = stmt.value
    if (slot_t is None
            or len(v.elements) != len(slot_t.element_types)
            or (nt is None
                and _tuple_literal_has_ref_elements(v, slot_t))):
        note_detail("assign.field_write_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    lit = _lower_tuple_literal(v, slot_t, lc, declared)
    _witness("field_write.tuple_literal")
    if ft is not None:
        return THIRFormConvert(result_type=st, value=lit, form=Form.STORAGE,
                               move=False, loc=loc)
    return lit


def _storage_use(slot: _ExprUse, member_t: TpyType, analyzer) -> _ExprUse:
    """The one use a storage slot hands its (non-literal) source. A field
    keeps what it is handed past the statement, so the dying-source lend
    stays refused. What the sink admits follows from the member the source
    lands in:

      * every member: the copy-assign takes a stored lvalue whole -- a
        field read, a container element, a borrowed call result
        (RECORD_COPY);
      * every member taken as a value (not lifted): a pointer-bound source
        reads through its deref (INDIRECT_READ);
      * a record / container member also a select of existing objects
        (SELECT_PRVALUE, which sema warns as a copy);
      * a pointer-repr Optional / pointer-variant union member: the source
        lifts WHOLE (PTR_OPT_LIFT / UNION_VALUE_LIFT) -- it is read as the
        `T*` / pointer variant it is, never dereffed.

    STORAGE result: the slot owns what it is handed, so a by-value call
    result lands whole. The right to hoist an argument temp is the slot
    contract's (`field_slot_use`), carried through unchanged."""
    ptr_opt = (isinstance(member_t, OptionalType)
               and member_t.uses_pointer_repr())
    ptr_union = _eligible_ptr_union(member_t, analyzer) is not None
    forms = _slot_lift_forms(ptr_opt, ptr_union) | _STORAGE_COPY_FORMS
    if not (ptr_opt or ptr_union):
        forms = forms | _VALUE_READ_FORMS
    if record_like(member_t, analyzer):
        forms = forms | _REF_MEMBER_FORMS
    if _f1_tuple(member_t, analyzer) is not None:
        # A mixed own + borrow tuple result (`std::tuple<Box, Box*>`) is
        # taken whole and lifted to the storage tuple.
        forms = forms | _ONLY_BTUPLE_SLOT
    return replace(slot, result=_ExprResultUse.STORAGE,
                   pos=SinkPos.FIELD_WRITE, forms=forms,
                   slot_target=member_t)


def _borrow_tuple(node: THIRExpr) -> bool:
    """A tuple that arrives in borrow form (`std::tuple<Box, Box*>`): a
    mixed own / borrow name or call result, whatever its stripped type
    says. The storage lift converts it; nothing moves it whole."""
    rt = _storage_type(node.result_type)
    return node.form is Form.BORROW and isinstance(rt, TupleType)


def _raw_pointer(node: THIRExpr) -> bool:
    """A source that arrives as a bare `T*` rather than a reference or a
    value: a pointer binding read without its deref (`THIRName.raw_pointer`),
    or a borrow element of a pointer-repr tuple. Only a whole lift consumes
    one; every other slot needs the deref the read did not take."""
    if isinstance(node, THIRName):
        return node.raw_pointer
    return (isinstance(node, THIRSubscript) and node.tuple_index is not None
            and node.form is Form.BORROW)


def _absorbs(member_t: TpyType, rt: 'TpyType | None', lc: _LowerCtx) -> bool:
    """Whether the slot's own assignment takes a value of type `rt` as it
    is: the same type, an Optional's inner or a union's member (the
    converting assignment), a str / bytes value into its owned twin, a
    record into its base (the assign slices -- sema warns), or a tuple
    whose still-pending literal elements take the slot's element types."""
    analyzer = lc.analyzer
    if rt is None or rt == member_t:
        return True
    if isinstance(member_t, (OptionalType, UnionType)):
        return _absorbed_member(member_t, rt, analyzer) != member_t
    if _same_viewfam(rt, member_t, analyzer):
        return True
    if _record_upcast(rt, member_t, analyzer):
        return True
    if (isinstance(rt, TupleType) and isinstance(member_t, TupleType)
            and len(rt.element_types) == len(member_t.element_types)):
        # A still-pending literal element takes its type from the slot's
        # element (pending-literal typing): a tuple literal written in
        # place (`self.pair = copy((77, b))`); a tuple read from a
        # function-local list has its cells' settled members.
        return all(isinstance(e, (IntLiteralType, FloatLiteralType,
                                  LiteralType))
                   or _absorbs(unwrap_readonly(m), unwrap_readonly(e), lc)
                   for e, m in zip(rt.element_types,
                                   member_t.element_types))
    return False


def _storage_lift_renders(member_t: TpyType, rt: 'TpyType | None',
                          lowered: THIRExpr, lc: _LowerCtx) -> bool:
    """Whether the borrow->storage conversion has a render for this
    (source, member) pair: the pointer-repr Optional and pointer-variant
    lifts over a source of that very type, the tuple lift likewise, the
    view->owned copy, the open-`T` construction, and a record / container
    member off a reference (never a pointer) borrow it absorbs."""
    analyzer = lc.analyzer
    if isinstance(member_t, OptionalType):
        return rt == member_t and (member_t.uses_pointer_repr()
                                   or member_t.uses_generic_param_trait())
    if isinstance(member_t, UnionType):
        return (rt == member_t
                and _eligible_ptr_union(member_t, analyzer) is not None)
    if isinstance(member_t, TupleType):
        return rt == member_t and (
            _f1_tuple(member_t, analyzer) is not None
            or _nested_storage_tuple(member_t, analyzer) is not None)
    if (_resolved_str_value(member_t, analyzer) is not None
            or _resolved_bytes_value(member_t, analyzer) is not None):
        # The view->owned copy; a view member has no storage form to lift to.
        return (rt is not None and not holds_borrowing_view(member_t)
                and _same_viewfam(rt, member_t, analyzer))
    if isinstance(member_t, TypeParamRef):
        return True
    return (record_like(member_t, analyzer)
            and _absorbs(member_t, rt, lc)
            and not _raw_pointer(lowered))


def _storage_value(lowered: THIRExpr, member_t: TpyType, slot_t: TpyType,
                   param_t: 'TpyType | None', mv: bool, whole: bool,
                   direct: bool, lc: _LowerCtx, loc) -> THIRExpr:
    """The render of a lowered source at a storage slot, read off the node:
    a move at the last use of an owned name, the borrow->storage lift for a
    borrowed source, the view->owned shim for a borrowed value-optional
    view param, and the bare assignment -- the slot's own copy or
    converting assignment -- for everything already in storage or value
    form. A (form, member) pair with no render rejects, named by the
    form.

    `whole`: the source NAME's binding is the slot's own Optional / union,
    read whole -- its node carries the flow-narrowed type, but what it
    renders is the whole binding. `direct`: a member-init, whose direct-init
    constructs an open `T` from the param form itself.

    STORAGE on the node is the read arm's word that the source IS storage: a
    member or element read names its slot's own optional / variant / tuple,
    an `Own`-declared name or call result owns what it hands over. A mixed
    own / borrow tuple, whatever its stripped type, arrives BORROW."""
    analyzer = lc.analyzer
    rt = member_t if whole else _storage_type(lowered.result_type)
    if (isinstance(lowered, THIRName) and param_t is not None
            and _opt_view_arg_shim(param_t, slot_t, analyzer)):
        # A borrowed `optional<view>` PARAM into an owned `optional<str>`
        # slot: the view->owned shim every other owned sink spells.
        _witness("field_write.optview_shim")
        return THIROptViewArg(result_type=slot_t, name=lowered.name,
                              form=Form.VALUE, loc=loc)
    named = lowered
    while isinstance(named, THIRCoerce):
        named = named.expr
    if (isinstance(unwrap_readonly(slot_t), UnionType)
            and lands_in_view_member(slot_t, named.result_type)):
        # The view member of a union: a member-init direct-initializes it
        # from the source; a body write from a NAME keeps master's reject
        # (BUGS.md#record-view-field-escapes-local-buffer).
        if direct and _absorbs(member_t, rt, lc):
            return lowered
        if not direct and isinstance(named, THIRName):
            raise ThirUnsupported(
                f"field_write.lift.{lowered.form.name.lower()}", detail=True)
    stored = lowered.form is Form.STORAGE or isinstance(rt, NoneType)
    if rt == member_t and stored:
        if mv:
            return THIRMove(value=lowered, result_type=member_t,
                            form=Form.STORAGE, loc=loc)
        return lowered
    ptr_lift = (rt == member_t
                and ((isinstance(member_t, OptionalType)
                      and member_t.uses_pointer_repr())
                     or _eligible_ptr_union(member_t, analyzer) is not None))
    if (mv and not ptr_lift and not _borrow_tuple(lowered)
            and not record_like(member_t, analyzer)
            and not isinstance(member_t, TypeParamRef)
            and not _raw_pointer(lowered)
            and _absorbs(member_t, rt, lc)):
        # An owned VALUE-typed name at its last use (a tuple / union /
        # optional local the frame holds by value): it moves in whole. A
        # pointer-repr tuple (the mixed own + borrow param, handed over and
        # movable) is not: its borrowed element must materialize through
        # the moving lift below.
        return THIRMove(value=lowered, result_type=member_t,
                        form=Form.STORAGE, loc=loc)
    if (not mv and not ptr_lift
            and not isinstance(member_t, TypeParamRef)
            and not _raw_pointer(lowered)
            and _absorbs(member_t, rt, lc)
            and (lowered.form is not Form.BORROW
                 or (member_t == slot_t and record_like(member_t, analyzer)))):
        # A value / owned rvalue, or a REFERENCE borrow of a record or
        # container at its own slot: the slot's copy or converting
        # assignment takes it as it is. (Inside an Optional / union the
        # borrow still converts to the member's storage.)
        return lowered
    if direct and not mv and isinstance(member_t, TypeParamRef):
        return lowered
    if not _storage_lift_renders(member_t, rt, lowered, lc):
        raise ThirUnsupported(
            f"field_write.lift.{lowered.form.name.lower()}", detail=True)
    # The union STORAGE conversion never moves (to_value_variant deref-
    # copies the active member), so the node's move flag stays a real-move
    # claim.
    union = _eligible_ptr_union(member_t, analyzer) is not None
    return THIRFormConvert(result_type=member_t, value=lowered,
                           form=Form.STORAGE, move=mv and not union,
                           materialize=_object_source_materialize(
                               lowered, analyzer),
                           loc=loc)


def _lower_storage_field(stmt: TpyAssign, plan: _StoragePlan,
                         slot: _ExprUse, lc: _LowerCtx,
                         declared: dict[str, TpyType], loc) -> THIRExpr:
    value = _storage_literal(stmt, plan, slot, lc, declared, loc)
    if value is None:
        value = _storage_source(stmt, plan.slot_t, slot, lc, declared, loc)
    return value


def _storage_source(stmt: TpyAssign, st: TpyType, slot: _ExprUse,
                    lc: _LowerCtx, declared: dict[str, TpyType],
                    loc) -> THIRExpr:
    """Lower a non-literal source ONCE, under the use the slot settles, and
    render it from the lowered node (`_storage_value`)."""
    analyzer = lc.analyzer
    v = stmt.value
    record_st = record_like(st, analyzer)
    opt_inner = (unwrap_readonly(st.inner)
                 if isinstance(st, OptionalType) else None)
    # `copy()` is an explicit construction: a record copy-constructs its
    # argument (`T(name)`), `copy(T(...))` is the constructor itself, and a
    # pointer-repr Optional's copy is the identity -- whose sink lift is
    # what copies, so the peeled name never moves.
    copy_slot = (st if record_st
                 else opt_inner if opt_inner is not None
                 and record_like(opt_inner, analyzer) else None)
    if copy_slot is not None:
        copy_row = _lower_copy_record(v, lc, declared, slot_type=copy_slot,
                                      loc=loc)
        if copy_row is not None:
            _witness("field_write.record_copy")
            return copy_row
        ctor_peel = copy_ctor_rvalue_source(v, analyzer)
        if ctor_peel is not None:
            _witness("field_write.record_copy_ctor")
            return _lower_expr(ctor_peel, lc, declared,
                               use=_storage_use(slot, copy_slot, analyzer))
    peeled = copy_ptr_optional_peel(v, analyzer)
    src = peeled if peeled is not None else v
    member_t = _absorbed_member(st, _source_type(src, analyzer), analyzer)
    param_t = None
    whole_binding = False
    if isinstance(src, TpyName) and src.name in declared:
        param_t = _param_declared_type(src.name, lc)
        whole_binding = (_storage_type(declared[src.name]) == st
                         and isinstance(st, (OptionalType, UnionType)))
        if whole_binding:
            # The NAME's storage is the slot's own Optional / union, flow
            # narrowing notwithstanding: it is read WHOLE and lifts whole.
            member_t = st
        elif (src.name in lc.pointers and isinstance(st, OptionalType)
              and st.uses_pointer_repr()):
            # A POINTER-bound source is already the borrow form of the
            # pointer-repr Optional slot: it lifts whole
            # (`ptr_to_optional[_move](p)`), never dereffed into the inner.
            member_t = st
    use = _storage_use(slot, member_t, analyzer)
    # A pointer-slot GLOBAL is read as the pointer it is: the lift takes
    # the `T*` (`h->v = ptr_to_optional(g);`), so the value-position deref
    # every other sink applies to it must not fire.
    if (isinstance(src, TpyName) and src.name in lc.prescan.global_slots
            and not record_like(member_t, analyzer)):
        use = replace(use, result=_ExprResultUse.RECEIVER, forms=None,
                      pos=SinkPos.UNSPECIFIED)
    whole = member_t == st
    lowered = _lower_expr(
        src, lc, declared, use=use,
        # An owned name's write IS its routed read -- the sink reads the
        # node's move facts and moves it at its last use or copies it.
        allow_unrouted_name=True,
        # A source landing in the slot's own Optional / union is consumed
        # WHOLE: no narrowing deref, no member-typed divergence.
        allow_whole_optional=whole and isinstance(st, OptionalType),
        allow_union_divergent=whole and isinstance(st, UnionType))
    _flush_witness("flush.field_write", lowered)
    pointer_bound = isinstance(src, TpyName) and src.name in lc.pointers
    if member_t == st and not whole_binding and not pointer_bound:
        # A source whose sema type is still pending (a literal-seeded local)
        # lands in the member its LOWERED type names.
        member_t = _absorbed_member(st, _storage_type(lowered.result_type),
                                    analyzer)
    mv = peeled is None and _node_moves(lowered)
    return _storage_value(lowered, member_t, st, param_t, mv,
                          whole_binding, _is_direct_init(slot), lc, loc)


@dataclass(frozen=True)
class _AnyFieldPlan:
    """An `Any` FIELD write: the default field assign with the value's
    sema-inserted `into_any` coerce rendered by `_lower_into_any`'s `{0}`
    template (`h.payload = ::tpy::make_any(n);`; a movable inner name's last
    use moves the whole make_any value), or an already-Any value copied
    bare."""
    ftype: TpyType


def _classify_any(stmt: TpyAssign, slot: _ExprUse,
                  lc: _LowerCtx,
                  declared: dict[str, TpyType],
                  pointers: AbstractSet[str]) -> _AnyFieldPlan | None:
    analyzer = lc.analyzer
    ftype = _slot_type(slot)
    if not _statements._is_any_type(ftype):
        return None
    # LITERAL construction: a container literal's brace-init has no type to
    # deduce inside the `make_any` wrap, so it stays out.
    if isinstance(_peel_coerce(stmt.value), CONTAINER_LITERAL_NODES):
        return None
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return None
    return _AnyFieldPlan(ftype)


def _lower_any_field(stmt: TpyAssign, plan: _AnyFieldPlan,
                     slot: _ExprUse, lc: _LowerCtx,
                     declared: dict[str, TpyType], loc) -> THIRExpr:
    _witness("field_write.any")
    value = _lower_expr(stmt.value, lc, declared, use=slot)
    if _node_moves(value):
        # The move peels the coerce and hands make_any's result to the
        # field as an rvalue. The SOURCE name is still copy-constructed into
        # make_any's by-value parameter, so a last-use write copies the
        # payload once (BUGS.md#any-field-write-copies-source).
        value = THIRMove(result_type=plan.ftype, value=value,
                         form=Form.STORAGE, loc=loc)
    return value


@dataclass(frozen=True)
class _ResidualPlan:
    """The slots the earlier families do not claim. `rows`: the shapes the
    value family's render rows do not claim but whose predicates admit -- a
    generic record's type-param `T` field (a plain assign whose
    BORROW->STORAGE convert renders the source bare/moved per instantiation)
    and the readonly-wrapped borrow-tuple-element edge -- which take the
    shared tail render (ftype is never Optional or a union there, so it
    collapses to the bare-copy-vs-FormConvert verdict). Any other slot no
    family renders (a recursive-alias wrapper, a tuple of type params) takes
    ONE source, read off the lowered node: a name that moves here relocates
    in whole (`t(std::move(t))`); every other source rejects by its form."""
    ftype: TpyType
    rows: bool


def _classify_residual(stmt: TpyAssign, slot: _ExprUse,
                       lc: _LowerCtx,
                       declared: dict[str, TpyType],
                       pointers: AbstractSet[str]) -> _ResidualPlan | None:
    analyzer = lc.analyzer
    ftype = _slot_type(slot)
    if (_scalar_field_write_ok(stmt, declared, analyzer, pointers, ftype)
            or _btuple_elem_field_write_ok(stmt, declared, lc, ftype)):
        return _ResidualPlan(ftype, rows=True)
    if ftype is None or not _field_receiver_ok(stmt.target, declared,
                                               analyzer):
        return None
    return _ResidualPlan(ftype, rows=False)


def _lower_residual_field(stmt: TpyAssign, plan: _ResidualPlan,
                          slot: _ExprUse, lc: _LowerCtx,
                          declared: dict[str, TpyType], loc) -> THIRExpr:
    if plan.rows:
        if isinstance(stmt.value, TpyNoneLiteral):
            return THIRLiteral(result_type=plan.ftype, value=None,
                               form=Form.STORAGE, loc=loc)
        return _storage_source(
            stmt, unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                plan.ftype))),
            slot, lc, declared, loc)
    lowered = _lower_expr(stmt.value, lc, declared, use=slot,
                          allow_unrouted_name=True)
    # A raw `T*` read (a narrowed pointer-bound local) is movable too, but
    # what moves is the pointee the read did not deref; a borrow tuple's
    # borrowed element must materialize: no render for either here.
    if (not _node_moves(lowered) or _raw_pointer(lowered)
            or _borrow_tuple(lowered)):
        raise ThirUnsupported(
            f"field_write.lift.{lowered.form.name.lower()}", detail=True)
    _witness("field_write.owned_move")
    return THIRMove(value=lowered, result_type=plan.ftype, form=Form.STORAGE,
                    loc=loc)


# (classify, lower-the-value, the target lowers FIRST): each family keeps
# its target-vs-value lowering order, because temp numbering follows it.
_FAMILIES: list[tuple[Callable, Callable, bool]] = [
    (_classify_opt_none, _lower_opt_none, True),
    (_classify_value, _lower_value_field, True),
    (_classify_storage, _lower_storage_field, False),
    (_classify_any, _lower_any_field, False),
    (_classify_residual, _lower_residual_field, False),
]


def _field_slot(stmt: TpyAssign, lc: _LowerCtx, declared: dict[str, TpyType],
                construct: SlotConstruct) -> _ExprUse:
    """The write's slot contract, settled from the DECLARED slot before any
    family classifies: a flow-narrowed `Optional` field reads as its inner,
    but it still stores the optional."""
    decl_slot = _container_field_write_slot(stmt, declared, lc.analyzer)
    if decl_slot is None:
        # The declaration walk answers for record, Optional-record and Ptr
        # receivers; any other receiver (a user-Deref wrapper, whose field
        # lives on the deref target) takes its field's expression type,
        # which no flow fact narrows there.
        t = lc.analyzer.get_expr_type(stmt.target)
        decl_slot = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                     if t is not None else None)
    return field_slot_use(decl_slot, construct)


def _slot_type(slot: _ExprUse) -> 'TpyType | None':
    """The declared slot every family classifies off (`_field_slot`)."""
    return slot.dest.type if slot.dest is not None else None


def lower_field_write(stmt: TpyAssign, lc: _LowerCtx,
                      declared: dict[str, TpyType],
                      pointers: AbstractSet[str],
                      loc) -> THIRAssign:
    """First matching family lowers the statement; a statement no family
    claims raises with the shared field-write reject tag."""
    slot = _field_slot(stmt, lc, declared, SlotConstruct.ASSIGN)
    for i, (classify, lower, target_first) in enumerate(_FAMILIES):
        if i == 1:
            # A class-constant target has its own lvalue render (the
            # receiver's evaluation split off), so it is not a family value.
            const = _classify_class_const(stmt, slot, lc, declared, pointers)
            if const is not None:
                return _lower_class_const(stmt, const, slot, lc, declared,
                                          loc)
        plan = classify(stmt, slot, lc, declared, pointers)
        if plan is None:
            continue
        if target_first:
            target = _lower_field_write_target(stmt, lc, declared)
            value = lower(stmt, plan, slot, lc, declared, loc)
        else:
            value = lower(stmt, plan, slot, lc, declared, loc)
            target = _lower_field_write_target(stmt, lc, declared)
        return THIRAssign(target=target, value=value, loc=loc)
    note_detail("assign.field_write_shape")
    raise ThirUnsupported(stmt_reject_reason(stmt))


def lower_member_init_value(stmt: TpyAssign, lc: _LowerCtx,
                            declared: dict[str, TpyType]) -> THIRExpr:
    """The value of a ctor member-init `f(<value>)`: the same families as a
    field write, over a slot that DIRECT-initializes and has no statement
    to hoist a temporary before. The whole lowering runs under a
    NO_FLUSH_POINT scope (`SlotPlacement`), so an operand anywhere in the
    source -- including the tuple and comprehension rows that bypass
    `_lower_expr` -- that would hoist a declaration raises a
    `no_flush`-marked reject, and the caller demotes the init to the ctor
    body, where a statement hosts it."""
    slot = _field_slot(stmt, lc, declared, SlotConstruct.DIRECT_INIT)
    with lc.placement_scope(SlotPlacement.NO_FLUSH_POINT):
        loc = getattr(stmt, "loc", None)
        arg = copy_call_arg(_peel_coerce(stmt.value), lc.analyzer)
        if arg is not None and not is_rvalue_source(lc.analyzer, arg):
            # A direct-init copy-constructs from an existing object
            # already, so an explicit `copy(x)` adds nothing: the
            # member-init is `f(x)`. A fresh argument keeps its copy row,
            # which constructs it in place.
            stmt = replace(stmt, value=arg)
        for classify, lower, _target_first in _FAMILIES:
            plan = classify(stmt, slot, lc, declared, lc.pointers)
            if plan is not None:
                return lower(stmt, plan, slot, lc, declared, loc)
        note_detail("assign.field_write_shape")
        raise ThirUnsupported(stmt_reject_reason(stmt))


def _value_sink_pos(slot: _ExprUse) -> SinkPos:
    """The sink a value-family source names: the member-init's own row for a
    direct-init, the field write's otherwise."""
    return (SinkPos.MIL_INIT
            if slot.dest is not None
            and slot.dest.construct is SlotConstruct.DIRECT_INIT
            else SinkPos.FIELD_WRITE)
