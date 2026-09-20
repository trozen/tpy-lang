"""Field borrows capture inline subobjects, independently of holder reseats."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _assert_rejects_at, _compile, _entry, _strict_reject
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL, NoneType, OptionalType, UnionType
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRBorrow, MIRDeref, MIRField, MIRFieldId,
    MIRFunction, MIRNotCovered, MIROptionalConstruct, MIROptionalLayout,
    MIROptionalPayload, MIRPlace, MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleElement,
    MIRUnionConstruct, MIRUnionLayout, MIRUnionPayload, MIRValueKind,
)
from .testutil import Heap, OptionalValue, Reference, UnionValue, execute
from .validate import MIRPresenceError, MIRValidationError, validate_function


SOURCE = """\
from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Outer:
    inner: Cell
    def __init__(self, value: int32):
        self.inner = Cell(value)
    def method(self) -> int32:
        saved = self.inner
        self.inner.value = 8
        return saved.value

class Deep:
    outer: Outer
    def __init__(self, value: int32):
        self.outer = Outer(value)

class ReadOuter:
    inner: readonly[Cell]
    def __init__(self, value: int32):
        self.inner = Cell(value)

class Observer:
    value: int32
    def __init__(self, outer: Outer):
        saved = outer.inner
        outer.inner.value = 19
        self.value = saved.value

def direct(outer: Outer) -> int32:
    # The local must retain the field's identity across the later write.
    saved = outer.inner
    outer.inner.value = 7
    return saved.value

def nested(root: Deep) -> int32:
    parent = root.outer
    saved = parent.inner
    # Inline field traversal adds no pointer indirection between members.
    root.outer.inner.value = 9
    return saved.value

def reseat(a: Outer, b: Outer, flag: bool) -> int32:
    current = a
    saved = current.inner
    if flag:
        current = b
    # Changing current must not retarget the earlier captured field.
    current.inner.value = 11
    return saved.value

def field_reseat(a: Outer, b: Outer, flag: bool) -> int32:
    current = a.inner
    if flag:
        current = b.inner
    current.value = 12
    return a.inner.value

def readonly_alias(outer: readonly[Outer], other: Outer) -> int32:
    saved = outer.inner
    # Readonly restricts access, not observation through another alias.
    other.inner.value = 13
    return saved.value

def scalar_snapshot(outer: Outer) -> int32:
    saved = outer.inner.value
    outer.inner.value = 14
    return saved

def readonly_field(outer: ReadOuter) -> int32:
    saved = outer.inner
    return saved.value

def tuple_capture(outer: Outer) -> int32:
    saved = outer.inner
    pair = (saved, 1)
    outer.inner.value = 15
    return pair[0].value

def singleton_capture(outer: Outer) -> int32:
    saved = outer.inner
    pair = (saved,)
    outer.inner.value = 16
    return pair[0].value

def tuple_root(outer: Outer) -> int32:
    pair = (outer, 1)
    pair[0].inner.value = 17
    return pair[0].inner.value

def optional_root(outer: Outer | None) -> int32:
    if outer is None:
        return 0
    outer.inner.value = 20
    return outer.inner.value

def union_root(outer: Outer | Cell) -> int32:
    if isinstance(outer, Outer):
        outer.inner.value = 21
        return outer.inner.value
    return 0

def loop(outer: Outer, flag: bool) -> int32:
    saved = outer.inner
    while flag:
        saved.value = 22
        flag = False
    return outer.inner.value

def replace_field(outer: Outer) -> int32:
    saved = outer.inner
    outer.inner = Cell(23)
    return saved.value

def owning() -> int32:
    outer = Outer(1)
    return outer.inner.value
