from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Variable starts rvalue-initialized but is later reassigned from an
# lvalue. Hoisting is not safe because p now aliases loop-scoped storage.
def rvalue_then_lvalue() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(i, i)]
        p: Point = Point(i, i)
        p = items[0]
        saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
