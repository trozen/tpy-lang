"""The one parameter binding (`call_contract.parameter_binding`): a body's
parameter slots, its summary, a stub's declaration and a constructor's
definition all bind a parameter by the class, holding and loan access it
derives from the `THIRParam` alone."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import INT32, OwnType, ReadonlyType
from .call_contract import (
    MIRParameterBinding, MIRReturnOrigin, MIRSummaryState, binding_param, parameter_binding,
    parameter_binding_problem, summary_problem,
)
from .collect import MIRBodyVerdict, enumerate_bodies
from .definitions import MIRConstructorDefinition, MIRDefinitions
from .lower import lower_function
from .nodes import MIRFunction, MIRSlotKind
from .summaries import summarize_function

SOURCE = '''\
from tpy import int32, auto_readonly, readonly, Own, Span, StrView
from tpy.extern import native


class P:
    def __init__(self, x: int32) -> None:
        self.x = x


class Box:
    def __init__(self, p: P, name: str, tag: StrView, n: int32) -> None:
        self.p = p
        self.name = name
        self.tag = tag
        self.n = n


class Sub(Box):
    pass


class Shelf:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @auto_readonly
    def me(self) -> "Shelf":
        return self


@native("probe_view", transient=True)
def probe_view(s: StrView) -> StrView: ...


def scalar(n: int32) -> int32:
    return n


def lent_str(s: str) -> int32:
    return len(s)


def own_str(s: Own[str]) -> int32:
    return len(s)


def view(v: StrView) -> StrView:
    return probe_view(v)


def record(p: P) -> int32:
    return p.x


def readonly_record(p: readonly[P]) -> int32:
    return p.x


def owned_record(p: Own[P]) -> int32:
    return p.x


def container(xs: list[int32]) -> int32:
    return len(xs)


def owned_container(xs: Own[list[int32]]) -> int32:
    return len(xs)


def readonly_container(xs: readonly[list[int32]]) -> int32:
    return len(xs)


def span(xs: Span[readonly[int32]]) -> int32:
    return len(xs)


def mut_span(xs: Span[int32]) -> None:
    xs[0] = 1


def fill(xs: list[int32]) -> None:
    xs.append(1)


def bump(p: P) -> None:
    p.x += 1


def pair(t: tuple[int32, int32]) -> int32:
    return t[0]


def maybe(p: readonly[P] | None) -> int32:
    if p is None:
        return 0
    return p.x


def maybe_scalar(n: int32 | None) -> int32:
    if n is None:
        return 0
    return n


def either(u: int32 | bool) -> int32:
    return 0


def grow(xs: list[P]) -> None:
    xs.append(P(1))


def own_pair(t: Own[tuple[int32, int32]]) -> int32:
    return 0


def own_either(u: Own[int32 | bool]) -> int32:
    return 0


def own_maybe(n: Own[int32 | None]) -> int32:
    return 0


def readonly_scalar(n: readonly[int32]) -> int32:
    return n
'''


@dataclass(frozen=True)
class _Program:
    compiler: object
    # THIR bodies by `Owner.name` for a method (the definition, not its access twin), `name` for a function.
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
                 for node, fn in ctx.thir_functions.items() if not fn.access_twin}
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(compiler, functions, mir.definitions, mir.workspace,
                       {v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.fixture
def active(program):
    with activate_compiler(program.compiler):
        yield program


def _param(program: _Program, name: str) -> th.THIRParam:
    return th.effective_params(program.functions[name])[0]


def _binding(program: _Program, name: str) -> MIRParameterBinding:
    binding = parameter_binding(_param(program, name), constructor=False)
    assert isinstance(binding, MIRParameterBinding), binding
    return binding


def _body(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _summary(program: _Program, name: str):
    result = program.workspace.summaries[program.functions[name].resolved_callee.identity]
    assert result.state is MIRSummaryState.KNOWN, result.reason
    return result.summary


# --- one binding per class ---------------------------------------------------------

@pytest.mark.parametrize(("name", "cls", "storage", "readonly"), [
    ("scalar", MIRClass.LEAF, True, False),
    # A borrowed owned leaf only reads the caller's storage; an owned one is the callee's own.
    ("lent_str", MIRClass.OWNED_LEAF, False, True),
    ("own_str", MIRClass.OWNED_LEAF, True, False),
    # A view's loan is read only.
    ("view", MIRClass.VIEW, False, True),
    ("record", MIRClass.RECORD, False, True),
    ("owned_record", MIRClass.RECORD, True, False),
    ("container", MIRClass.CONTAINER, False, True),
    ("owned_container", MIRClass.CONTAINER, True, False),
    ("span", MIRClass.CONTAINER_VIEW, False, True),
    ("pair", MIRClass.TUPLE, False, False),
    ("maybe", MIRClass.OPTIONAL, False, True),
    ("maybe_scalar", MIRClass.OPTIONAL, True, False),
    ("either", MIRClass.UNION, False, False),
])
def test_each_class_binds_from_its_thir_param(active: _Program, name: str, cls: MIRClass, storage: bool,
                                              readonly: bool) -> None:
    binding = _binding(active, name)
    assert (binding.cls, binding.storage, binding.readonly) == (cls, storage, readonly)
    # The body records the binding it built the slot from, and its summary publishes it.
    assert _body(active, name).parameter_bindings == (binding,)
    if cls not in (MIRClass.TUPLE, MIRClass.UNION):
        # A wrapper parameter's body has no summary yet ("summary storage or value shape").
        assert _summary(active, name).parameters == (binding,)
    assert parameter_binding_problem(active.functions[name].resolved_callee.signature.param_types[0], binding) is None


@pytest.mark.parametrize(("name", "passing", "cls", "storage", "readonly", "private_owned"), [
    # A scalar leaf by value is the callee's value.
    ("scalar", ParamPassing.VALUE, MIRClass.LEAF, True, False, False),
    # A borrowed owned leaf, a leaf view and a readonly region view are readonly loans.
    ("lent_str", ParamPassing.VIEW, MIRClass.OWNED_LEAF, False, True, False),
    ("view", ParamPassing.VALUE, MIRClass.VIEW, False, True, False),
    ("span", ParamPassing.VALUE, MIRClass.CONTAINER_VIEW, False, True, False),
    # A region view passed by value is still a loan, mutable when its elements are.
    ("mut_span", ParamPassing.VALUE, MIRClass.CONTAINER_VIEW, False, False, False),
    # A record or container by reference is a loan at its fact's access.
    ("container", ParamPassing.CONST_REF, MIRClass.CONTAINER, False, True, False),
    ("fill", ParamPassing.MUT_REF, MIRClass.CONTAINER, False, False, False),
    ("record", ParamPassing.CONST_REF, MIRClass.RECORD, False, True, False),
    ("bump", ParamPassing.MUT_REF, MIRClass.RECORD, False, False, False),
    # An object handed over is private owned storage.
    ("own_str", ParamPassing.VALUE, MIRClass.OWNED_LEAF, True, False, True),
    ("owned_record", ParamPassing.OWN, MIRClass.RECORD, True, False, True),
    ("owned_container", ParamPassing.OWN, MIRClass.CONTAINER, True, False, True),
    # A tuple or union keeps its members' access in its layout; a lent Optional record lends its payload's.
    ("pair", ParamPassing.CONST_REF, MIRClass.TUPLE, False, False, False),
    ("either", ParamPassing.CONST_REF, MIRClass.UNION, False, False, False),
    ("maybe", ParamPassing.POINTER, MIRClass.OPTIONAL, False, True, False),
    # A scalar Optional by value is the callee's value.
    ("maybe_scalar", ParamPassing.VALUE, MIRClass.OPTIONAL, True, False, False),
])
def test_holding_and_access_follow_class_and_passing(active: _Program, name: str, passing: ParamPassing,
                                                     cls: MIRClass, storage: bool, readonly: bool,
                                                     private_owned: bool) -> None:
    param = _param(active, name)
    binding = _binding(active, name)
    assert (binding.passing, binding.cls, binding.storage, binding.readonly, binding.private_owned) == (
        passing, cls, storage, readonly, private_owned)
    # The binding's parameter derives the binding back.
    assert parameter_binding(binding_param(param.type, binding), constructor=False,
                             declared_readonly=binding.readonly) == binding


@pytest.mark.parametrize("name", ["owned_record", "own_str", "owned_container"])
def test_an_object_handed_over_is_private_to_the_callee(active: _Program, name: str) -> None:
    # An `Own[R]` record, an `Own[str]` and an `Own[list[T]]` are the callee's alone.
    binding = _binding(active, name)
    assert binding.storage and binding.private_owned
    # A constructor's parameter is moved into the record the caller then owns.
    assert parameter_binding(_param(active, name), constructor=True) == replace(binding, private_owned=False)


@pytest.mark.parametrize("name", ["record", "container", "grow"])
def test_a_lent_record_and_a_lent_container_bind_by_one_rule(active: _Program, name: str) -> None:
    param, binding = _param(active, name), _binding(active, name)
    assert not binding.storage and not binding.private_owned
    # The loan's access is its fact's, record and container alike.
    fact = "borrowed_record" if binding.cls is MIRClass.RECORD else "native_container"
    for readonly in (False, True):
        lent = replace(param, **{fact: replace(getattr(param, fact), readonly=readonly)})
        assert parameter_binding(lent, constructor=False).readonly is readonly
    # Both are lent by reference only.
    assert parameter_binding(replace(param, passing=ParamPassing.VALUE), constructor=False) == (
        "unsupported reference parameter passing")


def test_a_declared_readonly_container_binds_readonly(active: _Program) -> None:
    binding = _binding(active, "readonly_container")
    assert (binding.cls, binding.storage, binding.readonly) == (MIRClass.CONTAINER, False, True)
    assert binding.native_container.readonly


def test_a_stub_container_without_a_fact_binds_at_its_declared_access(active: _Program) -> None:
    param = replace(_param(active, "container"), native_container=None)
    for readonly in (False, True):
        binding = parameter_binding(param, constructor=False, declared_readonly=readonly)
        assert (binding.cls, binding.storage, binding.readonly, binding.native_container) == (
            MIRClass.CONTAINER, False, readonly, None)
    # Handed over, it is the callee's own storage whatever it declares.
    owned = parameter_binding(replace(_param(active, "owned_container"), native_container=None),
                              constructor=False, declared_readonly=True)
    assert (owned.cls, owned.storage, owned.readonly, owned.private_owned) == (MIRClass.CONTAINER, True, False, True)


def test_an_optional_record_slot_is_mutable_while_its_payload_loan_is_readonly(active: _Program) -> None:
    binding = _binding(active, "maybe")
    assert binding.readonly and binding.borrowed_record == th.THIRBorrowedRecord(binding.type.inner, True)
    slot, = (s for s in _body(active, "maybe").slots if s.kind is MIRSlotKind.PARAMETER)
    assert slot.shape.cls is MIRClass.OPTIONAL and not slot.readonly
    assert _summary(active, "maybe").parameters[0].readonly


def test_a_declared_readonly_record_binds_readonly(active: _Program) -> None:
    param = _param(active, "readonly_record")
    binding = _binding(active, "readonly_record")
    # The record fact's access and the declared `readonly[...]` are one access, the binding's.
    assert binding.readonly and binding.borrowed_record.readonly
    assert parameter_binding(replace(param, borrowed_record=replace(param.borrowed_record, readonly=False)),
                             constructor=False) == binding


def test_a_span_of_readonly_elements_lends_them_readonly(active: _Program) -> None:
    binding = _binding(active, "span")
    assert binding.readonly and binding.native_container is not None
    slot, = (s for s in _body(active, "span").slots if s.kind is MIRSlotKind.PARAMETER)
    assert slot.readonly


# --- stubs ---------------------------------------------------------------------------

def _stub(program: _Program, name: str):
    stubs = [s for s in _body(program, name).call_summaries if isinstance(s.callee, th.THIRStubCallee)]
    assert len(stubs) == 1, stubs
    return stubs[0]


def test_a_stub_protocol_parameter_has_no_class(active: _Program) -> None:
    binding, = _stub(active, "container").parameters
    assert binding.protocol and binding.cls is None and binding.readonly and not binding.storage


def test_an_element_a_container_method_takes_is_private_to_it(active: _Program) -> None:
    receiver, element = _stub(active, "grow").parameters
    assert (receiver.cls, receiver.storage, receiver.readonly) == (MIRClass.CONTAINER, False, False)
    assert (element.cls, element.storage, element.private_owned) == (MIRClass.RECORD, True, True)


def test_a_view_stub_parameter_lends_its_loan_whatever_it_declares(active: _Program) -> None:
    summary = _stub(active, "view")
    # THIR publishes no readonly declaration for the transient stub's view.
    assert summary.callee.readonly == (False,)
    binding, = summary.parameters
    assert binding.cls is MIRClass.VIEW and binding.readonly
    # So the view result borrows the lent argument.
    assert summary.returns == frozenset({MIRReturnOrigin(0)})


# --- the access twin, constructors -----------------------------------------------------

def test_the_access_twin_rebinds_only_its_receiver_access(active: _Program) -> None:
    definition = active.functions["Shelf.me"]
    identity = definition.resolved_callee.identity
    body_id, twin = active.workspace.twins[identity]
    lowered = lower_function(twin, body_id, definitions=active.definitions, summaries=active.workspace.summaries)
    assert isinstance(lowered, MIRFunction)
    result = summarize_function(twin, lowered, active.definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    receiver = lowered.parameter_bindings[0]
    assert result.summary.parameters[0] == replace(
        receiver, passing=ParamPassing.CONST_REF, readonly=True,
        borrowed_record=th.THIRBorrowedRecord(receiver.type, True))
    assert result.summary.parameters == _summary(active, "Shelf.me").parameters


def test_a_constructor_definition_stores_its_bindings_and_an_inherited_one_reuses_them(active: _Program) -> None:
    box, sub = (active.definitions.records[t] for t in active.definitions.records if t.name in ("Box", "Sub"))
    assert isinstance(box, MIRConstructorDefinition) and isinstance(sub, MIRConstructorDefinition)
    assert [b.cls for b in box.bindings] == [MIRClass.RECORD, MIRClass.OWNED_LEAF, MIRClass.VIEW, MIRClass.LEAF]
    assert box.bindings == tuple(parameter_binding(p, constructor=True) for p in box.constructor.params)
    assert sub.bindings is box.bindings


# --- refusals ------------------------------------------------------------------------

def test_each_refusal_names_its_reason(active: _Program) -> None:
    record, span, container = (_param(active, n) for n in ("record", "span", "container"))
    owned_container, maybe, pair, either = (_param(active, n)
                                            for n in ("owned_container", "maybe", "pair", "either"))
    view, lent = _param(active, "view"), _param(active, "lent_str")
    ref = record.borrowed_record
    cases = [
        (replace(record, passing=None), "unpublished parameter passing"),
        (replace(span, borrowed_record=ref), "conflicting parameter facts"),
        (replace(container, tuple_layout=pair.tuple_layout), "conflicting parameter facts"),
        (replace(pair, optional_layout=maybe.optional_layout), "conflicting parameter facts"),
        (replace(either, borrowed_record=ref), "conflicting parameter facts"),
        (replace(maybe, borrowed_record=ref), "conflicting parameter facts"),
        (replace(span, passing=ParamPassing.CONST_REF), "unsupported view parameter passing"),
        (replace(view, passing=ParamPassing.CONST_REF), "unsupported view parameter passing"),
        (replace(span, native_container=container.native_container), "invalid native container fact"),
        (replace(container, native_container="list"), "invalid native container fact"),
        (replace(owned_container, native_container=replace(owned_container.native_container, readonly=True)),
         "readonly owned container parameter"),
        (replace(container, passing=ParamPassing.VALUE), "unsupported reference parameter passing"),
        (replace(maybe, optional_layout="payload"), "unsupported optional layout"),
        (replace(maybe, passing=ParamPassing.VALUE), "unsupported optional parameter passing"),
        (replace(record, borrowed_record="P"), "invalid reference fact"),
        (replace(record, borrowed_record=th.THIRBorrowedRecord(INT32, True)), "unsupported reference fact"),
        (replace(record, borrowed_record=active.functions["Shelf.me"].receiver), "reference type mismatch"),
        (replace(lent, passing=ParamPassing.MUT_REF), "unsupported owned-leaf parameter passing"),
        # A record with no fact, an object handed over by reference.
        (replace(record, borrowed_record=None), "unsupported parameter type"),
        (replace(_param(active, "owned_record"), passing=ParamPassing.CONST_REF), "unsupported parameter type"),
    ]
    for param, reason in cases:
        assert parameter_binding(param, constructor=False) == reason, (param, reason)
    # A constructor moves only a record handed over into a member.
    scalar = th.THIRParam("n", OwnType(INT32), passing=ParamPassing.VALUE)
    assert parameter_binding(scalar, constructor=True) == "unsupported parameter type"
    assert parameter_binding(scalar, constructor=False).private_owned


def test_a_container_fact_of_another_container_is_refused(active: _Program) -> None:
    container, other = _param(active, "container"), _param(active, "grow")
    # `list[P]`'s fact on a declared `list[int32]` parameter, at the parameter's own access.
    wrong = replace(other.native_container, readonly=container.native_container.readonly)
    assert parameter_binding(replace(container, native_container=wrong), constructor=False) == (
        "invalid native container fact")
    # A binding carrying it fails the validator's binding check and the summary check alike.
    bad = replace(_binding(active, "container"), type=wrong.type, native_container=wrong)
    typ = active.functions["container"].resolved_callee.signature.param_types[0]
    assert parameter_binding_problem(typ, bad) == "unsupported container call parameter"
    assert summary_problem(replace(_summary(active, "container"), parameters=(bad,))) == (
        "unsupported container call parameter")


@pytest.mark.parametrize("constructor", [False, True])
@pytest.mark.parametrize("fact", [True, False])
def test_an_owned_container_is_handed_over_at_own_only(active: _Program, constructor: bool, fact: bool) -> None:
    param = _param(active, "owned_container")
    if not fact:
        param = replace(param, native_container=None)
    assert isinstance(parameter_binding(param, constructor=constructor), MIRParameterBinding)
    # By value it would be a copy the caller makes, not the object it hands over.
    assert parameter_binding(replace(param, passing=ParamPassing.VALUE), constructor=constructor) == (
        "unsupported container parameter passing")


def test_a_lent_record_or_container_is_never_the_callees_storage(active: _Program) -> None:
    record, container = _param(active, "record"), _param(active, "container")
    for param in (replace(record, passing=ParamPassing.VALUE), replace(record, passing=ParamPassing.TRAIT),
                  replace(container, passing=ParamPassing.OWN)):
        assert parameter_binding(param, constructor=False) == "unsupported reference parameter passing", param


def test_an_owned_wrapper_is_handed_over_when_its_layout_is_published(active: _Program) -> None:
    own_pair, own_either, own_maybe = (_param(active, n) for n in ("own_pair", "own_either", "own_maybe"))
    # THIR publishes no layout for an `Own[...]` tuple, union or Optional, so the bodies do not lower.
    for name, param in (("own_pair", own_pair), ("own_either", own_either), ("own_maybe", own_maybe)):
        assert parameter_binding(param, constructor=False) == "unsupported parameter type"
        assert active.verdicts[name].reason == "unsupported parameter type"
    # With one, a tuple or union handed over is private to the callee.
    for param in (replace(own_pair, tuple_layout=_param(active, "pair").tuple_layout),
                  replace(own_either, union_layout=_param(active, "either").union_layout)):
        binding = parameter_binding(param, constructor=False)
        assert (binding.storage, binding.readonly, binding.private_owned) == (True, False, True)
    # An Optional's payload layout lays out only the Optional itself, never one handed over.
    assert parameter_binding(replace(own_maybe, optional_layout=_param(active, "maybe_scalar").optional_layout),
                             constructor=False) == "unsupported optional layout"


def test_a_binding_without_its_holding_is_refused(active: _Program) -> None:
    view, span, scalar, pair = (_param(active, n) for n in ("view", "span", "scalar", "pair"))
    either, maybe = _param(active, "either"), _param(active, "maybe")
    cases = [
        # A view handed over: a view never owns what it views.
        (th.THIRParam("v", OwnType(view.type), passing=ParamPassing.VALUE), "unsupported parameter type"),
        (th.THIRParam("xs", OwnType(span.type), native_container=span.native_container,
                      passing=ParamPassing.VALUE), "unsupported parameter type"),
        # A tuple, union or Optional without its layout.
        (replace(pair, tuple_layout=None), "unsupported parameter type"),
        (replace(either, union_layout=None), "unsupported parameter type"),
        (replace(maybe, optional_layout=None), "unsupported parameter type"),
        # A scalar leaf is the callee's value only by value.
        (replace(scalar, passing=ParamPassing.CONST_REF), "unsupported parameter type"),
        # Sema strips `readonly[...]` off a scalar parameter; a wrapped one is no inert leaf.
        (replace(scalar, type=ReadonlyType(INT32)), "unsupported parameter type"),
    ]
    for param, reason in cases:
        assert parameter_binding(param, constructor=False) == reason, (param, reason)
    readonly_scalar = _binding(active, "readonly_scalar")
    assert (readonly_scalar.type, readonly_scalar.cls, readonly_scalar.scalar_value) == (INT32, MIRClass.LEAF, True)


def test_a_fact_of_another_class_conflicts(active: _Program) -> None:
    record, pair, either = _param(active, "record"), _param(active, "pair"), _param(active, "either")
    protocol, = _stub(active, "container").callee.signature.param_types
    cases = [
        th.THIRParam("x", protocol, tuple_layout=pair.tuple_layout, passing=ParamPassing.CONST_REF),
        replace(record, borrowed_record=None, union_layout=either.union_layout),
    ]
    for param in cases:
        assert parameter_binding(param, constructor=False) == "conflicting parameter facts", param
