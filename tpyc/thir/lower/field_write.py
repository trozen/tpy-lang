"""Field-write lowering: `recv.field = <value>`.

The write's slot is settled first, from the DECLARED field type
(`field_slot_use`); a family is then picked by the SLOT alone, each a
(classify, lower) pair over a frozen plan. A family decides what it
admits (its receivers, the source types its slot takes) and how the source
is LOWERED for the slot; how the lowered source then reaches the slot is
the one conversion's (`convert`), read off the node's `Source` and the
slot. No row here names an expression kind except literal construction.
Families are tried in declaration order -- the first non-None plan wins --
so a family whose field-type slice overlaps a later one (None at any
Optional field, class constants before plain scalars) claims its
statements by position.
"""
from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from typing import Callable

from ...parse.nodes import (
    TpyDictComprehension,
    TpyListComprehension,
    TpySetComprehension,
    TpyArrayLiteral,
    TpyAssign,
    TpyBoolLiteral,
    TpyDictLiteral,
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
from ...typesys import (
    NoneType,
    OptionalType,
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
)
from .context import (
    _NO_FORMS, _ONLY_BTUPLE_SLOT, _ExprResultUse, _ExprUse, _LowerCtx,
                      _slot_lift_forms, SinkForm, SinkPos,
                      SlotConstruct, SlotHolds, SlotLifetime,
                      SlotPlacement, FieldSlot, field_slot_class,
                      field_slot_use, opt_callable_slot)
from .convert import Refuse, convert, landing, spelled, storage_type
from .checks import (
    _borrow_tuple_local_type,
    _bytes_field_write_ok,
    _container_comp_arg,
    _container_literal_shape_ok,
    _container_storage_field,
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
    tuple_stores_into,
    _comp_shadow_pointers,
    _eligible_char,
    _eligible_ptr_union,
    _eligible_scalar,
    _eligible_value_union,
    record_like,
    _f1_tuple,
    _nested_storage_tuple,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _field_receiver_or_unbound_self_ok,
    _peel_coerce,
    _resolved_bytes_value,
    _resolved_str_value,
    _ru_instance_literal_ok,
    _tuple_literal_has_ref_elements,
    _user_deref_field_recv_ok,
    _value_tuple,
    copy_call_arg,
    copy_ptr_optional_peel,
)
from ...value_category import CONTAINER_LITERAL_NODES, is_rvalue_source
from . import comprehensions as _comprehensions
from .expressions import (
    _flush_witness,
    _lower_class_const_write_target,
    _lower_copy_record,
    _lower_expr,
    _lower_ru_literal,
    _lower_tuple_literal,
    _slot_literal_retype,
    _subscript_yields_borrow_ptr,
    binding_of,
)

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


@dataclass(frozen=True)
class _ValueFieldPlan:
    """A value-family field write: the slot holds a plain value, so the
    source is lowered as the slot takes it and converted with no
    borrow<->storage lift beyond the bytes view copy. How the source is
    LOWERED is decided from the field type once, in chain order, so
    overlapping slices resolve by position exactly as the arm chain did."""
    ftype: TpyType
    # An owned str / bytes slot reads the source as it renders (the slot's
    # own construction takes a view or the owned value); every other value
    # slot takes the target-typed STORAGE render, its literal retyped.
    owned_viewfam: bool
    # A value-repr Optional slot consumes a source typed as the Optional
    # itself WHOLE (the `std::optional<T>` member copies bare).
    whole_optional: bool = False
    witness: str | None = None


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
    cls = field_slot_class(ftype, analyzer)
    plain = cls is FieldSlot.PLAIN
    if plain:
        plan = _ValueFieldPlan(
            ftype, owned_viewfam=False,
            witness=("field_write.opt_callable_name"
                     if opt_callable_slot(ftype) else None))
    elif (cls is FieldSlot.VALUE_OPT
          and not isinstance(stmt.value, TpyNoneLiteral)):
        plan = _ValueFieldPlan(
            ftype, owned_viewfam=False,
            whole_optional=isinstance(
                storage_type(analyzer.get_expr_type(stmt.value)),
                OptionalType),
            witness="field_write.value_opt_scalar")
    elif cls is FieldSlot.OWNED_STR:
        plan = _ValueFieldPlan(ftype, owned_viewfam=True,
                               witness="field_write.str")
    elif cls is FieldSlot.OWNED_BYTES:
        plan = _ValueFieldPlan(ftype, owned_viewfam=True,
                               witness="field_write.bytes")
    else:
        return None
    lam = _peel_coerce(stmt.value)
    if isinstance(lam, TpyLambda):
        _closure_field_fence(lam, slot, analyzer)
    view_slot = slot.lifetime is SlotLifetime.OUTLIVES_STATEMENT
    if view_slot and plain:
        # A view field keeps pointing into its source: a PLAIN one (a Span)
        # admits only a same-typed PARAM, whose buffer is the caller's.
        b = binding_of(_peel_coerce(stmt.value), lc, declared)
        ok = (b is not None and b.is_param and b.type is not None
              and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                  b.type))) == unwrap_readonly(unwrap_ref_type(
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
    # The `forms=_NO_FORMS` beside the `pos=` below is the position's OWN
    # row restated, not a narrowing of it: a field write keeps the value
    # past the statement, so the dying-source lend is refused here on
    # purpose. Every name's read is routed here, the unrouted Own payloads
    # included: the conversion reads the node's move facts, so a last use
    # moves and any other read copies.
    if plan.owned_viewfam:
        _witness(plan.witness)
        # A field-read source (`self.f = o.name`) is the bare member read
        # the assign copies from; a direct-init constructs the owned string
        # from whatever the source renders (the explicit view ctor fires).
        value = _lower_expr(stmt.value, lc, declared,
                            use=replace(slot, pos=_value_sink_pos(slot),
                                        forms=_NO_FORMS),
                            field_owned_str_ok=True,
                            allow_unrouted_name=True)
        return _converted(value, slot, lc)
    if isinstance(stmt.value, TpyNoneLiteral):
        # LITERAL construction: `None` into a `Ptr[T]` field is the null
        # pointer, into a `None`-typed field the monostate.
        return THIRLiteral(
            result_type=plan.ftype, value=None,
            form=(Form.STORAGE
                  if isinstance(unwrap_readonly(plan.ftype), NoneType)
                  else Form.VALUE),
            loc=loc)
    if plan.witness is not None:
        _witness(plan.witness)
    return _converted(_slot_literal_retype(
        _flush_witness(
            "flush.field_write",
            _lower_expr(stmt.value, lc, declared,
                        use=replace(slot, result=_ExprResultUse.STORAGE),
                        allow_unrouted_name=True,
                        allow_whole_optional=plan.whole_optional)),
        plan.ftype, lc), slot, lc)


def _converted(value: THIRExpr, slot: _ExprUse, lc: _LowerCtx, *,
               explicit_copy: bool = False) -> THIRExpr:
    """The lowered source as the slot takes it (`convert`); a pair with no
    conversion rejects under the field write's own reason family."""
    plan = convert(value, slot.dest, lc.analyzer,
                   explicit_copy=explicit_copy)
    if isinstance(plan, Refuse):
        raise ThirUnsupported("field_write." + plan.key, detail=True)
    if plan.row is not None:
        _witness("field_write." + plan.row)
    return spelled(plan, lc.render_type)


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
    a literal builds against it, any other source is lowered once and its
    stamped facts (`Source`) pick the render (`convert`)."""
    # The DECLARED slot: a flow-narrowed `Optional[C]` field reads as `C`
    # but still stores the optional.
    slot_t: TpyType


def _classify_storage(stmt: TpyAssign, slot: _ExprUse, lc: _LowerCtx,
                      declared: dict[str, TpyType],
                      pointers: AbstractSet[str]) -> _StoragePlan | None:
    analyzer = lc.analyzer
    slot_t = _slot_type(slot)
    if field_slot_class(slot_t, analyzer) is not FieldSlot.STORAGE:
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
        return _storage_tuple_literal(v, st, lc, declared,
                                      stmt_reject_reason(stmt))
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


def _storage_tuple_literal(v: TpyTupleLiteral, st: TpyType, lc: _LowerCtx,
                           declared: dict[str, TpyType],
                           reason: str) -> THIRExpr:
    """A tuple LITERAL at a tuple slot: the spelled brace-init, built
    against the slot. A VALUE tuple's (and an Optional of one's) borrow and
    storage forms coincide; a pointer-repr tuple's literal is value-captured
    here (a REF-captured one is refused below), so the brace-init already
    spells the storage form and no conversion wraps it; each element lands
    in its own storage slot by the container element's rules."""
    analyzer = lc.analyzer
    lit_st = unwrap_readonly(st.inner) if isinstance(st, OptionalType) else st
    slot_t = (_value_tuple(lit_st, analyzer)
              or (st if isinstance(st, TupleType) else None))
    if (slot_t is None
            or len(v.elements) != len(slot_t.element_types)
            or _tuple_literal_has_ref_elements(v, slot_t)):
        note_detail("assign.field_write_shape")
        raise ThirUnsupported(reason)
    _witness("field_write.tuple_literal")
    return _lower_tuple_literal(v, slot_t, lc, declared)


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


def _lower_storage_field(stmt: TpyAssign, plan: _StoragePlan,
                         slot: _ExprUse, lc: _LowerCtx,
                         declared: dict[str, TpyType], loc) -> THIRExpr:
    value = _storage_literal(stmt, plan, slot, lc, declared, loc)
    if value is None:
        value = _storage_source(stmt, slot, lc, declared, loc)
    return value


def _storage_source(stmt: TpyAssign, slot: _ExprUse,
                    lc: _LowerCtx, declared: dict[str, TpyType],
                    loc) -> THIRExpr:
    """Lower a non-literal source ONCE, under the use the slot settles for
    the member it lands in, and convert it (`convert`)."""
    analyzer = lc.analyzer
    st = slot.dest.type
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
            return _converted(
                _lower_expr(ctor_peel, lc, declared,
                            use=_storage_use(slot, copy_slot, analyzer)),
                slot, lc)
    peeled = copy_ptr_optional_peel(v, analyzer)
    src = peeled if peeled is not None else v
    b = binding_of(src, lc, declared)
    land = landing(b, analyzer.get_expr_type(src), st, analyzer)
    use = _storage_use(slot, land.member, analyzer)
    # A pointer-slot GLOBAL is read as the pointer it is: the lift takes
    # the `T*` (`h->v = ptr_to_optional(g);`), so the value-position deref
    # every other sink applies to it must not fire.
    if (b is not None and b.global_slot
            and not record_like(land.member, analyzer)):
        use = replace(use, result=_ExprResultUse.RECEIVER, forms=None,
                      pos=SinkPos.UNSPECIFIED)
    whole = land.member == st
    lowered = _lower_expr(
        src, lc, declared, use=use,
        # An owned name's write IS its routed read -- the conversion reads
        # the node's move facts and moves it at its last use or copies it.
        allow_unrouted_name=True,
        # A source landing in the slot's own Optional / union is consumed
        # WHOLE: no narrowing deref, no member-typed divergence.
        allow_whole_optional=whole and isinstance(st, OptionalType),
        allow_union_divergent=whole and isinstance(st, UnionType))
    _flush_witness("flush.field_write", lowered)
    return _converted(lowered, slot, lc, explicit_copy=peeled is not None)


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
    if field_slot_class(ftype, analyzer) is not FieldSlot.ERASED:
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
    return _converted(_lower_expr(stmt.value, lc, declared, use=slot),
                      slot, lc)


@dataclass(frozen=True)
class _ResidualPlan:
    """The slots the earlier families do not claim. `rows`: the shapes the
    value family's render rows do not claim but whose predicates admit -- a
    generic record's type-param `T` field (a plain assign whose
    BORROW->STORAGE convert renders the source bare/moved per instantiation)
    and the readonly-wrapped borrow-tuple-element edge -- which convert as
    a storage slot (ftype is never Optional or a union there, so it
    collapses to the bare-copy-vs-FormConvert verdict). Any other slot no
    family renders (a recursive-alias wrapper, a tuple of type params) takes
    ONE source: a name that moves here relocates in whole
    (`t(std::move(t))`); every other source rejects by its form."""
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
            stmt, replace(slot, dest=replace(
                slot.dest, holds=SlotHolds.OWNS,
                type=unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    plan.ftype))))),
            lc, declared, loc)
    lowered = _lower_expr(stmt.value, lc, declared, use=slot,
                          allow_unrouted_name=True)
    # No family admitted this source at this slot: the slot's class says
    # RELOCATE where no family renders the type at all; a classified slot
    # whose admission refused this source relocates the same way.
    value = _converted(lowered, replace(slot, dest=replace(
        slot.dest, holds=SlotHolds.RELOCATE)), lc)
    _witness("field_write.owned_move")
    return value


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
    return field_slot_use(decl_slot, construct, lc.analyzer, lc.placement)


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
    with lc.placement_scope(SlotPlacement.NO_FLUSH_POINT):
        slot = _field_slot(stmt, lc, declared, SlotConstruct.DIRECT_INIT)
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
