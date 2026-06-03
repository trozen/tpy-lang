# A ternary whose arms are both flagged tuple locals is itself hazardous (the
# result aliases either): the fact is UNION-derived from both arms, so yielding
# the ternary-bound local by name is rejected.
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
    yield u  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    b = Box(Int32(1))
    c = Box(Int32(2))
    for pair in gen(b, c, True):
        print(pair[1].val)


main()
