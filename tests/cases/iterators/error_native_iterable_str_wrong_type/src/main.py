"""Tests that str does NOT conform to NativeIterable[Int32]."""
from tpy import Int32, NativeIterable

def sum_ints(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    s: str = "hello"
    sum_ints(s)  # tpyc: error(/does not conform to protocol NativeIterable/)

main()
