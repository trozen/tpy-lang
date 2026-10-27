"""Conservative holder liveness over validated MIR, without move decisions."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .nodes import (
    MIRStatement, MIRBlockId, MIRBranch, MIRFunction, MIRGoto, MIRReturn,
    MIRPoint, MIRSlotId, MIRTerminator,
)
from .validate import reachable_blocks, source_definition, statement_reads, successors
from .value import MIRValidationError


@dataclass(frozen=True)
class MIRLiveness:
    function: MIRFunction
    live_in: Mapping[MIRBlockId, frozenset[MIRSlotId]]
    live_out: Mapping[MIRBlockId, frozenset[MIRSlotId]]
    # Index i is before statement i; len(statements) is before the terminator.
    points: Mapping[MIRPoint, frozenset[MIRSlotId]]
    entry_live: frozenset[MIRSlotId]


def _terminator_uses(term: MIRTerminator) -> frozenset[MIRSlotId]:
    match term:
        case MIRBranch():
            return frozenset({term.condition})
        case MIRReturn():
            return frozenset() if term.value is None else frozenset({term.value})
        case MIRGoto():
            return frozenset()
        case _:
            raise MIRValidationError("unknown liveness terminator")


def _liveness(fn: MIRFunction) -> MIRLiveness:
    """Holder liveness of a body, structural and backward: it reads no
    presence fact, so the one its preparation computes beside the phase-1
    presence (`prepare.prepare`) is the one every later pass reads."""
    blocks = {b.id: b for b in fn.blocks}
    reachable = reachable_blocks(fn)
    order = [b.id for b in fn.blocks if b.id in reachable]
    pred: dict[MIRBlockId, list[MIRBlockId]] = {bid: [] for bid in order}
    for bid in order:
        for dest in successors(blocks[bid].terminator):
            pred[dest].append(bid)
    alias_sources = {s.id: s.alias_source.root for s in fn.slots if s.alias_source is not None}

    def close(live: frozenset[MIRSlotId]) -> frozenset[MIRSlotId]:
        result = set(live)
        pending = list(live.intersection(alias_sources))
        while pending:
            root = alias_sources[pending.pop()]
            if root not in result:
                result.add(root)
                if root in alias_sources:
                    pending.append(root)
        return frozenset(result)

    def before(stmt: MIRStatement, after: frozenset[MIRSlotId]) -> frozenset[MIRSlotId]:
        definition = source_definition(stmt)
        return close((after - {definition}) | frozenset(statement_reads(stmt)))

    incoming = {bid: frozenset() for bid in order}
    outgoing = incoming.copy()
    work = deque(reversed(order))
    queued = set(order)
    while work:
        bid = work.popleft()
        queued.remove(bid)
        block = blocks[bid]
        out = frozenset().union(*(incoming[s] for s in successors(block.terminator)))
        live = close(out | _terminator_uses(block.terminator))
        for stmt in reversed(block.statements):
            live = before(stmt, live)
        outgoing[bid] = out
        if incoming[bid] != live:
            incoming[bid] = live
            for source in pred[bid]:
                if source not in queued:
                    queued.add(source)
                    work.append(source)

    points: dict[MIRPoint, frozenset[MIRSlotId]] = {}
    for bid in order:
        block = blocks[bid]
        live = close(outgoing[bid] | _terminator_uses(block.terminator))
        points[MIRPoint(bid, len(block.statements))] = live
        for index in range(len(block.statements) - 1, -1, -1):
            live = before(block.statements[index], live)
            points[MIRPoint(bid, index)] = live
    entry = incoming[fn.entry]
    return MIRLiveness(fn, MappingProxyType(incoming), MappingProxyType(outgoing),
                       MappingProxyType(points), entry)


def dump_liveness(result: MIRLiveness) -> str:
    def slots(live: frozenset[MIRSlotId]) -> str:
        return "{" + ", ".join(f"%{s.index}" for s in sorted(live, key=lambda s: s.index)) + "}"

    lines = [f"liveness entry: {slots(result.entry_live)}"]
    for block in result.function.blocks:
        if block.id not in result.live_in:
            continue
        lines.append(f"  bb{block.id.index} in={slots(result.live_in[block.id])} "
                     f"out={slots(result.live_out[block.id])}")
        for index in range(len(block.statements) + 1):
            lines.append(f"    before {index}: {slots(result.points[MIRPoint(block.id, index)])}")
    return "\n".join(lines) + "\n"
