# Per-element ownership check at an Own[tuple[P|None, P|None]] call site:
# function parameter (borrowed, never an Own local) as the source is
# rejected the same way as a non-last-use Own local. This is the most
# common Python shape that hits the diagnostic.
from tpy import Int32, Own


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> Int32:
    return Int32(0)


def helper(p: P, q: P) -> Int32:
    # p and q are borrowed params (not Own[P]) -- can't be moved into
    # the Own[tuple] slots.
    return take((p, q))  # tpyc: error(/tuple element 0.*explicit copy/)


def main() -> None:
    print(helper(P(1), P(2)))


main()
