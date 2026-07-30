# Default parameter values on a @classmethod: `cls` occupies the implicit
# receiver slot, so the defaults must still align with the parameters after it.
from typing import Self

from tpy import Int32, Own


class Point:
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    @classmethod
    def make(cls, x: Int32, y: Int32 = 9) -> Own[Self]:
        return cls(x, y)

    @classmethod
    def blank(cls, x: Int32 = 1, y: Int32 = 2) -> Own[Self]:
        return cls(x, y)


def main() -> None:
    a = Point.make(1)
    b = Point.make(1, 5)
    c = Point.blank()
    d = Point.blank(7)
    print(a.x, a.y)
    print(b.x, b.y)
    print(c.x, c.y)
    print(d.x, d.y)


main()
