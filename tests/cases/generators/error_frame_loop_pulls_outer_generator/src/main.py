# In a generator body, pulling a generator built before the loop through a
# method runs its body, which writes what its building call may write.
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


def grower(rows: list[list[int32]]) -> Iterator[int32]:
    while True:
        rows.append([0])
        yield len(rows)


def outer(rows: list[list[int32]]) -> Iterator[int32]:
    h = grower(rows)
    for i in range(3):
        g = items(rows[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'grower', which may write 'rows'/)
        yield first(g)
        # The subject: h's body appends to rows, moving rows[0].
        try:
            h.__next__()
        except StopIteration:
            pass


print(list(outer([[1, 2]])))
