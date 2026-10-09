"""Live holder leaves retain expired storage; reseats and scalar returns do not."""

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import BOOL
from .dependencies import MIRReferent, _dependencies
from .liveness import _liveness
from .nodes import (
    MIRAssign, MIRBlock, MIRBlockId, MIRBranch, MIRConstant, MIREdge, MIRFunction,
    MIRGoto, MIRNotCovered, MIRPayloadWrite, MIRPayloadWriteMode, MIRPlace, MIRPoint,
    MIRRecordWrite, MIRRecordWriteMode, MIRRegion, MIRRegionId, MIRReturn,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRStorageInit,
)
from .scope_lifetime import (
    MIRScopeEnds, _scope_conflicts, _scope_ends, analyze_scope_ends,
    dump_scope_inspection, inspect_scope_lifetimes,
)
from .test_payload_inspection import ALIAS, EXTRACT, READ, RESULT, alias_function
from .test_payload_lifetime import CURRENT, FLAG
from .test_retention import AGAIN, END, LOOP, SITE, loop_function
from .test_scope_lifetime import Artifacts, artifacts
from .validate import MIRPresenceError, MIRValidationError, _prepare_function, validate_function


def scoped_loop(shape: str, *, reseat_first: bool = False) -> tuple[MIRFunction, MIRPlace]:
    fn, _point, observed = loop_function(shape)
    root, iteration = MIRRegionId(fn.id, 0), MIRRegionId(fn.id, 1)
    slots = tuple(replace(s, residence=None if s.kind is MIRSlotKind.PARAMETER else
                          iteration if s.id == SITE else root,
                          storage_duration=iteration if s.id == SITE else s.storage_duration) for s in fn.slots)
    body = fn.blocks[1]
    initialize, borrow, current, read, capture = body.statements
    initialize = replace(initialize, storage_write=MIRRecordWrite(MIRRecordWriteMode.INITIALIZE_REGION))
    statements = (initialize, borrow, current, capture, read) if reseat_first else (
        initialize, borrow, current, read, capture)
    return replace(fn, slots=slots, blocks=tuple(
        replace(b, region=iteration, statements=statements) if b.id == LOOP else replace(b, region=root)
        for b in fn.blocks), regions=(MIRRegion(root, None, fn.entry), MIRRegion(iteration, root, LOOP))), observed


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("reseat_first", [False, True])
def test_loop_carried_retention_uses_successor_liveness(shape: str, reseat_first: bool) -> None:
    fn, observed = scoped_loop(shape, reseat_first=reseat_first)
    result = inspect_scope_lifetimes(fn)
    assert not isinstance(result.conflicts, MIRNotCovered)
    if reseat_first:
        assert not result.conflicts
    else:
        conflict, = result.conflicts
        assert conflict.edge == MIREdge(LOOP, 0)
        assert conflict.ended == MIRPlace(SITE) and conflict.holder == observed
        assert conflict.retained.place == MIRPlace(SITE)
    # The other successor returns a scalar already read before the end.
    assert not any(c.edge == MIREdge(LOOP, 1) for c in result.conflicts)
    assert dump_scope_inspection(result) == dump_scope_inspection(inspect_scope_lifetimes(fn))


def test_source_branch_escape_and_safe_mutation_controls(artifacts: Artifacts) -> None:
    bodies = artifacts[1]
    fn = bodies["retained"]
    assert isinstance(fn, MIRFunction)
    result = inspect_scope_lifetimes(fn)
    conflict, = result.conflicts
    assert next(s for s in fn.slots if s.id == conflict.holder.root).name == "saved"
    assert isinstance(next(s for s in fn.slots if s.id == conflict.ended.root).storage_duration, MIRRegionId)
    for name in ("branch", "loop", "wrapper", "union", "optional_record", "nested", "Observer", "own"):
        safe = inspect_scope_lifetimes(bodies[name])
        assert safe.conflicts == (), (name, safe)


def test_reseat_before_end_removes_the_expiring_dependency() -> None:
    fn, _ = scoped_loop("record")
    entry, loop, again, end = fn.blocks
    # Capture the original body-owned object again before leaving the iteration.
    restore_borrow = entry.statements[1]
    restore_capture = entry.statements[3]
    fn = replace(fn, blocks=(entry, replace(loop, statements=(*loop.statements, restore_borrow, restore_capture)), again, end))
    assert inspect_scope_lifetimes(fn).conflicts == ()


