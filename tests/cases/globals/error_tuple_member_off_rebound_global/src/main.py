# A TUPLE is a value type, so only its members are borrows: a non-value member
# rooted in a rebound module global is rejected per element. The whole-return
# arm is pinned by `error_view_returned_off_rebound_global`.
# XS is reference-typed, so `Cannot reassign global variable 'XS' of non-value
# type` is the sibling reject that fires instead when sema reaches `reset`
# first -- which is why this leg offers no `Own[...]` escape, only the
# parity-preserving remedy of dropping the rebind.
from tpy import int32

XS = [1, 2, 3]


def pair() -> tuple[list[int32], int32]:
    return (XS, 1)  # tpyc: error(/Cannot return a borrow of module variable 'XS'.*'reset' rebinds.*Stop rebinding 'XS'/)


def reset() -> None:
    global XS
    XS = [9]


def main() -> None:
    got, n = pair()
    reset()
    print(len(got), n)


main()
