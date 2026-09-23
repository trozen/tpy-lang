"""Named storage follows declaration blocks and lazy initialization anchors."""

from dataclasses import replace
from io import StringIO
from types import SimpleNamespace

import pytest

from . import nodes as th
from .lower import functions as function_lowering
from .temp_plan import validate_plan
from .testutil import _compile, _entry
from ..temp_schedule import TempQueue
from ..identity_map import IdentityMap
from ..codegen_cpp.context import TempState
from .emit import TempSink


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def read(cell: Cell) -> int32:
    return cell.value

def positive(cell: Cell) -> bool:
    return cell.value > 0

def eager(value: int32) -> int32:
    answer = read(Cell(value))
    return answer

def lazy(value: int32, flag: bool) -> int32:
    return read(Cell(value)) if flag else 0

def branches(value: int32, flag: bool) -> int32:
    if positive(Cell(value)):
        result = 1
    elif positive(Cell(value)):
        result = 2
    else:
        result = 3
    return result

def loop(value: int32) -> int32:
    while positive(Cell(value)):
        value = 0
    return value

def multiple(value: int32, flag: bool) -> bool:
    return flag and (positive(Cell(value)) or positive(Cell(0)))
'''


@pytest.fixture(scope="module")
def functions() -> dict[str, th.THIRFunction]:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {node.name: fn for node, fn in ctx.thir_functions.items()}


def test_eager_declaration_and_initialization_share_statement(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["eager"]
    plan = fn.temp_plan
    assert plan is not None
    validate_plan(fn.body, plan)
    entry, = plan.placements
    assert entry.declaration is entry.initialization is fn.body[0]
    assert entry.scope == 0 and not entry.optional
    with pytest.raises(TypeError):
        plan.by_node[0] = entry


def test_lazy_storage_and_payload_have_different_anchors(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["lazy"]
    plan = fn.temp_plan
    assert plan is not None
    entry, = plan.placements
    assert entry.optional and entry.scope == 0
    assert entry.declaration is fn.body[0]
    assert entry.initialization is fn.body[0].value.then


def test_condition_scopes_match_emitted_blocks(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["branches"]
    plan = fn.temp_plan
    assert plan is not None
    first, second = plan.placements
    assert first.scope == 0
    assert plan.scopes[second.scope].role == "condition"
    assert plan.scopes[second.scope].owner is fn.body[0].else_body[0]
    loop = functions["loop"]
    entry, = loop.temp_plan.placements
    assert loop.temp_plan.scopes[entry.scope].role == "iteration"
    assert entry.declaration is loop.body[0]


def test_nested_lazy_prefixes_keep_their_own_initializers(functions: dict[str, th.THIRFunction]) -> None:
    plan = functions["multiple"].temp_plan
    assert plan is not None
    first, second = plan.placements
    assert first.optional and second.optional
    assert first.initialization is not second.initialization
    assert first.declaration is second.declaration


def test_foreign_and_modified_plans_reject(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["eager"]
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(functions["lazy"].body, fn.temp_plan)
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(fn.temp_plan, placements=()))
    with pytest.raises(ValueError, match="foreign"):
        fn.temp_plan.placement(replace(fn.temp_plan.placements[0].node))


@pytest.mark.parametrize("field", ["node", "declaration", "initialization"])
def test_equal_but_foreign_placement_anchors_reject(functions: dict[str, th.THIRFunction], field: str) -> None:
    fn = functions["lazy"]
    entry, = fn.temp_plan.placements
    cloned = replace(entry, **{field: replace(getattr(entry, field))})
    assert cloned == entry
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(fn.temp_plan, placements=(cloned,)))


def test_equal_but_foreign_scope_and_index_entries_reject(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["loop"]
    plan = fn.temp_plan
    root, iteration = plan.scopes
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(plan, scopes=(root, replace(iteration, owner=replace(iteration.owner)))))
    entry, = plan.placements
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(plan, by_node=IdentityMap(((entry.node, replace(entry)),))))
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_plan(fn.body, replace(plan, initializations=IdentityMap(((entry.initialization, (replace(entry),)),))))


@pytest.mark.parametrize("mismatch", ["statement", "scope", "missing", "producer"])
def test_emission_rejects_wrong_declaration_events(functions: dict[str, th.THIRFunction], mismatch: str) -> None:
    fn = functions["eager"]
    plan = fn.temp_plan
    placement, = plan.placements
    sink = TempSink(SimpleNamespace(temps=TempState()))
    sink.bind_plan(plan)
    sink.statement = placement.declaration
    with pytest.raises(AssertionError):
        if mismatch == "producer":
            sink.create("Cell", "Cell(1)")
        elif mismatch == "missing":
            sink.finish_plan()
        else:
            sink.argument(placement.node, "Cell(1)", plan)
            if mismatch == "statement":
                sink.statement = fn.body[1]
            else:
                sink.scope = 1
            sink.flush(StringIO(), "")


def test_emission_rejects_wrong_lazy_initialization_anchor(functions: dict[str, th.THIRFunction]) -> None:
    plan = functions["lazy"].temp_plan
    placement, = plan.placements
    sink = TempSink(SimpleNamespace(temps=TempState()))
    sink.bind_plan(plan)
    with pytest.raises(AssertionError):
        with sink.conditional_region(replace(placement.initialization)):
            sink.argument(placement.node, "Cell(1)", plan)


def test_flushed_or_rolled_back_temps_do_not_join_outer_prefix() -> None:
    queue: TempQueue[str] = TempQueue()
    outer = queue.begin()
    moved = queue.register("moved", True)
    assert queue.drain() == (moved,)
    inner = queue.begin()
    selected = queue.register("selected", True)
    assert queue.end(inner) == (selected,)
    eager = queue.register("eager", False)
    assert queue.end(outer) == ()
    assert moved.optional and not moved.deferred
    assert selected.optional and selected.deferred
    assert not eager.optional and not eager.deferred
    assert not queue.register("after-region", True).optional


def test_prepared_and_unprepared_emission_are_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, modules = _compile(SOURCE)
    expected, _ = compiler.generate_code_and_thir(_entry(modules))
    monkeypatch.setattr(function_lowering, "prepare_temporaries", lambda body: None)
    compiler, modules = _compile(SOURCE)
    actual, ctx = compiler.generate_code_and_thir(_entry(modules))
    assert all(fn.temp_plan is None for fn in ctx.thir_functions.values())
    assert actual == expected
