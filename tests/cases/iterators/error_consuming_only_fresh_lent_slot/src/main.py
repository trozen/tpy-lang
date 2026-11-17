# A FRESH record whose only __iter__ consumes, with reference steps, passed to
# a lending `Iterable[Cell]` parameter is refused: the callee would bind
# references to steps the consuming iterator hands over and drops.
# It is refused as consuming-only, not for its owned steps: with no lending
# `__iter__` at all, no lending parameter could take it, whatever it yields.
from __future__ import annotations
from typing import Iterable, Iterator, Self
from tpy import Own, int32


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


class Only:
    items: list[Cell]

    def __init__(self) -> None:
        self.items = [Cell(1), Cell(2)]

    def __iter__(self: Own[Self]) -> Iterator[Own[Cell]]:
        while self.items:
            yield self.items.pop(0)


def total(xs: Iterable[Cell]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def main() -> None:
    # The subject: a fresh consuming-only record at a lending slot.
    print(total(Only()))  # tpyc: error(/'Only' can only be iterated once it is given up: its only __iter__ consumes/)


main()
