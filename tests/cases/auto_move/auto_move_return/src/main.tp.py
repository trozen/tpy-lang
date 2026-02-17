# Auto-move at return site: return local as Own[T] without copy()
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def make_point(x: Int32, y: Int32) -> Own[Point]:
    p = Point()
    p.x = x
    p.y = y
    return p  # last use of p -> auto-move (no copy needed)


def main():
    pt = make_point(10, 20)
    print(pt.x)
    print(pt.y)


main()
