# In a generator body, a store to a scalar property still counts against a
# generator held into the next pass: the setter is a call and may grow what
# the generator iterates.
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


class Bag:
    rows: list[list[int32]]
    _n: int32

    def __init__(self) -> None:
        self.rows = [[1, 2]]
        self._n = 0

    @property
    def n(self) -> int32:
        return self._n

    @n.setter
    def n(self, v: int32) -> None:
        self._n = v
        self.rows.append([v])


def outer(b: Bag) -> Iterator[int32]:
    for i in range(3):
        g = items(b.rows[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop stores into 'b'/)
        yield first(g)
        # The subject: the setter appends to b.rows, moving b.rows[0].
        b.n = i


print(list(outer(Bag())))