def stale_scalar_alias() -> MIRFunction:
    fn = alias_function(EXTRACT, READ)
    original = fn.blocks[0]
    child, after = MIRBlockId(fn.id, 1), MIRBlockId(fn.id, 2)
    root, inner = MIRRegionId(fn.id, 0), MIRRegionId(fn.id, 1)
    init = replace(original.statements[0], storage_write=MIRPayloadWrite(MIRPayloadWriteMode.INITIALIZE_REGION))
    slots = tuple(replace(s, residence=None if s.kind is MIRSlotKind.PARAMETER else inner if s.id == CURRENT else root,
                          storage_duration=inner if s.id == CURRENT else s.storage_duration) for s in fn.slots)
    return replace(fn, slots=slots, blocks=(
        MIRBlock(fn.entry, (), MIRGoto(child), root),
        MIRBlock(child, (init, EXTRACT), MIRGoto(after), inner),
        MIRBlock(after, (READ,), MIRReturn(RESULT), root)),
        regions=(MIRRegion(root, None, fn.entry), MIRRegion(inner, root, child)))


def test_scope_end_invalidates_scalar_alias_without_erasing_its_referent() -> None:
    fn = stale_scalar_alias()
    with pytest.raises(MIRPresenceError, match="storage end"):
        validate_function(fn)
    with pytest.raises(MIRPresenceError):
        analyze_scope_ends(fn)
    result = inspect_scope_lifetimes(fn)
    conflict, = result.conflicts
    assert conflict.holder == MIRPlace(ALIAS)
    assert conflict.ended == EXTRACT.value.source
    assert result.freshness
    assert "freshness" in dump_scope_inspection(result)


@pytest.mark.parametrize("physical_default", [False, True])
def test_reentering_and_reconstructing_does_not_erase_scalar_alias_failure(physical_default: bool) -> None:
    fn = stale_scalar_alias()
    if physical_default:
        child = fn.blocks[1]
        init, extract = child.statements
        fn = replace(fn, blocks=(fn.blocks[0], replace(child, statements=(
            MIRStorageInit(init.target, 0, MIRConstant(None)),
            replace(init, storage_write=MIRPayloadWrite(MIRPayloadWriteMode.ASSIGN)), extract)), fn.blocks[2]))
    exit_id = MIRBlockId(fn.id, 3)
    after = fn.blocks[2]
    fn = replace(fn, blocks=(*fn.blocks[:2], replace(after, terminator=MIRBranch(
        FLAG, fn.blocks[1].id, exit_id)), MIRBlock(exit_id, (), MIRReturn(RESULT), after.region)))
    # The same wrapper and extraction sites run again on the backedge. Every
    # read after their region exit remains stale, despite later reconstruction.
    with pytest.raises(MIRPresenceError, match="storage end"):
        validate_function(fn)
    result = inspect_scope_lifetimes(fn)
    conflict, = result.conflicts
    assert conflict.edge == MIREdge(fn.blocks[1].id)
    assert conflict.ended == EXTRACT.value.source
    assert result.freshness[0].point.block == after.id


@pytest.mark.parametrize("read_inside", [False, True])
def test_skipped_union_construction_keeps_old_payload_alias(read_inside: bool) -> None:
    fn = stale_scalar_alias()
    entry, initialize, after = fn.blocks
    guard, skipped, end = (MIRBlockId(fn.id, i) for i in (3, 4, 5))
    skip = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)
    fn = replace(fn, slots=(*fn.slots, skip), blocks=(
        replace(entry, terminator=MIRGoto(guard)),
        replace(initialize, statements=(*initialize.statements, READ) if read_inside else initialize.statements),
        replace(after, statements=() if read_inside else (READ,),
                terminator=MIRBranch(FLAG, entry.id, end)),
        MIRBlock(guard, (), MIRBranch(skip.id, skipped, initialize.id), initialize.region),
        MIRBlock(skipped, (), MIRGoto(entry.id), entry.region),
        MIRBlock(end, (), MIRReturn(fn.slots[3].id), entry.region)),
        regions=(fn.regions[0], replace(fn.regions[1], entry=guard)))
    prepared = _prepare_function(fn)
    dependencies = _dependencies(prepared, _liveness(prepared))
    assert dependencies.referents[MIRPoint(skipped, 0)][MIRPlace(ALIAS)] == frozenset({
        MIRReferent(EXTRACT.value.source)})
    assert MIRPlace(CURRENT) not in dict(prepared.presence.points[MIRPoint(skipped, 0)])
    result = inspect_scope_lifetimes(fn)
    assert MIREdge(guard, 0) not in result.ends.ends
    assert bool(result.freshness) is not read_inside
    assert bool(result.conflicts) is not read_inside
    if read_inside:
        validate_function(fn)
    else:
        with pytest.raises(MIRPresenceError, match="storage end"):
            validate_function(fn)


