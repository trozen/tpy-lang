# Generator with for...else (break skips else, no-break runs else)
from tpy import Int32
from typing import Iterator

def gen(items: list[Int32], limit: Int32) -> Iterator[Int32]:
    yield 999
    for x in items:
        if x >= limit:
            break
        yield x
    else:
        yield -1
    yield -2

def main():
    # No break: else block runs
    print("no break:")
    for v in gen([1, 2, 3], 10):
        print(v)
    # Break at 2: else block skipped
    print("break:")
    for v in gen([1, 2, 3], 2):
        print(v)

main()
