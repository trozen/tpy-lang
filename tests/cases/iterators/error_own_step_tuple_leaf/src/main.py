# A tuple step with one handed-over leaf, `tuple[Own[Cell], Cell]`, cannot
# fill a lending `Iterable[tuple[Cell, Cell]]`: the step rule is per leaf.
# A partly owned step is refused at an owning `Iterable[Own[tuple[...]]]`
# too, so no owning spelling is offered. (Such a `__next__` fails the C++
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


def total(xs: Iterable[tuple[Cell, Cell]]) -> int32:
    s = 0
    for t in xs:
        s += t[0].v + t[1].v
    return s


def main() -> None:
    print(total(Pairs()))  # tpyc: error(/Type Pairs does not conform to protocol Iterable\[tuple\[Cell, Cell\]\] in argument 'xs'/)


main()
