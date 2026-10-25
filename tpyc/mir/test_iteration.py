"""Iterator cursors and retained element aliases have independent identities."""

from dataclasses import replace

import pytest

from ..typesys import BOOL, INT32, NominalType, OptionalType, UnionType, TupleType
from .dependencies import MIRReferent
from .dump import dump_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant, MIRFunction, MIRGoto,
    MIRIteratorAdvance, MIRIteratorHasNext, MIRIteratorInit, MIRIteratorRead, MIRPlace, MIRPoint, MIRRead, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRRegion, MIRRegionId, MIRConstruct, MIRWrap,
)
from .shapes import (
    MIRContainerElements, MIRContainerStructure, MIRDeref, MIRTupleIndex, MIRUnionPayload,
)
from .value import MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRValidationError
from .reach import reaches
from .scope_lifetime import analyze_scope_ends
from .storage import analyze_storage
from .testutil import (
    hand_record, bind_parameters, ContainerValue, Reference, borrowed_shape, execute, leaf_shape, optional_shape,
    owned_shape, reslot, tuple_shape, union_shape, prepared,
)
from .prepare import validate_function

B = MIRBodyId("iteration", "retained")
P, Q, SOURCE, ITER, HAS, FIRST, CURRENT, OUT, SEVEN = (MIRSlotId(B, i) for i in range(9))
ENTRY, FIRST_BODY, NEXT, SECOND_BODY, DONE, EMPTY = (MIRBlockId(B, i) for i in range(6))
CELL = hand_record("Cell", "iteration.Cell", value=INT32)
LIST = NominalType("list", (CELL,), _module_qname="builtins.list")
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
RECORD = MIRRecordLayout(CELL, (FIELD,), True, True)
LAYOUT = (owned_shape(CELL),)


def assignment(target: MIRSlotId, value) -> MIRAssign:
    return MIRAssign(MIRPlace(target), value)


def fixture() -> MIRFunction:
    container = MIRSlot(P, borrowed_shape(LIST, members=LAYOUT), MIRSlotKind.PARAMETER)
    alias = MIRSlot(FIRST, borrowed_shape(CELL), MIRSlotKind.LOCAL)
    slots = (container, replace(container, id=Q), replace(container, id=SOURCE, kind=MIRSlotKind.LOCAL),
             replace(container, id=ITER, kind=MIRSlotKind.TEMPORARY,
                     shape=replace(container.shape, holding=MIRHolding.CURSOR)),
             MIRSlot(HAS, leaf_shape(BOOL), MIRSlotKind.TEMPORARY), alias, replace(alias, id=CURRENT),
             MIRSlot(OUT, leaf_shape(INT32), MIRSlotKind.LOCAL),
             MIRSlot(SEVEN, leaf_shape(INT32), MIRSlotKind.TEMPORARY))
    blocks = (
        MIRBlock(ENTRY, (assignment(SOURCE, MIRAlias(P)), assignment(ITER, MIRIteratorInit(SOURCE)),
                         assignment(SOURCE, MIRAlias(Q)), assignment(HAS, MIRIteratorHasNext(ITER))),
                 MIRBranch(HAS, FIRST_BODY, EMPTY)),
        MIRBlock(FIRST_BODY, (assignment(FIRST, MIRIteratorRead(ITER)),
                              assignment(ITER, MIRIteratorAdvance(ITER))), MIRGoto(NEXT)),
        MIRBlock(NEXT, (assignment(HAS, MIRIteratorHasNext(ITER)),), MIRBranch(HAS, SECOND_BODY, DONE)),
        MIRBlock(SECOND_BODY, (assignment(CURRENT, MIRIteratorRead(ITER)), assignment(SEVEN, MIRConstant(7)),
                               MIRAssign(MIRPlace(CURRENT, (MIRDeref(), FIELD)), MIRRead(MIRPlace(SEVEN)))),
                 MIRGoto(DONE)),
        MIRBlock(DONE, (assignment(OUT, MIRRead(MIRPlace(FIRST, (MIRDeref(), FIELD)))),), MIRReturn(OUT)),
        MIRBlock(EMPTY, (assignment(OUT, MIRConstant(0)),), MIRReturn(OUT)),
    )
    return bind_parameters(MIRFunction(B, INT32, slots, blocks, ENTRY, records=(RECORD,)))


def test_cursor_advance_and_source_reseat_do_not_retarget_saved_element() -> None:
    fn = fixture()
    validate_function(fn)
    heap = {1: {FIELD.id: 3}, 2: {FIELD.id: 4}, 3: {FIELD.id: 99}}
    other = ContainerValue((Reference(3),))
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), other, heap=heap) == 3
    assert heap[2][FIELD.id] == 7 and heap[3][FIELD.id] == 99
    assert execute(fn, ContainerValue(()), other, heap=heap) == 0
    assert execute(fn, ContainerValue((Reference(1),)), other, heap=heap) == 3
    live = prepared(fn).liveness
    deps = prepared(fn).dependencies
    elements = MIRReferent(MIRPlace(P, (MIRContainerElements(),)), external=True)
    structure = MIRReferent(MIRPlace(P, (MIRContainerStructure(),)), external=True)
    assert deps.referents[MIRPoint(FIRST_BODY, 0)][MIRPlace(ITER)] == {elements, structure}
    assert deps.active[MIRPoint(DONE, 0)] == {MIRPlace(FIRST): {elements}}
    assert SOURCE not in live.points[MIRPoint(FIRST_BODY, 0)]
    assert not analyze_storage(prepared(fn)).writes
    assert "iterator-advance" in dump_function(fn)
    assert analyze_scope_ends(prepared(fn)).reason == "missing emitted storage regions"
    # Another parameter's elements: two origins, the caller's oracle decides.
    assert reaches(elements, MIRReferent(MIRPlace(Q, (MIRContainerElements(),)), external=True),
                   lambda written, target: True)


