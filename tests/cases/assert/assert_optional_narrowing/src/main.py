from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x

    def mag(self) -> Int32:
        return self.x


def get_x(p: Point | None) -> Int32:
    assert p is not None
    return p.x


def get_mag(p: Point | None) -> Int32:
    assert p is not None, "point required"
    return p.mag()


q: Point | None = Point(7)
print(get_x(q))
print(get_mag(q))
