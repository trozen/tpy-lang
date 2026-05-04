# Form B equivalent of error_tuple_own_no_copy: returning a borrowed
# lvalue as an element of an `Own[tuple[T, T]]` requires explicit copy().
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def split(p: Point) -> Own[tuple[Point, Point]]:
    return (p, p)  # tpyc: error(/explicit copy/)


def main() -> None:
    pass


main()
