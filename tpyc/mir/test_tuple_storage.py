"""Inline tuple members share backing lifetime, but borrowed siblings do not."""

from dataclasses import replace

import pytest

from ..typesys import BOOL, INT32, OptionalType, TupleType, UnionType
from .dependencies import analyze_dependencies, MIRReferent
from .dump import dump_function
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIRConstruct, MIREdge,
    MIRFunction, MIRGoto, MIROptionalConstruct, MIROptionalCopy, MIRPlace, MIRPoint, MIRRead, MIRRegion, MIRRegionId,
    MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRTupleConstruct, MIRTupleCopy,
    MIRTupleInitialization, MIRUnionConstruct, MIRUnionCopy,
)
from .shapes import MIRDeref, MIRTupleIndex
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .retention import analyze_retention, may_overlap
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage, dump_storage
from .testutil import (
    hand_record,
    bind_parameters, Reference, borrowed_shape, execute, leaf_shape, optional_shape, owned_shape, reslot,
    tuple_shape, union_shape,
)
from .validate import validate_function


BODY = MIRBodyId("tuple_storage", "example")
CELL = hand_record("Cell", "tuple_storage.Cell", value=INT32)
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
PAIR, EXTERNAL, VALUE, FLAG, SAVED, RESULT = (MIRSlotId(BODY, i) for i in range(6))
ENTRY, INNER, EXIT = (MIRBlockId(BODY, i) for i in range(3))
ROOT, CHILD = (MIRRegionId(BODY, i) for i in range(2))
OWNED = owned_shape(CELL)
BORROWED = borrowed_shape(CELL)
LAYOUT = (OWNED, OWNED, BORROWED, leaf_shape(INT32))
TUPLE = TupleType((CELL, CELL, CELL, INT32))
INIT = MIRAssign(MIRPlace(PAIR), MIRTupleConstruct((MIRConstruct((VALUE,)), MIRConstruct((VALUE,)),
                                                 EXTERNAL, VALUE)), storage_write=MIRTupleInitialization())


def member(index: int) -> MIRPlace:
    return MIRPlace(PAIR, (MIRTupleIndex(index),))


def function(*statements: MIRAssign) -> MIRFunction:
    slots = (
        MIRSlot(PAIR, tuple_shape(TUPLE, LAYOUT), MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY,
                residence=ROOT),
        MIRSlot(EXTERNAL, borrowed_shape(CELL), MIRSlotKind.PARAMETER),
        MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
        MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
        MIRSlot(SAVED, borrowed_shape(CELL), MIRSlotKind.LOCAL, residence=ROOT),
        MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=ROOT),
    )
    return bind_parameters(MIRFunction(BODY, INT32, slots, (MIRBlock(ENTRY, statements, MIRReturn(VALUE), ROOT),), ENTRY,
                       records=(MIRRecordLayout(CELL, (FIELD,), True, True),),
                       regions=(MIRRegion(ROOT, None, ENTRY),)))


def scoped(fn: MIRFunction, inside: tuple[MIRAssign, ...], after: tuple[MIRAssign, ...]) -> MIRFunction:
    slots = (replace(fn.slots[0], storage_duration=CHILD, residence=CHILD), *fn.slots[1:])
    return replace(fn, slots=slots, blocks=(
        MIRBlock(ENTRY, (), MIRGoto(INNER), ROOT),
        MIRBlock(INNER, inside, MIRGoto(EXIT), CHILD),
        MIRBlock(EXIT, after, MIRReturn(VALUE), ROOT),
    ), regions=(*fn.regions, MIRRegion(CHILD, ROOT, INNER)))


