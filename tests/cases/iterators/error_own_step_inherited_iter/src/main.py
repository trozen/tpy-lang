# An inherited `__iter__` returning the object itself runs the subclass's
# `__next__`: an override handing over its step makes the subclass an
# owning iterator, refused at a lending `Iterable[Cell]` parameter. (Why the
# subclass hides 'Base.__iter__': BUGS.md#synthesized-iter-shadows-base-iter.)
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable
from tpy import Own, int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Base:
    c: Cell

    def __init__(self) -> None:
        self.c = Cell(8)

    def __iter__(self) -> "Base":
        return self

    def __next__(self) -> Cell:
        raise StopIteration


class Child(Base):
    def __next__(self) -> Own[Cell]:
        raise StopIteration


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(Child()).v)  # tpyc: error(/Type Child does not conform to protocol Iterable\[Cell\].*'Child' hands over its steps/)


main()
