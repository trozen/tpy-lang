"""Structural, typing and definite-assignment checks for MIR holders and places."""

from collections import deque

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, INT32_MAX, INT32_MIN, NominalType, TpyType, VoidType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlockId, MIRBranch, MIRCompare, MIRConstant, MIRDeref,
    MIRField, MIRFieldId, MIRGoto, MIRFunction, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRRvalue, MIRSlotId, MIRSlotKind, MIRValueKind,
)


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def operands(value: MIRRvalue) -> tuple[MIRSlotId, ...]:
    if isinstance(value, MIRRead):
        _require(isinstance(value.source, MIRPlace), "invalid read place")
        return (value.source.root,)
    if isinstance(value, MIRAlias):
        return (value.source,)
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


def validate_function(fn: MIRFunction) -> None:
    _require(bool(fn.id.module and fn.id.declaration), "empty body identity")
    _require(fn.return_type in (INT32, BOOL) or isinstance(fn.return_type, VoidType),
             "unsupported return type")
    slots = {s.id: s for s in fn.slots}
    blocks = {b.id: b for b in fn.blocks}
    _require(len(slots) == len(fn.slots), "duplicate slot ID")
    _require(len(blocks) == len(fn.blocks), "duplicate block ID")
    _require(fn.entry in blocks, "missing entry block")
    for slot in fn.slots:
        _require(slot.id.body == fn.id and slot.id.index >= 0, "foreign or invalid slot ID")
        if slot.value_kind is MIRValueKind.SCALAR:
            _require(slot.type in (INT32, BOOL) and slot.form is Form.VALUE and not slot.readonly,
                     "unsupported slot type or form")
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

    field_types: dict[MIRFieldId, TpyType] = {}

    def place_type(place: MIRPlace, *, write: bool = False) -> TpyType:
        _require(isinstance(place, MIRPlace), "invalid place")
        typ = slot_type(place.root)
        if not place.projections:
            return typ
        _require(len(place.projections) == 2
                 and isinstance(place.projections[0], MIRDeref)
                 and isinstance(place.projections[1], MIRField), "unsupported place projections")
        slot = slots[place.root]
        member = place.projections[1]
        _require(slot.value_kind is MIRValueKind.BORROWED_RECORD, "dereference needs reference holder")
        _require(isinstance(member.id, MIRFieldId) and member.id.owner == typ
                 and bool(member.id.name), "field owner mismatch")
        _require(member.type in (BOOL, INT32), "unsupported field type")
        _require(field_types.setdefault(member.id, member.type) == member.type,
                 "inconsistent field type")
        _require(not (write and slot.readonly), "store through readonly reference")
        return member.type

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, MIRAssign), "unknown instruction")
            target_type = place_type(stmt.target, write=True)
            value = stmt.value
            for operand in operands(value):
                slot_type(operand)
            if isinstance(value, MIRConstant):
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
