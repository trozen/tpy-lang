from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    def mag(self) -> int32:
        return self.x + self.y


g: Point | None = None
print(g is None)

g = Point(3, 4)
print(g is None)
print(g.x)
print(g.mag())
