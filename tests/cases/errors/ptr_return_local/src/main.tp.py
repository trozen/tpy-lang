from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def make_ptr() -> Ptr[Point]:
    pt: Point = Point(10, 20)
    return pt  # tpyc: error(/Cannot return local variable as pointer/)
