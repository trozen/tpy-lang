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

# First loop consumes the iterator
c = Counter(3)
print("first:")
for x in c:
    print(x)

# Second loop over same iterator should produce nothing
print("second:")
for x in c:
    print(x)

print("done")
