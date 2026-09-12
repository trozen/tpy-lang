"""Test generic functions with type annotation hints."""
from tpy import int32


def first[T](items: list[T]) -> T:
    return items[0]


# Type inferred from list[int32]
nums32: list[int32] = [int32(1), int32(2), int32(3)]
result: int32 = first(nums32)
print(result)

# Type inferred from list[int]
nums: list[int] = [10, 20, 30]
result2: int = first(nums)
print(result2)
