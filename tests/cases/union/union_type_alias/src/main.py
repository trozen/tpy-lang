# Type alias for union types using old-style assignment syntax
from tpy import Int32


class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius


class Rect:
    width: Int32
    height: Int32

    def __init__(self, width: Int32, height: Int32) -> None:
        self.width = width
        self.height = height


Shape = Circle | Rect


def describe(s: Shape) -> str:
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    c: Shape = Circle(Int32(10))
    r: Shape = Rect(Int32(3), Int32(4))
    print(describe(c))
    print(describe(r))

main()
