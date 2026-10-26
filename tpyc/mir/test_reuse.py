"""A repeated reseat refills its site's one owned sum; distinct sites retain distinct objects."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, OptionalType
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRBranch, MIRConstant, MIRConstruct, MIRCopy, MIRFunction,
    MIRGoto, MIRMove, MIRNotCovered, MIRPlace, MIRRead, MIRStorageWrite, MIRStorageWriteMode, MIRReturn, MIRSlot,
    MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRStorageInit, MIRWrap, MIRRegion, MIRRegionId,
)
from .shapes import MIRDeref, MIRUnionPayload
from .value import MIRField, MIRFieldId, MIRRecordLayout, MIRValidationError
from .storage import MIRStorageEvents, analyze_storage, dump_storage
from .testutil import (
    compiled_definitions, hand_record, bind_parameters, borrowed_shape, execute, leaf_shape, optional_shape,
    owned_shape, reslot,
    prepared,
)
from .prepare import validate_function


CELL_SOURCE = """\
from tpy import int32

class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value
"""


def loop_source(optional: bool, safe: bool) -> str:
    annotation = ": Cell | None" if optional else ""
    read = "if saved is not None:\n            result = saved.value" if optional else "result = saved.value"
    steps = [read, "current = Cell(n)"] if safe else ["current = Cell(n)", read]
    retain = "" if optional else "saved = current"
    initial_saved = "current" if optional else "Cell(0)"
    return CELL_SOURCE + f"""
def reuse(n: int32) -> int32:
    current{annotation} = Cell(0)
    saved{annotation} = {initial_saved}
    result = 0
    while n < 3:
        {steps[0]}
        {steps[1]}
        {retain}
        n = 2 if n == 1 else 3
    return result
"""


def fills(events: MIRStorageEvents, fn: MIRFunction) -> list[MIRAssign]:
    """The writes that move a built record into a site's owned sum."""
    slots = {s.id: s for s in fn.slots}
    return [s for s in events.writes.values() if isinstance(s.value, MIRWrap) and slots[s.target.root].shape.owned_sum]


def source_function(source: str, name: str = "reuse") -> MIRFunction:
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        fn = next(fn for fn in ctx.thir_functions.values() if fn.name == name)
        result = lower_function(fn, MIRBodyId("reuse", name),
                                definitions=compiled_definitions(compiler, tuple(ctx.thir_constructors.values())))
        assert isinstance(result, MIRFunction), result
        return result


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("safe", [False, True])
def test_source_reuse_has_one_physical_object_per_site(optional: bool, safe: bool) -> None:
    fn = source_function(loop_source(optional, safe))
    events = analyze_storage(prepared(fn))
    assert isinstance(events, MIRStorageEvents)
    # Each initial record, the loop's record built in its pass, then moved into the site's sum.
    assert [stmt.storage_write.mode for stmt in events.writes.values()] == [
        *([MIRStorageWriteMode.INITIALIZE] * (2 if optional else 3)), MIRStorageWriteMode.IN_PLACE]
    fill, = fills(events, fn)
    assert len({stmt.target.root for stmt in events.writes.values()}) == (3 if optional else 4)
    heap = {}
    # Fresh allocation on each iteration incorrectly makes the unsafe twin return 1.
    assert execute(fn, 1, heap=heap) == (0 if optional else 1 if safe else 2)
    assert len(heap) == (2 if optional else 3)
    assert sorted(next(iter(obj.values())) for obj in heap.values()) == ([0, 2] if optional else [0, 0, 2])
    assert dump_storage(events) == dump_storage(analyze_storage(prepared(fn)))
    assert "in_place" in dump_storage(events)
    with pytest.raises(TypeError):
        events.writes[next(iter(events.writes))] = next(iter(events.writes.values()))
    unvisited = {}
    assert execute(fn, 3, heap=unvisited) == 0
    assert len(unvisited) == (1 if optional else 2)


