from tpy import int32, Ptr, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def print_point(p: Point) -> None:
    print(p.x)

def test() -> None:
    pt: Point = Point(10, 20)
    cptr: Ptr[readonly[Point]] = pt
    print_point(cptr)  # tpyc: error(/Type mismatch.*expected Point, got Ptr\[readonly\[Point\]\]/)
