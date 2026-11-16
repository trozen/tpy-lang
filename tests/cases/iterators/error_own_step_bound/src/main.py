# A type parameter bounded by an owning protocol cannot fill a lending
# `Iterable[Cell]` parameter.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Protocol, Self
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Owned(Protocol):
    def __iter__(self) -> Self: ...
    def __next__(self) -> Own[Cell]: ...


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def bridge[I: Owned](xs: I) -> Cell:
    return first(xs)  # tpyc: error(/Type I does not conform to protocol Iterable\[Cell\].*'I' hands over its steps/)


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


def main() -> None:
    print(bridge(Counter()).v)


main()
