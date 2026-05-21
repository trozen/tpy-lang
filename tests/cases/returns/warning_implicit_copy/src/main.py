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

    # Warning: indirect flow through local -> field
    r = identity(pt)
    h.p = r  # tpyc: warning(/copies Point into field/)

    # Warning: indirect flow through local -> container
    s = identity(pts[0])
    pts[1] = s  # tpyc: warning(/copies Point into container/)

    # Warning: field-to-field copy (field access is a reference)
    h2 = Holder(pt)
    h.p = h2.p  # tpyc: warning(/copies Point into field/)

    # Warning: subscript-to-field copy (subscript is a reference)
    h.p = pts[0]  # tpyc: warning(/copies Point into field/)

    print(h.p.x)
    print(pts[0].x)

main()
