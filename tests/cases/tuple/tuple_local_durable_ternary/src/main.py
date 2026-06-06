# A ternary whose arms are both durable-member tuple locals: the result aliases
# whichever arm cond selects, so a post-boundary mutation reaches that arm's
# member. cond=True selects t (member b), so mutating pair[1] is visible in b.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box, c: Box, cond: bool) -> Iterator[tuple[Int32, Box]]:
    t = (1, b)
    t2 = (2, c)
    u = t if cond else t2
    yield u


def main() -> None:
    b = Box(1)
    c = Box(2)
    for pair in gen(b, c, True):
        pair[1].val = 99
    print(b.val, c.val)


main()
