# In a generator body, a generator held into the next pass whose borrowed
# operand has no traceable storage (a walrus) is rejected.
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


def outer(rows: list[list[int32]]) -> Iterator[int32]:
    for i in range(3):
        # The subject: what `(r := rows[0])` borrows is not traced.
        g = items((r := rows[0]))  # tpyc: error(/cannot keep 'g' open across passes of this loop: what it borrows cannot be traced/)
        yield first(g) + len(r)
        rows.append([i])


print(list(outer([[1, 2]])))
