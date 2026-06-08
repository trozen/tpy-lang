# A walrus-bound owning tuple local rebound to a storage alias must ALIAS the
# source (mutation visible), like the plain-assignment form.
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


def use(h: Holder) -> Int32:
    if (t := make_pair(9))[0] > 0:  # tpyc: ok
        first = t[1].val            # 9 -- the owning slot's Box
        t = h.pair                  # rebind to the alias
        t[1].val = 99               # mutate through it -- visible on h.pair
        return first
    return 0


def main() -> None:
    h = Holder(Box(5))
    print(use(h))            # 9
    print(h.pair[1].val)     # 99 (aliased, not copied)


main()
