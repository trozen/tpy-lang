# Tests inherited __next__ from parent class
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

class DoubleCounter(Counter):
    def __init__(self, limit: int32) -> None:
        super().__init__(limit * 2)

# Multi-level: GrandChild -> DoubleCounter -> Counter
class GrandChild(DoubleCounter):
    def __init__(self, limit: int32) -> None:
        super().__init__(limit)

# 1. for-loop over child inheriting __next__() from parent
for x in DoubleCounter(3):
    print(x)

# 2. for-loop over multi-level child
for x in GrandChild(2):
    print(x)

# 3. Verify output matches
print("done")
