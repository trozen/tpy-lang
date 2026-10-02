"""Substitute bounded parameter effects through the caller's existing referents."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..thir.nodes import THIRFieldIdentity
from .coverage import container_view_holder
from .dependencies import MIRDependencies, MIRReferent, MIRReferents, resolve_referents
from .dump import _place
from .nodes import (
    MIRCall, MIRContainerElements, MIRContainerStructure, MIRDeref, MIRField, MIRFieldId, MIRFunction,
    MIRNotCovered, MIRPlace, MIRPoint, MIRSlot, MIRSlotId, MIRValueKind, statement_call,
)
from .validate import MIRValidationError, validate_function


def _step(item: object) -> MIRField | MIRContainerStructure | MIRContainerElements:
    match item:
        case THIRFieldIdentity(owner=owner, name=name, type=typ):
            return MIRField(MIRFieldId(owner, name), typ)
        case MIRContainerStructure() | MIRContainerElements():
            return item
    raise MIRValidationError("unknown call write path item")


def _path_key(path: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(item.name if isinstance(item, THIRFieldIdentity) else type(item).__name__ for item in path)


def call_write_places(call: MIRCall, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[MIRPlace, ...]:
    """The caller's places a call may write: each summarized write on a
    parameter, at the record a borrowed record argument points at, or
    directly under a container argument (owned, borrowed, or a view)."""
    places = []
    for write in sorted(call.summary.writes, key=lambda w: (w.parameter, _path_key(w.path))):
        argument = slots[call.arguments[write.parameter]]
        through = argument.value_kind is MIRValueKind.BORROWED and not container_view_holder(argument)
        places.append(MIRPlace(argument.id, ((MIRDeref(),) if through else ()) + tuple(map(_step, write.path))))
    return tuple(places)


def resolve_call_writes(call: MIRCall, state: MIRReferents,
                        slots: Mapping[MIRSlotId, MIRSlot]) -> frozenset[MIRReferent] | None:
    """None means an effect has no proven origin, not that the call is harmless."""
    result: set[MIRReferent] = set()
    for place in call_write_places(call, slots):
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
    lines = ["call effects (possible field writes; owned-leaf fields are replacement events)"]
    for point, writes in result.writes.items():
        places = sorted(_place(r.place) + (" external" if r.external else "") for r in writes)
        lines.append(f"  bb{point.block.index} before {point.index}: writes={{" + ", ".join(places) + "}")
    return "\n".join(lines) + "\n"
