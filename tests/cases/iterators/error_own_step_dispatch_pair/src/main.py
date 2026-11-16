# A `@dispatch` pair over `Iterable[Cell]` and `Iterable[Own[Cell]]` cannot
# tell a live list apart: both conform equally, so the call is ambiguous
# (docs/OVERLOAD_DESIGN.md "Ambiguous overload").
from typing import Iterable, Iterator
from tpy import Own, int32, dispatch


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make(n: int32) -> Iterator[Own[Cell]]:
    for i in range(n):
        yield Cell(i)


@dispatch
def f(xs: Iterable[Cell]) -> int32:
    for c in xs:
        c.v += 100
    return 1


@dispatch
def f(xs: Iterable[Own[Cell]]) -> int32:
    for c in xs:
        c.v += 100
    return 2


def main() -> None:
    ys = [Cell(1)]
    print(f(ys), ys[0].v)  # tpyc: error(/Ambiguous overload for 'f'/)


main()
