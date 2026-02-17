from tpy import Int32, Own, copy


class Point:
    x: Int32
    y: Int32


def make_point(x: Int32, y: Int32) -> Own[Point]:
    p: Point = Point()
    p.x = x
    p.y = y
    return copy(p)  # tpyc: warning(/unnecessary copy/)


def take_point(p: Own[Point]) -> Int32:
    # Field access on Own[T] should work - unwraps to the underlying type
    return p.x + p.y


def main():
    # Pass Own[Point] directly to Own[Point] param - should work
    result: Int32 = take_point(make_point(10, 20))
    print(result)


main()
