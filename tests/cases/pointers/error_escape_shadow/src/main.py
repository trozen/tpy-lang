from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# p declared at function scope, then reused as for-each var: the
# reference-type rebind is rejected outright (the old shadow form
# could leak loop-local storage past the loop).
def foreach_shadow_escape() -> None:
    p: Point = Point(0, 0)
    saved: Point = Point(0, 0)
    for i in range(3):
        items: list[Point] = [Point(i, i)]
        for p in items:  # tpyc: error(/for-loop rebind of reference-type variable 'p'/)
            saved = p
