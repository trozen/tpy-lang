# Generator method: complex path (multiple yields, struct codegen)
from typing import Iterator
from tpy import Int32

class Range:
    start: Int32
    stop: Int32
    def __init__(self, start: Int32, stop: Int32) -> None:
        self.start = start
        self.stop = stop

    def total(self) -> Int32:
        return self.stop - self.start

    def __iter__(self) -> Iterator[Int32]:
        yield -1
        i = self.start
        while i < self.stop:
            yield i
            i += 1

    def pairs(self) -> Iterator[Int32]:
        i = self.start
        while i < self.stop:
            yield i * 10
            yield i * 10 + 1
            i += 1

def main() -> None:
    r = Range(3, 6)
    print(r.total())
    for x in r:
        print(x)
    for p in r.pairs():
        print(p)

main()
