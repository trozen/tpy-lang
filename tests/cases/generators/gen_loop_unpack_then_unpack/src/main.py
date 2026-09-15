# A generator whose for-loop unpack targets are bound again by a later tuple
# unpack: the for head ASSIGNS the shared frame slots rather than declaring
# loop-scoped ones, so the post-loop unpack writes the same names.
from typing import Iterator
from tpy import int32


def gen(items: list[tuple[int32, int32]]) -> Iterator[int32]:
    yield 0
    total = 0
    # `a` / `n` are unpack targets here and again after the loop.
    for a, n in items:  # tpyc: ok
        total += a + n
    a, n = (5, 6)
    yield total + a + n


def main() -> None:
    for v in gen([(1, 2), (3, 4)]):
        print(v)


main()
