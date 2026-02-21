from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# Variable goes rvalue → lvalue → rvalue. The final rvalue assignment
# re-enables hoisting because p now owns its storage again.
def rvalue_restored() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(99, 99)]
        p: Point = Point(0, 0)
        p = items[0]
        p = Point(i, i + 10)
        saved = p  # tpyc: warning(/hoisted to function scope/)
    print(saved.x, saved.y)

rvalue_restored()
