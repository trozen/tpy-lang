# `for v in self._src:` over a generator __iter__ that MUTATES the receiver.
# The enclosing method reads self only, so it looks read-only -- but iterating
# drives the mutating __iter__, which needs a non-const receiver. The method
# must be inferred non-readonly. Iterating drains the source (a second pass
# returns 0), proving the receiver is mutated in place, not copied.
from tpy import int32, Own
from typing import Iterator


class Source:
    _n: int32

    def __init__(self, n: int32):
        self._n = n

    def __iter__(self) -> Iterator[int32]:
        while self._n > 0:
            self._n -= 1
            yield self._n


class Consumer:
    _src: Source

    def __init__(self, src: Own[Source]):
        self._src = src

    def drain_sum(self) -> int32:
        acc: int32 = 0
        for v in self._src:
            acc += v
        return acc


def main():
    c = Consumer(Source(4))
    print(c.drain_sum())   # 3 + 2 + 1 + 0
    print(c.drain_sum())   # source drained -> 0


main()
