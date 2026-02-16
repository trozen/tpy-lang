# readonly[T]: field assignment on readonly param is rejected.
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def bad(p: readonly[Point]) -> None:
    p.x = Int32(99)  # tpyc: error(/Cannot mutate readonly reference/)
