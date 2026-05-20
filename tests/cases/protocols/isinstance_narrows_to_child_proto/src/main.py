# isinstance(xs, NativeIterable) narrows xs from Iterable[T] to
# NativeIterable[T] (which extends Iterable[T]). Exercises
# sema/narrowing.py:292's parent-protocol -> child-protocol path,
# which now routes through the unified is_subtype kernel via
# ProtocolChecker.protocol_inherits_from.
from typing import Iterable
from tpy import Int32, NativeIterable


def sum_native(xs: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in xs:
        total += x
    return total


def maybe_sum(xs: Iterable[Int32]) -> Int32:
    if isinstance(xs, NativeIterable):
        return sum_native(xs)
    return -1


def main() -> None:
    nums: list[Int32] = [10, 20, 30]
    print(maybe_sum(nums))


main()
