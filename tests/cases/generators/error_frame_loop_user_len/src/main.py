# In a generator body, `len()` on a user type calls its __len__ (and a truth
# test its __bool__ or __len__), which counts like any call against a
# generator held into the next pass.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32


ROWS: list[list[int32]] = [[1, 2]]


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def __len__(self) -> int32:
        ROWS.append([self.n])
        return self.n


def outer(n: int32) -> Iterator[int32]:
    b = Bag()
    for i in range(n):
        g = items(ROWS[0])  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls '__len__', which may write the module global 'ROWS'/)
        for v in g:
            yield v
            break
        # The subject: Bag.__len__ grows ROWS, moving ROWS[0].
        yield len(b)


print(list(outer(3)))
