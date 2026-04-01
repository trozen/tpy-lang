# Ref[T] from function return types must be stripped when inferring container
# constructor type args. Otherwise we get e.g. list[Ref[Point]] which maps to
# invalid C++ (std::vector<Point&>).
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

def identity(p: Point) -> Point:
    return p

def to_pair(p: Point) -> tuple[str, Point]:
    return (str(p), p)

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]

    # list(map(...)) -- Ref[Point] from identity return must not leak into list[T]
    result = list(map(identity, pts))  # tpyc: type(list[Point]) warning(/copies Point elements/)
    for p in result:
        print(p)

    # dict from map -- Ref must be stripped from both K and V
    d = dict(map(to_pair, pts))  # tpyc: type(dict[str, Point])
    for k in d:
        print(k, d[k])

main()
