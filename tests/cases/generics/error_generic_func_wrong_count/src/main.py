"""Test error when wrong number of type arguments provided."""
from tpy import int32


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
first[int32, str](nums)  # tpyc: error(/expects 1 type argument/)
