"""Inline tuple members share backing lifetime, but borrowed siblings do not."""

from dataclasses import replace

import pytest

from ..typesys import BOOL, INT32, OptionalType, TupleType, UnionType
from .dependencies import MIRReferent
from .dump import dump_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIREdge,
    MIRFunction, MIRGoto, MIRPlace, MIRPoint, MIRRead, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration, MIRConstruct, MIRStorageWrite, MIRStorageWriteMode, MIRWrap, MIRCopy,
)
from .shapes import MIRDeref, MIRTupleIndex
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .retention import analyze_retention
from .reach import reaches
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage, dump_storage
from .testutil import (
    hand_record, bind_parameters, Reference, borrowed_shape, execute, leaf_shape, optional_shape, owned_shape,
    reslot, tuple_shape, union_shape, prepared,
)
from .prepare import validate_function


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
# Each inline member is built in a temporary of the tuple's full expression,
# which the tuple's construct moves from.
TEMPS = (MIRSlotId(BODY, 10), MIRSlotId(BODY, 11))
INITIALIZE = MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)
BUILD = tuple(MIRAssign(MIRPlace(t), MIRConstruct((VALUE,)), storage_write=INITIALIZE) for t in TEMPS)
TUPLE_INIT = MIRAssign(MIRPlace(PAIR), MIRConstruct((*TEMPS, EXTERNAL, VALUE)), storage_write=INITIALIZE)
INIT = (*BUILD, TUPLE_INIT)


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
        *(MIRSlot(t, OWNED, MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY, residence=ROOT)
          for t in TEMPS),
    )
    return bind_parameters(MIRFunction(BODY, INT32, slots, (MIRBlock(ENTRY, statements, MIRReturn(VALUE), ROOT),), ENTRY,
                       records=(MIRRecordLayout(CELL, (FIELD,), True, True),),
                       regions=(MIRRegion(ROOT, None, ENTRY),)))


def scoped(fn: MIRFunction, inside: tuple[MIRAssign, ...], after: tuple[MIRAssign, ...]) -> MIRFunction:
    slots = tuple(replace(s, storage_duration=CHILD, residence=CHILD) if s.id == PAIR or s.id in TEMPS else s
                  for s in fn.slots)
    return replace(fn, slots=slots, blocks=(
        MIRBlock(ENTRY, (), MIRGoto(INNER), ROOT),
        MIRBlock(INNER, inside, MIRGoto(EXIT), CHILD),
        MIRBlock(EXIT, after, MIRReturn(VALUE), ROOT),
    ), regions=(*fn.regions, MIRRegion(CHILD, ROOT, INNER)))


def test_inline_identity_mutation_and_initialization_inventory() -> None:
    borrow = MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))
    mutation = MIRAssign(MIRPlace(SAVED, (MIRDeref(), FIELD)), MIRConstant(9))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(PAIR, (MIRTupleIndex(0), FIELD))))
    fn = function(*INIT, borrow, mutation, read)
    fn = replace(fn, blocks=(replace(fn.blocks[0], terminator=MIRReturn(RESULT)),))
    validate_function(fn)
    heap = {1: {FIELD.id: 3}}
    assert execute(fn, Reference(1), 7, False, heap=heap) == 9
    assert heap[1][FIELD.id] == 3
    # The external cell, the two member temporaries, and the tuple's backing.
    assert len(heap) == 4
    assert heap[4][MIRTupleIndex(1)][FIELD.id] == 7
    dependencies = prepared(fn).dependencies
    assert dependencies.referents[MIRPoint(ENTRY, 4)][MIRPlace(SAVED)] == frozenset({MIRReferent(member(0))})
    assert not reaches(MIRReferent(member(0)), MIRReferent(member(1)), lambda written, target: True)
    events = analyze_storage(prepared(fn))
    # Each member temporary's initialization, then the tuple's own.
    assert [(point, stmt.target) for point, stmt in events.writes.items()] == [
        (MIRPoint(ENTRY, i), stmt.target) for i, stmt in enumerate(INIT)]
    assert not analyze_retention(prepared(fn)).conflicts
    assert "initialize %0" in dump_storage(events)
    assert "%0 = construct (%10, %11, %1, %2) [initialize]" in dump_function(fn)
    assert analyze_scope_ends(prepared(fn)).ends[MIREdge(ENTRY)][0].storage == MIRPlace(PAIR)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_scope_end_distinguishes_owned_and_borrowed_member(index: int) -> None:
    projections = (MIRTupleIndex(index),) + ((MIRDeref(),) if index == 2 else ())
    borrow = MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(PAIR, projections)))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = scoped(function(), (*INIT, borrow), (read,))
    inspection = inspect_scope_lifetimes(prepared(fn))
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
            capture, copy = MIRConstruct((SAVED,)), MIRCopy(MIRPlace(holder))
        case "optional":
            shape = optional_shape(OptionalType(CELL), borrowed_shape(CELL))
            capture, copy = MIRWrap(1, MIRPlace(SAVED)), MIRCopy(MIRPlace(holder))
        case _:
            other = hand_record("Other", "tuple_storage.Other")
            shape = union_shape(UnionType((CELL, other)), (BORROWED, borrowed_shape(other)))
            capture, copy = MIRWrap(0, MIRPlace(SAVED)), MIRCopy(MIRPlace(holder))
    fn = function()
    fn = replace(fn, slots=(*fn.slots, *(MIRSlot(s, shape, MIRSlotKind.LOCAL, residence=ROOT)
                                       for s in (holder, copied))))
    fn = scoped(fn, (*INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))),
                     MIRAssign(MIRPlace(holder), capture)), (MIRAssign(MIRPlace(copied), copy),))
    conflicts = inspect_scope_lifetimes(prepared(fn)).conflicts
    assert len(conflicts) == 1 and conflicts[0].holder.root == holder
    assert conflicts[0].retained == MIRReferent(member(0))


