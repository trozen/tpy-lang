"""Replacement conflicts use retained payloads after RHS evaluation, not old uses."""

from dataclasses import dataclass, replace

import pytest

from ..thir.nodes import Form
from ..typesys import BOOL, INT32, NominalType, OptionalType, TupleType, UnionType
from .dependencies import MIRDependencies, MIRReferent, analyze_dependencies
from .liveness import MIRPoint, analyze_liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch,
    MIRConstant, MIRConstruct, MIRDeref, MIRField, MIRFieldId, MIRFunction, MIRGoto, MIRIsPresent,
    MIRNotCovered, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout,
    MIROptionalPayload, MIRPlace, MIRRead, MIRRecordLayout, MIRRecordWrite,
    MIRRecordWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout,
    MIRUnionConstruct, MIRUnionCopy, MIRUnionLayout, MIRUnionPayload, MIRValueKind,
)
from .retention import MIRRetention, MIRRetentionConflict, analyze_retention, dump_retention, may_overlap
from .storage import MIRStorageEvents, analyze_storage
from .test_reuse import loop_source, source_function
from .testutil import execute
from .validate import MIRValidationError


B = MIRBodyId("retention", "loop")
N, FLAG, INITIAL, SITE, CURRENT, TEMP, SAVED, COPIED, OUT = (MIRSlotId(B, i) for i in range(9))
A, LOOP, AGAIN, END = (MIRBlockId(B, i) for i in range(4))
CELL = NominalType("Cell", _module_qname="retention.Cell")
FIELD = MIRField(MIRFieldId(CELL, "value"), INT32)
LAYOUT = MIRRecordLayout(CELL, (FIELD,), True, True)


def analyze(fn: MIRFunction) -> MIRRetention:
    live = analyze_liveness(fn)
    result = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn))
    assert isinstance(result, MIRRetention), result
    return result


def reference(sid: MIRSlotId, *, parameter: bool = False) -> MIRSlot:
    return MIRSlot(sid, CELL, MIRSlotKind.PARAMETER if parameter else MIRSlotKind.LOCAL,
                   form=Form.BORROW, value_kind=MIRValueKind.BORROWED_RECORD)


def loop_function(shape: str, *, safe: bool = False, optional_owner: bool = False,
                  copied: bool = False) -> tuple[MIRFunction, MIRPoint, MIRPlace]:
    member = MIRTupleElement(CELL, MIRValueKind.BORROWED_RECORD)
    match shape:
        case "record":
            holder = reference(SAVED)
            wrap, copy = MIRAlias(TEMP), MIRAlias(SAVED)
            path = ()
        case "singleton" | "mixed":
            mixed = shape == "mixed"
            layout = MIRTupleLayout((member, MIRTupleElement(INT32)) if mixed else (member,))
            holder = MIRSlot(SAVED, TupleType(tuple(m.type for m in layout.elements)), MIRSlotKind.LOCAL,
                             value_kind=MIRValueKind.TUPLE, tuple_layout=layout)
            wrap, copy = MIRTupleConstruct((TEMP, N) if mixed else (TEMP,)), MIRTupleCopy(SAVED)
            path = (MIRTupleIndex(0),)
        case "optional":
            holder = MIRSlot(SAVED, OptionalType(CELL), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
                             optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED_RECORD))
            wrap, copy = MIROptionalConstruct(TEMP), MIROptionalCopy(SAVED)
            path = (MIROptionalPayload(),)
        case "union":
            other = NominalType("Other", _module_qname="retention.Other")
            holder = MIRSlot(SAVED, UnionType((CELL, other)), MIRSlotKind.LOCAL, value_kind=MIRValueKind.UNION,
                             union_layout=MIRUnionLayout((member, MIRTupleElement(other, MIRValueKind.BORROWED_RECORD))))
            wrap, copy = MIRUnionConstruct(0, TEMP), MIRUnionCopy(SAVED)
            path = (MIRUnionPayload(0),)
        case _:
            raise AssertionError(shape)
    current = (MIRSlot(CURRENT, OptionalType(CELL), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
                       optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED_RECORD))
               if optional_owner else reference(CURRENT))
    storage = MIRSlot(INITIAL, CELL, MIRSlotKind.TEMPORARY, form=Form.STORAGE,
                      value_kind=MIRValueKind.RECORD_STORAGE, storage_duration=MIRStorageDuration.BODY)
    slots = (MIRSlot(N, INT32, MIRSlotKind.PARAMETER), MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER),
             storage, replace(storage, id=SITE), current, reference(TEMP), holder,
             replace(holder, id=COPIED), MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    initial = MIRAssign(MIRPlace(INITIAL), MIRConstruct((N,)),
                        record_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    borrow = MIRAssign(MIRPlace(TEMP), MIRBorrow(MIRPlace(INITIAL)))
    set_current = MIRAssign(MIRPlace(CURRENT), MIROptionalConstruct(TEMP) if optional_owner else MIRAlias(TEMP))
    capture = MIRAssign(MIRPlace(SAVED), MIROptionalCopy(CURRENT) if optional_owner else wrap)
    copy_holder = MIRAssign(MIRPlace(COPIED), copy)
    observed = MIRPlace(COPIED if copied else SAVED, path)
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(observed.root, (*path, MIRDeref(), FIELD))))
    write = MIRAssign(MIRPlace(SITE), MIRConstruct((N,)), record_write=MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))
    replacement = (write, replace(borrow, value=MIRBorrow(MIRPlace(SITE))), set_current)
    reads = (read,) if not safe else ()
    captures = (capture, copy_holder) if copied else (capture,)
    statements = ((read,) if safe else ()) + replacement + reads + captures
    fn = MIRFunction(B, INT32, slots, (
        MIRBlock(A, (initial, borrow, set_current, capture, copy_holder), MIRGoto(LOOP)),
        MIRBlock(LOOP, statements, MIRBranch(FLAG, AGAIN, END)),
        MIRBlock(AGAIN, (MIRAssign(MIRPlace(FLAG), MIRConstant(False)), MIRAssign(MIRPlace(N), MIRConstant(2))), MIRGoto(LOOP)),
        MIRBlock(END, (), MIRReturn(OUT)),
    ), A, (LAYOUT,))
    return fn, MIRPoint(LOOP, 1 if safe else 0), observed


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("copied", [False, True])
def test_all_holder_shapes_keep_the_old_object(shape: str, safe: bool, copied: bool) -> None:
    fn, point, holder = loop_function(shape, safe=safe, copied=copied)
    result = analyze(fn)
    expected = () if safe else (MIRRetentionConflict(point, MIRReferent(MIRPlace(SITE)), holder,
                                                    MIRReferent(MIRPlace(SITE))),)
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
             MIRSlot(N, INT32, MIRSlotKind.PARAMETER), MIRSlot(OUT, INT32, MIRSlotKind.LOCAL))
    alias = MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT))
    read_alias = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    write = MIRAssign(MIRPlace(CURRENT, (MIRDeref(),)), MIRConstruct((OUT if rhs_only else N,)),
                      record_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, CURRENT))
    after = read_alias if live_alias else MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(CURRENT, (MIRDeref(), FIELD))))
    statements = (alias, *((read_alias,) if rhs_only else ()), write, after)
    return MIRFunction(B, INT32, slots, (MIRBlock(A, statements, MIRReturn(OUT)),), A, (LAYOUT,))


