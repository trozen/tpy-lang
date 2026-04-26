# Concrete-type re-parameterization of a generic parent. `IntCounted`
# fixes Iterable's T to Int32 -- inherited `__iter__(self) -> Iterator[T]`
# is substituted to `Iterator[Int32]`. Conformance only matches types
# whose `__iter__` returns an Iterator over Int32.
from typing import Iterable, Protocol
from tpy import Int32, Own


class IntCounted(Iterable[Int32], Protocol):
    def length(self) -> Int32: ...


class IntListIter:
    items: list[Int32]
    pos: Int32

    def __init__(self, items: list[Int32]) -> None:
        self.items = items
        self.pos = 0

    def __next__(self) -> Int32:
        if self.pos >= Int32(len(self.items)):
            raise StopIteration
        v = self.items[self.pos]
        self.pos += 1
        return v


class IntList(IntCounted):
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

    def add(self, x: Int32) -> None:
        self.items.append(x)

    def length(self) -> Int32:
        return Int32(len(self.items))

    def __iter__(self) -> Own[IntListIter]:
        return IntListIter(self.items)


def length_of(c: IntCounted) -> Int32:
    return c.length()


def total(c: IntCounted) -> Int32:
    # Iteration via the inherited `Iterable[Int32]` requirement; element
    # type is bound concretely at the child level (no type param to
    # substitute).
    s: Int32 = 0
    for x in c:
        s += x
    return s


def main() -> None:
    xs = IntList()
    xs.add(7)
    xs.add(8)
    print(length_of(xs))   # 2
    print(total(xs))       # 15


main()
