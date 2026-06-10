from tpy import Int32, Own, copy


class Point:
    x: Int32
    y: Int32


def make_point() -> Own[Point]:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return copy(p)


def main():
    # Own[T] is not allowed for variable declarations
    p: Own[Point] = make_point()  # tpyc: error(/Own\[T\] is redundant in this variable type/)
    print(42)


main()