@pytest.mark.parametrize("live_alias", [False, True])
@pytest.mark.parametrize("rhs_only", [False, True])
def test_in_place_exempts_owner_but_not_other_live_alias(live_alias: bool, rhs_only: bool) -> None:
    result = analyze(in_place_function(live_alias=live_alias, rhs_only=rhs_only))
    assert {c.holder for c in result.conflicts} == ({MIRPlace(SAVED)} if live_alias else set())
    assert all(c.affected == c.retained == MIRReferent(MIRPlace(CURRENT), True) for c in result.conflicts)


def test_readonly_alias_still_retains_the_old_object() -> None:
    fn = in_place_function(live_alias=True)
    fn = replace(fn, slots=tuple(replace(s, readonly=True) if s.id == SAVED else s for s in fn.slots))
    assert {c.holder for c in analyze(fn).conflicts} == {MIRPlace(SAVED)}


@pytest.mark.parametrize("live_alias", [False, True])
def test_optional_in_place_exempts_only_its_own_holder(live_alias: bool) -> None:
    fn = in_place_function(live_alias=live_alias)
    optional = MIRSlot(CURRENT, OptionalType(CELL), MIRSlotKind.PARAMETER, value_kind=MIRValueKind.OPTIONAL,
                        optional_layout=MIROptionalLayout(CELL, MIRValueKind.BORROWED_RECORD))
    path = (MIROptionalPayload(), MIRDeref())
    alias, write, read = fn.blocks[0].statements
    alias = replace(alias, value=MIRBorrow(MIRPlace(CURRENT, path)))
    write = replace(write, target=MIRPlace(CURRENT, path))
    if not live_alias:
        read = replace(read, value=MIRRead(MIRPlace(CURRENT, (*path, FIELD))))
    fn = replace(fn, slots=tuple(optional if s.id == CURRENT else s for s in fn.slots) + (
        MIRSlot(FLAG, BOOL, MIRSlotKind.LOCAL),), blocks=(
        MIRBlock(A, (MIRAssign(MIRPlace(FLAG), MIRIsPresent(CURRENT)),), MIRBranch(FLAG, LOOP, END)),
        MIRBlock(LOOP, (alias, write, read), MIRReturn(OUT)),
        MIRBlock(END, (MIRAssign(MIRPlace(OUT), MIRConstant(0)),), MIRReturn(OUT)),
    ))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == ({MIRPlace(SAVED)} if live_alias else set())
    assert all(c.affected == MIRReferent(MIRPlace(CURRENT, (MIROptionalPayload(),)), True) for c in result.conflicts)


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
        statements[read_index] = MIRAssign(MIRPlace(OUT), MIRIsPresent(COPIED))
        fn = replace(fn, return_type=BOOL, slots=tuple(replace(s, type=BOOL) if s.id == OUT else s for s in fn.slots))
    clear = MIRAssign(MIRPlace(COPIED if clear_copy else SAVED), MIROptionalConstruct())
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(clear, *statements)), *fn.blocks[2:]))
    result = analyze(fn)
    assert {c.holder for c in result.conflicts} == (set() if clear_copy else {holder})
    assert execute(fn, 1, True) == (False if clear_copy else 2)


