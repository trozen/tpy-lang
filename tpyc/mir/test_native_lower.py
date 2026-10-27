"""Native loops capture one source and retain selected elements across advances."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .dump import dump_function
from .testutil import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRFunction, MIRNotCovered, MIRPlace, MIRPoint, MIRReturn
from .shapes import MIRContainerElements
from .value import MIRHolding
from .retention import MIRRetention, analyze_retention
from .scope_lifetime import analyze_scope_ends
from .testutil import construct, compiled_definitions, ContainerValue, Reference, execute, prepared

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


def _retention(fn: MIRFunction) -> MIRRetention:
    result = analyze_retention(prepared(fn))
    assert isinstance(result, MIRRetention), result
    return result


def compile_bodies(source: str):
    compiler, modules = _compile(source)
    with activate_compiler(compiler):
        _, ctx = compiler.generate_code_and_thir(_entry(modules))
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        functions = {fn.name: fn for fn in ctx.thir_functions.values()}
        bodies = {name: lower_function(fn, MIRBodyId("native", name), definitions=definitions)
                  for name, fn in functions.items()}
        for ctor in ctx.thir_constructors.values():
            bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("native", ctor.record_name),
                                                       definitions=definitions)
    return functions, bodies, definitions, compiler


@pytest.fixture(autouse=True)
def _active(request: pytest.FixtureRequest):
    # MIR admits a record by the TypeDef its compilation registered.
    if "artifacts" not in request.fixturenames:
        yield
        return
    with activate_compiler(request.getfixturevalue("artifacts")[3]):
        yield


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
    _, bodies, _, _ = compile_bodies(source)
    fn = bodies["walk"]
    assert isinstance(fn, MIRFunction), fn
    field = fn.records[0].fields[0].id if record else None
    heap = {1: {field: 3}, 2: {field: 5}, 3: {field: 7}}
    values = (Reference(1), Reference(2), Reference(3)) if record else (3, 5, 7)
    assert execute(fn, ContainerValue(values), heap=heap) == 7
    assert execute(fn, ContainerValue(()), heap=heap) == 0
    assert "iterator-init" in dump_function(fn)


def test_saved_element_survives_target_reset_and_cursor_advance(artifacts) -> None:
    _, bodies, _, _ = artifacts
    fn = bodies["retained"]
    field = next(r.fields[0].id for r in fn.records if r.type.name == "Cell")
    heap = {1: {field: 1}, 2: {field: 2}, 3: {field: 3}}
    assert execute(fn, ContainerValue((Reference(1), Reference(2))), Reference(3), heap=heap) == 7
    assert heap[1][field] == heap[2][field] == 7
    assert heap[3][field] == 3
    deps = prepared(fn).dependencies
    saved = next(s.id for s in fn.slots if s.name == "saved")
    cell = next(s.id for s in fn.slots if s.name == "cell")
    final = next(b for b in fn.blocks if isinstance(b.terminator, MIRReturn))
    holders = deps.active[MIRPoint(final.id, 0)]
    assert MIRPlace(cell) not in holders
    assert any(isinstance(p, MIRContainerElements) for ref in holders[MIRPlace(saved)] for p in ref.place.projections)
    assert not analyze_scope_ends(prepared(fn)).ends
    iterator = next(s for s in fn.slots if s.shape.holding is MIRHolding.CURSOR)
    assert iterator.residence == fn.regions[0].id
    assert fn.slots[cell.index].residence != iterator.residence


def test_native_transfers_targets_and_constructor_tail(artifacts) -> None:
    _, bodies, _, _ = artifacts
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
    construct(bodies["Runner"], 0, values, heap=heap)
    assert heap[0][result] == 5
    assert execute(bodies["walk"], Reference(0), values, heap=heap) == 5
    assert heap[1][cell] == heap[2][cell] == 9
    assert execute(bodies["first"], values, heap=heap) == 9
    assert execute(bodies["first"], ContainerValue(()), heap=heap) == 42


@pytest.mark.parametrize("damage", ["missing", "source"])
def test_the_iteration_source_fact_is_not_read(artifacts, damage: str) -> None:
    # The loop's source is the place MIR builds: THIR's description of it, or none, changes nothing.
    functions, bodies, definitions, _ = artifacts
    fn = functions["retained"]
    loop = next(s for s in fn.body if isinstance(s, th.THIRForEach))
    if damage == "missing":
        loop = replace(loop, iteration=None)
    else:
        loop = replace(loop, iteration=replace(loop.iteration, source=replace(loop.iteration.source, readonly=True)))
    damaged = replace(fn, body=tuple(loop if isinstance(s, th.THIRForEach) else s for s in fn.body))
    assert lower_function(damaged, MIRBodyId("native", "retained"), definitions=definitions) == \
        lower_function(fn, MIRBodyId("native", "retained"), definitions=definitions)


def test_a_reassigned_source_is_no_fixed_holder_rule(artifacts) -> None:
    # A holder reseated beside its iteration is a pointer holder like a
    # record's: the dependencies follow the reseat, no layout set is read.
    functions, _, definitions, _ = artifacts
    fn = functions["retained"]
    damaged = replace(fn, layout=replace(fn.layout, reassigned_locals=frozenset({"source"})))
    assert lower_function(damaged, MIRBodyId("native", "retained"), definitions=definitions) == \
        lower_function(fn, MIRBodyId("native", "retained"), definitions=definitions)


@pytest.mark.parametrize("damage", ["binding", "hoist", "consuming"])
def test_native_facts_fail_closed(artifacts, damage: str) -> None:
    functions, _, definitions, _ = artifacts
    fn = functions["retained"]
    loop = next(s for s in fn.body if isinstance(s, th.THIRForEach))
    match damage:
        case "binding":
            loop = replace(loop, const_loop_var=True)
        case "hoist":
            loop = replace(loop, hoist_loop_var=True)
        case "consuming":
            loop = replace(loop, consuming=True)
    fn = replace(fn, body=tuple(loop if isinstance(s, th.THIRForEach) else s for s in fn.body))
    result = lower_function(fn, MIRBodyId("native", "damaged"),
                            definitions=definitions)
    assert isinstance(result, MIRNotCovered)


@pytest.mark.parametrize(("typ", "line"), [
    # An owned-leaf element or key is walked through a readonly borrow of it.
    ("list[str]", "%0: list[str] readonly-ref element=str:owned_leaf:readonly parameter xs"),
    ("dict[str, int32]", "%0: dict[str, int32] readonly-ref element=str:owned_leaf:readonly "
                         "value=int32:leaf:readonly parameter xs"),
])
def test_owned_leaf_elements_lower(typ: str, line: str) -> None:
    _, bodies, _, _ = compile_bodies(PREFIX + f'''def other(xs: {typ}) -> int32:
    for item in xs:
        pass
    return 0
''')
    fn = bodies["other"]
    assert isinstance(fn, MIRFunction), fn
    assert line in [l.strip() for l in dump_function(fn).splitlines()]


@pytest.mark.parametrize("typ", ["list[tuple[int32, int32]]", "list[Cell | None]", "set[int]",
                                  "dict[int32, Cell]"])
def test_other_native_elements_remain_uncovered(typ: str) -> None:
    _, bodies, _, _ = compile_bodies(PREFIX + f'''def other(xs: {typ}) -> int32:
    for item in xs:
        pass
    return 0
''')
    assert isinstance(bodies["other"], MIRNotCovered)


def test_source_structural_mutation_lowers_and_record_target_hoist_stays_uncovered() -> None:
    _, bodies, _, _ = compile_bodies(PREFIX + '''
def structural(xs: list[int32]) -> int32:
    alias = xs
    for item in xs:
        alias.append(7)
        break
    return 0
def growing(xs: list[int32]) -> int32:
    alias = xs
    for item in xs:
        alias.append(item)
    return 0
def hoisted(xs: list[Cell]) -> int32:
    for cell in xs:
        break
    else:
        return 0
    return cell.value
''')
    # The growth through the alias publishes a write of the iterated list's shape.
    structural = bodies["structural"]
    assert isinstance(structural, MIRFunction), structural
    assert any("call stub builtins.list.append" in line and "writes={param0[structure]}" in line
               for line in dump_function(structural).splitlines())
    # The `break` leaves the iterator dead after the write; without it the
    # iterator lives on into the next step, and retention reports the write.
    assert not _retention(structural).conflicts
    growing = bodies["growing"]
    assert isinstance(growing, MIRFunction), growing
    conflicts = _retention(growing).conflicts
    assert conflicts and all(growing.slots[c.holder.root.index].shape.holding is MIRHolding.CURSOR
                             for c in conflicts)
    assert isinstance(bodies["hoisted"], MIRNotCovered)
