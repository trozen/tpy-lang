# Rebinding a reference-typed parameter is rejected -- the caller's object is
# borrowed, not owned. Reassigning a value-typed parameter, which IS allowed,
# is pinned by tests/cases/control_flow/param_reassign.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

def bad_reassign(p: Point) -> None:
    p = Point(99, 99)  # tpyc: error(/Cannot reassign parameter/)
    print(p.x)

pt: Point = Point(1, 2)
