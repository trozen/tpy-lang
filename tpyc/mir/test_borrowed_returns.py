"""Returned aliases preserve parameter origins and emitted access independently."""

from dataclasses import replace
from pathlib import Path

import pytest

from ..thir.testutil import _compile, _entry
from ..thir.nodes import THIRBorrowedRecord, THIRCallableSignature, THIRFunction, THIRFunctionIdentity, THIRResolvedCallee
from ..type_def_registry import ParamPassing
from ..typesys import INT32, NominalType, RefType
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from .call_contract import MIRCallSummary, MIRParameterBinding, MIRSummaryState, result_problem, summary_problem
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, analyze_dependencies, resolve_call_returns
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBodyId, MIRCall, MIRConstruct, MIRDeref,
    MIRFunction, MIRNotCovered, MIRReturn, MIRPoint, MIRPlace, MIRRecordWrite, MIRRecordWriteMode,
)
from .retention import analyze_retention
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .summaries import summarize_function
from .validate import MIRValidationError, validate_function
from .testutil import Reference, execute
from .test_scope_inspection import scoped_loop
from .test_retention import CELL, SAVED


SOURCE = '''from tpy import int32, readonly, pure, Own

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def method(self) -> int32:
        saved = identity(self)
        saved.value = 32
        return self.value

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        local = Cell(value)
        saved = identity(local)
        saved.value = 33
        self.value = local.value

def identity(cell: Cell) -> Cell:
    return cell

def choose(flag: bool, a: Cell, b: Cell) -> Cell:
    return a if flag else b

def exits(flag: bool, a: Cell, b: Cell) -> Cell:
    if flag:
        return a
    return b

def reseat(flag: bool, a: Cell, b: Cell) -> Cell:
    saved = a
    if flag:
        saved = b
    return saved

def independent(a: Cell, b: Cell) -> Cell:
    n = b.value
    return a

def writing(a: Cell, b: Cell) -> Cell:
    b.value = 9
    return a

def observe(cell: readonly[Cell]) -> readonly[Cell]:
    return cell

def readonly_choice(flag: bool, a: Cell, b: readonly[Cell]) -> readonly[Cell]:
    if flag:
        return a
    return b

def readonly_forward(cell: readonly[Cell]) -> readonly[Cell]:
    return observe(cell)

@readonly
def decorated(cell: Cell) -> Cell:
    return cell

@pure
def pure_identity(cell: Cell) -> Cell:
    return cell

def owned(value: int32) -> Own[Cell]:
    return Cell(value)

def forward(cell: Cell) -> Cell:
    return identity(cell)

def binding(flag: bool, a: Cell, b: Cell) -> int32:
    saved = choose(flag, a, b)
    saved.value = 31
    return saved.value

def permuted(flag: bool, a: Cell, b: Cell) -> Cell:
    return choose(flag, b, a)

def second(a: Cell, b: Cell) -> Cell:
    return identity(b)

def repeated(flag: bool, a: Cell) -> Cell:
    return permuted(flag, a, a)

def overwrite(a: Cell, b: Cell) -> Cell:
    holder = a
    saved = identity(holder)
    holder = identity(b)
    saved = identity(saved)
    return saved

def readonly_binding(a: Cell) -> int32:
    saved = observe(a)
    a.value = 17
    return saved.value

def forwarded_write(a: Cell, b: Cell) -> Cell:
    saved = writing(a, b)
    saved.value = 21
    return saved

def lazy(flag: bool, a: Cell, b: Cell) -> Cell:
    return identity(a) if flag else identity(b)

def local_storage(value: int32) -> int32:
    local = Cell(value)
    saved = identity(local)
    local.value = 23
    return saved.value

def loop(flag: bool, a: Cell, b: Cell) -> int32:
    saved = identity(a)
    while flag:
        saved = identity(b)
        flag = False
    return saved.value

def range_loop(a: Cell, b: Cell) -> int32:
    saved = identity(a)
    for i in range(2):
        saved = identity(b)
    return saved.value

def temporary() -> int32:
    saved = observe(Cell(1))
    return saved.value

def recursive(a: Cell) -> Cell:
    return recursive(a)

def tuple_result(a: Cell) -> tuple[Cell]:
    return (a,)

def optional_result(a: Cell) -> Cell | None:
    return a

class Outer:
    cell: Cell
    def __init__(self, value: int32):
        self.cell = Cell(value)

def projected(outer: Outer) -> Cell:
    return outer.cell
'''


