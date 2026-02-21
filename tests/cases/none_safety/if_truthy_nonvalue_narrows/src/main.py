from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def score(p: Point | None) -> Int32:
    if p:  # tpyc: ok
        return p.x + 1  # tpyc: ok
    return 0


pt: Point = Point(2)
print(score(pt))
print(score(None))
