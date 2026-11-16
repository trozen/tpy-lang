# A partly owned tuple step, `tuple[Own[Cell], Cell]`, cannot fill an owning
# `Iterable[Own[tuple[Cell, Cell]]]` either: the step's lent `Cell` is not
# the whole owned tuple the slot asks for. (Such a `__next__` fails the C++
# build in a plain loop: BUGS.md#mixed-own-tuple-next-gxx-fails.)
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot" (per leaf).
from typing import Iterable, Self
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


G = Cell(50)


class Pairs:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> tuple[Own[Cell], Cell]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return (Cell(self.n), G)


def total(xs: Iterable[Own[tuple[Cell, Cell]]]) -> int32:
    s = 0
    for t in xs:
        s += t[0].v + t[1].v
    return s


def main() -> None:
    print(total(Pairs()))  # tpyc: error(/Type Pairs does not conform to protocol Iterable\[Own\[tuple\[Cell, Cell\]\]\] in argument 'xs'/)


main()
