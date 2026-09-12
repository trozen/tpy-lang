from typing import Sequence
from tpy import int32

def sum_ints(items: Sequence[int32]) -> int32:
    total: int32 = 0
    i: int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def main() -> None:
    strs: list[str] = ["a", "b"]
    sum_ints(strs)  # tpyc: error(/does not conform/)

main()
