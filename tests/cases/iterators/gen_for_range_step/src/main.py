# Complex generator with range(start, stop, step) for-loop
from tpy import int32
from typing import Iterator

def countdown(start: int32) -> Iterator[int32]:
    yield 999
    for i in range(start, 0, -1):
        yield i

def main():
    for x in countdown(5):
        print(x)

main()
