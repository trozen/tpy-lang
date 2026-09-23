"""Substitute bounded parameter effects through the caller's existing referents."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .dependencies import MIRDependencies, MIRReferent, MIRReferents, resolve_referents
from .dump import _place
from .nodes import (
    MIRCall, MIRDeref, MIRField, MIRFieldId, MIRFunction,
    MIRNotCovered, MIRPlace, MIRPoint, MIRSlot, MIRSlotId, statement_call,
)
from .validate import MIRValidationError, validate_function


def resolve_call_writes(call: MIRCall, state: MIRReferents,
                        slots: Mapping[MIRSlotId, MIRSlot]) -> frozenset[MIRReferent] | None:
    """None means an effect has no proven origin, not that the call is harmless."""
    result: set[MIRReferent] = set()
    for write in call.summary.writes:
        place = MIRPlace(call.arguments[write.parameter], (MIRDeref(), *(
            MIRField(MIRFieldId(f.owner, f.name), f.type) for f in write.path)))
        origins = resolve_referents(place, state, slots)
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
    validate_function(fn)
    if isinstance(dependencies, MIRNotCovered):
        return MIRNotCovered(fn.id, "call effects", dependencies.reason)
    if dependencies.function is not fn:
        raise MIRValidationError("dependencies belong to a different MIR function")
    slots = {s.id: s for s in fn.slots}
    writes: dict[MIRPoint, frozenset[MIRReferent]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            call = statement_call(stmt)
            point = MIRPoint(block.id, index)
            if call is None or point not in dependencies.referents:
                continue
            effects = resolve_call_writes(call, dependencies.referents[point], slots)
            if effects is None:
                return MIRNotCovered(fn.id, "call effects", "missing call write origin", stmt.loc)
            writes[point] = effects
    return MIRCallEffects(fn, MappingProxyType(writes))


def dump_call_effects(result: MIRCallEffects | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"call effects not covered: {result.reason}\n"
    lines = ["call effects (possible scalar-field writes; no storage replacement)"]
    for point, writes in result.writes.items():
        places = sorted(_place(r.place) + (" external" if r.external else "") for r in writes)
        lines.append(f"  bb{point.block.index} before {point.index}: writes={{" + ", ".join(places) + "}")
    return "\n".join(lines) + "\n"
