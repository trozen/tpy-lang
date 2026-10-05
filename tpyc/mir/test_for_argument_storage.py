"""Named argument lifetimes compose with existing range and iterator CFGs."""

from dataclasses import replace

import pytest

from ..codegen_cpp.context import CodeGenContext
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.temp_plan import prepare_temporaries
from ..thir.test_for_temp_plan import SOURCE
from ..thir.testutil import _compile, _entry
from .call_contract import MIRSummaryState
from .collect import call_definitions
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBranch, MIRCall, MIRConstruct,
    MIRCopy, MIRFunction, MIRGoto, MIRIteratorAdvance, MIRIteratorInit, MIRNotCovered,
    MIRPlace, MIRRangeAdvance, MIRRecordStorageInit, MIRRecordStorageKind,
    MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRValueKind,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import inspect_scope_lifetimes
from .validate import validate_function


Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE + '''
def retained(seed: Cell, n: int32) -> int32:
    result = 0
    for i in range(n):
        result = read(Cell(i))
    return result
''')
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
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
        result = next(b for identity, b in workspace.bodies.items()
                      if identity.declaration.split("@")[0] == name)
    assert isinstance(result, MIRFunction), result
    return result


def storage(fn: MIRFunction) -> list[MIRSlot]:
    return [s for s in fn.slots if s.value_kind is MIRValueKind.OWNED]


@pytest.mark.parametrize("name", ["ranges", "descending", "written", "native", "records",
                                  "readonly_records", "array", "keys", "members", "nested",
                                  "early", "method", "Runner"])
def test_for_callers_reach_valid_mir_without_copies(artifacts: Artifacts, name: str) -> None:
    fn = body(artifacts, name)
    validate_function(fn)
    assert storage(fn)
    values = [s.value for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign)]
    assert any(isinstance(v, MIRCall) for v in values)
    assert not any(isinstance(v, MIRCopy) for v in values)
    result = inspect_scope_lifetimes(fn)
    assert not isinstance(result, MIRNotCovered) and not result.conflicts
    if name in ("Runner", "method"):
        return
    summary = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
    if name == "records":
        # A field write through an element of a container parameter is a write origin no summary publishes yet.
        assert summary.state is MIRSummaryState.OPAQUE and summary.reason == "summary unsupported write origin"
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
    assert region.parent == counter.residence
    flow = MIRRegionFlow(fn)
    entries = [t for t in flow.edges.values() if region.id in t.entered]
    exits = [t for t in flow.edges.values() if region.id in t.exited and t.target is not None
             and fn.blocks[t.target.index].region == counter.residence]
    assert entries and exits
    assert all(backing.id in t.reset and counter.id not in t.reset for t in entries)
    assert all(backing.id in t.ended and counter.id not in t.reset for t in exits)


def test_native_iterator_survives_body_end_and_else_is_outside(artifacts: Artifacts) -> None:
    fn = body(artifacts, "native")
    iterator = next(s.target.root for b in fn.blocks for s in b.statements
                    if isinstance(s, MIRAssign) and isinstance(s.value, MIRIteratorInit))
    inside, otherwise = storage(fn)
    outer = fn.slots[iterator.index].residence
    assert fn.regions[inside.residence.index].parent == outer
    assert fn.regions[otherwise.residence.index].parent == outer
    assert inside.residence != otherwise.residence
    advance = next(b for b in fn.blocks if any(isinstance(s, MIRAssign)
                   and isinstance(s.value, MIRIteratorAdvance) for s in b.statements))
    exits = [t for t in MIRRegionFlow(fn).edges.values() if t.target == advance.id]
    assert exits and all(inside.id in t.ended and iterator not in t.reset for t in exits)


def test_range_break_ends_counter_and_backing_but_zero_trip_skips_body(artifacts: Artifacts) -> None:
    fn = body(artifacts, "descending")
    inside, otherwise = storage(fn)
    counter = next(s.target.root for b in fn.blocks for s in b.statements
                   if isinstance(s, MIRAssign) and isinstance(s.value, MIRRangeAdvance))
    counter_region = fn.slots[counter.index].residence
    flow = MIRRegionFlow(fn)
    breaks = [t for t in flow.edges.values() if t.target is not None
              and inside.residence in t.exited and counter_region in t.exited]
    assert len(breaks) == 1
    assert inside.id in breaks[0].ended and counter in breaks[0].reset
    assert otherwise.residence not in breaks[0].entered
    assert isinstance(fn.blocks[breaks[0].target.index].terminator, MIRReturn)

    head = next(b for b in fn.blocks if b.region == counter_region
                and isinstance(b.terminator, MIRBranch))
    empty = next(t for e, t in flow.edges.items() if e.source == head.id and e.arm == 1)
    assert counter_region in empty.exited and counter in empty.reset
    assert inside.residence not in empty.entered and inside.residence not in empty.exited
    assert inside.id not in empty.reset and inside.id not in empty.ended
    normal = fn.blocks[empty.target.index]
    assert normal.region == otherwise.residence and otherwise.residence in empty.entered
    assert isinstance(normal.terminator, MIRGoto)
    assert normal.terminator.target == breaks[0].target


