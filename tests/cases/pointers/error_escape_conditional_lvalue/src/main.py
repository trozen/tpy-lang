from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# One branch assigns from lvalue (items[0]), the other from rvalue (Point()).
# Conservative: p is NOT rvalue in all branches, so hoisting is unsafe.
def conditional_lvalue() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(i, i)]
        p: Point = Point(i, i)
        if i > 0:
            p = items[0]
        saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
