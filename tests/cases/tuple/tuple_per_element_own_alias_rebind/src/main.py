# A reassigned tuple local mixing a per-element-Own owning binding
# (tuple[Int32, Own[Box]]) with a storage alias aliases the source on
# rebind, not copies: mutation through the rebound local is visible on it.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (77, copy(b))


def make_pair(v: Int32) -> tuple[Int32, Own[Box]]:
    return (v, Box(v))


def use(h: Holder) -> Int32:
    t = make_pair(9)        # per-element owning binding: slot-materialized
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
