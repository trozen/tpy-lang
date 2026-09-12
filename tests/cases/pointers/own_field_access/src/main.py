"""Tests field access on Own[T] types.

Own[T] wraps a type to indicate ownership transfer (return by value).
Field access on Own[T] should work in:
- Parameters with Own[T] type
- Direct access on function calls returning Own[T]
"""
from tpy import int32, Own


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


def make_point(x: int32, y: int32) -> Own[Point]:
    return Point(x, y)


def use_owned_point(p: Own[Point]) -> int32:
    # Field access on Own[T] parameter
    return p.x + p.y


def main():
    # Direct field access on function returning Own[T]
    print(make_point(10, 20).x)
    print(make_point(30, 40).y)

    # Field access through Own[T] parameter
    print(use_owned_point(make_point(50, 60)))

    # Chained: return value assigned to Point, then accessed
    pt: Point = make_point(70, 80)
    print(pt.x)
    print(pt.y)


main()
