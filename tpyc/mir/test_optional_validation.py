"""Presence proofs must describe the current holder on every reaching path."""

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType, OptionalType, ReadonlyType
from .nodes import (
    MIRHolding,
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant,
    MIRFunction, MIRGoto, MIRIsPresent, MIRNot, MIROptionalConstruct,
    MIROptionalCopy, MIROptionalPayload, MIRPlace,
    MIRRead, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
)
from .testutil import OptionalValue, execute, leaf_shape, optional_shape, owned_shape, reslot
from .validate import MIRPresenceError, MIRValidationError, validate_function
from .presence import _State, _transfer

BODY = MIRBodyId("optional_validation", "example")
PARAM, CURRENT, SAVED, GUARD, COPY, VALUE, RESULT, FLAG = (MIRSlotId(BODY, i) for i in range(8))
ENTRY, YES, NO, JOIN, LOOP = (MIRBlockId(BODY, i) for i in range(5))
LAYOUT = leaf_shape(INT32)
SLOTS = (
    MIRSlot(PARAM, optional_shape(OptionalType(INT32), LAYOUT), MIRSlotKind.PARAMETER),
    MIRSlot(CURRENT, optional_shape(OptionalType(INT32), LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(SAVED, optional_shape(OptionalType(INT32), LAYOUT), MIRSlotKind.LOCAL),
    MIRSlot(GUARD, leaf_shape(BOOL), MIRSlotKind.LOCAL),
    MIRSlot(COPY, leaf_shape(BOOL), MIRSlotKind.LOCAL),
    MIRSlot(VALUE, leaf_shape(INT32), MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
    MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL),
    MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
)
CAPTURE = MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(MIRPlace(PARAM)))
TEST = MIRAssign(MIRPlace(GUARD), MIRIsPresent(MIRPlace(CURRENT)))
CLEAR = MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct())
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(CURRENT, (MIROptionalPayload(),))))
FALLBACK = MIRBlock(NO, (), MIRReturn(VALUE))


def function(*blocks: MIRBlock) -> MIRFunction:
    return MIRFunction(BODY, INT32, SLOTS, blocks, ENTRY)


@pytest.mark.parametrize("mutation", [
    CLEAR,
    MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(MIRPlace(PARAM))),
    MIRAssign(MIRPlace(GUARD), MIRConstant(True)),
    MIRAssign(MIRPlace(GUARD), MIRRead(MIRPlace(FLAG))),
])
def test_stale_test_cannot_authorize_extraction(mutation: MIRAssign) -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST, mutation), MIRBranch(GUARD, YES, NO)),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    with pytest.raises(MIRPresenceError, match="current presence proof"):
        validate_function(fn)


@pytest.mark.parametrize("negate", [False, True])
def test_boolean_copy_preserves_implication(negate: bool) -> None:
    copy = MIRAssign(MIRPlace(COPY), MIRNot(GUARD) if negate else MIRRead(MIRPlace(GUARD)))
    branch = MIRBranch(COPY, NO, YES) if negate else MIRBranch(COPY, YES, NO)
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST, copy), branch),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)), FALLBACK)
    validate_function(fn)
    for payload in (None, 0, 7):
        assert execute(fn, OptionalValue(payload), 13, False) == (13 if payload is None else payload)


def test_same_branch_target_does_not_keep_one_edges_fact() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, YES)),
                  MIRBlock(YES, (READ,), MIRReturn(RESULT)))
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


def test_join_loses_presence_when_one_path_clears() -> None:
    fn = function(
        MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (), MIRBranch(FLAG, LOOP, JOIN)),
        MIRBlock(LOOP, (CLEAR,), MIRGoto(JOIN)),
        MIRBlock(JOIN, (READ,), MIRReturn(RESULT)), FALLBACK)
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


@pytest.mark.parametrize("fresh", [False, True])
def test_loop_backedge_requires_a_new_test(fresh: bool) -> None:
    fn = function(
        MIRBlock(ENTRY, (CAPTURE, TEST), MIRGoto(LOOP)),
        MIRBlock(LOOP, (TEST,) if fresh else (), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (READ, CLEAR), MIRGoto(LOOP)), FALLBACK)
    if fresh:
        validate_function(fn)
        assert execute(fn, OptionalValue(0), 13, False) == 13
    else:
        with pytest.raises(MIRPresenceError):
            validate_function(fn)