def test_inline_identity_mutation_and_initialization_inventory() -> None:
    borrow = MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))
    mutation = MIRAssign(MIRPlace(SAVED, (MIRDeref(), FIELD)), MIRConstant(9))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(PAIR, (MIRTupleIndex(0), FIELD))))
    fn = function(INIT, borrow, mutation, read)
    fn = replace(fn, blocks=(replace(fn.blocks[0], terminator=MIRReturn(RESULT)),))
    validate_function(fn)
    heap = {1: {FIELD.id: 3}}
    assert execute(fn, Reference(1), 7, False, heap=heap) == 9
    assert heap[1][FIELD.id] == 3
    assert len(heap) == 2
    assert heap[2][MIRTupleIndex(1)][FIELD.id] == 7
    live = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, live)
    assert dependencies.referents[MIRPoint(ENTRY, 2)][MIRPlace(SAVED)] == frozenset({MIRReferent(member(0))})
    assert not may_overlap(MIRReferent(member(0)), MIRReferent(member(1)))
    events = analyze_storage(fn)
    assert not events.writes
    assert events.member_initializations == {MIRPoint(ENTRY, 0): (member(0), member(1))}
    assert not analyze_retention(fn, live, dependencies, events).conflicts
    assert "initialize-tuple-members %0[0], %0[1]" in dump_storage(events)
    assert "construct (%2), construct (%2), %1, %2" in dump_function(fn)
    assert analyze_scope_ends(fn).ends[MIREdge(ENTRY)][0].storage == MIRPlace(PAIR)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_scope_end_distinguishes_owned_and_borrowed_member(index: int) -> None:
    projections = (MIRTupleIndex(index),) + ((MIRDeref(),) if index == 2 else ())
    borrow = MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(PAIR, projections)))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = scoped(function(), (INIT, borrow), (read,))
    inspection = inspect_scope_lifetimes(fn)
    assert len(inspection.conflicts) == (0 if index == 2 else 1)
    if index != 2:
        assert inspection.conflicts[0].retained == MIRReferent(member(index))
        assert inspection.conflicts[0].ended == MIRPlace(PAIR)


@pytest.mark.parametrize("kind", ["tuple", "optional", "union"])
def test_aggregate_holders_retain_inline_member_after_tuple_ends(kind: str) -> None:
    holder, copied = MIRSlotId(BODY, 6), MIRSlotId(BODY, 7)
    match kind:
        case "tuple":
            shape = tuple_shape(TupleType((CELL,)), (BORROWED,))
            capture, copy = MIRTupleConstruct((SAVED,)), MIRTupleCopy(holder)
        case "optional":
            shape = optional_shape(OptionalType(CELL), borrowed_shape(CELL))
            capture, copy = MIROptionalConstruct(MIRPlace(SAVED)), MIROptionalCopy(MIRPlace(holder))
        case _:
            other = hand_record("Other", "tuple_storage.Other")
            shape = union_shape(UnionType((CELL, other)), (BORROWED, borrowed_shape(other)))
            capture, copy = MIRUnionConstruct(0, MIRPlace(SAVED)), MIRUnionCopy(MIRPlace(holder))
    fn = function()
    fn = replace(fn, slots=(*fn.slots, *(MIRSlot(s, shape, MIRSlotKind.LOCAL, residence=ROOT)
                                       for s in (holder, copied))))
    fn = scoped(fn, (INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))),
                     MIRAssign(MIRPlace(holder), capture)), (MIRAssign(MIRPlace(copied), copy),))
    conflicts = inspect_scope_lifetimes(fn).conflicts
    assert len(conflicts) == 1 and conflicts[0].holder.root == holder
    assert conflicts[0].retained == MIRReferent(member(0))


def test_skipped_initialization_and_repeated_scope_activation() -> None:
    fn = scoped(function(), (INIT,), ())
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (), MIRBranch(FLAG, INNER, EXIT), ROOT),
        MIRBlock(INNER, (INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))),
                         MIRAssign(MIRPlace(FLAG), MIRConstant(False))), MIRGoto(ENTRY), CHILD),
        MIRBlock(EXIT, (), MIRReturn(VALUE), ROOT),
    ))
    validate_function(fn)
    assert MIREdge(ENTRY, 1) not in analyze_scope_ends(fn).ends
    assert MIREdge(INNER) in analyze_scope_ends(fn).ends
    for flag, count in ((False, 1), (True, 2)):
        heap = {1: {FIELD.id: 3}}
        assert execute(fn, Reference(1), 7, flag, heap=heap) == 7
        assert len(heap) == count


