# For-loop over T: Iterable[int32] inside a generic class method
from typing import Iterable
from tpy import int32, Own

class RangeIter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class MyRange:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

class Summer[T: Iterable[int32]]:
    items: T

    def __init__(self, items: T) -> None:
        self.items = items

    def total(self) -> int32:
        result: int32 = 0
        for x in self.items:
            result += x
        return result

def main() -> None:
    r = MyRange(1, 6)
    s = Summer(r)
    print(s.total())

main()
