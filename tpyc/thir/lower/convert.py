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

from ...sema.alias_rebind import (bare_tuple, is_reference_leaf, leaves_at,
                                  peel_slot, reference_paths)
from ...sema.context import ElemPath, paths_overlap
from ...sema.dyn_erasure import dyn_forward_ok
from ...sema.own_copy import contains_reference_type
from ...type_def_registry import has_view_param_form, owned_from_view_explicit
from ...typesys import (
    FloatLiteralType,
    INT32,
    holds_borrowing_view,
    IntLiteralType,
    is_dyn_protocol,
    is_open_type_param_return,
    lands_in_view_member,
    LiteralType,
    NoneType,
    OptionalType,
    pointer_repr_optional,
    PtrType,
    RecursiveAliasInstanceType,
    Representation,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    unwrap_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    Form,
    THIRCopy,
    THIRExpr,
    THIRFormConvert,
    THIRLiteral,
    THIRMove,
    THIROptionalPtrArg,
    THIROptViewArg,
    THIRSubscript,
    THIRTempRead,
    BOUND_SOURCE_READ,
    THIRTupleLiteral,
)
from ..reject import ThirUnsupported, internal_error
from ..source import (BindingForm, BindingRepr, Held, MoveRow, Source,
                      TraitAbi, copy_permitted, path_permitted,
                      tuple_elems)
from .context import (Slot, SlotConstruct, SlotHolds, SlotLifetime,
                      SlotPosition, lends_elements, slot_for, storage_type)
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
    # The leaves the plan copies out of existing storage -- none a moved
    # leaf authorizes -- each as its path in the converted read and the
    # type it lands in (`_copy_permitted` checks them).
    copied: tuple[tuple[ElemPath, TpyType], ...] = ()


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
    without the sink's family prefix (`lift.borrow`); `message` the
    limitation in the user's terms, for a refusal BUGS.md tracks."""
    key: str
    message: 'str | None' = None


# What a reference slot may bind once the source is known not to die with
# its statement: existing storage or a borrow of it (a call that returns
# a reference is one). A FRESH value or a plain value never is.
_DURABLE_HELD = frozenset({Held.STORAGE, Held.BORROWED})

_FORM_WORD = {Held.BORROWED: "borrow", Held.STORAGE: "storage",
              Held.FRESH: "storage", Held.VALUE: "value"}


def _refuse_lift(held: Held) -> Refuse:
    return Refuse("lift." + _FORM_WORD[held])


def _lendable(s: Source) -> bool:
    """Whether a slot may point into the storage `s` names: storage known
    not to die with the statement. The address of a value that dies with
    it -- or whose lifetime nothing states -- would dangle."""
    return s.temporary is False and s.held in _DURABLE_HELD


def _copy_declared(s: Source, under: Source) -> bool:
    """Whether sema declared a copy of the whole read (`copy()` spells it
    or its record copies it whole)."""
    return () in copy_permitted(s) or () in copy_permitted(under)


def _copy_permitted(plan: Plan, slot: 'Slot', s: Source,
                    under: Source) -> 'ElemPath | None':
    """THE copy-permission rule: a copy of an existing object into owned
    storage is built only where `copy()` spells it or sema declared it
    (`copy_permitted`) -- CPython would hand the object itself on. The
    leaves it governs are the reference leaves (`is_reference_leaf`) the
    plan copies into a leaf the slot owns (`owned_reference_paths`, the
    record check's population): an open `T` among them too. The path of
    the first such leaf no permitted path covers; None where every copy
    is permitted."""
    permitted = (copy_permitted(s) | copy_permitted(under)
                 | _fresh(s) | _fresh(under))
    owned = owned_reference_paths(slot)
    for path, t in plan.copied:
        if (is_reference_leaf(t)
                and paths_overlap(path, owned)
                and not path_permitted(path, permitted)):
            return path
    return None


def _fresh(s: Source) -> frozenset[ElemPath]:
    """The leaves the read's record moves a new value into: copying one
    copies no existing object (`SourceRecord.fresh`)."""
    return s.record.fresh if s.record is not None else frozenset()


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
                rt: 'TpyType | None', types, *,
                const: bool = False) -> Plan | Refuse:
    """A pointer slot (`T*`): a pointer-repr Optional's, or a plain
    record's. A pointer-repr Optional read in storage form (the
    `std::optional<T>` a field holds) lifts to the borrow form; already in
    borrow form (a `T*` binding, a call handing one back) it passes. A
    `T` lvalue is borrowed by address, unless its read already is the raw
    pointer; an explicit `Ptr[T]` is the pointer itself."""
    if isinstance(st, UnionType):
        return _to_pointer_variant(expr, s, st, rt, types, const=const)
    if isinstance(st, OptionalType):
        pointee = unwrap_readonly(st.inner)
    else:
        # A plain record's pointer is its Optional's borrow form (`T*`).
        pointee = st
        st = OptionalType(pointee)
    if isinstance(rt, OptionalType) and _binds(pointee,
                                               unwrap_readonly(rt.inner),
                                               types):
        if s.held is Held.STORAGE and not s.pointer_held:
            if not _lendable(s):
                # The pointer into an optional that dies with the
                # statement would dangle.
                return Refuse("borrow_source")
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.BORROW, loc=expr.loc),
                        Effect.LIFT)
        return Plan(expr, Effect.PASS)
    if isinstance(rt, PtrType) and _binds(pointee,
                                          unwrap_readonly(rt.pointee), types):
        return Plan(expr, Effect.PASS)
    if s.pointer_held:
        return Plan(expr, Effect.PASS)
    if not _lendable(s):
        # The address of a value that dies with the statement -- or whose
        # lifetime nothing states -- would dangle.
        return Refuse("borrow_source")
    return Plan(THIROptionalPtrArg(result_type=st, form=Form.BORROW,
                                   value=expr, addr_of=True, loc=expr.loc),
                Effect.LIFT)


