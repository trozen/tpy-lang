"""An owned sum built empty holds no record; a refill preserves alias identity."""

from dataclasses import replace

import pytest

from ..typesys import BOOL, INT32, NoneType, OptionalType, TupleType, UnionType
from .dump import dump_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIREdge,
    MIRFunction, MIRGoto, MIRPlace, MIRPoint, MIRRead, MIRStorageInit, MIRStorageWrite,
    MIRStorageWriteMode, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRConstruct, MIRStatement, MIRWrap,
)
from .shapes import MIRDeref, MIRTupleIndex, MIRUnionPayload
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .payload_lifetime import analyze_payload_ends
from .retention import analyze_retention
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage, dump_storage
from .testutil import (
    hand_record, bind_parameters, borrowed_shape, execute, leaf_shape, optional_shape, owned_shape, tuple_shape,
    union_shape, prepared,
)
from .validate import MIRDefiniteAssignmentError
from .prepare import validate_function


BODY = MIRBodyId("record_initialization", "example")
CELL = hand_record("Cell", "record_initialization.Cell", value=INT32)
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
BACKING, CURRENT, SAVED, VALUE, FLAG, RESULT = (MIRSlotId(BODY, i) for i in range(6))
# The record each fill site builds before moving it into the sum.
BUILT = tuple(MIRSlotId(BODY, 6 + i) for i in range(2))
ENTRY, YES, NO, JOIN, ENTRY_AGAIN = (MIRBlockId(BODY, i) for i in range(5))
ROOT, CHILD = (MIRRegionId(BODY, i) for i in range(2))
PAYLOAD = MIRPlace(BACKING, (MIRUnionPayload(1),))
INIT = MIRStorageInit(MIRPlace(BACKING), 0, MIRConstant(None))


def write(site: int = 0) -> tuple[MIRAssign, MIRAssign]:
    """A fill: a record built in the site's storage, moved into the payload."""
    return (MIRAssign(MIRPlace(BUILT[site]), MIRConstruct((VALUE,)),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),
            MIRAssign(MIRPlace(BACKING), MIRWrap(1, MIRPlace(BUILT[site])),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)))


WRITE = write()
FILL = WRITE[1]
BIND = MIRAssign(MIRPlace(CURRENT), MIRBorrow(PAYLOAD))
SAVE = MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT))
MUTATE = MIRAssign(MIRPlace(CURRENT, (MIRDeref(), FIELD)), MIRConstant(9))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))


def function(*blocks: MIRBlock, built: MIRRegionId | None = None) -> MIRFunction:
    """`built`: the region a fill's storage lives in (the body's by default)."""
    slots = (
        MIRSlot(BACKING, optional_shape(OptionalType(CELL), owned_shape(CELL)), MIRSlotKind.LOCAL,
                storage_duration=MIRStorageDuration.BODY, residence=ROOT),
        MIRSlot(CURRENT, borrowed_shape(CELL), MIRSlotKind.LOCAL, residence=ROOT),
        MIRSlot(SAVED, borrowed_shape(CELL), MIRSlotKind.LOCAL, residence=ROOT),
        MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
        MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
        MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=ROOT),
        *(MIRSlot(sid, owned_shape(CELL), MIRSlotKind.TEMPORARY,
                  storage_duration=built if built is not None else MIRStorageDuration.BODY,
                  residence=built if built is not None else ROOT) for sid in BUILT),
    )
    return bind_parameters(MIRFunction(BODY, INT32, slots, blocks, ENTRY,
                       records=(MIRRecordLayout(CELL, (FIELD,), True, True),),
                       regions=(MIRRegion(ROOT, None, ENTRY),)))


def straight(*statements: MIRStatement) -> MIRFunction:
    return function(MIRBlock(ENTRY, statements, MIRReturn(VALUE), ROOT))


def selection(fn: MIRFunction, point: MIRPoint) -> frozenset[int]:
    return dict(prepared(fn).presence.points[point])[MIRPlace(BACKING)]


def test_empty_sum_is_not_a_record_or_a_source_definition() -> None:
    fn = straight(INIT)
    validate_function(fn)
    live = prepared(fn).liveness
    assert live.entry_live == frozenset({VALUE})
    assert not any(prepared(fn).dependencies.referents.values())
    inventory = analyze_storage(prepared(fn))
    # The sum is constructed empty: an inventory line, no record write, nothing materialized.
    assert not inventory.writes and list(inventory.constructions) == [MIRPoint(ENTRY, 0)]
    assert inventory.constructions[MIRPoint(ENTRY, 0)].materialized is None
    assert f"before 0: construct-wrapper %{BACKING.index}" in dump_storage(inventory)
    assert not analyze_payload_ends(prepared(fn)).ends
    # Its scope end ends the slot itself, which reaches any payload holder.
    event, = analyze_scope_ends(prepared(fn)).ends[MIREdge(ENTRY)]
    assert event.storage == MIRPlace(BACKING) and not event.payloads
    assert "initialize-storage %0 alternative=0 value=None" in dump_function(fn)
    assert execute(fn, 7, False) == 7


