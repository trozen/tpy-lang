"""Record hoists bind one backing; skipped construction leaves only its wrapper."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.validate import THIRValidationError, validate_function as validate_thir
from ..typesys import BOOL
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered,
    MIRRecordStorageInit, MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode,
    MIRRegionId, MIRStorageDuration,
)
from .scope_lifetime import MIRScopeEndKind, analyze_scope_ends, inspect_scope_lifetimes
from .testutil import Reference, execute


SOURCE = '''from tpy import int32

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Switch:
    value: bool
    def __init__(self, value: bool):
        self.value = value

def branch(flag: bool) -> int32:
    if flag:
        cell = Cell(1)
    else:
        return 0
    alias = cell
    cell.value = 7
    return alias.value

def boolean(flag: bool) -> bool:
    if flag:
        cell = Switch(False)
    else:
        return False
    alias = cell
    alias.value = True
    return cell.value

def repeated(again: bool) -> int32:
    while True:
        cell = Cell(1)
        if again:
            again = False
            continue
        break
    alias = cell
    cell.value = 8
    return alias.value

def while_else(flag: bool) -> int32:
    while flag:
        cell = Cell(1)
        break
    else:
        return 0
    alias = cell
    alias.value = 6
    return cell.value

def nested(outer: bool, choose: bool, skip: bool, stop: bool) -> int32:
    result = 0
    while outer:
        outer = False
        if choose:
            if stop:
                break
            cell = Cell(1)
        else:
            continue
        alias = cell
        cell.value = 5
        if skip:
            continue
        result = alias.value
    return result

def guard(flag: bool) -> int32:
    seen = False
    if flag and (seen := True):
        cell = Cell(1)
    else:
        return 0
    alias = cell
    cell.value = 3
    return alias.value if seen else 0

def late_operand(flag: bool, value: int32) -> int32:
    if flag:
        value = 5
        cell = Cell(value)
        value = 8
    else:
        return 0
    return cell.value

class Runner:
    result: int32
    def __init__(self, flag: bool):
        self.result = 0
        if flag:
            cell = Cell(2)
        else:
            return
        alias = cell
        cell.value = 9
        self.result = alias.value

    def method(self, flag: bool) -> int32:
        if flag:
            cell = Cell(3)
        else:
            return 0
        alias = cell
        cell.value = 10
        return alias.value
'''

Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction], MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("record_hoists", name), definitions=definitions,
                                  kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        if ctor.record_name == "Runner":
            bodies["Runner"] = lower_constructor(ctor, MIRBodyId("record_hoists", "Runner"), definitions=definitions)
    for name, fn in bodies.items():
        assert isinstance(fn, MIRFunction), (name, fn)
        assert any(isinstance(s, MIRRecordStorageInit) for b in fn.blocks for s in b.statements), name
    return functions, bodies, definitions, cpp


@pytest.mark.parametrize("flag", [False, True])
def test_shared_mutation_in_free_functions_methods_and_constructor_tails(artifacts: Artifacts, flag: bool) -> None:
    _, bodies, _, _ = artifacts
    for name, expected in (("branch", 7), ("while_else", 6), ("guard", 3)):
        assert execute(bodies[name], flag) == (expected if flag else 0)
    assert execute(bodies["boolean"], flag) is flag
    assert execute(bodies["repeated"], flag) == 8
    assert execute(bodies["late_operand"], flag, 2) == (5 if flag else 0)
    heap = {}
    execute(bodies["Runner"], Reference(0), flag, heap=heap)
    assert tuple(heap[0].values()) == (9 if flag else 0,)
    assert execute(bodies["method"], Reference(0), flag, heap=heap) == (10 if flag else 0)


@pytest.mark.parametrize("choose", [False, True])
@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("stop", [False, True])
def test_nested_empty_and_engaged_exits(artifacts: Artifacts, choose: bool, skip: bool, stop: bool) -> None:
    fn = artifacts[1]["nested"]
    assert execute(fn, True, choose, skip, stop) == (5 if choose and not (skip or stop) else 0)
    assert execute(fn, False, choose, skip, stop) == 0
    backing, = [s for s in fn.slots if s.record_storage is MIRRecordStorageKind.OPTIONAL]
    assert isinstance(backing.storage_duration, MIRRegionId)
    exits = list(analyze_scope_ends(fn).ends.values())
    assert any([e.kind for e in events] == [MIRScopeEndKind.RECORD_WRAPPER] for events in exits)
    assert any([e.kind for e in events] == [MIRScopeEndKind.RECORD_WRAPPER, MIRScopeEndKind.OBJECT] for events in exits)
    assert inspect_scope_lifetimes(fn).conflicts == ()


def test_producer_and_mir_agree_with_the_existing_optional_assignment(artifacts: Artifacts) -> None:
    functions, bodies, _, cpp = artifacts
    for name in ("branch", "repeated", "while_else", "boolean"):
        hoist = functions[name].body[0]
        fact, = hoist.hoisted_bindings
        assert fact.optional_record_storage == th.THIRBorrowedRecord(fact.type, False)
        assert fact.physical_default is None and fact.optional_layout is None
        assert fact.initially_assigned is False
        body = bodies[name]
        backing, = [s for s in body.slots if s.record_storage is MIRRecordStorageKind.OPTIONAL]
        assert backing.storage_duration is MIRStorageDuration.BODY
        writes = [s for b in body.blocks for s in b.statements
                  if isinstance(s, MIRAssign) and s.target.root == backing.id]
        assert len(writes) == 1
        assert writes[0].storage_write == MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN)
        emitted = cpp[cpp.index(f" {name}("):]
        emitted = emitted.split("\n}\n", 1)[0]
        assert emitted.index(f"std::optional<{fact.type}> cell;") < emitted.index("cell =")
        assert ".emplace(" not in emitted
    assert "Cell& alias = (*cell);" in cpp


@pytest.mark.parametrize("damage", ["missing_hoist", "missing_write", "wrong_write", "readonly", "assigned", "layout"])
def test_missing_or_inconsistent_positive_facts_stay_uncovered(artifacts: Artifacts, damage: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["branch"]
    hoist = fn.body[0]
    fact, = hoist.hoisted_bindings
    write = hoist.then_body[0]
    if damage == "missing_hoist":
        hoist = replace(hoist, hoisted_bindings=())
    elif damage == "missing_write":
        hoist = replace(hoist, then_body=(replace(write, optional_record_assignment=None),))
    elif damage == "wrong_write":
        hoist = replace(hoist, then_body=(replace(write, target=replace(write.target, name="absent")),))
    else:
        changed = (replace(fact, optional_record_storage=replace(fact.optional_record_storage, readonly=True))
                   if damage == "readonly" else replace(fact, initially_assigned=True)
                   if damage == "assigned" else replace(fact, optional_layout=th.THIROptionalLayout(BOOL)))
        hoist = replace(hoist, hoisted_bindings=(changed,))
    result = lower_function(replace(fn, body=(hoist, *fn.body[1:])), MIRBodyId("record_hoists", "damaged"),
                            definitions=definitions, kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered), result


def test_render_string_is_not_a_storage_fact(artifacts: Artifacts) -> None:
    fn = artifacts[0]["branch"]
    hoist = replace(fn.body[0], hoist_decls=(("cell", "opaque render data"),))
    result = lower_function(replace(fn, body=(hoist, *fn.body[1:])), MIRBodyId("record_hoists", "render"),
                            definitions=artifacts[2], kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRFunction), result
    assert execute(result, True) == 7


def test_thir_rejects_contradictory_record_facts(artifacts: Artifacts) -> None:
    fn = artifacts[0]["branch"]
    hoist = fn.body[0]
    fact, = hoist.hoisted_bindings
    bad = replace(fact, physical_default=th.THIRWrapperDefault(0, None))
    with pytest.raises(THIRValidationError, match="optional record backing"):
        validate_thir(replace(fn, body=(replace(hoist, hoisted_bindings=(bad,)), *fn.body[1:])))
    write = hoist.then_body[0]
    bad_write = replace(write, optional_record_assignment=replace(write.optional_record_assignment, readonly=True))
    with pytest.raises(THIRValidationError, match="optional record assignment"):
        validate_thir(replace(fn, body=(replace(hoist, then_body=(bad_write,)), *fn.body[1:])))


def test_call_result_keeps_its_coverage_boundary() -> None:
    source = '''from tpy import int32, Own
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def make() -> Own[Cell]:
    return Cell(1)

def example(flag: bool) -> int32:
    if flag:
        cell = make()
    else:
        return 0
    return cell.value
'''
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "example")
    result = lower_function(fn, MIRBodyId("record_hoists", "unsupported"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=MIRDefinitions(tuple(ctx.thir_constructors.values())))
    assert isinstance(result, MIRNotCovered) and result.reason == "unsupported expression type", result
