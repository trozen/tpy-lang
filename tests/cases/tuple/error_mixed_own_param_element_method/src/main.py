# A mutating method called on the OWNED element of a mixed owned+borrow
# tuple param, from a closure: rejected at the lowering like a method call on
# an owned element of the fully owned twin
# (BUGS.md#consume-own-element-of-mixed-tuple).
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def inc(self) -> None:
        self.n += 1


def sink(b: Own[Box]) -> Own[Box]:
    return b


def mut(b: Box) -> None:
    b.n = 5


def f(p: tuple[Own[Box], Box]) -> int32:
    def g() -> None:
        p[0].inc()  # tpyc: error(/method\.recv\.subscript/)
    g()
    return p[1].n


def main() -> None:
    b = Box(2)
    print(f((Box(1), b)))


main()
