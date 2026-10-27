"""Replacement conflicts use retained payloads after RHS evaluation, not old uses."""

from dataclasses import dataclass, replace

import pytest

from ..typesys import BOOL, INT32, OptionalType, TupleType, UnionType
from .dependencies import MIRDependencies, MIRReferent
from .liveness import MIRPoint
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIRFunction,
    MIRGoto, MIRNotCovered, MIRPlace, MIRRead, MIRStorageWrite, MIRStorageWriteMode, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRStorageDuration, MIRConstruct, MIRWrap, MIRIsAlternative, MIRCopy, MIRRegion, MIRRegionId,
    MIRStorageInit, MIRCall,
)
from .shapes import MIRContainerElements, MIRDeref, MIRTupleIndex, MIRUnionPayload
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .call_contract import MIRAliasObligation, MIRParameterWrite
from .retention import (
    MIRRetention, MIRRetentionConflict, _one_object, analyze_retention, dump_retention,
)
from .storage import MIRStorageEvents, analyze_storage
from .test_reuse import CELL_SOURCE, COPY_SOURCE, loop_source, source_function
from .testutil import (
    construction, hand_record, bind_parameters, borrowed_shape, execute, leaf_shape, optional_shape, owned_shape, reslot,
    tuple_shape, union_shape, prepared,
)
from ..thir.scalar_leaves import MIRClass
from .reach import reaches


B = MIRBodyId("retention", "loop")
N, FLAG, INITIAL, SITE, CURRENT, TEMP, SAVED, COPIED, OUT, BUILT = (MIRSlotId(B, i) for i in range(10))
ROOT_R, LOOP_R = MIRRegionId(B, 0), MIRRegionId(B, 1)
A, LOOP, AGAIN, END = (MIRBlockId(B, i) for i in range(4))
CELL = hand_record("Cell", "retention.Cell", value=INT32)
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
LAYOUT = MIRRecordLayout(CELL, (FIELD,), True, True)


def analyze(fn: MIRFunction) -> MIRRetention:
    result = analyze_retention(prepared(fn))
    assert isinstance(result, MIRRetention), result
    return result


def reference(sid: MIRSlotId, *, parameter: bool = False) -> MIRSlot:
    return MIRSlot(sid, borrowed_shape(CELL), MIRSlotKind.PARAMETER if parameter else MIRSlotKind.LOCAL)


