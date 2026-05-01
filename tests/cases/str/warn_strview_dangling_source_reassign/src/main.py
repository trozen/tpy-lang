# Pinned StrView/BytesView aliases dangle when their owned source is
# reassigned (str/bytes are value-type storage, no slot/pointer indirection).
# Pending views fall back to the owned type via source_mutated; pinned
# annotations can't fall back, so we warn at the mutation site.
from tpy import StrView, BytesView


def strview_pinned_alias_warns() -> None:
    s = "hello" + "world"
    view: StrView = s
    print(view)
    s = "other"  # tpyc: warning(/Mutation of 's'.*reassignment invalidates view 'view'/)
    # `view` is dangling here; don't read it. Print s instead to verify the
    # rebind ran.
    print(s)


def bytesview_pinned_alias_warns() -> None:
    b = b"hello" + b"world"
    bv: BytesView = b
    print(len(bv))
    b = b"other"  # tpyc: warning(/Mutation of 'b'.*reassignment invalidates view 'bv'/)
    print(len(b))


def view_rebind_clears_pinned_alias() -> None:
    """Rebinding the view itself clears the pinned-alias entry on its old source."""
    s1 = "hello" + "world"
    s2 = "foo" + "bar"
    view: StrView = s1
    view = s2  # view now aliases s2; the alias on s1 is cleared
    s1 = "other"  # tpyc: ok -- view no longer borrows s1
    print(view)
    print(s1)


def multiple_pinned_views_per_source() -> None:
    """Source reassignment warns once per pinned-view borrower in sorted order."""
    s = "hello" + "world"
    alpha: StrView = s
    beta: StrView = s
    print(len(alpha))
    print(len(beta))
    s = "other"  # tpyc: warning(/'alpha'/) warning(/'beta'/)
    print(s)


strview_pinned_alias_warns()
bytesview_pinned_alias_warns()
view_rebind_clears_pinned_alias()
multiple_pinned_views_per_source()
