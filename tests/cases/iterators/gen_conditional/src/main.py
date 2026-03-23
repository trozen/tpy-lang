# Generator with yield inside if/else branches
from tpy import Int32
from typing import Iterator

def evens(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        if i % 2 == 0:
            yield i
        i += 1

def main():
    for x in evens(10):
        print(x)

main()
