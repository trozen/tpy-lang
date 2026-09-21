"""Possible retained-object conflicts at logical replacement events."""

from dataclasses import dataclass

from .dependencies import MIRDependencies, MIRReferent, resolve_referents
from .dump import _location, _place
from .liveness import MIRLiveness, MIRPoint
from .nodes import MIRField, MIRFunction, MIRNotCovered, MIRPlace, MIRRecordWrite, MIRRecordWriteMode
from .storage import MIRStorageEvents
from .presence import MIREngagement
from .validate import MIRValidationError, _validated_function


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
    for point, stmt in events.writes.items():
        fact = stmt.storage_write
        assert isinstance(fact, MIRRecordWrite)
        if fact.mode in (MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.INITIALIZE_REGION):
            continue
        if fact.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN:
            engagement = prepared.presence.engagement.get(point)
            if engagement is None:
                continue
            if MIREngagement.ENGAGED not in dict(engagement).get(stmt.target.root, frozenset()):
                continue
        incoming = dependencies.referents[point]
        live_after = liveness.points[MIRPoint(point.block, point.index + 1)]
        affected = resolve_referents(stmt.target, incoming, slots)
        for holder, refs in sorted(incoming.items(), key=lambda item: _place(item[0])):
            if holder.root not in live_after or holder.root == fact.rebind_owner:
                continue
            for written in sorted(affected, key=_referent_key):
                for retained in sorted(refs, key=_referent_key):
                    if may_overlap(written, retained):
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
