# A class that names `Iterable[Cell]` as its base but declares
# `__next__ -> Own[Cell]` still hands its steps over, so it cannot fill a
# lending `Iterable[Cell]` base: the class's own declaration is refused
# (its `__iter__` returns an owning iterator where the base lends).
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Self
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Counter(Iterable[Cell]):  # tpyc: error(/Class 'Counter' does not conform to protocol 'Iterable\[Cell\]'/)
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Cell]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return Cell(self.n)


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(Counter()).v)


main()
