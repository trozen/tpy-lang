# Generator expressions: lazy iterators passed to functions accepting Iterable[T].
from typing import Iterable
from tpy import Int32

def sum_items(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    # Range source
    print(sum_items(x * x for x in range(5)))

    # List source
    items: list[Int32] = [1, 2, 3, 4, 5]
    print(sum_items(x * 2 for x in items))

    # Filter with if clause
    print(sum_items(x for x in range(10) if x % 2 == 0))

    # 2-arg range
    print(sum_items(x for x in range(5, 10)))

    # 3-arg range (step) -- exercises IIFE+Range codegen path
    print(sum_items(x for x in range(0, 10, 2)))

    # Body referencing outer local -- exercises [&] capture
    multiplier: Int32 = 3
    print(sum_items(x * multiplier for x in range(5)))

    # str.join with generator expression
    nums: list[Int32] = [1, 2, 3]
    print(", ".join(str(x) for x in nums))

    # Tuple unpacking in generator
    pairs: list[tuple[str, Int32]] = [("a", 1), ("b", 2), ("c", 3)]
    print(sum_items(v for _, v in pairs))

main()
