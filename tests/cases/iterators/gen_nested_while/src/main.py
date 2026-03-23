# Generator with yield in nested while-loops
from tpy import Int32
from typing import Iterator

def matrix() -> Iterator[Int32]:
    i: Int32 = 0
    while i < 3:
        j: Int32 = 0
        while j < 2:
            yield i * 10 + j
            j += 1
        i += 1

def main():
    for x in matrix():
        print(x)

main()
