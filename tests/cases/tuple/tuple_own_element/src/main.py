# Tuple with Own[T] wrapping for reference types
from tpy import Int32, Own, copy

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def make_pair(p: Point) -> tuple[Int32, Own[Point]]:
    return (Int32(42), copy(p))

def main() -> None:
    p = Point(Int32(1), Int32(2))
    t: tuple[Int32, Point] = (Int32(0), p)
    print(t[0])
    print(t[1])

    # From function return
    t2 = make_pair(Point(Int32(10), Int32(20)))
    print(t2[0])
    print(t2[1])

main()
