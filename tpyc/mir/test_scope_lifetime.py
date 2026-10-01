"""Storage ends follow emitted backing, including fresh loop activations."""

from dataclasses import replace

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, OptionalType
from .definitions import MIRDefinitions
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch,
    MIRConstant, MIREdge, MIRFunction, MIRGoto, MIRNotCovered,
    MIRIsPresent,
    MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRRegion, MIRRegionId,
    MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration, MIRValueKind,
)
from .scope_lifetime import MIRScopeEnds, analyze_scope_ends, dump_scope_ends
from .validate import MIRValidationError, validate_function


SOURCE = """\
from tpy import int32

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

    def nested(self, flag: bool) -> int32:
        if flag:
            local = Cell(2)
            local.value = self.value
            return local.value
        return 0

class Observer:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        if flag:
            local = Cell(3)
            self.value = local.value

def branch(flag: bool) -> int32:
    result = 0
    if flag:
        local = Cell(1)
        alias = local
        local.value = 2
        result = alias.value
    return result

def loop(flag: bool, stop: bool, skip: bool) -> int32:
    result = 0
    while flag:
        local = Cell(3)
        alias = local
        local.value = 4
        result = alias.value
        flag = False
        if stop:
            break
        if skip:
            continue
    else:
        final = Cell(5)
        result = final.value
    return result

def wrapper(flag: bool, value: int32 | None) -> int32:
    result = 0
    while flag:
        local = value
        if local is not None:
            result = local
        flag = False
    return result

def union(flag: bool, value: int32 | bool) -> int32:
    result = 0
    while flag:
        local = value
        if isinstance(local, int32):
            record = Cell(local)
            result = record.value
        flag = False
    return result

def optional_record(flag: bool) -> int32:
    if flag:
        local: Cell | None = Cell(6)
        if local is not None:
            return local.value
    return 0

def retained(flag: bool) -> int32:
    saved = Cell(0)
    if flag:
        local = Cell(1)
        saved = local
    return saved.value

def own(flag: bool) -> int32:
    current = Cell(0)
    saved = Cell(0)
    while flag:
        current = Cell(1)
        saved = current
        flag = False
    return saved.value

def nested_loops(outer: bool, inner: bool, stop: bool) -> int32:
    result = 0
    while outer:
        local = Cell(1)
        result = local.value
        while inner:
            inside = Cell(2)
            result = inside.value
            break
        else:
            fallback = Cell(3)
            result = fallback.value
            outer = False
            if stop:
                break
            continue
        outer = False
    else:
        final = Cell(4)
        result = final.value
    return result
"""


Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction | MIRNotCovered], str]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    (_header, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    thir = {node.name: fn for node, fn in ctx.thir_functions.items()}
    bodies = {name: lower_function(fn, MIRBodyId("scope", name), definitions=definitions)
              for name, fn in thir.items()}
    for ctor in ctx.thir_constructors.values():
        bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("scope", ctor.record_name),
                                                     definitions=definitions)
    return thir, bodies, cpp


