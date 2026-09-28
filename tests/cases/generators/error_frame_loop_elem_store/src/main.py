# In a generator body, an element store counts against a generator held into
# the next pass even when it borrows the whole list: the generator's own
# loop holds a reference to an element, which the store replaces.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


def items(rows: list[list[int32]]) -> Iterator[int32]:
    try:
        for r in rows:
            yield r[0]
    finally:
        print("items finally", rows[0][0])


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(rows: list[list[int32]]) -> Iterator[int32]:
    for i in range(2):
        g = items(rows)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop stores into 'rows'/)
        yield first(g)
        # The subject: replaces the element g's loop variable refers to.
        rows[0] = [100 + i, 200 + i]


print(list(outer([[1, 2], [3, 4]])))
