# Complex generator: yield before + yield inside for-loop over container
from tpy import int32
from typing import Iterator

def doubled(items: list[int32]) -> Iterator[int32]:
    yield 0
    for x in items:
        yield x * 2

def main():
    for x in doubled([1, 2, 3]):
        print(x)

main()
