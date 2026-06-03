# A ternary where only ONE arm is a flagged tuple local (the other is a
# non-flagged param tuple) still flags the result: the provenance derivation
# UNIONs the arms, so the lone hazardous arm carries the fact to the boundary.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(p: tuple[Int32, Box], b: Box, cond: bool) -> Iterator[tuple[Int32, Box]]:
    t = (1, b)
    u = p if cond else t
    yield u  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    pass


main()
