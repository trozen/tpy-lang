"""MIR over records with struct bases: a layout carrying its ancestors'
fields keyed by their declaring owner, storage binding at an ancestor's type
at a call argument (`binds_at`), field membership in the storage's layout,
a base initializer as a call of the base's constructor initializing the
base's fields, the inherited constructor calling its base's, and the owner-qualified
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
from .definitions import owned_leaf_layout, MIRConstructorDefinition, MIRDefinitions
from .dump import dump_function
from .testutil import lower_constructor, lower_function
from .nodes import MIRAssign, MIRBodyId, MIRCall, MIRConstant, MIRCopy, MIRFunction, MIRMove, MIRNotCovered, MIRPlace
from .value import MIRField, MIRRecordLayout, MIRValidationError
from .prepare import validate_function

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
        result = _Program(
            compiler, {c.record_name: c for c in ctx.thir_constructors.values()},
            {i.record_layout.type.name: i for i in ctx.thir_inherited_constructors.values()},
            {fn.name: fn for _, fn in mir.workspace.definitions.values() if isinstance(fn, th.THIRFunction)},
            mir.definitions, mir.workspace.summaries,
            {v.body.declaration.split("@")[0]: v for v in verdicts})
    return result


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


def _lowered(program: _Program, name: str) -> MIRFunction:
    fn = program.verdicts[name].function
    assert isinstance(fn, MIRFunction), program.verdicts[name].reason
    return fn


def _base_init(ctor: th.THIRConstructor, *args: th.THIRExpr) -> th.THIRConstructor:
    return replace(ctor, base_inits=(replace(ctor.base_inits[0], args=args),))


def _built(ctor: th.THIRConstructor, definitions: MIRDefinitions) -> MIRFunction | MIRNotCovered:
    return lower_constructor(ctor, MIRBodyId("main", f"{ctor.record_name}.__init__"), definitions=definitions)


def _refusal(ctor: th.THIRConstructor, definitions: MIRDefinitions) -> str:
    built = _built(ctor, definitions)
    assert isinstance(built, MIRNotCovered), dump_function(built)
    return built.reason


def _base_calls(fn: MIRFunction) -> list[MIRAssign]:
    """The base initializers of a constructor body: calls initializing a base's fields."""
    return [s for b in fn.blocks for s in b.statements
            if isinstance(s, MIRAssign) and isinstance(s.value, MIRCall) and s.value.initializes is not None]


def _sources(fn: MIRFunction, call: MIRAssign) -> list[object]:
    """What each argument of `call` holds: the parameter it borrows, or the constant it is."""
    names = {s.id: s.name for s in fn.slots}
    values = {s.target.root: s.value for b in fn.blocks for s in b.statements
              if isinstance(s, MIRAssign) and not s.target.projections}
    found = []
    for arg in call.value.arguments:
        value = values.get(arg)
        source = getattr(value, "source", arg)
        found.append(value if isinstance(value, MIRConstant) else
                     names[source.root if isinstance(source, MIRPlace) else source])
    return found


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
    sub = _definition(program, "Sub").layout
    # The body walks its own fields by their declaration: it carries Sub's layout only to be forged here.
    # (its `str` member needs the string's opaque layout).
    fn = replace(fn, records=tuple(r for r in fn.records if r.type not in (sub.type, STR))
                 + (sub, owned_leaf_layout(STR)))
    validate_function(fn)
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


# --- base initializers -----------------------------------------------------------

def test_a_base_initializer_calls_the_base_constructor(program: _Program) -> None:
    def base_call(name: str) -> tuple[str, list[object], bool]:
        fn = _lowered(program, f"{name}.__init__")
        call, = _base_calls(fn)
        return call.value.initializes.name, _sources(fn, call), call.value.may_raise
    # A lent name passes through; the base's own copy of it may raise.
    assert base_call("Sub") == ("Base", ["name"], True)
    # A constant argument is the base parameter's value.
    assert base_call("Deep") == ("Sub", ["name", MIRConstant(1)], True)
    # CONST_REF -> CONST_REF lends the owned leaves the base copies.
    assert [p.passing.name for p in program.constructors["BigSub"].params] == ["CONST_REF", "CONST_REF"]
    assert base_call("BigSub") == ("Big", ["v", "t"], True)
    # An owned-leaf constant into the base's own copy is materialized in the
    # derived body and handed over; a caller constructs through it.
    assert base_call("Lit") == ("Holder", [MIRConstant("x")], False)
    assert isinstance(program.definitions.records[_record(program, "Lit")], MIRConstructorDefinition)


def _moved_into_holder(program: _Program) -> th.THIRConstructor:
    """`Lit` taking an `Own[str]` it moves into Holder's `Own[str]` parameter."""
    lit, holder = program.constructors["Lit"], program.constructors["Holder"]
    param = holder.params[0]
    name = th.THIRName(STR, "s")
    return _base_init(replace(lit, params=(param,)), th.THIRMove(STR, name))


