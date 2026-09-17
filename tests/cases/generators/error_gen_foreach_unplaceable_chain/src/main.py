# The resumable (generator / async) for-each takes the same iterable rule as
# the sync one: a chain of two field hops has no storage key the
# iterator-invalidation check can look up, and the frame's captured source is
# left unmarked, so a body writing through the element would bind a const
# frame field and then assign to it.
from typing import Iterator

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Shelf:
    rows: list[list[Box]]

    def __init__(self) -> None:
        self.rows = [[Box(1)]]


class Depot:
    shelf: Shelf

    def __init__(self) -> None:
        self.shelf = Shelf()


# the reject is reported at the `def` -- the resumable iter-setup lowering
# raises outside a statement context, as its `res.*` siblings do
def gen(d: Depot) -> Iterator[int32]:  # tpyc: error(/res.for_iter_borrow_unplaceable/)
    for b in d.shelf.rows[0]:
        b.n += 1
        yield b.n


def main() -> None:
    for v in gen(Depot()):
        print(v)


main()
