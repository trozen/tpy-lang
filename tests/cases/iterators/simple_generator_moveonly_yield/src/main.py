# Regression: a simple (single-yield) generator yielding a move-only Own[T]
# (T is @nocopy). The peephole wrapped the yield value as
# std::optional<T>(__val) -- a copy -- so a move-only yield type (deleted
# copy ctor) failed the C++ build; it now moves the owned yield local out.
# Using @nocopy is deliberate: a silent copy at the yield boundary would be
# a compile error, not a parity-blind pass.
from typing import Iterator
from tpy import nocopy, Own, Int32


@nocopy
class Tok:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def toks_while(n: Int32) -> Iterator[Own[Tok]]:
    i: Int32 = 0
    while i < n:
        yield Tok(i * 2)
        i += 1


def toks_for(n: Int32) -> Iterator[Own[Tok]]:
    for i in range(n):
        yield Tok(i * 2)


def main() -> None:
    for t in toks_while(3):
        print(t.v)
    print("--")
    for t in toks_for(3):
        print(t.v)


main()
