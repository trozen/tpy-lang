# The delegated callees for the cross-module case -- one per shape the
# `__for_src` frame field has to spell through this module's namespace.
from typing import Iterator
from tpy import int32, Own


def walk() -> Iterator[int32]:
    yield 1
    yield 2


def pair[T](a: T, b: T) -> Iterator[T]:
    yield a
    yield b


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def steps(self) -> Iterator[int32]:
        yield self.n
        yield self.n + 1


class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 5

    def readings(self) -> Iterator[int32]:
        yield self.n
        yield self.n


def chatty() -> Iterator[int32]:
    print("  callee: before 1")
    yield 1
    print("  callee: after 1")
    yield 2
    print("  callee: after 2")


def guarded() -> Iterator[int32]:
    try:
        yield 1
        yield 2
    finally:
        print("  callee: cleanup")


class Box[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def two(self) -> Iterator[T]:
        yield self.items[0]
        yield self.items[1]
