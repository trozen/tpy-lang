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

# Iterator stored in a list, accessed by subscript
items: list[Counter] = [Counter(3)]

print("first:")
for x in items[0]:
    print(x)

print("second:")
for x in items[0]:
    print(x)

print("done")
