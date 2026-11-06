# A borrow tuple local first bound by a walrus inside a branch is refused at
# that walrus (BUGS.md#branch-walrus-borrow-tuple-declaration-rejects).
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def make_pair(n: int32) -> Own[tuple[int32, Box]]:
    return (n, Box(n))


def use(h: Holder, c: bool) -> int32:
    if c:
        if (t := h.pair)[0] > 0:  # tpyc: error(/this construct is not yet supported by C\+\+ code generation \(expr.walrus\)/)
            return t[0]
    else:
        if (t := make_pair(9))[0] > 0:
            return t[0]
    return 0


def main() -> None:
    pass


main()