def _to_pointer_variant(expr: THIRExpr, s: Source, st: UnionType,
                        rt: 'TpyType | None', types, *,
                        const: bool = False) -> Plan | Refuse:
    """A pointer-variant union slot (`Union<A*, B*>`). The union's own
    value read in storage form (a `Union<A, B>` field) lifts to the
    pointer variant; already in pointer form it passes. A member's object
    is borrowed by address, as the pointer-repr Optional's pointee is."""
    if rt == st:
        if s.held is Held.STORAGE and not s.pointer_held:
            # A const slot holds the const pointer variant.
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.BORROW,
                                        is_const=s.const or const,
                                        loc=expr.loc), Effect.LIFT)
        return Plan(expr, Effect.PASS)
    if absorbed_member(st, rt, types) == st:
        return Refuse("borrow_source")
    if s.pointer_held:
        return Plan(expr, Effect.PASS)
    if not _lendable(s):
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


def _owned_by_pointer(s: Source, src_t: 'TpyType | None',
                      slot: Slot) -> tuple[tuple[ElemPath, TpyType], ...]:
    """The leaves of the elements a slot owns that a tuple source holds
    only as a pointer: moving the tuple copies their pointees."""
    if (slot.elems is None or not isinstance(src_t, TupleType)
            or len(src_t.element_types) != len(slot.elems)):
        return ()
    return tuple(
        leaf for i, (e, p) in enumerate(zip(slot.elems,
                                            _elems_by_pointer(s, src_t)))
        if e.holds is SlotHolds.OWNS and p
        for leaf in leaves_at(e.type, (i,)))


def _owned_elem_by_pointer(s: Source, src_t: 'TpyType | None',
                           slot: Slot) -> bool:
    """Whether a tuple source holds an element the slot owns only as a
    pointer: a by-value tuple cannot take it as it renders."""
    return bool(_owned_by_pointer(s, src_t, slot))


def _existing_object(s: Source) -> bool:
    """Whether a slot's own construction from `s` copies an existing
    object: a fresh value or one dying with its statement is none."""
    return s.temporary is not True and s.held in (Held.STORAGE, Held.BORROWED)


# --- tuples, element by element ----------------------------------------------

class TupleTake(Enum):
    """What a tuple conversion takes out of its source's elements."""
    # Each element is copied -- a temporary's inline element excepted:
    # nothing reads the temporary after its conversion, so it moves.
    COPY = auto()
    # The tuple moves: each inline element moves, a pointee is copied (the
    # tuple owns only what it holds inline).
    MOVE_WHOLE = auto()
    # Each element as its own read verdict says (`Source.movable`).
    PER_ELEMENT = auto()


def layout_tuple(expr: THIRExpr) -> 'TupleType | None':
    """The source tuple as declared, `Own` elements kept (the analyzed or
    named type), falling back to the lowered type."""
    lowered = bare_tuple(expr.result_type)
    s = expr.source
    if lowered is None:
        return None
    for t in ((s.analyzed_type, s.named_type) if s is not None else ()):
        bt = bare_tuple(t)
        if (bt is not None
                and len(bt.element_types) == len(lowered.element_types)):
            return bt
    return lowered


def _elem_kind(t: TpyType) -> str:
    """The family a tuple element's two forms differ in: a record /
    container (`T*` vs `T`), a pointer-repr Optional (`T*` vs
    `std::optional<T>`), a pointer-variant union, an open `T`, a nested
    tuple, or a value (one form)."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(
        unwrap_readonly(t)))))
    if isinstance(t, TypeParamRef):
        return "trait"
    if isinstance(t, TupleType):
        return "tuple"
    if isinstance(t, OptionalType):
        return "optional" if t.uses_pointer_repr() else "value"
    if isinstance(t, UnionType):
        return "union" if t.uses_pointer_repr() else "value"
    if not t.is_value_type():
        return "record"
    return "value"


def _src_borrows(el: Source) -> bool:
    """The element is held in its borrow form: a bare pointer, an open
    `T`'s `val_or_ptr_t`, a pointer variant / borrow-form nested tuple."""
    return el.pointer_held or el.trait is not None or el.held is Held.BORROWED


def _slot_borrows(es: Slot) -> bool:
    return es.holds in (SlotHolds.BORROWS, SlotHolds.POINTER, SlotHolds.TRAIT)


def _forms_differ(elems: 'tuple[Source, ...]', layout: TupleType,
                  slot: Slot, types) -> bool:
    """Whether any element of a source holding `elems` is held in another
    form than the slot holds it (nested tuples compared element-wise)."""
    for el, et, es in zip(elems, layout.element_types, slot.elems):
        kind = _elem_kind(et)
        if kind == "value":
            continue
        if kind == "tuple":
            sub = bare_tuple(et)
            if (sub is not None and es.elems is not None
                    and len(es.elems) == len(sub.element_types)
                    and _forms_differ(tuple_elems(el, sub), sub, es, types)):
                return True
            continue
        if _src_borrows(el) != _slot_borrows(es):
            return True
        if kind == "record" and _elem_upcasts(et, es.type, types):
            # A subclass element hands over its base part on its own
            # (`as_base`); the whole tuple's construction would build the
            # base from the subclass object.
            return True
    return False


