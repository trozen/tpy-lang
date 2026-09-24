"""Returned argument aliases retain named backing identity through calls and loop activations."""

from dataclasses import replace

import pytest

from ..codegen_cpp.context import CodeGenContext
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.storage_facts import collect_storage_facts
from ..thir.temp_plan import prepare_temporaries
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL
from .call_contract import MIRSummaryState
from .collect import call_definitions
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies
from .liveness import analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRBodyKind, MIRCall, MIRConstruct,
    MIRCopy, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRRecordStorageInit,
    MIRRecordStorageKind, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRValueKind,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import inspect_scope_lifetimes
from .validate import validate_function


SOURCE = '''from tpy import int32, readonly, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def method(self, value: int32) -> int32:
        saved = observe(Cell(value))
        return saved.value

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        saved = observe(Cell(value))
        self.value = saved.value

def observe(cell: Cell) -> readonly[Cell]:
    return cell

def choose(flag: bool, a: Cell, b: Cell) -> readonly[Cell]:
    return a if flag else b

def independent(a: Cell, b: Cell) -> readonly[Cell]:
    value = b.value
    return a

def eager(value: int32) -> int32:
    saved = observe(Cell(value))
    return saved.value

def selected(flag: bool, value: int32) -> int32:
    saved = observe(Cell(value))
    return saved.value

def mixed(flag: bool, owner: Cell) -> int32:
    saved = choose(flag, owner, Cell(7))
    owner.value = 9
    return saved.value

def unreturned(owner: Cell) -> int32:
    saved = independent(owner, Cell(7))
    owner.value = 9
    return saved.value

def loop(flag: bool, value: int32, seed: Cell) -> int32:
    answer = 0
    while flag:
        saved = observe(Cell(value))
        answer = saved.value
        flag = False
    return answer

def ranges(value: int32, seed: Cell) -> int32:
    answer = 0
    for i in range(2):
        saved = observe(Cell(value))
        answer = saved.value
    return answer
'''


Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    return _compile_workspace(SOURCE)


def _compile_workspace(source: str) -> Artifacts:
    compiler, modules = _compile(source)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    return ctx, analyze_call_workspace(call_definitions(ctx, entry.name), definitions), definitions


def _thir(artifacts: Artifacts, name: str) -> th.THIRFunction:
    return next(fn for fn in artifacts[0].thir_functions.values() if fn.name == name)