def test_skipped_initialization_and_repeated_scope_activation() -> None:
    fn = scoped(function(), INIT, ())
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (), MIRBranch(FLAG, INNER, EXIT), ROOT),
        MIRBlock(INNER, (*INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))),
                         MIRAssign(MIRPlace(FLAG), MIRConstant(False))), MIRGoto(ENTRY), CHILD),
        MIRBlock(EXIT, (), MIRReturn(VALUE), ROOT),
    ))
    validate_function(fn)
    assert MIREdge(ENTRY, 1) not in analyze_scope_ends(prepared(fn)).ends
    assert MIREdge(INNER) in analyze_scope_ends(prepared(fn)).ends
    # One activation builds two member temporaries and the tuple's backing.
    for flag, count in ((False, 1), (True, 4)):
        heap = {1: {FIELD.id: 3}}
        assert execute(fn, Reference(1), 7, flag, heap=heap) == 7
        assert len(heap) == count


@pytest.mark.parametrize("statements, message", [
    ((*BUILD, replace(TUPLE_INIT, storage_write=None)), "inline aggregate whole write needs its storage write fact"),
    ((*BUILD, replace(TUPLE_INIT, value=MIRCopy(MIRPlace(PAIR)))), "tuple construction"),
    ((*BUILD, replace(TUPLE_INIT, value=MIRConstruct((VALUE, TEMPS[1], EXTERNAL, VALUE)))), "payload type"),
    ((*BUILD, replace(TUPLE_INIT, value=MIRConstruct((TEMPS[0], EXTERNAL, VALUE)))), "payload type"),
    ((*BUILD, replace(TUPLE_INIT, value=MIRConstruct((TEMPS[0], TEMPS[1], TEMPS[1], VALUE)))), "payload type"),
    ((*BUILD, replace(TUPLE_INIT, value=MIRConstruct((TEMPS[0], TEMPS[1], EXTERNAL, VALUE), may_raise=True))),
     "payload type"),
    ((replace(BUILD[0], value=MIRConstruct(())), BUILD[1], TUPLE_INIT), "mistyped record construction"),
    ((replace(BUILD[0], value=MIRConstruct((FLAG,))), BUILD[1], TUPLE_INIT), "mistyped record construction"),
    ((replace(BUILD[0], value=MIRConstruct((RESULT,))), BUILD[1], TUPLE_INIT), "definite assignment"),
    ((BUILD[0], TUPLE_INIT), "definite assignment"),
])
def test_invalid_tuple_initialization(statements: tuple[MIRAssign, ...], message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(function(*statements))


def test_a_storage_member_copies_through_a_holder_of_its_type() -> None:
    # The one storage-member operand rule: a moved temporary, or a copy
    # through a copyable holder, whatever aggregate the construct builds.
    validate_function(function(BUILD[1], replace(TUPLE_INIT, value=MIRConstruct((EXTERNAL, TEMPS[1], EXTERNAL,
                                                                                VALUE)))))
    # A readonly holder copies too; the copy raises exactly when the
    # record's copy may (an all-scalar `Cell`'s never does).
    copied = function(BUILD[1], replace(TUPLE_INIT, value=MIRConstruct((EXTERNAL, TEMPS[1], EXTERNAL, VALUE))))
    readonly = replace(copied, slots=tuple(reslot(s, readonly=True) if s.id == EXTERNAL else s
                                           for s in copied.slots))
    readonly = replace(readonly, slots=(reslot(readonly.slots[0], members=(OWNED, OWNED, replace(
        BORROWED, readonly=True), leaf_shape(INT32))), *readonly.slots[1:]))
    validate_function(bind_parameters(readonly))
    raising = replace(TUPLE_INIT, value=MIRConstruct((EXTERNAL, TEMPS[1], EXTERNAL, VALUE), may_raise=True))
    with pytest.raises(MIRValidationError, match="payload type"):
        validate_function(function(BUILD[1], raising))


def test_a_storage_member_is_never_moved_out_of_a_named_local() -> None:
    # Only a temporary of the full expression hands its storage over; a
    # named local keeps its own.
    local = MIRSlotId(BODY, 12)
    fn = function(replace(BUILD[0], target=MIRPlace(local)), BUILD[1],
                  replace(TUPLE_INIT, value=MIRConstruct((local, TEMPS[1], EXTERNAL, VALUE))))
    fn = replace(fn, slots=(*fn.slots, MIRSlot(local, OWNED, MIRSlotKind.LOCAL,
                                               storage_duration=MIRStorageDuration.BODY, residence=ROOT)))
    with pytest.raises(MIRValidationError, match="payload type"):
        validate_function(fn)


def test_repeated_initialization_and_owned_parameter_are_rejected() -> None:
    with pytest.raises(MIRValidationError, match="repeated tuple initialization"):
        validate_function(function(*INIT, TUPLE_INIT))
    fn = function(*INIT)
    with pytest.raises(MIRValidationError, match="local backing placement"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], storage_duration=None), *fn.slots[1:])))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=(replace(fn.slots[0], kind=MIRSlotKind.PARAMETER), *fn.slots[1:])))


