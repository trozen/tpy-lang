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
from ..typesys import BOOL, INT32, NominalType, OwnType, ReadonlyType, Representation, passing_representation
from .call_contract import (
    CONSTRUCTOR, MIRParameterBinding, MIRProtocolParameter, MIRReturnOrigin, MIRSummaryState,
    param_type, parameter_binding, parameter_contract_problem, signature_params, summary_problem,
)
from . import value as value_module
from .collect import MIRBodyVerdict, enumerate_bodies
from .definitions import MIRConstructorDefinition, MIRDefinitions
from .lower import lower_function
from .nodes import MIRBodyId, MIRFunction, MIRSlotKind
from .summaries import summarize_function
from .testutil import no_layouts, reaccess, prepared
from .value import Declared, Local, MIRHolding, MIRRole, Passing, loan_access, shape_problem, thir_member, value_shape

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

    @readonly
    def peek(self, p: P, n: int32) -> int32:
        return self.n + p.x + n


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


def maybe_read(p: P | None) -> int32:
    if p is None:
        return 0
    return p.x


def maybe_write(p: P | None) -> None:
    if p is not None:
        p.x = 1


def clear_readonly(xs: list[readonly[int32]]) -> None:
    xs.clear()


@readonly
def pick(p: P, q: P) -> int32:
    return p.x + q.x
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
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def program(compiled):
    compiler, modules = compiled
    with activate_compiler(compiler):
        entry = _entry(modules)
        _, ctx = compiler.generate_code_and_thir(entry)
        functions = {(f"{fn.receiver.type.name}." if fn.receiver is not None else "") + node.name: fn
                     for node, fn in ctx.thir_functions.items() if not fn.access_twin}
        with compiler.mir_analysis(((entry, ctx),)) as mir:
            verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                        compiler.thir_reject_by_node, mir.workspace)
            result = _Program(compiler, functions, mir.definitions, mir.workspace,
                           {v.body.declaration.split("@")[0]: v for v in verdicts})
    return result


@pytest.fixture
def active(program):
    with activate_compiler(program.compiler):
        yield program


def _param(program: _Program, name: str) -> th.THIRParam:
    return th.effective_params(program.functions[name])[0]


def _binding(program: _Program, name: str) -> MIRParameterBinding:
    binding = parameter_binding(_param(program, name))
    assert isinstance(binding, MIRParameterBinding), binding
    return binding


def _bound(param: th.THIRParam) -> MIRParameterBinding | str:
    """A body's parameter as the lowering binds it."""
    return parameter_binding(param)


# The contract's refusal of a binding of a well-formed value its inputs do not derive.
_DIFFERS = "call parameter binding differs from its derivation"


def _contract(binding: MIRParameterBinding) -> str | None:
    """A body's or a summary's binding against its contract."""
    return parameter_contract_problem(binding)


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
    assert binding.declared == signature_params(active.functions[name].resolved_callee.signature)[0]
    assert _contract(binding) is None


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
    # The binding is the one its declared type and passing derive.
    assert binding.declared == param_type(param) and _contract(binding) is None


@pytest.mark.parametrize("name", ["owned_record", "own_str", "owned_container"])
def test_an_object_handed_over_is_private_to_the_callee(active: _Program, name: str) -> None:
    # An `Own[R]` record, an `Own[str]` and an `Own[list[T]]` are the callee's alone.
    binding = _binding(active, name)
    assert binding.storage and binding.private_owned
    # A constructor's parameter is moved into the record the caller then owns.
    assert parameter_binding(_param(active, name), CONSTRUCTOR) == replace(binding, private_owned=False)


@pytest.mark.parametrize("name", ["record", "container", "grow"])
def test_a_lent_record_and_a_lent_container_bind_by_one_rule(active: _Program, name: str) -> None:
    param, binding = _param(active, name), _binding(active, name)
    assert not binding.storage and not binding.private_owned
    # The loan's access is its passing's, record and container alike; THIR's
    # per-kind fact is never read: a forged one, or none, binds the same.
    fact = "borrowed_record" if binding.cls is MIRClass.RECORD else "native_container"
    assert binding.readonly is (param.passing is ParamPassing.CONST_REF)
    for readonly in (False, True):
        assert parameter_binding(replace(param, **{fact: replace(getattr(param, fact), readonly=readonly)})) == binding
    assert parameter_binding(replace(param, **{fact: None})) == binding
    # Both are lent by reference only.
    assert parameter_binding(replace(param, passing=ParamPassing.VALUE)) == (
        "call parameter passing differs from its type")


