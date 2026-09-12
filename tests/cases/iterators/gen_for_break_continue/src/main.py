# Complex generator with break and continue in for-loop
from tpy import int32
from typing import Iterator

def filtered(items: list[int32], limit: int32) -> Iterator[int32]:
    yield -1
    for x in items:
        if x < 0:
            continue
        if x >= limit:
            break
        yield x * 2

def main():
    for v in filtered([3, -1, 5, 7, 2, 10, 1], 8):
        print(v)

main()