def test_distinct_replacement_sites_do_not_share_backing() -> None:
    source = loop_source(False, False).replace("        current = Cell(n)", """\
        if n == 1:
            current = Cell(n)
        else:
            current = Cell(n)""")
    fn = source_function(source)
    heap = {}
    assert execute(fn, 1, heap=heap) == 1
    assert len(heap) == 4
    events = analyze_storage(prepared(fn))
    assert isinstance(events, MIRStorageEvents)
    sites = [s.target for s in fills(events, fn)]
    assert len(sites) == 2 and sites[0] != sites[1]


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("position", ["method", "constructor"])
def test_reuse_in_methods_and_constructor_tails(optional: bool, position: str) -> None:
    free = loop_source(optional, False).split("def reuse(n: int32) -> int32:\n", 1)[1]
    if position == "method":
        source = CELL_SOURCE + "\nclass Host:\n    def reuse(self, n: int32) -> int32:\n"
    else:
        source = CELL_SOURCE + "\nclass Host:\n    value: int32\n    def __init__(self, n: int32):\n        self.value = 0\n"
        free = free.replace("return result", "self.value = result")
    source += "\n".join("    " + line for line in free.splitlines()) + "\n"
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        constructors = tuple(ctx.thir_constructors.values())
        definitions = MIRDefinitions(constructors)
        if position == "method":
            method = next(fn for fn in ctx.thir_functions.values() if fn.name == "reuse")
            fn = lower_function(method, MIRBodyId("reuse", "reuse"), definitions=definitions)
        else:
            ctor = next(c for c in constructors if c.record_name == "Host")
            fn = lower_constructor(ctor, MIRBodyId("reuse", "Host.__init__"), definitions=definitions)
        assert isinstance(fn, MIRFunction), fn
        events = analyze_storage(prepared(fn))
        assert isinstance(events, MIRStorageEvents)
        assert len(fills(events, fn)) == 1


def test_readonly_holder_can_borrow_reused_mutable_backing() -> None:
    fn = backing_function()
    fn = replace(fn, slots=(*fn.slots[:2], reslot(fn.slots[2], readonly=True), *fn.slots[3:]))
    validate_function(fn)
    assert execute(fn, 7, False) == 7


def backing_function() -> MIRFunction:
    """A loop refilling one owned sum (built empty before it) from a record
    each pass builds in its own region, then holding its payload."""
    body = MIRBodyId("storage", "facts")
    n, store, holder, result, flag, built = (MIRSlotId(body, i) for i in range(6))
    a, loop, end, latch = (MIRBlockId(body, i) for i in range(4))
    root, iteration = MIRRegionId(body, 0), MIRRegionId(body, 1)
    cell = hand_record("Cell", "storage.Cell", value=INT32)
    field = MIRField(MIRFieldId(cell, "value"), INT32)
    slots = (MIRSlot(n, leaf_shape(INT32), MIRSlotKind.PARAMETER),
             MIRSlot(store, optional_shape(OptionalType(cell), owned_shape(cell)), MIRSlotKind.LOCAL,
                     storage_duration=MIRStorageDuration.BODY, residence=root),
             MIRSlot(holder, borrowed_shape(cell), MIRSlotKind.LOCAL, residence=root),
             MIRSlot(result, leaf_shape(INT32), MIRSlotKind.LOCAL, residence=root),
             MIRSlot(flag, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
             MIRSlot(built, owned_shape(cell), MIRSlotKind.TEMPORARY, storage_duration=iteration,
                     residence=iteration))
    return bind_parameters(MIRFunction(body, INT32, slots, (
        MIRBlock(a, (MIRStorageInit(MIRPlace(store), 0, MIRConstant(None)),), MIRGoto(loop), root),
        MIRBlock(loop, (
            MIRAssign(MIRPlace(built), MIRConstruct((n,)), storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE)),
            MIRAssign(MIRPlace(store), MIRWrap(1, MIRPlace(built)),
                      storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE)),
            MIRAssign(MIRPlace(holder), MIRBorrow(MIRPlace(store, (MIRUnionPayload(1),)))),
        ), MIRGoto(latch), iteration),
        MIRBlock(end, (MIRAssign(MIRPlace(result), MIRRead(MIRPlace(holder, (MIRDeref(), field)))),), MIRReturn(result),
                 root),
        MIRBlock(latch, (), MIRBranch(flag, loop, end), root),
    ), a, (MIRRecordLayout(cell, (field,), True, True),),
        regions=(MIRRegion(root, None, a), MIRRegion(iteration, root, loop))))


