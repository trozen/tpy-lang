"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, INT32_MAX, INT32_MIN, NominalType, TpyType, VoidType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFieldId, MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRRvalue, MIRSlotId, MIRSlotKind, MIRValueKind,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove,
)


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def operands(value: MIRRvalue) -> tuple[MIRSlotId, ...]:
    if isinstance(value, (MIRRead, MIRCopy)):
        _require(isinstance(value.source, MIRPlace), "invalid read place")
        return (value.source.root,)
    if isinstance(value, (MIRAlias, MIRBorrow, MIRMove)):
        return (value.source,)
    if isinstance(value, MIRConstruct):
        return value.fields
    if isinstance(value, MIRCompare):
        return (value.left, value.right)
    if isinstance(value, MIRNot):
        return (value.operand,)
    if isinstance(value, MIRConstant):
        return ()
    raise MIRValidationError("unknown rvalue")


def successors(term: MIRGoto | MIRBranch | MIRReturn) -> tuple[MIRBlockId, ...]:
    if isinstance(term, MIRGoto):
        return (term.target,)
    if isinstance(term, MIRBranch):
        return (term.then, term.otherwise)
    if isinstance(term, MIRReturn):
        return ()
    raise MIRValidationError("missing or unknown terminator")


def _cyclic_blocks(blocks: dict[MIRBlockId, MIRBlock],
                   pred: dict[MIRBlockId, set[MIRBlockId]]) -> set[MIRBlockId]:
    """Two iterative DFS passes keep storage-site checks linear in CFG size."""
    visited: set[MIRBlockId] = set()
    order: list[MIRBlockId] = []
    for root in blocks:
        pending = [(root, False)]
        while pending:
            bid, finished = pending.pop()
            if finished:
                order.append(bid)
            elif bid not in visited:
                visited.add(bid)
                pending.append((bid, True))
                pending.extend((s, False) for s in successors(blocks[bid].terminator))
    visited.clear()
    cyclic: set[MIRBlockId] = set()
    for root in reversed(order):
        if root in visited:
            continue
        component: set[MIRBlockId] = set()
        pending_ids = [root]
        while pending_ids:
            bid = pending_ids.pop()
            if bid not in visited:
                visited.add(bid)
                component.add(bid)
                pending_ids.extend(pred[bid])
        if len(component) > 1 or root in pred[root]:
            cyclic.update(component)
    return cyclic