@pytest.mark.parametrize("change, message", [
    ({"storage_write": None}, "initialization fact"),
    ({"value": MIRTupleCopy(PAIR)}, "tuple construction"),
    ({"value": MIRTupleConstruct((EXTERNAL, MIRConstruct((VALUE,)), EXTERNAL, VALUE))}, "needs constructor"),
    ({"value": MIRTupleConstruct((MIRConstruct(()), MIRConstruct((VALUE,)), EXTERNAL, VALUE))}, "mistyped tuple"),
    ({"value": MIRTupleConstruct((MIRConstruct((FLAG,)), MIRConstruct((VALUE,)), EXTERNAL, VALUE))}, "mistyped tuple"),
    ({"value": MIRTupleConstruct((MIRConstruct((RESULT,)), MIRConstruct((VALUE,)), EXTERNAL, VALUE))}, "definite assignment"),
])
def test_invalid_tuple_initialization(change: dict[str, object], message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(function(replace(INIT, **change)))


def test_repeated_initialization_and_owned_parameter_are_rejected() -> None:
    with pytest.raises(MIRValidationError, match="repeated tuple initialization"):
        validate_function(function(INIT, INIT))
    fn = function(INIT)
    with pytest.raises(MIRValidationError, match="local backing placement"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], storage_duration=None), *fn.slots[1:])))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=(replace(fn.slots[0], kind=MIRSlotKind.PARAMETER), *fn.slots[1:])))


def test_same_activation_backedge_is_not_fresh_initialization() -> None:
    fn = scoped(function(), (INIT,), ())
    fn = replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=MIRBranch(FLAG, INNER, EXIT)), fn.blocks[2]))
    with pytest.raises(MIRValidationError, match="repeated initialization within region"):
        validate_function(fn)


def test_borrow_tuple_copy_cannot_copy_an_owned_source() -> None:
    copy = MIRSlotId(BODY, 6)
    fn = function(INIT, MIRAssign(MIRPlace(copy), MIRTupleCopy(PAIR)))
    slot = MIRSlot(copy, tuple_shape(TUPLE, (BORROWED, BORROWED, BORROWED, leaf_shape(INT32))), MIRSlotKind.LOCAL,
                   residence=ROOT)
    fn = replace(fn, slots=(*fn.slots, slot))
    with pytest.raises(MIRValidationError, match="owning tuple copy"):
        validate_function(fn)


def test_readonly_owned_member_does_not_grant_mutable_borrow() -> None:
    fn = function(INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))))
    layout = (replace(OWNED, readonly=True), *LAYOUT[1:])
    fn = replace(fn, slots=(reslot(fn.slots[0], members=layout), *fn.slots[1:]))
    with pytest.raises(MIRValidationError, match="borrow increases access"):
        validate_function(fn)
    fn = replace(fn, slots=(*fn.slots[:4], reslot(fn.slots[4], readonly=True), fn.slots[5]))
    validate_function(fn)


def test_join_keeps_possible_owned_member_loan() -> None:
    fn = scoped(function(), (INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))),
                (MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD)))),))
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(SAVED), MIRAlias(EXTERNAL)),), MIRBranch(FLAG, INNER, EXIT), ROOT),
        *fn.blocks[1:],
    ))
    conflicts = inspect_scope_lifetimes(fn).conflicts
    assert len(conflicts) == 1 and conflicts[0].retained == MIRReferent(member(0))


def test_reconstruction_cannot_erase_a_retained_loan_from_previous_activation() -> None:
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = scoped(function(), (INIT, read, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))), ())
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(SAVED), MIRAlias(EXTERNAL)),), MIRGoto(INNER), ROOT),
        fn.blocks[1], MIRBlock(EXIT, (), MIRGoto(INNER), ROOT),
    ))
    conflicts = inspect_scope_lifetimes(fn).conflicts
    assert len(conflicts) == 1 and conflicts[0].edge == MIREdge(INNER)
    assert conflicts[0].retained == MIRReferent(member(0))
