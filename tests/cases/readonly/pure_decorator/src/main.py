# @pure decorator: marks functions/methods as having no side effects.
# Pure implies readonly -- pure methods can be called on readonly receivers.
from __future__ import annotations
from tpy import Int32, pure, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    @pure
    def magnitude_sq(self) -> Int32:
        return self.x * self.x + self.y * self.y

    @pure
    def distance_sq(self, other: Point) -> Int32:
        dx: Int32 = self.x - other.x
        dy: Int32 = self.y - other.y
        return dx * dx + dy * dy


@pure
def add_values(a: Int32, b: Int32) -> Int32:
    return a + b


@pure
def compute(p: Point) -> Int32:
    return p.magnitude_sq() + add_values(p.x, p.y)


def use_readonly(p: readonly[Point]) -> None:
    # Pure methods can be called on readonly receivers
    print(p.magnitude_sq())
    print(p.distance_sq(Point(Int32(0), Int32(0))))


def main() -> None:
    p = Point(Int32(3), Int32(4))
    print(p.magnitude_sq())
    print(p.distance_sq(Point(Int32(1), Int32(1))))
    print(add_values(Int32(10), Int32(20)))
    print(compute(p))
    use_readonly(p)


main()
