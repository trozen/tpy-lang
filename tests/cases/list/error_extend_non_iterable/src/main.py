"""Tests that extend() rejects non-iterable arguments."""
from tpy import Int32

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    nums.extend(42)  # tpyc: error(/does not conform to protocol Iterable/)
