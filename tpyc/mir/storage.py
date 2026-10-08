"""Positive record-write inventory, without physical lifetime-end claims."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..thir.scalar_leaves import storage_leaf, view_leaf
from ..typesys import unwrap_readonly
from .call_effects import call_write_places
from .coverage import moved_storage
from .dump import _location, _place
from .liveness import MIRPoint
from .nodes import (
    MIRAssign, MIRConstruct, MIRContainerElements, MIRContainerStructure, MIRDeref, MIRField, MIRFunction,
    MIRMove, MIRNotCovered, MIRSlot, MIRSlotId, MIRRecordWrite, MIRTupleConstruct, MIRTupleIndex, MIRPlace,
    MIRValueKind, statement_call,
)
from .validate import successors, validate_function


@dataclass(frozen=True)
class MIRStorageEvents:
    function: MIRFunction
    writes: Mapping[MIRPoint, MIRAssign]
    member_initializations: Mapping[MIRPoint, tuple[MIRPlace, ...]]
    # A call whose summary may write owned storage it is lent (an owned-leaf
    # field's buffer, a container's shape or elements) replaces it: one
    # replacement event per written place, at the call.
    call_writes: Mapping[MIRPoint, tuple[MIRPlace, ...]] = MappingProxyType({})
    # A move out of owned storage empties the source place: a replacement
    # event on it, at the move.
    moves: Mapping[MIRPoint, MIRPlace] = MappingProxyType({})


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
    calls: dict[MIRPoint, tuple[MIRPlace, ...]] = {}
    moves: dict[MIRPoint, MIRPlace] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if (call := statement_call(stmt)) is not None and block.id in reached:
                replaced = tuple(place for place in call_write_places(call, slots)
                                 if storage_destination(place, slots))
                if replaced:
                    calls[MIRPoint(block.id, index)] = replaced
            if not isinstance(stmt, MIRAssign):
                continue
            if isinstance(stmt.value, MIRMove) and moved_storage(slots[stmt.value.source]) and block.id in reached:
                moves[MIRPoint(block.id, index)] = MIRPlace(stmt.value.source)
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
    return MIRStorageEvents(fn, MappingProxyType(writes), MappingProxyType(members), MappingProxyType(calls),
                            MappingProxyType(moves))


def owned_field(field: MIRField) -> bool:
    """A field whose storage its record owns and a write replaces in place:
    an owned leaf's buffer, a container, or an inline record member -- every
    modeled field but a scalar leaf, which holds no loan, and a view member,
    whose write rebinds the loan the record stores and replaces no storage."""
    bare = unwrap_readonly(field.type)
    return not storage_leaf(bare) and not view_leaf(bare)


def storage_destination(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> bool:
    """Whether a write to `place` replaces storage a borrow can point into:
    an OWNED root, the storage a borrowed holder points at (a trailing
    dereference), an owned-leaf, container or record member field, or a
    container's shape or elements region. The write's event follows its destination, whatever
    produces the value; a scalar field is overwritten, never a storage a
    borrow can point into."""
    if not place.projections:
        return slots[place.root].value_kind is MIRValueKind.OWNED
    last = place.projections[-1]
    return (isinstance(last, (MIRDeref, MIRContainerStructure, MIRContainerElements))
            or isinstance(last, MIRField) and owned_field(last))


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
    blocks = {block.id: block for block in result.function.blocks}
    for point, places in result.call_writes.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: call-write "
                     + ", ".join(_place(place) for place in places) + _location(loc))
    for point, place in result.moves.items():
        loc = blocks[point.block].statements[point.index].loc
        lines.append(f"  bb{point.block.index} before {point.index}: move-out {_place(place)}{_location(loc)}")
    return "\n".join(lines) + "\n"
