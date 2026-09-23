# Per-element ownership check at an Own[tuple[P, P]] call site (record
# elements, no Optional wrapper). Same diagnostic family as the
# Optional-element case, exercising the parallel path through
# own_tuple_target's per-element Own synthesis for non-value elements.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: Own[tuple[P, P]]) -> int32:
    return int32(0)


def main() -> None:
    a = P(1)
    b = P(2)
    # Both a and b are reused below, so neither is at last use here.
    print(take((a, b)))  # tpyc: error(/tuple element 0.*explicit copy/)
    print(take((a, b)))  # last-use, no error


main()
