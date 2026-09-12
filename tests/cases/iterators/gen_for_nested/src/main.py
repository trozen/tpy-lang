# Complex generator with nested for-loops containing yields
from tpy import int32
from typing import Iterator

def matrix(rows: list[int32], cols: list[int32]) -> Iterator[int32]:
    yield -1
    for r in rows:
        for c in cols:
            yield r * 10 + c

def main():
    for x in matrix([1, 2], [3, 4, 5]):
        print(x)

main()
