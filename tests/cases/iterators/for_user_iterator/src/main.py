# Tests user-defined iterator with __next__ and for-loop iteration
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

# 1. Direct use in for-loop (structural detection)
for x in Counter(5):
    print(x)

# 2. Pass to function taking Iterator[int32] (structural conformance)
def sum_iter(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

print(sum_iter(Counter(5)))

# 3. Empty iterator
for x in Counter(0):
    print(x)
print("done")
