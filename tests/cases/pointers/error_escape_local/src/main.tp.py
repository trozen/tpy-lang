from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def loop_escape() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        p: Point = Point(i, i)
        saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
