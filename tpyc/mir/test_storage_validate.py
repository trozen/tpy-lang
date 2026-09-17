"""Reference holders cannot be confused with scalar or initialized storage."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, NominalType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch,
    MIRConstant, MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRGoto,
    MIRPlace, MIRRead, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
)
from .validate import MIRValidationError, validate_function

B = MIRBodyId("test", "storage")
CELL = NominalType("Cell", _module_qname="test.Cell")
OTHER = NominalType("Cell", _module_qname="other.Cell")
P, Q, X, Y, FLAG = (MIRSlotId(B, i) for i in range(5))
A, C, D, E = (MIRBlockId(B, i) for i in range(4))
MEMBER = MIRField(MIRFieldId(CELL, "value"), INT32)


def field(root: MIRSlotId) -> MIRPlace:
    return MIRPlace(root, (MIRDeref(), MEMBER))


SLOTS = (
    MIRSlot(P, CELL, MIRSlotKind.PARAMETER, form=Form.BORROW,
            value_kind=MIRValueKind.BORROWED_RECORD),
    MIRSlot(Q, CELL, MIRSlotKind.LOCAL, form=Form.BORROW,
            value_kind=MIRValueKind.BORROWED_RECORD),
    MIRSlot(X, INT32, MIRSlotKind.LOCAL),
    MIRSlot(Y, BOOL, MIRSlotKind.LOCAL),
    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER),
)
BIND = MIRAssign(MIRPlace(Q), MIRAlias(P))
STORE = MIRAssign(field(Q), MIRConstant(7))
READ = MIRAssign(MIRPlace(X), MIRRead(field(P)))
GOOD = MIRFunction(B, INT32, SLOTS, (MIRBlock(A, (BIND, STORE, READ), MIRReturn(X)),), A)


def invalid(fn: MIRFunction, message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


@pytest.mark.parametrize("statement,message", [
    (MIRAssign(MIRPlace(Q), MIRRead(MIRPlace(P))), "read type mismatch"),
    (MIRAssign(MIRPlace(Q), MIRAlias(X)), "alias type mismatch"),
    (MIRAssign(MIRPlace(P), MIRAlias(Q)), "parameter reseat"),
    (MIRAssign(field(Q), MIRAlias(P)), "alias type mismatch"),
    (MIRAssign(MIRPlace(X, (MIRDeref(), MEMBER)), MIRConstant(7)), "needs reference holder"),
    (MIRAssign(MIRPlace(Q, (MEMBER,)), MIRConstant(7)), "unsupported place projections"),
    (MIRAssign(MIRPlace(Q, (MIRDeref(),)), MIRConstant(7)), "unsupported place projections"),
    (MIRAssign(MIRPlace(Q, (MIRDeref(), MIRField(MIRFieldId(OTHER, "value"), INT32))),
               MIRConstant(7)), "field owner mismatch"),
    (MIRAssign(field(Q), MIRConstant(True)), "constant type"),
    (MIRAssign(MIRPlace(Q), MIRConstant(7)), "constant type"),
    (MIRAssign(MIRPlace(Y), MIRRead(field(P))), "read type mismatch"),
])
def test_invalid_storage_operations(statement: MIRAssign, message: str) -> None:
    invalid(replace(GOOD, blocks=(MIRBlock(A, (BIND, statement, READ), MIRReturn(X)),)), message)


def test_valid_alias_and_field_operations() -> None:
    validate_function(GOOD)


def test_field_store_reads_holder_instead_of_initializing_it() -> None:
    invalid(replace(GOOD, blocks=(MIRBlock(A, (STORE, BIND, READ), MIRReturn(X)),)),
            "read before definite assignment")


def test_access_can_be_reduced_but_never_increased() -> None:
    readonly_p = replace(SLOTS[0], readonly=True)
    readonly_q = replace(SLOTS[1], readonly=True)
    invalid(replace(GOOD, slots=(readonly_p, *SLOTS[1:])), "alias increases access")
    invalid(replace(GOOD, slots=(SLOTS[0], readonly_q, *SLOTS[2:])), "store through readonly")
    # Reseating a const-reference holder is legal; only a referent write is forbidden.
    validate_function(replace(GOOD, slots=(readonly_p, readonly_q, *SLOTS[2:]),
                              blocks=(MIRBlock(A, (BIND, BIND, READ), MIRReturn(X)),)))


def test_field_identity_has_one_type() -> None:
    bad = MIRPlace(P, (MIRDeref(), replace(MEMBER, type=BOOL)))
    stmt = MIRAssign(MIRPlace(Y), MIRRead(bad))
    invalid(replace(GOOD, blocks=(MIRBlock(A, (BIND, STORE, stmt, READ), MIRReturn(X)),)),
            "inconsistent field type")


def test_nominal_reference_types_must_match() -> None:
    invalid(replace(GOOD, slots=(SLOTS[0], replace(SLOTS[1], type=OTHER), *SLOTS[2:])),
            "alias type mismatch")
    invalid(replace(GOOD, slots=(replace(SLOTS[0], type=NominalType("Cell")), *SLOTS[1:])),
            "reference slot type")


@pytest.mark.parametrize("typ", [BOOL, INT32])
def test_scalar_nominals_are_not_reference_holders(typ: NominalType) -> None:
    invalid(replace(GOOD, slots=(replace(SLOTS[0], type=typ), *SLOTS[1:])),
            "reference slot type")


def test_join_and_loop_cannot_initialize_reference_retroactively() -> None:
    blocks = (MIRBlock(A, (), MIRBranch(FLAG, C, D)),
              MIRBlock(C, (BIND,), MIRGoto(E)), MIRBlock(D, (), MIRGoto(E)),
              MIRBlock(E, (STORE, READ), MIRReturn(X)))
    invalid(replace(GOOD, blocks=blocks), "read before definite assignment")
    validate_function(replace(GOOD, blocks=(blocks[0], blocks[1],
                                           replace(blocks[2], statements=(BIND,)), blocks[3])))
    loop = (MIRBlock(A, (), MIRGoto(C)),
            MIRBlock(C, (STORE, BIND), MIRBranch(FLAG, C, D)),
            MIRBlock(D, (READ,), MIRReturn(X)))
    invalid(replace(GOOD, blocks=loop), "read before definite assignment")
    validate_function(replace(GOOD, blocks=(replace(loop[0], statements=(BIND,)), *loop[1:])))
