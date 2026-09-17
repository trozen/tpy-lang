"""Structural, typing and definite-assignment checks for scalar MIR."""

from collections import deque

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, INT32_MAX, INT32_MIN, TpyType, VoidType
from .nodes import (
    MIRAssign, MIRBlockId, MIRBranch, MIRCompare, MIRConstant, MIRGoto,
    MIRFunction, MIRNot, MIRRead, MIRReturn, MIRSlotId, MIRSlotKind,
)


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def operands(value: MIRRead | MIRCompare | MIRNot | MIRConstant) -> tuple[MIRSlotId, ...]:
    if isinstance(value, MIRRead):
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
        _require(slot.type in (INT32, BOOL) and slot.form is Form.VALUE,
                 "unsupported slot type or form")
        _require(isinstance(slot.kind, MIRSlotKind), "invalid slot kind")

    def slot_type(slot: MIRSlotId) -> TpyType:
        _require(slot in slots, "undeclared operand or destination")
        return slots[slot].type

    pred: dict[MIRBlockId, set[MIRBlockId]] = {b: set() for b in blocks}
    for block in fn.blocks:
        _require(block.id.body == fn.id and block.id.index >= 0,
                 "foreign or invalid block ID")
        for stmt in block.statements:
            _require(isinstance(stmt, MIRAssign), "unknown instruction")
            target_type = slot_type(stmt.target)
            value = stmt.value
            for operand in operands(value):
                slot_type(operand)
            if isinstance(value, MIRConstant):
                _require((target_type == BOOL and type(value.value) is bool)
                         or (target_type == INT32 and type(value.value) is int
                             and INT32_MIN <= value.value <= INT32_MAX),
                         "constant type or range mismatch")
            elif isinstance(value, MIRRead):
                _require(target_type == slot_type(value.source), "read type mismatch")
            elif isinstance(value, MIRCompare):
                _require(value.op in ("==", "!=", "<", "<=", ">", ">="),
                         "unsupported comparison")
                _require(target_type == BOOL, "comparison result is not bool")
                _require(slot_type(value.left) == slot_type(value.right),
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
    writes = {bid: {s.target for s in blocks[bid].statements} for bid in reachable}
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
            _require(set(operands(stmt.value)) <= assigned, "read before definite assignment")
            assigned.add(stmt.target)
        term = block.terminator
        if isinstance(term, MIRBranch):
            _require(term.condition in assigned, "branch before definite assignment")
        elif isinstance(term, MIRReturn) and term.value is not None:
            _require(term.value in assigned, "return before definite assignment")
