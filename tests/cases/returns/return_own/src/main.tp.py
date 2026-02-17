from tpy import Int32, Own, copy


class Point:
    x: Int32
    y: Int32


def create_point(x: Int32, y: Int32) -> Own[Point]:
    p: Point = Point()
    p.x = x
    p.y = y
    return copy(p)  # tpyc: warning(/unnecessary copy/)


def main():
    pt: Point = create_point(10, 20)
    print(pt.x)
    print(pt.y)


main()
