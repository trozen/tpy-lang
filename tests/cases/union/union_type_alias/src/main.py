# Type alias for union types using old-style assignment syntax
from tpy import int32


class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius


class Rect:
    width: int32
    height: int32

    def __init__(self, width: int32, height: int32) -> None:
        self.width = width
        self.height = height


Shape = Circle | Rect


def describe(s: Shape) -> str:
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    c: Shape = Circle(int32(10))
    r: Shape = Rect(int32(3), int32(4))
    print(describe(c))
    print(describe(r))

main()
