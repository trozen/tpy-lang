# Generator with tuple unpacking in for-loop: basic, wildcard _, multiple loops
from tpy import int32
from typing import Iterator

def sums(pairs: list[tuple[int32, int32]]) -> Iterator[int32]:
    yield -1
    for a, b in pairs:
        yield a + b

def firsts(pairs: list[tuple[int32, int32]]) -> Iterator[int32]:
    for x, _ in pairs:
        yield x

def multi(p1: list[tuple[int32, int32]], p2: list[tuple[int32, int32]]) -> Iterator[int32]:
    for a, b in p1:
        yield a + b
    for c, d in p2:
        yield c * d

def main():
    for v in sums([(1, 2), (3, 4), (5, 6)]):
        print(v)
    print("---")
    for v in firsts([(10, 20), (30, 40)]):
        print(v)
    print("---")
    for v in multi([(1, 2)], [(3, 4), (5, 6)]):
        print(v)

main()
