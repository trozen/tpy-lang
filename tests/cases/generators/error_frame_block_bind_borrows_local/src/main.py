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
        g = items(ys)
    else:
        g = items(xs)
    # The subject: g borrows ys, which dies with its block.
    for v in g:  # tpyc: error(/cannot use 'g' after the 'if' block it is bound in: it borrows 'ys'/)
        print(v)


main(True, [1])
