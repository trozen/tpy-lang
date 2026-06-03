# An explicitly-annotated alias binding (u: tuple[...] = t) propagates the hazard
# fact just like a plain alias -- both assignment forms funnel through the same
# fact-update chokepoint.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Int32, Box]:
    t = (1, b)
    u: tuple[Int32, Box] = t
    return u  # tpyc: error(/cannot yet alias it across a return/)


def main() -> None:
    pass


main()
