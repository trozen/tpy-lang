from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def get_x(p: Point | None) -> int32:
    assert p  # tpyc: ok
    return p.x  # tpyc: ok


pt: Point = Point(7)
print(get_x(pt))
