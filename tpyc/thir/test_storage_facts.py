"""Materialized storage facts link every producer to its placement or an explicit gap."""

from dataclasses import replace
from io import StringIO
from types import SimpleNamespace

import pytest

from . import nodes as th
from ..codegen_cpp.context import TempState
from .emit import ModuleCounter, TempSink, emit_thir_body
from .storage_facts import THIRBackingKind, collect_storage_facts, validate_storage_facts
from .temp_plan import prepare_temporaries
from .testutil import _compile, _entry
from .validate import validate_function


SOURCE = '''from tpy import int32, readonly, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        saved = observe(Cell(value))
        self.value = saved.value

def observe(cell: Cell) -> readonly[Cell]:
    return cell

def eager(value: int32) -> int32:
    saved = observe(Cell(value))
    return saved.value

def printed(value: int32):
    saved = observe(Cell(value))
    print(saved.value)

def select(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    return saved.value

def inline(value: int32) -> int32:
    return Cell(value).value

def stable(owner: Cell) -> int32:
    saved = observe(owner)
    alias = saved
    return alias.value

def forwarded(owner: Cell) -> readonly[Cell]:
    saved = observe(owner)
    alias = saved
    return alias

def reseated(first: Cell, second: Cell) -> readonly[Cell]:
    saved = observe(first)
    alias = saved
    saved = observe(second)
    return alias

def local_owner(value: int32) -> int32:
    local = Cell(value)
    alias = local
    local.value = 7
    return alias.value

class Wrapper:
    cell: Cell
    def __init__(self, value: int32):
        self.cell = Cell(value)

def field_alias(wrapper: Wrapper) -> int32:
    alias = wrapper.cell
    return alias.value
'''


@pytest.fixture(scope="module")
def compiled() -> tuple[dict[str, th.THIRFunction], dict[str, th.THIRConstructor]]:
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return ({node.name: fn for node, fn in ctx.thir_functions.items()},
            {ctor.record_name: ctor for ctor in ctx.thir_constructors.values()})


@pytest.fixture(scope="module")
def functions(compiled) -> dict[str, th.THIRFunction]:
    return compiled[0]


