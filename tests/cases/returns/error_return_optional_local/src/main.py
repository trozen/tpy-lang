from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


def make_optional(flag: bool) -> Point | None:
    if flag:
        p = Point(1, 2)
        return p  # tpyc: error(/Cannot return local or temporary/)
    return None
