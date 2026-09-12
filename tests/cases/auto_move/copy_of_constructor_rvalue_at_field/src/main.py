# `copy()` of a constructor prvalue written into a record field: the copy peels
# away and the constructor's own value is stored, so later writes to the source
# record are not visible through the field.
from tpy import int32, copy


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Holder:
    p: Point

    def __init__(self, p: Point) -> None:
        self.p = copy(p)


def use(h: Holder) -> None:
    h.p = copy(Point(3, 4))  # the copy of a ctor rvalue peels to the ctor


def main() -> None:
    src = Point(1, 2)
    h = Holder(src)
    src.x = 99  # the ctor copied, so the field is untouched
    print(h.p.x)
    use(h)
    print(h.p.x, h.p.y)


main()
