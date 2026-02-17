# Auto-move for per-iteration variable in loop: variable is created
# and consumed each iteration, so each use is a last use.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def main():
    for i in range(3):
        p = Point()
        p.x = i
        p.y = 0
        # p is last use per iteration -- auto-move
        print(consume(p))


main()
