# `__enter__` reaching its own field through a LOCAL HOP (`tmp = self.item;
# return tmp`) still lends the manager's storage, so the manager has to be kept
# for as long as the target is. Reading the return expression's own root called
# this a delegating manager and dropped the manager under a live alias.
#
# The target is mutated after the statement and read back through the manager,
# so a copy at the boundary would show the stale value instead.
from typing import Iterator

from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32):
        self.n = n


class Holder:
    item: Item

    def __init__(self, n: int32):
        self.item = Item(n)

    def __enter__(self) -> Item:
        tmp = self.item  # the hop that hides `self` from the return expression
        return tmp

    def __exit__(self, et, ev, tb) -> None:
        pass

    def total(self) -> int32:
        return self.item.n


def steps(start: int32) -> Iterator[int32]:
    h = Holder(start)
    with h as it:
        pass
    yield it.n
    it.n += 1  # mutates through the alias, after the statement AND a suspension
    yield it.n
    yield h.total()  # the manager sees the mutation -- proof it was not copied


def main() -> None:
    for v in steps(5):
        print(v)


main()
