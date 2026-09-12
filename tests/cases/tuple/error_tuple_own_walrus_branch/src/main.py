# Walrus REASSIGNMENT of a borrow-form tuple local (a second `t :=` after the
# local already exists) is rejected -- the walrus path can't rebind a borrow
# tuple in place; use a separate assignment statement.
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
        if (t := h.pair)[0] > 0:
            return t[0]
    else:
        if (t := make_pair(9))[0] > 0:  # tpyc: error(/walrus reassignment of non-value local/)
            return t[0]
    return 0


def main() -> None:
    pass


main()