def loop_function(shape: str, *, safe: bool = False, optional_owner: bool = False,
                  copied: bool = False) -> tuple[MIRFunction, MIRPoint, MIRPlace]:
    member = borrowed_shape(CELL)
    match shape:
        case "record":
            holder = reference(SAVED)
            wrap, copy = MIRAlias(TEMP), MIRAlias(SAVED)
            path = ()
        case "singleton" | "mixed":
            mixed = shape == "mixed"
            layout = (member, leaf_shape(INT32)) if mixed else (member,)
            holder = MIRSlot(SAVED, tuple_shape(TupleType(tuple(m.type for m in layout)), layout),
                             MIRSlotKind.LOCAL)
            wrap, copy = MIRConstruct((TEMP, N) if mixed else (TEMP,)), MIRCopy(MIRPlace(SAVED))
            path = (MIRTupleIndex(0),)
        case "optional":
            holder = MIRSlot(SAVED, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.LOCAL)
            wrap, copy = MIRWrap(1, MIRPlace(TEMP)), MIRCopy(MIRPlace(SAVED))
            path = (MIRUnionPayload(1),)
        case "union":
            other = hand_record("Other", "retention.Other")
            holder = MIRSlot(SAVED, union_shape(UnionType((CELL, other)), (member, borrowed_shape(other))),
                             MIRSlotKind.LOCAL)
            wrap, copy = MIRWrap(0, MIRPlace(TEMP)), MIRCopy(MIRPlace(SAVED))
            path = (MIRUnionPayload(0),)
        case _:
            raise AssertionError(shape)
    current = (MIRSlot(CURRENT, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.LOCAL)
               if optional_owner else reference(CURRENT))
    storage = MIRSlot(INITIAL, owned_shape(CELL), MIRSlotKind.TEMPORARY, storage_duration=MIRStorageDuration.BODY,
                      residence=ROOT_R)
    site = MIRSlot(SITE, optional_shape(OptionalType(CELL), owned_shape(CELL)), MIRSlotKind.LOCAL,
                   storage_duration=MIRStorageDuration.BODY, residence=ROOT_R)
    built = MIRSlot(BUILT, owned_shape(CELL), MIRSlotKind.TEMPORARY, storage_duration=LOOP_R, residence=LOOP_R)
    slots = (MIRSlot(N, leaf_shape(INT32), MIRSlotKind.PARAMETER),
             MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
             storage, site, replace(current, residence=ROOT_R), replace(reference(TEMP), residence=ROOT_R),
             replace(holder, residence=ROOT_R), replace(holder, id=COPIED, residence=ROOT_R),
             MIRSlot(OUT, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=ROOT_R), built)
    initial = MIRAssign(MIRPlace(INITIAL), construction(LAYOUT, N),
                        storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
    borrow = MIRAssign(MIRPlace(TEMP), MIRBorrow(MIRPlace(INITIAL)))
    set_current = MIRAssign(MIRPlace(CURRENT), MIRWrap(1, MIRPlace(TEMP)) if optional_owner else MIRAlias(TEMP))
    capture = MIRAssign(MIRPlace(SAVED), MIRCopy(MIRPlace(CURRENT)) if optional_owner else wrap)
    copy_holder = MIRAssign(MIRPlace(COPIED), copy)
    observed = MIRPlace(COPIED if copied else SAVED, path)
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(observed.root, (*path, MIRDeref(), FIELD))))
    write = (MIRAssign(MIRPlace(BUILT), construction(LAYOUT, N), storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),
             MIRAssign(MIRPlace(SITE), MIRWrap(1, MIRPlace(BUILT)), storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)))
    replacement = (*write, replace(borrow, value=MIRBorrow(MIRPlace(SITE, (MIRUnionPayload(1),)))), set_current)
    reads = (read,) if not safe else ()
    captures = (capture, copy_holder) if copied else (capture,)
    statements = ((read,) if safe else ()) + replacement + reads + captures
    fn = bind_parameters(MIRFunction(B, INT32, slots, (
        MIRBlock(A, (MIRStorageInit(MIRPlace(SITE), 0, MIRConstant(None)), initial, borrow, set_current, capture,
                     copy_holder), MIRGoto(LOOP), ROOT_R),
        MIRBlock(LOOP, statements, MIRBranch(FLAG, AGAIN, END), LOOP_R),
        MIRBlock(AGAIN, (MIRAssign(MIRPlace(FLAG), MIRConstant(False)), MIRAssign(MIRPlace(N), MIRConstant(2))),
                 MIRGoto(LOOP), ROOT_R),
        MIRBlock(END, (), MIRReturn(OUT), ROOT_R),
    ), A, (LAYOUT,), regions=(MIRRegion(ROOT_R, None, A), MIRRegion(LOOP_R, ROOT_R, LOOP))))
    # The wrap of the site is the replacing statement: one after the built record.
    return fn, MIRPoint(LOOP, 2 if safe else 1), observed


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("copied", [False, True])
def test_all_holder_shapes_keep_the_old_object(shape: str, safe: bool, copied: bool) -> None:
    fn, point, holder = loop_function(shape, safe=safe, copied=copied)
    result = analyze(fn)
    expected = () if safe else (MIRRetentionConflict(point, MIRReferent(MIRPlace(SITE, (MIRUnionPayload(1),))),
                                                    holder, MIRReferent(MIRPlace(SITE, (MIRUnionPayload(1),)))),)
    assert result.conflicts == expected
    assert execute(fn, 1, True) == (1 if safe else 2)
    assert result == analyze(fn)
    assert dump_retention(result) == dump_retention(analyze(fn))


@pytest.mark.parametrize("safe", [False, True])
def test_optional_own_rebind_kills_destination_and_intermediate_old_values(safe: bool) -> None:
    fn, point, holder = loop_function("optional", safe=safe, optional_owner=True)
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == (set() if safe else {holder})
    assert all(c.point == point for c in result.conflicts)
    assert execute(fn, 1, True) == (1 if safe else 2)


