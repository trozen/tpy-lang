# Outer narrowing from "if x is not None" preserved inside while loop bodies.
# Covers: simple while, multiple optionals, and mixed if/while narrowing.
from tpy import int32


def simple_while(x: int32 | None, n: int32) -> int32:
    total: int32 = 0
    if x is not None:
        i: int32 = 0
        while i < n:
            total = total + x  # tpyc: ok
            i = i + 1
    return total


def multiple_optionals(a: int32 | None, b: int32 | None, n: int32) -> int32:
    total: int32 = 0
    if a is not None:
        if b is not None:
            i: int32 = 0
            while i < n:
                total = total + a + b  # tpyc: ok
                i = i + 1
    return total


def outer_if_inner_while_narrowing(
    x: int32 | None, items: list[int32 | None]
) -> int32:
    total: int32 = 0
    if x is not None:
        i: int32 = 0
        while i < len(items):
            y: int32 | None = items[i]
            if y is not None:
                total = total + x + y  # tpyc: ok
            i = i + 1
    return total


def for_loop_variant(x: int32 | None, items: list[int32]) -> int32:
    total: int32 = 0
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

    items: list[int32 | None] = [1, None, 3]
    print(outer_if_inner_while_narrowing(10, items))
    print(outer_if_inner_while_narrowing(None, items))

    nums: list[int32] = [1, 2, 3]
    print(for_loop_variant(5, nums))
    print(for_loop_variant(None, nums))


main()
