# A tuple local cannot rebind between OWNING storage (a call returning
# Own[...] elements) and a storage alias: the C++ local has one fixed
# shape, and crossing the two would either silently copy where CPython
# aliases or point into a dying temporary. Both directions are rejected.
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


def pick(h: Holder) -> Int32:
    t = make_pair(9)
    t = h.pair  # tpyc: error(/Cannot bind tuple local/)
    return t[0]


def main() -> None:
    pass


main()
