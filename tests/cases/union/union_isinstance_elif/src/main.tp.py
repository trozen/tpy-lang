# isinstance elif chain with three-member union
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
    height: float
    def __init__(self, base: float, height: float) -> None:
        self.base = base
        self.height = height

def area(s: Circle | Rect | Triangle) -> float:
    if isinstance(s, Circle):
        return 3.14159 * s.radius * s.radius
    elif isinstance(s, Rect):
        return s.width * s.height
    else:
        return 0.5 * s.base * s.height

def main() -> None:
    c: Circle | Rect | Triangle = Circle(5.0)
    print(area(c))
    r: Circle | Rect | Triangle = Rect(3.0, 4.0)
    print(area(r))
    t: Circle | Rect | Triangle = Triangle(6.0, 8.0)
    print(area(t))

main()
