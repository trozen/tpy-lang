"""What replacing storage reaches: the one directional question every pass
that weighs a write against what a holder retains, storage that ends, or a
payload-selection fact asks, on referents (the storage a place resolves to
at a point); the one classifier of the storage events a statement makes;
and the two distinct-origin oracles the passes answer that question with."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from ..thir.scalar_leaves import MIRClass, inline_member_type, value_class
from ..typesys import OptionalType, TpyType, TupleType, UnionType, is_void_like_type, unwrap_readonly
from .nodes import (
    MIRAssign, MIRCopy, MIRMove, MIRPlace, MIRSlot, MIRSlotId, MIRSlotKind, MIRStatement,
    MIRStorageInit, MIRStorageWrite, MIRStorageWriteMode, MIRWrap, place_shape,
)
from .shapes import (
    REGION_STEPS, MIRContainerElements, MIRContainerStructure, MIRDeref, MIRUnionPayload, MIRWalk, Projection,
    lent_members, object_path, object_selector, replacement_reach, walk, walk_to,
)
from .value import (
    AGGREGATE_CLASSES, MIRField, MIRFieldId, MIRRecordLayout, MIRRecords, MIRShape, RecordFields, lent_record,
)


@dataclass(frozen=True)
class MIRReferent:
    place: MIRPlace
    # External origins may alias one another, including ancestor/child paths.
    external: bool = False
    # A literal's immortal storage, which nothing writes: decided where the
    # referent is created, never by elimination. Derived from the place, so
    # it does not take part in identity.
    static: bool = field(default=False, compare=False)
    # The loan an external record's view member stores (`place` names the
    # member): whatever owned-leaf storage outside the body it views, which
    # any external replacement may be.
    held: bool = False


@dataclass(frozen=True)
class MIROrigin:
    """What a dependency leaf names outside the body (`place`), and the
    value the projections of a referent rooted there step from (`shape`)."""
    place: MIRPlace
    shape: MIRShape


def parameter_objects(slot: MIRSlot) -> tuple[tuple[tuple[Projection, ...], MIRShape], ...]:
    """The caller's objects a parameter lends, an origin outside the body
    each: a borrowed record's, at the parameter itself, or one per pointer
    member of a wrapper, at its selector (`lent_members`); a local
    holder's record is found through its loans."""
    if slot.kind is not MIRSlotKind.PARAMETER:
        return ()
    if (record := lent_record(slot.shape)) is not None:
        return (((), record),)
    return tuple(((selector,), member) for selector, member in lent_members(slot.shape))


