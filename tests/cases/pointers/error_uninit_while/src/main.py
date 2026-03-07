from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def while_record(cond: bool) -> None:
    while cond:
        x: Point = Point(1, 2)
        break
    print(x)  # tpyc: error(/may not be assigned at this point/)
