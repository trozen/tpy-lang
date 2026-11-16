# A class whose `__next__` hands over fresh Cells cannot fill a lending
# `Iterable[Cell]` parameter (the callee may keep a step past it); the error
# names the owning spelling and collecting as remedies.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Self
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Cell]:
        if self.n >= 3:
            raise StopIteration
        self.n += 1
        return Cell(self.n)


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(Counter()).v)  # tpyc: error(/Type Counter does not conform to protocol Iterable\[Cell\] in argument 'xs': 'Counter' hands over its steps; declare the parameter 'Iterable\[Own\[Cell\]\]' to take them, or collect them first \(list\(\.\.\.\)\)/)


main()
