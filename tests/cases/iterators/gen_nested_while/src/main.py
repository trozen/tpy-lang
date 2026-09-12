# Generator with yield in nested while-loops
from tpy import int32
from typing import Iterator

def matrix() -> Iterator[int32]:
    i: int32 = 0
    while i < 3:
        j: int32 = 0
        while j < 2:
            yield i * 10 + j
            j += 1
        i += 1

def main():
    for x in matrix():
        print(x)

main()