@pytest.mark.parametrize("safe", [False, True])
def test_real_source_detects_only_late_alias_read(safe: bool) -> None:
    fn = source_function(loop_source(False, safe))
    result = analyze(fn)
    assert len(result.conflicts) == (0 if safe else 1)
    if not safe:
        slots = {slot.id: slot for slot in fn.slots}
        assert slots[result.conflicts[0].holder.root].name == "saved"
    assert "no lifetime-safety verdict" in dump_retention(result)


def in_place_function(*, live_alias: bool, rhs_only: bool = False) -> MIRFunction:
    slots = (reference(CURRENT, parameter=True), reference(SAVED),
             MIRSlot(N, leaf_shape(INT32), MIRSlotKind.PARAMETER),
             MIRSlot(OUT, leaf_shape(INT32), MIRSlotKind.LOCAL))
    alias = MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT))
    read_alias = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    write = MIRAssign(MIRPlace(CURRENT, (MIRDeref(),)), construction(LAYOUT, OUT if rhs_only else N),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, CURRENT))
    after = read_alias if live_alias else MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(CURRENT, (MIRDeref(), FIELD))))
    statements = (alias, *((read_alias,) if rhs_only else ()), write, after)
    return bind_parameters(MIRFunction(B, INT32, slots, (MIRBlock(A, statements, MIRReturn(OUT)),), A, (LAYOUT,)))


@pytest.mark.parametrize("live_alias", [False, True])
@pytest.mark.parametrize("rhs_only", [False, True])
def test_in_place_exempts_owner_but_not_other_live_alias(live_alias: bool, rhs_only: bool) -> None:
    result = analyze(in_place_function(live_alias=live_alias, rhs_only=rhs_only))
    assert {c.holder for c in result.conflicts} == ({MIRPlace(SAVED)} if live_alias else set())
    assert all(c.affected == c.retained == MIRReferent(MIRPlace(CURRENT), True) for c in result.conflicts)


def test_readonly_alias_still_retains_the_old_object() -> None:
    fn = in_place_function(live_alias=True)
    fn = bind_parameters(replace(fn, slots=tuple(reslot(s, readonly=True) if s.id == SAVED else s for s in fn.slots)))
    assert {c.holder for c in analyze(fn).conflicts} == {MIRPlace(SAVED)}


@pytest.mark.parametrize("live_alias", [False, True])
def test_optional_in_place_exempts_only_its_own_holder(live_alias: bool) -> None:
    fn = in_place_function(live_alias=live_alias)
    optional = MIRSlot(CURRENT, optional_shape(OptionalType(CELL), borrowed_shape(CELL)), MIRSlotKind.PARAMETER)
    path = (MIRUnionPayload(1), MIRDeref())
    alias, write, read = fn.blocks[0].statements
    alias = replace(alias, value=MIRBorrow(MIRPlace(CURRENT, path)))
    write = replace(write, target=MIRPlace(CURRENT, path))
    if not live_alias:
        read = replace(read, value=MIRRead(MIRPlace(CURRENT, (*path, FIELD))))
    fn = bind_parameters(replace(fn, slots=tuple(optional if s.id == CURRENT else s for s in fn.slots) + (
        MIRSlot(FLAG, leaf_shape(BOOL), MIRSlotKind.LOCAL),), blocks=(
        MIRBlock(A, (MIRAssign(MIRPlace(FLAG), MIRIsAlternative(MIRPlace(CURRENT), (1,))),), MIRBranch(FLAG, LOOP, END)),
        MIRBlock(LOOP, (alias, write, read), MIRReturn(OUT)),
        MIRBlock(END, (MIRAssign(MIRPlace(OUT), MIRConstant(0)),), MIRReturn(OUT)),
    )))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == ({MIRPlace(SAVED)} if live_alias else set())
    # An Optional parameter's payload is its own origin outside the body (`origin`).
    assert all(c.affected == MIRReferent(MIRPlace(CURRENT, (MIRUnionPayload(1),)), True) for c in result.conflicts)


