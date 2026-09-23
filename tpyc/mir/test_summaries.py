"""Known-empty call facts require complete local evidence, not a signature."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from ..thir.nodes import THIRFunction
from ..typesys import BOOL, INT32
from .call_contract import MIRSummaryResult, MIRSummaryState, summary_problem
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered
from .summaries import summarize_function
from .validate import MIRValidationError


SOURCE = '''from tpy import int32

class Cell:
    value: int32
    other: int32
    def __init__(self, value: int32):
        self.value = value
        self.other = 0

def read(cell: Cell) -> int32:
    return cell.value

def selected(cell: Cell, flag: bool) -> int32:
    alias = cell
    if flag:
        value = alias.value
    else:
        value = 0
    return value

def scalar(value: int32) -> bool:
    return not (value == 0)

def literal() -> bool:
    return True

def write(cell: Cell) -> int32:
    cell.value = 3
    return cell.value

def setter(cell: Cell, value: int32):
    cell.value = value

def conditional(left: Cell, right: Cell, flag: bool):
    alias = left
    if flag:
        alias = right
    alias.value = 4
    if flag:
        left.other = 5

def reseated(left: Cell, right: Cell):
    alias = left
    alias.value = 4
    alias = right
    alias.other = 5

def empty():
    pass

def loop(flag: bool) -> int32:
    while flag:
        flag = False
    return 0

def owned(value: int32) -> int32:
    cell = Cell(value)
    return cell.value

value = 3
def global_read() -> int32:
    return value
'''


Artifacts = tuple[dict[str, THIRFunction], dict[str, MIRFunction | MIRNotCovered], MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("main", name),
                                   kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
              for name, fn in functions.items()}
    return functions, bodies, definitions


@pytest.mark.parametrize("name", ["read", "selected", "scalar", "literal", "empty"])
def test_leaf_evidence_is_known(artifacts: Artifacts, name: str) -> None:
    functions, bodies, definitions = artifacts
    body = bodies[name]
    assert isinstance(body, MIRFunction)
    result = summarize_function(functions[name], body, definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    summary = result.summary
    assert summary.callee == functions[name].resolved_callee
    assert summary.reads == frozenset(range(len(functions[name].params)))
    assert summary.writes == summary.invalidates == summary.retains == summary.returns == frozenset()
    assert summary.normal_return_only


@pytest.mark.parametrize(("name", "reason"), [
    ("loop", "cyclic control flow"),
    ("owned", "storage or value shape"), ("global_read", "global access"),
])
def test_covered_mir_is_not_a_harmlessness_proof(artifacts: Artifacts, name: str, reason: str) -> None:
    functions, bodies, definitions = artifacts
    assert isinstance(bodies[name], MIRFunction)
    result = summarize_function(functions[name], bodies[name], definitions)
    assert result.state is MIRSummaryState.OPAQUE and reason in result.reason
    assert result.summary is None


@pytest.mark.parametrize(("name", "expected"), [
    ("write", {(0, "value")}), ("setter", {(0, "value")}),
    ("conditional", {(0, "value"), (1, "value"), (0, "other")}),
    ("reseated", {(0, "value"), (1, "other")}),
])
def test_writes_follow_aliases_at_each_write(artifacts: Artifacts, name: str,
                                          expected: set[tuple[int, str]]) -> None:
    functions, bodies, definitions = artifacts
    result = summarize_function(functions[name], bodies[name], definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    summary = result.summary
    assert {(w.parameter, w.path[0].name) for w in summary.writes} == expected
    assert all(len(w.path) == 1 and w.path[0].type == INT32
               and w.path[0].owner == summary.parameters[w.parameter].type for w in summary.writes)
    assert summary.invalidates == summary.retains == summary.returns == frozenset()
    assert summary_problem(summary) is None


@pytest.mark.parametrize("damage", ["index", "bool_index", "empty", "nested", "owner", "readonly", "untyped"])
def test_malformed_write_contract(artifacts: Artifacts, damage: str) -> None:
    functions, bodies, definitions = artifacts
    summary = summarize_function(functions["write"], bodies["write"], definitions).summary
    write, = summary.writes
    match damage:
        case "index":
            write = replace(write, parameter=1)
        case "bool_index":
            write = replace(write, parameter=False)
        case "empty":
            write = replace(write, path=())
        case "nested":
            write = replace(write, path=write.path * 2)
        case "owner":
            write = replace(write, path=(replace(write.path[0], owner=BOOL),))
        case "readonly":
            summary = replace(summary, parameters=(replace(summary.parameters[0], readonly=True),))
        case "untyped":
            write = 0
    assert summary_problem(replace(summary, writes=frozenset({write}))) is not None


def test_missing_or_mismatched_definition_cannot_supply_empty_effects(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    fn, body = functions["read"], bodies["read"]
    for declaration in (replace(fn, resolved_callee=None), functions["scalar"]):
        assert summarize_function(declaration, body, definitions).state is MIRSummaryState.OPAQUE
    assert summarize_function(fn, body, MIRDefinitions()).state is MIRSummaryState.OPAQUE


@pytest.mark.parametrize("damage", ["signature", "binding"])
def test_definition_parameter_mismatch_is_opaque(artifacts: Artifacts, damage: str) -> None:
    functions, bodies, definitions = artifacts
    fn = functions["read"]
    if damage == "signature":
        fn = replace(fn, resolved_callee=replace(fn.resolved_callee, signature=replace(
            fn.resolved_callee.signature, param_types=(BOOL,))))
        reason = "summary definition signature mismatch"
    else:
        fn = replace(fn, params=(replace(fn.params[0], name="other"),))
        reason = "summary parameter binding mismatch"
    result = summarize_function(fn, bodies["read"], definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == reason


def test_malformed_mir_is_not_swallowed_as_opaque(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    body = replace(bodies["read"], entry=MIRBodyId("bad", "entry"))
    with pytest.raises(MIRValidationError, match="missing entry block"):
        summarize_function(functions["read"], body, definitions)


def test_pending_opaque_and_known_empty_are_distinct(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    pending = MIRSummaryResult(MIRSummaryState.PENDING)
    opaque = MIRSummaryResult.opaque("missing body")
    known = summarize_function(functions["literal"], bodies["literal"], definitions)
    assert len({pending.state, opaque.state, known.state}) == 3
    with pytest.raises(ValueError, match="inconsistent"):
        MIRSummaryResult(MIRSummaryState.KNOWN)
    with pytest.raises(ValueError, match="inconsistent"):
        replace(known, state=MIRSummaryState.PENDING)
