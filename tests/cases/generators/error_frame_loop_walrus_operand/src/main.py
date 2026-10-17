# In a generator body, a generator held into the next pass whose borrowed
# operand has no single traceable storage (a walrus over a select of two lists)
# is rejected, conservatively (BUGS.md#loop-frame-check-precision); a walrus
# over one list is traced (records/call_result_lending).
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
          c: bool) -> Iterator[int32]:
    for i in range(3):
        # The subject: `(r := rows[0] if c else cols[0])` borrows one of two lists.
        g = items((r := rows[0] if c else cols[0]))  # tpyc: error(/cannot keep 'g' open across passes of this loop: what it borrows cannot be traced/)
        yield first(g) + len(r)
        rows.append([i])


print(list(outer([[1, 2]], [[3]], True)))
