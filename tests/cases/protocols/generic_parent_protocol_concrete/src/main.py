# Concrete-type re-parameterization of a generic parent. `IntCounted`
# fixes Iterable's T to int32 -- inherited `__iter__(self) -> Iterator[T]`
# is substituted to `Iterator[int32]`. Conformance only matches types
# whose `__iter__` returns an Iterator over int32.
from typing import Iterable, Protocol
from tpy import int32, Own


class IntCounted(Iterable[int32], Protocol):
    def length(self) -> int32: ...


class IntListIter:
    items: list[int32]
    pos: int32

    def __init__(self, items: list[int32]) -> None:
        self.items = items
        self.pos = 0

    def __next__(self) -> int32:
        if self.pos >= int32(len(self.items)):
            raise StopIteration
        v = self.items[self.pos]
        self.pos += 1
        return v


class IntList(IntCounted):
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def add(self, x: int32) -> None:
        self.items.append(x)

    def length(self) -> int32:
        return int32(len(self.items))

    def __iter__(self) -> Own[IntListIter]:
        return IntListIter(self.items)


def length_of(c: IntCounted) -> int32:
    return c.length()


def total(c: IntCounted) -> int32:
    # Iteration via the inherited `Iterable[int32]` requirement; element
    # type is bound concretely at the child level (no type param to
    # substitute).
    s: int32 = 0
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
