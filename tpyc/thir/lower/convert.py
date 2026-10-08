"""The one conversion boundary: how a lowered source reaches a slot.

`convert(expr, slot, types)` answers, for a source already lowered (its
facts stamped on the node as `expr.source`) and the place it lands in (a
`Slot`), whether it passes as it renders, is lifted or copied into the
slot's storage form, moves, or has no conversion here. The answer is read
off three things only: the SOURCE facts, the SLOT description, and the TYPE
relation between them (same type, an Optional's inner / a union's member
the slot's own assignment absorbs, the view of an owned buffer, a record
upcast). Nothing here asks which sink is converting, what kind of
expression the source was, or what the lowering context holds: a sink
that needs another answer is missing a source or slot fact, and the fact
goes where it is decided, not into a branch here.

Copy warnings are sema's; the conversion emits none. A refusal is a
`Refuse` whose key the sink files under its own family
(`field_write.` + `lift.borrow`).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto

from ...type_def_registry import owned_from_view_explicit
from ...typesys import (
    FloatLiteralType,
    holds_borrowing_view,
    IntLiteralType,
    is_open_type_param_return,
    lands_in_view_member,
    LiteralType,
    NoneType,
    OptionalType,
    PtrType,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    Form,
    THIRCopy,
    THIRExpr,
    THIRFormConvert,
    THIRMove,
    THIROptionalPtrArg,
    THIROptViewArg,
)
from ..source import Binding, Held, Source
from .context import Slot, SlotConstruct, SlotHolds, storage_type
from .checks import _covariant_record_upcast_ok, _record_slice_upcast_ok
from .predicates import (
    tuple_stores_into,
    _eligible_ptr_union,
    _f1_tuple,
    _nested_storage_tuple,
    _opt_view_arg_shim,
    _resolve_pending_view,
    _resolve_plain_alias,
    _resolved_bytes_value,
    _resolved_str_value,
    record_like,
)


class Effect(Enum):
    PASS = auto()         # the slot's own construction / assignment takes it
    LIFT = auto()         # borrow form -> storage form of the same value
    COPY = auto()         # a new owned value built from the source
    MOVE = auto()         # the source's storage relocates into the slot
    MATERIALIZE = auto()  # the source lands in storage the caller provides


@dataclass(frozen=True)
class StorageRequest:
    """Backing storage the caller must realize before the slot can hold
    the converted value (the caller owns placement; convert only asks)."""
    type: TpyType


@dataclass(frozen=True)
class Plan:
    expr: THIRExpr
    effect: Effect
    storage_request: StorageRequest | None = None
    # The row's name where a sink tallies it as a face of its own
    # (`<sink>.<row>` in the face registry).
    row: str | None = None
    # A COPY that constructs this type (`Source.copy_type`): `expr` is the
    # `THIRCopy`, whose C++ spelling the sink supplies (`spelled`).
    copy_type: TpyType | None = None


def spelled(plan: Plan, render) -> THIRExpr:
    """The plan's expression with its copy's type spelled by the sink's
    type renderer: the conversion decides the copy, the sink knows how its
    module spells a type."""
    if plan.copy_type is None:
        return plan.expr
    return replace(plan.expr, cpp_type=render(plan.copy_type))


@dataclass(frozen=True)
class Refuse:
    """No conversion of this source into this slot. `key` is the reason
    without the sink's family prefix (`lift.borrow`)."""
    key: str


# What a reference slot may bind once the source is known not to die with
# its statement: existing storage, a borrow of it, or a value read whose
# referent outlives the statement -- in the corpus, only the result of a
# callee declared to return a reference (the stamp reads it as VALUE).
# A FRESH value is never one.
_DURABLE_HELD = frozenset({Held.STORAGE, Held.BORROWED, Held.VALUE})

_FORM_WORD = {Held.BORROWED: "borrow", Held.STORAGE: "storage",
              Held.FRESH: "storage", Held.VALUE: "value"}


def _refuse_lift(held: Held) -> Refuse:
    return Refuse("lift." + _FORM_WORD[held])


# --- type relations ----------------------------------------------------------

