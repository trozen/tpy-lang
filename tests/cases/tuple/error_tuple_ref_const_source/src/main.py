# Returning readonly param as mutable tuple reference element should error
from tpy import int32, readonly

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def extract(p: readonly[Point]) -> tuple[int32, Point]:
    return (p.x, p)  # tpyc: error(/readonly source/)