def validate_function(fn: MIRFunction) -> None:
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(fn.return_type in (INT32, BOOL) or isinstance(fn.return_type, VoidType),
             "unsupported return type")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    records = {r.type: r for r in fn.records}
    _require(len(records) == len(fn.records), "duplicate record layout")
    field_types: dict[MIRFieldId, TpyType] = {}
    for record in fn.records:
        _require(isinstance(record.type, NominalType) and record.type.qualified_name() is not None
                 and record.type not in (BOOL, INT32) and not record.type.type_args
                 and not record.type.is_protocol, "unsupported record layout identity")
        _require(type(record.copyable) is bool and type(record.movable) is bool,
                 "invalid record eligibility")
        seen: set[MIRFieldId] = set()
        for member in record.fields:
            _require(isinstance(member, MIRField) and isinstance(member.id, MIRFieldId)
                     and member.id.owner == record.type and bool(member.id.name)
                     and member.id not in seen and member.type in (BOOL, INT32),
                     "invalid record layout field")
            seen.add(member.id)
            field_types[member.id] = member.type
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        if slot.value_kind is MIRValueKind.SCALAR:
            _require(slot.type in (INT32, BOOL) and slot.form is Form.VALUE and not slot.readonly,
                     "unsupported slot type or form")
        elif slot.value_kind is MIRValueKind.RECORD_STORAGE:
            _require(slot.type in records and slot.form is Form.STORAGE
                     and slot.kind is not MIRSlotKind.PARAMETER,
                     "unsupported record storage type or form")
        else:
            _require(slot.value_kind is MIRValueKind.BORROWED_RECORD
                     and isinstance(slot.type, NominalType) and slot.type.qualified_name() is not None
                     and slot.type not in (BOOL, INT32)
                     and not slot.type.type_args and not slot.type.is_protocol
                     and slot.form is Form.BORROW, "unsupported reference slot type or form")
        _require(type(slot.readonly) is bool, "invalid access capability")
        _require(isinstance(slot.kind, MIRSlotKind), "invalid slot kind")

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    def place_type(place: MIRPlace, *, write: bool = False) -> TpyType:
        _require(isinstance(place, MIRPlace), "invalid place")
        typ = slot_type(place.root)
        if not place.projections:
            return typ
        slot = slots[place.root]
        projections = place.projections
        if isinstance(projections[0], MIRDeref):
            _require(slot.value_kind is MIRValueKind.BORROWED_RECORD, "dereference needs reference holder")
            _require(not (write and slot.readonly), "store through readonly reference")
            projections = projections[1:]
            if not projections:
                _require(typ in records, "record place needs layout")
                return typ
        else:
            _require(slot.value_kind is MIRValueKind.RECORD_STORAGE, "field needs record storage")
            _require(not (write and slot.readonly), "store through readonly storage")
        _require(len(projections) == 1 and isinstance(projections[0], MIRField),
                 "unsupported place projections")
        member = projections[0]
        _require(isinstance(member.id, MIRFieldId) and member.id.owner == typ
                 and bool(member.id.name), "field owner mismatch")
        _require(member.type in (BOOL, INT32), "unsupported field type")
        if typ in records:
            _require(member.id in field_types, "field missing from record layout")
        _require(field_types.setdefault(member.id, member.type) == member.type,
                 "inconsistent field type")
        return member.type

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    initialized_storage: set[MIRSlotId] = set()
    owning_blocks: set[MIRBlockId] = set()
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, MIRAssign), "unknown instruction")
            target_type = place_type(stmt.target, write=True)
            value = stmt.value
            for operand in operands(value):
                slot_type(operand)
            if isinstance(value, (MIRConstruct, MIRCopy, MIRMove)):
                owning_blocks.add(block.id)
                target = slots[stmt.target.root]
                _require(target_type in records and (
                    (not stmt.target.projections and target.value_kind is MIRValueKind.RECORD_STORAGE)
                    or stmt.target.projections == (MIRDeref(),)), "record destination type")
                if not stmt.target.projections:
                    _require(stmt.target.root not in initialized_storage, "repeated storage initialization")
                    initialized_storage.add(stmt.target.root)
                else:
                    _require(isinstance(value, MIRConstruct) and records[target_type].movable,
                             "unsupported record replacement")
                if isinstance(value, MIRConstruct):
                    members = records[target_type].fields
                    _require(len(value.fields) == len(members) and all(
                        slot_type(src) == member.type for src, member in zip(value.fields, members)),
                        "incomplete or mistyped record construction")
                elif isinstance(value, MIRCopy):
                    _require(records[target_type].copyable and place_type(value.source) == target_type
                             and (value.source.projections == (MIRDeref(),)
                                  or (not value.source.projections
                                      and slots[value.source.root].value_kind is MIRValueKind.RECORD_STORAGE)),
                             "record copy source or eligibility")
                else:
                    source = slots[value.source]
                    _require(source.value_kind is MIRValueKind.RECORD_STORAGE
                             and source.type == target_type and not source.readonly
                             and records[target_type].movable and value.source != stmt.target.root,
                             "record move source or eligibility")
            elif isinstance(value, MIRBorrow):
                target, source = slots[stmt.target.root], slots[value.source]
                _require(not stmt.target.projections and target.value_kind is MIRValueKind.BORROWED_RECORD
                         and target.kind is not MIRSlotKind.PARAMETER
                         and source.value_kind is MIRValueKind.RECORD_STORAGE
                         and target_type == source.type, "storage borrow type mismatch")
                _require(not source.readonly or target.readonly, "borrow increases access")
            elif isinstance(value, MIRConstant):
                _require((target_type == BOOL and type(value.value) is bool)
                         or (target_type == INT32 and type(value.value) is int
                             and INT32_MIN <= value.value <= INT32_MAX),
                         "constant type or range mismatch")
            elif isinstance(value, MIRRead):
                _require(target_type in (BOOL, INT32)
                         and target_type == place_type(value.source), "read type mismatch")
            elif isinstance(value, MIRAlias):
                target = slots[stmt.target.root]
                source = slots[value.source]
                _require(not stmt.target.projections
                         and target.value_kind is MIRValueKind.BORROWED_RECORD
                         and source.value_kind is MIRValueKind.BORROWED_RECORD
                         and target_type == source.type, "alias type mismatch")
                _require(target.kind is not MIRSlotKind.PARAMETER, "reference parameter reseat")
                _require(not source.readonly or target.readonly, "alias increases access")
            elif isinstance(value, MIRCompare):
                _require(value.op in ("==", "!=", "<", "<=", ">", ">="),
                         "unsupported comparison")
                _require(target_type == BOOL, "comparison result is not bool")
                _require(slot_type(value.left) in (BOOL, INT32)
                         and slot_type(value.left) == slot_type(value.right),
                         "comparison operand type mismatch")
            elif isinstance(value, MIRNot):
                _require(target_type == BOOL and slot_type(value.operand) == BOOL,
                         "not operand or result is not bool")
        term = block.terminator
        for successor in successors(term):
            _require(successor in blocks, "invalid block target")
            pred[successor].add(block.id)
        if isinstance(term, MIRBranch):
            _require(slot_type(term.condition) == BOOL, "branch condition is not bool")
        elif isinstance(term, MIRReturn):
            if term.value is None:
                _require(isinstance(fn.return_type, VoidType), "missing return value")
            else:
                _require(slot_type(term.value) == fn.return_type, "return type mismatch")

    _require(not owning_blocks.intersection(_cyclic_blocks(blocks, pred)), "owning operation in cycle")

    reachable: set[MIRBlockId] = set()
    pending = [fn.entry]
    while pending:
        bid = pending.pop()
        if bid not in reachable:
            reachable.add(bid)
            pending.extend(successors(blocks[bid].terminator))
    parameters = {s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    writes = {bid: {s.target.root for s in blocks[bid].statements if not s.target.projections}
              for bid in reachable}
    # Intersection is a must analysis; initialize at top, with a synthetic
    # parameter-only incoming edge at entry even if the entry has a back edge.
    incoming = {bid: set(slots) for bid in reachable}
    outgoing = {bid: set(slots) for bid in reachable}
    work = deque(reachable)
    queued = set(reachable)
    while work:
        bid = work.popleft()
        queued.remove(bid)
        sources = [outgoing[p] for p in pred[bid] if p in reachable]
        if bid == fn.entry:
            sources.append(parameters)
        new_in = set.intersection(*sources)
        new_out = new_in | writes[bid]
        incoming[bid] = new_in
        if new_out != outgoing[bid]:
            outgoing[bid] = new_out
            for target in successors(blocks[bid].terminator):
                if target not in queued:
                    work.append(target)
                    queued.add(target)
    for bid in reachable:
        assigned = incoming[bid].copy()
        block = blocks[bid]
        for stmt in block.statements:
            reads = set(operands(stmt.value))
            if stmt.target.projections:
                reads.add(stmt.target.root)
            _require(reads <= assigned, "read before definite assignment")
            if not stmt.target.projections:
                assigned.add(stmt.target.root)
        term = block.terminator
        if isinstance(term, MIRBranch):
            _require(term.condition in assigned, "branch before definite assignment")
        elif isinstance(term, MIRReturn) and term.value is not None:
            _require(term.value in assigned, "return before definite assignment")
