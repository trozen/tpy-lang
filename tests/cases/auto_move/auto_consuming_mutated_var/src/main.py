# For-loop consuming triggers only when the loop var is mutated
# AND the element type is non-value (move != copy).
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test_readonly() -> None:
    items: list[Point] = [Point(1, 2), Point(3, 4)]
    # Loop var is read-only -- borrows even at last use
    for p in items:
        print(p.x, p.y)

def test_mutated() -> None:
    items: list[Point] = [Point(10, 20), Point(30, 40)]
    # Loop var is mutated + non-value element -- consumes
    for p in items:
        p.x = 0
        print(p.x, p.y)

def main() -> None:
    test_readonly()
    test_mutated()

main()
