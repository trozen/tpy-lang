# Generator consumed by list() builtin
from tpy import int32
from typing import Iterator

def squares(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i * i
        i += 1

def main():
    result = list(squares(5))
    print(result)

main()
