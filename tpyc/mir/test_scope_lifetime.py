"""Storage ends follow emitted backing, including fresh loop activations."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.temp_plan import prepare_temporaries
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, OptionalType
from .definitions import MIRDefinitions
from .testutil import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBranch, MIRConstant, MIREdge, MIRFunction, MIRGoto, MIRNotCovered,
    MIRStorageWrite, MIRStorageWriteMode, MIRPlace, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind,
    MIRStorageDuration, MIRWrap, MIRIsAlternative, MIRCopy,
)
from .shapes import MIRUnionPayload
from .scope_lifetime import MIRScopeEnds, analyze_scope_ends, dump_scope_ends
from .value import MIRValidationError
from .testutil import placed_storage, compiled_definitions, bind_parameters, leaf_shape, optional_shape, prepared
from .prepare import validate_function


SOURCE = """\
from tpy import int32, nocopy

@nocopy
class Pinned:
    value: int32
    def __init__(self, value: int32):
        self.value = value

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

def planned(flag: bool, value: int32 | None, owner: Pinned) -> int32:
    saved = owner if flag else Pinned(2)
    result = saved.value
    while flag:
        local = value
        flag = False
    return result

def planned_union(flag: bool, value: int32 | bool, owner: Pinned) -> int32:
    saved = owner if flag else Pinned(2)
    result = saved.value
    while flag:
        local = value
        flag = False
    return result

def planned_record(flag: bool, owner: Pinned) -> int32:
    saved = owner if flag else Pinned(2)
    result = saved.value
    while flag:
        local = Cell(3)
        flag = False
    return result

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


Artifacts = tuple[dict[str, th.THIRFunction], dict[str, MIRFunction | MIRNotCovered], str, MIRDefinitions]


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def artifacts(compiled) -> Artifacts:
    compiler, modules = compiled
    with activate_compiler(compiler):
        (_header, cpp), ctx = compiler.generate_code_and_thir(_entry(modules))
        definitions = compiled_definitions(compiler, tuple(ctx.thir_constructors.values()))
        thir = {node.name: fn for node, fn in ctx.thir_functions.items()}
        bodies = {name: lower_function(fn, MIRBodyId("scope", name), definitions=definitions)
                  for name, fn in thir.items()}
        for ctor in ctx.thir_constructors.values():
            bodies[ctor.record_name] = lower_constructor(ctor, MIRBodyId("scope", ctor.record_name),
                                                         definitions=definitions)
        return thir, bodies, cpp, definitions


