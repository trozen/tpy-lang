# Complex generator iterating over user type with __iter__()
from tpy import Int32, Own
from typing import Iterator

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

class NumberRange:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)

def doubled_range(r: NumberRange) -> Iterator[Int32]:
    yield -1
    for x in r:
        yield x * 2

def main():
    nr = NumberRange(1, 5)
    for x in doubled_range(nr):
        print(x)

main()
