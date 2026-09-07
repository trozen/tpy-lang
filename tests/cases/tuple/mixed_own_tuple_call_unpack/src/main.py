# Unpacking a call returning a tuple that mixes a borrowed record, an owned
# copy and plain scalars: mutating the original shows through the borrow only.
from tpy import Int32, Own, copy


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def split(p: Point) -> tuple[Point, Own[Point]]:
    return (p, copy(p))


def split3(p: Point) -> tuple[Point, Own[Point], Int32, str]:
    return (p, copy(p), 7, "hi")


def main() -> None:
    p = Point(1)
    # The borrow element aliases `p`; the Own element is its own copy. Value
    # scalars and strs ride alongside unchanged.
    ref, owned, n, s = split3(p)
    p.x = 9
    print(ref.x, owned.x, n, s)


main()
