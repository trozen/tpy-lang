# An ASSIGN-narrowed union local as a METHOD receiver: the read is the active
# member's inline get, so both the direct and the delegating method see the
# Circle the local was assigned.
from tpy import Float64, Int32


class Circle:
    r: Float64

    def __init__(self, r: Float64) -> None:
        self.r = r

    def area(self) -> Float64:
        return 3.14 * self.r * self.r

    def scaled(self, k: Int32) -> Float64:
        return self.area() * k


class Rect:
    w: Float64
    h: Float64

    def __init__(self, w: Float64, h: Float64) -> None:
        self.w = w
        self.h = h

    def area(self) -> Float64:
        return self.w * self.h


def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(c.area())  # the receiver is the assign-narrowed member
    print(c.scaled(2))


main()
