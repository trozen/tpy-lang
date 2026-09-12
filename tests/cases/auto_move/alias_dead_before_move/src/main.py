# Alias dead before move -- auto-move still works (no regression).
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def main():
    p = Point()
    p.x = 42
    alias = p
    print(alias.x)
    print(consume(p))  # tpyc: ok -- alias dead, p at last use


main()
