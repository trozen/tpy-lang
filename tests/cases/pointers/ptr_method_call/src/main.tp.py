from tpy import Ptr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> Int32:
        return self.x + self.y
    def describe(self) -> str:
        return "Point"

def main() -> None:
    pt: Point = Point(10, 20)
    p: Ptr[Point] = Ptr(pt)
    # Method call through Ptr auto-deref
    print(p.sum())
    print(p.describe())

main()
