# In a generator body, a generator bound in a `with` body stays open past
# the block, so its manager's __exit__ may not write what it borrows.
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


class Refill:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def __enter__(self) -> "Refill":
        return self

    def __exit__(self, et, ev, tb) -> None:
        self.xs = [7, 8]


def outer() -> Iterator[int32]:
    r = Refill()
    with r:
        # The subject: __exit__ replaces r.xs while g still iterates it.
        g = items(r.xs)  # tpyc: error(/cannot keep 'g' open past the 'with' block it is bound in: the block's exit may write 'r', which 'g' borrows/)
        yield first(g)
    yield first(g)


print(list(outer()))
