# Companion to iterable_native_narrowing: verifies that a user iterator
# (Counter -- has __iter__ returning Own[CounterIter], no __span__, no
# begin/end) routes through the else branch of `Iterable[T] | NativeIterable[T]`
# narrowing at runtime under tpyc. The C++ `NativeIterable` concept requires
# `std::ranges::begin(t)`/`end(t)`, which Counter doesn't have, so
# `if constexpr (NativeIterable<Counter, Int32>)` evaluates False and the
# universal __iter__/__next__ branch runs.
#
# Skipped under CPython because the CPython `NativeIterable` stub is
# `@runtime_checkable` with a single `__iter__` method -- any type with
# `__iter__` passes isinstance structurally, taking the if branch instead.
# Tracked as a sema/C++/CPython-alignment TODO.
from typing import Iterable
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


class Counter:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)


def sum_fast(it: Iterable[Int32] | NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    if isinstance(it, NativeIterable):
        # NativeIterable branch -- range-for via begin/end.
        for x in it:
            total += x * 100
    else:
        # Iterable branch -- universal __iter__/__next__.
        for x in it:
            total += x
    return total


def main() -> None:
    # Counter is Iterable but not NativeIterable under tpyc (no begin/end at
    # C++ level), so the else branch runs: sum = 10+11+12+13 = 46.
    c = Counter(10, 14)
    print(sum_fast(c))

    # Sanity: a list takes the fast branch (x * 100 each): 1*100 + 2*100 = 300.
    nums: list[Int32] = [1, 2]
    print(sum_fast(nums))


main()
