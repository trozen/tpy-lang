# Tuple literals with non-value types inside list literals should use value capture
from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y})"


def main() -> None:
    items: list[tuple[str, Point]] = [("a", Point(1, 2)), ("b", Point(3, 4))]
    for item in items:
        print(item)


main()
