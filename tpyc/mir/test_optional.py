"""Optional copies retain values or referents; extraction needs a current guard."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _assert_rejects_at, _compile, _entry, _strict_reject
from ..typesys import BOOL, INT32_MAX, INT32_MIN, VoidType
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBodyId, MIRFieldId, MIRFunction,
    MIRNotCovered, MIROptionalConstruct, MIROptionalCopy, MIRPlace, MIRReturn,
)
from .testutil import Heap, OptionalValue, Reference, execute
from .validate import MIRValidationError, validate_function as validate_mir

SOURCE = """\
from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def observe(self, a: Cell | None) -> int32:
        saved = a
        self.value = 9
        if saved is not None:
            return saved.value
        return 0

class Holder:
    value: int32
    def __init__(self, a: Cell | None):
        saved = a
        if saved is not None:
            self.value = saved.value
        else:
            self.value = 0

def records(a: Cell | None, other: Cell) -> int32:
    current = a
    saved = current
    current = None
    if saved is not None:
        saved.value = 9
    return other.value

def scalar(a: int32 | None, b: int32) -> int32:
    current = a
    saved = current
    current = b
    if saved is not None:
        return saved
    return 13

def boolean(a: bool | None) -> bool:
    current = a
    saved = current
    current = None
    if saved is not None:
        return saved
    return True

def readonly_alias(a: readonly[Cell] | None, other: Cell) -> int32:
    saved = a
    other.value = 12
    if saved is not None:
        return saved.value
    return 0

def loop(a: Cell | None) -> int32:
    current = a
    while current is not None:
        current.value = 9
        current = None
    return 0

def early(a: int32 | None) -> int32:
    if a is None:
        return 13
    return a

def lazy(a: int32 | None, flag: bool) -> int32:
    if a is not None and flag:
        return a
    return 13

def lazy_or(a: int32 | None, flag: bool) -> int32:
    if a is None or flag:
        return 13
    return a

def scalar_reseat(a: int32 | None, flag: bool) -> int32:
    current = a
    if flag:
        current = None
    if current is not None:
        return current
    return 13

def narrowed_copy(a: int32 | None, b: int32) -> int32:
    current = a
    saved = a
    if current is not None:
        saved = current
        current = b
    if saved is not None:
        return saved
    return 13
