# Reassigning a trusted-call-return local to a pointer-to-local must
# clear the trust so the subsequent return is rejected.
from tpy import Int32, Ptr


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


g: Point = Point(1, 2)


def addr_global() -> Ptr[Point]:
    return g


def reassigned_to_local() -> Ptr[Point]:
    p: Ptr[Point] = addr_global()  # tpyc: ok (trusted)
    local: Point = Point(5, 6)
    p = local                       # trust must be discarded here
    return p  # tpyc: error(/would dangle/)
