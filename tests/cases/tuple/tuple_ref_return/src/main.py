# Return tuple[Point, bool] from function -- Point is returned by reference
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
    p = Point(int32(1), int32(2))
    result = find(p)
    print(result[0])
    print(result[1])
    # Mutation through reference is visible
    p.x = int32(42)
    print(result[0])

main()
