# Unpack tuple with reference element -- unpacked variable is a reference
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def find(p: Point) -> tuple[Point, bool]:
    return (p, True)

def main() -> None:
    p = Point(Int32(10), Int32(20))
    pt, found = find(p)
    print(pt)
    print(found)
    # Mutation through reference
    p.x = Int32(99)
    print(pt)

main()
