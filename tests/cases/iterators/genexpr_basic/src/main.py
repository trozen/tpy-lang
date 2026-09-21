# Generator expressions: lazy iterators passed to functions accepting Iterable[T].
from typing import Iterable
from tpy import int32

def sum_items(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    # Range source
    print(sum_items(x * x for x in range(5)))

    # List source
    items: list[int32] = [1, 2, 3, 4, 5]
    print(sum_items(x * 2 for x in items))

    # Filter with if clause
    print(sum_items(x for x in range(10) if x % 2 == 0))

    # 2-arg range
    print(sum_items(x for x in range(5, 10)))

    # 3-arg range (positive step)
    print(sum_items(x for x in range(0, 10, 2)))

    # 3-arg range (negative step)
    print(sum_items(x for x in range(10, 0, -2)))

    # Literal list source (non-lvalue -- source must be owned by the generator)
    print(sum_items(x for x in [10, 20, 30]))

    # Non-lvalue source with filter condition
    print(sum_items(x for x in [1, 2, 3, 4, 5] if x > 2))

    # Body referencing outer local -- exercises [&] capture
    multiplier: int32 = 3
    print(sum_items(x * multiplier for x in range(5)))

    # Lvalue source with outer local in yield -- exercises explicit capture on lvalue path
    print(sum_items(x * multiplier for x in items))

    # Outer local referenced only in filter condition
    threshold: int32 = 3
    print(sum_items(x for x in range(10) if x > threshold))

    # Lvalue source with outer local only in filter (a capture the element never reads)
    print(sum_items(x for x in items if x > threshold))

    # str.join with generator expression
    nums: list[int32] = [1, 2, 3]
    print(", ".join(str(x) for x in nums))

    # Tuple unpacking in generator
    pairs: list[tuple[str, int32]] = [("a", 1), ("b", 2), ("c", 3)]
    print(sum_items(v for _, v in pairs))

main()
