from tpy import Int32, ReadOnlyPtr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def print_point(p: Point) -> None:
    print(p.x)

def test() -> None:
    pt: Point = Point(10, 20)
    cptr: ReadOnlyPtr[Point] = pt
    print_point(cptr)  # tpyc: error(/Type mismatch.*expected Point, got ReadOnlyPtr/)
