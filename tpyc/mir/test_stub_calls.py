"""Calls to stubs -- `@native` / `@cpp_template` free functions and builtin
initializers -- have no body to summarize: MIR derives their summary from
the declared contract (`@pure` / `transient=True`) and admits only readonly
leaf parameters, so the stub reads its arguments, reaches nothing else and
may raise. An owned-leaf result may borrow every argument the call lends."""

import re
from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import _iter_children
from ..type_def_registry import ParamPassing
from ..typesys import (
    BIGINT, INT32, STR, RefType, Representation, TupleType, VoidType, return_representation, unwrap_readonly,
    unwrap_ref_type,
)
from .call_contract import (
    MIRCallSummary, MIRContainerElements, MIRContainerStructure, MIRGlobalId, MIRParameterBinding, MIRParameterWrite,
    MIRReturnOrigin, MIRSummaryState, parameter_binding_problem, stub_protocol_argument, stub_summary, summary_problem,
)
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, analyze_dependencies
from .dump import dump_function
from .liveness import MIRPoint, analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRCall, MIRCompare, MIRCopy, MIRDeref, MIRFunction, MIRNotCovered,
    MIRPlace, MIRSlotKind, MIRValueKind,
)
from .retention import analyze_retention
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .validate import MIRValidationError, validate_function

SOURCE = """\
import math
import time
from tpy import int32, pure, StrView, String
from tpy.extern import native

G: str = "glob"

class Rec:
    n: int32
    def __init__(self, n: int32):
        self.n = n
    def __len__(self) -> int32:
        return self.n

@pure
@native("probe_view")
def probe_view(s: str) -> StrView: ...

@native("probe_plain")
def probe_plain(x: float) -> float: ...

@native("probe_tick")
def probe_tick() -> float: ...

@native("probe_fill", transient=True)
def probe_fill(s: String) -> None: ...

@native("probe_bump", transient=True)
def probe_bump(r: Rec) -> None: ...

def lent_len(s: str) -> int32:
    # `len` is @pure; its protocol parameter binds the str, lent for the call.
    return len(s)

def global_len() -> int32:
    # A global argument is lent through its handle: a stub writes no global.
    return len(G)

def clock() -> float:
    # `time.time` is bound `transient=True` and takes no arguments.
    return time.time()

def construct(x: float) -> int:
    # A pure initializer lends nothing, so its BigInt result is fresh.
    return int(x)

def smaller(a: int, b: int) -> int:
    # `min` binds `std::min`: the result may borrow either argument, so the local copies it.
    x = min(a, b)
    return x

def smaller_is(a: int, b: int) -> bool:
    # As an operand the borrowed result is read in place.
    return min(a, b) == a

def smaller_sum(a: int, b: int) -> int:
    # The result may borrow the sum's temporary; the copy happens inside its full expression.
    x = min(a + b, b)
    return x

def overloads(a: int, b: int, i: int32, j: int32) -> bool:
    # Two overloads of one stub in one body keep distinct identities.
    return min(i, j) == 1 and min(a, b) == a

def twice(x: float) -> float:
    y = math.log(x)
    return math.log(y)

def both(s: str, a: int, b: int) -> int32:
    x = min(a, b)
    return len(s)

def unmarked(x: float) -> float:
    return probe_plain(x)

def unmarked_nullary() -> float:
    # Lending no argument is no contract: an unmarked stub may reach any storage.
    return probe_tick()

def mut_ref(r: Rec) -> None:
    probe_bump(r)

def mutable_leaf(s: String) -> None:
    # A transient stub declares no const verdict for its String.
    probe_fill(s)

def record_len(r: Rec) -> int32:
    # `len` on a record dispatches the record's own `__len__`.
    return len(r)

def view_result(s: str) -> int32:
    v = probe_view(s)
    return 1
"""


@dataclass(frozen=True)
class _Lowered:
    compiler: object
    definitions: MIRDefinitions
    thir: dict
    bodies: dict
    summaries: dict
    # Asked while the compilation's view facts are latched: the per-test
    # reset clears them from the builtin TypeDefs.
    view_verdict: object


