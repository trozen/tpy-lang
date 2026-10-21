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
from typing import Iterable
from enum import Enum, auto

from ...sema.dyn_erasure import dyn_forward_ok
from ...type_def_registry import has_view_param_form, owned_from_view_explicit
from ...typesys import (
    FloatLiteralType,
    holds_borrowing_view,
    IntLiteralType,
    is_dyn_protocol,
    is_open_type_param_return,
    lands_in_view_member,
    LiteralType,
    NoneType,
    OptionalType,
    OwnType,
    pointer_repr_optional,
    PtrType,
    RecursiveAliasInstanceType,
    Representation,
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
from ..reject import ThirUnsupported, note_detail
from ..source import BindingRepr, Held, Source
from .context import (Slot, SlotConstruct, SlotHolds, SlotLifetime,
                      lends_elements, storage_type)
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
# its statement: existing storage or a borrow of it (a call that returns
# a reference is one). A FRESH value or a plain value never is.
_DURABLE_HELD = frozenset({Held.STORAGE, Held.BORROWED})

_FORM_WORD = {Held.BORROWED: "borrow", Held.STORAGE: "storage",
              Held.FRESH: "storage", Held.VALUE: "value"}


def _refuse_lift(held: Held) -> Refuse:
    return Refuse("lift." + _FORM_WORD[held])


def _copy_declared(s: Source, under: Source, explicit_copy: bool) -> bool:
    """The copy-permission rule: a copy of an existing object into owned
    storage is built only where `copy()` spells it or sema declared it
    (`Source.copy_declared`) -- CPython would hand the object itself on."""
    return explicit_copy or s.copy_declared or under.copy_declared


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


def _same_type(x: 'TpyType | None', y: 'TpyType | None', types) -> bool:
    """The same type, a recursive alias's placeholder being its union."""
    if x == y:
        return True
    if x is None or y is None:
        return False
    resolve = types.registry.resolve_recursive_refs
    return resolve(x) == resolve(y)


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
    elif isinstance(slot_t, RecursiveAliasInstanceType):
        members = tuple(unwrap_readonly(m) for m in slot_t.alternatives())
    else:
        return slot_t
    for m in members:
        if _same_type(src_t, m, types):
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
    if rt is None or _same_type(rt, member_t, types):
        return True
    if isinstance(member_t, (OptionalType, UnionType,
                             RecursiveAliasInstanceType)):
        return absorbed_member(member_t, rt, types) != member_t
    if _same_viewfam(rt, member_t, types):
        return True
    if _record_upcast(rt, member_t, types):
        return True
    if is_dyn_protocol(rt) and dyn_forward_ok(rt, member_t, types):
        # An erased owner of a protocol inheriting the slot's converts
        # (`unique_ptr<Child>` -> `unique_ptr<Base>`).
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
    return pointer_repr_optional(t) is not None


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
    if isinstance(st, UnionType):
        return _to_pointer_variant(expr, s, st, rt, types)
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
    if s.temporary is not False or s.held not in _DURABLE_HELD:
        # The address of a value that dies with the statement -- or whose
        # lifetime nothing states -- would dangle.
        return Refuse("borrow_source")
    return Plan(THIROptionalPtrArg(result_type=st, form=Form.BORROW,
                                   value=expr, addr_of=True, loc=expr.loc),
                Effect.LIFT)


def _to_pointer_variant(expr: THIRExpr, s: Source, st: UnionType,
                        rt: 'TpyType | None', types) -> Plan | Refuse:
    """A pointer-variant union slot (`Union<A*, B*>`). The union's own
    value read in storage form (a `Union<A, B>` field) lifts to the
    pointer variant; already in pointer form it passes. A member's object
    is borrowed by address, as the pointer-repr Optional's pointee is."""
    if rt == st:
        if s.held is Held.STORAGE and not s.pointer_held:
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.BORROW, is_const=s.const,
                                        loc=expr.loc), Effect.LIFT)
        return Plan(expr, Effect.PASS)
    if absorbed_member(st, rt, types) == st:
        return Refuse("borrow_source")
    if s.pointer_held:
        return Plan(expr, Effect.PASS)
    if s.temporary is not False or s.held not in _DURABLE_HELD:
        return Refuse("borrow_source")
    return Plan(THIROptionalPtrArg(result_type=st, form=Form.BORROW,
                                   value=expr, addr_of=True, loc=expr.loc),
                Effect.LIFT)


