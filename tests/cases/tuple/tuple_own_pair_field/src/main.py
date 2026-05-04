# Field of type `tuple[T, T]` initialized via `self.f = (a, b)`.
# Last-use owned locals auto-move into the field tuple, matching scalar
# `self.f = local` semantics. Exercises the assignment-path call to
# `own_tuple_target()` (third call site in sema/statements.py).
from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return "P(" + str(self.x) + "," + str(self.y) + ")"


class Pair:
    points: tuple[Point, Point]

    def __init__(self) -> None:
        a = Point(Int32(1), Int32(2))
        b = Point(Int32(3), Int32(4))
        self.points = (a, b)


def main() -> None:
    p = Pair()
    print(p.points[0])
    print(p.points[1])


main()
