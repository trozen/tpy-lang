"""Constructor entry initializes the supplied receiver before aliases observe it."""

from dataclasses import replace
from collections.abc import Callable

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, STR, STRVIEW, NominalType
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRBodyId, MIRBorrow, MIRCall, MIRConstant, MIRConstruct, MIRCopy, MIRFunction, MIRMemberInit,
    MIRMemberInits, MIRMove, MIRNotCovered, MIROp, MIRPlace, MIRRead, MIRSlotId, MIRSlotKind, MIRWrap,
)
from .shapes import MIRDeref
from .validate import value_may_raise
from .value import MIRFieldId, MIRValidationError, value_shape
from .testutil import bind_parameters, compiled_definitions, Reference, execute, no_layouts, reslot
from .prepare import validate_function


SOURCE = """\
from tpy import int32
class Cell:
    value: int32
    before: int32
    changed: bool
    def __init__(self, value: int32, reset: bool):
        self.value = value
        self.before = 0
        self.changed = False
        direct = self
        single = (self,)
        saved = (self, self.value)
        if reset:
            direct.value = 5
            single[0].value = 6
            saved[0].value = 7
        self.before = saved[1]
        self.changed = reset
class Plain:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Early:
    ready: bool
    def __init__(self, ready: bool):
        self.ready = ready
        if ready:
            return
        while not self.ready:
            self.ready = True
class Empty:
    def __init__(self):
        pass
def create(value: int32) -> int32:
    cell = Cell(value, True)
    return cell.value
"""


Artifacts = tuple[dict[str, th.THIRConstructor], dict[str, th.THIRFunction]]


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled) -> Artifacts:
    compiler, modules = compiled
    with activate_compiler(compiler):
        emitted, ctx = compiler.generate_code_and_thir(_entry(modules))
        assert emitted
        return ({ctor.record_name: ctor for ctor in ctx.thir_constructors.values()},
                {node.name: fn for node, fn in ctx.thir_functions.items()})


def lower(ctor: th.THIRConstructor) -> MIRFunction:
    result = lower_constructor(ctor, MIRBodyId("constructors", ctor.record_name))
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("reset", [False, True])
def test_initialization_precedes_alias_and_scalar_capture(artifacts: Artifacts, reset: bool) -> None:
    constructors, _ = artifacts
    fn = lower(constructors["Cell"])
    init = fn.receiver_init
    assert init is not None and init.receiver == fn.slots[0].id
    assert tuple(member.value for member in init.members.fields) == (
        MIRRead(MIRPlace(fn.slots[1].id)), MIRConstant(0), MIRConstant(False))
    heap = {41: {}, 42: {}}
    assert execute(fn, Reference(41), 3, reset, heap=heap) is None
    typ = fn.slots[0].type
    assert heap[41] == {MIRFieldId(typ, "value"): 7 if reset else 3,
                        MIRFieldId(typ, "before"): 3, MIRFieldId(typ, "changed"): reset}
    assert heap[42] == {}
    assert "initialize-receiver %0 (read %1, 0, False)" in dump_function(fn)


@pytest.mark.parametrize("ready", [False, True])
def test_entry_runs_once_before_early_return_or_loop(artifacts: Artifacts, ready: bool) -> None:
    fn = lower(artifacts[0]["Early"])
    heap = {}
    execute(fn, Reference(8), ready, heap=heap)
    assert heap[8] == {MIRFieldId(fn.slots[0].type, "ready"): True}


def test_empty_record_and_nonmovable_receiver(artifacts: Artifacts) -> None:
    empty = lower(artifacts[0]["Empty"])
    heap = {}
    execute(empty, Reference(1), heap=heap)
    assert heap == {1: {}}
    ctor = artifacts[0]["Plain"]
    fn = lower(replace(ctor, record_layout=replace(ctor.record_layout, movable=False)))
    execute(fn, Reference(2), 9, heap=heap)
    assert heap[2] == {MIRFieldId(fn.slots[0].type, "value"): 9}


