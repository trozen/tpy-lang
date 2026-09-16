# The adjacent shape that must keep rejecting: a frame tuple unpack whose
# source is a CHAINED lvalue (`hs[0].pair` -- a field off a subscript). The
# frame alias lift reads the source's form off a single-hop storage lvalue
# (a subscript or a field of a declared name); a chained receiver is not that
# shape, so it rejects rather than falling back to the by-value holder it used
# to take -- that holder copied the `Box` element, and the copy then dangled
# past the yield (BUGS.md#frame-unpack-chained-receiver-rejected).
from typing import Iterator

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n * 2))


def gen(hs: list[Holder]) -> Iterator[int32]:
    a, b = hs[0].pair  # tpyc: error(/not yet supported by C\+\+ code generation \(res.unpack\)/)
    yield a
    yield b.n


def main() -> None:
    for v in gen([Holder(1)]):
        print(v)


main()
