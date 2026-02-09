from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def nested_loop_escape() -> None:
    for i in range(3):
        outer: Point = Point(i, 0)
        for j in range(3):
            inner: Point = Point(j, j)
            outer = inner  # tpyc: error(/reference to 'inner' may outlive its storage/)