def _same_viewfam(a: TpyType, b: TpyType, types) -> bool:
    return ((_resolved_str_value(a, types) is not None
             and _resolved_str_value(b, types) is not None)
            or (_resolved_bytes_value(a, types) is not None
                and _resolved_bytes_value(b, types) is not None))


def _record_upcast(src_t: TpyType, t: TpyType, types) -> bool:
    """A record source a record slot takes by conversion: a covariant
    generic upcast (representation-preserving), or a subclass the assign
    slices (sema warns the narrowing)."""
    return (_covariant_record_upcast_ok(src_t, t, types)
            or _record_slice_upcast_ok(src_t, t, types))


def absorbed_member(slot_t: TpyType, src_t: 'TpyType | None',
                    types) -> TpyType:
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
        if _same_viewfam(src_t, m, types) or _record_upcast(src_t, m, types):
            return m
    return slot_t


def _absorbs(member_t: TpyType, rt: 'TpyType | None', types) -> bool:
    """Whether the slot's own assignment takes a value of type `rt` as it
    is: the same type, an Optional's inner or a union's member (the
    converting assignment), a str / bytes value into its owned twin, a
    record into its base (the assign slices -- sema warns), or a tuple
    whose still-pending literal elements take the slot's element types."""
    if rt is None or rt == member_t:
        return True
    if isinstance(member_t, (OptionalType, UnionType)):
        return absorbed_member(member_t, rt, types) != member_t
    if _same_viewfam(rt, member_t, types):
        return True
    if _record_upcast(rt, member_t, types):
        return True
    if (isinstance(rt, TupleType) and isinstance(member_t, TupleType)
            and len(rt.element_types) == len(member_t.element_types)):
        # A still-pending literal element takes its type from the slot's
        # element (pending-literal typing).
        return all(isinstance(e, (IntLiteralType, FloatLiteralType,
                                  LiteralType))
                   or _absorbs(unwrap_readonly(m), unwrap_readonly(e), types)
                   for e, m in zip(rt.element_types,
                                   member_t.element_types))
    return False


def _ptr_opt(t: TpyType) -> bool:
    return isinstance(t, OptionalType) and t.uses_pointer_repr()


def _binds(slot_t: TpyType, rt: 'TpyType | None', types) -> bool:
    """Whether a reference or pointer to `slot_t` binds an object of type
    `rt` as it is: the same type (through a plain alias), or a record
    upcast."""
    if rt is None:
        return False
    if (_resolve_plain_alias(rt, types)
            == _resolve_plain_alias(slot_t, types)):
        return True
    return _record_upcast(rt, slot_t, types)


def _to_pointer(expr: THIRExpr, s: Source, st: TpyType,
                rt: 'TpyType | None', types) -> Plan | Refuse:
    """A pointer-repr Optional slot (`T*`). Its own type read in storage
    form (the `std::optional<T>` a field holds) lifts to the borrow form;
    already in borrow form (a `T*` binding, a call handing one back) it
    passes. A `T` lvalue is borrowed by address, unless its read already
    is the raw pointer; an explicit `Ptr[T]` is the pointer itself."""
    # A pointer-variant union result keeps its own return arms: no sink
    # hands one to the conversion, so reaching here is a lowering bug.
    assert isinstance(st, OptionalType), f"pointer slot {st} has no row"
    pointee = unwrap_readonly(st.inner)
    if isinstance(rt, OptionalType) and _binds(pointee,
                                               unwrap_readonly(rt.inner),
                                               types):
        if s.held is Held.STORAGE and not s.pointer_held:
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.BORROW, loc=expr.loc),
                        Effect.LIFT)
        return Plan(expr, Effect.PASS)
    if isinstance(rt, PtrType) and _binds(pointee,
                                          unwrap_readonly(rt.pointee), types):
        return Plan(expr, Effect.PASS)
    if s.pointer_held:
        return Plan(expr, Effect.PASS)
    return Plan(THIROptionalPtrArg(result_type=st, form=Form.BORROW,
                                   value=expr, addr_of=True, loc=expr.loc),
                Effect.LIFT)


