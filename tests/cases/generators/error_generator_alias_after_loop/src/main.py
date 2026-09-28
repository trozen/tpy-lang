# A second name for a generator a plain function keeps in its loop body's
# block cannot be read after the loop: the generator ends with the pass
# (BUGS.md#plain-loop-generator-closes-at-pass-end).
from typing import Iterator

from tpy import int32


def items(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x


def first(g: Iterator[int32]) -> int32:
    for v in g:
        return v
    return -1


def f(xs: list[int32]) -> None:
    for i in range(2):
        g = items(xs)
        h = g
        print("in", i, first(g))
    # The subject: h names the last pass's g, which is gone.
    print("after", first(h))  # tpyc: error(/cannot use 'h' after the loop it is bound in: it is another name for 'g'/)


f([1, 2, 3])
