"""Shared mutation and reseating over THIR emitted from unit-owned source."""

from collections.abc import Iterator
from dataclasses import fields, replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRAlias, MIRBodyId, MIRFieldId, MIRFunction, MIRNotCovered,
    MIRValueKind,
)
from .testutil import Heap, Reference, execute

SOURCE = """\
from tpy import int32, readonly

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

def touch(cell: Cell) -> int32:
    alias = cell
    cell.value = 7
    return alias.value

def touch_alias(cell: Cell) -> int32:
    alias = cell
    alias.value = 8
    return cell.value

def reseat(a: Cell, b: Cell, flag: bool) -> int32:
    current = a
    saved = current
    if flag:
        current = b
    current.value = 7
    return saved.value

def reseat_local(a: Cell, b: Cell, flag: bool) -> int32:
    current = a
    other = b
    if flag:
        current = other
    current.value = 9
    return a.value

def reseat_pointer(a: Cell, b: Cell, flag: bool) -> int32:
    current = a
    other = a
    if flag:
        other = b
        current = other
    current.value = 10
    return a.value

def loop(a: Cell, b: Cell, flag: bool) -> int32:
    current = a
    saved = current
    while flag:
        current = b
        flag = False
    current.value = 12
    return saved.value

def shared(a: Cell, b: Cell) -> int32:
    a.value = 9
    return b.value

def snapshot(cell: Cell) -> int32:
    saved = cell.value
    cell.value = 11
    return saved

def shared_readonly(a: Cell, b: readonly[Cell]) -> int32:
    alias = b
    a.value = 13
    return alias.value

def readonly_reseat(a: readonly[Cell], b: readonly[Cell], flag: bool) -> int32:
    current = a
    if flag:
        current = b
    return current.value

def fields(cell: Cell, other: Other) -> int32:
    cell.value = 17
    cell.template = 19
    other.value = 23
    cell.flag = True
    return cell.template if cell.flag else other.value
"""


@pytest.fixture(scope="module")
def functions() -> dict[str, th.THIRFunction]:
    compiler, modules = _compile(SOURCE)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    return {node.name: fn for node, fn in ctx.thir_functions.items()}


def lower(fn: th.THIRFunction) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("storage", fn.name))
    assert isinstance(result, MIRFunction), result
    return result


def objects(fn: th.THIRFunction) -> tuple[MIRFieldId, Heap]:
    owner = fn.params[0].borrowed_record.type
    field = MIRFieldId(owner, "value")
    return field, {1: {field: 1}, 2: {field: 2}}


@pytest.mark.parametrize("name,expected", [("touch", 7), ("touch_alias", 8), ("snapshot", 1)])
def test_alias_writes_and_scalar_snapshot(functions: dict[str, th.THIRFunction], name: str, expected: int) -> None:
    fn = functions[name]
    field, heap = objects(fn)
    assert execute(lower(fn), Reference(1), heap=heap) == expected
    assert heap[1][field] == (11 if name == "snapshot" else expected)


@pytest.mark.parametrize("name,written", [
    ("reseat", 7), ("reseat_local", 9), ("reseat_pointer", 10), ("loop", 12),
])
@pytest.mark.parametrize("same", [False, True])
@pytest.mark.parametrize("flag", [False, True])
def test_reseat_does_not_redirect_saved_alias(functions: dict[str, th.THIRFunction], name: str, written: int,
                                             same: bool, flag: bool) -> None:
    fn = functions[name]
    field, heap = objects(fn)
    second = 1 if same else 2
    # A saved holder keeps a's referent even when current starts pointing at b.
    expected = written if same or not flag else 1
    assert execute(lower(fn), Reference(1), Reference(second), flag, heap=heap) == expected
    target = second if flag else 1
    assert heap[target][field] == written
    assert heap[3 - target][field] == 3 - target


@pytest.mark.parametrize("name,written", [("shared", 9), ("shared_readonly", 13)])
@pytest.mark.parametrize("same", [False, True])
def test_readonly_reads_observe_other_alias_writes(functions: dict[str, th.THIRFunction], name: str, written: int,
                                                 same: bool) -> None:
    fn = functions[name]
    field, heap = objects(fn)
    mir = lower(fn)
    assert mir.slots[1].readonly
    assert execute(mir, Reference(1), Reference(1 if same else 2), heap=heap) == (
        written if same else 2)
    assert heap[1][field] == written


@pytest.mark.parametrize("flag", [False, True])
def test_readonly_holder_can_be_reseated(functions: dict[str, th.THIRFunction], flag: bool) -> None:
    fn = functions["readonly_reseat"]
    _, heap = objects(fn)
    assert execute(lower(fn), Reference(1), Reference(2), flag, heap=heap) == (2 if flag else 1)


