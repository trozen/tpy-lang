# A generator expression over a fresh source, kept by a generator head
# across a suspension, is refused for now
# (BUGS.md#genexpr-fresh-source-frame-holder).
from typing import Iterable, Iterator
from tpy import int32


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


def outer() -> Iterator[int32]:
    for y in g_it(x * 2 for x in [1, 2, 3]):  # tpyc: error(/a generator expression over a fresh source/)
        yield y


print(list(outer()))
