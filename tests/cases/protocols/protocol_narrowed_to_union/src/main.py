# Narrowed Optional[Protocol] passed to a required multi-protocol union param.
# Regression test: the narrowed variable (pointer in C++) must be dereferenced.
from typing import Iterable
from tpy import int32, Spannable, span


def total(items: Spannable[int32] | Iterable[int32]) -> int32:
    if isinstance(items, Spannable):
        s = span(items)
        result: int32 = 0
        for x in s:
            result += x
        return result
    else:
        result2: int32 = 0
        for x2 in items:
            result2 += x2
        return result2


def maybe_total(items: Spannable[int32] | Iterable[int32] | None) -> int32:
    if items is not None:
        return total(items)
    return -1


def main() -> None:
    nums: list[int32] = [10, 20, 30]
    print(maybe_total(nums))    # 60
    print(maybe_total(None))    # -1


main()