def _elem_upcasts(et: TpyType, slot_t: TpyType, types) -> bool:
    src = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(et))))
    st = unwrap_readonly(unwrap_ref_type(unwrap_own(slot_t)))
    base = _upcast_to(src, st, types)
    return base is not None and not _same_type(src, base, types)


def tuple_forms_differ(expr: THIRExpr, slot: Slot, types) -> bool:
    """Whether a tuple source reaches `slot` only element by element."""
    layout = layout_tuple(expr)
    if (layout is None or slot.elems is None or expr.source is None
            or len(slot.elems) != len(layout.element_types)):
        return False
    return _forms_differ(tuple_elems(expr.source, layout), layout, slot,
                         types)


@dataclass
class ElemRead:
    """Reads of one element of a tuple source: `plain` as held, `object`
    through its pointer, `trait_ref` an open `T` element as a usable
    value or reference (`tuple_elem_ref`).

    The read's own facts follow from the element's and the receiver's: an
    element of a receiver that outlives the statement (a place, an unpack's
    holder, a loop's step) lives as long -- a pointer element's pointee is
    storage elsewhere, durable while the pointer is -- and a move of an
    element whose move is its copy (a scalar) is no move."""
    receiver: THIRExpr
    index: int
    type: TpyType
    source: Source
    loc: object
    # The receiver is an iterator's step, which holds a lent reference
    # element as a pointer (a generator's borrow form), a reference (a
    # runtime combinator's `range_elem`) or a `val_or_ref<T>` (a generic
    # producer's): its object reads through `unwrap_ref(tuple_elem_ref)`,
    # which takes each.
    step: bool = False

    def _read(self, *, deref: bool = False, elem_ref: bool = False
              ) -> THIRExpr:
        if deref and self.step and not isinstance(
                unwrap_readonly(self.type), OptionalType):
            deref, elem_ref = False, True
            via_ref = True
        else:
            via_ref = False
        node = THIRSubscript(
            result_type=self.type, receiver=self.receiver,
            index=THIRLiteral(result_type=INT32, value=self.index,
                              loc=self.loc),
            tuple_index=self.index, elem_ref=elem_ref, deref=deref,
            elem_pointer=self.source.pointer_held and not via_ref,
            step_elem=via_ref, loc=self.loc)
        # The read is a subscript of the receiver, no name's: an element a
        # literal was built from (a fresh object, a plain value) is the
        # receiver's storage now.
        rs = self.receiver.source
        # An element of a place is a place: reading it again runs nothing.
        src = replace(self.source, binding=None, id_read=False,
                      place=rs is not None and rs.place,
                      held=(Held.STORAGE
                            if self.source.held in (Held.FRESH, Held.VALUE)
                            else self.source.held))
        if deref or via_ref:
            src = replace(src, held=Held.BORROWED, pointer_held=False)
        if src.temporary is True and (
                src.prvalue or _elem_kind(self.type) != "record"):
            # The conversion's holder keeps a temporary's element to the
            # end of the full expression, an lvalue a slot may point into
            # until then -- except a record element of a temporary the
            # program did not build in place: nothing proves its borrow
            # done by then (BUGS.md#tuple-lend-from-temporary-refused).
            src = replace(src, temporary=False)
        src = replace(src, prvalue=False)
        if (src.temporary is None and rs is not None
                and rs.temporary is False and rs.held in _DURABLE_HELD):
            src = replace(src, temporary=False)
        if src.movable and unwrap_readonly(self.type).copy_is_cheap():
            src = replace(src, movable=False)
        object.__setattr__(node, "source", src)
        node._restamp_reads()
        return node

    def plain(self) -> THIRExpr:
        return self._read()

    def object(self) -> THIRExpr:
        """The element's object: a pointer element dereferenced -- not a
        pointer-repr Optional's, which is that Optional's borrow form."""
        return self._read(deref=self.source.pointer_held and not isinstance(
            unwrap_readonly(self.type), OptionalType))

    def trait_ref(self) -> THIRExpr:
        return self._read(elem_ref=True)

    def at(self, slot: Slot) -> THIRExpr:
        """The element as `slot` reads it: a pointer slot the element as
        held (a pointer passes, an inline element is addressed), an open
        `T` element through its trait -- as held where the slot spells it
        through that trait, or where the read moves it (its storage
        conversion copies a pointee) --, any other slot the element's object
        (a pointer element dereferenced)."""
        if slot.holds is SlotHolds.POINTER and not (
                self.step and self.source.pointer_held
                and not isinstance(unwrap_readonly(self.type),
                                   OptionalType)):
            return self.plain()
        if self.source.trait is not None:
            if slot.trait is self.source.trait or self.source.movable:
                return self.plain()
            return self.trait_ref()
        return self.object()


def _elem_takes(el: Source, take: TupleTake, dead: bool) -> bool:
    """Whether a conversion moves element `el` out of its source: an
    element held inline, or through an open `T`'s trait (whose storage
    conversion still copies a pointee) -- never a bare pointer's pointee
    unless its read verdict moves it."""
    if take is TupleTake.PER_ELEMENT:
        return el.movable
    inline = not el.pointer_held
    if take is TupleTake.MOVE_WHOLE:
        return inline
    return dead and inline


