"""Copy/move writes reuse the selected backing without losing retained aliases."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from . import test_record_initialization as optional
from .definitions import MIRDefinitions
from .liveness import MIRPoint
from .testutil import lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIRCopy, MIRFunction, MIRGoto,
    MIRMove, MIRRegion,
    MIRPlace, MIRRead, MIRStorageWrite, MIRStorageWriteMode, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRStorageDuration,
)
from .shapes import MIRDeref
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_retention import INITIAL, analyze, loop_function
from .testutil import compiled_definitions, construction, execute, owned_shape, reslot, prepared, with_calls
from .validate import MIRDefiniteAssignmentError
from .value import MIRValidationError
from .prepare import validate_function


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("safe", [False, True])
def test_reused_site_keeps_previous_holder_dependencies(move: bool, shape: str, safe: bool) -> None:
    fn, point, observed = loop_function(shape, safe=safe)
    block = fn.blocks[1]
    # The site's record built by a transfer instead of a construct.
    build = point.index - 1
    write = replace(block.statements[build], value=MIRMove(INITIAL) if move else MIRCopy(MIRPlace(INITIAL)))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(
        *block.statements[:build], write, *block.statements[build + 1:])), *fn.blocks[2:]))
    if move:
        # The site re-executes on the next pass and moves INITIAL again.
        with pytest.raises(MIRDefiniteAssignmentError, match="read after move"):
            analyze(fn)
        return
    assert {c.holder for c in analyze(fn).conflicts} == (set() if safe else {observed})
    heap = {}
    assert execute(fn, 3, True, heap=heap) == 3
    # Re-executing the site must neither consume its scalar source nor allocate a third object.
    assert len(heap) == 2
    assert all(tuple(fields.values()) == (3,) for fields in heap.values())


def optional_transfers(fn: MIRFunction, move: bool) -> MIRFunction:
    source = MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1)
    slot = MIRSlot(source, owned_shape(optional.CELL), MIRSlotKind.LOCAL, storage_duration=MIRStorageDuration.BODY,
                   residence=optional.ROOT)
    init = MIRAssign(MIRPlace(source), construction(optional.RECORD, optional.VALUE),
                     storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
    # Each fill's record is built by a transfer of `source`.
    built = {MIRPlace(b) for b in optional.BUILT}
    blocks = tuple(replace(block, statements=tuple(
        replace(stmt, value=MIRMove(source) if move else MIRCopy(MIRPlace(source)))
        if isinstance(stmt, MIRAssign) and stmt.target in built else stmt
        for stmt in block.statements)) for block in fn.blocks)
    entry = replace(blocks[0], statements=(init, *blocks[0].statements))
    return with_calls(replace(fn, slots=(*fn.slots, slot), blocks=(entry, *blocks[1:])))


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_joins_empty_and_filled_sum(move: bool) -> None:
    fn = optional.function(
        MIRBlock(optional.ENTRY, (optional.INIT,), MIRBranch(optional.FLAG, optional.YES, optional.NO), optional.ROOT),
        MIRBlock(optional.YES, optional.WRITE, MIRGoto(optional.JOIN), optional.ROOT),
        MIRBlock(optional.NO, (), MIRGoto(optional.JOIN), optional.ROOT),
        MIRBlock(optional.JOIN, (*optional.write(1), optional.BIND, optional.SAVE, optional.MUTATE, optional.READ),
                 MIRReturn(optional.RESULT), optional.ROOT))
    fn = optional_transfers(fn, move)
    if move:
        # The join's fill moves the source a second time on the filled path.
        with pytest.raises(MIRDefiniteAssignmentError, match="read after move"):
            analyze(fn)
        return
    assert execute(fn, 3, False) == execute(fn, 3, True) == 9
    assert optional.selection(fn, MIRPoint(optional.JOIN, 1)) == frozenset({0, 1})
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(prepared(fn)).conflicts == ()


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_preserves_identity_and_live_alias(move: bool) -> None:
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.INIT, *optional.WRITE, optional.BIND, optional.SAVE,
        *optional.write(1), optional.BIND, optional.MUTATE, optional.READ),
        MIRReturn(optional.RESULT), optional.ROOT))
    fn = optional_transfers(fn, move)
    if move:
        # The second fill moves the source again.
        with pytest.raises(MIRDefiniteAssignmentError, match="read after move"):
            analyze(fn)
        return
    heap = {}
    assert execute(fn, 3, False, heap=heap) == 9
    # The source and the sum's one inline record, refilled in place.
    assert len(heap) == 2
    assert sorted(next(iter(fields.values())) for fields in heap.values()) == [3, 9]
    # The second fill (after the source's initialization) replaces the record SAVED holds.
    assert {(c.point, c.holder) for c in analyze(fn).conflicts} == {
        (MIRPoint(optional.ENTRY, 7), MIRPlace(optional.SAVED))}


@pytest.mark.parametrize("move", [False, True])
def test_optional_transfer_backedge_does_not_reinitialize_the_sum(move: bool) -> None:
    fn = optional.function(
        MIRBlock(optional.ENTRY, (optional.INIT,), MIRGoto(optional.YES), optional.ROOT),
        MIRBlock(optional.YES, (*optional.WRITE, optional.BIND), MIRGoto(optional.NO), optional.CHILD),
        MIRBlock(optional.NO, (), MIRBranch(optional.FLAG, optional.ENTRY_AGAIN, optional.JOIN), optional.ROOT),
        MIRBlock(optional.ENTRY_AGAIN, (MIRAssign(MIRPlace(optional.FLAG), MIRConstant(False)),),
                 MIRGoto(optional.YES), optional.ROOT),
        MIRBlock(optional.JOIN, (optional.SAVE, optional.MUTATE, optional.READ),
                 MIRReturn(optional.RESULT), optional.ROOT), built=optional.CHILD)
    fn = replace(fn, regions=(*fn.regions, MIRRegion(optional.CHILD, optional.ROOT, optional.YES)))
    fn = optional_transfers(fn, move)
    if move:
        # The back edge re-runs the fill, which moves the source again.
        with pytest.raises(MIRDefiniteAssignmentError, match="read after move"):
            analyze(fn)
        return
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
    # CURRENT and SAVED both point at one record's storage (`BUILT[0]`);
    # root-ID inequality is not referent disjointness.
    owned = optional.BUILT[0]
    value = MIRMove(owned) if move else MIRCopy(MIRPlace(optional.SAVED, (MIRDeref(),)))
    write = MIRAssign(MIRPlace(optional.CURRENT, (MIRDeref(),)), value,
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, optional.CURRENT))
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.WRITE[0], MIRAssign(MIRPlace(optional.CURRENT), MIRBorrow(MIRPlace(owned))), optional.SAVE, write,
        read), MIRReturn(optional.RESULT), optional.ROOT))
    if readonly_alias:
        fn = replace(fn, slots=tuple(reslot(s, readonly=True) if s.id == optional.SAVED else s for s in fn.slots))
    assert execute(fn, 7, False) == 7
    # A move empties BACKING, which CURRENT itself points at: reading
    # through CURRENT afterwards reads moved-from storage.
    expected = ({MIRPlace(optional.SAVED)} if live_alias
                else {MIRPlace(optional.CURRENT)} if move else set())
    assert {c.holder for c in analyze(fn).conflicts} == expected
    if not move:
        assert optional.SAVED in prepared(fn).liveness.points[MIRPoint(optional.ENTRY, 3)]


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("mode,reason", [
    (None, "unsupported record replacement"),
    (MIRStorageWriteMode.INITIALIZE, "backing write needs private body storage"),
])
def test_projected_transfer_requires_in_place_fact(move: bool, mode: MIRStorageWriteMode | None, reason: str) -> None:
    source = optional.BUILT[1]
    write = MIRAssign(MIRPlace(optional.CURRENT, (MIRDeref(),)),
                      MIRMove(source) if move else MIRCopy(MIRPlace(source)),
                      storage_write=MIRStorageWrite(mode) if mode is not None else None)
    fn = optional.function(MIRBlock(optional.ENTRY, (
        optional.INIT, *optional.WRITE, optional.BIND, optional.SAVE, optional.write(1)[0], write, optional.READ),
        MIRReturn(optional.RESULT), optional.ROOT))
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(fn)


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("fault", ["uninitialized", "readonly", "ineligible"])
def test_optional_transfer_keeps_source_eligibility(move: bool, fault: str) -> None:
    fn = optional_transfers(optional.straight(optional.INIT, *optional.WRITE), move)
    if fault == "uninitialized":
        fn = replace(fn, blocks=(replace(fn.blocks[0], statements=fn.blocks[0].statements[1:]),))
        reason = "read before definite assignment"
    elif fault == "readonly":
        # Storage is never readonly: readonly is its holder's.
        fn = replace(fn, slots=(*fn.slots[:-1], reslot(fn.slots[-1], readonly=True)))
        reason = "readonly owned storage"
    else:
        fn = replace(fn, records=(replace(fn.records[0], **({"movable": False} if move else {"copyable": False})),))
        reason = "record (move|copy) source or eligibility"
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
def compiled():
    return _compile(HOIST_SOURCE)


@pytest.fixture(scope="module")
def hoist(compiled) -> tuple[th.THIRFunction, MIRDefinitions]:
    compiler, modules = compiled
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        return next(fn for node, fn in ctx.thir_functions.items() if node.name == "hoisted"), compiled_definitions(
            compiler, tuple(ctx.thir_constructors.values()))


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
    result = lower_function(fn, MIRBodyId("reused", "hoisted"), definitions=definitions)
    assert isinstance(result, MIRFunction), result
    assert execute(result, False) == 0 and execute(result, True) == 3
    transfer, = (s for s in analyze_storage(prepared(result)).writes.values() if isinstance(s.value, (MIRCopy, MIRMove)))
    # The transfer builds the record the fill then moves into the sum.
    assert transfer.storage_write.mode is MIRStorageWriteMode.INITIALIZE
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
