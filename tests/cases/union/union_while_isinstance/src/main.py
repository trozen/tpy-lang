# while isinstance() condition narrows inside loop body
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

def drain_circles(s: Circle | Rect) -> None:
    count: int32 = 0
    while isinstance(s, Circle):
        print(s.radius)
        count += 1
        if count >= 3:
            break

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    drain_circles(c)
    r: Circle | Rect = Rect(3.0, 4.0)
    drain_circles(r)

main()
