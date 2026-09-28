# In a plain function, a generator bound in a `with` body over a local stays
# the block's and closes before __exit__ runs: a read after it is an error
# (BUGS.md#generator-block-bind-borrows-local-rejects).
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


def main() -> None:
    r = Refill()
    with r:
        g = items(r.xs)
        print("in", first(g))
    # The subject: g would read the list __exit__ replaced.
    print("after", first(g))  # tpyc: error(/cannot use 'g' after the 'with' block it is bound in: it borrows 'r'/)


main()
