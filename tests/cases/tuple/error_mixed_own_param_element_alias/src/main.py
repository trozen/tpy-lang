# A name bound to the OWNED element of a mixed owned+borrow tuple param:
# a place inside the param with no alias form yet, rejected at the lowering
# exactly as the fully owned twin `tuple[Own[Box], Own[Box]]` is
# (BUGS.md#consume-own-element-of-mixed-tuple). A generator or async frame
# holds the tuple by value and aliases it: tuple/mixed_own_param_writes.
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
    owned = p[0]  # tpyc: error(/decl\.slot_type/)
    return owned.n + p[1].n


def main() -> None:
    b = Box(2)
    print(f((Box(1), b)))


main()
