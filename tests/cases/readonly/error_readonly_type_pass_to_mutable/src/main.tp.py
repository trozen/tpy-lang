# readonly[T]: passing readonly param to a mutable T parameter is rejected.
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def mutate(p: Point) -> None:
    p.x = Int32(99)

def bad(p: readonly[Point]) -> None:
    mutate(p)  # tpyc: error(/readonly/)
