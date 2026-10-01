"""Writing calls preserve aliases and carry effects through real argument uses."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

from ..codegen_cpp.context import CodeGenContext
from .. import cli
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import VoidType
from .call_contract import MIRSummaryResult, MIRSummaryState
from .call_effects import MIRCallEffects, analyze_call_effects, resolve_call_writes
from .collect import call_definitions, dump_codegen_mir
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBranch, MIRCall, MIRCallStmt, MIRFunction,
    MIRNotCovered, MIRPlace, MIRPoint, MIRRead,
)
from .payload_lifetime import inspect_payload_lifetimes
from .presence import _analyze_presence
from .retention import analyze_retention, may_overlap
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .validate import MIRValidationError, source_definition, statement_reads, validate_function


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    other: int32
    flag: bool
    def __init__(self, value: int32):
        self.value = value
        self.other = 0
        self.flag = False
    def method(self, value: int32):
        setter(self, value)

class Runner:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        cell = Cell(0)
        setter(cell, value)
        self.value = cell.value

class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def assign(cell: Cell, value: int32) -> int32:
    cell.value = value
    return cell.value

def setter(cell: Cell, value: int32):
    cell.value = value

def set_flag(cell: Cell, flag: bool):
    cell.flag = flag

def set_other(cell: Cell, value: int32):
    cell.other = value

def read(cell: Cell) -> int32:
    return cell.value

def read_void(cell: Cell):
    old = cell.value

def forward(cell: Cell, value: int32) -> int32:
    setter(cell, value)
    return assign(cell, value)

def choose(left: Cell, right: Cell, flag: bool):
    alias = left
    if flag:
        alias = right
    setter(alias, 7)

def reseat(left: Cell, right: Cell):
    alias = left
    setter(alias, 7)
    alias = right
    set_other(alias, 8)

def both(left: Cell, right: Cell):
    setter(left, 7)
    setter(right, 8)

def repeated(cell: Cell):
    both(cell, cell)

def observe(writer: Cell, reader: Cell) -> int32:
    setter(writer, 7)
    return read(reader)

def lazy(flag: bool, cell: Cell) -> int32:
    return assign(cell, 1) if flag else assign(cell, 2)

def eager(cell: Cell) -> bool:
    return assign(cell, 1) == read(cell)

def literal_comparison(cell: Cell) -> bool:
    return assign(cell, 1) == 1

def constructor_argument(cell: Cell) -> int32:
    local = Cell(assign(cell, 1))
    return local.value

def temporary_order(flag: bool, cell: Cell) -> int32:
    return assign(cell, 1) if flag else read(Cell(2))

def standalone(cell: Cell):
    setter(cell, 4)

def owned(value: int32) -> int32:
    cell = Cell(value)
    alias = cell
    setter(cell, 7)
    return alias.value

def loops(cell: Cell, flag: bool, count: int32, values: list[int32]):
    while flag:
        setter(cell, 1)
        flag = False
    for i in range(count):
        setter(cell, i)
    for value in values:
        setter(cell, value)

def holders(cell: Cell, flag: bool, optional: Cell | None, union: Cell | Other,
            single: tuple[Cell], mixed: tuple[Cell, int32, bool]) -> int32:
    alias = cell
    scalar = (1, flag)
    before = cell.flag
    set_flag(cell, True)
    setter(cell, 9)
    if optional is not None:
        result = optional.value
    if isinstance(union, Cell):
        result = union.value
    if before:
        return single[0].value
    if scalar[1]:
        return mixed[0].value
    return alias.value

def named_reader(value: int32):
    read_void(Cell(value))
'''

Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    (_, cpp), ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    # Exercise the complete CLI inspection pipeline, including all statement consumers.
    dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions,
                     compiler.thir_reject_by_node, workspace)
    return ctx, workspace, definitions, cpp


def body(artifacts: Artifacts, name: str) -> MIRFunction:
    ctx, workspace, definitions, _ = artifacts
    if name == "Runner":
        declaration = next(c for c in ctx.thir_constructors.values() if c.record_name == name)
        result = lower_constructor(declaration, MIRBodyId("main", name), definitions=definitions,
                                   summaries=workspace.summaries)
    elif name == "method":
        declaration = next(f for f in ctx.thir_functions.values() if f.name == name)
        result = lower_function(declaration, MIRBodyId("main", name),
                                definitions=definitions, summaries=workspace.summaries)
    else:
        result = next(b for bid, b in workspace.bodies.items() if bid.declaration.split("@")[0] == name)
    assert isinstance(result, MIRFunction), result
    return result


def calls(fn: MIRFunction) -> list[tuple[MIRPoint, MIRCall]]:
    result = []
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            match stmt:
                case MIRCallStmt(call=call) | MIRAssign(value=MIRCall() as call):
                    result.append((MIRPoint(block.id, index), call))
    return result


@pytest.mark.parametrize(("name", "expected"), [
    ("setter", {(0, "value")}), ("set_flag", {(0, "flag")}),
    ("forward", {(0, "value")}), ("choose", {(0, "value"), (1, "value")}),
    ("reseat", {(0, "value"), (1, "other")}), ("repeated", {(0, "value")}),
    ("observe", {(0, "value")}), ("lazy", {(1, "value")}),
])
def test_transitive_effects_preserve_all_origins(artifacts: Artifacts, name: str,
                                              expected: set[tuple[int, str]]) -> None:
    result = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert {(w.parameter, w.path[0].name) for w in result.summary.writes} == expected


@pytest.mark.parametrize("name", ["forward", "choose", "reseat", "both", "repeated", "observe", "lazy",
                                  "standalone", "owned", "loops", "holders", "method", "Runner", "named_reader"])
def test_call_statements_are_real_uses_without_storage_ends(artifacts: Artifacts, name: str) -> None:
    fn = body(artifacts, name)
    live = analyze_liveness(fn)
    deps = analyze_dependencies(fn, live)
    assert isinstance(deps, MIRDependencies)
    effects = analyze_call_effects(fn, deps)
    assert isinstance(effects, MIRCallEffects)
    storage = analyze_storage(fn)
    retention = analyze_retention(fn, live, deps, storage)
    assert not isinstance(retention, MIRNotCovered) and not retention.conflicts
    assert not inspect_scope_lifetimes(fn).conflicts
    assert not inspect_payload_lifetimes(fn).conflicts
    presence = _analyze_presence(fn)
    for point, call in calls(fn):
        block = next(b for b in fn.blocks if b.id == point.block)
        stmt = block.statements[point.index]
        assert set(call.arguments) <= live.points[point]
        assert point not in storage.writes
        if isinstance(stmt, MIRCallStmt):
            assert source_definition(stmt) is None and statement_reads(stmt) == call.arguments
            after = MIRPoint(point.block, point.index + 1)
            assert deps.referents[point] == deps.referents[after]
            assert presence.points[point] == presence.points[after]
            assert presence.engagement[point] == presence.engagement[after]
    assert not any(isinstance(s.type, VoidType) for s in fn.slots)


def test_call_site_mapping_uses_pre_call_alias_state(artifacts: Artifacts) -> None:
    for name, expected in (
        ("choose", [{("left", "value"), ("right", "value")}]),
        ("reseat", [{("left", "value")}, {("right", "other")}]),
        ("repeated", [{("cell", "value")}]),
    ):
        fn = body(artifacts, name)
        deps = analyze_dependencies(fn, analyze_liveness(fn))
        effects = analyze_call_effects(fn, deps)
        names = {s.id: s.name for s in fn.slots}
        assert [{(names[r.place.root], r.place.projections[0].id.name) for r in refs}
                for refs in effects.writes.values()] == expected
        assert all(r.external for refs in effects.writes.values() for r in refs)


def test_distinct_parameter_roots_can_alias(artifacts: Artifacts) -> None:
    fn = body(artifacts, "observe")
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    effects = analyze_call_effects(fn, deps)
    point, call = calls(fn)[1]
    reader, = deps.referents[point][MIRPlace(call.arguments[0])]
    write, = next(refs for refs in effects.writes.values() if refs)
    assert reader.place.root != write.place.root and may_overlap(reader, write)
    assert next(s for s in fn.slots if s.id == reader.place.root).readonly


def test_scalar_snapshot_is_not_overwritten_or_folded(artifacts: Artifacts) -> None:
    fn = body(artifacts, "holders")
    before = next(s.id for s in fn.slots if s.name == "before")
    definitions = [s for b in fn.blocks for s in b.statements if source_definition(s) == before]
    assert len(definitions) == 1
    presence = _analyze_presence(fn)
    branch = next(b for b in fn.blocks if isinstance(b.terminator, MIRBranch) and any(
        isinstance(s, MIRAssign) and s.target.root == b.terminator.condition
        and isinstance(s.value, MIRRead) and s.value.source == MIRPlace(before) for s in b.statements))
    assert sum(e.source == branch.id for e in presence.edges) == 2


def test_writing_calls_stay_on_separate_lazy_arms(artifacts: Artifacts) -> None:
    fn = body(artifacts, "lazy")
    branch = next(b.terminator for b in fn.blocks if isinstance(b.terminator, MIRBranch))
    assert {point.block for point, _ in calls(fn)} == {branch.then, branch.otherwise}


def test_writer_comparison_with_literal_has_no_competing_effect(artifacts: Artifacts) -> None:
    fn = body(artifacts, "literal_comparison")
    assert len(calls(fn)) == 1
    result = artifacts[1].summaries[th.THIRFunctionIdentity("main", "literal_comparison")]
    assert result.state is MIRSummaryState.KNOWN and result.summary.writes


@pytest.mark.parametrize(("name", "reason"), [
    ("eager", "order-sensitive eager operands"), ("temporary_order", "named argument crosses unproven evaluation order"),
    ("constructor_argument", "effectful constructor argument"),
])
def test_known_writes_do_not_prove_eager_or_named_temp_order(artifacts: Artifacts, name: str, reason: str) -> None:
    result = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
    assert result.state is MIRSummaryState.OPAQUE and reason in result.reason


@pytest.mark.parametrize("state", [MIRSummaryState.PENDING, MIRSummaryState.OPAQUE])
@pytest.mark.parametrize(("name", "callee"), [("standalone", "setter"), ("literal_comparison", "assign")])
def test_unproven_void_and_writing_calls_stay_uncovered(artifacts: Artifacts,
        state: MIRSummaryState, name: str, callee: str) -> None:
    ctx, workspace, definitions, _ = artifacts
    summaries = dict(workspace.summaries)
    summaries[th.THIRFunctionIdentity("main", callee)] = (
        MIRSummaryResult(state) if state is MIRSummaryState.PENDING else MIRSummaryResult.opaque("unproved"))
    declaration = next(f for f in ctx.thir_functions.values() if f.name == name)
    result = lower_function(declaration, MIRBodyId("main", name),
                            definitions=definitions, summaries=summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "call needs finalized known summary"


def test_void_call_requires_assigned_arguments_and_void_signature(artifacts: Artifacts) -> None:
    fn = body(artifacts, "standalone")
    point, call = calls(fn)[0]
    block = next(b for b in fn.blocks if b.id == point.block)
    call_stmt = block.statements[point.index]
    broken = replace(fn, blocks=(replace(block, statements=(call_stmt,)),))
    with pytest.raises(MIRValidationError, match="read before definite assignment"):
        validate_function(broken)
    scalar = artifacts[1].summaries[th.THIRFunctionIdentity("main", "assign")].summary
    broken = replace(fn, call_summaries=(scalar,), blocks=(replace(block, statements=tuple(
        replace(s, call=replace(call, summary=scalar)) if isinstance(s, MIRCallStmt) else s
        for s in block.statements)),))
    with pytest.raises(MIRValidationError, match="effect-only call needs void result"):
        validate_function(broken)


def test_missing_origins_are_not_empty_effects(artifacts: Artifacts) -> None:
    fn = body(artifacts, "standalone")
    _, call = calls(fn)[0]
    assert resolve_call_writes(call, {}, {s.id: s for s in fn.slots}) is None
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    point, _ = calls(fn)[0]
    broken = replace(deps, referents={point: {}})
    result = analyze_call_effects(fn, broken)
    assert isinstance(result, MIRNotCovered) and result.reason == "missing call write origin"


def test_dump_reports_writes_and_cpp_preserves_reference_calls(artifacts: Artifacts) -> None:
    dump = dump_function(body(artifacts, "forward"))
    assert "writes={param0.value}" in dump
    assert "[reader" not in dump
    assert "void setter(Cell& cell, int32_t value)" in artifacts[3]


def test_imported_writer_aliases_keep_the_selected_definition(tmp_path: Path,
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "helper.py").write_text('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def setter(cell: Cell, value: int32):
    cell.value = value
''')
    (tmp_path / "facade.py").write_text("from helper import setter as exported\n")
    main = tmp_path / "main.py"
    main.write_text('''from tpy import int32
from helper import Cell
from facade import exported as selected
def forward(cell: Cell, value: int32):
    selected(cell, value)
def outer(cell: Cell, value: int32):
    forward(cell, value)
''')
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", str(main)])
    assert cli._run_cli(False) == 0
    out = capsys.readouterr().out
    assert "call helper::setter" in out and "call main::forward" in out
    assert out.count("writes={param0.value}") == 2
