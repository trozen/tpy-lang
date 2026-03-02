# Box[Child] -> Box[Parent] covariant coercion via Covariant[T] marker.
# Tests: function arg, variable assignment, return value.
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box

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
    _s: float
    def __init__(self, s: float) -> None:
        self._s = s
    def area(self) -> float:
        return self._s * self._s

def print_area(b: Box[Shape]) -> None:
    print(b.get().area())

def make_shape() -> Own[Box[Shape]]:
    return Box(Circle(3.0))

def main() -> None:
    # Function arg coercion
    bc = Box(Circle(5.0))
    print_area(bc)

    bs = Box(Square(3.0))
    print_area(bs)

    # Variable assignment coercion
    bc2 = Box(Circle(2.0))
    b_shape: Box[Shape] = bc2
    print(b_shape.get().area())

    # Return coercion via Own
    b3 = make_shape()
    print(b3.get().area())

main()
