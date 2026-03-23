# Generator combining for...else with tuple unpacking
from tpy import Int32
from typing import Iterator

def gen(pairs: list[tuple[Int32, Int32]], limit: Int32) -> Iterator[Int32]:
    for a, b in pairs:
        if a + b >= limit:
            break
        yield a + b
    else:
        yield -1

def main():
    # No break: else runs
    print("no break:")
    for v in gen([(1, 2), (3, 4)], 100):
        print(v)
    # Break: else skipped
    print("break:")
    for v in gen([(1, 2), (3, 4)], 5):
        print(v)

main()
