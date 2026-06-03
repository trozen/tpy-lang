# An alias assigned on only one branch (u = t in the then-arm, a fresh literal
# in the else-arm) still carries the hazard after the join: the UNION merge keeps
# the fact, so the post-join return is conservatively rejected.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box, cond: bool) -> tuple[Int32, Box]:
    t = (1, b)
    if cond:
        u = t
    else:
        u = (2, b)
    return u  # tpyc: error(/cannot yet alias it across a return/)


def main() -> None:
    pass


main()