def test_a_declared_readonly_container_binds_readonly(active: _Program) -> None:
    binding = _binding(active, "readonly_container")
    assert (binding.cls, binding.storage, binding.readonly) == (MIRClass.CONTAINER, False, True)
    assert binding.value.readonly


def test_a_mutable_fact_on_a_declared_readonly_container_is_not_read(active: _Program) -> None:
    # The declaration decides the access: a forged MUTABLE fact gives the
    # slot no writable loan, at the binding or at the body.
    fn = active.functions["readonly_container"]
    param = _param(active, "readonly_container")
    forged = replace(param, native_container=replace(param.native_container, readonly=False))
    assert parameter_binding(forged) == _binding(active, "readonly_container")
    forged_body = lower_function(replace(fn, params=(forged,)), MIRBodyId("binding", "forged"),
                                 definitions=active.definitions)
    body = lower_function(fn, MIRBodyId("binding", "forged"), definitions=active.definitions)
    assert forged_body == body


def test_a_stub_container_without_a_fact_binds_at_its_declared_access(active: _Program) -> None:
    param = replace(_param(active, "container"), native_container=None)
    for readonly in (False, True):
        lent = replace(param, passing=param.type.param_passing(readonly))
        binding = parameter_binding(lent, Declared(readonly))
        assert (binding.cls, binding.storage, binding.readonly) == (MIRClass.CONTAINER, False, readonly)
        # A loan is passed at its declared access: the declaration, not the passing, decides it.
        assert parameter_binding(lent, Declared(not readonly)) == "call parameter passing differs from its type"
    # Handed over, it is the callee's own storage whatever it declares.
    owned = parameter_binding(replace(_param(active, "owned_container"), native_container=None),
                              Declared(True))
    assert (owned.cls, owned.storage, owned.readonly, owned.private_owned) == (MIRClass.CONTAINER, True, False, True)


def test_an_optional_record_slot_is_mutable_while_its_payload_loan_is_readonly(active: _Program) -> None:
    binding = _binding(active, "maybe")
    assert binding.readonly and binding.lent_record == value_shape(binding.type.inner, MIRClass.RECORD, access=True,
                                                                   role=MIRRole.LOAN)
    slot, = (s for s in _body(active, "maybe").slots if s.kind is MIRSlotKind.PARAMETER)
    assert slot.shape.cls is MIRClass.OPTIONAL and not slot.readonly
    assert _summary(active, "maybe").parameters[0].readonly


def test_a_declared_readonly_record_binds_readonly(active: _Program) -> None:
    param = _param(active, "readonly_record")
    binding = _binding(active, "readonly_record")
    # The record fact's access and the declared `readonly[...]` are one access, the binding's.
    assert binding.readonly and binding.lent_record.readonly
    assert parameter_binding(replace(param, borrowed_record=replace(param.borrowed_record, readonly=False))) == binding


def test_a_span_of_readonly_elements_lends_them_readonly(active: _Program) -> None:
    binding = _binding(active, "span")
    assert binding.readonly and binding.value.container_view_holder
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


def test_a_stub_binds_at_its_declared_access(active: _Program) -> None:
    # A stub publishes no fact: its declaration is the binding's one access
    # input, also where its passing alone would give another.
    mut_span, span = _param(active, "mut_span"), _param(active, "span")
    wrapped = replace(_param(active, "readonly_container"), native_container=None)
    for readonly in (False, True):
        lent = parameter_binding(replace(mut_span, native_container=None),
                                 Declared(readonly))
        assert (lent.cls, lent.readonly) == (MIRClass.CONTAINER_VIEW, readonly)
        # Readonly elements are read only whatever the declaration says.
        assert parameter_binding(replace(span, native_container=None),
                                 Declared(readonly)).readonly
        # A `readonly[...]` container reads only at any declared access and passing.
        assert parameter_binding(wrapped, Declared(readonly)).readonly
    # A stub record binds by its type too: `readonly[R]` reads only.
    record = th.THIRParam("p", ReadonlyType(_binding(active, "record").type), passing=ParamPassing.CONST_REF)
    bound = parameter_binding(record, Declared(True))
    assert isinstance(bound, MIRParameterBinding), bound
    assert (bound.cls, bound.storage, bound.readonly) == (MIRClass.RECORD, False, True)