"""

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {node.name: fn for node, fn in ctx.thir_functions.items()}, tuple(ctx.thir_constructors.values())


def lower(fn: th.THIRFunction) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("optionals", fn.name))
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("payload", [None, 0, 7, INT32_MIN, INT32_MAX])
def test_scalar_snapshots_and_presence(artifacts: Artifacts, payload: int | None) -> None:
    functions, _ = artifacts
    expected = 13 if payload is None else payload
    for name in ("scalar", "early", "narrowed_copy"):
        fn = lower(functions[name])
        args = (OptionalValue(payload),) if name == "early" else (OptionalValue(payload), 42)
        assert execute(fn, *args) == expected
        assert dump_function(fn) == dump_function(lower(functions[name]))


@pytest.mark.parametrize("payload", [None, False, True])
def test_false_is_a_present_payload(artifacts: Artifacts, payload: bool | None) -> None:
    assert execute(lower(artifacts[0]["boolean"]), OptionalValue(payload)) is (
        True if payload is None else payload)


@pytest.mark.parametrize("identity", [None, 1, 2])
def test_record_copy_does_not_follow_holder_clear(artifacts: Artifacts, identity: int | None) -> None:
    fn = lower(artifacts[0]["records"])
    member = MIRFieldId(fn.slots[0].optional_layout.type, "value")
    heap: Heap = {1: {member: 2}, 2: {member: 3}}
    payload = Reference(identity) if identity is not None else None
    assert execute(fn, OptionalValue(payload), Reference(1), heap=heap) == (9 if identity == 1 else 2)
    assert heap[1][member] == (9 if identity == 1 else 2)
    assert heap[2][member] == (9 if identity == 2 else 3)


@pytest.mark.parametrize("shared", [False, True])
def test_readonly_alias_observes_mutation(artifacts: Artifacts, shared: bool) -> None:
    fn = lower(artifacts[0]["readonly_alias"])
    member = MIRFieldId(fn.slots[0].optional_layout.type, "value")
    heap: Heap = {1: {member: 2}, 2: {member: 3}}
    assert execute(fn, OptionalValue(Reference(1 if shared else 2)), Reference(1), heap=heap) == (
        12 if shared else 3)


@pytest.mark.parametrize("present", [False, True])
def test_loop_guard_is_retested_after_clear(artifacts: Artifacts, present: bool) -> None:
    fn = lower(artifacts[0]["loop"])
    member = MIRFieldId(fn.slots[0].optional_layout.type, "value")
    heap: Heap = {1: {member: 2}}
    assert execute(fn, OptionalValue(Reference(1) if present else None), heap=heap) == 0
    assert heap[1][member] == (9 if present else 2)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("payload", [None, 0, 7])
def test_lazy_guards_and_join(artifacts: Artifacts, flag: bool, payload: int | None) -> None:
    functions, _ = artifacts
    value = 13 if payload is None else payload
    assert execute(lower(functions["lazy"]), OptionalValue(payload), flag) == (value if flag else 13)
    assert execute(lower(functions["lazy_or"]), OptionalValue(payload), flag) == (13 if flag else value)
    assert execute(lower(functions["scalar_reseat"]), OptionalValue(payload), flag) == (13 if flag else value)


def test_shared_parameter_and_declaration_producers(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    method = functions["observe"]
    constructor = next(c for c in constructors if c.record_name == "Holder")
    for body in (method, constructor):
        assert next(p for p in body.params if p.name == "a").optional_layout is not None
        decl = next(stmt for stmt in body.body if isinstance(stmt, th.THIRVarDecl) and stmt.name == "saved")
        assert decl.optional_layout is not None and decl.init.optional_read is not None
    result = lower_function(method, MIRBodyId("optionals", "observe"))
    assert isinstance(result, MIRFunction)
    value = MIRFieldId(method.receiver.type, "value")
    assert execute(result, Reference(1), OptionalValue(Reference(1)), heap={1: {value: 1}}) == 9


def test_missing_read_or_binding_fact_is_uncovered(artifacts: Artifacts) -> None:
    fn = artifacts[0]["scalar"]
    decl = fn.body[0]
    for bad_decl in (replace(decl, optional_layout=None),
                     replace(decl, init=replace(decl.init, optional_read=None))):
        bad = replace(fn, body=(bad_decl, *fn.body[1:]))
        assert isinstance(lower_function(bad, MIRBodyId("optionals", fn.name)), MIRNotCovered)


@pytest.mark.parametrize("annotation,reason", [
    ("Cell | None", "decl.reseat_param_source"),
    ("Cell | Other", "decl.union_reseat_source"),
])
def test_record_parameter_reseat_source_gate_is_unchanged(annotation: str, reason: str) -> None:
    source = SOURCE + """\
class Other:
    value: int32
""" + f"def unsupported(a: {annotation}, b: Cell) -> int32:\n" + """\
    current = a
    current = b
    return b.value
"""
    _, reasons = _strict_reject(source)
    _assert_rejects_at(reasons, "body:stmt.var_decl", reason)


@pytest.mark.parametrize("annotation", [
    "Own[Cell | None]", "tuple[int32] | None", "str | None", "list[Cell] | None",
    "GenericCell[int32] | None", "int32 | str",
])
def test_deferred_payload_families_are_uncovered(annotation: str) -> None:
    source = SOURCE + """\
from tpy import Own
class GenericCell[T]:
    value: T
class Child(Cell):
    pass
""" + f"\ndef excluded(a: {annotation}) -> int32:\n    return 1\n"
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    assert fn.params[0].optional_layout is None
    assert isinstance(lower_function(fn, MIRBodyId("optionals", "excluded")), MIRNotCovered)


def test_a_derived_record_payload_is_borrowed() -> None:
    source = SOURCE + """\
class Child(Cell):
    pass

def admitted(a: Child | None) -> int32:
    if a is not None:
        return a.value
    return 0
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "admitted")
    child = fn.params[0].optional_layout.payload
    assert child == th.THIRBorrowedRecord(child.type, True) and child.type.name == "Child"
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()),
                                 inherited=tuple(ctx.thir_inherited_constructors.values()))
    with activate_compiler(compiler):
        result = lower_function(fn, MIRBodyId("optionals", fn.name), definitions=definitions)
    assert isinstance(result, MIRFunction), result


