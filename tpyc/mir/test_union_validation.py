"""Selection proofs and extraction bindings cannot survive the wrong writes."""

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NoneType, NominalType, OptionalType, UnionType
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant, MIRFunction, MIRGoto, MIRIsAlternative, MIRNot,
    MIRPlace, MIRRead, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRUnionConstruct, MIRUnionCopy, MIRUnionExtract,
    MIRUnionPayload, MIRDeref, MIRConstruct,
)
from .value import MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRValidationError
from .testutil import (
    bind_parameters, Reference, UnionValue, alias_shape, borrowed_shape, execute, leaf_shape, optional_shape, owned_shape,
    reslot, union_shape,
)
from .validate import MIRPresenceError, validate_function


BODY = MIRBodyId("union_validation", "example")
PARAM, CURRENT, SAVED, GUARD, COPY, VALUE, RESULT, FLAG, ALIAS = (MIRSlotId(BODY, i) for i in range(9))
ENTRY, YES, NO, JOIN, LOOP, EXIT = (MIRBlockId(BODY, i) for i in range(6))
TYPE = UnionType((NoneType(), BOOL, INT32))
LAYOUT = (None, leaf_shape(BOOL), leaf_shape(INT32))
PAYLOAD = MIRPlace(CURRENT, (MIRUnionPayload(2),))
SLOTS = (
    MIRSlot(PARAM, union_shape(TYPE, LAYOUT), MIRSlotKind.PARAMETER),
    MIRSlot(CURRENT, union_shape(TYPE, LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(SAVED, union_shape(TYPE, LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(GUARD, leaf_shape(BOOL), MIRSlotKind.LOCAL),
    MIRSlot(COPY, leaf_shape(BOOL), MIRSlotKind.LOCAL),
    MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
    MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL),
    MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
    MIRSlot(ALIAS, alias_shape(INT32, readonly=True), MIRSlotKind.LOCAL, alias_source=PAYLOAD),
)
CAPTURE = MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(MIRPlace(PARAM)))
TEST = MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), (2,)))
CLEAR = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(PAYLOAD))
EXTRACT = MIRAssign(MIRPlace(ALIAS), MIRUnionExtract(PAYLOAD))
ALIAS_READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(ALIAS)))
BUILD = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(2, MIRPlace(VALUE)))
FALLBACK = MIRBlock(NO, (), MIRReturn(VALUE))


def function(*blocks: MIRBlock) -> MIRFunction:
    return bind_parameters(MIRFunction(BODY, INT32, SLOTS, blocks, ENTRY))