def test_a_forged_stub_access_differs_from_its_declaration(active: _Program) -> None:
    summary = _stub(active, "grow")
    assert summary_problem(summary, no_layouts()) is None
    # The stub's bindings are re-derived from its declaration, never from their passing alone.
    receiver = summary.parameters[0]
    for forged in (reaccess(receiver, not receiver.readonly),
                   reaccess(replace(receiver, passing=ParamPassing.CONST_REF), True)):
        damaged = replace(summary, parameters=(forged, *summary.parameters[1:]))
        assert summary_problem(damaged, no_layouts()) == "stub summary differs from its declaration"


# --- the access twin, constructors -----------------------------------------------------

def test_the_access_twin_rebinds_only_its_receiver_access(active: _Program) -> None:
    definition = active.functions["Shelf.me"]
    identity = definition.resolved_callee.identity
    body_id, twin = active.workspace.twins[identity]
    lowered = lower_function(twin, body_id, definitions=active.definitions, summaries=active.workspace.summaries)
    assert isinstance(lowered, MIRFunction)
    result = summarize_function(twin, lowered, prepared(lowered), active.definitions)
    assert result.state is MIRSummaryState.KNOWN, result.reason
    receiver = lowered.parameter_bindings[0]
    assert result.summary.parameters[0] == reaccess(replace(receiver, passing=ParamPassing.CONST_REF), True)
    assert result.summary.parameters == _summary(active, "Shelf.me").parameters


def test_a_constructor_definition_stores_its_bindings_and_an_inherited_one_reuses_them(active: _Program) -> None:
    box, sub = (active.definitions.records[t] for t in active.definitions.records if t.name in ("Box", "Sub"))
    assert isinstance(box, MIRConstructorDefinition) and isinstance(sub, MIRConstructorDefinition)
    assert [b.cls for b in box.bindings] == [MIRClass.RECORD, MIRClass.OWNED_LEAF, MIRClass.VIEW, MIRClass.LEAF]
    assert box.bindings == tuple(parameter_binding(p, CONSTRUCTOR) for p in box.constructor.params)
    assert sub.bindings is box.bindings


# --- refusals ------------------------------------------------------------------------

def test_each_refusal_names_its_reason(active: _Program) -> None:
    record, span, container = (_param(active, n) for n in ("record", "span", "container"))
    owned_container, maybe, pair, either = (_param(active, n)
                                            for n in ("owned_container", "maybe", "pair", "either"))
    view, lent = _param(active, "view"), _param(active, "lent_str")
    bump, mut_span = _param(active, "bump"), _param(active, "mut_span")
    ref = record.borrowed_record
    cases = [
        (replace(record, passing=None), "unpublished parameter passing"),
        (replace(container, tuple_layout=pair.tuple_layout), "conflicting layout facts"),
        (replace(pair, optional_layout=maybe.optional_layout), "conflicting layout facts"),
        (replace(span, passing=ParamPassing.CONST_REF), "call parameter passing differs from its type"),
        (replace(view, passing=ParamPassing.CONST_REF), "call parameter passing differs from its type"),
        (replace(container, passing=ParamPassing.VALUE), "call parameter passing differs from its type"),
        (replace(maybe, optional_layout="payload"), "missing or invalid layout fact"),
        (replace(maybe, passing=ParamPassing.VALUE), "call parameter passing differs from its type"),
        (replace(lent, passing=ParamPassing.MUT_REF), "call parameter passing differs from its type"),
        # An object handed over by reference.
        (replace(_param(active, "owned_record"), passing=ParamPassing.CONST_REF),
         "call parameter passing differs from its type"),
    ]
    # A per-kind fact is never read: forged, of another record or class, or
    # absent, it leaves the binding the type and the passing derive.
    shelf = active.functions["Shelf.me"].receiver
    ignored = [
        (bump, {"borrowed_record": replace(bump.borrowed_record, readonly=True)}),
        (mut_span, {"native_container": replace(mut_span.native_container, readonly=True)}),
        (span, {"native_container": replace(span.native_container, readonly=False)}),
        (span, {"native_container": container.native_container, "borrowed_record": ref}),
        (_param(active, "owned_record"), {"borrowed_record": th.THIRBorrowedRecord(ref.type, True)}),
        (owned_container, {"native_container": replace(owned_container.native_container, readonly=True)}),
        (either, {"borrowed_record": ref}),
        (maybe, {"borrowed_record": ref}),
        (container, {"native_container": "list"}),
        (record, {"borrowed_record": "P"}),
        (record, {"borrowed_record": th.THIRBorrowedRecord(INT32, True)}),
        (record, {"borrowed_record": shelf}),
        (record, {"borrowed_record": None}),
    ]
    for param, facts in ignored:
        assert _bound(replace(param, **facts)) == _bound(param), (param.name, facts)
    # A record MIR does not model -- no TypeDef registered for it here -- is refused by its type, as an
    # unmodeled container is (`admission_problem`).
    ghost = NominalType("Ghost", _module_qname="binding.Ghost")
    assert _bound(replace(record, type=ghost, borrowed_record=th.THIRBorrowedRecord(ghost, True))) == (
        "unmodeled record")
    for param, reason in cases:
        assert _bound(param) == reason, (param, reason)
    # A constructor moves only a record handed over into a member; a scalar
    # handed over is a container method's element.
    scalar = th.THIRParam("n", OwnType(INT32), passing=ParamPassing.VALUE)
    assert parameter_binding(scalar, CONSTRUCTOR) == "unsupported parameter type"
    assert parameter_binding(scalar, Declared(False)).private_owned


