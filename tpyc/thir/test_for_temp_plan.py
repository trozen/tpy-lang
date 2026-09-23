"""For-body temporaries reset independently of counters and captured sources."""

from dataclasses import replace

import pytest

from . import nodes as th
from .lower import functions as function_lowering
from .temp_plan import prepare_temporaries, validate_plan
from .testutil import _compile, _entry


SOURCE = '''from tpy import int32, nocopy, Array, readonly

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def read(cell: Cell) -> int32:
    return cell.value

def positive(cell: Cell) -> bool:
    return cell.value > 0

def ranges(n: int32, flag: bool) -> int32:
    result = 0
    for i in range(n):
        result = read(Cell(i)) if flag else read(Cell(0))
        flag = not flag
    else:
        result = read(Cell(result))
    return result

def descending(n: int32) -> int32:
    last = 42
    for last in range(n, 0, -1):
        if positive(Cell(last)):
            continue
        break
    else:
        last = read(Cell(7))
    return last

def written(start: int32, stop: int32) -> int32:
    last = 42
    for i in range(start, stop):
        last = read(Cell(i))
        start = 99
        stop = 99
        i = 88
    return last

def native(values: list[int32], flag: bool) -> int32:
    result = 0
    for value in values:
        result = read(Cell(value)) if flag else 0
    else:
        result = read(Cell(result))
    return result

def records(values: list[Cell], n: int32) -> int32:
    result = 0
    for cell in values:
        cell.value = read(Cell(n))
        result = cell.value
    return result

def readonly_records(values: readonly[list[Cell]], n: int32) -> int32:
    result = 0
    for cell in values:
        value = cell.value
        result = read(Cell(value))
    return result

def array(values: Array[int32, 2]) -> int32:
    result = 0
    for value in values:
        result = read(Cell(value))
    return result

def keys(values: dict[int32, int32]) -> int32:
    result = 0
    for value in values:
        result = read(Cell(value))
    return result

def members(values: set[int32]) -> int32:
    result = 0
    for value in values:
        result = read(Cell(value))
    return result

def nested(values: list[int32], n: int32, skip: bool) -> int32:
    result = 0
    for value in values:
        for i in range(n):
            while positive(Cell(i)):
                result = read(Cell(value))
                break
        else:
            result = read(Cell(value))
            if skip:
                continue
            break
        result = 99
    else:
        result = read(Cell(7))
    return result

def early(n: int32, take: bool) -> int32:
    for i in range(n):
        if take:
            return read(Cell(i))
        if positive(Cell(i)):
            break
        continue
    else:
        return read(Cell(7))
    return 9

class Runner:
    result: int32
    def __init__(self, n: int32):
        self.result = 0
        for i in range(n):
            self.result = read(Cell(i))
    def method(self, values: list[int32]) -> int32:
        result = 0
        for value in values:
            result = read(Cell(value))
        return result
'''


@pytest.fixture(scope="module")
def functions() -> dict[str, th.THIRFunction]:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {fn.name: fn for fn in ctx.thir_functions.values()}


