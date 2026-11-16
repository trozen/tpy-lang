# A child protocol redeclaring `__iter__ -> Iterator[Own[Cell]]` is no
# substitute for its parent whose `__iter__ -> Own[Iterator[Cell]]` lends:
# the `Own` around the iterator object does not make its step owning, so
# the child is refused at its declaration.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Iterator, Protocol
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Lent(Iterable[Cell], Protocol):
    def __iter__(self) -> Own[Iterator[Cell]]: ...


class Owned(Lent, Protocol):  # tpyc: error(/Protocol 'Owned' does not conform to protocol 'Lent': method '__iter__' hands over its step/)
    def __iter__(self) -> Iterator[Own[Cell]]: ...


def first(xs: Lent) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first([Cell(1)]).v)


main()
