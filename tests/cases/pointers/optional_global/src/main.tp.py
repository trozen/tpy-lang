from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    def mag(self) -> Int32:
        return self.x + self.y


g: Point | None = None
print(g is None)

g = Point(3, 4)
print(g is None)
print(g.x)
print(g.mag())
