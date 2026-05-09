# Generator with `prev: T | None = None` local surviving a yield, used
# as a pointer-form Optional tuple element. Covers the initial None
# init, value -> value carry, and a mid-flight reset to None on a
# sentinel element so we exercise the `prev = None` re-assignment path
# (not just the initial decl).
from typing import Iterator
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def gen(items: list[P]) -> Iterator[tuple[P | None, P | None]]:
    prev: P | None = None
    for it in items:
        yield (prev, it)
        # Sentinel: x == 0 means "reset prev"; otherwise carry forward.
        if it.x == Int32(0):
            prev = None
        else:
            prev = it
    yield (prev, None)


def show(p: tuple[P | None, P | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("a=none")
    if b is not None:
        print(b.x)
    else:
        print("b=none")
    print("---")


def main() -> None:
    items = [P(1), P(2), P(0), P(3)]
    for pair in gen(items):
        show(pair)


main()
