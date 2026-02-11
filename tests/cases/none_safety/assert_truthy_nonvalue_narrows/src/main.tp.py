from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def get_x(p: Point | None) -> Int32:
    assert p  # tpyc: ok
    return p.x  # tpyc: ok


pt: Point = Point(7)
print(get_x(pt))
