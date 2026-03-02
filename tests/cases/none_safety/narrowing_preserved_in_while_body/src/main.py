# Outer narrowing from "if x is not None" preserved inside while loop bodies.
# Covers: simple while, multiple optionals, and mixed if/while narrowing.
from tpy import Int32


def simple_while(x: Int32 | None, n: Int32) -> Int32:
    total: Int32 = 0
    if x is not None:
        i: Int32 = 0
        while i < n:
            total = total + x  # tpyc: ok
            i = i + 1
    return total


def multiple_optionals(a: Int32 | None, b: Int32 | None, n: Int32) -> Int32:
    total: Int32 = 0
    if a is not None:
        if b is not None:
            i: Int32 = 0
            while i < n:
                total = total + a + b  # tpyc: ok
                i = i + 1
    return total


def outer_if_inner_while_narrowing(
    x: Int32 | None, items: list[Int32 | None]
) -> Int32:
    total: Int32 = 0
    if x is not None:
        i: Int32 = 0
        while i < len(items):
            y: Int32 | None = items[i]
            if y is not None:
                total = total + x + y  # tpyc: ok
            i = i + 1
    return total


def for_loop_variant(x: Int32 | None, items: list[Int32]) -> Int32:
    total: Int32 = 0
    if x is not None:
        for item in items:
            total = total + item + x  # tpyc: ok
    return total


def main() -> None:
    print(simple_while(10, 3))
    print(simple_while(None, 3))

    print(multiple_optionals(2, 3, 4))
    print(multiple_optionals(None, 3, 4))
    print(multiple_optionals(2, None, 4))

    items: list[Int32 | None] = [1, None, 3]
    print(outer_if_inner_while_narrowing(10, items))
    print(outer_if_inner_while_narrowing(None, items))

    nums: list[Int32] = [1, 2, 3]
    print(for_loop_variant(5, nums))
    print(for_loop_variant(None, nums))


main()
