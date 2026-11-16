# A `__next__` overload set (handing over first) where one overload C++ may select
# hands over its step cannot fill a lending `Iterable[Cell]` parameter.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Self
from tpy import Own, dispatch, int32, readonly


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


FALLBACK = Cell(99)


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    @dispatch
    def __next__(self) -> Own[Cell]:
        if self.n >= 3:
            raise StopIteration
        self.n += 1
        return Cell(self.n)

    @dispatch
    @readonly
    def __next__(self) -> Cell:
        if self.n >= 3:
            raise StopIteration
        return FALLBACK


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(Counter()).v)  # tpyc: error(/Type Counter does not conform to protocol Iterable\[Cell\].*hands over its steps/)


main()
