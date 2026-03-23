# Error: yield in for-loop with multiple yield points (not yet supported)
from tpy import Int32
from typing import Iterator

def bad(items: list[Int32]) -> Iterator[Int32]:
    yield 0
    for x in items:
        yield x * 2  # tpyc: error(/yield inside for-loops is not yet supported/)

def main():
    pass

main()
