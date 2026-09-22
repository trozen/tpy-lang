"""Production copy writes retain the backing chosen for live and dead aliases."""

from textwrap import indent

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .collect import dump_codegen_mir
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRCopy, MIRFunction, MIRMove, MIRNotCovered, MIRRecordWriteMode
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage
from .test_cyclic_record_lower import nodes
from .test_retention import analyze
from .testutil import ContainerValue, Reference, execute


CELL_SOURCE = '''from tpy import int32, copy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
'''


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("retained", [False, True])
def test_existing_replacement_facts_admit_source_copies(optional: bool, retained: bool) -> None:
    annotation = ": Cell | None" if optional else ""
    save = f"    saved{annotation} = current\n" if retained else ""
    read = ("    if saved is not None:\n        saved.value = 5\n        return current.value\n"
            if retained and optional else "    saved.value = 5\n    return current.value\n"
            if retained else "    return current.value\n")
    if optional:
        read = "    if current is None:\n        return 0\n" + read
    source = CELL_SOURCE + f'''
def replace_copy(source: Cell, n: int32) -> int32:
    current{annotation} = Cell(1)
{save}    for i in range(n):
        current = copy(source)
        current.value = 9
{read}    return 0
'''
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    body = next(fn for node, fn in ctx.thir_functions.items() if node.name == "replace_copy")
    fn = lower_function(body, MIRBodyId("reused", "copy"), kind=MIRBodyKind.FREE_FUNCTION,
                        definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(fn, MIRFunction), fn
    write, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRCopy))
    assert write.storage_write.mode is (MIRRecordWriteMode.OWN_SITE if retained else MIRRecordWriteMode.IN_PLACE)
    assert ("current = &*(__slot_2 = Cell(source));" if retained else "(*current) = Cell(source);") in cpp
    field = fn.records[0].fields[0].id
    for n in (0, 3):
        heap = {0: {field: 3}}
        assert execute(fn, Reference(0), n, heap=heap) == (9 if n else 5 if retained else 1)
        assert heap[0][field] == 3
        assert len(heap) == (3 if retained and n else 2)
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()


HOIST_SOURCE = CELL_SOURCE + '''
    def method(self, flag: bool) -> int32:
        if flag:
            duplicate = copy(self)
        else:
            return 0
        alias = duplicate
        alias.value = 7
        return duplicate.value

from tpy import readonly
from tpy import copy as clone
import tpy

def branch(source: Cell, flag: bool) -> int32:
    if flag:
        duplicate = copy(source)
    else:
        return 0
    alias = duplicate
    alias.value = 7
    return duplicate.value

def readonly_copy(source: readonly[Cell], flag: bool) -> int32:
    if flag:
        duplicate = clone(source)
    else:
        return 0
    duplicate.value = 7
    return source.value

def qualified(source: Cell, flag: bool) -> int32:
    if flag:
        duplicate = tpy.copy(source)
    else:
        return 0
    source.value = 7
    return duplicate.value

def repeated(source: Cell, run: bool) -> int32:
    second = False
    while run:
        duplicate = copy(source)
        if second:
            break
        second = True
    else:
        return 0
    alias = duplicate
    alias.value = 7
    return duplicate.value

def ranged(source: Cell, count: int32) -> int32:
    for i in range(count):
        duplicate = copy(source)
        if i == 2:
            break
    else:
        return 0
    alias = duplicate
    alias.value = 7
    return duplicate.value

def native(source: Cell, items: list[int32]) -> int32:
    for item in items:
        duplicate = copy(source)
        if item == 2:
            break
    else:
        return 0
    alias = duplicate
    alias.value = 7
    return duplicate.value

def nested(source: Cell, count: int32, skip: bool, stop: bool) -> int32:
    result = 0
    for i in range(count):
        if skip:
            continue
        if stop:
            return result
        if i == 0:
            duplicate = copy(source)
        else:
            continue
        alias = duplicate
        alias.value = 7
        result = duplicate.value
    return result

class Runner:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        source = Cell(3)
        if flag:
            duplicate = copy(source)
        else:
            return
        alias = duplicate
        alias.value = 7
        self.value = duplicate.value

    @staticmethod
    def static(source: Cell, flag: bool) -> int32:
        if flag:
            duplicate = copy(source)
        else:
            return 0
        duplicate.value = 7
        return source.value

class Switch:
    value: bool
    def __init__(self, value: bool):
        self.value = value

def boolean(flag: bool) -> bool:
    source = Switch(False)
    if flag:
        duplicate = copy(source)
    else:
        return False
    alias = duplicate
    alias.value = True
    return duplicate.value and not source.value
'''

Hoists = tuple[dict[str, th.THIRNode], dict[str, MIRFunction], str]


