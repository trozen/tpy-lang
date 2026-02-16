# readonly[list[T]] iteration yields readonly[T] elements --
# mutating methods on elements are rejected.
from tpy import Int32, readonly

class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def set_x(self, v: Int32) -> None:
        self.x = v

def f(xs: readonly[list[Point]]) -> None:
    for p in xs:
        p.set_x(Int32(99))  # tpyc: error(/readonly/)
