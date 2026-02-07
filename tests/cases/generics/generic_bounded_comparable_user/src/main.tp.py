"""Test user-defined record with __lt__ satisfies Comparable bound."""
from __future__ import annotations
from tpy import Int32, Bool, Comparable


class MyInt:
    value: Int32

    def __init__(self, v: Int32):
        self.value = v

    def __lt__(self, other: MyInt) -> Bool:
        return self.value < other.value

    def __eq__(self, other: MyInt) -> Bool:
        return self.value == other.value


def is_less[T: Comparable](a: T, b: T) -> Bool:
    return a < b


def find_min[T: Comparable](a: T, b: T) -> T:
    if a < b:
        return a
    return b


def main() -> None:
    x = MyInt(10)
    y = MyInt(20)
    z = MyInt(10)

    # Test comparison via Comparable bound
    print(is_less(x, y))  # True
    print(is_less(y, x))  # False

    # Test returning bounded type
    result = find_min(x, y)
    print(result.value)  # 10

    # Test equality
    print(x == z)  # True
    print(x == y)  # False


main()
