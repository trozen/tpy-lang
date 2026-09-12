# Complex generator: multiple for-loops (container + range) with yields between
from tpy import int32
from typing import Iterator

def multi(items: list[int32], n: int32) -> Iterator[int32]:
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
