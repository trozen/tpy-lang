"""Source copies and replacements preserve their emitted storage across loops."""

from dataclasses import replace
from collections.abc import Iterator

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import _iter_children
from .collect import dump_codegen_mir
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRCopy, MIRFunction, MIRMove, MIRNotCovered, MIRRecordWriteMode
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .testutil import ContainerValue, Reference, execute


SOURCE = '''from tpy import int32, copy, readonly
from tpy import copy as clone
import tpy

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

    def copied_self(self, n: int32) -> int32:
        result = self.value
        for i in range(n):
            duplicate = copy(self)
            duplicate.value = i
            result = self.value
        return result

def copied(source: Cell, n: int32) -> int32:
    result = source.value
    for i in range(n):
        duplicate = copy(source)
        duplicate.value = i
        result = source.value
    return result

def copied_readonly(source: readonly[Cell], run: bool) -> int32:
    result = source.value
    second = False
    while run:
        duplicate = clone(source)
        duplicate.value = 9
        result = source.value
        run = not second
        second = True
    return result

def qualified(source: Cell, n: int32) -> int32:
    result = source.value
    for i in range(n):
        duplicate = tpy.copy(source)
        duplicate.value = 9
        result = source.value
    return result

def mutation(source: Cell, run: bool) -> int32:
    result = 0
    while run:
        duplicate = copy(source)
        source.value = 7
        result = duplicate.value
        run = False
    return result

def replaced(n: int32) -> int32:
    current = Cell(1)
    for i in range(n):
        current = Cell(2 if current.value == 1 else 1)
    return current.value

def optional(n: int32) -> int32:
    current: Cell | None = Cell(1)
    for i in range(n):
        if current is not None:
            current = Cell(2 if current.value == 1 else 1)
    if current is not None:
        return current.value
    return 0

def twice(n: int32) -> int32:
    current = Cell(1)
    for i in range(n):
        current = Cell(2 if current.value == 1 else 1)
        current = Cell(2 if current.value == 1 else 1)
    return current.value

def native(source: Cell, items: list[int32]) -> int32:
    result = source.value
    current = Cell(0)
    for item in items:
        duplicate = copy(source)
        duplicate.value = item
        current = Cell(duplicate.value)
        result = current.value
    return result

def nested(source: Cell, n: int32, stop: bool, skip: bool) -> int32:
    result = 0
    for i in range(n):
        for j in range(n):
            if skip:
                continue
            duplicate = copy(source)
            duplicate.value = j
            result = source.value
            if stop:
                break
        if stop:
            return result
    return result

class Runner:
    value: int32
    def __init__(self, n: int32):
        self.value = 0
        source = Cell(5)
        current = Cell(1)
        for i in range(n):
            duplicate = copy(source)
            duplicate.value = i
            current = Cell(duplicate.value)
            self.value = current.value

    def method(self, source: Cell, n: int32) -> int32:
        for i in range(n):
            duplicate = copy(source)
            duplicate.value = i
            self.value = source.value
        return self.value
'''

Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


def nodes(root: th.THIRNode) -> Iterator[th.THIRNode]:
    yield root
    for child in _iter_children(root):
        yield from nodes(child)


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("cyclic", name), definitions=definitions,
                                   kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        if ctor.record_name == "Runner":
            bodies["Runner"] = lower_constructor(ctor, MIRBodyId("cyclic", "Runner"), definitions=definitions)
    return functions, bodies, definitions, cpp


@pytest.mark.parametrize("name", ["copied", "copied_readonly", "qualified", "mutation", "copied_self",
                                   "replaced", "optional", "twice", "native", "nested", "method", "Runner"])
