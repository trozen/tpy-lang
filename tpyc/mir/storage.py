"""Positive record-write inventory, without physical lifetime-end claims."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .dump import _location, _place
from .liveness import MIRPoint
from .nodes import MIRAssign, MIRConstruct, MIRCopy, MIRFunction, MIRMove, MIRNotCovered, MIRRecordWrite, MIRStorageInit
from .validate import successors, validate_function


@dataclass(frozen=True)
class MIRStorageEvents:
    function: MIRFunction
    writes: Mapping[MIRPoint, MIRAssign]


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
    writes: dict[MIRPoint, MIRAssign] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRStorageInit):
                continue
            match stmt.value:
                case MIRConstruct() | MIRCopy() | MIRMove():
                    if not isinstance(stmt.storage_write, MIRRecordWrite):
                        return MIRNotCovered(fn.id, "storage", "missing record write fact", stmt.loc)
                    if block.id in reached:
                        writes[MIRPoint(block.id, index)] = stmt
    return MIRStorageEvents(fn, MappingProxyType(writes))


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
    return "\n".join(lines) + "\n"
