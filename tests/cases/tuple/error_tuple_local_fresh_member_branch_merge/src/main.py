# The owns-fresh fact merges with UNION at a branch join: flagged on the
# then-branch, clear on the else-branch -> still flagged after the join, so the
# yield is rejected (conservative; the runtime path could be the fresh one).
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(b: Box, cond: bool) -> Iterator[tuple[int32, Box]]:
    if cond:
        t = (int32(1), Box(int32(7)))   # owns fresh
    else:
        t = (int32(1), b)               # durable param member
    yield t  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    shared = Box(int32(5))
    for pair in gen(shared, True):
        print(pair[0], pair[1].val)


main()
