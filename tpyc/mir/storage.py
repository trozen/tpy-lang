"""Positive record-write inventory, without physical lifetime-end claims."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .dump import _location, _place
from .liveness import MIRPoint
from .nodes import (
    MIRAssign, MIRConstruct, MIRDeref, MIRFunction, MIRNotCovered, MIRSlot, MIRSlotId,
    MIRRecordWrite, MIRTupleConstruct, MIRTupleIndex, MIRPlace, MIRValueKind,
)
from .validate import successors, validate_function


@dataclass(frozen=True)
class MIRStorageEvents:
    function: MIRFunction
    writes: Mapping[MIRPoint, MIRAssign]
    member_initializations: Mapping[MIRPoint, tuple[MIRPlace, ...]]


def analyze_storage(fn: MIRFunction) -> MIRStorageEvents | MIRNotCovered:
    validate_function(fn)
    blocks = {block.id: block for block in fn.blocks}
    reached = set()
    pending = [fn.entry]
    while pending:
        bid = pending.pop()
        if bid not in reached:
            reached.add(bid)
            pending.extend(successors(blocks[bid].terminator))
    slots = {slot.id: slot for slot in fn.slots}
    writes: dict[MIRPoint, MIRAssign] = {}
    members: dict[MIRPoint, tuple[MIRPlace, ...]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if not isinstance(stmt, MIRAssign):
                continue
            if isinstance(stmt.value, MIRTupleConstruct):
                initialized = tuple(MIRPlace(stmt.target.root, (MIRTupleIndex(i),))
                                    for i, element in enumerate(stmt.value.elements) if isinstance(element, MIRConstruct))
                if initialized and block.id in reached:
                    members[MIRPoint(block.id, index)] = initialized
            elif storage_destination(stmt.target, slots):
                if not isinstance(stmt.storage_write, MIRRecordWrite):
                    return MIRNotCovered(fn.id, "storage", "missing record write fact", stmt.loc)
                if block.id in reached:
                    writes[MIRPoint(block.id, index)] = stmt
    return MIRStorageEvents(fn, MappingProxyType(writes), MappingProxyType(members))


def storage_destination(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether a write to `place` replaces whole storage: an OWNED root, or
    the storage a borrowed holder points at (a trailing dereference). The
    write's event follows its destination, whatever produces the value."""
    if not place.projections:
        return slots[place.root].value_kind is MIRValueKind.OWNED
    return isinstance(place.projections[-1], MIRDeref)


def dump_storage(result: MIRStorageEvents | MIRNotCovered) -> str:
    if isinstance(result, MIRNotCovered):
        return f"storage not covered: {result.reason}\n"
    lines = ["record writes (logical replacement; no physical lifetime-end verdict)"]
    for point, stmt in result.writes.items():
        fact = stmt.storage_write
        assert isinstance(fact, MIRRecordWrite)
        owner = f" rebind-owner=%{fact.rebind_owner.index}" if fact.rebind_owner is not None else ""
        lines.append(f"  bb{point.block.index} before {point.index}: {fact.mode.name.lower()} "
                     f"{_place(stmt.target)}{owner}{_location(stmt.loc)}")
    for point, members in result.member_initializations.items():
        lines.append(f"  bb{point.block.index} before {point.index}: initialize-tuple-members "
                     + ", ".join(_place(member) for member in members))
    return "\n".join(lines) + "\n"
