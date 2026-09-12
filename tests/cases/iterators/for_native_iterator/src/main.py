# Tests passing user-defined iterators to Iterator[T] protocol params
from __future__ import annotations
from typing import Iterator
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> int32:
        if self.current < self.limit:
            val = self.current
            self.current += 1
            return val
        raise StopIteration

def sum_iter(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

def count_iter(it: Iterator[int32]) -> int32:
    n: int32 = 0
    for x in it:
        n += 1
    return n

def first_or_fallback(it: Iterator[int32], fallback: int32) -> int32:
    for x in it:
        return x
    return fallback

# Pass Counter objects (which satisfy Iterator[int32])
print(sum_iter(Counter(0, 5)))          # 0+1+2+3+4 = 10
print(sum_iter(Counter(1, 6)))          # 1+2+3+4+5 = 15

print(count_iter(Counter(0, 7)))        # 7
print(count_iter(Counter(0, 0)))        # 0 (empty iterator)

print(first_or_fallback(Counter(0, 3), -1))   # 0
print(first_or_fallback(Counter(0, 0), -1))   # -1 (empty, returns fallback)
