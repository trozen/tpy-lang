# In a generator body, a call to a nested def writes what it captures, so it
# counts against a generator held into the next pass that borrows it.
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


def outer(n: int32) -> Iterator[int32]:
    buf: list[list[int32]] = [[1, 2]]

    def grow() -> None:
        buf.append([n])

    for i in range(n):
        # The subject: grow() may reallocate buf, which g's element lives in.
        grow()
        g = items(buf[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls 'grow', which may write 'buf'/)
        yield first(g)


print(list(outer(3)))
