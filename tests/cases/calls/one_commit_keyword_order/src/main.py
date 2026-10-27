# Pins the render of keyword arguments a construction reorders to
# parameter order. The order they run in is the C++ compiler's
# (BUGS.md#subexpression-right-to-left-eval), so the generated C++ pins it,
# not the output.
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
    return n


def main() -> None:
    # Written c, a, b; the C++ passes them as a, b, c.
    g = G(c=tick(1), a=tick(2), b=tick(3))
    print("built:", g.a, g.b, g.c)


main()
