# H1: `yield` inside a `match` on an enum subject (switch-based dispatch).
# The arm bodies suspend; the dispatch keeps its switch shape and each arm
# body is routed through the resumable state machine.
from typing import Iterator
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1
    BLUE = 2


def gen(c: Color) -> Iterator[int]:
    match c:
        case Color.RED:
            yield 1
            yield 2
        case Color.GREEN:
            yield 3
        case Color.BLUE:
            yield 4
    yield 100


def main() -> None:
    for v in gen(Color.RED):
        print(v)
    print("--")
    for v in gen(Color.BLUE):
        print(v)


main()
