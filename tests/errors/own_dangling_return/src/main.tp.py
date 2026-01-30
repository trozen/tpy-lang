from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def make_point() -> Own[Point]:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return p


def bad_wrapper() -> Point:
    return make_point()  # tpyc: error(/dangling|temporary/)


def main():
    pt: Point = bad_wrapper()
    print(pt.x)


main()
