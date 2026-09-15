# `p in xs` with a user-RECORD needle is refused at lowering, on purpose:
# `std::ranges::contains` compares with `operator==` alone, while CPython's
# containment tests `x is e or x == e`, so a record whose `__eq__` is not
# reflexive (a NaN field) would report absent although the container holds
# that very object -- a silent wrong answer where the reject is loud
# (BUGS.md#membership-skips-identity-shortcut). Closing that entry re-admits
# this form; until then the located reject is the contract.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    # Sema's own requirement for `in` is that the element be Equatable; the
    # record passes it, so the reject below is the lowering's, not sema's.
    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x


def has(pts: list[Point], p: Point) -> bool:
    return p in pts  # tpyc: error(/binop.shape.in.record/)


def main() -> None:
    print(has([Point(1)], Point(1)))


main()