@pytest.mark.parametrize("change", [{"move": True}, {"materialize": "copy"},
                                    {"generic_return": True}])
def test_alias_conversions_cannot_acquire_ownership_effects(
    functions: dict[str, th.THIRFunction], change: dict[str, object],
) -> None:
    fn = functions["readonly_reseat"]
    decl, branch, ret = fn.body
    reseat = branch.then_body[0]
    assert isinstance(decl.init, th.THIRFormConvert)
    assert isinstance(reseat.value, th.THIRFormConvert)
    assert decl.alias_binding is not None and reseat.alias_binding is not None
    for bad in (
        replace(fn, body=(replace(decl, init=replace(decl.init, **change)), branch, ret)),
        replace(fn, body=(decl, replace(branch, then_body=(replace(
            reseat, value=replace(reseat.value, **change)),)), ret)),
    ):
        not_covered(bad, "unsupported metadata")
        with pytest.raises(THIRValidationError, match="non-borrow conversion"):
            validate_thir(bad)


@pytest.mark.parametrize("typ", [BOOL, INT32])
def test_scalar_nominals_cannot_claim_borrowed_record_facts(typ: NominalType) -> None:
    fn = th.THIRFunction("f", (th.THIRParam("x", typ, th.THIRBorrowedRecord(typ, False), passing=ParamPassing.MUT_REF),),
                         INT32, (th.THIRReturn(th.THIRLiteral(INT32, 1)),), th.THIRFunctionLayout())
    not_covered(fn, "unsupported reference fact")


def walk(node: th.THIRNode) -> Iterator[th.THIRNode]:
    yield node
    for member in fields(node):
        value = getattr(node, member.name)
        if isinstance(value, th.THIRNode):
            yield from walk(value)
        elif isinstance(value, tuple):
            for child in value:
                if isinstance(child, th.THIRNode):
                    yield from walk(child)


