# Type alias with None member (optional union)
from tpy import Int32


class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius


class Rect:
    width: Int32

    def __init__(self, width: Int32) -> None:
        self.width = width


MaybeShape = Circle | Rect | None


def describe(s: MaybeShape) -> str:
    if s is None:
        return "nothing"
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    a: MaybeShape = Circle(Int32(1))
    b: MaybeShape = None
    print(describe(a))
    print(describe(b))

main()
