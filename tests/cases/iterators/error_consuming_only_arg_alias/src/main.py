# docs/LANGUAGE_FEATURES.md "User classes with a consuming `__iter__`": a class
# whose only __iter__ consumes is iterated only by giving it up. An owning-slot
# argument whose name another name aliases in the same statement is lent once
# the body settles, so it is refused, never a silent consume.
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


def loop(xs: Iterable[Own[int32]]) -> int32:
    t = 0
    for x in xs:
        t += x
    return t


def main() -> None:
    o = Only()
    o2 = o
    # The subject: `o2` reads the object after `loop(o)` takes it.
    print("same stmt", loop(o), len(o2.items))  # tpyc: error(/'Only' can only be iterated once it is given up: its only __iter__ consumes/)


main()