def test_a_binding_holding_another_container_is_refused(active: _Program) -> None:
    container, other = _param(active, "container"), _param(active, "grow")
    # `list[P]`'s fact on a declared `list[int32]` parameter is not read.
    wrong = replace(other.native_container, readonly=container.native_container.readonly)
    assert _bound(replace(container, native_container=wrong)) == _binding(active, "container")
    # A binding holding that container fails the contract check and the summary check alike.
    binding = _binding(active, "container")
    bad = replace(binding, value=value_shape(wrong.type, access=binding.readonly, role=MIRRole.LOAN))
    assert _contract(bad) == "reference type mismatch"
    assert summary_problem(replace(_summary(active, "container"), parameters=(bad,)), no_layouts()) == (
        "reference type mismatch")


@pytest.mark.parametrize("fact", [True, False])
def test_an_owned_container_is_handed_over_at_own_only(active: _Program, fact: bool) -> None:
    param = _param(active, "owned_container")
    if not fact:
        param = replace(param, native_container=None)
    assert isinstance(parameter_binding(param), MIRParameterBinding)
    # By value it would be a copy the caller makes, not the object it hands
    # over: `Own[list[T]]` is passed at OWN only.
    assert parameter_binding(replace(param, passing=ParamPassing.VALUE)) == (
        "call parameter passing differs from its type")


def test_a_lent_record_or_container_is_never_the_callees_storage(active: _Program) -> None:
    record, container = _param(active, "record"), _param(active, "container")
    for param in (replace(record, passing=ParamPassing.VALUE), replace(record, passing=ParamPassing.TRAIT),
                  replace(container, passing=ParamPassing.OWN)):
        assert parameter_binding(param) == "call parameter passing differs from its type", param


def test_an_owned_wrapper_is_handed_over_when_its_layout_is_published(active: _Program) -> None:
    own_pair, own_either, own_maybe = (_param(active, n) for n in ("own_pair", "own_either", "own_maybe"))
    # THIR publishes no layout for an `Own[...]` tuple, union or Optional, so the bodies do not lower.
    for name, param in (("own_pair", own_pair), ("own_either", own_either), ("own_maybe", own_maybe)):
        assert parameter_binding(param) == "unsupported parameter type"
        assert active.verdicts[name].reason == "unsupported parameter type"
    # A wrapper's layout lays out only the wrapper itself, never one handed over.
    for param, reason in ((replace(own_pair, tuple_layout=_param(active, "pair").tuple_layout), "value class mismatch"),
                          (replace(own_either, union_layout=_param(active, "either").union_layout),
                           "unsupported union layout"),
                          (replace(own_maybe, optional_layout=_param(active, "maybe_scalar").optional_layout),
                           "value class mismatch")):
        assert parameter_binding(param) == reason, param


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
        (replace(scalar, passing=ParamPassing.CONST_REF), "call parameter passing differs from its type"),
        # Sema strips `readonly[...]` off a scalar parameter; a wrapped one is no inert leaf.
        (replace(scalar, type=ReadonlyType(INT32)), "unsupported parameter type"),
    ]
    for param, reason in cases:
        assert parameter_binding(param) == reason, (param, reason)
    readonly_scalar = _binding(active, "readonly_scalar")
    assert (readonly_scalar.type, readonly_scalar.cls, readonly_scalar.scalar_value) == (INT32, MIRClass.LEAF, True)


