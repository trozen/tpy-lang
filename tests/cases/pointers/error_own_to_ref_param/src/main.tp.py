from tpy import Int32, Own, copy


class Point:
    x: Int32
    y: Int32


def make_point() -> Own[Point]:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return copy(p)


def use_point(p: Point) -> None:
    print(p.x)


def main():
    use_point(make_point())  # tpyc: error(/Cannot pass Own\[Point\]/)


main()