def test_scalar_field_write_is_shared_mutation_not_replacement() -> None:
    fn = in_place_function(live_alias=True)
    statements = fn.blocks[0].statements
    mutate = MIRAssign(MIRPlace(CURRENT, (MIRDeref(), FIELD)), MIRRead(MIRPlace(N)))
    fn = replace(fn, blocks=(replace(fn.blocks[0], statements=(statements[0], mutate, statements[-1])),))
    assert analyze(fn).conflicts == ()


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("copied", [False, True])
def test_overwriting_one_holder_does_not_drop_a_copied_loan(shape: str, copied: bool) -> None:
    fn, _, holder = loop_function(shape, copied=copied)
    block = fn.blocks[1]
    capture = next(s for s in block.statements if s.target == MIRPlace(SAVED))
    prelude = (MIRAssign(MIRPlace(TEMP), MIRBorrow(MIRPlace(INITIAL))), capture)
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(*prelude, *block.statements)), *fn.blocks[2:]))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == ({holder} if copied else set())
    assert execute(fn, 1, True) == (2 if copied else 1)


@pytest.mark.parametrize("clear_copy", [False, True])
def test_none_clear_removes_only_the_selected_optional_holder(clear_copy: bool) -> None:
    fn, _, holder = loop_function("optional", copied=True)
    block = fn.blocks[1]
    statements = list(block.statements)
    if clear_copy:
        read_index = next(i for i, s in enumerate(statements) if s.target.root == OUT)
        statements[read_index] = MIRAssign(MIRPlace(OUT), MIRIsAlternative(MIRPlace(COPIED), (1,)))
        fn = bind_parameters(replace(fn, return_type=BOOL, slots=tuple(reslot(s, type=BOOL) if s.id == OUT else s for s in fn.slots)))
    clear = MIRAssign(MIRPlace(COPIED if clear_copy else SAVED), MIRWrap(0))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(clear, *statements)), *fn.blocks[2:]))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == (set() if clear_copy else {holder})
    assert execute(fn, 1, True) == (False if clear_copy else 2)


def test_distinct_external_parameters_may_alias() -> None:
    fn = in_place_function(live_alias=True)
    fn = bind_parameters(replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.PARAMETER) if s.id == SAVED else s for s in fn.slots),
                 blocks=(replace(fn.blocks[0], statements=fn.blocks[0].statements[1:]),)))
    conflict, = analyze(fn).conflicts
    assert conflict.affected == MIRReferent(MIRPlace(CURRENT), True)
    assert conflict.retained == MIRReferent(MIRPlace(SAVED), True)


def test_two_payloads_of_one_parameter_may_alias() -> None:
    fn = in_place_function(live_alias=True)
    member = borrowed_shape(CELL)
    pair = MIRSlot(TEMP, tuple_shape(TupleType((CELL, CELL)), (member, member)), MIRSlotKind.PARAMETER)
    prelude = tuple(MIRAssign(MIRPlace(dest), MIRBorrow(MIRPlace(TEMP, (MIRTupleIndex(i), MIRDeref()))))
                    for i, dest in enumerate((CURRENT, SAVED)))
    fn = bind_parameters(replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.LOCAL) if s.id == CURRENT else s for s in fn.slots) + (pair,),
                 blocks=(replace(fn.blocks[0], statements=(*prelude, *fn.blocks[0].statements[1:])),)))
    conflict, = analyze(fn).conflicts
    assert conflict.affected == MIRReferent(MIRPlace(TEMP, (MIRTupleIndex(0),)), True)
    assert conflict.retained == MIRReferent(MIRPlace(TEMP, (MIRTupleIndex(1),)), True)


@pytest.mark.parametrize("same_field", [False, True])
def test_inline_siblings_are_disjoint_but_same_field_is_retained(same_field: bool) -> None:
    fn = in_place_function(live_alias=True)
    parent = hand_record("Parent", "retention.Parent")
    left = MIRField(MIRFieldId(parent, "left"), CELL)
    right = MIRField(MIRFieldId(parent, "right"), CELL)
    param = reslot(reference(TEMP, parameter=True), type=parent)
    prelude = (
        MIRAssign(MIRPlace(CURRENT), MIRBorrow(MIRPlace(TEMP, (MIRDeref(), left)))),
        MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(TEMP, (MIRDeref(), left if same_field else right)))),
    )
    fn = bind_parameters(replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.LOCAL) if s.id == CURRENT else s for s in fn.slots) + (param,),
                 blocks=(replace(fn.blocks[0], statements=(*prelude, *fn.blocks[0].statements[1:])),)),
                         fields=(left, right))
    assert {c.holder for c in analyze(fn).conflicts} == ({MIRPlace(SAVED)} if same_field else set())


