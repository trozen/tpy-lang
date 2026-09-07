# A READ-ONLY binding spells const element pointers, and the const set keys the
# whole optional-borrow-tuple family out.
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]

    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def read_only(h: Holder, flag: bool) -> Int32:
    t: tuple[Int32, Box] | None = None
    if flag:
        t = h.pair  # tpyc: error(/field.result_type/)
    if t is not None:
        return t[1].val
    return -1


def main() -> None:
    h = Holder(Box(9))
    print(read_only(h, True))


main()
