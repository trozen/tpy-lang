from typing import Sequence
from tpy import Int32, Array, Span
from tplib import ArrayList

def first(items: Sequence[Int32]) -> Int32:
    return items[0]

def sum_all(items: Sequence[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def use_span(s: Span[Int32]) -> Int32:
    # Pass Span to Sequence-accepting function
    return sum_all(s)

def main() -> None:
    # Test with list[Int32]
    nums: list[Int32] = [1, 2, 3, 4, 5]
    print(first(nums))     # 1
    print(sum_all(nums))   # 15

    # Test with Array[Int32, N]
    arr: Array[Int32, 3] = [10, 20, 30]
    print(first(arr))      # 10
    print(sum_all(arr))    # 60

    # Test with Span[Int32]
    print(use_span(arr))   # 60 (Span from Array)
    print(use_span(nums))  # 15 (Span from list)

    # Test with ArrayList[Int32, N] (user/library type)
    al = ArrayList[Int32, 8]()
    al.append(100)
    al.append(200)
    al.append(300)
    print(first(al))       # 100
    print(sum_all(al))     # 600

main()
