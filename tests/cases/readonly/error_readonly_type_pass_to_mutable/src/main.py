# readonly[T]: passing readonly param to a mutable T parameter is rejected.
from tpy import int32, readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def mutate(p: Point) -> None:
    p.x = int32(99)

def bad(p: readonly[Point]) -> None:
    mutate(p)  # tpyc: error(/readonly/)
