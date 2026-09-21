"""Iterator cursors and retained element aliases have independent identities."""

from dataclasses import replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, NominalType, OptionalType, UnionType, TupleType
from .dependencies import MIRReferent, analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant,
    MIRContainerElements, MIRContainerLayout, MIRContainerStructure, MIRDeref,
    MIRField, MIRFieldId, MIRFunction, MIRGoto, MIRIteratorAdvance, MIRIteratorHasNext,
    MIRIteratorInit, MIRIteratorRead, MIRPlace, MIRPoint, MIRRead, MIRRecordLayout,
    MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleElement, MIRValueKind,
    MIRRegion, MIRRegionId,
    MIROptionalConstruct, MIROptionalLayout, MIROptionalPayload,
    MIRUnionConstruct, MIRUnionLayout, MIRUnionPayload,
    MIRTupleConstruct, MIRTupleLayout, MIRTupleIndex,
)
from .retention import may_overlap
from .scope_lifetime import analyze_scope_ends
from .storage import analyze_storage
from .testutil import ContainerValue, Reference, execute
from .validate import MIRValidationError, validate_function

B = MIRBodyId("iteration", "retained")
P, Q, SOURCE, ITER, HAS, FIRST, CURRENT, OUT, SEVEN = (MIRSlotId(B, i) for i in range(9))
ENTRY, FIRST_BODY, NEXT, SECOND_BODY, DONE, EMPTY = (MIRBlockId(B, i) for i in range(6))
CELL = NominalType("Cell", _module_qname="iteration.Cell")
LIST = NominalType("list", (CELL,), _module_qname="builtins.list")
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
RECORD = MIRRecordLayout(CELL, (FIELD,), True, True)
LAYOUT = MIRContainerLayout(MIRTupleElement(CELL, MIRValueKind.BORROWED_RECORD))


def assignment(target: MIRSlotId, value) -> MIRAssign:
    return MIRAssign(MIRPlace(target), value)


def fixture() -> MIRFunction:
    container = MIRSlot(P, LIST, MIRSlotKind.PARAMETER, form=Form.BORROW,
                        value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=LAYOUT)
    alias = MIRSlot(FIRST, CELL, MIRSlotKind.LOCAL, form=Form.BORROW,
                    value_kind=MIRValueKind.BORROWED_RECORD)
    slots = (container, replace(container, id=Q), replace(container, id=SOURCE, kind=MIRSlotKind.LOCAL),
             replace(container, id=ITER, kind=MIRSlotKind.TEMPORARY, value_kind=MIRValueKind.NATIVE_ITERATOR),
             MIRSlot(HAS, BOOL, MIRSlotKind.TEMPORARY), alias, replace(alias, id=CURRENT),
             MIRSlot(OUT, INT32, MIRSlotKind.LOCAL), MIRSlot(SEVEN, INT32, MIRSlotKind.TEMPORARY))
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
    return MIRFunction(B, INT32, slots, blocks, ENTRY, records=(RECORD,))


def test_cursor_advance_and_source_reseat_do_not_retarget_saved_element() -> None:
    fn = fixture()
    validate_function(fn)
    heap = {1: {FIELD.id: 3}, 2: {FIELD.id: 4}, 3: {FIELD.id: 99}}
    other = ContainerValue((Reference(3),))
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), other, heap=heap) == 3
    assert heap[2][FIELD.id] == 7 and heap[3][FIELD.id] == 99
    assert execute(fn, ContainerValue(()), other, heap=heap) == 0
    assert execute(fn, ContainerValue((Reference(1),)), other, heap=heap) == 3
    live = analyze_liveness(fn)
    deps = analyze_dependencies(fn, live)
    elements = MIRReferent(MIRPlace(P, (MIRContainerElements(),)), external=True)
    structure = MIRReferent(MIRPlace(P, (MIRContainerStructure(),)), external=True)
    assert deps.referents[MIRPoint(FIRST_BODY, 0)][MIRPlace(ITER)] == {elements, structure}
    assert deps.active[MIRPoint(DONE, 0)] == {MIRPlace(FIRST): {elements}}
    assert SOURCE not in live.points[MIRPoint(FIRST_BODY, 0)]
    assert not analyze_storage(fn).writes
    assert "iterator-advance" in dump_function(fn)
    assert analyze_scope_ends(fn).reason == "missing emitted storage regions"
    assert may_overlap(elements, MIRReferent(MIRPlace(Q, (MIRContainerElements(),)), external=True))


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
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    assert MIRPlace(CURRENT) not in deps.referents[MIRPoint(DONE, 0)]
    assert MIRPlace(FIRST) in deps.active[MIRPoint(DONE, 0)]
    assert not analyze_scope_ends(fn).ends


@pytest.mark.parametrize("shape", ["optional", "union", "tuple"])
def test_aggregate_holder_retains_element_after_cursor_advance(shape: str) -> None:
    fn = fixture()
    saved = MIRSlotId(B, len(fn.slots))
    member = MIRTupleElement(CELL, MIRValueKind.BORROWED_RECORD)
    match shape:
        case "optional":
            slot = MIRSlot(saved, OptionalType(CELL), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
                           optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED_RECORD))
            value, projection = MIROptionalConstruct(FIRST), MIROptionalPayload()
        case "union":
            other = NominalType("Other", _module_qname="iteration.Other")
            fn = replace(fn, records=(*fn.records, MIRRecordLayout(other, (), True, True)))
            slot = MIRSlot(saved, UnionType((CELL, other)), MIRSlotKind.LOCAL, value_kind=MIRValueKind.UNION,
                           union_layout=MIRUnionLayout((member, MIRTupleElement(other, MIRValueKind.BORROWED_RECORD))))
            value, projection = MIRUnionConstruct(0, FIRST), MIRUnionPayload(0)
        case "tuple":
            slot = MIRSlot(saved, TupleType((CELL,)), MIRSlotKind.LOCAL, value_kind=MIRValueKind.TUPLE,
                           tuple_layout=MIRTupleLayout((member,)))
            value, projection = MIRTupleConstruct((FIRST,)), MIRTupleIndex(0)
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
    deps = analyze_dependencies(fn, analyze_liveness(fn))
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
            slots[3] = replace(slots[3], container_layout=MIRContainerLayout(MIRTupleElement(INT32)))
        case "const":
            slots[3] = replace(slots[3], readonly=True)
        case "iterator_copy":
            entry = replace(entry, statements=(entry.statements[0], assignment(ITER, MIRAlias(SOURCE)),
                                               *entry.statements[2:]))
        case "layout_on_scalar":
            slots[4] = replace(slots[4], container_layout=LAYOUT)
        case "arity":
            slots[0] = replace(slots[0], type=replace(slots[0].type, type_args=(CELL, CELL)))
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, slots=tuple(slots), blocks=(entry, first, nxt, second, done, empty)))
