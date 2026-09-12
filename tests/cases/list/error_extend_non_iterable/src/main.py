"""Tests that extend() rejects non-iterable arguments."""
from tpy import int32

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    nums.extend(42)  # tpyc: error(/does not conform to protocol Iterable/)
