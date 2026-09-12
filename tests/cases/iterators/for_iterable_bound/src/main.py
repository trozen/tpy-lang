# Bounded type param T: Iterable[int32], for-loop over T-typed param
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

def sum_all[T: Iterable[int32]](items: T) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    r = MyRange(1, 6)
    print(sum_all(r))

main()
