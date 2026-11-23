# A yielded tuple whose borrow member points into a fresh argument: the
# argument dies with the statement, so the pointer the yield hands out
# dangles at the next resumption -- refused as its scalar twin
# (`yield ident(P(7))`) is.
from typing import Iterator

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(p: P) -> tuple[int32, P]:
    return (0, p)


def g() -> Iterator[tuple[int32, P]]:
    yield mk(P(7))  # tpyc: error(/Cannot yield local or temporary as tuple element 1/)


def main() -> None:
    for i, p in g():
        print("g", i, p.x)


main()
