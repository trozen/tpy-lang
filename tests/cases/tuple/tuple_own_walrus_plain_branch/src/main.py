# A tuple local bound by a walrus owning call in one branch and a plain-assign
# storage alias in the sibling branch (a walrus FIRST binding, not a walrus
# reassignment). The owning slot is function-scoped; the alias arm shares the
# source (mutation visible), so the local stays one borrow form across both.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:
    return (n, Box(n))


def use(h: Holder, c: bool) -> Int32:
    if c:
        if (t := make_pair(9))[0] > 0:  # tpyc: ok
            return t[1].val
    else:
        t = h.pair
        t[1].val = 77   # mutate through the alias -- visible on h.pair
        return t[0]
    return 0


def main() -> None:
    print(use(Holder(Box(5)), True))    # 9 (owning slot)
    h = Holder(Box(5))
    print(use(h, False))                # 1
    print(h.pair[1].val)                # 77 (aliased, not copied)


main()
