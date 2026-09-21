"""Possible ends of inline scalar payloads, without a lifetime-safety verdict."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .coverage import scalar_wrapper
from .dependencies import MIRDependencies, _dependencies
from .dump import _location, _place
from .liveness import MIRLiveness, _liveness
from .nodes import (
    MIRFunction, MIRNotCovered, MIROptionalConstruct, MIROptionalCopy,
    MIROptionalPayload, MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRPoint,
    MIRUnionConstruct, MIRUnionCopy, MIRUnionPayload, MIRValueKind, MIRStorageInit,
)
from .presence import MIRPresenceIssue, MIRPresenceIssueKind
from .validate import MIRPrepared, MIRPresenceError, MIRValidationError, _prepare_function, _validated_function


@dataclass(frozen=True)
class MIRPayloadEnds:
    function: MIRFunction
    ends: Mapping[MIRPoint, frozenset[MIRPlace]]


def analyze_payload_ends(fn: MIRFunction) -> MIRPayloadEnds | MIRNotCovered:
    return _payload_ends(_validated_function(fn))


def _payload_ends(prepared: MIRPrepared) -> MIRPayloadEnds | MIRNotCovered:
    fn, presence = prepared.function, prepared.presence
    if presence.function is not fn:
        raise MIRValidationError("selection facts belong to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    ends: dict[MIRPoint, frozenset[MIRPlace]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRStorageInit):
                continue
            value = stmt.value
            if not isinstance(value, (MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy)):
                continue
            slot = slots[stmt.target.root]
            if not scalar_wrapper(slot):
                continue
            fact = stmt.storage_write
            if not isinstance(fact, MIRPayloadWrite):
                return MIRNotCovered(fn.id, "payload ends", "missing payload write fact", stmt.loc)
            point = MIRPoint(block.id, index)
            if point not in presence.points or fact.mode in (
                    MIRPayloadWriteMode.INITIALIZE, MIRPayloadWriteMode.INITIALIZE_REGION):
                continue
            incoming = dict(presence.points[point])
            old = incoming.get(slot.id, presence.domains[slot.id])
            match value:
                case MIROptionalConstruct(source=source):
                    new = frozenset({int(source is not None)})
                case MIRUnionConstruct(alternative=alternative):
                    new = frozenset({alternative})
                case MIROptionalCopy(source=source) | MIRUnionCopy(source=source):
                    if source == slot.id:
                        continue
                    new = incoming.get(source, presence.domains[source])
            payloads: set[MIRPlace] = set()
            for alternative in old:
                if not new - {alternative}:
                    continue
                if slot.value_kind is MIRValueKind.OPTIONAL:
                    if alternative == 1:
                        payloads.add(MIRPlace(slot.id, (MIROptionalPayload(),)))
                elif slot.union_layout.elements[alternative] is not None:
                    payloads.add(MIRPlace(slot.id, (MIRUnionPayload(alternative),)))
            if payloads:
                ends[point] = frozenset(payloads)
    return MIRPayloadEnds(fn, MappingProxyType(ends))


@dataclass(frozen=True)
class MIRPayloadConflict:
    point: MIRPoint
    payload: MIRPlace
    holder: MIRPlace


@dataclass(frozen=True)
class MIRPayloadInspection:
    function: MIRFunction
    ends: MIRPayloadEnds | MIRNotCovered
    conflicts: tuple[MIRPayloadConflict, ...] | MIRNotCovered
    freshness: tuple[MIRPresenceIssue, ...]


def inspect_payload_lifetimes(fn: MIRFunction) -> MIRPayloadInspection:
    """Inspect stale aliases without admitting them as valid MIR."""
    prepared = _prepare_function(fn)
    for issue in prepared.presence.issues:
        if issue.kind is MIRPresenceIssueKind.SELECTION:
            raise MIRPresenceError(issue.message)
    liveness = _liveness(prepared)
    dependencies = _dependencies(prepared, liveness)
    ends = _payload_ends(prepared)
    conflicts = _payload_conflicts(prepared, liveness, dependencies, ends)
    return MIRPayloadInspection(fn, ends, conflicts, prepared.presence.issues)


def _payload_conflicts(prepared: MIRPrepared, liveness: MIRLiveness,
                       dependencies: MIRDependencies | MIRNotCovered,
                       ends: MIRPayloadEnds | MIRNotCovered) -> tuple[MIRPayloadConflict, ...] | MIRNotCovered:
    fn = prepared.function
    for result in (prepared.presence, liveness, dependencies, ends):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("payload inspection input belongs to a different MIR function")
    for result in (dependencies, ends):
        if isinstance(result, MIRNotCovered):
            return MIRNotCovered(fn.id, "payload conflicts", f"{result.node_kind}: {result.reason}", result.loc)
    conflicts: list[MIRPayloadConflict] = []
    for point, payloads in ends.ends.items():
        incoming = dependencies.referents[point]
        live_after = liveness.points[MIRPoint(point.block, point.index + 1)]
        for holder, refs in sorted(incoming.items(), key=lambda item: _place(item[0])):
            if holder.root not in live_after:
                continue
            # Inline payloads already name storage; they are not pointer-holder leaves.
            retained = {ref.place for ref in refs if not ref.external}
            for place in sorted(payloads & retained, key=_place):
                conflicts.append(MIRPayloadConflict(point, place, holder))
    return tuple(conflicts)


def dump_payload_inspection(result: MIRPayloadInspection) -> str:
    lines = ["payload retention (possible conflicts at static places; no lifetime-safety verdict)"]
    if isinstance(result.conflicts, MIRNotCovered):
        lines.append(f"  not covered: {result.conflicts.reason}")
    else:
        for conflict in result.conflicts:
            point = conflict.point
            lines.append(f"  bb{point.block.index} before {point.index}: end {_place(conflict.payload)}; "
                         f"{_place(conflict.holder)} retains payload")
        if not result.conflicts:
            lines.append("  no conflicts in covered payload-end events")
    for issue in result.freshness:
        point = issue.point
        lines.append(f"  freshness bb{point.block.index} before {point.index}: {issue.message}")
    return "\n".join(lines) + "\n"


def dump_payload_ends(result: MIRPayloadEnds | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"payload ends not covered: {result.reason}\n"
    blocks = {b.id: b for b in result.function.blocks}
    lines = ["payload ends (possible inline scalar lifetime ends; no safety verdict)"]
    for point, places in result.ends.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: "
                     + ", ".join(sorted(_place(p) for p in places)) + _location(loc))
    return "\n".join(lines) + "\n"
