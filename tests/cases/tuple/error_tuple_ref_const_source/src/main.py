# Returning readonly param as mutable tuple reference element should error
from tpy import Int32, readonly

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def extract(p: readonly[Point]) -> tuple[Int32, Point]:
    return (p.x, p)  # tpyc: error(/readonly source/)
