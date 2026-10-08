"""Possible retained-object conflicts at logical replacement events."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..thir.scalar_leaves import owned_leaf, record_type
from ..typesys import TpyType, unwrap_readonly

from .coverage import container_holder
from .dependencies import MIRDependencies, MIRReferent, _referent, resolve_referents
from .dump import _location, _place
from .liveness import MIRLiveness, MIRPoint
from .nodes import (
    MIRContainerElements, MIRContainerStructure, MIRField, MIRFunction, MIRNotCovered, MIRPlace, MIRRecordWrite,
    MIRRecordWriteMode, MIRSlot, MIRSlotId,
)
from .storage import MIRStorageEvents, owned_field
from .presence import MIREngagement
from .validate import MIRValidationError, _validated_function


# An initialization creates its storage, so no loan can predate it: the
# record writes outside this set are the replacement events.
INITIALIZING_WRITES = frozenset({MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.INITIALIZE_REGION})


@dataclass(frozen=True)
class MIRRetentionConflict:
    point: MIRPoint
    affected: MIRReferent
    holder: MIRPlace
    retained: MIRReferent


@dataclass(frozen=True)
class MIRRetention:
    function: MIRFunction
    conflicts: tuple[MIRRetentionConflict, ...]


def _origin(place: MIRPlace) -> MIRPlace:
    """The object an external referent starts from: its holder leaf, before
    any field or container region inside it."""
    for index, projection in enumerate(place.projections):
        if isinstance(projection, (MIRField, MIRContainerStructure, MIRContainerElements)):
            return MIRPlace(place.root, place.projections[:index])
    return place


def may_overlap(left: MIRReferent, right: MIRReferent) -> bool:
    """Private roots are distinct; external payload origins need no
    disjointness. A stored loan of an external object views storage of no
    known place, so it may be any external storage."""
    if left.external != right.external:
        return False
    if left.external and (left.held or right.held):
        return True
    if left.external and _origin(left.place) != _origin(right.place):
        return True
    if left.place.root != right.place.root:
        return False
    a, b = left.place.projections, right.place.projections
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def _replaced_leaf(place: MIRPlace) -> bool:
    return bool(place.projections) and isinstance(field := place.projections[-1], MIRField) and owned_leaf(field.type)


def _reach(written: MIRPlace) -> tuple[tuple[object, ...], bool] | None:
    """For a write that keeps the identity of the object around it, the
    projection path a holder's referent must lie under, and whether the
    path itself is reached; None for a write that replaces whole storage.
    A container's shape write reaches everything inside the container (its
    iterators, elements, views of them) but not the container object, which
    every holder of it keeps; an elements write reaches what lies in the
    elements region. Element identity is never tracked: a write of one
    element reaches a holder of any element."""
    match written.projections:
        case (*base, MIRContainerStructure()):
            return tuple(base), False
        case (*_, MIRContainerElements()):
            return written.projections, True
        case (*_, MIRField() as field) if owned_field(field):
            return written.projections, True
    return None


def _whole_container(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """A referent naming a container object itself (a borrowed container
    parameter's own origin), which no container holds as an element."""
    return not ref.place.projections and container_holder(slots[ref.place.root])


def _referent_type(ref: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> TpyType | None:
    """The type of the object a referent names, when its path is fields only."""
    typ = slots[ref.place.root].type
    for projection in ref.place.projections:
        if not isinstance(projection, MIRField):
            return None
        typ = unwrap_readonly(projection.type)
    return typ


def static_referent(ref: MIRReferent) -> bool:
    """A literal's storage, as the dependency pass marked it."""
    return ref.static


def affects(written: MIRReferent, retained: MIRReferent, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether replacing `written` reaches what a holder of `retained` holds.
    A literal's static storage is never written, so no replacement reaches
    it. Replacing an owned-leaf, container or record member field replaces
    that storage only: the record around it keeps its identity, so only a
    holder at or under the field is affected; a container's shape or elements write
    reaches what lies inside it (`_reach`). In private storage, or under one
    external origin, that is the path; across external origins that may
    alias, it is any holder that could lie inside the written storage: no
    record or container lives inside an opaque buffer, and no container is
    an element of another."""
    if static_referent(written) or static_referent(retained):
        return False
    if written.external != retained.external:
        return False
    # A stored loan of an external object views owned-leaf storage outside
    # the body that any external replacement may be, a sibling member of
    # the same object included (its place names the member, not what it views).
    if written.held or retained.held:
        return True
    reach = _reach(written.place)
    if reach is None:
        return may_overlap(written, retained)
    if not written.external or _origin(written.place) == _origin(retained.place):
        path, inclusive = reach
        held = retained.place.projections
        return (written.place.root == retained.place.root and held[:len(path)] == path
                and (inclusive or len(held) > len(path)))
    if not _replaced_leaf(written.place):
        # Across external origins that may alias, a container's interior or
        # a record member may hold any record or owned leaf a holder
        # retains; a container object is never an element, so only a shape
        # or elements write spares one.
        region = isinstance(written.place.projections[-1], (MIRContainerStructure, MIRContainerElements))
        return not (region and _whole_container(retained, slots))
    typ = _referent_type(retained, slots)
    return typ is None or not (record_type(typ) or _whole_container(retained, slots))


def analyze_retention(fn: MIRFunction, liveness: MIRLiveness,
                      dependencies: MIRDependencies | MIRNotCovered,
                      events: MIRStorageEvents | MIRNotCovered) -> MIRRetention | MIRNotCovered:
    prepared = _validated_function(fn)
    for result in (liveness, dependencies, events):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("retention input belongs to a different MIR function")
    for result in (dependencies, events):
        if isinstance(result, MIRNotCovered):
            return MIRNotCovered(fn.id, "retention", f"{result.node_kind}: {result.reason}", result.loc)
    slots = {slot.id: slot for slot in fn.slots}
    conflicts: list[MIRRetentionConflict] = []
    # Replacement events, each at its statement's point: assignments to
    # owned storage, the places a call's summary may write, and owned
    # storage a move empties.
    replaced: list[tuple[MIRPoint, MIRPlace, MIRSlotId | None]] = []
    for point, stmt in events.writes.items():
        fact = stmt.storage_write
        assert isinstance(fact, MIRRecordWrite)
        if fact.mode in INITIALIZING_WRITES:
            continue
        if fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN:
            engagement = prepared.presence.engagement.get(point)
            if engagement is None:
                continue
            if MIREngagement.ENGAGED not in dict(engagement).get(stmt.target.root, frozenset()):
                continue
        replaced.append((point, stmt.target, fact.rebind_owner))
    for point, places in events.call_writes.items():
        replaced.extend((point, place, None) for place in places)
    for point, place in events.moves.items():
        replaced.append((point, place, None))
    for point, target, rebind_owner in sorted(replaced, key=lambda e: (e[0].block.index, e[0].index, _place(e[1]))):
        incoming = dependencies.referents[point]
        after = MIRPoint(point.block, point.index + 1)
        outgoing = dependencies.referents[after]
        live_after = liveness.points[after]
        affected = resolve_referents(target, incoming, slots)
        # The statement may install a loan into the storage it replaces --
        # a callee's transfer, or the record it fills -- so the holders of
        # the state it leaves count too, and the filled object's own loans
        # before the holder rebinding to it is live.
        holders = dependencies.live(incoming, live_after)
        for leaf, refs in dependencies.live(outgoing, live_after).items():
            holders[leaf] = holders.get(leaf, frozenset()) | refs
        if not target.projections:
            for key in dependencies.objects[target.root]:
                holders[key] = holders.get(key, frozenset()) | outgoing.get(key, frozenset())
        for holder, refs in sorted(holders.items(), key=lambda item: _place(item[0])):
            if holder.root == rebind_owner:
                continue
            for written in sorted(affected, key=_referent_key):
                for retained in sorted(refs, key=_referent_key):
                    if affects(written, retained, slots):
                        conflicts.append(MIRRetentionConflict(point, written, holder, retained))
    return MIRRetention(fn, tuple(conflicts))


def _referent_key(ref: MIRReferent) -> tuple[bool, str]:
    return ref.external, _place(ref.place)


def dump_retention(result: MIRRetention | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"retention not covered: {result.reason}\n"
    blocks = {block.id: block for block in result.function.blocks}
    lines = ["retention (possible logical-object conflicts; no lifetime-safety verdict)"]
    if not result.conflicts:
        lines.append("  no conflicts in covered replacement events")
    for conflict in result.conflicts:
        point = conflict.point
        loc = blocks[point.block].statements[point.index].loc
        affected, retained = _referent(conflict.affected), _referent(conflict.retained)
        lines.append(f"  bb{point.block.index} before {point.index}: replace {affected}; "
                     f"{_place(conflict.holder)} retains {retained}{_location(loc)}")
    return "\n".join(lines) + "\n"
