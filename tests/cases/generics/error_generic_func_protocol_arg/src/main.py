"""Test error when protocol type used as explicit type argument."""
from typing import Protocol


class Printable(Protocol):
    def __str__(self) -> str: ...


def first[T](items: list[T]) -> T:
    return items[0]


nums = [1, 2, 3]
first[Printable](nums)  # tpyc: error(/Protocol type.*cannot be used as a type argument/)
