# In a generator body, a loop over a held generator that borrows a rebound
# local is rejected (docs/LANGUAGE_FEATURES.md, Generators).
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def outer(c: bool) -> Iterator[int32]:
    xs = [1, 2]
    ys = xs
    if c:
        xs = [3, 4]
    g = items(xs)
    # The subject: g borrows xs's rebind storage.
    for v in g:  # tpyc: error(/cannot loop over generator 'g' here: it borrows 'xs'/)
        yield v
    yield ys[0]


print(list(outer(True)))