def _elems_by_pointer(s: Source, src_t: TupleType) -> list[bool]:
    """Per element of a tuple source, whether it holds that element as a
    pointer into an object it does not own -- or may: a read in its borrow
    form holds an open `T` element as `val_or_ptr_t<T>`, a pointer at a
    reference instantiation."""
    if s.elems is not None:
        return [el.pointer_held for el in s.elems]
    borrow_form = s.held not in (Held.STORAGE, Held.FRESH)
    return [(s.held is Held.BORROWED
             and TupleType._element_is_pointer_repr(et))
            or (borrow_form and TupleType._element_is_val_or_ptr(et))
            for et in src_t.element_types]


def _owned_elem_by_pointer(s: Source, src_t: 'TpyType | None',
                           slot: Slot) -> bool:
    """Whether a tuple source holds an element the slot owns only as a
    pointer: a by-value tuple cannot take it as it renders."""
    if (slot.elems is None or not isinstance(src_t, TupleType)
            or len(src_t.element_types) != len(slot.elems)):
        return False
    return any(e.holds is SlotHolds.OWNS and p
               for e, p in zip(slot.elems, _elems_by_pointer(s, src_t)))


def _to_borrow_tuple(expr: THIRExpr, s: Source,
                     slot: Slot) -> Plan | Refuse:
    """A result tuple in borrow form (`std::tuple<.., T*>`), element by
    element: a slot element that hands back a borrow takes a pointer, one
    the result owns takes the value. A source already holding each element
    so passes; one holding an element the result borrows inline (a storage
    tuple lvalue) lifts to the pointers into it (`tuple_to_pointer`, const
    where the source is); one holding an element the result owns as a
    pointer copies its pointee in (`tuple_to_storage` of the result form,
    the copy sema declared). A tuple that dies with the statement has
    nothing to point into."""
    tt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot.declared)))
    src_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        expr.result_type)))
    if isinstance(tt, OptionalType):
        # A nullable result: the whole optional of that very layout passes;
        # a tuple lands in its payload, which the optional absorbs.
        if isinstance(src_t, OptionalType):
            # A binding or a call holding the result's own layout; a
            # member's storage optional holds its elements inline.
            return (Plan(expr, Effect.PASS)
                    if s.id_read or s.held is not Held.STORAGE
                    else Refuse("borrow_source"))
        tt = unwrap_readonly(tt.inner)
    if not isinstance(tt, TupleType) or not isinstance(src_t, TupleType):
        return Refuse("borrow_source")
    src_ptr = _elems_by_pointer(s, src_t)
    want_ptr = [e.holds in (SlotHolds.BORROWS, SlotHolds.POINTER)
                for e in slot.elems]
    lift = any(w and not p for w, p in zip(want_ptr, src_ptr))
    own = any(e.holds is SlotHolds.OWNS and p
              for e, p in zip(slot.elems, src_ptr))
    if lift and own:
        return Refuse("borrow_source")
    if own:
        return Plan(THIRFormConvert(result_type=tt, value=expr,
                                    form=Form.STORAGE, param_form=True,
                                    loc=expr.loc), Effect.COPY)
    if not lift:
        return Plan(expr, Effect.PASS)
    if s.temporary is not False or s.held not in _DURABLE_HELD:
        return Refuse("borrow_source")
    return Plan(THIRFormConvert(result_type=tt, value=expr,
                                form=Form.BORROW, is_const=s.const,
                                loc=expr.loc), Effect.LIFT)


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


