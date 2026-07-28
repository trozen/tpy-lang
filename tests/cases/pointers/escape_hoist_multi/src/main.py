from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Two different loop-body vars both escape to outer scope.
# Both slots must be hoisted to function scope.
def multi_hoist() -> None:
    saved_a: Point = Point(0, 0)
    saved_b: Point = Point(0, 0)
    for i in range(3):
        a: Point = Point(i, 10)
        b: Point = Point(20, i)
        saved_a = a  # tpyc: warning(/will not keep the object it was given/)
        saved_b = b  # tpyc: warning(/will not keep the object it was given/)
    print(saved_a.x, saved_a.y)
    print(saved_b.x, saved_b.y)

multi_hoist()
