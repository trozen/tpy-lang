# Complex generator: multiple for-loops (container + range) with yields between
from tpy import Int32
from typing import Iterator

def multi(items: list[Int32], n: Int32) -> Iterator[Int32]:
    yield -1
    for x in items:
        yield x * 10
    yield -2
    for i in range(n):
        yield i * i
    yield -3

def main():
    for x in multi([1, 2, 3], 4):
        print(x)

main()