def test_unreachable_replacement_does_not_create_a_conflict() -> None:
    fn = in_place_function(live_alias=True)
    unreachable = replace(fn.blocks[0], id=END)
    fn = replace(fn, blocks=(replace(fn.blocks[0], statements=(
        MIRAssign(MIRPlace(OUT), MIRConstant(0)),)), unreachable))
    assert analyze(fn).conflicts == ()


def test_branch_join_reports_possible_retention_without_merging_private_roots() -> None:
    fn, _, _ = loop_function("record")
    entry = fn.blocks[0]
    # The site's sum filled once, outside any loop: its record built in body storage.
    initial_site = (MIRAssign(MIRPlace(BUILT), construction(LAYOUT, N),
                              storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),
                    MIRAssign(MIRPlace(SITE), MIRWrap(1, MIRPlace(BUILT)),
                              storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)))
    write = MIRAssign(MIRPlace(CURRENT, (MIRDeref(),)), construction(LAYOUT, N),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, CURRENT))
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = replace(fn, slots=tuple(replace(s, storage_duration=MIRStorageDuration.BODY, residence=ROOT_R)
                                 if s.id == BUILT else s for s in fn.slots),
                 regions=fn.regions[:1], blocks=(
        replace(entry, statements=(*entry.statements, *initial_site), terminator=MIRBranch(FLAG, LOOP, AGAIN)),
        MIRBlock(LOOP, (MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT)),), MIRGoto(END), ROOT_R),
        MIRBlock(AGAIN, (MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(SITE, (MIRUnionPayload(1),)))),), MIRGoto(END),
                 ROOT_R),
        MIRBlock(END, (write, read), MIRReturn(OUT), ROOT_R),
    ))
    assert analyze(fn).conflicts == (MIRRetentionConflict(
        MIRPoint(END, 0), MIRReferent(MIRPlace(INITIAL)), MIRPlace(SAVED), MIRReferent(MIRPlace(INITIAL))),)


def test_mixed_analysis_instances_are_rejected_and_uncovered_stays_uncovered() -> None:
    fn, _, _ = loop_function("record")
    ready = prepared(fn)
    assert isinstance(ready.dependencies, MIRDependencies) and isinstance(analyze_storage(ready), MIRStorageEvents)
    other = replace(fn)
    with pytest.raises(MIRValidationError, match="different MIR function"):
        analyze_retention(replace(ready, dependencies=replace(ready.dependencies, function=other)))
    missing = MIRNotCovered(B, "dependencies", "missing duration")
    result = analyze_retention(replace(ready, dependencies=missing))
    assert isinstance(result, MIRNotCovered)
    assert dump_retention(result).startswith("retention not covered:")


def test_actual_missing_write_metadata_cannot_be_an_empty_conflict_result() -> None:
    fn = in_place_function(live_alias=True)
    fn = replace(fn, blocks=(replace(fn.blocks[0], statements=tuple(
        replace(s, storage_write=None) for s in fn.blocks[0].statements)),))
    # A record rebuilt by a call states its storage fact: without one the
    # body is refused before retention could read it as conflict-free.
    with pytest.raises(MIRValidationError, match="call needs whole result holder"):
        analyze_retention(prepared(fn))
    # A copy into record storage that states no fact still validates:
    # retention cannot tell what it replaces, so it is not covered rather
    # than conflict-free.
    fn = source_function(CELL_SOURCE + COPY_SOURCE, "copied")
    entry = fn.blocks[0]
    copy, = (s for s in entry.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIRCopy))
    fn = replace(fn, blocks=(replace(entry, statements=tuple(
        replace(s, storage_write=None) if s is copy else s for s in entry.statements)), *fn.blocks[1:]))
    result = analyze_retention(prepared(fn))
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "storage: missing storage write fact"


def _any(written: MIRReferent, target: MIRReferent) -> bool:
    return True


def _none(written: MIRReferent, target: MIRReferent) -> bool:
    return False


