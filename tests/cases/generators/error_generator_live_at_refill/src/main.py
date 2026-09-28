# A plain function keeps a generator first bound in a loop body in the pass's
# block; read after the loop, it may borrow only what outlives the loop.
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


def main() -> None:
    for c in cells():
        # g borrows the pulled c, rebuilt by the next pull.
        g = cell_gen(c)
        print(first(g))
    # The subject: g borrows the loop variable, which ends with the loop.
    print(first(g))  # tpyc: error(/cannot use 'g' after the loop it is bound in: it borrows 'c'/)


main()
