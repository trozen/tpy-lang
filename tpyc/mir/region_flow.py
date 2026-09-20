"""Derived normal CFG region transitions, shared by the dataflow passes."""

from dataclasses import dataclass

from .nodes import (
    MIRBlockId, MIRBranch, MIREdge, MIRFunction, MIRGoto, MIRRegionId,
    MIRReturn, MIRSlotId, MIRTerminator,
    MIRStorageDuration,
)


def outgoing_edges(block: MIRBlockId, term: MIRTerminator) -> tuple[tuple[MIREdge, MIRBlockId | None], ...]:
    match term:
        case MIRGoto(target=target):
            return ((MIREdge(block), target),)
        case MIRBranch(then=yes, otherwise=no):
            return ((MIREdge(block, 0), yes), (MIREdge(block, 1), no))
        case MIRReturn():
            return ((MIREdge(block), None),)
        case _:
            raise ValueError("unknown region terminator")


@dataclass(frozen=True)
class MIRRegionTransition:
    target: MIRBlockId | None
    exited: tuple[MIRRegionId, ...]
    entered: tuple[MIRRegionId, ...]
    reset: frozenset[MIRSlotId]
    ended: frozenset[MIRSlotId]


class MIRRegionFlow:
    """Requires structurally validated region/slot identities."""

    def __init__(self, fn: MIRFunction) -> None:
        regions = {r.id: r for r in fn.regions}
        self.chains: dict[MIRRegionId | None, tuple[MIRRegionId, ...]] = {None: ()}
        for rid in regions:
            pending = []
            current = rid
            while current not in self.chains:
                pending.append(current)
                current = regions[current].parent
            for current in reversed(pending):
                self.chains[current] = (*self.chains[regions[current].parent], current)
        residents: dict[MIRRegionId, set[MIRSlotId]] = {r: set() for r in regions}
        storage: dict[MIRRegionId, set[MIRSlotId]] = {r: set() for r in regions}
        body = next((r.id for r in fn.regions if r.parent is None), None)
        for slot in fn.slots:
            if slot.residence is not None:
                residents[slot.residence].add(slot.id)
            if isinstance(slot.storage_duration, MIRRegionId):
                storage[slot.storage_duration].add(slot.id)
            elif slot.storage_duration is MIRStorageDuration.BODY and regions:
                storage[body].add(slot.id)
        blocks = {b.id: b for b in fn.blocks}
        self.edges: dict[MIREdge, MIRRegionTransition] = {}
        for block in fn.blocks:
            source = self.chains[block.region]
            for edge, target in outgoing_edges(block.id, block.terminator):
                dest = self.chains[blocks[target].region] if target is not None else ()
                common = 0
                while common < min(len(source), len(dest)) and source[common] == dest[common]:
                    common += 1
                exited, entered = tuple(reversed(source[common:])), dest[common:]
                reset = frozenset().union(*(residents[r] for r in (*exited, *entered)))
                ended = frozenset().union(*(storage[r] for r in exited))
                self.edges[edge] = MIRRegionTransition(target, exited, entered, reset, ended)
