# A generator first bound in a block and read after it may not borrow a local
# of that block (BUGS.md#generator-block-bind-borrows-local-rejects).
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def main(c: bool, xs: list[int32]) -> None:
    if c:
        ys = [10, 11]
        # The subject: g outlives the block ys dies with.
        g = items(ys)  # tpyc: error(/decl.frame_borrows_local/)
    else:
        g = items(xs)
    for v in g:
        print(v)


main(True, [1])
