# Tuple swap with non-value element: tuple params are mutable, so p[0] is T&
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def swap(p: tuple[Point, Int32]) -> tuple[Int32, Point]:
    return (p[1], p[0])

def main() -> None:
    p = Point(Int32(1), Int32(2))
    t = (p, Int32(10))
    result = swap(t)
    print(result[0])
    print(result[1].x)
    print(result[1].y)

main()
