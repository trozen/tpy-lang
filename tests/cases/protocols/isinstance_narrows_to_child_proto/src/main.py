# isinstance(xs, NativeIterable) narrows xs from Iterable[T] to
# NativeIterable[T] (which extends Iterable[T]). Exercises
# the narrowing's parent-protocol -> child-protocol path, which asks
# `protocol_ancestor_at` whether the check protocol inherits the declared one.
from typing import Iterable
from tpy import int32, NativeIterable


def sum_native(xs: NativeIterable[int32]) -> int32:
    total: int32 = 0
    for x in xs:
        total += x
    return total


def maybe_sum(xs: Iterable[int32]) -> int32:
    if isinstance(xs, NativeIterable):
        return sum_native(xs)
    return -1


def main() -> None:
    nums: list[int32] = [10, 20, 30]
    print(maybe_sum(nums))


main()
