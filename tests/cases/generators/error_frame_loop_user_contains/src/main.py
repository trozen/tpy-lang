# In a generator body, `in` on a user type calls its __contains__, which
# counts like any call against a generator held into the next pass.
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

    def __contains__(self, i: int32) -> bool:
        ROWS.append([i])
        return True


def outer(n: int32) -> Iterator[int32]:
    b = Grid()
    for i in range(n):
        g = items(ROWS[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls '__contains__', which may write the module global 'ROWS'/)
        for v in g:
            yield v
            break
        # The subject: Grid.__contains__ grows ROWS, moving ROWS[0].
        if i in b:
            yield 7


print(list(outer(3)))
