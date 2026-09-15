# `yield <call>` where the callee already returns the yield slot's BORROW form
# (`tuple[int32, P]` renders `std::tuple<int32_t, P*>`): no storage lift is
# owed, so the call relays bare -- the same verdict `return <call>` already
# takes at the plain return. Each section MUTATES through the yielded element
# and reads the owning container afterwards, so a copy instead of an alias
# would show up.
from typing import Iterator

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(xs: list[P], i: int32) -> tuple[int32, P]:
    return (i, xs[i])


class Box:
    xs: list[P]

    def __init__(self) -> None:
        self.xs = [P(10), P(20)]

    def pair(self, i: int32) -> tuple[int32, P]:
        return (i, self.xs[i])

    # method generator: the borrow-form call is a METHOD call on self
    def walk(self) -> Iterator[tuple[int32, P]]:
        yield self.pair(0)  # tpyc: ok
        yield self.pair(1)  # tpyc: ok


# free generator: the borrow-form call is a same-module free call
def g(xs: list[P]) -> Iterator[tuple[int32, P]]:
    yield mk(xs, 0)  # tpyc: ok
    yield mk(xs, 1)  # tpyc: ok


def main() -> None:
    xs = [P(10), P(20)]
    for i, p in g(xs):
        p.x += 1
        print("g", i, p.x)
    print("g after", xs[0].x, xs[1].x)

    b = Box()
    for i, p in b.walk():
        p.x += 2
        print("walk", i, p.x)
    print("walk after", b.xs[0].x, b.xs[1].x)


main()
