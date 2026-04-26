# Generic parent protocols. A protocol can extend a parameterized parent
# (e.g. `Iterable[T]`); methods/fields from the parent are inherited with
# the parent's type parameter substituted by the child's. Conformance and
# method lookup both walk the inherited surface.
from typing import Iterable, Protocol
from tpy import Int32, Own


# Counted[T] structurally requires `length()` plus inherits the
# `__iter__(self) -> Iterator[T]` requirement from Iterable[T].
class Counted[T](Iterable[T], Protocol):
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


class IntList(Counted[Int32]):
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

    def add(self, x: Int32) -> None:
        self.items.append(x)

    def length(self) -> Int32:
        return Int32(len(self.items))

    def __iter__(self) -> Own[IntListIter]:
        return IntListIter(self.items)


def length_of(c: Counted[Int32]) -> Int32:
    # Direct method on the protocol.
    return c.length()


def total(c: Counted[Int32]) -> Int32:
    # Iteration on a value typed as the child protocol uses the inherited
    # `__iter__` requirement from `Iterable[T]`.
    s: Int32 = 0
    for x in c:
        s += x
    return s


def main() -> None:
    xs = IntList()
    xs.add(10)
    xs.add(20)
    xs.add(30)
    print(length_of(xs))   # 3
    print(total(xs))       # 60
    # Iteration over the concrete type still works the usual way.
    s: Int32 = 0
    for x in xs:
        s += x
    print(s)               # 60


main()