@pytest.mark.parametrize("name", ["ranges", "native"])
def test_lazy_backing_exists_before_selection_but_payload_does_not(artifacts: Artifacts, name: str) -> None:
    fn = body(artifacts, name)
    backing = storage(fn)[0]
    assert backing.record_storage is MIRRecordStorageKind.OPTIONAL
    declaration = next(b for b in fn.blocks if any(isinstance(s, MIRRecordStorageInit)
                       and s.target.root == backing.id for s in b.statements))
    initialization = next(b for b in fn.blocks if any(isinstance(s, MIRAssign)
                          and s.target.root == backing.id and isinstance(s.value, MIRConstruct)
                          for s in b.statements))
    assert declaration.region == initialization.region == backing.residence
    assert isinstance(declaration.terminator, MIRBranch)
    assert initialization.id == declaration.terminator.then
    assert initialization.id != declaration.terminator.otherwise


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
    slots = fn.slots + tuple(replace(seed, id=sid, kind=MIRSlotKind.LOCAL, name=None, passing=None,
                                    residence=MIRRegionId(fn.id, 0)) for sid in (holder, copied))
    blocks = []
    for block in fn.blocks:
        statements = list(block.statements)
        if block.id == fn.entry:
            statements.insert(0, MIRAssign(MIRPlace(holder), MIRAlias(seed.id)))
        for stmt in block.statements:
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall):
                statements.append(MIRAssign(MIRPlace(holder), MIRAlias(stmt.value.arguments[0])))
        if isinstance(block.terminator, MIRReturn):
            statements.append(MIRAssign(MIRPlace(copied), MIRAlias(holder)))
        blocks.append(replace(block, statements=tuple(statements)))
    retained = replace(fn, slots=slots, blocks=tuple(blocks))
    validate_function(retained)
    result = inspect_scope_lifetimes(retained)
    assert result.conflicts and all(c.ended == MIRPlace(backing.id) for c in result.conflicts)
    assert all(c.holder.root == holder for c in result.conflicts)


def test_missing_for_scope_evidence_does_not_gain_coverage(artifacts: Artifacts) -> None:
    ctx, workspace, definitions = artifacts
    fn = next(f for f in ctx.thir_functions.values() if f.name == "ranges")
    result = lower_function(replace(fn, temp_plan=None), body(artifacts, "ranges").id, definitions=definitions,
                            summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "named argument needs complete temporary plan"


def test_existing_iteration_fact_gate_is_preserved(artifacts: Artifacts) -> None:
    ctx, workspace, definitions = artifacts
    fn = next(f for f in ctx.thir_functions.values() if f.name == "native")
    statements = tuple(replace(s, iteration=None) if isinstance(s, th.THIRForEach) else s for s in fn.body)
    damaged = replace(fn, body=statements, temp_plan=prepare_temporaries(statements))
    result = lower_function(damaged, body(artifacts, "native").id,
                            definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "missing or invalid native iteration facts"


@pytest.mark.parametrize("loop", [
    "for i in range(0, n, 2):\n        result = read(Cell(i))",
    "for i in range(n):\n        print(i)\n        result = read(Cell(i))",
], ids=["non-unit-step", "unknown-body-producer"])
def test_source_for_boundaries_leave_the_whole_body_unplanned(loop: str) -> None:
    compiler, modules = _compile(SOURCE + '''
def rejected(n: int32) -> int32:
    result = read(Cell(n))
    ''' + loop + '''
    return result
''')
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    fn = next(f for f in ctx.thir_functions.values() if f.name == "rejected")
    assert fn.temp_plan is None
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    result = next(b for identity, b in workspace.bodies.items()
                  if identity.declaration.split("@")[0] == "rejected")
    assert isinstance(result, MIRNotCovered) and result.reason == "named argument needs complete temporary plan"
