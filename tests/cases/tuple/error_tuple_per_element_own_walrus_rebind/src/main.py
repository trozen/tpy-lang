# A second walrus `:=` on a per-element-Own tuple local (collapsed to borrow
# form by the first binding) is rejected -- the walrus path can't rebind a
# borrow tuple in place; use a separate assignment statement.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, copy(b))


def make_pair(n: Int32) -> tuple[Int32, Own[Box]]:
    return (n, Box(n))


def use(h: Holder) -> Int32:
    first = (t := make_pair(9))[0]
    if (t := h.pair)[0] > 0:  # tpyc: error(/walrus reassignment of non-value local/)
        return t[0]
    return first


def main() -> None:
    pass


main()
