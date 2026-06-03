# The hazard fact propagates along a multi-step alias chain (s = r = t): each
# assignment inherits the prior name's fact, so returning the tail by name is
# still rejected.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Int32, Box]:
    t = (1, b)
    r = t
    s = r
    return s  # tpyc: error(/cannot yet alias it across a return/)


def main() -> None:
    pass


main()
