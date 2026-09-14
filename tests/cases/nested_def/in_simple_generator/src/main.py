# A single-yield generator containing a nested def that its loop body calls:
# the closure has to be visible from inside the frame.
from typing import Iterator
from tpy import int32


def gen(k: int32) -> Iterator[int32]:
    base = 100

    def scale(x: int32) -> int32:
        return x + base

    for i in range(k):
        yield scale(i)


def main() -> None:
    for v in gen(3):
        print(v)


main()
