# Tuple with Own[T] wrapping for reference types
from tpy import int32, Own, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def make_pair(p: Point) -> tuple[int32, Own[Point]]:
    return (int32(42), copy(p))

def main() -> None:
    p = Point(int32(1), int32(2))
    t: tuple[int32, Point] = (int32(0), p)
    print(t[0])
    print(t[1])

    # From function return
    t2 = make_pair(Point(int32(10), int32(20)))
    print(t2[0])
    print(t2[1])

main()
