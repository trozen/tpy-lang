# Generator yielding tuple[T | None, ...]: the yield expression is an
# rvalue tuple literal that must be lifted to storage form so it slots
# into std::optional<storage-tuple>. Covers simple-while, simple-for
# (range strategy), and simple-for (NativeIterable strategy).
from typing import Iterator
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def gen_for(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    for it in items:
        yield (it, None)


def gen_range(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    for i in range(len(items)):
        yield (items[i], None)


def gen_while(items: list[P], n: int32) -> Iterator[tuple[P | None, P | None]]:
    i = int32(0)
    while i < n:
        yield (items[i], None)
        i = i + 1


def main() -> None:
    items = [P(1), P(2), P(3)]

    for a, b in gen_for(items):
        if a is not None:
            print(a.x)

    for a, b in gen_range(items):
        if a is not None:
            print(a.x)

    for a, b in gen_while(items, int32(2)):
        if a is not None:
            print(a.x)


main()
