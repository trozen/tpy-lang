"""Returned aliases preserve parameter origins and emitted access independently."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from ..thir.nodes import THIRBorrowedRecord, THIRFunction
from ..typesys import INT32, NominalType, RefType
from .call_contract import MIRSummaryState, result_problem, summary_problem
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRReturn, MIRPoint, MIRPlace
from .summaries import summarize_function
from .validate import MIRValidationError, validate_function
from .testutil import Reference, execute


SOURCE = '''from tpy import int32, readonly, pure, Own

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

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
'''


Artifacts = tuple[dict[str, THIRFunction], dict[str, MIRFunction | MIRNotCovered], MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("main", name), definitions=definitions,
                                   kind=MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    return functions, bodies, definitions


@pytest.mark.parametrize("name, origins, readonly", [
    ("identity", {0}, False), ("choose", {1, 2}, False), ("exits", {1, 2}, False),
    ("reseat", {1, 2}, False), ("independent", {0}, False), ("writing", {0}, False),
    ("observe", {0}, True), ("pure_identity", {0}, False),
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


def test_borrowed_call_consumption_remains_closed(artifacts: Artifacts) -> None:
    _, bodies, _ = artifacts
    assert isinstance(bodies["forward"], MIRNotCovered)


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