def test_reach_distinguishes_payload_origins_from_inline_fields() -> None:
    other_field = MIRField(MIRFieldId(CELL, "other"), INT32)
    root = MIRPlace(CURRENT)
    child = MIRPlace(CURRENT, (FIELD,))
    sibling = MIRPlace(CURRENT, (other_field,))
    for external in (False, True):
        # A whole write reaches what lies under its place, never the storage around it.
        assert reaches(MIRReferent(root, external), MIRReferent(child, external), _any)
        assert not reaches(MIRReferent(child, external), MIRReferent(root, external), _any)
        assert not reaches(MIRReferent(child, external), MIRReferent(sibling, external), _any)
    assert not reaches(MIRReferent(root), MIRReferent(root, True), _any)
    assert not reaches(MIRReferent(root), MIRReferent(MIRPlace(SAVED)), _any)
    # Two external origins may be one object: the caller's oracle decides.
    assert reaches(MIRReferent(root, True), MIRReferent(MIRPlace(SAVED), True), _any)
    assert not reaches(MIRReferent(root, True), MIRReferent(MIRPlace(SAVED), True), _none)
    # Two payloads of one parameter are two origins: they can point at the same object or parent/child.
    for left, right in ((MIRTupleIndex(0), MIRTupleIndex(1)),
                        (MIRUnionPayload(0), MIRUnionPayload(1))):
        written = MIRReferent(MIRPlace(CURRENT, (left, FIELD)), True)
        target = MIRReferent(MIRPlace(CURRENT, (right, other_field)), True)
        assert reaches(written, target, _any) and not reaches(written, target, _none)


@dataclass(frozen=True)
class _Object:
    backing: MIRSlotId


_Concrete = int | bool | _Object | tuple[int | bool | _Object, ...]


