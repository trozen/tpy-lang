# @overload dispatch with match/case dead branch elimination
from typing import overload

class Circle:
    radius: float
    def __init__(self, radius: float) -> None:
        self.radius = radius

class Square:
    side: float
    def __init__(self, side: float) -> None:
        self.side = side

@overload
def area(shape: Circle) -> float: ...

@overload
def area(shape: Square) -> float: ...

def area(shape: Circle | Square) -> float:
    match shape:
        case Circle(radius=r):
            return 3.14 * r * r
        case Square(side=s):
            return s * s

def main() -> None:
    c = Circle(5.0)
    s = Square(3.0)
    print(area(c))
    print(area(s))

main()
