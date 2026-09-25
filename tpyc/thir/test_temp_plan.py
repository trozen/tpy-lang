"""Named storage follows declaration blocks and lazy initialization anchors."""

from dataclasses import replace
from io import StringIO
from types import SimpleNamespace

import pytest

from . import nodes as th
from .lower import functions as function_lowering
from .temp_plan import prepare_temporaries, validate_plan
from .testutil import _compile, _entry
from ..temp_schedule import TempQueue
from ..identity_map import IdentityMap
from ..codegen_cpp.context import TempState
from .emit import ModuleCounter, TempSink, emit_thir_body
from ..typesys import BOOL


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

def select(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    return saved.value

def select_reverse(flag: bool, owner: Cell) -> int32:
    saved = Cell(2) if flag else owner
    return saved.value

def select_nested(first: bool, second: bool, owner: Cell) -> int32:
    saved = (owner if first else Cell(2)) if second else Cell(3)
    return saved.value
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


def _render(fn: th.THIRFunction, body: tuple[th.THIRStmt, ...], *, planned: bool) -> str:
    plan = prepare_temporaries(body) if planned else None
    if planned:
        assert plan is not None
    ctx = SimpleNamespace(temps=TempState(), with_counter=0, try_counter=0, finally_counter=0)
    out = StringIO()
    emit_thir_body(out, replace(fn, body=body, temp_plan=plan), temps=TempSink(ctx),
                   with_counter=ModuleCounter(ctx, "with_counter"),
                   try_counter=ModuleCounter(ctx, "try_counter"),
                   finally_guard_counter=ModuleCounter(ctx, "finally_counter"))
    return out.getvalue()


@pytest.mark.parametrize("name,count", [("select", 1), ("select_reverse", 1), ("select_nested", 2)])
def test_select_initialization_is_the_exact_operand(functions: dict[str, th.THIRFunction], name: str, count: int) -> None:
    fn = functions[name]
    plan = fn.temp_plan
    assert plan is not None and len(plan.placements) == count
    for entry in plan.placements:
        assert isinstance(entry.node, th.THIRSlotEmplace)
        assert entry.initialization is entry.node and entry.optional
        assert entry.declaration is fn.body[0] and entry.scope == 0
    assert _render(fn, fn.body, planned=True) == _render(fn, fn.body, planned=False)


def _select_condition(functions: dict[str, th.THIRFunction], *, argument: bool = False) -> th.THIRCall:
    selected = functions["select"].body[0].init
    args = (selected,)
    if argument:
        args += functions["eager"].body[0].init.args
    # Internal witness: source field/call lowering has additional admission boundaries.
    return th.THIRCall(result_type=BOOL, callee="probe", args=args)


@pytest.mark.parametrize("argument", [False, True])
def test_select_while_head_stays_outside_repeated_argument_storage(functions: dict[str, th.THIRFunction], argument: bool) -> None:
    stmt = th.THIRWhile(condition=_select_condition(functions, argument=argument), body=(th.THIRBreak(),))
    body = (stmt,)
    plan = prepare_temporaries(body)
    assert plan is not None
    selected, *args = plan.placements
    assert selected.scope == 0 and plan.declarations_in(stmt, 0) == (selected,)
    if argument:
        arg, = args
        assert arg.scope == plan.scope(stmt, "iteration")
        assert plan.declarations_in(stmt, arg.scope) == (arg,)
    else:
        assert plan.scope(stmt, "iteration") is None
    cpp = _render(functions["select"], body, planned=True)
    assert cpp == _render(functions["select"], body, planned=False)
    assert cpp.index("std::optional<Cell> __select_slot_1;") < cpp.index("while (")
    if argument:
        assert cpp.index("while (true)") < cpp.index("Cell __tmp_2 = Cell(value);")
    assert "__select_slot_1.emplace(Cell(2))" in cpp


def test_select_elif_requires_nested_scope_and_loop_body_reactivates_storage(functions: dict[str, th.THIRFunction]) -> None:
    condition = _select_condition(functions)
    arm = th.THIRIf(condition=condition, then_body=())
    outer = th.THIRIf(condition=th.THIRLiteral(result_type=BOOL, value=False), then_body=(), else_body=(arm,))
    for body, owner, role in (
            ((outer,), arm, "condition"),
            ((th.THIRWhile(condition=th.THIRLiteral(result_type=BOOL, value=True),
                           body=(functions["select"].body[0], th.THIRBreak())),), None, "loop")):
        plan = prepare_temporaries(body)
        entry, = plan.placements
        scope = plan.scopes[entry.scope]
        assert scope.role == role and scope.owner is (owner or body[0])
        cpp = _render(functions["select"], body, planned=True)
        assert cpp == _render(functions["select"], body, planned=False)
        if role == "condition":
            assert cpp.index("} else {") < cpp.index("std::optional<Cell>") < cpp.index("if (probe")
        else:
            assert cpp.index("while (true)") < cpp.index("std::optional<Cell>")


def test_named_and_argument_declaration_channels_preserve_identity_and_order(functions: dict[str, th.THIRFunction]) -> None:
    selected = functions["select_nested"].body[0].init
    arg, = functions["eager"].body[0].init.args
    call = th.THIRCall(result_type=BOOL, callee="probe", args=(arg, selected, replace(arg)))
    body = (th.THIRExprStmt(expr=call),)
    plan = prepare_temporaries(body)
    assert plan is not None
    assert [type(p.node) for p in plan.placements] == [th.THIRSlotEmplace] * 2 + [th.THIRArgTemp] * 2
    cpp = _render(functions["select"], body, planned=True)
    assert cpp == _render(functions["select"], body, planned=False)
    assert cpp.index("__select_slot_2;") < cpp.index("__select_slot_3;") < cpp.index("__tmp_1 =") < cpp.index("__tmp_4 =")


@pytest.mark.parametrize("kind", [th.THIRArgTemp, th.THIRSlotEmplace])
def test_emission_rejects_reordered_producers_with_matching_counts(
        functions: dict[str, th.THIRFunction], kind: type[th.THIRArgTemp] | type[th.THIRSlotEmplace]) -> None:
    original = functions["eager"].body[0].init.args[0] if kind is th.THIRArgTemp else functions["select"].body[0].init.orelse
    nodes = (original, replace(original))
    stmt = th.THIRExprStmt(expr=th.THIRCall(result_type=BOOL, callee="probe", args=nodes))
    plan = prepare_temporaries((stmt,))
    sink = TempSink(SimpleNamespace(temps=TempState()))
    sink.bind_plan(plan)
    sink.statement = stmt
    for node in reversed(nodes):
        if kind is th.THIRArgTemp:
            sink.argument(node, "Cell(1)", plan)
        else:
            sink.select_slot(node)
    sink.flush(StringIO(), "")
    with pytest.raises(AssertionError):
        sink.finish_plan()


def test_select_plan_rejects_moved_scope_and_initialization(functions: dict[str, th.THIRFunction]) -> None:
    stmt = th.THIRWhile(condition=_select_condition(functions, argument=True), body=())
    body = (stmt,)
    plan = prepare_temporaries(body)
    select, arg = plan.placements
    for changed in (replace(select, scope=arg.scope), replace(select, initialization=stmt),
                    replace(select, optional=False), replace(select, node=replace(select.node))):
        with pytest.raises(ValueError, match="invalid or stale"):
            validate_plan(body, replace(plan, placements=(changed, arg)))


def test_select_duplicate_and_unrelated_named_producers_remain_unplanned(functions: dict[str, th.THIRFunction]) -> None:
    selected = functions["select"].body[0].init
    duplicate = th.THIRExprStmt(expr=th.THIRCall(result_type=BOOL, callee="probe", args=(selected, selected)))
    assert prepare_temporaries((duplicate,)) is None
    # Nested callables keep the whole body outside this bounded planner.
    unsupported = th.THIRNestedDef(name="nested", capture_cpp="&", body=functions["select"].body)
    assert prepare_temporaries((functions["select"].body[0], unsupported)) is None
    walrus = th.THIRWalrus(result_type=BOOL, name="flag", cpp_name="flag", cpp_type="bool",
                           value=th.THIRLiteral(result_type=BOOL, value=True))
    assert prepare_temporaries((functions["select"].body[0], th.THIRExprStmt(expr=walrus))) is None


@pytest.mark.parametrize("op", ["&&", "||"])
def test_value_select_shares_named_placement_without_anonymous_prefix(functions: dict[str, th.THIRFunction], op: str) -> None:
    fn = functions["select"]
    declaration = fn.body[0]
    selection = declaration.init
    value = th.THIRValueSelect(result_type=selection.result_type, form=selection.form,
                              lhs=selection.then, rhs=selection.orelse, op=op,
                              truthy_mode=th.TruthinessMode.ALWAYS_TRUE)
    body = (replace(declaration, init=value), fn.body[1])
    entry, = prepare_temporaries(body).placements
    assert entry.node is selection.orelse and entry.initialization is entry.node
    cpp = _render(fn, body, planned=True)
    assert cpp == _render(fn, body, planned=False)
    assert "__select_slot_1.emplace(Cell(2))" in cpp and "__tmp_" not in cpp


def test_emission_rejects_named_channel_and_scope_mismatches(functions: dict[str, th.THIRFunction]) -> None:
    plan = functions["select"].temp_plan
    placement, = plan.placements
    for mismatch in ("channel", "scope", "producer"):
        ctx = SimpleNamespace(temps=TempState())
        sink = TempSink(ctx)
        sink.bind_plan(plan)
        sink.statement = placement.declaration
        with pytest.raises(AssertionError):
            if mismatch == "producer":
                sink.declare_named_auto("__unplanned", "Cell")
            else:
                sink.select_slot(placement.node)
                if mismatch == "scope":
                    sink.scope = 1
                else:
                    # Same total count, but the actual sink used the wrong channel.
                    ctx.temps.rollback_to((0, 0))
                    ctx.temps.create_typed("Cell", "Cell(2)")
                sink.flush(StringIO(), "")
