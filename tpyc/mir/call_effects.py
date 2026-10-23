"""Substitute bounded parameter effects through the caller's existing referents."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .dependencies import MIRDependencies, MIRReferent, MIRReferents, resolve_referents
from .dump import _place
from ..typesys import TpyType
from .nodes import MIRFunction, MIRNotCovered, MIRPoint, MIRPresentCall, MIRSlot, MIRSlotId
from .value import MIRRecordLayout, MIRValidationError
from .validate import _validated_function


def resolve_call_writes(call: MIRPresentCall, state: MIRReferents, slots: Mapping[MIRSlotId, MIRSlot],
                        records: Mapping[TpyType, MIRRecordLayout]) -> frozenset[MIRReferent] | None:
    """The referents a call's written places (`MIRPresentCall.writes`)
    reach. None means an effect has no proven origin, not that the call is
    harmless."""
    result: set[MIRReferent] = set()
    for place in call.writes:
        origins = resolve_referents(place, state, slots, records)
        if not origins:
            return None
        result.update(origins)
    return frozenset(result)


@dataclass(frozen=True)
class MIRCallEffects:
    function: MIRFunction
    writes: Mapping[MIRPoint, frozenset[MIRReferent]]


def analyze_call_effects(fn: MIRFunction, dependencies: MIRDependencies | MIRNotCovered
                         ) -> MIRCallEffects | MIRNotCovered:
    present = _validated_function(fn).presence.calls
    if isinstance(dependencies, MIRNotCovered):
        return MIRNotCovered(fn.id, "call effects", dependencies.reason)
    if dependencies.function is not fn:
        raise MIRValidationError("dependencies belong to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    records = fn.record_layouts
    writes: dict[MIRPoint, frozenset[MIRReferent]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            point = MIRPoint(block.id, index)
            call = present.get(point)
            if call is None or point not in dependencies.referents:
                continue
            effects = resolve_call_writes(call, dependencies.referents[point], slots, records)
            if effects is None:
                return MIRNotCovered(fn.id, "call effects", "missing call write origin", stmt.loc)
            writes[point] = effects
    return MIRCallEffects(fn, MappingProxyType(writes))


def dump_call_effects(result: MIRCallEffects | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"call effects not covered: {result.reason}\n"
    lines = ["call effects (possible field writes; owned-leaf fields are replacement events)"]
    for point, writes in result.writes.items():
        places = sorted(_place(r.place) + (" external" if r.external else "") for r in writes)
        lines.append(f"  bb{point.block.index} before {point.index}: writes={{" + ", ".join(places) + "}")
    return "\n".join(lines) + "\n"