@pytest.mark.parametrize("change", ["type", "access", "form", "checked"])
def test_contradictory_optional_facts_are_rejected(artifacts: Artifacts, change: str) -> None:
    fn = artifacts[0]["readonly_alias"]
    decl = fn.body[0]
    if change == "type":
        decl = replace(decl, optional_layout=th.THIROptionalLayout(BOOL))
    elif change == "access":
        decl = replace(decl, is_const=False)
    elif change == "form":
        decl = replace(decl, form=th.Form.STORAGE)
    else:
        decl = replace(decl, init=replace(decl.init, opt_deref_check=True))
    bad = replace(fn, body=(decl, *fn.body[1:]))
    assert isinstance(lower_function(bad, MIRBodyId("optionals", bad.name)), MIRNotCovered)


def test_thir_rejects_readonly_capability_increase(artifacts: Artifacts) -> None:
    fn = artifacts[0]["readonly_alias"]
    param = fn.params[0]
    mutable = replace(param.optional_layout.payload, readonly=False)
    bad = replace(fn, params=(replace(param, optional_layout=th.THIROptionalLayout(mutable)), *fn.params[1:]))
    with pytest.raises(THIRValidationError, match="increases access"):
        validate_thir(bad)


def test_unproven_access_is_uncovered_without_changing_source_acceptance() -> None:
    compiler, modules = _compile(SOURCE + """\
def unchecked(a: Cell | None) -> int32:
    return a.value
""")
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "unchecked")
    assert isinstance(lower_function(fn, MIRBodyId("optionals", fn.name)), MIRNotCovered)


def test_internal_record_member_assembly(artifacts: Artifacts) -> None:
    fn = artifacts[0]["records"]
    decl = fn.body[0]
    member = th.THIRName(name="other", result_type=decl.optional_layout.payload.type,
                         form=th.Form.BORROW)
    # The frontend's parameter-reseat gate is pinned separately; this checks
    # the admitted IR operation without claiming that source spelling works.
    other = replace(fn.params[1], borrowed_record=replace(fn.params[1].borrowed_record, readonly=False))
    ir = lower(replace(fn, params=(fn.params[0], other),
                       body=(replace(decl, init=member), *fn.body[1:])))
    field = MIRFieldId(decl.optional_layout.payload.type, "value")
    heap: Heap = {1: {field: 2}}
    assert execute(ir, OptionalValue(None), Reference(1), heap=heap) == 9


def test_internal_readonly_optional_store_is_rejected(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["records"])
    readonly = tuple(replace(s, optional_layout=replace(s.optional_layout, readonly=True))
                     if s.optional_layout is not None else s for s in fn.slots)
    from_fn = replace(fn, slots=readonly)
    with pytest.raises(MIRValidationError, match="readonly reference"):
        validate_mir(from_fn)


@pytest.mark.parametrize("copy", [False, True])
@pytest.mark.parametrize("source_readonly,target_readonly", [(True, False), (False, True), (True, True)])
def test_optional_transfer_capabilities(artifacts: Artifacts, copy: bool,
                                        source_readonly: bool, target_readonly: bool) -> None:
    fn = lower(artifacts[0]["records"])
    slots = list(fn.slots)
    source = next(s for s in slots if s.name == ("a" if copy else "other"))
    target = next(s for s in slots if s.name == "current")
    slots[source.id.index] = (replace(source, optional_layout=replace(
        source.optional_layout, readonly=source_readonly)) if copy
        else replace(source, readonly=source_readonly))
    slots[target.id.index] = replace(target, optional_layout=replace(
        target.optional_layout, readonly=target_readonly))
    operation = MIROptionalCopy(source.id) if copy else MIROptionalConstruct(source.id)
    root = fn.blocks[0].region
    block = MIRBlock(fn.entry, (MIRAssign(MIRPlace(target.id), operation),), MIRReturn(), root)
    fn = replace(fn, slots=tuple(replace(s, residence=root) if s.residence else s for s in slots),
                 return_type=VoidType(), blocks=(block,), regions=(fn.regions[0],))
    if source_readonly and not target_readonly:
        with pytest.raises(MIRValidationError, match="access mismatch"):
            validate_mir(fn)
    else:
        validate_mir(fn)
