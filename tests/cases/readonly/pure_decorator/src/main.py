# @pure decorator: marks functions/methods as having no side effects.
# Pure implies readonly -- pure methods can be called on readonly receivers.
from __future__ import annotations
from tpy import int32, pure, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    @pure
    def magnitude_sq(self) -> int32:
        return self.x * self.x + self.y * self.y

    @pure
    def distance_sq(self, other: Point) -> int32:
        dx: int32 = self.x - other.x
        dy: int32 = self.y - other.y
        return dx * dx + dy * dy


@pure
def add_values(a: int32, b: int32) -> int32:
    return a + b


@pure
def compute(p: Point) -> int32:
    return p.magnitude_sq() + add_values(p.x, p.y)


def use_readonly(p: readonly[Point]) -> None:
    # Pure methods can be called on readonly receivers
    print(p.magnitude_sq())
    print(p.distance_sq(Point(int32(0), int32(0))))


def main() -> None:
    p = Point(int32(3), int32(4))
    print(p.magnitude_sq())
    print(p.distance_sq(Point(int32(1), int32(1))))
    print(add_values(int32(10), int32(20)))
    print(compute(p))
    use_readonly(p)


main()
