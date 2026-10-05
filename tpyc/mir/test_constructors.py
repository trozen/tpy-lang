"""Constructor entry initializes the supplied receiver before aliases observe it."""

from dataclasses import replace
from collections.abc import Callable

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, NominalType
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRBodyId, MIRConstant, MIRFieldId, MIRFunction, MIRMemberInit, MIRMemberInitMode, MIRNotCovered,
    MIRSlotId, MIRSlotKind,
)
from .testutil import Reference, execute
from .validate import MIRValidationError, validate_function


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
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
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
    assert tuple(member.source for member in init.fields) == (fn.slots[1].id, MIRConstant(0), MIRConstant(False))
    assert all(member.mode is MIRMemberInitMode.SCALAR and not member.may_raise for member in init.fields)
    heap = {41: {}, 42: {}}
    assert execute(fn, Reference(41), 3, reset, heap=heap) is None
    typ = fn.slots[0].type
    assert heap[41] == {MIRFieldId(typ, "value"): 7 if reset else 3,
                        MIRFieldId(typ, "before"): 3, MIRFieldId(typ, "changed"): reset}
    assert heap[42] == {}
    assert "initialize-receiver %0 (%1, 0, False)" in dump_function(fn)


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
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init, fields=tuple(
            MIRMemberInit(source) for source in fields))))


def test_entry_cannot_read_a_local_or_initialize_readonly_receiver(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["Cell"])
    local = next(s for s in fn.slots if s.kind is MIRSlotKind.TEMPORARY and s.type == INT32)
    with pytest.raises(MIRValidationError, match="initializer parameter"):
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init,
            fields=(MIRMemberInit(local.id), MIRMemberInit(MIRConstant(0)), MIRMemberInit(MIRConstant(False))))))
    with pytest.raises(MIRValidationError, match="constructor receiver"):
        validate_function(replace(fn, slots=(replace(fn.slots[0], readonly=True), *fn.slots[1:])))
    with pytest.raises(MIRValidationError, match="constructor receiver"):
        validate_function(replace(fn, receiver_init=replace(fn.receiver_init, receiver=fn.slots[1].id)))
    with pytest.raises(MIRValidationError, match="constructor entry mismatch"):
        validate_function(replace(fn, receiver_init=None))


@pytest.mark.parametrize("fields,body,reason", [
    ("value: int32 = 4", "pass", "incomplete constructor"),
    ("value: int32", "saved = value\n        self.value = saved", "incomplete constructor"),
    ("value: int32", "self.value = value\n        print((value, value))", "print argument needs a scalar leaf"),
    ("value: int32", "self.value = value + 1", "parameter or literal"),
])
def test_actual_emitted_exclusions(fields: str, body: str, reason: str) -> None:
    source = f"from tpy import int32\nclass Record:\n    {fields}\n    def __init__(self, value: int32):\n        {body}\n"
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
    result = lower_constructor(ctor, MIRBodyId("constructors", "excluded"))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


@pytest.mark.parametrize("field,param,body,reason", [
    ("value: str", "value: str", "self.value = value + '!'", "parameter or literal"),
    ("view: StrView", "value: StrView", "self.view = value", "record holds a borrow"),
    ("pair: tuple[str, str]", "value: str", "self.pair = (value, value)", "unsupported record fields"),
])
def test_owned_leaf_field_exclusions(field: str, param: str, body: str, reason: str) -> None:
    source = (f"from tpy import StrView\nclass Record:\n    {field}\n"
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