def test_a_container_thir_declined_a_fact_for_derives_from_its_type(active: _Program) -> None:
    # THIR publishes no fact for `list[readonly[int32]]`: the binding is
    # derived from the type and the passing, as for `list[int32]`.
    param = _param(active, "clear_readonly")
    assert param.native_container is None
    binding = _binding(active, "clear_readonly")
    assert (binding.cls, binding.storage, binding.readonly) == (MIRClass.CONTAINER, False, False)
    assert _body(active, "clear_readonly").parameter_bindings == (binding,)


def test_a_forged_container_element_is_not_read(active: _Program) -> None:
    # The members are the type's, whatever element a fact names.
    ints, records = _param(active, "container"), _param(active, "grow")
    point = records.native_container.element
    shelf = th.THIRBorrowedRecord(active.functions["Shelf.me"].receiver.type, False)
    cases = [
        (ints, th.THIRBorrowedRecord(point.type, False), "unsupported native container element"),
        (ints, BOOL, "unsupported native container element"),
        (records, shelf, "unsupported native container element"),
        (records, replace(point, readonly=not records.native_container.readonly), "unsupported native container element"),
        # A record element is pointed at, never stored in the fact inline.
        (records, th.THIROwnedRecord(point.type, records.native_container.readonly),
         "unsupported native container element"),
    ]
    for param, element, _ in cases:
        forged = replace(param, native_container=replace(param.native_container, element=element))
        assert _bound(forged) == _bound(param), (param.name, element)
    # The inline element differs from THIR's only in its holding: that is what refuses it.
    inline = thir_member(th.THIROwnedRecord(point.type, point.readonly))
    assert (inline.type, inline.cls, inline.readonly, inline.is_pointer) == (
        point.type, MIRClass.RECORD, point.readonly, False) and thir_member(point).is_pointer


# --- the contract --------------------------------------------------------------------

def test_the_contract_refuses_each_forged_value(active: _Program) -> None:
    lent_str, view, pair, either, record, readonly_record, container, owned_record = (
        _binding(active, n) for n in ("lent_str", "view", "pair", "either", "record", "readonly_record",
                                      "container", "owned_record"))
    cases = [
        # A lent owned leaf and a view only read; a tuple's and a union's own slot is never readonly.
        (reaccess(lent_str, False), "mutable loan of a readonly value"),
        (reaccess(view, False), "mutable loan of a readonly value"),
        (reaccess(pair, True), "readonly wrapper slot"),
        (reaccess(either, True), "readonly wrapper slot"),
        # A `readonly[R]` loan is never mutable.
        (reaccess(readonly_record, False), "mutable loan of a readonly value"),
        # A record or container lent at CONST_REF is a loan, never held by value.
        (replace(record, value=replace(record.value, holding=MIRHolding.VALUE)), "readonly owned storage"),
        (replace(container, value=replace(container.value, holding=MIRHolding.VALUE)), "readonly owned storage"),
        # An object handed over is private to a function, a loan never is.
        (replace(owned_record, private_owned=False), _DIFFERS),
        (replace(record, private_owned=True), _DIFFERS),
        # A record handed over is held by value, at no passing but its own.
        (replace(owned_record, value=replace(owned_record.value, holding=MIRHolding.POINTER)),
         "reference type mismatch"),
        (replace(owned_record, passing=ParamPassing.CONST_REF), "call parameter passing differs from its type"),
    ]
    for forged, reason in cases:
        assert _contract(forged) == reason, (forged, reason)
    # A tuple or union lent by reference is held by value.
    assert pair.passing is ParamPassing.CONST_REF and pair.value.holding is MIRHolding.VALUE and _contract(pair) is None
    # A constructor's definition keeps what it moves in: private to no one.
    assert parameter_contract_problem(owned_record, CONSTRUCTOR) == _DIFFERS
    assert parameter_contract_problem(replace(owned_record, private_owned=False), CONSTRUCTOR) is None
    # A stub's protocol parameter only reads.
    protocol, = _stub(active, "container").parameters
    assert parameter_contract_problem(protocol, Declared(True)) is None
    assert parameter_contract_problem(replace(protocol, value=MIRProtocolParameter(False)), Declared(True)) == _DIFFERS


