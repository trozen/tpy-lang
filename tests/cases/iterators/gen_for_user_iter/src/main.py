# Complex generator iterating over user type with __iter__()
from tpy import int32, Own
from typing import Iterator

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

class NumberRange:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

def doubled_range(r: NumberRange) -> Iterator[int32]:
    yield -1
    for x in r:
        yield x * 2

def main():
    nr = NumberRange(1, 5)
    for x in doubled_range(nr):
        print(x)

main()
