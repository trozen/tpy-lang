"""Storage construction is complete, typed, single-execution and definitely assigned."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, NominalType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch,
    MIRConstant, MIRConstruct, MIRCopy, MIRDeref, MIRField, MIRFieldId,
    MIRFunction, MIRGoto, MIRMove, MIRPlace, MIRRead, MIRRecordLayout, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
)
from .testutil import execute
from .validate import MIRValidationError, validate_function

B = MIRBodyId("owned", "verify")
CELL = NominalType("Cell", _module_qname="owned.Cell")
N, S, P, T, Q, RESULT, FLAG = (MIRSlotId(B, i) for i in range(7))
A, C, D, E = (MIRBlockId(B, i) for i in range(4))
MEMBER = MIRField(MIRFieldId(CELL, "value"), INT32)
LAYOUT = MIRRecordLayout(CELL, (MEMBER,), True, True)
SLOTS = (
    MIRSlot(N, INT32, MIRSlotKind.LOCAL),
    MIRSlot(S, CELL, MIRSlotKind.TEMPORARY, form=Form.STORAGE, value_kind=MIRValueKind.RECORD_STORAGE),
    MIRSlot(P, CELL, MIRSlotKind.LOCAL, form=Form.BORROW, value_kind=MIRValueKind.BORROWED_RECORD),
    MIRSlot(T, CELL, MIRSlotKind.TEMPORARY, form=Form.STORAGE, value_kind=MIRValueKind.RECORD_STORAGE),
    MIRSlot(Q, CELL, MIRSlotKind.LOCAL, form=Form.BORROW, value_kind=MIRValueKind.BORROWED_RECORD),
    MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL),
    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER),
)
NUMBER = MIRAssign(MIRPlace(N), MIRConstant(1))
INIT = MIRAssign(MIRPlace(S), MIRConstruct((N,)))
BORROW = MIRAssign(MIRPlace(P), MIRBorrow(S))
COPY = MIRAssign(MIRPlace(T), MIRCopy(MIRPlace(P, (MIRDeref(),))))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(P, (MIRDeref(), MEMBER))))
GOOD = MIRFunction(B, INT32, SLOTS,
                   (MIRBlock(A, (NUMBER, INIT, BORROW, COPY, READ), MIRReturn(RESULT)),), A, (LAYOUT,))


def invalid(fn: MIRFunction, message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


@pytest.mark.parametrize("stmt,message", [
    (MIRAssign(MIRPlace(T), MIRRead(MIRPlace(S))), "read type mismatch"),
    (MIRAssign(MIRPlace(T), MIRAlias(P)), "alias type mismatch"),
    (MIRAssign(MIRPlace(T), MIRConstruct(())), "incomplete or mistyped"),
    (MIRAssign(MIRPlace(T), MIRConstruct((FLAG,))), "incomplete or mistyped"),
    (MIRAssign(MIRPlace(T), MIRCopy(MIRPlace(P))), "copy source"),
    (MIRAssign(MIRPlace(T), MIRMove(P)), "move source"),
    (MIRAssign(MIRPlace(Q), MIRBorrow(P)), "borrow type"),
    (MIRAssign(MIRPlace(T), MIRBorrow(S)), "borrow type"),
    (MIRAssign(MIRPlace(P), MIRConstruct((N,))), "record destination"),
    (MIRAssign(MIRPlace(P, (MIRDeref(),)), MIRCopy(MIRPlace(S))), "record replacement"),
    (MIRAssign(MIRPlace(S), MIRMove(S)), "repeated storage initialization"),
])
def test_categories_do_not_conflate_storage_holders_and_values(stmt: MIRAssign, message: str) -> None:
    invalid(replace(GOOD, blocks=(MIRBlock(A, (NUMBER, INIT, BORROW, stmt, READ), MIRReturn(RESULT)),)), message)


def test_borrow_and_copy_require_initialized_storage() -> None:
    invalid(replace(GOOD, blocks=(MIRBlock(A, (NUMBER, BORROW, INIT, READ), MIRReturn(RESULT)),)),
            "read before definite assignment")
    direct_copy = MIRAssign(MIRPlace(T), MIRCopy(MIRPlace(S)))
    invalid(replace(GOOD, blocks=(MIRBlock(A, (NUMBER, direct_copy, INIT, BORROW, READ),
                                          MIRReturn(RESULT)),)), "read before definite assignment")
    field_store = MIRAssign(MIRPlace(S, (MEMBER,)), MIRConstant(2))
    invalid(replace(GOOD, blocks=(MIRBlock(A, (NUMBER, field_store, BORROW, READ), MIRReturn(RESULT)),)),
            "read before definite assignment")


def test_definite_assignment_at_join_and_single_execution_cycles() -> None:
    blocks = (MIRBlock(A, (NUMBER,), MIRBranch(FLAG, C, D)),
              MIRBlock(C, (INIT,), MIRGoto(E)), MIRBlock(D, (), MIRGoto(E)),
              MIRBlock(E, (BORROW, READ), MIRReturn(RESULT)))
    invalid(replace(GOOD, blocks=blocks), "read before definite assignment")
    cycle = (MIRBlock(A, (NUMBER,), MIRGoto(C)),
             MIRBlock(C, (INIT, BORROW), MIRBranch(FLAG, C, D)),
             MIRBlock(D, (READ,), MIRReturn(RESULT)))
    invalid(replace(GOOD, blocks=cycle), "owning operation in cycle")


def test_layout_and_eligibility_are_required() -> None:
    invalid(replace(GOOD, records=()), "record storage type")
    invalid(replace(GOOD, records=(LAYOUT, LAYOUT)), "duplicate record layout")
    invalid(replace(GOOD, records=(replace(LAYOUT, fields=(MEMBER, MEMBER)),)), "layout field")
    invalid(replace(GOOD, records=(replace(LAYOUT, copyable=False),)), "copy source or eligibility")
    moved = replace(COPY, value=MIRMove(S))
    fn = replace(GOOD, blocks=(MIRBlock(A, (NUMBER, INIT, BORROW, moved, READ), MIRReturn(RESULT)),))
    validate_function(fn)
    invalid(replace(fn, records=(replace(LAYOUT, movable=False),)), "move source or eligibility")
    invalid(replace(fn, slots=(SLOTS[0], replace(SLOTS[1], readonly=True), *SLOTS[2:])),
            "borrow increases access")
    invalid(replace(GOOD, slots=(SLOTS[0], replace(SLOTS[1], kind=MIRSlotKind.PARAMETER), *SLOTS[2:])),
            "record storage type")


def test_replacement_preserves_identity_and_reads_old_payload_first() -> None:
    alias = MIRAssign(MIRPlace(Q), MIRAlias(P))
    replacement = MIRAssign(MIRPlace(P, (MIRDeref(),)), MIRConstruct((RESULT,)))
    mutate = MIRAssign(MIRPlace(Q, (MIRDeref(), MEMBER)), MIRConstant(9))
    snapshot = replace(GOOD, blocks=(MIRBlock(A, (NUMBER, INIT, BORROW, READ, replacement, READ),
                                              MIRReturn(RESULT)),))
    validate_function(snapshot)
    assert execute(snapshot, False) == 1
    fn = replace(GOOD, blocks=(MIRBlock(A, (NUMBER, INIT, BORROW, alias, READ,
                                           replacement, mutate, READ), MIRReturn(RESULT)),))
    validate_function(fn)
    heap = {}
    assert execute(fn, False, heap=heap) == 9
    assert len(heap) == 1
    invalid(replace(fn, slots=(*SLOTS[:2], replace(SLOTS[2], readonly=True), *SLOTS[3:])),
            "alias increases access")
