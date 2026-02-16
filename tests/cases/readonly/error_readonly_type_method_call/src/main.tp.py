# readonly[T]: calling a non-readonly method on readonly param is rejected.
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
    p.set_x(Int32(99))  # tpyc: error(/Cannot call non-readonly method 'set_x' on readonly reference/)
