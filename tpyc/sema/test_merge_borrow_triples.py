"""A branch join keeps every binding site a loan's source name resolved to.

Two arms can hold the same (storage, borrower, kind) loan taken from two
bindings of one name (`for r in ros: t = r.bp` / `for r in others: t =
r.bp`). The loans are equal -- `source_sites` is not part of a loan's
identity -- so a plain set union would keep one arm's site and a write after
the join would reach only that arm's source.
"""

from ..diagnostics import Scope
from ..parse.nodes import TpyPassStmt
from .context import BorrowKind, FunctionTrackingState, LoanInfo
from .flow_facts import merge_borrow_triples
from .storage_walk import StorageKey

_KEY = StorageKey.of_name("r")


def _arms() -> tuple[object, object, frozenset, frozenset]:
    """One loan of `t` on `r`, filed in each arm under a different binding
    of `r`."""
    site_a, site_b = TpyPassStmt(), TpyPassStmt()
    state = FunctionTrackingState()
    state.current_scope = Scope(None)
    state.current_scope.bindings["r"] = None
    state.current_scope.binders["r"] = site_a
    state.borrow_tracker.add_borrow(_KEY, "t", BorrowKind.ALIAS)
    then_arm = state.borrow_tracker.freeze()
    state.borrow_tracker.reset()
    state.current_scope.binders["r"] = site_b
    state.borrow_tracker.add_borrow(_KEY, "t", BorrowKind.ALIAS)
    else_arm = state.borrow_tracker.freeze()
    return site_a, site_b, then_arm, else_arm


def _merged_loan(then_arm: frozenset, else_arm: frozenset, then_term: bool,
                 else_term: bool) -> LoanInfo:
    merged = merge_borrow_triples(then_arm, else_arm, then_term, else_term)
    assert len(merged) == 1
    storage, borrower, loan = next(iter(merged))
    assert (storage, borrower, loan.kind) == (_KEY, "t", BorrowKind.ALIAS)
    return loan


def _same(sites: tuple[object, ...], expected: tuple[object, ...]) -> bool:
    return (len(sites) == len(expected)
            and all(any(s is e for s in sites) for e in expected))


def test_both_live_arms_keep_both_sites() -> None:
    site_a, site_b, then_arm, else_arm = _arms()
    loan = _merged_loan(then_arm, else_arm, False, False)
    assert _same(loan.source_sites, (site_a, site_b))


def test_both_terminated_arms_keep_both_sites() -> None:
    site_a, site_b, then_arm, else_arm = _arms()
    loan = _merged_loan(then_arm, else_arm, True, True)
    assert _same(loan.source_sites, (site_a, site_b))


def test_a_terminated_arm_contributes_no_site() -> None:
    site_a, site_b, then_arm, else_arm = _arms()
    assert _same(_merged_loan(then_arm, else_arm, True, False).source_sites,
                 (site_b,))
    assert _same(_merged_loan(then_arm, else_arm, False, True).source_sites,
                 (site_a,))
