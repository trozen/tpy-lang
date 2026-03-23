# Complex generator with range(start, stop, step) for-loop
from tpy import Int32
from typing import Iterator

def countdown(start: Int32) -> Iterator[Int32]:
    yield 999
    for i in range(start, 0, -1):
        yield i

def main():
    for x in countdown(5):
        print(x)

main()
