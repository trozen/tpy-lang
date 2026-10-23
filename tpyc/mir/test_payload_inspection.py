"""Inspection explains stale payload aliases without accepting them as valid MIR."""

from dataclasses import replace
from itertools import product
from collections.abc import Callable

import pytest

from ..typesys import INT32
from .nodes import (
    MIRAssign, MIRBlock, MIRBranch, MIRFunction, MIRGoto, MIRIsAlternative,
    MIRNotCovered, MIRPlace, MIRPoint, MIRRead, MIRReturn, MIRSlot, MIRSlotId,
    MIRSlotKind, MIRUnionExtract,
)
from .payload_lifetime import (
    MIRPayloadConflict, MIRPayloadEnds, _payload_conflicts, _payload_ends,
    analyze_payload_ends, dump_payload_inspection, inspect_payload_lifetimes,
)
from .presence import MIRPresenceIssueKind, _analyze_presence
from .storage import analyze_storage
from .retention import analyze_retention
from .call_effects import analyze_call_effects
from .test_payload_lifetime import (
    ASSIGN, BODY, CURRENT, ENTRY, FLAG, GUARD, INIT, JOIN, NO, PARAM, VALUE, YES,
    payload, wrapper_function, write,
)
from .validate import MIRPresenceError
from .value import MIRValidationError
from .testutil import alias_shape, leaf_shape, prepared
from .prepare import validate_function, validated


ALIAS, RESULT = (MIRSlotId(BODY, i) for i in (6, 7))
EXTRACT = MIRAssign(MIRPlace(ALIAS), MIRUnionExtract(payload(False)))
READ = MIRAssign(MIRPlace(RESULT), MIRRead(MIRPlace(ALIAS)))


def alias_function(*statements: MIRAssign) -> MIRFunction:
    fn = wrapper_function(False, MIRBlock(ENTRY, (write(False, 2, fact=INIT), *statements), MIRReturn(RESULT)))
    return replace(fn, slots=(*fn.slots,
        MIRSlot(ALIAS, alias_shape(INT32, readonly=True), MIRSlotKind.LOCAL, alias_source=payload(False)),
        MIRSlot(RESULT, leaf_shape(INT32), MIRSlotKind.LOCAL)))


@pytest.mark.parametrize("tag", [0, 1, 2, PARAM, CURRENT])
def test_inspection_separates_lifetime_ends_from_strict_freshness(tag: int | MIRSlotId) -> None:
    fn = alias_function(EXTRACT, write(False, tag), READ)
    with pytest.raises(MIRPresenceError, match="alias used after holder replacement"):
        validate_function(fn)
    result = inspect_payload_lifetimes(prepared(fn))
    expected = () if tag in (2, CURRENT) else (MIRPayloadConflict(MIRPoint(ENTRY, 2), payload(False), MIRPlace(ALIAS)),)
    assert result.conflicts == expected
    assert len(result.freshness) == 1
    assert result.freshness[0].point == MIRPoint(ENTRY, 3)
    assert result.freshness[0].kind is MIRPresenceIssueKind.FRESHNESS
    out = dump_payload_inspection(result)
    assert "freshness bb0 before 3" in out
    assert "no lifetime-safety verdict" in out


@pytest.mark.parametrize("stmts", [
    (EXTRACT, READ, write(False, 0)),
    (EXTRACT, write(False, 0), write(False, 2), EXTRACT, READ),
    (EXTRACT, write(False, 2), EXTRACT, READ),
])
def test_snapshot_or_reextraction_does_not_retain_an_old_payload(stmts: tuple[MIRAssign, ...]) -> None:
    fn = alias_function(*stmts)
    validate_function(fn)
    result = inspect_payload_lifetimes(prepared(fn))
    assert result.conflicts == () and result.freshness == ()


def test_a_to_b_to_a_does_not_revive_alias_freshness() -> None:
    fn = alias_function(EXTRACT, write(False, 1), write(False, 2), READ)
    result = inspect_payload_lifetimes(prepared(fn))
    assert result.conflicts == (MIRPayloadConflict(MIRPoint(ENTRY, 2), payload(False), MIRPlace(ALIAS)),)
    assert result.freshness
    with pytest.raises(MIRPresenceError):
        validate_function(fn)


def test_selection_error_after_freshness_failure_still_blocks_inspection() -> None:
    direct = MIRAssign(MIRPlace(RESULT), MIRRead(payload(False)))
    fn = alias_function(EXTRACT, write(False, 0), READ, direct)
    assert [i.kind for i in _analyze_presence(fn, None).issues] == [MIRPresenceIssueKind.FRESHNESS,
                                                                 MIRPresenceIssueKind.SELECTION]
    with pytest.raises(MIRPresenceError, match="alias used after holder replacement"):
        validate_function(fn)
    with pytest.raises(MIRPresenceError, match="current presence proof"):
        inspect_payload_lifetimes(prepared(fn))


