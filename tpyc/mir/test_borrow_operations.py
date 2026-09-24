"""Operation demands retain exact origins and share whole-body lifetime checks."""

from dataclasses import replace
from types import MappingProxyType

import pytest

from ..thir.nodes import Form, THIRBorrowedRecord
from ..thir.testutil import _compile, _entry
from ..typesys import BOOL, INT32, ReadonlyType, TupleType
from .definitions import MIRDefinitions
from .dependencies import MIRReferent, _dependencies
from .liveness import _liveness
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBorrow, MIRBranch,
    MIRConstant, MIRConstruct, MIRDeref, MIREdge, MIRFunction, MIRGoto, MIRNotCovered, MIRPlace, MIRPoint,
    MIRRead, MIRRegion, MIRRegionId, MIRReturn, MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageDuration,
    MIRTupleConstruct, MIRTupleElement, MIRTupleIndex, MIRTupleInitialization, MIRTupleLayout, MIRValueKind,
)
from .storage_evidence import (
    MIRBorrowEvidence, MIRStorageConflictKind, MIRStorageVerdict, _borrow_evidence,
    certify_borrow_operations, certify_storage_origins,
)
from .test_storage_evidence import (
    BACKING, FLAG, ITERATION, OWNER, R_LOCAL, R_LOCAL_ARM, R_OWNER_ARM, R_SAVED,
    SAVED, _cell, _ref, activation, returning, reused,
)
from .validate import MIRValidationError, _prepare_function