def test_distinct_external_parameters_may_alias() -> None:
    fn = in_place_function(live_alias=True)
    fn = replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.PARAMETER) if s.id == SAVED else s for s in fn.slots),
                 blocks=(replace(fn.blocks[0], statements=fn.blocks[0].statements[1:]),))
    conflict, = analyze(fn).conflicts
    assert conflict.affected == MIRReferent(MIRPlace(CURRENT), True)
    assert conflict.retained == MIRReferent(MIRPlace(SAVED), True)


def test_two_payloads_of_one_parameter_may_alias() -> None:
    fn = in_place_function(live_alias=True)
    member = MIRTupleElement(CELL, MIRValueKind.BORROWED_RECORD)
    pair = MIRSlot(TEMP, TupleType((CELL, CELL)), MIRSlotKind.PARAMETER,
                   value_kind=MIRValueKind.TUPLE, tuple_layout=MIRTupleLayout((member, member)))
    prelude = tuple(MIRAssign(MIRPlace(dest), MIRBorrow(MIRPlace(TEMP, (MIRTupleIndex(i), MIRDeref()))))
                    for i, dest in enumerate((CURRENT, SAVED)))
    fn = replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.LOCAL) if s.id == CURRENT else s for s in fn.slots) + (pair,),
                 blocks=(replace(fn.blocks[0], statements=(*prelude, *fn.blocks[0].statements[1:])),))
    conflict, = analyze(fn).conflicts
    assert conflict.affected == MIRReferent(MIRPlace(TEMP, (MIRTupleIndex(0),)), True)
    assert conflict.retained == MIRReferent(MIRPlace(TEMP, (MIRTupleIndex(1),)), True)


@pytest.mark.parametrize("same_field", [False, True])
def test_inline_siblings_are_disjoint_but_same_field_is_retained(same_field: bool) -> None:
    fn = in_place_function(live_alias=True)
    parent = NominalType("Parent", _module_qname="retention.Parent")
    left = MIRField(MIRFieldId(parent, "left"), CELL)
    right = MIRField(MIRFieldId(parent, "right"), CELL)
    param = replace(reference(TEMP, parameter=True), type=parent)
    prelude = (
        MIRAssign(MIRPlace(CURRENT), MIRBorrow(MIRPlace(TEMP, (MIRDeref(), left)))),
        MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(TEMP, (MIRDeref(), left if same_field else right)))),
    )
    fn = replace(fn, slots=tuple(replace(s, kind=MIRSlotKind.LOCAL) if s.id == CURRENT else s for s in fn.slots) + (param,),
                 blocks=(replace(fn.blocks[0], statements=(*prelude, *fn.blocks[0].statements[1:])),))
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
    initial_site = MIRAssign(MIRPlace(SITE), MIRConstruct((N,)),
                             record_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    write = MIRAssign(MIRPlace(CURRENT, (MIRDeref(),)), MIRConstruct((N,)),
                      record_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, CURRENT))
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(SAVED, (MIRDeref(), FIELD))))
    fn = replace(fn, blocks=(
        replace(entry, statements=(*entry.statements, initial_site), terminator=MIRBranch(FLAG, LOOP, AGAIN)),
        MIRBlock(LOOP, (MIRAssign(MIRPlace(SAVED), MIRAlias(CURRENT)),), MIRGoto(END)),
        MIRBlock(AGAIN, (MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(SITE))),), MIRGoto(END)),
        MIRBlock(END, (write, read), MIRReturn(OUT)),
    ))
    assert analyze(fn).conflicts == (MIRRetentionConflict(
        MIRPoint(END, 0), MIRReferent(MIRPlace(INITIAL)), MIRPlace(SAVED), MIRReferent(MIRPlace(INITIAL))),)


