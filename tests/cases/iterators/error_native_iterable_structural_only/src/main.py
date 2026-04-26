# Sema-side NativeIterable conformance must be explicit, not structural.
# A user record with `__iter__` but no `extends NativeIterable` declaration
# would pass a structural method check, but the C++ NativeIterable concept
# requires std::ranges::begin/end (only synthesized when the record explicitly
# extends NativeIterable). Without this gate, sema would admit the call but
# C++ template instantiation would later fail.
from tpy import Int32, NativeIterable, Own


class CounterIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration


# Has __iter__ -- structurally matches NativeIterable's __iter__ method
# signature, but does NOT declare `extends NativeIterable`. Sema must reject.
class Counter:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)


def sum_native(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total


def main() -> None:
    c = Counter(1, 4)
    sum_native(c)  # tpyc: error(/does not conform to protocol/)


main()
