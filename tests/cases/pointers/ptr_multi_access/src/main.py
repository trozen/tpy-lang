from tpy import Ptr, Int32, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> Int32:
        return self.x + self.y

def use_fields(p: Ptr[Point]) -> None:
    # Multiple field accesses on same pointer in one expression
    print(p.x + p.y)

def use_methods(p: Ptr[Point]) -> None:
    # Method + field access on same pointer
    print(p.sum(), p.x)

def test() -> None:
    pt: Point = Point(3, 7)
    p: Ptr[Point] = take_ptr(pt)
    use_fields(p)
    use_methods(p)

test()