def test_replacement_preserves_physical_identity_and_reports_logical_conflict() -> None:
    fn = function(MIRBlock(ENTRY, (INIT, *WRITE, BIND, SAVE, *write(1), BIND, MUTATE, READ),
                           MIRReturn(RESULT), ROOT))
    validate_function(fn)
    assert execute(fn, 7, False) == 9
    assert not analyze_payload_ends(prepared(fn)).ends
    ends = analyze_scope_ends(prepared(fn)).ends[MIREdge(ENTRY)]
    assert MIRPlace(BACKING) in {e.storage for e in ends}
    # The refill of the selected alternative replaces the record `SAVED` holds;
    # the first fill replaces nothing.
    conflicts = analyze_retention(prepared(fn)).conflicts
    assert {(c.point, c.holder, c.affected.place) for c in conflicts} == {(MIRPoint(ENTRY, 6), MIRPlace(SAVED),
                                                                           PAYLOAD)}


@pytest.mark.parametrize("statements,message", [
    (WRITE, "assignment before storage initialization"),
    ((INIT, INIT), "repeated payload initialization"),
    ((INIT, BIND), "read before definite assignment"),
    ((INIT, SAVE), "read before definite assignment"),
    ((INIT, WRITE[0], replace(FILL, storage_write=None)), "optional field write needs a replacement fact"),
    ((INIT, WRITE[0], replace(FILL, storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))),
     "optional field write needs a replacement fact"),
    ((INIT, *WRITE, FILL), "optional field payload needs a moved temporary"),
])
def test_malformed_or_unavailable_storage(statements: tuple[MIRStatement, ...], message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(straight(*statements))


def test_selection_join_is_possible_but_does_not_allow_a_read() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (*WRITE, BIND), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    validate_function(fn)
    assert selection(fn, MIRPoint(JOIN, 0)) == frozenset({0, 1})
    assert MIRPlace(BACKING) in {e.storage for e in analyze_scope_ends(prepared(fn)).ends[MIREdge(JOIN)]}
    changed = replace(fn, blocks=(*fn.blocks[:3], replace(fn.blocks[3], statements=(BIND,))))
    with pytest.raises(MIRDefiniteAssignmentError):
        validate_function(changed)


def test_first_fill_after_join_does_not_need_previous_source_assignment() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, WRITE, MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (*write(1), BIND, SAVE, MUTATE, READ), MIRReturn(RESULT), ROOT))
    validate_function(fn)
    assert execute(fn, 3, True) == execute(fn, 3, False) == 9


def test_backedge_refills_the_sum_without_reinitializing_it() -> None:
    # Each pass builds its record in the pass's own region, then refills the sum.
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRGoto(YES), ROOT),
        MIRBlock(YES, (*WRITE, BIND), MIRGoto(NO), CHILD),
        MIRBlock(NO, (), MIRBranch(FLAG, ENTRY_AGAIN, JOIN), ROOT),
        MIRBlock(ENTRY_AGAIN, (MIRAssign(MIRPlace(FLAG), MIRConstant(False)),), MIRGoto(YES), ROOT),
        MIRBlock(JOIN, (SAVE, MUTATE, READ), MIRReturn(RESULT), ROOT), built=CHILD)
    fn = replace(fn, regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES)))
    validate_function(fn)
    assert execute(fn, 3, True) == 9
    assert selection(fn, MIRPoint(YES, 0)) == frozenset({0, 1})
    # The sum lives in the body's region: its scope ends only where the body
    # exits, never on the backedge; each pass's built record ends with its pass.
    ends = analyze_scope_ends(prepared(fn)).ends
    assert {edge for edge, found in ends.items() for end in found if end.storage.root == BACKING} == {MIREdge(JOIN)}
    assert {edge for edge, found in ends.items() for end in found if end.storage.root == BUILT[0]} == {MIREdge(YES)}


def test_nested_activation_ends_record_and_does_not_revive_old_alias() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT, *WRITE, BIND, SAVE), MIRGoto(NO), CHILD),
        MIRBlock(NO, (), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (READ,), MIRReturn(RESULT), ROOT), built=CHILD)
    fn = bind_parameters(replace(fn, slots=(replace(fn.slots[0], residence=CHILD, storage_duration=CHILD),
                            replace(fn.slots[1], residence=CHILD), *fn.slots[2:]),
                 regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES))))
    validate_function(fn)
    presence = prepared(fn).presence
    assert MIRPlace(BACKING) not in dict(presence.points[MIRPoint(YES, 0)])
    inspected = inspect_scope_lifetimes(prepared(fn))
    assert {(c.edge, c.holder) for c in inspected.conflicts} == {(MIREdge(YES), MIRPlace(SAVED))}


