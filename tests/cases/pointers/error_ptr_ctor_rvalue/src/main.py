from tpy import Ptr, int32, take_ptr

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test() -> None:
    p: Ptr[Point] = take_ptr(Point(1, 2))  # tpyc: error(/not a temporary or expression/)

test()
