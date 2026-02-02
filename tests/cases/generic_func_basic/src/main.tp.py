"""Test basic generic functions with type inference."""
from tpy import Int32


def first[T](items: list[T]) -> T:
    return items[0]


def last[T](items: list[T]) -> T:
    return items[len(items) - 1]


# Inference from list[int]
nums = [10, 20, 30]
print(first(nums))
print(last(nums))

# Inference from list[str]
words = ["hello", "world"]
print(first(words))
print(last(words))

# Inference from list[Int32]
vals: list[Int32] = [Int32(1), Int32(2), Int32(3)]
print(first(vals))
print(last(vals))
