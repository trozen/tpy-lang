"""Calls consume finalized evidence and remain real argument-use events."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from .. import cli
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL
from .call_contract import (
    MIR_RESULT, MIR_STATIC, MIRGlobalId, MIRLoanTransfer, MIRSummaryResult, MIRSummaryState, summary_problem,
)
from .call_effects import analyze_call_effects
from .collect import call_definitions, dump_codegen_mir
from .definitions import MIRDefinitions
from .dependencies import MIRUnseededLoan, apply_transfers
from .dump import dump_function
from .shapes import MIRUnionPayload
from .nodes import (
    MIRAssign, MIRBodyId, MIRBorrow, MIRBranch, MIRCall, MIRConstant,
    MIRCopy, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRPresentCall, MIRRead, MIRSlotKind, present_effects,
    statement_call,
)
from .lower import lower_function
from .presence import present_call
from .value import MIRValidationError
from .scope_lifetime import inspect_scope_lifetimes
from .region_flow import outgoing_edges
from .testutil import no_layouts, compiled_definitions, reslot, prepared, workspace_functions
from .prepare import validate_function


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def via_helper(self) -> int32:
        return read(self)

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        self.value = identity(value)

def identity(value: int32) -> int32:
    return value

def forward(cell: Cell) -> int32:
    return read(cell)

def read(cell: Cell) -> int32:
    return cell.value

def choice(flag: bool, cell: Cell) -> int32:
    return forward(cell) if flag else 0

def observe(value: int32) -> int32:
    cell = Cell(value)
    before = read(cell)
    cell.value = 9
    after = read(cell)
    return after

def yes() -> bool:
    return True

def noarg_forward() -> bool:
    return yes()

def unknown_result(flag: bool) -> int32:
    flag = yes()
    if flag:
        return 1
    return 2

def direct_condition() -> int32:
    if yes():
        return 1
    return 2

def loop(flag: bool, cell: Cell) -> int32:
    while flag:
        result = read(cell)
        flag = False
    return read(cell)

def write(cell: Cell) -> int32:
    cell.value = 8
    return cell.value

def effect(cell: Cell) -> int32:
    return write(cell)

def recurse_a() -> bool:
    return recurse_b()

def recurse_b() -> bool:
    return recurse_a()

def recurse_user() -> bool:
    return recurse_a()

def temporary(value: int32) -> int32:
    return read(Cell(value))

def retained(flag: bool) -> int32:
    saved = Cell(0)
    if flag:
        local = Cell(1)
        saved = local
    return read(saved)

global_value = 3
def global_argument() -> int32:
    return identity(global_value)

def global_reader() -> int32:
    return global_value

def reads_through_callee() -> int32:
    return global_reader()

def global_writer() -> int32:
    global global_value
    global_value = 4
    return 0

def calls_global_writer() -> int32:
    return global_writer()

def maybe_read(cell: Cell | None) -> int32:
    if cell is not None:
        return read(cell)
    return 0

def optional_record_argument(cell: Cell, flag: bool) -> int32:
    return maybe_read(cell if flag else None)

def maybe_write(cell: Cell | None) -> None:
    if cell is not None:
        cell.value = 7

def absent_write() -> int32:
    maybe_write(None)
    return 0

def present_write(cell: Cell) -> int32:
    maybe_write(cell)
    return cell.value

def bound_absent() -> int32:
    held: Cell | None = None
    maybe_write(held)
    return 0

def one_branch_absent(cell: Cell | None) -> int32:
    if cell is None:
        maybe_write(cell)
    maybe_write(cell)
    return 0

def noisy(cell: Cell) -> int32:
    before = read(cell)
    print(before)
    return read(cell)

def grow(xs: list[int32]) -> None:
    xs.append(1)

def infeasible_absent() -> int32:
    flag: int32 | None = None
    if flag is not None:
        maybe_write(None)
    return 0

def infeasible_call(xs: list[int32]) -> int32:
    flag: int32 | None = None
    if flag is not None:
        grow(xs)
    return 0

class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def union_write(u: Cell | Other | None) -> None:
    if isinstance(u, Cell):
        u.value = 7

def union_other(other: Other) -> int32:
    union_write(other)
    return other.value

def union_cell(cell: Cell) -> int32:
    union_write(cell)
    return cell.value

def union_passed(u: Cell | Other | None) -> int32:
    union_write(u)
    return 0
'''

Artifacts = tuple[dict[str, th.THIRFunction], MIRCallWorkspace, MIRDefinitions, str, str]


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
        out = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions,
                               compiler.thir_reject_by_node, workspace)
        return {node.name: fn for node, fn in ctx.thir_functions.items()}, workspace, definitions, cpp, out


def body_named(workspace: MIRCallWorkspace, name: str) -> MIRFunction:
    result = next(body for bid, body in workspace_functions(workspace).items() if bid.declaration.split("@")[0] == name)
    assert isinstance(result, MIRFunction), result
    return result


def calls(body: MIRFunction) -> list[tuple[MIRPoint, MIRAssign]]:
    return [(MIRPoint(block.id, index), stmt) for block in body.blocks
            for index, stmt in enumerate(block.statements)
            if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)]


def test_forward_definitions_and_transitive_calls_are_known(artifacts: Artifacts) -> None:
    _, workspace, _, _, dump = artifacts
    for name in ("read", "forward", "choice", "yes", "noarg_forward", "unknown_result", "write", "effect"):
        result = workspace.summaries[th.THIRFunctionIdentity("main", name)]
        assert result.state is MIRSummaryState.KNOWN, (name, result.reason)
    assert "call main::read" in dump and "call main::forward" in dump
    with pytest.raises(TypeError):
        workspace.summaries[th.THIRFunctionIdentity("main", "yes")] = None


def test_call_arguments_are_live_and_result_has_no_borrow(artifacts: Artifacts) -> None:
    _, workspace, _, cpp, _ = artifacts
    body = body_named(workspace, "observe")
    points = calls(body)
    assert len(points) == 2
    assert points[0][1].value.arguments == points[1][1].value.arguments
    assert not any(isinstance(stmt.value, MIRCopy) for block in body.blocks
                   for stmt in block.statements if isinstance(stmt, MIRAssign))
    live = prepared(body).liveness
    dependencies = prepared(body).dependencies
    assert not isinstance(dependencies, MIRNotCovered)
    for point, stmt in points:
        assert set(stmt.value.arguments) <= live.points[point]
        assert not any(holder.root == stmt.target.root
                       for holder in dependencies.referents[MIRPoint(point.block, point.index + 1)])
    # @nocopy plus a reference parameter pins the boundary that read-only output cannot.
    assert "int32_t read(const Cell& cell)" in cpp
    # `cell` is storage `observe` keeps to its end: its field write is the body's own.
    result = workspace.summaries[th.THIRFunctionIdentity("main", "observe")]
    assert result.state is MIRSummaryState.KNOWN and not result.summary.writes and not result.summary.returns


@pytest.mark.parametrize("name", ["unknown_result", "direct_condition"])
def test_constant_returning_call_still_has_unknown_result(artifacts: Artifacts, name: str) -> None:
    body = body_named(artifacts[1], name)
    assert len(calls(body)) == 1
    presence = prepared(body).presence
    assert not presence.issues
    branch = next(block for block in body.blocks if isinstance(block.terminator, MIRBranch))
    edges = tuple(edge for edge, _ in outgoing_edges(branch.id, branch.terminator))
    assert len(edges) == 2 and all(edge in presence.edges for edge in edges)
    # A constant replacement must prune an edge, so this assertion detects folding.
    folded = replace(body, blocks=tuple(replace(block, statements=tuple(
        replace(stmt, value=MIRConstant(True)) if isinstance(stmt, MIRAssign)
        and isinstance(stmt.value, MIRCall) else stmt for stmt in block.statements))
        for block in body.blocks))
    assert sum(edge in prepared(folded).presence.edges for edge in edges) == 1


def test_conditional_call_is_only_on_selected_arm(artifacts: Artifacts) -> None:
    body = body_named(artifacts[1], "choice")
    point, _ = calls(body)[0]
    branch = next(block.terminator for block in body.blocks if isinstance(block.terminator, MIRBranch))
    assert point.block == branch.then and point.block != branch.otherwise


def test_covered_caller_need_not_have_usable_summary(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    # An output effect keeps `noisy` opaque although its body and its calls lower.
    assert len(calls(body_named(workspace, "noisy"))) == 2
    result = workspace.summaries[th.THIRFunctionIdentity("main", "noisy")]
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary output effect"
    # A record temporary lent to `read` is the caller's private storage, not an unproven effect.
    result = workspace.summaries[th.THIRFunctionIdentity("main", "temporary")]
    assert result.state is MIRSummaryState.KNOWN and not result.summary.writes and not result.summary.returns
    # A loop is no obstacle: the dependency and liveness fixpoints summarize it.
    assert len(calls(body_named(workspace, "loop"))) == 2
    assert workspace.summaries[th.THIRFunctionIdentity("main", "loop")].state is MIRSummaryState.KNOWN


@pytest.mark.parametrize(("name", "reason"), [
    ("recurse_a", "recursive or recursion-dependent call"),
    ("recurse_b", "recursive or recursion-dependent call"),
    ("recurse_user", "recursive or recursion-dependent call"),
])
def test_unproven_calls_never_acquire_empty_effects(artifacts: Artifacts, name: str, reason: str) -> None:
    result = artifacts[1].summaries[th.THIRFunctionIdentity("main", name)]
    assert result.state is MIRSummaryState.OPAQUE and result.summary is None
    assert result.reason == reason


@pytest.mark.parametrize("bad_field", [False, True])
def test_writer_summary_requires_layout_and_mutable_actual(artifacts: Artifacts, bad_field: bool) -> None:
    workspace = artifacts[1]
    summary = workspace.summaries[th.THIRFunctionIdentity("main", "write")].summary
    assert summary is not None and summary.writes
    if bad_field:
        write, = summary.writes
        summary = replace(summary, writes=frozenset({replace(
            write, path=(replace(write.path[0], name="missing"),))}))
    body = body_named(workspace, "forward")
    layout = artifacts[2].get(body, summary.parameters[0].type).layout
    body = replace(body, records=(layout,),
                   call_summaries=(summary,), blocks=tuple(replace(block, statements=tuple(
        replace(stmt, value=replace(stmt.value, summary=summary))
        if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall) else stmt
        for stmt in block.statements)) for block in body.blocks))
    # A field missing from the layout fails the walk of the summary path itself.
    reason = "field missing from declared fields" if bad_field else "call record argument mismatch"
    with pytest.raises(MIRValidationError, match=reason):
        validate_function(body)


@pytest.mark.parametrize(("damage", "reason"), [
    ("missing", "call summary does not belong"), ("foreign", "call summary does not belong"),
    ("effects", "invalid call write path"), ("arity", "call arity mismatch"),
    ("duplicate", "duplicate call summary identity"), ("result", "call result type or target mismatch"),
    ("storage", "call record argument mismatch"),
])
def test_standalone_validation_requires_the_published_contract(artifacts: Artifacts, damage: str, reason: str) -> None:
    body = body_named(artifacts[1], "observe" if damage == "storage" else "forward")
    point, stmt = calls(body)[0]
    call = stmt.value
    if damage == "missing":
        body = replace(body, call_summaries=())
    elif damage == "duplicate":
        body = replace(body, call_summaries=body.call_summaries * 2)
    else:
        if damage == "foreign":
            call = replace(call, summary=replace(call.summary))
        elif damage == "effects":
            summary = replace(call.summary, writes=frozenset({0}))
            call = replace(call, summary=summary)
            body = replace(body, call_summaries=(summary,))
        elif damage == "arity":
            call = replace(call, arguments=())
        elif damage == "result":
            body = replace(body, slots=tuple(reslot(s, type=BOOL) if s.id == stmt.target.root else s
                                             for s in body.slots))
        else:
            storage = next(s.id for s in body.slots if s.shape.is_storage)
            call = replace(call, arguments=(storage,))
        body = replace(body, blocks=tuple(
            replace(block, statements=tuple(replace(s, value=call) if i == point.index else s
                                             for i, s in enumerate(block.statements)))
            if block.id == point.block else block for block in body.blocks))
    for analyze in (validate_function, prepared, dump_function):
        with pytest.raises(MIRValidationError, match=reason):
            analyze(body)


@pytest.mark.parametrize(("damage", "reason"), [
    ("stale", "call summary signature or contract mismatch"),
    ("effects", "call summary signature or contract mismatch"),
    ("arity", "call signature mismatch"), ("result", "call signature mismatch"),
    ("pending", "call needs finalized known summary"),
    ("opaque", "call needs finalized known summary"),
    ("unpublished", "signature passings unpublished"),
    ("passings", "signature passings mismatch"),
])
def test_call_coverage_checks_selected_signature_and_contract(artifacts: Artifacts, damage: str, reason: str) -> None:
    functions, workspace, definitions, _, _ = artifacts
    fn = functions["forward"]
    stmt = fn.body[0]
    call = stmt.value
    entries = dict(workspace.summaries)
    entry = entries[call.resolved_callee.identity]
    if damage in ("pending", "opaque"):
        entries[call.resolved_callee.identity] = (MIRSummaryResult(MIRSummaryState.PENDING)
                                                if damage == "pending" else MIRSummaryResult.opaque("unproven"))
    elif damage in ("stale", "effects"):
        summary = entry.summary
        summary = (replace(summary, callee=replace(summary.callee, signature=replace(
            summary.callee.signature, return_type=BOOL))) if damage == "stale" else
            replace(summary, writes=frozenset({0})))
        entries[call.resolved_callee.identity] = MIRSummaryResult(MIRSummaryState.KNOWN, summary)
    elif damage in ("unpublished", "passings"):
        # The call and the summary agree on a signature whose passings are not the bindings'.
        assert call.resolved_callee.signature.passings == (ParamPassing.CONST_REF,)
        callee = replace(call.resolved_callee, signature=replace(
            call.resolved_callee.signature, passings=None if damage == "unpublished" else (ParamPassing.MUT_REF,)))
        summary = replace(entry.summary, callee=callee)
        assert summary_problem(summary, no_layouts()) == reason
        entries[callee.identity] = MIRSummaryResult(MIRSummaryState.KNOWN, summary)
        fn = replace(fn, body=(replace(stmt, value=replace(call, resolved_callee=callee)),))
    else:
        call = replace(call, **({"args": ()} if damage == "arity" else {"result_type": BOOL}))
        fn = replace(fn, body=(replace(stmt, value=call),))
    result = lower_function(fn, MIRBodyId("bad", damage),
                            definitions=definitions, summaries=entries)
    assert isinstance(result, MIRNotCovered) and result.reason == reason


def test_scalar_global_argument_is_read_before_the_call(artifacts: Artifacts) -> None:
    # Sound because a KNOWN callee writes no global: the read value is the one the call sees.
    body = body_named(artifacts[1], "global_argument")
    (point, stmt), = calls(body)
    argument, = stmt.value.arguments
    slots = {s.id: s for s in body.slots}
    assert slots[argument].kind is MIRSlotKind.TEMPORARY
    read, = (s for b in body.blocks for s in b.statements if isinstance(s, MIRAssign) and s.target.root == argument)
    assert isinstance(read.value, MIRRead) and slots[read.value.source.root].kind is MIRSlotKind.GLOBAL
    result = artifacts[1].summaries[th.THIRFunctionIdentity("main", "global_argument")]
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.global_reads == frozenset({MIRGlobalId("main", "global_value")})
    # A global slot handed straight to the call skips the read.
    damaged = replace(body, blocks=tuple(replace(block, statements=tuple(
        replace(s, value=replace(s.value, arguments=(read.value.source.root,))) if s is stmt else s
        for s in block.statements)) for block in body.blocks))
    with pytest.raises(MIRValidationError, match="global value needs explicit read"):
        validate_function(damaged)


@pytest.mark.parametrize(("name", "reason"), [
    ("optional_record_argument", "unsupported optional argument"),
])
def test_argument_adaptations_remain_explicitly_uncovered(artifacts: Artifacts, name: str, reason: str) -> None:
    result = next(body for bid, body in workspace_functions(artifacts[1]).items() if bid.declaration.split("@")[0] == name)
    assert isinstance(result, MIRNotCovered) and result.reason == reason


def _call_statement(body: MIRFunction) -> tuple[int, int, MIRCall]:
    return next((b, i, call) for b, block in enumerate(body.blocks)
                for i, stmt in enumerate(block.statements) if (call := statement_call(stmt)) is not None)


def _present_call(body: MIRFunction) -> MIRPresentCall:
    """The first call of `body` as presence sees it."""
    b, i, call = _call_statement(body)
    called = prepared(body).presence.calls[MIRPoint(body.blocks[b].id, i)]
    assert called.call is call
    return called


def test_a_none_argument_is_absent_and_its_writes_reach_nothing(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    absent = body_named(workspace, "absent_write")
    called = _present_call(absent)
    assert called.call.summary.writes and not called.effects.writes and called.writes == ()
    assert not isinstance(analyze_call_effects(prepared(absent)),
                          MIRNotCovered)
    # A present argument is never absent: the call keeps its whole summary.
    called = _present_call(body_named(workspace, "present_write"))
    assert called.effects == called.call.summary and len(called.writes) == 1


def test_absence_follows_what_presence_proves_before_the_call(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    # A local bound to None is absent at the call, beyond a None literal.
    called = _present_call(body_named(workspace, "bound_absent"))
    assert called.call.summary.writes and not called.effects.writes and called.writes == ()
    # None on one branch only: absent there, and after the join it may be present.
    body = body_named(workspace, "one_branch_absent")
    inside, after = (called for _, called in sorted(prepared(body).presence.calls.items(),
                                                     key=lambda item: item[1].call.site.line))
    assert not inside.effects.writes and inside.writes == ()
    assert after.effects == after.call.summary and len(after.writes) == 1


def test_a_call_presence_never_reaches_keeps_its_own_block_facts(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    for name in ("infeasible_absent", "infeasible_call"):
        body = body_named(workspace, name)
        b, i, _ = _call_statement(body)
        # `flag` is None, so the branch holding the call is infeasible.
        assert MIRPoint(body.blocks[b].id, i) not in prepared(body).presence.points
    # A None argument is absent by the block's own statements: its writes are filtered.
    called = _present_call(body_named(workspace, "infeasible_absent"))
    assert called.call.summary.writes and not called.effects.writes and called.writes == ()
    # From an unknown entry nothing is proven absent: the call keeps its whole summary.
    called = _present_call(body_named(workspace, "infeasible_call"))
    assert called.effects == called.call.summary and len(called.writes) == 1


def test_a_union_argument_presence_excludes_drops_the_writes_through_that_alternative(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    # The callee writes through its `Cell` alternative only.
    write, = workspace.summaries[th.THIRFunctionIdentity("main", "union_write")].summary.writes
    assert isinstance(write.path[0], MIRUnionPayload)
    # An `Other` argument excludes the `Cell` alternative: the write reaches nothing.
    called = _present_call(body_named(workspace, "union_other"))
    assert called.call.summary.writes and not called.effects.writes and called.writes == ()
    # A `Cell` argument keeps it; a union parameter passed on has no presence fact and keeps it too.
    for name in ("union_cell", "union_passed"):
        called = _present_call(body_named(workspace, name))
        assert called.effects == called.call.summary and len(called.writes) == 1, name


def test_an_empty_or_missing_presence_fact_never_drops_an_effect(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    body = body_named(workspace, "union_cell")
    slots = {s.id: s for s in body.slots}
    _, _, call = _call_statement(body)
    assert present_call(call, frozenset(), slots).effects == call.summary
    assert present_call(call, frozenset({(MIRPlace(call.arguments[0]), frozenset())}), slots).effects == call.summary
    cell = next(iter(call.summary.writes)).path[0].alternative
    others = frozenset(range(len(slots[call.arguments[0]].shape.members))) - {cell}
    excluded = present_call(call, frozenset({(MIRPlace(call.arguments[0]), others)}), slots)
    assert not excluded.effects.writes and excluded.writes == ()


def test_an_absent_argument_holds_and_lends_no_transferred_loan(artifacts: Artifacts) -> None:
    _, workspace, _, _, _ = artifacts
    body = body_named(workspace, "absent_write")
    slots = {s.id: s for s in body.slots}
    records = body.record_layouts
    _, _, call = _call_statement(body)
    value = th.THIRFieldIdentity("main", "value", call.summary.parameters[0].type)
    into_absent = MIRLoanTransfer(0, (MIRUnionPayload(1), value), MIR_STATIC)
    from_absent = MIRLoanTransfer(MIR_RESULT, (value,), 0, (MIRUnionPayload(1), value))
    forged = replace(call, summary=replace(call.summary, transfers=frozenset({into_absent, from_absent})))
    absent = MIRPresentCall(forged, present_effects(forged.summary, {0: frozenset({1})}), ())
    assert absent.effects.transfers == frozenset()
    state: dict = {}
    apply_transfers(absent, state, slots, records)
    assert state == {}
    # Present, the same transfer is applied: the empty Optional holds no object to store into.
    with pytest.raises(MIRUnseededLoan, match="call transfer into no object"):
        apply_transfers(MIRPresentCall(forged, forged.summary, ()), {}, slots, records)


def test_global_reads_union_through_consumed_summaries(artifacts: Artifacts) -> None:
    _, workspace, _, _, dump = artifacts
    for name in ("global_reader", "reads_through_callee"):
        result = workspace.summaries[th.THIRFunctionIdentity("main", name)]
        assert result.state is MIRSummaryState.KNOWN, result.reason
        assert result.summary.global_reads == frozenset({MIRGlobalId("main", "global_value")})
    caller = dump.split("::reads_through_callee@", 1)[1].split("\nfn ", 1)[0]
    assert "global-reads={main::global_value}" in caller
    # A global write keeps the writer opaque, and so every caller of it.
    writer = workspace.summaries[th.THIRFunctionIdentity("main", "global_writer")]
    assert writer.state is MIRSummaryState.OPAQUE and writer.reason == "summary global access"
    assert isinstance(body_named(workspace, "global_writer"), MIRFunction)
    caller = next(body for bid, body in workspace_functions(workspace).items() if bid.declaration.startswith("calls_global_writer"))
    assert isinstance(caller, MIRNotCovered) and caller.reason == "call needs finalized known summary"


def test_cli_workspace_resolves_import_aliases(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "helper.py").write_text(
        "from tpy import int32\ndef value(n: int32) -> int32:\n    return n\n")
    (tmp_path / "facade.py").write_text("from helper import value as exported\n")
    main = tmp_path / "main.py"
    main.write_text('''from tpy import int32
from facade import exported as selected
import helper as h
def caller(n: int32) -> int32:
    result = selected(n)
    return h.value(result)
''')
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", str(main)])
    assert cli._run_cli(False) == 0
    out = capsys.readouterr().out
    assert out.count("call helper::value") == 2
    caller = out.split("::caller@", 1)[1].split("\nfn ", 1)[0]
    assert "MIR not covered" not in caller


def test_duplicate_or_missing_definition_does_not_resolve(artifacts: Artifacts) -> None:
    functions, original, definitions, _, _ = artifacts
    read = functions["read"]
    forward = functions["forward"]
    rid = body_named(original, "read").id
    fid = body_named(original, "forward").id
    for inputs in (((fid, forward),), ((rid, read), (rid, read), (fid, forward))):
        workspace = analyze_call_workspace(inputs, definitions)
        entry = workspace.summaries[forward.resolved_callee.identity]
        assert entry.state is MIRSummaryState.OPAQUE and entry.reason == "call needs finalized known summary"
        if len(inputs) > 1:
            assert workspace.summaries[read.resolved_callee.identity].reason == "duplicate definition identity"


def test_call_keeps_expired_backing_visible_to_scope_inspection(artifacts: Artifacts) -> None:
    body = body_named(artifacts[1], "retained")
    inspection = inspect_scope_lifetimes(prepared(body))
    assert not isinstance(inspection.conflicts, MIRNotCovered) and len(inspection.conflicts) == 1
    conflict = inspection.conflicts[0]
    assert next(s for s in body.slots if s.id == conflict.holder.root).name == "saved"


def test_call_does_not_make_an_uninitialized_argument_available(artifacts: Artifacts) -> None:
    body = body_named(artifacts[1], "observe")
    arg = calls(body)[0][1].value.arguments[0]
    broken = replace(body, blocks=tuple(replace(block, statements=tuple(
        stmt for stmt in block.statements
        if not (stmt.target.root == arg and isinstance(stmt.value, MIRBorrow)))) for block in body.blocks))
    with pytest.raises(MIRValidationError, match="before definite assignment"):
        validate_function(broken)


def test_ordinary_method_and_constructor_tail_share_call_consumer(artifacts: Artifacts) -> None:
    out = artifacts[-1]
    method = out.split("::Cell.via_helper@", 1)[1].split("\nfn ", 1)[0]
    ctor = out.split("::Caller.__init__@", 1)[1].split("\nfn ", 1)[0]
    assert "call main::read" in method and "MIR not covered" not in method
    assert "call main::identity" in ctor and "MIR not covered" not in ctor
