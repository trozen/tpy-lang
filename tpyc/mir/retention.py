"""Possible retained-object conflicts at logical replacement events."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..thir.scalar_leaves import owned_leaf, record_type
from ..typesys import TpyType, unwrap_readonly

from .dependencies import MIRDependencies, MIRReferent, resolve_referents
from .dump import _location, _place
from .liveness import MIRLiveness, MIRPoint
from .nodes import (
    MIRField, MIRFunction, MIRNotCovered, MIRPlace, MIRRecordWrite, MIRRecordWriteMode, MIRSlot, MIRSlotId,
    MIRSlotKind,
)
from .storage import MIRStorageEvents
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
    for index, projection in enumerate(place.projections):
        if isinstance(projection, MIRField):
            return MIRPlace(place.root, place.projections[:index])
    return place


def may_overlap(left: MIRReferent, right: MIRReferent) -> bool:
    """Private roots are distinct; external payload origins need no disjointness."""
    if left.external != right.external:
        return False
    if left.external and _origin(left.place) != _origin(right.place):
        return True
    if left.place.root != right.place.root:
        return False
    a, b = left.place.projections, right.place.projections
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def _replaced_leaf(place: MIRPlace) -> bool:
    return bool(place.projections) and isinstance(field := place.projections[-1], MIRField) and owned_leaf(field.type)


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
    it. Replacing an owned-leaf field replaces that buffer only: the record
    around it keeps its identity, so only a holder at or under the field is
    affected. In private storage, or under one external origin, that is the
    path; across external origins that may alias, it is any holder that is
    not of a record, since no record lives inside an opaque buffer."""
    if static_referent(written) or static_referent(retained):
        return False
    if not may_overlap(written, retained):
        return False
    if not _replaced_leaf(written.place):
        return True
    if not written.external or _origin(written.place) == _origin(retained.place):
        width = len(written.place.projections)
        return retained.place.projections[:width] == written.place.projections
    typ = _referent_type(retained, slots)
    return typ is None or not record_type(typ)


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
    # Replacement events: assignments to whole storage, and the field
    # buffers a call's summary may write, both at their statement's point.
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
    for point, target, rebind_owner in sorted(replaced, key=lambda e: (e[0].block.index, e[0].index, _place(e[1]))):
        incoming = dependencies.referents[point]
        live_after = liveness.points[MIRPoint(point.block, point.index + 1)]
        affected = resolve_referents(target, incoming, slots)
        for holder, refs in sorted(incoming.items(), key=lambda item: _place(item[0])):
            if holder.root not in live_after or holder.root == rebind_owner:
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
        affected = ("external:" if conflict.affected.external else "storage:") + _place(conflict.affected.place)
        retained = ("external:" if conflict.retained.external else "storage:") + _place(conflict.retained.place)
        lines.append(f"  bb{point.block.index} before {point.index}: replace {affected}; "
                     f"{_place(conflict.holder)} retains {retained}{_location(loc)}")
    return "\n".join(lines) + "\n"
