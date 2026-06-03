# Regression guard: aliasing a tuple local with only VALUE members (no borrow-form
# member) must NOT be flagged -- the hazard fact only propagates a real borrow
# hazard, so plain value-tuple aliases (including a chain) yield/return freely.
from typing import Iterator
from tpy import Int32


def gen() -> Iterator[tuple[Int32, Int32]]:
    t = (1, 2)
    u = t
    v = u
    yield v


def main() -> None:
    for pair in gen():
        print(pair[0] + pair[1])


main()
