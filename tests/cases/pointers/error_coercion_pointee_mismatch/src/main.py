"""Tests that Record -> Ptr[DifferentRecord] is rejected (pointee mismatch)."""
from tpy import int32, Ptr

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Color:
    r: int32
    g: int32
    b: int32

    def __init__(self, r: int32, g: int32, b: int32) -> None:
        self.r = r
        self.g = g
        self.b = b


def main() -> None:
    pt: Point = Point(10, 20)
    ptr: Ptr[Color] = pt  # tpyc: error(/Type mismatch.*expected Ptr\[Color\], got Point/)