def test_inspection_still_rejects_missing_selection() -> None:
    fn = stale_scalar_alias()
    child = fn.blocks[1]
    invalid = replace(child.statements[0], value=replace(child.statements[0].value, alternative=0, source=None))
    fn = replace(fn, blocks=(fn.blocks[0], replace(child, statements=(invalid, EXTRACT)), fn.blocks[2]))
    with pytest.raises(MIRPresenceError, match="alternative proof"):
        inspect_scope_lifetimes(fn)


def test_inputs_must_belong_to_the_same_exact_function() -> None:
    fn, _ = scoped_loop("record")
    prepared = _prepare_function(fn)
    live = _liveness(prepared)
    deps = _dependencies(prepared, live)
    ends = _scope_ends(prepared)
    for which in ("live", "dependencies", "ends"):
        foreign = replace(fn)
        with pytest.raises(MIRValidationError, match="different MIR function"):
            _scope_conflicts(prepared, replace(live, function=foreign) if which == "live" else live,
                             replace(deps, function=foreign) if which == "dependencies" else deps,
                             replace(ends, function=foreign) if which == "ends" else ends)
    missing = MIRNotCovered(fn.id, "dependencies", "unknown storage")
    result = _scope_conflicts(prepared, live, missing, ends)
    assert isinstance(result, MIRNotCovered) and "unknown storage" in result.reason


def test_dynamic_trace_finds_previous_activation_even_at_the_same_static_site() -> None:
    # Each trace is two iterations. Python tuples below are fresh object tokens;
    # the production dependency lattice intentionally stores only static sites.
    for reseat_first in (False, True):
        fn, observed = scoped_loop("record", reseat_first=reseat_first)
        saved = ("body", 0)
        ended: set[tuple[str, int]] = set()
        expired_reads = []
        for activation in (1, 2):
            current = ("iteration", activation)
            if reseat_first:
                saved = current
            if saved in ended:
                expired_reads.append(activation)
            saved = current
            ended.add(current)
        assert expired_reads == ([] if reseat_first else [2])
        conflicts = inspect_scope_lifetimes(fn).conflicts
        assert bool(conflicts) == bool(expired_reads)
        assert all(c.holder == observed and c.edge == MIREdge(LOOP, 0) for c in conflicts)


@pytest.mark.parametrize("shape", ["record", "singleton", "mixed", "optional", "union"])
@pytest.mark.parametrize("reseat_first", [False, True])
def test_skipped_construction_preserves_old_holder_dependencies(shape: str, reseat_first: bool) -> None:
    fn, observed = scoped_loop(shape, reseat_first=reseat_first)
    entry, loop, again, end = fn.blocks
    guard = MIRBlockId(fn.id, 4)
    skip = MIRSlot(MIRSlotId(fn.id, len(fn.slots)), BOOL, MIRSlotKind.PARAMETER, passing=ParamPassing.VALUE)
    # Read the saved alias before this activation decides whether to construct.
    reads = () if reseat_first else (loop.statements[3],)
    statements = loop.statements if reseat_first else (*loop.statements[:3], loop.statements[4])
    fn = replace(fn, slots=(*fn.slots, skip), blocks=(
        replace(entry, terminator=MIRGoto(guard)), replace(loop, statements=statements),
        replace(again, terminator=MIRGoto(guard)), end,
        MIRBlock(guard, reads, MIRBranch(skip.id, AGAIN, LOOP), loop.region)),
        regions=(fn.regions[0], replace(fn.regions[1], entry=guard)))
    result = inspect_scope_lifetimes(fn)
    assert MIREdge(guard, 0) not in result.ends.ends
    assert bool(result.conflicts) is not reseat_first
    assert all(c.holder == observed and c.edge == MIREdge(LOOP, 0) for c in result.conflicts)
    saved = ("body", 0)
    expired = set()
    stale_reads = []
    for activation, skipped in enumerate((False, True, False)):
        if not reseat_first and saved in expired:
            stale_reads.append(activation)
        if skipped:
            continue
        saved = ("iteration", activation)
        expired.add(saved)
    assert stale_reads == ([] if reseat_first else [1, 2])
