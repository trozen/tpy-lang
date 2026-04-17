# Protocol-typed Iterable[T] params: protocol-to-protocol forwarding, nested
# for-loops, and `in` operator. The basic iteration case is covered by
# iterable_builtin_types; this exercises the composition/control-flow shapes.
from typing import Iterable
from tpy import Int32, Array

def sum_iter(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def print_all(items: Iterable[Int32]) -> None:
    for x in items:
        print(x)

def process_and_sum(items: Iterable[Int32]) -> Int32:
    # Protocol-to-protocol passing: Iterable[T] -> Iterable[T]
    print_all(items)
    return sum_iter(items)

def nested_iteration(outer: Iterable[Int32], inner: Iterable[Int32]) -> Int32:
    # Nested for loops over two different protocol-typed params
    total: Int32 = 0
    for x in outer:
        for y in inner:
            total += x * y
    return total

def contains_value(items: Iterable[Int32], target: Int32) -> bool:
    # `in` operator on protocol-typed param
    return target in items

def main() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]

    # Protocol-to-protocol passing
    print(process_and_sum(arr))  # 10, 20, 30, 60

    # Nested iteration
    a: list[Int32] = [1, 2]
    b: list[Int32] = [10, 20]
    print(nested_iteration(a, b))  # (1*10 + 1*20) + (2*10 + 2*20) = 90

    # `in` operator
    nums: list[Int32] = [1, 2, 3]
    print(contains_value(nums, 2))   # True
    print(contains_value(nums, 99))  # False

main()
