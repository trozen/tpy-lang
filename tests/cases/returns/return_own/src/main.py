from tpy import int32, Own, copy


class Point:
    x: int32
    y: int32


def create_point(x: int32, y: int32) -> Own[Point]:
    p: Point = Point()
    p.x = x
    p.y = y
    return copy(p)  # tpyc: warning(/unnecessary copy/)


def main():
    pt: Point = create_point(10, 20)
    print(pt.x)
    print(pt.y)


main()