def test_ending_iteration_target_does_not_end_caller_elements() -> None:
    fn = fixture()
    root, first, second = (MIRRegionId(B, i) for i in range(3))
    regions = (MIRRegion(root, None, ENTRY), MIRRegion(first, root, FIRST_BODY),
               MIRRegion(second, root, SECOND_BODY))
    fn = replace(fn, regions=regions,
                 slots=tuple(replace(s, residence=None if s.kind is MIRSlotKind.PARAMETER else
                                     second if s.id == CURRENT else root) for s in fn.slots),
                 blocks=tuple(replace(b, region=first if b.id == FIRST_BODY else
                                      second if b.id == SECOND_BODY else root) for b in fn.blocks))
    deps = prepared(fn).dependencies
    assert MIRPlace(CURRENT) not in deps.referents[MIRPoint(DONE, 0)]
    assert MIRPlace(FIRST) in deps.active[MIRPoint(DONE, 0)]
    assert not analyze_scope_ends(prepared(fn)).ends


@pytest.mark.parametrize("shape", ["optional", "union", "tuple"])
def test_aggregate_holder_retains_element_after_cursor_advance(shape: str) -> None:
    fn = fixture()
    saved = MIRSlotId(B, len(fn.slots))
    member = borrowed_shape(CELL)
    match shape:
        case "optional":
            slot = MIRSlot(saved, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.LOCAL)
            value, projection = MIRWrap(1, MIRPlace(FIRST)), MIRUnionPayload(1)
        case "union":
            other = hand_record("Other", "iteration.Other")
            fn = replace(fn, records=(*fn.records, MIRRecordLayout(other, (), True, True)))
            slot = MIRSlot(saved, union_shape(UnionType((CELL, other)), (member, borrowed_shape(other))),
                           MIRSlotKind.LOCAL)
            value, projection = MIRWrap(0, MIRPlace(FIRST)), MIRUnionPayload(0)
        case "tuple":
            slot = MIRSlot(saved, tuple_shape(TupleType((CELL,)), (member,)), MIRSlotKind.LOCAL)
            value, projection = MIRConstruct((FIRST,)), MIRTupleIndex(0)
    blocks = list(fn.blocks)
    first = blocks[1]
    blocks[1] = replace(first, statements=(first.statements[0], assignment(saved, value), first.statements[1]))
    place = MIRPlace(saved, (projection,))
    blocks[4] = replace(blocks[4], statements=(assignment(OUT, MIRRead(
        MIRPlace(saved, (projection, MIRDeref(), FIELD)))),))
    fn = replace(fn, slots=(*fn.slots, slot), blocks=tuple(blocks))
    validate_function(fn)
    heap = {1: {FIELD.id: 3}, 2: {FIELD.id: 4}}
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), ContainerValue(()), heap=heap) == 3
    assert heap[2][FIELD.id] == 7
    deps = prepared(fn).dependencies
    assert deps.active[MIRPoint(DONE, 0)] == {
        place: {MIRReferent(MIRPlace(P, (MIRContainerElements(),)), external=True)}}


@pytest.mark.parametrize("damage", ["uninitialized", "no_guard", "exhausted", "stale_guard", "bad_target",
                                    "bad_layout", "const", "iterator_copy", "layout_on_scalar", "arity"])
def test_malformed_iterator_operations_fail_closed(damage: str) -> None:
    fn = fixture()
    entry, first, nxt, second, done, empty = fn.blocks
    slots = list(fn.slots)
    match damage:
        case "uninitialized":
            entry = replace(entry, statements=(entry.statements[0], *entry.statements[2:]))
        case "no_guard":
            entry = replace(entry, terminator=MIRGoto(FIRST_BODY))
        case "exhausted":
            entry = replace(entry, terminator=MIRBranch(HAS, EMPTY, FIRST_BODY))
        case "stale_guard":
            nxt = replace(nxt, statements=())
        case "bad_target":
            first = replace(first, statements=(first.statements[0], assignment(SOURCE, MIRIteratorAdvance(ITER))))
        case "bad_layout":
            slots[3] = reslot(slots[3], members=(leaf_shape(INT32),))
        case "const":
            slots[3] = reslot(slots[3], readonly=True)
        case "iterator_copy":
            entry = replace(entry, statements=(entry.statements[0], assignment(ITER, MIRAlias(SOURCE)),
                                               *entry.statements[2:]))
        case "layout_on_scalar":
            slots[4] = reslot(slots[4], members=LAYOUT)
        case "arity":
            slots[0] = reslot(slots[0], type=replace(slots[0].type, type_args=(CELL, CELL)))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=tuple(slots), blocks=(entry, first, nxt, second, done, empty)))