def test_mixed_analysis_instances_are_rejected_and_uncovered_stays_uncovered() -> None:
    fn, _, _ = loop_function("record")
    live = analyze_liveness(fn)
    deps = analyze_dependencies(fn, live)
    events = analyze_storage(fn)
    assert isinstance(deps, MIRDependencies) and isinstance(events, MIRStorageEvents)
    other = replace(fn)
    for inputs in ((analyze_liveness(other), deps, events),
                   (live, replace(deps, function=other), events),
                   (live, deps, replace(events, function=other))):
        with pytest.raises(MIRValidationError, match="different MIR function"):
            analyze_retention(fn, *inputs)
    for inputs in ((MIRNotCovered(B, "dependencies", "missing duration"), events),
                   (deps, MIRNotCovered(B, "storage", "missing record write fact"))):
        result = analyze_retention(fn, live, *inputs)
        assert isinstance(result, MIRNotCovered)
        assert dump_retention(result).startswith("retention not covered:")


def test_actual_missing_write_metadata_cannot_be_an_empty_conflict_result() -> None:
    fn = in_place_function(live_alias=True)
    fn = replace(fn, blocks=(replace(fn.blocks[0], statements=tuple(
        replace(s, record_write=None) for s in fn.blocks[0].statements)),))
    live = analyze_liveness(fn)
    result = analyze_retention(fn, live, analyze_dependencies(fn, live), analyze_storage(fn))
    assert isinstance(result, MIRNotCovered)
    assert "missing record write fact" in result.reason


def test_overlap_distinguishes_payload_origins_from_inline_fields() -> None:
    other_field = MIRField(MIRFieldId(CELL, "other"), INT32)
    root = MIRPlace(CURRENT)
    child = MIRPlace(CURRENT, (FIELD,))
    sibling = MIRPlace(CURRENT, (other_field,))
    for external in (False, True):
        assert may_overlap(MIRReferent(root, external), MIRReferent(child, external))
        assert may_overlap(MIRReferent(child, external), MIRReferent(root, external))
        assert not may_overlap(MIRReferent(child, external), MIRReferent(sibling, external))
    assert not may_overlap(MIRReferent(root), MIRReferent(root, True))
    assert not may_overlap(MIRReferent(root), MIRReferent(MIRPlace(SAVED)))
    assert may_overlap(MIRReferent(root, True), MIRReferent(MIRPlace(SAVED), True))
    # Two payloads of one parameter can point at the same object or parent/child.
    for left, right in ((MIRTupleIndex(0), MIRTupleIndex(1)),
                        (MIRUnionPayload(0), MIRUnionPayload(1))):
        assert may_overlap(MIRReferent(MIRPlace(CURRENT, (left, FIELD)), True),
                           MIRReferent(MIRPlace(CURRENT, (right, other_field)), True))


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
            before = values.copy()
            replaced = None
            match stmt.value:
                case MIRConstruct(fields=(operand,)):
                    root = stmt.target.root
                    replaced = root if root in heap else None
                    heap[root] = values[operand]
                    value = _Object(root)
                case MIRBorrow(source=source):
                    assert not source.projections
                    value = values[source.root]
                case MIRAlias(source=source):
                    value = values[source]
                case MIRTupleConstruct(elements=elements):
                    value = tuple(values[s] for s in elements)
                case MIROptionalConstruct(source=source) | MIRUnionConstruct(alternative=0, source=source):
                    value = (values[source],) if source is not None else ()
                case MIRTupleCopy(source=source) | MIROptionalCopy(source=source) | MIRUnionCopy(source=source):
                    value = tuple(values[source])
                case MIRRead(source=source):
                    value = values[source.root]
                    for projection in source.projections:
                        match projection:
                            case MIRTupleIndex(index=element):
                                value = value[element]
                            case MIROptionalPayload() | MIRUnionPayload():
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
                case MIRConstruct(fields=fields) | MIRTupleConstruct(elements=fields):
                    uses = fields
                case MIRRead(source=source) | MIRBorrow(source=source):
                    uses = (source.root,)
                case (MIRAlias(source=source) | MIRTupleCopy(source=source)
                      | MIROptionalCopy(source=source) | MIRUnionCopy(source=source)
                      | MIROptionalConstruct(source=source) | MIRUnionConstruct(source=source)):
                    uses = (source,)
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
            if slots[root].value_kind is MIRValueKind.RECORD_STORAGE or not used_before_overwrite(root, trace[index + 1:]):
                continue
            if isinstance(value, _Object):
                leaves = [(MIRPlace(root), value)]
            elif isinstance(value, tuple):
                leaves = []
                for element, item in enumerate(value):
                    if isinstance(item, _Object):
                        match slots[root].value_kind:
                            case MIRValueKind.TUPLE:
                                path = (MIRTupleIndex(element),)
                            case MIRValueKind.OPTIONAL:
                                path = (MIROptionalPayload(),)
                            case MIRValueKind.UNION:
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
