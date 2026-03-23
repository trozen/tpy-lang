# Generator with bare return (early exit -> StopIteration)
from tpy import Int32
from typing import Iterator

def maybe_count(n: Int32) -> Iterator[Int32]:
    if n <= 0:
        return
    i: Int32 = 0
    while i < n:
        yield i
        i += 1

def main():
    for x in maybe_count(0):
        print(x)
    print("empty done")
    for x in maybe_count(3):
        print(x)

main()
