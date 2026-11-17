# docs/LANGUAGE_FEATURES.md "User classes with a consuming `__iter__`": a class
# whose only __iter__ consumes it (self: Own[Self]) is iterated only by giving
# it up: a `for` over a live one is refused, never a silent consume.
from __future__ import annotations
from typing import Iterator, Self
from tpy import Own, int32


class Only:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [5]

    def __iter__(self: Own[Self]) -> Iterator[Own[int32]]:
        while self.items:
            yield self.items.pop(0)


def main() -> None:
    o = Only()
    # The subject: `o` is read after the loop, so the loop cannot consume it.
    for n in o:  # tpyc: error(/'Only' can only be iterated once it is given up: its only __iter__ consumes/)
        print(n)
    print(len(o.items))


main()
