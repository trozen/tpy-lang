# An augmented assignment through the OWNED element of a mixed owned+borrow
# tuple param whose type defines `__iadd__`: rejected at the lowering exactly
# as the fully owned twin is (BUGS.md#consume-own-element-of-mixed-tuple).
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, other: "Box") -> "Box":
        self.n += other.n
        return self


def f(p: tuple[Own[Box], Box]) -> int32:
    p[0] += p[1]  # tpyc: error(/stmt\.aug_assign/)
    return p[0].n


def main() -> None:
    b = Box(2)
    print(f((Box(1), b)))


main()
