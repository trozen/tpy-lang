from tpy import Ptr, Int32, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test() -> None:
    p: Ptr[Point] = take_ptr(Point(1, 2))  # tpyc: error(/not a temporary or expression/)

test()
