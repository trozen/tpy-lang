"""Tests that copy() has deep copy semantics for containers.

When returning Own[list[T]], C++ copies all elements by value.
The CPython harness must use deepcopy to match this behavior.
"""
from tpy import int32, Own, copy


class Point:
    x: int32


class Container:
    items: list[Point]


def take_items(c: Container) -> Own[list[Point]]:
    return copy(c.items)


def main() -> None:
    c: Container = Container()
    c.items = [Point()]
    c.items[0].x = 10

    # Get a copy of the items
    taken: list[Point] = take_items(c)

    # Modify the copy
    taken[0].x = 99

    # Original should be unchanged (deep copy semantics)
    print(c.items[0].x)  # 10
    print(taken[0].x)    # 99


main()
