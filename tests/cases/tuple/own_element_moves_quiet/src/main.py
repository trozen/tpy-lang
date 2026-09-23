# The quiet side of a tuple element's Own[T] slot: a fresh member, an explicit
# copy() and an owned local at its last use move or copy with no warning.
from typing import Self
from tpy import int32, Own, copy


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Holder:
    a: P

    def __init__(self) -> None:
        self.a = P(3)

    # a consuming method moves its own field once
    def give(self: Own[Self]) -> int32:
        return take((self.a, P(4)))  # tpyc: ok


def take(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(100)
    return len(a.xs) * 10 + len(b.xs)


def ret_moves() -> tuple[Own[P], int32]:
    n = P(5)
    # an owned local at its last use moves into the returned element
    return (n, 1)  # tpyc: ok


def main() -> None:
    c = P(7)
    d = P(8)
    # a fresh member and an explicit copy() take no warning
    print("fresh_copy", take((P(7), copy(c))), len(c.xs))  # tpyc: ok
    # owned locals at their last use move in
    print("last_use", take((c, d)))  # tpyc: ok
    print("give", Holder().give())
    m, k = ret_moves()
    m.xs.append(6)
    print("ret_moves", len(m.xs), k)


main()
