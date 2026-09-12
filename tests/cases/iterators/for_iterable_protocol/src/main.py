# Function taking Iterable[int32] parameter, for-loop over protocol-typed param
from typing import Iterable
from tpy import int32, Own

class CounterIter:
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

class Counter:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)

def sum_items(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    c = Counter(0, 5)
    print(sum_items(c))

    c2 = Counter(10, 15)
    print(sum_items(c2))

main()
