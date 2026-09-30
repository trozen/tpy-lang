"""Selection proofs and extraction bindings cannot survive the wrong writes."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NoneType, NominalType, UnionType
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant,
    MIRFunction, MIRGoto, MIRIsAlternative, MIRNot, MIRPlace, MIRRead, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleElement, MIRUnionConstruct,
    MIRUnionCopy, MIRUnionExtract, MIRUnionLayout, MIRUnionPayload, MIRValueKind,
    MIRDeref, MIRField, MIRFieldId, MIRRecordLayout, MIRConstruct,
)
from .testutil import Reference, UnionValue, execute
from .validate import MIRPresenceError, MIRValidationError, validate_function


BODY = MIRBodyId("union_validation", "example")
PARAM, CURRENT, SAVED, GUARD, COPY, VALUE, RESULT, FLAG, ALIAS = (MIRSlotId(BODY, i) for i in range(9))
ENTRY, YES, NO, JOIN, LOOP, EXIT = (MIRBlockId(BODY, i) for i in range(6))
TYPE = UnionType((NoneType(), BOOL, INT32))
LAYOUT = MIRUnionLayout((None, MIRTupleElement(BOOL), MIRTupleElement(INT32)))
PAYLOAD = MIRPlace(CURRENT, (MIRUnionPayload(2),))
SLOTS = (
    MIRSlot(PARAM, TYPE, MIRSlotKind.PARAMETER, value_kind=MIRValueKind.UNION, union_layout=LAYOUT),
    MIRSlot(CURRENT, TYPE, MIRSlotKind.LOCAL, value_kind=MIRValueKind.UNION, union_layout=LAYOUT),
    MIRSlot(SAVED, TYPE, MIRSlotKind.LOCAL, value_kind=MIRValueKind.UNION, union_layout=LAYOUT),
    MIRSlot(GUARD, BOOL, MIRSlotKind.LOCAL),
    MIRSlot(COPY, BOOL, MIRSlotKind.LOCAL),
    MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
    MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL),
    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
    MIRSlot(ALIAS, INT32, MIRSlotKind.LOCAL, form=Form.BORROW, readonly=True,
            value_kind=MIRValueKind.PAYLOAD_ALIAS, alias_source=PAYLOAD),
)
CAPTURE = MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(PARAM))
TEST = MIRAssign(MIRPlace(GUARD), MIRIsAlternative(CURRENT, (2,)))
CLEAR = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(PAYLOAD))
EXTRACT = MIRAssign(MIRPlace(ALIAS), MIRUnionExtract(PAYLOAD))
ALIAS_READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(ALIAS)))
BUILD = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(2, VALUE))
FALLBACK = MIRBlock(NO, (), MIRReturn(VALUE))


def function(*blocks: MIRBlock) -> MIRFunction:
    return MIRFunction(BODY, INT32, SLOTS, blocks, ENTRY)


@pytest.mark.parametrize("mutation", [
    CLEAR, MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(PARAM)),
    MIRAssign(MIRPlace(GUARD), MIRConstant(True)),
    MIRAssign(MIRPlace(GUARD), MIRRead(MIRPlace(FLAG))),
])
def test_stale_union_test_is_not_a_selection_proof(mutation: MIRAssign) -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST, mutation), MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    with pytest.raises(MIRPresenceError, match="current alternative proof"):
        validate_function(fn)


@pytest.mark.parametrize("negated", [False, True])
def test_complement_and_boolean_copy_select_one_alternative(negated: bool) -> None:
    test = replace(TEST, value=MIRIsAlternative(CURRENT, (0, 1)))
    copy = MIRAssign(MIRPlace(COPY), MIRNot(GUARD) if negated else MIRRead(MIRPlace(GUARD)))
    branch = MIRBranch(COPY, YES, NO) if negated else MIRBranch(COPY, NO, YES)
    fn = function(MIRBlock(ENTRY, (CAPTURE, test, copy), branch),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    validate_function(fn)
    for value in (UnionValue(0), UnionValue(1, False), UnionValue(2, 7)):
        assert execute(fn, value, 13, False) == (7 if value.alternative == 2 else 13)


def test_none_exclusion_does_not_select_a_concrete_member() -> None:
    test = replace(TEST, value=MIRIsAlternative(CURRENT, (0,)))
    fn = function(MIRBlock(ENTRY, (CAPTURE, test), MIRBranch(GUARD, NO, YES)),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


@pytest.mark.parametrize("fresh", [False, True])
def test_loop_requires_current_tags(fresh: bool) -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST), MIRGoto(LOOP)),
                  MIRBlock(LOOP, (TEST,) if fresh else (), MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, (READ, CLEAR), MIRGoto(LOOP)), FALLBACK)
    if fresh:
        validate_function(fn)
        assert execute(fn, UnionValue(2, 7), 13, False) == 13
    else:
        with pytest.raises(MIRPresenceError):
            validate_function(fn)


@pytest.mark.parametrize("reextract", [False, True])
def test_same_alternative_replacement_does_not_revive_old_scalar_alias(reextract: bool) -> None:
    # A new tag proof does not prove the old C++ subobject reference survived.
    statements = (CAPTURE, TEST)
    replacement = (EXTRACT, BUILD, TEST) + ((EXTRACT,) if reextract else ()) + (ALIAS_READ,)
    fn = function(MIRBlock(ENTRY, statements, MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, replacement, MIRReturn(RESULT)), FALLBACK)
    if reextract:
        validate_function(fn)
        assert execute(fn, UnionValue(2, 7), 13, False) == 13
    else:
        with pytest.raises(MIRPresenceError, match="alias used after holder replacement"):
            validate_function(fn)


def test_alias_validity_requires_every_join_predecessor() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, (EXTRACT,), MIRBranch(FLAG, LOOP, JOIN)),
                  MIRBlock(LOOP, (BUILD,), MIRGoto(JOIN)),
                  MIRBlock(JOIN, (ALIAS_READ,), MIRReturn(RESULT)), FALLBACK)
    with pytest.raises(MIRPresenceError, match="alias used after holder replacement"):
        validate_function(fn)


def test_join_recovers_a_selection_from_boolean_predecessors() -> None:
    fn = function(
        MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (MIRAssign(MIRPlace(COPY), MIRRead(MIRPlace(FLAG))),), MIRGoto(JOIN)),
        MIRBlock(NO, (MIRAssign(MIRPlace(COPY), MIRConstant(False)),), MIRGoto(JOIN)),
        MIRBlock(JOIN, (), MIRBranch(COPY, LOOP, EXIT)),
        MIRBlock(LOOP, (READ,), MIRReturn(RESULT)),
        MIRBlock(EXIT, (), MIRReturn(VALUE)))
    validate_function(fn)
    for arg in (UnionValue(0), UnionValue(1, False), UnionValue(2, 7)):
        for flag in (False, True):
            assert execute(fn, arg, 13, flag) == (7 if arg.alternative == 2 and flag else 13)


def test_same_successor_and_distinct_alternatives_lose_proof() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, YES)),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)))
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


def test_scalar_alias_recreated_each_loop_entry() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE,), MIRGoto(LOOP)),
                  MIRBlock(LOOP, (TEST,), MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, (EXTRACT, ALIAS_READ), MIRBranch(FLAG, JOIN, EXIT)),
                  MIRBlock(JOIN, (BUILD, MIRAssign(MIRPlace(FLAG), MIRConstant(False))), MIRGoto(LOOP)),
                  MIRBlock(EXIT, (), MIRReturn(RESULT)), FALLBACK)
    validate_function(fn)
    assert execute(fn, UnionValue(2, 7), 13, True) == 13
    assert execute(fn, UnionValue(2, 7), 13, False) == 7


def test_scalar_alias_is_a_place_not_a_snapshot() -> None:
    fn = function(MIRBlock(ENTRY, (BUILD, EXTRACT, ALIAS_READ), MIRReturn(RESULT)))
    validate_function(fn)
    assert execute(fn, UnionValue(0), 7, False) == 7
    # The interpreter deliberately follows the alias's place. Validation is
    # what excludes this replacement; a captured scalar would return stale 7.
    changed = replace(fn, blocks=(replace(fn.blocks[0], statements=(
        BUILD, EXTRACT, MIRAssign(MIRPlace(VALUE), MIRConstant(11)), BUILD, ALIAS_READ)),))
    assert execute(changed, UnionValue(0), 7, False) == 11
    with pytest.raises(MIRPresenceError):
        validate_function(changed)


@pytest.mark.parametrize("checked_read", [False, True])
def test_record_construction_requires_explicit_scalar_alias_read(checked_read: bool) -> None:
    record = NominalType("Box", _module_qname="union_validation.Box")
    storage = MIRSlotId(BODY, len(SLOTS))
    field = MIRField(MIRFieldId(record, "value"), INT32)
    slot = MIRSlot(storage, record, MIRSlotKind.LOCAL, form=Form.STORAGE,
                   value_kind=MIRValueKind.RECORD_STORAGE)
    # A checked read before replacement snapshots a scalar; the alias itself
    # cannot bypass that read by appearing directly in a constructor operand.
    statements = (BUILD, EXTRACT) + ((ALIAS_READ,) if checked_read else ()) + (
        CLEAR, MIRAssign(MIRPlace(storage), MIRConstruct((RESULT if checked_read else ALIAS,))))
    fn = replace(function(MIRBlock(ENTRY, statements, MIRReturn(VALUE))),
                 slots=(*SLOTS, slot), records=(MIRRecordLayout(record, (field,), True, True),))
    if checked_read:
        validate_function(fn)
        heap = {}
        assert execute(fn, UnionValue(0), 7, False, heap=heap) == 7
        assert heap[1][field.id] == 7
    else:
        with pytest.raises(MIRValidationError, match="record construction"):
            validate_function(fn)


@pytest.mark.parametrize("bad", [
    replace(SLOTS[0], union_layout=None),
    replace(SLOTS[0], union_layout=MIRUnionLayout((None, MIRTupleElement(INT32), MIRTupleElement(BOOL)))),
    replace(SLOTS[0], form=Form.BORROW),
    replace(SLOTS[8], alias_source=None),
])
def test_bad_union_layouts_and_alias_slots(bad: MIRSlot) -> None:
    slots = tuple(bad if s.id == bad.id else s for s in SLOTS)
    fn = replace(function(MIRBlock(ENTRY, (), MIRReturn(VALUE))), slots=slots)
    with pytest.raises(MIRValidationError):
        validate_function(fn)


@pytest.mark.parametrize("stmt", [
    MIRAssign(MIRPlace(PARAM), MIRUnionConstruct(0)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(2, FLAG)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(-1)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0, VALUE)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(VALUE)),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(CURRENT, ())),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(CURRENT, (3,))),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(CURRENT, (2, 2))),
    MIRAssign(MIRPlace(RESULT), MIRUnionExtract(PAYLOAD)),
    MIRAssign(MIRPlace(ALIAS), MIRRead(PAYLOAD)),
    MIRAssign(PAYLOAD, MIRConstant(1)),
])
def test_malformed_union_operations(stmt: MIRAssign) -> None:
    with pytest.raises(MIRValidationError):
        validate_function(function(MIRBlock(ENTRY, (BUILD, stmt), MIRReturn(VALUE))))


def reference_function(source_const: bool = False, dest_const: bool = False) -> MIRFunction:
    cell = NominalType("Cell", _module_qname="union_validation.Cell")
    other = NominalType("Other", _module_qname="union_validation.Other")
    typ = UnionType((cell, other))
    def layout(readonly: bool) -> MIRUnionLayout:
        return MIRUnionLayout(tuple(MIRTupleElement(t, MIRValueKind.BORROWED_RECORD, readonly) for t in (cell, other)))
    ref_slots = (
        MIRSlot(PARAM, typ, MIRSlotKind.PARAMETER, value_kind=MIRValueKind.UNION, union_layout=layout(source_const)),
        MIRSlot(CURRENT, typ, MIRSlotKind.LOCAL, value_kind=MIRValueKind.UNION, union_layout=layout(dest_const)),
        MIRSlot(SAVED, typ, MIRSlotKind.PARAMETER, value_kind=MIRValueKind.UNION, union_layout=layout(source_const)),
        MIRSlot(GUARD, BOOL, MIRSlotKind.LOCAL),
        MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
        MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL),
        MIRSlot(FLAG, cell, MIRSlotKind.PARAMETER, value_kind=MIRValueKind.BORROWED_RECORD, form=Form.BORROW),
        MIRSlot(ALIAS, cell, MIRSlotKind.LOCAL, value_kind=MIRValueKind.BORROWED_RECORD, form=Form.BORROW,
                readonly=dest_const),
    )
    field = MIRField(MIRFieldId(cell, "value"), INT32)
    bind = MIRAssign(MIRPlace(ALIAS), MIRUnionExtract(MIRPlace(CURRENT, (MIRUnionPayload(0),))))
    overwrite = MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(SAVED))
    mutate = MIRAssign(MIRPlace(FLAG, (MIRDeref(), field)), MIRRead(MIRPlace(VALUE)))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(ALIAS, (MIRDeref(), field))))
    return MIRFunction(BODY, INT32, ref_slots, (
        MIRBlock(ENTRY, (CAPTURE, replace(TEST, value=MIRIsAlternative(CURRENT, (0,)))), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (bind, overwrite, mutate, read), MIRReturn(RESULT)), FALLBACK,
    ), ENTRY)


@pytest.mark.parametrize("source_const,dest_const", [(False, False), (False, True), (True, True), (True, False)])
def test_reference_capture_survives_reseat_and_preserves_capability(source_const: bool, dest_const: bool) -> None:
    fn = reference_function(source_const, dest_const)
    if source_const and not dest_const:
        with pytest.raises(MIRValidationError, match="increases access"):
            validate_function(fn)
        return
    validate_function(fn)
    field = MIRFieldId(fn.slots[-1].type, "value")
    heap = {1: {field: 2}}
    assert execute(fn, UnionValue(0, Reference(1)), UnionValue(1, Reference(2)), 9, Reference(1), heap=heap) == 9


def test_readonly_extraction_cannot_create_mutable_alias() -> None:
    fn = reference_function(True, True)
    fn = replace(fn, slots=(*fn.slots[:-1], replace(fn.slots[-1], readonly=False)))
    with pytest.raises(MIRValidationError, match="increases access"):
        validate_function(fn)


@pytest.mark.parametrize("source_const,dest_const", [(False, False), (False, True), (True, True), (True, False)])
def test_record_member_construction_preserves_capability(source_const: bool, dest_const: bool) -> None:
    fn = reference_function(False, dest_const)
    source = replace(fn.slots[-2], readonly=source_const)
    # Construct directly from the record parameter, then retain the extracted
    # identity across wrapper replacement and mutate through a separate alias.
    construction = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0, FLAG))
    mutable = MIRSlotId(BODY, 9)
    mutable_slot = replace(source, id=mutable, readonly=False)
    entry = replace(fn.blocks[0], statements=(construction, *fn.blocks[0].statements[1:]))
    branch = fn.blocks[1]
    write = branch.statements[2]
    branch = replace(branch, statements=(*branch.statements[:2],
        replace(write, target=replace(write.target, root=mutable)), *branch.statements[3:]))
    fn = replace(fn, slots=(*fn.slots[:-2], source, fn.slots[-1], mutable_slot),
                 blocks=(entry, branch, fn.blocks[2]))
    if source_const and not dest_const:
        with pytest.raises(MIRValidationError, match="type or access mismatch"):
            validate_function(fn)
        return
    validate_function(fn)
    field = MIRFieldId(source.type, "value")
    heap = {1: {field: 2}}
    assert execute(fn, UnionValue(1, Reference(2)), UnionValue(1, Reference(2)),
                   9, Reference(1), Reference(1), heap=heap) == 9
