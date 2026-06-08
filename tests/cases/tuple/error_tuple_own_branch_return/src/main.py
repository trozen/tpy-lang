# Returning a tuple local that is OWNING on one reaching branch (a call
# returning Own[tuple[...]]) is rejected even though another branch aliases
# parameter storage: on the owning path the elements live in a function-local
# slot that dies at return, so the borrow would dangle.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:
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
