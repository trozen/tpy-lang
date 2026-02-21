from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def bad_reassign(p: Point) -> None:
    p = Point(99, 99)  # tpyc: error(/Cannot reassign parameter/)
    print(p.x)

def value_param_ok(x: Int32) -> Int32:
    x = x + 1  # tpyc: ok
    return x

pt: Point = Point(1, 2)