@pytest.fixture(scope="module")
def hoists() -> Hoists:
    compiler, modules = _compile(HOIST_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("reused", name), definitions=definitions,
                                  kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        if ctor.record_name == "Runner":
            functions["Runner"] = ctor
            bodies["Runner"] = lower_constructor(ctor, MIRBodyId("reused", "Runner"), definitions=definitions)
    for name, fn in bodies.items():
        assert isinstance(fn, MIRFunction), (name, fn)
    dumped = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions, compiler.thir_reject_by_node)
    return functions, bodies, dumped


@pytest.mark.parametrize("name", ["branch", "readonly_copy", "qualified", "repeated", "ranged", "native",
                                   "nested", "Runner", "method", "static", "boolean"])
def test_produced_copy_facts_target_optional_backing(hoists: Hoists, name: str) -> None:
    assignment, = (n for n in nodes(hoists[0][name]) if isinstance(n, th.THIRAssign)
                   and isinstance(n.value, th.THIRCopy))
    assert assignment.optional_record_assignment == th.THIRBorrowedRecord(assignment.value.result_type, False)
    fn = hoists[1][name]
    write, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRCopy))
    assert write.storage_write.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("name", ["branch", "readonly_copy", "qualified", "repeated", "ranged", "native",
                                   "method", "static"])
def test_hoisted_copy_keeps_aliases_shared_and_source_independent(hoists: Hoists, name: str) -> None:
    fn = hoists[1][name]
    field = fn.records[0].fields[0].id
    for run in (False, True):
        heap = {0: {field: 3}}
        count = 3 if run else 0
        argument = ContainerValue(tuple(range(count))) if name == "native" else count if name == "ranged" else run
        expected = 3 if name in ("readonly_copy", "qualified", "static") else 7
        assert execute(fn, Reference(0), argument, heap=heap) == (expected if run else 0)
        assert heap[0][field] == (7 if run and name == "qualified" else 3)
        # Repeated writes engage the same backing, not one object per trip.
        assert len(heap) == (2 if run else 1)


@pytest.mark.parametrize("skip,stop", [(False, False), (False, True), (True, False)])
def test_hoisted_copy_skipped_paths_and_constructor_tail(hoists: Hoists, skip: bool, stop: bool) -> None:
    fn = hoists[1]["nested"]
    field = fn.records[0].fields[0].id
    for count in (0, 3):
        heap = {0: {field: 3}}
        written = bool(count) and not (skip or stop)
        assert execute(fn, Reference(0), count, skip, stop, heap=heap) == (7 if written else 0)
        assert len(heap) == (2 if written else 1)
    ctor = hoists[1]["Runner"]
    field = next(r.fields[0].id for r in ctor.records if r.type.name == "Cell")
    for flag in (False, True):
        heap = {}
        execute(ctor, Reference(0), flag, heap=heap)
        assert tuple(heap[0].values()) == (7 if flag else 0,)
        assert heap[1][field] == 3
        assert execute(hoists[1]["boolean"], flag) is flag


def test_debug_collection_includes_optional_copy_writes(hoists: Hoists) -> None:
    dumped = hoists[2]
    assert "optional_assign" in dumped and " = copy " in dumped
    uncovered = [line for line in dumped.splitlines() if "MIR not covered" in line]
    assert len(uncovered) == 2
    assert any("__tpy_init" in line and "module initialization" in line for line in uncovered)
    assert any("Runner.static" in line and "body kind and receiver mismatch" in line for line in uncovered)


