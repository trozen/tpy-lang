# Test importing a union type alias from another module
from tpy import int32
from shapes import Circle, Rect, Shape


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
