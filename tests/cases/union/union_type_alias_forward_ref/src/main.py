# Old-style type alias with forward references (alias before class definitions)
from tpy import int32

Shape = Circle | Rect

class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius


class Rect:
    width: int32

    def __init__(self, width: int32) -> None:
        self.width = width


def describe(s: Shape) -> str:
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    c: Shape = Circle(int32(10))
    r: Shape = Rect(int32(3))
    print(describe(c))
    print(describe(r))

main()
