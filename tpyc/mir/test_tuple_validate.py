"""Internal tuple validation covers empty payloads and readonly references."""

from dataclasses import replace

import pytest

from ..typesys import BOOL, INT32, TupleType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant, MIRFunction, MIRGoto, MIRPlace,
    MIRRead, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleConstruct, MIRCopy,
)
from .shapes import MIRDeref, MIRTupleIndex
from .value import MIRField, MIRFieldId, MIRValidationError
from .testutil import (
    hand_record, bind_parameters, Reference, borrowed_shape, execute, leaf_shape, owned_shape, reslot, tuple_shape,
)
from .validate import validate_function

B = MIRBodyId("test", "tuples")
CELL = hand_record("Cell", "test.Cell", value=INT32)
TUPLE = TupleType((CELL, INT32))
A, R, X, PAIR, SAVED, RESULT, FLAG = (MIRSlotId(B, i) for i in range(7))
ENTRY, YES, NO, JOIN = (MIRBlockId(B, i) for i in range(4))
LAYOUT = (borrowed_shape(CELL, True), leaf_shape(INT32))
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
SLOTS = (
    MIRSlot(A, borrowed_shape(CELL), MIRSlotKind.PARAMETER),
    MIRSlot(R, borrowed_shape(CELL, readonly=True), MIRSlotKind.PARAMETER),
    MIRSlot(X, leaf_shape(INT32), MIRSlotKind.TEMPORARY),
    MIRSlot(PAIR, tuple_shape(TUPLE, LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(SAVED, tuple_shape(TUPLE, LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL),
    MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
)
INIT = MIRAssign(MIRPlace(X), MIRConstant(1))
MAKE = MIRAssign(MIRPlace(PAIR), MIRTupleConstruct((R, X)))
COPY = MIRAssign(MIRPlace(SAVED), MIRCopy(MIRPlace(PAIR)))
RESEAT = MIRAssign(MIRPlace(PAIR), MIRTupleConstruct((A, X)))
WRITE = MIRAssign(MIRPlace(A, (MIRDeref(), FIELD)), MIRConstant(9))
PROJECT = MIRPlace(SAVED, (MIRTupleIndex(0), MIRDeref(), FIELD))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(PROJECT))
GOOD = bind_parameters(MIRFunction(B, INT32, SLOTS, (MIRBlock(ENTRY, (INIT, MAKE, COPY, RESEAT, WRITE, READ),
                                                   MIRReturn(RESULT)),), ENTRY))


def invalid(fn: MIRFunction, reason: str) -> None:
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(fn)


def test_readonly_payload_preserves_shared_mutation() -> None:
    validate_function(GOOD)
    heap = {1: {FIELD.id: 3}}
    assert execute(GOOD, Reference(1), Reference(1), False, heap=heap) == 9
    assert heap == {1: {FIELD.id: 9}}
    heap = {1: {FIELD.id: 3}, 2: {FIELD.id: 4}}
    assert execute(GOOD, Reference(1), Reference(2), False, heap=heap) == 4
    assert heap == {1: {FIELD.id: 9}, 2: {FIELD.id: 4}}


def test_empty_tuple_payloads_construct_and_copy() -> None:
    # The parser rejects (), so this is deliberately an internal IR contract.
    layout = ()
    slots = (reslot(SLOTS[3], type=TupleType(()), members=layout),
             reslot(SLOTS[4], type=TupleType(()), members=layout), SLOTS[2])
    fn = bind_parameters(MIRFunction(B, INT32, slots, (MIRBlock(ENTRY, (
        MIRAssign(MIRPlace(PAIR), MIRTupleConstruct(())), COPY, INIT), MIRReturn(X)),), ENTRY))
    validate_function(fn)
    assert execute(fn) == 1


@pytest.mark.parametrize("statement,reason", [
    (MIRAssign(MIRPlace(SAVED), MIRCopy(MIRPlace(A))), "wrapper copy source mismatch"),
    (MIRAssign(MIRPlace(X), MIRCopy(MIRPlace(PAIR))), "wrapper copy needs local wrapper destination"),
    (MIRAssign(MIRPlace(SAVED), MIRTupleConstruct((X, R))), "payload type or access"),
    (MIRAssign(MIRPlace(SAVED), MIRTupleConstruct((R,))), "payload type or access"),
    (MIRAssign(MIRPlace(SAVED), MIRTupleConstruct((PAIR, X))), "payload type or access"),
    (MIRAssign(MIRPlace(SAVED), MIRRead(MIRPlace(PAIR))), "read type mismatch"),
    (MIRAssign(MIRPlace(SAVED), MIRAlias(PAIR)), "alias type mismatch"),
    (MIRAssign(MIRPlace(SAVED, (MIRTupleIndex(1),)), MIRConstant(2)), "element replacement"),
    (MIRAssign(PROJECT, MIRConstant(2)), "readonly reference"),
])
def test_tuple_operations_cannot_change_representation(statement: MIRAssign, reason: str) -> None:
    invalid(replace(GOOD, blocks=(MIRBlock(ENTRY, (INIT, MAKE, COPY, statement, READ),
                                          MIRReturn(RESULT)),)), reason)


@pytest.mark.parametrize("projections,reason", [
    ((MIRTupleIndex(-1),), "index out of range"),
    ((MIRTupleIndex(2),), "index out of range"),
    ((MIRTupleIndex(True),), "index out of range"),
    ((MIRDeref(),), "needs reference holder"),
    ((MIRTupleIndex(0), FIELD), "field needs record storage"),
    ((MIRTupleIndex(1), MIRDeref()), "needs reference holder"),
    ((MIRTupleIndex(0), MIRTupleIndex(0)), "needs tuple payload"),
    ((MIRTupleIndex(0), MIRDeref(), MIRDeref()), "needs reference holder"),
    ((MIRTupleIndex(0),), "read type mismatch"),
])
def test_projection_chain_is_typed(projections: tuple, reason: str) -> None:
    bad_read = replace(READ, value=MIRRead(MIRPlace(SAVED, projections)))
    invalid(replace(GOOD, blocks=(MIRBlock(ENTRY, (INIT, MAKE, COPY, bad_read),
                                          MIRReturn(RESULT)),)), reason)


def test_readonly_cannot_be_lost_on_capture_or_copy() -> None:
    mutable = (replace(LAYOUT[0], readonly=False), LAYOUT[1])
    for index in (3, 4):
        slots = list(SLOTS)
        slots[index] = reslot(slots[index], members=mutable)
        invalid(bind_parameters(replace(GOOD, slots=tuple(slots))), "type or access")


def test_tuple_layout_validation() -> None:
    for layout, reason in (
        ((), "tuple layout arity"),
        ((leaf_shape(BOOL), leaf_shape(INT32)), "tuple scalar"),
        # An owning layout makes the tuple storage, which needs its placement.
        ((owned_shape(CELL), leaf_shape(INT32)), "owned tuple needs local backing placement"),
    ):
        invalid(bind_parameters(replace(GOOD, slots=(*SLOTS[:3], reslot(SLOTS[3], members=layout), *SLOTS[4:]))), reason)
    invalid(bind_parameters(replace(GOOD, slots=(*SLOTS[:3], replace(SLOTS[3], kind=MIRSlotKind.PARAMETER), *SLOTS[4:]))),
            "tuple operation needs tuple destination")


def test_payloads_must_be_initialized_on_every_incoming_path() -> None:
    blocks = (MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO)),
              MIRBlock(YES, (MAKE,), MIRGoto(JOIN)), MIRBlock(NO, (), MIRGoto(JOIN)),
              MIRBlock(JOIN, (COPY, READ), MIRReturn(RESULT)))
    invalid(replace(GOOD, blocks=blocks), "read before definite assignment")
    validate_function(replace(GOOD, blocks=(blocks[0], blocks[1], replace(blocks[2], statements=(MAKE,)),
                                           blocks[3])))
    # A projected store reads the tuple; it cannot initialize the payload.
    mutable = (replace(LAYOUT[0], readonly=False), LAYOUT[1])
    slots = (*SLOTS[:4], reslot(SLOTS[4], members=mutable), *SLOTS[5:])
    invalid(bind_parameters(replace(GOOD, slots=slots, blocks=(MIRBlock(ENTRY, (
        MIRAssign(PROJECT, MIRConstant(7)), READ), MIRReturn(RESULT)),))), "read before definite assignment")