@pytest.mark.parametrize("mutation", [
    CLEAR, MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(MIRPlace(PARAM))),
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
    test = replace(TEST, value=MIRIsAlternative(MIRPlace(CURRENT), (0, 1)))
    copy = MIRAssign(MIRPlace(COPY), MIRNot(GUARD) if negated else MIRRead(MIRPlace(GUARD)))
    branch = MIRBranch(COPY, YES, NO) if negated else MIRBranch(COPY, NO, YES)
    fn = function(MIRBlock(ENTRY, (CAPTURE, test, copy), branch),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    validate_function(fn)
    for value in (UnionValue(0), UnionValue(1, False), UnionValue(2, 7)):
        assert execute(fn, value, 13, False) == (7 if value.alternative == 2 else 13)


def test_none_exclusion_does_not_select_a_concrete_member() -> None:
    test = replace(TEST, value=MIRIsAlternative(MIRPlace(CURRENT), (0,)))
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
    slot = MIRSlot(storage, owned_shape(record), MIRSlotKind.LOCAL)
    # A checked read before replacement snapshots a scalar; the alias itself
    # cannot bypass that read by appearing directly in a constructor operand.
    statements = (BUILD, EXTRACT) + ((ALIAS_READ,) if checked_read else ()) + (
        CLEAR, MIRAssign(MIRPlace(storage), MIRConstruct((RESULT if checked_read else ALIAS,))))
    fn = bind_parameters(replace(function(MIRBlock(ENTRY, statements, MIRReturn(VALUE))),
                 slots=(*SLOTS, slot), records=(MIRRecordLayout(record, (field,), True, True),)))
    if checked_read:
        validate_function(fn)
        heap = {}
        assert execute(fn, UnionValue(0), 7, False, heap=heap) == 7
        assert heap[1][field.id] == 7
    else:
        with pytest.raises(MIRValidationError, match="record construction"):
            validate_function(fn)


@pytest.mark.parametrize(("bad", "message"), [
    (reslot(SLOTS[0], members=()), "slot unsupported union layout"),
    (reslot(SLOTS[0], members=(None, leaf_shape(INT32), leaf_shape(BOOL))), "slot unsupported union scalar"),
    (reslot(SLOTS[0], holding=MIRHolding.POINTER), "slot invalid holding"),
    (replace(SLOTS[8], alias_source=None), "unsupported payload alias"),
    # A payload alias is no parameter: no binding derives its holding.
    (replace(SLOTS[8], kind=MIRSlotKind.PARAMETER), "parameter call parameter binding differs from its derivation"),
])
def test_bad_union_layouts_and_alias_slots(bad: MIRSlot, message: str) -> None:
    slots = tuple(bad if s.id == bad.id else s for s in SLOTS)
    # A forged parameter keeps the binding of the slot it forges; a slot made
    # a parameter has none to keep.
    fn = replace(function(MIRBlock(ENTRY, (), MIRReturn(VALUE))), slots=slots)
    if bad.kind is not SLOTS[bad.id.index].kind:
        fn = bind_parameters(replace(fn, parameter_bindings=()))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


def test_each_parameter_slot_holds_its_binding() -> None:
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    validate_function(fn)
    bindings = fn.parameter_bindings
    with pytest.raises(MIRValidationError, match="parameter slots differ from their bindings"):
        validate_function(replace(fn, parameter_bindings=bindings[:-1]))
    with pytest.raises(MIRValidationError, match="invalid parameter binding"):
        validate_function(replace(fn, parameter_bindings=(*bindings[:-1], "binding")))
    # A binding naming another value than its slot holds.
    union = bindings[0]
    forged = replace(union, value=replace(union.value, members=tuple(reversed(union.value.members))))
    with pytest.raises(MIRValidationError, match="parameter slot differs from its binding"):
        validate_function(replace(fn, parameter_bindings=(forged, *bindings[1:])))
    # Every binding has a passing, a constructor's built receiver's included.
    with pytest.raises(MIRValidationError, match="parameter malformed call parameter binding"):
        validate_function(replace(fn, parameter_bindings=(replace(union, passing=None), *bindings[1:])))
    # A slot no binding derives (a scalar Optional by pointer) is left without
    # one, so the count refuses it.
    maybe = MIRSlot(MIRSlotId(BODY, len(SLOTS)), optional_shape(OptionalType(INT32), leaf_shape(INT32)),
                    MIRSlotKind.PARAMETER)
    unbound = bind_parameters(replace(fn, slots=(*SLOTS, maybe), parameter_bindings=()),
                              {maybe.id: ParamPassing.POINTER})
    assert len(unbound.parameter_bindings) == len(bindings)
    with pytest.raises(MIRValidationError, match="parameter slots differ from their bindings"):
        validate_function(unbound)


@pytest.mark.parametrize("stmt", [
    MIRAssign(MIRPlace(PARAM), MIRUnionConstruct(0)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(2, MIRPlace(FLAG))),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(-1)),
    MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0, MIRPlace(VALUE))),
    MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(MIRPlace(VALUE))),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), ())),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), (3,))),
    MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), (2, 2))),
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
    def layout(readonly: bool) -> tuple:
        return tuple(borrowed_shape(t, readonly) for t in (cell, other))
    ref_slots = (
        MIRSlot(PARAM, union_shape(typ, layout(source_const)), MIRSlotKind.PARAMETER),
        MIRSlot(CURRENT, union_shape(typ, layout(dest_const)), MIRSlotKind.LOCAL),
        MIRSlot(SAVED, union_shape(typ, layout(source_const)), MIRSlotKind.PARAMETER),
        MIRSlot(GUARD, leaf_shape(BOOL), MIRSlotKind.LOCAL),
        MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER),
        MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL),
        MIRSlot(FLAG, borrowed_shape(cell), MIRSlotKind.PARAMETER),
        MIRSlot(ALIAS, borrowed_shape(cell, readonly=dest_const), MIRSlotKind.LOCAL),
    )
    field = MIRField(MIRFieldId(cell, "value"), INT32)
    bind = MIRAssign(MIRPlace(ALIAS), MIRUnionExtract(MIRPlace(CURRENT, (MIRUnionPayload(0),))))
    overwrite = MIRAssign(MIRPlace(CURRENT), MIRUnionCopy(MIRPlace(SAVED)))
    mutate = MIRAssign(MIRPlace(FLAG, (MIRDeref(), field)), MIRRead(MIRPlace(VALUE)))
    read = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(ALIAS, (MIRDeref(), field))))
    return bind_parameters(MIRFunction(BODY, INT32, ref_slots, (
        MIRBlock(ENTRY, (CAPTURE, replace(TEST, value=MIRIsAlternative(MIRPlace(CURRENT), (0,)))), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (bind, overwrite, mutate, read), MIRReturn(RESULT)), FALLBACK,
    ), ENTRY))


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
    fn = bind_parameters(replace(fn, slots=(*fn.slots[:-1], reslot(fn.slots[-1], readonly=False))))
    with pytest.raises(MIRValidationError, match="increases access"):
        validate_function(fn)


@pytest.mark.parametrize("source_const,dest_const", [(False, False), (False, True), (True, True), (True, False)])
def test_record_member_construction_preserves_capability(source_const: bool, dest_const: bool) -> None:
    fn = reference_function(False, dest_const)
    # The record parameter at its type's own passing for its access.
    source = reslot(fn.slots[-2], readonly=source_const)
    # Construct directly from the record parameter, then retain the extracted
    # identity across wrapper replacement and mutate through a separate alias.
    construction = MIRAssign(MIRPlace(CURRENT), MIRUnionConstruct(0, MIRPlace(FLAG)))
    mutable = MIRSlotId(BODY, 9)
    mutable_slot = reslot(source, id=mutable, readonly=False)
    entry = replace(fn.blocks[0], statements=(construction, *fn.blocks[0].statements[1:]))
    branch = fn.blocks[1]
    write = branch.statements[2]
    branch = replace(branch, statements=(*branch.statements[:2],
        replace(write, target=replace(write.target, root=mutable)), *branch.statements[3:]))
    fn = bind_parameters(replace(fn, slots=(*fn.slots[:-2], source, fn.slots[-1], mutable_slot),
                 blocks=(entry, branch, fn.blocks[2])))
    if source_const and not dest_const:
        with pytest.raises(MIRValidationError, match="type or access mismatch"):
            validate_function(fn)
        return
    validate_function(fn)
    field = MIRFieldId(source.type, "value")
    heap = {1: {field: 2}}
    assert execute(fn, UnionValue(1, Reference(2)), UnionValue(1, Reference(2)),
                   9, Reference(1), Reference(1), heap=heap) == 9
