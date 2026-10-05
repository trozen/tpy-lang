"""A summary's return origin is a path into a parameter, resolved by the caller like a write place."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..thir import nodes as th
from ..thir.test_method_stubs import _replace_node, nodes as _thir_nodes
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing, latch_declared_native_flags
from ..typesys import INT32, STR, NominalType, ReadonlyType, RefType
from .call_contract import (
    MIRCallSummary, MIRContainerElements, MIRContainerStructure, MIRParameterBinding, MIRReturnOrigin, MIRSummaryState, bound_result,
    result_problem, stub_summary, summary_problem,
)
from .collect import MIRBodyVerdict, MIRVerdictStatus, enumerate_bodies
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, analyze_dependencies, call_return_problem, resolve_call_returns
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRCall, MIRContainerLayout, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleElement, MIRValueKind,
)
from .summaries import summarize_function


SOURCE = '''\
from tpy import int32, readonly, auto_readonly, StrView, Span


class Inner:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Bag:
    n: int32
    name: str
    items: list[int32]
    more: list[int32]

    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name
        self.items = []
        self.more = []

    @property
    def elems(self) -> list[int32]:
        return self.items

    @elems.setter
    def elems(self, v: list[int32]) -> None:
        self.items = v

    @property
    def count(self) -> int32:
        return self.n

    @count.setter
    def count(self, v: int32) -> None:
        self.n = v

    @property
    def label(self) -> str:
        return self.name

    @property
    def tag(self) -> StrView:
        return self.name

    @auto_readonly
    def me(self) -> "Bag":
        return self

    @auto_readonly
    def view_items(self) -> list[int32]:
        return self.items

    def other_items(self) -> list[int32]:
        return self.more

    def grab(self) -> list[int32]:
        self.n += 1
        return self.items

    def push(self, v: int32) -> None:
        self.items.append(v)

    def rename(self, v: str) -> None:
        self.name = v

    def span_items(self) -> Span[int32]:
        return self.items[0:]


class Box:
    inner: Inner

    def __init__(self, inner: Inner) -> None:
        self.inner = inner


def via_getter(b: Bag) -> int32:
    xs = b.elems
    return xs[0]


def via_field(b: Bag) -> int32:
    ys = b.items
    return ys[0]


def read_count(b: Bag) -> int32:
    return b.count


def read_label(b: Bag) -> int:
    s = b.label
    return len(s)


def read_readonly(b: readonly[Bag]) -> int32:
    d = b.me()
    return b.count + d.n


def read_inferred(b: Bag) -> int32:
    t = 0
    for x in b.view_items():
        t += x
    return t


def write_through_me(b: Bag) -> None:
    d = b.me()
    d.n = 5


def set_count(b: Bag) -> None:
    b.count = 3


def iterate(b: Bag) -> int32:
    t = 0
    for x in b.elems:
        t += x
    return t


def view_across(b: Bag) -> int:
    v: StrView = b.tag
    b.rename("z")
    return len(v)


def iterate_grab(b: Bag) -> int32:
    t = 0
    for x in b.grab():
        t += x
    return t


def grow_alias(b: Bag) -> int32:
    xs = b.view_items()
    b.push(4)
    return xs[0]


def main() -> None:
    b = Bag("b")
    b.push(1)
    print(via_getter(b), via_field(b), read_count(b), read_label(b), read_readonly(b), iterate(b), grow_alias(b))
    set_count(b)
    write_through_me(b)
    print(read_inferred(b), view_across(b), b.count, len(b.span_items()), Box(Inner(1)).inner.x, iterate_grab(b))


main()
'''

INTS = NominalType("list", (INT32,), _module_qname="builtins.list")


@dataclass(frozen=True)
class _Program:
    compiler: object
    modules: list
    # THIR bodies by body declaration (`Bag.elems@23:4`, `...#2` for a twin).
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        functions = {body: fn for body, fn in mir.workspace.definitions.values()}
        functions.update({body: fn for body, fn in mir.workspace.twins.values()})
        yield _Program(compiler, modules, {k.declaration: v for k, v in functions.items()}, mir.definitions,
                       mir.workspace, {v.body.declaration.split("@")[0] + ("#2" if "#2" in v.body.declaration
                                                                          else ""): v for v in verdicts})


@pytest.fixture(autouse=True)
def active(program):
    # The per-test state reset clears the stub facts latched onto the static TypeDefs.
    for module in program.modules:
        for record in module.ast.all_records():
            if record.builtin_type_key:
                latch_declared_native_flags(record.builtin_type_key, record)
    with activate_compiler(program.compiler):
        yield


def _definition(program: _Program, name: str) -> th.THIRFunction:
    owner, _, member = name.partition(".")
    return next(fn for key, (_, fn) in program.workspace.definitions.items()
                if key.name == member and key.owner is not None and key.owner.endswith(owner)
                and key.accessor in (None, "fget"))


def _summary(program: _Program, name: str):
    result = program.workspace.summaries[_definition(program, name).resolved_callee.identity]
    return result


def _known(program: _Program, name: str):
    result = _summary(program, name)
    assert result.state is MIRSummaryState.KNOWN, (name, result.reason)
    return result.summary


def _bag(program: _Program) -> NominalType:
    return _definition(program, "Bag.push").receiver.type


def _field(program: _Program, name: str, typ) -> th.THIRFieldIdentity:
    return th.THIRFieldIdentity(_bag(program), name, typ)


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _slot(fn: MIRFunction, name: str) -> MIRSlot:
    return next(s for s in fn.slots if s.name == name)


def _end_referents(fn: MIRFunction, name: str) -> frozenset[MIRReferent]:
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    block = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    return deps.referents[MIRPoint(block.id, len(block.statements))][MIRPlace(_slot(fn, name).id)]


# --- the callee side: a path into the parameter ------------------------------------

def test_a_container_field_returns_as_its_path(program: _Program) -> None:
    items = _field(program, "items", INTS)
    assert _known(program, "Bag.elems").returns == {MIRReturnOrigin(0, (items,))}
    assert _known(program, "Bag.view_items").returns == {MIRReturnOrigin(0, (items,))}
    more = _field(program, "more", INTS)
    assert _known(program, "Bag.other_items").returns == {MIRReturnOrigin(0, (more,))}


def test_a_view_of_an_owned_leaf_field_returns_as_its_path(program: _Program) -> None:
    name = _field(program, "name", next(f.type for f in _bag_fields(program) if f.name == "name"))
    summary = _known(program, "Bag.tag")
    assert summary.returns == {MIRReturnOrigin(0, (name,))}
    assert summary.borrowed_result.readonly


def _bag_fields(program: _Program) -> tuple[th.THIRFieldIdentity, ...]:
    return tuple(th.THIRFieldIdentity(_bag(program), f.id.name, f.type)
                 for f in program.definitions.get(None, _bag(program)).layout.fields)


def test_the_whole_receiver_returns_with_an_empty_path(program: _Program) -> None:
    assert _known(program, "Bag.me").returns == {MIRReturnOrigin(0)}


def test_unspellable_origins_refuse(program: _Program) -> None:
    # A Span over a field: the origin is a region under the field, which the grammar does not return.
    assert _summary(program, "Bag.span_items").state is MIRSummaryState.OPAQUE
    assert _summary(program, "Bag.span_items").reason == "summary unsupported return origin"
    elems = _known(program, "Bag.elems")
    items = _field(program, "items", INTS)
    n = _field(program, "n", INT32)
    for returns, reason in (
        # A container result needs the container: a scalar field, the whole record or a region refuse.
        ({MIRReturnOrigin(0, (n,))}, "unsupported return origin type or access"),
        ({MIRReturnOrigin(0)}, "unsupported return origin type or access"),
        ({MIRReturnOrigin(0, (items, MIRContainerElements()))}, "unsupported return origin type or access"),
        # Two fields are no path of the grammar.
        ({MIRReturnOrigin(0, (items, items))}, "invalid return parameter"),
        ({MIRReturnOrigin(1, (items,))}, "invalid return parameter"),
    ):
        assert summary_problem(replace(elems, returns=frozenset(returns))) == reason, returns
    # A record field returned as a record (an inline record result) is deferred with nested records.
    me = _known(program, "Bag.me")
    assert summary_problem(replace(me, returns=frozenset({MIRReturnOrigin(0, (n,))}))) == (
        "unsupported return origin type or access")


def test_a_return_path_names_a_field_of_the_parameter_record(program: _Program) -> None:
    elems = _known(program, "Bag.elems")
    stranger = NominalType("Other", _module_qname="main.Other")
    foreign = th.THIRFieldIdentity(stranger, "items", INTS)
    assert summary_problem(replace(elems, returns=frozenset({MIRReturnOrigin(0, (foreign,))}))) == (
        "unsupported return origin type or access")


# --- the caller side: the origin's place, resolved like a direct borrow -------------

def test_a_getter_result_has_the_direct_alias_referent(program: _Program) -> None:
    getter, direct = _lowered(program, "via_getter"), _lowered(program, "via_field")

    def shape(fn: MIRFunction, name: str) -> set[tuple]:
        slots = {s.id: s for s in fn.slots}
        return {(slots[r.place.root].name, r.place.projections, r.external) for r in _end_referents(fn, name)}
    referents = shape(getter, "xs")
    assert referents == shape(direct, "ys")
    (root, projections, external), = referents
    assert root == "b" and external and [p.id.name for p in projections] == ["items"]


def test_call_return_problem_checks_the_endpoint_type(program: _Program) -> None:
    fn = _lowered(program, "via_getter")
    stmt = next(s for b in fn.blocks for s in b.statements if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall))
    slots = {s.id: s for s in fn.slots}
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    point = next(MIRPoint(b.id, i) for b in fn.blocks for i, s in enumerate(b.statements) if s is stmt)
    state = deps.referents[point]
    holder = slots[stmt.target.root]
    assert call_return_problem(stmt.value, holder, state, slots) is None
    n = _field(program, "n", INT32)
    scalar = replace(stmt.value, summary=replace(stmt.value.summary, returns=frozenset({MIRReturnOrigin(0, (n,))})))
    assert call_return_problem(scalar, holder, state, slots) == "container result of a non-container place"
    whole = replace(stmt.value, summary=replace(stmt.value.summary, returns=frozenset({MIRReturnOrigin(0)})))
    assert call_return_problem(whole, holder, state, slots) == "container result of a non-container argument"


def _call_site(program: _Program, name: str) -> tuple[MIRAssign, MIRSlot, dict, dict]:
    """The first call assigned to a holder in `name`, its holder, and the referents before it."""
    fn = _lowered(program, name)
    slots = {s.id: s for s in fn.slots}
    point, stmt = next((MIRPoint(b.id, i), s) for b in fn.blocks for i, s in enumerate(b.statements)
                       if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) and s.value.summary.returns)
    state = analyze_dependencies(fn, analyze_liveness(fn)).referents[point]
    return stmt, slots[stmt.target.root], state, slots


def _returning(call: MIRCall, *path: th.THIRFieldIdentity | MIRContainerStructure | MIRContainerElements) -> MIRCall:
    return replace(call, summary=replace(call.summary, returns=frozenset({MIRReturnOrigin(0, path)})))


def test_call_return_problem_checks_record_and_view_endpoints(program: _Program) -> None:
    """Hand-built origins: `summary_problem` refuses each before any caller sees it."""
    items, n = _field(program, "items", INTS), _field(program, "n", INT32)
    view, holder, state, slots = _call_site(program, "view_across")
    assert call_return_problem(view.value, holder, state, slots) is None
    assert call_return_problem(_returning(view.value, items), holder, state, slots) == (
        "view result of a non-leaf place")
    assert call_return_problem(_returning(view.value, items, MIRContainerElements()), holder, state, slots) == (
        "call result of a container region")
    record, holder, state, slots = _call_site(program, "write_through_me")
    assert call_return_problem(record.value, holder, state, slots) is None
    assert call_return_problem(_returning(record.value, n), holder, state, slots) == (
        "call result of a mismatched place")


def test_a_return_field_outside_the_certified_layout_is_opaque(program: _Program) -> None:
    """Hand-built definitions: the body lowers against the layout it is summarized with."""
    identity = _definition(program, "Bag.elems").resolved_callee.identity
    body, fn = program.workspace.definitions[identity]
    lowered = program.workspace.bodies[body]
    assert summarize_function(fn, lowered, program.definitions).state is MIRSummaryState.KNOWN
    bag = program.definitions.get(None, _bag(program))
    fewer = replace(bag, layout=replace(bag.layout, fields=tuple(
        f for f in bag.layout.fields if f.id.name != "items")))
    definitions = object.__new__(MIRDefinitions)
    object.__setattr__(definitions, "records", {**program.definitions.records, _bag(program): fewer})
    assert summarize_function(fn, lowered, definitions).reason == "summary return field differs from definition"


def test_a_container_result_may_be_more_readonly_than_its_type(program: _Program) -> None:
    mutable, frozen = RefType(INTS), RefType(ReadonlyType(INTS))
    assert result_problem(mutable, th.THIRBorrowedRecord(INTS, False)) is None
    assert result_problem(mutable, th.THIRBorrowedRecord(INTS, True)) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(INTS, True)) is None
    assert result_problem(frozen, th.THIRBorrowedRecord(INTS, False)) == "invalid borrowed result"
    strs = NominalType("list", (STR,), _module_qname="builtins.list")
    assert result_problem(mutable, th.THIRBorrowedRecord(strs, False)) == "invalid borrowed result"


def test_a_whole_container_rooted_in_an_elements_region_is_refused() -> None:
    body = MIRBodyId("main", "region")
    layout = MIRContainerLayout(MIRTupleElement(INT32))
    xs = MIRSlot(MIRSlotId(body, 0), INTS, MIRSlotKind.PARAMETER, "xs", form=th.Form.BORROW,
                 value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=layout, passing=ParamPassing.MUT_REF)
    ys = MIRSlot(MIRSlotId(body, 1), INTS, MIRSlotKind.LOCAL, "ys", form=th.Form.BORROW,
                 value_kind=MIRValueKind.BORROWED_CONTAINER, container_layout=layout)
    signature = th.THIRCallableSignature((INTS,), INTS, None, (ParamPassing.MUT_REF,))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("main", "ident"), signature)
    summary = MIRCallSummary(callee, (MIRParameterBinding(INTS, ParamPassing.MUT_REF, False),), frozenset({0}),
                             frozenset(), frozenset(), frozenset({MIRReturnOrigin(0)}), frozenset(), True)
    call = MIRCall(summary, (xs.id,), False)
    slots = {xs.id: xs, ys.id: ys}
    whole = {MIRPlace(xs.id): frozenset({MIRReferent(MIRPlace(xs.id), True)})}
    assert call_return_problem(call, ys, whole, slots) is None
    region = {MIRPlace(xs.id): frozenset({MIRReferent(MIRPlace(xs.id, (MIRContainerElements(),)), True)})}
    assert call_return_problem(call, ys, region, slots) == "container result rooted in an elements region"


def test_a_span_argument_is_projected_once() -> None:
    # `u = ident(tail(xs))`: the Span argument already holds xs's elements
    # region; resolving the origin's place never projects it again.
    span = NominalType("Span", (INT32,), _module_qname="tpy.Span")
    body = MIRBodyId("main", "chain")
    layout = MIRContainerLayout(MIRTupleElement(INT32))
    t = MIRSlot(MIRSlotId(body, 0), span, MIRSlotKind.LOCAL, "t", form=th.Form.BORROW,
                value_kind=MIRValueKind.BORROWED, container_layout=layout)
    u = MIRSlot(MIRSlotId(body, 1), span, MIRSlotKind.LOCAL, "u", form=th.Form.BORROW,
                value_kind=MIRValueKind.BORROWED, container_layout=layout)
    signature = th.THIRCallableSignature((span,), span, None, (ParamPassing.VALUE,))
    callee = th.THIRResolvedCallee(th.THIRFunctionIdentity("main", "ident"), signature)
    xs_elements = MIRReferent(MIRPlace(MIRSlotId(MIRBodyId("main", "caller"), 0), (MIRContainerElements(),)), True)
    state = {MIRPlace(t.id): frozenset({xs_elements})}
    slots = {t.id: t, u.id: u}
    for path in ((), (MIRContainerElements(),)):
        summary = MIRCallSummary(callee, (MIRParameterBinding(span, ParamPassing.VALUE, True),), frozenset({0}),
                                 frozenset(), frozenset(), frozenset({MIRReturnOrigin(0, path)}), frozenset(), True)
        assert resolve_call_returns(MIRCall(summary, (t.id,), False), state, slots, u) == {xs_elements}


def test_dump_spells_return_paths(program: _Program) -> None:
    text = dump_function(_lowered(program, "via_getter"))
    assert "returns={param0.items}" in text
    assert "returns={param0}" in dump_function(_lowered(program, "read_readonly"))


def test_a_writing_call_is_no_container_place(program: _Program) -> None:
    # The builder reads a place's operands before its container; a call that writes could reorder them.
    assert _known(program, "Bag.grab").writes
    verdict = program.verdicts["iterate_grab"]
    assert (verdict.status, verdict.reason) == (MIRVerdictStatus.UNCOVERED, "order-sensitive eager operands")


# --- accessor and twin calls at the caller -------------------------------------------

def test_accessor_calls_lower_at_every_result_kind(program: _Program) -> None:
    for name in ("read_count", "read_label", "set_count", "iterate", "via_getter", "grow_alias"):
        verdict = program.verdicts[name]
        assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), (name, verdict.reason)
        assert not verdict.conflicts, name
    # A view of a field from a getter, live across the method replacing the field.
    assert program.verdicts["view_across"].conflicts == ("replacement",)


def test_a_readonly_receiver_binds_a_readonly_result(program: _Program) -> None:
    verdict = program.verdicts["read_readonly"]
    assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.reason
    fn = _lowered(program, "read_readonly")
    assert _slot(fn, "d").readonly
    call = next(s.value for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) and s.value.summary.returns)
    assert call.summary.result_at(True).readonly and not call.summary.result_at(False).readonly


def _free(program: _Program, name: str) -> th.THIRFunction:
    return next(fn for key, (_, fn) in program.workspace.definitions.items()
                if key.owner is None and key.name == name)


def test_a_follows_receiver_result_takes_the_receiver_access(program: _Program) -> None:
    signature = _definition(program, "Bag.view_items").resolved_callee.signature
    assert signature.result_follows_receiver
    assert bound_result(signature, True).readonly and not bound_result(signature, False).readonly
    # Calls at a readonly and at a mutable receiver publish the definition's one signature.
    me = _definition(program, "Bag.me").resolved_callee
    calls = [node for name in ("read_readonly", "write_through_me")
             for node in _thir_nodes(_free(program, name), th.THIRMethodCall) if node.method_cpp == "me"]
    assert len(calls) == 2 and all(call.resolved_callee == me for call in calls)


@pytest.mark.parametrize("name,receiver,readonly", [
    ("read_readonly", ParamPassing.CONST_REF, True),     # b: readonly[Bag]
    # b: Bag, inferred const (no `readonly[...]`): C++ picks the const clone. A
    # mutable local bound to the result would make `b` mutable, so the result
    # is walked in place.
    ("read_inferred", ParamPassing.CONST_REF, True),
    ("write_through_me", ParamPassing.MUT_REF, False),   # d.n = 5 writes through the mutable clone's result
])
def test_a_twin_result_is_bound_at_the_receiver_binding(program: _Program, name: str, receiver: ParamPassing,
                                                        readonly: bool) -> None:
    assert _free(program, name).resolved_callee.signature.passings[0] is receiver
    verdict = program.verdicts[name]
    assert verdict.status in (MIRVerdictStatus.COVERED, MIRVerdictStatus.CERTIFIED), verdict.reason
    _, holder, _, _ = _call_site(program, name)
    assert holder.readonly is readonly


@pytest.mark.parametrize("name", ["read_readonly", "read_inferred", "write_through_me"])
def test_a_receiver_access_unlike_its_binding_is_refused(program: _Program, name: str) -> None:
    fn = _free(program, name)
    body = MIRBodyId("main", name)
    assert isinstance(lower_function(fn, body, definitions=program.definitions,
                                     summaries=program.workspace.summaries), MIRFunction)
    calls = _thir_nodes(fn, th.THIRMethodCall)
    assert calls
    for call in calls:
        # The call's fact is the access C++ picks the overload by; a binding
        # MIR reads otherwise is no evidence of either, so it never widens.
        flipped = replace(call.receiver_access, readonly=not call.receiver_access.readonly)
        result = lower_function(_replace_node(fn, call, replace(call, receiver_access=flipped)), body,
                                definitions=program.definitions, summaries=program.workspace.summaries)
        assert isinstance(result, MIRNotCovered) and result.reason == "call receiver access disagrees with its binding"


# --- twins ---------------------------------------------------------------------------

def test_twin_bodies_summarize_alike(program: _Program) -> None:
    for name in ("Bag.elems", "Bag.view_items", "Bag.me"):
        identity = _definition(program, name).resolved_callee.identity
        assert identity in program.workspace.twins, name
        assert program.workspace.summaries[identity].state is MIRSummaryState.KNOWN, name


def test_a_differing_twin_makes_the_summary_opaque(program: _Program) -> None:
    # A hand-built twin of `view_items` whose body returns another field.
    definition = _definition(program, "Bag.view_items")
    identity = definition.resolved_callee.identity
    other = _definition(program, "Bag.other_items")
    fake = replace(other, resolved_callee=definition.resolved_callee, access_twin=True,
                   receiver=replace(other.receiver, readonly=False))
    functions = [(body, fn) for body, fn in program.workspace.definitions.values()]
    functions.insert(0, (MIRBodyId("main", "fake_twin"), fake))
    workspace = analyze_call_workspace(tuple(functions), program.definitions)
    assert workspace.summaries[identity] == workspace.summaries[identity].opaque("twin bodies differ")
    # The real twin agrees.
    functions[0] = program.workspace.twins[identity]
    workspace = analyze_call_workspace(tuple(functions), program.definitions)
    assert workspace.summaries[identity].state is MIRSummaryState.KNOWN


# --- stubs ---------------------------------------------------------------------------

def test_stub_summaries_keep_whole_argument_origins(program: _Program) -> None:
    fn = _lowered(program, "read_label")
    stubs = [s for s in fn.call_summaries if isinstance(s.callee, th.THIRStubCallee)]
    assert stubs
    for summary in stubs:
        assert summary == stub_summary(summary.callee) and summary_problem(summary) is None
        assert all(not origin.path for origin in summary.returns)
