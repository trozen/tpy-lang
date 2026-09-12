# A second walrus `:=` on a per-element-Own tuple local (collapsed to borrow
# form by the first binding) is rejected -- the walrus path can't rebind a
# borrow tuple in place; use a separate assignment statement.
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, copy(b))


def make_pair(n: int32) -> tuple[int32, Own[Box]]:
    return (n, Box(n))


def use(h: Holder) -> int32:
    first = (t := make_pair(9))[0]
    if (t := h.pair)[0] > 0:  # tpyc: error(/walrus reassignment of non-value local/)
        return t[0]
    return first


def main() -> None:
    pass


main()
