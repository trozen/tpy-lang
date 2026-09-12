# A borrowed (param) member passed by NAME into a per-element-Own tuple param
# needs explicit copy() (the deferred-name analog of the literal-arg check).
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def sink(p: tuple[Own[Box], int32]) -> int32:
    return p[0].val


def f(b: Box) -> int32:
    pair = (b, 0)
    return sink(pair)  # tpyc: error(/pass borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
