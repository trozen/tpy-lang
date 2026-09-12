# Tuple literal assigned to obj.field (not self.field) uses VALUE capture.
from tpy import int32, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

class Holder:
    data: tuple[Point, int32]
    def __init__(self, p: Point, n: int32) -> None:
        self.data = (copy(p), n)
    def __repr__(self) -> str:
        return "Holder"

def update(h: Holder, p: Point) -> None:
    h.data = (copy(p), int32(99))

def main() -> None:
    p = Point(int32(1), int32(2))
    h = Holder(p, int32(42))
    print(h.data[0].x, h.data[1])
    # Update via non-self field assignment
    p2 = Point(int32(10), int32(20))
    update(h, p2)
    print(h.data[0].x, h.data[1])
    # Mutation of p2 should NOT affect h.data (value semantics)
    p2.x = int32(55)
    print(h.data[0].x)

main()
