# Callees and records the fresh-argument sections share.
from tpy import int32, Own


class Bag:
    def __init__(self, xs: list[int32]) -> None:
        # a field holds its own list: the copy of the caller's is warned
        self.xs = xs  # tpyc: warning(/copies list\[int32\] into field/)

    def sum_of(self, xs: list[int32]) -> int32:
        return total(xs)

    def grow_into(self, xs: list[int32]) -> int32:
        return grow(xs)


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[list[int32]]:
    return [1, 2, 3]


def mkp(n: int32) -> Own[P]:
    return P(n)


def total(xs: list[int32]) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def grow(xs: list[int32]) -> int32:
    xs.append(4)
    return len(xs)


def bump(p: P) -> int32:
    p.n += 1
    return p.n


def peek(p: P) -> int32:
    return p.n
