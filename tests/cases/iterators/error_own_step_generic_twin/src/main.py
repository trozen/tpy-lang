# The generic twin of a lending parameter, `Iterable[T]`, refuses an
# owning generator at T = Cell as its concrete twin `Iterable[Cell]` does.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
# The builtin combinators take such a lending slot (`enumerate(make(3))` is
# refused too) until TODO.md "Combinator signatures" gives them owning ones.
from typing import Iterable, Iterator
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make(n: int32) -> Iterator[Own[Cell]]:
    for i in range(n):
        yield Cell(i)


def first[T](xs: Iterable[T]) -> T:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(make(3)).v)  # tpyc: error(/does not conform to protocol Iterable\[Cell\]/)


main()
