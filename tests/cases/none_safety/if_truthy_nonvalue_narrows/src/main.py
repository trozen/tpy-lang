from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def score(p: Point | None) -> int32:
    if p:  # tpyc: ok
        return p.x + 1  # tpyc: ok
    return 0


pt: Point = Point(2)
print(score(pt))
print(score(None))
