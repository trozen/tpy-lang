# Returning a per-element-Own tuple local that is OWNING on one reaching
# branch (a call returning tuple[..., Own[T]]) is rejected even though the
# other branch aliases parameter storage -- the owning path's elements die.
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, copy(b))


def make_pair(v: int32) -> tuple[int32, Own[Box]]:
    return (v, Box(v))


def pick(h: Holder, c: bool) -> tuple[int32, Box]:
    if c:
        t = make_pair(9)
    else:
        t = h.pair
    return t  # tpyc: error(/Cannot return this tuple/)


def main() -> None:
    pass


main()
