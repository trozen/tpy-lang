# Tuple return with reference element: mutable param -> T& in tuple
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def make_pair(n: int32, p: Point) -> tuple[int32, Point]:
    return (n, p)

def main() -> None:
    p = Point(int32(1), int32(2))
    pair = make_pair(int32(42), p)
    print(pair[0])
    print(pair[1].x)
    print(pair[1].y)
    # Mutation through p is visible via pair[1] (reference semantics)
    p.x = int32(99)
    print(pair[1].x)

main()
