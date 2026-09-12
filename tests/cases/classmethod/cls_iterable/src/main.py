# `cls(...)` as a for-loop iterable: the construction is a prvalue, so it takes
# an owning capture. Binding a reference to it would not compile, and the
# decision must not depend on the callee's spelling -- `cls` names no record.
from tpy import int32, Own


class CounterIter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration


class Counter:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)

    @classmethod
    def total_through_cls(cls, n: int32) -> int32:
        total = 0
        for v in cls(0, n):
            total += v
        return total

    @staticmethod
    def total_through_name(n: int32) -> int32:
        total = 0
        for v in Counter(0, n):
            total += v
        return total


def main() -> None:
    print(Counter.total_through_cls(4))
    print(Counter.total_through_name(4))


main()
