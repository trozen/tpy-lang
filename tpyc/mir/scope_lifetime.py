"""Normal emitted-storage ends, without destruction effects or safety authority."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .coverage import scalar_wrapper
from .dependencies import MIRDependencies, MIRReferent, _dependencies
from .dump import _place
from .liveness import MIRLiveness, _liveness
from .nodes import (
    MIRBlockId, MIRConstruct, MIRCopy, MIREdge, MIRFunction, MIRMove,
    MIRNotCovered, MIROptionalPayload, MIRPayloadWrite, MIRPlace,
    MIRRecordWrite, MIRRegionId, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRUnionPayload, MIRValueKind, MIRPoint, MIRStorageInit,
)
from .presence import MIRPresenceIssue, MIRPresenceIssueKind
from .region_flow import MIRRegionFlow, outgoing_edges
from .retention import may_overlap
from .validate import MIRPrepared, MIRPresenceError, MIRValidationError, _prepare_function, _validated_function


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
            if isinstance(stmt, MIRStorageInit):
                initialized[block.id].add(stmt.target.root)
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


def inspect_scope_lifetimes(fn: MIRFunction) -> MIRScopeInspection:
    """Inspect retained aliases without admitting stale payload accesses."""
    prepared = _prepare_function(fn)
    for issue in prepared.presence.issues:
        if issue.kind is MIRPresenceIssueKind.SELECTION:
            raise MIRPresenceError(issue.message)
    live = _liveness(prepared)
    dependencies = _dependencies(prepared, live)
    ends = _scope_ends(prepared)
    conflicts = _scope_conflicts(prepared, live, dependencies, ends)
    return MIRScopeInspection(fn, ends, conflicts, prepared.presence.issues)


def _scope_conflicts(prepared: MIRPrepared, liveness: MIRLiveness,
                     dependencies: MIRDependencies | MIRNotCovered,
                     ends: MIRScopeEnds | MIRNotCovered) -> tuple[MIRScopeConflict, ...] | MIRNotCovered:
    fn = prepared.function
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
    regions = MIRRegionFlow(fn)
    conflicts = []
    for edge, events in ends.ends.items():
        target = regions.edges[edge].target
        live = liveness.live_in[target] if target is not None else frozenset()
        if not live:
            continue
        block = blocks[edge.source]
        incoming = dependencies.referents[MIRPoint(block.id, len(block.statements))]
        retained_holders = sorted(((holder, refs) for holder, refs in incoming.items() if holder.root in live),
                                  key=lambda pair: _place(pair[0]))
        for event in events:
            places = event.payloads if scalar_wrapper(slots[event.storage.root]) else (event.storage,)
            for ended in sorted(places, key=_place):
                for holder, refs in retained_holders:
                    for retained in sorted(refs, key=lambda r: (r.external, _place(r.place))):
                        if may_overlap(MIRReferent(ended), retained):
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
