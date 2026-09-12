# Generator with bare return (early exit -> StopIteration)
from tpy import int32
from typing import Iterator

def maybe_count(n: int32) -> Iterator[int32]:
    if n <= 0:
        return
    i: int32 = 0
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
