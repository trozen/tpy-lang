# Bounded type param T: Iterable[Int32], for-loop over T-typed param
from typing import Iterable
from tpy import Int32, Own

class RangeIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class MyRange:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

def sum_all[T: Iterable[Int32]](items: T) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    r = MyRange(1, 6)
    print(sum_all(r))

main()
