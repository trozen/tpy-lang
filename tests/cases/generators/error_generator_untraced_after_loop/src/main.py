# A generator bound in a loop and read after it is refused when what it
# borrows cannot be traced (a select of two lists' elements); an over-reject
# for a loop that writes nothing (BUGS.md#loop-frame-check-precision).
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def outer(rows: list[list[int32]], cols: list[list[int32]],
          c: bool) -> int32:
    for i in range(3):
        g = items(rows[0] if c else cols[0])
    # The subject: `g` is read after the loop that binds it.
    return first(g)  # tpyc: error(/cannot use 'g' after the loop it is bound in: what it borrows cannot be traced/)


print(outer([[1, 2]], [[3]], True))
