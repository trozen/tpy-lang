from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class Line:
    a: Point
    def __init__(self) -> None:
        self.a = Point(0)
g: Point | Line | None = None
print(g is None)
