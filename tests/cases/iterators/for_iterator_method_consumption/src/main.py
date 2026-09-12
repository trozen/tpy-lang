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

class Box:
    it: Counter

    def __init__(self) -> None:
        self.it = Counter(3)

    def get_it(self) -> Counter:
        return self.it

# Method call returns by reference — consumption must be preserved
b = Box()
print("first:")
for x in b.get_it():
    print(x)

print("second:")
for x in b.get_it():
    print(x)

print("done")
