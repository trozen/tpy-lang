# A generator loop over a slice of a container two hops into a tuple element
# (`t[0].ns[0:2]`) has no placeable iteration loan and is rejected at the
# generator, rebind of `t` or not (BUGS.md#iter-borrow-place-needs-hops).
from typing import Iterator

from tpy import int32


class N:
    ns: list[int32]

    def __init__(self, i: int32) -> None:
        self.ns = [i, i + 1, i + 2]


def gen(c: bool) -> Iterator[int32]:  # tpyc: error(/res\.for_iter_borrow_unplaceable/)
    t = (N(1), 5)
    # the slice two hops into element 0 spells no iteration loan key
    for x in t[0].ns[0:2]:
        if c:
            t = (N(10), 6)
        yield x
    yield t[0].ns[0]


for x in gen(True):
    print("gen", x)