BUILD, FILL = 0, 1


def rewrite_site(fn: MIRFunction, index: int = BUILD, **changes: object) -> MIRFunction:
    block = fn.blocks[1]
    statements = list(block.statements)
    statements[index] = replace(statements[index], **changes)
    return replace(fn, blocks=(fn.blocks[0], replace(block, statements=tuple(statements)), *fn.blocks[2:]))


@pytest.mark.parametrize("fact,message", [
    (None, "optional field write needs a replacement fact"),
    (MIRStorageWrite(MIRStorageWriteMode.INITIALIZE), "optional field write needs a replacement fact"),
    (MIRStorageWrite("own"), "optional field write needs a replacement fact"),
])
def test_cycles_require_positive_reuse_fact(fact: MIRStorageWrite | None, message: str) -> None:
    # The refill a cycle repeats replaces the sum's payload in place: only that fact admits it.
    fn = backing_function()
    validate_function(fn)
    with pytest.raises(MIRValidationError, match=message):
        validate_function(rewrite_site(fn, FILL, storage_write=fact))


@pytest.mark.parametrize("duration", [None, MIRStorageDuration.CALLER])
def test_reuse_requires_body_storage(duration: MIRStorageDuration | None) -> None:
    fn = backing_function()
    with pytest.raises(MIRValidationError, match="storage duration|local storage"):
        validate_function(replace(fn, slots=(fn.slots[0], replace(fn.slots[1], storage_duration=duration), *fn.slots[2:])))


def test_site_has_one_static_writer_and_valid_initialized_source() -> None:
    fn = backing_function()
    build, fill = fn.blocks[1].statements[:2]
    with pytest.raises(MIRValidationError, match="optional field payload needs a moved temporary"):
        validate_function(replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], statements=(
            build, fill, fill, *fn.blocks[1].statements[2:])), *fn.blocks[2:])))
    with pytest.raises(MIRValidationError, match="read before definite assignment"):
        validate_function(rewrite_site(fn, value=MIRCopy(build.target)))
    with pytest.raises(MIRValidationError, match="record move source or eligibility"):
        validate_function(rewrite_site(fn, value=MIRMove(build.target.root)))
    with pytest.raises(MIRValidationError, match="record write on non-record"):
        validate_function(rewrite_site(fn, target=MIRPlace(fn.slots[3].id), value=MIRConstant(1)))
    with pytest.raises(MIRValidationError, match="optional field payload needs a moved temporary"):
        validate_function(replace(fn, records=(replace(fn.records[0], movable=False),)))


def test_missing_fact_is_uncovered_even_when_acyclic() -> None:
    fn = backing_function()
    fn = replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=MIRGoto(fn.blocks[2].id)), *fn.blocks[2:]))
    fn = rewrite_site(fn, storage_write=None)
    validate_function(fn)
    result = analyze_storage(prepared(fn))
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "missing storage write fact"
    assert "storage not covered" in dump_storage(result)


def test_in_place_owner_and_projection_must_match_exactly() -> None:
    fn = backing_function()
    block = fn.blocks[1]
    holder = fn.slots[2].id
    stmt = MIRAssign(MIRPlace(holder, (MIRDeref(),)), MIRConstruct((fn.slots[0].id,)),
                     storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, holder))
    fn = replace(fn, blocks=(fn.blocks[0], replace(block, statements=(*block.statements, stmt),
                                                 terminator=MIRGoto(fn.blocks[2].id)), *fn.blocks[2:]))
    validate_function(fn)
    for bad in (replace(stmt, storage_write=MIRStorageWrite(MIRStorageWriteMode.IN_PLACE, fn.slots[1].id)),
                replace(stmt, target=MIRPlace(holder)),
                replace(stmt, target=MIRPlace(holder, (MIRDeref(), fn.records[0].fields[0])))):
        with pytest.raises(MIRValidationError, match="invalid in-place"):
            validate_function(replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], statements=(
                *block.statements, bad)), *fn.blocks[2:])))
    validate_function(replace(fn, blocks=(fn.blocks[0], replace(fn.blocks[1], terminator=block.terminator), *fn.blocks[2:])))
