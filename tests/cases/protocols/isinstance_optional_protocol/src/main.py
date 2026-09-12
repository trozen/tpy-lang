# isinstance() on Optional[Protocol] -- narrows away None at compile time
from typing import Sized
from tpy import int32, Spannable, Span, Array


def count_if_sized(items: Sized | None = None) -> int32:
    if isinstance(items, Sized):
        return len(items)
    return -1


def sum_span(items: Spannable[int32] | None = None) -> int32:
    if isinstance(items, Spannable):
        total: int32 = 0
        for x in items:
            total += x
        return total
    return -1


def check_not(items: Sized | None = None) -> int32:
    if not isinstance(items, Sized):
        return -1
    return len(items)


def main() -> None:
    arr: Array[int32, 3] = [10, 20, 30]
    s: Span[int32] = arr

    # Sized | None
    nums: list[int32] = [1, 2, 3, 4, 5]
    print(count_if_sized(nums))
    print(count_if_sized())

    # Spannable[T] | None (generic protocol)
    print(sum_span(s))
    print(sum_span())

    # not isinstance
    print(check_not(nums))
    print(check_not())


main()
