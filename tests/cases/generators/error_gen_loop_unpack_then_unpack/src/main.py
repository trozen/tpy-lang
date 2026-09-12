# A generator whose for-loop unpack targets are reused by a later tuple
# unpack: the second binding makes the unpack targets non-exclusive.
from typing import Iterator
from tpy import int32


def gen(items: list[tuple[int32, int32]]) -> Iterator[int32]:
    yield 0
    total = 0
    # `a` / `n` are unpack targets here and again after the loop.
    for a, n in items:  # tpyc: error(/stmt\.for_each:tuple\.reused_target/)
        total += a + n
    a, n = (5, 6)
    yield total + a + n


def main() -> None:
    for v in gen([(1, 2), (3, 4)]):
        print(v)


main()
