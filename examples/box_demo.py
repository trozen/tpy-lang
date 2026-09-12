# Box example: using tplib's heap-allocated owning container.
from dataclasses import dataclass
from tpy import int32, Own, dynamic
from tplib import Box
from typing import Protocol


@dataclass
class Point:
    x: int32
    y: int32


class Tag:
    _name: str

    def __init__(self, name: str) -> None:
        self._name = name

    def __str__(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"Tag('{self._name}')"


@dynamic
class Shape(Protocol):
    def area(self) -> float: ...


class Circle(Shape):
    _r: float

    def __init__(self, r: float) -> None:
        self._r = r

    def area(self) -> float:
        return 3.14 * self._r * self._r


class Square(Shape):
    _side: float

    def __init__(self, side: float) -> None:
        self._side = side

    def area(self) -> float:
        return self._side * self._side


def main() -> None:
    # --- Basic usage with int32 ---
    b = Box(int32(42))
    print(b)                          # Box(42)
    b.set(int32(100))
    print("after set:", b)            # Box(100)

    # --- Clone (Box is non-copyable, clone is explicit) ---
    c = b.clone()
    c.set(int32(999))
    print("original:", b)             # Box(100) (unchanged)
    print("clone:", c)                # Box(999)

    # --- take() consumes the box, returning the owned value ---
    val: int32 = c.take()
    print("taken:", val)              # 999
    # c is consumed here -- any further use would be a compile error

    # --- str vs repr ---
    bt = Box(Tag("hello"))
    print(str(bt))                    # Box(hello)
    print(repr(bt))                   # Box(Tag('hello'))

    # --- Box with a user-defined type ---
    bp = Box(Point(3, 4))
    print(bp)                         # Box(Point(x=3, y=4))

    pt: Point = bp.take()
    print("taken point:", pt.x, pt.y) # 3 4

    # --- Temporaries work too ---
    print("temp:", Box(int32(77)).take())  # 77

    # --- Covariant: Box[Circle] -> Box[Shape] ---
    print_area(Box(Circle(5.0)))      # 78.5
    print_area(Box(Square(4.0)))      # 16.0


def print_area(b: Box[Shape]) -> None:
    print("area:", b.get().area())


main()
