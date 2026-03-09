"""
FlowFacts -- immutable snapshot of flow-sensitive analysis state.

Captures the flow-sensitive properties that must be saved, restored,
and merged at branch/loop boundaries: definite assignment, termination,
rvalue tracking, parameter provenance, pointer non-null status, type
narrowing, and consumed-variable tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from .context import BorrowKind
from .value_range import ValueRange

if TYPE_CHECKING:
    from ..typesys import TpyType


class _MergePolicy(Enum):
    INTERSECT = auto()
    UNION = auto()


def _merge_sets(
    then_set: frozenset[str],
    else_set: frozenset[str],
    then_term: bool,
    else_term: bool,
    policy: _MergePolicy,
) -> frozenset[str]:
    """Merge two frozensets across branch endpoints.

    When a branch terminates (return/break/raise), it cannot reach the
    merge point, so only the other branch's set matters.  When both are
    live, ``policy`` decides: INTERSECT (conservative -- must hold on
    both paths) or UNION (conservative -- may hold on either path).
    """
    if then_term and else_term:
        return then_set | else_set
    if then_term:
        return else_set
    if else_term:
        return then_set
    if policy is _MergePolicy.INTERSECT:
        return then_set & else_set
    return then_set | else_set


def _merge_narrowed(
    then_narrowed: frozenset[tuple[str, TpyType]],
    else_narrowed: frozenset[tuple[str, TpyType]],
    then_term: bool,
    else_term: bool,
) -> frozenset[tuple[str, TpyType]]:
    """Merge narrowed-type mappings, represented as frozensets of (name, type) pairs.

    When both branches are live, only narrowings that agree on both sides
    survive (key present in both with equal type).
    """
    if then_term and else_term:
        then_dict = dict(then_narrowed)
        then_dict.update(else_narrowed)
        return frozenset(then_dict.items())
    if then_term:
        return else_narrowed
    if else_term:
        return then_narrowed
    then_dict = dict(then_narrowed)
    else_dict = dict(else_narrowed)
    merged = {k: v for k, v in then_dict.items()
              if k in else_dict and else_dict[k] == v}
    return frozenset(merged.items())


def _merge_value_ranges(
    then_ranges: frozenset[tuple[str, ValueRange]],
    else_ranges: frozenset[tuple[str, ValueRange]],
    then_term: bool,
    else_term: bool,
) -> frozenset[tuple[str, ValueRange]]:
    """Merge value range mappings across branch endpoints.

    When both branches are live, per-variable ranges are merged (widened)
    using ValueRange.merge. Variables tracked on only one branch are dropped
    (conservative -- we can only use a fact if it holds on all paths).
    """
    if then_term and else_term:
        then_dict = dict(then_ranges)
        then_dict.update(else_ranges)
        return frozenset(then_dict.items())
    if then_term:
        return else_ranges
    if else_term:
        return then_ranges
    then_dict = dict(then_ranges)
    else_dict = dict(else_ranges)
    merged = {}
    for k, v in then_dict.items():
        if k in else_dict:
            merged[k] = ValueRange.merge(v, else_dict[k])
    return frozenset(merged.items())


def _merge_borrow_triples(
    then_borrows: frozenset[tuple[str, str, BorrowKind]],
    else_borrows: frozenset[tuple[str, str, BorrowKind]],
    then_term: bool,
    else_term: bool,
) -> frozenset[tuple[str, str, BorrowKind]]:
    """Merge borrow (storage, borrower, kind) triples across branch endpoints.

    Uses UNION policy: a borrow exists after the merge if it exists in
    either live branch (conservative -- may hold on either path).
    When the same (storage, borrower) has different kinds on the two branches,
    the more dangerous kind wins (element > alias, ptr > alias, iter > alias).
    """
    if then_term and else_term:
        combined = then_borrows | else_borrows
    elif then_term:
        combined = else_borrows
    elif else_term:
        combined = then_borrows
    else:
        combined = then_borrows | else_borrows
    # Deduplicate (storage, borrower) pairs, keeping the more dangerous kind
    best: dict[tuple[str, str], BorrowKind] = {}
    for storage, borrower, kind in combined:
        key = (storage, borrower)
        prev = best.get(key)
        if prev is None or _BORROW_KIND_RANK[kind] > _BORROW_KIND_RANK[prev]:
            best[key] = kind
    return frozenset((s, b, k) for (s, b), k in best.items())


_BORROW_KIND_RANK: dict[BorrowKind, int] = {
    BorrowKind.ALIAS: 0,
    BorrowKind.FIELD: 1,
    BorrowKind.ITER: 2,
    BorrowKind.ELEMENT: 3,
    BorrowKind.PTR: 3,
}


@dataclass(frozen=True, slots=True)
class FlowFacts:
    """Immutable snapshot of flow-sensitive analysis state."""

    definitely_assigned: frozenset[str] = frozenset()
    init_terminated: bool = False
    rvalue_vars: frozenset[str] = frozenset()
    param_provenance_vars: frozenset[str] = frozenset()
    non_null_ptr_vars: frozenset[str] = frozenset()
    narrowed_types: frozenset[tuple[str, TpyType]] = frozenset()
    consumed_vars: frozenset[str] = frozenset()
    # Borrow map: (storage_name, borrower_name, BorrowKind) triples.
    # "__for_iter" is used as the borrower for implicit for-loop iterator borrows.
    borrows: frozenset[tuple[str, str, BorrowKind]] = frozenset()
    value_ranges: frozenset[tuple[str, ValueRange]] = frozenset()

    @staticmethod
    def merge(then: FlowFacts, else_: FlowFacts) -> FlowFacts:
        """Merge flow facts from then/else branches."""
        then_term = then.init_terminated
        else_term = else_.init_terminated
        return FlowFacts(
            definitely_assigned=_merge_sets(
                then.definitely_assigned, else_.definitely_assigned,
                then_term, else_term, _MergePolicy.INTERSECT,
            ),
            init_terminated=then_term and else_term,
            rvalue_vars=_merge_sets(
                then.rvalue_vars, else_.rvalue_vars,
                then_term, else_term, _MergePolicy.INTERSECT,
            ),
            param_provenance_vars=_merge_sets(
                then.param_provenance_vars, else_.param_provenance_vars,
                then_term, else_term, _MergePolicy.INTERSECT,
            ),
            non_null_ptr_vars=_merge_sets(
                then.non_null_ptr_vars, else_.non_null_ptr_vars,
                then_term, else_term, _MergePolicy.INTERSECT,
            ),
            narrowed_types=_merge_narrowed(
                then.narrowed_types, else_.narrowed_types,
                then_term, else_term,
            ),
            consumed_vars=_merge_sets(
                then.consumed_vars, else_.consumed_vars,
                then_term, else_term, _MergePolicy.UNION,
            ),
            borrows=_merge_borrow_triples(
                then.borrows, else_.borrows,
                then_term, else_term,
            ),
            value_ranges=_merge_value_ranges(
                then.value_ranges, else_.value_ranges,
                then_term, else_term,
            ),
        )
