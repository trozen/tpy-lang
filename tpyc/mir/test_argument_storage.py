"""Named argument backing follows emitted scopes, including lazy loop heads."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from .. import cli
from ..codegen_cpp.context import CodeGenContext
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..thir.temp_plan import prepare_temporaries
from ..thir.lower import functions as function_lowering
from ..typesys import NominalType, OptionalType, TupleType, UnionType
from .call_contract import MIRSummaryState
from .collect import call_definitions
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBorrow, MIRCall,
    MIRConstruct, MIRCopy, MIRFunction, MIRGoto, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRNotCovered, MIRRecordStorageInit, MIRRecordStorageKind, MIRReturn,
    MIRStorageDuration, MIRValueKind, MIRPlace, MIRRecordWrite, MIRRecordWriteMode, MIRRegion, MIRRegionId,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleLayout,
    MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIRUnionConstruct, MIRUnionCopy, MIRUnionLayout,
)
from .region_flow import MIRRegionFlow
from .scope_lifetime import analyze_scope_ends, inspect_scope_lifetimes
from .validate import validate_function


SOURCE = '''from tpy import int32, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def method(self, value: int32) -> int32:
        return read(Cell(value))

class Caller:
    value: int32
    def __init__(self, value: int32):
        self.value = 0
        self.value = read(Cell(value))

def read(cell: Cell) -> int32:
    return cell.value

def positive(cell: Cell) -> bool:
    return cell.value > 0

def compare(left: Cell, right: Cell) -> bool:
    return left.value == right.value

def eager(value: int32) -> int32:
    answer = read(Cell(value))
    value = 2
    return answer

def lazy(value: int32, flag: bool) -> int32:
    return read(Cell(value)) if flag else 0

def multiple(value: int32, flag: bool) -> bool:
    return flag and (positive(Cell(value)) or compare(Cell(0), Cell(value)))

def branches(value: int32, flag: bool) -> int32:
    if positive(Cell(value)):
        answer = 1
    elif positive(Cell(0)):
        answer = 2
    else:
        answer = read(Cell(3))
    return answer

def loop(value: int32, stop: bool, skip: bool) -> int32:
    while positive(Cell(value)):
        value = read(Cell(0))
        if stop:
            break
        if skip:
            continue
    else:
        value = read(Cell(7))
    return value

def lazy_loop(value: int32, flag: bool) -> int32:
    while positive(Cell(value)) if flag else False:
        flag = False
    return value

def plain_loop(value: int32, flag: bool) -> int32:
    while flag:
        value = read(Cell(0))
        flag = False
    return value

def early_return(value: int32) -> int32:
    while positive(Cell(value)):
        return value
    return 0

def lazy_elif(value: int32, flag: bool, select: bool) -> int32:
    if flag:
        return 1
    elif positive(Cell(value)) if select else False:
        return 2
    return 3

def identity(value: int32) -> int32:
    return value

def nested_operand(value: int32) -> int32:
    return read(Cell(identity(value)))

def mutable_reader(cell: Cell) -> int32:
    cell.value = 1
    return cell.value

def effect(value: int32) -> int32:
    return mutable_reader(Cell(value))
'''


Artifacts = tuple[CodeGenContext, MIRCallWorkspace, MIRDefinitions, str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    (_, cpp), ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    return ctx, workspace, definitions, cpp


def _body(artifacts: Artifacts, name: str) -> MIRFunction:
    ctx, workspace, definitions, _ = artifacts
    if name == "Cell.method":
        fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "method")
        body = lower_function(fn, MIRBodyId("main", name),
                              definitions=definitions, summaries=workspace.summaries)
    elif name == "Caller.__init__":
        ctor = next(ctor for ctor in ctx.thir_constructors.values() if ctor.record_name == "Caller")
        body = lower_constructor(ctor, MIRBodyId("main", name), definitions=definitions,
                                 summaries=workspace.summaries)
    else:
        body = next(value for key, value in workspace.bodies.items() if key.declaration.split("@")[0] == name)
    assert isinstance(body, MIRFunction), body
    return body


def _storage(body: MIRFunction) -> list[MIRSlot]:
    return [slot for slot in body.slots if slot.value_kind is MIRValueKind.OWNED]


@pytest.mark.parametrize("name", ["eager", "lazy", "multiple", "branches", "loop", "lazy_loop",
                                      "plain_loop", "early_return", "lazy_elif", "Cell.method", "Caller.__init__"])
def test_named_callers_are_valid_without_copying(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    validate_function(body)
    assert _storage(body)
    statements = [stmt for block in body.blocks for stmt in block.statements if isinstance(stmt, MIRAssign)]
    assert any(isinstance(stmt.value, MIRCall) for stmt in statements)
    assert not any(isinstance(stmt.value, MIRCopy) for stmt in statements)
    assert "const Cell&" in artifacts[3]
    result = inspect_scope_lifetimes(body)
    assert not isinstance(result, MIRNotCovered), result
    assert not result.conflicts


def test_storage_survives_eager_statement_and_first_if_join(artifacts: Artifacts) -> None:
    for name in ("eager", "branches"):
        body = _body(artifacts, name)
        first = _storage(body)[0]
        assert first.storage_duration is MIRStorageDuration.BODY
        ends = analyze_scope_ends(body)
        blocks = {block.id: block for block in body.blocks}
        relevant = [edge for edge, records in ends.ends.items() if any(end.storage.root == first.id for end in records)]
        assert relevant
        assert all(isinstance(blocks[edge.source].terminator, MIRReturn) for edge in relevant)


def test_lazy_wrapper_is_declared_before_branch_and_payload_is_conditional(artifacts: Artifacts) -> None:
    body = _body(artifacts, "lazy")
    storage, = _storage(body)
    assert storage.record_storage is MIRRecordStorageKind.OPTIONAL
    declaration = next(block for block in body.blocks if any(isinstance(stmt, MIRRecordStorageInit)
                                                             for stmt in block.statements))
    construction = next(block for block in body.blocks if any(isinstance(stmt, MIRAssign)
        and isinstance(stmt.value, MIRConstruct) for stmt in block.statements))
    assert declaration.id == body.entry and construction.id != declaration.id
    borrows = [stmt for stmt in construction.statements if isinstance(stmt, MIRAssign)
               and isinstance(stmt.value, MIRBorrow)]
    assert len(borrows) == 1 and borrows[0].value.source.root == storage.id


def test_elif_backing_encloses_remaining_chain_but_not_join(artifacts: Artifacts) -> None:
    body = _body(artifacts, "branches")
    first, condition, otherwise = _storage(body)
    regions = {region.id: region for region in body.regions}
    root = regions[next(block.region for block in body.blocks if block.id == body.entry)]
    assert first.storage_duration is MIRStorageDuration.BODY
    assert regions[condition.storage_duration].parent == root.id
    assert regions[otherwise.storage_duration].parent == condition.storage_duration
    flow = MIRRegionFlow(body)
    exits = [transition for transition in flow.edges.values() if condition.storage_duration in transition.exited]
    assert exits and all(condition.id in transition.ended for transition in exits)
    blocks = {block.id: block for block in body.blocks}
    assert all(transition.target is not None and blocks[transition.target].region == root.id for transition in exits)


def test_while_condition_and_body_temporaries_share_iteration_lifetime(artifacts: Artifacts) -> None:
    body = _body(artifacts, "loop")
    condition, inside, otherwise = _storage(body)
    assert condition.storage_duration == inside.storage_duration
    assert otherwise.storage_duration != condition.storage_duration
    regions = {region.id: region for region in body.regions}
    assert regions[otherwise.storage_duration].parent == regions[condition.storage_duration].parent
    flow = MIRRegionFlow(body)
    condition_block = next(block for block in body.blocks if any(isinstance(stmt, MIRAssign)
        and isinstance(stmt.value, MIRCall) and stmt.value.summary.callee.identity.name == "positive"
        for stmt in block.statements))
    true_edge = next(transition for edge, transition in flow.edges.items()
                     if edge.source == condition_block.id and edge.arm == 0)
    assert condition.id not in true_edge.ended


@pytest.mark.parametrize("name", ["loop", "lazy_loop", "early_return"])
def test_loop_condition_activation_ends_before_reentry(artifacts: Artifacts, name: str) -> None:
    body = _body(artifacts, name)
    storage = _storage(body)[0]
    region = next(region for region in body.regions if region.id == storage.storage_duration)
    flow = MIRRegionFlow(body)
    entering = [transition for transition in flow.edges.values() if region.id in transition.entered]
    exiting = [transition for transition in flow.edges.values() if region.id in transition.exited]
    assert entering and exiting
    assert all(storage.id in transition.reset for transition in entering)
    assert all(storage.id in transition.ended for transition in exiting)
    predecessors = [block for block in body.blocks if isinstance(block.terminator, MIRGoto)
                    and block.terminator.target == region.entry]
    assert predecessors and all(block.region == region.parent for block in predecessors)


def test_owning_callers_summarize_known_and_excluded_calls_stay_uncovered(artifacts: Artifacts) -> None:
    workspace = artifacts[1]
    # A named argument temporary is storage the caller keeps to its end and
    # only lends: private to the body, so it publishes nothing.
    for name in ("eager", "lazy", "multiple", "branches", "loop", "lazy_loop", "lazy_elif"):
        result = workspace.summaries[th.THIRFunctionIdentity("main", name)]
        assert result.state is MIRSummaryState.KNOWN, (name, result)
        assert not result.summary.writes and not result.summary.returns, name
    for name, reason in (("nested_operand", "named temporary needs stable scalar operands"),
                         ("effect", "named argument needs readonly record constructor")):
        body = next(value for key, value in workspace.bodies.items() if key.declaration.split("@")[0] == name)
        assert isinstance(body, MIRNotCovered) and body.reason == reason, body


def test_all_source_positions_preserve_cpp_without_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    compiler, modules = _compile(SOURCE)
    expected, ctx = compiler.generate_code_and_thir(_entry(modules))
    for name in ("method", "lazy_elif", "lazy_loop", "loop"):
        assert next(fn for node, fn in ctx.thir_functions.items() if node.name == name).temp_plan is not None
    assert next(ctor for ctor in ctx.thir_constructors.values() if ctor.record_name == "Caller").temp_plan is not None
    monkeypatch.setattr(function_lowering, "prepare_temporaries", lambda body: None)
    compiler, modules = _compile(SOURCE)
    actual, _ = compiler.generate_code_and_thir(_entry(modules))
    assert actual == expected


@pytest.mark.parametrize(("extra", "reason", "blocked_callee"), [
    ('''def rejected(cell: Cell, value: int32) -> bool:
    return cell.value == read(Cell(value))
''', "named argument crosses unproven evaluation order", None),
    ('''class Wide:
    pair: tuple[int32, int32]
    def __init__(self, value: int32):
        self.pair = (value, value)
def ignore_wide(cell: Wide) -> int32:
    return 0
def rejected() -> int32:
    return ignore_wide(Wide(1))
''', "summary record: unsupported record fields", "ignore_wide"),
    ('''class Hook:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def __del__(self):
        pass
def ignore_hook(cell: Hook) -> int32:
    return 0
def rejected() -> int32:
    return ignore_hook(Hook(1))
''', "summary record: custom record special member", "ignore_hook"),
    ('''def noisy(cell: Cell) -> int32:
    print(cell.value)
    return cell.value
def rejected() -> int32:
    return noisy(Cell(1))
''', "call needs finalized known summary", None),
    ('''def text(cell: Cell) -> str:
    return "value"
def rejected():
    text(Cell(1))
''', "unsupported expression form", None),
    ('''from tpy import Own
def consume(cell: Own[Cell]) -> int32:
    return cell.value
def rejected() -> int32:
    return consume(Cell(1))
''', "call needs finalized known summary", None),
    ('''global_value = 1
def rejected() -> int32:
    return read(Cell(global_value))
''', "named temporary needs stable scalar operands", None),
    ('''def rejected(value: int32 | None) -> int32:
    if value is not None:
        return read(Cell(value))
    return 0
''', "named argument needs complete temporary plan", None),
    ('''def rejected(value: int32 | bool) -> int32:
    if isinstance(value, int32):
        return read(Cell(value))
    return 0
''', "named argument needs complete temporary plan", None),
], ids=["order-proof", "record-layout-summary", "record-hook-summary", "effectful-summary",
        "unsupported-result-form", "owned-parameter-summary", "global-constructor-input",
        "unplanned-optional-narrowing", "unplanned-union-narrowing"])
def test_source_boundaries_reject_at_the_expected_gate(
        extra: str, reason: str, blocked_callee: str | None) -> None:
    compiler, modules = _compile(SOURCE + extra)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    result = next(body for identity, body in workspace.bodies.items()
                  if identity.declaration.split("@")[0] == "rejected")
    assert isinstance(result, MIRNotCovered), result
    if blocked_callee is not None:
        assert result.reason == "call needs finalized known summary"
        summary = workspace.summaries[th.THIRFunctionIdentity("main", blocked_callee)]
        assert summary.state is MIRSummaryState.OPAQUE and summary.reason == reason
    else:
        assert result.reason == reason


def test_deferred_backing_requires_verified_movability(artifacts: Artifacts) -> None:
    ctx, workspace, _, _ = artifacts
    definitions = MIRDefinitions(tuple(replace(ctor, record_layout=replace(ctor.record_layout, movable=False))
                                       if ctor.record_name == "Cell" else ctor for ctor in ctx.thir_constructors.values()))
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "lazy")
    result = lower_function(fn, _body(artifacts, "lazy").id,
                            definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and result.reason == "deferred argument needs movable backing"


def test_missing_plan_stays_uncovered(artifacts: Artifacts) -> None:
    ctx, workspace, definitions, _ = artifacts
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "eager")
    result = lower_function(replace(fn, temp_plan=None), _body(artifacts, "eager").id, definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered) and "complete temporary plan" in result.reason


@pytest.mark.parametrize("damage", [{"move": True}, {"addr_of": True}, {"form": th.Form.VALUE}])
def test_argument_adaptation_requires_exact_borrow_form(artifacts: Artifacts, damage: dict[str, object]) -> None:
    ctx, workspace, definitions, _ = artifacts
    fn = next(fn for node, fn in ctx.thir_functions.items() if node.name == "eager")
    declaration = fn.body[0]
    arg, = declaration.init.args
    body = (replace(declaration, init=replace(declaration.init, args=(replace(arg, **damage),))), *fn.body[1:])
    damaged = replace(fn, body=body, temp_plan=prepare_temporaries(body))
    result = lower_function(damaged, _body(artifacts, "eager").id,
                            definitions=definitions, summaries=workspace.summaries)
    assert isinstance(result, MIRNotCovered)
    expected = "readonly record" if "form" in damage else f"unsupported metadata: {next(iter(damage))}"
    assert expected in result.reason


@pytest.mark.parametrize("qualified", [False, True])
def test_imported_constructor_evidence(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
        qualified: bool) -> None:
    (tmp_path / "helper.py").write_text('''from tpy import int32, nocopy
@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def read(cell: Cell) -> int32:
    return cell.value
''')
    main = tmp_path / "main.py"
    main.write_text('''from tpy import int32
from helper import Cell as Box, read as selected
import helper as h
def caller(value: int32, flag: bool) -> int32:
    return selected(Box(value)) if flag else 0
'''.replace("Box(value)", "h.Cell(value)" if qualified else "Box(value)"))
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", str(main)])
    assert cli._run_cli(False) == 0
    caller = capsys.readouterr().out.split("::caller@", 1)[1].split("\nfn ", 1)[0]
    if qualified:
        assert "MIR not covered: named argument needs complete temporary plan" in caller
    else:
        assert "call helper::read" in caller and "construct" in caller
        assert "MIR not covered" not in caller


@pytest.mark.parametrize("shape", ["scalar", "tuple", "mixed_tuple", "optional", "union"])
@pytest.mark.parametrize("optional_backing", [False, True])
def test_retained_holders_observe_named_backing_scope_end(
        artifacts: Artifacts, shape: str, optional_backing: bool) -> None:
    fn = _body(artifacts, "eager")
    storage, = _storage(fn)
    original, = fn.blocks
    borrow = next(stmt for stmt in original.statements if isinstance(stmt, MIRAssign)
                  and isinstance(stmt.value, MIRBorrow))
    source = borrow.target.root
    borrowed = fn.slots[source.index]
    root, child = MIRRegionId(fn.id, 0), MIRRegionId(fn.id, 1)
    entry, inside, after = (MIRBlockId(fn.id, i) for i in range(3))
    holder, copied = (MIRSlotId(fn.id, len(fn.slots) + i) for i in range(2))
    member = MIRTupleElement(storage.type, MIRValueKind.BORROWED, readonly=True)
    match shape:
        case "scalar":
            typ, form = storage.type, th.Form.BORROW
            options = dict(value_kind=MIRValueKind.BORROWED, readonly=True)
            capture, copy = MIRAlias(source), MIRAlias(holder)
        case "tuple" | "mixed_tuple":
            scalar = next(slot for slot in fn.slots if slot.kind is MIRSlotKind.PARAMETER)
            types = (storage.type,) if shape == "tuple" else (storage.type, scalar.type)
            members = (member,) if shape == "tuple" else (member, MIRTupleElement(scalar.type))
            values = (source,) if shape == "tuple" else (source, scalar.id)
            typ, form = TupleType(types), th.Form.VALUE
            options = dict(value_kind=MIRValueKind.TUPLE, tuple_layout=MIRTupleLayout(members))
            capture, copy = MIRTupleConstruct(values), MIRTupleCopy(holder)
        case "optional":
            typ, form = OptionalType(storage.type), th.Form.VALUE
            options = dict(value_kind=MIRValueKind.OPTIONAL,
                           optional_layout=MIROptionalLayout(storage.type, MIRValueKind.BORROWED, True))
            capture, copy = MIROptionalConstruct(source), MIROptionalCopy(holder)
        case _:
            other = NominalType("Other", _module_qname="main.Other")
            typ, form = UnionType((storage.type, other)), th.Form.VALUE
            options = dict(value_kind=MIRValueKind.UNION,
                           union_layout=MIRUnionLayout((member, MIRTupleElement(
                               other, MIRValueKind.BORROWED, readonly=True))))
            capture, copy = MIRUnionConstruct(0, source), MIRUnionCopy(holder)
    slots = tuple(replace(slot, storage_duration=child, residence=child,
                          record_storage=MIRRecordStorageKind.OPTIONAL if optional_backing
                          else MIRRecordStorageKind.DIRECT) if slot.id == storage.id
                  else replace(slot, residence=child) if slot.id == borrowed.id else slot for slot in fn.slots)
    slots += tuple(MIRSlot(sid, typ, MIRSlotKind.LOCAL, form=form, residence=root, **options)
                   for sid in (holder, copied))
    statements = tuple(replace(stmt, storage_write=MIRRecordWrite(
        MIRRecordWriteMode.OPTIONAL_ASSIGN if optional_backing else MIRRecordWriteMode.INITIALIZE_REGION))
        if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRConstruct) else stmt
        for stmt in original.statements)
    if optional_backing:
        statements = (MIRRecordStorageInit(MIRPlace(storage.id)), *statements)
    fn = replace(fn, slots=slots, regions=(MIRRegion(root, None, entry), MIRRegion(child, root, inside)),
                 blocks=(MIRBlock(entry, (), MIRGoto(inside), root),
                         MIRBlock(inside, (*statements, MIRAssign(MIRPlace(holder), capture)), MIRGoto(after), child),
                         MIRBlock(after, (MIRAssign(MIRPlace(copied), copy),), original.terminator, root)))
    result = inspect_scope_lifetimes(fn)
    assert not result.freshness
    assert len(result.conflicts) == 1
    assert result.conflicts[0].ended == MIRPlace(storage.id)
    assert result.conflicts[0].holder.root == holder
