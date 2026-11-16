# Element types, handover sources and owning-slot consumers the sections
# share.
from typing import Iterable, Iterator, Self
from tpy import Own, int32, nocopy


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make(n: int32) -> Iterator[Own[Cell]]:
    for i in range(n):
        yield Cell(i)


class Counter:
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


class Bag:
    def __iter__(self) -> Iterator[Own[Cell]]:
        for i in range(2):
            yield Cell(i + 5)


@nocopy
class Tok:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class TokBag:
    def __iter__(self) -> Iterator[Own[Tok]]:
        for i in range(2):
            yield Tok(i)


class TokCursor:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Tok]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return Tok(self.n * 3)


def take(xs: Iterable[Own[Cell]]) -> int32:
    s = 0
    for c in xs:
        # a moved step is the callee's own: mutating it is unobservable
        c.v += 10
        s += c.v
    return s


def take_tok(xs: Iterable[Own[Tok]]) -> int32:
    s = 0
    for t in xs:
        s += t.v
    return s


def sum_lent(xs: Iterable[int32]) -> int32:
    s = 0
    for x in xs:
        s += x
    return s