def test_the_contract_refuses_a_malformed_binding(active: _Program) -> None:
    record, maybe, container = _binding(active, "record"), _binding(active, "maybe"), _binding(active, "container")
    protocol, = _stub(active, "container").parameters
    for forged in (replace(record, private_owned=0), replace(record, value=replace(record.value, readonly=1)),
                   replace(record, value="P"), replace(protocol, value=MIRProtocolParameter(1))):
        assert _contract(forged) == "malformed call parameter binding", forged
    # A member's access is a bool too, though equality takes 1 for True.
    element = replace(container.value.element, readonly=int(container.value.element.readonly))
    assert _contract(replace(container, value=replace(container.value, members=(element,)))) == (
        "invalid value access")
    # A value of another class than its declared type, a protocol parameter's included.
    assert _contract(replace(record, value=MIRProtocolParameter(True))) == "value class mismatch"
    assert parameter_contract_problem(replace(protocol, value=record.value), Declared(True)) == "value class mismatch"
    assert _contract(replace(maybe, value=record.value)) == "value class mismatch"


def test_a_forged_payload_access_is_refused_at_the_contract_and_the_summary(active: _Program) -> None:
    # The access only the binding carries is checked as the value's form: a
    # bool, never more mutable than declared.
    binding = _binding(active, "maybe_read")
    summary = _summary(active, "maybe_read")
    payload = binding.value.payload
    for access in (1, False):
        forged = replace(binding, value=replace(binding.value, members=(None, replace(payload, readonly=access))))
        if access is False:
            # `P | None` lends its payload mutably where the body writes through it.
            assert _contract(forged) is None
            continue
        assert _contract(forged) == "invalid value access"
        assert summary_problem(replace(summary, parameters=(forged,)), no_layouts()) == "invalid value access"
    declared = _binding(active, "maybe")
    mutable = replace(declared, value=replace(declared.value, members=(None, replace(declared.value.payload,
                                                                               readonly=False))))
    assert _contract(mutable) == "optional layout increases access"


def test_a_malformed_wrapper_layout_is_refused(active: _Program) -> None:
    pair, either = _param(active, "pair"), _param(active, "either")
    assert parameter_binding(replace(pair, tuple_layout=th.THIRTupleLayout(pair.tuple_layout.elements[:1]))) == (
        "tuple layout arity")
    assert parameter_binding(replace(either, union_layout=th.THIRUnionLayout(either.union_layout.type, (INT32,)))) == (
        "unsupported union layout")


def test_a_container_view_without_a_fact_derives_from_its_type(active: _Program) -> None:
    span = replace(_param(active, "span"), native_container=None)
    assert _bound(span) == _binding(active, "span")


def test_a_summary_binding_declares_the_signature_type(active: _Program) -> None:
    summary = _summary(active, "record")
    binding, = summary.parameters
    assert summary_problem(summary, no_layouts()) is None
    # The declared type is the contract's input: one other than the signature's does not pass.
    other = _param(active, "readonly_record").type
    assert summary_problem(replace(summary, parameters=(replace(binding, declared=other),)), no_layouts()) == (
        "call parameter binding declares another type")


@pytest.mark.parametrize("name", ["pick", "Shelf.peek"])
def test_a_body_binds_each_parameter_at_the_type_its_callers_bind(active: _Program, name: str) -> None:
    # An explicit `@readonly` body reads `readonly[P]` where its callers
    # bind the signature's `P`; a method's receiver binds first.
    fn = active.functions[name]
    types = signature_params(fn.resolved_callee.signature)
    offset = len(types) - len(fn.params)
    assert offset == (fn.receiver is not None)
    assert isinstance(fn.params[0].type, ReadonlyType) and types[offset] != fn.params[0].type
    bindings = _body(active, name).parameter_bindings
    assert tuple(b.declared for b in bindings) == types
    assert bindings[offset].readonly and bindings[offset].passing is ParamPassing.CONST_REF
    assert _summary(active, name).parameters == bindings


