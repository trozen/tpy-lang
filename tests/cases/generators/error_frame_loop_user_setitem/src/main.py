# In a generator body, an element store into a user type calls its
# __setitem__, which counts like any call against a generator held into the
# next pass.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


ROWS: list[list[int32]] = [[1, 2]]


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


class Grid:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __getitem__(self, i: int32) -> int32:
        return self.n

    def __setitem__(self, i: int32, v: int32) -> None:
        self.n = v
        ROWS.append([v])


def outer(n: int32) -> Iterator[int32]:
    b = Grid()
    for i in range(n):
        g = items(ROWS[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls '__setitem__', which may write the module global 'ROWS'/)
        for v in g:
            yield v
            break
        # The subject: Grid.__setitem__ grows ROWS, moving ROWS[0].
        b[0] = i


print(list(outer(3)))
