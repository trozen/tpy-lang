# Assigning non-value type into tuple field without copy() warns.
from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Container:
    data: tuple[Point, Int32]
    def __init__(self, p: Point, n: Int32) -> None:
        self.data = (p, n)  # tpyc: warning(/copies Point into field/)
    def __repr__(self) -> str:
        return "Container"

class ContainerOk:
    data: tuple[Point, Int32]
    def __init__(self, p: Point, n: Int32) -> None:
        self.data = (copy(p), n)  # tpyc: ok
    def __repr__(self) -> str:
        return "ContainerOk"

def main() -> None:
    p = Point(Int32(1), Int32(2))
    c = ContainerOk(p, Int32(42))
    print(c.data[0].x, c.data[1])

main()
