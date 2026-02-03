from tpy import Int32, Own, copy


class Point:
    x: Int32
    y: Int32


def make_point() -> Own[Point]:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return copy(p)


def bad_wrapper() -> Point:
    return make_point()  # tpyc: error(/dangling|temporary/)


def main():
    pt: Point = bad_wrapper()
    print(pt.x)


main()
