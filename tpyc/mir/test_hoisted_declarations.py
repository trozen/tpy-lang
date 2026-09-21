"""Predeclared holders become available at writes; backing keeps its emitted duration."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRRecordWrite,
    MIRRecordWriteMode, MIRReturn, MIRStorageDuration, MIRValueKind,
)
from .scope_lifetime import inspect_scope_lifetimes
from .test_retention import analyze
from .test_reuse import CELL_SOURCE, source_function
from .testutil import OptionalValue, Reference, execute
from .validate import MIRDefiniteAssignmentError, validate_function


SOURCE = '''from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

    def method(self, flag: bool) -> int32:
        if flag:
            local = Cell(1)
        else:
            local = Cell(2)
        alias = local
        local.value = 9
        return alias.value

    def constant_method(self) -> int32:
        while True:
            value = 1
            break
        return value

class ConstantObserver:
    value: int32
    def __init__(self):
        self.value = 0
        while True:
            value = 1
            break
        self.value = value

class Observer:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        if flag:
            local = Cell(1)
        else:
            local = Cell(2)
        alias = local
        local.value = 9
        self.value = alias.value

def scalar(flag: bool) -> int32:
    if flag:
        value = 1
    else:
        value = 2
    return value

def value_tuple(flag: bool) -> int32:
    if flag:
        value = (1, True)
    else:
        value = (2, False)
    return value[0]

def loop_tuple(flag: bool) -> int32:
    while flag:
        value = (1, True)
        break
    else:
        value = (2, False)
    return value[0]

def loop_scalar(flag: bool) -> int32:
    while flag:
        value = 1
        break
    else:
        value = 2
    return value

def borrowed(flag: bool, left: Cell, right: Cell) -> int32:
    if flag:
        value = left
    else:
        value = right
    alias = value
    value.value = 9
    return alias.value

def loop_borrowed(flag: bool, left: Cell, right: Cell) -> int32:
    while flag:
        value = left
        break
    else:
        value = right
    alias = value
    value.value = 9
    return alias.value

def record(flag: bool) -> int32:
    if flag:
        value = Cell(1)
    else:
        value = Cell(2)
    alias = value
    value.value = 9
    return alias.value

def escaped_loop(flag: bool) -> int32:
    saved = Cell(0)
    while flag:
        local = Cell(1)
        saved = local
        local.value = 9
        flag = False
    return saved.value

def ptr_optional(flag: bool, source: Cell | None) -> int32:
    if flag:
        value = source
    else:
        value = None
    if value is not None:
        value.value = 9
        return value.value
    return 0

def loop_optional(flag: bool, source: Cell | None) -> int32:
    while flag:
        value = source
        break
    else:
        value = None
    if value is not None:
        value.value = 9
        return value.value
    return 0

def readonly_record(flag: bool, left: readonly[Cell], right: readonly[Cell], owner: Cell) -> int32:
    if flag:
        value = left
    else:
        value = right
    owner.value = 9
    return value.value

def loop_readonly(flag: bool, left: readonly[Cell], right: readonly[Cell], owner: Cell) -> int32:
    while flag:
        value = left
        break
    else:
        value = right
    owner.value = 9
    return value.value

def readonly_tuple(flag: bool, left: Cell, right: Cell) -> int32:
    if flag:
        value = (left, 1)
    else:
        value = (right, 2)
    left.value = 9
    right.value = 8
    return value[0].value

def borrow_tuple(flag: bool, left: Cell, right: Cell) -> int32:
    if flag:
        value = (left, 1)
    else:
        value = (right, 2)
    value[0].value = 9
    return left.value if flag else right.value

def nested(flag: bool, choose: bool) -> int32:
    result = 0
    while flag:
        if choose:
            value = Cell(1)
        else:
            value = Cell(2)
        alias = value
        value.value = 9
        result = alias.value
        flag = False
    return result

def scalar_optional(flag: bool) -> int32:
    if flag:
        value: int32 | None = 1
    else:
        value = None
    if value is not None:
        return value
    return 0

def scalar_union(flag: bool) -> int32:
    if flag:
        value: int32 | bool = 1
    else:
        value = False
    if isinstance(value, int32):
        return value
    return 0

def constant_loop() -> int32:
    while True:
        value = 1
        break
    return value

global_cell: Cell | None = Cell(3)

def global_optional() -> int32:
    local: Cell | None
    local = global_cell
    if local is not None:
        return local.value
    return 0
'''


Hoists = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def hoists() -> Hoists:
    compiler, modules = _compile(SOURCE)
    (header, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {}
    for name, fn in functions.items():
        result = lower_function(fn, MIRBodyId("hoists", name), definitions=definitions,
                                kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
        if name == "global_optional":
            assert isinstance(result, MIRNotCovered) and result.node_kind == "THIRName"
        else:
            assert isinstance(result, MIRFunction), (name, result)
            bodies[name] = result
    for ctor in ctx.thir_constructors.values():
        result = lower_constructor(ctor, MIRBodyId("hoists", ctor.record_name), definitions=definitions)
        assert isinstance(result, MIRFunction), result
        bodies[ctor.record_name] = result
    return functions, bodies, definitions, header + cpp


def test_hoist_producers_preserve_results_and_shared_mutation(hoists: Hoists) -> None:
    _, bodies, _, _ = hoists
    for fn in bodies.values():
        assert inspect_scope_lifetimes(fn).conflicts == ()
    for flag in (False, True):
        for name in ("scalar", "value_tuple", "loop_tuple", "loop_scalar"):
            assert execute(bodies[name], flag) == (1 if flag else 2)
        for name in ("scalar_optional", "scalar_union"):
            assert execute(bodies[name], flag) == (1 if flag else 0)
        assert execute(bodies["record"], flag) == 9
        assert execute(bodies["escaped_loop"], flag) == (9 if flag else 0)
        assert execute(bodies["nested"], flag, True) == (9 if flag else 0)
        field = bodies["Cell"].records[0].fields[0].id
        for name in ("borrowed", "loop_borrowed", "borrow_tuple", "readonly_record", "loop_readonly", "readonly_tuple"):
            heap = {0: {field: 1}, 1: {field: 2}}
            readonly = name in ("readonly_record", "loop_readonly", "readonly_tuple")
            expected = 8 if name == "readonly_tuple" and not flag else 9
            owner = (Reference(0 if flag else 1),) if name in ("readonly_record", "loop_readonly") else ()
            assert execute(bodies[name], flag, Reference(0), Reference(1), *owner, heap=heap) == expected
            if not readonly:
                assert heap[0 if flag else 1][field] == 9
        for name in ("ptr_optional", "loop_optional"):
            heap = {0: {field: 1}}
            assert execute(bodies[name], flag, OptionalValue(Reference(0)), heap=heap) == (9 if flag else 0)
            assert heap[0][field] == (9 if flag else 1)
            assert execute(bodies[name], flag, OptionalValue(None)) == 0
        heap = {}
        execute(bodies["Observer"], Reference(0), flag, heap=heap)
        assert tuple(heap[0].values()) == (9,)
        assert execute(bodies["method"], Reference(0), flag, heap=heap) == 9


def test_constant_loop_initializes_hoist_in_each_callable(hoists: Hoists) -> None:
    _, bodies, _, _ = hoists
    assert execute(bodies["constant_loop"]) == 1
    heap = {}
    execute(bodies["ConstantObserver"], Reference(0), heap=heap)
    assert tuple(heap[0].values()) == (1,)
    assert execute(bodies["constant_method"], Reference(0), heap=heap) == 1


def test_record_backing_has_body_duration_but_holder_keeps_lexical_residence(hoists: Hoists) -> None:
    _, bodies, _, cpp = hoists
    for name in ("record", "nested", "escaped_loop"):
        fn = bodies[name]
        writes = [(s, stmt) for b in fn.blocks for stmt in b.statements
                  if isinstance(stmt.storage_write, MIRRecordWrite)
                  and stmt.storage_write.mode is MIRRecordWriteMode.OWN_SITE
                  for s in fn.slots if s.id == stmt.target.root]
        assert writes
        for backing, _ in writes:
            assert backing.storage_duration is MIRStorageDuration.BODY
            assert backing.residence.index == 0
        inspection = inspect_scope_lifetimes(fn)
        blocks = {b.id: b for b in fn.blocks}
        assert all(isinstance(blocks[edge.source].terminator, MIRReturn) for edge in inspection.ends.ends)
    local = next(s for s in bodies["escaped_loop"].slots if s.name == "local")
    assert local.value_kind is MIRValueKind.BORROWED_RECORD and local.residence.index != 0
    assert "Cell* value;" in cpp and "std::optional<Cell>" in cpp


@pytest.mark.parametrize("damage", ["missing", "name", "duplicate", "assigned", "placement", "type", "conflict", "extra"])
def test_incomplete_or_inconsistent_hoist_facts_fail_closed(hoists: Hoists, damage: str) -> None:
    functions, _, definitions, _ = hoists
    fn = functions["scalar"]
    stmt, *rest = fn.body
    fact, = stmt.hoisted_bindings
    if damage == "missing":
        stmt = replace(stmt, hoisted_bindings=())
    elif damage == "name":
        stmt = replace(stmt, hoisted_bindings=(replace(fact, name="other"),))
    elif damage == "duplicate":
        stmt = replace(stmt, hoist_decls=stmt.hoist_decls * 2, hoisted_bindings=(fact, fact))
    elif damage == "assigned":
        stmt = replace(stmt, hoisted_bindings=(replace(fact, initially_assigned=True),))
    elif damage == "placement":
        stmt = replace(stmt, hoisted_bindings=(replace(fact, placement=th.THIRStoragePlacement.BODY),))
    elif damage == "type":
        stmt = replace(stmt, hoisted_bindings=(replace(fact, type=BOOL),))
    elif damage == "conflict":
        stmt = replace(stmt, hoisted_bindings=(replace(fact, optional_layout=th.THIROptionalLayout(INT32),
                                                       tuple_layout=th.THIRTupleLayout((INT32,))),))
    else:
        stmt = replace(stmt, hoist_slots=(("value", "int32_t"),))
    result = lower_function(replace(fn, body=(stmt, *rest)), MIRBodyId("bad", damage),
                            kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
    assert isinstance(result, MIRNotCovered), result


def test_predeclaration_does_not_satisfy_definite_assignment(hoists: Hoists) -> None:
    functions, bodies, definitions, _ = hoists
    fn = functions["scalar"]
    stmt, *rest = fn.body
    result = lower_function(replace(fn, body=(replace(stmt, else_body=()), *rest)),
                            MIRBodyId("bad", "unassigned"), kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
    assert isinstance(result, MIRNotCovered) and "definite assignment" in result.reason
    mir = bodies["scalar"]
    value = next(s.id for s in mir.slots if s.name == "value")
    broken = replace(mir, blocks=tuple(replace(b, statements=tuple(
        s for s in b.statements if s.target.root != value)) for b in mir.blocks))
    with pytest.raises(MIRDefiniteAssignmentError, match="definite assignment"):
        validate_function(broken)


def test_hoist_analysis_uses_typed_facts_not_render_strings(hoists: Hoists) -> None:
    functions, _, definitions, _ = hoists
    fn = functions["scalar"]
    stmt, *rest = fn.body
    changed = replace(stmt, hoist_decls=(("value", "uninterpreted render data"),))
    result = lower_function(replace(fn, body=(changed, *rest)), MIRBodyId("facts", "scalar"),
                            kind=MIRBodyKind.FREE_FUNCTION, definitions=definitions)
    assert isinstance(result, MIRFunction) and execute(result, True) == 1


def test_optional_global_copy_keeps_bare_pointer_rendering(hoists: Hoists) -> None:
    functions, _, _, cpp = hoists
    fn = functions["global_optional"]
    copy = next(s for s in fn.body if isinstance(s, th.THIRAssign))
    assert isinstance(copy.value, th.THIRName) and not copy.value.deref
    assert copy.value.optional_read is not None and not copy.value.optional_read.extract
    assert "local = global_cell;" in cpp


@pytest.mark.parametrize("safe", [False, True])
def test_repeated_hoisted_declaration_reuses_backing_and_reports_retention(safe: bool) -> None:
    read, construct = "result = saved.value", "current = Cell(n)"
    first, second = (read, construct) if safe else (construct, read)
    source = CELL_SOURCE + f'''
def reuse(n: int32) -> int32:
    saved = Cell(0)
    result = 0
    while n < 3:
        {first}
        {second}
        saved = current
        n = 2 if n == 1 else 3
    return result
'''
    fn = source_function(source)
    modes = [s.storage_write.mode for b in fn.blocks for s in b.statements
             if isinstance(s.storage_write, MIRRecordWrite)]
    assert modes == [MIRRecordWriteMode.INITIALIZE_ONCE, MIRRecordWriteMode.OWN_SITE]
    holder = next(s for s in fn.slots if s.name == "current")
    assert holder.residence.index != 0
    heap = {}
    # Reallocating at the same site would hide the old alias's overwrite.
    assert execute(fn, 1, heap=heap) == (1 if safe else 2)
    assert len(heap) == 2
    result = analyze(fn)
    assert bool(result.conflicts) is not safe
    assert all(next(s for s in fn.slots if s.id == c.holder.root).name == "saved" for c in result.conflicts)
    empty = {}
    assert execute(fn, 3, heap=empty) == 0 and len(empty) == 1
