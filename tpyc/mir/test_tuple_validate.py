"""Internal tuple validation covers empty payloads and readonly references."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType, TupleType
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant,
    MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRGoto, MIRPlace, MIRRead,
    MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleConstruct, MIRTupleCopy,
    MIRTupleElement, MIRTupleIndex, MIRTupleLayout, MIRValueKind,
)
from .testutil import Reference, execute
from .validate import MIRValidationError, validate_function

B = MIRBodyId("test", "tuples")
CELL = NominalType("Cell", _module_qname="test.Cell")
TUPLE = TupleType((CELL, INT32))
A, R, X, PAIR, SAVED, RESULT, FLAG = (MIRSlotId(B, i) for i in range(7))
ENTRY, YES, NO, JOIN = (MIRBlockId(B, i) for i in range(4))
LAYOUT = MIRTupleLayout((MIRTupleElement(CELL, MIRValueKind.BORROWED, True),
                         MIRTupleElement(INT32)))
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
SLOTS = (
    MIRSlot(A, CELL, MIRSlotKind.PARAMETER, form=Form.BORROW, value_kind=MIRValueKind.BORROWED),
    MIRSlot(R, CELL, MIRSlotKind.PARAMETER, form=Form.BORROW,
            value_kind=MIRValueKind.BORROWED, readonly=True),
    MIRSlot(X, INT32, MIRSlotKind.TEMPORARY),
    MIRSlot(PAIR, TUPLE, MIRSlotKind.LOCAL, value_kind=MIRValueKind.TUPLE, tuple_layout=LAYOUT),
    MIRSlot(SAVED, TUPLE, MIRSlotKind.LOCAL, value_kind=MIRValueKind.TUPLE, tuple_layout=LAYOUT),
    MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL),
    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
)
INIT = MIRAssign(MIRPlace(X), MIRConstant(1))
MAKE = MIRAssign(MIRPlace(PAIR), MIRTupleConstruct((R, X)))
COPY = MIRAssign(MIRPlace(SAVED), MIRTupleCopy(PAIR))
RESEAT = MIRAssign(MIRPlace(PAIR), MIRTupleConstruct((A, X)))
WRITE = MIRAssign(MIRPlace(A, (MIRDeref(), FIELD)), MIRConstant(9))
PROJECT = MIRPlace(SAVED, (MIRTupleIndex(0), MIRDeref(), FIELD))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(PROJECT))
GOOD = MIRFunction(B, INT32, SLOTS, (MIRBlock(ENTRY, (INIT, MAKE, COPY, RESEAT, WRITE, READ),
                                                   MIRReturn(RESULT)),), ENTRY)


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
    layout = MIRTupleLayout(())
    slots = (replace(SLOTS[3], type=TupleType(()), tuple_layout=layout),
             replace(SLOTS[4], type=TupleType(()), tuple_layout=layout), SLOTS[2])
    fn = MIRFunction(B, INT32, slots, (MIRBlock(ENTRY, (
        MIRAssign(MIRPlace(PAIR), MIRTupleConstruct(())), COPY, INIT), MIRReturn(X)),), ENTRY)
    validate_function(fn)
    assert execute(fn) == 1


@pytest.mark.parametrize("statement,reason", [
    (MIRAssign(MIRPlace(SAVED), MIRTupleCopy(A)), "tuple source"),
    (MIRAssign(MIRPlace(X), MIRTupleCopy(PAIR)), "tuple destination"),
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
    mutable = MIRTupleLayout((replace(LAYOUT.elements[0], readonly=False), LAYOUT.elements[1]))
    for index in (3, 4):
        slots = list(SLOTS)
        slots[index] = replace(slots[index], tuple_layout=mutable)
        invalid(replace(GOOD, slots=tuple(slots)), "payload type or access")


def test_tuple_layout_validation() -> None:
    for layout, reason in (
        (None, "tuple slot"),
        (MIRTupleLayout(()), "tuple layout arity"),
        (MIRTupleLayout((MIRTupleElement(BOOL), MIRTupleElement(INT32))), "tuple scalar"),
        (MIRTupleLayout((MIRTupleElement(CELL, MIRValueKind.OWNED), MIRTupleElement(INT32))),
         "tuple slot"),
    ):
        invalid(replace(GOOD, slots=(*SLOTS[:3], replace(SLOTS[3], tuple_layout=layout), *SLOTS[4:])), reason)
    invalid(replace(GOOD, slots=(*SLOTS[:3], replace(SLOTS[3], kind=MIRSlotKind.PARAMETER), *SLOTS[4:])),
            "tuple operation needs tuple destination")


def test_payloads_must_be_initialized_on_every_incoming_path() -> None:
    blocks = (MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO)),
              MIRBlock(YES, (MAKE,), MIRGoto(JOIN)), MIRBlock(NO, (), MIRGoto(JOIN)),
              MIRBlock(JOIN, (COPY, READ), MIRReturn(RESULT)))
    invalid(replace(GOOD, blocks=blocks), "read before definite assignment")
    validate_function(replace(GOOD, blocks=(blocks[0], blocks[1], replace(blocks[2], statements=(MAKE,)),
                                           blocks[3])))
    # A projected store reads the tuple; it cannot initialize the payload.
    mutable = MIRTupleLayout((replace(LAYOUT.elements[0], readonly=False), LAYOUT.elements[1]))
    slots = (*SLOTS[:4], replace(SLOTS[4], tuple_layout=mutable), *SLOTS[5:])
    invalid(replace(GOOD, slots=slots, blocks=(MIRBlock(ENTRY, (
        MIRAssign(PROJECT, MIRConstant(7)), READ), MIRReturn(RESULT)),)), "read before definite assignment")
