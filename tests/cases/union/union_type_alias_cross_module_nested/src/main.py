# Test alias used in nested type annotations: list[Shape], Optional[Shape]
from tpy import Int32
from shapes import Circle, Rect, Shape


def main() -> None:
    xs: list[Shape] = []
    xs.append(Circle(Int32(1)))
    xs.append(Rect(Int32(2)))
    print(len(xs))

main()
