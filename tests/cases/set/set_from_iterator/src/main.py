# Tests set() constructor from user-defined iterator
from __future__ import annotations
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
            val = self.current
            self.current += 1
            return val
        raise StopIteration

def main() -> None:
    s = set(Counter(5))
    print(s)
    print(len(s))

main()
