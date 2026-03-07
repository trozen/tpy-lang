# match/case subject narrowing: access fields on subject directly (no binding)
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

def describe(s: Circle | Rect) -> None:
    match s:
        case Circle():
            print(s.radius)
        case Rect():
            print(s.width * s.height)

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    r: Circle | Rect = Rect(3.0, 4.0)
    describe(c)
    describe(r)

main()