def _view_copied_owner(t: TpyType, construct: SlotConstruct,
                       types) -> 'TpyType | None':
    """The owned type a VALUE slot holds when its construction takes no
    implicit conversion from its own view: a copy-initialized result takes
    none from any owned buffer passed as a view (the view constructor is
    explicit), an assignment none where `TypeDef.owned_from_view_explicit`."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    t = _resolve_pending_view(t, types) or t
    if construct is SlotConstruct.RETURN:
        return t if has_view_param_form(t) else None
    return t if owned_from_view_explicit(t) else None


def view_to_owned(expr: THIRExpr, slot: Slot, types) -> 'Plan | None':
    """A view source at a value slot holding an owned buffer whose
    construction takes no implicit conversion from its view
    (`_view_copied_owner`): the spelled copy (`std::string(v)`)."""
    if slot.holds is not SlotHolds.VALUE:
        return None
    owner_t = _view_copied_owner(slot.type, slot.construct, types)
    if owner_t is None or expr.source.held is not Held.BORROWED:
        return None
    return Plan(THIRFormConvert(result_type=owner_t, value=expr,
                                form=Form.STORAGE, move=False, loc=expr.loc),
                Effect.COPY)


def open_param_result(expr: THIRExpr, slot: Slot) -> 'Plan | None':
    """An open-`T` result (`val_or_ref_t<T>`) handed the bare read of an
    open-`T` PARAM (`param_val_or_ref_t<T>`): the two differ at a
    view-family instantiation, and `param_to_return<T>` owns at a
    value-typed `T` and passes a reference-typed one through. Only a bare
    open-`T` param (not `Own[T]`) is that read."""
    s = expr.source
    if slot.holds is not SlotHolds.TRAIT or not s.id_read:
        return None
    pt = s.param_type
    if pt is None or not is_open_type_param_return(
            unwrap_ref_type(unwrap_send_sync(pt))):
        return None
    return Plan(THIRFormConvert(result_type=slot.type, value=expr,
                                form=Form.STORAGE, generic_return=True,
                                loc=expr.loc),
                Effect.LIFT, row="open_tparam_param")


# --- the slot side of a read --------------------------------------------------

@dataclass(frozen=True)
class Landing:
    """The part of a storage slot a source lands in, decided from the
    source's binding and analyzed type before it is lowered (the read is
    lowered under it) and again after (`convert` reads the same rule)."""
    member: TpyType
    # The storage the source names (a binding, a field) is the slot's own
    # Optional / union: it is read whole and lifts whole, flow narrowing
    # notwithstanding.
    whole_binding: bool


def landing(binding: BindingRepr | None, named_t: 'TpyType | None',
            analyzed_t: 'TpyType | None', slot_t: TpyType,
            types) -> Landing:
    """`named_t`: the declared type of the storage the source names
    (`Source.named_type`)."""
    member = absorbed_member(slot_t, storage_type(analyzed_t), types)
    whole = (named_t is not None
             and storage_type(named_t) == slot_t
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

def borrowed_path_element_message(name: str, index: int, *,
                                  arity: 'int | None' = None,
                                  copies: 'Iterable[int]' = ()) -> str:
    """The sentence for an owning sink that would take an element of a
    pointer tuple some binding of which borrows it from other storage, a
    fact of the whole function (`arity`: the sink takes the whole tuple of
    that many elements, whose remedy is a tuple copying the `copies`
    positions and handing the rest on -- `copy()` takes no tuple): the move
    would take the caller's object, and its copy has no render here yet."""
    copies = set(copies)
    what = (f"copy({name}[{index}])" if arity is None
            else "(" + ", ".join(f"copy({name}[{i}])" if i in copies
                                 else f"{name}[{i}]"
                                 for i in range(arity)) + ")")
    return (f"element {index} of '{name}' is borrowed from other storage "
            f"by a binding of '{name}', so handing it to a sink that takes "
            f"ownership is not yet supported here; use {what} there instead")


def elementwise_tuple_storage(expr: THIRExpr, dest: TupleType, *,
                              param_form: bool = False
                              ) -> 'THIRFormConvert | None':
    """A tuple source at an owning tuple slot, converted element by element
    as the scalar at each position converts, from its per-element facts
    (`Source.elems`): an element the read owns (a pointer-tuple read sema
    declared no copy of, `own_element_copies`) moves out of its object at
    the read's last use, one some binding borrows is copied in (or kept as
    a pointer where a `param_form` destination borrows it), a value element
    moves with the tuple. None for a source with no element facts -- and
    for one owning no pointee it can move, where the whole-tuple copy
    (`tuple_to_storage`) is the render."""
    elems = expr.source.elems if expr.source is not None else None
    if elems is None or len(elems) != len(dest.element_types):
        return None
    keeps = [param_form and not isinstance(unwrap_readonly(dt), OwnType)
             and TupleType._element_is_pointer_repr(dt)
             for dt in dest.element_types]
    if any(el.pointer_held and el.undeclared and not keeps_pointer
           for el, keeps_pointer in zip(elems, keeps)):
        # An owning sink sema declared no read for: neither the move nor
        # the copy of the element is a verdict sema made.
        note_detail("ptr_tuple.undeclared_sink")
        raise ThirUnsupported("expr.name", loc=expr.loc)
    if not any(el.pointer_held and el.movable for el in elems):
        return None
    modes: list[str] = []
    for el, keeps_pointer in zip(elems, keeps):
        if el.pointer_held:
            modes.append("pointer" if keeps_pointer
                         else "move_pointee" if el.movable
                         else "copy_pointee")
        else:
            modes.append("move" if el.movable else "copy")
    return THIRFormConvert(result_type=dest, value=expr, form=Form.STORAGE,
                           move=True, param_form=param_form,
                           elem_modes=tuple(modes), loc=expr.loc)


