# Field init RHS that registers a codegen temp (e.g. varargs call) must be
# demoted from the member-initializer list to the constructor body. Otherwise
# the MIL would reference an undeclared temp, and the temp would leak into the
# next constructor body that triggers temps.flush.
import math


class Seg:
    length: float

    def __init__(self, x: int, y: int) -> None:
        self.length = math.hypot(float(x), float(y))


class Picture:
    width: int

    def __init__(self, w: int) -> None:
        self.width = w


class Triangle:
    a: float
    b: float
    label: str

    def __init__(self, p1x: int, p1y: int, p2x: int, p2y: int, name: str) -> None:
        self.a = math.hypot(float(p1x), float(p1y))
        self.label = name
        self.b = math.hypot(float(p2x), float(p2y))


def main() -> None:
    s = Seg(3, 4)
    p = Picture(7)
    t = Triangle(3, 4, 5, 12, "tri")
    print(s.length)
    print(p.width)
    print(t.a)
    print(t.b)
    print(t.label)


main()
