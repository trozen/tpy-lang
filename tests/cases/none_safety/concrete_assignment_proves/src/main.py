from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def make() -> Int32:
    p: Point | None = Point(7)
    return p.x


print(make())
