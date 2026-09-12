"""Tests that int32 -> Ptr coercion is rejected (no coercion path)."""
from tpy import int32, Ptr

class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    n: int32 = 42
    ptr: Ptr[Point] = n  # tpyc: error(/Type mismatch.*expected Ptr\[Point\], got int32/)
