# Return tuple[Point, bool] from function -- Point is returned by reference
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
    p = Point(Int32(1), Int32(2))
    result = find(p)
    print(result[0])
    print(result[1])
    # Mutation through reference is visible
    p.x = Int32(42)
    print(result[0])

main()
