# Reassigning a trusted-call-return local to a pointer-to-local must
# clear the trust so the subsequent return is rejected. The unclobbered
# chain -- bind the trusted return, then return it -- is pinned by
# tests/cases/pointers/ptr_return_trusted_call.
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


def reassigned_to_local() -> Ptr[Point]:
    p: Ptr[Point] = addr_global()  # trusted at this point
    local: Point = Point(5, 6)
    p = local                       # trust must be discarded here
    return p  # tpyc: error(/would dangle/)
