# An `Own` type argument on a protocol's non-step member leaves its
# `__iter__ -> Iterator[Cell]` lending, so a source whose steps are handed
# over is still refused.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Iterator, Protocol, Self
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


class Mixed[T](Protocol):
    def token(self) -> T: ...
    def __iter__(self) -> Iterator[Cell]: ...


class Source(Counter):
    def token(self) -> int32:
        return 42


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def consume(xs: Mixed[Own[int32]]) -> int32:
    return xs.token() + first(xs).v


def main() -> None:
    print(consume(Source()))  # tpyc: error(/Type Source does not conform to protocol Mixed\[Own\[int32\]\].*'Source' hands over its steps/)


main()
