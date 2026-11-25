"""What replacing storage reaches: the one directional question every pass
that weighs a write against what a holder retains, storage that ends, or a
payload-selection fact asks, on referents (the storage a place resolves to
at a point); the one classifier of the storage events a statement makes;
and the two distinct-origin oracles the passes answer that question with."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import NamedTuple, TypeVar

from ..thir.scalar_leaves import UNEXPANDED, MIRClass, stored_types, stores_record, value_class, walk_stored
from ..typesys import TpyType
from .nodes import (
    MIRAssign, MIRCall, MIRCopy, MIRMove, MIRPlace, MIRSlot, MIRSlotId, MIRSlotKind, MIRStatement,
    MIRStorageInit, MIRStorageWrite, MIRStorageWriteMode, MIRWrap, builds_base, place_shape,
)
from .shapes import (
    REGION_STEPS, MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload, MIRWalk,
    Projection,
    lent_members, object_path, object_selector, replacement_reach, walk, walk_to,
)
from .value import (
    AGGREGATE_CLASSES, SUM_CLASSES, WRAPPER_CLASSES, MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRecords, MIRShape,
    MIREffect, MIRStoredOutcome, MIRValidationError, RecordFields, lent_record, declared_record_fields, payload_fate,
)
from .validate import reaches_element


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
    return written.place.root == target.place.root and replaced_below(written.place, target.place) is not None


def replaced_below(written: MIRPlace, held: MIRPlace) -> tuple[Projection, ...] | None:
    """The steps `held` takes below the path a write at `written` replaces
    (`replacement_reach`; a whole write replaces everything under its
    place), None when the write does not reach it; the caller compares roots."""
    path, inclusive = replacement_reach(written.projections) or (written.projections, True)
    steps = held.projections
    if steps[:len(path)] != path or not inclusive and len(steps) == len(path):
        return None
    return steps[len(path):]


def no_distinct_alias(written: MIRReferent, target: MIRReferent) -> bool:
    """The definite oracle: two distinct external origins are never proven
    one object."""
    return False


def reaches_definitely(written: MIRReferent, target: MIRReferent) -> bool:
    """`reaches` with nothing left to the caller: neither side a stored loan
    of the caller's (`held`, storage of no known place) and no step between
    two distinct external origins (`no_distinct_alias`). Two element regions
    reach as one: element identity is not tracked, so the witness weighs
    such a pair as a MAY."""
    if written.held or target.held:
        return False
    return reaches(written, target, no_distinct_alias)


# --- the distinct-origin oracles ------------------------------------------------------

def _replaced_leaf(place: MIRPlace) -> bool:
    """Whether `place` is a field whose storage stores no record or
    container inline: an owned leaf's buffer, or a wrapper storing no
    record (`stores_record`: its every member a scalar or an owned leaf)."""
    if not (place.projections and isinstance(member := place.projections[-1], MIRField)):
        return False
    cls = value_class(member.type)
    return cls is MIRClass.OWNED_LEAF or cls in WRAPPER_CLASSES and not stores_record(member.type)


def _whole_container(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> TpyType | None:
    """The type of the container object a referent names itself (a
    borrowed container parameter's own origin), else None."""
    shape = slots[ref.place.root].shape
    return shape.type if not ref.place.projections and shape.whole_container else None


def _container_holds(container: TpyType, target: TpyType, records: MIRRecords) -> bool:
    """Whether storage of container type `container` may hold a `target`
    container among its members at any depth: a member of that type, a
    container member that may hold one, or a record member with a
    container field that may -- a record whose declared fields are unknown
    here may, as may a modeled value whose members nothing enumerates (a
    recursive alias union); a member of no class MIR models (an
    `Own[...]`) is only itself, never entered. A type the walk already
    entered answers there (a record reaching itself through its fields)."""
    # A walk of types, not of the holder's member shapes: a record member's
    # shape carries no fields, so below it only declared field types answer.
    fields = declared_record_fields(records.fields)
    members = stored_types(container, fields)
    return members is not UNEXPANDED and any(
        step.type == target or step.unexpanded
        for step in walk_stored(members, fields, lambda _, cls: cls is not None))


def _referent_shape(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot],
                    records: Mapping[TpyType, MIRRecordLayout]) -> MIRShape | None:
    """The object a referent names, when its path past its origin is
    fields only: stepped from its root (`referent_walk`)."""
    if fields_past_origin(ref.place) is None:
        return None
    return referent_walk(ref.place, slots, records).end