@pytest.fixture(scope="module")
def lowered():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        bodies = {name: lower_function(fn, MIRBodyId("stubs", name), definitions=mir.definitions,
                                       summaries=mir.workspace.summaries)
                  for name, fn in functions.items()}
        summaries = {identity.name: result for identity, result in mir.workspace.summaries.items()}
        view_verdict = stub_summary(_thir_calls(functions["view_result"])[0].stub_callee)
        yield _Lowered(compiler, mir.definitions, functions, bodies, summaries, view_verdict)


@pytest.fixture
def active(lowered):
    with activate_compiler(lowered.compiler):
        yield lowered


def _statements(fn: MIRFunction) -> list:
    return [stmt for block in fn.blocks for stmt in block.statements]


def _call_statements(fn: MIRFunction) -> list[MIRAssign]:
    return [stmt for stmt in _statements(fn) if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)]


def _lines(fn: MIRFunction) -> list[str]:
    return [re.sub(r" @ \d+:\d+$", "", line.strip()) for line in dump_function(fn).splitlines()]


def _thir_calls(fn: th.THIRFunction) -> list[th.THIRCall]:
    found, pending = [], list(fn.body)
    while pending:
        node = pending.pop(0)
        if isinstance(node, th.THIRCall):
            found.append(node)
        pending.extend(_iter_children(node))
    return found


def _point_after(fn: MIRFunction, stmt: MIRAssign) -> MIRPoint:
    block, index = next((b, i) for b in fn.blocks for i, s in enumerate(b.statements) if s is stmt)
    return MIRPoint(block.id, index + 1)


def _replace_call(fn: MIRFunction, stmt: MIRAssign, call: MIRCall, **body) -> MIRFunction:
    return replace(fn, **body, blocks=tuple(replace(b, statements=tuple(
        replace(s, value=call) if s is stmt else s for s in b.statements)) for b in fn.blocks))


def test_admitted_stub_calls_carry_their_declared_summary(active) -> None:
    # Two stubs in one body: `min` lends owned leaves, `len` lends through its protocol parameter.
    fn = active.bodies["both"]
    assert isinstance(fn, MIRFunction), fn
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    assert not isinstance(dependencies, MIRNotCovered) and not isinstance(events, MIRNotCovered)
    assert analyze_retention(fn, liveness, dependencies, events).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()
    # A stub may raise, so every call to one carries the exit fact.
    calls = [stmt.value for stmt in _call_statements(fn)]
    assert len(calls) == 2 and all(call.may_raise for call in calls) and fn.exceptional_exits
    for call in calls:
        summary = call.summary
        assert isinstance(summary.callee, th.THIRStubCallee)
        assert summary_problem(summary) is None and summary == stub_summary(summary.callee)
        assert summary.reads == frozenset(range(len(summary.parameters)))
        assert not (summary.writes or summary.invalidates or summary.transfers or summary.global_reads)
        assert summary.normal_return_only is False


