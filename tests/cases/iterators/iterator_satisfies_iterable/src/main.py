# Iterator[T] structurally satisfies Iterable[T] (superset of methods).
# Verifies that iterators can be passed where iterables are expected.
from __future__ import annotations
from typing import Iterable
from tpy import int32

class Counter:
    i: int32
    n: int32
    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n
    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        val = self.i
        self.i += 1
        return val
    def __iter__(self) -> Counter:
        return self

def sum_iterable(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    # Iterator passed to Iterable-typed parameter (structural conformance)
    print(sum_iterable(Counter(5)))

    # Iterator passed to list() constructor (single Iterable[Own[T]] overload)
    a = list(Counter(4))
    print(a)

    # Iterator passed to set() constructor
    s = set(Counter(4))
    print(s)

main()
