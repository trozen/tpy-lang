# `cls(...)` as a for-loop iterable: the construction is a prvalue, so it takes
# an owning capture. Binding a reference to it would not compile, and the
# decision must not depend on the callee's spelling -- `cls` names no record.
from tpy import Int32, Own


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

    @classmethod
    def total_through_cls(cls, n: Int32) -> Int32:
        total = 0
        for v in cls(0, n):
            total += v
        return total

    @staticmethod
    def total_through_name(n: Int32) -> Int32:
        total = 0
        for v in Counter(0, n):
            total += v
        return total


def main() -> None:
    print(Counter.total_through_cls(4))
    print(Counter.total_through_name(4))


main()
