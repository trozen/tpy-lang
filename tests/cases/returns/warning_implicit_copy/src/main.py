# Warn when a reference return is stored in a field or container element
# (implicit copy). No warning for local variable binding (C++ uses T&).
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Holder:
    p: Point
    def __init__(self, p: Point) -> None:
        self.p = p

def identity(p: Point) -> Point:
    return p

def main() -> None:
    pt = Point(1, 2)
    pts = [Point(1, 2), Point(3, 4)]

    # No warning: local binding uses C++ reference, no copy
    q = identity(pt)
    print(q.x)

    # Warning: field assignment copies from reference
    h = Holder(pt)
    h.p = identity(pt)  # tpyc: warning(/copies Point into field/)

    # Warning: subscript assignment copies from reference
    pts[0] = identity(pts[1])  # tpyc: warning(/copies Point into container/)

    print(h.p.x)
    print(pts[0].x)

main()
