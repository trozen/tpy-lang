# A per-element-Own tuple local bound via the WALRUS form then plain-reassigned
# to a storage alias must alias the source on rebind, like the var-decl form.
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
    first = (t := make_pair(9))[1].val   # tpyc: ok
    t = h.pair
    t[1].val = 99                        # mutate through the alias
    return first


def main() -> None:
    h = Holder(Box(5))
    print(use(h))         # 9 (owning slot's Box)
    print(h.pair[1].val)  # 99 (alias mutation observed -- not a copy)


main()
