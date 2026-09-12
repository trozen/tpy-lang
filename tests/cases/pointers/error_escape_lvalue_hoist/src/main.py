from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Lvalue-initialized var in loop body: p aliases items[0], so hoisting
# p's pointer doesn't help — the underlying storage (items) is still
# loop-scoped and dies at iteration end.
def lvalue_hoist_unsafe() -> None:
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(i, i)]
        p: Point = items[0]
        saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
