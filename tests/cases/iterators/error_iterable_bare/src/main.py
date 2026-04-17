# Bare generic protocol (no type argument) is rejected.
from typing import Iterable
from tpy import Int32

def sum_iter(items: Iterable) -> Int32:  # tpyc: error(/requires type arguments/)
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(sum_iter(nums))

main()
