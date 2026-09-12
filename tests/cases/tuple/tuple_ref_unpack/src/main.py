# Unpack tuple with reference element -- unpacked variable is a reference
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def find(p: Point) -> tuple[Point, bool]:
    return (p, True)

def main() -> None:
    p = Point(int32(10), int32(20))
    pt, found = find(p)
    print(pt)
    print(found)
    # Mutation through reference
    p.x = int32(99)
    print(pt)

main()
