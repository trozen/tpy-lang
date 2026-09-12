# isinstance(xs, NativeIterable) narrows xs from Iterable[T] to
# NativeIterable[T] (which extends Iterable[T]). Exercises
# sema/narrowing.py:292's parent-protocol -> child-protocol path,
# which now routes through the unified is_subtype kernel via
# ProtocolChecker.protocol_inherits_from.
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