def test_range_counter_body_and_else_have_distinct_parents(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["ranges"]
    loop = next(stmt for stmt in fn.body if isinstance(stmt, th.THIRForRange))
    plan = fn.temp_plan
    assert plan is not None
    counter, body, otherwise = (plan.scope(loop, role) for role in ("counter", "loop", "else"))
    assert plan.scopes[counter].parent == plan.scopes[otherwise].parent == 0
    assert plan.scopes[body].parent == counter
    first, second, last = plan.placements
    assert first.scope == second.scope == body and first.optional and second.optional
    assert last.scope == otherwise and not last.optional
    assert first.declaration is second.declaration is loop.body[0]
    assert first.initialization is loop.body[0].value.then
    assert second.initialization is loop.body[0].value.orelse
    assert last.declaration is loop.orelse[0]


def test_native_else_is_a_sibling_of_body(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["native"]
    loop = next(stmt for stmt in fn.body if isinstance(stmt, th.THIRForEach))
    plan = fn.temp_plan
    assert plan is not None and plan.scope(loop, "counter") is None
    body, otherwise = (plan.scope(loop, role) for role in ("loop", "else"))
    assert plan.scopes[body].parent == plan.scopes[otherwise].parent == 0
    first, last = plan.placements
    assert first.scope == body and first.optional
    assert last.scope == otherwise and not last.optional


def test_mixed_nested_loop_scope_tree(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["nested"]
    outer = fn.body[1]
    inner = outer.body[0]
    repeated = inner.body[0]
    plan = fn.temp_plan
    assert plan is not None
    outer_body = plan.scope(outer, "loop")
    counter = plan.scope(inner, "counter")
    inner_body = plan.scope(inner, "loop")
    assert plan.scopes[counter].parent == outer_body
    assert plan.scopes[inner_body].parent == counter
    assert plan.scopes[plan.scope(repeated, "iteration")].parent == inner_body
    assert plan.scopes[plan.scope(inner, "else")].parent == outer_body


@pytest.mark.parametrize("name", ["ranges", "descending", "written", "native", "records",
                                  "readonly_records", "array", "keys", "members", "nested", "early", "method"])
def test_for_plans_are_complete(functions: dict[str, th.THIRFunction], name: str) -> None:
    fn = functions[name]
    assert fn.temp_plan is not None
    validate_plan(fn.body, fn.temp_plan)


@pytest.mark.parametrize("field", ["start", "stop"])
def test_range_head_arguments_leave_body_unplanned(functions: dict[str, th.THIRFunction], field: str) -> None:
    loop = functions["ranges"].body[1]
    call = loop.body[0].value.then
    damaged = replace(loop, body=(), orelse=(), **{field: call})
    assert prepare_temporaries((damaged,)) is None


@pytest.mark.parametrize("field,value", [
    ("consuming", True), ("hoisted_tuple_lift_cpp", "tuple"),
    ("hoist_ptr_inits", ("source",)), ("str_literal_iterable", True),
    ("frame_src_field", "source"), ("iterable_lvalue", False),
])
def test_unhandled_native_metadata_leaves_body_unplanned(
        functions: dict[str, th.THIRFunction], field: str, value: object) -> None:
    loop = functions["native"].body[1]
    assert prepare_temporaries((replace(loop, **{field: value}),)) is None


def test_native_head_arguments_leave_body_unplanned(functions: dict[str, th.THIRFunction]) -> None:
    loop = functions["native"].body[1]
    damaged = replace(loop, iterable=loop.body[0].value.then, body=(), orelse=())
    assert prepare_temporaries((damaged,)) is None


def test_explicit_range_step_leaves_body_unplanned(functions: dict[str, th.THIRFunction]) -> None:
    loop = functions["ranges"].body[1]
    assert prepare_temporaries((replace(loop, step=loop.stop),)) is None


@pytest.mark.parametrize("damage", ["body_parent", "else_parent", "foreign_owner"])
def test_range_scope_damage_is_rejected(functions: dict[str, th.THIRFunction], damage: str) -> None:
    fn = functions["ranges"]
    loop = fn.body[1]
    plan = fn.temp_plan
    scopes = list(plan.scopes)
    role = "else" if damage == "else_parent" else "loop"
    index = plan.scope(loop, role)
    scopes[index] = (replace(scopes[index], owner=replace(loop)) if damage == "foreign_owner" else
                     replace(scopes[index], parent=0 if damage == "body_parent" else plan.scope(loop, "counter")))
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(plan, scopes=tuple(scopes)))


def test_preparation_preserves_all_for_emission(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, modules = _compile(SOURCE)
    expected, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert next(c for c in ctx.thir_constructors.values() if c.record_name == "Runner").temp_plan is not None
    monkeypatch.setattr(function_lowering, "prepare_temporaries", lambda body: None)
    compiler, modules = _compile(SOURCE)
    actual, _ = compiler.generate_code_and_thir(_entry(modules))
    assert actual == expected
