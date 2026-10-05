"""MIR over records with struct bases: a layout carrying its ancestors'
fields keyed by their declaring owner, storage binding at an ancestor's type
at a call argument (`binds_at`), field membership in the storage's layout,
the definitions chain composing each base constructor's verified definition
with its arguments, the inherited-constructor relabel, and the owner-qualified
spelling of a shadowed field."""

from dataclasses import dataclass, replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.test_method_stubs import _replace_node, nodes
from ..thir.testutil import _compile, _entry
from ..typesys import INT32, STR, NominalType
from .call_contract import MIRSummaryState, binds_at
from .collect import MIRBodyVerdict, enumerate_bodies, line_facts
from .coverage import MIRUnsupported
from .definitions import MIRConstructorDefinition, MIRDefinitions, _verify, constructor_initialization
from .lower import lower_function
from .nodes import (
    MIRBodyId, MIRConstant, MIRField, MIRFieldId, MIRFunction, MIRMemberInitMode, MIRNotCovered, MIRRecordLayout,
)
from .validate import MIRValidationError, validate_function

SOURCE = """\
from tpy import int32, Own, String


class Base:
    n: int32
    name: str

    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name

    def bump(self) -> None:
        self.n += 1


class Sub(Base):
    k: int32

    def __init__(self, name: str, k: int32) -> None:
        super().__init__(name)
        self.k = k

    def bump_twice(self) -> None:
        self.n += 2
        self.k += 1


class Deep(Sub):
    d: int32

    def __init__(self, name: str) -> None:
        super().__init__(name, 1)
        self.d = 3


class Other:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Big:
    v: int
    t: String

    def __init__(self, v: int, t: String) -> None:
        self.v = v
        self.t = t


class BigSub(Big):
    def __init__(self, v: int, t: String) -> None:
        super().__init__(v, t)


class Holder:
    s: str

    def __init__(self, s: Own[str]) -> None:
        self.s = s


class Lit(Holder):
    def __init__(self) -> None:
        super().__init__("x")


class Drop:
    n: int32

    def __init__(self, s: Own[str]) -> None:
        self.n = 1


class Skip(Base):
    k: int32

    def __init__(self) -> None:
        self.k = 1


class Effect:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        print(n)


class EffectSub(Effect):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Animal:
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs


class Dog(Animal):
    pass


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 1


class Shadow(Counter):
    n: int32

    def __init__(self) -> None:
        super().__init__()
        self.n = 100

    def own_bump(self) -> None:
        self.n += 1


def use_base(b: Base) -> int32:
    b.bump()
    return b.n


def through_base(s: Sub) -> int32:
    return use_base(s)


def read_base(b: Base) -> int32:
    return b.n


def through_read(s: Sub) -> int32:
    return read_base(s)


def use_shadow(sh: Shadow) -> int32:
    sh.bump()
    sh.own_bump()
    return sh.n


def make_dog() -> int32:
    d = Dog(4)
    return d.legs
"""


@dataclass(frozen=True)
class _Program:
    compiler: object
    constructors: dict[str, th.THIRConstructor]
    inherited: dict[str, th.THIRInheritedConstructor]
    functions: dict[str, th.THIRFunction]
    definitions: MIRDefinitions
    summaries: dict
    verdicts: dict[str, MIRBodyVerdict]


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        yield _Program(
            compiler, {c.record_name: c for c in ctx.thir_constructors.values()},
            {i.record_layout.type.name: i for i in ctx.thir_inherited_constructors.values()},
            {fn.name: fn for _, fn in mir.workspace.definitions.values()},
            mir.definitions, mir.workspace.summaries,
            {v.body.declaration.split("@")[0]: v for v in verdicts})


@pytest.fixture(autouse=True)
def active(program):
    with activate_compiler(program.compiler):
        yield


def _record(program: _Program, name: str) -> NominalType:
    return program.constructors[name].record_layout.type


def _definition(program: _Program, name: str) -> MIRConstructorDefinition:
    definition = program.definitions.records[_record(program, name) if name in program.constructors
                                             else program.inherited[name].record_layout.type]
    assert isinstance(definition, MIRConstructorDefinition), definition
    return definition