def test_field_identity_and_presentation_are_separate(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["fields"]
    cell, other = (param.borrowed_record.type for param in fn.params)
    assert cell != other
    heap = {1: {MIRFieldId(cell, "value"): 1, MIRFieldId(cell, "template"): 3,
                MIRFieldId(cell, "flag"): False}, 2: {MIRFieldId(other, "value"): 2}}
    mir = lower(fn)
    assert execute(mir, Reference(1), Reference(2), heap=heap) == 19
    assert heap == {1: {MIRFieldId(cell, "value"): 17, MIRFieldId(cell, "template"): 19,
                        MIRFieldId(cell, "flag"): True}, 2: {MIRFieldId(other, "value"): 23}}
    escaped = fn.body[1]
    assert isinstance(escaped, th.THIRAssign)
    assert escaped.target.field_identity.name == "template"
    assert escaped.target.field_cpp != "template"
    changed = replace(escaped, target=replace(escaped.target, field_cpp="different_cpp_name"))
    assert lower(replace(fn, body=(fn.body[0], changed, *fn.body[2:]))) == mir


def test_alias_producer_paths_and_dump(functions: dict[str, th.THIRFunction]) -> None:
    aliases = [node for fn in functions.values() for stmt in fn.body for node in walk(stmt)
               if getattr(node, "alias_binding", None) is not None]
    assert {type(node) for node in aliases} == {
        th.THIRVarDecl, th.THIRPtrLocalDecl, th.THIRPtrLocalRebind, th.THIRAssign}
    for name in ("reseat", "reseat_local", "reseat_pointer"):
        fn = lower(functions[name])
        assert any(isinstance(stmt.value, MIRAlias) for block in fn.blocks for stmt in block.statements)
        assert all(slot.type not in (BOOL, INT32) for slot in fn.slots
                   if slot.value_kind is MIRValueKind.BORROWED)
    text = dump_function(lower(functions["reseat"]))
    assert " = alias %" in text and "::value" in text
    assert "mutable-ref" in text
    assert "readonly-ref" in dump_function(lower(functions["shared_readonly"]))
    assert "bound method" not in text
    assert text == dump_function(lower(functions["reseat"]))


def not_covered(fn: th.THIRFunction, reason: str) -> None:
    result = lower_function(fn, MIRBodyId("storage", fn.name))
    assert isinstance(result, MIRNotCovered), result
    assert reason in result.reason


def test_semantic_facts_are_required(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["touch"]
    alias, store, ret = fn.body
    not_covered(replace(fn, params=(replace(fn.params[0], borrowed_record=None),)),
                "unsupported parameter type")
    not_covered(replace(fn, body=(replace(alias, alias_binding=None), store, ret)),
                "unsupported metadata")
    not_covered(replace(fn, body=(alias, replace(store, target=replace(
        store.target, field_identity=None)), ret)), "missing field identity")
    not_covered(replace(fn, body=(replace(alias, alias_binding=replace(
        alias.alias_binding, source="missing")), store, ret)), "alias source mismatch")
    # Unsupported unreachable statements must still prevent a whole-body proof.
    not_covered(replace(fn, body=(alias, store, ret, replace(store, target=replace(
        store.target, field_identity=None)))), "missing field identity")


@pytest.mark.parametrize("annotation", [
    "Own[Cell]", "Cell | int32", "tuple[tuple[Cell]]",
    "GenericCell[int32]", "Child",
])
def test_excluded_parameter_shapes(annotation: str) -> None:
    source = SOURCE + """\
from tpy import Own
class GenericCell[T]:
    value: T
class Child(Cell):
    pass
""" + f"\ndef excluded(value: {annotation}) -> int32:\n    return 1\n"
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    assert fn.params[0].borrowed_record is None
    not_covered(fn, "unsupported parameter type")


@pytest.mark.parametrize("body,reason", [
    ("    created = Cell(1)\n    return created.value\n", "missing constructor definition"),
    ("    return touch(cell)\n", "call needs finalized known summary"),
])
def test_excluded_storage_operations(body: str, reason: str) -> None:
    compiler, modules = _compile(SOURCE + "\ndef excluded(cell: Cell) -> int32:\n" + body)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    not_covered(fn, reason)


def test_unknown_reference_and_access_upgrade_are_not_covered(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["touch"]
    alias, store, ret = fn.body
    unknown = replace(alias, init=replace(alias.init, name="missing"),
                      alias_binding=replace(alias.alias_binding, source="missing"))
    not_covered(replace(fn, body=(unknown, store, ret)), "unknown reference source")
    param = fn.params[0]
    readonly = replace(param, borrowed_record=replace(param.borrowed_record, readonly=True))
    not_covered(replace(fn, params=(readonly,)), "alias increases access")


def test_inconsistent_thir_facts_fail_validation(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["touch"]
    alias, store, ret = fn.body
    bad_alias = replace(alias, alias_binding=replace(alias.alias_binding, source="missing"))
    with pytest.raises(THIRValidationError, match="alias binding disagrees"):
        validate_thir(replace(fn, body=(bad_alias, store, ret)))
    bad_field = replace(store.target, field_identity=replace(store.target.field_identity, type=BOOL))
    with pytest.raises(THIRValidationError, match="field identity disagrees"):
        validate_thir(replace(fn, body=(alias, replace(store, target=bad_field), ret)))
    param = fn.params[0]
    with pytest.raises(THIRValidationError, match="borrowed record fact disagrees"):
        validate_thir(replace(fn, params=(replace(param, type=INT32),)))


def test_self_alias_facts_reach_method_and_constructor_producers() -> None:
    compiler, modules = _compile("""\
from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
        alias = self
        alias.value = value
    def touch(self) -> int32:
        alias = self
        alias.value = 7
        return self.value
""")
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    method = next(fn for node, fn in ctx.thir_functions.items() if node.name == "touch")
    ctor = next(iter(ctx.thir_constructors.values()))
    for body in (method.body, ctor.body):
        alias = next(stmt for stmt in body if isinstance(stmt, th.THIRVarDecl) and stmt.name == "alias")
        assert isinstance(alias.init, th.THIRSelf)
        assert alias.alias_binding is not None and alias.alias_binding.source == "self"


@pytest.mark.parametrize("source,node_kind", [
    ("""\
class Special:
    stored: int32
    @property
    def value(self) -> int32:
        return self.stored
""", th.THIRMethodCall),
    ("""\
from typing import Final
class Special:
    value: Final[int32] = 4
""", th.THIRClassConstant),
    ("""\
from tpy.extern import native, native_field
@native
class Special:
    value: int32 = native_field("external_value")
""", th.THIRFieldAccess),
    ("""\
from tpy import auto_readonly
class Special:
    target: Cell
    @auto_readonly
    def __deref__(self) -> Cell:
        return self.target
""", th.THIRFieldAccess),
])
def test_indirect_or_special_fields_do_not_acquire_direct_storage_facts(
    source: str, node_kind: type[th.THIRExpr],
) -> None:
    compiler, modules = _compile(SOURCE + source + """\
def excluded(value: Special) -> int32:
    return value.value
""")
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "excluded")
    ret = fn.body[0]
    assert isinstance(ret, th.THIRReturn) and isinstance(ret.value, node_kind)
    if isinstance(ret.value, th.THIRFieldAccess):
        assert ret.value.field_identity is None
    not_covered(fn, "unsupported" if fn.params[0].borrowed_record is None
                or node_kind is not th.THIRFieldAccess else "unsupported metadata")
