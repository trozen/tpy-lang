# isinstance() on Optional[Protocol] -- narrows away None at compile time
from typing import Sized
from tpy import Int32, ReadOnlySpanLike, Span, Array


def count_if_sized(items: Sized | None = None) -> Int32:
    if isinstance(items, Sized):
        return len(items)
    return -1


def sum_span(items: ReadOnlySpanLike[Int32] | None = None) -> Int32:
    if isinstance(items, ReadOnlySpanLike):
        total: Int32 = 0
        for x in items:
            total += x
        return total
    return -1


def check_not(items: Sized | None = None) -> Int32:
    if not isinstance(items, Sized):
        return -1
    return len(items)


def main() -> None:
    arr: Array[Int32, 3] = [10, 20, 30]
    s: Span[Int32] = arr

    # Sized | None
    nums: list[Int32] = [1, 2, 3, 4, 5]
    print(count_if_sized(nums))
    print(count_if_sized())

    # ReadOnlySpanLike[T] | None (generic protocol)
    print(sum_span(s))
    print(sum_span())

    # not isinstance
    print(check_not(nums))
    print(check_not())


main()