def test_body_coverage_does_not_relax_constructor_call_summary(artifacts: Artifacts) -> None:
    constructors, functions = artifacts
    lower(constructors["Cell"])
    result = lower_function(functions["create"], MIRBodyId("constructors", "create"),
                            definitions=MIRDefinitions(tuple(constructors.values())))
    assert isinstance(result, MIRNotCovered) and result.reason == "constructor body effects"


@pytest.mark.parametrize("change,reason", [
    (lambda c: replace(c, mil_inits=c.mil_inits[:-1]), "incomplete constructor"),
    (lambda c: replace(c, mil_inits=c.mil_inits * 2), "duplicate constructor field"),
    (lambda c: replace(c, mil_inits=(replace(c.mil_inits[0], move=True),)), "move"),
    (lambda c: replace(c, mil_inits=(replace(c.mil_inits[0], field_identity=None),)), "field identity"),
    (lambda c: replace(c, record_layout=None), "missing record layout"),
    (lambda c: replace(c, params=(replace(c.params[0], type=BOOL),)), "needs parameter"),
    (lambda c: replace(c, base_inits=(th.THIRBaseInit("Base", (), NominalType("Base")),)),
     "base constructor identity"),
])
def test_incomplete_or_unsupported_initialization_fails_closed(
    artifacts: Artifacts, change: Callable[[th.THIRConstructor], th.THIRConstructor], reason: str,
) -> None:
    result = lower_constructor(change(artifacts[0]["Plain"]), MIRBodyId("constructors", "bad"))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_constructor_parameter_needs_published_passing(artifacts: Artifacts) -> None:
    ctor = artifacts[0]["Plain"]
    bad = replace(ctor, params=(replace(ctor.params[0], passing=None),))
    assert MIRDefinitions((bad,)).records[ctor.record_layout.type] == "unpublished parameter passing"


@pytest.mark.parametrize("fields", [(), (MIRConstant(True),), (MIRConstant(2**31),),
                                    (MIRSlotId(MIRBodyId("other", "body"), 0),)])
def test_malformed_entry_values_are_rejected(
    artifacts: Artifacts, fields: tuple[MIRSlotId | MIRConstant, ...],
) -> None:
    fn = lower(artifacts[0]["Plain"])
    with pytest.raises(MIRValidationError):
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init, members=MIRMemberInits(tuple(
            MIRMemberInit(s if isinstance(s, MIRConstant) else MIRRead(MIRPlace(s))) for s in fields)))))


def test_entry_cannot_read_a_local_or_initialize_readonly_receiver(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["Cell"])
    local = next(s for s in fn.slots if s.kind is MIRSlotKind.TEMPORARY and s.type == INT32)
    with pytest.raises(MIRValidationError, match="initializer parameter"):
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init, members=MIRMemberInits((
            MIRMemberInit(MIRRead(MIRPlace(local.id))), MIRMemberInit(MIRConstant(0)),
            MIRMemberInit(MIRConstant(False)))))))
    with pytest.raises(MIRValidationError, match="constructor receiver"):
        validate_function(bind_parameters(replace(fn, slots=(reslot(fn.slots[0], readonly=True), *fn.slots[1:]))))
    # Initializing another parameter is not building the receiver.
    with pytest.raises(MIRValidationError, match="invalid constructor receiver"):
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init, receiver=fn.slots[1].id)))
    with pytest.raises(MIRValidationError, match="constructor entry mismatch"):
        validate_function(replace(fn, receiver_init=None))


def test_the_built_receiver_is_parameter_zero(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["Cell"])
    receiver = next(iter(fn.slot_bindings))
    slots = {s.id: s for s in fn.slots}
    assert fn.receiver_init.receiver == receiver and slots[receiver].name == "self"
    # Another mutable parameter of the record's own type is no receiver the
    # entry may build, though it binds as the receiver does.
    other = replace(slots[receiver], id=MIRSlotId(fn.id, max(s.id.index for s in fn.slots) + 1),
                    name="other")
    widened = bind_parameters(replace(fn, slots=(*fn.slots, other)))
    assert widened.slot_bindings[other.id] == widened.slot_bindings[receiver]
    validate_function(widened)
    with pytest.raises(MIRValidationError, match="invalid constructor receiver"):
        validate_function(replace(widened, receiver_init=replace(widened.receiver_init, receiver=other.id)))
    # A body with no parameter has no receiver to build.
    bare = replace(fn, slots=tuple(s for s in fn.slots if s.kind is not MIRSlotKind.PARAMETER),
                   parameter_bindings=(), receiver_init=replace(fn.receiver_init, receiver=None))
    with pytest.raises(MIRValidationError, match="invalid constructor receiver"):
        validate_function(bare)
    with pytest.raises(MIRValidationError, match="invalid slot kind"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], kind="parameter"), *fn.slots[1:])))


