from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# p declared at function scope, then reused as for-each var over
# a loop-local container. The for-each var references loop-local storage
# (depth 2), so assigning to saved (depth 1) is an escape.
def foreach_shadow_escape() -> None:
    p: Point = Point(0, 0)
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(i, i)]
        for p in items:
            saved = p  # tpyc: error(/reference to 'p' may outlive its storage/)
