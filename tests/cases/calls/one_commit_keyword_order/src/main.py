# Pins today's evaluation order of keyword arguments a construction
# reorders to parameter order (BUGS.md#subexpression-right-to-left-eval).
from tpy import copy, int32


class G[T]:
    a: T
    b: T
    c: T

    def __init__(self, a: T, b: T, c: T) -> None:
        self.a = copy(a)
        self.b = copy(b)
        self.c = copy(c)


def tick(n: int32) -> int32:
    print("tick", n)
    return n


def main() -> None:
    # Written c, a, b; CPython evaluates them in that order.
    g = G(c=tick(1), a=tick(2), b=tick(3))
    print("built:", g.a, g.b, g.c)


main()
