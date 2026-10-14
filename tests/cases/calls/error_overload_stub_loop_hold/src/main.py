# A `typing.overload` stub carries its implementation's argument write, so
# a generator over the list cannot stay open across a pass that calls it.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator, overload

from tpy import int32


class C:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    @overload
    def feed(self, xs: list[int32]) -> None: ...
    @overload
    def feed(self, xs: list[int32], n: int32) -> None: ...
    def feed(self, xs: list[int32], n: int32 = 1) -> None:
        xs.append(n)


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(xs: list[int32]) -> Iterator[int32]:
    c = C()
    for i in range(3):
        g = items(xs)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'feed', which may write 'xs'/)
        yield first(g)
        c.feed(xs)  # tpyc: warning(/Passing borrowed container 'xs'/)


print(list(outer([1, 2])))
