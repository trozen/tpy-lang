# Regression: a simple for-generator over an Iterator[T] source (the
# direct-iterator path through _gen_simple_for_yield_body) yielding a
# move-only Own[@nocopy] -- the owned yield must be moved, not copied.
from typing import Iterator
from tpy import nocopy, Own, int32


@nocopy
class Tok:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def ints(n: int32) -> Iterator[int32]:
    i: int32 = 0
    while i < n:
        yield i
        i += 1


def make_toks(src: Iterator[int32]) -> Iterator[Own[Tok]]:
    for n in src:
        yield Tok(n * 10)


def main() -> None:
    for t in make_toks(ints(3)):
        print(t.v)


main()
