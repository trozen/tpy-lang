"""Test error when wrong number of type arguments provided."""
from tpy import Int32


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
first[Int32, str](nums)  # tpyc: error(/expects 1 type argument/)