def test_pure_stub_lends_an_owned_leaf_to_its_protocol_parameter(active) -> None:
    fn = active.bodies["lent_len"]
    stmt, = _call_statements(fn)
    binding, = stmt.value.summary.parameters
    assert binding.protocol and binding.passing is ParamPassing.CONST_REF and binding.readonly
    assert stmt.value.summary.callee.contract is th.THIRStubContract.PURE
    argument = fn.slots[stmt.value.arguments[0].index]
    assert (argument.type, argument.value_kind, argument.readonly) == (STR, MIRValueKind.BORROWED, True)
    # The holder borrows the parameter's storage in place: no copy.
    param, = (s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    refs = analyze_dependencies(fn, analyze_liveness(fn)).referents[_point_after(fn, stmt)]
    assert refs[MIRPlace(argument.id)] == frozenset({MIRReferent(MIRPlace(param), external=True)})
    assert not any(isinstance(s.value, MIRCopy) for s in _statements(fn) if isinstance(s, MIRAssign))
    assert "%2 = call stub tpy._builtins._funcs.len[Sized](%1) [pure, reader, may-raise]" in _lines(fn)
    # The stub's scalar result leaves the caller summarizable.
    assert active.summaries["lent_len"].state is MIRSummaryState.KNOWN


def test_a_global_argument_is_lent_through_its_handle(active) -> None:
    fn = active.bodies["global_len"]
    stmt, = _call_statements(fn)
    handle, = (s.id for s in fn.slots if s.kind is MIRSlotKind.GLOBAL)
    refs = analyze_dependencies(fn, analyze_liveness(fn)).referents[_point_after(fn, stmt)]
    assert refs[MIRPlace(stmt.value.arguments[0])] == frozenset({MIRReferent(MIRPlace(handle), external=True)})
    # The stub reads no global; the caller's own read of G is what its summary names.
    assert stmt.value.summary.global_reads == frozenset()
    result = active.summaries["global_len"]
    assert result.state is MIRSummaryState.KNOWN, result.reason
    assert result.summary.global_reads == frozenset({MIRGlobalId("main", "G")})


def test_transient_stub_without_arguments(active) -> None:
    fn = active.bodies["clock"]
    stmt, = _call_statements(fn)
    assert stmt.value.summary.callee.contract is th.THIRStubContract.TRANSIENT
    assert stmt.value.arguments == () and stmt.value.summary.parameters == ()
    assert "%0 = call stub time.time[]() [transient, reader, may-raise]" in _lines(fn)
    assert active.summaries["clock"].state is MIRSummaryState.KNOWN
    assert active.summaries["clock"].summary.normal_return_only is False


def test_pure_constructor_result_is_fresh(active) -> None:
    fn = active.bodies["construct"]
    stmt, = _call_statements(fn)
    summary = stmt.value.summary
    assert summary.callee.identity == th.THIRStubIdentity("builtins.int.__init__", (summary.parameters[0].type,))
    assert summary.returns == frozenset() and summary.borrowed_result is None
    result = fn.slots[stmt.target.root.index]
    assert result.value_kind is MIRValueKind.OWNED and result.type == BIGINT
    assert "%1 = call stub builtins.int.__init__[float](%2) [pure, reader, may-raise] [initialize_once]" in _lines(fn)


def test_stub_result_may_borrow_every_lent_argument(active) -> None:
    fn = active.bodies["smaller"]
    stmt, = _call_statements(fn)
    summary = stmt.value.summary
    assert summary.returns == frozenset({MIRReturnOrigin(0), MIRReturnOrigin(1)})
    assert summary.borrowed_result == th.THIRBorrowedRecord(BIGINT, True)
    holder = fn.slots[stmt.target.root.index]
    assert holder.value_kind is MIRValueKind.BORROWED and holder.readonly
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    refs = analyze_dependencies(fn, analyze_liveness(fn)).referents[_point_after(fn, stmt)]
    assert refs[MIRPlace(holder.id)] == frozenset(MIRReferent(MIRPlace(p), external=True) for p in params)
    # `x = min(a, b)` copies what the result references, as the C++ does.
    local = next(s for s in fn.slots if s.name == "x")
    copy, = (s for s in _statements(fn) if isinstance(s, MIRAssign) and s.target == MIRPlace(local.id))
    assert copy.value == MIRCopy(MIRPlace(holder.id, (MIRDeref(),)), may_raise=False)
    # A temporary argument's storage joins the origins.
    fn = active.bodies["smaller_sum"]
    stmt, = _call_statements(fn)
    refs = analyze_dependencies(fn, analyze_liveness(fn)).referents[_point_after(fn, stmt)]
    origins = refs[MIRPlace(stmt.target.root)]
    assert {r.external for r in origins} == {True, False}
    temporary, = (r.place.root for r in origins if not r.external)
    assert fn.slots[temporary.index].kind is MIRSlotKind.TEMPORARY
    # As an operand the holder is compared directly, with no copy.
    fn = active.bodies["smaller_is"]
    stmt, = _call_statements(fn)
    compare, = (s.value for s in _statements(fn) if isinstance(s, MIRAssign) and isinstance(s.value, MIRCompare))
    assert stmt.target.root in (compare.left, compare.right)
    assert not any(isinstance(s.value, MIRCopy) for s in _statements(fn) if isinstance(s, MIRAssign))


def test_overload_identities_stay_distinct_in_one_body(active) -> None:
    fn = active.bodies["overloads"]
    small, big = (stmt.value.summary for stmt in _call_statements(fn))
    assert small.callee.identity.qualified_name == big.callee.identity.qualified_name
    assert small.callee.identity.param_types == (INT32, INT32)
    assert big.callee.identity.param_types == (BIGINT, BIGINT)
    assert set(fn.call_summaries) == {small, big} and len(fn.call_summaries) == 2
    # Only the owned-leaf overload's result may borrow its arguments.
    assert small.returns == frozenset() and big.returns == frozenset({MIRReturnOrigin(0), MIRReturnOrigin(1)})


def test_calls_of_one_stub_share_one_summary(active) -> None:
    fn = active.bodies["twice"]
    first, second = (stmt.value.summary for stmt in _call_statements(fn))
    assert first is second and fn.call_summaries == (first,)


def test_refused_stubs_declare_what_the_gates_read(active) -> None:
    calls = {name: _thir_calls(active.thir[name])[0].stub_callee
             for name in ("unmarked", "unmarked_nullary", "mut_ref", "mutable_leaf", "record_len", "view_result")}
    assert calls["unmarked"].contract is None
    # No parameter to lend, and still no contract: admission is never derived from the arguments.
    assert calls["unmarked_nullary"].contract is None and calls["unmarked_nullary"].signature.param_types == ()
    assert calls["mut_ref"].contract is th.THIRStubContract.TRANSIENT
    assert calls["mut_ref"].signature.passings == (ParamPassing.MUT_REF,)
    # The passing alone would admit the String; the declared verdict is what refuses it.
    assert calls["mutable_leaf"].signature.passings == (ParamPassing.CONST_REF,)
    assert calls["mutable_leaf"].readonly == (False,)
    assert calls["record_len"].contract is th.THIRStubContract.PURE
    # A view result may borrow every argument the stub lends, from the declaration alone.
    assert calls["view_result"].contract is th.THIRStubContract.PURE
    assert isinstance(active.view_verdict, MIRCallSummary)
    assert active.view_verdict.returns == frozenset({MIRReturnOrigin(0)})


def test_stub_summary_admission_gates(active) -> None:
    length = _thir_calls(active.thir["lent_len"])[0].stub_callee
    assert not isinstance(stub_summary(length), str)
    signature = length.signature
    pair = TupleType((INT32, INT32))
    for damaged, reason in (
        (replace(length, contract=None), "stub declares no contract"),
        # A transient stub may reach a record argument's dunder through the protocol.
        (replace(length, contract=th.THIRStubContract.TRANSIENT), "stub protocol parameter needs a pure contract"),
        (replace(length, signature=replace(signature, passings=None)), "stub signature unpublished"),
        (replace(length, signature=replace(signature, return_representation=None)), "stub signature unpublished"),
        (replace(length, signature=replace(signature, passings=(ParamPassing.MUT_REF,))),
         "stub parameter is not a readonly leaf"),
        (replace(length, readonly=(False,)), "stub parameter is not a readonly leaf"),
        (replace(length, identity=replace(length.identity, param_types=())), "invalid stub callee"),
        # A stored aggregate result is neither a leaf nor an owned leaf.
        (replace(length, signature=replace(signature, return_type=pair,
                                           return_representation=return_representation(pair))),
         "unsupported stub result type"),
    ):
        assert stub_summary(damaged) == reason
    minimum = _thir_calls(active.thir["smaller"])[0].stub_callee
    # An owned leaf passed any way but a readonly borrow could be mutated or kept.
    for passing in (ParamPassing.VALUE, ParamPassing.MUT_REF, ParamPassing.OWN):
        damaged = replace(minimum, signature=replace(minimum.signature, passings=(passing, ParamPassing.CONST_REF)))
        assert stub_summary(damaged) == "stub parameter is not a readonly leaf"


def test_validator_rejects_a_summary_that_disagrees_with_the_declaration(active) -> None:
    fn = active.bodies["smaller"]
    stmt, = _call_statements(fn)
    summary = stmt.value.summary
    for damaged in (replace(summary, normal_return_only=True), replace(summary, returns=frozenset()),
                    replace(summary, global_reads=frozenset({MIRGlobalId("main", "G")})),
                    replace(summary, reads=frozenset({0}))):
        assert summary_problem(damaged) == "stub summary differs from its declaration"
        body = _replace_call(fn, stmt, replace(stmt.value, summary=damaged, may_raise=not damaged.normal_return_only),
                             call_summaries=(damaged,))
        with pytest.raises(MIRValidationError, match="stub summary differs from its declaration"):
            validate_function(body)
    # A summary of a stub with no contract is no summary at all.
    unmarked = replace(summary, callee=replace(summary.callee, contract=None))
    assert summary_problem(unmarked) == "stub declares no contract"
    # No declaration says a stub cannot raise, so a call claiming a normal return is refused.
    with pytest.raises(MIRValidationError, match="stub call must be a possible exceptional exit"):
        validate_function(_replace_call(fn, stmt, replace(stmt.value, may_raise=False)))


def test_validator_rejects_a_protocol_argument_that_is_not_a_builtin_leaf(active) -> None:
    fn = active.bodies["both"]
    length = next(stmt for stmt in _call_statements(fn) if stmt.value.summary.parameters[0].protocol)
    validate_function(fn)
    # The owned local `x` is storage, not a lent leaf.
    local = next(s.id for s in fn.slots if s.name == "x")
    with pytest.raises(MIRValidationError, match="call protocol argument is not a builtin leaf"):
        validate_function(_replace_call(fn, length, replace(length.value, arguments=(local,))))


def test_calls_of_one_identity_must_publish_one_declaration(active) -> None:
    fn = active.thir["twice"]
    first, second = _thir_calls(fn)
    changed = replace(second, stub_callee=replace(second.stub_callee, readonly=(False,)))
    decl, ret = fn.body
    assert decl.init is first and ret.value is second
    body = replace(fn, body=(decl, replace(ret, value=changed)))
    result = lower_function(body, MIRBodyId("stubs", "inconsistent"), definitions=active.definitions,
                            summaries={})
    assert isinstance(result, MIRNotCovered) and result.reason == "inconsistent stub callee facts"


# --- native container method stubs -------------------------------------------

METHOD_SOURCE = """\
from tpy import int32, Own, Span, StrView, readonly

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

class Ordered:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __lt__(self, other: Ordered) -> bool:
        return self.x < other.x

class Bag:
    items: list[int32]
    def __init__(self) -> None:
        self.items = []

def methods(xs: list[int32], d: dict[str, int32], ps: list[Point], os: list[Ordered], ys: list[int32],
            ss: list[str], sp: Span[int32], vs: list[StrView], v: StrView) -> int32:
    xs.append(1)
    a = xs.pop()
    b = xs.pop(0)
    xs.sort()
    os.sort()
    xs.extend(ys)
    g = d.get("k", 0)
    s = d.setdefault("k", 0)
    n = 0
    for e in d.values():
        n += e
    ps.append(Point(1))
    ss.append("a")
    sp.sort()
    vs.clear()
    cp = xs.copy()
    return a + b + g + s + n + len(xs) + len(cp)

def writes(xs: list[int32], d: dict[str, int32], ps: list[Point], k: str, sp: Span[int32]) -> None:
    xs[0] = 1
    d[k] = 3
    ps[0] = Point(2)
    sp[0] = 4

def shapes(xs: list[int32], r: readonly[list[int32]], own: Own[list[int32]], sp: Span[int32],
           rsp: Span[readonly[int32]], bag: Bag, ps: list[Point]) -> None:
    pass
"""


@dataclass(frozen=True)
class _Methods:
    thir: dict
    stubs: dict


def _method_stubs(fn: th.THIRFunction) -> dict[str, list[th.THIRStubCallee]]:
    found: dict[str, list] = {}
    pending = list(fn.body)
    while pending:
        node = pending.pop(0)
        stub = getattr(node, "stub_callee", None) if isinstance(node, (th.THIRMethodCall, th.THIRSetItem)) else None
        if stub is not None:
            found.setdefault(stub.identity.qualified_name.rsplit(".", 1)[-1], []).append(stub)
        pending.extend(_iter_children(node))
    return found


# Per test: the view facts are the compiled stubs', cleared between tests.
@pytest.fixture
def methods():
    compiler, modules = _compile(METHOD_SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    with activate_compiler(compiler):
        yield _Methods(functions, {name: _method_stubs(fn) for name, fn in functions.items()})


def _writes(summary: MIRCallSummary) -> set[tuple[int, tuple[str, ...]]]:
    return {(w.parameter, tuple(type(p).__name__ for p in w.path)) for w in summary.writes}


def _summary(stub: th.THIRStubCallee) -> MIRCallSummary:
    summary = stub_summary(stub)
    assert isinstance(summary, MIRCallSummary), summary
    assert summary_problem(summary) is None
    # Every method may raise and reads every parameter; a stub summary retains nothing.
    assert summary.normal_return_only is False and summary.reads == frozenset(range(len(summary.parameters)))
    assert not (summary.invalidates or summary.transfers or summary.global_reads)
    return summary


STRUCTURE = ("MIRContainerStructure",)
ELEMENTS = ("MIRContainerElements",)


def test_a_mutating_method_writes_its_receivers_structure(methods) -> None:
    stubs = methods.stubs["methods"]
    append = _summary(stubs["append"][0])
    assert _writes(append) == {(0, STRUCTURE)} and append.returns == frozenset()
    receiver, value = append.parameters
    assert (receiver.passing, receiver.readonly, receiver.borrowed_record) == (ParamPassing.MUT_REF, False, None)
    # The element argument is copied into the container.
    assert (value.type, value.passing, value.readonly) == (INT32, ParamPassing.VALUE, False)
    # Either `pop` overload removes an element; its result is the caller's own.
    for pop in stubs["pop"]:
        summary = _summary(pop)
        assert _writes(summary) == {(0, STRUCTURE)} and summary.returns == frozenset()
        assert summary.borrowed_result is None
    sort = _summary(stubs["sort"][0])
    assert sort.callee.bound_arguments == (INT32,) and _writes(sort) == {(0, STRUCTURE)}
    # A record element is moved in; an owned leaf is copied in.
    record, owned = (_summary(s) for s in stubs["append"][1:3])
    assert record.parameters[1].passing is ParamPassing.OWN and not record.parameters[1].readonly
    assert (owned.parameters[1].type, owned.parameters[1].passing) == (STR, ParamPassing.VALUE)


def test_readers_write_nothing_and_view_results_borrow_the_receiver(methods) -> None:
    stubs = methods.stubs["methods"]
    get = _summary(stubs["get"][0])
    assert get.writes == frozenset() and get.returns == frozenset() and get.borrowed_result is None
    assert [b.readonly for b in get.parameters] == [True, True, False]
    # The @auto_readonly mutable clone is pure: it reads a receiver it could write.
    values = _summary(stubs["values"][0])
    assert values.writes == frozenset() and not values.parameters[0].readonly
    assert values.returns == frozenset({MIRReturnOrigin(0)})
    assert values.borrowed_result == th.THIRBorrowedRecord(values.callee.signature.return_type, False)
    copy = _summary(stubs["copy"][0])
    assert copy.writes == frozenset() and copy.returns == frozenset() and copy.parameters[0].readonly


def test_setdefault_grows_the_dict_and_returns_a_copy(methods) -> None:
    setdefault = _summary(methods.stubs["methods"]["setdefault"][0])
    assert _writes(setdefault) == {(0, STRUCTURE)}
    # An int32 value returns by value; the key is lent as a view.
    assert setdefault.returns == frozenset() and setdefault.parameters[1].readonly


def test_refs_preserving_and_view_writes_replace_elements(methods) -> None:
    stubs = methods.stubs["writes"]
    for setitem in stubs["__setitem__"]:
        assert _writes(_summary(setitem)) == {(0, ELEMENTS)}
    # A view cannot change its container's shape: every write through it is an element write.
    sort = _summary(methods.stubs["methods"]["sort"][2])
    assert sort.parameters[0].passing is ParamPassing.VALUE and _writes(sort) == {(0, ELEMENTS)}


def test_kept_method_refusals(methods) -> None:
    stubs = methods.stubs["methods"]
    # User comparison code runs inside the sort.
    assert stub_summary(stubs["sort"][1]) == "stub protocol argument is not a builtin leaf"
    # An iterable parameter runs code the declaration does not describe.
    assert stub_summary(stubs["extend"][0]) == "stub declares no contract"
    assert stub_summary(stubs["clear"][0]) == "container holds a borrow"


def test_method_stub_summary_gates(methods) -> None:
    append = methods.stubs["methods"]["append"][0]
    signature = append.signature
    xs, value = signature.param_types
    nested = replace(xs, type_args=(xs,))
    damaged_receiver = (STR, value)
    for damaged, reason in (
        (replace(append, identity=replace(append.identity, param_types=damaged_receiver),
                 signature=replace(signature, param_types=damaged_receiver)), "unsupported stub receiver"),
        (replace(append, identity=replace(append.identity, param_types=(nested, value)),
                 signature=replace(signature, param_types=(nested, value))), "unsupported native container element"),
        (replace(append, bound_arguments=(signature.param_types[0],)), "stub protocol argument is not a builtin leaf"),
        # A container is readonly exactly at CONST_REF, and passes by reference.
        (replace(append, readonly=(True, False)), "unsupported container call parameter"),
        (replace(append, signature=replace(signature, passings=(ParamPassing.CONST_REF, ParamPassing.VALUE))),
         "unsupported container call parameter"),
        (replace(append, signature=replace(signature, passings=(ParamPassing.VALUE, ParamPassing.VALUE))),
         "call parameter passing differs from its type"),
        (replace(append, signature=replace(signature, passings=None)), "stub signature unpublished"),
        (replace(append, contract="pure"), "invalid stub callee"),
        (replace(append, receiver=False, mutates_elements=True), "invalid stub callee"),
        # A declared element write on a method that declares no write.
        (replace(append, mutates_elements=True, contract=th.THIRStubContract.PURE), "invalid stub callee"),
        (replace(append, mutates_elements=True, readonly=(True, False)), "invalid stub callee"),
        # A container returned by reference names no holder MIR models.
        (replace(append, signature=replace(signature, return_type=RefType(xs),
                                           return_representation=Representation.REFERENCE)),
         "unsupported stub result type"),
        # An element argument at a borrowing passing could be kept by the container.
        (replace(append, signature=replace(signature, passings=(ParamPassing.MUT_REF, ParamPassing.CONST_REF))),
         "stub parameter is not a readonly leaf"),
    ):
        assert stub_summary(damaged) == reason, reason
    # An element handed over to the container is the callee's own: never a readonly borrow.
    record = _summary(methods.stubs["methods"]["append"][1])
    element = record.parameters[1]
    assert parameter_binding_problem(record.callee.signature.param_types[1], element) is None
    assert parameter_binding_problem(record.callee.signature.param_types[1], replace(element, readonly=True)) == (
        "unsupported element call parameter")
    summary = _summary(append)
    for damaged in (replace(summary, writes=frozenset()),
                    replace(summary, writes=frozenset({MIRParameterWrite(0, (MIRContainerElements(),))}))):
        assert summary_problem(damaged) == "stub summary differs from its declaration"


def _user_summary(fn: th.THIRFunction, bindings: tuple[MIRParameterBinding, ...], *, writes=frozenset(),
                  ret=None, borrowed=None, returns=frozenset()) -> MIRCallSummary:
    types = tuple(p.type for p in fn.params)
    ret = ret if ret is not None else VoidType()
    signature = th.THIRCallableSignature(types, ret, borrowed, tuple(b.passing for b in bindings),
                                         return_representation(ret))
    return MIRCallSummary(th.THIRResolvedCallee(th.THIRFunctionIdentity("main", fn.name), signature), bindings,
                          frozenset(range(len(bindings))), frozenset(writes), frozenset(),
                          frozenset(MIRReturnOrigin(r) for r in returns), frozenset(), False)


def test_user_summaries_publish_container_writes(methods) -> None:
    fn = methods.thir["shapes"]
    params = {p.name: p for p in fn.params}
    bag = params["bag"].borrowed_record
    xs = params["xs"].native_container.type
    items = th.THIRFieldIdentity(bag.type, "items", xs)
    point_list = params["ps"].native_container.type
    sp, rsp = (params[n].type for n in ("sp", "rsp"))
    bindings = (MIRParameterBinding(xs, ParamPassing.MUT_REF, False),
                MIRParameterBinding(xs, ParamPassing.CONST_REF, True),
                MIRParameterBinding(xs, ParamPassing.OWN, False),
                MIRParameterBinding(sp, ParamPassing.VALUE, False),
                MIRParameterBinding(rsp, ParamPassing.VALUE, True),
                MIRParameterBinding(bag.type, ParamPassing.MUT_REF, False, replace(bag, readonly=False)),
                MIRParameterBinding(point_list, ParamPassing.CONST_REF, True))
    fn = replace(fn, params=tuple(replace(p, passing=b.passing) for p, b in zip(fn.params, bindings)))
    structure, elements = MIRContainerStructure(), MIRContainerElements()
    for path in ((0, (structure,)), (0, (elements,)), (2, (structure,)), (3, (elements,)),
                 (5, (items, structure)), (5, (items, elements))):
        write = MIRParameterWrite(*path)
        assert summary_problem(_user_summary(fn, bindings, writes={write})) is None, path
    for path, reason in (
        # A readonly parameter is never written; a view never changes its container's shape.
        ((1, (structure,)), "unsupported call write field or access"),
        ((4, (elements,)), "unsupported call write field or access"),
        ((3, (structure,)), "unsupported call write field or access"),
        # A container field is written only through a projection; a projection ends the path.
        ((5, (items,)), "unsupported call write field or access"),
        ((5, (structure, items)), "invalid call write path"),
        ((0, (elements, elements)), "invalid call write path"),
        ((5, (items, items, structure)), "invalid call write path"),
        ((0, ()), "invalid call write path"),
    ):
        write = MIRParameterWrite(*path)
        assert summary_problem(_user_summary(fn, bindings, writes={write})) == reason, path
    # Bindings: a container is readonly exactly at CONST_REF; a readonly-element view is readonly.
    for index, damaged in ((0, replace(bindings[0], readonly=True)), (2, replace(bindings[2], readonly=True)),
                           (4, replace(bindings[4], readonly=False))):
        broken = bindings[:index] + (damaged,) + bindings[index + 1:]
        assert summary_problem(_user_summary(fn, broken)) in (
            "unsupported container call parameter", "unsupported container view call parameter")


def test_user_summaries_return_container_views_and_elements_of_parameters(methods) -> None:
    fn = methods.thir["shapes"]
    xs, = (p for p in fn.params if p.name == "xs")
    ps, = (p for p in fn.params if p.name == "ps")
    fn = replace(fn, params=(xs, ps))
    bindings = (MIRParameterBinding(xs.native_container.type, ParamPassing.CONST_REF, True),
                MIRParameterBinding(ps.native_container.type, ParamPassing.CONST_REF, True))
    rsp = next(p.type for p in methods.thir["shapes"].params if p.name == "rsp")
    sp = next(p.type for p in methods.thir["shapes"].params if p.name == "sp")
    assert summary_problem(_user_summary(fn, bindings, ret=rsp, returns={0})) is None
    # A mutable view of a parameter lent readonly would write what the caller lent.
    assert summary_problem(_user_summary(fn, bindings, ret=sp, returns={0})) == (
        "unsupported return origin type or access")
    # A view of another element type is no view of the parameter.
    assert summary_problem(_user_summary(fn, bindings, ret=rsp, returns={1})) == (
        "unsupported return origin type or access")
    point = ps.native_container.element.type
    element = th.THIRBorrowedRecord(point, True)
    assert summary_problem(_user_summary(fn, bindings, ret=RefType(point), borrowed=element, returns={1})) is None
    assert summary_problem(_user_summary(fn, bindings, ret=RefType(point), borrowed=element, returns={0})) == (
        "unsupported return origin type or access")
    mutable = th.THIRBorrowedRecord(point, False)
    assert summary_problem(_user_summary(fn, bindings, ret=RefType(point), borrowed=mutable, returns={1})) == (
        "unsupported return origin type or access")


def test_a_pure_stub_reads_a_native_container_through_its_protocol_parameter(methods) -> None:
    fn = methods.thir["methods"]
    params = {p.name: p for p in fn.params}
    assert stub_protocol_argument(params["xs"].native_container.type)
    assert stub_protocol_argument(INT32) and stub_protocol_argument(STR)
    # A container holding a borrow, or of records running user code, is not the stub's own code.
    views = unwrap_readonly(unwrap_ref_type(params["vs"].type))
    ordered = unwrap_readonly(unwrap_ref_type(params["os"].type))
    assert not stub_protocol_argument(views) and not stub_protocol_argument(ordered)
    assert not stub_protocol_argument(ps_type := params["ps"].native_container.element.type)
    assert ps_type.qualified_name() == "__main__.Point"
