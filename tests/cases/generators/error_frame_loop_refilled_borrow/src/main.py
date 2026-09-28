# In a generator body, a generator bound in a loop is still open when the next
# pass runs, so the loop may not write anything it borrows.
from typing import Iterator

from tpy import int32


def inner(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def outer(rows: list[list[int32]]) -> Iterator[int32]:
    for row in rows:
        xs = [row[0], row[1]]
        # The subject: the next pass binds xs again while this g is open.
        g = inner(xs)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop binds 'xs' again/)
        for v in g:
            yield v


print(list(outer([[1, 2], [3, 4]])))
