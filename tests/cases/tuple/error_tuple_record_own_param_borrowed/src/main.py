# Per-element ownership check at an Own[tuple[P, P]] call site (record
# elements, no Optional wrapper). Same diagnostic family as the
# Optional-element case, exercising the parallel path through
# own_tuple_target's per-element Own synthesis for non-value elements.
from tpy import Int32, Own


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def take(t: Own[tuple[P, P]]) -> Int32:
    return Int32(0)


def main() -> None:
    a = P(1)
    b = P(2)
    # Both a and b are reused below, so neither is at last use here.
    print(take((a, b)))  # tpyc: error(/tuple element 0.*explicit copy/)
    print(take((a, b)))  # last-use, no error


main()
