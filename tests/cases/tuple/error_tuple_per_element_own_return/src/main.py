# A reassigned per-element-Own tuple local whose OWNING binding reaches the
# return points into a function-local slot that dies at exit -- rejected.
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


def pick(h: Holder) -> tuple[Int32, Box]:
    t = h.pair
    t = make_pair(9)
    return t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