@pytest.mark.parametrize("name", ["branch", "loop", "wrapper", "union", "optional_record", "nested", "Observer"])
def test_real_producers_own_scoped_storage(artifacts: Artifacts, name: str) -> None:
    _thir, bodies, _cpp, _definitions = artifacts
    fn = bodies[name]
    assert isinstance(fn, MIRFunction), fn
    result = analyze_scope_ends(prepared(fn))
    assert isinstance(result, MIRScopeEnds), result
    scoped = {s.id for s in fn.slots if isinstance(s.storage_duration, MIRRegionId)}
    assert scoped
    ended = {e.storage.root for events in result.ends.values() for e in events}
    assert scoped <= ended
    assert not ended.intersection(s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    assert dump_scope_ends(result) == dump_scope_ends(analyze_scope_ends(prepared(fn)))
    with pytest.raises(TypeError):
        result.ends[MIREdge(fn.entry)] = ()


def test_producer_and_emitter_agree_without_changing_placement(artifacts: Artifacts) -> None:
    thir, bodies, cpp, _definitions = artifacts
    branch = thir["branch"].body[1]
    assert branch.then_body[0].storage_placement is th.THIRStoragePlacement.SCOPE
    body = cpp[cpp.index(" branch("):].split("\n}\n", 1)[0]
    assert body.index("if (") < body.index("Cell local = Cell(1);")
    fn = bodies["own"]
    assert isinstance(fn, MIRFunction)
    # The body's storage and the reseat's own sum live for the body; only a
    # fill's built record ends earlier, with its full expression.
    roots = {s.id for s in placed_storage(fn)}
    assert all(s.storage_duration is MIRStorageDuration.BODY for s in fn.slots if s.id in roots)
    ends = analyze_scope_ends(prepared(fn))
    blocks = {b.id: b for b in fn.blocks}
    assert all(isinstance(blocks[e.source].terminator, MIRReturn) for e, events in ends.ends.items()
               if any(end.storage.root in roots for end in events))
    body = cpp[cpp.index(" own("):].split("\n}\n", 1)[0]
    assert body.index("std::optional<Cell>") < body.index("while (")


def test_loop_transfers_end_iteration_and_else_separately(artifacts: Artifacts) -> None:
    fn = artifacts[1]["loop"]
    result = analyze_scope_ends(prepared(fn))
    roots = [s.id for s in fn.slots if s.shape.is_storage]
    counts = {sid: sum(any(e.storage.root == sid for e in events) for events in result.ends.values()) for sid in roots}
    # Body fallthrough, break and continue each destroy the per-iteration local.
    assert sorted(counts.values()) == [1, 3]


def test_inner_else_break_and_continue_end_scopes_inside_out(artifacts: Artifacts) -> None:
    fn = artifacts[1]["nested_loops"]
    assert isinstance(fn, MIRFunction), fn
    result = analyze_scope_ends(prepared(fn))
    parents = {r.id: r.parent for r in fn.regions}
    exits = [(edge, events) for edge, events in result.ends.items() if len(events) == 2]
    assert len(exits) == 2
    for _, events in exits:
        assert parents[events[0].region] == events[1].region
    blocks = {b.id: b for b in fn.blocks}
    targets = {blocks[edge.source].terminator.target for edge, _ in exits}
    assert len(targets) == 2
    assert sum(isinstance(blocks[target].terminator, MIRBranch) for target in targets) == 1


# The bodies whose loop starts with a declaration owning scoped storage --
# an Optional, a union, an owned record -- each with the same declaration
# after a select whose temporary plan is in use.
DECLARATIONS = {"wrapper": "planned", "union": "planned_union", "loop": "planned_record"}
declarations = pytest.mark.parametrize("name", sorted(DECLARATIONS))


def _misplaced(fn: th.THIRFunction, before: tuple[th.THIRStmt, ...] = (), after: tuple[th.THIRStmt, ...] = (),
               *, falls_through: bool = False, placed: bool = False) -> th.THIRFunction:
    """`fn` with its loop's first declaration given no storage placement
    (unless `placed`), between `before` and `after`; `falls_through`
    drops the statements after the loop."""
    index = next(i for i, stmt in enumerate(fn.body) if isinstance(stmt, th.THIRWhile))
    loop = fn.body[index]
    declaration = loop.body[0] if placed else replace(loop.body[0], storage_placement=None)
    loop = replace(loop, body=(*before, declaration, *after, *loop.body[1:]))
    body = (*fn.body[:index], loop, *(() if falls_through else fn.body[index + 1:]))
    return replace(fn, body=body, temp_plan=prepare_temporaries(body) if fn.temp_plan is not None else None)


def _declaration(fn: th.THIRFunction) -> th.THIRStmt:
    """The declaration `_misplaced` takes the placement from."""
    return next(stmt for stmt in fn.body if isinstance(stmt, th.THIRWhile)).body[0]


def _lower_changed(artifacts: Artifacts, fn: th.THIRFunction, name: str) -> MIRFunction | MIRNotCovered:
    return lower_function(fn, MIRBodyId("scope", name), definitions=artifacts[3])


def _refused_at(result: MIRFunction | MIRNotCovered, declaration: th.THIRStmt) -> None:
    assert isinstance(result, MIRNotCovered) and result.reason == "missing direct storage placement", result
    assert (result.node_kind, result.loc) == (type(declaration).__name__, declaration.loc)


def _placement_refused(artifacts: Artifacts, name: str, forged: th.THIRFunction, placed: th.THIRFunction,
                       declaration: th.THIRStmt) -> None:
    """A wrapper's dropped placement refuses at its declaration; an owned
    record's placement is MIR's own (its declaration's scope, the body's
    where THIR hoists it), so the forged payload is not read: the body
    lowers as it does with its placement."""
    result = _lower_changed(artifacts, forged, "forged")
    if name != "loop":
        _refused_at(result, declaration)
        return
    assert result == _lower_changed(artifacts, placed, "forged")


def _both_return(fn: th.THIRFunction) -> th.THIRIf:
    """`if flag: return result else: return result`."""
    loop = next(stmt for stmt in fn.body if isinstance(stmt, th.THIRWhile))
    return th.THIRIf(loop.condition, (fn.body[-1],), (fn.body[-1],))


@declarations
def test_missing_placement_is_uncovered(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[0][name]
    assert isinstance(artifacts[1][name], MIRFunction), artifacts[1][name]
    _placement_refused(artifacts, name, _misplaced(fn), _misplaced(fn, placed=True), _declaration(fn))


@declarations
def test_missing_placement_after_a_jump_is_not_built(artifacts: Artifacts, name: str) -> None:
    # The refusal stands where the CFG would build the declaration's storage;
    # after a `break` nothing in the block is built.
    fn = artifacts[0][name]
    result = _lower_changed(artifacts, _misplaced(fn, (th.THIRBreak(),)), "unreached")
    assert isinstance(result, MIRFunction), result


@declarations
def test_missing_placement_after_a_return_is_not_built(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[0][name]
    result = _lower_changed(artifacts, _misplaced(fn, (fn.body[-1],)), "returned")
    assert isinstance(result, MIRFunction), result


@declarations
def test_missing_placement_after_an_if_whose_arms_return_is_not_built(artifacts: Artifacts, name: str) -> None:
    # An if without a temporary plan whose arms both end leaves no block to continue in.
    fn = artifacts[0][name]
    result = _lower_changed(artifacts, _misplaced(fn, (_both_return(fn),)), "if-returned")
    assert isinstance(result, MIRFunction), result


@declarations
def test_missing_placement_after_a_planned_if_whose_arms_return_is_refused(artifacts: Artifacts, name: str) -> None:
    # An if under a temporary plan always continues in its join block, so the declaration is built.
    thir, bodies, _cpp, _definitions = artifacts
    planned = DECLARATIONS[name]
    assert isinstance(bodies[planned], MIRFunction), bodies[planned]
    fn = thir[planned]
    assert fn.temp_plan is not None
    _placement_refused(artifacts, name, _misplaced(fn, (_both_return(fn),)),
                       _misplaced(fn, (_both_return(fn),), placed=True), _declaration(fn))


@declarations
def test_a_later_refusal_comes_before_placement(artifacts: Artifacts, name: str) -> None:
    # A refusal later in the body wins over the placement refusal the
    # declaration defers until the body reaches it (`Fragment.refusal`).
    fn = artifacts[0][name]
    result = _lower_changed(artifacts, _misplaced(fn, after=(th.THIRReturn(),)), "later")
    assert isinstance(result, MIRNotCovered) and result.reason == "missing return value", result


@declarations
def test_placement_comes_before_a_non_void_fallthrough(artifacts: Artifacts, name: str) -> None:
    fn = artifacts[0][name]
    placed = _lower_changed(artifacts, _misplaced(fn, falls_through=True, placed=True), "placed")
    assert isinstance(placed, MIRNotCovered) and placed.reason == "non-void fallthrough", placed
    # The refusal names the body, which has no location of its own.
    assert (placed.node_kind, placed.loc) == ("THIRFunction", None)
    _placement_refused(artifacts, name, _misplaced(fn, falls_through=True),
                       _misplaced(fn, falls_through=True, placed=True), _declaration(fn))


def region_fixture() -> MIRFunction:
    body = MIRBodyId("scope", "internal")
    value, flag, local = (MIRSlotId(body, i) for i in range(3))
    entry, iteration, after = (MIRBlockId(body, i) for i in range(3))
    root, child = (MIRRegionId(body, i) for i in range(2))
    init = MIRAssign(MIRPlace(local), MIRWrap(1, MIRPlace(value)),
                     storage_write=MIRStorageWrite(MIRStorageWriteMode.INITIALIZE))
    return bind_parameters(MIRFunction(body, INT32, (
        MIRSlot(value, leaf_shape(INT32), MIRSlotKind.PARAMETER),
        MIRSlot(flag, leaf_shape(BOOL), MIRSlotKind.PARAMETER),
        MIRSlot(local, optional_shape(OptionalType(INT32), leaf_shape(INT32)), MIRSlotKind.LOCAL,
                storage_duration=child, residence=child)), (
        MIRBlock(entry, (), MIRBranch(flag, iteration, after), root),
        MIRBlock(iteration, (init,), MIRGoto(entry), child),
        MIRBlock(after, (), MIRReturn(value), root)), entry,
        regions=(MIRRegion(root, None, entry), MIRRegion(child, root, iteration))))


def test_fresh_loop_activation_has_one_static_end() -> None:
    fn = region_fixture()
    result = analyze_scope_ends(prepared(fn))
    assert isinstance(result, MIRScopeEnds)
    assert tuple(result.ends) == (MIREdge(fn.blocks[1].id),)
    event, = result.ends[MIREdge(fn.blocks[1].id)]
    assert event.payloads == frozenset({MIRPlace(fn.slots[2].id, (MIRUnionPayload(1),))})


@pytest.mark.parametrize("damage, message", [
    ("cycle", "within region activation"),
    ("residence", "binding residence"),
    ("foreign", "storage region"),
    ("parent", "ancestry"),
    ("outside_read", "outside binding residence"),
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
        fn = bind_parameters(replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], residence=None))))
    elif damage == "foreign":
        fn = bind_parameters(replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], storage_duration=MIRRegionId(fn.id, 9)))))
    elif damage == "parent":
        fn = replace(fn, regions=(fn.regions[0], replace(fn.regions[1], parent=fn.regions[1].id)))
    elif damage == "outside_read":
        fn = replace(fn, blocks=(entry, iteration, replace(after, statements=iteration.statements)))
    elif damage == "body_residence":
        fn = bind_parameters(replace(fn, slots=(*fn.slots[:2], replace(fn.slots[2], storage_duration=MIRStorageDuration.BODY))))
    elif damage == "interior_entry":
        interior = replace(iteration, id=MIRBlockId(fn.id, 3), statements=())
        fn = replace(fn, blocks=(replace(entry, terminator=MIRGoto(interior.id)), iteration, after, interior))
    elif damage == "read_before_init":
        read = MIRAssign(MIRPlace(fn.slots[1].id), MIRIsAlternative(MIRPlace(fn.slots[2].id), (1,)))
        fn = replace(fn, blocks=(entry, replace(iteration, statements=(read, *iteration.statements)), after))
    with pytest.raises(MIRValidationError, match=message):
        validate_function(fn)


