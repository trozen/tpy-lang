# A generator binding `a` first as a for-loop variable and later as a
# tuple-unpack target: the loop variable is shadowed inside the frame.
from typing import Iterator
from tpy import Int32


def gen(xs: list[Int32]) -> Iterator[Int32]:
    yield 0
    total = 0
    # `a` is rebound below, so the loop variable cannot own a frame slot.
    for a in xs:  # tpyc: error(/stmt\.for_each:foreach\.var_shadow/)
        total += a
    a, n = (5, 6)
    yield total + a + n


def main() -> None:
    for v in gen([1, 2]):
        print(v)


main()