def _fields(definition: MIRConstructorDefinition) -> list[tuple[str, object, str, bool]]:
    return [(f"{i.field.id.owner.name}::{i.field.id.name}", i.source, i.mode.name, i.may_raise)
            for i in definition.initializers]


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _base_init(ctor: th.THIRConstructor, *args: th.THIRExpr) -> th.THIRConstructor:
    return replace(ctor, base_inits=(replace(ctor.base_inits[0], args=args),))


def _refusal(ctor: th.THIRConstructor, bases) -> str:
    try:
        constructor_initialization(ctor, bases)
    except MIRUnsupported as failure:
        return failure.reason
    raise AssertionError("composed")


# --- the binding rule ----------------------------------------------------------------

def test_binds_at_admits_the_type_and_its_ancestors_only() -> None:
    base, sub, other = (NominalType(n, _module_qname=f"m.{n}") for n in ("Base", "Sub", "Other"))
    records = {sub: MIRRecordLayout(sub, (), True, True, ancestors=(base,))}
    assert binds_at(records, sub, sub) and binds_at(records, sub, base)
    assert not binds_at(records, sub, other)
    # Ancestry is one way, and a type without a layout binds only at itself.
    assert not binds_at(records, base, sub) and binds_at(records, base, base)


# --- layouts -------------------------------------------------------------------------

def test_a_derived_layout_carries_its_ancestors_fields_in_construction_order(program: _Program) -> None:
    layout = _definition(program, "Deep").layout
    assert [f"{f.id.owner.name}::{f.id.name}" for f in layout.fields] == [
        "Base::n", "Base::name", "Sub::k", "Deep::d"]
    assert layout.ancestors == (_record(program, "Sub"), _record(program, "Base"))


def test_the_validator_checks_layout_owners_and_agreement(program: _Program) -> None:
    fn = _lowered(program, "Sub.bump_twice")
    validate_function(fn)
    sub = next(r for r in fn.records if r.type == _record(program, "Sub"))
    other = _record(program, "Other")

    def with_layout(layout: MIRRecordLayout) -> MIRFunction:
        return replace(fn, records=tuple(layout if r is sub else r for r in fn.records))

    # A field owned by a record that is no ancestor is no field of the layout.
    with pytest.raises(MIRValidationError, match="invalid record layout field"):
        validate_function(with_layout(replace(sub, ancestors=())))
    with pytest.raises(MIRValidationError, match="invalid record layout field"):
        validate_function(with_layout(replace(sub, ancestors=(other,))))
    with pytest.raises(MIRValidationError, match="invalid record layout ancestors"):
        validate_function(with_layout(replace(sub, ancestors=(sub.type,))))
    # One field identity is one storage of one type in every layout carrying it.
    base = MIRRecordLayout(_record(program, "Base"), (MIRField(sub.fields[0].id, STR),), True, True)
    with pytest.raises(MIRValidationError, match="inconsistent field type"):
        validate_function(replace(fn, records=(*fn.records, base)))


def test_an_inherited_field_is_a_member_of_the_storage_layout(program: _Program) -> None:
    fn = program.functions["bump_twice"]
    body = MIRBodyId("main", "bump_twice")
    assert isinstance(lower_function(fn, body, definitions=program.definitions,
                                     summaries=program.summaries), MIRFunction)
    store = fn.body[0]
    for owner in (_record(program, "Other"), _record(program, "Deep")):
        # Neither an unrelated record's field nor a descendant's is in Sub's layout.
        target = replace(store.target, field_identity=replace(store.target.field_identity, owner=owner))
        bad = replace(fn, body=(replace(store, target=target), *fn.body[1:]))
        result = lower_function(bad, body, definitions=program.definitions, summaries=program.summaries)
        assert isinstance(result, MIRNotCovered) and result.reason == "field owner mismatch"


# --- bodies --------------------------------------------------------------------------

def test_a_derived_method_publishes_writes_by_declaring_owner(program: _Program) -> None:
    summary = program.summaries[program.functions["bump_twice"].resolved_callee.identity]
    assert summary.state is MIRSummaryState.KNOWN, summary.reason
    assert {(w.parameter, w.path[0].owner.name, w.path[0].name) for w in summary.summary.writes} == {
        (0, "Base", "n"), (0, "Sub", "k")}


