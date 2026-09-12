# A fresh Own[T] rvalue (here a plain record from an Own-returning factory)
# lent to a borrow param: codegen materializes a named temp for the call.
from tpy import int32, Own, copy


class Point:
    x: int32
    y: int32


def make_point() -> Own[Point]:
    p = Point()
    p.x = 10
    p.y = 20
    return copy(p)


def use_point(p: Point) -> None:
    print(p.x)


def main():
    use_point(make_point())  # tpyc: ok


main()
