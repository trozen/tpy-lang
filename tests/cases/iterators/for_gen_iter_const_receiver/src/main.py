# Inverse of for_gen_iter_mutates_receiver: a generator __iter__ that reads
# self only (mutates just its local iteration state) must NOT demote the
# enclosing method -- sum_all() stays inferred readonly (emitted `const`).
# Guards against the fix over-demoting every field iteration.
from tpy import int32, Own
from typing import Iterator


class Source:
    _n: int32

    def __init__(self, n: int32):
        self._n = n

    def __iter__(self) -> Iterator[int32]:
        i: int32 = 0
        while i < self._n:
            yield i
            i += 1


class Consumer:
    _src: Source

    def __init__(self, src: Own[Source]):
        self._src = src

    def sum_all(self) -> int32:
        acc: int32 = 0
        for v in self._src:
            acc += v
        return acc


def main():
    c = Consumer(Source(4))
    print(c.sum_all())   # 0 + 1 + 2 + 3
    print(c.sum_all())   # non-draining -> same


main()
