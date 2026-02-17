# Auto-move in branches: both if/else paths are last use.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x + p.y


def main():
    p = Point()
    p.x = 10
    p.y = 20
    cond = True
    if cond:
        # last use on this path
        print(consume(p))
    else:
        # last use on this path
        print(consume(p))


main()
