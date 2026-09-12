# while loop with elif isinstance chain inside body
from tpy import int32

class Circle:
    radius: float

    def __init__(self, radius: float) -> None:
        self.radius = radius

class Rect:
    width: float
    height: float

    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height

class Triangle:
    base: float

    def __init__(self, base: float) -> None:
        self.base = base

def describe(s: Circle | Rect | Triangle) -> None:
    i: int32 = 0
    while i < 2:
        if isinstance(s, Circle):
            print(s.radius)
        elif isinstance(s, Rect):
            print(s.width)
        else:
            print(s.base)
        i += 1

def main() -> None:
    c: Circle | Rect | Triangle = Circle(1.0)
    describe(c)
    r: Circle | Rect | Triangle = Rect(2.0, 3.0)
    describe(r)
    t: Circle | Rect | Triangle = Triangle(4.0)
    describe(t)

main()
