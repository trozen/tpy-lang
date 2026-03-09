# For-loop tuple unpacking where some elements are non-value (reference) types.
# The tuple alias must be non-const so that T& bindings work.
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
    # str + non-value: str becomes string_view, Point becomes T&
    items: list[tuple[str, Point]] = [("a", Point(1, 2)), ("b", Point(3, 4))]
    for name, pt in items:
        print(name, pt)

    # Expensive value type + non-value: BigInt becomes const T&, Point becomes T&
    pairs: list[tuple[int, Point]] = [(1, Point(10, 20)), (2, Point(30, 40))]
    for n, pt in pairs:
        print(n, pt)

    # All non-value: both elements become T&
    segments: list[tuple[Point, Point]] = [(Point(0, 0), Point(1, 1)), (Point(2, 2), Point(3, 3))]
    for a, b in segments:
        print(a, b)

main()
