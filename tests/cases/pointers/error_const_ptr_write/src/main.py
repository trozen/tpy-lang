from tpy import int32, Ptr, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def try_write_const() -> None:
    pt: Point = Point(10, 20)
    cp: Ptr[readonly[Point]] = pt
    cp.x = 100  # tpyc: error(/Cannot assign through read-only pointer/)