def _lift_renders(member_t: TpyType, rt: 'TpyType | None',
                  pointer_held: bool, types) -> bool:
    """Whether the borrow->storage conversion has a render for this
    (source, member) pair: the pointer-repr Optional and pointer-variant
    lifts over a source of that very type, the tuple lift likewise, the
    view->owned copy, the open-`T` construction, and a record / container
    member off a reference (never a raw pointer) it absorbs."""
    if isinstance(member_t, OptionalType):
        return rt == member_t and (member_t.uses_pointer_repr()
                                   or member_t.uses_generic_param_trait())
    if isinstance(member_t, UnionType):
        return rt == member_t and _eligible_ptr_union(member_t,
                                                      types) is not None
    if isinstance(member_t, TupleType):
        return (isinstance(rt, TupleType) and tuple_stores_into(rt, member_t)
                and (_f1_tuple(member_t, types) is not None
                     or _nested_storage_tuple(member_t, types) is not None))
    if (_resolved_str_value(member_t, types) is not None
            or _resolved_bytes_value(member_t, types) is not None):
        # The view->owned copy; a view member has no storage form to lift to.
        return (rt is not None and not holds_borrowing_view(member_t)
                and _same_viewfam(rt, member_t, types))
    if isinstance(member_t, TypeParamRef):
        return True
    return (record_like(member_t, types) and _absorbs(member_t, rt, types)
            and not pointer_held)


