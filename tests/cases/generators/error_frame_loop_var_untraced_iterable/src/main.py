# In a generator body, a for-each variable over something whose storage
# cannot be traced (`reversed(cells)`) may refer into anything a generator
# held into the next pass borrows.
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


class G:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[1, 2]]


def outer(cells: list[G]) -> Iterator[int32]:
    for i in range(3):
        g = items(cells[0].rows[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'append', which may write 'c'/)
        yield first(g)
        for c in reversed(cells):
            # The subject: grows cells[0].rows, moving cells[0].rows[0].
            c.rows.append([i])


print(list(outer([G()])))