def test_planned_argument_links_placement_and_sink(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["eager"]
    facts = fn.storage_facts
    backing, = facts.backings
    placement, = fn.temp_plan.placements
    assert backing.kind is THIRBackingKind.ARGUMENT and backing.uncovered is None
    assert backing.node is placement.node and backing.placement is placement
    assert backing.holder is fn.body[0] is placement.declaration
    assert backing.full_expression is fn.body[0].init and backing.loc.line == 20
    obligation, = facts.obligations
    assert obligation.sink is fn.body[0] and obligation.value is fn.body[0].init
    assert obligation.backings == (backing.index,)
    assert facts.backing(backing.node) is backing


def test_missing_plan_keeps_backing_and_obligation(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["printed"]
    assert fn.temp_plan is None
    backing, = fn.storage_facts.backings
    assert backing.kind is THIRBackingKind.ARGUMENT and backing.placement is None
    assert backing.uncovered == "argument storage has no temporary plan"
    obligation, = fn.storage_facts.obligations
    assert obligation.backings == (backing.index,)


def test_select_slot_is_an_explicit_uncovered_sibling(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["select"]
    backing, = fn.storage_facts.backings
    assert backing.kind is THIRBackingKind.SELECT_SLOT and isinstance(backing.node, th.THIRSlotEmplace)
    assert backing.placement is None and backing.uncovered == "select slot placement is not planned"
    obligation, = fn.storage_facts.obligations
    assert isinstance(obligation.value, th.THIRIfExpr) and obligation.backings == (backing.index,)


def test_inline_storage_is_bounded_by_its_full_expression(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["inline"]
    backing, = fn.storage_facts.backings
    assert backing.kind is THIRBackingKind.FULL_EXPRESSION and backing.uncovered is None
    assert backing.holder is fn.body[0] and backing.full_expression is fn.body[0].value
    assert not fn.storage_facts.obligations


def test_stable_alias_and_call_each_keep_an_obligation(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["stable"]
    facts = fn.storage_facts
    assert facts is not None and not facts.backings
    assert len(facts.obligations) == 2
    assert all(obligation.sink is stmt for obligation, stmt in zip(facts.obligations, fn.body[:2]))
    assert all(obligation.backings == () for obligation in facts.obligations)


@pytest.mark.parametrize("name", ["observe", "forwarded", "reseated"])
def test_selected_borrowed_returns_are_explicit_obligations(
        functions: dict[str, th.THIRFunction], name: str) -> None:
    fn = functions[name]
    facts = fn.storage_facts
    contract = fn.resolved_callee.signature.borrowed_result
    assert facts.borrowed_result is contract and contract is not None
    assert len(facts.obligations) == len(fn.body)
    for obligation, stmt in zip(facts.obligations, fn.body):
        assert obligation.sink is stmt and obligation.backings == ()
    assert facts.obligations[-1].value is fn.body[-1].value
    validate_storage_facts(fn.body, fn.temp_plan, facts, borrowed_result=contract)


def test_local_owner_alias_needs_proof_without_argument_backing(
        functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["local_owner"]
    assert not fn.storage_facts.backings
    obligation, = fn.storage_facts.obligations
    assert obligation.sink is fn.body[1] and obligation.value is fn.body[1].init
    assert obligation.backings == ()


def test_assign_alias_and_storage_borrow_reseats_are_inventoried(
        functions: dict[str, th.THIRFunction]) -> None:
    alias = functions["stable"].body[1]
    assert alias.alias_binding is not None
    field_alias = functions["field_alias"].body[0]
    assert field_alias.storage_borrow is not None
    target = th.THIRName(result_type=alias.resolved_type, name=alias.name)
    writes = (
        th.THIRAssign(target=target, value=alias.init, alias_binding=alias.alias_binding),
        th.THIRAssign(target=target, value=field_alias.init, storage_borrow=field_alias.storage_borrow),
        th.THIRPtrLocalRebind(name=alias.name, kind=th.PtrSlotKind.PTR_ADDR,
                             value=alias.init, alias_binding=alias.alias_binding),
    )
    facts = collect_storage_facts(writes, None)
    assert len(facts.obligations) == len(writes)
    assert all(o.sink is stmt and o.value is stmt.value for o, stmt in zip(facts.obligations, writes))


def test_selected_return_contract_identity_cannot_be_reused(
        functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["observe"]
    contract = fn.resolved_callee.signature.borrowed_result
    for foreign in (None, replace(contract)):
        with pytest.raises(ValueError, match="invalid or stale"):
            validate_storage_facts(fn.body, fn.temp_plan, fn.storage_facts, borrowed_result=foreign)
    assert collect_storage_facts(fn.body, fn.temp_plan).obligations == ()


def test_outer_return_contract_does_not_mark_nested_returns(
        functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["observe"]
    contract = fn.resolved_callee.signature.borrowed_result
    nested = th.THIRNestedDef(name="inner", capture_cpp="&", body=fn.body)
    body = (nested, *fn.body)
    facts = collect_storage_facts(body, None, borrowed_result=contract)
    obligation, = facts.obligations
    assert obligation.sink is body[-1]


def test_constructor_facts_cover_body_and_initializers(compiled) -> None:
    ctor = compiled[1]["Caller"]
    facts = ctor.storage_facts
    assert facts.initializers == ctor.mil_inits + ctor.base_inits
    backing, = facts.backings
    assert backing.placement is ctor.temp_plan.placements[0]
    validate_storage_facts(ctor.body, ctor.temp_plan, facts, ctor.mil_inits + ctor.base_inits)
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(ctor.body, ctor.temp_plan, facts)


def test_foreign_stale_and_missing_plan_facts_reject(functions: dict[str, th.THIRFunction]) -> None:
    fn = functions["eager"]
    validate_storage_facts(fn.body, fn.temp_plan, fn.storage_facts)
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(functions["printed"].body, None, fn.storage_facts)
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(fn.body, None, fn.storage_facts)
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(fn.body, fn.temp_plan, collect_storage_facts(fn.body, None))
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(fn.body, fn.temp_plan, replace(fn.storage_facts, obligations=()))
    with pytest.raises(ValueError, match="foreign"):
        fn.storage_facts.backing(replace(fn.storage_facts.backings[0].node))


@pytest.mark.parametrize("field", ["node", "holder", "full_expression"])
def test_equal_but_foreign_backing_anchors_reject(functions: dict[str, th.THIRFunction], field: str) -> None:
    fn = functions["eager"]
    backing, = fn.storage_facts.backings
    cloned = replace(backing, **{field: replace(getattr(backing, field))})
    assert cloned == backing
    with pytest.raises(ValueError, match="invalid or stale"):
        validate_storage_facts(fn.body, fn.temp_plan, replace(fn.storage_facts, backings=(cloned,)))


def test_pointer_sink_shares_argument_planning(functions: dict[str, th.THIRFunction]) -> None:
    # No admitted source reaches a temporary-bearing PTR_ADDR sink; build the reseatable twin.
    decl, *rest = functions["eager"].body
    pointer = th.THIRPtrLocalDecl(name=decl.name, resolved_type=decl.resolved_type, kind=th.PtrSlotKind.PTR_ADDR,
                                  init=decl.init, cpp_type=decl.cpp_type, is_const=decl.is_const, loc=decl.loc)
    body = (pointer, *rest)
    plan = prepare_temporaries(body)
    placement, = plan.placements
    assert placement.declaration is placement.initialization is pointer
    facts = collect_storage_facts(body, plan)
    assert facts.backings[0].placement is placement and facts.obligations[0].sink is pointer
    unrelated = (replace(pointer, kind=th.PtrSlotKind.UNION_ADDR), *rest)
    assert prepare_temporaries(unrelated) is None
    facts = collect_storage_facts(unrelated, None)
    assert facts.backings[0].uncovered == "argument storage has no temporary plan" and not facts.obligations


def test_pointer_declaration_and_reseat_flush_backing_before_binding(functions: dict[str, th.THIRFunction]) -> None:
    # Source admission stays closed; these same-block borrows test the emitter's flush contract.
    original = functions["eager"]
    decl, ret = original.body
    call = decl.init
    assert isinstance(call, th.THIRCall)
    temp, = call.args
    assert isinstance(temp, th.THIRArgTemp) and isinstance(temp.init, th.THIRCtorCall)
    pointer = th.THIRPtrLocalDecl(
        name=decl.name, resolved_type=decl.resolved_type, kind=th.PtrSlotKind.PTR_ADDR,
        init=call, cpp_type=decl.cpp_type, is_const=decl.is_const, loc=decl.loc)
    next_ctor = replace(temp.init, args=(th.THIRLiteral(result_type=original.return_type, value=7),))
    next_call = replace(call, args=(replace(temp, init=next_ctor),))
    reseat = th.THIRPtrLocalRebind(name=decl.name, kind=th.PtrSlotKind.PTR_ADDR, value=next_call)
    assert isinstance(ret, th.THIRReturn) and isinstance(ret.value, th.THIRFieldAccess)
    assert isinstance(ret.value.receiver, th.THIRName)
    read = replace(ret, value=replace(ret.value, receiver=replace(ret.value.receiver, deref=True)))
    body = (pointer, reseat, read)
    plan = prepare_temporaries(body)
    assert plan is not None and len(plan.placements) == 2
    assert all(p.scope == 0 for p in plan.placements)

    outputs = []
    for selected_plan in (plan, None):
        facts = collect_storage_facts(body, selected_plan)
        fn = replace(original, body=body, temp_plan=selected_plan, storage_facts=facts)
        validate_storage_facts(body, selected_plan, facts)
        validate_function(fn)
        ctx = SimpleNamespace(temps=TempState(), with_counter=0, try_counter=0, finally_counter=0)
        out = StringIO()
        emit_thir_body(out, fn, temps=TempSink(ctx),
                       with_counter=ModuleCounter(ctx, "with_counter"),
                       try_counter=ModuleCounter(ctx, "try_counter"),
                       finally_guard_counter=ModuleCounter(ctx, "finally_counter"))
        outputs.append(out.getvalue())
    assert outputs[0] == outputs[1]
    lines = [line.strip() for line in outputs[0].splitlines()
             if line.strip() and not line.lstrip().startswith("//")]
    callee = call.callee_cpp or call.callee
    const = "const " if decl.is_const else ""
    # Exact address bindings exclude a copied return value or a write through the old referent.
    assert lines == [
        f"{temp.cpp_type} __tmp_1 = {temp.init.type_cpp}(value);",
        f"{const}{decl.cpp_type}* saved = &({callee}(__tmp_1));",
        f"{temp.cpp_type} __tmp_2 = {temp.init.type_cpp}(7);",
        f"saved = &({callee}(__tmp_2));",
        "return (*saved).value;",
    ]


def test_nested_body_storage_is_uncovered(functions: dict[str, th.THIRFunction]) -> None:
    decl = functions["eager"].body[0]
    lam = th.THIRLambda(decl.resolved_type, capture_cpp="&", params_cpp=(), body=decl.init)
    body = (th.THIRExprStmt(lam),)
    backing, = collect_storage_facts(body, prepare_temporaries(body)).backings
    assert backing.placement is None and backing.uncovered == "storage inside a nested body"
    nested = th.THIRNestedDef(name="inner", capture_cpp="&", body=body)
    backing, = collect_storage_facts((nested,), None).backings
    assert backing.uncovered == "storage inside a nested body"
