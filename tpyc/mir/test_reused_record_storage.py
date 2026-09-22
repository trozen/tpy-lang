"""Copy/move writes reuse the selected backing without losing retained aliases."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from . import test_record_initialization as optional
from .definitions import MIRDefinitions
from .liveness import MIRPoint, analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBodyId, MIRBodyKind, MIRBranch, MIRConstant,
    MIRConstruct, MIRCopy, MIRDeref, MIRFunction, MIRGoto, MIRMove,
    MIRPlace, MIRRead, MIRRecordWrite, MIRRecordWriteMode, MIRReturn, MIRSlot,
    MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRValueKind,
)
from .presence import MIREngagement, _analyze_presence
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_retention import INITIAL, analyze, loop_function
from .testutil import execute
from .validate import MIRValidationError, validate_function


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
def test_reused_site_keeps_previous_holder_dependencies(move: bool, shape: str, safe: bool) -> None:
    fn, point, observed = loop_function(shape, safe=safe)
    block = fn.blocks[1]
    write = replace(block.statements[point.index], value=MIRMove(INITIAL) if move else MIRCopy(MIRPlace(INITIAL)))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(
        *block.statements[:point.index], write, *block.statements[point.index + 1:])), *fn.blocks[2:]))
    assert {c.holder for c in analyze(fn).conflicts} == (set() if safe else {observed})
    heap = {}
    assert execute(fn, 3, True, heap=heap) == 3
    # Re-executing the site must neither consume its scalar source nor allocate a third object.
    assert len(heap) == 2
    assert all(tuple(fields.values()) == (3,) for fields in heap.values())


def optional_transfers(fn: MIRFunction, move: bool) -> MIRFunction:
    source = MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1)
    slot = MIRSlot(source, optional.CELL, MIRSlotKind.LOCAL, form=th.Form.STORAGE,
                   value_kind=MIRValueKind.RECORD_STORAGE,
                   storage_duration=MIRStorageDuration.BODY, residence=optional.ROOT)
    init = MIRAssign(MIRPlace(source), MIRConstruct((optional.VALUE,)),
                     storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE))
    blocks = tuple(replace(block, statements=tuple(
        replace(stmt, value=MIRMove(source) if move else MIRCopy(MIRPlace(source)))
        if isinstance(stmt, MIRAssign) and stmt.target == optional.WRITE.target else stmt
        for stmt in block.statements)) for block in fn.blocks)
    entry = replace(blocks[0], statements=(init, *blocks[0].statements))
    return replace(fn, slots=(*fn.slots, slot), blocks=(entry, *blocks[1:]))


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_joins_empty_and_engaged_backing(move: bool) -> None:
    fn = optional.function(
        MIRBlock(optional.ENTRY, (optional.INIT,), MIRBranch(optional.FLAG, optional.YES, optional.NO), optional.ROOT),
        MIRBlock(optional.YES, (optional.WRITE,), MIRGoto(optional.JOIN), optional.ROOT),
        MIRBlock(optional.NO, (), MIRGoto(optional.JOIN), optional.ROOT),
        MIRBlock(optional.JOIN, (optional.WRITE, optional.BIND, optional.SAVE, optional.MUTATE, optional.READ),
                 MIRReturn(optional.RESULT), optional.ROOT))
    fn = optional_transfers(fn, move)
    assert execute(fn, 3, False) == execute(fn, 3, True) == 9
    engagement = dict(_analyze_presence(fn).engagement[MIRPoint(optional.JOIN, 0)])
    assert engagement[optional.BACKING] == frozenset(MIREngagement)
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_preserves_identity_and_live_alias(move: bool) -> None:
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.INIT, optional.WRITE, optional.BIND, optional.SAVE,
        optional.WRITE, optional.BIND, optional.MUTATE, optional.READ),
        MIRReturn(optional.RESULT), optional.ROOT))
    fn = optional_transfers(fn, move)
    heap = {}
    assert execute(fn, 3, False, heap=heap) == 9
    assert len(heap) == 2
    assert sorted(next(iter(fields.values())) for fields in heap.values()) == [3, 9]
    assert {(c.point, c.holder) for c in analyze(fn).conflicts} == {
        (MIRPoint(optional.ENTRY, 5), MIRPlace(optional.SAVED))}


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_backedge_does_not_reinitialize_wrapper(move: bool) -> None:
    fn = optional.function(
        MIRBlock(optional.ENTRY, (optional.INIT,), MIRGoto(optional.YES), optional.ROOT),
        MIRBlock(optional.YES, (optional.WRITE, optional.BIND),
                 MIRBranch(optional.FLAG, optional.NO, optional.JOIN), optional.ROOT),
        MIRBlock(optional.NO, (MIRAssign(MIRPlace(optional.FLAG), MIRConstant(False)),),
                 MIRGoto(optional.YES), optional.ROOT),
        MIRBlock(optional.JOIN, (optional.SAVE, optional.MUTATE, optional.READ),
                 MIRReturn(optional.RESULT), optional.ROOT))
    fn = optional_transfers(fn, move)
    heap = {}
    assert execute(fn, 3, True, heap=heap) == 9
    assert len(heap) == 2
    assert analyze(fn).conflicts == ()


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("live_alias", [False, True])
@pytest.mark.parametrize("readonly_alias", [False, True])
def test_indirect_overlap_reads_before_replacement(move: bool, live_alias: bool, readonly_alias: bool) -> None:
    read = optional.READ if live_alias else replace(optional.READ, value=MIRRead(
        MIRPlace(optional.CURRENT, (MIRDeref(), optional.FIELD))))
    # CURRENT and SAVED both point at BACKING; root-ID inequality is not referent disjointness.
    value = MIRMove(optional.BACKING) if move else MIRCopy(MIRPlace(optional.SAVED, (MIRDeref(),)))
    write = MIRAssign(MIRPlace(optional.CURRENT, (MIRDeref(),)), value,
                      storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, optional.CURRENT))
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.INIT, optional.WRITE, optional.BIND, optional.SAVE, write, read),
        MIRReturn(optional.RESULT), optional.ROOT))
    if readonly_alias:
        fn = replace(fn, slots=tuple(replace(s, readonly=True) if s.id == optional.SAVED else s for s in fn.slots))
    assert execute(fn, 7, False) == 7
    assert {c.holder for c in analyze(fn).conflicts} == ({MIRPlace(optional.SAVED)} if live_alias else set())
    if not move:
        assert optional.SAVED in analyze_liveness(fn).points[MIRPoint(optional.ENTRY, 4)]


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("mode,reason", [
    (None, "unsupported record replacement"),
    (MIRRecordWriteMode.OWN_SITE, "backing write needs private body storage"),
    (MIRRecordWriteMode.OPTIONAL_ASSIGN, "invalid optional backing assignment"),
])
def test_projected_transfer_requires_in_place_fact(move: bool, mode: MIRRecordWriteMode | None, reason: str) -> None:
    write = MIRAssign(MIRPlace(optional.CURRENT, (MIRDeref(),)),
                      MIRMove(optional.BACKING) if move else MIRCopy(MIRPlace(optional.BACKING)),
                      storage_write=MIRRecordWrite(mode) if mode is not None else None)
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.INIT, optional.WRITE, optional.BIND, optional.SAVE, write, optional.READ),
        MIRReturn(optional.RESULT), optional.ROOT))
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(fn)


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("fault", ["uninitialized", "readonly", "ineligible"])
def test_optional_transfer_keeps_source_eligibility(move: bool, fault: str) -> None:
    fn = optional_transfers(optional.straight(optional.INIT, optional.WRITE), move)
    if fault == "uninitialized":
        fn = replace(fn, blocks=(replace(fn.blocks[0], statements=fn.blocks[0].statements[1:]),))
        reason = "read before definite assignment"
    elif fault == "readonly":
        fn = replace(fn, slots=(*fn.slots[:-1], replace(fn.slots[-1], readonly=True)))
        if not move:
            validate_function(fn)
            return
        reason = "record move source or eligibility"
    else:
        fn = replace(fn, records=(replace(fn.records[0], **({"movable": False} if move else {"copyable": False})),))
        reason = "movable record|copy source or eligibility"
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(fn)


HOIST_SOURCE = '''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def hoisted(flag: bool) -> int32:
    original = Cell(3)
    if flag:
        target = Cell(1)
    else:
        return 0
    target.value = 9
    return original.value
'''


@pytest.fixture(scope="module")
def hoist() -> tuple[th.THIRFunction, MIRDefinitions]:
    compiler, modules = _compile(HOIST_SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return next(fn for node, fn in ctx.thir_functions.items() if node.name == "hoisted"), MIRDefinitions(
        tuple(ctx.thir_constructors.values()))


@pytest.mark.parametrize("move", [False, True])
def test_internal_hoisted_transfer_preserves_source_read_form(hoist: tuple[th.THIRFunction, MIRDefinitions], move: bool) -> None:
    fn, definitions = hoist
    branch = fn.body[1]
    write = branch.then_body[0]
    source = th.THIRName(fn.body[0].resolved_type, "original", form=th.Form.BORROW)
    value = (th.THIRMove(source.result_type, source, form=source.form) if move else
             th.THIRCopy(source.result_type, source, "Cell", form=th.Form.STORAGE))
    changed = replace(write, value=value)
    fn = replace(fn, body=(fn.body[0], replace(branch, then_body=(changed,)), *fn.body[2:]))
    validate_thir(fn)
    result = lower_function(fn, MIRBodyId("reused", "hoisted"), kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
    assert isinstance(result, MIRFunction), result
    assert execute(result, False) == 0 and execute(result, True) == 3
    transfer, = (s for s in analyze_storage(result).writes.values() if isinstance(s.value, (MIRCopy, MIRMove)))
    assert transfer.storage_write.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
    bad = replace(changed, value=replace(value, form=th.Form.VALUE))
    with pytest.raises(THIRValidationError, match="invalid optional record assignment fact"):
        validate_thir(replace(fn, body=(fn.body[0], replace(branch, then_body=(bad,)), *fn.body[2:])))


def test_optional_assignment_fact_rejects_a_bare_record_read(hoist: tuple[th.THIRFunction, MIRDefinitions]) -> None:
    fn, _ = hoist
    branch = fn.body[1]
    write = branch.then_body[0]
    source = th.THIRName(fn.body[0].resolved_type, "original", form=th.Form.BORROW)
    write = replace(write, value=source)
    with pytest.raises(THIRValidationError, match="invalid optional record assignment fact"):
        validate_thir(replace(fn, body=(fn.body[0], replace(branch, then_body=(write,)), *fn.body[2:])))
