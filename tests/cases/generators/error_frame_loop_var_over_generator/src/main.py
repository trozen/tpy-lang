# In a generator body, a generator bound in a loop over another generator may
# not borrow the loop variable: each pull rebuilds the object it refers to.
from typing import Iterator

from tpy import int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def cells() -> Iterator[Cell]:
    c = Cell(0)
    yield c
    c = Cell(10)
    yield c


def cell_gen(c: Cell) -> Iterator[int32]:
    yield c.v


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer() -> Iterator[int32]:
    src = cells()
    for c in src:
        # The subject: the next pull rebuilds what c refers to.
        g = cell_gen(c)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop binds 'c' again/)
        yield first(g)


print(list(outer()))
