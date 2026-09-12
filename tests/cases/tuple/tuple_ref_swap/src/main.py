# Tuple swap with non-value element: tuple params are mutable, so p[0] is T&
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def swap(p: tuple[Point, int32]) -> tuple[int32, Point]:
    return (p[1], p[0])

def main() -> None:
    p = Point(int32(1), int32(2))
    t = (p, int32(10))
    result = swap(t)
    print(result[0])
    print(result[1].x)
    print(result[1].y)

main()
