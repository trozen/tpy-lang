"""Normal emitted-storage ends, without destruction effects or safety authority."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType

from .place_text import _place
from .nodes import (
    MIRBlockId, MIREdge, MIRFunction, MIRNotCovered, MIRPlace, MIRRegionId, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRPoint, MIRRecordStorageKind,
)
from .shapes import MIRUnionPayload
from .presence import MIRPresenceIssue, MIREngagement
from .prepare import MIRPrepared, validated_presence
from .reach import BEGINS, MIRReferent, reaches, storage_events, storage_exclusions
from .region_flow import MIRRegionFlow, outgoing_edges
from .value import MIRValidationError


class MIRScopeEndKind(Enum):
    OBJECT = auto()
    RECORD_WRAPPER = auto()


@dataclass(frozen=True)
class MIRScopeEnd:
    region: MIRRegionId
    storage: MIRPlace
    payloads: frozenset[MIRPlace] = frozenset()
    kind: MIRScopeEndKind = MIRScopeEndKind.OBJECT


@dataclass(frozen=True)
class MIRScopeEnds:
    function: MIRFunction
    ends: Mapping[MIREdge, tuple[MIRScopeEnd, ...]]


def analyze_scope_ends(prepared: MIRPrepared) -> MIRScopeEnds | MIRNotCovered:
    validated_presence(prepared)
    return _scope_ends(prepared)


def _scope_ends(prepared: MIRPrepared) -> MIRScopeEnds | MIRNotCovered:
    fn, presence = prepared.function, prepared.presence
    if presence.function is not fn:
        raise MIRValidationError("selection facts belong to a different MIR function")
    if not fn.regions:
        return MIRNotCovered(fn.id, "scope ends", "missing emitted storage regions")
    roots = {s.id: s for s in fn.slots if s.shape.is_storage
             or s.shape.owned_tuple or s.kind is MIRSlotKind.LOCAL and s.shape.scalar_wrapper}
    for slot in roots.values():
        if slot.storage_duration is None:
            return MIRNotCovered(fn.id, "scope ends", f"missing storage placement for %{slot.id.index}",
                                 slot=slot.id)
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    slots = {s.id: s for s in fn.slots}
    # Storage a block begins: a root its statements construct, initialize
    # or write by their storage events (`storage_events`; the fact follows
    # the destination's storage, not the operation filling it).
    initialized: dict[MIRBlockId, set[MIRSlotId]] = {}
    for block in fn.blocks:
        initialized[block.id] = set()
        for stmt in block.statements:
            tracked = [event for event in storage_events(stmt, (), slots)
                       if not event.place.projections and event.place.root in roots]
            if any(not event.known for event in tracked):
                return MIRNotCovered(fn.id, "scope ends", "missing storage initialization/write fact", stmt.loc)
            initialized[block.id].update(event.place.root for event in tracked if event.kind in BEGINS)
    # A body-hoisted OWN site's optional backing can remain unengaged forever.
    # A by-value parameter's storage is initialized before entry.
    incoming: dict[MIRBlockId, frozenset[MIRSlotId]] = {
        fn.entry: frozenset(sid for sid, slot in roots.items() if slot.kind is MIRSlotKind.PARAMETER)}
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
            branch = state - regions.edges[edge].ended
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
                if slot.shape.scalar_wrapper:
                    wrapper = MIRPlace(sid)
                    selected = facts.get(wrapper, presence.domains[wrapper])
                    payloads.update(MIRPlace(sid, (*wrapper.projections, MIRUnionPayload(i))) for i in selected
                                    if slot.shape.alternatives[i] is not None)
                if slot.record_storage is MIRRecordStorageKind.OPTIONAL:
                    events.append(MIRScopeEnd(rid, MIRPlace(sid), kind=MIRScopeEndKind.RECORD_WRAPPER))
                    if MIREngagement.ENGAGED not in dict(presence.edge_engagement[edge]).get(sid, frozenset()):
                        continue
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
                         f"{_place(event.storage)}" + (" wrapper-only" if event.kind is MIRScopeEndKind.RECORD_WRAPPER else "")
                         + (f" payloads={{{payloads}}}" if payloads else ""))
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class MIRScopeConflict:
    edge: MIREdge
    ended: MIRPlace
    holder: MIRPlace
    retained: MIRReferent


@dataclass(frozen=True)
class MIRScopeInspection:
    function: MIRFunction
    ends: MIRScopeEnds | MIRNotCovered
    conflicts: tuple[MIRScopeConflict, ...] | MIRNotCovered
    freshness: tuple[MIRPresenceIssue, ...]


def inspect_scope_lifetimes(prepared: MIRPrepared) -> MIRScopeInspection:
    """Inspect retained aliases; a stale payload access never prepares
    (`prepare.prepare` refuses an unproven selection)."""
    ends = _scope_ends(prepared)
    conflicts = _scope_conflicts(prepared, ends)
    return MIRScopeInspection(prepared.function, ends, conflicts, prepared.presence.issues)


def _scope_conflicts(prepared: MIRPrepared,
                     ends: MIRScopeEnds | MIRNotCovered) -> tuple[MIRScopeConflict, ...] | MIRNotCovered:
    fn, liveness, dependencies = prepared.function, prepared.liveness, prepared.dependencies
    for result in (prepared.presence, liveness, dependencies, ends):
        if isinstance(result, MIRNotCovered):
            if result.body != fn.id:
                raise MIRValidationError("uncovered analysis belongs to a different MIR body")
        elif result.function is not fn:
            raise MIRValidationError("scope inspection input belongs to a different MIR function")
    for result in (dependencies, ends):
        if isinstance(result, MIRNotCovered):
            return MIRNotCovered(fn.id, "scope conflicts", f"{result.node_kind}: {result.reason}", result.loc)
    blocks = {b.id: b for b in fn.blocks}
    slots = {s.id: s for s in fn.slots}
    exclusions = storage_exclusions(slots, fn.record_layouts)
    regions = MIRRegionFlow(fn)
    conflicts = []
    for edge, events in ends.ends.items():
        target = regions.edges[edge].target
        # At a body exit, a loan stored in the caller's object is a store escape.
        if target is None:
            continue
        block = blocks[edge.source]
        incoming = dependencies.referents[MIRPoint(block.id, len(block.statements))]
        retained_holders = sorted(dependencies.live(incoming, liveness.live_in[target]).items(),
                                  key=lambda pair: _place(pair[0]))
        for event in events:
            if event.kind is MIRScopeEndKind.RECORD_WRAPPER:
                continue
            places = event.payloads if slots[event.storage.root].shape.scalar_wrapper else (event.storage,)
            for ended in sorted(places, key=_place):
                for holder, refs in retained_holders:
                    for retained in sorted(refs, key=lambda r: (r.external, _place(r.place))):
                        if reaches(MIRReferent(ended), retained, exclusions):
                            conflicts.append(MIRScopeConflict(edge, ended, holder, retained))
    return tuple(conflicts)


def dump_scope_inspection(result: MIRScopeInspection) -> str:
    lines = ["scope retention (possible read conflicts; cleanup and lifetime safety uncovered)"]
    if isinstance(result.conflicts, MIRNotCovered):
        lines.append(f"  not covered: {result.conflicts.reason}")
    else:
        for conflict in result.conflicts:
            edge = conflict.edge
            lines.append(f"  bb{edge.source.index} edge {edge.arm}: end {_place(conflict.ended)}; "
                         f"{_place(conflict.holder)} retains {_place(conflict.retained.place)}")
        if not result.conflicts:
            lines.append("  no read conflicts in covered normal storage-end events")
    for issue in result.freshness:
        point = issue.point
        lines.append(f"  freshness bb{point.block.index} before {point.index}: {issue.message}")
    return "\n".join(lines) + "\n"
