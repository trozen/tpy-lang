"""Owned storage has identity independent of holders, copies and replacements."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from ..parse import RebindStorage
from ..thir import nodes as th
from ..thir.lower.storage import record_layout
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_constructor, validate_function
from ..typesys import BOOL, INT32, NominalType
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRBodyId, MIRBodyKind, MIRBorrow, MIRConstruct, MIRCopy, MIRFieldId, MIRFunction,
    MIRMove, MIRNotCovered, MIRRecordWriteMode, MIRValueKind,
)
from .storage import MIRStorageEvents, analyze_storage
from .testutil import Heap, Reference, execute

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]

SOURCE = """\
from tpy import int32, copy, readonly

class Cell:
    value: int32
    template: int32
    flag: bool

    def __init__(self, value: int32):
        self.value = value
        self.template = 3
        self.flag = False

class Other:
    value: int32

    def __init__(self, value: int32):
        self.value = value

def shared() -> int32:
    current = Cell(1)
    saved = current
    # The alias must observe mutation of the original owned storage.
    current.value = 9
    return saved.value

def replaced() -> int32:
    current = Cell(1)
    saved = current
    # Only current changes referent; saved must keep the first object alive.
    current = Cell(2)
    saved.value = 9
    return current.value

def in_place() -> int32:
    current = Cell(1)
    # Read the old payload before replacing it at the same storage identity.
    current = Cell(current.value)
    return current.value

def branches(flag: bool) -> int32:
    current = Cell(1)
    saved = current
    if flag:
        current = Cell(2)
    else:
        current = Cell(3)
    saved.value = 9
    return current.value

def copied() -> int32:
    current = Cell(1)
    duplicate = copy(current)
    # An explicit copy must isolate this mutation from current.
    duplicate.value = 9
    return current.value

def copy_readonly(source: readonly[Cell]) -> int32:
    duplicate = copy(source)
    # Readonly constrains source access, not the independently owned copy.
    duplicate.value = 9
    return source.value

def moved() -> int32:
    source = Cell(3)
    # Sema selects move-through because source is a fixed local at last use.
    target = source
    target.value = 9
    return target.value

def owned_then_borrowed(other: Cell, flag: bool) -> int32:
    current = Cell(1)
    if flag:
        current = other
    current.value = 7
    return other.value

def fields(flag: bool) -> int32:
    cell = Cell(1)
    other = Other(2)
    cell.template = 11
    other.value = 13
    cell.flag = flag
    return cell.template if cell.flag else other.value

def scalar_loop(flag: bool) -> int32:
    cell = Cell(1)
    saved = cell
    while flag:
        cell.value = 5
        flag = False
    return saved.value

def replacement_loop(flag: bool) -> int32:
    cell = Cell(1)
    while flag:
        cell = Cell(2)
        flag = False
    return cell.value
