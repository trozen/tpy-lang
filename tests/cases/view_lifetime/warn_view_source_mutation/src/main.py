# Mutating the backing storage of a live view warns (audit #10 item 4 + the
# borrow-registration of view locals): `a += ...` reallocates a's buffer just
# as a rebind does, invalidating views of a. Two shapes warn: the explicit
# pinned view (`c: StrView = a`), and an INFERRED view from a borrowing METHOD
# (`v = a.strip()`) -- the storage deduction decides that local is a view, so
# the loan on the receiver is registered exactly as a spelled StrView's is.
#
# A SLICE view (`v = a[i:j]`, str and bytearray alike) does NOT warn, and is
# right not to: it registers its source storage with the deduction, which falls
# the local back to an owned copy the moment the source is mutated -- nothing is
# left pointing into a's buffer, so there is nothing to warn about.
#
# Note: each warning shape's last read is BEFORE the mutation, so its borrow is
# already DEAD at the `+=` -- the warning is thus a conservative false positive
# (the borrow-conflict check is not last-use aware; tracked in TODO.md). It is
# the observable signal that the borrow was registered, which is what this
# guards. Reads are kept before the mutation because a genuinely
# live-across-mutation read would dangle (UB) and diverge from CPython, so a
# true-positive deterministic test isn't possible. When last-use precision lands
# those assertions flip to no-warning.
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def inferred_view() -> None:
    a = make()
    v = a.strip()
    print(v)
    a += " appended text that forces the std::string buffer to reallocate"  # tpyc: warning(/while borrowed/)


def pinned_view() -> None:
    a = make()
    c: StrView = a
    print(c)
    a += " appended text that forces the std::string buffer to reallocate"  # tpyc: warning(/while borrowed/)


def slice_view() -> None:
    a = make()
    v = a[3:9]
    print(v)
    a += " appended text that forces the std::string buffer to reallocate"  # tpyc: ok


def bytearray_slice_view() -> None:
    ba = bytearray(b"   padded long bytes that dodge the small buffer here   ")
    v = ba[3:9]
    print(len(v))
    ba.append(33)  # tpyc: ok


def main() -> None:
    inferred_view()
    pinned_view()
    slice_view()
    bytearray_slice_view()


main()
