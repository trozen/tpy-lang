# Generator method: simple path (single yield in loop, lambda codegen)
from typing import Iterator
from tpy import Int32

class Counter:
    limit: Int32
    def __init__(self, limit: Int32) -> None:
        self.limit = limit

    def __iter__(self) -> Iterator[Int32]:
        i: Int32 = 0
        while i < self.limit:
            yield i
            i += 1

def main() -> None:
    c = Counter(5)
    for x in c:
        print(x)

main()
