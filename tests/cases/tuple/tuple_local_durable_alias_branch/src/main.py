# u is bound on both branches to a durable-member tuple (alias in one arm, a
# fresh literal over the same durable member b in the other). After the join,
# returning u aliases b regardless of branch, so a post-boundary mutation
# reaches b.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box, cond: bool) -> tuple[int32, Box]:
    t = (1, b)
    if cond:
        u = t
    else:
        u = (2, b)
    return u


def main() -> None:
    b = Box(5)
    pair = f(b, True)
    pair[1].val = 99
    print(b.val)


main()
