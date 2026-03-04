# Tuple literal assigned to self.field uses VALUE capture (owned copies).
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Container:
    data: tuple[Point, Int32]
    def __init__(self, p: Point, n: Int32) -> None:
        self.data = (p, n)
    def __repr__(self) -> str:
        return "Container"

def main() -> None:
    p = Point(Int32(1), Int32(2))
    c = Container(p, Int32(42))
    print(c.data[0].x, c.data[0].y, c.data[1])
    # Mutation of p should NOT affect c.data (value semantics for fields)
    p.x = Int32(99)
    print(c.data[0].x)

main()