def test_a_summary_refuses_a_body_binding_its_facts_do_not_derive(active: _Program) -> None:
    # A payload made mutable in both the slot and its binding passes the
    # contract (`P | None` may lend mutably); only THIR's published access
    # tells the body read it.
    body = _body(active, "maybe_read")
    binding, = body.parameter_bindings
    payload = binding.value.payload
    assert payload.readonly
    forged = replace(binding, value=replace(binding.value, members=(None, replace(payload, readonly=False))))
    assert _contract(forged) is None
    slots = tuple(replace(s, shape=forged.value) if s.kind is MIRSlotKind.PARAMETER else s for s in body.slots)
    damaged = replace(body, slots=slots, parameter_bindings=(forged,))
    result = summarize_function(active.functions["maybe_read"], damaged, prepared(damaged), active.definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary parameter binding mismatch"


def test_the_shape_check_refuses_a_non_bool_access(active: _Program) -> None:
    record, maybe = _binding(active, "record"), _binding(active, "maybe")
    representation = passing_representation(record.passing)
    assert shape_problem(record.value, record.declared, representation=representation) is None
    assert shape_problem(replace(record.value, readonly=1), record.declared,
                         representation=representation) == "invalid value access"
    # A member's access, as the slot's.
    payload = maybe.value.payload
    assert payload.readonly is True and shape_problem(maybe.value, maybe.declared) is None
    assert shape_problem(replace(maybe.value, members=(None, replace(payload, readonly=1))), maybe.declared) == (
        "invalid value access")


def test_a_body_refuses_what_only_a_stub_binds(active: _Program) -> None:
    owned_record = _param(active, "owned_record")
    # A record handed over binds the caller's temporary at OWN only (its
    # type's passing), as exactly `Own[R]`.
    assert _bound(replace(owned_record, passing=ParamPassing.VALUE)) == "call parameter passing differs from its type"
    assert _bound(replace(owned_record, type=ReadonlyType(owned_record.type))) == "unsupported parameter type"
    # A scalar element handed over, a protocol parameter: a container method's or a stub's.
    assert _bound(th.THIRParam("n", OwnType(INT32), passing=ParamPassing.VALUE)) == "unsupported parameter type"
    protocol, = _stub(active, "container").callee.signature.param_types
    assert _bound(th.THIRParam("x", protocol, passing=ParamPassing.CONST_REF)) == "unsupported parameter type"


def test_the_contract_refuses_what_only_a_stub_binds(active: _Program) -> None:
    # Without a stub's declared access a binding is a function's, which
    # takes neither a protocol parameter nor a scalar element handed over.
    protocol, = _stub(active, "container").parameters
    element = parameter_binding(th.THIRParam("n", OwnType(INT32), passing=ParamPassing.VALUE),
                                Declared(False))
    assert element.private_owned
    for binding, readonly, reason in ((protocol, True, "unsupported parameter type"),
                                      (element, False, "unsupported parameter type")):
        assert parameter_contract_problem(binding, Declared(readonly)) is None
        assert _contract(binding) == reason, binding


def test_a_wrapper_layout_is_checked_at_its_passing(active: _Program, monkeypatch: pytest.MonkeyPatch) -> None:
    # A tuple lent at CONST_REF is held by value; its class is checked at
    # the reference its passing gives it, not at its holding's storage.
    pair = _param(active, "pair")
    assert pair.passing is ParamPassing.CONST_REF and isinstance(parameter_binding(pair),
                                                                    MIRParameterBinding)
    real = value_module.value_class

    def classed(typ: object, representation: Representation = Representation.STORAGE) -> MIRClass | None:
        return None if typ == pair.type and representation is Representation.REFERENCE else real(typ, representation)
    monkeypatch.setattr(value_module, "value_class", classed)
    assert parameter_binding(pair) == "value class mismatch"


@pytest.mark.parametrize("name", ["pick", "Shelf.peek"])
def test_a_body_its_signature_does_not_declare_binds_at_its_own_types(active: _Program, name: str) -> None:
    # Without a signature of its types the body binds as declared (a
    # method's receiver first) and has no summary.
    fn = active.functions[name]
    params = th.effective_params(fn)
    damaged = replace(fn, resolved_callee=replace(fn.resolved_callee, signature=replace(
        fn.resolved_callee.signature, param_types=(BOOL,) * len(params))))
    body = lower_function(damaged, MIRBodyId("binding", "unsigned"), definitions=active.definitions)
    assert isinstance(body, MIRFunction), body
    assert tuple(b.declared for b in body.parameter_bindings) == tuple(map(param_type, params))
    result = summarize_function(damaged, body, prepared(body), active.definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary definition signature mismatch"


def test_a_summary_refuses_a_declaration_passing_its_body_did_not_bind(active: _Program) -> None:
    # The body bound `p` at the passing THIR published; a declaration
    # passing it at the other record passing is another binding.
    fn = active.functions["record"]
    binding, = _body(active, "record").parameter_bindings
    other = ParamPassing.MUT_REF if binding.passing is ParamPassing.CONST_REF else ParamPassing.CONST_REF
    damaged = replace(fn, params=(replace(fn.params[0], passing=other),))
    body = _body(active, "record")
    result = summarize_function(damaged, body, prepared(body), active.definitions)
    assert result.state is MIRSummaryState.OPAQUE and result.reason == "summary parameter binding mismatch"


def test_a_fact_of_another_class_conflicts(active: _Program) -> None:
    record, pair, either = _param(active, "record"), _param(active, "pair"), _param(active, "either")
    bump, maybe_scalar = _param(active, "bump"), _param(active, "maybe_scalar")
    protocol, = _stub(active, "container").callee.signature.param_types
    cases = [
        (th.THIRParam("x", protocol, tuple_layout=pair.tuple_layout, passing=ParamPassing.CONST_REF),
         "conflicting layout facts"),
        # A union layout is of its declared union, checked before the class.
        (replace(record, borrowed_record=None, union_layout=either.union_layout), "unsupported union layout"),
        # A record or wrapper carrying another class's one layout.
        (replace(record, borrowed_record=None, tuple_layout=pair.tuple_layout), "conflicting layout facts"),
        (replace(pair, tuple_layout=None, optional_layout=maybe_scalar.optional_layout), "conflicting layout facts"),
        # A fact sits in its class's field: a tuple layout is no Optional's, a
        # record fact no tuple's, a union layout no tuple's.
        (replace(pair, tuple_layout=None, optional_layout=pair.tuple_layout), "missing or invalid layout fact"),
        (replace(record, borrowed_record=None, tuple_layout=record.borrowed_record), "missing or invalid layout fact"),
        (replace(either, union_layout=None, tuple_layout=either.union_layout), "missing or invalid layout fact"),
        # A readonly record fact in another field is no way past the access check.
        (replace(bump, borrowed_record=None, union_layout=replace(bump.borrowed_record, readonly=True)),
         "missing or invalid layout fact"),
    ]
    for param, reason in cases:
        assert _bound(param) == reason, param


def test_access_follows_its_named_source(active: _Program) -> None:
    # A lent `str` reads only: its class's own access.
    assert _binding(active, "lent_str").readonly
    # A record lent at MUT_REF is mutable; declared `readonly[R]` it reads only at the same passing.
    bump = _param(active, "bump")
    assert bump.passing is ParamPassing.MUT_REF and not _binding(active, "bump").readonly
    declared = _param(active, "readonly_record")
    assert loan_access(declared.type, MIRClass.RECORD, Passing(ParamPassing.MUT_REF))
    # The binding never passes it so: `readonly[R]` is passed at CONST_REF only.
    assert parameter_binding(replace(declared, passing=ParamPassing.MUT_REF)) == (
        "call parameter passing differs from its type")
    # An Optional record payload's access is the one THIR published: read-only where the body reads it.
    assert _binding(active, "maybe_read").readonly and not _binding(active, "maybe_write").readonly
    assert _param(active, "maybe_read").passing is _param(active, "maybe_write").passing is ParamPassing.POINTER
    # A local bound const, bound over a readonly source (a loop variable
    # over a readonly container), or a payload alias, reads only.
    point = _param(active, "bump").borrowed_record
    assert loan_access(point.type, MIRClass.RECORD, Local(const=True))
    assert loan_access(point.type, MIRClass.RECORD, Local(source_readonly=True))
    assert not loan_access(point.type, MIRClass.RECORD, Local())
    # A declared `readonly[R]` local reads only whatever its inputs.
    assert loan_access(ReadonlyType(point.type), MIRClass.RECORD, Local())
    assert loan_access(INT32, MIRClass.LEAF, Local(alias=True)) and not loan_access(INT32, MIRClass.LEAF, Local())
