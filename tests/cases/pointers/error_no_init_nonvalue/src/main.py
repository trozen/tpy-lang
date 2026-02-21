from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def bad_no_init() -> None:
    p: Point  # tpyc: error(/must have an initializer/)

def value_no_init_ok() -> None:
    x: Int32  # tpyc: ok

pt: Point = Point(1, 2)