@pytest.mark.parametrize("fields,body,reason", [
    ("value: int32 = 4", "pass", "incomplete constructor"),
    ("value: int32", "saved = value\n        self.value = saved", "incomplete constructor"),
    ("value: int32", "self.value = value\n        print((value, value))", "print argument needs a scalar leaf"),
    ("value: int32", "self.value = value + 1", "parameter or literal"),
])
def test_actual_emitted_exclusions(fields: str, body: str, reason: str) -> None:
    source = f"from tpy import int32\nclass Record:\n    {fields}\n    def __init__(self, value: int32):\n        {body}\n"
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
        result = lower_constructor(ctor, MIRBodyId("constructors", "excluded"))
        assert isinstance(result, MIRNotCovered) and reason in result.reason


@pytest.mark.parametrize("field,param,body,reason", [
    ("value: str", "value: str", "self.value = value + '!'", "parameter or literal"),
    # The record would store a loan of the constructor's own copy, which dies at return.
    ("view: StrView", "value: Own[str]", "self.view = value", "constructor view needs a lent parameter"),
    ("pair: tuple[str, str]", "value: str", "self.pair = (value, value)", "unsupported record fields"),
])
def test_owned_leaf_field_exclusions(field: str, param: str, body: str, reason: str) -> None:
    source = (f"from tpy import Own, StrView\nclass Record:\n    {field}\n"
              f"    def __init__(self, {param}):\n        {body}\n")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
    with activate_compiler(compiler):
        result = lower_constructor(ctor, MIRBodyId("constructors", "excluded"))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_initialization_uses_logical_fields_not_cpp_spellings(artifacts: Artifacts) -> None:
    ctor = artifacts[0]["Cell"]
    renamed = replace(ctor, record_name="Spelling", mil_inits=tuple(
        replace(mil, field_cpp="unrelated") for mil in reversed(ctor.mil_inits)))
    fn = lower_constructor(ctor, MIRBodyId("constructors", "identity"))
    other = lower_constructor(renamed, MIRBodyId("constructors", "identity"))
    assert dump_function(fn) == dump_function(other)


ENTRY_SOURCE = """\
from tpy import Own, StrView, String, int32, readonly
class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class Entry:
    n: int32
    s: str
    v: StrView
    w: StrView
    p: P
    q: P
    r: readonly[P | None]
    u: String
    t: str
    def __init__(self, n: int32, s: str, w: StrView, p: P, q: Own[P], u: String, o: Own[str]) -> None:
        self.n = n
        self.s = s
        self.v = s
        self.w = w
        self.p = p
        self.q = q
        self.r = None
        self.u = u
        self.t = o
class OwnView:
    s: StrView
    def __init__(self, s: Own[str]) -> None:
        self.s = s
"""


@pytest.fixture(scope="module")
def entry_program():
    compiler, modules = _compile(ENTRY_SOURCE)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        ctors = {ctor.record_name: ctor for ctor in ctx.thir_constructors.values()}
        definitions = compiled_definitions(compiler, tuple(ctors.values()))
        fn = lower_constructor(ctors["Entry"], MIRBodyId("constructors", "Entry"), definitions=definitions)
        assert isinstance(fn, MIRFunction), fn
        return compiler, fn, definitions, ctors


@pytest.fixture
def entry(entry_program):
    # Field and declared-type checks resolve under the compilation that registered them.
    compiler, *rest = entry_program
    with activate_compiler(compiler):
        yield rest


