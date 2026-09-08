# Returning a StrView through an intermediate borrowing-view call
# whose source is a local owned String must be flagged as dangling.
# Exercises return_borrows_from propagation across a free function. The
# same helper over a caller-owned source is pinned by
# tests/cases/returns/return_mixed_safe_sources_if.

from tpy import StrView, String


def pick_view(s: StrView) -> StrView:
    return s


def returns_view_through_call() -> StrView:
    local = String("temporary")
    return pick_view(StrView(local))  # tpyc: error(/Cannot return.*local or temporary/)
