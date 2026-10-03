"""User method calls lower through their callee's summary, whose parameter 0 is the receiver."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import INT32, NominalType
from .call_contract import (
    MIRContainerStructure, MIRParameterBinding, MIRParameterWrite, MIRSummaryResult, MIRSummaryState, summary_problem,
)
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import call_return_problem
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRBodyId, MIRCall, MIRCallStmt, MIRContainerLayout, MIRFunction, MIRNotCovered, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRTupleElement, MIRValueKind,
)
from .summaries import summarize_function
from .validate import MIRValidationError, validate_function


SOURCE = '''\
from tpy import int32, readonly


class Counter:
    n: int32
    items: list[int32]

    def __init__(self) -> None:
        self.n = 0
        self.items = []

    def bump(self) -> None:
        self.n += 1

    def push(self, v: int32) -> None:
        self.items.append(v)

    @readonly
    def total(self) -> int32:
        t = 0
        for x in self.items:
            t += x
        return t

    def inner(self) -> None:
        bump_free(self)

    def outer(self) -> None:
        self.inner()

    def countdown(self, k: int32) -> int32:
        if k <= 0:
            return self.n
        return self.countdown(k - 1)

    def ping(self, k: int32) -> int32:
        return pong(self, k)

    def merge_from(self, other: "Counter") -> None:
        for x in other.items:
            self.items.append(x)

    def all_items(self) -> list[int32]:
        return self.items


class Gauge:
    level: int32

    def __init__(self, level: int32) -> None:
        self.level = level

    def bump(self) -> None:
        self.level += 1


def bump_free(c: Counter) -> None:
    c.n += 1


def pong(c: Counter, k: int32) -> int32:
    if k <= 0:
        return 0
    return c.ping(k - 1)


def scale(k: int32) -> int32:
    return k * 2


def use(c: Counter) -> int32:
    c.bump()
    c.push(3)
    return c.total()


def grow(c: Counter) -> None:
    for x in c.items:
        c.push(x)


def call_merge(a: Counter, b: Counter) -> None:
    a.merge_from(b)


def both(c: Counter, g: Gauge) -> None:
    c.bump()
    g.bump()


def call_all_items(c: Counter) -> int:
    xs = c.all_items()
    return len(xs)
'''

INTS = NominalType("list", (INT32,), _module_qname="builtins.list")


@dataclass(frozen=True)
class _Program:
    compiler: object
    modules: list
    # THIR bodies by `Owner.name` for a method, `name` for a free function.
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    functions = {(f"{fn.receiver.type.name}." if fn.receiver is not None else "") + node.name: fn
                 for node, fn in ctx.thir_functions.items()}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, modules, functions, mir.definitions, mir.workspace,
                       {v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.fixture
def active(program):
    # The per-test state reset clears the stub facts latched onto the static TypeDefs.
    for module in program.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(program.compiler):
        yield program


def _summary(program: _Program, name: str) -> MIRSummaryResult:
    return program.workspace.summaries[program.functions[name].resolved_callee.identity]


def _known(program: _Program, name: str):
    result = _summary(program, name)
    assert result.state is MIRSummaryState.KNOWN, (name, result.reason)
    return result.summary


def _record(program: _Program, name: str) -> NominalType:
    return program.functions[f"{name}.bump"].receiver.type


def _field(program: _Program, owner: str, name: str, typ) -> th.THIRFieldIdentity:
    return th.THIRFieldIdentity(_record(program, owner), name, typ)


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


# --- method summaries --------------------------------------------------------------

def test_a_method_summary_binds_the_receiver_first(program: _Program) -> None:
    counter = _record(program, "Counter")
    identity = program.functions["Counter.push"].resolved_callee.identity
    assert identity.owner == counter.qualified_name()
    summary = _known(program, "Counter.push")
    assert summary.parameters == (
        MIRParameterBinding(counter, ParamPassing.MUT_REF, False, th.THIRBorrowedRecord(counter, False)),
        MIRParameterBinding(INT32, ParamPassing.VALUE, False))
    items = _field(program, "Counter", "items", INTS)
    assert summary.writes == {MIRParameterWrite(0, (items, MIRContainerStructure()))}
    assert summary.returns == frozenset() and summary_problem(summary) is None
    # The body's receiver slot carries the passing the signature publishes for parameter 0.
    receiver = next(s for s in _lowered(program, "Counter.push").slots if s.kind is MIRSlotKind.PARAMETER)
    assert (receiver.name, receiver.passing) == ("self", ParamPassing.MUT_REF)


def test_a_readonly_receiver_is_lent_readonly_and_never_written(program: _Program) -> None:
    counter = _record(program, "Counter")
    summary = _known(program, "Counter.total")
    assert summary.parameters == (
        MIRParameterBinding(counter, ParamPassing.CONST_REF, True, th.THIRBorrowedRecord(counter, True)),)
    assert summary.writes == frozenset()


def test_a_method_to_method_to_function_chain_is_scheduled_leaves_first(program: _Program) -> None:
    order = [body.declaration.split("@")[0] for body in program.workspace.bodies]
    assert order.index("bump_free") < order.index("Counter.inner") < order.index("Counter.outer")
    leaf = _known(program, "bump_free").writes
    assert leaf == {MIRParameterWrite(0, (_field(program, "Counter", "n", INT32),))}
    # Each caller publishes its callee's write through its own receiver.
    assert _known(program, "Counter.inner").writes == leaf
    assert _known(program, "Counter.outer").writes == leaf


@pytest.mark.parametrize("name", ["Counter.countdown", "Counter.ping", "pong"])
def test_recursive_methods_have_no_summary(program: _Program, name: str) -> None:
    result = _summary(program, name)
    assert (result.state, result.reason) == (MIRSummaryState.OPAQUE, "recursive or recursion-dependent call")


def test_same_named_methods_of_two_records_have_two_summaries(program: _Program) -> None:
    counter = program.functions["Counter.bump"].resolved_callee.identity
    gauge = program.functions["Gauge.bump"].resolved_callee.identity
    assert counter != gauge and (counter.module, counter.name) == (gauge.module, gauge.name)
    assert _known(program, "Counter.bump").writes == {MIRParameterWrite(0, (_field(program, "Counter", "n", INT32),))}
    assert _known(program, "Gauge.bump").writes == {
        MIRParameterWrite(0, (_field(program, "Gauge", "level", INT32),))}
    lines = dump_function(_lowered(program, "both"))
    assert f"call {counter.owner}.bump(%0)" in lines and f"call {gauge.owner}.bump(%1)" in lines


# --- callers -----------------------------------------------------------------------

def test_a_free_caller_binds_the_receiver_as_argument_zero(program: _Program) -> None:
    verdict = program.verdicts["use"]
    assert (verdict.status, verdict.conflicts) == (MIRVerdictStatus.COVERED, ())
    owner = program.functions["Counter.push"].resolved_callee.identity.owner
    assert f"call {owner}.push(%0, " in dump_function(verdict.function)
    assert _known(program, "use").writes == {
        MIRParameterWrite(0, (_field(program, "Counter", "n", INT32),)),
        MIRParameterWrite(0, (_field(program, "Counter", "items", INTS), MIRContainerStructure()))}


def test_a_method_growing_the_iterated_field_conflicts(program: _Program) -> None:
    verdict = program.verdicts["grow"]
    assert (verdict.status, verdict.conflicts) == (MIRVerdictStatus.COVERED, ("replacement",))


def test_an_aliased_record_argument_conflicts_at_the_callee(program: _Program) -> None:
    # A body assumes its borrowed parameters may alias: the caller passing two records is not checked again.
    assert program.verdicts["Counter.merge_from"].conflicts == ("replacement",)
    caller = program.verdicts["call_merge"]
    assert (caller.status, caller.conflicts) == (MIRVerdictStatus.COVERED, ())


def test_a_container_returned_from_the_receiver_keeps_the_non_container_origin_guard(program: _Program) -> None:
    summary = _known(program, "Counter.all_items")
    assert summary.returns == {0}
    # The caller's container binding refuses before the guard, as its free twin's does.
    caller = program.verdicts["call_all_items"]
    assert (caller.status, caller.reason) == (MIRVerdictStatus.UNCOVERED, "unsupported reference fact")
    body = MIRBodyId("methods", "guard")
    receiver = MIRSlot(MIRSlotId(body, 0), _record(program, "Counter"), MIRSlotKind.PARAMETER, "self",
                       form=th.Form.BORROW, value_kind=MIRValueKind.BORROWED)
    result = MIRSlot(MIRSlotId(body, 1), INTS, MIRSlotKind.LOCAL, "xs", form=th.Form.BORROW,
                     value_kind=MIRValueKind.BORROWED_CONTAINER,
                     container_layout=MIRContainerLayout(MIRTupleElement(INT32)))
    slots = {slot.id: slot for slot in (receiver, result)}
    problem = call_return_problem(MIRCall(summary, (receiver.id,), True), result, slots)
    assert problem == "container result of a non-container argument"


# --- contract validation -------------------------------------------------------------

def _owned_by(summary, owner: str):
    callee = summary.callee
    return replace(summary, callee=replace(callee, identity=replace(callee.identity, owner=owner)))


def test_a_method_summary_needs_its_owner_receiver_first(program: _Program) -> None:
    counter = _record(program, "Counter").qualified_name()
    # An owner without a receiver binding, a receiver of another record, an empty owner.
    assert summary_problem(_owned_by(_known(program, "scale"), counter)) == "method summary without its receiver"
    assert summary_problem(_owned_by(_known(program, "Gauge.bump"), counter)) == "method summary without its receiver"
    assert summary_problem(_owned_by(_known(program, "Counter.bump"), "")) == "invalid call summary identity or facts"


def test_a_damaged_receiver_binding_is_refused(program: _Program) -> None:
    total = _known(program, "Counter.total")
    receiver = total.parameters[0]
    written = MIRParameterWrite(0, (_field(program, "Counter", "n", INT32),))
    # A readonly receiver written, a receiver passed beyond its access, the receiver missing.
    assert summary_problem(replace(total, writes=frozenset({written}))) == "unsupported call write field or access"
    assert summary_problem(replace(total, parameters=(replace(receiver, passing=ParamPassing.MUT_REF),))) == (
        "unsupported record call parameter")
    push = _known(program, "Counter.push")
    assert summary_problem(replace(push, parameters=push.parameters[1:])) == "unsupported call summary contract"


def test_a_method_call_without_its_receiver_argument_is_malformed(program: _Program) -> None:
    fn = _lowered(program, "use")
    block = fn.blocks[0]
    index, stmt = next((i, s) for i, s in enumerate(block.statements) if isinstance(s, MIRCallStmt))
    damaged = replace(stmt, call=replace(stmt.call, arguments=stmt.call.arguments[1:]))
    statements = (*block.statements[:index], damaged, *block.statements[index + 1:])
    with pytest.raises(MIRValidationError, match="call arity mismatch"):
        validate_function(replace(fn, blocks=(replace(block, statements=statements), *fn.blocks[1:])))


def test_a_call_consuming_another_callees_summary_is_refused(active: _Program) -> None:
    summaries = dict(active.workspace.summaries)
    summaries[active.functions["Counter.bump"].resolved_callee.identity] = _summary(active, "Gauge.bump")
    result = lower_function(active.functions["both"], MIRBodyId("methods", "both"),
                            definitions=active.definitions, summaries=summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "call summary signature or contract mismatch"


def test_a_readonly_holder_cannot_receive_a_mutating_call(active: _Program) -> None:
    fn = active.functions["use"]
    param = fn.params[0]
    readonly = replace(fn, params=(replace(param, borrowed_record=replace(param.borrowed_record, readonly=True)),))
    result = lower_function(readonly, MIRBodyId("methods", "use"), definitions=active.definitions,
                            summaries=active.workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "call record argument mismatch"


@pytest.mark.parametrize("name, owner", [("Counter.bump", None), ("bump_free", "x.Counter")])
def test_a_body_and_its_callee_disagreeing_on_the_owner_is_opaque(active: _Program, name: str,
                                                                   owner: str | None) -> None:
    fn = active.functions[name]
    callee = fn.resolved_callee
    damaged = replace(fn, resolved_callee=replace(callee, identity=replace(callee.identity, owner=owner)))
    result = summarize_function(damaged, _lowered(active, name), active.definitions)
    assert (result.state, result.reason) == (MIRSummaryState.OPAQUE, "summary definition or result contract mismatch")
