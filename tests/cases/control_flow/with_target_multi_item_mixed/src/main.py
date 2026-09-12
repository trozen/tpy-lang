# One `with` statement, two managers with opposite verdicts: the first lends its
# own storage and its target is read afterwards (the manager is kept), the second
# lends a global (it keeps its ordinary lifetime and is destroyed at the end of
# the block). The decision is per item, so a statement-level answer would get one
# of them wrong -- the delegator's drop lands before the reads, while the owner's
# storage is still there to mutate. Only the delegator carries `__del__`: the kept
# manager's drop is deliberately late (see LANGUAGE_FEATURES.md `with` statement),
# and printing it here would pin that divergence in a parity-checked case.
from typing import Iterator

from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32):
        self.n = n


SHARED: Item = Item(7)


class Owner:
    item: Item

    def __init__(self, n: int32):
        self.item = Item(n)

    def __enter__(self) -> Item:
        return self.item

    def __exit__(self, et, ev, tb) -> None:
        pass


class Delegator:
    def __enter__(self) -> Item:
        return SHARED

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        print("delegator dropped")


def steps() -> Iterator[int32]:
    with Owner(5) as owned, Delegator() as lent:
        pass
    yield 0
    owned.n += 1  # mutating through the kept manager's storage, post-suspension
    yield owned.n
    yield lent.n


def main() -> None:
    for v in steps():
        print(v)
    print(SHARED.n)


main()
