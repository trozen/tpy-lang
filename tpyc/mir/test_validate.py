"""Malformed graph tests pin typing and loop-sensitive definite assignment."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRCompare,
    MIRConstant, MIRGoto, MIRFunction, MIRNot, MIRRead, MIRReturn, MIRSlot,
    MIRSlotId, MIRSlotKind,
)
from .validate import MIRValidationError, validate_function

B = MIRBodyId("test", "f")
P, X, Y = (MIRSlotId(B, i) for i in range(3))
A, C, D, E = (MIRBlockId(B, i) for i in range(4))
SLOTS = (MIRSlot(P, BOOL, MIRSlotKind.PARAMETER, "flag"),
         MIRSlot(X, INT32, MIRSlotKind.LOCAL, "x"), MIRSlot(Y, INT32, MIRSlotKind.TEMPORARY))
GOOD = MIRFunction(B, INT32, SLOTS,
                   (MIRBlock(A, (MIRAssign(X, MIRConstant(1)), MIRAssign(Y, MIRRead(X))), MIRReturn(Y)),), A)


def invalid(fn: MIRFunction, message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


@pytest.mark.parametrize("change,message", [
    ({"slots": (*SLOTS, SLOTS[0])}, "duplicate slot"),
    ({"blocks": (*GOOD.blocks, GOOD.blocks[0])}, "duplicate block"),
    ({"entry": C}, "missing entry"),
    ({"slots": (replace(SLOTS[0], form=Form.BORROW), *SLOTS[1:])}, "slot type or form"),
    ({"slots": (replace(SLOTS[0], kind="local"), *SLOTS[1:])}, "slot kind"),
    ({"blocks": (MIRBlock(A, (), MIRGoto(C)),)}, "invalid block target"),
    ({"blocks": (MIRBlock(A, (), None),)}, "terminator"),
    ({"blocks": (MIRBlock(A, (), MIRReturn()),)}, "missing return value"),
    ({"blocks": (MIRBlock(A, (), MIRReturn(P)),)}, "return type mismatch"),
    ({"blocks": (MIRBlock(A, (), MIRReturn(Y)),)}, "definite assignment"),
    ({"blocks": (MIRBlock(A, (MIRAssign(X, MIRConstant(True)),), MIRReturn(X)),)}, "constant type"),
    ({"blocks": (MIRBlock(A, (MIRAssign(X, MIRConstant(2**31)),), MIRReturn(X)),)}, "constant type"),
    ({"blocks": (MIRBlock(A, (MIRAssign(X, MIRRead(P)),), MIRReturn(X)),)}, "read type"),
    ({"blocks": (MIRBlock(A, (MIRAssign(X, MIRNot(P)),), MIRReturn(X)),)}, "not operand or result"),
    ({"blocks": (MIRBlock(A, (MIRAssign(P, MIRCompare("+", P, P)),), MIRReturn(X)),)}, "comparison"),
    ({"blocks": (MIRBlock(A, (MIRAssign(P, MIRRead(MIRSlotId(B, 99))),), MIRReturn(X)),)}, "undeclared"),
    ({"blocks": (MIRBlock(A, (MIRAssign(MIRSlotId(B, 99), MIRConstant(1)),), MIRReturn(X)),)}, "undeclared"),
    ({"blocks": (MIRBlock(A, (), MIRBranch(X, A, A)),)}, "branch condition"),
])
def test_malformed_graphs(change: dict[str, object], message: str) -> None:
    invalid(replace(GOOD, **change), message)


def test_merges_intersect_predecessor_assignments() -> None:
    blocks = (MIRBlock(A, (), MIRBranch(P, C, D)),
              MIRBlock(C, (MIRAssign(X, MIRConstant(1)),), MIRGoto(E)),
              MIRBlock(D, (), MIRGoto(E)), MIRBlock(E, (), MIRReturn(X)))
    invalid(replace(GOOD, blocks=blocks), "definite assignment")
    fixed = (*blocks[:2], replace(blocks[2], statements=(MIRAssign(X, MIRConstant(2)),)), blocks[3])
    validate_function(replace(GOOD, blocks=fixed))


def test_loop_backedge_cannot_initialize_first_iteration() -> None:
    blocks = (MIRBlock(A, (), MIRGoto(C)),
              MIRBlock(C, (MIRAssign(Y, MIRRead(X)), MIRAssign(X, MIRConstant(1))), MIRBranch(P, C, D)),
              MIRBlock(D, (), MIRReturn(Y)))
    invalid(replace(GOOD, blocks=blocks), "definite assignment")
    validate_function(replace(GOOD, blocks=(replace(blocks[0], statements=(MIRAssign(X, MIRConstant(0)),)),
                                           *blocks[1:])))


def test_entry_backedge_keeps_parameter_only_boundary() -> None:
    fn = replace(GOOD, blocks=(MIRBlock(A, (MIRAssign(Y, MIRRead(X)), MIRAssign(X, MIRConstant(1))), MIRGoto(A)),))
    invalid(fn, "definite assignment")


def test_unreachable_predecessor_does_not_poison_definite_assignment() -> None:
    blocks = (MIRBlock(A, (MIRAssign(X, MIRConstant(1)),), MIRGoto(C)),
              MIRBlock(C, (), MIRReturn(X)), MIRBlock(D, (), MIRGoto(C)))
    validate_function(replace(GOOD, blocks=blocks))


def test_foreign_body_ids_do_not_alias_same_index() -> None:
    foreign = MIRBodyId("other", "f")
    invalid(replace(GOOD, slots=(replace(SLOTS[0], id=MIRSlotId(foreign, 0)), *SLOTS[1:])), "foreign")
    invalid(replace(GOOD, blocks=(MIRBlock(MIRBlockId(foreign, 1), (), MIRReturn()), *GOOD.blocks)), "foreign")
