# A durable reference-member tuple bound on each branch, yielded after the join:
# the yield aliases whichever member the taken branch bound (b for cond=True),
# so a post-boundary mutation reaches that object.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(b: Box, c: Box, cond: bool) -> Iterator[tuple[int32, Box]]:
    if cond:
        t = (1, b)
    else:
        t = (1, c)
    yield t


def main() -> None:
    b = Box(1)
    c = Box(2)
    for pair in gen(b, c, True):
        pair[1].val = 99
    print(b.val, c.val)


main()
