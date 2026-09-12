# Opposite order of error_tuple_per_element_own_return: a per-element-Own
# local bound owning-first then rebound to durable param storage is returned
# safely -- the alias reaches the return, so the flow-sensitive owning fact
# is discarded and the return is accepted (mutation through it hits h.pair).
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


def pick(h: Holder) -> tuple[int32, Box]:
    t = make_pair(9)
    t = h.pair          # tpyc: ok
    return t


def use(h: Holder) -> int32:
    pr = pick(h)
    pr[1].val = 55      # mutate the returned alias -- visible on h.pair
    return pr[0]


def main() -> None:
    h = Holder(Box(5))
    print(use(h))         # 77
    print(h.pair[1].val)  # 55 (returned tuple aliased h.pair, not a copy)


main()
