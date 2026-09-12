from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def make() -> int32:
    p: Point | None = Point(7)
    return p.x


print(make())
