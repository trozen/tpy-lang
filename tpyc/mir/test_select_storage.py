"""Select aliases retain the selected record and its actual planned backing."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from ..identity_map import IdentityMap
from ..thir import nodes as th
from ..thir.storage_facts import THIRBackingKind, collect_storage_facts
from ..thir.temp_plan import prepare_temporaries
from ..typesys import BOOL, INT32, NominalType, OptionalType, TupleType, UnionType
from . import lower as lowering
from .definitions import MIRDefinitions
from .nodes import (
    MIRAssign, MIRBorrow, MIRConstant, MIRConstruct,
    MIROptionalConstruct, MIROptionalLayout, MIROptionalPayload, MIRPlace, MIRRead, MIRRecordStorageInit,
    MIRRecordStorageKind, MIRRecordWriteMode, MIRRegionId, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRTupleConstruct, MIRTupleElement, MIRTupleIndex, MIRTupleLayout, MIRUnionConstruct,
    MIRUnionLayout, MIRUnionPayload, MIRValueKind,
)
from .region_flow import MIRRegionFlow
from .storage_adapter import certify_thir_storage
from .storage_evidence import (
    MIRBorrowEvidence, MIRStorageConflictKind, MIRStorageVerdict, certify_storage_origins,
)
from .test_borrowed_argument_storage import Artifacts, _compile_workspace, _thir
from .test_storage_adapter import _request, _with_body
from .testutil import Reference, execute


SOURCE = '''from tpy import int32, readonly, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def forward(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    alias = saved
    owner.value = 9
    return alias.value

def reverse(flag: bool, owner: Cell) -> int32:
    saved = Cell(2) if flag else owner
    owner.value = 9
    return saved.value

def nested(flag: bool, choice: bool, owner: Cell) -> int32:
    saved = (owner if choice else Cell(2)) if flag else Cell(3)
    alias = saved
    owner.value = 9
    return alias.value

def readonly_read(flag: bool, owner: readonly[Cell]) -> int32:
    saved = owner if flag else Cell(2)
    alias = saved
    return alias.value

def escape(flag: bool, choice: bool, owner: Cell) -> int32:
    outer = owner
    if flag:
        saved = owner if choice else Cell(2)
        outer = saved
    return outer.value

def dead(flag: bool, choice: bool, owner: Cell) -> int32:
    outer = owner
    if flag:
        saved = owner if choice else Cell(2)
        outer = saved
        owner.value = outer.value
    outer = owner
    return outer.value

def early(flag: bool, choice: bool, owner: Cell) -> int32:
    if flag:
        saved = owner if choice else Cell(2)
        return saved.value
    return owner.value

def loop(flag: bool, choice: bool, owner: Cell) -> int32:
    result = 0
    while flag:
        saved = owner if choice else Cell(2)
        result = saved.value
        flag = False
        continue
    return result

def ranges(choice: bool, owner: Cell) -> int32:
    result = 0
    for index in range(2):
        saved = owner if choice else Cell(index)
        result = saved.value
    return result

def observe(owner: Cell) -> readonly[Cell]:
    return owner

def mixed(flag: bool, owner: readonly[Cell], value: int32) -> bool:
    saved = owner if flag else Cell(value)
    named = observe(Cell(value))
    scalar = Cell(value).value
    return saved.value == named.value and scalar == value

class Runner:
    value: int32
    def __init__(self, flag: bool, value: int32):
        self.value = 0
        owner = Cell(value)
        saved = owner if flag else Cell(2)
        self.value = saved.value
    def read(self, flag: bool, owner: Cell) -> int32:
        saved = owner if flag else Cell(2)
        self.value = saved.value
        return self.value
'''


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    return _compile_workspace(SOURCE)


@pytest.mark.parametrize("name", ["forward", "reverse", "nested", "readonly_read", "dead",
                                  "early", "loop", "ranges", "mixed", "read", "Runner"])
def test_select_uses_actual_optional_backing(artifacts: Artifacts, name: str) -> None:
    source = (next(c for c in artifacts[0].thir_constructors.values() if c.record_name == name)
              if name == "Runner" else _thir(artifacts, name))
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    assert result.certifies(request, source, result.function)
    assert isinstance(result.evidence, MIRBorrowEvidence)
    slots = {s.id: s for s in result.function.slots}
    statements = [s for b in result.function.blocks for s in b.statements]
    for backing in source.storage_facts.backings:
        if backing.kind is not THIRBackingKind.SELECT_SLOT:
            continue
        place = result.backings[backing.node]
        placement = source.temp_plan.placement(backing.node)
        slot = slots[place.root]
        assert not place.projections and slot.record_storage is MIRRecordStorageKind.OPTIONAL
        assert not slot.readonly
        assert place.root in result.evidence.explicit_roots
        assert placement is backing.placement and placement.initialization is backing.node
        assert any(isinstance(s, MIRRecordStorageInit) and s.target == place for s in statements)
        write, = (s for s in statements if isinstance(s, MIRAssign) and s.target == place)
        assert isinstance(write.value, MIRConstruct)
        assert write.storage_write.mode is MIRRecordWriteMode.OPTIONAL_ASSIGN
        assert any(isinstance(s, MIRAssign) and isinstance(s.value, MIRBorrow) and s.value.source == place
                   for s in statements)
        assert replace(backing.node) not in result.backings
        if name in ("loop", "ranges"):
            assert isinstance(slot.storage_duration, MIRRegionId)
            assert any(place.root in edge.reset for edge in MIRRegionFlow(result.function).edges.values()
                       if edge.entered)
        elif name not in ("early", "dead"):
            assert slot.storage_duration is MIRStorageDuration.BODY
    if name == "mixed":
        assert {b.kind for b in source.storage_facts.backings} == set(THIRBackingKind)
        assert len(result.evidence.explicit_roots) == 3


@pytest.mark.parametrize("name,args,expected,count", [
    ("forward", (True,), 9, 0), ("forward", (False,), 2, 1),
    ("reverse", (True,), 2, 1), ("reverse", (False,), 9, 0),
    ("nested", (True, True), 9, 0), ("nested", (True, False), 2, 1),
    ("nested", (False, True), 3, 1), ("readonly_read", (True,), 5, 0),
    ("readonly_read", (False,), 2, 1), ("early", (True, False), 2, 1),
    ("loop", (True, False), 2, 1), ("ranges", (False,), 1, 2),
])
def test_selected_construction_and_aliasing(
        artifacts: Artifacts, name: str, args: tuple[bool, ...], expected: int, count: int) -> None:
    source = _thir(artifacts, name)
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    field = artifacts[2].records[source.params[-1].borrowed_record.type].layout.fields[0].id
    heap = {0: {field: 5}}
    assert execute(result.function, *args, Reference(0), heap=heap) == expected
    assert len(heap) == 1 + count


def test_branch_local_alias_escape_is_a_conflict(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "escape")
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.CONFLICT, (result.gaps, result.evidence)
    assert not result.gaps and not result.evidence.gaps
    place, = result.backings.values()
    assert any(c.kind is MIRStorageConflictKind.SCOPE_END and c.origin == place
               for c in result.evidence.conflicts)
    assert not result.certifies(request, source, result.function)


@pytest.mark.parametrize("mutation", ["missing_plan", "stale_plan", "stale_facts", "missing_definition"])
def test_select_requires_complete_facts(artifacts: Artifacts, mutation: str) -> None:
    source = _thir(artifacts, "forward")
    if mutation == "stale_plan":
        source = replace(source, temp_plan=prepare_temporaries(source.body))
    elif mutation == "stale_facts":
        source = replace(source, storage_facts=replace(source.storage_facts, backings=()))
    elif mutation == "missing_plan":
        source = replace(source, temp_plan=None, storage_facts=collect_storage_facts(source.body, None))
    request = _request(artifacts, source)
    if mutation == "missing_definition":
        request = replace(request, definitions=MIRDefinitions())
    if mutation.startswith("stale"):
        with pytest.raises(ValueError, match="stale"):
            certify_thir_storage(request)
    else:
        result = certify_thir_storage(request)
        assert result.verdict is MIRStorageVerdict.NOT_COVERED


def test_missing_select_root_does_not_reuse_argument_proof(
        artifacts: Artifacts, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _thir(artifacts, "mixed")
    request = _request(artifacts, source)
    lowered = lowering.lower_function_storage(source, request.body, kind=request.kind,
                                              definitions=request.definitions, summaries=request.summaries)
    mapping = MappingProxyType(IdentityMap((node, place) for node, place in lowered.backings.items()
                                           if not isinstance(node, th.THIRSlotEmplace)))
    monkeypatch.setattr("tpyc.mir.storage_adapter.lower_function_storage",
                        lambda *args, **kwargs: replace(lowered, backings=mapping))
    result = certify_thir_storage(request)
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("no MIR backing correspondence" in gap.reason for gap in result.gaps)
    assert not result.certifies(request, source, result.function)


def test_select_certificate_is_exact_and_immutable(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "forward")
    request = _request(artifacts, source)
    result = certify_thir_storage(request)
    assert result.certifies(request, source, result.function)
    assert not result.certifies(replace(request), source, result.function)
    assert not result.certifies(request, replace(source), result.function)
    assert not result.certifies(request, source, replace(result.function))
    node, = result.backings
    with pytest.raises(TypeError):
        result.backings[node] = result.backings[node]
    evidence = result.evidence
    assert not evidence.certifies_operations(result.function, evidence.operations, frozenset())
    assert not evidence.certifies_operations(result.function, frozenset(), evidence.explicit_roots)


def test_unreachable_select_producer_stays_an_obligation(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "forward")
    source = _with_body(source, (replace(source.body[-1], value=th.THIRLiteral(INT32, 0)), *source.body))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.requires_proof and result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("no MIR backing correspondence" in gap.reason for gap in result.gaps)


def test_pruned_select_root_does_not_certify(artifacts: Artifacts) -> None:
    source = _thir(artifacts, "early")
    branch, final = source.body
    source = _with_body(source, (replace(branch, condition=th.THIRLiteral(BOOL, False)), final))
    result = certify_thir_storage(_request(artifacts, source))
    assert len(result.backings) == 1
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("storage is absent from the reachable MIR body" in gap.reason for gap in result.gaps)


def test_missing_operation_does_not_reuse_select_storage_proof(
        artifacts: Artifacts, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _thir(artifacts, "forward")
    request = _request(artifacts, source)
    lowered = lowering.lower_function_storage(source, request.body, kind=request.kind,
                                              definitions=request.definitions, summaries=request.summaries)
    alias = source.body[1]
    mapping = MappingProxyType(IdentityMap((node, points) for node, points in lowered.operations.items()
                                           if node is not alias))
    monkeypatch.setattr("tpyc.mir.storage_adapter.lower_function_storage",
                        lambda *args, **kwargs: replace(lowered, operations=mapping))
    result = certify_thir_storage(request)
    assert result.evidence.verdict is MIRStorageVerdict.CERTIFIED
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert not result.certifies(request, source, result.function)


@pytest.mark.parametrize("escaping", [False, True])
def test_internal_borrowed_return_checks_selected_origin(artifacts: Artifacts, escaping: bool) -> None:
    source = _thir(artifacts, "forward")
    observe = _thir(artifacts, "observe")
    borrowed = observe.resolved_callee.signature.borrowed_result
    value = th.THIRName(borrowed.type, "alias" if escaping else "owner", form=th.Form.BORROW)
    source = _with_body(replace(source, return_type=observe.return_type, resolved_callee=observe.resolved_callee),
                        (*source.body[:-1], replace(source.body[-1], value=value)))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is (MIRStorageVerdict.CONFLICT if escaping else MIRStorageVerdict.CERTIFIED)
    if escaping:
        place, = result.backings.values()
        assert any(c.kind is MIRStorageConflictKind.RETURN_ESCAPE and c.origin == place
                   for c in result.evidence.conflicts)


def test_internal_select_reseat_uses_the_planned_storage(artifacts: Artifacts) -> None:
    # Source currently withholds fresh select reseats; use its existing PTR_ADDR route.
    source = _thir(artifacts, "dead")
    initial, _, reseat, final = source.body
    select = _thir(artifacts, "forward").body[0].init
    reseat = replace(reseat, value=select, alias_binding=None)
    source = _with_body(source, (initial, reseat, final))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.CERTIFIED, (result.gaps, result.evidence)
    place, = result.backings.values()
    point, = result.operations[reseat]
    assert place.root in {origin.place.root for origin in result.evidence.origins[point]}


@pytest.mark.parametrize("with_argument", [False, True])
def test_while_condition_emplacement_remains_uncovered(artifacts: Artifacts, with_argument: bool) -> None:
    # Direct field access on a select is an internal condition witness, not a new source route.
    source = _thir(artifacts, "forward")
    field = replace(source.body[-1].value, receiver=source.body[0].init)
    condition = th.THIRBinOp(BOOL, field, "==", th.THIRLiteral(INT32, 2), None)
    if with_argument:
        call = _thir(artifacts, "mixed").body[1].init
        call = replace(call, args=tuple(replace(arg, init=replace(arg.init, args=(th.THIRLiteral(INT32, 2),)))
                                       for arg in call.args))
        other = th.THIRBinOp(BOOL, replace(field, receiver=call), "==", th.THIRLiteral(INT32, 2), None)
        condition = th.THIRBinOp(BOOL, condition, "&&", other, None)
    source = _with_body(source, (th.THIRWhile(condition, ()),
                                 replace(source.body[-1], value=th.THIRLiteral(INT32, 0))))
    assert source.temp_plan is not None
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(gap.reason == "repeated condition select emplacement" for gap in result.gaps)


@pytest.mark.parametrize("mutation", ["effect", "unstable_operand", "immovable", "hook", "and_or"])
def test_unsupported_select_producers_stay_uncovered(artifacts: Artifacts, mutation: str) -> None:
    source = _thir(artifacts, "forward")
    declaration, *rest = source.body
    select = declaration.init
    node = select.orelse
    definitions = artifacts[2]
    if mutation in ("immovable", "hook"):
        constructor = definitions.records[node.result_type].constructor
        layout = replace(constructor.record_layout, **({"movable": False} if mutation == "immovable"
                                                       else {"custom_destructor": True}))
        definitions = MIRDefinitions((replace(constructor, record_layout=layout),))
    elif mutation == "effect":
        constructor = definitions.records[node.result_type].constructor
        definitions = MIRDefinitions((replace(constructor, body=(th.THIRExprStmt(th.THIRLiteral(INT32, 0)),)),))
    elif mutation == "unstable_operand":
        # Pure field reads are outside the stable scalar operand contract for named storage.
        value = replace(source.body[-1].value, receiver=select.then)
        node = replace(node, value=replace(node.value, args=(value,)))
        select = replace(select, orelse=node)
    else:
        select = th.THIRValueSelect(select.result_type, select.then, node, "||", form=th.Form.BORROW)
    source = _with_body(source, (replace(declaration, init=select), *rest))
    request = replace(_request(artifacts, source), definitions=definitions)
    result = certify_thir_storage(request)
    assert result.requires_proof and result.verdict is MIRStorageVerdict.NOT_COVERED


@pytest.mark.parametrize("shape", ["record", "singleton_tuple", "mixed_tuple", "optional", "union"])
def test_retained_holders_track_the_actual_select_root(artifacts: Artifacts, shape: str) -> None:
    # Wrap the real select alias in MIR: wrapper declarations remain outside the THIR planner.
    source = _thir(artifacts, "escape")
    initial, branch, final = source.body
    source = _with_body(source, (initial, replace(branch, condition=th.THIRLiteral(BOOL, True)), final))
    result = certify_thir_storage(_request(artifacts, source))
    assert result.verdict is MIRStorageVerdict.CONFLICT
    function = result.function
    backing, = result.backings.values()
    slots = {slot.id: slot for slot in function.slots}
    holder, = (slot for slot in function.slots if slot.name == "outer")
    transfer = branch.then_body[-1]
    point, = result.operations[transfer]
    if shape == "record":
        path = MIRPlace(holder.id)
    else:
        borrowed = MIRTupleElement(holder.type, MIRValueKind.BORROWED_RECORD)
        wrapper_id = MIRSlotId(function.id, max(slots, key=lambda sid: sid.index).index + 1)
        capture = []
        extra_slots = []
        if shape in ("singleton_tuple", "mixed_tuple"):
            members, values = (borrowed,), (holder.id,)
            if shape == "mixed_tuple":
                scalar = MIRSlotId(function.id, wrapper_id.index + 1)
                extra_slots.append(MIRSlot(scalar, INT32, MIRSlotKind.TEMPORARY, residence=holder.residence))
                capture.append(MIRAssign(MIRPlace(scalar), MIRConstant(7)))
                members, values = (INT32, borrowed), (scalar, holder.id)
            wrapper = MIRSlot(wrapper_id, TupleType(tuple(m.type if isinstance(m, MIRTupleElement) else m
                                                         for m in members)), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.TUPLE,
                              tuple_layout=MIRTupleLayout(tuple(m if isinstance(m, MIRTupleElement)
                                                               else MIRTupleElement(m, MIRValueKind.SCALAR)
                                                               for m in members)), residence=holder.residence)
            capture.append(MIRAssign(MIRPlace(wrapper_id), MIRTupleConstruct(values)))
            path = MIRPlace(wrapper_id, (MIRTupleIndex(len(members) - 1),))
        elif shape == "optional":
            wrapper = MIRSlot(wrapper_id, OptionalType(holder.type), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.OPTIONAL,
                              optional_layout=MIROptionalLayout(holder.type, MIRValueKind.BORROWED_RECORD),
                              residence=holder.residence)
            capture.append(MIRAssign(MIRPlace(wrapper_id), MIROptionalConstruct(holder.id)))
            path = MIRPlace(wrapper_id, (MIROptionalPayload(),))
        else:
            other = NominalType("Other", _module_qname="select.Other")
            wrapper = MIRSlot(wrapper_id, UnionType((holder.type, other)), MIRSlotKind.LOCAL,
                              value_kind=MIRValueKind.UNION,
                              union_layout=MIRUnionLayout((borrowed, MIRTupleElement(other, MIRValueKind.BORROWED_RECORD))),
                              residence=holder.residence)
            capture.append(MIRAssign(MIRPlace(wrapper_id), MIRUnionConstruct(0, holder.id)))
            path = MIRPlace(wrapper_id, (MIRUnionPayload(0),))
        blocks = []
        for block in function.blocks:
            statements = []
            for index, statement in enumerate(block.statements):
                if (isinstance(statement, MIRAssign) and isinstance(statement.value, MIRRead)
                        and statement.value.source.root == holder.id and statement.value.source.projections):
                    statement = replace(statement, value=MIRRead(MIRPlace(
                        path.root, (*path.projections, *statement.value.source.projections))))
                statements.append(statement)
                if block.id == point.block and index == point.index:
                    statements.extend(capture)
            blocks.append(replace(block, statements=tuple(statements)))
        function = replace(function, slots=(*function.slots, wrapper, *extra_slots), blocks=tuple(blocks))
    evidence = certify_storage_origins(function, frozenset((backing.root,)), artifacts[2])
    assert evidence.verdict is MIRStorageVerdict.CONFLICT
    assert any(c.kind is MIRStorageConflictKind.SCOPE_END and c.origin == backing and c.holder == path
               for c in evidence.conflicts)


@pytest.mark.parametrize("body", [
    "held = (saved,)\n    owner.value = 9\n    return held[0].value",
    "held = (7, saved)\n    owner.value = 9\n    return held[1].value",
    "held: Cell | None = saved\n    if held is not None:\n        return held.value\n    return 0",
    "held: Cell | Other = saved\n    if isinstance(held, Cell):\n        return held.value\n    return 0",
])
def test_source_wrapper_sinks_keep_the_unplanned_select_obligation(body: str) -> None:
    # The frontend rejects reference tuple locals for @nocopy elements before MIR.
    source = SOURCE.split("def forward")[0].replace("@nocopy\n", "") + '''
class Other:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def wrapper(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    ''' + body + "\n"
    artifacts = _compile_workspace(source)
    function = _thir(artifacts, "wrapper")
    assert function.temp_plan is None
    assert any(b.kind is THIRBackingKind.SELECT_SLOT for b in function.storage_facts.backings)
    result = certify_thir_storage(_request(artifacts, function))
    assert result.requires_proof and result.verdict is MIRStorageVerdict.NOT_COVERED