"""


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    return ({node.name: fn for node, fn in ctx.thir_functions.items()},
            tuple(ctx.thir_constructors.values()))


def lower(fn: th.THIRFunction, constructors: tuple[th.THIRConstructor, ...]) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("owned", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRFunction), result
    return result


@pytest.mark.parametrize("name,expected,contents", [
    ("shared", 9, [9]), ("replaced", 2, [2, 9]), ("in_place", 1, [1]),
    ("copied", 1, [1, 9]), ("moved", 9, [3, 9]),
])
def test_storage_identity_and_mutation(artifacts: Artifacts, name: str, expected: int,
                                       contents: list[int]) -> None:
    functions, constructors = artifacts
    fn = lower(functions[name], constructors)
    heap: Heap = {}
    assert execute(fn, heap=heap) == expected
    member = next(f.id for f in fn.records[0].fields if f.id.name == "value")
    # Inspect both objects: matching the return alone would miss a silent copy.
    assert sorted(obj[member] for obj in heap.values()) == contents
    events = analyze_storage(fn)
    assert isinstance(events, MIRStorageEvents)
    expected_modes = {
        "shared": [MIRRecordWriteMode.INITIALIZE_ONCE],
        "replaced": [MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.OWN_SITE],
        "in_place": [MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.IN_PLACE],
        "copied": [MIRRecordWriteMode.INITIALIZE_ONCE] * 2,
        "moved": [MIRRecordWriteMode.INITIALIZE_ONCE] * 2,
    }
    assert [s.storage_write.mode for s in events.writes.values()] == expected_modes[name]
    for stmt in events.writes.values():
        if stmt.storage_write.mode is MIRRecordWriteMode.IN_PLACE:
            assert stmt.storage_write.rebind_owner == stmt.target.root


@pytest.mark.parametrize("flag", [False, True])
def test_branch_replacements_and_scalar_loops(artifacts: Artifacts, flag: bool) -> None:
    functions, constructors = artifacts
    heap: Heap = {}
    fn = lower(functions["branches"], constructors)
    assert execute(fn, flag, heap=heap) == (2 if flag else 3)
    assert len(heap) == 2
    assert execute(lower(functions["fields"], constructors), flag) == (11 if flag else 13)
    assert execute(lower(functions["scalar_loop"], constructors), flag) == (5 if flag else 1)


@pytest.mark.parametrize("flag", [False, True])
def test_owned_holder_can_reseat_to_parameter(artifacts: Artifacts, flag: bool) -> None:
    functions, constructors = artifacts
    fn = lower(functions["owned_then_borrowed"], constructors)
    layout = fn.records[0]
    heap = {10: {f.id: (2 if f.id.name == "value" else False if f.type == BOOL else 3)
                 for f in layout.fields}}
    assert execute(fn, Reference(10), flag, heap=heap) == (7 if flag else 2)
    assert len(heap) == 2


def test_readonly_source_copy_is_independent(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    fn = lower(functions["copy_readonly"], constructors)
    heap = {1: {f.id: (2 if f.id.name == "value" else False if f.type == BOOL else 3)
                for f in fn.records[0].fields}}
    original = heap[1].copy()
    assert execute(fn, Reference(1), heap=heap) == 2
    assert heap[1] == original and len(heap) == 2


def test_actual_producer_shapes_and_deterministic_dump(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    assert functions["shared"].body[0].owned_storage is not None
    assert functions["shared"].body[0].form is th.Form.STORAGE
    assert functions["replaced"].body[0].form is th.Form.BORROW
    assert functions["replaced"].body[2].rebind_storage is RebindStorage.OWN
    assert functions["in_place"].body[1].rebind_storage is RebindStorage.IN_PLACE
    assert functions["owned_then_borrowed"].body[0].kind is th.PtrSlotKind.RECORD_RVALUE
    assert isinstance(functions["copied"].body[1].init, th.THIRCopy)
    assert isinstance(functions["moved"].body[1].init, th.THIRMove)
    for name, operation in (("shared", MIRConstruct), ("copied", MIRCopy), ("moved", MIRMove)):
        fn = lower(functions[name], constructors)
        values = [s.value for b in fn.blocks for s in b.statements]
        assert any(isinstance(v, operation) for v in values)
        assert any(isinstance(v, MIRBorrow) for v in values)
        assert any(s.value_kind is MIRValueKind.RECORD_STORAGE for s in fn.slots)
        assert dump_function(fn) == dump_function(lower(functions[name], constructors))
    cell = next(c for c in constructors if c.record_name == "Cell")
    assert [f.name for f in cell.record_layout.fields] == ["value", "template", "flag"]
    assert all(m.field_identity is not None for m in cell.mil_inits)


def test_missing_definitions_are_uncovered(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    for name, definitions, reason in (
        ("shared", MIRDefinitions(), "missing constructor definition"),
    ):
        result = lower_function(functions[name], MIRBodyId("owned", name),
                                kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
        assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_cyclic_in_place_replacement(artifacts: Artifacts) -> None:
    fn = lower(artifacts[0]["replacement_loop"], artifacts[1])
    assert execute(fn, False) == 1
    assert execute(fn, True) == 2


@pytest.mark.parametrize("change,reason", [
    (lambda c: replace(c, mil_inits=c.mil_inits[:-1]), "incomplete constructor"),
    (lambda c: replace(c, mil_inits=(*c.mil_inits, c.mil_inits[0])), "duplicate constructor field"),
    (lambda c: replace(c, mil_inits=(replace(c.mil_inits[0], field_identity=None), *c.mil_inits[1:])),
     "constructor field identity"),
    (lambda c: replace(c, record_layout=replace(c.record_layout, custom_destructor=True)), "special member"),
    (lambda c: replace(c, record_layout=replace(c.record_layout, custom_copy=True)), "special member"),
    (lambda c: replace(c, record_layout=replace(c.record_layout, custom_move=True)), "special member"),
    (lambda c: replace(c, record_layout=replace(c.record_layout, unique_constructor=False)), "unique"),
    (lambda c: replace(c, body=(th.THIRExprStmt(th.THIRLiteral(INT32, 1)),)), "body effects"),
    (lambda c: replace(c, base_inits=(th.THIRBaseInit("Base", ()),)), "base_inits"),
])
def test_incomplete_or_effectful_constructor_is_not_summarized(
    artifacts: Artifacts, change: Callable[[th.THIRConstructor], th.THIRConstructor], reason: str,
) -> None:
    functions, constructors = artifacts
    changed = tuple(change(c) if c.record_name == "Cell" else c for c in constructors)
    fn = functions["shared"]
    result = lower_function(fn, MIRBodyId("owned", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(changed))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


@pytest.mark.parametrize("change,reason", [
    (lambda init: replace(init, args=()), "incomplete constructor arguments"),
    (lambda init: replace(init, args=(th.THIRLiteral(BOOL, True),)), "argument type"),
    (lambda init: replace(init, args=(th.THIRCall(INT32, "effect", ()),)), "unsupported expression"),
    (lambda init: replace(init, args=(th.THIRWalrus(INT32, "n", "n", th.THIRLiteral(INT32, 2)),)),
     "effectful constructor argument"),
])
def test_constructor_arguments_are_complete_and_pure(
    artifacts: Artifacts, change: Callable[[th.THIRCtorCall], th.THIRCtorCall], reason: str,
) -> None:
    functions, constructors = artifacts
    fn = functions["shared"]
    declaration = replace(fn.body[0], init=change(fn.body[0].init))
    fn = replace(fn, params=(th.THIRParam("n", INT32),), body=(declaration, *fn.body[1:]))
    result = lower_function(fn, MIRBodyId("owned", fn.name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


def test_cpp_spellings_do_not_supply_storage_or_constructor_identity(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    fn = functions["shared"]
    declaration = fn.body[0]
    changed = replace(fn, body=(replace(declaration, cpp_type="ignored",
                                        init=replace(declaration.init, type_cpp="also_ignored")), *fn.body[1:]))
    renamed = tuple(replace(c, record_name="presentation",
                            mil_inits=tuple(replace(m, field_cpp="presentation") for m in c.mil_inits))
                    for c in constructors)
    assert dump_function(lower(fn, constructors)) == dump_function(lower(changed, renamed))


@pytest.mark.parametrize("body", [
    "    cell = Cell(1)\n    return cell.value\n",
    "    alias = self\n    return alias.value\n",
])
def test_owned_and_self_alias_method_bodies(body: str) -> None:
    source = SOURCE.replace("class Other:", "    def method(self) -> int32:\n" +
                            "\n".join("    " + line for line in body.splitlines()) + "\n\nclass Other:")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(f for n, f in ctx.thir_functions.items() if n.name == "method")
    if body.startswith("    cell"):
        assert fn.body[0].owned_storage is not None
    else:
        assert fn.body[0].alias_binding.source == "self"
    result = lower_function(fn, MIRBodyId("owned", "method"), kind=MIRBodyKind.METHOD,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRFunction)
    value = MIRFieldId(fn.receiver.type, "value")
    assert execute(result, Reference(1), heap={1: {value: 7}}) == (1 if body.startswith("    cell") else 7)


@pytest.mark.parametrize("declaration,assignment,reason", [
    ("value: int32 = 5", "pass", "incomplete constructor initialization"),
    ("value: int32", "self.value = value\n        print(value)", "constructor body effects"),
    ("value: tuple[int32, int32]", "self.value = (value, value)", "unsupported record fields"),
    ("value: int32 | None", "self.value = value", "unsupported record fields"),
    ("value: list[int32]", "self.value = [value]", "unsupported record fields"),
])
def test_real_constructor_effects_and_sibling_shapes_remain_uncovered(
    declaration: str, assignment: str, reason: str,
) -> None:
    source = ("from tpy import int32\nclass Record:\n    " + declaration
              + "\n    def __init__(self, value: int32):\n        " + assignment
              + "\ndef build() -> int32:\n    record = Record(1)\n    return 0\n")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(f for n, f in ctx.thir_functions.items() if n.name == "build")
    result = lower_function(fn, MIRBodyId("owned", "build"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRNotCovered) and reason in result.reason


@pytest.mark.parametrize("decorator,methods,attribute,expected", [
    ("", "    def __copy__(self) -> Own[Record]:\n        return Record(self.value)\n",
     "custom_copy", True),
    ("", "    def __del__(self):\n        pass\n", "custom_destructor", True),
    ("", "    def __del__(self):\n        pass\n"
     "    def __move__(self, other: Own[Record]):\n        self.value = other.value\n", "custom_move", True),
    ("@nocopy\n", "", "copyable", False),
    ("@nomove\n", "", "movable", False),
])
def test_special_member_facts_come_from_real_declarations(
    decorator: str, methods: str, attribute: str, expected: bool,
) -> None:
    source = ("from __future__ import annotations\nfrom tpy import int32, Own, nocopy, nomove\n"
              + decorator + "class Record:\n    value: int32\n"
              "    def __init__(self, value: int32):\n        self.value = value\n" + methods
              + "\ndef build() -> int32:\n    record = Record(1)\n    return record.value\n")
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Record")
    assert getattr(ctor.record_layout, attribute) is expected
    fn = next(f for n, f in ctx.thir_functions.items() if n.name == "build")
    result = lower_function(fn, MIRBodyId("owned", "build"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions((ctor,)))
    if attribute.startswith("custom"):
        assert isinstance(result, MIRNotCovered) and "special member" in result.reason
    else:
        assert isinstance(result, MIRFunction)
        assert getattr(result.records[0], attribute) is False


def test_inconsistent_thir_storage_and_member_facts_fail_validation(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    fn = functions["shared"]
    declaration = fn.body[0]
    for bad in (replace(declaration, init=None),
                replace(declaration, owned_storage=replace(declaration.owned_storage, readonly=True)),
                replace(declaration, resolved_type=INT32)):
        with pytest.raises(THIRValidationError, match="owned storage disagrees"):
            validate_function(replace(fn, body=(bad, *fn.body[1:])))
    ctor = next(c for c in constructors if c.record_name == "Cell")
    wrong_member = replace(ctor.mil_inits[0].field_identity, name="absent")
    with pytest.raises(THIRValidationError, match="member identity disagrees"):
        validate_constructor(replace(ctor, mil_inits=(replace(ctor.mil_inits[0], field_identity=wrong_member),)))


def test_constructor_and_nested_body_producers_retain_owned_facts() -> None:
    source = SOURCE + """\

