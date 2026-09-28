# In a generator body, running a generator built before the loop runs its
# body, which writes what its call may write.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def grower(rows: list[list[int32]]) -> Iterator[int32]:
    while True:
        rows.append([0])
        yield len(rows)


def outer(rows: list[list[int32]]) -> Iterator[int32]:
    it = grower(rows)
    for i in range(3):
        g = items(rows[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'grower', which may write 'rows'/)
        for v in g:
            yield v
            break
        # The subject: pulling `it` appends to rows, moving rows[0].
        for k in it:
            break


print(list(outer([[1, 2]])))
