# Protocol-typed Iterable[T] params: protocol-to-protocol forwarding, nested
# for-loops, and `in` operator. The basic iteration case is covered by
# iterable_builtin_types; this exercises the composition/control-flow shapes.
from typing import Iterable
from tpy import int32, Array

def sum_iter(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def print_all(items: Iterable[int32]) -> None:
    for x in items:
        print(x)

def process_and_sum(items: Iterable[int32]) -> int32:
    # Protocol-to-protocol passing: Iterable[T] -> Iterable[T]
    print_all(items)
    return sum_iter(items)

def nested_iteration(outer: Iterable[int32], inner: Iterable[int32]) -> int32:
    # Nested for loops over two different protocol-typed params
    total: int32 = 0
    for x in outer:
        for y in inner:
            total += x * y
    return total

def contains_value(items: Iterable[int32], target: int32) -> bool:
    # `in` operator on protocol-typed param
    return target in items

def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]

    # Protocol-to-protocol passing
    print(process_and_sum(arr))  # 10, 20, 30, 60

    # Nested iteration
    a: list[int32] = [1, 2]
    b: list[int32] = [10, 20]
    print(nested_iteration(a, b))  # (1*10 + 1*20) + (2*10 + 2*20) = 90

    # `in` operator
    nums: list[int32] = [1, 2, 3]
    print(contains_value(nums, 2))   # True
    print(contains_value(nums, 99))  # False

main()
