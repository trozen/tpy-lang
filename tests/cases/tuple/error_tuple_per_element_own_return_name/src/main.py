# A borrowed (param) member returned by NAME into a per-element-Own tuple
# needs explicit copy() -- same as the literal `return (b, 0)` form.
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Own[Box], int32]:
    pair = (b, 0)
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