@dataclass(frozen=True)
class Built:
    """A tuple built element by element, and each leaf one of its owned
    elements copies out of existing storage (`Plan.copied`)."""
    expr: THIRExpr
    copied: tuple[tuple[ElemPath, TpyType], ...] = ()


def upcast_base(expr: THIRExpr, member: TpyType,
                types) -> 'TpyType | None':
    """The record of `member` a subclass object `expr` lands in, None for a
    source of the member's own class (or no record)."""
    s = expr.source
    if s is None or s.pointer_held or expr.result_type is None:
        return None
    return _upcast_to(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        expr.result_type))), member, types)


def _upcast_to(src_t: TpyType, member: TpyType, types) -> 'TpyType | None':
    """The record of `member` an object of record `src_t`, a subclass of
    it, lands in; None for no subclass upcast."""
    base = absorbed_member(member, src_t, types)
    return base if _record_slice_upcast_ok(src_t, base, types) else None


def as_base(expr: THIRExpr, member: TpyType, types, *,
            moved: bool = False) -> 'THIRExpr | None':
    """`expr` built into storage of the record `member`: an object of a
    subclass hands over its base part -- an existing object copied
    (`static_cast<const B&>(x)`), a moved one or a fresh value moved
    (`static_cast<B&&>(std::move(x))`, `static_cast<B&&>(Sub(..))`). Built
    from the subclass itself, the base would run a user `__init__` taking
    the subclass where CPython keeps the object's value. `moved`: the
    conversion moves `expr` out of its storage. None for a source of the
    member's own class (`upcast_base`)."""
    base = upcast_base(expr, member, types)
    if base is None:
        return None
    rvalue = moved or not _existing_object(expr.source)
    return THIRFormConvert(result_type=base,
                           value=expr.moved() if moved else expr,
                           form=Form.STORAGE, materialize=False,
                           base_copy=True, move=rvalue, loc=expr.loc)


def _build_tuple(receiver: THIRExpr, layout: TupleType,
                 elems: 'tuple[Source, ...]', slot: Slot, types, *,
                 take: TupleTake, dead: bool) -> 'Built | Refuse':
    st = storage_type(_slot_tuple(slot.declared or slot.type))
    out: list[THIRExpr] = []
    copied: list[tuple[ElemPath, TpyType]] = []
    for i, (el, et, es) in enumerate(zip(elems, layout.element_types,
                                         slot.elems)):
        # An element read has no source location of its own: it is the
        # conversion's, not a subscript the program wrote. What the
        # conversion takes out of the source is the element's move.
        r = ElemRead(receiver, i, storage_type(et),
                     replace(el, movable=_elem_takes(el, take, dead)), None)
        read = r.at(es)
        plan = _convert(read, es, types)
        if isinstance(plan, Refuse):
            if plan.key == "borrow_source" and read.source.temporary is True:
                # BUGS.md#tuple-lend-from-temporary-refused
                return Refuse("lend_from_temporary", message=(
                    "a tuple element borrowed from a temporary value is not "
                    "supported; bind the value to a variable first"))
            return plan
        out.append(plan.expr)
        copied.extend(((i,) + p, t) for p, t in plan.copied)
    spelled = (_slot_tuple(slot.declared)
               if slot.elements_by_result else None)
    return Built(THIRTupleLiteral(
        result_type=st, elements=tuple(out), spelled=spelled,
        spelled_const=slot.const and spelled is not None,
        loc=receiver.loc), tuple(copied))


def _slot_tuple(t: 'TpyType | None') -> 'TupleType | None':
    """The tuple a slot declares, an Optional's payload's."""
    bare = peel_slot(t) if t is not None else None
    return bare_tuple(bare.inner if isinstance(bare, OptionalType) else bare)


def per_element(expr: THIRExpr, slot: Slot, types, *,
                take: TupleTake) -> 'Built | Refuse':
    """THE tuple conversion: a source held in another form than `slot`
    holds its tuple, built as `THIRTupleLiteral` over each element read
    (`THIRSubscript`) converted as its scalar is at that element's slot.
    A source that is no place is evaluated once, bound by the literal's
    own lambda (`THIRTupleLiteral.bound`); a prvalue is a value nothing
    reads after the conversion, an lvalue is read through by reference.
    `take` says what moves out of it."""
    layout = layout_tuple(expr)
    s = expr.source
    if layout is None or s is None or slot.elems is None or len(
            slot.elems) != len(layout.element_types):
        return Refuse("tuple_source")
    bound = None
    dead = False
    receiver = expr
    if not s.place:
        bound = expr
        dead = s.prvalue
        read_source = (replace(s, temporary=True, movable=False,
                               binding=None, id_read=False)
                       if dead else s)
        receiver = THIRTempRead(result_type=expr.result_type,
                                read_id=BOUND_SOURCE_READ,
                                source=read_source, loc=expr.loc)
    elems = tuple_elems(s, layout, dead=dead)
    built = _build_tuple(receiver, layout, elems, slot, types, take=take,
                         dead=dead)
    if bound is None or isinstance(built, Refuse):
        return built
    return replace(built, expr=replace(built.expr, bound=bound))


