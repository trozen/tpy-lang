# Old-style type alias with forward references (alias before class definitions)
from tpy import Int32

Shape = Circle | Rect

class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius


class Rect:
    width: Int32

    def __init__(self, width: Int32) -> None:
        self.width = width


def describe(s: Shape) -> str:
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    c: Shape = Circle(Int32(10))
    r: Shape = Rect(Int32(3))
    print(describe(c))
    print(describe(r))

main()
