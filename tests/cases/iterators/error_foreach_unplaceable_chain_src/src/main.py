# A for-each iterable may be one hop from a storage key the borrow tracker can
# name (`rows[i]`, `g.rows`, `self.rows[i]`, `table[k]`). A DEEPER chain is
# refused: the iteration's loan would have no key, so nothing could catch
# `self.shelf.rows.append(...)` inside the loop reallocating the very vector
# the iteration points into (BUGS.md#iter-borrow-place-needs-hops).
# Workarounds: bind the element (`v = self.shelf.rows[i]` then `for b in v`)
# or the intermediate record (`sh = self.shelf` then `for b in sh.rows[i]`).
# `cube[i][j]` below is the same reject from the other direction -- a subscript
# whose receiver is itself a subscript -- and is unannotated only because the
# compile stops at the first error.
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


def bump_deep(d: Depot, i: int32) -> None:
    for b in d.shelf.rows[i]:  # tpyc: error(/foreach.iter_borrow_unplaceable/)
        b.n += 5


def bump_nested(cube: list[list[list[Box]]], i: int32, j: int32) -> None:
    # same tag: the subscript receiver is itself a subscript
    for b in cube[i][j]:
        b.n += 5


def main() -> None:
    bump_deep(Depot(), 0)


main()