def _to_borrow_tuple(expr: THIRExpr, s: Source, slot: Slot, types, *,
                     movable: bool, implicit: bool,
                     explicit_copy: bool) -> Plan | Refuse:
    """A result tuple in borrow form (`std::tuple<.., T*>`), element by
    element: a slot element that hands back a borrow takes a pointer, one
    the result owns takes the value. A source already holding each element
    so passes; any other is built element by element (`per_element`): an
    element the result borrows that the source holds inline (a storage
    tuple lvalue) is pointed into, one the result owns that the source
    holds as a pointer has its pointee copied in. The owned elements take
    the storage rows' rules: a movable source moves them (spelled where
    the slot does not move implicitly), any other copy of an object is one
    sema declared. A tuple that dies with the statement has nothing to
    point into."""
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
    owned = tuple(leaf for i, e in enumerate(slot.elems)
                  if e.holds is SlotHolds.OWNS and is_reference_leaf(e.type)
                  for leaf in leaves_at(e.type, (i,)))
    owns_object = bool(owned)
    mv = (owns_object and not explicit_copy and s.held is not Held.FRESH
          and tuple_moves(s, movable=movable))
    if lift and own:
        return Refuse("borrow_source")
    if own:
        if mv:
            per_elem = elementwise_plan(expr, slot, types)
            if per_elem is not None:
                return per_elem
        # Each owned element the source holds by pointer is copied in.
        built = per_element(expr, slot, types, take=TupleTake.COPY)
        if isinstance(built, Refuse):
            return built
        return Plan(built.expr, Effect.COPY, copied=built.copied)
    if not lift and tuple_forms_differ(expr, slot, types):
        # No element the result owns or borrows is a pointer of the other
        # kind, but a union, open `T` or nested element is held in another
        # form (a lend out of it checks its own lifetime).
        built = per_element(expr, slot, types, take=TupleTake.COPY)
        if isinstance(built, Refuse):
            return built
        return Plan(built.expr, Effect.LIFT, copied=built.copied)
    if not lift:
        if owns_object and not mv and implicit and not s.captured:
            # The result's own `return` would move the id-expression sema
            # declared a copy of; the borrow form has no spelled copy.
            return Refuse("tuple_source")
        if mv and not implicit:
            return Plan(THIRMove(result_type=expr.result_type, value=expr,
                                 form=expr.form, loc=expr.loc), Effect.MOVE)
        # The result's own construction copies each element it owns.
        return Plan(expr, Effect.PASS,
                    copied=(() if mv or not _existing_object(s)
                            else owned))
    if s.temporary is False and s.held not in _DURABLE_HELD:
        # Nothing outlives a source held in no storage; a temporary's lend
        # is refused per element (`_lendable` at each element's slot).
        return Refuse("borrow_source")
    # Each element the result borrows points into the source; each it owns
    # moves out where the read moves it, else is the copy its own read
    # record declares (sema warned it) or none.
    built = per_element(expr, slot, types,
                        take=TupleTake.PER_ELEMENT if mv else TupleTake.COPY)
    if isinstance(built, Refuse):
        return built
    return Plan(built.expr, Effect.COPY if built.copied else Effect.LIFT,
                copied=built.copied)


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
    if (slot.holds is not SlotHolds.TRAIT or not s.id_read
            or slot.trait is TraitAbi.YIELD_SLOT):
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


def moves_an_object(elems: 'tuple[Source, ...]') -> bool:
    """Whether a tuple source's element facts move an object out of it: a
    pointee, or an inline reference element (a value element moves with
    whatever does)."""
    return any(el.movable and (el.pointer_held or _holds_reference(el))
               for el in elems)


def _holds_reference(el: Source) -> bool:
    # A nested tuple is a value type that holds its reference leaves inline.
    t = el.analyzed_type or el.named_type
    return isinstance(t, TpyType) and contains_reference_type(unwrap_own(t))


def elementwise_plan(expr: THIRExpr, slot: Slot, types) -> Plan | None:
    """A tuple source some element of which its read moves, at an owning
    tuple slot, converted element by element as the scalar at each
    position converts, from its per-element facts (`Source.elems`): an
    element the read owns (one its record moves,
    `SourceRecord.element_moves`) moves out of its object at the read's
    last use, one some binding borrows is copied in (or kept as a pointer
    where the slot borrows it), a value element moves with the tuple. The
    plan states every leaf it copies in (`Plan.copied`). None for a source
    with no element facts -- and for one owning no pointee it can move,
    which converts whole."""
    elems = expr.source.elems if expr.source is not None else None
    if (elems is None or slot.elems is None
            or len(elems) != len(slot.elems)):
        return None
    if not moves_an_object(elems):
        return None
    built = per_element(expr, slot, types, take=TupleTake.PER_ELEMENT)
    if isinstance(built, Refuse):
        return None
    return Plan(built.expr, Effect.MOVE, copied=built.copied)


def tuple_moves(s: Source, *, movable: 'bool | None' = None) -> bool:
    """Whether a tuple read moves out of its source: a movable read
    (`movable`, when given, for the read under its coercions), and of a
    pointer tuple only one whose element verdicts move an object."""
    return ((s.movable if movable is None else movable)
            and (s.elems is None
                 or not any(el.pointer_held for el in s.elems)
                 or moves_an_object(s.elems)))


def param_tuple_slot(t: TpyType, types, *, const: bool = False) -> Slot:
    """A tuple parameter's slot, holding each element in its passing form
    (an `Own` element by value, a reference as its pointer)."""
    slot = slot_for(t, SlotPosition.PARAM, types, result_const=const)
    assert slot is not None
    return slot


def to_local(expr: THIRExpr, t: TpyType, form: BindingForm, types, *,
             const: bool = False) -> THIRExpr:
    """`expr` converted into a local of type `t` held in its binding's
    `form` (`const`: the binding is const), as its declaration or
    reseat writes it."""
    return converted(expr, slot_for(t, SlotPosition.LOCAL, types,
                                    construct=SlotConstruct.ASSIGN,
                                    form=form, result_const=const), types)


