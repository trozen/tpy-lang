# Reference-type tuple-unpack borrow aliases held across a yield in a generator:
# a, b must survive as `T*` aliases of the live list elements, so mutating them
# between yields is visible through the source list (generator sibling).
from typing import Iterator


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def first_two(items: list[Box]) -> tuple[Box, Box]:
    return (items[0], items[1])


def g(items: list[Box]) -> Iterator[int]:
    a, b = first_two(items)
    yield a.n
    a.n += 10
    b.n += 20
    yield a.n + b.n


def main() -> None:
    items = [Box(1), Box(2)]
    for v in g(items):
        print(v)
    print(items[0].n, items[1].n)


main()