def _storage_lift(value: THIRExpr, member: TpyType, *, move: bool = False,
                  materialize: 'bool | None' = None,
                  loc=None) -> THIRFormConvert:
    """The source lifted into the storage form of `member` (`tuple_to_storage`
    for a tuple, `ptr_to_optional` for an Optional, ...), moved or copied."""
    return THIRFormConvert(result_type=member, value=value, form=Form.STORAGE,
                           move=move, materialize=materialize, loc=loc)


def convert(expr: THIRExpr, slot: Slot, types, *,
            explicit_copy: bool = False) -> Plan | Refuse:
    """The render of `expr` at `slot`. `explicit_copy`: the source is
    copied, never moved -- the argument of a `copy()` the caller peeled, or
    a borrowed source whose copy sema warned about. A result spells that
    copy (`T(x)`); a field's assignment performs it."""
    s = expr.source
    under = expr.uncoerced.source
    # A recursive alias the slot names by its placeholder is its union.
    st = _resolve_plain_alias(slot.type, types)
    direct = slot.construct is SlotConstruct.DIRECT_INIT
    holds = slot.holds
    # A result's own `return` moves an id-expression naming an object of
    # the returning body implicitly; a capture is the enclosing body's.
    implicit = slot.implicit_move and s.id_read and not s.captured

    if (under.must_copy and under.copy_type is not None
            and slot.construct is SlotConstruct.RETURN
            and holds in (SlotHolds.OWNS, SlotHolds.VALUE)):
        # Sema declared a copy of this read (a name a closure still reads):
        # the result spells it, since its own construction would move.
        return Plan(THIRCopy(result_type=storage_type(expr.result_type),
                             value=expr, form=Form.STORAGE, loc=expr.loc),
                    Effect.COPY, copy_type=under.copy_type)

    value_t = expr.uncoerced.result_type
    if (slot.lifetime is SlotLifetime.OUTLIVES_FRAME
            and slot.representation is not Representation.VIEW
            and lands_in_view_member(st, value_t)
            and (_resolved_str_value(value_t, types) is not None
                 or _resolved_bytes_value(value_t, types) is not None)):
        # A str / bytes view the result wraps (a union's view member, an
        # Optional's view inner) has no lifetime check: sema's covers a
        # bare view only (BUGS.md#view-return-wrapped-slot-unchecked). A
        # union's view member takes nothing but a literal, built by the
        # literal arm; an Optional's takes a view, never an owned buffer.
        if isinstance(unwrap_readonly(st), UnionType):
            return Refuse("union_view_insert")
        if under.held in (Held.STORAGE, Held.FRESH):
            return Refuse("borrow_source")
    declared = _copy_declared(s, under, explicit_copy)
    if lends_elements(slot):
        return _to_borrow_tuple(expr, s, slot)
    if holds is SlotHolds.VALUE:
        if _owned_elem_by_pointer(s, storage_type(expr.result_type), slot):
            # The tuple does not take the source as it renders: each pointee
            # is copied in (the borrow -> storage tuple lift), where sema
            # declared that copy.
            if not declared:
                return Refuse("tuple_source")
            return Plan(_storage_lift(expr, st, loc=expr.loc), Effect.COPY)
        # A value slot's own construction / assignment takes the source as
        # it renders (an owned string assigns from a view), except an owned
        # buffer whose storage takes no view. A body write copies a
        # value-typed name even where it is movable; a member-init moves an
        # `Own` name.
        owned = view_to_owned(expr, slot, types)
        if owned is not None:
            return owned
        if (not explicit_copy and under.movable
                and (direct or (slot.construct is SlotConstruct.RETURN
                                and not implicit))):
            # The frame's own value at its last use moves. A return moves
            # an id-expression implicitly, so only a read it does not move
            # (a consuming method's `(*this)`, a staged result) is moved
            # out explicitly.
            return Plan(THIRMove(value=expr, result_type=st,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.ERASED:
        # The erasing coerce built the boxed value already; a movable name
        # under it hands the whole box over as an rvalue (the payload is
        # still copied into the box: BUGS.md#any-field-write-copies-source).
        # A result is copy-initialized from the box as the rvalue it is, or
        # from an `Any` name, which moves implicitly.
        if under.movable and slot.construct is not SlotConstruct.RETURN:
            return Plan(THIRMove(result_type=st, value=expr,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)

    rt_lowered = storage_type(expr.result_type)
    borrow_tuple = (s.held is Held.BORROWED
                    and isinstance(rt_lowered, TupleType))
    b = s.binding
    land = landing(b, s.named_type, s.analyzed_type, st, types)
    # A source read whole as the slot's own Optional / union is that type,
    # whatever its narrowed read would be.
    rt_whole = st if land.whole_binding else rt_lowered

    if holds is SlotHolds.TRAIT:
        lifted = open_param_result(expr, slot)
        if lifted is not None:
            return lifted
        if (not explicit_copy and under.movable and not implicit
                and slot.construct is SlotConstruct.RETURN):
            # The frame's own value at its last use, read through a deref:
            # a result moves it out (an id-expression moves implicitly).
            return Plan(THIRMove(value=expr, result_type=st,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.BORROWS:
        # A reference binds storage the caller can still reach: an object
        # of the slot's type, read as it is.
        if _ptr_opt(st) and (s.pointer_held or s.held is Held.FRESH
                             or not _binds(st, rt_whole, types)):
            # A getter's storage reference fed anything but the field it
            # names still takes the pointer conversions, which its signature
            # does not spell: the C++ build refuses them
            # (BUGS.md#property-getter-pointer-optional-return).
            return _to_pointer(expr, s, st, rt_whole, types)
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
        return _to_pointer(expr, s, st, rt_whole, types)

    if holds is SlotHolds.RELOCATE:
        # No conversion is known for this slot: only an owned value moves
        # in whole. A raw `T*` read moves the pointee the read did not
        # deref, and a borrow tuple's borrowed element must materialize.
        if not under.movable or s.pointer_held or borrow_tuple:
            return _refuse_lift(s.held)
        return Plan(THIRMove(value=expr, result_type=st, form=Form.STORAGE,
                             loc=expr.loc), Effect.MOVE)

    # OWNS: a storage slot.
    member = land.member
    if (member == st and not land.whole_binding
            and not (b is not None and b.pointer)):
        # A source whose analyzed type is still pending (a literal-seeded
        # local) lands in the member its LOWERED type names.
        member = absorbed_member(st, rt_lowered, types)
    # A value the conversion of the source built is an rvalue already.
    mv = not explicit_copy and under.movable and s.held is not Held.FRESH
    if (not explicit_copy and slot.construct is SlotConstruct.RETURN
            and under.id_read and not implicit
            and (under.owned_param or under.owned_result_storage)):
        # An `Own` parameter its own `return` would move implicitly: a
        # result staged first spells the move. A capture reads the
        # enclosing body's parameter: it moves only one whose binding is
        # the result's storage itself (`std::optional<P>&&`, its payload
        # maybe `@nocopy`); any other is copied
        # (BUGS.md#nested-def-captured-own-return-copies).
        mv = True
    # A raw pointer read at a pointer-repr Optional slot is that Optional's
    # borrow form, lifted whole.
    rt = (member if land.whole_binding or (
        s.pointer_held and member == st and _ptr_opt(st)) else rt_lowered)

    if slot.construct is SlotConstruct.RETURN:
        # A result is copy-initialized from the source: an rvalue, an
        # object a call hands back, or the binding's own name (an
        # id-expression, which moves implicitly) is taken as it renders.
        # Any other stored object is copied out explicitly -- or moved,
        # where it is the frame's own at its last use. A value type's
        # copy-initialization is its copy, whatever the source.
        if (record_like(member, types) and not s.pointer_held
                and _absorbs(member, rt, types)):
            if s.held is Held.FRESH or (not explicit_copy and (
                    s.held is Held.VALUE or implicit
                    or (s.id_read and not mv))):
                return Plan(expr, Effect.PASS)
            if mv:
                return Plan(THIRMove(result_type=expr.result_type, value=expr,
                                     form=expr.form, loc=expr.loc),
                            Effect.MOVE)
            if not explicit_copy and member.is_value_type():
                return Plan(expr, Effect.PASS)
            if s.copy_type is not None:
                if not declared:
                    # A copy of an existing object no `copy()` spells and
                    # no warning declared: CPython would hand the object
                    # itself back.
                    return Refuse("copy_undeclared")
                return Plan(THIRCopy(result_type=rt_lowered, value=expr,
                                     form=Form.STORAGE, loc=expr.loc),
                            Effect.COPY, copy_type=s.copy_type)

    param_t = s.param_type
    if param_t is not None and _opt_view_arg_shim(param_t, st, types):
        # A borrowed `optional<view>` PARAM into an owned `optional<str>`
        # slot: the view->owned shim.
        return Plan(THIROptViewArg(result_type=st, name=b.name,
                                   form=Form.VALUE, loc=expr.loc),
                    Effect.COPY, row="optview_shim")
    if (isinstance(unwrap_readonly(st), UnionType)
            and lands_in_view_member(st, expr.uncoerced.result_type)):
        # The view member of a union: a member-init or a result
        # initializes it from the source; a body write from a NAME is
        # refused (BUGS.md#record-view-field-escapes-local-buffer).
        initializes = direct or slot.construct is SlotConstruct.RETURN
        if initializes and _absorbs(member, rt, types):
            return Plan(expr, Effect.PASS)
        if not initializes and under.binding is not None:
            return _refuse_lift(s.held)
    stored = (s.held in (Held.STORAGE, Held.FRESH)
              or isinstance(rt, NoneType))
    if _same_type(rt, member, types) and stored:
        if mv and not implicit:
            return Plan(THIRMove(value=expr, result_type=member,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        if (s.captured and slot.construct is SlotConstruct.RETURN
                and not declared):
            # A result copy-initialized from the enclosing body's object
            # copies it: not this body's to move.
            return Refuse("copy_undeclared")
        return Plan(expr, Effect.PASS)
    ptr_union = _eligible_ptr_union(member, types) is not None
    ptr_lift = rt == member and (_ptr_opt(member) or ptr_union)
    if (mv and not ptr_lift and not borrow_tuple
            and not (s.held is Held.BORROWED
                     and _same_viewfam(rt, member, types))
            and not record_like(member, types)
            and not isinstance(member, TypeParamRef)
            and not s.pointer_held
            and _absorbs(member, rt, types)):
        # An owned value-typed name at its last use moves in whole (a view
        # of someone else's buffer owns nothing to move). A pointer-repr
        # tuple is not: its borrowed element must materialize through the
        # moving lift below.
        if implicit:
            return Plan(expr, Effect.PASS)
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
    if isinstance(member, TypeParamRef) and (
            slot.construct is SlotConstruct.RETURN or (direct and not mv)):
        # A member-init constructs an open `T` from the param form itself;
        # a result copy-initializes its storage form from the source as it
        # renders, a movable read that is no id-expression moved out.
        if mv and not implicit:
            return Plan(THIRMove(value=expr, result_type=member,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return Plan(expr, Effect.PASS)
    if not _lift_renders(member, rt, s.pointer_held, types) or (
            not mv and isinstance(member, TupleType)
            and any(types.ctx.is_type_nocopy(storage_type(e))
                    for e in member.element_types)):
        # No render -- or a whole-tuple copy of an element that cannot be
        # copied.
        return _refuse_lift(s.held)
    if mv and isinstance(member, TupleType):
        per_elem = elementwise_tuple_storage(expr, member)
        if per_elem is not None:
            return Plan(per_elem, Effect.MOVE)
    if (slot.construct is SlotConstruct.RETURN and not declared
            and _owned_elem_by_pointer(s, rt_lowered, slot)):
        # The lift below copies each pointee the source holds into the
        # result's element storage: the record row's copy, which sema
        # declares per result read (a store's copy is its store check's).
        return Refuse("tuple_source")
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
    return Plan(_storage_lift(value, member, move=moves,
                              materialize=False if obj else None,
                              loc=expr.loc),
                Effect.MOVE if moves or value is not expr else
                Effect.LIFT if ptr_lift else Effect.COPY)
