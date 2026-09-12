# Assigning non-value type into tuple field without copy() warns.
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
        self.data = (p, n)  # tpyc: warning(/copies Point into field/)
    def __repr__(self) -> str:
        return "Container"

class ContainerOk:
    data: tuple[Point, int32]
    def __init__(self, p: Point, n: int32) -> None:
        self.data = (copy(p), n)  # tpyc: ok
    def __repr__(self) -> str:
        return "ContainerOk"

def main() -> None:
    p = Point(int32(1), int32(2))
    c = ContainerOk(p, int32(42))
    print(c.data[0].x, c.data[1])

main()
