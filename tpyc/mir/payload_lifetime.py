"""Holders of inline scalar payloads a statement ends (`MIRStatementKills.payloads`), without a
lifetime-safety verdict."""

from dataclasses import dataclass

from .place_text import _location, _place
from .nodes import MIRFunction, MIRNotCovered, MIRPlace, MIRPoint
from .presence import MIRPresenceIssue
from .prepare import MIRPrepared
from .reach import MIRReferent
from .value import MIRValidationError
from .witness import MIRHazard, MIRHazardKind


@dataclass(frozen=True)
class MIRPayloadConflict:
    point: MIRPoint
    payload: MIRPlace
    holder: MIRPlace


@dataclass(frozen=True)
class MIRPayloadInspection:
    function: MIRFunction
    conflicts: tuple[MIRPayloadConflict, ...] | MIRNotCovered
    freshness: tuple[MIRPresenceIssue, ...]
    # One per ended payload and holder, for the witness.
    hazards: tuple[MIRHazard, ...] = ()


def inspect_payload_lifetimes(prepared: MIRPrepared) -> MIRPayloadInspection:
    """Inspect stale aliases without admitting them as valid MIR; a stale
    payload access never prepares (`prepare.prepare`)."""
    found = _payload_findings(prepared)
    if isinstance(found, MIRNotCovered):
        return MIRPayloadInspection(prepared.function, found, prepared.presence.issues)
    return MIRPayloadInspection(prepared.function, found[0], prepared.presence.issues, found[1])


def _payload_conflicts(prepared: MIRPrepared) -> tuple[MIRPayloadConflict, ...] | MIRNotCovered:
    found = _payload_findings(prepared)
    return found if isinstance(found, MIRNotCovered) else found[0]


def _payload_findings(prepared: MIRPrepared
                      ) -> tuple[tuple[MIRPayloadConflict, ...], tuple[MIRHazard, ...]] | MIRNotCovered:
    fn, liveness, dependencies = prepared.function, prepared.liveness, prepared.dependencies
    for result in (prepared.presence, liveness, dependencies):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("payload inspection input belongs to a different MIR function")
    if isinstance(dependencies, MIRNotCovered):
        return MIRNotCovered(fn.id, "payload conflicts", f"{dependencies.node_kind}: {dependencies.reason}",
                             dependencies.loc)
    conflicts: list[MIRPayloadConflict] = []
    hazards: list[MIRHazard] = []
    for point, payloads in prepared.kills.payloads.items():
        incoming = dependencies.referents[point]
        live_after = dependencies.live(incoming, liveness.points[MIRPoint(point.block, point.index + 1)])
        for holder, refs in sorted(live_after.items(), key=lambda item: _place(item[0])):
            # Inline payloads already name storage; they are not pointer-holder leaves.
            retained = {ref.place for ref in refs if not ref.external}
            for place in sorted(payloads & retained, key=_place):
                conflicts.append(MIRPayloadConflict(point, place, holder))
                hazards.append(MIRHazard(MIRHazardKind.PAYLOAD_END, point, place,
                                         frozenset({MIRReferent(place)}), holder, (refs,)))
    return tuple(conflicts), tuple(hazards)


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


def dump_payload_ends(prepared: MIRPrepared | MIRNotCovered) -> str:
    """The inline scalar payloads each statement ends (`MIRStatementKills.payloads`)."""
    if isinstance(prepared, MIRNotCovered):
        return f"payload ends not covered: {prepared.reason}\n"
    blocks = {b.id: b for b in prepared.function.blocks}
    lines = ["payload ends (possible inline scalar lifetime ends; no safety verdict)"]
    for point, places in prepared.kills.payloads.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: "
                     + ", ".join(sorted(_place(p) for p in places)) + _location(loc))
    return "\n".join(lines) + "\n"
