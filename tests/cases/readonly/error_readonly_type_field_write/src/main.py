# readonly[T]: field assignment on readonly param is rejected.
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def bad(p: readonly[Point]) -> None:
    p.x = int32(99)  # tpyc: error(/Cannot mutate readonly reference/)