@pytest.mark.parametrize("name", ["branch", "loop", "wrapper", "union", "optional_record", "nested", "Observer"])
def test_real_producers_own_scoped_storage(artifacts: Artifacts, name: str) -> None:
    _thir, bodies, _cpp = artifacts
    fn = bodies[name]
    assert isinstance(fn, MIRFunction), fn
    result = analyze_scope_ends(fn)
    assert isinstance(result, MIRScopeEnds), result
    scoped = {s.id for s in fn.slots if isinstance(s.storage_duration, MIRRegionId)}
    assert scoped
    ended = {e.storage.root for events in result.ends.values() for e in events}
    assert scoped <= ended
    assert not ended.intersection(s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    assert dump_scope_ends(result) == dump_scope_ends(analyze_scope_ends(fn))
    with pytest.raises(TypeError):
        result.ends[MIREdge(fn.entry)] = ()


def test_producer_and_emitter_agree_without_changing_placement(artifacts: Artifacts) -> None:
    thir, bodies, cpp = artifacts
    branch = thir["branch"].body[1]
    assert branch.then_body[0].storage_placement is th.THIRStoragePlacement.SCOPE
    body = cpp[cpp.index(" branch("):].split("\n}\n", 1)[0]
    assert body.index("if (") < body.index("Cell local = Cell(1);")
    fn = bodies["own"]
    assert isinstance(fn, MIRFunction)
    roots = {s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED}
    assert all(s.storage_duration is MIRStorageDuration.BODY for s in fn.slots if s.id in roots)
    ends = analyze_scope_ends(fn)
    blocks = {b.id: b for b in fn.blocks}
    assert all(isinstance(blocks[e.source].terminator, MIRReturn) for e in ends.ends)
    body = cpp[cpp.index(" own("):].split("\n}\n", 1)[0]
    assert body.index("std::optional<Cell>") < body.index("while (")


def test_loop_transfers_end_iteration_and_else_separately(artifacts: Artifacts) -> None:
    fn = artifacts[1]["loop"]
    result = analyze_scope_ends(fn)
    roots = [s.id for s in fn.slots if s.value_kind is MIRValueKind.OWNED]
    counts = {sid: sum(any(e.storage.root == sid for e in events) for events in result.ends.values()) for sid in roots}
    # Body fallthrough, break and continue each destroy the per-iteration local.
    assert sorted(counts.values()) == [1, 3]


def test_inner_else_break_and_continue_end_scopes_inside_out(artifacts: Artifacts) -> None:
    fn = artifacts[1]["nested_loops"]
    assert isinstance(fn, MIRFunction), fn
    result = analyze_scope_ends(fn)
    parents = {r.id: r.parent for r in fn.regions}
    exits = [(edge, events) for edge, events in result.ends.items() if len(events) == 2]
    assert len(exits) == 2
    for _, events in exits:
        assert parents[events[0].region] == events[1].region
    blocks = {b.id: b for b in fn.blocks}
    targets = {blocks[edge.source].terminator.target for edge, _ in exits}
    assert len(targets) == 2
    assert sum(isinstance(blocks[target].terminator, MIRBranch) for target in targets) == 1


def test_missing_placement_is_uncovered(artifacts: Artifacts) -> None:
    thir, _, _ = artifacts
    fn = thir["wrapper"]
    loop = fn.body[1]
    broken = replace(loop.body[0], storage_placement=None)
    changed = replace(fn, body=(fn.body[0], replace(loop, body=(broken, *loop.body[1:])), *fn.body[2:]))
    result = lower_function(changed, MIRBodyId("scope", "missing"))
    assert isinstance(result, MIRNotCovered) and "placement" in result.reason


def region_fixture() -> MIRFunction:
    body = MIRBodyId("scope", "internal")
    value, flag, local = (MIRSlotId(body, i) for i in range(3))
    entry, iteration, after = (MIRBlockId(body, i) for i in range(3))
    root, child = (MIRRegionId(body, i) for i in range(2))
    init = MIRAssign(MIRPlace(local), MIROptionalConstruct(value),
                     storage_write=MIRPayloadWrite(MIRPayloadWriteMode.INITIALIZE_REGION))
    return MIRFunction(body, INT32, (
        MIRSlot(value, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), MIRSlot(flag, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE),
        MIRSlot(local, OptionalType(INT32), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
                optional_layout=MIROptionalLayout(INT32), storage_duration=child, residence=child)), (
        MIRBlock(entry, (), MIRBranch(flag, iteration, after), root),
        MIRBlock(iteration, (init,), MIRGoto(entry), child),
        MIRBlock(after, (), MIRReturn(value), root)), entry,
        regions=(MIRRegion(root, None, entry), MIRRegion(child, root, iteration)))


def test_fresh_loop_activation_has_one_static_end() -> None:
    fn = region_fixture()
    result = analyze_scope_ends(fn)
    assert isinstance(result, MIRScopeEnds)
    assert tuple(result.ends) == (MIREdge(fn.blocks[1].id),)
    event, = result.ends[MIREdge(fn.blocks[1].id)]
    assert event.payloads == frozenset({MIRPlace(fn.slots[2].id, (MIROptionalPayload(),))})


@pytest.mark.parametrize("damage, message", [
    ("cycle", "within region activation"),
    ("residence", "binding residence"),
    ("foreign", "storage region"),
    ("parent", "ancestry"),
    ("outside_read", "outside binding residence"),
    ("wrong_mode", "activation initialization"),
    ("body_residence", "body storage has nested residence"),
    ("interior_entry", "entry into region interior"),
    ("read_before_init", "read before definite assignment"),
])
def test_invalid_region_contracts(damage: str, message: str) -> None:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    if damage == "cycle":
        fn = replace(fn, blocks=(entry, replace(iteration, terminator=MIRGoto(iteration.id)), after))
    elif damage == "residence":
        fn = replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], residence=None)))
    elif damage == "foreign":
        fn = replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], storage_duration=MIRRegionId(fn.id, 9))))
    elif damage == "parent":
        fn = replace(fn, regions=(fn.regions[0], replace(fn.regions[1], parent=fn.regions[1].id)))
    elif damage == "outside_read":
        fn = replace(fn, blocks=(entry, iteration, replace(after, statements=iteration.statements)))
    elif damage == "wrong_mode":
        stmt = replace(iteration.statements[0], storage_write=MIRPayloadWrite(MIRPayloadWriteMode.INITIALIZE))
        fn = replace(fn, blocks=(entry, replace(iteration, statements=(stmt,)), after))
    elif damage == "body_residence":
        fn = replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], storage_duration=MIRStorageDuration.BODY)))
    elif damage == "interior_entry":
        interior = replace(iteration, id=MIRBlockId(fn.id, 3), statements=())
        fn = replace(fn, blocks=(replace(entry, terminator=MIRGoto(interior.id)), iteration, after, interior))
    elif damage == "read_before_init":
        read = MIRAssign(MIRPlace(fn.slots[1].id), MIRIsPresent(fn.slots[2].id))
        fn = replace(fn, blocks=(entry, replace(iteration, statements=(read, *iteration.statements)), after))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


