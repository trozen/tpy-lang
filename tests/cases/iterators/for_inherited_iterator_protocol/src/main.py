# Inherited __next__ satisfies Iterator[T] protocol for params and constructors
from __future__ import annotations
from typing import Iterator
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class DoubleCounter(Counter):
    def __init__(self, limit: int32) -> None:
        super().__init__(limit * 2)

class GrandChild(DoubleCounter):
    def __init__(self, limit: int32) -> None:
        super().__init__(limit)

def consume(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

def main() -> None:
    # Child passed to Iterator[T] param
    print(consume(DoubleCounter(3)))

    # Grandchild passed to Iterator[T] param
    print(consume(GrandChild(2)))

    # list() from inherited iterator
    print(list(DoubleCounter(2)))

    # set() from inherited iterator
    s = set(DoubleCounter(2))
    print(len(s))

main()
