# Tuple literal assigned to self.field uses VALUE capture (owned copies).
from tpy import int32, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

class Container:
    data: tuple[Point, int32]
    def __init__(self, p: Point, n: int32) -> None:
        self.data = (copy(p), n)
    def __repr__(self) -> str:
        return "Container"

def main() -> None:
    p = Point(int32(1), int32(2))
    c = Container(p, int32(42))
    print(c.data[0].x, c.data[0].y, c.data[1])
    # Mutation of p should NOT affect c.data (value semantics for fields)
    p.x = int32(99)
    print(c.data[0].x)

main()