def test_storage_binds_at_an_ancestor_at_a_call(program: _Program) -> None:
    fn = _lowered(program, "through_base")
    validate_function(fn)
    sub = next(r for r in fn.records if r.type == _record(program, "Sub"))
    # Sub's storage bound at Base: refused once Base is no ancestor of the layout.
    own = replace(sub, fields=tuple(f for f in sub.fields if f.id.owner == sub.type), ancestors=())
    base = _definition(program, "Base").layout
    with pytest.raises(MIRValidationError, match="call argument type mismatch"):
        validate_function(replace(fn, records=(*(own if r is sub else r for r in fn.records), base)))


def test_lowering_refuses_storage_bound_at_a_non_ancestor(program: _Program) -> None:
    fn = program.functions["through_read"]
    assert isinstance(lower_function(fn, MIRBodyId("main", "through_read"), definitions=program.definitions,
                                     summaries=program.summaries), MIRFunction)
    other = _record(program, "Other")
    param = replace(fn.params[0], type=other, borrowed_record=th.THIRBorrowedRecord(other, False))
    arg, = nodes(fn, th.THIRCall)[0].args
    bad = _replace_node(replace(fn, params=(param,)), arg, replace(arg, result_type=other))
    result = lower_function(bad, MIRBodyId("main", "through_read"),
                            definitions=program.definitions, summaries=program.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "call record argument mismatch"


def test_a_shadowed_field_is_two_storages(program: _Program) -> None:
    layout = _definition(program, "Shadow").layout
    assert [f"{f.id.owner.name}::{f.id.name}" for f in layout.fields] == ["Counter::n", "Shadow::n"]
    summary = program.summaries[program.functions["use_shadow"].resolved_callee.identity]
    assert summary.state is MIRSummaryState.KNOWN, summary.reason
    assert {(w.path[0].owner.name, w.path[0].name) for w in summary.summary.writes} == {
        ("Counter", "n"), ("Shadow", "n")}


def test_a_shadowed_field_is_spelled_with_its_owner(program: _Program) -> None:
    facts = line_facts(program.verdicts["Shadow.own_bump"])
    assert [spelled for _line, spelled in facts.writes] == ["self.Shadow.n"]
    # The bare name is ambiguous: it names a field of each owner.
    assert facts.ambiguous == {"self.n": ("self.Counter.n", "self.Shadow.n")}
    plain = line_facts(program.verdicts["Sub.bump_twice"])
    assert sorted(spelled for _line, spelled in plain.writes) == ["self.k", "self.n"] and not plain.ambiguous


# --- the definitions chain -----------------------------------------------------------

def test_composition_per_leg(program: _Program) -> None:
    # A lent view (VIEW -> VIEW) keeps the base's copy; a scalar passes through.
    assert _fields(_definition(program, "Sub")) == [
        ("Base::n", MIRConstant(0), "SCALAR", False), ("Base::name", "name", "COPY", True),
        ("Sub::k", "k", "SCALAR", False)]
    # A constant leg takes the base member's mode.
    assert _fields(_definition(program, "Deep")) == [
        ("Base::n", MIRConstant(0), "SCALAR", False), ("Base::name", "name", "COPY", True),
        ("Sub::k", MIRConstant(1), "SCALAR", False), ("Deep::d", MIRConstant(3), "SCALAR", False)]
    # CONST_REF -> CONST_REF lends the owned leaves the base copies.
    assert [p.passing.name for p in program.constructors["BigSub"].params] == ["CONST_REF", "CONST_REF"]
    assert _fields(_definition(program, "BigSub")) == [("Big::v", "v", "COPY", False), ("Big::t", "t", "COPY", True)]
    # An owned-leaf constant into the base's own copy is materialized there:
    # the body composes it, a caller's construct has no operand for it.
    lit = constructor_initialization(program.constructors["Lit"], program.definitions.records)
    assert _fields(lit) == [("Holder::s", MIRConstant("x"), "COPY", True)]
    assert program.definitions.records[_record(program, "Lit")] == "constructor owned-leaf constant"


def _moved_into_holder(program: _Program) -> th.THIRConstructor:
    """`Lit` taking an `Own[str]` it moves into Holder's `Own[str]` parameter."""
    lit, holder = program.constructors["Lit"], program.constructors["Holder"]
    param = holder.params[0]
    name = th.THIRName(STR, "s")
    return _base_init(replace(lit, params=(param,)), th.THIRMove(STR, name))


def test_composition_of_moves(program: _Program) -> None:
    moved = _moved_into_holder(program)
    holder = _record(program, "Holder")
    # MOVE o MOVE: the member takes the caller's storage.
    assert _fields(constructor_initialization(moved, program.definitions.records)) == [
        ("Holder::s", "s", "MOVE", False)]
    # MOVE o COPY: the child's storage is moved into the base's own copy,
    # which the base copies on (and may raise); a caller cannot construct
    # through it.
    ctor = program.constructors["Holder"]
    copying = replace(ctor, mil_inits=(replace(ctor.mil_inits[0], value=ctor.mil_inits[0].value.value),))
    bases = {holder: constructor_initialization(copying)}
    assert _fields(constructor_initialization(moved, bases)) == [("Holder::s", "s", "MOVE", True)]
    with pytest.raises(MIRUnsupported) as refused:
        _verify(moved, bases)
    assert refused.value.reason == "constructor copies an owned parameter"


def test_composition_refusals(program: _Program) -> None:
    records = program.definitions.records
    moved = _moved_into_holder(program)
    # A bare name into an owning base parameter: a copy the leg does not model.
    assert _refusal(_base_init(moved, moved.base_inits[0].args[0].value), records) == (
        "base argument needs matching parameter or literal")
    # An expression argument.
    sub = program.constructors["Sub"]
    k = th.THIRName(INT32, "k")
    expression = th.THIRBinOp(INT32, k, "+", k, None)
    deep = program.constructors["Deep"]
    assert _refusal(_base_init(deep, deep.base_inits[0].args[0], expression),
                    records) == "base argument needs matching parameter or literal"
    # An owning leg whose base parameter feeds no member.
    drop = _record(program, "Drop")
    dropped = replace(moved, base_inits=(replace(moved.base_inits[0], base=drop),),
                      record_layout=replace(program.constructors["Drop"].record_layout,
                                            type=moved.record_layout.type, ancestors=(drop,)))
    assert _refusal(dropped, records) == "base argument effect not modeled"
    # A skipped base is value-initialized: its constructor never runs.
    assert program.constructors["Skip"].base_inits[0].args == ()
    assert records[_record(program, "Skip")] == "base constructor not called"
    assert _refusal(replace(sub, base_inits=()), records) == "base constructor not called"
    # One base built twice (sema never emits it; the chain refuses it anyway).
    assert _refusal(replace(sub, base_inits=(sub.base_inits[0], sub.base_inits[0])),
                    records) == "base constructor called twice"
    # A base whose definition is refused refuses the derived one.
    assert records[_record(program, "EffectSub")] == "base definition: constructor body effects"
    assert _refusal(sub, {}) == "base definition: missing constructor definition"


# --- the inherited constructor -------------------------------------------------------

def test_an_inherited_constructor_is_the_base_definition_at_the_derived_type(program: _Program) -> None:
    dog, animal = _definition(program, "Dog"), _definition(program, "Animal")
    assert dog.layout.type == program.inherited["Dog"].record_layout.type
    assert dog.layout.ancestors == (animal.layout.type,)
    assert dog.constructor is animal.constructor and dog.initializers == animal.initializers
    assert dog.layout.fields == animal.layout.fields
    # A caller constructs through it.
    assert program.verdicts["make_dog"].function is not None, program.verdicts["make_dog"].reason


@pytest.mark.parametrize("change", ["own field", "two bases", "special member"])
def test_inherited_constructor_shape_refusals(program: _Program, change: str) -> None:
    inherited = program.inherited["Dog"]
    layout = inherited.record_layout
    match change:
        case "own field":
            layout = replace(layout, fields=(*layout.fields, th.THIRFieldIdentity(layout.type, "tail", INT32)))
        case "two bases":
            layout = replace(layout, ancestors=(*layout.ancestors, _record(program, "Other")))
        case _:
            layout = replace(layout, custom_copy=True)
    definitions = MIRDefinitions(tuple(program.constructors.values()),
                                 inherited=(replace(inherited, record_layout=layout),))
    assert definitions.records[layout.type] == "inherited constructor shape"
