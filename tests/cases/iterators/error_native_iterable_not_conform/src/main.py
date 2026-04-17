# NativeIterable[T] param still works as a signature (it's the fast-path arm
# of the `Iterable[T] | NativeIterable[T]` union-narrowing idiom). Its
# protocol-conformance check must reject types that have no __iter__ at all.
from tpy import Int32, NativeIterable


class NotIterable:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


def sum_native(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total


def main() -> None:
    ni = NotIterable(42)
    sum_native(ni)  # tpyc: error(/does not conform to protocol/)


main()