def test_production_bodies_are_covered(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[1][name]
    assert isinstance(fn, MIRFunction), fn
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("name", ["copied", "copied_readonly", "qualified", "copied_self"])
def test_explicit_copy_preserves_source_and_owns_each_iteration(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[1][name]
    assert isinstance(fn, MIRFunction), fn
    field = fn.records[0].fields[0].id
    for trips in (0, 1, 3):
        heap = {0: {field: 5}}
        count = bool(trips) if name == "copied_readonly" else trips
        assert execute(fn, Reference(0), count, heap=heap) == 5
        assert heap[0][field] == 5
        assert len(heap) == 1 + (2 if count and name == "copied_readonly" else count)
    declarations = [n for n in nodes(artifacts[0][name]) if isinstance(n, th.THIRVarDecl) and n.name == "duplicate"]
    decl, = declarations
    assert isinstance(decl.init, th.THIRCopy)
    assert decl.owned_storage is not None and decl.storage_placement is th.THIRStoragePlacement.SCOPE
    assert any(isinstance(s.value, MIRCopy) for s in analyze_storage(fn).writes.values())


@pytest.mark.parametrize("name", ["replaced", "optional"])
def test_replacement_reuses_backing_and_reads_rhs_first(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[1][name]
    assert isinstance(fn, MIRFunction), fn
    for trips in (0, 1, 2, 3):
        heap = {}
        assert execute(fn, trips, heap=heap) == (2 if trips % 2 else 1)
        assert len(heap) == 1
    assert any(s.storage_write.mode is MIRRecordWriteMode.IN_PLACE for s in analyze_storage(fn).writes.values())


def test_source_mutation_does_not_reach_the_copy(artifacts: Artifacts) -> None:
    fn = artifacts[1]["mutation"]
    assert isinstance(fn, MIRFunction), fn
    field = fn.records[0].fields[0].id
    heap = {0: {field: 3}}
    assert execute(fn, Reference(0), True, heap=heap) == 3
    assert heap[0][field] == 7


def test_two_replacement_sites_share_one_live_backing(artifacts: Artifacts) -> None:
    fn = artifacts[1]["twice"]
    for trips in (0, 1, 3):
        heap = {}
        assert execute(fn, trips, heap=heap) == 1
        assert len(heap) == 1
    writes = [s for s in analyze_storage(fn).writes.values() if s.storage_write.mode is MIRRecordWriteMode.IN_PLACE]
    assert len(writes) == 2 and writes[0].target == writes[1].target


def test_native_iteration_nested_exits_and_callable_positions(artifacts: Artifacts) -> None:
    bodies = artifacts[1]
    fn = bodies["native"]
    field = fn.records[0].fields[0].id
    for elements, expected in (((), 5), ((2, 8, 4), 4)):
        heap = {0: {field: 5}}
        assert execute(fn, Reference(0), ContainerValue(elements), heap=heap) == expected
        assert heap[0][field] == 5
    for n, stop, skip, expected in ((0, False, False, 0), (3, False, False, 5),
                                    (3, True, False, 5), (3, False, True, 0)):
        assert execute(bodies["nested"], Reference(0), n, stop, skip, heap={0: {field: 5}}) == expected
    receiver_field = next(r.fields[0].id for r in bodies["Runner"].records if r.type.name == "Runner")
    heap = {0: {}, 1: {field: 7}}
    execute(bodies["Runner"], Reference(0), 3, heap=heap)
    assert heap[0][receiver_field] == 2
    assert execute(bodies["method"], Reference(0), Reference(1), 3, heap=heap) == 7
    assert heap[1][field] == 7


def test_source_storage_fact_is_required(artifacts: Artifacts) -> None:
    fn = artifacts[0]["copied_readonly"]
    loop = fn.body[2]
    decl = replace(loop.body[0], owned_storage=None, storage_placement=None)
    fn = replace(fn, body=(*fn.body[:2], replace(loop, body=(decl, *loop.body[1:])), fn.body[-1]))
    result = lower_function(fn, MIRBodyId("cyclic", "missing"), definitions=artifacts[2], kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported metadata: cpp_type"


def test_codegen_collection_includes_loop_copy() -> None:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    dumped = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name,
                              MIRDefinitions(tuple(ctx.thir_constructors.values())), compiler.thir_reject_by_node)
    copied = dumped.split(f"fn {entry.name}::copied@", 1)[1].split("\nfn ", 1)[0]
    replaced = dumped.split(f"fn {entry.name}::replaced@", 1)[1].split("\nfn ", 1)[0]
    assert " = copy " in copied and "initialize_region" in copied
    assert "in_place" in replaced


@pytest.mark.parametrize("loop", ["while", "range", "native"])
def test_source_moves_reach_mir_with_scoped_storage(loop: str) -> None:
    header = {"while": "while run", "range": "for index in range(n)",
              "native": "for index in items"}[loop]
    source = SOURCE.split("    def copied_self")[0] + '''
def moved(run: bool, stop: bool, skip: bool, early: bool, items: list[int32], n: int32) -> int32:
    result = 0
    second = False
    LOOP:
        original = Cell(3)
        target = original
        target.value = 9
        result = target.value
        if early:
            return result
        if stop:
            break
        run = not second
        second = True
        if skip:
            continue
    return result
'''.replace("LOOP", header)
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    function = next(fn for node, fn in ctx.thir_functions.items() if node.name == "moved")
    target, = (n for n in nodes(function) if isinstance(n, th.THIRVarDecl) and n.name == "target")
    assert isinstance(target.init, th.THIRMove)
    assert target.owned_storage is not None
    assert target.storage_placement is th.THIRStoragePlacement.SCOPE
    fn = lower_function(function, MIRBodyId("cyclic", "moved"), kind=MIRBodyKind.FREE_FUNCTION,
                        definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(fn, MIRFunction), fn
    move, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRMove))
    assert move.storage_write.mode is MIRRecordWriteMode.INITIALIZE_REGION
    assert inspect_scope_lifetimes(fn).conflicts == ()
    entry = _entry(modules)
    dumped = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name,
                              MIRDefinitions(tuple(ctx.thir_constructors.values())), compiler.thir_reject_by_node)
    assert " = move " in dumped and "initialize_region" in dumped
    for run, stop, skip, early, count in ((False, False, False, False, 0),
                                         (True, False, False, False, 2),
                                         (True, True, False, False, 1),
                                         (True, False, True, False, 2),
                                         (True, False, False, True, 1)):
        heap = {}
        items = ContainerValue((2, 3) if run else ())
        assert execute(fn, run, stop, skip, early, items, 2 if run else 0, heap=heap) == (9 if count else 0)
        assert len(heap) == 2 * count
        assert sum(fields[fn.records[0].fields[0].id] == 9 for fields in heap.values()) == count


def test_hoisted_move_assigns_optional_backing_without_retagging_source() -> None:
    source = SOURCE.split("    def copied_self")[0] + '''
def moved(flag: bool) -> int32:
    original = Cell(3)
    if flag:
        target = original
    else:
        return 0
    return target.value
'''
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    function = next(fn for node, fn in ctx.thir_functions.items() if node.name == "moved")
    assert "target = std::move(original);" in cpp
    result = lower_function(function, MIRBodyId("cyclic", "hoisted"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRFunction), result
    write, = (s for s in analyze_storage(result).writes.values() if isinstance(s.value, MIRMove))
    assert write.storage_write.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
    assignment, = (n for n in nodes(function) if isinstance(n, th.THIRAssign) and isinstance(n.value, th.THIRMove))
    assert assignment.optional_record_assignment is not None
    assert assignment.value.form is assignment.value.value.form
    for flag in (False, True):
        heap = {}
        assert execute(result, flag, heap=heap) == (3 if flag else 0)
        assert len(heap) == (2 if flag else 1)
    assert inspect_scope_lifetimes(result).conflicts == ()


@pytest.mark.parametrize("member", ["custom_copy", "custom_move", "custom_destructor"])
def test_copy_metadata_does_not_admit_custom_effects(artifacts: Artifacts, member: str) -> None:
    fn = artifacts[0]["copied"]
    ctor = next(d.constructor for typ, d in artifacts[2].records.items() if typ.name == "Cell")
    ctor = replace(ctor, record_layout=replace(ctor.record_layout, **{member: True}))
    result = lower_function(fn, MIRBodyId("cyclic", member), definitions=MIRDefinitions((ctor,)),
                            kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered) and result.reason == "custom record special member"
