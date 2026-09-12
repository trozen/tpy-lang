# A per-element-Own walrus tuple local bound inside an `if` condition then
# rebound to a storage alias aliases the source (walrus + branch-hoist).
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (77, copy(b))


def make_pair(v: int32) -> tuple[int32, Own[Box]]:
    return (v, Box(v))


def use(h: Holder) -> int32:
    if (t := make_pair(9))[0] > 0:   # tpyc: ok
        first = t[1].val
        t = h.pair
        t[1].val = 99                # mutate through the alias
        return first
    return -1


def main() -> None:
    h = Holder(Box(5))
    print(use(h))         # 9
    print(h.pair[1].val)  # 99


main()
