"""Possible ends of inline scalar payloads, without a lifetime-safety verdict."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .place_text import _location, _place
from .nodes import MIRAssign, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint
from .presence import MIRPresenceIssue
from .prepare import MIRPrepared, validated_presence
from .reach import storage_events, sum_payload_ends
from .value import MIRValidationError


@dataclass(frozen=True)
class MIRPayloadEnds:
    function: MIRFunction
    ends: Mapping[MIRPoint, frozenset[MIRPlace]]


def analyze_payload_ends(prepared: MIRPrepared) -> MIRPayloadEnds | MIRNotCovered:
    validated_presence(prepared)
    return _payload_ends(prepared)


def _payload_ends(prepared: MIRPrepared) -> MIRPayloadEnds | MIRNotCovered:
    fn, presence = prepared.function, prepared.presence
    if presence.function is not fn:
        raise MIRValidationError("selection facts belong to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    ends: dict[MIRPoint, frozenset[MIRPlace]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            # A whole sum write's scalar payloads (`storage_events`), weighed
            # by the one rule replacement reads too (`sum_payload_ends`).
            written = [event for event in storage_events(stmt, (), slots) if event.payloads]
            if not written:
                continue
            assert isinstance(stmt, MIRAssign)
            # The validator demands the fact on every whole write of an inline sum.
            if not all(event.known for event in written):
                raise MIRValidationError("inline sum write without its storage write fact")
            point = MIRPoint(block.id, index)
            if point not in presence.points:
                continue
            incoming = dict(presence.points[point])
            payloads = {place for event in written
                        for place, owns in sum_payload_ends(event, stmt.value, incoming.get) if not owns}
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


def inspect_payload_lifetimes(prepared: MIRPrepared) -> MIRPayloadInspection:
    """Inspect stale aliases without admitting them as valid MIR; a stale
    payload access never prepares (`prepare.prepare`)."""
    ends = _payload_ends(prepared)
    conflicts = _payload_conflicts(prepared, ends)
    return MIRPayloadInspection(prepared.function, ends, conflicts, prepared.presence.issues)


def _payload_conflicts(prepared: MIRPrepared,
                       ends: MIRPayloadEnds | MIRNotCovered) -> tuple[MIRPayloadConflict, ...] | MIRNotCovered:
    fn, liveness, dependencies = prepared.function, prepared.liveness, prepared.dependencies
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
        live_after = dependencies.live(incoming, liveness.points[MIRPoint(point.block, point.index + 1)])
        for holder, refs in sorted(live_after.items(), key=lambda item: _place(item[0])):
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
