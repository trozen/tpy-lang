from __future__ import annotations
from typing import Protocol, Self
from tpy import Int32, Own

class Addable(Protocol):
    def __add__(self, other: Self) -> Own[Self]: ...

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Point) -> Own[Point]:
        return Point(self.x + other.x, self.y + other.y)

def add_points(a: Addable, b: Addable) -> None:
    # Verify that a + b compiles (the protocol constraint allows it)
    result = a + b

def main() -> None:
    p1 = Point(1, 2)
    p2 = Point(3, 4)
    # Call through protocol-typed parameter
    add_points(p1, p2)

    # Directly verify the addition works and print result
    p3 = p1 + p2
    print(p3.x)
    print(p3.y)

main()
