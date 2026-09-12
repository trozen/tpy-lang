# Function taking Iterator[int32] parameter, for-loop over protocol-typed param
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

def sum_iter(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

def main() -> None:
    print(sum_iter(Counter(5)))
    print(sum_iter(Counter(0)))

main()
