from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def cond_record(cond: bool) -> None:
    if cond:
        x: Point = Point(1, 2)
    print(x)  # tpyc: error(/may not be assigned at this point/)
