"""Known-empty call facts require complete local evidence, not a signature."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from ..thir.nodes import THIRFunction
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32
from .call_contract import MIRGlobalId, MIRSummaryResult, MIRSummaryState, summary_problem
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBlockId, MIRBodyId, MIRFunction, MIRNotCovered, MIRRecordWriteMode,
    MIRStorageDuration, MIRValueKind,
)
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

def global_write() -> None:
    global value
    value = 4

def write_then_raise(cell: Cell, a: int32) -> int32:
    # The write lands before the checked multiply may throw.
    cell.value = 3
    return a * a

def range_sum(n: int32) -> int32:
    total = 0
    for i in range(n):
        total = total + i
    return total

def big_loop(n: int32, k: int) -> int32:
    # An owned local initialized once per iteration's activation.
    for i in range(n):
        t = k * 2
    return n
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


# `owned` keeps a constructed record to its end: private storage, no effect.
@pytest.mark.parametrize("name", ["read", "selected", "scalar", "literal", "empty", "owned"])
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
    ("global_write", "global access"),
])
def test_covered_mir_is_not_a_harmlessness_proof(artifacts: Artifacts, name: str, reason: str) -> None:
    functions, bodies, definitions = artifacts
    assert isinstance(bodies[name], MIRFunction)
    result = summarize_function(functions[name], bodies[name], definitions)
    assert result.state is MIRSummaryState.OPAQUE and reason in result.reason
    assert result.summary is None


@pytest.mark.parametrize(("name", "normal", "exits"), [
    ("loop", True, False), ("range_sum", False, True), ("big_loop", False, True),
])
def test_cyclic_bodies_summarize_through_the_fixpoints(artifacts: Artifacts, name: str, normal: bool,
                                                      exits: bool) -> None:
    functions, bodies, definitions = artifacts
    body = bodies[name]
    assert isinstance(body, MIRFunction) and body.exceptional_exits is exits
    result = summarize_function(functions[name], body, definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.normal_return_only is normal
    assert result.summary.writes == result.summary.global_reads == frozenset()


def test_repeated_owned_activation_is_private_storage(artifacts: Artifacts) -> None:
    body = artifacts[1]["big_loop"]
    local, = (s for s in body.slots if s.name == "t")
    assert local.value_kind is MIRValueKind.OWNED and local.storage_duration != MIRStorageDuration.BODY
    writes = [stmt.storage_write.mode for block in body.blocks for stmt in block.statements
              if isinstance(stmt, MIRAssign) and stmt.target.root == local.id]
    assert writes == [MIRRecordWriteMode.INITIALIZE_REGION]


def test_raising_summary_covers_writes_before_the_throw(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    result = summarize_function(functions["write_then_raise"], bodies["write_then_raise"], definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    summary = result.summary
    assert summary.normal_return_only is False
    assert {(w.parameter, w.path[0].name) for w in summary.writes} == {(0, "value")}
    assert summary_problem(summary) is None
    assert summary_problem(replace(summary, normal_return_only=None)) == "unsupported call summary contract"


def test_global_reads_are_summarized(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    result = summarize_function(functions["global_read"], bodies["global_read"], definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.global_reads == frozenset({MIRGlobalId("main", "value")})
    for damaged in (frozenset({("main", "value")}), frozenset({MIRGlobalId("", "value")}), ("main", "value")):
        assert summary_problem(replace(result.summary, global_reads=damaged)) is not None


def test_parameter_bindings_carry_passing_and_access(artifacts: Artifacts) -> None:
    functions, bodies, definitions = artifacts
    summary = summarize_function(functions["setter"], bodies["setter"], definitions).summary
    cell, value = summary.parameters
    assert (cell.passing, cell.readonly, cell.borrowed_record.readonly) == (ParamPassing.MUT_REF, False, False)
    assert (value.type, value.passing, value.readonly, value.borrowed_record) == (
        INT32, ParamPassing.VALUE, False, None)
    for damaged in (replace(value, passing=ParamPassing.CONST_REF), replace(value, readonly=True),
                    replace(cell, passing=ParamPassing.CONST_REF), replace(cell, type=BOOL)):
        broken = replace(summary, parameters=(damaged, value) if damaged.borrowed_record else (cell, damaged))
        assert summary_problem(broken) is not None, damaged
    # An unpublished passing fails closed.
    fn = functions["setter"]
    unpublished = replace(fn, params=(fn.params[0], replace(fn.params[1], passing=None)))
    result = summarize_function(unpublished, bodies["setter"], definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary parameter passing unpublished"


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


@pytest.mark.parametrize("damage", ["field", "origin"])
def test_write_evidence_needs_definition_and_reachable_origin(artifacts: Artifacts, damage: str) -> None:
    functions, bodies, definitions = artifacts
    body = bodies["write"]
    if damage == "field":
        def stale(stmt: MIRAssign) -> MIRAssign:
            if not stmt.target.projections:
                return stmt
            deref, field = stmt.target.projections
            return replace(stmt, target=replace(stmt.target, projections=(
                deref, replace(field, id=replace(field.id, name="missing")))))
        body = replace(body, blocks=tuple(replace(b, statements=tuple(stale(s) for s in b.statements))
                                         for b in body.blocks))
        reason = "summary write field differs from definition"
    else:
        # A disconnected but well-typed block has no dependency state to justify a write.
        extra = replace(body.blocks[0], id=MIRBlockId(body.id, max(b.id.index for b in body.blocks) + 1))
        body = replace(body, blocks=(*body.blocks, extra))
        reason = "summary missing write origin"
    result = summarize_function(functions["write"], body, definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == reason


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
