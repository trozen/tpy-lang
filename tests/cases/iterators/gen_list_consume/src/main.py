# Generator consumed by list() builtin
from tpy import Int32
from typing import Iterator

def squares(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        yield i * i
        i += 1

def main():
    result = list(squares(5))
    print(result)

main()