Artifacts = tuple[dict[str, THIRFunction], dict[str, MIRFunction | MIRNotCovered], MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("main", name), definitions=definitions)
              for name, fn in functions.items()}
    return functions, bodies, definitions


@pytest.mark.parametrize("name, origins, readonly", [
    ("identity", {0}, False), ("choose", {1, 2}, False), ("exits", {1, 2}, False),
    ("reseat", {1, 2}, False), ("independent", {0}, False), ("writing", {0}, False),
    ("observe", {0}, True), ("readonly_choice", {1, 2}, True), ("pure_identity", {0}, False),
])
def test_leaf_return_origins(artifacts: Artifacts, name: str, origins: set[int], readonly: bool) -> None:
    functions, bodies, definitions = artifacts
    body = bodies[name]
    assert isinstance(body, MIRFunction), body
    validate_function(body)
    result = summarize_function(functions[name], body, definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.returns == origins
    assert body.borrowed_result.readonly is readonly
    assert result.summary.callee.signature.borrowed_result == body.borrowed_result
    assert bool(result.summary.writes) is (name == "writing")
    assert "result borrowed " + ("readonly" if readonly else "mutable") in dump_function(body)
    deps = analyze_dependencies(body, analyze_liveness(body))
    assert not isinstance(deps, MIRNotCovered)
    for block in body.blocks:
        if isinstance(block.terminator, MIRReturn):
            point = MIRPoint(block.id, len(block.statements))
            assert deps.active[point][MIRPlace(block.terminator.value)]


def test_owned_result_is_not_a_borrow(artifacts: Artifacts) -> None:
    functions, bodies, _ = artifacts
    assert functions["owned"].resolved_callee.signature.borrowed_result is None
    assert isinstance(bodies["owned"], MIRNotCovered)


def test_decorated_result_preserves_emitted_access(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    assert bodies["decorated"].borrowed_result.readonly
    result = summarize_function(functions["decorated"], bodies["decorated"], definitions)
    assert result.state is MIRSummaryState.OPAQUE
    assert result.reason == "summary definition signature mismatch"


def test_borrowed_call_requires_known_callee(artifacts: Artifacts) -> None:
    _, bodies, _ = artifacts
    assert isinstance(bodies["forward"], MIRNotCovered)
    assert bodies["forward"].reason == "call needs finalized known summary"


def test_result_contract_rejects_missing_or_wrong_origins(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    summary = summarize_function(functions["identity"], bodies["identity"], definitions).summary
    for origins, message in (
        (frozenset(), "missing or unexpected return origins"),
        (frozenset({-1}), "invalid return parameter"),
        (frozenset({1}), "invalid return parameter"),
        (frozenset({True}), "invalid call summary identity or facts"),
    ):
        assert summary_problem(replace(summary, returns=origins)) == message
    readonly = summarize_function(functions["observe"], bodies["observe"], definitions).summary
    assert summary_problem(replace(summary, parameters=readonly.parameters)) == "unsupported return origin type or access"


def test_return_access_is_validated_without_thir(artifacts: Artifacts) -> None:
    _, bodies, _ = artifacts
    body = bodies["decorated"]
    assert isinstance(body, MIRFunction)
    with pytest.raises(MIRValidationError, match="^unsupported return type or access$"):
        validate_function(replace(body, borrowed_result=None))
    body = bodies["observe"]
    with pytest.raises(MIRValidationError, match="^unsupported return type or access$"):
        validate_function(replace(body, borrowed_result=replace(body.borrowed_result, readonly=False)))
    body = bodies["identity"]
    returned = next(block.terminator.value for block in body.blocks if isinstance(block.terminator, MIRReturn))
    slots = tuple(replace(slot, readonly=True) if slot.id == returned else slot for slot in body.slots)
    with pytest.raises(MIRValidationError, match="^borrowed return type or access mismatch$"):
        validate_function(replace(body, slots=slots))


def test_borrowed_return_requires_verified_layout(artifacts: Artifacts) -> None:
    body = artifacts[1]["identity"]
    with pytest.raises(MIRValidationError, match="missing borrowed return record layout"):
        validate_function(replace(body, records=()))


@pytest.mark.parametrize("generic", [False, True])
def test_result_contract_excludes_generic_and_protocol_records(generic: bool) -> None:
    typ = NominalType("Other", type_args=(INT32,) if generic else (),
                      is_protocol=not generic, _module_qname="test.Other")
    assert result_problem(RefType(typ), THIRBorrowedRecord(typ, False)) is not None


@pytest.mark.parametrize("name", ["choose", "exits", "reseat"])
@pytest.mark.parametrize("flag", [False, True])
def test_return_preserves_selected_identity(artifacts: Artifacts, name: str, flag: bool) -> None:
    _, bodies, _ = artifacts
    a, b = Reference(11), Reference(22)
    selected = b if flag and name == "reseat" or not flag and name != "reseat" else a
    assert execute(bodies[name], flag, a, b) == selected


@pytest.fixture(scope="module")
def workspace(artifacts: Artifacts) -> MIRCallWorkspace:
    functions, _, definitions = artifacts
    return analyze_call_workspace(tuple((MIRBodyId("main", n), fn) for n, fn in functions.items()), definitions)


@pytest.mark.parametrize("name, roots", [
    ("forward", {0}), ("permuted", {1, 2}), ("repeated", {1}),
    ("second", {1}),
    ("readonly_forward", {0}),
    ("overwrite", {0}), ("forwarded_write", {0}), ("lazy", {1, 2}),
])
def test_workspace_return_substitution(workspace: MIRCallWorkspace, name: str, roots: set[int]) -> None:
    result = next(r for key, r in workspace.summaries.items() if key.name == name)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.returns == roots
    if name == "forwarded_write":
        assert {w.parameter for w in result.summary.writes} == {0, 1}


@pytest.mark.parametrize("name", ["binding", "readonly_binding", "local_storage", "loop", "range_loop"])
def test_call_results_are_live_alias_holders(workspace: MIRCallWorkspace, name: str) -> None:
    body = workspace.bodies[MIRBodyId("main", name)]
    assert isinstance(body, MIRFunction), body
    dependencies = analyze_dependencies(body, analyze_liveness(body))
    assert not isinstance(dependencies, MIRNotCovered)
    calls = [(block, index, stmt) for block in body.blocks for index, stmt in enumerate(block.statements)
             if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)]
    assert calls
    for block, index, stmt in calls:
        origins = dependencies.referents[MIRPoint(block.id, index + 1)][stmt.target]
        assert origins
        assert all(r.external is (name != "local_storage") for r in origins)
        assert all(not r.place.projections for r in origins)
    saved = next(slot for slot in body.slots if slot.name == "saved")
    assert saved.readonly is (name == "readonly_binding")
    assert "returns={" in dump_function(body)


@pytest.mark.parametrize("name, reason", [
    ("temporary", "call needs borrowed record name"),
    ("recursive", "recursive or recursion-dependent call"),
    ("tuple_result", "unsupported return type"),
    ("optional_result", "unsupported return type"),
    ("projected", "unsupported borrowed expression form"),
    ("local_storage", "summary storage or value shape"),
])
def test_incomplete_result_evidence_stays_opaque(workspace: MIRCallWorkspace, name: str, reason: str) -> None:
    result = next(r for key, r in workspace.summaries.items() if key.name == name)
    assert result.state is MIRSummaryState.OPAQUE
    assert result.reason == reason


@pytest.mark.parametrize("name", ["loop", "range_loop"])
def test_borrowed_origin_joins_across_a_back_edge(workspace: MIRCallWorkspace, name: str) -> None:
    # `saved` enters the loop borrowing `a` and leaves the loop body borrowing
    # `b`: after the loop, where the entry edge and the back edge join, it may
    # borrow either.
    result = next(r for key, r in workspace.summaries.items() if key.name == name)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.writes == frozenset() and result.summary.returns == frozenset()
    body = workspace.bodies[MIRBodyId("main", name)]
    saved = next(s.id for s in body.slots if s.name == "saved")
    a, b = (MIRReferent(MIRPlace(next(s.id for s in body.slots if s.name == n)), external=True) for n in "ab")
    deps = analyze_dependencies(body, analyze_liveness(body))
    exit_block = next(block for block in body.blocks if isinstance(block.terminator, MIRReturn))
    joined = deps.referents[MIRPoint(exit_block.id, len(exit_block.statements))][MIRPlace(saved)]
    assert joined == {a, b}
    origins = {ref for refs in deps.referents.values() for leaf, values in refs.items()
               if leaf.root == saved for ref in values}
    assert origins == {a, b}


def test_imported_return_roots_are_substituted(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(SOURCE[:SOURCE.index("def binding")])
    compiler, modules = _compile('''from helper import Cell, identity as imported
def caller(flag: bool, a: Cell, b: Cell) -> Cell:
    return imported(b)
''', extra_lib_dirs=[tmp_path])
    functions = []
    constructors = []
    for module in modules:
        if module.name not in ("main", "helper"):
            continue
        _, ctx = compiler.generate_code_and_thir(module)
        functions.extend((MIRBodyId(module.name, fn.name), fn) for fn in ctx.thir_functions.values())
        constructors.extend(ctx.thir_constructors.values())
    result = analyze_call_workspace(tuple(functions), MIRDefinitions(tuple(constructors)))
    summary = next(v for k, v in result.summaries.items() if k.name == "caller")
    assert summary.state is MIRSummaryState.KNOWN, summary.reason
    assert summary.summary.returns == {2}


def test_returned_holder_participates_in_storage_analysis(workspace: MIRCallWorkspace) -> None:
    body = workspace.bodies[MIRBodyId("main", "local_storage")]
    local = next(s for s in body.slots if s.name == "local")
    value = next(s for s in body.slots if s.name == "value")
    saved = next(s for s in body.slots if s.name == "saved")
    # Replace storage after the call; a content write alone does not end its loans.
    blocks = tuple(replace(block, statements=tuple(
        MIRAssign(MIRPlace(local.id, (MIRDeref(),)), MIRConstruct((value.id,)),
                  storage_write=MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, local.id))
        if isinstance(stmt, MIRAssign) and stmt.target.projections else stmt
        for stmt in block.statements)) for block in body.blocks)
    changed = replace(body, blocks=blocks)
    live = analyze_liveness(changed)
    deps = analyze_dependencies(changed, live)
    retention = analyze_retention(changed, live, deps, analyze_storage(changed))
    assert not isinstance(retention, MIRNotCovered)
    assert any(conflict.holder == MIRPlace(saved.id) for conflict in retention.conflicts)
    original_live = analyze_liveness(body)
    original = analyze_retention(body, original_live, analyze_dependencies(body, original_live), analyze_storage(body))
    assert not isinstance(original, MIRNotCovered) and not original.conflicts
    scope = inspect_scope_lifetimes(body)
    assert not isinstance(scope.ends, MIRNotCovered)
    assert scope.conflicts == () and not scope.freshness


def test_call_result_retains_storage_across_scope_exit() -> None:
    body, observed = scoped_loop("record")
    ref = THIRBorrowedRecord(CELL, False)
    callee = THIRResolvedCallee(THIRFunctionIdentity("retention", "identity"),
                                THIRCallableSignature((RefType(CELL),), RefType(CELL), ref, (ParamPassing.MUT_REF,)))
    summary = MIRCallSummary(callee, (MIRParameterBinding(CELL, ParamPassing.MUT_REF, False, ref),),
                             frozenset({0}), frozenset(), frozenset(), frozenset({0}), frozenset(), True)
    blocks = tuple(replace(block, statements=tuple(
        replace(stmt, value=MIRCall(summary, (stmt.value.source,)))
        if isinstance(stmt, MIRAssign) and stmt.target == MIRPlace(SAVED) and isinstance(stmt.value, MIRAlias)
        else stmt for stmt in block.statements)) for block in body.blocks)
    changed = replace(body, blocks=blocks, call_summaries=(summary,))
    assert any(isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)
               for block in changed.blocks for stmt in block.statements)
    actual = inspect_scope_lifetimes(changed)
    assert not isinstance(actual.conflicts, MIRNotCovered)
    assert len(actual.conflicts) == 1 and actual.conflicts[0].holder == observed
    assert actual.conflicts == inspect_scope_lifetimes(body).conflicts


@pytest.mark.parametrize("damage", ["projected", "scalar", "readonly"])
def test_call_result_target_is_validated(workspace: MIRCallWorkspace, damage: str) -> None:
    body = workspace.bodies[MIRBodyId("main", "readonly_binding" if damage == "readonly" else "binding")]
    block, index, stmt = next((block, index, stmt) for block in body.blocks
                              for index, stmt in enumerate(block.statements)
                              if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall))
    if damage == "readonly":
        changed = replace(body, slots=tuple(replace(s, readonly=False) if s.id == stmt.target.root else s
                                            for s in body.slots))
        message = "call result type or access mismatch"
    else:
        if damage == "projected":
            a = next(s for s in body.slots if s.name == "a")
            target = MIRPlace(a.id, (MIRDeref(), body.records[0].fields[0]))
            message = "call needs whole result holder"
        else:
            target = MIRPlace(next(s.id for s in body.slots if s.type == INT32))
            message = "call result type or access mismatch"
        statements = (*block.statements[:index], replace(stmt, target=target), *block.statements[index + 1:])
        changed = replace(body, blocks=tuple(replace(b, statements=statements) if b.id == block.id else b
                                             for b in body.blocks))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(changed)


