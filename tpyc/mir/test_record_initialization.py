"""Empty backing has no record; engaged assignment preserves alias identity."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, NominalType, NoneType, OptionalType, TupleType, UnionType
from .dependencies import analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch,
    MIRConstant, MIRConstruct, MIRDeref, MIREdge, MIRField, MIRFieldId, MIRFunction,
    MIRGoto, MIRPlace, MIRPoint, MIRRead, MIRRecordLayout, MIRRecordStorageInit,
    MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode, MIRRegion,
    MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRValueKind, MIROptionalConstruct, MIROptionalLayout, MIROptionalPayload,
    MIRTupleConstruct, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRUnionConstruct, MIRUnionLayout, MIRUnionPayload, MIRStatement,
)
from .payload_lifetime import analyze_payload_ends
from .presence import MIREngagement, _analyze_presence
from .retention import analyze_retention
from .scope_lifetime import MIRScopeEndKind, analyze_scope_ends, inspect_scope_lifetimes
from .storage import analyze_storage
from .testutil import execute
from .validate import MIRDefiniteAssignmentError, MIRValidationError, validate_function


BODY = MIRBodyId("record_initialization", "example")
CELL = NominalType("Cell", _module_qname="record_initialization.Cell")
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
BACKING, CURRENT, SAVED, VALUE, FLAG, RESULT = (MIRSlotId(BODY, i) for i in range(6))
ENTRY, YES, NO, JOIN = (MIRBlockId(BODY, i) for i in range(4))
ROOT, CHILD = (MIRRegionId(BODY, i) for i in range(2))
INIT = MIRRecordStorageInit(MIRPlace(BACKING))
WRITE = MIRAssign(MIRPlace(BACKING), MIRConstruct((VALUE,)),
                  storage_write=MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
BIND = MIRAssign(MIRPlace(CURRENT), MIRBorrow(MIRPlace(BACKING)))
SAVE = MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT))
MUTATE = MIRAssign(MIRPlace(CURRENT, (MIRDeref(), FIELD)), MIRConstant(9))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))


def function(*blocks: MIRBlock) -> MIRFunction:
    slots = (
        MIRSlot(BACKING, CELL, MIRSlotKind.LOCAL, form=Form.STORAGE, value_kind=MIRValueKind.RECORD_STORAGE,
                storage_duration=MIRStorageDuration.BODY, residence=ROOT,
                record_storage=MIRRecordStorageKind.OPTIONAL),
        MIRSlot(CURRENT, CELL, MIRSlotKind.LOCAL, form=Form.BORROW,
                value_kind=MIRValueKind.BORROWED_RECORD, residence=ROOT),
        MIRSlot(SAVED, CELL, MIRSlotKind.LOCAL, form=Form.BORROW,
                value_kind=MIRValueKind.BORROWED_RECORD, residence=ROOT),
        MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER),
        MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER),
        MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL, residence=ROOT),
    )
    return MIRFunction(BODY, INT32, slots, blocks, ENTRY,
                       records=(MIRRecordLayout(CELL, (FIELD,), True, True),),
                       regions=(MIRRegion(ROOT, None, ENTRY),))


def straight(*statements: MIRStatement) -> MIRFunction:
    return function(MIRBlock(ENTRY, statements, MIRReturn(VALUE), ROOT))


def test_empty_wrapper_is_not_a_record_or_a_source_definition() -> None:
    fn = straight(INIT)
    validate_function(fn)
    live = analyze_liveness(fn)
    assert live.entry_live == frozenset({VALUE})
    assert not any(analyze_dependencies(fn, live).referents.values())
    assert not analyze_storage(fn).writes
    assert not analyze_payload_ends(fn).ends
    event, = analyze_scope_ends(fn).ends[MIREdge(ENTRY)]
    assert event.kind is MIRScopeEndKind.RECORD_WRAPPER
    assert "initialize-record-wrapper %0 empty" in dump_function(fn)
    assert execute(fn, 7, False) == 7


def test_replacement_preserves_physical_identity_and_reports_logical_conflict() -> None:
    fn = function(MIRBlock(ENTRY, (INIT, WRITE, BIND, SAVE, WRITE, BIND, MUTATE, READ),
                           MIRReturn(RESULT), ROOT))
    validate_function(fn)
    assert execute(fn, 7, False) == 9
    assert not analyze_payload_ends(fn).ends
    ends = analyze_scope_ends(fn).ends[MIREdge(ENTRY)]
    assert [e.kind for e in ends] == [MIRScopeEndKind.RECORD_WRAPPER, MIRScopeEndKind.OBJECT]
    live = analyze_liveness(fn)
    conflicts = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn)).conflicts
    assert {(c.point, c.holder) for c in conflicts} == {(MIRPoint(ENTRY, 4), MIRPlace(SAVED))}


@pytest.mark.parametrize("statements,message", [
    ((WRITE,), "record assignment before wrapper initialization"),
    ((INIT, INIT), "repeated payload initialization"),
    ((INIT, BIND), "read before definite assignment"),
    ((INIT, SAVE), "read before definite assignment"),
    ((INIT, replace(WRITE, storage_write=None)), "explicit assignment fact"),
    ((INIT, replace(WRITE, storage_write=MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))), "explicit assignment fact"),
])
def test_malformed_or_unavailable_storage(statements: tuple[MIRStatement, ...], message: str) -> None:
    with pytest.raises(MIRValidationError, match=message):
        validate_function(straight(*statements))


def test_engagement_join_is_possible_but_does_not_allow_a_read() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (WRITE, BIND), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    validate_function(fn)
    assert dict(_analyze_presence(fn).engagement[MIRPoint(JOIN, 0)])[BACKING] == frozenset(MIREngagement)
    assert len(analyze_scope_ends(fn).ends[MIREdge(JOIN)]) == 2
    changed = replace(fn, blocks=(*fn.blocks[:3], replace(fn.blocks[3], statements=(BIND,))))
    with pytest.raises(MIRDefiniteAssignmentError):
        validate_function(changed)


def test_first_engagement_after_join_does_not_need_previous_source_assignment() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (WRITE,), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (WRITE, BIND, SAVE, MUTATE, READ), MIRReturn(RESULT), ROOT))
    validate_function(fn)
    assert execute(fn, 3, True) == execute(fn, 3, False) == 9


def test_backedge_reuses_engaged_backing_without_reinitializing_wrapper() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRGoto(YES), ROOT),
        MIRBlock(YES, (WRITE, BIND), MIRBranch(FLAG, NO, JOIN), ROOT),
        MIRBlock(NO, (MIRAssign(MIRPlace(FLAG), MIRConstant(False)),), MIRGoto(YES), ROOT),
        MIRBlock(JOIN, (SAVE, MUTATE, READ), MIRReturn(RESULT), ROOT))
    validate_function(fn)
    assert execute(fn, 3, True) == 9
    assert dict(_analyze_presence(fn).engagement[MIRPoint(YES, 0)])[BACKING] == frozenset(MIREngagement)
    assert set(analyze_scope_ends(fn).ends) == {MIREdge(JOIN)}


def test_nested_activation_ends_record_and_does_not_revive_old_alias() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT, WRITE, BIND, SAVE), MIRGoto(NO), CHILD),
        MIRBlock(NO, (), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (READ,), MIRReturn(RESULT), ROOT))
    fn = replace(fn, slots=(replace(fn.slots[0], residence=CHILD, storage_duration=CHILD),
                            replace(fn.slots[1], residence=CHILD), *fn.slots[2:]),
                 regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES)))
    validate_function(fn)
    presence = _analyze_presence(fn)
    assert BACKING not in dict(presence.engagement[MIRPoint(YES, 0)])
    inspected = inspect_scope_lifetimes(fn)
    assert {(c.edge, c.holder) for c in inspected.conflicts} == {(MIREdge(YES), MIRPlace(SAVED))}


def test_empty_inner_activation_has_only_wrapper_end() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT,), MIRGoto(NO), CHILD),
        MIRBlock(NO, (), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    fn = replace(fn, slots=(replace(fn.slots[0], residence=CHILD, storage_duration=CHILD), *fn.slots[1:]),
                 regions=(*fn.regions, MIRRegion(CHILD, ROOT, YES)))
    validate_function(fn)
    event, = analyze_scope_ends(fn).ends[MIREdge(YES)]
    assert event.kind is MIRScopeEndKind.RECORD_WRAPPER
    assert not inspect_scope_lifetimes(fn).conflicts


@pytest.mark.parametrize("shape", ["tuple_one", "tuple_two", "optional", "union"])
def test_aggregate_holder_retains_the_same_record_across_overwrite(shape: str) -> None:
    holder = MIRSlotId(BODY, 6)
    member = MIRTupleElement(CELL, MIRValueKind.BORROWED_RECORD)
    if shape.startswith("tuple"):
        two = shape == "tuple_two"
        slot = MIRSlot(holder, TupleType((CELL, INT32) if two else (CELL,)), MIRSlotKind.LOCAL,
                       value_kind=MIRValueKind.TUPLE, residence=ROOT,
                       tuple_layout=MIRTupleLayout((member, MIRTupleElement(INT32)) if two else (member,)))
        bind = MIRAssign(MIRPlace(holder), MIRTupleConstruct((CURRENT, VALUE) if two else (CURRENT,)))
        payload = MIRPlace(holder, (MIRTupleIndex(0), MIRDeref()))
    elif shape == "optional":
        slot = MIRSlot(holder, OptionalType(CELL), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
                       residence=ROOT, optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED_RECORD))
        bind = MIRAssign(MIRPlace(holder), MIROptionalConstruct(CURRENT))
        payload = MIRPlace(holder, (MIROptionalPayload(), MIRDeref()))
    else:
        slot = MIRSlot(holder, UnionType((CELL, NoneType())), MIRSlotKind.LOCAL,
                       value_kind=MIRValueKind.UNION, residence=ROOT, union_layout=MIRUnionLayout((member, None)))
        bind = MIRAssign(MIRPlace(holder), MIRUnionConstruct(0, CURRENT))
        payload = MIRPlace(holder, (MIRUnionPayload(0), MIRDeref()))
    read_holder = MIRAssign(MIRPlace(SAVED), MIRBorrow(payload))
    fn = function(MIRBlock(ENTRY, (INIT, WRITE, BIND, bind, WRITE, BIND, read_holder, MUTATE, READ),
                           MIRReturn(RESULT), ROOT))
    fn = replace(fn, slots=(*fn.slots, slot))
    validate_function(fn)
    assert execute(fn, 2, False) == 9
    live = analyze_liveness(fn)
    conflicts = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn)).conflicts
    assert len(conflicts) == 1
    assert conflicts[0].point == MIRPoint(ENTRY, 4)
    assert conflicts[0].holder.root == holder


def test_partial_wrapper_construction_cannot_authorize_assignment() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (INIT,), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (WRITE,), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRValidationError, match="record assignment before wrapper initialization"):
        validate_function(fn)


def test_backedge_cannot_reinitialize_the_same_wrapper() -> None:
    fn = function(
        MIRBlock(ENTRY, (), MIRGoto(YES), ROOT),
        MIRBlock(YES, (INIT, WRITE), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    with pytest.raises(MIRValidationError, match="initialization in cycle"):
        validate_function(fn)


@pytest.mark.parametrize("change,message", [
    ({"record_storage": MIRRecordStorageKind.DIRECT}, "empty initialization needs optional"),
    ({"storage_duration": None}, "optional backing needs local"),
    ({"record_storage": "optional"}, "invalid record storage kind"),
])
def test_backing_metadata_is_required(change: dict, message: str) -> None:
    fn = straight(INIT, WRITE)
    fn = replace(fn, slots=(replace(fn.slots[0], **change), *fn.slots[1:]))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


def test_retention_skips_writes_on_contradictory_paths() -> None:
    fn = function(
        MIRBlock(ENTRY, (INIT,), MIRBranch(FLAG, YES, JOIN), ROOT),
        MIRBlock(YES, (), MIRBranch(FLAG, JOIN, NO), ROOT),
        MIRBlock(NO, (WRITE,), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (), MIRReturn(VALUE), ROOT))
    live = analyze_liveness(fn)
    writes = analyze_storage(fn)
    assert MIRPoint(NO, 0) in writes.writes
    assert MIRPoint(NO, 0) not in _analyze_presence(fn).engagement
    assert not analyze_retention(fn, live, analyze_dependencies(fn, live), writes).conflicts


def test_possible_engagement_with_retained_alias_reports_overwrite() -> None:
    external = MIRSlotId(BODY, 6)
    fn = function(
        MIRBlock(ENTRY, (INIT, MIRAssign(MIRPlace(SAVED), MIRAlias(external))), MIRBranch(FLAG, YES, NO), ROOT),
        MIRBlock(YES, (WRITE, BIND, SAVE), MIRGoto(JOIN), ROOT),
        MIRBlock(NO, (), MIRGoto(JOIN), ROOT),
        MIRBlock(JOIN, (MIRAssign(MIRPlace(VALUE), MIRConstant(9)), WRITE, BIND, READ), MIRReturn(RESULT), ROOT))
    fn = replace(fn, slots=(*fn.slots, MIRSlot(external, CELL, MIRSlotKind.PARAMETER,
                 form=Form.BORROW, value_kind=MIRValueKind.BORROWED_RECORD)))
    assert dict(_analyze_presence(fn).engagement[MIRPoint(JOIN, 1)])[BACKING] == frozenset(MIREngagement)
    live = analyze_liveness(fn)
    conflicts = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn)).conflicts
    assert {(c.point, c.holder) for c in conflicts} == {(MIRPoint(JOIN, 1), MIRPlace(SAVED))}