@pytest.mark.parametrize("position,header", [
    (position, header)
    for position in ("free", "method", "static", "constructor")
    for header in ("for i in range(count)", "for i in items", "while run")
    if position != "constructor" or header != "for i in items"
])
def test_fresh_move_source_into_guarded_backing(position: str, header: str) -> None:
    body = '''result = 0
second = False
LOOP:
    original = Cell(3)
    if flag:
        target = original
    else:
        break
    alias = target
    alias.value = 9
    result = target.value
    if stop:
        break
    run = not second
    second = True
'''.replace("LOOP", header)
    args = "count: int32, items: list[int32], run: bool, flag: bool, stop: bool"
    if position == "constructor":
        # Constructor parameter admission is scalar-only.
        args = "count: int32, run: bool, flag: bool, stop: bool"
    if position == "free":
        source = CELL_SOURCE + f"\ndef moved({args}) -> int32:\n" + indent(body + "return result\n", "    ")
    else:
        prefix = "class Runner:\n    value: int32\n"
        if position == "constructor":
            declaration = f"    def __init__(self, {args}):\n"
            body = "self.value = 0\n" + body + "self.value = result\n"
        else:
            prefix += "    def __init__(self):\n        self.value = 0\n"
            declaration = ("    @staticmethod\n" if position == "static" else "")
            declaration += f"    def moved({'' if position == 'static' else 'self, '}{args}) -> int32:\n"
            body += "return result\n"
        source = CELL_SOURCE + "\n" + prefix + declaration + indent(body, "        ")
    compiler, modules = _compile(source)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    if position == "constructor":
        function = next(c for c in ctx.thir_constructors.values() if c.record_name == "Runner")
        fn = lower_constructor(function, MIRBodyId("reused", "move_ctor"), definitions=definitions)
    else:
        function = next(fn for node, fn in ctx.thir_functions.items() if node.name == "moved")
        fn = lower_function(function, MIRBodyId("reused", "move"), definitions=definitions,
                            kind=MIRBodyKind.METHOD if function.receiver else MIRBodyKind.FREE_FUNCTION)
    assert isinstance(fn, MIRFunction), fn
    assignment, = (n for n in nodes(function) if isinstance(n, th.THIRAssign) and isinstance(n.value, th.THIRMove))
    assert assignment.optional_record_assignment is not None
    write, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRMove))
    assert write.storage_write.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
    assert analyze(fn).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()
    dumped = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, definitions, compiler.thir_reject_by_node)
    if position == "static":
        # The debug collector still classifies every class member as a receiver method.
        assert "MIR not covered: body kind and receiver mismatch" in dumped
    else:
        assert " = move " in dumped and "optional_assign" in dumped
    for run, flag, stop, trips in ((False, True, False, 0), (True, True, False, 2),
                                  (True, True, True, 1), (True, False, False, 1)):
        heap = {}
        arguments = (2 if run else 0, ContainerValue((1, 2) if run else ()), run, flag, stop)
        if position == "constructor":
            arguments = (2 if run else 0, run, flag, stop)
        if position in ("method", "constructor"):
            heap[0] = {}
            arguments = (Reference(0), *arguments)
        result = execute(fn, *arguments, heap=heap)
        expected = 9 if run and flag else 0
        assert (next(iter(heap[0].values())) if position == "constructor" else result) == expected
        # Each loop activation owns a source and, only on the taken guard, its destination.
        assert len(heap) == trips * (2 if flag else 1) + (position in ("method", "constructor"))
        field = next(r.fields[0].id for r in fn.records if r.type.name == "Cell")
        assert sum(fields.get(field) == 3 for fields in heap.values()) == trips


@pytest.mark.parametrize("move", [False, True])
def test_escape_hoisted_transfer_uses_one_body_backing_across_iterations(move: bool) -> None:
    transfer = "original = Cell(3)\n        duplicate = original" if move else "duplicate = copy(source)"
    source = CELL_SOURCE + f'''
def escaped(source: Cell, n: int32) -> int32:
    holder = Cell(0)
    for i in range(n):
        {transfer}
        holder = duplicate
    holder.value = 7
    return source.value
'''
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    body = next(fn for node, fn in ctx.thir_functions.items() if node.name == "escaped")
    fn = lower_function(body, MIRBodyId("reused", "escaped"), kind=MIRBodyKind.FREE_FUNCTION,
                        definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(fn, MIRFunction), fn
    write, = (s for s in analyze_storage(fn).writes.values() if isinstance(s.value, MIRMove if move else MIRCopy))
    assert write.storage_write.mode is MIRRecordWriteMode.OWN_SITE
    assert ("Cell* duplicate = &*(__slot_2 = std::move(original));" if move else
            "Cell* duplicate = &*(__slot_2 = Cell(source));") in cpp
    field = fn.records[0].fields[0].id
    for n in (0, 3):
        heap = {0: {field: 3}}
        assert execute(fn, Reference(0), n, heap=heap) == 3
        assert len(heap) == (3 + (n if move else 0) if n else 2)
    assert inspect_scope_lifetimes(fn).conflicts == ()


@pytest.mark.parametrize("move", [False, True])
def test_derived_to_base_hoist_compiles_without_a_same_record_fact(move: bool) -> None:
    source = CELL_SOURCE + '''
class Derived(Cell):
    other: int32
    def __init__(self, value: int32):
        super().__init__(value)
        self.other = 4

def example(flag: bool) -> int32:
    original = Derived(3)
    if flag:
        target: Cell = TRANSFER
    else:
        return 0
    target.value = 9
    return target.value
'''.replace("TRANSFER", "original" if move else "copy(original)")
    compiler, modules = _compile(source)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    function = next(fn for node, fn in ctx.thir_functions.items() if node.name == "example")
    assignment, = (n for n in nodes(function) if isinstance(n, th.THIRAssign)
                   and isinstance(n.value, th.THIRMove if move else th.THIRCopy))
    assert assignment.optional_record_assignment is None
    assert assignment.value.result_type != assignment.target.result_type
    assert ("target = std::move(original);" if move else "target = Derived(original);") in cpp
    result = lower_function(function, MIRBodyId("reused", "derived"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRNotCovered), result