def _argument_value(fn: MIRFunction, call: MIRAssign) -> object:
    return next(s.value for b in fn.blocks for s in b.statements
                if isinstance(s, MIRAssign) and s.target.root == call.value.arguments[0]
                and not s.target.projections)


def test_a_base_argument_moves_an_owned_parameter(program: _Program) -> None:
    fn = _built(_moved_into_holder(program), program.definitions)
    assert isinstance(fn, MIRFunction), fn.reason
    call, = _base_calls(fn)
    # The base's parameter takes the derived parameter's storage.
    moved = _argument_value(fn, call)
    assert isinstance(moved, MIRMove) and {s.id: s.name for s in fn.slots}[moved.source] == "s"
    # An owning argument whose base parameter feeds no member: the base's body drops it.
    drop = _record(program, "Drop")
    moved_ctor = _moved_into_holder(program)
    dropped = replace(moved_ctor, base_inits=(replace(moved_ctor.base_inits[0], base=drop),),
                      record_layout=replace(program.constructors["Drop"].record_layout,
                                            type=moved_ctor.record_layout.type, ancestors=(drop,)))
    fn = _built(dropped, program.definitions)
    assert isinstance(fn, MIRFunction), fn.reason
    call, = _base_calls(fn)
    assert call.value.initializes == drop and isinstance(_argument_value(fn, call), MIRMove)


def test_base_initializer_refusals(program: _Program) -> None:
    definitions = program.definitions
    moved = _moved_into_holder(program)
    # A bare owned name into an owning base parameter is a copy of it, as at any call.
    copied = _built(_base_init(moved, moved.base_inits[0].args[0].value), definitions)
    assert isinstance(copied, MIRFunction), copied.reason
    assert isinstance(_argument_value(copied, _base_calls(copied)[0]), MIRCopy)
    # A skipped base is value-initialized: its constructor never runs.
    assert program.constructors["Skip"].base_inits[0].args == ()
    assert program.verdicts["Skip.__init__"].reason == "base constructor not called"
    sub = program.constructors["Sub"]
    assert _refusal(replace(sub, base_inits=()), definitions) == "base constructor not called"
    # One base built twice (sema never emits it; the body refuses it anyway).
    assert _refusal(replace(sub, base_inits=(sub.base_inits[0], sub.base_inits[0])),
                    definitions) == "base constructor called twice"
    # A base whose constructor has no usable summary refuses the derived body's call.
    assert program.verdicts["EffectSub.__init__"].reason == "call needs finalized known summary"
    assert _refusal(sub, MIRDefinitions()) == "base definition: missing constructor definition"


# --- the inherited constructor -------------------------------------------------------

def test_an_inherited_constructor_is_the_base_definition_at_the_derived_type(program: _Program) -> None:
    dog, animal = _definition(program, "Dog"), _definition(program, "Animal")
    assert dog.layout.type == program.inherited["Dog"].record_layout.type
    assert dog.layout.ancestors == (animal.layout.type,)
    assert dog.callee == animal.callee and dog.initializes == animal.layout.type
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


# --- skipped and empty base calls ---------------------------------------------------

BASE_CALLS = """\
from tpy import int32


class Empty:
    def __init__(self) -> None:
        pass


class AfterEmpty(Empty):
    x: int32

    def __init__(self, x: int32) -> None:
        super().__init__()
        self.x = x


class Loud:
    def __init__(self, n: int32 = 7) -> None:
        print(n)


class SkipsLoud(Loud):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Quiet:
    def __init__(self, n: int32 = 7) -> None:
        pass


class SkipsQuiet(Quiet):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x
"""


def _base_call_verdicts() -> dict[str, MIRBodyVerdict]:
    compiler, modules = _compile(BASE_CALLS)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        return {v.body.declaration.split("@")[0]: v for v in enumerate_bodies(
            entry.ast, entry.analyzer, ctx, entry.name, mir.definitions, compiler.thir_reject_by_node,
            mir.workspace)}


def test_a_base_call_initializing_no_field_reads_nothing() -> None:
    # `super().__init__()` of a fieldless base before the own field: no whole read of the record.
    verdict = _base_call_verdicts()["AfterEmpty.__init__"]
    assert verdict.function is not None, verdict.reason
    assert verdict.summary.state is MIRSummaryState.KNOWN


def test_a_skipped_base_with_defaulted_parameters_is_its_call() -> None:
    verdicts = _base_call_verdicts()
    # C++ runs `Loud(7)`, which prints: the derived summary never drops it as normal-return.
    # The skipped base is a call of `Loud.__init__`, whose output effect leaves it no known summary.
    assert verdicts["SkipsLoud.__init__"].reason == "call needs finalized known summary"
    assert verdicts["Loud.__init__"].summary.reason == "summary output effect"
    # A call omitting a defaulted argument binds no value for it, as at any call.
    assert verdicts["SkipsQuiet.__init__"].reason == "incomplete constructor arguments"
