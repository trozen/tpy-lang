"""Test generic functions with type annotation hints."""
from tpy import Int32


def first[T](items: list[T]) -> T:
    return items[0]


# Type inferred from list[Int32]
nums32: list[Int32] = [Int32(1), Int32(2), Int32(3)]
result: Int32 = first(nums32)
print(result)

# Type inferred from list[int]
nums: list[int] = [10, 20, 30]
result2: int = first(nums)
print(result2)
