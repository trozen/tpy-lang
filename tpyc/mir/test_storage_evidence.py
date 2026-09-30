"""Storage-origin evidence certifies only complete facts about required roots."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from ..thir.nodes import Form, THIRBorrowedRecord
from ..thir.testutil import _compile, _entry
from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, NominalType, OptionalType, ReadonlyType
from ..mir_workspace import analyze_call_workspace
from .collect import call_definitions
from .definitions import MIRDefinitions
from .dependencies import _dependencies
from .liveness import _liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBorrow, MIRBranch,
    MIRConstant, MIRConstruct, MIRDeref, MIREdge, MIRField, MIRFieldId, MIRFunction, MIRGoto,
    MIRIsPresent, MIROptionalConstruct, MIROptionalLayout, MIRPlace, MIRPoint, MIRRead,
    MIRRecordLayout, MIRRecordStorageInit, MIRRecordStorageKind, MIRRecordWrite, MIRRecordWriteMode,
    MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRValueKind,
)
from .scope_lifetime import inspect_scope_lifetimes
from .storage_evidence import (
    MIRStorageConflictKind, MIRStorageEvidence, MIRStorageVerdict, _storage_evidence,
    certify_storage_origins,
)
from .test_payload_inspection import ALIAS, EXTRACT, READ, RESULT, alias_function
from .test_payload_lifetime import CURRENT, payload, write
from .test_scope_inspection import stale_scalar_alias
from .validate import MIRPresenceError, MIRValidationError, _prepare_function


SOURCE = '''from tpy import int32, readonly

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

class Hooked:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def __del__(self) -> None:
        pass

def observe(cell: Cell) -> readonly[Cell]:
    return cell

def choose(flag: bool, a: readonly[Cell], b: readonly[Cell]) -> readonly[Cell]:
    return a if flag else b

def poke(cell: Cell) -> None:
    cell.value = 2

def direct_escape(flag: bool) -> int32:
    saved = Cell(0)
    if flag:
        local = Cell(1)
        saved = local
    return saved.value

def call_escape(flag: bool, owner: Cell) -> int32:
    saved = observe(owner)
    if flag:
        local = Cell(1)
        saved = observe(local)
    return saved.value

def chain_escape(flag: bool, owner: Cell) -> int32:
    saved = observe(owner)
    if flag:
        local = Cell(1)
        first = observe(local)
        second = first
        saved = choose(flag, second, owner)
    return saved.value

def reseat_escape(flag: bool, owner: Cell) -> int32:
    outer = observe(owner)
    if flag:
        local = Cell(1)
        saved = observe(local)
        outer = saved
        saved = observe(owner)
    return outer.value

def loop_replaced(flag: bool, owner: Cell) -> int32:
    saved = observe(owner)
    result = 0
    while flag:
        earlier = saved
        local = Cell(1)
        result = earlier.value
        saved = observe(local)
        flag = False
    return result

def dead_alias(flag: bool) -> int32:
    result = 0
    if flag:
        local = Cell(1)
        alias = local
        result = alias.value
    return result

def reseat_safe(flag: bool, owner: Cell) -> int32:
    result = 0
    if flag:
        local = Cell(1)
        saved = observe(local)
        earlier = saved
        saved = observe(owner)
        result = earlier.value
    return result

def durable(flag: bool, owner: Cell) -> int32:
    saved = observe(owner)
    result = 0
    if flag:
        local = Cell(1)
        probe = observe(local)
        result = probe.value
        saved = choose(flag, owner, owner)
    return saved.value

def loop_reread(flag: bool, owner: Cell) -> int32:
    saved = observe(owner)
    result = 0
    while flag:
        result = saved.value
        local = Cell(1)
        saved = observe(local)
        flag = False
    return result

def loop_scoped(flag: bool) -> int32:
    result = 0
    while flag:
        local = Cell(1)
        saved = observe(local)
        result = saved.value
        flag = False
    return result

def returns_external(flag: bool, owner: Cell) -> readonly[Cell]:
    local = Cell(1)
    saved = choose(flag, local, owner)
    if saved.value > 0:
        return owner
    return owner

def writes_local(flag: bool) -> int32:
    local = Cell(1)
    poke(local)
    return local.value

class Holder:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def escape(self, flag: bool, owner: Cell) -> int32:
        saved = observe(owner)
        if flag:
            local = Cell(1)
            saved = observe(local)
        return saved.value

class Observer:
    value: int32
    def __init__(self, flag: bool):
        self.value = 0
        outer = Cell(0)
        saved = observe(outer)
        if flag:
            local = Cell(1)
            saved = observe(local)
        self.value = saved.value
'''


Artifacts = tuple[dict[str, MIRFunction], MIRDefinitions]


@pytest.fixture(scope="module")
def artifacts() -> Artifacts:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    definitions = MIRDefinitions(tuple(ctx.thir_constructors.values()))
    workspace = analyze_call_workspace(call_definitions(ctx, entry.name), definitions)
    bodies = {body.declaration.split("@")[0]: fn for body, fn in workspace.bodies.items()}
    method = next(fn for fn in ctx.thir_functions.values() if fn.name == "escape")
    bodies["escape"] = lower_function(method, MIRBodyId("main", "escape"), kind=MIRBodyKind.METHOD,
                                      definitions=definitions, summaries=workspace.summaries)
    ctor = next(c for c in ctx.thir_constructors.values() if c.record_name == "Observer")
    bodies["Observer"] = lower_constructor(ctor, MIRBodyId("main", "Observer"), definitions=definitions,
                                           summaries=workspace.summaries)
    return bodies, definitions


def _storage(fn: MIRFunction) -> frozenset[MIRSlotId]:
    return frozenset(s.id for s in fn.slots if s.value_kind is MIRValueKind.RECORD_STORAGE)


def _named(fn: MIRFunction, sid: MIRSlotId) -> str | None:
    return next(s.name for s in fn.slots if s.id == sid)


def _source(artifacts: Artifacts, name: str) -> tuple[MIRFunction, MIRStorageEvidence]:
    bodies, definitions = artifacts
    fn = bodies[name]
    assert isinstance(fn, MIRFunction), fn
    return fn, certify_storage_origins(fn, _storage(fn), definitions)


@pytest.mark.parametrize("name, kind, holder", [
    ("direct_escape", MIRStorageConflictKind.SCOPE_END, "saved"),
    ("call_escape", MIRStorageConflictKind.SCOPE_END, "saved"),
    ("chain_escape", MIRStorageConflictKind.SCOPE_END, "saved"),
    # Reseating `saved` does not erase the dependency `outer` already copied.
    ("reseat_escape", MIRStorageConflictKind.SCOPE_END, "outer"),
    # The hoisted site is replaced while `earlier` still holds the old object.
    ("loop_replaced", MIRStorageConflictKind.REPLACEMENT, "earlier"),
    ("escape", MIRStorageConflictKind.SCOPE_END, "saved"),
    ("Observer", MIRStorageConflictKind.SCOPE_END, "saved"),
])
def test_escaping_dependencies_conflict(artifacts: Artifacts, name: str,
                                        kind: MIRStorageConflictKind, holder: str) -> None:
    fn, evidence = _source(artifacts, name)
    assert evidence.verdict is MIRStorageVerdict.CONFLICT, evidence.gaps
    assert not evidence.gaps
    assert {c.kind for c in evidence.conflicts} == {kind}
    assert {_named(fn, c.holder.root) for c in evidence.conflicts} == {holder}
    assert all(c.origin.root in evidence.required and c.loc is not None for c in evidence.conflicts)
    assert not evidence.certifies(fn, evidence.required)


@pytest.mark.parametrize("name", ["dead_alias", "reseat_safe", "durable", "loop_reread",
                                  "loop_scoped", "returns_external", "writes_local"])
def test_contained_or_external_dependencies_certify(artifacts: Artifacts, name: str) -> None:
    fn, evidence = _source(artifacts, name)
    assert evidence.verdict is MIRStorageVerdict.CERTIFIED, (evidence.conflicts, evidence.gaps)
    assert evidence.certifies(fn, evidence.required)
    if name == "loop_scoped":
        # Every iteration ends its own activation of the same static backing.
        assert all(isinstance(s.storage_duration, MIRRegionId) for s in fn.slots if s.id in evidence.required)


def test_certificate_is_bound_to_the_exact_function_and_roots(artifacts: Artifacts) -> None:
    fn, evidence = _source(artifacts, "durable")
    assert evidence.function is fn and evidence.required == _storage(fn)
    twin = replace(fn)
    assert twin == fn and not evidence.certifies(twin, evidence.required)
    parameter = next(s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER)
    assert not evidence.certifies(fn, evidence.required | {parameter})


def test_conflict_on_other_storage_withholds_but_is_not_attributed(artifacts: Artifacts) -> None:
    bodies, definitions = artifacts
    fn = bodies["direct_escape"]
    body = frozenset(s.id for s in fn.slots if s.storage_duration is MIRStorageDuration.BODY)
    evidence = certify_storage_origins(fn, body, definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert not evidence.conflicts
    assert any("outside the required origins" in g.reason for g in evidence.gaps)


# Hand-built bodies over the verified `Cell` definition from the source above.

def _cell(definitions: MIRDefinitions, name: str = "Cell") -> tuple[NominalType, MIRRecordLayout]:
    typ = next(t for t in definitions.records if t.name == name)
    return typ, definitions.get(None, typ).layout


def _ref(sid: MIRSlotId, typ: NominalType, kind: MIRSlotKind, residence: MIRRegionId | None = None) -> MIRSlot:
    return MIRSlot(sid, typ, kind, form=Form.BORROW, value_kind=MIRValueKind.BORROWED_RECORD,
                   readonly=True, residence=residence)


LOOP = MIRBodyId("storage_evidence", "loop")
FLAG, OWNER, VALUE, SAVED, BACKING, OUT, EARLIER = (MIRSlotId(LOOP, i) for i in range(7))
ENTRY, HEADER, ITERATION, AFTER = (MIRBlockId(LOOP, i) for i in range(4))
ROOT, INNER = MIRRegionId(LOOP, 0), MIRRegionId(LOOP, 1)


def activation(definitions: MIRDefinitions, *, retain: bool, optional: bool = False) -> MIRFunction:
    """Each iteration constructs a fresh region activation of one static backing."""
    cell, layout = _cell(definitions)
    field = layout.fields[0]
    backing = MIRSlot(BACKING, cell, MIRSlotKind.LOCAL, form=Form.STORAGE, value_kind=MIRValueKind.RECORD_STORAGE,
                      storage_duration=INNER, residence=INNER,
                      record_storage=MIRRecordStorageKind.OPTIONAL if optional else MIRRecordStorageKind.DIRECT)
    read = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(SAVED, (MIRDeref(), field))))
    construct = MIRAssign(MIRPlace(BACKING), MIRConstruct((VALUE,)), storage_write=MIRRecordWrite(
        MIRRecordWriteMode.OPTIONAL_ASSIGN if optional else MIRRecordWriteMode.INITIALIZE_REGION))
    iteration = ((MIRRecordStorageInit(MIRPlace(BACKING)),) if optional else ()) + (
        construct, MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(BACKING))), read)
    if not retain:
        iteration += (MIRAssign(MIRPlace(SAVED), MIRAlias(OWNER)),)
    return MIRFunction(LOOP, INT32, (
        MIRSlot(FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), _ref(OWNER, cell, MIRSlotKind.PARAMETER),
        MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), _ref(SAVED, cell, MIRSlotKind.LOCAL, ROOT), backing,
        MIRSlot(OUT, INT32, MIRSlotKind.LOCAL, residence=ROOT)), (
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(SAVED), MIRAlias(OWNER)), MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(VALUE)))),
                 MIRGoto(HEADER), ROOT),
        MIRBlock(HEADER, (), MIRBranch(FLAG, ITERATION, AFTER), ROOT),
        MIRBlock(ITERATION, iteration, MIRGoto(HEADER), INNER),
        # Reading `saved` after the loop keeps it live across every iteration end.
        MIRBlock(AFTER, (read,), MIRReturn(OUT), ROOT)),
        ENTRY, (layout,), regions=(MIRRegion(ROOT, None, ENTRY), MIRRegion(INNER, ROOT, ITERATION)))


@pytest.mark.parametrize("optional", [False, True])
def test_loop_activation_checks_each_end_before_the_backedge(artifacts: Artifacts, optional: bool) -> None:
    definitions = artifacts[1]
    unsafe = certify_storage_origins(activation(definitions, retain=True, optional=optional),
                                     frozenset({BACKING}), definitions)
    assert unsafe.verdict is MIRStorageVerdict.CONFLICT
    # The backedge ends this activation while `saved` stays live into the next one.
    assert {c.site for c in unsafe.conflicts} == {MIREdge(ITERATION)}
    assert {(c.origin, c.holder) for c in unsafe.conflicts} == {(MIRPlace(BACKING), MIRPlace(SAVED))}
    safe = certify_storage_origins(activation(definitions, retain=False, optional=optional),
                                   frozenset({BACKING}), definitions)
    assert safe.verdict is MIRStorageVerdict.CERTIFIED, (safe.conflicts, safe.gaps)


def _without_engagement(prepared, *, edge: MIREdge | None = None, point: MIRPoint | None = None):
    presence = prepared.presence

    def drop(facts: frozenset) -> frozenset:
        return frozenset(f for f in facts if f[0] != BACKING)

    if edge is not None:
        presence = replace(presence, edge_engagement=MappingProxyType(
            {e: drop(f) if e == edge else f for e, f in presence.edge_engagement.items()}))
    if point is not None:
        presence = replace(presence, engagement=MappingProxyType(
            {p: drop(f) if p == point else f for p, f in presence.engagement.items()}))
    return replace(prepared, presence=presence)


@pytest.mark.parametrize("retain", [False, True])
def test_missing_end_engagement_is_not_a_clean_skip(artifacts: Artifacts, retain: bool) -> None:
    definitions = artifacts[1]
    fn = activation(definitions, retain=retain, optional=True)
    prepared = _without_engagement(_prepare_function(fn), edge=MIREdge(ITERATION))
    live = _liveness(prepared)
    evidence = _storage_evidence(prepared, live, _dependencies(prepared, live), frozenset({BACKING}), definitions)
    # The object end (and its conflict) disappears with the fact; the wrapper end remains.
    assert not evidence.conflicts
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("ends without an engagement fact" in g.reason for g in evidence.gaps)


def reused(definitions: MIRDefinitions, *, retain: bool) -> MIRFunction:
    """One body-lifetime optional backing is reassigned on every iteration."""
    cell, layout = _cell(definitions)
    field = layout.fields[0]
    fn = activation(definitions, retain=False, optional=True)
    slots = tuple(replace(s, storage_duration=MIRStorageDuration.BODY, residence=ROOT) if s.id == BACKING else s
                  for s in fn.slots) + (_ref(EARLIER, cell, MIRSlotKind.LOCAL, ROOT),)
    replace_site = MIRAssign(MIRPlace(BACKING), MIRConstruct((VALUE,)),
                             storage_write=MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
    read_earlier = MIRAssign(MIRPlace(OUT), MIRRead(MIRPlace(EARLIER, (MIRDeref(), field))))
    iteration = ((MIRAssign(MIRPlace(EARLIER), MIRAlias(SAVED)),) if retain else ()) + (
        replace_site, *((read_earlier,) if retain else ()), MIRAssign(MIRPlace(SAVED), MIRBorrow(MIRPlace(BACKING))))
    entry, header, _, after = fn.blocks
    return replace(fn, slots=slots, regions=(fn.regions[0],), blocks=(
        replace(entry, statements=(MIRRecordStorageInit(MIRPlace(BACKING)), *entry.statements)), header,
        MIRBlock(ITERATION, iteration, MIRGoto(HEADER), ROOT), after))


def test_reassigned_backing_keeps_prior_alias_and_needs_engagement(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    unsafe = certify_storage_origins(reused(definitions, retain=True), frozenset({BACKING}), definitions)
    assert unsafe.verdict is MIRStorageVerdict.CONFLICT
    conflict, = unsafe.conflicts
    assert conflict.kind is MIRStorageConflictKind.REPLACEMENT and conflict.holder == MIRPlace(EARLIER)
    fn = reused(definitions, retain=False)
    assert certify_storage_origins(fn, frozenset({BACKING}), definitions).verdict is MIRStorageVerdict.CERTIFIED
    prepared = _without_engagement(_prepare_function(fn), point=MIRPoint(ITERATION, 0))
    live = _liveness(prepared)
    evidence = _storage_evidence(prepared, live, _dependencies(prepared, live), frozenset({BACKING}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("replaced without an engagement fact" in g.reason for g in evidence.gaps)


def test_missing_entire_engagement_point_is_not_a_clean_skip(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = reused(definitions, retain=False)
    prepared = _prepare_function(fn)
    point = MIRPoint(ITERATION, 0)
    assert point in prepared.presence.points
    presence = replace(prepared.presence, engagement=MappingProxyType({
        p: facts for p, facts in prepared.presence.engagement.items() if p != point}))
    prepared = replace(prepared, presence=presence)
    live = _liveness(prepared)
    evidence = _storage_evidence(prepared, live, _dependencies(prepared, live), frozenset({BACKING}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("replaced without an engagement fact" in g.reason for g in evidence.gaps)


def test_infeasible_replacement_needs_no_engagement_fact(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = reused(definitions, retain=False)
    entry = fn.blocks[0]
    fn = replace(fn, blocks=(replace(entry, statements=(
        *entry.statements, MIRAssign(MIRPlace(FLAG), MIRConstant(False)))), *fn.blocks[1:]))
    prepared = _prepare_function(fn)
    point = MIRPoint(ITERATION, 0)
    assert point not in prepared.presence.points and point not in prepared.presence.engagement
    evidence = certify_storage_origins(fn, frozenset({BACKING}), definitions)
    assert not any("replaced without an engagement fact" in g.reason for g in evidence.gaps)


def _drop_origins(deps, holder: MIRPlace):
    return replace(deps, referents=MappingProxyType({
        point: MappingProxyType({leaf: refs for leaf, refs in state.items() if leaf != holder})
        for point, state in deps.referents.items()}))


@pytest.mark.parametrize("retain", [False, True])
def test_live_holder_without_origins_is_unknown(artifacts: Artifacts, retain: bool) -> None:
    definitions = artifacts[1]
    prepared = _prepare_function(activation(definitions, retain=retain))
    live = _liveness(prepared)
    deps = _drop_origins(_dependencies(prepared, live), MIRPlace(SAVED))
    evidence = _storage_evidence(prepared, live, deps, frozenset({BACKING}), definitions)
    assert not evidence.conflicts
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("has unknown origins" in g.reason for g in evidence.gaps)


RETURN = MIRBodyId("storage_evidence", "result")
R_FLAG, R_OWNER, R_VALUE, R_LOCAL, R_SPARE, R_SAVED = (MIRSlotId(RETURN, i) for i in range(6))
R_ENTRY, R_LOCAL_ARM, R_OWNER_ARM = (MIRBlockId(RETURN, i) for i in range(3))


def returning(definitions: MIRDefinitions, *, escape: bool) -> MIRFunction:
    cell, layout = _cell(definitions)
    region = MIRRegionId(RETURN, 0)
    storage = MIRSlot(R_LOCAL, cell, MIRSlotKind.LOCAL, form=Form.STORAGE, value_kind=MIRValueKind.RECORD_STORAGE,
                      storage_duration=MIRStorageDuration.BODY, residence=region)
    once = MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_ONCE)
    local_arm = MIRBorrow(MIRPlace(R_LOCAL)) if escape else MIRAlias(R_OWNER)
    return MIRFunction(RETURN, ReadonlyType(cell), (
        MIRSlot(R_FLAG, BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), _ref(R_OWNER, cell, MIRSlotKind.PARAMETER),
        MIRSlot(R_VALUE, INT32, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE), storage, replace(storage, id=R_SPARE),
        _ref(R_SAVED, cell, MIRSlotKind.LOCAL, region)), (
        MIRBlock(R_ENTRY, (MIRAssign(MIRPlace(R_LOCAL), MIRConstruct((R_VALUE,)), storage_write=once),
                           MIRAssign(MIRPlace(R_SPARE), MIRConstruct((R_VALUE,)), storage_write=once)),
                 MIRBranch(R_FLAG, R_LOCAL_ARM, R_OWNER_ARM), region),
        MIRBlock(R_LOCAL_ARM, (MIRAssign(MIRPlace(R_SAVED), local_arm),), MIRReturn(R_SAVED), region),
        MIRBlock(R_OWNER_ARM, (MIRAssign(MIRPlace(R_SAVED), MIRAlias(R_OWNER)),), MIRReturn(R_SAVED), region)),
        R_ENTRY, (layout,), regions=(MIRRegion(region, None, R_ENTRY),),
        borrowed_result=THIRBorrowedRecord(cell, True))


def test_borrowed_return_needs_explicit_origin_proof(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = returning(definitions, escape=True)
    # No successor is live after a return, so scope-end inspection sees nothing.
    assert inspect_scope_lifetimes(fn).conflicts == ()
    evidence = certify_storage_origins(fn, frozenset({R_LOCAL}), definitions)
    assert evidence.verdict is MIRStorageVerdict.CONFLICT
    conflict, = evidence.conflicts
    assert conflict.kind is MIRStorageConflictKind.RETURN_ESCAPE
    assert (conflict.origin, conflict.holder, conflict.site) == (MIRPlace(R_LOCAL), MIRPlace(R_SAVED),
                                                               MIREdge(R_LOCAL_ARM))
    other = certify_storage_origins(fn, frozenset({R_SPARE}), definitions)
    assert other.verdict is MIRStorageVerdict.NOT_COVERED and not other.conflicts
    external = returning(definitions, escape=False)
    assert certify_storage_origins(external, frozenset({R_LOCAL}), definitions).verdict is MIRStorageVerdict.CERTIFIED


def test_missing_return_origin_is_not_covered(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    prepared = _prepare_function(returning(definitions, escape=True))
    live = _liveness(prepared)
    deps = _drop_origins(_dependencies(prepared, live), MIRPlace(R_SAVED))
    evidence = _storage_evidence(prepared, live, deps, frozenset({R_LOCAL}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(g.reason == "borrowed return has unknown origins" for g in evidence.gaps)


def test_absent_payload_needs_a_positive_selection_fact(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    cell, _ = _cell(definitions)
    fn = returning(definitions, escape=False)
    maybe, guard = MIRSlotId(RETURN, 6), MIRSlotId(RETURN, 7)
    region = fn.regions[0].id
    entry = fn.blocks[0]
    fn = replace(fn, slots=(*fn.slots, MIRSlot(
        maybe, OptionalType(cell), MIRSlotKind.LOCAL, value_kind=MIRValueKind.OPTIONAL,
        optional_layout=MIROptionalLayout(cell, MIRValueKind.BORROWED_RECORD), residence=region),
        MIRSlot(guard, BOOL, MIRSlotKind.LOCAL, residence=region)), blocks=(replace(entry, statements=(
            *entry.statements, MIRAssign(MIRPlace(maybe), MIROptionalConstruct()),
            MIRAssign(MIRPlace(guard), MIRIsPresent(maybe)))), *fn.blocks[1:]))
    assert certify_storage_origins(fn, frozenset({R_LOCAL}), definitions).verdict is MIRStorageVerdict.CERTIFIED
    prepared = _prepare_function(fn)
    presence = prepared.presence
    unknown = replace(prepared, presence=replace(presence, points=MappingProxyType(
        {p: frozenset(f for f in facts if f[0] != maybe) for p, facts in presence.points.items()})))
    live = _liveness(unknown)
    evidence = _storage_evidence(unknown, live, _dependencies(unknown, live), frozenset({R_LOCAL}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(f"%{maybe.index} has unknown origins" in g.reason for g in evidence.gaps)


def test_missing_placement_or_write_facts_are_not_covered(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = returning(definitions, escape=True)
    fn = replace(fn, slots=tuple(replace(s, storage_duration=None) if s.id == R_LOCAL else s for s in fn.slots),
                 blocks=tuple(replace(b, statements=tuple(
                     replace(stmt, storage_write=None) if stmt.target == MIRPlace(R_LOCAL) else stmt
                     for stmt in b.statements)) for b in fn.blocks))
    evidence = certify_storage_origins(fn, frozenset({R_SPARE}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("missing storage duration" in g.reason for g in evidence.gaps)


def test_unmaterialized_required_origin_is_not_covered(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = activation(definitions, retain=True)
    entry = fn.blocks[0]
    # The loop never runs: its backing is never constructed on a feasible path.
    fn = replace(fn, blocks=(replace(entry, statements=(
        *entry.statements, MIRAssign(MIRPlace(FLAG), MIRConstant(False)))), *fn.blocks[1:]))
    evidence = certify_storage_origins(fn, frozenset({BACKING}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("never materialized" in g.reason for g in evidence.gaps)


@pytest.mark.parametrize("damage", ["hooked", "layout"])
def test_storage_records_need_verified_hook_free_definitions(artifacts: Artifacts, damage: str) -> None:
    definitions = artifacts[1]
    fn = activation(definitions, retain=False)
    if damage == "hooked":
        hooked = next(t for t in definitions.records if t.name == "Hooked")
        _, layout = _cell(definitions)
        field = MIRField(MIRFieldId(hooked, layout.fields[0].id.name), INT32)
        fn = replace(fn, records=(*fn.records, MIRRecordLayout(hooked, (field,), True, True)),
                     slots=tuple(replace(s, type=hooked) if s.id == BACKING else s for s in fn.slots),
                     blocks=tuple(replace(b, statements=tuple(
                         replace(stmt, value=MIRAlias(OWNER)) if stmt.target == MIRPlace(SAVED)
                         and isinstance(stmt.value, MIRBorrow) else stmt for stmt in b.statements))
                         for b in fn.blocks))
        reason = "custom record special member"
    else:
        fn = replace(fn, records=(replace(fn.records[0], copyable=not fn.records[0].copyable),))
        reason = "layout differs"
    evidence = certify_storage_origins(fn, frozenset({BACKING}), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any(reason in g.reason for g in evidence.gaps)


def _region(fn: MIRFunction) -> MIRFunction:
    root = MIRRegionId(fn.id, 0)
    return replace(fn, slots=tuple(s if s.kind is MIRSlotKind.PARAMETER else replace(s, residence=root)
                                   for s in fn.slots),
                   blocks=tuple(replace(b, region=root) for b in fn.blocks), regions=(MIRRegion(root, None, fn.entry),))


@pytest.mark.parametrize("tag, verdict", [(1, MIRStorageVerdict.CONFLICT), (2, MIRStorageVerdict.NOT_COVERED)])
def test_stale_alias_never_certifies(tag: int, verdict: MIRStorageVerdict) -> None:
    # Retagging ends the payload; a same-tag write does not, but the alias is still stale.
    fn = _region(alias_function(EXTRACT, write(False, tag), READ))
    evidence = certify_storage_origins(fn, frozenset({CURRENT}), MIRDefinitions())
    assert evidence.verdict is verdict
    assert any(g.reason.startswith("stale alias") for g in evidence.gaps)
    if tag == 1:
        conflict, = evidence.conflicts
        assert conflict.kind is MIRStorageConflictKind.PAYLOAD_END
        assert (conflict.origin, conflict.holder) == (payload(False), MIRPlace(ALIAS))


def test_stale_alias_across_scope_end_conflicts() -> None:
    evidence = certify_storage_origins(stale_scalar_alias(), frozenset({CURRENT}), MIRDefinitions())
    conflict, = evidence.conflicts
    assert conflict.kind is MIRStorageConflictKind.SCOPE_END and conflict.holder == MIRPlace(ALIAS)


def test_malformed_mir_and_requests_raise(artifacts: Artifacts) -> None:
    definitions = artifacts[1]
    fn = activation(definitions, retain=False)
    with pytest.raises(MIRPresenceError, match="alternative proof"):
        unchecked = MIRAssign(MIRPlace(RESULT), MIRRead(payload(False)))
        certify_storage_origins(_region(alias_function(write(False, 0), unchecked)),
                                frozenset({CURRENT}), MIRDefinitions())
    # Repeated initialization is a producer defect, never an uncovered result.
    iteration = fn.blocks[2]
    twice = replace(fn, blocks=(*fn.blocks[:2], replace(iteration, statements=(
        iteration.statements[0], *iteration.statements)), fn.blocks[3]))
    with pytest.raises(MIRValidationError, match="repeated storage initialization"):
        certify_storage_origins(twice, frozenset({BACKING}), definitions)
    for roots in (frozenset(), frozenset({OWNER}), frozenset({MIRSlotId(RETURN, BACKING.index)})):
        with pytest.raises(MIRValidationError):
            certify_storage_origins(fn, roots, definitions)
    prepared = _prepare_function(fn)
    live = _liveness(prepared)
    deps = _dependencies(prepared, live)
    with pytest.raises(MIRValidationError, match="different MIR function"):
        _storage_evidence(prepared, replace(live, function=replace(fn)), deps, frozenset({BACKING}), definitions)
    with pytest.raises(MIRValidationError, match="different MIR function"):
        _storage_evidence(prepared, live, replace(deps, function=replace(fn)), frozenset({BACKING}), definitions)