def test_empty_inner_activation_ends_only_the_sum() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT,), MIRGoto(NO), CHILD),
        MIRBlock(NO, (), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    fn = bind_parameters(replace(fn, slots=(replace(fn.slots[0], residence=CHILD, storage_duration=CHILD), *fn.slots[1:]),
                 regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES))))
    validate_function(fn)
    event, = analyze_scope_ends(prepared(fn)).ends[MIREdge(YES)]
    assert event.storage == MIRPlace(BACKING) and not event.payloads
    assert not inspect_scope_lifetimes(prepared(fn)).conflicts


@pytest.mark.parametrize("shape", ["tuple_one", "tuple_two", "optional", "union"])
def test_aggregate_holder_retains_the_same_record_across_overwrite(shape: str) -> None:
    holder = MIRSlotId(BODY, 8)
    member = borrowed_shape(CELL)
    if shape.startswith("tuple"):
        two = shape == "tuple_two"
        slot = MIRSlot(holder,
                       tuple_shape(TupleType((CELL, INT32) if two else (CELL,)),
                                   (member, leaf_shape(INT32)) if two else (member,)),
                       MIRSlotKind.LOCAL, residence=ROOT)
        bind = MIRAssign(MIRPlace(holder), MIRConstruct((CURRENT, VALUE) if two else (CURRENT,)))
        payload = MIRPlace(holder, (MIRTupleIndex(0), MIRDeref()))
    elif shape == "optional":
        slot = MIRSlot(holder, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.LOCAL,
                       residence=ROOT)
        bind = MIRAssign(MIRPlace(holder), MIRWrap(1, MIRPlace(CURRENT)))
        payload = MIRPlace(holder, (MIRUnionPayload(1), MIRDeref()))
    else:
        slot = MIRSlot(holder, union_shape(UnionType((CELL, NoneType())), (member, None)), MIRSlotKind.LOCAL,
                       residence=ROOT)
        bind = MIRAssign(MIRPlace(holder), MIRWrap(0, MIRPlace(CURRENT)))
        payload = MIRPlace(holder, (MIRUnionPayload(0), MIRDeref()))
    read_holder = MIRAssign(MIRPlace(SAVED), MIRBorrow(payload))
    fn = function(MIRBlock(ENTRY, (INIT, *WRITE, BIND, bind, *write(1), BIND, read_holder, MUTATE, READ),
                           MIRReturn(RESULT), ROOT))
    fn = bind_parameters(replace(fn, slots=(*fn.slots, slot)))
    validate_function(fn)
    assert execute(fn, 2, False) == 9
    conflicts = analyze_retention(prepared(fn)).conflicts
    assert len(conflicts) == 1
    assert conflicts[0].point == MIRPoint(ENTRY, 6)
    assert conflicts[0].holder.root == holder


def test_partial_sum_construction_cannot_authorize_assignment() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (INIT,), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, WRITE, MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRValidationError, match="assignment before storage initialization"):
        validate_function(fn)


def test_backedge_cannot_reinitialize_the_same_sum() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT,), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRValidationError, match="initialization in cycle"):
        validate_function(fn)


@pytest.mark.parametrize("change,message", [
    ({"shape": owned_shape(CELL)}, "physical initialization needs local inline sum"),
    ({"storage_duration": None}, "inline record sum needs local storage"),
    ({"kind": MIRSlotKind.TEMPORARY}, "inline record sum needs local storage"),
])
def test_sum_metadata_is_required(change: dict, message: str) -> None:
    fn = straight(INIT, *WRITE)
    fn = bind_parameters(replace(fn, slots=(replace(fn.slots[0], **change), *fn.slots[1:])))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


def test_retention_skips_writes_on_contradictory_paths() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(YES, (), MIRBranch(FLAG, JOIN, NO), ROOT),
        MIRBlock(NO, WRITE, MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    writes = analyze_storage(prepared(fn))
    assert MIRPoint(NO, 1) in writes.writes
    assert MIRPoint(NO, 1) not in prepared(fn).presence.points
    assert not analyze_retention(prepared(fn)).conflicts


def test_possible_fill_with_retained_alias_reports_overwrite() -> None:
    external = MIRSlotId(BODY, 8)
    fn = function(
        MIRBlock(ENTRY, (INIT, MIRAssign(MIRPlace(SAVED), MIRAlias(external))), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (*WRITE, BIND, SAVE), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (MIRAssign(MIRPlace(VALUE), MIRConstant(9)), *write(1), BIND, READ), MIRReturn(RESULT), ROOT))
    fn = bind_parameters(replace(fn, slots=(*fn.slots, MIRSlot(external, borrowed_shape(CELL), MIRSlotKind.PARAMETER))))
    assert selection(fn, MIRPoint(JOIN, 2)) == frozenset({0, 1})
    conflicts = analyze_retention(prepared(fn)).conflicts
    assert {(c.point, c.holder) for c in conflicts} == {(MIRPoint(JOIN, 2), MIRPlace(SAVED))}
