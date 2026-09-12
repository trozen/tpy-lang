from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


def make_optional(flag: bool) -> Point | None:
    if flag:
        p = Point(1, 2)
        return p  # tpyc: error(/Cannot return local or temporary/)
    return None