class Holder:
    value: int32

    def __init__(self, value: int32):
        cell = Cell(value)
        cell.value = 7
        self.value = cell.value

def outer() -> int32:
    def nested() -> int32:
        cell = Cell(1)
        cell.value = 9
        return cell.value
    return nested()
"""
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Holder")
    assert ctor.body[0].owned_storage is not None
    assert isinstance(MIRDefinitions((ctor,)).records[ctor.record_layout.type], str)
    outer = next(f for n, f in ctx.thir_functions.items() if n.name == "outer")
    nested = next(s for s in outer.body if isinstance(s, th.THIRNestedDef))
    assert nested.body[0].owned_storage is not None
    assert isinstance(lower_function(outer, MIRBodyId("owned", "outer"), kind=MIRBodyKind.FREE_FUNCTION),
                      MIRNotCovered)


def test_constructor_index_distinguishes_same_named_types_across_modules(tmp_path: Path) -> None:
    template = ("from tpy import int32\nclass Cell:\n    value: int32\n"
                "    def __init__(self, value: int32):\n        self.value = value\n")
    for module in ("left", "right"):
        (tmp_path / f"{module}.py").write_text(template)
    source = """\
from tpy import int32
from left import Cell as Left
from right import Cell as Right

def inspect() -> int32:
    first = Left(1)
    second = Right(2)
    first.value = 9
    return second.value
