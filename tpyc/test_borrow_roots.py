"""BorrowTracker root resolution over shapes no snapshot case can reach.

A capture re-seated through two aliases of ONE storage makes the borrow graph
a diamond. Resolving it needs the has-sources test to be unfiltered by the
visited set, or the second path reports the intermediate alias as a root; the
source-level face of that is masked today by the dotted-key gap filed as
BUGS.md#field-borrow-root-skips-alias-hop, which rejects the same programs
first, so the graph is the only level this is observable at.
"""
from .sema.context import BorrowKind, BorrowTracker


def _diamond() -> BorrowTracker:
    # q re-seated over b1 and b2, which both alias h.
    bt = BorrowTracker()
    bt.add_borrow("h", "b1", BorrowKind.ALIAS)
    bt.add_borrow("h", "b2", BorrowKind.ALIAS)
    bt.add_borrow("b1", "q", BorrowKind.OPAQUE)
    bt.add_borrow("b2", "q", BorrowKind.OPAQUE)
    return bt


def test_converging_paths_both_resolve_to_the_shared_root() -> None:
    assert _diamond().all_storage_through_borrows("q") == ["h"]


def test_distinct_roots_are_all_reported_nearest_first() -> None:
    bt = BorrowTracker()
    bt.add_borrow("h", "q", BorrowKind.OPAQUE)
    bt.add_borrow("g", "q", BorrowKind.OPAQUE)
    assert bt.all_storage_through_borrows("q") == ["h", "g"]


def test_unborrowed_name_has_no_root_and_falls_back_to_itself() -> None:
    bt = _diamond()
    assert bt.all_storage_through_borrows("z") == []
    assert bt.storage_roots_or_self("z") == ["z"]


def test_cycle_terminates() -> None:
    bt = BorrowTracker()
    bt.add_borrow("a", "b", BorrowKind.ALIAS)
    bt.add_borrow("b", "a", BorrowKind.ALIAS)
    # Every reachable node has a source, so none is storage; the caller's
    # fallback names the binding itself rather than an arbitrary cycle member.
    assert bt.all_storage_through_borrows("a") == []
    assert bt.storage_roots_or_self("a") == ["a"]
