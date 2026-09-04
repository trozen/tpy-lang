# A dict whose VALUE is a nested tuple, written from a CALL: the whole tuple
# comes back by value, so the store needs no per-level lift (the tuple-literal
# form of the same slot is the sibling shape).
from tpy import Int32


def make() -> tuple[Int32, tuple[Int32, Int32]]:
    return (1, (2, 3))


class Src:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def pair(self) -> tuple[Int32, tuple[Int32, Int32]]:
        return (self.base, (self.base + 1, self.base + 2))


def main() -> None:
    d: dict[Int32, tuple[Int32, tuple[Int32, Int32]]] = {}
    # The call source, free function and method.
    d[1] = make()
    d[2] = Src(10).pair()
    # A same-typed local: the plain value copy, so the local still reads.
    t = make()
    d[3] = t
    # The literal source keeps working beside it.
    d[4] = (7, (8, 9))
    print(len(d))
    print(d[1][0], d[1][1][0], d[1][1][1])
    print(d[2][0], d[2][1][0], d[2][1][1])
    print(d[3][0], d[3][1][0], d[3][1][1])
    print(t[0], t[1][0], t[1][1])
    print(d[4][0], d[4][1][0], d[4][1][1])


main()
