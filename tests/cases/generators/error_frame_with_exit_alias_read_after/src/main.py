# In a plain function, a second name for a generator its `with` block keeps
# (it borrows a local) cannot be read after the block either
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
        self.xs = [7, 8, 9]


def main() -> None:
    t = Refill()
    with t:
        g = items(t.xs)
        h = g
        print("in", first(h))
    # The subject: h names g, which closed with the block.
    print("after", first(h))  # tpyc: error(/cannot use 'h' after the 'with' block it is bound in: it is another name for 'g'/)


main()
