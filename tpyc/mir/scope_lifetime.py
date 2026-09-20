"""Normal emitted-storage ends, without destruction effects or safety authority."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .coverage import scalar_wrapper
from .dump import _place
from .nodes import (
    MIRBlockId, MIRConstruct, MIRCopy, MIREdge, MIRFunction, MIRMove,
    MIRNotCovered, MIROptionalPayload, MIRPayloadWrite, MIRPlace,
    MIRRecordWrite, MIRRegionId, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRUnionPayload, MIRValueKind,
)
from .region_flow import MIRRegionFlow, outgoing_edges
from .validate import MIRPrepared, MIRValidationError, _validated_function


@dataclass(frozen=True)
class MIRScopeEnd:
    region: MIRRegionId
    storage: MIRPlace
    payloads: frozenset[MIRPlace] = frozenset()


@dataclass(frozen=True)
class MIRScopeEnds:
    function: MIRFunction
    ends: Mapping[MIREdge, tuple[MIRScopeEnd, ...]]


def analyze_scope_ends(fn: MIRFunction) -> MIRScopeEnds | MIRNotCovered:
    return _scope_ends(_validated_function(fn))


def _scope_ends(prepared: MIRPrepared) -> MIRScopeEnds | MIRNotCovered:
    fn, presence = prepared.function, prepared.presence
    if presence.function is not fn:
        raise MIRValidationError("selection facts belong to a different MIR function")
    if not fn.regions:
        return MIRNotCovered(fn.id, "scope ends", "missing emitted storage regions")
    roots = {s.id: s for s in fn.slots if s.value_kind is MIRValueKind.RECORD_STORAGE
             or s.kind is MIRSlotKind.LOCAL and scalar_wrapper(s)}
    for slot in roots.values():
        if slot.storage_duration is None:
            return MIRNotCovered(fn.id, "scope ends", f"missing storage placement for %{slot.id.index}")
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    initialized: dict[MIRBlockId, set[MIRSlotId]] = {}
    for block in fn.blocks:
        initialized[block.id] = set()
        for stmt in block.statements:
            if stmt.target.projections or stmt.target.root not in roots:
                continue
            record = isinstance(stmt.value, (MIRConstruct, MIRCopy, MIRMove))
            if not isinstance(stmt.storage_write, MIRRecordWrite if record else MIRPayloadWrite):
                return MIRNotCovered(fn.id, "scope ends", "missing storage initialization/write fact", stmt.loc)
            initialized[block.id].add(stmt.target.root)
    # A body-hoisted OWN site's optional backing can remain unengaged forever.
    incoming: dict[MIRBlockId, frozenset[MIRSlotId]] = {fn.entry: frozenset()}
    outgoing: dict[MIRBlockId, frozenset[MIRSlotId]] = {}
    pending = deque([fn.entry])
    queued = {fn.entry}
    while pending:
        bid = pending.popleft()
        queued.remove(bid)
        state = incoming[bid] | initialized[bid]
        outgoing[bid] = state
        for edge, target in outgoing_edges(bid, blocks[bid].terminator):
            if target is None or edge not in presence.edges:
                continue
            branch = state - regions.edges[edge].reset
            updated = incoming.get(target, frozenset()) | branch
            if target not in incoming or updated != incoming[target]:
                incoming[target] = updated
                if target not in queued:
                    queued.add(target)
                    pending.append(target)
    ends: dict[MIREdge, tuple[MIRScopeEnd, ...]] = {}
    body_region = next(r.id for r in fn.regions if r.parent is None)
    for edge, transition in regions.edges.items():
        if edge not in presence.edges or edge.source not in outgoing:
            continue
        facts = dict(presence.edges[edge])
        events = []
        for rid in transition.exited:
            for sid in sorted(transition.ended & outgoing[edge.source], key=lambda s: s.index):
                slot = roots.get(sid)
                if slot is None:
                    continue
                owner = body_region if slot.storage_duration is MIRStorageDuration.BODY else slot.storage_duration
                if owner != rid:
                    continue
                payloads: set[MIRPlace] = set()
                if scalar_wrapper(slot):
                    selected = facts.get(sid, presence.domains[sid])
                    if slot.value_kind is MIRValueKind.OPTIONAL:
                        if 1 in selected:
                            payloads.add(MIRPlace(sid, (MIROptionalPayload(),)))
                    else:
                        payloads.update(MIRPlace(sid, (MIRUnionPayload(i),)) for i in selected
                                        if slot.union_layout.elements[i] is not None)
                events.append(MIRScopeEnd(rid, MIRPlace(sid), frozenset(payloads)))
        if events:
            ends[edge] = tuple(events)
    return MIRScopeEnds(fn, MappingProxyType(ends))


def dump_scope_ends(result: MIRScopeEnds | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"scope ends not covered: {result.reason}\n"
    lines = ["scope ends (possible normal storage ends; cleanup effects uncovered)"]
    for edge, events in result.ends.items():
        for event in events:
            payloads = ", ".join(sorted(_place(p) for p in event.payloads))
            lines.append(f"  bb{edge.source.index} edge {edge.arm}: r{event.region.index} "
                         f"{_place(event.storage)}" + (f" payloads={{{payloads}}}" if payloads else ""))
    return "\n".join(lines) + "\n"
