# Narrowed Optional[Protocol] passed to a required multi-protocol union param.
# Regression test: the narrowed variable (pointer in C++) must be dereferenced.
from typing import Iterable
from tpy import Int32, ReadOnlySpanLike, span


def total(items: ReadOnlySpanLike[Int32] | Iterable[Int32]) -> Int32:
    if isinstance(items, ReadOnlySpanLike):
        s = span(items)
        result: Int32 = 0
        for x in s:
            result += x
        return result
    else:
        result2: Int32 = 0
        for x2 in items:
            result2 += x2
        return result2


def maybe_total(items: ReadOnlySpanLike[Int32] | Iterable[Int32] | None) -> Int32:
    if items is not None:
        return total(items)
    return -1


def main() -> None:
    nums: list[Int32] = [10, 20, 30]
    print(maybe_total(nums))    # 60
    print(maybe_total(None))    # -1


main()
