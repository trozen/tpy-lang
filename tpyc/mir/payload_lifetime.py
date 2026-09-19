"""Possible ends of inline scalar payloads, without a lifetime-safety verdict."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .coverage import scalar_wrapper
from .dump import _location, _place
from .nodes import (
    MIRFunction, MIRNotCovered, MIROptionalConstruct, MIROptionalCopy,
    MIROptionalPayload, MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRPoint,
    MIRUnionConstruct, MIRUnionCopy, MIRUnionPayload, MIRValueKind,
)
from .presence import MIRPresence, _analyze_presence
from .validate import MIRValidationError, validate_function


@dataclass(frozen=True)
class MIRPayloadEnds:
    function: MIRFunction
    ends: Mapping[MIRPoint, frozenset[MIRPlace]]


def analyze_payload_ends(fn: MIRFunction) -> MIRPayloadEnds | MIRNotCovered:
    validate_function(fn)
    return _payload_ends(fn, _analyze_presence(fn))


def _payload_ends(fn: MIRFunction, presence: MIRPresence) -> MIRPayloadEnds | MIRNotCovered:
    if presence.function is not fn:
        raise MIRValidationError("selection facts belong to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    ends: dict[MIRPoint, frozenset[MIRPlace]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
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
            if point not in presence.points or fact.mode is MIRPayloadWriteMode.INITIALIZE:
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
