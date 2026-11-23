# A generator body whose loop head hands a nested temporary to a generator
# call: the frame holds it across the yields of its pass, one slot per pass.
from typing import Iterator

from tpy import int32, nocopy


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(c: Cell) -> Cell:
    return c


def inner(c: Cell) -> Iterator[int32]:
    yield c.n
    c.n += 1
    yield c.n


def outer() -> Iterator[int32]:
    for i in range(3):
        for n in inner(ident(Cell(i * 10))):  # tpyc: ok
            yield n


def main() -> None:
    print("generator", list(outer()))
