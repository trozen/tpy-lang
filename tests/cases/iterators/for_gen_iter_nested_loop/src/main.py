# Nested for-loops: `for src in self._sources: for v in src:` where src's
# generator __iter__ mutates it. The OUTER loop variable `src` must be bound
# mutable (not const), else the inner iteration can't drive the mutating
# __iter__. Guards the mark_loop_var_mutated path. Iterating drains every
# source (second pass -> 0), proving the elements are mutated in place.
from tpy import Int32, Own
from typing import Iterator


class Source:
    _n: Int32

    def __init__(self, n: Int32):
        self._n = n

    def __iter__(self) -> Iterator[Int32]:
        while self._n > 0:
            self._n -= 1
            yield self._n


class Consumer:
    _sources: list[Source]

    def __init__(self, srcs: Own[list[Source]]):
        self._sources = srcs

    def total(self) -> Int32:
        acc: Int32 = 0
        for src in self._sources:
            for v in src:
                acc += v
        return acc


def main():
    c = Consumer([Source(3), Source(2)])
    print(c.total())   # (2+1+0) + (1+0)
    print(c.total())   # all sources drained -> 0


main()