"""

Artifacts = tuple[dict[str, th.THIRFunction], tuple[th.THIRConstructor, ...]]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    return ({node.name: fn for node, fn in ctx.thir_functions.items()}, tuple(ctx.thir_constructors.values()))


def lower(fn: th.THIRFunction) -> MIRFunction:
    result = lower_function(fn, MIRBodyId("nested", fn.name), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRFunction), result
    return result


def objects(functions: dict[str, th.THIRFunction]) -> tuple[MIRFieldId, MIRFieldId, MIRFieldId, Heap]:
    outer = functions["direct"].params[0].borrowed_record.type
    cell = functions["direct"].body[0].storage_borrow.type
    deep = functions["nested"].params[0].borrowed_record.type
    inner, value, parent = MIRFieldId(outer, "inner"), MIRFieldId(cell, "value"), MIRFieldId(deep, "outer")
    return inner, value, parent, {1: {inner: {value: 1}}, 2: {inner: {value: 2}},
                                3: {parent: {inner: {value: 3}}}}


@pytest.mark.parametrize("name,expected", [("direct", 7), ("tuple_capture", 15),
                                         ("singleton_capture", 16), ("tuple_root", 17)])
def test_field_aliases_share_mutations(artifacts: Artifacts, name: str, expected: int) -> None:
    functions, _ = artifacts
    inner, value, _, heap = objects(functions)
    assert execute(lower(functions[name]), Reference(1), heap=heap) == expected
    assert heap[1][inner][value] == expected


def test_nested_inline_places_have_one_root_dereference(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    inner, value, parent, heap = objects(functions)
    fn = lower(functions["nested"])
    assert execute(fn, Reference(3), heap=heap) == 9
    assert heap[3][parent][inner][value] == 9
    path = next(stmt.target.projections for block in fn.blocks for stmt in block.statements
                if len(stmt.target.projections) == 4)
    assert isinstance(path[0], MIRDeref) and all(isinstance(p, MIRField) for p in path[1:])
    assert dump_function(fn) == dump_function(lower(functions["nested"]))


@pytest.mark.parametrize("name,written", [("reseat", 11), ("field_reseat", 12)])
@pytest.mark.parametrize("same", [False, True])
@pytest.mark.parametrize("flag", [False, True])
def test_holder_reseats_preserve_other_field_aliases(artifacts: Artifacts, name: str, written: int,
                                                   same: bool, flag: bool) -> None:
    functions, _ = artifacts
    inner, value, _, heap = objects(functions)
    second = 1 if same else 2
    assert execute(lower(functions[name]), Reference(1), Reference(second), flag, heap=heap) == (
        written if same or not flag else 1)
    assert heap[second if flag else 1][inner][value] == written


def test_readonly_alias_and_scalar_snapshot_differ(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    _, _, _, heap = objects(functions)
    assert execute(lower(functions["readonly_alias"]), Reference(1), Reference(1), heap=heap) == 13
    assert execute(lower(functions["scalar_snapshot"]), Reference(1), heap=heap) == 13
    assert execute(lower(functions["loop"]), Reference(1), True, heap=heap) == 22


def test_wrapper_roots_keep_selection_proofs(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    _, _, _, heap = objects(functions)
    assert execute(lower(functions["optional_root"]), OptionalValue(), heap=heap) == 0
    assert execute(lower(functions["optional_root"]), OptionalValue(Reference(1)), heap=heap) == 20
    fn = lower(functions["union_root"])
    outer = functions["direct"].params[0].borrowed_record.type
    layout = fn.slots[0].union_layout
    alternative = next(i for i, member in enumerate(layout.elements) if member.type == outer)
    assert execute(fn, UnionValue(alternative, Reference(1)), heap=heap) == 21


@pytest.mark.parametrize("name", ["optional_root", "union_root"])
def test_field_borrow_requires_current_wrapper_proof(artifacts: Artifacts, name: str) -> None:
    functions, _ = artifacts
    fn = lower(functions[name])
    direct = lower(functions["direct"])
    holder = replace(direct.slots[1], id=MIRSlotId(fn.id, len(fn.slots)), name="saved")
    block = next(block for block in fn.blocks if any(stmt.target.projections for stmt in block.statements))
    holder = replace(holder, residence=block.region)
    store = next(stmt for stmt in block.statements if stmt.target.projections)
    source = replace(store.target, projections=store.target.projections[:-1])
    if name == "union_root":
        param = fn.slots[0]
        outer = functions["direct"].params[0].borrowed_record.type
        alternative = next(i for i, member in enumerate(param.union_layout.elements) if member.type == outer)
        source = MIRPlace(param.id, (MIRUnionPayload(alternative), *source.projections))
    borrow = replace(store, target=MIRPlace(holder.id), value=MIRBorrow(source))
    guarded = replace(fn, slots=(*fn.slots, holder), blocks=tuple(
        replace(b, statements=(borrow, *b.statements)) if b.id == block.id else b for b in fn.blocks))
    validate_function(guarded)
    # Moving the same borrow above its guard must lose permission to select the payload.
    unguarded = replace(fn, slots=(*fn.slots, replace(holder, residence=fn.blocks[0].region)), blocks=tuple(
        replace(b, statements=(borrow, *b.statements)) if b.id == fn.entry else b for b in fn.blocks))
    with pytest.raises(MIRPresenceError):
        validate_function(unguarded)


@pytest.mark.parametrize("union", [False, True])
def test_captured_field_survives_wrapper_replacement(artifacts: Artifacts, union: bool) -> None:
    functions, _ = artifacts
    fn = lower(functions["direct"])
    param = fn.slots[0]
    wrapper_id = MIRSlotId(fn.id, len(fn.slots))
    if union:
        wrapper = MIRSlot(wrapper_id, UnionType((NoneType(), param.type)), MIRSlotKind.LOCAL,
                          value_kind=MIRValueKind.UNION,
                          union_layout=MIRUnionLayout((None, MIRTupleElement(
                              param.type, MIRValueKind.BORROWED_RECORD))))
        construct, clear = MIRUnionConstruct(1, param.id), MIRUnionConstruct(0)
        payload = MIRUnionPayload(1)
    else:
        wrapper = MIRSlot(wrapper_id, OptionalType(param.type), MIRSlotKind.LOCAL,
                          value_kind=MIRValueKind.OPTIONAL,
                          optional_layout=MIROptionalLayout(param.type, MIRValueKind.BORROWED_RECORD))
        construct, clear = MIROptionalConstruct(param.id), MIROptionalConstruct()
        payload = MIROptionalPayload()
    block = fn.blocks[0]
    borrow = block.statements[0]
    source = MIRPlace(wrapper_id, (payload, *borrow.value.source.projections))
    borrow = replace(borrow, value=MIRBorrow(source))
    init = MIRAssign(MIRPlace(wrapper_id), construct)
    replace_wrapper = MIRAssign(MIRPlace(wrapper_id), clear)
    wrapper = replace(wrapper, residence=block.region)
    fn = replace(fn, slots=(*fn.slots, wrapper), blocks=(replace(
        block, statements=(init, borrow, replace_wrapper, *block.statements[1:])),))
    validate_function(fn)
    _, _, _, heap = objects(functions)
    # Clearing the wrapper cannot detach a field already captured from its referent.
    assert execute(fn, Reference(1), heap=heap) == 7


def test_borrow_facts_reach_sibling_producers(artifacts: Artifacts) -> None:
    functions, constructors = artifacts
    assert functions["method"].body[0].storage_borrow is not None
    observer = next(ctor for ctor in constructors if ctor.record_name == "Observer")
    assert any(getattr(stmt, "storage_borrow", None) is not None for stmt in observer.body)
    result = lower_function(functions["method"], MIRBodyId("nested", "method"), kind=MIRBodyKind.METHOD)
    assert isinstance(result, MIRFunction)
    _, _, _, heap = objects(functions)
    assert execute(result, Reference(1), heap=heap) == 8


@pytest.mark.parametrize("name", ["replace_field", "owning"])
def test_nested_owning_operations_remain_uncovered(artifacts: Artifacts, name: str) -> None:
    functions, constructors = artifacts
    result = lower_function(functions[name], MIRBodyId("nested", name), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(constructors))
    assert isinstance(result, MIRNotCovered)
    assert result.reason == ("unsupported record fields" if name == "owning"
                             else "record field replacement is unsupported")


def test_storage_borrow_fact_must_agree_with_source(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = functions["direct"]
    decl = fn.body[0]
    broken = replace(decl.init, field_identity=replace(decl.init.field_identity, type=BOOL))
    with pytest.raises(THIRValidationError, match="storage borrow disagrees"):
        validate_thir(replace(fn, body=(replace(decl, init=broken), *fn.body[1:])))


def test_declared_readonly_field_restricts_captured_access(artifacts: Artifacts) -> None:
    functions, _ = artifacts
    fn = lower(functions["readonly_field"])
    holder = next(slot for slot in fn.slots if slot.name == "saved")
    assert holder.readonly
    bad = replace(fn, slots=tuple(replace(slot, readonly=False) if slot == holder else slot
                                 for slot in fn.slots))
    with pytest.raises(MIRValidationError, match="borrow increases access"):
        validate_function(bad)


@pytest.mark.parametrize("body,landmark", [
    ("def sample(root: Deep) -> int32:\n    saved = root.outer.inner\n    return saved.value\n",
     "stmt.var_decl:decl.slot_type"),
    ("def sample(outer: Outer) -> int32:\n    pair = (outer,)\n    saved = pair[0].inner\n    return saved.value\n",
     "stmt.var_decl:decl.slot_type"),
    ("def sample(outer: Outer) -> int32:\n    saved = outer.inner\n    saved = Cell(23)\n    return outer.inner.value\n",
     "stmt.var_decl:decl.reseat_source"),
])
def test_existing_frontend_alias_gates_remain(body: str, landmark: str) -> None:
    _, reasons = _strict_reject(SOURCE + body)
    _assert_rejects_at(reasons, "body:" + landmark)


def test_noncopyable_field_is_borrowed() -> None:
    source = """\
