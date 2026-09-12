from tpy import Ptr, int32, take_ptr

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> int32:
        return self.x + self.y
    def describe(self) -> str:
        return "Point"

def main() -> None:
    pt: Point = Point(10, 20)
    p: Ptr[Point] = take_ptr(pt)
    # Method call through Ptr auto-deref
    print(p.sum())
    print(p.describe())

main()
