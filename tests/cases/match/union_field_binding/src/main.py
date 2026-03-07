# match/case with keyword field bindings on a 2-member union
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
        case Circle(radius=r):
            print(r)
        case Rect(width=w, height=h):
            print(w * h)

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    describe(c)
    r: Circle | Rect = Rect(3.0, 4.0)
    describe(r)

main()
