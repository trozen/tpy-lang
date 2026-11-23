"""Named argument backings of loop heads nested in range and iterator loops compose with their CFGs."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..codegen_cpp.context import CodeGenContext
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .testutil import compiled_definitions, placed_storage, prepared, workspace_functions
from .call_contract import MIRSummaryState
from .collect import call_definitions
from .dump import _write_step
from .definitions import MIRDefinitions
from .testutil import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRCall,
    MIRCopy, MIRFunction, MIRNotCovered,
    MIRBranch, MIRIteratorAdvance, MIRIteratorInit, MIRPlace, MIRRangeAdvance,
    MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import inspect_scope_lifetimes
from .prepare import validate_function


Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions]


SOURCE = '''from tpy import int32, nocopy, Array, readonly

@nocopy
class Cell:
    value: int32
    xs: list[int32]
    def __init__(self, value: int32):
        self.value = value
        self.xs = [value]

def items(cell: readonly[Cell]) -> readonly[list[int32]]:
    return cell.xs

def ranges(n: int32) -> int32:
    result = 0
    for i in range(n):
        for v in items(Cell(i)):
            result = v
    else:
        for v in items(Cell(result)):
            result = v
    return result

def descending(n: int32) -> int32:
    last = 42
    for last in range(n, 0, -1):
        for v in items(Cell(last)):
            last = v
    return last

def broken(n: int32) -> int32:
    last = 42
    for i in range(n, 0, -1):
        for v in items(Cell(i)):
            last = v
            break
    else:
        for v in items(Cell(last)):
            last = v
    return last

def written(start: int32, stop: int32) -> int32:
    last = 42
    for i in range(start, stop):
        for v in items(Cell(i)):
            last = v
        start = 99
        stop = 99
        i = 88
    return last

def native(values: list[int32]) -> int32:
    result = 0
    for value in values:
        for v in items(Cell(value)):
            result = v
    else:
        for v in items(Cell(result)):
            result = v
    return result

def records(values: list[Cell], n: int32) -> int32:
    result = 0
    for cell in values:
        for v in items(Cell(n)):
            cell.value = v
        result = cell.value
    return result

def readonly_records(values: readonly[list[Cell]], n: int32) -> int32:
    result = 0
    for cell in values:
        value = cell.value
        for v in items(Cell(value)):
            result = v
    return result

def array(values: Array[int32, 2]) -> int32:
    result = 0
    for value in values:
        for v in items(Cell(value)):
            result = v
    return result

def keys(values: dict[int32, int32]) -> int32:
    result = 0
    for value in values:
        for v in items(Cell(value)):
            result = v
    return result

def members(values: set[int32]) -> int32:
    result = 0
    for value in values:
        for v in items(Cell(value)):
            result = v
    return result

def nested(values: list[int32], n: int32) -> int32:
    result = 0
    for value in values:
        for i in range(n):
            for v in items(Cell(value)):
                result = v
    return result

def early(n: int32, take: bool) -> int32:
    for i in range(n):
        for v in items(Cell(i)):
            if take:
                return v
    return 9

def retained(seed: Cell, n: int32) -> int32:
    result = 0
    for i in range(n):
        for v in items(Cell(i)):
            result = v
    return result

class Runner:
    result: int32
    def __init__(self, n: int32):
        self.result = 0
        for i in range(n):
            for v in items(Cell(i)):
                self.result = v
    def method(self, values: list[int32]) -> int32:
        result = 0
        for value in values:
            for v in items(Cell(value)):
                result = v
        return result
'''


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled) -> Artifacts:
    compiler, modules = compiled
    with activate_compiler(compiler):
        entry = _entry(modules)
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        return ctx, workspace, definitions


def body(artifacts: Artifacts, name: str) -> MIRFunction:
    ctx, workspace, definitions = artifacts
    if name == "Runner":
        ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == name)
        result = lower_constructor(ctor, MIRBodyId("main", name), definitions=definitions,
                                   summaries=workspace.summaries)
    elif name == "method":
        fn = next(fn for fn in ctx.thir_functions.values() if fn.name == name)
        result = lower_function(fn, MIRBodyId("main", name),
                                definitions=definitions, summaries=workspace.summaries)
    else:
        result = next(b for identity, b in workspace_functions(workspace).items()
                      if identity.declaration.split("@")[0] == name)
    assert isinstance(result, MIRFunction), result
    return result


def _dump_path(write) -> str:
    return f"param{write.parameter}" + "".join(map(_write_step, write.path))


def storage(fn: MIRFunction) -> list[MIRSlot]:
    return placed_storage(fn)


@pytest.mark.parametrize("name", ["ranges", "descending", "broken", "written", "native", "records",
                                  "readonly_records", "array", "keys", "members", "nested",
                                  "early", "method", "Runner"])
def test_for_callers_reach_valid_mir_without_copies(artifacts: Artifacts, name: str) -> None:
    fn = body(artifacts, name)
    validate_function(fn)
    assert storage(fn)
    values = [s.value for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign)]
    assert any(isinstance(v, MIRCall) for v in values)
    assert not any(isinstance(v, MIRCopy) for v in values)
    result = inspect_scope_lifetimes(prepared(fn))
    assert not isinstance(result, MIRNotCovered) and not result.conflicts
    if name in ("Runner", "method"):
        return
    summary = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
    if name == "records":
        # A field write through an element of a container parameter is published under its elements region.
        assert summary.state is MIRSummaryState.KNOWN, summary
        assert [_dump_path(w) for w in summary.summary.writes] == ["param0[elements].value"]
        return
    # Each activation's argument temporary is the caller's private storage, only lent to the callee.
    assert summary.state is MIRSummaryState.KNOWN, summary
    assert not summary.summary.writes and not summary.summary.returns


@pytest.mark.parametrize("name", ["ranges", "written"])
def test_range_counter_survives_each_body_activation(artifacts: Artifacts, name: str) -> None:
    fn = body(artifacts, name)
    advance = next(s for b in fn.blocks for s in b.statements
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIRRangeAdvance))
    counter = fn.slots[advance.target.root.index]
    backing = storage(fn)[0]
    region = fn.regions[backing.residence.index]
    # The head scope sits in the range body, inside the counter's region.
    assert fn.regions[region.parent.index].parent == counter.residence
    flow = MIRRegionFlow(fn)
    entries = [t for t in flow.edges.values() if region.id in t.entered]
    exits = [t for t in flow.edges.values() if region.id in t.exited and t.target is not None
             and fn.blocks[t.target.index].region == region.parent]
    assert entries and exits
    assert all(backing.id in t.reset and counter.id not in t.reset for t in entries)
    assert all(backing.id in t.ended and counter.id not in t.reset for t in exits)


def test_early_return_ends_temporary_and_counter_scopes(artifacts: Artifacts) -> None:
    fn = body(artifacts, "early")
    flow = MIRRegionFlow(fn)
    ended = [t for t in flow.edges.values() if t.target is None and t.ended]
    assert ended and all(len(t.exited) >= 2 for t in ended)
    returned = [b for b in fn.blocks if isinstance(b.terminator, MIRReturn)]
    assert len(returned) >= 2


def test_retaining_a_real_for_argument_reports_its_body_end(artifacts: Artifacts) -> None:
    fn = body(artifacts, "retained")
    seed = next(s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.name == "seed")
    backing, = storage(fn)
    holder, copied = (MIRSlotId(fn.id, len(fn.slots) + i) for i in range(2))
    slots = fn.slots + tuple(replace(seed, id=sid, kind=MIRSlotKind.LOCAL, name=None,
                                    residence=MIRRegionId(fn.id, 0)) for sid in (holder, copied))
    blocks = []
    for block in fn.blocks:
        statements = list(block.statements)
        if block.id == fn.entry:
            statements.insert(0, MIRAssign(MIRPlace(holder), MIRAlias(seed.id)))
        for stmt in block.statements:
            # The call lent the argument, not the construction filling its storage.
            if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)
                    and stmt.value.summary.callee.identity.name != "__init__"):
                statements.append(MIRAssign(MIRPlace(holder), MIRAlias(stmt.value.arguments[0])))
        if isinstance(block.terminator, MIRReturn):
            statements.append(MIRAssign(MIRPlace(copied), MIRAlias(holder)))
        blocks.append(replace(block, statements=tuple(statements)))
    retained = replace(fn, slots=slots, blocks=tuple(blocks))
    validate_function(retained)
    result = inspect_scope_lifetimes(prepared(retained))
    assert result.conflicts and all(c.ended == MIRPlace(backing.id) for c in result.conflicts)
    assert all(c.holder.root == holder for c in result.conflicts)


def test_missing_for_scope_evidence_does_not_gain_coverage(artifacts: Artifacts) -> None:
    ctx, workspace, definitions = artifacts
    fn = next(f for f in ctx.thir_functions.values() if f.name == "ranges")
    result = lower_function(replace(fn, temp_plan=None), body(artifacts, "ranges").id, definitions=definitions,
                            summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "named argument needs complete temporary plan"


@pytest.mark.parametrize("loop", [
    "for i in range(0, n, 2):\n        for v in items(Cell(i)):\n            result = v",
    "for i in range(n):\n        print(i)\n        for v in items(Cell(i)):\n            result = v",
], ids=["non-unit-step", "unknown-body-producer"])
def test_source_for_boundaries_leave_the_whole_body_unplanned(loop: str) -> None:
    compiler, modules = _compile(SOURCE + '''
def rejected(n: int32) -> int32:
    result = 0
    for v in items(Cell(n)):
        result = v
    ''' + loop + '''
    return result
''')
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    fn = next(f for f in ctx.thir_functions.values() if f.name == "rejected")
    assert fn.temp_plan is None
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    result = next(b for identity, b in workspace_functions(workspace).items()
                  if identity.declaration.split("@")[0] == "rejected")
    assert isinstance(result, MIRNotCovered) and result.reason == "named argument needs complete temporary plan"


def _ancestors(fn: MIRFunction, region: MIRRegionId) -> list[MIRRegionId]:
    chain = []
    while isinstance(region, MIRRegionId):
        chain.append(region)
        region = fn.regions[region.index].parent
    return chain


def test_body_and_else_head_backings_end_each_pass(artifacts: Artifacts) -> None:
    # The body's and the else's loop heads keep their arguments in regions
    # of their own under the outer loop's; a pass ends the body's, never
    # the outer iterator.
    fn = body(artifacts, "native")
    iterator = next(s.target.root for b in fn.blocks for s in b.statements
                    if isinstance(s, MIRAssign) and isinstance(s.value, MIRIteratorInit))
    inside, otherwise = storage(fn)
    outer = fn.slots[iterator.index].residence
    assert outer in _ancestors(fn, inside.residence)
    assert inside.residence != otherwise.residence
    assert inside.residence not in _ancestors(fn, otherwise.residence)
    advance = next(b for b in fn.blocks if any(
        isinstance(s, MIRAssign) and isinstance(s.value, MIRIteratorAdvance)
        and s.target.root == iterator for s in b.statements))
    flow = MIRRegionFlow(fn)
    leaves = [t for t in flow.edges.values() if inside.residence in t.exited]
    assert leaves and all(inside.id in t.ended and iterator not in t.reset
                          for t in leaves)
    passes = [t for t in flow.edges.values() if t.target == advance.id]
    assert passes and all(iterator not in t.reset for t in passes)


def test_range_zero_trip_skips_the_body_head_backing(artifacts: Artifacts) -> None:
    fn = body(artifacts, "broken")
    inside, otherwise = storage(fn)
    counter = next(s.target.root for b in fn.blocks for s in b.statements
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIRRangeAdvance))
    counter_region = fn.slots[counter.index].residence
    flow = MIRRegionFlow(fn)
    # The inner loop's break leaves its head scope: the backing ends.
    breaks = [t for t in flow.edges.values() if t.target is not None
              and inside.residence in t.exited and counter_region not in t.exited]
    assert breaks and all(inside.id in t.ended for t in breaks)
    # No pass: the counter's scope ends, the body's backing is never
    # entered, and control reaches the else, whose head keeps its own.
    head = next(b for b in fn.blocks if b.region == counter_region
                and isinstance(b.terminator, MIRBranch))
    empty = next(t for e, t in flow.edges.items() if e.source == head.id and e.arm == 1)
    assert counter_region in empty.exited and counter in empty.reset
    assert inside.residence not in empty.entered and inside.residence not in empty.exited
    assert inside.id not in empty.reset and inside.id not in empty.ended
    assert fn.blocks[empty.target.index].region in _ancestors(fn, otherwise.residence)
