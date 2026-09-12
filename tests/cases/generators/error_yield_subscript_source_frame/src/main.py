# A SUBSCRIPT source at a reference yield slot: a container element read has no
# borrow lvalue the yield ladder admits, so it stays a named rung on BOTH halves
# of the reference axis. A record element read out of a container at a value
# position additionally has no lowered form at all -- BUGS.md#subscript-elem-record,
# the blocker. Pinned at the record slot; the container twin rejects the same tag.
from typing import Iterator

from tpy import int32


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def each(xs: list[Box]) -> Iterator[Box]:  # tpyc: error(/not yet supported.*res.yield_type/)
    yield xs[0]
    yield xs[1]


def main() -> None:
    for b in each([Box(1), Box(2)]):
        print(b.v)


main()
