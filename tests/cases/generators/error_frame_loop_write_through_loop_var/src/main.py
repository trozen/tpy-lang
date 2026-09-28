# In a generator body, a write through a for-each variable over storage a
# generator held into the next pass borrows is a write into that storage.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(grid: list[list[list[int32]]]) -> Iterator[int32]:
    for i in range(3):
        for row in grid:
            # The subject: grows the list g iterates (grid[0][0] moves).
            row.append([i])
        g = items(grid[0][0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'append', which may write 'row'/)
        yield first(g)


print(list(outer([[[1, 2]]])))
