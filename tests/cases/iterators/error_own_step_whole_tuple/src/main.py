# A step handing over a whole tuple, `Own[tuple[Cell, int32]]`, hands over
# the `Cell` inside it, so it cannot fill a lending
# `Iterable[tuple[Cell, int32]]`.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot" (per leaf).
from typing import Iterable, Self
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Pairs:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[tuple[Cell, int32]]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return (Cell(self.n), self.n)


def total(xs: Iterable[tuple[Cell, int32]]) -> int32:
    s = 0
    for t in xs:
        s += t[0].v + t[1]
    return s


def main() -> None:
    print(total(Pairs()))  # tpyc: error(/Type Pairs does not conform to protocol Iterable\[tuple\[Cell, int32\]\].*'Pairs' hands over its steps/)


main()
