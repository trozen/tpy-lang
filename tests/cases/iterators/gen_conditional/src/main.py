# Generator with yield inside if/else branches
from tpy import int32
from typing import Iterator

def evens(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        if i % 2 == 0:
            yield i
        i += 1

def main():
    for x in evens(10):
        print(x)

main()
