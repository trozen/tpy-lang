from typing import Sequence
from tpy import Int32

def sum_ints(items: Sequence[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def main() -> None:
    strs: list[str] = ["a", "b"]
    sum_ints(strs)  # tpyc: error(/does not conform/)

main()
