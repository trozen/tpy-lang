# isinstance narrowing on two-member union with field access
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

def area(s: Circle | Rect) -> float:
    if isinstance(s, Circle):
        return 3.14159 * s.radius * s.radius
    else:
        return s.width * s.height

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(area(c))
    r: Circle | Rect = Rect(3.0, 4.0)
    print(area(r))

main()
