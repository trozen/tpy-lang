# Tests iter() and try_next() builtin functions
from tpy import Int32, Own, try_next

class CounterIter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

class Counter:
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.limit)

def main() -> None:
    # iter() returns an iterator from an iterable
    c = Counter(4)
    it = iter(c)

    # try_next() returns next value or None
    v = try_next(it)
    while v is not None:
        print(v)
        v = try_next(it)

    # Exhausted iterator returns None
    v2 = try_next(it)
    if v2 is None:
        print("exhausted")

main()
