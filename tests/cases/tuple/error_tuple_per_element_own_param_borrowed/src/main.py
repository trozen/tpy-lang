# Per-element ownership check fires for the canonical
# `tuple[Own[T], ...]` param form too (not just `Own[tuple[T, ...]]`).
# The dispatch in check_own_param has two entry paths -- this test
# pins the direct-per-element-form path.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: tuple[Own[P], Own[P]]) -> int32:
    a, b = t
    return a.x + b.x


def main() -> None:
    a = P(1)
    b = P(2)
    # a, b are reused below -- not at last use here.
    print(take((a, b)))  # tpyc: error(/tuple element 0.*explicit copy/)
    print(take((a, b)))  # last-use, no error


main()
