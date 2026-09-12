from tpy import int32, Ptr

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def make_ptr() -> Ptr[Point]:
    pt: Point = Point(10, 20)
    return pt  # tpyc: error(/Cannot return local or temporary/)
