# The durable hazard fact survives a branch join (UNION merge): a durable
# reference-member tuple bound on each arm, then yielded after the join, is
# still rejected (with the durable message -- no fresh hazard to mask it).
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box, c: Box, cond: bool) -> Iterator[tuple[Int32, Box]]:
    if cond:
        t = (1, b)
    else:
        t = (1, c)
    yield t  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    b = Box(Int32(1))
    c = Box(Int32(2))
    for pair in gen(b, c, True):
        print(pair[1].val)


main()
