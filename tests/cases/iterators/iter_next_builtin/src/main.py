# Test next() builtin on user-defined and built-in iterators
from __future__ import annotations
from tpy import Int32, error_return
from typing import Iterator

class Counter:
    value: Int32
    limit: Int32
    def __init__(self, limit: Int32) -> None:
        self.value = 0
        self.limit = limit
    def __iter__(self) -> Counter:
        return self
    @error_return(StopIteration)
    def __next__(self) -> Int32:
        if self.value >= self.limit:
            raise StopIteration
        v = self.value
        self.value += 1
        return v

def consume_two(it: Iterator[Int32]) -> None:
    try:
        a = next(it)
        b = next(it)
        print(a)
        print(b)
    except StopIteration:
        print("stopped early")

def main() -> None:
    # next() with try/except
    c = Counter(3)
    it = iter(c)
    try:
        v = next(it)
        print(v)
        v = next(it)
        print(v)
        v = next(it)
        print(v)
        v = next(it)
        print(v)
    except StopIteration:
        print("done")

    # next() on protocol-typed Iterator[T] parameter
    c2 = Counter(5)
    consume_two(iter(c2))

main()