def test_entry_members_are_the_cfg_value_nodes(entry) -> None:
    fn, _, _ = entry
    validate_function(fn)
    name = {s.name: s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER}
    # An `Own[...]` parameter is its own storage; the body reaches it through a holder.
    owned = {s.name: s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.shape.is_storage}
    deref = (MIRDeref(),)
    assert [m.value for m in fn.receiver_init.members.fields] == [
        MIRRead(MIRPlace(name["n"])), MIRCopy(MIRPlace(name["s"], deref), True),
        MIRBorrow(MIRPlace(name["s"], deref)), MIRAlias(name["w"]), MIRCopy(MIRPlace(name["p"], deref)),
        MIRMove(owned["q"]), MIRWrap(0), MIRCopy(MIRPlace(name["u"], deref), True), MIRMove(owned["o"])]


def _entry_member(fn: MIRFunction, field: str, value: object) -> MIRFunction:
    layout = fn.record_layouts[fn.slots[0].type]
    members = tuple(replace(m, value=value) if f.id.name == field else m
                    for m, f in zip(fn.receiver_init.members.fields, layout.fields))
    return replace(fn, receiver_init=replace(fn.receiver_init, members=MIRMemberInits(members)))


def test_the_entry_rule_reads_only_what_a_callers_construct_can_supply(entry) -> None:
    fn, definitions, ctors = entry
    name = {s.name: s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and not s.shape.is_storage}
    owned = {s.name: s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER and s.shape.is_storage}
    receiver = fn.receiver_init.receiver
    layout = fn.record_layouts[fn.slots[0].type]
    field = {f.id.name: f for f in layout.fields}
    point = next(r for r in fn.records if r.type.name == "P")
    deref = (MIRDeref(),)
    refused = [
        # The receiver's own field: the storage entry builds is never a source.
        ("s", MIRCopy(MIRPlace(receiver, (*deref, field["t"])), True), "initializer parameter"),
        # An `Own[R]` parameter's storage is moved, never copied.
        ("q", MIRCopy(MIRPlace(owned["q"].id)), "initializer parameter"),
        # A parameter is read whole: no projection past the storage it lends.
        ("n", MIRRead(MIRPlace(name["p"].id, (*deref, point.fields[0]))), "initializer parameter"),
        # A view is no owned leaf's source.
        ("s", MIRCopy(MIRPlace(name["w"].id, deref), True), "initializer parameter"),
        # A view member stores a lent parameter's loan, never an owned one's.
        ("v", MIRBorrow(MIRPlace(owned["o"].id)), "initializer parameter"),
        # A record member is a copy, a move or its own constructor's fields.
        ("p", MIRConstruct((name["n"].id,)), "invalid receiver initializer$"),
        # An operation or a call is no member initializer.
        ("n", MIROp("+", (name["n"].id,), True), "invalid receiver initializer$"),
        # Refused by its kind, before any summary is read.
        ("n", MIRCall(None, (name["n"].id,), True), "invalid receiver initializer$"),
        ("p", MIRCall(None, (name["p"].id,), True), "invalid receiver initializer$"),
    ]
    for member, value, message in refused:
        with pytest.raises(MIRValidationError, match=message):
            validate_function(_entry_member(fn, member, value))
    # A record or `String` copy reads through a readonly lend only (a mutable
    # holder of an owned leaf is no slot at all).
    for lent, message in (("p", "initializer parameter"), ("u", "mutable loan of a readonly value")):
        with pytest.raises(MIRValidationError, match=message):
            validate_function(bind_parameters(replace(fn, slots=tuple(
                reslot(s, readonly=False) if s.id == name[lent].id else s for s in fn.slots))))
    # The definition refuses a view member of the constructor's own copy.
    own_view = ctors["OwnView"].record_layout.type
    assert definitions.records[own_view] == "constructor view needs a lent parameter"


def test_a_constant_raises_only_where_it_is_materialized() -> None:
    records = no_layouts()
    assert value_may_raise(MIRConstant("lit"), value_shape(STR), records)
    assert not value_may_raise(MIRConstant("lit"), value_shape(STRVIEW), records)
    assert not value_may_raise(MIRConstant(1), value_shape(INT32), records)
