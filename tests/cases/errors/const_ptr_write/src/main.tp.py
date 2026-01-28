from tpy import Int32, ConstPtr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def try_write_const() -> None:
    pt: Point = Point(10, 20)
    cp: ConstPtr[Point] = pt
    cp.x = 100  # tpyc: error(/Cannot assign through ConstPtr/)