def test_skipped_region_and_absent_payload_are_distinct() -> None:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    empty = replace(iteration.statements[0], value=MIRWrap(0))
    absent = replace(fn, blocks=(entry, replace(iteration, statements=(empty,)), after))
    result = analyze_scope_ends(prepared(absent))
    assert len(result.ends) == 1
    assert not next(iter(result.ends.values()))[0].payloads
    skipped = replace(fn, blocks=(replace(entry, statements=(MIRAssign(
        MIRPlace(fn.slots[1].id), MIRConstant(False)),)), iteration, after))
    assert not analyze_scope_ends(prepared(skipped)).ends


def test_edge_selection_survives_equal_branch_destinations() -> None:
    fn = region_fixture()
    entry, iteration, after = fn.blocks
    parameter = replace(fn.slots[0], shape=optional_shape(OptionalType(INT32), leaf_shape(INT32)))
    guard = MIRSlot(MIRSlotId(fn.id, 3), leaf_shape(BOOL), MIRSlotKind.TEMPORARY, residence=iteration.region)
    from_source = replace(iteration.statements[0], value=MIRCopy(MIRPlace(parameter.id)))
    fn = bind_parameters(replace(fn, return_type=BOOL, slots=(parameter, *fn.slots[1:], guard), blocks=(
        entry, replace(iteration, statements=(from_source, MIRAssign(MIRPlace(guard.id), MIRIsAlternative(MIRPlace(fn.slots[2].id), (1,)))),
                       terminator=MIRBranch(guard.id, after.id, after.id)),
        replace(after, terminator=MIRReturn(fn.slots[1].id)))))
    result = analyze_scope_ends(prepared(fn))
    assert len(result.ends) == 2
    assert result.ends[MIREdge(iteration.id, 0)][0].payloads
    assert not result.ends[MIREdge(iteration.id, 1)][0].payloads


def test_bounded_trace_counts_distinct_dynamic_activations() -> None:
    fn = region_fixture()
    inventory = analyze_scope_ends(prepared(fn))
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
