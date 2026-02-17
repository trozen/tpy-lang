# Auto-move at last use: lvalue passed to Own[T] param is auto-moved
# when the variable is not used after the call.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x + p.y


def main():
    p = Point()
    p.x = 10
    p.y = 32
    # p is at last use here -- auto-move, no copy() needed
    result = consume(p)
    print(result)


main()
