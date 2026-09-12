# Tests list() constructor from user-defined iterator
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
            val = self.current
            self.current += 1
            return val
        raise StopIteration

result = list(Counter(5))
print(result)
