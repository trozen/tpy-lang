# Mix of reference and owned elements in return tuple
from tpy import int32, Own, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def split(p: Point) -> tuple[Point, Own[Point]]:
    return (p, copy(p))

def main() -> None:
    p = Point(int32(1), int32(2))
    ref, owned = split(p)
    print(ref)
    print(owned)
    # Mutation visible through ref, not through owned copy
    p.x = int32(99)
    print(ref)
    print(owned)

main()
