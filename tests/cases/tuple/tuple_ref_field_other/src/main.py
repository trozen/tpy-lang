# Tuple literal assigned to obj.field (not self.field) uses VALUE capture.
from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Holder:
    data: tuple[Point, Int32]
    def __init__(self, p: Point, n: Int32) -> None:
        self.data = (copy(p), n)
    def __repr__(self) -> str:
        return "Holder"

def update(h: Holder, p: Point) -> None:
    h.data = (copy(p), Int32(99))

def main() -> None:
    p = Point(Int32(1), Int32(2))
    h = Holder(p, Int32(42))
    print(h.data[0].x, h.data[1])
    # Update via non-self field assignment
    p2 = Point(Int32(10), Int32(20))
    update(h, p2)
    print(h.data[0].x, h.data[1])
    # Mutation of p2 should NOT affect h.data (value semantics)
    p2.x = Int32(55)
    print(h.data[0].x)

main()