def test_missing_actual_origin_is_not_an_empty_result(workspace: MIRCallWorkspace) -> None:
    body = workspace.bodies[MIRBodyId("main", "binding")]
    call = next(stmt.value for block in body.blocks for stmt in block.statements
                if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall))
    assert resolve_call_returns(call, {}, {s.id: s for s in body.slots}) is None


def test_scalar_summary_cannot_claim_borrowed_origins(workspace: MIRCallWorkspace) -> None:
    result = next(r for key, r in workspace.summaries.items() if key.name == "readonly_binding")
    assert result.state is MIRSummaryState.KNOWN
    assert summary_problem(replace(result.summary, returns=frozenset({0}))) == "missing or unexpected return origins"


def test_method_and_constructor_callers(workspace: MIRCallWorkspace) -> None:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    method = next(fn for node, fn in ctx.thir_functions.items() if node.name == "method")
    constructor = next(fn for fn in ctx.thir_constructors.values() if fn.record_name == "Caller")
    bodies = (
        lower_function(method, MIRBodyId("main", "method"),
                       definitions=definitions, summaries=workspace.summaries),
        lower_constructor(constructor, MIRBodyId("main", "Caller"),
                          definitions=definitions, summaries=workspace.summaries),
    )
    for body in bodies:
        assert isinstance(body, MIRFunction), body
        deps = analyze_dependencies(body, analyze_liveness(body))
        assert not isinstance(deps, MIRNotCovered)
        saved = next(s for s in body.slots if s.name == "saved")
        assert any(MIRPlace(saved.id) in holders for holders in deps.active.values())
