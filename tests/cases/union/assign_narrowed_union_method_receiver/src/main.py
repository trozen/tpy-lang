# An ASSIGN-narrowed union local as a METHOD receiver: the read is the active
# member's inline get, so both the direct and the delegating method see the
# Circle the local was assigned.
from tpy import float64, int32


class Circle:
    r: float64

    def __init__(self, r: float64) -> None:
        self.r = r

    def area(self) -> float64:
        return 3.14 * self.r * self.r

    def scaled(self, k: int32) -> float64:
        return self.area() * k


class Rect:
    w: float64
    h: float64

    def __init__(self, w: float64, h: float64) -> None:
        self.w = w
        self.h = h

    def area(self) -> float64:
        return self.w * self.h


def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(c.area())  # the receiver is the assign-narrowed member
    print(c.scaled(2))


main()
