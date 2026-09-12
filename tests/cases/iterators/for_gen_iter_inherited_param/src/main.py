# An INHERITED mutating generator __iter__, iterated via a plain parameter
# (free function). Guards two paths: the parent-inclusive __iter__ lookup
# (the __iter__ lives on the base) and the param-rooted mark_param_mutated
# branch (the receiver is a param, not self).
from tpy import int32, Own
from typing import Iterator


class BaseSource:
    _n: int32

    def __init__(self, n: int32):
        self._n = n

    def __iter__(self) -> Iterator[int32]:
        while self._n > 0:
            self._n -= 1
            yield self._n


class Source(BaseSource):
    _tag: int32

    def __init__(self, n: int32, tag: int32):
        super().__init__(n)
        self._tag = tag


def drain_sum(src: Source) -> int32:
    acc: int32 = 0
    for v in src:
        acc += v
    return acc


def main():
    print(drain_sum(Source(4, 9)))   # 3 + 2 + 1 + 0


main()
