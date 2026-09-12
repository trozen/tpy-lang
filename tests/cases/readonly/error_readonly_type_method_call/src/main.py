# readonly[T]: calling a non-readonly method on readonly param is rejected.
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def set_x(self, v: int32) -> None:
        self.x = v

def bad(p: readonly[Point]) -> None:
    p.set_x(int32(99))  # tpyc: error(/Cannot call non-readonly method 'set_x' on readonly reference/)
