"""Tests that Int32 -> Ptr coercion is rejected (no coercion path)."""
from tpy import Int32, Ptr

class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    n: Int32 = 42
    ptr: Ptr[Point] = n  # tpyc: error(/Type mismatch.*expected Ptr\[Point\], got Int32/)
