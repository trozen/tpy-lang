# Complex generator with break and continue in for-loop
from tpy import Int32
from typing import Iterator

def filtered(items: list[Int32], limit: Int32) -> Iterator[Int32]:
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