@pytest.fixture(scope="module")
def definitions() -> MIRDefinitions:
    compiler, modules = _compile('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
''')
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return MIRDefinitions(tuple(ctx.thir_constructors.values()))


BODY = MIRBodyId("borrow_operations", "forward")
FIRST, SECOND, CURRENT, ALIAS, VALUE = (MIRSlotId(BODY, i) for i in range(5))
ENTRY = MIRBlockId(BODY, 0)
ROOT = MIRRegionId(BODY, 0)


def forwarding(definitions: MIRDefinitions, *, dead: bool = False) -> MIRFunction:
    cell, layout = _cell(definitions)
    return MIRFunction(BODY, INT32 if dead else ReadonlyType(cell), (
        _ref(FIRST, cell, MIRSlotKind.PARAMETER), _ref(SECOND, cell, MIRSlotKind.PARAMETER),
        _ref(CURRENT, cell, MIRSlotKind.LOCAL, ROOT), _ref(ALIAS, cell, MIRSlotKind.LOCAL, ROOT),
        MIRSlot(VALUE, INT32, MIRSlotKind.PARAMETER)), (
        MIRBlock(ENTRY, (MIRAssign(MIRPlace(CURRENT), MIRAlias(FIRST)),
                         MIRAssign(MIRPlace(ALIAS), MIRAlias(CURRENT)),
                         MIRAssign(MIRPlace(CURRENT), MIRAlias(SECOND))),
                 MIRReturn(VALUE if dead else ALIAS), ROOT),), ENTRY, (layout,),
        regions=(MIRRegion(ROOT, None, ENTRY),),
        borrowed_result=None if dead else THIRBorrowedRecord(cell, True))


def operations(fn: MIRFunction) -> frozenset[MIRPoint]:
    slots = {s.id: s for s in fn.slots}
    writes = {MIRPoint(b.id, i) for b in fn.blocks for i, stmt in enumerate(b.statements)
              if isinstance(stmt, MIRAssign) and not stmt.target.projections
              and slots[stmt.target.root].value_kind is MIRValueKind.BORROWED_RECORD}
    returns = {MIRPoint(b.id, len(b.statements)) for b in fn.blocks
               if fn.borrowed_result is not None and isinstance(b.terminator, MIRReturn)}
    return frozenset(writes | returns)


def test_parameter_only_reseat_retains_each_operation_origin(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions)
    demand = operations(fn)
    evidence = certify_borrow_operations(fn, demand, frozenset(), definitions)
    assert isinstance(evidence, MIRBorrowEvidence)
    assert evidence.verdict is MIRStorageVerdict.CERTIFIED, evidence.gaps
    assert evidence.certifies_operations(fn, demand, frozenset())
    assert evidence.required == evidence.explicit_roots == frozenset()
    first = frozenset({MIRReferent(MIRPlace(FIRST), external=True)})
    second = frozenset({MIRReferent(MIRPlace(SECOND), external=True)})
    assert evidence.origins == {MIRPoint(ENTRY, 0): first, MIRPoint(ENTRY, 1): first,
                                MIRPoint(ENTRY, 2): second, MIRPoint(ENTRY, 3): first}


def test_evidence_binds_exact_demands_without_a_storage_only_query(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions)
    demand = operations(fn)
    evidence = certify_borrow_operations(fn, demand, frozenset(), definitions)
    assert not hasattr(evidence, "certifies")
    assert not evidence.certifies_operations(replace(fn), demand, frozenset())
    assert not evidence.certifies_operations(fn, demand - {MIRPoint(ENTRY, 0)}, frozenset())
    assert not evidence.certifies_operations(fn, demand | {MIRPoint(ENTRY, 4)}, frozenset())
    assert not evidence.certifies_operations(fn, demand, frozenset({FIRST}))
    assert not evidence.certifies_operations(fn, frozenset(), frozenset())
    with pytest.raises(TypeError):
        evidence.origins[MIRPoint(ENTRY, 0)] = frozenset()
    with pytest.raises(MIRValidationError, match="demanded operations"):
        certify_borrow_operations(fn, frozenset(), frozenset(), definitions)
    with pytest.raises(MIRValidationError, match="required origins"):
        certify_storage_origins(fn, frozenset(), definitions)


def test_dead_destination_still_has_a_demanded_origin(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions, dead=True)
    prepared = _prepare_function(fn)
    live = _liveness(prepared)
    deps = _dependencies(prepared, live)
    assert all(MIRPlace(ALIAS) not in state for state in deps.active.values())
    evidence = certify_borrow_operations(fn, operations(fn), frozenset(), definitions)
    assert evidence.verdict is MIRStorageVerdict.CERTIFIED
    assert evidence.origins[MIRPoint(ENTRY, 1)] == frozenset({MIRReferent(MIRPlace(FIRST), True)})


@pytest.mark.parametrize("retain", [False, True])
@pytest.mark.parametrize("optional", [False, True])
def test_reached_local_storage_checks_each_loop_activation(definitions: MIRDefinitions,
                                                          retain: bool, optional: bool) -> None:
    fn = activation(definitions, retain=retain, optional=optional)
    evidence = certify_borrow_operations(fn, operations(fn), frozenset(), definitions)
    assert evidence.required == frozenset({BACKING})
    assert evidence.explicit_roots == frozenset()
    assert MIRReferent(MIRPlace(BACKING)) in set().union(*evidence.origins.values())
    if retain:
        assert evidence.verdict is MIRStorageVerdict.CONFLICT
        assert {c.kind for c in evidence.conflicts} == {MIRStorageConflictKind.SCOPE_END}
        assert {c.site for c in evidence.conflicts} == {MIREdge(ITERATION)}
        assert {c.holder for c in evidence.conflicts} == {MIRPlace(SAVED)}
    else:
        assert evidence.verdict is MIRStorageVerdict.CERTIFIED, evidence.gaps


@pytest.mark.parametrize("retain", [False, True])
def test_whole_body_replacement_checks_aliases_outside_the_demand(definitions: MIRDefinitions,
                                                                retain: bool) -> None:
    fn = reused(definitions, retain=retain)
    # Demanding only the first parameter alias cannot bypass a later dangling use.
    point = MIRPoint(fn.entry, 1)
    evidence = certify_borrow_operations(fn, frozenset({point}), frozenset(), definitions)
    assert evidence.origins[point] == frozenset({MIRReferent(MIRPlace(OWNER), True)})
    assert evidence.required == frozenset()
    assert evidence.verdict is (MIRStorageVerdict.NOT_COVERED if retain else MIRStorageVerdict.CERTIFIED)
    if retain:
        assert any("outside the required origins" in gap.reason for gap in evidence.gaps)


@pytest.mark.parametrize("escape", [False, True])
def test_returns_add_internal_origins_and_keep_conflicts_at_actual_exit(definitions: MIRDefinitions,
                                                                      escape: bool) -> None:
    fn = returning(definitions, escape=escape)
    demand = frozenset({MIRPoint(R_LOCAL_ARM, 1), MIRPoint(R_OWNER_ARM, 1)})
    evidence = certify_borrow_operations(fn, demand, frozenset(), definitions)
    assert evidence.origins.keys() == demand
    if escape:
        assert evidence.required == frozenset({R_LOCAL})
        assert evidence.verdict is MIRStorageVerdict.CONFLICT
        conflict, = evidence.conflicts
        assert (conflict.kind, conflict.origin, conflict.holder, conflict.site) == (
            MIRStorageConflictKind.RETURN_ESCAPE, MIRPlace(R_LOCAL), MIRPlace(R_SAVED), MIREdge(R_LOCAL_ARM))
    else:
        assert evidence.required == frozenset()
        assert evidence.verdict is MIRStorageVerdict.CERTIFIED


def test_explicit_roots_are_retained_and_exactly_bound(definitions: MIRDefinitions) -> None:
    fn = returning(definitions, escape=False)
    demand, roots = operations(fn), frozenset({R_LOCAL})
    evidence = certify_borrow_operations(fn, demand, roots, definitions)
    assert evidence.verdict is MIRStorageVerdict.CERTIFIED
    assert evidence.required == evidence.explicit_roots == roots
    assert evidence.certifies_operations(fn, demand, roots)
    assert not evidence.certifies_operations(fn, demand, frozenset())


@pytest.mark.parametrize("escape", [False, True])
def test_owned_tuple_member_infers_its_local_backing(definitions: MIRDefinitions, escape: bool) -> None:
    cell, layout = _cell(definitions)
    pair, result = MIRSlotId(BODY, 5), MIRSlotId(BODY, 6)
    member = MIRPlace(pair, (MIRTupleIndex(0),))
    fn = forwarding(definitions, dead=not escape)
    backing = MIRSlot(pair, TupleType((cell, INT32)), MIRSlotKind.LOCAL, form=Form.STORAGE,
                      value_kind=MIRValueKind.TUPLE, tuple_layout=MIRTupleLayout((
                          MIRTupleElement(cell, MIRValueKind.RECORD_STORAGE), MIRTupleElement(INT32))),
                      storage_duration=MIRStorageDuration.BODY, residence=ROOT)
    fn = replace(fn, slots=(*fn.slots, backing, MIRSlot(result, INT32, MIRSlotKind.LOCAL, residence=ROOT)),
                 blocks=(MIRBlock(ENTRY, (
                     MIRAssign(MIRPlace(pair), MIRTupleConstruct((MIRConstruct((VALUE,)), VALUE)),
                               storage_write=MIRTupleInitialization()),
                     MIRAssign(MIRPlace(ALIAS), MIRBorrow(member)),
                     MIRAssign(MIRPlace(result), MIRRead(MIRPlace(ALIAS, (MIRDeref(), layout.fields[0]))))),
                     MIRReturn(ALIAS if escape else result), ROOT),))
    demand = operations(fn)
    evidence = certify_borrow_operations(fn, demand, frozenset(), definitions)
    assert evidence.explicit_roots == frozenset()
    assert evidence.required == frozenset({pair})
    assert evidence.origins == {point: frozenset({MIRReferent(member)}) for point in demand}
    assert not evidence.gaps
    if escape:
        assert evidence.verdict is MIRStorageVerdict.CONFLICT
        conflict, = evidence.conflicts
        assert (conflict.kind, conflict.origin, conflict.holder, conflict.site) == (
            MIRStorageConflictKind.RETURN_ESCAPE, member, MIRPlace(ALIAS), MIREdge(ENTRY))
    else:
        assert evidence.verdict is MIRStorageVerdict.CERTIFIED
        assert evidence.certifies_operations(fn, demand, frozenset())


@pytest.mark.parametrize("damage", ["post_point", "dead_holder", "return_holder", "analysis"])
def test_missing_operation_facts_never_certify(definitions: MIRDefinitions, damage: str) -> None:
    fn = forwarding(definitions, dead=damage == "dead_holder")
    prepared = _prepare_function(fn)
    live = _liveness(prepared)
    deps = _dependencies(prepared, live)
    if damage == "analysis":
        deps = MIRNotCovered(fn.id, "dependencies", "unresolved origin")
    else:
        states = dict(deps.referents)
        point = MIRPoint(ENTRY, 2 if damage == "dead_holder" else 3 if damage == "return_holder" else 1)
        if damage == "post_point":
            del states[point]
        else:
            states[point] = MappingProxyType({p: refs for p, refs in states[point].items() if p != MIRPlace(ALIAS)})
        deps = replace(deps, referents=MappingProxyType(states))
    evidence = _borrow_evidence(prepared, live, deps, operations(fn), frozenset(), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert not evidence.conflicts


@pytest.mark.parametrize("index", [-1, 3, 4])
def test_missing_or_unsupported_operation_is_not_silently_dropped(definitions: MIRDefinitions,
                                                                index: int) -> None:
    fn = forwarding(definitions, dead=True)
    demand = operations(fn) | {MIRPoint(ENTRY, index)}
    evidence = certify_borrow_operations(fn, demand, frozenset(), definitions)
    assert evidence.operations == demand
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED


def test_absent_block_and_infeasible_operation_remain_uncovered(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions)
    missing = MIRPoint(MIRBlockId(fn.id, 99), 0)
    evidence = certify_borrow_operations(fn, operations(fn) | {missing}, frozenset(), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    loop = activation(definitions, retain=False)
    entry = loop.blocks[0]
    loop = replace(loop, blocks=(replace(entry, statements=(
        *entry.statements, MIRAssign(MIRPlace(FLAG), MIRConstant(False)))), *loop.blocks[1:]))
    evidence = certify_borrow_operations(loop, operations(loop), frozenset(), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert any("no feasible presence facts" in g.reason for g in evidence.gaps)


def test_method_return_contract_does_not_expand_eligibility(definitions: MIRDefinitions) -> None:
    fn = replace(forwarding(definitions), kind=MIRBodyKind.METHOD)
    evidence = certify_borrow_operations(fn, operations(fn), frozenset(), definitions)
    assert evidence.verdict is MIRStorageVerdict.NOT_COVERED
    assert MIRPoint(ENTRY, 3) not in evidence.origins


def test_malformed_demands_and_foreign_analysis_raise(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions)
    foreign = MIRPoint(MIRBlockId(MIRBodyId("other", "other"), 0), 0)
    with pytest.raises(MIRValidationError, match="different MIR body"):
        certify_borrow_operations(fn, frozenset({foreign}), frozenset(), definitions)
    with pytest.raises(MIRValidationError, match="not storage"):
        certify_borrow_operations(fn, operations(fn), frozenset({FIRST}), definitions)
    prepared = _prepare_function(fn)
    live = _liveness(prepared)
    deps = _dependencies(prepared, live)
    with pytest.raises(MIRValidationError, match="different MIR function"):
        _borrow_evidence(prepared, live, replace(deps, function=replace(fn)), operations(fn),
                          frozenset(), definitions)


def test_incomplete_join_is_rejected_before_origin_union(definitions: MIRDefinitions) -> None:
    fn = forwarding(definitions)
    # The second arm reaches the join without assigning ALIAS. A union of the
    # first arm's origins must not turn that missing path into positive evidence.
    left, right, join = (MIRBlockId(BODY, i) for i in (1, 2, 3))
    flag = MIRSlotId(BODY, 5)
    fn = replace(fn, slots=(*fn.slots, MIRSlot(flag, BOOL, MIRSlotKind.PARAMETER)), blocks=(
        MIRBlock(ENTRY, (), MIRBranch(flag, left, right), ROOT),
        MIRBlock(left, (MIRAssign(MIRPlace(ALIAS), MIRAlias(FIRST)),), MIRGoto(join), ROOT),
        MIRBlock(right, (), MIRGoto(join), ROOT),
        MIRBlock(join, (), MIRReturn(ALIAS), ROOT)))
    with pytest.raises(MIRValidationError, match="return before definite assignment"):
        certify_borrow_operations(fn, frozenset({MIRPoint(join, 0)}), frozenset(), definitions)
