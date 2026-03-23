# Generator function: while-loop with yield, consumed by for-loop
from tpy import Int32
from typing import Iterator

def count(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        yield i
        i += 1

def main():
    for x in count(5):
        print(x)

main()
