# In a generator body, a generator bound in a loop may not borrow storage the
# loop binds again on its next pass (docs/LANGUAGE_FEATURES.md, Generators).
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer() -> Iterator[int32]:
    for i in range(2):
        xs = [i, i + 10]
        # The subject: the next pass refills xs while this g is still open.
        g = items(xs)  # tpyc: error(/cannot bind generator 'g' here: it borrows 'xs', which a loop around it binds again/)
        yield first(g)


print(list(outer()))
