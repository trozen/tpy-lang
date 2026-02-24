# Calling a record type alias as a constructor is not allowed (matches CPython)
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

type P = Point

p = P(Int32(1), Int32(2))  # tpyc: error(/not callable/)
