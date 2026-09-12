# Bare generic protocol (no type argument) is rejected.
from typing import Iterable
from tpy import int32

def sum_iter(items: Iterable) -> int32:  # tpyc: error(/requires type arguments/)
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(sum_iter(nums))

main()