def test_skipped_region_and_absent_payload_are_distinct() -> None:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    empty = replace(iteration.statements[0], value=MIROptionalConstruct())
    absent = replace(fn, blocks=(entry, replace(iteration, statements=(empty,)), after))
    result = analyze_scope_ends(absent)
    assert len(result.ends) == 1
    assert not next(iter(result.ends.values()))[0].payloads
    skipped = replace(fn, blocks=(replace(entry, statements=(MIRAssign(
        MIRPlace(fn.slots[1].id), MIRConstant(False)),)), iteration, after))
    assert not analyze_scope_ends(skipped).ends


def test_edge_selection_survives_equal_branch_destinations() -> None:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    parameter = replace(fn.slots[0], type=OptionalType(INT32), value_kind=MIRValueKind.OPTIONAL,
                        optional_layout=MIROptionalLayout(INT32))
    guard = MIRSlot(MIRSlotId(fn.id, 3), BOOL, MIRSlotKind.TEMPORARY, residence=iteration.region)
    from_source = replace(iteration.statements[0], value=MIROptionalCopy(parameter.id))
    fn = replace(fn, return_type=BOOL, slots=(parameter, *fn.slots[1:], guard), blocks=(
        entry, replace(iteration, statements=(from_source, MIRAssign(MIRPlace(guard.id), MIRIsPresent(fn.slots[2].id))),
                       terminator=MIRBranch(guard.id, after.id, after.id)),
        replace(after, terminator=MIRReturn(fn.slots[1].id))))
    result = analyze_scope_ends(fn)
    assert len(result.ends) == 2
    assert result.ends[MIREdge(iteration.id, 0)][0].payloads
    assert not result.ends[MIREdge(iteration.id, 1)][0].payloads


def test_bounded_trace_counts_distinct_dynamic_activations() -> None:
    fn = region_fixture()
    inventory = analyze_scope_ends(fn)
    root, child = fn.regions
    entry, iteration, after = fn.blocks
    # This execution oracle assigns fresh object tokens, independently of the
    # static inventory; the true input loops forever, so inspect three visits.
    for flag in (False, True):
        active: dict[MIRSlotId, tuple[int, MIRRegionId]] = {}
        generation = 0
        bid = entry.id
        tokens = []
        for _ in range(7):
            block = next(b for b in fn.blocks if b.id == bid)
            if bid == iteration.id:
                generation += 1
                active[fn.slots[2].id] = (generation, child.id)
            match block.terminator:
                case MIRBranch():
                    target = iteration if flag else after
                case MIRGoto():
                    target = entry
                case MIRReturn():
                    break
            if block.region == child.id and target.region == root.id:
                token, owner = active.pop(fn.slots[2].id)
                tokens.append(token)
                event, = inventory.ends[MIREdge(block.id)]
                assert event.region == owner and event.storage.root == fn.slots[2].id
            bid = target.id
        assert tokens == ([1, 2, 3] if flag else [])
