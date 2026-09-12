from __future__ import annotations
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

# 1. Direct use in for-loop
for x in Counter(5):
    print(x)

# 2. Empty iterator
for x in Counter(0):
    print(x)
print("done")
