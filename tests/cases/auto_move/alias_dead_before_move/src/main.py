# Alias dead before move -- auto-move still works (no regression).
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def main():
    p = Point()
    p.x = 42
    alias = p
    print(alias.x)
    print(consume(p))  # tpyc: ok -- alias dead, p at last use


main()
