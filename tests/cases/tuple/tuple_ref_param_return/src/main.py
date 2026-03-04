# Tuple return with reference element: mutable param -> T& in tuple
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def make_pair(n: Int32, p: Point) -> tuple[Int32, Point]:
    return (n, p)

def main() -> None:
    p = Point(Int32(1), Int32(2))
    pair = make_pair(Int32(42), p)
    print(pair[0])
    print(pair[1].x)
    print(pair[1].y)
    # Mutation through p is visible via pair[1] (reference semantics)
    p.x = Int32(99)
    print(pair[1].x)

main()
