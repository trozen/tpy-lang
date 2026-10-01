"""Constant edges disappear without losing guard writes or retaining dead storage."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32
from .definitions import MIRDefinitions
from .lower import lower_function
from .nodes import MIRBodyId, MIRBranch, MIRFunction, MIRNotCovered, MIRReturn, MIRSlotKind
from .scope_lifetime import inspect_scope_lifetimes
from .test_lower import lower
from .testutil import OptionalValue, Reference, UnionValue, execute
from .validate import MIRDefiniteAssignmentError, validate_function


def function(body: tuple[th.THIRStmt, ...], *params: th.THIRParam) -> th.THIRFunction:
    return th.THIRFunction("constant", params, INT32, body, th.THIRFunctionLayout())


def literal(value: bool | int) -> th.THIRLiteral:
    return th.THIRLiteral(BOOL if type(value) is bool else INT32, value)


@pytest.mark.parametrize("truth", [False, True])
@pytest.mark.parametrize("negations", [0, 1, 2])
def test_literal_and_not_edges(truth: bool, negations: int) -> None:
    condition: th.THIRExpr = literal(truth)
    for _ in range(negations):
        condition = th.THIRUnaryNot(BOOL, condition)
    fn = lower(function((th.THIRIf(condition, (th.THIRReturn(literal(1)),),
                                  (th.THIRReturn(literal(2)),)),)))
    assert not any(isinstance(b.terminator, MIRBranch) for b in fn.blocks)
    assert execute(fn) == (1 if truth != bool(negations % 2) else 2)


@pytest.mark.parametrize("truth", [False, True])
def test_loop_break_bypasses_else_and_false_loop_takes_else(truth: bool) -> None:
    value = th.THIRName(INT32, "value")
    fn = lower(function((
        th.THIRVarDecl("value", INT32),
        th.THIRWhile(literal(truth), (th.THIRAssign(value, literal(3)), th.THIRBreak()),
                     (th.THIRAssign(value, literal(4)),)),
        th.THIRReturn(value),
    )))
    assert execute(fn) == (3 if truth else 4)
    assert not any(isinstance(b.terminator, MIRBranch) for b in fn.blocks)
    # The validator still rejects an actually missing initialization on the chosen path.
    slot = next(s.id for s in fn.slots if s.name == "value")
    damaged = replace(fn, blocks=tuple(replace(b, statements=tuple(
        stmt for stmt in b.statements if stmt.target.root != slot)) for b in fn.blocks))
    with pytest.raises(MIRDefiniteAssignmentError, match="read before definite assignment"):
        validate_function(damaged)


def test_nonterminating_loop_discards_synthetic_exit_and_following_declarations() -> None:
    fn = lower(function((
        th.THIRWhile(literal(True), (th.THIRContinue(),)),
        th.THIRVarDecl("dead", INT32, literal(3)),
        th.THIRIf(literal(True), (th.THIRVarDecl("nested_dead", INT32, literal(4)),)),
    ), th.THIRParam("unused", INT32, passing=ParamPassing.VALUE)))
    assert not any(isinstance(b.terminator, (MIRBranch, MIRReturn)) for b in fn.blocks)
    assert {s.name for s in fn.slots if s.kind is MIRSlotKind.LOCAL} == set()
    assert [s.name for s in fn.slots if s.kind is MIRSlotKind.PARAMETER] == ["unused"]
    assert len(fn.regions) == 2
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("op", ["&&", "||", "select"])
@pytest.mark.parametrize("truth", [False, True])
def test_lazy_operands_keep_only_reached_writes(op: str, truth: bool) -> None:
    flag = th.THIRName(BOOL, "flag")
    write = th.THIRWalrus(BOOL, "flag", "flag", literal(True))
    expr = (th.THIRIfExpr(BOOL, literal(truth), write, literal(False)) if op == "select" else
            th.THIRBinOp(BOOL, literal(truth), op, write, None))
    fn = lower(function((th.THIRVarDecl("flag", BOOL, literal(False)), th.THIRExprStmt(expr),
                         th.THIRReturn(th.THIRIfExpr(INT32, flag, literal(1), literal(0))))))
    assert execute(fn) == int(not truth if op == "||" else truth)
    # Reading a mutable local stays dynamic even when its initializer was a literal.
    assert sum(isinstance(b.terminator, MIRBranch) for b in fn.blocks) == 1


def test_walrus_guard_write_survives_folding() -> None:
    flag = th.THIRName(BOOL, "flag")
    fn = lower(function((
        th.THIRVarDecl("flag", BOOL, literal(False)),
        th.THIRIf(th.THIRWalrus(BOOL, "flag", "flag", literal(True)), (),
                  (th.THIRAssign(flag, literal(False)),)),
        th.THIRReturn(th.THIRIfExpr(INT32, flag, literal(1), literal(0))),
    )))
    assert execute(fn) == 1
    assert sum(isinstance(b.terminator, MIRBranch) for b in fn.blocks) == 1


def test_select_destination_and_comparison_are_not_constant_facts() -> None:
    flag = th.THIRName(BOOL, "flag")
    condition = th.THIRIfExpr(BOOL, flag, literal(True), literal(False))
    fn = lower(function((th.THIRIf(condition, (th.THIRReturn(literal(1)),),
                                  (th.THIRReturn(literal(2)),)),), th.THIRParam("flag", BOOL, passing=ParamPassing.VALUE)))
    assert sum(isinstance(b.terminator, MIRBranch) for b in fn.blocks) == 2
    assert execute(fn, True) == 1
    assert execute(fn, False) == 2
    comparison = th.THIRBinOp(BOOL, literal(1), "==", literal(1), None)
    fn = lower(function((th.THIRIf(comparison, (th.THIRReturn(literal(1)),),
                                  (th.THIRReturn(literal(2)),)),)))
    assert any(isinstance(b.terminator, MIRBranch) for b in fn.blocks)


def test_parameter_dependent_assignment_still_needs_both_paths() -> None:
    value = th.THIRName(INT32, "value")
    fn = function((th.THIRVarDecl("value", INT32),
                   th.THIRIf(th.THIRName(BOOL, "flag"), (th.THIRAssign(value, literal(1)),)),
                   th.THIRReturn(value)), th.THIRParam("flag", BOOL, passing=ParamPassing.VALUE))
    result = lower_function(fn, MIRBodyId("test", "dynamic"))
    assert isinstance(result, MIRNotCovered) and "definite assignment" in result.reason


SOURCE = '''from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def storage(flag: bool, source: readonly[Cell], optional: int32 | None, union: int32 | bool) -> int32:
    if flag:
        dead = Cell(1)
        dead_tuple = (dead,)
        dead.value = 3
        dead_readonly = source
        dead_optional = optional
        dead_union = union
        return dead_tuple[0].value
    else:
        live = Cell(2)
        aliases = (live,)
        live.value = 9
        return aliases[0].value

def mutable_loop() -> int32:
    flag = True
    value = 0
    while flag:
        flag = False
        value = 3
        continue
    else:
        value = 4
    return value
'''


def test_emitted_storage_pruning_preserves_live_aliases() -> None:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    original = functions["storage"]
    branch = original.body[0]
    assert isinstance(branch, th.THIRIf)
    # Keep real storage facts while varying reachability at the THIR boundary.
    constant = replace(original, body=(replace(branch, condition=literal(False)),))
    fn = lower_function(constant, MIRBodyId("test", "storage"), definitions=definitions)
    assert isinstance(fn, MIRFunction), fn
    assert not any(s.name and s.name.startswith("dead") for s in fn.slots)
    assert any(b.id.index > index for index, b in enumerate(fn.blocks))
    assert any(s.id.index > index for index, s in enumerate(fn.slots))
    assert len([s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]) == 4
    assert inspect_scope_lifetimes(fn).conflicts == ()
    assert execute(fn, True, Reference(0), OptionalValue(None), UnionValue(0, 1), heap={0: {}}) == 9
    mutable = lower(functions["mutable_loop"])
    assert any(isinstance(b.terminator, MIRBranch) for b in mutable.blocks)
    assert execute(mutable) == 4
