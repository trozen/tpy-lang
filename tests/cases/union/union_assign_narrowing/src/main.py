# Assignment narrowing: union var initialized with concrete type narrows without isinstance
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

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(c.radius)
    r: Circle | Rect = Rect(3.0, 4.0)
    print(r.width)
    print(r.height)

main()
