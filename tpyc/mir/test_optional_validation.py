"""Presence proofs must describe the current holder on every reaching path."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, OptionalType
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant,
    MIRFunction, MIRGoto, MIRIsPresent, MIRNot, MIROptionalConstruct,
    MIROptionalCopy, MIROptionalLayout, MIROptionalPayload, MIRPlace,
    MIRRead, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
)
from .testutil import OptionalValue, execute
from .validate import MIRPresenceError, MIRValidationError, validate_function
from .presence import _State, _transfer

BODY = MIRBodyId("optional_validation", "example")
PARAM, CURRENT, SAVED, GUARD, COPY, VALUE, RESULT, FLAG = (MIRSlotId(BODY, i) for i in range(8))
ENTRY, YES, NO, JOIN, LOOP = (MIRBlockId(BODY, i) for i in range(5))
LAYOUT = MIROptionalLayout(INT32)
SLOTS = (
    MIRSlot(PARAM, OptionalType(INT32), MIRSlotKind.PARAMETER,
            value_kind=MIRValueKind.OPTIONAL, optional_layout=LAYOUT),
    MIRSlot(CURRENT, OptionalType(INT32), MIRSlotKind.LOCAL,
            value_kind=MIRValueKind.OPTIONAL, optional_layout=LAYOUT),
    MIRSlot(SAVED, OptionalType(INT32), MIRSlotKind.LOCAL,
            value_kind=MIRValueKind.OPTIONAL, optional_layout=LAYOUT),
    MIRSlot(GUARD, BOOL, MIRSlotKind.LOCAL),
    MIRSlot(COPY, BOOL, MIRSlotKind.LOCAL),
    MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER),
    MIRSlot(RESULT, INT32, MIRSlotKind.LOCAL),
    MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER),
)
CAPTURE = MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(PARAM))
TEST = MIRAssign(MIRPlace(GUARD), MIRIsPresent(CURRENT))
CLEAR = MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct())
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(CURRENT, (MIROptionalPayload(),))))
FALLBACK = MIRBlock(NO, (), MIRReturn(VALUE))


def function(*blocks: MIRBlock) -> MIRFunction:
    return MIRFunction(BODY, INT32, SLOTS, blocks, ENTRY)


@pytest.mark.parametrize("mutation", [
    CLEAR,
    MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(PARAM)),
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
    build = MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct(VALUE))
    save = MIRAssign(MIRPlace(SAVED), MIROptionalCopy(CURRENT))
    read = replace(READ, value=MIRRead(MIRPlace(SAVED, (MIROptionalPayload(),))))
    overwrite = MIRAssign(MIRPlace(SAVED), MIROptionalCopy(SAVED)) if self_copy else CLEAR
    fn = function(MIRBlock(ENTRY, (build, save, overwrite, read), MIRReturn(RESULT)))
    validate_function(fn)
    assert execute(fn, OptionalValue(None), 0, False) == 0


def test_unconditional_payload_access_is_rejected() -> None:
    fn = function(MIRBlock(ENTRY, (CAPTURE, READ), MIRReturn(RESULT)))
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


@pytest.mark.parametrize("bad", [
    replace(SLOTS[0], optional_layout=None),
    replace(SLOTS[0], optional_layout=MIROptionalLayout(BOOL)),
    replace(SLOTS[0], optional_layout=MIROptionalLayout(INT32, readonly=True)),
    replace(SLOTS[0], optional_layout=MIROptionalLayout(INT32, MIRValueKind.RECORD_STORAGE)),
    replace(SLOTS[0], type=OptionalType(INT32, force_pointer_repr=True)),
    replace(SLOTS[0], form=Form.BORROW),
])
def test_malformed_optional_layout_is_rejected(bad: MIRSlot) -> None:
    fn = function(MIRBlock(ENTRY, (), MIRReturn(VALUE)))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=(bad, *fn.slots[1:])))


@pytest.mark.parametrize("stmt", [
    MIRAssign(MIRPlace(PARAM), MIROptionalConstruct()),
    MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct(FLAG)),
    MIRAssign(MIRPlace(CURRENT), MIROptionalCopy(VALUE)),
    MIRAssign(MIRPlace(CURRENT, (MIROptionalPayload(),)), MIRConstant(1)),
    MIRAssign(MIRPlace(GUARD), MIRIsPresent(VALUE)),
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
        state = _transfer(state, MIRAssign(MIRPlace(holder), MIROptionalConstruct(VALUE)), booleans)
    for boolean in booleans:
        state = _transfer(state, MIRAssign(MIRPlace(boolean), MIRRead(MIRPlace(FLAG))), booleans)
    # Repeated reseats must not scan a copy of every holder fact per boolean.
    assert len(state.present) == len(holders)
    assert not state.conditions
    for holder in holders:
        state = _transfer(state, MIRAssign(MIRPlace(holder), MIROptionalConstruct()), booleans)
    assert state.present == frozenset((holder, frozenset({0})) for holder in holders)
