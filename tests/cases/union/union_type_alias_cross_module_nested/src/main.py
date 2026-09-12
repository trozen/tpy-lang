# Test alias used in nested type annotations: list[Shape], Optional[Shape]
from tpy import int32
from shapes import Circle, Rect, Shape


def main() -> None:
    xs: list[Shape] = []
    xs.append(Circle(int32(1)))
    xs.append(Rect(int32(2)))
    print(len(xs))

main()
