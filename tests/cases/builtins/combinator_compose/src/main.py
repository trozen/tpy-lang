# Combinator composition: chaining map/filter/enumerate/zip.
# Verifies that __next__()-based iterators (builtin and user-defined)
# work as inputs to other combinators.
from tpy import Int32, Span
from typing import Iterator

def double(x: Int32) -> Int32:
    return x * 2

def is_positive(x: Int32) -> bool:
    return x > 0

def triple_gen(items: Span[Int32]) -> Iterator[Int32]:
    for item in items:
        yield item * 3

def main() -> None:
    xs = [1, 2, 3, 4, 5]

    # enumerate(map(...))
    for i, v in enumerate(map(double, xs)):
        if i == 2:
            print(v)  # 6

    # filter over raw list
    nums = [-1, 2, -3, 4, -5]
    for v in filter(is_positive, nums):
        print(v)  # 2, 4

    # enumerate(filter(...))
    total = 0
    for i, v in enumerate(filter(is_positive, nums)):
        total = total + v
    print(total)  # 6

    # map(filter(...))
    for v in map(double, filter(is_positive, nums)):
        print(v)  # 4, 8

    # User-defined generator composed with enumerate
    for i, v in enumerate(triple_gen(xs)):
        if i == 1:
            print(v)  # 6

main()
