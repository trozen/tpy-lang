"""Native loops capture one source and retain selected elements across advances."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies
from .dump import dump_function
from .liveness import analyze_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRBodyId, MIRBodyKind, MIRContainerElements, MIRFunction,
    MIRNotCovered, MIRPlace, MIRPoint, MIRReturn, MIRValueKind,
)
from .scope_lifetime import analyze_scope_ends
from .testutil import ContainerValue, Reference, execute

PREFIX = '''from tpy import int32, Array, readonly
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
'''

SOURCE = PREFIX + '''
def retained(xs: list[Cell], seed: Cell) -> int32:
    saved = seed
    source = xs
    for cell in source:
        if cell.value == 1:
            saved = cell
        cell.value = 7
    return saved.value
def exits(xs: list[int32], stop: bool) -> int32:
    last = 42
    for last in xs:
        if last == 2:
            continue
        if stop:
            break
    else:
        last = 77
    return last
def target(xs: list[int32]) -> int32:
    last = 42
    for last in xs:
        last = 9
    return last
def nested(xs: list[int32]) -> int32:
    result = 0
    for item in xs:
        for i in range(0):
            result = 8
        else:
            if item == 1:
                continue
            break
        result = 9
    else:
        result = 7
    return result
def first(xs: list[Cell]) -> int32:
    for cell in xs:
        return cell.value
    return 42
class Runner:
    result: int32
    def __init__(self, xs: list[Cell]):
        self.result = 42
        for cell in xs:
            self.result = cell.value
    def walk(self, xs: list[Cell]) -> int32:
        for cell in xs:
            temp = Cell(cell.value)
            cell.value = 9
            self.result = temp.value
        return self.result
'''


def compile_bodies(source: str):
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    bodies = {name: lower_function(fn, MIRBodyId("native", name), definitions=definitions,
                                  kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    for ctor in ctx.thir_constructors.values():
        bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("native", ctor.record_name),
                                                   definitions=definitions)
    return functions, bodies, definitions


@pytest.fixture(scope="module")
def artifacts():
    result = compile_bodies(SOURCE)
    assert all(isinstance(body, MIRFunction) for body in result[1].values()), result[1]
    return result


@pytest.mark.parametrize("typ,record", [
    ("list[int32]", False), ("Array[int32, 3]", False), ("set[int32]", False),
    ("dict[int32, int32]", False), ("list[Cell]", True), ("Array[Cell, 3]", True),
    ("readonly[list[Cell]]", True), ("readonly[Array[Cell, 3]]", True),
])
@pytest.mark.parametrize("alias", [False, True])
def test_native_source_families(typ: str, record: bool, alias: bool) -> None:
    source = PREFIX + f'''def walk(xs: {typ}) -> int32:
    last = 0
    {'source = xs' if alias else 'pass'}
    for item in {'source' if alias else 'xs'}:
        last = {'item.value' if record else 'item'}
    return last
'''
    _, bodies, _ = compile_bodies(source)
    fn = bodies["walk"]
    assert isinstance(fn, MIRFunction), fn
    field = fn.records[0].fields[0].id if record else None
    heap = {1: {field: 3}, 2: {field: 5}, 3: {field: 7}}
    values = (Reference(1), Reference(2), Reference(3)) if record else (3, 5, 7)
    assert execute(fn, ContainerValue(values), heap=heap) == 7
    assert execute(fn, ContainerValue(()), heap=heap) == 0
    assert "iterator-init" in dump_function(fn)


def test_saved_element_survives_target_reset_and_cursor_advance(artifacts) -> None:
    _, bodies, _ = artifacts
    fn = bodies["retained"]
    field = next(r.fields[0].id for r in fn.records if r.type.name == "Cell")
    heap = {1: {field: 1}, 2: {field: 2}, 3: {field: 3}}
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), Reference(3), heap=heap) == 7
    assert heap[1][field] == heap[2][field] == 7
    assert heap[3][field] == 3
    deps = analyze_dependencies(fn, analyze_liveness(fn))
    saved = next(s.id for s in fn.slots if s.name == "saved")
    cell = next(s.id for s in fn.slots if s.name == "cell")
    final = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    holders = deps.active[MIRPoint(final.id, 0)]
    assert MIRPlace(cell) not in holders
    assert any(isinstance(p, MIRContainerElements) for ref in holders[MIRPlace(saved)] for p in ref.place.projections)
    assert not analyze_scope_ends(fn).ends
    iterator = next(s for s in fn.slots if s.value_kind is MIRValueKind.NATIVE_ITERATOR)
    assert iterator.residence == fn.regions[0].id
    assert fn.slots[cell.index].residence != iterator.residence


def test_native_transfers_targets_and_constructor_tail(artifacts) -> None:
    _, bodies, _ = artifacts
    assert execute(bodies["exits"], ContainerValue(()), True) == 77
    assert execute(bodies["exits"], ContainerValue((2, 3)), True) == 3
    assert execute(bodies["exits"], ContainerValue((2, 3)), False) == 77
    assert execute(bodies["target"], ContainerValue(())) == 42
    assert execute(bodies["target"], ContainerValue((1, 2))) == 9
    assert execute(bodies["nested"], ContainerValue((1, 2))) == 0
    assert execute(bodies["nested"], ContainerValue((1,))) == 7
    cell = next(r.fields[0].id for r in bodies["walk"].records if r.type.name == "Cell")
    result = next(r.fields[0].id for r in bodies["Runner"].records if r.type.name == "Runner")
    heap = {1: {cell: 3}, 2: {cell: 5}}
    values = ContainerValue((Reference(1), Reference(2)))
    execute(bodies["Runner"], Reference(0), values, heap=heap)
    assert heap[0][result] == 5
    assert execute(bodies["walk"], Reference(0), values, heap=heap) == 5
    assert heap[1][cell] == heap[2][cell] == 9
    assert execute(bodies["first"], values, heap=heap) == 9
    assert execute(bodies["first"], ContainerValue(()), heap=heap) == 42


@pytest.mark.parametrize("damage", ["missing", "source", "binding", "hoist", "consuming", "reseat"])
def test_native_facts_fail_closed(artifacts, damage: str) -> None:
    functions, _, definitions = artifacts
    fn = functions["retained"]
    loop = next(s for s in fn.body if isinstance(s, th.THIRForEach))
    match damage:
        case "missing":
            loop = replace(loop, iteration=None)
        case "source":
            loop = replace(loop, iteration=replace(loop.iteration, source=replace(loop.iteration.source, readonly=True)))
        case "binding":
            loop = replace(loop, const_loop_var=True)
        case "hoist":
            loop = replace(loop, hoist_loop_var=True)
        case "consuming":
            loop = replace(loop, consuming=True)
        case "reseat":
            fn = replace(fn, layout=replace(fn.layout, reassigned_locals=frozenset({"source"})))
    fn = replace(fn, body=tuple(loop if isinstance(s, th.THIRForEach) else s for s in fn.body))
    result = lower_function(fn, MIRBodyId("native", "damaged"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=definitions)
    assert isinstance(result, MIRNotCovered)


@pytest.mark.parametrize("typ", ["list[bool]", "list[tuple[int32, int32]]", "list[Cell | None]",
                                  "set[bool]", "dict[int32, Cell]"])
def test_other_native_elements_remain_uncovered(typ: str) -> None:
    _, bodies, _ = compile_bodies(PREFIX + f'''def other(xs: {typ}) -> int32:
    for item in xs:
        pass
    return 0
''')
    assert isinstance(bodies["other"], MIRNotCovered)


def test_source_structural_mutation_and_record_target_hoist_stay_uncovered() -> None:
    _, bodies, _ = compile_bodies(PREFIX + '''
def structural(xs: list[int32]) -> int32:
    alias = xs
    for item in xs:
        alias.append(7)
        break
    return 0
def hoisted(xs: list[Cell]) -> int32:
    for cell in xs:
        break
    else:
        return 0
    return cell.value
''')
    assert isinstance(bodies["structural"], MIRNotCovered)
    assert isinstance(bodies["hoisted"], MIRNotCovered)
