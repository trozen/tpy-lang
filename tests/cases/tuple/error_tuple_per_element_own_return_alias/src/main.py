# The borrow-into-Own hazard propagates through a tuple-local ALIAS: aliasing
# a hazardous local then returning it by name is still rejected.
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Own[Box], int32]:
    pair = (b, 0)
    pair2 = pair
    return pair2  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
