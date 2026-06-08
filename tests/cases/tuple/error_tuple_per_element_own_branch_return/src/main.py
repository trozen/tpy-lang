# Returning a per-element-Own tuple local that is OWNING on one reaching
# branch (a call returning tuple[..., Own[T]]) is rejected even though the
# other branch aliases parameter storage -- the owning path's elements die.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, copy(b))


def make_pair(v: Int32) -> tuple[Int32, Own[Box]]:
    return (v, Box(v))


def pick(h: Holder, c: bool) -> tuple[Int32, Box]:
    if c:
        t = make_pair(9)
    else:
        t = h.pair
    return t  # tpyc: error(/Cannot return this tuple/)


def main() -> None:
    pass


main()
