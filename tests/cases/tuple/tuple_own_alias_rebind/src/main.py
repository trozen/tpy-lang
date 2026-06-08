# A reassigned tuple local mixing an owning-call binding (Own[tuple[...]]) with
# a storage alias must ALIAS the source on rebind, not copy: mutating through
# the rebound local is visible on the source.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (77, b)


def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:
    return (v, Box(v))


def use(h: Holder) -> Int32:
    t = make_pair(9)        # owning binding: slot-materialized, t borrows it
    first = t[1].val
    t = h.pair              # tpyc: ok
    t[1].val = 99           # mutate through the alias -- visible on h.pair
    return first


def main() -> None:
    h = Holder(Box(5))
    r = use(h)
    print(r)                # 9 (owning slot's Box)
    print(h.pair[1].val)    # 99 (alias mutation observed -- not a copy)


main()
