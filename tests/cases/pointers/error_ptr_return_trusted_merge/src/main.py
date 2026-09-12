# If one branch of an if/else binds the Ptr to a local, trust must not
# survive the merge: returning the merged local is still dangling.
from tpy import int32, Ptr


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


g: Point = Point(1, 2)


def addr_global() -> Ptr[Point]:
    return g


def branch_one_local(cond: bool) -> Ptr[Point]:
    if cond:
        p: Ptr[Point] = addr_global()
    else:
        local: Point = Point(5, 6)
        p = local
    return p  # tpyc: error(/would dangle/)
