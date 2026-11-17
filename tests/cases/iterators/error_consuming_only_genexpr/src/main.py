# docs/LANGUAGE_FEATURES.md "User classes with a consuming `__iter__`": a class
# whose only __iter__ consumes is iterated only by giving it up. A generator
# expression's frame takes over no source, so it would lend one: refused.
from __future__ import annotations
from typing import Iterator, Self
from tpy import Own, int32


class Only:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5, 6]

    def __iter__(self: Own[Self]) -> Iterator[Own[int32]]:
        while self.items:
            yield self.items.pop(0)


def main() -> None:
    # The subject: the fresh record would be lent to the generator's frame.
    print(sum(x for x in Only()))  # tpyc: error(/'Only' can only be iterated once it is given up: its only __iter__ consumes/)


main()