def store_slot(t: TpyType, types) -> Slot:
    """The slot of owned storage of type `t` an assignment fills (a
    container element, a copy's result)."""
    slot = slot_for(t, SlotPosition.STORE, types,
                    construct=SlotConstruct.ASSIGN)
    assert slot is not None
    return slot


def converted(expr: THIRExpr, slot: Slot, types, *,
              explicit_copy: bool = False) -> THIRExpr:
    """`expr` as `slot` takes it (`convert`); a pair with no conversion
    rejects under the tuple family (`tuple.<key>`)."""
    plan = convert(expr, slot, types, explicit_copy=explicit_copy)
    if isinstance(plan, Refuse):
        raise ThirUnsupported("tuple." + plan.key, loc=expr.loc,
                              message=plan.message)
    return plan.expr


def _storage_lift(value: THIRExpr, member: TpyType, *, move: bool = False,
                  materialize: 'bool | None' = None,
                  loc=None) -> THIRFormConvert:
    """The source lifted into the storage form of `member` (`ptr_to_optional`
    for an Optional, `to_value_variant` for a union, ...), moved or copied;
    never a tuple, which converts element by element."""
    return THIRFormConvert(result_type=member, value=value, form=Form.STORAGE,
                           move=move, materialize=materialize, loc=loc)


# The constructs whose conversion checks its copies (`_copy_permitted`).
# A member-init is not yet checked: one local-binding cell still copies
# undeclared there (TODO.md, "tuples convert per element through
# `convert`").
_CONSTRUCT_WORDS = {SlotConstruct.ASSIGN: "an assignment",
                    SlotConstruct.PASS: "an argument",
                    SlotConstruct.RETURN: "a return"}
_COPY_CHECKED = frozenset(_CONSTRUCT_WORDS)


def convert(expr: THIRExpr, slot: Slot, types, *,
            explicit_copy: bool = False) -> Plan | Refuse:
    """The render of `expr` at `slot`. `explicit_copy`: the source is
    copied, never moved -- the argument of a `copy()` the caller peeled, or
    a borrowed source whose copy sema warned about. A result spells that
    copy (`T(x)`); a field's assignment performs it. It grants no copy:
    that is the read's record (`copy_permitted`).

    A conversion that would copy an existing object no `copy()` spells and
    sema did not declare refuses (`copy_undeclared`): CPython would hand
    the object itself on. Decided once, on the leaves the plan says it
    copies (`_copy_permitted`)."""
    plan = _convert(expr, slot, types, explicit_copy=explicit_copy)
    if isinstance(plan, Refuse) or slot.construct not in _COPY_CHECKED:
        return plan
    s = expr.source
    under = expr.uncoerced.source
    _check_records(expr, slot, s, under)
    if _copy_permitted(plan, slot, s, under) is None:
        return plan
    return Refuse("copy_undeclared")


def owned_reference_paths(slot: Slot, prefix: ElemPath = ()
                          ) -> frozenset[ElemPath]:
    """The reference leaf paths (`is_reference_leaf`, sema's test) the
    slot owns: an OWNS leaf, a tuple's per element slot; none of a
    borrow-form tuple, whose elements are held by result."""
    if slot.elems is not None:
        out: set[ElemPath] = set()
        for i, e in enumerate(slot.elems):
            out |= owned_reference_paths(e, prefix + (i,))
        return frozenset(out)
    if slot.holds is not SlotHolds.OWNS:
        return frozenset()
    if bare_tuple(slot.type) is not None:
        return frozenset(prefix + p for p in reference_paths(slot.type))
    return frozenset({prefix}) if is_reference_leaf(slot.type) else frozenset()


def _check_records(expr: THIRExpr, slot: Slot, s: Source,
                   under: Source) -> None:
    """Every reference leaf the destination owns has its outcome in the
    read's record: sema decided it. A leaf without one is an internal
    error."""
    owned = owned_reference_paths(slot)
    if not owned:
        return
    rec = s.record if s.record is not None else under.record
    lacking = sorted(p for p in owned
                     if rec is None or not paths_overlap(p, rec.leaves))
    if lacking:
        value = f"the value '{s.name}'" if s.name else "the value"
        where = "" if lacking == [()] else "tuple element " + ", ".join(
            ".".join(str(i) for i in p) for p in lacking) + " of "
        loc = getattr(expr, "loc", None)
        line = f" at line {loc.line}" if loc is not None else ""
        raise internal_error(
            "copy.no_record",
            f"no ownership decision for {where}{value}{line} stored by "
            f"{_CONSTRUCT_WORDS[slot.construct]}", loc)



