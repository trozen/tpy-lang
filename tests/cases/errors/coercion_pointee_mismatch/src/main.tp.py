"""Tests that Record -> Ptr[DifferentRecord] is rejected (pointee mismatch)."""
from tpy import Int32, Ptr

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


class Color:
    r: Int32
    g: Int32
    b: Int32

    def __init__(self, r: Int32, g: Int32, b: Int32) -> None:
        self.r = r
        self.g = g
        self.b = b


def main() -> None:
    pt: Point = Point(10, 20)
    ptr: Ptr[Color] = pt  # tpyc: error(/Type mismatch.*expected Ptr\[Color\], got Point/)
