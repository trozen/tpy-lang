# docs/LANGUAGE_FEATURES.md "User classes with a consuming `__iter__`": a class
# whose only __iter__ consumes is iterated only by giving it up. A slot that
# iterates its argument without taking it over (`Iterable[int32]`, no `Own`)
# would lend it: refused with the same reason, not as a protocol mismatch.
from __future__ import annotations
from typing import Iterable, Iterator, Self
from tpy import Own, int32


class Only:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5, 6]

    def __iter__(self: Own[Self]) -> Iterator[Own[int32]]:
        while self.items:
            yield self.items.pop(0)


def total(xs: Iterable[int32]) -> int32:
    t = 0
    for x in xs:
        t += x
    return t


def main() -> None:
    # The subject: a non-owning iterating parameter.
    print(total(Only()))  # tpyc: error(/'Only' can only be iterated once it is given up: its only __iter__ consumes/)


main()
