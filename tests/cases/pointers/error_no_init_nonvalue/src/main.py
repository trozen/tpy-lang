# A reference-typed local needs an initializer at its declaration. The value-
# typed spelling that IS allowed to stay bare until a later assignment is
# pinned by tests/cases/pointers/init_tracking.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

def bad_no_init() -> None:
    p: Point  # tpyc: error(/must have an initializer/)

pt: Point = Point(1, 2)
