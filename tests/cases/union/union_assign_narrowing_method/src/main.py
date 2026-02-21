# Assignment narrowing works for method calls on narrowed union vars
class Circle:
    radius: float

    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        return 3.14 * self.radius * self.radius

class Rect:
    width: float
    height: float

    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height

    def area(self) -> float:
        return self.width * self.height

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(c.area())
    r: Circle | Rect = Rect(3.0, 4.0)
    print(r.area())

main()
