from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def describe(x: Int32) -> None:
    print("int:", x)

def describe(p: Point) -> None:
    print("point:", p.x, p.y)

def test() -> None:
    pt: Point = Point(3, 7)
    p: Ptr[Point] = pt
    # Overload resolution should pick describe(Point) via Ptr[T] -> T deref coercion
    describe(p)

test()
