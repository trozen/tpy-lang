from typing import Sequence
from tpy import Int32, Array

def first(items: Sequence[Int32]) -> Int32:
    return items[0]

def sum_all(items: Sequence[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def main() -> None:
    nums: list[Int32] = [1, 2, 3, 4, 5]
    print(first(nums))     # 1
    print(sum_all(nums))   # 15

    arr: Array[Int32, 3] = [10, 20, 30]
    print(first(arr))      # 10
    print(sum_all(arr))    # 60

main()