def storage_exclusions(slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> DistinctExternal:
    """Retention's and scope_lifetime's oracle: a replacement of external
    storage may free a loan held anywhere external, except that no record
    or container lives inside an opaque buffer (a leaf replacement spares
    an aggregate) and a shape or elements write spares a whole container
    the written container's members cannot hold (`_container_holds`)."""
    def may(written: MIRReferent, target: MIRReferent) -> bool:
        if not _replaced_leaf(written.place):
            projections = written.place.projections
            if not (projections and isinstance(projections[-1], REGION_STEPS)):
                return True
            whole = _whole_container(target, slots)
            if whole is None:
                return True
            holder = referent_walk(MIRPlace(written.place.root, projections[:-1]), slots, records).end
            return holder is None or _container_holds(holder.type, whole, records)
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
    holds no record; a wrapper or a record holds it only if a member it
    stores inline (`stored_members`; a record's declared fields, `fields`)
    is the member or may hold it. Any other type -- unknown, a container,
    a record whose fields MIR does not model -- may."""
    if typ is None:
        return True

    def entered(_: TpyType, cls: MIRClass | None) -> bool:
        return cls not in _HOLDS_NO_RECORD and cls is not MIRClass.CONTAINER
    return any(step.cls is MIRClass.CONTAINER or step.unexpanded
               or isinstance(step.selector, MIRField) and step.selector.id == member
               for step in walk_stored([typ], declared_record_fields(fields), entered, last_first=True))


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
    # The holder a whole-record write rebinds to the written storage; the
    # slot a call's result is bound to at its call writes, which precede the
    # borrow the call returns.
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
                   slots: Mapping[MIRSlotId, MIRSlot],
                   initialized: tuple[MIRPlace, ...] = ()) -> tuple[MIRStorageEvent, ...]:
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
    if builds_base(stmt):
        # A base's constructor initializes the base's fields of the record
        # the target reaches (`initialized_places`, the call's): each
        # storage-owning one is an initialization, the record around them
        # keeps its identity.
        events.extend(MIRStorageEvent(MIRStorageEventKind.INITIALIZE, place) for place in initialized
                      if storage_destination(place, slots))
    elif isinstance(stmt, MIRAssign):
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
    # The result is bound on the normal return, after the callee's writes:
    # a whole slot it fills holds nothing they replace. This relies on a
    # covered body having no exceptional edge inside it (a raise leaves the
    # body without binding the result); TODO.md "The call-write rebind keys
    # to the normal-return edge".
    result = (stmt.target.root if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)
              and not stmt.target.projections else None)
    events.extend(MIRStorageEvent(MIRStorageEventKind.CALL_WRITE, place, result)
                  for place in call_writes if storage_destination(place, slots))
    if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and slots[stmt.value.source].shape.owns_storage:
        events.append(MIRStorageEvent(MIRStorageEventKind.MOVE, MIRPlace(stmt.value.source)))
    return tuple(events)


def stored_selection(value: object, suffix: tuple[Projection, ...],
                     selected: Callable[[MIRPlace], frozenset[int] | None]) -> MIRStoredOutcome | None:
    """What a direct write stores in the sum `suffix` names below the place
    it writes -- `value` the assignment's value or a `MIRStorageInit` --
    None when unknown: a construction selects its alternative (`wrap[k]`
    -> {k}, a sum beneath it what its payload source selects there; a sum
    under a payload it does not select is vacated); a copy what the source
    selects at the same suffix (`selected`, the incoming facts); any other
    value is unknown."""
    match value:
        case MIRStorageInit(alternative=alternative) if not suffix:
            return MIRStoredOutcome(frozenset({alternative}))
        case MIRWrap(alternative=alternative, source=source):
            if not suffix:
                return MIRStoredOutcome(frozenset({alternative}))
            head = suffix[0]
            if not isinstance(head, MIRUnionPayload):
                return None
            if head.alternative != alternative:
                return MIRStoredOutcome(vacates=True)
            return None if source is None else _selection_outcome(
                selected(MIRPlace(source.root, (*source.projections, *suffix[1:]))))
        case MIRCopy(source=source):
            return _selection_outcome(selected(MIRPlace(source.root, (*source.projections, *suffix))))
    return None


def _selection_outcome(selection: frozenset[int] | None) -> MIRStoredOutcome | None:
    return None if selection is None else MIRStoredOutcome(selection)


def written_selection(value: object, selected: Callable[[MIRPlace], frozenset[int] | None]) -> frozenset[int] | None:
    """The selection a direct write gives the sum it writes whole
    (`stored_selection` at the sum itself, which a write never vacates),
    None when unknown: the incoming selection presence reads
    (`sum_payload_ends` weighs the outcome itself, by `payload_fate`)."""
    outcome = stored_selection(value, (), selected)
    return None if outcome is None else outcome.alternatives


def sum_payload_ends(event: MIRStorageEvent, value: object,
                     selected: Callable[[MIRPlace], frozenset[int] | None]) -> tuple[tuple[MIRPlace, bool], ...]:
    """The old payloads a whole write of an inline sum reaches (`event`, of
    `storage_events`, writing `value`), each with whether it owns storage:
    an alternative the old selection (`selected`, None for unknown: any)
    may hold. A storage payload is reached whatever is written -- what the
    write does to it is its `payload_fate`, which the witness reads -- a
    scalar payload only where that fate is not an overwrite in place (a
    same-tag write ends nothing), so a value that states no selection (a
    call's result, a move) ends it."""
    old = selected(event.place)
    # A copy of the sum onto itself selects the alternative it already holds;
    # an element place is any element, so `xs[i] = xs[j]` is not one object.
    keeps = isinstance(value, MIRCopy) and value.source == event.place and not reaches_element(event.place)
    stored = stored_selection(value, (), selected)
    ends: list[tuple[MIRPlace, bool]] = []
    for alternative, owns in event.payloads:
        if old is not None and alternative not in old:
            continue
        if not owns and (keeps or payload_fate(stored, alternative) is MIREffect.OVERWRITES):
            continue
        ends.append((MIRPlace(event.place.root, (*event.place.projections, MIRUnionPayload(alternative))), owns))
    return tuple(ends)


def sum_places(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords,
               referent: bool = False) -> tuple[MIRPlace, ...]:
    """Every sum held by value at or under `place`, one per path: the place
    itself, and below a record stored inline each of its fields, below an
    owned tuple each member, below a sum each payload that is storage of
    its own -- each stepped by `place_shape`. Inline storage cannot contain
    itself, so the walk ends; a record whose layout is not here lists
    nothing under it, so a reader takes a sum it does not list as
    unknown. Per path, never per type: `w.a.o` and `w.b.o` are two sums.
    A path past a container's elements step names any element, no one
    sum, so none is listed there (its outcome is unknown, `may`).
    `referent`: `place` is a referent's (`MIRReferent.place`, no
    dereference steps), stepped as `referent_walk` steps one."""
    found: list[MIRPlace] = []

    def under(at: MIRPlace, step: Projection) -> MIRPlace:
        return MIRPlace(at.root, (*at.projections, step))

    def visit(at: MIRPlace) -> None:
        try:
            shape = referent_walk(at, slots, records).end if referent else place_shape(at, slots, records)
        except MIRValidationError:
            return
        if shape is None or shape.holding is not MIRHolding.VALUE:
            return
        if shape.cls in SUM_CLASSES:
            found.append(at)
            for alternative, member in enumerate(shape.members):
                if member is not None and member.is_storage:
                    visit(under(at, MIRUnionPayload(alternative)))
        elif shape.cls is MIRClass.TUPLE:
            for index in range(len(shape.members)):
                visit(under(at, MIRTupleIndex(index)))
        elif shape.cls is MIRClass.RECORD:
            layout = records.get(shape.type)
            for member in layout.fields if layout is not None and not layout.opaque else ():
                visit(under(at, member))
    if not reaches_element(place):
        visit(place)
    return tuple(found)


_K = TypeVar("_K")

# What a statement may store in each sum it writes (None: unknown).
MIRStored = dict[MIRPlace, MIRStoredOutcome | None]


def union_outcomes(a: MIRStoredOutcome | None, b: MIRStoredOutcome | None) -> MIRStoredOutcome | None:
    return None if a is None or b is None else a | b


def union_stored(found: dict[_K, MIRStoredOutcome | None], key: _K, outcome: MIRStoredOutcome | None) -> None:
    found[key] = union_outcomes(found[key], outcome) if key in found else outcome


def union_covered(found: MIRStored, written: MIRPlace, stated: Mapping[MIRPlace, MIRStoredOutcome | None],
                  slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords, referent: bool = False) -> None:
    """Union into `found` what a write of `written` stores in each sum at
    or under it (`sum_places`, `referent` as there), by what it states
    (`stated`): a sum the write covers but states nothing for is unknown,
    never left out -- a known outcome another write states for it must not
    stand alone."""
    for place in sum_places(written, slots, records, referent):
        union_stored(found, place, stated.get(place))


def statement_stored(stmt: MIRStatement, call_writes: tuple[MIRPlace, ...],
                     call_stored: Iterable[tuple[MIRPlace, MIRStoredOutcome | None]],
                     slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords,
                     selected: Callable[[MIRPlace], frozenset[int] | None]) -> MIRStored:
    """What a statement may store in each sum at or under a place it
    writes (`sum_places`), None when unknown: an assignment's by the one
    direct-write rule (`stored_selection`, over the incoming facts
    `selected`), a call's by what its callee publishes for the sum
    (`call_stored`, the caller's places, `MIRPresentCall.stored`) -- a sum
    under a call write it publishes nothing for is unknown -- and a move
    vacates what it empties. Several writes of one sum union. It states a
    fact about each sum's payload beside the replacement, which stays the
    whole written place."""
    found: MIRStored = {}
    if isinstance(stmt, MIRAssign) and not builds_base(stmt):
        target = stmt.target
        for place in sum_places(target, slots, records):
            union_stored(found, place, stored_selection(stmt.value, place.projections[len(target.projections):], selected))
    published: MIRStored = {}
    for place, outcome in call_stored:
        union_stored(published, place, outcome)
    for written in call_writes:
        union_covered(found, written, published, slots, records)
    if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRMove) and slots[stmt.value.source].shape.owns_storage:
        for place in sum_places(MIRPlace(stmt.value.source), slots, records):
            union_stored(found, place, MIRStoredOutcome(vacates=True))
    return found


class Replacement(NamedTuple):
    """One replacement: the place whose storage the statement replaces, the
    holder a whole-record write rebinds to it (None for any other), and the
    place the write's sums are stated below (`statement_stored`): the
    replaced place, or for a whole write of a local sum -- which replaces
    each payload it ends -- the sum written, so the payload's fate is read
    as a field's is."""
    place: MIRPlace
    rebinds: MIRSlotId | None
    anchor: MIRPlace


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
            found.append(Replacement(event.place, event.rebind_owner, event.place))
            continue
        assert isinstance(stmt, MIRAssign)
        found.extend(Replacement(place, event.rebind_owner, event.place)
                     for place, owns in sum_payload_ends(event, stmt.value, selected) if owns)
    return tuple(found)