def concrete_conflicts(fn: MIRFunction, repeat: bool) -> set[tuple[MIRPoint, MIRPlace, MIRSlotId]]:
    """Execute a bounded path, then inspect future uses before holder overwrite."""
    values: dict[MIRSlotId, _Concrete] = {N: 1, FLAG: repeat}
    heap: dict[MIRSlotId, int | bool] = {}
    blocks = {block.id: block for block in fn.blocks}
    slots = {slot.id: slot for slot in fn.slots}
    trace: list[tuple[MIRPoint, MIRAssign, dict[MIRSlotId, _Concrete], MIRSlotId | None]] = []
    bid = fn.entry
    for _ in range(10):
        block = blocks[bid]
        for index, stmt in enumerate(block.statements):
            if isinstance(stmt, MIRStorageInit):
                continue
            before = values.copy()
            replaced = None
            match stmt.value:
                case MIRWrap(source=source) if source is not None and slots[stmt.target.root].shape.owned_sum:
                    # The built record moves into the sum's payload: a refill replaces it.
                    root = stmt.target.root
                    replaced = root if root in heap else None
                    heap[root] = heap[source.root]
                    value = _Object(root)
                case MIRCall(arguments=(operand,)) if slots[stmt.target.root].shape.is_storage:
                    root = stmt.target.root
                    replaced = root if root in heap else None
                    heap[root] = values[operand]
                    value = _Object(root)
                case MIRBorrow(source=source):
                    assert not source.projections or slots[source.root].shape.owned_sum
                    value = values[source.root]
                case MIRAlias(source=source):
                    value = values[source]
                case MIRConstruct(fields=elements):
                    value = tuple(values[s] for s in elements)
                case MIRWrap(source=source):
                    value = (values[source.root],) if source is not None else ()
                case MIRCopy(source=source):
                    value = tuple(values[source.root])
                case MIRRead(source=source):
                    value = values[source.root]
                    for projection in source.projections:
                        match projection:
                            case MIRTupleIndex(index=element):
                                value = value[element]
                            case MIRUnionPayload():
                                value = value[0]
                            case MIRDeref():
                                assert isinstance(value, _Object)
                            case MIRField():
                                assert isinstance(value, _Object)
                                value = heap[value.backing]
                case MIRConstant(value=constant):
                    value = constant
                case _:
                    raise AssertionError(stmt)
            assert not stmt.target.projections
            values[stmt.target.root] = value
            trace.append((MIRPoint(bid, index), stmt, before, replaced))
        match block.terminator:
            case MIRReturn():
                break
            case MIRGoto(target=target):
                bid = target
            case MIRBranch(condition=condition, then=yes, otherwise=no):
                bid = yes if values[condition] else no
            case _:
                raise AssertionError(block.terminator)
    else:
        raise AssertionError("oracle exceeded its bounded path")

    def used_before_overwrite(root: MIRSlotId, future: list) -> bool:
        for _, stmt, _, _ in future:
            match stmt.value:
                case MIRConstruct(fields=fields) | MIRCall(arguments=fields):
                    uses = fields
                case MIRRead(source=source) | MIRBorrow(source=source):
                    uses = (source.root,)
                case MIRAlias(source=source):
                    uses = (source,)
                case MIRCopy(source=source) | MIRWrap(source=source):
                    uses = () if source is None else (source.root,)
                case MIRConstant():
                    uses = ()
                case _:
                    raise AssertionError(stmt)
            if root in uses:
                return True
            if stmt.target.root == root:
                return False
        return False

    found: set[tuple[MIRPoint, MIRPlace, MIRSlotId]] = set()
    for index, (point, _, before, replaced) in enumerate(trace):
        if replaced is None:
            continue
        for root, value in before.items():
            if slots[root].shape.owns_storage or not used_before_overwrite(root, trace[index + 1:]):
                continue
            if isinstance(value, _Object):
                leaves = [(MIRPlace(root), value)]
            elif isinstance(value, tuple):
                leaves = []
                for element, item in enumerate(value):
                    if isinstance(item, _Object):
                        match slots[root].shape.cls:
                            case MIRClass.TUPLE:
                                path = (MIRTupleIndex(element),)
                            case MIRClass.OPTIONAL:
                                path = (MIRUnionPayload(1),)
                            case MIRClass.UNION:
                                path = (MIRUnionPayload(0),)
                            case _:
                                raise AssertionError(root)
                        leaves.append((MIRPlace(root, path), item))
            else:
                leaves = []
            for holder, obj in leaves:
                if obj.backing == replaced:
                    found.add((point, holder, replaced))
    return found


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("copied", [False, True])
def test_conflicts_match_independent_bounded_execution(shape: str, safe: bool, copied: bool) -> None:
    fn, _, _ = loop_function(shape, safe=safe, copied=copied, optional_owner=shape == "optional")
    expected = concrete_conflicts(fn, False) | concrete_conflicts(fn, True)
    actual = {(c.point, c.holder, c.affected.place.root) for c in analyze(fn).conflicts}
    assert actual == expected


def test_an_elements_step_may_reach_one_element_under_one_origin() -> None:
    # `put(ps, ps[0].s)` with `put` replacing `ps[0]`: element identity is
    # not tracked, so a write ending at the elements region and a holder
    # under it may be one element -- the call breaks the obligation.
    ps, other = MIRSlotId(B, 0), MIRSlotId(B, 1)
    field = MIRField(MIRFieldId(INT32, "s"), INT32)
    obligation = MIRAliasObligation(1, (), MIRParameterWrite(0, (MIRContainerElements(),)))
    written = MIRReferent(MIRPlace(ps, (MIRDeref(), MIRContainerElements())), external=True)
    for retained in ((MIRDeref(), MIRContainerElements()), (MIRDeref(), MIRContainerElements(), field)):
        conflict = MIRRetentionConflict(MIRPoint(A, 0), written, MIRPlace(other), MIRReferent(MIRPlace(ps, retained),
                                                                                             external=True),
                                        obligation)
        assert _one_object(conflict), retained
    # A write under an element (`ps[elements].s`) is one step like any other.
    deep = MIRReferent(MIRPlace(ps, (MIRDeref(), MIRContainerElements(), field)), external=True)
    assert _one_object(MIRRetentionConflict(MIRPoint(A, 0), deep, MIRPlace(other), written, obligation))
    # Under another origin the two are not one object.
    elsewhere = MIRReferent(MIRPlace(other, (MIRDeref(), MIRContainerElements())), external=True)
    assert not _one_object(MIRRetentionConflict(MIRPoint(A, 0), written, MIRPlace(other), elsewhere, obligation))
