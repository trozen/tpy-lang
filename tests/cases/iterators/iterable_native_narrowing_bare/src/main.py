# Cleaner narrowing pattern: a plain `it: Iterable[T]` parameter with
# `isinstance(it, NativeIterable)` -- no union arm needed. NativeIterable
# extends Iterable, so codegen threads the element type through the parent
# protocol and emits `if constexpr (::tpy::NativeIterable<T_it, int32_t>)`
# (both template args). The narrowed branch also picks up the begin/end
# range-for fast path.
from typing import Iterable
from tpy import int32, NativeIterable


def sum_fast(it: Iterable[int32]) -> int32:
    total: int32 = 0
    if isinstance(it, NativeIterable):
        # Narrowed to NativeIterable[int32] -- emitted as begin/end range-for.
        for x in it:
            total += x
    else:
        # Still Iterable[int32] -- emitted as universal __iter__/__next__.
        for x in it:
            total += x
    return total


def main() -> None:
    nums: list[int32] = [1, 2, 3, 4]
    print(sum_fast(nums))  # 10


main()