@pytest.mark.parametrize("self_copy", [False, True])
def test_construction_and_copy_establish_presence(self_copy: bool) -> None:
    build = MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct(MIRPlace(VALUE)))
    save = MIRAssign(MIRPlace(SAVED), MIROptionalCopy(MIRPlace(CURRENT)))
    read = replace(READ, value=MIRRead(MIRPlace(SAVED, (MIROptionalPayload(),))))
    overwrite = MIRAssign(MIRPlace(SAVED), MIROptionalCopy(MIRPlace(SAVED))) if self_copy else CLEAR
    fn = function(MIRBlock(ENTRY, (build, save, overwrite, read), MIRReturn(RESULT)))
    validate_function(fn)
    assert execute(fn, OptionalValue(None), 0, False) == 0


def test_unconditional_payload_access_is_rejected() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, READ), MIRReturn(RESULT)))
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


@pytest.mark.parametrize("bad", [
    reslot(SLOTS[0], members=()),
    reslot(SLOTS[0], members=(leaf_shape(BOOL),)),
    reslot(SLOTS[0], members=(leaf_shape(INT32, True),)),
    reslot(SLOTS[0], type=OptionalType(INT32, force_pointer_repr=True)),
    reslot(SLOTS[0], holding=MIRHolding.POINTER),
])
def test_malformed_optional_layout_is_rejected(bad: MIRSlot) -> None:
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=(bad, *fn.slots[1:])))


def test_a_slot_payload_is_never_inline_storage() -> None:
    # A slot's payload is an inert leaf or a pointer at a record; a record
    # stored inline is an Optional field's, never a slot's.
    cell = NominalType("Cell", _module_qname="optional_validation.Cell")
    inline = reslot(SLOTS[0], type=OptionalType(cell), members=(owned_shape(cell),))
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError, match="unsupported optional reference"):
        validate_function(replace(fn, slots=(inline, *fn.slots[1:])))


@pytest.mark.parametrize("stmt", [
    MIRAssign(MIRPlace(PARAM), MIROptionalConstruct()),
    MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct(MIRPlace(FLAG))),
    MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(MIRPlace(VALUE))),
    MIRAssign(MIRPlace(CURRENT, (MIROptionalPayload(),)), MIRConstant(1)),
    MIRAssign(MIRPlace(GUARD), MIRIsPresent(MIRPlace(VALUE))),
])
def test_malformed_optional_operations_are_rejected(stmt: MIRAssign) -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, stmt), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError):
        validate_function(fn)


def test_join_recovers_conditional_proof_from_predecessor_facts() -> None:
    fn = function(
        MIRBlock(ENTRY, (CAPTURE, TEST), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (MIRAssign(MIRPlace(COPY), MIRRead(MIRPlace(FLAG))),), MIRGoto(JOIN)),
        MIRBlock(NO, (MIRAssign(MIRPlace(COPY), MIRConstant(False)),), MIRGoto(JOIN)),
        MIRBlock(JOIN, (), MIRBranch(COPY, LOOP, MIRBlockId(BODY, 5))),
        MIRBlock(LOOP, (READ,), MIRReturn(RESULT)),
        MIRBlock(MIRBlockId(BODY, 5), (), MIRReturn(VALUE)))
    validate_function(fn)
    for payload in (None, 0, 7):
        for flag in (False, True):
            assert execute(fn, OptionalValue(payload), 13, flag) == (
                payload if payload is not None and flag else 13)


def test_unrelated_boolean_results_do_not_duplicate_holder_facts() -> None:
    holders = tuple(MIRSlotId(BODY, i + 100) for i in range(100))
    booleans = {MIRSlotId(BODY, i + 200) for i in range(100)}
    state = _State()
    for holder in holders:
        state = _transfer(state, MIRAssign(MIRPlace(holder), MIROptionalConstruct(MIRPlace(VALUE))), booleans)
    for boolean in booleans:
        state = _transfer(state, MIRAssign(MIRPlace(boolean), MIRRead(MIRPlace(FLAG))), booleans)
    # Repeated reseats must not scan a copy of every holder fact per boolean.
    assert len(state.present) == len(holders)
    assert not state.conditions
    for holder in holders:
        state = _transfer(state, MIRAssign(MIRPlace(holder), MIROptionalConstruct()), booleans)
    assert state.present == frozenset((MIRPlace(holder), frozenset({0})) for holder in holders)


def test_a_member_type_is_bare_its_access_is_the_member_flag() -> None:
    wrapped = reslot(SLOTS[0], members=(leaf_shape(ReadonlyType(INT32)),))
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError, match="readonly-wrapped value type"):
        validate_function(replace(fn, slots=(wrapped, *fn.slots[1:])))


def test_members_belong_to_wrappers_and_containers_at_every_depth() -> None:
    nested = reslot(SLOTS[0], members=(replace(leaf_shape(INT32), members=(leaf_shape(INT32),)),))
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError, match="members on a memberless value"):
        validate_function(replace(fn, slots=(nested, *fn.slots[1:])))
