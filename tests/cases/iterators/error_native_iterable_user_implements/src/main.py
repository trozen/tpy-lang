# NativeIterable is reserved for @native types. Non-native records that
# declare it explicitly are rejected at sema -- the C++ NativeIterable
# concept requires real begin/end, which TPy can only guarantee for
# built-ins. User types should use Spannable[T] or Iterable[T] instead.
from tpy import Int32, Iterator, NativeIterable, Own


class CounterIter:
    _val: Int32

    def __init__(self, val: Int32) -> None:
        self._val = val

    def __next__(self) -> Int32:
        if self._val > 0:
            r = self._val
            self._val -= 1
            return r
        raise StopIteration


class Counter(NativeIterable[Int32]):  # tpyc: error(/NativeIterable is reserved for @native types/)
    _start: Int32

    def __init__(self, start: Int32) -> None:
        self._start = start

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self._start)


def main() -> None:
    c = Counter(Int32(3))
    for _ in c:
        pass


main()
