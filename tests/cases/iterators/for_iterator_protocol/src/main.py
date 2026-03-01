# Function taking Iterator[Int32] parameter, for-loop over protocol-typed param
from __future__ import annotations
from typing import Iterator
from tpy import Int32

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

def sum_iter(it: Iterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

def main() -> None:
    print(sum_iter(Counter(5)))
    print(sum_iter(Counter(0)))

main()
