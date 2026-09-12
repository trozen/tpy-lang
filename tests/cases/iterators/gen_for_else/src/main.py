# Generator with for...else (break skips else, no-break runs else)
from tpy import int32
from typing import Iterator

def gen(items: list[int32], limit: int32) -> Iterator[int32]:
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
