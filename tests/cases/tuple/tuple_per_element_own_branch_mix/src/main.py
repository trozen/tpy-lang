# A branch-mixed reassigned per-element-Own tuple local: one arm binds an
# owning call, the other aliases a field; mutation hits the right storage.
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


def use(h: Holder, c: bool) -> int32:
    if c:
        t = make_pair(9)
    else:
        t = h.pair          # tpyc: ok
    t[1].val = 42
    return t[0]


def main() -> None:
    h = Holder(Box(5))
    print(use(h, False))    # 77
    print(h.pair[1].val)    # 42 (aliased the field, mutation observed)
    h2 = Holder(Box(8))
    print(use(h2, True))    # 9
    print(h2.pair[1].val)   # 8 (owning slot mutated, field untouched)


main()
