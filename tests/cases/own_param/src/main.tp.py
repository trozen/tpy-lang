from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def make_point(x: Int32, y: Int32) -> Own[Point]:
    p: Point = Point()
    p.x = x
    p.y = y
    return p


def take_point(p: Own[Point]) -> Int32:
    # For now, just return a constant since field access on Own[T] isn't implemented yet
    return 99


def main():
    # Pass Own[Point] directly to Own[Point] param - should work
    result: Int32 = take_point(make_point(10, 20))
    print(result)


main()
