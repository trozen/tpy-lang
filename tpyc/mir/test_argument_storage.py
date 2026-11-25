"""Named argument backing a loop head keeps follows the scope the loop opens."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from .. import cli
from ..codegen_cpp.context import CodeGenContext
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.temp_plan import prepare_temporaries
from ..thir.lower import functions as function_lowering
from .call_contract import MIRSummaryState
from .collect import call_definitions
from .definitions import MIRDefinitions
from .testutil import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRCall, MIRCopy, MIRFunction, MIRGoto, MIRSlot, MIRNotCovered,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import inspect_scope_lifetimes
from .testutil import placed_storage, compiled_definitions, prepared, workspace_functions
from .prepare import validate_function


SOURCE = '''from tpy import int32, nocopy, readonly

@nocopy
class Cell:
    value: int32
    xs: list[int32]
    def __init__(self, value: int32):
        self.value = value
        self.xs = [value]
    def method(self, value: int32) -> int32:
        t = 0
        for c in items(Cell(value)):
            t += c
        return t

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        for c in items(Cell(value)):
            self.value = c

def items(cell: readonly[Cell]) -> readonly[list[int32]]:
    return cell.xs

def read(cell: readonly[Cell]) -> int32:
    return cell.value

def two(n: int32, cell: readonly[Cell]) -> readonly[list[int32]]:
    return cell.xs

def eager(value: int32) -> int32:
    answer = 0
    for c in items(Cell(value)):
        answer = c
    else:
        answer += 1
    return answer

def nested(value: int32, n: int32) -> int32:
    answer = 0
    for i in range(n):
        for c in items(Cell(i)):
            answer = c
    return answer

def early(value: int32) -> int32:
    for c in items(Cell(value)):
        return c
    return 0

def broken(value: int32, stop: bool) -> int32:
    answer = 0
    for c in items(Cell(value)):
        if stop:
            break
        answer = c
    return answer

def identity(value: int32) -> int32:
    return value

def nested_operand(value: int32) -> int32:
    t = 0
    for c in items(Cell(identity(value))):
        t += c
    return t

def mutable_items(cell: Cell) -> readonly[list[int32]]:
    cell.value = 1
    return cell.xs

def effect(value: int32) -> int32:
    t = 0
    for c in mutable_items(Cell(value)):
        t += c
    return t
'''


Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions, str]


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled) -> Artifacts:
    compiler, modules = compiled
    with activate_compiler(compiler):
        entry = _entry(modules)
        (_, cpp), ctx = compiler.generate_code_and_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        return ctx, workspace, definitions, cpp


def _body(artifacts: Artifacts, name: str) -> MIRFunction:
    ctx, workspace, definitions, _ = artifacts
    if name == "Cell.method":
        fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "method")
        body = lower_function(fn, MIRBodyId("main", name),
                              definitions=definitions, summaries=workspace.summaries)
    elif name == "Caller.__init__":
        ctor = next(ctor for ctor in ctx.thir_constructors.values() if ctor.record_name == "Caller")
        body = lower_constructor(ctor, MIRBodyId("main", name), definitions=definitions,
                                 summaries=workspace.summaries)
    else:
        body = next(value for key, value in workspace_functions(workspace).items() if key.declaration.split("@")[0] == name)
    assert isinstance(body, MIRFunction), body
    return body


def _storage(body: MIRFunction) -> list[MIRSlot]:
    return placed_storage(body)


@pytest.mark.parametrize("name", ["eager", "nested", "early", "broken", "Cell.method", "Caller.__init__"])
def test_named_callers_are_valid_without_copying(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    validate_function(body)
    assert _storage(body)
    statements = [stmt for block in body.blocks for stmt in block.statements if isinstance(stmt, MIRAssign)]
    assert any(isinstance(stmt.value, MIRCall) for stmt in statements)
    assert not any(isinstance(stmt.value, MIRCopy) for stmt in statements)
    assert "const Cell&" in artifacts[3]
    result = inspect_scope_lifetimes(prepared(body))
    assert not isinstance(result, MIRNotCovered), result
    assert not result.conflicts


def test_head_storage_spans_the_whole_loop(artifacts: Artifacts) -> None:
    # The fresh argument of the head lives until the loop statement ends --
    # its body and its else included -- and ends only on the edges leaving it.
    body = _body(artifacts, "eager")
    first, = _storage(body)
    regions = {region.id: region for region in body.regions}
    head = regions[first.storage_duration]
    assert any(region.parent == head.id for region in body.regions)
    flow = MIRRegionFlow(body)
    blocks = {block.id: block for block in body.blocks}
    exits = [transition for transition in flow.edges.values() if first.id in transition.ended]
    assert exits and all(head.id in transition.exited for transition in exits)
    assert all(blocks[transition.target].region == head.parent
               for transition in exits if transition.target is not None)


def test_inner_head_storage_resets_each_outer_pass(artifacts: Artifacts) -> None:
    body = _body(artifacts, "nested")
    storage = _storage(body)[0]
    region = next(region for region in body.regions if region.id == storage.storage_duration)
    flow = MIRRegionFlow(body)
    entering = [transition for transition in flow.edges.values() if region.id in transition.entered]
    exiting = [transition for transition in flow.edges.values() if region.id in transition.exited]
    assert entering and exiting
    assert all(storage.id in transition.reset for transition in entering)
    assert all(storage.id in transition.ended for transition in exiting)
    predecessors = [block for block in body.blocks if isinstance(block.terminator, MIRGoto)
                    and block.terminator.target == region.entry]
    assert predecessors and all(block.region == region.parent for block in predecessors)


def test_owning_callers_summarize_known_and_excluded_calls_stay_uncovered(artifacts: Artifacts) -> None:
    workspace = artifacts[1]
    # A named argument temporary is storage the caller keeps to its end and
    # only lends: private to the body, so it publishes nothing.
    for name in ("eager", "nested", "early", "broken"):
        result = workspace.summaries[th.THIRFunctionIdentity("main", name)]
        assert result.state is MIRSummaryState.KNOWN, (name, result)
        assert not result.summary.writes and not result.summary.returns, name
    for name, reason in (("nested_operand", "named temporary needs stable scalar operands"),
                         ("effect", "named argument needs readonly record constructor")):
        body = next(value for key, value in workspace_functions(workspace).items() if key.declaration.split("@")[0] == name)
        assert isinstance(body, MIRNotCovered) and body.reason == reason, body


def test_all_source_positions_preserve_cpp_without_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, modules = _compile(SOURCE)
    expected, ctx = compiler.generate_code_and_thir(_entry(modules))
    for name in ("method", "eager", "nested", "broken"):
        assert next(fn for node, fn in ctx.thir_functions.items() if node.name == name).temp_plan is not None
    assert next(ctor for ctor in ctx.thir_constructors.values() if ctor.record_name == "Caller").temp_plan is not None
    monkeypatch.setattr(function_lowering, "prepare_temporaries", lambda body: None)
    compiler, modules = _compile(SOURCE)
    actual, _ = compiler.generate_code_and_thir(_entry(modules))
    assert actual == expected


@pytest.mark.parametrize(("extra", "reason", "blocked_callee"), [
    ('''def bump(cell: Cell) -> int32:
    cell.value += 1
    return cell.value
def rejected(cell: Cell, value: int32) -> bool:
    for c in two(bump(cell), Cell(value)):
        return True
    return False
''', "order-sensitive eager operands", None),
    ('''class Wide:
    pair: tuple[tuple[int32, int32], int32]
    def __init__(self, value: int32):
        self.pair = ((value, value), value)
def ignore_wide(cell: readonly[Wide]) -> int32:
    return 0
def rejected(seed: Cell) -> int32:
    for c in two(ignore_wide(Wide(1)), seed):
        return c
    return 0
''', "summary record: unsupported record fields", "ignore_wide"),
    ('''class Hook:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def __del__(self):
        pass
def ignore_hook(cell: readonly[Hook]) -> int32:
    return 0
def rejected(seed: Cell) -> int32:
    for c in two(ignore_hook(Hook(1)), seed):
        return c
    return 0
''', "summary record: custom record special member", "ignore_hook"),
    ('''def noisy(cell: readonly[Cell]) -> int32:
    print(cell.value)
    return cell.value
def rejected(seed: Cell) -> int32:
    for c in two(noisy(Cell(1)), seed):
        return c
    return 0
''', "call needs finalized known summary", None),
    ('''global_value = 1
def rejected() -> int32:
    for c in items(Cell(global_value)):
        return c
    return 0
''', "named temporary needs stable scalar operands", None),
    ('''def rejected(value: int32 | None) -> int32:
    if value is not None:
        for c in items(Cell(value)):
            return c
    return 0
''', "named argument needs complete temporary plan", None),
    ('''def rejected(value: int32 | bool) -> int32:
    if isinstance(value, int32):
        for c in items(Cell(value)):
            return c
    return 0
''', "named argument needs complete temporary plan", None),
], ids=["order", "record-layout-summary", "record-hook-summary", "effectful-summary",
        "global-constructor-input", "unplanned-optional-narrowing", "unplanned-union-narrowing"])
def test_source_boundaries_reject_at_the_expected_gate(
        extra: str, reason: str, blocked_callee: str | None) -> None:
    compiler, modules = _compile(SOURCE + extra)
    with activate_compiler(compiler):
        entry = _entry(modules)
        _, ctx = compiler.generate_code_and_thir(entry)
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
        result = next(body for identity, body in workspace_functions(workspace).items()
                      if identity.declaration.split("@")[0] == "rejected")
        assert isinstance(result, MIRNotCovered), result
        if blocked_callee is not None:
            assert result.reason == "call needs finalized known summary"
            summary = workspace.summaries[th.THIRFunctionIdentity("main", blocked_callee)]
            assert summary.state is MIRSummaryState.OPAQUE and summary.reason == reason
        else:
            assert result.reason == reason


def test_missing_plan_stays_uncovered(artifacts: Artifacts) -> None:
    ctx, workspace, definitions, _ = artifacts
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "eager")
    result = lower_function(replace(fn, temp_plan=None), _body(artifacts, "eager").id, definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and "complete temporary plan" in result.reason


@pytest.mark.parametrize("damage", [{"move": True}, {"addr_of": True}, {"form": th.Form.VALUE}])
def test_argument_adaptation_requires_exact_borrow_form(artifacts: Artifacts, damage: dict[str, object]) -> None:
    ctx, workspace, definitions, _ = artifacts
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "eager")
    loop = fn.body[1]
    arg, = loop.iterable.args
    body = (fn.body[0], replace(loop, iterable=replace(loop.iterable, args=(replace(arg, **damage),))),
            *fn.body[2:])
    damaged = replace(fn, body=body, temp_plan=prepare_temporaries(body))
    result = lower_function(damaged, _body(artifacts, "eager").id,
                            definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered)
    expected = "readonly record" if "form" in damage else f"unsupported metadata: {next(iter(damage))}"
    assert expected in result.reason


@pytest.mark.parametrize("qualified", [False, True])
def test_imported_constructor_evidence(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
        qualified: bool) -> None:
    (tmp_path / "helper.py").write_text('''from tpy import int32, nocopy, readonly
@nocopy
class Cell:
    value: int32
    xs: list[int32]
    def __init__(self, value: int32):
        self.value = value
        self.xs = [value]
def read(cell: readonly[Cell]) -> readonly[list[int32]]:
    return cell.xs
''')
    main = tmp_path / "main.py"
    main.write_text('''from tpy import int32
from helper import Cell as Box, read as selected
import helper as h
def caller(value: int32) -> int32:
    for c in selected(Box(value)):
        return c
    return 0
'''.replace("Box(value)", "h.Cell(value)" if qualified else "Box(value)"))
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", str(main)])
    assert cli._run_cli(False) == 0
    caller = capsys.readouterr().out.split("::caller@", 1)[1].split("\nfn ", 1)[0]
    if qualified:
        assert "uncovered (named argument needs complete temporary plan" in caller
    else:
        assert "call helper::read" in caller and "call helper.Cell::__init__" in caller
        assert "<uncovered (" not in caller
