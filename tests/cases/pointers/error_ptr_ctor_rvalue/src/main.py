from tpy import Ptr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test() -> None:
    p: Ptr[Point] = Ptr(Point(1, 2))  # tpyc: error(/must be a mutable lvalue/)

test()
