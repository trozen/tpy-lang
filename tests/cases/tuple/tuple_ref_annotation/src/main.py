# Tuple type annotation with reference type -- reference return semantics
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def make(p: Point) -> tuple[int32, Point]:
    return (int32(1), p)

def main() -> None:
    p = Point(int32(10), int32(20))
    t = make(p)
    print(t[0])
    print(t[1])
    # Mutation through reference is visible
    p.x = int32(99)
    print(t[1])

main()
