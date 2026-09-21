"""Range captures, induction and source bindings have different lifetimes."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import INT32
from .definitions import MIRDefinitions
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, MIRRangeAdvance
from .scope_lifetime import inspect_scope_lifetimes
from .testutil import Reference, execute

SOURCE = '''from tpy import int32
def capture(start: int32, stop: int32) -> int32:
    last = 42
    for i in range(start, stop):
        start = 99
        stop = 99
        last = i
        i = 88
        continue
    return last
def descending(start: int32, stop: int32) -> int32:
    last = 42
    for last in range(start, stop, -1):
        if last == 2:
            continue
        if last == 1:
            break
    else:
        last = 77
    return last
def keep_target(stop: int32) -> int32:
    i = 42
    for i in range(stop):
        pass
    return i
def nested(stop: int32) -> int32:
    result = 0
    for i in range(stop):
        for j in range(0):
            result = 8
        else:
            if i == 0:
                continue
            break
        result = 9
    else:
        result = 7
    return result
def hoist(stop: int32) -> int32:
    for i in range(stop):
        value = i
    else:
        value = 7
    return value
def endpoint(start: int32, stop: int32) -> int32:
    last = 42
    for i in range(start, stop, 1):
        last = i
    return last
def early(stop: int32) -> int32:
    for i in range(stop):
        return i
    return 7
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
class Runner:
    result: int32
    def __init__(self, stop: int32):
        self.result = 42
        for i in range(stop):
            self.result = i
    def walk(self, stop: int32) -> int32:
        for i in range(stop):
            temp = Cell(i)
            self.result = temp.value
        return self.result
    def bound(self) -> int32:
        last = 42
        for i in range(self.result):
            self.result = 0
            last = i
        return last
'''


@pytest.fixture(scope="module")
def artifacts():
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    functions = {fn.name: fn for fn in ctx.thir_functions.values()}
    bodies = {name: lower_function(fn, MIRBodyId("ranges", name), definitions=definitions,
                                  kind=MIRBodyKind.METHOD if fn.receiver else MIRBodyKind.FREE_FUNCTION)
              for name, fn in functions.items()}
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Runner")
    bodies["Runner"] = lower_constructor(ctor, MIRBodyId("ranges", "Runner"), definitions=definitions)
    assert all(isinstance(body, MIRFunction) for body in bodies.values()), bodies
    return functions, bodies, definitions


def test_once_captured_bounds_and_independent_body_target(artifacts) -> None:
    _, bodies, _ = artifacts
    assert execute(bodies["capture"], 0, 3) == 2
    assert execute(bodies["capture"], 5, 3) == 42
    assert execute(bodies["keep_target"], 0) == 42
    assert execute(bodies["keep_target"], 3) == 2
    assert execute(bodies["descending"], 3, 0) == 1
    assert execute(bodies["descending"], 3, 2) == 77
    assert execute(bodies["descending"], 0, 3) == 77
    assert execute(bodies["nested"], 3) == 0
    assert execute(bodies["nested"], 1) == 7
    assert execute(bodies["hoist"], 0) == 7
    assert execute(bodies["hoist"], 3) == 7
    assert execute(bodies["endpoint"], 2147483646, 2147483647) == 2147483646
    assert execute(bodies["descending"], -2147483647, -2147483648) == 77
    assert execute(bodies["early"], 3) == 0
    assert execute(bodies["early"], 0) == 7


def test_methods_constructor_tails_and_iteration_storage(artifacts) -> None:
    _, bodies, _ = artifacts
    heap = {}
    execute(bodies["Runner"], Reference(0), 3, heap=heap)
    assert tuple(heap[0].values()) == (2,)
    assert execute(bodies["walk"], Reference(0), 4, heap=heap) == 3
    assert execute(bodies["bound"], Reference(0), heap=heap) == 2
    assert not inspect_scope_lifetimes(bodies["walk"]).conflicts
    assert "range-advance" in dump_function(bodies["capture"])
    for body in bodies.values():
        for block in body.blocks:
            for stmt in block.statements:
                if isinstance(getattr(stmt, "value", None), MIRRangeAdvance):
                    counter = body.slots[stmt.target.root.index]
                    assert counter.residence == block.region


@pytest.mark.parametrize("damage", ["step", "counter_write", "stop_capture", "target_residence", "hoists"])
def test_range_facts_fail_closed(artifacts, damage: str) -> None:
    functions, _, definitions = artifacts
    fn = functions["capture"]
    loop = next(s for s in fn.body if isinstance(s, th.THIRForRange))
    match damage:
        case "step":
            loop = replace(loop, step_kind="variable", step=th.THIRLiteral(result_type=INT32, value=1))
        case "counter_write":
            loop = replace(loop, target_written=False)
        case "stop_capture":
            loop = replace(loop, stop_is_literal=True)
        case "target_residence":
            loop = replace(loop, hoist_loop_var=True)
        case "hoists":
            loop = replace(loop, hoist_decls=(("missing", "int32_t"),))
    fn = replace(fn, body=tuple(loop if isinstance(s, th.THIRForRange) else s for s in fn.body))
    result = lower_function(fn, MIRBodyId("ranges", "damaged"), kind=MIRBodyKind.FREE_FUNCTION,
                            definitions=definitions)
    assert isinstance(result, MIRNotCovered)
