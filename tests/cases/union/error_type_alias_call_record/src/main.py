# Calling a record type alias as a constructor is not allowed (matches CPython)
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

type P = Point

p = P(int32(1), int32(2))  # tpyc: error(/not callable/)