"""
    compiler, modules = _compile(source, extra_lib_dirs=[tmp_path])
    constructors = []
    for module in modules:
        if module.name in ("left", "right"):
            _, ctx = compiler.generate_code_and_thir(module)
            constructors.extend(ctx.thir_constructors.values())
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(f for n, f in ctx.thir_functions.items() if n.name == "inspect")
    mir = lower(fn, tuple(constructors))
    assert len(mir.records) == 2 and mir.records[0].type != mir.records[1].type
    heap: Heap = {}
    assert execute(mir, heap=heap) == 2
    assert sorted(value for obj in heap.values() for value in obj.values()) == [2, 9]


def test_overloaded_constructor_cannot_be_stamped_unique() -> None:
    source = """\
from typing import overload
from tpy import int32

class Record:
    value: int32

    @overload
    def __init__(self, value: int32): ...

    @overload
    def __init__(self, value: int32, other: int32): ...

    def __init__(self, value: int32, other: int32 = 0):
        self.value = value
"""
    compiler, modules = _compile(source)
    typ = NominalType("Record", _module_qname="__main__.Record")
    # The normal constructor path excludes overloads before emitting its THIR.
    # Check the producer against the real registered signatures as well.
    fact = record_layout(typ, _entry(modules).analyzer)
    assert fact is not None and not fact.unique_constructor
