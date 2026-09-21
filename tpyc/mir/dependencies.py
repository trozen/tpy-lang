"""Possible referents and live dependencies, without lifetime-safety verdicts."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..typesys import BOOL, INT32, NominalType, unwrap_readonly
from .dump import _place
from .liveness import MIRLiveness, MIRPoint
from .nodes import (
    MIRAlias, MIRStatement, MIRStorageInit, MIRRecordStorageInit, MIRBlockId, MIRBorrow, MIRCompare, MIRConstant,
    MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFunction, MIRIsAlternative,
    MIRIsPresent, MIRMove, MIRNot, MIRNotCovered, MIROptionalConstruct,
    MIROptionalCopy, MIROptionalPayload, MIRPlace, MIRRead, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRTupleConstruct, MIRTupleCopy, MIRTupleIndex, MIRUnionConstruct,
    MIRUnionCopy, MIRUnionExtract, MIRUnionPayload, MIRValueKind,
    MIRContainerStructure, MIRContainerElements,
    MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance,
)
from .validate import MIRPrepared, MIRValidationError, _validated_function, successors
from .region_flow import MIRRegionFlow, outgoing_edges
from .coverage import owned_tuple


@dataclass(frozen=True)
class MIRReferent:
    place: MIRPlace
    # External origins may alias one another, including ancestor/child paths.
    external: bool = False


MIRReferents = Mapping[MIRPlace, frozenset[MIRReferent]]


@dataclass(frozen=True)
class MIRDependencies:
    function: MIRFunction
    referents: Mapping[MIRPoint, MIRReferents]
    active: Mapping[MIRPoint, MIRReferents]
    holders: Mapping[MIRPoint, Mapping[MIRReferent, frozenset[MIRPlace]]]
    entry_active: MIRReferents


def _leaves(slot: MIRSlot) -> tuple[MIRPlace, ...]:
    match slot.value_kind:
        case (MIRValueKind.BORROWED_RECORD | MIRValueKind.PAYLOAD_ALIAS
              | MIRValueKind.BORROWED_CONTAINER | MIRValueKind.NATIVE_ITERATOR):
            return (MIRPlace(slot.id),)
        case MIRValueKind.TUPLE:
            return tuple(MIRPlace(slot.id, (MIRTupleIndex(i),))
                         for i, m in enumerate(slot.tuple_layout.elements)
                         if m.kind is MIRValueKind.BORROWED_RECORD)
        case MIRValueKind.OPTIONAL:
            return ((MIRPlace(slot.id, (MIROptionalPayload(),)),)
                    if slot.optional_layout.kind is MIRValueKind.BORROWED_RECORD else ())
        case MIRValueKind.UNION:
            return tuple(MIRPlace(slot.id, (MIRUnionPayload(i),))
                         for i, m in enumerate(slot.union_layout.elements)
                         if m is not None and m.kind is MIRValueKind.BORROWED_RECORD)
        case MIRValueKind.SCALAR | MIRValueKind.RECORD_STORAGE:
            return ()
        case _:
            raise MIRValidationError("unknown dependency holder kind")


def _coverage(fn: MIRFunction) -> str | None:
    slots = {s.id: s for s in fn.slots}
    for slot in fn.slots:
        root = (slot if slot.value_kind is MIRValueKind.RECORD_STORAGE or owned_tuple(slot) else
                slots[slot.alias_source.root] if slot.alias_source is not None else None)
        if root is not None and root.storage_duration is None:
            return f"missing storage duration for %{root.id.index}"
    places = [s.alias_source for s in fn.slots if s.alias_source is not None]
    for block in fn.blocks:
        for stmt in block.statements:
            places.append(stmt.target)
            if isinstance(stmt, (MIRStorageInit, MIRRecordStorageInit)):
                continue
            match stmt.value:
                case MIRBorrow(source=p) | MIRRead(source=p) | MIRCopy(source=p) | MIRUnionExtract(source=p):
                    places.append(p)
    graph: dict[NominalType, set[NominalType]] = {}
    for place in places:
        for projection in place.projections:
            if isinstance(projection, MIRField) and (typ := unwrap_readonly(projection.type)) not in (BOOL, INT32):
                graph.setdefault(projection.id.owner, set()).add(typ)
                graph.setdefault(typ, set())
    degree = {typ: 0 for typ in graph}
    for children in graph.values():
        for typ in children:
            degree[typ] += 1
    pending = [typ for typ, count in degree.items() if count == 0]
    count = 0
    while pending:
        typ = pending.pop()
        count += 1
        for child in graph[typ]:
            degree[child] -= 1
            if degree[child] == 0:
                pending.append(child)
    return "recursive inline field paths" if count != len(graph) else None


def resolve_referents(place: MIRPlace, state: MIRReferents,
                      slots: Mapping[MIRSlotId, MIRSlot]) -> frozenset[MIRReferent]:
    """Resolve a validated place against the referents at its program point."""
    empty: frozenset[MIRReferent] = frozenset()
    # Payload selectors address a holder leaf; dereference follows its value.
    leaf = MIRPlace(place.root)
    refs = (frozenset({MIRReferent(leaf)})
            if slots[place.root].value_kind is MIRValueKind.RECORD_STORAGE else state.get(leaf, empty))
    for projection in place.projections:
        match projection:
            case MIRTupleIndex():
                leaf = MIRPlace(leaf.root, (*leaf.projections, projection))
                member = slots[place.root].tuple_layout.elements[projection.index]
                refs = (frozenset({MIRReferent(leaf)}) if member.kind is MIRValueKind.RECORD_STORAGE
                        else state.get(leaf, empty))
            case MIROptionalPayload() | MIRUnionPayload():
                leaf = MIRPlace(leaf.root, (*leaf.projections, projection))
                refs = state.get(leaf, empty)
            case MIRDeref():
                pass
            case MIRField() | MIRContainerStructure() | MIRContainerElements():
                refs = frozenset(MIRReferent(MIRPlace(r.place.root, (*r.place.projections, projection)), r.external)
                                 for r in refs)
            case _:
                raise MIRValidationError("unknown dependency projection")
    return refs


def analyze_dependencies(fn: MIRFunction, liveness: MIRLiveness) -> MIRDependencies | MIRNotCovered:
    return _dependencies(_validated_function(fn), liveness)


def _dependencies(prepared: MIRPrepared, liveness: MIRLiveness) -> MIRDependencies | MIRNotCovered:
    fn = prepared.function
    if liveness.function is not fn:
        raise MIRValidationError("liveness belongs to a different MIR function")
    reason = _coverage(fn)
    if reason is not None:
        return MIRNotCovered(fn.id, "dependencies", reason)
    slots = {s.id: s for s in fn.slots}
    leaves = {s.id: _leaves(s) for s in fn.slots}
    empty: frozenset[MIRReferent] = frozenset()

    def transfer(stmt: MIRStatement, state: dict[MIRPlace, frozenset[MIRReferent]]) -> None:
        if isinstance(stmt, (MIRStorageInit, MIRRecordStorageInit)):
            return
        target, value = stmt.target, stmt.value
        result: dict[MIRPlace, frozenset[MIRReferent]] = {}
        match value:
            case MIRIteratorInit(source=source):
                result[target] = frozenset(
                    MIRReferent(MIRPlace(r.place.root, (*r.place.projections, projection)), r.external)
                    for r in state.get(MIRPlace(source), empty)
                    for projection in (MIRContainerStructure(), MIRContainerElements()))
            case MIRIteratorAdvance(source=source):
                result[target] = state.get(MIRPlace(source), empty)
            case MIRIteratorRead(source=source):
                if slots[target.root].value_kind is MIRValueKind.BORROWED_RECORD:
                    result[target] = frozenset(r for r in state.get(MIRPlace(source), empty)
                                               if isinstance(r.place.projections[-1], MIRContainerElements))
            case MIRAlias(source=source):
                result[target] = state.get(MIRPlace(source), empty)
            case MIRBorrow(source=source):
                result[target] = resolve_referents(source, state, slots)
            case MIRUnionExtract(source=source):
                result[target] = (frozenset({MIRReferent(source)})
                                  if slots[target.root].value_kind is MIRValueKind.PAYLOAD_ALIAS
                                  else resolve_referents(source, state, slots))
            case MIRTupleCopy(source=source) | MIROptionalCopy(source=source) | MIRUnionCopy(source=source):
                result = {leaf: state.get(MIRPlace(source, leaf.projections), empty) for leaf in leaves[target.root]}
            case MIRTupleConstruct(elements=elements):
                result = {leaf: state.get(MIRPlace(elements[leaf.projections[0].index]), empty)
                          for leaf in leaves[target.root]}
            case MIROptionalConstruct(source=source):
                if source is not None:
                    result = {leaf: state.get(MIRPlace(source), empty) for leaf in leaves[target.root]}
            case MIRUnionConstruct(alternative=alternative, source=source):
                leaf = MIRPlace(target.root, (MIRUnionPayload(alternative),))
                if source is not None and leaf in leaves[target.root]:
                    result[leaf] = state.get(MIRPlace(source), empty)
            case (MIRConstant() | MIRRead() | MIRCompare() | MIRNot() | MIRIsPresent()
                  | MIRIsAlternative() | MIRConstruct() | MIRCopy() | MIRMove() | MIRIteratorHasNext()
                  | MIRRangeAdvance()):
                pass
            case _:
                raise MIRValidationError("unknown dependency operation")
        if not target.projections:
            for leaf in leaves[target.root]:
                state.pop(leaf, None)
            state.update((leaf, refs) for leaf, refs in result.items() if refs)

    seed = {leaf: frozenset({MIRReferent(leaf, external=True)})
            for slot in fn.slots if slot.kind is MIRSlotKind.PARAMETER for leaf in leaves[slot.id]}
    blocks = {b.id: b for b in fn.blocks}
    regions = MIRRegionFlow(fn)
    incoming: dict[MIRBlockId, dict[MIRPlace, frozenset[MIRReferent]]] = {fn.entry: seed.copy()}
    work = deque([fn.entry])
    queued = {fn.entry}
    while work:
        bid = work.popleft()
        queued.remove(bid)
        state = incoming[bid].copy()
        block = blocks[bid]
        for stmt in block.statements:
            transfer(stmt, state)
        for edge, dest in outgoing_edges(bid, block.terminator):
            if dest is None:
                continue
            changed = dest not in incoming
            joined = incoming.setdefault(dest, {})
            for leaf, refs in state.items():
                if leaf.root in regions.edges[edge].reset:
                    continue
                merged = joined.get(leaf, empty) | refs
                if merged != joined.get(leaf, empty):
                    joined[leaf] = merged
                    changed = True
            if changed and dest not in queued:
                work.append(dest)
                queued.add(dest)

    points: dict[MIRPoint, MIRReferents] = {}
    active: dict[MIRPoint, MIRReferents] = {}
    holders: dict[MIRPoint, Mapping[MIRReferent, frozenset[MIRPlace]]] = {}
    for block in fn.blocks:
        if block.id not in incoming:
            continue
        state = incoming[block.id].copy()
        for index in range(len(block.statements) + 1):
            point = MIRPoint(block.id, index)
            points[point] = MappingProxyType(state.copy())
            live = {leaf: refs for leaf, refs in state.items() if leaf.root in liveness.points[point]}
            active[point] = MappingProxyType(live)
            inverse: dict[MIRReferent, set[MIRPlace]] = {}
            for leaf, refs in live.items():
                for ref in refs:
                    inverse.setdefault(ref, set()).add(leaf)
            holders[point] = MappingProxyType({ref: frozenset(owners) for ref, owners in inverse.items()})
            if index < len(block.statements):
                transfer(block.statements[index], state)
    entry = MappingProxyType({leaf: refs for leaf, refs in incoming[fn.entry].items()
                              if leaf.root in liveness.entry_live})
    return MIRDependencies(fn, MappingProxyType(points), MappingProxyType(active), MappingProxyType(holders), entry)


def dump_dependencies(result: MIRDependencies | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"dependencies not covered: {result.reason}\n"

    def format_refs(refs: MIRReferents) -> str:
        return "; ".join(f"{_place(leaf)} -> {{" + ", ".join(sorted(
            ("external:" if ref.external else "storage:") + _place(ref.place) for ref in values)) + "}"
            for leaf, values in sorted(refs.items(), key=lambda item: _place(item[0]))) or "{}"

    lines = ["dependencies (external origins may alias; no safety verdict)",
             "  entry active: " + format_refs(result.entry_active)]
    for point, refs in result.referents.items():
        lines.append(f"  bb{point.block.index} before {point.index}: {format_refs(refs)}")
        lines.append("    active: " + format_refs(result.active[point]))
    return "\n".join(lines) + "\n"