def referent_walk(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> MIRWalk:
    """A referent's place stepped from its root slot, through the
    dereferences its path implies (`object_path`)."""
    shape = slots[place.root].shape
    return walk(shape, object_path(shape, place.projections), records)


def origin(leaf: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> MIROrigin:
    """The one origin of a leaf: its own identity (each wrapper member its
    own), from the object its slot holds (a holder's pointee, a wrapper
    member's) -- except that a container view (a Span) names the elements
    region it views, so an element read or a write through it lands there."""
    slot = slots[leaf.root]
    shape = walk_to(slot.shape, object_path(slot.shape, leaf.projections), {})
    if not leaf.projections and slot.shape.container_view_holder:
        return MIROrigin(MIRPlace(leaf.root, (MIRContainerElements(),)), shape)
    return MIROrigin(leaf, shape)


def _origin(place: MIRPlace) -> MIRPlace:
    """The object an external referent starts from: its holder leaf, before
    any field or container region inside it."""
    for index, projection in enumerate(place.projections):
        if isinstance(projection, (MIRField, *REGION_STEPS)):
            return MIRPlace(place.root, place.projections[:index])
    return place


def fields_past_origin(place: MIRPlace) -> tuple[MIRField, ...] | None:
    """The fields a referent's path takes past its origin (`_origin`), None
    when any other step follows it."""
    rest = place.projections[len(_origin(place).projections):]
    return rest if all(isinstance(projection, MIRField) for projection in rest) else None


def parameter_object(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[Projection, ...] | None:
    """The prefix of a place rooted at a parameter that names the caller's
    object it lies in (`shapes.object_selector` of the parameter's value);
    nothing for a root the body does not describe."""
    slot = slots.get(place.root)
    return () if slot is None else object_selector(slot.shape, place.projections)


# Whether storage under one external origin may lie in storage under
# another (two distinct origins that may be one object): each pass's own
# oracle, `storage_exclusions` or `declared_containment`.
DistinctExternal = Callable[[MIRReferent, MIRReferent], bool]


def reaches(written: MIRReferent, target: MIRReferent, distinct_external: DistinctExternal) -> bool:
    """Whether replacing the storage `written` names reaches `target`: what
    a holder retains, storage that ends, a wrapper whose selection a fact
    records. A literal's static storage is never written; the body's own
    storage is never the caller's. A stored loan of an external object
    views owned-leaf storage outside the body that any external
    replacement may be, a sibling member of the same object included (its
    place names the member, not what it views). Under one root of the
    body's own, or one external origin, the write reaches what lies under
    the path it replaces (`replacement_reach`: a member field reaches the
    field, a container's shape write its inside but not the container
    object; a whole write everything under its place) -- never the storage
    around it, so a write strictly inside an Optional's payload leaves the
    wrapper's selection alone. Referent paths carry no dereference: one
    resolves into the next origin. Across two distinct external origins
    that may be one object, `distinct_external` decides."""
    if written.static or target.static:
        return False
    if written.external != target.external:
        return False
    if written.held or target.held:
        return True
    if written.external and _origin(written.place) != _origin(target.place):
        return distinct_external(written, target)
    if written.place.root != target.place.root:
        return False
    path, inclusive = replacement_reach(written.place.projections) or (written.place.projections, True)
    held = target.place.projections
    return held[:len(path)] == path and (inclusive or len(held) > len(path))


# --- the distinct-origin oracles ------------------------------------------------------

def _replaced_leaf(place: MIRPlace) -> bool:
    return (bool(place.projections) and isinstance(member := place.projections[-1], MIRField)
            and value_class(member.type) is MIRClass.OWNED_LEAF)


def _whole_container(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """A referent naming a container object itself (a borrowed container
    parameter's own origin), which no container holds as an element."""
    return not ref.place.projections and slots[ref.place.root].shape.whole_container


def _referent_shape(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot],
                    records: Mapping[TpyType, MIRRecordLayout]) -> MIRShape | None:
    """The object a referent names, when its path past its origin is
    fields only: stepped from its root (`referent_walk`)."""
    if fields_past_origin(ref.place) is None:
        return None
    return referent_walk(ref.place, slots, records).end


def storage_exclusions(slots: Mapping[MIRSlotId, MIRSlot],
                       records: Mapping[TpyType, MIRRecordLayout]) -> DistinctExternal:
    """Retention's and scope_lifetime's oracle: a replacement of external
    storage may free a loan held anywhere external, except that no record
    or container lives inside an opaque buffer (a leaf replacement spares
    an aggregate) and no container is an element of another (a shape or
    elements write spares a whole container)."""
    def may(written: MIRReferent, target: MIRReferent) -> bool:
        if not _replaced_leaf(written.place):
            region = bool(written.place.projections) and isinstance(written.place.projections[-1], REGION_STEPS)
            return not (region and _whole_container(target, slots))
        shape = _referent_shape(target, slots, records)
        return shape is None or shape.cls not in AGGREGATE_CLASSES
    return may


def _place_type(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
               layouts: Mapping[TpyType, MIRRecordLayout]) -> TpyType | None:
    """The type of the value at a place, None at a container's structure
    region, which holds no one type MIR models."""
    if place.projections[-1:] == (MIRContainerStructure(),):
        return None
    return place_shape(place, slots, layouts).type


# Values that store no record: a scalar leaf, an owned leaf, a view of either kind.
_HOLDS_NO_RECORD = frozenset({MIRClass.LEAF, MIRClass.OWNED_LEAF, MIRClass.VIEW, MIRClass.CONTAINER_VIEW})


def _may_hold(typ: TpyType | None, member: MIRFieldId, fields: RecordFields) -> bool:
    """Whether storage of `typ` may hold record member `member`. Only what
    is proven not to answers no: a scalar leaf, an owned leaf or a view
    holds no record; a tuple, an Optional or a union holds the member only
    if an element, the payload or an alternative may; a record holds it
    only if its declared fields (`fields`), or those of a record stored
    inline in it (`inline_member_type`), include it. Any other type --
    unknown, a container, a record whose fields MIR does not model -- may."""
    pending = [typ]
    seen: set[TpyType] = set()
    while pending:
        current = pending.pop()
        if current is None:
            return True
        current = unwrap_readonly(current)
        if value_class(current) in _HOLDS_NO_RECORD:
            continue
        if current in seen:
            continue
        seen.add(current)
        match current:
            case TupleType():
                pending.extend(current.element_types)
                continue
            case OptionalType():
                pending.append(current.inner)
                continue
            case UnionType():
                pending.extend(m for m in current.members if not is_void_like_type(m))
                continue
        declared = fields.get(current) if value_class(current) is MIRClass.RECORD else None
        if declared is None:
            return True
        for f in declared:
            if f.id == member:
                return True
            pending.append(inline_member_type(f.type))
    return False


def member_may_hold(written: MIRPlace, wrapper: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
                    layouts: MIRRecords) -> bool:
    """Whether storage at the body place `written` may be or hold the
    wrapper member `wrapper` ends at: the same member, or storage whose
    declared type may hold it (`_may_hold`)."""
    member = wrapper.projections[-1]
    if not isinstance(member, MIRField):
        return True
    last = written.projections[-1] if written.projections else None
    if isinstance(last, MIRField) and last.id == member.id:
        return True
    return _may_hold(_place_type(written, slots, layouts), member.id, layouts.fields)


def declared_containment(written: MIRPlace, wrapper: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
                         layouts: MIRRecords) -> DistinctExternal:
    """Presence's oracle for a write of the body place `written` against a
    fact on the wrapper place `wrapper`: a written referent naming a whole
    object (no projection inside its origin) may be any object -- a `Base`
    parameter may name a `Sub` whose own fields no declared type shows --
    else the written place's declared type decides (`member_may_hold`).
    Sound because record fields are inline at their declared type, so a
    field never holds a subclass object; the answer closes over declared
    fields only."""
    answer = member_may_hold(written, wrapper, slots, layouts)
    return lambda w, t: w.place == _origin(w.place) or answer


# --- the storage events of a statement ------------------------------------------------

def storage_destination(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether a write to `place` replaces storage a borrow can point into:
    an owned-storage root, the storage a borrowed holder points at (a trailing
    dereference), or a replacement that keeps the object around it
    (`replacement_reach`: an owned-leaf, container or record member field,
    a container's shape or elements region). The write's event follows its
    destination, whatever produces the value; a scalar field is
    overwritten, never a storage a borrow can point into."""
    if not place.projections:
        return slots[place.root].shape.owns_storage
    return isinstance(place.projections[-1], MIRDeref) or replacement_reach(place.projections) is not None


class MIRStorageEventKind(Enum):
    # Storage created by an assignment (its INITIALIZE write fact).
    INITIALIZE = auto()
    # A wrapper constructed with its default, no assignment
    # (`MIRStorageInit`): a sum storing a record inline begins empty.
    CONSTRUCT = auto()
    # The current value replaced (IN_PLACE; a whole write of an inline sum).
    REPLACE = auto()
    # A scalar wrapper's value replaced: its inline payload ends
    # (payload_lifetime weighs it), no storage a borrow points into.
    PAYLOAD = auto()
    # A place a call's summary may write that is a storage destination.
    CALL_WRITE = auto()
    # Owned storage a move empties.
    MOVE = auto()


# The kinds that begin or replace a root's own storage, the target's: what
# materializes the value a root holds (when `known`).
BEGINS = frozenset({MIRStorageEventKind.INITIALIZE, MIRStorageEventKind.CONSTRUCT,
                    MIRStorageEventKind.REPLACE, MIRStorageEventKind.PAYLOAD})


@dataclass(frozen=True)
class MIRStorageEvent:
    kind: MIRStorageEventKind
    place: MIRPlace
    # The holder a whole-record write rebinds to the written storage.
    rebind_owner: MIRSlotId | None = None
    # False for an assignment whose storage write fact is missing: its kind
    # is the one its destination gives (a replacement of storage a borrow
    # can point into, or a scalar wrapper's payload write), what it does
    # there is unknown.
    known: bool = True
    # A whole write of an inline sum: each alternative with a payload, and
    # whether that payload owns storage (`sum_payload_ends` weighs them
    # against the old selection); a construction's: the payload place it
    # materializes, if any. Empty for any other event.
    payloads: tuple[tuple[int, bool], ...] = ()
    materialized: MIRPlace | None = None

    @property
    def unknown_replacement(self) -> bool:
        """A replacement of storage a borrow can point into whose fact is missing."""
        return not self.known and self.kind is MIRStorageEventKind.REPLACE


def storage_events(stmt: MIRStatement, call_writes: tuple[MIRPlace, ...],
                   slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[MIRStorageEvent, ...]:
    """Every storage event of a statement, with its kind: a wrapper's
    physical construction (`MIRStorageInit`, with the payload it
    materializes); an assignment by its storage write fact to a storage
    destination, to an owned tuple's root (no `is_storage` destination: its
    members moved in from temporaries of their own) or to a place in a
    scalar wrapper (its payload storage) -- without its fact, the kind the
    destination gives (a replacement, a payload write), not `known`; a whole
    write of an inline sum carries its per-alternative payload list; each
    place of `call_writes` (the call as its caller's presence sees it) that
    is a storage destination; and owned storage a move empties. The guards
    a reader that skips initializations relies on are named at
    `replacement_events`."""
    match stmt:
        case MIRStorageInit(target=target, alternative=alternative):
            # A scalar wrapper is its own storage; an owned sum materializes
            # only a selected alternative that owns storage.
            shape = slots[target.root].shape
            member = shape.members[alternative] if shape.owned_sum and not target.projections else None
            held = (target if not shape.owned_sum
                    else MIRPlace(target.root, (MIRUnionPayload(alternative),))
                    if member is not None and member.owns_storage else None)
            return (MIRStorageEvent(MIRStorageEventKind.CONSTRUCT, target, materialized=held),)
    events: list[MIRStorageEvent] = []
    if isinstance(stmt, MIRAssign):
        target = stmt.target
        root = slots[target.root].shape
        owned_tuple = not target.projections and root.owned_tuple
        wrapper = root.scalar_wrapper
        payloads = (tuple((k, m.owns_storage) for k, m in enumerate(root.members) if m is not None)
                    if not target.projections and root.inline_sum else ())
        if owned_tuple or wrapper or storage_destination(target, slots):
            fact = stmt.storage_write
            known = isinstance(fact, MIRStorageWrite)
            if known and fact.mode is MIRStorageWriteMode.INITIALIZE:
                kind = MIRStorageEventKind.INITIALIZE
            elif wrapper:
                kind = MIRStorageEventKind.PAYLOAD
            else:
                kind = MIRStorageEventKind.REPLACE
            events.append(MIRStorageEvent(kind, target, fact.rebind_owner if known else None, known,
                                          payloads if kind is not MIRStorageEventKind.INITIALIZE else ()))
    events.extend(MIRStorageEvent(MIRStorageEventKind.CALL_WRITE, place)
                  for place in call_writes if storage_destination(place, slots))
    if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and slots[stmt.value.source].shape.owns_storage:
        events.append(MIRStorageEvent(MIRStorageEventKind.MOVE, MIRPlace(stmt.value.source)))
    return tuple(events)


def sum_payload_ends(event: MIRStorageEvent, value: object,
                     selected: Callable[[MIRPlace], frozenset[int] | None]) -> tuple[tuple[MIRPlace, bool], ...]:
    """The old payloads a whole write of an inline sum ends (`event`, of
    `storage_events`), each with whether it owns storage, by what its member
    holds: only an alternative the old selection (`selected`, None for
    unknown: any) may hold ends; a storage payload ends whatever is written
    (a record has identity, so a refill of the same alternative replaces
    it), a scalar payload only when a sum construction or copy may select
    another alternative (a same-tag write ends nothing)."""
    old = selected(event.place)
    match value:
        case MIRWrap(alternative=alternative):
            new: frozenset[int] | None = frozenset({alternative})
        case MIRCopy(source=source) if source != event.place:
            new = selected(source)
        case _:
            new = frozenset()
    ends: list[tuple[MIRPlace, bool]] = []
    for alternative, owns in event.payloads:
        if old is not None and alternative not in old:
            continue
        if not owns and new is not None and not new - {alternative}:
            continue
        ends.append((MIRPlace(event.place.root, (*event.place.projections, MIRUnionPayload(alternative))), owns))
    return tuple(ends)


# One replacement: the place whose storage the statement replaces, and the
# holder a whole-record write rebinds to it (None for any other).
Replacement = tuple[MIRPlace, MIRSlotId | None]
_REPLACING = frozenset({MIRStorageEventKind.REPLACE, MIRStorageEventKind.CALL_WRITE, MIRStorageEventKind.MOVE})


def replacement_events(stmt: MIRStatement, call_writes: tuple[MIRPlace, ...],
                       selected: Callable[[MIRPlace], frozenset[int] | None],
                       slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[Replacement, ...] | None:
    """The storage a statement replaces, of its `storage_events`: a
    replacement, a call write, a move -- never an initialization, nor a
    scalar payload (payload_lifetime's). A whole write of an owned sum
    replaces the storage payloads `sum_payload_ends` weighs against the
    incoming selection (`selected`) -- a first fill of an empty slot
    replaces nothing. None when a write fact is missing where storage a
    borrow can point into may be replaced
    (`MIRStorageEvent.unknown_replacement`).

    Skipping initializations relies on an initialization never writing
    storage a live holder points into: the validator rejects a repeated
    initialization of storage (`initialized_storage`, which starts with
    the owned parameters a caller materialized, and
    `payload_initializations`, `MIRRepeatedInitializationError`), requires
    an owning region and placement for each, and refuses an owning
    operation in a cycle; a region-scoped storage initialized again each
    loop iteration is fresh after the region's reset, and scope_lifetime
    reports a holder outliving that region."""
    events = storage_events(stmt, call_writes, slots)
    if any(event.unknown_replacement for event in events):
        return None
    found: list[Replacement] = []
    for event in events:
        if event.kind not in _REPLACING:
            continue
        if not event.payloads:
            found.append((event.place, event.rebind_owner))
            continue
        assert isinstance(stmt, MIRAssign)
        found.extend((place, event.rebind_owner) for place, owns in sum_payload_ends(event, stmt.value, selected)
                     if owns)
    return tuple(found)