@pytest.mark.parametrize("analysis", [
    validate_function,
    lambda fn: analyze_storage(prepared(fn)),
    lambda fn: analyze_payload_ends(prepared(fn)),
    lambda fn: analyze_retention(prepared(fn)),
    lambda fn: analyze_call_effects(prepared(fn)),
])
def test_existing_public_apis_still_reject_stale_aliases(analysis: Callable[[MIRFunction], object]) -> None:
    fn = alias_function(EXTRACT, write(False, 0), READ)
    with pytest.raises(MIRPresenceError):
        analysis(fn)


def test_inspection_does_not_skip_structure_or_definite_assignment() -> None:
    fn = alias_function(READ)
    with pytest.raises(MIRValidationError, match="read before definite assignment"):
        inspect_payload_lifetimes(prepared(fn))
    with pytest.raises(MIRValidationError, match="missing entry block"):
        inspect_payload_lifetimes(prepared(replace(fn, blocks=())))


@pytest.mark.parametrize("missing", ["write", "duration"])
def test_uncovered_inputs_do_not_erase_freshness_issues(missing: str) -> None:
    fn = alias_function(EXTRACT, write(False, 0, fact=None if missing == "write" else ASSIGN), READ)
    if missing == "duration":
        fn = replace(fn, slots=tuple(replace(s, storage_duration=None) if s.id == CURRENT else s for s in fn.slots))
        # The first dependency run refuses the body: its preparation is that
        # refusal, and the freshness issue still refuses validation.
        assert isinstance(prepared(fn), MIRNotCovered)
        with pytest.raises(MIRPresenceError, match="alias used after holder replacement"):
            validated(fn)
        return
    result = inspect_payload_lifetimes(prepared(fn))
    assert isinstance(result.conflicts, MIRNotCovered)
    assert result.freshness
    assert "not covered:" in dump_payload_inspection(result)
    assert "no conflicts" not in dump_payload_inspection(result)


@pytest.mark.parametrize("wrong", ["presence", "liveness", "dependencies", "ends"])
def test_composition_rejects_equal_but_distinct_function_instances(wrong: str) -> None:
    fn = alias_function(EXTRACT, write(False, 0), READ)
    ready = prepared(fn)
    ends = _payload_ends(ready)
    other = replace(fn)
    if wrong == "presence":
        ready = replace(ready, presence=replace(ready.presence, function=other))
    elif wrong == "liveness":
        ready = replace(ready, liveness=replace(ready.liveness, function=other))
    elif wrong == "dependencies":
        ready = replace(ready, dependencies=replace(ready.dependencies, function=other))
    else:
        ends = replace(ends, function=other)
    with pytest.raises(MIRValidationError, match="different MIR function"):
        _payload_conflicts(ready, ends)


def test_branch_and_loop_conflicts_are_possible_static_place_reports() -> None:
    fn = alias_function(EXTRACT, READ)
    fn = replace(fn, blocks=(
        MIRBlock(ENTRY, (write(False, PARAM, fact=INIT),
                        MIRAssign(MIRPlace(GUARD), MIRIsAlternative(MIRPlace(CURRENT), (2,)))), MIRBranch(GUARD, YES, NO)),
        MIRBlock(YES, (EXTRACT,), MIRGoto(JOIN)),
        MIRBlock(JOIN, (write(False, 1), write(False, 2), READ), MIRBranch(FLAG, JOIN, NO)),
        MIRBlock(NO, (), MIRReturn(VALUE))))
    result = inspect_payload_lifetimes(prepared(fn))
    assert result.conflicts == (MIRPayloadConflict(MIRPoint(JOIN, 0), payload(False), MIRPlace(ALIAS)),)
    assert result.freshness


@pytest.mark.parametrize("tags", list(product(range(3), repeat=3)))
def test_concrete_payload_generations_are_covered_by_static_events(tags: tuple[int, ...]) -> None:
    fn = alias_function(EXTRACT, *(write(False, tag) for tag in tags), READ)
    result = inspect_payload_lifetimes(prepared(fn))
    assert isinstance(result.ends, MIRPayloadEnds)
    # The trace owns a new generation on each tag switch. An old alias never
    # revives, even when later generations occupy the same static place.
    tag, generation, retained_generation = 2, 0, 0
    physical_ends: set[tuple[MIRPoint, MIRPlace]] = set()
    real_conflicts: set[MIRPayloadConflict] = set()
    for index, next_tag in enumerate(tags, start=2):
        if next_tag != tag:
            if tag != 0:
                point, place = MIRPoint(ENTRY, index), payload(False, tag)
                physical_ends.add((point, place))
                if retained_generation == generation:
                    real_conflicts.add(MIRPayloadConflict(point, place, MIRPlace(ALIAS)))
            generation += 1
        tag = next_tag
    assert {(point, place) for point, places in result.ends.ends.items() for place in places} == physical_ends
    assert real_conflicts <= set(result.conflicts)
    if all(tag == 2 for tag in tags):
        assert result.conflicts == ()