def _lower(artifacts: Artifacts, fn: th.THIRFunction) -> MIRFunction | MIRNotCovered:
    _, workspace, definitions = artifacts
    return lower_function(fn, MIRBodyId("main", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                          definitions=definitions, summaries=workspace.summaries)


def _lazy(artifacts: Artifacts) -> MIRFunction:
    # No admitted source selects between temporary-bearing borrowed calls; build that shape.
    fn = _thir(artifacts, "selected")
    decl, *rest = fn.body
    other = replace(decl.init, args=tuple(replace(arg, init=replace(arg.init)) for arg in decl.init.args))
    select = th.THIRIfExpr(decl.init.result_type, th.THIRName(BOOL, "flag"), decl.init, other,
                           form=th.Form.BORROW)
    body = (replace(decl, init=select), *rest)
    plan = prepare_temporaries(body)
    assert [p.optional for p in plan.placements] == [True, True]
    result = _lower(artifacts, replace(fn, body=body, temp_plan=plan,
                                       storage_facts=collect_storage_facts(body, plan)))
    assert isinstance(result, MIRFunction), result
    return result


def _body(artifacts: Artifacts, name: str) -> MIRFunction:
    ctx, workspace, definitions = artifacts
    if name == "lazy":
        return _lazy(artifacts)
    if name == "method":
        fn = next(fn for fn in ctx.thir_functions.values() if fn.name == name)
        result = lower_function(fn, MIRBodyId("main", name), kind=MIRBodyKind.METHOD,
                                definitions=definitions, summaries=workspace.summaries)
    elif name == "Caller":
        ctor = next(ctor for ctor in ctx.thir_constructors.values() if ctor.record_name == name)
        result = lower_constructor(ctor, MIRBodyId("main", name), definitions=definitions,
                                   summaries=workspace.summaries)
    else:
        result = next(body for identity, body in workspace.bodies.items()
                      if identity.declaration.split("@")[0] == name)
    assert isinstance(result, MIRFunction), result
    return result


def _storage(body: MIRFunction) -> list[MIRSlot]:
    return [slot for slot in body.slots if slot.value_kind is MIRValueKind.RECORD_STORAGE]


@pytest.mark.parametrize("name", ["eager", "lazy", "mixed", "unreturned", "loop", "ranges", "method", "Caller"])
def test_returned_temporaries_are_valid_aliases(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    validate_function(body)
    assert _storage(body)
    values = [stmt.value for block in body.blocks for stmt in block.statements if isinstance(stmt, MIRAssign)]
    assert any(isinstance(value, MIRCall) and value.summary.returns for value in values)
    assert not any(isinstance(value, MIRCopy) for value in values)
    result = inspect_scope_lifetimes(body)
    assert not isinstance(result, MIRNotCovered) and not result.conflicts
    if name not in ("lazy", "method", "Caller"):
        summary = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
        assert summary.state is MIRSummaryState.OPAQUE
        assert summary.reason == "summary storage or value shape"


@pytest.mark.parametrize("name", ["eager", "lazy", "mixed", "unreturned"])
def test_return_origins_substitute_only_selected_arguments(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    deps = analyze_dependencies(body, analyze_liveness(body))
    assert not isinstance(deps, MIRNotCovered)
    backing = {slot.id for slot in _storage(body)}
    for block in body.blocks:
        for index, stmt in enumerate(block.statements):
            if not (isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall) and stmt.value.summary.returns):
                continue
            before = deps.referents[MIRPoint(block.id, index)]
            actual = deps.referents[MIRPoint(block.id, index + 1)][stmt.target]
            expected = frozenset(root for arg in stmt.value.summary.returns
                                 for root in before[MIRPlace(stmt.value.arguments[arg])])
            assert actual == expected and actual
            assert all(root.external or root.place.root in backing for root in actual)
            assert any(root.external for root in actual) is (name in ("mixed", "unreturned"))
            assert any(not root.external for root in actual) is (name != "unreturned")


def test_lazy_backing_initializes_in_each_selected_arm(artifacts: Artifacts) -> None:
    body = _lazy(artifacts)
    backing = _storage(body)
    assert len(backing) == 2 and all(slot.record_storage is MIRRecordStorageKind.OPTIONAL for slot in backing)
    declarations = [block.id for block in body.blocks for stmt in block.statements
                    if isinstance(stmt, MIRRecordStorageInit)]
    assert declarations == [body.entry, body.entry]
    constructions = [(block.id, stmt.target.root) for block in body.blocks for stmt in block.statements
                     if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRConstruct)]
    assert len({block for block, _ in constructions}) == 2
    assert all(block != body.entry for block, _ in constructions)
    assert {slot for _, slot in constructions} == {slot.id for slot in backing}


@pytest.mark.parametrize("name", ["loop", "ranges"])
def test_iteration_backing_ends_and_resets(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    backing = _storage(body)
    flow = MIRRegionFlow(body)
    for slot in backing:
        entries = [edge for edge in flow.edges.values() if slot.residence in edge.entered]
        exits = [edge for edge in flow.edges.values() if slot.residence in edge.exited]
        assert entries and exits
        assert all(slot.id in edge.reset for edge in entries)
        assert all(slot.id in edge.ended for edge in exits)


def test_missing_plan_does_not_invent_backing(artifacts: Artifacts) -> None:
    fn = _thir(artifacts, "eager")
    result = _lower(artifacts, replace(fn, temp_plan=None))
    assert isinstance(result, MIRNotCovered)
    assert result.reason == "named argument needs complete temporary plan"


@pytest.mark.parametrize("name", ["loop", "ranges"])
def test_retained_call_result_observes_iteration_end(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    seed = next(slot for slot in body.slots if slot.name == "seed")
    saved = next(slot for slot in body.slots if slot.name == "saved")
    holder, copied = (MIRSlotId(body.id, len(body.slots) + i) for i in range(2))
    slots = body.slots + tuple(replace(saved, id=sid, kind=MIRSlotKind.LOCAL,
                                      name=None, residence=MIRRegionId(body.id, 0)) for sid in (holder, copied))
    blocks = []
    captured = False
    for block in body.blocks:
        statements = []
        if block.id == body.entry:
            statements.append(MIRAssign(MIRPlace(holder), MIRAlias(seed.id)))
        for stmt in block.statements:
            statements.append(stmt)
            if not captured and isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall):
                # Retain the returned holder itself, not the call's actual argument.
                statements.append(MIRAssign(MIRPlace(holder), MIRAlias(stmt.target.root)))
                captured = True
        if isinstance(block.terminator, MIRReturn):
            statements.append(MIRAssign(MIRPlace(copied), MIRAlias(holder)))
        blocks.append(replace(block, statements=tuple(statements)))
    retained = replace(body, slots=slots, blocks=tuple(blocks))
    validate_function(retained)
    result = inspect_scope_lifetimes(retained)
    assert not isinstance(result, MIRNotCovered)
    assert result.conflicts and all(c.ended == MIRPlace(_storage(body)[0].id) for c in result.conflicts)
    assert all(c.holder.root == holder for c in result.conflicts)


@pytest.mark.parametrize("extra, reason", [
    ('''def explicit(cell: readonly[Cell]) -> readonly[Cell]:
    return cell
def rejected() -> int32:
    saved = explicit(Cell(1))
    return saved.value
''', "call needs borrowed record name"),
    ('''def mutable(cell: Cell) -> Cell:
    cell.value = 2
    return cell
def rejected() -> int32:
    saved = mutable(Cell(1))
    return saved.value
''', "named argument needs readonly record constructor"),
    ('''def identity(value: int32) -> int32:
    return value
def rejected(value: int32) -> int32:
    saved = observe(Cell(identity(value)))
    return saved.value
''', "named constructor needs stable scalar operands"),
    ('''def writes(owner: Cell, other: Cell) -> readonly[Cell]:
    owner.value = 2
    return other
def rejected(owner: Cell) -> int32:
    saved = writes(owner, Cell(1))
    return saved.value
''', "named argument crosses unproven evaluation order"),
])
def test_unverified_actuals_and_order_stay_uncovered(extra: str, reason: str) -> None:
    _, workspace, _ = _compile_workspace(SOURCE + extra)
    result = next(body for identity, body in workspace.bodies.items()
                  if identity.declaration.split("@")[0] == "rejected")
    assert isinstance(result, MIRNotCovered), result
    assert result.reason == reason