def test_same_activation_backedge_is_not_fresh_initialization() -> None:
    fn = scoped(function(), INIT, ())
    fn = replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=MIRBranch(FLAG, INNER, EXIT)), fn.blocks[2]))
    with pytest.raises(MIRValidationError, match="repeated initialization within region"):
        validate_function(fn)


def test_borrow_tuple_copy_cannot_copy_an_owned_source() -> None:
    copy = MIRSlotId(BODY, 6)
    fn = function(*INIT, MIRAssign(MIRPlace(copy), MIRCopy(MIRPlace(PAIR))))
    slot = MIRSlot(copy, tuple_shape(TUPLE, (BORROWED, BORROWED, BORROWED, leaf_shape(INT32))), MIRSlotKind.LOCAL,
                   residence=ROOT)
    fn = replace(fn, slots=(*fn.slots, slot))
    with pytest.raises(MIRValidationError, match="owning tuple copy"):
        validate_function(fn)


def test_readonly_owned_member_does_not_grant_mutable_borrow() -> None:
    fn = function(*INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0))))
    layout = (replace(OWNED, readonly=True), *LAYOUT[1:])
    fn = replace(fn, slots=(reslot(fn.slots[0], members=layout), *fn.slots[1:]))
    with pytest.raises(MIRValidationError, match="borrow increases access"):
        validate_function(fn)
    fn = replace(fn, slots=(*fn.slots[:4], reslot(fn.slots[4], readonly=True), *fn.slots[5:]))
    validate_function(fn)


def test_join_keeps_possible_owned_member_loan() -> None:
    fn = scoped(function(), (*INIT, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))),
                (MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD)))),))
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(SAVED), MIRAlias(EXTERNAL)),), MIRBranch(FLAG, INNER, EXIT), ROOT),
        *fn.blocks[1:],
    ))
    conflicts = inspect_scope_lifetimes(prepared(fn)).conflicts
    assert len(conflicts) == 1 and conflicts[0].retained == MIRReferent(member(0))


def test_reconstruction_cannot_erase_a_retained_loan_from_previous_activation() -> None:
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = scoped(function(), (*INIT, read, MIRAssign(MIRPlace(SAVED), MIRBorrow(member(0)))), ())
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(SAVED), MIRAlias(EXTERNAL)),), MIRGoto(INNER), ROOT),
        fn.blocks[1], MIRBlock(EXIT, (), MIRGoto(INNER), ROOT),
    ))
    conflicts = inspect_scope_lifetimes(prepared(fn)).conflicts
    assert len(conflicts) == 1 and conflicts[0].edge == MIREdge(INNER)
    assert conflicts[0].retained == MIRReferent(member(0))
