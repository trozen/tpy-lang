# Assignment narrowing interacts correctly with isinstance branches
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

def describe(s: Circle | Rect) -> str:
    if isinstance(s, Circle):
        return "circle"
    else:
        return "rect"

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    print(c.radius)
    if isinstance(c, Circle):
        print("yes circle")
    else:
        print("no")
    print(describe(c))

main()