def _view_copied_owner(t: TpyType, types) -> 'TpyType | None':
    """The owned type a VALUE slot holds when its storage takes no
    assignment from its own view (`TypeDef.owned_from_view_explicit`)."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    t = _resolve_pending_view(t, types) or t
    return t if owned_from_view_explicit(t) else None


# --- the slot side of a read --------------------------------------------------

@dataclass(frozen=True)
class Landing:
    """The part of a storage slot a source lands in, decided from the
    source's binding and analyzed type before it is lowered (the read is
    lowered under it) and again after (`convert` reads the same rule)."""
    member: TpyType
    # The source NAME's binding is the slot's own Optional / union: it is
    # read whole and lifts whole, flow narrowing notwithstanding.
    whole_binding: bool


def landing(binding: Binding | None, analyzed_t: 'TpyType | None',
            slot_t: TpyType, types) -> Landing:
    member = absorbed_member(slot_t, storage_type(analyzed_t), types)
    whole = (binding is not None and binding.type is not None
             and storage_type(binding.type) == slot_t
             and isinstance(slot_t, (OptionalType, UnionType)))
    if whole:
        member = slot_t
    elif (binding is not None and binding.type is not None
          and binding.pointer and _ptr_opt(slot_t)):
        # A POINTER-bound source is already the borrow form of the
        # pointer-repr Optional slot: it lifts whole, never dereffed.
        member = slot_t
    return Landing(member, whole)


# --- the conversion -----------------------------------------------------------

def convert(expr: THIRExpr, slot: Slot, types, *,
            explicit_copy: bool = False) -> Plan | Refuse:
    """The render of `expr` at `slot`. `explicit_copy`: the source is
    copied, never moved -- the argument of a `copy()` the caller peeled, or
    a borrowed source whose copy sema warned about. A result spells that
    copy (`T(x)`); a field's assignment performs it."""
    s = expr.source
    under = expr.uncoerced.source
    st = slot.type
    direct = slot.construct is SlotConstruct.DIRECT_INIT
    holds = slot.holds

    if (under.must_copy and under.copy_type is not None
            and slot.construct is SlotConstruct.RETURN
            and holds in (SlotHolds.OWNS, SlotHolds.VALUE)):
        # Sema declared a copy of this read (a name a closure still reads):
        # the result spells it, since its own construction would move.
        return Plan(THIRCopy(result_type=storage_type(expr.result_type),
                             value=expr, form=Form.STORAGE, loc=expr.loc),
                    Effect.COPY, copy_type=under.copy_type)

    if holds is SlotHolds.VALUE:
        # A value slot's own construction / assignment takes the source as
        # it renders (an owned string assigns from a view), except an owned
        # buffer whose storage takes no view. A body write copies a
        # value-typed name even where it is movable; a member-init moves an
        # `Own` name.
        owner_t = _view_copied_owner(st, types)
        if owner_t is not None and s.held is Held.BORROWED:
            return Plan(THIRFormConvert(result_type=owner_t, value=expr,
                                        form=Form.STORAGE, move=False,
                                        loc=expr.loc), Effect.COPY)
        if (not explicit_copy and under.movable
                and (direct or (slot.construct is SlotConstruct.RETURN
                                and not s.id_read))):
            # The frame's own value at its last use moves. A return moves
            # an id-expression implicitly, so only a read of another shape
            # (a consuming method's `(*this)`) is moved out explicitly.
            return Plan(THIRMove(value=expr, result_type=st,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.ERASED:
        # The erasing coerce built the boxed value already; a movable name
        # under it hands the whole box over as an rvalue (the payload is
        # still copied into the box: BUGS.md#any-field-write-copies-source).
        if under.movable:
            return Plan(THIRMove(result_type=st, value=expr,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)

    rt_lowered = storage_type(expr.result_type)
    borrow_tuple = (s.held is Held.BORROWED
                    and isinstance(rt_lowered, TupleType))

    if holds is SlotHolds.TRAIT:
        # An open-`T` result (`val_or_ref_t<T>`) handed the bare read of an
        # open-`T` PARAM (`param_val_or_ref_t<T>`): the two differ at a
        # view-family instantiation, and `param_to_return<T>` owns at a
        # value-typed `T` and passes a reference-typed one through. Only a
        # bare open-`T` param (not `Own[T]`) is that read.
        pt = s.binding.param_type if s.binding is not None else None
        if (s.id_read and pt is not None and is_open_type_param_return(
                unwrap_ref_type(unwrap_send_sync(pt)))):
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.STORAGE,
                                        generic_return=True, loc=expr.loc),
                        Effect.LIFT, row="open_tparam_param")
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.BORROWS:
        # A reference binds storage the caller can still reach: an object
        # of the slot's type, read as it is.
        if _ptr_opt(st) and (s.pointer_held or s.held is Held.FRESH
                             or not _binds(st, rt_lowered, types)):
            # A getter's storage reference fed anything but the field it
            # names still takes the pointer conversions, which its signature
            # does not spell: the C++ build refuses them
            # (BUGS.md#property-getter-pointer-optional-return).
            return _to_pointer(expr, s, st, rt_lowered, types)
        if s.temporary is not False or s.held not in _DURABLE_HELD:
            # Only storage known to outlive the statement binds: a value
            # that dies with it -- or whose lifetime nothing states --
            # would leave the reference dangling. Sema refuses most such
            # sources, not all
            # (BUGS.md#borrow-return-of-owning-result-passes-sema,
            # BUGS.md#rvalue-container-subscript-is-borrow).
            return Refuse("borrow_source")
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.POINTER:
        return _to_pointer(expr, s, st, rt_lowered, types)

    if holds is SlotHolds.RELOCATE:
        # No conversion is known for this slot: only an owned value moves
        # in whole. A raw `T*` read moves the pointee the read did not
        # deref, and a borrow tuple's borrowed element must materialize.
        if not under.movable or s.pointer_held or borrow_tuple:
            return _refuse_lift(s.held)
        return Plan(THIRMove(value=expr, result_type=st, form=Form.STORAGE,
                             loc=expr.loc), Effect.MOVE)

    # OWNS: a storage slot.
    b = s.binding
    land = landing(b, s.analyzed_type, st, types)
    member = land.member
    if (member == st and not land.whole_binding
            and not (b is not None and b.pointer)):
        # A source whose analyzed type is still pending (a literal-seeded
        # local) lands in the member its LOWERED type names.
        member = absorbed_member(st, rt_lowered, types)
    mv = not explicit_copy and under.movable
    rt = member if land.whole_binding else rt_lowered

    if slot.construct is SlotConstruct.RETURN:
        # A result is copy-initialized from the source: an rvalue, an
        # object a call hands back, or the binding's own name (an
        # id-expression, which moves implicitly) is taken as it renders.
        # Any other stored object is copied out explicitly -- or moved,
        # where it is the frame's own at its last use.
        if (record_like(member, types) and not s.pointer_held
                and _absorbs(member, rt, types)):
            if s.held is Held.FRESH or (not explicit_copy and (
                    s.held is Held.VALUE or s.id_read)):
                return Plan(expr, Effect.PASS)
            if mv:
                return Plan(THIRMove(result_type=expr.result_type, value=expr,
                                     form=expr.form, loc=expr.loc),
                            Effect.MOVE)
            if s.copy_type is not None:
                return Plan(THIRCopy(result_type=rt_lowered, value=expr,
                                     form=Form.STORAGE, loc=expr.loc),
                            Effect.COPY, copy_type=s.copy_type)

    param_t = b.param_type if b is not None else None
    if param_t is not None and _opt_view_arg_shim(param_t, st, types):
        # A borrowed `optional<view>` PARAM into an owned `optional<str>`
        # slot: the view->owned shim.
        return Plan(THIROptViewArg(result_type=st, name=b.name,
                                   form=Form.VALUE, loc=expr.loc),
                    Effect.COPY, row="optview_shim")
    if (isinstance(unwrap_readonly(st), UnionType)
            and lands_in_view_member(st, expr.uncoerced.result_type)):
        # The view member of a union: a member-init direct-initializes it
        # from the source; a body write from a NAME is refused
        # (BUGS.md#record-view-field-escapes-local-buffer).
        if direct and _absorbs(member, rt, types):
            return Plan(expr, Effect.PASS)
        if not direct and under.binding is not None:
            return _refuse_lift(s.held)
    stored = (s.held in (Held.STORAGE, Held.FRESH)
              or isinstance(rt, NoneType))
    if rt == member and stored:
        if mv:
            return Plan(THIRMove(value=expr, result_type=member,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)
    ptr_union = _eligible_ptr_union(member, types) is not None
    ptr_lift = rt == member and (_ptr_opt(member) or ptr_union)
    if (mv and not ptr_lift and not borrow_tuple
            and not record_like(member, types)
            and not isinstance(member, TypeParamRef)
            and not s.pointer_held
            and _absorbs(member, rt, types)):
        # An owned value-typed name at its last use moves in whole. A
        # pointer-repr tuple is not: its borrowed element must materialize
        # through the moving lift below.
        return Plan(THIRMove(value=expr, result_type=member,
                             form=Form.STORAGE, loc=expr.loc), Effect.MOVE)
    if (not mv and not ptr_lift
            and not isinstance(member, TypeParamRef)
            and not s.pointer_held
            and _absorbs(member, rt, types)
            and (s.held is not Held.BORROWED
                 or (member == st and record_like(member, types)))):
        # A value / owned rvalue, or a REFERENCE borrow of a record or
        # container at its own slot: the slot's copy or converting
        # assignment takes it as it is. (Inside an Optional / union the
        # borrow still converts to the member's storage.)
        return Plan(expr, Effect.PASS)
    if direct and not mv and isinstance(member, TypeParamRef):
        # A member-init constructs an open `T` from the param form itself.
        return Plan(expr, Effect.PASS)
    if not _lift_renders(member, rt, s.pointer_held, types):
        return _refuse_lift(s.held)
    # The union storage conversion never moves (to_value_variant
    # deref-copies the active member), so the move flag stays a real-move
    # claim. A reference-axis source is the object itself: nothing to
    # materialize (`THIRFormConvert.materialize`).
    moves = mv and not ptr_union
    obj = record_like(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        expr.result_type))), types) if expr.result_type is not None else False
    value = expr
    if mv and isinstance(rt_lowered, TupleType):
        # A tuple owns only its INLINE elements; a pointer element borrows
        # an object the source does not own. A consuming store moves the
        # tuple VALUE (inline elements move, pointees copy), never the
        # pointee-moving lift, which would steal the caller's object.
        value = THIRMove(value=expr, result_type=expr.result_type,
                         form=expr.form, loc=expr.loc)
        moves = False
    return Plan(THIRFormConvert(result_type=member, value=value,
                                form=Form.STORAGE, move=moves,
                                materialize=False if obj else None,
                                loc=expr.loc),
                Effect.MOVE if moves or value is not expr else
                Effect.LIFT if ptr_lift else Effect.COPY)
