# readonly[T]: local alias inherits readonly -- non-readonly method call rejected.
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def set_x(self, v: Int32) -> None:
        self.x = v

def bad(p: readonly[Point]) -> None:
    alias = p
    alias.set_x(Int32(99))  # tpyc: error(/Cannot call non-readonly method 'set_x' on readonly reference/)