from tpy import int32, nocopy
@nocopy
class Item:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Holder:
    item: Item
    def __init__(self, value: int32):
        self.item = Item(value)
def sample(holder: Holder) -> int32:
    saved = holder.item
    holder.item.value = 7
    return saved.value
"""
    compiler, modules = _compile(source)
    sources, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert sources
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "sample")
    owner = fn.params[0].borrowed_record.type
    item = fn.body[0].storage_borrow.type
    field, value = MIRFieldId(owner, "item"), MIRFieldId(item, "value")
    assert execute(lower(fn), Reference(1), heap={1: {field: {value: 1}}}) == 7


@pytest.mark.parametrize("change,message", [
    ("owner", "field owner mismatch"),
    ("deref", "dereference needs reference holder"),
    ("scalar", "borrow type mismatch"),
    ("readonly", "borrow increases access"),
])
def test_malformed_storage_borrows_are_rejected(artifacts: Artifacts, change: str, message: str) -> None:
    functions, _ = artifacts
    fn = lower(functions["direct"])
    block = fn.blocks[0]
    stmt = block.statements[0]
    assert isinstance(stmt.value, MIRBorrow)
    source = stmt.value.source
    member = source.projections[-1]
    if change == "owner":
        source = replace(source, projections=(*source.projections[:-1],
                         replace(member, id=MIRFieldId(member.type, "inner"))))
    elif change == "deref":
        source = replace(source, projections=(*source.projections, MIRDeref()))
    elif change == "scalar":
        scalar = functions["direct"].body[-1].value.field_identity
        source = replace(source, projections=(*source.projections,
                         MIRField(MIRFieldId(scalar.owner, scalar.name), scalar.type)))
    else:
        fn = replace(fn, slots=tuple(replace(slot, readonly=True) if slot.id == source.root else slot
                                    for slot in fn.slots))
    changed = replace(stmt, value=MIRBorrow(source))
    fn = replace(fn, blocks=(replace(block, statements=(changed, *block.statements[1:])), *fn.blocks[1:]))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)
