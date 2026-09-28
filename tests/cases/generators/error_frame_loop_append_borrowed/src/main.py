# In a generator body, a generator held into the next pass may not see its
# list grown: an append can reallocate the buffer its iterator points into.
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


def outer(xs: list[int32]) -> Iterator[int32]:
    for i in range(3):
        g = items(xs)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'append', which may write 'xs'/)
        yield first(g)
        # The subject: a structural mutation of the list g iterates.
        xs.append(i)


print(list(outer([1, 2])))
