from typing import Sequence
from tpy import int32, Array, Span
from tplib import ArrayList

def first(items: Sequence[int32]) -> int32:
    return items[0]

def sum_all(items: Sequence[int32]) -> int32:
    total: int32 = 0
    i: int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def use_span(s: Span[int32]) -> int32:
    # Pass Span to Sequence-accepting function
    return sum_all(s)

def main() -> None:
    # Test with list[int32]
    nums: list[int32] = [1, 2, 3, 4, 5]
    print(first(nums))     # 1
    print(sum_all(nums))   # 15

    # Test with Array[int32, N]
    arr: Array[int32, 3] = [10, 20, 30]
    print(first(arr))      # 10
    print(sum_all(arr))    # 60

    # Test with Span[int32]
    print(use_span(arr))   # 60 (Span from Array)
    print(use_span(nums))  # 15 (Span from list)

    # Test with ArrayList[int32, N] (user/library type)
    al = ArrayList[int32, 8]()
    al.append(100)
    al.append(200)
    al.append(300)
    print(first(al))       # 100
    print(sum_all(al))     # 600

main()