def _convert(expr: THIRExpr, slot: Slot, types, *,
             explicit_copy: bool = False) -> Plan | Refuse:
    s = expr.source
    under = expr.uncoerced.source
    # A recursive alias the slot names by its placeholder is its union.
    st = _resolve_plain_alias(slot.type, types)
    direct = slot.construct is SlotConstruct.DIRECT_INIT
    holds = slot.holds
    # A result's own `return` moves an id-expression naming an object of
    # the returning body implicitly; a capture is the enclosing body's.
    implicit = slot.implicit_move and s.id_read and not s.captured
    # A lambda parameter the record moves is the lambda's own result, which
    # its result slot moves implicitly; any other move of one is a compiler
    # bug (`check_record_move`).
    assert not (s.movable and s.move_exception is MoveRow.LAMBDA_PARAM
                and not implicit), (
        "record moves a lambda parameter outside its result", s.name)

    def passes(lands: TpyType) -> Plan:
        """The slot's own construction takes the source as it renders,
        building `lands` from it: a copy of an existing object unless the
        construction moves it (an id-expression naming the result body's
        own object, moved by its `return`)."""
        moves = implicit and under.movable
        copies = not moves and _existing_object(s)
        # The implicit move takes no subclass object: its base part is
        # moved out explicitly.
        return Plan(as_base(expr, lands, types, moved=moves) or expr,
                    Effect.PASS,
                    copied=tuple(leaves_at(lands, ())) if copies else ())

    # A tuple holding an element the slot owns by pointer does not take
    # the source as it renders: each pointee is copied in (the borrow ->
    # storage tuple lift).
    lifts_pointees = _owned_elem_by_pointer(
        s, storage_type(expr.result_type), slot)

    def lift_pointees() -> Plan | Refuse:
        built = per_element(expr, slot, types, take=TupleTake.COPY)
        if isinstance(built, Refuse):
            return built
        return Plan(built.expr, Effect.COPY, copied=built.copied)

    if (under.must_copy and under.copy_type is not None
            and slot.construct is SlotConstruct.RETURN
            and holds in (SlotHolds.OWNS, SlotHolds.VALUE)
            and not lends_elements(slot)):
        # Sema declared a copy of this read (a name a closure still reads):
        # the result spells it, since its own construction would move. A
        # borrow-form result's copy is its element rows'; a tuple's
        # pointees copy in through the lift, as the value slot's do.
        if lifts_pointees:
            return lift_pointees()
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
    declared = _copy_declared(s, under)
    if lends_elements(slot):
        return _to_borrow_tuple(expr, s, slot, types,
                                movable=under.movable and not under.must_copy,
                                implicit=implicit,
                                explicit_copy=explicit_copy)
    if holds is SlotHolds.VALUE:
        if lifts_pointees or tuple_forms_differ(expr, slot, types):
            # Where sema declared that copy; one its record moves some
            # element of converts element by element, each as its leaf
            # says. (A union, open `T` or nested element held in its borrow
            # form converts the same way, element by element.)
            if not explicit_copy and tuple_moves(s):
                per_elem = elementwise_plan(expr, slot, types)
                if per_elem is not None:
                    return per_elem
            if not declared and slot.construct is not SlotConstruct.RETURN:
                # A store's copy is its store check's; a result's is the
                # one check in `convert`.
                return Refuse("tuple_source")
            return lift_pointees()
        # A value slot's own construction / assignment takes the source as
        # it renders (an owned string assigns from a view), except an owned
        # buffer whose storage takes no view. A body write copies a
        # value-typed name even where it is movable; a member-init moves an
        # `Own` name.
        owned = view_to_owned(expr, slot, types)
        if owned is not None:
            return owned
        if (not explicit_copy and under.movable
                and not holds_borrowing_view(st)
                and (direct or (slot.construct is SlotConstruct.RETURN
                                and not implicit)
                     or (slot.construct is SlotConstruct.ASSIGN
                         and not under.id_read)
                     or (slot.construct is SlotConstruct.PASS
                         and slot.representation
                         is Representation.STORAGE))):
            # The frame's own value at its last use moves. A return moves
            # an id-expression implicitly, so only a read it does not move
            # (a consuming method's `(*this)`, a staged result) is moved
            # out explicitly. An assignment moves a value that is no name (an
            # element its read owns), an argument held as its
            # own storage (an owned tuple parameter's element) what it is
            # handed. A view slot owns nothing to move into. A conversion
            # builds its result already: what it converts is what moves.
            if expr.uncoerced is not expr:
                return Plan(expr.moved_under_coercions(), Effect.MOVE)
            return Plan(THIRMove(value=expr, result_type=st,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return passes(st)

    if holds is SlotHolds.ERASED:
        # The erasing coerce builds the box: a movable source under it moves
        # into the box (`make_any(std::move(n))`), a result's included -- the
        # return's implicit move takes the box, not its payload. An `Any`
        # name returned as it is moves implicitly.
        if under.movable and (slot.construct is not SlotConstruct.RETURN
                              or expr.uncoerced is not expr):
            return Plan(expr.moved_under_coercions(), Effect.MOVE)
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
        if slot.trait is TraitAbi.VAL_OR_PTR and s.trait is None:
            # A tuple element's `val_or_ptr_t<T>` over an open `T` the
            # source holds inline: a pointer into it at a reference-typed
            # instantiation, so it must outlive the slot.
            if not _lendable(s):
                return Refuse("borrow_source")
            return Plan(THIRFormConvert(result_type=st, value=expr,
                                        form=Form.BORROW,
                                        is_const=slot.const or s.const,
                                        trait=TraitAbi.VAL_OR_PTR,
                                        loc=expr.loc), Effect.LIFT)
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
            return _to_pointer(expr, s, st, rt_whole, types, const=slot.const)
        if not _lendable(s):
            # Only storage known to outlive the statement binds: a value
            # that dies with it -- or whose lifetime nothing states --
            # would leave the reference dangling. Sema refuses most such
            # sources, not all
            # (BUGS.md#borrow-return-of-owning-result-passes-sema,
            # BUGS.md#rvalue-container-subscript-is-borrow).
            return Refuse("borrow_source")
        return Plan(expr, Effect.PASS)

    if holds is SlotHolds.POINTER:
        return _to_pointer(expr, s, st, rt_whole, types, const=slot.const)

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
    mv = (not explicit_copy and s.held is not Held.FRESH
          and tuple_moves(s, movable=under.movable))
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
                return passes(member)
            if mv:
                return Plan(as_base(expr, member, types, moved=True)
                            or THIRMove(result_type=expr.result_type,
                                        value=expr, form=expr.form,
                                        loc=expr.loc),
                            Effect.MOVE)
            if not explicit_copy and member.is_value_type():
                return passes(member)
            if s.copy_type is not None:
                return Plan(THIRCopy(result_type=rt_lowered, value=expr,
                                     form=Form.STORAGE, loc=expr.loc),
                            Effect.COPY, copy_type=s.copy_type,
                            copied=tuple(leaves_at(member, ())))

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
            return passes(member)
        if not initializes and under.binding is not None:
            return _refuse_lift(s.held)
    tuple_member = (isinstance(member, TupleType) and slot.elems is not None
                    and len(slot.elems) == len(member.element_types))

    def stored_tuple() -> Plan | Refuse:
        """A tuple into the slot's own tuple storage: whole where the
        source holds every element as the slot does, else element by
        element. A consuming store moves the tuple VALUE (inline elements
        move, pointees copy), never a pointee, which is the caller's
        object."""
        if mv:
            per_elem = elementwise_plan(expr, slot, types)
            if per_elem is not None:
                return per_elem
        if not tuple_forms_differ(expr, slot, types):
            return Plan(expr.moved() if mv else expr,
                        Effect.MOVE if mv else Effect.COPY,
                        copied=(_owned_by_pointer(s, rt_lowered, slot)
                                if mv else tuple(leaves_at(member, ()))))
        built = per_element(
            expr, slot, types,
            take=TupleTake.MOVE_WHOLE if mv else TupleTake.COPY)
        if isinstance(built, Refuse):
            return built
        return Plan(built.expr, Effect.MOVE if mv else Effect.COPY,
                    copied=built.copied)

    if (tuple_member and tuple_forms_differ(expr, slot, types)
            and (mv or not any(types.ctx.is_type_nocopy(storage_type(e))
                               for e in member.element_types))):
        # A nested or union element held in its borrow form: no whole
        # assignment takes it, whatever the outer types say.
        return stored_tuple()
    stored = (s.held in (Held.STORAGE, Held.FRESH)
              or isinstance(rt, NoneType))
    if _same_type(rt, member, types) and stored:
        if mv and not implicit:
            return Plan(THIRMove(value=expr, result_type=member,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return passes(member)
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
            return passes(member)
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
        return passes(member)
    if isinstance(member, TypeParamRef) and s.trait is not None:
        # An open `T` held through its trait (a tuple element's
        # `val_or_ptr_t<T>`) is a pointer at a reference-typed
        # instantiation: its storage is constructed explicitly, which
        # copies the pointee (a moved read moves only a value).
        return Plan(_storage_lift(expr.moved() if mv else expr, member,
                                  loc=expr.loc),
                    Effect.MOVE if mv else Effect.COPY,
                    copied=() if mv else tuple(leaves_at(member, ())))
    if isinstance(member, TypeParamRef) and (
            slot.construct is SlotConstruct.RETURN or (direct and not mv)):
        # A member-init constructs an open `T` from the param form itself;
        # a result copy-initializes its storage form from the source as it
        # renders, a movable read that is no id-expression moved out.
        if mv and not implicit:
            return Plan(THIRMove(value=expr, result_type=member,
                                 form=Form.STORAGE, loc=expr.loc),
                        Effect.MOVE)
        return passes(member)
    if not mv and isinstance(member, TupleType) and any(
            types.ctx.is_type_nocopy(storage_type(e))
            for e in member.element_types):
        # A whole-tuple copy of an element that cannot be copied.
        return _refuse_lift(s.held)
    if tuple_member:
        # The tuple into the slot's own storage: whole where the source
        # holds each element as the slot does, else element by element.
        return stored_tuple()
    if not _lift_renders(member, rt, s.pointer_held, types):
        return _refuse_lift(s.held)
    if isinstance(member, TupleType):
        # A tuple member the slot spells no element slots for (a union's
        # alternative) converts at a storage slot of its own.
        inner = _convert(expr, slot_for(member, SlotPosition.STORE, types,
                                        construct=SlotConstruct.ASSIGN),
                         types, explicit_copy=explicit_copy or not mv)
        if isinstance(inner, Refuse):
            return inner
        return Plan(inner.expr, Effect.MOVE if mv else Effect.COPY,
                    copied=(_owned_by_pointer(s, rt_lowered, slot)
                            if mv else tuple(leaves_at(member, ()))))
    # A reference-axis source is the object itself: nothing to
    # materialize (`THIRFormConvert.materialize`).
    moves = mv
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
    based = (as_base(expr, member, types, moved=moves) if value is expr
             else None)
    if based is not None:
        return Plan(based, Effect.MOVE if moves else Effect.COPY,
                    copied=() if moves else tuple(leaves_at(member, ())))
    return Plan(_storage_lift(value, member, move=moves,
                              materialize=False if obj else None,
                              loc=expr.loc),
                Effect.MOVE if moves or value is not expr else Effect.COPY,
                copied=(_owned_by_pointer(s, rt_lowered, slot)
                        if value is not expr
                        else () if moves else tuple(leaves_at(member, ()))))
