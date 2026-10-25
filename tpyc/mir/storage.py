"""Positive inventory of every storage event, without physical lifetime-end claims."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .place_text import _location, _place
from .liveness import MIRPoint
from .nodes import MIRAssign, MIRFunction, MIRNotCovered, MIRPlace, MIRStorageWrite
from .prepare import MIRPrepared, validated_presence
from .reach import MIRStorageEvent, MIRStorageEventKind, storage_events
from .validate import reachable_blocks


@dataclass(frozen=True)
class MIRStorageEvents:
    function: MIRFunction
    # An assignment's own storage write: an initialization, a replacement,
    # an Optional assignment.
    writes: Mapping[MIRPoint, MIRAssign]
    # A call whose summary may write owned storage it is lent (an owned-leaf
    # field's buffer, a container's shape or elements) replaces it: one
    # replacement event per written place, at the call.
    call_writes: Mapping[MIRPoint, tuple[MIRPlace, ...]] = MappingProxyType({})
    # A move out of owned storage empties the source place: a replacement
    # event on it, at the move.
    moves: Mapping[MIRPoint, MIRPlace] = MappingProxyType({})
    # A wrapper constructed without an assignment: a scalar wrapper with its
    # default (`CONSTRUCT`) or an Optional record backing left empty (`BACKING`).
    constructions: Mapping[MIRPoint, MIRStorageEvent] = MappingProxyType({})
    # A scalar wrapper's value replaced in place: its inline payload ends.
    payload_writes: Mapping[MIRPoint, MIRAssign] = MappingProxyType({})


def analyze_storage(prepared: MIRPrepared) -> MIRStorageEvents | MIRNotCovered:
    """The inventory of every storage event of the body's reachable
    statements (`storage_events`), each kind kept: the dump's and the
    storage evidence's."""
    fn = prepared.function
    present = validated_presence(prepared).calls
    reached = reachable_blocks(fn)
    slots = {slot.id: slot for slot in fn.slots}
    writes: dict[MIRPoint, MIRAssign] = {}
    calls: dict[MIRPoint, tuple[MIRPlace, ...]] = {}
    moves: dict[MIRPoint, MIRPlace] = {}
    constructions: dict[MIRPoint, MIRStorageEvent] = {}
    payloads: dict[MIRPoint, MIRAssign] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            point = MIRPoint(block.id, index)
            called = present.get(point)
            events = storage_events(stmt, called.writes if called is not None else (), slots)
            if any(event.unknown_replacement for event in events):
                return MIRNotCovered(fn.id, "storage", "missing storage write fact", stmt.loc)
            if block.id not in reached:
                continue
            for event in events:
                match event.kind:
                    case MIRStorageEventKind.CALL_WRITE:
                        calls[point] = (*calls.get(point, ()), event.place)
                    case MIRStorageEventKind.MOVE:
                        moves[point] = event.place
                    case MIRStorageEventKind.CONSTRUCT | MIRStorageEventKind.BACKING:
                        constructions[point] = event
                    case MIRStorageEventKind.PAYLOAD if event.known:
                        assert isinstance(stmt, MIRAssign)
                        payloads[point] = stmt
                    case MIRStorageEventKind.PAYLOAD:
                        # Its fact missing: payload_lifetime refuses it.
                        pass
                    case _:
                        assert isinstance(stmt, MIRAssign)
                        writes[point] = stmt
    return MIRStorageEvents(fn, MappingProxyType(writes), MappingProxyType(calls),
                            MappingProxyType(moves), MappingProxyType(constructions), MappingProxyType(payloads))


def dump_storage(result: MIRStorageEvents | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"storage not covered: {result.reason}\n"
    lines = ["storage events (logical replacement; no physical lifetime-end verdict)"]
    for point, stmt in result.writes.items():
        fact = stmt.storage_write
        assert isinstance(fact, MIRStorageWrite)
        owner = f" rebind-owner=%{fact.rebind_owner.index}" if fact.rebind_owner is not None else ""
        lines.append(f"  bb{point.block.index} before {point.index}: {fact.mode.name.lower()} "
                     f"{_place(stmt.target)}{owner}{_location(stmt.loc)}")
    blocks = {block.id: block for block in result.function.blocks}
    for point, places in result.call_writes.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: call-write "
                     + ", ".join(_place(place) for place in places) + _location(loc))
    for point, place in result.moves.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: move-out {_place(place)}{_location(loc)}")
    for point, event in result.constructions.items():
        loc = blocks[point.block].statements[point.index].loc
        kind = "construct-backing" if event.kind is MIRStorageEventKind.BACKING else "construct-wrapper"
        lines.append(f"  bb{point.block.index} before {point.index}: {kind} {_place(event.place)}{_location(loc)}")
    for point, stmt in result.payload_writes.items():
        lines.append(f"  bb{point.block.index} before {point.index}: payload-write {_place(stmt.target)}"
                     f"{_location(stmt.loc)}")
    return "\n".join(lines) + "\n"
