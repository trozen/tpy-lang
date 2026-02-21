# Reassigned union var loses assignment narrowing, requires isinstance
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

def check(s: Circle | Rect) -> None:
    if isinstance(s, Circle):
        print(s.radius)
    else:
        print(s.width)

def main() -> None:
    s: Circle | Rect = Circle(1.0)
    print(s.radius)
    s = Rect(3.0, 4.0)
    if isinstance(s, Rect):
        print(s.width)

main()
