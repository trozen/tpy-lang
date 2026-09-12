# Generator method on a generic class with a BOUNDED type param. The
# out-of-class factory definition has to spell the same constraint as the
# in-class declaration (`Iterable<int32> T`, not bare `typename T`), and
# the body's for-loop has to see the bound to pick the iteration strategy.
from typing import Iterable, Iterator
from tpy import int32, Own


class RangeIter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            r = self.current
            self.current += 1
            return r
        raise StopIteration


class MyRange:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)


class Summer[T: Iterable[int32]]:
    items: T

    def __init__(self, items: T) -> None:
        self.items = items

    def each_doubled(self) -> Iterator[int32]:
        for x in self.items:
            yield x
            yield x * 2


def main() -> None:
    r = MyRange(1, 4)
    s = Summer(r)
    for v in s.each_doubled():
        print(v)


main()
